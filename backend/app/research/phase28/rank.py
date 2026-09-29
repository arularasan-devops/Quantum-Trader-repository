"""Phase 28 §10 — ranking, the gate and the research ceiling.

The gate is Phase 24's, imported rather than restated: development, validation and
untouched-holdout net R all positive, a holdout sample of at least 100 trades, a
holdout profit factor above 1, walk-forward positive in every scored fold, every
cost/slippage/timing variant survived, no collapse when a discovered band is
shifted, bounded complexity, and survival of the Benjamini-Hochberg correction
over the honest hypothesis count. The default answer is no.

Three clauses are added, one per way a multi-day study can fool its author:

1. **buy-and-hold.** A long swing rule that earns less per day of exposure than
   simply holding the instrument has discovered drift, not an edge. This clause is
   the whole reason the phase exists in the shape it does, and it does not apply to
   short cohorts, whose passive alternative is holding nothing;
2. **the unmeasured spread**, exactly as Phase 27: positive at the configured
   slippage but negative at two points per side is a lead at best, never validated;
3. **gap dependence**, reported as a fact rather than a veto — how much of the
   rule's result comes from the honest gap treatment versus the flattering one.

The ceiling is ``RESEARCH_LEAD``. Nothing self-promotes, nothing is wired to the
live engine, and a rule that clears every clause is an argument for paper
collection, not a decision to trade.
"""
from __future__ import annotations

import hashlib
import json

from app.research.phase24 import rank as p24rank
from app.research.phase27 import rank as p27rank
from app.research.phase28 import (
    REJECTED,
    RESEARCH_LEAD,
    RESEARCH_ONLY,
    VALIDATED,
    VERSION,
    discover,
)

MIN_HOLDOUT_TRADES = p24rank.MIN_HOLDOUT_TRADES
MIN_PROFIT_FACTOR = p24rank.MIN_PROFIT_FACTOR
MAX_COMPLEXITY = p24rank.MAX_COMPLEXITY
MAX_OUTLIER_CONTRIBUTION_PCT = p24rank.MAX_OUTLIER_CONTRIBUTION_PCT
FDR_ALPHA = p24rank.FDR_ALPHA

REQUIRED_SLIPPAGE_POINTS = 2.0
REQUIRED_SLIPPAGE_PCT = 0.05

# Phase 27's public restatement of Phase 24's weighting, reused unchanged so the
# three studies rank on the same scale and this one cannot flatter itself with a
# scoring function of its own.
score = p27rank.score


def _slippage_clause(rule: dict) -> list[str]:
    """Whether the rule survived the slippage that stands in for the spread."""
    stress = rule.get("cost_sensitivity") or []
    wanted = (
        f"slippage_pct={REQUIRED_SLIPPAGE_PCT}"
        if rule.get("vehicle") == "EQUITY_DELIVERY"
        else f"slippage_points={REQUIRED_SLIPPAGE_POINTS}"
    )
    rows = [r for r in stress if wanted in str(r.get("variant"))]
    if not rows:
        return ["slippage stress was not run, so the unmeasured spread is untested"]
    if any(not r.get("survives") for r in rows):
        return [
            f"turns negative at {wanted}, which is inside the spread this feed "
            "never publishes"
        ]
    return []


def _benchmark_clause(rule: dict) -> list[str]:
    """Buy-and-hold: the clause a long-only daily study cannot skip."""
    bench = rule.get("benchmark_holdout") or {}
    if not bench.get("applicable"):
        return [
            "buy-and-hold could not be measured in the holdout window, so the "
            "drift question is unanswered"
        ]
    beats = bench.get("beats_benchmark")
    if beats is None:
        return ["buy-and-hold comparison produced no number"]
    if not beats:
        per_day = bench.get("net_points_per_exposed_day")
        return [
            f"earns {per_day} points per exposed day against buy-and-hold's "
            f"{bench.get('benchmark_points_per_day')} — the passive alternative "
            "is better"
        ]
    return []


