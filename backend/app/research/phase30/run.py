"""Phase 30 — the study runner: everything §18-§22 needs, computed once.

Order matters and is enforced here: coverage is declared, the pool and its
non-pattern control are built, discovery runs on development only, the candidates
are frozen, and only then are validation, the untouched holdout, the walk-forward
folds, the cost stress and the outlier tests read. The averaging arms, the entry
quality bands, the confirmation comparison and the context marginals are measured
on the frozen cohorts rather than searched.
"""
from __future__ import annotations

import time

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase30 import (
    NO_AVERAGING_EDGE_FOUND,
    NO_CANDLE_PATTERN_EDGE_FOUND,
    REJECTED,
    RESEARCH_LEAD,
    VALIDATED,
    VERSION,
    averaging,
    context,
    options,
    patterns,
    pool,
    study,
)

# The stop band the descriptive tables are reported at. It is not "the best" band —
# it is the middle of the frozen grid, chosen before any result, so a reader is not
# shown the flattering slice.
REFERENCE_STOP = 1.0

# Only the strongest development candidates are carried through the full
# validation/holdout/stress evaluation, because each one re-resolves the pool
# several times. The *count* of every hypothesis evaluated is still carried into
# the correction, so shortlisting cannot flatter the statistics.
MAX_FULL_EVALUATIONS = 150

# When no development candidate clears the gates, this many of the best
# development rows are still evaluated and graded, so the §20 status table shows
# what was rejected and on which requirement.
FALLBACK_EVALUATIONS = 30


def _instruments() -> list[str]:
    return [i for i in p24data.BACKTEST_FILES if p24data.load_series(i) is not None]


def coverage() -> dict:
    """§1 — what the data supports, and which §19 questions it can answer."""
    cov = p24data.coverage()
    try:
        book_cov = p24data.option_book_coverage()
    except Exception:
        book_cov = {"unmeasured": True, "note": "option book store unreadable"}
    return {
        "underlying_five_year": cov,
        "option_books": book_cov,
        "answerable": {
            "candle_patterns_on_underlying_futures": [r["instrument"] for r in cov["usable"]],
            "options_ce_pe": (
                "captured window only; UNMEASURED over five years"
            ),
            "equities": "INSUFFICIENT_HISTORY — no five-year 1-minute series on disk",
        },
    }


def _quality_table(arm: study.Arm) -> list[dict]:
    """§6/§19 — outcomes per entry-quality label, measured rather than assumed."""
    p = arm.p
    base_t1, _ = study.control_rate(p, np.ones(len(p), dtype=bool))
    rows = []
    any_pattern = np.zeros(len(p), dtype=bool)
    for name in patterns.NAMES:
        any_pattern |= p.pat[name]
    for label in context.QUALITY_LABELS:
        m = any_pattern & (p.quality == label) & p.out.resolved
        s = study._summary(arm, m, base_t1)
        if not s.get("trades"):
            continue
        rows.append({"entry_quality": label, **{
            k: s[k] for k in (
                "trades", "t1_before_sl_pct", "avg_net_r", "profit_factor",
                "max_drawdown_r", "avg_mfe_r", "avg_mae_r",
            )
        }})
    return rows


def _context_marginals(arm: study.Arm) -> list[dict]:
    """§19 — does each context dimension change the pattern pool's outcome?

    Measured across all pattern rows at the reference band: the condition on
    versus the condition off, same geometry, same costs. This is descriptive and
    is not a search — no candidate is selected from this table.
    """
    p = arm.p
    base_t1, _ = study.control_rate(p, np.ones(len(p), dtype=bool))
    any_pattern = np.zeros(len(p), dtype=bool)
    for name in patterns.NAMES:
        any_pattern |= p.pat[name]
    rows = []
    for cname, cmask in arm.masks.items():
        on = study._summary(arm, any_pattern & cmask & p.out.resolved, base_t1)
        off = study._summary(arm, any_pattern & ~cmask & p.out.resolved, base_t1)
        if not on.get("trades") or not off.get("trades"):
            continue
        rows.append({
            "condition": cname,
            "trades_on": on["trades"],
            "t1_on_pct": on["t1_before_sl_pct"],
            "net_r_on": on["avg_net_r"],
            "trades_off": off["trades"],
            "t1_off_pct": off["t1_before_sl_pct"],
            "net_r_off": off["avg_net_r"],
            "t1_delta_pct": round(on["t1_before_sl_pct"] - off["t1_before_sl_pct"], 2),
            "net_r_delta": round(on["avg_net_r"] - off["avg_net_r"], 4),
        })
    rows.sort(key=lambda r: -r["net_r_delta"])
    return rows


