"""Phase 19 smoke — futures paper book, T1 calibration, production readiness.

Run: .venv/bin/python _smoke_phase19.py

What this asserts, in the order it matters:

1. the futures book refuses what it should — a disabled flag, an invalid plan, a
   zero-risk plan, a stale feed, a duplicate;
2. the executable side is charged in the right direction for a long and for a
   short, and the entry is never better than the mark;
3. every resolution path exists: T1..T3, stop, timeout, session close, and a stop
   wins over a target reached in the same tick;
4. costs are decomposed, slippage cannot improve a trade, and a modelled spread
   is never reported as measured;
5. calibration is honest — empty data blocks, a leaked (fit-and-evaluate) split
   is not accepted as calibrated, a random score does not clear the gate, no
   shuffling occurs, and the percentage stays hidden until the verdict passes;
6. readiness reports NOT_READY with reasons rather than silence, and no module in
   Phase 19 contains an order call;
7. the Checkpoint 1 gate fails a sub-target capture rate, reports an empty
   futures book as NO DATA rather than as a pass, and publishes no performance
   figure of any kind;
8. the lifecycle funnel separates the four reasons a session can record many
   candidates and resolve almost no costed legs — selectivity, an unfillable
   book, entries never made, entries never resolved — and refusals are counted
   by reason rather than as one total;
9. the grading A/B lifts a prior veto only where the pool fails to support it,
   never confuses "never measured" with "tested and cleared", separates "not yet
   measurable" (PENDING_DATA) from "judged and refused", and keeps both of those
   out of anything promotable.
"""
from __future__ import annotations

import os
import random
import re
import tempfile
import time
from types import SimpleNamespace

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="p19smoke-"))

from app.config import settings  # noqa: E402
from app.models import FuturesSignalCard  # noqa: E402
from app.research.phase19 import (  # noqa: E402
    calibration,
    futbook,
    futcosts,
    readiness,
    service,
)

from app.research.phase17 import aplus as p17aplus  # noqa: E402
from app.research.phase17 import paper as p17paper  # noqa: E402
from app.research.phase17 import quality as p17quality  # noqa: E402
from app.research.phase17 import reach as p17reach  # noqa: E402
from app.research.phase17 import schema as p17schema  # noqa: E402
from app.research.phase17 import service as p17service  # noqa: E402

import phase19_checkpoint1 as checkpoint1  # noqa: E402
import phase19_funnel as funnel  # noqa: E402
import phase19_grading_audit as audit  # noqa: E402
from app.research.phase19 import grading_ab as ab  # noqa: E402
from app.research.phase19 import roomab  # noqa: E402

CHECKS = 0
FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(label)


def plan(**kw: object) -> FuturesSignalCard:
    base = {
        "status": "VALID_FUTURES_PLAN",
        "signal": "BUY",
        "direction": "LONG",
        "instrument": "NIFTY",
        "contract": "NIFTY25SEPFUT",
        "expiry": "2025-09-25",
        "days_to_expiry": 10,
        "lot_size": 65,
        "entry": 24000.0,
        "stop": 23960.0,
        "target1": 24040.0,
        "target2": 24080.0,
        "target3": 24120.0,
        "signal_score": 72.0,
        "setup_type": "TREND_PULLBACK",
        "regime": "TRENDING",
        "atr": 60.0,
    }
    base.update(kw)
    return FuturesSignalCard(**base)


def fresh() -> None:
    futbook.reset_for_tests()


# --------------------------------------------------------------- 1. refusals
settings.phase19_futures_paper = False
fresh()
futbook.observe("NIFTY", plan(), 24000.0, now=1_000.0, feed_age_sec=1.0)
check(futbook.health()["entries"] == 0, "a disabled book does not enter")
check(
    futbook.health()["skipped"].get(futbook.SKIP_DISABLED) == 1,
    "the disabled refusal is counted, so an empty session says why",
)
settings.phase19_futures_paper = True

fresh()
futbook.observe("NIFTY", plan(status="INVALID_FUTURES_PLAN"), 24000.0, now=1_000.0)
check(futbook.health()["entries"] == 0, "an invalid plan does not enter")
futbook.observe("NIFTY", None, 24000.0, now=1_000.0)
check(futbook.health()["entries"] == 0, "no card does not enter")

fresh()
futbook.observe("NIFTY", plan(stop=24000.0), 24000.0, now=1_000.0)
check(futbook.health()["entries"] == 0, "a stop at the entry is refused, not clamped")
check(
    futbook.health()["skipped"].get(futbook.SKIP_NO_RISK) == 1,
    "zero risk is refused with its own reason (it produced the 1e8 R rows before)",
)

fresh()
futbook.observe("NIFTY", plan(), 24000.0, now=1_000.0, feed_age_sec=600.0)
check(futbook.health()["entries"] == 0, "a stale feed does not produce a paper fill")
check(
    futbook.health()["skipped"].get(futbook.SKIP_STALE) == 1, "staleness is counted"
)

fresh()
futbook.observe("NIFTY", plan(lot_size=None), 24000.0, now=1_000.0)
check(futbook.health()["entries"] == 0, "an unknown lot size cannot be costed")

fresh()
futbook.observe("NIFTY", plan(), 24000.0, now=1_000.0, feed_age_sec=1.0)
check(futbook.health()["entries"] == 1, "a valid fresh plan enters once")
futbook.observe("NIFTY", plan(), 24001.0, now=1_010.0, feed_age_sec=1.0)
check(futbook.health()["entries"] == 1, "the same plan does not enter twice")
check(len(futbook.open_positions()) == 1, "one open position")

# --------------------------------------------------- 2. the executable side
fresh()
futbook.observe("NIFTY", plan(), 24000.0, now=1_000.0, feed_age_sec=1.0)
long_pos = futbook.open_positions()[0]
check(
    long_pos["entry"] >= 24000.0,
    "a long pays up to enter — the fill is never better than the mark",
)
check(long_pos["exec_basis"] == futbook.EXEC_MODELLED,
      "with no futures book the crossing is MODELLED, never called measured")

fresh()
futbook.observe(
    "NIFTY", plan(direction="SHORT", signal="SELL", entry=24000.0, stop=24040.0,
                  target1=23960.0, target2=23920.0, target3=23880.0),
    24000.0, now=1_000.0, feed_age_sec=1.0,
)
short_pos = futbook.open_positions()[0]
check(short_pos["entry"] <= 24000.0, "a short sells down into the bid to enter")

fresh()
futbook.observe("NIFTY", plan(spread_points=4.0), 24000.0, now=1_000.0, feed_age_sec=1.0)
booked = futbook.open_positions()[0]
check(booked["exec_basis"] == futbook.EXEC_BOOK,
      "with a recorded spread the crossing is EXEC_BOOK")
check(abs(booked["entry"] - 24002.0) < 1e-6,
      "a booked long crosses half the spread to the offer")

