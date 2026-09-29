"""Exit advisor for open PAPER positions — research, applied to paper only.

Deliberately not a learned model. Exit timing needs labels of the form "what
would each alternative exit have returned", and the only place those labels can
come from honestly is closed paper trades on real prices — of which there are
none yet. So this is a deterministic advisor built from the mechanism Phase 5
identified (momentum dying while price is extended is what precedes the giveback),
and its suggestions are journaled so that when there ARE enough closed trades the
advisor can be scored against simply holding to stop/target.

States: HOLD / TIGHTEN_STOP / PARTIAL_EXIT / EXIT.

It cannot exit a real position: there are none, and it has no path to one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.ai import paper

HOLD = "HOLD"
TIGHTEN_STOP = "TIGHTEN_STOP"
PARTIAL_EXIT = "PARTIAL_EXIT"
EXIT = "EXIT"


@dataclass(frozen=True)
class ExitAdvice:
    action: str = HOLD
    exit_reason: str | None = None       # only set when action == EXIT
    new_stop: float | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {"action": self.action, "exit_reason": self.exit_reason,
                "new_stop": self.new_stop, "reasons": list(self.reasons)}


def advise(trade: dict, feats: dict, regime_state: str, direction_bias: str,
           mark: float) -> ExitAdvice:
    """Advice for one open paper position at the current mark."""
    entry = float(trade.get("entry_premium") or 0.0)
    stop = float(trade.get("stop") or 0.0)
    t1 = float(trade.get("target1") or 0.0)
    side = str(trade.get("side") or "CE").upper()
    if entry <= 0 or stop <= 0 or mark <= 0:
        return ExitAdvice(reasons=("insufficient position data",))

    risk = max(0.01, entry - stop)
    r_now = (mark - entry) / risk
    with_move = (side == "CE") == (direction_bias == "UP")
    persistence = float(feats.get("persistence_10") or 0.0)
    reasons: list[str] = [f"open {r_now:+.2f}R"]

    # The underlying turned against the leg while the leg is still open.
    if direction_bias in ("UP", "DOWN") and not with_move and r_now > 0.2:
        return ExitAdvice(action=EXIT, exit_reason=paper.TREND_REVERSAL,
                          reasons=tuple(reasons + [
                              f"underlying bias flipped to {direction_bias} "
                              f"against a {side} position in profit"]))
    if direction_bias in ("UP", "DOWN") and not with_move:
        return ExitAdvice(action=TIGHTEN_STOP,
                          new_stop=round(max(stop, entry - 0.5 * risk), 2),
                          reasons=tuple(reasons + ["bias against the position"]))

    # Momentum gone while extended: the Phase 5 giveback signature.
    if regime_state in ("EXHAUSTED", "STALLED") and r_now > 0.3:
        return ExitAdvice(action=PARTIAL_EXIT,
                          reasons=tuple(reasons + [
                              f"{regime_state} with the move spent — bank part"]))
    if persistence <= 0.2 and r_now >= 0.6:
        return ExitAdvice(action=TIGHTEN_STOP,
                          new_stop=round(max(stop, entry + 0.1 * risk), 2),
                          reasons=tuple(reasons + [
                              "persistence gone with the trade in profit — "
                              "stop to just above breakeven"]))
    # Most of the way to target with the move intact: protect, don't cut.
    if t1 and mark >= entry + 0.8 * (t1 - entry):
        return ExitAdvice(action=TIGHTEN_STOP,
                          new_stop=round(max(stop, entry + 0.5 * risk), 2),
                          reasons=tuple(reasons + ["80% of the way to T1"]))
    return ExitAdvice(reasons=tuple(reasons + ["nothing says exit"]))
