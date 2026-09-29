"""Phase 29 §6 — bounded discovery inside the captured window.

Same discipline as every earlier phase, and the same limits enforced rather than
hoped for:

* the split is **chronological and by session**, not by row. A boundary inside a
  session would put the morning of a day in development and the afternoon of the
  same day in the holdout, which is not out-of-sample;
* the search is staged — singles, then pairs from surviving singles, then triples
  from surviving pairs — and nothing is ever *selected* on validation or holdout
  data. Those windows confirm or refuse, once;
* every hypothesis evaluated is counted, across all three structures, all
  geometries and all stop bands, and the count is carried into the
  Benjamini-Hochberg correction so the correction has the honest denominator;
* the pool a cohort is compared against is its **own structure's** pool. A bull
  put spread has to beat selling bull put spreads indiscriminately, not beat
  zero and not beat a different structure.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np

from app.research.phase24 import metrics
from app.research.phase24.discover import benjamini_hochberg  # noqa: F401 - re-exported
from app.research.phase29 import conditions, legs, pool

DEV = "development"
VAL = "validation"
HOLDOUT = "holdout"

DEV_FRAC = 0.60
VAL_FRAC = 0.20

MIN_DEV_TRADES = 100
MIN_WINDOW_TRADES = 40
MIN_DEV_EDGE_PCT = 2.0
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
    return {
        DEV: np.isin(session, uniq[:dev_end]),
        VAL: np.isin(session, uniq[dev_end:val_end]),
        HOLDOUT: np.isin(session, uniq[val_end:]),
    }


def walk_folds(session: np.ndarray, folds: int = WALK_FOLDS) -> list[np.ndarray]:
    """Equal-session chronological folds, for confirmation rather than selection."""
    uniq = np.unique(session)
    if uniq.size < folds:
        return []
    return [np.isin(session, c) for c in np.array_split(uniq, folds)]


def base_rate(o, mask: np.ndarray) -> float:
    """The structure's own target-before-stop rate inside a window."""
    if not mask.any():
        return 0.0
    return float(o.t1_before_sl[mask].mean())


class Search:
    """One instrument, one structure, one geometry, masks evaluated once."""

    def __init__(self, p: pool.Pool) -> None:
        self.pool = p
        self.masks = conditions.masks(p.feat, p.side, p.structure)
        self.resolved = p.out.resolved
        self.win = session_windows(p.session)
        self.dev = self.win[DEV] & self.resolved
        self.val = self.win[VAL] & self.resolved
        self.hold = self.win[HOLDOUT] & self.resolved
        self.tests = 0
        self.near_misses: list[dict] = []

    def cohort(self, names: tuple[str, ...]) -> np.ndarray:
        m = np.ones(len(self.pool), dtype=bool)
        for n in names:
            m = m & self.masks[n]
        return m

    def evaluate(self, names: tuple[str, ...], window: np.ndarray) -> dict:
        m = self.cohort(names) & window
        stats = metrics.summarise(self.pool.out, m, base_rate(self.pool.out, window))
        if m.any():
            o = self.pool.out
            stats["avg_return_on_defined_risk_pct"] = round(
                100.0 * float(o.ror_defined_risk[m].mean()), 3
            )
            stats["avg_net_rupees"] = round(float(o.net_rupees[m].mean()), 2)
            stats["total_net_rupees"] = round(float(o.net_rupees[m].sum()), 2)
            stats["median_credit_points"] = round(float(np.median(o.credit[m])), 2)
            stats["median_defined_loss_points"] = round(
                float(np.median(o.max_loss[m])), 2
            )
        return stats

    def _dev_ok(self, stats: dict, br: float) -> bool:
        if stats.get("trades", 0) < MIN_DEV_TRADES:
            return False
        if stats["avg_net_r"] <= MIN_DEV_AVG_NET_R:
            return False
        return stats["t1_before_sl_pct"] - 100.0 * br >= MIN_DEV_EDGE_PCT

    def _consider(self, names: tuple[str, ...], stats: dict) -> None:
        if stats.get("trades", 0) >= MIN_DEV_TRADES:
            self.near_misses.append(self._row(names, stats))

    def run(self) -> list[dict]:
        """Staged search on development sessions only; survivors unranked."""
        survivors: list[dict] = []
        br = base_rate(self.pool.out, self.dev)

        singles: list[tuple[float, str]] = []
        for name in self.masks:
            stats = self.evaluate((name,), self.dev)
            self.tests += 1
            self._consider((name,), stats)
            if self._dev_ok(stats, br):
                survivors.append(self._row((name,), stats))
            if stats.get("trades", 0) >= MIN_DEV_TRADES:
                singles.append((stats["avg_net_r"], name))
        singles.sort(reverse=True)
        seeds = [n for _, n in singles[:TOP_SINGLES]]

        pairs: list[tuple[float, tuple[str, ...]]] = []
        for combo in combinations(seeds, 2):
            stats = self.evaluate(combo, self.dev)
            self.tests += 1
            self._consider(combo, stats)
            if self._dev_ok(stats, br):
                survivors.append(self._row(combo, stats))
            if stats.get("trades", 0) >= MIN_DEV_TRADES:
                pairs.append((stats["avg_net_r"], combo))
        pairs.sort(reverse=True)

        if MAX_CONDITIONS >= 3:
            for _, combo in pairs[:TOP_PAIRS]:
                for extra in seeds:
                    if extra in combo:
                        continue
                    triple = tuple(sorted(combo + (extra,)))
                    stats = self.evaluate(triple, self.dev)
                    self.tests += 1
                    self._consider(triple, stats)
                    if self._dev_ok(stats, br):
                        survivors.append(self._row(triple, stats))

        self.near_misses = sorted(
            _dedupe(self.near_misses),
            key=lambda r: r["development"]["avg_net_r"],
            reverse=True,
        )[:NEAR_MISS_KEEP]
        return _dedupe(survivors)

    def confirm(self, row: dict) -> dict:
        """Validation, holdout and session-fold results for one survivor."""
        names = tuple(row["conditions"])
        out = dict(row)
        out["validation"] = self.evaluate(names, self.val)
        out["holdout"] = self.evaluate(names, self.hold)
        folds = [
            self.evaluate(names, f & self.resolved)
            for f in walk_folds(self.pool.session)
        ]
        out["session_folds"] = folds
        out["folds_positive"] = sum(
            1 for f in folds
            if f.get("trades", 0) >= MIN_WINDOW_TRADES and f.get("avg_net_r", 0) > 0
        )
        out["folds_measurable"] = sum(
            1 for f in folds if f.get("trades", 0) >= MIN_WINDOW_TRADES
        )
        return out

    def _row(self, names: tuple[str, ...], dev_stats: dict) -> dict:
        p = self.pool
        return {
            "instrument": p.instrument,
            "structure": p.structure,
            "hold": p.hold,
            "vehicle": "OPTION_CREDIT_SPREAD",
            "underlying_view": legs.VIEW[p.structure],
            "short_steps_otm": p.short_steps,
            "width_steps": p.width_steps,
            "width_points": round(p.width_points, 2),
            "stop_credit_multiple": p.stop_credit_mult,
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
            r["instrument"], r["structure"], r["hold"], r["short_steps_otm"],
            r["width_steps"], r["stop_credit_multiple"],
            tuple(sorted(r["conditions"])),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out
