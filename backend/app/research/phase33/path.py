"""Phase 33 §5/§6/§8 — the forward path of every decision instant.

One pass per instrument and side records, for every eligible bar:

* the favourable and adverse excursion at each frozen holding horizon;
* the close-to-close return at each horizon, which is what a trade held to that
  horizon and closed actually gets — not the excursion, which is what it *could*
  have got with a perfect exit;
* the minute the favourable excursion peaked, and how far the trade fell back
  after that peak, so giveback is measured rather than inferred;
* the first minute each frozen distance was touched;
* the same three numbers for a hold to the session close, computed with a suffix
  scan instead of extending the loop, because an MCX session is 870 minutes long
  and the loop would cost seven times as much for one column.

The fill and session rules are Phase 31's and Phase 32's, unchanged so the three
phases quote the same sample:

* decision on bar ``i``, fill at ``open[i+1]``;
* a window never crosses a session boundary — an overnight gap is not an intraday
  excursion, and overnight is a separate study;
* the fill bar's high is excluded from the favourable side and its low included in
  the adverse side, because a one-minute bar cannot say which came first and the
  optimistic reading is what turns a losing rule into a winning backtest;
* eligibility is decided by the *longest* horizon, so a short hold cannot look
  better merely by keeping end-of-session rows a long hold had to drop.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import data
from app.research.phase31.excursion import LONG, SHORT, _bars_left, _prepare, _shift
from app.research.phase33 import FUT_LEVELS, HOLD_GRID, MAX_HOLD

SIDES = (LONG, SHORT)

_LEVEL_INDEX = {round(x, 6): i for i, x in enumerate(FUT_LEVELS)}


def side_name(side: int) -> str:
    return "LONG" if side == LONG else "SHORT"


class Path:
    """Per-bar forward path arrays for one instrument and one side."""

    __slots__ = (
        "instrument", "side", "entry", "fav", "adv", "ret", "first_touch",
        "first_adverse", "peak_minute", "post_peak_low", "session", "minute",
        "bars_available", "eligible", "sess_ret", "sess_fav", "sess_adv",
        "sess_minutes", "ts_minute",
    )

    def __init__(self, instrument: str, side: int) -> None:
        self.instrument = instrument
        self.side = side

    def level_index(self, level_pct: float) -> int:
        """Row in ``first_touch`` for a distance, or raise.

        Raising rather than interpolating is deliberate: a distance outside the
        frozen grid would be an unfrozen hypothesis.
        """
        key = round(float(level_pct), 6)
        if key not in _LEVEL_INDEX:
            raise KeyError(f"{level_pct} is not a frozen move level")
        return _LEVEL_INDEX[key]


def _session_suffix(
    s: data.Series, sess: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Within-session suffix maximum high, suffix minimum low and last close.

    ``suf_hi[i]`` is the highest high from bar ``i`` to the end of ``i``'s own
    session. A hold to the close is then two lookups instead of another 800
    iterations of the main loop.
    """
    n = len(s)
    suf_hi = np.empty(n, dtype=np.float64)
    suf_lo = np.empty(n, dtype=np.float64)
    last_close = np.empty(n, dtype=np.float64)
    hi, lo, cl = s.high, s.low, s.close
    run_hi = -np.inf
    run_lo = np.inf
    run_close = np.nan
    for i in range(n - 1, -1, -1):
        if i == n - 1 or sess[i] != sess[i + 1]:
            run_hi, run_lo, run_close = -np.inf, np.inf, cl[i]
        run_hi = max(run_hi, float(hi[i]))
        run_lo = min(run_lo, float(lo[i]))
        suf_hi[i] = run_hi
        suf_lo[i] = run_lo
        last_close[i] = run_close
    return suf_hi, suf_lo, last_close


