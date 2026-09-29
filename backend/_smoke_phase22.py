#!/usr/bin/env python
"""Phase 22 smoke — the frozen setup, the board and the verdict.

Checks the things that would silently invalidate the study rather than crash it:
the definition's fingerprint is stable, the pool keeps refused candidates, the
holdout is chronological, a vehicle without a two-sided book can never be
selected, and the board never reports a fill it could not have got at the ask.
"""
from __future__ import annotations

import sys

from app.research.phase22 import (
    board,
    definition as defn,
    pool as pool_mod,
    study as study_mod,
    vehicles,
    verdict,
)

OK = 0
FAIL: list[str] = []


def check(name: str, cond: bool) -> None:
    global OK
    if cond:
        OK += 1
    else:
        FAIL.append(name)


def row(**kw) -> dict:
    base = {
        "instrument": "NIFTY", "session": "2026-01-01", "entry_ts": 1000,
        "side": "LONG", "htf_trend": "UP", "htf_strength": 70.0,
        "regime": "TRENDING", "entry_trigger": "PULLBACK", "trap_prob": 5.0,
        "fake_prob": 5.0, "conviction_meter": 80.0, "risk": 20.0,
        "reward_risk": 2.0, "noise_points": 10.0, "signal": "BUY",
        "exit_reason": "TARGET", "r": 2.0, "minutes": 10, "mfe_r": 2.0,
        "mae_r": -0.3,
    }
    base.update(kw)
    return base


# ---- definition -----------------------------------------------------------
check("fingerprint stable", defn.fingerprint() == defn.fingerprint())
check("fingerprint 16 hex", len(defn.fingerprint()) == 16)
check("setup accepted", defn.is_setup(row()))
check("trend must agree", not defn.is_setup(row(side="SHORT")))
check("choppy refused", "STRUCTURE" in defn.refusals(row(regime="CHOPPY")))
check("breakout refused", "PULLBACK" in defn.refusals(row(entry_trigger="BREAKOUT")))
check("trap refused", "CONTINUATION" in defn.refusals(row(trap_prob=60.0)))
check("thin move refused",
      "MOVEMENT" in defn.refusals(row(risk=2.0, reward_risk=1.6,
                                      noise_points=10.0)))
check("poor rr refused", "ROOM" in defn.refusals(row(reward_risk=1.0)))
check("label", defn.label(row()) == defn.LABEL
      and defn.label(row(regime="CHOPPY")) == defn.OTHER)

# ---- pool -----------------------------------------------------------------
rows = [row(session=f"2026-01-{d:02d}", entry_ts=1000 + d, signal="WAIT",
            r=1.0 + (d % 3) - 1.0)
        for d in range(1, 21)]
rows += [row(session=f"2026-01-{d:02d}", entry_ts=2000 + d, regime="CHOPPY",
             r=-0.5) for d in range(1, 21)]
labelled = pool_mod.label_rows(rows)
check("refused candidates kept",
      len(labelled) == len(rows)
      and sum(1 for r in labelled
              if r["p22_engine"] == pool_mod.ENGINE_REFUSED) == 20)
check("source rows not mutated", all("p22_label" not in r for r in rows))
check("split", len(pool_mod.split(labelled)[defn.LABEL]) == 20)
check("families", pool_mod.family("NIFTY") == pool_mod.INDEX
      and pool_mod.family("CRUDEOIL") == pool_mod.MCX
      and pool_mod.family("RELIANCE") == pool_mod.EQUITY)
check("census names every condition",
      [c["condition"] for c in pool_mod.refusal_census(labelled)]
      == list(defn.CONDITIONS))
dup = pool_mod.load({"in_sample": rows[:5], "trades": rows[:5]})
check("load de-duplicates", len(dup) == 5)
check("load keeps chronology",
      [r["session"] for r in pool_mod.load(rows)]
      == sorted(r["session"] for r in rows))

# ---- study ----------------------------------------------------------------
parts = study_mod.periods(labelled)
dev_last = parts["spans"]["development"]["last"]
hold_first = parts["spans"]["holdout"]["first"]
check("holdout is chronological", dev_last < hold_first)
check("no session spans two periods",
      not (set(pool_mod.sessions(parts["development"]))
           & set(pool_mod.sessions(parts["holdout"]))))
summary = study_mod.summarise(pool_mod.split(labelled)[defn.LABEL])
check("thin cohort not measurable", summary["measurable"] is False)
check("gross labelled", summary["cost_basis"] == "GROSS")
costed = study_mod.summarise(pool_mod.split(labelled)[defn.LABEL], cost_r=0.5)
check("cost band labelled", costed["cost_basis"] == "ASSUMED_COST_BAND")
check("cost lowers expectancy",
      costed["expectancy_r"] < summary["expectancy_r"])
