"""Phase 15 smokes — folds, attribution, components, shadow, promotion.

Each check pins a claim the report makes, and several pin a *refusal*: the
shadow list must not pad to a quota, a probability must not appear without a
calibration record, and a rule must not be promotable on a thin sample. Those
are the failure modes that would put a fabricated number in front of a trader,
so they are asserted rather than trusted.
"""
from __future__ import annotations

from app.models import Candle
from app.research.phase14 import paths
from app.research.phase15 import (
    attribution,
    components,
    exits,
    folds,
    promotion,
    shadow,
)

checked = 0


def ok(cond: bool, what: str) -> None:
    global checked
    checked += 1
    assert cond, f"FAILED: {what}"


def trade(session: str, r: float, *, exit_reason: str = "TARGET",
          mfe: float = 1.0, mae: float = -0.4, **extra) -> dict:
    row = {"session": session, "entry_ts": f"{session}T09:20:00", "r": r,
           "risk": 10.0, "entry": 100.0, "stop": 90.0, "target": 120.0,
           "exit_reason": exit_reason, "mfe_r": mfe, "mae_r": mae,
           "instrument": "NIFTY", "side": "LONG"}
    row.update(extra)
    return row


def bar_at(minute: int, *, high: float, low: float) -> Candle:
    return Candle(time=1_770_000_000 + minute * 60, open=low, high=high,
                  low=low, close=high, volume=0.0)


# ---------------------------------------------------------------- §20 integrity
bad_rows = [
    trade("2026-01-01", 1.0),
    {**trade("2026-01-02", 1.0), "risk": 0.0},
    {**trade("2026-01-03", 1.0), "r": float("inf")},
    {**trade("2026-01-04", 1.0), "stop": 100.0},
    {k: v for k, v in trade("2026-01-05", 1.0).items() if k != "r"},
]
g = attribution.guard(bad_rows)
ok(g["rows"] == 5 and g["clean"] == 1, "guard keeps only the one sound row")
ok(g["data_errors"] == 4, "guard counts every unusable row")
ok("risk_not_positive" in g["by_failure"], "zero risk is named as its failure")
ok("r_not_finite" in g["by_failure"],
   "an infinite R is named as its failure")
ok(all("r" in row and row["risk"] > 0 for row in g["clean_rows"]),
   "no corrupted row reaches the clean set that feeds expectancy")

# ------------------------------------------------------------- §8 walk-forward
# Ten sessions, alternating so folds are cut on session boundaries.
pool = []
for day in range(1, 11):
    s = f"2026-02-{day:02d}"
    for i in range(20):
        pool.append(trade(s, 1.0 if i % 2 else -0.5,
                          exit_reason="TARGET" if i % 2 else "STOP",
                          regime="TRENDING" if day <= 5 else "CHOP",
                          trade_score=90.0 if i % 2 else 20.0))

f = folds.split_folds(pool, folds=5)
ok(len(f) == 5, "the pool splits into the requested number of folds")
ok(all(len({t["session"] for t in a} & {t["session"] for t in b}) == 0
       for i, a in enumerate(f) for b in f[i + 1:]),
   "no session appears in two folds — a session spanning folds is leakage")
ok([t["session"] for t in f[0]][0] < [t["session"] for t in f[-1]][0],
   "folds are chronological, not shuffled")

wf = folds.walk_forward(pool, lambda t: True, folds=5, min_fold_trades=5)
ok(wf["folds"] == 5 and wf["measured_folds"] == 5, "every fold is measured")
ok(wf["positive_folds"] == 5 and wf["verdict"] == folds.STABLE,
   "a selector positive in every fold is STABLE")
ok(wf["worst_fold"] is not None and wf["best_fold"] is not None,
   "the worst and best folds are both reported")
ok(wf["worst_fold"]["expectancy_r"] <= wf["best_fold"]["expectancy_r"],
   "the worst fold is not better than the best")

# A selector that only works in the first half must not read as stable.
one_regime = [t for t in pool if t["regime"] == "TRENDING"] + \
    [trade(f"2026-02-{d:02d}", -0.9, exit_reason="STOP", regime="CHOP")
     for d in range(6, 11) for _ in range(10)]
wf2 = folds.walk_forward(one_regime, lambda t: True, folds=5, min_fold_trades=5)
ok(wf2["verdict"] != folds.STABLE,
   "a selector that only worked in early folds is not STABLE")
