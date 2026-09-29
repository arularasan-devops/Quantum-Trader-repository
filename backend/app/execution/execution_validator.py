"""Execution Validator — combine Entry Optimizer + Strike Selector + Trade
Survival into ONE advisory execution recommendation with a clear WHY.

ADVISORY. Produces ENTER_NOW / WAIT_FOR_RETRACEMENT / CHANGE_STRIKE / SKIP_TRADE
and an execution grade. It NEVER changes the BUY/WAIT direction, confidence, or
strategy — the caller attaches the result to the decision for display only.
"""
from __future__ import annotations

from app.execution import entry_optimizer, strike_selector, trade_survival
from app.models import (
    Decision,
    ExecutionIntelligence,
    IndicatorSnapshot,
    OptionQuote,
    Signal,
)


def _grade(score: float) -> str:
    if score >= 90:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 68:
        return "B"
    if score >= 55:
        return "C"
    return "D"


def _timing_label(pullback_prob: float | None, extended: bool) -> str:
    p = pullback_prob if pullback_prob is not None else 30.0
    if not extended and p < 30:
        return "Excellent"
    if p < 45:
        return "Good"
    if p < 60:
        return "Fair"
    return "Late"


def evaluate(
    decision: Decision,
    chain: list[OptionQuote],
    snap: IndicatorSnapshot,
    vix: float | None = None,
) -> ExecutionIntelligence:
    """Run the three modules and fuse them. Only meaningful for a fresh BUY; the
    caller guards on that + the feature flag."""
    quote = next((q for q in chain if q.symbol == decision.recommended_option), None)

    entry = entry_optimizer.assess(decision, snap, quote)
    strike = strike_selector.select(decision, chain, snap)
    survival = trade_survival.analyze(decision, quote, snap, vix)

    # Strike quality = the recommended strike's composite score.
    strike_quality: float | None = None
    if strike.candidates:
        rec = next((c for c in strike.candidates if c.symbol == strike.recommended_symbol), None)
        strike_quality = rec.score if rec else strike.candidates[0].score

    # Final execution action (priority order). Timing dominates a strike swap: a
    # better strike bought at an extended price is still a late entry.
    if survival.recommendation == "SKIP":
        action = "SKIP_TRADE"
    elif entry.action == "WAIT":
        action = "WAIT_FOR_RETRACEMENT"
    elif survival.recommendation == "CHANGE_STRIKE" or strike.changed:
        action = "CHANGE_STRIKE"
    elif survival.recommendation == "WAIT":
        action = "WAIT_FOR_RETRACEMENT"
    else:
        action = "ENTER_NOW"

    timing = _timing_label(entry.pullback_probability, entry.extended)
    timing_score = 100.0 - (entry.pullback_probability or 30.0)
    exec_score = (
        0.35 * timing_score
        + 0.30 * (strike_quality if strike_quality is not None else 60.0)
        + 0.35 * survival.overall
    )

    reasons: list[str] = []
    if action == "SKIP_TRADE":
        reasons.append(
            f"Trade survival only {survival.overall:.0f}/100 — unlikely to survive a normal "
            f"pullback; skipping is safer."
        )
    elif action == "CHANGE_STRIKE":
        reasons.append(strike.reason if strike.changed else
                       f"Survival {survival.overall:.0f}/100 favours a more resilient strike.")
    elif action == "WAIT_FOR_RETRACEMENT":
        if entry.action == "WAIT":
            reasons.extend(entry.reasons[:1])
        else:
            reasons.append(
                f"Survival {survival.overall:.0f}/100 — waiting for a better entry improves the buffer."
            )
    else:
        reasons.append(entry.reasons[0] if entry.reasons else "Timing, strike and survival all acceptable.")

    return ExecutionIntelligence(
        enabled=True,
        grade=_grade(exec_score),
        entry_timing=timing,
        strike_quality=round(strike_quality, 0) if strike_quality is not None else None,
        trade_survival=survival.overall,
        pullback_probability=entry.pullback_probability,
        recommended_action=action,
        baseline_note=(
            f"Baseline {decision.signal.value} unchanged — execution advisory only."
            if decision.signal == Signal.BUY else "Execution advisory only."
        ),
        reasons=reasons,
        entry=entry,
        strike=strike,
        survival=survival,
    )
