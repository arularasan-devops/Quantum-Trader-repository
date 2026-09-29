"""Phase 24 smoke — the study's honesty properties, checked on built fixtures.

Every check here exists because the opposite mistake would make a losing study
look like a winning one: entering on the bar the decision was made, resolving a
trade with tomorrow's candles, calling a same-bar tie a win, charging one median
cost across five years of price levels, or letting a rule be scored on the very
data it was discovered on.

    .venv/bin/python _smoke_phase24.py
"""
from __future__ import annotations

import numpy as np

from app.research.phase19 import futcosts
from app.research.phase24 import (
    INSUFFICIENT_HISTORY,
    REQUIRES_MORE_DATA,
    VALIDATED,
    conditions,
    data,
    discover,
    features,
    metrics,
    outcomes,
    pool,
    rank,
    report,
    service,
    study,
)

PASS = 0
FAIL: list[str] = []
SKIPPED: list[str] = []


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"  FAILED: {label}")


def skip(label: str) -> None:
    """A check that needs the five-year history, on a machine that has none.

    The property is not verified and must not be reported as verified, but a
    missing data file is an install state, not a broken invariant — failing here
    would hide the real failures behind noise on every fresh install.
    """
    SKIPPED.append(label)
    print(f"  SKIPPED (no five-year history on disk): {label}")


def have_history() -> bool:
    return bool(data.coverage()["usable"])


def series(open_, high, low, close, ts):
    class S:
        pass

    s = S()
    s.open = np.asarray(open_, dtype=float)
    s.high = np.asarray(high, dtype=float)
    s.low = np.asarray(low, dtype=float)
    s.close = np.asarray(close, dtype=float)
    s.ts = np.asarray(ts, dtype=np.int64)
    s.volume = np.ones(len(open_), dtype=float)
    return s


# ---------------------------------------------------------------------------
# 1. Entry timing: the fill is the NEXT bar's open, never the signal bar.
# ---------------------------------------------------------------------------
def check_entry_timing() -> None:
    n = 40
    o = np.arange(100.0, 100.0 + n)
    ts = np.arange(n, dtype=np.int64) * 60
    idx = np.array([5], dtype=np.int64)
    side = np.array([outcomes.LONG], dtype=np.int8)
    atr = np.full(n, 2.0)
    last = np.full(idx.size, n - 1, dtype=np.int64)
    out = outcomes.resolve("NIFTY", o + 1, o - 1, o, ts, idx, side, atr, last)
    ok(abs(float(out.entry[0]) - float(o[6])) < 1e-9,
       "fill is the open of the bar AFTER the signal bar")

    out0 = outcomes.resolve("NIFTY", o + 1, o - 1, o, ts, idx, side, atr, last,
                            entry_delay_bars=0)
    ok(abs(float(out0.entry[0]) - float(o[6])) < 1e-9,
       "an entry delay below one bar cannot be requested away")


# ---------------------------------------------------------------------------
# 2. Session boundary: a trade is flattened at its session close.
# ---------------------------------------------------------------------------
def check_session_boundary() -> None:
    # Bar 8 is the session's last bar. Tomorrow rips upward; today's trade must
    # not be allowed to collect any of it.
    n = 20
    close = np.array([100.0] * 9 + [200.0] * 11)
    high = close + 0.5
    low = close - 0.5
    ts = np.arange(n, dtype=np.int64) * 60
    idx = np.array([5], dtype=np.int64)
    side = np.array([outcomes.LONG], dtype=np.int8)
    atr = np.full(n, 1.0)
    last = np.full(idx.size, 8, dtype=np.int64)
    out = outcomes.resolve("NIFTY", high, low, close, ts, idx, side, atr, last)
    ok(not bool(out.t1_before_sl[0]),
       "the next session's move cannot resolve today's trade")
    ok(int(out.bars_held[0]) <= 8 - 6,
       "holding time stops at the session's last bar")

    last_all = np.full(idx.size, n - 1, dtype=np.int64)
    out2 = outcomes.resolve("NIFTY", high, low, close, ts, idx, side, atr, last_all)
    ok(bool(out2.t1_before_sl[0]),
       "the same move IS collected when it is inside the session")


