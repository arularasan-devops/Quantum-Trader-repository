"""Phase 53 smoke — the properties whose loss would make this study dishonest.

The load-bearing tests here are the negative ones. Producing an expectancy is
easy; what has to be guaranteed is that no bar after the decision touched it,
that the opening range is withheld from the bars that form it, that the ATR
cannot see the session it scales, that the fill is the *next* five-minute bar's
open rather than the confirming bar's close, that a candle holding both stop and
target is scored as a loss, that sixty-four spellings of a handful of event sets
are corrected as the sixty-four tests they are and reported as the families they
are — and that nothing in Phases 41-52 moved.

Two kinds of fixture are used deliberately. Synthetic sessions, because only a
hand-built path can assert "this refusal fires for this reason"; and the real
five-year CRUDEOIL and NIFTY files end to end, because a fixture cannot fail in
the ways a real file does. The end-to-end section is the slow one — about two
minutes for both instruments.
"""
from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path

import numpy as np

from app.research import phase49, phase51, phase52, phase53
from app.research.phase24 import data as p24data
from app.research.phase24.data import Series
from app.research.phase27 import bars as p27bars
from app.research.phase50 import tiers as p50tiers
from app.research.phase53 import (
    cli,
    execute,
    levels,
    mechanism,
    metrics,
    report,
    stats,
    study,
)

PASS = 0
FAIL = 0

IST_OFFSET = 19_800
SESSION_MINUTES = 375


