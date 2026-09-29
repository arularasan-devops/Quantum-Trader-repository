"""Phase 28 §5 — resolving a trade that is allowed to be held overnight.

This is the one place where Phase 28 must NOT reuse Phase 24/27. Those resolvers
end every trade at the session close by design, because an intraday result settled
by a bar on the other side of an overnight gap is not an intraday result. A swing
study measures exactly the thing they forbid, so the forward walk here is over
daily bars and crosses as many nights as the horizon allows.

The rules, all of them frozen before any number was produced:

* the decision is taken on a daily **close** and the fill is the **next session's
  open**. Never the same bar's close, which is the classic daily-backtest fiction;
* risk is ``stop_atr`` × the 14-day ATR **as known at the signal bar**;
* every bar after the fill is checked at its OPEN before its range. A gap through
  the stop fills at the open and the extra loss is charged; a gap through the
  target fills at the open and the extra gain is credited. This is the single
  biggest difference between an honest and a flattering daily backtest, and it is
  measurable both ways: ``gap_fill_at_open=False`` reproduces the flattering
  assumption so the cost of the truth can be quantified rather than asserted;
* a bar containing both the stop and the target is resolved as the **stop**. Daily
  bars have no intrabar path, and assuming the good side of that ambiguity is how a
  daily study invents an edge;
* an unresolved trade exits at the close of the last bar in the horizon;
* costs come from :mod:`app.research.phase28.costs`, which charges a futures roll
  per contract crossing and cash equity as delivery. Slippage is charged in both
  directions and never improves a fill.
"""
from __future__ import annotations

import numpy as np

from app.market.instruments import get_spec
from app.research.phase24.outcomes import LONG, SHORT, Outcomes
from app.research.phase28 import EQUITY_DELIVERY, FUTURES
from app.research.phase28 import costs as cost_model
from app.research.phase28.dailybars import is_equity

# Frozen geometry. Same shape as Phase 24 so the three studies are comparable,
# expressed in trading days rather than in minutes.
STOP_ATR = 1.0
T1_R = 1.5
T2_R = 2.5
T3_R = 4.0
HORIZON_DAYS = 10
ENTRY_DELAY_BARS = 1

OUT_T1 = "T1_BEFORE_SL"
OUT_SL = "SL"
OUT_TIMEOUT = "TIMEOUT"

EXIT_STOP_LEVEL = 0
EXIT_STOP_GAP = 1
EXIT_TARGET_LEVEL = 2
EXIT_TARGET_GAP = 3
EXIT_HORIZON = 4

EXIT_LABELS = {
    EXIT_STOP_LEVEL: "STOP_AT_LEVEL",
    EXIT_STOP_GAP: "STOP_GAPPED_THROUGH",
    EXIT_TARGET_LEVEL: "TARGET_AT_LEVEL",
    EXIT_TARGET_GAP: "TARGET_GAPPED_THROUGH",
    EXIT_HORIZON: "HORIZON_CLOSE",
}


class SwingOutcomes(Outcomes):
    """Phase 24's container plus the three facts only a multi-day hold has."""

    __slots__ = ("exit_reason", "calendar_days", "rolls", "gap_fill_at_open")


def _empty(n: int) -> SwingOutcomes:
    o = SwingOutcomes.__new__(SwingOutcomes)
    o.idx = np.zeros(n, dtype=np.int64)
    o.side = np.zeros(n, dtype=np.int64)
    o.bars_to_t1 = np.zeros(n, dtype=np.int64)
    o.bars_to_sl = np.zeros(n, dtype=np.int64)
    o.bars_held = np.zeros(n, dtype=np.int64)
    o.t1_before_sl = np.zeros(n, dtype=bool)
    o.t2_before_sl = np.zeros(n, dtype=bool)
    o.t3_before_sl = np.zeros(n, dtype=bool)
    o.sl_hit = np.zeros(n, dtype=bool)
    o.resolved = np.zeros(n, dtype=bool)
    o.outcome = np.empty(n, dtype=object)
    o.entry = np.zeros(n, dtype=np.float64)
    o.stop = np.zeros(n, dtype=np.float64)
    o.risk = np.zeros(n, dtype=np.float64)
    o.t1 = np.zeros(n, dtype=np.float64)
    o.t2 = np.zeros(n, dtype=np.float64)
    o.t3 = np.zeros(n, dtype=np.float64)
    o.exit_price = np.zeros(n, dtype=np.float64)
    o.mfe_r = np.zeros(n, dtype=np.float64)
    o.mae_r = np.zeros(n, dtype=np.float64)
    o.gross_points = np.zeros(n, dtype=np.float64)
    o.net_points = np.zeros(n, dtype=np.float64)
    o.net_r = np.zeros(n, dtype=np.float64)
    o.net_rupees = np.zeros(n, dtype=np.float64)
    o.cost_points = np.zeros(n, dtype=np.float64)
    o.exit_reason = np.full(n, EXIT_HORIZON, dtype=np.int8)
    o.calendar_days = np.zeros(n, dtype=np.float64)
    o.rolls = np.zeros(n, dtype=np.int64)
    return o


