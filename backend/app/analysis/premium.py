"""Level 2 — Option Premium Behaviour analysis.

The option premium is read *independently* of the futures. A premium can move
very differently from the underlying (IV crush, decay, a violent institutional
dump, a squeeze). This module classifies how a premium series is *behaving* so
the decision engine can tell "natural" premium action from "abnormal".

Classifications:
  EXPLOSION      — fast accelerating rise (squeeze / aggressive buying)
  MOMENTUM_UP    — steady rise
  BREAKOUT       — closed above the recent premium range
  RECOVERY       — was falling, now turning back up (potential bounce)
  CONSOLIDATION  — tight range, no edge
  DECAY          — slow grind lower (theta / mild unwinding)
  MOMENTUM_DOWN  — steady fall
  BREAKDOWN      — closed below the recent premium range
  COLLAPSE       — fast accelerating drop (dump / liquidity vacuum)

Each result carries a self-referential direction: +1 = premium rising,
-1 = premium falling, 0 = flat. The engine maps that onto the *underlying*
depending on whether the leg is a CALL or a PUT.
"""
from __future__ import annotations

import numpy as np

from app.models import Candle


def _closes(candles: list[Candle]) -> np.ndarray:
    return np.array([c.close for c in candles], dtype=float)


def analyse(candles: list[Candle]) -> dict:
    """Return {state, direction, velocity, acceleration, detail}.

    velocity      = average %/bar over the last 5 bars
    acceleration  = recent 3-bar velocity minus the prior 3-bar velocity
    """
    if candles is None or len(candles) < 8:
        return {"state": "CONSOLIDATION", "direction": 0, "velocity": 0.0,
                "acceleration": 0.0, "detail": "insufficient premium history"}

    c = _closes(candles)
    rets = np.diff(c) / (c[:-1] + 1e-9) * 100.0  # % change per bar
    vol = float(np.std(rets[-12:])) or 0.4  # typical per-bar noise
    velocity = float(np.mean(rets[-5:]))
    vel_recent = float(np.mean(rets[-3:]))
    vel_prev = float(np.mean(rets[-6:-3]))
    acceleration = vel_recent - vel_prev

    # range / breakout read
    window = c[-12:]
    hi = float(window[:-1].max())
    lo = float(window[:-1].min())
    last = float(c[-1])
    rng = hi - lo
    recent_rng = float(c[-6:].max() - c[-6:].min())
    compressed = rng > 0 and recent_rng <= 0.45 * rng

    fast = abs(velocity) > 1.6 * vol
    accelerating = (acceleration > 0) == (velocity > 0) and abs(acceleration) > 0.6 * vol
    turned_up = vel_prev < -0.4 * vol and vel_recent > 0.4 * vol
    turned_down = vel_prev > 0.4 * vol and vel_recent < -0.4 * vol

    direction = 1 if velocity > 0.25 * vol else -1 if velocity < -0.25 * vol else 0

    # --- classify (order matters: strongest reads first) ---
    if velocity > 0 and fast and accelerating:
        state = "EXPLOSION"
    elif velocity < 0 and fast and accelerating:
        state = "COLLAPSE"
    elif turned_up:
        state, direction = "RECOVERY", 1
    elif turned_down:
        state, direction = "BREAKDOWN", -1
    elif last > hi and velocity > 0:
        state, direction = "BREAKOUT", 1
    elif last < lo and velocity < 0:
        state, direction = "BREAKDOWN", -1
    elif compressed and abs(velocity) < 0.6 * vol:
        state, direction = "CONSOLIDATION", 0
    elif velocity > 0.5 * vol:
        state, direction = "MOMENTUM_UP", 1
    elif -1.2 * vol <= velocity < -0.25 * vol:
        state, direction = "DECAY", -1
    elif velocity < 0:
        state, direction = "MOMENTUM_DOWN", -1
    else:
        state, direction = "CONSOLIDATION", 0

    detail = f"vel {velocity:+.2f}%/bar, accel {acceleration:+.2f}"
    return {"state": state, "direction": direction, "velocity": round(velocity, 3),
            "acceleration": round(acceleration, 3), "detail": detail}


