"""Phase 53 — the registered grid, the event families, the correction, the bar.

The shape of the computation, and why it is this shape:

* **eight event tables.** Only ``breakout_confirmation``, ``retest_confirmation``
  and ``retest_tolerance`` change which instants are entered, so the expensive
  detection runs eight times rather than sixty-four;
* **thirty-two resolutions.** ``stop_buffer`` fixes the stop and therefore the
  risk, and ``target_r`` scales the target off that risk, so each event table is
  resolved four times. A trade's outcome genuinely differs across those four:
  the same entry with a wider stop and a 2R target is a different trade, not a
  relabelled one;
* **sixty-four rows.** The two cost gates only filter rows that are already
  resolved;
* **families before correction.** Rows whose actual entry instants are identical
  are one *event* family. A distinct hypothesis is an event family **paired with
  a target multiple**, because 1.5R and 2.0R over the same entries are two
  claims about the same events rather than one claim spelled twice — and the
  false-discovery denominator stays the **full sixty-four**, never the count of
  distinct hypotheses and certainly never the count of survivors. Correcting
  against the survivors is exactly how a mined fluke passes.

The grade is applied in the order that refuses on the strongest ground first, so
the reported reason is the binding one rather than the first one checked.
"""
from __future__ import annotations

import datetime as dt
import hashlib

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase27 import bars as p27bars
from app.research.phase53 import (
    COST_BLOCKED,
    COST_STRESS_MULTIPLES,
    DECISION_TIMEFRAME_MINUTES,
    DISCOVERY,
    FDR_ALPHA,
    HISTORICAL_LEAD,
    LONG_BREAKOUT,
    MAX_DRAWDOWN_R,
    MAX_QUARTER_SHARE,
    MAX_STOP_ATR,
    MAX_TOP_TRADE_SHARE,
    MAX_YEAR_SHARE,
    MECHANISM,
    MIN_PROFIT_FACTOR,
    MIN_SESSIONS_DISCOVERY,
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
    SHORT_BREAKOUT,
    STOP_NOT_POSITIVE,
    STOP_TOO_WIDE,
    UNTOUCHED_HOLDOUT,
    VALIDATION,
    fingerprint,
    variants,
)
from app.research.phase53 import execute, levels, mechanism, metrics, stats

IST_OFFSET = 19_800

MIN_TRADES = {
    DISCOVERY: MIN_TRADES_DISCOVERY,
    VALIDATION: MIN_TRADES_VALIDATION,
    UNTOUCHED_HOLDOUT: MIN_TRADES_HOLDOUT,
}


def _year_quarter(ts: int) -> tuple[int, str]:
    """IST calendar year and quarter of a timestamp."""
    d = dt.datetime.fromtimestamp(int(ts) + IST_OFFSET, tz=dt.timezone.utc)
    return d.year, f"{d.year}Q{(d.month - 1) // 3 + 1}"


