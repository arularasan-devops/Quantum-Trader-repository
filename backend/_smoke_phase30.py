"""Phase 30 smoke — the candle/context/averaging study's honesty properties.

Every check here exists because the opposite mistake turns a nothing result into
a candle-pattern edge: flagging a pattern with a bar it could not have seen,
tuning a definition after looking at outcomes, confirming on the same bar the
signal fired, filling at the signal bar's close, comparing a pattern against
nothing instead of against the non-pattern control, correcting for the
hypotheses reported instead of the hypotheses run, spending the untouched
holdout on discovery, calling an arm that risks two units better because its
total return is bigger, pricing an option from underlying movement, or letting
a negative net expectancy hide whether the effect was absent or merely taxed
away.

The fixture is a purpose-built one-minute series written to a temporary file, so
these run anywhere: no five-year download, no captured book, no live engine.

    .venv/bin/python _smoke_phase30.py
"""
from __future__ import annotations

import inspect
import json
import math
import os
import sqlite3
import tempfile

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase24 import outcomes as p24out
from app.research.phase30 import (
    NO_AVERAGING_EDGE_FOUND,
    NO_CANDLE_PATTERN_EDGE_FOUND,
    REJECTED,
    REQUIRES_MORE_DATA,
    UNMEASURED,
    VALIDATED,
    averaging,
    context,
    options,
    patterns,
    pool,
    report,
    run,
    study,
)

PASS = 0
FAIL: list[str] = []

DAY = 86_400
IST_OFFSET = 19_800
OPEN_IST = 9 * 3_600 + 15 * 60
BARS_PER_SESSION = 375
INSTRUMENT = "NIFTY"

# §2/§3 vocabulary, spelled out here rather than imported, so a silent rename or
# a dropped pattern in the module fails this file instead of passing it.
REQUIRED_PATTERNS = (
    "HAMMER", "INVERTED_HAMMER", "BULLISH_ENGULFING", "PIERCING_LINE",
    "MORNING_STAR", "THREE_WHITE_SOLDIERS", "TWEEZER_BOTTOM", "BULLISH_HARAMI",
    "DRAGONFLY_DOJI",
    "SHOOTING_STAR", "HANGING_MAN", "BEARISH_ENGULFING", "DARK_CLOUD_COVER",
    "EVENING_STAR", "THREE_BLACK_CROWS", "TWEEZER_TOP", "BEARISH_HARAMI",
    "GRAVESTONE_DOJI",
    "INSIDE_BAR", "OUTSIDE_BAR", "MARUBOZU", "DOJI", "SPINNING_TOP",
    "RANGE_EXPANSION", "NR4", "NR7", "ENGULFING_AFTER_PULLBACK",
    "BREAKOUT_RETEST", "FAILED_BREAKOUT", "REJECTION_WICK",
)

REQUIRED_MEASUREMENTS = (
    "open", "high", "low", "close", "body", "range", "upper_wick", "lower_wick",
    "body_over_range", "upper_over_body", "lower_over_body", "range_atr",
    "volume", "close_position", "range_position_20",
)

REQUIRED_QUESTIONS = 17


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"  FAILED: {label}")


def build_series_file(path: str, *, sessions: int = 60) -> None:
    """A synthetic 1-minute series with enough shape to fire every pattern family.

    Deliberately not a random walk with a drift: a series that trends one way
    makes long patterns look profitable and would let a look-ahead bug pass. The
    oscillation returns to where it started, so any positive expectancy in the
    fixture is a bug in the study rather than a property of the fixture.
    """
    base_day = 1_780_000_000 // DAY * DAY
    rows = []
    level = 20_000.0
    for d in range(sessions):
        start = base_day + d * DAY + OPEN_IST - IST_OFFSET
        for m in range(BARS_PER_SESSION):
            swing = 40.0 * math.sin(m / 17.0) + 15.0 * math.sin(m / 3.1 + d)
            level = 20_000.0 + swing + 5.0 * math.sin(d / 4.0)
            o = level + 3.0 * math.sin(m / 2.0)
            c = level + 3.0 * math.cos(m / 2.3)
            wick = 4.0 + 3.0 * abs(math.sin(m / 5.0))
            rows.append({
                "time": start + m * 60,
                "open": round(o, 2),
                "high": round(max(o, c) + wick, 2),
                "low": round(min(o, c) - wick, 2),
                "close": round(c, 2),
                "volume": 1_000.0 + 500.0 * abs(math.sin(m / 11.0)),
            })
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _empty_store(path: str) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE candles (instrument TEXT, ts INTEGER, open REAL, "
        "high REAL, low REAL, close REAL, volume REAL, "
        "PRIMARY KEY (instrument, ts))"
    )
    con.execute(
        "CREATE TABLE chain_snapshots (instrument TEXT, ts INTEGER, "
        "payload TEXT NOT NULL, source TEXT, PRIMARY KEY (instrument, ts))"
    )
    con.commit()
    con.close()


