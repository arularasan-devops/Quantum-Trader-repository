"""Refusals for a MANUAL entry — the click, not the engine.

Twelve journal rows were tagged ``WAIT_SETUP``. None of them was an execution
bug: nine were manual entries taken while the engine's own signal read WAIT
(mostly "waiting for a pullback", i.e. the trade the engine was declining to
take), and two were taken while the feed was still warming, with confidence 0.0
and score 0.0 because there was no market data to compute them from. Those
twelve are -Rs14,346 and reached T1 zero times out of twelve.

So the dashboard's BUY button was the only place in the system where the engine's
verdict could be ignored silently. This module gives it a verdict of its own. It
reads state that already exists and computes nothing new — no score, no
confidence formula, no gate on the engine's path. The engine still emits exactly
what it emitted before; a human just can no longer overrule it without being
told, in words, what is being overruled.

Every refusal names its reason so the user is never left guessing why a click
did nothing.
"""
from __future__ import annotations

from app.models import Decision, Signal

# A decision computed with no data behind it. Both read exactly 0.0 only while
# the feed is warming — a real setup the engine dislikes still scores above zero.
_WARMING = 0.0


def refusal(decision: Decision | None, feed_ready: bool) -> str | None:
    """Why this manual entry must not be taken, or ``None`` to allow it.

    Ordered by how fundamental the problem is: no decision at all, then no data
    behind the decision, then the engine actively saying no, then a plan that
    cannot be risk-managed.
    """
    if decision is None:
        return (
            "No decision yet — the engine has not produced a call for this "
            "instrument. Nothing to enter against."
        )
    if not feed_ready:
        return (
            "Feed not ready — market data is stale or absent, so the premium, "
            "stop and target on screen are not current prices."
        )
    if decision.confidence == _WARMING and decision.signal_strength == _WARMING:
        return (
            "Data still warming — confidence and score are both 0.0 because the "
            "engine has no history to compute them from, not because the setup "
            "scored badly. Two such entries cost Rs2,974 and neither reached T1."
        )

    # The position-independent market call is the one that answers "should I open
    # something right now"; ``signal`` becomes HOLD/EXIT once a position is open.
    call = decision.market_signal or decision.signal
    if call in (Signal.WAIT, Signal.NO_TRADE, Signal.AVOID):
        return (
            f"Engine says {call.value} — this is the setup it is declining to "
            "take. Nine such manual entries are -Rs11,372 with 0 of 9 reaching "
            "T1. Wait for the call to turn BUY."
        )

    entry = decision.current_premium
    if not isinstance(entry, (int, float)) or entry <= 0:
        return "Plan invalid — no entry premium on the recommended leg."
    if not decision.recommended_option:
        return "Plan invalid — no option leg recommended."

    stop = decision.stop_loss
    if not isinstance(stop, (int, float)) or stop <= 0:
        return "Stop invalid — the plan carries no stop, so risk is undefined."
    if stop >= entry:
        # The sign bug that made losing legs read as winners: risk = entry - stop
        # goes negative and inverts every R computed from it.
        return (
            f"Stop invalid — the stop ({stop}) is at or above the entry premium "
            f"({entry}). Risk would be negative and every R on this trade would "
            "come out with the wrong sign."
        )

    target = decision.target1
    if not isinstance(target, (int, float)) or target <= entry:
        return (
            "Target invalid — T1 is missing or is not above the entry premium, "
            "so the trade has no defined win."
        )
    return None
