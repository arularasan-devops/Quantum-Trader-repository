"""Phase 54 — the registered grid, the sequential book, the families, the bar.

The shape of the computation, and why it is this shape:

* **four event tables.** Only ``lookback`` and ``pullback_window`` change which
  expansions exist and where their reclaims fall, so the detection runs four
  times per instrument rather than 128;
* **128 sequential walks.** Everything else is applied by walking the
  candidates in time order and holding **one position at a time**. This cannot
  be expressed as a filter over a pre-resolved table: whether a candidate is
  reachable depends on when the previous accepted trade *left*, which depends
  on the stop, the target and the hold cap. A vectorised version of this study
  would quietly trade two overlapping positions and report a book no rule could
  have run;
* **two collapses before anything is corrected.** Rows that enter the same
  instants are one **entry event set**; rows that also share a stop and a
  target are one **event family** — the finer unit, because the same entries
  with a wider stop and a 2R target are different economic events rather than a
  relabelled one. Both counts are reported;
* **the denominator stays whole.** False-discovery correction runs over all 256
  registered cells — 128 per instrument times two instruments — never over the
  distinct hypotheses and certainly never over the survivors. Correcting
  against the survivors is exactly how a mined fluke passes.

The grade refuses on the strongest ground first, so the reported reason is the
binding one rather than the first one checked.
"""
from __future__ import annotations

import datetime as dt
import hashlib

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase27 import bars as p27bars
from app.research.phase54 import (
    COST_BLOCKED,
    COST_STRESS_MULTIPLES,
    DISCOVERY,
    FDR_ALPHA,
    HISTORICAL_LEAD,
    MAX_DRAWDOWN_R,
    MAX_QUARTER_SHARE,
    MAX_TOP_TRADE_SHARE,
    MAX_YEAR_SHARE,
    MECHANISM,
    MIN_PROFIT_FACTOR,
    MIN_SESSIONS_DISCOVERY,
    MIN_TRADES_DISCOVERY,
    MIN_TRADES_FOR_PROMOTION,
    MIN_TRADES_HOLDOUT,
    MIN_TRADES_VALIDATION,
    MIN_YEARS_POSITIVE,
    OVERFIT_RISK,
    PARTITION_SHARES,
    PARTITIONS,
    POSITION_ALREADY_OPEN,
    PROMISING_NEEDS_DATA,
    REJECTED,
    RESOLUTION_TIMEFRAME_MINUTES,
    ROBUST_CANDIDATE,
    STOP_NOT_POSITIVE,
    STOP_TOO_WIDE,
    TOTAL_PARAMETERIZATIONS,
    UNTOUCHED_HOLDOUT,
    VALIDATION,
    fingerprint,
    variants,
)
from app.research.phase54 import daily as daily_mod
from app.research.phase54 import execute, mechanism, metrics, stats

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


