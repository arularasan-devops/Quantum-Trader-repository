"""Phase 53 — the fill, the stop, the R-multiple target, the cost, the clock.

The cost model is the shared one (``phase19.futcosts`` reached through Phase
24's public helper) for the same reason as every earlier phase: a research layer
that invents its own cheaper cost produces numbers nobody can reconcile against
the phases it hopes to feed. Everything else here is local to Phase 53, because
its target is an **R multiple of the measured risk** rather than a price level,
which no earlier resolver models.

The properties that decide whether any of this is honest:

* the decision is taken on a **completed** bar's close and the fill is the
  **next five-minute bar's open**. Entering on the close that triggered is the
  most common look-ahead in intraday research and it is worth about one bar of
  free edge;
* a bar containing both the stop and the target is a **loss**. A five-minute
  candle cannot say which came first, and the optimistic reading is what turns a
  losing rule into a winning backtest;
* the trade is flattened at the session close. An intraday result may never be
  settled by a bar on the far side of an overnight gap;
* the clock is real: 180 minutes is 36 five-minute bars, and an unresolved trade
  exits at the next bar's open, not at whatever price would have been kinder;
* the gate's cost and the charged cost are different numbers on purpose. The
  gate must be computable **before** the trade, so it uses the round trip
  modelled at the entry price; the charged cost uses the real entry and exit,
  because statutory charges are a share of turnover.
"""
from __future__ import annotations

import numpy as np

from app.market.instruments import get_spec
from app.research.phase24 import outcomes as p24outcomes
from app.research.phase53 import DECISION_TIMEFRAME_MINUTES, MAX_HOLD_MINUTES

LONG, SHORT = 1, -1

TARGET_HIT = "TARGET"
STOP_HIT = "STOP"
TIME_EXIT = "TIME_EXIT"

MAX_HOLD_BARS = MAX_HOLD_MINUTES // DECISION_TIMEFRAME_MINUTES   # 36

# Fixed probe prices for reading the cost model's slope in price. Constants, not
# data: a probe taken from the series would make one trade's cost depend on the
# prices of the other trades in the batch, which is a look-ahead that hides
# inside an innocuous-looking median.
COST_PROBE_PRICE = 1_000.0
COST_PROBE_STEP = 10_000.0


def cost_at_price(instrument: str, price: np.ndarray) -> np.ndarray:
    """Modelled round-trip cost in points for a trade entered and exited at ``price``.

    This is the number the cost gate divides into, and it is knowable at the
    decision bar: it needs the price and the instrument, and nothing that has
    not happened yet.
    """
    lot_size = int(get_spec(instrument).lot_size or 1)
    probe = np.array([COST_PROBE_PRICE, COST_PROBE_PRICE + COST_PROBE_STEP])
    c = p24outcomes.cost_points_per_trade(
        instrument, probe, probe, lot_size=lot_size, slippage_points=None
    )
    slope = (float(c[1]) - float(c[0])) / COST_PROBE_STEP
    intercept = float(c[0]) - slope * COST_PROBE_PRICE
    return intercept + slope * np.asarray(price, dtype=np.float64)


def charged_cost(
    instrument: str, entry: np.ndarray, exit_price: np.ndarray
) -> np.ndarray:
    """Round-trip cost actually charged to each trade, from the shared model."""
    return p24outcomes.cost_points_per_trade(
        instrument,
        np.asarray(entry, dtype=np.float64),
        np.asarray(exit_price, dtype=np.float64),
        lot_size=int(get_spec(instrument).lot_size or 1),
        slippage_points=None,
    )


