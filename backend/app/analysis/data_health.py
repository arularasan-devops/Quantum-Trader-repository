"""Per-instrument data quality, measured rather than assumed. OBSERVABILITY ONLY.

Nothing here is consulted by the decision engine, the gates, the confidence
formula, the strike selector or the execution path. It answers one question the
app previously could not answer about itself: *is the data this instrument is
being analysed on actually complete and current?*

Four sessions of recorded history put the honest answer at 46.5% of one-minute
bars missing and 38% of signals firing on data already flagged stale. Those
numbers were only discoverable by replaying a database offline, which meant the
degradation was invisible while it was happening. This module surfaces the same
measurements live, per instrument, so a contaminated board reads as contaminated
at the time rather than three weeks later.

Deliberately NOT hidden: a contaminated instrument is reported with its
``quality`` state and the reason, never dropped from the list. A missing
measurement is ``None``, never a zero — an instrument that has never ticked is
not an instrument with a fresh feed.
"""
from __future__ import annotations

import time

from app.config import settings
from app.market import tiers
from app.market.tick_quality import (
    DEAD,
    NO_DATA,
    STALE,
    classify_age,
    feed_quality,
)

# Bar completeness bands. The 5% figure is the acceptance threshold the research
# gates already use (``phase7.gates.MAX_MISSING_BAR_PCT``); it is restated here
# rather than imported, because a production observability module must not depend
# on a research package.
MAX_MISSING_BAR_PCT = 5.0
DEGRADED_MISSING_BAR_PCT = 20.0

# How old a recorded chain may be before the option view is not current. The
# chain is throttled to one snapshot per 60s by design, so 180s is three missed
# cycles — a real capture failure, not the throttle.
CHAIN_FRESH_SEC = 180.0
CHAIN_DEGRADED_SEC = 900.0

# Quality states, worst wins.
GOOD = "GOOD"
DEGRADED = "DEGRADED"
CONTAMINATED = "DATA_CONTAMINATED"
UNKNOWN = "UNKNOWN"

# UNKNOWN sits ABOVE good and BELOW degraded: an unmeasurable instrument must not
# read as healthy, and must not outrank a measured contamination either. Putting
# UNKNOWN last would let one never-ticked name hide a genuinely contaminated one.
_ORDER = {GOOD: 0, UNKNOWN: 1, DEGRADED: 2, CONTAMINATED: 3}


def _worst(*states: str) -> str:
    return max(states, key=lambda s: _ORDER.get(s, _ORDER[UNKNOWN]))


def _bar_state(missing_pct: float | None, bars: int) -> tuple[str, str | None]:
    if missing_pct is None or bars < 2:
        return UNKNOWN, "too few stored bars to measure completeness"
    if missing_pct <= MAX_MISSING_BAR_PCT:
        return GOOD, None
    if missing_pct <= DEGRADED_MISSING_BAR_PCT:
        return DEGRADED, f"{missing_pct:.1f}% of one-minute bars missing"
    return CONTAMINATED, (
        f"{missing_pct:.1f}% of one-minute bars missing — ATR, VWAP, extension "
        f"and room derive from these bars, so their magnitudes are unreliable"
    )


def _chain_state(age_sec: float | None) -> tuple[str, str | None]:
    if age_sec is None:
        return UNKNOWN, "no chain snapshot recorded for this instrument"
    if age_sec <= CHAIN_FRESH_SEC:
        return GOOD, None
    if age_sec <= CHAIN_DEGRADED_SEC:
        return DEGRADED, f"newest recorded chain is {age_sec / 60.0:.0f} min old"
    return CONTAMINATED, (
        f"newest recorded chain is {age_sec / 60.0:.0f} min old — strike, spread "
        f"and premium readings are not current"
    )


def _feed_state(feed_class: str) -> tuple[str, str | None]:
    if feed_class in (NO_DATA,):
        return UNKNOWN, "this instrument has never ticked"
    if feed_class == DEAD:
        return CONTAMINATED, "feed dead — the price being analysed is not current"
    if feed_class == STALE:
        return DEGRADED, "feed stale"
    return GOOD, None


