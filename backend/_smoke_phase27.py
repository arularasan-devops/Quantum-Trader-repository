"""Phase 27 smoke — the higher-timeframe study's honesty properties.

Every check exists because the opposite mistake would turn a losing study into a
winning one. Aggregation is the new machinery here, and it has three ways to lie
that a 1-minute study does not have:

* labelling an aggregated bar with its *opening* minute and then deciding on its
  close — a silent fifteen-minute look-ahead;
* letting a bucket span the overnight boundary, so a bar contains both today's
  close and tomorrow's open;
* keeping the holding horizon in *bars*, which would give the 15-minute study a
  45-hour hold and compare two different strategies as if they were one.

The rest of the file re-checks the properties inherited from Phase 24 at the new
timeframes: next-open entry, the same-bar tie resolved as the stop, slippage
charged once, both directions in the pool, development-only selection, an honest
FDR denominator, strict JSON, and the pre-committed stopping rule firing on the
gate's own result rather than on prose.

The fixtures are synthetic and built here, so this runs anywhere:

    .venv/bin/python _smoke_phase27.py
"""
from __future__ import annotations

import json
import os
import tempfile

import numpy as np

from app.market.instruments import get_spec
from app.research.phase19 import futcosts
from app.research.phase24 import features, outcomes
from app.research.phase24.data import Series
from app.research.phase27 import (
    RESAMPLE_CLAIM,
    RESEARCH_LEAD,
    SPREAD_CLAIM,
    STOP_RULE,
    bars,
    conditions,
    discover,
    outcomes_note,
    pool,
    rank,
    report,
    service,
    study,
)

PASS = 0
FAIL: list[str] = []

IST = bars.IST_OFFSET
DAY = 86_400


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)


