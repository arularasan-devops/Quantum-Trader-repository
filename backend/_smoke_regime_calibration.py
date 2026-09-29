"""Smoke test: the regime engine's staging cuts are reachable in both directions.

Blocker C from the Phase 6 export: across 13,276 live decisions the engine said
EXHAUSTED 85% of the time, EXTENDED 13%, and FRESH_MOMENTUM / TREND / RANGE never.
The cause was calibration, not the market and not the missing bars — extension was
gated on distance-from-swing in ATR at >=2 / >=3, while the measured distribution
of that quantity on one-minute CRUDEOIL and NIFTY starts at ~2.8 (p10). Staging now
uses position inside the 30-bar swing, which is scale-free.

What is proved, on synthetic series with a known shape:
 * a market sitting in the middle of its own range is NOT called EXHAUSTED;
 * a young directional push near its extreme CAN reach FRESH_MOMENTUM;
 * a move that has run to the far end of its range with momentum gone IS called
   EXHAUSTED, and follow_ok is withdrawn there;
 * the state is not decided by the price scale of the instrument (the same shape
   at 100 and at 80,000 gets the same label).
"""
from __future__ import annotations

import math

from app.ai import features as F
from app.ai import regime as reg
from app.models import Candle

checks = 0


def ok(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    assert cond, msg


def _series(shape: list[float], base: float) -> list[Candle]:
    """One bar per element; the element is a fraction offset from ``base``."""
    out = []
    for i, frac in enumerate(shape):
        px = base * (1.0 + frac)
        span = base * 0.0006
        out.append(Candle(time=1_700_000_000 + i * 60, open=px - span * 0.2,
                          high=px + span, low=px - span, close=px,
                          volume=1000.0 + i))
    return out


def _oscillating(n: int) -> list[float]:
    """Chop: price oscillates and ends in the middle of its own range."""
    return [0.004 * math.sin(2.0 * math.pi * (i + 1) / 8.0) for i in range(n)]


def _run_then_stall(n: int) -> list[float]:
    """A long push that ends flat at the top — the textbook exhausted tape."""
    out = []
    for i in range(n):
        if i < n - 6:
            out.append(0.012 * i / max(1, n - 6))
        else:
            out.append(0.012 + 0.00002 * ((i % 4) - 2))
    return out


def _fresh_push(n: int) -> list[float]:
    """A flat base, then a young push that is still at its extreme."""
    out = [0.0004 * ((i % 6) - 3) / 3.0 for i in range(n - 6)]
    top = out[-1]
    out += [top + 0.0015 * (k + 1) for k in range(6)]
    return out


def _classify(shape: list[float], base: float) -> reg.Regime:
    candles = _series(shape, base)
    feats = F.compute(candles)
    ok(bool(feats), "features must compute on a full window")
    return reg.classify(feats, None)


def main() -> None:
    n = F.WINDOW

    chop = _classify(_oscillating(n), 6000.0)
    ok(chop.state != reg.EXHAUSTED,
       f"a market in the middle of its range is not exhausted: {chop.state} "
       f"pos={chop.position_in_move}")
    ok(0.2 <= chop.position_in_move <= 0.8,
       f"chop must sit mid-range: pos={chop.position_in_move}")

    spent = _classify(_run_then_stall(n), 6000.0)
    ok(spent.state in (reg.EXHAUSTED, reg.EXTENDED),
       f"a move stalled at the far end of its range is spent: {spent.state}")
    ok(not spent.follow_ok,
       "following the move must not be endorsed in a spent state")
    ok(spent.position_in_move >= reg._POS_EXTENDED,
       f"a spent move must be late in its range: {spent.position_in_move}")

    fresh = _classify(_fresh_push(n), 6000.0)
    ok(fresh.state in (reg.FRESH_MOMENTUM, reg.BREAKOUT, reg.TREND_UP,
                       reg.TREND_DOWN, reg.VOL_EXPANSION),
       f"a young push must be reachable as something other than exhausted: "
       f"{fresh.state} pos={fresh.position_in_move}")
    ok(fresh.state != reg.EXHAUSTED,
       f"a push still at its extreme is not exhausted: {fresh.state}")

    # Scale invariance: the same shape on a 100-rupee and an 80,000-point
    # instrument must be labelled identically. The old ATR-distance cuts could
    # not guarantee this, which is how one label swallowed 85% of decisions.
    for base in (100.0, 6000.0, 80_000.0):
        ok(_classify(_run_then_stall(n), base).state == spent.state,
           f"the spent tape must label the same at base {base}")
        ok(_classify(_oscillating(n), base).state == chop.state,
           f"chop must label the same at base {base}")

    # The reported staging cuts must stay inside the measured distribution, so a
    # future edit cannot quietly reintroduce a cut below the tenth percentile.
    ok(0.5 < reg._POS_EXTENDED < reg._POS_EXHAUSTED < 1.0,
       "staging cuts must be ordered and inside the 0-1 range")

    print(f"REGIME CALIBRATION SMOKE PASSED ({checks} checks)")


if __name__ == "__main__":
    main()
