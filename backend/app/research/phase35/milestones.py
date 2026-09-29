"""Phase 35 §16 — the checkpoint clock and the aggregate the dashboard reads.

§16 sets three bars and this module refuses to blur them:

* **Checkpoint 1** at 20 sessions is an *observation* checkpoint. It reports; it
  decides nothing. A positive number here means the plumbing works, not that the
  opportunity is real;
* **Checkpoint 2** at 100 resolved trades and 50 independent sessions is when a
  general result may be treated as serious evidence;
* the relative-value candidate keeps its stricter gate — 200 trades, 60 sessions
  and 80% measured executable-cost coverage — and is never graded on the general
  floor just because the general floor happens to be met first.

Sessions are counted as *independent sessions*, not as rows. Forty legs from one
morning is one session, and a book whose 100 trades came from three days has not
met the bar however good the number looks. This is the arithmetic that Phase 33
had to walk back once already, so it is computed in one place and read from
there.

Every aggregate is computed twice — once per book — because ``CURRENT_ENGINE_PAPER``
and ``FULL_MARKET_PAPER`` are different populations and pooling them would let the
much larger board pool drown the engine's own record, which is the record the
question in §24 is actually about.
"""
from __future__ import annotations

from app.research.phase35 import (
    CHECKPOINT1_SESSIONS,
    CURRENT_ENGINE_PAPER,
    FULL_MARKET_PAPER,
    GENERAL_MIN_SESSIONS,
    GENERAL_MIN_TRADES,
    HORIZONS,
    MEASURED_EXECUTABLE,
    MEASURED_EXECUTABLE_SPEC_LOT,
    PAIR_MIN_COST_COVERAGE_PCT,
    PAIR_MIN_SESSIONS,
    PAIR_MIN_TRADES,
    REQUIRES_MORE_DATA,
    SESSION_CLOSE,
    UNMEASURED,
    missed,
    path,
)


def _session_of(leg: dict, sessions: dict[str, str | None]) -> str:
    return sessions.get(leg["obs_id"]) or path.session_date(float(leg["entry_ts"]))


# The session a leg belongs to is the observation's own session where it has one
# and the session date of its entry otherwise. It is needed inside SQL because
# counting distinct sessions in Python means holding every leg in memory.
_SESSION_SQL = (
    "CASE WHEN o.session IS NULL OR o.session = '' "
    "THEN p35_session(l.entry_ts) ELSE o.session END"
)


def _with_session_fn(con):
    """Register :func:`path.session_date` as a SQL function on this connection."""
    con.create_function("p35_session", 1, path.session_date)
    return con


def _leg_scope(book: str | None, f: dict) -> tuple[str, list]:
    """The FROM/WHERE that selects the filtered legs, without reading any.

    Every metric below is an aggregate over this same scope, computed by SQLite.
    It used to be computed in Python over ``store.legs`` plus dictionaries of
    every observation, every ``leg_path`` row and every ``leg_attribution`` row —
    on this store that is 2m legs and 6.7m path rows held at once, which took the
    machine into swap and killed the whole VM. The arithmetic is unchanged; only
    where it happens is.
    """
    sql = (
        " FROM paper_leg l LEFT JOIN raw_observation o ON o.obs_id = l.obs_id"
        " WHERE 1=1"
    )
    args: list = []
    if book:
        sql += " AND l.book = ?"
        args.append(book)
    if f.get("instrument"):
        sql += " AND l.instrument = ?"
        args.append(f["instrument"])
    if f.get("vehicle"):
        sql += " AND l.vehicle = ?"
        args.append(f["vehicle"])
    if f.get("source"):
        sql += " AND o.source = ?"
        args.append(str(f["source"]).upper())
    if f.get("setup"):
        sql += " AND o.opportunity_type = ?"
        args.append(str(f["setup"]).upper())
    if f.get("session"):
        sql += f" AND {_SESSION_SQL} = ?"
        args.append(f["session"])
    return sql, args


_RESOLVED = "l.entry_price IS NOT NULL AND l.net_pct IS NOT NULL"