def _entry_set_hash(instrument: str, rows: list[dict]) -> str:
    """Identity of an entry event set: instrument, then sorted instants and sides.

    The side is part of the identity because two variants that enter opposite
    ways at the same instant are emphatically not one hypothesis; the
    instrument is part of it so two names cannot merge counts by a coincidence
    of timestamps.
    """
    payload = instrument + "|" + ",".join(
        f"{r['entry_ts']}:{r['side']}"
        for r in sorted(rows, key=lambda r: (r["entry_ts"], r["side"]))
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _family_hash(instrument: str, rows: list[dict]) -> str:
    """Identity of an event family: the entries **and** the geometry on them."""
    payload = instrument + "|" + ",".join(
        f"{r['entry_ts']}:{r['side']}:{r['stop']:.4f}:{r['target']:.4f}"
        for r in sorted(rows, key=lambda r: (r["entry_ts"], r["side"]))
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _event_key(spec: dict) -> tuple[int, int]:
    return (spec["lookback"], spec["pullback_window"])


def event_tables(instrument: str) -> dict[tuple[int, int], dict]:
    """The four detected candidate tables, one per (lookback, pullback window)."""
    s1 = p24data.load_series(instrument)
    if s1 is None or len(s1) == 0:
        return {}
    s5, bar_stats = p27bars.resample(s1, RESOLUTION_TIMEFRAME_MINUTES)
    dly = daily_mod.build(s5)
    atr = daily_mod.atr_before(dly)
    part = daily_mod.chronological_partitions(dly, PARTITION_SHARES)

    out: dict[tuple[int, int], dict] = {}
    extremes: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for spec in variants():
        key = _event_key(spec)
        if key in out:
            continue
        lookback, window = key
        if lookback not in extremes:
            extremes[lookback] = daily_mod.extremes_before(dly, lookback)
        prior_high, prior_low = extremes[lookback]
        rows, funnel = mechanism.events(
            instrument, dly, prior_high, prior_low, atr,
            lookback=lookback, window=window,
        )
        for r in rows:
            r["partition"] = str(part[r["entry_ordinal"]])
        out[key] = {
            "rows": rows,
            "funnel": funnel,
            "series": s5,
            "daily": dly,
            "atr": atr,
            "bars": bar_stats,
            "partition": part,
        }
    return out


def _ordinal_of_index(dly: dict[str, np.ndarray], index: int) -> int:
    """Which daily session a five-minute bar index belongs to."""
    return int(np.searchsorted(dly["last_i"], int(index), side="left"))


def book(table: dict, instrument: str, spec: dict) -> tuple[list[dict], dict]:
    """Walk one parameterization's candidates in time, one position at a time.

    Returns the resolved trades and the refusals counted on the way. The
    refusals are per candidate inspected, so they add up to the candidate count
    of the table.
    """
    s5 = table["series"]
    dly = table["daily"]
    n_sessions = dly["close"].size
    refusals = {
        STOP_NOT_POSITIVE: 0, STOP_TOO_WIDE: 0, COST_BLOCKED: 0,
        POSITION_ALREADY_OPEN: 0,
    }
    trades: list[dict] = []
    last_exit_ordinal = -1

    for cand in table["rows"]:
        # One position, no pyramiding, and no re-entry until a **new**
        # expansion: an expansion the book had already seen while it was in a
        # trade is the same event, not a second one.
        if cand["expansion_ordinal"] <= last_exit_ordinal:
            refusals[POSITION_ALREADY_OPEN] += 1
            continue

        atr = cand["atr"]
        if cand["side"] > 0:
            stop = cand["pullback_extreme"] - spec["stop_buffer_atr"] * atr
            risk = cand["entry"] - stop
        else:
            stop = cand["pullback_extreme"] + spec["stop_buffer_atr"] * atr
            risk = stop - cand["entry"]
        if not (risk > 0):
            refusals[STOP_NOT_POSITIVE] += 1
            continue
        if risk / atr > spec["max_risk_atr"]:
            refusals[STOP_TOO_WIDE] += 1
            continue

        target = cand["entry"] + cand["side"] * spec["target_r"] * risk
        target_distance = abs(target - cand["entry"])
        gate_cost = cand["gate_cost_points"]
        cost_multiple = (
            target_distance / gate_cost if gate_cost > 0 else float("inf")
        )
        if cost_multiple < spec["cost_gate"]:
            refusals[COST_BLOCKED] += 1
            continue

        last_ordinal = min(
            cand["entry_ordinal"] + spec["max_hold_sessions"] - 1, n_sessions - 1
        )
        resolved = execute.resolve_one(
            instrument, s5.high, s5.low, s5.open, s5.close, s5.ts,
            fill_i=cand["fill_index"],
            last_allowed_i=int(dly["last_i"][last_ordinal]),
            side=cand["side"], entry=cand["entry"], stop=float(stop),
            target=float(target),
        )
        exit_ordinal = _ordinal_of_index(dly, resolved["exit_index"])
        last_exit_ordinal = exit_ordinal
        year, quarter = _year_quarter(cand["entry_ts"])
        trades.append({
            **cand, **resolved,
            "stop": float(stop),
            "target": float(target),
            "risk_atr": float(risk / atr),
            "target_distance": float(target_distance),
            "cost_multiple": float(cost_multiple),
            "exit_ordinal": exit_ordinal,
            "hold_sessions": int(exit_ordinal - cand["entry_ordinal"] + 1),
            "year": year,
            "quarter": quarter,
        })
    return [t for t in trades if t["resolved"]], refusals


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
    """The promotion bar, refusing on the strongest ground first."""
    reasons: list[str] = []
    disc = per_partition[DISCOVERY]
    val = per_partition[VALIDATION]
    hold = per_partition[UNTOUCHED_HOLDOUT]

    if not overall.get("measured"):
        return (COST_BLOCKED if had_candidates else REJECTED), [
            "every detected event was refused by the gate or the risk cap"
            if had_candidates else "the mechanism produced no event"
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

    # The hard floor: a multi-day mechanism can clear every ratio above on a
    # couple of dozen trades, and a couple of dozen trades cannot support the
    # word "robust" whatever the ratios say.
    if int(overall.get("trade_count") or 0) < MIN_TRADES_FOR_PROMOTION:
        reasons.append(
            f"{overall['trade_count']} trades in total is below the declared "
            f"promotion floor of {MIN_TRADES_FOR_PROMOTION}"
        )
        return PROMISING_NEEDS_DATA, reasons

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
        f"a completed daily close beyond the {spec['lookback']}-session "
        f"extreme; inside the next {spec['pullback_window']} completed "
        f"sessions price trades back to that level without any completed close "
        f"back inside, and a completed close beyond it reclaims; enter the next "
        f"session's open in the expansion's direction, stop "
        f"{spec['stop_buffer_atr']:.2f} ATR beyond the pullback extreme and "
        f"never wider than {spec['max_risk_atr']:.1f} ATR20, target "
        f"{spec['target_r']:.1f}R, only if that target is at least "
        f"{spec['cost_gate']:.0f} round trips away, flat after "
        f"{spec['max_hold_sessions']} sessions, one position at a time"
    )


def _entry_text(spec: dict) -> str:
    return (
        f"expansion close beyond the {spec['lookback']}-session extreme, "
        f"pullback to the level and reclaim inside "
        f"{spec['pullback_window']} completed sessions, fill at the next "
        f"session's open"
    )


def _exit_text(spec: dict) -> str:
    return (
        f"stop {spec['stop_buffer_atr']:.2f} ATR beyond the pullback extreme, "
        f"target {spec['target_r']:.1f}R, flat at the open after "
        f"{spec['max_hold_sessions']} completed sessions, same-bar tie taken "
        f"as the stop"
    )


def run_instrument(instrument: str, *, denominator: int) -> dict:
    """The whole study for one instrument, corrected against ``denominator``."""
    eligibility = stats.eligibility(instrument)
    empty_totals = {
        "TOTAL_PARAMETERIZATIONS": 0, "UNIQUE_ENTRY_EVENT_SETS": 0,
        "UNIQUE_EVENT_FAMILIES": 0, "DISCOVERY_LEADS": 0,
        "VALIDATION_POSITIVE": 0, "HOLDOUT_POSITIVE": 0,
        "ROBUST_CANDIDATES": 0,
    }
    if not eligibility.get("eligible"):
        return {
            "instrument": instrument, "eligible": False,
            "eligibility": eligibility, "rows": [], "families": [],
            "funnels": {}, "totals": dict(empty_totals),
        }

    tables = event_tables(instrument)
    rows_out: list[dict] = []
    for spec in variants():
        table = tables[_event_key(spec)]
        trades, refusals = book(table, instrument, spec)
        overall = metrics.describe(trades)
        years = metrics.per_period(trades, "year")
        quarters = metrics.per_period(trades, "quarter")
        per_partition = {
            p: metrics.describe(_partition_rows(trades, p)) for p in PARTITIONS
        }
        stress = {
            f"{m}": metrics.describe(_stress(trades, m))
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
            "entry_event_set": _entry_set_hash(instrument, trades),
            "event_family": _family_hash(instrument, trades),
            "trades": len(trades),
            "refusals": refusals,
            "overall": overall,
            "per_partition": per_partition,
            "per_year": years,
            "per_quarter": quarters,
            "cost_stress": stress,
            "direction_split": metrics.per_direction(trades),
            "had_candidates": bool(table["rows"]),
        })

    # The holding comparison: the same cell at both caps, paired.
    by_hold: dict[tuple, dict[int, dict]] = {}
    for row in rows_out:
        key = (row["lookback"], row["pullback_window"], row["stop_buffer_atr"],
               row["max_risk_atr"], row["target_r"], row["cost_gate"])
        by_hold.setdefault(key, {})[row["max_hold_sessions"]] = row
    for key, pair in by_hold.items():
        short_row = pair.get(min(pair))
        long_row = pair.get(max(pair))
        delta = metrics.holding_delta(
            short_row["overall"], long_row["overall"]
        ) if short_row is not None and long_row is not None else {"measured": False}
        for row in pair.values():
            row["holding_delta"] = delta

    for row in rows_out:
        row["effect_reading"] = metrics.effect_reading(
            row["overall"], row["per_year"], row["per_quarter"],
            row["holding_delta"],
        )
        row["effect_source"] = metrics.effect_source(
            row["overall"], row["holding_delta"]
        )

    entry_sets: dict[str, list[dict]] = {}
    families: dict[str, list[dict]] = {}
    for row in rows_out:
        entry_sets.setdefault(row["entry_event_set"], []).append(row)
        families.setdefault(row["event_family"], []).append(row)

    # One p-value per event family, corrected over the whole registered grid.
    keys = list(families)
    p_values = [
        float(families[k][0]["per_partition"][DISCOVERY].get("p_value_one_sided")
              or 1.0)
        for k in keys
    ]
    passes = metrics.benjamini_hochberg(
        p_values, tests=int(denominator), alpha=FDR_ALPHA
    )
    fdr_by_family = {k: bool(ok) for k, ok in zip(keys, passes)}

    for row in rows_out:
        row["fdr_pass"] = fdr_by_family.get(row["event_family"], False)
        status, reasons = _grade(
            row["per_partition"], row["per_year"], row["per_quarter"],
            row["cost_stress"], row["overall"],
            fdr_pass=row["fdr_pass"], had_candidates=row["had_candidates"],
        )
        row["final_status"] = status
        row["status_reasons"] = reasons
        row["overfit_status"] = (
            "SURVIVES_CORRECTION_OVER_THE_WHOLE_REGISTERED_GRID"
            if row["fdr_pass"]
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
        "entry_sets": [
            {
                "entry_event_set": key,
                "variants": [r["variant_id"] for r in rs],
                "trades": rs[0]["trades"],
            }
            for key, rs in sorted(
                entry_sets.items(), key=lambda kv: -kv[1][0]["trades"]
            )
        ],
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
            f"LB{k[0]}_PB{k[1]}": t["funnel"] for k, t in tables.items()
        },
        "bars": next(iter(tables.values()))["bars"] if tables else {},
        "daily_sessions": int(
            next(iter(tables.values()))["daily"]["close"].size if tables else 0
        ),
        "partition_sessions": _partition_sessions(tables),
        "direction_counts": _direction_counts(tables),
        "totals": {
            "TOTAL_PARAMETERIZATIONS": len(rows_out),
            "UNIQUE_ENTRY_EVENT_SETS": len(entry_sets),
            "UNIQUE_EVENT_FAMILIES": len(families),
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
    """How many sessions each partition holds, and how many carried an event."""
    out: dict[str, int] = {}
    for key, table in tables.items():
        label = f"LB{key[0]}_PB{key[1]}"
        part = table["partition"]
        for p in PARTITIONS:
            out[f"{label}:{p}:sessions"] = int(sum(1 for v in part if v == p))
            out[f"{label}:{p}:events"] = len({
                r["entry_ordinal"] for r in table["rows"]
                if r.get("partition") == p
            })
    return out


def _direction_counts(tables: dict) -> dict[str, dict[str, int]]:
    """Detected long and short candidates per table, before any pricing."""
    from app.research.phase54 import LONG_CONTINUATION, SHORT_CONTINUATION

    out: dict[str, dict[str, int]] = {}
    for key, table in tables.items():
        label = f"LB{key[0]}_PB{key[1]}"
        out[label] = {
            LONG_CONTINUATION: sum(
                1 for r in table["rows"] if r["direction"] == LONG_CONTINUATION
            ),
            SHORT_CONTINUATION: sum(
                1 for r in table["rows"] if r["direction"] == SHORT_CONTINUATION
            ),
            "STRICT_RECLAIM_DIAGNOSTIC": sum(
                1 for r in table["rows"] if r["strict_reclaim_available"]
            ),
        }
    return out


def run(instruments: tuple[str, ...]) -> dict:
    """The study over several instruments, corrected over the whole grid.

    The denominator is the registered grid times the number of instruments
    searched, because searching a second name is a second set of chances to
    find the same fluke.
    """
    denominator = TOTAL_PARAMETERIZATIONS * max(1, len(instruments))
    per = [run_instrument(name, denominator=denominator) for name in instruments]
    totals = {
        k: sum(p["totals"][k] for p in per)
        for k in (
            "TOTAL_PARAMETERIZATIONS", "UNIQUE_ENTRY_EVENT_SETS",
            "UNIQUE_EVENT_FAMILIES", "DISCOVERY_LEADS", "VALIDATION_POSITIVE",
            "HOLDOUT_POSITIVE", "ROBUST_CANDIDATES",
        )
    }
    return {
        "fingerprint": fingerprint(),
        "fdr_denominator": denominator,
        "instruments": per,
        "totals": totals,
    }
