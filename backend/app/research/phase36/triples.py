"""Phase 36 §2/§3/§4/§5 — same-timestamp FUTURES / CE / PE triples.

A *triple* is one decision instant at which all three vehicles were quoted
two-sided by the real feed. That is a strict definition and it throws a lot of
rows away, deliberately: if the future is executable and the call is not, then
comparing them measures which contract the feed happened to publish depth for,
not which vehicle expresses the move better. So the eligibility rule is all
three or none, and the rows that fail are counted by reason instead of being
quietly absent (§2's coverage requirement, and the thing that made every earlier
"futures wins" reading untrustworthy).

Everything here is read from the Phase 35 raw store, which is append-only. This
module writes nothing to raw and infers nothing: no midpoint, no LTP, no nearest
timestamp, no interpolation, no modelled spread. A quote that is not
MEASURED_EXECUTABLE simply is not part of a triple.
"""
from __future__ import annotations

import json

from app.research.phase35 import (
    CE,
    FUTURES,
    MEASURED_EXECUTABLE,
    PE,
    SHORT,
    normalize_direction,
)
from app.research.phase36 import (
    ATM,
    ITM,
    OTM_1,
    OTM_2,
    OTM_DEEP,
)

# Reasons a decision instant is not a comparable triple. Kept distinct from
# Phase 35's quote-level reasons because these are statements about the
# *comparison*, not about one quote.
NO_FUTURES_BOOK = "NO_EXECUTABLE_FUTURES_BOOK"
NO_CE_BOOK = "NO_EXECUTABLE_CE_BOOK"
NO_PE_BOOK = "NO_EXECUTABLE_PE_BOOK"
CROSSED_BOOK = "CROSSED_OR_ZERO_BOOK"
NO_DIRECTION = "NO_DIRECTION_RECORDED"


def _num(v: object) -> float | None:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _positive(v: object) -> float | None:
    f = _num(v)
    return f if f is not None and f > 0 else None


def executable(quote: dict | None) -> bool:
    """True only for a real, fresh, two-sided, non-crossed book.

    ``bid < ask`` is checked here rather than trusted: a crossed or equal book is
    either a feed artefact or a locked market, and pricing an ask-in/bid-out
    round trip through it would manufacture a free profit.
    """
    if not quote or quote.get("evidence") != MEASURED_EXECUTABLE:
        return False
    bid, ask = _positive(quote.get("bid")), _positive(quote.get("ask"))
    return bid is not None and ask is not None and bid < ask


def strike_step(con, instrument: str) -> float | None:
    """The instrument's strike interval, measured from what was captured.

    Taken as the smallest positive gap between adjacent distinct strikes rather
    than configured, so moneyness bucketing does not depend on a table that
    could be wrong for an instrument nobody re-checked. Returns ``None`` when
    fewer than two strikes were seen, and then moneyness stays unmeasured
    instead of defaulting to ATM.
    """
    rows = con.execute(
        "SELECT DISTINCT q.strike AS strike FROM raw_quote q "
        "JOIN raw_observation o ON o.obs_id = q.obs_id "
        "WHERE o.instrument = ? AND q.strike IS NOT NULL AND q.strike > 0 "
        "ORDER BY q.strike",
        (instrument,),
    ).fetchall()
    strikes = [float(r["strike"]) for r in rows]
    if len(strikes) < 2:
        return None
    gaps = [
        round(b - a, 6) for a, b in zip(strikes, strikes[1:], strict=False)
        if b - a > 0
    ]
    return min(gaps) if gaps else None


def moneyness(
    *, vehicle: str, strike: float | None, underlying: float | None,
    step: float | None,
) -> tuple[str | None, int | None]:
    """Bucket and signed step distance from ATM, or ``(None, None)``.

    Sign convention: positive steps are out of the money for the option's own
    side, so an OTM call and an OTM put both report positive distance and the
    two can share a table row.
    """
    k, u = _positive(strike), _positive(underlying)
    if k is None or u is None or not step or step <= 0:
        return None, None
    raw = (k - u) if vehicle == CE else (u - k)
    steps = int(round(raw / step))
    if steps < 0:
        return ITM, steps
    if steps == 0:
        return ATM, 0
    if steps == 1:
        return OTM_1, 1
    if steps == 2:
        return OTM_2, 2
    return OTM_DEEP, steps


