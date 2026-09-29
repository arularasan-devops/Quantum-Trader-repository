"""Signal Delay Analyzer — how much of the underlying move had already happened
before the frozen confirmation BUY fired?

ADVISORY / measurement only. Anchors the current up-leg at the most recent swing
low, measures the % of the swing already gone at the confirmation BUY vs the
remaining room to the swing high / resistance, and — if the Early Momentum engine
fired earlier in the same leg — quantifies how much earlier that entry was.
"""
from __future__ import annotations

import numpy as np

from app.analysis import structure as st
from app.config import settings
from app.models import Candle, IndicatorSnapshot, OptionType, SignalDelay


def analyze(
    candles: list[Candle],
    snap: IndicatorSnapshot,
    spot: float,
    side: OptionType | None,
    early_entry_premium: float | None = None,
    confirmation_premium: float | None = None,
    candles_between: int | None = None,
) -> SignalDelay:
    """`spot`, swing anchors and move %s are UNDERLYING prices; the entry
    comparison (`early_entry_premium` vs `confirmation_premium`) is in OPTION
    PREMIUM units — the two are never mixed."""
    out = SignalDelay()
    if spot is None or len(candles) < 12:
        out.note = "Not enough bars to measure signal delay."
        return out

    highs = np.array([c.high for c in candles], dtype=float)
    lows = np.array([c.low for c in candles], dtype=float)
    sh, sl = st.swing_points(highs, lows)
    up = side != OptionType.PUT  # default to the long/up case

    if up:
        move_start = sl[-1][1] if sl else float(lows[-20:].min())
        peak = max(spot, sh[-1][1]) if sh else float(highs.max())
        peak = max(peak, snap.resistance or peak)
        move_total = peak - move_start
        gone = spot - move_start
    else:
        move_start = sh[-1][1] if sh else float(highs[-20:].max())
        trough = min(spot, sl[-1][1]) if sl else float(lows.min())
        trough = min(trough, snap.support or trough)
        move_total = move_start - trough
        gone = move_start - spot
        peak = trough

    if move_total <= 0 or gone <= 0:
        out.note = "No clean directional leg to measure yet."
        return out

    missed = max(0.0, min(100.0, gone / move_total * 100.0))
    out.available = True
    out.move_start_price = round(move_start, 2)
    out.buy_price = round(spot, 2)
    out.peak_price = round(peak, 2)
    out.missed_move_pct = round(missed, 1)
    out.remaining_move_pct = round(100.0 - missed, 1)
    out.late = missed >= settings.signal_delay_late_pct

    # Entry comparison — PREMIUM vs PREMIUM only (never premium-vs-underlying).
    if confirmation_premium is not None and confirmation_premium > 0:
        out.confirmation_entry_premium = round(confirmation_premium, 2)
    if (
        early_entry_premium is not None and early_entry_premium > 0
        and out.confirmation_entry_premium is not None
    ):
        out.early_entry_premium = round(early_entry_premium, 2)
        diff = out.confirmation_entry_premium - out.early_entry_premium
        out.premium_diff = round(diff, 2)
        out.premium_diff_pct = round(diff / out.confirmation_entry_premium * 100.0, 2)
        out.candles_between = candles_between
        out.early_would_help = diff > 0
        cb = f", {candles_between} candles earlier" if candles_between is not None else ""
        if diff > 0:
            out.note = (
                f"Confirmation BUY entered with ~{missed:.0f}% of the move gone at premium "
                f"₹{out.confirmation_entry_premium:.1f}; the Early Momentum engine flagged this "
                f"leg at ₹{out.early_entry_premium:.1f}{cb} — ₹{diff:.1f} ({out.premium_diff_pct:.1f}%) cheaper."
            )
        else:
            out.note = (
                f"Confirmation BUY premium ₹{out.confirmation_entry_premium:.1f} was not higher "
                f"than the earlier Early Momentum premium ₹{out.early_entry_premium:.1f} — no entry "
                f"improvement on this leg."
            )
    else:
        out.note = (
            f"~{missed:.0f}% of the swing was already gone at the confirmation BUY "
            f"({out.remaining_move_pct:.0f}% remaining to {peak:.1f})."
        )
    return out
