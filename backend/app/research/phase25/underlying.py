"""Phase 25 §2 — the captured underlying candles behind each option snapshot.

The option book says what a contract cost; it says nothing about why anyone
would buy it. The market context comes from the same store's captured 1-minute
candles, and it is read through Phase 24's feature module so that "trend
agrees", "pullback 20-40%" and "volatility expanding" mean exactly what they
mean in the five-year study. One vocabulary, two datasets.

Alignment is causal and conservative: a snapshot at time ``t`` is aligned to the
last candle that **closed at or before** ``t``. A candle that is still forming
would leak the next minute's move into the entry decision.
"""
from __future__ import annotations

import os
import sqlite3
import time

import numpy as np

from app.research.phase24 import data as p24data

PAGE_ATTEMPTS = 4
PAGE_BACKOFF_SEC = 0.75


def load_series(instrument: str, *, db_path: str | None = None):
    """Captured 1-minute candles for one instrument, or ``None`` if there are none.

    Read from the live store with retries, for the same reason the book loader
    retries: a writing engine must cost a retry, not a silently empty series.
    """
    path = db_path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        )))),
        "data/history.db",
    )
    if not os.path.exists(path):
        return None
    q = (
        "SELECT ts, open, high, low, close, volume FROM candles "
        "WHERE instrument = ? ORDER BY ts"
    )
    rows: list[tuple] | None = None
    last: sqlite3.Error | None = None
    for attempt in range(PAGE_ATTEMPTS):
        con = p24data.connect_readonly(path)
        try:
            rows = con.execute(q, ((instrument or "").upper(),)).fetchall()
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
    clean = [
        {"time": int(t), "open": float(o), "high": float(h), "low": float(lo),
         "close": float(c), "volume": float(v or 0.0)}
        for t, o, h, lo, c, v in rows
        if None not in (t, o, h, lo, c)
    ]
    if not clean:
        return None
    return p24data.Series((instrument or "").upper(), clean)


def align(series, snap_ts: np.ndarray) -> np.ndarray:
    """Index of the last candle closed at or before each snapshot, or -1.

    A snapshot before the first captured candle has no context and is dropped by
    the caller rather than borrowed from the nearest later bar.
    """
    if series is None or len(series) == 0 or snap_ts.size == 0:
        return np.full(snap_ts.size, -1, dtype=np.int64)
    # ``ts`` on a candle is its open time; it has closed one minute later.
    pos = np.searchsorted(series.ts + 60, snap_ts, side="right") - 1
    return np.where(pos >= 0, pos, -1).astype(np.int64)
