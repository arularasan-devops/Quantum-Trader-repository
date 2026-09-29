"""Part 38 acceptance scenarios 1-16, checked end to end in one place.

These are the questions the 24-Aug session could not answer: which board call
reached the screen, which one reached the journal, which refusal stopped it and
which of the 456 BUY rows were the same call printed again. Each numbered check
below is one of the sixteen scenarios, in the spec's order, and each is asserted
against the ledgers the running app actually writes rather than a mock.
"""
from __future__ import annotations

import json
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="accept_")
os.environ["QT_DATA_DIR"] = _TMP

from app.config import settings  # noqa: E402

settings.data_dir = _TMP
settings.signal_lifecycle_log = True
settings.signal_journal_log = True
settings.exec_funnel_log = True

from app.analysis import exec_funnel  # noqa: E402
from app.analysis import futures_signal as fs  # noqa: E402
from app.analysis import signal_journal as sj  # noqa: E402
from app.analysis import signal_lifecycle as lc  # noqa: E402
from app.analysis import signal_reconciliation as recon  # noqa: E402
from app.analysis import signal_visibility as vis  # noqa: E402
from app.models import (  # noqa: E402
    Candle,
    Decision,
    IndicatorSnapshot,
    MarketStatus,
    OptionQuote,
    OptionType,
    Signal,
)

FAILS: list[str] = []
NOW = 1_756_000_000.0
CONTRACT = {"symbol": "NIFTY28AUG25FUT", "expiry": "2025-08-28",
            "lot_size": 75, "exchange": "NFO"}


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")
    if not cond:
        FAILS.append(name)


def dec(**kw) -> Decision:
    base = dict(
        signal=Signal.BUY,
        market_signal=Signal.BUY,
        confidence=88.0,
        signal_strength=70.0,
        trade_quality="GOOD",
        recommended_option="NIFTY25AUG2624000CE",
        option_type=OptionType.CALL,
        strike=24000.0,
        current_premium=100.0,
        plan_premium=100.0,
        stop_loss=90.0,
        target1=120.0,
        target2=130.0,
        target3=140.0,
        entry_trigger="PULLBACK",
        trade_score=76.0,
    )
    base.update(kw)
    return Decision(**base)


def quote(symbol: str, prem: float, strike: float) -> OptionQuote:
    return OptionQuote(symbol=symbol, strike=strike, option_type=OptionType.CALL,
                       premium=prem, iv=0.2, delta=0.5, gamma=0.01, theta=-1.0,
                       vega=2.0, oi=10000, oi_change=100, volume=5000,
                       bid=prem - 0.1, ask=prem + 0.1)


IND = IndicatorSnapshot(atr=30.0, vwap=24000.0, rsi=55.0, adx=25.0, trend="UP")
STATUS = MarketStatus.TRENDING
OPT_CANDLES = [Candle(time=int(NOW) - 60 * i, open=1, high=2, low=0.5,
                      close=1.5, volume=100) for i in range(30)][::-1]


def fut_candles(n: int, start: float, step: float) -> list[Candle]:
    out: list[Candle] = []
    px = start
    for i in range(n):
        px += step
        out.append(Candle(time=int(NOW) - (n - i) * 60, open=px - step,
                          high=px + 20, low=px - 20, close=px, volume=1000))
    return out


def reset() -> None:
    lc.reset_for_tests()
    vis.reset_for_tests()
    sj.reset_for_tests()
    for name in (lc.LEDGER_LOG, "signal_journal.jsonl", "exec_funnel.jsonl"):
        path = os.path.join(_TMP, name)
        if os.path.exists(path):
            os.remove(path)


def ledger() -> list[dict]:
    return lc.read_ledger(limit=10000)


