"""Phase 35 §12/§13 — CE vs PE, and futures vs options, at the same instant.

Every comparison in this module is between vehicles measured at the **same
observation**. That constraint is the whole method: comparing a CE at 10:04
against a PE at 10:19 compares two different markets and then calls one of them
a mistake.

Two further rules, both from the specification and both enforced in code:

* a side enters a comparison only when it has its own real two-sided book. §12 is
  explicit that CE/PE performance may never be inferred from underlying movement,
  so a missing PE book means the comparison does not exist for that instant — it
  does not mean the PE would have done the opposite of the CE;
* the winner is recorded as ``COUNTERFACTUAL_VEHICLE_COMPARISON`` and nothing
  reads it as a selection rule. §13 forbids using the outcome to decide which
  vehicle "should" have been chosen, because a selector that knows the outcome is
  not a selector. The comparison answers a different and legitimate question:
  *which vehicle was structurally cheaper and which gave more net room*, which is
  knowable at entry and is what the economics research in §19 acts on.
"""
from __future__ import annotations

import time

from app.research.phase35 import (
    CE,
    COUNTERFACTUAL_VEHICLE_COMPARISON,
    FUTURES,
    HORIZONS,
    MEASURED_EXECUTABLE,
    PE,
    REQUIRES_MORE_DATA,
    SESSION_CLOSE,
    paper,
    store,
)

CE_VS_PE = "CE_VS_PE"
FUTURES_VS_OPTIONS = "FUTURES_VS_OPTIONS"

# Below this many comparable instants nothing is reported as a finding. The
# captured store currently holds tens of real two-sided instants, so this floor
# will bind — which is the honest outcome, not a failure of the module.
MIN_COMPARISONS = 30


def _side(legs: dict[str, dict], vehicle: str) -> dict | None:
    leg = legs.get(vehicle)
    if not leg or leg.get("evidence") != MEASURED_EXECUTABLE:
        return None
    if leg.get("entry_price") is None or leg.get("net_pct") is None:
        return None
    return leg


def _cost_pct(leg: dict) -> float | None:
    cost, entry = leg.get("cost_points"), leg.get("entry_price")
    if cost is None or not entry:
        return None
    return round(100.0 * float(cost) / float(entry), 4)


def compare(obs_id: str, legs: dict[str, dict], paths: dict[str, dict]) -> list[dict]:
    """Comparison rows for one observation, at every horizon both sides reach."""
    out: list[dict] = []
    ce, pe, fut = _side(legs, CE), _side(legs, PE), _side(legs, FUTURES)
    horizons = [str(h) for h in HORIZONS] + [SESSION_CLOSE]

    for kind, members in (
        (CE_VS_PE, {CE: ce, PE: pe}),
        (FUTURES_VS_OPTIONS, {FUTURES: fut, CE: ce, PE: pe}),
    ):
        present = {v: leg for v, leg in members.items() if leg is not None}
        if len(present) < 2:
            continue
        for horizon in horizons:
            usable = {}
            for v, leg in present.items():
                snap = paths.get(f"{leg['leg_id']}|{horizon}")
                if snap and snap.get("net_pct") is not None:
                    usable[v] = snap
            if len(usable) < 2:
                continue
            payload = {
                v: {
                    "entry_price": present[v].get("entry_price"),
                    "entry_side": present[v].get("entry_side"),
                    "exit_price": present[v].get("exit_price"),
                    "cost_points": present[v].get("cost_points"),
                    "cost_pct": _cost_pct(present[v]),
                    "gross_pct": snap.get("gross_pct"),
                    "net_pct": snap.get("net_pct"),
                    "mfe_pct": snap.get("mfe_pct"),
                    "mae_pct": snap.get("mae_pct"),
                    "t1": snap.get("t1"),
                    "t2": snap.get("t2"),
                    "hold_minutes": None if horizon == SESSION_CLOSE else float(horizon),
                }
                for v, snap in usable.items()
            }
            best_net = max(payload, key=lambda v: float(payload[v]["net_pct"]))
            cheapest = min(
                (v for v in payload if payload[v]["cost_pct"] is not None),
                key=lambda v: float(payload[v]["cost_pct"]),
                default=None,
            )
            shallowest = max(
                (v for v in payload if payload[v]["mae_pct"] is not None),
                key=lambda v: float(payload[v]["mae_pct"]),
                default=None,
            )
            biggest_move = max(
                (v for v in payload if payload[v]["mfe_pct"] is not None),
                key=lambda v: float(payload[v]["mfe_pct"]),
                default=None,
            )
            out.append({
                "obs_id": obs_id,
                "horizon": horizon,
                "kind": kind,
                "winner": best_net,
                "payload": {
                    "label": COUNTERFACTUAL_VEHICLE_COMPARISON,
                    "vehicles": payload,
                    "highest_net": best_net,
                    "lowest_cost": cheapest,
                    "lowest_drawdown": shallowest,
                    "largest_net_move": biggest_move,
                    "note": (
                        "counterfactual: the winner is measured after the fact and "
                        "is never used to select a vehicle"
                    ),
                },
                "evidence": MEASURED_EXECUTABLE,
            })
    return out


FLUSH_EVERY = 5000


