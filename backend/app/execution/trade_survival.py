"""Trade Survival Analyzer — can this trade survive normal market pullbacks?

ADVISORY. Estimates, for the recommended option, how well its premium buffer
(entry → stop) absorbs a set of ordinary stress moves (ATR pullback, VWAP / EMA /
support / resistance retests, expiry decay, VIX expansion, gamma acceleration),
returning a 0..100 survival score per component and overall. It never changes the
BUY/WAIT direction.
"""
from __future__ import annotations

from app.config import settings
from app.models import (
    Decision,
    IndicatorSnapshot,
    OptionQuote,
    OptionType,
    SurvivalComponent,
    TradeSurvival,
)


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _move_survival(buffer_prem: float, delta: float, adverse_pts: float) -> float:
    """Score how well the premium buffer covers an adverse underlying move."""
    if adverse_pts <= 0:
        return 90.0
    drop = max(0.01, delta * adverse_pts)
    return _clamp(100.0 * buffer_prem / drop, 0.0, 100.0)


def analyze(
    decision: Decision,
    quote: OptionQuote | None,
    snap: IndicatorSnapshot,
    vix: float | None = None,
) -> TradeSurvival:
    out = TradeSurvival()
    premium = decision.current_premium
    spot = decision.spot_price
    if premium is None or premium <= 0 or spot is None:
        out.overall = 0.0
        out.recommendation = "SKIP"
        return out

    stop = decision.stop_loss if decision.stop_loss is not None else premium * 0.7
    buffer_prem = max(0.0, premium - stop)
    delta = abs(quote.delta) if quote and quote.delta else 0.4
    atr = snap.atr or (0.004 * spot)
    is_call = decision.option_type == OptionType.CALL

    comps: list[SurvivalComponent] = []

    # ATR pullback (1 ATR adverse).
    comps.append(SurvivalComponent(
        name="ATR", score=round(_move_survival(buffer_prem, delta, atr), 0),
        detail=f"Buffer ₹{buffer_prem:.1f} vs ~₹{delta*atr:.1f} drop on a 1-ATR move.",
    ))

    # VWAP retest.
    if snap.vwap is not None:
        adv = abs(spot - snap.vwap)
        comps.append(SurvivalComponent(
            name="VWAP", score=round(_move_survival(buffer_prem, delta, adv), 0),
            detail=f"VWAP {adv:.1f} pts away.",
        ))
    else:
        comps.append(SurvivalComponent(name="VWAP", score=0, available=False, detail="No VWAP."))

    # EMA retest (ema20 preferred, else ema9).
    ema = snap.ema20 or snap.ema9
    if ema is not None:
        adv = abs(spot - ema)
        comps.append(SurvivalComponent(
            name="EMA", score=round(_move_survival(buffer_prem, delta, adv), 0),
            detail=f"EMA {adv:.1f} pts away.",
        ))
    else:
        comps.append(SurvivalComponent(name="EMA", score=0, available=False, detail="No EMA."))

    # Support (calls) / Resistance (puts) retest — the natural invalidation level.
    if is_call and snap.support is not None:
        adv = max(0.0, spot - snap.support)
        comps.append(SurvivalComponent(
            name="Support", score=round(_move_survival(buffer_prem, delta, adv), 0),
            detail=f"Support {adv:.1f} pts below.",
        ))
    elif (not is_call) and snap.resistance is not None:
        adv = max(0.0, snap.resistance - spot)
        comps.append(SurvivalComponent(
            name="Resistance", score=round(_move_survival(buffer_prem, delta, adv), 0),
            detail=f"Resistance {adv:.1f} pts above.",
        ))
    else:
        comps.append(SurvivalComponent(
            name="Support/Resistance", score=0, available=False, detail="Level unavailable.",
        ))

    # Expiry decay: daily theta as % of premium, scaled by days to expiry.
    dte = max(0, settings.days_to_expiry)
    if quote is not None and quote.theta:
        theta_pct = abs(quote.theta) / premium * 100.0
        expiry_pen = theta_pct * (1.0 if dte >= 3 else 2.0 if dte >= 1 else 3.0)
        comps.append(SurvivalComponent(
            name="Expiry", score=round(_clamp(100.0 - expiry_pen), 0),
            detail=f"θ {theta_pct:.1f}%/day, {dte} DTE.",
        ))
    else:
        comps.append(SurvivalComponent(name="Expiry", score=70, detail=f"{dte} DTE (no θ)."))

    # VIX expansion (best-effort; skipped when unavailable).
    if vix is not None:
        vix_score = _clamp(100.0 - max(0.0, vix - 13.0) * 4.0)
        comps.append(SurvivalComponent(
            name="VIX", score=round(vix_score, 0), detail=f"India VIX {vix:.1f}.",
        ))
    else:
        comps.append(SurvivalComponent(name="VIX", score=0, available=False, detail="VIX unavailable."))

    # Gamma acceleration: high gamma relative to premium = faster swings both ways.
    if quote is not None and quote.gamma:
        gamma_pen = quote.gamma * atr * atr / max(1.0, premium) * 100.0
        comps.append(SurvivalComponent(
            name="Gamma", score=round(_clamp(100.0 - gamma_pen), 0),
            detail=f"Γ {quote.gamma:.4f}.",
        ))
    else:
        comps.append(SurvivalComponent(name="Gamma", score=70, detail="No Γ."))

    out.components = comps
    avail = [c for c in comps if c.available]
    out.overall = round(sum(c.score for c in avail) / len(avail), 0) if avail else 0.0

    mn = settings.exec_survival_min
    if out.overall >= mn:
        out.recommendation = "SAFE"
    elif out.overall >= mn - 15:
        out.recommendation = "WAIT"
    elif out.overall >= mn - 30:
        out.recommendation = "CHANGE_STRIKE"
    else:
        out.recommendation = "SKIP"
    return out
