"""Two-tier feed routing: which instruments get DEEP capture and which get a
lightweight BROAD scan.

This module is FEED ROUTING ONLY. It decides where the expensive, rate-limited
work goes — one-minute candle warm-up and option-chain recording — and nothing
else. It holds no decision, gate, threshold, confidence, stop, target, strike or
order logic, and it is not permitted to import any of them.

Why it exists, in measured terms: with 50 instruments active, every one of them
warms one-minute candles from a single rate-limited historical API (Angel
AB1021). Over four recorded sessions that left 46.5% of one-minute bars missing
and 38% of production signals firing on data already flagged stale. The cause is
capture cost spread across too many names, so the fix is to spend it on fewer:

  TIER 1 / DEEP   the existing production engine, unchanged, on 6-8 names: full
                  ticks, complete one-minute candles, recorded chain with
                  bid/ask, full indicators.
  TIER 2 / BROAD  LTP, % move, short-term momentum, activity, feed age. Enough
                  to see something happening; not enough to trade off, and
                  deliberately so.

Default is OFF. With ``QT_DEEP_WATCHLIST`` unset, ``enabled()`` is False and
every caller behaves exactly as it did before this module existed. Which names
belong in Tier 1 is a trading decision made by the operator in configuration,
never inferred here.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from app.config import settings
from app.market.instruments import REGISTRY, UNIVERSE

DEEP = "DEEP"
BROAD = "BROAD"


def _parse(raw: str) -> list[str]:
    """Split a configured list, keeping order, dropping unknown names."""
    out: list[str] = []
    for part in (raw or "").replace(";", ",").split(","):
        sym = part.strip().upper()
        if sym and sym in REGISTRY and sym not in out:
            out.append(sym)
    return out


@dataclass(frozen=True)
class Promotion:
    """A Tier 2 name temporarily given deep capture. Research/feed only."""
    instrument: str
    reason: str
    move_pct: float
    momentum_pct: float
    at: float
    until: float


class _Promotions:
    """Live promotions, held in memory only.

    Nothing here is persisted: a promotion is a statement about the current
    session's feed, and reloading a stale one after a restart would silently
    spend deep capture on a name that stopped moving hours ago.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Promotion] = {}
        self._log: list[dict] = []

    def _expire(self, now: float) -> None:
        for sym, p in list(self._items.items()):
            if p.until <= now:
                del self._items[sym]

    def active(self, now: float | None = None) -> list[Promotion]:
        now = time.time() if now is None else now
        with self._lock:
            self._expire(now)
            return sorted(self._items.values(), key=lambda p: -abs(p.move_pct))

    def add(self, instrument: str, *, reason: str, move_pct: float,
            momentum_pct: float, now: float | None = None) -> Promotion | None:
        now = time.time() if now is None else now
        sym = (instrument or "").upper()
        if sym not in REGISTRY:
            return None
        hold = max(60.0, float(settings.tier_promote_hold_sec))
        p = Promotion(instrument=sym, reason=reason, move_pct=move_pct,
                      momentum_pct=momentum_pct, at=now, until=now + hold)
        with self._lock:
            self._expire(now)
            cap = max(0, int(settings.max_promoted_instruments))
            if sym not in self._items and len(self._items) >= cap:
                # Drop the weakest current promotion rather than refusing the
                # stronger new one; a full slate must not mean the loudest name
                # of the day is the one that never gets captured.
                weakest = min(self._items.values(), key=lambda q: abs(q.move_pct))
                if abs(weakest.move_pct) >= abs(move_pct):
                    return None
                del self._items[weakest.instrument]
                self._log.append({"ts": now, "instrument": weakest.instrument,
                                  "event": "demoted",
                                  "reason": f"displaced by {sym}"})
            self._items[sym] = p
            self._log.append({"ts": now, "instrument": sym, "event": "promoted",
                              "reason": reason, "move_pct": move_pct,
                              "momentum_pct": momentum_pct})
            self._log[:] = self._log[-500:]
        return p

    def log(self, limit: int = 200) -> list[dict]:
        with self._lock:
            return list(self._log[-limit:])

    def reset(self) -> None:
        with self._lock:
            self._items.clear()
            self._log.clear()


promotions = _Promotions()


def enabled() -> bool:
    """True when the operator has configured a Tier 1 list."""
    return bool(_parse(settings.deep_watchlist))


def configured_deep() -> list[str]:
    """The operator's Tier 1 list, capped. The cap is the point of the split:
    a long deep list reproduces the capture cost this exists to remove."""
    cap = max(1, int(settings.max_deep_instruments))
    return _parse(settings.deep_watchlist)[:cap]


