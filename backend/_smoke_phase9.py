"""Focused smoke for the Phase 9 research package. No network, no DB, no orders.

The tables rendering is not what matters. What matters is that Phase 9 cannot cheat
in the four specific ways this phase makes possible:

* **no future information in an entry-time score** — the ladder rows must be
  byte-identical when the snapshots *after* the signal are replaced with different
  prices. This is asserted directly rather than argued in prose;
* **no second implementation** — every candidate outcome comes from
  ``policies.simulate`` and every cost from ``phase8.spread.measure``, so a Phase 9
  number cannot disagree with the Phase 7/8 number for the same trade;
* **no in-sample filter reading as evidence** — the shadow qualifier must report
  IN_SAMPLE_ONLY without a holdout, must never earn VALIDATED, must leave the
  baseline population intact, and must print avoided losers beside missed winners;
* **no prediction** — every candidate object carries ``predicted_net_r = None``, and
  the empirical bucket mean is labelled in-sample.

Plus the ordinary Phase 8 discipline: one primary sub-cause per BAD_STRIKE loss from
a fixed precedence, small cells labelled REQUIRES MORE DATA, contaminated inputs
stamped rather than dropped, and no production module importing any of it.

    .venv/bin/python _smoke_phase9.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.research.phase7 import paths, policies  # noqa: E402
from app.research.phase7.dataset import REAL, ChainSeries  # noqa: E402
from app.research.phase8 import calibration, findings  # noqa: E402
from app.research.phase9 import (  # noqa: E402
    badstrike,
    candidates,
    entryq,
    findings9,
    gates9,
    shadow,
    strikes,
    tradability,
)

CHECKS = 0
FAILS: list[str] = []
HERE = os.path.dirname(os.path.abspath(__file__))


def ok(label: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILS.append(f"{label}: {detail}")
        print(f"FAIL {label} {detail}")
    else:
        print(f"ok   {label} {detail}")


START = 1_700_000_000
SPOT = 7800.0


def leg(symbol: str, strike: float, premium: float, *, side: str = "CE",
        spread_pct: float = 2.0, oi: float = 50_000.0, volume: float = 5_000.0,
        delta: float = 0.5, iv: float = 20.0) -> dict:
    half = premium * spread_pct / 200.0
    return {"symbol": symbol, "strike": strike, "option_type": side,
            "premium": premium, "bid": round(premium - half, 2),
            "ask": round(premium + half, 2), "iv": iv, "delta": delta,
            "gamma": 0.0, "theta": 0.0, "vega": 0.0, "oi": oi, "oi_change": 0.0,
            "volume": volume}


def sym(strike: float) -> str:
    return f"NIFTY{int(strike)}CE"


LADDER = (7700.0, 7750.0, 7800.0, 7850.0, 7900.0)
BASE_PREMIUM = {7700.0: 140.0, 7750.0: 105.0, 7800.0: 75.0, 7850.0: 48.0,
                7900.0: 18.0}
BASE_DELTA = {7700.0: 0.78, 7750.0: 0.64, 7800.0: 0.50, 7850.0: 0.33,
              7900.0: 0.14}


def chain(steps: int = 12, *, drift: float = 1.0, wide: float | None = None,
          thin_oi: float | None = None, future_drift: float | None = None,
          pre_run: bool = False) -> ChainSeries:
    """A five-strike CE ladder over ``steps`` snapshots.

    ``future_drift`` changes the premium path only AFTER the signal snapshot, which
    is what the leakage test needs. ``pre_run`` inflates the premium BEFORE the
    signal, which is what a past-only expansion measure must be able to see.
    """
    s = ChainSeries("NIFTY", REAL)
    signal_i = 3
    for i in range(steps):
        legs: dict[str, dict] = {}
        for k in LADDER:
            base = BASE_PREMIUM[k]
            if i <= signal_i:
                px = base * (1.4 if pre_run and i == signal_i else 1.0)
            else:
                mult = future_drift if future_drift is not None else drift
                px = base * (1.0 + (mult - 1.0) * (i - signal_i))
            sp = 2.0
            oi = 50_000.0
            if wide is not None and k == 7800.0:
                sp = wide
            if thin_oi is not None and k == 7800.0:
                oi = thin_oi
            legs[sym(k)] = leg(sym(k), k, round(px, 2), spread_pct=sp, oi=oi,
                               delta=BASE_DELTA[k])
        s.ts.append(START + 60 * i)
        s.legs.append(legs)
        s.raw.append(list(legs.values()))
    return s


def event(series: ChainSeries, strike: float = 7800.0, *, confidence: float = 80.0,
          atr: float = 40.0, chase_class: str = "IDEAL_ENTRY") -> paths.BuyEvent:
    ts = START + 60 * 3
    entry = float(series.legs[3][sym(strike)]["premium"])
    stop = entry * 0.9
    ev = paths.BuyEvent(
        instrument="NIFTY", ts=ts, bar=3, side="CE", symbol=sym(strike),
        strike=strike, spot=SPOT, entry=entry, stop=stop,
        target1=entry + 2.0 * (entry - stop), target2=entry, target3=entry,
        confidence=confidence, trade_score=80.0, regime="TREND_UP", atr=atr,
        leg_delta=BASE_DELTA[strike], vwap=SPOT - 10.0, ema9=SPOT - 5.0,
        ema20=SPOT - 20.0, prev_mid=SPOT - 8.0, breakout_level=SPOT - 1.0,
        chain_age_sec=5, data_flag="FRESH")
    ev.path = paths.walk(series, sym(strike), ts, entry, stop, ev.target1)
    ev.chase = paths.measure_chase(ev, series)
    ev.chase["class"] = chase_class
    ev.expiry = {"expiry": "2026-08-27", "days_to_expiry": 8,
                 "minutes_to_expiry": 4800, "expiry_class": "NON_EXPIRY",
                 "venue": "NFO"}
    ev.expiry_class = "NON_EXPIRY"
    return ev


# --- 1. the ladder itself -------------------------------------------------
s = chain()
ev = event(s)
rows = strikes.ladder(s, ev)
ok("ladder finds every same-side strike within +-3 steps",
   len(rows) == len(LADDER), f"{len(rows)} rows")
ok("ATM is the strike nearest spot, and steps are signed",
   next(r["steps_from_atm"] for r in rows if r["strike"] == 7800.0) == 0
   and next(r["steps_from_atm"] for r in rows if r["strike"] == 7900.0) == 2
   and next(r["steps_from_atm"] for r in rows if r["strike"] == 7700.0) == -2)
ok("the selected leg is marked exactly once",
   sum(1 for r in rows if r["is_selected"]) == 1)
ok("every candidate carries its recorded book",
   all(r["bid"] is not None and r["ask"] is not None for r in rows))
ok("premium bands follow the production floor at 20",
   strikes.premium_band(18.0) == "<₹20" and strikes.premium_band(20.0) == "₹20-50"
   and strikes.premium_band(300.0) == "₹200+")

# --- 2. NO future information in an entry-time score ----------------------
rows_up = strikes.ladder(chain(future_drift=1.5), event(chain(future_drift=1.5)))
rows_down = strikes.ladder(chain(future_drift=0.5), event(chain(future_drift=0.5)))


def scrub(xs: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items()} for r in xs]


ok("entry-time rows are identical whatever happens afterwards",
   scrub(rows_up) == scrub(rows_down),
   "a differing row would mean the score leaked the future")
ok("past-only expansion is visible when the premium ran BEFORE the signal",
   (next(r["past_expansion_pct"] for r in strikes.ladder(
       chain(pre_run=True), event(chain(pre_run=True)))
       if r["strike"] == 7800.0) or 0) > 30.0)
ok("past-only expansion is flat when the premium ran AFTER the signal",
   abs(next(r["past_expansion_pct"] for r in rows_up
            if r["strike"] == 7800.0) or 0.0) < 1e-9)

# --- 3. ranks are comparative, never absolute thresholds ------------------
wide = chain(wide=40.0)
wide_rows = strikes.ladder(wide, event(wide))
sel = next(r for r in wide_rows if r["strike"] == 7800.0)
other = next(r for r in wide_rows if r["strike"] == 7750.0)
ok("a wider book ranks worse on spread than a tighter one",
   sel["rank_spread_pct_of_premium"] < other["rank_spread_pct_of_premium"],
   f"{sel['rank_spread_pct_of_premium']} vs {other['rank_spread_pct_of_premium']}")
ok("the composite states its weighting is arbitrary",
   "ARBITRARY" in strikes.study(
       [strikes.per_signal(s, ev)], 1)["entry_time_scoring"]["composite"])
ok("spread is also expressed against the risk unit, not just the premium",
   sel["spread_share_of_risk_pct"] > sel["spread_pct_of_premium"])

# --- 4. candidate outcomes reuse phase 7/8, like-for-like -----------------
# Checked on a winner AND a loser, so the equality cannot pass by both sides being
# a flat zero.
for tag, mult in (("winner", 1.10), ("loser", 0.90)):
    s_m = chain(drift=mult)
    ev_m = event(s_m)
    res_m = strikes.outcomes(s_m, ev_m, strikes.ladder(s_m, ev_m))
    sel_m = next(r for r in res_m if r["is_selected"])
    own_m = policies.simulate(ev_m, "CONTROL", "A_BASELINE")
    ok(f"the selected candidate reproduces the phase 7 fill exactly ({tag})",
       abs(sel_m["realised_r"] - round(own_m.r, 3)) < 1e-9
       and abs(own_m.r) > 0.5,
       f"{sel_m['realised_r']} vs {round(own_m.r, 3)}")
    ok(f"a neighbouring strike is separately measurable ({tag})",
       sum(1 for r in res_m if r.get("measurable")) == len(LADDER),
       f"{sum(1 for r in res_m if r.get('measurable'))} of {len(LADDER)}")

res = strikes.outcomes(s, ev, rows)
ok("every candidate is priced net of the recorded book",
   all(r["net_r"] <= r["realised_r"] + 1e-9 for r in res if r.get("measurable")))
ok("candidate outcomes carry a spread status, never a silent zero",
   all(r["spread_status"] for r in res if r.get("measurable")))
sig = strikes.per_signal(s, ev)
ok("hindsight is kept in its own named block",
   "hindsight_best_net_symbol" in sig and "better_at_entry_time" in sig)

# --- 5. BAD_STRIKE decomposition -----------------------------------------
clean = next(r for r in rows if r["strike"] == 7800.0)
ok("a clean leg falls through to the residual divergence",
   badstrike.primary_sub(badstrike.sub_flags(clean))
   == "OPTION_UNDERLYING_DIVERGENCE")
ok("a wide book is BAD_SPREAD, not divergence",
   badstrike.primary_sub(badstrike.sub_flags(
       next(r for r in wide_rows if r["strike"] == 7800.0))) == "BAD_SPREAD")
thin = chain(thin_oi=10.0)
ok("an unopened contract is LOW_OI",
   badstrike.primary_sub(badstrike.sub_flags(
       next(r for r in strikes.ladder(thin, event(thin))
            if r["strike"] == 7800.0))) == "LOW_OI")
ok("a far-OTM low-delta leg is POOR_DELTA before WRONG_MONEYNESS",
   badstrike.primary_sub(badstrike.sub_flags(
       next(r for r in rows if r["strike"] == 7900.0))) == "POOR_DELTA")
ok("a degenerate-greek leg disqualifies the analysis first",
   badstrike.primary_sub(badstrike.sub_flags(
       {**clean, "greeks_usable": False})) == "DEGENERATE_GREEKS")
ok("a missing ladder row is OTHER, never a guess",
   badstrike.primary_sub(badstrike.sub_flags(None)) == "OTHER")
ok("exactly one primary sub-cause per loss",
   all(badstrike.primary_sub(badstrike.sub_flags(r)) in badstrike.SUB_CAUSES
       for r in rows))

cause_rows = [
    {"instrument": "NIFTY", "symbol": sym(7800.0), "side": "CE", "ts_ist": "t1",
     "primary_cause": "BAD_STRIKE", "outcome": "LOSS", "realised_r": -1.0,
     "net_r": -1.1, "mfe_r": 0.1, "expiry_class": "NON_EXPIRY",
     "underlying_move": {"favourable": 1.0}},
    {"instrument": "NIFTY", "symbol": sym(7800.0), "side": "CE", "ts_ist": "t2",
     "primary_cause": "WRONG_DIRECTION", "outcome": "LOSS", "realised_r": -1.0,
     "net_r": -1.1, "mfe_r": 0.0, "expiry_class": "NON_EXPIRY",
     "underlying_move": {"favourable": 0.0}},
    {"instrument": "NIFTY", "symbol": sym(7800.0), "side": "CE", "ts_ist": "t3",
     "primary_cause": "BAD_STRIKE", "outcome": "WIN", "realised_r": 1.0,
     "net_r": 0.9, "mfe_r": 1.2, "expiry_class": "NON_EXPIRY",
     "underlying_move": {"favourable": 1.0}},
]
decomposed = badstrike.decompose(cause_rows, {("NIFTY", sym(7800.0), "t1"): sig})
ok("only BAD_STRIKE losses are decomposed",
   len(decomposed) == 1 and decomposed[0]["ts_ist"] == "t1",
   f"{len(decomposed)} rows")
bad_study = badstrike.study(decomposed, 2, 1,
                            findings9.contamination(64.0, atr_dependent=True))
ok("a one-row sub-cause is REQUIRES MORE DATA, not a finding",
   bad_study["table"][0]["label"] == findings.NEEDS_DATA)
ok("the decomposition prints its own thresholds",
   bad_study["thresholds"]["spread_share_of_risk_pct"]
   == badstrike.SPREAD_SHARE_OF_RISK_PCT)
ok("the residual is reported as the only selector indictment",
   "residual_pct" in bad_study and "indicts contract selection" in
   bad_study["reading"])

# --- 6. contamination is labelled, never dropped -------------------------
taint = findings9.contamination(64.0, atr_dependent=True)
ok("ATR-derived blocks are stamped DATA_CONTAMINATED above the gate",
   taint["status"] == findings9.CONTAMINATED, taint["status"])
ok("book-only blocks are not contaminated by missing bars",
   findings9.contamination(64.0, atr_dependent=False)["status"] == "CLEAN_INPUTS")
ok("a clean series is not stamped",
   findings9.contamination(1.0, atr_dependent=True)["status"] == "CLEAN_INPUTS")

# --- 7. tradability ------------------------------------------------------
trade_rows = []
for i, (share, net) in enumerate((
        (2.0, 0.5), (3.0, 0.4), (4.0, -0.2), (30.0, -0.9), (160.0, -1.4),
        (180.0, -1.6))):
    trade_rows.append({
        "instrument": "GOLD" if share > 100 else "NIFTY",
        "symbol": f"S{i}", "ts_ist": f"t{i}", "session": "2026-08-18",
        "spread_share_of_risk_pct": share, "oi": 50_000.0, "volume": 5_000.0,
        "realised_r": net + 0.1, "net_r": net, "target_before_stop_hit": net > 0,
        "entry_quality": "IDEAL_ENTRY", "data_flag": "FRESH",
        "mfe_r": 0.5, "mae_r": -0.5, "spread_cost_r": 0.1, "room_ratio": 0.5,
        "capture": {"capture_pct": 50.0}, "confidence": 80.0, "regime": "TREND_UP",
        "expiry_class": "NON_EXPIRY", "strike": 7800.0, "side": "CE",
        "entry": 75.0, "held_min": 5.0,
    })
trad = tradability.study(trade_rows, 1, findings9.contamination(64.0,
                                                               atr_dependent=False))
ok("tradability is PROPOSED_ONLY", trad["status"].startswith("PROPOSED_ONLY"))
ok("thresholds come from the sample's own distribution",
   trad["proposal"]["derivable"] and "terciles" in trad["proposal"]["method"])
ok("a book wider than the stop is RED whatever the terciles say",
   tradability.classify({"spread_share_of_risk_pct": 160.0, "oi": 1e9,
                         "volume": 1e9}, trad["proposal"]) == "RED")
ok("a missing book is RED, never assumed cheap",
   tradability.classify({"spread_share_of_risk_pct": None}, trad["proposal"])
   == "RED")
ok("the instrument whose book exceeds its stop is named",
   trad["not_tradable_after_spread"] == ["GOLD"],
   str(trad["not_tradable_after_spread"]))
ok("the proposal warns against applying itself",
   "fit the filter to those sessions" in trad["proposal"]["warning"])

# --- 8. entry quality ----------------------------------------------------
eq_rows = []
for cls, r in (("IDEAL_ENTRY", 1.2), ("GOOD_ENTRY", 0.6),
               ("CHASED_ENTRY", -0.3), ("SEVERELY_CHASED", -1.0)):
    for _ in range(10):
        eq_rows.append({
            "instrument": "NIFTY", "symbol": "S", "side": "CE", "ts_ist": "t",
            "session": "2026-08-18", "expiry_class": "NON_EXPIRY",
            "entry_quality": cls, "signal_premium": 75.0,
            "best_entry_next_bars": 74.0, "actual_entry": 75.0,
            "improvement_available_pct": 1.0, "consumed_frac": 0.5,
            "premium_expansion_before_entry_pct": 5.0,
            "distance_from_vwap_pct": 0.1, "distance_from_vwap_in_atr": 0.25,
            "room_ratio": 0.5, "regime": "TREND_UP", "realised_r": r,
            "net_r": r - 0.05, "spread_cost_r": 0.05, "mfe_r": max(r, 0.2),
            "mae_r": -0.4, "mfe_capture_pct": 60.0,
            "target_before_stop_hit": r > 0, "held_min": 6.0,
        })
eq = entryq.study(eq_rows, 1, taint)
ok("entry classes are ordered worst-last when chasing costs money",
   eq["ordering_check"]["verdict"] == "ORDERED_AS_EXPECTED",
   eq["ordering_check"]["verdict"])
ok("net expectancy is never better than gross in a class",
   all(b["net_expectancy_r"] <= b["gross_expectancy_r"]
       for b in eq["by_class"].values() if b.get("n")))
ok("the entry study says the production chase guard is untouched",
   "not the production chase guard" in eq["class_cut_points"])
ok("entry classes are phase 7's, not redefined here",
   set(eq["classes"]) <= set(entryq.CLASSES))
eq_unk = entryq.study(eq_rows + [{**eq_rows[0], "entry_quality": "UNKNOWN",
                                  "net_r": -9.0, "realised_r": -9.0}] * 12, 1, taint)
ok("an unmeasurable entry cannot masquerade as the worst class",
   "UNKNOWN" not in eq_unk["ordering_check"]["comparable_classes"]
   and eq_unk["ordering_check"]["verdict"] == "ORDERED_AS_EXPECTED",
   str(eq_unk["ordering_check"]["comparable_classes"]))
ok("the unmeasurable share is reported, not dropped",
   eq_unk["unmeasurable_entry_quality"]["n"] == 12
   and "NOT a bad one" in eq_unk["unmeasurable_entry_quality"]["reading"])

# --- 9. calibration error -------------------------------------------------
def pairs_at(conf: float, n: int, win: bool) -> list[tuple]:
    out = []
    for _ in range(n):
        series_ = chain(drift=1.06 if win else 0.94)
        e = event(series_, confidence=conf)
        out.append((e, policies.simulate(e, "CONTROL", "A_BASELINE")))
    return out


perfect = pairs_at(100.0, 12, True) + pairs_at(0.0, 12, False)
err = calibration.error(perfect, 1)
ok("a perfectly calibrated score scores 0 on Brier and ECE",
   err["brier_score"] == 0.0 and err["expected_calibration_error"] == 0.0,
   f"{err['brier_score']} / {err['expected_calibration_error']}")
inverted = pairs_at(95.0, 12, False) + pairs_at(72.0, 12, True)
err_bad = calibration.error(inverted, 1)
ok("an inverted score is worse than a flat base-rate forecast",
   err_bad["beats_base_rate_forecast"] is False,
   f"{err_bad['brier_score']} vs {err_bad['brier_of_base_rate_forecast']}")
ok("calibration error refuses to call the score a probability",
   "is not a probability" in err_bad["reading"]
   and "confidence is read only" in err_bad["guarantee"])
ok("too few trades is unmeasurable, not zero error",
   calibration.error(pairs_at(80.0, 2, True), 1)["measurable"] is False)

# --- 10. shadow qualifier ------------------------------------------------
for r in trade_rows:
    r["phase9_qualification"] = shadow.qualify(
        r, {"spread_share_of_risk_pct": r["spread_share_of_risk_pct"],
            "oi": r["oi"], "volume": r["volume"], "greeks_usable": True,
            "delta": 0.5, "room_ratio": 0.5}, trad["proposal"])
ok("every trade gets exactly one qualification label",
   all(r["phase9_qualification"] in (shadow.QUALIFIED, *shadow.REASONS)
       for r in trade_rows))
ok("an untradable book is rejected for spread, not for the signal",
   [r["phase9_qualification"] for r in trade_rows if
    r["spread_share_of_risk_pct"] > 100] == ["REJECTED_SPREAD"] * 2)
ok("stale data is rejected as data",
   shadow.qualify({"data_flag": "STALE"}, None, trad["proposal"])
   == "REJECTED_DATA")
ok("a missing ladder row is UNKNOWN, never QUALIFIED",
   shadow.qualify({"data_flag": "FRESH"}, None, trad["proposal"]) == "UNKNOWN")
cmp_block = shadow.compare(trade_rows, ["2026-08-18", "2026-08-19"])
ok("without a holdout the comparison is IN_SAMPLE_ONLY",
   cmp_block["status"] == "IN_SAMPLE_ONLY", cmp_block["status"])
ok("the shadow comparison can never be VALIDATED on this sample",
   cmp_block["label"] != findings.VALIDATED, cmp_block["label"])
ok("the baseline population is left intact",
   cmp_block["baseline"]["n"] == len(trade_rows))
ok("avoided losers are printed with their caveat and beside missed winners",
   "NOT evidence" in cmp_block["avoided_losers"]["caveat"]
   and "n" in cmp_block["missed_winners"])
ok("the drawdown is disclaimed as not a portfolio drawdown",
   "NOT a portfolio drawdown" in cmp_block["baseline"]["drawdown_caveat"])
ok("the qualifier states it filters nothing",
   "filters nothing" in cmp_block["guarantee"])

# --- 11. candidate objects carry no prediction ---------------------------
cand = candidates.study(trade_rows, {}, {}, 1)
ok("every candidate object refuses to predict net R",
   all(c["predicted_net_r"] is None for c in cand["candidates"]))
ok("every candidate explains why there is no prediction",
   all("requires a model" in c["why_no_prediction"] for c in cand["candidates"]))
ok("the empirical bucket mean is labelled in-sample",
   all("NOT a prediction" in c["empirical_disclaimer"] for c in cand["candidates"]))
ok("components are exposed, not hidden inside a weight",
   any(c["entry_quality_components"] for c in cand["candidates"]))

# --- 12. gates -----------------------------------------------------------
g = gates9.evaluate(resolved=337, sessions=2, ladder_coverage_pct=100.0,
                    median_candidates=7.0, book_coverage_pct=100.0,
                    holdout_sessions=1, missing_bar_pct=64.0, smallest_cell=3)
ok("a thin sample is EXPLORATORY_ONLY", g["status"] == "EXPLORATORY_ONLY")
ok("the failing gates are named",
   {"REAL_SESSIONS", "CHRONOLOGICAL_HOLDOUT", "DATA_COMPLETENESS",
    "CELL_SIZE"} <= set(g["failed"]), str(g["failed"]))
ok("a 4-strike recorded window fails LADDER_DEPTH",
   "LADDER_DEPTH" in gates9.evaluate(
       resolved=337, sessions=2, ladder_coverage_pct=100.0, median_candidates=4.0,
       book_coverage_pct=100.0, holdout_sessions=1, missing_bar_pct=1.0,
       smallest_cell=50)["failed"])
ok("passing everything permits comparison and nothing more",
   gates9.evaluate(resolved=500, sessions=25, ladder_coverage_pct=99.0,
                   median_candidates=7.0, book_coverage_pct=99.0,
                   holdout_sessions=6, missing_bar_pct=1.0,
                   smallest_cell=40)["status"] == "COMPARISON_PERMITTED")

# --- 13. no duplicate implementations, no production import --------------
src = []
for name in ("strikes.py", "badstrike.py", "tradability.py", "entryq.py",
             "shadow.py", "candidates.py", "gates9.py", "findings9.py",
             "__init__.py"):
    with open(os.path.join(HERE, "app", "research", "phase9", name)) as fh:
        src.append(fh.read())
blob = "\n".join(src)
ok("phase 9 defines no exit policy of its own",
   "def apply_exit" not in blob and "def simulate" not in blob)
ok("phase 9 defines no path walk of its own",
   "def walk" not in blob and "def measure_chase" not in blob)
ok("phase 9 defines no second spread-cost implementation",
   "def measure(" not in blob)
ok("phase 9 reuses the phase 7 pipeline and the phase 8 cost model",
   "from app.research.phase7" in blob and "phase8" in blob)

offenders: list[str] = []
for root, _dirs, files in os.walk(os.path.join(HERE, "app")):
    if os.sep + "research" in root:
        continue
    for f in files:
        if not f.endswith(".py"):
            continue
        with open(os.path.join(root, f)) as fh:
            if "phase9" in fh.read():
                offenders.append(os.path.join(root, f))
ok("no production module imports phase 9", not offenders, str(offenders))
ok("phase 9 holds no order-placing code path",
   not any(t in blob for t in ("place_order", "placeOrder", "broker.place",
                               "trade_mode", "live_orders")))

print()
if FAILS:
    print(f"PHASE 9 SMOKE FAILED ({len(FAILS)} of {CHECKS})")
    for f in FAILS:
        print(" -", f)
    raise SystemExit(1)
print(f"PHASE 9 SMOKE PASSED ({CHECKS} checks)")
