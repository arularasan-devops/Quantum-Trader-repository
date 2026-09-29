"""The single call the live loop makes into Phase 18 — the CAS observer.

``observe`` is called from the tick AFTER the production decision is complete and
returns nothing the tick can use. That is the whole coupling: no CAS value
reaches the Option Signal, its gates, its strike selection, its stop or its
exits, and the safety property can be checked by reading this one file.

Everything inside is paper. :func:`safety.assert_paper_only` runs on every call,
there is no broker import anywhere in the package, and the source scan in
:mod:`safety` is executed by the smoke test so the claim is verified rather than
asserted.

Failure isolation works the same way as Phase 17: an exception in research must
never break a tick, but must not be invisible either — failures are counted, the
last one is kept, and ``health()`` publishes both. A silently empty CAS dataset
is precisely the outcome this phase exists to prevent.
"""
from __future__ import annotations

import threading
import time
import uuid

from app.config import settings
from app.models import Candle, IndicatorSnapshot, OptionQuote
from app.research.phase17 import quality as p17quality, schema as p17schema
from app.research.phase18 import (
    capture,
    direction as cas_direction,
)
from app.research.phase18 import (
    execution,
    journal,
    paper,
    quality,
    safety,
    schema,
    session,
    signal as cas_signal,
    store,
)

_LOCK = threading.Lock()
_STATE: dict[str, int | float | str | None] = {
    "observations": 0,
    "written": 0,
    "failures": 0,
    "cards": 0,
    "entries": 0,
    "resolutions": 0,
    "last_error": None,
    "last_error_ts": None,
    "last_observation_ts": None,
    "skipped_outside_window": 0,
    "skipped_ineligible": 0,
}

# Today's material, kept in memory for the dashboard. Bounded: a five-second
# cadence over a twenty-minute window is ~240 samples per instrument, and the
# reports read from disk anyway.
_TODAY: list[dict] = []
_MAX_TODAY = 1_200
_CARDS: dict[str, dict] = {}
_OPEN: dict[str, paper.Leg] = {}
_PATHS: dict[str, list[dict]] = {}
_UNDERLYING: dict[str, list[float]] = {}
_PREMIUMS: dict[str, list[float]] = {}
_RESOLVED: list[dict] = []
_SESSION: str | None = None


def _bump(key: str, by: int = 1) -> None:
    with _LOCK:
        cur = _STATE.get(key)
        _STATE[key] = (int(cur) if isinstance(cur, (int, float)) else 0) + by


def _note_failure(exc: Exception) -> None:
    with _LOCK:
        cur = _STATE.get("failures")
        _STATE["failures"] = (int(cur) if isinstance(cur, (int, float)) else 0) + 1
        _STATE["last_error"] = f"{type(exc).__name__}: {exc}"
        _STATE["last_error_ts"] = time.time()


def _roll_session(sess: str) -> None:
    """A new session starts clean, but nothing open is thrown away silently."""
    global _SESSION
    if _SESSION == sess:
        return
    for leg in _OPEN.values():
        if leg.status not in schema.CLOSED:
            leg.status = schema.TIMEOUT
            _RESOLVED.append(paper.settle_leg(leg))
    _SESSION = sess
    _TODAY.clear()
    _CARDS.clear()
    _OPEN.clear()
    _PATHS.clear()
    _UNDERLYING.clear()
    _PREMIUMS.clear()


