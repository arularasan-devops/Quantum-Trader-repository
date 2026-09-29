"""Focused smoke for the Early Momentum Advisory Engine + Signal Delay Analyzer.

Verifies:
  1) No EARLY BUY during a clean, uninterrupted downtrend (falling-knife guard).
  2) EARLY BUY when a higher-low + VWAP reclaim + rising volume + bullish
     option chain + underlying confirmation all align, while still early.
  3) No EARLY BUY once price is already extended beyond VWAP (not early anymore).
  4) Signal Delay reports a meaningful missed-move % on a confirmation-style late
     entry, and flags early_would_help when an earlier price is supplied.
"""
from __future__ import annotations

from app.execution import early_momentum, signal_delay
from app.models import Candle, IndicatorSnapshot, OptionQuote, OptionType


def _c(t, o, h, low, c, v):
    return Candle(time=t, open=o, high=h, low=low, close=c, volume=v)


def _chain(spot):
    out = []
    for k in (spot - 20, spot - 10, spot, spot + 10, spot + 20):
        for ot in (OptionType.CALL, OptionType.PUT):
            out.append(OptionQuote(
                symbol=f"X{int(k)}{ot.value}", strike=float(k), option_type=ot,
                premium=25.0, iv=0.3, delta=0.5, gamma=0.01, theta=-1.0, vega=1.0,
                oi=10000, oi_change=100, volume=5000))
    return out


def downtrend():
    candles, price = [], 200.0
    for i in range(40):
        price -= 1.0
        candles.append(_c(i, price + 0.5, price + 0.6, price - 0.6, price, 1000))
    snap = IndicatorSnapshot(vwap=price + 6, atr=1.5, ema9=price - 1, ema20=price + 1,
                             oi_bias="BEARISH", oi_writing="CALL_WRITING",
                             premium_bias="BEARISH", support=price - 5, resistance=price + 12)
    return candles, snap, price


def early_up():
    """Down leg, a swing low, then a higher-low + reclaim of VWAP on rising volume."""
    candles = []
    price = 200.0
    i = 0
    # down leg to a first swing low ~170
    for _ in range(18):
        price -= 2.0
        candles.append(_c(i, price + 1, price + 1.2, price - 1.2, price, 1000))
        i += 1
    low1 = price
    # small bounce
    for _ in range(3):
        price += 1.5
        candles.append(_c(i, price - 1, price + 1, price - 1.2, price, 1100))
        i += 1
    # pullback to a HIGHER low (above low1)
    for _ in range(3):
        price -= 1.0
        candles.append(_c(i, price + 0.5, price + 0.8, price - 0.8, price, 1200))
        i += 1
    low2 = price  # higher low
    assert low2 > low1
    # push up reclaiming VWAP on rising volume
    for _ in range(6):
        price += 1.6
        candles.append(_c(i, price - 1, price + 1.2, price - 0.8, price, 2200))
        i += 1
    vwap = price - 1.0  # just reclaimed
    snap = IndicatorSnapshot(vwap=vwap, atr=1.8, ema9=price - 0.5, ema20=price - 1.5,
                             oi_bias="BULLISH", oi_writing="PUT_WRITING",
                             premium_bias="BULLISH", support=low2, resistance=price + 14)
    return candles, snap, price


def extended_up():
    candles, snap, price = early_up()
    # push far beyond VWAP so it is no longer "early"
    for _ in range(6):
        price += 3.0
        candles.append(_c(len(candles), price - 1, price + 1.5, price - 0.5, price, 2400))
    snap.resistance = price + 4
    return candles, snap, price


