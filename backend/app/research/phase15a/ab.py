"""Phase 15A §2-§12 — the four-arm A/B. RESEARCH ONLY.

Two changes came out of the 201,144-candidate scan: the opening gate measured
harmful out of sample, and every single-stock cohort measured negative. Both are
tempting to apply at once, which is exactly how a project convinces itself of
something. So this grades four arms, not two:

    BASELINE   opening gate ON,  all instruments
    A_ONLY     opening gate OFF, all instruments
    B_ONLY     opening gate ON,  five indices
    A_PLUS_B   opening gate OFF, five indices

A_ONLY and B_ONLY exist because a combined arm cannot tell you which half did
the work — or whether one half is carrying a change that the other quietly
cancels. The interaction term is reported for the same reason.

Every arm is graded on the identical pool, with identical outcome definitions,
identical fold boundaries and identical development/holdout split. Only the two
variables move. Results are gross and UNDERLYING_ONLY: no arm here is evidence
about option P&L, and the promotion verdict is a *paper* verdict.
"""
from __future__ import annotations

from statistics import median
from typing import Callable

from app.research.phase15 import attribution as attr
from app.research.phase15 import folds as folds_mod
from app.research.phase15 import shadow as shadow_mod

# The five index names the experiment universe keeps. Written out rather than
# derived from "is it an index", because the point of a frozen experiment
# universe is that it cannot drift when the registry changes.
INDEX_UNIVERSE: tuple[str, ...] = (
    "NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY", "FINNIFTY",
)

# Minutes after 09:15 IST that the production gate refuses. The definition is
# copied from the gate under test (phase14.rules.skip_first_15_minutes) so the
# arm measures the live rule and not a near-miss of it.
OPENING_MINUTES = 15.0

# Minutes in an IST equity session, 09:15 to 15:30. A candidate stamped outside
# that window did not come from an index or stock session: it means the pool's
# bar times were not session-aligned when it was built. Such rows are withheld
# rather than counted as "opening window", because a 00:08 stamp landing in the
# opening cohort would hand the gate a verdict it did not earn. MCX runs to
# 23:30 and is out of scope for this phase: a commodity pool needs its own
# session length here before either variable can be graded on it.
SESSION_MINUTES = 375.0

BASELINE = "BASELINE"
A_ONLY = "A_ONLY"
B_ONLY = "B_ONLY"
A_PLUS_B = "A_PLUS_B"
ARMS: tuple[str, ...] = (BASELINE, A_ONLY, B_ONLY, A_PLUS_B)

# (opening gate applied, universe restricted to the five indices)
ARM_SPEC: dict[str, tuple[bool, bool]] = {
    BASELINE: (True, False),
    A_ONLY: (False, False),
    B_ONLY: (True, True),
    A_PLUS_B: (False, True),
}

# Lenses every arm is reported through. The candidate lens answers "is the
# change good for the strategy"; the engine lens answers "is it good for what
# production would actually have placed", and the two can disagree — the scan
# found the engine's BUY bars performing like the bars it refused.
ALL_CANDIDATES = "ALL_CANDIDATES"
ENGINE_BUY = "ENGINE_BUY_ONLY"
LENSES: tuple[str, ...] = (ALL_CANDIDATES, ENGINE_BUY)

PAPER_CANDIDATE = "PAPER_PROMOTION_CANDIDATE"
FAILS = "FAILS"
THIN = "REQUIRES_MORE_DATA"

MIN_DEV = 100
MIN_HOLDOUT = 100
MIN_PROFIT_FACTOR = 1.0

TARGET = "TARGET"
STOP = "STOP"


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def instrument_of(row: dict) -> str:
    name = row.get("instrument")
    return name.upper() if isinstance(name, str) else ""


def in_index_universe(row: dict) -> bool:
    return instrument_of(row) in INDEX_UNIVERSE


def after_open(row: dict) -> bool:
    """Did this candidate appear after the first 15 minutes of its session?

    A row whose minute stamp is missing cannot be placed on either side of the
    gate. Such rows are excluded from every arm by :func:`gradable` rather than
    silently assumed to be late in the session, which would put unmeasured rows
    into the arm that keeps the most.
    """
    minute = _num(row.get("entry_minute_ist"))
    return minute is not None and minute >= OPENING_MINUTES


def in_session(row: dict) -> bool:
    minute = _num(row.get("entry_minute_ist"))
    return minute is not None and 0.0 <= minute <= SESSION_MINUTES


