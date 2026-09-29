"""Take one CAS sample: the ladder, both sides, at a measured instant — §3, §4, §5.

Three rules from the spec are enforced structurally here rather than by
convention.

**Nothing is fabricated.** A rung with no book on a side stores ``None`` on that
side. There is no nearest-strike substitution, no last-price stand-in for a
missing bid, and no carrying forward of the previous sample. A CAS dataset with
invented quotes would be worse than no dataset, because it would look complete.

**Both sides come from the same snapshot.** The rung is the unit, so a CE and its
PE are either quoted at one instant or the pair is visibly incomplete. §4 forbids
inferring one side from the other and this is the only way to keep that promise
after the data is on disk.

**The delay is measured, not assumed.** ``intent_ts`` is the instant we meant to
sample, ``capture_ts`` is when the snapshot happened, the feed's own timestamp is
kept when it gives one, and the difference is stored in milliseconds on every
row. Phase 17 matched 48 of 3,448 legs to a book because nobody was measuring
this until afterwards.
"""
from __future__ import annotations

import time
import uuid

from app.config import settings
from app.market.instruments import get_spec
from app.models import OptionQuote, OptionType
from app.research.phase17 import capture as p17capture
from app.research.phase17 import schema as p17schema
from app.research.phase18 import quality, schema, session

# Default research universe. Indices only: the 5-year study measured a 0.93-point
# median move on single stocks, which cannot pay for a round trip, and CAS does
# not change that arithmetic.
DEFAULT_UNIVERSE: tuple[str, ...] = (
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX",
)


def universe() -> frozenset[str]:
    raw = settings.phase18_universe or ""
    names = {s.strip().upper() for s in raw.split(",") if s.strip()}
    return frozenset(names or DEFAULT_UNIVERSE)


def eligible(instrument: str, ts: float) -> tuple[bool, str]:
    """Should this instrument be sampled at this instant?"""
    if not settings.phase18_cas_capture:
        return False, "phase18 capture disabled"
    name = (instrument or "").upper()
    if name not in universe():
        return False, f"{name} not in the CAS research universe"
    if not session.in_window(ts):
        return False, f"outside the CAS window ({session.state(ts)})"
    if session.mechanism(name) == session.AUCTION_NONE:
        return False, f"no CAS mechanism applies to {name}"
    return True, "eligible"


def _rung_targets(steps: int, far: int, very_far: int) -> list[tuple[str, int]]:
    out = [
        (schema.ATM, 0), (schema.OTM_1, 1), (schema.OTM_2, 2), (schema.OTM_3, 3),
    ]
    out = [(r, n) for r, n in out if n <= max(0, steps)]
    if far > 0:
        out.append((schema.FAR_OTM, far))
    if very_far > far:
        out.append((schema.VERY_FAR_OTM, very_far))
    return out


def _nearest(chain: list[OptionQuote], side: OptionType, strike: float) -> OptionQuote | None:
    """The quote at this strike, or ``None``.

    Deliberately exact rather than nearest-available: substituting a neighbouring
    strike would make the ladder look complete while the rung labels lied about
    the distance they measure. Tolerance is a tenth of a point for float noise.
    """
    best: OptionQuote | None = None
    for q in chain:
        if q.option_type != side:
            continue
        if abs(float(q.strike) - strike) <= 0.1:
            best = q
            break
    return best


