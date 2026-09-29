"""Resumable, throttled collection of underlying history. RESEARCH ONLY.

The existing Phase 2 downloader fetches one instrument in one pass and starts
over if it dies. Multi-year 1-minute history across a watchlist cannot work that
way: Angel's historical endpoint is rate-limited (``AB1021``), a run takes hours,
and anything that long gets interrupted. So collection here is a sequence of
dated windows, each recorded when it lands, and a resumed run asks only for the
windows that are still missing.

Three rules the caller cannot switch off:

* **the window is fixed by the calendar, not by the run.** Chunk boundaries are
  derived from ``start`` and the chunk size, so the same window always has the
  same key and a resumed run cannot leave a seam between two differently-aligned
  passes;
* **a rate limit is waited out, not surrendered to.** The provider answers a
  burst with ``Access denied because of exceeding access rate`` or drops the
  connection, and a five-year pull hits that repeatedly; each window is retried
  with an exponential backoff before it is written off, which is the difference
  between one pass and the user re-running the collector all afternoon;
* **a failure is recorded, not raised.** One exhausted window must not abandon
  the other nineteen instruments, so the reason is stored against that window and
  the run continues; the summary reports what is still missing;
* **the fetcher is injected.** The network call lives behind a callable, so this
  module is testable without credentials and cannot quietly fall back to the
  simulated feed.
"""
from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable

from app.research.phase14 import tokens
from app.research.phase14.spot_store import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_OK,
    SpotStore,
)

ONE_MINUTE = "ONE_MINUTE"
FIVE_MINUTE = "FIVE_MINUTE"

# Angel serves ONE_MINUTE history in windows of at most a month; 25 days keeps a
# margin and matches the Phase 2 downloader so both agree on what a chunk is.
CHUNK_DAYS = {ONE_MINUTE: 25, FIVE_MINUTE: 90}

# Minimum seconds between historical calls. The provider's own limiter is
# per-key; this one exists so a long collection stays polite even when it is the
# only thing running.
MIN_INTERVAL_SEC = 3.5

# Attempts per window, and the first backoff. 20s doubling gives 20/40/80 —
# roughly two and a half minutes of patience on a window that would otherwise be
# recorded as a hole in the history for the user to re-run.
MAX_ATTEMPTS = 4
BACKOFF_START_SEC = 20.0

# The provider says "slow down" in several dialects, and a dropped TLS connection
# under load reads as a timeout. Anything else (a bad token, a refused segment) is
# permanent for this window and retrying it only wastes the rate budget.
_RETRYABLE = (
    "exceeding access rate",
    "access denied",
    "ab1021",
    "too many requests",
    "timed out",
    "timeout",
    "max retries exceeded",
    "connection aborted",
    "connection reset",
)

_FMT = "%Y-%m-%d %H:%M"

# A fetcher takes (exchange, token, interval, from, to) and returns candle dicts.
Fetcher = Callable[[str, str, str, dt.datetime, dt.datetime], list[dict]]


def chunk_windows(start: dt.date, end: dt.date,
                  interval: str) -> list[tuple[dt.datetime, dt.datetime]]:
    """The fixed calendar windows covering ``start``..``end`` for ``interval``."""
    span = CHUNK_DAYS.get(interval, CHUNK_DAYS[ONE_MINUTE])
    out: list[tuple[dt.datetime, dt.datetime]] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + dt.timedelta(days=span - 1), end)
        out.append((
            dt.datetime.combine(cursor, dt.time(9, 0)),
            dt.datetime.combine(stop, dt.time(15, 45)),
        ))
        cursor = stop + dt.timedelta(days=1)
    return out


def is_retryable(exc: Exception) -> bool:
    """Whether waiting could plausibly make this window succeed."""
    text = str(exc).lower()
    return any(marker in text for marker in _RETRYABLE)


