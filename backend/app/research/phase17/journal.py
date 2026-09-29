"""§33 — one flat journal row per candidate, joined to its legs and paper episode.

The evidence itself lives in three append-only files (observations, resolved legs,
paper episodes) because they are written at different times: a candidate is
recorded at the decision instant, its legs resolve minutes later, and its paper
episode later still. This module is the read-side join — one wide row per
candidate, with the fields the daily review actually asks for, so a session can
be read in a spreadsheet without re-deriving the join by hand.

Two rules the row obeys, both of them consequences of earlier mistakes:

* nothing is recomputed here. Every value is copied from what was recorded at the
  time it was recorded. A journal that recomputes a label after the outcome is
  known cannot be used to ask whether the label predicted the outcome.
* gross, cost and net are always emitted together, and ``net_points`` stays empty
  unless the cost was MEASURED against a real book. A gross-only journal row is
  the specific artefact that made this tool look profitable when it was not.
"""
from __future__ import annotations

import csv
import io

from app.research.phase17 import cepe

SELECTED, OPPOSITE = "SELECTED", "OPPOSITE"

# The row, in column order. Explicit rather than derived from a dict so the
# schema is stable across sessions: a half-collected dataset whose columns moved
# is worse than no dataset at all.
FIELDS: tuple[str, ...] = (
    # identity and when
    "session", "signal_ts", "capture_ts", "observation_id", "global_signal_id",
    "episode_id", "instrument", "family", "weekday", "session_minute",
    "session_period",
    # what the board said
    "candidate_class", "market_signal", "direction", "trade_score", "confidence",
    # market context (UNDERLYING_ONLY — never option P&L)
    "market_basis", "regime", "htf_trend", "htf_alignment", "htf_strength",
    "momentum", "volatility_band", "atr_points", "vwap_side",
    # the vehicle, as quoted at the decision instant
    "selected_vehicle", "symbol", "strike", "expiry", "days_to_expiry",
    "expiry_class", "moneyness", "distance_from_atm", "delta_band",
    "premium", "bid", "ask", "mid", "spread", "spread_pct",
    "oi", "oi_change", "volume", "iv", "delta", "gamma", "theta",
    "underlying_price",
    # the opposite side, from the SAME timestamp
    "opposite_symbol", "opposite_premium", "opposite_bid", "opposite_ask",
    "opposite_spread", "opposite_spread_pct", "opposite_oi", "opposite_iv",
    "opposite_delta",
    # the published plan
    "entry", "entry_low", "entry_high", "stop", "target1", "target2", "target3",
    "risk", "reward_risk", "expected_move_points", "expected_hold_minutes",
    # economics, entry quality, reachability, A+
    "vehicle_class", "cost_points", "cost_rupees", "cost_over_risk",
    "cost_over_expected_move", "room_after_spread", "risk_after_spread",
    "entry_quality", "entry_basis", "t1_score", "t1_rank", "t1_basis",
    "not_a_probability",
    "a_plus_score", "a_plus_label", "a_plus_promotable",
    "a_plus_missing_components",
    # data integrity
    "data_quality", "both_sides", "source", "signal_to_snapshot_ms",
    "feed_age_ms", "tracking",
    # what the leg actually did (selected side)
    "leg_status", "leg_entry_price", "leg_exit_price", "leg_exit_reason",
    "leg_outcome", "leg_gross_points", "leg_cost_points", "leg_net_points",
    "leg_net_r", "leg_cost_status", "leg_mfe", "leg_mae", "leg_mfe_capture_pct",
    "leg_giveback", "leg_minutes_to_t1", "leg_minutes_to_stop",
    "leg_minutes_to_mfe", "leg_hold_minutes", "leg_hold_bucket",
    "leg_reached_half_r_then_reversed", "leg_reached_t1_then_reversed",
    # what the OPPOSITE leg did — §6 needs both to be a comparison
    "opposite_leg_net_points", "opposite_leg_outcome", "opposite_leg_mfe",
    "ce_pe_verdict", "attribution",
    # the paper episode, if one was opened
    "paper_episode_id", "paper_entry_price", "paper_entry_side",
    "paper_exit_price", "paper_exit_side", "paper_exit_reason",
    "paper_gross_points", "paper_cost_points", "paper_net_points",
    "paper_net_r", "paper_cost_status", "paper_hold_minutes",
    # standing disclaimers, on every row rather than in a footnote
    "paper_only", "no_real_order",
)


def _q(obs: dict, side: str) -> dict:
    q = obs.get(side)
    return q if isinstance(q, dict) else {}


def _d(obs: dict, key: str) -> dict:
    v = obs.get(key)
    return v if isinstance(v, dict) else {}


