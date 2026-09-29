"""What history we actually hold. RESEARCH ONLY.

A backtest is only worth its coverage, so this is deliberately the first thing
Phase 14 reports and the thing a replay refuses to run without: sessions held,
bars per session, thin sessions, gaps between consecutive sessions, and the
windows the collector could not fetch.

Bars per session are counted in **IST**, the clock the exchange and the journal
both use, so a session here means the same day a signal row means.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

from app.research.phase14.collector import ONE_MINUTE
from app.research.phase14.spot_store import SpotStore

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# A normal NSE cash session is 09:15–15:30 = 375 one-minute bars. Anything much
# under that is a short session, a holiday half-day or a hole in the download, and
# it is flagged rather than averaged away.
FULL_SESSION_BARS = 375
THIN_SESSION_FRACTION = 0.8


def session_of(ts: int) -> str:
    return dt.datetime.fromtimestamp(int(ts), IST).date().isoformat()


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def instrument_coverage(instrument: str, st: SpotStore,
                        interval: str = ONE_MINUTE) -> dict:
    rows = st.candles(instrument)
    per_session = Counter(session_of(r["ts"]) for r in rows)
    sessions = sorted(per_session)
    counts = [per_session[s] for s in sessions]
    thin = [s for s in sessions
            if per_session[s] < FULL_SESSION_BARS * THIN_SESSION_FRACTION]
    gaps: list[dict] = []
    for earlier, later in zip(sessions, sessions[1:]):
        days = (dt.date.fromisoformat(later) - dt.date.fromisoformat(earlier)).days
        # Four calendar days between sessions is a long weekend plus a holiday;
        # more than that is worth naming, because a replay silently skipping a
        # fortnight looks exactly like a quiet market.
        if days > 4:
            gaps.append({"after": earlier, "before": later, "calendar_days": days})
    return {
        "instrument": instrument,
        "interval": interval,
        "bars": len(rows),
        "sessions": len(sessions),
        "first_session": sessions[0] if sessions else None,
        "last_session": sessions[-1] if sessions else None,
        "bars_per_session_median": _median(counts),
        "bars_per_session_min": min(counts) if counts else 0,
        "bars_per_session_max": max(counts) if counts else 0,
        "thin_sessions": len(thin),
        "thin_session_dates": thin[:20],
        "session_gaps": gaps[:20],
        "chunk_status": st.chunk_stats(instrument, interval),
        "chunks_failed": st.failures(instrument, interval)[:20],
    }


def report(st: SpotStore, instruments: list[str] | None = None,
           interval: str = ONE_MINUTE) -> dict:
    names = instruments if instruments is not None else st.instruments()
    per = [instrument_coverage(n, st, interval) for n in names]
    return {
        "backend": st.backend,
        "interval": interval,
        "instruments": len(per),
        "bars_total": sum(p["bars"] for p in per),
        "sessions_max": max((p["sessions"] for p in per), default=0),
        "instruments_with_failed_chunks": [p["instrument"] for p in per
                                           if p["chunks_failed"]],
        "per_instrument": per,
    }