def collect_series(series: tokens.Series, *, start: dt.date, end: dt.date,
                   fetch: Fetcher, st: SpotStore,
                   interval: str = ONE_MINUTE,
                   min_interval_sec: float = MIN_INTERVAL_SEC,
                   max_attempts: int = MAX_ATTEMPTS,
                   backoff_start_sec: float = BACKOFF_START_SEC,
                   sleep: Callable[[float], None] = time.sleep) -> dict:
    """Collect one instrument's history, skipping windows already stored."""
    done = st.done_chunks(series.instrument, interval)
    windows = chunk_windows(start, end, interval)
    written = 0
    fetched_windows = 0
    skipped = 0
    retried = 0
    failed: list[str] = []
    for frm, to in windows:
        key = frm.date().isoformat()
        if key in done:
            skipped += 1
            continue
        if fetched_windows:
            sleep(min_interval_sec)
        rows: list[dict] | None = None
        last_error: Exception | None = None
        for attempt in range(1, max(1, max_attempts) + 1):
            try:
                rows = fetch(series.exchange, series.token, interval, frm, to)
                break
            except Exception as exc:
                last_error = exc
                if attempt >= max(1, max_attempts) or not is_retryable(exc):
                    break
                sleep(backoff_start_sec * (2 ** (attempt - 1)))
                retried += 1
        fetched_windows += 1
        now = int(time.time())
        if rows is None:
            st.mark_chunk(series.instrument, key, to.date().isoformat(), interval,
                          0, STATUS_FAILED, str(last_error)[:400], now)
            failed.append(key)
            continue
        n = st.insert_candles(series.instrument, rows)
        written += n
        st.mark_chunk(series.instrument, key, to.date().isoformat(), interval, n,
                      STATUS_OK if n else STATUS_EMPTY, None, now)
    cov = st.coverage(series.instrument)
    return {
        "instrument": series.instrument,
        "kind": series.kind,
        "exchange": series.exchange,
        "symbol": series.symbol,
        "interval": interval,
        "windows_total": len(windows),
        "windows_fetched": fetched_windows,
        "windows_skipped": skipped,
        "windows_retried": retried,
        "windows_failed": failed,
        "rows_written": written,
        "rows_stored": cov["rows"],
        "note": series.note,
    }


def collect(instruments: list[str], *, start: dt.date, end: dt.date,
            master: list[dict], fetch: Fetcher, st: SpotStore,
            interval: str = ONE_MINUTE,
            min_interval_sec: float = MIN_INTERVAL_SEC,
            max_attempts: int = MAX_ATTEMPTS,
            backoff_start_sec: float = BACKOFF_START_SEC,
            sleep: Callable[[float], None] = time.sleep) -> dict:
    """Collect a watchlist. Instruments with no cash series are reported, not hidden."""
    series, skipped = tokens.resolve_many(instruments, master)
    results = [
        collect_series(s, start=start, end=end, fetch=fetch, st=st,
                       interval=interval, min_interval_sec=min_interval_sec,
                       max_attempts=max_attempts,
                       backoff_start_sec=backoff_start_sec, sleep=sleep)
        for s in series
    ]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "interval": interval,
        "backend": st.backend,
        "instruments_collected": len(results),
        "instruments_unavailable": skipped,
        "rows_written": sum(r["rows_written"] for r in results),
        "windows_retried": sum(r["windows_retried"] for r in results),
        "windows_failed": {r["instrument"]: r["windows_failed"]
                           for r in results if r["windows_failed"]},
        "series": results,
    }


def angel_fetcher(smart, parse_ts: Callable[[str], int]) -> Fetcher:
    """A :data:`Fetcher` over a logged-in SmartAPI client.

    Kept separate from :func:`collect` so the collection logic never holds a
    session, and so a run against the simulated feed is impossible by construction
    rather than by a flag.
    """

    def fetch(exchange: str, token: str, interval: str,
              frm: dt.datetime, to: dt.datetime) -> list[dict]:
        resp = smart.getCandleData({
            "exchange": exchange,
            "symboltoken": token,
            "interval": interval,
            "fromdate": frm.strftime(_FMT),
            "todate": to.strftime(_FMT),
        })
        payload = (resp or {}).get("data") or []
        if not payload and not (resp or {}).get("status", True):
            raise RuntimeError(str((resp or {}).get("message") or "empty response"))
        return [
            {"ts": parse_ts(row[0]), "open": float(row[1]), "high": float(row[2]),
             "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])}
            for row in payload
        ]

    return fetch