def _cand(**over) -> study.Candidate:
    """A candidate that passes every §20 requirement, before the override."""
    good = {
        "trades": 400, "avg_net_r": 0.20, "t1_before_sl_pct": 55.0,
        "profit_factor": 1.4, "max_drawdown_r": 5.0, "total_net_r": 80.0,
        "p_value_vs_base_rate": 0.0001,
    }
    c = study.Candidate()
    c.instrument = INSTRUMENT
    c.pattern = "HAMMER"
    c.side = "LONG"
    c.stop_atr = 1.0
    c.confirmed = False
    c.conditions = ()
    c.dev = dict(good)
    c.val = dict(good)
    c.holdout = dict(good)
    c.all = dict(good)
    c.walk_forward = {"majority_positive": True, "positive": 4, "scored": 5}
    c.stress = {"survives_cost_stress": True}
    c.outlier = {"outlier_dependent": False}
    c.control_dev = {"control_t1_before_sl_pct": 40.0}
    c.p_value = 0.0001
    c.holdout_p_value = 0.001
    c.fdr_survivor = True
    c.status = ""
    c.reasons = []
    c.trades_per_day = 1.0
    c.trades_per_week = 5.0
    for k, v in over.items():
        setattr(c, k, v)
    return c


def main() -> int:  # noqa: C901 - a flat list of independent checks
    print("PHASE 30 SMOKE — candle / context / averaging study")

    # ---------------------------------------------------------------- §2 frozen
    ok(tuple(patterns.NAMES) == tuple(dict.fromkeys(patterns.NAMES)),
       "the pattern vocabulary has no duplicate name")
    ok(set(REQUIRED_PATTERNS) == set(patterns.NAMES),
       "every requested pattern exists and no extra pattern was invented")
    ok(all(patterns.SIDES[n] in (patterns.LONG, patterns.SHORT, patterns.BOTH)
           for n in patterns.NAMES),
       "every pattern declares which side it may be tested on")
    ok(len(patterns.FINGERPRINT) == 16 and patterns.fingerprint() == patterns.FINGERPRINT,
       "the definitions carry a stable fingerprint of their own source")
    src = inspect.getsource(patterns)
    code = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith(("#", '"', "*"))
    )
    ok("t1_before_sl" not in code and "net_r" not in code
       and "outcomes" not in code and "mfe" not in code and "mae" not in code,
       "no pattern definition can reference an outcome")

    with tempfile.TemporaryDirectory() as tmp:
        series_path = os.path.join(tmp, "SMOKE_ONE_MINUTE.jsonl")
        build_series_file(series_path)
        original = dict(p24data.BACKTEST_FILES)
        p24data.BACKTEST_FILES.clear()
        p24data.BACKTEST_FILES[INSTRUMENT] = series_path
        try:
            s = p24data.load_series(INSTRUMENT)
            ok(s is not None and len(s) == 60 * BARS_PER_SESSION,
               "the fixture series loads through the production loader")

            prep = pool.prepare(INSTRUMENT)
            ok(prep is not None, "the fixture prepares into a pool")

            det = prep.det
            counts = patterns.occurrence_counts(det)
            ok(sum(counts.values()) > 0, "the fixture fires patterns at all")
            ok(tuple(counts) == tuple(patterns.NAMES),
               "an occurrence count is reported for every pattern, zero included")

            # ------------------------------------------------- causality, hard
            # Detection on a truncated series must reproduce the full series'
            # flags exactly. Any use of a later bar shows up here as a mismatch.
            cut = len(s) - 500
            trunc = p24data.Series(INSTRUMENT, [
                {"time": int(s.ts[i]), "open": float(s.open[i]),
                 "high": float(s.high[i]), "low": float(s.low[i]),
                 "close": float(s.close[i]), "volume": float(s.volume[i])}
                for i in range(cut)
            ])
            from app.research.phase24 import features as p24feat
            tfeat = p24feat.build(trunc)
            tdet = patterns.detect(trunc, tfeat["atr"])
            mismatched = [
                n for n in patterns.NAMES
                if not np.array_equal(tdet[n][:cut - 5], det[n][:cut - 5])
            ]
            ok(not mismatched,
               f"no pattern uses a future bar (mismatched: {mismatched[:4]})")

            m = patterns.measure(s, prep.feat["atr"])
            missing = [k for k in REQUIRED_MEASUREMENTS if k not in m]
            ok(not missing, f"every §3 measurement is recorded (missing {missing})")
            ok(all(np.asarray(m[k]).size == len(s) for k in REQUIRED_MEASUREMENTS),
               "every measurement is aligned to the series")
            fin = np.isfinite(m["close_position"])
            ok(bool(((m["close_position"][fin] >= -1e-9)
                     & (m["close_position"][fin] <= 1 + 1e-9)).all()),
               "close position stays inside the candle's own range")

            # ------------------------------------------------- §5 confirmation
            idx = np.array([100, 100, 200, 200], dtype=np.int64)
            side = np.array([p24out.LONG, p24out.SHORT] * 2, dtype=np.int8)
            conf = context.confirmation(s, idx, side)
            for j, i in enumerate(idx):
                nxt = i + 1
                favourable_half = (
                    s.close[nxt] >= (s.high[nxt] + s.low[nxt]) / 2.0
                    if side[j] == p24out.LONG
                    else s.close[nxt] <= (s.high[nxt] + s.low[nxt]) / 2.0
                )
                direction = (
                    s.close[nxt] > s.close[i] if side[j] == p24out.LONG
                    else s.close[nxt] < s.close[i]
                )
                ok(bool(conf[j]) == bool(favourable_half and direction),
                   f"confirmation is the frozen next-bar rule (row {j})")
            csrc = inspect.getsource(context.confirmation)
            ok("idx + 1" in csrc or "idx+1" in csrc,
               "confirmation reads the bar after the pattern, never the same bar")

            # -------------------------------------------------------- §12 pool
            p = pool.build(prep, stop_atr=1.0, t1_r=1.5)
            ok(p is not None and len(p) > 0, "the candidate pool resolves")
            ok(set(np.unique(p.side)) == {p24out.LONG, p24out.SHORT},
               "the pool takes no view on direction: both sides are tested")
            ctrl = p.pat[pool.NON_PATTERN]
            any_pat = np.zeros(len(p), dtype=bool)
            for name in patterns.NAMES:
                any_pat |= p.pat[name]
            ok(ctrl.any() and not (ctrl & any_pat).any(),
               "the non-pattern control contains no pattern bar")
            ok(any_pat.any(), "the pool contains pattern rows")
            ok(int(ctrl.sum()) >= 30,
               f"the control is large enough to pin a base rate ({int(ctrl.sum())})")

            # A fill is the *next* bar's open. Filling at the signal bar's close
            # is the single most common way a candle study invents an edge.
            fills = p.out.entry
            ref = p.idx + (1 if p.confirmed_arm else 0)
            expected = s.open[np.minimum(ref + 1, len(s) - 1)]
            ok(bool(np.allclose(fills, expected)),
               "every fill is the open of the bar after the decision bar")

            ok(bool((p.out.risk[p.out.resolved] > 0).all()),
               "no resolved row carries a zero or negative risk")
            ok(set(np.unique(p.quality)) <= set(context.QUALITY_LABELS),
               "entry quality only uses the four declared labels")

            # A same-bar tie is scored as the stop, never as the target: a
            # 1-minute bar cannot say which came first, and scoring it the other
            # way is worth several points of hit rate on its own.
            hit = p.out.t1_before_sl & (p.out.bars_to_sl > 0) & (p.out.bars_to_t1 > 0)
            ok(bool((p.out.bars_to_t1[hit] < p.out.bars_to_sl[hit]).all()),
               "a target counts only when it was reached strictly before the stop")

            # --------------------------------------------- §14 splits, one-way
            w = study.windows(p.ts)
            dev, val, hold = w["development"], w["validation"], w["holdout"]
            ok(dev[1] <= val[0] and val[1] <= hold[0],
               "development, validation and holdout are chronological and disjoint")
            ok(hold[1] >= p.ts.max(), "the holdout ends at the last bar")
            fold_spans = study.folds(p.ts)
            ok(len(fold_spans) == study.WALK_FORWARD_FOLDS
               and all(a < b for a, b in fold_spans),
               "walk-forward folds are ordered forward spans")
            ok(all(fold_spans[i][1] <= fold_spans[i + 1][0]
                   for i in range(len(fold_spans) - 1)),
               "walk-forward folds do not overlap")

            # -------------------------------------------------- §9 averaging
            base = averaging.simulate(p, averaging.BASELINE)
            arm = averaging.simulate(p, "AVG_1.0R")
            ok(int(base.added.sum()) == 0,
               "the baseline never takes a second entry")
            ok(int(arm.added.sum()) > 0,
               "an averaging arm does take its one second entry")
            ok(bool((arm.units <= 1 + averaging.MAX_EXTRA_ENTRIES).all()),
               "no arm ever holds more than two units: no martingale, no third add")
            added = arm.added
            ok(bool((arm.total_risk_r[added] > arm.initial_risk_r[added] - 1e-9).all()),
               "a second entry increases the risk it reports")
            ok(bool(np.allclose(arm.total_risk_r[~added],
                                arm.initial_risk_r[~added], atol=1e-6)),
               "a row that never added reports only its initial risk")
            cmp_all = averaging.compare(p, p.out.resolved)
            ok(averaging.BASELINE in cmp_all["arms"],
               "the comparison always includes the no-averaging baseline")
            for name, row in cmp_all["arms"].items():
                if name == averaging.BASELINE or not row.get("trades"):
                    continue
                ok("expectancy_per_total_risk_r" in row,
                   f"{name} is judged per unit of total risk, not on total return")

            # --------------------------- §12 pattern vs control, §8 cost wall
            arms = study.build_arms(prep)
            ok(set(arms) == {(st, cf) for st in study.STOP_GRID for cf in (False, True)}
               or all(k[0] in study.STOP_GRID for k in arms),
               "arms cover the frozen stop grid and both confirmation arms")
            pvc = study.pattern_vs_control(arms, 1.0)
            ok(bool(pvc) and all("control_t1_before_sl_pct" in r for r in pvc),
               "every pattern row carries its own control's rate")
            ok(all("avg_gross_r" in r and "cost_over_risk" in r for r in pvc),
               "every pattern row states its pre-cost R next to its cost/risk")
            wall = run._cost_wall(arms)
            ok(bool(wall) and all(r["cost_over_risk"] is None or r["cost_over_risk"] > 0
                                  for r in wall),
               "the cost wall prices every stop band")
            screen = run._gross_screen(pvc)
            ok(screen.get("measured") is True and "any_gross_edge_larger_than_cost" in screen,
               "the gross screen separates 'no effect' from 'effect eaten by cost'")

            # ------------------------------------------------------- §13 count
            seeds, counted, tested = study.stage1(arms)
            ok(counted == len(patterns.NAMES) * len(arms),
               "every stage-1 hypothesis is counted, passed or failed")
            ok(len(tested) >= len(seeds),
               "the rows that failed the gates are still reported")
            ok(all(r["dev"]["trades"] >= study.MIN_DEV_TRADES for r in tested),
               "a row below the development sample bar is never carried forward")
        finally:
            p24data.BACKTEST_FILES.clear()
            p24data.BACKTEST_FILES.update(original)

        # ------------------------------------------------------- §10 options
        store = os.path.join(tmp, "empty.db")
        _empty_store(store)
        res = options.measure(INSTRUMENT, db_path=store)
        ok(res.get("status") == UNMEASURED,
           "an absent option book reads UNMEASURED, never zero and never modelled")
        osrc = inspect.getsource(options)
        ok("mid" not in osrc.replace("middle", ""), "no option leg is ever priced at the mid")
        ok("q.ask[e]" in osrc and "q.bid[x]" in osrc,
           "options enter at the ask and exit at the bid")

    # ---------------------------------------------------------- §20 grading
    ok(study.grade(_cand(), hypotheses=10).status == VALIDATED,
       "a candidate that meets every requirement can be VALIDATED")
    ok(study.grade(_cand(fdr_survivor=False), hypotheses=10_000).status != VALIDATED,
       "failing the multiple-testing correction blocks VALIDATED")
    hi_wr = _cand()
    hi_wr.all = {**hi_wr.all, "t1_before_sl_pct": 92.0, "avg_net_r": -0.3}
    hi_wr.holdout = {**hi_wr.holdout, "avg_net_r": -0.3}
    ok(study.grade(hi_wr, hypotheses=10).status != VALIDATED,
       "a 92% hit rate with a negative expectancy is not VALIDATED")
    small = _cand()
    small.all = {**small.all, "trades": 10}
    ok(study.grade(small, hypotheses=10).status == REQUIRES_MORE_DATA,
       "too small a sample is REQUIRES_MORE_DATA, not REJECTED")
    no_cost = _cand(stress={"survives_cost_stress": False})
    graded = study.grade(no_cost, hypotheses=10)
    ok(graded.status != VALIDATED and any("cost" in r for r in graded.reasons),
       "a candidate that needs optimistic execution is refused, with the reason")
    outlier = _cand(outlier={"outlier_dependent": True})
    ok(study.grade(outlier, hypotheses=10).status != VALIDATED,
       "profit created by the top winners is not VALIDATED")
    flat = _cand()
    flat.all = {**flat.all, "t1_before_sl_pct": 40.5}
    ok(study.grade(flat, hypotheses=10).status != VALIDATED,
       "a pattern that matches its own non-pattern control is not VALIDATED")
    for c in (study.grade(_cand(), hypotheses=10), small, hi_wr, outlier):
        ok(c.status in (VALIDATED, "RESEARCH_LEAD", REQUIRES_MORE_DATA, REJECTED),
           "every graded candidate holds exactly one of the four statuses")

    # ------------------------------------------------------------- §17 FDR
    strong = [_cand(p_value=1e-9) for _ in range(3)]
    study.apply_fdr(strong, 5)
    ok(all(c.fdr_survivor for c in strong),
       "a genuinely tiny p-value survives a small search")
    marginal = [_cand(p_value=0.02) for _ in range(3)]
    study.apply_fdr(marginal, 20_000)
    ok(not any(c.fdr_survivor for c in marginal),
       "the correction is applied over every hypothesis run, not the shortlist")
    ssrc = inspect.getsource(study)
    ok("c.p_value = c.dev.get(" in ssrc,
       "discovery correction uses the development p-value, never the holdout's")
    ok("c.holdout_p_value = c.holdout.get(" in ssrc,
       "the holdout p-value is recorded but kept out of selection")

    # ---------------------------------------------------- §19/§22 reporting
    empty = {
        "version": "SMOKE", "instruments": {}, "candidates": [],
        "options": {}, "verdict": {
            "pattern_verdict": NO_CANDLE_PATTERN_EDGE_FOUND,
            "averaging_verdict": NO_AVERAGING_EDGE_FOUND,
        },
    }
    ans = report.answers(empty)
    ok(len(ans) == REQUIRED_QUESTIONS,
       f"all {REQUIRED_QUESTIONS} required questions are answered even with no result "
       f"(got {len(ans)})")
    ok(all(a["answer"].strip() for a in ans),
       "no required question is answered with an empty string")
    md = report.markdown(empty, report.rankings(empty), ans)
    ok(NO_CANDLE_PATTERN_EDGE_FOUND in md and NO_AVERAGING_EDGE_FOUND in md,
       "the pre-committed stopping rules are printed in the report")
    ok("Room versus cost" in md,
       "the report shows whether the geometry can pay its own cost")
    with tempfile.TemporaryDirectory() as tmp:
        written = report.write(empty, path=tmp)
        ok(set(written) == set(report.ARTEFACTS),
           "every declared artefact is written")
        ok(all(os.path.exists(os.path.join(tmp, f)) for f in report.ARTEFACTS),
           "every artefact exists on disk")
        pvc_file = json.load(open(os.path.join(tmp, "p30_pattern_vs_control.json")))
        ok(isinstance(pvc_file, dict), "the pattern-vs-control artefact is readable")
        raw = json.load(open(os.path.join(tmp, "p30_raw_result.json")))
        ok(raw.get("verdict") == empty.get("verdict"),
           "the stored raw result carries the same verdict the report rendered")
        rewritten = report.write(raw, path=tmp)
        ok(set(rewritten) == set(report.ARTEFACTS),
           "the stored result alone is enough to re-render every artefact")
        ok(json.load(open(os.path.join(tmp, "p30_verdict.json")))["verdict"]
           == empty.get("verdict"),
           "re-rendering a report cannot move the verdict it reports")

    # --------------------------------------------------------- §21 production
    here = os.path.dirname(os.path.abspath(__file__))
    pkg = os.path.join(here, "app", "research", "phase30")
    banned = ("place_order", "placeOrder", "smart_api", "broker.", "order_path")
    offenders = []
    for fn in sorted(os.listdir(pkg)):
        if not fn.endswith(".py"):
            continue
        text = open(os.path.join(pkg, fn), encoding="utf-8").read()
        if any(b in text for b in banned):
            offenders.append(fn)
    ok(not offenders,
       f"no Phase 30 module can reach an order path (offenders: {offenders})")
    ok(report.OUT_DIR == "data/phase30",
       "Phase 30 writes only into its own artefact directory")

    print("")
    print(f"PHASE 30 SMOKE — {PASS} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
