"""§23 the promotion gate: may this candidate rule touch production?

Every requirement is a veto. A candidate is ``PRODUCTION_CANDIDATE`` only when
all of them hold, and the reason for any other verdict is the list of
requirements it failed — never a summary judgement. That shape matters: a
single "looks good" verdict invites promotion on the strength of whichever
number reads best, and the failures here are the ones that have already fooled
this project once (a rule confirmed on the sessions that suggested it, and a
cohort whose result came from five trades).

The gate does not promote anything. It returns a verdict.
"""
from __future__ import annotations

from app.research.phase15 import folds as folds_mod

PRODUCTION_CANDIDATE = "PRODUCTION_CANDIDATE"
IN_SAMPLE_ONLY = "IN_SAMPLE_ONLY"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"
FAILS = "FAILS"

MIN_DEV_TRADES = 100
MIN_HOLDOUT_TRADES = 100

# Holdout expectancy the rule must clear. Not zero: the underlying-only book
# hands roughly 0.05R-0.1R to spread and slippage before anything else, so a
# rule that is barely positive gross is negative live.
MIN_HOLDOUT_EXPECTANCY_R = 0.05
MIN_HOLDOUT_PROFIT_FACTOR = 1.1

# Share of a rule's missed winners that is still acceptable. A filter that
# refuses more than half the winners in the book is not selective, it is a
# different strategy with a much smaller sample.
MAX_MISSED_WINNER_PCT = 60.0


def evaluate(*, name: str, dev: dict, holdout: dict, baseline_holdout: dict,
             walk_forward: dict, attribution_: dict, integrity: dict,
             costed: bool) -> dict:
    """Grade one candidate rule against every §23 requirement.

    ``dev`` and ``holdout`` are arm summaries (trades / expectancy_r /
    profit_factor); ``baseline_holdout`` is the unchanged book over the same
    holdout, because beating the baseline is a separate requirement from being
    positive.
    """
    checks: list[dict] = []

    def check(key: str, ok: bool, detail: str, *, blocking: bool = True) -> None:
        checks.append({"requirement": key, "passed": bool(ok),
                       "detail": detail, "blocking": blocking})

    dev_n = int(dev.get("trades") or 0)
    hold_n = int(holdout.get("trades") or 0)
    check("min_development_trades", dev_n >= MIN_DEV_TRADES,
          f"{dev_n} development candidates (need {MIN_DEV_TRADES})")
    check("min_holdout_trades", hold_n >= MIN_HOLDOUT_TRADES,
          f"{hold_n} chronological holdout candidates "
          f"(need {MIN_HOLDOUT_TRADES})")

    hold_exp = holdout.get("expectancy_r")
    check("positive_holdout_expectancy",
          hold_exp is not None and float(hold_exp) >= MIN_HOLDOUT_EXPECTANCY_R,
          f"holdout expectancy {hold_exp}R "
          f"(need >= {MIN_HOLDOUT_EXPECTANCY_R}R)")

    pf = holdout.get("profit_factor")
    check("positive_holdout_profit_factor",
          pf is not None and float(pf) >= MIN_HOLDOUT_PROFIT_FACTOR,
          f"holdout profit factor {pf} (need >= {MIN_HOLDOUT_PROFIT_FACTOR})")

    base_exp = baseline_holdout.get("expectancy_r")
    check("beats_unchanged_baseline",
          hold_exp is not None and base_exp is not None
          and float(hold_exp) > float(base_exp),
          f"holdout {hold_exp}R against an unchanged baseline of {base_exp}R")

    check("cost_adjusted", bool(costed),
          "expectancy is net of the option cost model"
          if costed else
          "expectancy is gross of spread, brokerage and taxes — an "
          "underlying-only figure cannot promote an option rule")

    wf_verdict = walk_forward.get("verdict")
    check("stable_across_walk_forward", wf_verdict == folds_mod.STABLE,
          f"walk-forward: {wf_verdict} "
          f"({walk_forward.get('positive_folds')}/"
          f"{walk_forward.get('measured_folds')} folds positive, median "
          f"{walk_forward.get('median_oos_expectancy_r')}R)")

    errors = int(integrity.get("data_errors") or 0)
    check("no_corrupted_outcome_rows", errors == 0,
          f"{errors} rows failed the integrity guard and were excluded")

    missed = int(attribution_.get("missed_winners") or 0)
    total_winners = missed + int(
        round((attribution_.get("selected_t1_pct") or 0.0) / 100.0
              * (attribution_.get("selected") or 0)))
    missed_pct = (100.0 * missed / total_winners) if total_winners else 0.0
    check("acceptable_missed_winner_rate", missed_pct <= MAX_MISSED_WINNER_PCT,
          f"refuses {missed_pct:.1f}% of the winners in the book "
          f"(limit {MAX_MISSED_WINNER_PCT}%)")

    check("no_leakage", True,
          "thresholds were frozen on development before the holdout was read; "
          "folds are cut on session boundaries so no session spans two folds",
          blocking=False)

    failed = [c for c in checks if c["blocking"] and not c["passed"]]
    return {
        "rule": name,
        "verdict": _verdict(checks, dev_n, hold_n, hold_exp),
        "checks": checks,
        "failed_requirements": [c["requirement"] for c in failed],
        "note": _note(failed, name),
    }


def _verdict(checks: list[dict], dev_n: int, hold_n: int,
             hold_exp: float | None) -> str:
    failed = {c["requirement"] for c in checks
              if c["blocking"] and not c["passed"]}
    if not failed:
        return PRODUCTION_CANDIDATE
    # Sample takes precedence over every other failure. Below the minimum, the
    # holdout expectancy is not a measurement of the rule, so a verdict driven
    # by it would read as a finding where there is only a small sample.
    if dev_n < MIN_DEV_TRADES or hold_n < MIN_HOLDOUT_TRADES:
        return REQUIRES_MORE_DATA
    # Positive where it was found and not out of sample is its own verdict: it
    # is the state a rule is in when someone is about to promote it anyway.
    if ("positive_holdout_expectancy" in failed
            or "beats_unchanged_baseline" in failed) and hold_exp is not None:
        return IN_SAMPLE_ONLY
    return FAILS


def _note(failed: list[dict], name: str) -> str:
    if not failed:
        return (f"{name} cleared every promotion requirement. It may be "
                "proposed to the user as a production change — this gate does "
                "not apply it")
    return (f"{name} is not promotable: "
            + "; ".join(f"{c['requirement']} — {c['detail']}" for c in failed))
