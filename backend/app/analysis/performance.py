"""Performance / Validation reporting.

This module is deliberately conservative about what it will claim. Per the
project's honesty requirement, it will ONLY compute real performance metrics
(win rate, profit factor, expectancy, drawdown, Sharpe, etc.) from trades that
were recorded against a REAL market feed. On the simulated demo feed — or before
enough real trades have been collected — it returns a clear

    "Real performance metrics unavailable until historical market data has been
     collected."

placeholder instead of fabricating impressive-looking numbers from synthetic
ticks. This keeps the dashboard trustworthy.
"""
from __future__ import annotations

import datetime as _dt
import math

UNAVAILABLE = (
    "Real performance metrics unavailable until historical market data "
    "has been collected."
)

# Every metric the report is designed to surface. Kept explicit so the UI can
# render the full grid (showing the placeholder) even with zero data.
METRIC_KEYS = [
    "win_rate", "profit_factor", "expectancy", "avg_winner", "avg_loser",
    "max_drawdown", "sharpe_ratio", "total_net_pnl", "trades", "avg_hold_minutes",
    "buy_accuracy", "wait_accuracy", "exit_accuracy",
    "trap_detection_accuracy", "recovery_accuracy", "premium_behaviour_accuracy",
]


def _empty(reason: str, is_real: bool, recorded: int, required: int) -> dict:
    return {
        "is_real_data": False,
        "data_ready": False,
        "reason": reason,
        "message": UNAVAILABLE,
        "trades_recorded": recorded,
        "trades_required": required,
        "metrics": {k: None for k in METRIC_KEYS},
        "trade_distribution": {},
        "monthly_performance": {},
    }


def report(journal: list[dict], *, is_real_feed: bool, min_trades: int) -> dict:
    """Build the performance report from the closed-trade journal.

    `is_real_feed` must be True only for a real broker feed. When False, or when
    there are too few real trades, the honest placeholder is returned.
    """
    trades = list(journal or [])
    recorded = len(trades)

    if not is_real_feed:
        return _empty(
            "Connected to the simulated demo feed — synthetic results are not "
            "reported as real performance.",
            False, recorded, min_trades,
        )
    if recorded < min_trades:
        return _empty(
            f"Only {recorded}/{min_trades} real closed trades collected so far.",
            True, recorded, min_trades,
        )

    # ---- enough REAL trades: compute the metrics ----
    pnls = [float(t.get("net_pnl", 0.0)) for t in trades]
    wins = [p for p in pnls if p >= 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    n = len(pnls)

    win_rate = 100.0 * len(wins) / n
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = -gross_loss / len(losses) if losses else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    expectancy = sum(pnls) / n

    # max drawdown off the cumulative equity curve
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    # Sharpe of per-trade returns (not annualised — trade-level, honest label)
    mean = expectancy
    var = sum((p - mean) ** 2 for p in pnls) / n
    std = math.sqrt(var)
    sharpe = (mean / std) if std > 0 else 0.0

    holds = [t["holding_minutes"] for t in trades if t.get("holding_minutes") is not None]
    avg_hold = sum(holds) / len(holds) if holds else None

    # BUY accuracy = share of BUY-triggered entries that closed profitably
    buy_trades = [t for t in trades if (t.get("context") or {}).get("signal") == "BUY"]
    buy_acc = (100.0 * sum(1 for t in buy_trades if t.get("win")) / len(buy_trades)
               if buy_trades else None)

    # distribution by market regime captured at entry
    dist: dict[str, dict] = {}
    for t in trades:
        regime = (t.get("context") or {}).get("market_regime") or "UNKNOWN"
        d = dist.setdefault(regime, {"trades": 0, "wins": 0, "net_pnl": 0.0})
        d["trades"] += 1
        d["wins"] += 1 if t.get("win") else 0
        d["net_pnl"] = round(d["net_pnl"] + float(t.get("net_pnl", 0.0)), 1)

    # monthly performance
    monthly: dict[str, dict] = {}
    for t in trades:
        ts = t.get("time")
        if not ts:
            continue
        key = _dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m")
        m = monthly.setdefault(key, {"trades": 0, "net_pnl": 0.0})
        m["trades"] += 1
        m["net_pnl"] = round(m["net_pnl"] + float(t.get("net_pnl", 0.0)), 1)

    metrics = {
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "expectancy": round(expectancy, 1),
        "avg_winner": round(avg_win, 1),
        "avg_loser": round(avg_loss, 1),
        "max_drawdown": round(max_dd, 1),
        "sharpe_ratio": round(sharpe, 2),
        "total_net_pnl": round(sum(pnls), 1),
        "trades": n,
        "avg_hold_minutes": round(avg_hold, 1) if avg_hold is not None else None,
        "buy_accuracy": round(buy_acc, 1) if buy_acc is not None else None,
        # These need labelled shadow-mode outcomes (recorded live over time)
        # before they can be computed honestly; surfaced as pending, not faked.
        "wait_accuracy": None,
        "exit_accuracy": None,
        "trap_detection_accuracy": None,
        "recovery_accuracy": None,
        "premium_behaviour_accuracy": None,
    }
    return {
        "is_real_data": True,
        "data_ready": True,
        "reason": "",
        "message": "",
        "trades_recorded": recorded,
        "trades_required": min_trades,
        "metrics": metrics,
        "trade_distribution": dist,
        "monthly_performance": monthly,
    }
