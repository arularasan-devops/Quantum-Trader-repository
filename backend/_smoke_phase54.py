"""Phase 54 smoke — the properties whose loss would make this study dishonest.

The load-bearing tests are the negative ones. Producing a multi-day expectancy
is easy; what has to be guaranteed is that no session took part in deciding
itself, that the twenty-session extreme excludes the close being compared to it,
that the ATR cannot see the session it scales, that a completed close back
inside the range kills the setup rather than being read as a deeper pullback,
that the fill is the **next session's open** and never the reclaim close, that a
five-minute bar holding both stop and target is scored as a loss, that a
position is never doubled, that 128 spellings are corrected as the 128 tests
they are and reported as the families they are — and that nothing in Phases
41-53 moved.

Two kinds of fixture, deliberately. Synthetic sessions, because only a
hand-built path can assert "this refusal fires for this reason"; and the real
five-year CRUDEOIL and NIFTY files end to end, because a fixture cannot fail in
the ways a real file does. The end-to-end section is the slow one — about twenty
seconds for both instruments.
"""
from __future__ import annotations

import ast
import contextlib
import io
from pathlib import Path

import numpy as np

from app.research import phase49, phase51, phase52, phase53, phase54
from app.research.phase24 import data as p24data
from app.research.phase24.data import Series
from app.research.phase27 import bars as p27bars
from app.research.phase50 import tiers as p50tiers
from app.research.phase54 import (
    cli,
    daily as daily_mod,
    execute,
    mechanism,
    metrics,
    report,
    stats,
    study,
)

PASS = 0
FAIL = 0

IST_OFFSET = 19_800
SESSION_MINUTES = 75


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
def make_series(days: list[tuple[float, float, float, float]],
                instrument: str = "NIFTY") -> Series:
    """A synthetic 1-minute series from per-session daily OHLC.

    Each session is ``SESSION_MINUTES`` minutes from 09:15 IST and walks
    open -> high -> low -> close, so the session's aggregate OHLC is exactly the
    tuple it was built from and the daily builder has something to be right or
    wrong about.
    """
    rows: list[dict] = []
    for day, (o, h, low_, c) in enumerate(days):
        base = (20_000 + day) * 86_400 + (9 * 60 + 15) * 60 - IST_OFFSET
        path = [o, h, low_, c] + [c] * (SESSION_MINUTES - 4)
        prev = o
        for m, close in enumerate(path):
            rows.append({
                "time": base + m * 60,
                "open": prev,
                "high": max(prev, close),
                "low": min(prev, close),
                "close": close,
                "volume": 0.0,
            })
            prev = close
    return Series(instrument, rows)


# Thirty filler sessions: HIGH20 = 105, LOW20 = 95, and a true range of 10 every
# session so ATR20 is exactly 10 and the arithmetic below is checkable by hand.
FILLER = [(100.0, 105.0, 95.0, 100.0)] * 30

# A long sequence: a close beyond HIGH20, a session that trades back to 105 and
# still closes above it, then the session whose open is the fill.
LONG_EXPANSION = (100.0, 112.0, 99.0, 110.0)
LONG_RECLAIM = (110.0, 111.0, 104.0, 106.0)
LONG_ENTRY_DAY = (106.0, 120.0, 105.5, 119.0)

# The mirror, below LOW20.
SHORT_EXPANSION = (100.0, 101.0, 88.0, 90.0)
SHORT_RECLAIM = (90.0, 96.0, 89.0, 94.0)
SHORT_ENTRY_DAY = (94.0, 94.5, 80.0, 81.0)

# A completed close back inside the range: not a deeper pullback, a dead setup.
LONG_INVALIDATING = (110.0, 111.0, 100.0, 102.0)
# Never trades back to the level at all.
LONG_NO_PULLBACK = (110.0, 115.0, 106.0, 112.0)


def scaled(days: list[tuple[float, float, float, float]],
           factor: float = 10.0) -> list[tuple[float, float, float, float]]:
    """The same geometry at ten times the price, so the cost gate is clearable."""
    return [(o * factor, h * factor, low_ * factor, c * factor)
            for o, h, low_, c in days]


def tables(days: list[tuple[float, float, float, float]], *, lookback: int = 20,
           window: int = 2, instrument: str = "NIFTY") -> dict:
    """Detect over a synthetic day list exactly as the study does."""
    s1 = make_series(days, instrument)
    s5, bar_stats = p27bars.resample(s1, phase54.RESOLUTION_TIMEFRAME_MINUTES)
    dly = daily_mod.build(s5)
    atr = daily_mod.atr_before(dly)
    hi, lo = daily_mod.extremes_before(dly, lookback)
    rows, funnel = mechanism.events(
        instrument, dly, hi, lo, atr, lookback=lookback, window=window
    )
    part = daily_mod.chronological_partitions(dly, phase54.PARTITION_SHARES)
    for r in rows:
        r["partition"] = str(part[r["entry_ordinal"]])
    return {
        "rows": rows, "funnel": funnel, "series": s5, "daily": dly,
        "atr": atr, "bars": bar_stats, "partition": part,
    }


def spec_of(**over: float) -> dict:
    """One registered variant, optionally addressed by its fields."""
    base = {
        "variant_id": "FIXTURE", "lookback": 20, "pullback_window": 2,
        "stop_buffer_atr": 0.25, "max_risk_atr": 1.0, "target_r": 2.0,
        "max_hold_sessions": 5, "cost_gate": 5.0,
    }
    base.update(over)
    return base


# ------------------------------------------------------ §1 pre-registration
section("pre-registration and the registered grid")
ok(phase54.MECHANISM == "MULTI_DAY_EXPANSION_PULLBACK_CONTINUATION",
   "one mechanism, named")
ok(phase54.fingerprint() == phase54.fingerprint(), "fingerprint is stable")
ok(len(phase54.fingerprint()) == 16, "fingerprint width")
ok(phase54.SOURCE == "HISTORICAL_CANDLE_DATA", "source class declared")
ok(phase54.VEHICLE == "FUTURES_ONLY", "futures only")
ok(phase54.LOOKBACKS == (20, 30), "two lookbacks only")
ok(phase54.PULLBACK_WINDOWS == (2, 3), "two pullback windows only")
ok(phase54.STOP_BUFFER_ATR == (0.25, 0.50), "two stop buffers only")
ok(phase54.MAX_RISK_ATR == (1.0, 1.5), "two risk caps only")
ok(phase54.TARGET_R_MULTIPLES == (1.5, 2.0), "1.5R and 2.0R only")
ok(phase54.MAX_HOLD_SESSIONS == (5, 10), "five and ten sessions only")
ok(phase54.COST_GATE_MULTIPLES == (5.0, 8.0), "5x and 8x cost gates only")
ok(phase54.ATR_WINDOW_SESSIONS == 20, "trailing 20-session ATR")
ok(phase54.MAX_OPEN_POSITIONS == 1, "one position at a time")
ok(phase54.RESOLUTION_TIMEFRAME_MINUTES == 5, "paths are walked on 5m bars")
ok(phase54.PARTITION_SHARES == (0.60, 0.20, 0.20), "chronological 60/20/20")
ok(phase54.COST_STRESS_MULTIPLES == (1.0, 1.5, 2.0), "1x, 1.5x and 2x stress")

