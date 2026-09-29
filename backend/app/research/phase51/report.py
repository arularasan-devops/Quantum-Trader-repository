"""Phase 51 §14/§16 — the ranked table, the five counts, and the artefacts.

The ranking is deliberately dull. Candidates are ordered by the gate they
cleared and then by holdout expectancy — never by discovery P&L, which is the
one number a search of this size is guaranteed to maximise by accident.

If nothing survives, the table is empty and the counts say so. §15 is explicit
that "we found a profitable backtest" is not success, so this module has no path
that turns a lead into a candidate, and no path that prints a best row when no
row passed.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.research.phase51 import (
    HISTORICAL_LEAD,
    OVERFIT_RISK,
    PROMISING_NEEDS_DATA,
    REJECTED,
    ROBUST_CANDIDATE,
)
from app.research.phase51 import stats

ARTEFACT_DIR = Path("data/phase51")

STATUS_ORDER = {
    ROBUST_CANDIDATE: 0,
    PROMISING_NEEDS_DATA: 1,
    HISTORICAL_LEAD: 2,
    OVERFIT_RISK: 3,
    REJECTED: 4,
}

NO_RECOMMENDATION = (
    "EVERY_ROW_BELOW_IS_A_RESEARCH_OBSERVATION_ON_HISTORICAL_CANDLES_IT_IS_NOT_"
    "A_PREDICTION_A_RECOMMENDATION_A_SIGNAL_OR_A_PROMOTION_AND_NO_ROW_HERE_ARMS_"
    "ANY_GATE_OR_REACHES_ANY_ORDER_PATH"
)


def _row_view(r: dict) -> dict:
    """§14's schema, flattened for the table and the artefact."""
    d = r["discovery"]["summary"]
    v = (r.get("validation") or {}).get("summary") or {}
    h = (r.get("untouched_holdout") or {}).get("summary") or {}
    stress = (r.get("validation") or {}).get("cost_stress") or {}
    return {
        "candidate_id": r["candidate_id"],
        "mechanism_family": r["mechanism_family"],
        "human_readable_rule": r["human_readable_rule"],
        "instrument": r["instrument"],
        "entry_rule": r["rule_id"],
        "exit_rule": r.get("exit_rule", "STOP_1ATR_TARGET_1.5R_HORIZON_180_BARS"),
        "side": r["side"],
        "discovery_result": {
            "net_expectancy_r": d.get("net_expectancy_r"),
            "trade_count": d.get("trade_count"),
            "session_count": d.get("session_count"),
            "win_rate": d.get("win_rate"),
            "profit_factor": d.get("profit_factor"),
            "p_value_one_sided": d.get("p_value_one_sided"),
            "fdr_pass": r["fdr_pass"],
        },
        "validation_result": {
            "net_expectancy_r": v.get("net_expectancy_r"),
            "trade_count": v.get("trade_count"),
        } if v else None,
        "untouched_holdout_result": {
            "net_expectancy_r": h.get("net_expectancy_r"),
            "trade_count": h.get("trade_count"),
        } if h else None,
        "net_expectancy": d.get("net_expectancy_r"),
        "profit_factor": d.get("profit_factor"),
        "drawdown": d.get("max_drawdown_r"),
        "trade_count": d.get("trade_count"),
        "session_count": d.get("session_count"),
        "cost_stress": stress,
        "stability_status": r["stability_status"],
        "overfit_status": r["overfit_status"],
        "final_status": r["final_status"],
    }


def rank(result: dict) -> list[dict]:
    """Every row from every instrument, ordered by gate cleared, then holdout."""
    rows: list[dict] = []
    for inst in result["instruments"]:
        for r in inst.get("rows", []):
            rows.append(_row_view(r))

    def key(row: dict) -> tuple:
        h = row["untouched_holdout_result"] or {}
        v = row["validation_result"] or {}
        return (
            STATUS_ORDER.get(row["final_status"], 9),
            -(h.get("net_expectancy_r") or -1e9),
            -(v.get("net_expectancy_r") or -1e9),
        )

    rows.sort(key=key)
    return rows


def _fmt(x: object, nd: int = 3) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        if x != x:
            return "—"
        if x in (float("inf"), float("-inf")):
            return "inf"
        return f"{x:.{nd}f}"
    return str(x)


