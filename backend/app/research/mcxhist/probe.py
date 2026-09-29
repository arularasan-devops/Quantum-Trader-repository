"""The probe that runs before the collector — can this source answer at all?

A five-year pull is hundreds of rate-limited calls. Starting one on the
assumption that the endpoint serves expired MCX contracts would waste an
afternoon and, worse, could produce a file whose provenance nobody can state
afterwards. So the probe asks the smallest question that decides the plan, for
one contract per root:

* does a window **before this contract was listed** return rows at all;
* if it does, at what density, and at what price level — a series at the wrong
  level is a different instrument, and row count alone would not notice;
* does any other contract of the same root answer the same window, which
  separates "this token carries the root series" from "each contract has its own
  history".

Everything the probe learns is recorded per window, including the windows that
returned nothing, because an empty window is the finding in half the cases.
"""
from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable, Sequence

from app.research.mcxhist import MIN_INTERVAL_SEC, ONE_MINUTE
from app.research.mcxhist.contracts import Fetcher

#: Windows the probe asks for, one per year of the intended span plus a recent
#: control. Declared here so a probe run cannot be widened after seeing a
#: disappointing answer.
DEFAULT_PROBE_WINDOWS: tuple[tuple[str, str], ...] = (
    ("2021-08-02", "2021-08-04"),
    ("2022-07-05", "2022-07-07"),
    ("2023-07-05", "2023-07-07"),
    ("2024-07-05", "2024-07-09"),
    ("2025-07-07", "2025-07-09"),
    ("2026-01-05", "2026-01-07"),
)

Sleeper = Callable[[float], None]


def _quality(rows: Sequence[Sequence]) -> dict:
    """Timestamp and price shape of a returned window, or why it is empty."""
    if not rows:
        return {
            "rows": 0,
            "first_ts": None,
            "last_ts": None,
            "sessions": 0,
            "median_bars_per_session": 0,
            "close_min": None,
            "close_max": None,
            "monotonic": None,
            "duplicate_ts": 0,
            "timestamp_quality": "NO_ROWS",
        }
    stamps: list[int] = []
    closes: list[float] = []
    bad = 0
    for row in rows:
        try:
            stamps.append(int(dt.datetime.fromisoformat(row[0]).timestamp()))
            closes.append(float(row[4]))
        except Exception:
            bad += 1
    per_session: dict[int, int] = {}
    for stamp in stamps:
        day = (stamp + 19_800) // 86_400
        per_session[day] = per_session.get(day, 0) + 1
    counts = sorted(per_session.values())
    median = counts[len(counts) // 2] if counts else 0
    return {
        "rows": len(rows),
        "first_ts": rows[0][0],
        "last_ts": rows[-1][0],
        "sessions": len(per_session),
        "median_bars_per_session": median,
        "close_min": min(closes) if closes else None,
        "close_max": max(closes) if closes else None,
        "monotonic": all(b > a for a, b in zip(stamps, stamps[1:])),
        "duplicate_ts": len(stamps) - len(set(stamps)),
        "timestamp_quality": "UNPARSEABLE_ROWS" if bad else "ISO_WITH_OFFSET",
    }


def probe_token(
    fetch: Fetcher,
    exchange: str,
    token: str,
    *,
    windows: Sequence[tuple[str, str]] = DEFAULT_PROBE_WINDOWS,
    interval: str = ONE_MINUTE,
    sleep: Sleeper = time.sleep,
    min_interval_sec: float = MIN_INTERVAL_SEC,
) -> list[dict]:
    """One row per requested window: what came back, or the error verbatim."""
    out: list[dict] = []
    for index, (start, end) in enumerate(windows):
        if index:
            sleep(min_interval_sec)
        began = time.time()
        try:
            rows = fetch(
                exchange,
                token,
                interval,
                dt.date.fromisoformat(start),
                dt.date.fromisoformat(end),
            )
            record = {
                "window_from": start,
                "window_to": end,
                "response": "SUCCESS",
                "error": None,
                **_quality(rows or []),
            }
        except Exception as exc:  # the provider's refusal is the result
            record = {
                "window_from": start,
                "window_to": end,
                "response": "ERROR",
                "error": f"{type(exc).__name__}: {exc}"[:200],
                **_quality([]),
            }
        record["seconds"] = round(time.time() - began, 3)
        out.append(record)
    return out


def probe_root(
    fetch: Fetcher,
    root: str,
    exchange: str,
    contracts: Sequence[dict],
    *,
    windows: Sequence[tuple[str, str]] = DEFAULT_PROBE_WINDOWS,
    interval: str = ONE_MINUTE,
    sleep: Sleeper = time.sleep,
) -> dict:
    """Probe every listed contract of a root over the declared windows."""
    per_contract: list[dict] = []
    for contract in contracts:
        rows = probe_token(
            fetch,
            exchange,
            contract["token"],
            windows=windows,
            interval=interval,
            sleep=sleep,
        )
        answered = [r for r in rows if r["rows"] > 0]
        per_contract.append(
            {
                "root": root,
                "exchange": exchange,
                "trading_symbol": contract.get("trading_symbol"),
                "token": contract["token"],
                "expiry": contract.get("expiry"),
                "interval": interval,
                "windows_requested": len(rows),
                "windows_with_rows": len(answered),
                "pre_listing_history": bool(answered),
                "windows": rows,
            }
        )
    with_history = [c for c in per_contract if c["pre_listing_history"]]
    return {
        "root": root,
        "exchange": exchange,
        "contracts_probed": len(per_contract),
        "contracts_with_pre_listing_history": len(with_history),
        "history_tokens": [c["token"] for c in with_history],
        "verdict": _verdict(per_contract),
        "per_contract": per_contract,
    }


def _verdict(per_contract: Sequence[dict]) -> str:
    """What the probe licenses, in one declared string."""
    with_history = [c for c in per_contract if c["pre_listing_history"]]
    if not with_history:
        return "EXPIRED_CONTRACT_HISTORY_UNAVAILABLE"
    if len(with_history) == 1:
        return "SINGLE_TOKEN_ROOT_SERIES_UNVERIFIED_ROLL"
    return "MULTIPLE_TOKENS_ANSWER_PRE_LISTING_WINDOWS"


def reachability(root_probe: dict) -> dict[str, bool]:
    """token -> whether it answered any pre-listing window."""
    return {
        c["token"]: bool(c["pre_listing_history"]) for c in root_probe["per_contract"]
    }
