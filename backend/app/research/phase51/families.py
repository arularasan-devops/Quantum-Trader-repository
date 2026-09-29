"""Phase 51 §4/§5 — the mechanism families, as bounded rule generators.

A family is a named generator of **entry rules**. Each rule is a boolean mask
over the bars plus a side and a sentence a human can read. Nothing here decides
whether a rule is any good; that is the search's job, and most of what is
generated here will be refused.

Two design rules that matter more than the families themselves:

* **the grid is declared, then enumerated in full.** §4 asks for five
  penetration thresholds, four confirmations and three entry timings, and all
  sixty combinations of them are generated and counted. Generating all and
  reporting the best one is cherry-picking; generating all, counting all and
  correcting against the count is not. The count is the FDR denominator;
* **every rule is causal by construction.** A rule may only read the geometry
  arrays, which are already causal, and the confirmation and retest legs are
  built from *past* bars relative to the decision bar. A "break, retest and
  continue" rule triggers on the bar where the continuation is visible, not on
  the break that a later bar revealed to have been retested.

The wick-rejection, failure-reversal and mean-reversion members are the same
level events read in the opposite direction. They are separate hypotheses and
are counted separately, because "the level holds" and "the level breaks" cannot
both be someone's edge and any engine that reports both as winners has found
noise twice.
"""
from __future__ import annotations

from collections.abc import Iterator

import numpy as np

LONG, SHORT = 1, -1

# ------------------------------------------------------------------ §4 grids
# Penetration beyond the level before the event counts, in ATR.
PENETRATION_ATR = (0.0, 0.05, 0.10, 0.25, 0.50)
# What confirms the penetration.
CONFIRM_TOUCH = "touch"
CONFIRM_1M = "close_1m"
CONFIRM_5M = "close_5m"
CONFIRM_15M = "close_15m"
CONFIRMATIONS = (CONFIRM_TOUCH, CONFIRM_1M, CONFIRM_5M, CONFIRM_15M)
CONFIRM_BARS = {CONFIRM_TOUCH: 0, CONFIRM_1M: 1, CONFIRM_5M: 5, CONFIRM_15M: 15}
# When the decision is taken relative to the confirmed event.
TIMING_IMMEDIATE = "immediate_next_bar"
TIMING_CONFIRMED = "after_confirmation"
TIMING_RETEST = "after_retest"
TIMINGS = (TIMING_IMMEDIATE, TIMING_CONFIRMED, TIMING_RETEST)
# Session windows the event must fall in, in minutes since that session's open.
WINDOW_ALL = (0, 10_000)
WINDOW_OPEN = (0, 60)
WINDOW_MID = (60, 240)
WINDOW_LATE = (240, 10_000)
SESSION_WINDOWS = {
    "whole_session": WINDOW_ALL,
    "first_hour": WINDOW_OPEN,
    "midday": WINDOW_MID,
    "late_session": WINDOW_LATE,
}

# The retest tolerance and the continuation/reversion legs, frozen.
RETEST_TOLERANCE_ATR = 0.10
RETEST_LOOKBACK_BARS = 30
CONTINUATION_ATR = 0.50
REVERSION_ATR = 0.25

# §5's family names. Kept as constants so a report cannot invent a family that
# the generator does not implement.
F_PREV_DAY_LEVELS = "PREVIOUS_DAY_LEVELS"
F_OPENING_RANGE = "OPENING_RANGE_BREAKOUT"
F_GAP_CONTINUATION = "GAP_CONTINUATION"
F_GAP_FILL = "GAP_FILL"
F_MOMENTUM_CONT = "MOMENTUM_CONTINUATION"
F_MOMENTUM_EXHAUST = "MOMENTUM_EXHAUSTION"
F_FAILED_BREAKOUT = "FAILED_BREAKOUT_REVERSAL"
F_MEAN_REVERSION = "MEAN_REVERSION_AFTER_EXTREME"
F_VOL_EXPANSION = "VOLATILITY_EXPANSION_AFTER_COMPRESSION"
F_VOL_CONTRACTION = "VOLATILITY_CONTRACTION"
F_VWAP = "VWAP_DISPLACEMENT_REVERSION"
F_TREND_EXPANSION = "TREND_PLUS_RANGE_EXPANSION"
F_TREND_FAILURE = "TREND_FAILURE"
F_TIME_OF_DAY = "TIME_OF_DAY"
F_DAY_TYPE = "DAY_TYPE"
F_RELATIVE_VALUE = "RELATIVE_VALUE"
F_VEHICLE = "VEHICLE_SELECTION"
F_OPTION_SIDE = "OPTION_CE_VS_PE"
F_PROFIT_CAPTURE = "PROFIT_CAPTURE"