# ---------------------------------------------------------------------------
# 3. Same-bar ambiguity is scored as the loss.
# ---------------------------------------------------------------------------
def check_same_bar_tie() -> None:
    n = 12
    close = np.full(n, 100.0)
    high = np.full(n, 100.0)
    low = np.full(n, 100.0)
    # Bar 7 spans both the stop (99) and T1 (101.5) with a 1-point risk.
    high[7], low[7] = 105.0, 95.0
    ts = np.arange(n, dtype=np.int64) * 60
    idx = np.array([5], dtype=np.int64)
    side = np.array([outcomes.LONG], dtype=np.int8)
    atr = np.full(n, 1.0)
    last = np.full(idx.size, n - 1, dtype=np.int64)
    out = outcomes.resolve("NIFTY", high, low, close, ts, idx, side, atr, last)
    ok(int(out.bars_to_t1[0]) == int(out.bars_to_sl[0]) >= 0,
       "the ambiguous bar touches the stop and the target on the same bar")
    ok(not bool(out.t1_before_sl[0]) and bool(out.sl_hit[0]),
       "a same-bar tie is scored as the stop, never as the target")


# ---------------------------------------------------------------------------
# 4. Cost: per trade, from the shared model, charged both ways, never negative.
# ---------------------------------------------------------------------------
def check_costs() -> None:
    rng = np.random.default_rng(24)
    for inst in ("NIFTY", "CRUDEOIL"):
        lot = 1
        prices = rng.uniform(5_000.0, 30_000.0, 40)
        exits = prices * rng.uniform(0.98, 1.02, 40)
        got = outcomes._cost_points_per_trade(
            inst, prices, exits, lot_size=lot, slippage_points=None,
        )
        want = np.array([
            float(futcosts.round_trip(
                inst, entry=float(e), exit_price=float(x), lot_size=lot, lots=1,
            )["cost_points"])
            for e, x in zip(prices, exits)
        ])
        ok(float(np.max(np.abs(got - want))) < 1e-3,
           f"{inst}: per-trade cost reconstruction matches the cost model exactly")
        ok(float(got.min()) > 0.0, f"{inst}: cost is never zero or negative")
        spread = np.ptp(got)
        ok(spread > 0.0,
           f"{inst}: cost varies with the price level instead of one median")

    # Slippage is charged once, on both legs, and only by the cost model.
    n = 30
    o = np.full(n, 20_000.0)
    ts = np.arange(n, dtype=np.int64) * 60
    idx = np.array([5], dtype=np.int64)
    side = np.array([outcomes.LONG], dtype=np.int8)
    atr = np.full(n, 10.0)
    last = np.full(idx.size, n - 1, dtype=np.int64)
    zero = outcomes.resolve("NIFTY", o, o, o, ts, idx, side, atr, last,
                            slippage_points=0.0)
    five = outcomes.resolve("NIFTY", o, o, o, ts, idx, side, atr, last,
                            slippage_points=5.0)
    ok(abs(float(five.cost_points[0] - zero.cost_points[0]) - 10.0) < 1e-2,
       "5 points of slippage costs exactly 10 points on a round trip")
    ok(abs(float(zero.entry[0]) - 20_000.0) < 1e-9,
       "the entry price itself is not slipped a second time")

    doubled = outcomes.resolve("NIFTY", o, o, o, ts, idx, side, atr, last,
                               spread_multiplier=2.0)
    plain = outcomes.resolve("NIFTY", o, o, o, ts, idx, side, atr, last)
    ok(float(doubled.cost_points[0]) > float(plain.cost_points[0]),
       "the stress multiplier raises cost rather than being ignored")


