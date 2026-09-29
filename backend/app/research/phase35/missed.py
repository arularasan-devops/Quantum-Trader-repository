"""Phase 35 §11 — opportunities the engine did not take, and what they did next.

The engine's capture rate is the ratio nobody has been able to compute honestly
yet: Phase 33 found 1,149 of 1,201 board minutes carried no engine BUY, but only
30 of those rows had a real quote, so "the engine misses profitable minutes"
stayed unproven rather than disproven. This module computes the ratio the only
way it can be computed — over board observations that had an executable book and
a measured forward path — and reports the coverage beside it so the number can
never be read as more than its sample.

Both directions are counted, deliberately:

``ENGINE_MISSED_WINNER``
    a board opportunity the engine declined that went on to a positive net
    executable result.
``ENGINE_AVOIDED_LOSER``
    a board opportunity the engine declined that went on to a negative one.

Reporting only the first would be an indictment dressed as an analysis. A filter
that declines fifty losers and three winners is doing its job, and §19 says
explicitly that avoiding losses is not the same as being profitable — so both
counts appear, always, and the net of the declined pool is what actually
matters.

Every figure here is counterfactual. The engine did not take these trades, the
money was not made, and no line of this module may be read as foregone profit:
the missed winner's own cost is charged exactly as if it had been taken.
"""
from __future__ import annotations

from app.research.phase35 import (
    ENGINE_AVOIDED_LOSER,
    ENGINE_MISSED_WINNER,
    FULL_MARKET_PAPER,
    GENERAL_MIN_SESSIONS,
    GENERAL_MIN_TRADES,
    MEASURED_EXECUTABLE,
    REQUIRES_MORE_DATA,
    SESSION_CLOSE,
    path,
)

# Why the engine passed. Read from what the engine itself recorded at the time —
# never reconstructed, because a reconstructed reason is an opinion about the
# past written after the outcome is known.
REASON_WAIT = "ENGINE_SAID_WAIT"
REASON_AVOID = "ENGINE_SAID_AVOID"
REASON_NO_SIGNAL = "ENGINE_HAD_NO_SIGNAL"
REASON_UNRECORDED = "ENGINE_REASON_NOT_RECORDED"

_REASONS = {
    "WAIT": REASON_WAIT,
    "AVOID": REASON_AVOID,
    "NO_SIGNAL": REASON_NO_SIGNAL,
}


def why_missed(engine_class: str | None) -> str:
    return _REASONS.get((engine_class or "").upper(), REASON_UNRECORDED)


# The declined pool: a full-market leg whose observation the engine passed on,
# priced on an executable book and resolved. Selected in SQL because the pool is
# as large as the store and the summary below only ever needed counts from it.
_DECLINED = (
    " FROM paper_leg l JOIN raw_observation o ON o.obs_id = l.obs_id"
    " WHERE l.book = ?"
    " AND (o.engine_selected IS NULL OR o.engine_selected = 0)"
    " AND l.evidence = ? AND l.net_pct IS NOT NULL"
)
_DECLINED_ARGS = (FULL_MARKET_PAPER, MEASURED_EXECUTABLE)


def iter_rows(con):
    """Stream one row per declined board opportunity with a measured outcome.

    Streaming rather than returning a list: the full-market book is millions of
    legs on a real store, and the caller only ever folds them into counts.
    """
    # Time to T1 comes from the earliest horizon whose row already had T1, which
    # is a measured bound rather than an interpolated minute. A leg whose only
    # T1 row is the session close has no measured minute, which is why the
    # close horizon is excluded here and reported as ``None``.
    sql = (
        "SELECT l.obs_id, l.leg_id, l.instrument, l.entry_ts, l.vehicle,"
        " l.direction, l.entry_price, l.entry_side, l.gross_pct, l.net_pct,"
        " o.session AS session, o.opportunity_type AS opportunity_type,"
        " o.engine_class AS engine_class,"
        " c.mfe_pct AS mfe_pct, c.mae_pct AS mae_pct, c.t1 AS close_t1,"
        " t.t1_min AS t1_min"
        + _DECLINED.replace(
            " WHERE l.book = ?",
            " LEFT JOIN leg_path c ON c.leg_id = l.leg_id AND c.horizon = ?"
            " LEFT JOIN (SELECT leg_id, MIN(CAST(horizon AS REAL)) AS t1_min"
            "   FROM leg_path WHERE t1 AND horizon <> ? GROUP BY leg_id) t"
            " ON t.leg_id = l.leg_id"
            " WHERE l.book = ?",
            1,
        )
    )
    args = (SESSION_CLOSE, SESSION_CLOSE, *_DECLINED_ARGS)
    for r in con.execute(sql, args):
        net = float(r["net_pct"])
        yield {
            "obs_id": r["obs_id"],
            "leg_id": r["leg_id"],
            "instrument": r["instrument"],
            "session": r["session"],
            "ts": r["entry_ts"],
            "opportunity_type": r["opportunity_type"],
            "vehicle": r["vehicle"],
            "direction": r["direction"],
            "entry_price": r["entry_price"],
            "entry_side": r["entry_side"],
            "premium": r["entry_price"],
            "mfe_pct": r["mfe_pct"],
            "mae_pct": r["mae_pct"],
            "t1_reached": bool(r["close_t1"]),
            "time_to_t1_min": (
                None if r["t1_min"] is None else float(r["t1_min"])
            ),
            "gross_pct": r["gross_pct"],
            "net_pct": net,
            "label": ENGINE_MISSED_WINNER if net > 0 else ENGINE_AVOIDED_LOSER,
            "why_engine_missed": why_missed(r["engine_class"]),
            "evidence": MEASURED_EXECUTABLE,
            "counterfactual": True,
        }


