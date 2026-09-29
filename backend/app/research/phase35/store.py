"""Phase 35 §21 — the raw observation store, which is never overwritten.

Two classes of table live in one SQLite file:

**raw** (:data:`RAW_TABLES`) — one row per observed thing, written once. There is
no update and no delete API in this module for them, and :func:`connect`
installs triggers that abort an UPDATE or DELETE against them even if some later
caller tries to issue one by hand. That is stronger than a convention: the
earlier phases lost the ability to reproduce a report because a "fix-up" pass
rewrote rows it had already published, and a comment would not have stopped it.

**derived** (:data:`DERIVED_TABLES`) — paths, attributions, comparisons and
report rows. These are functions of raw rows and are rebuilt, so they may be
cleared and re-inserted. A derived table can therefore never be the only place
a measurement exists.

Identity is content-addressed rather than autoincrement: an observation's id is
a fingerprint of the fields that make it what it is (§3), so re-running the
capture over the same source rows converges instead of duplicating. Every raw
insert is ``INSERT OR IGNORE``, and the insert helpers return how many rows were
actually written (SQLite ``total_changes``) rather than how many were offered —
the same accounting bug Phase 34 had to fix, where a duplicate looked like a
successful capture.

All SQL is parameterised. Table names are never interpolated from caller input:
the only names that reach a statement come from the module-level tuples below.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator

from app.research.phase35 import DB_NAME

RAW_TABLES: tuple[str, ...] = ("raw_observation", "raw_quote", "raw_path_sample")
DERIVED_TABLES: tuple[str, ...] = (
    "paper_leg", "leg_path", "leg_attribution", "vehicle_comparison",
)

# Neither raw nor derived: bookkeeping about how far the capture files have been
# read. It holds no measurement, so it is excluded from :func:`counts` and from
# :func:`clear_derived`, and losing it costs a re-read rather than any evidence.
CURSOR_TABLE = "capture_cursor"

# The same kind of bookkeeping for the derived rebuild: which sessions have been
# rebuilt, and how many raw observations they held when that happened. Losing it
# costs a re-derivation, never a measurement.
DERIVED_CHECKPOINT_TABLE = "derived_session"

# How long a statement waits for another process's lock before it gives up. Long,
# deliberately: the competing writer is a batched flush, and an ingest that waits
# a minute finishes, while one that raises leaves captured minutes unignested.
BUSY_TIMEOUT_SEC = 60.0

# A session's checkpoint records which stage finished, not merely that something
# ran: the paper books are built for every session before the comparison stage
# starts, so a pass interrupted in the second stage must skip the first for that
# session and redo only the second.
# ``PARTIAL`` carries a watermark in its payload: the first timestamp in the
# session that is NOT yet built. It exists because a session of a real store is
# ~208k observations and hours of work, so a checkpoint only at the session
# boundary would still leave hours between save points.
STAGE_PAPER_PARTIAL = "PAPER_BOOKS_PARTIAL"
STAGE_PAPER_DONE = "PAPER_BOOKS_DONE"
STAGE_VEHICLE_DONE = "VEHICLE_COMPARISON_DONE"

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_observation (
    obs_id            TEXT PRIMARY KEY,
    ts                REAL NOT NULL,
    session           TEXT,
    instrument        TEXT NOT NULL,
    family            TEXT,
    source            TEXT NOT NULL,      -- ENGINE / BOARD
    opportunity_type  TEXT NOT NULL,
    direction         TEXT,
    engine_class      TEXT,               -- BUY / WAIT / AVOID / NO_SIGNAL
    engine_selected   INTEGER NOT NULL,   -- 1 when the engine chose to act
    selected_vehicle  TEXT,
    context_json      TEXT NOT NULL,      -- market/board state as captured
    origin            TEXT NOT NULL,      -- which upstream store the row came from
    origin_id         TEXT,
    ingest_ts         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_ts ON raw_observation (ts);
CREATE INDEX IF NOT EXISTS idx_obs_instrument ON raw_observation (instrument, ts);
CREATE INDEX IF NOT EXISTS idx_obs_source ON raw_observation (source, ts);

CREATE TABLE IF NOT EXISTS raw_quote (
    obs_id       TEXT NOT NULL,
    vehicle      TEXT NOT NULL,          -- CE / PE / FUTURES
    ts           REAL NOT NULL,
    symbol       TEXT,
    strike       REAL,
    expiry       TEXT,
    dte          INTEGER,
    bid          REAL,
    ask          REAL,
    traded       REAL,                   -- LTP, never used as an executable price
    spread       REAL,
    spread_pct   REAL,
    delta        REAL,
    iv           REAL,
    oi           REAL,
    volume       REAL,
    underlying   REAL,
    basis        REAL,
    lot_size     INTEGER,
    evidence     TEXT NOT NULL,
    reason       TEXT,                   -- why UNMEASURED, when it is
    PRIMARY KEY (obs_id, vehicle)
);
-- The forward path of a leg is read as "this contract, later in the session", so
-- without this index every leg costs a full scan of raw_quote and a rebuild is
-- quadratic in the size of the store.
CREATE INDEX IF NOT EXISTS idx_quote_path ON raw_quote (symbol, vehicle, ts);

CREATE TABLE IF NOT EXISTS raw_path_sample (
    obs_id     TEXT NOT NULL,
    vehicle    TEXT NOT NULL,
    ts         REAL NOT NULL,
    bid        REAL,
    ask        REAL,
    traded     REAL,
    evidence   TEXT NOT NULL,
    PRIMARY KEY (obs_id, vehicle, ts)
);

CREATE TABLE IF NOT EXISTS paper_leg (
    leg_id        TEXT PRIMARY KEY,
    obs_id        TEXT NOT NULL,
    book          TEXT NOT NULL,
    vehicle       TEXT NOT NULL,
    instrument    TEXT NOT NULL,
    direction     TEXT NOT NULL,
    entry_ts      REAL NOT NULL,
    entry_price   REAL,
    entry_side    TEXT,                  -- ASK / BID / TRADED
    lot_size      INTEGER,
    lot_source    TEXT,                  -- QUOTE / PLAN_CONTEXT / CONTRACT_SPEC
    cost_points   REAL,
    cost_evidence TEXT NOT NULL,
    exit_ts       REAL,
    exit_price    REAL,
    exit_side     TEXT,
    exit_reason   TEXT,
    gross_pct     REAL,
    net_pct       REAL,
    resolved      INTEGER NOT NULL,
    evidence      TEXT NOT NULL,
    reason        TEXT
);
CREATE INDEX IF NOT EXISTS idx_leg_book ON paper_leg (book, entry_ts);
-- A resumable rebuild drops and redoes one session at a time, which is a range
-- scan on entry_ts alone; the composite index above cannot serve it.
CREATE INDEX IF NOT EXISTS idx_leg_entry ON paper_leg (entry_ts);
CREATE INDEX IF NOT EXISTS idx_leg_obs ON paper_leg (obs_id);

CREATE TABLE IF NOT EXISTS leg_path (
    leg_id     TEXT NOT NULL,
    horizon    TEXT NOT NULL,            -- "15" or "SESSION_CLOSE"
    gross_pct  REAL,
    net_pct    REAL,
    mfe_pct    REAL,
    mae_pct    REAL,
    t1         INTEGER,
    t2         INTEGER,
    t3         INTEGER,
    giveback   REAL,
    evidence   TEXT NOT NULL,
    PRIMARY KEY (leg_id, horizon)
);

CREATE TABLE IF NOT EXISTS leg_attribution (
    leg_id           TEXT PRIMARY KEY,
    primary_cause    TEXT NOT NULL,
    channel          TEXT,               -- NULL when the leg never resolved
    gross_pct        REAL,
    spread_pct       REAL,
    brokerage_pct    REAL,
    statutory_pct    REAL,
    slippage_pct     REAL,
    net_pct          REAL,
    peak_pct         REAL,
    time_to_peak_min REAL,
    giveback_pct     REAL,
    evidence         TEXT NOT NULL,
    note             TEXT,
    -- How precisely ``time_to_peak_min`` is known. NULL means the peak's own
    -- timestamp was compared with the entry's. A value names the coarser basis
    -- used instead, e.g. a peak recovered from stored horizon buckets after the
    -- fact, which is an upper bound rather than the instant.
    time_resolution  TEXT
);

CREATE TABLE IF NOT EXISTS vehicle_comparison (
    obs_id       TEXT NOT NULL,
    horizon      TEXT NOT NULL,
    kind         TEXT NOT NULL,          -- CE_VS_PE / FUTURES_VS_OPTIONS
    winner       TEXT,
    payload_json TEXT NOT NULL,
    evidence     TEXT NOT NULL,
    PRIMARY KEY (obs_id, horizon, kind)
);

CREATE TABLE IF NOT EXISTS derived_session (
    session       TEXT PRIMARY KEY,
    stage         TEXT NOT NULL,
    observations  INTEGER NOT NULL,   -- raw rows in the session when it was built
    payload_json  TEXT NOT NULL,      -- the per-session build result, for totals
    updated_ts    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS capture_cursor (
    path       TEXT PRIMARY KEY,
    size       INTEGER NOT NULL,   -- file size when the offset was recorded
    mtime      REAL NOT NULL,
    offset     INTEGER NOT NULL,   -- bytes of complete lines already ingested
    rows_seen  INTEGER NOT NULL,
    updated_ts REAL NOT NULL,
    -- Read to its end. For a plain file this is implied by the offset reaching
    -- the size, but a gzipped roll's offset counts decompressed bytes while its
    -- size is compressed, so the two cannot be compared and completion has to
    -- be recorded rather than inferred. Honoured only while size and mtime
    -- still match, so a file that changed is re-read from the first byte.
    complete   INTEGER NOT NULL DEFAULT 0
);
"""

