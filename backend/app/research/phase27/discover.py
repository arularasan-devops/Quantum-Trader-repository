"""Phase 27 §2 — bounded, staged discovery on development years only.

The search itself is Phase 24's, inherited rather than re-written: singles, then
pairs built only from surviving singles, then triples built only from surviving
pairs, with every hypothesis counted so the multiple-testing correction gets the
honest denominator. Re-implementing a staged search per timeframe is how two
studies end up incomparable, and the gates here are the ones Phase 24 published:
100 development trades, positive net R after costs, and at least two points of
T1 rate above the cohort's own pool base rate.

What this subclass changes is only what has to change: the condition masks come
from the bar-honest Phase 27 vocabulary, and each rule carries the timeframe it
was found on so a 5-minute rule can never be read as a 15-minute one.
"""
from __future__ import annotations

import time

import numpy as np

from app.research.phase24 import discover as p24discover
from app.research.phase27 import conditions, pool

# Re-exported so callers do not have to import two discovery modules to read one
# study; these are Phase 24's definitions, unchanged.
DEV = p24discover.DEV
VAL = p24discover.VAL
HOLDOUT = p24discover.HOLDOUT
YEAR = p24discover.YEAR
STOP_BANDS = p24discover.STOP_BANDS
MIN_DEV_TRADES = p24discover.MIN_DEV_TRADES
MIN_DEV_T1_EDGE_PCT = p24discover.MIN_DEV_T1_EDGE_PCT
MIN_DEV_AVG_NET_R = p24discover.MIN_DEV_AVG_NET_R

windows = p24discover.windows
window_mask = p24discover.window_mask
base_rate = p24discover.base_rate


def benjamini_hochberg(
    p_values: list[float],
    alpha: float = 0.05,
    *,
    tests: int | None = None,
) -> list[bool]:
    """BH step-up against the number of hypotheses **evaluated**.

    The staged search throws most of its hypotheses away before ranking, so
    correcting against the survivors would divide by 40 when 500 rules were tried
    and would hand back significance the search did not earn. ``tests`` is the
    counted denominator; the untested hypotheses are the ones that failed on
    development, so they are treated as p = 1 and simply widen the denominator.
    """
    m_kept = len(p_values)
    if m_kept == 0:
        return []
    m = max(int(tests or m_kept), m_kept)
    order = sorted(range(m_kept), key=lambda i: p_values[i])
    keep = [False] * m_kept
    threshold_rank = -1
    for position, i in enumerate(order, start=1):
        if p_values[i] <= alpha * position / m:
            threshold_rank = position
    for position, i in enumerate(order, start=1):
        if threshold_rank >= 0 and position <= threshold_rank:
            keep[i] = True
    return keep


class Search(p24discover.Search):
    """One instrument, one timeframe, one stop band, masks evaluated once."""

    def __init__(self, p: pool.Pool, stop_band: float) -> None:
        # Phase 24's constructor would evaluate its own vocabulary against these
        # features under 1-minute names. The fields are set up here instead, with
        # the bar-honest masks, so nothing is computed twice and nothing is
        # labelled in minutes it was not measured in.
        self.pool = p
        self.timeframe = int(p.timeframe)
        self.stop_band = float(stop_band)
        self.masks = conditions.masks(p.feat, p.side, p.timeframe)
        self.resolved = p.out.resolved
        self.win = windows(p.ts)
        self.dev = window_mask(p.ts, self.win[DEV]) & self.resolved
        self.val = window_mask(p.ts, self.win[VAL]) & self.resolved
        self.hold = window_mask(p.ts, self.win[HOLDOUT]) & self.resolved
        self.tests = 0
        self.near_misses: list[dict] = []

    def _row(self, names: tuple[str, ...], side: int, dev_stats: dict) -> dict:
        row = super()._row(names, side, dev_stats)
        row["timeframe_minutes"] = self.timeframe
        return row

    def window_trades(self) -> dict[str, int]:
        """Resolved candidates per window, for the report's sample statement."""
        return {
            DEV: int(self.dev.sum()),
            VAL: int(self.val.sum()),
            HOLDOUT: int(self.hold.sum()),
        }


def window_note(ts: np.ndarray) -> dict:
    """The window boundaries in a form the report can print."""
    return {
        name: {
            "from_ts": lo,
            "to_ts": hi,
            "from": time.strftime("%Y-%m-%d", time.gmtime(lo)),
            "to": time.strftime("%Y-%m-%d", time.gmtime(hi)),
        }
        for name, (lo, hi) in windows(ts).items()
    }
