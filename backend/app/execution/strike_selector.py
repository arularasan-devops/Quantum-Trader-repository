"""Strike Selector — score nearby strikes and recommend the most resilient leg.

ADVISORY. Given the engine's recommended option, it evaluates strikes within
``exec_strike_search`` steps either side (same option type) on Greeks, liquidity,
reward:risk and affordability, and suggests a better strike if one clearly wins.
It never changes the BUY/WAIT direction.
"""
from __future__ import annotations

from app.config import settings
from app.models import (
    Decision,
    IndicatorSnapshot,
    OptionQuote,
    OptionType,
    StrikeCandidate,
    StrikeSelection,
)


def _moneyness(strike: float, spot: float, is_call: bool) -> str:
    band = max(1.0, 0.001 * spot)
    if abs(strike - spot) <= band:
        return "ATM"
    if is_call:
        return "ITM" if strike < spot else "OTM"
    return "ITM" if strike > spot else "OTM"


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _score(q: OptionQuote, atr: float, max_oi: int, max_vol: int) -> tuple[float, float | None, str]:
    """Composite 0..100 score for one candidate + an approx reward:risk + reason."""
    d = abs(q.delta)
    # Delta: reward directional resilience but penalise very deep ITM (expensive,
    # low leverage). Peak around 0.55.
    delta_score = _clamp(100.0 - abs(d - 0.55) * 180.0)
    # Theta drag relative to premium (less decay = better).
    theta_ratio = abs(q.theta) / max(1.0, q.premium)
    theta_score = _clamp(100.0 - theta_ratio * 400.0)
    # Liquidity: OI + volume, normalised to the chain's best.
    oi_score = _clamp(100.0 * q.oi / max_oi) if max_oi > 0 else 50.0
    vol_score = _clamp(100.0 * q.volume / max_vol) if max_vol > 0 else 50.0
    liq_score = 0.5 * oi_score + 0.5 * vol_score
    # Approx reward:risk from delta capture over 1 ATR up vs a delta-based stop.
    rr: float | None = None
    if q.premium > 0:
        up = d * (2.0 * atr)
        down = max(0.5, d * (1.0 * atr))
        rr = round(up / down, 2)
    rr_score = _clamp((rr or 1.0) * 40.0)
    composite = round(
        0.32 * delta_score + 0.20 * theta_score + 0.28 * liq_score + 0.20 * rr_score, 1
    )
    reason = (
        f"Δ{d:.2f}, θ/prem {theta_ratio*100:.1f}%, OI {q.oi:,}, vol {q.volume:,}"
    )
    return composite, rr, reason


def select(
    decision: Decision,
    chain: list[OptionQuote],
    snap: IndicatorSnapshot,
) -> StrikeSelection:
    sel = StrikeSelection(current_symbol=decision.recommended_option)
    if not decision.recommended_option or decision.option_type is None or decision.spot_price is None:
        return sel

    is_call = decision.option_type == OptionType.CALL
    otype = OptionType.CALL if is_call else OptionType.PUT
    same = [q for q in chain if q.option_type == otype and q.premium > 0]
    if not same:
        return sel
    same.sort(key=lambda q: q.strike)

    cur = next((q for q in same if q.symbol == decision.recommended_option), None)
    if cur is None:
        return sel
    idx = same.index(cur)
    n = max(1, settings.exec_strike_search)
    window = same[max(0, idx - n): idx + n + 1]

    atr = snap.atr or (0.004 * decision.spot_price)
    max_oi = max((q.oi for q in window), default=0)
    max_vol = max((q.volume for q in window), default=0)

    candidates: list[StrikeCandidate] = []
    for q in window:
        score, rr, reason = _score(q, atr, max_oi, max_vol)
        candidates.append(StrikeCandidate(
            symbol=q.symbol, strike=q.strike,
            moneyness=_moneyness(q.strike, decision.spot_price, is_call),
            score=score, delta=round(q.delta, 3), theta=round(q.theta, 2),
            iv=round(q.iv, 3), oi=q.oi, volume=q.volume, premium=q.premium,
            reward_risk=rr, reason=reason,
        ))
    candidates.sort(key=lambda c: c.score, reverse=True)
    sel.candidates = candidates

    best = candidates[0]
    cur_cand = next((c for c in candidates if c.symbol == cur.symbol), None)
    sel.recommended_symbol = best.symbol
    # Only advise a switch when the best beats the current by a clear margin.
    if cur_cand is not None and best.symbol != cur.symbol and best.score - cur_cand.score >= 8.0:
        sel.changed = True
        sel.reason = (
            f"{best.strike:g} {otype.value} scores {best.score:.0f} vs {cur_cand.score:.0f} "
            f"for the current {cur.strike:g} — {best.reason}."
        )
    else:
        sel.recommended_symbol = cur.symbol
        sel.changed = False
        sel.reason = "Current strike is already the most resilient nearby leg."
    return sel
