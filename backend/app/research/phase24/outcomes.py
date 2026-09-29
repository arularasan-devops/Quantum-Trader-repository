"""Phase 24 §4 — realistic entry, realistic exit, realistic cost.

Geometry is fixed here, in code, before any discovery runs: stop at one ATR,
targets at 1.5R / 2.5R / 4R, a bounded holding window, and a same-bar tie
resolved as the stop. None of those four choices is tuned later, because a study
that tunes its own geometry after seeing the outcomes has fitted the answer.

Three honesty properties:

* the signal is taken at the **close** of bar ``i`` and the fill happens at the
  **open of bar i+1** plus slippage. Entering at the same close a decision was
  made on is the most common look-ahead in intraday research;
* when a bar's range contains both the stop and the target, the **stop** wins.
  A 1-minute bar cannot say which came first, and the optimistic reading is what
  turns a losing rule into a winning backtest;
* the vehicle priced is FUTURES. Brokerage, statutory charges and configured
  slippage are charged both ways through the existing Phase 19 cost model; the
  spread is unmeasured because the futures feed publishes candles rather than
  depth, so every net figure is optimistic by exactly one spread and says so.
"""
from __future__ import annotations

import numpy as np

from app.market.instruments import get_spec
from app.research.phase19 import futcosts

# Frozen research geometry.
STOP_ATR = 1.0
T1_R = 1.5
T2_R = 2.5
T3_R = 4.0
HORIZON_BARS = 180        # a bounded intraday hold; unresolved is a real outcome
ENTRY_DELAY_BARS = 1      # decide on the close, fill on the next open

LONG, SHORT = 1, -1

T1_BEFORE_SL = "T1_BEFORE_SL"
SL_FIRST = "SL"
TIMEOUT = "TIMEOUT"


class Outcomes:
    """Resolved outcomes for one aligned block of candidates.

    Parallel arrays keyed by candidate order, so a discovery mask can select a
    cohort with one boolean index rather than re-resolving anything.
    """

    __slots__ = (
        "idx", "side", "entry", "stop", "risk", "t1", "t2", "t3",
        "t1_before_sl", "t2_before_sl", "t3_before_sl", "sl_hit",
        "exit_price", "bars_to_t1", "bars_to_sl", "bars_held",
        "mfe_r", "mae_r", "gross_points", "net_points", "net_r",
        "net_rupees", "cost_points", "resolved", "outcome",
    )

    def __len__(self) -> int:
        return int(self.idx.size)


def _first_hit(
    high: np.ndarray,
    low: np.ndarray,
    idx: np.ndarray,
    side: np.ndarray,
    level: np.ndarray,
    allowed: np.ndarray,
    horizon: int,
    *,
    favourable: bool,
) -> np.ndarray:
    """Bars until ``level`` is touched, or -1 if it never is inside the window.

    ``allowed`` is per candidate: the trade is flattened at the session close, so
    a bar in tomorrow's session can never resolve today's trade.
    """
    n = high.size
    out = np.full(idx.size, -1, dtype=np.int32)
    for k in range(1, horizon + 1):
        live = out < 0
        live &= k <= allowed
        if not live.any():
            break
        j = np.minimum(idx + k, n - 1)
        if favourable:
            hit = np.where(side == LONG, high[j] >= level, low[j] <= level)
        else:
            hit = np.where(side == LONG, low[j] <= level, high[j] >= level)
        hit &= (idx + k) < n
        out[live & hit] = k
    return out