# Families this engine can measure on candle data, and the ones it cannot.
# The second list is not searched: it is reported as needing data, which is a
# fact about the store and not a result about the market.
MEASURABLE_FAMILIES = (
    F_PREV_DAY_LEVELS, F_OPENING_RANGE, F_GAP_CONTINUATION, F_GAP_FILL,
    F_MOMENTUM_CONT, F_MOMENTUM_EXHAUST, F_FAILED_BREAKOUT, F_MEAN_REVERSION,
    F_VOL_EXPANSION, F_VOL_CONTRACTION, F_VWAP, F_TREND_EXPANSION,
    F_TREND_FAILURE, F_TIME_OF_DAY, F_DAY_TYPE,
)
UNMEASURABLE_FAMILIES = {
    F_RELATIVE_VALUE: (
        "a pair relationship needs two synchronised series at the same instant; "
        "the stored files are per-instrument and not time-aligned bar for bar"
    ),
    F_VEHICLE: (
        "choosing a vehicle needs the option book that the alternative would "
        "have been filled in, and a candle has no premium"
    ),
    F_OPTION_SIDE: (
        "a CE-versus-PE comparison needs two executable premiums at one "
        "instant; no historical option book exists in the store"
    ),
}

# §4's eight members, named so the report can show which geometry was tested.
BREAKOUT_MEMBERS = (
    "A_PREV_HIGH_BREAKOUT",
    "B_PREV_LOW_BREAKOUT",
    "C_WICK_REJECTION",
    "D_BREAK_AND_CLOSE_BEYOND",
    "E_BREAK_RETEST_CONTINUATION",
    "F_BREAK_FAILURE_REVERSAL",
    "G_BREAKOUT_CONTINUATION",
    "H_BREAKOUT_MEAN_REVERSION",
)


class Rule:
    """One entry hypothesis: a mask, a side, a family, and a readable sentence."""

    __slots__ = ("rule_id", "family", "member", "side", "mask", "text", "params")

    def __init__(self, rule_id: str, family: str, member: str, side: int,
                 mask: np.ndarray, text: str, params: dict) -> None:
        self.rule_id = rule_id
        self.family = family
        self.member = member
        self.side = int(side)
        self.mask = mask
        self.text = text
        self.params = params

    def __repr__(self) -> str:
        return f"<Rule {self.rule_id} n={int(self.mask.sum())}>"


def _shift(a: np.ndarray, k: int, fill: float = np.nan) -> np.ndarray:
    """``a`` moved forward by ``k`` bars: value at ``i`` is ``a[i-k]``."""
    if k <= 0:
        return a
    out = np.full(a.size, fill, dtype=np.float64)
    if k < a.size:
        out[k:] = a[:-k]
    return out


def _rolling_any(mask: np.ndarray, win: int) -> np.ndarray:
    """Did ``mask`` hold on any of the ``win`` bars ending at each index."""
    if mask.size == 0 or win <= 1:
        return mask.astype(bool)
    c = np.concatenate(([0], np.cumsum(mask.astype(np.int64))))
    idx = np.arange(mask.size)
    lo = np.maximum(0, idx - win + 1)
    return (c[idx + 1] - c[lo]) > 0


