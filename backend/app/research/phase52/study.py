"""Phase 52 §13/§16/§17 — run the grid, collapse it to families, grade it once.

The order of operations is the whole point, so it is stated here rather than
left to be inferred:

1. resample the stored minutes to the decision timeframe with the Phase 27
   aggregator, which labels every bar with its closing minute;
2. build the previous-day levels and the trailing daily ATR;
3. split the **sessions** chronologically, 60/20/20, before any event exists;
4. detect each session's event once per confirmation timeframe and resolve it
   once. The gap threshold, the stop cap and the cost gate are filters over that
   resolved table — they do not change an entry, a stop, a target or an exit, so
   resolving per variant would produce thirty-six copies of the same number;
5. hash each variant's entry set and collapse identical sets into one
   **event family**;
6. correct over the family count, then grade against the pre-registered bar.

Cost stress needs no re-resolution and gets none: multiplying the round trip
does not move the stop, the target or the clock, so the stressed net is the same
path with a larger charge. Re-walking it would only invite a difference.
"""
from __future__ import annotations

import datetime as dt
import hashlib

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase27 import bars as p27bars
from app.research.phase52 import (
    COST_BLOCKED,
    COST_STRESS_MULTIPLES,
    DECISION_TIMEFRAME_MINUTES,
    DISCOVERY,
    FDR_ALPHA,
    HISTORICAL_LEAD,
    MAX_DRAWDOWN_R,
    MAX_TOP_TRADE_SHARE,
    MAX_YEAR_SHARE,
    MIN_PROFIT_FACTOR,
    MIN_TRADES_DISCOVERY,
    MIN_TRADES_HOLDOUT,
    MIN_TRADES_VALIDATION,
    MIN_YEARS_POSITIVE,
    OVERFIT_RISK,
    PARTITION_SHARES,
    PARTITIONS,
    PROMISING_NEEDS_DATA,
    REJECTED,
    ROBUST_CANDIDATE,
    UNTOUCHED_HOLDOUT,
    VALIDATION,
    fingerprint,
    variants,
)
from app.research.phase52 import execute, levels, mechanism, metrics, stats

IST_OFFSET = 19_800
SECONDS_PER_DAY = 86_400

MIN_TRADES = {
    DISCOVERY: MIN_TRADES_DISCOVERY,
    VALIDATION: MIN_TRADES_VALIDATION,
    UNTOUCHED_HOLDOUT: MIN_TRADES_HOLDOUT,
}


def _year_quarter(ts: int) -> tuple[int, str]:
    """IST calendar year and quarter of a timestamp."""
    d = dt.datetime.fromtimestamp(int(ts) + IST_OFFSET, tz=dt.timezone.utc)
    return d.year, f"{d.year}Q{(d.month - 1) // 3 + 1}"


def _family_hash(rows: list[dict]) -> str:
    """Identity of an event set: the sorted entry timestamps, hashed.

    Two parameterizations that enter the same trades at the same instants are
    one hypothesis about the market wearing two names, and this is how the
    engine finds that out rather than asserting it.
    """
    payload = ",".join(str(r["entry_ts"]) for r in sorted(
        rows, key=lambda r: r["entry_ts"]
    ))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def resolved_table(instrument: str, confirmation: str) -> dict:
    """Every session's resolved event for one confirmation timeframe."""
    s1 = p24data.load_series(instrument)
    if s1 is None or len(s1) == 0:
        return {"instrument": instrument, "eligible": False, "rows": [],
                "funnel": {}, "bars": {}}
    s5, stats5 = p27bars.resample(s1, DECISION_TIMEFRAME_MINUTES)
    s15, stats15 = p27bars.resample(s1, 15)
    lv = levels.build(s5)
    rows, funnel = mechanism.event_table(instrument, s5, s15, lv, confirmation)

    if rows:
        fill_i = np.array([r["fill_index"] for r in rows], dtype=np.int64)
        side = np.array([r["side"] for r in rows], dtype=np.int64)
        entry = np.array([r["entry"] for r in rows], dtype=np.float64)
        stop = np.array([r["stop"] for r in rows], dtype=np.float64)
        target = np.array([r["target"] for r in rows], dtype=np.float64)
        last = np.array([r["session_last_bar"] for r in rows], dtype=np.int64)
        out = execute.resolve(
            instrument, s5.high, s5.low, s5.open,
            fill_i, side, entry, stop, target, last,
        )
        part = levels.chronological_partitions(
            lv["session"].astype(np.int64), PARTITION_SHARES
        )
        session_partition = {
            int(lv["session"][i]): str(part[i]) for i in range(len(part))
        }
        for k, r in enumerate(rows):
            for key, arr in out.items():
                r[key] = arr[k].item() if hasattr(arr[k], "item") else arr[k]
            year, quarter = _year_quarter(r["entry_ts"])
            r["year"] = year
            r["quarter"] = quarter
            r["partition"] = session_partition.get(r["session"], UNTOUCHED_HOLDOUT)
        rows = [r for r in rows if r["resolved"]]

    return {
        "instrument": instrument,
        "eligible": True,
        "confirmation": confirmation,
        "rows": rows,
        "funnel": funnel,
        "bars": {"five_minute": stats5, "fifteen_minute": stats15},
    }