# ------------------------------------------------------- 3. every resolution
def run(ticks: list[tuple[float, float]], card: FuturesSignalCard | None = None,
        close_at: tuple[float, float] | None = None) -> dict | None:
    fresh()
    futbook.observe("NIFTY", card or plan(), 24000.0, now=1_000.0, feed_age_sec=1.0)
    for ts, px in ticks:
        futbook.observe("NIFTY", card or plan(), px, now=ts, feed_age_sec=1.0)
    if close_at:
        futbook.close_session("NIFTY", close_at[1], now=close_at[0])
    rows = futbook.resolved()
    return rows[-1] if rows else None


row = run([(1_060.0, 24045.0), (1_120.0, 24085.0), (1_180.0, 24125.0)])
check(row is not None and row["outcome"] == futbook.T3, "T3 resolves the trade")
check(row is not None and row["reached_t1"] and row["reached_t2"] and row["reached_t3"],
      "T1 and T2 are recorded on the way to T3")
check(row is not None and row["minutes_to_t1"] == 1.0, "time to T1 is measured")
check(row is not None and row["net_r"] is not None, "a resolved row carries net R")
check(row is not None and row["gross_points"] > row["net_points"],
      "net is strictly worse than gross — costs are actually charged")

row = run([(1_060.0, 23955.0)])
check(row is not None and row["outcome"] == futbook.STOP, "the stop resolves the trade")
check(row is not None and row["net_r"] < 0, "a stopped trade nets a loss")

row = run([(1_060.0, 24125.0), (1_120.0, 23950.0)])
check(row is not None and row["outcome"] == futbook.T3,
      "T3 in an earlier tick resolves before a later stop")

row = run([(1_060.0, 24000.0)], close_at=(1_100.0, 24010.0))
check(row is not None and row["outcome"] == futbook.SESSION_END,
      "the session close flattens an open trade")
check(len(futbook.open_positions()) == 0, "nothing is carried past the close")

row = run([(1_000.0 + futbook.MAX_HOLD_MINUTES * 60.0 + 1.0, 24010.0)])
check(row is not None and row["outcome"] == futbook.TIMEOUT,
      "a trade that never concludes is resolved TIMEOUT, not left open")

fresh()
futbook.observe("NIFTY", plan(), 24000.0, now=1_000.0, feed_age_sec=1.0)
futbook.observe("NIFTY", plan(), 23955.0, now=1_060.0, feed_age_sec=1.0)
check(len(futbook.resolved()) == 1, "the trade stopped out")
futbook.observe("NIFTY", plan(), 23956.0, now=1_120.0, feed_age_sec=1.0)
check(
    len(futbook.resolved()) == 1 and futbook.health()["entries"] == 1,
    "a plan that already resolved does not re-enter — one plan is one row, not a "
    "dozen correlated ones",
)
check(
    futbook.health()["skipped"].get(futbook.SKIP_PLAN_DONE, 0) >= 1,
    "the re-entry refusal is counted",
)
futbook.observe(
    "NIFTY", plan(entry=23900.0, stop=23870.0, target1=23940.0), 23900.0,
    now=1_180.0, feed_age_sec=1.0,
)
check(futbook.health()["entries"] == 2, "a genuinely different plan does enter")

row = run([(1_060.0, 24035.0), (1_120.0, 23955.0)])
check(row is not None and row["mfe_points"] > 0 and row["mae_points"] < 0,
      "MFE and MAE are both tracked")
check(row is not None and row["giveback_points"] > 0,
      "give-back is the distance between MFE and the exit")
check(row is not None and row["hold_bucket"] in futbook.HOLD_BUCKETS,
      "the hold bucket is one of the declared ones")
check(row is not None and row["reached_t1"] is False and row["t1_before_stop"] is False,
      "a trade that never reached T1 does not claim it")
check(
    row is not None
    and [s["stage"] for s in row["lifecycle"]] == list(futbook.STAGES),
    "the full lifecycle is recorded: plan -> eligible -> entry -> exit -> resolved",
)
check(row is not None and row["paper_only"] and row["no_real_order"],
      "every resolved row is stamped paper-only")

# --------------------------------------------------------------- 4. the costs
c = futcosts.round_trip(
    "NIFTY", entry=24000.0, exit_price=24040.0, lot_size=65, lots=1
)
check(c["cost_status"] == futcosts.COST_MODELLED,
      "with no spread the cost is MODELLED")
check(c["spread_points"] is None,
      "an unrecorded spread is None, not zero — net is optimistic and says so")
check(c["tax_rupees"] > c["brokerage_rupees"],
      "statutory charges on one index lot exceed the flat brokerage")
check(
    all(c[k] is not None for k in
        ("cost_points", "cost_rupees", "brokerage_rupees", "tax_rupees", "txn_rupees")),
    "the round trip is decomposed, not one number",
)
booked_cost = futcosts.round_trip(
    "NIFTY", entry=24000.0, exit_price=24040.0, lot_size=65, lots=1,
    spread_points=4.0,
)
check(booked_cost["cost_status"] == futcosts.COST_MEASURED,
      "a recorded spread makes the cost MEASURED")
check(booked_cost["cost_points"] > c["cost_points"],
      "charging a spread costs more than not charging one")
negative = futcosts.round_trip(
    "NIFTY", entry=24000.0, exit_price=24040.0, lot_size=65, lots=1,
    slippage_points=-50.0,
)
check(negative["cost_points"] > 0 and negative["slippage_points"] == 0.0,
      "negative slippage cannot pay the trader for crossing the spread")
check(
    futcosts.round_trip("NIFTY", entry=None, exit_price=1.0, lot_size=65)["cost_status"]
    == futcosts.COST_UNKNOWN,
    "an uncostable row is UNKNOWN rather than free",
)
mcx = futcosts.round_trip(
    "CRUDEOIL", entry=6900.0, exit_price=6950.0, lot_size=100, lots=1
)
check(mcx["tax_rupees"] > 0, "MCX pays CTT rather than STT, and it is charged")

# ---------------------------------------------------------- 5. calibration
empty = calibration.evaluate([])
check(empty["verdict"] == calibration.REQUIRES_MORE_DATA,
      "no data is REQUIRES_MORE_DATA, not a passing calibration")
check(empty["may_publish_probability"] is False,
      "the T1 percentage is not published on an empty dataset")
check(empty["display"] == "T1_SCORE_AND_RANK",
      "the caller is told to keep showing the score and rank")
check(bool(empty["blockers"]), "the blockers are listed explicitly")

rng = random.Random(7)


def synth(n: int, sessions: int, *, informative: bool) -> list[dict]:
    rows: list[dict] = []
    for i in range(n):
        score = rng.uniform(10.0, 99.0)
        p = (score / 140.0) if informative else 0.4
        rows.append({
            "session": f"2025-09-{1 + (i % sessions):02d}",
            "t1_score": score,
            "t1_rank": "RANK_B",
            "outcome": "T1" if rng.random() < p else "STOP",
        })
    return rows


mapped = calibration.to_rows(synth(40, 8, informative=True))
check(len(mapped) == 40, "scored resolved rows map through")
check(
    [r["session"] for r in mapped] == sorted(r["session"] for r in mapped),
    "rows are ordered chronologically — nothing is shuffled",
)
check(
    calibration.to_rows([{"session": "2025-09-01", "outcome": "T1"}]) == [],
    "an unscored row is dropped, not scored zero",
)

