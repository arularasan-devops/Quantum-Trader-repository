"""Phase 46 — the two arms, once outcomes exist.

Arm A is the production call's own paper leg: the vehicle the engine selected,
at the instants where it read a buy. Arm B is the same legs with the ones the
overlay did not support removed. So B is a **subset of A**, not a second
strategy, and the difference between them is a description of which legs the
overlay would have stood beside — not a test of one approach against another.
That sentence is on every payload this module produces, because a two-column
table invites exactly the reading it rules out.

Nothing here can influence an admission. It reads the outcome table, which is
written by the Phase 45 resolver a quarter of an hour after the instant, and no
module in the admission path imports this one — the smoke checks the direction
of that dependency rather than trusting it.

The metrics are the ones asked for and no more: resolved legs, net, expectancy,
profit factor, win rate, max drawdown, average cost, MFE, MAE and giveback.
Below :data:`MIN_RESOLVED_FOR_A_COMPARISON` resolved legs an arm is printed with
its sample and no comparison is drawn. That is a display rule, not a promotion
gate — there is no promotion in this phase to reach.
"""
from __future__ import annotations

import os

from app.research.phase45 import store as p45store
from app.research.phase46 import (
    ARM_SIGNAL_ONLY,
    ARM_SIGNAL_PLUS_OVERLAY,
    INSUFFICIENT_SAMPLE,
    MIN_RESOLVED_FOR_A_COMPARISON,
    NOT_A_PROMOTION,
    PAPER_ONLY,
    SHARED_LEGS,
    SUPPORTED,
    VERSION,
)
from app.research.phase46 import store as store_mod

# The paper legs that count as resolved. A leg still open has no net to add and
# must not be counted as a flat one, which would dilute both arms by the same
# wrong number and make the arms look more alike than they are.
RESOLVED = "RESOLVED"