# Raw immutability, enforced by the database rather than by discipline.
_GUARDS = "".join(
    f"""
CREATE TRIGGER IF NOT EXISTS no_update_{t} BEFORE UPDATE ON {t}
BEGIN SELECT RAISE(ABORT, 'phase35 raw rows are append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_{t} BEFORE DELETE ON {t}
BEGIN SELECT RAISE(ABORT, 'phase35 raw rows are append-only'); END;
"""
    for t in RAW_TABLES
)


def _backend_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))


def db_path() -> str:
    return os.path.join(_backend_root(), "data", DB_NAME)


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open the store, creating the schema and the append-only guards.

    Opened for concurrent use, because there genuinely are two processes: the
    running app reads the derived tables to serve its panels while an ingest
    writes them. On the default rollback journal a reader blocks the writer's
    commit outright, which is how an ingest died mid-flush with ``database is
    locked`` and left 253 captured minutes unignested. WAL lets the readers
    carry on across a write, and the busy timeout makes a genuine
    writer-vs-writer collision wait its turn instead of raising — a research
    rebuild is idempotent and resumable, so waiting is always right and failing
    never is.
    """
    target = path or db_path()
    if target != ":memory:":
        os.makedirs(os.path.dirname(target), exist_ok=True)
    con = sqlite3.connect(target, timeout=BUSY_TIMEOUT_SEC)
    con.row_factory = sqlite3.Row
    if target != ":memory:":
        # WAL is a property of the file, so this is set once and inherited by
        # every later connection, including ones opened by an older build.
        con.execute("PRAGMA journal_mode=WAL")
    con.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_SEC * 1000)}")
    con.executescript(SCHEMA)
    _migrate_nullable_channel(con)
    _migrate_leg_lot_source(con)
    _migrate_attrib_time_resolution(con)
    _migrate_cursor_complete(con)
    con.executescript(_GUARDS)
    con.commit()
    return con


def _migrate_nullable_channel(con: sqlite3.Connection) -> None:
    """An entered-but-unresolved leg has no channel, so the column must allow NULL.

    Earlier stores declared it NOT NULL, which forced a channel to be invented for
    a leg whose result is not known yet. The table is derived and rebuilt from raw
    on every pass, so dropping it costs nothing and loses nothing.
    """
    cols = con.execute("PRAGMA table_info(leg_attribution)").fetchall()
    if any(c["name"] == "channel" and int(c["notnull"]) for c in cols):
        con.execute("DROP TABLE leg_attribution")
        con.executescript(SCHEMA)


def _migrate_leg_lot_source(con: sqlite3.Connection) -> None:
    """Add ``paper_leg.lot_source`` to a store written before lot provenance.

    Added rather than rebuilt: the column is NULL on existing rows, which is the
    truth about them — they were built when the lot size had one source and no
    record of it. A rebuild fills it; nothing here invents it.
    """
    cols = {c["name"] for c in con.execute("PRAGMA table_info(paper_leg)")}
    if "lot_source" not in cols:
        con.execute("ALTER TABLE paper_leg ADD COLUMN lot_source TEXT")


def _migrate_attrib_time_resolution(con: sqlite3.Connection) -> None:
    """Add ``leg_attribution.time_resolution`` to a store written without it.

    NULL on existing rows, which is the truth about them: they were written by a
    pass that timed the peak from the sample that made it.
    """
    cols = {c["name"] for c in con.execute("PRAGMA table_info(leg_attribution)")}
    if "time_resolution" not in cols:
        con.execute("ALTER TABLE leg_attribution ADD COLUMN time_resolution TEXT")


def _migrate_cursor_complete(con: sqlite3.Connection) -> None:
    """Add ``capture_cursor.complete`` to a store written before compressed rolls.

    Defaults to 0, which is the safe direction: an existing cursor is treated as
    unfinished, so the next pass re-reads from its offset rather than declaring a
    file done on evidence that was never recorded. Re-reading writes nothing new.
    """
    cols = {c["name"] for c in con.execute(f"PRAGMA table_info({CURSOR_TABLE})")}
    if "complete" not in cols:
        con.execute(
            f"ALTER TABLE {CURSOR_TABLE} "
            "ADD COLUMN complete INTEGER NOT NULL DEFAULT 0"
        )


def capture_cursors(con: sqlite3.Connection) -> dict[str, dict]:
    """How far each capture file has been read, keyed by path."""
    return {
        str(r["path"]): dict(r)
        for r in con.execute("SELECT * FROM capture_cursor")
    }


def save_capture_cursor(
    con: sqlite3.Connection,
    path: str,
    *,
    size: int,
    mtime: float,
    offset: int,
    rows_seen: int,
    now: float,
    complete: bool = False,
) -> None:
    """Record that ``offset`` bytes of ``path`` are ingested.

    Written *after* the rows themselves, so an interruption between the two costs
    a re-read of the last batch and never a skip. Re-reading is free of
    consequence because raw ids are content-addressed.

    ``complete`` says the reader reached end of file, which is the only way a
    compressed roll can be known to be finished.
    """
    con.execute(
        "INSERT INTO capture_cursor "
        "(path, size, mtime, offset, rows_seen, updated_ts, complete) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET "
        "size = excluded.size, mtime = excluded.mtime, "
        "offset = excluded.offset, rows_seen = excluded.rows_seen, "
        "updated_ts = excluded.updated_ts, complete = excluded.complete",
        (path, int(size), float(mtime), int(offset), int(rows_seen), float(now),
         1 if complete else 0),
    )
    con.commit()


def clear_capture_cursors(con: sqlite3.Connection) -> int:
    """Forget every read position, so the next pass rescans from the first byte."""
    before = con.total_changes
    con.execute("DELETE FROM capture_cursor")
    con.commit()
    return int(con.total_changes - before)


def session_progress(con: sqlite3.Connection) -> dict[str, dict]:
    """Which sessions are already derived, keyed by session date.

    ``payload`` is the per-session build result, kept so that a resumed pass can
    report totals over the whole store rather than over only the sessions this
    pass happened to touch. A resumed run that under-reported its own totals
    would look like evidence had been lost.
    """
    out: dict[str, dict] = {}
    for r in con.execute(f"SELECT * FROM {DERIVED_CHECKPOINT_TABLE}"):  # noqa: S608
        row = dict(r)
        try:
            row["payload"] = json.loads(row.pop("payload_json") or "{}")
        except ValueError:
            row["payload"] = {}
        out[str(row["session"])] = row
    return out


def save_session_progress(
    con: sqlite3.Connection,
    session: str,
    *,
    stage: str,
    observations: int,
    payload: dict,
    now: float,
) -> None:
    """Record that ``session`` is derived up to ``stage``.

    Written after the rows themselves and committed, so an interruption between
    the two costs a re-derivation of that one session and never a skipped one.
    ``observations`` is stored because raw is append-only but not append-once per
    session: a later capture pass can add rows to a session already rebuilt, and
    a checkpoint that ignored the count would silently leave those rows out of
    the derived tables.
    """
    con.execute(
        f"INSERT INTO {DERIVED_CHECKPOINT_TABLE} "  # noqa: S608 - literal name
        "(session, stage, observations, payload_json, updated_ts) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(session) DO UPDATE SET stage = excluded.stage, "
        "observations = excluded.observations, "
        "payload_json = excluded.payload_json, updated_ts = excluded.updated_ts",
        (
            str(session), str(stage), int(observations),
            json.dumps(payload or {}, default=str, sort_keys=True), float(now),
        ),
    )
    con.commit()


def clear_session_progress(
    con: sqlite3.Connection, session: str | None = None,
) -> int:
    """Forget one session's derived checkpoint, or every one of them."""
    before = con.total_changes
    if session is None:
        con.execute(f"DELETE FROM {DERIVED_CHECKPOINT_TABLE}")  # noqa: S608
    else:
        con.execute(
            f"DELETE FROM {DERIVED_CHECKPOINT_TABLE} WHERE session = ?",  # noqa: S608
            (str(session),),
        )
    con.commit()
    return int(con.total_changes - before)


