"""Smoke test for the Flow entry-economics gate and the 0.5R shadow exit.

Run: .venv/bin/python _smoke_flow_economics.py

Covers the four things that decide whether these two changes are safe:
  * a leg whose expected move beats its round trip is ECONOMIC and still BUYs;
  * a leg whose move cannot pay the round trip is refused and held at WAIT;
  * a leg that cannot be MEASURED is never refused on a guess;
  * the shadow trail exits on a half-allowance giveback, never before it arms,
    and leaves the live rule's own exit untouched.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile

from app.analysis import option_costs
from app.config import settings
from app.execution import flow, flow_economics, flow_shadow, flow_tracker
from app.models import Candle, IndicatorSnapshot, OptionQuote, OptionType

CHECKS = 0


def ok(cond: bool, what: str) -> None:
    global CHECKS
    assert cond, what
    CHECKS += 1


def mk(o: float, c: float, t: int, span: float = 0.5) -> Candle:
    return Candle(time=t, open=o, high=max(o, c) + span, low=min(o, c) - span,
                  close=c, volume=1000.0)


def quote(premium: float, delta: float = 0.5, *, put: bool = False,
          bid: float | None = None, ask: float | None = None) -> OptionQuote:
    return OptionQuote(
        symbol="X100PE" if put else "X100CE", strike=100.0,
        option_type=OptionType.PUT if put else OptionType.CALL,
        premium=premium, iv=0.2, delta=-delta if put else delta, gamma=0.01,
        theta=-1.0, vega=1.0, oi=1000, oi_change=10, volume=500,
        bid=bid, ask=ask,
    )


snap = IndicatorSnapshot(vwap=99.0, atr=1.0)
# A rising tape of decisive green candles: the fresh-BUY fixture the Flow smoke
# uses, so any state change here is the economics gate and nothing else.
up = [mk(100 + i, 100 + i + 0.9, 1000 + i * 60) for i in range(8)]
# A wide tape: 40-point candles, so a 0.5-delta leg expects ~20 points of move.
wide = [mk(100 + i * 40, 100 + i * 40 + 30, 1000 + i * 60, span=5.0)
        for i in range(8)]

# --- the two measured terms -------------------------------------------------
rng = flow_economics.typical_range(up, 10)
ok(rng is not None and abs(rng - 1.9) < 1e-6,
   "the range term is the median candle range, measured not assumed")
ok(flow_economics.typical_range([], 10) is None,
   "no candles is an absent range, not a zero range")
ok(flow_economics.typical_range(
    [Candle(time=1, open=1, high=1, low=1, close=1, volume=0)], 10) is None,
   "a zero-range candle cannot stand in for a measurement")

econ = flow_economics.assess("NIFTY", quote(200.0), wide)
ok(econ["verdict"] == flow_economics.OK,
   "a 20-point expected move against a ~2-point round trip is economic")
ok(econ["cost_spread_source"] == option_costs.ASSUMED,
   "with no book quoted the spread is labelled as the family median it is")
ok(econ["ratio"] is not None
   and abs(econ["ratio"] - econ["expected_move_points"] / econ["cost_points"]) < 0.02,
   "the ratio is expected move over cost, both in premium points")

# A real book replaces the assumption, and a tight one lowers the cost.
priced = flow_economics.assess("NIFTY", quote(200.0, bid=199.9, ask=200.1), wide)
ok(priced["cost_spread_source"] == option_costs.MEASURED,
   "a quoted bid/ask is used and reported as measured")
ok(priced["cost_points"] < econ["cost_points"],
   "a 0.2-point quoted book costs less than the assumed family spread")
crossed = flow_economics.assess("NIFTY", quote(200.0, bid=200.5, ask=200.1), wide)
ok(crossed["cost_spread_source"] == option_costs.ASSUMED,
   "a crossed book is unusable and must not become a cheap measured spread")
zero = flow_economics.assess("NIFTY", quote(200.0, bid=0.0, ask=0.0), wide)
ok(zero["cost_spread_source"] == option_costs.ASSUMED,
   "a zero book is a missing measurement, never a free spread")

# --- refusal, and the boundary ---------------------------------------------
thin = flow_economics.assess("NIFTY", quote(6.0), up)
ok(thin["verdict"] == flow_economics.REFUSED,
   "a 1-point expected move cannot pay a round trip several times its size")
ok(thin["cost_pct_of_premium"] is not None and thin["cost_pct_of_premium"] > 10.0,
   "the refused leg is the >10%-of-premium cohort the cost audit found")

boundary = dict(econ)
ok(econ["ratio"] >= settings.flow_cost_multiple,
   "the economic fixture clears the configured multiple")
ok(boundary["required_multiple"] == round(float(settings.flow_cost_multiple), 2),
   "the report states the multiple it was judged against")

# Exactly at the multiple must PASS: the rule is "at least", so a leg sitting on
# the boundary is not refused by a rounding accident.
saved = settings.flow_cost_multiple
try:
    settings.flow_cost_multiple = econ["ratio"]
    ok(flow_economics.assess("NIFTY", quote(200.0), wide)["verdict"]
       == flow_economics.OK,
       "a leg exactly at the required multiple is economic, not refused")
finally:
    settings.flow_cost_multiple = saved

# --- unmeasurable never refuses -------------------------------------------
no_delta = flow_economics.assess("NIFTY", quote(6.0, delta=0.0), up)
ok(no_delta["verdict"] == flow_economics.UNMEASURED,
   "a placeholder zero delta is an absent reading, not a reason to refuse")
warmup = flow_economics.assess("NIFTY", quote(6.0), [])
ok(warmup["verdict"] == flow_economics.UNMEASURED,
   "with no candles yet there is no range to compare the cost against")
unnamed = flow_economics.assess("", quote(6.0), up)
ok(unnamed["verdict"] == flow_economics.UNMEASURED,
   "an unnamed instrument has no lot size, so brokerage cannot be per-point")
ok(unnamed["cost_points"] is None,
   "an uncostable leg reports no cost rather than a fabricated one")

# --- the gate inside the state machine ------------------------------------
cheap_chain = [quote(6.0), quote(6.0, put=True)]
rich_chain = [quote(200.0), quote(200.0, put=True)]
fresh_rich = flow.detect(wide, rich_chain, snap, 380.0, None, instrument="NIFTY")
ok(fresh_rich.state == "BUY" and fresh_rich.side == OptionType.CALL,
   "an economic leg still takes the BUY the engine would have taken")
ok(fresh_rich.economics_verdict == flow_economics.OK,
   "the advisory carries the verdict it was judged on")

fresh_cheap = flow.detect(up, cheap_chain, snap, 107.0, None, instrument="NIFTY")
ok(fresh_cheap.state == "WAIT",
   "green flow on a leg that cannot pay for itself is held at WAIT")
ok(fresh_cheap.economics_verdict == flow_economics.REFUSED
   and "cannot pay for itself" in " ".join(fresh_cheap.reasons),
   "the refusal says which leg was refused and why, in its own numbers")

unnamed_sig = flow.detect(up, cheap_chain, snap, 107.0, None)
ok(unnamed_sig.state == "BUY",
   "a caller that names no instrument keeps its old behaviour exactly")

off = settings.flow_min_economics_enabled
try:
    settings.flow_min_economics_enabled = False
    ok(flow.detect(up, cheap_chain, snap, 107.0, None,
                   instrument="NIFTY").state == "BUY",
       "the gate is reversible: off restores the pre-gate behaviour")
finally:
    settings.flow_min_economics_enabled = off

# A flip into an uneconomic leg still exits the old one, and does not switch.
ce_rec = {"option_symbol": "X100CE", "side": OptionType.CALL.value,
          "entry_premium": 120.0, "peak_premium": 120.0, "open_ctime": 1000}
down = [mk(120 - i, 120 - i - 0.9, 2000 + i * 60) for i in range(8)]
flip = flow.detect(down, cheap_chain, snap, 112.0, ce_rec, instrument="NIFTY")
ok(flip.state == "EXIT" and flip.exit_now and not flip.switched,
   "an uneconomic flip exits the old leg to cash instead of switching into it")
ok(flip.prev_side == OptionType.CALL,
   "the exited side is still reported so the leg can be closed")
wide_down = [mk(400 - i * 40, 400 - i * 40 - 30, 2000 + i * 60, span=5.0)
             for i in range(8)]
rich_rec = dict(ce_rec, entry_premium=200.0, peak_premium=200.0)
flip_ok = flow.detect(wide_down, rich_chain, snap, 130.0, rich_rec,
                      instrument="NIFTY")
ok(flip_ok.state == "SWITCH" and flip_ok.switched,
   "an economic flip still switches exactly as before")

# --- the 0.5R trailing shadow --------------------------------------------
unit = flow_shadow.r_unit()
ok(abs(unit - settings.flow_giveback_points) < 1e-9,
   "Flow's R is its own giveback allowance, and it is stated not implied")
sh = flow_shadow.start(100.0)
ok(sh["giveback_allowance_points"] == round(0.5 * unit, 2)
   and sh["arm_at_premium"] == round(100.0 + 0.5 * unit, 2),
   "the shadow arms at +0.5R and trails a 0.5R giveback")

flow_shadow.step(sh, 100.0, 100.0, 96.0, 10)
ok(sh["state"] == flow_shadow.OPEN and not sh["armed"],
   "the trail does not tighten before it arms, so it cannot beat the live stop")
flow_shadow.step(sh, 100.0, 100.0 + 0.5 * unit, 100.0, 20)
ok(sh["armed"] and sh["state"] == flow_shadow.OPEN,
   "reaching +0.5R arms the trail without exiting at the trigger itself")
flow_shadow.step(sh, 100.0, 100.0 + unit, 100.0 + 0.4 * unit, 30)
ok(sh["state"] == flow_shadow.EXITED,
   "a giveback of half an allowance from the peak exits the shadow")
before = dict(sh)
flow_shadow.step(sh, 100.0, 100.0 + unit, 90.0, 40)
ok(sh == before, "an exited shadow never exits twice")

settled = flow_shadow.settle(sh, 100.0, 90.0, 50)
ok(settled["exit_premium"] == before["exit_premium"]
   and settled["points"] == round(before["exit_premium"] - 100.0, 2),
   "settling an exited shadow keeps its own fill, not the live one")

never = flow_shadow.start(100.0)
flow_shadow.step(never, 100.0, 100.0, 99.0, 10)
done = flow_shadow.settle(never, 100.0, 130.0, 20)
ok(done["state"] == flow_shadow.UNARMED and done["points"] == 30.0,
   "a leg the trail never touched is worth what the live rule made on it")

# The tracker must carry the shadow without touching the live leg's own result.
rec = {"instrument": "NIFTY", "side": OptionType.CALL.value,
       "option_symbol": "X100CE", "ts_open": 1000, "entry_premium": 100.0,
       "peak_premium": 100.0, "last_premium": 100.0, "ts_max": 1000,
       "entry_spread": None, "shadow_exit": flow_shadow.start(100.0),
       "agreement": "PENDING"}
chain_up = [quote(100.0 + unit)]
flow_tracker.update("NIFTY", flow.FlowSignal(enabled=True, state="HOLD"),
                    chain_up, up, 107.0, False, 1100, rec)
for tick, ts in ((100.0 + 0.4 * unit, 1200), (100.0 + 0.4 * unit, 1250)):
    flow_tracker.update("NIFTY", flow.FlowSignal(enabled=True, state="HOLD"),
                        [quote(tick)], up, 107.0, False, ts, rec)
fin = flow_tracker._finalize(rec, "EXIT", 1300)
ok(fin["final_premium"] == round(100.0 + 0.4 * unit, 2),
   "the live leg's own exit price is unchanged by the shadow")
ok(fin["shadow_exit"]["state"] == flow_shadow.EXITED,
   "the finished leg records where the shadow rule would have got out")
ok(fin["shadow_exit"]["cost_spread_source"] == option_costs.ASSUMED,
   "the shadow is costed by the same model as the live leg, and says which basis")
ok(fin["shadow_exit"]["vs_live_points"] is not None,
   "the record states the shadow's difference from the live rule, per leg")

# A refused leg opens nothing, so the refusal itself must be on record.
tmp = tempfile.mkdtemp(prefix="flow_refusal_smoke_")
saved_dir = settings.data_dir
try:
    settings.data_dir = tmp
    for _ in range(3):
        ok(flow_tracker.update("NIFTY", fresh_cheap, cheap_chain, up, 107.0,
                               False, 1400, None) is None,
           "a refused candidate opens no paper leg")
    rows = [json.loads(ln) for ln in
            open(os.path.join(tmp, "flow_refusals.jsonl"), encoding="utf-8")]
    ok(len(rows) == 1,
       "one candle's refusal is written once, not once per tick")
    ok(rows[0]["ratio"] == fresh_cheap.economics_ratio
       and rows[0]["required_multiple"] == fresh_cheap.economics_required
       and rows[0]["cost_spread_source"] == fresh_cheap.economics_cost_source,
       "the refusal row keeps the numbers it was refused on, and their basis")
    ok(rows[0]["spot"] == 107.0 and rows[0]["candle_time"] == up[-1].time,
       "the row is placed in the market so the refused leg can be graded later")
finally:
    settings.data_dir = saved_dir
    shutil.rmtree(tmp, ignore_errors=True)

print(f"checked {CHECKS}")
print("flow economics + shadow exit smoke: OK")
