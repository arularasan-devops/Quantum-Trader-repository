"""Feed profile recorder — what the feed cost and what it delivered, per session.

Pure observability. Nothing here is read by a decision, a gate or an order path,
and a failure is swallowed so a measurement can never break a live tick.

Why it exists: the case for a smaller deep watchlist is arithmetic — fewer names
means fewer rate-limited warm-up calls and fewer recorded chains — and arithmetic
is not evidence. Answering "did reducing the deep watchlist actually improve the
feed?" needs the same set of numbers measured on a session recorded BEFORE the
reduction and on one recorded AFTER it. Those numbers (tick cost, sweep coverage,
scan age, stale instrument counts, reconnects, REST pressure, process cost) exist
only in memory while the backend runs, so before this module the "after" could
never be compared with the "before".

Each record carries the deep/broad configuration that produced it, so a session
is self-labelling: the comparison never has to assume which setting was live.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import resource
import threading
import time

from app.config import settings
from app.market import tiers
from app.market.tick_quality import feed_quality

LOG_NAME = "feed_profiles.jsonl"

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

_lock = threading.Lock()
_last_write: float = 0.0
_START = time.time()


def log_path() -> str:
    return os.path.join(settings.data_dir, LOG_NAME)


def _rss_mb() -> float | None:
    """Peak resident set of this process, in MB, or None if unavailable."""
    try:
        raw = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None
    # Linux reports kilobytes, macOS bytes. Both are peak, not current, and are
    # reported as peak so a mid-session read is not mistaken for a live gauge.
    mb = raw / 1024.0 if raw < 1e8 else raw / (1024.0 * 1024.0)
    return round(mb, 1)


def _cpu_sec() -> float | None:
    try:
        ru = resource.getrusage(resource.RUSAGE_SELF)
        return round(float(ru.ru_utime + ru.ru_stime), 1)
    except Exception:
        return None


def snapshot(scan: dict | None = None, now: float | None = None) -> dict:
    """One profile record. ``scan`` is the tick loop's own metrics, passed in by
    the caller rather than imported, so this module stays free of any dependency
    on the application object."""
    now = time.time() if now is None else now
    cap = tiers.capacity(now)
    fq = feed_quality.summary(now)
    scan = dict(scan or {})
    ages = scan.get("ages") or {}
    age_vals = sorted(float(v) for v in ages.values())
    return {
        "ts": int(now),
        "session": dt.datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d"),
        "time_ist": dt.datetime.fromtimestamp(now, IST).strftime("%H:%M:%S"),
        # The configuration that produced every number below it.
        "split_enabled": cap["split_enabled"],
        "deep": cap["deep"],
        "deep_count": cap["deep_count"],
        "broad_count": cap["broad_count"],
        "active_count": cap["active_count"],
        "promoted": cap["promoted"],
        "warm_priority_names": cap["warm_priority_names"],
        "recorded_chain_writes_per_cycle": cap["recorded_chain_writes_per_cycle"],
        "quote_calls_per_cycle": cap["quote_calls_per_cycle"],
        # What the loop delivered.
        "tick_cost_ms": scan.get("tick_cost_ms"),
        "scanned_per_cycle": scan.get("scanned_per_cycle"),
        "sweep_estimate_sec": scan.get("sweep_estimate_sec"),
        "scan_target_sec": scan.get("scan_target_sec"),
        "median_scan_age_sec": scan.get("median_age_sec"),
        "worst_scan_age_sec": scan.get("worst_age_sec"),
        "p90_scan_age_sec": (round(age_vals[int(0.9 * (len(age_vals) - 1))], 1)
                             if age_vals else None),
        "instruments_behind": scan.get("behind_count"),
        "warm_total": scan.get("warm_total"),
        "warm_done": scan.get("warm_done"),
        "closed_skipped": scan.get("closed_skipped"),
        # Feed quality as the tick accounting measured it.
        "feed_instruments": fq.get("instruments"),
        "feed_fresh": fq.get("fresh"),
        "feed_aging": fq.get("aging"),
        "feed_stale": fq.get("stale"),
        "feed_dead": fq.get("dead"),
        "feed_no_data": fq.get("no_data"),
        # Process cost, so "fewer names" can be checked against real resource use.
        "cpu_sec_total": _cpu_sec(),
        "peak_rss_mb": _rss_mb(),
        "uptime_sec": int(now - _START),
    }


def record(scan: dict | None = None, now: float | None = None) -> dict | None:
    """Append a profile if the interval has elapsed. Returns what was written."""
    now = time.time() if now is None else now
    if not settings.feed_profile_log:
        return None
    interval = max(30.0, float(settings.feed_profile_interval_sec))
    with _lock:
        global _last_write
        if now - _last_write < interval:
            return None
        _last_write = now
    rec = snapshot(scan, now)
    try:
        os.makedirs(settings.data_dir, exist_ok=True)
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        return None
    return rec


def recent(limit: int = 50) -> list[dict]:
    """The tail of the profile log, oldest first. Empty when nothing is recorded."""
    try:
        with open(log_path(), encoding="utf-8") as fh:
            lines = fh.readlines()[-max(1, limit):]
    except OSError:
        return []
    out: list[dict] = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