def _legs(limit: int) -> list[dict]:
    """Resolved paper legs from the Phase 45 journal, oldest first.

    Read-only: the shadow journal is opened through the accessor Phase 45 owns,
    and this module has no INSERT of any kind. Ordered by decision instant
    because drawdown is a path, not a set.
    """
    path = p45store.db_path()
    if not os.path.exists(path):
        return []
    con = p45store.open_read_only(path)
    try:
        rows = con.execute(
            "SELECT e.event_id, e.obs_id, e.decision_ts, e.session,"
            " e.instrument, e.vehicle, e.contract, e.production_signal,"
            " e.engine_class, e.engine_selected_vehicle, e.definition,"
            " o.outcome_status, o.net_pnl_points, o.net_pct, o.gross_pct,"
            " o.cost_points, o.mfe_pct, o.mae_pct, o.giveback_pct,"
            " o.hold_minutes"
            " FROM shadow_event e"
            " JOIN paper_outcome o ON o.event_id = e.event_id"
            " WHERE o.outcome_status = ? AND o.net_pct IS NOT NULL"
            " ORDER BY e.decision_ts ASC LIMIT ?",
            (RESOLVED, max(1, min(int(limit), 200000))),
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def _overlay_states(event_ids: list[str]) -> dict[str, str]:
    """The overlay state each of those events was journalled with.

    Keyed on the Phase 45 event id, which is why the overlay row carries it:
    the join is on identity, never on a nearby timestamp.
    """
    path = store_mod.db_path()
    if not os.path.exists(path) or not event_ids:
        return {}
    con = store_mod.connect(path)
    try:
        out: dict[str, str] = {}
        chunk = 400
        for start in range(0, len(event_ids), chunk):
            batch = event_ids[start:start + chunk]
            marks = ",".join("?" for _ in batch)
            rows = con.execute(
                f"SELECT event_id, overlay_state FROM overlay_event"
                f" WHERE event_id IN ({marks})",
                batch,
            ).fetchall()
            for row in rows:
                out[str(row["event_id"])] = str(row["overlay_state"])
        return out
    finally:
        con.close()


def _num(value: object) -> float | None:
    """A float, or None. A missing metric is missing, not zero."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def drawdown(series: list[float]) -> float | None:
    """The deepest peak-to-trough fall of the cumulative net, in the same unit.

    Computed on the legs in decision order, which is the only order in which a
    drawdown means anything. Returned as a non-negative magnitude; zero means
    the cumulative curve never fell, not that it was never measured.
    """
    if not series:
        return None
    total = 0.0
    peak = 0.0
    worst = 0.0
    for value in series:
        total += value
        peak = max(peak, total)
        worst = min(worst, total - peak)
    return abs(worst)


def metrics(legs: list[dict]) -> dict:
    """The metric set for one arm. Every figure names the sample it came from.

    Profit factor is left as None when there are no losing legs rather than
    reported as infinity or as a large number: an arm with no losses has not
    demonstrated a ratio, it has demonstrated a short sample.
    """
    nets = [n for n in (_num(r.get("net_pct")) for r in legs) if n is not None]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    gains = sum(wins)
    pains = abs(sum(losses))
    costs = [
        c for c in (_num(r.get("cost_points")) for r in legs) if c is not None
    ]
    points = [
        p for p in (_num(r.get("net_pnl_points")) for r in legs)
        if p is not None
    ]
    return {
        "resolved_trades": len(legs),
        "legs_with_net": len(nets),
        "net_pct_total": sum(nets) if nets else None,
        "net_points_total": sum(points) if points else None,
        "expectancy_net_pct": _mean(nets),
        "profit_factor": (gains / pains) if pains > 0 else None,
        "profit_factor_note": (
            None if pains > 0
            else "NO_LOSING_LEG_IN_THIS_SAMPLE_SO_NO_RATIO_IS_DEFINED"
        ),
        "win_rate_pct": (100.0 * len(wins) / len(nets)) if nets else None,
        "wins": len(wins),
        "losses": len(losses),
        "flat": len(nets) - len(wins) - len(losses),
        "max_drawdown_net_pct": drawdown(nets),
        "avg_cost_points": _mean(costs),
        "avg_mfe_pct": _mean([
            v for v in (_num(r.get("mfe_pct")) for r in legs) if v is not None
        ]),
        "avg_mae_pct": _mean([
            v for v in (_num(r.get("mae_pct")) for r in legs) if v is not None
        ]),
        "avg_giveback_pct": _mean([
            v for v in (_num(r.get("giveback_pct")) for r in legs)
            if v is not None
        ]),
        "avg_hold_minutes": _mean([
            v for v in (_num(r.get("hold_minutes")) for r in legs)
            if v is not None
        ]),
        "first_ts": legs[0].get("decision_ts") if legs else None,
        "last_ts": legs[-1].get("decision_ts") if legs else None,
        "sessions": len({
            str(r.get("session")) for r in legs if r.get("session")
        }),
        "sample_label": (
            INSUFFICIENT_SAMPLE
            if len(legs) < MIN_RESOLVED_FOR_A_COMPARISON else "SAMPLE_REPORTED"
        ),
    }


def arms(legs: list[dict], states: dict[str, str]) -> dict:
    """Arm A, arm B, and what separates them.

    Arm A is the production engine's own selected vehicle at instants it read a
    buy — the closest paper analogue of following the current board. Arm B
    keeps the legs whose overlay row said SUPPORTED at the instant, and is
    therefore always a subset of A.
    """
    arm_a = [
        r for r in legs
        if (str(r.get("production_signal") or "").upper() == "BUY")
        and (
            r.get("engine_selected_vehicle") is None
            or str(r.get("engine_selected_vehicle")) == str(r.get("vehicle"))
        )
    ]
    arm_b = [r for r in arm_a if states.get(str(r.get("event_id"))) == SUPPORTED]
    kept = {str(r.get("event_id")) for r in arm_b}
    dropped = [r for r in arm_a if str(r.get("event_id")) not in kept]
    return {
        ARM_SIGNAL_ONLY: {
            "arm": ARM_SIGNAL_ONLY,
            "selection": (
                "every resolved paper leg on the vehicle the production engine "
                "selected, at the instants its market read was BUY"
            ),
            **metrics(arm_a),
        },
        ARM_SIGNAL_PLUS_OVERLAY: {
            "arm": ARM_SIGNAL_PLUS_OVERLAY,
            "selection": (
                "the same legs, keeping only those whose overlay row read "
                f"{SUPPORTED} at the decision instant"
            ),
            **metrics(arm_b),
        },
        "legs_in_a_not_in_b": len(dropped),
        "dropped_by_state": _tally([
            states.get(str(r.get("event_id"))) for r in dropped
        ]),
        "not_independent": SHARED_LEGS,
        "comparable": (
            len(arm_b) >= MIN_RESOLVED_FOR_A_COMPARISON
            and len(arm_a) >= MIN_RESOLVED_FOR_A_COMPARISON
        ),
    }


def _tally(values: list[str | None]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        key = value or "NO_OVERLAY_ROW"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def report(*, limit: int = 100000) -> dict:
    """The whole comparison payload, with its own refusal built in.

    When either arm is under the display floor the arms are still published —
    the sample is the finding at that point — but ``verdict`` says so instead of
    naming a better arm, and no arm is ever named better than the other here at
    any sample size. That is what the outcome table is for, not what this
    report is for.
    """
    legs = _legs(limit)
    states = _overlay_states([str(r["event_id"]) for r in legs])
    measured = arms(legs, states)
    a = measured[ARM_SIGNAL_ONLY]
    b = measured[ARM_SIGNAL_PLUS_OVERLAY]
    verdict = (
        INSUFFICIENT_SAMPLE if not measured["comparable"]
        else "ARMS_REPORTED_NO_CONCLUSION_DRAWN"
    )
    return {
        "phase": VERSION,
        "arms": measured,
        "resolved_legs_available": len(legs),
        "overlay_rows_joined": len(states),
        "verdict": verdict,
        "difference": {
            "expectancy_net_pct": _delta(
                a["expectancy_net_pct"], b["expectancy_net_pct"]),
            "win_rate_pct": _delta(a["win_rate_pct"], b["win_rate_pct"]),
            "max_drawdown_net_pct": _delta(
                a["max_drawdown_net_pct"], b["max_drawdown_net_pct"]),
            "avg_cost_points": _delta(
                a["avg_cost_points"], b["avg_cost_points"]),
            "read_as": (
                "descriptive difference between a set and its own subset; a "
                "positive number here is not an improvement and not a result"
            ),
        },
        "min_resolved_for_a_comparison": MIN_RESOLVED_FOR_A_COMPARISON,
        "status": PAPER_ONLY,
        "not_a_promotion": NOT_A_PROMOTION,
    }


def _delta(a: float | None, b: float | None) -> float | None:
    """B minus A, or None if either side has no measurement."""
    if a is None or b is None:
        return None
    return b - a
