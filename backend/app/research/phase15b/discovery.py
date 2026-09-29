"""Phase 15B — discover selective market setups on the 5-year candidate pool.

RESEARCH ONLY, UNDERLYING_ONLY. Nothing here places an order, changes the engine
or promotes anything.

The pipeline exists to make one specific failure impossible: searching a large
pool across many dimensions until something looks good, then quoting it. So:

    DEVELOPMENT   every bucket of every dimension is measured, hypotheses are
                  counted, and a bucket only becomes a proposal if it beats the
                  baseline by more than the multiple-testing threshold allows
                  for the number of comparisons made
          |
    VALIDATION    proposals are re-measured on later, untouched sessions.
                  Anything that does not repeat is dropped here
          |
    HOLDOUT       only the frozen shortlist is measured, once, plus a
                  walk-forward across the folds

Prior evidence to keep in view while reading the output: an earlier search of
231 condition combinations on this same pool reached a best T1-before-SL of
43.4%, and the whole-pool baseline is slightly negative. A cohort claiming a
much higher T1 rate is far more likely to be one of the false positives this
module counts than a discovery.
"""
from __future__ import annotations

from collections.abc import Callable
from math import log, sqrt
from statistics import median

from app.research.phase15 import attribution, folds as folds_mod
from app.research.phase15a import ab
from app.research.phase15b import dimensions as dims

# Rows a cohort needs in a period before its numbers are quoted as evidence.
MIN_COHORT = 100

# Two-sided significance budget, spread across every comparison the module
# makes. Bonferroni is blunt, but the alternative here is quoting the best of
# hundreds of comparisons at a threshold designed for one.
ALPHA = 0.05

# A cohort must beat the baseline by at least this much to be worth a live
# option's spread even before cost is measured. The 5-year study's best stable
# cohort was +0.086R, so this is deliberately below that and above zero.
MIN_LIFT_R = 0.02

# Verdicts. No other string may be attached to a setup.
SURVIVES = "SURVIVES"
SURVIVES_SO_FAR = "SURVIVES_SO_FAR"
IN_SAMPLE_ONLY = "IN_SAMPLE_ONLY"
THIN = "REQUIRES_MORE_DATA"
FAILS = "FAILS"

UNDERLYING_ONLY = "UNDERLYING_ONLY"

# What the live option layer must still verify before any surviving setup
# becomes a paper trade. None of it is in the underlying pool, so none of it can
# be inferred from anything this module prints.
LIVE_LAYER_CHECKS: tuple[str, ...] = (
    "vehicle: CE or PE (or futures) chosen from the live chain, not assumed",
    "premium: the strike's own premium at entry, against the premium floor",
    "spread: measured bid/ask, not an assumed percentage",
    "liquidity: OI and volume at that strike now",
    "IV and delta: the option's own sensitivity to the move the setup expects",
    "room: expected move against the round-trip cost in rupees at this premium",
    "entry: the live chase guard, so the fill is not chased past the plan",
)

MAX_INTERACTION_SEEDS = 3

# One fixed conjunction, tested cell by cell. Screening dimensions one at a time
# cannot find a setup whose parts are individually unremarkable but jointly
# strong — "MIDCPNIFTY in a trend with the higher timeframe" can be the best
# cohort in the pool while MIDCPNIFTY, TRENDING and WITH_HTF each look ordinary,
# because each marginal averages the good cells with the bad. This grid is the
# answer to that, and it is one fixed grid rather than a search over grids: its
# cells are counted into the significance threshold like every other comparison.
GRID: tuple[str, ...] = ("instrument", "volatility_band", "regime",
                         "htf_alignment")

# Coarser grids, used only when the finer one has no cell holding enough rows to
# measure. The choice is made on cell COUNTS alone, never on any cell's result,
# so it cannot pull a favourable cell into the study.
GRID_LADDER: tuple[tuple[str, ...], ...] = (
    GRID,
    ("instrument", "volatility_band", "htf_alignment"),
    ("universe", "volatility_band", "htf_alignment"),
    ("universe", "htf_alignment"),
)