ok(wf2["positive_share"] < folds.MIN_POSITIVE_SHARE,
   "its positive share is below the stability bar")

thin = folds.walk_forward(pool[:5], lambda t: True, folds=5)
ok(thin["verdict"] == folds.THIN,
   "fewer sessions than folds returns REQUIRES_MORE_DATA, not a verdict")

cmp_ = folds.compare(pool, {"high_score": lambda t: t["trade_score"] >= 80},
                     folds=5, min_fold_trades=5)
ok("baseline" in cmp_ and cmp_["baseline"]["folds"] == 5,
   "the whole-pool baseline is always reported alongside selectors")
ok(cmp_["selectors"]["high_score"]["beats_baseline"] is True,
   "a selector keeping only the winners beats the baseline")

# --------------------------------------------------------------- §18 attribution
att = attribution.attribute(pool, lambda t: t["trade_score"] >= 80,
                            rules={"trade_score_ge_80":
                                   lambda t: t["trade_score"] >= 80})
ok(att["selected"] == 100 and att["rejected"] == 100,
   "selected and rejected cohorts are both counted")
ok(att["missed_winners"] == 0, "no winner is missed when all winners are kept")
ok(att["avoided_losers"] == 100, "every loser it refused is credited")
ok(att["avoided_loser_r"] > 0, "avoided loss is reported as a positive saving")
ok(att["selected_t1_pct"] == 100.0, "the kept arm's T1 rate is reported")

inverse = attribution.attribute(pool, lambda t: t["trade_score"] < 80)
ok(inverse["missed_winners"] == 100,
   "a selector that refuses the winners is charged for every one")
ok(inverse["verdict"] != att["verdict"],
   "refusing winners and refusing losers do not share a verdict")

reasons = attribution.rejection_reasons(
    trade("2026-03-01", -1.0, regime="CHOP", trade_score=10.0),
    {"trending": lambda t: t["regime"] == "TRENDING",
     "score": lambda t: t["trade_score"] >= 80})
ok(sorted(reasons) == ["score", "trending"],
   "every failed condition is listed, not just the first")

# --------------------------------------------------------------------- §14 capture
cap = attribution.capture([
    trade("2026-03-02", -0.6, exit_reason="STOP", mfe=0.8, mae=-1.0),
    trade("2026-03-02", 1.5, exit_reason="TARGET", mfe=2.5, mae=-0.2),
])
ok(cap["t1_pct"] == 50.0 and cap["sl_pct"] == 50.0, "T1 and SL rates are split")
ok(cap["reached_half_r_then_lost"] == 1,
   "a trade that ran +0.8R and stopped out is counted as given back")
ok(cap["capture_pct"] is not None and cap["capture_pct"] < 100.0,
   "capture is reported as a share of the excursion the book offered")

# ------------------------------------------------------------------ §16 components
underlying = trade("2026-03-03", 1.0, regime="TRENDING", htf_trend="UP",
                   htf_strength=80.0, risk_over_noise=1.5,
                   entry_trigger="PULLBACK", reward_risk=2.0)
scored = components.score_row(underlying)
ok(scored["components"]["vehicle_edge"] is None,
   "vehicle edge is NOT scored without a chain — never approximated")
ok(scored["components"]["tradability"] is None,
   "tradability is NOT scored without a real bid/ask")
ok("chain" in scored["reasons"]["tradability"],
   "the gap names the data that would fill it")
ok(scored["components"]["market_edge"] is not None
   and scored["components"]["entry_edge"] is not None
   and scored["components"]["room"] is not None,
   "the dimensions history can measure are scored")
ok(scored["components"]["data_quality"] == 60.0,
   "data quality reports 3 of 5 dimensions measurable, not a flattering 100")

comp = shadow.composite(scored["components"])
ok(comp["score"] is not None and 0.0 <= comp["score"] <= 100.0,
   "the composite is on a 0-100 scale")
ok(comp["missing"] == ["vehicle_edge", "tradability"],
   "the composite names its unmeasured dimensions")
ok(comp["weights_renormalised"] is True,
   "weights are renormalised over measured components, not filled with 50")
ok(abs(sum(comp["contributions"].values()) - comp["score"]) < 0.2,
   "the published contributions add up to the published score")
ok(shadow.composite({"market_edge": None})["score"] is None,
   "a candidate with nothing measurable gets no score at all")
