"""Pydantic schemas shared across the API and analysis engine."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Candle(BaseModel):
    time: int  # unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: float


class OptionType(str, Enum):
    CALL = "CE"
    PUT = "PE"


class OptionQuote(BaseModel):
    symbol: str
    strike: float
    option_type: OptionType
    premium: float
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    oi: int
    oi_change: int
    volume: int
    # Real top-of-book, when the feed carries it (Angel SNAP_QUOTE / FULL depth).
    # Optional and unused by the decision engine — recorded for research so
    # spread/liquidity can finally be measured instead of assumed.
    bid: float | None = None
    ask: float | None = None
    # Quantity resting at the top of each side, as the feed published it. Also
    # research-only: a two-sided book with one lot on the far side is not the
    # same executable book as one with fifty, and a spread quoted without a size
    # cannot say which it was. Absent stays absent — never zero, which would
    # read as "nothing resting" rather than "not published".
    bid_size: int | None = None
    ask_size: int | None = None
    # When THIS bid/ask was last delivered by the feed, as a unix timestamp. The
    # cache carries a book forward across ticks that arrive without depth, so
    # without this a minutes-old book is indistinguishable from a live one.
    book_ts: float | None = None
    # Contract multiplier as the feed published it (Angel scrip master). Unused
    # by the decision engine and carried for research: a round-trip cost is a
    # flat fee divided by this quantity, so a book recorded without it cannot be
    # costed at all and every leg built from it stays unresolved.
    lot_size: int | None = None


class OrderResult(BaseModel):
    ok: bool
    mode: str  # paper | live
    side: str  # BUY | SELL
    option_symbol: str
    lots: int
    fill_premium: float | None = None
    order_id: str | None = None
    message: str = ""


class Signal(str, Enum):
    BUY = "BUY"
    WAIT = "WAIT"
    HOLD = "HOLD"
    EXIT = "EXIT"
    NO_TRADE = "NO_TRADE"
    # Low-quality setup: no clear higher-timeframe trend, a trap against us, or a
    # chase with no pullback. Explicitly "stand aside" rather than forcing BUY.
    AVOID = "AVOID"


class MarketStatus(str, Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    LOW_VOLUME = "LOW_VOLUME"
    NEWS_MODE = "NEWS_MODE"
    REVERSAL = "REVERSAL_MODE"
    BREAKOUT = "BREAKOUT_MODE"


class NewsSentiment(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class NewsItem(BaseModel):
    time: int
    source: str
    headline: str
    category: str  # macro | geopolitical | inventory | opec | fed
    sentiment: NewsSentiment
    impact: float  # -1..1 signed strength


class EventGuard(BaseModel):
    """Pre-event guard status surfaced to the dashboard/engine."""
    active: bool = False
    minutes_to: int | None = None
    event: str | None = None
    impact: str | None = None
    factor: float = 1.0


class IndicatorSnapshot(BaseModel):
    """Every computed metric, so the UI can be fully transparent."""

    ema9: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    vwap: float | None = None
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    atr: float | None = None
    adx: float | None = None
    supertrend: float | None = None
    supertrend_dir: int | None = None  # 1 up, -1 down
    bb_upper: float | None = None
    bb_mid: float | None = None
    bb_lower: float | None = None
    support: float | None = None
    resistance: float | None = None
    swing_high: float | None = None
    swing_low: float | None = None
    trend: str | None = None  # UP / DOWN / SIDEWAYS
    market_structure: str | None = None  # HH_HL / LH_LL / MIXED
    momentum: float | None = None
    volume_spike: bool = False
    breakout: str | None = None  # BREAKOUT / BREAKDOWN / RETEST / NONE
    candle_pattern: str | None = None
    chart_pattern: str | None = None  # V_REVERSAL / INVERTED_V / DOUBLE_* / *_FLOW
    chart_pattern_bias: str | None = None  # BULLISH / BEARISH / NEUTRAL
    pcr: float | None = None
    max_oi_call_strike: float | None = None
    max_oi_put_strike: float | None = None
    expected_move: float | None = None
    institutional: str | None = None  # BULLISH / BEARISH / NEUTRAL
    institutional_label: str | None = None  # Strong / Moderate / Weak
    volume_level: str | None = None  # Very High / High / Normal / Low
    # --- Level 1: price-action traps / continuation ---
    fake_signal: str | None = None  # FAKE_BREAKOUT / FAKE_BREAKDOWN / NONE
    liquidity_sweep: str | None = None  # SWEEP_HIGH / SWEEP_LOW / NONE
    pullback: str | None = None  # PULLBACK_UP / PULLBACK_DOWN / NONE
    ignition: str | None = None  # IGNITION_UP / IGNITION_DOWN / NONE
    # A FRESH Supertrend flip — the BUY/SELL marker on a Supertrend chart.
    supertrend_flip: str | None = None  # FLIP_UP / FLIP_DOWN / NONE
    # --- Level 2: option premium behaviour ---
    premium_state: str | None = None  # EXPLOSION / COLLAPSE / DECAY / ...
    premium_bias: str | None = None  # BULLISH / BEARISH / NEUTRAL (underlying)
    premium_call_state: str | None = None
    premium_put_state: str | None = None
    # per-leg premium dynamics (%/bar velocity and its acceleration)
    premium_ce_velocity: float | None = None
    premium_ce_acceleration: float | None = None
    premium_pe_velocity: float | None = None
    premium_pe_acceleration: float | None = None
    # --- Level 3: open-interest state ---
    oi_state: str | None = None  # LONG_BUILDUP / SHORT_BUILDUP / ...
    oi_bias: str | None = None  # BULLISH / BEARISH / NEUTRAL
    oi_writing: str | None = None  # PUT_WRITING / CALL_WRITING / NONE
    oi_change_pct: float | None = None
    # --- Level 5: estimated smart money (honest proxy, NOT order-flow) ---
    smart_money: str | None = None  # BULLISH / BEARISH / NEUTRAL
    smart_money_label: str | None = None  # always flags "Estimated"
    smart_money_detail: str | None = None
    # --- Level 6: news materiality for today's session ---
    news_material: bool = False
    news_bull_pct: float | None = None
    news_bear_pct: float | None = None
    # --- Volume profile (Phase 1): where the session actually traded ---
    poc: float | None = None  # Point of Control — highest-volume price
    vah: float | None = None  # Value Area High (top of the ~70% volume zone)
    val: float | None = None  # Value Area Low (bottom of the ~70% volume zone)
    value_area_position: str | None = None  # ABOVE_VALUE / INSIDE_VALUE / BELOW_VALUE


class SpreadLeg(BaseModel):
    action: str            # SELL (the credit leg) / BUY (the defined-risk hedge)
    option_type: str       # CE / PE
    strike: float
    premium: float         # MODELLED (Black-Scholes), not a live quote
    delta: float


class CreditSpreadSignal(BaseModel):
    """Premium-SELLING engine — defined-risk credit spreads, PAPER ONLY.

    Sells an OTM spread instead of buying options, so time decay works FOR the
    position: it wins whenever price does not run past the short strike, which
    is most days. That is where the high win rate comes from — it is the short
    strike's delta, not a prediction.

    The measured backtest (1,243 NIFTY sessions, Black-Scholes priced on
    realised vol) is the reason for the two hard rules below:

      * 81.4% win rate frictionless, but only ~66% (PF 1.19) once the bid-ask is
        charged on all four legs, entry and exit.
      * Held overnight it LOSES (PF 0.65) — a gap straight through the short
        strike is unmanageable. So this engine is INTRADAY ONLY and must be flat
        before the close.

    Every leg price here is MODELLED. Advisory/paper only — it never places an
    order, live or otherwise.
    """

    enabled: bool = False
    state: str = "WAIT"          # PROPOSE / OPEN / CLOSE / WAIT / BLOCKED
    structure: str | None = None  # BULL_PUT / BEAR_CALL / IRON_CONDOR
    reason: str | None = None
    legs: list[SpreadLeg] = []
    net_credit: float | None = None      # collected per lot, MODELLED
    max_loss: float | None = None        # defined risk per lot
    reward_risk: float | None = None
    win_probability: float | None = None  # from the short-strike delta
    breakeven_low: float | None = None
    breakeven_high: float | None = None
    stop_at: float | None = None         # close when the loss reaches this
    implied_vol: float | None = None     # realised-vol proxy used for pricing
    minutes_to_close: int | None = None  # intraday-only guard
    paper_only: bool = True              # hard-locked; never live


class FuturesSignal(BaseModel):
    """Board state of the SEPARATE futures paper tool. PAPER ONLY.

    Denominated in INDEX POINTS, not premium: one point is ``lot_size`` rupees,
    there is no theta and no IV, and a bearish view is a SHORT rather than a long
    put. It is a distinct tool run alongside the option engine so the two can be
    compared on the same market; it shares no risk logic with it.

    The loss on a futures position is unbounded, so ``stop`` is not advisory — the
    size is derived FROM it (risk budget ÷ stop distance ÷ lot size).

    Nothing here is validated against real futures fills. ``paper_only`` is
    hard-locked True and there is no live order route into this tool.
    """

    enabled: bool = False
    state: str = "OFF"  # OFF / WAIT / BLOCKED / ENTERED / HOLDING / CLOSED / ERROR
    side: str | None = None  # LONG / SHORT
    entry: float | None = None
    price: float | None = None
    stop: float | None = None
    target1: float | None = None
    target2: float | None = None
    target3: float | None = None
    locked_floor: float | None = None  # banked level the exit now defends
    lots: int | None = None
    lot_size: int | None = None
    risk_points: float | None = None   # entry-to-stop distance, in points
    risk_rupees: float | None = None   # what the stop costs if it is hit
    open_points: float | None = None
    open_rupees: float | None = None
    net_points: float | None = None    # set on CLOSED
    net_rupees: float | None = None    # set on CLOSED
    levels_hit: str | None = None
    reason: str | None = None
    sizing_note: str | None = None
    paper_only: bool = True            # hard-locked; never live


class FuturesPlanCheck(BaseModel):
    """One named validation of a futures plan, with the number it was judged on.

    A plan is refused with a measured value against a stated threshold rather
    than a verdict, so a refusal can be argued with instead of merely believed.
    """

    name: str
    passed: bool
    value: float | str | None = None
    threshold: float | str | None = None
    detail: str = ""


class FuturesSignalCard(BaseModel):
    """RESEARCH-ONLY futures signal — its own vehicle, its own validation.

    This is not an option signal with the strike removed. The direction comes
    from the shared market read, but everything downstream of it is futures
    arithmetic: risk in INDEX POINTS (one point = ``lot_size`` rupees), no
    premium, no theta, no IV crush, a SHORT for a bearish view instead of a long
    put, and an unbounded loss that makes the stop the risk control rather than
    an advisory level.

    ``status`` is ``VALID_FUTURES_PLAN`` only when every check in ``checks``
    passed. Nothing here places, sizes for, or proposes a real order: it is
    published for comparison against the option plan on the same market event.
    """

    enabled: bool = True
    status: str = "INVALID_FUTURES_PLAN"  # VALID_FUTURES_PLAN / INVALID_FUTURES_PLAN
    # NO_SIGNAL is a stated position, not an absence: the market read produced no
    # direction, which is different from a direction that failed validation.
    signal: str = "NO_SIGNAL"  # BUY / SELL / NO_SIGNAL  (BUY = long futures)
    direction: str | None = None  # LONG / SHORT
    instrument: str | None = None
    exchange: str | None = None
    contract: str | None = None
    expiry: str | None = None
    days_to_expiry: int | None = None
    lot_size: int | None = None
    # Contract rollover (research only, Phase 12 §11). The feed quotes the near
    # month; in expiry week the research plan is written on the next contract
    # instead of refusing itself. The prices stay the quoted contract's, so a
    # rolled plan carries the near-to-next basis as an unmeasured error and says
    # so rather than presenting rolled levels as if they were measured.
    quoted_contract: str | None = None
    quoted_days_to_expiry: int | None = None
    rolled: bool = False
    reason_for_roll: str | None = None
    price_basis: str | None = None
    price_basis_note: str = ""

    price: float | None = None
    entry: float | None = None
    entry_zone_low: float | None = None
    entry_zone_high: float | None = None
    stop: float | None = None
    target1: float | None = None
    target2: float | None = None
    target3: float | None = None
    risk_points: float | None = None
    risk_rupees_per_lot: float | None = None
    reward_risk_t1: float | None = None
    reward_risk_t2: float | None = None
    cost_points: float | None = None       # modelled round trip, in points
    cost_to_risk_pct: float | None = None  # what the round trip costs of 1R

    atr: float | None = None
    atr_pct: float | None = None
    adx: float | None = None
    regime: str | None = None
    trend: str | None = None
    momentum: str | None = None
    setup_type: str | None = None
    signal_score: float | None = None
    expected_move_points: float | None = None

    spread_points: float | None = None   # None = the futures book is not recorded
    spread_pct: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    liquidity_note: str = "UNAVAILABLE"

    data_freshness_sec: float | None = None
    plan_age_seconds: int = 0
    plan_version: int = 1
    plan_state: str = "FRESH"
    invalid_reason: str | None = None
    reason: str = ""
    checks: list[FuturesPlanCheck] = Field(default_factory=list)

    global_signal_id: str | None = None
    episode_id: str | None = None
    futures_signal_id: str | None = None
    market: str = "FUTURES"
    vehicle: str = "FUTURES"
    signal_ts: int | None = None
    signal_time_ist: str | None = None
    # Publication churn (Phase 12 §1), reported for the same reason as on the
    # option card: a restated plan must not read like a new one.
    event_type: str | None = None
    raw_event_count: int | None = None
    meaningful_update_count: int | None = None
    last_published_at: float | None = None

    # Feed state for the badge (Phase 12A §15): the same FRESH / AGING / STALE /
    # DEAD / NO_DATA band the audit uses, so a plan refused on STALE_FEED shows
    # the measured age that refused it. The engine's own 90s threshold is
    # unchanged and is not read from this band.
    feed_state: str | None = None
    feed_source: str | None = None   # WS / REST / NONE — where the last bar came from
    research_only: bool = True   # hard-locked; there is no order route from here
    executable: bool = False     # hard-locked; futures never auto-execute


class ScoreBreakdown(BaseModel):
    name: str
    signal: str  # BULLISH / BEARISH / NEUTRAL
    weight: float
    contribution: float  # signed weighted value
    detail: str
    level: int = 0  # 1..7 decision priority (1 = price action, 7 = indicators)
    confirm_only: bool = False  # indicators that may only confirm, never trigger


class RecoveryAnalysis(BaseModel):
    in_position: bool
    entry_premium: float | None = None
    current_premium: float | None = None
    unrealized_pct: float | None = None
    recovery_probability: float | None = None  # 0..1
    expected_recovery_minutes: int | None = None
    recommended_action: str | None = None  # HOLD / EXIT


class ZeroToHero(BaseModel):
    """Expiry-day speculative sleeve suggestion. HIGH RISK, isolated budget.
    ``active`` False + a ``reason`` when there is no punt to make."""

    active: bool = False
    reason: str | None = None
    is_expiry_day: bool = False
    side: OptionType | None = None
    option_symbol: str | None = None
    strike: float | None = None
    premium: float | None = None
    lot_size: int | None = None
    lots: int | None = None
    budget: float = 0.0        # fixed daily budget (max loss for the sleeve)
    cost: float | None = None  # premium × lot_size × lots
    max_loss: float | None = None
    note: str | None = None


class Position(BaseModel):
    option_symbol: str | None = None
    side: str | None = None  # LONG
    entry_premium: float | None = None
    current_premium: float | None = None
    quantity_lots: int = 0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    brokerage: float = 0.0
    # Charges itemised beside the P&L, so a reader can see which component is
    # eating the trade instead of trusting one blended number.
    statutory_charges: float = 0.0
    total_costs: float = 0.0
    cost_model: str = ""
    net_pnl: float = 0.0
    holding_minutes: int = 0
    entry_time: int | None = None
    trailing_stop: float | None = None
    # Research capture (advisory only): the best (peak) and worst (trough)
    # premium seen since entry, used to derive MFE / MAE on exit. Never affects
    # the frozen engine or any decision — pure post-trade measurement.
    peak_premium: float | None = None
    trough_premium: float | None = None


class OptionRecommendation(BaseModel):
    option_symbol: str
    strike: float
    option_type: OptionType
    premium: float
    confidence: float  # 0..100


class ValidationCheck(BaseModel):
    """One Pre-Trade Validator check (Risk Management v2)."""

    name: str          # Stop Validity / Max Premium Loss / Reward:Risk / Liquidity / Spread
    passed: bool
    detail: str        # human-readable explanation (always populated)
    available: bool = True  # False when the feed can't supply the input (skipped, not failed)


class TradeValidation(BaseModel):
    """Result of the Risk Management v2 pre-trade validation layer. Advisory,
    feature-flagged; when it rejects, the BUY is downgraded to AVOID with reasons.
    Never changes entry logic, confidence, or strategy rules."""

    enabled: bool = False
    approved: bool = True
    checks: list[ValidationCheck] = Field(default_factory=list)
    rejections: list[str] = Field(default_factory=list)
    # Volatility/Greeks-aware premium stop this layer computed (points). None when
    # the layer did not run or there is no actionable option.
    refined_stop: float | None = None
    refined_stop_source: str | None = None  # e.g. "delta+gamma" / "delta" / "atr"
    max_premium_loss_pct: float | None = None
    reward_risk: float | None = None
    note: str = ""


class ExecutionEntry(BaseModel):
    """Entry Optimizer output — is NOW the best time to enter?"""

    action: str = "ENTER_NOW"           # ENTER_NOW / WAIT
    extended: bool = False
    atr_from_vwap: float | None = None  # distance from VWAP in ATR units
    pullback_probability: float | None = None  # 0..100
    current_entry: float | None = None  # current premium
    better_entry: float | None = None   # estimated better premium if waiting
    wait_candles: int | None = None
    reasons: list[str] = Field(default_factory=list)


class StrikeCandidate(BaseModel):
    symbol: str
    strike: float
    moneyness: str          # ITM / ATM / OTM
    score: float            # 0..100 composite
    delta: float
    theta: float
    iv: float
    oi: int
    volume: int
    premium: float
    reward_risk: float | None = None
    survival: float | None = None       # 0..100
    reason: str = ""


class StrikeSelection(BaseModel):
    """Strike Selector output — the most resilient nearby strike."""

    current_symbol: str | None = None
    recommended_symbol: str | None = None
    changed: bool = False
    reason: str = ""
    candidates: list[StrikeCandidate] = Field(default_factory=list)


class SurvivalComponent(BaseModel):
    name: str               # ATR / VWAP / EMA / Support / Resistance / Expiry / VIX / Gamma
    score: float            # 0..100
    available: bool = True
    detail: str = ""


class TradeSurvival(BaseModel):
    """Trade Survival Analyzer output — can the trade survive normal pullbacks?"""

    overall: float = 0.0    # 0..100
    recommendation: str = "SAFE"  # SAFE / WAIT / CHANGE_STRIKE / SKIP
    components: list[SurvivalComponent] = Field(default_factory=list)


class ExecutionIntelligence(BaseModel):
    """Phase 3.6 Execution Intelligence Layer. ADVISORY ONLY — it never changes
    the BUY/WAIT direction, confidence, or strategy. It reports execution quality
    and a recommended execution action, always with a human-readable WHY."""

    enabled: bool = False
    grade: str = "—"                    # A+ / A / B / C / D
    entry_timing: str = "—"             # Excellent / Good / Fair / Late
    strike_quality: float | None = None  # 0..100
    trade_survival: float | None = None  # 0..100
    pullback_probability: float | None = None  # 0..100
    # ENTER_NOW / WAIT_FOR_RETRACEMENT / CHANGE_STRIKE / SKIP_TRADE
    recommended_action: str = "ENTER_NOW"
    baseline_note: str = "BUY direction unchanged — execution advisory only."
    reasons: list[str] = Field(default_factory=list)
    entry: ExecutionEntry | None = None
    strike: StrikeSelection | None = None
    survival: TradeSurvival | None = None


class EarlyCheck(BaseModel):
    """One piece of evidence the Early Momentum engine requires. `weight` is the
    adaptive point contribution (0..100 total across checks) for the current
    market context; `points` is what it actually contributed (weight if passed)."""

    name: str          # Higher-Low / VWAP Reclaim / Volume / Option-Chain / Underlying
    passed: bool
    detail: str
    available: bool = True
    weight: float = 0.0
    points: float = 0.0


# The market-state ladder from accumulation → confirmed trend. Each stage is
# advisory; only the frozen engine ever produces a real (Stage-4) BUY signal.
STAGE_LABELS = {
    0: "NO_SETUP",
    1: "MOMENTUM_BUILDING",
    2: "STRUCTURE_CONFIRMED",
    3: "EARLY_BUY",
    4: "CONFIRMATION_BUY",
}


class MomentumPlan(BaseModel):
    """A complete, manual trade plan for an EARLY BUY advisory — expressed in
    OPTION PREMIUM units (what the user actually buys/sells). Derived from the
    underlying structure (swing stop + ATR buffer) translated to premium via the
    option delta, with 1R/2R/3R targets. ADVISORY ONLY — never auto-executed."""

    option_symbol: str | None = None      # the ATM leg this plan is for
    entry_low: float | None = None        # entry zone (premium)
    entry_high: float | None = None
    stop_loss: float | None = None        # premium stop (structure+ATR, via delta)
    target1: float | None = None          # 1R
    target2: float | None = None          # 2R
    target3: float | None = None          # 3R
    risk_per_lot: float | None = None      # premium at risk per lot (entry − stop) × lot size
    reward_risk: float | None = None       # to target1
    holding_time: str = ""                 # expected hold, plain English
    exit_conditions: list[str] = Field(default_factory=list)
    risk_level: str = "HIGH"               # advisory is always higher-risk than confirmation
    underlying_stop: float | None = None   # structure stop on the UNDERLYING (context)
    note: str = ""


class EarlyMomentum(BaseModel):
    """Early Momentum Advisory Engine output. A SECOND, independent engine that
    runs in PARALLEL with the frozen confirmation engine. ADVISORY ONLY — it
    never changes the frozen signal/confidence/strategy. It maps the market onto
    a 5-stage accumulation→trend ladder and only flags EARLY BUY at Stage 3."""

    enabled: bool = False
    active: bool = False                 # True → an EARLY BUY advisory is live (Stage 3)
    side: OptionType | None = None       # CALL (early long) / PUT (early short)
    stage_num: int = 0                   # 0..4
    stage: str = "NO_SETUP"              # STAGE_LABELS[stage_num]
    score: float = 0.0                   # 0..100 adaptive aligned-evidence score
    regime: str = "—"                    # context used to weight the evidence
    weight_note: str = ""                # human-readable summary of adaptive weighting
    option_symbol: str | None = None     # suggested ATM leg for the advisory
    entry_hint: float | None = None      # current premium of that leg
    atr_from_vwap: float | None = None
    checks: list[EarlyCheck] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    plan: MomentumPlan | None = None     # full trade plan (Stage 3 EARLY BUY only)
    note: str = ""


class EarlyEarly(BaseModel):
    """Early-Early Mode (Stage 2.5) — a SEPARATE, feature-flagged ADD-ON that
    sits BETWEEN Stage 2 (Structure) and Stage 3 (Early Buy). It fires when
    MULTIPLE evidence pieces align (higher-low + a bullish VWAP-reclaim candle +
    above-average volume + positive EMA slope + improving option-chain +
    adequate room before resistance) WITHOUT waiting for the full Stage-3 score
    gate — earlier, but higher-risk. Completely isolated from the frozen engine
    and the existing Early Momentum logic. ADVISORY ONLY — never auto-executed."""

    enabled: bool = False
    active: bool = False                 # True → a live Stage-2.5 Early-Early advisory
    side: OptionType | None = None       # CALL (early long) / PUT (early short)
    stage_label: str = "2.5 · EARLY-EARLY"
    # --- probability / evidence panel ---
    probability: float = 0.0             # transparent 0..100 estimate (NOT a promise)
    evidence_count: int = 0              # how many of the six pieces passed
    evidence_required: int = 5
    checks: list[EarlyCheck] = Field(default_factory=list)
    risk_level: str = "HIGH"             # always higher-risk than Early Buy
    hold_time: str = ""                  # expected hold, plain English
    # --- recommended action tier ---
    # IGNORE / WATCH / PREPARE / SMALL_POSITION / NORMAL_ENTRY / CONSERVATIVE_ENTRY
    recommended_action: str = "IGNORE"
    action_reason: str = ""
    # --- validation (all must pass before `active` is set) ---
    reward_risk: float | None = None     # to Target 1; must be ≥ configured min (2.0)
    room_atr: float | None = None        # ATR of room to next resistance/support
    spread_pct: float | None = None      # option bid/ask spread % of mid (None = unavailable)
    liquidity_ok: bool = False           # OI/volume/spread pass
    rr_ok: bool = False
    room_ok: bool = False
    # --- suggested leg + plan ---
    option_symbol: str | None = None
    entry_hint: float | None = None
    plan: MomentumPlan | None = None     # premium-based plan (RR ≥ 2:1)
    # --- agreement tracker (filled live from the tracker record) ---
    agreement: str = "PENDING"           # PENDING / CONFIRMED / REJECTED / — (idle)
    confirm_delay_candles: int | None = None  # candles from Early-Early → frozen confirm
    reasons: list[str] = Field(default_factory=list)
    note: str = ""


class ScalpSignal(BaseModel):
    """Quick Scalp Engine — a SEPARATE, feature-flagged ADD-ON that targets
    SMALL, short-duration option-premium moves (≈8–15 points) off EXHAUSTION /
    BOUNCE setups. It is the opposite intent of the trend engines: it fires when
    price is EXTENDED (a climax + ATR stretch from VWAP + a support/resistance
    rejection) and a micro-reversal begins, aiming for a quick fixed target with
    a tight stop, breakeven-after-partial and a hard time-stop. Completely
    independent of the frozen engine, Early Momentum and Early-Early. ADVISORY
    ONLY — never auto-executed."""

    enabled: bool = False
    active: bool = False                 # True → a live scalp advisory
    side: OptionType | None = None       # CALL (bounce up) / PUT (drop down)
    setup: str = "—"                     # BOUNCE / EXHAUSTION / —
    # --- probability / condition panel ---
    probability: float = 0.0             # transparent 0..100 estimate (NOT a promise)
    condition_count: int = 0             # how many scalp conditions passed
    condition_required: int = 5
    checks: list[EarlyCheck] = Field(default_factory=list)
    risk_level: str = "HIGH"             # scalps are fast + higher-risk
    hold_time: str = ""                  # expected hold, plain English
    # --- recommended action tier (same ladder as Early-Early) ---
    recommended_action: str = "IGNORE"
    action_reason: str = ""
    # --- fixed-target scalp exit model ---
    target_points: float | None = None   # fixed premium objective (points)
    reward_risk: float | None = None
    breakeven_at: float | None = None    # premium level to move stop → breakeven
    time_stop_candles: int | None = None
    liquidity_ok: bool = False
    rr_ok: bool = False
    # --- suggested leg + plan ---
    option_symbol: str | None = None
    entry_hint: float | None = None
    plan: MomentumPlan | None = None     # premium-based scalp plan (fixed target)
    # --- agreement tracker (frozen engine confirmation, for comparison) ---
    agreement: str = "PENDING"           # PENDING / CONFIRMED / REJECTED / —
    confirm_delay_candles: int | None = None
    # --- LIVE EXIT alert (fires while a scalp leg is running and rolls over) ---
    in_trade: bool = False               # a scalp leg is currently open/tracked
    exit_now: bool = False               # True → GET OUT: the move is falling
    exit_urgency: str = "NONE"           # NONE / WATCH / EXIT
    exit_reason: str = ""                # plain-English why to exit
    exit_signals: list[str] = Field(default_factory=list)  # which triggers fired
    live_premium: float | None = None    # current premium of the tracked leg
    live_points: float | None = None     # current premium − entry (points so far)
    reasons: list[str] = Field(default_factory=list)
    note: str = ""


class FlowSignal(BaseModel):
    """Flow Engine (Candle-Flow) — a SEPARATE, feature-flagged ADD-ON with its
    OWN dashboard tab. Deliberately SIMPLE: it reads the 1-minute candle flow and
    says BUY while the candle is GREEN in the direction the market is moving, STAY
    (HOLD) while it keeps printing green, and EXIT the moment it rolls RED (or the
    option premium gives back points). It auto-picks the side moving WITH the
    1-min candle (CE up / PE down) and SWITCHES sides when the flow flips. It uses
    candlestick patterns to project the next candle's likely colour. Completely
    independent of the frozen engine, Early Momentum, Early-Early and Quick Scalp.
    ADVISORY ONLY — never auto-executed."""

    enabled: bool = False
    # BUY (enter/stay long the green side) / HOLD (still green) / EXIT (rolled red)
    # / SWITCH (flow flipped side — exit the old, enter the new) / WAIT (unclear)
    state: str = "WAIT"
    side: OptionType | None = None       # the side currently in the green flow
    prev_side: OptionType | None = None  # side we were riding before (for a switch)
    switched: bool = False               # the flow flipped side on this tick
    in_trade: bool = False               # we are currently riding a flow leg
    just_entered: bool = False           # fresh entry → show sticky BUY / JUST ENTERED
    bars_in_trade: int = 0               # candles since entry (1 = the entry candle)
    # --- what the 1-min candle is doing right now ---
    candle_color: str = "—"              # GREEN / RED / FLAT
    candle_pattern: str = "NONE"
    pattern_bias: str = "NEUTRAL"        # BULLISH / BEARISH / NEUTRAL
    projection: str = "UNCLEAR"          # LIKELY_GREEN / LIKELY_RED / UNCLEAR
    green_streak: int = 0                # consecutive with-flow candles
    strength: float = 0.0                # 0..100 flow strength
    # --- the option leg we'd ride ---
    option_symbol: str | None = None
    entry_hint: float | None = None      # current premium of that leg
    entry_premium: float | None = None   # premium when this flow BUY was taken
    live_premium: float | None = None    # current premium of the tracked leg
    peak_premium: float | None = None    # best premium seen since entry
    points: float | None = None          # live − entry (points so far)
    peak_points: float | None = None     # peak − entry: the most this leg was ever up
    giveback: float | None = None        # peak − live (points given back)
    # --- where the leg gets cut ---
    # Flow has no fixed stop-loss: the leg is cut when the premium gives back
    # ``giveback_limit_points`` FROM ITS PEAK, so the level RATCHETS UP as the
    # peak rises and is not an entry-relative stop. Published as a premium so the
    # screen can show the number the exit actually fires at, rather than leaving
    # the trader to work it out from two other fields.
    giveback_limit_points: float | None = None  # the configured giveback allowance
    exit_trigger_premium: float | None = None   # peak − allowance: premium the EXIT fires at
    points_to_exit: float | None = None         # live − trigger: room left before it fires
    # --- entry economics: can this leg's expected move pay its own round trip? ---
    # ECONOMIC / UNECONOMIC / UNMEASURED. An UNECONOMIC candidate is held at WAIT
    # instead of becoming a BUY; UNMEASURED never refuses a leg, because a missing
    # delta or range is an absent measurement, not evidence against the trade.
    economics_verdict: str | None = None
    economics_cost_points: float | None = None      # round trip in premium points
    economics_cost_source: str | None = None        # MEASURED / ASSUMED_FAMILY_MEDIAN
    economics_expected_points: float | None = None  # delta x typical 1-min range
    economics_ratio: float | None = None            # expected move / cost
    economics_required: float | None = None         # the configured multiple
    economics_note: str = ""
    # --- EXIT alert ---
    exit_now: bool = False
    exit_reason: str = ""
    exit_signals: list[str] = Field(default_factory=list)
    headline: str = ""                   # one-line call for the big banner
    reasons: list[str] = Field(default_factory=list)
    note: str = ""


class SignalDelay(BaseModel):
    """Signal Delay Analyzer. Measures how much of the underlying move had
    already happened before the frozen confirmation BUY fired, and whether an
    EARLY entry would have been materially earlier. Pure measurement/advisory."""

    available: bool = False
    # --- underlying-swing metrics (all in UNDERLYING price units) ---
    move_start_price: float | None = None   # swing low/high anchoring the leg
    buy_price: float | None = None          # underlying price at the confirmation BUY
    peak_price: float | None = None         # swing high / resistance used as move target
    missed_move_pct: float | None = None    # % of the move already gone at the BUY
    remaining_move_pct: float | None = None
    late: bool = False                      # missed_move_pct >= configured threshold
    # --- option-premium entry comparison (all in OPTION PREMIUM units) ---
    confirmation_entry_premium: float | None = None  # option premium at the confirmation BUY
    early_entry_premium: float | None = None          # option premium when EARLY first flagged
    premium_diff: float | None = None        # confirmation − early (₹, positive = early cheaper)
    premium_diff_pct: float | None = None     # premium_diff as % of the confirmation premium
    candles_between: int | None = None        # candles from the EARLY flag to the confirmation BUY
    early_would_help: bool = False            # early premium was materially cheaper
    note: str = ""


class GateTrace(BaseModel):
    """Why a fresh BUY did or did not happen, gate by gate.

    Pure observability: the engine's own gate booleans, captured at the moment
    the decision was made, plus the ranked reason it was refused. Nothing here
    changes a decision — it only makes an existing decision explainable, so a
    WAIT can be attributed to a named gate instead of being read as "the engine
    saw nothing".

    ``True`` always means "this gate is satisfied / not blocking", whatever the
    underlying condition is phrased as in the engine, so a False is always the
    thing to look at.
    """

    # --- engine-layer gates (app/engine/decision.py) ---
    htf_trend_ok: bool = True          # a 5-min trend exists at all
    one_min_ok: bool = True            # 1-min is not fighting the 5-min hard
    no_trap: bool = True               # no fake breakout / liquidity sweep against us
    not_ranging: bool = True           # regime is not a blocked range
    not_risky: bool = True             # not volatile/news with no direction
    strength_ok: bool = True           # directional strength above the floor
    entry_ready: bool = True           # a timing trigger fired (pullback/momentum/…)
    not_over_extended: bool = True     # not buying the climax bar
    premium_not_exploding: bool = True # not chasing a vertical premium
    confidence_ok: bool = True         # confidence >= the regime-adjusted gate
    indicators_not_conflicting: bool = True
    reward_risk_ok: bool = True        # R:R >= min_reward_risk
    news_not_opposing: bool = True     # no strong headline against the side
    premium_quality_ok: bool = True    # the leg's premium is not DANGEROUS
    trap_probability_ok: bool = True   # buy/sell-trap probability below the veto
    # --- board-layer gates (app/engine/signal_gate.py) ---
    board_regime_ok: bool = True       # trending/breakout regime
    board_adx_ok: bool = True
    board_move_ok: bool = True         # a measurable expected move
    board_htf_agrees: bool = True      # 5-min backs the side
    board_bias15_agrees: bool = True   # 15-min bias backs the side
    board_opportunity_ok: bool = True  # premium can travel far enough to pay
    # --- ranked attribution (None when nothing blocked) ---
    primary_blocker: str | None = None
    secondary_blocker: str | None = None
    blockers: list[str] = Field(default_factory=list)
    # --- context for later statistics ---
    entry_trigger: str | None = None
    confidence: float | None = None
    confidence_gate: float | None = None
    reward_risk: float | None = None
    directional_strength: float | None = None


class Decision(BaseModel):
    """The single, top-level recommendation the whole app exists to produce."""

    signal: Signal
    confidence: float  # 0..100
    signal_strength: float  # 0..100
    trade_quality: str  # A+ / A / B / C
    # Position-INDEPENDENT market scan — what the engine would signal for a FRESH
    # entry right now (BUY / WAIT / NO_TRADE). Stays visible even while a position
    # is open (when `signal` becomes HOLD/EXIT), so the BUY/WAIT call never
    # "disappears". Defaults to `signal` on the client if unset.
    market_signal: Signal | None = None
    market_confidence: float | None = None
    # Reversal / flip: set while holding when the OPPOSITE side now has a real
    # edge (e.g. hold a CALL but the move turned down -> exit and buy a PUT).
    reversal: bool = False
    reversal_option: str | None = None
    reversal_option_type: OptionType | None = None
    # Averaging hint: while HOLDing and underwater but the thesis still holds,
    # optionally suggest adding one lot to lower the average. Conservative and
    # OFF whenever the direction has flipped (then it's EXIT/flip, never add).
    average_ok: bool = False
    average_note: str | None = None
    # Advisory position-sizing hint attached AFTER the frozen engine runs (from
    # the risk layer: capital × risk% ÷ per-lot stop risk). Display-only — it
    # never changes the BUY/WAIT/stop/target decision.
    suggested_lots: int | None = None
    # Risk Management v2 pre-trade validation (advisory, feature-flagged). Present
    # only when QT_RISK_V2_ENABLED; a rejection downgrades a BUY to AVOID.
    trade_validation: TradeValidation | None = None
    # Phase 3.6 Execution Intelligence (advisory, feature-flagged). Present only
    # when QT_EXECUTION_INTELLIGENCE_ENABLED. NEVER changes signal/confidence.
    execution_intelligence: ExecutionIntelligence | None = None
    # Early Momentum Advisory Engine + Signal Delay (advisory, feature-flagged).
    # Present only when QT_EARLY_MOMENTUM_ENABLED. Parallel to the frozen engine;
    # NEVER changes signal/confidence/strategy.
    early_momentum: EarlyMomentum | None = None
    signal_delay: SignalDelay | None = None
    # Early-Early Mode (Stage 2.5) — a SEPARATE feature-flagged add-on. Present
    # only when QT_EARLY_EARLY_ENABLED. Isolated from the frozen engine and the
    # Early Momentum logic; advisory-only, NEVER changes signal/confidence.
    early_early: EarlyEarly | None = None
    # Quick Scalp Engine — a SEPARATE feature-flagged add-on. Present only when
    # QT_SCALP_ENABLED. Isolated from the frozen engine, Early Momentum and
    # Early-Early; advisory-only, NEVER changes signal/confidence/strategy.
    scalp: ScalpSignal | None = None
    # Flow Engine (Candle-Flow) — a SEPARATE feature-flagged add-on with its own
    # dashboard tab. Present only when QT_FLOW_ENABLED. Isolated from every other
    # engine; advisory-only, NEVER changes signal/confidence/strategy.
    flow: FlowSignal | None = None
    credit_spread: CreditSpreadSignal | None = None
    futures: FuturesSignal | None = None
    # Higher-timeframe (5-min) context that GATES direction. The 1-min chart is
    # used only to time the execution (pullback entry).
    htf_trend: str | None = None  # UP / DOWN / SIDEWAYS
    htf_strength: float | None = None  # 0..100
    entry_trigger: str | None = None  # PULLBACK / BREAKOUT_RETEST / WAIT_PULLBACK / NONE
    # ATR-based dynamic risk (Phase 1): stop/targets scale with volatility+regime.
    atr_points: float | None = None       # 1x ATR in underlying points
    underlying_stop: float | None = None  # invalidation level on the FUTURES price
    recommended_option: str | None = None
    strike: float | None = None
    option_type: OptionType | None = None
    current_premium: float | None = None
    spot_price: float | None = None  # current underlying/futures price
    atm_strike: float | None = None  # nearest strike to spot
    moneyness: str | None = None  # ATM / ITM / OTM of the recommended strike
    entry_range: tuple[float, float] | None = None
    stop_loss: float | None = None
    target1: float | None = None
    target2: float | None = None
    target3: float | None = None
    expected_holding_minutes: int | None = None
    recovery_probability: float | None = None
    reasons: list[str] = Field(default_factory=list)
    buy_score: float = 0.0
    sell_score: float = 0.0
    wait_score: float = 0.0
    exit_score: float = 0.0
    # --- richer fields for the high-confidence dashboard ---
    risk_level: str = "MEDIUM"  # LOW / MEDIUM / HIGH
    risk_score: float = 0.0  # 0..100 numeric risk (higher = riskier)
    trade_score: float = 0.0  # 0..100 overall quality/strength of the setup
    # How convinced the engine is, 0..100. This is NOT a probability and must
    # never be displayed as one: it is a rescaling of directional strength and
    # agreement, it cannot fall below 50, and it read 78 on average where the
    # event happened 42.8% of the time. It ranks weakly (+0.05 vs realised R),
    # so it is useful for sorting setups and useless for sizing them.
    conviction_meter: float | None = None  # 0..100
    # The frequency actually measured for this trade's reward:risk over 5 years
    # of history — the honest counterpart to the meter above.
    observed_target_rate: dict | None = None
    expected_move_points: float | None = None  # favourable underlying move, points
    opportunity_score: float = 0.0  # 0..100 composite
    # Phase 1.5 decision-support meters (all transparent heuristics, not promises)
    opportunity_label: str = "LOW"  # VERY_HIGH / HIGH / MEDIUM / LOW / AVOID
    risk_meter: str = "MEDIUM"  # VERY_LOW / LOW / MEDIUM / HIGH / EXTREME
    # Trade Score (0..100) broken down by evidence category so the user sees WHY.
    trade_score_breakdown: dict[str, float] = Field(default_factory=dict)
    # Premium Quality — is the option we'd buy behaving healthily?
    premium_quality: float | None = None  # 0..100
    premium_health: str | None = None  # HEALTHY / NEUTRAL / DANGEROUS
    premium_momentum: float | None = None  # signed -100..100
    premium_velocity: float | None = None  # %/bar
    premium_acceleration: float | None = None
    # Trap detector — probability (0..100) the move is a trap against a buyer.
    buy_trap_prob: float = 0.0
    sell_trap_prob: float = 0.0
    fake_breakout_prob: float = 0.0
    fake_breakdown_prob: float = 0.0
    # Estimated smart-money activity (proxy from volume+OI+price, NOT order flow)
    smart_money: str | None = None  # BULLISH / BEARISH / NEUTRAL
    smart_money_label: str | None = None
    expected_recovery_minutes: int | None = None
    do_not_buy_above: float | None = None
    emergency_exit: float | None = None
    next_review_seconds: int = 45
    # Gate-by-gate trace of THIS decision (observability only — see GateTrace).
    gates: GateTrace | None = None
    locked: bool = False  # signal is committed/stable (not flickering)
    signal_age_seconds: int = 0  # how long the current signal has been live
    # --- plan freshness (Phase 11A ext.) -----------------------------------
    # The signal is held stable across ticks while the premium keeps moving, so
    # the stop/targets committed with it can stop describing a trade that is
    # still enterable. These fields say which plan is on screen and whether it
    # can still be acted on; they never change the levels themselves.
    plan_premium: float | None = None  # premium the levels were computed from
    plan_version: int = 1              # bumped on every level refresh
    plan_age_seconds: int = 0          # since the levels were last computed
    plan_state: str = "FRESH"          # FRESH / REFRESHED / STALE / INVALID
    plan_invalid_reason: str | None = None  # TARGET_NOT_ABOVE_ENTRY / ...
    # False when the printed plan cannot be entered at the live premium. A
    # dashboard must not present such a call as an actionable BUY.
    plan_actionable: bool = True
    # --- signal identity (Phase 11A ext.) ----------------------------------
    # One id chain per signal, carried by the dashboard, the journal, the funnel
    # and the reconciliation so the same call is nameable in all of them. The
    # episode is the de-duplicated call; the global id names this occurrence of
    # it. Identity only — nothing here influences a decision.
    global_signal_id: str | None = None
    episode_id: str | None = None
    market: str = "OPTIONS"   # OPTIONS / FUTURES
    vehicle: str | None = None  # CE / PE / FUTURES
    # --- publication churn (Phase 12 §1) ------------------------------------
    # How many raw ticks this episode has produced against how many were worth
    # publishing, and when it was last published. Reported so a reader can tell
    # a call that changed from the same call restated; nothing here decides.
    event_type: str | None = None  # INITIAL / MEANINGFUL_UPDATE / HEARTBEAT / ...
    raw_event_count: int | None = None
    meaningful_update_count: int | None = None
    last_published_at: float | None = None
    # --- research badges (Phase 12A §15) ------------------------------------
    # DISPLAY ONLY. On 26 Aug, 17 UNTRADABLE calls and 29 REJECT_SPREAD calls
    # reached the board looking identical to a clean one and accounted for
    # ₹96,231 of the ₹1,09,909 shadow loss. These fields make that visible
    # without gating: the signal, its score, its levels and its actionability
    # are computed exactly as before and none of them reads these fields.
    tradability: str | None = None      # TRADABLE / CAUTION / UNTRADABLE / UNKNOWN
    tradability_reasons: list[str] = Field(default_factory=list)
    spread_pct_of_premium: float | None = None
    spread_over_risk_pct: float | None = None
    a_plus_label: str | None = None     # A_PLUS / REJECT_SPREAD / REJECT_DATA / ...
    a_plus_reasons: list[str] = Field(default_factory=list)
    entry_quality: str | None = None    # IDEAL_ENTRY / ... / SEVERELY_CHASED
    entry_zone_distance_r: float | None = None
    # --- Phase 13A entry location / 13B hold window -------------------------
    # Also display only, and for the same reason: the 26 Aug card printed BUY at
    # a premium that had already expanded and carried no time budget at all.
    # These say where the premium sits against the plan's own entry and how long
    # comparable calls historically took to resolve. Nothing waits on them: a
    # WAIT_PULLBACK call is still published as the BUY the engine computed.
    entry_state: str | None = None      # BUY_NOW / WAIT_PULLBACK / ... / UNKNOWN
    entry_state_reasons: list[str] = Field(default_factory=list)
    pullback_level: float | None = None
    room_fraction_remaining: float | None = None
    hold_expected_low_min: float | None = None
    hold_expected_high_min: float | None = None
    hold_long_tail_min: float | None = None
    hold_window_basis: str | None = None
    # True for every field in this block: they are research labels, and a
    # dashboard must present them as observations rather than as instructions.
    badges_research_only: bool = True


class Alert(BaseModel):
    time: int
    kind: str  # BUY_NOW / EXIT_NOW / TARGET_HIT / STOP_LOSS / HIGH_VOLATILITY / NEWS / BIG_PLAYER / VOLUME_SPIKE / CRASH
    message: str
    severity: str  # info / warning / critical


class InstrumentOption(BaseModel):
    symbol: str
    display: str


class RiskStatus(BaseModel):
    capital: float
    risk_per_trade_pct: float
    day_pnl: float = 0.0
    trades_today: int = 0
    trades_left: int = 0
    consecutive_losses: int = 0
    loss_limit: float = 0.0
    loss_used_pct: float = 0.0  # 0..100 of the daily loss budget consumed
    can_trade: bool = True
    block_reason: str | None = None
    suggested_lots: int = 1
    lot_size: int = 1                # contract lot size for the active instrument
    brokerage_per_lot: float = 0.0   # round-trip brokerage estimate is 2x this
    squareoff_note: str = ""


class FeedHealth(BaseModel):
    """How fresh the prices on screen actually are.

    A silent drop from the WebSocket push to the ~3s REST poll looks identical
    on the board to a live feed, so the age of the last tick is reported
    explicitly rather than inferred from the mode alone.
    """

    mode: str = "simulated"          # push | rest | simulated
    live: bool = False               # push feed and a recent tick
    underlying_age_sec: float | None = None  # since the last futures/spot tick
    option_age_sec: float | None = None      # since the last ATM option tick
    stale_after_sec: float = 8.0     # age at which the provider gives up on push
    ws_connected: bool = False
    subscribed_tokens: int = 0
    push_pct_today: float | None = None  # share of the session spent on push
    fallbacks_today: int = 0             # push -> rest transitions today
    note: str = ""


class LearningStats(BaseModel):
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0  # 0..100
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    best: float = 0.0
    worst: float = 0.0
    note: str = ""


class BacktestResult(BaseModel):
    instrument: str
    candles_tested: int
    trades: int
    wins: int
    losses: int
    win_rate: float
    gross_pnl: float
    net_pnl: float
    max_drawdown: float
    avg_hold_minutes: float
    profit_factor: float
    equity_curve: list[float]


class AutoTradeEntry(BaseModel):
    """Snapshot of the currently-held auto entry so the dashboard can always show
    exactly what the bot took (the signal card clears once in a position)."""

    option_symbol: str | None = None
    side: str | None = None            # CE / PE
    strike: float | None = None
    entry_premium: float | None = None
    lots: int | None = None
    entry_time: int | None = None
    target1: float | None = None
    target2: float | None = None
    target3: float | None = None
    stop_loss: float | None = None
    locked_floor: float | None = None  # highest target locked so far (ratchet)


class AutoTradeState(BaseModel):
    """Paper auto-trader status surfaced to the dashboard. Simulated fills only."""

    enabled: bool = False
    mode: str = "paper"                 # always 'paper' for now (never live here)
    min_confidence: float = 90.0        # auto-buy gate
    max_lots: int = 1
    in_position: bool = False
    last_action: str | None = None      # e.g. "AUTO BUY NIFTY24450CE @ 182"
    last_action_time: int | None = None
    entries_today: int = 0
    day_pnl: float = 0.0
    blocked_reason: str | None = None
    # Persistent details of the open auto position (targets/stop/lock), so the UI can
    # display "what it took" even after the live BUY signal card has cleared.
    entry: AutoTradeEntry | None = None
    # Recent completed auto trades (most-recent first) for the bot history panel.
    history: list[dict] = Field(default_factory=list)


class IndiaVix(BaseModel):
    """India VIX (equity-market implied volatility). Advisory context only."""
    available: bool = False
    last: float | None = None
    change_pct: float | None = None
    level: str | None = None  # LOW / MODERATE / ELEVATED / HIGH / EXTREME
    note: str | None = None


class MarketBreadth(BaseModel):
    """NIFTY-50 advances / declines. Advisory context only."""
    available: bool = False
    advances: int | None = None
    declines: int | None = None
    unchanged: int | None = None
    ratio: float | None = None  # advances / declines
    bias: str | None = None  # BULLISH / BEARISH / NEUTRAL
    note: str | None = None


class MaxPain(BaseModel):
    """Option-writer pain minimum from the chain. Advisory context only."""
    available: bool = False
    strike: float | None = None
    spot: float | None = None
    distance_pct: float | None = None  # (spot - max_pain) / spot * 100
    bias: str | None = None
    note: str | None = None


class PcrReading(BaseModel):
    """Put/Call OI ratio with a plain-English bias. Advisory context only."""
    available: bool = False
    pcr: float | None = None
    bias: str | None = None  # BULLISH / BEARISH / NEUTRAL
    note: str | None = None


class QualityComponent(BaseModel):
    name: str
    score: float  # 0..100
    weight: float
    detail: str
    available: bool = True


class TradeQuality(BaseModel):
    """Advisory 0..100 setup-quality filter (A+/Excellent/Good/Average/Skip).
    Ranks setup quality; NEVER changes the frozen engine's BUY/WAIT decision."""
    score: float = 0.0
    grade: str = "SKIP"  # A+ / EXCELLENT / GOOD / AVERAGE / SKIP / NO SETUP
    components: list[QualityComponent] = Field(default_factory=list)
    note: str = ""


