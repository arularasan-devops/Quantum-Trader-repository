"""Smoke test for PHASE 23 — the break-even hurdle PAPER SHADOW. Verifies:
  * the hurdle is computed from the ASK with the MEASURED spread, never the
    family-median assumption
  * an absent, crossed, zero or non-numeric book is UNMEASURED and every arm
    ABSTAINS on it (neither allowed nor credited as a refusal)
  * the three arms (EXISTING / <=5% / <=3%) score the same resolved trades
  * MONEY SAVED and MONEY MISSED are both reported, plus the same net effect with
    the single largest refused trade removed
  * the sweep covers 3%-5% inclusive and the verdict starts NOT promotable
  * observe() records one row per BUY episode, resolves from the engine's own
    closed trade, and swallows a store failure instead of raising into a tick
  * futures are neither recorded nor filtered here
Run: .venv/bin/python _smoke_phase23.py
"""
from __future__ import annotations

import os
import tempfile

from app.config import settings

_TMP = tempfile.mkdtemp(prefix="p23_smoke_")
settings.data_dir = _TMP  # isolate the store before anything writes

from app.analysis import option_costs  # noqa: E402
from app.models import Decision, OptionQuote, OptionType, Signal  # noqa: E402
from app.research.phase23 import (  # noqa: E402
    hurdle as H, report, service, shadow, store, verdict,
)

checks = 0


def ok(cond: bool, label: str) -> None:
    global checks
    assert cond, f"FAILED: {label}"
    checks += 1
    print(f"  ok  {label}")


print("[1] hurdle from a measured book")
h = H.compute("NIFTY", bid=98.0, ask=102.0, lot_size=75, lots=1)
ok(h.status == H.MEASURED, "two-sided book is MEASURED")
ok(h.entry_premium == 102.0, "entry premium is the ASK, not the last price")
ok(h.spread_points == 4.0, "spread is ask-bid in points")
ok(abs(h.spread_pct - 100.0 * 4.0 / 102.0) < 0.01, "spread % is against the ask")
ok(h.hurdle_pct is not None and h.hurdle_pct > h.spread_pct,
   "hurdle exceeds the spread alone (brokerage + statutory charged)")
manual = option_costs.round_trip("NIFTY", 102.0, None, 75, 1, quoted_spread=4.0)
ok(manual is not None and manual.spread_source == option_costs.MEASURED,
   "cost model reports the spread as MEASURED")
ok(abs(h.hurdle_pct - 100.0 * manual.cost_points / 102.0) < 1e-3,
   "hurdle % == round-trip cost points / ask")

print("[2] the family-median assumption is never used for a shadow decision")
assumed = option_costs.round_trip("RELIANCE", 100.0, None, 500, 1)
ok(assumed is not None and assumed.spread_source == option_costs.ASSUMED,
   "the cost model itself can still assume (for labelled historical reports)")
for bad in (dict(bid=None, ask=102.0), dict(bid=98.0, ask=None),
            dict(bid=0.0, ask=102.0), dict(bid=103.0, ask=102.0),
            dict(bid="x", ask="y")):
    bad_h = H.compute("NIFTY", lot_size=75, lots=1, **bad)
    ok(bad_h.status == H.UNMEASURED and bad_h.hurdle_pct is None,
       f"unusable book -> UNMEASURED ({bad})")
    ok(bad_h.decide(3.0) == H.ABSTAIN and bad_h.decide(5.0) == H.ABSTAIN,
       "every arm ABSTAINS on an unmeasured row")

print("[3] gate decisions")
ok(H.compute("NIFTY", bid=199.0, ask=200.0, lot_size=75).decide(3.0) == H.ALLOW,
   "a tight book on a large premium passes <=3%")
wide = H.compute("NIFTY", bid=8.0, ask=10.0, lot_size=75)
ok(wide.decide(3.0) == H.REFUSE and wide.decide(5.0) == H.REFUSE,
   "a 20% spread is refused by both cutoffs")
