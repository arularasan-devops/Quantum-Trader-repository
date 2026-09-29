"""Smoke test: Phase 12A Track B — entry quality, tradability reasons, cohorts.

The 26 Aug session lost ₹1,09,909 in the shadow book, and 46 of the 120 costed
closes carried a research label that predicted it (UNTRADABLE 11.8% win,
REJECT_SPREAD 13.8%). Track B exists to make that measurable per label, per
reason and per family — and to keep it honest below §19's sample floors.

What is proved here:

* entry quality is classified from **signal-time facts only** — an entry zone,
  the premium, its own recent range, ATR — never from a bar after the signal;
* a chased entry worsens the tradability reason list and the A+ label, and does
  so without touching the production chase guard;
* the label→outcome join is by signal_id, net R charges that call's own recorded
  spread, and a call with no book gets no net number instead of a gross one;
* reason cohorts overlap, and the report says so rather than forcing one cause;
* every cohort below MIN_COHORT reads INSUFFICIENT_SAMPLE, and nothing anywhere
  in the body reads VALIDATED;
* the §17 artefacts all write, and the §18 questions the data cannot answer read
  REQUIRES_MORE_DATA / INSUFFICIENT_SAMPLE with a shortfall.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile

from app.analysis import a_plus_shadow
from app.analysis import entry_quality as eq
from app.analysis import label_outcomes as lo
from app.analysis import futures_feed_audit, phase12a_report as p12a
from app.analysis import tradability
from app.config import settings

checks = 0


def ok(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    assert cond, msg


# ------------------------------------------------------------ entry quality
inside = eq.classify(premium=100.0, entry_zone_low=98.0, entry_zone_high=102.0,
                     risk_points=20.0, first_target=130.0)
ok(inside["state"] == eq.IDEAL, f"inside its own zone is IDEAL, got {inside['state']}")
ok(inside["zone_distance_r"] == 0.0, "a premium inside the zone is 0.0R away")
ok(inside["research_only"] is True, "entry quality is research only")

near = eq.classify(premium=104.0, entry_zone_low=98.0, entry_zone_high=102.0,
                   risk_points=20.0)
ok(near["state"] == eq.GOOD, f"0.1R past the zone is GOOD, got {near['state']}")

acceptable = eq.classify(premium=107.0, entry_zone_low=98.0,
                         entry_zone_high=102.0, risk_points=20.0)
ok(acceptable["state"] == eq.ACCEPTABLE,
   f"0.25R past the zone is ACCEPTABLE, got {acceptable['state']}")

far = eq.classify(premium=120.0, entry_zone_low=98.0, entry_zone_high=102.0,
                  risk_points=20.0)
ok(far["state"] in (eq.CHASED, eq.SEVERELY_CHASED),
   f"0.9R past the zone is chased, got {far['state']}")

miles = eq.classify(premium=140.0, entry_zone_low=98.0, entry_zone_high=102.0,
                    risk_points=20.0)
ok(miles["state"] == eq.SEVERELY_CHASED,
   f"1.9R past the zone is SEVERELY_CHASED, got {miles['state']}")

blind = eq.classify(premium=140.0)
ok(blind["state"] == eq.UNKNOWN,
   "with no zone and no corroborating fact the state is UNKNOWN, never a guess")

# Corroborating evidence may worsen a label but must not invent a chase where
# the plan's own zone says the entry was fine.
corroborated = eq.classify(premium=100.0, entry_zone_low=98.0,
                           entry_zone_high=102.0, risk_points=20.0,
                           premium_at_setup=60.0, premium_recent_low=58.0,
                           premium_recent_high=101.0)
ok(corroborated["state"] != eq.IDEAL,
   "a 67% premium expansion before the signal must worsen an in-zone entry")
ok(corroborated["premium_expansion_pct"] is not None,
   "the expansion that worsened it is reported as the evidence")

# No future bar may reach this classifier: its inputs are signal-time only.
ok("outcome" not in json.dumps(inside) and "realized" not in json.dumps(inside),
   "no outcome-derived field may appear in an entry-quality row")

# --------------------------------------------------- tradability + A+ wiring
chased = tradability.assess(
    "NIFTY", premium=100.0, bid=99.5, ask=100.5, risk_points=20.0,
    first_target=130.0, volume=50_000, open_interest=500_000,
    quote_age_sec=1.0, delta=0.45, iv=14.0, entry_quality=eq.SEVERELY_CHASED)
ok(tradability.PREMIUM_EXPANDED in chased["reasons"],
   "a severely chased entry is recorded as PREMIUM_ALREADY_EXPANDED")
ok(chased["entry_quality"] == eq.SEVERELY_CHASED,
   "the entry state that produced the reason is recorded beside it")
ok(chased["delta"] == 0.45 and chased["iv"] == 14.0,
   "delta and IV are carried on the tradability row for the §14 journal")

clean = tradability.assess(
    "NIFTY", premium=100.0, bid=99.5, ask=100.5, risk_points=20.0,
    first_target=130.0, volume=50_000, open_interest=500_000,
    quote_age_sec=1.0, delta=0.45, iv=14.0, entry_quality=eq.IDEAL)
ok(tradability.PREMIUM_EXPANDED not in clean["reasons"],
   "an ideal entry is not flagged as expanded")
ok(clean["status"] == tradability.TRADABLE,
   f"a 1% spread with room and size is TRADABLE, got {clean['status']}")

wide_t1 = tradability.assess(
    "NIFTY", premium=100.0, bid=96.0, ask=104.0, risk_points=20.0,
    first_target=104.0, volume=50_000, open_interest=500_000, quote_age_sec=1.0)
ok(tradability.SPREAD_VS_TARGET in wide_t1["reasons"],
   "a spread of 8 against a 4-point T1 distance is SPREAD_LARGE_VS_T1_DISTANCE")
ok(wide_t1["spread_over_t1_distance"] is not None,
   "the ratio that produced it is reported")

grade = a_plus_shadow.classify("NIFTY", tradability_row=clean, score=90.0,
                               plan_actionable=True, delta=0.45,
                               entry_state=eq.SEVERELY_CHASED)
ok(grade["label"] == a_plus_shadow.REJECT_ENTRY,
   f"a severely chased entry is REJECT_ENTRY, got {grade['label']}")
ok(a_plus_shadow.SEVERELY_CHASED in grade["reasons"],
   "the A+ rejection names the entry state it rejected on")
ok(grade["executable"] is False and grade["research_only"] is True,
   "an A+ label is never executable")

# The same call with a clean entry must not be rejected on entry: the chase
# label is the only thing that changed.
ok(a_plus_shadow.classify("NIFTY", tradability_row=clean, score=90.0,
                          plan_actionable=True, delta=0.45,
                          entry_state=eq.IDEAL)["label"]
   != a_plus_shadow.REJECT_ENTRY,
   "an ideal entry with the same book is not rejected on the entry check")

# ------------------------------------------------------------ label cohorts
def journal_row(sid: str, *, status: str, reasons: list[str], label: str,
                state: str, family: str = "INDEX", option_type: str = "CE",
                spread: float | None = 1.0) -> dict:
    return {
        "signal_id": sid,
        "session": "2026-08-26",
        "instrument": "NIFTY",
        "family": family,
        "option_type": option_type,
        "signal_vehicle": "OPTIONS",
        "market_state": {"spread_pct_of_premium": 1.0},
        "entry_plan": {"entry_price": 100.0, "risk_points": 20.0},
        "tradability": {"status": status, "reasons": reasons, "spread": spread},
        "a_plus_shadow": {"label": label, "reasons": ["X"] if label !=
                          a_plus_shadow.A_PLUS else []},
        "entry_quality": {"state": state},
    }


def outcome_row(sid: str, realized: float, *, mfe: float = 1.0,
                order: str = "TARGET_FIRST") -> dict:
    return {
        "event": "RESOLVED",
        "signal_id": sid,
        "session": "2026-08-26",
        "instrument": "NIFTY",
        "family": "INDEX",
        "entry": 100.0,
        "risk_points": 20.0,
        "realized_r": realized,
        "mfe_r": mfe,
        "mae_r": -0.3,
        "mfe_capture": 0.5,
        "give_back_r": 0.4,
        "minutes_to_mfe": 5.0,
        "minutes_to_resolution": 20.0,
        "order": order,
        "targets_reached": {"T1": {"minutes_from_signal": 8.0}},
        "stop_event": None,
    }


journal: list[dict] = []
outcomes: list[dict] = []
for i in range(12):
    sid = f"U{i}"
    journal.append(journal_row(sid, status=tradability.UNTRADABLE,
                               reasons=[tradability.SPREAD_EATS_RISK,
                                        tradability.THIN],
                               label=a_plus_shadow.REJECT_SPREAD,
                               state=eq.SEVERELY_CHASED))
    outcomes.append(outcome_row(sid, -1.0, order="STOP_FIRST"))
for i in range(12):
    sid = f"T{i}"
    journal.append(journal_row(sid, status=tradability.TRADABLE, reasons=[],
                               label=a_plus_shadow.A_PLUS, state=eq.IDEAL,
                               option_type="PE"))
    outcomes.append(outcome_row(sid, 1.5))
# One call with no recorded book: it must have no net number at all.
journal.append(journal_row("NOBOOK", status=tradability.UNKNOWN, reasons=[],
                           label=a_plus_shadow.UNKNOWN, state=eq.UNKNOWN,
                           spread=None))
outcomes.append(outcome_row("NOBOOK", -1.0))
# A futures row in the same journal must never be pooled into option cohorts.
fut = journal_row("FUT1", status=tradability.TRADABLE, reasons=[],
                  label=a_plus_shadow.A_PLUS, state=eq.IDEAL)
fut["signal_vehicle"] = "FUTURES"
journal.append(fut)

study = lo.study(journal, outcomes)
ok(study["resolved"] == 25, f"25 resolved option rows, got {study['resolved']}")
ok(study["labels_missing"] == 0, "every resolved row found its journalled labels")
ok(study["baseline"]["costed"] == 24,
   f"the bookless call has no net number, got costed={study['baseline']['costed']}")
ok(study["baseline"]["uncosted"] == 1, "and it is counted as uncosted, not zero")

by_trad = {row["cohort"]: row for row in study["by_tradability"]}
un = by_trad[f"tradability={tradability.UNTRADABLE}"]
tr = by_trad[f"tradability={tradability.TRADABLE}"]
ok(un["resolved"] == 12 and tr["resolved"] == 12,
   "cohorts are joined signal_id -> outcome, one row each")
ok(un["win_rate_net_pct"] == 0.0 and tr["win_rate_net_pct"] == 100.0,
   "the cohort economics follow the joined outcomes")
ok(un["expectancy_net_r"] < tr["expectancy_net_r"],
   "the UNTRADABLE cohort is worse than the TRADABLE one on net R")
ok(un["profit_factor_net"] == 0.0, "a cohort that never won has a zero factor")
ok(tr["profit_factor_net"] is None,
   "a cohort that never lost has no profit factor rather than an infinite one")
ok(un["reached_half_r_then_lost"] == 12,
   "a cohort that ran +1R and died is counted as giveback, not as a loss only")
ok(by_trad[f"tradability={tradability.UNKNOWN}"]["status"] == lo.INSUFFICIENT,
   "a 1-row cohort is INSUFFICIENT_SAMPLE, never a percentage")

reasons = {row["cohort"]: row["resolved"] for row in study["by_untradable_reason"]}
ok(reasons[f"tradability_reasons~{tradability.SPREAD_EATS_RISK}"] == 12
   and reasons[f"tradability_reasons~{tradability.THIN}"] == 12,
   "reason cohorts overlap: one call with two reasons is counted in both")
ok(sum(reasons.values()) > un["resolved"],
   "so reason counts exceed the cohort, which the notes state explicitly")
ok(any("overlap" in n for n in study["notes"]),
   "the overlap is disclosed in the body, not left for the reader to infer")

by_eq = {row["cohort"]: row for row in study["by_entry_quality"]}
ok(by_eq[f"entry_quality={eq.SEVERELY_CHASED}"]["resolved"] == 12,
   "entry-quality cohorts are built from the recorded state")
ok(by_eq[f"entry_quality={eq.IDEAL}"]["expectancy_net_r"]
   > by_eq[f"entry_quality={eq.SEVERELY_CHASED}"]["expectancy_net_r"],
   "and are comparable against each other on net R")

by_type = {row["cohort"]: row["resolved"] for row in study["by_option_type"]}
ok(by_type["option_type=CE"] == 13 and by_type["option_type=PE"] == 12,
   "CE and PE are reported separately: both are long options, never pooled")

ok(study["a_plus_vs_baseline"]["status"] in (lo.OBSERVATION, lo.IN_SAMPLE_ONLY),
   "the A+ comparison carries a status")
ok("in-sample" in study["a_plus_vs_baseline"]["note"],
   "and says in as many words that it is in-sample by construction")
statuses_seen = {row["status"] for val in study.values() if isinstance(val, list)
                 for row in val if isinstance(row, dict) and "status" in row}
ok(statuses_seen and "VALIDATED" not in statuses_seen,
   f"no cohort status may read VALIDATED below the §19 gates, "
   f"saw {sorted(statuses_seen)}")
ok(statuses_seen <= {lo.OBSERVATION, lo.IN_SAMPLE_ONLY, lo.INSUFFICIENT},
   f"every cohort status is one of the permitted labels, "
   f"saw {sorted(statuses_seen)}")
ok(study["session_shortfall"] == lo.MIN_SESSIONS - 1,
   f"one session is {lo.MIN_SESSIONS - 1} short of §19")
ok(study["research_only"] is True, "the whole study is research only")

# ------------------------------------------------------- §17 artefacts, §18
tmp = tempfile.mkdtemp(prefix="qt_p12b_")
original = settings.data_dir
try:
    settings.data_dir = tmp
    with open(os.path.join(tmp, "signal_journal.jsonl"), "w",
              encoding="utf-8") as fh:
        for row in journal:
            fh.write(json.dumps(row) + "\n")
    with open(os.path.join(tmp, "signal_outcomes.jsonl"), "w",
              encoding="utf-8") as fh:
        for row in outcomes:
            fh.write(json.dumps(row) + "\n")

    # A populated feed audit, because the report has to aggregate real per-
    # contract rows and not only the empty audit a fresh process starts with.
    futures_feed_audit.reset_for_tests()
    futures_feed_audit.observe("NIFTY", {
        "instrument": "NIFTY", "source": "REST", "age_seconds": 1_800.0,
        "subscription_state": "NOT_SUBSCRIBED", "ticks": 0,
    }, now=1_787_700_000.0)
    audit = futures_feed_audit.report()
    ok(audit["instruments"] == 1 and len(audit["rows"]) == 1,
       "the audit reports one contract row for one observed contract")

    written = p12a.write_all("2026-08-26")
    ok(written["complete"] and not written["artefacts_missing"],
       f"all seven §17 artefacts must write, missing {written['artefacts_missing']}")
    for name in p12a.ARTEFACTS:
        path = os.path.join(tmp, name)
        ok(os.path.exists(path) and os.path.getsize(path) > 0,
           f"{name} must exist and be non-empty")

    body = json.loads(open(os.path.join(tmp, p12a.REPORT_JSON),
                           encoding="utf-8").read())
    ok(body["phase"] == "12A", "the report names its phase")
    ok(body["records_omitted"] == 0 and body["report_valid"] is True,
       "records_omitted must be 0 and the report valid")
    ok(body["records_seen"] == body["records_processed"],
       "one recorded session: seen and processed agree")
    ok(len(body["questions"]) == 13, f"§18 has 13 questions, got "
       f"{len(body['questions'])}")
    statuses = {q["question"][:3]: q["status"] for q in body["questions"]}
    ok(statuses["2. "] == p12a.REQUIRES_MORE_DATA,
       "Q2 cannot be answered without a live session")
    ok(statuses["9. "] == p12a.REQUIRES_MORE_DATA,
       "Q9 cannot claim an out-of-sample edge on one session")
    ok(statuses["13."] == p12a.REQUIRES_MORE_DATA,
       "Q13 must answer 'not enough evidence' on one session")
    q13 = [q for q in body["questions"] if q["question"].startswith("13.")][0]
    ok(q13["stated_answer"] is False,
       "and the stated answer to 'is there enough for a production change' is No")
    ok(q13["shortfall"]["sessions"] == lo.MIN_SESSIONS - 1,
       "with the session shortfall stated")
    for q in body["questions"]:
        ok(q["status"] in (p12a.OBSERVATION, p12a.IN_SAMPLE_ONLY,
                           p12a.INSUFFICIENT, p12a.REQUIRES_MORE_DATA),
           f"{q['question'][:20]} carries a permitted honesty label, "
           f"got {q['status']}")
    ok("VALIDATED" not in {q["status"] for q in body["questions"]},
       "no question may read VALIDATED below the §19 gates")
    ok(body["validation"]["in_sample_promotion"] is False,
       "in-sample promotion is refused in the body itself")
    for frozen in ("chase guard", "exits", "broker execution",
                   "the 90s futures refusal threshold"):
        ok(any(frozen in item for item in body["production_unchanged"]),
           f"'{frozen}' must be listed as unchanged")

    feed = body["futures_feed_audit"]
    ok(feed["instruments"] == 1,
       "the feed section carries the observed contract count")
    ok(feed["causes_all_instruments"].get("TOKEN_NOT_SUBSCRIBED") == 1,
       f"and names the cause the audit observed, got "
       f"{feed['causes_all_instruments']}")

    md = open(os.path.join(tmp, p12a.REPORT_MD), encoding="utf-8").read()
    ok("records omitted: 0" in md, "the markdown states its coverage")
    ok("REQUIRES_MORE_DATA" in md,
       "and carries the honest statuses rather than only the numbers")
finally:
    settings.data_dir = original
    shutil.rmtree(tmp, ignore_errors=True)

print(f"PHASE 12A TRACK B SMOKE PASSED ({checks} checks)")