def gradable(rows: list[dict]) -> dict:
    """Rows that can be placed on both sides of both variables.

    An arm comparison is only honest if every arm is offered the same rows, so a
    row missing the minute stamp or the instrument name is removed from all four
    and counted here instead.
    """
    keep = []
    unmeasurable = {"minute_unknown": 0, "minute_out_of_session": 0,
                    "instrument_unknown": 0}
    for row in rows:
        if not instrument_of(row):
            unmeasurable["instrument_unknown"] += 1
            continue
        if _num(row.get("entry_minute_ist")) is None:
            unmeasurable["minute_unknown"] += 1
            continue
        if not in_session(row):
            unmeasurable["minute_out_of_session"] += 1
            continue
        keep.append(row)
    return {
        "rows": len(rows),
        "gradable": len(keep),
        "excluded": sum(unmeasurable.values()),
        "by_reason": unmeasurable,
        "gradable_rows": keep,
        "note": ("a row without a usable session-minute stamp or an "
                 "instrument name cannot be placed on either side of the two "
                 "variables, so it is withheld from all four arms instead of "
                 "defaulting into the arm that keeps the most. A large "
                 "minute_out_of_session count means the pool's bar times were "
                 "not session-aligned when it was built, and the opening-gate "
                 "result should not be read until it is rebuilt"),
    }


def selector(arm: str, *, lens: str = ALL_CANDIDATES) -> Callable[[dict], bool]:
    """The keep-predicate for one arm under one lens."""
    if arm not in ARM_SPEC:
        raise ValueError(f"unknown arm {arm!r}; expected one of {ARMS}")
    if lens not in LENSES:
        raise ValueError(f"unknown lens {lens!r}; expected one of {LENSES}")
    gate_on, index_only = ARM_SPEC[arm]

    def keep(row: dict) -> bool:
        if lens == ENGINE_BUY and not bool(row.get("taken")):
            return False
        if gate_on and not after_open(row):
            return False
        if index_only and not in_index_universe(row):
            return False
        return True

    return keep


def _max_drawdown(rs: list[float]) -> float:
    peak = equity = worst = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return round(worst, 2)


def _median_minutes(rows: list[dict], reason: str) -> float | None:
    vals = [_num(r.get("minutes")) for r in rows
            if r.get("exit_reason") == reason]
    clean = [v for v in vals if v is not None]
    return round(median(clean), 1) if clean else None


def summarise(rows: list[dict], *, sessions_in_pool: int | None = None) -> dict:
    """The measured record of one arm's kept candidates. No forecast in here."""
    n = len(rows)
    if n == 0:
        return {"trades": 0, "expectancy_r": None, "t1_pct": None,
                "profit_factor": None, "net_r": 0.0, "max_drawdown_r": 0.0,
                "note": "this arm kept nothing, so it has no record to report"}
    rs = [float(r["r"]) for r in rows if _num(r.get("r")) is not None]
    gain = sum(r for r in rs if r > 0)
    loss = -sum(r for r in rs if r < 0)
    t1 = sum(1 for r in rows if r.get("exit_reason") == TARGET)
    sl = sum(1 for r in rows if r.get("exit_reason") == STOP)
    mfes = [v for v in (_num(r.get("mfe_r")) for r in rows) if v is not None]
    maes = [v for v in (_num(r.get("mae_r")) for r in rows) if v is not None]
    sessions = {str(r.get("session")) for r in rows if r.get("session")}
    days = sessions_in_pool if sessions_in_pool else len(sessions)
    return {
        "trades": n,
        "expectancy_r": round(sum(rs) / len(rs), 4) if rs else None,
        "net_r": round(sum(rs), 2),
        "t1_pct": round(100.0 * t1 / n, 1),
        "sl_pct": round(100.0 * sl / n, 1),
        "neither_pct": round(100.0 * (n - t1 - sl) / n, 1),
        "profit_factor": round(gain / loss, 2) if loss > 0 else None,
        "max_drawdown_r": _max_drawdown(rs),
        "mean_mfe_r": round(sum(mfes) / len(mfes), 3) if mfes else None,
        "mean_mae_r": round(sum(maes) / len(maes), 3) if maes else None,
        "median_minutes_to_t1": _median_minutes(rows, TARGET),
        "median_minutes_to_sl": _median_minutes(rows, STOP),
        "sessions_with_a_candidate": len(sessions),
        "signals_per_day": round(n / days, 2) if days else None,
        # Two arms with the same R and different point moves are not the same
        # trade: cost is fixed in rupees, so the points are what decide whether
        # a target clears its own round trip.
        "median_mfe_points": _median_points(rows),
        "gross_only": True,
        "cost_basis": "UNDERLYING_ONLY",
    }


