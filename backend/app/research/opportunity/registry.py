"""§3 — the candidate registry.

One append-only log holds every candidate ever generated and every status
change it went through. Current state is the fold of the lines for a
``candidate_id``, so a later line can add a screening result without any
earlier line being rewritten.

Two rules are enforced here rather than documented and hoped for:

**A definition is immutable once it has observed a live session.** The sessions
were measured under the rule as written; editing the rule and keeping the
sessions is the cheapest way to manufacture a result, and it happens by
accident far more often than on purpose. An attempted edit at
:data:`~app.research.opportunity.SHADOW` or later is refused and the caller is
told to generate a new candidate, which costs it a new fingerprint and an
honest extra entry in the hypothesis count.

**Status moves only along declared transitions.** A candidate cannot return
from ``REJECTED`` to ``PAPER``, and cannot reach ``PAPER`` without having
passed a historical screen first. Without this the lifecycle is a suggestion,
and the one thing the promotion gate relies on is that a candidate in
``HOLDOUT`` really did go through the earlier stages.
"""
from __future__ import annotations

import datetime as dt
import zoneinfo

from app.research.opportunity import (
    CANDIDATE_FILE,
    DISCOVERY,
    FAMILIES,
    FROZEN_AT,
    SCHEMA_VERSION,
    STATUSES,
    TRANSITIONS,
    VERSION,
)
from app.research.opportunity import store

_IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# The fields that make a candidate what it is. Only these are fingerprinted:
# a change to any of them is a different hypothesis, and a change to anything
# else (a note, a status, a screening result) is not.
DEFINITION_FIELDS: tuple[str, ...] = (
    "candidate_name", "mechanism_family", "instrument_scope", "vehicle_scope",
    "entry_definition", "exit_definition", "cost_definition",
    "required_inputs", "train_period", "validation_period", "holdout_period",
    "minimum_trades", "minimum_sessions", "promotion_gate",
)

RECORD_FIELDS: tuple[str, ...] = DEFINITION_FIELDS + (
    "candidate_id", "definition_fingerprint", "historical_dataset_ids",
    "created_at", "status", "rejection_reason", "schema_version",
    "generator_version",
)


def now_ist() -> str:
    return dt.datetime.now(_IST).isoformat()


def fingerprint(definition: dict) -> str:
    """Hash of the definition fields only, so prose and status cannot move it."""
    return store.digest({k: definition.get(k) for k in DEFINITION_FIELDS})


def candidate_id(definition: dict) -> str:
    """Content-addressed identity: the same definition is the same candidate.

    Deliberately not a counter. Re-running the generator must converge on the
    candidates it already made rather than creating a second copy of each one
    and doubling the hypothesis count for nothing.
    """
    fam = str(definition.get("mechanism_family") or "?").split("_")[0]
    name = str(definition.get("candidate_name") or "unnamed")
    return f"{fam}_{name}_{fingerprint(definition)[:10]}"


def all_lines() -> list[dict]:
    return store.read(CANDIDATE_FILE)


def candidates() -> list[dict]:
    """Current state of every candidate, in the order first registered.

    A later line updates only the fields it carries: a status change that
    mentions nothing else must not blank the definition that established the
    candidate.
    """
    folded: dict[str, dict] = {}
    for row in all_lines():
        cid = row.get("candidate_id")
        if not cid:
            continue
        cur = folded.setdefault(cid, {})
        cur.update({k: v for k, v in row.items() if v is not None})
    return list(folded.values())


def get(cid: str) -> dict | None:
    for row in candidates():
        if row.get("candidate_id") == cid:
            return row
    return None


def by_status(status: str) -> list[dict]:
    return [c for c in candidates() if c.get("status") == status]


