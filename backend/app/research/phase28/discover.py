"""Phase 28 §8 — bounded, staged discovery on development bars only.

The staging is Phase 24's, inherited on purpose: singles, then pairs built only
from surviving singles, then triples built only from surviving pairs, with every
hypothesis counted so the multiple-testing correction gets the honest denominator
rather than a flattering one.

Two things had to change for a daily study, and both are deviations that would be
dishonest to leave implicit:

1. **The split is fractional, not a fixed 3y/1y/1y.** A daily series spends its
   first 250 sessions on feature warm-up, so five calendar years of data yields
   about four years of candidates; Phase 24's fixed windows would have consumed all
   four and left an empty holdout. The windows are therefore 60% / 20% / 20% of the
   candidate span, chronological, with the holdout never read until the gate runs.
2. **The development sample gate is smaller.** One daily bar per side per session
   is roughly 250 candidates a year against a 1-minute study's hundreds of
   thousands, so a 100-trade *development* gate would reject every three-condition
   rule before it was measured. It is 40 here — and the *holdout* bar for calling
   anything validated stays at 100 trades, unchanged, because that is the number
   that decides whether a result is real.
"""
from __future__ import annotations

import time
from itertools import combinations

import numpy as np

from app.research.phase24 import metrics
from app.research.phase24.outcomes import LONG
from app.research.phase27.discover import benjamini_hochberg
from app.research.phase28 import conditions, outcomes, pool

DEV = "development"
VAL = "validation"
HOLDOUT = "holdout"

DEV_FRACTION = 0.60
VAL_FRACTION = 0.20

# Frozen searched space, declared before the authoritative run.
STOP_BANDS = (1.0, 2.0, 3.0)
HORIZONS = (5, 10, 20)

MIN_DEV_TRADES = 40
MIN_DEV_T1_EDGE_PCT = 2.0
MIN_DEV_AVG_NET_R = 0.0
NEAR_MISS_KEEP = 5
TOP_SINGLES = 8
TOP_PAIRS = 12
MAX_CONDITIONS = 3


def windows(ts: np.ndarray) -> dict[str, tuple[int, int]]:
    """Chronological development / validation / holdout boundaries."""
    t0, t1 = int(ts.min()), int(ts.max()) + 1
    span = max(1, t1 - t0)
    dev_end = t0 + int(span * DEV_FRACTION)
    val_end = t0 + int(span * (DEV_FRACTION + VAL_FRACTION))
    return {DEV: (t0, dev_end), VAL: (dev_end, val_end), HOLDOUT: (val_end, t1)}


def window_mask(ts: np.ndarray, span: tuple[int, int]) -> np.ndarray:
    lo, hi = span
    return (ts >= lo) & (ts < hi)


def window_note(ts: np.ndarray) -> dict:
    return {
        name: {
            "from_ts": lo,
            "to_ts": hi,
            "from": time.strftime("%Y-%m-%d", time.gmtime(lo)),
            "to": time.strftime("%Y-%m-%d", time.gmtime(hi)),
            "years": round((hi - lo) / (365.25 * 86_400.0), 2),
        }
        for name, (lo, hi) in windows(ts).items()
    }


def base_rate(o: outcomes.SwingOutcomes, mask: np.ndarray) -> float:
    """The pool's own T1 rate inside a window — the coin a cohort must beat."""
    if not mask.any():
        return 0.0
    return float(o.t1_before_sl[mask].mean())


