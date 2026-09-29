"""Phase 24 §1 — the universe, and what history actually covers.

This module answers one question honestly: for which instruments is there enough
history to run a chronological five-year study? It does not widen a series, fill
a gap or resample a thin instrument up to a usable length.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

import numpy as np

from app.research.phase24 import INSUFFICIENT_HISTORY

# The requested universe, kept verbatim so the report can show what was asked
# for next to what the data supports.
INDEX = ("NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY")
MCX = ("CRUDEOIL", "NATURALGAS", "GOLD", "SILVER", "COPPER")

# One-minute five-year series shipped with the repository.
BACKTEST_FILES = {
    "NIFTY": "data/backtest/NIFTY_ONE_MINUTE.jsonl",
    "CRUDEOIL": "data/backtest/CRUDEOIL_ONE_MINUTE.jsonl",
}

HISTORY_DB = "data/history.db"

# A five-year chronological split needs at least three development years, one
# validation year and one untouched holdout year. Below this an instrument cannot
# be split at all, so it is excluded rather than reported on a shorter window and
# silently compared against instruments with five years.
MIN_SESSIONS = 750
MIN_BARS = 100_000

# A CE/PE study needs enough real two-sided books to price entry-at-ask and
# exit-at-bid across more than a handful of sessions. Below this the option half
# of the study stays REQUIRES_MORE_DATA.
MIN_TWO_SIDED_BOOKS = 5_000

# Counting books walks every stored payload, which on a long capture is hundreds
# of megabytes of JSON. Done as one query it holds a read transaction open for
# minutes against a store the live engine is writing, and the whole count dies on
# the first lock. Paged instead, each page its own short query, each page retried.
PAGE_ROWS = 2_000
PAGE_ATTEMPTS = 4
PAGE_BACKOFF_SEC = 0.75


def _cause(exc: sqlite3.Error) -> str:
    """A fixed, safe description of why the store could not be read.

    The sqlite message is never surfaced: it can name tables, columns and file
    paths. What the operator needs is the actionable cause, so the message is
    classified into one of a fixed set of sentences and the rest discarded.
    """
    msg = str(exc).lower()
    if "locked" in msg or "busy" in msg:
        return "the live engine is holding the store's write lock"
    if "no such table" in msg:
        return "the store has no option-chain table yet"
    if "no such column" in msg:
        return "the option-chain table predates provenance labelling"
    if "readonly" in msg or "read-only" in msg or "attempt to write" in msg:
        return (
            "the store is in WAL mode and its shared-memory file cannot be "
            "created from this process"
        )
    if "disk i/o" in msg or "malformed" in msg or "corrupt" in msg:
        return "the store reported a disk or integrity error"
    return "the store could not be read"


def _connect_ro(path: str) -> sqlite3.Connection:
    """Open the history database for reading while the engine may be writing it.

    Two live-engine states break a plain `mode=ro` handle, and both look like an
    unreadable store rather than a busy one:

    * the writer holds the lock, which a busy timeout waits out instead of
      failing immediately;
    * the store is in WAL mode, where a read-only handle cannot create the -shm
      file it needs and every query fails until someone opens it writably.

    So the read-only handle is probed with a real query, and only if that fails
    is a normal handle used. Nothing in this module issues a write; the second
    handle exists to satisfy WAL's shared-memory requirement, not to modify the
    engine's store.
    """
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30.0)
    try:
        con.execute("PRAGMA busy_timeout = 30000")
        con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
        return con
    except sqlite3.Error:
        con.close()
    con = sqlite3.connect(path, timeout=30.0)
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA query_only = ON")
    return con


def cause(exc: sqlite3.Error) -> str:
    """Public name for the safe error classification, for other packages.

    Additive wrapper around the same fixed sentences; the sqlite message is
    still never surfaced.
    """
    return _cause(exc)


def connect_readonly(path: str) -> sqlite3.Connection:
    """Public name for the live-store read handle, for other research packages.

    Additive wrapper: the behaviour and every existing caller are unchanged. It
    exists so a separate study does not have to re-derive the WAL and
    busy-writer handling that this one already got wrong once.
    """
    return _connect_ro(path)


class Series:
    """One instrument's 1-minute series as parallel numpy arrays.

    Arrays rather than rows because the outcome resolver walks the same forward
    window for hundreds of thousands of candidates; a per-row object model turns
    a two-minute study into an hour.
    """

    __slots__ = ("instrument", "ts", "open", "high", "low", "close", "volume")

    def __init__(self, instrument: str, rows: list[dict]) -> None:
        rows = sorted(rows, key=lambda r: int(r["time"]))
        self.instrument = instrument
        self.ts = np.array([int(r["time"]) for r in rows], dtype=np.int64)
        self.open = np.array([float(r["open"]) for r in rows], dtype=np.float64)
        self.high = np.array([float(r["high"]) for r in rows], dtype=np.float64)
        self.low = np.array([float(r["low"]) for r in rows], dtype=np.float64)
        self.close = np.array([float(r["close"]) for r in rows], dtype=np.float64)
        self.volume = np.array(
            [float(r.get("volume") or 0.0) for r in rows], dtype=np.float64
        )

    def __len__(self) -> int:
        return int(self.ts.size)


def _root() -> str:
    """Backend directory, so the CLI works from any working directory."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))