def observe(
    instrument: str,
    chain: list[OptionQuote],
    *,
    spot: float | None,
    ind: IndicatorSnapshot | None = None,
    candles: list[Candle] | None = None,
    htf_trend: str | None = None,
    tick_ts: float | None = None,
    capture_ts: float | None = None,
    expiry: str | None = None,
    days_to_expiry: int | None = None,
    source: str = p17quality.UNKNOWN_SOURCE,
    quote_age_ms: float | None = None,
    market_open: bool | None = None,
    futures_quote: p17schema.Quote | None = None,
) -> str | None:
    """Record one CAS sample and advance the paper book. Returns nothing usable.

    The return value is the observation id, for logging and tests only. The
    caller must not branch on it, and the live tick does not.
    """
    if not settings.phase18_cas_capture:
        return None
    safety.assert_paper_only()
    now = tick_ts if tick_ts is not None else time.time()
    if not session.in_window(now):
        _bump("skipped_outside_window")
        return None

    try:
        obs = capture.build(
            instrument, chain,
            spot=spot, intent_ts=now,
            capture_ts=capture_ts if capture_ts is not None else time.time(),
            expiry=expiry, days_to_expiry=days_to_expiry,
            source=source, quote_age_ms=quote_age_ms,
            market_open=market_open, futures_quote=futures_quote,
        )
        if obs is None:
            _bump("skipped_ineligible")
            return None
        _roll_session(obs.session)
        _bump("observations")
        with _LOCK:
            _STATE["last_observation_ts"] = now

        row = obs.to_dict()
        _TODAY.append(row)
        if len(_TODAY) > _MAX_TODAY:
            del _TODAY[: len(_TODAY) - _MAX_TODAY]
        if store.write_observation(row):
            _bump("written")

        if spot is not None:
            _UNDERLYING.setdefault(instrument, []).append(float(spot))

        atr = float(ind.atr) if ind is not None and ind.atr else None
        det = cas_direction.classify(
            candles=candles, ind=ind, spot=spot,
            window_prices=list(_UNDERLYING.get(instrument, [])),
            atr=atr, htf_trend=htf_trend,
        )
        card = cas_signal.build_card(
            row, direction_detail=det, atr=atr,
            momentum_atr=_momentum_atr(instrument, atr),
            per_session=None, paper_rows=_RESOLVED,
        )
        _CARDS[instrument] = card
        _bump("cards")

        if settings.phase18_paper:
            _advance_paper(instrument, row, card, now, atr)
        return obs.observation_id
    except Exception as exc:  # research must never break a live tick
        _note_failure(exc)
        return None


def _momentum_atr(instrument: str, atr: float | None) -> float | None:
    prices = _UNDERLYING.get(instrument) or []
    if len(prices) < 2 or not atr or atr <= 0:
        return None
    return (prices[-1] - prices[0]) / atr


def _advance_paper(
    instrument: str, row: dict, card: dict, now: float, atr: float | None
) -> None:
    """Mark open legs, then open new ones — never the other way round.

    Resolving first means a leg that closes and a leg that opens on the same tick
    cannot both occupy the same budget slot, and a leg cannot be marked against
    the quote that also opened it.
    """
    for key, leg in list(_OPEN.items()):
        q = _quote_of(row, leg.rung, leg.option_type)
        if q is not None:
            _PATHS.setdefault(key, []).append({"ts": now, "bid": q.get("bid")})
        paper.update(leg, q, now)
        if session.state(now) == session.POST_CAS and leg.status not in schema.CLOSED:
            carry = settings.phase18_overnight
            leg.overnight = carry
            if not carry:
                paper.close(leg, q, now, status=schema.TIMEOUT)
        if leg.status in schema.CLOSED:
            settled = paper.settle_leg(leg)
            settled["exit_variants"] = paper.exit_variants(
                leg, _PATHS.get(key) or [],
            )
            _RESOLVED.append(settled)
            store.write_paper(settled)
            _bump("resolutions")
            _OPEN.pop(key, None)
            _PATHS.pop(key, None)

    if card.get("signal") not in (schema.CAS_BUY_CE, schema.CAS_BUY_PE):
        return
    if len(_OPEN) >= int(settings.phase18_track_budget):
        return

    rung = str(card.get("rung") or "")
    side = str(card.get("option_type") or "")
    quote = _quote_of(row, rung, side)
    if quote is None:
        return
    prem = quote.get("premium")
    if isinstance(prem, (int, float)):
        _PREMIUMS.setdefault(instrument, []).append(float(prem))

    for variant in paper.STRATEGIES:
        key = f"{instrument}:{variant}"
        if key in _OPEN:
            continue
        take, why = paper.should_enter(
            variant, ts=now,
            window_prices=list(_UNDERLYING.get(instrument, [])),
            atr=atr,
            premium_series=list(_PREMIUMS.get(instrument, [])),
            spread_pct=(
                float(quote["spread_pct"])
                if isinstance(quote.get("spread_pct"), (int, float)) else None
            ),
        )
        if not take:
            continue
        fill = execution.entry_fill(quote)
        if fill["executability"] != schema.EXECUTABLE:
            continue
        leg = paper.Leg(
            episode_id=uuid.uuid4().hex[:16],
            session=str(row.get("session") or ""),
            instrument=instrument,
            strategy_variant=variant,
            option_type=side,
            rung=rung,
            strike=card.get("strike") if isinstance(
                card.get("strike"), (int, float)) else None,
            symbol=str(quote.get("symbol")) if quote.get("symbol") else None,
            expiry=str(row.get("expiry")) if row.get("expiry") else None,
            days_to_expiry=(
                int(row["days_to_expiry"])
                if isinstance(row.get("days_to_expiry"), int) else None
            ),
            expiry_class=(
                str(row.get("expiry_class")) if row.get("expiry_class") else None
            ),
            signal_ts=now,
            entry_ts=now,
            entry_quote=quote,
            entry_price=float(fill["price"]),
            stop=_num(card.get("stop")),
            t1=_num(card.get("t1")),
            t2=_num(card.get("t2")),
            t3=_num(card.get("t3")),
            status=schema.HOLD,
            data_quality=str(quote.get("data_quality") or quality.MISSING),
        )
        leg.milestones.append(f"ENTRY_{variant}: {why}")
        _OPEN[key] = leg
        _PATHS[key] = [{"ts": now, "bid": quote.get("bid")}]
        _bump("entries")


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _quote_of(row: dict, rung: str, side: str) -> dict | None:
    for lr in row.get("ladder") or []:
        if lr.get("rung") == rung:
            q = lr.get("ce" if side == schema.CE else "pe")
            return q if isinstance(q, dict) else None
    return None