def _median_points(rows: list[dict]) -> float | None:
    pts = []
    for row in rows:
        mfe, risk = _num(row.get("mfe_r")), _num(row.get("risk"))
        if mfe is not None and risk is not None and risk > 0:
            pts.append(mfe * risk)
    return round(median(pts), 3) if pts else None


def delta(rows: list[dict], base_keep: Callable[[dict], bool],
          arm_keep: Callable[[dict], bool]) -> dict:
    """§17 what an arm gained and what it gave up, against the baseline arm.

    Reported as four counts rather than one net figure, because "expectancy
    improved" is equally true of an arm that found winners and of an arm that
    merely stopped trading — and only one of those is an edge.
    """
    gained = [r for r in rows if arm_keep(r) and not base_keep(r)]
    lost = [r for r in rows if base_keep(r) and not arm_keep(r)]

    def side(cohort: list[dict]) -> dict:
        rs = [float(r["r"]) for r in cohort if _num(r.get("r")) is not None]
        mfes = [v for v in (_num(r.get("mfe_r")) for r in cohort)
                if v is not None]
        return {
            "candidates": len(cohort),
            "would_have_hit_t1": sum(1 for r in cohort
                                     if r.get("exit_reason") == TARGET),
            "would_have_hit_sl": sum(1 for r in cohort
                                     if r.get("exit_reason") == STOP),
            "counterfactual_r": round(sum(rs), 2),
            "counterfactual_r_per_candidate": (round(sum(rs) / len(rs), 4)
                                               if rs else None),
            "mean_mfe_r": round(sum(mfes) / len(mfes), 3) if mfes else None,
        }

    gained_s, lost_s = side(gained), side(lost)
    return {
        "winners_gained": gained_s["would_have_hit_t1"],
        "losers_taken_on": gained_s["would_have_hit_sl"],
        "winners_lost": lost_s["would_have_hit_t1"],
        "losers_avoided": lost_s["would_have_hit_sl"],
        "added": gained_s,
        "removed": lost_s,
        "net_r_change": round(gained_s["counterfactual_r"]
                              - lost_s["counterfactual_r"], 2),
        "note": _delta_note(gained_s, lost_s),
    }


def _delta_note(gained: dict, lost: dict) -> str:
    parts = []
    if gained["candidates"]:
        parts.append(
            f"adds {gained['candidates']} candidates worth "
            f"{gained['counterfactual_r']:+}R "
            f"({gained['would_have_hit_t1']} reached target, "
            f"{gained['would_have_hit_sl']} stopped)")
    if lost["candidates"]:
        parts.append(
            f"removes {lost['candidates']} candidates worth "
            f"{lost['counterfactual_r']:+}R "
            f"({lost['would_have_hit_t1']} winners given up, "
            f"{lost['would_have_hit_sl']} losers avoided)")
    if not parts:
        return "this arm keeps exactly what the baseline keeps"
    return "; ".join(parts)


def by_instrument(rows: list[dict], keep: Callable[[dict], bool]) -> list[dict]:
    """Per-instrument contribution, kept separate rather than pooled.

    A pooled figure lets one instrument's losses be paid for by another's gains,
    which is the mistake that made the 11-name book look merely mediocre instead
    of showing that six of the names were unusable.
    """
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(instrument_of(row), []).append(row)
    out = []
    for name in sorted(groups):
        kept = [r for r in groups[name] if keep(r)]
        stat = summarise(kept)
        out.append({
            "instrument": name,
            "in_index_universe": name in INDEX_UNIVERSE,
            "candidates": len(groups[name]),
            "kept": len(kept),
            "t1_pct": stat.get("t1_pct"),
            "expectancy_r": stat.get("expectancy_r"),
            "profit_factor": stat.get("profit_factor"),
            "max_drawdown_r": stat.get("max_drawdown_r"),
            "median_mfe_points": stat.get("median_mfe_points"),
            "signals_per_day": stat.get("signals_per_day"),
        })
    return out


