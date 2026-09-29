"""Row shapes for the Phase 17 evidence store — §3, §4, §14, §33.

One reason this is a module of frozen dataclasses rather than free-form dicts:
the fields are being written now and read in three months, by which time the only
defence against a silently renamed key is a single definition both sides import.
The other reason is honesty about absence. Every optional field here is
``None``-able and nothing defaults to zero, because a zero spread, a zero premium
and a zero delta are all values that would pass a report and mean the opposite of
what they say.

``as_dict`` is the on-disk form and it is flat per quote, so the JSONL can be
read by a plain csv/pandas consumer later without a nested unpack.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields

from app.research.phase17 import quality

CE = "CE"
PE = "PE"
FUTURES = "FUTURES"
NO_TRADE = "NO_TRADE"
VEHICLES: tuple[str, ...] = (CE, PE, FUTURES, NO_TRADE)

# §14 strike distance. Bands are in strike steps from ATM, signed by moneyness of
# the option itself rather than by the underlying's direction, so an ITM call and
# an ITM put land in the same band.
ATM = "ATM"
ITM = "ITM"
OTM = "OTM"
MONEYNESS: tuple[str, ...] = (ATM, ITM, OTM)

# §13 expiry classes.
EXPIRY_DAY = "EXPIRY_DAY"
PRE_EXPIRY = "PRE_EXPIRY"
NON_EXPIRY = "NON_EXPIRY"
EXPIRY_CLASSES: tuple[str, ...] = (EXPIRY_DAY, PRE_EXPIRY, NON_EXPIRY)

# §5 candidate classes. Recorded as the engine's own words: the point of
# capturing refusals is to be able to ask later whether the refusal was right, and
# that question dies if the label is normalised away at write time.
BUY = "BUY"
WAIT = "WAIT"
AVOID = "AVOID"
NO_SIGNAL = "NO_SIGNAL"


def _num(v: float | int | None) -> float | None:
    """Keep a number, reject anything that is not finite. NaN never enters."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


