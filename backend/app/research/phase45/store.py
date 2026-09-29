"""Phase 45 — the journal, in its own database, written once per event.

Its own file. A phase that writes into the evidence another phase reads is one
bad migration away from destroying a capture that cannot be repeated, so the raw
Phase 35 store is opened read-only here and this journal lives beside it rather
than inside it.

Two tables, and the split between them is the safety property. ``shadow_event``
holds only what was knowable at the decision instant, and it is append-only:
``INSERT OR IGNORE`` on an ``event_id`` derived from provenance, so re-observing
or replaying the same instant converges on the row that is already there instead
of overwriting it with a later opinion. Phase 44 used ``INSERT OR REPLACE`` for
the same job; that is a mutable journal, and a mutable journal cannot answer
"what did this say at the time".

``paper_outcome`` holds what happened afterwards — entry, exit, MFE, MAE,
giveback, net. It is a separate table with its own writer, and nothing in
:mod:`app.research.phase45.evaluator` imports this module, so an outcome cannot
travel back into an admission. That is not a stylistic preference: an earlier
phase in this project did exactly that, and the resulting table looked like an
edge.
"""
from __future__ import annotations

import os
import sqlite3

from app.config import settings
from app.research import phase45

SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_event (
    event_id                TEXT PRIMARY KEY,
    obs_id                  TEXT NOT NULL,
    decision_ts             REAL NOT NULL,
    capture_ts              REAL,
    ingest_ts               REAL NOT NULL,
    session                 TEXT,
    instrument              TEXT NOT NULL,
    family                  TEXT,
    vehicle                 TEXT NOT NULL,
    vehicle_label           TEXT NOT NULL,
    contract                TEXT,
    strike                  REAL,
    expiry                  TEXT,
    dte                     INTEGER,
    underlying_price        REAL,
    moneyness               TEXT,
    direction               TEXT,
    read_direction          TEXT,
    shadow_action           TEXT NOT NULL,
    reason                  TEXT,
    evidence                TEXT NOT NULL,
    book_state              TEXT,
    bid                     REAL,
    ask                     REAL,
    bid_size                INTEGER,
    ask_size                INTEGER,
    last_traded_price       REAL,
    spread                  REAL,
    spread_pct              REAL,
    feed_age_ms             REAL,
    book_age_ms             REAL,
    quote_quality           TEXT,
    lot_size                INTEGER,
    lot_source              TEXT,
    entry_side              TEXT,
    entry_price             REAL,
    atr_points              REAL,
    trailing_range_points   REAL,
    range_source            TEXT,
    delta                   REAL,
    expected_move_points    REAL,
    modelled_cost_points    REAL,
    measured_cost_points    REAL,
    measured_charges_points REAL,
    measured_spread_points  REAL,
    cost_pct_of_entry       REAL,
    cost_evidence           TEXT,
    ratio_modelled          REAL,
    ratio_measured          REAL,
    gate_multiple           REAL,
    gate_result             INTEGER,
    engine_class            TEXT,
    engine_selected_vehicle TEXT,
    production_signal       TEXT,
    session_period          TEXT,
    regime                  TEXT,
    classification          TEXT NOT NULL,
    data_quality            TEXT,
    lifecycle               TEXT NOT NULL,
    definition              TEXT NOT NULL,
    version                 TEXT NOT NULL,
    source                  TEXT
);
CREATE INDEX IF NOT EXISTS idx_p45_session
    ON shadow_event (session, decision_ts);
CREATE INDEX IF NOT EXISTS idx_p45_board
    ON shadow_event (vehicle, shadow_action, decision_ts);
CREATE INDEX IF NOT EXISTS idx_p45_definition
    ON shadow_event (definition, decision_ts);
CREATE INDEX IF NOT EXISTS idx_p45_instrument
    ON shadow_event (instrument, decision_ts);

CREATE TABLE IF NOT EXISTS paper_outcome (
    event_id            TEXT PRIMARY KEY,
    resolved_ts         REAL NOT NULL,
    lifecycle           TEXT NOT NULL,
    outcome_status      TEXT NOT NULL,
    reason              TEXT,
    entry_side          TEXT,
    entry_price         REAL,
    entry_ts            REAL,
    exit_side           TEXT,
    exit_price          REAL,
    exit_ts             REAL,
    exit_trigger        TEXT,
    hold_minutes        REAL,
    samples             INTEGER,
    gross_pnl_points    REAL,
    gross_pct           REAL,
    cost_points         REAL,
    net_pnl_points      REAL,
    net_pct             REAL,
    mfe_pct             REAL,
    mae_pct             REAL,
    peak_pct            REAL,
    giveback_pct        REAL,
    t1_hit              INTEGER,
    t2_hit              INTEGER,
    t3_hit              INTEGER,
    definition          TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES shadow_event (event_id)
);
CREATE INDEX IF NOT EXISTS idx_p45_outcome_status
    ON paper_outcome (outcome_status, resolved_ts);
