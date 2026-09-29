"""Quick Scalp Engine — a SEPARATE, feature-flagged ADD-ON.

A dedicated *short-duration* scalp advisory that targets SMALL option-premium
moves (≈8–15 points) off EXHAUSTION / BOUNCE setups. It is deliberately the
OPPOSITE intent of the trend engines (Early Momentum / Early-Early): instead of
requiring price to be *near* VWAP and early in a fresh trend, it fires when
price is EXTENDED and exhausted and a snap-back begins — exactly the kind of
sharp bounce the trend engines are designed to skip.

It is kept in its own module so it is COMPLETELY ISOLATED from the frozen
confirmation engine, the Early Momentum engine and the Early-Early add-on — it
reads market data and produces its own advisory object; it mutates nothing else.

It fires only when MULTIPLE conditions align (never a single bar):

  1. Selling/Buying climax        — a large-range, high-volume exhaustion bar
  2. ATR extension from VWAP       — price stretched away from the mean
  3. Support/Resistance rejection  — tagged a level and rejected (wick)
  4. Volume spike                  — participation on the turn
  5. First Higher-Low / Lower-High — micro-structure turning
  6. Micro-structure break         — breaks the last minor swing (turn confirmed)
  7. Option-premium stabilization  — the leg stopped bleeding / ticked our way

Before any scalp is issued it must ALSO pass:
  * a valid fixed-target plan with Reward:Risk >= QT_SCALP_MIN_RR (tight stop)
  * liquidity validation on the suggested option leg

Exit model (dedicated): a small FIXED target (QT_SCALP_TARGET_POINTS premium
points), a move to BREAKEVEN after partial progress, and a hard TIME-STOP.

ADVISORY ONLY — it never places an order and never touches the frozen engine.
"""
from __future__ import annotations

import numpy as np

from app.analysis import structure as st
from app.config import settings
from app.models import (
    Candle,
    EarlyCheck,
    IndicatorSnapshot,
    MomentumPlan,
    OptionQuote,
    OptionType,
    ScalpSignal,
)

# Conditions that MUST be present for a valid scalp (structure turning + a real
# breakout of the micro swing) — the rest add conviction / probability.
_ESSENTIAL = ("Micro-Structure Break", "First Higher-Low / Lower-High")

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


def _premium_stabilizing(up: bool, snap: IndicatorSnapshot) -> tuple[bool, str]:
    """Has the leg we'd buy stopped bleeding / started ticking our way? Uses the
    per-leg premium velocity/acceleration already computed by the indicators (no
    fabricated premium history)."""
    if up:
        vel = snap.premium_ce_velocity
        acc = snap.premium_ce_acceleration
        leg = "CE"
    else:
        vel = snap.premium_pe_velocity
        acc = snap.premium_pe_acceleration
        leg = "PE"
    if vel is None and acc is None:
        return False, "no premium velocity data"
    v = vel or 0.0
    a = acc or 0.0
    ok = v >= 0.0 or a > 0.0
    return ok, f"{leg} vel {v:.2f}%/bar, accel {a:.2f}"


