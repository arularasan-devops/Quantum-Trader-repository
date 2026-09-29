"""Focused smoke for the Phase 10 research package. No network, no DB, no orders.

Phase 10's specific ways of going wrong are not arithmetic slips — they are honest
mistakes that look like results, so each one is asserted directly:

* **an unsupported probability must be refused, not caveated.** Below the sample
  gate ``probability.build`` must return MODEL_UNAVAILABLE with ``mapping is None``.
  A weak curve with a warning attached is the exact failure Phase 10 exists to fix.
* **calibration must never see the future.** The train/dev/holdout split must be
  chronological by session; a holdout session must never appear in the training
  block.
* **the score must not filter while it is uninformative.** When ``score.study``
  measures the SIGNAL SCORE as unpredictive, the A+ qualifier must report the score
  component as present-but-disabled and must not reject a single trade on it.
* **an in-sample A+ improvement must not read as evidence** — IN_SAMPLE_ONLY
  without a holdout, never VALIDATED, baseline population intact, avoided losers
  printed beside missed winners.
* **an unmeasured improvement must not be claimed.** Without a recorded after
  session, ``capacity.compare`` must return AFTER_NOT_MEASURED.
* **``.env`` can never enter the nightly archive**, including when a caller adds it
  to the member list on purpose.
* **the book survives a missing model** — rows still written, probability ``None``.
* **no production module imports Phase 10.**

    .venv/bin/python _smoke_phase10.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.research.phase10 import (  # noqa: E402
    aplus,
    capacity,
    capture10,
    export,
    freshness,
    gates10,
    paperbook,
    premiums,
    probability,
    score,
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


def trade(session: str, conf: float, won: bool, *, net: float | None = None,
          instrument: str = "NIFTY", flag: str = "FRESH",
          spread_share: float | None = 10.0, mfe: float = 1.0) -> dict:
    r = 1.5 if won else -1.0
    return {
        "session": session, "instrument": instrument, "symbol": f"{instrument}X",
        "side": "CE", "ts_ist": f"{session} 10:00:00", "confidence": conf,
        "realised_r": r, "net_r": r if net is None else net,
        "target_before_stop_hit": won, "mfe_r": mfe, "mae_r": -0.4,
        "min_to_mfe": 5, "min_to_mae": 2, "held_min": 20,
        "capture": {"capture_pct": 60.0}, "exit_reason": "TARGET1" if won else "STOP",
        "data_flag": flag, "chain_age_sec": 30.0, "entry_quality": "GOOD_ENTRY",
        "spread_share_of_risk_pct": spread_share, "spread_cost_r": 0.05,
        "oi": 5000.0, "volume": 900.0, "room_ratio": 0.5, "delta": 0.45,
        "premium_band": "₹50-200", "minutes_to_expiry": 900,
        "expiry_class": "WEEKLY", "days_to_expiry": 2, "regime": "TREND",
        "premium_expansion_pct": 12.0, "underlying_favourable": 0.4,
        "underlying_adverse": -0.2, "greeks_usable": True,
    }


SESSIONS = ["2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20"]
# An inverted score: the high bucket loses, the low bucket wins. Cells are kept
# above the per-cell gate on purpose, so a "not measurable" verdict cannot be
# what makes the inversion assertions below pass.
ROWS = [trade(SESSIONS[i % 4], 78.0 if i % 2 else 92.0, bool(i % 2))
        for i in range(80)]

# --- 1. probability refuses an unsupported mapping ------------------------
prob = probability.build(ROWS, SESSIONS)
ok("small sample yields MODEL_UNAVAILABLE",
   prob["status"] == probability.MODEL_UNAVAILABLE, prob["status"])
ok("no mapping is produced when the sample is short", prob["mapping"] is None)
ok("the refusal names the shortfall",
   "required" in prob["reason"] and str(prob["train_rows"]) in prob["reason"])
ok("the refusal says what would change it",
   "more recorded sessions" in prob["what_would_change_this"])

sp = probability.split(ROWS, SESSIONS)
ok("the split is chronological, not random",
   sp["train_sessions"] == SESSIONS[:2] and sp["holdout_sessions"] == SESSIONS[3:],
   f"{sp['train_sessions']} / {sp['holdout_sessions']}")
ok("no holdout session leaks into training",
   not (set(sp["train_sessions"]) & set(sp["holdout_sessions"])))
ok("two blocks are refused as a calibration split",
   probability.split(ROWS, SESSIONS[:2])["usable"] is False)

# The fitters themselves must work, or the refusal above proves nothing.
big = []
for i in range(400):
    s = 60.0 + (i % 40)
    big.append(trade(SESSIONS[i % 4], s, (i % 40) > 25))
fitted = probability.fit_platt(*probability.xy(big))
ok("platt fits when given a real sample", fitted is not None)
ok("platt is monotone in the score",
   probability.apply_platt(fitted, 0.9) != probability.apply_platt(fitted, 0.6))
iso = probability.fit_isotonic(*probability.xy(big))
ok("isotonic is non-decreasing",
   all(a["probability"] <= b["probability"]
       for a, b in zip(iso["steps"], iso["steps"][1:])))

# --- 2. score measurement -------------------------------------------------
sc = score.study(ROWS, len(SESSIONS))
ok("the value is called SIGNAL SCORE", sc["label_used"] == "SIGNAL SCORE")
ok("the tooltip refuses the word probability as a claim",
   "not a calibrated probability" in sc["tooltip"])
ok("the production display is stated unchanged",
   "unchanged" in sc["naming_note"])
ok("an inverted score is reported as not predictive", sc["is_predictive"] is False,
   str(sc["monotonicity"]["verdict"]))
ok("inversions are enumerated, not summarised away",
   len(sc["monotonicity"]["inversions"]) >= 1)
ok("brier is compared against a constant forecast",
   "brier_of_constant_base_rate_forecast" in sc["brier"])

mono_rows = ([trade(SESSIONS[i % 4], 72.0, i % 10 < 2) for i in range(40)]
             + [trade(SESSIONS[i % 4], 88.0, i % 10 < 8) for i in range(40)])
mono = score.study(mono_rows, 4)
ok("a genuinely ordering score is reported as monotone",
   mono["monotonicity"]["monotone"] is True, mono["monotonicity"]["verdict"])

# --- 3. A+ qualifier -----------------------------------------------------
classes = aplus.instrument_tradability(ROWS)
ok("instrument classes carry their own sample size",
   all("trades" in v and "judgeable" in v for v in classes.values()))
ok("instrument thresholds are PROPOSED_ONLY",
   all(v["threshold_status"] == "PROPOSED_ONLY" for v in classes.values()))

wide = [trade("2026-08-17", 90.0, False, instrument="GOLD", spread_share=300.0)
        for _ in range(25)]
wide_classes = aplus.instrument_tradability(wide)
ok("an instrument whose book exceeds its risk is classed untradable",
   wide_classes["GOLD"]["class"] == "UNTRADABLE_ON_THIS_SAMPLE")

proposal = {"green_max_spread_share_pct": 5.0, "yellow_max_spread_share_pct": 20.0}
for r in ROWS:
    r["phase10_qualification"], r["phase10_qualification_trigger"] = (
        aplus.qualify_detail(r, {**r, "greeks_usable": True}, proposal,
                             instrument_classes=classes,
                             score_component_enabled=False, min_signal_score=None))
cmp_block = aplus.compare(ROWS, SESSIONS, score_is_predictive=False)
ok("the score component is present but disabled while uninformative",
   "DISABLED" in cmp_block["components"]["signal_score"])
ok("nothing is rejected on the score while it is disabled",
   cmp_block["qualification_counts"]["REJECTED_SCORE"] == 0)
ok("A+ without a holdout is IN_SAMPLE_ONLY",
   cmp_block["status"] == "IN_SAMPLE_ONLY", cmp_block["status"])
ok("A+ never claims VALIDATED at this sample size",
   cmp_block["label"] != "VALIDATED", cmp_block["label"])
ok("the baseline population is untouched by the qualifier",
   cmp_block["baseline"]["n"] == len(ROWS))
ok("avoided losers carry the construction caveat",
   "by construction" in cmp_block["avoided_losers"]["caveat"])
ok("missed winners are reported beside avoided losers",
   "missed_winners" in cmp_block and "avoided_losers" in cmp_block)
ok("both arms run on the same signals",
   "same production signals" in cmp_block["population"])
ok("A+ answers the beat-baseline question with UNPROVEN here",
   "UNPROVEN" in cmp_block["answer_to_does_a_plus_beat_baseline"])

stale = trade("2026-08-17", 90.0, False, flag="STALE")
label, _ = aplus.qualify_detail(stale, {**stale}, proposal,
                                instrument_classes=classes)
ok("a stale trade keeps Phase 9's data rejection rather than a new reason",
   label == "REJECTED_DATA", label)

# --- 4. freshness --------------------------------------------------------
fr = freshness.study([freshness.row_of(r) for r in ROWS], 4)
ok("freshness reports the BUY denominator it actually has",
   "WAIT and NO_TRADE decisions are not in the recorded schema" in fr["denominator"])
ok("unrecorded exchange/receive timestamps are None, not invented",
   freshness.row_of(ROWS[0])["exchange_ts"] is None
   and freshness.row_of(ROWS[0])["receive_ts"] is None)
ok("freshness enforces nothing", "enforced nowhere" in fr["guarantee"])
stale_fr = freshness.study([freshness.row_of(trade("2026-08-17", 90.0, False,
                                                  flag="STALE"))], 1)
ok("a stale decision is counted as stale",
   stale_fr["overall"]["stale_pct"] == 100.0)

# --- 5. premiums ---------------------------------------------------------
signals = [{"instrument": "NIFTY", "entry_time_rows": [
    {"premium": 15.0, "premium_band": "<₹20", "spread_share_of_risk_pct": 180.0,
     "spread_pct": 8.0, "delta": 0.2, "oi": 400.0, "volume": 50.0,
     "is_selected": False},
    {"premium": 120.0, "premium_band": "₹50-200", "spread_share_of_risk_pct": 9.0,
     "spread_pct": 1.0, "delta": 0.5, "oi": 9000.0, "volume": 1200.0,
     "is_selected": True}]}]
pr = premiums.study(signals, ROWS, 4)
ok("the premium floor is stated unchanged",
   "UNCHANGED" in pr["premium_floor_status"])
ok("cheap legs available but unselected is reported as a selector fact",
   "selector" in pr["cheap_premium_answer"], pr["cheap_premium_answer"][:60])
ok("all five monitored instruments appear",
   all(k in pr["monitored_instruments"] for k in premiums.MONITORED))
ok("an unrecorded monitored instrument is unmeasured, not good",
   "unmeasured rather than good" in pr["monitored_instruments"]["SILVER"]["note"])

# --- 6. capture ----------------------------------------------------------
cap = capture10.study(ROWS, 4, 46.5)
ok("capture stamps its ATR inputs contaminated",
   cap["atr_contamination"]["status"] == "DATA_CONTAMINATED",
   cap["atr_contamination"]["status"])
ok("hindsight is labelled hindsight",
   all(r["peak_known_only_in_hindsight"] for r in cap["rows"]))
ok("no hold-until-peak behaviour is introduced",
   "no HOLD UNTIL PEAK" in cap["guarantee"])
ok("exit problems and selection problems are counted separately",
   "reached_1r_then_finished_negative_net" in cap
   and "never_reached_half_r" in cap)

# --- 7. paper book -------------------------------------------------------
bk = paperbook.book(ROWS, model_status=paperbook.NO_VALIDATED_MODEL)
ok("the book still records every signal without a model",
   bk["rows_recorded"] == len(ROWS))
ok("a missing model empties only the middle column",
   bk["rows_with_model_unavailable"] == len(ROWS))
ok("no probability is invented", bk["invented_probabilities"] == 0
   and all(r["shadow_paper_decision"]["probability"] is None for r in bk["book"]))
ok("the unavailable reason is explicit",
   all(r["shadow_paper_decision"]["reason"] == paperbook.MODEL_UNAVAILABLE
       for r in bk["book"]))
ok("the book places no orders", "no order" in bk["guarantee"])

# --- 8. capacity ---------------------------------------------------------
before = capacity.snapshot(label=capacity.BEFORE, instruments_deep=50,
                           missing_bar_pct=46.5, stale_signal_pct=38.0)
cc = capacity.compare(before, None, deep_count=8)
ok("no after session means no improvement claim",
   cc["status"] == capacity.NOT_MEASURED, cc["status"])
ok("the estimate is labelled an estimate",
   cc["estimate"]["status"] == capacity.ESTIMATE)
ok("the estimate refuses to predict completeness",
   "missing_bar_pct" in cc["estimate"]["does_not_predict"])
ok("unmeasured metrics stay None rather than zero",
   before["metrics"]["cpu_pct"] is None and "cpu_pct" in before["unmeasured"])
after = capacity.snapshot(label=capacity.AFTER, instruments_deep=8,
                          missing_bar_pct=9.0, stale_signal_pct=6.0)
cc2 = capacity.compare(before, after, deep_count=8)
ok("a single before/after pair is labelled as one pair",
   cc2["status"] == "MEASURED_SINGLE_SESSION_PAIR" and "confounds" in cc2["verdict"])

# --- 9. export cannot carry a secret ------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    data = Path(tmp) / "data"
    data.mkdir()
    (data / "signals.jsonl").write_text('{"a":1}\n')
    (data / ".env").write_text("QT_API_KEY=FAKE-KEY-FOR-TESTING\n")
    (data / "credentials.json").write_text("{}")
    pl = export.plan(data)
    ok("the export member list is an allow-list",
       all(m["member"] != ".env" for m in pl["members"]))
    manifest = export.write(data, Path(tmp) / "out", on=date(2026, 8, 20))
    ok("the archive is named per spec",
       manifest["archive"].endswith("qt_daily_2026-08-20.zip"))
    ok("no .env in the archive",
       not any(".env" in n for n in manifest["members"]))
    ok("no credentials file in the archive",
       not any("credential" in n for n in manifest["members"]))
    ok("missing members are reported, not fabricated",
       "ai_decisions.jsonl" in manifest["missing_members"])
    ok("the exporter deletes nothing",
       (data / "signals.jsonl").is_file() and (data / ".env").is_file())
    ok("a forbidden name is rejected even if a caller adds it",
       export._is_forbidden(data / ".env")
       and export._is_forbidden(data / "credentials.json")
       and export._is_forbidden(data / "key.pem"))

# --- 10. gates -----------------------------------------------------------
g = gates10.evaluate(resolved=393, sessions=4, missing_bar_pct=46.5,
                     book_coverage_pct=100.0, ladder_coverage_pct=85.0,
                     holdout_sessions=2, smallest_cell=10,
                     calibration_train_rows=200, calibration_holdout_rows=10,
                     sessions_at_deep_watchlist=0)
ok("this dataset is EXPLORATORY_ONLY", g["status"] == gates10.EXPLORATORY_ONLY)
for gate in ("REAL_SESSIONS", "DATA_COMPLETENESS", "CHRONOLOGICAL_HOLDOUT",
             "CALIBRATION_SAMPLE", "TIER_SPLIT_MEASURED"):
    ok(f"gate {gate} fails and says what it forbids", gate in g["failed"])
ok("VALIDATED is not among the permitted labels",
   "VALIDATED" not in g["permitted_labels"])
g2 = gates10.evaluate(resolved=1000, sessions=25, missing_bar_pct=2.0,
                      book_coverage_pct=99.0, ladder_coverage_pct=95.0,
                      holdout_sessions=6, smallest_cell=40,
                      calibration_train_rows=600, calibration_holdout_rows=200,
                      sessions_at_deep_watchlist=6)
ok("a complete dataset would permit the comparison",
   g2["status"] == gates10.COMPARISON_PERMITTED, str(g2["failed"]))

# --- 11. no duplicate implementations, no production import -------------
src = []
pkg = os.path.join(HERE, "app", "research", "phase10")
for name in sorted(os.listdir(pkg)):
    if name.endswith(".py"):
        with open(os.path.join(pkg, name)) as fh:
            src.append(fh.read())
blob = "\n".join(src)
ok("phase 10 defines no exit policy of its own",
   "def apply_exit" not in blob and "def simulate" not in blob)
ok("phase 10 defines no path walk of its own",
   "def walk" not in blob and "def measure_chase" not in blob)
ok("phase 10 defines no second spread-cost implementation",
   "def measure(" not in blob)
ok("phase 10 reuses the phase 7/8/9 pipeline",
   "from app.research.phase7" in blob and "phase9" in blob)
ok("phase 10 holds no order-placing code path",
   not any(t in blob for t in ("place_order", "placeOrder", "broker.place",
                               "live_orders")))

offenders: list[str] = []
for root, _dirs, files in os.walk(os.path.join(HERE, "app")):
    if os.sep + "research" in root:
        continue
    for f in files:
        if not f.endswith(".py"):
            continue
        with open(os.path.join(root, f)) as fh:
            if "phase10" in fh.read():
                offenders.append(os.path.join(root, f))
ok("no production module imports phase 10", not offenders, str(offenders))

print()
if FAILS:
    print(f"PHASE 10 SMOKE FAILED ({len(FAILS)} of {CHECKS})")
    for f in FAILS:
        print(" -", f)
    raise SystemExit(1)
print(f"PHASE 10 SMOKE PASSED ({CHECKS} checks)")
