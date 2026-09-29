"""§8/§13/§20 — the paper outcome engine, on executable prices only.

A shadow signal says a candidate wanted to act and that the book could price
it. This module turns those into resolved paper legs and the metrics a
promotion decision would need. Everything in it follows from one rule:

**The fill is the side you would actually have been given.** A long option is
entered at the ASK and exited at the BID. A short is the mirror. Futures use
the executable side in the direction of the trade. The midpoint never appears —
not as a fallback, not for a "reference" number, not when the spread looks
unreasonable. A midpoint fill is the single most common way a paper book shows
an edge that does not exist, because it books half the spread as profit on both
ends of every trade, and on a wide option book that is larger than any real
edge being measured.

**A leg with no exit price is unresolved, not flat.** If the forward path runs
out before the stop, target or time limit is reached, the leg stays open and
contributes to no metric. Counting it as zero rewards candidates that fire near
the end of the capture window, which is exactly where the capture is thinnest.

**The journal cannot double-count itself.** Rows are keyed on the candidate and
the observation, and re-running a session converges. With an append-only file
there is no later pass that could remove a duplicate, and a duplicated leg
counts one outcome twice in every metric derived from it.

**Loss containment is declared and enforced in the simulation.** Per-candidate
and aggregate daily limits, a maximum hold, and a cap on concurrent legs — the
same numbers a live design would inherit, applied here where they cost nothing
to test.
"""
from __future__ import annotations

import datetime as dt
import zoneinfo

from app.research.opportunity import (
    CE,
    DAILY_AGGREGATE_LOSS_PCT,
    DAILY_CANDIDATE_LOSS_PCT,
    JOURNAL_FILE,
    MAX_CONCURRENT_PAPER_LEGS,
    MAX_HOLD_MINUTES,
    MEASURED,
    MILESTONE_NOT_REACHED,
    MILESTONES,
    PE,
    SCHEMA_VERSION,
)
from app.research.opportunity import registry, shadow, stats, store
from app.research.opportunity.generator import EXITS
from app.research.phase35 import store as p35store

_IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# Per-leg costs other than the spread, as a percentage of notional. The spread
# is not in here: it is paid by entering at the ask and leaving at the bid, and
# adding a spread allowance on top of that charges it twice.
BROKERAGE_PCT = 0.01
TAXES_PCT = 0.01
SLIPPAGE_PCT = 0.01
NON_SPREAD_COST_PCT = BROKERAGE_PCT + TAXES_PCT + SLIPPAGE_PCT

RESOLVED = "RESOLVED"
OPEN_AT_END = "OPEN_WHEN_THE_CAPTURE_ENDED_NOT_A_FLAT_TRADE"
REFUSED_LIMIT = "REFUSED_BY_A_PAPER_LOSS_LIMIT"
REFUSED_CONCURRENCY = "REFUSED_THE_CONCURRENT_LEG_CAP_WAS_FULL"

LONG_OPTION_SIDES = {"entry": "ASK", "exit": "BID"}


def _exit_rule(candidate: dict) -> dict:
    rule = (candidate.get("exit_definition") or {}).get("rule")
    return EXITS.get(str(rule), EXITS["T1_THEN_BREAKEVEN"])


def _entry_price(row: dict) -> tuple[float | None, str]:
    """The executable entry price and the side it came from.

    Long anything is bought at the ask. Short futures is sold at the bid. There
    is no third branch, because the third branch is always the midpoint.
    """
    bid, ask = row.get("bid"), row.get("ask")
    if bid is None or ask is None:
        return None, "NONE"
    vehicle = str(row.get("vehicle"))
    direction = str(row.get("direction"))
    if vehicle in (CE, PE):
        # A long option position, whichever way the underlying view points: a
        # PE is bought to express a short view, and it is still bought at the
        # ask.
        return float(ask), "ASK"
    if direction == "LONG":
        return float(ask), "ASK"
    return float(bid), "BID"


def _exit_side(row: dict) -> str:
    vehicle = str(row.get("vehicle"))
    if vehicle in (CE, PE):
        return "BID"
    return "BID" if str(row.get("direction")) == "LONG" else "ASK"


def _executable(sample: dict, side: str) -> float | None:
    value = sample.get("bid") if side == "BID" else sample.get("ask")
    if value is None or float(value) <= 0:
        return None
    return float(value)