def _stress(rows: list[dict], multiple: float) -> list[dict]:
    """The same resolved paths with the round trip charged ``multiple`` times.

    Cost does not move a stop, a target or a clock, so the path is unchanged and
    only the charge differs.
    """
    out: list[dict] = []
    for r in rows:
        cost = r["cost_points"] * float(multiple)
        net = r["gross_points"] - cost
        out.append({**r, "cost_points": cost, "net_points": net,
                    "net_r": net / r["risk_points"]})
    return out


def _partition_rows(rows: list[dict], partition: str) -> list[dict]:
    return [r for r in rows if r["partition"] == partition]


def _grade(
    per_partition: dict[str, dict],
    years: dict[str, dict],
    stress: dict[str, dict],
    overall: dict,
    *,
    fdr_pass: bool,
    had_candidates: bool,
) -> tuple[str, list[str]]:
    """§17's bar, applied in the order that refuses on the strongest ground first.

    Returns the final status and every reason the status is not
    ``ROBUST_CANDIDATE``, because a single label loses the argument.
    """
    reasons: list[str] = []
    disc = per_partition[DISCOVERY]
    val = per_partition[VALIDATION]
    hold = per_partition[UNTOUCHED_HOLDOUT]

    if not overall.get("measured"):
        return (COST_BLOCKED if had_candidates else REJECTED), [
            "the gate admitted no session" if had_candidates
            else "the mechanism produced no event"
        ]

    if disc["trade_count"] < MIN_TRADES[DISCOVERY]:
        reasons.append(
            f"discovery has {disc['trade_count']} trades against a declared "
            f"floor of {MIN_TRADES[DISCOVERY]}"
        )
        positive = (disc.get("net_expectancy_r") or 0.0) > 0
        return (PROMISING_NEEDS_DATA if positive else REJECTED), reasons

    if (disc.get("net_expectancy_r") or 0.0) <= 0:
        reasons.append(
            f"discovery net expectancy {disc['net_expectancy_r']:+.3f}R is not "
            "positive, so there is nothing to validate"
        )
        return REJECTED, reasons

    if val["trade_count"] < MIN_TRADES[VALIDATION]:
        reasons.append(
            f"validation has {val['trade_count']} trades against a declared "
            f"floor of {MIN_TRADES[VALIDATION]}"
        )
        return PROMISING_NEEDS_DATA, reasons

    if (val.get("net_expectancy_r") or 0.0) <= 0:
        reasons.append(
            f"validation net expectancy {val['net_expectancy_r']:+.3f}R is not "
            "positive: the discovery result did not repeat forward in time"
        )
        return HISTORICAL_LEAD, reasons

    if not fdr_pass:
        reasons.append(
            "the discovery result does not survive false-discovery correction "
            "over the event families tested"
        )
        return OVERFIT_RISK, reasons

    if hold["trade_count"] < MIN_TRADES[UNTOUCHED_HOLDOUT]:
        reasons.append(
            f"the untouched holdout has {hold['trade_count']} trades against a "
            f"declared floor of {MIN_TRADES[UNTOUCHED_HOLDOUT]}"
        )
        return PROMISING_NEEDS_DATA, reasons

    if (hold.get("net_expectancy_r") or 0.0) <= 0:
        reasons.append(
            f"untouched holdout net expectancy {hold['net_expectancy_r']:+.3f}R "
            "is not positive"
        )
        return HISTORICAL_LEAD, reasons

    pf = overall.get("profit_factor")
    if pf is None or pf < MIN_PROFIT_FACTOR:
        reasons.append(f"profit factor {pf} is below the declared {MIN_PROFIT_FACTOR}")
    if (overall.get("max_drawdown_r") or 0.0) > MAX_DRAWDOWN_R:
        reasons.append(
            f"drawdown {overall['max_drawdown_r']:.1f}R exceeds the declared "
            f"bound of {MAX_DRAWDOWN_R}R"
        )
    share = overall.get("top_trade_share")
    if share is not None and share > MAX_TOP_TRADE_SHARE:
        reasons.append(
            f"one trade is {100.0 * share:.0f}% of the net result, above the "
            f"declared {100.0 * MAX_TOP_TRADE_SHARE:.0f}%"
        )
    positive_years = sum(1 for v in years.values() if v["net_expectancy_r"] > 0)
    if positive_years < MIN_YEARS_POSITIVE:
        reasons.append(
            f"{positive_years} calendar years are positive against a declared "
            f"minimum of {MIN_YEARS_POSITIVE}"
        )
    total = overall.get("net_total_r") or 0.0
    if total > 0 and years:
        worst = max(v["net_total_r"] / total for v in years.values())
        if worst > MAX_YEAR_SHARE:
            reasons.append(
                f"one calendar year carries {100.0 * worst:.0f}% of the net "
                f"result, above the declared {100.0 * MAX_YEAR_SHARE:.0f}%"
            )
    for mult, row in stress.items():
        if float(mult) <= 1.0:
            continue
        exp = row.get("net_expectancy_r")
        if exp is None or exp <= 0:
            reasons.append(
                f"net expectancy is {exp} at {mult}x cost, so the result is a "
                "cost assumption rather than an edge"
            )
    if reasons:
        return OVERFIT_RISK, reasons
    return ROBUST_CANDIDATE, []