@dataclass(frozen=True)
class Quote:
    """One option (or futures) book at one measured instant — §3.

    ``premium`` is the traded last price; ``mid`` is derived from the book. They
    are kept separate on purpose: an LTP from a thin strike can sit outside its
    own bid/ask, and a study that silently substituted one for the other would
    report spreads that no one could have traded.
    """

    instrument: str
    vehicle: str                       # CE / PE / FUTURES
    symbol: str | None = None
    strike: float | None = None
    expiry: str | None = None
    days_to_expiry: int | None = None
    expiry_class: str | None = None

    bid: float | None = None
    ask: float | None = None
    # Quantity resting at the top of each side, when the feed published it. A
    # spread of one paisa on one lot and the same spread on fifty are different
    # executable books, and without the sizes a later reader cannot tell which
    # one was quoted. Absent stays absent rather than becoming zero.
    bid_size: int | None = None
    ask_size: int | None = None
    premium: float | None = None

    # Contract multiplier at the capture instant, from the feed's own scrip
    # master. Recorded beside the book because a cost is a flat fee divided by
    # this quantity: a book captured without it cannot be costed later, and the
    # value published today is not necessarily the value published next series.
    lot_size: int | None = None

    oi: float | None = None
    oi_change: float | None = None
    volume: float | None = None
    iv: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None

    underlying_price: float | None = None
    atm_strike: float | None = None
    moneyness: str | None = None
    distance_from_atm: float | None = None
    strike_steps_from_atm: int | None = None
    delta_band: str | None = None

    source: str = quality.UNKNOWN_SOURCE
    feed_age_ms: float | None = None
    # Age of THIS bid/ask at the capture instant, which is not the tick age:
    # the provider cache carries a book forward across ticks that arrive with
    # no depth. ``None`` means the feed never dated the book, so its freshness
    # cannot be proved and no study may treat it as an executable price.
    book_age_ms: float | None = None
    signal_to_snapshot_ms: float | None = None
    snapshot_ts: float | None = None
    data_quality: str = quality.MISSING

    @classmethod
    def from_dict(cls, row: dict | None) -> "Quote | None":
        """Rebuild a recorded quote. Derived properties are recomputed, never read
        back from the row, so a replay cannot inherit a stale mid or spread."""
        if not row:
            return None
        return cls(**{
            f.name: row.get(f.name, getattr(cls, f.name, None))
            for f in fields(cls)
        })

    @property
    def mid(self) -> float | None:
        b, a = _num(self.bid), _num(self.ask)
        if b is None or a is None or not quality.two_sided(b, a):
            return None
        return round((a + b) / 2.0, 4)

    @property
    def spread(self) -> float | None:
        b, a = _num(self.bid), _num(self.ask)
        if b is None or a is None or not quality.two_sided(b, a):
            return None
        return round(a - b, 4)

    @property
    def spread_pct(self) -> float | None:
        """Spread as a percentage of premium.

        Measured against the LTP when there is one, because that is the number a
        reader compares against the family medians (0.80% index, 1.61% MCX,
        9.52% single stock). Falls back to the mid when no LTP was quoted.
        """
        sp = self.spread
        ref = _num(self.premium) or self.mid
        if sp is None or not ref or ref <= 0:
            return None
        return round(100.0 * sp / ref, 3)

    @property
    def has_book(self) -> bool:
        return quality.two_sided(self.bid, self.ask)

    def as_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "vehicle": self.vehicle,
            "symbol": self.symbol,
            "strike": self.strike,
            "expiry": self.expiry,
            "days_to_expiry": self.days_to_expiry,
            "expiry_class": self.expiry_class,
            "lot_size": self.lot_size,
            "bid": self.bid,
            "ask": self.ask,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "mid": self.mid,
            "premium": self.premium,
            "spread": self.spread,
            "spread_pct": self.spread_pct,
            "oi": self.oi,
            "oi_change": self.oi_change,
            "volume": self.volume,
            "iv": self.iv,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "underlying_price": self.underlying_price,
            "atm_strike": self.atm_strike,
            "moneyness": self.moneyness,
            "distance_from_atm": self.distance_from_atm,
            "strike_steps_from_atm": self.strike_steps_from_atm,
            "delta_band": self.delta_band,
            "source": self.source,
            "feed_age_ms": self.feed_age_ms,
            "book_age_ms": self.book_age_ms,
            "signal_to_snapshot_ms": self.signal_to_snapshot_ms,
            "snapshot_ts": self.snapshot_ts,
            "data_quality": self.data_quality,
            "has_book": self.has_book,
        }


@dataclass(frozen=True)
class Plan:
    """The levels the candidate was published with — never recomputed here."""

    entry: float | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    stop: float | None = None
    target1: float | None = None
    target2: float | None = None
    target3: float | None = None
    risk: float | None = None
    reward_risk: float | None = None
    expected_move_points: float | None = None
    expected_hold_minutes: int | None = None
    lot_size: int | None = None

    @classmethod
    def from_dict(cls, row: dict | None) -> "Plan":
        if not row:
            return cls()
        return cls(**{f.name: row.get(f.name) for f in fields(cls)})

    def as_dict(self) -> dict:
        return {
            "entry": self.entry,
            "entry_low": self.entry_low,
            "entry_high": self.entry_high,
            "stop": self.stop,
            "target1": self.target1,
            "target2": self.target2,
            "target3": self.target3,
            "risk": self.risk,
            "reward_risk": self.reward_risk,
            "expected_move_points": self.expected_move_points,
            "expected_hold_minutes": self.expected_hold_minutes,
            "lot_size": self.lot_size,
        }