def build(
    instrument: str,
    chain: list[OptionQuote],
    *,
    spot: float | None,
    intent_ts: float,
    capture_ts: float | None = None,
    exchange_ts: float | None = None,
    receive_ts: float | None = None,
    expiry: str | None = None,
    days_to_expiry: int | None = None,
    source: str = p17capture.quality.UNKNOWN_SOURCE,
    quote_age_ms: float | None = None,
    market_open: bool | None = None,
    futures_quote: p17schema.Quote | None = None,
) -> schema.CasObservation | None:
    """One CAS observation, or ``None`` when there is nothing real to record."""
    name = (instrument or "").upper()
    ok, _why = eligible(name, intent_ts)
    if not ok:
        return None
    cap_ts = float(capture_ts if capture_ts is not None else time.time())
    delta_ms = (cap_ts - float(intent_ts)) * 1000.0

    spec = get_spec(name)
    step = float(spec.strike_step) if spec is not None else None
    atm = None
    if spot is not None and step:
        atm = round(float(spot) / step) * step

    desc = session.describe(
        intent_ts, name, market_open=market_open, expiry=expiry,
        days_to_expiry=days_to_expiry,
    )

    ladder: list[schema.Rung] = []
    if atm is not None and step:
        targets = _rung_targets(
            int(settings.phase18_ladder_steps),
            int(settings.phase18_far_otm_steps),
            int(settings.phase18_very_far_otm_steps),
        )
        for rung, n in targets:
            # OTM is away from spot in each side's own direction: calls above,
            # puts below. One rung therefore holds two different strikes for
            # n > 0, which is what "OTM_2 on both sides" has to mean.
            ce_strike = atm + n * step
            pe_strike = atm - n * step
            ce_q = _nearest(chain, OptionType.CALL, ce_strike)
            pe_q = _nearest(chain, OptionType.PUT, pe_strike)
            if ce_q is None and pe_q is None:
                continue
            ladder.append(schema.Rung(
                rung=rung,
                steps=n,
                strike=ce_strike if n == 0 else None,
                ce=_quote(
                    name, ce_q, schema.CE, spot=spot, atm=atm, step=step,
                    expiry=expiry, dte=days_to_expiry, source=source,
                    quote_age_ms=quote_age_ms, delta_ms=delta_ms, cap_ts=cap_ts,
                ),
                pe=_quote(
                    name, pe_q, schema.PE, spot=spot, atm=atm, step=step,
                    expiry=expiry, dte=days_to_expiry, source=source,
                    quote_age_ms=quote_age_ms, delta_ms=delta_ms, cap_ts=cap_ts,
                ),
            ))

    if not ladder and spot is None:
        return None

    # The observation's own quality is the best rung it managed to record: if any
    # pair arrived on time with a book, the sample is that good. Individual
    # quotes keep their own state and the measured tables use those.
    states = [q.data_quality for q in
              (r.side(s) for r in ladder for s in schema.SIDES) if q is not None]
    obs_quality = (
        min(states, key=lambda s: quality.STATES.index(s))
        if states else quality.MISSING
    )

    return schema.CasObservation(
        observation_id=uuid.uuid4().hex[:16],
        session=desc["session_date"],
        instrument=name,
        exchange=desc["exchange"],
        intent_ts=float(intent_ts),
        capture_ts=cap_ts,
        exchange_ts=exchange_ts,
        receive_ts=receive_ts if receive_ts is not None else cap_ts,
        timestamp_delta_ms=round(delta_ms, 1),
        cas_state=desc["cas_state"],
        sub_window=desc["sub_window"],
        mark=session.nearest_mark(intent_ts),
        remaining_seconds=desc["remaining_seconds"],
        is_cas_day=bool(desc["is_cas_day"]),
        day_source=str(desc["day_source"]),
        cas_mechanism=str(desc["cas_mechanism"]),
        is_expiry_day=desc["is_expiry_day"],
        expiry=expiry,
        days_to_expiry=days_to_expiry,
        expiry_class=desc["expiry_class"],
        underlying=float(spot) if spot is not None else None,
        atm_strike=atm,
        strike_step=step,
        source=source,
        quote_age_ms=quote_age_ms,
        data_quality=obs_quality,
        ladder=ladder,
        futures=futures_quote,
    )


def _quote(
    instrument: str,
    q: OptionQuote | None,
    side: str,
    *,
    spot: float | None,
    atm: float | None,
    step: float | None,
    expiry: str | None,
    dte: int | None,
    source: str,
    quote_age_ms: float | None,
    delta_ms: float,
    cap_ts: float,
) -> p17schema.Quote | None:
    """One side of one rung. ``None`` in, ``None`` out — never a placeholder."""
    if q is None:
        return None
    has_book = quality.two_sided(q.bid, q.ask)
    state = quality.classify(
        intent_to_snapshot_ms=delta_ms,
        quote_age_ms=quote_age_ms,
        has_book=has_book,
    )
    steps = (
        None if atm is None or not step
        else int(round((float(q.strike) - atm) / step))
    )
    return p17schema.Quote(
        instrument=instrument,
        vehicle=side,
        symbol=q.symbol,
        strike=float(q.strike),
        expiry=expiry,
        days_to_expiry=dte,
        expiry_class=session.expiry_class(dte),
        bid=q.bid,
        ask=q.ask,
        premium=q.premium,
        oi=q.oi,
        oi_change=q.oi_change,
        volume=q.volume,
        iv=q.iv,
        delta=q.delta,
        gamma=q.gamma,
        theta=q.theta,
        underlying_price=spot,
        atm_strike=atm,
        moneyness=p17capture.moneyness(side, float(q.strike), spot),
        distance_from_atm=(
            None if atm is None else round(float(q.strike) - atm, 2)
        ),
        strike_steps_from_atm=steps,
        delta_band=p17capture.delta_band(q.delta),
        source=source,
        feed_age_ms=quote_age_ms,
        signal_to_snapshot_ms=round(delta_ms, 1),
        snapshot_ts=cap_ts,
        data_quality=state,
    )