def _evaluate(
    up: bool,
    candles: list[Candle],
    snap: IndicatorSnapshot,
    spot: float,
    atr: float,
) -> list[EarlyCheck]:
    """Compute the (up to) seven scalp conditions for one direction."""
    opens = np.array([c.open for c in candles], dtype=float)
    highs = np.array([c.high for c in candles], dtype=float)
    lows = np.array([c.low for c in candles], dtype=float)
    closes = np.array([c.close for c in candles], dtype=float)
    vols = np.array([c.volume for c in candles], dtype=float)
    checks: list[EarlyCheck] = []

    def add(name: str, passed: bool, detail: str, available: bool = True):
        checks.append(EarlyCheck(name=name, passed=bool(passed and available), detail=detail, available=available))

    avg_vol = float(vols[-11:-1].mean()) if len(vols) >= 11 else (float(vols.mean()) if len(vols) else 0.0)

    # 1) Selling (for an up scalp) / buying (down) CLIMAX in the last few bars:
    #    a large-range bar (>= 1.8 ATR) on a volume spike, pushing AWAY from us
    #    (a red flush for a bounce up / a green blow-off for a drop down).
    climax = False
    cdetail = "no climax bar"
    look = range(max(0, len(candles) - 10), len(candles))
    for i in look:
        rng = float(highs[i] - lows[i])
        if atr > 0 and rng >= 1.8 * atr and vols[i] >= 1.8 * max(avg_vol, 1e-9):
            flush = (closes[i] < opens[i]) if up else (closes[i] > opens[i])
            if flush:
                climax = True
                cdetail = f"range {rng / atr:.1f} ATR on {vols[i]:.0f} vol (>{1.8 * avg_vol:.0f})"
                break
    add("Selling/Buying Climax", climax, cdetail)

    # 2) ATR EXTENSION from VWAP — price stretched away from the mean (exhaustion
    #    that tends to snap back). For an up scalp price is BELOW VWAP by >= N ATR.
    ext_ok = False
    edetail = "no VWAP/ATR"
    if snap.vwap is not None and atr > 0:
        dist = (snap.vwap - spot) if up else (spot - snap.vwap)
        ext_atr = dist / atr
        ext_ok = ext_atr >= settings.scalp_min_atr_extension
        edetail = f"{ext_atr:.1f} ATR {'below' if up else 'above'} VWAP {snap.vwap:.1f}"
    add("ATR Extension", ext_ok, edetail, available=snap.vwap is not None and atr > 0)

    # 3) Support (up) / Resistance (down) REJECTION — a recent bar tagged the
    #    level and closed back off it with a wick.
    rej = False
    rdetail = "no S/R level"
    level = snap.support if up else snap.resistance
    if level is not None:
        rej_win = range(max(0, len(candles) - 6), len(candles))
        for i in rej_win:
            body_lo = min(opens[i], closes[i])
            body_hi = max(opens[i], closes[i])
            if up:
                tagged = lows[i] <= level * 1.002
                wick = (body_lo - lows[i]) >= 0.4 * max(highs[i] - lows[i], 1e-9)
                held = closes[i] > level
            else:
                tagged = highs[i] >= level * 0.998
                wick = (highs[i] - body_hi) >= 0.4 * max(highs[i] - lows[i], 1e-9)
                held = closes[i] < level
            if tagged and wick and held:
                rej = True
                rdetail = f"rejected {'support' if up else 'resistance'} {level:.1f} with wick"
                break
    add("S/R Rejection", rej, rdetail, available=level is not None)

    # 4) Volume spike on the turn.
    vol_ok = False
    vdetail = "not enough volume history"
    if len(vols) >= 11:
        last_v = float(vols[-1])
        vol_ok = snap.volume_spike or last_v >= 1.5 * max(avg_vol, 1e-9)
        vdetail = f"last {last_v:.0f} vs avg {avg_vol:.0f}"
    add("Volume Spike", vol_ok, vdetail, available=len(vols) >= 11)

    # 5) First Higher-Low (up) / Lower-High (down) — micro-structure turning.
    shp, slp = st.swing_points(highs, lows)
    if up:
        if len(slp) >= 2:
            prev, last = slp[-2][1], slp[-1][1]
            add("First Higher-Low / Lower-High", last > prev, f"swing low {last:.1f} vs prior {prev:.1f}")
        else:
            add("First Higher-Low / Lower-High", False, "not enough swing lows", available=False)
    else:
        if len(shp) >= 2:
            prev, last = shp[-2][1], shp[-1][1]
            add("First Higher-Low / Lower-High", last < prev, f"swing high {last:.1f} vs prior {prev:.1f}")
        else:
            add("First Higher-Low / Lower-High", False, "not enough swing highs", available=False)

    # 6) Micro-structure BREAK — the last bar breaks the prior 3-bar micro swing
    #    in our direction (the turn is confirmed, not just hoped for).
    if len(closes) >= 4:
        last_close = float(closes[-1])
        if up:
            micro = float(highs[-4:-1].max())
            add("Micro-Structure Break", last_close > micro, f"close {last_close:.1f} > micro-high {micro:.1f}")
        else:
            micro = float(lows[-4:-1].min())
            add("Micro-Structure Break", last_close < micro, f"close {last_close:.1f} < micro-low {micro:.1f}")
    else:
        add("Micro-Structure Break", False, "not enough history", available=False)

    # 7) Option-premium stabilization — the leg we'd buy stopped bleeding.
    stab_ok, stab_detail = _premium_stabilizing(up, snap)
    add("Premium Stabilization", stab_ok, stab_detail,
        available=not stab_detail.startswith("no premium"))

    return checks