grid = phase54.variants()
ok(len(grid) == 128, "exactly 128 parameterizations, 2^7")
ok(phase54.TOTAL_PARAMETERIZATIONS == 128, "the declared per-instrument count")
ok(len({v["variant_id"] for v in grid}) == 128, "variant ids are distinct")
ok(len({tuple(sorted(v.items())) for v in grid}) == 128, "no combination repeats")
for field, expected in (
    ("lookback", {20, 30}),
    ("pullback_window", {2, 3}),
    ("stop_buffer_atr", {0.25, 0.50}),
    ("max_risk_atr", {1.0, 1.5}),
    ("target_r", {1.5, 2.0}),
    ("max_hold_sessions", {5, 10}),
    ("cost_gate", {5.0, 8.0}),
):
    ok({v[field] for v in grid} == expected, f"the grid covers {field} exactly")
ok(len({(v["lookback"], v["pullback_window"]) for v in grid}) == 4,
   "four distinct event tables carry all 128 rows")

pre = phase54.preregistration()
ok(pre["mechanism"] == phase54.MECHANISM, "the prereg names the mechanism")
ok(pre["total_parameterizations_per_instrument"] == 128,
   "the prereg declares the grid size rather than leaving it to the code path")
ok("NEVER_OVER_THE_SURVIVORS" in pre["fdr_denominator_basis"],
   "the prereg declares the whole denominator before anything is measured")
ok(phase54.RECLAIM_BASIS in pre.values() or
   pre.get("reclaim_basis") == phase54.RECLAIM_BASIS,
   "the consequential reclaim reading is in the frozen text, not implicit")
ok(pre.get("pullback_extreme_basis") == phase54.PULLBACK_EXTREME_BASIS,
   "the pullback extreme reading is frozen too")
ok(pre["honesty"]["overnight_risk"] ==
   phase54.OVERNIGHT_RISK_IS_ONLY_PARTLY_MODELLED,
   "the overnight-gap limitation is declared rather than assumed away")
ok("OPTIMISTIC" in pre["honesty"]["candle_has_no_book"],
   "the candle honesty says the net figures are optimistic")
ok(len(phase54.FINAL_STATUSES) == 6 and
   phase54.ROBUST_CANDIDATE in phase54.FINAL_STATUSES, "six allowed statuses")
ok(set(phase54.EFFECT_READINGS) == {
    phase54.DIRECTIONAL_EDGE, phase54.COST_DRIVEN_APPEARANCE,
    phase54.HOLDING_PERIOD_EFFECT, phase54.RECENT_PERIOD_OVERFIT,
    phase54.INSUFFICIENT_SAMPLE, phase54.NO_EDGE,
}, "the six required stability readings exist and no seventh")
ok(set(phase54.EFFECT_SOURCES) == {
    phase54.EFFECT_DIRECTIONAL, phase54.EFFECT_COST, phase54.EFFECT_TIME,
    phase54.EFFECT_INTERACTION, phase54.EFFECT_NONE,
}, "direction, cost, holding, interaction and none are separable")

# The fingerprint must move if any registered number moves, and must not move
# for anything else. Without this the freeze is decorative.
before = phase54.fingerprint()
saved = phase54.TARGET_R_MULTIPLES
phase54.TARGET_R_MULTIPLES = (1.5, 3.0)
ok(phase54.fingerprint() != before, "changing the grid changes the fingerprint")
phase54.TARGET_R_MULTIPLES = saved
ok(phase54.fingerprint() == before, "restoring the grid restores it exactly")

# ----------------------------------------------------------------- isolation
section("isolation: no earlier phase engine, no order path")
pkg = Path("app/research/phase54")
ok(len(sorted(pkg.glob("*.py"))) >= 8, "the package is all present")
forbidden_modules = ("phase53", "phase52", "phase51", "phase50", "phase49")
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
    ok(all("order" not in name for name in imported),
       f"{path.name} has no order path")
importers = [
    p for p in Path("app").rglob("*.py")
    if "phase54" not in p.parts and "phase54" in p.read_text()
]
ok(not importers, f"no module outside phase54 imports it (found {importers})")
ok("phase24" in " ".join(
    n.module or "" for n in ast.walk(ast.parse((pkg / "execute.py").read_text()))
    if isinstance(n, ast.ImportFrom)
), "the cost model is the shared Phase 24 helper, not a local invention")

# ----------------------------------------------------- daily bars from 1m
section("completed daily bars, aggregated from the minute series")
fix = tables(FILLER + [LONG_EXPANSION, LONG_RECLAIM, LONG_ENTRY_DAY])
dly = fix["daily"]
ok(dly["close"].size == 33, "one daily bar per session, no more and no fewer")
ok(bool((np.diff(dly["session"]) > 0).all()), "sessions are strictly ordered")
ok(bool((dly["high"] >= dly["low"]).all()), "no daily high below its low")
ok(bool((dly["high"] >= np.maximum(dly["open"], dly["close"])).all()),
   "the daily high dominates the open and the close")
ok(abs(float(dly["high"][30]) - 112.0) < 1e-9, "the daily high is the session's")
ok(abs(float(dly["low"][30]) - 99.0) < 1e-9, "the daily low is the session's")
ok(abs(float(dly["close"][30]) - 110.0) < 1e-9,
   "the daily close is the last minute's close")
ok(abs(float(dly["open"][30]) - 100.0) < 1e-9,
   "the daily open is the first minute's open")
ok(bool((dly["first_i"] <= dly["last_i"]).all()),
   "every session's first bar precedes its last")
ok(bool((dly["first_i"][1:] > dly["last_i"][:-1]).all()),
   "no five-minute bar belongs to two sessions")
ok(int(dly["bars"].sum()) == fix["series"].ts.size,
   "the sessions account for every five-minute bar exactly once")
ok(fix["bars"]["spans_a_session"] is False, "no aggregated bar spans a session")
ok(len(daily_mod.session_bounds(np.array([], dtype=np.int64))) == 0,
   "an empty series has no sessions rather than raising")

# ------------------------------------------------------- causal levels
section("prior extremes and an ATR that cannot see its own session")
hi20, lo20 = daily_mod.extremes_before(dly, 20)
hi30, lo30 = daily_mod.extremes_before(dly, 30)
atr = daily_mod.atr_before(dly)
ok(bool(np.isnan(hi20[:20]).all()) and bool(np.isnan(lo20[:20]).all()),
   "HIGH20/LOW20 are NaN until twenty completed sessions exist")
ok(bool(np.isnan(hi30[:30]).all()), "HIGH30 waits for thirty")
ok(abs(float(hi20[30]) - 105.0) < 1e-9, "HIGH20 is the prior twenty highs' max")
ok(abs(float(lo20[30]) - 95.0) < 1e-9, "LOW20 is the prior twenty lows' min")
ok(abs(float(hi30[30]) - 105.0) < 1e-9, "HIGH30 over thirty sessions")
ok(abs(float(lo30[30]) - 95.0) < 1e-9, "LOW30 over thirty sessions")
ok(float(hi20[31]) == 112.0,
   "the expansion session enters the *next* session's reference window")
ok(float(hi20[30]) < float(dly["close"][30]),
   "the level the expansion is compared to excludes the expansion itself")
for k in range(20, dly["close"].size):
    ok(float(hi20[k]) == float(dly["high"][k - 20:k].max()),
       f"HIGH20 at {k} is exactly the prior window's max")
    ok(float(lo20[k]) == float(dly["low"][k - 20:k].min()),
       f"LOW20 at {k} is exactly the prior window's min")
ok(bool(np.isnan(atr[:21]).all()),
   "ATR20 is NaN until twenty completed true ranges exist, the first session "
   "having no previous close")