def _same_session(f: dict[str, np.ndarray], k: int) -> np.ndarray:
    """Is the bar ``k`` back inside the same session as this bar."""
    return _shift(f["session"], k, fill=-1.0) == f["session"]


def _in_window(f: dict[str, np.ndarray], window: tuple[int, int]) -> np.ndarray:
    lo, hi = window
    t = f["time_since_open"]
    return (t >= lo) & (t < hi)


def _finite(*arrays: np.ndarray) -> np.ndarray:
    out = np.isfinite(arrays[0])
    for a in arrays[1:]:
        out = out & np.isfinite(a)
    return out


# ------------------------------------------------------------------- §4 family
def _penetrated(close: np.ndarray, level: np.ndarray, atr: np.ndarray,
                pen: float, side: int) -> np.ndarray:
    """Is price beyond the level by at least ``pen`` ATR, in ``side``'s direction."""
    edge = level + side * pen * atr
    return (close > edge) if side == LONG else (close < edge)


def _touched(high: np.ndarray, low: np.ndarray, level: np.ndarray,
             atr: np.ndarray, pen: float, side: int) -> np.ndarray:
    edge = level + side * pen * atr
    return (high > edge) if side == LONG else (low < edge)


def _confirmed(close: np.ndarray, high: np.ndarray, low: np.ndarray,
               level: np.ndarray, atr: np.ndarray, pen: float, side: int,
               confirm: str, f: dict[str, np.ndarray]) -> np.ndarray:
    """The confirmed-event mask for one penetration and one confirmation rule.

    ``touch`` is the bar's extreme crossing the edge. The close confirmations
    require the close beyond the edge to have held for the whole confirmation
    window, inside one session — a 15-minute confirmation that borrows the
    previous session's closes is not a confirmation.
    """
    if confirm == CONFIRM_TOUCH:
        return _touched(high, low, level, atr, pen, side)
    bars = CONFIRM_BARS[confirm]
    held = _penetrated(close, level, atr, pen, side)
    out = held.copy()
    for k in range(1, bars):
        out = out & _shift(held.astype(np.float64), k, fill=0.0).astype(bool) \
            & _same_session(f, k)
    return out


def _retested(f: dict[str, np.ndarray], level: np.ndarray, atr: np.ndarray,
              side: int) -> np.ndarray:
    """Has price come back to within tolerance of the level since breaking it.

    Read strictly backwards: the return must have happened on a bar at or before
    this one, and the break must have happened before the return. A retest
    identified from a later bar's behaviour is a look-ahead worth several ATR.
    """
    tol = RETEST_TOLERANCE_ATR * atr
    back_at_level = (np.abs(f["close"] - level) <= tol)
    returned = _rolling_any(back_at_level & np.isfinite(tol), RETEST_LOOKBACK_BARS)
    beyond_now = _penetrated(f["close"], level, atr, 0.0, side)
    return returned & beyond_now


def _first_bar_of(event: np.ndarray, f: dict[str, np.ndarray]) -> np.ndarray:
    """The bar an event *begins* on, never a bar it merely continues through.

    Without this, "enter immediately" and "enter while the condition holds" are
    the same mask, and a rule that fires on two hundred consecutive bars of one
    breakout is counted as two hundred independent trades.
    """
    prev = _shift(event.astype(np.float64), 1, fill=0.0).astype(bool)
    return event & ~(prev & _same_session(f, 1))


