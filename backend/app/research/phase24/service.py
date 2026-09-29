"""Phase 24 read-only reads for the API.

There is no write path and no hook: nothing in Phase 24 observes a live tick,
places a paper trade or influences a decision. The API can only read artefacts
that the CLI wrote, so a dashboard cannot start a ten-minute study by accident
either.
"""
from __future__ import annotations

import time

from app.research.phase24 import VALIDATED, data, report

# Counting two-sided books walks every stored snapshot's payload, which on a
# store with a long capture is seconds of work — too much for a panel that
# refreshes. Cached briefly; the number moves slowly by nature.
_BOOKS_TTL_SEC = 600.0
_books_cache: tuple[float, dict] | None = None


def option_books(*, max_age_sec: float = _BOOKS_TTL_SEC) -> dict:
    global _books_cache
    now = time.time()
    if _books_cache and now - _books_cache[0] < max_age_sec:
        return _books_cache[1]
    books = data.option_book_coverage()
    _books_cache = (now, books)
    return books


def coverage() -> dict:
    """What history exists, and what the stored option books can support."""
    return {
        "series": data.coverage(),
        "option_books": option_books(),
    }


def summary() -> dict:
    """The last written report, trimmed to what a panel needs."""
    out = report.latest()
    if not out:
        return {
            "available": False,
            "note": "no study has been run on this machine yet; run "
                    "`python -m app.research.phase24.cli run`",
            "paper_only": True,
        }
    return {
        "available": True,
        "version": out.get("version"),
        "generated_at": out.get("generated_at"),
        "windows": out.get("windows"),
        "pools": out.get("pools"),
        "hypotheses_evaluated": out.get("hypotheses_evaluated"),
        "answers": out.get("answers"),
        "conclusion": out.get("conclusion"),
        "coverage": out.get("coverage"),
        "option_books": out.get("option_books"),
        "data_status": out.get("data_status"),
        "vehicles": out.get("vehicles"),
        "geometry_sweep": report.latest_geometry(),
        "paper_only": True,
    }


def strategies(limit: int = 25) -> list[dict]:
    """Ranked strategies, best first, trimmed for the table."""
    rows = report.latest_ranked()
    capped = rows[: max(1, min(int(limit), 200))]
    out: list[dict] = []
    for r in capped:
        dev = r.get("development") or {}
        val = r.get("validation") or {}
        hold = r.get("holdout") or {}
        wf = r.get("walk_forward") or {}
        sel = r.get("selectivity") or {}
        out.append({
            "strategy_id": r.get("strategy_id"),
            "instrument": r.get("instrument"),
            "vehicle": r.get("vehicle"),
            "side": r.get("side"),
            "stop_band_atr": r.get("stop_band_atr"),
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
            "holdout_longest_losing_streak": hold.get("longest_losing_streak"),
            "outlier_top1_pct": hold.get("outlier_top1_contribution_pct"),
            "folds_positive": wf.get("folds_positive"),
            "folds_scored": wf.get("folds_scored"),
            "walk_forward_stable": wf.get("stable"),
            "trades_per_week": sel.get("trades_per_week"),
            "no_trade_pct": sel.get("no_trade_pct"),
            "cost_sensitivity": r.get("cost_sensitivity"),
            "fingerprint_published": r.get("status") == VALIDATED,
        })
    return out
