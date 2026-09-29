"""Phase 27 §4 — ranking, the promotion gate and the research ceiling.

The gate is Phase 24's, imported rather than restated: development, validation and
untouched-holdout net R all positive, a holdout sample of at least 100 trades, a
holdout profit factor above 1, walk-forward positive in every scored fold, every
cost/slippage/timing variant survived, no collapse when a discovered band is
shifted, bounded complexity, and survival of the Benjamini-Hochberg correction
over the honest hypothesis count. The default answer is no.

One clause is added here, and only one, because this study's whole premise is the
unmeasured futures spread: a rule that is positive at the configured slippage but
not at two points per side is **not** treated as validated. It is a lead at best.

The ceiling in this phase is ``RESEARCH_LEAD``. Nothing self-promotes, nothing is
wired to the live engine, and a rule that clears every clause is still an argument
for paper collection rather than a decision to trade.
"""
from __future__ import annotations

import hashlib
import json

from app.research.phase24 import metrics, rank as p24rank
from app.research.phase27 import (
    REJECTED,
    RESEARCH_LEAD,
    RESEARCH_ONLY,
    VALIDATED,
    discover,
)

MIN_HOLDOUT_TRADES = p24rank.MIN_HOLDOUT_TRADES
MIN_PROFIT_FACTOR = p24rank.MIN_PROFIT_FACTOR
MAX_COMPLEXITY = p24rank.MAX_COMPLEXITY
MAX_OUTLIER_CONTRIBUTION_PCT = p24rank.MAX_OUTLIER_CONTRIBUTION_PCT
FDR_ALPHA = p24rank.FDR_ALPHA

# A rule that clears every clause carries the gate verdict VALIDATED and the
# research label RESEARCH_LEAD: there is deliberately no path from this study to a
# live decision, so the highest label it can publish is a lead.

# The slippage variant a lead must survive to be more than a lead, in points per
# side. The configured baseline is one point.
REQUIRED_SLIPPAGE_POINTS = 2.0


def score(rule: dict) -> float:
    """One comparable number, with the same weights and penalties as Phase 24.

    Weighted towards out-of-sample net R rather than the hit rate, and penalised
    for complexity, thin windows, outlier dependence, drawdown and failed stress
    variants. Restated here rather than imported from Phase 24's private helper so
    that this phase cannot change behaviour if that helper is ever refactored.
    """
    dev = rule.get("development") or {}
    val = rule.get("validation") or {}
    hold = rule.get("holdout") or {}
    wf = rule.get("walk_forward") or {}
    out = 0.0
    out += 1.0 * float(dev.get("avg_net_r") or 0.0)
    out += 2.0 * float(val.get("avg_net_r") or 0.0)
    out += 3.0 * float(hold.get("avg_net_r") or 0.0)
    pf = hold.get("profit_factor") or val.get("profit_factor")
    if isinstance(pf, (int, float)):
        out += 0.5 * min(float(pf), 3.0)
    folds = wf.get("folds_scored") or 0
    if folds:
        out += 1.0 * (wf.get("folds_positive", 0) / folds)
    out -= 0.25 * (rule.get("complexity", 1) - 1)
    for window in (dev, val, hold):
        if window.get("trades", 0) < metrics.MIN_TRADES:
            out -= 0.5
        outlier = window.get("outlier_top1_contribution_pct")
        if (
            isinstance(outlier, (int, float))
            and outlier > MAX_OUTLIER_CONTRIBUTION_PCT
        ):
            out -= 0.5
        dd = window.get("max_drawdown_r")
        total = window.get("total_net_r")
        if (
            isinstance(dd, (int, float))
            and isinstance(total, (int, float))
            and total > 0
        ):
            out -= 0.5 * min(1.0, dd / max(total, 1e-9))
    stress = rule.get("cost_sensitivity") or []
    if stress:
        failed = sum(1 for r in stress if not r.get("survives"))
        out -= 0.75 * (failed / len(stress))
    return round(out, 4)


