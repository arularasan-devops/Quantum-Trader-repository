"""Phase 50 — the event journal and the reading register. Append-only, own file.

Two tables, and neither of them replaces anything.

``event_resolution`` holds **event summaries**, one row per resolution reading of
one event. Not one row per event: an event resolved at 11:00 with no later quote
and again at 15:40 with one is two facts, and the second must not overwrite the
first — the same reason Phase 47 journals a tally per marked-through instant
instead of keeping one mutable row per session. The observation rows the events
were built from are in the Phase 46 journal and are neither copied nor touched
here; only their ids are carried, so any event can be taken apart again.

``tally_reading`` holds **what the journal held when a tally was read**. This is
the field the registry was missing: without it, a reading of 11 taken while the
session was still writing rows and a reading of 11 taken when 476 already
existed look identical, and the second is the only one of the two that is wrong.
Coverage is recorded at read time and the accrual verdict is derived from it
later, never assumed.

No UPDATE and no DELETE appear in this file.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3

from app.config import settings
from app.research.phase50 import ARTEFACT_DIR, DB_NAME

SCHEMA = """
CREATE TABLE IF NOT EXISTS event_resolution (
    resolution_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    candidate_id TEXT,
    session TEXT NOT NULL,
    arm TEXT NOT NULL,
    definition TEXT NOT NULL,
    rule_fingerprint TEXT NOT NULL,
    resolution_fingerprint TEXT NOT NULL,
    instrument TEXT,
    vehicle TEXT,
    contract TEXT,
    expiry TEXT,
    strike REAL,
    direction TEXT,
    overlay_state TEXT,
    first_ts REAL,
    last_ts REAL,
    duration_seconds REAL,
    observation_count INTEGER NOT NULL,
    market_session_status TEXT NOT NULL,
    status_evidence TEXT,
    data_quality TEXT,
    resolution_status TEXT NOT NULL,
    resolution_reason TEXT,
    entry_side TEXT,
    entry_price REAL,
    entry_ts REAL,
    exit_side TEXT,
    exit_price REAL,
    exit_ts REAL,
    gross_pct REAL,
    cost_points REAL,
    cost_basis TEXT,
    charges_points REAL,
    spread_points REAL,
    net_pct REAL,
    net_points REAL,
    mfe_pct REAL,
    mae_pct REAL,
    t1_hit INTEGER,
    t2_hit INTEGER,
    t3_hit INTEGER,
    hold_minutes REAL,
    time_to_favorable_sec REAL,
    time_to_adverse_sec REAL,
    giveback_pct REAL,
    pct_of_mfe_given_back REAL,
    forward_samples INTEGER,
    forward_samples_dropped INTEGER,
    underlying_leg_ids TEXT,
    read_ts REAL NOT NULL,
    status TEXT NOT NULL,
    order_path TEXT NOT NULL,
    version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_event_session
    ON event_resolution(session DESC, arm);
CREATE INDEX IF NOT EXISTS ix_event_event ON event_resolution(event_id);
CREATE INDEX IF NOT EXISTS ix_event_read ON event_resolution(read_ts DESC);

CREATE TABLE IF NOT EXISTS tally_reading (
    reading_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    reading_ts REAL NOT NULL,
    session TEXT NOT NULL,
    arm TEXT NOT NULL,
    definition TEXT NOT NULL,
    journal_rows_available_at_read INTEGER,
    selected_rows_at_read INTEGER,
    event_count_at_read INTEGER,
    selection_bound INTEGER,
    count_is TEXT,
    selection_fingerprint TEXT NOT NULL,
    session_status TEXT NOT NULL,
    superseded_by TEXT,
    supersession_reason TEXT,
    tally_state TEXT NOT NULL,
    status TEXT NOT NULL,
    order_path TEXT NOT NULL,
    version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reading_snapshot ON tally_reading(snapshot_id);
CREATE INDEX IF NOT EXISTS ix_reading_session
    ON tally_reading(session DESC, arm);

CREATE TABLE IF NOT EXISTS policy_outcome (
    outcome_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    session TEXT NOT NULL,
    arm TEXT NOT NULL,
    overlay_partition TEXT NOT NULL,
    overlay_state_at_first_observation TEXT,
    production_signal TEXT,
    partition_rule TEXT NOT NULL,
    exit_policy_id TEXT NOT NULL,
    exit_policy_fingerprint TEXT NOT NULL,
    exit_policy_set_fingerprint TEXT NOT NULL,
    is_coverage_diagnostic INTEGER NOT NULL,
    candidate_id TEXT,
    instrument TEXT,
    vehicle TEXT,
    contract TEXT,
    expiry TEXT,
    strike REAL,
    direction TEXT,
    first_ts REAL,
    market_session_status TEXT NOT NULL,
    observation_count INTEGER NOT NULL,
    resolution_status TEXT NOT NULL,
    resolution_reason TEXT,
    entry_side TEXT,
    entry_price REAL,
    entry_ts REAL,
    exit_side TEXT,
    exit_price REAL,
    exit_ts REAL,
    exit_reason TEXT,
    exit_sample_source TEXT,
    hold_seconds REAL,
    gross_pct REAL,
    cost_points REAL,
    cost_basis TEXT,
    net_pct REAL,
    net_points REAL,
    mfe_pct REAL,
    mae_pct REAL,
    t1_hit INTEGER,
    t2_hit INTEGER,
    t3_hit INTEGER,
    giveback_pct REAL,
    forward_samples INTEGER,
    forward_samples_dropped INTEGER,
    read_ts REAL NOT NULL,
    status TEXT NOT NULL,
    order_path TEXT NOT NULL,
    version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_policy_session
    ON policy_outcome(session DESC, exit_policy_id);
CREATE INDEX IF NOT EXISTS ix_policy_event ON policy_outcome(event_id);
CREATE INDEX IF NOT EXISTS ix_policy_group
    ON policy_outcome(overlay_partition, exit_policy_id);

CREATE TABLE IF NOT EXISTS capture_cadence (
    activation_id TEXT PRIMARY KEY,
    session TEXT NOT NULL,
    policy TEXT NOT NULL,
    tier_fingerprint TEXT NOT NULL,
    slow_tier_sec REAL NOT NULL,
    fast_tier TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    tier_rule TEXT NOT NULL,
    fresh_sample_rule TEXT NOT NULL,
    first_seen_ts REAL NOT NULL,
    status TEXT NOT NULL,
    order_path TEXT NOT NULL,
    version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cadence_session
    ON capture_cadence(session DESC, tier_fingerprint);
"""

EVENT_COLUMNS: tuple[str, ...] = (
    "resolution_id", "event_id", "candidate_id", "session", "arm", "definition",
    "rule_fingerprint", "resolution_fingerprint", "instrument", "vehicle",
    "contract", "expiry", "strike", "direction", "overlay_state", "first_ts",
    "last_ts", "duration_seconds", "observation_count", "market_session_status",
    "status_evidence", "data_quality", "resolution_status", "resolution_reason",
    "entry_side", "entry_price", "entry_ts", "exit_side", "exit_price",
    "exit_ts", "gross_pct", "cost_points", "cost_basis", "charges_points",
    "spread_points", "net_pct", "net_points", "mfe_pct", "mae_pct", "t1_hit",
    "t2_hit", "t3_hit", "hold_minutes", "time_to_favorable_sec",
    "time_to_adverse_sec", "giveback_pct", "pct_of_mfe_given_back",
    "forward_samples", "forward_samples_dropped", "underlying_leg_ids",
    "read_ts", "status", "order_path", "version",
)

# One row per event per policy per outcome. The policy fingerprint is in the
# identity, so a row measured under a policy and a row measured under a later
# revision of that policy cannot collide — a table where they could would let a
# changed rule overwrite the results of the rule it replaced.
POLICY_COLUMNS: tuple[str, ...] = (
    "outcome_id", "event_id", "session", "arm", "overlay_partition",
    "overlay_state_at_first_observation", "production_signal",
    "partition_rule", "exit_policy_id", "exit_policy_fingerprint",
    "exit_policy_set_fingerprint", "is_coverage_diagnostic", "candidate_id",
    "instrument", "vehicle", "contract", "expiry", "strike", "direction",
    "first_ts", "market_session_status", "observation_count",
    "resolution_status", "resolution_reason", "entry_side", "entry_price",
    "entry_ts", "exit_side", "exit_price", "exit_ts", "exit_reason",
    "exit_sample_source", "hold_seconds", "gross_pct", "cost_points",
    "cost_basis", "net_pct", "net_points", "mfe_pct", "mae_pct", "t1_hit",
    "t2_hit", "t3_hit", "giveback_pct", "forward_samples",
    "forward_samples_dropped", "read_ts", "status", "order_path", "version",
)

# One row per session per cadence fingerprint. This is the fresh-sample
# boundary in data rather than in a comment: a session captured while the
# sampled tier was gated at 60 seconds and a session captured at 15 carry
# different fingerprints here, so a later comparison can count its own sessions
# instead of trusting that nobody pooled them.
CADENCE_COLUMNS: tuple[str, ...] = (
    "activation_id", "session", "policy", "tier_fingerprint", "slow_tier_sec",
    "fast_tier", "enabled", "tier_rule", "fresh_sample_rule", "first_seen_ts",
    "status", "order_path", "version",
)

READING_COLUMNS: tuple[str, ...] = (
    "reading_id", "snapshot_id", "reading_ts", "session", "arm", "definition",
    "journal_rows_available_at_read", "selected_rows_at_read",
    "event_count_at_read", "selection_bound", "count_is",
    "selection_fingerprint", "session_status", "superseded_by",
    "supersession_reason", "tally_state", "status", "order_path", "version",
)


def resolution_id(event_id: str, resolution_status: str, exit_ts: object) -> str:
    """One id per event per resolution *outcome*, so a re-read converges.

    The exit instant is part of it: an event re-resolved after a later quote
    arrived is a new fact and appends, while re-running the same reading twice
    writes nothing. Nothing is updated in either case.
    """
    stamp = ""
    if isinstance(exit_ts, (int, float)) and not isinstance(exit_ts, bool):
        stamp = str(int(exit_ts))
    raw = f"{event_id}|{resolution_status}|{stamp}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def outcome_id(
    event_id: str, policy_fingerprint: str, resolution_status: str,
    exit_ts: object,
) -> str:
    """One id per event per frozen policy per outcome, so a re-read converges."""
    stamp = ""
    if isinstance(exit_ts, (int, float)) and not isinstance(exit_ts, bool):
        stamp = str(int(exit_ts))
    raw = f"{event_id}|{policy_fingerprint}|{resolution_status}|{stamp}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def activation_id(session: str, tier_fingerprint: str, enabled: bool) -> str:
    """One id per session per cadence fingerprint per enabled state.

    A session that ran part-way under the new gate and part-way under a rollback
    therefore journals both, instead of the first line of the day deciding what
    the whole session is labelled as.
    """
    raw = f"{session}|{tier_fingerprint}|{'ON' if enabled else 'OFF'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def reading_id(snapshot_id: str, reading_ts: object) -> str:
    """One id per snapshot per reading instant."""
    stamp = ""
    if isinstance(reading_ts, (int, float)) and not isinstance(reading_ts, bool):
        stamp = str(int(reading_ts))
    return hashlib.sha256(f"{snapshot_id}|{stamp}".encode()).hexdigest()[:32]


def root() -> str:
    """The backend directory, so the CLI works from any working directory."""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )))


def data_dir() -> str:
    configured = settings.data_dir
    return (
        configured if os.path.isabs(configured)
        else os.path.join(root(), configured)
    )


def db_path() -> str:
    """The event journal file, under the configured data directory."""
    return os.path.join(data_dir(), ARTEFACT_DIR, DB_NAME)


def connect(path: str | None = None) -> sqlite3.Connection:
    """A connection with the schema applied and rows returned by name."""
    target = path or db_path()
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _insert(
    conn: sqlite3.Connection, table: str, columns: tuple[str, ...],
    key: str, rows: list[dict],
) -> dict:
    if not rows:
        return {"written": 0, "duplicate": 0}
    # Table, column list and conflict key come from this module's own constants,
    # never from a caller; every value is bound.
    placeholders = ",".join("?" for _ in columns)
    sql = (
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT({key}) DO NOTHING"
    )
    written = 0
    for row in rows:
        written += conn.execute(
            sql, [_cell(row.get(col)) for col in columns]
        ).rowcount
    conn.commit()
    return {"written": written, "duplicate": len(rows) - written}


def insert_events(conn: sqlite3.Connection, rows: list[dict]) -> dict:
    """Append event resolutions, ignoring ones already journalled."""
    return _insert(conn, "event_resolution", EVENT_COLUMNS, "resolution_id", rows)


def insert_policy_outcomes(conn: sqlite3.Connection, rows: list[dict]) -> dict:
    """Append per-policy event outcomes, ignoring ones already journalled."""
    return _insert(conn, "policy_outcome", POLICY_COLUMNS, "outcome_id", rows)


def policy_outcomes(
    conn: sqlite3.Connection, *, session: str | None = None,
    policy: str | None = None, limit: int = 2000,
) -> list[dict]:
    """Journalled per-policy outcomes, newest reading first."""
    bound = max(1, int(limit))
    clauses: list[str] = []
    params: list[object] = []
    if session:
        clauses.append("session = ?")
        params.append(session)
    if policy:
        clauses.append("exit_policy_id = ?")
        params.append(policy)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(bound)
    cur = conn.execute(
        f"SELECT * FROM policy_outcome {where} "
        f"ORDER BY read_ts DESC, first_ts DESC LIMIT ?",
        params,
    )
    return [dict(r) for r in cur.fetchall()]


def insert_cadence(conn: sqlite3.Connection, rows: list[dict]) -> dict:
    """Append cadence activations, ignoring ones already journalled."""
    return _insert(conn, "capture_cadence", CADENCE_COLUMNS, "activation_id", rows)


def cadence_sessions(
    conn: sqlite3.Connection, *, tier_fingerprint: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Journalled cadence activations, newest session first."""
    bound = max(1, int(limit))
    params: list[object] = []
    where = ""
    if tier_fingerprint:
        where = "WHERE tier_fingerprint = ?"
        params.append(tier_fingerprint)
    params.append(bound)
    cur = conn.execute(
        f"SELECT * FROM capture_cadence {where} "
        "ORDER BY session DESC, first_seen_ts DESC LIMIT ?",
        params,
    )
    return [dict(row) for row in cur.fetchall()]


def insert_readings(conn: sqlite3.Connection, rows: list[dict]) -> dict:
    """Append tally readings, ignoring ones already journalled."""
    return _insert(conn, "tally_reading", READING_COLUMNS, "reading_id", rows)


def _cell(value: object) -> object:
    if isinstance(value, bool):
        return 1 if value else 0
    if value is None or isinstance(value, (int, float, str, bytes)):
        return value
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, default=str)
    return str(value)


def events(
    conn: sqlite3.Connection, *, session: str | None = None,
    arm: str | None = None, limit: int = 500,
) -> list[dict]:
    """Journalled event resolutions, newest reading first."""
    bound = max(1, int(limit))
    clauses: list[str] = []
    params: list[object] = []
    if session:
        clauses.append("session = ?")
        params.append(session)
    if arm:
        clauses.append("arm = ?")
        params.append(arm)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(bound)
    cur = conn.execute(
        f"SELECT * FROM event_resolution {where} "
        f"ORDER BY read_ts DESC, first_ts DESC LIMIT ?",
        params,
    )
    return [dict(r) for r in cur.fetchall()]


def latest_per_event(
    conn: sqlite3.Connection, *, session: str | None = None,
    arm: str | None = None, limit: int = 5000,
) -> list[dict]:
    """The newest resolution reading of each event, newest event first.

    Reduced in Python rather than with a correlated subquery: the reduction is
    "newest reading per event", and getting that wrong in SQL fails by
    returning a plausible row from the wrong moment.
    """
    rows = events(conn, session=session, arm=arm, limit=limit)
    seen: dict[str, dict] = {}
    for row in rows:
        key = str(row.get("event_id"))
        if key not in seen:
            seen[key] = row
    return sorted(
        seen.values(), key=lambda r: (r.get("first_ts") or 0.0), reverse=True,
    )


def readings(
    conn: sqlite3.Connection, *, session: str | None = None, limit: int = 500,
) -> list[dict]:
    """Journalled tally readings, newest first."""
    bound = max(1, int(limit))
    if session:
        cur = conn.execute(
            "SELECT * FROM tally_reading WHERE session = ? "
            "ORDER BY reading_ts DESC LIMIT ?",
            (session, bound),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM tally_reading ORDER BY reading_ts DESC LIMIT ?",
            (bound,),
        )
    return [dict(r) for r in cur.fetchall()]


def coverage_by_snapshot(conn: sqlite3.Connection) -> dict[str, dict]:
    """The earliest reading recorded for each snapshot id, by id.

    Earliest rather than newest: what matters about a snapshot is what the
    journal held **when it was taken**, and a later reading of the same
    snapshot describes a later moment.
    """
    cur = conn.execute(
        "SELECT * FROM tally_reading ORDER BY reading_ts ASC LIMIT 5000"
    )
    out: dict[str, dict] = {}
    for raw in cur.fetchall():
        row = dict(raw)
        out.setdefault(str(row.get("snapshot_id")), row)
    return out


def counts(conn: sqlite3.Connection) -> dict:
    """Row totals for both tables and the span they cover."""
    ev = conn.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT event_id) AS events, "
        "COUNT(DISTINCT session) AS sessions, MIN(read_ts) AS first_ts, "
        "MAX(read_ts) AS last_ts FROM event_resolution"
    ).fetchone()
    rd = conn.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT snapshot_id) AS snapshots "
        "FROM tally_reading"
    ).fetchone()
    return {
        "resolution_rows": int(ev["n"] or 0),
        "events": int(ev["events"] or 0),
        "sessions": int(ev["sessions"] or 0),
        "first_ts": ev["first_ts"],
        "last_ts": ev["last_ts"],
        "reading_rows": int(rd["n"] or 0),
        "snapshots_read": int(rd["snapshots"] or 0),
        **_policy_counts(conn),
    }


def _policy_counts(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT event_id) AS events, "
        "COUNT(DISTINCT exit_policy_id) AS policies FROM policy_outcome"
    ).fetchone()
    return {
        "policy_outcome_rows": int(row["n"] or 0),
        "policy_events": int(row["events"] or 0),
        "policies_journalled": int(row["policies"] or 0),
    }