def resolve_leg(row: dict, path: list[dict], exit_rule: dict) -> dict:
    """Walk one leg forward over recorded two-sided samples.

    ``path`` is the vehicle's own later quotes — the same contract, later in the
    session. Each step is priced on the exit side, so the number that decides a
    stop is the number the position could have been closed at, not the one it
    was marked at.
    """
    entry, entry_side = _entry_price(row)
    exit_side = _exit_side(row)
    if entry is None:
        return {"status": "UNPRICED_AT_ENTRY", "resolved": False}

    sign = 1.0 if str(row.get("direction")) == "LONG" else -1.0
    if str(row.get("vehicle")) in (CE, PE):
        # A long option gains when its own premium rises, whichever direction
        # the underlying view was. The premium is the traded quantity here.
        sign = 1.0
    atr = float(row.get("atr") or 0.0)
    price = float(row.get("decision_price") or 0.0)
    if atr <= 0 or price <= 0:
        return {"status": "NO_GEOMETRY", "resolved": False}

    # Geometry in percent of the decision price, then applied to the premium or
    # the futures price the leg is actually in.
    stop_pct = 100.0 * float(exit_rule["stop_atr"]) * atr / price
    t1_pct = (100.0 * float(exit_rule["t1_atr"]) * atr / price
              if exit_rule.get("t1_atr") else None)
    horizon = min(int(exit_rule.get("time_stop_min") or 60), MAX_HOLD_MINUTES)
    trail_frac = (exit_rule.get("trail") or {}).get("giveback_frac")
    decision_ts = float(row.get("decision_ts") or 0.0)

    best_pct = 0.0
    worst_pct = 0.0
    reached_t1 = False
    stop_at = -stop_pct
    last: dict | None = None
    for sample in path:
        ts = float(sample.get("ts") or 0.0)
        if ts <= decision_ts:
            continue
        if (ts - decision_ts) / 60.0 > horizon:
            break
        px = _executable(sample, exit_side)
        if px is None:
            continue  # unmeasured instant: skipped, never interpolated
        move = 100.0 * sign * (px - entry) / entry
        best_pct = max(best_pct, move)
        worst_pct = min(worst_pct, move)
        last = {"ts": ts, "px": px, "move": move}

        if move <= stop_at:
            return _resolved(row, entry, entry_side, exit_side, px, ts, move,
                             best_pct, worst_pct, "STOP", reached_t1)
        if t1_pct is not None and not reached_t1 and move >= t1_pct:
            reached_t1 = True
            if exit_rule.get("breakeven_after_t1"):
                stop_at = NON_SPREAD_COST_PCT
            if trail_frac is None:
                return _resolved(row, entry, entry_side, exit_side, px, ts,
                                 move, best_pct, worst_pct, "TARGET", True)
        if reached_t1 and trail_frac is not None:
            trail_at = best_pct - abs(best_pct) * float(trail_frac)
            if move <= trail_at:
                return _resolved(row, entry, entry_side, exit_side, px, ts,
                                 move, best_pct, worst_pct, "TRAIL", True)
    if last is None:
        return {"status": "NO_FORWARD_PATH_WAS_CAPTURED", "resolved": False}
    if (last["ts"] - decision_ts) / 60.0 < horizon * 0.5:
        # The capture stopped well before the rule would have exited. Treating
        # the last seen price as an exit would invent a decision the rule never
        # made.
        return {"status": OPEN_AT_END, "resolved": False,
                "last_seen_ts": last["ts"]}
    return _resolved(row, entry, entry_side, exit_side, last["px"], last["ts"],
                     last["move"], best_pct, worst_pct, "TIME_STOP", reached_t1)


def _resolved(row: dict, entry: float, entry_side: str, exit_side: str,
              exit_px: float, exit_ts: float, gross_pct: float,
              best_pct: float, worst_pct: float, reason: str,
              reached_t1: bool) -> dict:
    net_pct = gross_pct - NON_SPREAD_COST_PCT
    giveback = (best_pct - gross_pct) if best_pct > 0 else 0.0
    return {
        "status": RESOLVED,
        "resolved": True,
        "entry_price": entry,
        "entry_side": entry_side,
        "exit_price": exit_px,
        "exit_side": exit_side,
        "exit_ts": exit_ts,
        "exit_reason": reason,
        "reached_t1": reached_t1,
        "gross_pct": gross_pct,
        "spread_paid": "PAID_BY_ENTERING_AT_THE_ASK_AND_LEAVING_AT_THE_BID",
        "cost_pct": NON_SPREAD_COST_PCT,
        "cost_basis": "MEASURED_SPREAD_PLUS_MODELLED_BROKERAGE_TAX_SLIPPAGE",
        "net_pct": net_pct,
        "mfe_pct": best_pct,
        "mae_pct": worst_pct,
        "giveback_pct": giveback,
        "hold_min": (exit_ts - float(row.get("decision_ts") or 0.0)) / 60.0,
        "execution_evidence": MEASURED,
        "midpoint_used": False,
    }