# States that, on the correct leg, are strong confirmation vs. warnings.
BULLISH_LEG_STATES = {"EXPLOSION", "MOMENTUM_UP", "BREAKOUT", "RECOVERY"}
BEARISH_LEG_STATES = {"COLLAPSE", "MOMENTUM_DOWN", "BREAKDOWN", "DECAY"}

# How "healthy" each premium behaviour is FOR THE BUYER of that leg. A leg you
# buy wants its own premium rising cleanly; decay/collapse/rejection is danger.
_LEG_QUALITY = {
    "EXPLOSION": 92,
    "BREAKOUT": 85,
    "MOMENTUM_UP": 78,
    "RECOVERY": 68,
    "CONSOLIDATION": 45,
    "DECAY": 28,
    "MOMENTUM_DOWN": 22,
    "BREAKDOWN": 15,
    "COLLAPSE": 8,
}


def leg_quality(beh: dict) -> dict:
    """Premium Quality for BUYING this leg: is the premium healthy or dangerous?

    Combines the classified state with the measured velocity/acceleration into a
    0..100 score plus a HEALTHY / NEUTRAL / DANGEROUS health label. This tells
    the dashboard whether the option we'd buy is actually behaving well, not just
    which direction the underlying leans.
    """
    if beh is None:
        return {"quality": None, "health": None, "momentum": None,
                "velocity": None, "acceleration": None}
    state = beh.get("state", "CONSOLIDATION")
    vel = float(beh.get("velocity", 0.0))
    acc = float(beh.get("acceleration", 0.0))
    base = _LEG_QUALITY.get(state, 45)
    # reward genuine upward momentum + positive acceleration, penalise the reverse
    base += max(-18.0, min(18.0, vel * 6.0))
    base += max(-10.0, min(10.0, acc * 5.0))
    quality = round(max(0.0, min(100.0, base)), 1)
    health = "HEALTHY" if quality >= 62 else "DANGEROUS" if quality <= 32 else "NEUTRAL"
    # momentum = signed strength 0..100 of the premium's own move
    momentum = round(max(-100.0, min(100.0, vel * 30.0)), 1)
    return {"quality": quality, "health": health, "momentum": momentum,
            "velocity": round(vel, 3), "acceleration": round(acc, 3)}


def underlying_bias(call_beh: dict, put_beh: dict) -> tuple[str, float, str]:
    """Combine ATM CALL + PUT premium behaviour into an *underlying* bias.

    Rising call premium and/or collapsing put premium => bullish underlying,
    and vice-versa. Returns (BULLISH|BEARISH|NEUTRAL, strength 0..1, detail).
    """
    score = 0.0
    # call leg: its own direction maps straight onto the underlying
    if call_beh["state"] in BULLISH_LEG_STATES:
        score += 1.0 if call_beh["state"] in ("EXPLOSION", "BREAKOUT") else 0.6
    elif call_beh["state"] in BEARISH_LEG_STATES:
        score -= 1.0 if call_beh["state"] == "COLLAPSE" else 0.6
    # put leg: inverted — a strong put means bearish underlying
    if put_beh["state"] in BULLISH_LEG_STATES:
        score -= 1.0 if put_beh["state"] in ("EXPLOSION", "BREAKOUT") else 0.6
    elif put_beh["state"] in BEARISH_LEG_STATES:
        score += 1.0 if put_beh["state"] == "COLLAPSE" else 0.6

    strength = min(1.0, abs(score) / 2.0)
    detail = f"CE {call_beh['state'].lower()} / PE {put_beh['state'].lower()}"
    if score > 0.4:
        return "BULLISH", strength, detail
    if score < -0.4:
        return "BEARISH", strength, detail
    return "NEUTRAL", strength, detail