class Search:
    """One instrument, one stop band, one horizon, masks evaluated once."""

    def __init__(self, p: pool.Pool) -> None:
        self.pool = p
        self.masks = conditions.masks(p.feat, p.side)
        self.resolved = p.out.resolved
        self.win = windows(p.ts)
        self.dev = window_mask(p.ts, self.win[DEV]) & self.resolved
        self.val = window_mask(p.ts, self.win[VAL]) & self.resolved
        self.hold = window_mask(p.ts, self.win[HOLDOUT]) & self.resolved
        self.tests = 0
        self.near_misses: list[dict] = []

    def cohort(self, names: tuple[str, ...], side: int) -> np.ndarray:
        m = self.pool.side == side
        for n in names:
            m = m & self.masks[n]
        return m

    def evaluate(
        self, names: tuple[str, ...], side: int, window: np.ndarray
    ) -> dict:
        m = self.cohort(names, side) & window
        return metrics.summarise(
            self.pool.out, m, base_rate(self.pool.out, window)
        )

    def _dev_ok(self, stats: dict, br: float) -> bool:
        if stats.get("trades", 0) < MIN_DEV_TRADES:
            return False
        if stats["avg_net_r"] <= MIN_DEV_AVG_NET_R:
            return False
        return stats["t1_before_sl_pct"] - 100.0 * br >= MIN_DEV_T1_EDGE_PCT

    def _consider(self, names: tuple[str, ...], side: int, stats: dict) -> None:
        if stats.get("trades", 0) >= MIN_DEV_TRADES:
            self.near_misses.append(self._row(names, side, stats))

    def run(self) -> list[dict]:
        """Staged search on development bars; returns survivors, unranked."""
        survivors: list[dict] = []
        br = base_rate(self.pool.out, self.dev)
        for side in outcomes.sides_for(self.pool.vehicle):
            singles: list[tuple[float, str]] = []
            for name in self.masks:
                stats = self.evaluate((name,), side, self.dev)
                self.tests += 1
                self._consider((name,), side, stats)
                if self._dev_ok(stats, br):
                    survivors.append(self._row((name,), side, stats))
                if stats.get("trades", 0) >= MIN_DEV_TRADES:
                    singles.append((stats["avg_net_r"], name))
            singles.sort(reverse=True)
            seeds = [n for _, n in singles[:TOP_SINGLES]]

            pairs: list[tuple[float, tuple[str, ...]]] = []
            for combo in combinations(seeds, 2):
                stats = self.evaluate(combo, side, self.dev)
                self.tests += 1
                self._consider(combo, side, stats)
                if self._dev_ok(stats, br):
                    survivors.append(self._row(combo, side, stats))
                if stats.get("trades", 0) >= MIN_DEV_TRADES:
                    pairs.append((stats["avg_net_r"], combo))
            pairs.sort(reverse=True)

            if MAX_CONDITIONS >= 3:
                for _, combo in pairs[:TOP_PAIRS]:
                    for extra in seeds:
                        if extra in combo:
                            continue
                        triple = tuple(sorted(combo + (extra,)))
                        stats = self.evaluate(triple, side, self.dev)
                        self.tests += 1
                        self._consider(triple, side, stats)
                        if self._dev_ok(stats, br):
                            survivors.append(self._row(triple, side, stats))
        self.near_misses = sorted(
            _dedupe(self.near_misses),
            key=lambda r: r["development"]["avg_net_r"],
            reverse=True,
        )[:NEAR_MISS_KEEP]
        return _dedupe(survivors)

    def window_trades(self) -> dict[str, int]:
        return {
            DEV: int(self.dev.sum()),
            VAL: int(self.val.sum()),
            HOLDOUT: int(self.hold.sum()),
        }

    def _row(self, names: tuple[str, ...], side: int, dev_stats: dict) -> dict:
        return {
            "instrument": self.pool.instrument,
            "vehicle": self.pool.vehicle,
            "side": "LONG" if side == LONG else "SHORT",
            "side_int": int(side),
            "stop_band_atr": self.pool.stop_atr,
            "horizon_trading_days": self.pool.horizon_days,
            "conditions": list(names),
            "complexity": len(names),
            "development": dev_stats,
        }


def _dedupe(rows: list[dict]) -> list[dict]:
    """Same conditions in a different order are the same rule."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for r in rows:
        key = (
            r["instrument"], r["side"], r["stop_band_atr"],
            r["horizon_trading_days"], tuple(sorted(r["conditions"])),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def searched_space() -> dict:
    """The predeclared search, for the artefacts."""
    return {
        "conditions": list(conditions.names()),
        "condition_count": len(conditions.names()),
        "max_conditions_per_rule": MAX_CONDITIONS,
        "stop_bands_atr": list(STOP_BANDS),
        "horizons_trading_days": list(HORIZONS),
        "sides": ["LONG", "SHORT (futures only — cash delivery cannot be short)"],
        "staging": "singles -> pairs from top singles -> triples from top pairs",
        "development_gates": {
            "min_trades": MIN_DEV_TRADES,
            "min_avg_net_r": MIN_DEV_AVG_NET_R,
            "min_t1_edge_over_pool_pct": MIN_DEV_T1_EDGE_PCT,
        },
        "split": {
            "development_fraction": DEV_FRACTION,
            "validation_fraction": VAL_FRACTION,
            "holdout_fraction": round(1.0 - DEV_FRACTION - VAL_FRACTION, 2),
            "why_not_3y_1y_1y": (
                "250 sessions of the five years are feature warm-up, so fixed "
                "three-year development would leave no holdout"
            ),
        },
    }


__all__ = [
    "Search", "windows", "window_mask", "window_note", "base_rate",
    "benjamini_hochberg", "searched_space", "DEV", "VAL", "HOLDOUT",
    "STOP_BANDS", "HORIZONS", "MIN_DEV_TRADES",
]
