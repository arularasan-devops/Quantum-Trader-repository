"""Phase 28 §6 — the multi-day candidate pool.

Every eligible daily bar produces a candidate on every side the vehicle can
actually take: LONG and SHORT for a futures instrument, LONG only for cash equity,
because delivery cannot be held short overnight. Building the pool on both sides
where they exist keeps the pool's own base rate near a coin flip, which is the
number a discovered cohort has to beat — a pool built only on the side that
happened to work is a study that has already chosen its answer.

Two things are stated rather than assumed:

* the **first 250 sessions are warm-up** and produce no candidate. The 52-week
  high/low and the 200-day regime are meaningless before then, and a partial-year
  "52-week high" is exactly the kind of quietly-wrong feature that makes a daily
  backtest look clever;
* the pool is **one candidate per bar per side**, no stride. Daily bars are already
  close to independent, so there is nothing to thin, and every session gets a
  decision — including the ones where the answer is no trade.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24.outcomes import LONG
from app.research.phase28 import EQUITY_DELIVERY, dailybars, features, outcomes

WARMUP_BARS = features.YEAR_WIN

FEATURE_KEYS = tuple(sorted({
    "close", "high", "low", "open", "atr", "atr_pct", "vol_ratio", "ema_fast",
    "ema_slow", "ema_regime", "ema_fast_above_slow", "above_regime_ema", "rsi",
    "trend_short", "trend_long", "prior_high_20", "prior_low_20", "prior_high_50",
    "prior_low_50", "year_high", "year_low", "pct_from_year_high", "swing_high",
    "swing_low", "swing_range", "pullback_from_high_pct", "pullback_from_low_pct",
    "gap_pct", "range_over_atr", "body_over_range", "mom_short_atr",
    "mom_long_atr", "volume_ratio", "closed_up",
}))


class Pool:
    """Candidates for one instrument at one stop band and holding horizon."""

    __slots__ = (
        "instrument", "vehicle", "feat", "idx", "side", "out", "ts", "series",
        "bar_stats", "stop_atr", "horizon_days", "t1_r", "sessions",
    )

    def __len__(self) -> int:
        return int(self.idx.size)


def build(
    instrument: str,
    *,
    stop_atr: float = outcomes.STOP_ATR,
    horizon_days: int = outcomes.HORIZON_DAYS,
    t1_r: float = outcomes.T1_R,
    **resolve_kw,
) -> Pool | None:
    """Build and resolve one instrument's swing pool, or ``None`` if ineligible.

    ``resolve_kw`` is forwarded to :func:`outcomes.resolve`, so the robustness grid
    re-resolves the *same* candidates under worse execution instead of generating a
    different pool that would not be comparable to the baseline.
    """
    got = dailybars.load(instrument)
    if got is None:
        return None
    series, stats = got
    ok, _why = dailybars.eligible(stats)
    if not ok:
        return None
    if len(series) <= WARMUP_BARS + outcomes.ENTRY_DELAY_BARS + 1:
        return None

    vehicle = outcomes.vehicle_for(instrument)
    feat = features.build(series)
    n = len(series)
    base = np.arange(n)
    eligible = np.zeros(n, dtype=bool)
    eligible[WARMUP_BARS:] = True
    eligible &= np.isfinite(feat["atr"]) & (feat["atr"] > 0)
    eligible &= np.isfinite(feat["close"]) & (feat["close"] > 0)
    # A candidate needs a bar to be filled on and at least one bar to resolve in.
    eligible &= (n - 1 - base) >= outcomes.ENTRY_DELAY_BARS + 1

    idx = base[eligible]
    if idx.size == 0:
        return None

    sides = outcomes.sides_for(vehicle)
    idx_all = np.concatenate([idx] * len(sides))
    side = np.concatenate([
        np.full(idx.size, s, dtype=np.int8) for s in sides
    ])

    out = outcomes.resolve(
        instrument,
        vehicle,
        open_=series.open,
        high=series.high,
        low=series.low,
        close=series.close,
        ts=series.ts,
        idx=idx_all,
        side=side,
        atr=feat["atr"],
        horizon_days=horizon_days,
        stop_atr=stop_atr,
        t1_r=t1_r,
        **resolve_kw,
    )

    p = Pool()
    p.instrument = instrument.upper()
    p.vehicle = vehicle
    p.series = series
    p.feat = {k: np.asarray(feat[k])[idx_all] for k in FEATURE_KEYS}
    p.idx = idx_all
    p.side = side
    p.out = out
    p.ts = series.ts[idx_all]
    p.bar_stats = stats
    p.stop_atr = float(stop_atr)
    p.horizon_days = int(horizon_days)
    p.t1_r = float(t1_r)
    p.sessions = int(idx.size)
    return p


def summary(p: Pool) -> dict:
    """Pool-level counts, including the base rate a cohort must beat."""
    o = p.out
    res = o.resolved
    total = int(res.sum())
    if total == 0:
        return {
            "instrument": p.instrument,
            "vehicle": p.vehicle,
            "candidates": int(len(p)),
            "resolved": 0,
        }
    gapped = np.isin(
        o.exit_reason[res],
        [outcomes.EXIT_STOP_GAP, outcomes.EXIT_TARGET_GAP],
    )
    return {
        "instrument": p.instrument,
        "vehicle": p.vehicle,
        "source": p.bar_stats.get("source"),
        "daily_bars": int(p.bar_stats.get("daily_bars") or 0),
        "span_years": p.bar_stats.get("span_years"),
        "candidates": int(len(p)),
        "resolved": total,
        "unresolved": int((~res).sum()),
        "sides": ["LONG"] if p.vehicle == EQUITY_DELIVERY else ["LONG", "SHORT"],
        "long": int((p.side == LONG).sum()),
        "short": int((p.side != LONG).sum()),
        "stop_band_atr": p.stop_atr,
        "horizon_trading_days": p.horizon_days,
        "t1_r": p.t1_r,
        "base_t1_before_sl_pct": round(float(o.t1_before_sl[res].mean() * 100.0), 2),
        "base_avg_net_r": round(float(o.net_r[res].mean()), 4),
        "base_timeout_pct": round(
            float((o.outcome[res] == outcomes.OUT_TIMEOUT).mean() * 100.0), 2
        ),
        "gap_resolved_pct": round(float(gapped.mean() * 100.0), 2),
        "median_risk_points": round(float(np.median(o.risk[res])), 4),
        "median_cost_points": round(float(np.median(o.cost_points[res])), 4),
        "cost_as_fraction_of_risk": round(
            float(np.median(o.cost_points[res]) / np.median(o.risk[res])), 4
        ),
        "median_trading_days_held": float(np.median(o.bars_held[res])),
        "median_calendar_days_held": float(np.median(o.calendar_days[res])),
        "rolls_charged": int(o.rolls[res].sum()),
        "first_ts": int(p.ts.min()),
        "last_ts": int(p.ts.max()),
    }


__all__ = ["Pool", "build", "summary", "WARMUP_BARS", "FEATURE_KEYS"]
