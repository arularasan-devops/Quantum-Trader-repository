"""Phase 29 §7 — ranking, and the ceiling on any verdict.

Written so the default answer is no and the *best* answer is still not a
promotion. A structure that passes every clause is ``RESEARCH_LEAD``: worth
collecting live paper evidence for, not worth trading. There is deliberately no
``VALIDATED`` in this module's vocabulary, because a window of weeks cannot
support that word however good the internal agreement looks.

Two clauses exist only in this phase, because they are the two ways a credit
spread flatters itself:

* **the win rate is not the result.** A seller who keeps half the credit 85% of
  the time and loses one credit the rest of the time is flat before costs, so a
  high ``target_before_stop`` rate with a non-positive net R is refused
  explicitly rather than allowed to look like a near miss;
* **friction against credit is a hard gate.** A structure whose round-trip
  friction exceeds its whole credit cannot be profitable at any hit rate, and is
  rejected on arithmetic before its statistics are read.
"""
from __future__ import annotations

import hashlib
import json

import numpy as np

from app.research.phase24 import discover as p24discover
from app.research.phase24 import metrics
from app.research.phase29 import (
    ASSIGNMENT_CLAIM,
    MARGIN_CLAIM,
    REJECTED,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD,
    RESEARCH_ONLY,
    WINDOW_CLAIM,
)
from app.research.phase29.discover import MIN_WINDOW_TRADES

MIN_PROFIT_FACTOR = 1.0
MAX_COMPLEXITY = 3
MAX_OUTLIER_CONTRIBUTION_PCT = 40.0
MAX_FRICTION_PCT_OF_CREDIT = 100.0
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
    if isinstance(pf, (int, float)) and np.isfinite(pf):
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
    """Verdict plus every clause that failed, in plain words."""
    dev = rule.get("development") or {}
    val = rule.get("validation") or {}
    hold = rule.get("holdout") or {}
    stress = rule.get("robustness") or []
    reasons: list[str] = []
    fatal = False
    thin = False

    friction = rule.get("median_friction_pct_of_credit")
    if isinstance(friction, (int, float)) and friction >= MAX_FRICTION_PCT_OF_CREDIT:
        reasons.append(
            f"round-trip friction is {friction:.1f}% of the credit collected, so "
            "the structure cannot be profitable at any hit rate"
        )
        fatal = True

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
            hit = window.get("t1_before_sl_pct")
            extra = (
                f" despite keeping the target credit {hit}% of the time"
                if isinstance(hit, (int, float)) and hit >= 60.0 else ""
            )
            reasons.append(f"{label} net R is not positive{extra}")
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
            "structure": rule["structure"],
            "hold": rule.get("hold"),
            "short_steps": rule["short_steps_otm"],
            "width_steps": rule["width_steps"],
            "stop_credit_multiple": rule["stop_credit_multiple"],
            "conditions": sorted(rule["conditions"]),
        },
        sort_keys=True,
    )
    return "P29_" + hashlib.sha256(payload.encode()).hexdigest()[:12]


def fingerprint(rule: dict) -> dict:
    """Machine-readable match rule, populated from the discovered definition.

    Nothing is wired to it. ``paper_only`` is unconditionally true: no status this
    module can produce would justify anything else, and a short structure is the
    one place where an accidental promotion has an open-ended cost.
    """
    return {
        "version": "PHASE29_FINGERPRINT_V1",
        "instrument": rule["instrument"],
        "vehicle": "OPTION_CREDIT_SPREAD",
        "structure": rule["structure"],
        "hold": rule.get("hold"),
        "underlying_view": rule["underlying_view"],
        "short_steps_otm": rule["short_steps_otm"],
        "width_steps": rule["width_steps"],
        "width_points": rule["width_points"],
        "stop_credit_multiple": rule["stop_credit_multiple"],
        "take_profit_fraction_of_credit": rule.get("take_profit_fraction"),
        "require_all": sorted(rule["conditions"]),
        "status": rule.get("status", RESEARCH_ONLY),
        "paper_only": True,
        "defined_risk": True,
        "window": WINDOW_CLAIM,
        "margin": MARGIN_CLAIM,
        "assignment": ASSIGNMENT_CLAIM,
    }