def resolve(
    instrument: str,
    vehicle: str,
    *,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    ts: np.ndarray,
    idx: np.ndarray,
    side: np.ndarray,
    atr: np.ndarray,
    horizon_days: int = HORIZON_DAYS,
    entry_delay_bars: int = ENTRY_DELAY_BARS,
    exit_delay_bars: int = 0,
    stop_atr: float = STOP_ATR,
    t1_r: float = T1_R,
    t2_r: float = T2_R,
    t3_r: float = T3_R,
    slippage_points: float | None = None,
    slippage_pct: float | None = None,
    spread_multiplier: float = 1.0,
    gap_fill_at_open: bool = True,
) -> SwingOutcomes:
    """Resolve every candidate over a multi-day forward window."""
    n_bars = int(close.size)
    n = int(idx.size)
    o = _empty(n)
    o.idx = idx.astype(np.int64)
    o.side = side.astype(np.int64)
    o.gap_fill_at_open = bool(gap_fill_at_open)
    horizon = max(1, int(horizon_days))
    delay = max(1, int(entry_delay_bars))
    exit_delay = max(0, int(exit_delay_bars))

    for k in range(n):
        i = int(idx[k])
        fill = i + delay
        if fill >= n_bars:
            o.resolved[k] = False
            o.outcome[k] = None
            continue
        sgn = 1.0 if int(side[k]) == LONG else -1.0
        entry = float(open_[fill])
        risk = float(stop_atr) * float(atr[i])
        if risk <= 0.0 or entry <= 0.0:
            o.resolved[k] = False
            o.outcome[k] = None
            continue
        stop = entry - sgn * risk
        t1 = entry + sgn * float(t1_r) * risk
        t2 = entry + sgn * float(t2_r) * risk
        t3 = entry + sgn * float(t3_r) * risk

        last = min(fill + horizon - 1, n_bars - 1)
        exit_bar = last
        exit_price = float(close[last])
        reason = EXIT_HORIZON
        hit_t1 = hit_sl = False
        bars_to_t1 = bars_to_sl = -1
        mfe = mae = 0.0

        for j in range(fill, last + 1):
            o_j, h_j, l_j = float(open_[j]), float(high[j]), float(low[j])
            fav = sgn * (h_j - entry) if sgn > 0 else sgn * (l_j - entry)
            adv = sgn * (l_j - entry) if sgn > 0 else sgn * (h_j - entry)
            mfe = max(mfe, fav)
            mae = min(mae, adv)

            gapped_stop = j > fill and sgn * (o_j - stop) <= 0.0
            gapped_target = j > fill and sgn * (o_j - t1) >= 0.0
            if gapped_stop:
                # Both gaps in one open is impossible: the open cannot be beyond
                # the stop and beyond the target at once.
                hit_sl, bars_to_sl = True, j - fill
                exit_bar = j
                exit_price = o_j if gap_fill_at_open else stop
                reason = EXIT_STOP_GAP if gap_fill_at_open else EXIT_STOP_LEVEL
                break
            if gapped_target:
                hit_t1, bars_to_t1 = True, j - fill
                exit_bar = j
                exit_price = o_j if gap_fill_at_open else t1
                reason = EXIT_TARGET_GAP if gap_fill_at_open else EXIT_TARGET_LEVEL
                break
            touched_stop = (l_j <= stop) if sgn > 0 else (h_j >= stop)
            touched_t1 = (h_j >= t1) if sgn > 0 else (l_j <= t1)
            if touched_stop:
                # Stop wins the same-bar ambiguity: a daily bar has no path.
                hit_sl, bars_to_sl = True, j - fill
                exit_bar, exit_price, reason = j, stop, EXIT_STOP_LEVEL
                break
            if touched_t1:
                hit_t1, bars_to_t1 = True, j - fill
                exit_bar, exit_price, reason = j, t1, EXIT_TARGET_LEVEL
                break

        if exit_delay and reason != EXIT_HORIZON:
            delayed = min(exit_bar + exit_delay, n_bars - 1)
            if delayed > exit_bar:
                exit_bar, exit_price = delayed, float(close[delayed])
                reason = EXIT_HORIZON

        calendar_days = max(
            0.0, (float(ts[exit_bar]) - float(ts[fill])) / 86_400.0
        )
        charge = cost_model.round_trip(
            instrument,
            vehicle,
            entry=entry,
            exit_price=exit_price,
            calendar_days_held=calendar_days,
            slippage_points=slippage_points,
            slippage_pct=slippage_pct,
            spread_multiplier=spread_multiplier,
        )
        cost_points = float(charge.get("cost_points") or 0.0)
        gross = sgn * (exit_price - entry)
        net = gross - cost_points

        o.entry[k] = entry
        o.stop[k] = stop
        o.risk[k] = risk
        o.t1[k] = t1
        o.t2[k] = t2
        o.t3[k] = t3
        o.t1_before_sl[k] = hit_t1
        o.t2_before_sl[k] = bool(not hit_sl and mfe >= float(t2_r) * risk)
        o.t3_before_sl[k] = bool(not hit_sl and mfe >= float(t3_r) * risk)
        o.sl_hit[k] = hit_sl
        o.exit_price[k] = exit_price
        o.bars_to_t1[k] = bars_to_t1
        o.bars_to_sl[k] = bars_to_sl
        o.bars_held[k] = exit_bar - fill + 1
        o.mfe_r[k] = mfe / risk
        o.mae_r[k] = mae / risk
        o.gross_points[k] = gross
        o.net_points[k] = net
        o.net_r[k] = net / risk
        o.cost_points[k] = cost_points
        o.net_rupees[k] = net * _unit_size(instrument, vehicle)
        o.resolved[k] = True
        o.outcome[k] = OUT_T1 if hit_t1 else (OUT_SL if hit_sl else OUT_TIMEOUT)
        o.exit_reason[k] = reason
        o.calendar_days[k] = calendar_days
        o.rolls[k] = int(charge.get("rolls") or 0)
    return o


