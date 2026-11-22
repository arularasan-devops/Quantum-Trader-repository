"""First-hour re-entry lockout.

After the first completed BUY-to-full-EXIT trade in an instrument during the
first hour (``first_hour_lockout_session_start_ist`` to
``first_hour_lockout_until_ist``, IST), new auto BUYs in that instrument are
blocked until the end of that hour. Instruments in ``first_hour_lockout_exempt``
(NIFTY, SENSEX) are never locked. A scale-out is not a completed trade.

Keyed by the underlying instrument, so a different strike or CE/PE of the same
underlying can't bypass it. Persisted per IST date in
``<data_dir>/first_hour_lockout.json``: it survives restarts, and a new session
day (after a weekend or holiday) starts clean.
"""
from __future__ import annotations

import json
import os
import threading
import time

from app.config import settings

_IST = 19800
_lock = threading.Lock()


def _path() -> str:
    return os.path.join(settings.data_dir, "first_hour_lockout.json")


def _ist_day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts) + _IST))


def _ist_midnight(ts: float) -> int:
    return (int(ts) + _IST) // 86400 * 86400 - _IST


def _hm(s: str) -> int:
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def applies(instrument: str) -> bool:
    return bool(settings.first_hour_lockout) and instrument.upper() not in {
        x.upper() for x in settings.first_hour_lockout_exempt}


def _load() -> dict:
    try:
        with open(_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def record_full_exit(instrument: str, entry_ts: float | None, exit_ts: float) -> dict | None:
    """Arm the lockout on the first full exit inside the first hour. Returns the
    (existing or new) record, or None when nothing applies."""
    if not applies(instrument):
        return None
    start = _hm(settings.first_hour_lockout_session_start_ist)
    end = _hm(settings.first_hour_lockout_until_ist)
    mins = (int(exit_ts) + _IST) % 86400 // 60
    if not (start <= mins < end):
        return None
    inst = instrument.upper()
    day = _ist_day(exit_ts)
    with _lock:
        data = _load()
        data = {day: data.get(day, {})}  # older days no longer matter
        recs = data[day]
        if inst in recs:
            return recs[inst]
        recs[inst] = {
            "instrument": inst, "day": day,
            "first_entry_ts": float(entry_ts) if entry_ts else None,
            "full_exit_ts": float(exit_ts),
            "lockout_until_ts": float(_ist_midnight(exit_ts) + end * 60),
        }
        _save(data)
        return recs[inst]


def blocked(instrument: str, now: float) -> dict | None:
    """The active lockout record for ``instrument``, or None."""
    if not applies(instrument):
        return None
    rec = (_load().get(_ist_day(now)) or {}).get(instrument.upper())
    if rec and now < float(rec.get("lockout_until_ts") or 0.0):
        return rec
    return None
