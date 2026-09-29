"""Futures feed audit — DIAGNOSTICS ONLY (Phase 12A §1–§2).

Why this exists
---------------
On 26 Aug the futures research engine refused **1,321** times with
``STALE_FEED`` while the option feed on the same session was healthy, and the
only number available was a single feed age. A single age cannot say which hop
produced it, so the cause would have had to be guessed. This module records the
whole path instead, per contract, per observation:

    broker subscription → token → websocket → REST fallback → cache
    → timestamp → processing → futures signal

and labels the resulting age ``FRESH / AGING / STALE / DEAD / NO_DATA``.

What it does NOT do
-------------------
Nothing here is a gate. The futures research engine keeps its own 90-second
freshness refusal, and this module neither feeds it nor relaxes it — the bands
below exist so the audit can describe an age, not so an age can be excused.
There is no order path, paper or live, anywhere in this file.
"""
from __future__ import annotations

import json
import os
import statistics
import threading
import time

from app.config import settings

# freshness states (§1)
FRESH = "FRESH"
AGING = "AGING"
STALE = "STALE"
DEAD = "DEAD"
NO_DATA = "NO_DATA"
STATES = (FRESH, AGING, STALE, DEAD, NO_DATA)

# tick sources (§1)
WS = "WS"
REST = "REST"
NONE = "NONE"

REPORT_JSON = "futures_feed_audit.json"
LEDGER = "futures_feed.jsonl"

# One sample per instrument per this many seconds. The audit answers "how old
# were the bars across the session", which a per-tick sample would not answer
# any better while writing tens of thousands of rows.
SAMPLE_INTERVAL_SEC = 60.0

_LOCK = threading.Lock()
_latest: dict[str, dict] = {}
_samples: dict[str, list[dict]] = {}
_last_sample_at: dict[str, float] = {}


def state(age_sec: float | None) -> str:
    """Label a feed age. Diagnostics only — no gate reads this."""
    if age_sec is None:
        return NO_DATA
    if age_sec <= settings.futures_feed_aging_sec:
        return FRESH
    if age_sec <= settings.futures_feed_stale_sec:
        return AGING
    if age_sec <= settings.futures_feed_dead_sec:
        return STALE
    return DEAD