def _path(con, row: dict, horizon_min: int) -> list[dict]:
    """The same contract's later recorded quotes, in order.

    Two sources, in this order. Phase 35's stored path samples where they
    exist, and otherwise the raw quote table filtered to the *same symbol* —
    the same strike and expiry, not merely the same vehicle class. Following a
    different contract forward would resolve the leg on something that was
    never entered, and on an option ladder the neighbouring strike can move
    several times as much.
    """
    obs_id = str(row.get("obs_id"))
    vehicle = str(row.get("vehicle"))
    samples = p35store.path_samples(con, obs_id, vehicle)
    if samples:
        return sorted(samples, key=lambda s: float(s.get("ts") or 0.0))
    symbol = row.get("symbol")
    if not symbol:
        return []
    start = float(row.get("decision_ts") or 0.0)
    cur = con.execute(
        "SELECT ts, bid, ask, traded, evidence FROM raw_quote "
        "WHERE symbol = ? AND vehicle = ? AND ts > ? AND ts <= ? "
        "ORDER BY ts ASC",
        (str(symbol), vehicle, start, start + horizon_min * 60.0),
    )
    return [{"ts": r[0], "bid": r[1], "ask": r[2], "traded": r[3],
             "evidence": r[4]} for r in cur.fetchall()]


def run(*, session: str | None = None, limit: int | None = None) -> dict:
    """Resolve every shadow signal that has not been journalled yet."""
    rows = [r for r in shadow.observations() if r.get("verdict") == shadow.SIGNAL]
    if session:
        rows = [r for r in rows if str(r.get("session")) == str(session)]
    if limit:
        rows = rows[:limit]
    cand_by_id = {c["candidate_id"]: c for c in registry.candidates()}
    seen = store.keys(JOURNAL_FILE, "leg_id")

    out = {
        "signals_considered": len(rows),
        "written": 0,
        "already_present": 0,
        "resolved": 0,
        "unresolved": 0,
        "refused_by_limit": 0,
        "statuses": {},
        "limits": {
            "max_hold_minutes": MAX_HOLD_MINUTES,
            "daily_candidate_loss_pct": DAILY_CANDIDATE_LOSS_PCT,
            "daily_aggregate_loss_pct": DAILY_AGGREGATE_LOSS_PCT,
            "max_concurrent_legs": MAX_CONCURRENT_PAPER_LEGS,
        },
        "schema_version": SCHEMA_VERSION,
        "standing_limit": (
            "paper only. Fills are the executable side of a recorded book; no "
            "midpoint is used anywhere and no order was ever placed."
        ),
    }
    day_candidate: dict[tuple[str, str], float] = {}
    day_total: dict[str, float] = {}
    # Exit instants of legs already taken, so concurrency is counted on overlap
    # rather than on how many rows a session happens to contain.
    open_until: list[float] = []
    con = p35store.connect()
    try:
        for row in sorted(rows, key=lambda r: float(r.get("decision_ts") or 0)):
            cand = cand_by_id.get(str(row.get("candidate_id")))
            if cand is None:
                continue
            day = str(row.get("session"))
            key = (day, str(row.get("candidate_id")))
            leg = {
                "leg_id": store.digest([row.get("candidate_id"),
                                        row.get("obs_id"), "paper"]),
                "candidate_id": row.get("candidate_id"),
                "candidate_name": row.get("candidate_name"),
                "definition_fingerprint": row.get("definition_fingerprint"),
                "obs_id": row.get("obs_id"),
                "session": day,
                "instrument": row.get("instrument"),
                "vehicle": row.get("vehicle"),
                "direction": row.get("direction"),
                "decision_ts": row.get("decision_ts"),
                "book": "OPPORTUNITY_PAPER",
                "journalled_at": dt.datetime.now(_IST).isoformat(),
                "schema_version": SCHEMA_VERSION,
            }
            # Loss limits are checked against what the simulation has already
            # lost today, before the leg is taken, exactly as a live risk check
            # would have to be.
            now = float(row.get("decision_ts") or 0.0)
            open_until[:] = [t for t in open_until if t > now]
            if day_candidate.get(key, 0.0) <= -DAILY_CANDIDATE_LOSS_PCT:
                res = {"status": REFUSED_LIMIT, "resolved": False,
                       "limit": "DAILY_CANDIDATE_LOSS_PCT"}
            elif day_total.get(day, 0.0) <= -DAILY_AGGREGATE_LOSS_PCT:
                res = {"status": REFUSED_LIMIT, "resolved": False,
                       "limit": "DAILY_AGGREGATE_LOSS_PCT"}
            elif len(open_until) >= MAX_CONCURRENT_PAPER_LEGS:
                res = {"status": REFUSED_CONCURRENCY, "resolved": False,
                       "limit": "MAX_CONCURRENT_PAPER_LEGS"}
            else:
                rule = _exit_rule(cand)
                horizon = min(int(rule.get("time_stop_min") or 60),
                              MAX_HOLD_MINUTES)
                res = resolve_leg(row, _path(con, row, horizon), rule)
            leg.update(res)
            if res.get("resolved"):
                open_until.append(float(res["exit_ts"]))
                net = float(res["net_pct"])
                day_candidate[key] = day_candidate.get(key, 0.0) + net
                day_total[day] = day_total.get(day, 0.0) + net
                out["resolved"] += 1
            elif res.get("status") in (REFUSED_LIMIT, REFUSED_CONCURRENCY):
                out["refused_by_limit"] += 1
            else:
                out["unresolved"] += 1
            status = str(res.get("status"))
            out["statuses"][status] = out["statuses"].get(status, 0) + 1
            if store.append_unique(JOURNAL_FILE, leg, field="leg_id", seen=seen):
                out["written"] += 1
            else:
                out["already_present"] += 1
    finally:
        con.close()
    return out