def previous_day_level_rules(f: dict[str, np.ndarray]) -> Iterator[Rule]:
    """§4 A-H over both previous-day levels, the full declared grid.

    Lazily generated. The grid is a few thousand masks over half a million bars
    and materialising it would cost more memory than the whole study; the search
    consumes one rule at a time and keeps only its summary.
    """
    close, high, low = f["close"], f["high"], f["low"]
    atr = f["atr"]
    scale = np.where(atr > 0, atr, np.nan)
    levels = {
        "previous_day_high": (f["prev_high"], LONG, "high"),
        "previous_day_low": (f["prev_low"], SHORT, "low"),
    }
    for level_name, (level, break_side, tag) in levels.items():
        ok = _finite(level, atr, close)
        beyond = (close - level) / scale * break_side
        for pen in PENETRATION_ATR:
            touched = _touched(high, low, level, atr, pen, break_side)
            penetrated = _penetrated(close, level, atr, pen, break_side)
            # C and F read the level, not the confirmation grid, so they are
            # generated once per level and threshold. Repeating an identical
            # mask under twelve confirmation labels would pad the trial count
            # with hypotheses the engine never actually varied.
            rejected = touched & ~penetrated & ok
            broke_recently = _rolling_any(penetrated, RETEST_LOOKBACK_BARS)
            failed = (broke_recently
                      & ~_penetrated(close, level, atr, 0.0, break_side) & ok)
            for win_name, window in SESSION_WINDOWS.items():
                in_win = _in_window(f, window)
                base = {"level": level_name, "penetration_atr": pen,
                        "session_window": win_name}
                yield Rule(
                    f"{F_PREV_DAY_LEVELS}|C_WICK_REJECTION|{level_name}|"
                    f"pen{pen:g}|{win_name}",
                    F_PREV_DAY_LEVELS, "C_WICK_REJECTION", -break_side,
                    _first_bar_of(rejected, f) & in_win,
                    f"price wicks {pen:g} ATR through the previous day's {tag} "
                    f"and closes back inside, taken against the break, "
                    f"{win_name}", dict(base))
                yield Rule(
                    f"{F_PREV_DAY_LEVELS}|F_BREAK_FAILURE_REVERSAL|{level_name}|"
                    f"pen{pen:g}|{win_name}",
                    F_PREV_DAY_LEVELS, "F_BREAK_FAILURE_REVERSAL", -break_side,
                    _first_bar_of(failed, f) & in_win,
                    f"price broke the previous day's {tag} by {pen:g} ATR within "
                    f"{RETEST_LOOKBACK_BARS} bars and has closed back inside it, "
                    f"taken against the break, {win_name}", dict(base))

            for confirm in CONFIRMATIONS:
                event = _confirmed(close, high, low, level, atr, pen,
                                   break_side, confirm, f) & ok
                first = _first_bar_of(event, f)
                retest = _retested(f, level, atr, break_side)
                for timing in TIMINGS:
                    # Three genuinely different decision bars, not three labels
                    # on one mask: the bar the event begins on, any bar while it
                    # holds, and the bar the level is regained on after a return.
                    if timing == TIMING_IMMEDIATE:
                        ready = first
                    elif timing == TIMING_CONFIRMED:
                        ready = event
                    else:
                        ready = _first_bar_of(event & retest, f)
                    for win_name, window in SESSION_WINDOWS.items():
                        in_win = _in_window(f, window)
                        mask = ready & in_win
                        params = {
                            "level": level_name, "penetration_atr": pen,
                            "confirmation": confirm, "timing": timing,
                            "session_window": win_name,
                        }
                        stem = (f"{F_PREV_DAY_LEVELS}|%s|{level_name}|pen{pen:g}|"
                                f"{confirm}|{timing}|{win_name}")
                        member = ("A_PREV_HIGH_BREAKOUT" if break_side == LONG
                                  else "B_PREV_LOW_BREAKOUT")
                        yield Rule(
                            stem % member, F_PREV_DAY_LEVELS, member,
                            break_side, mask,
                            f"price breaks the previous day's {tag} by {pen:g} "
                            f"ATR, confirmed by {confirm}, entered {timing}, "
                            f"{win_name}", dict(params))
                        yield Rule(
                            stem % "D_BREAK_AND_CLOSE_BEYOND",
                            F_PREV_DAY_LEVELS, "D_BREAK_AND_CLOSE_BEYOND",
                            break_side, mask & penetrated,
                            f"price breaks the previous day's {tag} and closes "
                            f"beyond it by {pen:g} ATR, confirmed by {confirm}, "
                            f"entered {timing}, {win_name}", dict(params))
                        yield Rule(
                            stem % "E_BREAK_RETEST_CONTINUATION",
                            F_PREV_DAY_LEVELS, "E_BREAK_RETEST_CONTINUATION",
                            break_side, mask & retest,
                            f"price breaks the previous day's {tag}, returns to "
                            f"within {RETEST_TOLERANCE_ATR:g} ATR of it, and is "
                            f"beyond it again, confirmed by {confirm}, "
                            f"{win_name}", dict(params))
                        yield Rule(
                            stem % "G_BREAKOUT_CONTINUATION",
                            F_PREV_DAY_LEVELS, "G_BREAKOUT_CONTINUATION",
                            break_side, mask & (beyond >= CONTINUATION_ATR),
                            f"price is at least {CONTINUATION_ATR:g} ATR beyond "
                            f"the previous day's {tag} after a break confirmed "
                            f"by {confirm}, taken with the move, {win_name}",
                            dict(params))
                        # G and H are one event read two ways, deliberately. At
                        # most one of them can be an edge, and an engine that
                        # reports both as positive has found noise twice.
                        yield Rule(
                            stem % "H_BREAKOUT_MEAN_REVERSION",
                            F_PREV_DAY_LEVELS, "H_BREAKOUT_MEAN_REVERSION",
                            -break_side, mask & (beyond >= REVERSION_ATR),
                            f"price is at least {REVERSION_ATR:g} ATR beyond the "
                            f"previous day's {tag} after a break confirmed by "
                            f"{confirm}, taken against the move, {win_name}",
                            dict(params))