def _unit_size(instrument: str, vehicle: str) -> float:
    """Units per position: a futures lot, or one share in cash delivery."""
    if vehicle == EQUITY_DELIVERY:
        return 1.0
    return float(get_spec(instrument).lot_size)


def vehicle_for(instrument: str) -> str:
    return EQUITY_DELIVERY if is_equity(instrument) else FUTURES


def sides_for(vehicle: str) -> tuple[int, ...]:
    """Which sides this vehicle can actually take overnight.

    Cash delivery cannot be held short in the Indian market, so an equity produces
    long candidates only. Modelling an equity short from a stock futures contract
    whose five-year history is not on disk would be inventing data.
    """
    return (LONG,) if vehicle == EQUITY_DELIVERY else (LONG, SHORT)


def execution_note() -> dict:
    """The frozen execution model, for the artefacts."""
    return {
        "signal": "daily close",
        "fill": f"open of the next session ({ENTRY_DELAY_BARS} bar delay)",
        "stop": f"{STOP_ATR}x the 14-day ATR known at the signal bar",
        "targets_r": {"t1": T1_R, "t2": T2_R, "t3": T3_R},
        "horizon_trading_days": HORIZON_DAYS,
        "same_bar_ambiguity": "resolved as the stop",
        "gap_policy": (
            "every bar is checked at its open first; a gap through either level "
            "fills at the open, in both directions"
        ),
        "timeout": "close of the last bar in the horizon",
        "costs": cost_model.model_note(),
    }


__all__ = [
    "resolve", "SwingOutcomes", "execution_note", "vehicle_for", "sides_for",
    "STOP_ATR", "T1_R", "T2_R", "T3_R", "HORIZON_DAYS", "ENTRY_DELAY_BARS",
    "EXIT_LABELS", "OUT_T1", "OUT_SL", "OUT_TIMEOUT",
]
