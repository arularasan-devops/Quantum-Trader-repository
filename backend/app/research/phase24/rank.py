"""Phase 24 §6/§15/§16 — composite ranking, the promotion gate, fingerprints.

The gate is written so that the default answer is no. A rule reaches VALIDATED
only by passing every clause; anything that merely looks good is RESEARCH_ONLY,
and anything that breaks on out-of-sample data or under cost stress is REJECTED.
Nothing here promotes anything into the live engine: the status is a label on a
report, and the production path is not consulted or changed.
"""
from __future__ import annotations

import hashlib
import json

from app.research.phase24 import REJECTED, RESEARCH_ONLY, VALIDATED, discover, metrics

# §15 promotion clauses.
MIN_HOLDOUT_TRADES = 100
MIN_PROFIT_FACTOR = 1.0
MAX_COMPLEXITY = 3
MAX_OUTLIER_CONTRIBUTION_PCT = 40.0
FDR_ALPHA = 0.05


def _score(rule: dict) -> float:
    """A single comparable number, penalties included.

    Weighted towards out-of-sample net R rather than the hit rate: §6 forbids
    ranking on win rate, and development performance is the one number every
    overfitted rule already has.
    """
    dev = rule.get("development") or {}
    val = rule.get("validation") or {}
    hold = rule.get("holdout") or {}
    wf = rule.get("walk_forward") or {}
    score = 0.0
    score += 1.0 * float(dev.get("avg_net_r") or 0.0)
    score += 2.0 * float(val.get("avg_net_r") or 0.0)
    score += 3.0 * float(hold.get("avg_net_r") or 0.0)
    pf = hold.get("profit_factor") or val.get("profit_factor")
    if isinstance(pf, (int, float)):
        score += 0.5 * min(float(pf), 3.0)
    folds = wf.get("folds_scored") or 0
    if folds:
        score += 1.0 * (wf.get("folds_positive", 0) / folds)
    # Penalties.
    score -= 0.25 * (rule.get("complexity", 1) - 1)
    for window in (dev, val, hold):
        if window.get("trades", 0) < metrics.MIN_TRADES:
            score -= 0.5
        outlier = window.get("outlier_top1_contribution_pct")
        if isinstance(outlier, (int, float)) and outlier > MAX_OUTLIER_CONTRIBUTION_PCT:
            score -= 0.5
        dd = window.get("max_drawdown_r")
        total = window.get("total_net_r")
        if isinstance(dd, (int, float)) and isinstance(total, (int, float)) and total > 0:
            score -= 0.5 * min(1.0, dd / max(total, 1e-9))
    stress = rule.get("cost_sensitivity") or []
    if stress:
        failed = sum(1 for r in stress if not r.get("survives"))
        score -= 0.75 * (failed / len(stress))
    return round(score, 4)


def gate(rule: dict, *, fdr_survivor: bool) -> tuple[str, list[str]]:
    """Promotion verdict plus every clause that failed, in plain words."""
    dev = rule.get("development") or {}
    val = rule.get("validation") or {}
    hold = rule.get("holdout") or {}
    wf = rule.get("walk_forward") or {}
    stress = rule.get("cost_sensitivity") or []
    perturb = rule.get("parameter_perturbation") or []
    reasons: list[str] = []
    fatal = False

    if (dev.get("avg_net_r") or -1) <= 0:
        reasons.append("development net R is not positive")
        fatal = True
    if (val.get("avg_net_r") or -1) <= 0:
        reasons.append("validation year net R is not positive")
        fatal = True
    if (hold.get("avg_net_r") or -1) <= 0:
        reasons.append("final unseen holdout net R is not positive")
        fatal = True
    if hold.get("trades", 0) < MIN_HOLDOUT_TRADES:
        reasons.append(
            f"holdout sample is {hold.get('trades', 0)} trades, below {MIN_HOLDOUT_TRADES}"
        )
    pf = hold.get("profit_factor")
    if not isinstance(pf, (int, float)) or pf <= MIN_PROFIT_FACTOR:
        reasons.append("holdout profit factor is not above 1")
    if not wf.get("stable"):
        reasons.append(
            f"walk-forward positive in {wf.get('folds_positive', 0)}/"
            f"{wf.get('folds_scored', 0)} folds"
        )
    if stress and any(not r.get("survives") for r in stress):
        reasons.append("fails at least one cost/entry/exit stress variant")
    if perturb and any(not r.get("survives") for r in perturb):
        reasons.append("collapses when a discovered band is shifted to a neighbour")
    if rule.get("complexity", 1) > MAX_COMPLEXITY:
        reasons.append("more conditions than the complexity ceiling")
    if not fdr_survivor:
        reasons.append("does not survive multiple-testing correction")
    if not rule.get("dev_gate_passed"):
        # Near-miss cohorts are carried into the report so the closest failure is
        # visible, but a cohort that never cleared development cannot be promoted
        # by a lucky holdout.
        reasons.append("did not clear the development gate")
        fatal = True
    outlier = hold.get("outlier_top1_contribution_pct")
    if isinstance(outlier, (int, float)) and outlier > MAX_OUTLIER_CONTRIBUTION_PCT:
        reasons.append(f"top 1% of winners contribute {outlier}% of holdout net R")

    if not reasons:
        return VALIDATED, []
    return (REJECTED if fatal else RESEARCH_ONLY), reasons


def rank(rules: list[dict]) -> list[dict]:
    """Score, correct for multiple testing, gate, and sort."""
    p_values = [
        float((r.get("holdout") or {}).get("p_value_vs_base_rate") or 1.0)
        for r in rules
    ]
    keep = discover.benjamini_hochberg(p_values, FDR_ALPHA)
    out: list[dict] = []
    for r, survivor in zip(rules, keep, strict=False):
        r = dict(r)
        r["score"] = _score(r)
        r["fdr_survivor"] = bool(survivor)
        status, reasons = gate(r, fdr_survivor=bool(survivor))
        r["status"] = status
        r["failed_clauses"] = reasons
        r["strategy_id"] = strategy_id(r)
        r["fingerprint"] = fingerprint(r)
        out.append(r)
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


def strategy_id(rule: dict) -> str:
    """Stable short id from the rule's own definition, not from its results."""
    payload = json.dumps(
        {
            "instrument": rule["instrument"],
            "side": rule["side"],
            "stop_band_atr": rule["stop_band_atr"],
            "conditions": sorted(rule["conditions"]),
        },
        sort_keys=True,
    )
    return "P24_" + hashlib.sha256(payload.encode()).hexdigest()[:12]


def fingerprint(rule: dict) -> dict:
    """Machine-readable match rule, populated from the discovered definition.

    A live engine could evaluate this against the current bar without knowing
    anything about Phase 24 — but nothing is wired to it, and only a VALIDATED
    status would make that appropriate.
    """
    return {
        "version": "PHASE24_FINGERPRINT_V1",
        "instrument": rule["instrument"],
        "vehicle": rule["vehicle"],
        "side": rule["side"],
        "stop_atr": rule["stop_band_atr"],
        "t1_r": rule.get("t1_r"),
        "require_all": sorted(rule["conditions"]),
        "status": rule.get("status", RESEARCH_ONLY),
        "paper_only": rule.get("status") != VALIDATED,
    }