small = calibration.evaluate(mapped)
check(small["verdict"] != calibration.CALIBRATED,
      "40 outcomes cannot calibrate a probability")
check(any("holdout" in b for b in small["blockers"]),
      "the short holdout is named as the blocker")

noise = calibration.evaluate(calibration.to_rows(synth(600, 30, informative=False)))
check(noise["verdict"] == calibration.NOT_CALIBRATED,
      "a score unrelated to the outcome is NOT_CALIBRATED even with 600 rows")
check(noise["may_publish_probability"] is False,
      "and its percentage stays hidden")
check(noise["brier"] is not None and noise["ece"] is not None,
      "Brier and ECE are still reported, so the failure is legible")
check(
    noise["brier_skill"] is not None and noise["brier_skill"] < 0.02,
    "a noise score shows no skill against the base rate",
)
check(len(noise["reliability_curve"]) == len(calibration.BINS),
      "the reliability curve has one row per declared bin")
check(
    noise["dev_sessions"] and noise["holdout_sessions"]
    and max(noise["dev_sessions"]) < min(noise["holdout_sessions"]),
    "the split is chronological: every dev session precedes every holdout session",
)

strong = calibration.evaluate(calibration.to_rows(synth(900, 40, informative=True)))
check(strong["scored_holdout_rows"] > 0, "an informative score scores its holdout")
check(
    strong["verdict"] in (calibration.CALIBRATED, calibration.NOT_CALIBRATED),
    "a large informative sample reaches a verdict either way",
)
check(
    (strong["display"] == "T1_PROBABILITY")
    == (strong["verdict"] == calibration.CALIBRATED),
    "the percentage is displayed only when the verdict is CALIBRATED",
)

# ---------------------------------------------------------- 6. readiness
empty_oos = {"development": {}, "holdout": {}, "rows_considered": 0}
opt = readiness.option_row(empty_oos, capture_pct=None)
check(opt["status"] == readiness.NOT_READY, "an empty option book is NOT_READY")
check(any("capture" in b for b in opt["blockers"]),
      "unmeasured capture is named as a blocker")
check(opt["paper_only"] and opt["no_real_order"] and opt["promotion_is_manual"],
      "the row is stamped paper-only and manually promoted")

fut = readiness.futures_row(empty_oos, freshness_pct=None, resolved=0)
check(fut["status"] == readiness.NOT_READY, "an empty futures book is NOT_READY")
check(any("resolved futures" in b for b in fut["blockers"]),
      "zero resolved futures trades is stated, not implied")

positive = {
    "development": {"trades": 120, "expectancy_r": 0.3, "profit_factor": 1.6},
    "holdout": {"trades": 40, "expectancy_r": 0.2, "profit_factor": 1.3},
    "rows_considered": 160, "rows_excluded_uncosted": 0,
}
short_hold = readiness.option_row(positive, capture_pct=95.0)
check(short_hold["status"] == readiness.PAPER,
      "a positive in-sample result with a short holdout is PAPER, not ready")
check(any("holdout" in b for b in short_hold["blockers"]),
      "the holdout shortfall is the stated reason")

low_capture = readiness.option_row(
    {
        "development": {"trades": 150, "expectancy_r": 0.3, "profit_factor": 1.6},
        "holdout": {"trades": 150, "expectancy_r": 0.25, "profit_factor": 1.4},
        "rows_considered": 300, "rows_excluded_uncosted": 0,
    },
    capture_pct=40.0,
)
check(low_capture["status"] != readiness.PRODUCTION_CANDIDATE,
      "a passing gate on 40% capture is not a production candidate")
check(any("capture" in b for b in low_capture["blockers"]),
      "capture is named as the blocker before performance is read")

board = readiness.board([opt, fut])
check(board["best_status"] == readiness.NOT_READY, "the board reports the best status")
check(board["any_production_candidate"] is False, "nothing is a candidate on day one")
check(board["promotion_is_manual"] and board["no_real_order"],
      "the board itself cannot promote anything")
check(list(readiness.ORDER)[-1] == readiness.PRODUCTION_CANDIDATE,
      "PRODUCTION_CANDIDATE is the top status and still requires review")

# ------------------------------------------------- service + safety scan
live = service.readiness()
check(isinstance(live["rows"], list) and len(live["rows"]) == 3,
      "the panel reports exactly three strategies")
check(
    {r["strategy"] for r in live["rows"]}
    == {"OPTION_A_PLUS", "FUTURES_A_PLUS", "CAS"},
    "Option A+, Futures A+ and CAS each get a row",
)
check("capture" in live, "the panel carries the capture rates that gate it")
board_payload = service.futures_board()
check(board_payload["no_real_order"] is True, "the futures board is order-free")
check(isinstance(service.calibration()["blockers"], list),
      "calibration runs against the installed store")

ORDER_CALLS = re.compile(
    r"\b(place_order|placeOrder|buy\(|sell\(|smart_api|smartConnect|confirm=True)\b"
)
src_dir = os.path.join(os.path.dirname(__file__), "app", "research", "phase19")
offenders: list[str] = []
for name in sorted(os.listdir(src_dir)):
    if not name.endswith(".py"):
        continue
    with open(os.path.join(src_dir, name), encoding="utf-8") as fh:
        body = fh.read()
    for line in body.splitlines():
        if line.strip().startswith("#") or line.strip().startswith("*"):
            continue
        if ORDER_CALLS.search(line):
            offenders.append(f"{name}: {line.strip()}")
check(not offenders, f"no order call exists anywhere in Phase 19: {offenders}")

# Restart survival: a trade open when the process stopped is reloaded rather than
# silently dropped, which would bias the sample toward short trades.
fresh()
futbook.observe("NIFTY", plan(), 24000.0, now=time.time(), feed_age_sec=1.0)
check(os.path.exists(os.path.join(settings.data_dir, futbook.OPEN_NAME)),
      "the open position is persisted for a restart")
persisted = futbook.read_log()
check(isinstance(persisted, list), "resolved rows are readable from disk")

# --- Checkpoint 1 gate: the two-session "is the recorder working" report. -----
# It must FAIL on a bad capture rate, must never invent a futures measurement out
# of an empty book, and must never print a performance figure — its whole purpose
# is to say whether the data can be trusted, not whether the strategy works.
fresh()
ck1 = checkpoint1.build(None)
check(ck1["verdict"] in ("CAPTURE_BROKEN", "INCOMPLETE", "CAPTURE_HEALTHY"),
      "checkpoint 1 returns one of the three verdicts")
check(len(ck1["lines"]) == 6, "checkpoint 1 reports all six measurements")
check(
    all(ln["verdict"] in (checkpoint1.PASS, checkpoint1.FAIL, checkpoint1.NODATA)
        for ln in ck1["lines"]),
    "every checkpoint 1 line is PASS, FAIL or NO DATA",
)
check(checkpoint1.lifecycle_verdict(0, 0) == checkpoint1.NODATA,
      "a book that saw no valid plan is NO DATA, not a failure")