full = shadow.composite({k: 100.0 for k in shadow.COMPONENTS})
ok(full["score"] == 100.0, "all components at 100 scores 100")

# --------------------------------------------------- §15 the T1 label refuses to lie
label = shadow.t1_label(91.0)
ok(label["t1_probability"] is None,
   "no probability is emitted without a calibration record")
ok(label["probability_status"] == shadow.NOT_CALIBRATED
   and label["t1_score"] == 91.0 and label["t1_rank"] == "TOP",
   "a score and a rank are published in place of the percentage")
thin_cal = shadow.t1_label(91.0, {"oos_resolved": 40, "observed_t1_pct": 90.0,
                                  "claimed_t1_pct": 91.0})
ok(thin_cal["t1_probability"] is None,
   "40 resolved outcomes is not enough to publish a probability")
wrong_cal = shadow.t1_label(91.0, {"oos_resolved": 500,
                                   "observed_t1_pct": 33.0,
                                   "claimed_t1_pct": 91.0})
ok(wrong_cal["t1_probability"] is None,
   "a claim of 91% against an observed 33% publishes nothing")
ok("33.0%" in wrong_cal["probability_note"],
   "the note states the observed rate that contradicted the claim")
good_cal = shadow.t1_label(91.0, {"oos_resolved": 500, "observed_t1_pct": 42.8,
                                  "claimed_t1_pct": 44.0})
ok(good_cal["t1_probability"] == 42.8
   and good_cal["probability_status"] == shadow.CALIBRATED,
   "a checked calibration publishes the OBSERVED rate, not the claimed one")

# -------------------------------------------------- §15 the list is capped, not filled
cards = [shadow.card({**underlying, "instrument": f"I{i}"},
                     {"market_edge": float(i * 10), "entry_edge": float(i * 10),
                      "room": float(i * 10), "data_quality": 60.0})
         for i in range(10)]
lst = shadow.shadow_list(cards, bar=75.0)
ok(len(lst["preferred"]) <= shadow.MAX_PREFERRED, "preferred rows are capped")
ok(all(c["a_plus_score"] >= 75.0 for c in lst["preferred"]),
   "no row below the bar is presented as preferred")
ok(all(c["below_bar"] for c in lst["watch"]),
   "watch rows are labelled as below the bar")
empty = shadow.shadow_list(cards, bar=99.9)
ok(empty["preferred"] == [] and empty["qualified"] == 0,
   "zero A+ candidates is a valid result — the list is not padded")
ok(empty["watch"], "the near-misses are still visible when nothing qualifies")
ok(cards[0]["basis"] == "UNDERLYING_ONLY",
   "an underlying-only card says so on its face")
ok(cards[0]["t1_probability"] is None,
   "no card carries a probability by default")

# A list ranked by a score that has not been shown to sort outcomes must say so
# on every row: "score 86.7, TOP" reads as a quality claim otherwise.
ok(lst["rank_status"] == shadow.RANK_NOT_VALIDATED
   and all(c["rank_status"] == shadow.RANK_NOT_VALIDATED
           for c in lst["preferred"] + lst["watch"]),
   "an untested ordering is stamped RANK_NOT_VALIDATED on the list and every row")
sorted_rank = shadow.shadow_list(
    cards, bar=75.0, ranking={"sorts_outcomes": True})
ok(sorted_rank["rank_status"] == shadow.RANK_SORTS_IN_SAMPLE
   and all(c["rank_status"] == shadow.RANK_SORTS_IN_SAMPLE
           for c in sorted_rank["preferred"]),
   "an ordering that sorted outcomes is marked in-sample, never as validated")
flat_rank = shadow.shadow_list(
    cards, bar=75.0,
    ranking={"sorts_outcomes": False, "top_minus_bottom_t1_pct": -1.5,
             "top_minus_bottom_expectancy_r": 0.024})
ok(flat_rank["rank_status"] == shadow.RANK_NOT_VALIDATED
   and "did NOT sort outcomes" in flat_rank["rank_note"],
   "a composite that failed the sort test says so beside its own list")

# ------------------------------------------------------- exit counterfactuals
# A loser that was up 0.8R before it stopped: an earlier 0.5R target would have
# been filled first, because a losing trade's peak precedes its exit.
loser_that_ran = trade("2026-03-01", -1.0, exit_reason="STOP", mfe=0.8, mae=-1.0)
r_val, why = exits.target_at(loser_that_ran, 0.5)
ok(r_val == 0.5 and "reached first" in why,
   "a 0.5R target is credited to a loser that was 0.8R up before stopping")