def opening_gate(rows: list[dict]) -> dict:
    """§5 the opening cohort priced on its own, in and out of sample.

    The gate's average across the book is not the question — a rule that refuses
    4.6% of candidates moves the average almost not at all while still being
    wrong about the cohort it refuses. So the refused cohort is priced directly
    against the cohort that is kept.
    """
    early = [r for r in rows if not after_open(r)]
    late = [r for r in rows if after_open(r)]
    early_s, late_s = summarise(early), summarise(late)
    early_exp, late_exp = early_s.get("expectancy_r"), late_s.get("expectancy_r")
    lift = (round(float(late_exp) - float(early_exp), 4)
            if early_exp is not None and late_exp is not None else None)
    return {
        "opening_minutes": OPENING_MINUTES,
        "refused_by_gate": early_s,
        "kept_by_gate": late_s,
        "gate_lift_r": lift,
        "share_refused_pct": (round(100.0 * len(early) / len(rows), 2)
                              if rows else 0.0),
        "verdict": _gate_verdict(lift, early, late),
    }


def _gate_verdict(lift: float | None, early: list[dict],
                  late: list[dict]) -> str:
    if lift is None or len(early) < folds_mod.MIN_FOLD_TRADES:
        return (f"only {len(early)} candidates fell inside the opening window; "
                "that is not enough to price the gate either way")
    if lift > 0:
        return (f"the candidates the gate keeps did {lift:+}R per trade BETTER "
                f"than the ones it refuses, so the gate is doing what it was "
                f"added for on this pool ({len(early)} refused, {len(late)} "
                "kept)")
    return (f"the candidates the gate refuses did {-lift:+}R per trade BETTER "
            f"than the ones it keeps — on this pool the gate is removing the "
            f"better cohort ({len(early)} refused, {len(late)} kept)")


def excluded_cohorts(rows: list[dict]) -> dict:
    """§17 the two exclusions priced separately, by reason."""
    stocks = [r for r in rows if not in_index_universe(r)]
    early = [r for r in rows if not after_open(r)]
    return {
        "stock_exclusion": {
            "reason_excluded": "not in the five-index experiment universe",
            "instruments": sorted({instrument_of(r) for r in stocks}),
            **summarise(stocks),
        },
        "opening_gate": {
            "reason_excluded": (f"entered within the first "
                                f"{int(OPENING_MINUTES)} minutes of the "
                                "session"),
            **summarise(early),
        },
    }


def interaction(arms: dict[str, dict], *, lens: str = ALL_CANDIDATES,
                period: str = "holdout") -> dict:
    """§7 do A and B add up, cancel, or is one carrying the other?

    ``a_effect + b_effect`` is what the two changes would be worth if they were
    independent. The interaction term is the measured combined effect minus that
    sum: strongly negative means the two changes are largely the same change
    counted twice.
    """
    def exp_of(name: str) -> float | None:
        arm = arms.get(name, {})
        return (arm.get(lens, {}).get(period, {}) or {}).get("expectancy_r")

    base, a, b, ab = (exp_of(BASELINE), exp_of(A_ONLY),
                      exp_of(B_ONLY), exp_of(A_PLUS_B))
    if None in (base, a, b, ab):
        return {"measurable": False,
                "note": ("at least one arm kept too little to have an "
                         "expectancy, so the interaction cannot be computed")}
    a_eff = round(float(a) - float(base), 4)
    b_eff = round(float(b) - float(base), 4)
    ab_eff = round(float(ab) - float(base), 4)
    inter = round(ab_eff - (a_eff + b_eff), 4)
    return {
        "measurable": True,
        "lens": lens,
        "period": period,
        "baseline_expectancy_r": base,
        "a_effect_r": a_eff,
        "b_effect_r": b_eff,
        "combined_effect_r": ab_eff,
        "interaction_r": inter,
        "note": _interaction_note(a_eff, b_eff, ab_eff, inter),
    }


def _interaction_note(a: float, b: float, ab: float, inter: float) -> str:
    who = [f"removing the opening gate is worth {a:+}R on its own",
           f"the index-only universe is worth {b:+}R on its own",
           f"together {ab:+}R"]
    if abs(inter) < 0.005:
        who.append("the interaction is ~0, so they act independently")
    elif inter < 0:
        who.append(f"the interaction is {inter:+}R — the two changes overlap, "
                   "so applying both buys less than adding their separate "
                   "effects suggests")
    else:
        who.append(f"the interaction is {inter:+}R — each change is worth more "
                   "in the presence of the other")
    return "; ".join(who)


