"""Flow Engine (Candle-Flow) — a SEPARATE, feature-flagged ADD-ON.

The user's ask, plainly: *"when the candle is green give me the buy signal and
stay while it's green; the moment it turns red / gives back points, show EXIT.
Auto-pick CE vs PE from whichever side is moving with the 1-min candle and switch
sides when it flips. Use candle patterns to judge whether the next candle is more
likely green or red. Remove the unwanted checks."*

So this engine is deliberately SIMPLE and fast — it has NONE of the trend-engine
gates (no VWAP-distance, no score, no room-to-target). It reads the raw 1-minute
candle flow and produces one of:

    BUY    — a decisive GREEN candle just printed in the moving direction → ride it
    HOLD   — still green / still with the flow → stay in
    EXIT   — the candle rolled RED against us (or the premium gave back points)
    SWITCH — the flow flipped side (e.g. CE → PE): exit the old leg, take the new
    WAIT   — no decisive flow yet

It auto-selects the side moving WITH the candle (CE when the underlying prints
green, PE when it prints red) and, while riding a leg, only FLIPS when a decisive
opposite candle appears (so a single indecision wick does not whipsaw it).

It is kept in its own module so it is COMPLETELY ISOLATED from the frozen
confirmation engine, Early Momentum, Early-Early and Quick Scalp. It reads market
data and produces its own advisory object; it mutates nothing else.

ADVISORY ONLY — it never places an order and never touches the frozen engine.
"""
from __future__ import annotations

import numpy as np

from app.analysis import structure as st
from app.config import settings
from app.execution import flow_economics
from app.models import Candle, FlowSignal, IndicatorSnapshot, OptionQuote, OptionType

# Candlestick patterns grouped by directional bias so we can project the next
# candle's likely colour and flag reversal candles that force an EXIT.
_BULLISH = {
    "MORNING_STAR", "THREE_WHITE_SOLDIERS", "BULLISH_ENGULFING", "PIERCING",
    "BULLISH_HARAMI", "TWEEZER_BOTTOM", "HAMMER", "MARUBOZU_UP",
}
_BEARISH = {
    "EVENING_STAR", "THREE_BLACK_CROWS", "BEARISH_ENGULFING", "DARK_CLOUD_COVER",
    "BEARISH_HARAMI", "TWEEZER_TOP", "SHOOTING_STAR", "MARUBOZU_DOWN",
}
# The subset of reversal patterns strong enough to force an immediate EXIT the
# moment they print against an open leg (not just lower the projection).
_STRONG_REVERSAL = {
    "EVENING_STAR", "THREE_BLACK_CROWS", "BEARISH_ENGULFING", "DARK_CLOUD_COVER",
    "SHOOTING_STAR", "MORNING_STAR", "BULLISH_ENGULFING", "PIERCING",
}


def _pattern_bias(pattern: str) -> str:
    if pattern in _BULLISH:
        return "BULLISH"
    if pattern in _BEARISH:
        return "BEARISH"
    return "NEUTRAL"


def _pick_atm(chain: list[OptionQuote], spot: float, side: OptionType) -> OptionQuote | None:
    same = [q for q in chain if q.option_type == side and q.premium > 0]
    if not same:
        return None
    return min(same, key=lambda q: abs(q.strike - spot))


def _leg_premium(chain: list[OptionQuote], symbol: str | None) -> float | None:
    if not symbol:
        return None
    for q in chain:
        if q.symbol == symbol and q.premium and q.premium > 0:
            return float(q.premium)
    return None


def _set_economics(out: FlowSignal, econ: dict) -> None:
    """Copy one economics assessment onto the advisory, unchanged."""
    out.economics_verdict = econ["verdict"]
    out.economics_cost_points = econ["cost_points"]
    out.economics_cost_source = econ["cost_spread_source"]
    out.economics_expected_points = econ["expected_move_points"]
    out.economics_ratio = econ["ratio"]
    out.economics_required = econ["required_multiple"]
    out.economics_note = econ["note"]


def _decisive(o: float, h: float, lo: float, c: float) -> tuple[bool, bool, float]:
    """Return (green, red, body_fraction). A candle is a decisive green/red only
    when its body is at least `flow_min_body_frac` of its range."""
    rng = max(h - lo, 1e-9)
    body_frac = abs(c - o) / rng
    decisive = body_frac >= settings.flow_min_body_frac
    return (decisive and c > o), (decisive and c < o), body_frac


def _streak(opens: np.ndarray, closes: np.ndarray, want_green: bool) -> int:
    """Consecutive with-flow candles counting back from the last bar."""
    n = 0
    for i in range(len(closes) - 1, -1, -1):
        green = closes[i] > opens[i]
        if (green and want_green) or ((not green) and (not want_green) and closes[i] < opens[i]):
            n += 1
        else:
            break
    return n


