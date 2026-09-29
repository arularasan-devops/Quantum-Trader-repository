"""Capture the option book AT THE SIGNAL INSTANT — Phase 17 §3, §4, §5, §14.

The whole phase turns on this module, and on one fact about where it is called
from. The existing research capture writes the chain against the last COMPLETED
one-minute bar, which is up to 60 seconds behind the decision; that is not a
detail, it is the reason the previous option study had 48 usable rows out of
3,448 and a 60-second median gap. Capturing from the tick that has just computed
the decision fixes it for free: the chain is already in memory, the snapshot is
the same object the engine read, so the timestamp distance is milliseconds and no
extra broker call is made. Rate limits are not a constraint here because nothing
is fetched.

What one call records:

* the SELECTED side — the contract the engine named;
* the OPPOSITE side at the same strike and the same instant, so §6's CE/PE
  question is answerable without ever inferring one side from another timestamp;
* a strike WINDOW around ATM (both types), which is what makes §14's strike
  distance study possible while costing nothing — those quotes are in the chain
  already;
* the futures book when the provider carries one;
* the plan and the market context as published, never recomputed.

Eligibility is deliberately wide (every BUY, WAIT and refusal on an allowed
instrument) because §5 wants counterfactuals, and as of Phase 20 it spans the
whole research-admitted universe rather than the five index roots: indexes, the
MCX commodities and the optionable equities.

Single stocks were excluded here on a measured finding — 0.93 points of median
favourable travel against a ~2.4-point round trip on a Rs 150 premium — but that
finding came from the UNDERLYING pool, so it is evidence about direction and not
about the vehicle. Capturing them is how it gets tested on the book itself; the
prior still ranks them last, and a stock that cannot pay its spread will show it
here rather than being assumed.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from app.analysis import instrument_family as fam
from app.config import settings
from app.models import Candle, Decision, IndicatorSnapshot, OptionQuote
from app.research.phase17 import quality, schema
from app.research.phase20 import universe as p20universe

_IST_OFFSET_SEC = 19800

# Instruments whose vehicle evidence is worth the storage: the Phase 20
# research-admitted universe, so adding an optionable name to the registry adds it
# to research instead of leaving it permanently unmeasured. Which of these produce
# rows is still bounded by what the app is actually streaming. Overridable by
# settings, never narrowed silently.
INDEX_ONLY_UNIVERSE: tuple[str, ...] = (
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX",
)

# Strikes either side of ATM to record. 2 gives 5 strikes x 2 types = 10 quotes
# per observation, enough for an ATM/ITM/OTM comparison and small enough that a
# session's file stays in the tens of megabytes.
WINDOW_STEPS = 2

_DELTA_BANDS: tuple[tuple[str, float, float], ...] = (
    ("D_0_15", 0.0, 0.15),
    ("D_15_35", 0.15, 0.35),
    ("D_35_55", 0.35, 0.55),
    ("D_55_75", 0.55, 0.75),
    ("D_75_100", 0.75, 1.01),
)


def default_universe() -> tuple[str, ...]:
    """Every research-admitted optionable instrument."""
    return p20universe.capture_universe()


def universe() -> tuple[str, ...]:
    """Instruments eligible for vehicle capture."""
    raw = (settings.phase17_universe or "").strip()
    if not raw:
        return default_universe()
    names = tuple(
        n.strip().upper() for n in raw.replace(";", ",").split(",") if n.strip()
    )
    return names or default_universe()


def eligible(instrument: str) -> bool:
    return (instrument or "").upper() in universe()


def ist_parts(ts: float) -> tuple[str, str, int]:
    """(session date, weekday name, minutes since 09:15 IST) for a timestamp."""
    dt = datetime.fromtimestamp(float(ts) + _IST_OFFSET_SEC, tz=timezone.utc)
    minute = (dt.hour * 60 + dt.minute) - (9 * 60 + 15)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%A").upper(), minute


def session_period(minute: int | None) -> str | None:
    """Named opening period. Same boundaries as the Phase 15B study, so a live
    row and a historical row land in the same bucket without a mapping table."""
    if minute is None:
        return None
    if minute < 0:
        return None
    if minute < 15:
        return "OPEN_0_15"
    if minute < 60:
        return "EARLY_15_60"
    if minute < 240:
        return "MID_60_240"
    if minute <= 375:
        return "LATE_240_375"
    return None


def delta_band(delta: float | None) -> str | None:
    if not isinstance(delta, (int, float)):
        return None
    d = abs(float(delta))
    for name, lo, hi in _DELTA_BANDS:
        if lo <= d < hi:
            return name
    return _DELTA_BANDS[-1][0]


def expiry_class(days_to_expiry: int | None) -> str | None:
    """§13. EXPIRY_DAY / PRE_EXPIRY / NON_EXPIRY from days remaining."""
    if days_to_expiry is None:
        return None
    d = int(days_to_expiry)
    if d <= 0:
        return schema.EXPIRY_DAY
    if d == 1:
        return schema.PRE_EXPIRY
    return schema.NON_EXPIRY


def atm_strike(chain: list[OptionQuote], spot: float | None) -> float | None:
    """Nearest listed strike to spot. None when either input is absent — an
    assumed ATM would silently mislabel every moneyness in the window."""
    if not chain or not isinstance(spot, (int, float)) or spot <= 0:
        return None
    strikes = sorted({float(q.strike) for q in chain if q.strike})
    if not strikes:
        return None
    return min(strikes, key=lambda s: abs(s - float(spot)))


def strike_step(chain: list[OptionQuote]) -> float | None:
    """The listed strike interval, taken as the smallest positive gap."""
    strikes = sorted({float(q.strike) for q in chain if q.strike})
    if len(strikes) < 2:
        return None
    gaps = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
    return min(gaps) if gaps else None


def moneyness(vehicle: str, strike: float | None, spot: float | None) -> str | None:
    if strike is None or not isinstance(spot, (int, float)) or spot <= 0:
        return None
    if abs(float(strike) - float(spot)) < 1e-9:
        return schema.ATM
    if vehicle == schema.CE:
        return schema.ITM if float(strike) < float(spot) else schema.OTM
    if vehicle == schema.PE:
        return schema.ITM if float(strike) > float(spot) else schema.OTM
    return None


def book_age_ms(book_ts: float | None, capture_ts: float) -> float | None:
    """How old this bid/ask was when it was captured, in milliseconds.

    ``None`` when the feed did not date the book: a provider that carries a book
    forward across depthless ticks cannot prove the quote existed at the
    decision instant, and an unproven age must stay unproven rather than
    default to zero.
    """
    if not isinstance(book_ts, (int, float)) or float(book_ts) <= 0:
        return None
    return round(max(0.0, (float(capture_ts) - float(book_ts))) * 1000.0, 3)


def to_quote(
    q: OptionQuote,
    *,
    instrument: str,
    spot: float | None,
    atm: float | None,
    step: float | None,
    expiry: str | None,
    days_to_expiry: int | None,
    signal_ts: float,
    capture_ts: float,
    source: str,
    quote_age_ms: float | None,
) -> schema.Quote:
    """One chain row, with its own measured freshness attached.

    ``moneyness`` is computed against SPOT rather than against the ATM strike:
    at a 50-point strike step the nearest strike can be 25 points away, and
    calling that contract ATM would put a genuinely in-the-money option in the
    ATM bucket for the whole strike-distance study.
    """
    vehicle = q.option_type.value if q.option_type else None
    strike = float(q.strike) if q.strike else None
    steps: int | None = None
    if strike is not None and atm is not None and step:
        steps = int(round((strike - atm) / step))
    dist = None if strike is None or atm is None else round(strike - atm, 4)
    has_book = quality.two_sided(q.bid, q.ask)
    return schema.Quote(
        instrument=instrument,
        vehicle=vehicle or "",
        symbol=q.symbol,
        strike=strike,
        expiry=expiry,
        days_to_expiry=days_to_expiry,
        expiry_class=expiry_class(days_to_expiry),
        bid=q.bid,
        ask=q.ask,
        bid_size=int(q.bid_size) if q.bid_size else None,
        ask_size=int(q.ask_size) if q.ask_size else None,
        premium=float(q.premium) if q.premium else None,
        lot_size=int(q.lot_size) if q.lot_size else None,
        oi=float(q.oi) if q.oi is not None else None,
        oi_change=float(q.oi_change) if q.oi_change is not None else None,
        volume=float(q.volume) if q.volume is not None else None,
        iv=float(q.iv) if q.iv is not None else None,
        delta=float(q.delta) if q.delta is not None else None,
        gamma=float(q.gamma) if q.gamma is not None else None,
        theta=float(q.theta) if q.theta is not None else None,
        underlying_price=float(spot) if isinstance(spot, (int, float)) else None,
        atm_strike=atm,
        moneyness=moneyness(vehicle or "", strike, spot),
        distance_from_atm=dist,
        strike_steps_from_atm=steps,
        delta_band=delta_band(q.delta),
        source=source,
        feed_age_ms=quote_age_ms,
        book_age_ms=book_age_ms(getattr(q, "book_ts", None), capture_ts),
        signal_to_snapshot_ms=round((capture_ts - signal_ts) * 1000.0, 3),
        snapshot_ts=capture_ts,
        data_quality=quality.classify(
            signal_to_snapshot_ms=(capture_ts - signal_ts) * 1000.0,
            quote_age_ms=quote_age_ms,
            has_book=has_book,
        ),
    )


def opposite_symbol(chain: list[OptionQuote], selected: OptionQuote) -> OptionQuote | None:
    """The other side at the SAME strike, from the SAME snapshot.

    Same strike rather than "the mirror-image OTM strike" because the question
    §6 asks is which side of one decision was better, and only the same strike
    holds the underlying's move constant across the pair.
    """
    want = schema.PE if selected.option_type.value == schema.CE else schema.CE
    for q in chain:
        if q.option_type.value == want and abs(float(q.strike) - float(selected.strike)) < 1e-9:
            return q
    return None


def window_quotes(
    chain: list[OptionQuote],
    *,
    atm: float | None,
    step: float | None,
    steps: int = WINDOW_STEPS,
) -> list[OptionQuote]:
    """Both types across ATM +/- ``steps`` strikes."""
    if atm is None or not step:
        return []
    lo, hi = atm - steps * step, atm + steps * step
    out = [
        q for q in chain
        if q.strike is not None and lo - 1e-9 <= float(q.strike) <= hi + 1e-9
    ]
    out.sort(key=lambda q: (float(q.strike), q.option_type.value))
    return out


def _direction(dec: Decision) -> str | None:
    if dec.vehicle == schema.CE or (dec.option_type and dec.option_type.value == schema.CE):
        return "BULLISH"
    if dec.vehicle == schema.PE or (dec.option_type and dec.option_type.value == schema.PE):
        return "BEARISH"
    if dec.htf_trend == "UP":
        return "BULLISH"
    if dec.htf_trend == "DOWN":
        return "BEARISH"
    return None


def candidate_class(dec: Decision) -> str:
    """The engine's own call, unnormalised.

    HOLD/EXIT come from a position already being open; the fresh-market read is
    what this study is about, so ``market_signal`` is preferred and the position
    signal is only a fallback.
    """
    sig = dec.market_signal or dec.signal
    val = sig.value if sig else None
    if val == "BUY":
        return schema.BUY
    if val == "WAIT":
        return schema.WAIT
    if val in ("AVOID", "NO_TRADE"):
        return schema.AVOID
    return schema.NO_SIGNAL


def _plan(dec: Decision, chain_premium: float | None) -> schema.Plan:
    entry_low = entry_high = None
    if dec.entry_range and len(dec.entry_range) == 2:
        entry_low, entry_high = float(dec.entry_range[0]), float(dec.entry_range[1])
    entry = chain_premium if chain_premium else dec.current_premium
    risk = None
    if entry is not None and dec.stop_loss is not None:
        risk = round(float(entry) - float(dec.stop_loss), 4)
    rr = None
    if risk and risk > 0 and dec.target1 is not None and entry is not None:
        rr = round((float(dec.target1) - float(entry)) / risk, 3)
    return schema.Plan(
        entry=entry,
        entry_low=entry_low,
        entry_high=entry_high,
        stop=dec.stop_loss,
        target1=dec.target1,
        target2=dec.target2,
        target3=dec.target3,
        risk=risk,
        reward_risk=rr,
        expected_move_points=dec.expected_move_points,
        expected_hold_minutes=dec.expected_holding_minutes,
        lot_size=None,
    )


def _context(
    dec: Decision,
    ind: IndicatorSnapshot | None,
    status: str | None,
    signal_ts: float,
    candles: list[Candle] | None,
) -> schema.MarketContext:
    session, weekday, minute = ist_parts(signal_ts)
    htf_align = None
    direction = _direction(dec)
    if dec.htf_trend and direction:
        want = "UP" if direction == "BULLISH" else "DOWN"
        htf_align = "WITH_HTF" if dec.htf_trend == want else (
            "AGAINST_HTF" if dec.htf_trend in ("UP", "DOWN") else "NO_HTF"
        )
    momentum = None
    vwap_side = None
    atr = dec.atr_points
    if ind is not None:
        momentum = ind.momentum
        price = dec.spot_price
        if isinstance(ind.vwap, (int, float)) and isinstance(price, (int, float)):
            vwap_side = "ABOVE_VWAP" if price >= ind.vwap else "BELOW_VWAP"
        if atr is None:
            atr = ind.atr
    return schema.MarketContext(
        direction=direction,
        regime=status,
        htf_trend=dec.htf_trend,
        htf_alignment=htf_align,
        htf_strength=dec.htf_strength,
        momentum=momentum if isinstance(momentum, (int, float)) else None,
        volatility_band=None,
        atr_points=atr if isinstance(atr, (int, float)) else None,
        vwap_side=vwap_side,
        session_minute=minute,
        session_period=session_period(minute),
        weekday=weekday,
        session=session,
        trade_score=dec.trade_score,
        confidence=dec.confidence,
        market_signal=candidate_class(dec),
    )


def observation_id(instrument: str, signal_ts: float, cls: str) -> str:
    """Milliseconds, because two candidates inside one second must be tellable
    apart — the same lesson the signal journal learned."""
    return f"{instrument}-{int(signal_ts * 1000)}-{cls}"


def build(
    instrument: str,
    dec: Decision,
    chain: list[OptionQuote],
    *,
    spot: float | None,
    ind: IndicatorSnapshot | None = None,
    status: str | None = None,
    candles: list[Candle] | None = None,
    signal_ts: float | None = None,
    capture_ts: float | None = None,
    expiry: str | None = None,
    days_to_expiry: int | None = None,
    source: str = quality.UNKNOWN_SOURCE,
    quote_age_ms: float | None = None,
    futures_quote: schema.Quote | None = None,
    window_steps: int = WINDOW_STEPS,
) -> schema.Observation | None:
    """One observation from one live tick, or ``None`` when there is nothing to
    record (no chain at all, or an ineligible instrument).

    Never raises on a partial input: a candidate with no named contract still
    produces a row against the ATM pair, because a refusal with no vehicle is
    still evidence about what the refusal avoided.
    """
    if not chain:
        return None
    signal_ts = float(signal_ts if signal_ts is not None else time.time())
    capture_ts = float(capture_ts if capture_ts is not None else time.time())
    atm = atm_strike(chain, spot)
    step = strike_step(chain)

    selected_raw: OptionQuote | None = None
    if dec.recommended_option:
        selected_raw = next(
            (q for q in chain if q.symbol == dec.recommended_option), None
        )
    if selected_raw is None and atm is not None:
        # No named contract (a WAIT or a refusal): use the ATM contract on the
        # side the read implies, so the row still measures a real vehicle. The
        # candidate_class says it was not a BUY, so this can never be read as a
        # trade the engine asked for.
        want = schema.PE if _direction(dec) == "BEARISH" else schema.CE
        selected_raw = next(
            (q for q in chain
             if q.option_type.value == want
             and abs(float(q.strike) - atm) < 1e-9),
            None,
        )
    if selected_raw is None:
        return None
    opposite_raw = opposite_symbol(chain, selected_raw)

    def conv(q: OptionQuote) -> schema.Quote:
        return to_quote(
            q,
            instrument=instrument,
            spot=spot,
            atm=atm,
            step=step,
            expiry=expiry,
            days_to_expiry=days_to_expiry,
            signal_ts=signal_ts,
            capture_ts=capture_ts,
            source=source,
            quote_age_ms=quote_age_ms,
        )

    cls = candidate_class(dec)
    sel = conv(selected_raw)
    opp = conv(opposite_raw) if opposite_raw is not None else None
    win = [conv(q) for q in window_quotes(chain, atm=atm, step=step, steps=window_steps)]

    return schema.Observation(
        observation_id=observation_id(instrument, signal_ts, cls),
        signal_ts=signal_ts,
        capture_ts=capture_ts,
        instrument=instrument,
        family=fam.family(instrument),
        candidate_class=cls,
        direction=_direction(dec),
        selected_vehicle=sel.vehicle or None,
        market_signal_id=dec.episode_id,
        global_signal_id=dec.global_signal_id,
        episode_id=dec.episode_id,
        session=ist_parts(signal_ts)[0],
        selected=sel,
        opposite=opp,
        futures=futures_quote,
        window=win,
        plan=_plan(dec, sel.premium),
        context=_context(dec, ind, status, signal_ts, candles),
    )
