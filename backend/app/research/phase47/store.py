"""Phase 47 — the session journal of the call board.

The calls themselves are already journalled: a Phase 47 call *is* a Phase 46
overlay row, which is itself derived from a Phase 45 shadow event. Re-recording
them here would create a third copy of the same instant, and three copies of a
fact disagree the moment one of them is written by a newer definition.

What this file keeps is the thing the live board cannot: **what the session
tally said, at the time it said it.** A screen that recomputes from the store on
every poll can silently change its own history when the store gains rows; a
recorded snapshot cannot. One row per session per arm per marked-through
instant, append-only, no UPDATE and no DELETE — so the record of a session that
read -0.4% at 14:00 survives the session ending at +0.2%.

The snapshot id is derived from the session, the definition, the arm and the
instant marked through, so re-running the recorder converges on the row already
written instead of appending a second version of the same moment.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time

from app.config import settings
from app.research.phase47 import ARTEFACT_DIR, DB_NAME

SCHEMA = """
CREATE TABLE IF NOT EXISTS session_mark (
    snapshot_id TEXT PRIMARY KEY,
    session TEXT NOT NULL,
    arm TEXT NOT NULL,
    definition TEXT NOT NULL,
    snapshot_ts REAL NOT NULL,
    marked_through_ts REAL,
    instrument TEXT,
    calls INTEGER NOT NULL,
    marked INTEGER NOT NULL,
    unmarkable INTEGER NOT NULL,
    stale_marks INTEGER,
    net_pct_total REAL,
    net_pct_mean REAL,
    winners INTEGER,
    losers INTEGER,
    flat INTEGER,
    win_rate_pct REAL,
    profit_factor REAL,
    best_pct REAL,
    worst_pct REAL,
    avg_cost_points REAL,
    drawdown_pct REAL,
    lifecycle TEXT,
    legs INTEGER,
    events INTEGER,
    grouping_rule TEXT,
    selection_bound INTEGER,
    legs_available INTEGER,
    count_is TEXT,
    status TEXT NOT NULL,
    order_path TEXT NOT NULL,
    version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mark_session
    ON session_mark(session DESC, arm);
CREATE INDEX IF NOT EXISTS ix_mark_ts ON session_mark(snapshot_ts DESC);
"""

COLUMNS: tuple[str, ...] = (
    "snapshot_id", "session", "arm", "definition", "snapshot_ts",
    "marked_through_ts", "instrument", "calls", "marked", "unmarkable",
    "stale_marks", "net_pct_total", "net_pct_mean", "winners", "losers",
    "flat", "win_rate_pct", "profit_factor", "best_pct", "worst_pct",
    "avg_cost_points", "drawdown_pct", "lifecycle", "legs", "events",
    "grouping_rule", "selection_bound", "legs_available", "count_is",
    "status", "order_path", "version",
)

# Columns added after rows already existed. Added, never redefined: an existing
# row keeps every value it was written with and simply reports NULL for a
# column that did not exist when it was recorded, which is the truth about it.
ADDED: tuple[tuple[str, str], ...] = (
    ("legs", "INTEGER"),
    ("events", "INTEGER"),
    ("grouping_rule", "TEXT"),
    ("selection_bound", "INTEGER"),
    ("legs_available", "INTEGER"),
    ("count_is", "TEXT"),
)


def snapshot_id(
    session: str,
    arm: str,
    definition: str,
    marked_through_ts: object,
    selection: str = "",
) -> str:
    """A deterministic id for one session-arm tally at one marked-through instant.

    ``selection`` identifies which call selection the tally was taken under.
    Two tallies of one session that disagree because the selection was
    corrected are two different facts, and an id that ignored the selection
    would make the second one a duplicate of the first — which is exactly how
    a tally recorded by a broken query survives its own fix.
    """
    through = ""
    if isinstance(marked_through_ts, (int, float)):
        through = str(int(marked_through_ts))
    raw = f"{session}|{arm}|{definition}|{through}"
    if selection:
        raw = f"{raw}|{selection}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def root() -> str:
    """The backend directory, so the CLI works from any working directory."""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )))


def db_path() -> str:
    """The call-board journal file, under the configured data directory."""
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
    _add_columns(conn)
    conn.commit()
    return conn


def _add_columns(conn: sqlite3.Connection) -> None:
    """Bring an older journal up to the current columns, additively.

    ``ALTER TABLE ... ADD COLUMN`` only. No row is rewritten and no value is
    back-filled: a tally recorded before events were counted has no event count,
    and inventing one now would be this phase fabricating history while claiming
    to preserve it.
    """
    present = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(session_mark)")
    }
    for name, kind in ADDED:
        if name not in present:
            # Identifier and type come from the fixed ADDED tuple above, never
            # from a caller.
            conn.execute(f"ALTER TABLE session_mark ADD COLUMN {name} {kind}")


def insert_snapshots(conn: sqlite3.Connection, rows: list[dict]) -> dict:
    """Append session tallies, ignoring ones already recorded.

    ``ON CONFLICT(snapshot_id) DO NOTHING`` rather than ``INSERT OR IGNORE``:
    the blanket form would also swallow a NOT NULL violation and count a
    dropped row as a duplicate, which is how a journal starts lying about how
    much it holds.
    """
    if not rows:
        return {"written": 0, "duplicate": 0}
    placeholders = ",".join("?" for _ in COLUMNS)
    sql = (
        f"INSERT INTO session_mark ({','.join(COLUMNS)}) "
        f"VALUES ({placeholders}) ON CONFLICT(snapshot_id) DO NOTHING"
    )
    now = time.time()
    written = 0
    for row in rows:
        values = [
            _cell(now if col == "snapshot_ts" and row.get(col) is None
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


def latest_per_session(
    conn: sqlite3.Connection, *, limit: int = 40,
) -> list[dict]:
    """The most recent tally recorded for each session and arm, newest first.

    The *recorded* tally, which is the point of the table: it is what the board
    showed, not what the board would show now if it recomputed.
    """
    # Reduced in Python rather than with a correlated subquery: the reduction is
    # "newest snapshot per (session, arm)", and getting that wrong in SQL fails
    # by returning a plausible row from the wrong moment, which is the one
    # failure mode this table exists to prevent.
    cur = conn.execute(
        "SELECT * FROM session_mark ORDER BY snapshot_ts DESC LIMIT 5000"
    )
    seen: dict[tuple[str, str], dict] = {}
    for raw in cur.fetchall():
        row = dict(raw)
        key = (str(row.get("session")), str(row.get("arm")))
        if key not in seen:
            seen[key] = row
    out = sorted(
        seen.values(),
        key=lambda r: (str(r.get("session")), str(r.get("arm"))),
        reverse=True,
    )
    return out[:max(1, int(limit))]


def snapshots(
    conn: sqlite3.Connection, *, session: str | None = None, limit: int = 200,
) -> list[dict]:
    """Recorded tallies, newest first, optionally for one session only."""
    if session:
        cur = conn.execute(
            "SELECT * FROM session_mark WHERE session = ? "
            "ORDER BY snapshot_ts DESC LIMIT ?",
            (session, max(1, int(limit))),
        )
    else:
        cur = conn.execute(
            "SELECT * FROM session_mark ORDER BY snapshot_ts DESC LIMIT ?",
            (max(1, int(limit)),),
        )
    return [dict(r) for r in cur.fetchall()]


def counts(conn: sqlite3.Connection) -> dict:
    """Row totals and the span the journal covers."""
    row = conn.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT session) AS sessions, "
        "MIN(snapshot_ts) AS first_ts, MAX(snapshot_ts) AS last_ts "
        "FROM session_mark"
    ).fetchone()
    return {
        "rows": int(row["n"] or 0),
        "sessions": int(row["sessions"] or 0),
        "first_ts": row["first_ts"],
        "last_ts": row["last_ts"],
    }