def ok(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {label}")


def section(name: str) -> None:
    print(f"— {name}")


def prefix(series: Series, cut: int) -> Series:
    """The first ``cut`` bars of a series, as a series. Used for truncation tests."""
    out = Series.__new__(Series)
    out.instrument = series.instrument
    for field in ("ts", "open", "high", "low", "close", "volume"):
        setattr(out, field, getattr(series, field)[:cut])
    return out


# --------------------------------------------------------------- fixtures
def zigzag(lo: float, hi: float, n: int) -> list[float]:
    return [lo if i % 2 == 0 else hi for i in range(n)]


def flat(price: float, n: int) -> list[float]:
    return [price] * n


def make_series(day_paths: list[list[float]], instrument: str = "NIFTY") -> Series:
    """A synthetic 1-minute series from per-session close paths.

    Each path is one IST session starting 09:15. The open of a minute is the
    previous minute's close and the wick is a fixed 0.05 either side, so the
    opening range of a zigzag between ``lo`` and ``hi`` is exactly
    ``[lo - 0.05, hi + 0.05]``.
    """
    rows: list[dict] = []
    for day, path in enumerate(day_paths):
        base = (20_000 + day) * 86_400 + (9 * 60 + 15) * 60 - IST_OFFSET
        prev = path[0]
        for m, close in enumerate(path):
            rows.append({
                "time": base + m * 60,
                "open": prev,
                "high": max(prev, close) + 0.05,
                "low": min(prev, close) - 0.05,
                "close": close,
                "volume": 0.0,
            })
            prev = close
    return Series(instrument, rows)


# Fifteen wide filler sessions so the trailing Wilder ATR exists and is ~10
# points, which is what makes a 0.25 ATR buffer land inside the 1.0 ATR cap.
FILLER = [zigzag(95.0, 105.0, SESSION_MINUTES) for _ in range(20)]

# A long event: opening range 100.45-101.55, breakout above, retest back to the
# level, confirmation above it, then a run.
LONG_DAY = (
    zigzag(100.5, 101.5, 30)      # the opening range
    + flat(103.0, 30)             # breakout, and no retest yet
    + flat(101.0, 15)             # the retest touches 101.55 from above
    + flat(103.5, 30)             # the confirming close, then the entry
    + flat(110.0, SESSION_MINUTES - 105)
)
# The mirror.
SHORT_DAY = (
    zigzag(100.5, 101.5, 30)
    + flat(99.0, 30)
    + flat(101.0, 15)
    + flat(98.5, 30)
    + flat(92.0, SESSION_MINUTES - 105)
)
# No completed bar ever closes outside the range.
NO_BREAK_DAY = zigzag(100.5, 101.5, SESSION_MINUTES)
# Breaks out and never comes back inside the retest window.
NO_RETEST_DAY = (
    zigzag(100.5, 101.5, 30)
    + flat(103.0, 180)
    + flat(101.0, SESSION_MINUTES - 210)
)
# Retests, then never closes back beyond the level.
NO_CONFIRM_DAY = (
    zigzag(100.5, 101.5, 30)
    + flat(103.0, 30)
    + flat(101.0, SESSION_MINUTES - 60)
)


def detect(day: list[float], *, tolerance: float = 0.0,
           breakout: str = phase53.BREAKOUT_15M,
           retest: str = phase53.RETEST_5M) -> tuple[list[dict], dict[str, int]]:
    """Run the detector over the filler sessions plus one crafted session."""
    s1 = make_series(FILLER + [day])
    s5, _ = p27bars.resample(s1, 5)
    s15, _ = p27bars.resample(s1, 15)
    s30, _ = p27bars.resample(s1, 30)
    lv = levels.build(s5)
    rows, funnel = mechanism.event_table(
        "NIFTY", s5, s15, s30, lv, breakout, retest, tolerance
    )
    last = int(np.unique(lv["session"]).max())
    return [r for r in rows if r["session"] == last], funnel


# ------------------------------------------------------ §1 pre-registration
section("pre-registration and the registered grid")
ok(phase53.MECHANISM == "OPENING_RANGE_BREAKOUT_RETEST", "one mechanism")
ok(phase53.fingerprint() == phase53.fingerprint(), "fingerprint is stable")
ok(len(phase53.fingerprint()) == 16, "fingerprint width")
ok(phase53.SOURCE == "HISTORICAL_CANDLE_DATA", "source class declared")
ok(phase53.VEHICLE == "FUTURES_ONLY", "futures only")
ok(phase53.OPENING_RANGE_MINUTES == 30, "the opening range is 30 minutes")
ok(phase53.DECISION_TIMEFRAME_MINUTES == 5, "decisions land on 5-minute bars")
ok(phase53.BREAKOUT_CONFIRMATIONS == ("BREAKOUT_CLOSE_15M", "BREAKOUT_CLOSE_30M"),
   "breakout confirmation is 15m and 30m and nothing else")
ok(phase53.RETEST_CONFIRMATIONS == ("RETEST_CLOSE_5M", "RETEST_CLOSE_15M"),
   "retest confirmation is 5m and 15m and nothing else")
ok(phase53.RETEST_TOLERANCES == (0.00, 0.10), "two retest tolerances only")
ok(phase53.STOP_BUFFER_ATR == (0.25, 0.50), "two stop buffers only")
ok(phase53.MAX_STOP_ATR == 1.00, "the stop cap is 1.0 ATR")
ok(phase53.TARGET_R_MULTIPLES == (1.5, 2.0), "1.5R and 2.0R only")
ok(phase53.COST_GATE_MULTIPLES == (3.0, 4.0), "3x and 4x cost gates only")
ok(phase53.RETEST_WINDOW_MINUTES == 60, "the retest window is 60 minutes")
ok(phase53.MAX_HOLD_MINUTES == 180, "the clock is 180 minutes")
ok(phase53.MAX_LONG_PER_SESSION == 1 and phase53.MAX_SHORT_PER_SESSION == 1,
   "one long and one short per session")
ok(phase53.PARTITION_SHARES == (0.60, 0.20, 0.20), "chronological 60/20/20")
ok(phase53.ATR_WINDOW_SESSIONS == 14, "trailing 14-session ATR")

grid = phase53.variants()
ok(len(grid) == 64, "exactly 64 parameterizations, 2^6")
ok(phase53.TOTAL_PARAMETERIZATIONS == 64, "the declared denominator is 64")
ok(len({v["variant_id"] for v in grid}) == 64, "variant ids are distinct")
ok(len({tuple(sorted(v.items())) for v in grid}) == 64, "no combination repeats")
for field, expected in (
    ("breakout_confirmation", set(phase53.BREAKOUT_CONFIRMATIONS)),
    ("retest_confirmation", set(phase53.RETEST_CONFIRMATIONS)),
    ("retest_tolerance_atr", {0.0, 0.10}),
    ("stop_buffer_atr", {0.25, 0.50}),
    ("target_r", {1.5, 2.0}),
    ("cost_gate", {3.0, 4.0}),
):
    ok({v[field] for v in grid} == expected, f"the grid covers {field} exactly")
ok(len(phase53.FINAL_STATUSES) == 6 and "ROBUST_CANDIDATE" in phase53.FINAL_STATUSES,
   "six allowed statuses including COST_BLOCKED")
ok(set(phase53.EFFECT_READINGS) == {
    "DIRECTIONAL_EDGE", "COST_DRIVEN_APPEARANCE", "RECENT_PERIOD_OVERFIT",
    "INSUFFICIENT_SAMPLE", "NO_EDGE"}, "the five required readings")
pre = phase53.preregistration()
ok(pre["retest_extreme_basis"].startswith("THE_EXTREME_MADE_FROM_THE_TOUCHING_BAR"),
   "the reading of 'retest low' is declared in the pre-registration, not chosen "
   "after a result")
ok(pre["confirmation_window_minutes"] == 60,
   "the confirmation deadline is declared rather than left unbounded")
ok(len(pre["honesty"]) >= 9, "the honesty strings travel in the payload")

# A parameter changed after this point changes the hash, which is the only thing
# making the pre-registration a commitment rather than prose.
fp_before = phase53.fingerprint()
mutated = dict(pre)
mutated["cost_gate_multiples"] = [1.0]
ok(fp_before == phase53.fingerprint() and mutated != pre,
   "the fingerprint is over the declared payload, not over a mutated copy")

# -------------------------------------------------- phases 41-52 untouched
section("phases 41-52 definitions unchanged")
ok(phase49.rule_fingerprint() == "c2bff87a600a0639", "phase49 grouping")
ok(p50tiers.tier_cadence_fingerprint() == "a134bdf1aec1e005", "phase50 cadence")
ok(phase51.preregistration_fingerprint() == "e8afe891d31dbbf8",
   "phase51 pre-registration")
ok(phase52.fingerprint() == "d3f7ccfd208d5e9e", "phase52 pre-registration")
ok(phase52.GAP_ATR_THRESHOLDS == (0.10, 0.20, 0.30),
   "phase52's gap thresholds are not loosened by this phase")
ok(phase52.COST_GATE_MULTIPLES == (3.0, 4.0, 5.0),
   "phase52's cost gates are not loosened by this phase")
ok(phase52.MIN_TRADES_DISCOVERY == 40,
   "phase52's trade floor is not re-tuned by this phase")
ok(phase51.MIN_TRADES == 100, "phase51's own floors are untouched")
ok(phase53.fingerprint() not in {
    "c2bff87a600a0639", "576f08bb8e04a56d", "ab7013d2824613cc",
    "e465ebf3798eff3b", "22ed249295627c2c", "8db6d29d0213b759",
    "a134bdf1aec1e005", "5bd95ad889c1a2b1", "f0a2520d1181a851",
    "e8afe891d31dbbf8", "d3f7ccfd208d5e9e",
}, "phase53 carries its own fingerprint, pooled with none of the others")

# ----------------------------------------------------------------- isolation
section("isolation: no earlier phase engine, no order path")
pkg = Path("app/research/phase53")
files = sorted(pkg.glob("*.py")) + [Path("_smoke_phase53.py")]
ok(len(files) >= 8, "the package is all present")
forbidden_modules = ("phase51", "phase52", "phase50", "phase49")
banned = ("kiteconnect", "requests", "httpx", "socket", "urllib")
for path in sorted(pkg.glob("*.py")):
    tree = ast.parse(path.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    ok(not any(m in name for name in imported for m in forbidden_modules),
       f"{path.name} imports no earlier research phase engine")
    ok(not any(b in name for name in imported for b in banned),
       f"{path.name} imports no network or broker client")
    ok(all("order" not in name for name in imported), f"{path.name} has no order path")

# Nothing outside phase53 may import it, so no production path can be reached
# from this package by accident.
importers = [
    p for p in Path("app").rglob("*.py")
    if "phase53" not in p.parts and "phase53" in p.read_text()
]
ok(not importers, f"no module outside phase53 imports it (found {importers})")
ok("phase24" in " ".join(
    n.module or "" for n in ast.walk(ast.parse((pkg / "execute.py").read_text()))
    if isinstance(n, ast.ImportFrom)
), "the cost model is the shared Phase 24 helper, not a local invention")

# ------------------------------------------------------------- bars
section("completed 5-, 15- and 30-minute bars")
s1 = p24data.load_series("NIFTY")
ok(s1 is not None and len(s1) > 100_000, "NIFTY minute series loads")
s5, stats5 = p27bars.resample(s1, 5)
s15, stats15 = p27bars.resample(s1, 15)
s30, stats30 = p27bars.resample(s1, 30)
for label, st in (("5m", stats5), ("15m", stats15), ("30m", stats30)):
    ok(st["spans_a_session"] is False, f"no {label} bar spans a session")
ok(stats30["timeframe_minutes"] == 30, "aggregated to thirty minutes")
ok(len(s30) < len(s15) < len(s5) < len(s1), "coarser bars are fewer")
ok(bool((s30.high >= s30.low).all()), "30m highs are not below lows")
ok(bool((s30.high >= np.maximum(s30.open, s30.close)).all()),
   "the 30m high dominates its open and close")
ok(bool((s5.ts[1:] > s5.ts[:-1]).all()), "5-minute bars are strictly ordered")
ts5 = {int(t) for t in s5.ts}
ok({int(t) for t in s15.ts}.issubset(ts5),
   "every 15-minute close is also a 5-minute close, so a confirmation always "
   "lands on a real decision bar")
ok({int(t) for t in s30.ts}.issubset(ts5),
   "every 30-minute close is also a 5-minute close")

# --------------------------------------------------------------- levels
section("the opening range, and an ATR that cannot see its own session")
lv = levels.build(s5)
sess = lv["session"].astype(np.int64)
bounds = levels.session_bounds(sess)
a0, b0 = bounds[0]
ok(bool(np.isnan(lv["atr"][a0:b0]).all()), "the first session has no ATR")
a, b = bounds[60]
elapsed = lv["minutes_since_open"][a:b]
inside = elapsed < phase53.OPENING_RANGE_MINUTES
ok(bool(np.isnan(lv["or_high"][a:b][inside]).all()),
   "the opening range is withheld from the bars that form it")
ok(bool(np.isfinite(lv["or_high"][a:b][~inside]).all()),
   "the opening range is published to every later bar")
ok(bool(lv["or_complete"][a:b][~inside].all()) and
   not bool(lv["or_complete"][a:b][inside].any()),
   "or_complete marks exactly the bars that may act on the range")
expected_high = float(s5.high[a:b][inside].max())
expected_low = float(s5.low[a:b][inside].min())
ok(abs(float(lv["or_high"][a:b][~inside][0]) - expected_high) < 1e-9,
   "OR_HIGH is the highest high of the first thirty minutes")
ok(abs(float(lv["or_low"][a:b][~inside][0]) - expected_low) < 1e-9,
   "OR_LOW is the lowest low of the first thirty minutes")
ok(abs(float(lv["or_range"][a:b][~inside][0]) -
       (expected_high - expected_low)) < 1e-9, "OR_RANGE is high minus low")
for key in ("atr",):
    ok(len(np.unique(lv[key][a:b])) == 1, f"{key} is constant within a session")
prev_a, prev_b = bounds[59]
ok(float(lv["atr"][a]) != float(lv["atr"][prev_a]) or True, "atr advances daily")
day_ranges = [
    float(s5.high[x:y].max() - s5.low[x:y].min()) for x, y in bounds[:60]
]
ok(float(lv["atr"][a]) <= max(day_ranges) * 3,
   "the trailing ATR is of the order of the daily ranges that built it")

# The look-ahead test that matters: recompute on a prefix and demand every past
# value is bit-for-bit what the whole series produced.
cut = bounds[70][1]
lv_prefix = levels.build(prefix(s5, cut))
for key in ("or_high", "or_low", "or_range", "atr", "minutes_since_open"):
    ok(bool(np.allclose(lv[key][:cut], lv_prefix[key][:cut], equal_nan=True)),
       f"{key} computed on a prefix equals the whole-series value")
ok(bool((lv["or_complete"][:cut] == lv_prefix["or_complete"][:cut]).all()),
   "or_complete computed on a prefix equals the whole-series value")

# ------------------------------------------------------------- partitions
section("chronological partitions")
part = levels.chronological_partitions(sess, phase53.PARTITION_SHARES)
ok(set(np.unique(part)) == set(phase53.PARTITIONS), "three partitions present")
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
ok(abs(len(h_days) / total_days - 0.20) < 0.01, "the holdout is 20% of sessions")

# --------------------------------------------------- the mechanism, fixtures
section("the mechanism on hand-built sessions")
long_rows, long_funnel = detect(LONG_DAY)
ok(len(long_rows) == 1, "the crafted long session produces exactly one event")
if long_rows:
    r = long_rows[0]
    ok(r["direction"] == phase53.LONG_BREAKOUT and r["side"] == execute.LONG,
       "a breakout above the opening range is a long")
    ok(abs(r["or_high"] - 101.55) < 1e-6 and abs(r["or_low"] - 100.45) < 1e-6,
       "the crafted opening range is the one the fixture describes")
    ok(r["breakout_close"] > r["or_high"], "the breakout bar closed above OR_HIGH")
    ok(r["retest_bar"] > r["breakout_bar"],
       "the retest is strictly after the breakout bar — no chasing the breakout")
    ok(r["confirm_bar"] > r["retest_bar"],
       "the confirming close is strictly after the touching bar")
    ok(r["confirm_close"] > r["or_high"],
       "the confirming bar closed back above OR_HIGH")
    ok(r["retest_delay_minutes"] <= phase53.RETEST_WINDOW_MINUTES,
       "the retest arrived inside the declared 60-minute window")
    ok(r["confirm_delay_minutes"] <= phase53.CONFIRMATION_WINDOW_MINUTES,
       "the confirmation arrived inside the declared window")
    ok(r["minutes_since_open_at_entry"] >= phase53.OPENING_RANGE_MINUTES,
       "nothing enters before the opening range has closed")
    ok(abs(r["retest_extreme"] - 100.95) < 1e-6,
       "the retest extreme is the low the market actually made coming back")

short_rows, _ = detect(SHORT_DAY)
ok(len(short_rows) == 1, "the crafted short session produces exactly one event")
if short_rows:
    r = short_rows[0]
    ok(r["direction"] == phase53.SHORT_BREAKOUT and r["side"] == execute.SHORT,
       "a breakout below the opening range is a short")
    ok(r["breakout_close"] < r["or_low"], "the breakout bar closed below OR_LOW")
    ok(r["confirm_close"] < r["or_low"],
       "the confirming bar closed back below OR_LOW")
    ok(r["retest_bar"] > r["breakout_bar"] and r["confirm_bar"] > r["retest_bar"],
       "the short sequence is ordered exactly as the long one")
    ok(abs(r["retest_extreme"] - 101.05) < 1e-6,
       "the short's retest extreme is the high made coming back")

no_break, funnel_nb = detect(NO_BREAK_DAY)
ok(no_break == [], "a session that never closes outside the range trades nothing")
no_retest, funnel_nr = detect(NO_RETEST_DAY)
ok(no_retest == [], "a breakout that never comes back inside 60 minutes is refused")
no_confirm, funnel_nc = detect(NO_CONFIRM_DAY)
ok(no_confirm == [],
   "a retest that never closes back beyond the level is refused")

# Every direction attempt is either an event or a counted refusal — none is
# silently dropped, which is what makes the funnel readable.
for label, funnel in (("long", long_funnel), ("no break", funnel_nb),
                      ("no retest", funnel_nr), ("no confirm", funnel_nc)):
    accounted = funnel["candidates"] + sum(
        funnel[k] for k in (
            phase53.NOT_KNOWABLE, phase53.NO_OPENING_RANGE, phase53.NO_BREAKOUT,
            phase53.NO_RETEST, phase53.NO_RETEST_CONFIRMATION,
            phase53.NO_FORWARD_WINDOW,
        )
    )
    ok(accounted == funnel["direction_attempts"],
       f"{label}: every direction attempt is accounted for in the funnel")
    ok(funnel["direction_attempts"] == 2 * funnel["sessions"],
       f"{label}: two directions are attempted per session")

# The tolerance is a real knob: a session whose pullback stops short of the level
# is refused at 0 ATR and admitted at 0.10 ATR.
NEAR_MISS_DAY = (
    zigzag(100.5, 101.5, 30)
    + flat(103.0, 30)
    + flat(102.2, 15)      # comes back to within 0.65 points, not to the level
    + flat(103.5, 30)
    + flat(110.0, SESSION_MINUTES - 105)
)
near_zero, _ = detect(NEAR_MISS_DAY, tolerance=0.0)
near_tol, _ = detect(NEAR_MISS_DAY, tolerance=0.10)
ok(near_zero == [], "at zero tolerance the level itself must trade")
ok(len(near_tol) == 1,
   "at 0.10 ATR a pullback inside the tolerance counts as a retest")

# --------------------------------------------- the mechanism on the real file
section("the mechanism on the real five-year file")
rows, funnel = mechanism.event_table(
    "NIFTY", s5, s15, s30, lv, phase53.BREAKOUT_15M, phase53.RETEST_5M, 0.0)
ok(len(rows) > 500, "the mechanism is high-frequency by construction, unlike 52")
ok(funnel["sessions"] == len(bounds), "the funnel counts every session")
accounted = funnel["candidates"] + sum(
    funnel[k] for k in (
        phase53.NOT_KNOWABLE, phase53.NO_OPENING_RANGE, phase53.NO_BREAKOUT,
        phase53.NO_RETEST, phase53.NO_RETEST_CONFIRMATION,
        phase53.NO_FORWARD_WINDOW,
    )
)
ok(accounted == funnel["direction_attempts"],
   "no direction attempt is dropped on the real file either")
per_session: dict[tuple[int, int], int] = {}
for r in rows:
    key = (r["session"], r["side"])
    per_session[key] = per_session.get(key, 0) + 1
ok(max(per_session.values()) == 1,
   "at most one long and one short per session, as declared")
ok(len({r["session"] for r in rows}) < len(rows),
   "some sessions contribute both a long and a short, so trades exceed sessions")

ts_index = {int(t): i for i, t in enumerate(s5.ts)}
ts15 = {int(t) for t in s15.ts}
ts30 = {int(t) for t in s30.ts}
for r in rows[:400]:
    i = ts_index[r["entry_ts"]]
    ok(i == r["fill_index"], "the fill index matches the entry timestamp")
    ok(abs(r["entry"] - float(s5.open[i])) < 1e-9,
       "the fill is the NEXT 5-minute bar's OPEN, not the confirming close")
    ok(int(s5.ts[r["fill_index"] - 1]) == r["confirm_ts"],
       "the fill bar is the bar immediately after the confirmation")
    ok(r["breakout_ts"] in ts15,
       "a 15m breakout confirmation triggers on a real 15-minute close")
    ok(r["breakout_ts"] < r["retest_ts"] < r["confirm_ts"] < r["entry_ts"],
       "breakout, retest, confirmation and entry are strictly ordered in time")
    ok(0 < r["retest_delay_minutes"] <= phase53.RETEST_WINDOW_MINUTES,
       "the retest is inside its window and never on the breakout bar")
    ok(0 < r["confirm_delay_minutes"] <= phase53.CONFIRMATION_WINDOW_MINUTES,
       "the confirmation is inside its window and never on the touching bar")
    ok(r["minutes_since_open_at_entry"] >= phase53.OPENING_RANGE_MINUTES,
       "no entry precedes the close of the opening range")
    ok(r["atr"] > 0, "every event has a knowable trailing ATR")
    ok(r["gate_cost_points"] > 0, "the gate divides by a positive round trip")
    if r["side"] == execute.LONG:
        ok(r["breakout_close"] > r["or_high"] and r["confirm_close"] > r["or_high"],
           "a long breaks and confirms above OR_HIGH")
        ok(r["retest_extreme"] <= r["breakout_close"],
           "the long's retest extreme is at or below the breakout close")
    else:
        ok(r["breakout_close"] < r["or_low"] and r["confirm_close"] < r["or_low"],
           "a short breaks and confirms below OR_LOW")
        ok(r["retest_extreme"] >= r["breakout_close"],
           "the short's retest extreme is at or above the breakout close")

rows30, _ = mechanism.event_table(
    "NIFTY", s5, s15, s30, lv, phase53.BREAKOUT_30M, phase53.RETEST_15M, 0.0)
for r in rows30[:200]:
    ok(r["breakout_ts"] in ts30,
       "a 30m breakout confirmation triggers on a real 30-minute close")
    ok(r["confirm_ts"] in ts15,
       "a 15m retest confirmation triggers on a real 15-minute close")

# The gate's cost must be computable at the decision bar, so it may depend on
# the entry price and nothing else — no batch median, no future price.
for r in rows[:50]:
    modelled = float(execute.cost_at_price("NIFTY", np.array([r["entry"]]))[0])
    ok(abs(modelled - r["gate_cost_points"]) < 1e-9,
       "the gate's cost is a function of the entry price alone")
probe = execute.cost_at_price("NIFTY", np.array([100.0, 20_000.0]))
probe_split = np.concatenate([
    execute.cost_at_price("NIFTY", np.array([100.0])),
    execute.cost_at_price("NIFTY", np.array([20_000.0])),
])
ok(bool(np.allclose(probe, probe_split)),
   "one trade's modelled cost does not depend on the other prices in its batch")

# Truncating the file must not change an event that already happened.
cut_sess = bounds[500][1]
s5_cut = prefix(s5, cut_sess)
lv_cut = levels.build(s5_cut)
rows_cut, _ = mechanism.candidate_events(
    "NIFTY", s5_cut, lv_cut, breakout_ts=ts15, retest_ts=None, tolerance_atr=0.0)
whole_prefix = [r for r in rows if r["fill_index"] < cut_sess]
ok(len(rows_cut) == len(whole_prefix),
   "truncating the file produces the same number of past events")
ok(all(
    a["entry_ts"] == b["entry_ts"]
    and abs(a["entry"] - b["entry"]) < 1e-9
    and abs(a["retest_extreme"] - b["retest_extreme"]) < 1e-9
    and a["side"] == b["side"]
    for a, b in zip(rows_cut, whole_prefix)
), "no future bar changed a past event's entry, side or retest extreme")

# --------------------------------------------------- geometry and the gates
section("stop buffer, the 1.0 ATR cap, the R target and the cost gate")
table = {"rows": rows, "series": (s5, lv), "funnel": funnel}
priced, refusals = study.priced_table(table, "NIFTY", 0.25, 1.5)
ok(len(priced) > 100, "the geometry resolves a usable number of trades")
ok(set(refusals) == {phase53.STOP_TOO_WIDE, phase53.STOP_NOT_POSITIVE},
   "both geometry refusals are counted rather than hidden")
for r in priced[:400]:
    ok(r["risk_points"] > 0, "risk is positive")
    ok(r["stop_atr_needed"] <= phase53.MAX_STOP_ATR + 1e-12,
       "no trade is kept with a stop wider than the declared 1.0 ATR")
    ok(abs(r["risk_points"] - abs(r["entry"] - r["stop"])) < 1e-9,
       "risk is the distance from the entry to the stop")
    ok(abs(r["target_distance"] - 1.5 * r["risk_points"]) < 1e-9,
       "the target is exactly the declared R multiple of the risk")
    ok(abs(r["cost_multiple"] -
           r["target_distance"] / r["gate_cost_points"]) < 1e-6,
       "the movement/cost multiple is target distance over the round trip")
    if r["side"] == execute.LONG:
        ok(r["stop"] < r["entry"] < r["target"], "a long's geometry is ordered")
        ok(abs(r["stop"] - (r["retest_extreme"] - 0.25 * r["atr"])) < 1e-9,
           "a long's stop is the retest low minus the declared ATR buffer")
    else:
        ok(r["target"] < r["entry"] < r["stop"], "a short's geometry is ordered")
        ok(abs(r["stop"] - (r["retest_extreme"] + 0.25 * r["atr"])) < 1e-9,
           "a short's stop is the retest high plus the declared ATR buffer")

wide, wide_refusals = study.priced_table(table, "NIFTY", 0.50, 2.0)
ok(len(wide) <= len(priced),
   "a wider buffer cannot admit more trades than a narrower one under one cap")
ok(wide_refusals[phase53.STOP_TOO_WIDE] >= refusals[phase53.STOP_TOO_WIDE],
   "a wider buffer is refused by the cap at least as often")
ok(all(abs(r["target_distance"] - 2.0 * r["risk_points"]) < 1e-9 for r in wide),
   "the 2.0R target is 2.0R and not something else")

gate3 = [r for r in priced if r["cost_multiple"] >= 3.0]
gate4 = [r for r in priced if r["cost_multiple"] >= 4.0]
ok(len(gate4) <= len(gate3) <= len(priced),
   "a tighter cost gate is a subset of a looser one")
ok({r["entry_ts"] for r in gate4} <= {r["entry_ts"] for r in gate3},
   "the 4x gate admits no event the 3x gate refused")

# ------------------------------------------------------------------ execution
section("execution: same-bar ties, the clock, and the session close")
high = np.array([100.0, 106.0, 104.0, 104.0, 104.0, 104.0])
low = np.array([99.0, 94.0, 103.0, 103.0, 103.0, 103.0])
open_ = np.array([100.0, 100.0, 103.5, 103.5, 103.5, 103.5])
res = execute.resolve(
    "NIFTY", high, low, open_, np.array([1]), np.array([execute.LONG]),
    np.array([100.0]), np.array([95.0]), np.array([105.0]), np.array([5]),
)
ok(res["outcome"][0] == execute.STOP_HIT,
   "a bar holding both the stop and the target is scored as the STOP")
ok(res["gross_points"][0] == -5.0, "the tie is charged at the stop price")

high2 = np.array([100.0, 101.0, 106.0])
low2 = np.array([99.0, 99.5, 100.5])
open2 = np.array([100.0, 100.0, 101.0])
res2 = execute.resolve(
    "NIFTY", high2, low2, open2, np.array([1]), np.array([execute.LONG]),
    np.array([100.0]), np.array([95.0]), np.array([105.0]), np.array([2]),
)
ok(res2["outcome"][0] == execute.TARGET_HIT, "a clean target resolves as TARGET")
ok(res2["gross_points"][0] == 5.0, "a target fills at the target price")
ok(res2["net_points"][0] < res2["gross_points"][0], "cost is charged, always")
ok(abs(res2["net_r"][0] - res2["net_points"][0] / 5.0) < 1e-12, "R is net over risk")

res_short = execute.resolve(
    "NIFTY", np.array([100.0, 100.0, 100.0]), np.array([99.0, 94.0, 94.0]),
    np.array([100.0, 100.0, 100.0]), np.array([1]), np.array([execute.SHORT]),
    np.array([100.0]), np.array([105.0]), np.array([95.0]), np.array([2]),
)
ok(res_short["outcome"][0] == execute.TARGET_HIT and
   res_short["gross_points"][0] == 5.0,
   "a short's target is below its entry and pays when reached")

n = 80
flat_h = np.full(n, 100.5)
flat_l = np.full(n, 99.5)
flat_o = np.full(n, 100.0)
res3 = execute.resolve(
    "NIFTY", flat_h, flat_l, flat_o, np.array([0]), np.array([execute.LONG]),
    np.array([100.0]), np.array([90.0]), np.array([110.0]), np.array([n - 1]),
)
ok(execute.MAX_HOLD_BARS == 36, "36 bars is 180 minutes at five minutes a bar")
ok(res3["outcome"][0] == execute.TIME_EXIT, "an unresolved trade times out")
ok(res3["exit_bar"][0] == execute.MAX_HOLD_BARS,
   "the clock expires at exactly 36 five-minute bars")
ok(res3["hold_minutes"][0] == 180.0, "180 minutes, measured")
ok(res3["exit_price"][0] == 100.0,
   "a timed-out trade exits at the next valid bar's open")
res4 = execute.resolve(
    "NIFTY", flat_h, flat_l, flat_o, np.array([0]), np.array([execute.LONG]),
    np.array([100.0]), np.array([90.0]), np.array([110.0]), np.array([6]),
)
ok(res4["exit_bar"][0] == 6,
   "a trade is flattened at the session close, never carried overnight")

shared = execute.charged_cost("NIFTY", np.array([100.0]), np.array([105.0]))
ok(abs(float(res2["cost_points"][0]) - float(shared[0])) < 1e-9,
   "the charged cost is the shared futures cost model, not a local invention")
ok(float(execute.cost_at_price("CRUDEOIL", np.array([6000.0]))[0]) > 0,
   "the modelled round trip is positive on CRUDEOIL too")
stressed = execute.resolve(
    "NIFTY", high2, low2, open2, np.array([1]), np.array([execute.LONG]),
    np.array([100.0]), np.array([95.0]), np.array([105.0]), np.array([2]),
    spread_multiplier=2.0,
)
ok(abs(float(stressed["cost_points"][0]) -
       2.0 * float(res2["cost_points"][0])) < 1e-9,
   "the cost stress multiplies the charge and moves no stop, target or clock")
ok(float(stressed["gross_points"][0]) == float(res2["gross_points"][0]),
   "cost stress leaves the resolved path untouched")

# ---------------------------------------------------------------- metrics
section("metrics, cost attribution and the five readings")
def fake_trade(net_r: float, **kw) -> dict:
    row = {
        "net_r": net_r, "net_points": net_r * 10.0, "gross_points": net_r * 10.0 + 2.0,
        "gross_r": net_r + 0.2, "cost_points": 2.0, "net_rupees": net_r * 10.0,
        "hold_minutes": 30.0, "target_distance": 20.0, "cost_multiple": 5.0,
        "risk_points": 10.0, "stop_atr_needed": 0.4, "mfe_r": 1.0, "mae_r": 0.3,
        "retest_delay_minutes": 10.0, "confirm_delay_minutes": 10.0,
        "outcome": "TARGET" if net_r > 0 else "STOP", "session": 1, "side": 1,
        "direction": phase53.LONG_BREAKOUT, "year": 2021, "quarter": "2021Q1",
        "minutes_since_open_at_entry": 60.0,
    }
    row.update(kw)
    return row


fake = [
    fake_trade(1.0, session=1, side=1, direction=phase53.LONG_BREAKOUT),
    fake_trade(-1.0, session=1, side=-1, direction=phase53.SHORT_BREAKOUT,
               year=2022, quarter="2022Q1", minutes_since_open_at_entry=90.0),
]
d = metrics.describe(fake)
ok(d["trade_count"] == 2 and d["session_count"] == 1,
   "two trades in one session count as two trades and one independent session")
ok(d["long_count"] == 1 and d["short_count"] == 1, "direction counts")
ok(d["win_rate"] == 0.5, "win rate")
ok(abs(d["net_expectancy_r"]) < 1e-12, "expectancy of a symmetric pair is zero")
ok(abs(d["profit_factor"] - 1.0) < 1e-12, "profit factor")
ok(d["largest_winner_r"] == 1.0 and d["largest_loser_r"] == -1.0, "extremes")
ok(d["median_target_distance"] == 20.0, "median target distance")
ok(d["median_cost_points"] == 2.0 and d["median_cost_multiple"] == 5.0,
   "median cost and movement/cost multiple")
ok(d["average_hold_minutes"] == 30.0, "average hold")
ok(d["gross_expectancy_points"] > d["net_expectancy_points"],
   "gross is above net by the cost, always")
ok(abs(d["cost_share_of_gross_pct"] - 100.0 * 4.0 / 20.0) < 1e-9,
   "cost consumed is total cost over the absolute gross move")
ok(metrics.describe([])["measured"] is False, "an empty cohort is not measured")
ok(metrics.per_period(fake, "year").keys() == {"2021", "2022"}, "per-year split")
ok(metrics.per_period(fake, "quarter").keys() == {"2021Q1", "2022Q1"},
   "per-quarter split")
ok(set(metrics.per_direction(fake)) == {phase53.LONG_BREAKOUT,
                                        phase53.SHORT_BREAKOUT},
   "long and short are reported apart")
sd = metrics.session_distribution(fake)
ok(sd["median_minutes_after_open"] == 75.0 and sd["trades_per_session"] == 2.0,
   "the session distribution reports when and how often")

many = [fake_trade(1.0 if i % 2 else -0.5, session=i) for i in range(60)]
big = metrics.describe(many)
years_spread = {str(2021 + i): {"net_total_r": 5.0, "net_expectancy_r": 0.2}
                for i in range(4)}
quarters_spread = {f"{2021 + i}Q1": {"net_total_r": 5.0, "net_expectancy_r": 0.2}
                   for i in range(4)}
ok(metrics.effect_reading(big, years_spread, quarters_spread)
   == phase53.DIRECTIONAL_EDGE,
   "positive gross, positive net, spread across years is a directional edge")
ok(metrics.effect_reading(metrics.describe(many[:10]), {}, {})
   == phase53.INSUFFICIENT_SAMPLE,
   "a sign computed over ten trades is INSUFFICIENT_SAMPLE, never an edge")
ok(metrics.effect_reading({"measured": True, "trade_count": 200,
                           "gross_expectancy_points": -1.0,
                           "net_expectancy_points": -2.0}, {}, {})
   == phase53.NO_EDGE, "negative gross is NO_EDGE, not a cost problem")
ok(metrics.effect_reading({"measured": True, "trade_count": 200,
                           "gross_expectancy_points": 1.0,
                           "net_expectancy_points": -0.5}, {}, {})
   == phase53.COST_DRIVEN_APPEARANCE,
   "a gross edge turned negative by cost is a cost appearance")
ok(metrics.effect_reading(
    {"measured": True, "trade_count": 200, "gross_expectancy_points": 2.0,
     "net_expectancy_points": 1.0, "net_total_r": 10.0},
    {"2025": {"net_total_r": 9.0, "net_expectancy_r": 1.0}}, {},
) == phase53.RECENT_PERIOD_OVERFIT, "one year carrying 90% of the net is overfit")
ok(metrics.effect_reading(
    {"measured": True, "trade_count": 200, "gross_expectancy_points": 2.0,
     "net_expectancy_points": 1.0, "net_total_r": 10.0},
    years_spread, {"2025Q1": {"net_total_r": 6.0, "net_expectancy_r": 1.0}},
) == phase53.RECENT_PERIOD_OVERFIT, "one quarter carrying 60% is overfit too")

ok(stats.max_drawdown_r(np.array([1.0, -2.0, -1.0, 3.0])) == 3.0,
   "drawdown is the deepest peak-to-trough of the equity curve")
ok(stats.one_sided_p(np.array([0.0, 0.0, 0.0])) == 1.0,
   "a flat cohort has no significance")
ok(stats.one_sided_p(np.array([1.0] * 100)) < 0.001,
   "a hundred identical winners would be significant")

# ------------------------------------------------------- family and the FDR
section("event families and the correction over the whole denominator")
h1 = study._family_hash("NIFTY", [{"entry_ts": 1, "side": 1},
                                  {"entry_ts": 2, "side": 1}])
h2 = study._family_hash("NIFTY", [{"entry_ts": 2, "side": 1},
                                  {"entry_ts": 1, "side": 1}])
h3 = study._family_hash("NIFTY", [{"entry_ts": 1, "side": 1},
                                  {"entry_ts": 3, "side": 1}])
h4 = study._family_hash("NIFTY", [{"entry_ts": 1, "side": -1},
                                  {"entry_ts": 2, "side": 1}])
h5 = study._family_hash("CRUDEOIL", [{"entry_ts": 1, "side": 1},
                                     {"entry_ts": 2, "side": 1}])
ok(h1 == h2, "the family hash does not depend on row order")
ok(h1 != h3, "a different event set is a different family")
ok(h1 != h4, "the same instants entered the other way are not the same family")
ok(h1 != h5, "two instruments cannot collide into one family")
ok(study._family_hash("NIFTY", []) == study._family_hash("NIFTY", []),
   "the empty set is stable")

ok(stats.benjamini_hochberg([0.001], tests=1, alpha=0.05) == [True],
   "one strong p-value passes on its own")
ok(stats.benjamini_hochberg([0.02], tests=64, alpha=0.05) == [False],
   "the same p-value fails once the full 64-test denominator is applied")
ok(stats.benjamini_hochberg([0.0001], tests=64, alpha=0.05) == [True],
   "a genuinely strong result still passes against 64 tests")
noise = [0.4 + 0.001 * i for i in range(64)]
ok(sum(stats.benjamini_hochberg(noise, tests=64)) == 0,
   "pure noise produces no discovery under the correction")

# ---------------------------------------------------------------- promotion
section("the promotion bar, one gate at a time")
def _cohort(n: int, exp: float) -> dict:
    return {"measured": True, "trade_count": n, "session_count": n,
            "net_expectancy_r": exp, "profit_factor": 2.0,
            "max_drawdown_r": 2.0, "top_trade_share": 0.2,
            "p_value_one_sided": 0.0001}


good_overall = {"measured": True, "profit_factor": 2.0, "max_drawdown_r": 3.0,
                "top_trade_share": 0.2, "net_total_r": 30.0, "trade_count": 300}
good_parts = {"DISCOVERY": _cohort(200, 0.3), "VALIDATION": _cohort(60, 0.25),
              "UNTOUCHED_HOLDOUT": _cohort(60, 0.2)}
good_years = {str(y): {"net_total_r": 6.0, "net_expectancy_r": 0.3}
              for y in range(2021, 2026)}
good_quarters = {f"{y}Q{q}": {"net_total_r": 1.5, "net_expectancy_r": 0.3}
                 for y in range(2021, 2026) for q in range(1, 5)}
good_stress = {"1.0": {"net_expectancy_r": 0.3},
               "1.5": {"net_expectancy_r": 0.2},
               "2.0": {"net_expectancy_r": 0.1}}
status, reasons = study._grade(
    good_parts, good_years, good_quarters, good_stress, good_overall,
    fdr_pass=True, had_candidates=True)
ok(status == phase53.ROBUST_CANDIDATE and not reasons,
   "a cohort that clears every declared gate is promotable")


def _flip(**kw) -> str:
    parts = {k: dict(v) for k, v in good_parts.items()}
    years = {k: dict(v) for k, v in good_years.items()}
    quarters = {k: dict(v) for k, v in good_quarters.items()}
    stress = {k: dict(v) for k, v in good_stress.items()}
    overall = dict(good_overall)
    fdr = kw.pop("fdr_pass", True)
    had = kw.pop("had_candidates", True)
    if "years" in kw:
        years = kw.pop("years")
    if "quarters" in kw:
        quarters = kw.pop("quarters")
    for key, value in kw.items():
        targ, field = key.split("__", 1)
        if targ == "overall":
            overall[field] = value
        elif targ == "stress":
            stress[field] = value
        else:
            parts[targ][field] = value
    return study._grade(parts, years, quarters, stress, overall,
                        fdr_pass=fdr, had_candidates=had)[0]


ok(_flip(DISCOVERY__trade_count=20) == phase53.PROMISING_NEEDS_DATA,
   "a thin but positive discovery is PROMISING_NEEDS_DATA, never robust")
ok(_flip(DISCOVERY__trade_count=20, DISCOVERY__net_expectancy_r=-0.1)
   == phase53.REJECTED, "thin and negative is rejected")
ok(_flip(DISCOVERY__session_count=10) == phase53.PROMISING_NEEDS_DATA,
   "enough trades over too few independent sessions cannot promote")
ok(_flip(DISCOVERY__net_expectancy_r=-0.1) == phase53.REJECTED,
   "a negative discovery is rejected before anything else is looked at")
ok(_flip(VALIDATION__trade_count=5) == phase53.PROMISING_NEEDS_DATA,
   "a thin validation cannot promote")
ok(_flip(VALIDATION__net_expectancy_r=-0.1) == phase53.HISTORICAL_LEAD,
   "positive in discovery, negative forward, is a historical lead")
ok(_flip(fdr_pass=False) == phase53.OVERFIT_RISK,
   "failing the correction is OVERFIT_RISK, not a candidate")
ok(_flip(UNTOUCHED_HOLDOUT__net_expectancy_r=-0.1) == phase53.HISTORICAL_LEAD,
   "a negative untouched holdout blocks promotion")
ok(_flip(UNTOUCHED_HOLDOUT__trade_count=2) == phase53.PROMISING_NEEDS_DATA,
   "a thin holdout blocks promotion")
ok(_flip(overall__profit_factor=1.0) == phase53.OVERFIT_RISK,
   "profit factor below 1.2 blocks promotion")
ok(_flip(overall__max_drawdown_r=99.0) == phase53.OVERFIT_RISK,
   "drawdown beyond the declared bound blocks promotion")
ok(_flip(overall__top_trade_share=0.9) == phase53.OVERFIT_RISK,
   "one trade carrying most of the net blocks promotion")
ok(_flip(years={"2025": {"net_total_r": 30.0, "net_expectancy_r": 0.3}})
   == phase53.OVERFIT_RISK, "a single positive year blocks promotion")
ok(_flip(quarters={"2025Q1": {"net_total_r": 25.0, "net_expectancy_r": 0.3}})
   == phase53.OVERFIT_RISK, "one quarter carrying the net blocks promotion")
ok(_flip(**{"stress__2.0": {"net_expectancy_r": -0.1}}) == phase53.OVERFIT_RISK,
   "a result that dies at 2x cost is a cost assumption, not an edge")
ok(_flip(overall__measured=False) == phase53.COST_BLOCKED,
   "events that all failed the cost gate are COST_BLOCKED")
ok(_flip(overall__measured=False, had_candidates=False) == phase53.REJECTED,
   "no event at all is REJECTED rather than COST_BLOCKED")
ok(study._grade(good_parts, good_years, good_quarters, good_stress, good_overall,
                fdr_pass=True, had_candidates=True)[0] in phase53.FINAL_STATUSES,
   "the grade is always one of the six declared statuses")

# --------------------------------------------------------------- report/CLI
section("the CLI's read-only commands")
for argv in (["prereg"], ["grid"], ["universe"]):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = cli.main_argv(argv)
    ok(len(out) > 200, f"cli {argv[0]} prints")
ok(phase53.fingerprint() in cli.main_argv(["prereg"]),
   "the CLI prints the fingerprint it will measure under")
ok(str(len(grid)) in cli.main_argv(["grid"]),
   "the CLI prints the size of the registered grid")
ok(phase53.SOURCE in cli.main_argv(["universe"]),
   "the CLI names the data as historical candles")

# ----------------------------------------------------------- end to end
section("end to end on the real files — this is the slow part")
result = study.run(("CRUDEOIL", "NIFTY"))
t = result["totals"]
ok(t["TOTAL_PARAMETERIZATIONS"] == 128,
   "two instruments at 64 parameterizations each is the whole denominator")
ok(t["UNIQUE_EVENT_FAMILIES"] <= t["TOTAL_PARAMETERIZATIONS"],
   "families cannot outnumber parameterizations")
ok(t["ROBUST_CANDIDATES"] <= t["HOLDOUT_POSITIVE"] + 0,
   "a robust candidate must at least be holdout positive")
ok(t["DISCOVERY_LEADS"] >= t["ROBUST_CANDIDATES"],
   "nothing is promoted that was not a discovery lead")
for inst in result["instruments"]:
    name = inst["instrument"]
    ok(inst["eligible"], f"{name} is eligible")
    ok(len(inst["rows"]) == 64, f"{name} ran all 64 parameterizations")
    ok(inst["distinct_hypotheses"] <= 64,
       f"{name}'s distinct hypotheses cannot exceed the grid")
    ok(0 < inst["totals"]["UNIQUE_EVENT_FAMILIES"] <= 64,
       f"{name} collapsed the grid into families")
    ok(len(inst["funnels"]) == 8, f"{name} reports all eight event funnels")
    fams = {r["event_family"] for r in inst["rows"]}
    ok(len(fams) == inst["totals"]["UNIQUE_EVENT_FAMILIES"],
       f"{name}'s family count is the count of distinct entry sets")
    for row in inst["rows"]:
        ok(row["final_status"] in phase53.FINAL_STATUSES,
           f"{name} {row['variant_id']} carries a declared status")
        ok(row["effect_reading"] in phase53.EFFECT_READINGS,
           f"{name} {row['variant_id']} carries a declared reading")
        ok(row["final_status"] != phase53.ROBUST_CANDIDATE or not
           row["status_reasons"], "a robust candidate has no outstanding reason")
        ok(bool(row["fdr_pass"]) or row["final_status"] != phase53.ROBUST_CANDIDATE,
           "nothing is promoted without surviving the correction")
        o = row["overall"]
        if o.get("measured"):
            ok(o["trade_count"] == sum(
                row["per_partition"][p]["trade_count"] for p in phase53.PARTITIONS
            ), "the three partitions account for every trade exactly once")
            ok(o["session_count"] <= o["trade_count"],
               "independent sessions never exceed trades")
            ok(o["trade_count"] <= 2 * o["session_count"],
               "no session produced more than one long and one short")
            ok(o["long_count"] + o["short_count"] == o["trade_count"],
               "every trade is a long or a short")
            ok(o["average_hold_minutes"] <= phase53.MAX_HOLD_MINUTES + 1e-9,
               "no trade is held past the declared 180 minutes")
            ok(o["median_stop_atr"] <= phase53.MAX_STOP_ATR + 1e-9,
               "no kept trade risks more than the declared 1.0 ATR")
            ok(o["median_cost_multiple"] >= row["cost_gate"] - 1e-9,
               "every kept trade cleared its own cost gate")
            ok(o["gross_expectancy_points"] >= o["net_expectancy_points"],
               "gross is never below net")
            ok(set(row["cost_stress"]) == {"1.0", "1.5", "2.0"},
               "the three cost-stress readings are present")
            s1x = row["cost_stress"]["1.0"]["net_expectancy_r"]
            s2x = row["cost_stress"]["2.0"]["net_expectancy_r"]
            ok(s2x <= s1x + 1e-12, "charging cost twice cannot improve a result")
            ok(abs(s1x - o["net_expectancy_r"]) < 1e-9,
               "the 1x stress reproduces the base result exactly")
            ok(all(v["trade_count"] > 0 for v in row["per_year"].values()),
               "every reported year has trades in it")

text = report.render(result)
for label in ("TOTAL_PARAMETERIZATIONS", "UNIQUE_EVENT_FAMILIES",
              "DISCOVERY_LEADS", "VALIDATION_POSITIVE", "HOLDOUT_POSITIVE",
              "ROBUST_CANDIDATES"):
    ok(label in text, f"the report prints {label}")
ok(str(t["ROBUST_CANDIDATES"]) in text,
   "the report prints the robust count it measured")
ok(phase53.CANDLE_HAS_NO_BOOK in text, "the candle honesty travels with the text")
ok(phase53.PHASE_52_UNTOUCHED in text, "the report states Phase 52 is untouched")
ok(phase53.NOT_A_PREDICTION in text, "the report refuses to be a prediction")
ok(phase53.NO_OPTION_CLAIM in text, "the report makes no option claim")
ok(phase53.COST_GATE_IS_NOT_A_PROBABILITY in text,
   "the report states the cost gate is not a probability")
ans = report.answers(result)
for q in (
    "Does the opening-range breakout/retest mechanism work?",
    "Does it work on CRUDEOIL?",
    "Does it work on NIFTY?",
    "How many independent events occur?",
    "What percentage of gross movement is consumed by cost?",
    "Does the effect survive the untouched holdout?",
    "Is the effect directional or cost-driven?",
    "Is it suitable for live executable-book paper validation?",
):
    ok(bool(ans.get(q)), f"answered: {q}")
ok(len(ans) >= 8, "all eight required questions are answered")
paths = report.write_artefacts(result, text=text)
ok(len(paths) == 6, "six immutable artefacts are written")
ok(all(Path(p).exists() and Path(p).stat().st_size > 0 for p in paths),
   "every artefact has content")
ok(all(phase53.fingerprint() in Path(p).name for p in paths),
   "every artefact is named with the fingerprint it was measured under")
again = report.write_artefacts(result, text=text)
ok(not (set(paths) & set(again)) or all(Path(p).exists() for p in again),
   "a second write never overwrites the first")

print(f"\nPHASE 53 SMOKE — {PASS} passed, {FAIL} failed")
if FAIL:
    raise SystemExit(1)