def run_instrument(instrument: str) -> dict:
    """The whole study for one instrument: grid, families, grades, funnel."""
    eligibility = stats.eligibility(instrument)
    if not eligibility.get("eligible"):
        return {
            "instrument": instrument,
            "eligible": False,
            "eligibility": eligibility,
            "rows": [],
            "families": [],
            "totals": {
                "TOTAL_HYPOTHESES": 0, "UNIQUE_EVENT_FAMILIES": 0,
                "DISCOVERY_LEADS": 0, "VALIDATION_POSITIVE": 0,
                "HOLDOUT_POSITIVE": 0, "ROBUST_CANDIDATES": 0,
            },
        }

    tables = {
        conf: resolved_table(instrument, conf)
        for conf in {v["confirmation"] for v in variants()}
    }

    rows_out: list[dict] = []
    for spec in variants():
        table = tables[spec["confirmation"]]
        selected = [
            r for r in table["rows"]
            if r["gap_atr"] >= spec["gap_atr"]
            and r["stop_atr_needed"] <= spec["max_stop_atr"]
            and r["cost_multiple"] >= spec["cost_gate"]
        ]
        overall = metrics.describe(selected)
        years = metrics.per_period(selected, "year")
        quarters = metrics.per_period(selected, "quarter")
        per_partition = {
            p: metrics.describe(_partition_rows(selected, p)) for p in PARTITIONS
        }
        stress = {
            f"{m}": metrics.describe(_stress(selected, m))
            for m in COST_STRESS_MULTIPLES
        }
        rows_out.append({
            **spec,
            "instrument": instrument,
            "candidate_id": f"{instrument}_{spec['variant_id']}",
            "mechanism_family": "GAP_FAILURE_RANGE_REENTRY_REVERSION",
            "human_readable_rule": _rule_text(spec),
            "entry_rule": _entry_text(spec),
            "exit_rule": (
                "stop at the failed extension's extreme plus 0.25 ATR, target "
                "at the previous day's close, flat after 120 minutes or at the "
                "session close, same-bar tie taken as the stop"
            ),
            "event_family": _family_hash(selected),
            "trades": len(selected),
            "gate_refused": len(table["rows"]) - len(selected),
            "overall": overall,
            "per_partition": per_partition,
            "per_year": years,
            "per_quarter": quarters,
            "cost_stress": stress,
            "session_distribution": metrics.session_distribution(selected),
            "effect_reading": metrics.effect_reading(
                per_partition[DISCOVERY], years
            ),
            "direction_split": _direction_split(selected),
            "had_candidates": bool(table["rows"]),
        })

    # §16 — collapse to families before anything is corrected or ranked.
    families: dict[str, list[dict]] = {}
    for row in rows_out:
        families.setdefault(row["event_family"], []).append(row)
    family_count = len(families)

    # One p-value per family, from its discovery partition, corrected over the
    # family count rather than the parameterization count.
    reps = [rows[0] for rows in families.values()]
    p_values = [
        float(r["per_partition"][DISCOVERY].get("p_value_one_sided") or 1.0)
        for r in reps
    ]
    passes = metrics.benjamini_hochberg(
        p_values, tests=family_count, alpha=FDR_ALPHA
    )
    fdr_by_family = {
        r["event_family"]: bool(ok) for r, ok in zip(reps, passes)
    }

    for row in rows_out:
        row["fdr_pass"] = fdr_by_family.get(row["event_family"], False)
        status, reasons = _grade(
            row["per_partition"], row["per_year"], row["cost_stress"],
            row["overall"], fdr_pass=row["fdr_pass"],
            had_candidates=row["had_candidates"],
        )
        row["final_status"] = status
        row["status_reasons"] = reasons
        row["overfit_status"] = (
            "SURVIVES_CORRECTION_OVER_THE_FAMILY_COUNT" if row["fdr_pass"]
            else "INDISTINGUISHABLE_FROM_THE_FAMILIES_IT_WAS_SELECTED_FROM"
        )

    discovery_leads = [
        r for r in rows_out
        if (r["per_partition"][DISCOVERY].get("net_expectancy_r") or 0.0) > 0
        and r["per_partition"][DISCOVERY]["trade_count"] >= MIN_TRADES[DISCOVERY]
    ]
    validation_positive = [
        r for r in rows_out
        if (r["per_partition"][VALIDATION].get("net_expectancy_r") or 0.0) > 0
        and r["per_partition"][VALIDATION]["trade_count"]
        >= MIN_TRADES[VALIDATION]
    ]
    holdout_positive = [
        r for r in rows_out
        if (r["per_partition"][UNTOUCHED_HOLDOUT].get("net_expectancy_r") or 0.0) > 0
        and r["per_partition"][UNTOUCHED_HOLDOUT]["trade_count"]
        >= MIN_TRADES[UNTOUCHED_HOLDOUT]
    ]
    robust = [r for r in rows_out if r["final_status"] == ROBUST_CANDIDATE]

    return {
        "instrument": instrument,
        "eligible": True,
        "eligibility": eligibility,
        "fingerprint": fingerprint(),
        "rows": rows_out,
        "families": [
            {
                "event_family": fam,
                "variants": [r["variant_id"] for r in rs],
                "trades": rs[0]["trades"],
                "fdr_pass": fdr_by_family.get(fam, False),
            }
            for fam, rs in sorted(families.items(), key=lambda kv: -kv[1][0]["trades"])
        ],
        "funnels": {c: t["funnel"] for c, t in tables.items()},
        "bars": next(iter(tables.values()))["bars"] if tables else {},
        "partition_sessions": _partition_sessions(tables),
        "totals": {
            "TOTAL_HYPOTHESES": len(rows_out),
            "UNIQUE_EVENT_FAMILIES": family_count,
            "DISCOVERY_LEADS": len(discovery_leads),
            "VALIDATION_POSITIVE": len(validation_positive),
            "HOLDOUT_POSITIVE": len(holdout_positive),
            "ROBUST_CANDIDATES": len(robust),
        },
    }