@dataclass(frozen=True)
class MarketContext:
    """What the market looked like — §18, always UNDERLYING_ONLY.

    Carried beside the vehicle so a later study can ask "did this vehicle
    behave differently in this regime" without joining back to another file.
    Labelled at the field level rather than in prose: ``basis`` says
    UNDERLYING_ONLY on every row, so a row lifted out of context still says what
    it is.
    """

    basis: str = "UNDERLYING_ONLY"
    direction: str | None = None
    regime: str | None = None
    htf_trend: str | None = None
    htf_alignment: str | None = None
    htf_strength: float | None = None
    momentum: float | None = None
    volatility_band: str | None = None
    atr_points: float | None = None
    vwap_side: str | None = None
    session_minute: int | None = None
    session_period: str | None = None
    weekday: str | None = None
    session: str | None = None
    trade_score: float | None = None
    confidence: float | None = None
    market_signal: str | None = None

    @classmethod
    def from_dict(cls, row: dict | None) -> "MarketContext":
        """``as_dict`` writes ``basis`` out as ``market_basis`` (the row is flat and
        ``basis`` alone would be ambiguous beside the reach basis), so the name is
        mapped back here rather than silently defaulted."""
        if not row:
            return cls()
        vals = {f.name: row.get(f.name) for f in fields(cls) if f.name != "basis"}
        return cls(basis=row.get("market_basis") or cls.basis, **vals)

    def as_dict(self) -> dict:
        return {
            "market_basis": self.basis,
            "direction": self.direction,
            "regime": self.regime,
            "htf_trend": self.htf_trend,
            "htf_alignment": self.htf_alignment,
            "htf_strength": self.htf_strength,
            "momentum": self.momentum,
            "volatility_band": self.volatility_band,
            "atr_points": self.atr_points,
            "vwap_side": self.vwap_side,
            "session_minute": self.session_minute,
            "session_period": self.session_period,
            "weekday": self.weekday,
            "session": self.session,
            "trade_score": self.trade_score,
            "confidence": self.confidence,
            "market_signal": self.market_signal,
        }


@dataclass
class Observation:
    """One market candidate with both option sides measured at one instant.

    This is the unit the whole phase collects. It deliberately does NOT contain
    an outcome: outcomes arrive later, keyed by ``observation_id``, so a row is
    never rewritten and a resolved leg cannot retroactively change the evidence
    that was available when it was opened.
    """

    observation_id: str
    signal_ts: float
    capture_ts: float
    instrument: str
    family: str
    candidate_class: str                 # BUY / WAIT / AVOID / NO_SIGNAL
    direction: str | None
    selected_vehicle: str | None         # CE / PE / FUTURES / None
    market_signal_id: str | None = None
    global_signal_id: str | None = None
    episode_id: str | None = None
    session: str | None = None

    selected: Quote | None = None
    opposite: Quote | None = None
    futures: Quote | None = None
    window: list[Quote] = field(default_factory=list)

    plan: Plan = field(default_factory=Plan)
    context: MarketContext = field(default_factory=MarketContext)

    # Filled by the analysis layers, all research-only.
    economics: dict | None = None
    entry: dict | None = None
    reach: dict | None = None
    aplus: dict | None = None
    tracking: str | None = None          # TRACKED / TRACKING_DROPPED / NOT_ELIGIBLE
    # The futures leg's own geometry at THIS signal — entry, stop and targets in
    # futures points, with the basis it was built on. Separate from ``plan``,
    # which is the option plan in premium: the two are different units and
    # merging them is how a futures net R would be computed against an option
    # stop. ``None`` when no futures book was quoted at the signal.
    futures_plan: dict | None = None
    # Research-only MCX target construction (MCX_FUTURES_CONTINUATION_BASIS).
    mcx_plan: dict | None = None

    @classmethod
    def from_dict(cls, row: dict) -> "Observation":
        """Rebuild a recorded observation well enough to be re-graded.

        Everything grading reads is restored: both books, the window, the plan,
        the context, and the analysis dicts as they were written. ``data_quality``
        and ``both_sides`` are properties and are recomputed from the quotes, so a
        replay can never inherit a verdict it should be deriving.
        """
        return cls(
            observation_id=str(row.get("observation_id") or ""),
            signal_ts=float(row.get("signal_ts") or 0.0),
            capture_ts=float(row.get("capture_ts") or 0.0),
            instrument=str(row.get("instrument") or ""),
            family=str(row.get("family") or ""),
            candidate_class=str(row.get("candidate_class") or NO_SIGNAL),
            direction=row.get("direction"),
            selected_vehicle=row.get("selected_vehicle"),
            market_signal_id=row.get("market_signal_id"),
            global_signal_id=row.get("global_signal_id"),
            episode_id=row.get("episode_id"),
            session=row.get("session"),
            selected=Quote.from_dict(row.get("selected")),
            opposite=Quote.from_dict(row.get("opposite")),
            futures=Quote.from_dict(row.get("futures")),
            window=[
                q for q in (Quote.from_dict(w) for w in (row.get("window") or []))
                if q is not None
            ],
            plan=Plan.from_dict(row.get("plan")),
            context=MarketContext.from_dict(row),
            economics=row.get("economics"),
            entry=row.get("entry"),
            reach=row.get("reach"),
            aplus=row.get("aplus"),
            futures_plan=row.get("futures_plan"),
            mcx_plan=row.get("mcx_plan"),
            tracking=row.get("tracking"),
        )

    @property
    def data_quality(self) -> str:
        """Quality of the observation as a whole: the worse of the two sides.

        Both sides matter even when only one is "the" trade, because the CE/PE
        counterfactual is the thing this row exists for. A row with a clean
        selected side and no opposite book cannot answer it, and saying so here
        is what keeps that row out of the counterfactual sample.
        """
        return quality.worst_of(
            self.selected.data_quality if self.selected else quality.MISSING,
            self.opposite.data_quality if self.opposite else quality.MISSING,
        )

    @property
    def both_sides(self) -> bool:
        return bool(
            self.selected
            and self.opposite
            and self.selected.has_book
            and self.opposite.has_book
        )

    def as_dict(self) -> dict:
        row: dict = {
            "observation_id": self.observation_id,
            "signal_ts": self.signal_ts,
            "capture_ts": self.capture_ts,
            "instrument": self.instrument,
            "family": self.family,
            "candidate_class": self.candidate_class,
            "direction": self.direction,
            "selected_vehicle": self.selected_vehicle,
            "market_signal_id": self.market_signal_id,
            "global_signal_id": self.global_signal_id,
            "episode_id": self.episode_id,
            "session": self.session,
            "selected": self.selected.as_dict() if self.selected else None,
            "opposite": self.opposite.as_dict() if self.opposite else None,
            "futures": self.futures.as_dict() if self.futures else None,
            "window": [q.as_dict() for q in self.window],
            "plan": self.plan.as_dict(),
            "economics": self.economics,
            "entry": self.entry,
            "reach": self.reach,
            "aplus": self.aplus,
            "futures_plan": self.futures_plan,
            "mcx_plan": self.mcx_plan,
            "tracking": self.tracking,
            "data_quality": self.data_quality,
            "both_sides": self.both_sides,
        }
        row.update(self.context.as_dict())
        return row


