"""Phase 21 smoke — the futures leg at the signal, MCX research basis, reports.

Run: .venv/bin/python _smoke_phase21.py

What this asserts, in the order it matters:

1. a futures book read at the signal instant becomes a full quote — contract,
   expiry, DTE, timestamp, bid, ask, spread, OI, volume, feed age, quality — and
   survives a write/read round trip with those fields intact;
2. the three capture states separate: a fresh two-sided book is EXACT, an older
   one NEAR, and a feed with no book is MISSING with the reason, never a price;
3. **no cross-timestamp pairing**: a book snapshotted well after the signal
   cannot be EXACT however good the feed's own age looks, and the delay is
   recorded on the row rather than discarded;
4. with no two-sided book there is no fill: the LTP is a reference only, the leg
   is not costable, and the tracker resolves it COST_UNKNOWN rather than charging
   a spread nobody quoted;
5. side awareness: a bullish read is a futures LONG entered at the ask, a bearish
   read a SHORT entered at the bid, and the geometry mirrors accordingly;
6. MCX geometry is measured from the commodity's own futures continuation, with
   expected move / stop / T1-T3 / room / reward-risk, labelled
   MCX_FUTURES_CONTINUATION_BASIS and flagged not validated;
7. below the sample floor the MCX plan and the MCX market edge both refuse and
   name the shortfall, rather than publishing a percentile off a handful of bars;
8. the MCX market edge stays separate from the cash prior: an override marks the
   A+ row as not-cash-prior and carries the basis, and the cash-prior path is
   unchanged when no override is passed;
9. priority tiers: CRUDEOIL is DEEP and always due, the other four are SAMPLED
   and thinned to one row per interval, and none of the five is removed;
10. the report separates OPTION quality from FUTURES capture and separates
    INDEX / MCX / STOCK, counts missing evidence instead of averaging it away,
    withholds a vehicle preference below the bar, and says whether the read was
    the whole series or a bounded tail;
11. the overnight collector is standalone: importable without touching live
    capture, refusing while the session is open or an engine is answering.
"""
from __future__ import annotations

import os
import pathlib
import re
import tempfile

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="p21smoke-"))

from app.analysis import instrument_family as fam  # noqa: E402
from app.models import Candle  # noqa: E402
from app.research.phase17 import aplus  # noqa: E402
from app.research.phase17 import futures as fut  # noqa: E402
from app.research.phase17 import mcx, quality, schema, tracker  # noqa: E402
from app.research.phase21 import report as rep  # noqa: E402

CHECKS = 0
FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(label)


SIGNAL = 1_700_000_000.0


def book(**over: object) -> dict:
    out = {
        "symbol": "CRUDEOIL25SEPFUT", "expiry": "2026-09-18",
        "days_to_expiry": 17, "lot_size": 100, "exchange": "MCX",
        "ltp": 5_600.0, "bid": 5_599.0, "ask": 5_601.0,
        "oi": 12_345, "volume": 6_789, "feed_age_sec": 0.4,
        "quote_ts": SIGNAL, "source": "websocket",
    }
    out.update(over)
    return out


# ------------------------------------------------- 1. every required field, kept
q = fut.quote("CRUDEOIL", book(), signal_ts=SIGNAL, capture_ts=SIGNAL + 0.1,
              source="websocket")
check(q is not None, "a futures book at the signal produces a quote")
assert q is not None
check(q.vehicle == schema.FUTURES, "the leg is recorded as the FUTURES vehicle")
for field, value in (
    ("symbol", "CRUDEOIL25SEPFUT"), ("expiry", "2026-09-18"),
    ("days_to_expiry", 17), ("bid", 5_599.0), ("ask", 5_601.0),
    ("premium", 5_600.0), ("oi", 12_345.0), ("volume", 6_789.0),
):
    check(getattr(q, field) == value, f"the futures quote records {field}")
check(q.spread == 2.0, "the futures spread is the quoted book, not an assumption")
check(q.feed_age_ms == 400.0, "the futures feed age is carried in ms")
check(q.snapshot_ts == SIGNAL + 0.1, "the snapshot timestamp is the capture instant")
check(q.strike is None, "a futures leg has no strike")