def rows(con) -> list[dict]:
    """One row per declined board opportunity with a measured executable outcome."""
    return list(iter_rows(con))


def summary(con) -> dict:
    """Capture rate and the two-sided verdict on what the engine declined.

    Counted in SQL over the declined pool rather than over a materialised list of
    it: the same figures, without holding the book in memory.
    """
    head = con.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT CASE WHEN o.session IS NOT NULL"
        "   AND o.session <> '' THEN o.session END) AS sessions,"
        " SUM(CASE WHEN l.net_pct > 0 THEN 1 ELSE 0 END) AS winners,"
        " SUM(l.net_pct) AS net_sum" + _DECLINED,
        _DECLINED_ARGS,
    ).fetchone()
    total_resolved = int(head["n"] or 0)
    winners_n = int(head["winners"] or 0)
    sessions = int(head["sessions"] or 0)
    by_reason = _tally(
        con,
        "SELECT o.engine_class AS k, COUNT(*) AS n" + _DECLINED
        + " GROUP BY o.engine_class",
        _DECLINED_ARGS,
        key=why_missed,
    )
    by_vehicle = _tally(
        con,
        "SELECT l.vehicle AS k, COUNT(*) AS n" + _DECLINED
        + " AND l.net_pct > 0 GROUP BY l.vehicle",
        _DECLINED_ARGS,
    )
    engine_legs_n = int(con.execute(
        "SELECT COUNT(*) AS n FROM paper_leg"
        " WHERE book = 'CURRENT_ENGINE_PAPER' AND net_pct IS NOT NULL"
    ).fetchone()["n"] or 0)
    enough = (
        total_resolved >= GENERAL_MIN_TRADES and sessions >= GENERAL_MIN_SESSIONS
    )
    net_of_declined = (
        round(float(head["net_sum"] or 0.0) / total_resolved, 4)
        if total_resolved else None
    )
    considered = total_resolved + engine_legs_n
    return {
        "engine_resolved_legs": engine_legs_n,
        "declined_with_measured_outcome": total_resolved,
        "sessions": sessions,
        "missed_winners": winners_n,
        "avoided_losers": total_resolved - winners_n,
        "engine_capture_rate_pct": (
            round(100.0 * engine_legs_n / considered, 2) if considered else None
        ),
        "mean_net_pct_of_declined_pool": net_of_declined,
        "status": MEASURED_EXECUTABLE if enough else REQUIRES_MORE_DATA,
        "floors": {"trades": GENERAL_MIN_TRADES, "sessions": GENERAL_MIN_SESSIONS},
        "by_reason": by_reason,
        "by_vehicle": by_vehicle,
        "note": (
            "counterfactual only: these trades were not taken and their costs are "
            "charged as if they had been. Missed winners are meaningless without "
            "the avoided losers beside them"
        ),
    }


def _counts(items: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for row in items:
        k = str(row.get(key))
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _tally(con, sql: str, args, key=None) -> dict:
    """A grouped count, with distinct raw values folded onto the same label."""
    out: dict[str, int] = {}
    for r in con.execute(sql, args):
        k = str(key(r["k"])) if key else str(r["k"])
        out[k] = out.get(k, 0) + int(r["n"] or 0)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def sessions_covered(con) -> int:
    """Distinct sessions with at least one observation — the §16 checkpoint clock."""
    row = con.execute(
        "SELECT COUNT(DISTINCT session) AS n FROM raw_observation "
        "WHERE session IS NOT NULL"
    ).fetchone()
    if row and int(row["n"] or 0):
        return int(row["n"])
    days = {
        path.session_date(float(r["ts"]))
        for r in con.execute("SELECT ts FROM raw_observation").fetchall()
    }
    return len(days)
