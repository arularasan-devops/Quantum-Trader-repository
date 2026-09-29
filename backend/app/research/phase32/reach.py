"""Phase 32 — when each distance is first touched, and what that pays.

One forward pass per instrument and side records, for every candidate distance,
the minute at which it was first touched in favour and the minute at which the
same-signed distance was first touched against. Every T1/stop/cap configuration
is then resolved by arithmetic on those two arrays, so adding a configuration
costs no extra pass over the bars and no configuration can quietly be measured on
a different sample than another.

The honesty rules are the same as Phase 31's, plus one more that only matters
once a stop exists:

* the decision is bar ``i``, the fill is ``open[i+1]``;
* a window never crosses a session boundary;
* the fill bar's own high does not count in favour (it cannot be shown to have
  happened after the fill) but its low does count against;
* when a single minute contains both T1 and the stop, the **stop** is taken. A
  one-minute bar cannot say which came first, and the optimistic reading is what
  turns a losing rule into a winning backtest.
"""
from __future__ import annotations

import numpy as np

from app.market.instruments import get_spec
from app.research.phase24 import data, outcomes as p24outcomes
from app.research.phase31.excursion import LONG, SHORT, _bars_left, _prepare, _shift
from app.research.phase32 import CAPS, SL_MULTIPLES, T1_GRID

MAX_CAP = max(CAPS)

# Every distance any configuration can ask about: the T1 grid plus each stop
# distance implied by a multiple of it.
DISTANCES: tuple[float, ...] = tuple(sorted({
    round(t, 6) for t in T1_GRID
} | {
    round(t * m, 6) for t in T1_GRID for m in SL_MULTIPLES
}))
_DIST_INDEX = {d: i for i, d in enumerate(DISTANCES)}


class Reach:
    """First-touch minutes and horizon returns for one instrument and one side."""

    __slots__ = ("instrument", "side", "entry", "first_fav", "first_adv",
                 "ret", "session", "minute", "bars_available", "eligible")

    def __init__(self, instrument: str, side: int) -> None:
        self.instrument = instrument
        self.side = side

    def index_of(self, distance_pct: float) -> int:
        """Row in ``first_fav``/``first_adv`` for a distance, or raise.

        Raising rather than interpolating is deliberate: a distance outside the
        frozen grid would be an unfrozen hypothesis.
        """
        key = round(float(distance_pct), 6)
        if key not in _DIST_INDEX:
            raise KeyError(f"{distance_pct} is not a frozen distance")
        return _DIST_INDEX[key]


def build(s: data.Series, side: int) -> Reach:
    """One forward pass: first touch of every frozen distance, both directions."""
    n = len(s)
    sess, into = _prepare(s)
    r = Reach(s.instrument, side)

    entry = _shift(s.open, 1, np.nan)
    run_hi = np.full(n, -np.inf)
    run_lo = np.full(n, np.inf)
    adv_lo = _shift(s.low, 1, np.nan)
    adv_hi = _shift(s.high, 1, np.nan)

    nd = len(DISTANCES)
    first_fav = np.zeros((nd, n), dtype=np.int16)
    first_adv = np.zeros((nd, n), dtype=np.int16)
    ret: dict[int, np.ndarray] = {}

    sess_f = sess.astype(np.float64)
    for k in range(1, MAX_CAP + 1):
        same = _shift(sess_f, 1 + k, -1.0) == sess_f
        hi = np.where(same, _shift(s.high, 1 + k, np.nan), np.nan)
        lo = np.where(same, _shift(s.low, 1 + k, np.nan), np.nan)
        with np.errstate(invalid="ignore"):
            run_hi = np.fmax(run_hi, hi)
            run_lo = np.fmin(run_lo, lo)
            adv_lo = np.fmin(adv_lo, lo)
            adv_hi = np.fmax(adv_hi, hi)
            if side == LONG:
                fav_pct = 100.0 * (run_hi - entry) / entry
                adv_pct = 100.0 * (entry - adv_lo) / entry
            else:
                fav_pct = 100.0 * (entry - run_lo) / entry
                adv_pct = 100.0 * (adv_hi - entry) / entry
            for g, dist in enumerate(DISTANCES):
                hit = (fav_pct >= dist) & (first_fav[g] == 0)
                first_fav[g][hit] = k
                hit = (adv_pct >= dist) & (first_adv[g] == 0)
                first_adv[g][hit] = k
            if k in CAPS:
                cl = np.where(same, _shift(s.close, 1 + k, np.nan), np.nan)
                sgn = 1.0 if side == LONG else -1.0
                ret[k] = 100.0 * sgn * (cl - entry) / entry

    r.entry = entry
    r.first_fav = first_fav
    r.first_adv = first_adv
    r.ret = ret
    r.session = sess
    r.minute = into
    r.bars_available = _bars_left(sess)
    # The longest cap decides eligibility for every cap, so a shorter cap cannot
    # look better merely by keeping end-of-session bars a longer one had to drop.
    r.eligible = (
        np.isfinite(entry)
        & np.isfinite(ret[MAX_CAP])
        & (r.bars_available >= MAX_CAP)
    )
    return r


def cost_pct(instrument: str, entry: np.ndarray) -> np.ndarray:
    """Round-trip cost as a percentage of price, from the shared cost model.

    Charged through ``phase19.futcosts`` rather than restated here. The spread is
    not in it — the historical feed publishes candles, not depth — so every net
    figure in this phase is optimistic by exactly one spread and says so.
    """
    lot = int(get_spec(instrument).lot_size or 1)
    pts = p24outcomes.cost_points_per_trade(
        instrument, entry, entry, lot_size=lot, slippage_points=None,
    )
    with np.errstate(invalid="ignore", divide="ignore"):
        out = 100.0 * pts / entry
    return np.where(np.isfinite(out), out, np.nan)


T1_FIRST = 1
SL_FIRST = -1
TIMEOUT = 0


def resolve(
    r: Reach, t1_pct: float, sl_pct: float, cap: int, mask: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Outcome code and gross percentage for one configuration on one cohort.

    A tie inside a minute resolves as the stop. Neither touched by the cap is a
    timeout, closed at that bar's close — a real outcome, not a hold-until-it-works.
    """
    ti = r.index_of(t1_pct)
    si = r.index_of(sl_pct)
    ff = r.first_fav[ti][mask].astype(np.int32)
    fa = r.first_adv[si][mask].astype(np.int32)
    t1_in = (ff > 0) & (ff <= cap)
    sl_in = (fa > 0) & (fa <= cap)

    t1_win = t1_in & (~sl_in | (ff < fa))
    sl_win = sl_in & ~t1_win
    code = np.where(t1_win, T1_FIRST, np.where(sl_win, SL_FIRST, TIMEOUT))
    gross = np.where(
        t1_win, float(t1_pct),
        np.where(sl_win, -float(sl_pct), r.ret[cap][mask]),
    )
    return code.astype(np.int8), gross


def minutes_to_t1(r: Reach, t1_pct: float, cap: int, mask: np.ndarray) -> np.ndarray:
    """Minutes to T1 for the rows where T1 arrived inside the cap."""
    ff = r.first_fav[r.index_of(t1_pct)][mask].astype(np.int32)
    return ff[(ff > 0) & (ff <= cap)].astype(np.float64)


SIDES = (LONG, SHORT)


def side_name(side: int) -> str:
    return "LONG" if side == LONG else "SHORT"
