"""Signal outcome tracker — logs every BUY signal the frozen engine gives and
resolves it against real option prices (did it reach Target or Stop Loss).

This is pure observability layered on top of the FROZEN engine: it never places
an order and never influences a decision. Each committed BUY recommendation is
recorded once with its entry premium, stop and targets, then marked
TARGET / STOP / EXPIRED as the recommended option's live premium evolves. Daily
aggregates power the dashboard scoreboard so the user can see, per day, how many
signals were given, how many hit target and how many hit stop.

Storage is a small JSON file under ``data_dir`` so it works out of the box with
zero external services and survives restarts.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models import Decision, OptionQuote

_IST = timezone(timedelta(hours=5, minutes=30))
_LOCK = threading.Lock()
_MAX_ROWS = 2000

# in-memory guard so a single BUY episode is logged once per instrument
_last_key: dict[str, str] = {}


def _path() -> str:
    d = settings.data_dir
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "signal_log.json")


def _load() -> list[dict]:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(rows: list[dict]) -> None:
    rows = rows[-_MAX_ROWS:]
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    os.replace(tmp, _path())


def _ist_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, _IST).strftime("%Y-%m-%d")


def _ist_hhmm(ts: int) -> str:
    return datetime.fromtimestamp(ts, _IST).strftime("%H:%M")


def _targets_reached(row: dict, peak: float) -> int:
    n = 0
    for k in ("target1", "target2", "target3"):
        t = row.get(k)
        if t is not None and peak >= t:
            n += 1
    return n


def observe(instrument: str, decision: Decision, chain: list[OptionQuote], now: int) -> None:
    """Called each tick. Logs a fresh BUY signal (once) and resolves any open
    signals for this instrument against the recommended option's live premium.
    Best-effort: never raises into the tick loop."""
    try:
        with _LOCK:
            rows = _load()
            prices = {q.symbol: q.premium for q in chain}
            changed = _resolve(instrument, prices, now, rows)
            changed = _maybe_log(instrument, decision, prices, now, rows) or changed
            if changed:
                _save(rows)
    except Exception:
        return


def _mins(row: dict, now: int) -> float:
    return round(max(0, now - int(row.get("ts", now))) / 60.0, 1)


def _finalize(row: dict, status: str, prem: float | None, now: int, reason: str) -> None:
    row["status"] = status
    row["resolved_ts"] = now
    row["resolved_premium"] = prem
    row["close_reason"] = reason


def _maybe_log(instrument: str, d: Decision, prices: dict, now: int, rows: list[dict]) -> bool:
    signal = d.signal.value if d.signal else None
    actionable = (
        signal == "BUY"
        and d.recommended_option
        and d.current_premium
        and d.stop_loss is not None
        and d.target1 is not None
    )
    key = f"{signal}|{d.recommended_option}|{d.option_type.value if d.option_type else ''}"
    if not actionable:
        _last_key[instrument] = key
        return False
    if _last_key.get(instrument) == key:
        return False
    _last_key[instrument] = key

    # A new distinct BUY has arrived. Historically this closed every still-open
    # signal on the instrument, which destroyed the outcome record: signals were
    # being killed a median of 7 minutes in, some 96% of the way to T1, so only a
    # quarter of them ever resolved to a real T1/STOP. A same-direction re-signal
    # is a re-entry, not an invalidation — it is left running to resolve on its
    # own merits. Only a DIRECTION FLIP invalidates the earlier read, and a cap
    # keeps the open set bounded.
    side = d.option_type.value if d.option_type else None
    live = [
        row for row in rows
        if row.get("instrument") == instrument and row.get("status") == "OPEN"
    ]
    for row in live:
        if side is not None and row.get("option_type") not in (None, side):
            prem = prices.get(row.get("option"))
            final = "TARGET" if str(row.get("first_hit") or "").startswith("T") else "EXPIRED"
            _finalize(row, final, prem, now, "REVERSED")
    still_open = [row for row in live if row.get("status") == "OPEN"]
    excess = len(still_open) + 1 - max(1, settings.signal_max_open_per_instrument)
    for row in sorted(still_open, key=lambda r: int(r.get("ts", 0)))[:max(0, excess)]:
        prem = prices.get(row.get("option"))
        final = "TARGET" if str(row.get("first_hit") or "").startswith("T") else "EXPIRED"
        _finalize(row, final, prem, now, "SUPERSEDED")

    entry = float(d.current_premium)
    rows.append({
        "id": f"{instrument}-{now}",
        "instrument": instrument,
        "date": _ist_date(now),
        "ts": now,
        "time": _ist_hhmm(now),
        "signal": signal,
        "option": d.recommended_option,
        "option_type": d.option_type.value if d.option_type else None,
        "strike": d.strike,
        "entry": entry,
        "stop": d.stop_loss,
        "target1": d.target1,
        "target2": d.target2,
        "target3": d.target3,
        "confidence": d.confidence,
        "status": "OPEN",
        "peak": entry,
        "trough": entry,
        "targets_hit": 0,
        # progressive milestones: minutes from entry to each target / stop
        "t1_mins": None,
        "t2_mins": None,
        "t3_mins": None,
        "stop_mins": None,
        "first_hit": None,   # which of T1/T2/T3/STOP was reached first
        "close_reason": None,
        "resolved_ts": None,
        "resolved_premium": None,
    })
    return True


def _record_milestones(row: dict, prem: float, now: int) -> bool:
    """Stamp the time-to-reach for any newly crossed target, and the first event
    (T1/T2/T3/STOP) that occurred. Returns True if anything changed."""
    changed = False
    for i, k in enumerate(("target1", "target2", "target3"), start=1):
        t = row.get(k)
        mk = f"t{i}_mins"
        if t is not None and prem >= t and row.get(mk) is None:
            row[mk] = _mins(row, now)
            if row.get("first_hit") is None:
                row["first_hit"] = f"T{i}"
            changed = True
    stop = row.get("stop")
    if stop is not None and prem <= stop and row.get("stop_mins") is None:
        row["stop_mins"] = _mins(row, now)
        if row.get("first_hit") is None:
            row["first_hit"] = "STOP"
        changed = True
    return changed


def _resolve(instrument: str, prices: dict, now: int, rows: list[dict]) -> bool:
    changed = False
    today = _ist_date(now)
    for row in rows:
        if row.get("instrument") != instrument or row.get("status") != "OPEN":
            continue
        prem = prices.get(row.get("option"))
        if prem is not None:
            if prem > row.get("peak", prem):
                row["peak"] = prem
                changed = True
            if prem < row.get("trough", prem):
                row["trough"] = prem
                changed = True
            if _record_milestones(row, prem, now):
                changed = True
            row["targets_hit"] = _targets_reached(row, row["peak"])
            t1 = row.get("target1")
            t3 = row.get("target3")
            stop = row.get("stop")
            # Finalize (close the trade) the moment it's decided: stop hit, or the
            # FIRST target (T1) reached — matching the "book at T1" guidance, so a
            # signal that reaches its target is shown as TARGET, not left OPEN.
            if stop is not None and prem <= stop:
                status = "TARGET" if str(row.get("first_hit") or "").startswith("T") else "STOP"
                _finalize(row, status, prem, now, "STOP")
                changed = True
                continue
            if t1 is not None and prem >= t1:
                _finalize(row, "TARGET", prem, now, "T1")
                changed = True
                continue
            if t3 is not None and prem >= t3:
                _finalize(row, "TARGET", prem, now, "T3")
                changed = True
                continue
        # still open but from a previous day → the session ended unresolved
        if row.get("date") != today:
            status = "TARGET" if str(row.get("first_hit") or "").startswith("T") else "EXPIRED"
            _finalize(row, status, prem, now, "EOD")
            changed = True
    return changed


_BUCKETS = [
    ("80-85", 80.0, 85.0),
    ("85-90", 85.0, 90.0),
    ("90-95", 90.0, 95.0),
    ("95+", 95.0, 1000.0),
]


def _bucket_stats(rows: list[dict]) -> list[dict]:
    """Break resolved signals down by conviction band so the user can see, e.g.,
    whether 80-85 signals hit stops more often than 90-95 / 95+."""
    out = []
    for label, lo, hi in _BUCKETS:
        band = [
            r for r in rows
            if r.get("confidence") is not None and lo <= float(r["confidence"]) < hi
        ]
        target = sum(1 for r in band if r["status"] == "TARGET")
        stop = sum(1 for r in band if r["status"] == "STOP")
        resolved = target + stop
        out.append({
            "band": label,
            "signals": len(band),
            "target": target,
            "stop": stop,
            "open": sum(1 for r in band if r["status"] == "OPEN"),
            "win_rate": round(target / resolved * 100, 1) if resolved else None,
            "stop_rate": round(stop / resolved * 100, 1) if resolved else None,
        })
    return out


def _avg(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 1) if vals else None


def _timing_stats(rows: list[dict]) -> dict:
    """How the frozen engine's signals actually played out: how often each target
    is reached, the typical minutes to reach it, and how often stop hits first.
    Only counts finalized signals (not still-open ones)."""
    done = [r for r in rows if r.get("status") in ("TARGET", "STOP", "EXPIRED")]
    n = len(done)
    levels = []
    for i in range(1, 4):
        mk = f"t{i}_mins"
        reached = [r for r in done if r.get(mk) is not None]
        levels.append({
            "level": f"T{i}",
            "reached": len(reached),
            "reach_pct": round(len(reached) / n * 100, 1) if n else None,
            "avg_mins": _avg([float(r[mk]) for r in reached]),
        })
    stop_first = [r for r in done if r.get("first_hit") == "STOP"]
    stop_mins = [float(r["stop_mins"]) for r in done if r.get("stop_mins") is not None]
    return {
        "finalized": n,
        "levels": levels,
        "stop_first": len(stop_first),
        "stop_first_pct": round(len(stop_first) / n * 100, 1) if n else None,
        "avg_mins_to_stop": _avg(stop_mins),
    }


def _day_stats(day_rows: list[dict], date: str) -> dict:
    target = sum(1 for r in day_rows if r["status"] == "TARGET")
    stop = sum(1 for r in day_rows if r["status"] == "STOP")
    open_ = sum(1 for r in day_rows if r["status"] == "OPEN")
    expired = sum(1 for r in day_rows if r["status"] == "EXPIRED")
    resolved = target + stop
    return {
        "date": date,
        "signals": len(day_rows),
        "target": target,
        "stop": stop,
        "open": open_,
        "expired": expired,
        "win_rate": round(target / resolved * 100, 1) if resolved else None,
    }


def export_rows(instrument: str | None = None) -> list[dict]:
    """The raw tracked signals, newest last — what the export button downloads.

    Read straight from the live store rather than from a file the user copies by
    hand, because a stale copy is indistinguishable from a tracker that stopped
    recording.
    """
    with _LOCK:
        rows = _load()
    if instrument:
        rows = [r for r in rows if r.get("instrument") == instrument]
    return sorted(rows, key=lambda r: int(r.get("ts", 0)))


def latest_ts() -> int | None:
    """Timestamp of the most recent signal, so staleness is visible."""
    with _LOCK:
        rows = _load()
    stamps = [int(r.get("ts", 0)) for r in rows if r.get("ts")]
    return max(stamps) if stamps else None


def stats(instrument: str, days: int = 7, recent: int = 12) -> dict:
    with _LOCK:
        rows = [r for r in _load() if r.get("instrument") == instrument]
    now = int(datetime.now(_IST).timestamp())
    today = _ist_date(now)

    by_date: dict[str, list[dict]] = {}
    for r in rows:
        by_date.setdefault(r.get("date", "?"), []).append(r)

    dates = sorted(by_date.keys(), reverse=True)[:days]
    day_list = [_day_stats(by_date[d], d) for d in dates]
    today_stats = _day_stats(by_date.get(today, []), today)

    all_target = sum(1 for r in rows if r["status"] == "TARGET")
    all_stop = sum(1 for r in rows if r["status"] == "STOP")
    all_resolved = all_target + all_stop
    overall = {
        "signals": len(rows),
        "target": all_target,
        "stop": all_stop,
        "open": sum(1 for r in rows if r["status"] == "OPEN"),
        "win_rate": round(all_target / all_resolved * 100, 1) if all_resolved else None,
    }

    recent_rows = sorted(rows, key=lambda r: r.get("ts", 0), reverse=True)[:recent]
    recent_out = [
        {
            "time": r.get("time"),
            "date": r.get("date"),
            "option_type": r.get("option_type"),
            "strike": r.get("strike"),
            "entry": r.get("entry"),
            "status": r.get("status"),
            "targets_hit": r.get("targets_hit", 0),
            "confidence": r.get("confidence"),
            "first_hit": r.get("first_hit"),
            "t1_mins": r.get("t1_mins"),
            "t2_mins": r.get("t2_mins"),
            "t3_mins": r.get("t3_mins"),
        }
        for r in recent_rows
    ]

    return {
        "instrument": instrument,
        "today": today_stats,
        "overall": overall,
        "days": day_list,
        "buckets": _bucket_stats(rows),
        "timing": _timing_stats(rows),
        "recent": recent_out,
    }
