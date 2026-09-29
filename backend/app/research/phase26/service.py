"""Phase 26 read-only reads for the API.

There is no write path and no tick hook: nothing in Phase 26 observes a live
tick, places a paper trade, changes a gate or influences a decision. The API can
only read artefacts the CLI wrote, so a dashboard refresh cannot start a study
either, and the advisory table is served with the sentence that says it is not
wired to anything.
"""
from __future__ import annotations

import time

from app.research.phase26 import (
    ADVISORY_CLAIM,
    RESEARCH_LEAD,
    WINDOW_CLAIM,
    coverage as coverage_mod,
    report,
)

# Coverage walks stored payloads, which is seconds of work on a long capture —
# too much for a panel that refreshes. Cached briefly; it moves slowly.
_COVERAGE_TTL_SEC = 600.0
_cache: tuple[float, dict] | None = None


def coverage(*, max_age_sec: float = _COVERAGE_TTL_SEC) -> dict:
    global _cache
    now = time.time()
    if _cache and now - _cache[0] < max_age_sec:
        return _cache[1]
    cov = coverage_mod.coverage()
    _cache = (now, cov)
    return cov


def _unavailable() -> dict:
    return {
        "available": False,
        "note": "no exit/tradability study has been run on this machine yet; "
                "run `python -m app.research.phase26.cli run`",
        "window_claim": WINDOW_CLAIM,
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
        "quick_mode": out.get("quick_mode"),
        "execution": out.get("execution"),
        "window_claim": out.get("window_claim", WINDOW_CLAIM),
        "advisory_claim": out.get("advisory_claim", ADVISORY_CLAIM),
        "event_hypotheses_evaluated": out.get("event_hypotheses_evaluated"),
        "variant_totals": out.get("variant_totals"),
        "variants_tested": out.get("variants_tested"),
        "conclusion": out.get("conclusion"),
        "answers": out.get("answers"),
        "instruments": [
            {
                "instrument": r.get("instrument"),
                "status": r.get("status"),
                "candidates": r.get("candidates"),
                "sessions": r.get("sessions"),
                "best_variant_on_development": r.get("best_variant_on_development"),
                "uplift": r.get("variant_uplift_vs_baseline_r"),
                "variants": [
                    {
                        "variant": v.get("variant"),
                        "trades": v.get("trades"),
                        "avg_net_r": v.get("avg_net_r"),
                        "profit_factor": v.get("profit_factor"),
                        "profit_exit_before_sl_pct": v.get(
                            "profit_exit_before_sl_pct"
                        ),
                        "outcome_mix": v.get("outcome_mix"),
                        "median_hold_sec": v.get("median_hold_sec"),
                        "holdout": v.get("holdout"),
                    }
                    for v in (r.get("variants") or [])
                ],
                "reasons": (r.get("coverage") or {}).get("reasons"),
            }
            for r in out.get("instruments") or []
        ],
        "paper_only": True,
    })


def advisory() -> dict:
    """The measured tradability table, with its advisory-only claim attached."""
    adv = report.latest_advisory()
    if not adv:
        return _unavailable()
    return report.jsonable_dict({
        "available": True,
        "advisory_claim": ADVISORY_CLAIM,
        "window_claim": WINDOW_CLAIM,
        "wired_to_live_refusal": False,
        **adv,
    })


def cohorts(limit: int = 25) -> list[dict]:
    """Ranked event cohorts, best first, trimmed for the table."""
    rows = report.latest_events()
    out: list[dict] = []
    for r in rows[: max(1, min(int(limit), 200))]:
        dev = r.get("development") or {}
        val = r.get("validation") or {}
        hold = r.get("holdout") or {}
        sel = r.get("selectivity") or {}
        out.append({
            "strategy_id": r.get("strategy_id"),
            "instrument": r.get("instrument"),
            "option_type": r.get("option_type"),
            "exit_variant": r.get("variant"),
            "conditions": r.get("conditions"),
            "score": r.get("score"),
            "status": r.get("status"),
            "failed_clauses": r.get("failed_clauses"),
            "fdr_survivor": r.get("fdr_survivor"),
            "fdr_denominator": r.get("fdr_denominator"),
            "dev_trades": dev.get("trades"),
            "dev_avg_net_r": dev.get("avg_net_r"),
            "val_trades": val.get("trades"),
            "val_avg_net_r": val.get("avg_net_r"),
            "holdout_trades": hold.get("trades"),
            "holdout_avg_net_r": hold.get("avg_net_r"),
            "holdout_profit_factor": hold.get("profit_factor"),
            "holdout_max_drawdown_r": hold.get("max_drawdown_r"),
            "outlier_top1_pct": hold.get("outlier_top1_contribution_pct"),
            "folds_positive": r.get("folds_positive"),
            "folds_measurable": r.get("folds_measurable"),
            "trades_per_session": sel.get("trades_per_session"),
            "no_trade_pct": sel.get("no_trade_pct"),
            "robustness": r.get("robustness"),
            "is_research_lead": r.get("status") == RESEARCH_LEAD,
            "paper_only": True,
        })
    return [report.jsonable_dict(r) for r in out]


def variants() -> dict:
    """Pooled exit-variant table, for the panel's headline comparison."""
    rows = report.latest_variants()
    if not rows:
        return _unavailable()
    return {
        "available": True,
        "rows": [report.jsonable_dict(r) for r in rows],
        "window_claim": WINDOW_CLAIM,
        "paper_only": True,
    }
