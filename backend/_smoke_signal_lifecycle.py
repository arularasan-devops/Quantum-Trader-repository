"""Smoke checks for the signal lifecycle, visibility audit and reconciliation.

Each check corresponds to something the 24-Aug session could not answer:
  1. 2,772 journal rows and 456 board BUY rows, with no way to prove which of
     them ever reached the dashboard — a signal could disappear silently;
  2. 150 raw BUY events on one leg, indistinguishable in a count from 150 calls
     that were never published (duplicate vs missing);
  3. 2,857 funnel refusals that could not be attributed to the signal they
     refused, so "BUY on screen, no order" had no per-signal answer.
"""
from __future__ import annotations

import os
import tempfile
import time

_TMP = tempfile.mkdtemp()
os.environ["QT_DATA_DIR"] = _TMP

from app.analysis import exec_funnel  # noqa: E402
from app.analysis import signal_journal as sj  # noqa: E402
from app.analysis import signal_lifecycle as lc  # noqa: E402
from app.analysis import signal_reconciliation as recon  # noqa: E402
from app.analysis import signal_visibility as vis  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import (  # noqa: E402
    Candle,
    Decision,
    IndicatorSnapshot,
    MarketStatus,
    OptionQuote,
    OptionType,
    Signal,
)

settings.data_dir = _TMP
settings.signal_lifecycle_log = True
settings.signal_journal_log = True
settings.exec_funnel_log = True

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    if not ok:
        _failures.append(label)


def _dec(**kw) -> Decision:
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
        stop_loss=90.0,
        target1=120.0,
        target2=130.0,
        target3=140.0,
        entry_trigger="PULLBACK",
        trade_score=76.0,
    )
    base.update(kw)
    return Decision(**base)


def _reset() -> None:
    """Clear both the in-memory trackers and the ledger file.

    The ledger is append-only by design, so a check that counts rows has to
    start from an empty file or it counts the previous check's evidence.
    """
    lc.reset_for_tests()
    vis.reset_for_tests()
    sj.reset_for_tests()
    for name in (lc.LEDGER_LOG, "signal_journal.jsonl", "exec_funnel.jsonl"):
        path = os.path.join(_TMP, name)
        if os.path.exists(path):
            os.remove(path)


def _stages(gsid: str) -> list[str]:
    entry = lc.tracked(gsid) or {}
    return list(entry.get("order", []))


def _ledger() -> list[dict]:
    return lc.read_ledger(limit=10000)


# --- 1. a fresh BUY walks the chain and is named -----------------------------
_reset()
d1 = _dec()
vis.observe_option("NIFTY", d1, sj.board_key(d1), now=1_700_000_000.0)
check("a signal gets a global id", bool(d1.global_signal_id), d1.global_signal_id or "")
check("a signal gets an episode id", bool(d1.episode_id), d1.episode_id or "")
check("the vehicle is recorded", d1.vehicle == "CE", str(d1.vehicle))
check("the market is recorded", d1.market == "OPTIONS", str(d1.market))
st1 = _stages(d1.global_signal_id)
for stage in (lc.GENERATED, lc.CLASSIFIED, lc.PLAN_CREATED, lc.PLAN_VALIDATED,
              lc.DASHBOARD_PUBLISHED):
    check(f"a valid BUY records {stage}", stage in st1, ",".join(st1))
check("every event carries an event_id",
      all(r.get("event_id") for r in _ledger()))
check("every event carries a stage, status and source",
      all(r.get("stage") and r.get("status") and "source" in r for r in _ledger()))

# --- 2. the same call again is a repeat, not a second signal -----------------
first_id = d1.global_signal_id
for i in range(5):
    d = _dec()
    vis.observe_option("NIFTY", d, sj.board_key(d), now=1_700_000_010.0 + i)
check("5 raw events collapse into one episode",
      d.episode_id == d1.episode_id, f"{d.episode_id} vs {d1.episode_id}")
check("a repeat keeps the same global signal id", d.global_signal_id == first_id)
check("a repeat does not restate the whole chain",
      _stages(first_id).count(lc.GENERATED) == 1,
      str(_stages(first_id).count(lc.GENERATED)))

