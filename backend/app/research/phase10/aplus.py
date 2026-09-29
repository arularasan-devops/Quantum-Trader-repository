"""Phase 10 §8-9 — the A+ shadow qualifier and its validation. RESEARCH ONLY.

Phase 9's qualifier is continued rather than replaced: its components (data
freshness, spread share of risk, liquidity, room, entry quality/chase, option
quality) already produced the only positive net expectancy in that phase, and
re-deriving them here would break the comparison across phases. Phase 10 adds the
two components §8 asks for that Phase 9 did not have:

* **signal score** — as a component among others, never as a probability, and
  only where ``score`` measured it to be informative. On this dataset it is
  measured NOT to be, so the component is present, disabled, and reported as
  disabled. That is the honest wiring: a component whose evidence says it carries
  no information must not silently contribute.
* **instrument tradability** — whether the instrument's book is habitually wider
  than the risk it is traded with. GOLD's median spread cost was 145% of intended
  risk over four sessions; that is a property of the contract, not of a signal.

Outputs are §8's exact labels. There is no BUY output, by design: the qualifier
labels a recorded decision and blocks nothing.

§9's validation runs baseline and A+ over the SAME production signals — never a
re-picked population — and reports both, with the holdout separated. Every guard
from Phase 9 is kept: avoided losers are meaningless without missed winners, and
an in-sample improvement is arithmetic rather than evidence.
"""
from __future__ import annotations

from app.research.phase9 import shadow
from app.research.phase9.findings9 import validated

QUALIFIED = shadow.QUALIFIED
REASONS = (*shadow.REASONS, "REJECTED_SCORE", "REJECTED_INSTRUMENT")

# An instrument whose median spread cost exceeds this share of intended risk is
# not a bad signal, it is a bad contract to express a signal in. PROPOSED_ONLY.
UNTRADABLE_INSTRUMENT_MEDIAN_SPREAD_PCT = 100.0
MIN_TRADES_TO_JUDGE_INSTRUMENT = 20


