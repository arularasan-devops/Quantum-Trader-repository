"""Portfolio construction (§13, §21-§23) and the §17 benchmark comparison.

Trade-level expectancy and a portfolio's net CAGR are different questions, and a
mechanism can pass the first and fail the second — too few simultaneous
candidates to fill the slots, or too many on the same day so the slots are
rationed, or a drawdown that a per-trade average hides. So the surviving rows are
simulated as an actual book:

* long only, cash only, no leverage, no margin, no shorting;
* at most one open position per symbol;
* at most ``size`` positions open at once, filled at the session's open;
* candidates in excess of free slots are rationed deterministically (highest
  prior turnover first) and the rejected ones are counted, not dropped silently;
* a position's exit session and price come from the same exit resolution the
  trade-level study used, so the two layers cannot disagree;
* a trade is skipped when cash is short, and that is reported as
  ``skipped_no_cash`` rather than financed.

`PORTFOLIO_CONSTRUCTION_EFFECT` is then the difference between the portfolio's
net CAGR and what the trade-level expectancy alone would suggest, which is what
§9 asks to be kept separate.
"""
from __future__ import annotations

import numpy as np

from . import PAPER_CAPITAL_INR, WEIGHTING_METHODS
from .exits import Outcome
from .panel import Panel
from ..nse.indices import NIFTY_50


def simulate(
    panel: Panel,
    signals: np.ndarray,
    outcome: Outcome,
    *,
    size: int,
    weighting: str = "EQUAL_WEIGHT",
    capital: float = PAPER_CAPITAL_INR,
    cost_multiplier: float = 1.0,
    start_row: int = 0,
    end_row: int | None = None,
) -> dict:
    """Run the book over ``[start_row, end_row)`` and return its statistics."""
    if weighting not in WEIGHTING_METHODS:
        raise ValueError(f"unknown weighting {weighting}")
    days = panel.shape[0]
    end_row = days if end_row is None else end_row
    from .tradecosts import net_returns, regime_arrays

    rates = regime_arrays(panel.dates)
    rank_metric = panel.turnover  # prior-session turnover is already causal below
    volatility = panel.features["vol_20"]

    cash = capital
    equity_curve = np.full(days, np.nan)
    open_positions: dict[int, dict] = {}
    trades: list[dict] = []
    skipped_no_cash = 0
    rationed = 0
    invested_sessions = 0
    max_concurrent = 0
    min_cash = capital

    for row in range(start_row, end_row):
        # 1. close positions whose exit session is today
        for column in [key for key, item in open_positions.items() if item["exit_row"] == row]:
            item = open_positions.pop(column)
            net = float(
                net_returns(
                    entry_price=np.array([item["entry_price"]]),
                    exit_price=np.array([item["exit_price"]]),
                    buy_rate_index=np.array([item["entry_row"]]),
                    sell_rate_index=np.array([row]),
                    rates=rates,
                    multiplier=cost_multiplier,
                    notional=item["notional"],
                )[0]
            )
            cash += item["notional"] * (1.0 + net)
            trades.append(
                {
                    "symbol": str(panel.symbols[column]),
                    "entry_date": str(panel.dates[item["entry_row"]]),
                    "exit_date": str(panel.dates[row]),
                    "net_return": net,
                    "net_inr": item["notional"] * net,
                    "holding_sessions": int(row - item["entry_row"] + 1),
                }
            )

        # 2. open new positions into free slots
        free = size - len(open_positions)
        if free > 0:
            candidates = np.nonzero(signals[row])[0]
            candidates = np.array([column for column in candidates if column not in open_positions])
            if candidates.size:
                exit_rows = row + np.maximum(outcome.holding[row, candidates].astype(np.int64), 1) - 1
                usable = (
                    np.isfinite(outcome.entry_price[row, candidates])
                    & np.isfinite(outcome.exit_price[row, candidates])
                    & (outcome.reason[row, candidates] > 0)
                )
                candidates, exit_rows = candidates[usable], exit_rows[usable]
            if candidates.size:
                order = np.argsort(-np.nan_to_num(rank_metric[row, candidates], nan=-np.inf))
                candidates, exit_rows = candidates[order], exit_rows[order]
                if candidates.size > free:
                    rationed += int(candidates.size - free)
                for column, exit_row in zip(candidates[:free], exit_rows[:free]):
                    weight = 1.0 / size
                    if weighting == "INVERSE_VOLATILITY":
                        own = volatility[row, column]
                        weight = 1.0 / size if not np.isfinite(own) or own <= 0 else min(1.0 / size, 0.01 / own / size)
                    notional = min((cash + _open_value(open_positions)) * weight, cash)
                    if notional <= 0 or notional > cash:
                        skipped_no_cash += 1
                        continue
                    cash -= notional
                    open_positions[int(column)] = {
                        "entry_row": row,
                        "exit_row": int(exit_row),
                        "entry_price": float(outcome.entry_price[row, column]),
                        "exit_price": float(outcome.exit_price[row, column]),
                        "notional": float(notional),
                    }

        # 3. mark to market on today's close
        held = 0.0
        for column, item in open_positions.items():
            mark = panel.close[row, column]
            if not np.isfinite(mark) or mark <= 0:
                mark = item["entry_price"]
            held += item["notional"] * (mark / item["entry_price"])
        equity_curve[row] = cash + held
        if open_positions:
            invested_sessions += 1
        max_concurrent = max(max_concurrent, len(open_positions))
        min_cash = min(min_cash, cash)

    # Force-close anything still open at the final session, at its last mark.
    for column, item in list(open_positions.items()):
        mark = panel.close[end_row - 1, column]
        if not np.isfinite(mark) or mark <= 0:
            mark = item["entry_price"]
        cash += item["notional"] * (mark / item["entry_price"])
        trades.append(
            {
                "symbol": str(panel.symbols[column]),
                "entry_date": str(panel.dates[item["entry_row"]]),
                "exit_date": str(panel.dates[end_row - 1]),
                "net_return": float(mark / item["entry_price"] - 1.0),
                "net_inr": float(item["notional"] * (mark / item["entry_price"] - 1.0)),
                "holding_sessions": int(end_row - 1 - item["entry_row"] + 1),
                "status": "MARKED_OPEN_AT_WINDOW_END",
            }
        )
        open_positions.pop(column)

    series = equity_curve[start_row:end_row]
    return {
        "size": size,
        "weighting": weighting,
        "cost_multiplier": cost_multiplier,
        "window": [str(panel.dates[start_row]), str(panel.dates[end_row - 1])],
        "trades": len(trades),
        "skipped_no_cash": skipped_no_cash,
        "rationed_candidates": rationed,
        "invested_session_share": float(invested_sessions / max(end_row - start_row, 1)),
        "max_concurrent": int(max_concurrent),
        "min_cash": float(min_cash),
        # Structural, not measured: the book has one entry path and it buys.
        "shorts": 0,
        "leverage": "NONE_CASH_ONLY",
        **_curve_stats(series, panel.dates[start_row:end_row], capital),
        "per_trade": trades,
    }