# --- 3. a different leg is a different episode, and the old one is superseded -
d2 = _dec(recommended_option="NIFTY25AUG2624100CE", strike=24100.0)
vis.observe_option("NIFTY", d2, sj.board_key(d2), now=1_700_000_100.0)
check("a different leg is a different episode", d2.episode_id != d1.episode_id)
check("a different leg is a different signal", d2.global_signal_id != first_id)
sup = [r for r in _ledger() if r.get("reason") == lc.EPISODE_SUPERSEDED]
check("the replaced call is superseded, not lost", len(sup) == 1, str(len(sup)))
check("superseded names the signal that replaced it",
      bool(sup and (sup[0].get("detail") or {}).get("superseded_by")
           == d2.global_signal_id))

# --- 4. an unenterable plan is a MISSED stage with a reason ------------------
_reset()
stale = _dec(target1=95.0, plan_state="STALE", plan_actionable=False,
             plan_invalid_reason="TARGET_NOT_ABOVE_ENTRY")
vis.observe_option("BANKNIFTY", stale, sj.board_key(stale), now=1_700_001_000.0)
miss = [r for r in _ledger() if r.get("status") == lc.MISSED]
check("a stale plan is recorded as a miss", len(miss) == 1, str(len(miss)))
check("the missed stage is plan validation",
      bool(miss) and miss[0]["stage"] == lc.PLAN_VALIDATED)
check("the missed reason is PLAN_STALE",
      bool(miss) and miss[0]["reason"] == lc.PLAN_STALE)
check("a stale plan is still published so the user sees the downgrade",
      lc.DASHBOARD_PUBLISHED in _stages(stale.global_signal_id))
check("an invalid plan is distinguished from a stale one",
      (lambda d: (vis.observe_option("MIDCPNIFTY", d, sj.board_key(d),
                                     now=1_700_001_100.0),
                  any(r["reason"] == lc.PLAN_INVALID
                      for r in _ledger() if r["status"] == lc.MISSED))[1])(
          _dec(target1=95.0, plan_state="INVALID", plan_actionable=False)))

# --- 5. WAIT is a statement, not a missing signal ----------------------------
_reset()
wait = _dec(signal=Signal.WAIT, market_signal=Signal.WAIT, stop_loss=None,
            target1=None, target2=None, target3=None)
vis.observe_option("NIFTY", wait, sj.board_key(wait), now=1_700_002_000.0)
check("a WAIT is published like any other call",
      lc.DASHBOARD_PUBLISHED in _stages(wait.global_signal_id))
check("a WAIT is not recorded as a missed BUY",
      not [r for r in _ledger() if r.get("status") == lc.MISSED])

# --- 6. execution refusals are attributed to the signal that raised them -----
_reset()
d3 = _dec()
vis.observe_option("NIFTY", d3, sj.board_key(d3), now=1_700_003_000.0)
with vis.execution_context("NIFTY", d3):
    exec_funnel.reached("SIGNAL")
    exec_funnel.blocked(stage="RISK", blocker="CAPITAL_UNAVAILABLE",
                        instrument="NIFTY", option=d3.recommended_option,
                        value=100.0, threshold=50.0, where="smoke",
                        reason="not enough capital")
rows = [r for r in _ledger() if r["global_signal_id"] == d3.global_signal_id]
check("a funnel refusal lands on the signal that caused it",
      any(r["status"] == lc.MISSED and r["reason"] == lc.CAPITAL_BLOCK
          for r in rows))
check("a stage the signal reached is recorded too",
      lc.PAPER_ELIGIBLE in _stages(d3.global_signal_id))
check("the raw blocker name is preserved beside the mapped reason",
      any((r.get("detail") or {}).get("blocker") == "CAPITAL_UNAVAILABLE"
          for r in rows))
before = len(_ledger())
exec_funnel.blocked(stage="RISK", blocker="CAPITAL_UNAVAILABLE",
                    instrument="NIFTY", option="X", value=1.0, threshold=2.0,
                    where="smoke", reason="outside any signal")
check("a refusal raised outside a signal is not attributed to one",
      len(_ledger()) == before, str(len(_ledger()) - before))
check("an unmapped blocker is flagged rather than bucketed as UNKNOWN",
      (lambda: (vis.execution_context("NIFTY", d3).__enter__(),
                exec_funnel.blocked(stage="VALIDATION", blocker="A_NEW_GATE",
                                    instrument="NIFTY", option="X", value=1.0,
                                    threshold=2.0, where="smoke", reason="new"),
                vis._ctx.__setattr__("active", None),
                any((r.get("detail") or {}).get("blocker_unmapped")
                    for r in _ledger()))[3])())