"""

# The column list is this module's own constant, not the caller's keys, so a
# caller cannot widen the statement and every insert is parameterised.
EVENT_COLUMNS: tuple[str, ...] = (
    "event_id", "obs_id", "decision_ts", "capture_ts", "ingest_ts", "session",
    "instrument", "family", "vehicle", "vehicle_label", "contract", "strike",
    "expiry", "dte", "underlying_price", "moneyness", "direction",
    "read_direction", "shadow_action", "reason", "evidence", "book_state",
    "bid", "ask", "bid_size", "ask_size", "last_traded_price", "spread",
    "spread_pct", "feed_age_ms", "book_age_ms", "quote_quality", "lot_size",
    "lot_source", "entry_side", "entry_price", "atr_points",
    "trailing_range_points", "range_source", "delta", "expected_move_points",
    "modelled_cost_points", "measured_cost_points", "measured_charges_points",
    "measured_spread_points", "cost_pct_of_entry", "cost_evidence",
    "ratio_modelled", "ratio_measured", "gate_multiple", "gate_result",
    "engine_class", "engine_selected_vehicle", "production_signal",
    "session_period", "regime", "classification", "data_quality", "lifecycle",
    "definition", "version", "source",
)

OUTCOME_COLUMNS: tuple[str, ...] = (
    "event_id", "resolved_ts", "lifecycle", "outcome_status", "reason",
    "entry_side", "entry_price", "entry_ts", "exit_side", "exit_price",
    "exit_ts", "exit_trigger", "hold_minutes", "samples", "gross_pnl_points",
    "gross_pct", "cost_points", "net_pnl_points", "net_pct", "mfe_pct",
    "mae_pct", "peak_pct", "giveback_pct", "t1_hit", "t2_hit", "t3_hit",
    "definition",
)

# Columns a filter may name. An allowlist rather than string interpolation of
# whatever the caller sent: the board is reachable from an HTTP endpoint.
FILTERS: frozenset[str] = frozenset({
    "session", "instrument", "vehicle", "direction", "shadow_action",
    "evidence", "definition", "family",
})


def root() -> str:
    """The backend directory, so the CLI works from any working directory."""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )))


def db_path() -> str:
    """The journal file, under the configured data directory.

    Resolved through ``settings.data_dir`` rather than a fixed path beside the
    package, so a session pointed at another data directory keeps its shadow
    journal there too instead of writing into the default one. A relative
    setting is anchored to the backend directory, so the CLI still works from
    any working directory.
    """
    configured = settings.data_dir
    base = (
        configured if os.path.isabs(configured)
        else os.path.join(root(), configured)
    )
    return os.path.join(base, phase45.ARTEFACT_DIR, phase45.DB_NAME)


def connect(path: str | None = None) -> sqlite3.Connection:
    """The journal, created on first use.

    ``check_same_thread`` is off because the callers are genuinely on different
    threads: the capture hook runs on the tick path while the board reads and
    the outcome resolver run in the event loop's executor. Every use is
    serialised behind the service lock. Leaving the check on left the
    connection unusable to whichever thread did not open it, which failed every
    live write and every board read rather than only a rare one.
    """
    target = path or db_path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    con = sqlite3.connect(target, timeout=30.0, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def open_read_only(path: str) -> sqlite3.Connection:
    """Another phase's database, opened so a write is impossible."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def insert_events(con: sqlite3.Connection, rows: list[dict]) -> dict:
    """Append rows that are not already journalled. Returns written/skipped.

    ``INSERT OR IGNORE``: an event already in the journal stays as it was
    written, so a duplicate observation or a replay of a stored session cannot
    restate history. The skipped count is returned rather than swallowed
    because it is the number the idempotency claim is checked against.
    """
    if not rows:
        return {"written": 0, "duplicate": 0}
    placeholders = ", ".join("?" for _ in EVENT_COLUMNS)
    sql = (f"INSERT OR IGNORE INTO shadow_event ({', '.join(EVENT_COLUMNS)})"
           f" VALUES ({placeholders})")
    before = con.total_changes
    con.executemany(sql, [tuple(_cell(r.get(c)) for c in EVENT_COLUMNS)
                          for r in rows])
    con.commit()
    written = con.total_changes - before
    return {"written": written, "duplicate": len(rows) - written}


