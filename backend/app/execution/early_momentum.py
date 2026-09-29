"""Early Momentum Advisory Engine — a SECOND, independent engine that runs in
PARALLEL with the frozen confirmation engine.

It maps the market onto a 5-stage accumulation→trend ladder using five pieces of
evidence (higher-low, VWAP reclaim, rising volume, improving option chain,
underlying confirmation). The evidence is weighted ADAPTIVELY by market regime,
India VIX, expiry proximity and time of day — e.g. VWAP reclaim + volume matter
more in trending/high-volatility sessions than in quiet ranges. It is ADVISORY:
it never changes the frozen engine's signal, confidence, or strategy.

Stages: 0 No Setup · 1 Momentum Building · 2 Structure Confirmed · 3 Early Buy ·
4 Confirmation Buy (the frozen engine's own confirmed BUY).
"""
from __future__ import annotations

import json as _json
import os as _os
import time as _time

import numpy as np

from app.analysis import structure as st
from app.config import settings
from app.models import (
    Candle,
    EarlyCheck,
    EarlyMomentum,
    IndicatorSnapshot,
    MomentumPlan,
    OptionQuote,
    OptionType,
    STAGE_LABELS,
)

# Base (regime-neutral) weight of each evidence pillar; renormalised to 100.
_BASE_WEIGHTS = {
    "Higher-Low": 0.25,
    "VWAP Reclaim": 0.25,
    "Volume": 0.15,
    "Option-Chain": 0.15,
    "Underlying": 0.20,
}


def _regime(snap: IndicatorSnapshot) -> str:
    """Coarse trend/range classification from ADX / trend / structure."""
    if snap.adx is not None:
        if snap.adx >= 25:
            return "TRENDING"
        if snap.adx < 18:
            return "RANGING"
    if snap.trend in ("UP", "DOWN") or snap.market_structure in ("HH_HL", "LH_LL"):
        return "TRENDING"
    if snap.trend == "SIDEWAYS" or snap.market_structure == "MIXED":
        return "RANGING"
    return "MIXED"


def _tod_score(now: int | None, is_mcx: bool) -> float:
    """Liquidity quality by time of day (0..100). Mirrors the intelligence layer."""
    if now is None:
        return 70.0
    ist = _time.gmtime(now + 5 * 3600 + 1800)
    mins = ist.tm_hour * 60 + ist.tm_min
    if is_mcx:
        if 17 * 60 <= mins < 23 * 60:
            return 100.0
        if 9 * 60 <= mins < 17 * 60:
            return 70.0
        if 23 * 60 <= mins <= 23 * 60 + 30:
            return 45.0
        return 55.0
    open_m, close_m = 9 * 60 + 15, 15 * 60 + 30
    if mins < open_m or mins > close_m:
        return 40.0
    if mins < open_m + 15 or mins > close_m - 15:
        return 55.0
    if 12 * 60 <= mins < 13 * 60:
        return 65.0
    return 100.0


def _adaptive_weights(
    regime: str, vix: float | None, dte: float, tod: float
) -> tuple[dict[str, float], str]:
    """Scale the base weights by context, then renormalise to sum to 100.

    Returns (weights_in_points, human_note). Momentum evidence (VWAP + Volume)
    is amplified in trending / high-VIX sessions; structure + option-chain
    evidence is amplified in ranges / near expiry / thin liquidity."""
    m = dict(_BASE_WEIGHTS)
    notes: list[str] = []

    if regime == "TRENDING":
        m["VWAP Reclaim"] *= 1.30
        m["Volume"] *= 1.30
        m["Underlying"] *= 1.15
        m["Higher-Low"] *= 0.90
        notes.append("trend↑ VWAP/Volume")
    elif regime == "RANGING":
        m["Higher-Low"] *= 1.20
        m["Option-Chain"] *= 1.20
        m["Volume"] *= 0.80
        m["VWAP Reclaim"] *= 0.90
        notes.append("range↑ structure/chain")

    if vix is not None:
        if vix >= 16:
            m["VWAP Reclaim"] *= 1.20
            m["Volume"] *= 1.20
            m["Higher-Low"] *= 0.95
            notes.append(f"VIX {vix:.0f}↑ momentum")
        elif vix < 12:
            m["Option-Chain"] *= 1.15
            m["Volume"] *= 0.90
            notes.append(f"VIX {vix:.0f}↓ chain")

    if dte <= 1:
        m["Option-Chain"] *= 1.25
        m["Volume"] *= 1.10
        m["Underlying"] *= 0.90
        notes.append("expiry↑ chain")
    elif dte >= 5:
        m["Underlying"] *= 1.10
        m["Higher-Low"] *= 1.05
        notes.append("far-expiry↑ structure")

    if tod >= 85:
        m["Volume"] *= 1.10
        m["VWAP Reclaim"] *= 1.05
    elif tod < 55:
        m["Volume"] *= 0.80
        m["Higher-Low"] *= 1.10
        notes.append("thin-hours↓ volume")

    total = sum(m.values()) or 1.0
    pts = {k: round(v / total * 100.0, 1) for k, v in m.items()}
    return pts, ", ".join(notes) or "neutral weighting"


