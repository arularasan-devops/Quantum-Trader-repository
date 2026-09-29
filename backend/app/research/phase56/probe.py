"""Three measurements, each designed so a failure cannot be read as a finding.

1. **coverage** — how far back the daily series goes and how many sessions it
   carries, read by asking for far more history than the study needs. The floor
   the provider returns is a property of the provider, not of the request;
2. **corporate actions** — whether a published split or bonus appears in the
   series as an uncorrected overnight jump. If it does not, the provider has
   applied the action retroactively without recording it, which is the fact that
   decides whether §5's RAW vs ADJUSTED separation is constructible at all;
3. **vanished securities** — whether a symbol that stopped trading inside the
   window is still reachable. This is the survivorship measurement, and it is
   two-sided: absence from the master *and* unreachability by token.

Every probe distinguishes three outcomes: present, absent, and inconclusive. A
transport error, a rate-limit response or an empty window on an otherwise live
symbol is inconclusive. The mcxhist stage produced a "0 reachable" verdict from a
throttled probe once; nothing here may repeat it.
"""
from __future__ import annotations

import datetime as dt

from app.research.phase56 import (
    ACTION_RATIO_TOLERANCE,
    ACTION_WINDOW_DAYS,
    CORPORATE_ACTION_PROBES,
    COVERAGE_PROBES,
    ONE_DAY,
    PROBE_ABSENT,
    PROBE_INCONCLUSIVE,
    PROBE_PRESENT,
    SERIES_PRE_ADJUSTED,
    SERIES_RAW,
    SERIES_UNDETERMINED,
    VANISHED_SECURITY_PROBES,
)

#: How far back coverage is asked for. Deliberately absurd: the answer is then
#: the provider's floor rather than the question's.
DEEP_START = dt.date(2000, 1, 1)

RATE_LIMIT_MARKERS = (
    "exceeding access rate",
    "ab1021",
    "too many",
    "access denied",
    "couldn't parse the json",
)


def is_rate_limited(message: str) -> bool:
    """True when the provider throttled rather than answered. Public because the
    caller's fetcher needs the same test to know what is worth retrying."""
    low = (message or "").lower()
    return any(marker in low for marker in RATE_LIMIT_MARKERS)


def _parse_day(stamp: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(stamp)[:10])
    except Exception:
        return None


def _fetch_daily(fetch, token: str, start: dt.date, end: dt.date) -> tuple[list[list], str | None]:
    """``(rows, error)``. Never raises: the error text is the evidence."""
    try:
        rows = fetch(
            {
                "exchange": "NSE",
                "symboltoken": str(token),
                "interval": ONE_DAY,
                "fromdate": f"{start.isoformat()} 09:15",
                "todate": f"{end.isoformat()} 15:30",
            }
        )
    except Exception as exc:  # provider transport and payload errors alike
        return [], f"{type(exc).__name__}: {exc}"
    return list(rows or []), None


# ------------------------------------------------------------------- coverage


def probe_coverage(
    fetch,
    tokens: dict[str, str],
    *,
    symbols: tuple[str, ...] = COVERAGE_PROBES,
    end: dt.date | None = None,
) -> dict:
    """Series floor, ceiling and session count per ruler symbol."""
    end = end or dt.date.today()
    rows_out: list[dict] = []
    for symbol in symbols:
        token = tokens.get(symbol)
        if token is None:
            rows_out.append(
                {
                    "symbol": symbol,
                    "outcome": PROBE_INCONCLUSIVE,
                    "reason": "not present in the master; cannot be a ruler",
                }
            )
            continue
        candles, error = _fetch_daily(fetch, token, DEEP_START, end)
        if error is not None:
            rows_out.append(
                {
                    "symbol": symbol,
                    "token": token,
                    "outcome": PROBE_INCONCLUSIVE,
                    "reason": error,
                    "rate_limited": is_rate_limited(error),
                }
            )
            continue
        days = [d for d in (_parse_day(c[0]) for c in candles) if d is not None]
        if not days:
            rows_out.append(
                {
                    "symbol": symbol,
                    "token": token,
                    "outcome": PROBE_INCONCLUSIVE,
                    "reason": "no parseable rows returned for a live symbol",
                }
            )
            continue
        rows_out.append(
            {
                "symbol": symbol,
                "token": token,
                "outcome": PROBE_PRESENT,
                "first_session": min(days).isoformat(),
                "last_session": max(days).isoformat(),
                "sessions": len(days),
                "requested_from": DEEP_START.isoformat(),
                "calls_for_full_series": 1,
            }
        )
    present = [r for r in rows_out if r["outcome"] == PROBE_PRESENT]
    floors = sorted(r["first_session"] for r in present)
    return {
        "probed": len(rows_out),
        "present": len(present),
        "inconclusive": sum(1 for r in rows_out if r["outcome"] == PROBE_INCONCLUSIVE),
        "history_floor_earliest": floors[0] if floors else None,
        "history_floor_latest": floors[-1] if floors else None,
        "sessions_min": min((r["sessions"] for r in present), default=None),
        "sessions_max": max((r["sessions"] for r in present), default=None),
        "rows": rows_out,
    }


# ----------------------------------------------------------- corporate actions


