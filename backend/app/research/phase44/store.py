"""Phase 44 §3 — the journal, in its own database.

Its own file, not the raw store and not ``opportunity.db``: a phase that writes
into the evidence another phase reads is one bad migration away from corrupting
a five-week capture, and Phase 44 has no reason to touch either. Reads of the
raw store are opened read-only through :func:`open_raw` for the same reason.

Two tables. ``switch_event`` is append-only — arming and disarming are recorded,
never overwritten, so "when was this on, and under which definition" is
answerable afterwards rather than inferred. ``shadow_row`` is one row per
decision instant, keyed on the observation id so re-recording a session is
idempotent and cannot silently double-count a day.
"""
from __future__ import annotations

import os
import sqlite3

from app.research import phase44

SCHEMA = """
CREATE TABLE IF NOT EXISTS switch_event (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    state       TEXT NOT NULL,          -- ARMED / DORMANT
    definition  TEXT NOT NULL,          -- the fingerprint it was armed under
    operator    TEXT,
    note        TEXT
);
CREATE TABLE IF NOT EXISTS shadow_row (
    obs_id                   TEXT PRIMARY KEY,
    session                  TEXT NOT NULL,
    ts                       REAL NOT NULL,
    instrument               TEXT NOT NULL,
    vehicle                  TEXT NOT NULL,
    source                   TEXT,       -- ENGINE / BOARD
    direction                TEXT,
    contract                 TEXT,
    expiry                   TEXT,
    lot_size                 INTEGER,
    bid                      REAL,
    ask                      REAL,
    base_admits              INTEGER,
    expected_move_points     REAL,
    decision_close           REAL,
    modelled_cost_points     REAL,
    measured_cost_points     REAL,
    measured_charges_points  REAL,
    measured_spread_points   REAL,
    ratio_modelled           REAL,
    ratio_measured           REAL,
    admits_modelled          INTEGER,
    admits                   INTEGER,
    evidence                 TEXT NOT NULL,
    reason                   TEXT,
    definition               TEXT NOT NULL,
    recorded_ts              REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shadow_session ON shadow_row (session, ts);
CREATE INDEX IF NOT EXISTS idx_shadow_admits ON shadow_row (admits, ts);
"""

COLUMNS: tuple[str, ...] = (
    "obs_id", "session", "ts", "instrument", "vehicle", "source", "direction",
    "contract", "expiry", "lot_size", "bid", "ask", "base_admits",
    "expected_move_points", "decision_close", "modelled_cost_points",
    "measured_cost_points", "measured_charges_points", "measured_spread_points",
    "ratio_modelled", "ratio_measured", "admits_modelled", "admits",
    "evidence", "reason", "definition", "recorded_ts",
)


def root() -> str:
    """The backend directory, so the CLI works from any working directory."""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )))


def db_path() -> str:
    return os.path.join(root(), phase44.ARTEFACT_DIR, phase44.DB_NAME)


def connect(path: str | None = None) -> sqlite3.Connection:
    """The journal, created on first use."""
    target = path or db_path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    con = sqlite3.connect(target)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def open_raw(path: str) -> sqlite3.Connection:
    """The Phase 35 raw store, opened read-only.

    ``mode=ro`` rather than discipline: this phase reads the evidence of a
    capture that cannot be repeated, and a read-only handle makes an accidental
    write impossible rather than unlikely.
    """
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def insert(con: sqlite3.Connection, rows: list[dict]) -> int:
    """Write journal rows, replacing any earlier row for the same decision.

    Parameterised throughout, and the column list is the module's own constant
    rather than the caller's keys, so a caller cannot widen the statement.
    """
    if not rows:
        return 0
    placeholders = ", ".join("?" for _ in COLUMNS)
    sql = (f"INSERT OR REPLACE INTO shadow_row ({', '.join(COLUMNS)})"
           f" VALUES ({placeholders})")
    con.executemany(sql, [tuple(r.get(c) for c in COLUMNS) for r in rows])
    con.commit()
    return len(rows)


def sessions(con: sqlite3.Connection) -> list[str]:
    return [str(r["session"]) for r in con.execute(
        "SELECT DISTINCT session FROM shadow_row ORDER BY session")]


def tally(con: sqlite3.Connection) -> dict:
    """Counts the report needs, computed in SQL rather than in Python."""
    row = con.execute(
        "SELECT COUNT(*) AS rows_total,"
        " COUNT(DISTINCT session) AS sessions,"
        " SUM(CASE WHEN evidence IN (?, ?) THEN 1 ELSE 0 END) AS measurable,"
        " SUM(CASE WHEN evidence = ? THEN 1 ELSE 0 END)"
        " AS measured_captured_lot,"
        " SUM(CASE WHEN evidence = ? THEN 1 ELSE 0 END) AS measured_spec_lot,"
        " SUM(CASE WHEN admits = 1 THEN 1 ELSE 0 END) AS admitted,"
        " SUM(CASE WHEN admits_modelled = 1 THEN 1 ELSE 0 END) AS admitted_modelled,"
        " AVG(measured_cost_points) AS avg_measured_cost,"
        " AVG(modelled_cost_points) AS avg_modelled_cost,"
        " AVG(measured_spread_points) AS avg_spread"
        " FROM shadow_row",
        (phase44.MEASURED, phase44.SPEC_LOT, phase44.MEASURED,
         phase44.SPEC_LOT),
    ).fetchone()
    return {k: row[k] for k in row.keys()}