# Dimensions that are readings of the same thing. Only one member of a family
# may seed an interaction: "INDEX + NIFTY" is not two conditions.
FAMILIES: dict[str, str] = {
    "universe": "instrument_family",
    "instrument": "instrument_family",
    "score_band": "score_family",
    "confidence_band": "score_family",
    "momentum": "score_family",
    "entry_quality": "score_family",
    "htf_alignment": "htf_family",
    "htf_strength": "htf_family",
    "extension": "geometry_family",
    "room": "geometry_family",
    "risk_level": "geometry_family",
}


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _inv_norm(p: float) -> float:
    """Standard normal quantile, Moro/Acklam rational approximation.

    Written out rather than pulled from scipy: the module must not add a
    dependency to compute one threshold, and this is accurate to ~1e-9 over the
    range of p that a significance threshold uses.
    """
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = sqrt(-2 * log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q
                + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = sqrt(-2 * log(1 - p))
        return -((((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q
                  + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q
                             + 1))
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r
            + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r
                            + b[4]) * r + 1)


def z_threshold(hypotheses: int, *, alpha: float = ALPHA) -> dict:
    """The z a cohort must clear once the number of comparisons is priced in."""
    tests = max(int(hypotheses), 1)
    unadjusted = -_inv_norm(alpha / 2.0)
    adjusted = -_inv_norm(alpha / (2.0 * tests))
    return {
        "hypotheses": tests,
        "alpha": alpha,
        "z_unadjusted": round(unadjusted, 3),
        "z_bonferroni": round(adjusted, 3),
        "expected_false_positives_unadjusted": round(tests * alpha, 2),
        "note": (f"{tests} comparisons were made, so at the usual 5% threshold "
                 f"about {round(tests * alpha, 1)} of them would look "
                 "significant on noise alone. A proposal must clear the "
                 f"adjusted threshold of {round(adjusted, 2)} sigma instead, "
                 "and then repeat on validation and holdout"),
    }


def geometry(rows: list[dict]) -> dict:
    """The plan geometry of a cohort, which is how a T1 rate can be flattered.

    A cohort with nearer targets reaches T1 more often without being better, so
    reward:risk and the rupee size of a risk unit travel with every T1 rate this
    module prints.
    """
    rrs = [v for v in (_num(r.get("reward_risk")) for r in rows)
           if v is not None]
    risks = [v for v in (_num(r.get("risk")) for r in rows) if v is not None]
    return {
        "mean_reward_risk": round(sum(rrs) / len(rrs), 3) if rrs else None,
        "median_reward_risk": round(median(rrs), 3) if rrs else None,
        "median_risk_points": round(median(risks), 3) if risks else None,
    }


def summarise(rows: list[dict], *, sessions_in_pool: int | None = None) -> dict:
    out = ab.summarise(rows, sessions_in_pool=sessions_in_pool)
    out.update(geometry(rows))
    return out


def _variance(rs: list[float], mean: float) -> float:
    if len(rs) < 2:
        return 0.0
    return sum((r - mean) ** 2 for r in rs) / (len(rs) - 1)


def compare(cohort: list[dict], baseline: list[dict], *,
            sessions_in_pool: int | None = None,
            baseline_geometry: dict | None = None) -> dict:
    """One cohort against the pool it was cut from, with its own error bar."""
    stats = summarise(cohort, sessions_in_pool=sessions_in_pool)
    c_rs = [v for v in (_num(r.get("r")) for r in cohort) if v is not None]
    b_rs = [v for v in (_num(r.get("r")) for r in baseline) if v is not None]
    lift = z = None
    base_exp = round(sum(b_rs) / len(b_rs), 4) if b_rs else None
    if c_rs and b_rs:
        c_mean, b_mean = sum(c_rs) / len(c_rs), sum(b_rs) / len(b_rs)
        lift = round(c_mean - b_mean, 4)
        se = sqrt(_variance(c_rs, c_mean) / len(c_rs)
                  + _variance(b_rs, b_mean) / len(b_rs))
        z = round(lift / se, 2) if se > 0 else None
    stats["baseline_expectancy_r"] = base_exp
    stats["lift_r"] = lift
    stats["lift_sigma"] = z
    # A cohort whose targets are nearer than the pool's is a different trade,
    # not a better one, and its T1 rate must be read with that in mind.
    base_rr = (baseline_geometry or geometry(baseline)).get("mean_reward_risk")
    own_rr = stats.get("mean_reward_risk")
    stats["geometry_comparable"] = bool(
        base_rr and own_rr and own_rr >= 0.9 * float(base_rr))
    if not stats["geometry_comparable"]:
        stats["geometry_note"] = (
            "this cohort's mean reward:risk sits below the pool's, so part of "
            "any higher T1 rate is a nearer target rather than a better setup")
    return stats


def split_periods(rows: list[dict], *, dev_share: float = 0.7) -> dict:
    """Cut development from validation on a session boundary, chronologically.

    No session may appear in both: two candidates from the same morning are not
    independent observations, and splitting mid-session would let development
    see the afternoon of a session validation is then graded on.
    """
    sessions = sorted({str(r.get("session")) for r in rows if r.get("session")})
    if len(sessions) < 2:
        return {"development": list(rows), "validation": [],
                "development_sessions": sessions, "validation_sessions": [],
                "note": ("the pool spans fewer than two sessions, so it cannot "
                         "be cut into development and validation")}
    cut = max(1, min(len(sessions) - 1, int(len(sessions) * dev_share)))
    dev_names = set(sessions[:cut])
    dev = [r for r in rows if str(r.get("session")) in dev_names]
    val = [r for r in rows if str(r.get("session")) not in dev_names]
    return {
        "development": dev,
        "validation": val,
        "development_sessions": sessions[:cut],
        "validation_sessions": sessions[cut:],
        "note": ("development and validation are cut on a session boundary, so "
                 "no session contributes rows to both"),
    }


def _sessions(rows: list[dict]) -> int:
    return len({str(r.get("session")) for r in rows if r.get("session")})


def scan_dimension(name: str, dev: list[dict], cuts: dict) -> dict:
    """Measure every bucket of one dimension on the development rows."""
    buckets: dict[str, list[dict]] = {}
    unplaceable = 0
    for row in dev:
        key = dims.bucket(row, name, cuts)
        if key is None:
            unplaceable += 1
            continue
        buckets.setdefault(key, []).append(row)
    base_geom = geometry(dev)
    days = _sessions(dev)
    rows_out = []
    for key, cohort in sorted(buckets.items()):
        stats = compare(cohort, dev, sessions_in_pool=days,
                        baseline_geometry=base_geom)
        stats["bucket"] = key
        stats["dimension"] = name
        stats["measurable"] = bool(stats["trades"] >= MIN_COHORT)
        rows_out.append(stats)
    return {
        "dimension": name,
        "buckets": rows_out,
        "buckets_tested": len(rows_out),
        "rows_without_a_reading": unplaceable,
        "note": ("a row that did not carry this dimension's reading is counted "
                 "here and excluded from its buckets rather than placed in a "
                 "default one"),
    }


def scan_grid(dev: list[dict], cuts: dict, *,
              grid: tuple[str, ...] = GRID) -> dict:
    """Measure every cell of one fixed conjunction on development rows.

    Only cells that reach the sample bar are measured, and only those count as
    comparisons: a cell holding nine rows was never a candidate for anything and
    charging the threshold for it would punish the cells that were.
    """
    cells: dict[tuple[str, ...], list[dict]] = {}
    unplaceable = 0
    for row in dev:
        key = tuple(dims.bucket(row, name, cuts) for name in grid)
        if any(part is None for part in key):
            unplaceable += 1
            continue
        cells.setdefault(tuple(str(part) for part in key), []).append(row)
    base_geom = geometry(dev)
    days = _sessions(dev)
    measured, thin = [], 0
    for key, cohort in cells.items():
        if len(cohort) < MIN_COHORT:
            thin += 1
            continue
        conditions = dict(zip(grid, key, strict=True))
        stats = compare(cohort, dev, sessions_in_pool=days,
                        baseline_geometry=base_geom)
        stats["bucket"] = dims.label(conditions)
        stats["dimension"] = "conjunction"
        stats["conditions"] = conditions
        stats["measurable"] = True
        measured.append(stats)
    measured.sort(key=lambda r: -(r.get("expectancy_r") or -9.0))
    return {
        "dimension": "conjunction",
        "grid": list(grid),
        "rows": len(dev),
        "cells_populated": len(cells),
        "cells_measured": len(measured),
        "cells_under_the_bar": thin,
        "buckets": measured,
        "buckets_tested": len(measured),
        "rows_without_a_reading": unplaceable,
        "note": (f"cells of {' x '.join(grid)} holding at least {MIN_COHORT} "
                 "development rows. A conjunction can be strong while each of "
                 "its parts looks ordinary, which is why the marginal scans "
                 "alone are not enough"),
    }


def scan_conjunction(dev: list[dict], cuts: dict, *,
                     ladder: tuple[tuple[str, ...], ...] = GRID_LADDER) -> dict:
    """The finest conjunction the sample can actually support.

    A four-way grid over a short pool splits it into cells of nine rows, which
    measures nothing; a two-way grid over five years is coarse but real. So the
    ladder is walked from fine to coarse and stops at the first grid with a
    measurable cell, and the report says which grid that was.
    """
    attempts = []
    for grid in ladder:
        scan = scan_grid(dev, cuts, grid=grid)
        attempts.append({"grid": list(grid),
                         "cells_populated": scan["cells_populated"],
                         "cells_measured": scan["cells_measured"]})
        if scan["cells_measured"] > 0:
            scan["grids_attempted"] = attempts
            scan["grid_choice_note"] = (
                f"{' x '.join(grid)} is the finest grid in which any cell holds "
                f"{MIN_COHORT} development rows. Coarser or finer grids were "
                "chosen on cell counts alone, never on a cell's result")
            return scan
    scan = scan_grid(dev, cuts, grid=ladder[-1])
    scan["grids_attempted"] = attempts
    scan["grid_choice_note"] = (
        f"no conjunction had a cell holding {MIN_COHORT} development rows, so "
        "no conjunction was tested. This is a sample-size limit, not evidence "
        "that conjunctions do not matter")
    return scan


def _grid_proposals(grid_scan: dict, threshold: dict) -> list[dict]:
    out = []
    for row in grid_scan["buckets"]:
        lift, z = row.get("lift_r"), row.get("lift_sigma")
        if lift is None or z is None:
            continue
        if lift >= MIN_LIFT_R and z >= threshold["z_bonferroni"]:
            out.append({"conditions": dict(row["conditions"]),
                        "label": row["bucket"],
                        "kind": "conjunction",
                        "development": row})
    return out


def _proposal(dim_scan: dict, threshold: dict) -> list[dict]:
    out = []
    for row in dim_scan["buckets"]:
        lift, z = row.get("lift_r"), row.get("lift_sigma")
        if not row["measurable"] or lift is None or z is None:
            continue
        if lift >= MIN_LIFT_R and z >= threshold["z_bonferroni"]:
            out.append({"conditions": {row["dimension"]: row["bucket"]},
                        "label": dims.label({row["dimension"]: row["bucket"]}),
                        "kind": "single",
                        "development": row})
    return out


def _measure(setup: dict, rows: list[dict], cuts: dict) -> dict:
    keep = dims.selector(setup["conditions"], cuts)
    cohort = [r for r in rows if keep(r)]
    return compare(cohort, rows, sessions_in_pool=_sessions(rows))


def _interaction_seeds(confirmed: list[dict]) -> list[dict]:
    """Up to three confirmed single conditions, from distinct dimensions."""
    seen: set[str] = set()
    seeds = []
    singles = [s for s in confirmed if s["kind"] == "single"]
    for setup in sorted(singles,
                        key=lambda s: -(s["validation"]["lift_r"] or 0.0)):
        dim = next(iter(setup["conditions"]))
        # instrument and universe say the same thing twice, as do score_band and
        # confidence_band; combining a family with itself adds a hypothesis and
        # no information.
        family = FAMILIES.get(dim, dim)
        if family in seen:
            continue
        seen.add(family)
        seeds.append(setup)
        if len(seeds) >= MAX_INTERACTION_SEEDS:
            break
    return seeds


def _combinations(seeds: list[dict]) -> list[dict]:
    """A bounded interaction set: every pair, and the triple if there are three.

    Bounded on purpose. An unbounded grid over sixteen dimensions is thousands
    of comparisons, and at that point the best cell in the grid says nothing
    about the market.
    """
    out = []
    for i in range(len(seeds)):
        for j in range(i + 1, len(seeds)):
            merged = dict(seeds[i]["conditions"])
            merged.update(seeds[j]["conditions"])
            out.append({"conditions": merged, "label": dims.label(merged),
                        "kind": "pair"})
    if len(seeds) >= 3:
        merged: dict[str, str] = {}
        for seed in seeds[:3]:
            merged.update(seed["conditions"])
        out.append({"conditions": merged, "label": dims.label(merged),
                    "kind": "triple"})
    return out


# Selecting on room or extension changes the PLAN, not the moment: it says
# "prefer setups whose target sits further from the stop", which in option terms
# needs a bigger move before the premium clears its own round trip. It is a real
# result, but it is not a market-timing result and must not be read as one.
GEOMETRY_DIMENSIONS = ("room", "extension", "risk_level")


def geometry_selection(conditions: dict[str, str]) -> bool:
    return any(dim in GEOMETRY_DIMENSIONS for dim in conditions)


def _verdict(setup: dict, walk: dict) -> tuple[str, str]:
    dev, val, hold = setup["development"], setup["validation"], setup["holdout"]
    thin = [name for name, block in (("development", dev), ("validation", val),
                                     ("holdout", hold))
            if int(block.get("trades") or 0) < MIN_COHORT]
    if thin:
        return THIN, (f"{setup['label']} has fewer than {MIN_COHORT} rows in "
                      f"{', '.join(thin)}, so it is not evidence either way yet")
    h_lift = hold.get("lift_r")
    h_pf = hold.get("profit_factor")
    if h_lift is None or h_lift <= 0:
        return IN_SAMPLE_ONLY, (
            f"{setup['label']} beat the baseline in development and validation "
            f"and did not on the holdout ({h_lift}R), which is what an "
            "overfitted cohort looks like")
    if h_pf is None or h_pf <= 1.0:
        return FAILS, (f"{setup['label']} lifted expectancy on the holdout but "
                       f"its profit factor is {h_pf}, so it does not pay for "
                       "itself")
    if walk.get("verdict") == folds_mod.STABLE:
        return SURVIVES, (
            f"{setup['label']} beat the baseline in development, repeated on "
            f"validation, held on the holdout (+{h_lift}R, PF {h_pf}) and was "
            f"positive in {walk.get('positive_folds')}/"
            f"{walk.get('measured_folds')} walk-forward folds. Still "
            f"{UNDERLYING_ONLY}: no option was priced")
    return SURVIVES_SO_FAR, (
        f"{setup['label']} held on the holdout (+{h_lift}R, PF {h_pf}) but was "
        f"positive in only {walk.get('positive_folds')}/"
        f"{walk.get('measured_folds')} folds "
        f"({walk.get('verdict')}), so it has not shown it works in every "
        "regime")


def rank(setups: list[dict]) -> list[dict]:
    """Survivors ordered by holdout expectancy, with what a rank does not mean."""
    live = [s for s in setups
            if s["verdict"] in (SURVIVES, SURVIVES_SO_FAR)]
    live.sort(key=lambda s: -(s["holdout"].get("expectancy_r") or 0.0))
    out = []
    for i, setup in enumerate(live, start=1):
        hold = setup["holdout"]
        out.append({
            "rank": i,
            "setup": setup["label"],
            "kind": setup["kind"],
            "holdout_trades": hold.get("trades"),
            "t1_pct": hold.get("t1_pct"),
            "expectancy_r": hold.get("expectancy_r"),
            "lift_r": hold.get("lift_r"),
            "profit_factor": hold.get("profit_factor"),
            "max_drawdown_r": hold.get("max_drawdown_r"),
            "candidates_per_day": hold.get("signals_per_day"),
            "median_mfe_points": hold.get("median_mfe_points"),
            "mean_reward_risk": hold.get("mean_reward_risk"),
            "geometry_comparable": hold.get("geometry_comparable"),
            "walk_forward": setup["walk_forward"].get("verdict"),
            "verdict": setup["verdict"],
            "cost_basis": UNDERLYING_ONLY,
        })
    return out


def handoff(ranked: list[dict]) -> dict:
    """What the live option layer must verify before any of this is traded.

    This is the contract between the two tests the user described: history says
    whether the market setup selects, and only the live chain says whether the
    option at that moment is worth buying.
    """
    return {
        "setups": [row["setup"] for row in ranked],
        "must_verify_live": list(LIVE_LAYER_CHECKS),
        "paper_rule": ("historically surviving market setup AND live option "
                       "tradable AND live entry within the plan = paper A+ "
                       "candidate. Any one of the three missing = NO TRADE"),
        "not_established_here": (
            "option profitability. Every number in this study is gross "
            f"{UNDERLYING_ONLY} underlying R: no premium, spread, brokerage, "
            "slippage or expiry decay has been applied, and a positive "
            "underlying cohort can still lose money once they are"),
    }


def run(in_sample: list[dict], holdout: list[dict], *,
        dev_share: float = 0.7,
        folds: int = folds_mod.DEFAULT_FOLDS,
        min_fold_trades: int = folds_mod.MIN_FOLD_TRADES) -> dict:
    """The whole three-stage discovery, from raw pool to ranked survivors."""
    in_guard = attribution.guard(in_sample)
    hold_guard = attribution.guard(holdout)
    clean_in = in_guard["clean_rows"]
    hold_rows = hold_guard["clean_rows"]

    split = split_periods(clean_in, dev_share=dev_share)
    dev_rows, val_rows = split["development"], split["validation"]
    cuts = dims.fit_cuts(dev_rows)

    # --- stage 1: every bucket of every dimension, on development only -------
    scans = [scan_dimension(name, dev_rows, cuts)
             for name in dims.DIMENSION_NAMES]
    grid_scan = scan_conjunction(dev_rows, cuts)
    hypotheses = (sum(s["buckets_tested"] for s in scans)
                  + grid_scan["buckets_tested"])
    # The interaction set is bounded, so its size is known before it is built
    # and can be included in the threshold rather than smuggled past it.
    seeds_possible = min(MAX_INTERACTION_SEEDS, len(scans))
    interaction_budget = (seeds_possible * (seeds_possible - 1)) // 2 + (
        1 if seeds_possible >= 3 else 0)
    threshold = z_threshold(hypotheses + interaction_budget)

    proposals: list[dict] = []
    for scan in scans:
        proposals.extend(_proposal(scan, threshold))
    proposals.extend(_grid_proposals(grid_scan, threshold))

    # --- stage 2: do the proposals repeat on later sessions? ----------------
    confirmed, rejected = [], []
    for setup in proposals:
        setup["validation"] = _measure(setup, val_rows, cuts)
        lift = setup["validation"].get("lift_r")
        if (int(setup["validation"].get("trades") or 0) >= MIN_COHORT
                and lift is not None and lift > 0):
            confirmed.append(setup)
        else:
            setup["dropped_at"] = "VALIDATION"
            setup["reason"] = (
                f"{setup['label']} beat the baseline in development and did "
                f"not repeat on validation (lift {lift}R over "
                f"{setup['validation'].get('trades')} rows)")
            rejected.append(setup)

    # --- bounded interactions, from confirmed singles only ------------------
    for combo in _combinations(_interaction_seeds(confirmed)):
        combo["development"] = _measure(combo, dev_rows, cuts)
        combo["validation"] = _measure(combo, val_rows, cuts)
        dev_ok = (int(combo["development"].get("trades") or 0) >= MIN_COHORT
                  and (combo["development"].get("lift_r") or 0) >= MIN_LIFT_R)
        val_ok = (int(combo["validation"].get("trades") or 0) >= MIN_COHORT
                  and (combo["validation"].get("lift_r") or 0) > 0)
        if dev_ok and val_ok:
            confirmed.append(combo)
        else:
            combo["dropped_at"] = "VALIDATION"
            combo["reason"] = (
                f"{combo['label']} did not clear development and validation "
                "together, so the combination adds nothing to its parts")
            rejected.append(combo)

    # --- stage 3: the holdout, touched once, with a frozen shortlist --------
    graded = []
    for setup in confirmed:
        setup["holdout"] = _measure(setup, hold_rows, cuts)
        setup["walk_forward"] = folds_mod.walk_forward(
            hold_rows, dims.selector(setup["conditions"], cuts),
            folds=folds, min_fold_trades=min_fold_trades)
        verdict, note = _verdict(setup, setup["walk_forward"])
        if geometry_selection(setup["conditions"]):
            note += (". This selects on plan geometry rather than on the "
                     "market: it prefers trades whose target sits further "
                     "from the stop, and a wider target needs a bigger move "
                     "before the option premium clears its round trip, which "
                     "this study cannot price")
        setup["geometry_selection"] = geometry_selection(setup["conditions"])
        setup["verdict"], setup["note"] = verdict, note
        graded.append(setup)

    ranked = rank(graded)
    baselines = {
        "development": summarise(dev_rows, sessions_in_pool=_sessions(dev_rows)),
        "validation": summarise(val_rows, sessions_in_pool=_sessions(val_rows)),
        "holdout": summarise(hold_rows, sessions_in_pool=_sessions(hold_rows)),
    }
    return {
        "phase": "15B",
        "purpose": ("discover selective market setups on 5 years of underlying "
                    "history, so the live option layer only has to answer what "
                    "history cannot"),
        "research_only": True,
        "live_orders": False,
        "cost_basis": UNDERLYING_ONLY,
        "periods": {
            "development_rows": len(dev_rows),
            "validation_rows": len(val_rows),
            "holdout_rows": len(hold_rows),
            "development_sessions": len(split["development_sessions"]),
            "validation_sessions": len(split["validation_sessions"]),
            "holdout_sessions": _sessions(hold_rows),
            "split_note": split["note"],
        },
        "integrity": {
            "data_errors": (int(in_guard["data_errors"])
                            + int(hold_guard["data_errors"])),
            "by_failure": {**in_guard["by_failure"], **hold_guard["by_failure"]},
            "note": attribution.DATA_ERROR + ": " + in_guard["note"],
        },
        "baselines": baselines,
        "cuts": {"tertiles": cuts["tertiles"], "fitted_on": cuts["fitted_on"],
                 "note": cuts["note"]},
        "multiple_testing": threshold,
        "dimensions": scans,
        "conjunction": grid_scan,
        "proposals": len(proposals),
        "confirmed": len(confirmed),
        "rejected": [
            {"label": s["label"], "dropped_at": s["dropped_at"],
             "reason": s["reason"]} for s in rejected],
        "setups": [
            {"label": s["label"], "kind": s["kind"],
             "conditions": s["conditions"], "development": s["development"],
             "validation": s["validation"], "holdout": s["holdout"],
             "geometry_selection": s["geometry_selection"],
             "walk_forward": {k: v for k, v in s["walk_forward"].items()
                              if k != "rows"},
             "verdict": s["verdict"], "note": s["note"]}
            for s in graded],
        "ranking": ranked,
        "handoff_to_live": handoff(ranked),
        "disclaimers": [
            f"every row is gross and {UNDERLYING_ONLY}: no premium, spread, "
            "brokerage or slippage is applied, and no row is option P&L",
            "T1-before-SL is printed beside expectancy, profit factor, "
            "reward:risk and the median move in points, because a nearer target "
            "raises a T1 rate without improving anything",
            "the holdout was measured once, on a shortlist frozen by "
            "development and validation; no threshold in this study was chosen "
            "by reading it",
            "an earlier search of 231 combinations on this pool reached a best "
            "T1-before-SL of 43.4%; a cohort claiming far more than that is "
            "more likely one of the counted false positives than a discovery",
            "candidates overlap in time and are not a tradable equity curve",
            "a surviving setup is a market-selection result and authorises "
            "nothing beyond being offered to the live option layer",
            "the marginal scans average each dimension's good cells with its "
            "bad ones, so a conjunction can be strong while its parts look "
            "ordinary; the fixed "
            + " x ".join(GRID) + " grid exists for that case, and any "
            "conjunction outside it was not tested",
        ],
    }


def selector_for(setup_label: str, study: dict,
                 cuts: dict) -> Callable[[dict], bool] | None:
    """Rebuild one graded setup's predicate, for reuse by the paper layer."""
    for setup in study["setups"]:
        if setup["label"] == setup_label:
            return dims.selector(setup["conditions"], cuts)
    return None