def _legs_by_side(legs: list[dict]) -> dict[str, dict]:
    """The most recent resolved leg per side, keyed SELECTED / OPPOSITE."""
    out: dict[str, dict] = {}
    for leg in legs:
        side = str(leg.get("side") or "")
        prev = out.get(side)
        if prev is None or float(leg.get("exit_ts") or 0.0) >= float(
            prev.get("exit_ts") or 0.0
        ):
            out[side] = leg
    return out


def row(
    obs: dict,
    *,
    legs: list[dict] | None = None,
    paper_row: dict | None = None,
    comparison: dict | None = None,
) -> dict:
    """One journal row. Missing pieces stay empty rather than being invented."""
    sel, opp = _q(obs, "selected"), _q(obs, "opposite")
    plan, econ = _d(obs, "plan"), _d(obs, "economics")
    ent, rch, ap = _d(obs, "entry"), _d(obs, "reach"), _d(obs, "aplus")
    by_side = _legs_by_side(legs or [])
    leg = by_side.get(SELECTED, {})
    oleg = by_side.get(OPPOSITE, {})
    pp = paper_row or {}
    cmp_ = comparison or {}
    return {
        "session": obs.get("session"),
        "signal_ts": obs.get("signal_ts"),
        "capture_ts": obs.get("capture_ts"),
        "observation_id": obs.get("observation_id"),
        "global_signal_id": obs.get("global_signal_id"),
        "episode_id": obs.get("episode_id"),
        "instrument": obs.get("instrument"),
        "family": obs.get("family"),
        "weekday": obs.get("weekday"),
        "session_minute": obs.get("session_minute"),
        "session_period": obs.get("session_period"),
        "candidate_class": obs.get("candidate_class"),
        "market_signal": obs.get("market_signal"),
        "direction": obs.get("direction"),
        "trade_score": obs.get("trade_score"),
        "confidence": obs.get("confidence"),
        "market_basis": obs.get("market_basis"),
        "regime": obs.get("regime"),
        "htf_trend": obs.get("htf_trend"),
        "htf_alignment": obs.get("htf_alignment"),
        "htf_strength": obs.get("htf_strength"),
        "momentum": obs.get("momentum"),
        "volatility_band": obs.get("volatility_band"),
        "atr_points": obs.get("atr_points"),
        "vwap_side": obs.get("vwap_side"),
        "selected_vehicle": obs.get("selected_vehicle"),
        "symbol": sel.get("symbol"),
        "strike": sel.get("strike"),
        "expiry": sel.get("expiry"),
        "days_to_expiry": sel.get("days_to_expiry"),
        "expiry_class": sel.get("expiry_class"),
        "moneyness": sel.get("moneyness"),
        "distance_from_atm": sel.get("distance_from_atm"),
        "delta_band": sel.get("delta_band"),
        "premium": sel.get("premium"),
        "bid": sel.get("bid"),
        "ask": sel.get("ask"),
        "mid": sel.get("mid"),
        "spread": sel.get("spread"),
        "spread_pct": sel.get("spread_pct"),
        "oi": sel.get("oi"),
        "oi_change": sel.get("oi_change"),
        "volume": sel.get("volume"),
        "iv": sel.get("iv"),
        "delta": sel.get("delta"),
        "gamma": sel.get("gamma"),
        "theta": sel.get("theta"),
        "underlying_price": sel.get("underlying_price"),
        "opposite_symbol": opp.get("symbol"),
        "opposite_premium": opp.get("premium"),
        "opposite_bid": opp.get("bid"),
        "opposite_ask": opp.get("ask"),
        "opposite_spread": opp.get("spread"),
        "opposite_spread_pct": opp.get("spread_pct"),
        "opposite_oi": opp.get("oi"),
        "opposite_iv": opp.get("iv"),
        "opposite_delta": opp.get("delta"),
        "entry": plan.get("entry"),
        "entry_low": plan.get("entry_low"),
        "entry_high": plan.get("entry_high"),
        "stop": plan.get("stop"),
        "target1": plan.get("target1"),
        "target2": plan.get("target2"),
        "target3": plan.get("target3"),
        "risk": plan.get("risk"),
        "reward_risk": plan.get("reward_risk"),
        "expected_move_points": plan.get("expected_move_points"),
        "expected_hold_minutes": plan.get("expected_hold_minutes"),
        "vehicle_class": econ.get("vehicle_class"),
        "cost_points": econ.get("cost_points"),
        "cost_rupees": econ.get("cost_rupees"),
        "cost_over_risk": econ.get("cost_over_risk"),
        "cost_over_expected_move": econ.get("cost_over_expected_move"),
        "room_after_spread": econ.get("room_after_spread"),
        "risk_after_spread": econ.get("risk_after_spread"),
        "entry_quality": ent.get("entry_quality"),
        "entry_basis": ent.get("basis"),
        "t1_score": rch.get("t1_score"),
        "t1_rank": rch.get("t1_rank"),
        # Whether T1 was the engine's own target or derived from the expected
        # move. Without it a reader cannot tell a committed target from a
        # modelled one, and the two must never be pooled.
        "t1_basis": rch.get("t1_basis"),
        "not_a_probability": True,
        "a_plus_score": ap.get("a_plus_score"),
        "a_plus_label": ap.get("a_plus_label"),
        "a_plus_promotable": ap.get("promotable"),
        "a_plus_missing_components": ";".join(
            str(c) for c in (ap.get("missing_components") or [])
        ),
        "data_quality": obs.get("data_quality"),
        "both_sides": obs.get("both_sides"),
        "source": sel.get("source"),
        "signal_to_snapshot_ms": sel.get("signal_to_snapshot_ms"),
        "feed_age_ms": sel.get("feed_age_ms"),
        "tracking": obs.get("tracking"),
        "leg_status": leg.get("status"),
        "leg_entry_price": leg.get("entry_price"),
        "leg_exit_price": leg.get("exit_price"),
        "leg_exit_reason": leg.get("exit_reason"),
        "leg_outcome": leg.get("outcome"),
        "leg_gross_points": leg.get("gross_points"),
        "leg_cost_points": leg.get("cost_points"),
        "leg_net_points": leg.get("net_points"),
        "leg_net_r": leg.get("net_r"),
        "leg_cost_status": leg.get("cost_status"),
        "leg_mfe": leg.get("mfe"),
        "leg_mae": leg.get("mae"),
        "leg_mfe_capture_pct": leg.get("mfe_capture_pct"),
        "leg_giveback": leg.get("giveback"),
        "leg_minutes_to_t1": leg.get("minutes_to_t1"),
        "leg_minutes_to_stop": leg.get("minutes_to_stop"),
        "leg_minutes_to_mfe": leg.get("minutes_to_mfe"),
        "leg_hold_minutes": leg.get("hold_minutes"),
        "leg_hold_bucket": leg.get("hold_bucket"),
        "leg_reached_half_r_then_reversed": leg.get("reached_half_r_then_reversed"),
        "leg_reached_t1_then_reversed": leg.get("reached_t1_then_reversed"),
        "opposite_leg_net_points": oleg.get("net_points"),
        "opposite_leg_outcome": oleg.get("outcome"),
        "opposite_leg_mfe": oleg.get("mfe"),
        "ce_pe_verdict": cmp_.get("side_verdict"),
        "attribution": cmp_.get("fault"),
        "paper_episode_id": pp.get("episode_id"),
        "paper_entry_price": pp.get("entry_price"),
        "paper_entry_side": pp.get("entry_side"),
        "paper_exit_price": pp.get("exit_price"),
        "paper_exit_side": pp.get("exit_side"),
        "paper_exit_reason": pp.get("exit_reason"),
        "paper_gross_points": pp.get("gross_points"),
        "paper_cost_points": pp.get("cost_points"),
        "paper_net_points": pp.get("net_points"),
        "paper_net_r": pp.get("net_r"),
        "paper_cost_status": pp.get("cost_status"),
        "paper_hold_minutes": pp.get("hold_minutes"),
        "paper_only": True,
        "no_real_order": True,
    }