check(checkpoint1.lifecycle_verdict(0, 7) == checkpoint1.FAIL,
      "plans seen but nothing resolved is a failure — trades are being dropped")
check(checkpoint1.lifecycle_verdict(3, 7) == checkpoint1.PASS,
      "resolved rows prove the lifecycle ran end to end")
check(checkpoint1.futures_freshness_pct([], None) is None,
      "freshness with nothing measured is None, not 100%")
check(
    checkpoint1.futures_freshness_pct(
        [{"entry_feed_age_sec": 1.0}, {"entry_feed_age_sec": 999.0}], None
    ) == 50.0,
    "offline freshness is measured from the age each row recorded",
)
check(
    checkpoint1.exit_capture([
        {"exit_ts": 1, "exit_side": "BID"},
        {"exit_ts": 2, "exit_side": "LTP"},
    ])["pct"] == 50.0,
    "exit capture counts only legs that exited against a real bid",
)
check(
    checkpoint1.orphans([{"observation_id": "A"}], [])["total"] == 0,
    "no paper rows means no orphans",
)
check(
    not any(
        word in ln["measurement"].lower()
        for ln in ck1["lines"]
        for word in ("expectancy", "win rate", "profit", "pf")
    ),
    "checkpoint 1 measures the recorder, never performance",
)
check(checkpoint1.verdict_of(89.9, 90.0) == checkpoint1.FAIL,
      "a capture rate below target fails the gate")
check(checkpoint1.verdict_of(90.0, 90.0) == checkpoint1.PASS,
      "a capture rate at target passes the gate")
check(checkpoint1.verdict_of(1.0, 0.0, lower_is_better=True) == checkpoint1.FAIL,
      "a single orphan row fails the gate")

# --- Lifecycle funnel: WHERE the candidates went. -----------------------------
# The question this answers is "8,775 candidates, one costed leg — selectivity or
# a broken lifecycle?", so each of those causes must produce its own diagnosis
# from the counts alone. A funnel that says "healthy" whatever the data is worth
# nothing.
fun = funnel.build(None)
check(isinstance(fun["stages"]["candidate_ticks"], int),
      "the funnel counts recorded candidate ticks")
check(fun["stages"]["distinct_opportunities"]
      <= fun["stages"]["candidate_ticks"],
      "distinct opportunities can never exceed the ticks that produced them")
check(fun["stages"]["a_plus_ticks"] <= fun["stages"]["candidate_ticks"],
      "A+ is a subset of what was graded")
check(fun["stages"]["admissible_ticks"] <= fun["stages"]["a_plus_ticks"],
      "only an A+ tick can be admissible — admission is downstream of grading")

base = {
    "candidate_ticks": 100, "distinct_opportunities": 10,
    "refused_at_grading": {"REJECT_SPREAD": 90},
    "a_plus_ticks": 10, "a_plus_opportunities": 2,
    "refused_at_admission": {}, "admissible_ticks": 10,
    "admissible_opportunities": 2, "paper_entries_seen": 2,
    "resolved_legs": 2, "resolved_without_exit_price": 0,
}
check(funnel.diagnose({**base, "candidate_ticks": 0, "resolved_legs": 0,
                       "a_plus_ticks": 0, "admissible_opportunities": 0,
                       "paper_entries_seen": 0}, None)[0] == funnel.NO_DATA,
      "an empty window diagnoses nothing rather than blaming the lifecycle")
check(funnel.diagnose({**base, "a_plus_ticks": 0, "admissible_ticks": 0,
                       "admissible_opportunities": 0, "resolved_legs": 0,
                       "paper_entries_seen": 0}, None)[0]
      == funnel.A_PLUS_SELECTIVE,
      "no A+ candidate is selectivity, and the gate doing it must be named")
check("REJECT_SPREAD" in funnel.diagnose(
          {**base, "a_plus_ticks": 0, "admissible_ticks": 0,
           "admissible_opportunities": 0, "resolved_legs": 0,
           "paper_entries_seen": 0}, None)[1],
      "the selectivity explanation names the gate, not just the count")
check(funnel.diagnose({**base, "refused_at_admission": {"REFUSED_NO_BOOK": 10},
                       "admissible_ticks": 0, "admissible_opportunities": 0,
                       "resolved_legs": 0, "paper_entries_seen": 0}, None)[0]
      == funnel.ADMISSION_BLOCKED,
      "A+ candidates with no fillable book is an admission problem, not grading")
check(funnel.diagnose({**base, "paper_entries_seen": 0,
                       "resolved_legs": 0}, None)[0] == funnel.NOT_ENTERING,
      "admissible candidates and no entry is the case that needs code")
check(funnel.diagnose({**base, "resolved_legs": 0}, {"open": 2})[0]
      == funnel.NOT_RESOLVING,
      "entries that never resolve are distinguished from entries never made")
check(funnel.diagnose({**base, "resolved_without_exit_price": 1}, None)[0]
      == funnel.RESOLVER_LOSING,
      "a resolved row with no exit price is a resolver fault, checked first")
check(funnel.diagnose({**base, "duplicate_episode_rows": 2}, None)[0]
      == funnel.DUPLICATE_EPISODES,
      "a repeated episode id is correlated evidence, not two trades")
check(funnel.diagnose({**base, "a_plus_ticks": 0}, None)[0]
      == funnel.GRADING_MISMATCH,
      "a costed leg with no A+ observation behind it is a reconciliation fault")
check(funnel.diagnose(base, {"open": 0})[0] == funnel.HEALTHY,
      "a full funnel with resolutions reads as working")

check(funnel.admission_of({"selected": {"has_book": False}})
      == p17paper.REFUSED_NO_BOOK,
      "a one-sided book could never have been filled")
check(funnel.admission_of(
          {"selected": {"has_book": True, "data_quality": p17quality.DEGRADED,
                        "ask": 10.0}}) == p17paper.REFUSED_DATA,
      "a degraded book is refused rather than filled at a minute-old ask")
check(funnel.admission_of(
          {"selected": {"has_book": True, "data_quality": p17quality.EXACT,
                        "ask": 0.0}}) == p17paper.REFUSED_NO_BOOK,
      "a zero ask is not a price")
check(funnel.admission_of(
          {"selected": {"has_book": True, "data_quality": p17quality.EXACT,
                        "ask": 10.0}}) == p17paper.ENTERED,
      "a fresh two-sided book with a positive ask is admissible")
check(funnel.opportunity_key({"session": "S", "instrument": "NIFTY",
                              "selected": {"symbol": "X"}, "direction": "CE"})
      == funnel.opportunity_key({"session": "S", "instrument": "NIFTY",
                                 "selected": {"symbol": "X"},
                                 "direction": "CE"}),
      "the same symbol re-observed on the next tick is one opportunity")
check(funnel.opportunity_key({"session": "S", "instrument": "NIFTY",
                              "selected": {"symbol": "X"}, "direction": "CE"})
      != funnel.opportunity_key({"session": "T", "instrument": "NIFTY",
                                 "selected": {"symbol": "X"},
                                 "direction": "CE"}),
      "the same symbol on a later day is a new opportunity")

