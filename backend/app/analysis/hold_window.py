"""How long does a call like this one usually take? — Phase 13B, RESEARCH ONLY.

The card already prints ``Hold: ~N min``. That number is a formula —
``30 / directional_strength`` widened by the level scale — so it says how far the
targets were placed, not how long anything historically took, and it reads as a
promise. The user's complaint after 26 Aug was exactly that: the tool suggested
holding longer while the premium was giving back its gain.

This module answers the same question from the recorded outcome ledger instead:

* ``expected_low`` / ``expected_high`` — the interquartile time-to-T1 of calls
  that reached T1 in this cohort. Most resolutions happen inside it;
* ``long_tail`` — the 90th percentile of the same distribution. Past it a call
  that has not resolved is in the minority that historically did not;
* ``stop_median`` — how long losers took to reach the stop, so the window is not
  read as "nothing bad happens before this";
* ``basis`` — the cohort, its sample size, and whether the numbers are the
  cohort's own or the pooled ones because the cohort was too small.

It is a *time-to-resolution* estimate and it is labelled as one. It is NOT a
probability that the target arrives: on this book only 27.4% of calls reached the
underlying target before the stop, so a window that implied "hold and it pays"
would be false. Nothing here is calibrated, so nothing here says "probability".

Frozen behaviour: this does not change ``expected_holding_minutes``, the stop, the
targets, the exits or the follow window. It publishes a second figure beside the
production one, and :func:`state` names where an open call sits inside it so a
time stop can be *researched* (Phase 13 Part 8) before anything acts on one.
"""
from __future__ import annotations

import time

from app.analysis import instrument_family, signal_journal
from app.config import settings

WITHIN_WINDOW = "WITHIN_WINDOW"
BEYOND_EXPECTED = "BEYOND_EXPECTED"
BEYOND_LONG_TAIL = "BEYOND_LONG_TAIL"
UNKNOWN = "UNKNOWN"
STATES = (WITHIN_WINDOW, BEYOND_EXPECTED, BEYOND_LONG_TAIL, UNKNOWN)

COHORT_OWN = "COHORT_OWN_QUANTILES"
COHORT_POOLED = "POOLED_QUANTILES_COHORT_TOO_SMALL"
COHORT_NONE = "NO_RESOLVED_SAMPLE"

# The ledger is re-read at most this often: the quantiles move slowly and the
# card asks for them on every tick.
_CACHE_TTL_SEC = 300.0
_cache: dict[str, object] = {"built_at": 0.0, "cohorts": {}}


