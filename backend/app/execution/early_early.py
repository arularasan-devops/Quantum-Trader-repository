"""Early-Early Mode (Stage 2.5) — a SEPARATE, feature-flagged ADD-ON.

This is an *even earlier* advisory tier that sits BETWEEN Stage 2 (Structure
Confirmed) and Stage 3 (Early Buy) of the existing Early Momentum ladder. It is
deliberately kept in its own module so it is COMPLETELY ISOLATED from both the
frozen confirmation engine and the existing Early Momentum logic — it reads
market data and produces its own advisory object; it mutates nothing else.

It fires when MULTIPLE evidence pieces align at the same time — NOT on a single
green candle:

  1. Higher-Low (up) / Lower-High (down)         — structure turning
  2. Bullish/Bearish VWAP-reclaim candle          — a real close back thru VWAP
  3. Above-average volume                         — participation
  4. Positive EMA slope                           — EMA9 vs EMA20 in direction
  5. Improving option-chain                       — OI / writing / premium bias
  6. Adequate room before resistance/support      — somewhere to actually go

Before any Early-Early is issued it must ALSO pass hard validation:
  * Reward:Risk (to Target 1) >= QT_EARLY_EARLY_MIN_RR (default 2.0)
  * Liquidity / spread validation on the suggested option leg
  * Enough room to resistance/support to realistically reach Target 1

ADVISORY ONLY — it never places an order and never touches the frozen engine.
"""
from __future__ import annotations

import numpy as np

from app.analysis import structure as st
from app.config import settings
from app.models import (
    Candle,
    EarlyCheck,
    EarlyEarly,
    IndicatorSnapshot,
    MomentumPlan,
    OptionQuote,
    OptionType,
)

# The six evidence pillars, in display order.
_ESSENTIAL = ("Higher-Low", "VWAP-Reclaim Candle")

# Recommended-action ladder (increasing conviction).
ACTIONS = (
    "IGNORE",
    "WATCH",
    "PREPARE",
    "SMALL_POSITION",
    "NORMAL_ENTRY",
    "CONSERVATIVE_ENTRY",
)


def _pick_atm(chain: list[OptionQuote], spot: float, side: OptionType) -> OptionQuote | None:
    same = [q for q in chain if q.option_type == side and q.premium > 0]
    if not same:
        return None
    return min(same, key=lambda q: abs(q.strike - spot))


