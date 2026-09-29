"""§9/§15/§21/§22 — champion/challenger, the promotion gates, and readiness.

The default answer here is :data:`NO_CANDIDATE`, and with a sample this size it
is the *correct* answer. That is not pessimism built in for its own sake: the
gates are an AND of eleven conditions, and a wide search over hundreds of
candidates produces things that clear one or two of them constantly.

**Every gate must pass.** There is no score, no weighted average and no
"promising enough" branch, because a composite score lets a large positive on a
cheap dimension pay for a failure on an expensive one — it is how a candidate
with a hundred trades in four sessions ends up promoted for having a high
profit factor.

**The champion is not the leader.** A challenger displaces the champion only by
clearing its own full sample *and* beating it by a margin that a short lucky run
cannot supply. Ranking first is not evidence; on any given fortnight something
must rank first, including when nothing has an edge.

**A milestone is not a verdict.** Ten or twenty-five resolved trades is a
progress note. It is reported with its caveat attached in the same row, because
a bare early positive number is the single most misread output this whole
platform produces.

Nothing here authorises anything. The strongest value is
:data:`READY_FOR_CONTROLLED_LIVE_REVIEW`, which means a human should hold a
review — not that anything may be traded with money.
"""
from __future__ import annotations

import datetime as dt
import zoneinfo

from app.research.opportunity import (
    CHALLENGER,
    CHALLENGER_MIN_SESSIONS,
    CHALLENGER_MIN_TRADES,
    CHAMPION,
    FROZEN_AT,
    GATE_NAMES,
    MAX_ONE_TRADE_SHARE,
    NO_CANDIDATE,
    NO_CHAMPION,
    NOT_READY,
    PAPER,
    PAPER_ONLY,
    PROMOTION_COST_STRESS_MULT,
    PROMOTION_MAX_DD_PCT,
    PROMOTION_MIN_NET_PCT,
    PROMOTION_MIN_PF,
    PROMOTION_MIN_SESSIONS,
    PROMOTION_MIN_TRADES,
    READY_FOR_CONTROLLED_LIVE_REVIEW,
    SCHEMA_VERSION,
    SHADOW_VALIDATED,
)
from app.research.opportunity import guard, mechanisms as mech
from app.research.opportunity import paper, registry, screen

_IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# How much better than the champion a challenger must be, in mean net percent
# per trade, before it takes over. A margin rather than a strict inequality
# because two candidates within noise of each other are not ranked by the data,
# and swapping on the sign of noise churns the champion every fortnight.
CHALLENGER_MARGIN_PCT = 0.02


def gates(candidate: dict, metrics: dict, screen_row: dict | None,
          look_ahead: dict) -> dict:
    """Every promotion gate for one candidate, each with its own verdict."""
    trades = int(metrics.get("resolved_trades") or 0)
    sessions = int(metrics.get("sessions") or 0)
    net = metrics.get("net_total_pct")
    pf = metrics.get("profit_factor")
    dd = metrics.get("max_drawdown_pct")
    stressed = metrics.get("cost_stress_net_pct")
    share = metrics.get("one_trade_share")
    holdout = (screen_row or {}).get("holdout") or {}
    holdout_net = holdout.get("net_total_pct")

    rows = {
        "FROZEN_FINGERPRINT": _gate(
            bool(candidate.get("definition_fingerprint"))
            and str(candidate.get("status")) in FROZEN_AT,
            f"status={candidate.get('status')} "
            f"fingerprint={candidate.get('definition_fingerprint')}"),
        "RESOLVED_TRADES": _gate(
            trades >= PROMOTION_MIN_TRADES,
            f"{trades} of {PROMOTION_MIN_TRADES} resolved paper trades"),
        "SESSIONS": _gate(
            sessions >= PROMOTION_MIN_SESSIONS,
            f"{sessions} of {PROMOTION_MIN_SESSIONS} distinct sessions"),
        "NET_AFTER_COSTS": _gate(
            net is not None and float(net) > PROMOTION_MIN_NET_PCT,
            f"net {net} percent after measured spread and modelled charges"),
        "PROFIT_FACTOR": _gate(
            pf is not None and float(pf) >= PROMOTION_MIN_PF,
            f"profit factor {pf} against a floor of {PROMOTION_MIN_PF}"),
        "DRAWDOWN": _gate(
            dd is not None and float(dd) <= PROMOTION_MAX_DD_PCT,
            f"peak drawdown {dd} percent against a ceiling of "
            f"{PROMOTION_MAX_DD_PCT}"),
        "HOLDOUT_POSITIVE": _gate(
            holdout_net is not None and float(holdout_net) > 0
            and int(holdout.get("trades") or 0) > 0,
            _holdout_detail(screen_row, holdout)),
        "COST_STRESS": _gate(
            stressed is not None and float(stressed) > 0,
            f"net {stressed} percent with costs at "
            f"{PROMOTION_COST_STRESS_MULT}x the modelled ones"),
        "NO_SINGLE_OUTLIER": _gate(
            share is None or float(share) <= MAX_ONE_TRADE_SHARE,
            f"largest single trade is {share} of the total net"),
        "NO_LOOK_AHEAD": _gate(
            look_ahead.get("status") == guard.CLEAN,
            str(look_ahead.get("status"))),
        "DATA_COVERAGE": _gate(
            trades > 0 and sessions > 0
            and not metrics.get("midpoint_used_anywhere"),
            "resolved on executable sides with no midpoint anywhere"),
    }
    failed = [name for name in GATE_NAMES if not rows[name]["pass"]]
    return {
        "gates": rows,
        "all_pass": not failed,
        "failed": failed,
        "gate_count": len(GATE_NAMES),
        "rule": "every gate must pass; there is no composite score",
    }