ok(H.SWEEP_PCT[0] == 3.0 and H.SWEEP_PCT[-1] == 5.0 and len(H.SWEEP_PCT) >= 5,
   "sweep brackets 3%-5% inclusive")

print("[4] arms, attribution and the sweep on a synthetic book")
rows = []


def mk(idx, hurdle_pct, net, outcome, *, engine=True, resolved=True,
       measured=True, instrument="NIFTY", otype="CE", ts=1_700_000_000):
    row = {
        "kind": store.OBS, "id": f"r{idx}", "ts": ts + idx * 3600,
        "instrument": instrument, "option": f"OPT{idx}", "option_type": otype,
        "engine_would_buy": engine, "resolved": resolved,
        "hurdle_status": H.MEASURED if measured else H.UNMEASURED,
        "hurdle_pct": hurdle_pct if measured else None,
        "measured_spread_pct": 1.5 if measured else None,
        "arm_existing": H.ALLOW if engine else H.REFUSE,
        "outcome": outcome, "net_pnl": net, "net_r": (net / 1000.0) if net else 0.0,
        "hold_minutes": 7,
        "real_feed": True, "feed": "angelone",
    }
    for name, cut in (("arm_shadow_3", 3.0), ("arm_shadow_5", 5.0)):
        if not measured:
            row[name] = H.ABSTAIN
        else:
            row[name] = H.ALLOW if hurdle_pct <= cut else H.REFUSE
    return row


rows.append(mk(1, 2.0, 5000.0, "T1_BEFORE_SL"))
rows.append(mk(2, 2.5, -1200.0, "SL"))
rows.append(mk(3, 4.0, 900.0, "T1_BEFORE_SL", otype="PE"))
rows.append(mk(4, 7.5, -9000.0, "SL", instrument="RELIANCE", otype="PE"))
rows.append(mk(5, 6.0, 400.0, "T1_BEFORE_SL", instrument="RELIANCE"))
rows.append(mk(6, 4.5, -300.0, "SL"))
rows.append(mk(7, 3.0, 100.0, "TRAIL_OR_LOCK", measured=False))
rows.append(mk(8, 2.0, None, "NO_TRADE", engine=False, resolved=False))

existing = report.arm_report(rows, report.EXISTING)
g5 = report.arm_report(rows, report.SHADOW_5)
g3 = report.arm_report(rows, report.SHADOW_3)
ok(existing["accepted"] == 7, "EXISTING holds every resolved engine trade")
ok(g5["accepted"] == 4, "<=5% holds the four measured rows at or under 5%")
ok(g3["accepted"] == 2, "<=3% holds only the two rows at or under 3%")
ok(existing["net_pnl"] == -4100.0, "EXISTING net is the engine's own book")
ok(g5["net_pnl"] == 4400.0, "<=5% net drops the two wide-spread legs")
att5 = g5["attribution"]
ok(att5["money_saved_by_refusing_high_hurdle_trades"] == 9000.0,
   "MONEY SAVED counts only the losses refused")
ok(att5["money_missed_by_refusing_them"] == 400.0,
   "MONEY MISSED counts the profit given up")
ok(att5["net_effect"] == 8600.0, "net effect is saved minus missed")
ok(att5["abstained_unmeasured"] == 1,
   "the unmeasured row is abstained, not counted as a refusal")
ok(att5["net_effect_excluding_largest"] == -400.0,
   "removing the single largest refused trade exposes outlier dependence")
ok(existing.get("attribution") is None,
   "the unchanged baseline has nothing to attribute")

sweep = report.sweep(rows)
ok([r["threshold_pct"] for r in sweep] == list(H.SWEEP_PCT),
   "sweep reports every cutoff between 3% and 5%")
ok(all(r["vs_existing_net_pnl"] == r["net_pnl"] - existing["net_pnl"]
       for r in sweep), "sweep compares against the unchanged live baseline")