def _evaluate(
    up: bool,
    candles: list[Candle],
    snap: IndicatorSnapshot,
    spot: float,
    atr: float,
    weights: dict[str, float],
) -> tuple[list[EarlyCheck], float, float | None]:
    highs = np.array([c.high for c in candles], dtype=float)
    lows = np.array([c.low for c in candles], dtype=float)
    closes = np.array([c.close for c in candles], dtype=float)
    vols = np.array([c.volume for c in candles], dtype=float)
    checks: list[EarlyCheck] = []

    def add(name: str, passed: bool, detail: str, available: bool = True):
        w = weights.get(name, 0.0)
        checks.append(EarlyCheck(
            name=name, passed=passed, detail=detail, available=available,
            weight=w, points=round(w if (passed and available) else 0.0, 1),
        ))

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

    # 2) VWAP reclaim (crossed VWAP in the trade direction within the last 5 bars).
    if snap.vwap is not None and len(closes) >= 6:
        vwap = snap.vwap
        recent = closes[-5:]
        reclaimed = (spot > vwap and float(recent.min()) <= vwap) if up \
            else (spot < vwap and float(recent.max()) >= vwap)
        add("VWAP Reclaim", bool(reclaimed), f"price {spot:.1f} vs VWAP {vwap:.1f}")
    else:
        add("VWAP Reclaim", False, "no VWAP/history", available=False)

    # 3) Rising volume (last 3 bars vs the prior 10-bar average).
    if len(vols) >= 13:
        recent_v = float(vols[-3:].mean())
        base_v = float(vols[-13:-3].mean())
        add("Volume", recent_v > 1.15 * max(base_v, 1e-9),
            f"recent {recent_v:.0f} vs base {base_v:.0f}")
    else:
        add("Volume", False, "not enough volume history", available=False)

    # 4) Option-chain strength improving in the trade direction.
    if up:
        oc = (snap.oi_bias == "BULLISH" or snap.oi_writing == "PUT_WRITING"
              or snap.premium_bias == "BULLISH")
    else:
        oc = (snap.oi_bias == "BEARISH" or snap.oi_writing == "CALL_WRITING"
              or snap.premium_bias == "BEARISH")
    add("Option-Chain", bool(oc),
        f"OI {snap.oi_bias}, writing {snap.oi_writing}, prem {snap.premium_bias}")

    # 5) Underlying confirmation: fast EMA reclaim / momentum in the direction.
    conf = False
    if snap.ema9 is not None and snap.ema20 is not None:
        conf = (snap.ema9 > snap.ema20) if up else (snap.ema9 < snap.ema20)
    if not conf and len(closes) >= 4:
        conf = (closes[-1] > closes[-4]) if up else (closes[-1] < closes[-4])
    add("Underlying", bool(conf),
        "EMA9>EMA20 / rising" if up else "EMA9<EMA20 / falling")

    score = round(sum(c.points for c in checks), 1)
    atr_from_vwap = None
    if snap.vwap is not None and atr > 0:
        raw = (spot - snap.vwap) if up else (snap.vwap - spot)
        atr_from_vwap = round(raw / atr, 2)
    return checks, score, atr_from_vwap


