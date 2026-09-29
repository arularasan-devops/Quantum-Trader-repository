"""Phase 29 read-only reads for the API.

There is no write path and no tick hook: nothing in Phase 29 observes a live
tick, places a paper trade or influences a decision. The API can only read
artefacts the CLI wrote, so a dashboard refresh cannot start a study either — a
study takes minutes and a panel that could start one would be a denial of
service against the machine that runs the trading engine.
"""
from __future__ import annotations

import time

from app.research.phase29 import (
    ASSIGNMENT_CLAIM,
    MARGIN_CLAIM,
    RESEARCH_LEAD,
    WINDOW_CLAIM,
    report,
)

# Coverage walks stored payloads, which is seconds of work on a long capture —
# too much for a panel that refreshes. Cached briefly; it moves slowly.
_COVERAGE_TTL_SEC = 600.0
_cache: tuple[float, dict] | None = None

NOT_RUN = (
    "no defined-risk credit-spread study has been run on this machine yet; run "
    "`python -m app.research.phase29.cli run`"
)


def coverage(*, max_age_sec: float = _COVERAGE_TTL_SEC) -> dict:
    """Which instruments have a ladder wide enough to price a spread."""
    global _cache
    now = time.time()
    if _cache and now - _cache[0] < max_age_sec:
        return _cache[1]
    cov = report.jsonable_dict(report.coverage())
    _cache = (now, cov)
    return cov


def summary() -> dict:
    """The last written study, trimmed to what a panel needs."""
    out = report.latest()
    if not out:
        return {
            "available": False,
            "note": NOT_RUN,
            "window_claim": WINDOW_CLAIM,
            "margin_claim": MARGIN_CLAIM,
            "assignment_claim": ASSIGNMENT_CLAIM,
            "paper_only": True,
        }
    return report.jsonable_dict({
        "available": True,
        "version": out.get("version"),
        "generated_at": out.get("generated_at"),
        "runtime_seconds": out.get("runtime_seconds"),
        "quick_mode": out.get("quick_mode"),
        "geometry": out.get("geometry"),
        "window_claim": out.get("window_claim", WINDOW_CLAIM),
        "margin_claim": MARGIN_CLAIM,
        "assignment_claim": ASSIGNMENT_CLAIM,
        "hypotheses_evaluated": out.get("hypotheses_evaluated"),
        "conclusion": out.get("conclusion"),
        "buyer_versus_seller": out.get("buyer_versus_seller"),
        "hold_comparison": out.get("hold_comparison"),
        "answers": out.get("answers"),
        "instruments": [
            {
                "instrument": r.get("instrument"),
                "status": r.get("status"),
                "strike_step_measured": r.get("strike_step_measured"),
                "geometries_priced": r.get("geometries_priced"),
                "coverage": r.get("coverage"),
                "credit_bands": r.get("credit_bands"),
                "time_of_day": r.get("time_of_day"),
                "reasons": (r.get("coverage") or {}).get("reasons"),
            }
            for r in out.get("instruments") or []
        ],
        "paper_only": True,
    })


def sweep(limit: int = 60) -> list[dict]:
    """The unconditional structure/geometry table, best net R first.

    Read before the cohort table on purpose: a cohort inside a negative pool is a
    subset of a losing population, and this is the table that says so.
    """
    rows = report.latest_sweep()
    scored = [r for r in rows if isinstance(r.get("avg_net_r"), (int, float))]
    scored.sort(key=lambda r: r["avg_net_r"], reverse=True)
    unscored = [r for r in rows if not isinstance(r.get("avg_net_r"), (int, float))]
    return report.jsonable((scored + unscored)[: max(1, min(int(limit), 500))])


