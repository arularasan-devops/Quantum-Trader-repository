"""Regime engine — what state is this market in, and how sure are we?

Deterministic and explainable on purpose. A learned regime label would need a
target to learn against, and Phase 5 showed the regime→outcome mapping is not
stable enough out-of-sample to define one honestly (cell rankings flipped between
chronological thirds). So this engine measures the state and reports a confidence
built from how many independent measurements agree — it does NOT claim the state
predicts the outcome. The one regime finding that did replicate on both
instruments and both halves of the sample is carried as an explicit flag:

    following a move pays only when the move is FRESH; EXTENDED and EXHAUSTED
    states punish trading with the move.

That flag is what the orchestrator consumes. Nothing here reads an option chain
(no real chain data exists — Phase 5, Step 1).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# States (superset of the Phase 4 scanner states, which stay as-is).
TREND_UP = "TREND_UP"
TREND_DOWN = "TREND_DOWN"
RANGE = "RANGE"
BREAKOUT = "BREAKOUT"
MEAN_REVERSION = "MEAN_REVERSION"
VOL_EXPANSION = "VOLATILITY_EXPANSION"
VOL_CONTRACTION = "VOLATILITY_CONTRACTION"
FRESH_MOMENTUM = "FRESH_MOMENTUM"
EXTENDED = "EXTENDED"
EXHAUSTED = "EXHAUSTED"
STALLED = "STALLED"
UNKNOWN = "UNKNOWN"

# Phase 5: states where trading WITH the move was significantly worse than
# trading against it on both instruments, in both halves of the sample.
_FOLLOW_HOSTILE = (EXTENDED, EXHAUSTED, STALLED)

# Staging cuts, measured (phase7_regime_diag.py) rather than assumed.
#
# The live engine reported 85% EXHAUSTED, 13% EXTENDED and ZERO FRESH_MOMENTUM
# across 13,276 decisions, which is not a market fact — it was a calibration bug.
# Extension was gated on distance-from-swing in ATR units at >=2 / >=3, but the
# measured distribution of that quantity on one-minute CRUDEOIL and NIFTY is
# p10 2.8-3.0, p50 ~5.0, p90 9-16: the thresholds sat BELOW the tenth percentile,
# so nearly every bar was "exhausted", and FRESH_MOMENTUM (which additionally
# required extension < 2) was unreachable. The bug survives gap-free data — on
# contiguous windows only, the label was still 87% EXHAUSTED — so it is the
# metric, not the missing bars.
#
# Position inside the 30-bar swing is scale-free instead (measured p50 ~0.5,
# p75 ~0.76, p90 ~0.94 on both instruments), so these cuts describe how OFTEN a
# state is claimed, not how often it wins. Nothing here asserts predictiveness.
_POS_EXTENDED = 0.75         # measured p75 of position-in-move
_POS_EXHAUSTED = 0.90        # measured p90
# persistence_10 is |net direction| over 10 bars: measured p75 0.4, p90 0.4,
# p99 0.6-0.8. The old >=0.6 requirement selected the top ~1% of bars.
_PERSIST_FRESH = 0.4
_PERSIST_GONE = 0.2
# "Still making new extremes": measured p25 of bars_since_extreme is 2 on both
# instruments, so this selects roughly the freshest quarter of bars.
_FRESH_BARS = 2.0


@dataclass(frozen=True)
class Regime:
    state: str = UNKNOWN
    confidence: float = 0.0          # 0-100, agreement between measurements
    direction_bias: str = "FLAT"     # UP | DOWN | FLAT — the move already in place
    trend_strength: float = 0.0      # ADX
    volatility_state: str = UNKNOWN
    extension_atr: float = 0.0       # distance from the 30-bar swing, in ATR
    position_in_move: float = 0.5    # 0-1 through the 30-bar swing, on the moving side
    pullback_depth_atr: float = 0.0
    bars_since_extreme: float = 0.0
    follow_ok: bool = False          # Phase 5 flag: is trading WITH the move sane here?
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "confidence": round(self.confidence, 1),
            "direction_bias": self.direction_bias,
            "trend_strength": round(self.trend_strength, 1),
            "volatility_state": self.volatility_state,
            "extension_atr": round(self.extension_atr, 2),
            "position_in_move": round(self.position_in_move, 3),
            "pullback_depth_atr": round(self.pullback_depth_atr, 2),
            "bars_since_extreme": self.bars_since_extreme,
            "follow_ok": self.follow_ok,
            "reasons": list(self.reasons),
        }


def _vol_state(atr_expansion: float, rvol_vs_base: float) -> str:
    if atr_expansion >= 1.25 or rvol_vs_base >= 1.4:
        return VOL_EXPANSION
    if atr_expansion <= 0.8 and rvol_vs_base <= 0.8:
        return VOL_CONTRACTION
    return "VOLATILITY_NORMAL"


def classify(feats: dict, scan_state: str | None = None) -> Regime:
    """Regime for the bar the features describe. ``scan_state`` is the Phase 4
    scanner classification when available; it is used as one vote, not as truth."""
    if not feats:
        return Regime(reasons=("no features",))

    adx = float(feats.get("adx") or 0.0)
    ema_spread = float(feats.get("ema_spread_atr") or 0.0)
    ema50 = float(feats.get("ema50_dist_atr") or 0.0)
    st = float(feats.get("supertrend_dir") or 0.0)
    persistence = float(feats.get("persistence_10") or 0.0)
    rsi = float(feats.get("rsi") or 50.0)
    brk = float(feats.get("breakout_atr") or 0.0)
    brkd = float(feats.get("breakdown_atr") or 0.0)
    ext_lo = float(feats.get("ext_from_swing_lo_atr") or 0.0)
    ext_hi = float(feats.get("ext_from_swing_hi_atr") or 0.0)
    depth = float(feats.get("pullback_depth_atr") or 0.0)
    since = float(feats.get("bars_since_extreme") or 0.0)
    ret5 = float(feats.get("ret_5") or 0.0)
    ret30 = float(feats.get("ret_30") or 0.0)
    rvol = float(feats.get("rvol_vs_base") or 1.0)
    atr_exp = float(feats.get("atr_expansion") or 1.0)

    up_votes = sum((ema_spread > 0.05, ema50 > 0.1, st > 0, ret30 > 0, rsi > 52))
    dn_votes = sum((ema_spread < -0.05, ema50 < -0.1, st < 0, ret30 < 0, rsi < 48))
    if up_votes >= 4:
        bias, agreement = "UP", up_votes
    elif dn_votes >= 4:
        bias, agreement = "DOWN", dn_votes
    elif up_votes > dn_votes:
        bias, agreement = "UP", up_votes
    elif dn_votes > up_votes:
        bias, agreement = "DOWN", dn_votes
    else:
        bias, agreement = "FLAT", 0

    vol = _vol_state(atr_exp, rvol)
    # Extension = how far price has run from the swing it came out of, on the
    # side it is running. This is the quantity Phase 5 found matters.
    extension = ext_lo if bias == "UP" else ext_hi if bias == "DOWN" else max(ext_lo, ext_hi)
    # How far through its own 30-bar swing the move has travelled, on the side it
    # is travelling: 0 = just left the extreme it came from, 1 = at the far end.
    # This, not distance-in-ATR, is what the staging cuts are calibrated on.
    raw_pos = feats.get("_range_pos")
    pos = 0.5 if raw_pos is None else float(raw_pos)
    if bias == "DOWN":
        pos = 1.0 - pos
    elif bias == "FLAT":
        pos = max(pos, 1.0 - pos)

    reasons: list[str] = []
    state = UNKNOWN

    # Ordered by how strongly the evidence identifies the state. Extension and
    # exhaustion come FIRST: Phase 5's only cross-instrument result is that
    # getting these two wrong is what costs money.
    if abs(ret5) < 0.02 and persistence <= _PERSIST_GONE and vol == VOL_CONTRACTION:
        state = STALLED
        reasons.append("no movement, contracted volatility")
    elif persistence >= _PERSIST_FRESH and since <= _FRESH_BARS:
        # Late in the range AND still making new extremes with momentum intact is
        # a fresh push, not an exhausted one — a breakout out of a tight base is
        # at 100% of its own range by construction, so position alone would
        # mislabel exactly the state Phase 5 says is the only one worth following.
        state = FRESH_MOMENTUM
        reasons.append("directional push still making new extremes")
    elif pos >= _POS_EXHAUSTED and persistence <= _PERSIST_GONE:
        state = EXHAUSTED
        reasons.append(f"{pos * 100:.0f}% through its 30-bar swing, momentum gone")
    elif pos >= _POS_EXTENDED:
        state = EXTENDED
        reasons.append(f"{pos * 100:.0f}% through the swing it came from")
    elif (brk > 0.15 or brkd > 0.15) and rvol >= 1.1:
        state = BREAKOUT
        reasons.append("clearing the 20-bar range on above-baseline volatility")
    elif adx >= 22 and bias == "UP":
        state = TREND_UP
        reasons.append(f"ADX {adx:.0f} with EMA structure up")
    elif adx >= 22 and bias == "DOWN":
        state = TREND_DOWN
        reasons.append(f"ADX {adx:.0f} with EMA structure down")
    elif (rsi >= 70 or rsi <= 30) and extension >= 1.2:
        state = MEAN_REVERSION
        reasons.append(f"RSI {rsi:.0f} at the edge of a stretched move")
    elif vol == VOL_EXPANSION:
        state = VOL_EXPANSION
        reasons.append("range/ATR expanding without a clear direction")
    elif vol == VOL_CONTRACTION:
        state = VOL_CONTRACTION
        reasons.append("range/ATR compressing")
    else:
        state = RANGE
        reasons.append("no trend, no expansion")

    # Confidence: agreement of the directional votes, the strength of the trend
    # measurement and whether the scanner independently saw the same thing. It is
    # a measure of how well-identified the STATE is — not of how likely a trade is
    # to win. Those are different things and conflating them is how a dashboard
    # starts lying.
    conf = 25.0 + 10.0 * agreement                     # 25-75
    conf += min(15.0, max(0.0, (adx - 15.0)))          # trend clarity
    if scan_state and scan_state == state:
        conf += 10.0
        reasons.append("scanner agrees")
    elif scan_state and scan_state not in (UNKNOWN, "NO_DATA"):
        conf -= 5.0
        reasons.append(f"scanner says {scan_state}")
    if state in (STALLED, UNKNOWN):
        conf = min(conf, 40.0)
    conf = max(0.0, min(100.0, conf))

    follow_ok = state not in _FOLLOW_HOSTILE
    if not follow_ok:
        reasons.append("Phase 5: trading WITH the move loses in this state")

    return Regime(
        state=state, confidence=conf, direction_bias=bias, trend_strength=adx,
        volatility_state=vol, extension_atr=extension, position_in_move=pos,
        pullback_depth_atr=depth,
        bars_since_extreme=since, follow_ok=follow_ok, reasons=tuple(reasons),
    )