def _target_grid(prep: pool.Prepared) -> list[dict]:
    """§8 — the existing target geometry tested separately from the stop grid."""
    rows = []
    for t1_r in study.TARGET_GRID:
        p = pool.build(prep, stop_atr=REFERENCE_STOP, t1_r=t1_r)
        if p is None:
            continue
        any_pattern = np.zeros(len(p), dtype=bool)
        for name in patterns.NAMES:
            any_pattern |= p.pat[name]
        m = any_pattern & p.out.resolved
        o = p.out
        if not m.any():
            continue
        risk = o.risk[m]
        rows.append({
            "t1_r": t1_r,
            "trades": int(m.sum()),
            "t1_before_sl_pct": round(100.0 * float(o.t1_before_sl[m].mean()), 2),
            "avg_net_r": round(float(o.net_r[m].mean()), 4),
            "median_cost_over_risk": round(float(np.median(o.cost_points[m] / risk)), 4),
            "pays_its_cost": bool(
                float(np.median(o.cost_points[m] / risk)) < t1_r / 3.0
            ),
        })
    return rows


def _cost_wall(arms: dict[tuple[float, bool], study.Arm]) -> list[dict]:
    """§8/§11 — does the geometry have room to pay its own execution cost?

    One row per stop band over the whole pattern pool: median stop distance,
    modelled round-trip cost, cost as a fraction of risk, and pre-cost versus
    post-cost R. A band whose cost is a large fraction of risk cannot be rescued
    by any pattern, and this table says so before any candidate is discussed.
    """
    rows = []
    for stop in study.STOP_GRID:
        arm = arms.get((stop, False))
        if arm is None:
            continue
        p = arm.p
        any_pattern = np.zeros(len(p), dtype=bool)
        for name in patterns.NAMES:
            any_pattern |= p.pat[name]
        m = any_pattern & p.out.resolved
        if not m.any():
            continue
        o = p.out
        risk = o.risk[m]
        cost_over_risk = float(np.median(o.cost_points[m] / risk))
        rows.append({
            "stop_atr": stop,
            "trades": int(m.sum()),
            "median_stop_points": round(float(np.median(risk)), 2),
            "median_round_trip_cost_points": round(float(np.median(o.cost_points[m])), 2),
            "cost_over_risk": round(cost_over_risk, 4),
            "avg_gross_r": round(float(np.mean(o.gross_points[m] / risk)), 4),
            "avg_net_r": round(float(o.net_r[m].mean()), 4),
            "t1_before_sl_pct": round(100.0 * float(o.t1_before_sl[m].mean()), 2),
            "gross_edge_exceeds_cost": bool(
                float(np.mean(o.gross_points[m] / risk)) > cost_over_risk
            ),
        })
    return rows


def _gross_screen(rows: list[dict]) -> dict:
    """Was there a pre-cost effect at all? Separates "no signal" from "cost wall"."""
    scored = [r for r in rows if r.get("avg_gross_r") is not None]
    if not scored:
        return {"measured": False}
    best = max(scored, key=lambda r: r["avg_gross_r"])
    positive = [r for r in scored if r["avg_gross_r"] > 0]
    return {
        "measured": True,
        "patterns_scored": len(scored),
        "patterns_with_positive_gross_r": len(positive),
        "best_pattern": best["pattern"],
        "best_avg_gross_r": best["avg_gross_r"],
        "best_pattern_cost_over_risk": best.get("cost_over_risk"),
        "any_gross_edge_larger_than_cost": bool(
            best.get("cost_over_risk") is not None
            and best["avg_gross_r"] > best["cost_over_risk"]
        ),
    }


