"""How long is worth holding? — Phase 13 Parts 6, 7, 8. RESEARCH ONLY.

The card currently shows no time budget, which is how a call that had already paid
+6% was still open when it round-tripped. This module measures the distribution the
hold window should be built from — time to T1, T2, T3 and to the stop — and then
asks the time-stop question directly: **of the trades still open at minute N and
still short of T1, what did they go on to do?**

That framing matters. It is not "trades that lasted N minutes did badly"; it is the
decision a trader actually faces at minute N with the target not yet reached.

One limit is structural and is not worked around: the ledger records milestones and
the resolution, not the premium at an arbitrary minute, so this study reports what
the surviving cohort eventually did and **cannot** price an exit taken at minute N.
The realised R of a time stop needs the minute premium path
(:mod:`app.research.phase7`), and until that runs on a recorded slice the time-stop
verdict stays REQUIRES_MORE_DATA rather than being estimated from the milestones.
"""
from __future__ import annotations

from app.analysis import label_outcomes

THRESHOLDS_MIN = (10, 20, 30, 45, 60, 80, 120)
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"


def _num(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _q(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ys = sorted(values)
    return round(ys[min(len(ys) - 1, int(round(quantile * (len(ys) - 1))))], 2)


def timings(rows: list[dict]) -> dict:
    """Part 6 — the measured distributions the hold window is published from."""
    def col(key: str) -> list[float]:
        return [v for v in (_num(r.get(key)) for r in rows) if v is not None]

    t1, t2, t3 = col("minutes_to_t1"), col("minutes_to_t2"), col("minutes_to_t3")
    stop, res = col("minutes_to_stop"), col("minutes_to_resolution")
    winners = [r for r in rows if (_num(r.get("net_r")) or 0.0) > 0]
    win_t1 = [v for v in (_num(r.get("minutes_to_t1")) for r in winners)
              if v is not None]
    return {
        "resolved": len(rows),
        "reached_t1": len(t1),
        "reached_t1_pct": (round(100.0 * len(t1) / len(rows), 1) if rows else None),
        "t1_minutes": {"p25": _q(t1, 0.25), "median": _q(t1, 0.5),
                       "p75": _q(t1, 0.75), "p90": _q(t1, 0.90)},
        "t2_minutes": {"median": _q(t2, 0.5), "p90": _q(t2, 0.90), "n": len(t2)},
        "t3_minutes": {"median": _q(t3, 0.5), "p90": _q(t3, 0.90), "n": len(t3)},
        "stop_minutes": {"median": _q(stop, 0.5), "p90": _q(stop, 0.90),
                         "n": len(stop)},
        "resolution_minutes": {"median": _q(res, 0.5), "p90": _q(res, 0.90)},
        "net_winner_t1_minutes": {"median": _q(win_t1, 0.5), "p90": _q(win_t1, 0.90),
                                  "n": len(win_t1)},
        "note": ("time to a milestone is the timestamp the journal latched when the "
                 "milestone was crossed; none of these are inferred from a price"),
    }


def _open_at(row: dict, minutes: int) -> bool:
    resolution = _num(row.get("minutes_to_resolution"))
    return resolution is not None and resolution > minutes


def _short_of_t1_at(row: dict, minutes: int) -> bool:
    t1 = _num(row.get("minutes_to_t1"))
    return t1 is None or t1 > minutes


def threshold(rows: list[dict], minutes: int, sessions: int) -> dict:
    """The cohort a time stop at ``minutes`` would have acted on."""
    still_open = [r for r in rows if _open_at(row=r, minutes=minutes)]
    cohort_rows = [r for r in still_open if _short_of_t1_at(r, minutes)]
    block = label_outcomes.cohort(f"OPEN_AND_SHORT_OF_T1_AT_{minutes}M",
                                  cohort_rows, sessions)
    return {
        "threshold_minutes": minutes,
        "still_open": len(still_open),
        "still_open_pct": (round(100.0 * len(still_open) / len(rows), 1)
                           if rows else None),
        "cohort": block,
        "eventually_reached_t1": sum(
            1 for r in cohort_rows if _num(r.get("minutes_to_t1")) is not None),
        "eventually_stopped": sum(1 for r in cohort_rows
                                  if r.get("outcome") == "STOP"),
        "eventually_timed_out": sum(1 for r in cohort_rows
                                    if r.get("outcome") == "TIMEOUT"),
        "exit_at_threshold_priced": False,
    }


def study(rows: list[dict]) -> dict:
    """Parts 6/7/8 over the joined rows."""
    sessions = len({r["session"] for r in rows if r.get("session")})
    return {
        "part": "13 Parts 6, 7, 8",
        "research_only": True,
        "sessions": sessions,
        "timings": timings(rows),
        "thresholds": [threshold(rows, m, sessions) for m in THRESHOLDS_MIN],
        "time_stop_verdict": {
            "status": REQUIRES_MORE_DATA,
            "reason": ("the outcome ledger holds milestones and the resolution, "
                       "not the premium at an arbitrary minute, so the realised R "
                       "of an exit taken at the threshold cannot be priced from "
                       "it; the surviving cohort's eventual outcomes are reported "
                       "instead and the priced version needs the minute premium "
                       "path from a recorded chain slice"),
        },
        "notes": [
            "the cohort at each threshold is the decision actually faced: still "
            "open and still short of T1 at that minute",
            "no production time stop exists and none is proposed by this module",
        ],
    }