def aplus_bar(dev_rows: list[dict], *, top_pct: float = 20.0) -> dict:
    """The A+ score bar, fitted on DEVELOPMENT only and then frozen.

    A+ here is not a probability and not a promise: it is the arm's own kept
    candidates that also carried an engine BUY and a trade score in the top
    slice of the development half. The bar is cut on development rows so the
    holdout cannot choose the threshold that flatters it — which is the single
    mistake that produced this project's earlier "83%" cards.
    """
    scores = sorted(
        v for v in (_num(r.get("trade_score")) for r in dev_rows
                    if bool(r.get("taken")))
        if v is not None
    )
    if len(scores) < MIN_DEV:
        return {"bar": None, "fitted_on": len(scores),
                "note": ("too few scored development BUY rows to cut an A+ "
                         "bar; the A+ arm is reported as unavailable rather "
                         "than fitted on a handful of rows")}
    idx = min(len(scores) - 1,
              max(0, int(round((1.0 - top_pct / 100.0) * (len(scores) - 1)))))
    return {"bar": round(scores[idx], 3), "fitted_on": len(scores),
            "top_pct_of_development": top_pct,
            "note": ("the bar is the development half's own score percentile, "
                     "frozen before the holdout was read")}


def aplus(rows: list[dict], keep: Callable[[dict], bool], bar: dict, *,
          sessions_in_pool: int | None = None) -> dict:
    """§9 one arm's A+ shadow, measured. Promotes nothing."""
    value = bar.get("bar")
    if value is None:
        return {"available": False, "note": bar.get("note")}

    def is_aplus(row: dict) -> bool:
        score = _num(row.get("trade_score"))
        return (keep(row) and bool(row.get("taken"))
                and score is not None and score >= float(value))

    selected = [r for r in rows if is_aplus(r)]
    return {
        "available": True,
        "bar": value,
        **summarise(selected, sessions_in_pool=sessions_in_pool),
        "attribution": attr.attribute(rows, is_aplus),
        "per_day": _per_day(rows, is_aplus),
        "label_is_not_a_probability": True,
    }


def _per_day(rows: list[dict], keep: Callable[[dict], bool],
             label: str = "A+") -> dict:
    """§10 the per-session distribution, including the days with nothing."""
    return shadow_mod.frequency(rows, keep, bar_label=label)


def paper_gate(*, name: str, dev: dict, holdout: dict,
               baseline_holdout: dict, walk_forward: dict,
               integrity: dict) -> dict:
    """§12 may this arm become a *paper* configuration? Every check is a veto.

    Deliberately not the §23 production gate: no requirement here is
    cost-adjusted, because this pool has no option fills in it. Clearing this
    gate earns an arm a paper configuration and nothing else — the words are
    PAPER_PROMOTION_CANDIDATE for exactly that reason.
    """
    checks: list[dict] = []

    def check(key: str, ok: bool, detail: str) -> None:
        checks.append({"requirement": key, "passed": bool(ok),
                       "detail": detail})

    dev_n = int(dev.get("trades") or 0)
    hold_n = int(holdout.get("trades") or 0)
    check("min_development_outcomes", dev_n >= MIN_DEV,
          f"{dev_n} development outcomes (need {MIN_DEV})")
    check("min_holdout_outcomes", hold_n >= MIN_HOLDOUT,
          f"{hold_n} chronological holdout outcomes (need {MIN_HOLDOUT})")

    hold_exp = holdout.get("expectancy_r")
    check("positive_holdout_expectancy",
          hold_exp is not None and float(hold_exp) > 0,
          f"holdout expectancy {hold_exp}R (need > 0)")

    pf = holdout.get("profit_factor")
    check("holdout_profit_factor_above_one",
          pf is not None and float(pf) > MIN_PROFIT_FACTOR,
          f"holdout profit factor {pf} (need > {MIN_PROFIT_FACTOR})")

    base_exp = baseline_holdout.get("expectancy_r")
    check("beats_unchanged_baseline",
          hold_exp is not None and base_exp is not None
          and float(hold_exp) > float(base_exp),
          f"holdout {hold_exp}R against the unchanged baseline's {base_exp}R")

    wf = walk_forward.get("verdict")
    check("stable_across_walk_forward", wf == folds_mod.STABLE,
          f"walk-forward: {wf} ({walk_forward.get('positive_folds')}/"
          f"{walk_forward.get('measured_folds')} folds positive, median "
          f"{walk_forward.get('median_oos_expectancy_r')}R)")

    errors = int(integrity.get("data_errors") or 0)
    check("no_data_integrity_contamination", errors == 0,
          f"{errors} rows failed the integrity guard and were excluded")

    check("no_leakage", True,
          "the arms are frozen definitions, not fitted thresholds: nothing "
          "was chosen by reading the holdout, and folds are cut on session "
          "boundaries so no session spans two folds")

    failed = [c["requirement"] for c in checks if not c["passed"]]
    if not failed:
        verdict = PAPER_CANDIDATE
    elif dev_n < MIN_DEV or hold_n < MIN_HOLDOUT:
        verdict = THIN
    else:
        verdict = FAILS
    return {
        "arm": name,
        "verdict": verdict,
        "checks": checks,
        "failed_requirements": failed,
        "cost_basis": "UNDERLYING_ONLY",
        "note": _gate_note(name, verdict, failed),
    }


