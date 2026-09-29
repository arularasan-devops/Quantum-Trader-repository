"""Phase 28 read-only reads for the API.

There is no write path and no tick hook: nothing in Phase 28 observes a live tick,
places a paper trade, changes a gate or influences a decision. These functions can
only read artefacts the CLI wrote, so a dashboard refresh cannot start a study —
the run is deliberately not reachable over HTTP.

Two payload rules exist because of what a panel can imply by omission:

* every payload carries the pre-committed stopping rule and the buy-and-hold
  claim, so a positive-looking long row cannot be read as an edge without the
  passive comparison next to it;
* the cash-equity status is carried separately from every strategy number. A
  machine with no stock history must render "UNANSWERED", never an empty
  strategy table that looks like a failed stock study.
"""
from __future__ import annotations

import time

from app.research.phase28 import (
    BUY_HOLD_CLAIM,
    DELIVERY_CLAIM,
    GAP_CLAIM,
    INSUFFICIENT_HISTORY,
    RESEARCH_LEAD,
    ROLLOVER_CLAIM,
    STOP_RULE,
    dailybars,
    report,
)

# Coverage loads and aggregates every candidate series, which is seconds of work —
# too much for a panel that refreshes. Cached briefly; it only moves when history
# is collected.
_COVERAGE_TTL_SEC = 600.0
_cache: tuple[float, dict] | None = None


def coverage(*, max_age_sec: float = _COVERAGE_TTL_SEC) -> dict:
    """Which instruments have five years of daily bars, and which do not."""
    global _cache
    now = time.time()
    if _cache and now - _cache[0] < max_age_sec:
        return _cache[1]
    cov = dailybars.coverage()
    payload = report.jsonable_dict({
        **cov,
        "daily_bar_rules": dailybars.rules(),
        "equity_history": report.equity_history_note(cov),
        "research_only": True,
        "paper_only": True,
    })
    _cache = (now, payload)
    return payload


def _unavailable() -> dict:
    return {
        "available": False,
        "note": (
            "no multi-day study has been run on this machine yet; run "
            "`python -m app.research.phase28.cli run`"
        ),
        "stop_rule": STOP_RULE,
        "buy_and_hold_claim": BUY_HOLD_CLAIM,
        "research_only": True,
        "paper_only": True,
    }


def summary() -> dict:
    """The last written study, trimmed to what a panel needs."""
    out = report.latest()
    if not out:
        return _unavailable()
    return report.jsonable_dict({
        "available": True,
        "version": out.get("version"),
        "generated_at": out.get("generated_at"),
        "runtime_seconds": out.get("runtime_seconds"),
        "quick_mode": out.get("quick_mode"),
        "instruments": out.get("instruments"),
        "data_status": out.get("data_status"),
        "answers": out.get("answers"),
        "conclusion": out.get("conclusion"),
        "equity_history": (
            out.get("answers", {}).get("cash_equity_verdict")
            if isinstance(out.get("answers"), dict) else None
        ),
        "option_logic": out.get("option_logic"),
        "gap_claim": GAP_CLAIM,
        "rollover_claim": ROLLOVER_CLAIM,
        "delivery_claim": DELIVERY_CLAIM,
        "buy_and_hold_claim": BUY_HOLD_CLAIM,
        "stop_rule": STOP_RULE,
        "research_ceiling": RESEARCH_LEAD,
        "insufficient_history_label": INSUFFICIENT_HISTORY,
        "research_only": True,
        "paper_only": True,
    })


def funnel() -> dict:
    """How far the search got per vehicle, clause by clause."""
    payload = report.latest_funnel()
    if not payload:
        return _unavailable()
    return report.jsonable_dict({
        "available": True, **payload,
        "research_only": True, "paper_only": True,
    })


def economics() -> dict:
    """Pool economics and the 1m/5m/15m/multi-day cost-per-risk comparison."""
    payload = report.latest_economics()
    if not payload:
        return _unavailable()
    return report.jsonable_dict({
        "available": True, **payload,
        "research_only": True, "paper_only": True,
    })


def baselines() -> dict:
    """Buy-and-hold and the textbook rules, measured under the same costs."""
    payload = report.latest_baselines()
    if not payload:
        return _unavailable()
    return report.jsonable_dict({
        "available": True, **payload,
        "buy_and_hold_claim": BUY_HOLD_CLAIM,
        "research_only": True, "paper_only": True,
    })


def strategies(limit: int = 25) -> list[dict]:
    """Ranked cohorts with their windows, verdicts and failed clauses."""
    payload = report.latest_ranked()
    rows: list[dict] = []
    for block in payload.get("vehicles") or []:
        for r in block.get("ranked") or []:
            rows.append({
                "strategy_id": r.get("strategy_id"),
                "vehicle": r.get("vehicle"),
                "instrument": r.get("instrument"),
                "side": r.get("side"),
                "stop_band_atr": r.get("stop_band_atr"),
                "horizon_trading_days": r.get("horizon_trading_days"),
                "conditions": r.get("conditions"),
                "complexity": r.get("complexity"),
                "score": r.get("score"),
                "status": r.get("status"),
                "gate_verdict": r.get("gate_verdict"),
                "fdr_survivor": r.get("fdr_survivor"),
                "fdr_denominator": r.get("fdr_denominator"),
                "failed_clauses": r.get("failed_clauses"),
                "parameter_perturbation_tested": r.get(
                    "parameter_perturbation_tested"
                ),
                "development": r.get("development"),
                "validation": r.get("validation"),
                "holdout": r.get("holdout"),
                "walk_forward": r.get("walk_forward"),
                "benchmark_holdout": r.get("benchmark_holdout"),
                "gap_dependence": r.get("gap_dependence"),
                "cost_sensitivity": r.get("cost_sensitivity"),
                "exit_breakdown": r.get("exit_breakdown"),
                "selectivity": r.get("selectivity"),
            })
    rows.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
    return [report.jsonable_dict(r) for r in rows[: max(1, int(limit))]]


__all__ = [
    "coverage", "summary", "funnel", "economics", "baselines", "strategies",
]