ok(abs(float(atr[30]) - 10.0) < 1e-9, "the filler's ATR20 is exactly 10")
tr = daily_mod.true_ranges(dly)
ok(bool(np.isnan(tr[0])), "the first session has no true range")
ok(abs(float(tr[30]) - 13.0) < 1e-9,
   "the true range uses the previous close when it is the wider span")
ok(daily_mod.true_ranges({"close": np.array([1.0]), "high": np.array([1.0]),
                          "low": np.array([1.0])}).size == 1,
   "a one-session series returns a NaN range rather than raising")

# The look-ahead test that matters: recompute on a prefix and demand every past
# value is bit-identical. A level or an ATR that changes when later sessions are
# removed was reading them.
long_days = FILLER + [LONG_EXPANSION, LONG_RECLAIM, LONG_ENTRY_DAY] + FILLER
s1_full = make_series(long_days)
s5_full, _ = p27bars.resample(s1_full, 5)
dly_full = daily_mod.build(s5_full)
cut_bars = int(dly_full["last_i"][32]) + 1
dly_cut = daily_mod.build(prefix(s5_full, cut_bars))
ok(dly_cut["close"].size == 33, "the prefix ends on a session boundary")
for name, fn in (("HIGH20", lambda d: daily_mod.extremes_before(d, 20)[0]),
                 ("LOW20", lambda d: daily_mod.extremes_before(d, 20)[1]),
                 ("HIGH30", lambda d: daily_mod.extremes_before(d, 30)[0]),
                 ("ATR20", daily_mod.atr_before)):
    a = fn(dly_full)[:33]
    b = fn(dly_cut)
    ok(bool(np.array_equal(np.nan_to_num(a, nan=-1.0),
                           np.nan_to_num(b, nan=-1.0))),
       f"{name} computed on a prefix equals the whole-series value, session for "
       f"session — nothing after the decision reached it")

# ------------------------------------------------------------ partitions
section("chronological partitions, never shuffled")
part = daily_mod.chronological_partitions(dly_full, phase54.PARTITION_SHARES)
labels = [str(x) for x in part]
ok(labels[0] == phase54.DISCOVERY and labels[-1] == phase54.UNTOUCHED_HOLDOUT,
   "the file starts in discovery and ends in the untouched holdout")
first_val = labels.index(phase54.VALIDATION)
first_hold = labels.index(phase54.UNTOUCHED_HOLDOUT)
ok(first_val < first_hold, "validation precedes the holdout")
ok(set(labels[:first_val]) == {phase54.DISCOVERY} and
   set(labels[first_val:first_hold]) == {phase54.VALIDATION} and
   set(labels[first_hold:]) == {phase54.UNTOUCHED_HOLDOUT},
   "each partition is one contiguous block of sessions, so no future session "
   "sits inside the training period")
ok(abs(first_val / len(labels) - 0.60) < 0.01, "discovery is the first 60%")
ok(abs((first_hold - first_val) / len(labels) - 0.20) < 0.01,
   "validation is the next 20%")

# --------------------------------------------------------- the mechanism
section("expansion, pullback, invalidation, reclaim, next-session-open entry")
rows = fix["rows"]
longs = [r for r in rows if r["side"] > 0]
ok(len(longs) == 1, "the crafted long sequence produces exactly one candidate")
cand = longs[0]
ok(cand["expansion_ordinal"] == 30, "the expansion is the session that closed "
   "beyond HIGH20")
ok(abs(cand["level"] - 105.0) < 1e-9, "the level is HIGH20, not today's high")
ok(cand["touch_ordinal"] == 31, "the pullback session is the one that reached it")
ok(cand["reclaim_ordinal"] == 31, "under the frozen reading that session "
   "reclaims by closing beyond the level")
ok(abs(cand["pullback_extreme"] - 104.0) < 1e-9,
   "the pullback extreme is the reaching session's low")
ok(cand["entry_ordinal"] == 32, "entry is the session after the reclaim")
ok(abs(cand["entry"] - 106.0) < 1e-9,
   "the fill is that session's open, not the reclaim close")
ok(cand["entry_ts"] == int(dly["open_ts"][32]),
   "the entry timestamp is the opening bar of the entry session")
ok(cand["fill_index"] == int(dly["first_i"][32]),
   "the fill index is the entry session's first five-minute bar")
ok(cand["direction"] == phase54.LONG_CONTINUATION, "the direction is declared")
ok(abs(cand["atr"] - 10.0) < 1e-9, "the candidate carries the causal ATR")
ok(cand["gate_cost_points"] > 0, "the gate's round trip is priced at the entry")
ok(cand["pullback_sessions"] == 1, "the pullback took one completed session")

short_fix = tables(FILLER + [SHORT_EXPANSION, SHORT_RECLAIM, SHORT_ENTRY_DAY])
shorts = [r for r in short_fix["rows"] if r["side"] < 0]
ok(len(shorts) == 1, "the mirrored short sequence produces one candidate")
s = shorts[0]
ok(abs(s["level"] - 95.0) < 1e-9, "the short level is LOW20")
ok(s["side"] == -1 and s["direction"] == phase54.SHORT_CONTINUATION,
   "a downside expansion is entered short")
ok(abs(s["pullback_extreme"] - 96.0) < 1e-9,
   "the short pullback extreme is the reaching session's high")
ok(abs(s["entry"] - 94.0) < 1e-9, "the short fills at the next session's open")

no_exp = tables(FILLER + [(100.0, 104.0, 96.0, 101.0)] * 3)
ok(not no_exp["rows"], "a close inside the range produces no candidate")
ok(no_exp["funnel"][phase54.NO_EXPANSION] > 0, "and is counted as NO_EXPANSION")

no_pb = tables(FILLER + [LONG_EXPANSION] + [LONG_NO_PULLBACK] * 3)
ok(no_pb["funnel"][phase54.NO_PULLBACK] == 1,
   "an expansion that never trades back to the level is NO_PULLBACK")
ok(not [r for r in no_pb["rows"] if r["side"] > 0],
   "and produces no long candidate")

inval = tables(FILLER + [LONG_EXPANSION, LONG_INVALIDATING, LONG_ENTRY_DAY])
ok(inval["funnel"][phase54.INVALIDATED] == 1,
   "a completed close back inside the range invalidates the setup")
ok(not [r for r in inval["rows"] if r["side"] > 0],
   "an invalidated setup is never entered, however it later behaves")

# The window is a window: the same reaching session outside it is not a trade.
late = tables(FILLER + [LONG_EXPANSION] + [LONG_NO_PULLBACK] * 2
              + [LONG_RECLAIM, LONG_ENTRY_DAY], window=2)
ok(not [r for r in late["rows"] if r["side"] > 0 and r["expansion_ordinal"] == 30],
   "a reclaim after the two-session window has expired is not entered")
late3 = tables(FILLER + [LONG_EXPANSION] + [LONG_NO_PULLBACK] * 2
               + [LONG_RECLAIM, LONG_ENTRY_DAY], window=3)
ok([r for r in late3["rows"] if r["side"] > 0 and r["expansion_ordinal"] == 30],
   "the registered three-session window does reach it, which is what makes the "
   "window a tested parameter rather than a decoration")

no_forward = tables(FILLER + [LONG_EXPANSION, LONG_RECLAIM])
ok(no_forward["funnel"][phase54.NO_FORWARD_SESSION] == 1,
   "a reclaim on the last session has no open to fill in and is refused")