def _gate_note(name: str, verdict: str, failed: list[str]) -> str:
    if verdict == PAPER_CANDIDATE:
        return (f"{name} cleared every paper requirement on gross, "
                "underlying-only rows. That earns a paper configuration and "
                "nothing more: no option fill has been measured, so this is "
                "not evidence about money")
    if verdict == THIN:
        return (f"{name} does not have the sample to be graded either way "
                f"yet ({', '.join(failed)})")
    return f"{name} is not a paper candidate: {', '.join(failed)}"


def run(dev: list[dict], holdout: list[dict], *,
        folds: int = folds_mod.DEFAULT_FOLDS,
        min_fold_trades: int = folds_mod.MIN_FOLD_TRADES) -> dict:
    """Grade all four arms through both lenses on one pool.

    ``dev`` and ``holdout`` are the same chronological split the rest of the
    study uses; the arms are applied to both, and every reported verdict is
    driven by the holdout and the folds rather than by the development half.
    """
    dev_guard = attr.guard(dev)
    hold_guard = attr.guard(holdout)
    dev_ok = gradable(dev_guard["clean_rows"])
    hold_ok = gradable(hold_guard["clean_rows"])
    dev_rows: list[dict] = dev_ok["gradable_rows"]
    hold_rows: list[dict] = hold_ok["gradable_rows"]
    combined = dev_rows + hold_rows

    integrity = {
        "development": {k: v for k, v in dev_guard.items()
                        if k != "clean_rows"},
        "holdout": {k: v for k, v in hold_guard.items() if k != "clean_rows"},
        "data_errors": (int(dev_guard["data_errors"])
                        + int(hold_guard["data_errors"])),
        "unmeasurable_development": {k: v for k, v in dev_ok.items()
                                     if k != "gradable_rows"},
        "unmeasurable_holdout": {k: v for k, v in hold_ok.items()
                                 if k != "gradable_rows"},
    }
    off_session = (int(dev_ok["by_reason"]["minute_out_of_session"])
                   + int(hold_ok["by_reason"]["minute_out_of_session"]))
    offered = int(dev_guard["rows"]) + int(hold_guard["rows"])
    integrity["minute_out_of_session"] = off_session
    integrity["minute_out_of_session_pct"] = (
        round(100.0 * off_session / offered, 2) if offered else 0.0)
    # The opening gate is the whole of variable A, so a pool whose stamps are
    # not session-relative cannot answer variable A at all. That is stated as a
    # blocking condition rather than left as a footnote under a printed verdict.
    integrity["opening_gate_measurable"] = bool(
        offered and 100.0 * off_session / offered < 5.0)

    dev_sessions = len({str(r.get("session")) for r in dev_rows
                        if r.get("session")})
    hold_sessions = len({str(r.get("session")) for r in hold_rows
                         if r.get("session")})

    # Cut once, on development, and reuse for every arm: a bar refitted per arm
    # would make the A+ rows a different selection in each column and the four
    # A+ summaries would no longer be comparable.
    bar = aplus_bar(dev_rows)

    arms: dict[str, dict] = {}
    for arm in ARMS:
        entry: dict = {
            "arm": arm,
            "opening_gate_applied": ARM_SPEC[arm][0],
            "universe": ("INDEX_ONLY" if ARM_SPEC[arm][1]
                         else "ALL_INSTRUMENTS"),
        }
        for lens in LENSES:
            keep = selector(arm, lens=lens)
            base_keep = selector(BASELINE, lens=lens)
            d = [r for r in dev_rows if keep(r)]
            h = [r for r in hold_rows if keep(r)]
            entry[lens] = {
                "development": summarise(d, sessions_in_pool=dev_sessions),
                "holdout": summarise(h, sessions_in_pool=hold_sessions),
                "selection_pct_holdout": (round(100.0 * len(h) / len(hold_rows), 1)
                                          if hold_rows else 0.0),
                "walk_forward": folds_mod.walk_forward(
                    combined, keep, folds=folds,
                    min_fold_trades=min_fold_trades),
                "vs_baseline_holdout": delta(hold_rows, base_keep, keep),
                "attribution_holdout": attr.attribute(hold_rows, keep),
                "capture_holdout": attr.capture(h),
                "per_day_holdout": _per_day(hold_rows, keep, label=arm),
                "aplus_holdout": aplus(hold_rows, keep, bar,
                                       sessions_in_pool=hold_sessions),
            }
        arms[arm] = entry

    baseline_hold = arms[BASELINE][ALL_CANDIDATES]["holdout"]
    baseline_hold_buy = arms[BASELINE][ENGINE_BUY]["holdout"]
    for arm in ARMS:
        for lens in LENSES:
            base = baseline_hold if lens == ALL_CANDIDATES else baseline_hold_buy
            block = arms[arm][lens]
            block["paper_gate"] = paper_gate(
                name=f"{arm} ({lens})",
                dev=block["development"], holdout=block["holdout"],
                baseline_holdout=base, walk_forward=block["walk_forward"],
                integrity=integrity)

    return {
        "phase": "15A",
        "research_only": True,
        "live_orders": False,
        "pool": {
            "development_rows": len(dev_rows),
            "holdout_rows": len(hold_rows),
            "development_sessions": dev_sessions,
            "holdout_sessions": hold_sessions,
            "instruments": sorted({instrument_of(r) for r in combined}),
            "index_universe": list(INDEX_UNIVERSE),
            "excluded_stocks": sorted(
                {instrument_of(r) for r in combined}
                - set(INDEX_UNIVERSE)),
        },
        "integrity": integrity,
        "arms": arms,
        "opening_gate_development": opening_gate(dev_rows),
        "opening_gate_holdout": opening_gate(hold_rows),
        "excluded_cohorts_holdout": excluded_cohorts(hold_rows),
        "by_instrument_holdout": by_instrument(
            hold_rows, selector(BASELINE, lens=ALL_CANDIDATES)),
        "interaction": interaction(arms),
        "interaction_engine_buy": interaction(arms, lens=ENGINE_BUY),
        "final_table": final_table(arms),
        "disclaimers": [
            "R is gross and UNDERLYING_ONLY: no brokerage, tax or spread is "
            "charged, and none of these rows is option P&L",
            "the four arms are frozen definitions rather than fitted "
            "thresholds, so nothing here was chosen by reading the holdout",
            "candidates overlap in time and are not a tradable equity curve; "
            "the arms answer 'does this change select better trades', not "
            "'what would this have earned'",
            "PAPER_PROMOTION_CANDIDATE authorises a paper configuration and "
            "nothing else — no arm here is a production change",
            "t1_pct is a measured share of a cohort's own rows, not a "
            "forecast, and no probability is published anywhere in this report",
        ],
    }


def final_table(arms: dict[str, dict], *,
                lens: str = ALL_CANDIDATES) -> list[dict]:
    """§20 the one required table, holdout only."""
    rows = []
    for arm in ARMS:
        block = arms.get(arm, {}).get(lens, {})
        hold = block.get("holdout", {})
        rows.append({
            "arm": arm,
            "universe": arms.get(arm, {}).get("universe"),
            "skip_15m": arms.get(arm, {}).get("opening_gate_applied"),
            "candidates": hold.get("trades"),
            "t1_pct": hold.get("t1_pct"),
            "net_r": hold.get("net_r"),
            "expectancy_r": hold.get("expectancy_r"),
            "profit_factor": hold.get("profit_factor"),
            "max_drawdown_r": hold.get("max_drawdown_r"),
            "signals_per_day": hold.get("signals_per_day"),
            "walk_forward": block.get("walk_forward", {}).get("verdict"),
            "verdict": block.get("paper_gate", {}).get("verdict"),
        })
    return rows
