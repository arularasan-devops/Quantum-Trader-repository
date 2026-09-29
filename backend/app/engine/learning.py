"""Journal-driven learning stats.

Scores every closed trade to surface the metrics that matter for improving:
win rate, average win/loss, profit factor and expectancy. This is the feedback
loop — over time these tell you whether the signals are actually working and
where the edge is leaking.
"""
from __future__ import annotations

from app.models import LearningStats


def summarize(journal: list[dict]) -> LearningStats:
    pnls = [float(t.get("net_pnl", 0.0)) for t in journal]
    if not pnls:
        return LearningStats(note="No closed trades yet — stats appear after your first exit.")
    wins = [p for p in pnls if p >= 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    win_rate = len(wins) / len(pnls)
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
    pf = gross_win / gross_loss if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
    return LearningStats(
        total_trades=len(pnls),
        wins=len(wins),
        losses=len(losses),
        win_rate=round(win_rate * 100, 1),
        avg_win=round(avg_win, 1),
        avg_loss=round(avg_loss, 1),
        profit_factor=round(pf, 2),
        expectancy=round(expectancy, 1),
        best=round(max(pnls), 1),
        worst=round(min(pnls), 1),
        note="Expectancy = avg ₹ per trade. Profit factor > 1 means net profitable.",
    )
