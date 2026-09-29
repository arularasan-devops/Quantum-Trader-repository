"""Where every signal went, and what stopped it — recorded as it happens.

Part 26 of the Phase 11A extension. For every production BUY the question this
answers is: did the engine generate it, did plan validation pass, was it
published to the dashboard, did the UI show it, did the journal get it, was it
paper-eligible, was it blocked before execution, and did execution or the broker
reject it. Nothing may disappear without a MISSED_STAGE and a MISSED_REASON.

Two design decisions worth stating, because they are what make this honest:

* **Execution refusals are not re-reported by hand.** Every gate in the auto
  entry path already reports itself to the execution funnel. This module
  subscribes to the funnel and attributes those events to the signal that was
  being executed at the time, so a gate added later is covered automatically and
  a gate cannot be represented differently in two ledgers.
* **A repeat is not a new signal.** The same call on the next tick belongs to the
  same episode and the same ``global_signal_id``; it is counted as a repeat.
  Five raw events collapsing into one episode with one dashboard signal is
  correct de-duplication, and only the *absence* of a dashboard signal is a
  miss (Part 29).

Measurement only: no function here decides, sizes, routes, delays or blocks
anything, and nothing in the trading path reads any of it back.
"""

from __future__ import annotations

import threading
import time

from app.analysis import exec_funnel
from app.analysis import signal_churn as churn
from app.analysis import signal_lifecycle as lc
from app.config import settings
from app.models import Decision, FuturesSignalCard, OptionType, Signal

# A repeat of the same call writes at most one ledger row per this interval. The
# in-memory repeat count is exact regardless; this only bounds the file, which
# would otherwise take one line per tick of an unchanged board. This is the
# fallback for when churn control is off, so the two regimes are comparable.
_REPEAT_WRITE_SEC = 300.0

_LOCK = threading.Lock()
# instrument -> (episode_id, global_signal_id, last_repeat_write_ts)
_current: dict[str, tuple[str, str, float]] = {}
# The same, for the futures branch. Kept separate so an option call and a futures
# call on the same instrument never overwrite each other's identity.
_futures_current: dict[str, tuple[str, str, float]] = {}
# The signal whose execution is being attempted on THIS thread. Instruments tick
# concurrently, so the context has to be per-thread or funnel events would be
# attributed to whichever instrument ticked last.
_ctx = threading.local()
# Signals already recorded as seen by a client, newest last. Bounded: this is a
# de-duplication aid, not a record.
_seen_visible: list[str] = []
_SEEN_MAX = 2000

# Funnel stage -> the lifecycle stage a refusal there prevented.
_STAGE_MAP = {
    "SIGNAL": lc.PAPER_ELIGIBLE,
    "RISK": lc.PAPER_ELIGIBLE,
    "VALIDATION": lc.EXECUTION_CHECK,
    "EXECUTION": lc.EXECUTION_ACCEPTED,
    "BROKER": lc.FILLED,
    "ACCEPTED": lc.FILLED,
    "FILLED": lc.FILLED,
}

# Funnel stage reached -> the lifecycle stage that proves it.
_REACHED_MAP = {
    "SIGNAL": lc.PAPER_ELIGIBLE,
    "RISK": lc.PAPER_ELIGIBLE,
    "VALIDATION": lc.EXECUTION_CHECK,
    "EXECUTION": lc.EXECUTION_ACCEPTED,
    "BROKER": lc.EXECUTION_ACCEPTED,
    "ACCEPTED": lc.EXECUTION_ACCEPTED,
    "FILLED": lc.FILLED,
}

# Blocker name (or a prefix of one) -> MISSED_REASON. Anything unmatched is an
# EXECUTION_BLOCK with the raw blocker kept in the detail, so a new gate is never
# quietly folded into UNKNOWN.
_REASON_MAP = {
    "PLAN_STALE": lc.PLAN_STALE,
    "PLAN_INVALID": lc.PLAN_INVALID,
    "CAPITAL": lc.CAPITAL_BLOCK,
    "UNAFFORDABLE": lc.CAPITAL_BLOCK,
    "EPISODE_ALREADY_TRADED": lc.ALREADY_TRADED,
    "ALREADY_TRADED": lc.ALREADY_TRADED,
    "ALREADY_IN_POSITION": lc.ALREADY_TRADED,
    "COOLDOWN": lc.COOLDOWN,
    "REENTRY_COOLDOWN": lc.COOLDOWN,
    "STALE_FEED": lc.STALE_FEED,
    "FROZEN_PREMIUM": lc.STALE_FEED,
    "DATA_QUALITY": lc.STALE_FEED,
    "RISK": lc.RISK_BLOCK,
    "DAILY_LOSS": lc.RISK_BLOCK,
    "NON_POSITIVE_RISK": lc.RISK_BLOCK,
    "BROKER": lc.BROKER_REJECTION,
    "ORDER_REJECTED": lc.BROKER_REJECTION,
}


