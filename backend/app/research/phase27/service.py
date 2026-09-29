"""Phase 27 read-only reads for the API.

There is no write path and no tick hook: nothing in Phase 27 observes a live tick,
places a paper trade, changes a gate or influences a decision. These functions can
only read artefacts the CLI wrote, so a dashboard refresh cannot start a study —
the run is deliberately not reachable over HTTP — and every payload carries the
stopping rule and the unmeasured-spread sentence so a panel cannot present a
research number as a tradable one.
"""
from __future__ import annotations

import time

from app.research.phase27 import (
    RESAMPLE_CLAIM,
    RESEARCH_LEAD,
    SPREAD_CLAIM,
    STOP_RULE,
    bars,
    report,
)

# Coverage aggregates the whole stored series, which is seconds of work — too much
# for a panel that refreshes. Cached briefly; it moves only when history is added.
_COVERAGE_TTL_SEC = 600.0
_cache: tuple[float, dict] | None = None


def coverage(*, max_age_sec: float = _COVERAGE_TTL_SEC) -> dict:
    """Which series can be aggregated, and how many 5m/15m bars that is."""
    global _cache
    now = time.time()
    if _cache and now - _cache[0] < max_age_sec:
        return _cache[1]
    cov = bars.coverage()
    _cache = (now, cov)
    return cov


def _unavailable() -> dict:
    return {
        "available": False,
        "note": (
            "no higher-timeframe study has been run on this machine yet; run "
            "`python -m app.research.phase27.cli run`"
        ),
        "stop_rule": STOP_RULE,
        "spread_claim": SPREAD_CLAIM,
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
        "timeframes": out.get("timeframes"),
        "execution": out.get("execution"),
        "resample_claim": out.get("resample_claim", RESAMPLE_CLAIM),
        "spread_claim": out.get("spread_claim", SPREAD_CLAIM),
        "stop_rule": out.get("stop_rule", STOP_RULE),
        "option_logic": out.get("option_logic"),
        # The artefact writes the per-timeframe blocks under ``per_timeframe``;
        # the in-memory study calls them ``timeframe_results``. Read either, so a
        # panel is not silently empty depending on which one it was handed.
        "timeframe_results": (
            out.get("timeframe_results") or out.get("per_timeframe") or []
        ),
        "answers": out.get("answers"),
        "conclusion": out.get("conclusion"),
        "research_ceiling": RESEARCH_LEAD,
        "research_only": True,
        "paper_only": True,
    })


def comparison() -> dict:
    """1-minute vs 5-minute vs 15-minute pool economics."""
    payload = report.latest_comparison()
    if not payload:
        return _unavailable()
    return report.jsonable_dict({
        "available": True,
        **payload,
        "research_only": True,
        "paper_only": True,
    })


def strategies(limit: int = 25) -> list[dict]:
    """Ranked cohorts with their windows, verdicts and failed clauses."""
    rows = report.latest_ranked()
    trimmed: list[dict] = []
    for r in rows[: max(1, int(limit))]:
        trimmed.append({
            "strategy_id": r.get("strategy_id"),
            "timeframe_minutes": r.get("timeframe_minutes"),
            "instrument": r.get("instrument"),
            "side": r.get("side"),
            "stop_band_atr": r.get("stop_band_atr"),
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
            "cost_sensitivity": r.get("cost_sensitivity"),
            "selectivity": r.get("selectivity"),
        })
    return [report.jsonable_dict(r) for r in trimmed]


def geometry() -> list[dict]:
    """Pool economics per timeframe, stop band and target, with no entry rule."""
    return [report.jsonable_dict(r) for r in report.latest_geometry()]