def _inserted(con: sqlite3.Connection, sql: str, rows: list[tuple]) -> int:
    if not rows:
        return 0
    before = con.total_changes
    con.executemany(sql, rows)
    con.commit()
    return int(con.total_changes - before)


def save_observations(con: sqlite3.Connection, rows: list[dict]) -> int:
    """Append observations. Re-offering an identical row is a no-op, not a dupe."""
    payload = [
        (
            r["obs_id"], float(r["ts"]), r.get("session"), r["instrument"],
            r.get("family"), r["source"], r["opportunity_type"], r.get("direction"),
            r.get("engine_class"), 1 if r.get("engine_selected") else 0,
            r.get("selected_vehicle"),
            json.dumps(r.get("context") or {}, default=str, sort_keys=True),
            r["origin"], r.get("origin_id"), float(r["ingest_ts"]),
        )
        for r in rows
    ]
    return _inserted(
        con,
        "INSERT OR IGNORE INTO raw_observation (obs_id, ts, session, instrument, "
        "family, source, opportunity_type, direction, engine_class, "
        "engine_selected, selected_vehicle, context_json, origin, origin_id, "
        "ingest_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        payload,
    )


_QUOTE_FIELDS = (
    "obs_id", "vehicle", "ts", "symbol", "strike", "expiry", "dte", "bid", "ask",
    "traded", "spread", "spread_pct", "delta", "iv", "oi", "volume", "underlying",
    "basis", "lot_size", "evidence", "reason",
)