# ------------------------------------------------------------------- §5 families
# Thresholds are declared as small grids, in ATR or percentile units so one
# number means the same thing on every instrument.
GAP_ATR = (0.5, 1.0, 2.0)
MOMENTUM_ATR = (1.0, 1.5, 2.5)
EXTREME_ATR = (2.0, 3.0, 4.0)
COMPRESSION_RATIO = (0.6, 0.8)
EXPANSION_RATIO = (1.3, 1.8)
RANGE_PCTL_LOW = (10.0, 25.0)
RANGE_PCTL_HIGH = (75.0, 90.0)
VWAP_ATR = (1.0, 2.0, 3.0)
PREV_RANGE_PCTL = ((0.0, 33.0), (33.0, 66.0), (66.0, 100.1))
RETURN_WINDOWS_SEARCHED = (5, 15, 30, 60)


def _yield(rules: list[Rule], family: str, member: str, side: int,
           mask: np.ndarray, text: str, params: dict, f: dict[str, np.ndarray],
           window: str = "whole_session") -> None:
    """Register one §5 rule, firing on the condition's onset only.

    "price is above VWAP by one ATR" holds for hundreds of consecutive bars.
    Counting each of them as a trade would report ten thousand trades that are
    really one observation, and every interval and p-value computed from that
    count would be wrong by the same factor. Onset only, for every family.
    """
    rid = f"{family}|{member}|" + "|".join(
        f"{k}{v}" for k, v in sorted(params.items())
    ) + f"|{window}"
    rules.append(Rule(
        rid, family, member, side,
        _first_bar_of(mask, f) & _in_window(f, SESSION_WINDOWS[window]),
        text, dict(params, session_window=window),
    ))