def aggregate(con, *, book: str, filters: dict | None = None) -> dict:
    """The §20 metric block for one book, over the legs matching ``filters``."""
    f = filters or {}
    _with_session_fn(con)
    scope, args = _leg_scope(book, f)

    head = con.execute(
        "SELECT COUNT(*) AS eligible,"
        " COUNT(DISTINCT l.obs_id) AS opportunities,"
        f" COUNT(DISTINCT {_SESSION_SQL}) AS sessions,"
        " SUM(CASE WHEN l.entry_price IS NOT NULL THEN 1 ELSE 0 END) AS entered,"
        f" SUM(CASE WHEN {_RESOLVED} THEN 1 ELSE 0 END) AS resolved,"
        f" SUM(CASE WHEN {_RESOLVED} THEN l.net_pct ELSE 0 END) AS net_sum,"
        f" SUM(CASE WHEN {_RESOLVED} AND l.net_pct > 0 THEN l.net_pct ELSE 0 END)"
        " AS gain,"
        f" SUM(CASE WHEN {_RESOLVED} AND l.net_pct < 0 THEN -l.net_pct ELSE 0 END)"
        " AS loss,"
        f" SUM(CASE WHEN {_RESOLVED} AND l.net_pct > 0 THEN 1 ELSE 0 END) AS wins"
        + scope,
        args,
    ).fetchone()

    eligible = int(head["eligible"] or 0)
    entered = int(head["entered"] or 0)
    resolved = int(head["resolved"] or 0)
    nets_n = resolved
    net_sum = float(head["net_sum"] or 0.0)
    gain, loss = float(head["gain"] or 0.0), float(head["loss"] or 0.0)
    wins = int(head["wins"] or 0)

    # The session-close row of each resolved leg: T1 is counted over every
    # resolved leg (a leg with no close row simply did not reach T1), while the
    # excursion means are counted only where they were measured.
    close = con.execute(
        "SELECT SUM(CASE WHEN p.t1 THEN 1 ELSE 0 END) AS t1s,"
        " SUM(CASE WHEN p.mfe_pct IS NOT NULL THEN p.mfe_pct ELSE 0 END) AS mfe_sum,"
        " SUM(CASE WHEN p.mfe_pct IS NOT NULL THEN 1 ELSE 0 END) AS mfe_n,"
        " SUM(CASE WHEN p.mae_pct IS NOT NULL THEN p.mae_pct ELSE 0 END) AS mae_sum,"
        " SUM(CASE WHEN p.mae_pct IS NOT NULL THEN 1 ELSE 0 END) AS mae_n"
        + scope.replace(
            " WHERE 1=1",
            " LEFT JOIN leg_path p ON p.leg_id = l.leg_id AND p.horizon = ?"
            " WHERE 1=1",
            1,
        )
        + f" AND {_RESOLVED}",
        [SESSION_CLOSE, *args],
    ).fetchone()

    give = con.execute(
        "SELECT SUM(a.giveback_pct) AS s, COUNT(a.giveback_pct) AS n"
        + scope.replace(
            " WHERE 1=1",
            " LEFT JOIN leg_attribution a ON a.leg_id = l.leg_id WHERE 1=1",
            1,
        )
        + f" AND {_RESOLVED}",
        args,
    ).fetchone()
    give_n = int(give["n"] or 0)

    mfe_n, mae_n = int(close["mfe_n"] or 0), int(close["mae_n"] or 0)
    return {
        "book": book,
        "filters": {k: v for k, v in f.items() if v},
        "market_opportunities": int(head["opportunities"] or 0),
        "paper_eligible": eligible,
        "paper_entered": entered,
        "paper_resolved": resolved,
        "unresolved": entered - resolved,
        "sessions": int(head["sessions"] or 0),
        "net_pnl_pct_sum": round(net_sum, 4) if nets_n else None,
        "net_pnl_pct_mean": round(net_sum / nets_n, 4) if nets_n else None,
        "profit_factor": round(gain / loss, 3) if loss > 0 else None,
        "win_pct": round(100.0 * wins / nets_n, 2) if nets_n else None,
        "t1_pct": (
            round(100.0 * int(close["t1s"] or 0) / nets_n, 2) if nets_n else None
        ),
        "mfe_pct_mean": (
            round(float(close["mfe_sum"] or 0.0) / mfe_n, 4) if mfe_n else None
        ),
        "mae_pct_mean": (
            round(float(close["mae_sum"] or 0.0) / mae_n, 4) if mae_n else None
        ),
        "giveback_pct_mean": (
            round(float(give["s"] or 0.0) / give_n, 4) if give_n else None
        ),
        "best_hold_window": best_hold_window(con, book=book, filters=f),
        "evidence": MEASURED_EXECUTABLE if resolved else UNMEASURED,
        "paper_only": True,
    }