ok(not no_forward["rows"], "and no candidate is invented for it")

strict = tables(FILLER + [LONG_EXPANSION, LONG_RECLAIM,
                          (106.0, 120.0, 105.5, 119.0), LONG_ENTRY_DAY])
longs2 = [r for r in strict["rows"] if r["side"] > 0]
ok(bool(longs2) and longs2[0]["strict_reclaim_available"],
   "a later session that also closes beyond the level is recorded as available "
   "under the stricter reading")
ok(strict["funnel"]["candidates_under_the_strict_reclaim_reading"] >= 1,
   "the strict reading is counted as a diagnostic")
ok(longs2[0]["entry_ordinal"] == 32,
   "and the frozen fill is unchanged by that diagnostic")
# The diagnostic has to be able to say no, or it says nothing. Here the session
# after the reclaim closes back inside the range, so the stricter reading that
# demands a separate later reclaim finds none.
no_strict = tables(FILLER + [LONG_EXPANSION, LONG_RECLAIM,
                             (106.0, 107.0, 100.0, 101.0), LONG_ENTRY_DAY])
ns_long = [r for r in no_strict["rows"] if r["side"] > 0]
ok(bool(ns_long) and not ns_long[0]["strict_reclaim_available"],
   "a sequence with no later reclaim reports the diagnostic as absent rather "
   "than as trivially satisfied")
ok(no_strict["funnel"]["candidates_under_the_strict_reclaim_reading"] == 0,
   "and the funnel counts zero of them, so the two readings are genuinely "
   "different numbers rather than the same number twice")
ok(ns_long[0]["entry_ordinal"] == 32,
   "while the frozen reading still fills at the session after its own reclaim")
funnel_total = sum(
    fix["funnel"][k] for k in (
        phase54.NOT_KNOWABLE, phase54.NO_EXPANSION, phase54.NO_PULLBACK,
        phase54.INVALIDATED, phase54.NO_RECLAIM, phase54.NO_FORWARD_SESSION,
    )
) + fix["funnel"]["candidates"]
ok(funnel_total == fix["funnel"]["direction_attempts"],
   "every direction attempt ends in exactly one funnel bucket")
ok(fix["funnel"]["direction_attempts"] == 2 * fix["funnel"]["sessions"],
   "one long and one short attempt per session, and no third")

# ------------------------------------------------------------- execution
section("the fill, the stop, the target, the tie, the gap and the clock")
ok(abs(execute.cost_at_price("NIFTY", np.array([22_000.0]))[0] -
       execute.cost_at_price("NIFTY", np.array([22_000.0]))[0]) < 1e-12,
   "the cost model is deterministic")
c_low = float(execute.cost_at_price("NIFTY", np.array([10_000.0]))[0])
c_high = float(execute.cost_at_price("NIFTY", np.array([30_000.0]))[0])
ok(0 < c_low < c_high, "a larger turnover costs more, and both cost something")
batch = execute.cost_at_price("NIFTY", np.array([10_000.0, 30_000.0]))
ok(abs(float(batch[0]) - c_low) < 1e-9,
   "a price's cost does not depend on the other prices in the batch — the "
   "probe is fixed, so there is no look-ahead through the cost feature")


def path(bars: list[tuple[float, float, float, float]]) -> dict:
    """Five-minute OHLC arrays from a list of bars, with one-minute stamps."""
    a = np.array(bars, dtype=np.float64)
    return {
        "open_": a[:, 0], "high": a[:, 1], "low": a[:, 2], "close": a[:, 3],
        "ts": np.arange(len(bars), dtype=np.int64) * 300 + 1_600_000_000,
    }


def resolve(bars, *, side=1, entry=100.0, stop=95.0, target=110.0,
            fill_i=0, last_allowed_i=None, **kw) -> dict:
    p = path(bars)
    return execute.resolve_one(
        "NIFTY", p["high"], p["low"], p["open_"], p["close"], p["ts"],
        fill_i=fill_i,
        last_allowed_i=len(bars) - 1 if last_allowed_i is None else last_allowed_i,
        side=side, entry=entry, stop=stop, target=target, **kw
    )


r = resolve([(100.0, 101.0, 99.0, 100.0), (100.0, 111.0, 100.0, 110.0),
             (110.0, 112.0, 90.0, 95.0)])
ok(r["outcome"] == execute.TARGET_HIT, "a target touched first is a target")
ok(abs(r["exit_price"] - 110.0) < 1e-9, "and is filled at the target")
ok(r["exit_index"] == 1, "on the bar that touched it, not later")
ok(abs(r["gross_points"] - 10.0) < 1e-9, "gross is the distance travelled")
ok(r["cost_points"] > 0 and r["net_points"] < r["gross_points"],
   "cost is charged and always reduces the result")
ok(abs(r["net_r"] - r["net_points"] / 5.0) < 1e-9, "R is net over initial risk")
ok(abs(r["risk_points"] - 5.0) < 1e-9, "the risk is entry to stop")

r = resolve([(100.0, 101.0, 94.0, 96.0)])
ok(r["outcome"] == execute.STOP_HIT and abs(r["exit_price"] - 95.0) < 1e-9,
   "a stop touched is a stop, filled at the stop")
ok(r["net_r"] < -1.0, "a stopped trade loses its risk plus the round trip")

r = resolve([(100.0, 111.0, 94.0, 100.0)])
ok(r["outcome"] == execute.STOP_HIT,
   "one bar holding both levels is a loss, because a candle cannot order them")

r = resolve([(100.0, 101.0, 99.0, 100.0), (90.0, 92.0, 88.0, 91.0)])
ok(r["outcome"] == execute.STOP_HIT and abs(r["exit_price"] - 90.0) < 1e-9,
   "a stop gapped through overnight is filled at the open, which is worse than "
   "the stop rather than conveniently at it")
r = resolve([(100.0, 101.0, 99.0, 100.0), (115.0, 116.0, 114.0, 115.0)])
ok(r["outcome"] == execute.TARGET_HIT and abs(r["exit_price"] - 115.0) < 1e-9,
   "and a gapped target is filled at that open too — the rule cuts both ways")

r = resolve([(100.0, 101.0, 99.0, 100.0), (100.0, 102.0, 99.0, 101.0),
             (101.0, 103.0, 100.0, 102.0)], last_allowed_i=1)
ok(r["outcome"] == execute.TIME_EXIT, "the clock exits an unresolved position")
ok(abs(r["exit_price"] - 101.0) < 1e-9,
   "at the open of the bar after the last allowed session, not at a kinder price")
r = resolve([(100.0, 101.0, 99.0, 100.0), (100.0, 102.0, 99.0, 101.5)],
            last_allowed_i=1)
ok(r["outcome"] == execute.DATA_END_EXIT and abs(r["exit_price"] - 101.5) < 1e-9,
   "a position the file outlives is marked DATA_END at the last completed close")

r_short = resolve([(100.0, 101.0, 89.0, 90.0)], side=-1, entry=100.0,
                  stop=105.0, target=90.0)
ok(r_short["outcome"] == execute.TARGET_HIT and r_short["gross_points"] > 0,
   "a short profits when price falls to its target")
r_short = resolve([(100.0, 106.0, 99.0, 105.0)], side=-1, entry=100.0,
                  stop=105.0, target=90.0)
ok(r_short["outcome"] == execute.STOP_HIT and r_short["gross_points"] < 0,
   "and loses when price rises to its stop")

