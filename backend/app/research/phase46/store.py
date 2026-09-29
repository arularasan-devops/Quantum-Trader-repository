"""Phase 46 — the overlay journal.

Its own database file, beside Phase 45's rather than inside it. Two reasons,
both learned here: a research layer that writes into another phase's tables can
corrupt that phase's history by being wrong about its own schema, and a reader
three months from now must be able to delete this file without losing a single
row of evidence. The overlay is a view; the evidence it describes lives in
Phase 45 and the raw store.

Append-only and idempotent. ``overlay_id`` is derived from the Phase 45 event
and this phase's definition, so replaying a session, re-observing an instant or
restarting the worker converges on the row already written instead of appending
a second verdict about the same instant — which would double every count on the
board and quietly rewrite what the overlay said at the time.

There is no UPDATE and no DELETE in this module. A correction is a new
definition, and a new definition is a new namespace.
"""
from __future__ import annotations

import os
import sqlite3
import time

from app.config import settings
from app.research.phase46 import ARTEFACT_DIR, DB_NAME

SCHEMA = """
CREATE TABLE IF NOT EXISTS overlay_event (
    overlay_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    obs_id TEXT NOT NULL,
    decision_ts REAL NOT NULL,
    written_ts REAL NOT NULL,
    session TEXT,
    instrument TEXT NOT NULL,
    family TEXT,
    vehicle TEXT NOT NULL,
    vehicle_label TEXT,
    contract TEXT,
    strike REAL,
    expiry TEXT,
    dte INTEGER,
    direction TEXT,
    read_direction TEXT,
    production_signal TEXT,
    production_signal_normalised TEXT,
    engine_class TEXT,
    engine_selected_vehicle TEXT,
    overlay_state TEXT NOT NULL,
    overlay_reason TEXT,
    research_reason TEXT,
    shadow_action TEXT,
    evidence TEXT,
    book_state TEXT,
    bid REAL,
    ask REAL,
    bid_size INTEGER,
    ask_size INTEGER,
    premium REAL,
    spread REAL,
    spread_pct REAL,
    feed_age_ms REAL,
    book_age_ms REAL,
    quote_quality TEXT,
    data_quality TEXT,
    entry_side TEXT,
    entry_price REAL,
    expected_move_points REAL,
    modelled_cost_points REAL,
    measured_cost_points REAL,
    measured_spread_points REAL,
    cost_pct_of_entry REAL,
    cost_evidence TEXT,
    expected_move_over_modelled_cost REAL,
    expected_move_over_measured_cost REAL,
    gate_multiple REAL,
    gate_result INTEGER,
    research_candidate TEXT,
    shadow_definition TEXT,
    definition TEXT NOT NULL,
    classification TEXT NOT NULL,
    mode TEXT NOT NULL,
    production_effect TEXT NOT NULL,
    version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_overlay_ts ON overlay_event(decision_ts DESC);
CREATE INDEX IF NOT EXISTS ix_overlay_obs ON overlay_event(obs_id);
CREATE INDEX IF NOT EXISTS ix_overlay_event ON overlay_event(event_id);
CREATE INDEX IF NOT EXISTS ix_overlay_state
    ON overlay_event(overlay_state, decision_ts DESC);
CREATE INDEX IF NOT EXISTS ix_overlay_instrument
    ON overlay_event(instrument, vehicle, decision_ts DESC);
"""

COLUMNS: tuple[str, ...] = (
    "overlay_id", "event_id", "obs_id", "decision_ts", "written_ts", "session",
    "instrument", "family", "vehicle", "vehicle_label", "contract", "strike",
    "expiry", "dte", "direction", "read_direction", "production_signal",
    "production_signal_normalised", "engine_class", "engine_selected_vehicle",
    "overlay_state", "overlay_reason", "research_reason", "shadow_action",
    "evidence", "book_state", "bid", "ask", "bid_size", "ask_size", "premium",
    "spread", "spread_pct", "feed_age_ms", "book_age_ms", "quote_quality",
    "data_quality", "entry_side", "entry_price", "expected_move_points",
    "modelled_cost_points", "measured_cost_points", "measured_spread_points",
    "cost_pct_of_entry", "cost_evidence", "expected_move_over_modelled_cost",
    "expected_move_over_measured_cost", "gate_multiple", "gate_result",
    "research_candidate", "shadow_definition", "definition", "classification",
    "mode", "production_effect", "version",
)

_FILTERABLE: dict[str, str] = {
    "instrument": "instrument = ?",
    "vehicle": "vehicle = ?",
    "state": "overlay_state = ?",
    "session": "session = ?",
    "definition": "definition = ?",
    "production": "production_signal_normalised = ?",
}


def root() -> str:
    """The backend directory, so the CLI works from any working directory."""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )))