def _partition_sessions(tables: dict[str, dict]) -> dict[str, int]:
    """How many sessions produced an event in each partition, per confirmation."""
    out: dict[str, int] = {}
    for conf, table in tables.items():
        for p in PARTITIONS:
            key = f"{conf}:{p}"
            out[key] = len({
                r["session"] for r in table["rows"] if r.get("partition") == p
            })
    return out


def _direction_split(rows: list[dict]) -> dict[str, dict]:
    """Upside-gap shorts and downside-gap longs, separately.

    A mechanism that only works one way round is not the mechanism as stated,
    and pooling the two sides would hide that.
    """
    out: dict[str, dict] = {}
    for label in (mechanism.GAP_UP, mechanism.GAP_DOWN):
        sel = [r for r in rows if r["direction"] == label]
        if sel:
            out[label] = metrics.describe(sel)
    return out


def _rule_text(spec: dict) -> str:
    return (
        f"session opens more than {spec['gap_atr']:.2f} ATR beyond the previous "
        f"day's extreme; after the first 15 minutes price extends past the "
        f"opening area and then a {spec['confirmation']} closes back inside the "
        f"previous day's range; enter the next 5-minute bar against the gap, "
        f"stop beyond the failed extreme by 0.25 ATR capped at "
        f"{spec['max_stop_atr']:.2f} ATR, target the previous day's close, only "
        f"if that target is at least {spec['cost_gate']:.0f} round trips away, "
        f"flat within 120 minutes"
    )


def _entry_text(spec: dict) -> str:
    return (
        f"gap >= {spec['gap_atr']:.2f} ATR, extension beyond the 15-minute "
        f"opening area, {spec['confirmation']}, fill at the next 5-minute open"
    )


def run(instruments: tuple[str, ...]) -> dict:
    """The study over several instruments, with the five §16 totals summed."""
    per = [run_instrument(name) for name in instruments]
    totals = {
        k: sum(p["totals"][k] for p in per)
        for k in (
            "TOTAL_HYPOTHESES", "UNIQUE_EVENT_FAMILIES", "DISCOVERY_LEADS",
            "VALIDATION_POSITIVE", "HOLDOUT_POSITIVE", "ROBUST_CANDIDATES",
        )
    }
    return {
        "fingerprint": fingerprint(),
        "instruments": per,
        "totals": totals,
    }