def save_quotes(con: sqlite3.Connection, rows: list[dict]) -> int:
    payload = [tuple(r.get(f) for f in _QUOTE_FIELDS) for r in rows]
    cols = ", ".join(_QUOTE_FIELDS)
    marks = ", ".join("?" * len(_QUOTE_FIELDS))
    return _inserted(
        con, f"INSERT OR IGNORE INTO raw_quote ({cols}) VALUES ({marks})", payload,
    )


def save_path_samples(con: sqlite3.Connection, rows: list[dict]) -> int:
    payload = [
        (r["obs_id"], r["vehicle"], float(r["ts"]), r.get("bid"), r.get("ask"),
         r.get("traded"), r["evidence"])
        for r in rows
    ]
    return _inserted(
        con,
        "INSERT OR IGNORE INTO raw_path_sample (obs_id, vehicle, ts, bid, ask, "
        "traded, evidence) VALUES (?,?,?,?,?,?,?)",
        payload,
    )


_LEG_FIELDS = (
    "leg_id", "obs_id", "book", "vehicle", "instrument", "direction", "entry_ts",
    "entry_price", "entry_side", "lot_size", "lot_source", "cost_points",
    "cost_evidence",
    "exit_ts", "exit_price", "exit_side", "exit_reason", "gross_pct", "net_pct",
    "resolved", "evidence", "reason",
)