def _pick_atm(chain: list[OptionQuote], spot: float, side: OptionType) -> OptionQuote | None:
    same = [q for q in chain if q.option_type == side and q.premium > 0]
    if not same:
        return None
    return min(same, key=lambda q: abs(q.strike - spot))


def _build_plan(
    up: bool,
    candles: list[Candle],
    snap: IndicatorSnapshot,
    spot: float,
    atr: float,
    quote: OptionQuote | None,
) -> MomentumPlan | None:
    """Build a complete, premium-based trade plan for an EARLY BUY.

    Stop is anchored on the UNDERLYING structure (most recent swing low for a
    long / swing high for a short) buffered by 0.5·ATR, then translated into an
    OPTION-PREMIUM stop via the leg's delta. Targets are 1R/2R/3R in premium.
    Units are never mixed: the underlying stop stays a context field; entry,
    stop and targets on the plan are all premium."""
    if quote is None or quote.premium <= 0 or atr <= 0:
        return None
    entry = quote.premium
    # Delta magnitude (per 1 point of underlying). Floor so a tiny/So-far-OTM
    # delta can't imply an unrealistically tight premium stop.
    delta = abs(quote.delta) if quote.delta else 0.0
    delta = max(delta, 0.35)

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

    # Premium risk per the underlying stop distance (a long option GAINS as the
    # underlying moves our way and LOSES as it moves against us, ~delta·move).
    prem_risk = max(0.1, round(u_dist * delta, 1))
    stop = round(max(0.1, entry - prem_risk), 1)
    prem_risk = round(entry - stop, 1)  # actual after flooring
    if prem_risk <= 0:
        return None

    band = max(0.3, round(0.15 * prem_risk, 1))
    plan = MomentumPlan(
        option_symbol=quote.symbol,
        entry_low=round(entry - band, 1),
        entry_high=round(entry + band, 1),
        stop_loss=stop,
        target1=round(entry + prem_risk, 1),
        target2=round(entry + 2 * prem_risk, 1),
        target3=round(entry + 3 * prem_risk, 1),
        risk_per_lot=round(prem_risk * max(1, settings.lot_size), 1),
        reward_risk=1.0,
        underlying_stop=round(u_stop, 1),
        risk_level="HIGH",
    )
    plan.holding_time = "Intraday — typically 3–10 candles (15–50 min on 5m) while the trend holds."
    sidelbl = "below" if up else "above"
    plan.exit_conditions = [
        f"Stop hit: premium ≤ ₹{stop:.1f} (underlying {sidelbl} {plan.underlying_stop:.1f}).",
        "Trend fails: price loses VWAP against the trade → exit even before the stop.",
        "Stage regression: momentum drops back to Structure/No-Setup → the early thesis is gone.",
        "Book partial at Target 1, trail the rest; take Target 2/3 only while momentum persists.",
        "Time-stop: no follow-through within ~10 candles → exit and wait for the next leg.",
    ]
    plan.note = (
        f"Advisory plan. Buy the leg in ₹{plan.entry_low:.1f}–₹{plan.entry_high:.1f}; "
        f"risk ₹{prem_risk:.1f}/unit to the stop; scale out at ₹{plan.target1:.1f} / "
        f"₹{plan.target2:.1f} / ₹{plan.target3:.1f}. Higher risk than a confirmation BUY."
    )
    return plan


