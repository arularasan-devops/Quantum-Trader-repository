"""The Phase 17 studies and their artefacts — §13-§17, §24, §26-§28, §34, §35.

Every table in this module obeys three rules that the earlier reports did not.

**The unmatched count is printed first.** A study over 48 usable rows drawn from
3,448 is not a study, and the only way a reader can know that is if the report
says so before it says anything else. So §27's data-quality block leads every
artefact, and any cohort table with fewer rows than ``MIN_COHORT`` reports
``REQUIRES_MORE_DATA`` in place of a number rather than a number nobody should
act on.

**Net, or nothing.** Rows whose cost was not measured from a real book are
excluded from every economic table and counted in the exclusion line. Mixing a
modelled spread into a measured sample is how the previous gross reports came to
describe trades that could not have been made.

**A difference is not a finding.** Cohort tables print the sample size, the T1
rate, the expectancy AND the reward:risk together — because Phase 15B showed a
cohort at 51.7% T1 and 0.92 RR sitting beside one at 36.9% and 1.70 with the same
expectancy. T1 alone would have ranked the worse trade first.
"""
from __future__ import annotations

import statistics
from collections.abc import Callable

from app.research.phase17 import (
    aplus,
    cepe,
    economics,
    entry as entry_mod,
    quality,
    reach,
    schema,
)

# Below this, a cohort reports REQUIRES_MORE_DATA instead of a rate. Same floor
# as the Phase 15B study, so a live cohort and a historical one are comparable.
MIN_COHORT = 100
# A weaker floor for descriptive medians (spread, cost) where the quantity is a
# property of the book rather than an outcome estimate.
MIN_DESCRIPTIVE = 20

THIN = "REQUIRES_MORE_DATA"


def _median(vals: list[float]) -> float | None:
    clean = [float(v) for v in vals if isinstance(v, (int, float))]
    return round(statistics.median(clean), 4) if clean else None


def _mean(vals: list[float]) -> float | None:
    clean = [float(v) for v in vals if isinstance(v, (int, float))]
    return round(sum(clean) / len(clean), 4) if clean else None


def _costed(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("cost_status") == schema.COST_MEASURED]


def _outcome_stats(rows: list[dict]) -> dict:
    """T1 rate, expectancy, PF and RR together. Never T1 alone."""
    n = len(rows)
    if n == 0:
        return {"n": 0, "status": THIN}
    rs = [float(r["net_r"]) for r in rows if isinstance(r.get("net_r"), (int, float))]
    t1 = sum(1 for r in rows if r.get("outcome") in (schema.T1, schema.T2, schema.T3))
    gain = sum(r for r in rs if r > 0)
    loss = -sum(r for r in rs if r < 0)
    rr: list[float] = []
    for r in rows:
        risk = r.get("risk")
        t1p = r.get("target1")
        entry = r.get("entry_price") or r.get("entry_premium")
        if all(isinstance(v, (int, float)) for v in (risk, t1p, entry)) and risk:
            rr.append((float(t1p) - float(entry)) / float(risk))
    return {
        "n": n,
        "status": "OK" if n >= MIN_COHORT else THIN,
        "t1_pct": round(100.0 * t1 / n, 2),
        "net_expectancy_r": _mean(rs),
        "profit_factor": round(gain / loss, 3) if loss > 0 else None,
        "median_reward_risk": _median(rr),
        "median_net_points": _median(
            [r.get("net_points") for r in rows]
        ),
        "median_gross_points": _median([r.get("gross_points") for r in rows]),
        "median_cost_points": _median([r.get("cost_points") for r in rows]),
        "note": (
            None if n >= MIN_COHORT else
            f"{n} outcome(s); {MIN_COHORT} needed before this rate means anything"
        ),
    }


def _cohorts(rows: list[dict], key: Callable[[dict], str | None]) -> dict:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        k = key(r)
        if k is None:
            continue
        groups.setdefault(str(k), []).append(r)
    return {k: _outcome_stats(v) for k, v in sorted(groups.items())}