def _resolve(rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(_root(), rel)


def load_series(instrument: str) -> Series | None:
    """Load a five-year 1-minute series, or ``None`` when there isn't one.

    Duplicate timestamps are collapsed to the first occurrence: the collector
    pages history in overlapping chunks, and a duplicated bar would be counted
    twice in every statistic downstream.
    """
    rel = BACKTEST_FILES.get(instrument.upper())
    if not rel:
        return _imported_series(instrument)
    path = _resolve(rel)
    if not os.path.exists(path):
        return _imported_series(instrument)
    seen: set[int] = set()
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                t = int(r["time"])
            except Exception:
                continue
            if t in seen:
                continue
            seen.add(t)
            rows.append(r)
    if not rows:
        return None
    return Series(instrument.upper(), rows)


def _imported_series(instrument: str) -> "Series | None":
    """Fall back to an operator-imported historical CSV, when one is registered.

    Additive and last in precedence: a shipped five-year file always wins, so no
    existing result changes source, and with no imports registered this returns
    ``None`` exactly as the bare lookup did. The import layer is what decides
    eligibility; nothing is re-judged here on friendlier terms.
    """
    try:
        from app.research.historical_import import adapter
    except ImportError:
        # The import layer is an overlay; a tree without it behaves as before.
        return None
    return adapter.load_series(instrument, timeframe=1)


def _imported_instruments() -> set[str]:
    """Instruments an operator has imported a CSV for, so coverage names them.

    Additive: an empty registry leaves the requested universe exactly as it was.
    """
    try:
        from app.research.historical_import import registry
    except ImportError:
        return set()
    return {
        (r.get("instrument") or "").upper()
        for r in registry.datasets()
        if r.get("dataset_id") and int(r.get("timeframe") or 0) == 1
    } - {""}


def _thin_instruments() -> dict[str, dict]:
    """Row counts for every instrument that only has captured candles.

    These are read from ``history.db`` purely to report *why* they are excluded.
    """
    path = _resolve(HISTORY_DB)
    out: dict[str, dict] = {}
    if not os.path.exists(path):
        return out
    q = (
        "SELECT instrument, COUNT(*), MIN(ts), MAX(ts) "
        "FROM candles GROUP BY instrument"
    )
    # Retried for the same reason as the book scan: a live writer holding the
    # lock would otherwise turn every captured bar count into a zero, which reads
    # like an empty store instead of a busy one.
    for attempt in range(PAGE_ATTEMPTS):
        con = _connect_ro(path)
        try:
            rows = con.execute(q).fetchall()
        except sqlite3.Error:
            time.sleep(PAGE_BACKOFF_SEC * (attempt + 1))
            continue
        finally:
            con.close()
        for name, bars, lo, hi in rows:
            out[str(name).upper()] = {
                "bars": int(bars),
                "first_ts": int(lo or 0),
                "last_ts": int(hi or 0),
            }
        return out
    return out


def coverage() -> dict:
    """What the study can and cannot run on, with the counts behind each verdict."""
    thin = _thin_instruments()
    usable: list[dict] = []
    excluded: list[dict] = []
    for inst in sorted(set(INDEX) | set(MCX) | set(BACKTEST_FILES)
                       | _imported_instruments()):
        s = load_series(inst)
        if s is not None and len(s) >= MIN_BARS:
            sessions = int(np.unique(s.ts // 86_400).size)
            usable.append({
                "instrument": inst,
                "bars": len(s),
                "sessions": sessions,
                "first_ts": int(s.ts[0]),
                "last_ts": int(s.ts[-1]),
            })
            continue
        info = thin.get(inst, {})
        excluded.append({
            "instrument": inst,
            "bars": int(info.get("bars") or (len(s) if s else 0)),
            "first_ts": int(info.get("first_ts") or 0),
            "last_ts": int(info.get("last_ts") or 0),
            "status": INSUFFICIENT_HISTORY,
            "reason": (
                "no five-year 1-minute series on disk; only captured live "
                "candles. Build one with the history collector "
                "(phase21 equity/index collector) before this instrument can "
                "enter the study."
                if inst in BACKTEST_FILES else
                "no five-year 1-minute series on disk; only captured live candles"
            ),
        })
    return {
        "requested_universe": {
            "index": list(INDEX),
            "mcx": list(MCX),
            "equity": "all liquid optionable equities",
        },
        "usable": usable,
        "excluded": excluded,
        "min_bars_required": MIN_BARS,
        "equity_note": (
            "no equity has a five-year 1-minute series on disk; equity discovery "
            "cannot be run and is reported as INSUFFICIENT_HISTORY, not as a "
            "negative result"
        ),
    }


def _read_counts(path: str) -> tuple[int, int]:
    """Total snapshots and real-broker snapshots, retried past a busy writer."""
    last: sqlite3.Error | None = None
    for attempt in range(PAGE_ATTEMPTS):
        con = _connect_ro(path)
        try:
            total = int(
                con.execute("SELECT COUNT(*) FROM chain_snapshots").fetchone()[0]
            )
            real = int(con.execute(
                "SELECT COUNT(*) FROM chain_snapshots WHERE source = ?",
                ("REAL_BROKER",),
            ).fetchone()[0])
            return total, real
        except sqlite3.Error as exc:
            last = exc
            time.sleep(PAGE_BACKOFF_SEC * (attempt + 1))
        finally:
            con.close()
    raise last if last is not None else sqlite3.OperationalError("unreadable store")


def _has_two_sided_leg(payload: str) -> bool:
    """Whether a stored chain payload carries at least one usable book.

    Usable means a real quote on both sides: two positive numbers with the ask
    at or above the bid. A one-sided or crossed book cannot price entry-at-ask
    and exit-at-bid, so it does not count.
    """
    # Cheap reject before parsing: most unusable payloads have no bid key at all,
    # and parsing a multi-hundred-kilobyte ladder to discover that is the whole
    # cost of the scan.
    if '"bid"' not in payload:
        return False
    try:
        arr = json.loads(payload)
    except Exception:
        return False
    if not isinstance(arr, list):
        return False
    for leg in arr:
        if not isinstance(leg, dict):
            continue
        bid, ask = leg.get("bid"), leg.get("ask")
        if (
            isinstance(bid, (int, float)) and not isinstance(bid, bool)
            and isinstance(ask, (int, float)) and not isinstance(ask, bool)
            and float(bid) > 0 and float(ask) > 0 and float(ask) >= float(bid)
        ):
            return True
    return False


def _scan_books(path: str) -> tuple[int, dict[str, int], int, int]:
    """Count snapshots carrying a real two-sided book, one short page at a time.

    Paging by rowid keeps each read transaction to milliseconds, so a writer that
    grabs the lock costs one retried page instead of the whole count. Pages are
    disjoint and ordered, so a row is neither counted twice nor skipped.
    """
    two = 0
    by_inst: dict[str, int] = {}
    lo_ts, hi_ts = 0, 0
    after = 0
    q = (
        "SELECT rowid, instrument, ts, payload FROM chain_snapshots "
        "WHERE rowid > ? AND source IS NOT ? ORDER BY rowid LIMIT ?"
    )
    while True:
        rows: list[tuple] | None = None
        last: sqlite3.Error | None = None
        for attempt in range(PAGE_ATTEMPTS):
            con = _connect_ro(path)
            try:
                rows = con.execute(q, (after, "SIMULATOR", PAGE_ROWS)).fetchall()
                break
            except sqlite3.Error as exc:
                last = exc
                time.sleep(PAGE_BACKOFF_SEC * (attempt + 1))
            finally:
                con.close()
        if rows is None:
            raise last if last is not None else sqlite3.OperationalError(
                "unreadable store"
            )
        if not rows:
            break
        for rid, inst, ts, payload in rows:
            after = max(after, int(rid))
            if not isinstance(payload, str) or not _has_two_sided_leg(payload):
                continue
            two += 1
            name = str(inst).upper()
            by_inst[name] = by_inst.get(name, 0) + 1
            t = int(ts or 0)
            lo_ts = t if lo_ts == 0 else min(lo_ts, t)
            hi_ts = max(hi_ts, t)
    return two, by_inst, lo_ts, hi_ts


def option_book_coverage() -> dict:
    """How much of the stored option-chain history has a real two-sided book.

    This is the number that decides whether §11 (independent CE/PE discovery) can
    run at all, so it is measured rather than assumed.
    """
    path = _resolve(HISTORY_DB)
    out: dict = {
        "snapshots": 0,
        "real_broker_snapshots": 0,
        "snapshots_with_two_sided_book": 0,
        "two_sided_by_instrument": {},
        "two_sided_first_ts": 0,
        "two_sided_last_ts": 0,
        "ce_pe_study_possible": False,
        "unmeasured": False,
        "note": "",
    }
    if not os.path.exists(path):
        out["unmeasured"] = True
        out["note"] = "no history database on disk, so nothing could be measured"
        return out
    try:
        counts = _read_counts(path)
        out["snapshots"] = counts[0]
        out["real_broker_snapshots"] = counts[1]
        two, by_inst, lo_ts, hi_ts = _scan_books(path)
        out["snapshots_with_two_sided_book"] = two
        out["two_sided_by_instrument"] = dict(sorted(by_inst.items()))
        out["two_sided_first_ts"] = lo_ts
        out["two_sided_last_ts"] = hi_ts
    except sqlite3.Error as exc:
        # No sqlite message is surfaced; the classified cause says what to do
        # about it, and the store is reported as unmeasured rather than empty.
        out["note"] = (
            "the stored option books could not be counted: "
            f"{_cause(exc)}. CE/PE stays REQUIRES_MORE_DATA — this is an "
            "unmeasured store, not an empty one. Retry with the engine stopped "
            "(./stop.sh) to measure it"
        )
        out["unmeasured"] = True
        return out
    # The verdict follows the store, not a fixed sentence: a capture that has
    # accumulated real two-sided books makes the CE/PE half of the study
    # possible over its own window, and one that has not does not.
    two = int(out["snapshots_with_two_sided_book"])
    out["ce_pe_study_possible"] = two >= MIN_TWO_SIDED_BOOKS
    if two == 0:
        out["note"] = (
            "no stored option snapshot carries a two-sided book, so entry-at-ask "
            "and exit-at-bid cannot be honoured; CE/PE stays REQUIRES_MORE_DATA "
            "and no premium is modelled"
        )
    elif not out["ce_pe_study_possible"]:
        out["note"] = (
            f"only {two:,} stored snapshot(s) carry a two-sided book against the "
            f"{MIN_TWO_SIDED_BOOKS:,} needed for a chronological CE/PE study; "
            "CE/PE stays REQUIRES_MORE_DATA and no premium is modelled"
        )
    else:
        out["note"] = (
            f"{two:,} stored snapshots carry a real two-sided book across "
            f"{len(out['two_sided_by_instrument'])} instruments, so a CE/PE study "
            "is possible over the captured window (not over five years); the "
            "five-year discovery below remains futures-only"
        )
    return out