ok(exits.target_at(loser_that_ran, 1.5)[0] == -1.0,
   "a target the trade never reached leaves the loss untouched")
ok(exits.breakeven_after(loser_that_ran, 0.5)[0] == 0.0,
   "a breakeven stop scratches a loser that reached the trigger")
ok(exits.breakeven_after(loser_that_ran, 0.9)[0] == -1.0,
   "a breakeven trigger the trade never reached does not rescue the loss")
winner = trade("2026-03-01", 2.0, exit_reason="TARGET", mfe=2.0, mae=-0.2)
ok(exits.breakeven_after(winner, 0.5)[0] == 2.0,
   "a breakeven stop never changes a trade that already won")
ok(exits.partial_at(winner, 1.0)[0] == 1.5,
   "banking half at 1R on a 2R winner books 1.5R, not 2R")
ok(exits.partial_at(loser_that_ran, 1.0)[0] == -1.0,
   "nothing is banked when the trade never reached the partial level")
ok(exits.trailing_stop(winner, 0.5)[0] is None
   and exits.REQUIRES_PATH_DATA in exits.trailing_stop(winner, 0.5)[1],
   "a trailing stop is refused rather than guessed without the per-bar path")
ok(exits.baseline({"r": 1.0})[0] is None,
   "a row without excursions cannot be re-priced and is not assumed flat")

ex_pool = []
for day in range(1, 11):
    s = f"2026-04-{day:02d}"
    for i in range(20):
        # Every loser peaks at +0.6R first, so an earlier target must help and
        # the walk-forward must see it in every fold.
        ex_pool.append(trade(s, 1.0 if i % 2 else -1.0,
                             exit_reason="TARGET" if i % 2 else "STOP",
                             mfe=1.0 if i % 2 else 0.6,
                             mae=-0.2 if i % 2 else -1.0))
ev = exits.evaluate(ex_pool, folds=5, min_fold_trades=10)
ok(ev["variants"]["as_traded"]["expectancy_r"] == 0.0,
   "the as-traded arm re-prices to the book's own expectancy")
ok(ev["variants"]["target_at_0.5R"]["beats_as_traded"]
   and ev["variants"]["target_at_0.5R"]["verdict"] == folds.STABLE,
   "an exit that helps in every fold is reported as stable and better")
ok(ev["variants"]["trail_giveback_0.5R"]["priced"] == 0,
   "an unpriceable exit reports zero priced rows rather than a fabricated one")
ok(ev["basis"] == "UNDERLYING_ONLY" and "gross" in ev["cost_warning"],
   "the exit study states it is gross, so no variant reads as profitable")
ok(all(row["exit_reason"] in ("TARGET", "STOP", "SCRATCH")
       for row in exits.reprice(ex_pool, exits.baseline)["rows"]),
   "re-priced rows carry their own exit reason, not the original one")

# ------------------------------------------------- per-bar path exit simulation
# The case the excursion estimate cannot see and the reason path capture exists:
# a WINNER that went +0.6R, came back to entry, then ran to its target. A
# breakeven stop scratches it at 0R; the estimate leaves it at its full win.
entry, risk = 100.0, 10.0
plan = {"side": "LONG", "entry": entry, "risk": risk, "stop": 90.0,
        "target": 115.0, "r": 1.5, "mfe_r": 1.5, "mae_r": -0.1}
bars = [
    bar_at(1, high=106.0, low=100.5),   # +0.6R
    bar_at(2, high=101.0, low=99.9),    # back to entry
    bar_at(3, high=115.0, low=101.0),   # then runs to target
]
st = paths.start(plan)
for b in bars:
    paths.step(st, plan, b)
paths.finish(st, plan)
alt = plan["alt_exits"]
ok(alt["breakeven_after_0.5R"] == 0.0,
   "a winner that retouched entry after +0.5R is scratched, not credited")
ok(exits.breakeven_after(plan, 0.5)[0] == 1.5,
   "the excursion estimate cannot see that scratch — which is the upward bias")
ok(plan["path_facts"]["retouched_entry_after"]["0.5R"] is True,
   "the row records that entry was retouched after the trigger")