result = study_mod.run(labelled)
check("study reports every period",
      set(result["periods"]) == {"development", "validation", "holdout"})
check("stop bands honest without paths",
      result["stop_bands"]["with_bands"] == 0
      and "note" in result["stop_bands"])

# ---- vehicles -------------------------------------------------------------
cap = {
    "session": "2026-01-01", "signal_ts": 1000, "instrument": "NIFTY",
    "direction": "LONG",
    "entrants": [
        {"vehicle": "CALL", "net_r": 0.8, "book_source": "RECORDED_BOOK",
         "cost_treatment": vehicles.ASK_IN_BID_OUT},
        {"vehicle": "PUT", "net_r": -1.0, "book_source": "RECORDED_BOOK",
         "cost_treatment": vehicles.ASK_IN_BID_OUT},
        {"vehicle": "FUTURES", "net_r": 5.0, "book_source": "UNAVAILABLE",
         "cost_treatment": vehicles.MODELLED_NO_DEPTH},
    ],
}
decision = vehicles.choose(cap)
check("measured option beats unmeasured futures", decision["vehicle"] == "CALL")
check("unmeasured futures never measured",
      [e["status"] for e in decision["entrants"] if e["vehicle"] == "FUTURES"]
      == [vehicles.UNMEASURED])
negative = dict(cap, entrants=[dict(cap["entrants"][0], net_r=-0.2),
                               cap["entrants"][2]])
check("no positive measured option -> NO_TRADE",
      vehicles.choose(negative)["vehicle"] == vehicles.NO_TRADE)
check("futures disclosed on NO_TRADE",
      any(vehicles.MODELLED_NO_DEPTH in r
          for r in vehicles.choose(negative)["reasons"]))
joined = vehicles.join(labelled, [cap])
check("join reports coverage", joined[0]["joined"] is True)
far = dict(cap, signal_ts=1000 + 10 * vehicles.JOIN_TOLERANCE_SEC)
check("distant capture not joined", vehicles.join(labelled, [far])[0]["joined"]
      is False)
vehicle_run = vehicles.run(labelled, [cap])
check("mid never used", vehicle_run["selected"]["mid_used_anywhere"] is False)
check("coverage stated",
      vehicle_run["coverage"]["captured_opportunities"] == 1)

# ---- verdict --------------------------------------------------------------
thin = verdict.assess(result, None)
check("thin study cannot survive", thin["verdict"] != verdict.SURVIVES)
check("paper only", thin["execution"] == "PAPER_ONLY")
check("verdict is one of three",
      thin["verdict"] in (verdict.SURVIVES, verdict.FAILS, verdict.THIN))
check("no vehicle capture is UNKNOWN not PASS",
      next(c for c in thin["checks"] if c["condition"] == "REAL_BID_ASK")
      ["state"] == verdict.UNKNOWN)

# ---- board ----------------------------------------------------------------
from app.state import AppState  # noqa: E402

st = AppState("NIFTY")
snap = st.tick()
live = st.provider.option_chain()
dec = snap.decision
candles = st.provider.futures_candles(60)
board.reset()
out = board.observe("NIFTY", dec, "TRENDING", candles, snap.futures_price,
                    live, 75)
check("board row has a status", out["status"] in board.STATUSES)
check("board row is paper only",
      out["paper_only"] is True and out["no_real_order"] is True)
check("board says which conditions refused",
      isinstance(out["setup_refusals"], list))
check("board carries the definition", out["definition"] == defn.VERSION)

# both option sides must reach the board even when only one is recommended:
# the opposite side is quoted at the nearest strike, never omitted.
check("nearest finds the side the engine did not recommend",
      board.nearest(live, "CE", dec.strike or dec.atm_strike) is not None
      and board.nearest(live, "PE", dec.strike or dec.atm_strike) is not None)
vset = board.vehicle_set("NIFTY", dec, live, 75, board.LONG,
                         {"bid": 100.0, "ask": 100.5})
check("CE, PE and FUTURES are all visible",
      [v["vehicle"] for v in vset] == ["CE", "PE", "FUTURES"])

shape = {"stop_pct": 0.3, "t1_pct": 0.5, "t2_pct": 0.9, "t3_pct": 1.4}
quote = live[0]
no_book = quote.model_copy(update={"bid": None, "ask": None})
check("no book -> unmeasured",
      board._vehicle("CE", no_book.symbol, no_book, dec, 75, shape=shape,
                     own_plan=False)["status"] == board.UNMEASURED)
crossed = quote.model_copy(update={"bid": 10.0, "ask": 9.0})
check("crossed book rejected",
      board._vehicle("CE", crossed.symbol, crossed, dec, 75, shape=shape,
                     own_plan=False)["status"] == board.UNMEASURED)