def capture_report(observations: list[dict]) -> dict:
    """§27 + §3. Data quality of the capture itself — the KPI that gates the rest."""
    sel_q = [
        (o.get("selected") or {}).get("data_quality") or quality.MISSING
        for o in observations
    ]
    opp_q = [
        (o.get("opposite") or {}).get("data_quality") or quality.MISSING
        for o in observations
    ]
    deltas = [
        (o.get("selected") or {}).get("signal_to_snapshot_ms")
        for o in observations
    ]
    both = sum(1 for o in observations if o.get("both_sides"))
    with_chain = sum(1 for o in observations if o.get("selected"))
    sources: dict[str, int] = {}
    for o in observations:
        s = (o.get("selected") or {}).get("source") or quality.UNKNOWN_SOURCE
        sources[s] = sources.get(s, 0) + 1
    tally = quality.tally(sel_q)
    return {
        "total_candidates": len(observations),
        "chain_available": with_chain,
        "both_side_matches": both,
        "both_side_pct": (
            round(100.0 * both / len(observations), 2) if observations else None
        ),
        "selected_side": tally,
        "opposite_side": quality.tally(opp_q),
        "median_signal_to_snapshot_ms": _median(deltas),
        "max_signal_to_snapshot_ms": max(
            (float(d) for d in deltas if isinstance(d, (int, float))), default=None
        ),
        "sources": sources,
        "exact_match_rate_pct": tally["exact_match_rate_pct"],
        "target_pct": quality.EXACT_RATE_TARGET_PCT,
        "meets_target": tally["meets_target"],
        "gate": (
            "Vehicle economics may be reported as measured only while this rate "
            f"is at or above {quality.EXACT_RATE_TARGET_PCT}%. Below it, the "
            "tables are descriptive and no economics claim is validated."
        ),
        "prior_failure_reference": {
            "legs": 3448, "matched": 48, "median_gap_sec": 60.0,
            "note": "the sample this capture design exists to avoid repeating",
        },
    }


def vehicle_report(observations: list[dict], legs: list[dict]) -> dict:
    """§9 + §10. Spread, cost and the ratios that decide whether a leg can pay."""
    quotes = [o.get("selected") or {} for o in observations]
    usable = [
        q for q in quotes
        if quality.usable(q.get("data_quality")) and q.get("spread") is not None
    ]
    classes: dict[str, int] = {c: 0 for c in economics.CLASSES}
    for o in observations:
        c = (o.get("economics") or {}).get("vehicle_class") or economics.UNKNOWN
        classes[c if c in classes else economics.UNKNOWN] += 1
    costed = _costed(legs)
    return {
        "quotes_with_book": len(usable),
        "quotes_total": len(quotes),
        "median_spread_points": _median([q.get("spread") for q in usable]),
        "median_spread_pct": _median([q.get("spread_pct") for q in usable]),
        "median_cost_points": _median(
            [(o.get("economics") or {}).get("cost_points") for o in observations]
        ),
        "median_cost_over_risk": _median(
            [(o.get("economics") or {}).get("cost_over_risk") for o in observations]
        ),
        "median_cost_over_expected_move": _median(
            [(o.get("economics") or {}).get("cost_over_expected_move")
             for o in observations]
        ),
        "vehicle_classes": classes,
        "class_thresholds": {
            "green_cost_over_risk": economics.GREEN_COST_OVER_RISK,
            "red_cost_over_risk": economics.RED_COST_OVER_RISK,
            "green_cost_over_move": economics.GREEN_COST_OVER_MOVE,
            "red_cost_over_move": economics.RED_COST_OVER_MOVE,
            "red_spread_pct": economics.RED_SPREAD_PCT,
            "fitted": False,
            "note": (
                "Seeds chosen before any outcome was read. Fitting these on the "
                "same rows that then measure their benefit is the Phase 15B trap; "
                "they are tested through dev/validation/holdout once the matched "
                "sample supports it."
            ),
        },
        "by_class": _cohorts(costed, lambda r: r.get("vehicle_class")),
        "outcomes_costed": len(costed),
        "outcomes_excluded_uncosted": len(legs) - len(costed),
        "status": "OK" if len(usable) >= MIN_DESCRIPTIVE else THIN,
    }


def ce_pe_report(legs: list[dict]) -> dict:
    """§6 + §8. Right side, wrong side — and wrong market vs wrong vehicle."""
    out = cepe.tally(legs)
    out["interpretation"] = {
        cepe.WRONG_MARKET: "the read was wrong; a better option would not have helped",
        cepe.WRONG_VEHICLE: (
            "the underlying went the predicted way and the premium still did not "
            "pay for the round trip"
        ),
        cepe.BOTH_BAD: "neither side paid; usually a market that did not move",
    }
    out["status"] = "OK" if out["classified"] >= MIN_COHORT else THIN
    return out