def _holdout_detail(screen_row: dict | None, holdout: dict) -> str:
    if screen_row is None:
        return ("no historical screen row exists for this candidate, so its "
                "chronological holdout is unmeasured — not passed")
    if not holdout.get("trades"):
        return ("the untouched holdout period contains no trade for this "
                "candidate: unmeasured, not passed")
    return (f"holdout net {holdout.get('net_total_pct')} percent over "
            f"{holdout.get('trades')} trades")


def _gate(ok: bool, detail: str) -> dict:
    return {"pass": bool(ok), "detail": detail}


def _screen_rows() -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in screen.results():
        cid = str(row.get("candidate_id"))
        latest[cid] = row       # append-only file, so the last row is current
    return latest


def readiness(candidate: dict, verdict: dict, metrics: dict) -> str:
    """The readiness label. Never an authorisation."""
    if verdict["all_pass"]:
        return READY_FOR_CONTROLLED_LIVE_REVIEW
    if int(metrics.get("resolved_trades") or 0) == 0:
        return NOT_READY
    only_sample = set(verdict["failed"]) <= {"RESOLVED_TRADES", "SESSIONS"}
    if only_sample:
        # Everything measurable passes and the sample is simply not there yet.
        return SHADOW_VALIDATED
    return PAPER_ONLY


