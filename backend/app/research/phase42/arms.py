"""Phase 42 — the arms: what each candidate multiple would have admitted.

One pass over the store, session by session, accumulating counters rather than
rows. The store this has to run on holds two million legs and 6.7 million path
samples, and two earlier readers in this project took the machine down by
materialising what they read, so nothing here keeps a list whose length grows
with the number of legs.

Each arm is scored on two pools, because the live gate has two behaviours and
conflating them would measure a gate nobody runs:

``as_implemented``
    what the gate does — admit the leg when the ratio clears the multiple, and
    also admit it when the economics could not be measured at all.
``measured_only``
    the same arm over the legs whose ratio was measurable, which is the only
    pool where the arms actually differ from one another.
"""
from __future__ import annotations

import sqlite3

from app.research.phase42 import (
    EX_ANTE_MEASURED,
    THRESHOLDS,
    UNKNOWN_BASIS,
)
from app.research.phase42.exante import admits, leg_delta, ratio, trailing_ranges


class Tally:
    """Running net-percentage statistics for one pool. Constant memory."""

    def __init__(self) -> None:
        self.legs = 0
        self.total = 0.0
        self.wins = 0
        self.profit = 0.0
        self.loss = 0.0

    def add(self, net: float) -> None:
        self.legs += 1
        self.total += net
        if net > 0:
            self.wins += 1
            self.profit += net
        else:
            self.loss += -net

    def merge(self, other: Tally) -> None:
        self.legs += other.legs
        self.total += other.total
        self.wins += other.wins
        self.profit += other.profit
        self.loss += other.loss

    def stats(self) -> dict:
        if not self.legs:
            return {"legs": 0, "mean_net_pct": None, "win_pct": None,
                    "profit_factor": None, "sum_net_pct": None}
        return {
            "legs": self.legs,
            "mean_net_pct": round(self.total / self.legs, 4),
            "win_pct": round(100.0 * self.wins / self.legs, 2),
            "profit_factor": (round(self.profit / self.loss, 3)
                              if self.loss > 0 else None),
            "sum_net_pct": round(self.total, 2),
        }


class Arm:
    """One candidate multiple, and the pools it would have split the book into."""

    def __init__(self, multiple: float) -> None:
        self.multiple = multiple
        self.admitted = Tally()
        self.refused = Tally()

    def observe(self, net: float, value: float | None) -> None:
        verdict = admits(value, self.multiple)
        if verdict is None:
            return
        (self.admitted if verdict else self.refused).add(net)

    def merge(self, other: Arm) -> None:
        self.admitted.merge(other.admitted)
        self.refused.merge(other.refused)


class SessionResult:
    """Everything one session contributes, as counters."""

    def __init__(self, session: str) -> None:
        self.session = session
        self.arms: dict[float, Arm] = {m: Arm(m) for m in THRESHOLDS}
        self.measured = Tally()
        self.unmeasured = Tally()
        self.reasons: dict[str, int] = {}
        self.range_sources: dict[str, int] = {}
        # How the round trip the ratio divides by was established, per measured
        # leg. Kept per session because the cut is chronological: a store whose
        # cost basis changed part-way through can put one basis on each side of
        # it, and an arm chosen on one and scored on the other tests nothing.
        self.cost_bases: dict[str, int] = {}

    def merge(self, other: SessionResult) -> None:
        for multiple, arm in self.arms.items():
            arm.merge(other.arms[multiple])
        self.measured.merge(other.measured)
        self.unmeasured.merge(other.unmeasured)
        for key, n in other.reasons.items():
            self.reasons[key] = self.reasons.get(key, 0) + n
        for key, n in other.range_sources.items():
            self.range_sources[key] = self.range_sources.get(key, 0) + n
        for key, n in other.cost_bases.items():
            self.cost_bases[key] = self.cost_bases.get(key, 0) + n

    def arm_rows(self) -> list[dict]:
        rows = []
        for multiple in THRESHOLDS:
            arm = self.arms[multiple]
            pool = Tally()
            pool.merge(arm.admitted)
            pool.merge(self.unmeasured)
            rows.append({
                "multiple": multiple,
                "measured_only": arm.admitted.stats(),
                "refused": arm.refused.stats(),
                "as_implemented": pool.stats(),
            })
        return rows

    def payload(self) -> dict:
        return {
            "session": self.session,
            "measured": self.measured.stats(),
            "unmeasured": self.unmeasured.stats(),
            "unmeasured_reasons": dict(sorted(self.reasons.items())),
            "range_sources": dict(sorted(self.range_sources.items())),
            "cost_bases": dict(sorted(self.cost_bases.items())),
            "arms": self.arm_rows(),
        }