def _decisive_streak(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    want_green: bool,
) -> int:
    """Consecutive DECISIVE candles (body >= flow_min_body_frac of range) of the
    wanted colour, counting back from the last bar. Used as the de-whipsaw
    confirmation: we only flip side / fire a fresh BUY once this many decisive
    candles line up, so a single opposite candle can't twitch the signal."""
    n = 0
    for i in range(len(closes) - 1, -1, -1):
        g, r, _ = _decisive(float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i]))
        if (want_green and g) or ((not want_green) and r):
            n += 1
        else:
            break
    return n


def detect(
    candles: list[Candle],
    chain: list[OptionQuote],
    snap: IndicatorSnapshot,
    spot: float,
    rec: dict | None,
    instrument: str = "",
) -> FlowSignal:
    """Produce the Candle-Flow advisory. `rec` is the open flow record (or None)
    that carries the leg we are currently riding. Isolated + advisory-only."""
    out = FlowSignal(enabled=True)
    if spot is None or len(candles) < 4:
        out.note = "Warming up — need a few 1-min candles."
        return out

    opens = np.array([c.open for c in candles], dtype=float)
    highs = np.array([c.high for c in candles], dtype=float)
    lows = np.array([c.low for c in candles], dtype=float)
    closes = np.array([c.close for c in candles], dtype=float)

    o, h, lo, c = float(opens[-1]), float(highs[-1]), float(lows[-1]), float(closes[-1])
    cur_green, cur_red, body_frac = _decisive(o, h, lo, c)
    out.candle_color = "GREEN" if cur_green else ("RED" if cur_red else "FLAT")

    pattern = st.candle_pattern(opens, highs, lows, closes)
    bias = _pattern_bias(pattern)
    out.candle_pattern = pattern
    out.pattern_bias = bias

    # Short-trend fallback when the current candle is indecisive.
    net3 = c - float(opens[-3])
    raw_up = cur_green or (not cur_red and net3 >= 0)

    in_trade = rec is not None
    tracked_side = (
        OptionType.CALL if rec.get("side") == OptionType.CALL.value else OptionType.PUT
    ) if in_trade else None

    # De-whipsaw confirmation: how many consecutive DECISIVE candles must line up
    # in a direction before we flip side / fire a fresh BUY. 1 = the old twitchy
    # behaviour; 2+ ignores a single opposite candle so chop no longer flip-flops.
    confirm = max(1, settings.flow_confirm_candles)
    green_dec_streak = _decisive_streak(opens, highs, lows, closes, want_green=True)
    red_dec_streak = _decisive_streak(opens, highs, lows, closes, want_green=False)

    # --- decide the side (CE/PE moving with the candle) ---
    if in_trade and tracked_side is not None:
        # Reversal strength measured against the CURRENTLY-held leg (used for the
        # sticky "switch only on a strong confirmed reversal" rule).
        rev_against_tracked = (
            (bias == "BEARISH") if tracked_side == OptionType.CALL else (bias == "BULLISH")
        )
        strong_rev_tracked = pattern in _STRONG_REVERSAL and rev_against_tracked
        opp_streak = red_dec_streak if tracked_side == OptionType.CALL else green_dec_streak
        if settings.flow_switch_strong_only:
            # Sticky side: only flip on a strong confirmed reversal candle. A plain
            # opposite run just EXITS the leg (below) — it never auto-switches.
            flip = strong_rev_tracked
        else:
            # Old behaviour: flip once `confirm` decisive opposite candles line up.
            flip = opp_streak >= confirm
        side = (OptionType.PUT if tracked_side == OptionType.CALL else OptionType.CALL) if flip else tracked_side
    else:
        side = OptionType.CALL if raw_up else OptionType.PUT
    out.side = side

    # Consecutive decisive candles that AGREE with (favor) / oppose (against) the
    # chosen side — the confirmation counters the state machine reads below.
    favor_dec_streak = green_dec_streak if side == OptionType.CALL else red_dec_streak
    against_dec_streak = red_dec_streak if side == OptionType.CALL else green_dec_streak

    favor = cur_green if side == OptionType.CALL else cur_red
    against = cur_red if side == OptionType.CALL else cur_green
    reversal_against = (
        (bias == "BEARISH") if side == OptionType.CALL else (bias == "BULLISH")
    )
    strong_reversal = pattern in _STRONG_REVERSAL and reversal_against

    streak = _streak(opens, closes, want_green=(side == OptionType.CALL))
    out.green_streak = streak
    out.strength = round(min(100.0, 25.0 + streak * 15.0 + body_frac * 45.0), 0)

    # --- projection of the next candle's colour ---
    if reversal_against:
        out.projection = "LIKELY_RED"
    elif (bias == "BULLISH" and side == OptionType.CALL) or (bias == "BEARISH" and side == OptionType.PUT):
        out.projection = "LIKELY_GREEN"
    elif favor and streak >= 2:
        out.projection = "LIKELY_GREEN"
    elif against:
        out.projection = "LIKELY_RED"
    else:
        out.projection = "UNCLEAR"

    # --- the option leg + live P&L ---
    if in_trade:
        out.entry_premium = rec.get("entry_premium")
        out.peak_premium = rec.get("peak_premium")
        out.option_symbol = rec.get("option_symbol")
        live = _leg_premium(chain, rec.get("option_symbol"))
    else:
        q = _pick_atm(chain, spot, side)
        if q is not None:
            out.option_symbol = q.symbol
            out.entry_hint = round(q.premium, 1)
            # Entry economics of the leg we would actually buy, measured before
            # the state machine runs so a refusal can name its own numbers.
            _set_economics(out, flow_economics.assess(instrument, q, candles))
        live = q.premium if q is not None else None
    out.live_premium = round(live, 1) if live is not None else None
    if in_trade and live is not None and out.entry_premium:
        out.points = round(live - float(out.entry_premium), 1)
        peak = out.peak_premium if out.peak_premium is not None else out.entry_premium
        out.giveback = round(max(0.0, float(peak) - live), 1)
        out.peak_points = round(float(peak) - float(out.entry_premium), 1)
        # The level the EXIT actually fires at, stated as a premium. It trails the
        # peak, so it is not a stop in the entry-relative sense and must never be
        # shown as one — it can sit above entry once the leg has run.
        allowance = float(settings.flow_giveback_points)
        out.giveback_limit_points = round(allowance, 1)
        out.exit_trigger_premium = round(max(0.0, float(peak) - allowance), 1)
        out.points_to_exit = round(live - out.exit_trigger_premium, 1)
    out.in_trade = in_trade

    sidelbl = "CE" if side == OptionType.CALL else "PE"
    giveback_hit = out.giveback is not None and out.giveback >= settings.flow_giveback_points

    # Candles since the leg was opened (1 = the entry candle itself). Drives the
    # "sticky BUY" window so a one-candle BUY flash stays visible for a few bars.
    if in_trade:
        open_ct = rec.get("open_ctime")
        bars_after = sum(1 for cd in candles if open_ct is not None and cd.time > open_ct)
        out.bars_in_trade = bars_after + 1
    else:
        out.bars_in_trade = 1

    # --- state machine ---
    if in_trade and tracked_side is not None and side != tracked_side:
        # Flow flipped side → exit the old leg and take the new one. The new leg
        # is a fresh entry, so it faces the same economics test: when it cannot
        # pay for itself the old leg is still exited, but the flip is not taken.
        nq = _pick_atm(chain, spot, side)
        if nq is not None:
            _set_economics(out, flow_economics.assess(instrument, nq, candles))
        oldlbl = "CE" if tracked_side == OptionType.CALL else "PE"
        out.prev_side = tracked_side
        out.exit_now = True
        out.exit_signals = [f"Flow flipped {oldlbl}→{sidelbl}"]
        if (settings.flow_min_economics_enabled
                and out.economics_verdict == flow_economics.REFUSED):
            out.state = "EXIT"
            out.exit_reason = (
                f"Candle flipped decisively — exit {oldlbl}. The {sidelbl} leg is "
                f"not taken: {out.economics_note}."
            )
            out.headline = f"EXIT {oldlbl} · {sidelbl} leg too expensive"
            out.reasons.append(
                f"Flow is now {sidelbl}, but that leg's expected move is only "
                f"{out.economics_ratio:.1f}x its round trip against the "
                f"{out.economics_required:.0f}x this needs — flat instead."
            )
        else:
            out.switched = True
            out.state = "SWITCH"
            out.just_entered = True  # the new side is a fresh entry
            out.bars_in_trade = 1
            out.exit_reason = (
                f"Candle flipped decisively — exit {oldlbl}, switch to {sidelbl}."
            )
            out.headline = f"SWITCH → BUY {sidelbl} · exit {oldlbl}"
            out.reasons.append(
                f"1-min candle turned {'red' if tracked_side == OptionType.CALL else 'green'} "
                f"against {oldlbl} — flow is now {sidelbl}."
            )
    elif in_trade:
        # EXIT triggers. Giveback and a strong reversal PATTERN fire immediately
        # (protect profit fast). A plain colour turn against us only exits once
        # `confirm` decisive opposite candles line up — a single red pause in an
        # uptrend no longer kicks us out (that was the whipsaw).
        sigs: list[str] = []
        if against_dec_streak >= confirm:
            sigs.append(f"{against_dec_streak} decisive candle(s) against us")
        if strong_reversal:
            sigs.append(f"Reversal candle ({pattern.replace('_', ' ').title()})")
        if giveback_hit:
            sigs.append(f"Gave back {out.giveback:.0f} pts from peak")
        # Strength-band exit: the move is weakening (strength fell back below the
        # floor) — get out even before a red candle, and keep the locked profit.
        if settings.flow_strength_floor > 0 and out.strength < settings.flow_strength_floor:
            sigs.append(f"Strength {out.strength:.0f} < {settings.flow_strength_floor:.0f} — fading")
        if sigs:
            out.state = "EXIT"
            out.exit_now = True
            out.exit_signals = sigs
            out.exit_reason = "Flow is rolling over — " + ", ".join(sigs) + ". Get out."
            out.headline = f"EXIT {sidelbl} NOW"
        else:
            out.state = "HOLD"
            # Sticky BUY: for the first `flow_sticky_candles` bars of a fresh
            # entry keep flagging it as a just-entered BUY so the entry is visible.
            if out.bars_in_trade <= max(1, settings.flow_sticky_candles):
                out.just_entered = True
                out.headline = f"BUY {sidelbl} · JUST ENTERED (candle {out.bars_in_trade})"
            else:
                out.headline = f"HOLD {sidelbl} · riding the flow"
            note = (
                f"{sidelbl} flow intact — {streak} with-flow candle(s), "
                f"{('+' + format(out.points, '.0f')) if out.points is not None else '—'} pts so far. Stay in."
            )
            if against:
                # One opposite candle: caution, but not enough to exit yet.
                note = (
                    f"One candle against {sidelbl} — holding (need {confirm} in a row to exit). "
                    f"{('+' + format(out.points, '.0f')) if out.points is not None else '—'} pts."
                )
            out.reasons.append(note)
    else:
        # Fresh-entry gates: decisive with-flow streak, no reversal against us,
        # strength in the band (>= floor), and — when required — the candlestick
        # projection supporting the side (next candle likely green for the leg).
        strength_ok = (
            settings.flow_strength_floor <= 0 or out.strength >= settings.flow_strength_floor
        )
        pattern_ok = (not settings.flow_require_pattern) or out.projection == "LIKELY_GREEN"
        # A leg whose own round trip costs more than a third of the move it can
        # expect is refused: the cost audit found the leg COUNT, not the signal,
        # was what this book lost to. An unmeasurable leg is not refused.
        econ_refused = (
            settings.flow_min_economics_enabled
            and out.economics_verdict == flow_economics.REFUSED
        )
        if (
            favor
            and favor_dec_streak >= confirm
            and not reversal_against
            and strength_ok
            and pattern_ok
            and econ_refused
        ):
            out.state = "WAIT"
            out.headline = (
                f"WAIT {sidelbl} · move only {out.economics_ratio:.1f}x its cost"
                if out.economics_ratio is not None else f"WAIT {sidelbl} · uneconomic"
            )
            out.reasons.append(
                f"{sidelbl} flow is there, but the leg cannot pay for itself — "
                f"{out.economics_note}, i.e. "
                f"{out.economics_ratio:.1f}x against the "
                f"{out.economics_required:.0f}x this needs."
            )
        elif (
            favor
            and favor_dec_streak >= confirm
            and not reversal_against
            and strength_ok
            and pattern_ok
        ):
            out.state = "BUY"
            out.just_entered = True
            out.headline = f"BUY {sidelbl} · green flow"
            out.reasons.append(
                f"{favor_dec_streak} decisive {sidelbl} candle(s) in a row "
                f"(strength {out.strength:.0f}) — ride the flow."
            )
        elif favor and favor_dec_streak >= confirm and not reversal_against and not strength_ok:
            out.state = "WAIT"
            out.headline = f"WAIT {sidelbl} · strength {out.strength:.0f} < {settings.flow_strength_floor:.0f}"
            out.reasons.append(
                f"{sidelbl} flow forming but strength {out.strength:.0f} is below the "
                f"{settings.flow_strength_floor:.0f} floor — wait for a stronger push."
            )
        elif favor or favor_dec_streak >= 1:
            out.state = "WAIT"
            out.headline = f"WAIT {sidelbl} · flow forming"
            need = max(0, confirm - favor_dec_streak)
            out.reasons.append(
                f"{sidelbl} flow forming — need {need} more decisive candle(s) to confirm the BUY."
            )
        else:
            out.state = "WAIT"
            out.headline = "WAIT · no clear flow"
            out.reasons.append("No decisive candle flow yet — stand aside (choppy/sideways).")

    out.note = "Candle-Flow (fast, higher risk). Advisory only — no automatic order."
    return out
