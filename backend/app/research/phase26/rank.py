"""Phase 26 §5 — rank the event cohorts, and cap what any of them can claim.

Same shape as Phase 25's gate, with two deliberate differences:

* the multiple-testing correction is applied against the **number of hypotheses
  actually evaluated**, not against the handful that reached the gate. Phase 25
  corrected over the survivors, which is the friendlier denominator; here the
  p-value list is padded to the real test count, so a cohort has to clear a
  harder bar than it did in the previous study;
* a cohort carries the exit variant it was measured under. A result that only
  exists under one exit rule is a property of that rule, and hiding which one
  would make it unreproducible.

``RESEARCH_LEAD`` remains the ceiling. There is no ``VALIDATED`` in this
vocabulary and nothing here writes to a signal, a book or the order path.
"""
from __future__ import annotations

import hashlib
import json

from app.research.phase24 import metrics
from app.research.phase24.discover import benjamini_hochberg
from app.research.phase25.discover import MIN_WINDOW_TRADES
from app.research.phase26 import (
    REJECTED,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD,
    RESEARCH_ONLY,
    WINDOW_CLAIM,
)

MIN_PROFIT_FACTOR = 1.0
MAX_OUTLIER_CONTRIBUTION_PCT = 40.0
FDR_ALPHA = 0.05


def _score(row: dict) -> float:
    """One comparable number, weighted to out-of-sample net R."""
    dev = row.get("development") or {}
    val = row.get("validation") or {}
    hold = row.get("holdout") or {}
    score = 1.0 * float(dev.get("avg_net_r") or 0.0)
    score += 2.0 * float(val.get("avg_net_r") or 0.0)
    score += 3.0 * float(hold.get("avg_net_r") or 0.0)
    pf = hold.get("profit_factor") or val.get("profit_factor")
    if isinstance(pf, (int, float)):
        score += 0.5 * min(float(pf), 3.0)
    measurable = row.get("folds_measurable") or 0
    if measurable:
        score += 1.0 * (row.get("folds_positive", 0) / measurable)
    score -= 0.25 * (len(row.get("conditions") or []) - 1)
    for window in (dev, val, hold):
        if window.get("trades", 0) < MIN_WINDOW_TRADES:
            score -= 0.5
        outlier = window.get("outlier_top1_contribution_pct")
        if isinstance(outlier, (int, float)) and outlier > MAX_OUTLIER_CONTRIBUTION_PCT:
            score -= 0.5
    stress = row.get("robustness") or []
    if stress:
        score -= 0.75 * (sum(1 for r in stress if not r.get("survives")) / len(stress))
    return round(score, 4)


def gate(row: dict, *, fdr_survivor: bool) -> tuple[str, list[str]]:
    """Verdict plus every failed clause, in plain words.

    ``REQUIRES_MORE_DATA`` is kept separate from ``REJECTED`` on purpose: a
    cohort the window is too small to judge has not been refuted, and reporting
    missing evidence as a negative result is how a study lies without stating a
    single false number.
    """
    dev = row.get("development") or {}
    val = row.get("validation") or {}
    hold = row.get("holdout") or {}
    stress = row.get("robustness") or []
    reasons: list[str] = []
    fatal = False
    thin = False

    if (dev.get("avg_net_r") or -1) <= 0:
        reasons.append("development net R is not positive")
        fatal = True
    for label, window in (("validation", val), ("holdout", hold)):
        n = window.get("trades", 0)
        if n < MIN_WINDOW_TRADES:
            reasons.append(
                f"{label} sample is {n} trades, below {MIN_WINDOW_TRADES} — the "
                "captured window is too short to confirm or refuse this"
            )
            thin = True
        elif (window.get("avg_net_r") or -1) <= 0:
            reasons.append(f"{label} net R is not positive")
            fatal = True
    pf = hold.get("profit_factor")
    if hold.get("trades", 0) >= MIN_WINDOW_TRADES and (
        not isinstance(pf, (int, float)) or pf <= MIN_PROFIT_FACTOR
    ):
        reasons.append("holdout profit factor is not above 1")
        fatal = True
    measurable = row.get("folds_measurable") or 0
    if measurable and row.get("folds_positive", 0) < measurable:
        reasons.append(
            f"session folds positive in {row.get('folds_positive', 0)}/{measurable}"
        )
    if not measurable:
        reasons.append("no session fold reached the minimum sample")
        thin = True
    if stress and any(not r.get("survives") for r in stress):
        failed = [r["variant"] for r in stress if not r.get("survives")]
        reasons.append(
            "fails execution stress: " + ", ".join(failed)
        )
    if not fdr_survivor:
        reasons.append(
            "does not survive multiple-testing correction over every hypothesis "
            "evaluated"
        )
    outlier = hold.get("outlier_top1_contribution_pct")
    if isinstance(outlier, (int, float)) and outlier > MAX_OUTLIER_CONTRIBUTION_PCT:
        reasons.append(f"top 1% of winners contribute {outlier}% of holdout net R")
    if hold.get("trades", 0) < metrics.MIN_TRADES:
        reasons.append(
            f"holdout sample is below the {metrics.MIN_TRADES}-trade reporting bar, "
            "so this is a lead to collect live evidence for, not a result"
        )
        thin = True

    if not reasons:
        return RESEARCH_LEAD, []
    if fatal:
        return REJECTED, reasons
    return (REQUIRES_MORE_DATA if thin else RESEARCH_ONLY), reasons


def rank(rows: list[dict], *, hypotheses_evaluated: int = 0) -> list[dict]:
    """Score, correct over the honest denominator, gate, and sort.

    ``hypotheses_evaluated`` is every event/side/economics combination that was
    tested, including the ones that never reached the gate. The p-value list is
    padded to that length so the correction is not computed over a
    self-selected sample.
    """
    p_values = [
        float((r.get("holdout") or {}).get("p_value_vs_base_rate") or 1.0)
        for r in rows
    ]
    pad = max(0, int(hypotheses_evaluated) - len(p_values))
    keep = benjamini_hochberg(p_values + [1.0] * pad, FDR_ALPHA)[:len(p_values)]
    out: list[dict] = []
    for r, survivor in zip(rows, keep, strict=False):
        r = dict(r)
        r["score"] = _score(r)
        r["fdr_survivor"] = bool(survivor)
        r["fdr_denominator"] = max(int(hypotheses_evaluated), len(p_values))
        status, reasons = gate(r, fdr_survivor=bool(survivor))
        r["status"] = status
        r["failed_clauses"] = reasons
        r["strategy_id"] = strategy_id(r)
        r["fingerprint"] = fingerprint(r)
        out.append(r)
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


def strategy_id(row: dict) -> str:
    """Stable short id from the definition, never from the results."""
    payload = json.dumps({
        "instrument": row["instrument"],
        "option_type": row["option_type"],
        "variant": row["variant"],
        "conditions": sorted(row["conditions"]),
    }, sort_keys=True)
    return "P26_" + hashlib.sha256(payload.encode()).hexdigest()[:12]


def fingerprint(row: dict) -> dict:
    """Machine-readable match rule. Nothing is wired to it."""
    return {
        "version": "PHASE26_FINGERPRINT_V1",
        "instrument": row["instrument"],
        "vehicle": row["option_type"],
        "exit_variant": row["variant"],
        "require_all": sorted(row["conditions"]),
        "status": row.get("status", RESEARCH_ONLY),
        "paper_only": True,
        "window": WINDOW_CLAIM,
    }
