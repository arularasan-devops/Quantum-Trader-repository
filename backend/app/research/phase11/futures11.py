"""Phase 11 §2 — the futures branch. RESEARCH ONLY. No order path.

The question this answers is narrow and it is the one that matters before any
vehicle choice can be argued: **when the direction was right, would the futures
contract have paid better than the option?** Four sessions of evidence say the
option book loses to its own bid/ask far more often than to the market, and a
futures contract has a different cost structure — a much tighter book in points,
no theta, no strike, but statutory charges on the full notional and an unbounded
loss if the stop is not honoured.

So this evaluates a futures trade on the same market event the production signal
fired on, using the recorded candles, and charges it properly:

* the levels come from the **production futures tool's own** ``_levels`` and its
  ``_cost_points``. Reusing them is deliberate — a second stop formula here would
  make the comparison about my arithmetic instead of about the vehicle.
* a bar that contains **both** the stop and a target is ``AMBIGUOUS_BAR`` and is
  resolved as the stop. One-minute bars cannot say which came first, and
  resolving it the other way is how a backtest invents an edge.
* the futures **book is not recorded**, so the spread is ``UNAVAILABLE`` rather
  than assumed to be one tick. The cost model charges slippage and statutory
  charges; it does not pretend to know the depth.

Nothing here places, sizes or proposes a futures order, and no production module
imports it.
"""
from __future__ import annotations

from app.execution.futures_paper import _cost_points, _levels
from app.market.instruments import REGISTRY
from app.models import Candle

LONG = "LONG"
SHORT = "SHORT"

# Terminal states of a followed futures trade.
T1, T2, T3 = "T1", "T2", "T3"
STOP = "STOP"
TIMEOUT = "TIMEOUT"
AMBIGUOUS = "AMBIGUOUS_BAR"

UNAVAILABLE = "UNAVAILABLE"

# How long the trade is followed, in one-minute bars. Matches the option side's
# follow window so the two vehicles are judged over the same market.
HORIZON_BARS = 90


def side_for(option_type: str | None, direction: str | None = None) -> str | None:
    """The futures side that expresses the same view as the production leg.

    A bearish option view is a long PUT; the same view in futures is a SHORT, not
    a long anything. Mapping this explicitly is the whole reason the branch can be
    compared to the option branch at all.
    """
    opt = (option_type or "").upper()
    if opt in ("CE", "CALL"):
        return LONG
    if opt in ("PE", "PUT"):
        return SHORT
    d = (direction or "").upper()
    if d in ("UP", "BULLISH", "LONG", "BUY"):
        return LONG
    if d in ("DOWN", "BEARISH", "SHORT", "SELL"):
        return SHORT
    return None


def _atr(candles: list[Candle], index: int, period: int = 14) -> float | None:
    """True-range average over the bars before the signal. None when too short."""
    if index < period + 1:
        return None
    trs: list[float] = []
    for i in range(index - period + 1, index + 1):
        prev_close = float(candles[i - 1].close)
        hi, lo = float(candles[i].high), float(candles[i].low)
        trs.append(max(hi - lo, abs(hi - prev_close), abs(lo - prev_close)))
    return sum(trs) / len(trs) if trs else None


