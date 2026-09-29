"""Phase 11 §7 — why did the losers lose? RESEARCH ONLY.

A pooled loss column cannot distinguish the two failures that need opposite
fixes. "GOLD PE lost ₹4,000" reads as a wrong opinion; if GOLD's round-trip book
was 130% of the intended risk, the opinion was never given a chance to be wrong —
the stop was inside the spread before the market moved. One of those is fixed by
better direction, the other only by not trading that contract, and treating the
second as the first is how a model gets blamed for an execution problem.

So every losing trade is attributed to exactly one cause, in a fixed precedence,
and each cause is assigned to a side:

* **SIGNAL_SIDE** — the read was wrong, or the target was never reachable.
* **ECONOMIC** — the contract could not pay: spread, liquidity, strike.
* **EXECUTION** — the entry, the exit or the data feed, not the opinion.

The precedence matters more than the taxonomy: the economic tests run BEFORE the
directional one, so an unpayable contract can never be recorded as a bad signal.
The reverse ordering is what produced the earlier conclusion that "high
confidence is inverted".
"""
from __future__ import annotations

from collections.abc import Callable

from app.research.phase9.tradability import UNTRADABLE_SPREAD_SHARE_PCT

from .families import FAMILIES, split

WRONG_DIRECTION = "WRONG_DIRECTION"
BAD_SPREAD = "BAD_SPREAD"
LOW_LIQUIDITY = "LOW_LIQUIDITY"
BAD_STRIKE_SELECTION = "BAD_STRIKE_SELECTION"
ENTRY_CHASE = "ENTRY_CHASE"
INSUFFICIENT_ROOM = "INSUFFICIENT_ROOM"
DATA_QUALITY = "DATA_QUALITY"
EXECUTION = "EXECUTION"
EXIT = "EXIT"
OTHER = "OTHER"

CAUSES = (WRONG_DIRECTION, BAD_SPREAD, LOW_LIQUIDITY, BAD_STRIKE_SELECTION,
          ENTRY_CHASE, INSUFFICIENT_ROOM, DATA_QUALITY, EXECUTION, EXIT, OTHER)

SIGNAL_SIDE = "SIGNAL_SIDE"
ECONOMIC = "ECONOMIC"
EXECUTION_SIDE = "EXECUTION"
UNCLASSIFIED = "UNCLASSIFIED"

SIDE_OF = {
    WRONG_DIRECTION: SIGNAL_SIDE,
    INSUFFICIENT_ROOM: SIGNAL_SIDE,
    BAD_SPREAD: ECONOMIC,
    LOW_LIQUIDITY: ECONOMIC,
    BAD_STRIKE_SELECTION: ECONOMIC,
    ENTRY_CHASE: EXECUTION_SIDE,
    EXIT: EXECUTION_SIDE,
    EXECUTION: EXECUTION_SIDE,
    DATA_QUALITY: EXECUTION_SIDE,
    OTHER: UNCLASSIFIED,
}

# A trade whose favourable excursion never reached this was never in profit, so
# its loss is about the read rather than about giving something back.
NEVER_WENT_ANYWHERE_MFE_R = 0.25
# A trade that reached this and still lost had a winner and handed it back.
WINNER_HANDED_BACK_MFE_R = 1.0
# Diagnostic floors, matching Phase 9's qualifier so the two blocks agree.
MIN_OI = 1000.0
MIN_VOLUME = 100.0
MIN_ABS_DELTA = 0.25


def cause_of(trade: dict, entry_row: dict | None) -> tuple[str, str]:
    """``(cause, evidence)`` for one losing trade. Economic tests come first."""
    if trade.get("data_flag") in ("STALE", "NO_DATA"):
        return DATA_QUALITY, f"the decision was made on {trade['data_flag']} data"

    share = trade.get("spread_share_of_risk_pct")
    if share is not None and float(share) >= UNTRADABLE_SPREAD_SHARE_PCT:
        return BAD_SPREAD, (f"round-trip book was {float(share):.0f}% of the "
                            f"intended risk, so the stop sat inside the spread")

    gross, net = trade.get("realised_r"), trade.get("net_r")
    if gross is not None and net is not None and gross > 0 >= net:
        return BAD_SPREAD, (f"gross {gross:+.2f}R became net {net:+.2f}R: the "
                            f"trade was right and the book took it")

    if trade.get("entry_quality") == "SEVERELY_CHASED":
        return ENTRY_CHASE, "the entry was classified SEVERELY_CHASED"

    mfe = trade.get("mfe_r")
    if mfe is not None and float(mfe) >= WINNER_HANDED_BACK_MFE_R:
        return EXIT, (f"reached {float(mfe):+.2f}R favourable and closed at "
                      f"{(gross if gross is not None else 0.0):+.2f}R")

    if entry_row is not None:
        oi, vol = entry_row.get("oi"), entry_row.get("volume")
        if (oi is not None and float(oi) < MIN_OI) or (
                vol is not None and float(vol) < MIN_VOLUME):
            return LOW_LIQUIDITY, f"OI {oi} / volume {vol} at signal time"
        delta = entry_row.get("delta")
        if delta is not None and abs(float(delta)) < MIN_ABS_DELTA:
            return BAD_STRIKE_SELECTION, (f"selected leg delta "
                                          f"{float(delta):.2f} — too far out to "
                                          f"follow the underlying")
        room = entry_row.get("room_ratio")
        if room is not None and float(room) > 1.0:
            return INSUFFICIENT_ROOM, (f"target1 needed {float(room):.2f}x the ATR "
                                       f"available (DATA_CONTAMINATED input)")

    if mfe is not None and float(mfe) <= NEVER_WENT_ANYWHERE_MFE_R:
        return WRONG_DIRECTION, (f"never exceeded {float(mfe):+.2f}R favourable: "
                                 f"the direction did not happen")
    return OTHER, "no measurable defect isolated for this loss"