def _reason_for(blocker: str) -> tuple[str, bool]:
    """(MISSED_REASON, exact) for a funnel blocker name."""
    if blocker in _REASON_MAP:
        return _REASON_MAP[blocker], True
    for prefix, reason in _REASON_MAP.items():
        if blocker.startswith(prefix):
            return reason, True
    return lc.EXECUTION_BLOCK, False


def vehicle_of(dec: Decision) -> str:
    if dec.option_type == OptionType.CALL:
        return lc.CE
    if dec.option_type == OptionType.PUT:
        return lc.PE
    return "-"


def _is_buy(dec: Decision) -> bool:
    return dec.signal == Signal.BUY or dec.market_signal == Signal.BUY


def observe_option(instrument: str, dec: Decision, call_key: str,
                   now: float | None = None) -> None:
    """Stamp the pre-execution lifecycle of one option board call.

    Called once per tick, after the plan has been validated and before the
    execution path runs. It stamps generation, classification, the plan, plan
    validation and dashboard publication, and writes the ids back onto the
    decision so the dashboard, the journal and the reconciliation all name the
    same signal.
    """
    now = time.time() if now is None else now
    episode = lc.episode_id(instrument, call_key, now)
    vehicle = vehicle_of(dec)

    superseded: tuple[str, str, float] | None = None
    with _LOCK:
        prev = _current.get(instrument)
        repeat = prev is not None and prev[0] == episode
        if repeat:
            gsid = prev[1]
            due = now - prev[2] >= _REPEAT_WRITE_SEC
            _current[instrument] = (episode, gsid, now if due else prev[2])
        else:
            gsid = lc.new_signal_id(instrument, vehicle, now)
            due = False
            superseded = prev
            # The clock for repeat rows starts now, so the first repeat does not
            # immediately look overdue and restate a chain already written.
            _current[instrument] = (episode, gsid, now)

    dec.global_signal_id = gsid
    dec.episode_id = episode
    dec.market = lc.OPTIONS
    dec.vehicle = vehicle

    # Materiality is judged on the publishable state of the call, not on the
    # clock: an unchanged call is restated on the heartbeat, a changed one at
    # once. Every tick is counted either way (Phase 12 §1).
    ch = churn.observe(episode, _snapshot(dec), now,
                       instrument=instrument, market=lc.OPTIONS)
    dec.event_type = ch["event_type"]
    dec.raw_event_count = ch["raw_event_count"]
    dec.meaningful_update_count = ch["meaningful_update_count"]
    dec.last_published_at = ch["last_published_at"]

    common = dict(global_signal_id=gsid, episode=episode, instrument=instrument,
                  vehicle=vehicle, market=lc.OPTIONS, now=now)

    if repeat:
        publish = (ch["publish"] if settings.signal_churn_control else due)
        if publish:
            # The same call, again. Recorded as a repeat of a known episode so a
            # reader can tell 150 raw events collapsing into one call from 150
            # calls that never reached the dashboard. ``event_type`` says whether
            # anything actually changed, and the counters carry the raw events
            # that were folded in, so nothing is lost by not restating them.
            lc.record(stage=lc.GENERATED, status=lc.OK, source="state.tick",
                      reason=lc.DUPLICATE_EPISODE,
                      value=lc.episode_repeat_count(episode),
                      detail={
                          "event_type": ch["event_type"],
                          "material_changes": ch["reasons"],
                          "raw_event_count": ch["raw_event_count"],
                          "meaningful_update_count":
                              ch["meaningful_update_count"],
                          "suppressed_since_last_publication":
                              ch["suppressed_count"],
                      }, **common)
        return

    if superseded is not None and _is_buy(dec):
        # The previous call for this instrument is no longer what the board is
        # showing. It is not lost — it is superseded, and it says so.
        lc.missed(global_signal_id=superseded[1], episode=superseded[0],
                  stage=lc.USER_VISIBLE, reason=lc.EPISODE_SUPERSEDED,
                  instrument=instrument, market=lc.OPTIONS,
                  source="state.tick", detail={"superseded_by": gsid}, now=now)

    action = (dec.market_signal or dec.signal or Signal.WAIT).value
    lc.record(stage=lc.GENERATED, source="engine.decide",
              value=dec.confidence, reason=action, **common)
    lc.record(stage=lc.CLASSIFIED, source="engine.decide",
              reason=dec.entry_trigger or dec.trade_quality or None,
              value=dec.trade_score, **common)

    has_levels = dec.stop_loss is not None and dec.target1 is not None
    if has_levels:
        lc.record(stage=lc.PLAN_CREATED, source="engine.decide",
                  value=dec.current_premium, threshold=dec.stop_loss,
                  detail={"target1": dec.target1, "plan_version": dec.plan_version},
                  **common)
    elif _is_buy(dec):
        lc.missed(stage=lc.PLAN_CREATED, reason=lc.PLAN_INVALID,
                  source="engine.decide", value=dec.current_premium,
                  detail={"missing": "stop_or_target"}, **common)

    if not _is_buy(dec):
        # A WAIT is a statement about the market, not a signal that went missing.
        # It is published like any other and carries no plan to validate.
        lc.record(stage=lc.DASHBOARD_PUBLISHED, source="state.tick",
                  reason=action, **common)
        return

    if dec.plan_actionable and has_levels:
        lc.record(stage=lc.PLAN_VALIDATED, source="state._mark_plan",
                  value=dec.current_premium, threshold=dec.target1,
                  detail={"plan_state": dec.plan_state,
                          "plan_version": dec.plan_version}, **common)
    else:
        reason = (lc.PLAN_STALE if dec.plan_state == "STALE" else lc.PLAN_INVALID)
        lc.missed(stage=lc.PLAN_VALIDATED, reason=reason,
                  source="state._mark_plan", value=dec.current_premium,
                  threshold=dec.target1,
                  detail={"plan_state": dec.plan_state,
                          "plan_invalid_reason": dec.plan_invalid_reason},
                  **common)

    lc.record(stage=lc.DASHBOARD_PUBLISHED, source="state.tick",
              reason=action, value=dec.current_premium,
              detail={"event_type": ch["event_type"]}, **common)