def _slippage_clause(rule: dict) -> list[str]:
    """Whether the rule survived the slippage variants that stand in for spread."""
    stress = rule.get("cost_sensitivity") or []
    wanted = f"slippage_points={REQUIRED_SLIPPAGE_POINTS}"
    rows = [r for r in stress if wanted in str(r.get("variant"))]
    if not rows:
        return ["slippage stress was not run, so the unmeasured spread is untested"]
    if any(not r.get("survives") for r in rows):
        return [
            f"turns negative at {REQUIRED_SLIPPAGE_POINTS} points of slippage per "
            "side, which is inside the unmeasured futures spread"
        ]
    return []


def perturbation_tested(rule: dict) -> bool:
    """Whether the band-shift test applied to this rule at all.

    Only the pullback bands have neighbours to swap, so a rule built from
    gap/trend/opening conditions has an empty perturbation list. An empty list
    reads like a pass, which is why it is reported as a separate fact rather than
    folded into the failed clauses: the test did not run, it did not succeed.
    """
    return bool(rule.get("parameter_perturbation"))


def gate(rule: dict, *, fdr_survivor: bool) -> tuple[str, list[str]]:
    """Phase 24's clauses plus the slippage clause this timeframe study needs."""
    status, reasons = p24rank.gate(rule, fdr_survivor=fdr_survivor)
    extra = _slippage_clause(rule)
    reasons = list(reasons) + extra
    if not reasons:
        return VALIDATED, []
    if status == REJECTED:
        return REJECTED, reasons
    return (RESEARCH_ONLY if status == VALIDATED else status), reasons


def research_label(status: str) -> str:
    """The label carried in the report; the ceiling is a lead, never a promotion."""
    return RESEARCH_LEAD if status == VALIDATED else status


def rank(rules: list[dict], *, tests: int | None = None) -> list[dict]:
    """Score, correct for multiple testing, gate, and sort best first.

    ``tests`` is the number of hypotheses the search actually evaluated, which is
    much larger than the number that reached this table. Correcting against the
    survivors would be correcting against the wrong denominator, so the caller
    passes the counted one and it is used as-is.
    """
    p_values = [
        float((r.get("holdout") or {}).get("p_value_vs_base_rate") or 1.0)
        for r in rules
    ]
    denominator = max(int(tests or len(rules)), len(rules))
    keep = discover.benjamini_hochberg(p_values, FDR_ALPHA, tests=denominator)
    out: list[dict] = []
    for r, survivor in zip(rules, keep, strict=False):
        r = dict(r)
        r["score"] = score(r)
        r["fdr_survivor"] = bool(survivor)
        r["fdr_denominator"] = denominator
        r["fdr_ranked_rows"] = len(rules)
        status, reasons = gate(r, fdr_survivor=bool(survivor))
        r["gate_verdict"] = status
        r["status"] = research_label(status)
        r["failed_clauses"] = reasons
        r["parameter_perturbation_tested"] = perturbation_tested(r)
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
            "timeframe_minutes": rule.get("timeframe_minutes"),
            "side": rule["side"],
            "stop_band_atr": rule["stop_band_atr"],
            "conditions": sorted(rule["conditions"]),
        },
        sort_keys=True,
    )
    return "P27_" + hashlib.sha256(payload.encode()).hexdigest()[:12]


def fingerprint(rule: dict) -> dict:
    """Machine-readable match rule, carried for review only.

    Nothing reads this at runtime. It exists so a reviewer can see exactly what a
    lead claims, on which timeframe, before anyone argues for paper collection.
    """
    return {
        "version": "PHASE27_FINGERPRINT_V1",
        "instrument": rule["instrument"],
        "timeframe_minutes": rule.get("timeframe_minutes"),
        "vehicle": rule["vehicle"],
        "side": rule["side"],
        "stop_atr": rule["stop_band_atr"],
        "t1_r": rule.get("t1_r"),
        "require_all": sorted(rule["conditions"]),
        "status": rule.get("status", RESEARCH_ONLY),
        "paper_only": True,
    }