def main():
    dc, ds, dp = downtrend()
    d = early_momentum.detect(dc, _chain(dp), ds, dp, vix=14.0, now=int(1e9), is_mcx=True)
    print(f"[downtrend]  active={d.active} stage={d.stage_num}:{d.stage} score={d.score} "
          f"side={d.side} regime={d.regime} wnote='{d.weight_note}'")
    assert not d.active, "should NOT fire EARLY BUY in a clean downtrend"
    assert d.stage_num < 3

    ec, es, ep = early_up()
    e = early_momentum.detect(ec, _chain(ep), es, ep, vix=17.0, now=int(1e9), is_mcx=True)
    passed = [c.name for c in e.checks if c.passed]
    wts = {c.name: c.weight for c in e.checks}
    print(f"[early_up]   active={e.active} stage={e.stage_num}:{e.stage} score={e.score} "
          f"side={e.side} atr_vwap={e.atr_from_vwap} regime={e.regime} passed={passed}")
    print(f"[weights]    {wts} sum={round(sum(wts.values()),1)} note='{e.weight_note}'")
    assert e.side == OptionType.CALL
    assert e.stage_num == 3 and e.active
    # Stage-3 EARLY BUY must carry a full, self-consistent premium trade plan.
    pl = e.plan
    assert pl is not None, "Stage-3 EARLY BUY must include a trade plan"
    print(f"[plan]       entry={pl.entry_low}-{pl.entry_high} stop={pl.stop_loss} "
          f"t1={pl.target1} t2={pl.target2} t3={pl.target3} risk/lot={pl.risk_per_lot}")
    assert pl.stop_loss is not None and pl.target1 is not None
    assert pl.stop_loss < pl.entry_low, "stop must sit below the entry zone (long premium)"
    assert pl.target1 < pl.target2 < pl.target3, "targets must be increasing 1R/2R/3R"
    assert pl.target1 > pl.entry_high, "targets must be above the entry zone"
    assert len(pl.exit_conditions) >= 3 and pl.holding_time, "plan needs exit conditions + holding time"
    assert abs(sum(wts.values()) - 100.0) < 0.5, "adaptive weights must renormalise to 100"

    # Stage 4: when the frozen engine confirms a BUY on the same side.
    e4 = early_momentum.detect(ec, _chain(ep), es, ep, vix=17.0, now=int(1e9),
                               baseline_buy=True, baseline_side=OptionType.CALL)
    print(f"[stage4]     stage={e4.stage_num}:{e4.stage} active={e4.active}")
    assert e4.stage_num == 4 and not e4.active

    xc, xs, xp = extended_up()
    x = early_momentum.detect(xc, _chain(xp), xs, xp, vix=14.0, now=int(1e9))
    print(f"[extended]   active={x.active} stage={x.stage_num}:{x.stage} atr_vwap={x.atr_from_vwap} note={x.note}")
    assert not x.active, "should NOT fire EARLY BUY once extended beyond VWAP"

    # Signal delay on the early_up leg (confirmation-style late buy at current price).
    sd = signal_delay.analyze(ec, es, ep, OptionType.CALL)
    print(f"[delay]      missed={sd.missed_move_pct}% remaining={sd.remaining_move_pct}% late={sd.late}")
    assert sd.available and sd.missed_move_pct is not None

    # Premium-vs-premium: early premium 100 vs confirmation premium 120 → 20 (16.7%) cheaper.
    sd2 = signal_delay.analyze(ec, es, ep, OptionType.CALL,
                               early_entry_premium=100.0, confirmation_premium=120.0,
                               candles_between=4)
    print(f"[delay+early] conf={sd2.confirmation_entry_premium} early={sd2.early_entry_premium} "
          f"diff={sd2.premium_diff} diff%={sd2.premium_diff_pct} candles={sd2.candles_between} "
          f"would_help={sd2.early_would_help}")
    assert sd2.confirmation_entry_premium == 120.0 and sd2.early_entry_premium == 100.0
    assert sd2.premium_diff == 20.0 and abs(sd2.premium_diff_pct - 16.67) < 0.1
    assert sd2.candles_between == 4 and sd2.early_would_help
    # Units must NOT be mixed: diff% must be a sane premium %, never ~99% vs underlying.
    assert sd2.premium_diff_pct < 100.0

    # Baseline immutability: attaching the advisory must not touch frozen fields.
    from app.models import Decision, Signal
    dec = Decision(signal=Signal.WAIT, confidence=42.0, signal_strength=30.0,
                   trade_quality="C", recommended_option=None)
    before = (dec.signal, dec.confidence, dec.signal_strength, dec.recommended_option,
              dec.stop_loss, dec.target1)
    dec.early_momentum = early_momentum.detect(ec, _chain(ep), es, ep)
    dec.signal_delay = signal_delay.analyze(ec, es, ep, OptionType.CALL, None)
    after = (dec.signal, dec.confidence, dec.signal_strength, dec.recommended_option,
             dec.stop_loss, dec.target1)
    print(f"[baseline]   before==after: {before == after}")
    assert before == after, "advisory engine must NOT change the frozen decision"

    print("\nALL EARLY-MOMENTUM SMOKE CHECKS PASSED")


if __name__ == "__main__":
    main()