def _excursions(
    high: np.ndarray,
    low: np.ndarray,
    idx: np.ndarray,
    side: np.ndarray,
    entry: np.ndarray,
    stop_bar: np.ndarray,
    allowed: np.ndarray,
    horizon: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Best and worst excursion in points, up to the bar the trade left the book."""
    n = high.size
    best = np.zeros(idx.size)
    worst = np.zeros(idx.size)
    for k in range(1, horizon + 1):
        alive = (stop_bar < 0) | (k <= stop_bar)
        alive &= (k <= allowed) & ((idx + k) < n)
        if not alive.any():
            continue
        j = np.minimum(idx + k, n - 1)
        fav = np.where(side == LONG, high[j] - entry, entry - low[j])
        adv = np.where(side == LONG, entry - low[j], high[j] - entry)
        best = np.where(alive, np.maximum(best, fav), best)
        worst = np.where(alive, np.maximum(worst, adv), worst)
    return best, worst


def _cost_points_per_trade(
    instrument: str,
    entry: np.ndarray,
    exit_price: np.ndarray,
    *,
    lot_size: int,
    slippage_points: float | None,
) -> np.ndarray:
    """Round-trip cost in points for every candidate, from the shared model.

    Statutory charges are a percentage of turnover, so a trade at NIFTY 15,000
    does not cost what the same trade costs at 26,000 and one median cost for a
    five-year study would misprice both ends of it. The model is called three
    times to read its own coefficients — it is linear in the entry and exit
    prices — and those coefficients are then applied per trade, so the cost still
    comes from ``phase19.futcosts`` and nothing is restated here. The smoke test
    checks the reconstruction against direct calls at random prices.
    """
    def c(e: float, x: float) -> float:
        return float(futcosts.round_trip(
            instrument, entry=e, exit_price=x, lot_size=lot_size, lots=1,
            slippage_points=slippage_points,
        ).get("cost_points") or 0.0)

    finite = np.isfinite(entry) & np.isfinite(exit_price)
    p0 = float(np.median(entry[finite])) if finite.any() else 1000.0
    # A large probe step on purpose: the model rounds its answer to four
    # decimals, and a small step would read that rounding as the slope.
    d = max(1_000.0, p0)
    base = c(p0, p0)
    per_entry = (c(p0 + d, p0) - base) / d
    per_exit = (c(p0, p0 + d) - base) / d
    out = base + per_entry * (entry - p0) + per_exit * (exit_price - p0)
    return np.where(np.isfinite(out), out, base)


def resolve(
    instrument: str,
    high: np.ndarray,
    low: np.ndarray,
    open_: np.ndarray,
    ts: np.ndarray,
    idx: np.ndarray,
    side: np.ndarray,
    atr: np.ndarray,
    session_last_bar: np.ndarray,
    *,
    horizon: int = HORIZON_BARS,
    entry_delay_bars: int = ENTRY_DELAY_BARS,
    exit_delay_bars: int = 0,
    slippage_points: float | None = None,
    spread_multiplier: float = 1.0,
    stop_atr: float = STOP_ATR,
    t1_r: float = T1_R,
) -> Outcomes:
    """Resolve every candidate's forward path with costs charged.

    ``entry_delay_bars`` and ``exit_delay_bars`` exist for §9's sensitivity grid;
    the defaults are the honest baseline, not the best case.
    """
    n = high.size
    fill_i = np.minimum(idx + max(1, int(entry_delay_bars)), n - 1)
    spec = get_spec(instrument)
    lot_size = int(spec.lot_size or 1)

    # The fill is the next open, unadjusted. Slippage is charged once, on both
    # legs, by the cost model below; moving the entry price as well would charge
    # the entry leg twice and quietly make the study look worse than it is.
    entry = open_[fill_i]

    risk = np.maximum(atr[idx] * float(stop_atr), 1e-9)
    stop = entry - side * risk
    t1 = entry + side * risk * float(t1_r)
    t2 = entry + side * risk * T2_R
    t3 = entry + side * risk * T3_R

    # The trade is flattened at the session close: an intraday result must never
    # be settled by a bar on the other side of an overnight gap.
    allowed = np.minimum(horizon, np.maximum(0, session_last_bar - fill_i))
    sl_bar = _first_hit(high, low, fill_i, side, stop, allowed, horizon, favourable=False)
    t1_bar = _first_hit(high, low, fill_i, side, t1, allowed, horizon, favourable=True)
    t2_bar = _first_hit(high, low, fill_i, side, t2, allowed, horizon, favourable=True)
    t3_bar = _first_hit(high, low, fill_i, side, t3, allowed, horizon, favourable=True)

    def before(target_bar: np.ndarray) -> np.ndarray:
        """Target reached strictly before the stop. A same-bar tie is a loss."""
        hit = target_bar >= 0
        no_sl = sl_bar < 0
        return hit & (no_sl | (target_bar < sl_bar))

    t1_ok = before(t1_bar)
    t2_ok = before(t2_bar)
    t3_ok = before(t3_bar)
    sl_first = (sl_bar >= 0) & ~t1_ok

    # Exit price: the target for a winner, the stop for a loser, otherwise the
    # close of the horizon's last bar (a real trade is flattened, not held).
    delay = max(0, int(exit_delay_bars))
    exit_bar = np.where(t1_ok, t1_bar, np.where(sl_first, sl_bar, allowed)) + delay
    exit_bar = np.minimum(exit_bar, np.maximum(allowed, 0) + delay)
    exit_j = np.minimum(fill_i + exit_bar, n - 1)
    exit_price = np.where(
        t1_ok & (delay == 0), t1,
        np.where(sl_first & (delay == 0), stop, open_[exit_j]),
    )

    mfe, mae = _excursions(high, low, fill_i, side, entry, sl_bar, allowed, horizon)
    gross = side * (exit_price - entry)

    cost_points = _cost_points_per_trade(
        instrument,
        entry,
        exit_price,
        lot_size=lot_size,
        slippage_points=slippage_points,
    ) * float(spread_multiplier)
    net = gross - cost_points

    o = Outcomes()
    o.idx = idx
    o.side = side
    o.entry = entry
    o.stop = stop
    o.risk = risk
    o.t1, o.t2, o.t3 = t1, t2, t3
    o.t1_before_sl = t1_ok
    o.t2_before_sl = t2_ok
    o.t3_before_sl = t3_ok
    o.sl_hit = sl_first
    o.exit_price = exit_price
    o.bars_to_t1 = t1_bar
    o.bars_to_sl = sl_bar
    o.bars_held = exit_bar
    o.mfe_r = mfe / risk
    o.mae_r = mae / risk
    o.gross_points = gross
    o.net_points = net
    o.net_r = net / risk
    o.net_rupees = net * float(lot_size)
    o.cost_points = cost_points
    # A candidate with no forward window inside its own session never resolved.
    o.resolved = (allowed > 0) & ((fill_i + 1) < n)
    o.outcome = np.where(t1_ok, T1_BEFORE_SL, np.where(sl_first, SL_FIRST, TIMEOUT))
    return o


def cost_points_per_trade(
    instrument: str,
    entry: np.ndarray,
    exit_price: np.ndarray,
    *,
    lot_size: int,
    slippage_points: float | None = None,
) -> np.ndarray:
    """Public name for the per-trade round-trip cost, for other research phases.

    Additive wrapper around the same reconstruction this module already uses, so
    a later study charges cost from ``phase19.futcosts`` rather than re-deriving
    a cost model of its own and drifting away from this one.
    """
    return _cost_points_per_trade(
        instrument, entry, exit_price,
        lot_size=lot_size, slippage_points=slippage_points,
    )


def cost_note(instrument: str, entry: float, exit_price: float) -> dict:
    """The cost decomposition for one representative trade, for the report."""
    return futcosts.round_trip(
        instrument,
        entry=entry,
        exit_price=exit_price,
        lot_size=int(get_spec(instrument).lot_size or 1),
        lots=1,
    )
