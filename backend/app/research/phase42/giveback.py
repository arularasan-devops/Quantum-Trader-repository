"""Phase 42 §2 — where the giveback goes, horizon by horizon.

The attribution table put 263,889 of 636,312 graded legs in the ``GIVEBACK``
channel: legs that reached profit and handed it back. That is the largest single
channel and bigger than cost and direction together, so it is worth knowing
whether the money was returned early or late, and whether any *fixed* horizon
would have kept more of it than holding to the close did.

Two things this deliberately is not.

It is not an exit rule. Every figure here is measured on the leg's realised
forward path, which is exactly the look-ahead that made §19's room table
unusable as a filter. "The best horizon in hindsight was 30 minutes" is not a
finding that a 30-minute exit would have worked, because the best horizon is
chosen per leg after seeing all of them. The per-leg best is reported only as a
dispersion measure — if the best horizon were the same leg after leg, a time
exit would be worth pre-registering; if it is scattered, it is noise and a fixed
horizon cannot capture it.

It is not the giveback *cause*. A leg can hand back a peak because the move was
never real, or because the exit sat too far away. Separating those needs the
first-event race, which is Phase 40's frozen question and is not re-answered
here.

The pass streams: the store it must run on holds 6.7 million path rows, and this
module keeps counters plus one leg's horizons at a time.
"""
from __future__ import annotations

import sqlite3

from app.research.phase35 import HORIZONS, SESSION_CLOSE
from app.research.phase42.arms import Tally

# Horizons in the order they happen, so a table reads as time passing rather
# than as SQLite's string sort ("10" before "2").
ORDER: tuple[str, ...] = tuple(str(h) for h in HORIZONS) + (SESSION_CLOSE,)
_RANK: dict[str, int] = {h: i for i, h in enumerate(ORDER)}


class HorizonTally:
    """Net, excursion and giveback at one horizon for one channel."""

    def __init__(self) -> None:
        self.net = Tally()
        self.mfe_total = 0.0
        self.mfe_legs = 0
        self.giveback_total = 0.0
        self.giveback_legs = 0
        self.reached = 0
        self.retained_total = 0.0
        self.retained_legs = 0
        self.back_to_entry = 0
        self.turned_negative = 0
        self.milestones = [0, 0, 0]
        self.milestone_legs = 0

    def add(self, net: object, mfe: object, giveback: object,
            milestones: tuple[object, object, object]) -> None:
        if isinstance(net, (int, float)):
            self.net.add(float(net))
        if isinstance(mfe, (int, float)):
            self.mfe_total += float(mfe)
            self.mfe_legs += 1
        if isinstance(giveback, (int, float)):
            self.giveback_total += float(giveback)
            self.giveback_legs += 1
        if isinstance(mfe, (int, float)) and float(mfe) > 0:
            self.reached += 1
            if isinstance(net, (int, float)):
                self.retained_total += 100.0 * float(net) / float(mfe)
                self.retained_legs += 1
                self.back_to_entry += int(float(net) <= 0)
                self.turned_negative += int(float(net) < 0)
        if any(isinstance(m, (int, float)) for m in milestones):
            self.milestone_legs += 1
            for i, hit in enumerate(milestones):
                if isinstance(hit, (int, float)) and int(hit):
                    self.milestones[i] += 1

    def stats(self) -> dict:
        return {
            **self.net.stats(),
            "mean_mfe_pct": (round(self.mfe_total / self.mfe_legs, 4)
                             if self.mfe_legs else None),
            "mean_giveback_pct": (
                round(self.giveback_total / self.giveback_legs, 4)
                if self.giveback_legs else None
            ),
            "legs_that_reached_profit": self.reached,
            "mean_retained_share_of_peak_pct": (
                round(self.retained_total / self.retained_legs, 2)
                if self.retained_legs else None
            ),
            "returned_to_entry_or_worse_pct": share(
                self.back_to_entry, self.retained_legs,
            ),
            "turned_negative_pct": share(
                self.turned_negative, self.retained_legs,
            ),
            "legs_with_milestones": self.milestone_legs,
            "t1_pct": share(self.milestones[0], self.milestone_legs),
            "t2_pct": share(self.milestones[1], self.milestone_legs),
            "t3_pct": share(self.milestones[2], self.milestone_legs),
        }


def share(hits: int, of: int) -> float | None:
    """A percentage, or None when nothing was measured to take it over."""
    return round(100.0 * hits / of, 2) if of else None


def _rows(con: sqlite3.Connection, channel: str | None) -> sqlite3.Cursor:
    """Every stored horizon of every attributed leg, grouped by leg.

    Ordered by ``leg_id`` so the per-leg best horizon can be taken from a
    single streaming pass: the primary key on ``leg_path`` already provides
    that order, so no sort is materialised.
    """
    sql = (
        "SELECT p.leg_id AS leg_id, p.horizon AS horizon, p.net_pct AS net_pct,"
        " p.mfe_pct AS mfe_pct, p.giveback AS giveback, p.t1 AS t1,"
        " p.t2 AS t2, p.t3 AS t3, a.channel AS channel"
        " FROM leg_path p JOIN leg_attribution a ON a.leg_id = p.leg_id"
        " WHERE a.channel IS NOT NULL"
    )
    args: list[object] = []
    if channel:
        sql += " AND a.channel = ?"
        args.append(channel)
    return con.execute(sql + " ORDER BY p.leg_id", args)


