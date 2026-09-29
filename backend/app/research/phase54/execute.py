"""Phase 54 — the multi-session fill, stop, R-multiple target, cost and clock.

The cost model is the shared one (``phase19.futcosts`` reached through Phase
24's public helper), for the same reason every earlier phase reuses it: a
research layer that invents its own cheaper cost produces numbers nobody can
reconcile against the phases it hopes to feed. The resolver is local, because a
position that lives for ten sessions is not something any earlier phase models.

What makes this honest, and where it is still optimistic:

* the decision is a **completed daily close** and the fill is the **next
  session's open**. No trade is entered on the bar that triggered it;
* the path is walked on **five-minute** bars across every session the position
  is allowed to live, not on four daily numbers. A stop and a target that both
  fall inside one session are ordered as finely as this data can order them;
* a five-minute bar containing both levels is a **loss**. The candle cannot say
  which came first;
* the clock counts **sessions including the entry session**, and an unresolved
  position leaves at the open of the session after the last allowed one — not
  at whatever price would have been kinder;
* **overnight gaps are where this model is still optimistic.** A level the
  market had already gapped through when a bar opened is filled at that bar's
  **open** rather than at the level, which cuts both ways — a gapped stop is
  worse than the stop and a gapped target better than the target. What a candle
  still cannot show is what a resting order would actually have received inside
  the gap, so real multi-day losses remain at least this large and probably
  larger. That is stated in the artefacts rather than buried;
* the gate's cost and the charged cost are different numbers on purpose: the
  gate must be computable **before** the trade, so it uses the round trip
  modelled at the entry price, while the charge uses the real entry and exit
  because statutory charges are a share of turnover.
"""
from __future__ import annotations

import numpy as np

from app.market.instruments import get_spec
from app.research.phase24 import outcomes as p24outcomes

LONG, SHORT = 1, -1

TARGET_HIT = "TARGET"
STOP_HIT = "STOP"
TIME_EXIT = "TIME_EXIT"
DATA_END_EXIT = "DATA_END"

# Fixed probe prices for reading the cost model's slope in price. Constants, not
# data: a probe taken from the series would make one trade's cost depend on the
# prices of the other trades in the batch, which is a look-ahead hiding inside
# an innocuous-looking median.
COST_PROBE_PRICE = 1_000.0
COST_PROBE_STEP = 10_000.0


def cost_at_price(instrument: str, price: np.ndarray) -> np.ndarray:
    """Modelled round-trip cost in points for a trade entered and exited at ``price``.

    This is the number the cost gate divides into, and it is knowable at the
    decision session: it needs the price and the instrument, and nothing that
    has not happened yet.
    """
    lot_size = int(get_spec(instrument).lot_size or 1)
    probe = np.array([COST_PROBE_PRICE, COST_PROBE_PRICE + COST_PROBE_STEP])
    c = p24outcomes.cost_points_per_trade(
        instrument, probe, probe, lot_size=lot_size, slippage_points=None
    )
    slope = (float(c[1]) - float(c[0])) / COST_PROBE_STEP
    intercept = float(c[0]) - slope * COST_PROBE_PRICE
    return intercept + slope * np.asarray(price, dtype=np.float64)


def charged_cost(instrument: str, entry: float, exit_price: float) -> float:
    """Round-trip cost actually charged to one trade, from the shared model."""
    return float(p24outcomes.cost_points_per_trade(
        instrument,
        np.array([float(entry)], dtype=np.float64),
        np.array([float(exit_price)], dtype=np.float64),
        lot_size=int(get_spec(instrument).lot_size or 1),
        slippage_points=None,
    )[0])


def resolve_one(
    instrument: str,
    high: np.ndarray,
    low: np.ndarray,
    open_: np.ndarray,
    close: np.ndarray,
    ts: np.ndarray,
    *,
    fill_i: int,
    last_allowed_i: int,
    side: int,
    entry: float,
    stop: float,
    target: float,
    spread_multiplier: float = 1.0,
) -> dict:
    """Walk one trade forward to target, stop or the clock, and charge cost.

    ``fill_i`` is the five-minute bar the trade fills on and ``entry`` is that
    bar's open; the walk starts on the fill bar itself because its range can
    reach either level. ``last_allowed_i`` is the last five-minute bar of the
    last session the position may live in.
    """
    n = high.size
    end_i = int(min(last_allowed_i, n - 1))
    start_i = int(fill_i)
    risk = max(abs(float(entry) - float(stop)), 1e-9)

    win_high = high[start_i:end_i + 1]
    win_low = low[start_i:end_i + 1]
    if side == LONG:
        stop_hits = np.nonzero(win_low <= stop)[0]
        target_hits = np.nonzero(win_high >= target)[0]
    else:
        stop_hits = np.nonzero(win_high >= stop)[0]
        target_hits = np.nonzero(win_low <= target)[0]
    stop_k = int(stop_hits[0]) if stop_hits.size else -1
    target_k = int(target_hits[0]) if target_hits.size else -1

    # The tie is a loss: same-bar ambiguity resolves to the stop. A level the
    # market had already gapped through when the bar opened is filled at that
    # **open**, not at the level — the one place a multi-day model can cheaply
    # stop flattering itself, and it cuts both ways: a gapped stop is worse than
    # the stop and a gapped target is better than the target.
    if stop_k >= 0 and (target_k < 0 or stop_k <= target_k):
        outcome, exit_k = STOP_HIT, stop_k
        bar_open = float(open_[start_i + stop_k])
        gapped = bar_open < stop if side == LONG else bar_open > stop
        exit_price = bar_open if gapped else float(stop)
    elif target_k >= 0:
        outcome, exit_k = TARGET_HIT, target_k
        bar_open = float(open_[start_i + target_k])
        gapped = bar_open > target if side == LONG else bar_open < target
        exit_price = bar_open if gapped else float(target)
    elif end_i + 1 < n:
        outcome, exit_k = TIME_EXIT, end_i - start_i + 1
        exit_price = float(open_[end_i + 1])
    else:
        # The file simply stops. Marked distinctly so a reader can see how many
        # trades were settled by the edge of the data rather than by the rule,
        # and priced at the last completed close because there is no next open.
        outcome, exit_k = DATA_END_EXIT, end_i - start_i
        exit_price = float(close[end_i])

    exit_i = int(min(start_i + exit_k, n - 1))
    path_high = high[start_i:exit_i + 1]
    path_low = low[start_i:exit_i + 1]
    if path_high.size == 0:
        mfe = mae = 0.0
    elif side == LONG:
        mfe = float(path_high.max() - entry)
        mae = float(entry - path_low.min())
    else:
        mfe = float(entry - path_low.min())
        mae = float(path_high.max() - entry)

    gross = float(side) * (exit_price - float(entry))
    cost = charged_cost(instrument, entry, exit_price) * float(spread_multiplier)
    net = gross - cost
    lot_size = int(get_spec(instrument).lot_size or 1)
    return {
        "risk_points": float(risk),
        "outcome": outcome,
        "exit_index": exit_i,
        "exit_ts": int(ts[exit_i]),
        "exit_price": float(exit_price),
        "hold_bars": int(exit_i - start_i),
        "gross_points": gross,
        "cost_points": cost,
        "net_points": net,
        "net_r": net / risk,
        "gross_r": gross / risk,
        "net_rupees": net * float(lot_size),
        "mfe_r": mfe / risk,
        "mae_r": mae / risk,
        "resolved": bool(np.isfinite(net)),
    }
