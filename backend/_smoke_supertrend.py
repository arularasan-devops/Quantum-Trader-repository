"""Focused check that the Supertrend behaves like the TradingView indicator:
it flips only on a close through the trailing band, it holds through pullbacks
inside a trend, and the flip lands at the START of the expansion (not the peak).
"""
import numpy as np

from app.analysis.indicators import supertrend_flip, supertrend_series


def synth(moves: list[float], *, wick: float = 0.4):
    """Build OHLC arrays from a list of per-candle closes-changes."""
    close = 100.0
    o, h, low, c = [], [], [], []
    for m in moves:
        prev = close
        close = prev + m
        o.append(prev)
        c.append(close)
        h.append(max(prev, close) + wick)
        low.append(min(prev, close) - wick)
    return (np.array(o), np.array(h), np.array(low), np.array(c))


# 1) Down-move into a base, then an expansion up: the flip must appear near the
#    START of the up-leg, well before the top.
down = [-1.0] * 25
base = [0.2, -0.2, 0.15, -0.15, 0.1, -0.1] * 3
up = [1.4] * 25
_o, h, low, c = synth(down + base + up)
direction, line = supertrend_series(h, low, c, 10, 3.0)

flip_idx = None
start_of_up = len(down) + len(base)
for i in range(start_of_up, len(c)):
    if direction[i] == 1 and direction[i - 1] == -1:
        flip_idx = i
        break
assert flip_idx is not None, "no up-flip detected on a clear expansion"
peak_idx = int(np.argmax(c))
bars_after_start = flip_idx - start_of_up
entry_px = c[flip_idx]
peak_px = c[peak_idx]
captured = (peak_px - entry_px) / (peak_px - c[start_of_up - 1]) * 100
print(
    f"OK  flips {bars_after_start} candles into the up-leg "
    f"(peak is {peak_idx - start_of_up} candles in) — entry {entry_px:.1f}, peak {peak_px:.1f}"
)
print(f"OK  captures {captured:.1f}% of the move that follows the base")
assert flip_idx < peak_idx, "flip must precede the peak, not follow it"
assert captured > 60, f"entry is too late — only {captured:.1f}% of the move left"

# 2) It must NOT flip on an ordinary pullback inside the trend.
trend_with_dip = [1.2] * 20 + [-0.9, -0.8, -0.7] + [1.2] * 15
_o2, h2, l2, c2 = synth(trend_with_dip)
d2, _ = supertrend_series(h2, l2, c2, 10, 3.0)
flips = int((np.diff(d2[12:]) != 0).sum())
print(f"OK  holds through a 3-candle pullback inside the trend (flips={flips})")
assert flips == 0, "band flipped on noise — multiplier/ratchet is wrong"

# 3) A real trend break DOES flip it.
broken = [1.2] * 25 + [-2.5] * 12
_o3, h3, l3, c3 = synth(broken)
d3, _ = supertrend_series(h3, l3, c3, 10, 3.0)
assert d3[-1] == -1, "failed to flip down on a genuine trend break"
print("OK  flips down on a genuine trend break")

# 4) supertrend_flip only reports a FRESH flip (no late chasing).
_o4, h4, l4, c4 = synth(down + base + up)
fresh = supertrend_flip(h4[: flip_idx + 1], l4[: flip_idx + 1], c4[: flip_idx + 1])
stale = supertrend_flip(h4, l4, c4)
print(f"OK  fresh flip reported as {fresh}; 20+ candles later reports {stale}")
assert fresh == "FLIP_UP", fresh
assert stale == "NONE", "a stale flip must not be re-reported as an entry"

# 5) Not enough data must be safe, never a fabricated signal.
assert supertrend_flip(h4[:5], l4[:5], c4[:5]) == "NONE"
print("OK  insufficient history returns NONE (no fabricated signal)")
print("ALL SUPERTREND CHECKS PASSED")
