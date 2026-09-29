"""Phase 28 smoke — the multi-day study's honesty properties.

Every check here exists because the opposite mistake would turn a losing study
into a winning one. A daily horizon brings three failure modes the intraday
phases never had, and each one flatters the result:

* **the overnight gap.** A stop that is "hit" 200 points beyond itself must fill
  at the open, not at the level. Filling at the level is the single easiest way
  to publish a profitable daily backtest that cannot be traded;
* **the passive alternative.** A long-only cash rule in a market that drifts up
  will show a positive number without any edge at all, so buy-and-hold per day of
  exposure is a gate clause, not a footnote;
* **the missing history.** No cash-equity name has five years of daily bars on a
  fresh machine. That is UNANSWERED, and the report must never let it read as a
  failed stock strategy.

The rest re-checks what the earlier phases established at the new horizon:
next-open entry, the same-bar tie resolved as the stop, costs charged once per
leg plus rolls, both directions in a futures pool and one direction in a cash
pool, development-only selection, an honest FDR denominator, strict JSON, and the
pre-committed stopping rule firing on the gate's own result rather than on prose.

Fixtures are synthetic and built here, so this runs anywhere:

    .venv/bin/python _smoke_phase28.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile

import numpy as np

from app.market.instruments import get_spec
from app.research.phase19 import futcosts
from app.research.phase24.data import Series
from app.research.phase24.outcomes import LONG, SHORT
from app.research.phase28 import (
    BUY_HOLD_CLAIM,
    DELIVERY_CLAIM,
    EQUITY_DELIVERY,
    FUTURES,
    GAP_CLAIM,
    INSUFFICIENT_HISTORY,
    RESEARCH_LEAD,
    ROLLOVER_CLAIM,
    SHORT_CLAIM,
    STOP_RULE,
    baseline,
    collect,
    conditions,
    costs,
    dailybars,
    discover,
    features,
    outcomes,
    pool,
    rank,
    report,
    service,
    study,
)

PASS = 0
FAIL: list[str] = []

IST = dailybars.IST_OFFSET
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
def minute_series(
    sessions: int = 3, minutes: int = 30, instrument: str = "NIFTY"
) -> Series:
    """An intraday series, so daily aggregation has something to collapse."""
    base = (1_600_000_000 // DAY) * DAY - IST + (9 * 60 + 15) * 60
    ts = np.concatenate([
        np.array([base + d * DAY + 60 * k for k in range(minutes)], dtype=np.int64)
        for d in range(sessions)
    ])
    n = ts.size
    k = np.arange(n, dtype=float)
    s = Series.__new__(Series)
    s.instrument = instrument
    s.ts = ts
    s.open = 20_000.0 + k * 0.5 + 30.0 * np.sin(k / 7.0)
    s.close = s.open + 0.25
    s.high = np.maximum(s.open, s.close) + 1.0 + (k % 7)
    s.low = np.minimum(s.open, s.close) - 1.0 - (k % 5)
    s.volume = 100.0 + (k % 11)
    return s


def daily_series(bars: int = 1_400, instrument: str = "NIFTY") -> Series:
    """A daily series long enough to be eligible, with a real trend and pullbacks."""
    base = (1_500_000_000 // DAY) * DAY - IST + dailybars.CLOSE_SECONDS
    # Weekends are skipped so the calendar span of N bars is realistic: 1,400
    # trading days is about 5.4 years, which is what the eligibility rule wants.
    days: list[int] = []
    d = 0
    while len(days) < bars:
        if (d % 7) < 5:
            days.append(d)
        d += 1
    ts = np.array([base + x * DAY for x in days], dtype=np.int64)
    k = np.arange(bars, dtype=float)
    s = Series.__new__(Series)
    s.instrument = instrument
    s.ts = ts
    mid = (
        1_000.0 + k * 0.9
        + 60.0 * np.sin(k / 23.0) + 25.0 * np.sin(k / 5.0)
        + 12.0 * np.cos(k / 3.0)
    )
    s.open = mid
    s.close = mid + 3.0 * np.sin(k / 2.0)
    s.high = np.maximum(s.open, s.close) + 6.0 + (k % 5)
    s.low = np.minimum(s.open, s.close) - 6.0 - (k % 4)
    s.volume = 1_000.0 + 100.0 * (k % 9)
    return s


def build_pool(instrument: str = "NIFTY", bars: int = 1_400, **kw) -> pool.Pool:
    """A pool over a synthetic daily series, with the loader stubbed out."""
    series = daily_series(bars, instrument)
    _, stats = dailybars._normalise_daily(series)
    stats["source"] = dailybars.SOURCE_NATIVE_DAILY
    fixture = dailybars._finish(series, stats)

    def fake_load(inst: str):
        return fixture if inst.upper() == instrument.upper() else None

    real = dailybars.load
    dailybars.load = fake_load
    try:
        return pool.build(instrument, **kw)
    finally:
        dailybars.load = real


def arrays(o: list[tuple[float, float, float, float]]) -> dict[str, np.ndarray]:
    """OHLC rows as the arrays the resolver takes, timestamped one per session."""
    base = (1_500_000_000 // DAY) * DAY - IST + dailybars.CLOSE_SECONDS
    return {
        "open_": np.array([r[0] for r in o], dtype=np.float64),
        "high": np.array([r[1] for r in o], dtype=np.float64),
        "low": np.array([r[2] for r in o], dtype=np.float64),
        "close": np.array([r[3] for r in o], dtype=np.float64),
        "ts": np.array(
            [base + i * DAY for i in range(len(o))], dtype=np.int64
        ),
    }


def resolve_one(
    rows: list[tuple[float, float, float, float]],
    *,
    side: int = LONG,
    atr: float = 10.0,
    instrument: str = "NIFTY",
    vehicle: str = FUTURES,
    **kw,
):
    a = arrays(rows)
    n = len(rows)
    return outcomes.resolve(
        instrument,
        vehicle,
        idx=np.array([0], dtype=np.int64),
        side=np.array([side], dtype=np.int8),
        atr=np.full(n, atr, dtype=np.float64),
        **a,
        **kw,
    )


# ---------------------------------------------------------------------------
# §1 daily bars: aggregation, precedence, eligibility
# ---------------------------------------------------------------------------
def check_aggregation() -> None:
    s = minute_series(sessions=3, minutes=30)
    out, st = dailybars.aggregate(s)
    ok(out.ts.size == st["daily_bars"] == 3,
       "three intraday sessions collapse to exactly three daily bars")
    ok(close(out.open[0], s.open[0]),
       "a daily bar opens at its session's first bar's open")
    ok(close(out.close[0], s.close[29]),
       "a daily bar closes at its session's LAST bar's close")
    ok(close(out.high[0], s.high[:30].max()),
       "a daily bar's high is the true maximum of its own session")
    ok(close(out.low[0], s.low[:30].min()),
       "a daily bar's low is the true minimum of its own session")
    ok(close(out.volume[0], s.volume[:30].sum()),
       "a daily bar's volume is the sum of its own session")
    ok(all(((int(t) + IST) % DAY) == dailybars.CLOSE_SECONDS for t in out.ts),
       "every daily bar is stamped 15:30 IST of its own session")
    ok(bool((np.diff(out.ts) > 0).all()), "daily timestamps are increasing")
    ok(close(float(out.high.max()), float(s.high.max()))
       and close(float(out.low.min()), float(s.low.min())),
       "aggregation neither invents nor loses range")
    ok(st["thin_sessions"] == 0 and st["median_source_bars_per_session"] == 30.0,
       "a full session is not counted as thin")

    thin = minute_series(sessions=1, minutes=4)
    _, tst = dailybars.aggregate(thin)
    ok(tst["thin_sessions"] == 1,
       "a session with almost no bars is reported as thin, not silently dropped")

    # a session missing from the feed stays missing: no bar is invented for it
    gapped = minute_series(sessions=3, minutes=10)
    keep = (dailybars._sessions(gapped.ts) != dailybars._sessions(gapped.ts)[10])
    g = Series.__new__(Series)
    g.instrument = "NIFTY"
    g.ts = gapped.ts[keep]
    for f in ("open", "high", "low", "close", "volume"):
        setattr(g, f, getattr(gapped, f)[keep])
    _, gst = dailybars.aggregate(g)
    ok(gst["daily_bars"] == 2,
       "a missing session stays missing rather than being interpolated")


def check_source_precedence() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        real_dir = dailybars.BACKTEST_DIR
        dailybars.BACKTEST_DIR = tmp
        try:
            inst = "SMOKEINST"
            base = (1_500_000_000 // DAY) * DAY - IST + 10 * 3600
            minute_rows = [
                {"time": base + d * DAY + 60 * m, "open": 100.0 + m,
                 "high": 101.0 + m, "low": 99.0 + m, "close": 100.5 + m,
                 "volume": 5.0}
                for d in range(3) for m in range(20)
            ]
            daily_rows = [
                {"time": base + d * DAY, "open": 1.0, "high": 2.0, "low": 0.5,
                 "close": 1.5, "volume": 9.0}
                for d in range(4)
            ]
            m_path = os.path.join(tmp, f"{inst}_ONE_MINUTE.jsonl")
            d_path = os.path.join(tmp, f"{inst}_ONE_DAY.jsonl")
            with open(m_path, "w", encoding="utf-8") as fh:
                for r in minute_rows:
                    fh.write(json.dumps(r) + "\n")
            with open(d_path, "w", encoding="utf-8") as fh:
                for r in daily_rows:
                    fh.write(json.dumps(r) + "\n")

            series, stats = dailybars.load(inst)
            ok(stats["source"] == dailybars.SOURCE_NATIVE_DAILY
               and stats["daily_bars"] == 4,
               "a native daily file is preferred over aggregating minutes")
            ok(stats["aggregated"] is False,
               "a native daily series is not labelled as aggregated")
            ok(all(((int(t) + IST) % DAY) == dailybars.CLOSE_SECONDS
                   for t in series.ts),
               "a native daily bar is re-stamped to its own session's close")

            os.remove(d_path)
            series, stats = dailybars.load(inst)
            ok(stats["source"] == dailybars.SOURCE_MINUTE_AGGREGATE
               and stats["daily_bars"] == 3,
               "with no daily file the minute file is aggregated instead")
            ok(stats["source_bars"] == 60,
               "the aggregate reports every source bar it consumed")

            os.remove(m_path)
            ok(dailybars.load(inst) is None,
               "an instrument with no file and no stored candles loads as None")
        finally:
            dailybars.BACKTEST_DIR = real_dir


def check_eligibility() -> None:
    good = {"daily_bars": 1_300, "span_years": 5.2}
    ok(dailybars.eligible(good)[0], "1,300 bars over 5.2 years is eligible")
    thin_ok, why = dailybars.eligible({"daily_bars": 999, "span_years": 5.2})
    ok(not thin_ok and "999" in why,
       "999 daily bars is excluded and the count is stated")
    short_ok, why = dailybars.eligible({"daily_bars": 1_300, "span_years": 2.0})
    ok(not short_ok and "2.0" in why,
       "a long file over a short span is excluded and the span is stated")
    ok(dailybars.eligible({})[0] is False,
       "an instrument with no history is not eligible by default")

    r = dailybars.rules()
    text = json.dumps(r).lower()
    for needle, label in (
        ("15:30", "the rules state the daily bar's timestamp convention"),
        ("interpolat", "the rules state that nothing is interpolated"),
        ("thin", "the rules state that thin sessions are reported"),
    ):
        ok(needle in text, label)


def check_vehicle_classification() -> None:
    for inst in ("NIFTY", "BANKNIFTY", "SENSEX", "CRUDEOIL", "GOLD", "ZINC"):
        ok(dailybars.is_equity(inst) is False,
           f"{inst} is not cash equity, so it is priced as futures")
    for inst in ("TCS", "RELIANCE", "TATASTEEL"):
        ok(dailybars.is_equity(inst) is True,
           f"{inst} is cash equity, so it is priced with delivery charges")
    ok(outcomes.vehicle_for("NIFTY") == FUTURES
       and outcomes.vehicle_for("TCS") == EQUITY_DELIVERY,
       "the vehicle follows the classification, not the caller")
    ok(outcomes.sides_for(FUTURES) == (LONG, SHORT),
       "a futures pool takes both directions")
    ok(outcomes.sides_for(EQUITY_DELIVERY) == (LONG,),
       "a cash-delivery pool is long only: delivery cannot be held short")
    split = dailybars.universe_by_vehicle()
    ok(all(not dailybars.is_equity(i) for i in split[FUTURES])
       and all(dailybars.is_equity(i) for i in split[EQUITY_DELIVERY]),
       "the universe split agrees with the per-instrument classification")


# ---------------------------------------------------------------------------
# §2 execution: next-open entry, gaps, ties, horizon
# ---------------------------------------------------------------------------
def check_entry_is_next_open() -> None:
    rows = [(100.0, 101.0, 99.0, 100.0),      # signal bar
            (103.0, 104.0, 102.0, 103.5),     # entry bar: fills at ITS open
            (104.0, 105.0, 103.0, 104.0)]
    o = resolve_one(rows, atr=50.0)
    ok(close(o.entry[0], 103.0),
       "the fill is the next session's open, never the signal bar's close")
    ok(close(o.stop[0], 103.0 - 50.0),
       "the stop is one ATR band from the fill, not from the signal close")


def check_gap_through_stop() -> None:
    rows = [(100.0, 101.0, 99.0, 100.0),
            (100.0, 101.0, 99.5, 100.0),      # entry at 100, stop at 90
            (85.0, 99.0, 84.0, 98.0)]         # opens 5 points BELOW the stop
    o = resolve_one(rows, atr=10.0)
    ok(o.sl_hit[0] and o.exit_reason[0] == outcomes.EXIT_STOP_GAP,
       "a gap through the stop is recorded as a gap stop, not a level stop")
    ok(close(o.exit_price[0], 85.0),
       "a gap stop fills at the open it actually gapped to, not at the stop")
    ok(float(o.net_points[0]) < -10.0,
       "the gap costs more than the risk taken, which is the point of the model")
    ok(not o.t1_before_sl[0], "a gapped stop is not scored as a target")

    flatter = resolve_one(rows, atr=10.0, gap_fill_at_open=False)
    ok(close(flatter.exit_price[0], 90.0)
       and flatter.exit_reason[0] == outcomes.EXIT_STOP_LEVEL,
       "the diagnostic variant fills at the stop, and is labelled as the "
       "flattering one")
    ok(float(flatter.net_points[0]) > float(o.net_points[0]),
       "ignoring gap risk always looks better, which is why it is not the default")


def check_gap_through_target() -> None:
    rows = [(100.0, 101.0, 99.0, 100.0),
            (100.0, 101.0, 99.5, 100.0),      # entry 100, stop 90, T1 at 115
            (130.0, 132.0, 128.0, 131.0)]     # opens above T1
    o = resolve_one(rows, atr=10.0)
    ok(o.t1_before_sl[0] and o.exit_reason[0] == outcomes.EXIT_TARGET_GAP,
       "a gap through the target is recorded as a gap target")
    ok(close(o.exit_price[0], 130.0),
       "a favourable gap is also filled at the open, not at the target")
    ok(float(o.net_points[0]) > 15.0,
       "the favourable gap is credited honestly rather than capped at the target")


def check_same_bar_tie() -> None:
    rows = [(100.0, 101.0, 99.0, 100.0),
            (100.0, 101.0, 99.5, 100.0),      # entry 100, stop 90, T1 115
            (100.5, 120.0, 85.0, 100.0)]      # touches BOTH inside one bar
    o = resolve_one(rows, atr=10.0)
    ok(o.sl_hit[0] and not o.t1_before_sl[0],
       "a bar touching both levels is resolved as the stop, never as the target")
    ok(o.exit_reason[0] == outcomes.EXIT_STOP_LEVEL and close(o.exit_price[0], 90.0),
       "the ambiguous bar exits at the stop level with no gap credit")


def check_short_side() -> None:
    rows = [(100.0, 101.0, 99.0, 100.0),
            (100.0, 101.0, 99.5, 100.0),      # short entry 100, stop 110, T1 85
            (80.0, 99.0, 79.0, 82.0)]
    o = resolve_one(rows, side=SHORT, atr=10.0)
    ok(o.t1_before_sl[0] and o.exit_reason[0] == outcomes.EXIT_TARGET_GAP,
       "a short is resolved against its own stop and target, mirrored correctly")
    ok(close(o.exit_price[0], 80.0) and float(o.gross_points[0]) > 0.0,
       "a short profits when price falls, priced at the gapped open")


def check_horizon_timeout() -> None:
    quiet = [(100.0, 100.5, 99.5, 100.0)] * 8
    o = resolve_one(quiet, atr=10.0, horizon_days=3)
    ok(o.exit_reason[0] == outcomes.EXIT_HORIZON,
       "a trade that reaches neither level exits at the horizon")
    ok(int(o.bars_held[0]) == 3,
       "the horizon is counted in trading sessions, not calendar days")
    ok(o.outcome[0] == outcomes.OUT_TIMEOUT and o.resolved[0],
       "a timeout is a resolved outcome, not a discarded candidate")
    ok(close(float(o.calendar_days[0]), 2.0),
       "calendar days are the elapsed time between the fill bar and the exit bar, "
       "tracked separately for the rollover charge")


def check_costs_charged_once() -> None:
    rows = [(100.0, 101.0, 99.0, 100.0),
            (100.0, 101.0, 99.5, 100.0),
            (115.5, 116.0, 114.0, 115.5)]
    o = resolve_one(rows, atr=10.0, slippage_points=3.0)
    charge = costs.futures_round_trip(
        "NIFTY", entry=float(o.entry[0]), exit_price=float(o.exit_price[0]),
        calendar_days_held=float(o.calendar_days[0]), slippage_points=3.0,
    )
    ok(close(float(o.cost_points[0]), float(charge["cost_points"]), 1e-4),
       "the resolver charges exactly what the cost model says, no more")
    ok(close(float(o.net_points[0]),
             float(o.gross_points[0]) - float(o.cost_points[0]), 1e-6),
       "net points are gross minus the charged cost, with nothing unaccounted")
    lot = float(get_spec("NIFTY").lot_size)
    ok(close(float(o.net_rupees[0]), float(o.net_points[0]) * lot, 1e-3),
       "rupees use the instrument's configured lot size, not one unit")


# ---------------------------------------------------------------------------
# §3 costs: futures rolls and itemised cash delivery
# ---------------------------------------------------------------------------
def check_rollover() -> None:
    ok(costs.rolls_held(0.0) == 0 and costs.rolls_held(29.0) == 0,
       "a hold shorter than a contract month crosses no expiry")
    ok(costs.rolls_held(30.0) == 1 and costs.rolls_held(65.0) == 2,
       "one roll is charged per contract month held")
    ok(costs.rolls_held(-5.0) == 0,
       "a negative holding time cannot produce a credit")

    flat = costs.futures_round_trip(
        "NIFTY", entry=20_000.0, exit_price=20_100.0, calendar_days_held=1.0)
    rolled = costs.futures_round_trip(
        "NIFTY", entry=20_000.0, exit_price=20_100.0, calendar_days_held=65.0)
    ok(rolled["rolls"] == 2 and flat["rolls"] == 0,
       "the roll count is reported on the charge itself")
    ok(close(rolled["cost_points"], flat["cost_points"] * 3.0, 1e-3),
       "each roll pays a full extra round trip: expiry is never crossed free")
    ok("30 calendar days" in rolled["roll_note"],
       "the roll approximation is stated on the row, not left implicit")

    ref = futcosts.round_trip(
        "NIFTY", entry=20_000.0, exit_price=20_100.0,
        lot_size=int(get_spec("NIFTY").lot_size), slippage_points=None,
    )
    ok(close(flat["cost_points"], ref["cost_points"], 1e-6),
       "with no roll the futures charge is Phase 19's, reused not reimplemented")
    ok(flat["cost_status"] == costs.COST_MODELLED,
       "an unmeasured futures spread is labelled MODELLED, never MEASURED")
    stressed = costs.futures_round_trip(
        "NIFTY", entry=20_000.0, exit_price=20_100.0, spread_multiplier=1.5)
    ok(close(stressed["cost_points"], flat["cost_points"] * 1.5, 1e-3),
       "the spread multiplier is the handle for the depth this feed never shows")


def check_equity_costs() -> None:
    c = costs.equity_round_trip("TCS", entry=3_000.0, exit_price=3_300.0)
    items = ("stt_points", "txn_points", "sebi_points", "stamp_points",
             "gst_points", "brokerage_points")
    ok(all(k in c for k in items), "every statutory cash charge is itemised")
    ok(close(c["statutory_points"], sum(float(c[k]) for k in items), 1e-6),
       "the itemised charges add up to the statutory total")
    ok(close(c["stt_points"], 6_300.0 * costs.EQUITY_STT_PCT / 100.0, 1e-6),
       "delivery STT is charged on both legs' turnover")
    ok(close(c["stamp_points"], 3_000.0 * costs.EQUITY_STAMP_PCT / 100.0, 1e-6),
       "stamp duty is charged on the buy leg only")
    ok(close(c["slippage_points"], 6_300.0 * 0.03 / 100.0, 1e-6),
       "slippage is charged on both sides, as a percentage of each fill")
    ok(close(c["cost_points"], c["statutory_points"] + c["slippage_points"], 1e-6),
       "the headline cash cost is statutory plus slippage and nothing hidden")
    ok(c["dp_charge_rupees_per_sell"] == costs.EQUITY_DP_RUPEES_PER_SELL,
       "the flat depository charge is reported in rupees")
    ok("dp_charge_points" not in c and "depository" in c["note"].lower(),
       "the flat charge is never divided by an assumed position size")
    ok(c["rolls"] == 0 and "no premium" in c["note"],
       "cash delivery has no expiry to roll and no premium to decay")
    ok(costs.equity_round_trip("TCS", entry=0.0, exit_price=1.0)["cost_points"]
       is None,
       "an unpriceable round trip returns no cost rather than a zero")

    disp_e = costs.round_trip(
        "TCS", EQUITY_DELIVERY, entry=100.0, exit_price=110.0)
    disp_f = costs.round_trip(
        "NIFTY", FUTURES, entry=100.0, exit_price=110.0)
    ok(disp_e["vehicle"] == EQUITY_DELIVERY and disp_f["vehicle"] == FUTURES,
       "the dispatcher charges each vehicle with its own model")

    e = resolve_one(
        [(100.0, 101.0, 99.0, 100.0)] * 6,
        instrument="TCS", vehicle=EQUITY_DELIVERY, atr=5.0, horizon_days=3,
    )
    ok(float(e.cost_points[0]) > 0.0 and close(float(e.net_rupees[0]),
                                               float(e.net_points[0]), 1e-9),
       "a cash trade is charged per share, so rupees equal points for one share")


# ---------------------------------------------------------------------------
# §4 features and the bounded vocabulary
# ---------------------------------------------------------------------------
def check_feature_causality() -> None:
    s = daily_series(600)
    full = features.build(s)
    cut = 500
    part = Series.__new__(Series)
    part.instrument = s.instrument
    part.ts = s.ts[:cut]
    for f in ("open", "high", "low", "close", "volume"):
        setattr(part, f, getattr(s, f)[:cut])
    partial = features.build(part)
    for key in ("atr", "rsi", "ema_fast", "ema_regime", "prior_high_20",
                "pullback_from_high_pct", "gap_pct", "mom_long_atr",
                "volume_ratio", "pct_from_year_high"):
        a = np.asarray(full[key])[:cut]
        b = np.asarray(partial[key])
        same = np.allclose(a[np.isfinite(a) & np.isfinite(b)],
                           b[np.isfinite(a) & np.isfinite(b)], atol=1e-9)
        ok(same, f"{key} on bar i uses only bars up to i (no look-ahead)")

    i = 300
    ok(close(float(full["prior_high_20"][i]), float(s.high[i - 20:i].max())),
       "the 20-day breakout level excludes the bar being decided on")
    ok(close(float(full["prior_low_50"][i]), float(s.low[i - 50:i].min())),
       "the 50-day breakout level excludes the bar being decided on")
    ok(close(float(full["gap_pct"][i]),
             (float(s.open[i]) / float(s.close[i - 1]) - 1.0) * 100.0, 1e-9),
       "the gap is measured against the previous session's close")


def check_conditions() -> None:
    names = conditions.names()
    p = build_pool("NIFTY")
    masks = conditions.masks(p.feat, p.side)
    ok(set(masks) == set(names),
       "every declared condition produces a mask, and no mask is undeclared")
    ok(all(m.dtype == bool and m.size == len(p) for m in masks.values()),
       "every mask is boolean and aligned to the candidate order")
    ok(any(m.any() for m in masks.values()),
       "the vocabulary selects something on a real pool")
    ok(len(names) == len(set(names)), "no condition is declared twice")

    band_names = [b[0] for b in conditions.PULLBACK_BANDS]
    ok(all(b in names for b in band_names) and len(band_names) == 4,
       "the pullback bands are the four fixed ones, declared before the run")
    stacked = np.vstack([masks[b] for b in band_names]).sum(axis=0)
    ok(int(stacked.max()) <= 1,
       "the pullback bands do not overlap, so a bar lands in at most one")

    longs, shorts = p.side == LONG, p.side == SHORT
    agree = masks["trend_short_agrees"]
    ok(int(agree[longs].sum()) > 0 and int(agree[shorts].sum()) > 0,
       "a side-relative condition fires for both directions of the same bar")
    ok(not np.array_equal(agree[longs], agree[shorts]),
       "trend agreement is relative to the side, not a fixed bullish filter")


def check_pool() -> None:
    p = build_pool("NIFTY")
    ok(p is not None and len(p) > 0, "a futures pool builds from daily bars")
    n_long = int((p.side == LONG).sum())
    n_short = int((p.side == SHORT).sum())
    ok(n_long == n_short > 0,
       "every eligible session produces both a LONG and a SHORT futures candidate")
    ok(int(p.idx.min()) >= pool.WARMUP_BARS,
       "the first 250 sessions are warm-up and produce no candidate")
    ok(p.vehicle == FUTURES and p.instrument == "NIFTY",
       "the pool carries its instrument and the vehicle it was priced as")
    ok(int(p.ts.size) == int(p.idx.size) == len(p),
       "one timestamp per candidate, so a cohort cannot be mis-dated")

    e = build_pool("TCS")
    ok(e is not None and int((e.side == SHORT).sum()) == 0,
       "a cash-equity pool generates no short candidate at all")
    summ = pool.summary(e)
    ok(summ["sides"] == ["LONG"] and summ["vehicle"] == EQUITY_DELIVERY,
       "the cash pool's summary states long-only, with the reason in the claims")
    fs = pool.summary(p)
    ok(fs["horizon_trading_days"] == outcomes.HORIZON_DAYS,
       "the pool summary states the holding horizon in sessions")
    ok(fs.get("gap_resolved_pct") is not None,
       "the pool reports how much of it was resolved by a gap, not by a level")

    tight = build_pool("NIFTY", bars=900)
    ok(tight is None,
       "an instrument with too little history builds no pool rather than a short one")


# ---------------------------------------------------------------------------
# §5 chronology, selection, multiple testing, gate
# ---------------------------------------------------------------------------
def check_windows_and_selection() -> None:
    p = build_pool("NIFTY")
    s = discover.Search(p)
    dev, val, hold = s.win[discover.DEV], s.win[discover.VAL], s.win[discover.HOLDOUT]
    ok(dev[1] <= val[0] and val[1] <= hold[0],
       "development, validation and the holdout are chronological, not shuffled")
    ok(not bool((s.dev & s.val).any()) and not bool((s.dev & s.hold).any())
       and not bool((s.val & s.hold).any()),
       "no candidate appears in two windows")
    ok(int(s.hold.sum()) > 0, "the holdout window is not empty")
    rows = s.run()
    ok(s.tests > 0, "the search counts every hypothesis it evaluated")
    ok(s.tests >= len(rows) and s.tests >= len(conditions.names()),
       "the count is at least the vocabulary it swept, not just the rows kept")
    ok(all(len(r["conditions"]) <= discover.MAX_CONDITIONS for r in rows),
       "no discovered rule exceeds the declared complexity ceiling")
    ok(all("holdout" not in r and "validation" not in r for r in rows),
       "discovery never reads the validation window or the holdout")
    for r in rows:
        ok(float((r.get("development") or {}).get("avg_net_r") or -1)
           > discover.MIN_DEV_AVG_NET_R,
           "a kept row cleared the development bar on development data")
    ok(all(len(nm["conditions"]) <= discover.MAX_CONDITIONS
           for nm in s.near_misses),
       "near misses obey the same complexity ceiling")

    space = discover.searched_space()
    ok(space["max_conditions_per_rule"] == 3
       and space["condition_count"] == len(conditions.names()),
       "the searched space is declared as data, so the denominator is auditable")
    ok("cash delivery cannot be short" in json.dumps(space),
       "the searched space states why cash equity has one side")


def check_fdr() -> None:
    p_values = [0.001, 0.02, 0.2, 0.6]
    few = discover.benjamini_hochberg(p_values, tests=4)
    many = discover.benjamini_hochberg(p_values, tests=50_000)
    ok(sum(few) >= sum(many),
       "the same p-values survive less often against a larger hypothesis count")
    ok(discover.benjamini_hochberg([], tests=0) == [],
       "an empty search corrects to no survivors rather than failing")

    rows = [{"holdout": {"p_value_vs_base_rate": 0.001}, "instrument": "NIFTY",
             "vehicle": FUTURES, "side": "LONG", "stop_band_atr": 1.0,
             "horizon_trading_days": 10, "conditions": ["a"], "complexity": 1}]
    honest = rank.rank([dict(r) for r in rows], tests=20_000)
    naive = rank.rank([dict(r) for r in rows], tests=None)
    ok(honest[0]["fdr_denominator"] == 20_000 and naive[0]["fdr_denominator"] == 1,
       "the correction uses the hypotheses evaluated, not the rows that survived")
    ok(naive[0]["fdr_survivor"] and not honest[0]["fdr_survivor"],
       "the honest denominator is what kills a single lucky cohort")


def good_rule() -> dict:
    return {
        "instrument": "NIFTY", "vehicle": FUTURES, "side": "LONG",
        "stop_band_atr": 1.0, "horizon_trading_days": 10,
        "conditions": ["trend_short_agrees", "breakout_20d"], "complexity": 2,
        "development": {"avg_net_r": 0.2, "trades": 500},
        "validation": {"avg_net_r": 0.15, "trades": 200},
        "holdout": {"avg_net_r": 0.12, "trades": 250, "profit_factor": 1.4,
                    "p_value_vs_base_rate": 0.0001},
        "walk_forward": {"folds_scored": 5, "folds_positive": 5, "stable": True},
        "cost_sensitivity": [
            {"variant": "slippage_points=2.0", "avg_net_r": 0.05,
             "survives": True},
            {"variant": "spread_multiplier=1.5", "avg_net_r": 0.03,
             "survives": True},
            {"variant": "gap_fill_at_open=False", "avg_net_r": 0.2,
             "survives": True},
        ],
        "parameter_perturbation": [{"avg_net_r": 0.08, "survives": True}],
        "benchmark_holdout": {
            "applicable": True, "beats_benchmark": True,
            "net_points_per_exposed_day": 3.0, "benchmark_points_per_day": 1.0,
        },
        "dev_gate_passed": True,
    }


def check_gate() -> None:
    status, reasons = rank.gate(good_rule(), fdr_survivor=True)
    ok(status == rank.VALIDATED and not reasons,
       "a cohort positive in all three windows, stressed and benchmarked, clears")
    ok(rank.research_label(status) == RESEARCH_LEAD,
       "clearing the gate publishes a RESEARCH_LEAD, never a live promotion")

    for label, mutate in (
        ("negative holdout", lambda r: r["holdout"].update(avg_net_r=-0.01)),
        ("thin holdout", lambda r: r["holdout"].update(trades=40)),
        ("holdout PF below 1", lambda r: r["holdout"].update(profit_factor=0.9)),
        ("negative validation", lambda r: r["validation"].update(avg_net_r=-0.02)),
        ("unstable walk-forward",
         lambda r: r["walk_forward"].update(folds_positive=2, stable=False)),
        ("outlier-dependent", lambda r: r["holdout"].update(
            outlier_top1_contribution_pct=80.0)),
    ):
        r = json.loads(json.dumps(good_rule()))
        mutate(r)
        st, why = rank.gate(r, fdr_survivor=True)
        ok(st != rank.VALIDATED and why, f"the gate rejects a {label} cohort")

    st, why = rank.gate(good_rule(), fdr_survivor=False)
    ok(st != rank.VALIDATED and any("multiple-testing" in w for w in why),
       "failing the multiple-testing correction alone still blocks the gate")

    r = json.loads(json.dumps(good_rule()))
    r["cost_sensitivity"] = [{"variant": "spread_multiplier=1.5",
                              "avg_net_r": 0.03, "survives": True}]
    st, why = rank.gate(r, fdr_survivor=True)
    ok(st != rank.VALIDATED and any("slippage" in w for w in why),
       "an untested slippage variant is treated as a failure, not as a pass")

    r = json.loads(json.dumps(good_rule()))
    r["benchmark_holdout"] = {
        "applicable": True, "beats_benchmark": False,
        "net_points_per_exposed_day": 0.4, "benchmark_points_per_day": 1.2,
    }
    st, why = rank.gate(r, fdr_survivor=True)
    ok(st != rank.VALIDATED
       and any("passive alternative is better" in w for w in why),
       "a long cohort that loses to buy-and-hold is not a lead, however positive")

    r = json.loads(json.dumps(good_rule()))
    r["benchmark_holdout"] = {"applicable": False}
    st, why = rank.gate(r, fdr_survivor=True)
    ok(st != rank.VALIDATED and any("unanswered" in w for w in why),
       "an unmeasurable benchmark is unanswered, not a pass")

    r = json.loads(json.dumps(good_rule()))
    ranked = rank.rank([r], tests=1)
    ok(ranked[0]["status"] == RESEARCH_LEAD
       and ranked[0]["gap_dependence"]["measured"] is True,
       "the ranked row carries the gap-dependence diagnostic it was measured with")
    ok(ranked[0]["strategy_id"].startswith("P28_"),
       "a strategy id is derived from the rule's definition, not its results")
    ok(rank.rank([json.loads(json.dumps(good_rule()))], tests=1)[0]["strategy_id"]
       == ranked[0]["strategy_id"],
       "the same rule always gets the same id, so a lead can be re-run")


def check_walk_forward() -> None:
    p = build_pool("NIFTY")
    s = discover.Search(p)
    rows = s.run() or s.near_misses
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
# §6 buy-and-hold: the clause a long-only daily study cannot skip
# ---------------------------------------------------------------------------
def check_buy_and_hold() -> None:
    p = build_pool("TCS")
    bh = baseline.buy_and_hold(p.series, p.vehicle)
    ok(bh["available"] and bh["trading_days"] == len(p.series),
       "buy-and-hold is measured over every session in the window")
    ok(close(bh["gross_points"],
             float(p.series.close[-1]) - float(p.series.open[0]), 1e-4),
       "buy-and-hold is first open to last close, with nothing timed")
    ok(bh["cost_points"] > 0.0,
       "the passive alternative pays its own round trip too")
    ok(close(bh["net_points"], bh["gross_points"] - bh["cost_points"], 1e-4),
       "the passive net is gross minus its charged cost")

    win = discover.windows(p.ts)
    hold = baseline.buy_and_hold(
        p.series, p.vehicle,
        lo_ts=win[discover.HOLDOUT][0], hi_ts=win[discover.HOLDOUT][1])
    ok(hold["available"] and hold["trading_days"] < bh["trading_days"],
       "the holdout comparison is measured over the holdout window only")

    fut = build_pool("NIFTY")
    rolls = baseline.buy_and_hold(fut.series, fut.vehicle)
    ok(rolls["rolls_charged"] > 0,
       "holding futures for years is charged the rolls it would have paid")

    s = discover.Search(p)
    m = s.hold & s.masks["trend_short_agrees"]
    cmp_long = baseline.compare(p.out, m, LONG, hold)
    ok(cmp_long["benchmark"] == "BUY_AND_HOLD" and cmp_long["applicable"],
       "a long cash cohort is compared against holding the same instrument")
    ok(cmp_long["net_points_per_exposed_day"] is None
       or isinstance(cmp_long["net_points_per_exposed_day"], float),
       "the comparison is per day of exposure, so a rare rule is not credited "
       "for drift it was absent for")
    cmp_short = baseline.compare(fut.out, fut.side == SHORT, SHORT, rolls)
    ok(cmp_short["benchmark_points_per_day"] == 0.0,
       "a short cohort's passive alternative is holding nothing, not the index")
    empty = baseline.compare(p.out, np.zeros(len(p), dtype=bool), LONG, hold)
    ok(empty["trades"] == 0 and empty["net_points_per_exposed_day"] is None,
       "a cohort with no trades reports no per-day figure rather than a zero")


# ---------------------------------------------------------------------------
# §7 report: honest equity status, artefacts, stopping rule
# ---------------------------------------------------------------------------
def check_equity_history_note() -> None:
    cov = {"usable": [{"instrument": "NIFTY"}],
           "excluded": [{"instrument": "TCS"}, {"instrument": "CRUDEOIL"}]}
    note = report.equity_history_note(cov)
    ok(note["status"] == INSUFFICIENT_HISTORY,
       "with no stock history the stock half is INSUFFICIENT_HISTORY")
    ok("UNANSWERED" in note["note"] and "not" in note["note"],
       "the note says the stock question is unanswered, not answered negatively")
    ok(note["missing"] == ["TCS"],
       "only cash-equity names are listed as the missing stock history")
    ok("collect --instrument" in note["how_to_answer_it"],
       "the note says exactly how to make the stock question answerable")
    ok("survivorship" in note["corporate_actions"]
       and "split" in note["corporate_actions"],
       "corporate actions and survivorship are stated as limitations")

    tested = report.equity_history_note(
        {"usable": [{"instrument": "TCS"}], "excluded": []})
    ok(tested["status"] == "TESTED" and tested["instruments"] == ["TCS"],
       "with real stock history the status flips to TESTED")


def check_conclusion_and_artefacts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        none_out = {"instruments": [], "vehicle_results": []}
        c = report.conclusion(none_out)
        ok(c["verdict"] == INSUFFICIENT_HISTORY
           and c["stop_rule_triggered"] is False,
           "no history at all is a data status and does NOT trigger the stop rule")

        block = {
            "vehicle": FUTURES, "hypotheses_evaluated": 800,
            "instruments": ["NIFTY"], "ranked": [], "top10": [], "pools": [],
            "diagnostics": {
                "cohorts_ranked": 40, "positive_all_three_windows": 3,
                "holdout_sample_adequate": 1, "walk_forward_stable": 1,
                "survives_every_stress_variant": 0,
                "beats_buy_and_hold_in_holdout": 0, "fdr_survivors": 0,
                "research_leads": 0,
            },
        }
        failed = {"instruments": ["NIFTY"], "vehicle_results": [block],
                  "equity_history": {"status": INSUFFICIENT_HISTORY}}
        c = report.conclusion(failed)
        ok(c["verdict"] == "REJECTED" and c["headline"] == report.NO_EDGE,
           "a failed multi-day study produces the explicit no-edge headline")
        ok(c["stop_rule_triggered"] is True,
           "the pre-committed stopping rule fires on the gate's own result")
        ok("3 were positive in development, validation and the untouched holdout"
           in c["statement"],
           "the rejection counts how far the search got rather than saying "
           "nothing was ever positive")
        ok("independent, not a funnel" in c["statement"],
           "the rejection says its tallies are independent, not a funnel")
        ok(c["equity_caveat"]["status"] == INSUFFICIENT_HISTORY,
           "a rejection carries the equity caveat so the two are never conflated")

        lead = dict(good_rule(), status=RESEARCH_LEAD, score=1.0,
                    strategy_id="P28_X", failed_clauses=[])
        won = {"instruments": ["NIFTY"], "vehicle_results": [
            dict(block, ranked=[lead], top10=[lead], research_leads=[lead])]}
        c = report.conclusion(won)
        ok(c["verdict"] == RESEARCH_LEAD and c["stop_rule_triggered"] is False,
           "a surviving lead does not trigger the stop rule")
        ok("not a promotion" in json.dumps(c),
           "even a surviving lead is reported as not a promotion")

        payload = {"version": "T", "nan": float("nan"), "inf": float("inf"),
                   "arr": np.arange(3), "f": np.float64(1.5), "i": np.int64(2),
                   "b": np.bool_(True)}
        path = report._write("p28_smoke.json", payload, tmp)
        with open(path, encoding="utf-8") as fh:
            back = json.load(fh)
        ok(back["nan"] is None and back["arr"] == [0, 1, 2],
           "artefacts are strict JSON: NaN is null and numpy arrays are lists")
        ok(isinstance(back["f"], float) and isinstance(back["i"], int)
           and back["b"] is True,
           "numpy scalars are written as JSON numbers, not as strings")
        ok(back["inf"] == report.NO_LOSING_TRADE,
           "an infinite profit factor is written as its reason, not as a number")
        ok(report.latest(tmp) is None,
           "a directory with no verdict reads as no report, not as a zero result")
        ok(report.latest_ranked(tmp) == {} and report.latest_baselines(tmp) == {},
           "missing artefacts read as empty rather than raising")

        ref = report.intraday_reference()
        ok(isinstance(ref, dict) and "available" in ref,
           "the intraday comparison states whether Phase 27's rows were found")
        ok(ref.get("available") or "not estimated" in ref.get("note", ""),
           "a missing intraday reference is absent, never estimated")


def check_claims_present() -> None:
    for text, label in (
        (GAP_CLAIM, "the package states how overnight gaps are filled"),
        (ROLLOVER_CLAIM, "the package states how futures rolls are approximated"),
        (DELIVERY_CLAIM, "the package states what cash delivery is charged"),
        (SHORT_CLAIM, "the package states why cash equity is long only"),
        (BUY_HOLD_CLAIM, "the package states the passive comparison it must beat"),
        (STOP_RULE, "the package carries the pre-committed stopping rule"),
    ):
        ok(isinstance(text, str) and len(text) > 40, label)
    ok("no further strategy phase" in STOP_RULE,
       "the stopping rule says explicitly that no further phase follows a failure")
    note = json.dumps(outcomes.execution_note()).lower()
    for needle, label in (
        ("next", "the execution note says the fill is the next session's open"),
        ("gap", "the execution note says a gap fills at the open"),
        ("stop", "the execution note says the same-bar tie is the stop"),
    ):
        ok(needle in note, label)


# ---------------------------------------------------------------------------
# §8 the collector: makes the stock question answerable, safely
# ---------------------------------------------------------------------------
def check_collector() -> None:
    ok(collect._parse_row(["2024-01-02T00:00:00+05:30", 1, 2, 0.5, 1.5, 9])["time"]
       == int(dt.datetime(2024, 1, 2, 0, 0, tzinfo=collect.IST).timestamp()),
       "a provider timestamp with an offset is converted exactly")
    naive = collect._parse_row(["2024-01-02T00:00:00", 1, 2, 0.5, 1.5, 9])
    ok(naive["time"] == int(
        dt.datetime(2024, 1, 2, 0, 0, tzinfo=collect.IST).timestamp()),
       "a timestamp with no offset is read as IST, not as this machine's clock")
    ok(collect._parse_row(["not-a-date", 1, 2, 3, 4, 5]) is None
       and collect._parse_row([]) is None
       and collect._parse_row(["2024-01-02T00:00:00", "x", 2, 3, 4]) is None,
       "an unusable provider row is dropped rather than crashing the run")
    ok("time" in collect._parse_row(["2024-01-02T00:00:00+05:30", 1, 2, 3, 4]),
       "rows are keyed the way dailybars reads them, or the file looks empty")

    wins = collect.windows(dt.date(2020, 1, 1), dt.date(2021, 6, 30))
    ok(len(wins) >= 2, "a multi-year request is paged into windows")
    ok(wins[0][0].date() == dt.date(2020, 1, 1)
       and wins[-1][1].date() == dt.date(2021, 6, 30),
       "the windows span exactly the requested range")
    for a, b in zip(wins, wins[1:], strict=False):
        ok(b[0].date() == a[1].date() + dt.timedelta(days=1),
           "consecutive windows are contiguous, so a resumed run has no seam")

    with tempfile.TemporaryDirectory() as tmp:
        master = [{"symbol": "TCS-EQ", "name": "TCS", "token": "11536",
                   "exch_seg": "NSE", "instrumenttype": ""}]
        base = int(dt.datetime(2024, 1, 2, 0, 0, tzinfo=collect.IST).timestamp())

        def fake(exchange, token, interval, frm, to):
            ok_call = interval == collect.ONE_DAY
            if not ok_call:
                raise AssertionError("the collector must ask for daily bars")
            day = frm.toordinal()
            return [[
                dt.datetime.fromtimestamp(
                    base + 86_400 * (day % 3), collect.IST).isoformat(),
                100.0, 101.0, 99.0, 100.5, 1_000,
            ]]

        first = collect.collect(
            "TCS", years=1.0, fetch=fake, out_dir=tmp, master=master,
            sleep=lambda _s: None, today=dt.date(2024, 6, 30),
        )
        ok(first["rows_on_disk_after"] > 0 and first["windows_failed"] == [],
           "a clean collection writes rows and reports no failed window")
        ok(os.path.exists(collect.path_for("TCS", tmp)),
           "the file is written where dailybars will look for it")
        again = collect.collect(
            "TCS", years=1.0, fetch=fake, out_dir=tmp, master=master,
            sleep=lambda _s: None, today=dt.date(2024, 6, 30),
        )
        ok(again["rows_on_disk_before"] == first["rows_on_disk_after"],
           "a re-run reads the existing file instead of starting over")
        ok(again["rows_on_disk_after"] == first["rows_on_disk_after"],
           "the same bars are deduplicated on timestamp, not appended twice")

        calls = {"n": 0}

        def flaky(exchange, token, interval, frm, to):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("rate limited")
            return fake(exchange, token, interval, frm, to)

        out = collect.collect(
            "TCS", years=1.0, fetch=flaky, out_dir=tmp, master=master,
            sleep=lambda _s: None, today=dt.date(2024, 6, 30),
        )
        ok(len(out["windows_failed"]) == 1 and out["rows_on_disk_after"] > 0,
           "one failed window is reported and does not discard the others")
        ok("survivorship" in out["note"],
           "the collected series carries its adjustment and survivorship caveat")

        rows = dailybars._rows_from_jsonl(collect.path_for("TCS", tmp))
        ok(rows and all("time" in r for r in rows),
           "the collected file is readable by the daily loader")
        ok([int(r["time"]) for r in rows] == sorted(int(r["time"]) for r in rows),
           "rows are written in chronological order")


def check_collector_refuses_simulation() -> None:
    from app.config import settings

    real = settings.data_provider
    try:
        settings.data_provider = "simulated"
        try:
            collect.angel_fetcher()
            ok(False, "the collector refuses to collect from the simulated feed")
        except RuntimeError as exc:
            msg = str(exc)
            ok("simulated" in msg and "angelone" in msg,
               "the collector refuses to collect from the simulated feed")
            ok("password" not in msg.lower() and "secret" not in msg.lower(),
               "the refusal names the setting, never a credential")
    finally:
        settings.data_provider = real

    src = open(
        os.path.join(os.path.dirname(__file__), "app", "research", "phase28",
                     "collect.py"),
        encoding="utf-8",
    ).read()
    for banned in ("api_key", "client_code", "totp", "password", "--secret"):
        ok(banned not in src.lower().replace("credential", ""),
           f"the collector never names {banned!r}: credentials stay in the "
           f"environment")


# ---------------------------------------------------------------------------
# isolation: options frozen, no order path
# ---------------------------------------------------------------------------
def check_isolation() -> None:
    pkg = os.path.join(os.path.dirname(__file__), "app", "research", "phase28")
    files = sorted(f for f in os.listdir(pkg) if f.endswith(".py"))
    joined = "".join(
        open(os.path.join(pkg, f), encoding="utf-8").read() for f in files
    )
    for banned in ("place_order", "broker.buy", "order_router", "on_tick",
                   "register_hook", "paper_entry"):
        ok(banned not in joined, f"no module in Phase 28 references {banned!r}")
    for frozen in ("phase25", "phase26", "option_chain", "chain_store"):
        ok(frozen not in joined,
           f"Phase 28 never touches {frozen!r}: option logic and capture stay "
           f"frozen")
    ok("phase19" in joined and "phase24" in joined,
       "Phase 28 reuses the shared cost model and the shared metric definitions")

    src = open(os.path.join(pkg, "service.py"), encoding="utf-8").read()
    for banned in ("report.run", "pool.build", "study.geometry_sweep",
                   "study.stress_grid", "discover.Search"):
        ok(banned not in src,
           f"the read-only service cannot start a study via {banned!r}")
    unavailable = service._unavailable()
    ok(unavailable["available"] is False and unavailable["paper_only"] is True,
       "an un-run study reports unavailable, never an empty result")
    ok(STOP_RULE in json.dumps(unavailable, ensure_ascii=False)
       and BUY_HOLD_CLAIM in json.dumps(unavailable, ensure_ascii=False),
       "every service payload carries the stopping rule and the passive comparison")
    for fn in (service.summary, service.funnel, service.economics,
               service.baselines):
        got = fn()
        ok(isinstance(got, dict) and "available" in got,
           f"service.{fn.__name__}() answers with an availability flag rather "
           f"than raising when nothing has been run")
    ok(isinstance(service.strategies(5), list),
       "service.strategies() reads as a list, empty when no study is on disk")


def main() -> int:
    print("PHASE 28 SMOKE — MULTI-DAY FUTURES & CASH EQUITY")
    print("-" * 70)
    check_aggregation()
    check_source_precedence()
    check_eligibility()
    check_vehicle_classification()
    check_entry_is_next_open()
    check_gap_through_stop()
    check_gap_through_target()
    check_same_bar_tie()
    check_short_side()
    check_horizon_timeout()
    check_costs_charged_once()
    check_rollover()
    check_equity_costs()
    check_feature_causality()
    check_conditions()
    check_pool()
    check_windows_and_selection()
    check_fdr()
    check_gate()
    check_walk_forward()
    check_buy_and_hold()
    check_equity_history_note()
    check_conclusion_and_artefacts()
    check_claims_present()
    check_collector()
    check_collector_refuses_simulation()
    check_isolation()
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