# --- 7. a fill and an exit close the chain ----------------------------------
vis.mark_filled("NIFTY", d3, 100.0, 2, now=1_700_003_100.0)
vis.mark_exited("NIFTY", d3, 120.0, "TARGET", now=1_700_003_200.0)
st3 = _stages(d3.global_signal_id)
for stage in (lc.FILLED, lc.POSITION_OPEN, lc.EXITED):
    check(f"a taken trade records {stage}", stage in st3)

# --- 8. the journal carries the same ids ------------------------------------
_reset()
now = 1_700_004_000.0
d4 = _dec()


def _quote(symbol: str, prem: float, strike: float) -> OptionQuote:
    return OptionQuote(symbol=symbol, strike=strike,
                       option_type=OptionType.CALL, premium=prem, iv=0.2,
                       delta=0.5, gamma=0.01, theta=-1.0, vega=2.0, oi=10000,
                       oi_change=100, volume=5000, bid=prem - 0.1,
                       ask=prem + 0.1)


ind = IndicatorSnapshot(atr=30.0, vwap=24000.0, rsi=55.0, adx=25.0, trend="UP")
candles = [Candle(time=int(now) - 60 * i, open=1, high=2, low=0.5, close=1.5,
                  volume=100) for i in range(30)][::-1]
status = MarketStatus.TRENDING
chain = [_quote(d4.recommended_option or "X", 100.0, 24000.0)]
vis.observe_option("NIFTY", d4, sj.board_key(d4), now=now)
sj.observe("NIFTY", d4, chain, ind, 24010.0, status, candles, now=now,
           expiry="2026-08-26", minutes_to_expiry=400)
jrows = sj.read_journal(limit=50)
check("the journal row carries the dashboard's signal id",
      bool(jrows) and jrows[-1].get("global_signal_id") == d4.global_signal_id)
check("the journal and the ledger agree on the episode",
      bool(jrows) and jrows[-1].get("episode_id") == d4.episode_id)
check("the journal row names the market and the traded leg",
      bool(jrows) and jrows[-1].get("market") == "OPTIONS"
      and jrows[-1].get("signal_vehicle") == "CE",
      str(bool(jrows) and jrows[-1].get("signal_vehicle")))
check("the coarse vehicle field existing reports filter on is unchanged",
      bool(jrows) and jrows[-1].get("vehicle") == "OPTION")

# --- 9. reconciliation: published + journalled reconciles cleanly ------------
vis.mark_user_visible("NIFTY", d4, now=now)
session = lc.session_of(now)
rep = recon.reconcile(session=session)
check("the BUY is counted as an engine BUY", rep["counts"]["engine_buys"] == 1,
      str(rep["counts"]))
check("the BUY is counted as published",
      rep["counts"]["dashboard_published"] == 1)
check("the BUY is counted as seen by a client",
      rep["counts"]["user_visible"] == 1)
check("the BUY is counted as journalled",
      rep["counts"]["journal_recorded"] == 1)
check("nothing is reported as missing publication",
      rep["missing_dashboard_publication"] == [])
check("nothing is reported as missing a journal row",
      rep["missing_journal_row"] == [])
check("a visibility record is written once, not once per poll",
      (lambda: (vis.mark_user_visible("NIFTY", d4, now=now),
                recon.reconcile(session=session)["counts"]["user_visible"] == 1)[1])())

# --- 10. a journal BUY with no publication is reported as a failure ----------
_reset()
now2 = 1_700_005_000.0
d5 = _dec(strike=24500.0, recommended_option="NIFTY25AUG2624500CE")
# Deliberately journal WITHOUT publishing: this is the fault being detected.
sj.observe("NIFTY", d5, [_quote(d5.recommended_option or "X", 100.0, 24500.0)],
           ind, 24010.0, status, candles, now=now2, expiry="2026-08-26",
           minutes_to_expiry=400)
rep2 = recon.reconcile(session=lc.session_of(now2))
check("a journalled BUY with no publication is reported",
      len(rep2["missing_dashboard_publication"]) == 1,
      str(rep2["missing_dashboard_publication"]))
check("the failure is named MISSING_DASHBOARD_PUBLICATION",
      bool(rep2["missing_dashboard_publication"])
      and rep2["missing_dashboard_publication"][0]["failure"]
      == recon.MISSING_DASHBOARD_PUBLICATION)