def instrument_health(instrument: str, *, now: float | None = None,
                      bar_limit: int = 1200, store=None) -> dict:
    """Measure one instrument. Every field is either measured or ``None``."""
    now = time.time() if now is None else now
    if store is None:
        from app import storage as _storage

        store = _storage.store

    gaps = store.candle_gaps(instrument, limit=bar_limit,
                             step=int(settings.candle_interval_seconds))
    snap = feed_quality.snapshot(instrument, now=now)
    chain_ts = store.last_chain_ts(instrument)
    chain_age = (now - chain_ts) if chain_ts else None

    bar_q, bar_note = _bar_state(gaps.get("missing_pct"), int(gaps.get("bars") or 0))
    chain_q, chain_note = _chain_state(chain_age)
    feed_q, feed_note = _feed_state(str(snap.get("state") or NO_DATA))
    tier = tiers.tier_of(instrument, now)

    # A BROAD-tier instrument is not contaminated for lacking depth — it was
    # never supposed to have any. Reporting it as contaminated would make the
    # tier split look like a data failure, which is the opposite of the truth.
    if tier == tiers.BROAD:
        chain_q, chain_note = UNKNOWN, "broad tier — chain is not captured by design"
        if bar_q in (DEGRADED, CONTAMINATED):
            bar_note = f"{bar_note} (broad tier — bars are not warmed by design)"

    notes = [n for n in (bar_note, chain_note, feed_note) if n]
    return {
        "instrument": instrument,
        "tier": tier,
        # --- bar completeness ---
        "bars": gaps.get("bars"),
        "expected_bars": gaps.get("expected"),
        "missing_bars": gaps.get("missing"),
        "missing_bar_pct": gaps.get("missing_pct"),
        "largest_gap_bars": (gaps.get("gaps") or [{}])[0].get("missing_bars")
        if gaps.get("gaps") else None,
        # Duplicates and out-of-order rows cannot survive into storage: the
        # candles table is keyed (instrument, ts) and read back ordered. They are
        # counted where they DO occur — on the wire — by tick_quality.
        "duplicate_ticks": snap.get("duplicate_count"),
        "out_of_order_ticks": snap.get("out_of_order_count"),
        "invalid_ticks": snap.get("invalid_count"),
        "max_tick_gap_ms": snap.get("max_tick_gap_ms"),
        # --- freshness ---
        "feed_state": snap.get("state"),
        "feed_age_ms": snap.get("last_tick_age_ms"),
        "exchange_lag_ms": snap.get("exchange_to_receive_ms"),
        "last_exchange_ts": snap.get("last_exchange_ts"),
        "last_receive_ts": snap.get("last_receive_ts"),
        "source": snap.get("source"),
        "reconnects": snap.get("reconnect_count"),
        "rest_polls": snap.get("rest_polls"),
        # --- chain ---
        "chain_last_ts": chain_ts,
        "chain_age_sec": round(chain_age, 1) if chain_age is not None else None,
        # --- verdicts ---
        "bar_quality": bar_q,
        "chain_quality": chain_q,
        "feed_quality_state": feed_q,
        "quality": _worst(bar_q, chain_q, feed_q),
        "notes": notes,
    }


def summary(*, now: float | None = None, bar_limit: int = 1200,
            deep_only: bool = False, store=None) -> dict:
    """Measure the active universe.

    ``deep_only`` reports Tier 1 alone, which is the comparison that matters
    when judging the tier split: Tier 2 is not expected to have complete bars,
    so including it would flatter or damn the split for the wrong reason.
    """
    now = time.time() if now is None else now
    from app.market.instruments import UNIVERSE

    names = list(tiers.deep_names(now)) if deep_only else list(UNIVERSE)
    rows = [instrument_health(s, now=now, bar_limit=bar_limit, store=store)
            for s in names]

    measured = [r for r in rows if r["missing_bar_pct"] is not None]
    deep_measured = [r for r in measured if r["tier"] == tiers.DEEP]
    expected = sum(int(r["expected_bars"] or 0) for r in deep_measured)
    missing = sum(int(r["missing_bars"] or 0) for r in deep_measured)
    stale_states = {STALE, DEAD}
    ticked = [r for r in rows if r["feed_state"] not in (None, NO_DATA)]
    stale = [r for r in ticked if r["feed_state"] in stale_states]

    # The headline is DEEP-tier weighted completeness, because that is the
    # number the 5% gate is about and the only one the tier split can move.
    deep_missing_pct = round(100.0 * missing / expected, 2) if expected else None
    worst = _worst(*[r["quality"] for r in rows]) if rows else UNKNOWN
    return {
        "as_of": int(now),
        "tier_split_enabled": tiers.enabled(),
        "capacity": tiers.capacity(now),
        "instrument_count": len(rows),
        "deep_missing_bar_pct": deep_missing_pct,
        "deep_expected_bars": expected,
        "deep_missing_bars": missing,
        "target_missing_bar_pct": MAX_MISSING_BAR_PCT,
        "meets_target": (deep_missing_pct is not None
                         and deep_missing_pct <= MAX_MISSING_BAR_PCT),
        "ticked_count": len(ticked),
        "stale_feed_count": len(stale),
        "stale_feed_pct": round(100.0 * len(stale) / len(ticked), 1) if ticked else None,
        "worst_quality": worst,
        "contaminated": sorted(r["instrument"] for r in rows
                               if r["quality"] == CONTAMINATED),
        "instruments": rows,
        "promotions": tiers.promotions.log(50),
        "caveat": "measured over the stored window only; a short window after a "
                  "restart reads cleaner than the session actually was",
    }


def classify_feed_age(age_ms: float | None) -> str:
    """Re-exported so callers classify an age the same way the feed does."""
    return classify_age(age_ms)