def _num(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def observe(instrument: str, trace: dict | None, now: float | None = None) -> dict | None:
    """Record one observation of an instrument's futures feed path.

    Returns the stored row, or None when the sample was skipped (rate-limited to
    one per ``SAMPLE_INTERVAL_SEC``) or the audit is switched off. Never raises
    into the caller's tick: the audit must not be able to break the scan.
    """
    if not settings.futures_feed_audit or not instrument or not trace:
        return None
    now = time.time() if now is None else now
    age = _num(trace.get("age_seconds"))
    row = dict(trace)
    row["instrument"] = instrument
    row["freshness"] = state(age)
    row["ts"] = int(now)
    # Which side of the §3 fix produced this row, so before/after is read off
    # the ledger rather than remembered.
    row["ws_bars_enabled"] = bool(settings.futures_ws_bars)
    with _LOCK:
        _latest[instrument] = row
        last = _last_sample_at.get(instrument, 0.0)
        if now - last < SAMPLE_INTERVAL_SEC:
            return None
        _last_sample_at[instrument] = now
        _samples.setdefault(instrument, []).append(row)
    _append_ledger(row)
    return row


def _append_ledger(row: dict) -> None:
    if not settings.futures_feed_audit:
        return
    try:
        os.makedirs(settings.data_dir, exist_ok=True)
        with open(os.path.join(settings.data_dir, LEDGER), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except OSError:
        # A full or read-only data dir must not take the scan down with it.
        return


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return round(ordered[idx], 1)


def _age_stats(rows: list[dict]) -> dict:
    ages = [a for a in (_num(r.get("age_seconds")) for r in rows) if a is not None]
    states = {s: 0 for s in STATES}
    for r in rows:
        states[str(r.get("freshness") or NO_DATA)] = (
            states.get(str(r.get("freshness") or NO_DATA), 0) + 1
        )
    sources: dict[str, int] = {}
    for r in rows:
        src = str(r.get("source") or NONE)
        sources[src] = sources.get(src, 0) + 1
    fresh_like = states[FRESH]
    stale_like = states[STALE] + states[DEAD]
    return {
        "samples": len(rows),
        "median_age_sec": round(statistics.median(ages), 1) if ages else None,
        "p75_age_sec": _pct(ages, 0.75),
        "p95_age_sec": _pct(ages, 0.95),
        "max_age_sec": round(max(ages), 1) if ages else None,
        "fresh_pct": round(100.0 * fresh_like / len(rows), 1) if rows else None,
        "stale_pct": round(100.0 * stale_like / len(rows), 1) if rows else None,
        "no_data_pct": round(100.0 * states[NO_DATA] / len(rows), 1) if rows else None,
        "states": states,
        "sources": sources,
    }


def _throughput(rows: list[dict]) -> dict:
    """Session throughput from the first and last sample of a run."""
    if len(rows) < 2:
        return {"ticks_per_sec": None, "rest_polls": None, "rest_deferred": None,
                "rate_limit_errors": None, "window_sec": None}
    first, last = rows[0], rows[-1]
    window = max(1.0, float(int(last.get("ts") or 0) - int(first.get("ts") or 0)))
    ticks = (_num(last.get("ticks")) or 0.0) - (_num(first.get("ticks")) or 0.0)
    polls = (_num(last.get("rest_attempts")) or 0.0) - (_num(first.get("rest_attempts")) or 0.0)
    deferred = (
        (_num(last.get("rest_deferred")) or 0.0) - (_num(first.get("rest_deferred")) or 0.0)
    )
    budget_last = last.get("hist_budget") or {}
    budget_first = first.get("hist_budget") or {}
    limits = (_num(budget_last.get("rate_limits")) or 0.0) - (
        _num(budget_first.get("rate_limits")) or 0.0)
    return {
        "ticks_per_sec": round(ticks / window, 3),
        "rest_polls": int(polls),
        "rest_deferred": int(deferred),
        "rate_limit_errors": int(limits),
        "window_sec": int(window),
    }


def _cause(rows: list[dict], latest: dict) -> dict:
    """State the measured reason an instrument's bars are old.

    Every branch names the counter it was decided on. Where the counters do not
    single one out the verdict is ``UNDETERMINED`` rather than the most likely
    story.
    """
    stats = _age_stats(rows or [latest])
    flow = _throughput(rows or [])
    reasons: list[str] = []
    sub = str(latest.get("subscription_state") or "UNKNOWN")
    if sub in ("NO_SOCKET", "SOCKET_DOWN"):
        reasons.append(f"WEBSOCKET_UNAVAILABLE:{sub}")
    elif sub == "NOT_SUBSCRIBED":
        reasons.append("TOKEN_NOT_SUBSCRIBED")
    elif sub == "WANTED_BUT_NOT_ON_SOCKET":
        # The provider asked for the token and the socket does not carry it: the
        # per-socket cap dropped it. Named separately because it is the one cause
        # that used to report itself as SUBSCRIBED.
        reasons.append("TOKEN_DROPPED_BY_SOCKET_CAP")
    tick_age = _num(latest.get("tick_age_sec"))
    if sub == "SUBSCRIBED" and (tick_age is None or tick_age > settings.futures_feed_stale_sec):
        reasons.append("SUBSCRIBED_BUT_NOT_TICKING")
    if (flow.get("rest_deferred") or 0) > 0:
        reasons.append("HISTORICAL_BUDGET_DEFERRED")
    if (flow.get("rate_limit_errors") or 0) > 0:
        reasons.append("HISTORICAL_RATE_LIMITED")
    if str(latest.get("source")) == NONE or _num(latest.get("age_seconds")) is None:
        reasons.append("NO_BARS")
    # This function explains OLD bars. An instrument whose newest bar is current
    # and which was never stale has nothing to explain, so conditions that did
    # not cost it freshness (a deferred historical refresh while the socket
    # pushes every second) are reported separately instead of as the verdict --
    # otherwise a healthy MCX contract reads HISTORICAL_BUDGET_DEFERRED at a bar
    # age of 5 seconds, which sends the reader after the wrong thing.
    age = _num(latest.get("age_seconds"))
    current = age is not None and age <= settings.futures_feed_stale_sec
    never_stale = (stats.get("stale_pct") or 0.0) == 0.0
    if reasons and current and never_stale:
        return {"verdict": "OK", "reasons": [], "not_causal": reasons,
                "diag": latest.get("diag")}
    verdict = reasons[0] if reasons else (
        "OK" if stats.get("stale_pct") in (0.0, None) else "UNDETERMINED")
    return {"verdict": verdict, "reasons": reasons, "not_causal": [],
            "diag": latest.get("diag")}


def report() -> dict:
    """Per-contract audit rows plus session aggregates, for the artefact."""
    with _LOCK:
        latest = {k: dict(v) for k, v in _latest.items()}
        samples = {k: list(v) for k, v in _samples.items()}
    rows: list[dict] = []
    for inst in sorted(latest):
        srows = samples.get(inst) or [latest[inst]]
        rows.append({
            "instrument": inst,
            "latest": latest[inst],
            "age": _age_stats(srows),
            "throughput": _throughput(srows),
            "cause": _cause(srows, latest[inst]),
        })
    all_samples = [r for srows in samples.values() for r in srows]
    return {
        "scope": "DIAGNOSTICS_ONLY",
        "enabled": bool(settings.futures_feed_audit),
        "ws_bars_enabled": bool(settings.futures_ws_bars),
        "bands_sec": {
            "aging_above": settings.futures_feed_aging_sec,
            "stale_above": settings.futures_feed_stale_sec,
            "dead_above": settings.futures_feed_dead_sec,
        },
        "engine_refusal_threshold_sec": 90.0,
        "threshold_note": (
            "the futures research engine's own 90s freshness refusal is "
            "unchanged and does not read these bands"
        ),
        "instruments": len(rows),
        "session": _age_stats(all_samples) if all_samples else _age_stats(
            list(latest.values())),
        "rows": rows,
    }


def write_report() -> str:
    """Write ``futures_feed_audit.json`` under ``data_dir``; return its path."""
    os.makedirs(settings.data_dir, exist_ok=True)
    path = os.path.join(settings.data_dir, REPORT_JSON)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report(), fh, indent=2, default=str)
    os.replace(tmp, path)
    return path


def reset_for_tests() -> None:
    with _LOCK:
        _latest.clear()
        _samples.clear()
        _last_sample_at.clear()