check("a signal absent from the ledger entirely says so",
      bool(rep2["missing_dashboard_publication"])
      and rep2["missing_dashboard_publication"][0]["in_lifecycle_ledger"] is False)

# --- 11. duplicates do not inflate the counts -------------------------------
_reset()
now3 = 1_700_006_000.0
for i in range(150):
    d = _dec()
    vis.observe_option("ICICIBANK", d, sj.board_key(d), now=now3 + i)
rep3 = recon.reconcile(session=lc.session_of(now3))
check("150 raw events are one engine BUY, not 150",
      rep3["counts"]["engine_buys"] == 1, str(rep3["counts"]["engine_buys"]))
check("the repeats are counted as repeats",
      rep3["counts"]["duplicate_raw_events"] > 0,
      str(rep3["counts"]["duplicate_raw_events"]))
check("one episode is reported", rep3["counts"]["unique_episodes"] == 1)

# --- 12. the missed-signals panel separates refused from vanished -----------
# Wall clock here: a funnel refusal is stamped by the funnel itself, so the two
# sides have to share a session for the panel to join them.
_reset()
T_NOW = time.time()
bad = _dec(target1=95.0, plan_state="STALE", plan_actionable=False)
vis.observe_option("SBIN", bad, sj.board_key(bad), now=T_NOW)
rows = recon.missed_signals(session=lc.session_of(T_NOW))
check("a refused plan appears in the missed panel", len(rows) == 1, str(len(rows)))
check("a correctly refused plan is labelled as such",
      bool(rows) and rows[0]["classification"] == "CORRECTLY_REFUSED",
      str(rows and rows[0]["classification"]))
check("the panel states the stage and the reason",
      bool(rows) and rows[0]["missed_stage"] == lc.PLAN_VALIDATED
      and rows[0]["missed_reason"] == lc.PLAN_STALE)
blocked_row = _dec()
vis.observe_option("SBIN", blocked_row, sj.board_key(blocked_row), now=T_NOW + 1)
with vis.execution_context("SBIN", blocked_row):
    exec_funnel.blocked(stage="RISK", blocker="COOLDOWN", instrument="SBIN",
                        option=blocked_row.recommended_option, value=1.0,
                        threshold=2.0, where="smoke", reason="cooldown")
rows2 = recon.missed_signals(session=lc.session_of(T_NOW), reason=lc.COOLDOWN)
check("filtering the panel by reason works", len(rows2) == 1, str(len(rows2)))
check("an execution block is not called a correct refusal",
      bool(rows2) and rows2[0]["classification"] == "STOPPED_BEFORE_USER")

# --- 13. the daily report is written as json and markdown -------------------
out = recon.write_daily(session=lc.session_of(T_NOW))
check("the daily json is written",
      os.path.exists(os.path.join(_TMP, recon.DAILY_JSON)))
check("the daily markdown is written",
      os.path.exists(os.path.join(_TMP, recon.DAILY_MD)))
check("the day's report is kept under its own name",
      any(out["session"] in p for p in out["written"]))
md = open(os.path.join(_TMP, recon.DAILY_MD), encoding="utf-8").read()
check("the report states the engine BUY count", "engine BUYs" in md)
check("the report states why signals stopped", "Why signals stopped" in md)

# --- 14. the ledger cannot influence a decision -----------------------------
src = open("app/analysis/signal_lifecycle.py", encoding="utf-8").read()
check("the ledger imports nothing from the engine or execution layers",
      "from app.engine" not in src and "from app.execution" not in src)
vsrc = open("app/analysis/signal_visibility.py", encoding="utf-8").read()
check("the visibility audit imports nothing from the engine or execution layers",
      "from app.engine" not in vsrc and "from app.execution" not in vsrc)
rsrc = open("app/analysis/signal_reconciliation.py", encoding="utf-8").read()
check("reconciliation places no orders",
      "order" not in rsrc.lower().replace("recorded", ""))
check("a lifecycle write cannot raise into a tick",
      (lambda: (lc.record(global_signal_id="x", episode="y", stage="Z",
                          value=object()), True)[1])())

print()
if _failures:
    print(f"SIGNAL LIFECYCLE SMOKE FAILED ({len(_failures)}): {_failures}")
    raise SystemExit(1)
print("SIGNAL LIFECYCLE SMOKE PASSED")
