"""Phase 33 — the run: inventory, movement, economics, giveback, selection, verdict.

The order is deliberate and is the honesty of the phase:

1. inventory and the answerability map, so which of the 21 questions can be
   answered is written down *before* any outcome exists;
2. the measured five-year futures work — hold grid, movement distribution,
   giveback, empirical targets, overnight — per instrument, side and family;
3. selection on development data only, then validation, the untouched holdout,
   walk-forward, cost stress and outlier removal;
4. the multiple-testing correction over every horizon looked at;
5. the captured-window diagnostics — engine pool against board pool, option
   cohorts — which are labelled ``COUNTERFACTUAL`` or ``REQUIRES_MORE_DATA`` and
   can never turn into a verdict on their own.

Nothing here writes to production, and no discovered holding period is wired
anywhere: the output is a report.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase32.reach import cost_pct
from app.research.phase33 import (
    HOLD_GRID,
    MAX_HOLD,
    NO_HOLD_WINDOW,
    REQUIRES_MORE_DATA,
)
from app.research.phase33 import cohorts as p33cohorts
from app.research.phase33 import compare, families, hold, overnight, select, universe
from app.research.phase33.path import SIDES, build, side_name

# The horizon whose movement distribution and target percentiles are reported.
# It is the longest frozen intraday hold, so the reach curve is not truncated
# before the distances it is asked about can be reached.
REPORT_HORIZON = MAX_HOLD


def _underlying_map(
    instruments: list[str], minutes: set[int]
) -> dict[tuple[str, int], float]:
    """(instrument, minute) -> measured close, used only to label moneyness.

    Only the minutes the board actually observed are kept: a moneyness label needs
    the underlying at that instant, and holding five years of closes in a dict to
    answer a few thousand lookups is waste, not evidence.
    """
    out: dict[tuple[str, int], float] = {}
    for inst in instruments:
        s = p24data.load_series(inst)
        if s is None:
            continue
        ts = (np.asarray(s.ts, dtype=np.int64) // 60) * 60
        keep = np.isin(ts, np.fromiter(minutes, dtype=np.int64, count=len(minutes))) \
            if minutes else np.zeros(ts.size, dtype=bool)
        for t, c in zip(ts[keep].tolist(),
                        np.asarray(s.close, dtype=float)[keep].tolist()):
            out[(inst, t)] = c
    return out


def _instrument(inst: str) -> dict:
    """Everything measurable for one five-year instrument."""
    s = p24data.load_series(inst)
    if s is None:
        return {"instrument": inst, "status": REQUIRES_MORE_DATA,
                "reason": "no one-minute series could be loaded"}
    out: dict = {"instrument": inst, "bars": len(s), "sides": {}}
    paths: dict[int, object] = {}
    costs: np.ndarray | None = None
    for side in SIDES:
        p = build(s, side)
        paths[side] = p
        if costs is None:
            costs = cost_pct(inst, p.entry)
        c = families.build(s, side)
        rows: list[dict] = []
        for fam in families.NAMES:
            mask = c.masks[fam] & p.eligible
            n = int(mask.sum())
            row: dict = {
                "family": fam,
                "n": n,
                "sessions": int(np.unique(p.session[mask]).size),
                "rankable": families.rankable(mask),
            }
            if n:
                row["hold_table"] = hold.hold_table(p, mask, costs)
                row["move_distribution"] = hold.move_distribution(
                    p, mask, REPORT_HORIZON
                )
                row["giveback"] = hold.giveback(p, mask, costs)
                row["empirical_targets"] = hold.empirical_targets(
                    p, mask, costs, REPORT_HORIZON
                )
                row["overnight"] = overnight.measure(s, p, mask)
                row["selection"] = select.evaluate(p, mask, costs, fam)
            else:
                row["status"] = REQUIRES_MORE_DATA
                row["reason"] = (
                    "the family never occurred on an eligible bar of this "
                    "instrument and side"
                )
            rows.append(row)
        out["sides"][side_name(side)] = {
            "eligible_rows": int(p.eligible.sum()),
            "eligible_sessions": int(np.unique(p.session[p.eligible]).size),
            "families": rows,
        }
    out["paths"] = paths
    out["cost_pct"] = costs
    out["cost_pct_median"] = (
        None if costs is None or not np.isfinite(costs).any()
        else round(float(np.nanmedian(costs)), 6)
    )
    return out


def run() -> dict:
    """The whole phase, as one dictionary."""
    answer = universe.answerability()
    five_year = list(answer["five_year_instruments"])

    measured: list[dict] = []
    for inst in five_year:
        measured.append(_instrument(inst))

    selections = [
        fam["selection"]
        for m in measured
        for side in m.get("sides", {}).values()
        for fam in side["families"]
        if "selection" in fam
    ]
    correction = select.apply_correction(selections)

    engine = compare.engine_pool()
    board = compare.board_pool()
    cov = compare.coverage(engine, board)
    paths = {
        m["instrument"]: m["paths"] for m in measured if "paths" in m
    }
    costs = {m["instrument"]: m["cost_pct"] for m in measured if "cost_pct" in m}
    diagnosis = compare.underlying_diagnosis(
        engine, board, paths, costs, horizon=HOLD_GRID[-1]
    )
    board_minutes = {r["ts"] - (r["ts"] % 60) for r in board}
    option_cohorts = p33cohorts.split(
        board, _underlying_map(five_year, board_minutes)
    )

    for m in measured:
        m.pop("paths", None)
        m.pop("cost_pct", None)

    return {
        "answerability": answer,
        "five_year_instruments": five_year,
        "instruments": measured,
        "selections": selections,
        "correction": correction,
        "engine_vs_board": {
            "coverage": cov,
            "underlying_diagnosis": diagnosis,
        },
        "option_cohorts": option_cohorts,
        "verdict": correction["verdict"],
        "verdict_scope": "FUTURES_UNDERLYING_ONLY",
        "verdict_note": (
            "the verdict is about holding windows on measured one-minute futures "
            "data; option premium holding windows are "
            f"{option_cohorts['premium_hold_status']} and are not part of it"
        ),
        "production_changed": False,
    }


def headline(res: dict) -> str:
    v = res.get("verdict") or NO_HOLD_WINDOW
    return f"{v} — {res['correction']['hypotheses_counted']} hypotheses counted"