def instrument_tradability(rows: list[dict]) -> dict:
    """Per-instrument spread cost as a share of intended risk. PROPOSED_ONLY.

    Four sessions cannot condemn an instrument permanently, so this returns a
    *classification with its own sample size attached* and the report says what
    the sample forbids. Nothing is removed from any universe by this function.
    """
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["instrument"], []).append(r)
    out: dict[str, dict] = {}
    for name, sub in sorted(by.items()):
        shares = [r["spread_share_of_risk_pct"] for r in sub
                  if r.get("spread_share_of_risk_pct") is not None]
        med = None
        if shares:
            ordered = sorted(shares)
            med = round(ordered[len(ordered) // 2], 1)
        judgeable = len(sub) >= MIN_TRADES_TO_JUDGE_INSTRUMENT and med is not None
        out[name] = {
            "trades": len(sub),
            "median_spread_share_of_risk_pct": med,
            "judgeable": judgeable,
            "class": (None if not judgeable else
                      "UNTRADABLE_ON_THIS_SAMPLE"
                      if med >= UNTRADABLE_INSTRUMENT_MEDIAN_SPREAD_PCT else "TRADABLE"),
            "threshold_status": "PROPOSED_ONLY",
            "caveat": "four sessions cannot permanently remove an instrument. This "
                      "is a monitoring class, not a decision",
        }
    return out


def qualify_detail(trade: dict, entry_row: dict | None, proposal: dict,
                   *, instrument_classes: dict | None = None,
                   score_component_enabled: bool = False,
                   min_signal_score: float | None = None) -> tuple[str, str]:
    """``(label, trigger)`` for one production decision. Blocks nothing.

    Phase 9's precedence is preserved so the counts stay comparable, and the two
    new components are appended rather than inserted: a trade Phase 9 rejected for
    a stale feed must keep reading as a data rejection, not become an instrument
    rejection because the ordering moved.
    """
    label, trigger = shadow.qualify_detail(trade, entry_row, proposal)
    if label != QUALIFIED:
        return label, trigger

    cls = (instrument_classes or {}).get(trade.get("instrument"), {})
    if cls.get("class") == "UNTRADABLE_ON_THIS_SAMPLE":
        return "REJECTED_INSTRUMENT", (
            f"instrument median spread cost "
            f"{cls.get('median_spread_share_of_risk_pct')}% of intended risk")

    if score_component_enabled and min_signal_score is not None:
        score = trade.get("confidence")
        if score is not None and float(score) < float(min_signal_score):
            return "REJECTED_SCORE", f"signal score {float(score):.0f} below " \
                                     f"{float(min_signal_score):.0f}"
    return QUALIFIED, trigger


def compare(rows: list[dict], sessions_ordered: list[str], *,
            score_is_predictive: bool) -> dict:
    """§9 — baseline against A+, on the same signals, holdout separated."""
    n_sessions = len(sessions_ordered)
    qualified = [r for r in rows if r["phase10_qualification"] == QUALIFIED]
    rejected = [r for r in rows if r["phase10_qualification"] != QUALIFIED]

    counts = {QUALIFIED: len(qualified)}
    for reason in REASONS:
        counts[reason] = sum(1 for r in rows
                             if r["phase10_qualification"] == reason)
    triggers: dict[str, dict[str, int]] = {}
    for r in rows:
        cell = triggers.setdefault(r["phase10_qualification"], {})
        t = str(r.get("phase10_qualification_trigger", "unrecorded"))
        cell[t] = cell.get(t, 0) + 1

    missed_winners = [r for r in rejected if (r.get("net_r") or 0.0) > 0]
    avoided_losers = [r for r in rejected if (r.get("net_r") or 0.0) <= 0]

    cut = max(1, int(0.7 * n_sessions)) if n_sessions >= 2 else n_sessions
    dev_sessions = sessions_ordered[:cut]
    hold_sessions = sessions_ordered[cut:]
    hold = [r for r in rows if r["session"] in set(hold_sessions)]
    dev = [r for r in rows if r["session"] in set(dev_sessions)]
    hold_base = shadow.metrics(hold, n_sessions)
    hold_aplus = shadow.metrics(
        [r for r in hold if r["phase10_qualification"] == QUALIFIED], n_sessions)
    dev_base = shadow.metrics(dev, n_sessions)
    dev_aplus = shadow.metrics(
        [r for r in dev if r["phase10_qualification"] == QUALIFIED], n_sessions)

    def better(a: dict, b: dict) -> bool:
        return bool(a.get("net_expectancy_r") is not None
                    and b.get("net_expectancy_r") is not None
                    and a["net_expectancy_r"] > b["net_expectancy_r"])

    agrees = better(hold_aplus, hold_base) and better(dev_aplus, dev_base)
    base_block = shadow.metrics(rows, n_sessions)
    aplus_block = shadow.metrics(qualified, n_sessions)
    delta = None
    if (aplus_block.get("net_expectancy_r") is not None
            and base_block.get("net_expectancy_r") is not None):
        delta = round(aplus_block["net_expectancy_r"]
                      - base_block["net_expectancy_r"], 3)

    holdout_usable = len(hold_sessions) >= shadow.MIN_HOLDOUT_SESSIONS
    return {
        "status": "HOLDOUT_TESTED" if holdout_usable else "IN_SAMPLE_ONLY",
        "sessions": n_sessions,
        "population": "the same production signals in both arms — the baseline "
                      "aggregate is never filtered, and A+ is a label on top of it",
        "components": {
            "data_freshness": "ENABLED (Phase 9)",
            "spread_share_of_risk": "ENABLED (Phase 9)",
            "liquidity_oi_volume": "ENABLED (Phase 9)",
            "room_to_target": "ENABLED (Phase 9, DATA_CONTAMINATED input)",
            "entry_quality_chase": "ENABLED (Phase 9)",
            "option_quality_delta_greeks": "ENABLED (Phase 9)",
            "instrument_tradability": "ENABLED (Phase 10)",
            "signal_score": ("ENABLED (Phase 10)" if score_is_predictive else
                             "PRESENT BUT DISABLED — the score was measured on this "
                             "sample to carry no usable information about the "
                             "outcome, so letting it filter would be filtering on "
                             "noise"),
        },
        "qualification_counts": counts,
        "qualification_triggers": {k: dict(sorted(v.items(), key=lambda kv: -kv[1]))
                                   for k, v in sorted(triggers.items())},
        "qualified_pct": round(100.0 * len(qualified) / max(1, len(rows)), 1),
        "baseline": base_block,
        "a_plus_only": aplus_block,
        "net_expectancy_delta_r": delta,
        "missed_winners": {
            "n": len(missed_winners),
            "total_net_r_given_up": round(
                sum(r.get("net_r") or 0.0 for r in missed_winners), 3),
            "by_reason": {reason: sum(1 for r in missed_winners
                                      if r["phase10_qualification"] == reason)
                          for reason in REASONS},
        },
        "avoided_losers": {
            "n": len(avoided_losers),
            "total_net_r_avoided": round(
                sum(r.get("net_r") or 0.0 for r in avoided_losers), 3),
            "by_reason": {reason: sum(1 for r in avoided_losers
                                      if r["phase10_qualification"] == reason)
                          for reason in REASONS},
            "caveat": "an in-sample filter avoids losers by construction. Read only "
                      "beside missed_winners",
        },
        "chronological_holdout": {
            "dev_sessions": dev_sessions,
            "holdout_sessions": hold_sessions,
            "dev_trades": len(dev),
            "holdout_trades": len(hold),
            "dev_baseline": dev_base,
            "dev_a_plus": dev_aplus,
            "holdout_baseline": hold_base,
            "holdout_a_plus": hold_aplus,
            "holdout_agrees_with_dev": agrees,
            "usable": holdout_usable,
            "required_sessions": shadow.MIN_HOLDOUT_SESSIONS,
            "reading": (
                f"{len(hold_sessions)} holdout session(s) against "
                f"{shadow.MIN_HOLDOUT_SESSIONS} required: the split is computed so "
                f"the machinery is testable, and it cannot validate anything at this "
                f"sample size"),
        },
        "label": validated(len(qualified), n_sessions,
                           holdout_sessions=len(hold_sessions),
                           holdout_agrees=agrees),
        "answer_to_does_a_plus_beat_baseline": (
            "measured better than baseline on unseen sessions" if
            (holdout_usable and better(hold_aplus, hold_base)) else
            "UNPROVEN — the holdout is too small to answer this. The in-sample "
            "improvement is what a filter does by construction and is not evidence"),
        "guarantee": "no BUY is emitted, no trade is filtered, the baseline "
                     "population is unchanged, no threshold is applied to "
                     "production, and the Signal tab never sees these labels",
        "proposed_thresholds_status": "PROPOSED_ONLY",
    }