def gap_dependence(rule: dict) -> dict:
    """How much of the rule's result the honest gap treatment costs it."""
    stress = rule.get("cost_sensitivity") or []
    rows = [r for r in stress if "gap_fill_at_open=False" in str(r.get("variant"))]
    honest = (rule.get("holdout") or {}).get("avg_net_r")
    if not rows:
        return {"measured": False}
    flattering = rows[0].get("avg_net_r")
    return {
        "measured": True,
        "avg_net_r_with_honest_gap_fills": honest,
        "avg_net_r_if_stops_filled_at_the_stop": flattering,
        "note": (
            "the second number is what a daily backtest that ignores gap risk "
            "would have printed; the difference is the cost of telling the truth"
        ),
    }


def gate(rule: dict, *, fdr_survivor: bool) -> tuple[str, list[str]]:
    """Phase 24's clauses plus slippage and buy-and-hold."""
    status, reasons = p24rank.gate(rule, fdr_survivor=fdr_survivor)
    reasons = list(reasons) + _slippage_clause(rule) + _benchmark_clause(rule)
    if not reasons:
        return VALIDATED, []
    if status == REJECTED:
        return REJECTED, reasons
    return (RESEARCH_ONLY if status == VALIDATED else status), reasons


def research_label(status: str) -> str:
    """The label carried in the report; the ceiling is a lead, never a promotion."""
    return RESEARCH_LEAD if status == VALIDATED else status


def perturbation_tested(rule: dict) -> bool:
    """Whether the band-shift test applied to this rule at all.

    Only the pullback bands have neighbours to swap, so a rule built from trend,
    breakout or gap conditions has an empty list. An empty list reads like a pass,
    which is why it is reported as its own fact: the test did not run, it did not
    succeed.
    """
    return bool(rule.get("parameter_perturbation"))


def rank(rules: list[dict], *, tests: int | None = None) -> list[dict]:
    """Score, correct for multiple testing, gate, and sort best first.

    ``tests`` is the number of hypotheses the search actually evaluated, which is
    much larger than the number that reached this table; correcting against the
    survivors would be correcting against the wrong denominator.
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
        r["gap_dependence"] = gap_dependence(r)
        r["strategy_id"] = strategy_id(r)
        r["fingerprint"] = fingerprint(r)
        out.append(r)
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


def strategy_id(rule: dict) -> str:
    """Stable short id from the rule's definition, not from its results."""
    payload = json.dumps(
        {
            "instrument": rule["instrument"],
            "vehicle": rule["vehicle"],
            "side": rule["side"],
            "stop_band_atr": rule["stop_band_atr"],
            "horizon_trading_days": rule.get("horizon_trading_days"),
            "conditions": sorted(rule["conditions"]),
        },
        sort_keys=True,
    )
    return "P28_" + hashlib.sha256(payload.encode()).hexdigest()[:12]


def fingerprint(rule: dict) -> dict:
    """Machine-readable match rule, carried for review only.

    Nothing reads this at runtime. It exists so a reviewer can see exactly what a
    lead claims before anyone argues for paper collection.
    """
    return {
        "version": VERSION,
        "instrument": rule["instrument"],
        "vehicle": rule["vehicle"],
        "side": rule["side"],
        "stop_atr": rule["stop_band_atr"],
        "t1_r": rule.get("t1_r"),
        "horizon_trading_days": rule.get("horizon_trading_days"),
        "entry": "next session open after the signal close",
        "require_all": sorted(rule["conditions"]),
        "status": rule.get("status", RESEARCH_ONLY),
        "paper_only": True,
    }


__all__ = [
    "rank", "gate", "score", "research_label", "strategy_id", "fingerprint",
    "gap_dependence", "perturbation_tested", "MIN_HOLDOUT_TRADES", "FDR_ALPHA",
]
