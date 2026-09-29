"""Smoke test: the full AI → paper path can actually execute, end to end.

This exists because of an honest problem with the trained artefact: its calibrated
probability never exceeds ~0.47, so with the real model loaded the AI correctly
never reaches BUY_NOW and the execution path would go untested. Rather than deploy
a model that clears the bar by construction, the path is proved here with a
SYNTHETIC artefact written to a temp file and pointed at by settings for the
duration of the test — never promoted, never left behind.

What is proved: BUY_NOW is reachable, a paper position opens with premium
stop/targets from the underlying ATR, spread and slippage are charged, live marks
update MFE/MAE, a target closes with reason TARGET and a positive R, and every
one of it happens without touching a broker.
"""
from __future__ import annotations

import json
import os
import random
import tempfile
import time

from app.ai import features as F
from app.ai import journal as aij
from app.ai import orchestrator, paper, probability
from app.config import settings
from app.models import Candle, OptionQuote, OptionType
from app.research.db import Database

checks = 0


def ok(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    assert cond, msg


def _candles(n: int, drift: float, seed: int = 11, push_bars: int = 25,
             pullback_bars: int = 8) -> list[Candle]:
    """A flat base, a recent push, then a retrace into the decision bar.

    The shape matters. A series that trends for its whole length is EXTENDED far
    from its swing by the end, and the entry-quality engine correctly refuses it —
    which is the behaviour Phase 5 asked for. A tradeable bar is a FRESH move that
    has pulled back, so that is what this builds.
    """
    rnd = random.Random(seed)
    px = 6000.0
    out = []
    now = int(time.time()) - n * 60
    for i in range(n):
        from_end = n - i
        if from_end <= pullback_bars:
            step = -drift * 1.2
        elif from_end <= push_bars:
            step = drift
        else:
            step = 0.0
        px *= 1.0 + step + rnd.gauss(0, 0.0008)
        out.append(Candle(time=now + i * 60, open=px, high=px * 1.001,
                          low=px * 0.999, close=px, volume=1000 + rnd.randint(0, 400)))
    return out


def _synthetic_artefact(path: str, p: float) -> None:
    """An artefact that returns a constant calibrated probability ``p``.

    Zero coefficients, so the raw score is the intercept and the calibration table
    maps it to exactly ``p``. It carries no information; it exists only to unblock
    the plumbing below it.
    """
    n = len(F.FEATURES)
    coef = [0.0] * n
    # Only the side flag matters, so CE and PE get different probabilities and the
    # direction engine has a real edge to act on.
    coef[F.FEATURES.index("side_is_pe")] = -1.0
    with open(path, "w") as fh:
        json.dump({
            "version": "SYNTHETIC-TEST-ONLY",
            "features": list(F.FEATURES),
            "coef": coef, "intercept": 1.0,
            "mean": [0.0] * n, "scale": [1.0] * n,
            "calibration": [{"predicted": 0.5, "actual": 0.5},
                            {"predicted": 0.7311, "actual": p}],
            "metrics": {"oos": {"auc": 0.5}},
            "limitations": ["SYNTHETIC — carries no information, tests only."],
        }, fh)


def _quote(premium: float, delta: float = 0.5) -> OptionQuote:
    return OptionQuote(symbol="SMOKE6000CE", strike=6000.0,
                       option_type=OptionType.CALL, premium=premium, iv=0.25,
                       delta=delta, gamma=0.0, theta=-1.0, vega=1.0, oi=5000,
                       oi_change=0, volume=2500, bid=premium - 0.4, ask=premium + 0.4)


class _FakeProvider:
    """Serves one option quote at a settable premium. No order methods exist."""

    def __init__(self) -> None:
        self.premium = 100.0

    def option_chain(self) -> list[OptionQuote]:
        return [_quote(self.premium)]

    def futures_price(self) -> float:
        return 6000.0


class _FakeState:
    def __init__(self) -> None:
        self.provider = _FakeProvider()
        self.instrument = "CRUDEOIL"


def main() -> None:
    fd, model_path = tempfile.mkstemp(prefix="qt_ai_model_", suffix=".json")
    os.close(fd)
    fd, db_path = tempfile.mkstemp(prefix="qt_ai_flow_", suffix=".db")
    os.close(fd)
    saved_model_path = settings.ai_model_path
    saved_paper_enabled = settings.ai_paper_enabled
    saved_ignore_hours = settings.ignore_market_hours
    saved_min_prob = settings.ai_min_probability
    saved_min_eq = settings.ai_min_entry_quality
    saved_min_regime = settings.ai_min_regime_confidence
    saved_min_edge = settings.ai_min_direction_edge
    previous_journal = None
    try:
        _synthetic_artefact(model_path, 0.72)  # CE ≈ 0.72, PE ≈ 0.50
        settings.ai_model_path = model_path
        settings.ai_paper_enabled = True
        settings.ignore_market_hours = True
        settings.ai_min_probability = 0.55
        settings.ai_min_entry_quality = 20.0
        settings.ai_min_regime_confidence = 20.0
        settings.ai_min_direction_edge = 0.0
        probability.reset()
        previous_journal = aij.use_journal(aij.AIJournal(Database(f"sqlite:///{db_path}")))

        st = probability.status()
        ok(st.get("available") is True, f"synthetic artefact must load: {st}")

        # 1. The stack reaches BUY_NOW on a clean trending series.
        decision = None
        # Most seeds are refused as extended — that is the engine working. The
        # loop looks for the shape it should accept.
        for seed in range(1, 60):
            d = orchestrator.evaluate("CRUDEOIL", _candles(F.WINDOW, 0.0003, seed),
                                      "FRESH", 150.0)
            if d.decision == orchestrator.BUY_NOW:
                decision = d
                break
        ok(decision is not None,
           "BUY_NOW must be reachable when the model clears the threshold")
        ok(decision.side in ("CE", "PE"), "a BUY_NOW must name a side")
        ok(decision.probability is not None and decision.probability >= 0.55,
           "a BUY_NOW must carry the probability that justified it")
        ok(decision.as_dict()["real_money_execution"] == "DISABLED",
           "every decision must state that real execution is off")

        # 2. It opens a paper position, with costs charged on the fill.
        state = _FakeState()
        res = paper.open_paper("CRUDEOIL", decision.side or "CE",
                               state.provider.option_chain()[0], decision.features,
                               "FRESH", 150.0, decision.probability, "TREND_UP",
                               decision.side or "CE", "ENTRY_NOW")
        ok(res["result"] == paper.PAPER_ENTERED, f"paper entry refused: {res}")
        ok(res["entry"] > 100.0,
           "the fill must cross the spread and pay slippage, not fill at mid")
        trade = aij.journal().open_trades()[0]
        ok(float(trade["stop"]) < float(trade["entry_premium"]) < float(trade["target1"]),
           "stop/target geometry must bracket the entry")
        ok(int(trade["lots"]) >= 1 and int(trade["lot_size"]) >= 1,
           "size must be at least one lot")

        # 3. A live mark updates the position; MFE/MAE track it.
        state.provider.premium = float(trade["entry_premium"]) + 1.0
        events = paper.manage(lambda _inst: state)
        ok(events == [], "a small favourable move must not close the position")
        t2 = aij.journal().open_trades()[0]
        ok(float(t2["mfe"] or 0) > 0, "MFE must record the favourable excursion")
        ok(float(t2["unrealized_pnl"] or 0) != 0, "unrealised P&L must update")

        # 4. Target closes the trade with the right reason and a positive R.
        # Exits are marked at the bid less slippage, so the quote must clear the
        # target by more than the round-trip cost of crossing.
        state.provider.premium = float(trade["target1"]) * 1.05 + 2.0
        events = paper.manage(lambda _inst: state)
        ok(len(events) == 1 and events[0]["reason"] == paper.TARGET,
           f"a target touch must close with reason TARGET: {events}")
        ok(events[0]["r_multiple"] > 0, "a target exit must be positive R")
        ok(not aij.journal().open_trades(), "the book must be empty after the exit")
        closed = aij.journal().closed_trades()
        ok(len(closed) == 1 and float(closed[0]["costs"]) > 0,
           "brokerage must be charged to the paper trade")

        # 5. Duplicate protection and the P&L summary.
        pnl = paper.pnl_summary()
        ok(pnl["closed_trades"] == 1 and pnl["paper_mode"] is True,
           "the paper summary must report the closed trade in paper mode")
        again = paper.open_paper("CRUDEOIL", decision.side or "CE",
                                 state.provider.option_chain()[0], decision.features,
                                 "FRESH", 150.0, decision.probability, "TREND_UP",
                                 decision.side or "CE", "ENTRY_NOW")
        if again["result"] == paper.PAPER_ENTERED:
            dup = paper.open_paper("CRUDEOIL", decision.side or "CE",
                                   state.provider.option_chain()[0],
                                   decision.features, "FRESH", 150.0,
                                   decision.probability, "TREND_UP",
                                   decision.side or "CE", "ENTRY_NOW")
            ok(dup["result"] == paper.NO_PAPER_TRADE
               and "DUPLICATE_POSITION" in dup["failed_checks"],
               f"a duplicate position must be refused: {dup}")
        print(f"AI PAPER FLOW SMOKE PASSED ({checks} checks)")
    finally:
        settings.ai_model_path = saved_model_path
        settings.ai_paper_enabled = saved_paper_enabled
        settings.ignore_market_hours = saved_ignore_hours
        settings.ai_min_probability = saved_min_prob
        settings.ai_min_entry_quality = saved_min_eq
        settings.ai_min_regime_confidence = saved_min_regime
        settings.ai_min_direction_edge = saved_min_edge
        probability.reset()
        aij.use_journal(previous_journal)
        for path in (model_path, db_path):
            if os.path.exists(path):
                os.unlink(path)


if __name__ == "__main__":
    main()
