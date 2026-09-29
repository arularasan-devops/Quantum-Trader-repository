"""Why did this signal lose? — Phase 12 §7, RESEARCH ONLY.

"54% of resolved calls were VEHICLE_FAILURE" came from a two-way split: the
underlying ever moved the called way, therefore the market was right, therefore
the option was at fault. That answered neither of the questions that matter —
whether the direction was genuinely there, and whether the loss came from the
contract, the exit, the fill or missing data.

Six outcomes, decided in this order, each from recorded facts:

``DATA_FAILURE``
    the evidence needed to attribute is not there: no two-sided quote at signal
    time, or the underlying was never recorded. Attributing anyway is how a
    measurement problem gets filed as a market problem.
``EXECUTION_FAILURE``
    the plan was never filled. The loss (or the missed gain) belongs to the
    execution path, and the lifecycle ledger holds the stage and reason.
``NO_FAILURE``
    the vehicle made money.
``EXIT_FAILURE``
    the direction was there and the premium reached at least the give-back
    threshold in R, and it was still handed back. The entry and the contract did
    their job; the exit did not.
``VEHICLE_FAILURE``
    the direction was there and the premium still lost. Evidence is required and
    named: spread, delta, liquidity, room, premium behaviour, or the option
    diverging from a favourable underlying.
``DIRECTION_FAILURE``
    the direction was not there on the stated definition.

Nothing here changes an exit, a gate or a score. It labels resolved history.
"""
from __future__ import annotations

from app.analysis import direction_attribution as da

DIRECTION_FAILURE = "DIRECTION_FAILURE"
VEHICLE_FAILURE = "VEHICLE_FAILURE"
EXECUTION_FAILURE = "EXECUTION_FAILURE"
EXIT_FAILURE = "EXIT_FAILURE"
DATA_FAILURE = "DATA_FAILURE"
NO_FAILURE = "NO_FAILURE"
KINDS = (DIRECTION_FAILURE, VEHICLE_FAILURE, EXECUTION_FAILURE, EXIT_FAILURE,
         DATA_FAILURE, NO_FAILURE)

# Vehicle evidence codes. A VEHICLE_FAILURE with no evidence is not asserted.
EV_SPREAD = "SPREAD_TOOK_THE_MOVE"
EV_SPREAD_RISK = "SPREAD_LARGE_VS_RISK"
EV_DELTA = "LOW_DELTA_LEG"
EV_LIQUIDITY = "THIN_LIQUIDITY"
EV_ROOM = "INSUFFICIENT_ROOM"
EV_DIVERGENCE = "OPTION_DIVERGED_FROM_UNDERLYING"
EV_DECAY = "PREMIUM_DECAYED_WHILE_FLAT"

# Why data could not answer it.
NO_BOOK = "NO_TWO_SIDED_QUOTE_AT_SIGNAL"
NO_UNDERLYING = "UNDERLYING_NOT_RECORDED"
NO_DIRECTION_DEFINITION = "NO_ANSWERABLE_DIRECTION_DEFINITION"

# A leg below this delta moves a fraction of the underlying, so a favourable
# move that fails to pay is a property of the leg that was picked.
LOW_DELTA = 0.25
# The premium reached this much in R and still resolved a loser: the exit, not
# the contract.
GIVE_BACK_R = 1.0

# Which direction definition decides "was the direction there". B is the
# defensible one available today: a favourable half-ATR before an ATR against.
# C and D are consulted as support; A alone is never enough, which is the whole
# point of §6.
PRIMARY = da.B
SUPPORTING = (da.C, da.D, da.E)


def _direction_present(verdicts: dict | None) -> tuple[bool | None, str]:
    """Was the direction there, and on which definition was that decided."""
    if not verdicts:
        return None, NO_DIRECTION_DEFINITION
    primary = verdicts.get(PRIMARY)
    if primary is not None:
        return bool(primary), PRIMARY
    for name in SUPPORTING:
        value = verdicts.get(name)
        if value is not None:
            return bool(value), name
    return None, NO_DIRECTION_DEFINITION