def _agg(rows: list[dict]) -> dict:
    by_cause: dict[str, dict] = {}
    for cause in CAUSES:
        sub = [r for r in rows if r.get("loss_cause") == cause]
        nets = [r["net_r"] for r in sub if r.get("net_r") is not None]
        by_cause[cause] = {
            "side": SIDE_OF[cause],
            "n": len(sub),
            "pct_of_losses": round(100.0 * len(sub) / len(rows), 1) if rows else None,
            "total_net_r": round(sum(nets), 3) if nets else None,
            "mean_net_r": round(sum(nets) / len(nets), 3) if nets else None,
            "instruments": sorted({r["instrument"] for r in sub}),
        }
    sides: dict[str, dict] = {}
    for side in (SIGNAL_SIDE, ECONOMIC, EXECUTION_SIDE, UNCLASSIFIED):
        sub = [r for r in rows if SIDE_OF.get(r.get("loss_cause") or OTHER) == side]
        nets = [r["net_r"] for r in sub if r.get("net_r") is not None]
        sides[side] = {
            "n": len(sub),
            "pct_of_losses": round(100.0 * len(sub) / len(rows), 1) if rows else None,
            "total_net_r": round(sum(nets), 3) if nets else None,
        }
    total = sum(v["total_net_r"] or 0.0 for v in sides.values())
    economic_and_execution = (sides[ECONOMIC]["total_net_r"] or 0.0) + (
        sides[EXECUTION_SIDE]["total_net_r"] or 0.0)
    return {
        "losses": len(rows),
        "by_cause": by_cause,
        "by_side": sides,
        "total_net_r_lost": round(total, 3),
        "share_of_loss_economic_or_execution_pct": (
            round(100.0 * economic_and_execution / total, 1) if total else None),
        "share_of_loss_signal_side_pct": (
            round(100.0 * (sides[SIGNAL_SIDE]["total_net_r"] or 0.0) / total, 1)
            if total else None),
    }


def study(rows: list[dict], entry_of: Callable[[dict], dict | None]) -> dict:
    """§7 — attribute every losing trade, per family.

    ``rows`` are all resolved trades; the losers are selected on NET R, because a
    trade that was gross-positive and net-negative is a loss to the account and
    calling it a win is how the flow book came to show a profit it never had.
    """
    losers: list[dict] = []
    for r in rows:
        net = r.get("net_r")
        if net is None or float(net) > 0:
            continue
        cause, evidence = cause_of(r, entry_of(r))
        r["loss_cause"] = cause
        r["loss_cause_evidence"] = evidence
        r["loss_side"] = SIDE_OF[cause]
        losers.append(r)

    by_family = split(losers)
    families = {fam: _agg(by_family.get(fam) or []) for fam in FAMILIES}
    return {
        "causes": list(CAUSES),
        "side_of_cause": dict(SIDE_OF),
        "precedence": [
            "DATA_QUALITY — the decision was made on stale or missing data",
            "BAD_SPREAD — the book was wider than the intended risk",
            "BAD_SPREAD — gross positive, net negative",
            "ENTRY_CHASE — the entry was severely chased",
            "EXIT — a winner of 1R or more was handed back",
            "LOW_LIQUIDITY / BAD_STRIKE_SELECTION / INSUFFICIENT_ROOM — the "
            "contract chosen could not express the signal",
            "WRONG_DIRECTION — only once none of the above applies",
        ],
        "why_this_order": "the economic tests run first so an unpayable contract "
                          "cannot be recorded as a bad signal. Attributing those "
                          "losses to direction is what made high scores look "
                          "inverted",
        "by_family": families,
        "note": "losses are selected on NET R. A gross-positive, net-negative trade "
                "is a loss to the account",
    }
