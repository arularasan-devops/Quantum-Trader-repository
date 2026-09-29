"""Phase 31 smoke — the excursion study's honesty properties.

Each check exists because the opposite mistake turns "how much can it give" into
a number that cannot be earned: filling at the decision bar's own price, letting
a window run through an overnight gap, quoting a favourable excursion without the
adverse one measured on the same window, measuring short horizons on a larger
sample than long ones, calling an underlying move a premium result, averaging a
journal's placeholder fills into a premium percentage, mixing a futures price
percentage into a premium percentage, or presenting 21 real two-sided instants as
a distribution.

The excursion fixture is a hand-built series whose forward path is known exactly,
so the arithmetic is checked against numbers computed by hand rather than against
itself.

    .venv/bin/python _smoke_phase31.py
"""
from __future__ import annotations

import json
import os
import tempfile

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase31 import (
    ASSUMED_PREMIUM_MAP,
    BASES,
    DELTA_BANDS,
    HORIZONS,
    MEASURED_REALIZED,
    MEASURED_UNDERLYING,
    PCT_GRID,
    PREMIUM_TARGETS_PCT,
    UNMEASURED,
    evidence,
    excursion,
    premium_map,
    realized,
    report,
    run as runner,
)

PASS = 0
FAIL: list[str] = []

DAY = 86_400
IST_OFFSET = 19_800
OPEN_IST = 9 * 3_600 + 15 * 60
INSTRUMENT = "NIFTY"
BARS_PER_SESSION = 130   # enough for a 60-minute window plus eligible bars


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"  FAILED: {label}")


def series_file(path: str, *, sessions: int = 4) -> None:
    """A series whose forward path is exactly known.

    Within a session price rises by exactly 1.0 per bar with a 0.5 wick each way,
    so from a fill at ``open[i+1]`` the favourable excursion after ``h`` bars is
    arithmetic rather than a simulation artefact. Each session restarts at the
    same level, so an overnight gap of a known size sits between sessions and a
    window that leaks across one is detectable.
    """
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


