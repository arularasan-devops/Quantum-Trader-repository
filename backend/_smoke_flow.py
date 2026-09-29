"""Smoke test for the Flow Engine (Candle-Flow). Verifies:
  * a run of decisive green candles with no open leg → BUY CE
  * ONE red candle against an open CE leg → HOLD (de-whipsaw: no flip on 1 candle)
  * TWO decisive reds against an open CE leg → SWITCH to PE
  * a small pullback that gives back points → EXIT (immediate, regardless of confirm)
  * a green continuation while riding CE → HOLD
Run: .venv/bin/python _smoke_flow.py
"""
from __future__ import annotations

from app.config import settings
from app.execution import flow
from app.models import Candle, IndicatorSnapshot, OptionQuote, OptionType

# The de-whipsaw default is 2 confirming candles; assert that so the fixtures below
# (which use 1 vs 2 opposite candles) exercise the intended boundary.
assert settings.flow_confirm_candles == 2, "expected default flow_confirm_candles=2"


def mk(o, c, t, vol=1000.0):
    hi = max(o, c) + 0.5
    lo = min(o, c) - 0.5
    return Candle(time=t, open=o, high=hi, low=lo, close=c, volume=vol)


def chain(spot: float, ce=120.0, pe=110.0):
    return [
        OptionQuote(symbol="X100CE", strike=100.0, option_type=OptionType.CALL,
                    premium=ce, iv=0.2, delta=0.5, gamma=0.01, theta=-1, vega=1,
                    oi=1000, oi_change=10, volume=500),
        OptionQuote(symbol="X100PE", strike=100.0, option_type=OptionType.PUT,
                    premium=pe, iv=0.2, delta=-0.5, gamma=0.01, theta=-1, vega=1,
                    oi=1000, oi_change=10, volume=500),
    ]


snap = IndicatorSnapshot(vwap=99.0, atr=1.0)

# rising green candles (strong bodies)
up = [mk(100 + i, 100 + i + 0.9, 1000 + i * 60) for i in range(8)]
fl = flow.detect(up, chain(107.0), snap, 107.0, None)
print(f"[green]  state={fl.state} side={fl.side} candle={fl.candle_color} proj={fl.projection} streak={fl.green_streak}")
assert fl.state == "BUY" and fl.side == OptionType.CALL, "fresh green flow should BUY CE"

# riding CE; ONE ordinary decisive RED candle (no reversal pattern, no big
# giveback) → de-whipsaw HOLDS (does not flip on a single opposite candle).
rec = {"side": "CE", "option_symbol": "X100CE", "entry_premium": 120.0,
       "peak_premium": 122.0, "open_ctime": 1000, "ts_open": 1000, "last_premium": 121.0}
one_red = up + [mk(106.8, 106.2, 1000 + 8 * 60)]  # small decisive red, does NOT engulf prior
# The strength-band exit is a SEPARATE rule that fires on this fixture (strength
# 42 < floor 70). Hold it aside so this case tests the de-whipsaw rule only,
# which is what it was written for; the strength exit has its own coverage.
_floor = settings.flow_strength_floor
settings.flow_strength_floor = 0.0
fl1r = flow.detect(one_red, chain(106.0, ce=121.0), snap, 106.0, rec)
settings.flow_strength_floor = _floor
print(f"[1 red]  state={fl1r.state} side={fl1r.side} pattern={fl1r.candle_pattern} switched={fl1r.switched} exit={fl1r.exit_now}")
assert fl1r.state == "HOLD" and fl1r.side == OptionType.CALL and not fl1r.switched, \
    "a single ordinary red candle must NOT flip (de-whipsaw) — HOLD CE"

# The two cases below are the sticky-side rule (`flow_switch_strong_only`, on by
# default): the held side flips only on a STRONG confirmed reversal candle, and a
# plain run of opposite candles leaves to cash instead of auto-flipping. These
# assertions were written before that setting existed and had the two cases the
# other way round. The rule itself is untouched.

# riding CE; a single STRONG REVERSAL candle (bearish engulfing) → flip to PE
one_engulf = up + [mk(107.9, 106.0, 1000 + 8 * 60)]  # bearish engulfing
flrev = flow.detect(one_engulf, chain(106.0, ce=118.0), snap, 106.0, rec)
print(f"[rev]    state={flrev.state} side={flrev.side} pattern={flrev.candle_pattern} switched={flrev.switched}")
assert flrev.state == "SWITCH" and flrev.side == OptionType.PUT and flrev.switched, \
    "a strong confirmed reversal is the one case that flips the held side"

# riding CE; TWO ordinary decisive REDs, no reversal pattern → out to cash on the
# same side, never a flip
down = up + [mk(106.8, 106.2, 1000 + 8 * 60), mk(106.1, 105.4, 1000 + 9 * 60)]
fl2 = flow.detect(down, chain(104.0, ce=116.0), snap, 104.0, rec)
print(f"[switch] state={fl2.state} side={fl2.side} switched={fl2.switched} exit={fl2.exit_now} reason={fl2.exit_reason}")
assert fl2.state == "EXIT" and not fl2.switched and fl2.exit_now, \
    "a plain opposite run exits to cash and does not auto-flip the side"