def _averaging(arm: study.Arm, candidates: list[study.Candidate]) -> dict:
    """§9 — the four arms on the whole pattern pool and on the best cohorts."""
    p = arm.p
    any_pattern = np.zeros(len(p), dtype=bool)
    for name in patterns.NAMES:
        any_pattern |= p.pat[name]
    out = {
        "all_patterns": averaging.compare(p, any_pattern & p.out.resolved),
        "per_pattern": {},
        "note": (
            "each arm widens its own stop so its add level is reachable, so the "
            "arms do not risk the same money; expectancy per unit of total risk "
            "is the comparison that decides whether averaging helped"
        ),
    }
    for name in patterns.NAMES:
        m = p.pat[name] & p.out.resolved
        if int(m.sum()) < study.MIN_DEV_TRADES:
            continue
        rows = averaging.compare(p, m)
        out["per_pattern"][name] = rows
    helped = []
    for name, rows in list(out["per_pattern"].items()) + [
        ("ALL_PATTERNS", out["all_patterns"])
    ]:
        for arm_name, v in rows["vs_baseline"].items():
            if v.get("helped"):
                helped.append({"cohort": name, "arm": arm_name})
    out["arms_that_helped"] = helped
    out["verdict"] = (
        NO_AVERAGING_EDGE_FOUND if not helped else "AVERAGING_IMPROVED_SOME_COHORTS"
    )
    return out