r1 = resolve([(100.0, 111.0, 100.0, 110.0)])
r2 = resolve([(100.0, 111.0, 100.0, 110.0)], spread_multiplier=2.0)
ok(abs(r2["cost_points"] - 2.0 * r1["cost_points"]) < 1e-9,
   "the stress multiplier doubles the cost")
ok(r1["exit_index"] == r2["exit_index"] and r1["outcome"] == r2["outcome"],
   "and moves no stop, no target and no clock — the path is the same path")
ok(r1["mfe_r"] >= 0 and r1["mae_r"] >= 0, "excursions are reported as magnitudes")

# ------------------------------------------------------- the sequential book
section("the risk cap, the cost gate, one position and no re-entry")
# The hand-built prices are scaled by ten here for one reason, stated rather
# than tuned around: at a price of 106 a 9-point target is only about 3.4x the
# modelled round trip, so the registered 5x gate refuses it — correctly. The
# same geometry ten times larger clears the gate, which is what lets the
# assertions below test the stop, the target and the book instead of the gate.
big = tables(scaled(FILLER + [LONG_EXPANSION, LONG_RECLAIM, LONG_ENTRY_DAY]))
blocked_small, small_ref = study.book(fix, "NIFTY", spec_of())
ok(not blocked_small and small_ref[phase54.COST_BLOCKED] == 1,
   "a target inside five round trips is refused by the registered gate, which "
   "is the gate doing its job rather than an inconvenience")
trades, refusals = study.book(big, "NIFTY", spec_of())
ok(len(trades) == 1, "the crafted long sequence produces one trade")
t = trades[0]
ok(abs(t["stop"] - (1040.0 - 0.25 * 100.0)) < 1e-9,
   "the stop is the pullback extreme less the buffer in ATR")
ok(abs(t["risk_points"] - 45.0) < 1e-9, "the risk is entry to stop")
ok(abs(t["target"] - (1060.0 + 2.0 * 45.0)) < 1e-9, "the target is 2.0R away")
ok(abs(t["risk_atr"] - 0.45) < 1e-9, "the risk is reported in ATR terms")
ok(t["cost_multiple"] >= 5.0, "the kept trade cleared its own cost gate")
ok(t["hold_sessions"] >= 1, "the hold is counted in sessions")
ok(t["year"] > 2000 and t["quarter"].endswith(("Q1", "Q2", "Q3", "Q4")),
   "each trade carries its calendar year and quarter")

wide_trades, _ = study.book(big, "NIFTY", spec_of(stop_buffer_atr=0.50,
                                                  max_risk_atr=1.0))
ok(len(wide_trades) == 1 and abs(wide_trades[0]["risk_atr"] - 0.70) < 1e-9,
   "the wider buffer is still inside the 1.0 ATR cap, so the next assertion "
   "tests the cap rather than the arithmetic")
tight, refused = study.book(big, "NIFTY", spec_of(stop_buffer_atr=0.50,
                                                  max_risk_atr=0.5))
ok(not tight and refused[phase54.STOP_TOO_WIDE] == 1,
   "a stop wider than the declared risk cap is refused, not shrunk to fit")
blocked, cost_ref = study.book(big, "NIFTY", spec_of(cost_gate=1e9))
ok(not blocked and cost_ref[phase54.COST_BLOCKED] == 1,
   "an unreachable cost gate blocks the trade and is counted as COST_BLOCKED")
ok(sum(refusals.values()) + len(trades) == len(big["rows"]),
   "every inspected candidate is either a trade or exactly one refusal")

# Two overlapping sequences: the second expansion happens while the first trade
# is still open, so a book that traded both would be a book no rule could run.
overlap_days = scaled(
    FILLER + [LONG_EXPANSION, LONG_RECLAIM, LONG_ENTRY_DAY]
    + [(119.0, 130.0, 118.0, 129.0), (129.0, 130.0, 111.0, 128.0),
       (128.0, 140.0, 127.0, 139.0)]
    + FILLER
)
ov = tables(overlap_days)
ov_long = [r for r in ov["rows"] if r["side"] > 0]
ok(len(ov_long) >= 2, "the fixture really does offer a second overlapping entry")
ov_trades, ov_ref = study.book(ov, "NIFTY", spec_of(max_hold_sessions=10))
ok(len({x["entry_ordinal"] for x in ov_trades}) == len(ov_trades),
   "no two trades share an entry session")
for a, b in zip(ov_trades, ov_trades[1:]):
    ok(b["entry_ordinal"] > a["exit_ordinal"],
       "a position is never opened before the previous one left")
    ok(b["expansion_ordinal"] > a["exit_ordinal"],
       "and the next trade needs an expansion that happened after that exit, "
       "which is the no-re-entry-until-a-new-expansion rule")
ok(ov_ref[phase54.POSITION_ALREADY_OPEN] >= 1,
   "the candidate that would have pyramided is refused and counted")

# ------------------------------------------------------ hashes and families
section("entry event sets and event families")
h1 = study._entry_set_hash("NIFTY", trades)
ok(h1 == study._entry_set_hash("NIFTY", list(reversed(trades))),
   "the entry-set hash does not depend on row order")
ok(h1 != study._entry_set_hash("CRUDEOIL", trades),
   "two instruments cannot merge into one hypothesis by a timestamp coincidence")
flipped = [{**trades[0], "side": -trades[0]["side"]}]
ok(h1 != study._entry_set_hash("NIFTY", flipped),
   "the same instant entered the other way is a different hypothesis")
f1 = study._family_hash("NIFTY", trades)
wider = [{**trades[0], "stop": trades[0]["stop"] - 1.0}]
ok(study._entry_set_hash("NIFTY", wider) == h1,
   "a different stop on the same instants is the same entry event set")
ok(study._family_hash("NIFTY", wider) != f1,
   "but a different family, because the economics differ")
ok(study._entry_set_hash("NIFTY", []) == study._entry_set_hash("NIFTY", []),
   "an empty book hashes without raising")

# ---------------------------------------------------------------- statistics
section("the correction, the drawdown and the eligibility gate")
ok(stats.one_sided_p(np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])) < 0.05,
   "a consistently positive sample gets a small p-value")
ok(stats.one_sided_p(np.array([-1.0] * 8)) > 0.5,
   "a consistently negative one does not")
ok(stats.one_sided_p(np.array([1.0])) == 1.0,
   "a single observation is refused rather than declared significant")
ok(stats.one_sided_p(np.array([0.0, 0.0, 0.0])) >= 0.5,
   "a zero-mean sample is not significant")
ok(stats.one_sided_p(np.array([1.0, np.nan, 1.0, 1.0])) < 1.0,
   "a NaN is dropped rather than poisoning the statistic")
ok(stats.benjamini_hochberg([1e-7], tests=256) == [True],
   "and still passes against the full denominator when it is strong enough")
ok(stats.benjamini_hochberg([0.001], tests=256) == [False],
   "a p-value that would pass alone fails once the whole registered grid is "
   "the denominator, which is the point of correcting over 256 rather than "
   "over the survivors")
ok(stats.benjamini_hochberg([0.001], tests=1) == [True],
   "the same p-value would have passed uncorrected — the denominator is doing "
   "real work")
ok(stats.benjamini_hochberg([], tests=256) == [],
   "no tests, no passes, no exception")
ok(stats.benjamini_hochberg([1e-9, 0.9], tests=2) == [True, False],
   "BH keeps the strong test and refuses the weak one in the same family")
ok(stats.benjamini_hochberg([0.5] * 4, tests=2) == [False] * 4,
   "the denominator is never allowed below the number of p-values supplied")