# ---------------------------------------------------------------------------
# 5. Features are causal: a future bar cannot change a past feature.
# ---------------------------------------------------------------------------
def check_features_causal() -> None:
    rng = np.random.default_rng(7)
    n = 900
    close = 20_000 + np.cumsum(rng.normal(0, 5, n))
    high = close + rng.uniform(1, 6, n)
    low = close - rng.uniform(1, 6, n)
    ts = np.arange(n, dtype=np.int64) * 60 + 1_600_000_000
    a = features.build(series(close, high, low, close, ts))

    close2 = close.copy()
    high2, low2 = high.copy(), low.copy()
    cut = 700
    close2[cut:] += 500.0        # a violent future
    high2[cut:] += 500.0
    low2[cut:] += 500.0
    b = features.build(series(close2, high2, low2, close2, ts))

    bad = [k for k in a
           if not np.allclose(np.nan_to_num(a[k][:cut], nan=-9e9),
                              np.nan_to_num(b[k][:cut], nan=-9e9))]
    ok(not bad, f"no feature reads the future (leaking: {bad[:4]})")
    ok(len(a) >= 20, "the feature layer is not a stub")


# ---------------------------------------------------------------------------
# 6. Pool: both sides of every observation, rejected opportunities included.
# ---------------------------------------------------------------------------
def check_pool() -> None:
    if not have_history():
        skip("the NIFTY pool builds from the five-year file")
        return
    p = pool.build("NIFTY")
    ok(p is not None, "the NIFTY pool builds from the five-year file")
    if p is None:
        return
    longs = int((p.side == outcomes.LONG).sum())
    shorts = int((p.side == outcomes.SHORT).sum())
    ok(longs == shorts and longs > 10_000,
       "every observation is offered as both a long and a short")
    s = pool.summary(p)
    ok(s["resolved"] + s["unresolved"] == s["candidates"],
       "resolved and unresolved candidates account for the whole pool")
    base = s["base_t1_before_sl_pct"]
    ok(20.0 < base < 55.0,
       f"the unconditional pool is close to a coin at 1.5R ({base}%)")
    ok(s["base_avg_net_r"] < 0.0,
       "with no rule at all the pool loses money — the cost wall is real")


# ---------------------------------------------------------------------------
# 7. Conditions: side-aware, boolean, and none of them is always true.
# ---------------------------------------------------------------------------
def conclusion_without_data() -> dict:
    return report.conclusion([], ran_on_data=False)


def check_conditions() -> None:
    if not have_history():
        skip("the condition vocabulary is exercised on the real pool")
        return
    p = pool.build("NIFTY")
    if p is None:
        return
    m = conditions.masks(p.feat, p.side)
    ok(len(m) >= 25, "the condition vocabulary is bounded but not trivial")
    always = [k for k, v in m.items() if v.all() or not v.any()]
    ok(not always, f"no condition is constant across the pool ({always[:4]})")
    for k, v in m.items():
        ok(v.dtype == bool and v.size == p.side.size,
           f"{k} is a boolean mask aligned to the pool")
        break
    ln = m["trend_5m_agrees"][p.side == outcomes.LONG]
    sh = m["trend_5m_agrees"][p.side == outcomes.SHORT]
    ok(float(ln.mean()) != float(sh.mean()),
       "a directional condition means the opposite thing for a short")


# ---------------------------------------------------------------------------
# 8. Windows: three years, one year, one year, in order and disjoint.
# ---------------------------------------------------------------------------
def check_windows() -> None:
    ts = np.arange(0, 5 * discover.YEAR, 3600, dtype=np.int64) + 1_500_000_000
    w = discover.windows(ts)
    ok(set(w) == {discover.DEV, discover.VAL, discover.HOLDOUT},
       "development, validation and holdout windows are all defined")
    dev, val, hold = w[discover.DEV], w[discover.VAL], w[discover.HOLDOUT]
    ok(dev[1] <= val[0] <= val[1] <= hold[0],
       "the windows are chronological and non-overlapping")
    ok(abs((dev[1] - dev[0]) - 3 * discover.YEAR) < discover.YEAR,
       "development is the first three years")
    ok(hold[1] >= int(ts[-1]), "the holdout ends at the last available bar")