row = q.as_dict()
back = schema.Quote.from_dict(row)
check(back is not None, "a recorded futures quote can be read back")
assert back is not None
for field in ("symbol", "expiry", "days_to_expiry", "bid", "ask", "premium",
              "oi", "volume", "feed_age_ms", "signal_to_snapshot_ms",
              "snapshot_ts", "data_quality", "vehicle"):
    check(getattr(back, field) == getattr(q, field),
          f"{field} survives the futures quote round trip")
check(back.spread == q.spread and back.spread_pct == q.spread_pct,
      "spread is recomputed on read rather than trusted from the row")

# ------------------------------------------------------- 2. the three states
check(fut.capture_state(q) == (fut.EXACT, None),
      "a fresh two-sided book at the signal is EXACT")

near = fut.quote("CRUDEOIL", book(feed_age_sec=6.0),
                 signal_ts=SIGNAL, capture_ts=SIGNAL + 0.1)
assert near is not None
state, why = fut.capture_state(near)
check(state == fut.NEAR, "a real but older two-sided book is NEAR, not EXACT")
check(why is None, "a NEAR leg needs no absence reason")

check(fut.quote("CRUDEOIL", None, signal_ts=SIGNAL, capture_ts=SIGNAL) is None,
      "no feed produces no futures quote at all")
check(fut.capture_state(None) == (fut.MISSING, fut.NO_FEED),
      "an absent futures leg is MISSING with the reason NO_FUTURES_FEED")
check(fut.quote("CRUDEOIL", book(symbol=None), signal_ts=SIGNAL,
                capture_ts=SIGNAL) is None,
      "a feed with no named contract records no leg")
check(fut.quote("CRUDEOIL", book(ltp=None, bid=None, ask=None),
                signal_ts=SIGNAL, capture_ts=SIGNAL) is None,
      "a book with neither price nor depth records no leg")

ltp_only = fut.quote("CRUDEOIL", book(bid=None, ask=None),
                     signal_ts=SIGNAL, capture_ts=SIGNAL + 0.1)
assert ltp_only is not None
check(fut.capture_state(ltp_only) == (fut.MISSING, fut.NO_BOOK),
      "an LTP with no two-sided book is MISSING, not a capture")
check(ltp_only.spread is None, "a one-sided futures book has no spread invented")

tally = fut.tally([fut.EXACT, fut.EXACT, fut.NEAR, fut.MISSING])
check(tally["counts"] == {fut.EXACT: 2, fut.NEAR: 1, fut.MISSING: 1},
      "the capture tally counts the three states separately")
check(tally["exact_pct"] == 50.0 and tally["captured_pct"] == 75.0,
      "exact and captured rates are reported apart from each other")
check(fut.tally([])["exact_pct"] is None,
      "an empty tally publishes no rate rather than 0%")

# ------------------------------------------- 3. no cross-timestamp pairing
late = fut.quote("CRUDEOIL", book(feed_age_sec=0.1),
                 signal_ts=SIGNAL, capture_ts=SIGNAL + 45.0)
assert late is not None
check(late.signal_to_snapshot_ms == 45_000.0,
      "the signal-to-snapshot delay is recorded, not discarded")
check(fut.capture_state(late)[0] != fut.EXACT,
      "a book snapshotted long after the signal cannot be EXACT")
much_later = fut.quote("CRUDEOIL", book(feed_age_sec=0.1),
                       signal_ts=SIGNAL, capture_ts=SIGNAL + 600.0)
assert much_later is not None
check(fut.capture_state(much_later) == (fut.MISSING, fut.STALE_BOOK),
      "a futures book from another minute is MISSING/STALE, never paired")

# --------------------------------------------- 4/5. fills, sides, geometry
check(fut.side_of("BULLISH") == fut.LONG, "a bullish read is a futures LONG")
check(fut.side_of("BEARISH") == fut.SHORT, "a bearish read is a futures SHORT")
check(fut.side_of("SIDEWAYS") is None, "an unreadable direction refuses a side")