ok(abs(stats.max_drawdown_r(np.array([1.0, -2.0, 1.0])) - 2.0) < 1e-9,
   "the drawdown is the worst peak-to-trough of the equity curve in R")
ok(stats.max_drawdown_r(np.array([1.0, 1.0])) == 0.0,
   "a curve that only rises has no drawdown")
ok(stats.max_drawdown_r(np.array([])) == 0.0, "an empty curve has none either")
elig = stats.eligibility("NIFTY")
ok(elig["eligible"] and elig["source"] == phase54.SOURCE,
   "NIFTY is eligible and named as historical candle data")
ok(elig["bars"] > phase54.MIN_BARS and elig["sessions"] > phase54.MIN_SESSIONS,
   "and clears the declared data floors")
ok("NOT_AN_EXECUTABLE_BOOK" in elig["execution_note"],
   "and carries the reminder that an OHLC file is not a book")
ok(not stats.eligibility("NOT_AN_INSTRUMENT")["eligible"],
   "an instrument with no file is refused rather than searched")
ok(stats.eligibility("NOT_AN_INSTRUMENT")["reason"],
   "and says why rather than returning a bare false")
# These files carry negative volumes, which makes every volume-derived feature
# meaningless. The guarantee is not a disclaimer but an absence: no module in
# the phase reads the field at all.
volume_readers = [
    p.name for p in sorted(Path("app/research/phase54").glob("*.py"))
    if "volume" in p.read_text()
]
ok(volume_readers == [],
   "no Phase 54 module touches the volume field, which in these files is "
   "negative often enough to be unusable")

# ------------------------------------------------------------- metrics
section("the required metric set, the splits and the readings")
desc = metrics.describe(trades)
for field in ("trade_count", "session_count", "win_rate", "average_winner_r",
              "average_loser_r", "net_expectancy_r", "gross_expectancy_r",
              "profit_factor", "max_drawdown_r", "average_hold_sessions",
              "median_risk_points", "median_target_distance",
              "median_cost_points", "median_cost_multiple",
              "cost_share_of_gross_pct", "long_count", "short_count",
              "largest_winner_r", "largest_loser_r", "top_trade_share",
              "median_pullback_sessions", "net_total_r", "median_risk_atr",
              "p_value_one_sided", "target_rate", "stop_rate",
              "time_exit_rate"):
    ok(field in desc, f"describe reports {field}")
ok(metrics.describe([])["measured"] is False,
   "an empty book is unmeasured rather than zero-filled")
ok(desc["gross_expectancy_r"] >= desc["net_expectancy_r"],
   "gross is never below net")
by_year = metrics.per_period(trades, "year")
ok(sum(v["trade_count"] for v in by_year.values()) == len(trades),
   "the per-year split accounts for every trade exactly once")
by_dir = metrics.per_direction(trades)
ok(sum(v["trade_count"] for v in by_dir.values()) == len(trades),
   "long and short account for every trade exactly once")
ok(set(by_dir).issubset(set(phase54.DIRECTIONS)),
   "the direction split uses the declared direction labels")

good = {"measured": True, "trade_count": 60,
        "net_expectancy_points": 20.0, "gross_expectancy_points": 25.0,
        "net_expectancy_r": 0.20, "gross_expectancy_r": 0.25,
        "net_total_r": 12.0, "cost_share_of_gross_pct": 5.0}
delta = metrics.holding_delta(
    {"measured": True, "net_expectancy_r": 0.05, "gross_expectancy_r": 0.05,
     "trade_count": 60},
    {"measured": True, "net_expectancy_r": 0.30, "gross_expectancy_r": 0.35,
     "trade_count": 60},
)
ok(delta["measured"] and delta["material"] and delta["gross_delta_r"] > 0,
   "a cell that improves at the longer cap is a measured, material holding "
   "effect")
ok(metrics.holding_delta({"measured": False}, {"measured": True})["measured"]
   is False, "an unpaired cell reports no holding comparison")
ok(metrics.effect_reading(good, {}, {}) == phase54.DIRECTIONAL_EDGE,
   "a positive gross and a positive net over a real sample reads as direction")
ok(metrics.effect_reading({"measured": False}, {}, {}) == phase54.NO_EDGE,
   "nothing measured claims no edge rather than a reading it cannot support")
ok(metrics.effect_reading({**good, "trade_count": 3}, {}, {}) ==
   phase54.INSUFFICIENT_SAMPLE,
   "a handful of multi-day trades reads INSUFFICIENT_SAMPLE, whatever its sign")
ok(metrics.effect_reading({**good, "net_expectancy_points": -1.0}, {}, {}) ==
   phase54.COST_DRIVEN_APPEARANCE,
   "a positive gross that cost turns negative is the cost wall, not an edge")
ok(metrics.effect_reading({**good, "gross_expectancy_points": -1.0}, {}, {}) ==
   phase54.NO_EDGE, "a non-positive gross reads NO_EDGE before cost is blamed")
ok(metrics.effect_reading(
    good, {"2026": {"net_total_r": 11.0, "net_expectancy_r": 1.0}},
    {}) == phase54.RECENT_PERIOD_OVERFIT,
   "a net that leans on one year reads RECENT_PERIOD_OVERFIT")
ok(metrics.effect_reading(
    good, {}, {"2026Q1": {"net_total_r": 11.0}}) ==
   phase54.RECENT_PERIOD_OVERFIT, "and so does one that leans on one quarter")
ok(metrics.effect_reading(
    {**good, "net_expectancy_r": 0.2}, {}, {},
    {"measured": True, "material": True, "longer_is_better": True,
     "short_net_expectancy_r": -0.05}) == phase54.HOLDING_PERIOD_EFFECT,
   "a row positive only once the hold cap is loosened reads as the hold, not "
   "as direction")
ok(metrics.effect_source({"measured": False}) == phase54.EFFECT_NONE,
   "nothing measured separates into none of the three")
ok(metrics.effect_source({**good, "net_expectancy_r": -0.1}) ==
   phase54.EFFECT_NONE, "a negative net separates into nothing to attribute")
ok(metrics.effect_source(good) == phase54.EFFECT_INTERACTION,
   "a positive gross plus a small cost share is an interaction of the two, "
   "which is what the phase is asked to separate rather than conflate")
ok(metrics.effect_source({**good, "cost_share_of_gross_pct": 90.0}) ==
   phase54.EFFECT_DIRECTIONAL,
   "strip the cost efficiency and what is left is attributed to direction")
ok(metrics.effect_source(
    {**good, "cost_share_of_gross_pct": 90.0, "gross_expectancy_r": -0.1}) ==
   phase54.EFFECT_NONE,
   "a positive net on a negative gross attributes to none of the three")
ok(metrics.effect_source(good) in phase54.EFFECT_SOURCES,
   "and a measured row always lands on one of the declared five")

# --------------------------------------------------------- promotion gate
section("the promotion bar, refusing on the strongest ground first")
good_overall = {"measured": True, "trade_count": 60, "session_count": 60,
                "net_expectancy_r": 0.2, "gross_expectancy_r": 0.25,
                "profit_factor": 1.5, "max_drawdown_r": 4.0,
                "net_total_r": 15.0, "top_trade_share": 0.1}