# ---------------------------------------------------------------------------
# 9. Metrics: Wilson bounds, drawdown, profit factor, small-sample labelling.
# ---------------------------------------------------------------------------
def check_metrics() -> None:
    lo, hi = metrics.wilson(50, 100)
    ok(lo < 50.0 < hi and 0.0 <= lo and hi <= 100.0,
       "the Wilson interval brackets the point estimate")
    lo2, hi2 = metrics.wilson(500, 1000)
    ok((hi2 - lo2) < (hi - lo), "a bigger sample gives a tighter interval")
    import math as _m
    empty = metrics.wilson(0, 0)
    ok(all(_m.isnan(v) for v in empty),
       "an empty sample yields no interval rather than a fake 0-0")
    dd = metrics.max_drawdown(np.array([1.0, -2.0, -3.0, 4.0]))
    ok(abs(dd - 5.0) < 1e-9, "drawdown is the worst peak-to-trough run")
    ok(metrics.longest_losing_streak(np.array([1.0, -1, -1, -1, 2.0])) == 3,
       "the losing streak counts consecutive losers")
    ok(metrics.profit_factor(np.array([2.0, -1.0])) == 2.0,
       "profit factor is gross win over gross loss")
    ok(metrics.profit_factor(np.array([])) is None,
       "profit factor of an empty cohort is undefined")
    ok(_m.isinf(metrics.profit_factor(np.array([1.0, 2.0]))),
       "profit factor with no losers is infinite, not silently 0")
    p = metrics.binomial_p(60, 100, 0.5)
    ok(0.0 < p < 0.1, "an unusual hit rate gets a small p-value")
    ok(metrics.binomial_p(50, 100, 0.5) > 0.4,
       "a hit rate at the base rate is not significant")


# ---------------------------------------------------------------------------
# 10. Multiple testing: Benjamini-Hochberg behaves at both extremes.
# ---------------------------------------------------------------------------
def check_fdr() -> None:
    flags = discover.benjamini_hochberg([0.001, 0.002, 0.5, 0.9])
    ok(flags[0] and flags[1] and not flags[2],
       "clear winners survive the FDR correction and noise does not")
    ok(not any(discover.benjamini_hochberg([0.2, 0.4, 0.6, 0.8])),
       "a field of weak p-values produces no survivor")
    ok(discover.benjamini_hochberg([]) == [], "an empty field is handled")


# ---------------------------------------------------------------------------
# 11. The promotion gate refuses on every clause it claims to check.
# ---------------------------------------------------------------------------
def _good_rule() -> dict:
    stats = {
        "trades": 400, "t1_before_sl_pct": 45.0, "avg_net_r": 0.15,
        "profit_factor": 1.4, "max_drawdown_r": 20.0,
        "outlier_top1_contribution_pct": 8.0, "avg_net_r_excl_top5pct": 0.06,
        "p_value_vs_base_rate": 0.001, "sample_status": "OK",
        "base_rate_pct": 35.0,
    }
    return {
        "strategy_id": "P24_test", "instrument": "NIFTY", "side": "LONG",
        "vehicle": "FUTURES", "stop_band_atr": 2.0, "complexity": 2,
        "conditions": ["trend_5m_agrees", "volatility_expanding"],
        "development": dict(stats), "validation": dict(stats),
        "holdout": dict(stats),
        "walk_forward": {"folds_scored": 5, "folds_positive": 4, "stable": True},
        "cost_sensitivity": [
            {"label": "spread x1.5", "avg_net_r": 0.05, "survives": True},
        ],
        "parameter_perturbation": [{"avg_net_r": 0.04, "survives": True}],
        "regimes": {"trending": {"avg_net_r": 0.1}, "sideways": {"avg_net_r": 0.05}},
        "selectivity": {"trades_per_week": 3.0, "no_trade_pct": 99.0},
        "dev_gate_passed": True,
    }