ok(alt["target_at_0.5R"] == 0.5 and alt["target_at_1.5R"] == 1.5,
   "an earlier target is filled at its level on the bar that reached it")
ok(alt["partial_half_at_1.0R"] == 1.25,
   "half banked at 1R and half riding to the actual 1.5R books 1.25R")
ok(alt["trail_giveback_0.5R"] == 0.1,
   "the trail exits 0.5R under the peak as it stood, never tighter than entry")
ok(plan["alt_exits_basis"] == paths.PATH_MEASURED,
   "a path-simulated row says so, so the study cannot mistake it for an estimate")

# Same-bar ambiguity must be resolved against the variant, both ways.
amb = {"side": "LONG", "entry": entry, "risk": risk, "stop": 90.0,
       "target": 115.0, "r": -1.0, "mfe_r": 0.6, "mae_r": -1.0}
st2 = paths.start(amb)
paths.step(st2, amb, bar_at(1, high=106.0, low=89.0))  # +0.6R and the stop
paths.finish(st2, amb)
ok(amb["alt_exits"]["target_at_0.5R"] == -1.0,
   "a bar covering both an earlier target and the stop resolves to the stop")
ok(amb["alt_exits"]["breakeven_after_0.5R"] == -1.0,
   "a breakeven stop is not armed by a bar that also hit the stop")
ok(amb["alt_exits"]["partial_half_at_0.5R"] == -1.0,
   "no partial is banked on a bar that also hit the stop")

# A loser that ran first is the case both bases agree on.
ran = {"side": "SHORT", "entry": entry, "risk": risk, "stop": 110.0,
       "target": 85.0, "r": -1.0, "mfe_r": 0.8, "mae_r": -1.0}
st3 = paths.start(ran)
paths.step(st3, ran, bar_at(1, high=99.0, low=92.0))    # 0.8R in favour
paths.step(st3, ran, bar_at(2, high=110.0, low=99.0))   # then stopped
paths.finish(st3, ran)
ok(ran["alt_exits"]["target_at_0.5R"] == 0.5
   and exits.target_at(ran, 0.5)[0] == 0.5,
   "on a loser that ran first, the path and the estimate agree")
ok(ran["alt_exits"]["breakeven_after_0.5R"] == 0.0,
   "the breakeven stop scratches a loser that reached the trigger, short side too")

paths_pool = []
for day in range(1, 11):
    s = f"2026-05-{day:02d}"
    for i in range(20):
        row = trade(s, 1.0 if i % 2 else -1.0,
                    exit_reason="TARGET" if i % 2 else "STOP",
                    mfe=1.0 if i % 2 else 0.6,
                    mae=-0.2 if i % 2 else -1.0)
        row["alt_exits"] = {n: 0.25 for n in paths.names()}
        paths_pool.append(row)
pev = exits.evaluate(paths_pool, folds=5, min_fold_trades=10)
ok(pev["variants"]["trail_giveback_0.5R"]["basis"] == exits.PATH_MEASURED
   and pev["variants"]["trail_giveback_0.5R"]["expectancy_r"] == 0.25,
   "a trail becomes answerable once the pool carries the simulated path")
ok(pev["variants"]["as_traded"]["basis"] == exits.EXCURSION_ESTIMATE,
   "the as-traded arm is always the book's own R, never a simulated one")
ok("simulated bar by bar" in pev["assumption"],
   "a fully path-measured study states that basis instead of the estimate caveat")
half = [dict(r) for r in paths_pool]
del half[0]["alt_exits"]
ok(exits.evaluate(half, folds=5, min_fold_trades=10)["variants"]
   ["breakeven_after_0.5R"]["basis"] == exits.EXCURSION_ESTIMATE,
   "one row without a path drops the whole variant to the estimate, never mixed")

# ------------------------------------------------------------------- §17 frequency
freq = shadow.frequency(pool, lambda t: t["trade_score"] >= 80)
ok(freq["sessions"] == 10, "frequency is measured per session")
ok(freq["mean_per_day"] == 10.0, "mean per day is reported")
sparse = shadow.frequency(pool, lambda t: t["session"] == "2026-02-01")
ok(sparse["days_with_none_pct"] == 90.0,
   "sessions that produced nothing are kept in the denominator")
ok(sparse["days_with"]["0"] == 9, "the zero-candidate days are counted")