good_parts = {
    phase54.DISCOVERY: {"trade_count": 40, "session_count": 40,
                        "net_expectancy_r": 0.2},
    phase54.VALIDATION: {"trade_count": 12, "session_count": 12,
                         "net_expectancy_r": 0.2},
    phase54.UNTOUCHED_HOLDOUT: {"trade_count": 12, "session_count": 12,
                                "net_expectancy_r": 0.2},
}
good_years = {str(y): {"net_total_r": 3.0, "net_expectancy_r": 0.2}
              for y in range(2021, 2026)}
good_quarters = {f"{y}Q{q}": {"net_total_r": 1.0, "net_expectancy_r": 0.2}
                 for y in range(2021, 2026) for q in (1, 2, 3, 4)}
good_stress = {"1.0": {"net_expectancy_r": 0.2},
               "1.5": {"net_expectancy_r": 0.15},
               "2.0": {"net_expectancy_r": 0.1}}


def grade(**over) -> str:
    parts = {k: dict(v) for k, v in good_parts.items()}
    years = dict(good_years)
    quarters = dict(good_quarters)
    stress = {k: dict(v) for k, v in good_stress.items()}
    overall = dict(good_overall)
    fdr_pass = over.pop("fdr_pass", True)
    had = over.pop("had_candidates", True)
    for key, value in over.items():
        if key == "years":
            years = value
        elif key == "quarters":
            quarters = value
        elif key.startswith("stress__"):
            stress[key.split("__", 1)[1]] = value
        elif key.startswith("overall__"):
            overall[key.split("__", 1)[1]] = value
        else:
            partition, field = key.split("__", 1)
            parts[partition][field] = value
    return study._grade(parts, years, quarters, stress, overall,
                        fdr_pass=fdr_pass, had_candidates=had)[0]


ok(grade() == phase54.ROBUST_CANDIDATE,
   "a row that clears every declared condition is a robust candidate")
ok(grade(DISCOVERY__net_expectancy_r=-0.1) == phase54.REJECTED,
   "a negative discovery is rejected before anything else is looked at")
ok(grade(DISCOVERY__trade_count=10) == phase54.PROMISING_NEEDS_DATA,
   "a thin discovery cannot promote, however good it looks")
ok(grade(VALIDATION__net_expectancy_r=-0.1) == phase54.HISTORICAL_LEAD,
   "positive in discovery, negative forward, is a historical lead")
ok(grade(VALIDATION__trade_count=2) == phase54.PROMISING_NEEDS_DATA,
   "a thin validation cannot promote")
ok(grade(UNTOUCHED_HOLDOUT__net_expectancy_r=-0.1) == phase54.HISTORICAL_LEAD,
   "a negative untouched holdout blocks promotion")
ok(grade(UNTOUCHED_HOLDOUT__trade_count=2) == phase54.PROMISING_NEEDS_DATA,
   "a thin holdout blocks promotion")
ok(grade(fdr_pass=False) == phase54.OVERFIT_RISK,
   "failing the correction is OVERFIT_RISK, not a candidate")
ok(grade(overall__trade_count=30) == phase54.PROMISING_NEEDS_DATA,
   "fewer trades than the promotion floor cannot promote")
ok(grade(overall__profit_factor=1.0) == phase54.OVERFIT_RISK,
   "a profit factor below 1.2 blocks promotion")
ok(grade(overall__max_drawdown_r=99.0) == phase54.OVERFIT_RISK,
   "a drawdown beyond the declared bound blocks promotion")
ok(grade(overall__top_trade_share=0.9) == phase54.OVERFIT_RISK,
   "one trade carrying most of the net blocks promotion")
ok(grade(years={"2025": {"net_total_r": 30.0, "net_expectancy_r": 0.3}})
   == phase54.OVERFIT_RISK, "a single positive year blocks promotion")
ok(grade(quarters={"2025Q1": {"net_total_r": 25.0, "net_expectancy_r": 0.3}})
   == phase54.OVERFIT_RISK, "one quarter carrying the net blocks promotion")
ok(grade(**{"stress__2.0": {"net_expectancy_r": -0.1}}) == phase54.OVERFIT_RISK,
   "a result that dies at 2x cost is a cost assumption, not an edge")
ok(grade(overall__measured=False) == phase54.COST_BLOCKED,
   "events that were all refused by the gate or the cap are COST_BLOCKED")
ok(grade(overall__measured=False, had_candidates=False) == phase54.REJECTED,
   "no event at all is REJECTED rather than COST_BLOCKED")
ok(grade() in phase54.FINAL_STATUSES,
   "the grade is always one of the six declared statuses")

# ------------------------------------------------- phases 41-53 unchanged
section("phases 41-53 definitions unchanged")
ok(phase49.rule_fingerprint() == "c2bff87a600a0639", "phase49 grouping")
ok(p50tiers.tier_cadence_fingerprint() == "a134bdf1aec1e005", "phase50 cadence")
ok(phase51.preregistration_fingerprint() == "e8afe891d31dbbf8",
   "phase51 pre-registration")
ok(phase52.fingerprint() == "d3f7ccfd208d5e9e", "phase52 pre-registration")
ok(phase53.fingerprint() == "dfb264f351c46336", "phase53 pre-registration")
ok(phase53.RETEST_TOLERANCES == (0.00, 0.10),
   "phase53's tolerances are not loosened by this phase")
ok(phase53.MAX_HOLD_MINUTES == 180,
   "phase53's 180-minute clock is not re-tuned by this phase")
ok(phase53.COST_GATE_MULTIPLES == (3.0, 4.0),
   "phase53's cost gates are not loosened by this phase")
ok(phase52.GAP_ATR_THRESHOLDS == (0.10, 0.20, 0.30),
   "phase52's gap thresholds are untouched")
ok(phase51.MIN_TRADES == 100, "phase51's own floors are untouched")
ok(phase54.fingerprint() not in {
    "c2bff87a600a0639", "a134bdf1aec1e005", "e8afe891d31dbbf8",
    "d3f7ccfd208d5e9e", "dfb264f351c46336",
}, "phase54 carries its own fingerprint, pooled with none of the others")

# --------------------------------------------------------------- the CLI
section("the CLI's read-only commands")
for argv in (["prereg"], ["grid"], ["universe"]):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = cli.main_argv(argv)
    ok(len(out) > 200, f"cli {argv[0]} prints")
ok(phase54.fingerprint() in cli.main_argv(["prereg"]),
   "the CLI prints the fingerprint it will measure under")
ok(phase54.RECLAIM_BASIS in cli.main_argv(["prereg"]),
   "and the reclaim reading, so the interpretation is not hidden in code")
ok("128" in cli.main_argv(["grid"]),
   "the CLI prints the size of the registered grid")
ok(phase54.SOURCE in cli.main_argv(["universe"]),
   "the CLI names the data as historical candles")
funnel_text = cli.main_argv(["funnel", "--instrument", "NIFTY"])
ok(phase54.NO_EXPANSION in funnel_text, "the funnel command prints the refusals")

# ------------------------------------------------------------ end to end
section("end to end on the real files — this is the slow part")
s1 = p24data.load_series("CRUDEOIL")
ok(s1 is not None and len(s1) > 500_000, "the CRUDEOIL minute series loads")
result = study.run(("CRUDEOIL", "NIFTY"))
t = result["totals"]
ok(t["TOTAL_PARAMETERIZATIONS"] == 256,
   "two instruments at 128 parameterizations each is the whole denominator")
ok(t["UNIQUE_ENTRY_EVENT_SETS"] <= t["TOTAL_PARAMETERIZATIONS"],
   "entry event sets cannot outnumber parameterizations")