def save_legs(con: sqlite3.Connection, rows: list[dict]) -> int:
    """Write paper legs. Derived, so a re-run replaces a leg of the same id."""
    payload = [
        tuple(
            (1 if r.get(f) else 0) if f == "resolved" else r.get(f)
            for f in _LEG_FIELDS
        )
        for r in rows
    ]
    cols = ", ".join(_LEG_FIELDS)
    marks = ", ".join("?" * len(_LEG_FIELDS))
    return _inserted(
        con, f"INSERT OR REPLACE INTO paper_leg ({cols}) VALUES ({marks})", payload,
    )


_PATH_FIELDS = (
    "leg_id", "horizon", "gross_pct", "net_pct", "mfe_pct", "mae_pct", "t1", "t2",
    "t3", "giveback", "evidence",
)


def save_leg_paths(con: sqlite3.Connection, rows: list[dict]) -> int:
    payload = [
        tuple(
            (None if r.get(f) is None else int(bool(r.get(f))))
            if f in ("t1", "t2", "t3") else r.get(f)
            for f in _PATH_FIELDS
        )
        for r in rows
    ]
    cols = ", ".join(_PATH_FIELDS)
    marks = ", ".join("?" * len(_PATH_FIELDS))
    return _inserted(
        con, f"INSERT OR REPLACE INTO leg_path ({cols}) VALUES ({marks})", payload,
    )


