"""Phase 32 — the authoritative run across every instrument that has candles.

Order matters and is fixed: the universe is inventoried first, the hypothesis
count is fixed from that inventory *before* any outcome is evaluated, then each
five-year instrument gets its configuration grid, its one development-window
choice and its single look at the untouched holdout. Instruments with only a
short capture are described, never selected on.

Research only. No gate, entry, stop, target, sizing or order-path change.
"""
from __future__ import annotations

import time

import numpy as np

from app.research.phase31 import realized
from app.research.phase32 import (
    DERIVED_PREMIUM,
    MEASURED_UNDERLYING,
    MIN_COST_MULTIPLE,
    NO_REACHABLE_T1,
    REQUIRES_MORE_DATA,
    VALIDATED,
    premium,
    reach,
    t1 as t1mod,
    universe,
)


def _cost(instrument: str, entry: np.ndarray) -> tuple[np.ndarray, str | None]:
    """Round-trip cost for an instrument, or a stated reason there is none."""
    try:
        return reach.cost_pct(instrument, entry), None
    except Exception as exc:  # noqa: BLE001 - a missing spec must not fail the run
        return np.full(entry.shape, np.nan), (
            f"no cost model for this instrument ({exc.__class__.__name__}), so no "
            "net figure is reported for it"
        )


def _descriptive_premium(inst: str, ratio: dict, hurdle: dict) -> list[dict]:
    """Premium arithmetic at the shortest distance that is worth attempting.

    Nothing was selected, so there is no validated T1 to translate. What can still
    be stated is the shortest frozen distance that pays its own round trip several
    times over — the same ``MIN_COST_MULTIPLE`` this repository already refuses
    plans below — carrying that distance's *measured* holdout reach rate. It is
    descriptive: reachable, not validated, and no premium figure here is measured.
    """
    for row in hurdle["reach"]:
        mult = row.get("t1_in_multiples_of_cost")
        if mult is None or mult < MIN_COST_MULTIPLE:
            continue
        rows = premium.strike_choice(
            inst, ratio, row["t1_pct"],
            reached_pct=row.get(f"reached_within_{reach.MAX_CAP}m_pct"),
            median_minutes=row.get("minutes_to_reach_median"),
        )
        for out in rows:
            out["selection_basis"] = "SHORTEST_COST_CLEARING_DISTANCE_NOT_VALIDATED"
            out["t1_in_multiples_of_cost"] = mult
            out["adverse_same_size_first_pct"] = row.get(
                f"adverse_same_size_first_within_{reach.MAX_CAP}m_pct"
            )
        return rows
    return []


def _five_year(inst: str, s, ratio: dict, hypotheses_total: int) -> dict:
    out: dict = {
        "instrument": inst,
        "tier": universe.FIVE_YEAR,
        "basis": MEASURED_UNDERLYING,
        "bars": len(s),
        "sides": {},
    }
    for side in reach.SIDES:
        r = reach.build(s, side)
        cost, cost_note = _cost(inst, r.entry)
        study = t1mod.study_instrument(r, cost)
        study["cost_unavailable"] = cost_note
        hold = t1mod.windows(r)[t1mod.HOLDOUT]
        # Descriptive, and reported whether or not anything is selected: how far a
        # target has to sit before the round trip is payable at all, with the
        # equal-sized adverse move beside every reach rate.
        study["cost_hurdle"] = t1mod.describe_reach(r, cost, hold)
        sel = study.get("selected")
        if sel is None:
            study["premium_translation"] = _descriptive_premium(
                inst, ratio, study["cost_hurdle"]
            )
        else:
            status, reasons = t1mod.verdict(sel, hypotheses_total)
            study["verdict"] = status
            study["verdict_reasons"] = reasons
            mins = reach.minutes_to_t1(r, sel["t1_pct"], sel["cap_min"], hold)
            total = int(hold.sum())
            reached = round(100.0 * mins.size / total, 3) if total else None
            study["premium_translation"] = premium.strike_choice(
                inst, ratio, sel["t1_pct"],
                reached_pct=reached,
                median_minutes=sel.get("minutes_to_t1_median"),
            )
            sel["holdout_t1_reached_pct"] = reached
        out["sides"][reach.side_name(side)] = study
    return out


def _short(inst: str, s) -> dict:
    out: dict = {
        "instrument": inst,
        "tier": universe.SHORT_CAPTURE,
        "basis": MEASURED_UNDERLYING,
        "bars": len(s),
        "verdict": REQUIRES_MORE_DATA,
        "verdict_reasons": [
            "history too short to split chronologically, so any T1 chosen on it "
            "would be fitted to the few sessions captured"
        ],
        "sides": {},
    }
    for side in reach.SIDES:
        r = reach.build(s, side)
        cost, cost_note = _cost(inst, r.entry)
        row = t1mod.describe_reach(r, cost)
        row["cost_unavailable"] = cost_note
        out["sides"][reach.side_name(side)] = row
    return out


def study(instruments: list[str] | None = None) -> dict:
    """Run the whole study and return the complete result."""
    started = time.time()
    inv = universe.inventory()
    if instruments:
        wanted = {i.upper() for i in instruments}
        inv = [row for row in inv if row["instrument"] in wanted]

    five = [row["instrument"] for row in inv if row["tier"] == universe.FIVE_YEAR]
    short = [row["instrument"] for row in inv if row["tier"] == universe.SHORT_CAPTURE]

    # Fixed before any outcome exists: one count for the whole study, so the
    # correction cannot shrink by reporting instruments separately.
    per_side = len(t1mod.configs())
    hypotheses_total = per_side * len(reach.SIDES) * max(1, len(five))

    ratio = (realized.summary() or {}).get("premium_to_strike_ratio") or {}

    results: dict[str, dict] = {}
    for inst in five:
        s, _tier = universe.load(inst)
        if s is None:
            continue
        results[inst] = _five_year(inst, s, ratio, hypotheses_total)
    for inst in short:
        s, _tier = universe.load(inst)
        if s is None or len(s) < reach.MAX_CAP + 3:
            continue
        results[inst] = _short(inst, s)

    validated = [
        f"{inst} {side}"
        for inst, row in results.items()
        for side, sr in row.get("sides", {}).items()
        if sr.get("verdict") == VALIDATED
    ]
    return {
        "phase": 32,
        "scope": "RESEARCH_ONLY",
        "inventory": inv,
        "five_year_instruments": five,
        "short_capture_instruments": short,
        "hypotheses_counted": hypotheses_total,
        "hypotheses_per_instrument_side": per_side,
        "bonferroni_threshold": 0.05 / max(1, hypotheses_total),
        "instruments": results,
        "validated": validated,
        "headline": VALIDATED if validated else NO_REACHABLE_T1,
        "bases_used": {
            MEASURED_UNDERLYING: "real one-minute bars; index or futures movement",
            DERIVED_PREMIUM: "stated arithmetic from a measured move; optimistic",
            REQUIRES_MORE_DATA: "captured candles only, no chronological split",
        },
        "limits": [
            "the historical feed publishes candles, not depth, so no net figure "
            "here carries a measured bid/ask spread and every one is optimistic "
            "by exactly one spread",
            "no premium percentage in this phase is measured; option books for "
            "these years do not exist in this store",
            "a reachable T1 is not a profitable T1: the adverse side of the same "
            "size is reported next to every reach rate for that reason",
        ],
        "runtime_sec": round(time.time() - started, 1),
    }
