"""Phase 25 read-only reads for the API.

There is no write path and no tick hook: nothing in Phase 25 observes a live
tick, places a paper trade or influences a decision. The API can only read
artefacts the CLI wrote, so a dashboard refresh cannot start a study either.
"""
from __future__ import annotations

import time

from app.research.phase25 import RESEARCH_LEAD, WINDOW_CLAIM, report

# Coverage walks stored payloads, which is seconds of work on a long capture —
# too much for a panel that refreshes. Cached briefly; it moves slowly.
_COVERAGE_TTL_SEC = 600.0
_cache: tuple[float, dict] | None = None


def coverage(*, max_age_sec: float = _COVERAGE_TTL_SEC) -> dict:
    global _cache
    now = time.time()
    if _cache and now - _cache[0] < max_age_sec:
        return _cache[1]
    cov = report.coverage()
    _cache = (now, cov)
    return cov


def summary() -> dict:
    """The last written study, trimmed to what a panel needs.

    Non-finite floats are carried as a sentence rather than a number: an
    infinite profit factor is not valid JSON and must not reach a panel as one.
    """
    out = report.latest()
    if not out:
        return {
            "available": False,
            "note": "no captured-window option study has been run on this "
                    "machine yet; run `python -m app.research.phase25.cli run`",
            "window_claim": WINDOW_CLAIM,
            "paper_only": True,
        }
    return report.jsonable_dict({
        "available": True,
        "version": out.get("version"),
        "generated_at": out.get("generated_at"),
        "quick_mode": out.get("quick_mode"),
        "geometry": out.get("geometry"),
        "window_claim": out.get("window_claim", WINDOW_CLAIM),
        "hypotheses_evaluated": out.get("hypotheses_evaluated"),
        "conclusion": out.get("conclusion"),
        "answers": out.get("answers"),
        "instruments": [
            {
                "instrument": r.get("instrument"),
                "status": r.get("status"),
                "pool": r.get("pool"),
                "sides": r.get("sides"),
                "time_of_day": r.get("time_of_day"),
                "hurdle_bands": r.get("hurdle_bands"),
                "geometry_sweep": r.get("geometry_sweep"),
                "reasons": (r.get("coverage") or {}).get("reasons"),
            }
            for r in out.get("instruments") or []
        ],
        "paper_only": True,
    })


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
            "option_type": r.get("option_type"),
            "underlying_view": r.get("side"),
            "stop_band_pct_of_premium": r.get("stop_band_pct_of_premium"),
            "conditions": r.get("conditions"),
            "complexity": r.get("complexity"),
            "score": r.get("score"),
            "status": r.get("status"),
            "failed_clauses": r.get("failed_clauses"),
            "dev_trades": dev.get("trades"),
            "dev_t1_pct": dev.get("t1_before_sl_pct"),
            "dev_avg_net_r": dev.get("avg_net_r"),
            "val_trades": val.get("trades"),
            "val_avg_net_r": val.get("avg_net_r"),
            "holdout_trades": hold.get("trades"),
            "holdout_t1_pct": hold.get("t1_before_sl_pct"),
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