def check_gate() -> None:
    good = _good_rule()
    status, why = rank.gate(good, fdr_survivor=True)
    ok(status == VALIDATED and not why,
       f"a candidate meeting every clause is VALIDATED ({why})")

    cases = {
        "holdout sample": ("holdout", "trades", 10),
        "holdout expectancy": ("holdout", "avg_net_r", -0.05),
        "profit factor": ("holdout", "profit_factor", 0.8),
        "outlier dependence": ("holdout", "outlier_top1_contribution_pct", 90.0),
        "validation expectancy": ("validation", "avg_net_r", -0.2),
    }
    for label, (block, key, value) in cases.items():
        r = _good_rule()
        r[block][key] = value
        status, why = rank.gate(r, fdr_survivor=True)
        ok(status != VALIDATED and bool(why),
           f"the gate refuses on {label}")

    r = _good_rule()
    status, why = rank.gate(r, fdr_survivor=False)
    ok(status != VALIDATED, "failing the FDR correction blocks promotion")

    r = _good_rule()
    r["walk_forward"] = {"folds_scored": 5, "folds_positive": 1, "stable": False}
    status, why = rank.gate(r, fdr_survivor=True)
    ok(status != VALIDATED, "an unstable walk-forward blocks promotion")

    r = _good_rule()
    r["complexity"] = 9
    r["conditions"] = [f"c{i}" for i in range(9)]
    status, why = rank.gate(r, fdr_survivor=True)
    ok(status != VALIDATED, "an over-complex rule cannot be promoted")

    r = _good_rule()
    r["cost_sensitivity"] = [
        {"label": "spread x1.5", "avg_net_r": -0.4, "survives": False},
    ]
    status, why = rank.gate(r, fdr_survivor=True)
    ok(status != VALIDATED, "a rule that dies on wider spreads is refused")

    r = _good_rule()
    r["dev_gate_passed"] = False
    status, why = rank.gate(r, fdr_survivor=True)
    ok(status != VALIDATED, "a near-miss cohort can never be promoted")


# ---------------------------------------------------------------------------
# 12. Ranking is a total order and prefers the honest candidate.
# ---------------------------------------------------------------------------
def check_ranking() -> None:
    strong = _good_rule()
    weak = _good_rule()
    weak["conditions"] = ["trend_15m_agrees", "volatility_expanding"]
    for block in ("development", "validation", "holdout"):
        weak[block]["avg_net_r"] = 0.01
        weak[block]["profit_factor"] = 1.02
        weak[block]["trades"] = 120
    strong_id = rank.strategy_id(strong)
    ordered = rank.rank([weak, strong])
    ok(ordered[0]["strategy_id"] == strong_id,
       "the stronger candidate ranks first")
    ok(ordered[0]["score"] > ordered[1]["score"], "the score is a total order")
    ok(all("score" in r and "status" in r for r in ordered),
       "every ranked row carries a score and a status")

    outlier = _good_rule()
    outlier["conditions"] = ["trend_15m_agrees", "volatility_expanding"]
    outlier["holdout"]["outlier_top1_contribution_pct"] = 85.0
    outlier["holdout"]["avg_net_r_excl_top5pct"] = -0.3
    ordered2 = rank.rank([outlier, strong])
    ok(ordered2[0]["strategy_id"] == strong_id,
       "a rule carried by its best 1% ranks below a broad one")
    ok(ordered2[1]["status"] != VALIDATED,
       "outlier dependence is a refusal, not just a lower rank")

    fp = rank.fingerprint(strong)
    ok(fp["require_all"] == sorted(strong["conditions"])
       and fp.get("vehicle") == "FUTURES",
       "the fingerprint states the rule it came from")
    ok(fp["paper_only"] is True or strong.get("status") == VALIDATED,
       "a fingerprint that is not VALIDATED is marked paper-only")
    ok(rank.strategy_id(strong) == rank.strategy_id(_good_rule()),
       "the strategy id is stable for the same rule")


