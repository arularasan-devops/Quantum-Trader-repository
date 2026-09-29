"""Phase 36 §25/§26 — the 14-section report and the single verdict.

The verdict is computed from the same dict the report prints, and every gate it
applies is listed in the payload with the value that passed or failed it. That is
the only way a reader can disagree with the conclusion on the evidence rather
than on trust.

§26 allows exactly three answers and none of them is VALIDATED. The gates are
ordered so the most common honest outcome — not enough independent sessions —
cannot be skipped past by a large single-session sample. Ten thousand triples
from one afternoon are ten thousand correlated observations of one afternoon.
"""
from __future__ import annotations

from app.research.phase35 import CE, FUTURES, PE
from app.research.phase36 import (
    DEFAULT_INSTRUMENT,
    LEAD,
    MIN_SESSIONS_FOR_LEAD,
    MIN_TRIPLES_FOR_LEAD,
    NEEDS_DATA,
    NO_ADVANTAGE,
    PAPER_ONLY,
    RESEARCH_ONLY,
)


def _best(ranking: list[dict]) -> dict | None:
    for row in ranking:
        if row.get("eligible") and row.get("net_mean_pct") is not None:
            return row
    return None


def verdict(state: dict) -> dict:
    """§26 — one of three, with every gate's value shown.

    Order matters: sample floors first, then whether anything is positive at all,
    then whether the positive thing survives the placebo, the holdout, the stress
    grid and outlier removal. A gate that cannot be evaluated counts as not
    passed, never as passed-by-default.
    """
    comparison = state.get("vehicle_comparison") or {}
    ranking = comparison.get("ranking") or []
    best = _best(ranking)
    table = comparison.get("table") or {}
    sessions = max(
        [int(row.get("sessions") or 0) for row in table.values()] or [0]
    )
    entered = max(
        [int(row.get("n_entered") or 0) for row in table.values()] or [0]
    )
    chron = state.get("chronological") or {}
    wf = state.get("walk_forward") or {}
    stress = (state.get("stress") or {}).get("by_vehicle") or {}
    outl = (state.get("outliers") or {}).get("by_vehicle") or {}
    plac = (state.get("placebo") or {}).get("by_vehicle") or {}

    vehicle = best["vehicle"] if best else None
    net = best["net_mean_pct"] if best else None
    placebo_net = (plac.get(vehicle) or {}).get("net_mean_pct") if vehicle else None

    gates = [
        {
            "gate": "TRIPLES_FLOOR",
            "need": MIN_TRIPLES_FOR_LEAD, "have": entered,
            "passed": entered >= MIN_TRIPLES_FOR_LEAD,
        },
        {
            "gate": "SESSIONS_FLOOR",
            "need": MIN_SESSIONS_FOR_LEAD, "have": sessions,
            "passed": sessions >= MIN_SESSIONS_FOR_LEAD,
        },
        {
            "gate": "A_VEHICLE_IS_POSITIVE_NET",
            "need": "> 0", "have": net,
            "passed": isinstance(net, (int, float)) and net > 0,
        },
        {
            "gate": "BEATS_RANDOM_ENTRY_PLACEBO",
            "need": "signal net > placebo net", "have": placebo_net,
            "passed": (
                isinstance(net, (int, float)) and isinstance(
                    placebo_net, (int, float)
                ) and net > placebo_net
            ),
        },
        {
            "gate": "UNTOUCHED_HOLDOUT_CONFIRMS",
            "need": "HOLDOUT_CONFIRMS", "have": chron.get("status"),
            "passed": chron.get("status") == "HOLDOUT_CONFIRMS",
        },
        {
            "gate": "WALK_FORWARD_MAJORITY_POSITIVE",
            "need": "> 50%", "have": wf.get("positive_folds_pct"),
            "passed": (wf.get("positive_folds_pct") or 0) > 50.0,
        },
        {
            "gate": "SURVIVES_COST_AND_SLIPPAGE_STRESS",
            "need": "positive in every cell",
            "have": (stress.get(vehicle) or {}).get("positive_everywhere"),
            "passed": bool((stress.get(vehicle) or {}).get("positive_everywhere")),
        },
        {
            "gate": "NOT_CARRIED_BY_OUTLIERS",
            "need": "positive with top 5% removed",
            "have": (outl.get(vehicle) or {}).get("carried_by_outliers"),
            "passed": not (outl.get(vehicle) or {}).get("carried_by_outliers", True),
        },
    ]
    floors_passed = all(
        g["passed"] for g in gates
        if g["gate"] in ("TRIPLES_FLOOR", "SESSIONS_FLOOR")
    )
    positive = any(
        g["passed"] for g in gates if g["gate"] == "A_VEHICLE_IS_POSITIVE_NET"
    )
    all_passed = all(g["passed"] for g in gates)

    if not floors_passed:
        label = NEEDS_DATA
    elif all_passed:
        label = LEAD
    elif not positive:
        label = NO_ADVANTAGE
    else:
        # Positive but something downstream did not confirm. That is a data
        # question, not a negative finding: with a confirmed-negative you stop,
        # with an unconfirmed positive you keep capturing.
        label = NEEDS_DATA
    return {
        "verdict": label,
        "leading_vehicle": vehicle,
        "leading_net_mean_pct": net,
        "gates": gates,
        "research_only": RESEARCH_ONLY,
        "paper_only": PAPER_ONLY,
        "production_changed": False,
        "note": (
            "VALIDATED is not a value this study can emit; a frozen vehicle rule "
            "can earn it only from independent paper evidence later"
        ),
    }