def build(s: data.Series, side: int) -> Path:
    """One forward pass over one instrument and one side."""
    n = len(s)
    sess, into = _prepare(s)
    sgn = 1.0 if side == LONG else -1.0
    p = Path(s.instrument, side)

    entry = _shift(s.open, 1, np.nan)
    # The fill bar contributes its adverse extreme only.
    fav_run = np.full(n, -np.inf)
    with np.errstate(invalid="ignore"):
        # Percentage, like every other excursion in this module: the fill bar's
        # adverse extreme is on the same axis as the later ones, so the running
        # minimum below compares like with like.
        adv_run = (
            100.0
            * sgn
            * (_shift(s.low if side == LONG else s.high, 1, np.nan) - entry)
            / entry
        )
    peak_minute = np.zeros(n, dtype=np.int16)
    post_peak_low = np.full(n, np.inf)

    nl = len(FUT_LEVELS)
    first_touch = np.zeros((nl, n), dtype=np.int16)
    first_adverse = np.zeros((nl, n), dtype=np.int16)
    fav: dict[int, np.ndarray] = {}
    adv: dict[int, np.ndarray] = {}
    ret: dict[int, np.ndarray] = {}

    sess_f = sess.astype(np.float64)
    for k in range(1, MAX_HOLD + 1):
        same = _shift(sess_f, 1 + k, -1.0) == sess_f
        hi = np.where(same, _shift(s.high, 1 + k, np.nan), np.nan)
        lo = np.where(same, _shift(s.low, 1 + k, np.nan), np.nan)
        with np.errstate(invalid="ignore"):
            # Signed excursions: favourable is positive, adverse is negative, so
            # both sides of one trade live on one axis and giveback is a
            # subtraction rather than a sign convention.
            fav_k = 100.0 * sgn * ((hi if side == LONG else lo) - entry) / entry
            adv_k = 100.0 * sgn * ((lo if side == LONG else hi) - entry) / entry
            new_peak = np.isfinite(fav_k) & (fav_k > fav_run)
            fav_run = np.fmax(fav_run, fav_k)
            peak_minute = np.where(new_peak, k, peak_minute).astype(np.int16)
            # The peak minute's own adverse extreme counts against it: within one
            # bar the order is unknown, and the pessimistic reading is the honest
            # one.
            post_peak_low = np.where(
                new_peak, adv_k, np.fmin(post_peak_low, adv_k)
            )
            adv_run = np.fmin(adv_run, adv_k)
            for g, lvl in enumerate(FUT_LEVELS):
                hit = (fav_run >= lvl) & (first_touch[g] == 0)
                first_touch[g][hit] = k
                hit = (adv_run <= -lvl) & (first_adverse[g] == 0)
                first_adverse[g][hit] = k
            if k in HOLD_GRID:
                cl = np.where(same, _shift(s.close, 1 + k, np.nan), np.nan)
                fav[k] = fav_run.astype(np.float32)
                adv[k] = adv_run.astype(np.float32)
                ret[k] = (100.0 * sgn * (cl - entry) / entry).astype(np.float32)

    suf_hi, suf_lo, last_close = _session_suffix(s, sess)
    # From the fill: the favourable side starts one bar later than the adverse
    # side, exactly as inside the loop.
    fav_src = _shift(suf_hi if side == LONG else suf_lo, 2, np.nan)
    adv_src = _shift(suf_lo if side == LONG else suf_hi, 1, np.nan)
    with np.errstate(invalid="ignore"):
        p.sess_fav = (100.0 * sgn * (fav_src - entry) / entry).astype(np.float32)
        p.sess_adv = (100.0 * sgn * (adv_src - entry) / entry).astype(np.float32)
        p.sess_ret = (
            100.0 * sgn * (_shift(last_close, 1, np.nan) - entry) / entry
        ).astype(np.float32)

    p.entry = entry
    p.fav, p.adv, p.ret = fav, adv, ret
    p.first_touch = first_touch
    p.first_adverse = first_adverse
    p.peak_minute = peak_minute
    p.post_peak_low = post_peak_low.astype(np.float32)
    p.session = sess
    p.minute = into
    # Minute-aligned decision timestamps, so a captured-window pool recorded to
    # the second can be joined to these bars without a fuzzy match.
    p.ts_minute = (np.asarray(s.ts, dtype=np.int64) // 60) * 60
    p.bars_available = _bars_left(sess)
    p.sess_minutes = np.minimum(p.bars_available, 100_000).astype(np.int32)
    p.eligible = (
        np.isfinite(entry)
        & np.isfinite(ret[MAX_HOLD])
        & (p.bars_available >= MAX_HOLD)
    )
    return p


def horizon_keys() -> tuple[int, ...]:
    return tuple(HOLD_GRID)


def reach_rate(p: Path, level_pct: float, horizon: int, mask: np.ndarray) -> float:
    """Fraction of the cohort whose favourable excursion reached a level in time."""
    ft = p.first_touch[p.level_index(level_pct)][mask]
    if ft.size == 0:
        return float("nan")
    return float(np.mean((ft > 0) & (ft <= horizon)))


def minutes_to(p: Path, level_pct: float, horizon: int, mask: np.ndarray) -> np.ndarray:
    """Minutes to first touch, for the rows that touched inside the horizon."""
    ft = p.first_touch[p.level_index(level_pct)][mask].astype(np.int32)
    return ft[(ft > 0) & (ft <= horizon)].astype(np.float64)


def adverse_first_rate(
    p: Path, level_pct: float, horizon: int, mask: np.ndarray
) -> float:
    """How often an adverse move of the same size arrived before the favourable.

    A tie inside one minute counts as adverse-first: a one-minute bar cannot say
    which extreme came first, and the optimistic reading is the one that flatters
    the result.
    """
    g = p.level_index(level_pct)
    ft = p.first_touch[g][mask].astype(np.int32)
    fa = p.first_adverse[g][mask].astype(np.int32)
    if ft.size == 0:
        return float("nan")
    fav_in = (ft > 0) & (ft <= horizon)
    adv_in = (fa > 0) & (fa <= horizon)
    return float(np.mean(adv_in & (~fav_in | (fa <= ft))))