br = report.breakdowns(rows, report.EXISTING)
ok(set(br["by_instrument"]) == {"NIFTY", "RELIANCE"}, "per-instrument breakdown")
ok(set(br["by_option_type"]) == {"CE", "PE"}, "CE/PE breakdown")
ok(br["by_time_of_day"], "time-of-day breakdown present")
ok(report.time_bucket(None) == "UNKNOWN", "a missing timestamp is not bucketed")

cov = report.coverage(rows)
ok(cov["measured_book"] == 7 and cov["unmeasured_book"] == 1,
   "coverage separates measured from unmeasured opportunities")

sim = mk(9, 2.0, 5_000_000.0, "T1_BEFORE_SL")
sim["real_feed"] = False
sim["feed"] = "simulated"
with_sim = rows + [sim]
ok(report.arm_report(with_sim, report.EXISTING)["net_pnl"] == existing["net_pnl"],
   "a simulated-feed row never reaches an arm")
ok(report.coverage(with_sim)["simulated_feed_rows_excluded"] == 1
   and report.coverage(with_sim)["rows_on_file"] == 9
   and report.coverage(with_sim)["opportunities"] == 8,
   "simulated rows stay visible in coverage, excluded from the shadow")

stab = report.stability(rows, 3.0)
ok(stab["status"] == "OK" and "halves" in stab,
   "stability splits the shadow chronologically")
ok(report.stability(rows[:2], 3.0)["status"] == "INSUFFICIENT_DATA",
   "too few trades reports INSUFFICIENT_DATA rather than a number")

print("[5] verdict starts un-promotable")
ver = verdict.evaluate(rows)
ok(ver["status"] in (verdict.NOT_PROVEN, verdict.INSUFFICIENT),
   f"verdict is not a promotion on 7 trades ({ver['status']})")
ok(ver["discovered_threshold_pct"] is None, "no threshold is discovered yet")
ok(ver["promotion_requires_human_review"] is True,
   "promotion is flagged as a human decision")
ok(any("sample" in r for c in ver["candidates"] for r in c["blocking_reasons"]),
   "sample size is named as a blocking reason")

print("[6] observe / resolve lifecycle")
shadow._reset_for_tests()
if os.path.exists(store.path()):
    os.remove(store.path())


def decision(sym="NIFTY24500CE", strike=24500.0, otype=OptionType.CALL):
    return Decision(
        signal=Signal.BUY, confidence=88.0, signal_strength=80.0,
        trade_quality="A+", recommended_option=sym, strike=strike,
        option_type=otype, current_premium=101.0, stop_loss=90.0,
        target1=120.0, entry_trigger="PULLBACK",
    )


def chain(bid=100.0, ask=102.0, sym="NIFTY24500CE"):
    return [OptionQuote(symbol=sym, strike=24500.0, option_type=OptionType.CALL,
                        premium=101.0, iv=0.2, delta=0.5, gamma=0.01, theta=-1,
                        vega=1, oi=1000, oi_change=10, volume=500,
                        bid=bid, ask=ask)]


rid = service.observe("NIFTY", decision(), chain(), lot_size=75, lots=1,
                      engine_bought=True, engine_fill=102.0,
                      in_position_before=False, now=1_700_000_000)
ok(rid is not None, "an auto BUY opportunity is recorded")
saved = store.rows()
ok(len(saved) == 1, "one row on the store")
row = saved[0]
ok(row["engine_would_buy"] is True, "the engine's own decision is recorded")
ok(row["hurdle_status"] == H.MEASURED and row["bid"] == 100.0
   and row["ask"] == 102.0, "the live book travels with the row")
ok(row["arm_shadow_3"] in (H.ALLOW, H.REFUSE)
   and row["arm_shadow_5"] in (H.ALLOW, H.REFUSE),
   "both arms decided at observation time, before the outcome")
ok(row["outcome"] == "NO_TRADE" and row["resolved"] is False,
   "an unresolved row carries no outcome")

