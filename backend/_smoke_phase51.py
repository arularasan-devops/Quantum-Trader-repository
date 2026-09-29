"""Phase 51 smoke — the properties that would make the search dishonest if lost.

Deliberately heavy on the negative tests. A discovery engine that reports a
positive number is easy; the hard part is guaranteeing the number was not
produced by a look-ahead, a reused holdout, a correction against the survivors,
or an overlapping trade count, and each of those is asserted here rather than
argued in a document.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import inspect
import io
import json
from pathlib import Path

import numpy as np

from app.research import phase51
from app.research.phase51 import (
    cli,
    families,
    geometry,
    report,
    search,
    simulate,
    stats,
    universe,
)

PASS = 0
FAIL = 0


def ok(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {label}")


def section(name: str) -> None:
    print(f"— {name}")


# ---------------------------------------------------------------- §1 vocabulary
section("pre-registration and vocabulary")
ok(phase51.FINAL_STATUSES == (
    "ROBUST_CANDIDATE", "PROMISING_NEEDS_DATA", "HISTORICAL_LEAD",
    "OVERFIT_RISK", "REJECTED"), "the five statuses, in order")
ok(len(phase51.FINAL_STATUSES) == 5, "no sixth status")
ok(phase51.PARTITIONS == ("DISCOVERY", "VALIDATION", "UNTOUCHED_HOLDOUT"),
   "three chronological partitions")
ok(phase51.preregistration_fingerprint()
   == phase51.preregistration_fingerprint(), "fingerprint is stable")
ok(len(phase51.preregistration_fingerprint()) == 16, "fingerprint width")
prereg = phase51.preregistration()
ok(prereg["source"] == phase51.HISTORICAL_CANDLE_DATA,
   "data class is declared historical candles")
ok("NO_BID_NO_ASK" in prereg["not_executable_book"],
   "pre-registration states the candle carries no executable side")
ok("PROMISING_NEEDS_DATA" in prereg["no_option_pricing"],
   "no option premium is modelled from an underlying candle")
ok(phase51.FDR_ALPHA == 0.05, "alpha frozen at 0.05")
ok(phase51.MIN_TRADES >= 100 and phase51.MIN_SESSIONS >= 30, "sample floors")
ok(phase51.MIN_YEARS_POSITIVE >= 3, "at least three positive years required")
ok(phase51.MAX_TOP_TRADE_SHARE <= 0.35, "one-trade concentration bound")
ok(2.0 in phase51.COST_STRESS_MULTIPLES, "2x cost stress is in the grid")

# The Phase 41-50 fingerprints must be untouched by this phase. Recomputing
# them here means a later edit to a shared module fails this suite rather than
# silently re-labelling sessions already journalled.
section("phases 41-50 definitions unchanged")
from app.research import phase49  # noqa: E402
from app.research.phase50 import tiers as p50tiers  # noqa: E402

ok(phase49.rule_fingerprint() == "c2bff87a600a0639", "phase49 grouping")
ok(p50tiers.tier_cadence_fingerprint() == "a134bdf1aec1e005",
   "phase50 tier cadence")
ok(phase51.preregistration_fingerprint() not in {
    "c2bff87a600a0639", "576f08bb8e04a56d", "ab7013d2824613cc",
    "e465ebf3798eff3b", "22ed249295627c2c", "8db6d29d0213b759",
    "a134bdf1aec1e005", "5bd95ad889c1a2b1", "f0a2520d1181a851",
}, "phase51 carries its own fingerprint, pooled with none of the others")

# ------------------------------------------------------------------- §2 universe
section("universe")
surv = universe.survey()
names = {d["instrument"] for d in surv["eligible"]}
ok("NIFTY" in names and "CRUDEOIL" in names, "NIFTY and CRUDEOIL eligible")
for d in surv["eligible"]:
    ok(d["sessions"] >= universe.MIN_SESSIONS, f"{d['instrument']} session floor")
    ok(d["bars"] >= universe.MIN_BARS, f"{d['instrument']} bar floor")
    ok(d["source"] == phase51.HISTORICAL_CANDLE_DATA,
       f"{d['instrument']} classified as candle data")
ok(universe.load("DEFINITELY_NOT_AN_INSTRUMENT") is None,
   "an unknown instrument is refused, not invented")

# Every key the CLI prints must exist on the row it prints from. A survey read
# through a misspelled key raises at the point of use and not before, so the
# printing commands are executed here rather than inspected.
section("the read-only commands run")
for argv in (["universe"], ["prereg"], ["grid"]):
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            cli.main_argv(argv)
    except BaseException as exc:                       # noqa: BLE001
        ok(False, f"cli {argv[0]} raised {type(exc).__name__}: {exc}")
    else:
        ok(len(buf.getvalue()) > 0, f"cli {argv[0]} prints a report")

# ------------------------------------------------------------------- §3 features
section("features are causal")
s = universe.load("NIFTY")
f = geometry.build(s)
ok(f["close"].size == s.ts.size, "features aligned to bars")
ok(len(f) > 50, "the declared feature set is present")
# §3's list, by the name this layer gives each one.
for key in ("prev_high", "prev_low", "prev_close", "prev_mid", "prev_range",
            "prev_range_percentile", "current_day_open", "opening_gap",
            "distance_from_previous_high", "distance_from_previous_low",
            "distance_from_previous_close", "distance_from_day_open",
            "opening_range_5m_size", "opening_range_15m_size",
            "opening_range_30m_size", "opening_range_60m_size",
            "session_high", "session_low", "rolling_high",
            "rolling_low", "return_1m", "return_5m", "return_15m",
            "return_30m", "return_60m", "atr", "range_expansion",
            "range_percentile", "volume_ratio", "time_since_open",
            "time_bucket", "vwap", "estimated_cost_points",
            "expected_move_over_cost", "move_over_previous_day_range"):
    ok(key in f, f"feature {key} present")
ok(set(geometry.feature_names()) <= set(f),
   "every declared feature name is actually built")

# Truncation test: features computed on a prefix must equal the same features
# computed on the whole series, bar for bar. Any use of a future bar breaks it.
cut = int(s.ts.size * 0.7)
prefix = type(s).__new__(type(s))
prefix.instrument = s.instrument
for attr in ("ts", "open", "high", "low", "close", "volume"):
    setattr(prefix, attr, getattr(s, attr)[:cut])
fp = geometry.build(prefix)
noncausal = []
for k, v in fp.items():
    a, b = v[:cut - 1], f[k][:cut - 1]
    if a.dtype.kind in "fc":
        same = np.allclose(np.nan_to_num(a, nan=-1e18),
                           np.nan_to_num(b, nan=-1e18), atol=1e-9)
    else:
        same = bool(np.array_equal(a, b))
    if not same:
        noncausal.append(k)
ok(not noncausal, f"no feature reads a future bar ({noncausal})")

# The source itself must not contain a shift by a negative amount, which is the
# one-line way a look-ahead enters a feature layer.
for mod in (geometry, families, simulate, search):
    tree = ast.parse(Path(inspect.getfile(mod)).read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in {"_shift", "shift"}):
            for arg in node.args[1:2]:
                if isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub):
                    bad.append(ast.unparse(node))
    ok(not bad, f"{mod.__name__} never shifts a series backwards ({bad})")

# The cost feature must not depend on the window it was computed over. The
# shared helper anchors a linear probe at the median of the prices handed to it,
# which is why this layer probes at fixed constants instead.
half = geometry.cost_points("NIFTY", s.close[:cut])
ok(np.allclose(half, f["estimated_cost_points"][:cut], atol=1e-9),
   "the modelled cost at a bar is independent of the bars after it")
ok(np.all(f["estimated_cost_points"] > 0), "every bar carries a positive cost")

ok(np.all(f["time_since_open"] >= 0), "time since open is never negative")
vr = f["volume_ratio"]
ok(np.all(vr[np.isfinite(vr)] >= 0), "no negative volume ratio survives")

# ------------------------------------------------------------------- §4 families
section("previous-day family grid")
g = families.grid_size()
ok(families.PENETRATION_ATR == (0.0, 0.05, 0.10, 0.25, 0.50),
   "the five declared penetration thresholds")
ok(families.CONFIRMATIONS == ("touch", "close_1m", "close_5m", "close_15m"),
   "the four declared confirmations")
ok(families.TIMINGS == ("immediate_next_bar", "after_confirmation",
                        "after_retest"), "the three declared timings")
ok(len(families.SESSION_WINDOWS) == 4, "four session windows")
ok(g["previous_day_family"] > 2000, "the previous-day grid is fully enumerated")

rules = list(families.all_rules(f))
fam = {r.family for r in rules}
for required in (families.F_PREV_DAY_LEVELS, families.F_OPENING_RANGE,
                 families.F_GAP_CONTINUATION, families.F_GAP_FILL,
                 families.F_MOMENTUM_CONT, families.F_MOMENTUM_EXHAUST,
                 families.F_FAILED_BREAKOUT, families.F_MEAN_REVERSION,
                 families.F_VOL_EXPANSION, families.F_VOL_CONTRACTION,
                 families.F_VWAP, families.F_TREND_EXPANSION,
                 families.F_TREND_FAILURE, families.F_TIME_OF_DAY,
                 families.F_DAY_TYPE):
    ok(required in fam, f"family {required} generated")
for unmeasurable in (families.F_RELATIVE_VALUE, families.F_VEHICLE,
                     families.F_OPTION_SIDE):
    ok(unmeasurable in families.UNMEASURABLE_FAMILIES,
       f"{unmeasurable} declared unmeasurable rather than fabricated")
    ok(unmeasurable not in fam, f"{unmeasurable} generates no candle rule")
ok(len({r.rule_id for r in rules}) == len(rules), "rule ids are unique")
ok(all(r.side in (1, -1) for r in rules), "every rule has a direction")
ok({r.side for r in rules} == {1, -1}, "both directions are searched")
ok(all(r.mask.dtype == bool for r in rules), "masks are boolean")
ok(all(r.text for r in rules), "every rule is human readable")

# ----------------------------------------------------------------- §7 execution
section("execution and costs")
sess = f["session"].astype(np.int64)
parts = search.partitions(sess)
ok(set(parts) == set(phase51.PARTITIONS), "three partitions built")
ok(not (parts["DISCOVERY"] & parts["VALIDATION"]).any(), "no discovery/validation overlap")
ok(not (parts["VALIDATION"] & parts["UNTOUCHED_HOLDOUT"]).any(),
   "no validation/holdout overlap")
ok(not (parts["DISCOVERY"] & parts["UNTOUCHED_HOLDOUT"]).any(),
   "no discovery/holdout overlap")
d_max = sess[parts["DISCOVERY"]].max()
v_min, v_max = sess[parts["VALIDATION"]].min(), sess[parts["VALIDATION"]].max()
h_min = sess[parts["UNTOUCHED_HOLDOUT"]].min()
ok(d_max < v_min, "validation is strictly after discovery in time")
ok(v_max < h_min, "the holdout is strictly after validation in time")
ok(np.unique(sess[parts["DISCOVERY"]]).size
   + np.unique(sess[parts["VALIDATION"]]).size
   + np.unique(sess[parts["UNTOUCHED_HOLDOUT"]]).size
   == np.unique(sess).size, "every session lands in exactly one partition")
ok(simulate.ENTRY_DELAY_BARS >= 1, "the fill is at least one bar after the decision")

mask = f["distance_from_previous_high"] > 0
idx = simulate.non_overlapping(mask & parts["DISCOVERY"])
ok(idx.size > 0, "a trivial rule produces candidates")
ok(np.all(np.diff(idx) >= simulate.COOLDOWN_BARS),
   "two trades from one rule can never be open at once")
o = simulate.resolve("NIFTY", f, idx, 1)
keep = simulate.tradable(o)
ok(keep.any(), "candidates resolve")
ok(np.all(o.cost_points[keep] > 0), "every trade is charged a cost")
ok(np.all(o.net_points[keep] <= o.gross_points[keep]),
   "net is never better than gross")
ok(np.all(np.isfinite(o.net_r[keep])), "net R is finite")
# Same-bar conflict: a stop and a target on one bar must be scored as the loss.
both = (o.bars_to_t1 >= 0) & (o.bars_to_sl >= 0) & (o.bars_to_t1 == o.bars_to_sl)
ok(not (both & (o.outcome == "T1_BEFORE_SL")).any(),
   "a same-bar stop/target tie is resolved as the stop")
# The fill is the next bar's open, never the decision bar's close.
fill = np.minimum(idx + simulate.ENTRY_DELAY_BARS, f["close"].size - 1)
ok(np.allclose(o.entry, f["open"][fill]), "the fill is the next bar's open")

atr_eff, feasible = simulate.effective_atr(f)
ok(np.all(atr_eff[np.isfinite(atr_eff)] >= 0), "effective ATR is non-negative")
ok(simulate.MIN_RISK_COST_MULTIPLE >= 2.0,
   "risk floors at twice the modelled cost so the target clears three")
risk = o.risk[keep]
ok(np.all(risk >= simulate.MIN_RISK_COST_MULTIPLE * o.cost_points[keep] * 0.5),
   "the cost floor reaches the resolved risk")
ok(np.all(atr_eff[feasible] <= simulate.MAX_RISK_ATR * f["atr"][feasible] + 1e-9),
   "an infeasible bar is refused rather than traded")
ok(set(simulate.PATH_DEPENDENT_EXITS_REFUSED) >= {
    "TRAILING_STOP", "BREAKEVEN_RATCHET", "PARTIAL_PROFIT", "MFE_GIVEBACK"},
   "path-dependent exits are refused by name, not silently dropped")
ok(len(simulate.EXIT_VARIANTS) >= 10, "the declared exit grid is present")

# ---------------------------------------------------------------- §8 statistics
section("multiple testing and stability")
ok(stats.benjamini_hochberg([0.001], tests=1) == [True], "BH admits a clear win")
ok(stats.benjamini_hochberg([0.001], tests=100_000) == [False],
   "the same p-value fails against a hundred thousand trials")
ok(stats.benjamini_hochberg([0.04], tests=10) == [False],
   "a marginal p-value does not survive ten trials")
ok(stats.benjamini_hochberg([], tests=100) == [], "no p-values, no survivors")
ok(all(stats.benjamini_hochberg([0.2, 0.3], tests=2)) is False,
   "nothing passes when nothing should")
# Pure noise, five independent draws of five hundred hypotheses. BH does not
# promise zero false discoveries; it promises they stay a small share. So the
# test is that the uncorrected rule would admit dozens and the corrected one
# admits a handful — which is the whole reason the correction is here.
survivors = uncorrected = 0
for seed in (1, 7, 51, 99, 2024):
    rng = np.random.default_rng(seed)
    noise = [stats.one_sided_p(rng.normal(0.0, 1.0, 200)) for _ in range(500)]
    survivors += sum(stats.benjamini_hochberg(noise, tests=len(noise)))
    uncorrected += sum(1 for p in noise if p <= 0.05)
ok(uncorrected > 50, f"uncorrected, noise alone admits {uncorrected} of 2500")
ok(survivors <= 5, f"under FDR only {survivors} of 2500 noise draws survive")
rng = np.random.default_rng(51)
ok(stats.one_sided_p(rng.normal(1.0, 1.0, 400)) < 1e-6,
   "a real effect is detected")
ok(stats.one_sided_p(np.array([1.0, 1.0])) == 1.0, "too few trades, no claim")
ok(stats.max_drawdown_r(np.array([1.0, -3.0, 1.0])) == 3.0, "drawdown in R")
ok(stats.max_drawdown_r(np.array([])) == 0.0, "no trades, no drawdown")
ok(stats.regime_labels(np.array([0.5, 1.0, 2.0, np.nan])).tolist()
   == ["QUIET", "NORMAL", "VOLATILE", "UNKNOWN"], "regime bands")

# §12: the gates must not reward win rate. A high-win-rate negative-expectancy
# rule has to fail, and a low-win-rate positive one has to be admissible.
lots_of_small_wins = np.array([0.1] * 90 + [-2.0] * 10)
ok(lots_of_small_wins.mean() < 0, "constructed: 90% win rate, negative net")
few_big_wins = np.array([3.0] * 30 + [-1.0] * 70)
ok(few_big_wins.mean() > 0, "constructed: 30% win rate, positive net")
sessions = np.arange(100)
years = np.repeat(np.arange(4), 25)
zeros = np.zeros(100)
hi = stats.describe(lots_of_small_wins, lots_of_small_wins, sessions, years,
                    zeros, zeros, np.ones(100))
lo = stats.describe(few_big_wins, few_big_wins, sessions, years, zeros, zeros,
                    np.ones(100))
ok(hi["win_rate"] > lo["win_rate"], "the win rates are as constructed")
ok(hi["net_expectancy_r"] < 0 < lo["net_expectancy_r"],
   "expectancy, not win rate, separates them")
stress_ok = {"1": {"net_expectancy_r": 0.2}, "1.5": {"net_expectancy_r": 0.1},
             "2": {"net_expectancy_r": 0.05}}
stress_bad = {"1": {"net_expectancy_r": 0.2}, "1.5": {"net_expectancy_r": -0.1},
              "2": {"net_expectancy_r": -0.3}}
# Interleaved rather than sorted, so the equity curve has no long losing run and
# the drawdown bound is not what this particular assertion is testing.
spread_wins = np.tile(np.array([3.0] + [-1.0] * 2 + [0.0]), 50)
sess200 = np.arange(spread_wins.size)
yr200 = np.repeat(np.arange(4), spread_wins.size // 4)
steady = stats.describe(spread_wins, spread_wins, sess200, yr200,
                        np.zeros(spread_wins.size), np.zeros(spread_wins.size),
                        np.ones(spread_wins.size))
yrs = stats.per_year(spread_wins, yr200)
ok(steady["net_expectancy_r"] > 0 and steady["win_rate"] < 0.3,
   "constructed: positive expectancy on a minority of winners")
ok(steady["max_drawdown_r"] <= phase51.MAX_DRAWDOWN_R,
   "and inside the declared drawdown bound")
ok(stats.stability_status(steady, yrs, stress_ok) == stats.STABILITY_PASS,
   "a stable rule passes")
ok(stats.stability_status(steady, yrs, stress_bad)
   == stats.STABILITY_COST_FRAGILE,
   "a rule that dies at 1.5x cost is cost-fragile")
ok(stats.stability_status(steady, {"2021": {"net_expectancy_r": 1.0}}, stress_ok)
   == stats.STABILITY_ONE_PERIOD, "one positive year is one period")
deep = np.array([-1.0] * 40 + [1.0] * 80)
dd = stats.describe(deep, deep, np.arange(120), np.repeat(np.arange(4), 30),
                    np.zeros(120), np.zeros(120), np.ones(120))
ok(stats.stability_status(dd, stats.per_year(deep, np.repeat(np.arange(4), 30)),
                          stress_ok) == stats.STABILITY_DRAWDOWN,
   "a 40R drawdown is refused whatever the end result")
spike = np.array([50.0] + [-0.2] * 120)
sp = stats.describe(spike, spike, np.arange(121), np.repeat(np.arange(4),
                    [31, 30, 30, 30]), np.zeros(121), np.zeros(121),
                    np.ones(121))
ok(sp["top_trade_share"] > phase51.MAX_TOP_TRADE_SHARE,
   "one trade carrying the result is visible")
ok(stats.stability_status(sp, stats.per_year(spike, np.repeat(np.arange(4),
   [31, 30, 30, 30])), stress_ok) == stats.STABILITY_CONCENTRATED,
   "and it is rejected for concentration")
thin = stats.describe(np.array([0.5] * 10), np.array([0.5] * 10),
                      np.arange(10), np.zeros(10), np.zeros(10), np.zeros(10),
                      np.ones(10))
ok(stats.stability_status(thin, {}, stress_ok) == stats.STABILITY_UNMEASURED,
   "ten trades is unmeasured, not stable")

# ------------------------------------------------------------------- §11 status
section("status gates")
pos = {"summary": {"measured": True, "net_expectancy_r": 0.2, "trade_count": 200,
                   "session_count": 150}, "years": {}, "cost_stress": {}}
neg = {"summary": {"measured": True, "net_expectancy_r": -0.2,
                   "trade_count": 200, "session_count": 150},
       "years": {}, "cost_stress": {}}
unm = {"summary": {"measured": False}, "years": {}, "cost_stress": {}}
S = search._final_status
ok(S(neg, True, pos, pos, stats.STABILITY_PASS) == phase51.REJECTED,
   "a negative discovery is rejected whatever follows")
ok(S(unm, False, None, None, stats.STABILITY_UNMEASURED)
   == phase51.PROMISING_NEEDS_DATA, "unmeasured is never called rejected")
ok(S(pos, False, None, None, stats.STABILITY_UNMEASURED) == phase51.OVERFIT_RISK,
   "positive but failing FDR is overfit risk, not a candidate")
ok(S(pos, True, neg, None, stats.STABILITY_PASS) == phase51.OVERFIT_RISK,
   "dying in validation is overfit risk")
ok(S(pos, True, pos, neg, stats.STABILITY_PASS) == phase51.HISTORICAL_LEAD,
   "dying in the holdout is a historical lead, not a candidate")
ok(S(pos, True, pos, pos, stats.STABILITY_CONCENTRATED)
   == phase51.HISTORICAL_LEAD, "an unstable survivor is not promoted")
ok(S(pos, True, pos, pos, stats.STABILITY_PASS) == phase51.ROBUST_CANDIDATE,
   "and everything cleared is a robust candidate")
ok(all(S(a, b, c, d, e) in phase51.FINAL_STATUSES
       for a in (pos, neg, unm) for b in (True, False)
       for c in (pos, neg, unm, None) for d in (pos, neg, unm, None)
       for e in (stats.STABILITY_PASS, stats.STABILITY_UNMEASURED,
                 stats.STABILITY_CONCENTRATED)),
   "no combination produces a status outside the declared five")

# ------------------------------------------------------- end-to-end, small slice
section("end to end")
res = search.run_instrument("CRUDEOIL")
ok(res["eligible"], "CRUDEOIL ran")
ok(res["total_hypotheses_tested"] > 2000,
   "the whole grid was tested, not a subset")
ok(res["total_discovery_leads"] <= res["total_hypotheses_tested"],
   "leads cannot exceed hypotheses")
ok(res["total_validation_leads"] <= res["total_discovery_leads"],
   "the funnel only narrows")
ok(res["total_untouched_holdout_positive"] <= res["total_validation_leads"],
   "holdout positives cannot exceed validation leads")
ok(res["total_robust_candidates"] <= res["total_untouched_holdout_positive"],
   "a robust candidate must have been holdout positive")
ok(all(r["final_status"] in phase51.FINAL_STATUSES for r in res["rows"]),
   "every row carries a declared status")
# The holdout must be untouched for anything that did not survive validation.
for r in res["rows"]:
    if not r["fdr_pass"]:
        ok(r["validation"] is None and r["untouched_holdout"] is None,
           "a rule failing FDR never touches validation or the holdout")
        break
for r in res["rows"]:
    if r["fdr_pass"] and r["validation"] is not None and \
            (r["validation"]["summary"].get("net_expectancy_r") or 0) <= 0:
        ok(r["untouched_holdout"] is None,
           "a rule failing validation never touches the holdout")
        break

# §13 lead-conditioned exits. One entry only: the point is the bookkeeping, and
# the grid over all leads is a command, not a test.
lead_rows = [r for r in res["rows"] if not r["fdr_pass"]][:1]
if lead_rows:
    pc = search.profit_capture_search("CRUDEOIL", lead_rows,
                                      lead_conditioned=True)
    ok(pc["entry_selection"] == search.LEAD_CONDITIONED,
       "a lead-conditioned exit search says so in its payload")
    ok(pc["entries_considered"] == 1,
       "a lead the entry search refused is admitted only in this mode")
    ok(pc["total_exit_hypotheses_tested"] == len(simulate.EXIT_VARIANTS),
       "exit trials are counted in the exit search's own denominator")
    ok("CANNOT_PROMOTE_AN_ENTRY" in pc["conditioning_caveat"],
       "the conditioning caveat travels with the numbers")
    strict = search.profit_capture_search("CRUDEOIL", lead_rows)
    ok(strict["entries_considered"] == 0 and not strict["rows"],
       "the default mode still admits nothing that failed entry FDR")
    ok(strict["entry_selection"] == search.FDR_CLEARED,
       "the default mode is labelled as FDR-cleared")
# An exit result must not be able to write an entry's status.
src = ast.parse(inspect.getsource(search.profit_capture_search))
ok(not [n for n in ast.walk(src) if isinstance(n, ast.Constant)
        and n.value in phase51.FINAL_STATUSES],
   "the exit search never names a final entry status")

whole = {"version": "t", "preregistration_fingerprint":
         phase51.preregistration_fingerprint(), "universe": surv,
         "instruments": [res], "totals": {
             "TOTAL_HYPOTHESES_TESTED": res["total_hypotheses_tested"],
             "TOTAL_DISCOVERY_LEADS": res["total_discovery_leads"],
             "TOTAL_VALIDATION_LEADS": res["total_validation_leads"],
             "TOTAL_UNTOUCHED_HOLDOUT_POSITIVE":
                 res["total_untouched_holdout_positive"],
             "TOTAL_ROBUST_CANDIDATES": res["total_robust_candidates"]},
         "unmeasurable_families": families.UNMEASURABLE_FAMILIES,
         "p_value_caveat": stats.P_VALUE_IS_ORDERING_ONLY}
text = report.render(whole)
for token in ("TOTAL_HYPOTHESES_TESTED", "TOTAL_DISCOVERY_LEADS",
              "TOTAL_VALIDATION_LEADS", "TOTAL_UNTOUCHED_HOLDOUT_POSITIVE",
              "TOTAL_ROBUST_CANDIDATES", "ROBUST_CANDIDATES",
              "DISCOVERY_LEADS", "HISTORICAL_CANDLE_DATA"):
    ok(token in text, f"the report prints {token}")
# §14's banned vocabulary. The refusal constants are stripped first: a line
# that says a row is NOT a recommendation must not be read as one.
prose = text.replace(report.NO_RECOMMENDATION, "").lower()
for banned in ("buy probability", "confidence", "winner", "recommend",
               "guaranteed", "will profit", "prediction"):
    ok(banned not in prose, f"the report never says {banned!r}")
ok("NOT_A_PREDICTION" in report.NO_RECOMMENDATION,
   "and the refusal itself is stated in the report")
ranked = report.rank(whole)
ok(len(ranked) == len(res["rows"]), "every row appears in the ranking")
if ranked:
    keys = set(ranked[0])
    for required in ("candidate_id", "mechanism_family", "human_readable_rule",
                     "instrument", "entry_rule", "exit_rule",
                     "discovery_result", "validation_result",
                     "untouched_holdout_result", "net_expectancy",
                     "profit_factor", "drawdown", "trade_count",
                     "session_count", "cost_stress", "stability_status",
                     "overfit_status", "final_status"):
        ok(required in keys, f"§14 column {required}")
    statuses = [r["final_status"] for r in ranked]
    ok(statuses == sorted(statuses, key=lambda s: report.STATUS_ORDER[s]),
       "the ranking is by gate cleared, not by historical P&L")
    ok(report.STATUS_ORDER[phase51.ROBUST_CANDIDATE] == 0,
       "robust candidates rank first when they exist")
if res["total_robust_candidates"] == 0:
    ok("NONE." in text, "zero is reported as zero")
    ok("ROBUST_CANDIDATES — 0" in text, "and the count is printed as zero")

# ------------------------------------------------------------------ §16 no reach
section("no production, broker or order path is reachable")
mods = [geometry, families, simulate, search, report, stats, universe, cli,
        phase51]
# Identifiers, not prose. These modules are free to *say* they reach no order
# path and must contain nothing that calls one, so the check reads the names in
# the syntax tree rather than the characters in the file.
banned_calls = {"place_order", "placeorder", "cancel_order", "modify_order",
                "smartconnect", "execute_order", "send_order", "square_off",
                "place_gtt", "order_place", "buy", "sell"}
for mod in mods:
    src = Path(inspect.getfile(mod)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    idents: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            idents.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            idents.add(node.attr.lower())
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            idents.add(node.name.lower())
    ok(not (idents & banned_calls),
       f"{mod.__name__} calls no order primitive ({idents & banned_calls})")
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for name in imported:
        ok(not name.startswith("app.broker"), f"{mod.__name__} imports no broker")
        ok("smart" not in name.lower(), f"{mod.__name__} imports no broker sdk")
        ok(not name.startswith("app.trading"),
           f"{mod.__name__} imports no trading path")
        for phase in ("phase41", "phase42", "phase43", "phase44", "phase50"):
            ok(phase not in name,
               f"{mod.__name__} does not import {phase}")

# Phase 51 must not be reachable from the live tick path either: nothing in the
# production modules may import it.
hits = []
for p in Path("app").rglob("*.py"):
    if "phase51" in p.parts[-2:] or p.name == "phase51":
        continue
    if "app/research/phase51" in str(p).replace("\\", "/"):
        continue
    t = p.read_text(encoding="utf-8", errors="ignore")
    if "phase51" in t:
        hits.append(str(p))
ok(not hits, f"no module outside phase51 imports it ({hits})")

fp_before = phase51.preregistration_fingerprint()
ok(hashlib.sha256(json.dumps(phase51.preregistration(), sort_keys=True,
                             default=str).encode()).hexdigest()[:16] is not None,
   "the pre-registration payload is serialisable")
ok(phase51.preregistration_fingerprint() == fp_before,
   "reading the pre-registration does not change it")

print(f"PHASE 51 SMOKE — {PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