def rows(
    observations: list[dict],
    legs: list[dict],
    paper_rows: list[dict],
) -> list[dict]:
    """Join the three evidence files into one row per candidate, oldest first.

    An observation with no resolved leg still produces a row: an unresolved or
    dropped candidate is evidence about coverage, and dropping it would bias the
    journal towards the legs that happened to fit in the tracking budget.
    """
    by_obs_legs: dict[str, list[dict]] = {}
    for leg in legs:
        key = str(leg.get("observation_id") or "")
        by_obs_legs.setdefault(key, []).append(leg)
    by_obs_paper: dict[str, dict] = {}
    for pp in paper_rows:
        key = str(pp.get("observation_id") or "")
        prev = by_obs_paper.get(key)
        if prev is None or float(pp.get("entry_ts") or 0.0) >= float(
            prev.get("entry_ts") or 0.0
        ):
            by_obs_paper[key] = pp

    out: list[dict] = []
    for obs in observations:
        key = str(obs.get("observation_id") or "")
        obs_legs = by_obs_legs.get(key, [])
        by_side = _legs_by_side(obs_legs)
        comparison = None
        sel_leg = by_side.get(SELECTED)
        opp_leg = by_side.get(OPPOSITE)
        if sel_leg is not None and opp_leg is not None:
            comparison = cepe.compare(sel_leg, opp_leg)
        out.append(row(
            obs, legs=obs_legs, paper_row=by_obs_paper.get(key),
            comparison=comparison,
        ))
    out.sort(key=lambda r: float(r.get("signal_ts") or 0.0))
    return out


def to_csv(journal_rows: list[dict]) -> str:
    """The same rows as CSV, header always written even when there are none."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(FIELDS), extrasaction="ignore")
    writer.writeheader()
    for r in journal_rows:
        writer.writerow({k: r.get(k) for k in FIELDS})
    return buf.getvalue()
