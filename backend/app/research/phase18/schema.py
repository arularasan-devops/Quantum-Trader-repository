"""The CAS record shapes and the vocabularies every module spells the same way.

The option book itself reuses :class:`app.research.phase17.schema.Quote`: it
already carries bid, ask, premium, spread, OI, IV and the Greeks with the
midpoint refusing to exist without a two-sided book, and duplicating forty fields
so that a CAS row could be called a CAS row would be how the two datasets start
disagreeing about what a spread is.

What is new here is the *ladder* — the same instant recorded across ATM out to
very-far-OTM on both sides — because the whole CAS claim lives in the far strikes
and the whole CAS doubt lives in what those strikes cost to get out of.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.research.phase17 import schema as p17schema
from app.research.phase17.schema import Quote

# ------------------------------------------------------------------ sides
CE = "CE"
PE = "PE"
SIDES: tuple[str, str] = (CE, PE)

# ----------------------------------------------------------- ladder rungs
# Distance from ATM in strike steps. Named rather than numeric so a report can
# never present "3" from a 50-point chain beside "3" from a 100-point chain.
ATM = "ATM"
OTM_1 = "OTM_1"
OTM_2 = "OTM_2"
OTM_3 = "OTM_3"
FAR_OTM = "FAR_OTM"
VERY_FAR_OTM = "VERY_FAR_OTM"
RUNGS: tuple[str, ...] = (ATM, OTM_1, OTM_2, OTM_3, FAR_OTM, VERY_FAR_OTM)
RUNG_STEPS: dict[str, int] = {
    ATM: 0, OTM_1: 1, OTM_2: 2, OTM_3: 3, FAR_OTM: 6, VERY_FAR_OTM: 10,
}

# ------------------------------------------------------------- directions
CAS_BULLISH = "CAS_BULLISH"
CAS_BEARISH = "CAS_BEARISH"
CAS_NEUTRAL = "CAS_NEUTRAL"
CAS_UNCLEAR = "CAS_UNCLEAR"
DIRECTIONS: tuple[str, ...] = (
    CAS_BULLISH, CAS_BEARISH, CAS_NEUTRAL, CAS_UNCLEAR,
)

# ---------------------------------------------------------------- signals
CAS_BUY_CE = "CAS_BUY_CE"
CAS_BUY_PE = "CAS_BUY_PE"
CAS_WAIT = "CAS_WAIT"
CAS_NO_TRADE = "CAS_NO_TRADE"
SIGNALS: tuple[str, ...] = (CAS_BUY_CE, CAS_BUY_PE, CAS_WAIT, CAS_NO_TRADE)

# ------------------------------------------------------- paper lifecycle
WAIT = "WAIT"
BUY = "BUY"
HOLD = "HOLD"
T1 = "T1"
T2 = "T2"
T3 = "T3"
PROTECT = "PROTECT"
EXIT = "EXIT"
SL = "SL"
TIMEOUT = "TIMEOUT"
STATUSES: tuple[str, ...] = (
    WAIT, BUY, HOLD, T1, T2, T3, PROTECT, EXIT, SL, TIMEOUT,
)
# Statuses that mean the episode is finished.
CLOSED: frozenset[str] = frozenset({EXIT, SL, TIMEOUT})

# --------------------------------------------------------------- outcomes
RIGHT_SIDE = "RIGHT_SIDE"
WRONG_SIDE = "WRONG_SIDE"
DIRECTION_FAILURE = "DIRECTION_FAILURE"
BOTH_BAD = "BOTH_BAD"
UNKNOWN = "UNKNOWN"

# ------------------------------------------------- executability (§13/§27)
EXECUTABLE = "EXECUTABLE"
NOT_EXECUTABLE = "NOT_EXECUTABLE"
EXECUTABILITY_UNKNOWN = "UNKNOWN"

# ------------------------------------------------------------ gap classes
GAP_UP = "UP"
GAP_DOWN = "DOWN"
GAP_FLAT = "FLAT"

# ----------------------------------------------------------- regime label
PRE_CAS_REGIME = "PRE_CAS_REGIME"
CAS_REGIME = "CAS_REGIME"
# CAS began on 3 August. Anything before it is a different closing mechanism and
# is never counted as CAS evidence (§25).
CAS_START_DATE = "2025-08-03"

# ------------------------------------------------------- validation states
RESEARCH = "RESEARCH"
PAPER = "PAPER"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"
FAILS = "FAILS"
CAS_PRODUCTION_CANDIDATE = "CAS_PRODUCTION_CANDIDATE"

# --------------------------------------------------------- expiry cohorts
# Re-exported from Phase 17 rather than redefined: the cohort a row belongs to
# has to mean the same thing in both datasets or the comparison between the CAS
# book and the normal book is not a comparison.
EXPIRY_DAY = p17schema.EXPIRY_DAY
PRE_EXPIRY = p17schema.PRE_EXPIRY
NON_EXPIRY = p17schema.NON_EXPIRY
EXPIRY_CLASSES: tuple[str, ...] = (EXPIRY_DAY, PRE_EXPIRY, NON_EXPIRY)

STRATEGY = "CAS"
INTRADAY = "INTRADAY"


@dataclass(frozen=True)
class Rung:
    """One ladder position: the same strike quoted on both sides at one instant.

    Both sides are held together deliberately. §4 forbids inferring one side from
    the other, and the only structural way to keep that promise is to make the
    pair the unit of storage, so a CE without its PE is visibly a CE without its
    PE.
    """

    rung: str
    steps: int
    strike: float | None
    ce: Quote | None = None
    pe: Quote | None = None

    def side(self, which: str) -> Quote | None:
        return self.ce if which == CE else self.pe

    def to_dict(self) -> dict:
        return {
            "rung": self.rung,
            "steps": self.steps,
            "strike": self.strike,
            "ce": self.ce.as_dict() if self.ce is not None else None,
            "pe": self.pe.as_dict() if self.pe is not None else None,
        }


@dataclass
class CasObservation:
    """One high-frequency CAS sample for one instrument — §3, §4, §5."""

    observation_id: str
    session: str
    instrument: str
    exchange: str | None

    intent_ts: float          # the instant we meant to sample
    capture_ts: float         # the instant the snapshot was taken
    exchange_ts: float | None  # the feed's own timestamp, when it gives one
    receive_ts: float | None
    timestamp_delta_ms: float | None

    cas_state: str
    sub_window: str | None
    mark: str | None          # T1510..T1530 when this sample stands for a mark
    remaining_seconds: float | None

    is_cas_day: bool
    day_source: str
    cas_mechanism: str
    is_expiry_day: bool | None
    expiry: str | None
    days_to_expiry: int | None
    expiry_class: str | None

    underlying: float | None
    atm_strike: float | None
    strike_step: float | None
    source: str
    quote_age_ms: float | None
    data_quality: str

    ladder: list[Rung] = field(default_factory=list)
    futures: Quote | None = None
    # Filled by the research layers; never read by production.
    direction: str | None = None
    direction_detail: dict | None = None
    signal: str | None = None
    signal_detail: dict | None = None
    cas_score: float | None = None
    score_detail: dict | None = None

    def quotes(self) -> list[Quote]:
        out: list[Quote] = []
        for r in self.ladder:
            if r.ce is not None:
                out.append(r.ce)
            if r.pe is not None:
                out.append(r.pe)
        return out

    def both_sides_pct(self) -> float | None:
        if not self.ladder:
            return None
        both = sum(1 for r in self.ladder if r.ce is not None and r.pe is not None)
        return round(100.0 * both / len(self.ladder), 1)

    def find(self, rung: str, side: str) -> Quote | None:
        for r in self.ladder:
            if r.rung == rung:
                return r.side(side)
        return None

    def to_dict(self) -> dict:
        return {
            "observation_id": self.observation_id,
            "session": self.session,
            "instrument": self.instrument,
            "exchange": self.exchange,
            "intent_ts": self.intent_ts,
            "capture_ts": self.capture_ts,
            "exchange_ts": self.exchange_ts,
            "receive_ts": self.receive_ts,
            "timestamp_delta_ms": self.timestamp_delta_ms,
            "cas_state": self.cas_state,
            "sub_window": self.sub_window,
            "mark": self.mark,
            "remaining_seconds": self.remaining_seconds,
            "is_cas_day": self.is_cas_day,
            "day_source": self.day_source,
            "cas_mechanism": self.cas_mechanism,
            "is_expiry_day": self.is_expiry_day,
            "expiry": self.expiry,
            "days_to_expiry": self.days_to_expiry,
            "expiry_class": self.expiry_class,
            "underlying": self.underlying,
            "atm_strike": self.atm_strike,
            "strike_step": self.strike_step,
            "source": self.source,
            "quote_age_ms": self.quote_age_ms,
            "data_quality": self.data_quality,
            "both_sides_pct": self.both_sides_pct(),
            "ladder": [r.to_dict() for r in self.ladder],
            "futures": self.futures.as_dict() if self.futures is not None else None,
            "direction": self.direction,
            "direction_detail": self.direction_detail,
            "signal": self.signal,
            "signal_detail": self.signal_detail,
            "cas_score": self.cas_score,
            "score_detail": self.score_detail,
            "strategy": STRATEGY,
            "regime": CAS_REGIME,
            "paper_only": True,
            "no_real_order": True,
        }


def quote_from_dict(d: dict | None) -> Quote | None:
    """Rebuild a Quote from a stored row, ignoring derived fields."""
    if not d:
        return None
    keep = {
        "instrument", "vehicle", "symbol", "strike", "expiry", "days_to_expiry",
        "expiry_class", "bid", "ask", "premium", "oi", "oi_change", "volume",
        "iv", "delta", "gamma", "theta", "underlying_price", "atm_strike",
        "moneyness", "distance_from_atm", "strike_steps_from_atm", "delta_band",
        "source", "feed_age_ms", "signal_to_snapshot_ms", "snapshot_ts",
        "data_quality",
    }
    return Quote(**{k: v for k, v in d.items() if k in keep})