# Refusals must be counted BY REASON: a single total cannot separate "A+ is
# selective" from "the book was never fillable".
p17paper.reset()
health = p17paper.health()
check(isinstance(health["refused_by_reason"], dict),
      "paper health publishes refusals by reason")
check(health["refused_by_reason"] == {}, "a reset engine has refused nothing")
obs_not_a_plus = SimpleNamespace(
    observation_id="O1", instrument="NIFTY", selected=None,
    aplus={"a_plus_label": "WATCH"}, reach={}, entry={}, economics={},
    selected_vehicle="CE", direction="CE", signal_ts=time.time(),
    plan=SimpleNamespace(stop=1.0, target1=2.0, target2=3.0, target3=4.0),
)
p17paper.consider(obs_not_a_plus, now=time.time())
check(p17paper.health()["refused_by_reason"].get(p17paper.REFUSED_NOT_A_PLUS) == 1,
      "a non-A+ refusal is attributed to grading, not to the book")
check(sum(p17paper.health()["refused_by_reason"].values())
      == p17paper.health()["refused"],
      "the by-reason counts add up to the refusal total")
p17paper.reset()

# --- grading audit -----------------------------------------------------------
# The audit exists to separate "this gate is the binding constraint" from "this
# gate is simply checked first". If it agreed with the recorded label by
# construction it would be unable to detect an ordering artefact at all, so the
# checks below pin the independence of the gate evaluation.
good_row = {
    "observation_id": "A1", "instrument": "NIFTY", "session": "S",
    "direction": "BULLISH", "data_quality": p17quality.EXACT,
    "selected": {"symbol": "NIFTY100CE"},
    "economics": {"vehicle_class": "GREEN", "cost_status": "MEASURED",
                  "spread_pct": 1.0},
    "entry": {"entry_quality": "IDEAL"},
    "reach": {"t1_rank": "RANK_A"},
    "aplus": {"a_plus_score": 88.0, "a_plus_label": "A_PLUS",
              "missing_components": [], "components": {}},
}
check(audit.gates_failed(good_row) == [],
      "a candidate that cleared every gate is reported as blocked by nothing")
check(audit.first_gate([]) is None, "no failing gate means no first gate")

stale = dict(good_row, data_quality=p17quality.STALE)
check(audit.G_DATA in audit.gates_failed(stale),
      "a stale book fails the data gate")
red = dict(good_row, economics=dict(good_row["economics"], vehicle_class="RED"))
check(audit.G_VEHICLE_RED in audit.gates_failed(red),
      "a RED vehicle fails the vehicle gate")
wide = dict(good_row, economics=dict(good_row["economics"], spread_pct=99.0))
check(audit.G_SPREAD in audit.gates_failed(wide),
      "a spread past the RED ceiling fails the spread gate")
chased = dict(good_row, entry={"entry_quality": "SEVERELY_CHASED"})
check(audit.G_ENTRY_CHASED in audit.gates_failed(chased),
      "a chased entry fails the entry gate")
no_room = dict(good_row, reach={"t1_rank": "RANK_E"})
check(audit.G_ROOM in audit.gates_failed(no_room),
      "a RANK_E reach fails the room gate")
unmeasured = dict(good_row, aplus=dict(good_row["aplus"],
                                       missing_components=["entry_edge"]))
check(audit.G_MISSING in audit.gates_failed(unmeasured),
      "an unmeasured component is itself a hard A+ blocker")
low = dict(good_row, aplus=dict(good_row["aplus"], a_plus_score=10.0))
check(audit.G_SCORE in audit.gates_failed(low),
      "a score under the A+ floor fails the score gate")

# The ordering artefact this whole tool is for: ICICIBANK's prior is a validated
# negative, so a row that is ALSO stale is recorded REJECT_DATA and the market
# refusal never appears in the label tally. The audit must see both.
bn_stale = dict(good_row, instrument="ICICIBANK", data_quality=p17quality.STALE)
bn_gates = audit.gates_failed(bn_stale)
check(audit.G_MARKET_PRIOR in bn_gates and audit.G_DATA in bn_gates,
      "a gate hidden behind an earlier one is still counted")
check(audit.first_gate(bn_gates) == audit.G_DATA,
      "the recorded label is reproduced from the first gate in engine order")
check(audit.GATE_LABEL[audit.G_MARKET_PRIOR] == p17aplus.REJECT_MARKET,
      "the market gate maps to the label the engine writes")
check(p17aplus.PRIOR_DEV_R["ICICIBANK"] < 0 <= p17aplus.PRIOR_DEV_R["NIFTY"],
      "the audit's premise holds: some instruments are refused on prior alone")

# The prior demotion. A negative the five-year pool could not separate from zero
# no longer refuses the instrument; it only lowers the ceiling. The five that stay
# negative out of sample, and the one validated negative, still refuse.
fnf = dict(good_row, instrument="FINNIFTY")
check(audit.G_MARKET_PRIOR not in audit.gates_failed(fnf)
      and audit.G_MARKET_PRIOR not in audit.gates_failed(
          dict(good_row, instrument="BANKNIFTY")),
      "a prior indistinguishable from zero is ranking information, not a veto")
check(p17aplus.PRIOR_DEV_R["FINNIFTY"] < 0
      and p17aplus.market_edge("FINNIFTY")[0] < p17aplus.market_edge("NIFTY")[0],
      "an admitted instrument keeps its worse prior as a lower market_edge")
check(audit.G_MARKET_PRIOR in audit.gates_failed(
          dict(good_row, instrument="ICICIBANK")),
      "the one validated negative keeps its refusal")
under_bar_names = ("INFY", "TCS", "RELIANCE", "SBIN", "HDFCBANK")
check(not any(audit.G_MARKET_PRIOR in audit.gates_failed(
                  dict(good_row, instrument=name))
              for name in under_bar_names),
      "a negative that missed the bar no longer refuses the instrument outright")
check(all(p17aplus.market_edge(name)[0] < p17aplus.market_edge("FINNIFTY")[0]
          for name in under_bar_names),
      "but it enters research ranked below every instrument with a better prior")
check(p17aplus.prior_label("ICICIBANK") == p17aplus.VALIDATED_NEGATIVE
      and p17aplus.prior_label("HDFCBANK") == p17aplus.NEGATIVE_BUT_UNDER_BAR
      and p17aplus.prior_label("BANKNIFTY")
      == p17aplus.INDISTINGUISHABLE_FROM_ZERO,
      "the six labels distinguish validated, under-bar and indistinguishable")
check(p17aplus.prior_label("CRUDEOIL") == p17aplus.INSUFFICIENT_DATA
      and p17aplus.prior_vetoes("CRUDEOIL")[0] is False,
      "an instrument the pool never measured is INSUFFICIENT_DATA, not a pass"
      " to veto")
check(set(p17aplus.VETO_LABELS) <= set(p17aplus.PRIOR_LABELS)
      and p17aplus.INDISTINGUISHABLE_FROM_ZERO not in p17aplus.VETO_LABELS,
      "only labels from the published set may refuse, and not the neutral one")