def report() -> dict:
    """The promotion-readiness report across every candidate with paper legs."""
    la = guard.audit(mech.ADMISSION_FUNCTIONS)
    screens = _screen_rows()
    cands = {c["candidate_id"]: c for c in registry.candidates()}
    rows: list[dict] = []
    for m in paper.summary()["rows"]:
        cid = str(m["candidate_id"])
        cand = cands.get(cid)
        if cand is None:
            continue
        verdict = gates(cand, m, screens.get(cid), la)
        rows.append({
            "candidate_id": cid,
            "candidate_name": cand.get("candidate_name"),
            "mechanism_family": cand.get("mechanism_family"),
            "instrument_scope": cand.get("instrument_scope"),
            "vehicle_scope": cand.get("vehicle_scope"),
            "status": cand.get("status"),
            "definition_fingerprint": cand.get("definition_fingerprint"),
            "resolved_trades": m["resolved_trades"],
            "sessions": m["sessions"],
            "net_total_pct": m["net_total_pct"],
            "profit_factor": m["profit_factor"],
            "max_drawdown_pct": m["max_drawdown_pct"],
            "milestone": m["milestone"],
            "gates": verdict["gates"],
            "gates_passed": verdict["gate_count"] - len(verdict["failed"]),
            "gates_failed": verdict["failed"],
            "promotable": verdict["all_pass"],
            "readiness": readiness(cand, verdict, m),
        })
    promotable = [r for r in rows if r["promotable"]]
    champ = champion(rows)
    return {
        "evaluated": len(rows),
        "promotable": len(promotable),
        "verdict": (NO_CANDIDATE if not promotable
                    else "CANDIDATES_MEET_EVERY_PRODUCTION_PAPER_GATE"),
        "champion": champ["champion"],
        "challengers": champ["challengers"],
        "rows": sorted(rows, key=lambda r: (-r["gates_passed"],
                                            str(r["candidate_name"]))),
        "gate_definition": {
            "min_resolved_trades": PROMOTION_MIN_TRADES,
            "min_sessions": PROMOTION_MIN_SESSIONS,
            "min_net_pct": PROMOTION_MIN_NET_PCT,
            "min_profit_factor": PROMOTION_MIN_PF,
            "max_drawdown_pct": PROMOTION_MAX_DD_PCT,
            "cost_stress_multiple": PROMOTION_COST_STRESS_MULT,
            "max_one_trade_share": MAX_ONE_TRADE_SHARE,
            "holdout": "chronological, untouched, must be positive",
            "look_ahead": "AST audit of every admission function must be clean",
            "all_of_them": True,
        },
        "live_readiness_definition": {
            "states": [NOT_READY, PAPER_ONLY, SHADOW_VALIDATED,
                       READY_FOR_CONTROLLED_LIVE_REVIEW],
            "meaning_of_the_highest": (
                "a human review may be held. It is not an authorisation to "
                "trade money, and nothing in this package can issue one."
            ),
        },
        "generated_at": dt.datetime.now(_IST).isoformat(),
        "schema_version": SCHEMA_VERSION,
    }


def champion(rows: list[dict]) -> dict:
    """The validated paper champion, if one exists, and its challengers.

    A candidate must be at :data:`PAPER` or beyond and hold a challenger-sized
    sample before it is even comparable. Everything else is a challenger, and
    displacing the champion needs a margin, not a nose.
    """
    comparable = [r for r in rows
                  if int(r["resolved_trades"]) >= CHALLENGER_MIN_TRADES
                  and int(r["sessions"]) >= CHALLENGER_MIN_SESSIONS
                  and str(r["status"]) in FROZEN_AT
                  and str(r["status"]) != "SHADOW"]
    if not comparable:
        return {
            "champion": {
                "state": NO_CHAMPION,
                "why": (f"no candidate holds {CHALLENGER_MIN_TRADES} resolved "
                        f"paper trades over {CHALLENGER_MIN_SESSIONS} sessions "
                        f"at status {PAPER} or beyond"),
            },
            "challengers": [
                {"candidate_id": r["candidate_id"], "role": CHALLENGER,
                 "resolved_trades": r["resolved_trades"],
                 "sessions": r["sessions"],
                 "net_total_pct": r["net_total_pct"]}
                for r in rows
            ],
        }
    ranked = sorted(comparable, key=lambda r: -_mean_net(r))
    top = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    margin_ok = (runner is None
                 or _mean_net(top) - _mean_net(runner) >= CHALLENGER_MARGIN_PCT)
    return {
        "champion": {
            "state": CHAMPION if margin_ok else "TIED_WITHIN_NOISE_NO_CHAMPION",
            "candidate_id": top["candidate_id"],
            "candidate_name": top["candidate_name"],
            "resolved_trades": top["resolved_trades"],
            "sessions": top["sessions"],
            "net_total_pct": top["net_total_pct"],
            "margin_rule": (
                f"a challenger must beat the champion by "
                f"{CHALLENGER_MARGIN_PCT} percent mean net per trade on its "
                f"own full sample; ranking first is not evidence"
            ),
        },
        "challengers": [
            {"candidate_id": r["candidate_id"], "role": CHALLENGER,
             "resolved_trades": r["resolved_trades"],
             "sessions": r["sessions"], "net_total_pct": r["net_total_pct"]}
            for r in ranked[1:]
        ],
    }


def _mean_net(row: dict) -> float:
    trades = int(row.get("resolved_trades") or 0)
    net = row.get("net_total_pct")
    if not trades or net is None:
        return float("-inf")
    return float(net) / trades
