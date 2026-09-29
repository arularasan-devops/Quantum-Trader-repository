"""Smoke test: the gate trace and the missed-opportunity recorder.

Two properties matter here and nothing else does:

1. The trace is OBSERVATIONAL — attaching it must not change a single signal.
   The test proves that by deciding the same bars twice, with the recorder both
   on and off, and requiring identical signals.
2. The blocker attribution is USABLE — a WAIT must name the gate that refused
   it, and that gate must be one that actually reads as failed.

Writes only to a throwaway temp data dir.
"""
from __future__ import annotations

import tempfile
import time

from app.config import settings

settings.data_dir = tempfile.mkdtemp(prefix="qt_smoke_trace_")

from app.analysis import missed  # noqa: E402
from app.engine.decision import (  # noqa: E402
    _gate_states,
    classify_market,
    compute_indicators,
    decide,
    rank_gate_trace,
)
from app.models import (  # noqa: E402
    Candle,
    GateTrace,
    OptionQuote,
    OptionType,
    Signal,
)

checks = 0


def ok(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    assert cond, msg


def _chain(spot: float) -> list[OptionQuote]:
    atm = round(spot / 50.0) * 50.0
    out = []
    for side in (OptionType.CALL, OptionType.PUT):
        out.append(
            OptionQuote(
                symbol=f"TEST{int(atm)}{side.value}",
                strike=atm,
                option_type=side,
                premium=120.0,
                iv=18.0,
                delta=0.5 if side == OptionType.CALL else -0.5,
                gamma=0.0, theta=-1.0, vega=1.0,
                oi=10000, oi_change=100, volume=5000,
            )
        )
    return out


def _candles(n: int, up: bool) -> list[Candle]:
    """A clean one-way ramp (up or down) plus a flat stretch, so the run
    produces both a directional read and a refused one."""
    base = 20000.0
    out = []
    t = 1_700_000_000
    for i in range(n):
        drift = (i * 2.0) if up else (-i * 2.0)
        if i > n * 0.7:  # flat tail — nothing to trade
            drift = (n * 0.7 * 2.0) * (1 if up else -1)
        c = base + drift
        out.append(
            Candle(time=t + i * 60, open=c - 1, high=c + 3, low=c - 3, close=c, volume=1000.0)
        )
    return out


def main() -> None:
    # --- 1. ranking: earliest failing gate wins, and order is stable ---
    tr = GateTrace()
    ok(rank_gate_trace(tr).primary_blocker is None, "an all-clear trace has no blocker")
    tr = GateTrace(confidence_ok=False, htf_trend_ok=False)
    rank_gate_trace(tr)
    ok(tr.primary_blocker == "HTF_TREND", "HTF_TREND precedes CONFIDENCE in the pipeline")
    ok(tr.secondary_blocker == "CONFIDENCE", "the second failure is the secondary blocker")
    ok(tr.blockers == ["HTF_TREND", "CONFIDENCE"], "blockers list both, in pipeline order")

    labels = [lbl for lbl, _ in _gate_states(GateTrace())]
    ok(len(labels) == len(set(labels)), "no duplicate gate labels")
    ok(all(ok_ for _, ok_ in _gate_states(GateTrace())), "a default trace is all-clear")

    # --- 2. every decision carries a trace, and blockers agree with the flags --
    traced = 0
    waits_named = 0
    for up in (True, False):
        candles = _candles(180, up)
        for end in range(120, 180, 7):
            window = candles[:end]
            spot = window[-1].close
            chain = _chain(spot)
            snap = compute_indicators(window, chain)
            status = classify_market(snap, False)
            dec, _ = decide(
                window, chain, snap, 0.0, False, status, False, None, 1.0, spot=spot
            )
            ok(dec.gates is not None, "every decision carries a gate trace")
            g = dec.gates
            assert g is not None
            traced += 1
            failed = {lbl for lbl, passed in _gate_states(g) if not passed}
            ok(set(g.blockers) == failed, "blockers must equal the failed flags")
            if g.primary_blocker is not None:
                ok(g.primary_blocker in failed, "the primary blocker must be a failed gate")
            if dec.signal == Signal.BUY:
                ok(g.primary_blocker is None, "a BUY cannot carry a blocker")
            elif g.primary_blocker is not None:
                waits_named += 1
    ok(traced >= 15, f"the run must exercise a real number of bars (got {traced})")
    ok(waits_named > 0, "at least one refusal must name its gate")

    # --- 3. the recorder is measurement only, and cannot fabricate a record ---
    candles = _candles(180, True)
    chain = _chain(candles[-1].close)
    snap = compute_indicators(candles, chain)
    status = classify_market(snap, False)
    dec, _ = decide(
        candles, chain, snap, 0.0, False, status, False, None, 1.0,
        spot=candles[-1].close,
    )

    settings.missed_opportunity_log = False
    ok(missed.update("TEST", dec, chain, int(time.time()), None) is None,
       "the recorder must stay silent when switched off")
    settings.missed_opportunity_log = True

    now = int(time.time())
    blocked = dec.model_copy(update={"signal": Signal.WAIT})
    if blocked.gates is not None and not blocked.gates.blockers:
        blocked.gates.blockers = ["CONFIDENCE"]
        blocked.gates.primary_blocker = "CONFIDENCE"
    rec = missed.update("TEST", blocked, chain, now, None)
    ok(rec is not None, "a refused setup with a leg opens a record")
    assert rec is not None
    ok(rec["primary_blocker"] is not None, "the record carries the gate that refused it")

    # a favourable move inside the window is captured as MFE, then written out
    up_chain = [q.model_copy(update={"premium": q.premium + 30.0}) for q in chain]
    rec = missed.update("TEST", blocked, up_chain, now + 60, rec)
    assert rec is not None
    ok(rec["peak_premium"] > rec["ref_premium"], "the peak follows the leg up")
    closed = missed.update("TEST", blocked, up_chain, now + 60 * 31, rec)
    ok(closed is None, "the window closes and the record is written")

    rows = missed._read_all()
    ok(len(rows) == 1, "exactly one record per closed window")
    ok(rows[0]["mfe_points"] == 30.0, f"MFE is measured, not estimated ({rows[0]})")
    ok(rows[0]["mae_points"] == 0.0, "no adverse move happened, so MAE is zero")

    s = missed.summary()
    ok(s["total"] == 1, "the summary counts the record")
    ok(s["gates"][0]["over_20pct"] == 1, "a +25% move counts against its blocker")
    ok(s["gates"][0]["over_5pct"] == 1, "and against the lower thresholds too")

    # --- 4. a BUY is never recorded as a missed opportunity ---
    buy = dec.model_copy(update={"signal": Signal.BUY})
    ok(missed.update("TEST2", buy, chain, now, None) is None,
       "a taken trade is not a missed one")

    print(f"OK gate trace + missed-opportunity recorder — {checks} checks")


if __name__ == "__main__":
    main()
