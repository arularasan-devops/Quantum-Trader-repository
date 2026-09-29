"""Metrics, significance and multiple-testing correction (§15/§19/§23).

Three honesty properties matter more than the formulae:

* **win rate is never a ranking key.** It is reported because it is asked for,
  and it sits beside net expectancy, profit factor, drawdown and cost share,
  which are what decide anything;
* **the FDR denominator is the whole registered grid**, not the rows that looked
  interesting. `TOTAL_HYPOTHESES` is fixed in the pre-registration before any
  measurement, so it cannot shrink after the fact;
* **duplicate mechanisms are collapsed into event families.** Two grid rows that
  fire on the identical set of (session, symbol) cells are one discovery, not
  two, and are reported as one family — while both still count in the denominator,
  because both were tested.
"""
from __future__ import annotations

import hashlib
import math

import numpy as np

#: Label carried by every per-trade drawdown so it is never read as a book drawdown.
SEQUENCE_BASIS = "ADDITIVE_PER_POSITION_UNITS_NOT_A_CAPITAL_CONSTRAINED_BOOK"


def summary(net: np.ndarray, gross: np.ndarray | None = None) -> dict:
    """Trade-level statistics for one mechanism x exit x partition."""
    net = np.asarray(net, dtype=np.float64)
    net = net[np.isfinite(net)]
    if net.size == 0:
        return {"trades": 0}
    wins = net[net > 0]
    losses = net[net < 0]
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())
    out = {
        "trades": int(net.size),
        "net_expectancy": float(net.mean()),
        "net_median": float(np.median(net)),
        "win_rate": float(wins.size / net.size),
        "avg_win": float(wins.mean()) if wins.size else 0.0,
        "avg_loss": float(losses.mean()) if losses.size else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else math.inf,
        "std": float(net.std(ddof=1)) if net.size > 1 else 0.0,
        "total_net": float(net.sum()),
        "worst_trade": float(net.min()),
        "best_trade": float(net.max()),
    }
    out["t_stat"] = _t_stat(net)
    out["p_value"] = _p_value(out["t_stat"])
    if gross is not None:
        gross = np.asarray(gross, dtype=np.float64)
        gross = gross[np.isfinite(gross)]
        if gross.size:
            out["gross_expectancy"] = float(gross.mean())
            out["cost_drag"] = float(gross.mean() - net.mean())
            out["cost_consumption"] = (
                float((gross.mean() - net.mean()) / abs(gross.mean())) if gross.mean() else math.inf
            )
    return out


def _t_stat(values: np.ndarray) -> float:
    if values.size < 2:
        return 0.0
    deviation = values.std(ddof=1)
    if deviation == 0:
        return 0.0
    return float(values.mean() / (deviation / math.sqrt(values.size)))


def _p_value(t_stat: float) -> float:
    """One-sided normal p-value for a positive mean. Conservative for large n."""
    if t_stat <= 0:
        return 1.0
    return float(0.5 * math.erfc(t_stat / math.sqrt(2.0)))


def equity_curve(net: np.ndarray) -> dict:
    """Drawdown statistics over the trade sequence, additively and per unit.

    These trades overlap: a mechanism can hold twenty names at once, so treating
    the trade list as one compounding sequential ledger would invent a book that
    never existed and drive any negative-expectancy row to a ~100% drawdown that
    means nothing. The curve here is additive in units of one full position, which
    is the honest per-trade statistic. The drawdown a book would actually have
    suffered is produced by ``portfolio.simulate`` on dated, capital-constrained
    positions, and that is the number the promotion gate reads.
    """
    net = np.asarray(net, dtype=np.float64)
    net = net[np.isfinite(net)]
    if net.size == 0:
        return {
            "trade_sequence_drawdown_units": 0.0,
            "max_drawdown_trades": 0,
            "longest_losing_streak": 0,
            "drawdown_basis": SEQUENCE_BASIS,
        }
    equity = np.cumsum(net)
    peak = np.maximum.accumulate(equity)
    drawdown = equity - peak
    trough = int(np.argmin(drawdown))
    recovery = trough
    while recovery < equity.size and equity[recovery] < peak[trough]:
        recovery += 1
    streak = best = 0
    for value in net:
        streak = streak + 1 if value < 0 else 0
        best = max(best, streak)
    return {
        "trade_sequence_drawdown_units": float(-drawdown.min()),
        "max_drawdown_trades": int(recovery - trough),
        "longest_losing_streak": int(best),
        "total_return_units": float(equity[-1]),
        "drawdown_basis": SEQUENCE_BASIS,
    }


def benjamini_hochberg(p_values: list[float], alpha: float, denominator: int) -> list[bool]:
    """BH step-up over ``denominator`` hypotheses, not over the list length."""
    order = sorted(range(len(p_values)), key=lambda index: p_values[index])
    passed = [False] * len(p_values)
    threshold_rank = 0
    for rank, index in enumerate(order, start=1):
        if p_values[index] <= alpha * rank / denominator:
            threshold_rank = rank
    for rank, index in enumerate(order, start=1):
        if rank <= threshold_rank:
            passed[index] = True
    return passed


def event_family(rows: np.ndarray, columns: np.ndarray) -> str:
    """Stable hash of an event set, so identical mechanisms collapse (§19).

    The hash is over the event *set*: pairs are sorted first, so two mechanisms
    that fire on the same cells collapse into one family regardless of the order
    in which their events were enumerated.
    """
    pairs = np.stack(
        (np.asarray(rows, dtype=np.int64).ravel(), np.asarray(columns, dtype=np.int64).ravel())
    )
    order = np.lexsort((pairs[1], pairs[0]))
    blob = pairs[0][order].tobytes() + b"|" + pairs[1][order].tobytes()
    return hashlib.sha256(blob).hexdigest()[:16]


def concentration(net: np.ndarray, keys: np.ndarray) -> dict:
    """Share of total net profit contributed by the single largest key.

    ``keys`` is the per-trade symbol or year. A mechanism whose profit lives in
    one stock or one year is not a mechanism, and §23 asks for exactly this.
    """
    net = np.asarray(net, dtype=np.float64)
    finite = np.isfinite(net)
    net, keys = net[finite], np.asarray(keys)[finite]
    if net.size == 0:
        return {"top_share": 1.0, "top_key": "", "distinct_keys": 0, "positive_keys": 0}
    total = net.sum()
    unique = np.unique(keys)
    sums = {str(key): float(net[keys == key].sum()) for key in unique}
    positive = sum(1 for value in sums.values() if value > 0)
    if total <= 0:
        return {
            "top_share": math.inf,
            "top_key": "",
            "distinct_keys": int(unique.size),
            "positive_keys": positive,
            "per_key": sums,
        }
    top_key = max(sums, key=lambda name: sums[name])
    return {
        "top_share": float(sums[top_key] / total),
        "top_key": top_key,
        "distinct_keys": int(unique.size),
        "positive_keys": positive,
        "per_key": sums,
    }
