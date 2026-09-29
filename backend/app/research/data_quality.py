"""Data-quality validation for imported market history.

Detects the common integrity problems in candle data before it is trusted for
replay/analytics: missing bars (intraday gaps), duplicate timestamps,
out-of-order rows, zero/negative prices and impossible OHLC relationships.
Session boundaries (overnight / weekend / holiday gaps) are NOT flagged as
missing — only gaps *within* a continuous session are.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.config import settings
from app.research.store import ResearchStore, store


def validate_rows(rows: list[dict], *, interval_seconds: int | None = None) -> list[dict]:
    interval = interval_seconds or settings.candle_interval_seconds
    issues: list[dict] = []
    seen: set[int] = set()
    prev_ts: int | None = None
    # a gap larger than this is treated as a session boundary, not a hole
    session_break = max(interval * 5, 900)

    for r in rows:
        ts = int(r["ts"])
        if ts in seen:
            issues.append({"ts": ts, "kind": "duplicate", "detail": "duplicate timestamp"})
            continue
        seen.add(ts)
        o, h, low, c = r.get("open"), r.get("high"), r.get("low"), r.get("close")
        if None not in (o, h, low, c):
            if min(o, h, low, c) <= 0:
                issues.append({"ts": ts, "kind": "non_positive_price",
                               "detail": f"o={o} h={h} l={low} c={c}"})
            if h < max(o, c) or low > min(o, c) or h < low:
                issues.append({"ts": ts, "kind": "bad_ohlc",
                               "detail": f"o={o} h={h} l={low} c={c}"})
        if prev_ts is not None:
            delta = ts - prev_ts
            if delta < 0:
                issues.append({"ts": ts, "kind": "out_of_order",
                               "detail": f"ts {ts} < prev {prev_ts}"})
            elif interval < delta <= session_break:
                missing = int(delta / interval) - 1
                issues.append({"ts": ts, "kind": "missing_bars",
                               "detail": f"~{missing} bar(s) missing before this ts"})
        prev_ts = ts
    return issues


def validate_stored(instrument: str, *, record: bool = True,
                    st: ResearchStore | None = None) -> dict:
    st = st or store()
    rows = st.futures_rows(instrument)
    issues = validate_rows(rows)
    if record and issues:
        st.insert_quality_issues(instrument, issues)
    by_kind: dict[str, int] = {}
    for i in issues:
        by_kind[i["kind"]] = by_kind.get(i["kind"], 0) + 1
    span = None
    if rows:
        span = {
            "from": datetime.fromtimestamp(int(rows[0]["ts"]), tz=timezone.utc).isoformat(),
            "to": datetime.fromtimestamp(int(rows[-1]["ts"]), tz=timezone.utc).isoformat(),
        }
    return {
        "instrument": instrument,
        "rows_checked": len(rows),
        "issues": len(issues),
        "by_kind": by_kind,
        "span": span,
        "clean": len(issues) == 0,
    }
