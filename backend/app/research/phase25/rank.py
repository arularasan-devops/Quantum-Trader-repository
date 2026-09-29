"""Phase 25 §7 — ranking and the ceiling on any verdict.

The gate is written so the default answer is no, and so the *best* answer is
still not a promotion. A rule that passes every clause is ``RESEARCH_LEAD``:
worth collecting live paper evidence for, not worth trading. There is
deliberately no ``VALIDATED`` in this module's vocabulary, because a window of
weeks cannot support that word however good the internal agreement looks.

Nothing here writes to a book, a signal or the order path.
"""
from __future__ import annotations

import hashlib
import json

from app.research.phase24 import discover as p24discover
from app.research.phase24 import metrics
from app.research.phase25 import (
    REJECTED,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD,
    RESEARCH_ONLY,
    WINDOW_CLAIM,
)
from app.research.phase25.discover import MIN_WINDOW_TRADES

MIN_PROFIT_FACTOR = 1.0
MAX_COMPLEXITY = 3
MAX_OUTLIER_CONTRIBUTION_PCT = 40.0
FDR_ALPHA = 0.05


def _score(rule: dict) -> float:
    """One comparable number, weighted to out-of-sample net R, penalties included."""
    dev = rule.get("development") or {}
    val = rule.get("validation") or {}
    hold = rule.get("holdout") or {}
    score = 0.0
    score += 1.0 * float(dev.get("avg_net_r") or 0.0)
    score += 2.0 * float(val.get("avg_net_r") or 0.0)
    score += 3.0 * float(hold.get("avg_net_r") or 0.0)
    pf = hold.get("profit_factor") or val.get("profit_factor")
    if isinstance(pf, (int, float)):
        score += 0.5 * min(float(pf), 3.0)
    measurable = rule.get("folds_measurable") or 0
    if measurable:
        score += 1.0 * (rule.get("folds_positive", 0) / measurable)
    score -= 0.25 * (rule.get("complexity", 1) - 1)
    for window in (dev, val, hold):
        if window.get("trades", 0) < MIN_WINDOW_TRADES:
            score -= 0.5
        outlier = window.get("outlier_top1_contribution_pct")
        if isinstance(outlier, (int, float)) and outlier > MAX_OUTLIER_CONTRIBUTION_PCT:
            score -= 0.5
    stress = rule.get("robustness") or []
    if stress:
        failed = sum(1 for r in stress if not r.get("survives"))
        score -= 0.75 * (failed / len(stress))
    return round(score, 4)


def gate(rule: dict, *, fdr_survivor: bool) -> tuple[str, list[str]]:
    """Verdict plus every clause that failed, in plain words.

    ``RESEARCH_LEAD`` is the ceiling. ``REQUIRES_MORE_DATA`` is used when the
    rule is not contradicted but the captured window is simply too small to say
    anything — that is a different answer from ``REJECTED`` and is kept
    separate, because reporting missing data as a negative result is how a study
    lies without stating a single false number.
    """
    dev = rule.get("development") or {}
    val = rule.get("validation") or {}
    hold = rule.get("holdout") or {}
    stress = rule.get("robustness") or []
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
                f"{label} sample is {n} trades, below {MIN_WINDOW_TRADES} — "
                "the captured window is too short to confirm or refuse this"
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
    measurable = rule.get("folds_measurable") or 0
    if measurable and rule.get("folds_positive", 0) < measurable:
        reasons.append(
            f"session folds positive in {rule.get('folds_positive', 0)}/{measurable}"
        )
    if not measurable:
        reasons.append("no session fold reached the minimum sample")
        thin = True
    if stress and any(not r.get("survives") for r in stress):
        reasons.append("fails at least one cost, slippage or exit-delay variant")
    if rule.get("complexity", 1) > MAX_COMPLEXITY:
        reasons.append("more conditions than the complexity ceiling")
    if not fdr_survivor:
        reasons.append("does not survive multiple-testing correction")
    if not rule.get("dev_gate_passed"):
        reasons.append("did not clear the development gate")
        fatal = True
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


def rank(rules: list[dict]) -> list[dict]:
    """Score, correct for multiple testing, gate, and sort."""
    p_values = [
        float((r.get("holdout") or {}).get("p_value_vs_base_rate") or 1.0)
        for r in rules
    ]
    keep = p24discover.benjamini_hochberg(p_values, FDR_ALPHA)
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
            "option_type": rule["option_type"],
            "stop_band": rule["stop_band_pct_of_premium"],
            "conditions": sorted(rule["conditions"]),
        },
        sort_keys=True,
    )
    return "P25_" + hashlib.sha256(payload.encode()).hexdigest()[:12]


def fingerprint(rule: dict) -> dict:
    """Machine-readable match rule, populated from the discovered definition.

    Nothing is wired to it. ``paper_only`` is unconditionally true here: no
    status this module can produce would justify anything else.
    """
    return {
        "version": "PHASE25_FINGERPRINT_V1",
        "instrument": rule["instrument"],
        "vehicle": rule["option_type"],
        "underlying_view": rule["side"],
        "stop_pct_of_premium": rule["stop_band_pct_of_premium"],
        "t1_r": rule.get("t1_r"),
        "require_all": sorted(rule["conditions"]),
        "status": rule.get("status", RESEARCH_ONLY),
        "paper_only": True,
        "window": WINDOW_CLAIM,
    }