# Outcome vocabulary — §5, §16, §17, §22.
T1 = "T1"
T2 = "T2"
T3 = "T3"
STOP = "SL"
TIMEOUT = "TIMEOUT"
INVALIDATED = "INVALIDATED"
OUTCOMES: tuple[str, ...] = (T1, T2, T3, STOP, TIMEOUT, INVALIDATED)

# §10 cost status.
COST_MEASURED = "MEASURED"
COST_UNKNOWN = "UNKNOWN"

# Hold-time buckets — §16. Boundaries in minutes, upper-exclusive.
HOLD_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("LT_5", 0.0, 5.0),
    ("M_5_15", 5.0, 15.0),
    ("M_15_30", 15.0, 30.0),
    ("M_30_60", 30.0, 60.0),
    ("M_60_120", 60.0, 120.0),
    ("GT_120", 120.0, float("inf")),
)


def hold_bucket(minutes: float | None) -> str | None:
    m = _num(minutes)
    if m is None or m < 0:
        return None
    for name, lo, hi in HOLD_BUCKETS:
        if lo <= m < hi:
            return name
    return HOLD_BUCKETS[-1][0]


@dataclass
class LegOutcome:
    """The resolved path of ONE side of one observation — §5, §16, §17.

    Both the selected and the opposite side produce one of these, which is what
    makes §6 answerable: the two rows share ``observation_id`` and were opened at
    the same instant off the same chain, so comparing them is a comparison and not
    an anecdote.
    """

    observation_id: str
    instrument: str
    vehicle: str
    side: str                            # SELECTED / OPPOSITE / FUTURES
    symbol: str | None
    signal_ts: float
    session: str | None = None
    entry_ts: float | None = None
    entry_premium: float | None = None   # the ask, for a buy
    entry_quality: str | None = None
    exit_ts: float | None = None
    exit_premium: float | None = None    # the bid, for a sell
    outcome: str | None = None
    exit_reason: str | None = None

    stop: float | None = None
    target1: float | None = None
    target2: float | None = None
    target3: float | None = None
    risk: float | None = None

    # LONG / SHORT, and only ever set on a futures leg: an option leg here is
    # always bought. Without it a reader cannot tell whether a futures row's
    # positive net R came from the contract rising or falling.
    position: str | None = None
    # PUBLISHED / PROPORTIONAL for an option leg; the futures basis label for a
    # futures leg. Travels into the leg file so a comparison can refuse to pool
    # legs graded against different target constructions.
    geometry: str | None = None

    # Carried from the observation so a cohort table can be built from the leg
    # file alone. Copied at OPEN time, never at resolution: a label recomputed
    # after the outcome is known is not the label the candidate was judged by.
    vehicle_class: str | None = None
    a_plus_label: str | None = None
    a_plus_score: float | None = None
    t1_rank: str | None = None

    mfe: float | None = None
    mae: float | None = None
    mfe_ts: float | None = None
    minutes_to_mfe: float | None = None
    minutes_to_t1: float | None = None
    minutes_to_t2: float | None = None
    minutes_to_t3: float | None = None
    minutes_to_stop: float | None = None
    hold_minutes: float | None = None
    hold_bucket_name: str | None = None

    reached_half_r_then_reversed: bool | None = None
    reached_1r_then_reversed: bool | None = None
    reached_t1_then_reversed: bool | None = None
    reached_t2_then_reversed: bool | None = None
    mfe_capture_pct: float | None = None
    giveback: float | None = None

    gross_points: float | None = None
    gross_r: float | None = None
    cost_points: float | None = None
    cost_rupees: float | None = None
    net_points: float | None = None
    net_r: float | None = None
    cost_status: str = COST_UNKNOWN
    spread_source: str | None = None

    underlying_entry: float | None = None
    underlying_exit: float | None = None
    underlying_best: float | None = None
    underlying_worst: float | None = None

    samples: int = 0
    data_quality: str = quality.MISSING
    resolved: bool = False

    def as_dict(self) -> dict:
        return {
            "observation_id": self.observation_id,
            "instrument": self.instrument,
            "vehicle": self.vehicle,
            "side": self.side,
            "symbol": self.symbol,
            "signal_ts": self.signal_ts,
            "session": self.session,
            "entry_ts": self.entry_ts,
            "entry_premium": self.entry_premium,
            "entry_quality": self.entry_quality,
            "exit_ts": self.exit_ts,
            "exit_premium": self.exit_premium,
            "outcome": self.outcome,
            "exit_reason": self.exit_reason,
            "stop": self.stop,
            "target1": self.target1,
            "target2": self.target2,
            "target3": self.target3,
            "risk": self.risk,
            "vehicle_class": self.vehicle_class,
            "a_plus_label": self.a_plus_label,
            "a_plus_score": self.a_plus_score,
            "t1_rank": self.t1_rank,
            "mfe": self.mfe,
            "mae": self.mae,
            "mfe_ts": self.mfe_ts,
            "minutes_to_mfe": self.minutes_to_mfe,
            "minutes_to_t1": self.minutes_to_t1,
            "minutes_to_t2": self.minutes_to_t2,
            "minutes_to_t3": self.minutes_to_t3,
            "minutes_to_stop": self.minutes_to_stop,
            "hold_minutes": self.hold_minutes,
            "hold_bucket": self.hold_bucket_name,
            "reached_half_r_then_reversed": self.reached_half_r_then_reversed,
            "reached_1r_then_reversed": self.reached_1r_then_reversed,
            "reached_t1_then_reversed": self.reached_t1_then_reversed,
            "reached_t2_then_reversed": self.reached_t2_then_reversed,
            "mfe_capture_pct": self.mfe_capture_pct,
            "giveback": self.giveback,
            "gross_points": self.gross_points,
            "gross_r": self.gross_r,
            "cost_points": self.cost_points,
            "cost_rupees": self.cost_rupees,
            "net_points": self.net_points,
            "net_r": self.net_r,
            "cost_status": self.cost_status,
            "spread_source": self.spread_source,
            "position": self.position,
            "geometry": self.geometry,
            "underlying_entry": self.underlying_entry,
            "underlying_exit": self.underlying_exit,
            "underlying_best": self.underlying_best,
            "underlying_worst": self.underlying_worst,
            "samples": self.samples,
            "data_quality": self.data_quality,
            "resolved": self.resolved,
        }