def option_leg_fields(quote: dict, *, step: float | None) -> dict:
    """§4 — everything recorded about an option side of the triple."""
    bucket, steps = moneyness(
        vehicle=quote["vehicle"], strike=quote.get("strike"),
        underlying=quote.get("underlying"), step=step,
    )
    bid, ask = _positive(quote.get("bid")), _positive(quote.get("ask"))
    spread = (
        round(ask - bid, 4) if bid is not None and ask is not None else None
    )
    return {
        "vehicle": quote["vehicle"],
        "symbol": quote.get("symbol"),
        "expiry": quote.get("expiry"),
        "dte": quote.get("dte"),
        "strike": quote.get("strike"),
        "underlying": quote.get("underlying"),
        "atm_bucket": bucket,
        "atm_steps": steps,
        # The premium recorded for banding is the ASK — the price actually paid
        # to open. Banding on the traded price would put a leg in a cheaper band
        # than the one it was bought in.
        "premium": ask,
        "bid": bid,
        "ask": ask,
        "spread": spread,
        "spread_pct": (
            round(100.0 * spread / ask, 4)
            if spread is not None and ask else None
        ),
        "delta": _num(quote.get("delta")),
        "iv": _num(quote.get("iv")),
        "oi": _num(quote.get("oi")),
        "volume": _num(quote.get("volume")),
        "lot_size": quote.get("lot_size"),
    }


def futures_leg_fields(quote: dict) -> dict:
    """§5 — everything recorded about the futures side of the triple."""
    bid, ask = _positive(quote.get("bid")), _positive(quote.get("ask"))
    spread = round(ask - bid, 4) if bid is not None and ask is not None else None
    return {
        "vehicle": FUTURES,
        "symbol": quote.get("symbol"),
        "expiry": quote.get("expiry"),
        "dte": quote.get("dte"),
        "bid": bid,
        "ask": ask,
        "spread": spread,
        "spread_pct": (
            round(100.0 * spread / ask, 4)
            if spread is not None and ask else None
        ),
        "underlying": _num(quote.get("underlying")),
        "basis": _num(quote.get("basis")),
        "oi": _num(quote.get("oi")),
        "volume": _num(quote.get("volume")),
        "lot_size": quote.get("lot_size"),
    }


def _plan(obs: dict) -> dict:
    ctx = obs.get("context_json")
    if not isinstance(ctx, str) or not ctx:
        return {}
    try:
        body = json.loads(ctx) or {}
    except ValueError:
        return {}
    plan = body.get("plan")
    return plan if isinstance(plan, dict) else {}


def directional_option(direction: str) -> str:
    """Which option side expresses the view. Long call up, long put down."""
    return PE if normalize_direction(direction) == SHORT else CE


def build(con, *, instrument: str, limit: int | None = None) -> dict:
    """Every comparable triple for one instrument, plus why the rest are not.

    Returns raw comparison inputs only. No outcome is resolved here, so nothing
    in this function can see the future — which is what makes it safe for the
    eligibility rule to be strict.
    """
    step = strike_step(con, instrument)
    obs_rows = con.execute(
        "SELECT * FROM raw_observation WHERE instrument = ? ORDER BY ts",
        (instrument,),
    ).fetchall()
    if limit:
        obs_rows = obs_rows[:limit]

    triples: list[dict] = []
    refused: dict[str, int] = {}

    def refuse(reason: str) -> None:
        refused[reason] = refused.get(reason, 0) + 1

    for row in obs_rows:
        obs = dict(row)
        quotes = {
            q["vehicle"]: dict(q) for q in con.execute(
                "SELECT * FROM raw_quote WHERE obs_id = ?", (obs["obs_id"],),
            ).fetchall()
        }
        fut, ce, pe = quotes.get(FUTURES), quotes.get(CE), quotes.get(PE)
        if not executable(fut):
            refuse(NO_FUTURES_BOOK)
            continue
        if not executable(ce):
            refuse(NO_CE_BOOK)
            continue
        if not executable(pe):
            refuse(NO_PE_BOOK)
            continue
        direction = normalize_direction(obs.get("direction"))
        if direction is None:
            # A vehicle cannot be chosen for a view that was never recorded, and
            # assuming LONG would put half the sample on the wrong side of §9.
            # Translation happens here, not a rewrite of the stored row: capture
            # keeps the upstream wording (BULLISH/BEARISH) and this study reads it
            # as a position.
            refuse(NO_DIRECTION)
            continue
        plan = _plan(obs)
        triples.append({
            "obs_id": obs["obs_id"],
            "ts": float(obs["ts"]),
            "session": obs.get("session"),
            "instrument": instrument,
            "direction": direction,
            "opportunity_type": obs.get("opportunity_type"),
            "source": obs.get("source"),
            "engine_selected": bool(obs.get("engine_selected")),
            "engine_vehicle": obs.get("selected_vehicle"),
            "directional_option": directional_option(direction),
            "lot_size": plan.get("lot_size"),
            FUTURES: futures_leg_fields(fut),
            CE: option_leg_fields(ce, step=step),
            PE: option_leg_fields(pe, step=step),
        })

    return {
        "instrument": instrument,
        "strike_step": step,
        "observations": len(obs_rows),
        "triples": triples,
        "eligible": len(triples),
        "refused_by_reason": dict(sorted(refused.items(), key=lambda kv: -kv[1])),
        "eligible_pct": (
            round(100.0 * len(triples) / len(obs_rows), 2) if obs_rows else None
        ),
        "note": (
            "a triple requires a real, fresh, two-sided, non-crossed book on "
            "all three vehicles at the same decision instant; partial coverage "
            "is counted, never filled in"
        ),
    }