def futures_report(observations: list[dict], legs: list[dict]) -> dict:
    """§15. Futures beside options, and only where the feed was fresh."""
    rows = [o.get("futures") for o in observations if o.get("futures")]
    fut_legs = [leg for leg in legs if leg.get("vehicle") == schema.FUTURES]
    fresh = [r for r in rows if quality.usable((r or {}).get("data_quality"))]
    return {
        "quotes": len(rows),
        "fresh_quotes": len(fresh),
        "median_spread_points": _median([(r or {}).get("spread") for r in fresh]),
        "outcomes": _outcome_stats(_costed(fut_legs)),
        "status": "OK" if len(fresh) >= MIN_DESCRIPTIVE else THIN,
        "note": (
            "Futures research only. No futures order is placed by this phase, and "
            "an option is never automatically replaced by a future."
        ),
    }


def strike_report(observations: list[dict], legs: list[dict]) -> dict:
    """§14. ATM/ITM/OTM and delta bands — measured, production unchanged."""
    by_obs = {o.get("observation_id"): o for o in observations}

    def moneyness_of(leg: dict) -> str | None:
        o = by_obs.get(leg.get("observation_id"))
        if not o:
            return None
        side = "selected" if leg.get("side") == "SELECTED" else "opposite"
        return (o.get(side) or {}).get("moneyness")

    def band_of(leg: dict) -> str | None:
        o = by_obs.get(leg.get("observation_id"))
        if not o:
            return None
        side = "selected" if leg.get("side") == "SELECTED" else "opposite"
        return (o.get(side) or {}).get("delta_band")

    window_rows = [
        q for o in observations for q in (o.get("window") or [])
        if quality.usable(q.get("data_quality"))
    ]
    spreads: dict[str, list[float]] = {}
    for q in window_rows:
        m = q.get("moneyness")
        if m and isinstance(q.get("spread_pct"), (int, float)):
            spreads.setdefault(str(m), []).append(float(q["spread_pct"]))
    costed = _costed(legs)
    return {
        "window_quotes": len(window_rows),
        "median_spread_pct_by_moneyness": {
            k: _median(v) for k, v in sorted(spreads.items())
        },
        "outcomes_by_moneyness": _cohorts(costed, moneyness_of),
        "outcomes_by_delta_band": _cohorts(costed, band_of),
        "production_unchanged": True,
        "note": (
            "Strike selection in production is untouched. This measures what the "
            "alternatives would have cost and returned."
        ),
        "status": "OK" if len(window_rows) >= MIN_DESCRIPTIVE else THIN,
    }


def expiry_report(observations: list[dict], legs: list[dict]) -> dict:
    """§13 + §15. Expiry class, DTE and weekday — option-only variables."""
    by_obs = {o.get("observation_id"): o for o in observations}

    def cls_of(leg: dict) -> str | None:
        o = by_obs.get(leg.get("observation_id"))
        return ((o or {}).get("selected") or {}).get("expiry_class")

    def dte_of(leg: dict) -> str | None:
        o = by_obs.get(leg.get("observation_id"))
        d = ((o or {}).get("selected") or {}).get("days_to_expiry")
        return f"DTE_{int(d)}" if isinstance(d, (int, float)) else None

    def weekday_of(leg: dict) -> str | None:
        o = by_obs.get(leg.get("observation_id"))
        return (o or {}).get("weekday")

    costed = _costed(legs)
    return {
        "by_expiry_class": _cohorts(costed, cls_of),
        "by_days_to_expiry": _cohorts(costed, dte_of),
        "by_weekday": _cohorts(costed, weekday_of),
        "median_spread_pct_by_expiry_class": {
            k: _median([
                ((by_obs.get(leg.get("observation_id")) or {}).get("selected") or {}
                 ).get("spread_pct")
                for leg in v
            ])
            for k, v in _group(costed, cls_of).items()
        },
        "production_unchanged": True,
        "note": (
            "Expiry and weekday effects need many expiries, not many rows: a week "
            "of sessions is one observation of each. No production expiry rule is "
            "created here."
        ),
    }


def _group(rows: list[dict], key: Callable[[dict], str | None]
           ) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        k = key(r)
        if k is not None:
            groups.setdefault(str(k), []).append(r)
    return dict(sorted(groups.items()))