def sessions(con: sqlite3.Connection) -> list[str]:
    """Capture dates in order. A session is the unit a threshold is judged on."""
    return [
        str(r["session"]) for r in con.execute(
            "SELECT DISTINCT session FROM raw_observation"
            " WHERE session IS NOT NULL ORDER BY session",
        )
    ]


def _legs(
    con: sqlite3.Connection, session: str, book: str | None,
    instrument: str | None,
) -> sqlite3.Cursor:
    """Resolved legs of one session with the quote that priced their entry.

    The join is to the quote of the leg's own vehicle at its own observation, so
    the delta is the one that existed at the decision instant rather than the
    nearest one found later.
    """
    sql = (
        "SELECT l.vehicle AS vehicle, l.instrument AS instrument,"
        " l.entry_ts AS entry_ts, l.entry_price AS entry_price,"
        " l.cost_points AS cost_points, l.net_pct AS net_pct,"
        " l.cost_evidence AS cost_evidence, q.delta AS delta"
        " FROM paper_leg l"
        " JOIN raw_observation o ON o.obs_id = l.obs_id"
        " LEFT JOIN raw_quote q ON q.obs_id = l.obs_id AND q.vehicle = l.vehicle"
        " WHERE o.session = ? AND l.resolved = 1 AND l.net_pct IS NOT NULL"
        " AND l.entry_price IS NOT NULL AND l.entry_price <> 0"
    )
    args: list[object] = [session]
    if book:
        sql += " AND l.book = ?"
        args.append(book)
    if instrument:
        sql += " AND l.instrument = ?"
        args.append(instrument)
    return con.execute(sql + " ORDER BY l.entry_ts", args)


def one_session(
    con: sqlite3.Connection, session: str, *, book: str | None = None,
    instrument: str | None = None,
) -> SessionResult:
    """Score every arm over one session. Bounded by the session's instruments."""
    out = SessionResult(session)
    ranges = trailing_ranges(con, session)
    for row in _legs(con, session, book, instrument):
        net = float(row["net_pct"])
        vehicle = str(row["vehicle"] or "")
        instrument_name = str(row["instrument"] or "")
        series = ranges.get(instrument_name)
        span, span_reason = (series.before(float(row["entry_ts"]))
                             if series is not None else (None, None))
        measured = ratio(
            delta=leg_delta(vehicle, row["delta"]),
            trailing_range_points=span,
            cost_points=row["cost_points"],
            entry_price=row["entry_price"],
        )
        if measured["evidence"] == EX_ANTE_MEASURED:
            out.measured.add(net)
            basis = str(row["cost_evidence"] or UNKNOWN_BASIS)
            out.cost_bases[basis] = out.cost_bases.get(basis, 0) + 1
            if series is not None:
                key = series.source
                out.range_sources[key] = out.range_sources.get(key, 0) + 1
        else:
            out.unmeasured.add(net)
            reason = measured["reason"] or span_reason or "UNKNOWN"
            out.reasons[reason] = out.reasons.get(reason, 0) + 1
        for arm in out.arms.values():
            arm.observe(net, measured["ratio"])
    return out


def sweep(
    con: sqlite3.Connection, *, book: str | None = None,
    instrument: str | None = None,
) -> list[SessionResult]:
    """Every session, in date order, each scored independently."""
    return [
        one_session(con, session, book=book, instrument=instrument)
        for session in sessions(con)
    ]


def pooled(results: list[SessionResult]) -> SessionResult:
    """All sessions summed. Descriptive: it is not a test of anything."""
    out = SessionResult("ALL_SESSIONS")
    for result in results:
        out.merge(result)
    return out
