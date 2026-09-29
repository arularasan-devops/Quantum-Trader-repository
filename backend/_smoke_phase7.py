"""Focused smoke for the Phase 7 research package. No network, no DB, no orders.

What is worth proving here is not "the numbers look nice" — it is that the study
cannot cheat:

* provenance is decided per snapshot and never guessed;
* a policy cannot see a price before the timestamp it acts on (no lookahead);
* every exit policy keeps the production stop and can only exit EARLIER;
* TARGET_FIRST / STOP_FIRST is decided by timestamp order, not by magnitude;
* the chase cut points come from the data, not from constants;
* the acceptance gates refuse conclusions while the sample is thin.

    .venv/bin/python _smoke_phase7.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.research.phase7 import attribution, gates, paths, policies, quality  # noqa: E402
from app.research.phase7.dataset import (  # noqa: E402
    REAL,
    SIM,
    UNKNOWN,
    ChainSeries,
    load_chains,
    provenance,
)

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


def _leg(symbol: str, premium: float, oi_change: int = 0) -> dict:
    return {"symbol": symbol, "strike": 7800.0, "option_type": "CE",
            "premium": premium, "iv": 20.0, "delta": 0.5, "gamma": 0.0,
            "theta": 0.0, "vega": 0.0, "oi": 100, "oi_change": oi_change,
            "volume": 10}


def series_from(prices: list[float], start: int = 1_700_000_000,
                symbol: str = "X7800CE") -> ChainSeries:
    s = ChainSeries("TEST", REAL)
    for i, px in enumerate(prices):
        ts = start + 60 * i
        s.ts.append(ts)
        s.legs.append({symbol: _leg(symbol, px)})
        s.raw.append([_leg(symbol, px)])
    return s


def event(entry: float, stop: float, target: float, prices: list[float],
          start: int = 1_700_000_000) -> paths.BuyEvent:
    s = series_from([entry] + prices, start)
    ev = paths.BuyEvent(
        instrument="TEST", ts=start, bar=0, side="CE", symbol="X7800CE",
        strike=7800.0, spot=7800.0, entry=entry, stop=stop, target1=target,
        target2=target, target3=target, confidence=80.0, trade_score=80.0,
        regime="TREND_UP", atr=10.0, leg_delta=0.5, vwap=7790.0, ema9=7795.0,
        ema20=7780.0, prev_mid=7792.0, breakout_level=7799.0,
        chain_age_sec=5, data_flag="FRESH")
    ev.path = paths.walk(s, "X7800CE", start, entry, stop, target)
    return ev


# --- provenance is per snapshot -------------------------------------------
ok("provenance real", provenance(json.dumps([_leg("A", 10.0, 0)])) == REAL)
ok("provenance sim", provenance(json.dumps([_leg("A", 10.0, 7)])) == SIM)
ok("provenance mixed payload is sim",
   provenance(json.dumps([_leg("A", 10.0, 0), _leg("B", 11.0, 3)])) == SIM)
ok("provenance empty is unknown", provenance("[]") == UNKNOWN)
ok("provenance garbage is unknown", provenance("{oops") == UNKNOWN)

db_path = os.path.join(tempfile.mkdtemp(prefix="qt-p7-smoke-"), "history.db")
db = sqlite3.connect(db_path)
db.execute("CREATE TABLE chain_snapshots (instrument TEXT, ts INTEGER, payload TEXT)")
db.executemany(
    "INSERT INTO chain_snapshots VALUES (?,?,?)",
    [("TEST", 1, json.dumps([_leg("A", 10.0, 0)])),
     ("TEST", 61, json.dumps([_leg("A", 11.0, 5)])),
     ("TEST", 121, "[]")])
db.commit()
db.close()
loaded = load_chains(db_path, "TEST")
ok("loader keeps populations apart",
   (len(loaded[REAL]), len(loaded[SIM]), len(loaded[UNKNOWN])) == (1, 1, 1),
   str({k: len(v) for k, v in loaded.items()}))

# --- Part 1/2 audits ------------------------------------------------------
audit = quality.chain_audit(loaded)
ok("audit reports no spread", audit["verdict"]["spread_measurable"] is False)
ok("audit flags single session", audit["verdict"]["single_session_only"] is True)
ok("audit counts unknown separately",
   audit["by_provenance"][UNKNOWN] == 1, str(audit["by_provenance"]))

# --- path outcome is decided by TIME, not by size -------------------------
# Stop is touched first, then a much larger target excursion follows. A study
# that compared magnitudes would call this a winner.
ev = event(100.0, 90.0, 110.0, [95.0, 89.0, 130.0])
ok("stop before target => STOP_FIRST", ev.path.outcome == "STOP_FIRST",
   ev.path.outcome)
ok("mfe/mae still recorded on the full path",
   ev.path.mfe_r > 2.9 and ev.path.mae_r < -1.0,
   f"mfe={ev.path.mfe_r:.2f} mae={ev.path.mae_r:.2f}")

ev2 = event(100.0, 90.0, 110.0, [105.0, 112.0, 80.0])
ok("target before stop => TARGET_FIRST", ev2.path.outcome == "TARGET_FIRST",
   ev2.path.outcome)

# --- every exit policy keeps the stop -------------------------------------
# The series gaps from 92 straight to 88 through the 90 stop. On a 60s snapshot
# series that IS what a stop exit looks like: it fills at the next observed quote,
# so the realised loss is worse than 1R. What must hold for every policy is that
# the stop is honoured at the first quote through it and nothing is held past it.
loser = event(100.0, 90.0, 110.0, [98.0, 92.0, 88.0, 70.0, 60.0])
for name in policies.EXIT_POLICIES:
    fill = policies.simulate(loser, "CONTROL", name)
    ok(f"stop honoured under {name}",
       fill.exit_reason == "STOP" and fill.exit == 88.0,
       f"exit={fill.exit:.2f} reason={fill.exit_reason}")
    ok(f"{name} does not hold past the stop touch", fill.r <= -1.0 and fill.r > -1.5,
       f"r={fill.r:.3f} (worse than -1R because the fill is the next snapshot)")

# --- exit policies may only leave EARLIER than the baseline ---------------
winner = event(100.0, 90.0, 115.0, [104.0, 108.0, 112.0, 106.0, 101.0, 99.0])
base = policies.simulate(winner, "CONTROL", "A_BASELINE")
for name in policies.EXIT_POLICIES:
    fill = policies.simulate(winner, "CONTROL", name)
    ok(f"{name} exits no later than baseline",
       fill.exit_ts <= base.exit_ts or base.exit_reason == "HORIZON_END",
       f"{fill.exit_reason}@{fill.exit_ts} vs {base.exit_reason}@{base.exit_ts}")

# --- no lookahead: a policy acting at bar k uses only quotes >= k ---------
late = policies.apply_exit(winner.path, 100.0, winner.path.quotes[3][0], 90.0,
                           115.0, "C_TRAIL_30")
ok("exit walk starts at the entry timestamp",
   late[1] >= winner.path.quotes[3][0], str(late))

# --- entry policies: a limit that is never reached is a MISS, not a fill --
nodip = event(100.0, 90.0, 110.0, [101.0, 103.0, 106.0, 109.0, 112.0])
entered, price, _, _ = policies.apply_entry(nodip, "PULLBACK")
ok("pullback that never comes is a miss", entered is False)
dip = event(100.0, 90.0, 110.0, [97.0, 103.0, 106.0])
entered, price, _, delay = policies.apply_entry(dip, "PULLBACK")
ok("pullback fills at the better price", entered and price < dip.entry,
   f"{price}")
ok("pullback records its delay", delay >= 1, str(delay))
ok("control always fills at the signal price",
   policies.apply_entry(dip, "CONTROL")[1] == dip.entry)

# --- Part 5: cut points are measured, not constants -----------------------
evs = [event(100.0, 90.0, 110.0, [100.0 - i, 100.0 + i, 100.0 + 2 * i])
       for i in range(1, 11)]
for e in evs:
    e.chase = paths.measure_chase(e, series_from([e.entry]))
cuts = paths.chase_thresholds(evs)
ok("chase cuts derived from the sample", cuts["n"] == len(evs) and cuts["cuts"],
   str(cuts.get("cuts")))
ok("chase cuts are ordered",
   cuts["cuts"]["ideal"] <= cuts["cuts"]["good"] <= cuts["cuts"]["acceptable"]
   <= cuts["cuts"]["chased"], str(cuts["cuts"]))
ok("no cuts => UNKNOWN class, never a default label",
   paths.classify_chase(0.5, None) == "UNKNOWN")
ok("empty sample yields no cuts", paths.chase_thresholds([])["cuts"] is None)

# --- Part 7: capture -------------------------------------------------------
cap = paths.mfe_capture(0.5, 2.0)
ok("capture percentage", cap["capture_pct"] == 25.0, str(cap))
ok("give-back in R", cap["giveback_r"] == 1.5, str(cap))
ok("nothing offered => no capture number",
   paths.mfe_capture(-1.0, 0.0)["capture_pct"] is None)

# --- Part 22: causes are exclusive and ordered ----------------------------
ok("never moved => SIGNAL_WRONG",
   attribution.classify_loss(0.1, -1.0, False, 0.0, -1.0, -1.1) == "SIGNAL_WRONG")
ok("stopped then target => STOP_TOO_TIGHT",
   attribution.classify_loss(0.9, -1.0, True, 0.0, -1.0, -1.1) == "STOP_TOO_TIGHT")
ok("1R offered and lost => EXIT_TOO_LATE",
   attribution.classify_loss(1.8, -0.9, False, 0.0, -0.9, -1.0) == "EXIT_TOO_LATE")
ok("chased entry classified",
   attribution.classify_loss(0.6, -0.4, False, 4.0, -0.4, -0.5) == "ENTRY_CHASED")
ok("cost-only loss classified",
   attribution.classify_loss(0.6, 0.0, False, 0.0, 0.2, -0.1) == "COSTS")

split = attribution.signal_vs_execution([
    {"mfe_r": 2.0, "realised_r": -1.0}, {"mfe_r": 1.5, "realised_r": 0.2}])
ok("signal vs execution split computed",
   split["r_offered_total"] == 3.5 and split["capture_pct"] is not None, str(split))

# --- Part 23: sizes must not leak into the comparison --------------------
manual = {"A": attribution.Leg("A", 98.0, 103.0, lots=20, lot_size=100)}
bot = {"A": attribution.Leg("A", 100.0, 91.5, lots=1, lot_size=100)}
cmp_big = attribution.compare_fills(manual, bot)
manual_small = {"A": attribution.Leg("A", 98.0, 103.0, lots=1, lot_size=100)}
cmp_small = attribution.compare_fills(manual_small, bot)
ok("comparison is size-invariant",
   cmp_big["rows"][0]["gap_r"] == cmp_small["rows"][0]["gap_r"],
   f"{cmp_big['rows'][0]['gap_r']} vs {cmp_small['rows'][0]['gap_r']}")
ok("entry/exit attribution split present",
   set(cmp_big["attribution_pct"]) == {"entry_timing", "exit_timing"})
ok("unshared legs are not compared",
   attribution.compare_fills({"A": manual["A"]}, {"B": bot["A"]})["shared_legs"] == 0)

# --- Part 24: the gates must refuse ---------------------------------------
thin = gates.evaluate(
    {"REAL_BROKER": {"sessions": 1}, "verdict": {"spread_measurable": False}},
    {"missing_pct": 38.0}, 12, 3, 2)
ok("thin sample is EXPLORATORY_ONLY", thin["status"] == "EXPLORATORY_ONLY")
ok("thin sample permits no conclusion", thin["conclusions_permitted"] is False)
ok("every failure names what it forbids",
   all(g["forbids"] for g in thin["gates"] if not g["pass"]))
rich = gates.evaluate(
    {"REAL_BROKER": {"sessions": 40}, "verdict": {"spread_measurable": True}},
    {"missing_pct": 1.0}, 500, 60, 40)
ok("a qualifying sample reaches READY_FOR_VALIDATION",
   rich["status"] == "READY_FOR_VALIDATION" and rich["conclusions_permitted"])
ok("even then it is not a deploy signal",
   "not for direct deployment" in rich["meaning"])

# --- summarise: metrics survive an empty population ----------------------
empty = policies.summarise([], 0)
ok("empty study does not fabricate an expectancy",
   empty["expectancy_r"] is None and empty["profit_factor"] is None)

print()
if FAILS:
    print(f"PHASE 7 SMOKE FAILED ({len(FAILS)} of {CHECKS})")
    for f in FAILS:
        print(f" - {f}")
    raise SystemExit(1)
print(f"PHASE 7 SMOKE PASSED ({CHECKS} checks)")
