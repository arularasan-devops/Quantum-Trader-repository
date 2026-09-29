"""The gate: which instruments may be graded, and on what evidence.

The per-series measurement is the mcxhist audit, reused unchanged — coverage
bands, calendar gaps, duplicates, monotonicity, impossible bars, overnight
boundary discontinuities, per-year breakdown. What this module adds is the part
that only makes sense across a universe:

* the correct session length per instrument class, so a 375-minute NSE session
  is not graded against an 870-minute MCX one and declared half-empty;
* one gate verdict per instrument, decided by pre-declared thresholds rather
  than by how the instrument later performs;
* the excluded names stay in the table with their numbers and their reason. An
  instrument dropped silently reads as an instrument never requested.
"""
from __future__ import annotations

import numpy as np

from app.research.mcxhist import audit as mcxaudit
from app.research.phase55 import (
    GATE_EXCLUDED_HISTORY,
    GATE_EXCLUDED_QUALITY,
    GATE_INCLUDED,
    GATE_UNRESOLVED,
    MIN_BARS,
    MIN_GRADED_SESSIONS,
    UNIVERSE,
    fingerprint,
)


def series_arrays(series) -> tuple[np.ndarray, ...]:
    return (series.ts, series.open, series.high, series.low, series.close)


def gate(audit_row: dict) -> tuple[str, str]:
    """Verdict and reason for one audited instrument."""
    graded = int(audit_row.get("graded_sessions") or 0)
    bars = int(audit_row["bars"])
    if bars < MIN_BARS:
        return GATE_EXCLUDED_HISTORY, (
            f"{bars:,} bars against a declared floor of {MIN_BARS:,}"
        )
    if graded < MIN_GRADED_SESSIONS:
        return GATE_EXCLUDED_HISTORY, (
            f"{graded:,} graded sessions against a declared floor of "
            f"{MIN_GRADED_SESSIONS:,}; a 60/20/20 chronological split needs "
            "three development years, one validation and one untouched"
        )
    dupes = int(audit_row.get("duplicate_timestamps") or 0)
    if dupes or not audit_row.get("monotonic", True):
        return GATE_EXCLUDED_QUALITY, (
            f"{dupes:,} duplicate timestamps / non-monotonic series; every "
            "statistic downstream would double-count a minute"
        )
    if int(audit_row.get("impossible_bars") or 0):
        return GATE_EXCLUDED_QUALITY, (
            f"{audit_row['impossible_bars']:,} bars with high < low or a close "
            "outside the range"
        )
    return GATE_INCLUDED, (
        f"{graded:,} graded sessions, {bars:,} bars, "
        f"{audit_row.get('coverage_pct')}% session coverage"
    )


def audit_instrument(instrument: str, series, resolution: dict | None = None) -> dict:
    """Audit one instrument's loaded series and decide its gate."""
    klass, exchange, minutes = UNIVERSE.get(instrument, ("", "", 375))
    if series is None or len(series) == 0:
        return {
            "instrument": instrument,
            "instrument_class": klass,
            "exchange": exchange,
            "session_minutes": minutes,
            "bars": 0,
            "gate": GATE_EXCLUDED_HISTORY,
            "gate_reason": "no series available to research",
            "series_class": (resolution or {}).get("series_class"),
            "token": (resolution or {}).get("token"),
        }
    row = mcxaudit.audit_series(
        instrument,
        *series_arrays(series),
        full_minutes=minutes,
        source=(resolution or {}).get("basis", ""),
        tokens=[t for t in [(resolution or {}).get("token")] if t],
    )
    verdict, reason = gate(row)
    row.update({
        "instrument_class": klass,
        "exchange": exchange,
        "session_minutes": minutes,
        "gate": verdict,
        "gate_reason": reason,
        "series_class": (resolution or {}).get("series_class"),
        "token": (resolution or {}).get("token"),
    })
    return row


def unresolved_row(resolution: dict) -> dict:
    klass, exchange, minutes = UNIVERSE.get(
        resolution["instrument"], ("", resolution.get("exchange", ""), 375)
    )
    return {
        "instrument": resolution["instrument"],
        "instrument_class": klass,
        "exchange": exchange,
        "session_minutes": minutes,
        "bars": 0,
        "gate": GATE_UNRESOLVED,
        "gate_reason": resolution.get("basis") or "token not resolved",
        "series_class": None,
        "token": None,
    }


def run(load_series, instruments: list[str], resolutions: dict[str, dict] | None = None) -> dict:
    """Audit every requested instrument. ``load_series`` is injected for tests."""
    resolutions = resolutions or {}
    rows: list[dict] = []
    for instrument in instruments:
        res = resolutions.get(instrument)
        if res is not None and res.get("status") == "UNRESOLVED":
            rows.append(unresolved_row(res))
            continue
        rows.append(audit_instrument(instrument, load_series(instrument), res))
    included = [r["instrument"] for r in rows if r["gate"] == GATE_INCLUDED]
    return {
        "fingerprint": fingerprint(),
        "requested": list(instruments),
        "included": included,
        "excluded": [
            {"instrument": r["instrument"], "gate": r["gate"], "reason": r["gate_reason"]}
            for r in rows
            if r["gate"] != GATE_INCLUDED
        ],
        "instruments": rows,
    }