def _build_plan(
    up: bool,
    candles: list[Candle],
    snap: IndicatorSnapshot,
    spot: float,
    atr: float,
    quote: OptionQuote | None,
) -> tuple[MomentumPlan | None, float | None]:
    """Fixed-target scalp plan (small target + TIGHT stop). Returns (plan, rr)."""
    if quote is None or quote.premium <= 0 or atr <= 0:
        return None, None
    entry = float(quote.premium)
    delta = max(abs(quote.delta) if quote.delta else 0.0, 0.35)
    target_pts = float(settings.scalp_target_points)

    highs = np.array([c.high for c in candles], dtype=float)
    lows = np.array([c.low for c in candles], dtype=float)
    shp, slp = st.swing_points(highs, lows)
    if up:
        swing = slp[-1][1] if slp else float(lows[-6:].min())
        u_stop = min(swing, spot) - 0.3 * atr
        u_dist = spot - u_stop
    else:
        swing = shp[-1][1] if shp else float(highs[-6:].max())
        u_stop = max(swing, spot) + 0.3 * atr
        u_dist = u_stop - spot
    if u_dist <= 0:
        u_dist = 0.4 * atr

    struct_risk = round(u_dist * delta, 1)
    # Tight scalp stop: cap the premium risk so R:R to the fixed target clears
    # the minimum (a scalp lives or dies on a small, tight stop).
    rr_cap = target_pts / max(settings.scalp_min_rr, 0.1)
    # Floor to 1 decimal (never round UP past the cap) so the resulting R:R
    # always clears the configured minimum rather than failing on a rounding tick.
    prem_risk = max(0.1, int(min(struct_risk, rr_cap) * 10) / 10.0)
    stop = round(max(0.1, entry - prem_risk), 1)
    prem_risk = round(entry - stop, 1)
    if prem_risk <= 0:
        return None, None

    rr = round(target_pts / prem_risk, 2)
    band = max(0.2, round(0.1 * prem_risk, 1))
    target1 = round(entry + target_pts, 1)
    breakeven_at = round(entry + settings.scalp_breakeven_frac * target_pts, 1)

    plan = MomentumPlan(
        option_symbol=quote.symbol,
        entry_low=round(entry - band, 1),
        entry_high=round(entry + band, 1),
        stop_loss=stop,
        target1=target1,
        target2=round(entry + 1.5 * target_pts, 1),
        target3=round(entry + 2.0 * target_pts, 1),
        risk_per_lot=round(prem_risk * max(1, settings.lot_size), 1),
        reward_risk=rr,
        underlying_stop=round(u_stop, 1),
        risk_level="HIGH",
    )
    plan.holding_time = (
        f"Scalp — typically 1–{settings.scalp_max_hold_candles} candles; "
        "in and out for the points."
    )
    sidelbl = "below" if up else "above"
    plan.exit_conditions = [
        f"Fixed target: exit at +{target_pts:.0f} pts (₹{target1:.1f}).",
        f"Move stop to breakeven once premium reaches ₹{breakeven_at:.1f} (partial progress).",
        f"Hard stop: premium ≤ ₹{stop:.1f} (underlying {sidelbl} {plan.underlying_stop:.1f}).",
        f"Time-stop: exit after {settings.scalp_max_hold_candles} candles if the target is not hit.",
    ]
    plan.note = (
        f"Quick scalp (HIGHER RISK). Buy ₹{plan.entry_low:.1f}–₹{plan.entry_high:.1f}; "
        f"risk ₹{prem_risk:.1f}/unit for a fixed +{target_pts:.0f}-pt target (R:R {rr:.1f}:1)."
    )
    return plan, rr


def _liquidity(quote: OptionQuote | None) -> bool:
    if quote is None:
        return False
    return quote.oi > 0 and quote.volume > 0 and quote.premium > 0


def _probability(conditions: int, rr: float | None, liquidity_ok: bool) -> float:
    """Transparent 0..100 estimate (NOT a promise): scalps are lower-probability
    by nature, so the ceiling is capped below the trend engines."""
    p = 30.0 + conditions * 7.0            # 5/7 → 65, 7/7 → 79
    if rr is not None and rr >= 2.0:
        p += 5.0
    if liquidity_ok:
        p += 2.0
    return round(max(0.0, min(85.0, p)), 0)