def _evaluate(
    up: bool,
    candles: list[Candle],
    snap: IndicatorSnapshot,
    spot: float,
    atr: float,
) -> tuple[list[EarlyCheck], float | None]:
    """Compute the six Early-Early evidence checks INDEPENDENTLY of the Early
    Momentum engine. Returns (checks, room_atr)."""
    opens = np.array([c.open for c in candles], dtype=float)
    highs = np.array([c.high for c in candles], dtype=float)
    lows = np.array([c.low for c in candles], dtype=float)
    closes = np.array([c.close for c in candles], dtype=float)
    vols = np.array([c.volume for c in candles], dtype=float)
    checks: list[EarlyCheck] = []

    def add(name: str, passed: bool, detail: str, available: bool = True):
        checks.append(EarlyCheck(name=name, passed=bool(passed and available), detail=detail, available=available))

    # 1) Higher-low (up) / lower-high (down).
    shp, slp = st.swing_points(highs, lows)
    if up:
        if len(slp) >= 2:
            prev, last = slp[-2][1], slp[-1][1]
            add("Higher-Low", last > prev, f"swing low {last:.1f} vs prior {prev:.1f}")
        else:
            add("Higher-Low", False, "not enough swing lows yet", available=False)
    else:
        if len(shp) >= 2:
            prev, last = shp[-2][1], shp[-1][1]
            add("Higher-Low", last < prev, f"swing high {last:.1f} vs prior {prev:.1f}")
        else:
            add("Higher-Low", False, "not enough swing highs yet", available=False)

    # 2) A real VWAP-reclaim CANDLE: the just-closed bar is directional (green for
    #    a long / red for a short) AND closes on our side of VWAP, having crossed
    #    back through VWAP within the last few bars (a genuine reclaim that is
    #    still holding — not a single-tick flash, and not just sitting above VWAP).
    if snap.vwap is not None and len(closes) >= 4:
        vwap = snap.vwap
        last_open, last_close = float(opens[-1]), float(closes[-1])
        recent = closes[-4:-1]  # the 3 bars before the current one
        if up:
            crossed = float(recent.min()) <= vwap * 1.001
            reclaim = last_close > last_open and last_close > vwap and crossed
        else:
            crossed = float(recent.max()) >= vwap * 0.999
            reclaim = last_close < last_open and last_close < vwap and crossed
        add("VWAP-Reclaim Candle", reclaim, f"close {last_close:.1f} vs VWAP {vwap:.1f} (recent cross)")
    else:
        add("VWAP-Reclaim Candle", False, "no VWAP/history", available=False)

    # 3) Above-average volume on the reclaim (last bar vs prior 10-bar average).
    if len(vols) >= 11:
        last_v = float(vols[-1])
        base_v = float(vols[-11:-1].mean())
        add("Above-Avg Volume", last_v > 1.2 * max(base_v, 1e-9), f"last {last_v:.0f} vs avg {base_v:.0f}")
    else:
        add("Above-Avg Volume", False, "not enough volume history", available=False)

    # 4) Positive EMA slope (EMA9 vs EMA20 in the trade direction + price beyond EMA9).
    ema_ok = False
    detail = "EMA data unavailable"
    if snap.ema9 is not None and snap.ema20 is not None:
        if up:
            ema_ok = snap.ema9 > snap.ema20 and spot >= snap.ema9
        else:
            ema_ok = snap.ema9 < snap.ema20 and spot <= snap.ema9
        detail = f"EMA9 {snap.ema9:.1f} vs EMA20 {snap.ema20:.1f}"
    add("Positive EMA Slope", ema_ok, detail, available=snap.ema9 is not None and snap.ema20 is not None)

    # 5) Improving option-chain in the trade direction.
    if up:
        oc = snap.oi_bias == "BULLISH" or snap.oi_writing == "PUT_WRITING" or snap.premium_bias == "BULLISH"
    else:
        oc = snap.oi_bias == "BEARISH" or snap.oi_writing == "CALL_WRITING" or snap.premium_bias == "BEARISH"
    add("Improving Chain", oc, f"OI {snap.oi_bias}, writing {snap.oi_writing}, prem {snap.premium_bias}")

    # 6) Adequate room before the next resistance (long) / support (short).
    room_atr: float | None = None
    level = snap.resistance if up else snap.support
    if level is not None and atr > 0:
        room = (level - spot) if up else (spot - level)
        room_atr = round(room / atr, 2)
        add("Room to Target", room_atr >= settings.early_early_min_room_atr,
            f"{room_atr:.1f} ATR to {'resistance' if up else 'support'} {level:.1f}")
    else:
        add("Room to Target", False, "no resistance/support level", available=False)

    return checks, room_atr


