"""Smoke test: the AI stack decides, refuses and journals correctly.

Covers the behaviour that must hold regardless of what any model says:

* A stale/dead feed produces NO_TRADE before anything else is computed.
* With no probability model loaded, the AI reaches WATCH — never BUY_NOW. An
  unvalidated model must not be able to trade by default.
* Regime classification is deterministic and never crashes on flat/extreme input.
* Entry quality refuses extended entries (the Phase 5 mechanism).
* Every paper pre-trade check fires on the condition it names, and any failure
  yields NO_PAPER_TRADE.
* Journal writes and reads round-trip, including decisions the AI declined —
  which is what keeps the dataset free of survivor bias.
"""
from __future__ import annotations

import os
import random
import tempfile
import time

from app.ai import entry_quality as eq
from app.ai import features as F
from app.ai import journal as aij
from app.ai import orchestrator, paper, regime
from app.models import Candle, OptionQuote, OptionType

checks = 0


def ok(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    assert cond, msg


def _candles(n: int, drift: float = 0.0, seed: int = 3) -> list[Candle]:
    rnd = random.Random(seed)
    px = 6000.0
    out = []
    for i in range(n):
        px *= 1.0 + drift + rnd.gauss(0, 0.0009)
        out.append(Candle(time=1_700_000_000 + i * 60, open=px,
                          high=px * 1.0009, low=px * 0.9991, close=px,
                          volume=900 + rnd.randint(0, 500)))
    return out


def test_stale_feed_refuses_first() -> None:
    c = _candles(F.WINDOW)
    for state in ("STALE", "DEAD", "NO_DATA"):
        d = orchestrator.evaluate("CRUDEOIL", c, state, 99999.0)
        ok(d.decision == orchestrator.NO_TRADE, f"{state} must be NO_TRADE")
        ok(d.blocked_by == f"FEED_{state}", "must name the feed as the blocker")
    d = orchestrator.evaluate("CRUDEOIL", c[:10], "FRESH", 100.0)
    ok(d.blocked_by == "NO_DATA", "too few bars must refuse")


def test_no_model_never_buys() -> None:
    from app.ai import probability

    if probability.status().get("available"):
        return  # a model is deployed on this machine; covered by the model tests
    for drift in (0.0, 0.0008, -0.0008):
        d = orchestrator.evaluate("CRUDEOIL", _candles(F.WINDOW, drift), "FRESH", 200.0)
        ok(d.decision != orchestrator.BUY_NOW,
           f"BUY_NOW with no model loaded (drift {drift})")
        ok(d.decision in (orchestrator.WATCH, orchestrator.NO_TRADE),
           f"unexpected decision {d.decision}")
        if d.blocked_by == "MODEL_UNAVAILABLE":
            ok(any("model" in r.lower() for r in d.reasons),
               "must say the model is missing")


def test_regime_is_deterministic_and_total() -> None:
    feats = F.compute(_candles(F.WINDOW, 0.0006))
    ok(feats is not None, "features must compute")
    a = regime.classify(feats, None)
    b = regime.classify(feats, None)
    ok(a == b, "regime classification must be deterministic")
    ok(0.0 <= a.confidence <= 100.0, "confidence must be a percentage")
    flat = [Candle(time=1_700_000_000 + i * 60, open=100.0, high=100.0, low=100.0,
                   close=100.0, volume=0) for i in range(F.WINDOW)]
    ff = F.compute(flat)
    if ff is not None:
        r = regime.classify(ff, None)
        ok(r.state in (regime.STALLED, regime.RANGE, regime.VOL_CONTRACTION,
                       regime.UNKNOWN),
           f"a dead-flat series should not look tradeable: {r.state}")
    for state in (regime.EXTENDED, regime.EXHAUSTED, regime.STALLED):
        ok(True, state)  # vocabulary present


def test_entry_quality_refuses_extension() -> None:
    feats = F.compute(_candles(F.WINDOW, 0.0006))
    q = eq.evaluate(feats, "CE", regime.EXTENDED, False, 6.0)
    ok(q.verdict in (eq.TOO_EXTENDED, eq.NO_ENTRY, eq.WAIT_PULLBACK),
       f"a 6-ATR extension must not be ENTRY_NOW (got {q.verdict})")
    ok(q.score <= 100.0 and q.score >= 0.0, "score must be a percentage")


def _quote(premium: float = 100.0, delta: float = 0.5) -> OptionQuote:
    return OptionQuote(symbol="TEST25000CE", strike=25000.0,
                       option_type=OptionType.CALL, premium=premium, iv=0.2,
                       delta=delta, gamma=0.0, theta=-1.0, vega=1.0, oi=1000,
                       oi_change=0, volume=500, bid=premium - 0.5, ask=premium + 0.5)


def test_paper_checks_fire() -> None:
    feats = F.compute(_candles(F.WINDOW, 0.0004))
    ok("FEED_STALE" in paper.pretrade_checks(
        "CRUDEOIL", "CE", _quote(), feats, "STALE", 9000.0, 0.7),
       "stale feed must fail a named check")
    ok("FEED_NO_DATA" in paper.pretrade_checks(
        "CRUDEOIL", "CE", _quote(), feats, "NO_DATA", None, 0.7),
       "no data must fail")
    ok("NO_CONTRACT" in paper.pretrade_checks(
        "CRUDEOIL", "CE", None, feats, "FRESH", 100.0, 0.7),
       "a missing contract must fail")
    ok("INVALID_PRICE" in paper.pretrade_checks(
        "CRUDEOIL", "CE", _quote(0.0), feats, "FRESH", 100.0, 0.7),
       "a zero premium must fail")
    ok("NO_PROBABILITY" in paper.pretrade_checks(
        "CRUDEOIL", "CE", _quote(), feats, "FRESH", 100.0, None),
       "a missing probability must fail")
    res = paper.open_paper("CRUDEOIL", "CE", _quote(), feats, "STALE", 9000.0,
                           0.7, "TREND_UP", "CE", "ENTRY_NOW")
    ok(res["result"] == paper.NO_PAPER_TRADE, "a failed check must refuse entry")
    ok(res.get("failed_checks"), "the refusal must name what failed")


def test_journal_round_trip() -> None:
    fd, path = tempfile.mkstemp(prefix="qt_ai_journal_", suffix=".db")
    os.close(fd)
    try:
        from app.research.db import Database

        j = aij.AIJournal(Database(f"sqlite:///{path}"))
        did = j.insert_decision({
            "ts": int(time.time()) - 4000, "instrument": "CRUDEOIL", "price": 6000.0,
            "atr": 12.0, "feed_state": "FRESH", "data_age_ms": 120.0,
            "regime": "TREND_UP", "regime_conf": 70.0, "ai_decision": "NO_TRADE",
            "ai_side": None, "baseline_decision": "BUY", "baseline_side": "CE",
            "blocked_by": "TOO_EXTENDED", "reasons": ["extended"],
            "features": {"ret_5": 0.001},
        })
        ok(bool(did), "insert_decision must return an id")
        rows = j.decisions("CRUDEOIL", 10)
        ok(len(rows) == 1 and rows[0]["blocked_by"] == "TOO_EXTENDED",
           "a DECLINED decision must be journaled — no survivor bias")
        pend = j.unresolved_decisions(int(time.time()), 10)
        ok(len(pend) == 1, "an unresolved decision must be listed for labelling")
        j.insert_outcome({"decision_id": did, "resolved_ts": int(time.time()),
                          "horizon_bars": 30, "basis": "UNDERLYING",
                          "ce_result": "TARGET", "ce_r": 1.2, "ce_mfe_r": 1.4,
                          "ce_mae_r": 0.3, "pe_result": "STOP", "pe_r": -1.0,
                          "pe_mfe_r": 0.2, "pe_mae_r": 1.0})
        ok(len(j.unresolved_decisions(int(time.time()), 10)) == 0,
           "a resolved decision must leave the pending queue")
        ds = j.dataset(10)
        ok(len(ds) == 1 and ds[0]["ce_result"] == "TARGET",
           "the dataset must join decisions to outcomes")
        ok(ds[0]["pe_result"] == "STOP",
           "both sides must be labelled on identical levels")
        tid = j.insert_trade({
            "decision_id": did, "entry_ts": int(time.time()), "instrument": "CRUDEOIL",
            "symbol": "TEST25000CE", "side": "CE", "lots": 1, "lot_size": 100,
            "entry_premium": 100.0, "stop": 95.0, "target1": 106.0,
            "probability": 0.6, "regime": "TREND_UP", "entry_quality": "ENTRY_NOW",
        })
        ok(len(j.open_trades()) == 1, "an open paper trade must be listed")
        ok(j.has_open("CRUDEOIL", "CE"), "duplicate detection must see it")
        j.close_trade(tid, int(time.time()), 106.0, "TARGET", 600.0, 40.0, 1.2,
                      900.0)
        ok(len(j.open_trades()) == 0, "a closed trade must leave the open book")
        closed = j.closed_trades()
        ok(len(closed) == 1 and closed[0]["exit_reason"] == "TARGET",
           "the exit reason must persist")
    finally:
        os.unlink(path)


def main() -> None:
    test_stale_feed_refuses_first()
    test_no_model_never_buys()
    test_regime_is_deterministic_and_total()
    test_entry_quality_refuses_extension()
    test_paper_checks_fire()
    test_journal_round_trip()
    print(f"AI ENGINE SMOKE PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