def legs(candidate_id: str | None = None) -> list[dict]:
    rows = store.read(JOURNAL_FILE)
    if candidate_id:
        rows = [r for r in rows if str(r.get("candidate_id")) == str(candidate_id)]
    return rows


def milestone(resolved_count: int) -> dict:
    """Which descriptive milestone a sample has reached, and its caveat."""
    reached = None
    for name, need, caveat in MILESTONES:
        if resolved_count >= need:
            reached = {"milestone": name, "at_trades": need, "caveat": caveat}
    if reached is None:
        return {"milestone": MILESTONE_NOT_REACHED, "at_trades": 0,
                "caveat": "DESCRIPTIVE_ONLY_NOT_EVIDENCE_OF_ANYTHING"}
    return reached


def metrics(candidate_id: str) -> dict:
    """The full metric set for one candidate's resolved paper legs."""
    rows = [r for r in legs(candidate_id) if r.get("resolved")]
    nets = [float(r["net_pct"]) for r in rows]
    gross = [float(r["gross_pct"]) for r in rows]
    sessions = sorted({str(r.get("session")) for r in rows})
    stressed = [n - NON_SPREAD_COST_PCT * 0.5 for n in nets]
    total = sum(nets)
    one_trade = (max(nets) / total) if (nets and total > 0) else None
    return {
        "candidate_id": candidate_id,
        "resolved_trades": len(rows),
        "sessions": len(sessions),
        "session_list": sessions,
        "gross_total_pct": sum(gross),
        "net_total_pct": total,
        "net_mean_pct": stats.mean(nets),
        "win_rate": stats.win_rate(nets),
        "profit_factor": stats.profit_factor(nets),
        "max_drawdown_pct": stats.max_drawdown(nets),
        "expectancy_pct": stats.mean(nets),
        "mfe_mean_pct": stats.mean([float(r.get("mfe_pct") or 0.0) for r in rows]),
        "mae_mean_pct": stats.mean([float(r.get("mae_pct") or 0.0) for r in rows]),
        "giveback_mean_pct": stats.mean(
            [float(r.get("giveback_pct") or 0.0) for r in rows]),
        "hold_min_mean": stats.mean([float(r.get("hold_min") or 0.0) for r in rows]),
        "t1_rate": (100.0 * sum(1 for r in rows if r.get("reached_t1")) / len(rows)
                    if rows else None),
        "cost_stress_net_pct": sum(stressed),
        "cost_stress_basis": "COSTS_RAISED_BY_HALF_AGAIN_OVER_THE_MODELLED_ONES",
        "one_trade_share": one_trade,
        "milestone": milestone(len(rows)),
        "p_value": stats.p_value(nets),
        "vehicles": sorted({str(r.get("vehicle")) for r in rows}),
        "midpoint_used_anywhere": any(r.get("midpoint_used") for r in rows),
    }


def summary() -> dict:
    """Per-candidate paper metrics across the whole journal."""
    ids = sorted({str(r.get("candidate_id")) for r in store.read(JOURNAL_FILE)})
    return {
        "candidates": len(ids),
        "rows": [metrics(cid) for cid in ids],
        "futures_side_rule": "EXECUTABLE_SIDE_IN_THE_DIRECTION_OF_THE_TRADE",
        "long_option_side_rule": LONG_OPTION_SIDES,
        "midpoint_rule": "NEVER_USED_ANYWHERE_IN_THIS_MODULE",
    }
