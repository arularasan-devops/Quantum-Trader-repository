"""Focused smoke for the Phase 8 research package. No network, no DB, no orders.

What is worth proving is not that the tables render — it is that Phase 8 cannot
overclaim and cannot quietly disagree with Phase 7:

* it computes no R of its own: every fill number comes from ``policies.simulate``;
* a non-monotone confidence table is reported as a calibration FAILURE, and a
  monotone one is not upgraded past HYPOTHESIS;
* an unmeasurable or crossed book is "unmeasured", never zero cost;
* spread cost only ever makes a trade worse (net R <= gross R);
* exactly one primary cause per trade, taken from the stated priority order, and
  the flags still record every cause that applied;
* winners are classified too, so a cause that describes the market is visible;
* small cells are labelled REQUIRES MORE DATA rather than reported as findings.

    .venv/bin/python _smoke_phase8.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import datetime as dt  # noqa: E402

from app.models import Candle  # noqa: E402
from app.research.phase7 import paths, policies  # noqa: E402
from app.research.phase7.dataset import IST, REAL, ChainSeries  # noqa: E402
from app.research.phase8 import (  # noqa: E402
    calibration,
    capture,
    causes,
    contract,
    continuation,
    findings,
    gates8,
    spread,
)
from app.research.phase8 import expiry as expiry_mod  # noqa: E402

CHECKS = 0
FAILS: list[str] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILS.append(f"{label}: {detail}")
        print(f"FAIL {label} {detail}")
    else:
        print(f"ok   {label} {detail}")


START = 1_700_000_000


def leg(symbol: str, premium: float, side: str = "CE", strike: float = 7800.0,
        bid: float | None = None, ask: float | None = None, oi: int = 500,
        volume: int = 100) -> dict:
    row = {"symbol": symbol, "strike": strike, "option_type": side,
           "premium": premium, "iv": 20.0, "delta": 0.5, "gamma": 0.0,
           "theta": 0.0, "vega": 0.0, "oi": oi, "oi_change": 0, "volume": volume}
    if bid is not None:
        row["bid"] = bid
    if ask is not None:
        row["ask"] = ask
    return row


def series(prices: list[float], *, symbol: str = "X7800CE", side: str = "CE",
           spread_pct: float | None = None, mirror: list[float] | None = None,
           oi: int = 500, volume: int = 100, crossed: bool = False) -> ChainSeries:
    s = ChainSeries("TEST", REAL)
    for i, px in enumerate(prices):
        legs = {}
        book = {}
        if spread_pct is not None:
            half = px * spread_pct / 200.0
            book = {"bid": round(px - half, 2), "ask": round(px + half, 2)}
            if crossed:
                book = {"bid": book["ask"], "ask": book["bid"]}
        legs[symbol] = leg(symbol, px, side, bid=book.get("bid"),
                           ask=book.get("ask"), oi=oi, volume=volume)
        if mirror is not None and i < len(mirror):
            other = "PE" if side == "CE" else "CE"
            sym2 = f"X7800{other}"
            legs[sym2] = leg(sym2, mirror[i], other, bid=book.get("bid"),
                             ask=book.get("ask"))
        s.ts.append(START + 60 * i)
        s.legs.append(legs)
        s.raw.append(list(legs.values()))
    return s


def event(entry: float, stop: float, target: float, prices: list[float], *,
          confidence: float = 80.0, regime: str = "TREND_UP",
          data_flag: str = "FRESH", chain_age: int = 5, atr: float = 10.0,
          leg_delta: float = 0.5, s: ChainSeries | None = None,
          bar: int = 0) -> paths.BuyEvent:
    s = s if s is not None else series([entry] + prices)
    ev = paths.BuyEvent(
        instrument="TEST", ts=START, bar=bar, side="CE", symbol="X7800CE",
        strike=7800.0, spot=7800.0, entry=entry, stop=stop, target1=target,
        target2=target, target3=target, confidence=confidence, trade_score=80.0,
        regime=regime, atr=atr, leg_delta=leg_delta, vwap=7790.0, ema9=7795.0,
        ema20=7780.0, prev_mid=7792.0, breakout_level=7799.0,
        chain_age_sec=chain_age, data_flag=data_flag)
    ev.path = paths.walk(s, "X7800CE", START, entry, stop, target)
    ev.chase = paths.measure_chase(ev, s)
    ev.chase["class"] = "IDEAL_ENTRY"
    return ev, s


def row_of(ev, fill) -> dict:
    path = ev.path
    return {
        "ts_ist": "x",
        "realised_r": round(fill.r, 3),
        "mfe_r": round(fill.mfe_r, 3),
        "mae_r": round(fill.mae_r, 3),
        "target_after_stop": bool(
            path.first_target_ts is not None and path.first_stop_ts is not None
            and path.first_target_ts > path.first_stop_ts),
    }


# --- labels ---------------------------------------------------------------
ok("tiny cell requires more data",
   findings.label(3, 1) == findings.NEEDS_DATA)
ok("modest cell is a hypothesis only",
   findings.label(25, 1) == findings.HYPOTHESIS)
ok("passing gates still only a hypothesis",
   findings.label(500, 40) == findings.HYPOTHESIS,
   "Phase 8 never promotes a finding to a rule")
ok("descriptive block is an observation at any size",
   findings.label(1, 1, comparative=False) == findings.OBSERVATION)

# --- calibration ----------------------------------------------------------
ok("bucket edges follow the spec",
   [calibration.bucket_of(x) for x in (69.9, 70.0, 74.9, 80.0, 94.9, 95.0, 99.0)]
   == ["<70", "70-75", "70-75", "80-85", "90-95", "95+", "95+"])


def calib_pairs(spec: list[tuple[float, int, bool]]) -> list[tuple]:
    """(confidence, count, winner) -> reconstructed pairs at that confidence."""
    out = []
    for conf, n, win in spec:
        for _ in range(n):
            if win:      # target reached before stop
                ev, _s = event(100.0, 90.0, 110.0,
                               [101.0, 102.0, 103.0, 104.0, 106.0, 112.0],
                               confidence=conf)
            else:        # stop first
                ev, _s = event(100.0, 90.0, 110.0,
                               [99.0, 98.0, 97.0, 96.0, 93.0, 89.0],
                               confidence=conf)
            out.append((ev, policies.simulate(ev, "CONTROL", "A_BASELINE")))
    return out


rising = calib_pairs([(72.0, 12, False), (82.0, 12, False), (92.0, 12, True)])
mono = calibration.study(rising, sessions=1)
ok("monotone sample is not called a failure",
   mono["monotonicity"]["calibration_failure"] is False,
   mono["monotonicity"]["verdict"])
ok("monotone sample is still only a hypothesis",
   mono["label"] == findings.HYPOTHESIS, mono["label"])

falling = calib_pairs([(72.0, 12, True), (82.0, 12, True), (92.0, 12, False)])
bad = calibration.study(falling, sessions=1)
ok("inverted confidence is a calibration failure",
   bad["monotonicity"]["calibration_failure"] is True,
   bad["monotonicity"]["verdict"])
ok("inversion is named with the buckets involved",
   any(i["from"] == "80-85" and i["to"] == "90-95"
       for i in bad["monotonicity"]["hit_rate_inversions"]),
   str(bad["monotonicity"]["hit_rate_inversions"]))
ok("rank correlation has the sign of the inversion",
   (bad["monotonicity"]["spearman_confidence_vs_realised_r"] or 0) < 0,
   str(bad["monotonicity"]["spearman_confidence_vs_realised_r"]))
ok("two thin buckets are untestable, not a failure",
   calibration.study(calib_pairs([(72.0, 3, True), (92.0, 3, False)]),
                     sessions=1)["monotonicity"]["verdict"] == "UNTESTABLE")
ok("calibration reuses the phase 7 metric block",
   mono["buckets"][1]["actual_target_before_stop_pct"] is not None
   and mono["population"].startswith("CONTROL"))

# --- spread ---------------------------------------------------------------
WIN_PATH = [101.0, 102.0, 103.0, 104.0, 106.0, 112.0]
ev, s = event(100.0, 90.0, 110.0, WIN_PATH)
s_book = series([100.0, *WIN_PATH], spread_pct=4.0)
ev_book, _ = event(100.0, 90.0, 110.0, WIN_PATH, s=s_book)
fill = policies.simulate(ev_book, "CONTROL", "A_BASELINE")
sp = spread.measure(ev_book, fill, s_book)
ok("book present -> measured", sp["measured"] is True, str(sp.get("entry_spread")))
ok("net R never better than gross R", sp["net_r"] <= sp["gross_r"],
   f"{sp['net_r']} vs {sp['gross_r']}")
ok("spread cost is charged at both ends",
   sp["entry_spread_cost"] > 0 and sp["exit_spread_cost"] > 0, str(sp))
ok("spread share of risk is reported",
   sp["spread_share_of_risk_pct"] > 0, str(sp["spread_share_of_risk_pct"]))

no_book = spread.measure(ev, policies.simulate(ev, "CONTROL", "A_BASELINE"), s)
ok("absent book is unmeasured, not free", no_book["measured"] is False
   and "net_r" not in no_book)
s_cross = series([100.0, *WIN_PATH], spread_pct=4.0, crossed=True)
ev_cross, _ = event(100.0, 90.0, 110.0, WIN_PATH, s=s_cross)
cross = spread.measure(ev_cross,
                       policies.simulate(ev_cross, "CONTROL", "A_BASELINE"), s_cross)
ok("crossed book is unmeasured", cross["measured"] is False)

agg = spread.study([sp, no_book], sessions=1)
ok("aggregate separates measured from unmeasured",
   agg["overall"]["n"] == 2 and agg["overall"]["measured"] == 1,
   str(agg["overall"]["n"]))
ok("aggregate keeps gross and net apart",
   agg["overall"]["net_expectancy_r"] <= agg["overall"]["gross_expectancy_r"])
ok("aggregate splits by side and instrument",
   "CE" in agg["by_side"] and "TEST" in agg["by_instrument"])

# --- causes ---------------------------------------------------------------
flat = [Candle(time=START + 60 * i, open=7800.0, high=7801.0, low=7799.0,
               close=7800.0, volume=10) for i in range(40)]
rally = [Candle(time=START + 60 * i, open=7800.0 + i, high=7805.0 + i,
                low=7799.0 + i, close=7800.0 + i, volume=10) for i in range(40)]

# direction: the leg went nowhere while its mirror ran to target
DEAD_PATH = [99.5, 99.0, 98.5, 98.0, 97.0, 96.0]
s_dir = series([100.0, *DEAD_PATH],
               mirror=[100.0, 103.0, 106.0, 109.0, 112.0, 115.0, 120.0])
ev_dir, _ = event(100.0, 90.0, 110.0, DEAD_PATH, s=s_dir)
f_dir = policies.simulate(ev_dir, "CONTROL", "A_BASELINE")
r_dir = row_of(ev_dir, f_dir)
c_dir = causes.classify(ev_dir, f_dir, r_dir, {"measured": False}, s_dir, flat)
ok("mirror leg is priced with the engine's own risk and R:R",
   c_dir["mirror"]["available"] is True and c_dir["mirror"]["side"] == "PE",
   str(c_dir["mirror"]))
ok("dead leg + mirror at target = WRONG_DIRECTION",
   c_dir["primary_cause"] == "WRONG_DIRECTION", c_dir["primary_cause"])

# stale data outranks everything else
ev_stale, s_stale = event(100.0, 90.0, 110.0, DEAD_PATH,
                          data_flag="STALE", chain_age=300)
f_stale = policies.simulate(ev_stale, "CONTROL", "A_BASELINE")
c_stale = causes.classify(ev_stale, f_stale, row_of(ev_stale, f_stale),
                          {"measured": False}, s_stale, flat)
ok("stale data is the primary cause when present",
   c_stale["primary_cause"] == "DATA_QUALITY", c_stale["primary_cause"])

# underlying moved, premium did not -> the contract, not the direction
ev_strike, s_strike = event(100.0, 90.0, 110.0,
                            [99.8, 99.6, 99.4, 99.2, 99.0, 98.5], atr=4.0)
f_strike = policies.simulate(ev_strike, "CONTROL", "A_BASELINE")
c_strike = causes.classify(ev_strike, f_strike, row_of(ev_strike, f_strike),
                           {"measured": False}, s_strike, rally)
ok("underlying moved but premium did not = BAD_STRIKE",
   c_strike["primary_cause"] == "BAD_STRIKE",
   f"{c_strike['primary_cause']} {c_strike['underlying_move']}")

# gave a full R back
ev_late, s_late = event(100.0, 90.0, 130.0,
                        [104.0, 108.0, 112.0, 115.0, 105.0, 100.0, 96.0])
f_late = policies.simulate(ev_late, "CONTROL", "A_BASELINE")
r_late = row_of(ev_late, f_late)
c_late = causes.classify(ev_late, f_late, r_late, {"measured": False}, s_late, flat)
ok("a full R offered and lost is an exit cause, not a signal cause",
   c_late["primary_cause"] in ("EXIT_TOO_LATE", "INSUFFICIENT_ROOM"),
   c_late["primary_cause"])

# heavy book charged against a tight stop
c_spread = causes.classify(
    ev_book, fill, row_of(ev_book, fill),
    {"measured": True, "spread_share_of_risk_pct": 40.0, "net_r": -0.2},
    s_book, flat)
ok("BAD_SPREAD needs a measured book",
   "BAD_SPREAD" in c_spread["flags"], str(c_spread["flags"]))

ok("exactly one primary cause per trade",
   all(isinstance(c["primary_cause"], str) and c["primary_cause"] in causes.CAUSES
       for c in (c_dir, c_stale, c_strike, c_late, c_spread)))
ok("flags keep every cause that applied",
   len(c_dir["flags"]) >= 1 and c_dir["primary_cause"] in c_dir["flags"])
ok("priority order is the stated definition",
   causes.primary({"EXIT_TOO_LATE": True, "WRONG_DIRECTION": True})
   == "WRONG_DIRECTION")
ok("no flags at all -> UNKNOWN", causes.primary({}) == "UNKNOWN")

table = causes.evidence_table([c_dir, c_stale, c_strike, c_late, c_spread],
                              sessions=1)
ok("evidence table counts wins and losses separately",
   table["wins"] + table["losses"] == table["resolved"] == 5,
   f"{table['wins']}/{table['losses']}")
ok("evidence table classifies winners too, so a market-describing cause shows",
   any(r["win_flagged"] for r in table["table"]) or table["wins"] == 0)
ok("every cause appears in the table",
   {r["cause"] for r in table["table"]} == set(causes.CAUSES))
ok("thin loss population is labelled, not concluded",
   table["label"] == findings.NEEDS_DATA, table["label"])
ok("primary counts sum to the losses",
   sum(r["loss_primary"] for r in table["table"]) == table["losses"])

ok("cause rows carry the expiry class so Part K can split on it",
   "expiry_class" in c_dir)
by_x = causes.by_expiry([c_dir, c_stale, c_strike, c_late, c_spread], sessions=1)
ok("attribution splits by expiry class", "by_class" in by_x and by_x["by_class"])

# --- expiry classification ------------------------------------------------
ok("NFO/MCX symbol grammar parses",
   expiry_mod.parse_expiry("NIFTY18AUG2624450PE") == dt.date(2026, 8, 18)
   and expiry_mod.parse_expiry("CRUDEOIL17AUG268100CE") == dt.date(2026, 8, 17))
ok("BFO compact weekly grammar parses",
   expiry_mod.parse_expiry("SENSEX2682077400CE") == dt.date(2026, 8, 20))
ok("BFO October/November/December letters parse",
   expiry_mod.parse_expiry("SENSEX26O0677400CE") == dt.date(2026, 10, 6))
ok("an unrecognised symbol is not guessed",
   expiry_mod.parse_expiry("WEIRD-SYMBOL-1") is None)
ok("classification is by days to expiry, not by a hard-coded date",
   (expiry_mod.classify(0), expiry_mod.classify(2), expiry_mod.classify(9),
    expiry_mod.classify(None))
   == (expiry_mod.EXPIRY_DAY, expiry_mod.PRE_EXPIRY, expiry_mod.NON_EXPIRY,
       expiry_mod.UNKNOWN))
d_nifty = expiry_mod.describe("NIFTY", "NIFTY18AUG2624450PE",
                              int(dt.datetime(2026, 8, 18, 10, 0,
                                              tzinfo=IST).timestamp()))
ok("same date is expiry day for a contract expiring that day",
   d_nifty["expiry_class"] == expiry_mod.EXPIRY_DAY
   and d_nifty["days_to_expiry"] == 0, str(d_nifty))
ok("minutes to expiry counts down to the venue close",
   abs(d_nifty["minutes_to_expiry"] - 330.0) < 1.0,
   str(d_nifty["minutes_to_expiry"]))
d_gold = expiry_mod.describe("GOLD", "GOLD31AUG26155500CE",
                             int(dt.datetime(2026, 8, 18, 10, 0,
                                             tzinfo=IST).timestamp()))
ok("the SAME session is non-expiry for a different contract",
   d_gold["expiry_class"] == expiry_mod.NON_EXPIRY and d_gold["venue"] == "MCX",
   str(d_gold))

x_rows = [
    {"instrument": "NIFTY", "session": "2026-08-18", "expiry": "2026-08-18",
     "expiry_class": expiry_mod.EXPIRY_DAY, "venue": "NFO", "realised_r": 1.0,
     "target_before_stop_hit": True, "mfe_r": 1.2, "mae_r": -0.1,
     "capture": {"capture_pct": 80.0, "giveback_r": 0.2}, "min_to_mfe": 5.0,
     "min_to_mae": 2.0, "held_min": 10.0, "spread_cost_r": 0.05,
     "premium_expansion_pct": 12.0, "underlying_favourable": 20.0,
     "underlying_adverse": 4.0},
    {"instrument": "GOLD", "session": "2026-08-18", "expiry": "2026-08-31",
     "expiry_class": expiry_mod.NON_EXPIRY, "venue": "MCX", "realised_r": -1.0,
     "target_before_stop_hit": False, "mfe_r": 0.1, "mae_r": -1.0,
     "capture": {"capture_pct": 0.0, "giveback_r": 0.1}, "min_to_mfe": 1.0,
     "min_to_mae": 8.0, "held_min": 12.0, "spread_cost_r": 0.9,
     "premium_expansion_pct": 1.0, "underlying_favourable": 2.0,
     "underlying_adverse": 30.0},
]
x_study = expiry_mod.study(x_rows, sessions=1)
ok("expiry study never pools instruments in the per-instrument view",
   set(x_study["by_instrument"]) == {"NIFTY", "GOLD"}
   and list(x_study["by_instrument"]["NIFTY"]["by_class"]) == [
       expiry_mod.EXPIRY_DAY])
ok("expiry study reports every Part B metric",
   {"target_before_stop_pct", "median_mfe_capture_pct", "median_giveback_r",
    "median_min_to_mfe", "median_min_to_mae", "median_spread_cost_r",
    "median_premium_expansion_pct"}
   <= set(x_study["by_class"][expiry_mod.EXPIRY_DAY]))
ok("single-trade expiry cells are labelled, not concluded",
   x_study["by_class"][expiry_mod.EXPIRY_DAY]["label"] == findings.NEEDS_DATA)

# --- profit capture -------------------------------------------------------
CAP_PATH = [104.0, 108.0, 112.0, 109.0, 106.0, 103.0, 101.0]
# a recorded book, so every policy can be priced net and ranked on net R
s_cap = series([100.0, *CAP_PATH], spread_pct=2.0)
ev_cap, _ = event(100.0, 90.0, 130.0, CAP_PATH, s=s_cap)
ev_cap.expiry_class = expiry_mod.EXPIRY_DAY
cap = capture.run([ev_cap], {"TEST": s_cap}, sessions=1)
ok("capture runs the spec's extra levels through the phase 7 grammar",
   {"B_FIXED_4PCT", "C_TRAIL_25", "C_TRAIL_40"} <= set(capture.POLICIES)
   and cap["policies"]["B_FIXED_4PCT"]["n"] == 1)
ok("fixed capture keeps more of the excursion than holding to the end",
   cap["policies"]["B_FIXED_4PCT"]["median_mfe_capture_pct"]
   > cap["policies"]["A_BASELINE"]["median_mfe_capture_pct"],
   f"{cap['policies']['B_FIXED_4PCT']['median_mfe_capture_pct']} vs "
   f"{cap['policies']['A_BASELINE']['median_mfe_capture_pct']}")
ok("an early exit that the market invalidated is charged as premature",
   cap["policies"]["B_FIXED_2PCT"]["premature_exit_pct"] == 100.0,
   str(cap["policies"]["B_FIXED_2PCT"]["premature_exit_pct"]))
ok("capture splits by expiry class, entry quality, instrument, side, regime",
   all(k in cap for k in ("by_expiry_class", "by_entry_quality", "by_instrument",
                          "by_side", "by_regime"))
   and expiry_mod.EXPIRY_DAY in cap["by_expiry_class"])
best = capture.best_by_net(cap["policies"], min_n=1)
ok("the best policy is returned as a candidate, never a recommendation",
   best is not None and best["status"].startswith("CANDIDATE_ONLY")
   and "margin_over_baseline_r" in best)
ok("thin policy blocks are not ranked at all",
   capture.best_by_net(cap["policies"], min_n=10) is None)

# --- continuation ---------------------------------------------------------
f_cap = policies.simulate(ev_cap, "CONTROL", "A_BASELINE")
obs = continuation.observations(ev_cap, f_cap)
ok("continuation observes only in-profit quotes",
   obs and all(o["extension_r"] > 0 for o in obs), str(len(obs)))
ok("continuation features are causal: momentum uses past quotes only",
   obs[0]["premium_momentum"] == 0.0
   and obs[1]["premium_momentum"] > 0.0, str(obs[:2]))
ok("a quote that later ran further is marked continued",
   obs[0]["continued"] is True)
ok("the last in-profit quote of a fading path is not marked continued",
   obs[-1]["continued"] is False)
cstudy = continuation.study(obs, sessions=1)
ok("observations from one trade count as one trade, not one per quote",
   cstudy["overall"]["trades_contributing"] == 1
   and cstudy["overall"]["n_observations"] == len(obs),
   str(cstudy["overall"]["trades_contributing"]))
ok("continuation refuses the word probability",
   "NOT probabilities" in cstudy["definitions"]["warning"])
ok("continuation states below 20 observations do not claim separation",
   cstudy["separation"]["states_with_20_plus_observations"] == 0
   and cstudy["separation"]["continuation_spread_pp"] is None)

# --- contract quality -----------------------------------------------------
good_leg = leg("X7800CE", 100.0, bid=99.0, ask=101.0)
c_ok = contract.describe(good_leg, 7800.0, 7800.0, "CE")
ok("contract records the recorded chain fields",
   c_ok["recorded"] and c_ok["spread"] == 2.0 and c_ok["greeks_usable"])
degenerate = dict(good_leg, iv=0.01, delta=1.0)
c_bad = contract.describe(degenerate, 7800.0, 7500.0, "CE")
ok("a degenerate deep-ITM greek is labelled, not averaged in",
   c_bad["greeks_usable"] is False and "delta" in c_bad["reason"])
ok("moneyness is signed toward the money",
   c_bad["moneyness_pct"] > 0 and contract.describe(
       good_leg, 7800.0, 8000.0, "CE")["moneyness_pct"] < 0)
ok("a missing leg is reported as unrecorded",
   contract.describe(None, 7800.0, 7800.0, "CE")["recorded"] is False)
c_study = contract.study(
    [{"contract": c_ok, "realised_r": 1.0, "target_before_stop_hit": True},
     {"contract": c_bad, "realised_r": -1.0, "target_before_stop_hit": False}],
    sessions=1)
ok("greek aggregates exclude the degenerate leg",
   c_study["legs_recorded"] == 2 and c_study["degenerate_greek_legs"] == 1
   and c_study["greek_distributions_usable_only"]["iv"]["n"] == 1)

# --- phase 8 gates --------------------------------------------------------
g_thin = gates8.evaluate(resolved=141, sessions=1, expiry_sessions=1,
                         expiry_trades=23, spread_coverage_pct=100.0,
                         expiry_coverage_pct=100.0, smallest_cell=5)
ok("one expiry session cannot support an expiry claim",
   "ADEQUATE_EXPIRY_SESSIONS" in g_thin["failed"]
   and g_thin["status"] == "EXPLORATORY_ONLY")
ok("a failing gate names what it forbids",
   all(g["forbids"] for g in g_thin["gates"]))
ok("descriptive statistics survive a failing gate",
   "descriptive statistics are still produced" in g_thin["verdict"])
g_full = gates8.evaluate(resolved=500, sessions=25, expiry_sessions=8,
                         expiry_trades=120, spread_coverage_pct=99.0,
                         expiry_coverage_pct=99.0, smallest_cell=45)
ok("gates can pass once the data exists", not g_full["failed"]
   and g_full["status"] == "COMPARISON_PERMITTED")
ok("an unmeasurable book fails the spread-coverage gate",
   "SPREAD_COVERAGE" in gates8.evaluate(
       resolved=500, sessions=25, expiry_sessions=8, expiry_trades=120,
       spread_coverage_pct=40.0, expiry_coverage_pct=99.0,
       smallest_cell=45)["failed"])

# --- VALIDATED is unreachable without a holdout ---------------------------
ok("VALIDATED needs gates AND an agreeing chronological holdout",
   findings.validated(500, 40, holdout_sessions=6, holdout_agrees=True)
   == findings.VALIDATED)
ok("a passing in-sample block alone is never VALIDATED",
   findings.validated(500, 40, holdout_sessions=0, holdout_agrees=True)
   == findings.HYPOTHESIS)
ok("a holdout that disagrees is never VALIDATED",
   findings.validated(500, 40, holdout_sessions=6, holdout_agrees=False)
   == findings.HYPOTHESIS)
ok("one session can never be VALIDATED",
   findings.validated(141, 1, holdout_sessions=6, holdout_agrees=True)
   == findings.HYPOTHESIS)

# --- no duplicate implementations ----------------------------------------
src = []
for name in ("calibration.py", "spread.py", "causes.py", "findings.py",
             "expiry.py", "capture.py", "continuation.py", "contract.py",
             "gates8.py"):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "app", "research", "phase8", name)) as fh:
        src.append(fh.read())
blob = "\n".join(src)
ok("phase 8 defines no exit policy of its own",
   "def apply_exit" not in blob and "def simulate" not in blob)
ok("phase 8 defines no path walk of its own",
   "def walk" not in blob and "first_target_ts is None" not in blob)
ok("phase 8 imports the phase 7 pipeline",
   "from app.research.phase7" in blob)

print()
if FAILS:
    print(f"PHASE 8 SMOKE FAILED ({len(FAILS)} of {CHECKS})")
    for f in FAILS:
        print(" -", f)
    raise SystemExit(1)
print(f"PHASE 8 SMOKE PASSED ({CHECKS} checks)")