# A gate that is never a sole blocker cannot be the thing to change, which is the
# only actionable column in the report.
audit_rows = [good_row, dict(low, observation_id="A2"),
              dict(bn_stale, observation_id="A3")]
sole = [r for r in audit_rows if len(audit.gates_failed(r)) == 1]
check([r["observation_id"] for r in sole] == ["A2"],
      "only the score-blocked row has a single blocker")
check(audit.opportunity_key(good_row) == funnel.opportunity_key(good_row),
      "the audit and the funnel agree on what one opportunity is")

ceil = {c["instrument"]: c for c in audit.ceilings()}
check(ceil["ICICIBANK"]["refused_on_prior"] is True
      and ceil["ICICIBANK"]["t1_score_needed_yellow"] is None,
      "an instrument refused on its prior gets no score requirement, it is out")
check(ceil["NIFTY"]["a_plus_reachable"] is True,
      "A+ is arithmetically reachable on a positive-prior instrument")
check(ceil["NIFTY"]["ceiling_green_vehicle"]
      > ceil["NIFTY"]["ceiling_yellow_vehicle"],
      "a GREEN vehicle ceiling is above the YELLOW one")
check(ceil["MIDCPNIFTY"]["t1_score_needed_yellow"]
      < ceil["SENSEX"]["t1_score_needed_yellow"],
      "a better prior demands less of the room term to reach the same floor")
check(len([c for c in audit.ceilings() if c["refused_on_prior"]])
      == len([n for n in p17aplus.PRIOR_DEV_R
              if p17aplus.prior_vetoes(n)[0]]),
      "exactly the instruments the grader refuses are reported as refused")
check(ceil["BANKNIFTY"]["refused_on_prior"] is False
      and ceil["BANKNIFTY"]["a_plus_reachable"] is True,
      "an admitted research instrument has a reachable ceiling again")

audit_result = audit.build(None)
check(audit_result["thresholds_unchanged"] is True,
      "the audit states that it changed no threshold")
check(audit_result["cascade_mismatch_count"] == 0
      or audit_result["cascade_mismatches"],
      "a cascade mismatch is always accompanied by the offending rows")
check(audit_result["opportunities"] <= audit_result["ticks"],
      "distinct opportunities can never exceed recorded ticks")
check(set(audit_result["weights"]) == set(p17aplus.WEIGHTS),
      "the audit prints the same weight table the grader used")

# ---------------------------------------------------------------- grading A/B
# The audit found the prior gate refuses 8 of 11 instruments before the book is
# read, one of them on -0.0010R. These assert the A/B says so honestly: that the
# veto is only lifted where the evidence does not support it, that "not measured"
# is never dressed up as "tested and cleared", and that v2 exists only here.

bn_row = dict(good_row, instrument="BANKNIFTY", data_quality=p17quality.STALE)
check(ab.G_MARKET_PRIOR in ab.gates_failed(
          bn_row, binding_negative=ab.ALL_NEGATIVE_PRIORS),
      "v1: every negative prior refuses the instrument")
check(ab.G_MARKET_PRIOR not in ab.gates_failed(bn_row,
                                               binding_negative=frozenset()),
      "v2: a prior with no supported veto stops refusing the instrument")
check(ab.G_MARKET_PRIOR in ab.gates_failed(
          bn_row, binding_negative=frozenset({"BANKNIFTY"})),
      "v2 is not 'admit everything': a supported veto still refuses")
check(ab.gates_failed(good_row, binding_negative=frozenset({"BANKNIFTY"})) == [],
      "the A/B changes nothing for an instrument without a negative prior")
check(ab.G_MARKET_PRIOR not in ab.gates_failed(bn_row)
      and ab.G_MARKET_PRIOR in ab.gates_failed(bn_stale),
      "with no policy given the A/B follows the grader of record, so an audit "
      "reproduces recorded labels")
check(ab.ALL_NEGATIVE_PRIORS == frozenset(
          n for n, v in p17aplus.PRIOR_DEV_R.items() if v < 0),
      "v1 is replayable as its own explicit policy, not as a default")

# READY / PENDING_DATA / REJECTED. An unmeasured component must not look like a
# judgement: at the open, entry_edge is routinely unknown.
pending = dict(good_row, aplus=dict(good_row["aplus"], a_plus_score=61.0,
                                    missing_components=["entry_edge"]))
check(ab.state_of(ab.gates_failed(good_row)) == ab.READY,
      "a fully measured, fully clear candidate is A_PLUS_READY")
check(ab.state_of(ab.gates_failed(pending)) == ab.PENDING_DATA,
      "an otherwise clear candidate with a warming component is PENDING_DATA")
check(ab.state_of(ab.gates_failed(low)) == ab.REJECTED,
      "a measured candidate under the floor is REJECTED, not PENDING_DATA")
check(ab.state_of(ab.gates_failed(dict(pending, data_quality=p17quality.STALE)))
      == ab.REJECTED,
      "a real gate failure outranks a missing component")
check(ab.READY not in (ab.PENDING_DATA, ab.REJECTED)
      and ab.grade([pending], binding_negative=None, version=ab.V2,
                   key=audit.opportunity_key)["promotable_states"] == [ab.READY],
      "PENDING_DATA is not promotable — only READY is")

# Prior validation: the sign of a mean is not evidence; the standard error is.
weak = ab.validate_prior("FINNIFTY", [0.5, -0.52] * 40)
check(weak["verdict"] == ab.INDISTINGUISHABLE_FROM_ZERO
      and not weak["negative_veto_supported"],
      "a mean indistinguishable from zero does not support a permanent veto")
under = ab.validate_prior("INFY", [0.01, -0.02] * 40)
check(under["verdict"] == ab.NEGATIVE_BUT_UNDER_BAR
      and ab.NOMINAL_SIGMA <= under["sigma"] < ab.SIGMA_BAR,
      "a clearly negative mean that misses the strict bar is reported as "
      "negative, not as neutral")
check(ab.NEGATIVE_BUT_UNDER_BAR in ab.VETO_LIFTED_ON
      and ab.binding_negatives([under]) == frozenset(),
      "the same bar decides both directions: an unvalidated negative stops "
      "refusing")
check(under["verdict"] != ab.INDISTINGUISHABLE_FROM_ZERO
      and under["pool_mean_r"] < 0,
      "lifting the refusal does not restate the finding as neutral")
check(ab.validate_prior("MIDCPNIFTY", [-0.01, 0.02] * 40)["verdict"]
      == ab.POSITIVE_BUT_UNDER_BAR,
      "a positive mean under the bar is not promoted to validated either")
check(set(ab.VERDICTS) == {
          ab.VALIDATED_NEGATIVE, ab.NEGATIVE_BUT_UNDER_BAR,
          ab.INDISTINGUISHABLE_FROM_ZERO, ab.POSITIVE_BUT_UNDER_BAR,
          ab.VALIDATED_POSITIVE, ab.INSUFFICIENT_DATA},
      "the six verdicts are published as one set, shared with the grader")