def _snapshot(dec: Decision) -> churn.Snapshot:
    """The publishable state of an option call, for the materiality test."""
    zone = dec.entry_range or (None, None)
    return churn.Snapshot(
        action=(dec.market_signal or dec.signal or Signal.WAIT).value,
        premium=dec.current_premium,
        score=dec.trade_score,
        direction=vehicle_of(dec),
        strike=dec.strike,
        setup=dec.entry_trigger or dec.trade_quality,
        plan_version=dec.plan_version,
        regime=dec.htf_trend,
        entry_low=zone[0],
        entry_high=zone[1],
        actionable=dec.plan_actionable,
    )


def _futures_snapshot(card: FuturesSignalCard) -> churn.Snapshot:
    """The same, for a research futures plan."""
    return churn.Snapshot(
        action=card.signal,
        premium=card.entry if card.entry is not None else card.price,
        score=card.signal_score,
        direction=card.direction,
        strike=None,
        setup=card.setup_type,
        plan_version=card.plan_version,
        regime=card.regime,
        entry_low=card.entry_zone_low,
        entry_high=card.entry_zone_high,
        actionable=card.status == "VALID_FUTURES_PLAN",
    )


def observe_futures(instrument: str, card: FuturesSignalCard,
                    now: float | None = None) -> None:
    """Stamp the lifecycle of one research futures signal.

    The futures branch gets its own episode key — instrument, contract and
    direction — so a futures plan is never conflated with the option call on the
    same market event, and both can be counted separately in the reconciliation.

    The chain stops at DASHBOARD_PUBLISHED by design: there is no paper
    eligibility, no execution check and no fill stage here, because this vehicle
    has no order route at all. A futures card that reaches execution stages would
    itself be the bug.
    """
    now = time.time() if now is None else now
    key = f"FUT|{card.contract or instrument}|{card.direction or 'NONE'}"
    episode = lc.episode_id(instrument, key, now)

    with _LOCK:
        prev = _futures_current.get(instrument)
        repeat = prev is not None and prev[0] == episode
        if repeat:
            gsid = prev[1]
            due = now - prev[2] >= _REPEAT_WRITE_SEC
            _futures_current[instrument] = (episode, gsid, now if due else prev[2])
        else:
            gsid = lc.new_signal_id(instrument, lc.FUTURES, now)
            due = False
            _futures_current[instrument] = (episode, gsid, now)

    card.global_signal_id = gsid
    card.episode_id = episode
    card.futures_signal_id = gsid
    card.market = lc.FUTURES
    card.vehicle = lc.FUTURES

    ch = churn.observe(episode, _futures_snapshot(card), now,
                       instrument=instrument, market=lc.FUTURES)
    card.event_type = ch["event_type"]
    card.raw_event_count = ch["raw_event_count"]
    card.meaningful_update_count = ch["meaningful_update_count"]
    card.last_published_at = ch["last_published_at"]

    common = dict(global_signal_id=gsid, episode=episode, instrument=instrument,
                  vehicle=lc.FUTURES, market=lc.FUTURES, now=now)

    if repeat:
        publish = (ch["publish"] if settings.signal_churn_control else due)
        if publish:
            lc.record(stage=lc.GENERATED, source="futures.research",
                      reason=lc.DUPLICATE_EPISODE,
                      value=lc.episode_repeat_count(episode),
                      detail={
                          "event_type": ch["event_type"],
                          "material_changes": ch["reasons"],
                          "raw_event_count": ch["raw_event_count"],
                          "meaningful_update_count":
                              ch["meaningful_update_count"],
                          "suppressed_since_last_publication":
                              ch["suppressed_count"],
                      }, **common)
        return

    lc.record(stage=lc.GENERATED, source="futures.research",
              reason=card.signal, value=card.signal_score, **common)
    lc.record(stage=lc.CLASSIFIED, source="futures.research",
              reason=card.setup_type, value=card.signal_score, **common)

    if card.signal == "NO_SIGNAL":
        # No direction is a statement about the market, not a lost signal.
        lc.record(stage=lc.DASHBOARD_PUBLISHED, source="futures.research",
                  reason=card.invalid_reason or card.signal, **common)
        return

    lc.record(stage=lc.PLAN_CREATED, source="futures.research",
              value=card.entry, threshold=card.stop,
              detail={"target1": card.target1, "risk_points": card.risk_points},
              **common)
    if card.status == "VALID_FUTURES_PLAN":
        lc.record(stage=lc.PLAN_VALIDATED, source="futures.research",
                  value=card.reward_risk_t1, threshold=card.cost_to_risk_pct,
                  detail={"status": card.status}, **common)
    else:
        lc.missed(stage=lc.PLAN_VALIDATED, reason=lc.PLAN_INVALID,
                  source="futures.research", value=card.reward_risk_t1,
                  detail={"status": card.status,
                          "invalid_reason": card.invalid_reason,
                          "failed_checks": [c.name for c in card.checks
                                            if not c.passed]}, **common)
    lc.record(stage=lc.DASHBOARD_PUBLISHED, source="futures.research",
              reason=card.signal, value=card.price,
              detail={"event_type": ch["event_type"]}, **common)