def main() -> int:
    global PASS

    # ------------------------------------------------------------ §0 labels
    ok(set(BASES) == {MEASURED_UNDERLYING, MEASURED_REALIZED,
                      ASSUMED_PREMIUM_MAP, UNMEASURED},
       "every result must carry one of the four declared evidence bases")
    ok(len(set(BASES)) == len(BASES), "evidence bases are distinct labels")
    ok(HORIZONS == (1, 3, 5, 10, 15, 30, 60),
       "the horizon grid is frozen before any outcome is measured")
    ok(tuple(sorted(PCT_GRID)) == tuple(PCT_GRID) and PCT_GRID[0] > 0,
       "the excursion threshold grid is positive and ordered")
    ok(tuple(sorted(PREMIUM_TARGETS_PCT)) == tuple(PREMIUM_TARGETS_PCT),
       "the premium targets are frozen and ordered")

    with tempfile.TemporaryDirectory() as tmp:
        fp = os.path.join(tmp, "NIFTY_ONE_MINUTE.jsonl")
        series_file(fp)
        original = dict(p24data.BACKTEST_FILES)
        p24data.BACKTEST_FILES.clear()
        p24data.BACKTEST_FILES[INSTRUMENT] = fp
        try:
            s = p24data.load_series(INSTRUMENT)
        finally:
            p24data.BACKTEST_FILES.clear()
            p24data.BACKTEST_FILES.update(original)
        ok(s is not None and len(s) == 4 * BARS_PER_SESSION,
           "the fixture series loads with every bar")

        long_ex = excursion.build(s, excursion.LONG)
        short_ex = excursion.build(s, excursion.SHORT)
        elig = excursion.eligible(long_ex)

        # -------------------------------------------------- §2 fill and window
        i = int(np.flatnonzero(elig)[0])
        ok(long_ex.entry[i] == s.open[i + 1],
           "the fill is the next bar's open, never the decision bar's own price")
        ok(not np.isfinite(long_ex.entry[len(s) - 1]),
           "the last bar of the series has no fill and cannot be measured")

        # In the fixture, high[i+1+h] = open[i+1] + h + 0.5, so the favourable
        # excursion is a number computable by hand.
        for h in (1, 5, 30, 60):
            want = 100.0 * (h + 0.5) / long_ex.entry[i]
            ok(abs(long_ex.fav[h][i] - want) < 1e-9,
               f"the {h}-minute favourable excursion equals the hand-computed "
               f"{want:.4f}%")
        # The adverse side includes the fill bar's own low, the favourable side
        # excludes the fill bar's high: both conservative, since where inside a
        # one-minute bar the extreme occurred is unknown.
        want_adv = 100.0 * (s.low[i + 1] - long_ex.entry[i]) / long_ex.entry[i]
        ok(abs(long_ex.adv[60][i] - want_adv) < 1e-9,
           "the adverse side includes the fill bar's own low, so a move against "
           "the fill in the fill minute is counted")
        ok(abs(long_ex.fav[1][i]
               - 100.0 * (s.high[i + 2] - long_ex.entry[i]) / long_ex.entry[i])
           < 1e-9,
           "the favourable side excludes the fill bar's high, which cannot be "
           "proven to have occurred after the fill")

        # ------------------------------------------------- §2 session boundary
        # The bar 60 minutes before a session's close cannot be eligible: its
        # window would have to cross the overnight gap.
        last_of_session = []
        for k in range(len(s) - 1):
            if long_ex.session[k] != long_ex.session[k + 1]:
                last_of_session.append(k)
        ok(last_of_session, "the fixture contains session boundaries to test")
        for k in last_of_session:
            ok(not elig[k],
               "a bar whose 60-minute window would cross the session boundary "
               "is excluded")
            ok(not elig[k - 30],
               "a bar 30 minutes from the close is excluded too, since its "
               "longest window does not fit its own session")
        gap = float(s.open[last_of_session[0] + 1] - s.close[last_of_session[0]])
        ok(abs(gap) > 100.0,
           "the fixture's overnight gap is large enough that a leaking window "
           "would be visible")
        ok(float(np.nanmax(long_ex.fav[60][elig])) < 10.0,
           "no eligible favourable excursion contains the overnight gap")

        # ------------------------------------------- §2 identical sample sizes
        counts = {h: int(np.isfinite(long_ex.fav[h][elig]).sum())
                  for h in HORIZONS}
        ok(len(set(counts.values())) == 1,
           f"every horizon is measured on the identical sample {counts}")

        # ------------------------------------------------------- §2 side signs
        ok(float(np.nanmean(long_ex.fav[60][elig])) > 0
           and float(np.nanmean(long_ex.adv[60][elig])) <= 0,
           "on a rising fixture LONG favours up and its adverse side is <= 0")
        ok(float(np.nanmean(short_ex.fav[60][elig])) <= 0,
           "SHORT does not profit from the same rising fixture, so the sides "
           "are not the same measurement relabelled")
        ok(float(np.nanmean(short_ex.adv[60][elig]))
           < float(np.nanmean(short_ex.fav[60][elig])),
           "SHORT's adverse side is worse than its favourable side on a rising "
           "fixture, as it must be")
        ok(all(long_ex.adv[h][i] <= 0 for h in HORIZONS),
           "a fill whose own minute traded below it records an adverse move at "
           "every horizon, never a favourable-only path")

        # ------------------------------------------------------ §2 timing grid
        summ = excursion.summarize(long_ex)
        ok(summ["decision_instants"] == int(elig.sum()),
           "the summary counts exactly the eligible decision instants")
        ok(summ["sessions"] <= 4,
           "a cohort reports the sessions it spans, not the series total")
        t = summ["thresholds"][f"{PCT_GRID[0]:.2f}"]
        prev = -1.0
        for h in HORIZONS:
            cur = t[f"reached_within_{h}m_pct"]
            ok(cur >= prev,
               f"reach rate cannot fall as the horizon grows ({h}m)")
            prev = cur
        ok(abs(t["reached_within_60m_pct"] - t["reached_pct_of_instants"]) < 1e-9,
           "the 60-minute reach rate is the overall reach rate, since 60 is the "
           "longest window measured")
        big = summ["thresholds"][f"{PCT_GRID[-1]:.2f}"]
        ok(big["reached_pct_of_instants"] <= t["reached_pct_of_instants"],
           "a larger move cannot be reached more often than a smaller one")
        ok(t["minutes_to_reach"]["n"] > 0
           and t["minutes_to_reach"]["p50"] >= 1,
           "time-to-reach is measured in whole minutes from the fill")
        ok(0.0 <= t["adverse_same_size_first_pct"] <= 100.0,
           "the adverse-first rate is a percentage of the rows that reached")
        ok(t["adverse_same_size_first_pct"] == 0.0,
           "on a monotonically rising fixture the adverse side never arrives "
           "first, so the ordering is real and not an artefact")

        periods = excursion.by_period(long_ex)
        ok([p["period"] for p in periods]
           == [name for name, _, _ in excursion.PERIODS],
           "the session-period split reports every frozen period")
        ok(sum(p["decision_instants"] for p in periods)
           == summ["decision_instants"],
           "the period cohorts partition the sample exactly once")

        # -------------------------------------------- §4 the assumed map rows
        ratio = {"NIFTY": {"n": 12, "median_premium_over_strike_pct": 0.45}}
        tbl = premium_map.table(INSTRUMENT, ratio, summ["thresholds"])
        ok(len(tbl) == len(PREMIUM_TARGETS_PCT) * len(DELTA_BANDS),
           "the map covers every premium target at every delta band")
        ok(all(r["basis"] == ASSUMED_PREMIUM_MAP for r in tbl),
           "every mapped row is labelled an assumption, never a measured "
           "option result")
        ok(all("measured" in r["note"] or "optimistic" in r["note"] for r in tbl),
           "every mapped row states that it is an optimistic bound")
        ok(all(r["premium_ratio_source"].startswith("measured") for r in tbl),
           "the premium/strike ratio is sourced from recorded entries when they "
           "exist, and says so")
        ok(all(r["threshold_at_least_requirement"] or "off_grid" in r
               for r in tbl),
           "a mapped row is answered at a threshold at least as large as its "
           "requirement, or not answered at all")
        # A +20% premium at delta 0.50 on a 0.45%-of-strike premium needs
        # 20 * 0.0045 / 0.5 = 0.18% of the underlying.
        need = premium_map.required_underlying_pct(20.0, 0.45, 0.50)
        ok(abs(need - 0.18) < 1e-9,
           "the premium-to-underlying arithmetic matches the stated formula")
        ok(premium_map.required_underlying_pct(20.0, 0.45, 0.80)
           < premium_map.required_underlying_pct(20.0, 0.45, 0.35),
           "a higher delta needs a smaller underlying move for the same "
           "premium gain")
        far = [r for r in tbl if r.get("off_grid")]
        ok(all(r["measured_reach_pct_of_instants"] is None for r in far),
           "a requirement beyond the frozen grid is left unanswered rather "
           "than extrapolated")
        unmeasured_inst = premium_map.premium_over_strike_pct("SILVER", {})
        ok(unmeasured_inst[1].startswith("stated"),
           "an instrument with no recorded entries uses a stated ratio and "
           "declares it")

    # ------------------------------------------ §3 journal classification
    ok(realized.MIN_USABLE_TRADES >= 30,
       "the realized half refuses to describe a distribution from a handful of "
       "fills")
    raw, prov = realized.load()
    classes = prov["by_class"]
    ok(set(classes) <= {realized.DUPLICATE, realized.PLACEHOLDER,
                        realized.NO_FILL, realized.FUTURES, realized.MANUAL,
                        realized.USABLE},
       "every journal row receives exactly one declared class")
    usable = [t for t in raw if t.klass == realized.USABLE]
    ok(all("FUT" not in (t.symbol or "").upper() for t in usable),
       "a futures row never counts as an option premium percentage")
    ok(all((t.reason or "") in realized.ENGINE_REASONS for t in usable),
       "only an engine-resolved exit counts, since a manual close measures the "
       "operator rather than the market")
    pairs = {tuple(p) for p in prov["repeated_entry_exit_pairs"]}
    ok(all(t.klass in (realized.PLACEHOLDER, realized.DUPLICATE)
           for t in raw
           if t.entry is not None and t.exit is not None
           and (float(t.entry), float(t.exit)) in pairs),
       "a repeated entry/exit pair is classified out as a placeholder or a "
       "duplicate rather than averaged in")
    ok(all(t.pct is not None for t in usable),
       "every usable row can state a premium percentage")
    rsum = realized.summary()
    ok(rsum["sufficient"] == (rsum["usable_option_trades"]
                              >= realized.MIN_USABLE_TRADES),
       "sufficiency is decided by the declared minimum, not by preference")
    ok(len(rsum["limits"]) >= 3
       and any("not what was available" in x for x in rsum["limits"]),
       "the realized numbers always carry the exit-truncation limit")
    ok("futures_pct_of_price" in rsum
       and rsum["futures_pct_of_price"] is not rsum["option_premium_pct"],
       "futures percentages are reported separately from premium percentages")
    ok(realized.strike_from_symbol("NIFTY24500CE") == 24500.0,
       "a strike is parsed from a real broker symbol")
    ok(realized.strike_from_symbol("NIFTY FUT") is None,
       "a futures symbol yields no strike")

    # ---------------------------------------------------------- §1 evidence
    inv = evidence.inventory()
    ok(inv["min_required_for_premium_distribution"]
       == evidence.MIN_REAL_PATH_INSTANTS,
       "the premium-distribution minimum is declared, not chosen per run")
    ok(inv["premium_percentage_measurable"] is (
        inv["real_two_sided_decision_instants"]
        >= evidence.MIN_REAL_PATH_INSTANTS),
       "measurability follows the count of real two-sided instants")
    ok(evidence.UNKNOWN not in (evidence.REAL,),
       "an unlabelled snapshot is not treated as a real broker snapshot")
    src = inv["option_books"]["by_source"]
    ok(inv["option_books"]["real_broker_snapshots"] == src.get("REAL_BROKER", 0),
       "only snapshots labelled REAL_BROKER count as real books")
    ok("SIMULATOR" in inv["note"] or "SIMULATOR" in json.dumps(src),
       "simulated books are named in the inventory rather than silently mixed in")

    # ------------------------------------------------------- §4 report shape
    result = {
        "phase": 31,
        "scope": "RESEARCH_ONLY",
        "inventory": inv,
        "instruments": {},
        "realized": {"basis": MEASURED_REALIZED, **rsum},
        "premium_book_result": UNMEASURED,
        "premium_book_reason": "fixture",
        "bases_used": {b: "fixture" for b in BASES},
        "runtime_sec": 0.0,
    }
    with tempfile.TemporaryDirectory() as tmp:
        files = report.write(result, path=tmp)
        ok(set(files) == set(report.ARTEFACTS),
           "every declared artefact is written")
        for name, path in files.items():
            ok(os.path.getsize(path) > 0, f"{name} is not empty")
        md = open(files["PHASE31_RESULT.md"], encoding="utf-8").read()
        ok("Research only" in md,
           "the report states its research-only scope on the page")
        ok(UNMEASURED in md,
           "an unmeasurable premium distribution is named in the report rather "
           "than omitted")
        answers = json.load(open(files["p31_answers.json"], encoding="utf-8"))
        ok(all(a["basis"] in BASES or a["basis"] == "MEASURED" for a in answers),
           "every answer names the basis it rests on")
        ok(any("exit rule" in a["answer"] for a in answers),
           "the report says outright that a realized figure is partly a "
           "statement about the exit rule")

    # --------------------------------------------------------- §5 production
    here = os.path.dirname(os.path.abspath(__file__))
    pkg = os.path.join(here, "app", "research", "phase31")
    banned = ("place_order", "placeOrder", "smart_api", "broker.", "order_path")
    offenders = []
    for fn in sorted(os.listdir(pkg)):
        if not fn.endswith(".py"):
            continue
        text = open(os.path.join(pkg, fn), encoding="utf-8").read()
        if any(b in text for b in banned):
            offenders.append(fn)
    ok(not offenders,
       f"no Phase 31 module can reach an order path (offenders: {offenders})")
    ok(report.OUT_DIR == "data/phase31",
       "Phase 31 writes only into its own artefact directory")
    ok(runner.INSTRUMENTS and all(isinstance(i, str) for i in runner.INSTRUMENTS),
       "the run declares which instruments it measures")

    print("")
    print(f"PHASE 31 SMOKE — {PASS} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