def cohorts(limit: int = 25) -> list[dict]:
    """Ranked cohorts, best first, trimmed for the table."""
    rows = report.latest_ranked()
    out: list[dict] = []
    for r in rows[: max(1, min(int(limit), 200))]:
        dev = r.get("development") or {}
        val = r.get("validation") or {}
        hold = r.get("holdout") or {}
        sel = r.get("selectivity") or {}
        out.append({
            "strategy_id": r.get("strategy_id"),
            "instrument": r.get("instrument"),
            "structure": r.get("structure"),
            "underlying_view": r.get("underlying_view"),
            "hold": r.get("hold"),
            "short_steps_otm": r.get("short_steps_otm"),
            "width_steps": r.get("width_steps"),
            "width_points": r.get("width_points"),
            "stop_credit_multiple": r.get("stop_credit_multiple"),
            "take_profit_fraction_of_credit": r.get("take_profit_fraction"),
            "conditions": r.get("conditions"),
            "complexity": r.get("complexity"),
            "score": r.get("score"),
            "status": r.get("status"),
            "fdr_survivor": r.get("fdr_survivor"),
            "failed_clauses": r.get("failed_clauses"),
            "median_friction_pct_of_credit": r.get("median_friction_pct_of_credit"),
            "development": {
                "trades": dev.get("trades"),
                "target_before_stop_pct": dev.get("t1_before_sl_pct"),
                "avg_net_r": dev.get("avg_net_r"),
            },
            "validation": {
                "trades": val.get("trades"),
                "target_before_stop_pct": val.get("t1_before_sl_pct"),
                "avg_net_r": val.get("avg_net_r"),
            },
            "holdout": {
                "trades": hold.get("trades"),
                "target_before_stop_pct": hold.get("t1_before_sl_pct"),
                "avg_net_r": hold.get("avg_net_r"),
                "profit_factor": hold.get("profit_factor"),
                "avg_return_on_defined_risk_pct": hold.get(
                    "avg_return_on_defined_risk_pct"
                ),
                "outlier_top1_contribution_pct": hold.get(
                    "outlier_top1_contribution_pct"
                ),
            },
            "folds_positive": r.get("folds_positive"),
            "folds_measurable": r.get("folds_measurable"),
            "robustness": r.get("robustness"),
            "trades_per_session": sel.get("trades_per_session"),
            "no_trade_pct": sel.get("no_trade_pct"),
            "paper_only": True,
        })
    return report.jsonable(out)


def holds() -> dict:
    """The two declared intraday holds side by side, as a description.

    Trade-weighted over the priced geometries. It is not a selection: a hold that
    reads better here has not been validated, and neither hold carries a position
    overnight.
    """
    out = report.latest()
    if not out:
        return {"available": False, "note": NOT_RUN, "rows": [], "paper_only": True}
    return report.jsonable_dict({
        "available": True,
        "rows": out.get("hold_comparison") or [],
        "note": (
            "trade-weighted across the priced geometries, not a validated "
            "selection; neither hold carries a position overnight"
        ),
        "window_claim": out.get("window_claim", WINDOW_CLAIM),
        "paper_only": True,
    })


def leads() -> list[dict]:
    """Only the cohorts that cleared every clause, with their limitations attached.

    A lead is a candidate for *paper* collection. The three claims travel with it
    so a caller cannot read a lead without also reading what it does not mean.
    """
    rows = [
        r for r in report.latest_ranked() if r.get("status") == RESEARCH_LEAD
    ]
    return report.jsonable([
        {
            "strategy_id": r.get("strategy_id"),
            "instrument": r.get("instrument"),
            "structure": r.get("structure"),
            "conditions": r.get("conditions"),
            "fingerprint": r.get("fingerprint"),
            "window_claim": WINDOW_CLAIM,
            "margin_claim": MARGIN_CLAIM,
            "assignment_claim": ASSIGNMENT_CLAIM,
            "paper_only": True,
            "promoted": False,
        }
        for r in rows
    ])


__all__ = [
    "coverage", "summary", "sweep", "cohorts", "holds", "leads", "NOT_RUN",
]