def _first_touch(
    high: np.ndarray,
    low: np.ndarray,
    fill_i: np.ndarray,
    side: np.ndarray,
    level: np.ndarray,
    allowed: np.ndarray,
    *,
    favourable: bool,
) -> np.ndarray:
    """Bars from the fill until ``level`` is touched, or -1 inside the window."""
    n = high.size
    out = np.full(fill_i.size, -1, dtype=np.int64)
    if fill_i.size == 0:
        return out
    for k in range(0, MAX_HOLD_BARS + 1):
        live = (out < 0) & (k <= allowed)
        if not live.any():
            break
        j = np.minimum(fill_i + k, n - 1)
        if favourable:
            hit = np.where(side == LONG, high[j] >= level, low[j] <= level)
        else:
            hit = np.where(side == LONG, low[j] <= level, high[j] >= level)
        hit &= (fill_i + k) < n
        out[live & hit] = k
    return out


def _excursions(
    high: np.ndarray,
    low: np.ndarray,
    fill_i: np.ndarray,
    side: np.ndarray,
    entry: np.ndarray,
    exit_bar: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Best and worst excursion in points, up to the bar the trade left."""
    n = high.size
    best = np.zeros(fill_i.size)
    worst = np.zeros(fill_i.size)
    for k in range(0, MAX_HOLD_BARS + 1):
        alive = (k <= exit_bar) & ((fill_i + k) < n)
        if not alive.any():
            continue
        j = np.minimum(fill_i + k, n - 1)
        fav = np.where(side == LONG, high[j] - entry, entry - low[j])
        adv = np.where(side == LONG, entry - low[j], high[j] - entry)
        best = np.where(alive, np.maximum(best, fav), best)
        worst = np.where(alive, np.maximum(worst, adv), worst)
    return best, worst


def resolve(
    instrument: str,
    high: np.ndarray,
    low: np.ndarray,
    open_: np.ndarray,
    fill_i: np.ndarray,
    side: np.ndarray,
    entry: np.ndarray,
    stop: np.ndarray,
    target: np.ndarray,
    session_last_bar: np.ndarray,
    *,
    spread_multiplier: float = 1.0,
) -> dict[str, np.ndarray]:
    """Walk every trade forward to target, stop or the clock, and charge cost.

    ``fill_i`` is the index of the five-minute bar the trade fills on and
    ``entry`` is that bar's open. The walk starts at the fill bar itself,
    because the range of the bar the fill happened on can touch either level.
    """
    n = high.size
    risk = np.maximum(np.abs(entry - stop), 1e-9)

    allowed = np.minimum(MAX_HOLD_BARS, np.maximum(0, session_last_bar - fill_i))
    stop_bar = _first_touch(
        high, low, fill_i, side, stop, allowed, favourable=False
    )
    target_bar = _first_touch(
        high, low, fill_i, side, target, allowed, favourable=True
    )

    target_first = (target_bar >= 0) & ((stop_bar < 0) | (target_bar < stop_bar))
    stop_first = (stop_bar >= 0) & ~target_first

    exit_bar = np.where(
        target_first, target_bar, np.where(stop_first, stop_bar, allowed)
    )
    exit_j = np.minimum(fill_i + exit_bar, n - 1)
    exit_price = np.where(
        target_first, target, np.where(stop_first, stop, open_[exit_j])
    )

    mfe, mae = _excursions(high, low, fill_i, side, entry, exit_bar)
    gross = side * (exit_price - entry)
    cost = charged_cost(instrument, entry, exit_price) * float(spread_multiplier)
    net = gross - cost
    lot_size = int(get_spec(instrument).lot_size or 1)

    return {
        "risk_points": risk,
        "stop_bar": stop_bar,
        "target_bar": target_bar,
        "exit_bar": exit_bar,
        "exit_price": exit_price,
        "hold_minutes": exit_bar.astype(np.float64) * DECISION_TIMEFRAME_MINUTES,
        "gross_points": gross,
        "cost_points": cost,
        "net_points": net,
        "net_r": net / risk,
        "gross_r": gross / risk,
        "net_rupees": net * float(lot_size),
        "mfe_r": mfe / risk,
        "mae_r": mae / risk,
        "outcome": np.where(
            target_first, TARGET_HIT, np.where(stop_first, STOP_HIT, TIME_EXIT)
        ),
        "resolved": (allowed >= 0) & (fill_i < n) & np.isfinite(net),
    }