def _ratio_across(candles: list[list], ex_date: dt.date) -> dict | None:
    """Close-to-close ratio across the first session on or after ``ex_date``."""
    series = []
    for candle in candles:
        day = _parse_day(candle[0])
        if day is None:
            continue
        series.append((day, float(candle[4])))
    series.sort()
    for i in range(1, len(series)):
        if series[i][0] >= ex_date:
            before_day, before = series[i - 1]
            after_day, after = series[i][0], series[i][1]
            if before <= 0:
                return None
            return {
                "before_session": before_day.isoformat(),
                "after_session": after_day.isoformat(),
                "close_before": before,
                "close_after": after,
                "observed_ratio": round(after / before, 6),
            }
    return None


def probe_corporate_actions(
    fetch,
    tokens: dict[str, str],
    *,
    probes: tuple[dict, ...] = CORPORATE_ACTION_PROBES,
) -> dict:
    """Does a published split/bonus show up as a raw jump, or was it pre-applied?"""
    rows_out: list[dict] = []
    for probe in probes:
        symbol = probe["symbol"]
        ex_date = dt.date.fromisoformat(probe["ex_date"])
        token = tokens.get(symbol)
        if token is None:
            rows_out.append({**probe, "outcome": PROBE_INCONCLUSIVE, "reason": "symbol absent from master"})
            continue
        candles, error = _fetch_daily(
            fetch,
            token,
            ex_date - dt.timedelta(days=ACTION_WINDOW_DAYS),
            ex_date + dt.timedelta(days=ACTION_WINDOW_DAYS),
        )
        if error is not None:
            rows_out.append(
                {
                    **probe,
                    "outcome": PROBE_INCONCLUSIVE,
                    "reason": error,
                    "rate_limited": is_rate_limited(error),
                }
            )
            continue
        measured = _ratio_across(candles, ex_date)
        if measured is None:
            rows_out.append({**probe, "outcome": PROBE_INCONCLUSIVE, "reason": "no session pair spans the ex-date"})
            continue
        expected = float(probe["ratio"])
        observed = measured["observed_ratio"]
        raw_jump = abs(observed - expected) <= ACTION_RATIO_TOLERANCE * expected
        rows_out.append(
            {
                **probe,
                **measured,
                "outcome": PROBE_PRESENT,
                "raw_jump_detected": raw_jump,
                "series_class": SERIES_RAW if raw_jump else SERIES_PRE_ADJUSTED,
            }
        )
    graded = [r for r in rows_out if r["outcome"] == PROBE_PRESENT]
    raw = [r for r in graded if r["raw_jump_detected"]]
    if not graded:
        verdict = SERIES_UNDETERMINED
    elif len(raw) == len(graded):
        verdict = SERIES_RAW
    elif not raw:
        verdict = SERIES_PRE_ADJUSTED
    else:
        # A source that adjusts some actions and not others is the worst case:
        # neither RAW nor ADJUSTED can be assumed for an unprobed symbol.
        verdict = SERIES_UNDETERMINED
    return {
        "probed": len(rows_out),
        "graded": len(graded),
        "inconclusive": sum(1 for r in rows_out if r["outcome"] == PROBE_INCONCLUSIVE),
        "raw_jumps_detected": len(raw),
        "series_verdict": verdict,
        "action_record_published": False,
        "action_record_reason": (
            "the provider exposes candles and a scrip master; neither carries an "
            "ex-date, a ratio or an action type, so an adjustment cannot be "
            "reproduced, reversed or audited from this source"
        ),
        "rows": rows_out,
    }


# ---------------------------------------------------------- vanished securities


def probe_vanished(
    fetch,
    master_symbols: set[str],
    *,
    probes: tuple[dict, ...] = VANISHED_SECURITY_PROBES,
    tokens: dict[str, str] | None = None,
) -> dict:
    """Are securities that stopped trading inside the window still reachable?

    Two questions, because failing either one breaks survivorship control: is the
    symbol in the master (so a universe builder could find it), and does any
    token serve its pre-event history (so its returns could be measured)?
    """
    tokens = tokens or {}
    rows_out: list[dict] = []
    for probe in probes:
        symbol = probe["symbol"]
        in_master = symbol.upper() in master_symbols
        row = {**probe, "in_master": in_master}
        token = tokens.get(symbol)
        if not in_master and token is None:
            row.update(
                {
                    "outcome": PROBE_ABSENT,
                    "history_reachable": False,
                    "reason": (
                        "no master row and therefore no token; the security is "
                        "unnameable in this source, so its history cannot be "
                        "requested at all"
                    ),
                }
            )
            rows_out.append(row)
            continue
        event = dt.date.fromisoformat(probe["event_date"])
        candles, error = _fetch_daily(
            fetch, token or "", event - dt.timedelta(days=30), event + dt.timedelta(days=1)
        )
        if error is not None:
            row.update({"outcome": PROBE_INCONCLUSIVE, "reason": error, "rate_limited": is_rate_limited(error)})
        elif candles:
            row.update({"outcome": PROBE_PRESENT, "history_reachable": True, "rows": len(candles)})
        else:
            row.update({"outcome": PROBE_ABSENT, "history_reachable": False, "reason": "token served no pre-event rows"})
        rows_out.append(row)
    reachable = [r for r in rows_out if r.get("history_reachable")]
    return {
        "probed": len(rows_out),
        "in_master": sum(1 for r in rows_out if r["in_master"]),
        "history_reachable": len(reachable),
        "absent": sum(1 for r in rows_out if r["outcome"] == PROBE_ABSENT),
        "inconclusive": sum(1 for r in rows_out if r["outcome"] == PROBE_INCONCLUSIVE),
        "rows": rows_out,
    }
