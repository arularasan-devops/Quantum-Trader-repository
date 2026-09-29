"""Signal Board Journal — an immutable record of what the Signal tab actually said.

MEASUREMENT ONLY. This module cannot open, close, size, veto or delay a trade. It
is called after the decision is complete and it writes files nothing in the
trading path reads.

Why it exists at all: every conclusion about whether the engine is any good has so
far been reconstructed from a replay, and a replay can only see what the database
happened to store. The market state that produced a call — the regime, the gate
trace, the entry zone, the freshness of the tick it was computed on — was never
written down, so questions like "what did the engine predict, at what price, on
what evidence, and did each target actually arrive" were answerable only in
approximation. This records the call itself, at the moment it is made, and then
follows it to a resolution.

Two files under ``data_dir``, both append-only:

``signal_journal.jsonl``
    One row per distinct Signal-tab call — BUY, WAIT and NO_TRADE alike. Rows are
    never rewritten; a correction is a new event, not an edit.
``signal_outcomes.jsonl``
    Events against a journalled signal: each target as it is reached, the stop,
    the resolution. The terminal ``RESOLVED`` event carries the whole outcome.

Three deliberate refusals, because a journal that guesses is worse than no
journal:

* the greeks are **model-derived**. Angel One quotes an LTP, not an IV surface, so
  IV/delta/gamma/theta here come from inverting Black-Scholes on the LTP. Every
  row says so in ``greeks_source``, and a consumer that needs broker greeks must
  treat them as absent.
* the setup type is **derived from named engine fields or it is SETUP_UNKNOWN**.
  The engine does not classify setups, so each row carries the fields the label
  was read from. Nothing is inferred from a language model or from the outcome.
* the volatility class comes from the instrument's **own recorded history** as a
  percentile, not from a threshold someone chose. Below ``_VOL_MIN_HISTORY``
  observations there is no class, only the measured ATR percentage.

Outcome tracking continues past T1 on purpose. Production books at T1; the
journal keeps following the leg so "T1 → T2 → T3" and "T1 → STOP" are
distinguishable, which is what the give-back and exit questions need. Following a
signal is not holding a position — no order exists.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.market.instruments import REGISTRY
from app.market.tick_quality import feed_quality
from app.analysis.plan_validity import unusable as _plan_unusable
from app.analysis import a_plus_shadow
from app.analysis import direction_attribution
from app.analysis import entry_location
from app.analysis import entry_quality
from app.analysis import failure_attribution
from app.analysis import hold_window
from app.analysis import instrument_family
from app.analysis import signal_visibility
from app.analysis import tradability
from app.models import (
    Candle,
    Decision,
    FuturesSignalCard,
    IndicatorSnapshot,
    MarketStatus,
    OptionQuote,
)

JOURNAL_LOG = "signal_journal.jsonl"
OUTCOMES_LOG = "signal_outcomes.jsonl"

UNAVAILABLE = "UNAVAILABLE"
SETUP_UNKNOWN = "SETUP_UNKNOWN"
VOL_UNCLASSIFIED = "VOLATILITY_UNCLASSIFIED"

# Terminal outcomes, per §10. NO_ENTRY is a WAIT/NO_TRADE row: the board spoke and
# there was nothing to follow, which is still a record of what it said.
T1 = "T1"
T2 = "T2"
T3 = "T3"
STOP = "STOP"
TIMEOUT = "TIMEOUT"
EXPIRED = "EXPIRED"
INVALIDATED = "INVALIDATED"
NO_ENTRY = "NO_ENTRY"

# Which came first, per §12. Never scored from the eventual best price.
STOP_FIRST = "STOP_FIRST"
TARGET_FIRST = "TARGET_FIRST"
FIRST_UNKNOWN = "UNKNOWN"

_IST = timezone(timedelta(hours=5, minutes=30))
_LOCK = threading.Lock()

# A signal is followed for this long before it is resolved as TIMEOUT. Long
# enough for an intraday option leg to work, short enough that the record is
# about this call and not about the day.
_FOLLOW_SEC = 90 * 60
# The same board state is journalled once. Without this the file would carry one
# row per tick of an unchanged WAIT.
_REPEAT_SEC = 15 * 60
# How many past ATR percentages per instrument the volatility percentile is read
# from, and the minimum before any class is stated at all.
_VOL_HISTORY = 400
_VOL_MIN_HISTORY = 60

# in-memory only; a restart loses the open set and the affected signals resolve as
# UNRESOLVED in the statistics rather than being silently dropped or invented.
_open: dict[str, dict] = {}
_last_key: dict[str, tuple[str, float]] = {}
_vol_history: dict[str, deque[float]] = {}
# The last futures research card seen per instrument, so an option row can record
# what the other vehicle was offering on the same idea at the same moment (§14).
# Facts only, and stale-marked by age — nothing selects from this.
_last_futures: dict[str, dict] = {}

log = logging.getLogger(__name__)

# Journal writes are swallowed so a bad row cannot break a tick. That silence is
# itself a failure mode — it makes "nothing was recorded today" and "every write
# threw" look identical on the Reports tab — so each swallowed error is kept here
# and published by :func:`journal_health`.
@dataclass
class _Failures:
    count: int = 0
    last_error: str | None = None
    last_instrument: str | None = None
    last_at: str | None = None
    seen: set[str] = field(default_factory=set)


_failures = _Failures()


def _note_failure(instrument: str, exc: Exception, now: float) -> None:
    """Remember a swallowed journal error so it can be seen and fixed.

    The write is deliberately non-fatal, so the only evidence a tick failed is
    what is kept here. The exception type is enough to tell "the journal is
    broken" from "the market was quiet"; the traceback that says *where* goes to
    the log only, never into an API response.
    """
    _failures.count += 1
    _failures.last_error = type(exc).__name__
    _failures.last_instrument = instrument
    _failures.last_at = _ist(now)
    # Logged once per distinct error so a broken tick is visible in the log
    # without a repeating message every scan drowning everything else.
    if _failures.last_error not in _failures.seen:
        _failures.seen.add(_failures.last_error)
        log.warning("signal journal write failed (%s): %s\n%s", instrument,
                    exc, traceback.format_exc(limit=8))


def journal_health() -> dict:
    """Whether the journal is actually recording, and why not if it is not.

    An empty Reports tab has two very different causes — a quiet session, or a
    journal that is throwing on every tick — and they must never look the same.
    """
    rows_today = 0
    last_row_at: str | None = None
    session = _session(time.time())
    try:
        with open(journal_path(), encoding="utf-8") as fh:
            for line in fh:
                # Cheap prefilter first: the date also appears in expiry and
                # other fields, so a substring hit is only a candidate and the
                # row is confirmed on ``recorded_at`` before it is counted.
                if session not in line:
                    continue
                at = json.loads(line).get("recorded_at")
                if isinstance(at, str) and at.startswith(session):
                    rows_today += 1
                    last_row_at = at
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    healthy = _failures.count == 0
    return {
        "enabled": bool(settings.signal_journal_log),
        # File name only — the absolute path is server internals.
        "file": JOURNAL_LOG,
        "session": session,
        "rows_today": rows_today,
        "last_row_at": last_row_at,
        "write_failures": _failures.count,
        "last_error": _failures.last_error,
        "last_error_at": _failures.last_at,
        "last_error_instrument": _failures.last_instrument,
        "verdict": (
            "DISABLED — signal_journal_log is off, nothing is being recorded"
            if not settings.signal_journal_log else
            "FAILING — the journal is throwing on write, so the Reports tab is "
            "empty because nothing is being stored, not because the market was "
            "quiet. The traceback is in the backend log"
            if not healthy else
            "RECORDING"
            if rows_today else
            "IDLE — no error, but nothing recorded this session yet: either the "
            "scanner has not run or every call repeated the previous board state"
        ),
    }


def journal_path() -> str:
    return os.path.join(settings.data_dir, JOURNAL_LOG)


def outcomes_path() -> str:
    return os.path.join(settings.data_dir, OUTCOMES_LOG)


def _append(path: str, rec: dict) -> None:
    os.makedirs(settings.data_dir, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


def _ist(ts: float) -> str:
    return datetime.fromtimestamp(ts, _IST).strftime("%Y-%m-%d %H:%M:%S")


def _session(ts: float) -> str:
    return datetime.fromtimestamp(ts, _IST).strftime("%Y-%m-%d")


def _leg(chain: list[OptionQuote], symbol: str | None) -> OptionQuote | None:
    if not symbol:
        return None
    for q in chain:
        if q.symbol == symbol:
            return q
    return None


def _spread(quote: OptionQuote | None) -> dict:
    """The book at signal time, and whether it was quoted at all.

    A spread computed from a missing bid is not a narrow spread, so absence is
    reported as absence.
    """
    if quote is None or quote.bid is None or quote.ask is None:
        return {"bid": None, "ask": None, "spread": None,
                "spread_pct_of_premium": None, "book_source": UNAVAILABLE}
    bid, ask = float(quote.bid), float(quote.ask)
    spread = round(ask - bid, 2)
    prem = float(quote.premium) or None
    return {
        "bid": bid,
        "ask": ask,
        "spread": spread,
        "spread_pct_of_premium": round(100.0 * spread / prem, 2) if prem else None,
        "book_source": "REAL_BROKER_QUOTE",
    }


def _setup(dec: Decision, ind: IndicatorSnapshot, candles: list[Candle],
           price: float) -> dict:
    """§8 — the setup type, read from named engine fields or left unknown.

    The engine has no setup classifier. Rather than invent one and present its
    guess as the engine's intent, this reads the fields the engine does set and
    records which of them produced the label.
    """
    ev: list[str] = []
    label = SETUP_UNKNOWN

    breakout = (ind.breakout or "NONE").upper()
    pullback = (ind.pullback or "NONE").upper()
    ignition = (ind.ignition or "NONE").upper()
    flip = (ind.supertrend_flip or "NONE").upper()
    trigger = (dec.entry_trigger or "NONE").upper()
    pattern = (ind.chart_pattern or "").upper()

    if trigger == "PULLBACK" or pullback.startswith("PULLBACK"):
        label = "PULLBACK"
        ev = ["decision.entry_trigger", "indicators.pullback"]
    elif breakout in ("BREAKOUT", "BREAKDOWN"):
        label = "BREAKOUT"
        ev = ["indicators.breakout"]
    elif trigger == "BREAKOUT_RETEST":
        label = "RANGE_BREAK"
        ev = ["decision.entry_trigger"]
    elif ignition.startswith("IGNITION"):
        label = "MOMENTUM"
        ev = ["indicators.ignition"]
    elif flip.startswith("FLIP") or pattern in ("V_REVERSAL", "INVERTED_V"):
        label = "REVERSAL"
        ev = ["indicators.supertrend_flip", "indicators.chart_pattern"]
    elif ind.vwap is not None and len(candles) >= 2:
        prev, vwap = float(candles[-2].close), float(ind.vwap)
        if prev < vwap <= price:
            label = "VWAP_RECLAIM"
            ev = ["indicators.vwap", "candles[-2].close"]
        elif prev > vwap >= price:
            label = "VWAP_RECLAIM"
            ev = ["indicators.vwap", "candles[-2].close"]

    if label == SETUP_UNKNOWN and (ind.trend or "").upper() in ("UP", "DOWN") \
            and (dec.htf_trend or "").upper() == (ind.trend or "").upper():
        label = "TREND_CONTINUATION"
        ev = ["decision.htf_trend", "indicators.trend"]

    return {
        "setup_type": label,
        "setup_evidence_fields": ev,
        "setup_source": ("DERIVED_FROM_ENGINE_FIELDS" if label != SETUP_UNKNOWN
                         else "ENGINE_DOES_NOT_CLASSIFY_SETUPS"),
        "entry_trigger": dec.entry_trigger,
        "breakout": ind.breakout,
        "pullback": ind.pullback,
        "ignition": ind.ignition,
        "supertrend_flip": ind.supertrend_flip,
        "chart_pattern": ind.chart_pattern,
    }


def _volatility(instrument: str, ind: IndicatorSnapshot, price: float,
                candles: list[Candle]) -> dict:
    """§14 — volatility at signal time, classified against recorded history only."""
    atr = float(ind.atr) if ind.atr else None
    atr_pct = round(100.0 * atr / price, 3) if atr and price else None
    recent = candles[-20:]
    rng = None
    if recent:
        hi = max(float(c.high) for c in recent)
        lo = min(float(c.low) for c in recent)
        rng = round(hi - lo, 2)

    hist = _vol_history.setdefault(instrument, deque(maxlen=_VOL_HISTORY))
    percentile: float | None = None
    label = VOL_UNCLASSIFIED
    basis = f"fewer than {_VOL_MIN_HISTORY} recorded observations for this instrument"
    if atr_pct is not None:
        if len(hist) >= _VOL_MIN_HISTORY:
            below = sum(1 for v in hist if v <= atr_pct)
            percentile = round(100.0 * below / len(hist), 1)
            if percentile < 25.0:
                label = "LOW_VOLATILITY"
            elif percentile < 75.0:
                label = "NORMAL_VOLATILITY"
            elif percentile < 95.0:
                label = "HIGH_VOLATILITY"
            else:
                label = "EXTREME_VOLATILITY"
            basis = (f"percentile of the last {len(hist)} recorded ATR% readings "
                     f"for this instrument")
        hist.append(atr_pct)

    return {
        "atr_points": atr,
        "atr_pct_of_price": atr_pct,
        "recent_range_points": rng,
        "momentum": ind.momentum,
        "expansion_state": ind.premium_state,
        "volatility_class": label,
        "volatility_percentile": percentile,
        "volatility_class_basis": basis,
    }


def _gates(dec: Decision) -> dict:
    g = dec.gates
    if g is None:
        return {"gate_states": UNAVAILABLE, "primary_blocker": None,
                "secondary_blocker": None, "blockers": []}
    states = {k: v for k, v in g.model_dump().items() if isinstance(v, bool)}
    return {
        "gate_states": states,
        "gates_failed": sorted(k for k, v in states.items() if not v),
        "primary_blocker": g.primary_blocker,
        "secondary_blocker": g.secondary_blocker,
        "blockers": list(g.blockers),
    }


def _identity(instrument: str, dec: Decision, quote: OptionQuote | None,
              expiry: str | None, ts: float) -> dict:
    spec = REGISTRY.get(instrument)
    return {
        "instrument": instrument,
        "exchange": spec.exchange if spec else UNAVAILABLE,
        "lot_size": spec.lot_size if spec else None,
        "vehicle": "OPTION" if dec.recommended_option else "NONE",
        "option_type": dec.option_type.value if dec.option_type else None,
        "strike": dec.strike,
        "expiry": expiry,
        "tradingsymbol": dec.recommended_option,
        "moneyness": dec.moneyness,
        "premium": float(quote.premium) if quote else dec.current_premium,
        "session": _session(ts),
        "time_ist": _ist(ts),
        "ts": int(ts),
    }


def _market_state(dec: Decision, ind: IndicatorSnapshot, quote: OptionQuote | None,
                  price: float, status: MarketStatus, instrument: str,
                  now: float) -> dict:
    feed = feed_quality.snapshot(instrument, now)
    return {
        "underlying_ltp": price,
        "atm_strike": dec.atm_strike,
        "leg_ltp": float(quote.premium) if quote else None,
        **_spread(quote),
        "volume": int(quote.volume) if quote else None,
        "oi": int(quote.oi) if quote else None,
        "oi_change": int(quote.oi_change) if quote else None,
        # Angel One quotes an LTP only; these are inverted from it, not broker
        # greeks, and a consumer must not read them as market-implied.
        "iv": float(quote.iv) if quote else None,
        "delta": float(quote.delta) if quote else None,
        "gamma": float(quote.gamma) if quote else None,
        "theta": float(quote.theta) if quote else None,
        "greeks_source": ("MODEL_DERIVED_FROM_LTP" if quote else UNAVAILABLE),
        "regime": status.value if status else None,
        "trend_1m": ind.trend,
        "trend_5m": dec.htf_trend,
        "htf_strength": dec.htf_strength,
        "market_structure": ind.market_structure,
        "vwap": ind.vwap,
        "adx": ind.adx,
        "rsi": ind.rsi,
        "expected_move_points": dec.expected_move_points,
        "feed_state": feed.get("state"),
        "feed_age_ms": feed.get("last_tick_age_ms"),
        "data_quality_score": feed.get("data_quality_score"),
        "exchange_to_receive_ms": feed.get("exchange_to_receive_ms"),
    }


def _vehicle_facts(row: dict, quote: OptionQuote | None) -> dict:
    """§7 evidence about the contract, as recorded at signal time."""
    trad = row.get("tradability") or {}
    state = row.get("market_state") or {}
    liq = trad.get("liquidity") or {}
    return {
        "spread_pct_of_premium": state.get("spread_pct_of_premium"),
        "spread_over_risk": trad.get("spread_over_risk"),
        "expected_room_r": trad.get("expected_room_r"),
        "thin_liquidity": liq.get("label") == "THIN",
        "tradability_status": trad.get("status"),
        "delta": float(quote.delta) if quote and quote.delta is not None else None,
        "book_quoted": bool(quote and quote.bid is not None and quote.ask is not None),
    }


def _vehicle_selection(instrument: str, dec: Decision, trad: dict | None,
                       now: float) -> dict:
    """§14 — the market idea, the vehicle it was expressed in, and what else was
    on offer at that moment.

    Three separate things the journal used to record as one. The market idea is a
    direction on the underlying; the vehicle is the contract that idea had to be
    bought through; the execution is whether anything was actually followed.
    Keeping them apart is what makes "the read was right, the option was wrong" a
    measurable statement rather than an opinion.

    Recording only. Production selects the option leg exactly as it always has —
    ``selected_vehicle`` reports that choice, it does not make it, and the futures
    alternative is written down precisely because nothing acts on it.
    """
    option = {
        "vehicle": instrument_family.OPTIONS,
        "unit": "PREMIUM",
        "available": bool(dec.recommended_option and dec.current_premium),
        "contract": dec.recommended_option,
        "leg": dec.vehicle,
        "entry": float(dec.current_premium) if dec.current_premium else None,
        "stop": float(dec.stop_loss) if dec.stop_loss is not None else None,
        "targets": {"T1": dec.target1, "T2": dec.target2, "T3": dec.target3},
        "spread": (trad or {}).get("spread"),
        "spread_pct_of_premium": (trad or {}).get("spread_pct_of_premium"),
        "spread_over_risk": (trad or {}).get("spread_over_risk"),
        "expected_room_r": (trad or {}).get("expected_room_r"),
        "tradability_status": (trad or {}).get("status"),
    }
    fut = _last_futures.get(instrument)
    futures = {
        "vehicle": instrument_family.FUTURES,
        "unit": "INDEX_POINTS",
        "available": bool(fut and fut.get("status") == "VALID_FUTURES_PLAN"),
        "contract": (fut or {}).get("contract"),
        "direction": (fut or {}).get("direction"),
        "status": (fut or {}).get("status"),
        "entry": (fut or {}).get("entry"),
        "stop": (fut or {}).get("stop"),
        "targets": (fut or {}).get("targets"),
        "spread": (fut or {}).get("spread_points"),
        "days_to_expiry": (fut or {}).get("days_to_expiry"),
        "rolled": (fut or {}).get("rolled"),
        "quoted_at": (fut or {}).get("time_ist"),
        "age_seconds": (round(now - float(fut["ts"]), 1)
                        if fut and fut.get("ts") else None),
        "executable": False,
    }
    no_trade = {
        "vehicle": "NO_TRADE",
        "unit": "NONE",
        "available": True,
        "reason": "always available; costs nothing and loses nothing",
    }
    return {
        "market_idea": {
            "direction": ("BEARISH" if (dec.vehicle or "").upper() == "PE"
                          else "BULLISH" if dec.vehicle else None),
            "action": dec.market_signal.value if dec.market_signal else None,
            "signal_score": dec.trade_score,
            "underlying": instrument,
            "underlying_price": dec.spot_price,
        },
        "selected_vehicle": (instrument_family.OPTIONS if option["available"]
                             else "NO_TRADE"),
        "selection_reason": ("PRODUCTION_TRADES_OPTIONS_ONLY"
                             if option["available"] else "NO_TAKEABLE_LEG"),
        "vehicle_alternatives": [option, futures, no_trade],
        "research_only": True,
        "note": ("the alternatives are recorded, not ranked: no vehicle is chosen "
                 "by this block and there is no futures order path"),
    }


def _entry_quality(dec: Decision, ind: IndicatorSnapshot,
                   price: float) -> dict | None:
    """§11 — where in the move this call was bought, measured at signal time.

    ``plan_premium`` is the premium the printed levels were computed from, which
    is the closest thing recorded to "the premium when the setup formed"; the
    live premium is what a buyer actually pays. The gap between them is the
    expansion the buyer is paying for, and on the 26 Aug card the user
    photographed it was the whole complaint.

    Evidence only. The chase guard, strike selector and premium floor are
    untouched, so a SEVERELY_CHASED call still reaches the dashboard.
    """
    if not settings.entry_quality_research:
        return None
    lo = hi = None
    if dec.entry_range:
        lo, hi = float(dec.entry_range[0]), float(dec.entry_range[1])
    entry = float(dec.current_premium) if dec.current_premium else None
    stop = float(dec.stop_loss) if dec.stop_loss is not None else None
    return entry_quality.classify(
        premium=entry,
        entry_zone_low=lo,
        entry_zone_high=hi,
        risk_points=((entry - stop) if entry is not None and stop is not None
                     else None),
        first_target=float(dec.target1) if dec.target1 else None,
        premium_at_setup=(float(dec.plan_premium) if dec.plan_premium else None),
        underlying_price=price,
        underlying_ref=float(ind.vwap) if ind.vwap is not None else None,
        atr=float(ind.atr) if ind.atr else None,
    )


def _entry_location(dec: Decision, eq: dict | None) -> dict | None:
    """13A — where the live premium sits against the plan's own entry.

    Reads the entry-quality zone distance so the two labels can never disagree
    about the same premium. DISPLAY ONLY: a WAIT_PULLBACK card is still the BUY
    the engine published, at the levels the engine published.
    """
    if not settings.entry_location_research:
        return None
    entry = float(dec.current_premium) if dec.current_premium else None
    stop = float(dec.stop_loss) if dec.stop_loss is not None else None
    lo = float(dec.entry_range[0]) if dec.entry_range else None
    hi = float(dec.entry_range[1]) if dec.entry_range else None
    return entry_location.classify(
        premium=entry,
        risk_points=((entry - stop) if entry is not None and stop is not None
                     else None),
        entry_zone_low=lo,
        entry_zone_high=hi,
        first_target=float(dec.target1) if dec.target1 else None,
        planned_entry=(float(dec.plan_premium) if dec.plan_premium else None),
        entry_quality_state=(eq or {}).get("state"),
        zone_distance_r=(eq or {}).get("zone_distance_r"),
    )


def _attach_entry_state(dec: Decision, state: dict | None) -> None:
    """Put the 13A reading on the card. Nothing downstream reads these."""
    if not state:
        return
    dec.entry_state = state["state"]
    dec.entry_state_reasons = list(state.get("reasons") or [])
    dec.pullback_level = state.get("pullback_level")
    dec.room_fraction_remaining = state.get("room_fraction_remaining")


def _hold_window(instrument: str, dec: Decision) -> dict | None:
    """13B — the measured time-to-resolution window for this cohort.

    The interquartile time-to-T1 of resolved calls on this instrument and side,
    with the p90 as the long tail. It is not a probability that the target
    arrives — only 27.4% of calls reached the underlying target before the stop —
    and it does not replace ``expected_holding_minutes``, which production still
    computes from directional strength exactly as before.
    """
    if not settings.entry_location_research:
        return None
    return hold_window.window(
        instrument, dec.option_type.value if dec.option_type else None)


def _attach_hold_window(dec: Decision, win: dict | None) -> None:
    """Put the 13B window on the card, beside — never over — the plan's own
    ``expected_holding_minutes``."""
    if not win:
        return
    dec.hold_expected_low_min = win.get("expected_low")
    dec.hold_expected_high_min = win.get("expected_high")
    dec.hold_long_tail_min = win.get("long_tail")
    dec.hold_window_basis = win.get("basis")


def _tradability(instrument: str, dec: Decision, quote: OptionQuote | None,
                 now: float, entry_state: str | None = None,
                 record: bool = True) -> dict | None:
    """§4 — the tradability assessment for this call, recorded for the studies.

    ``record`` False assesses without adding the row to the study aggregate: the
    display badge re-reads the same call on every tick and must not be counted
    as a new observation of it.
    """
    if not settings.tradability_research:
        return None
    entry = float(dec.current_premium) if dec.current_premium else None
    stop = float(dec.stop_loss) if dec.stop_loss is not None else None
    risk = (entry - stop) if entry is not None and stop is not None else None
    age_ms = feed_quality.snapshot(instrument, now).get("last_tick_age_ms")
    spec = REGISTRY.get(instrument)
    result = tradability.assess(
        instrument,
        premium=(float(quote.premium) if quote and quote.premium else entry),
        bid=float(quote.bid) if quote and quote.bid is not None else None,
        ask=float(quote.ask) if quote and quote.ask is not None else None,
        risk_points=risk,
        first_target=float(dec.target1) if dec.target1 else None,
        volume=float(quote.volume) if quote and quote.volume is not None else None,
        open_interest=float(quote.oi) if quote and quote.oi is not None else None,
        quote_age_sec=(float(age_ms) / 1000.0 if age_ms is not None else None),
        lot_size=(int(spec.lot_size) if spec and spec.lot_size else None),
        delta=float(quote.delta) if quote and quote.delta is not None else None,
        iv=float(quote.iv) if quote and quote.iv is not None else None,
        entry_quality=entry_state,
        vehicle=instrument_family.OPTIONS,
    )
    if record:
        tradability.record(result)
    return result


def _entry_plan(dec: Decision, expiry: str | None, session: str,
                minutes_to_expiry: int | None) -> dict:
    lo = hi = None
    if dec.entry_range:
        lo, hi = float(dec.entry_range[0]), float(dec.entry_range[1])
    entry = float(dec.current_premium) if dec.current_premium else None
    stop = float(dec.stop_loss) if dec.stop_loss is not None else None
    risk = round(entry - stop, 2) if entry is not None and stop is not None else None
    return {
        "entry_price": entry,
        "entry_zone_low": lo,
        "entry_zone_high": hi,
        "do_not_buy_above": dec.do_not_buy_above,
        "underlying_entry": dec.spot_price,
        "underlying_stop": dec.underlying_stop,
        "stop": stop,
        "stop_pct": (round(100.0 * risk / entry, 2)
                     if risk is not None and entry else None),
        "risk_points": risk,
        "target1": dec.target1,
        "target2": dec.target2,
        "target3": dec.target3,
        "expected_r": (round((float(dec.target1) - entry) / risk, 2)
                       if dec.target1 is not None and entry is not None
                       and risk not in (None, 0) else None),
        "expected_points": (round(float(dec.target1) - entry, 2)
                            if dec.target1 is not None and entry is not None
                            else None),
        "expected_pct": (round(100.0 * (float(dec.target1) - entry) / entry, 2)
                         if dec.target1 is not None and entry else None),
        "expected_holding_minutes": dec.expected_holding_minutes,
        "suggested_lots": dec.suggested_lots,
        "minutes_to_expiry": minutes_to_expiry,
        # Expiry day is read from the quoted expiry date, not inferred from the
        # minutes remaining: a Thursday afternoon and an expiry morning can both
        # be under a day and they are not the same trade.
        "expiry_day": (expiry == session if expiry else None),
    }


def _board_key(dec: Decision) -> str:
    """What counts as a distinct board call.

    The action, the leg and — for a refusal — the gate that refused it. A WAIT
    that becomes a WAIT for a different reason is a different statement about the
    market and is journalled again.
    """
    action = dec.market_signal.value if dec.market_signal else (
        dec.signal.value if dec.signal else "NONE")
    blocker = dec.gates.primary_blocker if dec.gates else None
    return "|".join([
        action,
        dec.recommended_option or "-",
        dec.option_type.value if dec.option_type else "-",
        str(dec.strike or "-"),
        blocker or "-",
    ])


def attach_badges(instrument: str, dec: Decision, chain: list[OptionQuote],
                  ind: IndicatorSnapshot, price: float,
                  now: float | None = None) -> None:
    """§15 — put the research labels for THIS call on the card. DISPLAY ONLY.

    On 26 Aug the 17 UNTRADABLE and 29 REJECT_SPREAD calls carried ₹96,231 of
    the ₹1,09,909 shadow loss and reached the board looking exactly like a clean
    call, because §4/§11/§13 were specified research-only. This writes the labels
    onto the card so a reader can see them.

    It changes no decision: the signal, score, levels, strike and
    ``plan_actionable`` are already computed when this runs, nothing downstream
    reads these fields, and the assessment is not recorded into the study
    aggregate (the same call is re-badged every tick). Best-effort by contract —
    a badge must never break a tick.
    """
    if not settings.signal_badges:
        return
    now = time.time() if now is None else now
    try:
        quote = _leg(chain, dec.recommended_option)
        eq = _entry_quality(dec, ind, price)
        trad = _tradability(instrument, dec, quote, now,
                            entry_state=(eq or {}).get("state"), record=False)
        if eq:
            dec.entry_quality = eq["state"]
            dec.entry_zone_distance_r = eq.get("zone_distance_r")
        _attach_entry_state(dec, _entry_location(dec, eq))
        _attach_hold_window(dec, _hold_window(instrument, dec))
        if trad:
            dec.tradability = trad["status"]
            dec.tradability_reasons = list(trad.get("reasons") or [])
            dec.spread_pct_of_premium = trad.get("spread_pct_of_premium")
            over_risk = trad.get("spread_over_risk")
            dec.spread_over_risk_pct = (round(100.0 * float(over_risk), 1)
                                        if over_risk is not None else None)
        if settings.a_plus_shadow:
            grade = a_plus_shadow.classify(
                instrument,
                tradability_row=trad,
                score=dec.trade_score,
                plan_actionable=dec.plan_actionable,
                has_leg=bool(dec.recommended_option),
                delta=(float(quote.delta)
                       if quote and quote.delta is not None else None),
                family=instrument_family.family(instrument),
                entry_state=(eq or {}).get("state"),
            )
            dec.a_plus_label = grade["label"]
            dec.a_plus_reasons = list(grade.get("reasons") or [])
    except Exception:
        return


def board_key(dec: Decision) -> str:
    """Public form of :func:`_board_key`.

    The lifecycle ledger derives its episode id from the same key, so a call is
    the same episode in the journal and in the reconciliation instead of two
    ids that only look related.
    """
    return _board_key(dec)


def observe(instrument: str, dec: Decision, chain: list[OptionQuote],
            ind: IndicatorSnapshot, price: float, status: MarketStatus,
            candles: list[Candle], now: float | None = None,
            expiry: str | None = None,
            minutes_to_expiry: int | None = None) -> None:
    """Journal this Signal-tab call and advance any signal already being followed.

    Best-effort by contract: a failure must never break a tick. But it must not
    be invisible either — swallowing the exception silently is what turns a
    broken journal into an empty Reports tab with no explanation, indistinguishable
    from a quiet market. So the failure is counted and kept, and
    :func:`journal_health` publishes it.
    """
    if not settings.signal_journal_log:
        return
    now = time.time() if now is None else now
    try:
        with _LOCK:
            _follow(instrument, chain, now, price)
            _maybe_journal(instrument, dec, chain, ind, price, status, candles,
                           now, expiry, minutes_to_expiry)
    except Exception as exc:
        _note_failure(instrument, exc, now)
        return


def _maybe_journal(instrument: str, dec: Decision, chain: list[OptionQuote],
                   ind: IndicatorSnapshot, price: float, status: MarketStatus,
                   candles: list[Candle], now: float,
                   expiry: str | None, minutes_to_expiry: int | None) -> None:
    key = _board_key(dec)
    last = _last_key.get(instrument)
    if last is not None and last[0] == key and now - last[1] < _REPEAT_SEC:
        return
    _last_key[instrument] = (key, now)

    action = dec.market_signal.value if dec.market_signal else (
        dec.signal.value if dec.signal else "NONE")
    quote = _leg(chain, dec.recommended_option)
    # Milliseconds, not seconds: two different calls inside the same second
    # otherwise share an id, and a report cannot then tell them apart.
    signal_id = f"{instrument}-{int(now * 1000)}-{action}"
    eq = _entry_quality(dec, ind, price)
    trad = _tradability(instrument, dec, quote, now,
                        entry_state=(eq or {}).get("state"))
    row = {
        "signal_id": signal_id,
        # §14 — the id of the market idea, which is the same across every vehicle
        # it could have been expressed in. The option leg is one expression of it.
        "market_signal_id": dec.global_signal_id or signal_id,
        "episode_id": f"{instrument}-{_session(now)}-{key}",
        # The id the dashboard and the lifecycle ledger know this call by, so a
        # journal row can be matched to a dashboard signal (or its absence
        # proven) instead of being compared by instrument and timestamp.
        "global_signal_id": dec.global_signal_id,
        "market": dec.market,
        "recorded_at": _ist(now),
        "board_action": action,
        "position_signal": dec.signal.value if dec.signal else None,
        **_identity(instrument, dec, quote, expiry, now),
        # The traded leg (CE / PE / FUTURES). Kept beside the coarser
        # ``vehicle`` ("OPTION"/"NONE") that existing reports already filter on,
        # so adding the finer filter does not change what they mean.
        "signal_vehicle": dec.vehicle,
        "market_state": _market_state(dec, ind, quote, price, status, instrument, now),
        "signal_info": {
            "signal_score": dec.trade_score,
            "confidence": dec.confidence,
            "signal_strength": dec.signal_strength,
            "trade_quality": dec.trade_quality,
            "risk_level": dec.risk_level,
            "opportunity_score": dec.opportunity_score,
            "conviction_meter": dec.conviction_meter,
            "observed_target_rate": dec.observed_target_rate,
            "reason_codes": list(dec.reasons),
            "reason_text": "; ".join(dec.reasons),
            **_gates(dec),
            **_setup(dec, ind, candles, price),
        },
        "volatility": _volatility(instrument, ind, price, candles),
        "entry_plan": _entry_plan(dec, expiry, _session(now), minutes_to_expiry),
        # Phase 12 §3/§4 — the family this call belongs to, and whether the
        # contract it must be expressed in was economically takeable. Evidence
        # attached to the row; no gate reads it.
        "family": instrument_family.family(instrument),
        "tradability": trad,
        # Phase 12A §11 — entry location at signal time. Recorded, never enforced.
        "entry_quality": eq,
        # Phase 13A/13B — the entry state against the plan's own entry and the
        # measured hold window for this cohort, recorded so a future session's
        # rows carry real labels instead of ones backfilled from the plan.
        "entry_location": _entry_location(dec, eq),
        "hold_window": _hold_window(instrument, dec),
        # §14 — market idea, vehicle selection and the alternatives, kept apart.
        "vehicle_selection": _vehicle_selection(instrument, dec, trad, now),
        "followed": False,
        "immutable": True,
        "note": ("an observational record of what the Signal tab said; no order "
                 "exists on this row"),
    }

    # A call is followed when a buy was stated — as the market bias or as the
    # position the board printed — and it carried the leg, premium, stop and
    # first target an outcome can be measured against.
    called_buy = action == "BUY" or (dec.signal and dec.signal.value == "BUY")
    tracked = (called_buy and dec.recommended_option
               and dec.current_premium and dec.stop_loss is not None
               and dec.target1 is not None)
    # A plan whose target is not above the premium, or whose stop is not below
    # it, has no trade in it: following it books an instant "target" the moment
    # it is written and divides by a zero risk. It is recorded as a call and
    # named, but it is never followed as an outcome.
    unusable = _plan_unusable(dec.current_premium, dec.stop_loss, dec.target1) if tracked else None
    if unusable is not None:
        tracked = False
    row["plan_state"] = dec.plan_state
    row["plan_version"] = dec.plan_version
    row["plan_age_seconds"] = dec.plan_age_seconds
    row["plan_actionable"] = bool(dec.plan_actionable)
    row["plan_invalid_reason"] = dec.plan_invalid_reason or unusable
    row["followed"] = bool(tracked)
    # §14 — the third thing, kept apart from the idea and the vehicle: what was
    # actually done. Nothing here is an order; the route is the shadow journal.
    row["execution"] = {
        "followed": bool(tracked),
        "entry": float(dec.current_premium) if dec.current_premium else None,
        "stop_loss": float(dec.stop_loss) if dec.stop_loss is not None else None,
        "targets": {"T1": dec.target1, "T2": dec.target2, "T3": dec.target3},
        "route": "SHADOW_JOURNAL",
        "order_placed": False,
    }
    # §13 — a shadow A+ label over the evidence already on this row. Recorded
    # beside the call, never in front of it: no gate, size or route reads it.
    if settings.a_plus_shadow:
        grade = a_plus_shadow.classify(
            instrument,
            tradability_row=trad,
            score=dec.trade_score,
            plan_actionable=row["plan_actionable"],
            has_leg=bool(dec.recommended_option),
            delta=float(quote.delta) if quote and quote.delta is not None else None,
            family=row["family"],
            entry_state=(eq or {}).get("state"),
        )
        a_plus_shadow.record(grade, _session(now))
        row["a_plus_shadow"] = {
            "label": grade["label"],
            "reasons": grade["reasons"],
            "checks": grade["checks"],
            "research_only": True,
        }
    if not tracked:
        row["outcome"] = NO_ENTRY
        row["not_followed_reason"] = (
            unusable if unusable is not None
            else None if not called_buy else
            "NO_LEVELS" if (dec.stop_loss is None or dec.target1 is None)
            else "NO_LEG_OR_PREMIUM"
        )
    _append(journal_path(), row)
    # Tell the lifecycle ledger the journal has this call, and whether it is
    # being followed to an outcome. Without this a call present in Reports but
    # absent from the dashboard cannot be told from one the dashboard refused.
    signal_visibility.mark_journalled(
        instrument, dec, signal_id, bool(tracked),
        row.get("not_followed_reason"), now=now,
    )

    if tracked:
        entry = float(dec.current_premium)
        _open[signal_id] = {
            "signal_id": signal_id,
            # Carried so the resolution can be matched back to the market idea it
            # came from, which is what §5 pairs the two vehicles on.
            "market_signal_id": row["market_signal_id"],
            "episode_id": row["episode_id"],
            "global_signal_id": dec.global_signal_id,
            "family": row["family"],
            "vehicle_selection": row["vehicle_selection"],
            # §13 label as it stood at the call, so the report can ask what the
            # A+ set actually did without re-deriving it after the fact.
            "a_plus_shadow": (row.get("a_plus_shadow") or {}).get("label"),
            "instrument": instrument,
            "symbol": dec.recommended_option,
            "session": _session(now),
            "ts": int(now),
            "entry": entry,
            "stop": float(dec.stop_loss),
            # Positive by construction: an entry at or below its stop is refused
            # above, so this never becomes the 1e-9 that produced R in the
            # hundreds of millions.
            "risk": entry - float(dec.stop_loss),
            "targets": {T1: dec.target1, T2: dec.target2, T3: dec.target3},
            "peak": entry, "peak_ts": int(now),
            "trough": entry, "trough_ts": int(now),
            "hit": {},
            "sequence": [],
            "first_event": None,
            # The market read, kept apart from the vehicle: direction is what the
            # engine claimed about the underlying, the premium is only how that
            # claim was expressed.
            "direction": "BEARISH" if (dec.vehicle or "").upper() == "PE" else "BULLISH",
            "underlying_entry": float(price) if price else None,
            "underlying_last": float(price) if price else None,
            "underlying_best": float(price) if price else None,
            "underlying_worst": float(price) if price else None,
            # Phase 12 §6: the path facts the five direction definitions need,
            # latched on first crossing so "which came first" stays answerable.
            "direction_marks": direction_attribution.marks_template(
                ind.atr, price, dec.underlying_stop,
                "BEARISH" if (dec.vehicle or "").upper() == "PE" else "BULLISH"),
            # Phase 12 §7: the vehicle facts as they stood when the call was
            # made. Read back at resolution, because a spread measured after the
            # fact is not the spread the trade was taken into.
            "vehicle_facts": _vehicle_facts(row, quote),
            # §14: the hold window the card showed, kept so the resolved row can
            # be compared against what was promised rather than against a
            # window chosen afterwards.
            "expected_holding_minutes": dec.expected_holding_minutes,
            "entry_quality": (row.get("entry_quality") or {}).get("state"),
        }


def observe_futures(instrument: str, card: FuturesSignalCard,
                    now: float | None = None) -> None:
    """Journal one RESEARCH futures signal (Part 36).

    Written into the same journal file as the option calls so one query answers
    "what did the system say today", but tagged ``market=FUTURES`` and
    ``signal_vehicle=FUTURES`` so the two can never be summed by accident: a
    futures result is in index points and an option result is in premium, and
    adding them would be meaningless.

    A futures row is **never followed to an outcome here**. It carries
    ``not_followed_reason=RESEARCH_ONLY``, which is one of the reasons Part 28
    exempts from needing a dashboard-to-book match, because there is no order
    route for this vehicle at all. Best-effort: a journal must never break a
    tick.
    """
    if not settings.signal_journal_log:
        return
    now = time.time() if now is None else now
    try:
        with _LOCK:
            key = f"FUT|{card.contract or instrument}|{card.direction or 'NONE'}|{card.status}"
            last = _last_key.get(f"FUT::{instrument}")
            if last is not None and last[0] == key and now - last[1] < _REPEAT_SEC:
                return
            _last_key[f"FUT::{instrument}"] = (key, now)
            # Remembered so an option row can state what the other vehicle was
            # offering on the same idea (§14). Facts as quoted; never a choice.
            _last_futures[instrument] = {
                "ts": int(now),
                "time_ist": _ist(now),
                "status": card.status,
                "direction": card.direction,
                "contract": card.contract,
                "expiry": card.expiry,
                "days_to_expiry": card.days_to_expiry,
                "rolled": bool(card.rolled),
                "entry": card.entry,
                "stop": card.stop,
                "targets": {"T1": card.target1, "T2": card.target2,
                            "T3": card.target3},
                "spread_points": card.spread_points,
                "risk_points": card.risk_points,
            }
            spec = REGISTRY.get(instrument)
            row = {
                "signal_id": f"{instrument}-FUT-{int(now * 1000)}-{card.signal}",
                "episode_id": card.episode_id,
                "global_signal_id": card.global_signal_id,
                "futures_signal_id": card.futures_signal_id,
                # §14 — the same market-idea id the option row carries, so both
                # expressions of one idea can be read together without matching
                # on instrument and timestamp.
                "market_signal_id": card.global_signal_id,
                "family": instrument_family.family(instrument),
                "market": "FUTURES",
                "recorded_at": _ist(now),
                "board_action": card.signal,
                "position_signal": None,
                "instrument": instrument,
                "exchange": card.exchange or (spec.exchange if spec else UNAVAILABLE),
                "lot_size": card.lot_size,
                "vehicle": "FUTURES",
                "signal_vehicle": "FUTURES",
                "option_type": None,
                "strike": None,
                "expiry": card.expiry,
                "tradingsymbol": card.contract,
                "moneyness": None,
                # Points, not premium. Named ``premium`` only because every
                # existing report reads that field as "the price of the thing
                # traded"; the market/vehicle tags are what stop the two being
                # mixed.
                "premium": card.price,
                "session": _session(now),
                "time_ist": _ist(now),
                "ts": int(now),
                "signal_info": {
                    "signal_score": card.signal_score,
                    "setup_type": card.setup_type,
                    "reason_text": card.reason,
                    "direction": card.direction,
                    "status": card.status,
                    "checks": [c.model_dump(mode="json") for c in card.checks],
                },
                "volatility": {
                    "atr": card.atr, "atr_pct": card.atr_pct, "adx": card.adx,
                    "regime": card.regime,
                },
                "entry_plan": {
                    "unit": "INDEX_POINTS",
                    "entry_price": card.entry,
                    "entry_zone_low": card.entry_zone_low,
                    "entry_zone_high": card.entry_zone_high,
                    "stop_loss": card.stop,
                    "target1": card.target1,
                    "target2": card.target2,
                    "target3": card.target3,
                    "risk_points": card.risk_points,
                    "reward_risk_t1": card.reward_risk_t1,
                    "cost_to_risk_pct": card.cost_to_risk_pct,
                },
                "plan_state": card.plan_state,
                "plan_version": card.plan_version,
                "plan_age_seconds": card.plan_age_seconds,
                "plan_actionable": card.status == "VALID_FUTURES_PLAN",
                "plan_invalid_reason": card.invalid_reason,
                "followed": False,
                "outcome": NO_ENTRY,
                "not_followed_reason": "RESEARCH_ONLY",
                "research_only": True,
                "immutable": True,
                "note": ("a research record of the futures plan; this vehicle has "
                         "no order path, paper or live"),
            }
            _append(journal_path(), row)
    except Exception:
        return


def _follow(instrument: str, chain: list[OptionQuote], now: float,
            underlying: float | None = None) -> None:
    """Advance every open signal on this instrument against the live premium.

    The underlying is tracked beside the premium so the market read and the
    chosen vehicle can be judged separately: an option can lose on a move that
    went the right way, and calling that a wrong direction hides the real fault.
    """
    prices = {q.symbol: float(q.premium) for q in chain if q.premium}
    for sid, st in list(_open.items()):
        if st["instrument"] != instrument:
            continue
        if underlying:
            st["underlying_last"] = float(underlying)
            if st["underlying_best"] is None:
                st["underlying_best"] = float(underlying)
            elif st["direction"] == "BEARISH":
                st["underlying_best"] = min(st["underlying_best"], float(underlying))
            else:
                st["underlying_best"] = max(st["underlying_best"], float(underlying))
            if st.get("underlying_worst") is None:
                st["underlying_worst"] = float(underlying)
            elif st["direction"] == "BEARISH":
                st["underlying_worst"] = max(st["underlying_worst"], float(underlying))
            else:
                st["underlying_worst"] = min(st["underlying_worst"], float(underlying))
            marks = st.get("direction_marks")
            if marks is not None and st.get("underlying_entry"):
                direction_attribution.observe_underlying(
                    marks, float(underlying), float(st["underlying_entry"]),
                    now - st["ts"])
        prem = prices.get(st["symbol"])
        if prem is not None:
            if prem > st["peak"]:
                st["peak"], st["peak_ts"] = prem, int(now)
            if prem < st["trough"]:
                st["trough"], st["trough_ts"] = prem, int(now)
            for name in (T1, T2, T3):
                level = st["targets"].get(name)
                if level is None or name in st["hit"]:
                    continue
                if prem >= float(level):
                    _milestone(st, name, prem, now)
            stop = st["stop"]
            if STOP not in st["hit"] and prem <= stop:
                _milestone(st, STOP, prem, now)
                _resolve(sid, st, prem, now, STOP)
                continue
            if T3 in st["hit"]:
                _resolve(sid, st, prem, now, T3)
                continue
        if now - st["ts"] >= _FOLLOW_SEC:
            _resolve(sid, st, prem, now, TIMEOUT)
            continue
        if _session(now) != st["session"]:
            _resolve(sid, st, prem, now, EXPIRED)


def _hold_timing(st: dict, now: float, outcome: str) -> dict:
    """How long this call took, milestone by milestone (§14).

    Every figure comes from a latched milestone, so an unreached target reports
    null instead of the follow window, and ``exit_policy`` names the rule that
    closed the follow rather than describing the price. Nothing here changes an
    exit: the follow window and the stop are the frozen ones.
    """
    def mins(name: str) -> float | None:
        hit = st["hit"].get(name)
        return hit["minutes_from_signal"] if hit else None

    held = round((now - st["ts"]) / 60.0, 1)
    expected = st.get("expected_holding_minutes")
    policy = {STOP: "STOP_HIT", T3: "FINAL_TARGET_HIT",
              TIMEOUT: "FOLLOW_WINDOW_ELAPSED",
              EXPIRED: "SESSION_ENDED"}.get(outcome, "UNRESOLVED")
    return {
        "hold_minutes": held,
        "expected_holding_minutes": expected,
        "held_beyond_expected": (held > float(expected)
                                 if expected else None),
        "minutes_to_t1": mins(T1),
        "minutes_to_t2": mins(T2),
        "minutes_to_t3": mins(T3),
        "minutes_to_stop": mins(STOP),
        "follow_window_minutes": round(_FOLLOW_SEC / 60.0, 1),
        "time_stop_state": ("TIMED_OUT" if outcome == TIMEOUT else
                            "WITHIN_WINDOW"),
        "exit_policy": policy,
        "entry_quality": st.get("entry_quality"),
        "timing_research_only": True,
    }


def _milestone(st: dict, name: str, prem: float, now: float) -> None:
    mins = round((now - st["ts"]) / 60.0, 1)
    st["hit"][name] = {"ts": int(now), "time_ist": _ist(now),
                       "minutes_from_signal": mins, "premium": prem,
                       "points": round(prem - st["entry"], 2),
                       "pct": round(100.0 * (prem - st["entry"]) / st["entry"], 2),
                       "mfe_at_reach": round(st["peak"] - st["entry"], 2)}
    st["sequence"].append(name)
    if st["first_event"] is None:
        st["first_event"] = name
    _append(outcomes_path(), {
        "event": name, "signal_id": st["signal_id"],
        "instrument": st["instrument"], "symbol": st["symbol"],
        "time_ist": _ist(now), "ts": int(now),
        "premium": prem, "minutes_from_signal": mins,
    })


DIRECTION_FAILURE = "DIRECTION_FAILURE"
VEHICLE_FAILURE = "VEHICLE_FAILURE"
NO_FAILURE = "NONE"
UNKNOWN_FAILURE = "UNKNOWN"


def _market_vs_vehicle(st: dict, final: float, entry: float) -> dict:
    """Was the market read right, and was the chosen vehicle right? (Part 24)

    Two separate questions, so a losing trade can be attributed. The underlying
    moving the called way while the premium still lost is a VEHICLE_FAILURE — the
    spread, the strike or the decay — not a wrong call on the market. Execution
    failures are not decided here: a signal that never reached a fill has no
    resolution to attribute, and the lifecycle ledger records that stage instead.
    """
    u_entry = st.get("underlying_entry")
    u_final = st.get("underlying_last")
    u_best = st.get("underlying_best")
    sign = -1.0 if st.get("direction") == "BEARISH" else 1.0
    vehicle_won = final > entry
    if not u_entry or u_final is None or u_best is None:
        return {
            "market_signal_correct": None,
            "vehicle_signal_correct": vehicle_won,
            "failure_kind": NO_FAILURE if vehicle_won else UNKNOWN_FAILURE,
            "market_move_points": None,
            "market_best_move_points": None,
            "attribution_note": "the underlying was not recorded for this signal",
        }
    move = round(sign * (float(u_final) - float(u_entry)), 2)
    best = round(sign * (float(u_best) - float(u_entry)), 2)
    market_right = best > 0
    if vehicle_won:
        kind = NO_FAILURE
    elif market_right:
        kind = VEHICLE_FAILURE
    else:
        kind = DIRECTION_FAILURE
    return {
        "market_signal_correct": market_right,
        "vehicle_signal_correct": vehicle_won,
        "failure_kind": kind,
        # Signed in the direction that was called, so a positive number always
        # means the market did what the call said.
        "market_move_points": move,
        "market_best_move_points": best,
        "attribution_note": (
            "MARKET_SIGNAL and VEHICLE_SIGNAL are judged separately: the "
            "underlying answers the direction, the premium answers the vehicle. "
            "market_signal_correct is definition A (any favourable move) and is "
            "a per-row fact only — aggregating it into a directional accuracy "
            "figure is what Phase 12 §6 retired; use direction_attribution"
        ),
    }


def _direction_verdicts(st: dict) -> dict:
    """The §6 definitions for this signal, with the path they were read from."""
    marks = st.get("direction_marks")
    if not marks:
        return {name: None for name in direction_attribution.DEFINITIONS}
    u_entry = st.get("underlying_entry")
    u_final = st.get("underlying_last")
    finish = (None if not u_entry or u_final is None
              else float(marks.get("sign") or 1.0) * (float(u_final) - float(u_entry)))
    out = dict(direction_attribution.verdicts(marks, finish))
    out["path"] = {
        "atr": marks.get("atr"),
        "stop_distance": marks.get("stop_distance"),
        "fav_half_atr_sec": marks.get("fav_half_atr_sec"),
        "fav_one_atr_sec": marks.get("fav_one_atr_sec"),
        "adverse_atr_sec": marks.get("adverse_atr_sec"),
        "target_1r_sec": marks.get("target_1r_sec"),
        "stop_sec": marks.get("stop_sec"),
        "finish_move_points": None if finish is None else round(finish, 2),
    }
    return out


def _failure_attribution(st: dict, realized: float | None, mfe_r: float | None,
                         verdicts: dict) -> dict:
    """§7 — the six-way attribution for this resolution."""
    facts = dict(st.get("vehicle_facts") or {})
    u_entry = st.get("underlying_entry")
    u_best = st.get("underlying_best")
    fav = None
    if u_entry and u_best is not None:
        sign = -1.0 if st.get("direction") == "BEARISH" else 1.0
        fav = round(sign * (float(u_best) - float(u_entry)), 2)
    facts.update({
        "realized_r": realized,
        "mfe_r": mfe_r,
        "underlying_recorded": u_entry is not None and st.get("underlying_last") is not None,
        "underlying_favorable_move": fav,
    })
    # A followed signal was entered by construction, so this resolution is not an
    # execution failure; the ones that never reached a fill are held by the
    # lifecycle ledger, which records the stage they stopped at.
    return failure_attribution.classify(verdicts=verdicts, trade=facts, filled=True)


def _resolve(sid: str, st: dict, prem: float | None, now: float,
             outcome: str) -> None:
    risk = st["risk"]
    entry = st["entry"]
    first = st["first_event"]
    order = (STOP_FIRST if first == STOP else
             TARGET_FIRST if first in (T1, T2, T3) else FIRST_UNKNOWN)
    mfe = round(st["peak"] - entry, 2)
    mae = round(st["trough"] - entry, 2)
    final = prem if prem is not None else st["peak"]

    # R is points divided by the risk taken, so it only exists when a real risk
    # was taken. A plan with a stop at or above its entry risks nothing and is
    # left with R unavailable, never with the astronomical number an epsilon
    # denominator produces.
    def _r(points: float) -> float | None:
        return round(points / risk, 3) if risk > 0 else None

    realized = _r(final - entry)
    rec = {
        "event": "RESOLVED",
        "signal_id": sid,
        # §5/§14 — the market idea this outcome belongs to, the vehicle it was
        # expressed in, and the unit it is measured in. Premium results and index
        # point results are never added together, so both carry their unit.
        "market_signal_id": st.get("market_signal_id"),
        "episode_id": st.get("episode_id"),
        "global_signal_id": st.get("global_signal_id"),
        "vehicle": instrument_family.OPTIONS,
        "unit": "PREMIUM",
        "family": st.get("family"),
        "direction": st.get("direction"),
        "vehicle_selection": st.get("vehicle_selection"),
        "a_plus_shadow": st.get("a_plus_shadow"),
        "instrument": st["instrument"],
        "symbol": st["symbol"],
        "session": st["session"],
        "signal_ts": st["ts"],
        "signal_time_ist": _ist(st["ts"]),
        "resolved_ts": int(now),
        "resolved_time_ist": _ist(now),
        "outcome": outcome,
        # §12 — which arrived first, taken from the event order and never from the
        # eventual best price. A later target after a stop is not a win.
        "first_event": first,
        "order": order,
        "target_sequence": " -> ".join(st["sequence"]) or "NONE",
        "entry": entry,
        "stop": st["stop"],
        "risk_points": round(risk, 2),
        "final_premium": final,
        "targets": st["targets"],
        "targets_reached": {k: v for k, v in st["hit"].items() if k != STOP},
        "stop_event": st["hit"].get(STOP),
        "mfe_points": mfe,
        "mfe_r": _r(mfe),
        "mfe_pct": round(100.0 * mfe / entry, 2),
        "minutes_to_mfe": round((st["peak_ts"] - st["ts"]) / 60.0, 1),
        "mae_points": mae,
        "mae_r": _r(mae),
        "mae_pct": round(100.0 * mae / entry, 2),
        "minutes_to_mae": round((st["trough_ts"] - st["ts"]) / 60.0, 1),
        "realized_r": realized,
        "r_unavailable_reason": None if risk > 0 else "NON_POSITIVE_RISK",
        "mfe_capture": (round(realized / (mfe / risk), 3)
                        if mfe > 0 and risk > 0 and realized is not None
                        else None),
        "give_back_r": (round(mfe / risk - realized, 3)
                        if mfe > 0 and risk > 0 and realized is not None
                        else None),
        "minutes_to_resolution": round((now - st["ts"]) / 60.0, 1),
        # §14 — the timing fields as their own block, so "how long does this hold"
        # is answerable from the ledger and not only from an ad-hoc analysis. Only
        # milestones the follow actually recorded appear; an unreached target is
        # null rather than the follow window.
        **_hold_timing(st, now, outcome),
        **_market_vs_vehicle(st, final, entry),
        # Phase 12 §6 — five named direction definitions instead of one
        # unfalsifiable "the underlying ever moved our way" figure.
        "direction_attribution": _direction_verdicts(st),
        # Phase 12 §7 — six-way attribution with the evidence it was decided on.
        # ``failure_kind`` above stays as the Part 24 two-way label so existing
        # reports keep their meaning; this block is the one to read.
        "failure_attribution": _failure_attribution(
            st, realized, _r(mfe), _direction_verdicts(st)),
        "gross_only": ("realized_r is premium-to-premium; costs and the book are "
                       "applied by the research layer, not here"),
    }
    _append(outcomes_path(), rec)
    _open.pop(sid, None)


def open_count() -> int:
    """How many signals are currently being followed. Diagnostics only."""
    return len(_open)


def read_journal(limit: int | None = 500,
                 session: str | None = None) -> list[dict]:
    """Newest journalled calls first. Read-only; the file is never modified.

    ``limit=None`` returns the whole session, which reconciliation needs: a tail
    read makes an incomplete report look complete (Phase 12 §2).
    """
    rows = _read(journal_path())
    if session:
        rows = [r for r in rows if r.get("session") == session]
    if limit is not None:
        rows = rows[-limit:]
    return rows[::-1]


def read_outcomes(limit: int = 2000) -> list[dict]:
    return _read(outcomes_path())[-limit:][::-1]


def resolutions(session: str | None = None) -> list[dict]:
    rows = [r for r in _read(outcomes_path()) if r.get("event") == "RESOLVED"]
    if session:
        rows = [r for r in rows if r.get("session") == session]
    return rows


@dataclass
class _Ledger:
    """What has already been parsed out of one append-only file."""

    size: int
    mtime_ns: int
    offset: int
    head: bytes = b""
    rows: list[dict] = field(default_factory=list)


_read_cache: dict[str, _Ledger] = {}
# Enough of the file's start to notice it was replaced rather than appended to:
# a rotated ledger that happens to be no smaller than the old one would
# otherwise look like growth.
_HEAD_BYTES = 512


def _parse_into(rows: list[dict], blob: bytes) -> int:
    """Append the complete lines of ``blob`` to ``rows``; return bytes consumed.

    A tick can be halfway through writing its line while a board is reading, so a
    trailing fragment is left unconsumed and parsed on the next read instead of
    being dropped as malformed.
    """
    cut = blob.rfind(b"\n") + 1
    for line in blob[:cut].splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return cut


def _read(path: str) -> list[dict]:
    """Every record of an append-only ledger, parsed once per appended line.

    One board request scans the journal several times (the rows, the without-plan
    count, the session list, the statistics) and the file only ever grows, so
    re-parsing tens of megabytes per scan is what made the boards slow. The parse
    is kept between calls, and when the file has grown only the newly appended
    bytes are parsed — a live tick costs one line, not the whole ledger. Any other
    change (the file shrank, was rotated or replaced) falls back to a full parse,
    so a rewritten file is never served from a stale cache.

    The returned rows are shared with the cache and every caller here treats them
    as read-only. Copy a row (``{**row}``) before modifying it.
    """
    try:
        st = os.stat(path)
    except OSError:
        _read_cache.pop(path, None)
        return []
    hit = _read_cache.get(path)
    if hit is not None:
        if hit.size == st.st_size and hit.mtime_ns == st.st_mtime_ns:
            return hit.rows
        if st.st_size >= hit.size:
            with open(path, "rb") as fh:
                head = fh.read(_HEAD_BYTES)
                if head == hit.head:
                    fh.seek(hit.offset)
                    hit.offset += _parse_into(hit.rows, fh.read())
                    hit.size, hit.mtime_ns = st.st_size, st.st_mtime_ns
                    return hit.rows
    rows: list[dict] = []
    with open(path, "rb") as fh:
        blob = fh.read()
    offset = _parse_into(rows, blob)
    _read_cache[path] = _Ledger(st.st_size, st.st_mtime_ns, offset,
                                blob[:_HEAD_BYTES], rows)
    return rows


def reset_for_tests() -> None:
    """Clear process state. Used by the smoke; never called by the app."""
    _open.clear()
    _read_cache.clear()
    _last_key.clear()
    _vol_history.clear()
    _last_futures.clear()
