"""Smoke for the Phase 11 multi-vehicle layer. No network, no DB, no orders.

Each failure mode this layer could have is asserted rather than described:

* **a futures win invented by bar ambiguity.** A one-minute bar holding both the
  stop and a target must resolve as the stop, or every backtest here is fiction.
* **a gross figure compared against a costed one.** An option leg with no
  recorded book must be excluded from the comparison, not entered with its gross R.
* **an option and a future judged on different levels.** Both must be followed on
  the production engine's own stop and targets, over the same window.
* **families pooled.** An index event and an MCX event must not share a verdict,
  and futures must be its own family, not filed under the underlying's options.
* **a preference asserted from noise.** A margin under 0.1R must be labelled as
  noise, and a family under the evidence bar must read UNPROVEN.
* **a fabricated greek.** IV, gamma and theta are not in the feed and must be
  ``None``, never inverted from an assumed vol.
* **provenance mixed.** A simulator premium path must be stamped SIMULATOR.

    .venv/bin/python _smoke_vehicles.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.models import Candle  # noqa: E402
from app.research.phase7.dataset import REAL, SIM, ChainSeries  # noqa: E402
from app.research.phase11 import (  # noqa: E402
    families,
    futures11,
    options11,
    vehicles,
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


T0 = 1_780_000_000


def candles(closes: list[float], *, highs: list[float] | None = None,
            lows: list[float] | None = None) -> list[Candle]:
    out = []
    for i, c in enumerate(closes):
        hi = highs[i] if highs else c + 2.0
        lo = lows[i] if lows else c - 2.0
        out.append(Candle(time=T0 + 60 * i, open=c, high=hi, low=lo, close=c,
                          volume=1000.0))
    return out


def series(legs_by_ts: list[tuple[int, dict]], source: str = REAL) -> ChainSeries:
    s = ChainSeries(instrument="NIFTY", source=source)
    for ts, legs in legs_by_ts:
        s.ts.append(ts)
        s.legs.append(legs)
        s.raw.append(list(legs.values()))
    return s


def leg(symbol: str, premium: float, *, bid: float | None = None,
        ask: float | None = None, strike: float = 25000.0,
        kind: str = "CE") -> dict:
    row = {"symbol": symbol, "premium": premium, "strike": strike,
           "option_type": kind, "delta": 0.5, "oi": 10_000.0,
           "oi_change": 0.0, "volume": 500.0, "expiry": "2026-08-28"}
    if bid is not None:
        row["bid"] = bid
    if ask is not None:
        row["ask"] = ask
    return row


# --- 1. families: futures is its own product ----------------------------
ok("an index future is INDEX_FUTURES, not INDEX_OPTIONS",
   families.family_of("NIFTY", "FUTURES") == families.INDEX_FUTURES)
ok("an MCX future is MCX_FUTURES",
   families.family_of("CRUDEOIL", "FUTURES") == families.MCX_FUTURES)
ok("a row that states no vehicle keeps the old options meaning",
   families.family_of("NIFTY") == families.INDEX_OPTIONS
   and families.family_of("GOLD") == families.MCX_OPTIONS)
ok("CE and PE are both the options family",
   families.family_of("NIFTY", "CE") == families.INDEX_OPTIONS
   and families.family_of("NIFTY", "PE") == families.INDEX_OPTIONS)
ok("a stock is OTHER in either vehicle",
   families.family_of("RELIANCE", "FUTURES") == families.OTHER
   and families.family_of("RELIANCE") == families.OTHER)
ok("the five families are all reported, empty ones included",
   set(families.split([]).keys()) == set(families.FAMILIES)
   and len(families.FAMILIES) == 5)
ok("tagging reads the vehicle off the row",
   [r["family"] for r in families.tag(
       [{"instrument": "NIFTY", "vehicle": "FUTURES"},
        {"instrument": "NIFTY"}])] == [families.INDEX_FUTURES,
                                       families.INDEX_OPTIONS])

# --- 2. futures branch ---------------------------------------------------
short = futures11.side_for("PE")
ok("a bearish option view maps to a SHORT future, not a long anything",
   short == futures11.SHORT and futures11.side_for("CE") == futures11.LONG)
ok("no option type and no direction gives no side",
   futures11.side_for(None) is None)

flat = candles([100.0] * 40)
res = futures11.evaluate("NIFTY", flat, 5, futures11.LONG)
ok("a signal with too few prior bars is not measurable, not assumed",
   res["measurable"] is False and "ATR" in res["reason"])

rise = candles([100.0 + i * 0.5 for i in range(40)]
               + [120.0 + i * 2.0 for i in range(40)])
res = futures11.evaluate("NIFTY", rise, 39, futures11.LONG)
ok("a futures trade on a trending tape is measurable", res["measurable"] is True)
ok("the futures stop comes from the production futures levels",
   res["stop"] < res["entry"] < res["target1"] < res["target2"] < res["target3"])
ok("targets reached are recorded in order",
   res["target_sequence"].startswith("T1"), res["target_sequence"])
ok("costs are charged, so net R is below gross R",
   res["net_r"] < res["gross_r"] and res["cost_points"] > 0)
ok("the futures book is absent rather than assumed to be one tick",
   res["spread"] is None
   and res["spread_source"] == futures11.UNAVAILABLE)
ok("MFE is recorded with the minute it happened",
   res["mfe_r"] > 0 and res["minutes_to_mfe"] > 0)

# a bar that contains the stop AND a target
mixed = candles([100.0 + i * 0.5 for i in range(40)])
i = len(mixed)
atr = futures11._atr(mixed, 39) or 1.0
entry = mixed[39].close
mixed.append(Candle(time=T0 + 60 * i, open=entry, high=entry + 20 * atr,
                    low=entry - 20 * atr, close=entry, volume=10.0))
res = futures11.evaluate("NIFTY", mixed, 39, futures11.LONG)
ok("a bar holding both the stop and a target resolves as the stop",
   res["outcome"] == futures11.STOP and res["bar_ambiguous"] is True)
ok("that bar's target is not counted as reached",
   res["targets_reached"] == {} and res["target_before_stop"] is False)
ok("the ambiguity rule travels with the row",
   "cannot order" in res["ambiguity_rule"])

fall = candles([100.0 - i * 0.5 for i in range(40)]
               + [80.0 - i * 2.0 for i in range(40)])
res = futures11.evaluate("CRUDEOIL", fall, 39, futures11.SHORT)
ok("a SHORT is scored in its own direction",
   res["measurable"] and res["stop"] > res["entry"] > res["target1"])
ok("a SHORT that runs down is a win, not a loss", res["gross_r"] > 0)

# --- 3. options branch ---------------------------------------------------
path = [(T0 + 60 * k, 100.0 + k * 3.0) for k in range(1, 30)]
sr = series([(T0, {"N1": leg("N1", 100.0, bid=99.0, ask=101.0)})]
           + [(ts, {"N1": leg("N1", px, bid=px - 1, ask=px + 1)})
              for ts, px in path])
row = options11.candidate("NIFTY", "N1", sr.legs[0]["N1"], spot=25000.0,
                          stop=90.0, targets=(110.0, 120.0, 130.0),
                          series=sr, ts=T0)
ok("the option leg is measurable on a recorded premium path",
   row["measurable"] is True)
ok("the recorded book is used, not a modelled one",
   row["book_source"] == "RECORDED_BOOK" and row["spread"] == 2.0)
ok("net R is gross R less one crossing of the book",
   abs(row["net_r"] - (row["gross_r"] - 2.0 / 10.0)) < 1e-6,
   f"{row['net_r']} vs {row['gross_r']}")
ok("IV, gamma and theta are absent, not invented",
   row["iv"] is None and row["gamma"] is None and row["theta"] is None
   and row["greeks_source"] == "PROVIDER_DELTA_ONLY")
ok("the spread is expressed against the risk the stop defines",
   row["spread_pct_of_risk"] == 20.0, str(row["spread_pct_of_risk"]))
ok("a book worth a fifth of the risk is FAIR, not GOOD",
   row["entry_quality"] == "FAIR", row["entry_quality"])
ok("provenance is carried from the series", row["data_source"] == REAL)

nobook = series([(T0, {"N1": leg("N1", 100.0)})]
                + [(ts, {"N1": leg("N1", px)}) for ts, px in path])
row = options11.candidate("NIFTY", "N1", nobook.legs[0]["N1"], spot=25000.0,
                          stop=90.0, targets=(110.0, 120.0, 130.0),
                          series=nobook, ts=T0)
ok("with no recorded book there is no net figure at all",
   row["net_r"] is None and row["book_source"] == options11.UNAVAILABLE)
ok("a gross figure is still recorded, clearly labelled gross",
   row["gross_r"] is not None)
ok("entry quality without a book is UNAVAILABLE, not GOOD",
   row["entry_quality"] == options11.UNAVAILABLE)

small = [(T0 + 60 * k, 100.0 + k * 0.1) for k in range(1, 30)]
wide = series([(T0, {"G1": leg("G1", 100.0, bid=94.0, ask=106.0)})]
             + [(ts, {"G1": leg("G1", px, bid=px - 6, ask=px + 6)})
                for ts, px in small])
row = options11.candidate("GOLD", "G1", wide.legs[0]["G1"], spot=25000.0,
                          stop=90.0, targets=(110.0, 120.0, 130.0),
                          series=wide, ts=T0)
ok("a book wider than the intended risk is UNPAYABLE",
   row["entry_quality"] == "UNPAYABLE", row["entry_quality"])
ok("an unpayable contract can win gross and still lose net",
   row["gross_r"] > 0 > row["net_r"], f"{row['gross_r']} {row['net_r']}")

sim = series([(T0, {"N1": leg("N1", 100.0, bid=99.0, ask=101.0)})]
            + [(ts, {"N1": leg("N1", px, bid=px - 1, ask=px + 1)})
               for ts, px in path], source=SIM)
row = options11.candidate("NIFTY", "N1", sim.legs[0]["N1"], spot=25000.0,
                          stop=90.0, targets=(110.0, 120.0, 130.0),
                          series=sim, ts=T0)
ok("a simulator path is stamped SIMULATOR and never as broker data",
   row["data_source"] == SIM)

stopped = series([(T0, {"N1": leg("N1", 100.0, bid=99.0, ask=101.0)})]
                + [(T0 + 60, {"N1": leg("N1", 88.0, bid=87.0, ask=89.0)}),
                   (T0 + 120, {"N1": leg("N1", 140.0, bid=139.0, ask=141.0)})])
row = options11.candidate("NIFTY", "N1", stopped.legs[0]["N1"], spot=25000.0,
                          stop=90.0, targets=(110.0, 120.0, 130.0),
                          series=stopped, ts=T0)
ok("a stop before a later rally is a stop, and the rally is not a target",
   row["outcome"] == options11.STOP and row["targets_reached"] == {}
   and row["target_before_stop"] is False)
ok("the minute of the stop is recorded", row["minutes_to_stop"] == 1.0)

res = options11.evaluate(
    "NIFTY", sr, T0, symbol="N1", option_type="CE", stop=90.0,
    targets=(110.0, 120.0, 130.0), spot=25000.0)
ok("the production leg is identified as such, not as one candidate of many",
   res["production_leg"] is not None
   and res["production_leg"]["role"] == "PRODUCTION_LEG")
res_missing = options11.evaluate(
    "NIFTY", sr, T0, symbol="N1", option_type="CE", stop=None,
    targets=(None,), spot=25000.0)
ok("no production stop means no option evaluation, not a chosen one",
   res_missing["measurable"] is False)

# --- 4. the selector ----------------------------------------------------
def fut_row(net: float) -> dict:
    return {"measurable": True, "net_r": net, "gross_r": net + 0.1,
            "cost_r": 0.1, "target_before_stop": net > 0, "first_event": "T1",
            "outcome": "T1", "spread_source": futures11.UNAVAILABLE}


def opt_res(net: float | None, kind: str = "CALL",
            quality: str = "GOOD") -> dict:
    row = {"measurable": True, "role": "PRODUCTION_LEG", "vehicle": kind,
           "net_r": net, "gross_r": 1.0, "cost_r": 0.2,
           "entry_quality": quality, "target_before_stop": True,
           "first_event": "T1", "outcome": "T1",
           "book_source": ("RECORDED_BOOK" if net is not None
                           else options11.UNAVAILABLE)}
    return {"measurable": True, "candidates": [row], "production_leg": row}


c = vehicles.compare(instrument="NIFTY", session="2026-08-21", signal_ts=T0,
                     direction="LONG", futures_row=fut_row(1.2),
                     options_result=opt_res(0.4))
ok("the higher net R wins", c["best_vehicle"] == vehicles.FUTURES)
ok("the reason codes state the fact that decided it",
   any(code.startswith("FUTURES_HIGHEST_NET_R") for code in
       c["vehicle_reason_codes"]))
ok("the margin over the runner-up is stated, not implied",
   any("MARGIN_OVER_CALL" in code for code in c["vehicle_reason_codes"]))
ok("a futures choice is filed under the futures family",
   c["family"] == families.INDEX_FUTURES
   and c["options_family"] == families.INDEX_OPTIONS)
ok("the row says it was never applied",
   c["research_only"] is True and c["applied_to_production"] is False)

c = vehicles.compare(instrument="NIFTY", session="2026-08-21", signal_ts=T0,
                     direction="LONG", futures_row=fut_row(1.2),
                     options_result=opt_res(1.15))
ok("a margin inside 0.1R is labelled noise rather than a preference",
   "MARGIN_WITHIN_NOISE_NOT_A_PREFERENCE" in c["vehicle_reason_codes"])

c = vehicles.compare(instrument="GOLD", session="2026-08-21", signal_ts=T0,
                     direction="LONG", futures_row=fut_row(-0.5),
                     options_result=opt_res(-1.0))
ok("when nothing pays, the answer is NO_TRADE",
   c["best_vehicle"] == vehicles.NO_TRADE
   and "BEST_VEHICLE_NET_R_NOT_POSITIVE" in c["vehicle_reason_codes"])
ok("the best loser is still named so the loss is attributable",
   any(code.startswith("BEST_WAS_") for code in c["vehicle_reason_codes"]))

c = vehicles.compare(instrument="NIFTY", session="2026-08-21", signal_ts=T0,
                     direction="LONG", futures_row=None,
                     options_result=opt_res(None))
ok("an option with no book does not compete on its gross figure",
   c["best_vehicle"] == vehicles.NO_TRADE
   and "OPTION_BOOK_UNAVAILABLE_SO_NOT_COMPARABLE" in c["vehicle_reason_codes"])
ok("a vehicle that was never evaluated says so",
   "FUTURES_NOT_EVALUATED" in c["vehicle_reason_codes"])

c = vehicles.compare(instrument="NIFTY", session="2026-08-21", signal_ts=T0,
                     direction="SHORT", futures_row=fut_row(0.9),
                     options_result=opt_res(0.2, kind="PUT"))
ok("a PUT competes as PUT, not as CALL",
   any(e["vehicle"] == vehicles.PUT for e in c["entrants"]))
ok("the futures cost treatment is declared as modelled, not as a book",
   any(e.get("cost_treatment") == "MODELLED_CHARGES_NO_DEPTH"
       for e in c["entrants"]))
ok("the option cost treatment is declared as ask-in/bid-out",
   any(e.get("cost_treatment") == "ASK_IN_BID_OUT_RECORDED_BOOK"
       for e in c["entrants"]))
ok("the winning vehicle with a poor book is flagged",
   "WINNER_BOOK_POOR" in vehicles.compare(
       instrument="NIFTY", session="s", signal_ts=T0, direction="LONG",
       futures_row=None,
       options_result=opt_res(0.6, quality="POOR"))["vehicle_reason_codes"])

# --- 5. the comparison report -------------------------------------------
rows = [
    vehicles.compare(instrument="NIFTY", session="2026-08-21", signal_ts=T0,
                     direction="LONG", futures_row=fut_row(1.2),
                     options_result=opt_res(0.4)),
    vehicles.compare(instrument="GOLD", session="2026-08-21", signal_ts=T0,
                     direction="LONG", futures_row=fut_row(-0.5),
                     options_result=opt_res(-2.0)),
]
rep = vehicles.report(rows, sessions=5, holdout_sessions=2)
ok("index and MCX get separate tables",
   set(rep["families"]) == {families.INDEX_OPTIONS, families.MCX_OPTIONS})
ok("the MCX loss does not enter the index numbers",
   rep["families"][families.INDEX_OPTIONS]["by_vehicle"][
       vehicles.FUTURES]["avg_net_r"] == 1.2)
ok("five sessions and two holdouts is UNPROVEN, whatever the numbers say",
   all(f["verdict"] == "UNPROVEN_INSUFFICIENT_EVIDENCE"
       for f in rep["families"].values()))
ok("the evidence bar is stated with the verdict",
   "5 holdout sessions" in
   rep["families"][families.INDEX_OPTIONS]["verdict_basis"])
ok("NO_TRADE carries no invented economics",
   rep["families"][families.MCX_OPTIONS]["by_vehicle"][
       vehicles.NO_TRADE]["avg_net_r"] is None)
empty = vehicles.report([], sessions=0, holdout_sessions=0)
ok("with no events there are no families and no verdict",
   empty["families"] == {})
ok("the futures cost advantage is disclosed in the report itself",
   "flattered" in rep["futures_cost_caveat"])
ok("the report states that families are never pooled",
   "never pooled" in rep["pooling_rule"])
ok("head-to-head only counts events where both vehicles were costable",
   rep["families"][families.INDEX_OPTIONS]["head_to_head_events"] == 1)

# --- 6. isolation --------------------------------------------------------
blob = "\n".join(
    open(os.path.join(HERE, "app", "research", "phase11", f),
         encoding="utf-8").read()
    for f in ("futures11.py", "options11.py", "vehicles.py", "vehiclerun.py"))
ok("the vehicle layer holds no order path",
   not any(t in blob for t in ("place_order", "placeOrder", "broker.place",
                               "live_orders", "def tick(")))
ok("the vehicle layer does not touch the live futures paper book",
   "futures_paper.tick" not in blob and "record_entry" not in blob)
ok("the futures levels are the production ones, not a second formula",
   "from app.execution.futures_paper import _cost_points, _levels" in blob)

offenders = []
for root, _dirs, files in os.walk(os.path.join(HERE, "app")):
    if os.sep + "research" in root:
        continue
    for f in files:
        if f.endswith(".py") and any(
                t in open(os.path.join(root, f), encoding="utf-8").read()
                for t in ("futures11", "options11", "vehiclerun",
                          "phase11.vehicles")):
            offenders.append(os.path.join(root, f))
ok("no production module imports the vehicle layer", not offenders,
   str(offenders))

print()
if FAILS:
    print(f"VEHICLE SMOKE FAILED ({len(FAILS)} of {CHECKS})")
    for f in FAILS:
        print(" -", f)
    raise SystemExit(1)
print(f"VEHICLE SMOKE PASSED ({CHECKS} checks)")
