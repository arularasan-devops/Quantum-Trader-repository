"""Collect the universe's 1-minute history, then hand it to research.

Collection reuses the mcxhist collector and store verbatim — calendar-aligned
windows, bounded backoff, rate-limit waits, failures recorded rather than
raised, resume by skipping windows already stored. Nothing about that machinery
is MCX-specific; only the token resolution was, and that lives in
``universe.py``.

The handoff is the part worth explaining. Phase 53 and Phase 54 read a series
through ``phase24.data.load_series``, which knows two shipped files and
otherwise defers to the historical-import adapter. So rather than edit a Phase
24 constant — which would change the data source of every earlier study at
once — this module writes each collected instrument to a CSV and imports it
through that existing layer. The adapter already refuses to shadow a shipped
file, so NIFTY and CRUDEOIL keep the exact series Phases 51–54 measured, and the
eighteen new names arrive by the door the import layer was built to be.
"""
from __future__ import annotations

import csv
import datetime as dt
import os

from app.research.mcxhist import collector as mcxcollector
from app.research.mcxhist.store import Store
from app.research.phase55 import (
    ALREADY_SHIPPED,
    CHUNK_DAYS,
    ONE_MINUTE,
    UNIVERSE,
)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

SOURCE = "angel_smartapi_getCandleData (phase55 universe collection)"


def default_db(data_dir: str = "data") -> str:
    return os.path.join(data_dir, "phase55hist.db")


def collect_instrument(
    fetch,
    store: Store,
    resolution: dict,
    *,
    start: dt.date,
    end: dt.date,
    interval: str = ONE_MINUTE,
    chunk_days: int = CHUNK_DAYS,
    progress=None,
) -> dict:
    """Pull one resolved instrument. Resumable; a failed window is recorded."""
    instrument = resolution["instrument"]
    if resolution.get("status") != "RESOLVED" or not resolution.get("token"):
        return {
            "instrument": instrument,
            "status": "SKIPPED_UNRESOLVED",
            "reason": resolution.get("basis"),
        }
    summary = mcxcollector.collect(
        fetch,
        store,
        root=instrument,
        exchange=resolution["exchange"],
        token=resolution["token"],
        trading_symbol=resolution.get("trading_symbol"),
        expiry=resolution.get("expiry"),
        start=start,
        end=end,
        interval=interval,
        chunk_days=chunk_days,
        progress=progress,
        source=SOURCE,
    )
    summary["instrument"] = instrument
    summary["status"] = "COLLECTED"
    summary["series_class"] = resolution.get("series_class")
    return summary


def write_csv(store: Store, instrument: str, out_dir: str) -> dict:
    """One instrument's stored bars as a CSV the import layer can read.

    Refuses to write when the store holds more than one token for the
    instrument: concatenating two tokens is exactly the silent stitch this
    project has spent two stages refusing to do.
    """
    tokens = store.tokens(instrument)
    if len(tokens) != 1:
        return {
            "instrument": instrument,
            "status": "REFUSED_MULTIPLE_TOKENS",
            "tokens": tokens,
            "reason": (
                f"{len(tokens)} tokens stored; exporting would concatenate "
                "series that were never one series"
            ),
        }
    rows = store.bars(instrument, tokens[0])
    if not rows:
        return {"instrument": instrument, "status": "NO_BARS", "tokens": tokens}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{instrument}_ONE_MINUTE.csv")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "time", "open", "high", "low", "close", "volume"])
        for ts, open_, high, low, close, volume in rows:
            stamp = dt.datetime.fromtimestamp(int(ts), IST).isoformat()
            writer.writerow([instrument, stamp, open_, high, low, close, volume])
    return {
        "instrument": instrument,
        "status": "WRITTEN",
        "path": path,
        "rows": len(rows),
        "token": tokens[0],
    }


def register(instrument: str, csv_path: str, exchange: str) -> dict:
    """Import one CSV through the historical-import layer. Idempotent on bytes."""
    if instrument.upper() in ALREADY_SHIPPED:
        return {
            "instrument": instrument,
            "status": "SHIPPED_FILE_WINS",
            "reason": (
                "the repository ships this five-year series; an import must "
                "not shadow the data earlier phases were measured on"
            ),
        }
    from app.research.historical_import import ingest

    result = ingest.import_file(
        csv_path,
        source=SOURCE,
        tz="Asia/Kolkata",
        timeframe=1,
        instrument=instrument,
        exchange=exchange,
    )
    return {"instrument": instrument, **result}


def plan(instruments: list[str] | None = None) -> list[str]:
    """Declared order of work: the universe, shipped names excluded."""
    names = [n.upper() for n in (instruments or list(UNIVERSE))]
    return [n for n in names if n in UNIVERSE and n not in ALREADY_SHIPPED]