def mark_futures_visible(instrument: str, card: FuturesSignalCard | None,
                         source: str = "api.futures_signal",
                         now: float | None = None) -> None:
    """A client received this futures card."""
    if card is None or not card.global_signal_id or not card.episode_id:
        return
    with _LOCK:
        if card.global_signal_id in _seen_visible:
            return
        _seen_visible.append(card.global_signal_id)
        if len(_seen_visible) > _SEEN_MAX:
            del _seen_visible[:-_SEEN_MAX]
    lc.record(global_signal_id=card.global_signal_id, episode=card.episode_id,
              instrument=instrument, vehicle=lc.FUTURES, market=lc.FUTURES,
              stage=lc.USER_VISIBLE, source=source, reason=card.signal, now=now)


def _ids(dec: Decision, instrument: str) -> dict | None:
    if not dec.global_signal_id or not dec.episode_id:
        return None
    return dict(global_signal_id=dec.global_signal_id, episode=dec.episode_id,
                instrument=instrument, vehicle=dec.vehicle or "",
                market=dec.market or lc.OPTIONS)


def mark_user_visible(instrument: str, dec: Decision | None,
                      source: str = "api.snapshot",
                      now: float | None = None) -> None:
    """The snapshot carrying this signal was actually served to a client.

    Publication and visibility are different claims: the first is the backend
    putting the signal in the snapshot, the second is a client receiving it. The
    measured complaint — "it is in Reports but I never saw it" — needs both.
    """
    if dec is None:
        return
    ids = _ids(dec, instrument)
    if ids is None:
        return
    # The dashboard polls, so the same signal is served many times. Visibility
    # is a fact about the signal, not about the poll: record it once.
    with _LOCK:
        if dec.global_signal_id in _seen_visible:
            return
        _seen_visible.append(dec.global_signal_id)
        if len(_seen_visible) > _SEEN_MAX:
            del _seen_visible[:-_SEEN_MAX]
    lc.record(stage=lc.USER_VISIBLE, source=source, now=now,
              reason=(dec.signal.value if dec.signal else None), **ids)


