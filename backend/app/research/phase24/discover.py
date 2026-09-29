"""Phase 24 §5/§6/§8 — bounded discovery on development years only.

The search is deliberately small and staged, because §5 forbids brute-forcing
thousands of combinations and picking the best:

1. every single condition, per instrument, per side, per stop band;
2. pairs built only from the singles that survived stage 1;
3. triples built only from the pairs that survived stage 2.

Nothing is ever *selected* on validation or holdout data. Stages run on the
development window alone; the later windows only ever confirm or refuse what
development proposed. The number of hypotheses actually evaluated is counted and
carried into the report so the Benjamini-Hochberg correction is applied to the
real number, not to a flattering one.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np

from app.research.phase24 import conditions, metrics, outcomes, pool

# Chronological windows, §8. Years are counted from the first bar, not from a
# calendar year, so an incomplete first year cannot shrink the holdout.
YEAR = 365 * 86_400
DEV_YEARS = 3
VAL_YEARS = 1

DEV = "development"
VAL = "validation"
HOLDOUT = "holdout"

# Frozen stop-geometry bands. A 1-minute ATR stop on index futures pays close to
# one full R in charges, so a study that only tested one band would report
# "nothing works" when what it measured was "this stop is too tight to pay for
# itself". The band is part of the searched space and is counted as such.
STOP_BANDS = (1.0, 2.0, 3.0, 4.0)

# Stage gates on development only.
MIN_DEV_TRADES = 100
MIN_DEV_T1_EDGE_PCT = 2.0     # must beat its own pool base rate by this much
MIN_DEV_AVG_NET_R = 0.0       # must be net positive after costs
NEAR_MISS_KEEP = 5
TOP_SINGLES = 8
TOP_PAIRS = 12
MAX_CONDITIONS = 3


def windows(ts: np.ndarray) -> dict[str, tuple[int, int]]:
    """Chronological development / validation / holdout boundaries."""
    t0 = int(ts.min())
    dev_end = t0 + DEV_YEARS * YEAR
    val_end = dev_end + VAL_YEARS * YEAR
    return {
        DEV: (t0, dev_end),
        VAL: (dev_end, val_end),
        HOLDOUT: (val_end, int(ts.max()) + 1),
    }


def window_mask(ts: np.ndarray, span: tuple[int, int]) -> np.ndarray:
    lo, hi = span
    return (ts >= lo) & (ts < hi)


def base_rate(o: outcomes.Outcomes, mask: np.ndarray) -> float:
    """The pool's own T1 rate inside a window — the coin a cohort must beat."""
    if not mask.any():
        return 0.0
    return float(o.t1_before_sl[mask].mean())


class Search:
    """One instrument at one stop band, with its masks evaluated once."""

    def __init__(self, p: pool.Pool, stop_band: float) -> None:
        self.pool = p
        self.stop_band = stop_band
        self.masks = conditions.masks(p.feat, p.side)
        self.resolved = p.out.resolved
        self.win = windows(p.ts)
        self.dev = window_mask(p.ts, self.win[DEV]) & self.resolved
        self.val = window_mask(p.ts, self.win[VAL]) & self.resolved
        self.hold = window_mask(p.ts, self.win[HOLDOUT]) & self.resolved
        self.tests = 0
        # Every cohort large enough to be read, gate or no gate. A study whose only
        # output is "nothing passed" hides whether it missed narrowly or by a mile.
        self.near_misses: list[dict] = []

    def cohort(self, names: tuple[str, ...], side: int) -> np.ndarray:
        m = self.pool.side == side
        for n in names:
            m = m & self.masks[n]
        return m

    def evaluate(self, names: tuple[str, ...], side: int, window: np.ndarray) -> dict:
        m = self.cohort(names, side) & window
        return metrics.summarise(self.pool.out, m, base_rate(self.pool.out, window))

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
        """Staged search; returns the development-surviving rules, unranked."""
        survivors: list[dict] = []
        br = base_rate(self.pool.out, self.dev)
        for side in (outcomes.LONG, outcomes.SHORT):
            singles: list[tuple[float, str]] = []
            for name in self.masks:
                stats = self.evaluate((name,), side, self.dev)
                self.tests += 1
                self._consider((name,), side, stats)
                if self._dev_ok(stats, br):
                    survivors.append(self._row((name,), side, stats))
                # Ranking for the next stage uses net R, not the hit rate: §6
                # explicitly refuses to promote a high win rate that loses money.
                if stats.get("trades", 0) >= MIN_DEV_TRADES:
                    singles.append((stats["avg_net_r"], name))
            singles.sort(reverse=True)
            seeds = [n for _, n in singles[:TOP_SINGLES]]

            pairs: list[tuple[float, tuple[str, str]]] = []
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

    def _row(self, names: tuple[str, ...], side: int, dev_stats: dict) -> dict:
        return {
            "instrument": self.pool.instrument,
            "vehicle": "FUTURES",
            "side": "LONG" if side == outcomes.LONG else "SHORT",
            "side_int": int(side),
            "stop_band_atr": self.stop_band,
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
            tuple(sorted(r["conditions"])),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def benjamini_hochberg(p_values: list[float], alpha: float = 0.05) -> list[bool]:
    """BH step-up: which p-values survive at FDR ``alpha``.

    Applied to the number of hypotheses actually evaluated, including the stop
    bands and both sides — the correction is only meaningful if the denominator is
    the honest one.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    keep = [False] * m
    threshold_rank = -1
    for rank, i in enumerate(order, start=1):
        if p_values[i] <= alpha * rank / m:
            threshold_rank = rank
    for rank, i in enumerate(order, start=1):
        if threshold_rank >= 0 and rank <= threshold_rank:
            keep[i] = True
    return keep
