"""Phase 31 §2 — forward excursion of the real five-year series.

No stop, no target, no strategy. From every eligible decision bar the fill is the
*next* bar's open, and from there the module measures how far price travelled in
favour and against, in percent, at each frozen horizon — plus how long a given
percentage took to arrive and how often the adverse side arrived first.

Two properties matter for honesty:

* a window never crosses a session boundary, so an overnight gap is never counted
  as an intraday excursion;
* favourable and adverse are reported together for the same horizon, so the "it
  gives 20%" claim always arrives next to what it risked to give it.

The fill bar is treated asymmetrically, deliberately. Where inside a one-minute
bar its high and low occurred is unknown, so the fill bar's high is **excluded**
from the favourable side (it cannot be proven to have happened after the fill)
while its low is **included** in the adverse side (it must be assumed to have).
Both choices push the measurement the same way: less favourable, more adverse
than an optimistic reading would give.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import data, features
from app.research.phase31 import HORIZONS, PCT_GRID

LONG, SHORT = 1, -1
SIDES = (LONG, SHORT)

MAX_HORIZON = max(HORIZONS)

# Percentiles reported for every distribution. A median alone hides the fact that
# most of the answer lives in the tail.
PCTILES = (10, 25, 50, 75, 90)

# Session periods, so "when" can be answered by part of day as well as by clock
# minutes. Boundaries are minutes from the session's first bar.
PERIODS = (
    ("OPEN_0_30", 0, 30),
    ("MORNING_30_90", 30, 90),
    ("MIDDAY_90_240", 90, 240),
    ("LATE_240_PLUS", 240, 10_000),
)


class Excursion:
    """Forward excursion arrays for one instrument and one side."""

    __slots__ = ("instrument", "side", "idx", "entry", "fav", "adv",
                 "first_fav", "first_adv", "minute", "session", "sessions",
                 "bars_available")

    def __init__(self, instrument: str, side: int) -> None:
        self.instrument = instrument
        self.side = side


def _prepare(s: data.Series) -> tuple[np.ndarray, np.ndarray]:
    sess = features._sessions(s.ts)
    mins = features._minutes_into_day(s.ts)
    first = np.zeros(len(s), dtype=np.int64)
    # Minutes since the session's own first bar, which MCX and NFO start at
    # different clock times.
    starts: dict[int, int] = {}
    for i, sid in enumerate(sess):
        if sid not in starts:
            starts[sid] = int(mins[i])
        first[i] = starts[sid]
    return sess, mins - first


def _bars_left(sess: np.ndarray) -> np.ndarray:
    """Bars of the same session available *after the fill* of each bar.

    A row is only measured when its longest window fits inside its own session,
    so every horizon is measured on the identical sample and no window silently
    ends early at the close.
    """
    n = sess.size
    left = np.zeros(n, dtype=np.int64)
    end = n - 1
    for i in range(n - 1, -1, -1):
        if i < n - 1 and sess[i] != sess[i + 1]:
            end = i
        left[i] = end - (i + 1)
    return np.maximum(left, 0)


def _shift(a: np.ndarray, k: int, fill: float) -> np.ndarray:
    out = np.full(a.shape, fill, dtype=np.float64)
    if k < a.size:
        out[:-k] = a[k:]
    return out


def build(s: data.Series, side: int) -> Excursion:
    """Measure the excursion of every eligible bar on one side.

    The decision bar is ``i``; the fill is ``open[i+1]``; the window is bars
    ``i+2 .. i+1+h`` of the same session, so no part of the measurement is
    visible at the decision.
    """
    n = len(s)
    sess, into = _prepare(s)
    ex = Excursion(s.instrument, side)

    entry = _shift(s.open, 1, np.nan)

    # Favourable extremes start empty; adverse extremes start at the fill bar's
    # own low/high, which is the conservative reading of an unknown intra-bar
    # path (see the module note).
    run_hi = np.full(n, -np.inf)
    run_lo = np.full(n, np.inf)
    adv_lo = _shift(s.low, 1, np.nan)
    adv_hi = _shift(s.high, 1, np.nan)
    fav = {}
    adv = {}
    # First bar (in minutes from fill) at which each threshold was crossed;
    # 0 means "never within the longest horizon".
    first_fav = np.zeros((len(PCT_GRID), n), dtype=np.int16)
    first_adv = np.zeros((len(PCT_GRID), n), dtype=np.int16)

    sess_f = sess.astype(np.float64)
    for k in range(1, MAX_HORIZON + 1):
        # bar i+1+k, still inside the session of bar i
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
                adv_pct = 100.0 * (adv_lo - entry) / entry
            else:
                fav_pct = 100.0 * (entry - run_lo) / entry
                adv_pct = 100.0 * (entry - adv_hi) / entry
        for g, thr in enumerate(PCT_GRID):
            hit = (fav_pct >= thr) & (first_fav[g] == 0)
            first_fav[g][hit] = k
            hit = (adv_pct <= -thr) & (first_adv[g] == 0)
            first_adv[g][hit] = k
        if k in HORIZONS:
            fav[k] = fav_pct.copy()
            adv[k] = adv_pct.copy()

    ex.idx = np.arange(n)
    ex.entry = entry
    ex.fav = fav
    ex.adv = adv
    ex.first_fav = first_fav
    ex.first_adv = first_adv
    ex.minute = into
    ex.bars_available = _bars_left(sess)
    ex.session = sess
    ex.sessions = int(np.unique(sess).size)
    return ex


def eligible(ex: Excursion) -> np.ndarray:
    """Bars with a fill price and a full longest-horizon window in-session.

    Requiring the *longest* window for every row keeps each horizon measured on
    the identical sample, so a shorter horizon cannot look better simply because
    it kept the end-of-session bars a longer one had to drop.
    """
    ok = np.isfinite(ex.entry) & np.isfinite(ex.fav[MAX_HORIZON])
    ok = ok & np.isfinite(ex.adv[MAX_HORIZON])
    return ok & (ex.bars_available >= MAX_HORIZON)


def _dist(a: np.ndarray) -> dict:
    a = a[np.isfinite(a)]
    if a.size == 0:
        return {"n": 0}
    qs = np.percentile(a, PCTILES)
    return {
        "n": int(a.size),
        "mean": round(float(a.mean()), 4),
        **{f"p{p}": round(float(q), 4) for p, q in zip(PCTILES, qs)},
    }


def summarize(ex: Excursion, mask: np.ndarray | None = None) -> dict:
    """Excursion distribution, threshold reach rates and timing for one cohort."""
    m = eligible(ex)
    if mask is not None:
        m = m & mask
    rows: dict = {
        "instrument": ex.instrument,
        "side": "LONG" if ex.side == LONG else "SHORT",
        "decision_instants": int(m.sum()),
        # Sessions the cohort actually spans, not the series total, so a period
        # cohort cannot borrow the whole run's breadth.
        "sessions": int(np.unique(ex.session[m]).size),
        "horizons": {},
        "thresholds": {},
    }
    for h in HORIZONS:
        rows["horizons"][str(h)] = {
            "favourable_pct": _dist(ex.fav[h][m]),
            "adverse_pct": _dist(ex.adv[h][m]),
        }
    total = int(m.sum())
    for g, thr in enumerate(PCT_GRID):
        ff = ex.first_fav[g][m].astype(np.int32)
        fa = ex.first_adv[g][m].astype(np.int32)
        reached = ff > 0
        # "adverse first" only compares rows where the favourable side arrived at
        # all; a row that never reached the threshold has nothing to order.
        both = reached & (fa > 0)
        adverse_first = int(np.sum(both & (fa < ff)))
        times = ff[reached]
        row = {
            "threshold_pct": thr,
            "reached_pct_of_instants": round(
                100.0 * reached.sum() / total, 3) if total else None,
            "adverse_same_size_first_pct": round(
                100.0 * adverse_first / max(1, int(reached.sum())), 3),
            "minutes_to_reach": _dist(times.astype(np.float64)),
        }
        for h in HORIZONS:
            row[f"reached_within_{h}m_pct"] = round(
                100.0 * float(np.sum(reached & (ff <= h))) / total, 3
            ) if total else None
        rows["thresholds"][f"{thr:.2f}"] = row
    return rows


def by_period(ex: Excursion) -> list[dict]:
    """Same measurement split by part of session, which answers "when"."""
    out = []
    for name, lo, hi in PERIODS:
        mask = (ex.minute >= lo) & (ex.minute < hi)
        s = summarize(ex, mask)
        s["period"] = name
        out.append(s)
    return out