def evaluate(instrument: str, candles: list[Candle], index_at_signal: int,
             side: str, *, support: float | None = None,
             resistance: float | None = None,
             horizon_bars: int = HORIZON_BARS) -> dict:
    """Follow one research-only futures trade on the recorded candles.

    ``index_at_signal`` is the bar the production signal was computed on; the
    trade is entered at that bar's close and followed from the next bar, so no
    bar the decision could not have seen is used.
    """
    n = len(candles)
    if not (0 <= index_at_signal < n) or side not in (LONG, SHORT):
        return {"measurable": False,
                "reason": "no bar at the signal timestamp, or no directional side"}
    atr = _atr(candles, index_at_signal)
    entry = float(candles[index_at_signal].close)
    if atr is None or atr <= 0 or entry <= 0:
        return {"measurable": False,
                "reason": "fewer than 15 bars before the signal, so no ATR exists "
                          "to size a stop from"}

    stop, t1, t2, t3 = _levels(side, entry, atr, support, resistance)
    risk = abs(entry - stop)
    if risk <= 0:
        return {"measurable": False, "reason": "the derived stop equals the entry"}

    spec = REGISTRY.get(instrument.upper())
    lot_size = spec.lot_size if spec else 1
    long_side = side == LONG

    def r_of(price: float) -> float:
        return ((price - entry) if long_side else (entry - price)) / risk

    reached: dict[str, dict] = {}
    sequence: list[str] = []
    mfe = mae = 0.0
    mfe_bar = mae_bar = index_at_signal
    outcome = TIMEOUT
    exit_price = entry
    exit_bar = index_at_signal
    ambiguous = False
    stop_bar: int | None = None

    last = min(n - 1, index_at_signal + horizon_bars)
    for i in range(index_at_signal + 1, last + 1):
        bar = candles[i]
        hi, lo = float(bar.high), float(bar.low)
        best = hi if long_side else lo
        worst = lo if long_side else hi
        if r_of(best) > mfe:
            mfe, mfe_bar = r_of(best), i
        if r_of(worst) < mae:
            mae, mae_bar = r_of(worst), i

        stop_touched = lo <= stop if long_side else hi >= stop
        for name, level in ((T1, t1), (T2, t2), (T3, t3)):
            if name in reached:
                continue
            hit = hi >= level if long_side else lo <= level
            if not hit:
                continue
            # A bar holding both the stop and this target cannot say which came
            # first. It is resolved as the stop, and the row says so.
            if stop_touched:
                ambiguous = True
                continue
            reached[name] = {"bar": i, "minutes_from_signal": i - index_at_signal,
                             "points": round(abs(level - entry), 2),
                             "r": round(r_of(level), 3)}
            sequence.append(name)

        if stop_touched:
            outcome, exit_price, exit_bar, stop_bar = STOP, stop, i, i
            sequence.append(AMBIGUOUS if ambiguous else STOP)
            break
        if T3 in reached:
            outcome, exit_price, exit_bar = T3, t3, i
            break
    else:
        exit_price, exit_bar = float(candles[last].close), last

    cost_points = _cost_points(instrument, entry, exit_price, lot_size, 1)
    gross_r = round(r_of(exit_price), 3)
    net_r = round(gross_r - cost_points / risk, 3)
    first = sequence[0] if sequence else None

    return {
        "measurable": True,
        "vehicle": "FUTURES",
        "instrument": instrument.upper(),
        "side": side,
        "entry": round(entry, 2),
        "stop": stop,
        "target1": t1, "target2": t2, "target3": t3,
        "risk_points": round(risk, 2),
        "atr_points": round(atr, 2),
        "atr_pct_of_price": round(100.0 * atr / entry, 3),
        "expected_move_points": round(abs(t1 - entry), 2),
        "lot_size": lot_size,
        "risk_rupees": round(risk * lot_size, 2),
        # The futures book is not in the recorded data, so it is absent rather
        # than assumed. The cost model charges statutory charges and slippage.
        "spread": None,
        "spread_source": UNAVAILABLE,
        "liquidity_volume": float(candles[index_at_signal].volume or 0.0),
        "targets_reached": reached,
        "target_sequence": " -> ".join(sequence) or "NONE",
        "first_event": first,
        "target_before_stop": first in (T1, T2, T3),
        "outcome": outcome,
        "bar_ambiguous": ambiguous,
        "ambiguity_rule": ("a bar containing both the stop and a target is "
                           "resolved as the stop; one-minute bars cannot order "
                           "the two"),
        "mfe_r": round(mfe, 3),
        "mfe_points": round(mfe * risk, 2),
        "minutes_to_mfe": mfe_bar - index_at_signal,
        "mae_r": round(mae, 3),
        "mae_points": round(mae * risk, 2),
        "minutes_to_mae": mae_bar - index_at_signal,
        "exit_price": round(exit_price, 2),
        "minutes_to_resolution": exit_bar - index_at_signal,
        "minutes_to_target": (reached[T1]["minutes_from_signal"]
                              if T1 in reached else None),
        "minutes_to_stop": (stop_bar - index_at_signal
                            if stop_bar is not None else None),
        "gross_r": gross_r,
        "cost_points": round(cost_points, 3),
        "cost_r": round(cost_points / risk, 3),
        "net_r": net_r,
        "cost_basis": ("production futures cost model: brokerage, STT/CTT on "
                       "turnover, exchange charges and configured slippage"),
    }
