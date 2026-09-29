"""Phase 32 smoke — the properties that stop a reachable T1 from being a fake one.

Each check exists because the opposite mistake makes a target look attainable
when it is not: filling at the decision bar's own price, letting a window cross an
overnight gap, resolving a same-minute T1-and-stop tie in favour of the target,
choosing a T1 with the holdout visible, selecting on a window with too few trades,
comparing configurations measured on different samples, quoting a derived premium
percentage as a measured one, or claiming a verdict for an instrument that only
has a few weeks of candles.

The fixture series has an exactly known forward path, so the arithmetic is checked
against numbers computed by hand.

    .venv/bin/python _smoke_phase32.py
"""
from __future__ import annotations

import json
import os
import tempfile

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase31 import premium_map
from app.research.phase32 import (
    BASES,
    CAPS,
    COST_STRESS,
    DELTA_BANDS,
    DERIVED_PREMIUM,
    DEV_FRACTION,
    MEASURED_UNDERLYING,
    MIN_BARS_FOR_SPLIT,
    MIN_TRADES_PER_WINDOW,
    NO_REACHABLE_T1,
    REJECTED,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD,
    SL_MULTIPLES,
    T1_GRID,
    VAL_FRACTION,
    VALIDATED,
    premium,
    reach,
    report,
    run as runner,
    t1 as t1mod,
    universe,
)

PASS = 0
FAIL: list[str] = []

DAY = 86_400
IST_OFFSET = 19_800
OPEN_IST = 9 * 3_600 + 15 * 60
INSTRUMENT = "NIFTY"
BARS_PER_SESSION = 130


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"  FAILED: {label}")


def series_file(path: str, *, sessions: int = 6) -> None:
    """A rising fixture: +1.0 per bar with a 0.5 wick each way, sessions restart."""
    base_day = 1_780_000_000 // DAY * DAY
    with open(path, "w", encoding="utf-8") as fh:
        for d in range(sessions):
            start = base_day + d * DAY + OPEN_IST - IST_OFFSET
            for m in range(BARS_PER_SESSION):
                o = 1_000.0 + m
                fh.write(json.dumps({
                    "time": start + m * 60,
                    "open": o,
                    "high": o + 0.5,
                    "low": o - 0.5,
                    "close": o + 0.4,
                    "volume": 1_000.0,
                }) + "\n")


def load_fixture(path: str):
    original = dict(p24data.BACKTEST_FILES)
    p24data.BACKTEST_FILES.clear()
    p24data.BACKTEST_FILES[INSTRUMENT] = path
    try:
        return p24data.load_series(INSTRUMENT)
    finally:
        p24data.BACKTEST_FILES.clear()
        p24data.BACKTEST_FILES.update(original)


def hand_reach(first_fav: list[int], first_adv: list[int], ret: float) -> reach.Reach:
    """A Reach built by hand, so the tie and timeout rules can be checked exactly."""
    n = len(first_fav)
    r = reach.Reach("FIXTURE", reach.LONG)
    nd = len(reach.DISTANCES)
    r.first_fav = np.zeros((nd, n), dtype=np.int16)
    r.first_adv = np.zeros((nd, n), dtype=np.int16)
    for g in range(nd):
        r.first_fav[g] = np.array(first_fav, dtype=np.int16)
        r.first_adv[g] = np.array(first_adv, dtype=np.int16)
    r.entry = np.full(n, 1_000.0)
    r.ret = {cap: np.full(n, ret) for cap in CAPS}
    r.session = np.zeros(n, dtype=np.int64)
    r.minute = np.arange(n)
    r.bars_available = np.full(n, reach.MAX_CAP)
    r.eligible = np.ones(n, dtype=bool)
    return r