_NO_PEAK: dict = {
    "legs": 0, "mean_peak_pct": None, "mean_giveback_pct": None,
    "mean_time_to_peak_min": None, "legs_with_a_coarse_peak_time": 0,
}


def peak_and_time(con: sqlite3.Connection, channel: str | None) -> dict:
    """Peak, giveback and time-to-peak per channel, aggregated in SQLite.

    ``time_resolution`` is carried through rather than averaged away: a peak
    recovered from stored horizon buckets is an upper bound on when the peak
    happened, so a mean over those is coarser than it looks.
    """
    sql = (
        "SELECT channel, COUNT(*) AS legs, AVG(peak_pct) AS peak,"
        " AVG(giveback_pct) AS giveback, AVG(time_to_peak_min) AS ttp,"
        " SUM(CASE WHEN time_resolution IS NOT NULL THEN 1 ELSE 0 END) AS coarse"
        " FROM leg_attribution WHERE channel IS NOT NULL"
    )
    args: list[object] = []
    if channel:
        sql += " AND channel = ?"
        args.append(channel)
    out: dict[str, dict] = {}
    for row in con.execute(sql + " GROUP BY channel", args):
        out[str(row["channel"])] = {
            "legs": int(row["legs"]),
            "mean_peak_pct": _round(row["peak"]),
            "mean_giveback_pct": _round(row["giveback"]),
            "mean_time_to_peak_min": _round(row["ttp"]),
            "legs_with_a_coarse_peak_time": int(row["coarse"] or 0),
        }
    return out


def _round(value: object) -> float | None:
    return round(float(value), 4) if isinstance(value, (int, float)) else None


def decompose(con: sqlite3.Connection, *, channel: str | None = None) -> dict:
    """Per-horizon economics per channel, plus the per-leg best-horizon spread."""
    by_channel: dict[str, dict[str, HorizonTally]] = {}
    best_counts: dict[str, dict[str, int]] = {}
    close_beaten: dict[str, list[int]] = {}
    leg_id = None
    leg_channel = ""
    horizons: dict[str, float] = {}

    def flush() -> None:
        if not horizons or not leg_channel:
            return
        best = max(horizons.items(), key=lambda kv: (kv[1], -_RANK[kv[0]]))
        counts = best_counts.setdefault(leg_channel, {})
        counts[best[0]] = counts.get(best[0], 0) + 1
        close = horizons.get(SESSION_CLOSE)
        if close is not None:
            tallies = close_beaten.setdefault(leg_channel, [0, 0])
            tallies[1] += 1
            if best[1] > close:
                tallies[0] += 1

    for row in _rows(con, channel):
        this = str(row["leg_id"])
        if this != leg_id:
            flush()
            leg_id, horizons = this, {}
            leg_channel = str(row["channel"] or "")
        horizon = str(row["horizon"])
        if horizon not in _RANK:
            continue
        slot = by_channel.setdefault(leg_channel, {})
        slot.setdefault(horizon, HorizonTally()).add(
            row["net_pct"], row["mfe_pct"], row["giveback"],
            (row["t1"], row["t2"], row["t3"]),
        )
        if isinstance(row["net_pct"], (int, float)):
            horizons[horizon] = float(row["net_pct"])
    flush()

    peaks = peak_and_time(con, channel)
    return {
        "by_channel": {
            name: {
                "peak": peaks.get(name, _NO_PEAK),
                "horizons": [
                    {"horizon": h, **slot[h].stats()}
                    for h in ORDER if h in slot
                ],
                "best_horizon_in_hindsight": _histogram(
                    best_counts.get(name, {}),
                ),
                "legs_where_some_horizon_beat_the_close": (
                    close_beaten.get(name, [0, 0])[0]
                ),
                "legs_with_a_close_figure": (
                    close_beaten.get(name, [0, 0])[1]
                ),
            }
            for name, slot in sorted(by_channel.items())
        },
        "retained_note": (
            "RETAINED_IS_NET_OVER_A_GROSS_PEAK: the retained share divides the "
            "horizon's net result by its gross favourable excursion, so the "
            "round trip is charged in the numerator and not in the peak. A leg "
            "can therefore read as returning more than the whole peak to the "
            "market when the peak never covered its own cost."
        ),
        "hindsight_note": (
            "BEST_HORIZON_IS_HINDSIGHT: the best horizon is chosen per leg "
            "after its whole path is known. A concentrated distribution would "
            "make a fixed time exit worth pre-registering as its own shadow; a "
            "scattered one means no fixed horizon can capture it. Neither "
            "reading is a licence to change the exit."
        ),
    }


def _histogram(counts: dict[str, int]) -> list[dict]:
    total = sum(counts.values())
    return [
        {"horizon": h, "legs": counts[h],
         "share_pct": round(100.0 * counts[h] / total, 2) if total else None}
        for h in ORDER if h in counts
    ]
