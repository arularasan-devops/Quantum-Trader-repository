"""Loading recorded history with provenance kept separate. RESEARCH ONLY.

Every later Phase 7 question depends on one thing being honest: which rows came
from the broker and which from the simulator. Phase 6's export mixed 51,096
snapshots with no source column at all, so the rule below is the only available
discriminator and it is applied per snapshot, never per instrument or per day.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field

from app.models import Candle, OptionQuote, OptionType

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

REAL = "REAL_BROKER"
SIM = "SIMULATOR"
UNKNOWN = "UNKNOWN"

# How far a candle may reach for the snapshot that describes it. The recorded
# cadence is ~60s, so anything beyond 90s belongs to a different minute.
MATCH_TOLERANCE_SEC = 90


def provenance(payload: str) -> str:
    """REAL_BROKER / SIMULATOR / UNKNOWN for one stored chain payload.

    The Angel provider cannot report an OI delta and writes ``oi_change=0`` on
    every leg; the simulator draws it from a Gaussian and effectively never
    produces an all-zero payload. An unreadable or empty payload is UNKNOWN — it
    is never silently folded into either population.
    """
    try:
        legs = json.loads(payload)
    except (ValueError, TypeError):
        return UNKNOWN
    if not isinstance(legs, list) or not legs:
        return UNKNOWN
    if all((leg.get("oi_change") or 0) == 0 for leg in legs):
        return REAL
    return SIM


def as_quote(leg: dict) -> OptionQuote:
    return OptionQuote(
        symbol=str(leg["symbol"]), strike=float(leg["strike"]),
        option_type=OptionType(leg["option_type"]),
        premium=float(leg.get("premium") or 0.0), iv=float(leg.get("iv") or 0.0),
        delta=float(leg.get("delta") or 0.0), gamma=float(leg.get("gamma") or 0.0),
        theta=float(leg.get("theta") or 0.0), vega=float(leg.get("vega") or 0.0),
        oi=int(leg.get("oi") or 0), oi_change=int(leg.get("oi_change") or 0),
        volume=int(leg.get("volume") or 0),
    )


@dataclass
class ChainSeries:
    """Chain snapshots for one instrument, one provenance, ascending in time."""

    instrument: str
    source: str
    ts: list[int] = field(default_factory=list)
    legs: list[dict] = field(default_factory=list)   # {symbol: leg dict}
    raw: list[list[dict]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.ts)

    def index_at(self, when: int, tolerance: int = MATCH_TOLERANCE_SEC) -> int | None:
        """Index of the snapshot nearest ``when`` within tolerance."""
        if not self.ts:
            return None
        k = bisect.bisect_left(self.ts, when)
        best: int | None = None
        for j in (k - 1, k):
            if 0 <= j < len(self.ts) and abs(self.ts[j] - when) <= tolerance:
                if best is None or abs(self.ts[j] - when) < abs(self.ts[best] - when):
                    best = j
        return best

    def at(self, when: int, tolerance: int = MATCH_TOLERANCE_SEC) -> dict | None:
        i = self.index_at(when, tolerance)
        return self.legs[i] if i is not None else None

    def age_at(self, when: int, tolerance: int = MATCH_TOLERANCE_SEC) -> int | None:
        """Seconds between ``when`` and the snapshot used for it. This is the
        chain age a decision at ``when`` would really have been taken on."""
        i = self.index_at(when, tolerance)
        return None if i is None else when - self.ts[i]

    def premium(self, symbol: str, when: int,
               tolerance: int = MATCH_TOLERANCE_SEC) -> float | None:
        snap = self.at(when, tolerance)
        if not snap:
            return None
        leg = snap.get(symbol)
        if not leg:
            return None
        px = float(leg.get("premium") or 0.0)
        return px if px > 0 else None

    def forward(self, after: int, bars: int, symbol: str,
                step: int = 60) -> list[tuple[int, float]]:
        """``(ts, premium)`` for ``symbol`` from the snapshots strictly after
        ``after``, at most ``bars`` of them. Missing quotes are dropped, not
        interpolated — an absent leg is absent, not unchanged."""
        out: list[tuple[int, float]] = []
        k = bisect.bisect_right(self.ts, after)
        for j in range(k, min(len(self.ts), k + bars * 2)):
            leg = self.legs[j].get(symbol)
            if not leg:
                continue
            px = float(leg.get("premium") or 0.0)
            if px <= 0:
                continue
            out.append((self.ts[j], px))
            if len(out) >= bars:
                break
        return out


def connect(db_path: str) -> sqlite3.Connection:
    """Read-only connection that survives a live backend holding the write lock.

    Research runs while the app is up, and a long-running writer makes even a
    read-only connection fail with "database is locked". Rather than making the
    caller stop the app (or reading torn pages with ``immutable=1``), the file is
    copied once and read from the copy: a consistent snapshot, a few seconds old.
    """
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)
    try:
        db.execute("SELECT COUNT(*) FROM chain_snapshots LIMIT 1").fetchone()
        return db
    except sqlite3.OperationalError:
        db.close()
    tmp = os.path.join(tempfile.mkdtemp(prefix="qt-phase7-"),
                       os.path.basename(db_path))
    shutil.copy2(db_path, tmp)
    for suffix in ("-wal", "-shm"):
        if os.path.exists(db_path + suffix):
            shutil.copy2(db_path + suffix, tmp + suffix)
    return sqlite3.connect(f"file:{tmp}?mode=ro", uri=True, timeout=30.0)


def load_chains(db_path: str, instrument: str) -> dict[str, ChainSeries]:
    """All snapshots for ``instrument``, split by provenance. Duplicate
    timestamps are kept in the count but only the first is usable as a series."""
    series = {k: ChainSeries(instrument, k) for k in (REAL, SIM, UNKNOWN)}
    db = connect(db_path)
    try:
        rows = db.execute(
            "SELECT ts, payload FROM chain_snapshots WHERE instrument=? ORDER BY ts",
            (instrument,),
        ).fetchall()
    finally:
        db.close()
    for ts, payload in rows:
        kind = provenance(payload)
        s = series[kind]
        if kind == UNKNOWN:
            s.ts.append(int(ts))
            s.legs.append({})
            s.raw.append([])
            continue
        legs = json.loads(payload)
        s.ts.append(int(ts))
        s.legs.append({str(leg["symbol"]): leg for leg in legs})
        s.raw.append(legs)
    return series


def load_candles(db_path: str, instrument: str, limit: int = 0) -> list[Candle]:
    db = connect(db_path)
    try:
        sql = ("SELECT ts, open, high, low, close, volume FROM candles "
               "WHERE instrument=? ORDER BY ts")
        rows = db.execute(sql, (instrument,)).fetchall()
    finally:
        db.close()
    if limit:
        rows = rows[-limit:]
    return [Candle(time=int(r[0]), open=r[1], high=r[2], low=r[3], close=r[4],
                   volume=r[5]) for r in rows]


def instruments_with_chains(db_path: str) -> list[str]:
    db = connect(db_path)
    try:
        return [r[0] for r in db.execute(
            "SELECT instrument, COUNT(*) n FROM chain_snapshots GROUP BY instrument "
            "ORDER BY n DESC")]
    finally:
        db.close()


def session_of(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d")


def ist(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, IST).isoformat()
