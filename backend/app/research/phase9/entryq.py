"""Phase 9 §6-7 — does entry quality decide the outcome? RESEARCH ONLY.

Phase 7 already measures the chase: signal premium, the best entry available in the
next five bars, the fraction of the post-signal move already consumed, and the
IDEAL…SEVERELY_CHASED class whose cut points come from the measured distribution
rather than from taste. None of that is recomputed here — ``ev.chase`` is read as-is.

What Phase 9 adds is the comparison the spec asks for and Phase 8 only made per exit
policy: the outcome of each entry class *net of the recorded book*, side by side with
where the entry sat relative to VWAP, ATR and the available room. The question is
narrow and worth answering precisely: **is a chased entry a worse trade, or merely a
worse price on the same trade?** The two have different consequences — the first says
wait, the second says size differently — and only the net column can tell them apart,
because a chased entry pays the same spread as a patient one.

The production chase guard is not touched. These classes are labels on recorded
history; they gate nothing.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from app.research.phase7.paths import CHASE_CLASSES
from app.research.phase7.policies import median

from .findings9 import label9, note9

CLASSES = (*CHASE_CLASSES, "UNKNOWN")


def row_of(trade: dict, ev, entry_row: dict | None) -> dict:
    """The §6 field list for one resolved trade, assembled from existing measures."""
    chase = ev.chase or {}
    dist_vwap = None
    if ev.vwap:
        raw = (ev.spot - ev.vwap) / ev.vwap if ev.side == "CE" else (ev.vwap - ev.spot) / ev.vwap
        dist_vwap = round(100.0 * raw, 3)
    dist_atr = None
    if ev.atr and ev.vwap:
        dist_atr = round(abs(ev.spot - ev.vwap) / ev.atr, 3)
    return {
        "instrument": trade["instrument"],
        "symbol": trade["symbol"],
        "side": trade["side"],
        "ts_ist": trade["ts_ist"],
        "session": trade["session"],
        "expiry_class": trade["expiry_class"],
        "entry_quality": chase.get("class") or "UNKNOWN",
        "signal_premium": chase.get("signal_premium"),
        "best_entry_next_bars": chase.get("best_entry_next_bars"),
        "actual_entry": trade["entry"],
        "improvement_available_pct": chase.get("improvement_available_pct"),
        "consumed_frac": chase.get("consumed_frac"),
        "premium_expansion_before_entry_pct": (
            None if not entry_row else entry_row.get("past_expansion_pct")),
        "distance_from_vwap_pct": dist_vwap,
        "distance_from_vwap_in_atr": dist_atr,
        "room_ratio": None if not entry_row else entry_row.get("room_ratio"),
        "regime": trade["regime"],
        "realised_r": trade["realised_r"],
        "net_r": trade["net_r"],
        "spread_cost_r": trade["spread_cost_r"],
        "mfe_r": trade["mfe_r"],
        "mae_r": trade["mae_r"],
        "mfe_capture_pct": trade["capture"].get("capture_pct"),
        "target_before_stop_hit": trade["target_before_stop_hit"],
        "held_min": trade["held_min"],
    }


def _block(rows: list[dict], sessions: int) -> dict:
    if not rows:
        return {"n": 0, "label": label9(0, sessions)}
    rs = [r["realised_r"] for r in rows]
    nets = [r["net_r"] for r in rows if r["net_r"] is not None]
    wins = [r for r in rs if r > 0]
    bad = -sum(r for r in rs if r <= 0)
    net_wins = [r for r in nets if r > 0]
    net_bad = -sum(r for r in nets if r <= 0)
    return {
        "n": len(rows),
        "win_rate_pct": round(100.0 * len(wins) / len(rs), 1),
        "target_before_stop_pct": round(100.0 * sum(
            1 for r in rows if r["target_before_stop_hit"]) / len(rows), 1),
        "gross_expectancy_r": round(sum(rs) / len(rs), 3),
        "net_expectancy_r": round(sum(nets) / len(nets), 3) if nets else None,
        "gross_profit_factor": round(sum(wins) / bad, 3) if bad > 0 else None,
        "net_profit_factor": round(sum(net_wins) / net_bad, 3) if net_bad > 0 else None,
        "median_spread_cost_r": median([r["spread_cost_r"] for r in rows
                                       if r["spread_cost_r"] is not None]),
        "median_mfe_r": median([r["mfe_r"] for r in rows]),
        "median_mae_r": median([r["mae_r"] for r in rows]),
        "median_mfe_capture_pct": median([r["mfe_capture_pct"] for r in rows
                                          if r["mfe_capture_pct"] is not None]),
        "median_improvement_available_pct": median(
            [r["improvement_available_pct"] for r in rows
             if r["improvement_available_pct"] is not None]),
        "median_premium_expansion_before_entry_pct": median(
            [r["premium_expansion_before_entry_pct"] for r in rows
             if r["premium_expansion_before_entry_pct"] is not None]),
        "median_distance_from_vwap_in_atr": median(
            [r["distance_from_vwap_in_atr"] for r in rows
             if r["distance_from_vwap_in_atr"] is not None]),
        "median_room_ratio": median([r["room_ratio"] for r in rows
                                     if r["room_ratio"] is not None]),
        "label": label9(len(rows), sessions),
        "note": note9(len(rows), sessions),
    }


def study(rows: list[dict], sessions: int, contamination: dict) -> dict:
    """§7 — entry class against outcome, plus the two-way splits worth seeing."""
    by_class: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_class[r["entry_quality"]].append(r)

    ordered = [c for c in CLASSES if by_class.get(c)]
    blocks = {c: _block(by_class[c], sessions) for c in ordered}

    # Monotonicity here answers the narrow question: does a worse entry class do
    # worse? Reported on NET expectancy, because gross flatters a bad price less
    # than the book does.
    # UNKNOWN is excluded: it is not a worse entry, it is an unmeasurable one, and
    # leaving it at the end of an ordering test would let missing data masquerade as
    # the worst entry class.
    seq = [(c, blocks[c]["net_expectancy_r"]) for c in ordered
           if c in CHASE_CLASSES and blocks[c]["net_expectancy_r"] is not None
           and blocks[c]["n"] >= 10]
    breaks = [{"from": a, "to": b, "gain": round(bv - av, 3)}
              for (a, av), (b, bv) in zip(seq, seq[1:], strict=False) if bv > av]

    def split(key: str) -> dict:
        groups: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for r in rows:
            groups[str(r.get(key))][r["entry_quality"]].append(r)
        return {g: {c: _block(v, sessions) for c, v in sorted(sub.items())}
                for g, sub in sorted(groups.items())}

    return {
        "resolved": len(rows),
        "classes": ordered,
        "class_cut_points": "measured per run by phase7.paths.chase_thresholds; "
                            "quintiles of consumed-move within this dataset, not "
                            "absolute quality and not the production chase guard",
        "by_class": blocks,
        "unmeasurable_entry_quality": {
            "n": len(by_class.get("UNKNOWN", [])),
            "pct_of_resolved": round(
                100.0 * len(by_class.get("UNKNOWN", [])) / max(1, len(rows)), 1),
            "by_instrument": {
                name: count for name, count in sorted(Counter(
                    r["instrument"] for r in by_class.get("UNKNOWN", [])).items())},
            "cause": "the consumed-move fraction could not be computed at signal "
                     "time, so phase 7 assigned no chase class",
            "reading": "UNKNOWN is an unmeasurable entry, NOT a bad one. It is "
                       "excluded from the ordering test and from any comparison "
                       "between classes, and it is reported rather than dropped so "
                       "the missing share is visible",
        },
        "ordering_check": {
            "expected": "net expectancy should fall from IDEAL_ENTRY to "
                        "SEVERELY_CHASED if chasing costs money",
            "comparable_classes": [c for c, _ in seq],
            "improvements_against_expectation": breaks,
            "verdict": ("UNTESTABLE" if len(seq) < 2
                        else "ORDERED_AS_EXPECTED" if not breaks
                        else "NOT_ORDERED"),
            "reading": "if the order does not hold, a chased entry in this sample is "
                       "a worse price on the same trade rather than a worse trade — "
                       "which argues for sizing and patience at entry, not for a new "
                       "rejection rule. Either way the production chase guard is "
                       "unchanged here",
        },
        "by_instrument": split("instrument"),
        "by_expiry_class": split("expiry_class"),
        "by_regime": split("regime"),
        "data_contamination": contamination,
        "guarantee": "phase7.paths.measure_chase / classify_chase are read, never "
                     "modified; no entry class gates anything in production",
    }