def deep_names(now: float | None = None) -> list[str]:
    """Instruments receiving full capture: the configured list plus any live
    promotion. Returns the whole active universe when the split is off."""
    if not enabled():
        return list(UNIVERSE)
    names = configured_deep()
    if settings.tier_promotion_enabled:
        for p in promotions.active(now):
            if p.instrument not in names and p.instrument in UNIVERSE:
                names.append(p.instrument)
    return names


def broad_names(now: float | None = None) -> list[str]:
    """Instruments receiving the light scan only. Empty when the split is off —
    with a single tier there is no such thing as a broad-only name."""
    if not enabled():
        return []
    deep = set(deep_names(now))
    configured = _parse(settings.broad_watchlist)
    pool = configured or list(UNIVERSE)
    return [s for s in pool if s not in deep]


def tier_of(instrument: str, now: float | None = None) -> str:
    """Which tier an instrument is in. DEEP whenever the split is off, because
    that is what single-tier capture actually does to every name."""
    if not enabled():
        return DEEP
    return DEEP if (instrument or "").upper() in set(deep_names(now)) else BROAD


def is_deep(instrument: str, now: float | None = None) -> bool:
    return tier_of(instrument, now) == DEEP


def consider_promotion(instrument: str, *, move_pct: float | None,
                       momentum_pct: float | None, feed_age_sec: float | None,
                       now: float | None = None) -> Promotion | None:
    """Promote a Tier 2 name into deep capture when it is strongly interesting.

    Returns the promotion, or None with no side effects. A promotion changes
    what the feed CAPTURES and nothing else: no gate is relaxed, no signal is
    produced, no order is placed. Stale input can never promote — spending deep
    capture because of a late tick is how the feed got starved in the first
    place.
    """
    if not (settings.tier_promotion_enabled and enabled()):
        return None
    sym = (instrument or "").upper()
    if sym not in REGISTRY or sym in set(deep_names(now)):
        return None
    if feed_age_sec is None or feed_age_sec > float(settings.tier_promote_max_feed_age_sec):
        return None
    mv = abs(float(move_pct)) if move_pct is not None else 0.0
    mo = abs(float(momentum_pct)) if momentum_pct is not None else 0.0
    if mv < float(settings.tier_promote_min_move_pct):
        return None
    if mo < float(settings.tier_promote_min_momentum_pct):
        return None
    return promotions.add(sym, reason=f"move {mv:.2f}% momentum {mo:.2f}%",
                          move_pct=mv, momentum_pct=mo, now=now)


# Capture cost per instrument, in units of "one rate-limited historical API
# call per warm-up" and "one chain fetch per chain cycle". These are the two
# costs that starved the feed; a quote is comparatively free, which is exactly
# why the broad tier can be wide.
WARM_CALLS_PER_DEEP = 1
CHAIN_CALLS_PER_DEEP = 1
QUOTE_CALLS_PER_NAME = 1


def capacity(now: float | None = None) -> dict:
    """What the current tier split costs the feed, per cycle.

    Reported rather than asserted. This function makes no claim that the split
    improved anything — that requires a session actually recorded at the smaller
    Tier 1 and compared against one recorded at the full watchlist, which is a
    research measurement and not something this module may conclude.
    """
    active = list(UNIVERSE)
    deep = [s for s in deep_names(now) if s in active] if enabled() else active
    broad = [s for s in broad_names(now) if s in active]
    return {
        "split_enabled": enabled(),
        "active_count": len(active),
        "deep": deep,
        "deep_count": len(deep),
        "broad_count": len(broad),
        "max_deep_instruments": int(settings.max_deep_instruments),
        "promoted": [p.instrument for p in promotions.active(now)]
        if settings.tier_promotion_enabled and enabled() else [],
        # The cost model, stated so the numbers can be checked rather than
        # trusted. Warm-up ORDER and recorded-chain writes follow the deep count;
        # a broad name is still ticked by the scanner and still prices a chain
        # when its turn comes, so the per-tick provider cost is NOT reduced by
        # the split alone. Trimming the active universe is what reduces that, and
        # this dict must not be read as claiming otherwise.
        "warm_priority_names": len(deep) * WARM_CALLS_PER_DEEP,
        "recorded_chain_writes_per_cycle": len(deep) * CHAIN_CALLS_PER_DEEP,
        "quote_calls_per_cycle": (len(deep) + len(broad)) * QUOTE_CALLS_PER_NAME,
        "single_tier_recorded_chain_writes_per_cycle":
            len(active) * CHAIN_CALLS_PER_DEEP,
        "not_reduced_by_split": (
            "per-tick option-chain pricing for broad names: the scanner still "
            "ticks them, so REST relief beyond warm-up ordering and chain "
            "recording needs the active universe itself trimmed"),
    }
