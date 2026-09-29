"""Exit families (§8/§26), resolved as date x symbol outcome matrices.

The key structural point: an outcome depends only on the fill cell, never on
which mechanism produced it. So every exit rule is resolved once for *every*
cell of the panel, and each entry mechanism's statistics are then a masked read
of those matrices. That is what makes 34 entries x 14 exits affordable without
approximating anything — the same forward walk is shared rather than repeated.

Resolution rules, all deliberately unfavourable where the daily bar is ambiguous:

* entry fills at ``open[t]``. No intraday path exists in daily data, so if a
  session's range contains both the stop and the target, the **stop** is taken.
  Assuming the good fill first is how a daily-bar backtest invents profit;
* a gap through the stop fills at that session's open, not at the stop price;
* a gap through the target also fills at the open — which is *favourable*, and is
  correct: the order would have executed there;
* a moving-average or time exit fills at that session's close;
* if the symbol stops trading before the exit condition occurs, the position is
  closed at the last close actually printed, labelled ``STOPPED_TRADING``. This
  is the survivorship-honest choice: a name that vanished is not a name that
  kept its entry price.
"""
from __future__ import annotations

import numpy as np

from . import EXIT_STOP, EXIT_TARGET, EXIT_TIME
from .panel import Panel

EXIT_MA = "MA_EXIT"
EXIT_TRAIL = "TRAIL_EXIT"
EXIT_PREV_LOW = "PREV_LOW_INVALIDATION"
EXIT_STOPPED_TRADING = "STOPPED_TRADING"
EXIT_UNRESOLVED = "UNRESOLVED_NO_FORWARD_DATA"

#: Reason codes, as small integers in the matrices.
REASONS = (
    EXIT_UNRESOLVED,
    EXIT_STOP,
    EXIT_TARGET,
    EXIT_TIME,
    EXIT_MA,
    EXIT_TRAIL,
    EXIT_PREV_LOW,
    EXIT_STOPPED_TRADING,
)
REASON_CODE = {name: code for code, name in enumerate(REASONS)}


def _up(matrix: np.ndarray, periods: int) -> np.ndarray:
    """Session ``t + periods`` aligned onto row ``t``."""
    out = np.full_like(matrix, np.nan)
    if periods == 0:
        return matrix.copy()
    out[:-periods] = matrix[periods:]
    return out


class Outcome:
    """Resolved forward outcome for every cell, for one exit configuration."""

    __slots__ = ("name", "config", "entry_price", "exit_price", "reason", "holding", "mfe", "mae")

    def __init__(self, name, config, entry_price, exit_price, reason, holding, mfe, mae):
        self.name = name
        self.config = config
        self.entry_price = entry_price
        self.exit_price = exit_price
        self.reason = reason
        self.holding = holding
        self.mfe = mfe
        self.mae = mae

    def gross_return(self) -> np.ndarray:
        with np.errstate(invalid="ignore", divide="ignore"):
            out = self.exit_price / self.entry_price - 1.0
        return np.where(self.reason > 0, out, np.nan)


