"""The CAS card — §11 signal plus every field the dashboard shows.

One observation in, one card out. The card is research output: it carries
``CAS PAPER ONLY`` in its own payload, it never returns a production decision,
and the only thing downstream of it is the paper book.

``CAS_NO_TRADE`` and ``CAS_WAIT`` are first-class answers. A window with a wide
book, an unclear direction or a stale quote produces one of them and the reason
why, rather than a forced side — the whole point of measuring this regime is to
find out whether it is tradable, and a module that always finds a trade cannot
answer that.
"""
from __future__ import annotations

from app.research.phase18 import (
    direction as cas_direction,
)
from app.research.phase18 import (
    execution,
    expectation,
    quality,
    schema,
    score as cas_score,
)

# A book wider than this is not worth entering at any score: the round trip is
# charged twice against a twenty-minute hold.
MAX_SPREAD_PCT = 8.0
# Below this score the card is a WAIT even when everything else is available.
MIN_SCORE = 45.0
# Rungs the card is allowed to select, in preference order. The two furthest
# rungs are captured and studied but never proposed, because nothing has yet
# shown they can be sold into.
CANDIDATE_RUNGS = (schema.OTM_1, schema.OTM_2, schema.ATM, schema.OTM_3)


def _f(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return None if f != f else f


def _quote_for(obs: dict, rung: str, side: str) -> dict | None:
    for lr in obs.get("ladder") or []:
        if lr.get("rung") == rung:
            q = lr.get("ce" if side == schema.CE else "pe")
            return q if isinstance(q, dict) else None
    return None


def _pick_rung(obs: dict, side: str) -> tuple[str | None, dict | None, str]:
    """The best tradable rung on the chosen side, or a reason there is none."""
    seen: list[str] = []
    for rung in CANDIDATE_RUNGS:
        q = _quote_for(obs, rung, side)
        if not q:
            seen.append(f"{rung}: no quote")
            continue
        state = str(q.get("data_quality") or quality.MISSING)
        if state not in quality.FILLABLE:
            seen.append(f"{rung}: book {state}")
            continue
        sp = _f(q.get("spread_pct"))
        if sp is None:
            seen.append(f"{rung}: no spread")
            continue
        if sp > MAX_SPREAD_PCT:
            seen.append(f"{rung}: spread {sp:.1f}%")
            continue
        return rung, q, "tradable rung found"
    return None, None, "; ".join(seen) or "no ladder captured"


def build_card(
    obs: dict,
    *,
    direction_detail: dict | None = None,
    atr: float | None = None,
    momentum_atr: float | None = None,
    per_session: list[dict] | None = None,
    paper_rows: list[dict] | None = None,
) -> dict:
    """Everything the CAS tab renders for one instrument at one instant."""
    instrument = str(obs.get("instrument") or "")
    underlying = _f(obs.get("underlying"))
    det = direction_detail or {"direction": schema.CAS_UNCLEAR,
                               "bullish_votes": 0, "bearish_votes": 0,
                               "components": {}}
    dir_call = str(det.get("direction") or schema.CAS_UNCLEAR)
    side = cas_direction.side_for(dir_call)

    card: dict[str, object] = {
        "strategy": schema.STRATEGY,
        "paper_only": True,
        "banner": "CAS PAPER ONLY",
        "no_real_order": True,
        "session": obs.get("session"),
        "instrument": instrument,
        "exchange": obs.get("exchange"),
        "timestamp": obs.get("intent_ts"),
        "cas_state": obs.get("cas_state"),
        "sub_window": obs.get("sub_window"),
        "remaining_seconds": obs.get("remaining_sec"),
        "expiry": obs.get("expiry"),
        "days_to_expiry": obs.get("days_to_expiry"),
        "expiry_class": obs.get("expiry_class"),
        "direction": dir_call,
        "direction_components": det.get("components"),
        "data_quality": obs.get("data_quality"),
        "observation_id": obs.get("observation_id"),
        "cas_score": None,
        "cas_score_is_probability": False,
    }

    # The underlying half of the card is knowable whether or not a strike is
    # tradable, and a WAIT that still shows the expected move is more useful
    # than a blank panel — so it is filled in before either refusal.
    underlying_block = expectation.card_block(
        instrument=instrument, underlying=underlying, atr=atr, premium=None,
        delta=None, gamma=None, spread=None,
        per_session=per_session, paper_rows=paper_rows,
    )

    if side is None:
        card.update(underlying_block)
        card.update({
            "signal": schema.CAS_NO_TRADE,
            "status": schema.WAIT,
            "reason": f"direction {dir_call} — no side to take",
            "option_type": None,
        })
        return card

    rung, quote, why = _pick_rung(obs, side)
    if rung is None or quote is None:
        card.update(underlying_block)
        card.update({
            "signal": schema.CAS_NO_TRADE,
            "status": schema.WAIT,
            "reason": f"no tradable strike on the {side} side ({why})",
            "option_type": side,
        })
        return card

    bid, ask = _f(quote.get("bid")), _f(quote.get("ask"))
    premium = _f(quote.get("premium")) or _f(quote.get("mid"))
    spread = _f(quote.get("spread"))
    spread_pct = _f(quote.get("spread_pct"))
    delta, gamma = _f(quote.get("delta")), _f(quote.get("gamma"))

    block = expectation.card_block(
        instrument=instrument, underlying=underlying, atr=atr,
        premium=ask if ask is not None else premium,
        delta=delta, gamma=gamma, spread=spread,
        per_session=per_session, paper_rows=paper_rows,
    )

    t1 = _f(block.get("t1"))
    entry = _f(block.get("current_option_premium"))
    t1_needs = (
        None if t1 is None or entry is None or not delta or abs(delta) < 1e-6
        else (t1 - entry) / abs(delta)
    )

    sc = cas_score.score(
        direction=dir_call,
        direction_votes=(int(det.get("bullish_votes") or 0),
                         int(det.get("bearish_votes") or 0)),
        momentum_atr=momentum_atr,
        atr_pct=(100.0 * atr / underlying) if atr and underlying else None,
        rung=rung,
        spread_pct=spread_pct,
        oi=_f(quote.get("oi")),
        volume=_f(quote.get("volume")),
        premium=entry,
        expected_move_points=_f(block.get("cas_expected_move_points")),
        t1_needs_points=t1_needs,
        remaining_sec=_f(obs.get("remaining_sec")),
        expiry_class=(obs.get("expiry_class") if isinstance(
            obs.get("expiry_class"), str) else None),
        data_quality=str(quote.get("data_quality") or quality.MISSING),
    )

    fill = execution.entry_fill(quote)
    total = float(sc["score"])
    if fill["executability"] != schema.EXECUTABLE:
        sig, status, reason = (
            schema.CAS_WAIT, schema.WAIT,
            f"not priceable: {fill.get('reason')}",
        )
    elif total < MIN_SCORE:
        sig, status, reason = (
            schema.CAS_WAIT, schema.WAIT,
            f"score {total:.0f} below the {MIN_SCORE:.0f} research floor",
        )
    else:
        sig = schema.CAS_BUY_CE if side == schema.CE else schema.CAS_BUY_PE
        status, reason = schema.BUY, f"score {total:.0f}, spread {spread_pct}%"

    card.update(block)
    card.update({
        "signal": sig,
        "status": status,
        "reason": reason,
        "option_type": side,
        "rung": rung,
        "strike": _f(quote.get("strike")),
        "symbol": quote.get("symbol"),
        "bid": bid,
        "ask": ask,
        "spread": spread,
        "spread_pct": spread_pct,
        "preferred_entry": ask,
        "iv": _f(quote.get("iv")),
        "delta": delta,
        "gamma": gamma,
        "theta": _f(quote.get("theta")),
        "oi": _f(quote.get("oi")),
        "volume": _f(quote.get("volume")),
        "cas_score": sc["score"],
        "cas_score_components": sc["components"],
        "cas_score_is_probability": False,
        "t1_rank": cas_score.rank_label(total),
        "entry_quality": _entry_quality(spread_pct),
        "tradability": _tradability(spread_pct, fill["executability"]),
        "expected_hold_seconds": obs.get("remaining_sec"),
        "executability": fill["executability"],
    })
    return card


def _entry_quality(spread_pct: float | None) -> str:
    if spread_pct is None:
        return "UNKNOWN"
    if spread_pct <= 1.0:
        return "GOOD"
    if spread_pct <= 3.0:
        return "FAIR"
    return "POOR"


def _tradability(spread_pct: float | None, executability: str) -> str:
    if executability != schema.EXECUTABLE:
        return schema.EXECUTABILITY_UNKNOWN
    if spread_pct is None:
        return schema.EXECUTABILITY_UNKNOWN
    return schema.EXECUTABLE if spread_pct <= MAX_SPREAD_PCT else schema.NOT_EXECUTABLE
