"""The research recorder's tier gate — the approved slow-tier change.

RESEARCH ONLY. This module decides how often the **research observation
recorder** may re-read a book. It has no bearing on the production scanner, the
signal, the stop, the target, the broker or the order path, and nothing in it
returns a price, a decision or a size.

Why it exists rather than a one-line edit
-----------------------------------------
The sampled tier lived in :mod:`app.research.phase17.mcx` next to the MCX target
statistics, gated at 60 seconds to stop a day of capture reaching 784 MB. The
measured consequence, over 2026-09-17: 47 of 86 eligible events could not answer
a 15-second observation grid at all, because a 61-second recording cannot be
replayed at 15 seconds without the backfill the policy refuses. So the gate is
the binding constraint on the fairness comparison, and it moves here — where the
policy it serves is declared, fingerprinted and journalled — instead of becoming
an unexplained constant in a module about continuation percentiles.

What changes and what does not
------------------------------
* the sampled tier may be visited every :data:`SHADOW_SLOW_TIER_SEC` seconds;
* tier *membership* is unchanged — :func:`app.research.phase17.mcx.tier` still
  decides who is sampled, and no instrument enters or leaves;
* the fast tier is **not** reduced. It already visits finer than any 15-second
  policy needs, and CRUDEOIL — the one instrument whose cohorts are already
  comparable — is in it;
* the gate's inputs are the instrument, the clock and the last capture instant.
  The overlay state, the vehicle, the direction, the cost state and every future
  outcome are not inputs, which is the bias the change exists to remove;
* :data:`settings.phase50_shadow_slow_tier` returns the previous 60-second gate
  without a code change, for the rollback condition.

The honest limit, stated where the change is made: **a permission to visit is
not a quote.** HINDUNILVR's 88-second median gap is the feed's arrival rate, not
this gate, and no cadence policy here can improve it. Lifting the gate equalises
the recorder's contribution to the asymmetry and nothing else — so while the
fast tier runs at about a second, the all-market cadence is still unequal and
the all-market selected-vs-declined comparison stays withheld.
"""
from __future__ import annotations

import threading

from app.config import settings
from app.research.phase17 import mcx
from app.research.phase50 import (
    NO_ORDER_PATH,
    PAPER_ONLY,
    SHADOW_SLOW_TIER_SEC,
    SHADOW_TIER_ACTIVE,
    SHADOW_TIER_COST_IS_WRITES,
    SHADOW_TIER_FAST_UNCHANGED,
    SHADOW_TIER_FRESH_SAMPLE,
    SHADOW_TIER_GATE_IS_NOT_A_QUOTE,
    SHADOW_TIER_NO_BRANCH,
    SHADOW_TIER_NOT_EQUAL_MARKET,
    SHADOW_TIER_POLICY,
    SHADOW_TIER_REVERTED,
    SHADOW_TIER_ROLLBACK,
    SHADOW_TIER_RULE,
    SHADOW_TIER_SESSIONS_REQUIRED,
    VERSION,
    tier_cadence_fingerprint,
)
from app.research.phase50 import store

# The gate this replaces, kept as a named number so the report can print what
# the old cadence was rather than describing it as "about a minute".
PREVIOUS_SLOW_TIER_SEC = mcx.SAMPLE_INTERVAL_SEC

FAST_TIER_CADENCE = "EVERY_TICK_UNGATED_UNCHANGED_BY_THIS_INCREMENT"

_LOCK = threading.Lock()
# Sessions already journalled in this process, so the tick path writes one row
# per session per fingerprint instead of one per tick.
_NOTED: set[tuple[str, str, bool]] = set()


def enabled() -> bool:
    """Is the 15-second sampled tier armed? False restores the 60-second gate."""
    return bool(settings.phase50_shadow_slow_tier)


def slow_tier_sec() -> float:
    """The sampled tier's current gate, in seconds."""
    return float(SHADOW_SLOW_TIER_SEC) if enabled() else float(PREVIOUS_SLOW_TIER_SEC)


def due(instrument: str, *, now: float, last_capture_ts: float | None) -> bool:
    """May the research recorder read this instrument's book on this tick?

    Same signature and same meaning as the gate it supersedes, so the tick path
    reads one interface. Deliberately a function of the instrument, the clock
    and the last capture instant only: an argument for the overlay state or the
    direction would be the branch this change exists to delete.
    """
    if mcx.tier(instrument) != mcx.SAMPLED:
        return True
    if last_capture_ts is None:
        return True
    return (float(now) - float(last_capture_ts)) >= slow_tier_sec()


def note_session(session: str, *, now: float) -> bool:
    """Journal that this session was captured under this cadence. Idempotent.

    Returns True when a row was written. This is the fresh-sample boundary as
    data: a comparison counts sessions carrying the new fingerprint instead of
    assuming that the sessions it happens to hold were all captured under one
    policy. A journal failure is swallowed — the recorder's job is to capture,
    and a missing bookkeeping row makes a session uncounted rather than
    mislabelled.
    """
    if not session:
        return False
    on = enabled()
    fingerprint = tier_cadence_fingerprint()
    key = (session, fingerprint, on)
    with _LOCK:
        if key in _NOTED:
            return False
        _NOTED.add(key)
    row = {
        "activation_id": store.activation_id(session, fingerprint, on),
        "session": session,
        "policy": SHADOW_TIER_POLICY,
        "tier_fingerprint": fingerprint,
        "slow_tier_sec": slow_tier_sec(),
        "fast_tier": FAST_TIER_CADENCE,
        "enabled": 1 if on else 0,
        "tier_rule": SHADOW_TIER_RULE,
        "fresh_sample_rule": SHADOW_TIER_FRESH_SAMPLE,
        "first_seen_ts": float(now),
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "version": VERSION,
    }
    try:
        conn = store.connect()
        try:
            store.insert_cadence(conn, [row])
        finally:
            conn.close()
    except Exception:
        with _LOCK:
            _NOTED.discard(key)
        return False
    return True


def policy() -> dict:
    """The frozen tier policy, for the report and the tests."""
    return {
        "policy": SHADOW_TIER_POLICY,
        "state": SHADOW_TIER_ACTIVE if enabled() else SHADOW_TIER_REVERTED,
        "enabled": enabled(),
        "previous_slow_tier_sec": float(PREVIOUS_SLOW_TIER_SEC),
        "slow_tier_sec": slow_tier_sec(),
        "declared_slow_tier_sec": float(SHADOW_SLOW_TIER_SEC),
        "fast_tier": FAST_TIER_CADENCE,
        "sampled_instruments": sorted(mcx.SAMPLED_CAPTURE),
        "fast_instruments": sorted(mcx.DEEP_CAPTURE),
        "tier_rule": SHADOW_TIER_RULE,
        "fast_tier_unchanged": SHADOW_TIER_FAST_UNCHANGED,
        "no_branch": SHADOW_TIER_NO_BRANCH,
        "not_equal_market": SHADOW_TIER_NOT_EQUAL_MARKET,
        "gate_is_not_a_quote": SHADOW_TIER_GATE_IS_NOT_A_QUOTE,
        "cost_is_writes_not_requests": SHADOW_TIER_COST_IS_WRITES,
        "rollback": SHADOW_TIER_ROLLBACK,
        "fresh_sample_rule": SHADOW_TIER_FRESH_SAMPLE,
        "sessions_required": int(SHADOW_TIER_SESSIONS_REQUIRED),
        "tier_fingerprint": tier_cadence_fingerprint(),
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
    }


def reset() -> None:
    """Forget which sessions this process journalled. Tests only."""
    with _LOCK:
        _NOTED.clear()