def other_family_rules(f: dict[str, np.ndarray]) -> list[Rule]:
    """§5's measurable families, each as its own declared grid."""
    rules: list[Rule] = []
    close, atr = f["close"], f["atr"]
    scale = np.where(atr > 0, atr, np.nan)
    ok = _finite(close, atr)

    # 2 — opening-range breakout, every declared opening window.
    for win in (5, 15, 30, 60):
        hi = f[f"opening_range_{win}m_high"]
        lo = f[f"opening_range_{win}m_low"]
        for pen in PENETRATION_ATR:
            for side, level, tag in ((LONG, hi, "high"), (SHORT, lo, "low")):
                mask = _penetrated(close, level, atr, pen, side) & ok & np.isfinite(level)
                _yield(rules, F_OPENING_RANGE, f"OR{win}M_BREAK_{tag.upper()}",
                       side, mask,
                       f"price closes {pen:g} ATR beyond the {win}-minute "
                       f"opening range {tag}", {"window_m": win, "pen": pen}, f)

    # 3 / 4 — gap continuation and gap fill, off the same measured gap.
    gap_atr = f["opening_gap"] / scale
    for thr in GAP_ATR:
        up = (gap_atr >= thr) & ok
        down = (gap_atr <= -thr) & ok
        _yield(rules, F_GAP_CONTINUATION, "GAP_UP_CONTINUATION", LONG, up,
               f"the session gapped up by at least {thr:g} ATR, taken with the gap",
               {"gap_atr": thr}, f, "first_hour")
        _yield(rules, F_GAP_CONTINUATION, "GAP_DOWN_CONTINUATION", SHORT, down,
               f"the session gapped down by at least {thr:g} ATR, taken with the gap",
               {"gap_atr": thr}, f, "first_hour")
        # A fill is the gap closing back toward yesterday's close, so the trade
        # is against the gap and only while the gap is still open.
        still_open_up = up & (close > f["prev_close"])
        still_open_down = down & (close < f["prev_close"])
        _yield(rules, F_GAP_FILL, "GAP_UP_FILL", SHORT, still_open_up,
               f"the session gapped up by at least {thr:g} ATR and price is "
               f"still above yesterday's close, taken toward the close",
               {"gap_atr": thr}, f, "first_hour")
        _yield(rules, F_GAP_FILL, "GAP_DOWN_FILL", LONG, still_open_down,
               f"the session gapped down by at least {thr:g} ATR and price is "
               f"still below yesterday's close, taken toward the close",
               {"gap_atr": thr}, f, "first_hour")

    # 5 / 6 — momentum continuation and exhaustion, same events, both readings.
    for win in RETURN_WINDOWS_SEARCHED:
        r = f[f"return_{win}m_atr"]
        for thr in MOMENTUM_ATR:
            up = (r >= thr) & ok
            down = (r <= -thr) & ok
            _yield(rules, F_MOMENTUM_CONT, f"UP_{win}M_CONTINUATION", LONG, up,
                   f"price has risen at least {thr:g} ATR over {win} minutes, "
                   f"taken with the move", {"win_m": win, "atr": thr}, f)
            _yield(rules, F_MOMENTUM_CONT, f"DOWN_{win}M_CONTINUATION", SHORT, down,
                   f"price has fallen at least {thr:g} ATR over {win} minutes, "
                   f"taken with the move", {"win_m": win, "atr": thr}, f)
            _yield(rules, F_MOMENTUM_EXHAUST, f"UP_{win}M_EXHAUSTION", SHORT, up,
                   f"price has risen at least {thr:g} ATR over {win} minutes, "
                   f"taken against the move", {"win_m": win, "atr": thr}, f)
            _yield(rules, F_MOMENTUM_EXHAUST, f"DOWN_{win}M_EXHAUSTION", LONG, down,
                   f"price has fallen at least {thr:g} ATR over {win} minutes, "
                   f"taken against the move", {"win_m": win, "atr": thr}, f)

    # 7 — failed breakout of the session's own extreme.
    made_high = _rolling_any(close >= f["session_high"], 15)
    made_low = _rolling_any(close <= f["session_low"], 15)
    for thr in (0.5, 1.0):
        back_off_high = made_high & (
            (f["session_high"] - close) / scale >= thr) & ok
        back_off_low = made_low & (
            (close - f["session_low"]) / scale >= thr) & ok
        _yield(rules, F_FAILED_BREAKOUT, "SESSION_HIGH_FAILURE", SHORT,
               back_off_high,
               f"price made the session high within 15 bars and has given back "
               f"{thr:g} ATR from it", {"giveback_atr": thr}, f)
        _yield(rules, F_FAILED_BREAKOUT, "SESSION_LOW_FAILURE", LONG,
               back_off_low,
               f"price made the session low within 15 bars and has recovered "
               f"{thr:g} ATR from it", {"giveback_atr": thr}, f)

    # 8 — mean reversion after an extreme move from the session open.
    from_open = f["distance_from_day_open_atr"]
    for thr in EXTREME_ATR:
        _yield(rules, F_MEAN_REVERSION, "EXTREME_UP_REVERSION", SHORT,
               (from_open >= thr) & ok,
               f"price is at least {thr:g} ATR above the session open, taken "
               f"toward it", {"extreme_atr": thr}, f)
        _yield(rules, F_MEAN_REVERSION, "EXTREME_DOWN_REVERSION", LONG,
               (from_open <= -thr) & ok,
               f"price is at least {thr:g} ATR below the session open, taken "
               f"toward it", {"extreme_atr": thr}, f)

    # 9 / 10 — volatility expansion after compression, and contraction itself.
    vr = f["volatility_ratio"]
    for comp in COMPRESSION_RATIO:
        compressed = _rolling_any((vr <= comp) & np.isfinite(vr), 30)
        for exp in EXPANSION_RATIO:
            expanded = compressed & (f["range_expansion"] >= exp) & ok
            for side in (LONG, SHORT):
                tag = "LONG" if side == LONG else "SHORT"
                _yield(rules, F_VOL_EXPANSION, f"COMPRESSION_BREAK_{tag}", side,
                       expanded & (
                           (close > f["current_day_open"]) if side == LONG
                           else (close < f["current_day_open"])),
                       f"the fast ATR fell to {comp:g} of the slow ATR within 30 "
                       f"bars and this bar's range is {exp:g} ATR, taken in the "
                       f"direction of the session's move so far",
                       {"compression": comp, "expansion": exp}, f)
    for pctl in RANGE_PCTL_LOW:
        quiet = (f["range_percentile"] <= pctl) & ok
        for side in (LONG, SHORT):
            tag = "LONG" if side == LONG else "SHORT"
            _yield(rules, F_VOL_CONTRACTION, f"QUIET_RANGE_{tag}", side, quiet,
                   f"this bar's range sits in the bottom {pctl:g}th percentile "
                   f"of its trailing window", {"range_pctl": pctl}, f)

    # 11 — VWAP displacement and reversion.
    disp = f["distance_from_vwap"] / scale
    for thr in VWAP_ATR:
        _yield(rules, F_VWAP, "ABOVE_VWAP_REVERSION", SHORT, (disp >= thr) & ok,
               f"price is at least {thr:g} ATR above the session VWAP, taken "
               f"toward it", {"vwap_atr": thr}, f)
        _yield(rules, F_VWAP, "BELOW_VWAP_REVERSION", LONG, (disp <= -thr) & ok,
               f"price is at least {thr:g} ATR below the session VWAP, taken "
               f"toward it", {"vwap_atr": thr}, f)
        _yield(rules, F_VWAP, "ABOVE_VWAP_CONTINUATION", LONG, (disp >= thr) & ok,
               f"price is at least {thr:g} ATR above the session VWAP, taken "
               f"away from it", {"vwap_atr": thr}, f)
        _yield(rules, F_VWAP, "BELOW_VWAP_CONTINUATION", SHORT, (disp <= -thr) & ok,
               f"price is at least {thr:g} ATR below the session VWAP, taken "
               f"away from it", {"vwap_atr": thr}, f)

    # 12 / 13 — trend with range expansion, and trend failure.
    trend_up = f["return_30m_atr"] >= 1.0
    trend_down = f["return_30m_atr"] <= -1.0
    for exp in EXPANSION_RATIO:
        wide = f["range_expansion"] >= exp
        _yield(rules, F_TREND_EXPANSION, "UPTREND_EXPANSION", LONG,
               trend_up & wide & ok,
               f"price is up more than 1 ATR over 30 minutes and this bar's "
               f"range is {exp:g} ATR", {"expansion": exp}, f)
        _yield(rules, F_TREND_EXPANSION, "DOWNTREND_EXPANSION", SHORT,
               trend_down & wide & ok,
               f"price is down more than 1 ATR over 30 minutes and this bar's "
               f"range is {exp:g} ATR", {"expansion": exp}, f)
    _yield(rules, F_TREND_FAILURE, "UPTREND_LOSES_VWAP", SHORT,
           trend_up & (f["distance_from_vwap"] < 0) & ok,
           "price is up more than 1 ATR over 30 minutes but has closed back "
           "below the session VWAP", {}, f)
    _yield(rules, F_TREND_FAILURE, "DOWNTREND_LOSES_VWAP", LONG,
           trend_down & (f["distance_from_vwap"] > 0) & ok,
           "price is down more than 1 ATR over 30 minutes but has closed back "
           "above the session VWAP", {}, f)

    # 14 — time of day, on its own, as the control the other families need. If a
    # bare time bucket pays as well as a mechanism, the mechanism is the time.
    for name, window in SESSION_WINDOWS.items():
        if name == "whole_session":
            continue
        for side in (LONG, SHORT):
            tag = "LONG" if side == LONG else "SHORT"
            _yield(rules, F_TIME_OF_DAY, f"BARE_WINDOW_{tag}", side, ok,
                   f"no condition beyond the session window itself ({name})",
                   {}, f, name)

    # 15 — day type, from yesterday's range percentile. Yesterday's character is
    # knowable at today's open; today's is not.
    pctl = f["prev_range_percentile"]
    for lo, hi in PREV_RANGE_PCTL:
        band = (pctl >= lo) & (pctl < hi) & ok
        for side in (LONG, SHORT):
            tag = "LONG" if side == LONG else "SHORT"
            _yield(rules, F_DAY_TYPE, f"PREV_RANGE_{int(lo)}_{int(hi)}_{tag}",
                   side, band & (f["return_15m_atr"] >= 0.5 if side == LONG
                                 else f["return_15m_atr"] <= -0.5),
                   f"yesterday's range sat between the {lo:g}th and {hi:g}th "
                   f"percentile of the trailing 60 sessions, and price has moved "
                   f"half an ATR in 15 minutes", {"pctl_lo": lo, "pctl_hi": hi}, f)
    return rules