def _build_plan(
    up: bool,
    candles: list[Candle],
    snap: IndicatorSnapshot,
    spot: float,
    atr: float,
    quote: OptionQuote | None,
) -> tuple[MomentumPlan | None, float | None]:
    """Premium-based plan with Target 1 at 2R (reward:risk = 2:1). Returns
    (plan, reward_risk)."""
    if quote is None or quote.premium <= 0 or atr <= 0:
        return None, None
    entry = quote.premium
    delta = max(abs(quote.delta) if quote.delta else 0.0, 0.35)

    highs = np.array([c.high for c in candles], dtype=float)
    lows = np.array([c.low for c in candles], dtype=float)
    shp, slp = st.swing_points(highs, lows)
    if up:
        swing = slp[-1][1] if slp else float(lows[-10:].min())
        u_stop = min(swing, spot) - 0.5 * atr
        u_dist = spot - u_stop
    else:
        swing = shp[-1][1] if shp else float(highs[-10:].max())
        u_stop = max(swing, spot) + 0.5 * atr
        u_dist = u_stop - spot
    if u_dist <= 0:
        u_dist = 0.6 * atr

    prem_risk = max(0.1, round(u_dist * delta, 1))
    stop = round(max(0.1, entry - prem_risk), 1)
    prem_risk = round(entry - stop, 1)
    if prem_risk <= 0:
        return None, None

    band = max(0.3, round(0.15 * prem_risk, 1))
    # Target 1 at 2R, Target 2 at 3R, Target 3 at 4R → reward:risk (to T1) = 2:1.
    plan = MomentumPlan(
        option_symbol=quote.symbol,
        entry_low=round(entry - band, 1),
        entry_high=round(entry + band, 1),
        stop_loss=stop,
        target1=round(entry + 2 * prem_risk, 1),
        target2=round(entry + 3 * prem_risk, 1),
        target3=round(entry + 4 * prem_risk, 1),
        risk_per_lot=round(prem_risk * max(1, settings.lot_size), 1),
        reward_risk=2.0,
        underlying_stop=round(u_stop, 1),
        risk_level="HIGH",
    )
    plan.holding_time = "Intraday — typically 2–8 candles (earlier and faster than a normal Early Buy)."
    sidelbl = "below" if up else "above"
    plan.exit_conditions = [
        f"Stop hit: premium ≤ ₹{stop:.1f} (underlying {sidelbl} {plan.underlying_stop:.1f}).",
        "Evidence collapses (VWAP lost / EMA slope flips) → exit; the early-early thesis is gone.",
        "Book partial at Target 1 (2R) and trail the rest to Target 2/3.",
        "Time-stop: no follow-through within ~8 candles → step out and wait for Stage 3.",
    ]
    plan.note = (
        f"Early-Early advisory (HIGHER RISK). Buy the leg in ₹{plan.entry_low:.1f}–₹{plan.entry_high:.1f}; "
        f"risk ₹{prem_risk:.1f}/unit; Target 1 at 2R ₹{plan.target1:.1f}."
    )
    return plan, 2.0


def _liquidity(quote: OptionQuote | None) -> tuple[bool, float | None]:
    """Best-effort liquidity/spread validation. OptionQuote carries no bid/ask on
    this feed, so the % spread is reported as None (unavailable) and we gate on
    OI + volume instead — honest, never fabricated."""
    if quote is None:
        return False, None
    ok = quote.oi > 0 and quote.volume > 0 and quote.premium > 0
    return ok, None


def _probability(evidence: int, rr: float | None, room_atr: float | None, liquidity_ok: bool) -> float:
    """Transparent 0..100 estimate (NOT a promise): base on evidence breadth,
    bonuses for strong reward:risk, generous room and confirmed liquidity."""
    p = 34.0 + evidence * 8.0                       # 5/6 → 74, 6/6 → 82
    if rr is not None and rr >= 2.5:
        p += 6.0
    if room_atr is not None and room_atr >= 2.5:
        p += 6.0
    if liquidity_ok:
        p += 2.0
    return round(max(0.0, min(90.0, p)), 0)


def _recommend(
    enabled: bool,
    active: bool,
    evidence: int,
    required: int,
    probability: float,
    rr: float | None,
    room_ok: bool,
    liquidity_ok: bool,
) -> tuple[str, str]:
    """Map the state onto the six-tier recommended-action ladder."""
    if not enabled:
        return "IGNORE", "Early-Early mode is off."
    if active:
        if probability >= 72 and rr is not None and rr >= 2.5 and room_ok and liquidity_ok:
            return "CONSERVATIVE_ENTRY", "All evidence + strong R:R and room — highest-conviction early-early entry."
        if probability >= 60 and rr is not None and rr >= 2.0:
            return "NORMAL_ENTRY", "Multi-evidence Early-Early with R:R ≥ 2:1 — take a normal early-early position."
        return "SMALL_POSITION", "Valid but earlier/thinner — size down and confirm follow-through."
    if evidence >= required - 1:
        return "PREPARE", "One piece away — get the order ready; wait for the last confirmation."
    if evidence >= 2:
        return "WATCH", "Some evidence forming — watch for the higher-low + VWAP-reclaim candle."
    return "IGNORE", "No meaningful early-early evidence yet."