def insert_outcome(con: sqlite3.Connection, row: dict) -> dict:
    """Append one resolution. Also append-only, for the same reason."""
    placeholders = ", ".join("?" for _ in OUTCOME_COLUMNS)
    sql = (f"INSERT OR IGNORE INTO paper_outcome"
           f" ({', '.join(OUTCOME_COLUMNS)}) VALUES ({placeholders})")
    before = con.total_changes
    con.execute(sql, tuple(_cell(row.get(c)) for c in OUTCOME_COLUMNS))
    con.commit()
    written = con.total_changes - before
    return {"written": written, "duplicate": 1 - written}


def _cell(value: object) -> object:
    """SQLite-storable form. ``bool`` becomes 0/1 rather than being rejected."""
    if isinstance(value, bool):
        return 1 if value else 0
    if value is None or isinstance(value, (int, float, str, bytes)):
        return value
    return str(value)


def _where(filters: dict, *, prefix: str = "") -> tuple[str, list]:
    """A parameterised WHERE from allowlisted columns only."""
    clauses: list[str] = []
    params: list = []
    for key in sorted(filters):
        if key not in FILTERS:
            continue
        value = filters[key]
        if value is None or value == "":
            continue
        clauses.append(f"{prefix}{key} = ?")
        params.append(value)
    since, until = filters.get("since"), filters.get("until")
    if isinstance(since, (int, float)):
        clauses.append(f"{prefix}decision_ts >= ?")
        params.append(float(since))
    if isinstance(until, (int, float)):
        clauses.append(f"{prefix}decision_ts <= ?")
        params.append(float(until))
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def events(
    con: sqlite3.Connection,
    *,
    filters: dict | None = None,
    limit: int = 200,
) -> list[dict]:
    """Journalled events, newest first, bounded."""
    where, params = _where(filters or {}, prefix="e.")
    rows = con.execute(
        "SELECT e.*, o.outcome_status, o.net_pct, o.mfe_pct, o.mae_pct,"
        " o.giveback_pct, o.exit_price, o.exit_side, o.hold_minutes,"
        " o.t1_hit, o.t2_hit, o.t3_hit"
        " FROM shadow_event e"
        " LEFT JOIN paper_outcome o ON o.event_id = e.event_id"
        + where +
        " ORDER BY e.decision_ts DESC, e.vehicle LIMIT ?",
        (*params, max(1, min(int(limit), 2000))),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_per_vehicle(
    con: sqlite3.Connection, *, filters: dict | None = None, limit: int = 60,
) -> list[dict]:
    """The most recent event per (instrument, vehicle, contract).

    What a live board shows: one current row per contract rather than every
    minute of it. Resolved outcomes are joined in so a row can carry the paper
    result of the same event once it exists.
    """
    where, params = _where(filters or {})
    rows = con.execute(
        "SELECT e.*, o.outcome_status, o.net_pct, o.mfe_pct, o.mae_pct,"
        " o.giveback_pct, o.exit_price, o.exit_side, o.hold_minutes"
        " FROM shadow_event e"
        " JOIN (SELECT instrument, vehicle, IFNULL(contract, '') AS c,"
        "       MAX(decision_ts) AS ts FROM shadow_event"
        + where +
        "       GROUP BY instrument, vehicle, c) latest"
        "   ON latest.instrument = e.instrument AND latest.vehicle = e.vehicle"
        "  AND latest.c = IFNULL(e.contract, '') AND latest.ts = e.decision_ts"
        " LEFT JOIN paper_outcome o ON o.event_id = e.event_id"
        " ORDER BY e.instrument, e.vehicle, e.decision_ts DESC LIMIT ?",
        (*params, max(1, min(int(limit), 500))),
    ).fetchall()
    return [dict(r) for r in rows]


def choices(con: sqlite3.Connection) -> dict:
    """The filter values actually present, so the board offers no empty option."""
    def col(name: str) -> list[str]:
        return [str(r[0]) for r in con.execute(
            f"SELECT DISTINCT {name} FROM shadow_event"
            f" WHERE {name} IS NOT NULL ORDER BY {name}"
        )]
    # Column names here are literals in this module, never caller input.
    return {
        "sessions": col("session"),
        "instruments": col("instrument"),
        "vehicles": col("vehicle"),
        "directions": col("direction"),
        "definitions": col("definition"),
        "actions": col("shadow_action"),
    }


def unresolved(
    con: sqlite3.Connection, *, actions: tuple[str, ...], limit: int = 500,
) -> list[dict]:
    """Signalled events with no outcome row yet, oldest first.

    Oldest first because the resolver needs the ones whose forward window has
    had time to happen; newest first would hand it the instants least likely to
    be resolvable.
    """
    marks = ", ".join("?" for _ in actions)
    rows = con.execute(
        "SELECT e.* FROM shadow_event e"
        " LEFT JOIN paper_outcome o ON o.event_id = e.event_id"
        f" WHERE o.event_id IS NULL AND e.shadow_action IN ({marks})"
        " ORDER BY e.decision_ts ASC LIMIT ?",
        (*actions, max(1, min(int(limit), 5000))),
    ).fetchall()
    return [dict(r) for r in rows]


def tally(con: sqlite3.Connection, *, filters: dict | None = None) -> dict:
    """The performance counts, computed in SQL rather than in Python."""
    where, params = _where(filters or {}, prefix="e.")
    row = con.execute(
        "SELECT COUNT(*) AS events,"
        " COUNT(DISTINCT e.session) AS sessions,"
        " SUM(CASE WHEN e.evidence = 'MEASURED' THEN 1 ELSE 0 END)"
        "   AS measurable,"
        " SUM(CASE WHEN e.shadow_action = ? THEN 1 ELSE 0 END) AS unmeasured,"
        " SUM(CASE WHEN e.shadow_action = ? THEN 1 ELSE 0 END) AS waits,"
        " SUM(CASE WHEN e.shadow_action IN (?, ?) THEN 1 ELSE 0 END)"
        "   AS signals,"
        " AVG(e.measured_cost_points) AS avg_measured_cost,"
        " AVG(e.modelled_cost_points) AS avg_modelled_cost,"
        " AVG(e.measured_spread_points) AS avg_spread,"
        " AVG(e.ratio_measured) AS avg_ratio_measured"
        " FROM shadow_event e" + where,
        (phase45.SHADOW_UNMEASURED, phase45.SHADOW_WAIT,
         phase45.SHADOW_BUY, phase45.SHADOW_SELL, *params),
    ).fetchone()
    out = {k: row[k] for k in row.keys()}
    out.update(_outcome_tally(con, where, params))
    out["reasons"] = _reasons(con, where, params)
    return out


def _outcome_tally(con: sqlite3.Connection, where: str, params: list) -> dict:
    row = con.execute(
        "SELECT COUNT(*) AS resolved,"
        " SUM(CASE WHEN o.net_pnl_points > 0 THEN 1 ELSE 0 END) AS wins,"
        " SUM(CASE WHEN o.net_pnl_points <= 0 THEN 1 ELSE 0 END) AS losses,"
        " SUM(o.gross_pnl_points) AS gross_points,"
        " SUM(o.cost_points) AS cost_points,"
        " SUM(o.net_pnl_points) AS net_points,"
        " AVG(o.net_pct) AS avg_net_pct,"
        " AVG(o.gross_pct) AS avg_gross_pct,"
        " AVG(o.mfe_pct) AS avg_mfe_pct,"
        " AVG(o.mae_pct) AS avg_mae_pct,"
        " AVG(o.giveback_pct) AS avg_giveback_pct,"
        " AVG(o.hold_minutes) AS avg_hold_minutes,"
        " SUM(CASE WHEN o.net_pnl_points > 0 THEN o.net_pnl_points ELSE 0 END)"
        "   AS gain_sum,"
        " SUM(CASE WHEN o.net_pnl_points < 0 THEN -o.net_pnl_points ELSE 0 END)"
        "   AS loss_sum,"
        " SUM(o.t1_hit) AS t1_hits, SUM(o.t2_hit) AS t2_hits,"
        " SUM(o.t3_hit) AS t3_hits"
        " FROM paper_outcome o JOIN shadow_event e ON e.event_id = o.event_id"
        + where,
        tuple(params),
    ).fetchone()
    out = {k: row[k] for k in row.keys()}
    pending = con.execute(
        "SELECT COUNT(*) AS n FROM shadow_event e"
        " LEFT JOIN paper_outcome o ON o.event_id = e.event_id"
        + (where + " AND " if where else " WHERE ")
        + "o.event_id IS NULL AND e.shadow_action IN (?, ?)",
        (*params, phase45.SHADOW_BUY, phase45.SHADOW_SELL),
    ).fetchone()
    out["unresolved"] = pending["n"]
    return out


def _reasons(con: sqlite3.Connection, where: str, params: list) -> list[dict]:
    """Refusal reasons by count, so a blank board explains itself."""
    rows = con.execute(
        "SELECT e.reason AS reason, e.shadow_action AS action,"
        " COUNT(*) AS n FROM shadow_event e"
        + (where + " AND " if where else " WHERE ")
        + "e.reason IS NOT NULL"
        " GROUP BY e.reason, e.shadow_action ORDER BY n DESC",
        tuple(params),
    ).fetchall()
    return [dict(r) for r in rows]