again = service.observe("NIFTY", decision(), chain(), lot_size=75, lots=1,
                        engine_bought=False, engine_fill=None,
                        in_position_before=True, now=1_700_000_060)
ok(again is None, "no row while a position is already open and nothing was bought")
service.observe("NIFTY", decision(), chain(), lot_size=75, lots=1,
                engine_bought=False, engine_fill=None,
                in_position_before=False, now=1_700_000_120)
service.observe("NIFTY", decision(), chain(), lot_size=75, lots=1,
                engine_bought=False, engine_fill=None,
                in_position_before=False, now=1_700_000_180)
ok(len(store.rows()) == 1, "a persisting BUY is one opportunity, not one per tick")

wait = decision()
wait.signal = Signal.WAIT
ok(service.observe("NIFTY", wait, chain(), lot_size=75, lots=1,
                   engine_bought=False, engine_fill=None,
                   in_position_before=False) is None,
   "a WAIT is not an opportunity and is not counted as a refusal")

service.resolve("NIFTY", {
    "time": 1_700_000_900, "exit": 118.0, "exit_reason": "TARGET 1 HIT",
    "net_pnl": 1200.0, "risk_rupees": 900.0, "holding_minutes": 15,
    "lots": 1, "cost_status": "MEASURED", "exit_bid": 117.5, "exit_ask": 118.5,
    "entry_bid": 100.0, "entry_ask": 102.0,
})
resolved = [r for r in store.rows() if r["id"] == rid][0]
ok(resolved["resolved"] is True, "the engine's closed trade resolves the row")
ok(resolved["outcome"] == shadow.T1, "TARGET 1 HIT classifies as T1_BEFORE_SL")
ok(resolved["net_r"] == round(1200.0 / 900.0, 3), "net R is net P&L / risk")
ok(resolved["hold_minutes"] == 15, "hold time is carried")
ok(shadow.classify_exit("STOP LOSS HIT") == shadow.SL
   and shadow.classify_exit("TRAIL EXIT") == shadow.TRAIL
   and shadow.classify_exit(None) == shadow.OTHER, "exit classification")
again2 = service.observe("NIFTY", decision(), chain(), lot_size=75, lots=1,
                         engine_bought=False, engine_fill=None,
                         in_position_before=False, now=1_700_001_500)
ok(again2 is not None,
   "after the episode closed, the same option is a new opportunity")
ok(shadow.health()["observed"] == 2 and shadow.health()["resolved"] == 1
   and shadow.health()["failures"] == 0, "health counts observations honestly")

print("[7] a research failure never reaches the tick")
broken = Decision(signal=Signal.BUY, confidence=1.0, signal_strength=1.0,
                  trade_quality="C", recommended_option="X")
before = shadow.health()["failures"]
res = shadow.observe("NIFTY", broken, [], lot_size=0, lots=1,
                     engine_bought=False, engine_fill=None,
                     in_position_before=False, now=1_700_100_000)
ok(res is not None, "a leg with no usable book is still recorded (as UNMEASURED)")
ok(shadow.health()["failures"] == before, "no failure raised on a missing book")

print("[8] futures stay outside this experiment")
built = service.summary()["report"]
ok(built["futures_excluded"] is True and "signal quality" in built["futures_note"],
   "the report states futures are excluded and why")
ok(all(r.get("vehicle") == "OPTION" for r in store.rows()),
   "every recorded row is an option leg")
ok(built["spread_source"] == "MEASURED_BID_ASK_ONLY",
   "the report names its spread source")

print("[9] the shadow cannot place or block an order")
src = open(os.path.join("app", "research", "phase23", "shadow.py"),
           encoding="utf-8").read()
for banned in ("place_order", ".buy(", ".sell(", "settings.auto_trade_enabled"):
    ok(banned not in src, f"shadow.py never references {banned}")

print(f"\nPHASE 23 SMOKE: {checks} checks passed")