check(ab.validate_prior("FINNIFTY", [-0.5, 0.1])["verdict"]
      == ab.INSUFFICIENT_DATA,
      "two rows produce no verdict at all")
strong = ab.validate_prior("BANKNIFTY", [-1.0, -0.9] * 40)
check(strong["verdict"] == ab.VALIDATED_NEGATIVE
      and strong["negative_veto_supported"] is True,
      "a mean well past the multiple-testing bar does support the veto")
check(ab.validate_prior("NIFTY", [1.0, 0.9] * 40)["verdict"]
      == ab.VALIDATED_POSITIVE,
      "a validated positive mean is reported as such, not as a veto")
check(strong["sigma_bar"] == ab.SIGMA_BAR and strong["comparisons"]
      == ab.COMPARISONS,
      "the bar and the number of comparisons travel with the verdict")
check(ab.binding_negatives([weak, strong]) == frozenset({"BANKNIFTY"}),
      "only instruments with a supported veto stay binding under v2")

split = ab.relaxed_by_v2([weak, ab.validate_prior("TCS", []), under])
check(split["tested_and_indistinguishable_from_zero"] == ["FINNIFTY"]
      and split["never_measured_in_this_pool"] == ["TCS"]
      and split["lifted_but_still_negative_under_bar"] == ["INFY"],
      "lifting a tested veto is not conflated with never having measured it, "
      "nor with a negative that only missed the bar")

# Out of sample. A pooled mean is one in-sample number, and vetoing an instrument
# for five years on one in-sample number is how the current rule came to be.
def _pool(inst, r_for_session, sessions=20, per=10):
    out = []
    for i in range(sessions):
        day = f"2026-0{1 + i // 28}-{1 + i % 28:02d}"
        for j in range(per):
            # Jittered: a pool with zero variance has no standard error, and a
            # mean with no standard error is INSUFFICIENT_DATA by design.
            val = r_for_session(i) + (0.2 if j % 2 else -0.2)
            out.append({"instrument": inst, "session": day, "r": val,
                        "exit_reason": "TARGET" if val > 0 else "STOP"})
    return out


periods = ab.split_chronological(_pool("TCS", lambda i: -1.0))
check(not (
    {r["session"] for r in periods["development"]}
    & {r["session"] for r in periods["holdout"]}
), "no session contributes rows to both development and the holdout")
check(len(periods["holdout"]) >= ab.MIN_HOLDOUT_ROWS
      and periods["session_spans"]["holdout"][0]
      > periods["session_spans"]["development"][1],
      "the holdout is the LAST block of sessions, not a random sample")

held = ab.validate_instrument("TCS", _pool("TCS", lambda i: -1.0))
check(held["verdict"] == ab.VALIDATED_NEGATIVE
      and held["holdout_keeps_the_sign"] is True
      and held["walk_forward"]["stable_negative"] is True,
      "a veto survives when the pool, the holdout and every fold agree")
check(held["veto_retained_under_v2"] is True,
      "a validated negative veto is retained under v2")

flipped = ab.validate_instrument(
    "TCS", _pool("TCS", lambda i: 2.0 if 8 <= i < 12 else -1.0))
check(flipped["pooled_verdict"] == ab.VALIDATED_NEGATIVE
      and flipped["verdict"] == ab.NEGATIVE_BUT_UNDER_BAR
      and flipped["downgraded_because"] == "SIGN_FLIPS_ACROSS_FOLDS"
      and flipped["veto_retained_under_v2"] is False,
      "a mean carried by one stretch of history does not survive the bar, so "
      "its refusal is lifted — while still reported as negative, not neutral")

late = ab.validate_instrument("TCS", _pool("TCS", lambda i: -1.0 if i < 14
                                           else 1.0))
check(late["verdict"] == ab.INDISTINGUISHABLE_FROM_ZERO
      and late["downgraded_because"] == "HOLDOUT_DOES_NOT_KEEP_THE_SIGN"
      and late["holdout_keeps_the_sign"] is False
      and late["veto_retained_under_v2"] is False,
      "a veto that the holdout contradicts is not supported by the holdout")

thin = ab.validate_instrument("TCS", _pool("TCS", lambda i: -1.0, sessions=4,
                                           per=9))
check(thin["verdict"] == ab.INSUFFICIENT_DATA
      and thin["downgraded_because"] == "HOLDOUT_TOO_SMALL_TO_READ"
      and thin["veto_retained_under_v2"] is True,
      "a holdout too small to read leaves the veto exactly as production has it")
check(ab.binding_negatives([thin]) == frozenset({"TCS"}),
      "v2 does not lift a veto on an instrument the pool never measured")
four = ab.relaxed_by_v2([held, late, thin, flipped])
check(four["kept_validated_negative"] == ["TCS"]
      and four["tested_and_indistinguishable_from_zero"] == ["TCS"]
      and four["never_measured_in_this_pool"] == ["TCS"]
      and four["lifted_but_still_negative_under_bar"] == ["TCS"],
      "the four prior groups are reported separately, never merged")
check(ab.binding_negatives([held, late, thin, flipped]) == frozenset({"TCS"})
      and set(four["kept_validated_negative"])
      | set(four["never_measured_in_this_pool"]) == {"TCS"},
      "only the validated and the never-measured groups still refuse")
check(ab.validate_instrument("MIDCPNIFTY", _pool("MIDCPNIFTY", lambda i: -1.0))
      ["veto_retained_under_v2"] is False,
      "a positive-prior instrument has no veto to retain either way")
check(ab._pf_and_drawdown([1.0, -1.0, -1.0, 2.0]) == (1.5, -2.0),
      "drawdown is peak-to-trough over the sequence, so the order matters")
shuffled = ab.validate_instrument(
    "TCS", list(reversed(_pool("TCS", lambda i: -1.0))))
check(shuffled["periods"]["holdout"]["max_drawdown_r"]
      == held["periods"]["holdout"]["max_drawdown_r"]
      and shuffled["period_spans"] == held["period_spans"],
      "an unsorted pool file is put in chronological order before it is cut")
check(held["periods"]["holdout"]["profit_factor"] == 0.0
      and held["periods"]["holdout"]["max_drawdown_r"] < 0,
      "PF and drawdown are reported per period, not only pooled")

wf_thin = ab.walk_forward(_pool("TCS", lambda i: -1.0, sessions=6, per=2))
check(wf_thin["verdict"] == ab.INSUFFICIENT_DATA
      and wf_thin["folds_negative"] == 0,
      "folds too small to read are counted as unmeasured, not as agreeing")

ab_rows = [good_row, dict(low, observation_id="B2"),
           dict(bn_stale, observation_id="B3"),
           dict(pending, observation_id="B4", instrument="FINNIFTY")]
v1 = ab.grade(ab_rows, binding_negative=ab.ALL_NEGATIVE_PRIORS, version=ab.V1,
              key=audit.opportunity_key)
v2 = ab.grade(ab_rows, binding_negative=frozenset(), version=ab.V2,
              key=audit.opportunity_key)