def build(con, *, flush_every: int = FLUSH_EVERY, progress=None,
          resume: bool = False, pause: float = 0.0,
          now: float | None = None) -> dict:
    """Comparison rows over the whole store, and the coverage that bounds them.

    Read one observation at a time and written in batches. Loading every leg and
    every path row first is the same answer but costs memory proportional to the
    whole store, and on a full store that is millions of rows held only to be
    grouped by the observation they already sort by.

    Like the paper stage this walks **session by session and checkpoints each
    one**, so an interrupted pass resumes at the session it stopped in instead of
    redoing the store. A comparison is between vehicles at the same observation
    and reads nothing outside it, so partitioning cannot change a single row.

    ``progress`` is called with ``(done, total)``. The total is known here — the
    distinct observations already in ``paper_leg`` — so this stage can report a
    real fraction rather than a count with no denominator.

    ``pause`` sleeps that many seconds after each session, for the same reason as
    in the paper stage: the box this runs on is also capturing a live session.
    This stage is the cheap half, so the pause is applied per session rather than
    per chunk.
    """
    started = float(time.time() if now is None else now)
    sessions = paper.session_inventory(con)
    done_by_session = store.session_progress(con) if resume else {}
    total = int(
        (con.execute("SELECT COUNT(DISTINCT obs_id) AS n FROM paper_leg")
         .fetchone() or {"n": 0})["n"]
    )
    written = 0
    compared = 0
    seen = 0
    skipped_sessions: list[str] = []
    rebuilt_sessions: list[str] = []
    pending: list[dict] = []
    for sess in sessions:
        prior = done_by_session.get(sess["session"]) if resume else None
        cur = con.execute(
            "SELECT DISTINCT obs_id FROM paper_leg "
            "WHERE entry_ts >= ? AND entry_ts < ? ORDER BY obs_id",
            (sess["start_ts"], sess["end_ts"]),
        )
        obs_ids = [str(r["obs_id"]) for r in cur]
        cur.close()
        if (
            prior
            and prior.get("stage") == store.STAGE_VEHICLE_DONE
            and int(prior.get("observations") or -1) == sess["observations"]
        ):
            payload = prior.get("payload") or {}
            written += int(payload.get("comparison_rows_written") or 0)
            compared += int(payload.get("observations_compared") or 0)
            seen += len(obs_ids)
            skipped_sessions.append(sess["session"])
            if progress is not None:
                progress(seen, total)
            continue
        session_written = 0
        session_compared = 0
        for obs_id in obs_ids:
            vehicles = {
                leg["vehicle"]: leg
                for leg in (
                    dict(r) for r in con.execute(
                        "SELECT * FROM paper_leg WHERE obs_id = ? "
                        "ORDER BY entry_ts", (obs_id,),
                    )
                )
            }
            paths = {
                f"{r['leg_id']}|{r['horizon']}": dict(r)
                for r in con.execute(
                    "SELECT p.* FROM leg_path p JOIN paper_leg l "
                    "ON l.leg_id = p.leg_id WHERE l.obs_id = ?",
                    (obs_id,),
                )
            }
            rows = compare(obs_id, vehicles, paths)
            if rows:
                session_compared += 1
            pending.extend(rows)
            seen += 1
            if flush_every and len(pending) >= flush_every:
                session_written += store.save_comparisons(con, pending)
                pending.clear()
                if progress is not None:
                    progress(seen, total)
        if pending:
            session_written += store.save_comparisons(con, pending)
            pending.clear()
        written += session_written
        compared += session_compared
        # Written after the rows, so an interruption between the two costs this
        # session's comparisons again rather than skipping them.
        store.save_session_progress(
            con, sess["session"], stage=store.STAGE_VEHICLE_DONE,
            observations=sess["observations"],
            payload={
                **((prior or {}).get("payload") or {}),
                "comparison_rows_written": session_written,
                "observations_compared": session_compared,
            },
            now=started,
        )
        rebuilt_sessions.append(sess["session"])
        if progress is not None:
            progress(seen, total)
        if pause > 0:
            time.sleep(float(pause))
    if progress is not None:
        progress(total, total)
    return {
        "comparison_rows_written": written,
        "observations_compared": compared,
        "sessions_rebuilt": rebuilt_sessions,
        "sessions_skipped_already_built": skipped_sessions,
        "session_pause_seconds": float(pause),
        "resumable": True,
        "label": COUNTERFACTUAL_VEHICLE_COMPARISON,
    }


def summary(con) -> dict:
    """Which vehicle won, how often, at the session close — with the floor applied."""
    out: dict[str, dict] = {}
    for kind in (CE_VS_PE, FUTURES_VS_OPTIONS):
        # Counted in SQL: the comparison table is one row per instant per
        # horizon, so reading it whole to count the close rows scales with the
        # store rather than with the answer.
        n_close = int(con.execute(
            "SELECT COUNT(*) AS n FROM vehicle_comparison"
            " WHERE kind = ? AND horizon = ?",
            (kind, SESSION_CLOSE),
        ).fetchone()["n"] or 0)
        wins: dict[str, int] = {}
        for r in con.execute(
            "SELECT winner, COUNT(*) AS n FROM vehicle_comparison"
            " WHERE kind = ? AND horizon = ? AND winner IS NOT NULL"
            " AND winner <> '' GROUP BY winner",
            (kind, SESSION_CLOSE),
        ):
            wins[r["winner"]] = wins.get(r["winner"], 0) + int(r["n"] or 0)
        enough = n_close >= MIN_COMPARISONS
        out[kind] = {
            "comparable_instants": n_close,
            "wins_by_vehicle": dict(sorted(wins.items(), key=lambda kv: -kv[1])),
            "status": MEASURED_EXECUTABLE if enough else REQUIRES_MORE_DATA,
            "floor": MIN_COMPARISONS,
            "note": (
                "both sides had real two-sided books at the same instant"
                if enough else
                f"fewer than {MIN_COMPARISONS} instants where both sides had real "
                f"books; nothing here is a finding yet"
            ),
        }
    return out