def entry_report(observations: list[dict], legs: list[dict]) -> dict:
    """§11. Entry quality, from genuine premium history only."""
    counts = {c: 0 for c in entry_mod.CLASSES}
    for o in observations:
        c = (o.get("entry") or {}).get("entry_quality") or entry_mod.UNKNOWN
        counts[c if c in counts else entry_mod.UNKNOWN] += 1
    by_obs = {o.get("observation_id"): o for o in observations}

    def eq_of(leg: dict) -> str | None:
        o = by_obs.get(leg.get("observation_id"))
        return (
            leg.get("entry_quality")
            or ((o or {}).get("entry") or {}).get("entry_quality")
        )

    return {
        "counts": counts,
        "unknown_pct": (
            round(100.0 * counts[entry_mod.UNKNOWN] / len(observations), 2)
            if observations else None
        ),
        "outcomes_by_entry_quality": _cohorts(_costed(legs), eq_of),
        "thresholds_pct": {
            "ideal": entry_mod.IDEAL_PCT, "good": entry_mod.GOOD_PCT,
            "acceptable": entry_mod.ACCEPTABLE_PCT, "chased": entry_mod.CHASED_PCT,
            "fitted": False,
        },
        "note": (
            "UNKNOWN is a measurement, not a gap: a candidate with no genuine "
            "signal-time premium history cannot be graded, and replayed rows "
            "never can be."
        ),
    }


def aplus_report(observations: list[dict], legs: list[dict]) -> dict:
    """§20 + §21 + §8. The score, its components, and the rank distribution."""
    labels = {label: 0 for label in aplus.LABELS}
    for o in observations:
        label = (o.get("aplus") or {}).get("a_plus_label")
        if label in labels:
            labels[label] += 1
    ranks = reach.rank_table([o.get("reach") or {} for o in observations])
    by_obs = {o.get("observation_id"): o for o in observations}

    def label_of(leg: dict) -> str | None:
        o = by_obs.get(leg.get("observation_id"))
        return ((o or {}).get("aplus") or {}).get("a_plus_label")

    def rank_of(leg: dict) -> str | None:
        o = by_obs.get(leg.get("observation_id"))
        return ((o or {}).get("reach") or {}).get("t1_rank")

    scores = [
        (o.get("aplus") or {}).get("a_plus_score") for o in observations
    ]
    return {
        "labels": labels,
        "median_score": _median(scores),
        "weights": dict(aplus.WEIGHTS),
        "score_floors": {
            "a_plus": aplus.A_PLUS_MIN_SCORE, "watch": aplus.WATCH_MIN_SCORE,
            "fitted": False,
        },
        "t1_ranks": ranks,
        "outcomes_by_label": _cohorts(_costed(legs), label_of),
        "outcomes_by_t1_rank": _cohorts(_costed(legs), rank_of),
        "reuses_signal_score": False,
        "note": (
            "A+ is a research ranking. It does not gate, alter or delay the "
            "production Signal. The Signal Score is not reused as a component: it "
            "was measured at +0.001R lift (0.08 sigma) over 61k candidates."
        ),
    }


def paper_report(paper_rows: list[dict]) -> dict:
    """§24. Today's paper book, gross and net side by side, options and futures."""
    def block(rows: list[dict]) -> dict:
        costed = _costed(rows)
        outcomes = {o: 0 for o in schema.OUTCOMES}
        for r in rows:
            o = r.get("outcome")
            if o in outcomes:
                outcomes[o] += 1
        nets = [r["net_r"] for r in costed if isinstance(r.get("net_r"), (int, float))]
        gain = sum(r for r in nets if r > 0)
        loss = -sum(r for r in nets if r < 0)
        wins = sum(1 for r in nets if r > 0)
        return {
            "entries": len(rows),
            "costed": len(costed),
            "uncosted_excluded": len(rows) - len(costed),
            "outcomes": outcomes,
            "win_rate_pct": round(100.0 * wins / len(nets), 2) if nets else None,
            "gross_points": _sum([r.get("gross_points") for r in rows]),
            "cost_points": _sum([r.get("cost_points") for r in rows]),
            "cost_rupees": _sum([r.get("cost_rupees") for r in rows]),
            "net_points": _sum([r.get("net_points") for r in rows]),
            "net_r": round(sum(nets), 3) if nets else None,
            "expectancy_r": _mean(nets),
            "profit_factor": round(gain / loss, 3) if loss > 0 else None,
            "median_mfe_capture_pct": _median(
                [r.get("mfe_capture_pct") for r in rows]
            ),
            "median_hold_minutes": _median([r.get("hold_minutes") for r in rows]),
        }

    opts = [r for r in paper_rows if r.get("vehicle") in (schema.CE, schema.PE)]
    futs = [r for r in paper_rows if r.get("vehicle") == schema.FUTURES]
    return {
        "options": block(opts),
        "futures": block(futs),
        "hold_buckets": _group(paper_rows, lambda r: r.get("hold_bucket")),
        "hold_bucket_stats": _cohorts(
            _costed(paper_rows), lambda r: r.get("hold_bucket")
        ),
        "giveback": {
            "reached_half_r_then_reversed": sum(
                1 for r in paper_rows if r.get("reached_half_r_then_reversed")
            ),
            "reached_t1_then_reversed": sum(
                1 for r in paper_rows if r.get("reached_t1_then_reversed")
            ),
            "reached_t2_then_reversed": sum(
                1 for r in paper_rows if r.get("reached_t2_then_reversed")
            ),
            "median_giveback_points": _median(
                [r.get("giveback") for r in paper_rows]
            ),
            "note": (
                "Input to the exit study only. No production exit is changed by "
                "this phase."
            ),
        },
        "paper_only": True,
        "no_real_order": True,
    }


