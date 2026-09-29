"""Was this a good price, or a chase? — §11.

The rule this module exists to enforce: entry quality is computed from GENUINE
signal-time premium history or it is UNKNOWN. There is no third option. A
historical row has no premium history — the 5-year pool is underlying-only — so
every replayed candidate is UNKNOWN here, permanently, and that is the correct
answer rather than a gap to be filled by interpolating a premium from the index.

What "chased" means in premium terms: the option has already run from where the
setup was first visible, so the same underlying target now sits further away in
percentage terms and the stop sits closer. The measurement is therefore against
the option's own recent path, not against the published entry zone alone — a
premium can be inside the zone and still be 30% above where it was ninety seconds
ago.

The history is a per-symbol ring in memory, fed by the live chain. It is
deliberately not persisted: a ring rebuilt from disk after a restart would span a
gap of unknown length, and a peak from before lunch is not the peak this entry is
being chased from.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from app.research.phase17 import schema

IDEAL = "IDEAL"
GOOD = "GOOD"
ACCEPTABLE = "ACCEPTABLE"
CHASED = "CHASED"
SEVERELY_CHASED = "SEVERELY_CHASED"
UNKNOWN = "UNKNOWN"
CLASSES: tuple[str, ...] = (IDEAL, GOOD, ACCEPTABLE, CHASED, SEVERELY_CHASED, UNKNOWN)

# Look-back for "recent". Long enough to contain the move that produced the
# signal, short enough that this morning's spike is not called a chase now.
LOOKBACK_SEC = 900
# Minimum observations before a judgement is offered at all. Two points can
# describe any slope; a handful cannot describe a peak.
MIN_SAMPLES = 5

# Premium expansion from the recent trough, as a percentage. Seeds, not fitted.
IDEAL_PCT = 3.0
GOOD_PCT = 8.0
ACCEPTABLE_PCT = 15.0
CHASED_PCT = 30.0

_LOCK = threading.Lock()
_HIST: dict[str, deque[tuple[float, float]]] = {}
_MAXLEN = 400


def note(symbol: str, premium: float | None, *, ts: float | None = None) -> None:
    """Record one live premium observation for ``symbol``."""
    if not symbol or not isinstance(premium, (int, float)) or float(premium) <= 0:
        return
    ts = time.time() if ts is None else float(ts)
    with _LOCK:
        ring = _HIST.get(symbol)
        if ring is None:
            ring = deque(maxlen=_MAXLEN)
            _HIST[symbol] = ring
        ring.append((ts, float(premium)))


def note_chain(quotes: list[schema.Quote], *, ts: float | None = None) -> int:
    """Record every quote in one captured observation. Returns rows noted."""
    n = 0
    for q in quotes:
        if q.symbol and q.premium:
            note(q.symbol, q.premium, ts=ts if ts is not None else q.snapshot_ts)
            n += 1
    return n


def history(symbol: str, *, now: float | None = None,
            lookback_sec: int = LOOKBACK_SEC) -> list[tuple[float, float]]:
    now = time.time() if now is None else float(now)
    with _LOCK:
        ring = list(_HIST.get(symbol) or ())
    return [(t, p) for t, p in ring if now - t <= lookback_sec]


def assess(q: schema.Quote | None, plan: schema.Plan, *,
           now: float | None = None) -> dict:
    """Entry quality for one quote. UNKNOWN unless real history supports it."""
    out: dict = {
        "entry_quality": UNKNOWN,
        "reasons": [],
        "signal_premium": None,
        "current_premium": None,
        "entry_zone": None,
        "distance_from_zone_pct": None,
        "premium_expansion_pct": None,
        "extension_from_trough_pct": None,
        "distance_from_recent_peak_pct": None,
        "recent_low": None,
        "recent_high": None,
        "samples": 0,
        "basis": "LIVE_PREMIUM_HISTORY",
    }
    if q is None or not q.symbol:
        out["reasons"].append("NO_QUOTE")
        return out
    px = q.ask if (q.has_book and isinstance(q.ask, (int, float))) else q.premium
    if not isinstance(px, (int, float)) or float(px) <= 0:
        out["reasons"].append("NO_PREMIUM")
        return out
    px = float(px)
    out["current_premium"] = px
    if plan.entry_low is not None and plan.entry_high is not None:
        out["entry_zone"] = [plan.entry_low, plan.entry_high]
        mid = (float(plan.entry_low) + float(plan.entry_high)) / 2.0
        if mid > 0:
            out["distance_from_zone_pct"] = round(100.0 * (px - mid) / mid, 3)

    hist = history(q.symbol, now=now)
    out["samples"] = len(hist)
    if len(hist) < MIN_SAMPLES:
        # Not a downgrade and not a pass: there is no basis for a judgement, and
        # saying UNKNOWN is what keeps these rows out of the §11 study instead of
        # diluting it with guesses.
        out["reasons"].append("INSUFFICIENT_PREMIUM_HISTORY")
        return out

    lows = min(p for _, p in hist)
    highs = max(p for _, p in hist)
    first = hist[0][1]
    out["recent_low"], out["recent_high"] = lows, highs
    out["signal_premium"] = first
    if lows > 0:
        out["extension_from_trough_pct"] = round(100.0 * (px - lows) / lows, 3)
    if first > 0:
        out["premium_expansion_pct"] = round(100.0 * (px - first) / first, 3)
    if highs > 0:
        out["distance_from_recent_peak_pct"] = round(100.0 * (px - highs) / highs, 3)

    ext = out["extension_from_trough_pct"]
    if ext is None:
        out["reasons"].append("NO_TROUGH")
        return out
    e = float(ext)
    if e <= IDEAL_PCT:
        out["entry_quality"] = IDEAL
    elif e <= GOOD_PCT:
        out["entry_quality"] = GOOD
    elif e <= ACCEPTABLE_PCT:
        out["entry_quality"] = ACCEPTABLE
    elif e <= CHASED_PCT:
        out["entry_quality"] = CHASED
    else:
        out["entry_quality"] = SEVERELY_CHASED
    return out


def historical_unknown() -> dict:
    """The row a replayed candidate gets. Explicit, so a reader of the artefacts
    can see WHY it is unknown rather than inferring it from an empty field."""
    return {
        "entry_quality": UNKNOWN,
        "reasons": ["UNDERLYING_ONLY_NO_PREMIUM_HISTORY"],
        "basis": "UNDERLYING_ONLY",
        "samples": 0,
    }


def reset() -> None:
    """Test seam."""
    with _LOCK:
        _HIST.clear()


def health() -> dict:
    with _LOCK:
        return {
            "symbols": len(_HIST),
            "observations": sum(len(v) for v in _HIST.values()),
            "lookback_sec": LOOKBACK_SEC,
            "min_samples": MIN_SAMPLES,
        }