def _family_hash(instrument: str, rows: list[dict]) -> str:
    """Identity of an event set: instrument, then sorted entry instants and sides.

    The side is part of the identity because a long and a short can fill on the
    same instant in the same session, and two variants that enter opposite ways
    at the same moment are emphatically not one hypothesis. The instrument is
    part of it so two instruments cannot share a family by coincidence of
    timestamps and have their counts silently merged.
    """
    payload = instrument + "|" + ",".join(
        f"{r['entry_ts']}:{r['side']}"
        for r in sorted(rows, key=lambda r: (r["entry_ts"], r["side"]))
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _event_key(spec: dict) -> tuple[str, str, float]:
    return (
        spec["breakout_confirmation"],
        spec["retest_confirmation"],
        spec["retest_tolerance_atr"],
    )


def _geometry_key(spec: dict) -> tuple[str, str, float, float, float]:
    return _event_key(spec) + (spec["stop_buffer_atr"], spec["target_r"])


def event_tables(instrument: str) -> dict[tuple[str, str, float], dict]:
    """The eight detected event tables, one per (breakout, retest, tolerance)."""
    s1 = p24data.load_series(instrument)
    if s1 is None or len(s1) == 0:
        return {}
    s5, stats5 = p27bars.resample(s1, DECISION_TIMEFRAME_MINUTES)
    s15, stats15 = p27bars.resample(s1, 15)
    s30, stats30 = p27bars.resample(s1, 30)
    lv = levels.build(s5)
    part = levels.chronological_partitions(
        lv["session"].astype(np.int64), PARTITION_SHARES
    )
    session_partition = {
        int(lv["session"][i]): str(part[i]) for i in range(len(part))
    }

    out: dict[tuple[str, str, float], dict] = {}
    for spec in variants():
        key = _event_key(spec)
        if key in out:
            continue
        rows, funnel = mechanism.event_table(
            instrument, s5, s15, s30, lv, key[0], key[1], key[2]
        )
        for r in rows:
            r["partition"] = session_partition.get(r["session"], UNTOUCHED_HOLDOUT)
        out[key] = {
            "rows": rows,
            "funnel": funnel,
            "series": (s5, lv),
            "bars": {
                "five_minute": stats5,
                "fifteen_minute": stats15,
                "thirty_minute": stats30,
            },
        }
    return out


def priced_table(table: dict, instrument: str, buffer_atr: float,
                 target_r: float) -> tuple[list[dict], dict[str, int]]:
    """Apply one stop buffer and one target multiple, then resolve every trade.

    The stop cap and the "fill opened past its own stop" refusal are counted
    here rather than in the detector, because both depend on the buffer and a
    detector that knew the buffer would be eight tables pretending to be one.
    """
    s5, lv = table["series"]
    refusals = {STOP_TOO_WIDE: 0, STOP_NOT_POSITIVE: 0}
    kept: list[dict] = []
    for r in table["rows"]:
        atr = r["atr"]
        if r["side"] > 0:
            stop = r["retest_extreme"] - buffer_atr * atr
            risk = r["entry"] - stop
        else:
            stop = r["retest_extreme"] + buffer_atr * atr
            risk = stop - r["entry"]
        if not (risk > 0):
            refusals[STOP_NOT_POSITIVE] += 1
            continue
        if risk / atr > MAX_STOP_ATR:
            refusals[STOP_TOO_WIDE] += 1
            continue
        target = r["entry"] + r["side"] * target_r * risk
        target_distance = abs(target - r["entry"])
        gate_cost = r["gate_cost_points"]
        kept.append({
            **r,
            "stop_buffer_atr": buffer_atr,
            "target_r": target_r,
            "stop": float(stop),
            "risk_points": float(risk),
            "stop_atr_needed": float(risk / atr),
            "target": float(target),
            "target_distance": float(target_distance),
            "cost_multiple": float(
                target_distance / gate_cost if gate_cost > 0 else float("inf")
            ),
        })

    if not kept:
        return [], refusals

    fill_i = np.array([r["fill_index"] for r in kept], dtype=np.int64)
    side = np.array([r["side"] for r in kept], dtype=np.int64)
    entry = np.array([r["entry"] for r in kept], dtype=np.float64)
    stop_a = np.array([r["stop"] for r in kept], dtype=np.float64)
    target_a = np.array([r["target"] for r in kept], dtype=np.float64)
    last = np.array([r["session_last_bar"] for r in kept], dtype=np.int64)
    out = execute.resolve(
        instrument, s5.high, s5.low, s5.open,
        fill_i, side, entry, stop_a, target_a, last,
    )
    for k, r in enumerate(kept):
        for key, arr in out.items():
            r[key] = arr[k].item() if hasattr(arr[k], "item") else arr[k]
        year, quarter = _year_quarter(r["entry_ts"])
        r["year"] = year
        r["quarter"] = quarter
    return [r for r in kept if r["resolved"]], refusals


def _stress(rows: list[dict], multiple: float) -> list[dict]:
    """The same resolved paths with the round trip charged ``multiple`` times.

    Cost moves no stop, no target and no clock, so the path is unchanged and
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
    quarters: dict[str, dict],
    stress: dict[str, dict],
    overall: dict,
    *,
    fdr_pass: bool,
    had_candidates: bool,
) -> tuple[str, list[str]]:
    """The promotion bar, refusing on the strongest ground first.

    Returns the final status and every reason it is not ``ROBUST_CANDIDATE``,
    because a single label loses the argument.
    """
    reasons: list[str] = []
    disc = per_partition[DISCOVERY]
    val = per_partition[VALIDATION]
    hold = per_partition[UNTOUCHED_HOLDOUT]

    if not overall.get("measured"):
        return (COST_BLOCKED if had_candidates else REJECTED), [
            "the gate admitted no event" if had_candidates
            else "the mechanism produced no event"
        ]

    if disc["trade_count"] < MIN_TRADES[DISCOVERY]:
        reasons.append(
            f"discovery has {disc['trade_count']} trades against a declared "
            f"floor of {MIN_TRADES[DISCOVERY]}"
        )
        positive = (disc.get("net_expectancy_r") or 0.0) > 0
        return (PROMISING_NEEDS_DATA if positive else REJECTED), reasons

    if disc["session_count"] < MIN_SESSIONS_DISCOVERY:
        reasons.append(
            f"discovery spans {disc['session_count']} sessions against a "
            f"declared floor of {MIN_SESSIONS_DISCOVERY}"
        )
        return PROMISING_NEEDS_DATA, reasons

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
            "over the whole registered grid"
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
    if total > 0 and quarters:
        worst_q = max(v["net_total_r"] / total for v in quarters.values())
        if worst_q > MAX_QUARTER_SHARE:
            reasons.append(
                f"one quarter carries {100.0 * worst_q:.0f}% of the net "
                f"result, above the declared {100.0 * MAX_QUARTER_SHARE:.0f}%"
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


def _rule_text(spec: dict) -> str:
    return (
        f"the first 30 minutes set the opening range; a completed "
        f"{spec['breakout_confirmation']} closes outside it; within 60 minutes "
        f"price returns to the level within {spec['retest_tolerance_atr']:.2f} "
        f"ATR and a completed {spec['retest_confirmation']} then closes back "
        f"beyond it; enter the next 5-minute bar in the breakout's direction, "
        f"stop beyond the retest extreme by {spec['stop_buffer_atr']:.2f} ATR "
        f"and never wider than {MAX_STOP_ATR:.2f} ATR, target "
        f"{spec['target_r']:.1f}R, only if that target is at least "
        f"{spec['cost_gate']:.0f} round trips away, flat within 180 minutes"
    )


def _entry_text(spec: dict) -> str:
    return (
        f"{spec['breakout_confirmation']} outside the 30-minute opening range, "
        f"retest within {spec['retest_tolerance_atr']:.2f} ATR inside 60 "
        f"minutes, {spec['retest_confirmation']} back beyond the level, fill at "
        f"the next 5-minute open"
    )


def _exit_text(spec: dict) -> str:
    return (
        f"stop {spec['stop_buffer_atr']:.2f} ATR beyond the retest extreme, "
        f"target {spec['target_r']:.1f}R, flat after 180 minutes or at the "
        f"session close, same-bar tie taken as the stop"
    )


def run_instrument(instrument: str) -> dict:
    """The whole study for one instrument: grid, families, grades, funnels."""
    eligibility = stats.eligibility(instrument)
    if not eligibility.get("eligible"):
        return {
            "instrument": instrument,
            "eligible": False,
            "eligibility": eligibility,
            "rows": [],
            "families": [],
            "funnels": {},
            "totals": {
                "TOTAL_PARAMETERIZATIONS": 0, "UNIQUE_EVENT_FAMILIES": 0,
                "DISCOVERY_LEADS": 0, "VALIDATION_POSITIVE": 0,
                "HOLDOUT_POSITIVE": 0, "ROBUST_CANDIDATES": 0,
            },
        }

    tables = event_tables(instrument)
    priced: dict[tuple, tuple[list[dict], dict[str, int]]] = {}
    for spec in variants():
        gkey = _geometry_key(spec)
        if gkey in priced:
            continue
        priced[gkey] = priced_table(
            tables[_event_key(spec)], instrument,
            spec["stop_buffer_atr"], spec["target_r"],
        )

    rows_out: list[dict] = []
    for spec in variants():
        resolved, geometry_refusals = priced[_geometry_key(spec)]
        selected = [r for r in resolved if r["cost_multiple"] >= spec["cost_gate"]]
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
            "mechanism_family": MECHANISM,
            "human_readable_rule": _rule_text(spec),
            "entry_rule": _entry_text(spec),
            "exit_rule": _exit_text(spec),
            "event_family": _family_hash(instrument, selected),
            "trades": len(selected),
            "geometry_refusals": geometry_refusals,
            "cost_gate_refused": len(resolved) - len(selected),
            "overall": overall,
            "per_partition": per_partition,
            "per_year": years,
            "per_quarter": quarters,
            "cost_stress": stress,
            "session_distribution": metrics.session_distribution(selected),
            "effect_reading": metrics.effect_reading(overall, years, quarters),
            "direction_split": metrics.per_direction(selected),
            "had_candidates": bool(resolved),
        })

    # Collapse to families before anything is corrected or ranked.
    families: dict[str, list[dict]] = {}
    for row in rows_out:
        families.setdefault(row["event_family"], []).append(row)
    family_count = len(families)

    # One p-value per distinct hypothesis — (event family, target multiple) —
    # corrected against the whole registered grid.
    hypotheses: dict[tuple[str, float], dict] = {}
    for row in rows_out:
        hypotheses.setdefault(
            (row["event_family"], row["target_r"]), row
        )
    keys = list(hypotheses)
    p_values = [
        float(hypotheses[k]["per_partition"][DISCOVERY].get("p_value_one_sided")
              or 1.0)
        for k in keys
    ]
    passes = metrics.benjamini_hochberg(
        p_values, tests=len(rows_out), alpha=FDR_ALPHA
    )
    fdr_by_hypothesis = {k: bool(ok) for k, ok in zip(keys, passes)}
    fdr_by_family = {
        fam: any(
            fdr_by_hypothesis.get((fam, r["target_r"]), False) for r in rs
        )
        for fam, rs in families.items()
    }

    for row in rows_out:
        row["fdr_pass"] = fdr_by_hypothesis.get(
            (row["event_family"], row["target_r"]), False
        )
        status, reasons = _grade(
            row["per_partition"], row["per_year"], row["per_quarter"],
            row["cost_stress"], row["overall"],
            fdr_pass=row["fdr_pass"], had_candidates=row["had_candidates"],
        )
        row["final_status"] = status
        row["status_reasons"] = reasons
        row["overfit_status"] = (
            "SURVIVES_CORRECTION_OVER_THE_WHOLE_REGISTERED_GRID" if row["fdr_pass"]
            else "INDISTINGUISHABLE_FROM_THE_GRID_IT_WAS_SELECTED_FROM"
        )

    def _positive(row: dict, partition: str) -> bool:
        cell = row["per_partition"][partition]
        return (
            (cell.get("net_expectancy_r") or 0.0) > 0
            and cell["trade_count"] >= MIN_TRADES[partition]
        )

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
            for fam, rs in sorted(
                families.items(), key=lambda kv: -kv[1][0]["trades"]
            )
        ],
        "funnels": {
            f"{k[0]}|{k[1]}|TOL{k[2]:.2f}": t["funnel"] for k, t in tables.items()
        },
        "bars": next(iter(tables.values()))["bars"] if tables else {},
        "partition_sessions": _partition_sessions(tables),
        "direction_counts": _direction_counts(tables),
        "distinct_hypotheses": len(keys),
        "totals": {
            "TOTAL_PARAMETERIZATIONS": len(rows_out),
            "UNIQUE_EVENT_FAMILIES": family_count,
            "DISCOVERY_LEADS": sum(1 for r in rows_out if _positive(r, DISCOVERY)),
            "VALIDATION_POSITIVE": sum(
                1 for r in rows_out if _positive(r, VALIDATION)
            ),
            "HOLDOUT_POSITIVE": sum(
                1 for r in rows_out if _positive(r, UNTOUCHED_HOLDOUT)
            ),
            "ROBUST_CANDIDATES": sum(
                1 for r in rows_out if r["final_status"] == ROBUST_CANDIDATE
            ),
        },
    }


def _partition_sessions(tables: dict) -> dict[str, int]:
    """How many sessions produced a detected event in each partition."""
    out: dict[str, int] = {}
    for key, table in tables.items():
        label = f"{key[0]}|{key[1]}|TOL{key[2]:.2f}"
        for p in PARTITIONS:
            out[f"{label}:{p}"] = len({
                r["session"] for r in table["rows"] if r.get("partition") == p
            })
    return out


def _direction_counts(tables: dict) -> dict[str, dict[str, int]]:
    """Detected long and short events per event table, before any pricing."""
    out: dict[str, dict[str, int]] = {}
    for key, table in tables.items():
        label = f"{key[0]}|{key[1]}|TOL{key[2]:.2f}"
        out[label] = {
            LONG_BREAKOUT: sum(
                1 for r in table["rows"] if r["direction"] == LONG_BREAKOUT
            ),
            SHORT_BREAKOUT: sum(
                1 for r in table["rows"] if r["direction"] == SHORT_BREAKOUT
            ),
        }
    return out


def run(instruments: tuple[str, ...]) -> dict:
    """The study over several instruments, with the six totals summed."""
    per = [run_instrument(name) for name in instruments]
    totals = {
        k: sum(p["totals"][k] for p in per)
        for k in (
            "TOTAL_PARAMETERIZATIONS", "UNIQUE_EVENT_FAMILIES",
            "DISCOVERY_LEADS", "VALIDATION_POSITIVE", "HOLDOUT_POSITIVE",
            "ROBUST_CANDIDATES",
        )
    }
    return {
        "fingerprint": fingerprint(),
        "instruments": per,
        "totals": totals,
    }