check(v1["tick_states"][ab.PENDING_DATA] == 0
      and v2["tick_states"][ab.PENDING_DATA] == 1,
      "v2 moves a FINNIFTY candidate out of REJECTED and into PENDING_DATA")
check(v1["ticks"] == v2["ticks"] == len(ab_rows),
      "both versions are replayed over exactly the same rows")
check(sum(v1["opportunity_states"].values()) == v1["opportunities"],
      "every opportunity lands in exactly one state")
check(v1["opportunities"] == len({audit.opportunity_key(r) for r in ab_rows}),
      "opportunities are counted on the shared key, not per tick")

ab_result = ab.compare(ab_rows, [weak, strong], key=audit.opportunity_key,
                       sessions=2)
check(ab_result["production_grader_unchanged"] is True
      and ab_result["research_only"] is True,
      "the A/B declares itself research-only and the grader untouched")
check(ab_result["outcome_basis"] == "RESOLVED_LEGS_ONLY",
      "outcomes are never modelled for a candidate that was never entered")
check(ab_result["v2_keeps_vetoing"] == ["BANKNIFTY"],
      "the supported veto survives the comparison")
check(audit.gates_failed is ab.gates_failed and audit.ORDER is ab.ORDER,
      "the audit and the A/B read one implementation of the gate rule")

# --- room A/B ----------------------------------------------------------------
# `room` could only ever be measured on the leg production was recommending, so
# every other recorded candidate was unmeasurable by construction. The replay
# re-grades rows already on disk to say what a derived target changes — and must
# reproduce the old grade exactly where an engine target existed.


def _obs_row(*, target1: float | None) -> dict:
    q = p17schema.Quote(
        instrument="NIFTY", vehicle=p17schema.CE, symbol="NIFTY24500CE",
        strike=24500.0, expiry="2026-07-16", days_to_expiry=2,
        expiry_class=p17schema.PRE_EXPIRY, bid=100.0, ask=101.0, premium=100.5,
        oi=500000, volume=120000, iv=14.5, delta=0.5, underlying_price=24510.0,
        atm_strike=24500.0, moneyness=p17schema.ATM, distance_from_atm=0.0,
        strike_steps_from_atm=0, source=p17quality.WEBSOCKET, feed_age_ms=300.0,
        signal_to_snapshot_ms=120.0, snapshot_ts=1_800_000_000.0,
        data_quality=p17quality.EXACT,
    )
    obs = p17schema.Observation(
        observation_id=f"R-{target1}", signal_ts=1_800_000_000.0,
        capture_ts=1_800_000_000.2, instrument="NIFTY", family="INDEX",
        candidate_class=p17schema.BUY if target1 else p17schema.WAIT,
        direction="BULLISH", selected_vehicle=p17schema.CE, session="2026-07-14",
        context=p17schema.MarketContext(direction="BULLISH", regime="TREND"),
        selected=q, opposite=p17schema.Quote(
            instrument="NIFTY", vehicle=p17schema.PE, symbol="NIFTY24500PE",
            bid=95.0, ask=96.0, premium=95.5, delta=-0.5,
            source=p17quality.WEBSOCKET, snapshot_ts=1_800_000_000.0,
            data_quality=p17quality.EXACT,
        ),
        plan=p17schema.Plan(
            entry=101.0, stop=91.0, target1=target1, risk=10.0,
            expected_move_points=40.0,
        ),
    )
    p17service.enrich(obs)
    return obs.as_dict()


engine_row, derived_row = _obs_row(target1=115.0), _obs_row(target1=None)
check(engine_row["reach"]["t1_basis"] == "ENGINE_TARGET"
      and derived_row["reach"]["t1_basis"] == "MODELLED_FROM_EXPECTED_MOVE",
      "a candidate with no published target is graded against a derived one")
check(engine_row["aplus"]["promotable"] is True
      and derived_row["aplus"]["promotable"] is False,
      "only the engine-target row is eligible for a promotion sample")

rt = p17schema.Observation.from_dict(engine_row)
check(rt.plan.target1 == 115.0 and rt.selected is not None
      and rt.selected.ask == 101.0 and rt.context.direction == "BULLISH",
      "a recorded row rebuilds into the observation it was written from")
check(rt.data_quality == engine_row["data_quality"]
      and rt.both_sides == engine_row["both_sides"],
      "and derived verdicts are recomputed, not read back off the row")

# What that same candidate looked like when it was recorded, before a derived
# target existed: no T1, so no room, so A+ was unreachable however good it was.
as_recorded = p17schema.Observation.from_dict(derived_row)
as_recorded.reach = {
    "t1_score": None, "t1_rank": p17reach.RANK_UNKNOWN, "reasons": ["NO_T1"],
    "components": {}, "t1_basis": p17reach.BASIS_NONE,
}
as_recorded.aplus = p17aplus.score(as_recorded)
old_row = as_recorded.as_dict()
check("room" in (old_row["aplus"]["missing_components"] or []),
      "before the change, a candidate with no published target had no room at all")

room = roomab.replay([engine_row, old_row])
check(room["t1_basis"] == {"ENGINE_TARGET": 1,
                           "MODELLED_FROM_EXPECTED_MOVE": 1},
      "the replay reports what each row was measured against")
check(room["room_measured_v1"] == 1 and room["room_measured_v2"] == 2,
      "the derived target is what makes the extra row measurable at all")
check(room["regression_mismatch_count"] == 0
      and room["verdict"] != roomab.INVALID,
      "re-grading an engine-target row reproduces the grade it was given")
check(room["promotable_opportunities_v2"] <= room["a_plus_opportunities_v2"],
      "a derived-target A+ is never counted as promotable")
check(room["room_still_unmeasured_because"] == {},
      "with nothing left unmeasured the reason tally is empty, not absent")

# A derived target does not rescue a row whose book was never captured, and the
# report has to say which constraint is now the binding one.
no_book = p17schema.Observation.from_dict(
    dict(derived_row,
         selected=dict(derived_row["selected"],
                       data_quality=p17quality.MISSING)))
no_book.reach = p17reach.assess(no_book.selected, no_book.plan, no_book.economics)
no_book.aplus = p17aplus.score(no_book)
stuck = roomab.replay([no_book.as_dict()])
check(stuck["room_measured_v2"] == 0
      and stuck["room_still_unmeasured_because"]
      == {f"QUALITY_{p17quality.MISSING}": 1},
      "an uncaptured book still refuses room, and the surviving reason is named")

real = engine_row["aplus"]["a_plus_label"]
wrong = p17aplus.WATCH if real != p17aplus.WATCH else p17aplus.A_PLUS
broken = dict(engine_row, aplus=dict(engine_row["aplus"], a_plus_label=wrong))
bad_run = roomab.replay([broken])
check(bad_run["regression_mismatch_count"] == 1
      and bad_run["verdict"] == roomab.INVALID,
      "a disagreement on an engine-target row invalidates the run loudly")

print(f"checked {CHECKS}")
if FAILURES:
    for f in FAILURES:
        print("FAIL:", f)
    raise SystemExit(f"phase19 smoke: {len(FAILURES)} FAILURE(S)")
print("phase19 smoke: OK")
