"""One-off, idempotent copy of SQLite databases into PostgreSQL.

    python -m app.migrate_sqlite [--dry-run] [--pg-url URL] PATH [PATH ...]

PATH may be a SQLite file or a directory (searched recursively for SQLite files).
Sources are opened read-only and never modified.

* ``jr_*`` tables are copied into the live journal v2 tables (deterministic ids,
  ``ON CONFLICT DO NOTHING``), so re-running never duplicates rows.
* Every other table goes to schema ``legacy_<file stem>`` with the same columns
  plus ``_row_hash`` (sha256 of the row) as primary key, so re-runs are no-ops.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BATCH = 5000
_SQLITE_EXT = {".db", ".sqlite", ".sqlite3"}
_TYPES = {"integer": "BIGINT", "real": "DOUBLE PRECISION", "blob": "BYTEA", "text": "TEXT"}


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def find_sqlite(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.suffix.lower() in _SQLITE_EXT and _is_sqlite(f))
        elif _is_sqlite(p):
            out.append(p)
        else:
            print(f"skip (not a SQLite file): {p}")
    return out


def _open_ro(path: Path) -> tuple[sqlite3.Connection, str | None]:
    """Open read-only; if that fails (e.g. WAL file on a read-only mount), read a temp copy."""
    try:
        # A WAL file means even a read-only open may touch -shm; read a copy instead.
        if Path(str(path) + "-wal").exists():
            raise sqlite3.OperationalError("WAL present")
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return conn, None
    except sqlite3.OperationalError:
        tmp = tempfile.mkdtemp(prefix="qt_mig_")
        for suffix in ("", "-wal", "-shm"):
            side = Path(str(path) + suffix)
            if side.exists():
                shutil.copy2(side, Path(tmp) / (path.name + suffix))
        print(f"  (read via temporary copy: {path.name})")
        return sqlite3.connect(Path(tmp) / path.name), tmp


def _affinity(decl: str) -> str:
    d = (decl or "").upper()
    if "INT" in d:
        return "integer"
    if any(k in d for k in ("REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL")):
        return "real"
    if "BLOB" in d:
        return "blob"
    return "text"


def _pg_columns(src: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    cols = []
    for _cid, name, decl, *_ in src.execute(f"PRAGMA table_info({_q(table)})"):
        aff = _affinity(decl)
        allowed = {"integer": ("integer",), "real": ("real", "integer"), "blob": ("blob",),
                   "text": ("text",)}[aff]
        bad = src.execute(
            f"SELECT 1 FROM {_q(table)} WHERE typeof({_q(name)}) NOT IN "
            f"({','.join('?' * (len(allowed) + 1))}) LIMIT 1", (*allowed, "null")).fetchone()
        cols.append((name, "TEXT" if bad else _TYPES[aff]))
    return cols


def _row_hash(row: tuple) -> str:
    def enc(v):
        return v.hex() if isinstance(v, (bytes, memoryview)) else v
    return hashlib.sha256(json.dumps([enc(v) for v in row], default=str).encode()).hexdigest()


def _count(cur, fq: str) -> int:
    cur.execute(f"SELECT COUNT(*) FROM {fq}")
    return int(cur.fetchone()[0])


def _pg_table_exists(cur, schema: str, table: str) -> bool:
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
                (schema, table))
    return cur.fetchone() is not None


def _copy(src, cur, table: str, fq: str, src_cols: list[str], dst_cols: list[str],
          conflict: str, with_hash: bool, dry: bool,
          bool_cols: frozenset[int] = frozenset()) -> tuple[int, int]:
    n_src = src.execute(f"SELECT COUNT(*) FROM {_q(table)}").fetchone()[0]
    if dry:
        return n_src, 0
    before = _count(cur, fq)
    cols = (["_row_hash"] if with_hash else []) + dst_cols
    sql = (f"INSERT INTO {fq} ({','.join(map(_q, cols))}) VALUES "
           f"({','.join(['%s'] * len(cols))}) ON CONFLICT {conflict} DO NOTHING")
    rows = src.execute(f"SELECT {','.join(map(_q, src_cols))} FROM {_q(table)}")
    while True:
        chunk = rows.fetchmany(BATCH)
        if not chunk:
            break
        if bool_cols:  # SQLite stores booleans as 0/1
            chunk = [tuple(bool(v) if i in bool_cols and v is not None else v
                           for i, v in enumerate(r)) for r in chunk]
        if with_hash:
            chunk = [(_row_hash(tuple(r)), *r) for r in chunk]
        cur.executemany(sql, [tuple(r) for r in chunk])
    return n_src, _count(cur, fq) - before


def migrate(paths: list[str], pg_url: str, dry: bool = False) -> int:
    import psycopg

    files = find_sqlite(paths)
    if not files:
        print("no SQLite files found")
        return 1
    if not dry:
        # make sure the journal v2 tables exist before copying jr_* rows
        from app.journal.store import JournalStore
        from app.research.db import Database
        JournalStore(Database(pg_url))
    with psycopg.connect(pg_url) as pg:
        failed: list[str] = []
        for f in files:
            print(f"\n== {f}")
            try:
                src, tmp = _open_ro(f)
            except (sqlite3.Error, OSError) as exc:
                print(f"  FAILED to open: {exc}")
                failed.append(str(f))
                continue
            try:
                _migrate_file(src, f, pg, dry)
            except Exception as exc:  # noqa: BLE001 - report and continue with other files
                pg.rollback()
                print(f"  FAILED: {type(exc).__name__}: {exc}")
                failed.append(str(f))
            finally:
                src.close()
                if tmp:
                    shutil.rmtree(tmp, ignore_errors=True)
    if failed:
        print("\nFAILED files:\n  " + "\n  ".join(failed))
        return 1
    return 0


def _migrate_file(src: sqlite3.Connection, f: Path, pg, dry: bool) -> None:
    schema = "legacy_" + re.sub(r"[^a-z0-9_]", "_", f.stem.lower())
    tables = [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for t in tables:
        with pg.cursor() as cur:
            if t.startswith("jr_") and (dry or _pg_table_exists(cur, "public", t)):
                names = [c[1] for c in src.execute(f"PRAGMA table_info({_q(t)})")]
                if not dry:
                    cur.execute("SELECT column_name, data_type FROM information_schema.columns "
                                "WHERE table_schema='public' AND table_name=%s", (t,))
                    have = dict(cur.fetchall())
                    names = [c for c in names if c in have]
                bools = frozenset(i for i, c in enumerate(names) if not dry and have[c] == "boolean")
                n, ins = _copy(src, cur, t, _q(t), names, names, "", False, dry, bools)
                dest = f"public.{t}"
            else:
                cols = _pg_columns(src, t)
                fq = f"{_q(schema)}.{_q(t)}"
                if not dry:
                    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_q(schema)}")
                    cur.execute(
                        f"CREATE TABLE IF NOT EXISTS {fq} (_row_hash TEXT PRIMARY KEY, "
                        + ", ".join(f"{_q(c)} {ty}" for c, ty in cols) + ")")
                names = [c for c, _ in cols]
                n, ins = _copy(src, cur, t, fq, names, names, "(_row_hash)", True, dry)
                dest = f"{schema}.{t}"
        if not dry:
            pg.commit()
        print(f"  {t}: {n} source rows -> {dest}" + ("" if dry else f" (+{ins} new)"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--pg-url", default=os.environ.get("QT_DB_URL", ""))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if not a.pg_url.startswith(("postgres://", "postgresql://")):
        print("QT_DB_URL / --pg-url must be a PostgreSQL URL", file=sys.stderr)
        return 2
    return migrate(a.paths, a.pg_url, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
