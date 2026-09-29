"""Phase 9 §16-17 — a qualifier run beside the baseline, blocking nothing.

RESEARCH ONLY. This module answers "what would have happened if the trades whose
contract was economically hopeless had been skipped?" — and it is the single most
dangerous block in the phase, because an in-sample filter *always* looks good. Any
filter built on the same trades it is scored on will avoid losers by construction;
that is not evidence, it is arithmetic.

Three defences are built into the code rather than left to the prose:

1. **Chronological holdout.** The sessions are split in time: the qualifier's
   thresholds come from the earlier sessions only, and the headline comparison is
   reported separately on the later ones. Where there are too few sessions to split
   — which is the case on this dataset — the holdout block says so and the in-sample
   block is labelled ``IN_SAMPLE_ONLY``, worth nothing as evidence.
2. **Avoided losers are not a result.** They are reported next to *missed winners*,
   always, and the verdict reads the pair or nothing.
3. **No production path.** The qualifier returns a label per baseline BUY. It does
   not filter the population, does not reach the Signal tab, and the engine does not
   import this module. Every baseline trade stays in the baseline aggregate.

Rejection reasons follow §16 and are assigned in a fixed precedence, so a trade has
exactly one reason and the counts add up.
"""
from __future__ import annotations

from app.research.phase7.gates import MIN_REAL_SESSIONS
from app.research.phase7.policies import median

from . import tradability
from .findings9 import label9, validated

REASONS = ("REJECTED_DATA", "REJECTED_SPREAD", "REJECTED_LIQUIDITY",
           "REJECTED_BAD_STRIKE", "REJECTED_CHASE", "REJECTED_ROOM", "UNKNOWN")
QUALIFIED = "QUALIFIED"

MIN_HOLDOUT_SESSIONS = 5           # phase8.findings.validated's requirement
SEVERE_CHASE = ("SEVERELY_CHASED",)


def qualify_detail(trade: dict, entry_row: dict | None,
                   proposal: dict) -> tuple[str, str]:
    """``(label, trigger)`` for one baseline BUY. Nothing is blocked by either.

    The trigger exists because "REJECTED_DATA" on its own is opaque: a rejection
    because the feed was stale is a different problem from one because the broker's
    greeks were degenerate on that leg, and only the second is fixable at the
    recording end. The report aggregates the triggers so a large rejection bucket
    cannot hide its cause.
    """
    if trade.get("data_flag") in ("NO_DATA", "STALE"):
        return "REJECTED_DATA", f"data_flag={trade.get('data_flag')}"
    if entry_row is None:
        return "UNKNOWN", "no ladder row recorded at signal time"
    cls = tradability.classify(entry_row, proposal)
    if cls == "RED":
        share = entry_row.get("spread_share_of_risk_pct")
        if share is None:
            return "REJECTED_DATA", "no two-sided book on the selected leg"
        if share >= tradability.UNTRADABLE_SPREAD_SHARE_PCT:
            return "REJECTED_SPREAD", "book wider than the intended risk"
        oi, vol = entry_row.get("oi"), entry_row.get("volume")
        if (oi is not None and oi < 1000.0) or (vol is not None and vol < 100.0):
            return "REJECTED_LIQUIDITY", "OI or volume below the diagnostic floor"
        return "REJECTED_SPREAD", "worst proposed tercile on spread share of risk"
    if not entry_row.get("greeks_usable"):
        # The feed's delta cannot be trusted on this leg, so room and moneyness are
        # unavailable rather than favourable.
        return "REJECTED_DATA", "degenerate greeks on the selected leg"
    delta = entry_row.get("delta")
    if delta is not None and abs(delta) < 0.25:
        return "REJECTED_BAD_STRIKE", "delta below the diagnostic floor"
    if trade.get("entry_quality") in SEVERE_CHASE:
        return "REJECTED_CHASE", "phase 7 classified the entry SEVERELY_CHASED"
    room = entry_row.get("room_ratio")
    if room is not None and room > 1.0:
        return "REJECTED_ROOM", "target1 needs more than the ATR offered"
    return QUALIFIED, "no measurable defect at entry time"


def qualify(trade: dict, entry_row: dict | None, proposal: dict) -> str:
    """One label for one baseline BUY. Nothing is blocked by this return value."""
    return qualify_detail(trade, entry_row, proposal)[0]


def _block(rows: list[dict], label_n: int, sessions: int) -> dict:
    if not rows:
        return {"n": 0, "label": label9(0, sessions)}
    rs = [r["realised_r"] for r in rows]
    nets = [r["net_r"] for r in rows if r["net_r"] is not None]
    wins = [r for r in rs if r > 0]
    bad = -sum(r for r in rs if r <= 0)
    net_wins = [r for r in nets if r > 0]
    net_bad = -sum(r for r in nets if r <= 0)
    equity = 0.0
    peak = 0.0
    dd = 0.0
    for r in nets:
        equity += r
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    return {
        "n": len(rows),
        "win_rate_pct": round(100.0 * len(wins) / len(rs), 1),
        "target_before_stop_pct": round(100.0 * sum(
            1 for r in rows if r["target_before_stop_hit"]) / len(rows), 1),
        "gross_expectancy_r": round(sum(rs) / len(rs), 3),
        "net_expectancy_r": round(sum(nets) / len(nets), 3) if nets else None,
        "gross_profit_factor": round(sum(wins) / bad, 3) if bad > 0 else None,
        "net_profit_factor": round(sum(net_wins) / net_bad, 3) if net_bad > 0 else None,
        "total_net_r": round(sum(nets), 3) if nets else None,
        "median_mfe_r": median([r["mfe_r"] for r in rows]),
        "median_mae_r": median([r["mae_r"] for r in rows]),
        "median_mfe_capture_pct": median(
            [r["capture"].get("capture_pct") for r in rows
             if r["capture"].get("capture_pct") is not None]),
        "median_spread_cost_r": median([r["spread_cost_r"] for r in rows
                                        if r["spread_cost_r"] is not None]),
        "sequential_net_r_drawdown": round(dd, 3),
        "drawdown_caveat": "this is the running sum of per-trade net R in timestamp "
                           "order. It is NOT a portfolio drawdown: the replay applies "
                           "no position sizing, no concurrency limit, no cooldown and "
                           "no risk governor",
        "label": label9(label_n, sessions),
    }