# riding CE, small indecisive pullback but premium gave back a lot → EXIT (giveback)
rec2 = {"side": "CE", "option_symbol": "X100CE", "entry_premium": 120.0,
        "peak_premium": 140.0, "open_ctime": 1000, "ts_open": 1000, "last_premium": 128.0}
flat = up + [mk(107.9, 107.95, 1000 + 8 * 60)]  # tiny body (indecisive)
fl3 = flow.detect(flat, chain(108.0, ce=128.0), snap, 108.0, rec2)
print(f"[give]   state={fl3.state} exit={fl3.exit_now} giveback={fl3.giveback} points={fl3.points}")
assert fl3.state == "EXIT" and fl3.exit_now, "big giveback should EXIT even on an indecisive candle"

# riding CE, green continuation → HOLD
rec3 = {"side": "CE", "option_symbol": "X100CE", "entry_premium": 120.0,
        "peak_premium": 130.0, "open_ctime": 1000, "ts_open": 1000, "last_premium": 130.0}
cont = up + [mk(108.0, 109.0, 1000 + 8 * 60)]  # decisive green
fl4 = flow.detect(cont, chain(109.0, ce=131.0), snap, 109.0, rec3)
print(f"[hold]   state={fl4.state} side={fl4.side} points={fl4.points} bars={fl4.bars_in_trade} sticky={fl4.just_entered}")
assert fl4.state == "HOLD" and fl4.side == OptionType.CALL, "green continuation should HOLD CE"
# open_ctime=1000 → many bars since → past the sticky window → plain HOLD.
assert not fl4.just_entered and "HOLD" in fl4.headline, "an old leg should be plain HOLD, not sticky BUY"

# fresh green BUY → sticky flag set on the entry candle
fl5 = flow.detect(up, chain(107.0), snap, 107.0, None)
assert fl5.state == "BUY" and fl5.just_entered and fl5.bars_in_trade == 1, "fresh BUY must set just_entered"

# HOLD within the sticky window (entered only ~2 candles ago) → still shows BUY
rec_recent = {"side": "CE", "option_symbol": "X100CE", "entry_premium": 120.0,
              "peak_premium": 121.0, "open_ctime": 1000 + 6 * 60, "ts_open": 1000, "last_premium": 121.0}
fl6 = flow.detect(cont, chain(109.0, ce=131.0), snap, 109.0, rec_recent)
print(f"[sticky] state={fl6.state} bars={fl6.bars_in_trade} sticky={fl6.just_entered} headline={fl6.headline!r}")
assert fl6.state == "HOLD" and fl6.just_entered and fl6.bars_in_trade <= 3 and "BUY" in fl6.headline, \
    "a just-entered leg should show sticky BUY within the window"

# --- the numbers the screen shows a paper trade: entry, best, and where it cuts ---
# The exit level trails the PEAK, so on a leg that has run it must sit above the
# entry premium. If it were ever reported as an entry-relative stop the screen
# would understate the risk on a fresh leg and overstate it on a runner.
from app.config import settings  # noqa: E402

give = float(settings.flow_giveback_points)
rec_run = {"side": "CE", "option_symbol": "X100CE", "entry_premium": 120.0,
           "peak_premium": 160.0, "open_ctime": 1000, "ts_open": 1000,
           "last_premium": 158.0}
fl7 = flow.detect(cont, chain(109.0, ce=158.0), snap, 109.0, rec_run)
print(f"[level]  entry={fl7.entry_premium} peak_pts={fl7.peak_points} "
      f"exit_at={fl7.exit_trigger_premium} room={fl7.points_to_exit}")
assert fl7.peak_points == 40.0, "peak_points must be peak - entry, in premium points"
assert fl7.giveback_limit_points == round(give, 1), \
    "the published allowance must be the one the exit actually uses"
assert fl7.exit_trigger_premium == round(160.0 - give, 1), \
    "the exit level must be peak - allowance, not entry - allowance"
assert fl7.exit_trigger_premium > fl7.entry_premium, \
    "on a leg that has run, the trailing exit sits ABOVE entry — it is not a stop-loss"
assert fl7.points_to_exit == round(158.0 - fl7.exit_trigger_premium, 1), \
    "room left must be live - exit level"

# Flat leg: nothing given back yet, so the level sits one allowance under entry.
rec_flat = {"side": "CE", "option_symbol": "X100CE", "entry_premium": 120.0,
            "peak_premium": 120.0, "open_ctime": 1000, "ts_open": 1000,
            "last_premium": 120.0}
fl8 = flow.detect(cont, chain(109.0, ce=120.0), snap, 109.0, rec_flat)
assert fl8.peak_points == 0.0 and fl8.exit_trigger_premium == round(120.0 - give, 1), \
    "an unmoved leg must show zero peak points and entry - allowance as the cut"

# Not in a trade: no leg, so no fabricated entry, peak or exit level.
fl9 = flow.detect(up, chain(107.0), snap, 107.0, None)
assert not fl9.in_trade and fl9.entry_premium is None, \
    "a BUY call with no leg yet has no recorded entry — only an entry hint"
assert fl9.peak_points is None and fl9.exit_trigger_premium is None \
    and fl9.points_to_exit is None, \
    "with no tracked leg these must stay None rather than invent a level"

print("\nALL FLOW SMOKE CHECKS PASSED")
