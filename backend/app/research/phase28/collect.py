"""Phase 28 §2 — fetch daily history so the cash-equity half is answerable.

Without this the stock question cannot be asked at all: ``data/backtest`` holds
five-year 1-minute files for NIFTY and CRUDEOIL only, and every equity name comes
back ``INSUFFICIENT_HISTORY``. That is a data gap, not a strategy result, and the
fix is to fetch the daily series rather than to reason about it.

Three rules, borrowed from the Phase 14 collector because they were learned the
hard way:

* **the fetcher is injected.** The network call lives behind a callable, so this
  module is testable without credentials and can never quietly fall back to the
  simulated feed;
* **credentials come from the environment only.** Nothing is accepted on the
  command line, nothing is written to the artefacts, and nothing is logged;
* **a window that fails is reported, not raised.** One exhausted window must not
  discard the windows that already landed, and the summary says exactly which
  dates are missing.

The output is ``data/backtest/<INST>_ONE_DAY.jsonl``, which ``dailybars.load``
prefers over aggregating minutes. Rows are deduplicated on timestamp and written
in chronological order; an existing file is merged with rather than truncated, so
a re-run extends the history instead of starting over.

Honest limitation, carried into the report: the provider returns its own adjusted
daily series. Splits, bonuses and dividends are therefore only as adjusted as the
provider makes them, and delisted names are absent, so a study over a fixed
watchlist carries survivorship bias. Neither is corrected for here; both are
stated.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from collections.abc import Callable

from app.market.instruments import get_spec
from app.research.phase14 import tokens
from app.research.phase28 import dailybars

ONE_DAY = "ONE_DAY"
CHUNK_DAYS = 200          # daily candles are small; the limit is calls, not rows
MIN_INTERVAL_SEC = 3.5    # stay polite even when nothing else is running
_FMT = "%Y-%m-%d %H:%M"
# The provider stamps its rows "2024-01-02T00:00:00+05:30". If an offset is ever
# missing, IST is assumed explicitly rather than falling back to whatever this
# machine's clock is set to — a UTC box would file every bar on the wrong session.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# (exchange, token, interval, from, to) -> provider candle rows
Fetcher = Callable[[str, str, str, dt.datetime, dt.datetime], list[list]]


def windows(start: dt.date, end: dt.date) -> list[tuple[dt.datetime, dt.datetime]]:
    """Fixed calendar windows, so a resumed run cannot leave a seam."""
    out: list[tuple[dt.datetime, dt.datetime]] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + dt.timedelta(days=CHUNK_DAYS - 1), end)
        out.append((
            dt.datetime.combine(cursor, dt.time(9, 0)),
            dt.datetime.combine(stop, dt.time(15, 45)),
        ))
        cursor = stop + dt.timedelta(days=1)
    return out


def _parse_row(row: list) -> dict | None:
    """One provider row as a candle, or ``None`` if it is not usable."""
    try:
        stamped = dt.datetime.fromisoformat(str(row[0]))
        if stamped.tzinfo is None:
            stamped = stamped.replace(tzinfo=IST)
        ts = int(stamped.timestamp())
        return {
            # "time" is the key ``dailybars`` reads; a row keyed anything else
            # would be silently skipped and the file would look empty.
            "time": ts,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]) if len(row) > 5 else 0.0,
        }
    except (TypeError, ValueError, IndexError):
        return None


def path_for(instrument: str, out_dir: str | None = None) -> str:
    d = out_dir or dailybars.backtest_dir()
    return os.path.join(d, f"{instrument.upper()}_{ONE_DAY}.jsonl")


def _existing(path: str) -> dict[int, dict]:
    if not os.path.exists(path):
        return {}
    rows: dict[int, dict] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("time") is not None:
                rows[int(row["time"])] = row
    return rows


def _write(path: str, rows: dict[int, dict]) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for ts in sorted(rows):
            fh.write(json.dumps(rows[ts]) + "\n")
    return len(rows)


def scrip_master() -> list[dict]:
    """The Angel scrip master, fetched once through the provider's own cache.

    The cache is reused rather than downloading the file again, which is also how
    the provider itself resolves tokens; the master is a public file and carries no
    credentials.
    """
    from app.market.angelone import _get_scrip_master

    return [row for row in _get_scrip_master() if isinstance(row, dict)]


def angel_fetcher() -> Fetcher:
    """The real fetcher, logged in from the environment.

    Credentials are read by the provider from environment variables; none are
    accepted as arguments, printed, or persisted.
    """
    from app.config import settings
    from app.market.angelone import AngelOneProvider

    if settings.data_provider.lower() != "angelone":
        raise RuntimeError(
            "daily history collection requires QT_DATA_PROVIDER=angelone and "
            "SmartAPI credentials in the environment; refusing to run against "
            "the simulated feed"
        )
    sessions: dict[str, object] = {}

    def fetch(
        exchange: str, token: str, interval: str,
        frm: dt.datetime, to: dt.datetime,
    ) -> list[list]:
        smart = sessions.get("smart")
        if smart is None:
            smart = AngelOneProvider(get_spec("NIFTY"))._smart
            sessions["smart"] = smart
        resp = smart.getCandleData({
            "exchange": exchange,
            "symboltoken": token,
            "interval": interval,
            "fromdate": frm.strftime(_FMT),
            "todate": to.strftime(_FMT),
        })
        return list((resp or {}).get("data") or [])

    return fetch


def collect(
    instrument: str,
    *,
    years: float = 5.0,
    fetch: Fetcher | None = None,
    out_dir: str | None = None,
    master: list[dict] | None = None,
    min_interval_sec: float = MIN_INTERVAL_SEC,
    sleep: Callable[[float], None] = time.sleep,
    today: dt.date | None = None,
) -> dict:
    """Fetch ``years`` of daily bars for one instrument and merge them to disk."""
    inst = instrument.upper()
    fetch = fetch or angel_fetcher()
    series = tokens.resolve(inst, master if master is not None else scrip_master())
    end = today or dt.date.today()
    start = end - dt.timedelta(days=int(365.25 * years) + 1)
    path = path_for(inst, out_dir)
    rows = _existing(path)
    before = len(rows)
    fetched = 0
    failed: list[str] = []
    for frm, to in windows(start, end):
        try:
            raw = fetch(series.exchange, series.token, ONE_DAY, frm, to)
        except Exception as exc:  # one window must not lose the others
            failed.append(f"{frm.date().isoformat()}: {exc}")
            continue
        for row in raw:
            parsed = _parse_row(row)
            if parsed is not None:
                rows[parsed["time"]] = parsed
                fetched += 1
        sleep(min_interval_sec)
    written = _write(path, rows)
    return {
        "instrument": inst,
        "path": path,
        "interval": ONE_DAY,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "rows_fetched": fetched,
        "rows_on_disk_before": before,
        "rows_on_disk_after": written,
        "windows_failed": failed,
        "note": (
            "provider-adjusted daily bars; splits/bonuses/dividends are only as "
            "adjusted as the provider makes them and delisted names are absent, "
            "so a fixed watchlist carries survivorship bias. Neither is "
            "corrected for"
        ),
    }


__all__ = ["collect", "windows", "path_for", "angel_fetcher", "ONE_DAY", "Fetcher"]
