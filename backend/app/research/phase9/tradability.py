"""Phase 9 §9/§12 — is a contract economically tradable at all? RESEARCH ONLY.

The strongest thing in the Phase 8 evidence is not a strategy result, it is an
arithmetic one: on two independent sessions GOLD's quoted book consumed ~169% of the
intended risk unit, SILVER ~19%, NIFTY ~3.6%. When the spread is wider than the stop,
the trade is unwinnable before the market does anything, whatever the signal says.

This module therefore does two things and deliberately not a third:

* it reports the **empirical distribution** of the tradability inputs, per
  instrument, so the thresholds can be argued from deciles rather than from taste;
* it applies *proposed* thresholds derived from those deciles and shows what each
  class would have contained — counts and outcomes.

What it does not do is choose the thresholds (§306) or apply them anywhere near
production. ``PROPOSED_ONLY`` is stamped on the block, and the classifier here is
never imported by the engine.

Inputs are the recorded book only — spread, OI, volume, quote age — so this block is
not contaminated by the missing one-minute bars.
"""
from __future__ import annotations

from collections import defaultdict

from app.research.phase7.policies import median

from .findings9 import label9

CLASSES = ("GREEN", "YELLOW", "RED")

# The one absolute statement in this module, and it is arithmetic rather than
# empirical: when the round-trip book equals or exceeds the intended risk, the stop
# is inside the spread and no signal quality can recover it. Stated as a constant so
# it is visible, and it is a *ceiling* on the proposal, not a tuned parameter.
UNTRADABLE_SPREAD_SHARE_PCT = 100.0