def run(instruments: list[str] | None = None) -> dict:
    """Run the whole study. Returns one dict; :mod:`report` renders it."""
    t_start = time.time()
    names = [i.upper() for i in (instruments or _instruments())]
    result: dict = {
        "version": VERSION,
        "pattern_fingerprint": patterns.FINGERPRINT,
        "generated_ts": int(t_start),
        "coverage": coverage(),
        "frozen": {
            "patterns": {k: v for k, v in patterns.SIDES.items()},
            "stop_grid_atr": list(study.STOP_GRID),
            "target_grid_r": list(study.TARGET_GRID),
            "base_target_r": study.BASE_TARGET_R,
            "confirmation": (
                "next bar closes beyond the pattern bar's close in the trade's "
                "direction and in the favourable half of its own range; the "
                "confirmed arm fills one bar later"
            ),
            "averaging_arms": {k: v for k, v in averaging.ARMS.items()},
            "entry_quality_labels": list(context.QUALITY_LABELS),
            "splits": {"development": 0.6, "validation": 0.2, "holdout": 0.2},
            "walk_forward_folds": study.WALK_FORWARD_FOLDS,
            "fdr_alpha": study.FDR_ALPHA,
            "max_conditions": study.MAX_CONDITIONS,
        },
        "instruments": {},
        "hypotheses": {"stage1": 0, "stage2": 0, "total": 0},
        "candidates": [],
        "options": {},
    }

    all_candidates: list[study.Candidate] = []
    total_hypotheses = 0

    for inst in names:
        prep = pool.prepare(inst)
        if prep is None:
            result["instruments"][inst] = {"status": "INSUFFICIENT_HISTORY"}
            continue
        arms = study.build_arms(prep)
        if not arms:
            result["instruments"][inst] = {"status": "INSUFFICIENT_HISTORY"}
            continue
        ref = arms[(REFERENCE_STOP, False)]

        seeds, n1, tested = study.stage1(arms)
        expanded, n2 = study.stage2(arms, seeds)
        total_hypotheses += n1 + n2
        result["hypotheses"]["stage1"] += n1
        result["hypotheses"]["stage2"] += n2

        pool_rows = {
            f"stop{stop}_{'confirmed' if c else 'pattern_only'}": pool.summary(a.p)
            for (stop, c), a in arms.items()
        }
        shortlist = sorted(
            [{**s, "conditions": ()} for s in seeds] + expanded,
            key=lambda r: -(r["dev"].get("avg_net_r") or 0.0),
        )[:MAX_FULL_EVALUATIONS]
        # Nothing cleared the development gates. Grade the best development rows
        # anyway, so the status table names what was tried and why each row failed
        # rather than being silently empty.
        fallback = not shortlist
        if fallback:
            shortlist = sorted(
                [{**s, "conditions": ()} for s in tested],
                key=lambda r: -(r["dev"].get("avg_net_r") or -9.9),
            )[:FALLBACK_EVALUATIONS]

        evaluated = [study.evaluate(prep, arms, c) for c in shortlist]
        all_candidates.extend(evaluated)
        pvc = study.pattern_vs_control(arms, REFERENCE_STOP)

        result["instruments"][inst] = {
            "status": "STUDIED",
            "pools": pool_rows,
            "windows": {k: list(v) for k, v in ref.windows.items()},
            "walk_forward_folds": [list(f) for f in ref.folds],
            "pattern_vs_control": pvc,
            "cost_wall": _cost_wall(arms),
            "gross_edge_screen": _gross_screen(pvc),
            "entry_quality": _quality_table(ref),
            "context_marginals": _context_marginals(ref),
            "target_geometry_grid": _target_grid(prep),
            "averaging": _averaging(ref, evaluated),
            "stage1_tested": len(tested),
            "stage1_survivors": len(seeds),
            "graded_without_dev_survivor": fallback,
            "stage2_candidates": len(expanded),
            "hypotheses_counted": n1 + n2,
            "fully_evaluated": len(evaluated),
            # A hypothesis that was counted but not carried through the full
            # validation/holdout/stress evaluation holds no status at all. Saying
            # so here keeps an absent status from reading as a pass.
            "counted_but_not_fully_evaluated": max(
                0, (len(seeds) + len(expanded)) - len(evaluated)
            ),
            "shortlist_note": (
                f"the {MAX_FULL_EVALUATIONS} best development rows are evaluated "
                "end to end; every hypothesis is still counted in the "
                "multiple-testing denominator, and a row that was not evaluated "
                "carries no status rather than an implied one"
            ),
        }
        result["options"][inst] = options.measure(inst)

    result["hypotheses"]["total"] = total_hypotheses
    study.apply_fdr(all_candidates, total_hypotheses)
    for c in all_candidates:
        study.grade(c, hypotheses=total_hypotheses)
    result["candidates"] = [c.as_dict() for c in all_candidates]

    validated = [c for c in all_candidates if c.status == VALIDATED]
    leads = [c for c in all_candidates if c.status == RESEARCH_LEAD]
    result["verdict"] = {
        "validated": len(validated),
        "research_leads": len(leads),
        "rejected": sum(1 for c in all_candidates if c.status == REJECTED),
        "pattern_verdict": (
            NO_CANDLE_PATTERN_EDGE_FOUND if not validated else "CANDLE_PATTERN_VALIDATED"
        ),
        "averaging_verdict": _overall_averaging_verdict(result),
        "note": (
            "no candidate met every §20 requirement, so the study's answer is that "
            "the five-year data does not contain a candle/context effect that "
            "survives its own control, a chronological holdout and realistic cost"
            if not validated else
            "at least one candidate met every §20 requirement; it is still "
            "research-only and nothing is promoted"
        ),
    }
    result["runtime_sec"] = round(time.time() - t_start, 1)
    return result


def _overall_averaging_verdict(result: dict) -> str:
    for inst in result["instruments"].values():
        avg = inst.get("averaging") or {}
        if avg.get("arms_that_helped"):
            return "AVERAGING_IMPROVED_SOME_COHORTS"
    return NO_AVERAGING_EDGE_FOUND
