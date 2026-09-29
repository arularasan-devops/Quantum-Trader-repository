"""Entry Optimizer — is NOW the best time to enter, or is a better entry likely?

ADVISORY. Reads the frozen engine's indicators + the recommended option quote and
returns ENTER_NOW / WAIT with an estimated better entry and the reasoning. It
never changes the BUY/WAIT decision itself.
"""
from __future__ import annotations

from app.config import settings
from app.models import Decision, ExecutionEntry, IndicatorSnapshot, OptionQuote, OptionType


def assess(
    decision: Decision,
    snap: IndicatorSnapshot,
    quote: OptionQuote | None,
) -> ExecutionEntry:
    premium = decision.current_premium
    want_call = decision.option_type == OptionType.CALL
    spot = decision.spot_price
    vwap = snap.vwap
    atr = snap.atr

    reasons: list[str] = []
    entry = ExecutionEntry(current_entry=premium)

    if spot is None or vwap is None or not atr or atr <= 0:
        entry.action = "ENTER_NOW"
        reasons.append("Insufficient VWAP/ATR data to assess timing — no wait advised.")
        entry.reasons = reasons
        return entry

    # Signed extension in the TRADE direction, in ATR units. Positive = price has
    # already run in our favour beyond VWAP (chasing risk); negative = below VWAP.
    raw = (spot - vwap) if want_call else (vwap - spot)
    ext = round(raw / atr, 2)
    entry.atr_from_vwap = ext

    extended = ext >= settings.exec_extended_atr
    entry.extended = extended

    # Pullback probability heuristic (0..100) from extension + trap/breakout state.
    prob = 25.0
    if ext >= 1.0:
        prob = min(90.0, 35.0 + (ext - 1.0) * 30.0)
    fake_up = snap.fake_signal in ("FAKE_BREAKOUT", "FAKE_BREAKDOWN")
    if fake_up:
        prob = min(95.0, prob + 15.0)
        reasons.append(f"Trap risk flagged ({snap.fake_signal}).")
    if snap.breakout == "BREAKOUT" and extended:
        reasons.append("Breakout already extended from VWAP — chasing risk.")
    # A pullback that's ALREADY underway in our direction is a GOOD entry, not a wait.
    pulling_back = (want_call and snap.pullback == "PULLBACK_DOWN") or (
        (not want_call) and snap.pullback == "PULLBACK_UP"
    )
    if pulling_back:
        prob = max(10.0, prob - 25.0)
        reasons.append("Price is already pulling back into the level — favourable entry.")
    entry.pullback_probability = round(prob, 0)

    if extended and prob >= 55.0 and not pulling_back and premium:
        # Estimate the better entry: expect a pullback toward VWAP of ~half the
        # current extension (bounded), converted to premium via the option delta.
        pullback_pts = min(raw, 0.5 * raw + 0.25 * atr)
        delta = abs(quote.delta) if quote and quote.delta else 0.4
        better = max(0.5, round(premium - delta * pullback_pts, 1))
        entry.action = "WAIT"
        entry.better_entry = better
        entry.wait_candles = 1 if ext < 2.0 else (2 if ext < 3.0 else 3)
        reasons.insert(
            0,
            f"Price extended {ext:.1f} ATR beyond VWAP; ~{prob:.0f}% chance of a pullback "
            f"to ≈₹{better} within {entry.wait_candles} candle(s).",
        )
    else:
        entry.action = "ENTER_NOW"
        if not reasons:
            reasons.append(
                f"Entry is not over-extended ({ext:.1f} ATR from VWAP) — timing is acceptable."
            )

    entry.reasons = reasons
    return entry
