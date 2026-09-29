"""Phase 25 §1 — the captured option books, and what they can support.

Reads the stored chain snapshots the live engine wrote and turns them into a
per-contract quote path. Three properties matter more than the plumbing:

* **only real books count.** A snapshot is used only when the broker produced it
  (``REAL_BROKER``) and a leg is used only when it carries a positive,
  uncrossed two-sided quote. Simulator rows and one-sided legs are counted for
  coverage and never priced;
* **the store is live.** The engine writes to the same database, so every read
  is paged over ``rowid`` with per-page retries — a writer costs one retried
  page, not the whole load — and an unreadable store reports a classified cause
  and ``unmeasured``, never a zero that reads like an empty capture;
* **eligibility is declared before results.** An instrument below the snapshot,
  session or contract-path minimums is ``REQUIRES_MORE_DATA``. It is not scored
  on a handful of books and then compared against an instrument with thousands.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase25 import REQUIRES_MORE_DATA

HISTORY_DB = "data/history.db"

REAL_BROKER = "REAL_BROKER"
SIMULATOR = "SIMULATOR"

# Paging, for the same reason Phase 24 pages: one long query against a store the
# engine is writing dies on the first lock and loses the entire count.
PAGE_ROWS = 500
PAGE_ATTEMPTS = 4
PAGE_BACKOFF_SEC = 0.75

IST_OFFSET = 19_800

# Minimums for an instrument to be studied rather than reported. A captured
# window is short by construction, so these are sample minimums, not a claim
# that the window is long enough to validate anything.
MIN_SNAPSHOTS = 500
MIN_SESSIONS = 4
# Forward-resolvable quotes: how many stored observations have a *later* quote
# of the same contract to resolve against. This is the sample bar.
MIN_RESOLVABLE_QUOTES = 2_000
# Distinct contracts is not a sample bar and must not be used as one: a captured
# ladder is a few strikes wide, so it is bounded by ladder width however long
# the capture runs. It is kept as an independence floor — a result must not rest
# on one or two contracts' paths — and reported as a caveat.
MIN_CONTRACT_PATHS = 8


class Quotes:
    """One option contract's stored quote path, in snapshot order.

    ``pos`` is the contract's own position inside the instrument's snapshot
    sequence, so a forward walk never assumes the contract was quoted in every
    snapshot — on a thin name it usually was not.
    """

    __slots__ = ("symbol", "strike", "option_type", "pos", "ts", "bid", "ask")

    def __init__(self, symbol: str, strike: float, option_type: str) -> None:
        self.symbol = symbol
        self.strike = float(strike)
        self.option_type = option_type
        self.pos: list[int] = []
        self.ts: list[int] = []
        self.bid: list[float] = []
        self.ask: list[float] = []

    def freeze(self) -> None:
        self.pos = np.asarray(self.pos, dtype=np.int64)
        self.ts = np.asarray(self.ts, dtype=np.int64)
        self.bid = np.asarray(self.bid, dtype=np.float64)
        self.ask = np.asarray(self.ask, dtype=np.float64)

    def __len__(self) -> int:
        return int(len(self.pos))


class Chain:
    """One instrument's captured books: snapshots, legs and contract paths."""

    __slots__ = ("instrument", "ts", "legs", "quotes", "snapshots", "sessions",
                 "simulator_snapshots", "one_sided_legs")

    def __init__(self, instrument: str) -> None:
        self.instrument = instrument.upper()
        self.ts: np.ndarray = np.zeros(0, dtype=np.int64)
        self.legs: list[dict[str, dict]] = []
        self.quotes: dict[str, Quotes] = {}
        self.snapshots = 0
        self.sessions = 0
        self.simulator_snapshots = 0
        self.one_sided_legs = 0

    def __len__(self) -> int:
        return int(self.ts.size)


def _resolve(rel: str) -> str:
    if os.path.isabs(rel):
        return rel
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))
    return os.path.join(root, rel)


def store_path() -> str:
    return _resolve(HISTORY_DB)


def _usable(leg: dict) -> bool:
    """A leg with a real, positive, uncrossed two-sided quote."""
    bid, ask = leg.get("bid"), leg.get("ask")
    if isinstance(bid, bool) or isinstance(ask, bool):
        return False
    if not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)):
        return False
    return float(bid) > 0 and float(ask) > 0 and float(ask) >= float(bid)


def _parse(payload: str) -> list[dict] | None:
    try:
        arr = json.loads(payload)
    except Exception:
        return None
    return arr if isinstance(arr, list) else None


def _page(con: sqlite3.Connection, instrument: str, after: int) -> list[tuple]:
    return con.execute(
        "SELECT rowid, ts, payload, source FROM chain_snapshots "
        "WHERE instrument = ? AND rowid > ? ORDER BY rowid LIMIT ?",
        (instrument, after, PAGE_ROWS),
    ).fetchall()