def sections(state: dict) -> list[dict]:
    """§25 — the fourteen required sections, each with its own sample size."""
    cov = state.get("coverage") or {}
    comparison = (state.get("vehicle_comparison") or {}).get("table") or {}

    def n_of(vehicle: str) -> int:
        return int((comparison.get(vehicle) or {}).get("n_entered") or 0)

    return [
        {"section": "1. DATA QUALITY", "n": cov.get("eligible"),
         "detail": {
             "observations": cov.get("observations"),
             "eligible_triples": cov.get("eligible"),
             "eligible_pct": cov.get("eligible_pct"),
             "refused_by_reason": cov.get("refused_by_reason"),
         }},
        {"section": "2. VEHICLE COMPARISON",
         "n": n_of(FUTURES) + n_of(CE) + n_of(PE),
         "detail": (state.get("vehicle_comparison") or {}).get("ranking")},
        {"section": "3. BEST VEHICLE BY DIRECTION",
         "n": sum(
             int((v or {}).get("n_triples") or 0)
             for v in (state.get("by_direction") or {}).values()
         ),
         "detail": _direction_digest(state.get("by_direction") or {})},
        {"section": "4. BEST VEHICLE BY PREMIUM BAND",
         "n": len(state.get("by_premium_band") or {}),
         "detail": _cut_digest(state.get("by_premium_band") or {})},
        {"section": "5. BEST VEHICLE BY DTE",
         "n": len(state.get("by_dte") or {}),
         "detail": _cut_digest(state.get("by_dte") or {})},
        {"section": "6. BEST VEHICLE BY STRIKE DISTANCE",
         "n": len(state.get("by_moneyness") or {}),
         "detail": _cut_digest(state.get("by_moneyness") or {})},
        {"section": "7. BEST VEHICLE BY TIME OF DAY",
         "n": len(state.get("by_time_of_day") or {}),
         "detail": _cut_digest(state.get("by_time_of_day") or {})},
        {"section": "8. BEST HOLD PERIOD", "n": n_of(FUTURES),
         "detail": {
             v: {
                 "best_hold": (comparison.get(v) or {}).get("best_hold"),
                 "net_mean_pct": (
                     comparison.get(v) or {}
                 ).get("best_hold_net_mean_pct"),
             } for v in (FUTURES, CE, PE)
         }},
        {"section": "9. GIVEBACK",
         "n": sum(
             int((v or {}).get("n") or 0)
             for v in (state.get("giveback") or {}).values()
         ),
         "detail": state.get("giveback")},
        {"section": "10. ENGINE MISSED / WRONG VEHICLE",
         "n": (state.get("engine") or {}).get("wrong_vehicle_n"),
         "detail": {
             "by_side": (state.get("engine") or {}).get("by_side"),
             "missed": (state.get("engine") or {}).get(
                 "engine_missed_best_available"),
             "selected": (state.get("engine") or {}).get(
                 "engine_selected_best_available"),
         }},
        {"section": "11. GROSS-TO-NET COST DECOMPOSITION",
         "n": n_of(FUTURES) + n_of(CE) + n_of(PE),
         "detail": state.get("cost_decomposition")},
        {"section": "12. OUTLIER ANALYSIS", "n": n_of(FUTURES),
         "detail": (state.get("outliers") or {}).get("by_vehicle")},
        {"section": "13. COST / SLIPPAGE STRESS", "n": n_of(FUTURES),
         "detail": {
             v: (
                 (state.get("stress") or {}).get("by_vehicle") or {}
             ).get(v, {}).get("positive_everywhere")
             for v in (FUTURES, CE, PE)
         }},
        {"section": "14. CHRONOLOGICAL HOLDOUT",
         "n": ((state.get("chronological") or {}).get(
             "UNTOUCHED_HOLDOUT") or {}).get("n"),
         "detail": {
             "chosen_vehicle": (state.get("chronological") or {}).get(
                 "chosen_vehicle"),
             "status": (state.get("chronological") or {}).get("status"),
             "walk_forward_hit_rate_pct": (
                 state.get("walk_forward") or {}
             ).get("hit_rate_pct"),
         }},
    ]