def _recommend(
    active: bool,
    conditions: int,
    required: int,
    probability: float,
    rr: float | None,
    liquidity_ok: bool,
) -> tuple[str, str]:
    if active:
        if probability >= 68 and rr is not None and rr >= 2.0 and liquidity_ok:
            return "SMALL_POSITION", "Strong scalp setup — take a SMALL position for the fixed target (scalps are fast/higher-risk)."
        if rr is not None and rr >= settings.scalp_min_rr:
            return "SMALL_POSITION", "Valid scalp with acceptable R:R — size down; obey the time-stop."
        return "WATCH", "Setup forming but R:R thin — watch only."
    if conditions >= required - 1:
        return "PREPARE", "One condition away — get ready; wait for the micro-structure break."
    if conditions >= 2:
        return "WATCH", "Some exhaustion/bounce evidence — watch for the turn."
    return "IGNORE", "No scalp setup yet."


def evaluate_exit(
    up: bool,
    candles: list[Candle],
    snap: IndicatorSnapshot,
    spot: float,
    entry_premium: float | None,
    live_premium: float | None,
    stop_loss: float | None,
    target1: float | None,
) -> tuple[bool, str, str, list[str]]:
    """LIVE exit check for a running scalp leg: is the move ROLLING OVER (about
    to fall) so we should get out fast — before the hard stop? Returns
    (exit_now, urgency, reason, signals). Reads market data only; advisory."""
    opens = np.array([c.open for c in candles], dtype=float)
    highs = np.array([c.high for c in candles], dtype=float)
    lows = np.array([c.low for c in candles], dtype=float)
    closes = np.array([c.close for c in candles], dtype=float)
    vols = np.array([c.volume for c in candles], dtype=float)
    signals: list[str] = []

    # 1) Target reached → book it (a "good" exit, still EXIT urgency).
    if target1 is not None and live_premium is not None and live_premium >= target1:
        return True, "EXIT", f"Target reached (₹{live_premium:.1f} ≥ ₹{target1:.1f}) — book it.", ["Target hit"]

    # 2) Hard stop already breached.
    if stop_loss is not None and live_premium is not None and live_premium <= stop_loss:
        return True, "EXIT", f"Stop hit (₹{live_premium:.1f} ≤ ₹{stop_loss:.1f}) — out now.", ["Stop hit"]

    # 3) The option leg is BLEEDING — premium velocity turned against us.
    vel = (snap.premium_ce_velocity if up else snap.premium_pe_velocity)
    if vel is not None and vel <= -0.4:
        signals.append("Premium bleeding")

    # 4) Price LOST VWAP (for a long) / reclaimed it (for a short) — the snap-back
    #    is failing.
    if snap.vwap is not None and len(closes):
        last_close = float(closes[-1])
        if up and last_close < snap.vwap:
            signals.append("Lost VWAP")
        elif (not up) and last_close > snap.vwap:
            signals.append("Reclaimed VWAP")

    # 5) A bearish (for a long) / bullish (for a short) REVERSAL candle on volume.
    if len(closes) >= 11:
        o, hi, lo, c = float(opens[-1]), float(highs[-1]), float(lows[-1]), float(closes[-1])
        rng = max(hi - lo, 1e-9)
        avg_v = float(vols[-11:-1].mean())
        vol_up = vols[-1] >= 1.3 * max(avg_v, 1e-9)
        if up:
            upper_wick = (hi - max(o, c)) >= 0.5 * rng
            bear = c < o and (c - lo) <= 0.35 * rng
            if (bear or upper_wick) and vol_up:
                signals.append("Bearish reversal candle")
        else:
            lower_wick = (min(o, c) - lo) >= 0.5 * rng
            bull = c > o and (hi - c) <= 0.35 * rng
            if (bull or lower_wick) and vol_up:
                signals.append("Bullish reversal candle")

    # 6) Micro-structure break AGAINST us — a lower-high break (long) / higher-low
    #    break (short): the reversal is confirming.
    if len(closes) >= 4:
        last_close = float(closes[-1])
        if up and last_close < float(lows[-4:-1].min()):
            signals.append("Broke last higher-low")
        elif (not up) and last_close > float(highs[-4:-1].max()):
            signals.append("Broke last lower-high")

    # Decide urgency. Two+ independent rollover signals (or losing VWAP with any
    # other) → EXIT NOW; a single early warning → WATCH.
    strong = "Lost VWAP" in signals or "Reclaimed VWAP" in signals
    if len(signals) >= 2 or (strong and signals):
        return True, "EXIT", "Move is rolling over — " + ", ".join(signals) + ". Get out.", signals
    if signals:
        return False, "WATCH", "Early warning — " + ", ".join(signals) + ". Tighten / be ready to exit.", signals
    return False, "NONE", "", signals


