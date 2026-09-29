"""Are chased entries actually worse? — Phase 13 Parts 1, 2, 5, 10. RESEARCH ONLY.

The label was built because the 26 Aug card bought a premium that had already run.
Whether that is expensive is a measurement, not an opinion, and this module makes
it: every resolved call is put in its entry-quality cohort and its entry-state
cohort, then cut by family, side, expiry state and regime, and each cohort is
reported with its own sample size and status.

Two habits are enforced rather than described:

* cohorts are never pooled across families. On this book INDEX legs pay ~0.26% of
  premium in spread and single stocks ~1.06% at the signalled strike, so a pooled
  "chased entries lose" number would mostly be a statement about which family the
  chased calls happened to be in;
* nothing here reads VALIDATED. Both label columns are recomputed from signal-time
  journal fields for the sessions that predate the Phase 12A build, which makes
  them in-sample descriptions of those sessions — the thresholds and the data are
  the same data.
"""
from __future__ import annotations

from app.analysis import entry_location, entry_quality, instrument_family
from app.analysis import label_outcomes

from . import ledger


def _sessions(rows: list[dict]) -> int:
    return len({r["session"] for r in rows if r.get("session")})


def _cut(rows: list[dict], key: str, values: tuple[str, ...],
         sessions: int) -> list[dict]:
    return [label_outcomes.cohort(f"{key}={v}",
                                  [r for r in rows if r.get(key) == v], sessions)
            for v in values]


def _cross(rows: list[dict], outer: str, outer_values: tuple[str, ...],
           sessions: int) -> dict:
    """Entry quality inside each family / side / expiry state / regime."""
    out: dict[str, list[dict]] = {}
    for value in outer_values:
        subset = [r for r in rows if r.get(outer) == value]
        if not subset:
            continue
        out[str(value)] = _cut(subset, "entry_quality", entry_quality.STATES,
                               sessions)
    return out


def study(rows: list[dict]) -> dict:
    """Parts 1/2/5/10 tables over the joined rows."""
    sessions = _sessions(rows)
    regimes = tuple(sorted({str(r.get("regime") or "UNKNOWN") for r in rows}))
    return {
        "part": "13 Parts 1, 2, 5, 10",
        "research_only": True,
        "resolved": len(rows),
        "sessions": sessions,
        "baseline": label_outcomes.cohort("ALL", rows, sessions),
        "by_entry_quality": _cut(rows, "entry_quality", entry_quality.STATES,
                                 sessions),
        "by_entry_state": _cut(rows, "entry_state", entry_location.STATES,
                               sessions),
        "by_expiry_state": _cut(rows, "expiry_state", ledger.EXPIRY_STATES,
                                sessions),
        "entry_quality_by_family": _cross(rows, "family",
                                          instrument_family.FAMILIES, sessions),
        "entry_quality_by_side": _cross(rows, "option_type",
                                        ("CE", "PE", "UNKNOWN"), sessions),
        "entry_quality_by_expiry_state": _cross(rows, "expiry_state",
                                                ledger.EXPIRY_STATES, sessions),
        "entry_quality_by_regime": _cross(rows, "regime", regimes, sessions),
        "monotonic_in_net_expectancy": _monotonic(rows, sessions),
        "notes": [
            "entry quality and entry state are signal-time labels; neither gated, "
            "delayed or suppressed any of these calls — every one of them printed "
            "on the card exactly as before",
            "labels for sessions recorded before the Phase 12A build were "
            "recomputed from that call's own journalled premium, entry zone, "
            "risk, target and ATR, so no outcome or forward price entered them",
            "cohorts are reported per family and per side because the spread a "
            "family pays dominates a pooled comparison",
        ],
    }


def _monotonic(rows: list[dict], sessions: int) -> dict:
    """Does net expectancy fall as the entry gets later? The honest check.

    Phase 12A's tradability label failed exactly here — TRADABLE was worse than
    CAUTION — so the same question is asked of this label before anyone proposes
    acting on it. A label that does not order the outcomes is not a gate
    candidate, whatever its individual cohorts look like.
    """
    order = (entry_quality.IDEAL, entry_quality.GOOD, entry_quality.ACCEPTABLE,
             entry_quality.CHASED, entry_quality.SEVERELY_CHASED)
    seq: list[dict] = []
    for state in order:
        block = label_outcomes.cohort(state,
                                      [r for r in rows if r["entry_quality"] == state],
                                      sessions)
        if block["resolved"] >= label_outcomes.MIN_COHORT:
            seq.append({"state": state, "n": block["resolved"],
                        "expectancy_net_r": block["expectancy_net_r"],
                        "win_rate_net_pct": block["win_rate_net_pct"]})
    values = [s["expectancy_net_r"] for s in seq
              if s["expectancy_net_r"] is not None]
    decreasing = (len(values) >= 3
                  and all(a >= b for a, b in zip(values, values[1:])))
    return {
        "cohorts_compared": seq,
        "comparable_cohorts": len(values),
        "net_expectancy_decreasing_with_lateness": (decreasing
                                                    if len(values) >= 3 else None),
        "status": (label_outcomes.IN_SAMPLE_ONLY if len(values) >= 3
                   else label_outcomes.INSUFFICIENT),
        "note": ("a label that does not order the outcomes is not a gate "
                 "candidate; Phase 12A's tradability label failed this check and "
                 "was not promoted"),
    }
