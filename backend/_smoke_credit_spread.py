"""Focused checks on the credit-spread engine: risk really is capped, the
structure follows the regime, and the intraday-only guard cannot be bypassed."""
import math
import random

from app.config import settings
from app.execution import credit_spread as cs
from app.models import Candle, MarketStatus

random.seed(7)
spot = 24000.0
px = spot
candles = []
for i in range(400):
    px *= 1.0 + random.gauss(0, 0.0006)
    candles.append(Candle(time=i * 60, open=px, high=px * 1.001,
                          low=px * 0.999, close=px, volume=1000))
spot = candles[-1].close

settings.credit_spread_enabled = True
step = 50.0
fails = []


def check(name, cond, extra=""):
    print(f"{'OK ' if cond else 'FAIL'} {name} {extra}")
    if not cond:
        fails.append(name)


# 1. never naked: every SELL leg must have a BUY hedge on the same side
sig = cs.detect(candles, spot, step, MarketStatus.TRENDING, "UP", 300)
sells = [lg for lg in sig.legs if lg.action == "SELL"]
buys = [lg for lg in sig.legs if lg.action == "BUY"]
check("defined risk: every short leg is hedged",
      len(sells) == len(buys) and len(sells) > 0,
      f"({len(sells)} short / {len(buys)} long)")

# 2. max loss must equal (width - credit) and be finite/positive
width = settings.credit_spread_width_steps * step
expected = width * len(sells) - (sig.net_credit or 0) - (
    settings.credit_spread_slippage_per_leg * len(sig.legs) * 2)
check("max loss is capped and finite",
      sig.max_loss is not None and 0 < sig.max_loss < math.inf,
      f"(max_loss={sig.max_loss}, width={width}, credit={sig.net_credit})")
check("max loss ~= width - credit",
      sig.max_loss is not None and abs(sig.max_loss - expected) < width * 0.5,
      f"({sig.max_loss} vs ~{round(expected, 1)})")

# 3. structure follows the regime
up = cs.detect(candles, spot, step, MarketStatus.TRENDING, "UP", 300)
dn = cs.detect(candles, spot, step, MarketStatus.TRENDING, "DOWN", 300)
rg = cs.detect(candles, spot, step, MarketStatus.RANGING, "SIDEWAYS", 300)
check("bullish -> BULL_PUT (sells PE)", up.structure == "BULL_PUT"
      and all(lg.option_type == "PE" for lg in up.legs), f"({up.structure})")
check("bearish -> BEAR_CALL (sells CE)", dn.structure == "BEAR_CALL"
      and all(lg.option_type == "CE" for lg in dn.legs), f"({dn.structure})")
check("ranging -> IRON_CONDOR (both sides)", rg.structure == "IRON_CONDOR"
      and {lg.option_type for lg in rg.legs} == {"PE", "CE"}, f"({rg.structure})")

# 4. short strikes are OTM on the correct side
short_pe = [lg.strike for lg in up.legs if lg.action == "SELL"]
check("bull put sells BELOW spot", all(k < spot for k in short_pe),
      f"(spot {spot:.0f}, short {short_pe})")
short_ce = [lg.strike for lg in dn.legs if lg.action == "SELL"]
check("bear call sells ABOVE spot", all(k > spot for k in short_ce),
      f"(spot {spot:.0f}, short {short_ce})")

# 5. the hedge is FURTHER out than the short leg (otherwise it isn't a hedge)
lp = [lg.strike for lg in up.legs if lg.action == "BUY"]
check("bull put hedge is further OTM", all(b < s for b, s in zip(lp, short_pe)),
      f"(long {lp} < short {short_pe})")

# 6. INTRADAY ONLY — cannot open near the close, and must say CLOSE
late = cs.detect(candles, spot, step, MarketStatus.TRENDING, "UP", 10)
check("flattens before the bell", late.state == "CLOSE", f"(state={late.state})")
tight = cs.detect(candles, spot, step, MarketStatus.TRENDING, "UP", 45)
check("refuses a late open", tight.state == "BLOCKED", f"(state={tight.state})")

# 7. hard paper lock
check("paper_only is locked on", sig.paper_only is True)
check("engine exposes no order path",
      not any(h in dir(cs) for h in ("place_order", "buy", "sell", "execute")))

# 8. win probability rises as the short delta falls (the dial, demonstrated)
probs = {}
for d in (0.30, 0.20, 0.10):
    settings.credit_spread_short_delta = d
    s = cs.detect(candles, spot, step, MarketStatus.TRENDING, "UP", 300)
    probs[d] = (s.win_probability, s.net_credit)
settings.credit_spread_short_delta = 0.20
print("\n   delta -> (win prob %, credit):", probs)
wp = [probs[d][0] for d in (0.30, 0.20, 0.10)]
cr = [probs[d][1] for d in (0.30, 0.20, 0.10)]
check("lower delta = higher win rate", wp == sorted(wp), f"({wp})")
check("lower delta = smaller credit", cr == sorted(cr, reverse=True), f"({cr})")

print("\nproposal:", sig.state, "|", sig.reason)
if fails:
    raise SystemExit(f"FAILED: {fails}")
print("\nALL CREDIT-SPREAD CHECKS PASSED")