class Intelligence(BaseModel):
    """Phase 3.1 advisory Intelligence Layer. READ-ONLY market context computed
    alongside the frozen engine — it never feeds back into signal generation."""
    india_vix: IndiaVix = Field(default_factory=IndiaVix)
    breadth: MarketBreadth = Field(default_factory=MarketBreadth)
    max_pain: MaxPain = Field(default_factory=MaxPain)
    pcr: PcrReading = Field(default_factory=PcrReading)
    trade_quality: TradeQuality = Field(default_factory=TradeQuality)


class Snapshot(BaseModel):
    """Full state pushed to the client each tick."""

    time: int
    underlying: str
    instrument: str = "CRUDEOIL"
    instrument_name: str = "Crude Oil"
    instruments: list[InstrumentOption] = []
    tradingview_symbol: str = "MCX:CRUDEOIL"
    futures_price: float
    futures_change: float
    futures_change_pct: float
    # Official exchange day OHLC (matches the broker app). None on feeds that
    # can't provide it (e.g. simulated) — the UI then falls back to candles.
    futures_day_high: float | None = None
    futures_day_low: float | None = None
    futures_day_open: float | None = None
    futures_prev_close: float | None = None
    option_day_high: float | None = None
    option_day_low: float | None = None
    market_open: bool = True
    session_note: str | None = None
    data_source: str = "simulated"
    feed_mode: str = "simulated"  # push (live WebSocket) | rest (~3s poll) | simulated
    feed_health: FeedHealth | None = None
    trade_mode: str = "paper"  # paper | live
    can_trade_live: bool = False  # provider supports real order placement
    risk: RiskStatus | None = None
    auto_trade: AutoTradeState | None = None
    zero_to_hero: ZeroToHero | None = None
    learning: LearningStats | None = None
    market_status: MarketStatus
    news_sentiment: NewsSentiment
    news_score: float
    indicators: IndicatorSnapshot
    decision: Decision
    recovery: RecoveryAnalysis
    position: Position
    watchlist: list[OptionRecommendation]
    score_breakdown: list[ScoreBreakdown]
    news: list[NewsItem]
    event_guard: EventGuard | None = None
    alerts: list[Alert]
    intelligence: Intelligence | None = None
    selected_option_candles: list[Candle]
    futures_candles: list[Candle]
    # The RESEARCH futures plan for this instrument, published beside the option
    # decision rather than inside it: a separate vehicle with separate validation
    # and no order route. None when the research engine is disabled.
    futures_signal: FuturesSignalCard | None = None
