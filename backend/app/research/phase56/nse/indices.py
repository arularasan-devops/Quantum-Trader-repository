"""§17 benchmark series, from NSE's own dated index-close files.

``/content/indices/ind_close_all_<ddmmyyyy>.csv`` carries one row per published
index for that session::

    Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,
    Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield
    Nifty 50,13-07-2023,19495.2,19567,19385.8,19413.75,29.45,.15,...

This is a benchmark and a market-context series only. It is deliberately *not*
an index-membership source: the file publishes the index's price, never its
constituents, so it does nothing for the `HISTORICAL_INDEX_MEMBERSHIP` gap and
is not allowed to imply otherwise.

Two indices are kept, both named in the pre-registration before any measurement:
``NIFTY 50`` (the §17 primary benchmark) and ``NIFTY 500`` (the broad benchmark,
and the market-context series a relative-strength feature is measured against).
A session whose file is absent is recorded as absent; no value is carried
forward, because a flat benchmark day that never happened is a fabricated
return.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .archive import Archive
from .bhav import sanitize

NIFTY_50 = "NIFTY 50"
NIFTY_500 = "NIFTY 500"
#: Indices retained, keyed by the normalised name used everywhere downstream.
KEPT_INDICES = (NIFTY_50, NIFTY_500)

INDEX_FIELDS = ("trade_date", "index_name", "open", "high", "low", "close", "turnover_inr")


def index_paths(day: date) -> list[str]:
    return [f"/content/indices/ind_close_all_{day.strftime('%d%m%Y')}.csv"]


@dataclass(frozen=True)
class IndexRow:
    trade_date: str
    index_name: str
    open: float
    high: float
    low: float
    close: float
    turnover_inr: float

    def as_dict(self) -> dict:
        return {
            "trade_date": self.trade_date,
            "index_name": self.index_name,
            "open": f"{self.open:.4f}",
            "high": f"{self.high:.4f}",
            "low": f"{self.low:.4f}",
            "close": f"{self.close:.4f}",
            "turnover_inr": f"{self.turnover_inr:.2f}",
        }


def _number(text: str) -> float:
    cleaned = (text or "").strip().replace(",", "")
    if cleaned in ("", "-", "NA", "na"):
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_index_csv(text: str, day: date) -> list[IndexRow]:
    """The kept indices for one session, or [] if the file names none of them."""
    out: list[IndexRow] = []
    for raw in csv.DictReader(sanitize(text).splitlines()):
        name = (raw.get("Index Name") or "").strip().upper()
        if name not in KEPT_INDICES:
            continue
        close = _number(raw.get("Closing Index Value", ""))
        if close <= 0:
            continue
        out.append(
            IndexRow(
                trade_date=day.isoformat(),
                index_name=name,
                open=_number(raw.get("Open Index Value", "")) or close,
                high=_number(raw.get("High Index Value", "")) or close,
                low=_number(raw.get("Low Index Value", "")) or close,
                close=close,
                # Published in crore; stored in rupees like every other turnover.
                turnover_inr=_number(raw.get("Turnover (Rs. Cr.)", "")) * 1_00_00_000.0,
            )
        )
    return out


class IndexStore:
    """One append-only CSV of index closes beside the raw equity store."""

    def __init__(self, root: Path):
        self.path = Path(root) / "indices" / "index_close.csv"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> dict[str, dict[str, IndexRow]]:
        """``index name -> {session -> row}``."""
        out: dict[str, dict[str, IndexRow]] = {}
        if not self.path.exists():
            return out
        with self.path.open(newline="") as handle:
            for raw in csv.DictReader(handle):
                row = IndexRow(
                    trade_date=raw["trade_date"],
                    index_name=raw["index_name"],
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                    turnover_inr=float(raw["turnover_inr"] or 0.0),
                )
                out.setdefault(row.index_name, {})[row.trade_date] = row
        return out

    def write(self, rows: list[IndexRow]) -> int:
        """Rewrite from the full set. Derived from cached files, so rebuildable."""
        ordered = sorted(rows, key=lambda row: (row.trade_date, row.index_name))
        with self.path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(INDEX_FIELDS))
            writer.writeheader()
            for row in ordered:
                writer.writerow(row.as_dict())
        return len(ordered)


def ingest_indices(days: list[date], archive: Archive, store: IndexStore) -> dict:
    """Fetch and store the kept index closes for ``days``.

    Returns per-status counts. ``UNAVAILABLE`` is transport failure and stays
    unfinished — it is never folded into "this session has no benchmark".
    """
    existing = store.read()
    have = {
        day
        for day in {stamp for series in existing.values() for stamp in series}
    }
    rows = [row for series in existing.values() for row in series.values()]
    counts = {"CACHED_OR_STORED": 0, "FETCHED": 0, "NOT_PUBLISHED": 0, "UNAVAILABLE": 0, "NO_KEPT_INDEX": 0}
    for day in days:
        if day.isoformat() in have:
            counts["CACHED_OR_STORED"] += 1
            continue
        result = archive.fetch(index_paths(day), f"idx_{day.isoformat()}.csv")
        if not result.ok:
            counts[result.status] = counts.get(result.status, 0) + 1
            continue
        parsed = parse_index_csv(result.path.read_text(errors="replace"), day)
        if not parsed:
            counts["NO_KEPT_INDEX"] += 1
            continue
        rows.extend(parsed)
        counts["FETCHED"] += 1
    counts["stored_rows"] = store.write(rows)
    return counts
