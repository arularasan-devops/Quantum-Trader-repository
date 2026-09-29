"""Smoke test: Phase 13A/13B — entry location and hold-time intelligence.

The 26 Aug Crude chart is the case this phase exists for: the BUY printed at 328
after the premium had already run, reached 348, and round-tripped to 325. Two
questions came out of it — was it bought late, and how long was it reasonable to
hold — and both are answerable only if the research refuses the easy answers.

What is proved here:

* an entry state is derived from **signal-time facts only** — premium, the plan's
  own zone, risk, T1 — and is UNKNOWN rather than a guess when they are missing;
* room to T1 is judged as a fraction of the plan's *own* entry-to-T1 distance, not
  against an absolute R floor: this engine places T1 near 0.9R by design, so an
  absolute floor invalidates a book it was never measuring;
* the hold window is a measured resolution distribution, never a probability, and
  falls back to the pooled quantiles rather than reporting a 3-row cohort;
* a pullback definition is charged for what it skipped: fill rate, missed winners
  and missed MFE, with the whole book counting a skipped signal as zero — and the
  improvement is split into the part from a better price and the part from simply
  declining trades, because on a losing book the second flatters every method;
* a time threshold reports the surviving cohort's eventual outcomes and says in as
  many words that an exit *at* the threshold is not priced;
* every artefact writes, no status reads VALIDATED, and nothing in the module set
  touches a production decision.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile

import numpy as np

from app.analysis import entry_location as el
from app.analysis import entry_quality as eq
from app.analysis import hold_window as hw
from app.analysis import label_outcomes as lo
from app.analysis import structure
from app.config import settings
from app.research.phase7 import paths
from app.research.phase7 import policies as p7
from app.research.phase13 import ledger, protection, pullback, report, timestop

checks = 0


def ok(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    assert cond, msg


# ---------------------------------------------------------- 13A entry location
# entry 100, risk 20, T1 at 118: the plan's own distance to T1 is 18 points.
plan = {"risk_points": 20.0, "entry_zone_low": 98.0, "entry_zone_high": 102.0,
        "first_target": 118.0, "planned_entry": 100.0}

at_plan = el.classify(premium=100.0, **plan)
ok(at_plan["state"] == el.BUY_NOW,
   f"the premium the plan was written at is BUY_NOW, got {at_plan['state']}")
ok(at_plan["research_only"] is True, "an entry state is research only")
ok(at_plan["room_fraction_remaining"] == 1.0,
   "at the planned entry the whole distance to T1 is still unpaid")

# T1 is 0.9R away here, so an absolute 1R room floor would invalidate this call.
ok(at_plan["room_to_target_r"] < 1.0,
   "T1 sits inside 1R by design, which is why room is judged as a fraction")

past_zone = el.classify(premium=107.0, **plan)
ok(past_zone["state"] == el.WAIT_PULLBACK,
   f"0.25R above the published zone is WAIT_PULLBACK, got {past_zone['state']}")
ok(past_zone["pullback_level"] is not None,
   "and the level to wait for is named rather than left to the reader")
ok(past_zone["pullback_level"] < 107.0, "a pullback level is below the premium")

# Two thirds of the way to T1 already paid: less than half the move is left.
late = el.classify(premium=113.0, **plan)
ok(late["state"] == el.INVALIDATED,
   f"with 27% of the plan's move left the call is INVALIDATED, got {late['state']}")
ok(el.NO_ROOM in late["reasons"], "and says the room is what invalidated it")

passed = el.classify(premium=120.0, **plan)
ok(passed["state"] == el.INVALIDATED and el.TARGET_PASSED in passed["reasons"],
   "a premium already past T1 is INVALIDATED on the target, not on the room")

unconfirmed = el.classify(premium=100.0, confirmation_present=False, **plan)
ok(unconfirmed["state"] == el.WAIT_CONFIRMATION,
   f"an explicitly absent confirmation waits, got {unconfirmed['state']}")
trapped = el.classify(premium=100.0, trap_probability=90.0, **plan)
ok(trapped["state"] == el.WAIT_CONFIRMATION,
   "a high trap probability waits rather than buying into a sweep")

ok(el.classify(premium=None, risk_points=20.0)["state"] == el.UNKNOWN,
   "with no premium the state is UNKNOWN, never a guess")
ok(el.classify(premium=100.0, risk_points=None)["state"] == el.UNKNOWN,
   "with no risk there is no scale to judge the entry on")

# The two labels must judge the same premium: entry quality's own zone distance
# is what the location state reads, so they cannot disagree about the facts.
quality = eq.classify(premium=107.0, entry_zone_low=98.0, entry_zone_high=102.0,
                      risk_points=20.0, first_target=118.0)
shared = el.classify(premium=107.0, entry_quality_state=quality["state"],
                     zone_distance_r=quality["zone_distance_r"], **plan)
ok(shared["zone_distance_r"] == quality["zone_distance_r"],
   "both labels report the same distance above the zone")
ok("outcome" not in json.dumps(shared) and "realized" not in json.dumps(shared),
   "no outcome-derived field may appear in an entry-location row")

# --------------------------------------------------------------- expiry state
ok(ledger.expiry_state("2026-08-26", "2026-08-26") == ledger.EXPIRY_DAY,
   "a session on its expiry date is EXPIRY_DAY")
ok(ledger.expiry_state("2026-08-27", "2026-08-26") == ledger.EXPIRY_DAY,
   "and a session after it is not silently NON_EXPIRY")
ok(ledger.expiry_state("2026-08-26", "2026-08-28") == ledger.PRE_EXPIRY,
   "two days out is PRE_EXPIRY")
ok(ledger.expiry_state("2026-08-26", "2026-09-30") == ledger.NON_EXPIRY,
   "a month out is NON_EXPIRY")
ok(ledger.expiry_state("2026-08-26", None) == ledger.EXPIRY_UNKNOWN,
   "an unrecorded expiry is UNKNOWN rather than assumed far away")


# ------------------------------------------------------------- 13B hold window
def resolved(sid: str, *, realized: float, mae: float, mfe: float,
             t1: float | None, stop: float | None, minutes_to_mae: float,
             resolution: float, session: str = "2026-08-26",
             option_type: str = "CE") -> dict:
    return {
        "event": "RESOLVED", "signal_id": sid, "session": session,
        "instrument": "NIFTY", "family": "INDEX", "option_type": option_type,
        "entry": 100.0, "risk_points": 20.0, "realized_r": realized,
        "mfe_r": mfe, "mae_r": mae, "mfe_capture": 0.5, "give_back_r": 0.3,
        "minutes_to_mfe": 6.0, "minutes_to_mae": minutes_to_mae,
        "minutes_to_resolution": resolution,
        "order": "TARGET_FIRST" if realized > 0 else "STOP_FIRST",
        "outcome": "TARGET" if realized > 0 else "STOP",
        "targets_reached": ({"T1": {"minutes_from_signal": t1}} if t1 is not None
                            else {}),
        "stop_event": ({"minutes_from_signal": stop} if stop is not None else None),
    }


def call(sid: str, *, session: str = "2026-08-26", action: str = "BUY",
         option_type: str = "CE", expiry: str = "2026-09-30") -> dict:
    return {
        "signal_id": sid, "session": session, "instrument": "NIFTY",
        "family": "INDEX", "option_type": option_type, "expiry": expiry,
        "signal_vehicle": "OPTIONS", "board_action": action,
        "market_state": {"spread_pct_of_premium": 1.0, "regime": "TREND"},
        "entry_plan": {"entry_price": 100.0, "risk_points": 20.0,
                       "entry_zone_low": 98.0, "entry_zone_high": 102.0,
                       "target1": 118.0, "expected_holding_minutes": 30},
        "tradability": {"status": "TRADABLE", "reasons": [], "spread": 1.0},
    }


win = {"expected_low": 5.0, "expected_high": 30.0, "long_tail": 80.0}
ok(hw.state(12.0, win) == hw.WITHIN_WINDOW, "12 min of a 5-30 window is WITHIN")
ok(hw.state(45.0, win) == hw.BEYOND_EXPECTED,
   "45 min is past the expected window but inside the long tail")
ok(hw.state(120.0, win) == hw.BEYOND_LONG_TAIL, "120 min is past the long tail")
ok(hw.state(None, win) == hw.UNKNOWN, "with no elapsed time the state is UNKNOWN")
ok(hw.state(12.0, {}) == hw.UNKNOWN, "and with no measured window, likewise")

# --------------------------------------------------- pullback: fill and refusal
# A dip of 0.3R at minute 2, T1 at minute 9: a 0.2R limit fills before the move.
filled_row = {"entry": 100.0, "risk_points": 20.0, "gross_r": 0.9, "cost_r": 0.2,
              "mae_r": -0.3, "mfe_r": 1.0, "minutes_to_mae": 2.0,
              "minutes_to_t1": 9.0, "minutes_to_resolution": 20.0,
              "target_before_stop": True}
sim = pullback.simulate(filled_row, depth_r=0.2)
ok(sim["filled"] and sim["gross_r_same_unit"] == 1.1,
   f"filling 0.2R lower adds 0.2R at the same stop, got {sim}")
ok(sim["net_r_same_unit"] == 0.9,
   "and the same round-trip cost is still charged")
ok(sim["gross_r_own_risk"] > sim["gross_r_same_unit"],
   "the smaller-risk view is reported too, but is not the comparable number")

# The same dip arriving *after* T1 is not a fill: the trade had already gone.
late_dip = dict(filled_row, minutes_to_mae=14.0)
ok(pullback.simulate(late_dip, depth_r=0.2)["filled"] is False,
   "a dip after the first milestone cannot have filled the entry")

shallow = pullback.simulate(dict(filled_row, mae_r=-0.05), depth_r=0.2)
ok(shallow["filled"] is False and shallow["missed_net_r"] == 0.7,
   f"a trade that never dipped is a miss carrying the immediate result, {shallow}")
ok(shallow["immediate_continuation"] is True,
   "and is marked as having continued without offering the retrace")
ok(pullback.simulate({"mae_r": -1.0}, depth_r=0.2)["evaluated"] is False,
   "a row with no gross R is unevaluable rather than counted as zero")

# ------------------------------------------------------- the book-level accounting
# Six winners that never dipped, six losers that all dipped first. A pullback
# method fills only the losers, so its book must not look better for skipping.
book: list[dict] = []
for i in range(12):
    loser = i % 2 == 0
    book.append({
        "session": "2026-08-26", "instrument": "NIFTY", "family": "INDEX",
        "option_type": "CE", "entry": 100.0, "risk_points": 20.0,
        "gross_r": -1.0 if loser else 1.2, "cost_r": 0.2,
        "net_r": -1.2 if loser else 1.0,
        "mae_r": -0.6 if loser else -0.02,
        "mfe_r": 0.1 if loser else 1.4,
        "minutes_to_mae": 3.0, "minutes_to_t1": None if loser else 8.0,
        "minutes_to_stop": 15.0 if loser else None,
        "minutes_to_resolution": 15.0 if loser else 12.0,
        "target_before_stop": not loser, "realized_r": -1.0 if loser else 1.2,
        "mfe_capture": 0.1 if loser else 0.8, "give_back_r": 0.3,
        "minutes_to_mfe": 4.0, "minutes_to_t2": None, "minutes_to_t3": None,
        "outcome": "STOP" if loser else "TARGET",
    })
adverse = pullback.definition("R_RETRACE_30", book, depth_r=0.3)
ok(adverse["filled"] == 6 and adverse["missed"] == 6,
   f"the dip selects the losers here: {adverse['filled']} filled")
ok(adverse["missed_winners"] == 6,
   "and every winner is counted as missed, not omitted from the comparison")
ok(adverse["dip_selects_losers"] is True,
   "the study says so rather than crediting the better fill price")
ok(adverse["book_expectancy_net_r_zero_for_missed"]
   < adverse["control_expectancy_net_r"],
   "a method that fills only the losers must lose to the immediate control")
ok(adverse["fill_rate_pct_lower_bound"] == 50.0,
   "the fill rate is reported as a lower bound, not as an achieved rate")
ok(adverse["improvement_source"] in ("BETTER_ENTRY", "DECLINED_TRADES"),
   "the improvement is attributed to a source in every case")

# --------------------------------------------------------- time thresholds
rows = [dict(r, session="2026-08-26") for r in book]
thr = timestop.threshold(rows, 10, 1)
ok(thr["exit_at_threshold_priced"] is False,
   "an exit taken at the threshold is never claimed to be priced")
ok(thr["still_open"] == 12 and thr["cohort"]["resolved"] == 6,
   f"six were still open and short of T1 at 10 min, got {thr}")
ok(thr["eventually_stopped"] == 6 and thr["eventually_reached_t1"] == 0,
   "and every one of them went on to stop rather than reach T1")
tstudy = timestop.study(rows)
ok(tstudy["time_stop_verdict"]["status"] == timestop.REQUIRES_MORE_DATA,
   "so the time-stop verdict stays REQUIRES_MORE_DATA")
ok([t["threshold_minutes"] for t in tstudy["thresholds"]]
   == list(timestop.THRESHOLDS_MIN),
   "every threshold in the spec is reported")

# ------------------------------------------------------------ protection policies
prot = protection.study(rows)
policies = {p["policy"]: p for p in prot["policies"]}
ok(set(protection.POLICIES) <= set(policies),
   "the three priceable policies are all reported")
ok(all(p["costed"] == policies["A_BASELINE"]["costed"] for p in prot["policies"]),
   "every policy is costed on the same calls: a partial pays the full round trip")
for needs in protection.NEEDS_PATH:
    ok(needs in prot["policies_needing_premium_path"],
       f"{needs} needs the minute path and must not be estimated from milestones")

# ------------------------------------- level-based entry policies (13A Part 3)
# The spec's remaining entry alternatives — recent swing retest, fair-value-gap
# retest and next-candle confirmation — live with the other seven in the Phase 7
# comparison, because they need the minute premium path the ledger has not got.
for name in ("SWING_RETEST", "FVG_RETEST", "NEXT_CANDLE_CONFIRM"):
    ok(name in p7.ENTRY_POLICIES, f"{name} is one of the compared entry policies")

highs = np.array([100.0, 101.0, 108.0, 109.0, 110.0])
lows = np.array([99.0, 100.0, 103.0, 105.0, 106.0])
bull, bear = structure.fair_value_gap(highs, lows)
ok(bull == 105.0,
   f"the most recent unfilled gap's lower edge is the retest level, got {bull}")
ok(bear is None, "and there is no bearish gap in a one-way series")
filled_gap = structure.fair_value_gap(
    np.array([100.0, 101.0, 108.0, 109.0]), np.array([99.0, 100.0, 103.0, 100.5]))
ok(filled_gap[0] is None, "a gap price has traded back through is not offered")


def buy_event(**kw: object) -> paths.BuyEvent:
    """A reconstructed BUY with one forward quote, for the entry policies."""
    base = dict(instrument="NIFTY", ts=1_756_000_000, bar=60, side="CE",
                symbol="NIFTY24SEP24000CE", strike=24000.0, spot=24010.0,
                entry=100.0, stop=90.0, target1=109.0, target2=0.0, target3=0.0,
                confidence=70.0, trade_score=60.0, regime="TRENDING", atr=20.0,
                leg_delta=0.5, vwap=24000.0, ema9=24005.0, ema20=23990.0,
                prev_mid=24000.0, breakout_level=24008.0, chain_age_sec=5,
                data_flag="FRESH")
    base.update(kw)
    ev = paths.BuyEvent(**base)  # type: ignore[arg-type]
    ev.path = paths.OptionPath(symbol=str(base["symbol"]), entry=100.0, stop=90.0,
                               target1=109.0)
    return ev


swing = buy_event(swing_low=24000.0)
entered, price, _, _ = p7.apply_entry(swing, "SWING_RETEST")
ok(not entered, "no quote reached the swing retest, so the signal is declined")
swing.path.quotes = [(1_756_000_060, 99.0), (1_756_000_120, 94.5)]
entered, price, _, delay = p7.apply_entry(swing, "SWING_RETEST")
ok(entered and price == 94.5 and delay == 2,
   f"a 10-point retest at delta 0.5 fills 5 below the signal, got {price}")
no_swing = buy_event(swing_low=0.0)
no_swing.path.quotes = [(1_756_000_060, 80.0)]
ok(not p7.apply_entry(no_swing, "SWING_RETEST")[0],
   "with no swing pivot in the window the policy declines rather than "
   "substituting another level")
above = buy_event(swing_low=24020.0)
above.path.quotes = [(1_756_000_060, 80.0)]
ok(not p7.apply_entry(above, "SWING_RETEST")[0],
   "a level above price would mean the trade is wrong, not cheaper")

fvg = buy_event(fvg_bull=24000.0)
fvg.path.quotes = [(1_756_000_060, 95.0)]
ok(p7.apply_entry(fvg, "FVG_RETEST")[0], "the gap edge maps to a premium limit")
fvg_pe = buy_event(side="PE", fvg_bear=24020.0)
fvg_pe.path.quotes = [(1_756_000_060, 95.0)]
ok(p7.apply_entry(fvg_pe, "FVG_RETEST")[0],
   "a PE reads the bearish gap above price, not the bullish one below it")
ok(not p7.apply_entry(buy_event(side="PE", fvg_bear=0.0), "FVG_RETEST")[0],
   "and declines when there is no bearish gap")

confirm = buy_event()
confirm.path.quotes = [(1_756_000_060, 101.0), (1_756_000_120, 104.0)]
entered, price, _, delay = p7.apply_entry(confirm, "NEXT_CANDLE_CONFIRM")
ok(entered and price == 101.0 and delay == 1,
   "confirmation pays the next quote — MORE than the signal price, by design")
unconfirmed = buy_event()
unconfirmed.path.quotes = [(1_756_000_060, 99.0)]
ok(not p7.apply_entry(unconfirmed, "NEXT_CANDLE_CONFIRM")[0],
   "an unconfirmed setup is skipped")
ok("NEXT_CANDLE_CONFIRM" in p7.CONFIRMATION_POLICIES,
   "and it is flagged as a policy that pays up, so its worse average entry "
   "price is not read as a failure of a retest")

winner = buy_event()
winner.path.quotes = [(1_756_000_060, 105.0), (1_756_000_120, 109.5)]
study = p7.entry_study([winner])
cost = study["SWING_RETEST"]["opportunity_cost"]
ok(study["SWING_RETEST"]["entered"] == 0 and cost["declined"] == 1,
   "the retest declined the only signal")
ok(cost["missed_winners"] == 1 and cost["control_expectancy_on_declined_r"] > 0,
   "and is charged with the winner it skipped, priced as the immediate entry "
   f"experienced it, got {cost}")
ok(all("opportunity_cost" in block for block in study.values()),
   "every entry policy reports what it gave up, not only what it filled")

# ---------------------------------------------- ledger, artefacts and honesty
tmp = tempfile.mkdtemp(prefix="qt_p13_")
original = settings.data_dir
try:
    settings.data_dir = tmp
    hw.reset_for_tests()
    journal: list[dict] = []
    outcomes: list[dict] = []
    for day, session in enumerate(("2026-08-24", "2026-08-25", "2026-08-26")):
        for i in range(12):
            sid = f"S{day}_{i}"
            loser = i % 3 == 0
            journal.append(call(sid, session=session,
                                option_type="PE" if i % 2 else "CE"))
            outcomes.append(resolved(
                sid, realized=-1.0 if loser else 1.2,
                mae=-0.6 if loser else -0.05, mfe=0.2 if loser else 1.4,
                t1=None if loser else 8.0, stop=16.0 if loser else None,
                minutes_to_mae=3.0, resolution=16.0 if loser else 12.0,
                session=session, option_type="PE" if i % 2 else "CE"))
    # A WAIT call tracked to outcome: an entry-location label on one would
    # describe an entry nobody was told to take.
    journal.append(call("WAIT1", action="WAIT"))
    outcomes.append(resolved("WAIT1", realized=1.0, mae=-0.1, mfe=1.0, t1=5.0,
                             stop=None, minutes_to_mae=2.0, resolution=10.0))
    with open(os.path.join(tmp, "signal_journal.jsonl"), "w",
              encoding="utf-8") as fh:
        for row in journal:
            fh.write(json.dumps(row) + "\n")
    with open(os.path.join(tmp, "signal_outcomes.jsonl"), "w",
              encoding="utf-8") as fh:
        for row in outcomes:
            fh.write(json.dumps(row) + "\n")

    led, cov = ledger.build()
    ok(cov["resolved_rows"] == 36,
       f"36 BUY resolutions over three sessions, got {cov['resolved_rows']}")
    ok(cov["non_buy_resolutions_excluded"] == 1,
       "and the WAIT resolution is excluded and counted, not silently dropped")
    ok(cov["labels_backfilled"] == 36 and cov["labels_recorded"] == 0,
       "these rows predate the 12A build, so every label is recomputed")
    ok(cov["rows_with_premium_chase_facts"] == 0,
       "no premium-path fact was recorded, which is what bounds Q1")
    ok(cov["chase_visible_cohorts"] == 0,
       "and no cohort holds a scorable chase sample")
    visible = [{"chase_facts_present": True, "entry_quality": q}
               for q in (["IDEAL_ENTRY"] * 12 + ["CHASED_ENTRY"] * 11
                         + ["GOOD_ENTRY"] * 4)]
    ok(ledger._chase_visible_cohorts(visible) == 2,
       "two cohorts clear the sample floor, the four-row one does not")
    ok(ledger._chase_visible_cohorts(
        [{"chase_facts_present": False, "entry_quality": "CHASED_ENTRY"}] * 50
    ) == 0, "rows without premium-path facts never count toward it")
    ok(cov["session_count"] == 3, "three sessions of the twenty §14 requires")
    ok(all(r["entry_state"] in el.STATES for r in led),
       "every row carries a known entry state")
    ok(all(r["expiry_state"] == ledger.NON_EXPIRY for r in led),
       "a September expiry on an August session is NON_EXPIRY")

    written = report.write_all()
    ok(written["complete"] and not written["artefacts_missing"],
       f"all eight artefacts must write, missing {written['artefacts_missing']}")
    for name in report.ARTEFACTS:
        path = os.path.join(tmp, name)
        ok(os.path.exists(path) and os.path.getsize(path) > 0,
           f"{name} must exist and be non-empty")

    body = json.loads(open(os.path.join(tmp, report.REPORT_JSON),
                           encoding="utf-8").read())
    ok(body["scope"] == "RESEARCH_SHADOW_PAPER_ONLY" and body["research_only"],
       "the report states its own scope")
    ok(len(body["questions"]) == 13, "all thirteen questions are answered")
    statuses = {q["status"] for q in body["questions"]}
    ok("VALIDATED" not in statuses,
       f"no question may read VALIDATED below the §14 gates, saw {statuses}")
    ok(statuses <= {lo.OBSERVATION, lo.IN_SAMPLE_ONLY, lo.INSUFFICIENT,
                    "REQUIRES_MORE_DATA", "HYPOTHESIS"},
       f"every status is one of the permitted labels, saw {sorted(statuses)}")
    ok(body["validation"]["in_sample_promotion"] is False,
       "in-sample promotion is refused in the body itself")
    q1 = next(q for q in body["questions"] if q["question"].startswith("1."))
    ok(q1["status"] == "REQUIRES_MORE_DATA",
       "the chase question cannot be answered from labels backfilled at zero "
       f"distance, got {q1['status']}")
    q6 = next(q for q in body["questions"] if q["question"].startswith("6."))
    ok(q6["status"] == "REQUIRES_MORE_DATA",
       "and a time stop cannot be priced without the minute premium path")
    for frozen in ("chase guard", "production stop", "production exits",
                   "no option writing, no short calls or puts, no credit spreads",
                   "broker execution"):
        ok(any(frozen in item for item in body["production_unchanged"]),
           f"'{frozen}' must be listed as unchanged")

    md = open(os.path.join(tmp, report.REPORT_MD), encoding="utf-8").read()
    ok("REQUIRES_MORE_DATA" in md,
       "the markdown carries the honest statuses, not only the numbers")
    ok("rows where a chase could be seen at all: 0" in md,
       "and states on its face how much of the book can be judged")
finally:
    settings.data_dir = original
    hw.reset_for_tests()
    shutil.rmtree(tmp, ignore_errors=True)

print(f"PHASE 13A/13B SMOKE PASSED ({checks} checks)")