def main() -> int:  # noqa: PLR0915 - one linear checklist, deliberately flat
    global PASS

    # --------------------------------------------------------- frozen constants
    ok(set(BASES) == {MEASURED_UNDERLYING, DERIVED_PREMIUM, REQUIRES_MORE_DATA},
       "every result carries one of the three declared evidence bases")
    ok(tuple(sorted(T1_GRID)) == tuple(T1_GRID) and T1_GRID[0] > 0,
       "the T1 grid is positive and ordered, frozen before any outcome")
    ok(all(m >= 1.0 for m in SL_MULTIPLES),
       "no stop is tighter than its own T1, which would be a hidden reward/risk "
       "advantage rather than a stop choice")
    ok(tuple(sorted(CAPS)) == tuple(CAPS) and CAPS[0] > 0,
       "the holding caps are positive and ordered")
    ok(abs(DEV_FRACTION + VAL_FRACTION - 0.80) < 1e-12,
       "the untouched holdout is exactly the last 20% of sessions")
    ok(COST_STRESS[0] == 1.0 and max(COST_STRESS) >= 2.0,
       "cost stress starts at the modelled cost and reaches at least twice it")
    ok(MIN_TRADES_PER_WINDOW > 0 and MIN_BARS_FOR_SPLIT >= 100_000,
       "a window needs a stated minimum sample and a split needs real history")
    ok(len(t1mod.configs()) == len(T1_GRID) * len(SL_MULTIPLES) * len(CAPS),
       "the counted hypothesis grid is exactly the frozen product, no more")

    # ------------------------------------------------------- distance addressing
    for t in T1_GRID:
        ok(round(t, 6) in reach.DISTANCES, f"T1 {t}% is an addressable distance")
        for m in SL_MULTIPLES:
            ok(round(t * m, 6) in reach.DISTANCES,
               f"the stop at {m}x{t}% is an addressable distance")
    r_probe = hand_reach([1], [0], 0.0)
    try:
        r_probe.index_of(0.123456)
        ok(False, "an off-grid distance is refused rather than interpolated")
    except KeyError:
        ok(True, "an off-grid distance is refused rather than interpolated")

    # ------------------------------------------------------- tie and timeout rules
    # row 0: T1 and stop in the same minute; row 1: T1 first; row 2: stop first;
    # row 3: neither inside the cap; row 4: T1 only after the shortest cap.
    r = hand_reach([7, 3, 9, 0, 20], [7, 9, 3, 0, 0], ret=-0.02)
    d = T1_GRID[1]
    code, gross = reach.resolve(r, d, d, 15, np.ones(5, dtype=bool))
    ok(code[0] == reach.SL_FIRST and gross[0] == -d,
       "a minute containing both T1 and the stop resolves as the stop, because a "
       "one-minute bar cannot say which came first")
    ok(code[1] == reach.T1_FIRST and gross[1] == d,
       "T1 strictly earlier than the stop resolves as T1")
    ok(code[2] == reach.SL_FIRST and gross[2] == -d,
       "the stop strictly earlier than T1 resolves as the stop")
    ok(code[3] == reach.TIMEOUT and gross[3] == -0.02,
       "neither touched by the cap closes at that bar's close, not held on")
    ok(code[4] == reach.TIMEOUT,
       "a touch after the cap does not count for that cap")
    code2, _ = reach.resolve(r, d, d, 30, np.ones(5, dtype=bool))
    ok(code2[4] == reach.T1_FIRST,
       "the same touch does count for a cap long enough to contain it")

    with tempfile.TemporaryDirectory() as tmp:
        fp = os.path.join(tmp, "NIFTY_ONE_MINUTE.jsonl")
        series_file(fp)
        s = load_fixture(fp)
        ok(s is not None and len(s) == 6 * BARS_PER_SESSION,
           "the fixture series loads with every bar")

        rl = reach.build(s, reach.LONG)
        rs = reach.build(s, reach.SHORT)

        i = int(np.flatnonzero(rl.eligible)[0])
        ok(rl.entry[i] == s.open[i + 1],
           "the fill is the next bar's open, never the decision bar's own price")
        ok(not np.isfinite(rl.entry[len(s) - 1]),
           "the last bar has no fill and is not eligible")
        ok(bool(np.all(rl.bars_available[rl.eligible] >= reach.MAX_CAP)),
           "eligibility requires the longest cap's window, so every cap is "
           "measured on the identical sample")
        ok(int(rl.eligible.sum()) == int(rs.eligible.sum()),
           "LONG and SHORT are measured on the same eligible sample")

        # In the fixture high[i+1+k] = entry + k + 0.5, so the first minute at
        # which a distance is touched is arithmetic.
        entry = float(rl.entry[i])
        for dist in (0.10, 0.20):
            want = int(np.ceil(dist / 100.0 * entry - 0.5))
            got = int(rl.first_fav[rl.index_of(dist)][i])
            ok(got == want,
               f"{dist}% is first touched at minute {want} by hand, and the "
               f"measurement says {got}")

        ok(int(rl.first_adv[rl.index_of(1.00)][i]) == 0,
           "a distance never touched against reads 0, not a silent hit")
        ok(rl.ret[60][i] > 0 and rs.ret[60][i] < 0,
           "the cap return is signed by side: a rising fixture pays LONG and "
           "costs SHORT")

        # A window must not cross a session boundary.
        last = int(np.flatnonzero(rl.eligible)[-1])
        ok(rl.session[last] == rl.session[last + reach.MAX_CAP],
           "the last eligible bar's whole window is inside its own session, so "
           "no overnight gap is counted as an intraday move")

        cost = reach.cost_pct(INSTRUMENT, rl.entry)
        ok(np.isfinite(cost[i]) and cost[i] > 0,
           "the round-trip cost comes from the shared cost model and is positive")
        zero = np.zeros_like(cost)
        with_cost = t1mod.evaluate(rl, cost, 0.20, 0.20, 60, rl.eligible)
        without = t1mod.evaluate(rl, zero, 0.20, 0.20, 60, rl.eligible)
        ok(with_cost["net_expectancy_pct"] < without["net_expectancy_pct"],
           "charging cost makes the result worse, both ways, never better")
        ok(without["net_expectancy_pct"] == without["gross_expectancy_pct"],
           "with no cost charged, net equals gross exactly")
        ok(abs(with_cost["net_expectancy_r"] * 0.20
               - with_cost["net_expectancy_pct"]) < 1e-6,
           "net R is the net percentage divided by the stop distance")
        ok(abs(with_cost["t1_first_pct"] + with_cost["sl_first_pct"]
               + with_cost["timeout_pct"] - 100.0) < 1e-6,
           "every resolved trade is exactly one of T1-first, stop-first or timeout")

        # ------------------------------------------------------ chronological split
        w = t1mod.windows(rl)
        ok(not bool((w[t1mod.DEV] & w[t1mod.VAL]).any())
           and not bool((w[t1mod.VAL] & w[t1mod.HOLDOUT]).any())
           and not bool((w[t1mod.DEV] & w[t1mod.HOLDOUT]).any()),
           "the three windows are disjoint")
        union = w[t1mod.DEV] | w[t1mod.VAL] | w[t1mod.HOLDOUT]
        ok(bool(np.array_equal(union, rl.eligible)),
           "every eligible row belongs to exactly one window; none is discarded")
        dev_sess = set(np.unique(rl.session[w[t1mod.DEV]]).tolist())
        hold_sess = set(np.unique(rl.session[w[t1mod.HOLDOUT]]).tolist())
        ok(not (dev_sess & hold_sess),
           "no session appears in both development and holdout")
        ok(max(dev_sess) < min(hold_sess),
           "the holdout is strictly later in time than development, never sampled")

        # --------------------------------------------- the sample bar has real teeth
        study = t1mod.study_instrument(rl, cost)
        ok(study["hypotheses"] == len(t1mod.configs()),
           "the study counts every configuration it looked at")
        ok(study["window_trades"][t1mod.DEV] < MIN_TRADES_PER_WINDOW,
           "the fixture's development window is deliberately below the minimum")
        ok(study["verdict"] == NO_REACHABLE_T1 and study["selected"] is None,
           "with too few development trades nothing is selected, even though the "
           "fixture rises every single bar")
        ok(all("trades" in row[name]
               for row in study["configs"] for name in t1mod.WINDOWS),
           "every configuration reports its own trade count in every window")
        ok(len({(row["t1_pct"], row["sl_pct"], row["cap_min"])
                for row in study["configs"]}) == len(study["configs"]),
           "no configuration is evaluated twice, which would inflate the count")

        # Selection, on a fixture large enough to pass the bar, must use DEV only.
        saved = t1mod.MIN_TRADES_PER_WINDOW
        try:
            t1mod.MIN_TRADES_PER_WINDOW = 1
            study2 = t1mod.study_instrument(rl, cost)
        finally:
            t1mod.MIN_TRADES_PER_WINDOW = saved
        sel = study2.get("selected")
        ok(sel is not None and sel["chosen_on"] == t1mod.DEV,
           "the chosen configuration records that it was chosen on development")
        best_dev = max(
            (row for row in study2["configs"]
             if (row[t1mod.DEV].get("net_expectancy_r") or -1.0) > 0),
            key=lambda row: row[t1mod.DEV]["net_expectancy_r"],
        )
        ok(sel["t1_pct"] == best_dev["t1_pct"] and sel["sl_pct"] == best_dev["sl_pct"]
           and sel["cap_min"] == best_dev["cap_min"],
           "the selection is the best development configuration, not the best "
           "holdout one")
        ok("holdout_cost_stress" in sel and "holdout_outliers_removed" in sel,
           "the chosen configuration is stressed on cost and on outlier removal")
        ok(set(sel["holdout_cost_stress"]) == {f"cost_{m}x" for m in COST_STRESS},
           "every frozen cost multiplier is reported")

        # --------------------------------------------------------------- verdicts
        good = {
            "dev": {"net_expectancy_r": 0.10, "p_value_one_sided": 1e-12},
            "val": {"net_expectancy_r": 0.08},
            "holdout": {"net_expectancy_r": 0.05, "trades": MIN_TRADES_PER_WINDOW},
            "holdout_cost_stress": {"cost_2.0x": {"net_expectancy_r": 0.01}},
            "holdout_outliers_removed": {
                "top_5.0pct_removed": {"net_expectancy_r": 0.01}},
        }
        status, reasons = t1mod.verdict(good, 100)
        ok(status == VALIDATED and not reasons,
           "a candidate clearing every bar is VALIDATED and states no reason")

        thin = json.loads(json.dumps(good))
        thin["holdout"]["trades"] = MIN_TRADES_PER_WINDOW - 1
        status, reasons = t1mod.verdict(thin, 100)
        ok(status == RESEARCH_LEAD
           and any("too few resolved trades" in x for x in reasons),
           "a positive candidate with a thin holdout is a lead, not VALIDATED")

        costly = json.loads(json.dumps(good))
        costly["holdout_cost_stress"]["cost_2.0x"]["net_expectancy_r"] = -0.01
        status, reasons = t1mod.verdict(costly, 100)
        ok(status == RESEARCH_LEAD
           and any("twice the modelled cost" in x for x in reasons),
           "a candidate that needs optimistic execution is not VALIDATED")

        outlier = json.loads(json.dumps(good))
        outlier["holdout_outliers_removed"]["top_5.0pct_removed"][
            "net_expectancy_r"] = -0.2
        status, reasons = t1mod.verdict(outlier, 100)
        ok(status == RESEARCH_LEAD and any("top 5%" in x for x in reasons),
           "a result created by its best trades is not VALIDATED")

        lucky = json.loads(json.dumps(good))
        lucky["dev"]["p_value_one_sided"] = 0.04
        status, reasons = t1mod.verdict(lucky, 100)
        ok(status == RESEARCH_LEAD and any("Bonferroni" in x for x in reasons),
           "0.04 does not survive the correction for 100 counted hypotheses")
        ok(t1mod.verdict(lucky, 1)[0] == VALIDATED,
           "the same p-value does survive when only one hypothesis was asked, "
           "which is exactly what the count is for")

        failed = json.loads(json.dumps(good))
        failed["val"]["net_expectancy_r"] = -0.05
        failed["holdout"]["net_expectancy_r"] = -0.05
        ok(t1mod.verdict(failed, 100)[0] == REJECTED,
           "a candidate that fails validation is REJECTED, not a lead")

        # ------------------------------------------------- descriptive short capture
        desc = t1mod.describe_reach(rl, cost)
        ok("verdict" not in desc and "selected" not in desc,
           "a short capture is described, never selected on")
        rows = {row["t1_pct"]: row for row in desc["reach"]}
        for t in T1_GRID:
            row = rows[t]
            ok(row["reached_within_15m_pct"] <= row["reached_within_30m_pct"]
               <= row["reached_within_60m_pct"],
               f"{t}% reach cannot fall as the cap grows")
        ok(rows[T1_GRID[0]]["reached_within_60m_pct"]
           >= rows[T1_GRID[-1]]["reached_within_60m_pct"],
           "a larger target is never reached more often than a smaller one")
        ok(rows[0.05]["clears_round_trip_cost"] in (True, False),
           "each distance states whether it clears the round-trip cost at all")
        med = desc["round_trip_cost_pct_median"]
        ok(all(row["clears_round_trip_cost"] == (row["t1_pct"] > med)
               for row in desc["reach"]),
           "the cost flag is the distance against the measured round trip, not a "
           "judgement about it")
        ok(all(abs(row["t1_in_multiples_of_cost"] - row["t1_pct"] / med) < 0.01
               for row in desc["reach"]),
           "each distance is also stated in multiples of its own round-trip cost")
        adv = f"adverse_same_size_first_within_{reach.MAX_CAP}m_pct"
        ok(all(row[adv] is not None for row in desc["reach"]),
           "the equal-sized adverse move is reported beside every reach rate, so a "
           "reach rate is never readable on its own")
        ok(all(0.0 <= row[adv] <= 100.0 for row in desc["reach"]),
           "the adverse-first rate is a share of the same instants, not of a "
           "subset chosen after the fact")

        # A descriptive window is measured on the rows asked for, nothing else.
        half = rl.eligible.copy()
        half[rl.eligible.nonzero()[0][: int(rl.eligible.sum()) // 2]] = False
        part = t1mod.describe_reach(rl, cost, half)
        ok(part["decision_instants"] < desc["decision_instants"],
           "passing a window restricts the description to that window's instants")

        # ------------------------------------------------------------- premium math
        ratio = 0.45
        for delta in DELTA_BANDS:
            need = premium_map.required_underlying_pct(20.0, ratio, delta)
            back = premium.premium_gain_pct(need, ratio, delta)
            ok(abs(back - 20.0) < 1e-9,
               f"the premium bridge inverts exactly at delta {delta}")
        ok(premium.premium_gain_pct(0.2, ratio, 0.80)
           > premium.premium_gain_pct(0.2, ratio, 0.35),
           "a higher delta converts the same move into more premium")
        ok(premium.premium_gain_pct(0.2, 0.45, 0.5)
           > premium.premium_gain_pct(0.2, 4.50, 0.5),
           "a premium worth less of spot moves a larger percentage on the same "
           "underlying move")
        try:
            premium.premium_gain_pct(0.2, 0.0, 0.5)
            ok(False, "a zero premium/strike ratio is refused, not divided by")
        except ValueError:
            ok(True, "a zero premium/strike ratio is refused, not divided by")
        prows = premium.strike_choice(INSTRUMENT, {}, 0.20,
                                      reached_pct=31.3, median_minutes=25.0)
        ok(len(prows) == len(DELTA_BANDS)
           and all(p["basis"] == DERIVED_PREMIUM for p in prows),
           "every premium row is labelled derived, never measured")
        ok(all(p["measured_reach_pct"] == 31.3 for p in prows),
           "the probability half of a derived row carries the measured reach rate")
        ok(all("theta" in p["note"] and "spread" in p["note"] for p in prows),
           "each derived row states what makes the real outcome worse")

        # ----------------------------------------------------------- tier gating
        inv = [row for row in universe.inventory()
               if row["instrument"] == INSTRUMENT]
        original = dict(p24data.BACKTEST_FILES)
        p24data.BACKTEST_FILES.clear()
        p24data.BACKTEST_FILES[INSTRUMENT] = fp
        try:
            inv = [row for row in universe.inventory()
                   if row["instrument"] == INSTRUMENT]
            ok(inv and inv[0]["tier"] == universe.SHORT_CAPTURE,
               "a short series shipped as a five-year file is still tiered as a "
               "short capture, on bar and session count, not on where it came from")
            res = runner.study([INSTRUMENT])
            ok(res["headline"] == NO_REACHABLE_T1 and not res["validated"],
               "the fixture run claims nothing")
            ok(res["instruments"][INSTRUMENT]["verdict"] == REQUIRES_MORE_DATA,
               "a short capture's verdict is REQUIRES_MORE_DATA with a reason")
            ok(res["hypotheses_counted"] > 0
               and res["bonferroni_threshold"] == 0.05 / res["hypotheses_counted"],
               "the correction threshold is derived from the counted total")
            ok(res["scope"] == "RESEARCH_ONLY",
               "the result declares itself research only")
            ok(any("optimistic by exactly one spread" in x for x in res["limits"]),
               "the missing spread is stated as a limit, not omitted")
            # A fixture must never overwrite the real run's artefacts.
            out = report.write(res, out_dir=os.path.join(tmp, "phase32"))
            ok(out["dir"] != p24data._resolve(report.OUT_DIR),
               "the fixture wrote its artefacts somewhere disposable")
            for name in report.FILES:
                ok(os.path.exists(os.path.join(out["dir"], name)),
                   f"artefact {name} is written")
            md = open(os.path.join(out["dir"], "PHASE32_RESULT.md"),
                      encoding="utf-8").read()
            ok("Research only. No production, order-path or live-gate change." in md,
               "the report states its scope in the first lines")
            ok(NO_REACHABLE_T1 in md, "the report prints the honest headline")
            ans = json.loads(open(
                os.path.join(out["dir"], "p32_answers.json"),
                encoding="utf-8").read())
            ok(ans["how_much_premium_can_be_expected_over_5_years"]["answer"]
               == "NOT_MEASURABLE",
               "the five-year premium question is answered NOT_MEASURABLE")
            ok(ans["can_it_be_tested_on_all_instruments"]["answer"] == "PARTLY",
               "the all-instruments question is answered partly, with counts")
            ok(ans["grid_counted"]["hypotheses_counted"]
               == res["hypotheses_counted"],
               "the answers carry the same hypothesis count as the run")
        finally:
            p24data.BACKTEST_FILES.clear()
            p24data.BACKTEST_FILES.update(original)

    # A verdict without its reason is the shape a reader mistakes for "no reason
    # given", so the row must carry the runner's own wording.
    rows = report._selected_rows({"instruments": {"X": {
        "tier": universe.FIVE_YEAR,
        "sides": {"LONG": {
            "verdict": NO_REACHABLE_T1,
            "reason": "nothing was positive on development",
            "selected": None,
            "premium_translation": [{
                "t1_underlying_pct": 0.15,
                "selection_basis": "SHORTEST_COST_CLEARING_DISTANCE_NOT_VALIDATED",
            }],
        }},
    }}})
    ok(len(rows) == 1
       and rows[0]["verdict_reason"] == "nothing was positive on development",
       "a per-side row reports why the verdict came out that way")
    ok(rows[0]["descriptive_t1_pct"] == 0.15
       and rows[0]["descriptive_basis"].endswith("NOT_VALIDATED"),
       "the descriptive distance is carried with its not-validated basis")

    print(f"PHASE 32 SMOKE — {PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
