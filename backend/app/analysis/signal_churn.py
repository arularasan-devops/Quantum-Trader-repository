"""Material-change publication: a repeat is only re-published when it changed.

Phase 12 §1. The 25 Aug session recorded 24,301 lifecycle rows against 599
episodes, and 6,027 of those rows were the same call restated on the next tick.
A single ``SILVER…243000PE`` leg produced 2,578 raw events on its own. That noise
is not evidence of anything: it hides the handful of moments where a plan
actually changed, and it makes every count derived from event rows meaningless.

What this module does and does not do:

* It decides whether a repeat of a *known* episode is worth publishing again,
  and names the reason — ``INITIAL``, ``MEANINGFUL_UPDATE``, ``HEARTBEAT``,
  ``INVALIDATED``, ``RESOLVED``, or ``SUPPRESSED`` for one that is not.
* **No raw event is lost.** Every tick increments ``raw_event_count`` for its
  episode whether or not it is published, and the reason a suppressed tick was
  judged immaterial is kept on the episode, so suppression can be audited and
  cannot become a new silent-disappearance bug: the counters are reported next to
  the publication counts, and the difference between them is the churn removed.
* It never decides anything a trader sees as a decision. Materiality changes how
  often an unchanged call is restated, not whether the call exists, what it says,
  or whether it is eligible for paper or execution.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field

from app.config import settings

REPORT_JSON = "signal_churn_report.json"

# event_type values. A publication always carries exactly one of these.
INITIAL = "INITIAL"
MEANINGFUL_UPDATE = "MEANINGFUL_UPDATE"
HEARTBEAT = "HEARTBEAT"
INVALIDATED = "INVALIDATED"
RESOLVED = "RESOLVED"
SUPPRESSED = "SUPPRESSED"

EVENT_TYPES = (INITIAL, MEANINGFUL_UPDATE, HEARTBEAT, INVALIDATED, RESOLVED,
               SUPPRESSED)

# Why a repeat was material. Reported so a reader can see which trigger is
# responsible for the publications that remain.
PREMIUM_MOVED = "PREMIUM_MOVED"
SCORE_MOVED = "SCORE_MOVED"
ZONE_MOVED = "ENTRY_ZONE_MOVED"
DIRECTION_CHANGED = "DIRECTION_CHANGED"
STRIKE_CHANGED = "STRIKE_CHANGED"
SETUP_CHANGED = "SETUP_CHANGED"
PLAN_VERSION_CHANGED = "PLAN_VERSION_CHANGED"
REGIME_CHANGED = "REGIME_CHANGED"
ACTION_CHANGED = "ACTION_CHANGED"
INVALIDATION = "INVALIDATION"
REVALIDATION = "REVALIDATION"
HEARTBEAT_DUE = "HEARTBEAT_DUE"

NO_MATERIAL_CHANGE = "NO_MATERIAL_CHANGE"


@dataclass
class Snapshot:
    """The publishable state of one call, reduced to what materiality needs."""

    action: str | None = None
    premium: float | None = None
    score: float | None = None
    direction: str | None = None
    strike: float | None = None
    setup: str | None = None
    plan_version: int | None = None
    regime: str | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    actionable: bool | None = None

    def zone_mid(self) -> float | None:
        if self.entry_low is None or self.entry_high is None:
            return None
        return (float(self.entry_low) + float(self.entry_high)) / 2.0


@dataclass
class _State:
    published: Snapshot
    last_published_at: float
    raw_event_count: int = 1
    meaningful_update_count: int = 0
    heartbeat_count: int = 0
    suppressed_count: int = 0
    published_count: int = 1
    last_event_type: str = INITIAL
    last_reasons: list[str] = field(default_factory=list)
    last_suppressed_at: float | None = None
    last_suppressed_reason: str | None = None
    instrument: str = ""
    market: str = ""
    first_published_at: float = 0.0


_LOCK = threading.Lock()
_episodes: dict[str, _State] = {}
# Bounded so a long session cannot grow this without limit. Eviction is oldest
# first by first publication; the ledger keeps the record either way.
_MAX_EPISODES = 20_000


def _pct_move(now_v: float | None, then_v: float | None) -> float | None:
    if now_v is None or then_v is None:
        return None
    base = abs(float(then_v))
    if base <= 0:
        return None
    return abs(float(now_v) - float(then_v)) / base * 100.0


def _material(new: Snapshot, old: Snapshot) -> list[str]:
    """Every trigger that fired, in the order the spec lists them."""
    out: list[str] = []
    prem = _pct_move(new.premium, old.premium)
    if prem is not None and prem >= settings.churn_premium_pct:
        out.append(PREMIUM_MOVED)
    if (new.score is not None and old.score is not None
            and abs(float(new.score) - float(old.score))
            >= settings.churn_score_points):
        out.append(SCORE_MOVED)
    zone = _pct_move(new.zone_mid(), old.zone_mid())
    if zone is not None and zone >= settings.churn_zone_pct:
        out.append(ZONE_MOVED)
    if new.direction != old.direction:
        out.append(DIRECTION_CHANGED)
    if new.strike != old.strike:
        out.append(STRIKE_CHANGED)
    if new.setup != old.setup:
        out.append(SETUP_CHANGED)
    if new.plan_version != old.plan_version:
        out.append(PLAN_VERSION_CHANGED)
    if new.regime != old.regime:
        out.append(REGIME_CHANGED)
    if new.action != old.action:
        out.append(ACTION_CHANGED)
    if new.actionable is not None and new.actionable != old.actionable:
        # Both directions matter: a plan going stale is as publishable as one
        # becoming enterable again, and the two must not read the same.
        out.append(INVALIDATION if not new.actionable else REVALIDATION)
    return out


def observe(episode: str, snap: Snapshot, now: float, *,
            instrument: str = "", market: str = "") -> dict:
    """Count this raw event and say whether it should be published.

    Returns ``{"publish", "event_type", "reasons", "raw_event_count",
    "meaningful_update_count", "last_published_at", "suppressed_count"}``. The
    caller publishes only when ``publish`` is true, and records ``event_type``
    with whatever it writes.
    """
    with _LOCK:
        st = _episodes.get(episode)
        if st is None:
            if len(_episodes) >= _MAX_EPISODES:
                oldest = min(_episodes,
                             key=lambda k: _episodes[k].first_published_at)
                _episodes.pop(oldest, None)
            _episodes[episode] = _State(
                published=snap, last_published_at=now, instrument=instrument,
                market=market, first_published_at=now,
                last_reasons=[INITIAL])
            return {
                "publish": True,
                "event_type": INITIAL,
                "reasons": [INITIAL],
                "raw_event_count": 1,
                "meaningful_update_count": 0,
                "suppressed_count": 0,
                "last_published_at": now,
            }

        st.raw_event_count += 1
        if not settings.signal_churn_control:
            # The counters stay exact either way, so before/after is measurable
            # against the same episodes with the flag off.
            st.published_count += 1
            st.last_published_at = now
            st.published = snap
            st.last_event_type = MEANINGFUL_UPDATE
            return {
                "publish": True,
                "event_type": MEANINGFUL_UPDATE,
                "reasons": ["CHURN_CONTROL_DISABLED"],
                "raw_event_count": st.raw_event_count,
                "meaningful_update_count": st.meaningful_update_count,
                "suppressed_count": st.suppressed_count,
                "last_published_at": st.last_published_at,
            }

        reasons = _material(snap, st.published)
        if reasons:
            event_type = (INVALIDATED if INVALIDATION in reasons
                          else MEANINGFUL_UPDATE)
            st.meaningful_update_count += 1
        elif now - st.last_published_at >= settings.churn_heartbeat_seconds:
            event_type = HEARTBEAT
            reasons = [HEARTBEAT_DUE]
            st.heartbeat_count += 1
        else:
            st.suppressed_count += 1
            st.last_suppressed_at = now
            st.last_suppressed_reason = NO_MATERIAL_CHANGE
            st.last_event_type = SUPPRESSED
            return {
                "publish": False,
                "event_type": SUPPRESSED,
                "reasons": [NO_MATERIAL_CHANGE],
                "raw_event_count": st.raw_event_count,
                "meaningful_update_count": st.meaningful_update_count,
                "suppressed_count": st.suppressed_count,
                "last_published_at": st.last_published_at,
            }

        st.published = snap
        st.last_published_at = now
        st.published_count += 1
        st.last_event_type = event_type
        st.last_reasons = reasons
        return {
            "publish": True,
            "event_type": event_type,
            "reasons": reasons,
            "raw_event_count": st.raw_event_count,
            "meaningful_update_count": st.meaningful_update_count,
            "suppressed_count": st.suppressed_count,
            "last_published_at": st.last_published_at,
        }


def mark_terminal(episode: str, event_type: str, now: float) -> dict | None:
    """Record the episode's end (``INVALIDATED`` / ``RESOLVED``).

    A terminal event is always publishable: the point of throttling is to stop
    restating an unchanged live call, never to hide the moment one ends.
    """
    if event_type not in (INVALIDATED, RESOLVED):
        return None
    with _LOCK:
        st = _episodes.get(episode)
        if st is None:
            return None
        st.raw_event_count += 1
        st.published_count += 1
        st.last_published_at = now
        st.last_event_type = event_type
        st.last_reasons = [event_type]
        return {
            "publish": True,
            "event_type": event_type,
            "reasons": [event_type],
            "raw_event_count": st.raw_event_count,
            "meaningful_update_count": st.meaningful_update_count,
            "suppressed_count": st.suppressed_count,
            "last_published_at": st.last_published_at,
        }


def episode_stats(episode: str) -> dict | None:
    """The counters every episode must expose."""
    with _LOCK:
        st = _episodes.get(episode)
        if st is None:
            return None
        return _as_row(episode, st)


def _as_row(episode: str, st: _State) -> dict:
    return {
        "episode_id": episode,
        "instrument": st.instrument,
        "market": st.market,
        "raw_event_count": st.raw_event_count,
        "meaningful_update_count": st.meaningful_update_count,
        "heartbeat_count": st.heartbeat_count,
        "suppressed_count": st.suppressed_count,
        "published_count": st.published_count,
        "last_event_type": st.last_event_type,
        "last_reasons": list(st.last_reasons),
        "last_published_at": st.last_published_at,
        "last_suppressed_at": st.last_suppressed_at,
        "last_suppressed_reason": st.last_suppressed_reason,
        "first_published_at": st.first_published_at,
    }


def report(limit: int = 200) -> dict:
    """Churn per episode plus the session totals, noisiest episode first.

    ``raw_events`` versus ``publications`` is the before/after the spec asks
    for, measured on the same episodes rather than against a remembered number
    from a previous session.
    """
    with _LOCK:
        rows = [_as_row(ep, st) for ep, st in _episodes.items()]
    rows.sort(key=lambda r: r["raw_event_count"], reverse=True)
    raw = sum(r["raw_event_count"] for r in rows)
    pub = sum(r["published_count"] for r in rows)
    suppressed = sum(r["suppressed_count"] for r in rows)
    return {
        "enabled": settings.signal_churn_control,
        "thresholds": {
            "premium_pct": settings.churn_premium_pct,
            "score_points": settings.churn_score_points,
            "entry_zone_pct": settings.churn_zone_pct,
            "heartbeat_seconds": settings.churn_heartbeat_seconds,
        },
        "totals": {
            "episodes": len(rows),
            "raw_events": raw,
            "publications": pub,
            "suppressed": suppressed,
            "meaningful_updates": sum(r["meaningful_update_count"]
                                      for r in rows),
            "heartbeats": sum(r["heartbeat_count"] for r in rows),
            "churn_removed_pct": (round(suppressed / raw * 100.0, 1)
                                  if raw else 0.0),
            "publications_per_episode": (round(pub / len(rows), 2)
                                         if rows else 0.0),
        },
        "note": ("raw_event_count counts every tick, published or not: nothing "
                 "is lost, only restatement is throttled"),
        "episodes": rows[:limit],
    }


def write_report(limit: int = 200) -> str:
    os.makedirs(settings.data_dir, exist_ok=True)
    path = os.path.join(settings.data_dir, REPORT_JSON)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report(limit), fh, indent=2, default=str)
    os.replace(tmp, path)
    return path


def reset_for_tests() -> None:
    """Clear process state. Used by the smoke; never called by the app."""
    with _LOCK:
        _episodes.clear()