_ATTRIB_FIELDS = (
    "leg_id", "primary_cause", "channel", "gross_pct", "spread_pct",
    "brokerage_pct", "statutory_pct", "slippage_pct", "net_pct", "peak_pct",
    "time_to_peak_min", "giveback_pct", "evidence", "note",
    "time_resolution",
)


def save_attributions(con: sqlite3.Connection, rows: list[dict]) -> int:
    payload = [tuple(r.get(f) for f in _ATTRIB_FIELDS) for r in rows]
    cols = ", ".join(_ATTRIB_FIELDS)
    marks = ", ".join("?" * len(_ATTRIB_FIELDS))
    return _inserted(
        con,
        f"INSERT OR REPLACE INTO leg_attribution ({cols}) VALUES ({marks})",
        payload,
    )


def save_comparisons(con: sqlite3.Connection, rows: list[dict]) -> int:
    payload = [
        (r["obs_id"], r["horizon"], r["kind"], r.get("winner"),
         json.dumps(r.get("payload") or {}, default=str, sort_keys=True),
         r["evidence"])
        for r in rows
    ]
    return _inserted(
        con,
        "INSERT OR REPLACE INTO vehicle_comparison (obs_id, horizon, kind, winner, "
        "payload_json, evidence) VALUES (?,?,?,?,?,?)",
        payload,
    )


def clear_derived(con: sqlite3.Connection) -> None:
    """Drop every derived row so a run rebuilds them from raw.

    Only the derived tables are named here; a raw table passed to this function
    would be rejected by its own trigger, which is the intended failure.
    """
    for table in DERIVED_TABLES:
        con.execute(f"DELETE FROM {table}")  # noqa: S608 - table names are literals
    con.commit()


def clear_derived_window(
    con: sqlite3.Connection, start_ts: float, end_ts: float,
) -> dict:
    """Drop the derived rows belonging to observations in ``[start_ts, end_ts)``.

    A leg is a function of its own observation's quotes and of later quotes on the
    same contract *within the same session* — never of another session — so a
    session's derived rows can be dropped and rebuilt without touching any other
    session's. That is what makes the rebuild resumable, and the smoke suite
    asserts it by comparing a session-at-a-time rebuild against a whole-store one.

    Only derived tables are named. Raw rows in the window are untouched, and an
    attempt to delete one would be aborted by its own trigger.
    """
    args = (float(start_ts), float(end_ts))
    legs = [
        str(r["leg_id"]) for r in con.execute(
            "SELECT leg_id FROM paper_leg WHERE entry_ts >= ? AND entry_ts < ?",
            args,
        )
    ]
    removed = {"paper_leg": 0, "leg_path": 0, "leg_attribution": 0,
               "vehicle_comparison": 0}
    for chunk_start in range(0, len(legs), 500):
        chunk = legs[chunk_start:chunk_start + 500]
        marks = ",".join("?" * len(chunk))
        for table in ("leg_path", "leg_attribution"):
            before = con.total_changes
            con.execute(
                f"DELETE FROM {table} WHERE leg_id IN ({marks})",  # noqa: S608
                chunk,
            )
            removed[table] += int(con.total_changes - before)
    before = con.total_changes
    con.execute(
        "DELETE FROM vehicle_comparison WHERE obs_id IN "
        "(SELECT obs_id FROM raw_observation WHERE ts >= ? AND ts < ?)", args,
    )
    removed["vehicle_comparison"] = int(con.total_changes - before)
    before = con.total_changes
    con.execute(
        "DELETE FROM paper_leg WHERE entry_ts >= ? AND entry_ts < ?", args,
    )
    removed["paper_leg"] = int(con.total_changes - before)
    con.commit()
    return removed