# --------------------------------------------------------------- read-only
def health() -> dict:
    with _LOCK:
        state = dict(_STATE)
    states = _quality_states()
    gate = quality.gate(states)
    return {
        "strategy": schema.STRATEGY,
        "paper_only": True,
        "no_real_order": True,
        "enabled": bool(settings.phase18_cas_capture),
        "paper_enabled": bool(settings.phase18_paper),
        "session": _SESSION,
        "window": "15:10-15:30 IST",
        "open_legs": len(_OPEN),
        "resolved_legs": len(_RESOLVED),
        "quality": gate,
        "store": store.health(),
        **state,
    }


def _quality_states() -> list[str]:
    out: list[str] = []
    for row in _TODAY:
        for lr in row.get("ladder") or []:
            for key in ("ce", "pe"):
                q = lr.get(key)
                out.append(
                    str(q.get("data_quality")) if isinstance(q, dict)
                    else quality.MISSING
                )
    return out


def cards() -> list[dict]:
    return sorted(
        _CARDS.values(),
        key=lambda c: float(c.get("cas_score") or 0.0), reverse=True,
    )


def open_positions() -> list[dict]:
    out = []
    for key, leg in _OPEN.items():
        row = leg.to_dict()
        path = _PATHS.get(key) or []
        last = path[-1] if path else {}
        entry = leg.entry_price or 0.0
        bid = last.get("bid") if isinstance(last.get("bid"), (int, float)) else None
        row.update({
            "current_bid": bid,
            "current_pnl_points": (
                None if bid is None or not entry else round(float(bid) - entry, 2)
            ),
            "mfe_points": (
                None if leg.peak_bid is None or not entry
                else round(leg.peak_bid - entry, 2)
            ),
            "mae_points": (
                None if leg.trough_bid is None or not entry
                else round(leg.trough_bid - entry, 2)
            ),
            "giveback_points": (
                None if leg.peak_bid is None or bid is None
                else round(leg.peak_bid - float(bid), 2)
            ),
            "elapsed_seconds": (
                None if leg.entry_ts is None or not path
                else round(float(path[-1]["ts"]) - leg.entry_ts)
            ),
        })
        out.append(row)
    return out


def resolved() -> list[dict]:
    return list(_RESOLVED)


def observations_today() -> list[dict]:
    return list(_TODAY)


def reconciliation() -> dict:
    return journal.reconcile(
        observations=_TODAY, cards=list(_CARDS.values()),
        paper_rows=_RESOLVED + [leg.to_dict() for leg in _OPEN.values()],
    )


def journal_rows(limit: int = 200) -> list[dict]:
    rows = journal.rows(_RESOLVED)
    return rows[-max(1, limit):]


def reset_for_tests() -> None:
    """Clear in-memory state. Used by the smoke suite only."""
    global _SESSION
    _SESSION = None
    _TODAY.clear()
    _CARDS.clear()
    _OPEN.clear()
    _PATHS.clear()
    _UNDERLYING.clear()
    _PREMIUMS.clear()
    _RESOLVED.clear()
    for key in ("observations", "written", "failures", "cards", "entries",
                "resolutions", "skipped_outside_window", "skipped_ineligible"):
        _STATE[key] = 0
    _STATE["last_error"] = None