def metrics(rows: list[dict], sessions: int) -> dict:
    """The outcome block for an arbitrary set of trades, for later phases.

    Public so Phase 10 scores its own qualifier with *this* metric code rather
    than a second copy of it: two implementations of expectancy is how a
    comparison stops being a comparison.
    """
    return _block(rows, len(rows), sessions)


def compare(rows: list[dict], sessions_ordered: list[str]) -> dict:
    """§17 — baseline against the qualified subset, in-sample and on a holdout."""
    n_sessions = len(sessions_ordered)
    qualified = [r for r in rows if r["phase9_qualification"] == QUALIFIED]
    rejected = [r for r in rows if r["phase9_qualification"] != QUALIFIED]
    counts = {QUALIFIED: len(qualified)}
    for reason in REASONS:
        counts[reason] = sum(1 for r in rows
                             if r["phase9_qualification"] == reason)
    triggers: dict[str, dict[str, int]] = {}
    for r in rows:
        cell = triggers.setdefault(r["phase9_qualification"], {})
        trig = str(r.get("phase9_qualification_trigger", "unrecorded"))
        cell[trig] = cell.get(trig, 0) + 1

    def net(r: dict) -> float | None:
        return r["net_r"]

    missed_winners = [r for r in rejected if (net(r) or 0.0) > 0]
    avoided_losers = [r for r in rejected if (net(r) or 0.0) <= 0]

    # Chronological split: earliest sessions inform, latest sessions test.
    cut = max(1, int(0.7 * n_sessions)) if n_sessions >= 2 else n_sessions
    dev_sessions = set(sessions_ordered[:cut])
    hold_sessions = set(sessions_ordered[cut:])
    dev = [r for r in rows if r["session"] in dev_sessions]
    hold = [r for r in rows if r["session"] in hold_sessions]
    hold_base = _block(hold, len(hold), n_sessions)
    hold_qual = _block([r for r in hold if r["phase9_qualification"] == QUALIFIED],
                       len(hold), n_sessions)
    agrees = bool(
        hold_qual.get("net_expectancy_r") is not None
        and hold_base.get("net_expectancy_r") is not None
        and hold_qual["net_expectancy_r"] > hold_base["net_expectancy_r"])

    base_block = _block(rows, len(rows), n_sessions)
    qual_block = _block(qualified, len(qualified), n_sessions)
    delta = None
    if (qual_block.get("net_expectancy_r") is not None
            and base_block.get("net_expectancy_r") is not None):
        delta = round(qual_block["net_expectancy_r"]
                      - base_block["net_expectancy_r"], 3)

    return {
        "status": ("IN_SAMPLE_ONLY" if len(hold_sessions) < MIN_HOLDOUT_SESSIONS
                   else "HOLDOUT_TESTED"),
        "sessions": n_sessions,
        "holdout_sessions_available": len(hold_sessions),
        "holdout_sessions_required": MIN_HOLDOUT_SESSIONS,
        "qualification_counts": counts,
        "qualification_triggers": {k: dict(sorted(v.items(), key=lambda kv: -kv[1]))
                                   for k, v in sorted(triggers.items())},
        "qualified_pct": round(100.0 * len(qualified) / max(1, len(rows)), 1),
        "baseline": base_block,
        "qualified_only": qual_block,
        "net_expectancy_delta_r": delta,
        "missed_winners": {
            "n": len(missed_winners),
            "total_net_r_given_up": round(sum(net(r) or 0.0 for r in missed_winners), 3),
            "by_reason": {reason: sum(1 for r in missed_winners
                                      if r["phase9_qualification"] == reason)
                          for reason in REASONS},
        },
        "avoided_losers": {
            "n": len(avoided_losers),
            "total_net_r_avoided": round(sum(net(r) or 0.0 for r in avoided_losers), 3),
            "by_reason": {reason: sum(1 for r in avoided_losers
                                      if r["phase9_qualification"] == reason)
                          for reason in REASONS},
            "caveat": "an in-sample filter avoids losers by construction. This count "
                      "is NOT evidence and must be read only beside missed_winners",
        },
        "chronological_holdout": {
            "dev_sessions": sorted(dev_sessions),
            "holdout_sessions": sorted(hold_sessions),
            "dev_trades": len(dev),
            "holdout_trades": len(hold),
            "holdout_baseline": hold_base,
            "holdout_qualified": hold_qual,
            "holdout_agrees_with_dev": agrees,
            "usable": len(hold_sessions) >= MIN_HOLDOUT_SESSIONS,
            "reading": (f"{len(hold_sessions)} holdout session(s) against "
                        f"{MIN_HOLDOUT_SESSIONS} required and {n_sessions} of "
                        f"{MIN_REAL_SESSIONS} total: the split is computed and shown "
                        f"so the machinery is testable, but it cannot validate "
                        f"anything at this sample size"),
        },
        "label": validated(len(qualified), n_sessions,
                           holdout_sessions=len(hold_sessions),
                           holdout_agrees=agrees),
        "guarantee": "the qualifier labels recorded trades and filters nothing. No "
                     "production module imports this file, the Signal tab never sees "
                     "these labels, and the baseline population is unchanged",
    }