px, how = fut.entry_price(q, fut.LONG)
check((px, how) == (5_601.0, "BOOK"), "a long futures entry crosses to the ask")
px, how = fut.entry_price(q, fut.SHORT)
check((px, how) == (5_599.0, "BOOK"), "a short futures entry is hit on the bid")
px, how = fut.entry_price(ltp_only, fut.LONG)
check(px == 5_600.0 and how == "LTP_REFERENCE_NOT_FILLABLE",
      "with no book the LTP is a reference price and says it is not fillable")
check(fut.entry_price(q, None) == (None, fut.MISSING),
      "no side means no futures entry price")


def option(instrument: str, vehicle: str) -> schema.Quote:
    """A clean two-sided option book, so the row's own quality is EXACT and the
    futures column is the only thing under test."""
    return schema.Quote(
        instrument=instrument, vehicle=vehicle,
        symbol=f"{instrument}{vehicle}", strike=5_600.0,
        expiry="2026-09-18", days_to_expiry=17,
        bid=98.0, ask=102.0, premium=100.0, delta=0.5,
        oi=1_000.0, volume=500.0, source="websocket",
        feed_age_ms=100.0, signal_to_snapshot_ms=50.0, snapshot_ts=SIGNAL,
        data_quality=quality.EXACT,
    )


def observation(direction: str, futures_quote: schema.Quote | None,
                instrument: str = "CRUDEOIL") -> schema.Observation:
    return schema.Observation(
        observation_id=f"obs-{instrument}-{direction}",
        instrument=instrument, family=fam.family(instrument),
        candidate_class="WAIT",
        direction=direction,
        selected_vehicle=schema.CE,
        selected=option(instrument, schema.CE),
        opposite=option(instrument, schema.PE),
        futures=futures_quote,
        signal_ts=SIGNAL, capture_ts=SIGNAL,
    )


def candles(n: int, *, drift: float, start: float = 5_500.0) -> list[Candle]:
    out: list[Candle] = []
    price = start
    for i in range(n):
        o = price
        c = price + drift
        out.append(Candle(
            time=int(SIGNAL - (n - i) * 60),
            open=o, high=max(o, c) + 2.0, low=min(o, c) - 2.0,
            close=c, volume=1_000.0,
        ))
        price = c
    return out


up_bars = candles(600, drift=1.0)
mcx_plan = mcx.plan("CRUDEOIL", 5_600.0, up_bars, "BULLISH")
check(mcx_plan["verdict"] == "MEASURED", "enough directional bars measure an MCX plan")
check(mcx_plan["basis"] == mcx.BASIS,
      "the MCX plan is labelled MCX_FUTURES_CONTINUATION_BASIS")
check(mcx_plan["basis"] != mcx.CASH_BASIS,
      "the MCX basis is not the cash-underlying basis")
check(mcx_plan["not_validated"] is True and mcx_plan["research_only"] is True,
      "the MCX plan says it is research only and not validated")
for field in ("expected_move_points", "stop_points", "stop", "target1",
              "target2", "target3", "room_points", "reward_to_risk"):
    check(mcx_plan[field] is not None, f"the MCX plan publishes {field}")
check(mcx_plan["target1"] > mcx_plan["entry_reference"] > mcx_plan["stop"],
      "an MCX long's target is above entry and its stop below it")
check(mcx_plan["target3"] >= mcx_plan["target2"] >= mcx_plan["target1"],
      "the MCX targets are ordered by the percentile they came from")

short_plan = mcx.plan("CRUDEOIL", 5_600.0, candles(600, drift=-1.0), "BEARISH")
check(short_plan["verdict"] == "MEASURED", "a bearish MCX plan measures too")
check(short_plan["target1"] < short_plan["entry_reference"] < short_plan["stop"],
      "an MCX short's target is below entry and its stop above it")

thin = mcx.plan("CRUDEOIL", 5_600.0, candles(20, drift=1.0), "BULLISH")
check(thin["verdict"] == mcx.INSUFFICIENT_DATA,
      "too few samples refuses an MCX plan")
check(any(str(r).startswith("ONLY_") for r in thin["reasons"]),
      "the MCX refusal names the sample shortfall")