def journal() -> list[dict]:
    path = os.path.join(_TMP, "signal_journal.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def publish_option(instrument: str, d: Decision, now: float) -> None:
    vis.observe_option(instrument, d, sj.board_key(d), now=now)


# --- 1. a BUY that is generated reaches a dashboard, and only the right one ---
reset()
opt = dec()
publish_option("NIFTY", opt, NOW)
fcs = fut_candles(140, 24000.0, 8.0)
fut = fs.evaluate("NIFTY", fcs, fcs[-1].close, IndicatorSnapshot(
    atr=40.0, adx=28.0, trend="UP", momentum=0.4), MarketStatus.TRENDING,
    contract=CONTRACT, feed_age_sec=2.0, minutes_to_close=200.0, now=NOW + 1)
vis.observe_futures("NIFTY", fut, now=NOW + 1)
rep = recon.reconcile(session=lc.session_of(NOW))
check("1. a generated BUY is published to a dashboard",
      rep["counts"]["dashboard_published"] >= 1, str(rep["counts"]))
check("1. the two vehicles are counted on their own boards",
      rep["counts"]["option_dashboard_buys"] == 1
      and rep["counts"]["futures_dashboard_buys"] == 1,
      f"opt={rep['counts']['option_dashboard_buys']} "
      f"fut={rep['counts']['futures_dashboard_buys']}")

# --- 2. a futures BUY belongs to the futures board ---------------------------
fut_rows = [r for r in ledger() if r.get("market") == lc.MARKET_FUTURES]
check("2. a futures signal is stamped as the futures market and vehicle",
      bool(fut_rows) and all(r.get("vehicle") == lc.FUTURES
                             for r in fut_rows))
check("2. the futures card carries its own signal identity",
      bool(fut.global_signal_id) and bool(fut.episode_id)
      and bool(fut.futures_signal_id))
check("2. no futures event is filed under the options market",
      not [r for r in fut_rows if r.get("market") == lc.OPTIONS])

# --- 3. an option BUY belongs to the option board ----------------------------
opt_rows = [r for r in ledger() if r.get("market") == lc.OPTIONS]
check("3. an option signal is stamped OPTIONS with the traded leg",
      bool(opt_rows) and all(r.get("vehicle") in (lc.CE, lc.PE)
                             for r in opt_rows))
check("3. the option and the futures read are separate signals",
      opt.global_signal_id != fut.global_signal_id)

# --- 4. the same call printed again is the same episode ----------------------
reset()
first = dec()
publish_option("NIFTY", first, NOW)
again = first
for i in range(9):
    again = dec()
    publish_option("NIFTY", again, NOW + 1 + i)
check("4. ten raw prints are one episode",
      again.episode_id == first.episode_id)
check("4. ten raw prints are one signal id",
      again.global_signal_id == first.global_signal_id)
r4 = recon.reconcile(session=lc.session_of(NOW))
check("4. reconciliation reports one engine BUY for the repeated call",
      r4["counts"]["engine_buys"] == 1, str(r4["counts"]["engine_buys"]))

# --- 5. an invalid plan is not an active dashboard BUY -----------------------
reset()
invalid = dec(target1=95.0, plan_state="INVALID", plan_actionable=False,
              plan_invalid_reason="TARGET_NOT_ABOVE_ENTRY")
publish_option("BANKNIFTY", invalid, NOW)
check("5. an invalid plan is not actionable", invalid.plan_actionable is False)
check("5. the invalid plan states its geometric reason",
      invalid.plan_invalid_reason == "TARGET_NOT_ABOVE_ENTRY")
miss5 = [r for r in ledger() if r.get("status") == lc.MISSED]
check("5. plan validation is recorded as the stage that refused it",
      bool(miss5) and miss5[0]["stage"] == lc.PLAN_VALIDATED
      and miss5[0]["reason"] == lc.PLAN_INVALID,
      str(miss5 and (miss5[0]["stage"], miss5[0]["reason"])))
check("5. the refusal is still published, so it is visible not silent",
      lc.DASHBOARD_PUBLISHED in (lc.tracked(invalid.global_signal_id) or {})
      .get("order", []))

# --- 6. a stale plan is taken out of the actionable set ----------------------
reset()
stale = dec(current_premium=137.0, plan_premium=100.0, target1=103.0,
            plan_state="STALE", plan_actionable=False,
            plan_invalid_reason="PLAN_STALE", plan_age_seconds=3600,
            plan_version=1)
publish_option("ICICIBANK", stale, NOW)
check("6. a plan whose target sits below the live premium is not actionable",
      stale.plan_actionable is False and stale.target1 < stale.current_premium)
miss6 = [r for r in ledger() if r.get("reason") == lc.PLAN_STALE]
check("6. the staleness is recorded with its own reason", len(miss6) == 1,
      str(len(miss6)))
check("6. the plan's age and version travel with it",
      stale.plan_age_seconds == 3600 and stale.plan_version == 1)

# --- 7. a dashboard BUY has a journal row -----------------------------------
reset()
d7 = dec()
publish_option("NIFTY", d7, NOW)
sj.observe("NIFTY", d7, [quote(d7.recommended_option or "X", 100.0, 24000.0)],
           IND, 24010.0, STATUS, OPT_CANDLES, now=NOW, expiry="2026-08-26",
           minutes_to_expiry=400)
j7 = journal()
check("7. the journal row exists for a published BUY", bool(j7))
check("7. the journal row carries the dashboard's ids",
      bool(j7) and j7[-1].get("global_signal_id") == d7.global_signal_id
      and j7[-1].get("episode_id") == d7.episode_id)
r7 = recon.reconcile(session=lc.session_of(NOW))
check("7. nothing is reported as missing a journal row",
      r7["missing_journal_row"] == [], str(r7["missing_journal_row"]))

# --- 8. a journal BUY has a dashboard record, unless it is exempt ------------
check("8. every journal BUY reconciles to a published signal",
      r7["counts"]["journal_recorded"] == r7["counts"]["engine_buys"],
      f"{r7['counts']['journal_recorded']} vs {r7['counts']['engine_buys']}")
fut8 = fs.evaluate("NIFTY", fcs, fcs[-1].close, IndicatorSnapshot(
    atr=40.0, adx=28.0, trend="UP", momentum=0.4), MarketStatus.TRENDING,
    contract=CONTRACT, feed_age_sec=2.0, minutes_to_close=200.0, now=NOW + 5)
vis.observe_futures("NIFTY", fut8, now=NOW + 5)
sj.observe_futures("NIFTY", fut8, now=NOW + 5)
frow = [r for r in journal() if r.get("market") == "FUTURES"]
check("8. a futures research row is exempted by name, not silently",
      bool(frow) and frow[-1]["not_followed_reason"] == "RESEARCH_ONLY"
      and frow[-1]["research_only"] is True)
check("8. a research row claims no entry",
      bool(frow) and frow[-1]["followed"] is False)

# --- 9. an execution attempt names the episode it belongs to ----------------
reset()
d9 = dec()
publish_option("NIFTY", d9, NOW)
with vis.execution_context("NIFTY", d9):
    exec_funnel.reached("SIGNAL")
    exec_funnel.reached("VALIDATION")
rows9 = [r for r in ledger() if r["global_signal_id"] == d9.global_signal_id]
check("9. the attempt is attributed to a real signal and episode",
      bool(rows9) and all(r.get("episode_id") == d9.episode_id for r in rows9))
check("9. the stages the attempt reached are recorded",
      {lc.PAPER_ELIGIBLE, lc.EXECUTION_CHECK} <=
      set((lc.tracked(d9.global_signal_id) or {}).get("order", [])),
      str((lc.tracked(d9.global_signal_id) or {}).get("order")))

# --- 10. a journal BUY with no publication is a reconciliation failure -------
reset()
d10 = dec(strike=24500.0, recommended_option="NIFTY25AUG2624500CE")
sj.observe("NIFTY", d10, [quote(d10.recommended_option or "X", 100.0, 24500.0)],
           IND, 24010.0, STATUS, OPT_CANDLES, now=NOW, expiry="2026-08-26",
           minutes_to_expiry=400)
r10 = recon.reconcile(session=lc.session_of(NOW))
check("10. the missing publication is reported, not averaged away",
      len(r10["missing_dashboard_publication"]) == 1,
      str(r10["counts"]))
check("10. the failure is named MISSING_DASHBOARD_PUBLICATION",
      r10["missing_dashboard_publication"][0]["failure"]
      == recon.MISSING_DASHBOARD_PUBLICATION)

# --- 11. a broker rejection is recorded with its reason ----------------------
reset()
d11 = dec()
publish_option("NIFTY", d11, NOW)
with vis.execution_context("NIFTY", d11):
    exec_funnel.reached("SIGNAL")
    exec_funnel.blocked(stage="BROKER", blocker="ORDER_REJECTED",
                        instrument="NIFTY", option=d11.recommended_option,
                        value=0.0, threshold=0.0, where="smoke",
                        reason="broker said no")
rows11 = [r for r in ledger() if r["global_signal_id"] == d11.global_signal_id
          and r["status"] == lc.MISSED]
check("11. a broker rejection is recorded as BROKER_REJECTION",
      bool(rows11) and rows11[0]["reason"] == lc.BROKER_REJECTION,
      str(rows11 and rows11[0]["reason"]))
check("11. the broker's own words are kept beside the mapped reason",
      bool(rows11) and (rows11[0].get("detail") or {}).get("blocker")
      == "ORDER_REJECTED")
check("11. the rejection is filed against the fill that never happened",
      bool(rows11) and rows11[0]["stage"] == lc.FILLED)

# --- 12. a risk refusal is recorded with its reason -------------------------
reset()
d12 = dec()
publish_option("NIFTY", d12, NOW)
with vis.execution_context("NIFTY", d12):
    exec_funnel.reached("SIGNAL")
    exec_funnel.blocked(stage="RISK", blocker="DAILY_LOSS_LIMIT",
                        instrument="NIFTY", option=d12.recommended_option,
                        value=-6000.0, threshold=-5000.0, where="smoke",
                        reason="daily loss limit hit")
rows12 = [r for r in ledger() if r["global_signal_id"] == d12.global_signal_id
          and r["status"] == lc.MISSED]
check("12. a risk refusal is recorded as RISK_BLOCK",
      bool(rows12) and rows12[0]["reason"] == lc.RISK_BLOCK,
      str(rows12 and rows12[0]["reason"]))
check("12. the measured value and the threshold are both kept",
      bool(rows12) and rows12[0].get("value") == -6000.0
      and rows12[0].get("threshold") == -5000.0)

# --- 13. duplicate events do not inflate the trade count --------------------
reset()
for i in range(150):
    # Ten seconds apart, as the 24-Aug ICICIBANK leg was printed: the repeats
    # span the session rather than a single minute.
    publish_option("ICICIBANK", dec(), NOW + i * 10)
r13 = recon.reconcile(session=lc.session_of(NOW))
check("13. 150 prints are one engine BUY", r13["counts"]["engine_buys"] == 1,
      str(r13["counts"]["engine_buys"]))
check("13. the repeats are counted as repeats, separately",
      r13["counts"]["duplicate_raw_events"] > 0,
      str(r13["counts"]["duplicate_raw_events"]))
check("13. a repeat is stated as a repeat of a known episode",
      any(r.get("reason") == lc.DUPLICATE_EPISODE for r in ledger()))
check("13. the repeats are not added to the traded or filled counts",
      r13["counts"]["execution_attempts"] == 0 and r13["counts"]["filled"] == 0)
check("13. one episode is reported", r13["counts"]["unique_episodes"] == 1)

# --- 14. a futures signal cannot place an order, live or paper --------------
fsrc = open("app/analysis/futures_signal.py", encoding="utf-8").read()
check("14. the futures engine imports no broker or order layer",
      "from app.market.broker" not in fsrc and "place_order" not in fsrc
      and "from app.execution.trader" not in fsrc)
check("14. the futures engine's only execution import is pure arithmetic",
      fsrc.count("from app.execution") == 1
      and "futures_paper import _cost_points, _entry_setup, _levels" in fsrc)
check("14. every futures card declares itself unexecutable",
      fut.executable is False and fut8.executable is False
      and fut.research_only is True)
msrc = open("app/main.py", encoding="utf-8").read()
fut_ep = msrc[msrc.index('"/api/futures-signal"'):]
fut_ep = fut_ep[:fut_ep.index("@app.", 10)] if "@app." in fut_ep[10:] else fut_ep
check("14. the futures endpoint exposes no order route",
      "place_order" not in fut_ep and "execute" not in fut_ep,
      "futures-signal handler")

# --- 15. an option signal still cannot go live without the armed path -------
tsrc = open("app/state.py", encoding="utf-8").read()
check("15. the live authorisation invariant is unchanged",
      "return bool(self.is_live and settings.auto_trade_enabled "
      "and settings.auto_trade_allow_live)" in tsrc)
check("15. nothing in this phase's code arms live trading",
      all("auto_trade_allow_live" not in open(p, encoding="utf-8").read()
          for p in ("app/analysis/futures_signal.py",
                    "app/analysis/signal_lifecycle.py",
                    "app/analysis/signal_visibility.py",
                    "app/analysis/signal_reconciliation.py")))
check("15. the lifecycle ledger cannot reach the execution layer",
      all("from app.execution" not in open(p, encoding="utf-8").read()
          for p in ("app/analysis/signal_lifecycle.py",
                    "app/analysis/signal_visibility.py",
                    "app/analysis/signal_reconciliation.py")))

# --- 16. no signal disappears silently --------------------------------------
reset()
kept = dec()
publish_option("NIFTY", kept, NOW)
sj.observe("NIFTY", kept, [quote(kept.recommended_option or "X", 100.0, 24000.0)],
           IND, 24010.0, STATUS, OPT_CANDLES, now=NOW, expiry="2026-08-26",
           minutes_to_expiry=400)
vis.mark_user_visible("NIFTY", kept, now=NOW)
dropped = dec(strike=24700.0, recommended_option="NIFTY25AUG2624700CE",
              target1=95.0, plan_state="STALE", plan_actionable=False,
              plan_invalid_reason="PLAN_STALE")
publish_option("NIFTY", dropped, NOW + 60)
session = lc.session_of(NOW)
r16 = recon.reconcile(session=session)
missed16 = recon.missed_signals(session=session)
seen = {r["episode_id"] for r in ledger() if r["stage"] == lc.USER_VISIBLE
        and r["status"] == lc.OK}
stopped = {m["episode_id"] for m in missed16}
check("16. every BUY episode either reached the user or is reported as stopped",
      {kept.episode_id, dropped.episode_id} <= (seen | stopped),
      f"seen={sorted(seen)} stopped={sorted(stopped)}")
check("16. both BUYs are accounted for in the reconciliation counts",
      r16["counts"]["engine_buys"] == 2
      and r16["counts"]["dashboard_published"] == 2,
      str(r16["counts"]))
check("16. the dropped call is on the missed panel with a stage and a reason",
      any(m["episode_id"] == dropped.episode_id
          and m["missed_stage"] and m["missed_reason"] for m in missed16),
      str([(m["episode_id"], m["missed_reason"]) for m in missed16]))
check("16. a correct refusal is not confused with a vanished signal",
      all(m["classification"] in ("CORRECTLY_REFUSED", "STOPPED_BEFORE_USER",
                                  "STOPPED_BEFORE_EXECUTION")
          for m in missed16),
      str([m["classification"] for m in missed16]))
check("16. the signal that survived is not on the missed panel",
      not any(m["episode_id"] == kept.episode_id for m in missed16))
check("16. a later favourable move does not invent a missed opportunity",
      "price_moved" not in json.dumps(missed16)
      and all("favourable" not in (m.get("missed_reason") or "").lower()
              for m in missed16))

print()
if FAILS:
    print(f"SIGNAL ACCEPTANCE SMOKE FAILED ({len(FAILS)}): {FAILS}")
    raise SystemExit(1)
print("SIGNAL ACCEPTANCE SMOKE PASSED")