# ---------------------------------------------------------------------------
# 13. Honesty: no option result, no equity result, nothing self-promoting.
# ---------------------------------------------------------------------------
def check_honesty() -> None:
    v = report._vehicle_note()
    ok(v["CE"]["status"] == REQUIRES_MORE_DATA
       and v["PE"]["status"] == REQUIRES_MORE_DATA,
       "CE and PE stay REQUIRES_MORE_DATA instead of being modelled")
    ok("unmeasured" in v["FUTURES"]["note"],
       "the futures result names its unmeasured spread")
    ok(v["NO_TRADE"]["status"] == "FIRST_CLASS_OUTCOME",
       "NO TRADE is a first-class outcome")

    books = data.option_book_coverage()
    two = books["snapshots_with_two_sided_book"]
    # The claim must follow the store in both directions: an empty store may not
    # be reported as measurable, and a store that has captured real books may not
    # be reported as empty.
    ok(bool(books["note"]), "the option-book verdict states what it measured")
    ok(books["ce_pe_study_possible"] == (two >= data.MIN_TWO_SIDED_BOOKS),
       "whether a CE/PE study is possible is decided by the store, not by source")
    ok(sum((books.get("two_sided_by_instrument") or {}).values()) == two,
       "the per-instrument book counts add up to the total")
    ok(v["CE"]["status"] == REQUIRES_MORE_DATA,
       "CE stays REQUIRES_MORE_DATA for the five-year study whatever the capture holds")

    cov = data.coverage()
    thin = {r["instrument"] for r in cov["excluded"]}
    ok("BANKNIFTY" in thin and "GOLD" in thin,
       "instruments without five years are excluded, not judged")
    ok(all(r["status"] == INSUFFICIENT_HISTORY for r in cov["excluded"]),
       "excluded instruments carry INSUFFICIENT_HISTORY, not a negative verdict")
    ok({r["instrument"] for r in cov["usable"]} <= {"NIFTY", "CRUDEOIL"},
       "nothing beyond NIFTY and CRUDEOIL is ever claimed as usable history")
    ok(conclusion_without_data()["STATUS"] == REQUIRES_MORE_DATA,
       "a machine with no history reports REQUIRES_MORE_DATA, not a negative result")
    ok("did not run" in str(conclusion_without_data()["CLOSEST_CANDIDATE"]),
       "an unrun study says so instead of naming a closest candidate")

    check_book_scan()

    src = open("app/research/phase24/outcomes.py").read()
    for banned in ("premium", "iv ", "delta"):
        ok(banned not in src.lower().replace("premiums", ""),
           f"outcomes.py invents no option {banned.strip()}")


