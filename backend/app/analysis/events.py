"""Economic-event calendar + pre-event guard.

Produces a list of upcoming high-impact scheduled events and a "guard" that
tells the dashboard/engine when we're inside the danger window around one
(so it can WARN and mildly dampen confidence — never a hard block).

Two sources, merged:
  1. Built-in RECURRING events computed from simple date rules (no network):
       - EIA Weekly Petroleum Status Report  (Wed 15:00 UTC ~ crude inventory)
       - US Nonfarm Payrolls                  (1st Fri 12:30 UTC)
       - India F&O monthly expiry             (last Thu 10:00 UTC = 15:30 IST)
  2. An OPTIONAL public ICS calendar (QT_EVENTS_ICS) for things that don't
     follow a simple rule (FOMC, RBI MPC, company results). Best-effort;
     network failures are swallowed so the dashboard never blocks.

Times are UTC epoch seconds. Rules use approximate standard release times and
ignore US daylight-saving/holiday shifts — good enough for a "stand aside"
guard, not for millisecond timing.
"""
from __future__ import annotations

import calendar
import datetime as _dt
import time
from dataclasses import dataclass

import httpx

from app.config import settings


@dataclass(frozen=True)
class Event:
    ts: int          # UTC epoch seconds of the scheduled release
    name: str
    category: str    # inventory | macro | expiry | rates | results | other
    impact: str      # HIGH | MEDIUM | LOW
    affects: str     # "crude" | "equity" | "all"


def _utc(y: int, m: int, d: int, hh: int, mm: int) -> int:
    return int(_dt.datetime(y, m, d, hh, mm, tzinfo=_dt.timezone.utc).timestamp())


def _first_weekday(y: int, m: int, weekday: int) -> int:
    """Day-of-month of the first given weekday (Mon=0..Sun=6)."""
    first = _dt.date(y, m, 1).weekday()
    return 1 + (weekday - first) % 7


def _last_weekday(y: int, m: int, weekday: int) -> int:
    days = calendar.monthrange(y, m)[1]
    last = _dt.date(y, m, days).weekday()
    return days - (last - weekday) % 7


def _recurring(now: int, horizon_days: int) -> list[Event]:
    """Generate the built-in recurring events within [now, now+horizon]."""
    out: list[Event] = []
    start = _dt.datetime.fromtimestamp(now, _dt.timezone.utc).date()
    end = start + _dt.timedelta(days=horizon_days)

    # Iterate month windows covering the horizon.
    months: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1

    for (yy, mm) in months:
        # US Nonfarm Payrolls — first Friday 12:30 UTC.
        d = _first_weekday(yy, mm, 4)
        out.append(Event(_utc(yy, mm, d, 12, 30), "US Nonfarm Payrolls", "macro", "HIGH", "all"))
        # India F&O monthly expiry — last Thursday 10:00 UTC (15:30 IST).
        d = _last_weekday(yy, mm, 3)
        out.append(Event(_utc(yy, mm, d, 10, 0), "India F&O monthly expiry", "expiry", "HIGH", "equity"))

    # EIA Weekly Petroleum Status Report — every Wednesday 15:00 UTC.
    day = start
    while day <= end:
        if day.weekday() == 2:  # Wednesday
            out.append(Event(_utc(day.year, day.month, day.day, 15, 0),
                             "EIA crude inventories", "inventory", "HIGH", "crude"))
        day += _dt.timedelta(days=1)

    return [e for e in out if now <= e.ts <= now + horizon_days * 86400]


def _from_ics(now: int, horizon_days: int) -> list[Event]:
    url = settings.events_ics_url.strip()
    if not url:
        return []
    try:
        resp = httpx.get(url, timeout=6.0, follow_redirects=True)
        resp.raise_for_status()
        text = resp.text
    except Exception:
        return []
    out: list[Event] = []
    name = ""
    dtstart = 0
    for raw in text.splitlines():
        line = raw.strip()
        if line == "BEGIN:VEVENT":
            name, dtstart = "", 0
        elif line.startswith("SUMMARY:"):
            name = line[len("SUMMARY:"):].strip()
        elif line.startswith("DTSTART"):
            val = line.split(":", 1)[-1].strip()
            dtstart = _parse_ics_dt(val)
        elif line == "END:VEVENT" and name and dtstart:
            if now <= dtstart <= now + horizon_days * 86400:
                out.append(Event(dtstart, name, "other", "HIGH", "all"))
    return out


def _parse_ics_dt(val: str) -> int:
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            dt = _dt.datetime.strptime(val, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return 0


def upcoming(now: int | None = None, horizon_days: int = 10, affects: str | None = None) -> list[Event]:
    """Merged, de-duplicated, time-sorted upcoming events."""
    now = now or int(time.time())
    events = _recurring(now, horizon_days) + _from_ics(now, horizon_days)
    if affects:
        events = [e for e in events if e.affects in ("all", affects)]
    events.sort(key=lambda e: e.ts)
    # de-dup identical name+minute
    seen: set[tuple[str, int]] = set()
    out: list[Event] = []
    for e in events:
        key = (e.name, e.ts // 60)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def guard(now: int | None = None, affects: str = "all") -> dict:
    """Pre-event guard status for the CURRENT moment.

    Returns {active, minutes_to, event, impact, factor}. ``factor`` is a mild
    confidence multiplier the engine applies (1.0 = no effect). Inside the
    danger window of a HIGH-impact relevant event it drops to ~0.85 so the tool
    leans toward WAIT — it is deliberately NOT a hard block.
    """
    now = now or int(time.time())
    if not settings.event_guard_enabled:
        return {"active": False, "minutes_to": None, "event": None, "impact": None, "factor": 1.0}
    before = settings.event_guard_before_minutes * 60
    after = settings.event_guard_after_minutes * 60
    nxt: Event | None = None
    for e in upcoming(now - after, horizon_days=2, affects=affects):
        if e.impact != "HIGH":
            continue
        nxt = e
        break
    if nxt is None:
        return {"active": False, "minutes_to": None, "event": None, "impact": None, "factor": 1.0}
    delta = nxt.ts - now
    active = -after <= delta <= before
    return {
        "active": active,
        "minutes_to": round(delta / 60),
        "event": nxt.name,
        "impact": nxt.impact,
        "factor": 0.85 if active else 1.0,
    }
