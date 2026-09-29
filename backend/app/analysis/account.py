"""Paper-account summary + monthly report generation.

Aggregates the durable, cross-instrument closed paper trades (persisted by
``storage.HistoryStore``) into an account view: starting capital, realised P&L,
equity, per-day and per-month breakdowns, and win statistics. Also renders a CSV
monthly statement.

Paper-only: the "balance/equity" here is notional (starting capital + cumulative
realised paper P&L). It is never a real broker balance and never reflects a live
order — the app does not place live orders.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io


def _day(ts: int) -> str:
    return _dt.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")


def _month(ts: int) -> str:
    return _dt.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m")


def _stats(trades: list[dict]) -> dict:
    n = len(trades)
    pnls = [float(t.get("net_pnl") or 0.0) for t in trades]
    wins = [p for p in pnls if p >= 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "net_pnl": round(sum(pnls), 1),
        "gross_profit": round(gross_win, 1),
        "gross_loss": round(-gross_loss, 1),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_pnl": round(sum(pnls) / n, 1) if n else 0.0,
    }


def summary(trades: list[dict], *, capital: float, is_real_feed: bool) -> dict:
    """Full account summary from all closed paper trades (oldest-first)."""
    overall = _stats(trades)
    realised = overall["net_pnl"]

    by_day_map: dict[str, list[dict]] = {}
    by_month_map: dict[str, list[dict]] = {}
    for t in trades:
        by_day_map.setdefault(_day(t.get("ts", 0)), []).append(t)
        by_month_map.setdefault(_month(t.get("ts", 0)), []).append(t)

    # per-day rows with a running equity curve
    by_day = []
    equity = capital
    for day in sorted(by_day_map):
        s = _stats(by_day_map[day])
        equity += s["net_pnl"]
        by_day.append({"date": day, **s, "equity": round(equity, 1)})

    by_month = []
    for month in sorted(by_month_map):
        by_month.append({"month": month, **_stats(by_month_map[month])})

    recent = [
        {
            "date": _dt.datetime.utcfromtimestamp(int(t.get("ts", 0))).strftime("%Y-%m-%d %H:%M"),
            "instrument": t.get("instrument"),
            "option": t.get("option"),
            "option_type": t.get("option_type"),
            "lots": t.get("lots"),
            "entry": t.get("entry"),
            "exit": t.get("exit"),
            "net_pnl": round(float(t.get("net_pnl") or 0.0), 1),
            "win": bool(t.get("win")),
            "holding_minutes": t.get("holding_minutes"),
        }
        for t in trades[-50:][::-1]
    ]

    return {
        "mode": "paper",
        "is_real_feed": is_real_feed,
        "note": (
            "Paper account — notional balance = starting capital + cumulative "
            "realised paper P&L. Not a real broker balance; no live orders are placed."
        ),
        "starting_capital": round(capital, 1),
        "realised_pnl": realised,
        "equity": round(capital + realised, 1),
        "return_pct": round(realised / capital * 100, 2) if capital else 0.0,
        "overall": overall,
        "by_day": by_day,
        "by_month": by_month,
        "recent_trades": recent,
        "months_available": sorted(by_month_map.keys(), reverse=True),
    }


def monthly_csv(trades: list[dict], month: str, *, capital: float) -> str:
    """Render a CSV statement for a single month (``YYYY-MM``)."""
    rows = [t for t in trades if _month(t.get("ts", 0)) == month]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"Quantum Trader — PAPER account statement — {month}"])
    w.writerow(["(Notional paper trades only — not a real broker statement)"])
    w.writerow([])
    w.writerow([
        "Date/Time (UTC)", "Instrument", "Option", "Type", "Lots",
        "Entry", "Exit", "Net P&L", "Result", "Hold (min)",
    ])
    for t in rows:
        w.writerow([
            _dt.datetime.utcfromtimestamp(int(t.get("ts", 0))).strftime("%Y-%m-%d %H:%M"),
            t.get("instrument"), t.get("option"), t.get("option_type"),
            t.get("lots"), t.get("entry"), t.get("exit"),
            round(float(t.get("net_pnl") or 0.0), 1),
            "WIN" if t.get("win") else "LOSS", t.get("holding_minutes"),
        ])
    s = _stats(rows)
    w.writerow([])
    w.writerow(["Summary"])
    w.writerow(["Trades", s["trades"]])
    w.writerow(["Wins", s["wins"]])
    w.writerow(["Losses", s["losses"]])
    w.writerow(["Win rate %", s["win_rate"]])
    w.writerow(["Gross profit", s["gross_profit"]])
    w.writerow(["Gross loss", s["gross_loss"]])
    w.writerow(["Profit factor", s["profit_factor"]])
    w.writerow(["Net P&L", s["net_pnl"]])
    w.writerow(["Starting capital", round(capital, 1)])
    return buf.getvalue()