check(thin["target1"] is None, "a refused MCX plan publishes no target")
check(mcx.plan("NIFTY", 20_000.0, up_bars, "BULLISH")["reasons"] == ["NOT_MCX"],
      "the MCX geometry refuses a non-MCX instrument")
check(mcx.plan("CRUDEOIL", 5_600.0, up_bars, "SIDEWAYS")["reasons"]
      == ["DIRECTION_UNKNOWN"], "an unreadable direction refuses an MCX plan")
check(mcx.plan("CRUDEOIL", None, up_bars, "BULLISH")["reasons"] == ["NO_PRICE"],
      "no futures reference price refuses an MCX plan")

geo = fut.geometry(observation("BULLISH", q), mcx_plan=mcx_plan)
check(geo["capture"] == fut.EXACT, "the geometry carries the capture state")
check(geo["basis"] == fut.BASIS_MCX,
      "an MCX futures leg is graded on the continuation basis")
check(geo["side"] == fut.LONG and geo["entry"] == 5_601.0,
      "the futures leg is a long entered at the ask")
check(geo["target1"] > geo["entry"] > geo["stop"],
      "the futures long geometry is the right way up")
check(geo["spread"] == 2.0 and geo["oi"] == 12_345.0 and geo["volume"] == 6_789.0,
      "the futures row carries spread, OI and volume for the tradability read")
check(geo["research_only"] is True, "the futures geometry is research only")

short_geo = fut.geometry(observation("BEARISH", q), mcx_plan=short_plan)
check(short_geo["side"] == fut.SHORT and short_geo["entry"] == 5_599.0,
      "a bearish futures leg is a short entered on the bid")
check(short_geo["target1"] < short_geo["entry"] < short_geo["stop"],
      "the futures short geometry is mirrored, not copied")

no_leg = fut.geometry(observation("BULLISH", None), mcx_plan=mcx_plan)
check(no_leg["capture"] == fut.MISSING and no_leg["entry"] is None,
      "no futures book means no futures entry and a MISSING row")
check(no_leg["basis"] == fut.BASIS_NONE,
      "a MISSING futures leg claims no geometry basis")

ltp_geo = fut.geometry(observation("BULLISH", ltp_only), mcx_plan=mcx_plan)
check(ltp_geo["capture"] == fut.MISSING,
      "an LTP-only futures row is not a capture")
check(ltp_geo["entry_source"] == "LTP_REFERENCE_NOT_FILLABLE",
      "an LTP-only futures entry is marked unfillable")

# ------------------------------------- 4b. an uncosted leg stays uncosted
def leg_row(oid: str, vehicle: str, *, cost: str, net_r: float | None,
            instrument: str = "CRUDEOIL") -> schema.LegOutcome:
    return schema.LegOutcome(
        observation_id=oid, instrument=instrument, vehicle=vehicle,
        side=schema.FUTURES if vehicle == schema.FUTURES else "SELECTED",
        symbol=f"{instrument}{vehicle}", signal_ts=SIGNAL,
        resolved=True, cost_status=cost, net_r=net_r,
        position=fut.LONG if vehicle == schema.FUTURES else None,
        geometry=fut.BASIS_MCX if vehicle == schema.FUTURES else "PUBLISHED",
    )


leg = leg_row("obs-1", schema.FUTURES, cost=schema.COST_UNKNOWN, net_r=None)
row = leg.as_dict()
check(row["position"] == fut.LONG and row["geometry"] == fut.BASIS_MCX,
      "a futures leg writes which way it was positioned and on which geometry, "
      "so a reader is never left inferring the sign from the sign of net R")
check(leg.as_dict()["cost_status"] == schema.COST_UNKNOWN,
      "a futures leg with no quoted exit is written COST_UNKNOWN")
check(hasattr(tracker, "_futures_mark"),
      "the tracker marks the futures path separately from the exit price")
one_sided = book(bid=None, ask=None)
check(tracker._futures_mark(one_sided) == (5_600.0, one_sided),
      "a book with no depth still marks the path from the last trade")
check(tracker._futures_mark(None) == (None, None),
      "no futures book marks nothing at all")
check(tracker._futures_mark(book(ltp=None))[0] == 5_600.0,
      "with no last trade the mid of a two-sided book is the fallback mark")
