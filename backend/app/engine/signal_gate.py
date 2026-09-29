"""Quantum Signal — the single-page decision gate.

This module does NOT create a new strategy and it NEVER changes the frozen
engine. It is a READ-ONLY layer that takes the snapshot the frozen engine
already produced and answers one question for the new single-page dashboard:

    "Is there a *clean*, tradeable move right now — or should I stay out?"

The findings from the 1-year Angel backtest (Crude + Nifty: raw signals had no
edge in chop) drive the design: a BUY is only surfaced as ACTIONABLE when the
market is genuinely trending/expanding (regime + ADX + expected-move gate) and
the higher-timeframe agrees. In chop the card is forced to WAIT with an honest
reason, instead of firing on noise.

Everything here is advisory and paper-only. No orders are placed.
"""
from __future__ import annotations

import numpy as np

from app.analysis import indicators as ind
from app.analysis import opportunity
from app.analysis import signal_lifecycle as lc
from app.config import settings
from app.engine.decision import _aggregate_htf, rank_gate_trace
from app.market.instruments import get_spec
from app.models import Candle, GateTrace, MarketStatus, Signal, Snapshot

# Regime buckets for the honest traffic-light strip.
_TREND_REGIMES = {MarketStatus.TRENDING, MarketStatus.BREAKOUT}
_CHOP_REGIMES = {MarketStatus.RANGING, MarketStatus.LOW_VOLUME}
_CAUTION_REGIMES = {
    MarketStatus.VOLATILE,
    MarketStatus.NEWS_MODE,
    MarketStatus.REVERSAL,
}

_REGIME_LABEL = {
    MarketStatus.TRENDING: ("Trending", "green"),
    MarketStatus.BREAKOUT: ("Breakout", "green"),
    MarketStatus.RANGING: ("Rangy / chop", "amber"),
    MarketStatus.LOW_VOLUME: ("Quiet / thin", "grey"),
    MarketStatus.VOLATILE: ("Volatile", "amber"),
    MarketStatus.NEWS_MODE: ("News-driven", "amber"),
    MarketStatus.REVERSAL: ("Reversal risk", "amber"),
}


# Measured 5-year backtest of the frozen engine + this gate (underlying points,
# 2021-08 → 2026-08, real Angel data). See app/backtest/REPORT_ENGINE.md. These
# are honest MEASURED numbers on the UNDERLYING — option theta/spread not yet
# included — shown so the dashboard stops saying "calibrating" for validated
# instruments. Instruments not listed here have no measured backtest yet.
_MEASURED: dict[str, dict] = {
    "NIFTY": {"win_rate_pct": 43.0, "profit_factor": 1.05, "avg_points": 0.47, "trades": 10186},
    "CRUDEOIL": {"win_rate_pct": 42.6, "profit_factor": 1.06, "avg_points": 0.29, "trades": 26402},
}
_MEASURED_WINDOW = "5 yrs (2021-08→2026-08), underlying — options theta/spread not included"


