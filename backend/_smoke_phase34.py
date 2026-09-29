"""Phase 34 smoke — the properties that stop an adaptive exit from faking an edge.

Each check exists because the opposite mistake makes a trailing exit, a premium
percentage or a morning direction look profitable when it is not:

* closing a position on the first bar of the *next* session, which books an
  overnight premium gap the rule never held. This one was a real defect: it turned
  the premium study's fixed-120 arm into +39% per trade before it was caught, and
  the check below pins the corrected behaviour.
* filling a trail at its level on a bar that opened straight through it.
* letting a trail ratchet on a bar that had already stopped the trade out.
* counting only the flips that worked.
* charging no spread to a premium number, or calling a spread-modelled figure
  measured.
* ranking a morning by the day's own full range, or a volatility baseline that
  includes the session being ranked.
* choosing a horizon or an exit with the untouched holdout visible.
* storing the same option bar twice, so a harvest re-run inflates the sample.

    .venv/bin/python _smoke_phase34.py
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import sqlite3
import tempfile

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase31.excursion import LONG, SHORT
from app.research.phase34 import (
    MAX_DTE_DAYS,
    MEASURED_TRADED_PRICE,
    MIN_BARS_PER_CONTRACT,
    REQUIRES_MORE_DATA,
    SPREAD_MODELLED,
    STRIKE_WINDOW_PCT,
    adaptive,
    harvest,
    morning,
    premium,
    signals,
    store,
    study,
)

PASS = 0
FAIL: list[str] = []

DAY = 86_400
IST_OFFSET = 19_800
OPEN_IST = 9 * 3_600 + 15 * 60
BARS = 200


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"  FAILED: {label}")


def _rows(specs: list[tuple[float, float, float, float]], *, day: int = 0,
          start_minute: int = 0) -> list[dict]:
    base = 1_780_000_000 // DAY * DAY + day * DAY + OPEN_IST - IST_OFFSET
    return [
        {"time": base + (start_minute + m) * 60, "open": o, "high": h,
         "low": lo, "close": c, "volume": 1.0}
        for m, (o, h, lo, c) in enumerate(specs)
    ]


def series(specs: list[tuple[float, float, float, float]], **kw):
    return p24data.Series("FIXTURE", _rows(specs, **kw))


def _memory_store() -> sqlite3.Connection:
    """The real schema, in memory: the smoke never touches the harvested store."""
    con = sqlite3.connect(":memory:")
    for stmt in store.SCHEMA:
        con.execute(stmt)
    con.commit()
    return con


def _candles(start_minute: int, n: int) -> list[list]:
    """Broker-shaped rows: ``[iso_time, open, high, low, close, volume]``."""
    base = dt.datetime(2026, 9, 1, 9, 15)
    return [
        [(base + dt.timedelta(minutes=start_minute + m)).isoformat(),
         100.0, 101.0, 99.0, 100.5, 10.0]
        for m in range(n)
    ]


def two_session_gap_series():
    """A flat session, then a session that opens 50% higher.

    Session one never triggers any exit, so the arm must fall back to the session's
    own last close. A study that closes on the next available bar books the gap.
    """
    flat = [(100.0, 100.2, 99.8, 100.0)] * 30
    gapped = [(150.0, 150.5, 149.5, 150.0)] * 30
    return p24data.Series("FIXTURE", _rows(flat, day=0) + _rows(gapped, day=1))


def gap_through_trail_series():
    """Rises to +10%, then one bar opens far below the 25% giveback level."""
    specs = [(100.0, 100.0, 100.0, 100.0)]        # bar 0 decides
    specs.append((100.0, 100.0, 100.0, 100.0))    # bar 1 fills at 100
    specs.append((100.0, 110.0, 100.0, 110.0))    # peak +10%
    specs.append((90.0, 90.0, 89.0, 90.0))        # opens through the 7.5% level
    specs += [(90.0, 90.1, 89.9, 90.0)] * 26
    return p24data.Series("FIXTURE", _rows(specs))


def main() -> int:
    # ---------------------------------------------------------------- adaptive
    s = two_session_gap_series()
    entries = np.zeros(len(s), dtype=bool)
    entries[0] = True
    cost = np.zeros(len(s))
    res = adaptive.resolve(s, LONG, entries, kind=adaptive.FIXED, param=120.0,
                           cost=cost)
    ok(res["n"] == 1 and res["reasons"] == ["SESSION_CLOSE"],
       "an unfilled horizon falls back to the session close, not the next session")
    ok(abs(float(res["net_pct"][0])) < 0.01,
       "the overnight gap is not booked: a flat session returns ~0, not +50%")

    g = gap_through_trail_series()
    ent = np.zeros(len(g), dtype=bool)
    ent[0] = True
    res = adaptive.resolve(g, LONG, ent, kind=adaptive.TRAIL_GIVEBACK, param=25.0,
                           cost=np.zeros(len(g)))
    ok(res["reasons"] == ["TRAIL_HIT"], "a giveback trail that is breached exits")
    ok(abs(float(res["net_pct"][0]) - (-10.0)) < 1e-6,
       "a bar that opens through the trail fills at that open, not at the level")
    ok(abs(float(res["peak_pct"][0]) - 10.0) < 1e-6,
       "the peak is the best look actually reached, in percent")

    res_atr = adaptive.resolve(g, LONG, ent, kind=adaptive.TRAIL_ATR, param=1.0,
                               cost=np.zeros(len(g)))
    ok(res_atr["n"] == 0,
       "an ATR trail refuses to trade before ATR exists, rather than guessing one")

    many = np.zeros(len(g), dtype=bool)
    many[[0, 5, 10]] = True
    res_many = adaptive.resolve(g, LONG, many, kind=adaptive.TRAIL_GIVEBACK,
                                param=25.0, cost=np.zeros(len(g)))
    ok(res_many["entry_price"].size == res_many["n"]
       and float(res_many["entry_price"][0]) == 100.0,
       "each resolved trade carries its own fill price, so a charge cannot be "
       "paired with a different trade than the one it was paid on")

    ok(len(adaptive.arms()) == (len(adaptive.FIXED_HORIZONS)
                                + len(adaptive.ATR_MULTIPLES)
                                + len(adaptive.GIVEBACK_PCT) + 1),
       "the exit grid is frozen and every arm is enumerable for the hypothesis count")

    rising = series([(100.0 + m, 100.6 + m, 99.4 + m, 100.4 + m) for m in range(60)])
    a = adaptive.atr(rising)
    ok(np.isnan(a[: adaptive.ATR_PERIOD]).all() and np.isfinite(a[-1]),
       "ATR is NaN until it has its own period of bars, never seeded with a guess")
    ok(float(a[-1]) > 0, "ATR is positive on a moving series")

    # ---------------------------------------------------------------- signals
    up = series([(100.0 + m, 100.5 + m, 99.5 + m, 100.4 + m) for m in range(80)])
    sign = signals.sign_of(up, signals.EMA_FLIP)
    ok(set(np.unique(sign[np.isfinite(sign)])) <= {-1.0, 0.0, 1.0},
       "a trend sign is -1, 0 or +1 and never a magnitude")
    ok(float(sign[-1]) == 1.0, "a monotonically rising series ends in an up sign")
    ent_long = signals.entries(sign, LONG)
    ent_short = signals.entries(sign, SHORT)
    ok(int(ent_long.sum()) >= 1 and int(ent_short.sum()) == 0,
       "only the flips into the traded direction are entries, and all of them are")

    flips = np.zeros(len(up))
    flips[:40] = 1.0
    flips[40:] = -1.0
    e = np.zeros(len(up), dtype=bool)
    e[10] = True
    r = adaptive.resolve(up, LONG, e, kind=adaptive.FLIP, param=0.0,
                         cost=np.zeros(len(up)), flip_sign=flips)
    ok(r["reasons"] == ["FLIP"] and int(r["hold_minutes"][0]) == 30,
       "a flip exit leaves on the bar the sign turns, not at a fixed clock")

    # ---------------------------------------------------------------- grading
    thin = {"n": 10, "net_pct": np.full(10, 0.5), "hold_minutes": np.ones(10),
            "peak_pct": np.ones(10), "reasons": ["HORIZON"] * 10,
            "cost_pct_median": 0.04}
    ok(study.grade(thin)["status"] == REQUIRES_MORE_DATA,
       "a thin arm is REQUIRES_MORE_DATA, never graded on its win rate")

    n = 1_000
    winner = {"n": n, "net_pct": np.full(n, 0.5), "hold_minutes": np.ones(n),
              "peak_pct": np.ones(n), "reasons": ["HORIZON"] * n,
              "cost_pct_median": 0.04}
    verdict = study.grade(winner)
    ok(verdict["status"] == study.VALIDATED,
       "an arm positive in every split, fold, stress and trim can still validate")

    spike = np.full(n, -0.01)
    spike[-1] = 50.0
    outlier = {"n": n, "net_pct": spike, "hold_minutes": np.ones(n),
               "peak_pct": np.ones(n), "reasons": ["HORIZON"] * n,
               "cost_pct_median": 0.04}
    ok(study.grade(outlier)["status"] != study.VALIDATED,
       "one exceptional trade cannot validate an otherwise losing arm")

    front = np.concatenate((np.full(600, 1.0), np.full(400, -1.0)))
    decaying = {"n": n, "net_pct": front, "hold_minutes": np.ones(n),
                "peak_pct": np.ones(n), "reasons": ["HORIZON"] * n,
                "cost_pct_median": 0.04}
    graded = study.grade(decaying)
    ok(graded["status"] != study.VALIDATED
       and graded["untouched_holdout"]["net_expectancy_pct"] < 0,
       "an edge that only exists early fails on the untouched holdout")
    ok(graded["development"]["net_expectancy_pct"] > 0,
       "the split is chronological, so the early edge shows in development only")

    trimmed = study._trim(np.array([1.0, 2.0, 3.0, 100.0]), 25.0)
    ok(100.0 not in trimmed.tolist() and len(trimmed) == 3,
       "the outlier trim removes the top winners and keeps chronological order")

    # ---------------------------------------------------------------- premium
    ok(premium.SPREAD_GRID_PCT and all(x > 0 for x in premium.SPREAD_GRID_PCT),
       "a premium net figure always pays a stated spread, never zero")
    ch = premium.charges_pct(np.array([100.0, 200.0]))
    ok(np.all(ch > 0), "brokerage and statutory charges are charged on every premium")
    cheap = premium.charges_pct(np.array([5.0]))[0]
    rich = premium.charges_pct(np.array([300.0]))[0]
    ok(cheap > rich * 5,
       "a per-order ticket is a far bigger percentage of a cheap premium than a rich one")
    lows = [lo for lo, _ in premium.PREMIUM_BANDS]
    highs = [hi for _, hi in premium.PREMIUM_BANDS]
    ok(lows == sorted(lows) and lows[1:] == highs[:-1] and lows[0] == 0.0
       and highs[-1] == float("inf"),
       "the premium bands tile every entry price exactly once, with no gap or overlap")

    with contextlib.closing(_memory_store()) as con:
        row = {"token": "T1", "symbol": "NIFTY08SEP2624000CE", "root": "NIFTY",
               "exchange": "NFO", "option_type": "CE", "strike": 24_000.0,
               "expiry": "2026-09-08", "lot_size": 75}
        store.record_contracts(con, [row], dt.date(2026, 9, 1))
        first = store.save_bars(con, "T1", _candles(0, 50))
        again = store.save_bars(con, "T1", _candles(0, 50))
        total = con.execute("SELECT COUNT(*) FROM option_bars").fetchone()[0]
        ok(first == 50 and again == 0 and total == 50,
           "re-harvesting the same window stores no duplicate bar")
        ok(premium.load_contract(con, "T1", row["symbol"]) is None,
           f"a contract under {MIN_BARS_PER_CONTRACT} bars is not measured at all")
        store.save_bars(con, "T1", _candles(50, MIN_BARS_PER_CONTRACT))
        ok(store.window_done(con, "T1", dt.date(2026, 8, 1),
                             dt.date(2026, 8, 15)) is False,
           "a window is only skipped once it has actually been logged as fetched")
        store.log_window(con, "T1", dt.date(2026, 8, 1), dt.date(2026, 8, 15), 0)
        ok(store.window_done(con, "T1", dt.date(2026, 8, 1),
                             dt.date(2026, 8, 15)) is True,
           "a window that legitimately returned nothing is not asked for again")
        ps = premium.load_contract(con, "T1", row["symbol"])
        ok(ps is not None and len(ps) >= MIN_BARS_PER_CONTRACT,
           "a contract with enough bars loads as a premium series")
        ok(premium.contracts_with_bars(con)[0]["token"] == "T1",
           "only contracts that actually carry bars enter the premium study")

    # ---------------------------------------------------------------- harvest
    today = dt.date(2026, 9, 1)
    rows = [
        {"token": "A", "symbol": "NIFTY08SEP2624000CE", "root": "NIFTY",
         "exchange": "NFO", "option_type": "CE", "strike": 24_000.0,
         "expiry": "2026-09-08", "lot_size": 75},
        {"token": "B", "symbol": "NIFTY08SEP2630000CE", "root": "NIFTY",
         "exchange": "NFO", "option_type": "CE", "strike": 30_000.0,
         "expiry": "2026-09-08", "lot_size": 75},
        {"token": "C", "symbol": "NIFTY30MAR2724000CE", "root": "NIFTY",
         "exchange": "NFO", "option_type": "CE", "strike": 24_000.0,
         "expiry": "2027-03-30", "lot_size": 75},
    ]
    with contextlib.closing(_memory_store()) as con:
        store.record_contracts(con, rows, today)
        picked = harvest.select_contracts(con, ("NIFTY",), {"NIFTY": 24_000.0},
                                         today=today)
        tokens = {p["token"] for p in picked}
        ok("A" in tokens,
           f"a strike inside the {STRIKE_WINDOW_PCT}% window is harvested")
        ok("B" not in tokens,
           "a strike far from the underlying is not harvested at all")
        ok("C" not in tokens,
           f"a contract beyond {MAX_DTE_DAYS} days to expiry is out of the set")

    wins = harvest.windows(dt.date(2026, 8, 1), dt.date(2026, 9, 1))
    ok(all(a <= b for a, b in wins)
       and all(b < wins[i + 1][0] for i, (_, b) in enumerate(wins[:-1])),
       "the request windows are ordered and never overlap, so no bar is asked twice")
    ok(wins[0][0] == dt.date(2026, 8, 1) and wins[-1][1] == dt.date(2026, 9, 1),
       "the windows cover the asked range exactly, with no date beyond today")

    # ---------------------------------------------------------------- morning
    exp = morning.expansion(np.array([1.0] * 20 + [3.0]))
    ok(np.isnan(exp[:20]).all(),
       "no session is ranked before its own trailing baseline exists")
    ok(abs(float(exp[20]) - 3.0) < 1e-9,
       "expansion is the open range over the trailing median, excluding today")

    trend = [(100.0 + m, 100.5 + m, 99.5 + m, 100.4 + m) for m in range(BARS)]
    ms = morning.build("FIXTURE", series(trend))
    ok(int(ms.day.size) == 1 and int(ms.direction[0]) == 1,
       "an up open window is labelled UP, once per session")
    ok(ms.fav["CLOSE"][0] >= ms.adv["CLOSE"][0],
       "favourable and adverse excursions are reported side by side, never alone")
    ok(float(ms.net["CLOSE"][0]) < float(ms.gross["CLOSE"][0]),
       "the morning trade pays a round-trip cost")

    prows = morning.persistence(ms)
    stances = {r["stance"] for r in prows}
    ok(stances == {"CONTINUE", "FADE"},
       "both stances are reported, so the mirrored half is not hidden")
    horizons = {r["horizon_minutes"] for r in prows}
    ok(horizons == {str(h) for h in morning.HORIZONS} | {"CLOSE"},
       "every frozen horizon is counted, including the session close")

    down = morning.build("FIXTURE", series(
        [(200.0 - m, 200.5 - m, 199.5 - m, 199.6 - m) for m in range(BARS)]))
    ok(int(down.direction[0]) == -1 and down.fav["CLOSE"][0] > 0,
       "a short's favourable side is the low, not the high")

    # -------------------------------------------------------------- artefacts
    with tempfile.TemporaryDirectory() as tmp:
        out = morning.run(instruments=(), out_dir=tmp, progress=False)
        ok(out["verdict"] == morning.NO_PERSISTENCE and out["production_changed"]
           is False,
           "with no instrument the morning verdict is no-edge, and nothing changed")
        ok(os.path.exists(os.path.join(tmp, "p34_morning.json")),
           "the morning artefact is written where it is asked to be")
        saved = json.load(open(os.path.join(tmp, "p34_morning.json")))
        ok(saved["open_window_minutes"] == morning.OPEN_WINDOW,
           "the artefact records the frozen open window it was computed with")

    labels = {MEASURED_TRADED_PRICE, SPREAD_MODELLED, REQUIRES_MORE_DATA}
    ok(len(labels) == 3 and SPREAD_MODELLED != MEASURED_TRADED_PRICE,
       "a spread-modelled premium is never labelled a measured one")

    print(f"PHASE 34 SMOKE — {PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