good = quote.model_copy(update={"bid": 99.0, "ask": 101.0})
priced = board._vehicle("CE", good.symbol, good, dec, 75, shape=shape,
                        own_plan=False)
check("entry is the ask", priced["entry"] == 101.0)
check("spread measured", priced["spread"] == 2.0)
check("cost charged", (priced.get("cost_points") or 0) >= 2.0)
check("alternate levels are labelled as transferred, not as the plan",
      priced["levels_basis"] == "PLAN_SHAPE_APPLIED_TO_OWN_ASK"
      and priced["stop"] == 70.7 and priced["target1"] == 151.5)

# a plan with no premium or no stop cannot be transferred to another strike
no_shape = dec.model_copy(update={"stop_loss": None})
check("no plan shape when the plan has no stop",
      board.plan_shape(no_shape) is None)
check("untransferable plan is refused, not guessed",
      "NO_TRANSFERABLE_PLAN" in board._vehicle(
          "PE", good.symbol, good, dec, 75, shape=None, own_plan=False)["reasons"])

# the side that cannot express the direction is priced AND refused, so the
# board can say why rather than leaving the comparison blank
wrong = board._vehicle("PE", good.symbol, good, dec, 75, shape=shape,
                       own_plan=False, wrong_side=True)
check("wrong side priced but refused",
      wrong["status"] == board.MEASURED
      and "WRONG_VEHICLE_FOR_DIRECTION" in wrong["reasons"])

# ---- futures as a real competitor ----------------------------------------
plan = dec.model_copy(update={"underlying_stop": 24900.0,
                              "expected_move_points": 120.0})
fut = board.futures_vehicle("NIFTY", {"tradingsymbol": "NIFTYFUT",
                                      "bid": 24999.0, "ask": 25001.0},
                            plan, board.LONG, 75)
check("quoted futures book is MEASURED", fut["status"] == board.MEASURED)
check("futures entered on the executable side", fut["entry"] == 25001.0)
check("futures charged its own costs",
      isinstance(fut["cost_points"], (int, float)) and fut["cost_points"] > 0)
short = board.futures_vehicle("NIFTY", {"bid": 24999.0, "ask": 25001.0},
                              plan.model_copy(update={"underlying_stop":
                                                      25100.0}),
                              board.SHORT, 75)
check("a short sells the bid and targets below it",
      short["entry"] == 24999.0 and short["target1"] < short["entry"])
nodepth = board.futures_vehicle("NIFTY", {"ltp": 25000.0}, plan,
                                board.LONG, 75)
check("futures without depth stays UNMEASURED",
      nodepth["status"] == board.UNMEASURED
      and "FUTURES_DEPTH_NOT_CAPTURED" in nodepth["reasons"])
check("an unmeasured futures leg can never be selected",
      board.choose([fut, nodepth])[0] is fut)

# vehicles are ranked in one unit: room after cost per unit of their own risk
check("ranking is unit-free",
      board.net_rr({"room_after_cost": 30.0, "risk_points": 10.0}) == 3.0
      and board.net_rr({"room_after_cost": 30.0, "risk_points": 0.0}) is None)
best, _ = board.choose([
    {"vehicle": "CE", "status": board.MEASURED, "reasons": [],
     "room_after_cost": 40.0, "risk_points": 40.0},     # 1.0R
    {"vehicle": "FUTURES", "status": board.MEASURED, "reasons": [],
     "room_after_cost": 90.0, "risk_points": 30.0},     # 3.0R
])
check("futures wins when its net reward:risk is better",
      best["vehicle"] == "FUTURES")
check("a measured leg whose cost eats the room is not viable",
      board.choose([{"vehicle": "CE", "status": board.MEASURED, "reasons": [],
                     "room_after_cost": -1.0, "risk_points": 10.0}])[0] is None)

best, reasons = board.choose([
    {"vehicle": "CE", "status": board.UNMEASURED, "reasons": ["NO_TWO_SIDED_BOOK"]},
    {"vehicle": "FUTURES", "status": board.UNMEASURED,
     "reasons": ["FUTURES_DEPTH_NOT_CAPTURED"]},
])
check("nothing measured -> NO_TRADE", best is None and reasons)
payload = board.board()
check("board payload is paper only", payload["paper_only"] is True)
check("NO_TRADE is first class", isinstance(payload["no_trade"], bool))
check("fingerprint on the board",
      payload["definition_fingerprint"] == defn.fingerprint())
board.reset()

print(f"\n{OK} checks passed, {len(FAIL)} failed")
for name in FAIL:
    print(f"  FAILED: {name}")
if FAIL:
    sys.exit(1)
print(f"ALL {OK} PHASE 22 CHECKS PASSED")