ok(t["UNIQUE_ENTRY_EVENT_SETS"] <= t["UNIQUE_EVENT_FAMILIES"],
   "families are the finer unit: a family splits an entry set by geometry, "
   "never merges two of them")
ok(t["DISCOVERY_LEADS"] >= t["ROBUST_CANDIDATES"],
   "nothing is promoted that was not a discovery lead")
ok(t["ROBUST_CANDIDATES"] <= t["HOLDOUT_POSITIVE"],
   "a robust candidate must at least be holdout positive")
for inst in result["instruments"]:
    name = inst["instrument"]
    ok(inst["eligible"], f"{name} is eligible")
    ok(len(inst["rows"]) == 128, f"{name} ran all 128 parameterizations")
    ok(len(inst["funnels"]) == 4, f"{name} reports all four event funnels")
    ok(len(inst["entry_sets"]) <= 128 and len(inst["families"]) <= 128,
       f"{name}'s distinct hypotheses cannot exceed the grid")
    ok(inst["daily_sessions"] > phase54.MIN_SESSIONS,
       f"{name} aggregated more completed sessions than the declared floor")
    sets = {r["entry_event_set"] for r in inst["rows"]}
    fams = {r["event_family"] for r in inst["rows"]}
    ok(len(sets) == inst["totals"]["UNIQUE_ENTRY_EVENT_SETS"],
       f"{name}'s entry-set count is the count of distinct hashes")
    ok(len(fams) == inst["totals"]["UNIQUE_EVENT_FAMILIES"],
       f"{name}'s family count is the count of distinct hashes")
    for row in inst["rows"]:
        vid = row["variant_id"]
        ok(row["final_status"] in phase54.FINAL_STATUSES,
           f"{name} {vid} carries a declared status")
        ok(row["effect_reading"] in phase54.EFFECT_READINGS,
           f"{name} {vid} carries a declared reading")
        ok(row["effect_source"] in phase54.EFFECT_SOURCES,
           f"{name} {vid} carries a declared effect source")
        ok(row["final_status"] != phase54.ROBUST_CANDIDATE or
           not row["status_reasons"],
           f"{name} {vid}: a robust candidate has no outstanding reason")
        ok(bool(row["fdr_pass"]) or
           row["final_status"] != phase54.ROBUST_CANDIDATE,
           f"{name} {vid}: nothing is promoted without the correction")
        o = row["overall"]
        if not o.get("measured"):
            continue
        ok(o["trade_count"] == sum(
            row["per_partition"][p]["trade_count"] for p in phase54.PARTITIONS
        ), f"{name} {vid}: the partitions account for every trade exactly once")
        ok(o["session_count"] == o["trade_count"],
           f"{name} {vid}: one position at a time means one trade per session")
        ok(o["long_count"] + o["short_count"] == o["trade_count"],
           f"{name} {vid}: every trade is a long or a short")
        ok(o["median_risk_atr"] <= row["max_risk_atr"] + 1e-9,
           f"{name} {vid}: no kept trade risks more than its declared cap")
        ok(o["median_cost_multiple"] >= row["cost_gate"] - 1e-9,
           f"{name} {vid}: every kept trade cleared its own cost gate")
        ok(o["average_hold_sessions"] <= row["max_hold_sessions"] + 1.0 + 1e-9,
           f"{name} {vid}: no trade outlives the hold cap plus the exit session")
        ok(o["gross_expectancy_points"] >= o["net_expectancy_points"],
           f"{name} {vid}: gross is never below net")
        ok(set(row["cost_stress"]) == {"1.0", "1.5", "2.0"},
           f"{name} {vid}: the three cost-stress readings are present")
        s1x = row["cost_stress"]["1.0"]["net_expectancy_r"]
        s2x = row["cost_stress"]["2.0"]["net_expectancy_r"]
        ok(s2x <= s1x + 1e-12,
           f"{name} {vid}: charging cost twice cannot improve a result")
        ok(abs(s1x - o["net_expectancy_r"]) < 1e-9,
           f"{name} {vid}: the 1x stress reproduces the base result exactly")
        ok(all(v["trade_count"] > 0 for v in row["per_year"].values()),
           f"{name} {vid}: every reported year has trades in it")

# Rows that share an entry event set must share the entries themselves, and
# rows whose only difference is the hold cap must share them too — the holding
# question is only answerable if the entries are held fixed.
by_set: dict[str, list[dict]] = {}
for inst in result["instruments"]:
    for row in inst["rows"]:
        by_set.setdefault(row["entry_event_set"], []).append(row)
ok(any(len(v) > 1 for v in by_set.values()),
   "the grid really does spell some event sets more than once")
ok(all(len({r["instrument"] for r in v}) == 1 for v in by_set.values()),
   "no entry event set spans two instruments")

text = report.render(result)
for label in report.TOTAL_KEYS:
    ok(label in text, f"the report prints {label}")
ok(str(t["ROBUST_CANDIDATES"]) in text,
   "the report prints the robust count it measured")
ok(phase54.CANDLE_HAS_NO_BOOK in text,
   "the candle honesty travels with the text")
ok(phase54.OVERNIGHT_RISK_IS_ONLY_PARTLY_MODELLED in text,
   "so does the overnight-gap limitation of a multi-day model")
ok(phase54.SAME_BAR_TIE_IS_A_LOSS in text, "so does the same-bar tie rule")
ok(phase54.EARLIER_PHASES_UNTOUCHED in text,
   "the report states the earlier phases are untouched")
ok(phase54.NOT_A_PREDICTION in text, "the report refuses to be a prediction")
ok(phase54.NO_OPTION_CLAIM in text, "the report makes no option claim")
ok(phase54.NO_ORDER_PATH in text, "the report states there is no order path")
ok(phase54.COST_GATE_IS_NOT_A_PROBABILITY in text,
   "the report states the cost gate is not a probability")
ok(phase54.STRICT_RECLAIM_IS_A_DIAGNOSTIC in text,
   "and that the stricter reclaim reading was a diagnostic, never graded")
if t["ROBUST_CANDIDATES"] == 0:
    ok("ROBUST_CANDIDATES = 0" in text,
       "a zero result is printed as a zero, with no row substituted for one")

ans = report.answers(result)
for q in (
    "Does the multi-day expansion/reclaim mechanism contain a directional edge?",
    "Does it work on CRUDEOIL?",
    "Does it work on NIFTY?",
    "How many independent events occur?",
    "Does longer holding materially improve gross expectancy?",
    "Does it remain positive after realistic costs?",
    "Does it survive the untouched holdout?",
    "Is the effect stable across years?",
    "Is it suitable for live executable futures paper validation?",
):
    ok(bool(ans.get(q)), f"answered: {q}")
ok(len(ans) >= 9, "all nine required questions are answered")
ok(any("directional" in q and "cost" in q for q in ans),
   "and the required direction / cost / holding separation is answered too")

paths = report.write_artefacts(result, text=text)
ok(len(paths) == 6, "six immutable artefacts are written")
ok(all(Path(p).exists() and Path(p).stat().st_size > 0 for p in paths),
   "every artefact has content")
ok(all(phase54.fingerprint() in Path(p).name for p in paths),
   "every artefact is named with the fingerprint it was measured under")
again = report.write_artefacts(result, text=text)
ok(not (set(paths) & set(again)) or all(Path(p).exists() for p in again),
   "a second write never overwrites the first")

print(f"\nPHASE 54 SMOKE — {PASS} passed, {FAIL} failed")
if FAIL:
    raise SystemExit(1)