def _direction_digest(by_direction: dict) -> dict:
    out: dict[str, dict] = {}
    for view, body in by_direction.items():
        side = body.get("option_side")
        out[view] = {
            "n_triples": body.get("n_triples"),
            "futures_net_mean_pct": (
                body.get(FUTURES) or {}
            ).get("best_hold_net_mean_pct"),
            f"{side}_net_mean_pct": (
                body.get(side) or {}
            ).get("best_hold_net_mean_pct"),
        }
    return out


def _cut_digest(cut: dict) -> dict:
    """The winning vehicle per bucket, or why the bucket has no winner."""
    out: dict[str, dict] = {}
    for label, body in cut.items():
        per = body.get("by_vehicle") or {}
        eligible = {
            v: s.get("net_mean_pct") for v, s in per.items()
            if s.get("net_mean_pct") is not None and s.get("sufficient")
        }
        out[label] = {
            "n": body.get("n"),
            "spread_pct_mean": body.get("spread_pct_mean"),
            "cost_pct_mean": body.get("cost_pct_mean"),
            "best_vehicle": (
                max(eligible, key=lambda v: eligible[v]) if eligible else None
            ),
            "best_net_mean_pct": max(eligible.values()) if eligible else None,
            "status": None if eligible else "REQUIRES_MORE_DATA",
        }
    return out


def headline(state: dict) -> str:
    v = state.get("verdict") or {}
    cov = state.get("coverage") or {}
    failed = [g["gate"] for g in v.get("gates", []) if not g["passed"]]
    lines = [
        # The title names the instrument that was measured. The framework is
        # instrument-agnostic, and a NIFTY run printing "CRUDEOIL" is exactly
        # how a report gets quoted about the wrong market a month later.
        f"{state.get('instrument', DEFAULT_INSTRUMENT)} VEHICLE REPORT",
        f"  verdict            : {v.get('verdict')}",
        f"  eligible triples   : {cov.get('eligible')} of "
        f"{cov.get('observations')} observations "
        f"({cov.get('eligible_pct')}%)",
        f"  leading vehicle    : {v.get('leading_vehicle')} "
        f"net {v.get('leading_net_mean_pct')}%/leg",
        f"  gates not passed   : {', '.join(failed) if failed else 'none'}",
        f"  {RESEARCH_ONLY} / {PAPER_ONLY} — no order path, no production change",
    ]
    return "\n".join(lines)