rank = shadow.rank_cohort(pool, lambda t: t["trade_score"])
ok(rank["sorts_outcomes"] is True,
   "a score that separates winners from losers is reported as sorting")
flat = shadow.rank_cohort(pool, lambda _t: 50.0)
ok(flat["sorts_outcomes"] is False,
   "a constant score is not reported as sorting outcomes")

# ------------------------------------------------------------------- §23 promotion
strong = {"trades": 400, "expectancy_r": 0.2, "profit_factor": 1.6}
base_h = {"trades": 2000, "expectancy_r": 0.01, "profit_factor": 1.02}
stable_wf = {"verdict": folds.STABLE, "positive_folds": 5, "measured_folds": 5,
             "median_oos_expectancy_r": 0.18}
clean_att = {"missed_winners": 10, "selected_t1_pct": 50.0, "selected": 400}

verdict = promotion.evaluate(name="candidate", dev=strong, holdout=strong,
                             baseline_holdout=base_h, walk_forward=stable_wf,
                             attribution_=clean_att,
                             integrity={"data_errors": 0}, costed=True)
ok(verdict["verdict"] == promotion.PRODUCTION_CANDIDATE,
   "a rule meeting every requirement is a PRODUCTION_CANDIDATE")
ok(verdict["failed_requirements"] == [], "and it fails no requirement")

gross = promotion.evaluate(name="candidate", dev=strong, holdout=strong,
                           baseline_holdout=base_h, walk_forward=stable_wf,
                           attribution_=clean_att,
                           integrity={"data_errors": 0}, costed=False)
ok(gross["verdict"] != promotion.PRODUCTION_CANDIDATE,
   "a gross-only result cannot be promoted however good it looks")
ok("cost_adjusted" in gross["failed_requirements"],
   "and the missing cost adjustment is the stated reason")

five = promotion.evaluate(name="five_winners",
                          dev={"trades": 5, "expectancy_r": 1.0,
                               "profit_factor": None},
                          holdout={"trades": 5, "expectancy_r": 1.0,
                                   "profit_factor": None},
                          baseline_holdout=base_h, walk_forward=stable_wf,
                          attribution_=clean_att,
                          integrity={"data_errors": 0}, costed=True)
ok(five["verdict"] == promotion.REQUIRES_MORE_DATA,
   "5/5 winners is REQUIRES_MORE_DATA, never a promotion")
ok("min_development_trades" in five["failed_requirements"],
   "the sample requirement is named")

unstable = promotion.evaluate(name="one_regime", dev=strong, holdout=strong,
                              baseline_holdout=base_h,
                              walk_forward={"verdict": folds.ONE_REGIME,
                                            "positive_folds": 1,
                                            "measured_folds": 5,
                                            "median_oos_expectancy_r": -0.02},
                              attribution_=clean_att,
                              integrity={"data_errors": 0}, costed=True)
ok(unstable["verdict"] == promotion.FAILS,
   "a rule that worked in one regime only FAILS the gate")

worse = promotion.evaluate(name="no_better", dev=strong,
                           holdout={"trades": 400, "expectancy_r": 0.06,
                                    "profit_factor": 1.2},
                           baseline_holdout={"trades": 2000,
                                             "expectancy_r": 0.09,
                                             "profit_factor": 1.3},
                           walk_forward=stable_wf, attribution_=clean_att,
                           integrity={"data_errors": 0}, costed=True)
ok("beats_unchanged_baseline" in worse["failed_requirements"],
   "being positive is not enough — it must beat the unchanged baseline")

corrupt = promotion.evaluate(name="dirty", dev=strong, holdout=strong,
                             baseline_holdout=base_h, walk_forward=stable_wf,
                             attribution_=clean_att,
                             integrity={"data_errors": 3}, costed=True)
ok("no_corrupted_outcome_rows" in corrupt["failed_requirements"],
   "corrupted outcome rows block promotion")

greedy = promotion.evaluate(name="refuses_most_winners", dev=strong,
                            holdout=strong, baseline_holdout=base_h,
                            walk_forward=stable_wf,
                            attribution_={"missed_winners": 900,
                                          "selected_t1_pct": 50.0,
                                          "selected": 400},
                            integrity={"data_errors": 0}, costed=True)
ok("acceptable_missed_winner_rate" in greedy["failed_requirements"],
   "a filter that refuses most of the book's winners is not promotable")

print(f"checked {checked} / OK")