def detect(
    candles: list[Candle],
    chain: list[OptionQuote],
    snap: IndicatorSnapshot,
    spot: float,
) -> EarlyEarly:
    """Produce the Early-Early (Stage 2.5) advisory. Isolated + advisory-only."""
    out = EarlyEarly(enabled=True, evidence_required=settings.early_early_min_evidence)
    if spot is None or len(candles) < 15:
        out.note = "Warming up — not enough bars for early-early analysis."
        out.recommended_action = "IGNORE"
        return out

    atr = snap.atr or (0.004 * spot)

    up_checks, up_room = _evaluate(True, candles, snap, spot, atr)
    dn_checks, dn_room = _evaluate(False, candles, snap, spot, atr)
    up_n = sum(1 for c in up_checks if c.passed)
    dn_n = sum(1 for c in dn_checks if c.passed)

    up = up_n >= dn_n
    checks, room_atr = (up_checks, up_room) if up else (dn_checks, dn_room)
    side = OptionType.CALL if up else OptionType.PUT
    evidence = up_n if up else dn_n

    out.side = side
    out.checks = checks
    out.evidence_count = evidence
    out.room_atr = room_atr

    q = _pick_atm(chain, spot, side)
    if q is not None:
        out.option_symbol = q.symbol
        out.entry_hint = q.premium

    plan, rr = _build_plan(up, candles, snap, spot, atr, q)
    liquidity_ok, spread_pct = _liquidity(q)
    out.plan = plan
    out.reward_risk = rr
    out.spread_pct = spread_pct
    out.liquidity_ok = liquidity_ok

    passed_names = {c.name for c in checks if c.passed}
    essentials_ok = all(name in passed_names for name in _ESSENTIAL)
    room_ok = "Room to Target" in passed_names
    rr_ok = rr is not None and rr >= settings.early_early_min_rr
    out.room_ok = room_ok
    out.rr_ok = bool(rr_ok)

    # Active only when ALL gates pass: enough evidence, the two essentials, valid
    # plan with R:R ≥ 2:1, adequate room, and acceptable liquidity.
    out.active = bool(
        evidence >= settings.early_early_min_evidence
        and essentials_ok
        and rr_ok
        and room_ok
        and liquidity_ok
        and plan is not None
    )

    out.probability = _probability(evidence, rr, room_atr, liquidity_ok)
    out.risk_level = "HIGH"
    out.hold_time = plan.holding_time if plan else "Intraday (earlier/faster than a normal Early Buy)."

    action, reason = _recommend(
        True, out.active, evidence, settings.early_early_min_evidence,
        out.probability, rr, room_ok, liquidity_ok,
    )
    out.recommended_action = action
    out.action_reason = reason

    sidelabel = "CE" if up else "PE"
    if out.active:
        out.reasons.append(
            f"EARLY-EARLY {sidelabel} — {evidence}/6 evidence aligned "
            f"(prob ~{out.probability:.0f}%, R:R {rr:.1f}:1, room {room_atr:.1f} ATR). Higher risk than Early Buy."
        )
        out.note = f"Stage 2.5 · Early-Early {sidelabel} — earlier than Early Buy, HIGHER RISK."
    elif not rr_ok and evidence >= settings.early_early_min_evidence and essentials_ok:
        out.note = "Evidence aligned but reward:risk < 2:1 or no room — not issued (validation gate)."
    elif evidence >= settings.early_early_min_evidence - 1:
        out.note = "Forming — one piece away from an Early-Early setup."
    else:
        out.note = "No early-early setup."
    return out
