"""Phase 25 §5 — bounded discovery inside the captured window.

Same discipline as the five-year study, on a window that is weeks long, so the
limits are stated up front and enforced rather than hoped for:

* the split is **chronological and by session**, not by row. A window boundary
  inside a session would put the morning of a day in development and the
  afternoon of the same day in the holdout, which is not out-of-sample;
* the search is staged — singles, then pairs from surviving singles, then
  triples from surviving pairs — and nothing is ever *selected* on validation or
  holdout data. Those windows only confirm or refuse;
* the number of hypotheses actually evaluated is counted, including both sides
  and every stop band, and carried into the Benjamini-Hochberg correction, so
  the correction is applied to the honest denominator;
* the captured window cannot carry a walk-forward of years, so the folds here
  are session folds and are reported as such. A survivor is a lead, never a
  validated edge.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np

from app.research.phase24 import metrics
from app.research.phase24.discover import benjamini_hochberg  # noqa: F401 - re-exported
from app.research.phase25 import conditions, outcomes, pool

DEV = "development"
VAL = "validation"
HOLDOUT = "holdout"

# Fractions of *sessions*, chronologically. A short capture cannot afford a
# 3/1/1 year split, so the shape is stated here and the report repeats it.
DEV_FRAC = 0.60
VAL_FRAC = 0.20

# Stage gates on development only. Kept at the same shape as the five-year
# study; the sample bar is what makes a captured-window survivor honest.
MIN_DEV_TRADES = 100
MIN_WINDOW_TRADES = 40
MIN_DEV_T1_EDGE_PCT = 2.0
MIN_DEV_AVG_NET_R = 0.0
NEAR_MISS_KEEP = 5
TOP_SINGLES = 8
TOP_PAIRS = 12
MAX_CONDITIONS = 3

WALK_FOLDS = 4


def session_windows(session: np.ndarray) -> dict[str, np.ndarray]:
    """Boolean row masks for the chronological development/validation/holdout."""
    uniq = np.unique(session)
    n = uniq.size
    if n == 0:
        empty = np.zeros(session.size, dtype=bool)
        return {DEV: empty, VAL: empty.copy(), HOLDOUT: empty.copy()}
    dev_end = max(1, int(round(n * DEV_FRAC)))
    val_end = max(dev_end, int(round(n * (DEV_FRAC + VAL_FRAC))))
    dev_days, val_days = uniq[:dev_end], uniq[dev_end:val_end]
    hold_days = uniq[val_end:]
    return {
        DEV: np.isin(session, dev_days),
        VAL: np.isin(session, val_days),
        HOLDOUT: np.isin(session, hold_days),
    }


def walk_folds(session: np.ndarray, folds: int = WALK_FOLDS) -> list[np.ndarray]:
    """Equal-session chronological folds, for confirmation rather than selection."""
    uniq = np.unique(session)
    if uniq.size < folds:
        return []
    chunks = np.array_split(uniq, folds)
    return [np.isin(session, c) for c in chunks]


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
        self.win = session_windows(p.session)
        self.dev = self.win[DEV] & self.resolved
        self.val = self.win[VAL] & self.resolved
        self.hold = self.win[HOLDOUT] & self.resolved
        self.tests = 0
        self.near_misses: list[dict] = []

    def cohort(self, names: tuple[str, ...], option_type: str) -> np.ndarray:
        m = self.pool.option_type == option_type
        for n in names:
            m = m & self.masks[n]
        return m

    def evaluate(self, names: tuple[str, ...], option_type: str,
                 window: np.ndarray) -> dict:
        m = self.cohort(names, option_type) & window
        return metrics.summarise(self.pool.out, m, base_rate(self.pool.out, window))

    def _dev_ok(self, stats: dict, br: float) -> bool:
        if stats.get("trades", 0) < MIN_DEV_TRADES:
            return False
        if stats["avg_net_r"] <= MIN_DEV_AVG_NET_R:
            return False
        return stats["t1_before_sl_pct"] - 100.0 * br >= MIN_DEV_T1_EDGE_PCT

    def _consider(self, names: tuple[str, ...], option_type: str,
                  stats: dict) -> None:
        if stats.get("trades", 0) >= MIN_DEV_TRADES:
            self.near_misses.append(self._row(names, option_type, stats))

    def run(self) -> list[dict]:
        """Staged search on development sessions only; survivors unranked."""
        survivors: list[dict] = []
        br = base_rate(self.pool.out, self.dev)
        for otype in ("CE", "PE"):
            singles: list[tuple[float, str]] = []
            for name in self.masks:
                stats = self.evaluate((name,), otype, self.dev)
                self.tests += 1
                self._consider((name,), otype, stats)
                if self._dev_ok(stats, br):
                    survivors.append(self._row((name,), otype, stats))
                if stats.get("trades", 0) >= MIN_DEV_TRADES:
                    singles.append((stats["avg_net_r"], name))
            singles.sort(reverse=True)
            seeds = [n for _, n in singles[:TOP_SINGLES]]

            pairs: list[tuple[float, tuple[str, str]]] = []
            for combo in combinations(seeds, 2):
                stats = self.evaluate(combo, otype, self.dev)
                self.tests += 1
                self._consider(combo, otype, stats)
                if self._dev_ok(stats, br):
                    survivors.append(self._row(combo, otype, stats))
                if stats.get("trades", 0) >= MIN_DEV_TRADES:
                    pairs.append((stats["avg_net_r"], combo))
            pairs.sort(reverse=True)

            if MAX_CONDITIONS >= 3:
                for _, combo in pairs[:TOP_PAIRS]:
                    for extra in seeds:
                        if extra in combo:
                            continue
                        triple = tuple(sorted(combo + (extra,)))
                        stats = self.evaluate(triple, otype, self.dev)
                        self.tests += 1
                        self._consider(triple, otype, stats)
                        if self._dev_ok(stats, br):
                            survivors.append(self._row(triple, otype, stats))
        self.near_misses = sorted(
            _dedupe(self.near_misses),
            key=lambda r: r["development"]["avg_net_r"],
            reverse=True,
        )[:NEAR_MISS_KEEP]
        return _dedupe(survivors)

    def confirm(self, row: dict) -> dict:
        """Validation, holdout and session-fold results for one survivor."""
        names = tuple(row["conditions"])
        otype = row["option_type"]
        cohort = self.cohort(names, otype)
        val = metrics.summarise(
            self.pool.out, cohort & self.val, base_rate(self.pool.out, self.val)
        )
        hold = metrics.summarise(
            self.pool.out, cohort & self.hold, base_rate(self.pool.out, self.hold)
        )
        folds = []
        for f in walk_folds(self.pool.session):
            fm = f & self.resolved
            folds.append(metrics.summarise(
                self.pool.out, cohort & fm, base_rate(self.pool.out, fm)
            ))
        out = dict(row)
        out["validation"] = val
        out["holdout"] = hold
        out["session_folds"] = folds
        out["folds_positive"] = sum(
            1 for f in folds
            if f.get("trades", 0) >= MIN_WINDOW_TRADES and f.get("avg_net_r", 0) > 0
        )
        out["folds_measurable"] = sum(
            1 for f in folds if f.get("trades", 0) >= MIN_WINDOW_TRADES
        )
        return out

    def _row(self, names: tuple[str, ...], option_type: str,
             dev_stats: dict) -> dict:
        return {
            "instrument": self.pool.instrument,
            "vehicle": option_type,
            "option_type": option_type,
            "side": "LONG_VIEW" if option_type == "CE" else "SHORT_VIEW",
            "stop_band_pct_of_premium": round(100.0 * self.stop_band, 1),
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
            r["instrument"], r["option_type"], r["stop_band_pct_of_premium"],
            tuple(sorted(r["conditions"])),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out
