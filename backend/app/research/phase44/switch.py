"""Phase 44 §3 — the switch, off by default in code rather than in config.

The dormancy is not a setting someone can forget to set. With no
``switch_event`` in the journal the state is :data:`phase44.DORMANT`, so a fresh
install, a restored backup and an empty database all behave the same way:
nothing is recorded. Arming is an explicit, recorded operator act.

Arming binds to the definition hash. If the rule is edited afterwards, the arm
lapses and recording refuses — because pooling rows measured under two rules
into one sample is exactly the mistake Phase 41 was built to prevent and
Phase 42 made anyway when its cost basis changed mid-study.

Nothing here can start a trade. The armed state gates one thing: whether a row
is written to a research journal.
"""
from __future__ import annotations

import sqlite3
import time

from app.research import phase44
from app.research.phase44 import freeze, gate


def latest(con: sqlite3.Connection) -> dict | None:
    """The most recent switch event, or ``None`` if it was never armed."""
    row = con.execute(
        "SELECT ts, state, definition, operator, note FROM switch_event"
        " ORDER BY event_id DESC LIMIT 1"
    ).fetchone()
    return None if row is None else {k: row[k] for k in row.keys()}


def state(con: sqlite3.Connection) -> dict:
    """Whether the recorder may write, and if not, why not.

    ``may_record`` is the only field a caller should branch on, and it is false
    unless every condition holds — an unknown state is a refusal, not a
    permission.
    """
    return gate.decision(latest(con), freeze.fingerprint()["definition"])


def arm(con: sqlite3.Connection, *, operator: str, note: str = "") -> dict:
    """Record an arming under the current definition and return the new state.

    An operator name is required rather than defaulted: the point of the
    append-only log is to answer who turned it on, and a default value would
    make the column always answer "someone".
    """
    if not operator.strip():
        raise ValueError("arming requires an operator name")
    _write(con, phase44.ARMED, operator=operator, note=note)
    return state(con)


def disarm(con: sqlite3.Connection, *, operator: str, note: str = "") -> dict:
    if not operator.strip():
        raise ValueError("disarming requires an operator name")
    _write(con, phase44.DORMANT, operator=operator, note=note)
    return state(con)


def _write(con: sqlite3.Connection, new_state: str, *,
           operator: str, note: str) -> None:
    con.execute(
        "INSERT INTO switch_event (ts, state, definition, operator, note)"
        " VALUES (?, ?, ?, ?, ?)",
        (time.time(), new_state, freeze.fingerprint()["definition"],
         operator.strip(), note.strip() or None),
    )
    con.commit()


def history(con: sqlite3.Connection) -> list[dict]:
    return [{k: r[k] for k in r.keys()} for r in con.execute(
        "SELECT ts, state, definition, operator, note FROM switch_event"
        " ORDER BY event_id"
    )]