def _open_value(open_positions: dict) -> float:
    return float(sum(item["notional"] for item in open_positions.values()))


def _curve_stats(series: np.ndarray, dates: np.ndarray, capital: float) -> dict:
    valid = np.isfinite(series)
    if not valid.any():
        return {"net_cagr": 0.0, "max_drawdown": 0.0, "final_equity": capital}
    values = series[valid]
    stamps = dates[valid]
    peak = np.maximum.accumulate(values)
    drawdown = values / peak - 1.0
    sessions = values.size
    total = values[-1] / capital
    years = sessions / 252.0
    cagr = float(total ** (1.0 / years) - 1.0) if years > 0 and total > 0 else -1.0
    months = _period_returns(values, stamps, 7)
    quarters = _period_returns(values, stamps, "quarter")
    return {
        "net_cagr": cagr,
        "total_return": float(total - 1.0),
        "max_drawdown": float(-drawdown.min()),
        "max_drawdown_date": str(stamps[int(np.argmin(drawdown))]),
        "worst_month": min(months.values()) if months else 0.0,
        "worst_quarter": min(quarters.values()) if quarters else 0.0,
        "positive_months": sum(1 for value in months.values() if value > 0),
        "total_months": len(months),
        "final_equity": float(values[-1]),
        "sessions": int(sessions),
    }


def _period_returns(values: np.ndarray, dates: np.ndarray, key) -> dict:
    labels = []
    for stamp in dates:
        text = str(stamp)
        if key == "quarter":
            labels.append(f"{text[:4]}Q{(int(text[5:7]) - 1) // 3 + 1}")
        else:
            labels.append(text[:key])
    out: dict[str, float] = {}
    labels_array = np.array(labels)
    for label in dict.fromkeys(labels):
        inside = labels_array == label
        block = values[inside]
        out[label] = float(block[-1] / block[0] - 1.0) if block.size > 1 and block[0] else 0.0
    return out


def benchmark(panel: Panel, *, start_row: int = 0, end_row: int | None = None, name: str = NIFTY_50) -> dict:
    """Buy-and-hold on the published index close over the same sessions (§17)."""
    series = panel.index_close.get(name)
    end_row = panel.shape[0] if end_row is None else end_row
    if series is None or not np.isfinite(series[start_row:end_row]).any():
        return {"benchmark": name, "status": "BENCHMARK_UNAVAILABLE"}
    window = series[start_row:end_row]
    valid = np.isfinite(window)
    values = window[valid]
    stamps = panel.dates[start_row:end_row][valid]
    stats = _curve_stats(values / values[0] * PAPER_CAPITAL_INR, stamps, PAPER_CAPITAL_INR)
    return {"benchmark": name, "status": "BUY_AND_HOLD", **stats}