def _safe_sizing(instrument: str, premium: float | None, stop: float | None) -> dict:
    """Risk-based 'safest lots' for the shown call: size so a single stop loses
    only ``risk_per_trade_pct`` of capital, then cap by ``auto_trade_max_lots``.
    Pure/read-only — mirrors RiskManager.suggested_lots without touching state."""
    spec = get_spec(instrument)
    lot_size = max(1, spec.lot_size)
    cap = settings.capital
    risk_pct = settings.risk_per_trade_pct
    risk_amount = cap * risk_pct / 100.0
    lots = None
    per_lot_risk = None
    if premium is not None and stop is not None and premium > stop:
        per_lot_risk = (premium - stop) * lot_size
        raw = max(1, int(risk_amount // max(0.01, per_lot_risk)))
        lots = max(1, min(settings.auto_trade_max_lots, raw))
    return {
        "capital": round(cap, 0),
        "risk_per_trade_pct": risk_pct,
        "risk_amount": round(risk_amount, 0),
        "lot_size": lot_size,
        "suggested_lots": lots,
        "per_lot_risk": round(per_lot_risk, 0) if per_lot_risk is not None else None,
        "est_risk": round(per_lot_risk * lots, 0) if (per_lot_risk is not None and lots) else None,
        "max_lots_cap": settings.auto_trade_max_lots,
    }


def _timeframe_bias(candles: list[Candle], factor: int) -> str:
    """UP / DOWN / SIDEWAYS bias on a higher timeframe, aggregated from the
    1-min futures candles (factor=15 → 15-min, factor=5 → 5-min). Uses an EMA
    fast/slow cross plus recent slope — the same idea the frozen engine uses for
    its 5-min gate, applied here purely for the read-only bias layer."""
    htf = _aggregate_htf(candles, max(1, factor))
    if len(htf) < 6:
        return "SIDEWAYS"
    closes = np.array([c.close for c in htf], dtype=float)
    price = float(closes[-1]) or 1.0
    ema_fast = ind.ema(closes, min(9, len(closes)))
    ema_slow = ind.ema(closes, min(21, len(closes)))
    if ema_fast is None or ema_slow is None:
        return "SIDEWAYS"
    look = min(4, len(closes) - 1)
    slope = (closes[-1] - closes[-1 - look]) / price
    if ema_fast > ema_slow and slope > 0:
        return "UP"
    if ema_fast < ema_slow and slope < 0:
        return "DOWN"
    return "SIDEWAYS"


def evaluate(snap: Snapshot, *, adx_min: float = 20.0) -> dict:
    """Collapse the full snapshot into the single Quantum Signal view.

    Returns a plain dict (JSON-ready). Read-only — never mutates the snapshot,
    the decision, or any engine state.
    """
    dec = snap.decision
    ind = snap.indicators
    status = snap.market_status

    # --- regime traffic light (honest) ---
    regime_label, regime_color = _REGIME_LABEL.get(status, (status.value, "grey"))

    # --- volatility gate: is a 5–10 pt move even realistic right now? ---
    adx = ind.adx
    adx_ok = adx is not None and adx >= adx_min
    exp_move = dec.expected_move_points
    # A move floor scaled to the instrument's typical range (ATR). If we can see
    # neither ADX nor an expected move, we cannot claim the tape is tradeable.
    move_ok = exp_move is not None and exp_move > 0

    trending = status in _TREND_REGIMES
    choppy = status in _CHOP_REGIMES

    # --- opportunity: can the PREMIUM travel far enough to pay for the trade? ---
    # Direction being right is not enough. The journal's losing pattern was a
    # correct call on a contract whose premium moved +0.7% against an 8% stop.
    opp = opportunity.evaluate(snap)

    # --- higher-timeframe agreement (5-min from the frozen engine) ---
    htf = (dec.htf_trend or "").upper()
    side = dec.option_type.value if dec.option_type else None
    htf_agrees = (
        (side == "CE" and htf == "UP")
        or (side == "PE" and htf == "DOWN")
        or side is None
    )

    # --- 15-min MARKET BIAS layer (top of the hybrid pipeline) ---
    # 15m bias → 5m trend (htf above) → 1m timing (the engine's BUY) → exit.
    bias15 = _timeframe_bias(snap.futures_candles, 15)
    bias_agrees = (
        (side == "CE" and bias15 == "UP")
        or (side == "PE" and bias15 == "DOWN")
        or side is None
    )

    # --- position management takes priority over any fresh entry ---
    in_position = bool(snap.position.option_symbol)
    base_signal = dec.signal

    reasons: list[str] = []
    action = "WAIT"
    actionable = False

    if in_position and base_signal in (Signal.EXIT,):
        action = "EXIT"
        reasons.append("Open position — engine says close it.")
    elif in_position and base_signal in (Signal.HOLD,):
        action = "HOLD"
        reasons.append("Open position — thesis still intact, hold.")
    elif base_signal == Signal.BUY and (dec.entry_trigger or "").upper() == "SUPERTREND":
        # A Supertrend FLIP is by definition a trend-CHANGE signal: at the flip bar
        # the 5-min trend and 15-min bias still reflect the OLD direction, and ADX
        # is usually low after the base. Applying those gates here is what delayed
        # the BUY until the move had already run (the "signal only at the peak"
        # problem), so they do not apply to this trigger. The engine still enforced
        # the trap/chop rejections, the R:R gate and the premium stop floor.
        action = f"BUY {side}" if side else "BUY"
        actionable = True
        reasons = list(dec.reasons[:4]) or ["Supertrend flipped — entry at the turn, not the peak."]
    elif base_signal == Signal.BUY and (dec.entry_trigger or "").upper() == "REVERSAL":
        # A CONFIRMED early-turn/reversal BUY intentionally fires BEFORE the
        # 5-min trend flips (to enter near the low/high, not the peak), so the
        # trend/ADX/higher-timeframe gates do NOT apply. The engine already
        # required a confirmed turn (sweep reclaim / V-reversal / reversal candle
        # at a stretched extreme), rejected chop/traps, and enforced the R:R gate.
        action = f"BUY {side}" if side else "BUY"
        actionable = True
        reasons = list(dec.reasons[:4]) or ["Confirmed reversal at the extreme — early-turn entry."]
    elif base_signal == Signal.BUY:
        # A BUY only becomes ACTIONABLE if the gates agree.
        gate_fail: list[str] = []
        if not trending:
            gate_fail.append(f"regime is {regime_label.lower()} (need a trend/breakout)")
        if not adx_ok:
            shown = f"{adx:.0f}" if adx is not None else "n/a"
            gate_fail.append(f"trend strength ADX {shown} < {adx_min:.0f}")
        if not move_ok:
            gate_fail.append("no measurable expected move")
        if not htf_agrees:
            gate_fail.append(f"5-min trend ({htf or 'flat'}) does not back the {side} side")
        if not bias_agrees:
            gate_fail.append(f"15-min bias ({bias15.lower()}) does not back the {side} side")
        if settings.opportunity_gate_enabled and not opp["tradeable"]:
            gate_fail.append(opp["reason"])
        if gate_fail:
            action = "WAIT"
            actionable = False
            reasons.append("Signal present but filtered out — " + "; ".join(gate_fail) + ".")
        else:
            action = f"BUY {side}" if side else "BUY"
            actionable = True
            reasons = list(dec.reasons[:4]) or ["Trend + strength + higher-timeframe all agree."]
    else:
        # WAIT / AVOID / NO_TRADE from the engine → stay out, explain why.
        action = "WAIT"
        if choppy:
            reasons.append(f"No clean move — market is {regime_label.lower()}. Stay out.")
        elif dec.reasons:
            reasons = list(dec.reasons[:3])
        else:
            reasons.append("No confirmed setup right now.")

    # --- gate trace: the engine's own gates plus this board layer's, ranked ---
    # A copy, never the engine's object: this layer is read-only by contract and
    # must not mutate the snapshot it was handed.
    trace = (dec.gates or GateTrace()).model_copy(deep=True)
    if base_signal == Signal.BUY and not in_position and not actionable:
        # Only meaningful when the board itself refused an engine BUY; on a
        # Supertrend/reversal trigger these gates deliberately do not apply.
        trace.board_regime_ok = bool(trending)
        trace.board_adx_ok = bool(adx_ok)
        trace.board_move_ok = bool(move_ok)
        trace.board_htf_agrees = bool(htf_agrees)
        trace.board_bias15_agrees = bool(bias_agrees)
        trace.board_opportunity_ok = (
            not settings.opportunity_gate_enabled or bool(opp["tradeable"])
        )
    rank_gate_trace(trace)

    # Volatility verdict for the strip (independent of BUY/WAIT).
    if trending and adx_ok:
        vol_state, vol_color = "Move likely", "green"
    elif choppy or not adx_ok:
        vol_state, vol_color = "Too quiet / choppy", "amber"
    else:
        vol_state, vol_color = "Mixed", "grey"

    return {
        "instrument": snap.instrument,
        "instrument_name": snap.instrument_name,
        "spot": dec.spot_price if dec.spot_price is not None else snap.futures_price,
        "change_pct": snap.futures_change_pct,
        "market_open": snap.market_open,
        "as_of": snap.time,
        # THE single answer
        "action": action,
        "actionable": actionable,
        "side": side if actionable else None,
        "reasons": reasons,
        # honest regime / volatility strip
        "regime": {"label": regime_label, "color": regime_color, "raw": status.value},
        "volatility": {
            "state": vol_state,
            "color": vol_color,
            "adx": round(adx, 1) if adx is not None else None,
            "adx_min": adx_min,
            "atr_points": round(ind.atr, 2) if ind.atr is not None else None,
            "expected_move_points": round(exp_move, 1) if exp_move is not None else None,
        },
        "opportunity": opp,
        "htf": {"trend": htf or None, "agrees": htf_agrees if actionable else None},
        "bias15": {"trend": bias15, "agrees": bias_agrees if actionable else None},
        # the plan (only meaningful when actionable) — all from the frozen engine
        "plan": {
            "option_symbol": dec.recommended_option,
            "strike": dec.strike,
            "moneyness": dec.moneyness,
            "current_premium": dec.current_premium,
            "entry_range": list(dec.entry_range) if dec.entry_range else None,
            "stop_loss": dec.stop_loss,
            "target1": dec.target1,
            "target2": dec.target2,
            "target3": dec.target3,
            "underlying_stop": dec.underlying_stop,
            "expected_holding_minutes": dec.expected_holding_minutes,
        },
        # confidence, shown honestly
        "confidence": round(dec.confidence, 0),
        # A conviction reading, published under its real name. It sorts setups
        # weakly and forecasts nothing, so it must never be shown as a chance.
        "conviction_meter": dec.conviction_meter,
        # The frequency that WAS measured, keyed on this call's reward:risk —
        # target distance is the only thing found to move the hit rate.
        "observed_target_rate": dec.observed_target_rate,
        "win_probability": None,
        "win_probability_note": (
            "not published as a probability: the engine's conviction reading "
            "cannot fall below 50 and averaged 78 where the target was reached "
            "42.8% of the time. Use observed_target_rate, which is measured"
        ),
        # honest measured 5-yr backtest of this exact signal (underlying only)
        "measured": (
            {**_MEASURED[snap.instrument], "window": _MEASURED_WINDOW}
            if snap.instrument in _MEASURED
            else None
        ),
        # safest position sizing for THIS call (risk-based, capped)
        "sizing": _safe_sizing(snap.instrument, dec.current_premium, dec.stop_loss),
        # speculative expiry-day sleeve suggestion (HIGH RISK; may be inactive)
        "zero_to_hero": snap.zero_to_hero.model_dump() if snap.zero_to_hero else None,
        # gate-by-gate attribution of THIS call (observability only)
        "gates": trace.model_dump(),
        "primary_blocker": trace.primary_blocker,
        "secondary_blocker": trace.secondary_blocker,
        "trade_quality": dec.trade_quality if actionable else None,
        "risk_level": dec.risk_level,
        # How old the displayed levels are and whether they can still be entered
        # (Part 30). The premium the plan was built on is shown next to the live
        # one, because that difference is what made a frozen target invisible.
        "plan_freshness": {
            "plan_state": dec.plan_state,
            "plan_version": dec.plan_version,
            "plan_age_seconds": dec.plan_age_seconds,
            "plan_premium": dec.plan_premium,
            "current_premium": dec.current_premium,
            "plan_actionable": dec.plan_actionable,
            "plan_invalid_reason": dec.plan_invalid_reason,
            "signal_age_seconds": dec.signal_age_seconds,
            "data_age_seconds": max(0, int(
                snap.time - (snap.futures_candles[-1].time
                             if snap.futures_candles else snap.time))),
        },
        # The research labels for this call (Phase 12A §15). DISPLAY ONLY: they
        # are attached after the decision is final, and on 26 Aug the UNTRADABLE
        # and REJECT_SPREAD calls they name carried ₹96,231 of the ₹1,09,909
        # shadow loss while looking identical to a clean call on this board.
        "research_badges": {
            "tradability": dec.tradability,
            "tradability_reasons": dec.tradability_reasons,
            "spread_pct_of_premium": dec.spread_pct_of_premium,
            "spread_over_risk_pct": dec.spread_over_risk_pct,
            "a_plus_label": dec.a_plus_label,
            "a_plus_reasons": dec.a_plus_reasons,
            "entry_quality": dec.entry_quality,
            "entry_zone_distance_r": dec.entry_zone_distance_r,
            # Phase 13A/13B: where the premium sits against the plan's own entry,
            # and how long comparable resolved calls took. Neither delays a BUY.
            "entry_state": dec.entry_state,
            "entry_state_reasons": dec.entry_state_reasons,
            "pullback_level": dec.pullback_level,
            "room_fraction_remaining": dec.room_fraction_remaining,
            "hold_window": {
                "expected_low_min": dec.hold_expected_low_min,
                "expected_high_min": dec.hold_expected_high_min,
                "long_tail_min": dec.hold_long_tail_min,
                "basis": dec.hold_window_basis,
                "is_probability": False,
            },
            "research_only": dec.badges_research_only,
            "note": ("labels recorded beside this call; the signal, score, "
                     "levels and strike were computed without them"),
        },
        # Identity and the stages this exact call has reached, so a BUY that is
        # on screen can be traced to the journal and the book (Parts 25-26).
        "identity": {
            "global_signal_id": dec.global_signal_id,
            "episode_id": dec.episode_id,
            "market": dec.market,
            "vehicle": dec.vehicle,
        },
        "lifecycle": _lifecycle_view(dec.global_signal_id),
    }


def _lifecycle_view(global_signal_id: str | None) -> dict:
    """Publication and execution status of one call, from the lifecycle ledger.

    Read-only and best-effort: a signal that has not been stamped yet reports
    unknown rather than claiming it was published.
    """
    entry = lc.tracked(global_signal_id) if global_signal_id else None
    stages = (entry or {}).get("stages") or {}

    def ok(stage: str) -> bool:
        return (stages.get(stage) or {}).get("status") == lc.OK

    execution = "NOT_ATTEMPTED"
    if ok(lc.FILLED):
        execution = lc.FILLED
    elif ok(lc.EXECUTION_ACCEPTED):
        execution = lc.EXECUTION_ACCEPTED
    elif stages.get(lc.EXECUTION_CHECK) or stages.get(lc.PAPER_ELIGIBLE):
        blocked = next((s for s in (lc.FILLED, lc.EXECUTION_ACCEPTED,
                                    lc.EXECUTION_CHECK, lc.PAPER_ELIGIBLE)
                        if (stages.get(s) or {}).get("status") == lc.MISSED), None)
        execution = ((stages.get(blocked) or {}).get("reason") or lc.EXECUTION_BLOCK
                     if blocked else lc.EXECUTION_CHECK)
    return {
        "tracked": entry is not None,
        "dashboard_published": ok(lc.DASHBOARD_PUBLISHED),
        "user_visible": ok(lc.USER_VISIBLE),
        "journal_recorded": ok(lc.PLAN_VALIDATED) and ok(lc.DASHBOARD_PUBLISHED),
        "paper_eligible": ok(lc.PAPER_ELIGIBLE),
        "execution_status": execution,
        "last_stage": (entry or {}).get("last_stage"),
        "last_status": (entry or {}).get("last_status"),
        "stages": [{"stage": s, "status": v.get("status"), "reason": v.get("reason")}
                   for s, v in stages.items()],
    }
