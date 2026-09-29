"""AI trade orchestrator — turns the analysis stack into one paper decision.

    data quality → regime → direction → entry quality → probability → risk
                                     ↓
                    BUY_NOW / WAIT_PULLBACK / WATCH / NO_TRADE

Deterministic. Given the same inputs it returns the same decision, and every
decision carries the reason it was reached, so a bad decision can be argued with
rather than guessed at.

The order of the checks is the point. Data quality and regime hostility come
first, and no probability — however high — can override them: they encode the two
things this project has actually measured (a stale feed makes every downstream
number fiction; following an EXTENDED move loses on both instruments). The model
probability is consulted last and only ever *removes* trades.

BUY_NOW here means "the AI would open a PAPER position". It is not a
recommendation to the user, it is not shown on the production signal board, and
it cannot reach a broker.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.ai import direction as dir_engine
from app.ai import entry_quality as eq_engine
from app.ai import features as F
from app.ai import probability, regime
from app.config import settings

BUY_NOW = "BUY_NOW"
WAIT_PULLBACK = "WAIT_PULLBACK"
WATCH = "WATCH"
NO_TRADE = "NO_TRADE"
EXIT = "EXIT"


@dataclass(frozen=True)
class AIDecision:
    instrument: str
    decision: str = NO_TRADE
    side: str | None = None
    probability: float | None = None
    expected_r: float | None = None
    suggested_entry: float | None = None
    price: float | None = None
    atr: float | None = None
    feed_state: str = "NO_DATA"
    data_age_ms: float | None = None
    regime: dict = field(default_factory=dict)
    direction: dict = field(default_factory=dict)
    entry: dict = field(default_factory=dict)
    prob: dict = field(default_factory=dict)
    blocked_by: str | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)
    features: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "decision": self.decision,
            "side": self.side,
            "probability": self.probability,
            "expected_r": self.expected_r,
            "suggested_entry": self.suggested_entry,
            "price": self.price,
            "atr": self.atr,
            "feed_state": self.feed_state,
            "data_age_ms": self.data_age_ms,
            "regime": self.regime,
            "direction": self.direction,
            "entry_quality": self.entry,
            "probability_detail": self.prob,
            "blocked_by": self.blocked_by,
            "reasons": list(self.reasons),
            "real_money_execution": "DISABLED",
        }


def _no(inst: str, blocked: str, why: str, **kw) -> AIDecision:
    return AIDecision(instrument=inst, decision=NO_TRADE, blocked_by=blocked,
                      reasons=(why,), **kw)


def evaluate(instrument: str, candles: list, feed_state: str,
             data_age_ms: float | None, scan_state: str | None = None,
             scan_verdict: str | None = None) -> AIDecision:
    """One AI decision for one instrument from candles ending at the current bar."""
    if not candles or len(candles) < F.MIN_WINDOW:
        return _no(instrument, "NO_DATA",
                   f"need {F.MIN_WINDOW} bars, have {len(candles or [])}",
                   feed_state=feed_state, data_age_ms=data_age_ms)

    # 1. Data quality. Everything downstream is arithmetic on these prices.
    if feed_state in ("STALE", "DEAD", "NO_DATA"):
        return _no(instrument, f"FEED_{feed_state}",
                   f"feed is {feed_state} — no decision is meaningful on it",
                   feed_state=feed_state, data_age_ms=data_age_ms)

    feats = F.compute(list(candles)[-F.WINDOW:])
    if feats is None:
        return _no(instrument, "NO_FEATURES", "features not computable on this window",
                   feed_state=feed_state, data_age_ms=data_age_ms)

    price = float(feats["_price"])
    atr = float(feats["_atr"])
    common = {"price": price, "atr": atr, "feed_state": feed_state,
              "data_age_ms": data_age_ms, "features": feats}

    # 2. Regime.
    reg = regime.classify(feats, scan_state)
    reasons: list[str] = [f"{reg.state} (conf {reg.confidence:.0f})"]
    if reg.confidence < settings.ai_min_regime_confidence:
        return AIDecision(instrument=instrument, decision=WATCH,
                          regime=reg.as_dict(), blocked_by="REGIME_UNCLEAR",
                          reasons=tuple(reasons + [
                              f"regime confidence {reg.confidence:.0f} < "
                              f"{settings.ai_min_regime_confidence:.0f} — "
                              "state not identified well enough to act on"]),
                          **common)

    # 3. Direction. Both sides scored on the same bar; no standing side bias.
    dr = dir_engine.evaluate(feats, reg.direction_bias)
    if not dr.available:
        return AIDecision(instrument=instrument, decision=WATCH,
                          regime=reg.as_dict(), direction=dr.as_dict(),
                          blocked_by="MODEL_UNAVAILABLE",
                          reasons=tuple(reasons + list(dr.reasons) + [
                              "no probability model loaded — the AI will not "
                              "guess a side"]),
                          **common)
    if dr.side is None:
        return AIDecision(instrument=instrument, decision=WATCH,
                          regime=reg.as_dict(), direction=dr.as_dict(),
                          blocked_by="NO_SIDE_EDGE",
                          reasons=tuple(reasons + list(dr.reasons)), **common)
    side = dr.side
    reasons += list(dr.reasons)

    # 4. Entry quality — the layer Phase 5 pointed at.
    eq = eq_engine.evaluate(feats, side, reg.state, reg.follow_ok, reg.extension_atr)
    reasons += list(eq.reasons)
    prob = probability.score(feats, side)
    p = prob.get("p_target_before_stop")
    exp_r = prob.get("expected_r")

    base = dict(instrument=instrument, side=side, probability=p, expected_r=exp_r,
                regime=reg.as_dict(), direction=dr.as_dict(), entry=eq.as_dict(),
                prob=prob, **common)

    if eq.verdict == eq_engine.TOO_EXTENDED:
        return AIDecision(decision=NO_TRADE, blocked_by="TOO_EXTENDED",
                          reasons=tuple(reasons), **base)
    if eq.verdict == eq_engine.NO_ENTRY:
        return AIDecision(decision=NO_TRADE, blocked_by="NO_ENTRY",
                          reasons=tuple(reasons), **base)

    # 5. Probability. Consulted last, and only ever removes trades.
    if p is None:
        return AIDecision(decision=WATCH, blocked_by="NO_PROBABILITY",
                          reasons=tuple(reasons), **base)
    reasons.append(f"P(target before stop) {p:.3f}, expected {exp_r:+.3f}R "
                   "(underlying basis, before option costs)")
    if p < settings.ai_min_probability:
        return AIDecision(decision=WATCH, blocked_by="PROBABILITY_BELOW_THRESHOLD",
                          reasons=tuple(reasons + [
                              f"{p:.3f} < {settings.ai_min_probability:.3f}"]),
                          **base)
    if eq.score < settings.ai_min_entry_quality:
        return AIDecision(decision=WAIT_PULLBACK,
                          suggested_entry=eq.suggested_entry,
                          blocked_by="ENTRY_QUALITY_LOW",
                          reasons=tuple(reasons + [
                              f"entry score {eq.score:.0f} < "
                              f"{settings.ai_min_entry_quality:.0f}"]),
                          **base)
    if eq.verdict == eq_engine.WAIT_PULLBACK:
        return AIDecision(decision=WAIT_PULLBACK,
                          suggested_entry=eq.suggested_entry,
                          blocked_by=None, reasons=tuple(reasons), **base)

    return AIDecision(decision=BUY_NOW, blocked_by=None,
                      reasons=tuple(reasons + ["all AI gates passed — PAPER only"]),
                      **base)