def detect(
    candles: list[Candle],
    chain: list[OptionQuote],
    snap: IndicatorSnapshot,
    spot: float,
) -> ScalpSignal:
    """Produce the Quick Scalp advisory. Isolated + advisory-only."""
    out = ScalpSignal(enabled=True, condition_required=settings.scalp_min_conditions)
    out.target_points = float(settings.scalp_target_points)
    out.time_stop_candles = settings.scalp_max_hold_candles
    if spot is None or len(candles) < 15:
        out.note = "Warming up — not enough bars for scalp analysis."
        return out

    atr = snap.atr or (0.004 * spot)

    up_checks = _evaluate(True, candles, snap, spot, atr)
    dn_checks = _evaluate(False, candles, snap, spot, atr)
    up_n = sum(1 for c in up_checks if c.passed)
    dn_n = sum(1 for c in dn_checks if c.passed)

    up = up_n >= dn_n
    checks = up_checks if up else dn_checks
    conditions = up_n if up else dn_n
    side = OptionType.CALL if up else OptionType.PUT

    out.side = side
    out.checks = checks
    out.condition_count = conditions
    out.setup = "BOUNCE" if up else "EXHAUSTION"

    q = _pick_atm(chain, spot, side)
    if q is not None:
        out.option_symbol = q.symbol
        out.entry_hint = q.premium

    plan, rr = _build_plan(up, candles, snap, spot, atr, q)
    liquidity_ok = _liquidity(q)
    out.plan = plan
    out.reward_risk = rr
    out.liquidity_ok = liquidity_ok
    if plan is not None:
        out.breakeven_at = round(
            (plan.entry_low + plan.entry_high) / 2.0 + settings.scalp_breakeven_frac * float(settings.scalp_target_points),
            1,
        ) if plan.entry_low is not None and plan.entry_high is not None else None

    passed_names = {c.name for c in checks if c.passed}
    essentials_ok = all(name in passed_names for name in _ESSENTIAL)
    rr_ok = rr is not None and rr >= settings.scalp_min_rr
    out.rr_ok = bool(rr_ok)

    out.active = bool(
        conditions >= settings.scalp_min_conditions
        and essentials_ok
        and rr_ok
        and liquidity_ok
        and plan is not None
    )

    out.probability = _probability(conditions, rr, liquidity_ok)
    out.risk_level = "HIGH"
    out.hold_time = plan.holding_time if plan else (
        f"Scalp — 1–{settings.scalp_max_hold_candles} candles."
    )

    action, reason = _recommend(
        out.active, conditions, settings.scalp_min_conditions, out.probability, rr, liquidity_ok,
    )
    out.recommended_action = action
    out.action_reason = reason

    sidelabel = "CE" if up else "PE"
    if out.active:
        out.reasons.append(
            f"SCALP {sidelabel} ({out.setup}) — {conditions}/7 conditions aligned "
            f"(prob ~{out.probability:.0f}%, R:R {rr:.1f}:1, fixed +{settings.scalp_target_points:.0f}-pt target). Fast, higher risk."
        )
        out.note = f"Quick Scalp {sidelabel} — small fixed target, tight stop, HIGHER RISK."
    elif not rr_ok and conditions >= settings.scalp_min_conditions and essentials_ok:
        out.note = "Conditions aligned but reward:risk below the scalp minimum — not issued."
    elif conditions >= settings.scalp_min_conditions - 1:
        out.note = "Forming — one condition away from a scalp setup."
    else:
        out.note = "No scalp setup."
    return out