check(tracker._futures_mark(book(ltp=None, bid=None, ask=None))[0] is None,
      "a book with neither a trade nor depth marks nothing")

# ------------------------------- 8. the MCX edge is not the cash prior
edge = mcx.market_edge("CRUDEOIL", up_bars, "BULLISH")
check(edge[0] is not None, "a measured MCX continuation produces a market edge")
check(mcx.BASIS in edge[1], "the MCX market edge carries its basis in the note")
check(mcx.market_edge("CRUDEOIL", candles(20, drift=1.0), "BULLISH")[0] is None,
      "too few samples score no MCX market edge rather than a neutral 50")
check(mcx.INSUFFICIENT_DATA in
      mcx.market_edge("CRUDEOIL", candles(20, drift=1.0), "BULLISH")[1],
      "the MCX edge refusal names the insufficiency")
check(mcx.market_edge("NIFTY", up_bars, "BULLISH") == (None, "NOT_MCX"),
      "the MCX market edge refuses a cash-index name")

obs = observation("BULLISH", q)
obs.mcx_plan = mcx_plan
scored = aplus.score(obs, market_edge_override=edge)
check(scored["market_edge_is_cash_prior"] is False,
      "an MCX row is marked as NOT scored on the cash prior")
check(scored["market_edge_basis"] == edge[1],
      "the A+ row carries the MCX basis it was scored on")
check(scored["components"]["market_edge"]["measured"] is True,
      "the overridden market edge counts as measured")
plain = aplus.score(observation("BULLISH", q, instrument="NIFTY"))
check(plain["market_edge_is_cash_prior"] is True,
      "with no override the cash-prior path is unchanged")
check(plain["market_edge_basis"] == aplus.PRIOR_BASIS,
      "an index row still reports the cash-prior basis")

# ------------------------------------------------ 9. priority tiers
check(mcx.tier("CRUDEOIL") == mcx.DEEP, "CRUDEOIL is the deep-capture MCX name")
for name in ("NATURALGAS", "GOLD", "SILVER", "COPPER"):
    check(mcx.tier(name) == mcx.SAMPLED, f"{name} is sampled, not removed")
    check(name in mcx.SAMPLED_CAPTURE, f"{name} is still in the MCX universe")
check(mcx.tier("NIFTY") is None, "a non-MCX name has no MCX tier")
check(mcx.due("CRUDEOIL", now=SIGNAL, last_capture_ts=SIGNAL - 0.1) is True,
      "a deep MCX name is due on every tick")
check(mcx.due("NIFTY", now=SIGNAL, last_capture_ts=SIGNAL - 0.1) is True,
      "the sampling gate never thins a non-MCX instrument")
check(mcx.due("GOLD", now=SIGNAL, last_capture_ts=None) is True,
      "a sampled name is due the first time it is seen")
check(mcx.due("GOLD", now=SIGNAL, last_capture_ts=SIGNAL - 1.0) is False,
      "a sampled name is thinned within its interval")
check(mcx.due("GOLD", now=SIGNAL,
              last_capture_ts=SIGNAL - mcx.SAMPLE_INTERVAL_SEC) is True,
      "a sampled name is due again after its interval")

# ------------------------------------------------ 10. separated reporting
def obs_row(instrument: str, *, futures_state: str, edge_measured: bool,
            oid: str) -> dict:
    fq = {fut.EXACT: q, fut.NEAR: near, fut.MISSING: None}[futures_state]
    o = observation("BULLISH", fq, instrument=instrument)
    o.observation_id = oid
    o.aplus = {
        "components": {"market_edge": {"measured": edge_measured}},
        "market_edge_basis": mcx.BASIS if instrument == "CRUDEOIL"
        else aplus.PRIOR_BASIS,
    }
    o.futures_plan = fut.geometry(o, mcx_plan=mcx_plan
                                 if instrument == "CRUDEOIL" else None)
    if instrument == "CRUDEOIL":
        o.mcx_plan = mcx_plan
    return o.as_dict()