def _vehicle_evidence(trade: dict) -> list[str]:
    """Named, observed reasons the contract rather than the read may have lost."""
    ev: list[str] = []
    spread_pct = trade.get("spread_pct_of_premium")
    spread_over_risk = trade.get("spread_over_risk")
    if spread_pct is not None and float(spread_pct) >= 2.0:
        ev.append(EV_SPREAD)
    if spread_over_risk is not None and float(spread_over_risk) >= 0.25:
        ev.append(EV_SPREAD_RISK)
    delta = trade.get("delta")
    if delta is not None and abs(float(delta)) < LOW_DELTA:
        ev.append(EV_DELTA)
    if trade.get("thin_liquidity"):
        ev.append(EV_LIQUIDITY)
    room = trade.get("expected_room_r")
    if room is not None and float(room) < 1.0:
        ev.append(EV_ROOM)
    fav = trade.get("underlying_favorable_move")
    realized = trade.get("realized_r")
    if (fav is not None and float(fav) > 0
            and realized is not None and float(realized) <= 0):
        ev.append(EV_DIVERGENCE)
    if (fav is not None and abs(float(fav)) <= 0
            and realized is not None and float(realized) < 0):
        ev.append(EV_DECAY)
    return ev


def classify(*, verdicts: dict | None, trade: dict,
             filled: bool | None = None) -> dict:
    """Attribute one resolved signal.

    ``verdicts`` is the §6 direction block. ``trade`` carries the recorded facts:
    ``realized_r``, ``mfe_r``, ``spread_pct_of_premium``, ``spread_over_risk``,
    ``delta``, ``thin_liquidity``, ``expected_room_r``, ``book_quoted``,
    ``underlying_recorded`` and ``underlying_favorable_move``.
    """
    reasons: list[str] = []
    if trade.get("book_quoted") is False:
        reasons.append(NO_BOOK)
    if trade.get("underlying_recorded") is False:
        reasons.append(NO_UNDERLYING)

    present, decided_by = _direction_present(verdicts)
    if present is None:
        reasons.append(NO_DIRECTION_DEFINITION)

    realized = trade.get("realized_r")
    mfe = trade.get("mfe_r")
    won = realized is not None and float(realized) > 0

    if filled is False:
        kind = EXECUTION_FAILURE
    elif reasons and not won:
        kind = DATA_FAILURE
    elif won:
        kind = NO_FAILURE
    elif realized is None:
        kind = DATA_FAILURE
        reasons.append("NO_R_AVAILABLE")
    elif present and mfe is not None and float(mfe) >= GIVE_BACK_R:
        kind = EXIT_FAILURE
    elif present:
        kind = VEHICLE_FAILURE
    else:
        kind = DIRECTION_FAILURE

    evidence = _vehicle_evidence(trade) if kind == VEHICLE_FAILURE else []
    if kind == VEHICLE_FAILURE and not evidence:
        # The direction was there and the premium lost, but nothing in the record
        # says why. Asserting the contract was at fault without evidence is the
        # habit §7 exists to end.
        kind = DATA_FAILURE
        reasons.append("NO_VEHICLE_EVIDENCE")

    return {
        "failure_kind": kind,
        "direction_present": present,
        "direction_decided_by": decided_by,
        "vehicle_evidence": evidence,
        "data_gaps": reasons,
        "give_back_r_threshold": GIVE_BACK_R,
        "research_only": True,
        "note": ("attribution of resolved history; it changes no exit, gate or "
                 "score, and a signal that never filled is attributed to "
                 "execution rather than to the market read"),
    }


def summarise(rows: list[dict]) -> dict:
    """Counts per kind, plus how often the read was right and the option wrong."""
    counts = {k: 0 for k in KINDS}
    evidence: dict[str, int] = {}
    for row in rows:
        kind = str(row.get("failure_kind"))
        if kind in counts:
            counts[kind] += 1
        for code in row.get("vehicle_evidence") or []:
            evidence[code] = evidence.get(code, 0) + 1
    attributable = sum(counts[k] for k in KINDS if k != DATA_FAILURE)
    vehicle = counts[VEHICLE_FAILURE]
    return {
        "resolved": len(rows),
        "counts": counts,
        "attributable": attributable,
        "unattributable": counts[DATA_FAILURE],
        "vehicle_evidence_counts": evidence,
        "direction_right_option_wrong_pct": (
            round(100.0 * vehicle / attributable, 1) if attributable else None),
        "direction_right_option_wrong_note": (
            "share of attributable resolved signals where the direction was "
            f"present on {PRIMARY} and the option still lost with named evidence"),
        "research_only": True,
    }