def mark_journalled(instrument: str, dec: Decision, signal_id: str,
                    followed: bool, reason: str | None,
                    now: float | None = None) -> None:
    """The signal journal recorded this call, and whether it is being followed."""
    ids = _ids(dec, instrument)
    if ids is None:
        return
    lc.record(stage=lc.PAPER_ELIGIBLE if followed else lc.PLAN_VALIDATED,
              status=lc.OK if followed else lc.REFUSED,
              source="signal_journal", reason=reason,
              detail={"journal_signal_id": signal_id, "followed": followed},
              now=now, **ids)


class execution_context:
    """Attribute every funnel event raised inside this block to one signal.

    Used around the auto-entry path so the gates already reporting to the
    execution funnel do not have to report themselves to the lifecycle too.
    """

    def __init__(self, instrument: str, dec: Decision) -> None:
        self.instrument = instrument
        self.dec = dec

    def __enter__(self) -> "execution_context":
        _ctx.active = (self.instrument, self.dec)
        return self

    def __exit__(self, *exc) -> None:
        _ctx.active = None
        return None


def _active() -> tuple[str, Decision] | None:
    return getattr(_ctx, "active", None)


def _on_funnel_event(event: dict) -> None:
    """Attribute one funnel event to the signal whose execution raised it."""
    active = _active()
    if active is None:
        return
    instrument, dec = active
    if not dec.global_signal_id or not dec.episode_id:
        return
    common = dict(global_signal_id=dec.global_signal_id,
                  episode=dec.episode_id, instrument=instrument,
                  vehicle=dec.vehicle or "", market=dec.market or lc.OPTIONS,
                  now=event.get("ts"))
    if event.get("kind") == "REACHED":
        stage = _REACHED_MAP.get(str(event.get("stage")))
        if stage is None:
            return
        lc.record(stage=stage, source="exec_funnel",
                  reason=str(event.get("stage")), **common)
        return

    blocker = str(event.get("primary_blocker") or lc.UNKNOWN)
    reason, exact = _reason_for(blocker)
    stage = _STAGE_MAP.get(str(event.get("stage")), lc.EXECUTION_CHECK)
    detail = {"funnel_stage": event.get("stage"), "blocker": blocker,
              "where": event.get("where"), "text": event.get("reason")}
    if not exact:
        detail["blocker_unmapped"] = True
    lc.missed(stage=stage, reason=reason, source="exec_funnel",
              value=event.get("value"), threshold=event.get("threshold"),
              detail=detail, **common)


def mark_filled(instrument: str, dec: Decision, premium: float, lots: int,
                now: float | None = None) -> None:
    """A paper or live fill happened for this signal."""
    ids = _ids(dec, instrument)
    if ids is None:
        return
    lc.record(stage=lc.FILLED, source="auto_trade", value=premium,
              detail={"lots": lots}, now=now, **ids)
    lc.record(stage=lc.POSITION_OPEN, source="auto_trade", value=premium,
              detail={"lots": lots}, now=now, **ids)


def mark_exited(instrument: str, dec: Decision, premium: float | None,
                reason: str | None, now: float | None = None) -> None:
    """The position opened for this signal was closed."""
    ids = _ids(dec, instrument)
    if ids is None:
        return
    lc.record(stage=lc.EXITED, source="auto_trade", reason=reason,
              value=premium, now=now, **ids)


def reset_for_tests() -> None:
    """Clear process state. Used by the smoke; never called by the app."""
    with _LOCK:
        _current.clear()
        _futures_current.clear()
        _seen_visible.clear()
    churn.reset_for_tests()
    _ctx.active = None


exec_funnel.subscribe(_on_funnel_event)