def all_rules(f: dict[str, np.ndarray]) -> Iterator[Rule]:
    """Every generated hypothesis, §4 first then §5, one at a time.

    An iterator rather than a list: the full grid is several thousand boolean
    masks over half a million bars, and holding them all at once would cost
    gigabytes for no benefit — the search reads each rule exactly once.
    """
    yield from previous_day_level_rules(f)
    yield from other_family_rules(f)


def grid_size() -> dict:
    """The declared hypothesis budget, computable before any data is read.

    Printed in the pre-registration so the number of trials is a commitment
    rather than a number discovered after the search decided what to keep.
    """
    # Five of the eight members vary with the confirmation and timing grid; the
    # wick rejection and the failed break read the level alone, so they are
    # enumerated once per level, threshold and window instead of repeating one
    # identical mask under twelve labels.
    levels = 2
    event_members = 5                # A/B, D, E, G, H
    level_only_members = 2           # C, F
    prev_day = (
        levels * len(PENETRATION_ATR) * len(CONFIRMATIONS) * len(TIMINGS)
        * len(SESSION_WINDOWS) * event_members
        + levels * len(PENETRATION_ATR) * len(SESSION_WINDOWS)
        * level_only_members
    )
    return {
        "previous_day_family": prev_day,
        "penetration_thresholds": len(PENETRATION_ATR),
        "confirmations": len(CONFIRMATIONS),
        "timings": len(TIMINGS),
        "session_windows": len(SESSION_WINDOWS),
        "breakout_members": len(BREAKOUT_MEMBERS),
        "measurable_families": len(MEASURABLE_FAMILIES),
        "unmeasurable_families": {k: v for k, v in UNMEASURABLE_FAMILIES.items()},
    }