def deciles(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    xs = sorted(values)

    def q(f: float) -> float:
        return round(xs[min(len(xs) - 1, int(f * len(xs)))], 3)

    return {"n": len(xs), "min": round(xs[0], 3), "p10": q(0.1), "p25": q(0.25),
            "p50": q(0.5), "p75": q(0.75), "p90": q(0.9), "max": round(xs[-1], 3)}


def propose(rows: list[dict]) -> dict:
    """Threshold *proposal* from this sample's own distribution.

    The cut points are the terciles of the observed spread-share distribution, so
    they describe the recorded book instead of an opinion about what a good spread
    is. They are reported, not applied to anything that trades.
    """
    shares = [r["spread_share_of_risk_pct"] for r in rows
              if r.get("spread_share_of_risk_pct") is not None]
    ois = [r["oi"] for r in rows if r.get("oi") is not None]
    vols = [r["volume"] for r in rows if r.get("volume") is not None]
    d = deciles(shares)
    if not shares:
        return {"status": "PROPOSED_ONLY", "derivable": False,
                "reason": "no leg in the sample carried a usable two-sided book"}
    xs = sorted(shares)
    green_cut = round(xs[int(0.33 * len(xs))], 2)
    yellow_cut = round(xs[int(0.66 * len(xs))], 2)
    return {
        "status": "PROPOSED_ONLY",
        "derivable": True,
        "method": "terciles of the measured spread-share distribution, with an "
                  "arithmetic ceiling: a book at or above 100% of intended risk is "
                  "RED regardless of where the terciles fall",
        "green_max_spread_share_pct": green_cut,
        "yellow_max_spread_share_pct": min(yellow_cut, UNTRADABLE_SPREAD_SHARE_PCT),
        "red_above_pct": min(yellow_cut, UNTRADABLE_SPREAD_SHARE_PCT),
        "untradable_ceiling_pct": UNTRADABLE_SPREAD_SHARE_PCT,
        "green_min_oi": None if not ois else round(sorted(ois)[int(0.33 * len(ois))], 0),
        "green_min_volume": None if not vols else round(
            sorted(vols)[int(0.33 * len(vols))], 0),
        "spread_share_deciles": d,
        "oi_deciles": deciles(ois),
        "volume_deciles": deciles(vols),
        "warning": "these numbers are descriptions of two sessions. Applying them as "
                   "a production filter would fit the filter to those sessions; they "
                   "exist so a later decision can be argued from a distribution",
    }


def classify(row: dict, proposal: dict) -> str:
    """The proposed class of one leg. Research classification only."""
    share = row.get("spread_share_of_risk_pct")
    if share is None:
        return "RED"
    if share >= UNTRADABLE_SPREAD_SHARE_PCT:
        return "RED"
    if not proposal.get("derivable"):
        return "YELLOW"
    if share <= proposal["green_max_spread_share_pct"]:
        oi = row.get("oi")
        vol = row.get("volume")
        min_oi = proposal.get("green_min_oi")
        min_vol = proposal.get("green_min_volume")
        if (min_oi is not None and (oi is None or oi < min_oi)) or \
           (min_vol is not None and (vol is None or vol < min_vol)):
            return "YELLOW"
        return "GREEN"
    if share <= proposal["yellow_max_spread_share_pct"]:
        return "YELLOW"
    return "RED"


def _outcome_block(rows: list[dict], sessions: int) -> dict:
    if not rows:
        return {"n": 0, "label": label9(0, sessions)}
    rs = [r["realised_r"] for r in rows if r.get("realised_r") is not None]
    nets = [r["net_r"] for r in rows if r.get("net_r") is not None]
    wins = [r for r in rs if r > 0]
    bad = -sum(r for r in rs if r <= 0)
    return {
        "n": len(rows),
        "gross_expectancy_r": round(sum(rs) / len(rs), 3) if rs else None,
        "net_expectancy_r": round(sum(nets) / len(nets), 3) if nets else None,
        "profit_factor": round(sum(wins) / bad, 3) if bad > 0 else None,
        "win_rate_pct": round(100.0 * len(wins) / len(rs), 1) if rs else None,
        "target_before_stop_pct": round(100.0 * sum(
            1 for r in rows if r.get("target_before_stop_hit")) / len(rows), 1),
        "median_spread_share_of_risk_pct": median(
            [r["spread_share_of_risk_pct"] for r in rows
             if r.get("spread_share_of_risk_pct") is not None]),
        "label": label9(len(rows), sessions),
    }


def study(rows: list[dict], sessions: int, contamination: dict) -> dict:
    """§9 + §12 — distributions, proposed classes, and outcome per class."""
    proposal = propose(rows)
    for r in rows:
        r["tradability"] = classify(r, proposal)

    by_class: dict[str, list[dict]] = defaultdict(list)
    by_inst: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_class[r["tradability"]].append(r)
        by_inst[r["instrument"]].append(r)

    inst_block = {}
    for name, sub in sorted(by_inst.items()):
        shares = [r["spread_share_of_risk_pct"] for r in sub
                  if r.get("spread_share_of_risk_pct") is not None]
        counts = {c: sum(1 for r in sub if r["tradability"] == c) for c in CLASSES}
        nets = [r["net_r"] for r in sub if r.get("net_r") is not None]
        inst_block[name] = {
            "n": len(sub),
            "spread_share_of_risk_pct": deciles(shares),
            "proposed_class_counts": counts,
            "dominant_class": max(counts, key=lambda c: counts[c]),
            "net_expectancy_r": round(sum(nets) / len(nets), 3) if nets else None,
            "book_wider_than_stop_pct": round(100.0 * sum(
                1 for s in shares if s >= UNTRADABLE_SPREAD_SHARE_PCT)
                / max(1, len(shares)), 1),
            "label": label9(len(sub), sessions),
        }

    return {
        "status": "PROPOSED_ONLY — no threshold here is applied to production, and "
                  "this classifier is not imported by the engine",
        "proposal": proposal,
        "by_class": {c: _outcome_block(by_class.get(c, []), sessions) for c in CLASSES},
        "by_instrument": inst_block,
        "economically_tradable_after_spread": [
            name for name, b in inst_block.items()
            if b["net_expectancy_r"] is not None and b["net_expectancy_r"] > 0],
        "not_tradable_after_spread": [
            name for name, b in inst_block.items()
            if b["book_wider_than_stop_pct"] >= 50.0],
        "data_contamination": contamination,
        "reading": "an instrument whose book routinely exceeds the intended risk is "
                   "not a signal-quality problem and cannot be fixed by a better "
                   "model; either the risk unit is too small for that contract or "
                   "the contract should not be traded with this stop discipline",
    }