rows = [
    obs_row("CRUDEOIL", futures_state=fut.EXACT, edge_measured=True, oid="c1"),
    obs_row("CRUDEOIL", futures_state=fut.NEAR, edge_measured=True, oid="c2"),
    obs_row("GOLD", futures_state=fut.MISSING, edge_measured=False, oid="g1"),
    obs_row("NIFTY", futures_state=fut.EXACT, edge_measured=False, oid="n1"),
    obs_row("RELIANCE", futures_state=fut.MISSING, edge_measured=False, oid="r1"),
]
legs = [
    leg_row("c1", schema.FUTURES, cost=schema.COST_MEASURED, net_r=0.8).as_dict(),
    leg_row("c1", schema.CE, cost=schema.COST_MEASURED, net_r=-0.4).as_dict(),
    leg_row("c2", schema.CE, cost=schema.COST_UNKNOWN, net_r=9.9).as_dict(),
]
out = rep.build(rows, legs, complete=True)

check(out["totals"][rep.OPTION]["rows"] == 5,
      "the report counts every observation on the OPTION side")
check(out["totals"][rep.FUTURES]["counts"][fut.EXACT] == 2,
      "the FUTURES capture count is measured, not inherited from option quality")
check(out["totals"][rep.OPTION]["quality"]["counts"][quality.EXACT] == 5
      and out["totals"][rep.FUTURES]["counts"][fut.MISSING] == 2,
      "option quality and futures capture are separate measurements")
check(out["totals"][rep.FUTURES]["missing_reasons"].get(fut.NO_FEED) == 2,
      "each absent futures leg is counted against a reason")
check(out["totals"]["market_edge"] == {
    "measured": 2, "missing": 3, "measured_pct": 40.0,
    "bases": {mcx.BASIS: 2},
}, "measured and missing market edge are counted apart, with the basis named")

fams = out["families"]
check(set(fams) == set(fam.FAMILIES), "the report has a section per asset family")
check(fams[fam.MCX]["rows"] == 3 and fams[fam.INDEX]["rows"] == 1
      and fams[fam.STOCK]["rows"] == 1,
      "rows are split by family and nothing is pooled across them")
check(fams[fam.MCX][rep.FUTURES]["counts"][fut.EXACT] == 1,
      "the MCX futures capture rate is the MCX rows only")
check(fams[fam.INDEX]["market_edge"]["measured"] == 0,
      "an index row with no five-year prior is counted missing, not zero")
check(fams[fam.MCX]["mcx_geometry"]["basis"] == mcx.BASIS
      and fams[fam.MCX]["mcx_geometry"]["not_validated"] is True,
      "the MCX section reports its research basis and that it is not validated")
check(fams[fam.MCX]["mcx_geometry"]["measured"] == 2,
      "the MCX section counts how many rows got a continuation target")
check("mcx_geometry" not in fams[fam.INDEX],
      "the continuation basis is not reported for a cash-index family")

rolled = rep.build([], [
    leg_row("gone-1", schema.CE, cost=schema.COST_MEASURED, net_r=0.4,
            instrument="NIFTY").as_dict(),
], complete=True)["families"][fam.INDEX]
check(rolled["legs"] == 1 and rolled["rows"] == 0,
      "a leg is filed under its own instrument's family, not under whether its "
      "observation is still in the read window")
check(rolled["legs_without_observation_in_scope"] == 1,
      "and the report says the originating observation rolled out of scope")

ss = out["totals"]["same_signal"]
check(ss["comparisons"] == 1,
      "only legs resolved from ONE observation id are a same-signal comparison")
check(ss["by_vehicle"][schema.FUTURES]["legs"] == 1
      and ss["by_vehicle"][schema.CE]["legs"] == 1,
      "each vehicle's costed legs are counted separately")
check(schema.CE in ss["by_vehicle"] and ss["by_vehicle"][schema.CE]["legs"] == 1,
      "an uncosted leg is excluded rather than counted at its LTP")
check(ss["preferred_vehicle"] == rep.WITHHELD,
      "a vehicle preference is withheld below the comparison bar")
check(str(ss["min_comparisons"]) in ss["note"],
      "the withheld note names how many comparisons are required")
check(rep.MIN_COMPARISONS > 1, "the comparison bar is more than a single row")