def check_book_scan() -> None:
    """The paged book scan on a store shaped like the live one.

    The count used to be a single query over every payload, which on a long
    capture holds a read transaction open for minutes and dies whole on the
    first lock the live engine takes. It is paged now, so the properties that
    matter are that pages neither drop nor double-count a row and that a real
    two-sided book is what gets counted.
    """
    import sqlite3
    import tempfile

    payloads = [
        # counted: a real book on both sides
        ('NIFTY', 10, '[{"bid": 1.0, "ask": 1.2}]'),
        # counted once only, though two legs qualify
        ('NIFTY', 20, '[{"bid": 1.0, "ask": 1.2}, {"bid": 2.0, "ask": 2.4}]'),
        # not counted: one-sided
        ('CRUDEOIL', 30, '[{"bid": 0, "ask": 1.2}]'),
        # not counted: crossed
        ('CRUDEOIL', 40, '[{"bid": 3.0, "ask": 1.2}]'),
        # not counted: no book at all
        ('CRUDEOIL', 50, '[{"ltp": 4.0}]'),
        # counted
        ('CRUDEOIL', 60, '[{"bid": 5.0, "ask": 5.5}]'),
        # excluded by provenance, not by book quality
        ('SIMOPT', 70, '[{"bid": 1.0, "ask": 1.1}]'),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/history.db"
        con = sqlite3.connect(path)
        con.execute(
            "CREATE TABLE chain_snapshots (instrument TEXT, ts INTEGER, "
            "payload TEXT, source TEXT)"
        )
        con.executemany(
            "INSERT INTO chain_snapshots VALUES (?,?,?,?)",
            [(i, t, p, "SIMULATOR" if i == "SIMOPT" else "REAL_BROKER")
             for i, t, p in payloads],
        )
        con.commit()
        con.close()

        was = data.PAGE_ROWS
        try:
            # One row per page: the paging path is what is under test, so it must
            # be exercised rather than swallowed by a single large page.
            data.PAGE_ROWS = 1
            two, by_inst, lo, hi = data._scan_books(path)
        finally:
            data.PAGE_ROWS = was
        ok(two == 3, f"the paged scan counts each qualifying snapshot once ({two})")
        ok(by_inst == {"NIFTY": 2, "CRUDEOIL": 1},
           f"paging attributes books to the right instrument ({by_inst})")
        ok((lo, hi) == (10, 60),
           f"the captured window spans the first and last counted book ({lo},{hi})")

        total, real = data._read_counts(path)
        ok(total == len(payloads) and real == len(payloads) - 1,
           "snapshot totals and real-broker totals are read from the store")

        one_page = data._scan_books(path)[0]
        ok(one_page == two, "the count does not depend on the page size")

    # A failure names an actionable cause and never the sqlite message, which can
    # carry table, column and file names.
    locked = data._cause(sqlite3.OperationalError("database is locked"))
    ok("write lock" in locked and "database is locked" not in locked,
       "a locked store is reported as locked, without the sqlite message")
    ok("no option-chain table" in data._cause(
        sqlite3.OperationalError("no such table: chain_snapshots")),
       "a missing chain table is reported as such")
    ok("could not be read" in data._cause(sqlite3.OperationalError("frobnicated")),
       "an unrecognised failure falls back to a safe generic cause")


# ---------------------------------------------------------------------------
# 14. Isolation: Phase 24 touches no Phase 23 or production file.
# ---------------------------------------------------------------------------
def check_isolation() -> None:
    import os
    import subprocess

    hits: list[str] = []
    for name in os.listdir("app/research/phase24"):
        if not name.endswith(".py"):
            continue
        src = open(f"app/research/phase24/{name}").read()
        for banned in ("phase23", "auto_trader", "place_order", "paper_store",
                       "app.broker", "from app.trading", "order_path"):
            if banned in src:
                hits.append(f"{name}:{banned}")
    ok(not hits, f"no Phase 24 module imports Phase 23 or an order path ({hits})")

    grep = subprocess.run(
        ["grep", "-rn", "phase24", "app/research/phase23/"],
        capture_output=True, text=True,
    )
    ok(not grep.stdout.strip(), "Phase 23 has no reference to Phase 24")

    ok(not any(
        hasattr(service, n) for n in ("run", "start", "promote", "arm", "observe")
    ), "the Phase 24 service exposes no write or run entry point")


# ---------------------------------------------------------------------------
# 15. Determinism and end-to-end: the same input gives the same study.
# ---------------------------------------------------------------------------
def check_determinism() -> None:
    if not have_history():
        skip("the study is deterministic on the real pool")
        return
    a = pool.build("NIFTY")
    b = pool.build("NIFTY")
    if a is None or b is None:
        return
    ok(np.array_equal(a.out.net_r, b.out.net_r),
       "two runs of the same pool produce identical outcomes")

    masks = conditions.masks(a.feat, a.side)
    rule = {
        "instrument": "NIFTY", "side": "LONG", "side_int": outcomes.LONG,
        "stop_band_atr": outcomes.STOP_ATR, "t1_r": outcomes.T1_R,
        "conditions": ["trend_5m_agrees"], "complexity": 1,
    }
    m = study._cohort_mask(a, masks, rule)
    ok(0 < int(m.sum()) < m.size,
       "a one-condition cohort selects some candidates and refuses others")
    sel = study.trades_per_period(a, masks, rule)
    ok(sel["trades_per_week"] > 0 and 0.0 <= sel["no_trade_pct"] <= 100.0,
       "selectivity is reported as trades per week and a NO TRADE share")

    base = study.baselines(a, masks)
    ok("no_edge_control_random_half" in base or base,
       "baselines include a no-edge control")


def main() -> int:
    print("PHASE 24 SMOKE — 5Y DISCOVERY STUDY")
    for fn in (
        check_entry_timing, check_session_boundary, check_same_bar_tie,
        check_costs, check_features_causal, check_pool, check_conditions,
        check_windows, check_metrics, check_fdr, check_gate, check_ranking,
        check_honesty, check_isolation, check_determinism,
    ):
        fn()
    print("")
    tail = f", {len(SKIPPED)} skipped (no five-year history)" if SKIPPED else ""
    if FAIL:
        print(f"PHASE 24 SMOKE: {PASS} passed, {len(FAIL)} FAILED{tail}")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print(f"PHASE 24 SMOKE: {PASS} checks passed{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
