"""Phase 49 — the supersession registry. Append-only, and its own file.

A recorded tally is a photograph of what a board said. When the board was
wrong, the photograph is still a true record of what was shown, and editing it
would destroy the only evidence that the mistake happened. So the correction
lives here instead: one appended row saying *this tally is superseded by that
one, for this reason, at this time, under this selection*.

Its own database file, not a table inside the Phase 47 journal, for one
reason — this file contains no UPDATE and no DELETE against the thing it
describes, and cannot contain one, because it does not hold it. The Phase 47
schema is untouched by this phase and needs no migration.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time

from app.config import settings
from app.research.phase49 import ARTEFACT_DIR, DB_NAME

SCHEMA = """
CREATE TABLE IF NOT EXISTS tally_supersession (
    supersession_id TEXT PRIMARY KEY,
    superseded_snapshot_id TEXT NOT NULL,
    superseding_snapshot_id TEXT NOT NULL,
    session TEXT NOT NULL,
    arm TEXT NOT NULL,
    definition TEXT NOT NULL,
    superseded_calls INTEGER,
    superseding_calls INTEGER,
    reason TEXT NOT NULL,
    superseded_selection TEXT NOT NULL,
    selection TEXT NOT NULL,
    selection_fingerprint TEXT NOT NULL,
    recorded_ts REAL NOT NULL,
    version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_supersession_superseded
    ON tally_supersession(superseded_snapshot_id);
CREATE INDEX IF NOT EXISTS ix_supersession_session
    ON tally_supersession(session DESC, arm);
"""

COLUMNS: tuple[str, ...] = (
    "supersession_id", "superseded_snapshot_id", "superseding_snapshot_id",
    "session", "arm", "definition", "superseded_calls", "superseding_calls",
    "reason", "superseded_selection", "selection", "selection_fingerprint",
    "recorded_ts", "version",
)


def supersession_id(superseded: str, superseding: str) -> str:
    """Deterministic, so re-running the correction converges instead of stacking."""
    raw = f"{superseded}|{superseding}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def root() -> str:
    """The backend directory, so the CLI works from any working directory."""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )))


def db_path() -> str:
    """The registry file, under the configured data directory."""
    configured = settings.data_dir
    base = (
        configured if os.path.isabs(configured)
        else os.path.join(root(), configured)
    )
    return os.path.join(base, ARTEFACT_DIR, DB_NAME)


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


def insert(conn: sqlite3.Connection, rows: list[dict]) -> dict:
    """Append supersession records, ignoring ones already registered."""
    if not rows:
        return {"written": 0, "duplicate": 0}
    placeholders = ",".join("?" for _ in COLUMNS)
    sql = (
        f"INSERT INTO tally_supersession ({','.join(COLUMNS)}) "
        f"VALUES ({placeholders}) ON CONFLICT(supersession_id) DO NOTHING"
    )
    now = time.time()
    written = 0
    for row in rows:
        values = [
            _cell(now if col == "recorded_ts" and row.get(col) is None
                  else row.get(col))
            for col in COLUMNS
        ]
        written += conn.execute(sql, values).rowcount
    conn.commit()
    return {"written": written, "duplicate": len(rows) - written}


def _cell(value: object) -> object:
    if isinstance(value, bool):
        return 1 if value else 0
    if value is None or isinstance(value, (int, float, str, bytes)):
        return value
    return str(value)


def superseded_ids(conn: sqlite3.Connection) -> dict[str, dict]:
    """Every superseded snapshot id, with the record that superseded it."""
    cur = conn.execute(
        "SELECT * FROM tally_supersession ORDER BY recorded_ts ASC LIMIT 5000"
    )
    out: dict[str, dict] = {}
    for raw in cur.fetchall():
        row = dict(raw)
        out[str(row.get("superseded_snapshot_id"))] = row
    return out


def records(
    conn: sqlite3.Connection, *, session: str | None = None, limit: int = 200,
) -> list[dict]:
    """The registry, newest first, optionally for one session."""
    bound = max(1, int(limit))
    if session:
        cur = conn.execute(
            "SELECT * FROM tally_supersession WHERE session = ? "
            "ORDER BY recorded_ts DESC LIMIT ?",
            (session, bound),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM tally_supersession ORDER BY recorded_ts DESC LIMIT ?",
            (bound,),
        )
    return [dict(r) for r in cur.fetchall()]


def counts(conn: sqlite3.Connection) -> dict:
    """How many corrections the registry holds and what they span."""
    row = conn.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT session) AS sessions, "
        "MIN(recorded_ts) AS first_ts, MAX(recorded_ts) AS last_ts "
        "FROM tally_supersession"
    ).fetchone()
    return {
        "rows": int(row["n"] or 0),
        "sessions": int(row["sessions"] or 0),
        "first_ts": row["first_ts"],
        "last_ts": row["last_ts"],
    }