def close(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(float(a) - float(b)) <= tol


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def session_ts(day: int, first_minute: int, minutes: int) -> np.ndarray:
    """UTC timestamps for ``minutes`` consecutive IST minutes of one session."""
    base = (1_700_000_000 // DAY) * DAY + day * DAY + first_minute * 60 - IST
    return np.array([base + 60 * k for k in range(minutes)], dtype=np.int64)


def make_series(
    sessions: int = 4,
    minutes: int = 375,
    first_minute: int = 9 * 60 + 15,
    instrument: str = "NIFTY",
) -> Series:
    ts = np.concatenate([
        session_ts(d, first_minute, minutes) for d in range(sessions)
    ])
    n = ts.size
    s = Series.__new__(Series)
    s.instrument = instrument
    s.ts = ts
    # A deterministic, mildly trending saw so highs and lows are distinguishable
    # per minute and no two aggregated bars are identical.
    k = np.arange(n, dtype=float)
    s.open = 20_000.0 + k * 0.5 + 30.0 * np.sin(k / 17.0)
    s.close = s.open + 0.25
    s.high = np.maximum(s.open, s.close) + 1.0 + (k % 7)
    s.low = np.minimum(s.open, s.close) - 1.0 - (k % 5)
    s.volume = 100.0 + (k % 11)
    return s


# ---------------------------------------------------------------------------
# §1 aggregation
# ---------------------------------------------------------------------------
def check_resample() -> None:
    s = make_series(sessions=3, minutes=30)
    for tf in (5, 15):
        out, st = bars.resample(s, tf)
        ok(st["spans_a_session"] is False,
           f"no {tf}m bar spans a session boundary")
        ok(st["source_minute_bars"] == len(s),
           f"{tf}m aggregation reports every source minute it consumed")
        ok(out.ts.size == st["bars"] == 3 * (30 // tf),
           f"{tf}m aggregation produces whole bars for whole sessions")
        # the first bar of the first session, checked field by field
        ok(close(out.open[0], s.open[0]),
           f"{tf}m bar open is the first constituent minute's open")
        ok(close(out.close[0], s.close[tf - 1]),
           f"{tf}m bar close is the LAST constituent minute's close")
        ok(close(out.high[0], s.high[:tf].max()),
           f"{tf}m bar high is the true maximum of its own minutes")
        ok(close(out.low[0], s.low[:tf].min()),
           f"{tf}m bar low is the true minimum of its own minutes")
        ok(close(out.volume[0], s.volume[:tf].sum()),
           f"{tf}m bar volume is the sum of its own minutes")
        ok(int(out.ts[0]) == int(s.ts[tf - 1]),
           f"{tf}m bar is timestamped at its closing minute, not its opening one")
        # aggregation must not invent or drop range
        ok(close(float(out.high.max()), float(s.high.max())),
           f"{tf}m aggregation neither invents nor loses the series high")
        ok(close(float(out.low.min()), float(s.low.min())),
           f"{tf}m aggregation neither invents nor loses the series low")
        ok(close(float(out.volume.sum()), float(s.volume.sum())),
           f"{tf}m aggregation conserves total volume")

    # every aggregated bar's timestamp is strictly increasing and is a real minute
    out, _ = bars.resample(s, 15)
    ok(bool((np.diff(out.ts) > 0).all()), "aggregated timestamps are increasing")
    ok(set(out.ts.tolist()).issubset(set(s.ts.tolist())),
       "every aggregated timestamp is a real minute from the source series")


def check_session_anchoring() -> None:
    """Buckets are anchored per session, not to the wall clock."""
    a = session_ts(0, 9 * 60 + 15, 30)   # NSE-style open
    b = session_ts(1, 9 * 60, 30)        # MCX-style open
    ts = np.concatenate([a, b])
    s = Series.__new__(Series)
    s.instrument = "NIFTY"
    n = ts.size
    s.ts = ts
    s.open = np.arange(n, dtype=float) + 100.0
    s.close = s.open + 0.5
    s.high = s.open + 1.0
    s.low = s.open - 1.0
    s.volume = np.ones(n)
    out, st = bars.resample(s, 15)
    ok(st["bars"] == 4 and st["short_bars"] == 0,
       "two sessions with different open times both bucket from their own open")
    ok(close(out.open[2], s.open[30]),
       "the second session's first bar starts at that session's first minute")
    ok(st["spans_a_session"] is False,
       "a session-anchored bucket never merges two sessions")


def check_short_bars() -> None:
    """A session tail is a short bar, counted, never padded with tomorrow."""
    s = make_series(sessions=2, minutes=32)   # 32 = two 15s and a 2-minute tail
    out, st = bars.resample(s, 15)
    ok(st["short_bars"] == 2 and st["full_bars"] == 4,
       "each session's tail is one short bar, counted separately")
    ok(st["short_bar_pct"] == round(100.0 * 2 / 6, 2),
       "the short-bar percentage is reported, so a reader can discount them")
    ok(close(out.close[2], s.close[31]),
       "a short tail bar closes on its own session's last minute")
    ok(st["spans_a_session"] is False,
       "a short tail bar is not completed with the next session's minutes")

    # a mid-session feed gap also produces short bars rather than a spanning bar
    ts = np.concatenate([session_ts(0, 9 * 60 + 15, 10),
                         session_ts(0, 9 * 60 + 40, 10)])
    g = Series.__new__(Series)
    g.instrument = "NIFTY"
    g.ts = ts
    g.open = np.arange(ts.size, dtype=float) + 50.0
    g.close = g.open
    g.high = g.open + 1.0
    g.low = g.open - 1.0
    g.volume = np.ones(ts.size)
    _, gst = bars.resample(g, 15)
    ok(gst["short_bars"] >= 1 and gst["spans_a_session"] is False,
       "a mid-session feed gap yields short bars, not a bar that spans the gap")


def check_timeframe_conversions() -> None:
    ok(pool.horizon_bars(5) == 36 and pool.horizon_bars(15) == 12,
       "the three-hour hold is held constant in clock time across timeframes")
    ok(pool.horizon_bars(5) * 5 == pool.horizon_bars(15) * 15 == 180,
       "both timeframes hold for the same 180 minutes")
    ok(pool.min_bars_left(5) == 6 and pool.min_bars_left(15) == 2,
       "the 30-minute tail rule is converted per timeframe")
    ok(pool.opening_bars(15) >= 2 and pool.opening_bars(5) >= 4,
       "a candidate waits for the opening range to have closed at its timeframe")
    grid = study.stress_grid(15)
    delays = {r["entry_delay_bars"] for r in grid if r.get("entry_delay_bars")}
    ok(delays and all(d * 15 in (15, 30, 60) or d == 1 for d in delays),
       "entry-delay stress is expressed in minutes and converted to bars")
    g5 = {r["entry_delay_bars"] for r in study.stress_grid(5)
          if r.get("entry_delay_bars")}
    ok(max(g5) * 5 == max(delays) * 15 == 60,
       "the longest entry delay is the same 60 minutes at both timeframes")


# ---------------------------------------------------------------------------
# §2 pool and execution
# ---------------------------------------------------------------------------
def build_pool(tf: int = 15, **kw) -> pool.Pool:
    s = make_series(sessions=40, minutes=375)
    series, stats = bars.resample(s, tf)

    def fake_load(instrument: str, timeframe: int):
        return (series, stats) if int(timeframe) == tf else None

    real = bars.load
    bars.load = fake_load          # noqa: F811 - fixture injection, restored below
    try:
        return pool.build("NIFTY", tf, **kw)
    finally:
        bars.load = real


def check_pool() -> None:
    p = build_pool(15)
    ok(p is not None and len(p) > 0, "a 15-minute pool builds from aggregated bars")
    n_long = int((p.side == outcomes.LONG).sum())
    n_short = int((p.side == outcomes.SHORT).sum())
    ok(n_long == n_short > 0,
       "every eligible bar produces both a LONG and a SHORT candidate")
    ok(p.timeframe == 15 and p.bar_stats["timeframe_minutes"] == 15,
       "the pool carries its timeframe and its aggregation statistics")
    ok(int(p.ts.size) == int(p.idx.size),
       "one timestamp per candidate, so a cohort cannot be mis-dated")
    summ = pool.summary(p)
    ok(summ["horizon_minutes"] == 180 and summ["horizon_bars"] == 12,
       "the pool summary states the hold in minutes as well as bars")
    ok("short_bar_pct" in summ,
       "the pool summary carries the short-bar share of its own bars")


def check_execution_semantics() -> None:
    """Next-open entry, later bars only, and the same-bar tie is the stop."""
    n = 40
    ts = session_ts(0, 9 * 60 + 15, n)
    high = np.full(n, 100.5)
    low = np.full(n, 99.5)
    open_ = np.full(n, 100.0)
    idx = np.array([5], dtype=np.int64)
    side = np.array([outcomes.LONG], dtype=np.int8)
    atr = np.full(n, 1.0)
    last = np.full(1, n - 1, dtype=np.int64)

    # a bar that contains BOTH the stop and the target
    open_[6] = 100.0
    high[7] = 101.6      # target at 101.5 (1.5R on a 1-point stop)
    low[7] = 98.9        # stop at 99.0
    out = outcomes.resolve("NIFTY", high, low, open_, ts, idx, side, atr, last,
                           horizon=12)
    ok(close(out.entry[0], open_[6]),
       "entry is the open of the bar AFTER the decision bar, unadjusted")
    ok(out.t1_before_sl[0] == 0 and out.outcome[0] == outcomes.SL_FIRST,
       "a bar containing both the stop and the target is resolved as the stop")

    # nothing before the fill can resolve the trade
    high2 = np.full(n, 100.2)
    low2 = np.full(n, 99.8)
    high2[:6] = 200.0     # a huge favourable move BEFORE the entry
    low2[:6] = 50.0
    out2 = outcomes.resolve("NIFTY", high2, low2, open_, ts, idx, side, atr, last,
                            horizon=12)
    ok(out2.outcome[0] == outcomes.TIMEOUT,
       "bars at or before the decision bar cannot resolve the outcome")

    # the session close flattens the trade: tomorrow's bars are unreachable
    ts2 = np.concatenate([session_ts(0, 9 * 60 + 15, 10),
                          session_ts(1, 9 * 60 + 15, 10)])
    h = np.full(20, 100.2)
    lo = np.full(20, 99.8)
    op = np.full(20, 100.0)
    h[12:] = 500.0        # a gap up in the NEXT session
    out3 = outcomes.resolve(
        "NIFTY", h, lo, op, ts2,
        np.array([5]), np.array([outcomes.LONG], dtype=np.int8),
        np.full(20, 1.0), np.array([9]), horizon=12,
    )
    ok(out3.t1_before_sl[0] == 0,
       "a trade is flattened in its own session and cannot be paid by a gap")


def check_costs() -> None:
    """Slippage is charged once, through the shared model, not twice."""
    n = 30
    ts = session_ts(0, 9 * 60 + 15, n)
    high = np.full(n, 100.5)
    low = np.full(n, 99.5)
    open_ = np.full(n, 100.0)
    idx = np.array([5], dtype=np.int64)
    side = np.array([outcomes.LONG], dtype=np.int8)
    atr = np.full(n, 1.0)
    last = np.full(1, n - 1, dtype=np.int64)
    base = outcomes.resolve("NIFTY", high, low, open_, ts, idx, side, atr, last,
                            horizon=10)
    ok(close(base.entry[0], 100.0),
       "the fill price is the raw next open; slippage is not added to it")
    worse = outcomes.resolve("NIFTY", high, low, open_, ts, idx, side, atr, last,
                             horizon=10, slippage_points=5.0)
    ok(close(worse.entry[0], base.entry[0]),
       "more slippage does not move the fill price, only the charge")
    ok(worse.cost_points[0] > base.cost_points[0],
       "more slippage costs more points")
    ok(close(worse.net_points[0], worse.gross_points[0] - worse.cost_points[0]),
       "net = gross - cost, with the cost counted exactly once")
    direct = futcosts.round_trip(
        "NIFTY", entry=float(base.entry[0]), exit_price=float(base.exit_price[0]),
        lot_size=int(get_spec("NIFTY").lot_size or 1), lots=1, slippage_points=None,
    )
    ok(abs(float(direct["cost_points"]) - float(base.cost_points[0])) < 0.05,
       "the charged cost reconciles with a direct phase19.futcosts call")
    ok(close(base.net_r[0], base.net_points[0] / base.risk[0]),
       "net R is net points over the risk actually taken")


def check_no_leakage() -> None:
    """Features at a bar use that bar and earlier, never a later one."""
    s = make_series(sessions=6, minutes=90)
    series, _ = bars.resample(s, 15)
    feat_a = features.build(series)
    cut = len(series) - 3
    trimmed = Series.__new__(Series)
    trimmed.instrument = series.instrument
    for f in ("ts", "open", "high", "low", "close", "volume"):
        setattr(trimmed, f, getattr(series, f)[:cut])
    feat_b = features.build(trimmed)
    same = True
    for key in ("atr", "momentum_atr", "vol_expansion", "trend_15m", "vwap_side"):
        a = np.asarray(feat_a[key])[:cut]
        b = np.asarray(feat_b[key])
        fa = np.isfinite(a) if a.dtype.kind == "f" else np.ones(a.size, bool)
        fb = np.isfinite(b) if b.dtype.kind == "f" else np.ones(b.size, bool)
        same &= bool((fa == fb).all()) and bool((a[fa] == b[fb]).all())
    ok(same, "deleting the last bars changes no earlier feature value")


# ---------------------------------------------------------------------------
# §2b vocabulary
# ---------------------------------------------------------------------------
def check_conditions() -> None:
    names = conditions.names()
    ok(all("_5m_" not in n and "_15m_" not in n for n in names),
       "no condition name claims a minute window it no longer has")
    ok("trend_5bar_agrees" in names and "trend_15bar_agrees" in names,
       "timeframe-relative trend conditions are renamed in bars, not minutes")
    p = build_pool(15)
    masks = conditions.masks(p.feat, p.side, p.timeframe)
    ok(set(masks) == set(names),
       "every declared condition produces a mask, and no mask is undeclared")
    ok(all(m.dtype == bool and m.size == len(p) for m in masks.values()),
       "every mask is boolean and aligned to the candidate order")
    ok(any(m.any() for m in masks.values()),
       "the vocabulary selects something on a real pool")
    labels = conditions.window_labels(15)
    ok(labels.get("trend_5bar_agrees", "").endswith("75 minutes")
       or "75" in labels.get("trend_5bar_agrees", ""),
       "a 5-bar window is reported as 75 minutes at the 15-minute timeframe")
    ok("25" in conditions.window_labels(5).get("trend_5bar_agrees", ""),
       "the same 5-bar window is reported as 25 minutes at 5 minutes")


# ---------------------------------------------------------------------------
# §3 chronology, selection and multiple testing
# ---------------------------------------------------------------------------
def check_windows_and_selection() -> None:
    p = build_pool(15)
    s = discover.Search(p, 1.0)
    dev, val, hold = s.win[discover.DEV], s.win[discover.VAL], s.win[discover.HOLDOUT]
    ok(dev[1] <= val[0] and val[1] <= hold[0],
       "development, validation and the holdout are chronological, not shuffled")
    ok(not bool((s.dev & s.val).any()) and not bool((s.dev & s.hold).any())
       and not bool((s.val & s.hold).any()),
       "no candidate appears in two windows")
    rows = s.run()
    ok(s.tests > 0, "the search counts every hypothesis it evaluated")
    ok(s.tests >= len(rows),
       "the hypothesis count is at least the number of rows kept")
    ok(all(r["timeframe_minutes"] == 15 for r in rows),
       "every discovered row carries the timeframe it was found at")
    for r in rows:
        gates = r.get("development") or {}
        ok(float(gates.get("avg_net_r") or -1) >= discover.MIN_DEV_AVG_NET_R,
           "a kept row cleared the development bar on development data")
    ok(all("holdout" not in r for r in rows),
       "discovery never reads the holdout: selection is development-only")


def check_fdr() -> None:
    p_values = [0.001, 0.02, 0.2, 0.6]
    few = discover.benjamini_hochberg(p_values, tests=4)
    many = discover.benjamini_hochberg(p_values, tests=50_000)
    ok(sum(few) >= sum(many),
       "the same p-values survive less often against a larger hypothesis count")
    ok(sum(many) == 0 or few[0],
       "the FDR correction uses the honest denominator, not the survivor count")
    ok(discover.benjamini_hochberg([], tests=0) == [],
       "an empty search corrects to no survivors rather than failing")


def check_gate() -> None:
    good = {
        "development": {"avg_net_r": 0.2, "trades": 500},
        "validation": {"avg_net_r": 0.15, "trades": 200},
        "holdout": {"avg_net_r": 0.12, "trades": 250, "profit_factor": 1.4},
        "walk_forward": {"folds_scored": 5, "folds_positive": 5, "stable": True},
        "cost_sensitivity": [
            {"variant": "slippage_points=2.0", "avg_net_r": 0.05,
             "survives": True},
            {"variant": "spread_multiplier=1.5", "avg_net_r": 0.03,
             "survives": True},
        ],
        "parameter_perturbation": [{"avg_net_r": 0.08, "survives": True}],
        "complexity": 2,
        "dev_gate_passed": True,
    }
    status, reasons = rank.gate(good, fdr_survivor=True)
    ok(status == rank.VALIDATED and not reasons,
       "a candidate positive in all three windows and every stress clears the gate")
    ok(rank.research_label(status) == RESEARCH_LEAD,
       "clearing the gate publishes a RESEARCH_LEAD, never a live promotion")

    for label, mutate in (
        ("negative holdout", lambda r: r["holdout"].update(avg_net_r=-0.01)),
        ("thin holdout", lambda r: r["holdout"].update(trades=40)),
        ("holdout PF below 1", lambda r: r["holdout"].update(profit_factor=0.9)),
        ("unstable walk-forward",
         lambda r: r["walk_forward"].update(folds_positive=2, stable=False)),
        ("negative validation", lambda r: r["validation"].update(avg_net_r=-0.02)),
        ("outlier-dependent", lambda r: r["holdout"].update(
            outlier_top1_contribution_pct=80.0)),
    ):
        r = json.loads(json.dumps(good))
        mutate(r)
        st, why = rank.gate(r, fdr_survivor=True)
        ok(st != rank.VALIDATED and why, f"the gate rejects a {label} candidate")

    r = json.loads(json.dumps(good))
    st, why = rank.gate(r, fdr_survivor=False)
    ok(st != rank.VALIDATED
       and any("multiple-testing" in w for w in why),
       "failing the multiple-testing correction alone still blocks the gate")

    r = json.loads(json.dumps(good))
    r["cost_sensitivity"] = [
        {"label": "slippage_2.0_points", "avg_net_r": -0.01, "slippage_points": 2.0},
    ]
    st, why = rank.gate(r, fdr_survivor=True)
    ok(st != rank.VALIDATED and any("slippage" in w for w in why),
       "a candidate that dies inside the unmeasured spread is not a lead")

    r = json.loads(json.dumps(good))
    r["cost_sensitivity"] = [{"label": "spread_x1.5", "avg_net_r": 0.03}]
    st, why = rank.gate(r, fdr_survivor=True)
    ok(st != rank.VALIDATED and any("slippage" in w for w in why),
       "an untested slippage variant is treated as a failure, not as a pass")


def check_walk_forward() -> None:
    p = build_pool(15)
    s = discover.Search(p, 1.0)
    rows = s.run()
    if not rows:
        ok(True, "walk-forward is skipped when the fixture yields no cohort")
        return
    wf = study.walk_forward(p, s.masks, rows[0])
    ok(wf["folds_scored"] <= study.WALK_FORWARD_FOLDS,
       "walk-forward scores at most the declared number of folds")
    ok(wf["folds_positive"] <= wf["folds_scored"],
       "positive folds cannot exceed scored folds")
    ok(all(f["from_ts"] <= f["to_ts"] for f in wf["folds"]),
       "each walk-forward fold is a forward-in-time window")
    tos = [f["to_ts"] for f in wf["folds"]]
    ok(tos == sorted(tos), "walk-forward folds run in chronological order")


# ---------------------------------------------------------------------------
# §4 report, artefacts and the stopping rule
# ---------------------------------------------------------------------------
def check_execution_note() -> None:
    note = outcomes_note.execution_note()
    text = json.dumps(note).lower()
    for needle, label in (
        ("next", "the note says the fill is the next bar's open"),
        ("stop", "the note says the same-bar tie is resolved as the stop"),
        ("spread", "the note says the futures spread is unmeasured"),
        ("phase19", "the note names the shared cost model it used"),
        ("phase24", "the note names the shared outcome resolver it used"),
    ):
        ok(needle in text, label)


def check_report_and_stop_rule() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        no_data = {
            "data_status": "NO_USABLE_HISTORY",
            "timeframe_results": [],
        }
        c = report.conclusion(no_data)
        ok(c["verdict"] == "REQUIRES_MORE_DATA" and c["stop_rule_triggered"] is False,
           "missing history is REQUIRES_MORE_DATA and does NOT trigger the stop rule")

        block = {
            "timeframe_minutes": 15,
            "hypotheses_evaluated": 500,
            "candidates_surviving_development": 10,
            "diagnostics": {
                "positive_all_three_windows": 3,
                "holdout_sample_adequate": 1,
                "walk_forward_stable": 1,
                "survives_every_stress_variant": 0,
                "fdr_survivors": 0,
                "research_leads": 0,
            },
            "ranked": [],
            "top10": [],
            "pools": [],
        }
        failed = {"data_status": "OK", "timeframe_results": [block, dict(block,
                  timeframe_minutes=5)]}
        c = report.conclusion(failed)
        ok(c["verdict"] == "REJECTED" and c["headline"] == report.NO_EDGE,
           "both timeframes failing produces the explicit no-edge headline")
        ok(c["stop_rule_triggered"] is True,
           "the pre-committed stopping rule fires on the gate's own result")
        ok("3 were positive on development" in c["statement"],
           "the rejection counts how far the search got instead of saying nothing "
           "was ever positive")
        ok(any("does not say the market has no edge" in w
               for w in c["what_this_does_not_say"]),
           "the rejection states what it does not claim")

        lead = {
            "strategy_id": "P27_X", "timeframe_minutes": 15, "instrument": "NIFTY",
            "side": "LONG", "conditions": ["a"], "complexity": 1,
            "stop_band_atr": 2.0, "status": RESEARCH_LEAD,
            "score": 1.0, "holdout": {"avg_net_r": 0.2}, "failed_clauses": [],
        }
        won = {"data_status": "OK", "timeframe_results": [
            dict(block, ranked=[lead], top10=[lead], research_leads=[lead])]}
        c = report.conclusion(won)
        ok(c["stop_rule_triggered"] is False and c["research_label"] == RESEARCH_LEAD,
           "a surviving lead does not trigger the stop rule and stays a lead")
        ok("promotion" in json.dumps(c),
           "even a surviving lead is reported as not a promotion")

        payload = {
            "version": "T", "nan": float("nan"), "arr": np.arange(3),
            "f": np.float64(1.5), "i": np.int64(2),
        }
        path = os.path.join(tmp, "p27_test.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report.jsonable(payload), fh, allow_nan=False, default=str)
        with open(path, encoding="utf-8") as fh:
            back = json.load(fh)
        ok(back["nan"] is None and back["arr"] == [0, 1, 2],
           "artefacts are strict JSON: NaN is null and numpy types are plain")
        ok(isinstance(back["f"], float) and isinstance(back["i"], int),
           "numpy scalars are written as JSON numbers")


def check_claims_present() -> None:
    for text, label in (
        (SPREAD_CLAIM, "the package states the futures spread is unmeasured"),
        (RESAMPLE_CLAIM, "the package states where the higher-timeframe bars came "
                         "from"),
        (STOP_RULE, "the package carries the pre-committed stopping rule"),
    ):
        ok(isinstance(text, str) and len(text) > 40, label)
    ok("no further strategy phase" in STOP_RULE,
       "the stopping rule says explicitly that no further phase follows a failure")


# ---------------------------------------------------------------------------
# isolation: options frozen, no order path, no tick hook
# ---------------------------------------------------------------------------
def check_isolation() -> None:
    pkg = os.path.join(os.path.dirname(__file__), "app", "research", "phase27")
    files = sorted(f for f in os.listdir(pkg) if f.endswith(".py"))
    joined = "".join(
        open(os.path.join(pkg, f), encoding="utf-8").read() for f in files
    )
    for banned in ("place_order", "broker.buy", "order_router", "on_tick",
                   "register_hook", "paper_entry"):
        ok(banned not in joined, f"no module in Phase 27 references {banned!r}")
    for frozen in ("phase25", "phase26", "option_chain", "chain_store",
                   "option_costs"):
        ok(frozen not in joined,
           f"Phase 27 never touches {frozen!r}: option logic and capture stay frozen")
    ok("phase24" in joined and "phase19" in joined,
       "Phase 27 reuses the shared resolver and the shared cost model")

    src = open(os.path.join(pkg, "service.py"), encoding="utf-8").read()
    for banned in ("report.run", "study.geometry_sweep", "pool.build"):
        ok(banned not in src,
           f"the read-only service cannot start a study via {banned!r}")
    unavailable = service._unavailable()
    ok(unavailable["available"] is False and unavailable["paper_only"] is True,
       "an un-run study reports unavailable, never an empty result")
    ok(STOP_RULE in json.dumps(unavailable, ensure_ascii=False),
       "every service payload carries the stopping rule")


def check_service_reads() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(tmp, exist_ok=True)
        ok(report.latest(tmp) is None,
           "a directory with no report reads as no report, not as a zero result")
        ok(report.latest_ranked(tmp) == [] and report.latest_geometry(tmp) == [],
           "missing artefacts read as empty lists rather than raising")
        ok(report.latest_comparison(tmp) == {},
           "a missing comparison reads as empty rather than as a comparison")


def main() -> int:
    print("PHASE 27 SMOKE — HIGHER-TIMEFRAME FUTURES (5m / 15m)")
    print("-" * 70)
    check_resample()
    check_session_anchoring()
    check_short_bars()
    check_timeframe_conversions()
    check_pool()
    check_execution_semantics()
    check_costs()
    check_no_leakage()
    check_conditions()
    check_windows_and_selection()
    check_fdr()
    check_gate()
    check_walk_forward()
    check_execution_note()
    check_report_and_stop_rule()
    check_claims_present()
    check_isolation()
    check_service_reads()
    print("")
    if FAIL:
        print(f"{PASS} passed · {len(FAIL)} FAILED")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print(f"{PASS} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