def load_chain(instrument: str, *, db_path: str | None = None) -> Chain:
    """Every stored real-broker snapshot for one instrument, as quote paths.

    Raises :class:`sqlite3.Error` when the store cannot be read after the
    retries; the caller reports that as unmeasured rather than as empty.
    """
    inst = (instrument or "").upper()
    chain = Chain(inst)
    path = db_path or store_path()
    if not os.path.exists(path):
        return chain

    ts_list: list[int] = []
    after = 0
    while True:
        rows: list[tuple] | None = None
        last: sqlite3.Error | None = None
        for attempt in range(PAGE_ATTEMPTS):
            con = p24data.connect_readonly(path)
            try:
                rows = _page(con, inst, after)
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
        for rid, ts, payload, source in rows:
            after = max(after, int(rid))
            if str(source or "").upper() != REAL_BROKER:
                chain.simulator_snapshots += 1
                continue
            arr = _parse(payload) if isinstance(payload, str) else None
            if not arr:
                continue
            book: dict[str, dict] = {}
            for leg in arr:
                if not isinstance(leg, dict):
                    continue
                if not _usable(leg):
                    chain.one_sided_legs += 1
                    continue
                symbol = str(leg.get("symbol") or "")
                otype = str(leg.get("option_type") or "").upper()
                strike = leg.get("strike")
                if not symbol or otype not in ("CE", "PE"):
                    continue
                if not isinstance(strike, (int, float)) or isinstance(strike, bool):
                    continue
                book[symbol] = leg
            if not book:
                continue
            pos = len(ts_list)
            ts_list.append(int(ts))
            chain.legs.append(book)
            for symbol, leg in book.items():
                q = chain.quotes.get(symbol)
                if q is None:
                    q = Quotes(symbol, float(leg["strike"]),
                               str(leg["option_type"]).upper())
                    chain.quotes[symbol] = q
                q.pos.append(pos)
                q.ts.append(int(ts))
                q.bid.append(float(leg["bid"]))
                q.ask.append(float(leg["ask"]))

    chain.ts = np.asarray(ts_list, dtype=np.int64)
    chain.snapshots = int(chain.ts.size)
    chain.sessions = int(np.unique((chain.ts + IST_OFFSET) // 86_400).size) if (
        chain.snapshots
    ) else 0
    for q in chain.quotes.values():
        q.freeze()
    return chain


def contract_paths(chain: Chain, *, min_quotes: int = 2) -> int:
    """Contracts quoted often enough to have a forward path at all."""
    return int(sum(1 for q in chain.quotes.values() if len(q) >= min_quotes))


def resolvable_quotes(chain: Chain, *, min_quotes: int = 2) -> int:
    """Stored observations that have a later quote of the same contract.

    This is the number of candidates the store can actually resolve, so it is
    the sample the study is limited by — not the number of distinct contracts.
    """
    return int(sum(len(q) - 1 for q in chain.quotes.values()
                   if len(q) >= min_quotes))


def eligibility(chain: Chain) -> dict:
    """Whether this instrument can be studied, with the counts behind it."""
    paths = contract_paths(chain)
    resolvable = resolvable_quotes(chain)
    reasons: list[str] = []
    if chain.snapshots < MIN_SNAPSHOTS:
        reasons.append(
            f"only {chain.snapshots:,} real-broker snapshots with a two-sided "
            f"book (needs {MIN_SNAPSHOTS:,})"
        )
    if chain.sessions < MIN_SESSIONS:
        reasons.append(
            f"only {chain.sessions} captured session(s) (needs {MIN_SESSIONS})"
        )
    if resolvable < MIN_RESOLVABLE_QUOTES:
        reasons.append(
            f"only {resolvable:,} stored quotes have a later quote of the same "
            f"contract to resolve against (needs "
            f"{MIN_RESOLVABLE_QUOTES:,})"
        )
    if paths < MIN_CONTRACT_PATHS:
        reasons.append(
            f"only {paths:,} distinct contracts were quoted more than once, so "
            f"a result would rest on too few independent legs (needs "
            f"{MIN_CONTRACT_PATHS:,})"
        )
    return {
        "instrument": chain.instrument,
        "snapshots": chain.snapshots,
        "sessions": chain.sessions,
        "contract_paths": paths,
        "resolvable_quotes": resolvable,
        "independence_caveat": (
            f"{resolvable:,} resolvable quotes come from only {paths:,} "
            f"distinct contracts, so candidates overlap heavily and the "
            f"effective sample is far smaller than the trade count"
        ),
        "simulator_snapshots_excluded": chain.simulator_snapshots,
        "one_sided_legs_excluded": chain.one_sided_legs,
        "first_ts": int(chain.ts[0]) if chain.snapshots else 0,
        "last_ts": int(chain.ts[-1]) if chain.snapshots else 0,
        "eligible": not reasons,
        "status": "ELIGIBLE" if not reasons else REQUIRES_MORE_DATA,
        "reasons": reasons,
    }


def instruments_with_books(*, db_path: str | None = None) -> dict[str, int]:
    """Snapshot count per instrument, real-broker rows only.

    Cheap enough to run before loading anything: it never touches a payload.
    """
    path = db_path or store_path()
    out: dict[str, int] = {}
    if not os.path.exists(path):
        return out
    q = (
        "SELECT instrument, COUNT(*) FROM chain_snapshots "
        "WHERE source = ? GROUP BY instrument"
    )
    last: sqlite3.Error | None = None
    for attempt in range(PAGE_ATTEMPTS):
        con = p24data.connect_readonly(path)
        try:
            for name, count in con.execute(q, (REAL_BROKER,)).fetchall():
                out[str(name).upper()] = int(count)
            return out
        except sqlite3.Error as exc:
            last = exc
            time.sleep(PAGE_BACKOFF_SEC * (attempt + 1))
        finally:
            con.close()
    raise last if last is not None else sqlite3.OperationalError("unreadable store")