def resolve(panel: Panel, config: dict) -> Outcome:
    """Walk every cell forward under one exit configuration."""
    hold = int(config["hold"])
    stop_atr = config.get("stop_atr")
    target_atr = config.get("target_atr")
    ma_exit = config.get("ma_exit")
    trail_atr = config.get("trail_atr")
    prev_low_stop = bool(config.get("prev_low_stop", False))

    entry = panel.open.astype(np.float64)
    entry = np.where(entry > 0, entry, np.nan)
    atr = panel.features["atr"].astype(np.float64)

    stop_level = entry - stop_atr * atr if stop_atr else np.full_like(entry, np.nan)
    if prev_low_stop:
        floor = panel.features["prev_low"].astype(np.float64)
        stop_level = np.fmin(stop_level, floor) if stop_atr else floor
    target_level = entry + target_atr * atr if target_atr else np.full_like(entry, np.nan)

    shape = entry.shape
    exit_price = np.full(shape, np.nan)
    reason = np.zeros(shape, dtype=np.int8)
    holding = np.zeros(shape, dtype=np.int16)
    peak = np.where(np.isfinite(entry), entry, np.nan)
    trough = np.where(np.isfinite(entry), entry, np.nan)
    highest_close = np.where(np.isfinite(entry), entry, np.nan)
    last_close = np.full(shape, np.nan)
    bars_traded = np.zeros(shape, dtype=np.int16)
    open_position = np.isfinite(entry)

    moving_average = (
        _rolling_mean_forward(panel.close, int(ma_exit)) if ma_exit else None
    )

    for step in range(hold):
        if not open_position.any():
            break
        high = _up(panel.high.astype(np.float64), step)
        low = _up(panel.low.astype(np.float64), step)
        close = _up(panel.close.astype(np.float64), step)
        day_open = _up(panel.open.astype(np.float64), step)
        traded = open_position & np.isfinite(close) & (close > 0)

        peak = np.where(traded, np.fmax(peak, high), peak)
        trough = np.where(traded, np.fmin(trough, low), trough)
        last_close = np.where(traded, close, last_close)
        bars_traded = np.where(traded, step + 1, bars_traded)

        # 1. stop / invalidation, taken before any favourable event
        if stop_atr or prev_low_stop:
            hit = traded & np.isfinite(stop_level) & (low <= stop_level)
            fill = np.where(day_open <= stop_level, day_open, stop_level)
            code = REASON_CODE[EXIT_PREV_LOW if prev_low_stop and not stop_atr else EXIT_STOP]
            exit_price = np.where(hit, fill, exit_price)
            reason = np.where(hit, code, reason)
            holding = np.where(hit, step + 1, holding)
            open_position &= ~hit
            traded &= ~hit

        # 2. trailing ratchet from the highest close reached so far
        if trail_atr:
            trail = highest_close - trail_atr * atr
            hit = traded & np.isfinite(trail) & (low <= trail)
            fill = np.where(day_open <= trail, day_open, trail)
            exit_price = np.where(hit, fill, exit_price)
            reason = np.where(hit, REASON_CODE[EXIT_TRAIL], reason)
            holding = np.where(hit, step + 1, holding)
            open_position &= ~hit
            traded &= ~hit
            highest_close = np.where(traded, np.fmax(highest_close, close), highest_close)

        # 3. target
        if target_atr:
            hit = traded & np.isfinite(target_level) & (high >= target_level)
            fill = np.where(day_open >= target_level, day_open, target_level)
            exit_price = np.where(hit, fill, exit_price)
            reason = np.where(hit, REASON_CODE[EXIT_TARGET], reason)
            holding = np.where(hit, step + 1, holding)
            open_position &= ~hit
            traded &= ~hit

        # 4. moving-average exit on the close
        if moving_average is not None:
            average = _up(moving_average, step)
            hit = traded & np.isfinite(average) & (close < average)
            exit_price = np.where(hit, close, exit_price)
            reason = np.where(hit, REASON_CODE[EXIT_MA], reason)
            holding = np.where(hit, step + 1, holding)
            open_position &= ~hit
            traded &= ~hit

        # 5. time exit on the last session of the holding window
        if step == hold - 1:
            hit = traded
            exit_price = np.where(hit, close, exit_price)
            reason = np.where(hit, REASON_CODE[EXIT_TIME], reason)
            holding = np.where(hit, step + 1, holding)
            open_position &= ~hit

    # Positions still open fall into two very different cases, and collapsing
    # them would bias the last sessions of every partition — the holdout most of
    # all, since it ends at the dataset's final session:
    #
    # * the holding window runs past the dataset's last session. Nothing is known
    #   about the outcome, so the trade is UNRESOLVED and takes no exit price;
    # * the window fits inside the dataset but the symbol stopped printing. That
    #   is a real event and is marked at the last close actually printed.
    days = shape[0]
    required_end = np.arange(days, dtype=np.int64)[:, None] + hold - 1
    truncated = open_position & (required_end > days - 1)
    stranded = open_position & ~truncated & np.isfinite(last_close)
    exit_price = np.where(stranded, last_close, exit_price)
    reason = np.where(stranded, REASON_CODE[EXIT_STOPPED_TRADING], reason)
    holding = np.where(stranded, np.maximum(bars_traded, 1), holding)
    open_position &= ~stranded
    reason = np.where(open_position, REASON_CODE[EXIT_UNRESOLVED], reason)
    exit_price = np.where(open_position, np.nan, exit_price)
    holding = np.where(open_position, 0, holding)

    with np.errstate(invalid="ignore", divide="ignore"):
        mfe = peak / entry - 1.0
        mae = trough / entry - 1.0
    return Outcome(
        name=config["name"],
        config=dict(config),
        entry_price=entry,
        exit_price=exit_price,
        reason=reason,
        holding=holding,
        mfe=np.where(reason > 0, mfe, np.nan),
        mae=np.where(reason > 0, mae, np.nan),
    )


def _rolling_mean_forward(close: np.ndarray, window: int) -> np.ndarray:
    """Trailing mean including the current session, for use as an exit level.

    An exit may legitimately use the session it exits on — the decision is taken
    at that close, after it printed. That is the one place a current-session bar
    is allowed, and only for exits, never for entries.
    """
    from .panel import _nanmean, _rolling

    return _rolling(close.astype(np.float32), window, _nanmean).astype(np.float64)
