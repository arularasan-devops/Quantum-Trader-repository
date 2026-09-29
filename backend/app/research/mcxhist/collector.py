"""Resumable, throttled collection of MCX futures history. RESEARCH ONLY.

The rules are the ones the Phase 14 collector learned the hard way, restated
here rather than imported, because that module fetches *cash* series and refuses
MCX names by design — changing it to serve commodities would alter behaviour the
rest of the project depends on.

* **windows are fixed by the calendar, not by the run.** Boundaries come from the
  declared start date and ``CHUNK_DAYS``, so the same window always has the same
  key and a resumed run cannot leave a seam between two differently aligned
  passes;
* **a rate limit is waited out, not surrendered to.** AB1021 and its several
  dialects get an exponential backoff before the window is written off;
* **a failure is recorded, not raised.** One exhausted window must not abandon
  the rest of the span. The reason is stored against that window and the run
  continues;
* **the fetcher is injected.** No credential is read here and no network call is
  made here, so the whole collector is testable and cannot fall back to the
  simulated feed.

Nothing in this module interprets a price. It moves rows from the provider into
the store and reports what moved.
"""
from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable

from app.research.mcxhist import (
    BACKOFF_START_SEC,
    CHUNK_DAYS,
    MAX_ATTEMPTS,
    MIN_INTERVAL_SEC,
    ONE_MINUTE,
    fingerprint,
)
from app.research.mcxhist.contracts import Fetcher
from app.research.mcxhist.store import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_OK,
    Store,
)

Sleeper = Callable[[float], None]
Progress = Callable[[str], None]

_RATE_LIMIT_MARKERS = (
    "ab1021",
    "too many",
    "exceeding access rate",
    "access denied",
    "parse the json",
    "couldn't parse",
    "rate limit",
)


def is_rate_limited(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _RATE_LIMIT_MARKERS)


def windows(start: dt.date, end: dt.date, chunk_days: int = CHUNK_DAYS) -> list[tuple[dt.date, dt.date]]:
    """Calendar-aligned request windows over ``[start, end]``."""
    out: list[tuple[dt.date, dt.date]] = []
    cursor = start
    step = dt.timedelta(days=max(1, chunk_days) - 1)
    while cursor <= end:
        window_end = min(cursor + step, end)
        out.append((cursor, window_end))
        cursor = window_end + dt.timedelta(days=1)
    return out


def collect(
    fetch: Fetcher,
    store: Store,
    *,
    root: str,
    exchange: str,
    token: str,
    trading_symbol: str | None,
    expiry: str | None,
    start: dt.date,
    end: dt.date,
    interval: str = ONE_MINUTE,
    chunk_days: int = CHUNK_DAYS,
    sleep: Sleeper = time.sleep,
    min_interval_sec: float = MIN_INTERVAL_SEC,
    max_attempts: int = MAX_ATTEMPTS,
    backoff_start_sec: float = BACKOFF_START_SEC,
    progress: Progress | None = None,
    source: str = "angel_smartapi_getCandleData",
) -> dict:
    """Pull ``[start, end]`` for one token, skipping windows already stored."""
    store.record_provenance(
        root,
        token,
        interval,
        exchange=exchange,
        trading_symbol=trading_symbol,
        expiry=expiry,
        source=source,
        fingerprint=fingerprint(),
    )
    already = store.done_chunks(root, token, interval)
    planned = windows(start, end, chunk_days)
    summary = {
        "root": root,
        "token": token,
        "interval": interval,
        "windows_planned": len(planned),
        "windows_skipped_already_stored": 0,
        "windows_ok": 0,
        "windows_empty": 0,
        "windows_failed": 0,
        "bars_inserted": 0,
        "rows_rejected": 0,
        "rate_limit_waits": 0,
    }
    last_call = 0.0
    for window_start, window_end in planned:
        key = window_start.isoformat()
        if key in already:
            summary["windows_skipped_already_stored"] += 1
            continue

        rows: list | None = None
        reason: str | None = None
        backoff = backoff_start_sec
        for attempt in range(max_attempts):
            gap = min_interval_sec - (time.time() - last_call)
            if gap > 0:
                sleep(gap)
            try:
                rows = fetch(exchange, token, interval, window_start, window_end)
                last_call = time.time()
                break
            except Exception as exc:
                last_call = time.time()
                reason = f"{type(exc).__name__}: {exc}"[:300]
                if is_rate_limited(str(exc)) and attempt < max_attempts - 1:
                    summary["rate_limit_waits"] += 1
                    sleep(backoff)
                    backoff *= 2
                    continue
                rows = None
                break

        if rows is None:
            store.mark_chunk(
                root, token, interval, key, window_end.isoformat(),
                STATUS_FAILED, 0, reason,
            )
            summary["windows_failed"] += 1
            if progress:
                progress(f"  {key}..{window_end}: FAILED {reason}")
            continue

        if not rows:
            store.mark_chunk(
                root, token, interval, key, window_end.isoformat(),
                STATUS_EMPTY, 0, "provider returned no rows",
            )
            summary["windows_empty"] += 1
            if progress:
                progress(f"  {key}..{window_end}: empty")
            continue

        accepted = store.insert_bars(root, token, rows)
        summary["rows_rejected"] += max(0, len(rows) - accepted)
        summary["bars_inserted"] += accepted
        summary["windows_ok"] += 1
        store.mark_chunk(
            root, token, interval, key, window_end.isoformat(),
            STATUS_OK, accepted, None,
        )
        if progress:
            progress(
                f"  {key}..{window_end}: +{accepted} bars "
                f"(total {store.bar_count(root, token)})"
            )

    summary["chunk_stats"] = store.chunk_stats(root, token, interval)
    summary["bars_stored_total"] = store.bar_count(root, token)
    return summary


def angel_fetcher(smart) -> Fetcher:
    """Adapt a logged-in SmartConnect client to the injected fetcher signature.

    The provider is asked for ``09:00`` to ``23:59`` IST so the whole MCX evening
    cycle is inside the window. Its refusal is raised, not swallowed, so the
    collector's backoff can see it.
    """

    def fetch(
        exchange: str, token: str, interval: str, start: dt.date, end: dt.date
    ) -> list[list]:
        params = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": interval,
            "fromdate": f"{start.isoformat()} 09:00",
            "todate": f"{end.isoformat()} 23:59",
        }
        response = smart.getCandleData(params)
        message = str((response or {}).get("message") or "")
        if is_rate_limited(message):
            raise RuntimeError(f"provider rate limit: {message}"[:200])
        return (response or {}).get("data") or []

    return fetch