def counts(con: sqlite3.Connection) -> dict:
    out: dict[str, int] = {}
    for table in RAW_TABLES + DERIVED_TABLES:
        row = con.execute(
            f"SELECT COUNT(*) AS n FROM {table}"  # noqa: S608 - literal names
        ).fetchone()
        out[table] = int(row["n"]) if row else 0
    return out


def observations(
    con: sqlite3.Connection,
    *,
    instrument: str | None = None,
    source: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    return list(
        iter_observations(
            con, instrument=instrument, source=source, limit=limit
        )
    )


def iter_observations(
    con: sqlite3.Connection,
    *,
    instrument: str | None = None,
    source: str | None = None,
    limit: int | None = None,
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> Iterator[dict]:
    """The same rows in the same order, one at a time.

    A full-store rebuild reads every observation, and materialising them all
    first costs memory that the rebuild then competes with itself for. The
    ordering is identical, so a caller that streams produces identical output.
    """
    sql = "SELECT * FROM raw_observation WHERE 1=1"
    args: list[object] = []
    if instrument:
        sql += " AND instrument = ?"
        args.append(instrument)
    if source:
        sql += " AND source = ?"
        args.append(source)
    if start_ts is not None:
        sql += " AND ts >= ?"
        args.append(float(start_ts))
    if end_ts is not None:
        sql += " AND ts < ?"
        args.append(float(end_ts))
    sql += " ORDER BY ts"
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    cur = con.execute(sql, args)
    try:
        for row in cur:
            yield dict(row)
    finally:
        cur.close()


def quotes(con: sqlite3.Connection, obs_id: str) -> list[dict]:
    return [
        dict(r) for r in con.execute(
            "SELECT * FROM raw_quote WHERE obs_id = ? ORDER BY vehicle", (obs_id,),
        ).fetchall()
    ]


def path_samples(con: sqlite3.Connection, obs_id: str, vehicle: str) -> list[dict]:
    return [
        dict(r) for r in con.execute(
            "SELECT * FROM raw_path_sample WHERE obs_id = ? AND vehicle = ? "
            "ORDER BY ts", (obs_id, vehicle),
        ).fetchall()
    ]


def legs(
    con: sqlite3.Connection,
    *,
    book: str | None = None,
    resolved: bool | None = None,
    instrument: str | None = None,
    vehicle: str | None = None,
) -> list[dict]:
    sql = "SELECT * FROM paper_leg WHERE 1=1"
    args: list[object] = []
    if book:
        sql += " AND book = ?"
        args.append(book)
    if instrument:
        sql += " AND instrument = ?"
        args.append(instrument)
    if vehicle:
        sql += " AND vehicle = ?"
        args.append(vehicle)
    if resolved is not None:
        sql += " AND resolved = ?"
        args.append(1 if resolved else 0)
    sql += " ORDER BY entry_ts"
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def attributions(con: sqlite3.Connection) -> list[dict]:
    return [
        dict(r) for r in con.execute("SELECT * FROM leg_attribution").fetchall()
    ]


def comparisons(con: sqlite3.Connection, kind: str | None = None) -> list[dict]:
    if kind:
        rows = con.execute(
            "SELECT * FROM vehicle_comparison WHERE kind = ?", (kind,),
        ).fetchall()
    else:
        rows = con.execute("SELECT * FROM vehicle_comparison").fetchall()
    return [dict(r) for r in rows]