def db_path() -> str:
    """The overlay journal file, under the configured data directory.

    Resolved through ``settings.data_dir`` exactly as Phase 45 resolves its
    own, so a session pointed at another data directory keeps its overlay
    beside the evidence it describes rather than in the default one.
    """
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
    # ``check_same_thread`` is off for the reason Phase 45's is: the writer
    # runs on the shadow worker while the board reads run in the event loop's
    # executor, and every use is serialised behind the service lock.
    conn = sqlite3.connect(target, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def insert_events(conn: sqlite3.Connection, rows: list[dict]) -> dict:
    """Append overlay rows, ignoring ones already journalled.

    Ignoring a conflict rather than updating on one, deliberately: the first
    write for an instant is the one taken at that instant, and a later pass has
    by definition seen more of the world. Overwriting would let a replay
    rewrite history in a table whose only purpose is to say what was known
    then.

    ``ON CONFLICT(overlay_id) DO NOTHING`` and not ``INSERT OR IGNORE``: the
    blanket form also swallows a NOT NULL or type violation, so a row missing a
    required field would be silently dropped and counted as a duplicate. This
    form ignores the one collision that is expected and lets every other
    breakage surface as an error the caller's counters record.
    """
    if not rows:
        return {"written": 0, "duplicate": 0}
    placeholders = ",".join("?" for _ in COLUMNS)
    sql = (
        f"INSERT INTO overlay_event ({','.join(COLUMNS)}) "
        f"VALUES ({placeholders}) ON CONFLICT(overlay_id) DO NOTHING"
    )
    now = time.time()
    written = 0
    for row in rows:
        values = [
            _cell(now if col == "written_ts" and row.get(col) is None
                  else row.get(col))
            for col in COLUMNS
        ]
        written += conn.execute(sql, values).rowcount
    conn.commit()
    return {"written": written, "duplicate": len(rows) - written}


def _cell(value: object) -> object:
    """SQLite-storable form of a value, with booleans kept as 0/1 integers."""
    if isinstance(value, bool):
        return 1 if value else 0
    if value is None or isinstance(value, (int, float, str, bytes)):
        return value
    return str(value)


def _where(filters: dict | None) -> tuple[str, list[object]]:
    """The WHERE clause for a bounded, named set of filters only.

    Column names are never taken from the caller: an unknown key is dropped
    rather than interpolated, and every value is bound as a parameter.
    """
    if not filters:
        return "", []
    clauses: list[str] = []
    params: list[object] = []
    for key, clause in _FILTERABLE.items():
        value = filters.get(key)
        if value is None or value == "":
            continue
        clauses.append(clause)
        params.append(value)
    since = filters.get("since")
    if isinstance(since, (int, float)) and since > 0:
        clauses.append("decision_ts >= ?")
        params.append(float(since))
    until = filters.get("until")
    if isinstance(until, (int, float)) and until > 0:
        clauses.append("decision_ts <= ?")
        params.append(float(until))
    if not clauses:
        return "", []
    return " WHERE " + " AND ".join(clauses), params


def events(
    conn: sqlite3.Connection,
    *,
    filters: dict | None = None,
    limit: int = 240,
) -> list[dict]:
    """Overlay rows, newest decision instant first."""
    clause, params = _where(filters)
    sql = (
        f"SELECT * FROM overlay_event{clause} "
        f"ORDER BY decision_ts DESC, instrument, vehicle LIMIT ?"
    )
    cur = conn.execute(sql, [*params, max(1, int(limit))])
    return [dict(r) for r in cur.fetchall()]


def events_for_observation(conn: sqlite3.Connection, obs_id: str) -> list[dict]:
    """Every vehicle row for one decision instant, in vehicle order."""
    cur = conn.execute(
        "SELECT * FROM overlay_event WHERE obs_id = ? ORDER BY vehicle",
        (obs_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def state_counts(conn: sqlite3.Connection, *, filters: dict | None = None) -> dict:
    """How many rows sit in each overlay state.

    A row count, and labelled as one wherever it is shown: the number of
    SUPPORTED rows is a property of the feed and the sample, not a result.
    """
    clause, params = _where(filters)
    cur = conn.execute(
        f"SELECT overlay_state, COUNT(*) AS n FROM overlay_event{clause} "
        f"GROUP BY overlay_state ORDER BY n DESC",
        params,
    )
    return {str(r["overlay_state"]): int(r["n"]) for r in cur.fetchall()}


def options(conn: sqlite3.Connection) -> dict:
    """The distinct filter values actually present, for the board's controls."""
    out: dict[str, list[str]] = {}
    for key, column in (
        ("instruments", "instrument"), ("vehicles", "vehicle"),
        ("states", "overlay_state"), ("sessions", "session"),
        ("definitions", "definition"),
    ):
        cur = conn.execute(
            f"SELECT DISTINCT {column} AS v FROM overlay_event "
            f"WHERE {column} IS NOT NULL ORDER BY v"
        )
        out[key] = [str(r["v"]) for r in cur.fetchall()]
    return out


def counts(conn: sqlite3.Connection) -> dict:
    """Row totals, and the span the journal covers."""
    cur = conn.execute(
        "SELECT COUNT(*) AS n, MIN(decision_ts) AS first_ts, "
        "MAX(decision_ts) AS last_ts, COUNT(DISTINCT obs_id) AS instants, "
        "COUNT(DISTINCT session) AS sessions FROM overlay_event"
    )
    row = cur.fetchone()
    return {
        "rows": int(row["n"] or 0),
        "instants": int(row["instants"] or 0),
        "sessions": int(row["sessions"] or 0),
        "first_ts": row["first_ts"],
        "last_ts": row["last_ts"],
    }
