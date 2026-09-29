"""Phase 52 smoke — the properties that would make this mechanism dishonest if lost.

The load-bearing tests here are the negative ones. It is easy to produce an
expectancy; the hard part is guaranteeing that no bar after the decision touched
it, that the fill is the next bar's open rather than the trigger's close, that a
candle containing both stop and target is scored as a loss, that the cost gate
was computable before the trade, that thirty-six spellings of one event set were
corrected as one hypothesis, and that nothing in Phase 41-51 moved.

Runtime is a couple of minutes: the end-to-end section runs the real CRUDEOIL
and NIFTY study over the five-year files rather than a fixture, because a
fixture cannot fail in the ways a real file can.
"""
from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path

import numpy as np

from app.research import phase49, phase51, phase52
from app.research.phase24 import data as p24data
from app.research.phase27 import bars as p27bars
from app.research.phase50 import tiers as p50tiers
from app.research.phase52 import (
    cli,
    execute,
    levels,
    mechanism,
    metrics,
    report,
    study,
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


def prefix(series: p24data.Series, cut: int) -> p24data.Series:
    """The first ``cut`` bars of a series, as a series. Used for truncation tests."""
    out = p24data.Series.__new__(p24data.Series)
    out.instrument = series.instrument
    for field in ("ts", "open", "high", "low", "close", "volume"):
        setattr(out, field, getattr(series, field)[:cut])
    return out


# --------------------------------------------------------- §1 pre-registration
section("pre-registration and the frozen grid")
ok(phase52.MECHANISM == "GAP_FAILURE_RANGE_REENTRY_REVERSION", "one mechanism")
ok(phase52.fingerprint() == phase52.fingerprint(), "fingerprint is stable")
ok(len(phase52.fingerprint()) == 16, "fingerprint width")
ok(phase52.SOURCE == "HISTORICAL_CANDLE_DATA", "source class declared")
ok(phase52.VEHICLE == "FUTURES_ONLY", "futures only, per §12")
ok(phase52.GAP_ATR_THRESHOLDS == (0.10, 0.20, 0.30), "§13 gap thresholds")
ok(phase52.MAX_STOP_ATR == (0.50, 0.75), "§13 stop caps")
ok(phase52.COST_GATE_MULTIPLES == (3.0, 4.0, 5.0), "§13 cost gates")
ok(len(phase52.CONFIRMATIONS) == 2, "§13 two confirmations")
ok(phase52.STOP_BUFFER_ATR == 0.25, "§7 stop buffer")
ok(phase52.MAX_HOLD_MINUTES == 120, "§10 hold limit")
ok(phase52.MAX_TRADES_PER_SESSION == 1, "§11 one trade per session")
ok(phase52.WAIT_MINUTES == 15, "§4 waiting period")
ok(phase52.DECISION_TIMEFRAME_MINUTES == 5, "§1 decision timeframe")
ok(phase52.PARTITION_SHARES == (0.60, 0.20, 0.20), "§1 chronological 60/20/20")
ok(phase52.preregistration()["target"] == "PREVIOUS_DAY_CLOSE", "§8 target")

grid = phase52.variants()
ok(len(grid) == 36, "exactly 36 parameterizations, 3x2x2x3")
ok(len({v["variant_id"] for v in grid}) == 36, "variant ids are distinct")
ok({v["gap_atr"] for v in grid} == {0.10, 0.20, 0.30}, "grid covers the gaps")
ok({v["cost_gate"] for v in grid} == {3.0, 4.0, 5.0}, "grid covers the gates")
ok(len(phase52.FINAL_STATUSES) == 6 and "ROBUST_CANDIDATE" in phase52.FINAL_STATUSES,
   "six allowed statuses including COST_BLOCKED")
ok(set(phase52.EFFECT_READINGS) == {
    "DIRECTIONAL_EDGE", "COST_DRIVEN_APPEARANCE", "RECENT_PERIOD_OVERFIT",
    "NO_EDGE"}, "§15's four readings")

# A parameter added or changed after this point changes the fingerprint, which
# is the only thing making the pre-registration a commitment rather than prose.
pre = dict(phase52.preregistration())
fp_before = phase52.fingerprint()
pre["cost_gate_multiples"] = [1.0]
ok(fp_before == phase52.fingerprint(), "mutating a copy cannot move the hash")

# -------------------------------------------------- phases 41-51 untouched
section("phases 41-51 definitions unchanged")
ok(phase49.rule_fingerprint() == "c2bff87a600a0639", "phase49 grouping")
ok(p50tiers.tier_cadence_fingerprint() == "a134bdf1aec1e005", "phase50 cadence")
ok(phase51.preregistration_fingerprint() == "e8afe891d31dbbf8",
   "phase51 pre-registration")
ok(phase52.fingerprint() not in {
    "c2bff87a600a0639", "576f08bb8e04a56d", "ab7013d2824613cc",
    "e465ebf3798eff3b", "22ed249295627c2c", "8db6d29d0213b759",
    "a134bdf1aec1e005", "5bd95ad889c1a2b1", "f0a2520d1181a851",
    "e8afe891d31dbbf8",
}, "phase52 carries its own fingerprint, pooled with none of the others")
ok(phase51.MIN_TRADES == 100, "phase51's own floors are not re-tuned by phase52")

# ------------------------------------------------------------- 5-minute bars
section("completed 5-minute bars")
s1 = p24data.load_series("NIFTY")
ok(s1 is not None and len(s1) > 100_000, "NIFTY minute series loads")
s5, stats5 = p27bars.resample(s1, 5)
s15, _ = p27bars.resample(s1, 15)
ok(stats5["spans_a_session"] is False, "no 5-minute bar spans a session")
ok(stats5["timeframe_minutes"] == 5, "aggregated to five minutes")
ok(len(s5) < len(s1), "aggregation reduces the bar count")
ok(bool((s5.high >= s5.low).all()), "aggregated highs are not below lows")
ok(bool((s5.high >= np.maximum(s5.open, s5.close)).all()),
   "aggregated high dominates open and close")
ok(bool((s5.ts[1:] > s5.ts[:-1]).all()), "5-minute bars are strictly ordered")
ok(set(int(t) for t in s15.ts).issubset(set(int(t) for t in s5.ts)),
   "every 15-minute close is also a 5-minute close, so the confirmation lands "
   "on a real decision bar")

# ------------------------------------------------------------------- levels
section("previous-day levels and a trailing ATR that cannot see today")
lv = levels.build(s5)
sess = lv["session"].astype(np.int64)
bounds = levels.session_bounds(sess)
first_a, first_b = bounds[0]
ok(bool(np.isnan(lv["pdh"][first_a:first_b]).all()),
   "the first session has no previous-day high")
ok(bool(np.isnan(lv["atr"][first_a:first_b]).all()),
   "the first session has no trailing ATR")
a2, b2 = bounds[5]
prev_a, prev_b = bounds[4]
ok(abs(float(lv["pdh"][a2]) - float(s5.high[prev_a:prev_b].max())) < 1e-6,
   "PDH is the previous session's high")
ok(abs(float(lv["pdl"][a2]) - float(s5.low[prev_a:prev_b].min())) < 1e-6,
   "PDL is the previous session's low")
ok(abs(float(lv["pdc"][a2]) - float(s5.close[prev_b - 1])) < 1e-6,
   "PDC is the previous session's last close")
ok(abs(float(lv["pdr"][a2]) - (float(lv["pdh"][a2]) - float(lv["pdl"][a2]))) < 1e-6,
   "PDR is PDH minus PDL")
for key in ("pdh", "pdl", "pdc", "atr"):
    a3, b3 = bounds[40]
    vals = lv[key][a3:b3]
    ok(len(np.unique(vals)) == 1, f"{key} is constant within a session")
a4, b4 = bounds[40]
ok(float(lv["day_open"][a4]) == float(s5.open[a4]), "day open is the first open")
area = lv["open_area_high"][a4:b4]
early = lv["minutes_since_open"][a4:b4] < phase52.WAIT_MINUTES
ok(bool(np.isnan(area[early]).all()),
   "the opening area is not published to the bars that define it")
ok(bool(np.isfinite(area[~early]).all()),
   "the opening area is published to every later bar")
expected_area = float(s5.high[a4:b4][early].max())
ok(abs(float(area[~early][0]) - expected_area) < 1e-6,
   "the opening area high is the highest high of the first fifteen minutes")

# The look-ahead test that matters: recompute the levels on a prefix of the
# series and assert every value on that prefix is bit-for-bit what the whole
# series produced. A future bar leaking into a level fails here.
cut = bounds[60][1]
lv_prefix = levels.build(prefix(s5, cut))
for key in ("pdh", "pdl", "pdc", "pdr", "atr", "open_area_high",
            "open_area_low", "day_open", "session_high_to_date"):
    whole = lv[key][:cut]
    part = lv_prefix[key][:cut]
    same = np.allclose(whole, part, equal_nan=True)
    ok(bool(same), f"{key} computed on a prefix equals the whole-series value")

# ------------------------------------------------------------- partitions
section("chronological partitions")
part = levels.chronological_partitions(sess, phase52.PARTITION_SHARES)
ok(set(np.unique(part)) == set(phase52.PARTITIONS), "three partitions present")
d_days = {int(d) for d in sess[part == "DISCOVERY"]}
v_days = {int(d) for d in sess[part == "VALIDATION"]}
h_days = {int(d) for d in sess[part == "UNTOUCHED_HOLDOUT"]}
ok(not (d_days & v_days) and not (v_days & h_days) and not (d_days & h_days),
   "no session appears in two partitions")
ok(max(d_days) < min(v_days), "discovery ends before validation begins")
ok(max(v_days) < min(h_days), "validation ends before the holdout begins")
total_days = len(d_days) + len(v_days) + len(h_days)
ok(total_days == len(set(int(d) for d in sess)), "every session is partitioned")
ok(abs(len(d_days) / total_days - 0.60) < 0.01, "discovery is 60% of sessions")
ok(abs(len(h_days) / total_days - 0.20) < 0.01, "holdout is 20% of sessions")

# --------------------------------------------------------------- mechanism
section("the mechanism, detected causally")
rows5, funnel5 = mechanism.event_table(
    "NIFTY", s5, s15, lv, phase52.CONFIRM_5M)
rows15, funnel15 = mechanism.event_table(
    "NIFTY", s5, s15, lv, phase52.CONFIRM_15M)
ok(len(rows5) > 0, "the mechanism produces events on NIFTY")
ok(funnel5["sessions"] == len(bounds), "the funnel counts every session")
accounted = sum(
    funnel5[k] for k in (
        phase52.NOT_KNOWABLE, phase52.NO_GAP, phase52.NO_EXTENSION,
        phase52.NO_FAILURE_CLOSE, phase52.NO_FORWARD_WINDOW,
        phase52.STOP_TOO_WIDE, phase52.TARGET_WRONG_SIDE,
    )
) + funnel5["candidates"]
ok(accounted == funnel5["sessions"],
   "every session is either an event or a counted refusal — none is dropped")
ok(len({r["session"] for r in rows5}) == len(rows5),
   "§11 — at most one event per session")

ts_index = {int(t): i for i, t in enumerate(s5.ts)}
for r in rows5:
    i = ts_index[r["entry_ts"]]
    ok(i == r["fill_index"], "fill index matches the entry timestamp")
    ok(abs(r["entry"] - float(s5.open[i])) < 1e-9,
       "§6 — the fill is the next bar's OPEN, not the trigger's close")
    ok(int(s5.ts[r["fill_index"] - 1]) == r["trigger_ts"],
       "the fill bar is the bar immediately after the trigger")
    ok(r["minutes_since_open_at_entry"] >= phase52.WAIT_MINUTES,
       "§4 — nothing enters inside the first fifteen minutes")
    ok(r["trigger_bar"] >= r["extension_bar"],
       "§5 — the failure close comes at or after the extension")
    ok(r["gap_atr"] >= min(phase52.GAP_ATR_THRESHOLDS),
       "§3 — every event gapped past the level by at least the smallest "
       "declared threshold")
    ok(r["stop_atr_needed"] > 0, "the stop is on the losing side of the entry")
    ok(abs(r["target"] - r["pdc"]) < 1e-9, "§8 — the target is PDC and nothing else")
    if r["direction"] == mechanism.GAP_UP:
        ok(r["side"] == execute.SHORT, "§6 — an upside failure is a short")
        ok(r["day_open"] > r["pdh"], "an upside gap opens above PDH")
        ok(r["trigger_close"] < r["pdh"], "§5 — the trigger closed below PDH")
        ok(r["stop"] > r["entry"], "a short's stop is above its entry")
        ok(r["target"] < r["entry"], "a short's target is below its entry")
        ok(r["stop"] >= r["failure_extreme"], "the stop sits beyond the extreme")
    else:
        ok(r["side"] == execute.LONG, "§6 — a downside failure is a long")
        ok(r["day_open"] < r["pdl"], "a downside gap opens below PDL")
        ok(r["trigger_close"] > r["pdl"], "§5 — the trigger closed above PDL")
        ok(r["stop"] < r["entry"], "a long's stop is below its entry")
        ok(r["target"] > r["entry"], "a long's target is above its entry")
        ok(r["stop"] <= r["failure_extreme"], "the stop sits beyond the extreme")
    ok(abs(r["target_distance"] - abs(r["entry"] - r["pdc"])) < 1e-9,
       "§9 — target distance is |entry - PDC|")
    ok(r["gate_cost_points"] > 0, "the gate divides by a positive round trip")
    ok(abs(r["cost_multiple"] - r["target_distance"] / r["gate_cost_points"])
       < 1e-6, "§9 — the movement/cost multiple is distance over round trip")

# The gate's cost must be knowable at the decision bar, so it may depend on the
# entry price and nothing else. Recomputing it from the entry alone reproduces it.
for r in rows5[:20]:
    modelled = float(execute.cost_at_price("NIFTY", np.array([r["entry"]]))[0])
    ok(abs(modelled - r["gate_cost_points"]) < 1e-9,
       "the gate's cost is a function of the entry price alone")

# The 15-minute confirmation must confirm on a real 15-minute close.
ts15 = {int(t) for t in s15.ts}
for r in rows15:
    ok(r["trigger_ts"] in ts15,
       "§13 — the 15-minute confirmation triggers on a 15-minute bar's close")

# Truncating the series must not change any event that already happened.
cut_sess = bounds[500][1]
s5_cut = prefix(s5, cut_sess)
lv_cut = levels.build(s5_cut)
rows_cut, _ = mechanism.candidate_events("NIFTY", s5_cut, lv_cut, None)
whole_prefix = [r for r in rows5 if r["fill_index"] < cut_sess]
ok(len(rows_cut) == len(whole_prefix),
   "truncating the file produces the same number of past events")
same_all = all(
    abs(a["entry"] - b["entry"]) < 1e-9
    and abs(a["stop"] - b["stop"]) < 1e-9
    and abs(a["target"] - b["target"]) < 1e-9
    and a["entry_ts"] == b["entry_ts"]
    for a, b in zip(rows_cut, whole_prefix)
)
ok(same_all, "no future bar changed a past event's entry, stop or target")

# ------------------------------------------------------------------ execution
section("execution: same-bar ties, the clock, and the session close")
# A synthetic long: bar 1 contains both the stop and the target.
high = np.array([100.0, 106.0, 104.0, 104.0, 104.0, 104.0])
low = np.array([99.0, 94.0, 103.0, 103.0, 103.0, 103.0])
open_ = np.array([100.0, 100.0, 103.5, 103.5, 103.5, 103.5])
res = execute.resolve(
    "NIFTY", high, low, open_,
    np.array([1]), np.array([execute.LONG]), np.array([100.0]),
    np.array([95.0]), np.array([105.0]), np.array([5]),
)
ok(res["outcome"][0] == execute.STOP_HIT,
   "a bar containing both the stop and the target is scored as the STOP")
ok(res["gross_points"][0] == -5.0, "the tie is charged at the stop price")

# A clean target on a later bar.
high2 = np.array([100.0, 101.0, 106.0])
low2 = np.array([99.0, 99.5, 100.5])
open2 = np.array([100.0, 100.0, 101.0])
res2 = execute.resolve(
    "NIFTY", high2, low2, open2,
    np.array([1]), np.array([execute.LONG]), np.array([100.0]),
    np.array([95.0]), np.array([105.0]), np.array([2]),
)
ok(res2["outcome"][0] == execute.TARGET_HIT, "a clean target resolves as TARGET")
ok(res2["gross_points"][0] == 5.0, "a target fills at the target price")
ok(res2["net_points"][0] < res2["gross_points"][0], "cost is charged, always")
ok(res2["net_r"][0] == res2["net_points"][0] / 5.0, "R is net over risk")

# The clock: nothing may run past 24 five-minute bars.
n = 60
flat_h = np.full(n, 100.5)
flat_l = np.full(n, 99.5)
flat_o = np.full(n, 100.0)
res3 = execute.resolve(
    "NIFTY", flat_h, flat_l, flat_o,
    np.array([0]), np.array([execute.LONG]), np.array([100.0]),
    np.array([90.0]), np.array([110.0]), np.array([n - 1]),
)
ok(res3["outcome"][0] == execute.TIME_EXIT, "an unresolved trade times out")
ok(res3["exit_bar"][0] == execute.MAX_HOLD_BARS,
   "§10 — the clock expires at exactly 24 five-minute bars")
ok(res3["hold_minutes"][0] == 120.0, "§10 — 120 minutes, measured")
ok(res3["exit_price"][0] == 100.0, "a timed-out trade exits at the bar's open")

# The session close binds before the clock when the session ends first.
res4 = execute.resolve(
    "NIFTY", flat_h, flat_l, flat_o,
    np.array([0]), np.array([execute.LONG]), np.array([100.0]),
    np.array([90.0]), np.array([110.0]), np.array([6]),
)
ok(res4["exit_bar"][0] == 6,
   "a trade is flattened at the session close, never carried overnight")
ok(execute.MAX_HOLD_BARS == 24, "24 bars is 120 minutes at five minutes a bar")

# Cost must come from the shared model, not a local invention.
shared = execute.charged_cost("NIFTY", np.array([100.0]), np.array([105.0]))
ok(abs(float(res2["cost_points"][0]) - float(shared[0])) < 1e-9,
   "the charged cost is the shared Phase 19/24 futures cost model")
ok(float(execute.cost_at_price("CRUDEOIL", np.array([6000.0]))[0]) > 0,
   "the modelled round trip is positive on CRUDEOIL too")

# ---------------------------------------------------------------- metrics
section("metrics and the §15 reading")
fake = [
    {"net_r": 1.0, "net_points": 10.0, "gross_points": 12.0, "gross_r": 1.2,
     "cost_points": 2.0, "net_rupees": 10.0, "hold_minutes": 30.0,
     "target_distance": 20.0, "cost_multiple": 5.0, "risk_points": 10.0,
     "mfe_r": 1.2, "mae_r": 0.2, "stop_atr_needed": 0.4, "outcome": "TARGET",
     "session": 1, "year": 2021, "quarter": "2021Q1",
     "minutes_since_open_at_entry": 30.0},
    {"net_r": -1.0, "net_points": -10.0, "gross_points": -8.0, "gross_r": -0.8,
     "cost_points": 2.0, "net_rupees": -10.0, "hold_minutes": 60.0,
     "target_distance": 20.0, "cost_multiple": 5.0, "risk_points": 10.0,
     "mfe_r": 0.1, "mae_r": 1.0, "stop_atr_needed": 0.4, "outcome": "STOP",
     "session": 2, "year": 2022, "quarter": "2022Q1",
     "minutes_since_open_at_entry": 90.0},
]
d = metrics.describe(fake)
ok(d["trade_count"] == 2 and d["session_count"] == 2,
   "trade count equals session count, by §11 construction")
ok(d["win_rate"] == 0.5, "win rate")
ok(abs(d["net_expectancy_r"]) < 1e-12, "expectancy of a symmetric pair is zero")
ok(abs(d["profit_factor"] - 1.0) < 1e-12, "profit factor")
ok(d["largest_winner_r"] == 1.0 and d["largest_loser_r"] == -1.0, "extremes")
ok(d["average_hold_minutes"] == 45.0, "average hold")
ok(abs(d["cost_share_of_gross_pct"] - 400.0 / 20.0) < 1e-9,
   "cost share is total cost over the absolute gross move")
ok(metrics.describe([])["measured"] is False, "an empty cohort is not measured")
ok(metrics.per_period(fake, "year").keys() == {"2021", "2022"}, "per-year split")
ok(metrics.per_period(fake, "quarter").keys() == {"2021Q1", "2022Q1"},
   "per-quarter split")
ok(metrics.session_distribution(fake)["median_minutes_after_open"] == 60.0,
   "session distribution")

ok(metrics.effect_reading({"measured": True, "gross_expectancy_points": -1.0,
                           "net_expectancy_points": -2.0}, {}) == "NO_EDGE",
   "negative gross is NO_EDGE, not a cost problem")
ok(metrics.effect_reading({"measured": True, "gross_expectancy_points": 1.0,
                           "net_expectancy_points": -0.5}, {})
   == "COST_DRIVEN_APPEARANCE",
   "positive gross turned negative by cost is a cost appearance")
ok(metrics.effect_reading(
    {"measured": True, "gross_expectancy_points": 2.0,
     "net_expectancy_points": 1.0, "net_total_r": 10.0},
    {"2025": {"net_total_r": 9.0, "net_expectancy_r": 1.0}},
) == "RECENT_PERIOD_OVERFIT", "one year carrying 90% of the net is overfit")
ok(metrics.effect_reading(
    {"measured": True, "gross_expectancy_points": 2.0,
     "net_expectancy_points": 1.0, "net_total_r": 10.0},
    {"2023": {"net_total_r": 3.0, "net_expectancy_r": 1.0},
     "2024": {"net_total_r": 3.5, "net_expectancy_r": 1.0},
     "2025": {"net_total_r": 3.5, "net_expectancy_r": 1.0}},
) == "DIRECTIONAL_EDGE", "positive gross, positive net, spread across years")

# ------------------------------------------------------------ family collapse
section("§16 event families")
h1 = study._family_hash([{"entry_ts": 1}, {"entry_ts": 2}])
h2 = study._family_hash([{"entry_ts": 2}, {"entry_ts": 1}])
h3 = study._family_hash([{"entry_ts": 1}, {"entry_ts": 3}])
ok(h1 == h2, "the family hash does not depend on row order")
ok(h1 != h3, "a different event set is a different family")
ok(study._family_hash([]) == study._family_hash([]), "the empty set is stable")

# ---------------------------------------------------------------- promotion
section("§17 the promotion bar")
def _cohort(n: int, exp: float) -> dict:
    return {"measured": True, "trade_count": n, "session_count": n,
            "net_expectancy_r": exp, "profit_factor": 2.0,
            "max_drawdown_r": 2.0, "top_trade_share": 0.2,
            "p_value_one_sided": 0.001}


good_overall = {"measured": True, "profit_factor": 2.0, "max_drawdown_r": 3.0,
                "top_trade_share": 0.2, "net_total_r": 30.0,
                "trade_count": 80}
good_parts = {"DISCOVERY": _cohort(60, 0.3), "VALIDATION": _cohort(20, 0.25),
              "UNTOUCHED_HOLDOUT": _cohort(20, 0.2)}
good_years = {str(y): {"net_total_r": 6.0, "net_expectancy_r": 0.3}
              for y in range(2021, 2026)}
good_stress = {"1.0": {"net_expectancy_r": 0.3},
               "1.5": {"net_expectancy_r": 0.2},
               "2.0": {"net_expectancy_r": 0.1}}
status, reasons = study._grade(
    good_parts, good_years, good_stress, good_overall,
    fdr_pass=True, had_candidates=True)
ok(status == phase52.ROBUST_CANDIDATE and not reasons,
   "a cohort that clears every declared gate is promotable")

# Each gate, flipped one at a time, must block promotion.
def _flip(**kw) -> str:
    parts = {k: dict(v) for k, v in good_parts.items()}
    years = {k: dict(v) for k, v in good_years.items()}
    stress = {k: dict(v) for k, v in good_stress.items()}
    overall = dict(good_overall)
    fdr = kw.pop("fdr_pass", True)
    if "years" in kw:
        years = kw.pop("years")
    for key, value in kw.items():
        target, field = key.split("__", 1)
        if target == "overall":
            overall[field] = value
        elif target == "stress":
            stress[field] = value
        else:
            parts[target][field] = value
    return study._grade(parts, years, stress, overall,
                        fdr_pass=fdr, had_candidates=True)[0]


ok(_flip(DISCOVERY__trade_count=5) == phase52.PROMISING_NEEDS_DATA,
   "a thin but positive discovery is PROMISING_NEEDS_DATA, never robust")
ok(_flip(DISCOVERY__net_expectancy_r=-0.1) == phase52.REJECTED,
   "a negative discovery is rejected before anything else is looked at")
ok(_flip(VALIDATION__trade_count=2) == phase52.PROMISING_NEEDS_DATA,
   "a thin validation cannot promote")
ok(_flip(VALIDATION__net_expectancy_r=-0.1) == phase52.HISTORICAL_LEAD,
   "positive in discovery, negative forward, is a historical lead")
ok(_flip(fdr_pass=False) == phase52.OVERFIT_RISK,
   "failing the correction is OVERFIT_RISK, not a candidate")
ok(_flip(UNTOUCHED_HOLDOUT__net_expectancy_r=-0.1) == phase52.HISTORICAL_LEAD,
   "a negative untouched holdout blocks promotion")
ok(_flip(UNTOUCHED_HOLDOUT__trade_count=1) == phase52.PROMISING_NEEDS_DATA,
   "a thin holdout blocks promotion")
ok(_flip(overall__profit_factor=1.0) == phase52.OVERFIT_RISK,
   "profit factor below 1.2 blocks promotion")
ok(_flip(overall__max_drawdown_r=99.0) == phase52.OVERFIT_RISK,
   "drawdown beyond the declared bound blocks promotion")
ok(_flip(overall__top_trade_share=0.9) == phase52.OVERFIT_RISK,
   "one trade carrying the result blocks promotion")
ok(_flip(**{"stress__2.0": {"net_expectancy_r": -0.1}}) == phase52.OVERFIT_RISK,
   "a result that dies at 2x cost is a cost assumption, not an edge")
ok(_flip(years={"2025": {"net_total_r": 30.0, "net_expectancy_r": 0.3}})
   == phase52.OVERFIT_RISK,
   "one year carrying the whole result blocks promotion")
ok(study._grade(
    {p: metrics.describe([]) for p in phase52.PARTITIONS}, {}, {},
    metrics.describe([]), fdr_pass=False, had_candidates=True,
)[0] == phase52.COST_BLOCKED,
   "a variant whose gate admitted nothing is COST_BLOCKED, not REJECTED")

# ---------------------------------------------------------------- isolation
section("isolation: research only, no order path")
pkg = Path("app/research/phase52")
forbidden_modules = {
    "app.broker", "app.orders", "app.api", "app.routes", "app.live",
    "kiteconnect", "requests", "httpx", "socket", "urllib",
}
for path in sorted(pkg.glob("*.py")):
    tree = ast.parse(path.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    bad = {m for m in imported if any(
        m == f or m.startswith(f + ".") for f in forbidden_modules)}
    ok(not bad, f"{path.name} imports no order path ({bad})")
    names = {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    } | {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
    }
    ok("place_order" not in names and "place_orders" not in names,
       f"{path.name} calls no order placement")
ok("phase52" in str(report.ARTEFACT_DIR),
   "artefacts are written only under the phase's own directory")
ok(phase52.NO_ORDER_PATH.startswith("THIS_MODULE_READS_HISTORICAL_FILES"),
   "the isolation statement travels with the payload")

# -------------------------------------------------------------- end to end
section("end to end on the real five-year files")
result = study.run(("CRUDEOIL", "NIFTY"))
ok(result["fingerprint"] == phase52.fingerprint(), "the run carries the hash")
ok(len(result["instruments"]) == 2, "both instruments ran")
t = result["totals"]
ok(t["TOTAL_HYPOTHESES"] == 72, "36 parameterizations per instrument")
ok(t["UNIQUE_EVENT_FAMILIES"] <= t["TOTAL_HYPOTHESES"],
   "§16 — families never exceed parameterizations")
ok(t["ROBUST_CANDIDATES"] >= 0, "the robust count is reported")
ok(t["ROBUST_CANDIDATES"] <= t["HOLDOUT_POSITIVE"] + 0,
   "no candidate is robust without being holdout-positive")

for inst in result["instruments"]:
    ok(len(inst["rows"]) == 36, f"{inst['instrument']} ran the full grid")
    fams = {r["event_family"] for r in inst["rows"]}
    ok(len(fams) == inst["totals"]["UNIQUE_EVENT_FAMILIES"],
       f"{inst['instrument']} family count matches the distinct hashes")
    for r in inst["rows"]:
        ok(r["final_status"] in phase52.FINAL_STATUSES,
           f"{r['variant_id']} carries an allowed status")
        ok(r["effect_reading"] in phase52.EFFECT_READINGS,
           f"{r['variant_id']} carries one of the four readings")
        ok(r["trades"] == r["overall"].get("trade_count", 0),
           f"{r['variant_id']} trade count is consistent")
        if r["final_status"] != phase52.ROBUST_CANDIDATE:
            ok(bool(r["status_reasons"]),
               f"{r['variant_id']} states why it is not robust")
        parts = r["per_partition"]
        summed = sum(parts[p]["trade_count"] for p in phase52.PARTITIONS)
        ok(summed == r["trades"],
           f"{r['variant_id']} partitions sum to the trade count")
    for r in inst["rows"][:6]:
        ok(r["gate_refused"] >= 0, "the gate's refusals are counted")

# A tighter gap threshold can only ever select a subset of a looser one, which
# is the structural reason the grid is not 36 independent tests.
for inst in result["instruments"]:
    by_id = {r["variant_id"]: r for r in inst["rows"]}
    for conf in phase52.CONFIRMATIONS:
        loose = by_id[f"GAP0.10_{conf}_STOP0.75_GATE3.0"]["trades"]
        tight = by_id[f"GAP0.30_{conf}_STOP0.75_GATE3.0"]["trades"]
        ok(tight <= loose,
           f"{inst['instrument']} {conf}: a wider gap requirement cannot admit "
           "more events")
        cheap = by_id[f"GAP0.10_{conf}_STOP0.75_GATE3.0"]["trades"]
        dear = by_id[f"GAP0.10_{conf}_STOP0.75_GATE5.0"]["trades"]
        ok(dear <= cheap, "a stricter cost gate cannot admit more events")
        narrow = by_id[f"GAP0.10_{conf}_STOP0.50_GATE3.0"]["trades"]
        ok(narrow <= loose, "a tighter stop cap cannot admit more events")

# ------------------------------------------------------------------- report
section("report and CLI")
text = report.render(result)
ok("PHASE 52" in text and phase52.fingerprint() in text,
   "the report prints the fingerprint")
for key in ("TOTAL_HYPOTHESES", "UNIQUE_EVENT_FAMILIES", "DISCOVERY_LEADS",
            "VALIDATION_POSITIVE", "HOLDOUT_POSITIVE", "ROBUST_CANDIDATES"):
    ok(key in text, f"§16 totals include {key}")
ans = report.answers(result)
ok(len(ans) >= 8, "§18 — eight questions answered")
ok(any("CRUDEOIL" in q for q in ans), "CRUDEOIL is answered by name")
ok(any("NIFTY" in q for q in ans), "NIFTY is answered by name")
ok(any("holdout" in q.lower() for q in ans), "the holdout question is answered")
ok(any("cost" in q.lower() for q in ans), "the cost question is answered")
ok(all(isinstance(v, list) and v for v in ans.values()),
   "no question is answered with silence")
for note in (phase52.CANDLE_HAS_NO_BOOK, phase52.SAME_BAR_TIE_IS_A_LOSS,
             phase52.NO_OPTION_CLAIM, phase52.NOT_A_PREDICTION):
    ok(note in text, "the honesty note travels with the numbers")
if result["totals"]["ROBUST_CANDIDATES"] == 0:
    ok("ROBUST_CANDIDATES = 0" in text, "a zero is reported as a zero")

# Vocabulary: the report may not promise anything. The declared disclaimers are
# stripped first, because they necessarily contain the words being searched for.
scrubbed = text
for note in phase52.preregistration()["honesty"].values():
    scrubbed = scrubbed.replace(note, "")
scrubbed = scrubbed.replace("not a prediction", "").replace(
    "never by discovery", "")
lowered = scrubbed.lower()
for word in ("buy probability", "confidence score", "guaranteed", "winner is",
             "we recommend", "will profit", "sure thing"):
    ok(word not in lowered, f"the report does not say '{word}'")

out = cli.main_argv(["prereg"])
ok("PHASE 52 PRE-REGISTRATION" in out and phase52.fingerprint() in out,
   "cli prereg runs and prints the hash")
out = cli.main_argv(["grid"])
ok(out.count("GAP0.") == 36, "cli grid prints all 36 parameterizations")
with contextlib.redirect_stdout(io.StringIO()):
    out = cli.main_argv(["funnel", "--instrument", "NIFTY"])
ok("PHASE 52 FUNNEL" in out and "NO_GAP" in out, "cli funnel runs")
ok("--lead-conditioned" not in out, "no borrowed phase 51 switch leaked in")

print(f"\nPHASE 52 SMOKE — {PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
