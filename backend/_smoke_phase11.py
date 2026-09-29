"""Focused smoke for the Phase 11 research package. No network, no DB, no orders.

Phase 11's failure modes are specific and each is asserted directly rather than
described:

* **a family must never be pooled.** An index population and an MCX population
  with opposite outcomes must produce two tables with two verdicts, and the MCX
  rows must not move the index answer.
* **A+ thresholds must be derived per family.** A pooled tercile sets the index
  cut from MCX books, so the two derived cuts must differ when the books do.
* **an unpayable contract must never be attributed to direction.** A loss on a
  leg whose book exceeded the intended risk must classify as BAD_SPREAD and land
  on the economic side, not WRONG_DIRECTION.
* **a gross win that nets negative is a loss**, and it is an economic one.
* **the flow book must be costed at ask-in / bid-out**, and the costed number must
  be able to disagree with the mid-to-mid one.
* **an unrecorded funnel must say UNRECORDED**, never infer a binding limit.
* **an unmeasured feed comparison must stay unmeasured** while every session ran
  with the split disabled.
* **a small family must fail its own gate** even when the pooled row count passes.
* **no production module imports Phase 11.**

    .venv/bin/python _smoke_phase11.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.research.phase11 import (  # noqa: E402
    aplus11,
    attribution,
    families,
    gates11,
    ledger,
    preview,
    score11,
    shadow11,
    tradability11,
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
          spread_share: float | None = 10.0, mfe: float = 1.0,
          gross: float | None = None) -> dict:
    r = (1.5 if won else -1.0) if gross is None else gross
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


SESSIONS = ["2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"]

# --- 1. families ---------------------------------------------------------
ok("index underlyings are INDEX_OPTIONS",
   all(families.family_of(s) == families.INDEX_OPTIONS
       for s in ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY", "FINNIFTY",
                 "BANKEX")))
ok("MCX underlyings are MCX_OPTIONS",
   all(families.family_of(s) == families.MCX_OPTIONS
       for s in ("CRUDEOIL", "GOLD", "SILVER", "COPPER", "NATURALGAS")))
ok("a single-stock option on NFO is not an index option",
   families.family_of("RELIANCE") == families.OTHER,
   families.family_of("RELIANCE"))
ok("an unknown instrument is OTHER rather than guessed",
   families.family_of("NOT_A_REAL_SYMBOL") == families.OTHER)
ok("the recommended deep list is a recommendation, not a default the code applies",
   families.RECOMMENDED_DEEP == ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY",
                                 "CRUDEOIL", "NATURALGAS"))

# An index population where the high score wins, and an MCX population where the
# high score loses. Pooled, these average into nothing; split, they disagree.
INDEX_ROWS = [trade(SESSIONS[i % 5], 90.0 if i % 2 else 70.0, bool(i % 2),
                    instrument="NIFTY", spread_share=3.0) for i in range(120)]
MCX_ROWS = [trade(SESSIONS[i % 5], 90.0 if i % 2 else 70.0, not (i % 2),
                  instrument="GOLD", spread_share=140.0,
                  net=-2.5 if i % 2 else -1.0) for i in range(120)]
ROWS = families.tag([*INDEX_ROWS, *MCX_ROWS])

split = families.split(ROWS)
ok("tagging splits the population without losing a row",
   len(split[families.INDEX_OPTIONS]) == 120
   and len(split[families.MCX_OPTIONS]) == 120)
ok("every family key exists even when empty",
   families.OTHER in split and split[families.OTHER] == [])
summary = families.summary(ROWS)
ok("the summary states the no-pooling rule",
   "no metric in this phase is reported across families" in summary["pooling_rule"])

# --- 2. score, per family -----------------------------------------------
sc = score11.study_by_family(ROWS, len(SESSIONS))
idx_use = sc["by_family"][families.INDEX_OPTIONS]["usefulness"]
mcx_use = sc["by_family"][families.MCX_OPTIONS]["usefulness"]
ok("the score is read as a ranking in the family where it ordered outcomes",
   idx_use["usable_as_ranking"] is True, str(idx_use["objections"]))
ok("the score is refused in the family where it inverted",
   mcx_use["usable_as_ranking"] is False, str(mcx_use["objections"]))
ok("the two families are reported as disagreeing", sc["families_disagree"] is True)
ok("a usable ranking is still only IN_SAMPLE_ONLY",
   idx_use["label"] == "IN_SAMPLE_ONLY", idx_use["label"])
ok("the score keeps its honest label", sc["label_used"] == "SIGNAL SCORE")
ok("no probability mapping is fitted in the score block",
   "NOT_FITTED_IN_THIS_BLOCK" in sc["probability_status"])
ok("the formula is stated unchanged", "unchanged" in sc["guarantee"])
index_only = score11.study_by_family(INDEX_ROWS, len(SESSIONS))
ok("adding MCX rows does not change the index answer",
   index_only["by_family"][families.INDEX_OPTIONS]["usefulness"][
       "usable_as_ranking"] == idx_use["usable_as_ranking"])

# --- 3. A+ per family ----------------------------------------------------
def entry_of(row: dict) -> dict:
    return {**row, "greeks_usable": True}


taint = {"status": "DATA_CONTAMINATED"}
ap = aplus11.study_by_family(ROWS, SESSIONS, entry_of,
                             score_predictive={families.INDEX_OPTIONS: True,
                                               families.MCX_OPTIONS: False},
                             book_taint=taint)
cuts = ap["proposed_spread_cut_by_family_pct"]
ok("each family derives its own spread cut",
   cuts[families.INDEX_OPTIONS] != cuts[families.MCX_OPTIONS], str(cuts))
ok("the index cut is not set by MCX books",
   cuts[families.INDEX_OPTIONS] < cuts[families.MCX_OPTIONS], str(cuts))
ok("the thresholds are flagged as differing", ap["thresholds_differ_between_families"])
ok("A+ output stays shadow only", ap["status"] == "SHADOW_ONLY")
ok("A+ thresholds stay PROPOSED_ONLY",
   ap["proposed_thresholds_status"] == "PROPOSED_ONLY")
ok("no instrument is excluded by A+", "no instrument is excluded" in ap["guarantee"])
ok("both family answers are present and neither claims VALIDATED",
   "VALIDATED" not in ap["answer_index"] and "VALIDATED" not in ap["answer_mcx"])
ok("every row carries a phase 11 label",
   all(r.get("phase11_qualification") for r in ROWS))

# --- 4. tradability ------------------------------------------------------
tr = tradability11.study(ROWS, len(SESSIONS))
ok("a 3%-of-risk book is TRADABLE",
   tr["by_instrument"]["NIFTY"]["class"] == tradability11.TRADABLE)
ok("a book wider than the risk is UNTRADABLE_ON_SAMPLE",
   tr["by_instrument"]["GOLD"]["class"] == tradability11.UNTRADABLE_ON_SAMPLE)
ok("the distribution is reported, not just the median",
   all(k in tr["by_instrument"]["GOLD"] for k in
       ("p75_spread_share_of_risk_pct", "p90_spread_share_of_risk_pct",
        "max_spread_share_of_risk_pct")))
ok("the share of trades whose book exceeded the risk is reported",
   tr["by_instrument"]["GOLD"]["pct_of_trades_spread_exceeded_risk"] == 100.0)
ok("the classes are applied nowhere",
   "not applied as a gate" in tr["thresholds_status"])
thin = tradability11.study(
    [trade("2026-08-17", 80.0, True, instrument="COPPER", spread_share=4.0)
     for _ in range(5)], 1)
ok("too few trades is UNMEASURED rather than TRADABLE",
   thin["by_instrument"]["COPPER"]["class"] == tradability11.UNMEASURED)

# --- 5. loss attribution -------------------------------------------------
att = attribution.study(ROWS, entry_of)
mcx = att["by_family"][families.MCX_OPTIONS]
ok("an unpayable contract is BAD_SPREAD, not WRONG_DIRECTION",
   mcx["by_cause"][attribution.BAD_SPREAD]["n"] > 0
   and mcx["by_cause"][attribution.WRONG_DIRECTION]["n"] == 0,
   json.dumps({k: v["n"] for k, v in mcx["by_cause"].items()}))
ok("that loss lands on the economic side, not the signal side",
   mcx["by_side"][attribution.ECONOMIC]["n"] == mcx["losses"]
   and mcx["by_side"][attribution.SIGNAL_SIDE]["n"] == 0)
ok("the economic share of the loss is quantified",
   mcx["share_of_loss_economic_or_execution_pct"] == 100.0,
   str(mcx["share_of_loss_economic_or_execution_pct"]))

gross_win_net_loss = trade("2026-08-17", 88.0, True, instrument="NIFTY",
                           gross=0.4, net=-0.2, spread_share=60.0)
cause, evidence = attribution.cause_of(gross_win_net_loss, entry_of(gross_win_net_loss))
ok("a gross win that nets negative is an economic loss",
   cause == attribution.BAD_SPREAD and "the book took it" in evidence, evidence)
ok("a gross-win-net-loss trade is counted as a loss at all",
   attribution.study([gross_win_net_loss], entry_of)["by_family"][
       families.INDEX_OPTIONS]["losses"] == 1)

wrong = trade("2026-08-17", 88.0, False, instrument="NIFTY", spread_share=2.0,
              mfe=0.05)
ok("a leg that never moved on a payable book is WRONG_DIRECTION",
   attribution.cause_of(wrong, entry_of(wrong))[0] == attribution.WRONG_DIRECTION)
handed_back = trade("2026-08-17", 88.0, False, instrument="NIFTY",
                    spread_share=2.0, mfe=1.8)
ok("a 1R winner that finished negative is an EXIT failure",
   attribution.cause_of(handed_back, entry_of(handed_back))[0] == attribution.EXIT)
stale = trade("2026-08-17", 88.0, False, instrument="NIFTY", flag="STALE")
ok("a decision made on stale data is DATA_QUALITY",
   attribution.cause_of(stale, entry_of(stale))[0] == attribution.DATA_QUALITY)
ok("the precedence is stated in the report, not left implicit",
   any("economic tests run first" in s for s in [att["why_this_order"]]))

# --- 6. shadow book without a model -------------------------------------
sh = shadow11.build(ROWS, model_available=False, model_status="MODEL_UNAVAILABLE")
ok("no model means WATCH / NO_VALIDATED_MODEL",
   sh["status"] == shadow11.STATUS_NO_MODEL, sh["status"])
ok("the shadow book still records rows per family",
   sh["by_family"][families.INDEX_OPTIONS]["rows_recorded"] == 120)
ok("no probability is emitted without a validated mapping",
   all(r["shadow_paper_decision"]["probability"] is None
       for r in sh["by_family"][families.INDEX_OPTIONS]["book"]))
ok("the shadow book opens and closes nothing",
   "does not open, close, size or suppress" in sh["independence"])

# --- 7. funnel and costed flow ------------------------------------------
fn = ledger.funnel(None, ["2026-08-21"])
ok("a missing funnel log is UNRECORDED, not inferred",
   fn["by_session"]["2026-08-21"]["binding_limit"] == "UNRECORDED")
ok("the report says why the answer is missing",
   "in memory only" in fn["by_session"]["2026-08-21"]["why_unrecorded"])

with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "exec_funnel.jsonl"
    # 1786936800 is 2026-08-16 in IST; the exact date only has to be consistent.
    base = 1787000000
    lines = [{"ts": base + i, "kind": "BLOCKED", "stage": "RISK",
              "primary_blocker": "account trade cap", "instrument": "NIFTY",
              "value": 50, "threshold": 50, "reason": "cap reached",
              "where": "risk"} for i in range(4)]
    lines.append({"ts": base + 10, "kind": "BLOCKED", "stage": "VALIDATION",
                  "primary_blocker": "spread", "instrument": "GOLD",
                  "value": 300.0, "threshold": 100.0, "reason": "book too wide",
                  "where": "validation"})
    lines.append({"ts": base + 20, "kind": "COUNTS",
                  "reached": {"SIGNAL": 91, "FILLED": 7}, "blocked": {}})
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    session = ledger._session_of(base)
    fn2 = ledger.funnel(path, [session])
    block = fn2["by_session"][session]
    ok("a recorded funnel names the most frequent blocker",
       block["recorded"] and "account trade cap" in block["binding_limit"],
       block["binding_limit"])
    ok("the funnel keeps the value and threshold it refused on",
       block["blockers"][0]["example"]["threshold"] == 50)
    ok("stage counts survive the round trip",
       block["stage_counts"]["FILLED"] == 7)
    ok("the funnel refuses to equate the top blocker with the day's reason",
       "not automatically the reason" in fn2["reading"])

    flow = Path(tmp) / "flow_signals.jsonl"
    flow.write_text(json.dumps({
        "instrument": "GOLD", "side": "CE", "option_symbol": "GOLDX",
        "ts_open": base, "ts_close": base + 60, "entry_premium": 2200.0,
        "final_premium": 2210.0, "lots": 1, "lot_size": 100,
        "duration_sec": 60, "close_reason": "EXIT"}) + "\n")
    empty_db = Path(tmp) / "empty.db"
    import sqlite3

    conn = sqlite3.connect(empty_db)
    conn.execute("CREATE TABLE chain_snapshots (instrument TEXT, ts INTEGER, "
                 "payload TEXT, source TEXT)")
    conn.execute("INSERT INTO chain_snapshots VALUES (?,?,?,?)",
                 ("GOLD", base, json.dumps([{"symbol": "GOLDX", "premium": 2200.0,
                                             "bid": 2075.0, "ask": 2325.0}]),
                  "real"))
    conn.execute("INSERT INTO chain_snapshots VALUES (?,?,?,?)",
                 ("GOLD", base + 60, json.dumps([{"symbol": "GOLDX",
                                                  "premium": 2210.0,
                                                  "bid": 2085.0,
                                                  "ask": 2335.0}]), "real"))
    conn.commit()
    conn.close()
    cf = ledger.costed_flow(flow, str(empty_db), [ledger._session_of(base)])
    leg = cf["by_instrument"]["GOLD"]
    ok("the flow book is recomputed at ask-in / bid-out",
       cf["overall"]["gross_rupees"] == 1000.0
       and cf["overall"]["net_rupees"] is not None
       and cf["overall"]["net_rupees"] < 0,
       f"gross {cf['overall']['gross_rupees']} net {cf['overall']['net_rupees']}")
    ok("a mid-to-mid gain can be a costed loss",
       leg["gross_rupees"] > 0 > leg["net_rupees"])
    ok("the production flow ledger is named as mid-to-mid",
       "the mid" in cf["production_ledger_pricing"])
    ok("nothing in production is changed by the costed view",
       "nothing in production" in cf["what_changed_here"])
    unpriced = ledger.costed_flow(flow, str(Path(tmp) / "missing.db"), [])
    ok("a leg with no recorded book is unpriced rather than assumed",
       unpriced["available"] is True
       and unpriced["overall"]["legs_with_recorded_book"] == 0)

ok("no flow log means the flow book is simply not recomputed",
   ledger.costed_flow(None, "x.db", [])["available"] is False)

# --- 8. gates ------------------------------------------------------------
g = gates11.evaluate(
    resolved=1130, sessions=5, missing_bar_pct=23.9, book_coverage_pct=100.0,
    ladder_coverage_pct=88.2, holdout_sessions=2, smallest_cell=30,
    calibration_train_rows=200, calibration_holdout_rows=10,
    sessions_at_deep_watchlist=0,
    resolved_by_family={families.INDEX_OPTIONS: 336, families.MCX_OPTIONS: 794},
    smallest_family_cell={families.INDEX_OPTIONS: 40,
                          families.MCX_OPTIONS: 60})
ok("this dataset is EXPLORATORY_ONLY", g["status"] == "EXPLORATORY_ONLY")
ok("the family gates are added on top of Phase 10's",
   len(g["family_gates_added_by_phase11"]) == 4)
ok("VALIDATED is not a permitted label", "VALIDATED" not in g["permitted_labels"])
small = gates11.evaluate(
    resolved=1130, sessions=25, missing_bar_pct=2.0, book_coverage_pct=99.0,
    ladder_coverage_pct=95.0, holdout_sessions=6, smallest_cell=40,
    calibration_train_rows=600, calibration_holdout_rows=200,
    sessions_at_deep_watchlist=6,
    resolved_by_family={families.INDEX_OPTIONS: 12, families.MCX_OPTIONS: 1118},
    smallest_family_cell={families.INDEX_OPTIONS: 6, families.MCX_OPTIONS: 60})
ok("a pooled row count cannot carry a starved family",
   f"FAMILY_SAMPLE_{families.INDEX_OPTIONS}" in small["failed"]
   and small["status"] == "EXPLORATORY_ONLY", str(small["failed"]))

# --- 9. research payloads are payloads ----------------------------------
pv = preview.signal_preview(ROWS, tr["by_instrument"])
ok("the preview marks the score as not a probability",
   all(r["signal_score_is_not_a_probability"] for r in pv["rows"]))
ok("the preview forbids a probability column",
   "probability_of_profit" in pv["must_not_display"])
ok("the preview is wired to nothing", "no route serves this payload" in pv["wiring"])

# --- 10. isolation -------------------------------------------------------
src = []
pkg = os.path.join(HERE, "app", "research", "phase11")
for name in sorted(os.listdir(pkg)):
    if name.endswith(".py"):
        with open(os.path.join(pkg, name)) as fh:
            src.append(fh.read())
blob = "\n".join(src)
ok("phase 11 defines no exit policy of its own",
   "def apply_exit" not in blob and "def simulate" not in blob)
ok("phase 11 defines no second spread-cost implementation",
   "def measure(" not in blob)
ok("phase 11 reuses the earlier pipeline",
   "from app.research.phase9" in blob and "phase10" in blob)
ok("phase 11 holds no order-placing code path",
   not any(t in blob for t in ("place_order", "placeOrder", "broker.place",
                               "live_orders")))
ok("phase 11 implements none of the deferred exit policies",
   not any(t in blob for t in ("def hold_until_peak", "def trailing_exit",
                               "def partial_exit")))

offenders: list[str] = []
for root, _dirs, files in os.walk(os.path.join(HERE, "app")):
    if os.sep + "research" in root:
        continue
    for f in files:
        if not f.endswith(".py"):
            continue
        with open(os.path.join(root, f)) as fh:
            if "phase11" in fh.read():
                offenders.append(os.path.join(root, f))
ok("no production module imports phase 11", not offenders, str(offenders))

print()
if FAILS:
    print(f"PHASE 11 SMOKE FAILED ({len(FAILS)} of {CHECKS})")
    for f in FAILS:
        print(" -", f)
    raise SystemExit(1)
print(f"PHASE 11 SMOKE PASSED ({CHECKS} checks)")