def log_transition(instrument: str, em: EarlyMomentum, now: int) -> None:
    """Append a stage transition to a JSONL log so future AI training can learn
    WHEN the market moved from accumulation → trend, not just win/loss. Best
    effort — a logging failure must never break a live tick."""
    try:
        path = _os.path.join(settings.data_dir, "early_momentum_transitions.jsonl")
        _os.makedirs(settings.data_dir, exist_ok=True)
        row = {
            "ts": int(now),
            "instrument": instrument,
            "stage_num": em.stage_num,
            "stage": em.stage,
            "side": em.side.value if em.side else None,
            "score": em.score,
            "regime": em.regime,
            "atr_from_vwap": em.atr_from_vwap,
            "passed": [c.name for c in em.checks if c.passed],
            "option_symbol": em.option_symbol,
            "entry_hint": em.entry_hint,
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(_json.dumps(row) + "\n")
    except Exception:
        pass


def detect(
    candles: list[Candle],
    chain: list[OptionQuote],
    snap: IndicatorSnapshot,
    spot: float,
    *,
    vix: float | None = None,
    now: int | None = None,
    is_mcx: bool = True,
    baseline_buy: bool = False,
    baseline_side: OptionType | None = None,
) -> EarlyMomentum:
    out = EarlyMomentum(enabled=True)
    if spot is None or len(candles) < 15:
        out.note = "Warming up — not enough bars for early-momentum analysis."
        return out

    atr = snap.atr or (0.004 * spot)
    regime = _regime(snap)
    dte = max(0.0, float(settings.days_to_expiry))
    tod = _tod_score(now, is_mcx)
    weights, weight_note = _adaptive_weights(regime, vix, dte, tod)
    out.regime = regime
    out.weight_note = weight_note

    up_checks, up_score, up_atr = _evaluate(True, candles, snap, spot, atr, weights)
    dn_checks, dn_score, dn_atr = _evaluate(False, candles, snap, spot, atr, weights)

    up = up_score >= dn_score
    checks, score, atr_from_vwap = (
        (up_checks, up_score, up_atr) if up else (dn_checks, dn_score, dn_atr)
    )
    side = OptionType.CALL if up else OptionType.PUT
    out.side = side
    out.score = score
    out.checks = checks
    out.atr_from_vwap = atr_from_vwap

    q = _pick_atm(chain, spot, side)
    if q is not None:
        out.option_symbol = q.symbol
        out.entry_hint = q.premium

    hl = next((c for c in checks if c.name == "Higher-Low"), None)
    vw = next((c for c in checks if c.name == "VWAP Reclaim"), None)
    structure_ok = bool(hl and hl.passed)
    core_ok = bool(structure_ok and vw and vw.passed)
    still_early = atr_from_vwap is not None and atr_from_vwap <= settings.early_momentum_max_atr_from_vwap
    gate = settings.early_momentum_min_score
    passed = [c.name for c in checks if c.passed]

    # --- 5-stage market-state ladder ---
    baseline_aligned = baseline_buy and (baseline_side is None or baseline_side == side)
    if baseline_aligned:
        stage = 4  # Confirmation Buy — the frozen engine's own confirmed BUY.
    elif core_ok and score >= gate and still_early:
        stage = 3  # Early Buy.
    elif structure_ok:
        stage = 2  # Structure Confirmed (higher-low in place).
    elif score >= max(20.0, gate * 0.4):
        stage = 1  # Momentum Building (some evidence, structure not yet confirmed).
    else:
        stage = 0  # No Setup.

    out.stage_num = stage
    out.stage = STAGE_LABELS[stage]
    out.active = stage == 3

    # A full, manual trade plan accompanies a live EARLY BUY (Stage 3 only).
    if stage == 3:
        out.plan = _build_plan(up, candles, snap, spot, atr, q)

    sidelabel = "CE" if up else "PE"
    if stage == 4:
        out.note = (
            f"Confirmation BUY {sidelabel} — the frozen engine confirmed this {'up' if up else 'down'} "
            f"trend. Early Momentum flagged the earlier stages of this leg."
        )
    elif stage == 3:
        out.reasons.append(
            f"EARLY {sidelabel} — new {'up' if up else 'down'} leg forming: "
            f"{', '.join(passed)} aligned (adaptive score {score:.0f}/{gate:.0f}); still early "
            f"({atr_from_vwap:.1f} ATR from VWAP). Higher risk than the confirmation BUY."
        )
    elif stage == 2 and not still_early:
        out.note = (
            f"Structure confirmed but price already {atr_from_vwap:.1f} ATR beyond VWAP — "
            f"no longer early; wait for the next leg or a confirmation BUY."
        )
    elif stage == 2:
        out.note = f"Structure confirmed (higher-{'low' if up else 'high'}); waiting for VWAP reclaim + score ≥ {gate:.0f}."
    elif stage == 1:
        out.note = f"Momentum building ({', '.join(passed) or 'weak evidence'}); no structure shift yet."
    else:
        out.note = "No early-momentum setup."
    return out