def best_hold_window(con, *, book: str | None = None,
                     filters: dict | None = None) -> dict:
    """Which horizon had the highest mean net — described, never armed.

    This is the horizon that *was* best on the legs collected so far. It is not a
    recommendation and nothing reads it as an exit: Phase 33 found the apparent
    best window flipping sign between development and holdout, which is what an
    accident looks like when it is tested honestly.
    """
    _with_session_fn(con)
    scope, args = _leg_scope(book, filters or {})
    counted = {
        str(r["horizon"]): (int(r["n"] or 0), float(r["s"] or 0.0))
        for r in con.execute(
            "SELECT p.horizon AS horizon, COUNT(p.net_pct) AS n,"
            " SUM(p.net_pct) AS s"
            + scope.replace(
                " WHERE 1=1",
                " JOIN leg_path p ON p.leg_id = l.leg_id WHERE 1=1",
                1,
            )
            + f" AND {_RESOLVED} GROUP BY p.horizon",
            args,
        )
    }
    rows = []
    for horizon in [str(h) for h in HORIZONS] + [SESSION_CLOSE]:
        n, total = counted.get(horizon, (0, 0.0))
        if n:
            rows.append({
                "horizon": horizon,
                "trades": n,
                "mean_net_pct": round(total / n, 4),
            })
    if not rows:
        return {"horizon": None, "status": REQUIRES_MORE_DATA, "by_horizon": []}
    best = max(rows, key=lambda r: r["mean_net_pct"])
    return {
        "horizon": best["horizon"],
        "mean_net_pct": best["mean_net_pct"],
        "trades": best["trades"],
        "by_horizon": rows,
        "status": (
            MEASURED_EXECUTABLE if best["trades"] >= GENERAL_MIN_TRADES
            else REQUIRES_MORE_DATA
        ),
        "armed": False,
        "note": (
            "descriptive only: the best window on collected legs, not an exit rule"
        ),
    }


def cost_coverage_pct(con, *, book: str) -> float | None:
    """Share of entered legs whose cost was measured on an executable book.

    A cost computed from a registry lot size is deliberately NOT counted here.
    It is a usable number — the multiplier is a published contract property, not
    a guess at a price — but the gate this feeds is the strict one, and a
    contract whose lot size the feed never stated is a contract the capture did
    not fully see. :func:`spec_lot_cost_pct` reports that share separately so
    the gap stays visible instead of being averaged into coverage.
    """
    row = con.execute(
        "SELECT COUNT(*) AS n,"
        " SUM(CASE WHEN cost_evidence = ? THEN 1 ELSE 0 END) AS measured"
        " FROM paper_leg WHERE book = ? AND entry_price IS NOT NULL",
        (MEASURED_EXECUTABLE, book),
    ).fetchone()
    n = int(row["n"] or 0)
    if not n:
        return None
    return round(100.0 * int(row["measured"] or 0) / n, 2)


def spec_lot_cost_pct(con, *, book: str) -> float | None:
    """Share of entered legs costed on an executable fill with a registry lot."""
    row = con.execute(
        "SELECT COUNT(*) AS n,"
        " SUM(CASE WHEN cost_evidence = ? THEN 1 ELSE 0 END) AS spec"
        " FROM paper_leg WHERE book = ? AND entry_price IS NOT NULL",
        (MEASURED_EXECUTABLE_SPEC_LOT, book),
    ).fetchone()
    n = int(row["n"] or 0)
    if not n:
        return None
    return round(100.0 * int(row["spec"] or 0) / n, 2)


def checkpoints(con) -> dict:
    """Where each book stands against the three §16 bars."""
    sessions = missed.sessions_covered(con)
    out = {"sessions_observed": sessions, "books": {}}
    for book in (CURRENT_ENGINE_PAPER, FULL_MARKET_PAPER):
        agg = aggregate(con, book=book)
        resolved, book_sessions = agg["paper_resolved"], agg["sessions"]
        coverage = cost_coverage_pct(con, book=book)
        general_met = (
            resolved >= GENERAL_MIN_TRADES and book_sessions >= GENERAL_MIN_SESSIONS
        )
        pair_met = (
            resolved >= PAIR_MIN_TRADES
            and book_sessions >= PAIR_MIN_SESSIONS
            and coverage is not None
            and coverage >= PAIR_MIN_COST_COVERAGE_PCT
        )
        out["books"][book] = {
            "resolved_trades": resolved,
            "independent_sessions": book_sessions,
            "measured_cost_coverage_pct": coverage,
            # Costed on an executable fill, with the multiplier read from the
            # contract registry rather than from the feed. Reported, never
            # folded into the coverage the gate reads.
            "contract_spec_lot_cost_pct": spec_lot_cost_pct(con, book=book),
            "checkpoint1_20_sessions": {
                "met": sessions >= CHECKPOINT1_SESSIONS,
                "sessions": sessions,
                "required": CHECKPOINT1_SESSIONS,
                "decides_nothing": True,
                "note": "observation checkpoint only; no promotion may follow it",
            },
            "checkpoint2_general_evidence": {
                "met": general_met,
                "required_trades": GENERAL_MIN_TRADES,
                "required_sessions": GENERAL_MIN_SESSIONS,
            },
            "relative_value_gate": {
                "met": pair_met,
                "required_trades": PAIR_MIN_TRADES,
                "required_sessions": PAIR_MIN_SESSIONS,
                "required_cost_coverage_pct": PAIR_MIN_COST_COVERAGE_PCT,
                "note": (
                    "the stricter gate is never replaced by the general floor, and "
                    "meeting it permits reconsideration only — not promotion"
                ),
            },
            "status": MEASURED_EXECUTABLE if general_met else REQUIRES_MORE_DATA,
        }
    return out