def render(result: dict, *, top: int = 20) -> str:
    out: list[str] = []
    a = out.append
    a("PHASE 51 — HISTORICAL ALPHA DISCOVERY")
    a(f"  preregistration {result['preregistration_fingerprint']}")
    a("  HISTORICAL_CANDLE_DATA   research only   no order path reachable")
    a("")

    a("  UNIVERSE")
    for d in result["universe"]["eligible"]:
        a(f"    {d['instrument']:12s} {d['bars']:>9,} bars  "
          f"{d['sessions']:>6,} sessions  {d['source']}")
    for d in result["universe"]["refused"]:
        a(f"    {d['instrument']:12s} REFUSED  {d.get('exclusion_reason')}")
    a("")

    for inst in result["instruments"]:
        if not inst.get("eligible"):
            a(f"  {inst['instrument']}: not eligible — {inst.get('reason')}")
            continue
        ps = inst["partition_sessions"]
        a(f"  {inst['instrument']}  sessions {inst['sessions']:,}  "
          f"discovery {ps['DISCOVERY']}  validation {ps['VALIDATION']}  "
          f"untouched holdout {ps['UNTOUCHED_HOLDOUT']}")
        a(f"    hypotheses {inst['total_hypotheses_tested']:,}   "
          f"discovery leads {inst['total_discovery_leads']}   "
          f"validation leads {inst['total_validation_leads']}   "
          f"holdout positive {inst['total_untouched_holdout_positive']}   "
          f"robust {inst['total_robust_candidates']}   "
          f"{inst['elapsed_seconds']}s")
    a("")

    t = result["totals"]
    a("  COUNTS")
    for k in ("TOTAL_HYPOTHESES_TESTED", "TOTAL_DISCOVERY_LEADS",
              "TOTAL_VALIDATION_LEADS", "TOTAL_UNTOUCHED_HOLDOUT_POSITIVE",
              "TOTAL_ROBUST_CANDIDATES"):
        a(f"    {k:38s} {t[k]:,}")
    a("")

    rows = rank(result)
    robust = [r for r in rows if r["final_status"] == ROBUST_CANDIDATE]
    a(f"  ROBUST_CANDIDATES — {len(robust)}")
    if not robust:
        a("    NONE. Zero rules cleared every gate, which is reported as zero and")
        a("    not replaced by the best-looking row. The funnel above says where")
        a("    they died; a search that returns nothing has either been strict")
        a("    or been asked of data too thin, and the counts distinguish those.")
    else:
        for r in robust[:top]:
            a(f"    {r['candidate_id']}  {r['instrument']:9s} {r['side']:5s} "
              f"holdout {_fmt((r['untouched_holdout_result'] or {}).get('net_expectancy_r'))}R  "
              f"{r['human_readable_rule']}")
    a("")

    a(f"  DISCOVERY_LEADS — top {top} by gate cleared, not by historical P&L")
    a(f"    {'candidate':16s} {'instrument':10s} {'side':5s} {'status':22s} "
      f"{'disc R':>8s} {'val R':>8s} {'hold R':>8s} {'trades':>7s} rule")
    for r in rows[:top]:
        v = (r["validation_result"] or {}).get("net_expectancy_r")
        h = (r["untouched_holdout_result"] or {}).get("net_expectancy_r")
        a(f"    {r['candidate_id']:16s} {r['instrument']:10s} {r['side']:5s} "
          f"{r['final_status']:22s} {_fmt(r['net_expectancy'], 3):>8s} "
          f"{_fmt(v, 3):>8s} {_fmt(h, 3):>8s} "
          f"{r['trade_count']:>7,} {r['human_readable_rule'][:70]}")
    if not rows:
        a("    none — no rule reached the discovery-lead floor")
    a("")

    a("  FAMILIES THAT CANNOT BE MEASURED ON THIS DATA")
    for k, v in result["unmeasurable_families"].items():
        a(f"    {k:34s} {PROMISING_NEEDS_DATA}  {v}")
    a("")
    a(f"  {stats.P_VALUE_IS_ORDERING_ONLY}")
    a(f"  {NO_RECOMMENDATION}")
    return "\n".join(out)


def write_artefacts(result: dict, *, directory: Path | None = None) -> list[Path]:
    """Immutable artefacts, named by fingerprint and write time.

    Nothing is overwritten: a run is evidence, and evidence that can be replaced
    in place is not evidence.
    """
    d = directory or ARTEFACT_DIR
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    fp = result["preregistration_fingerprint"]
    rows = rank(result)
    files = {
        f"phase51_run_{fp}_{stamp}.json": {
            "preregistration_fingerprint": fp,
            "universe": result["universe"],
            "totals": result["totals"],
            "instruments": [
                {k: v for k, v in inst.items() if k != "rows"}
                for inst in result["instruments"]
            ],
            "unmeasurable_families": result["unmeasurable_families"],
            "p_value_caveat": result["p_value_caveat"],
            "no_recommendation": NO_RECOMMENDATION,
        },
        f"phase51_discovery_leads_{fp}_{stamp}.json": {
            "no_recommendation": NO_RECOMMENDATION,
            "rows": rows,
        },
        f"phase51_robust_candidates_{fp}_{stamp}.json": {
            "no_recommendation": NO_RECOMMENDATION,
            "rows": [r for r in rows if r["final_status"] == ROBUST_CANDIDATE],
        },
        f"phase51_report_{fp}_{stamp}.txt": None,
    }
    written: list[Path] = []
    for name, payload in files.items():
        p = d / name
        if p.exists():
            continue
        if payload is None:
            p.write_text(render(result), encoding="utf-8")
        else:
            p.write_text(json.dumps(payload, indent=2, default=str),
                         encoding="utf-8")
        written.append(p)
    return written