def _quantile(xs: list[float], f: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    idx = min(len(ys) - 1, max(0, int(round(f * (len(ys) - 1)))))
    return round(ys[idx], 1)


def _cohort_key(instrument: str, side: str | None) -> str:
    fam = instrument_family.family(instrument)
    return f"{fam}|{side}" if side else fam


def _measure(rows: list[dict]) -> dict:
    """Quantiles of one cohort's resolved calls. Latched milestones only."""
    t1: list[float] = []
    stops: list[float] = []
    timeouts = 0
    resolved = 0
    for r in rows:
        resolved += 1
        reached = r.get("targets_reached") or {}
        hit = reached.get("T1") if isinstance(reached, dict) else None
        if isinstance(hit, dict) and hit.get("minutes_from_signal") is not None:
            t1.append(float(hit["minutes_from_signal"]))
        stop = r.get("stop_event")
        if isinstance(stop, dict) and stop.get("minutes_from_signal") is not None:
            stops.append(float(stop["minutes_from_signal"]))
        if str(r.get("outcome") or "") in ("TIMEOUT", "EXPIRED"):
            timeouts += 1
    return {
        "resolved": resolved,
        "reached_t1": len(t1),
        "t1_p25": _quantile(t1, 0.25),
        "t1_p50": _quantile(t1, 0.50),
        "t1_p75": _quantile(t1, 0.75),
        "t1_p90": _quantile(t1, 0.90),
        "stop_median": _quantile(stops, 0.50),
        "stopped": len(stops),
        "timeouts": timeouts,
        "reached_t1_pct": (round(100.0 * len(t1) / resolved, 1)
                           if resolved else None),
    }


def _build(session: str | None = None) -> dict[str, dict]:
    """Per-cohort and pooled quantiles from the recorded resolutions."""
    rows = signal_journal.resolutions(session)
    cohorts: dict[str, list[dict]] = {"POOLED": []}
    for r in rows:
        instrument = str(r.get("instrument") or "")
        symbol = str(r.get("symbol") or "")
        side = ("CE" if symbol.endswith("CE") else
                "PE" if symbol.endswith("PE") else None)
        cohorts["POOLED"].append(r)
        for key in {_cohort_key(instrument, None), _cohort_key(instrument, side)}:
            cohorts.setdefault(key, []).append(r)
    return {key: _measure(rs) for key, rs in cohorts.items()}


def cohorts(session: str | None = None, force: bool = False) -> dict[str, dict]:
    """Cached cohort table. ``force`` re-reads the ledger immediately."""
    now = time.time()
    built = float(_cache["built_at"])  # type: ignore[arg-type]
    if force or session is not None or now - built > _CACHE_TTL_SEC:
        table = _build(session)
        if session is None:
            _cache["cohorts"] = table
            _cache["built_at"] = now
        return table
    return dict(_cache["cohorts"])  # type: ignore[arg-type]


def window(instrument: str, side: str | None = None) -> dict:
    """The measured hold window for a call on this instrument and side.

    Falls back to the pooled quantiles when the cohort has fewer resolved calls
    than ``hold_window_min_cohort``, and says which of the two it used. With no
    resolved sample at all every figure is None — the card then shows nothing
    rather than a fabricated range.
    """
    table = cohorts()
    key = _cohort_key(instrument, side)
    own = table.get(key) or {}
    pooled = table.get("POOLED") or {}
    if own.get("reached_t1", 0) >= settings.hold_window_min_cohort:
        src, basis = own, COHORT_OWN
    elif pooled.get("reached_t1", 0) > 0:
        src, basis = pooled, COHORT_POOLED
        key = "POOLED"
    else:
        return {
            "expected_low": None, "expected_high": None, "long_tail": None,
            "median": None, "stop_median": None, "cohort": key,
            "cohort_resolved": own.get("resolved", 0), "cohort_reached_t1": 0,
            "reached_t1_pct": None, "basis": COHORT_NONE,
            "research_only": True, "is_probability": False,
            "note": ("no resolved call has reached T1 in the recorded ledger yet, "
                     "so no window is published"),
        }
    return {
        "expected_low": src.get("t1_p25"),
        "expected_high": src.get("t1_p75"),
        "long_tail": src.get("t1_p90"),
        "median": src.get("t1_p50"),
        "stop_median": src.get("stop_median"),
        "cohort": key,
        "cohort_resolved": src.get("resolved"),
        "cohort_reached_t1": src.get("reached_t1"),
        "reached_t1_pct": src.get("reached_t1_pct"),
        "basis": basis,
        "research_only": True,
        "is_probability": False,
        "note": ("expected time to resolution measured from calls that did "
                 "resolve; it is not a probability that the target arrives, and "
                 "it does not change the production hold estimate, stop, targets "
                 "or exits"),
    }


def state(minutes_held: float | None, win: dict) -> str:
    """Where an open call sits inside its own measured window."""
    if minutes_held is None or win.get("expected_high") is None:
        return UNKNOWN
    held = float(minutes_held)
    tail = win.get("long_tail")
    if tail is not None and held > float(tail):
        return BEYOND_LONG_TAIL
    if held > float(win["expected_high"]):
        return BEYOND_EXPECTED
    return WITHIN_WINDOW


def report(session: str | None = None) -> dict:
    """Artefact body for ``hold_window_analysis.json``."""
    table = cohorts(session, force=True)
    pooled = table.get("POOLED") or {}
    return {
        "phase": "13B",
        "research_only": True,
        "session": session,
        "min_cohort_for_own_quantiles": settings.hold_window_min_cohort,
        "pooled": pooled,
        "cohorts": {k: v for k, v in sorted(table.items()) if k != "POOLED"},
        "production_estimate_unchanged": True,
        "note": ("time-to-T1 quantiles of resolved calls, per family and side. "
                 "In-sample description of the recorded sessions: it is not a "
                 "forecast, not calibrated, and not a probability."),
    }


def reset_for_tests() -> None:
    _cache["built_at"] = 0.0
    _cache["cohorts"] = {}