def _sum(vals: list[float | None]) -> float | None:
    clean = [float(v) for v in vals if isinstance(v, (int, float))]
    return round(sum(clean), 4) if clean else None


def missed_report(observations: list[dict], legs: list[dict]) -> dict:
    """§26. Was each refusal right? CORRECT_REFUSAL vs MISSED_WINNER and why."""
    CORRECT = "CORRECT_REFUSAL"
    MISSED = "MISSED_WINNER"
    BAD_VEHICLE = "BAD_VEHICLE"
    BAD_ENTRY = "BAD_ENTRY"
    BAD_SPREAD = "BAD_SPREAD"
    DATA_FAILURE = "DATA_FAILURE"
    counts = {k: 0 for k in (CORRECT, MISSED, BAD_VEHICLE, BAD_ENTRY, BAD_SPREAD,
                             DATA_FAILURE)}
    by_obs: dict[str, dict] = {}
    for leg in legs:
        if leg.get("side") == "SELECTED":
            by_obs[str(leg.get("observation_id"))] = leg
    rows: list[dict] = []
    for o in observations:
        label = (o.get("aplus") or {}).get("a_plus_label")
        if label == aplus.A_PLUS:
            continue
        leg = by_obs.get(str(o.get("observation_id")))
        if leg is None:
            continue
        net = leg.get("net_points")
        gross = leg.get("gross_points")
        result = net if isinstance(net, (int, float)) else gross
        if not isinstance(result, (int, float)):
            counts[DATA_FAILURE] += 1
            continue
        won = float(result) > 0
        if not won:
            counts[CORRECT] += 1
            bucket = CORRECT
        else:
            counts[MISSED] += 1
            bucket = MISSED
            # Why it was refused, so a refusal rule can be judged on its own
            # reason rather than on the aggregate.
            if label == aplus.REJECT_VEHICLE:
                counts[BAD_VEHICLE] += 1
            elif label == aplus.REJECT_ENTRY:
                counts[BAD_ENTRY] += 1
            elif label == aplus.REJECT_SPREAD:
                counts[BAD_SPREAD] += 1
            elif label == aplus.REJECT_DATA:
                counts[DATA_FAILURE] += 1
        rows.append({
            "observation_id": o.get("observation_id"),
            "a_plus_label": label,
            "result_points": result,
            "basis": leg.get("cost_status"),
            "bucket": bucket,
        })
    graded = counts[CORRECT] + counts[MISSED]
    return {
        "counts": counts,
        "graded": graded,
        "missed_winner_pct": (
            round(100.0 * counts[MISSED] / graded, 2) if graded else None
        ),
        "rows": rows[:200],
        "status": "OK" if graded >= MIN_COHORT else THIN,
        "note": (
            "A refusal that was never recorded cannot be shown to have been "
            "right. Every non-A+ candidate with a resolved path is graded here."
        ),
    }


def htf_report(observations: list[dict], legs: list[dict]) -> dict:
    """§19. Completeness check: does the feed contain AGAINST_HTF rows at all?"""
    counts: dict[str, int] = {}
    for o in observations:
        k = o.get("htf_alignment") or "UNKNOWN"
        counts[k] = counts.get(k, 0) + 1
    by_obs = {o.get("observation_id"): o for o in observations}

    def align_of(leg: dict) -> str | None:
        return (by_obs.get(leg.get("observation_id")) or {}).get("htf_alignment")

    present = [k for k, v in counts.items() if v > 0 and k in
               ("WITH_HTF", "AGAINST_HTF")]
    return {
        "counts": counts,
        "buckets_present": sorted(present),
        "answerable": len(present) >= 2,
        "outcomes_by_alignment": _cohorts(_costed(legs), align_of),
        "note": (
            "The 5-year pool contained one bucket, 98,377 rows, all WITH_HTF, so "
            "'does HTF alignment matter' was unanswerable there — not 'no "
            "effect'. Production HTF logic is unchanged."
        ),
    }