# A vehicle that wins the most comparisons while losing money on average is the
# 1 Sep CE column exactly: preferring it would recommend the slower loser.
many = []
for i in range(rep.MIN_COMPARISONS + 5):
    many.append(leg_row(f"m{i}", schema.CE, cost=schema.COST_MEASURED,
                        net_r=0.2 if i % 5 else -9.0).as_dict())
    many.append(leg_row(f"m{i}", schema.FUTURES, cost=schema.COST_MEASURED,
                        net_r=0.1).as_dict())
loser = rep.same_signal(many)
check(loser["comparisons"] > rep.MIN_COMPARISONS,
      "the loser case clears the comparison bar, so only economics can refuse it")
check(loser["leader_by_comparisons_won"] == schema.CE
      and loser["by_vehicle"][schema.CE]["mean_net_r"] < 0,
      "the leading vehicle here wins on count and loses on net R")
check(loser["preferred_vehicle"] == rep.WITHHELD
      and "mean net R" in loser["note"],
      "winning more comparisons while losing money is not a vehicle preference")

both_ok = [
    leg_row(f"b{i}", v, cost=schema.COST_MEASURED,
            net_r=(0.9 if v == schema.FUTURES else 0.1)).as_dict()
    for i in range(rep.MIN_COMPARISONS + 1)
    for v in (schema.FUTURES, schema.CE)
]
good = rep.same_signal(both_ok)
check(good["preferred_vehicle"] == schema.FUTURES,
      "with enough costed comparisons and positive economics a preference is published")
check(rep.same_signal([
    leg_row(f"o{i}", v, cost=schema.COST_MEASURED, net_r=0.5).as_dict()
    for i in range(rep.MIN_COMPARISONS + 1) for v in (schema.CE, schema.PE)
])["preferred_vehicle"] == rep.WITHHELD,
      "a CE-vs-PE ordering with no futures leg does not answer the vehicle question")

cov = out["evidence_coverage"]
check(cov["complete"] is True and cov["scope"] == rep.COMPLETE,
      "a whole-series read is reported as complete")
bounded = rep.build(rows, legs, complete=False, rows_dropped=9)
check(bounded["evidence_coverage"]["scope"] == rep.BOUNDED
      and bounded["evidence_coverage"]["rows_dropped"] == 9,
      "a bounded tail read says so and reports what it dropped")
check(rep.build([], [], complete=True)["totals"][rep.FUTURES]["exact_pct"] is None,
      "an empty report publishes no capture rate")

# ------------------------------------- 11. the collector is standalone
import datetime as dt  # noqa: E402

import phase21_collect_overnight as coll  # noqa: E402

check(coll._session_is_open(dt.datetime(2026, 9, 1, 11, 0)) is True,
      "a weekday late morning counts as an open session")
check(coll._session_is_open(dt.datetime(2026, 9, 1, 17, 0)) is False,
      "after the guard hour the collection may run")
check(coll._session_is_open(dt.datetime(2026, 9, 5, 11, 0)) is False,
      "a Saturday is not an open session")
check(coll._engine_is_live(1) is False,
      "a port with no engine on it reports no live capture")
check("option" in coll.CAVEAT.lower() and "underlying" in coll.CAVEAT.lower(),
      "the collector states that it collects underlying history, not options")

src = pathlib.Path("phase21_collect_overnight.py").read_text()
check("phase14_collect.py" in src,
      "the collector delegates to the existing resumable implementation")
check("resumable" in src.lower(), "the collector documents that it resumes")
for banned in ("place_order", "placeOrder", "app.state", "from app.main"):
    check(banned not in src,
          f"the standalone collector does not reach into {banned}")

# ------------------------------------------------ no order path anywhere
for mod in ("app/research/phase21/report.py", "app/research/phase17/futures.py",
            "app/research/phase17/mcx.py"):
    text = pathlib.Path(mod).read_text()
    check(not re.search(r"place_order|placeOrder|/order", text),
          f"{mod} contains no order path")

print(f"checked {CHECKS}")
if FAILURES:
    for f in FAILURES:
        print(f"FAIL: {f}")
    raise SystemExit(1)
print("phase21 smoke: OK")