def register(definition: dict, *, dataset_ids: list[str] | None = None) -> dict:
    """Register a candidate in ``DISCOVERY``, or return the existing one.

    Idempotent on the definition. The return value carries ``created`` so a
    caller — and the hypothesis count — can tell a new hypothesis from one that
    was already being tested.
    """
    fam = definition.get("mechanism_family")
    if fam not in FAMILIES:
        raise ValueError(f"unknown mechanism family: {fam!r}")
    missing = [f for f in ("candidate_name", "entry_definition",
                           "exit_definition", "cost_definition",
                           "instrument_scope", "vehicle_scope")
               if not definition.get(f)]
    if missing:
        raise ValueError(f"candidate definition is incomplete: {missing}")

    cid = candidate_id(definition)
    existing = get(cid)
    if existing:
        return {**existing, "created": False}

    record = {k: definition.get(k) for k in DEFINITION_FIELDS}
    record.update({
        "candidate_id": cid,
        "definition_fingerprint": fingerprint(definition),
        "historical_dataset_ids": list(dataset_ids or []),
        "created_at": now_ist(),
        "status": DISCOVERY,
        "rejection_reason": None,
        "schema_version": SCHEMA_VERSION,
        "generator_version": VERSION,
    })
    store.append(CANDIDATE_FILE, record)
    return {**record, "created": True}


def set_status(cid: str, status: str, *, reason: str | None = None,
               detail: dict | None = None) -> dict:
    """Move a candidate along a declared transition, or refuse.

    The refusal is a returned result rather than an exception: a cycle over
    hundreds of candidates must not abort because one of them was already
    rejected, and the reason has to end up in the report either way.
    """
    if status not in STATUSES:
        return {"ok": False, "error": f"unknown status {status!r}"}
    cur = get(cid)
    if not cur:
        return {"ok": False, "error": f"unknown candidate {cid!r}"}
    old = str(cur.get("status"))
    if old == status:
        return {"ok": True, "unchanged": True, "candidate_id": cid,
                "status": status}
    if status not in TRANSITIONS.get(old, frozenset()):
        return {"ok": False, "candidate_id": cid,
                "error": f"illegal transition {old} -> {status}",
                "legal": sorted(TRANSITIONS.get(old, frozenset()))}
    store.append(CANDIDATE_FILE, {
        "candidate_id": cid,
        "status": status,
        "rejection_reason": reason,
        "status_changed_at": now_ist(),
        "status_detail": detail,
        "schema_version": SCHEMA_VERSION,
    })
    return {"ok": True, "candidate_id": cid, "from": old, "status": status}


def amend_definition(cid: str, changes: dict) -> dict:
    """Edit a definition — refused once the candidate has observed live sessions.

    The refusal names the way forward, because the honest path is not blocked:
    generate the changed rule as its own candidate. It gets its own fingerprint
    and its own sample, and the sessions measured under the old rule stay
    attached to the old rule.
    """
    cur = get(cid)
    if not cur:
        return {"ok": False, "error": f"unknown candidate {cid!r}"}
    status = str(cur.get("status"))
    if status in FROZEN_AT:
        return {
            "ok": False,
            "candidate_id": cid,
            "status": status,
            "error": "DEFINITION_IS_FROZEN_AT_THIS_STATUS",
            "remedy": (
                "register the changed rule as a new candidate; it takes a new "
                "fingerprint and its own sample, and the sessions already "
                "measured stay attached to the definition that produced them"
            ),
        }
    touched = sorted(set(changes) & set(DEFINITION_FIELDS))
    if not touched:
        return {"ok": False, "candidate_id": cid,
                "error": "NOTHING_IN_THE_DEFINITION_WAS_CHANGED"}
    merged = {**{k: cur.get(k) for k in DEFINITION_FIELDS},
              **{k: changes[k] for k in touched}}
    new_id = candidate_id(merged)
    store.append(CANDIDATE_FILE, {
        "candidate_id": cid,
        "status": cur.get("status"),
        "superseded_by": new_id,
        "amended_at": now_ist(),
        "amended_fields": touched,
        "schema_version": SCHEMA_VERSION,
    })
    fresh = register({**merged,
                      "mechanism_family": cur.get("mechanism_family")})
    return {"ok": True, "candidate_id": new_id, "superseded": cid,
             "amended_fields": touched, "created": fresh.get("created")}


def counts() -> dict[str, int]:
    out = {s: 0 for s in STATUSES}
    for c in candidates():
        st = str(c.get("status"))
        if st in out:
            out[st] += 1
    return out
