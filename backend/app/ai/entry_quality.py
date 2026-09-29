"""Entry-quality engine — is NOW the right moment, on the right price?

This is the layer Phase 5 pointed at. The measured mechanism, cross-instrument
and stable across chronological thirds, was:

    entering WITH a move puts the entry at a local extreme, so the ordinary
    pullback reaches a tight stop before the target. The penalty was −0.075R
    (Crude) / −0.070R (Nifty), it was flat across horizons 15→240 bars, and it
    shrank ~60% as the stop widened (−0.094R at 0.5 ATR → −0.035R at 1.5 ATR).

That is stop geometry, not direction. Two consequences are implemented here:

* Entry **distance from the local extreme** is scored, not just direction. An
  entry a fraction of an ATR back from the extreme has materially more room to
  the same stop.
* Extension is a hard veto, not a penalty. In EXTENDED/EXHAUSTED states the
  research says following the move loses regardless of everything else.

``WAIT_PULLBACK`` returns a concrete level, so it is an actionable state rather
than a mood: the orchestrator re-evaluates each cycle and only converts it to
BUY_NOW if price actually reaches the level while the rest of the stack still
agrees. The thresholds are set from the pullback study (``phase6_pullback.py``)
and are the AI engine's own — production entry rules are untouched.
"""
from __future__ import annotations

from dataclasses import dataclass, field

ENTRY_NOW = "ENTRY_NOW"
WAIT_PULLBACK = "WAIT_PULLBACK"
TOO_EXTENDED = "TOO_EXTENDED"
NO_ENTRY = "NO_ENTRY"

# Entry sits this far or more back from the local extreme (in ATR) to count as
# "room given". Below it the entry is at the extreme and pays the geometry cost.
_GOOD_PULLBACK_ATR = 0.35
# Beyond this the pullback is no longer a pullback — the move is being reversed.
_MAX_PULLBACK_ATR = 1.5
# Extension past this is a veto (matches the regime engine's EXTENDED boundary).
_MAX_EXTENSION_ATR = 2.0
_MAX_VWAP_DIST_ATR = 2.5


@dataclass(frozen=True)
class EntryQuality:
    verdict: str = NO_ENTRY
    score: float = 0.0                    # 0-100
    pullback_depth_atr: float = 0.0
    bars_since_extreme: float = 0.0
    extension_atr: float = 0.0
    vwap_dist_atr: float = 0.0
    suggested_entry: float | None = None  # level to wait for, when WAIT_PULLBACK
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "score": round(self.score, 1),
            "pullback_depth_atr": round(self.pullback_depth_atr, 2),
            "bars_since_extreme": self.bars_since_extreme,
            "extension_atr": round(self.extension_atr, 2),
            "vwap_dist_atr": round(self.vwap_dist_atr, 2),
            "suggested_entry": self.suggested_entry,
            "reasons": list(self.reasons),
        }


def evaluate(feats: dict, side: str, regime_state: str, follow_ok: bool,
             extension_atr: float) -> EntryQuality:
    """Entry quality for taking ``side`` (CE/PE) at this bar's price."""
    if not feats:
        return EntryQuality(reasons=("no features",))

    price = float(feats.get("_price") or 0.0)
    atr = float(feats.get("_atr") or 0.0)
    depth = float(feats.get("pullback_depth_atr") or 0.0)
    since = float(feats.get("bars_since_extreme") or 0.0)
    vwap_d = float(feats.get("vwap_dist_atr") or 0.0)
    persistence = float(feats.get("persistence_10") or 0.0)
    rsi = float(feats.get("rsi") or 50.0)
    up = side.upper() == "CE"
    # Distance from VWAP measured on the side being traded.
    vwap_stretch = vwap_d if up else -vwap_d

    reasons: list[str] = []
    if not price or not atr:
        return EntryQuality(reasons=("no price/ATR",))

    # ---- hard vetoes ----
    if extension_atr >= _MAX_EXTENSION_ATR or not follow_ok:
        return EntryQuality(
            verdict=TOO_EXTENDED, score=0.0, pullback_depth_atr=depth,
            bars_since_extreme=since, extension_atr=extension_atr,
            vwap_dist_atr=vwap_d,
            reasons=(f"{extension_atr:.1f} ATR from the swing in a {regime_state} "
                     "state — Phase 5: following a move this far along loses",),
        )
    if vwap_stretch >= _MAX_VWAP_DIST_ATR:
        return EntryQuality(
            verdict=TOO_EXTENDED, score=0.0, pullback_depth_atr=depth,
            bars_since_extreme=since, extension_atr=extension_atr,
            vwap_dist_atr=vwap_d,
            reasons=(f"{vwap_stretch:.1f} ATR above VWAP on the side being bought",),
        )
    if depth > _MAX_PULLBACK_ATR:
        return EntryQuality(
            verdict=NO_ENTRY, score=0.0, pullback_depth_atr=depth,
            bars_since_extreme=since, extension_atr=extension_atr,
            vwap_dist_atr=vwap_d,
            reasons=(f"retraced {depth:.1f} ATR — this is a reversal, not a pullback",),
        )
    if (up and rsi >= 78) or (not up and rsi <= 22):
        return EntryQuality(
            verdict=WAIT_PULLBACK, score=25.0, pullback_depth_atr=depth,
            bars_since_extreme=since, extension_atr=extension_atr,
            vwap_dist_atr=vwap_d,
            suggested_entry=round(price - (1 if up else -1) * _GOOD_PULLBACK_ATR * atr, 2),
            reasons=(f"RSI {rsi:.0f} — buying the extreme of the push",),
        )

    # ---- score (0-100) ----
    score = 50.0
    if depth >= _GOOD_PULLBACK_ATR:
        score += 20.0
        reasons.append(f"{depth:.2f} ATR of room back from the extreme")
    else:
        score -= 15.0
        reasons.append(f"only {depth:.2f} ATR back from the extreme "
                       "(entry sits where the stop is nearest)")
    if persistence >= 0.6:
        score += 10.0
        reasons.append("directional persistence intact")
    elif persistence <= 0.2:
        score -= 10.0
        reasons.append("no persistence in the last 10 bars")
    if extension_atr < 1.0:
        score += 10.0
        reasons.append(f"only {extension_atr:.1f} ATR into the move")
    if abs(vwap_stretch) <= 1.0:
        score += 5.0
    if since <= 2:
        score -= 5.0
        reasons.append("the extreme was set within the last 2 bars")
    score = max(0.0, min(100.0, score))

    if depth >= _GOOD_PULLBACK_ATR:
        verdict = ENTRY_NOW
    else:
        verdict = WAIT_PULLBACK
        reasons.append("wait for price to give back some of the last leg")

    suggested = None
    if verdict == WAIT_PULLBACK:
        need = (_GOOD_PULLBACK_ATR - depth) * atr
        suggested = round(price - (1 if up else -1) * need, 2)

    return EntryQuality(
        verdict=verdict, score=score, pullback_depth_atr=depth,
        bars_since_extreme=since, extension_atr=extension_atr,
        vwap_dist_atr=vwap_d, suggested_entry=suggested, reasons=tuple(reasons),
    )
