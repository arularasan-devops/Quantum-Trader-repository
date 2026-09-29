"""The market-setup dimensions Phase 15B tests, and how their buckets are cut.

Every dimension here is derivable from a candidate row of the underlying replay.
Nothing that needs the option chain is in this file: premium, spread, bid/ask,
OI, IV and delta belong to the live layer and are listed as its job instead.

Two rules hold throughout:

* Continuous readings are cut into buckets on the DEVELOPMENT rows only, and the
  cut points are then frozen and applied unchanged to validation and holdout.
  Cutting on the whole pool would let the holdout choose its own bucket edges,
  which is the quietest form of leakage there is.
* A row that cannot be placed in a bucket returns None and is withheld from that
  dimension, never dropped into a default bucket. A dimension's buckets must sum
  to the rows that actually carried the reading.
"""
from __future__ import annotations

from collections.abc import Callable

from app.research.phase19 import volatility as vol

# Session structure, in minutes after 09:15 IST. OPEN_0_15 is exactly the cohort
# the production skip_first_15_minutes gate refuses, so the gate is tested here
# as a bucket of a dimension rather than as a special case.
PERIODS: tuple[tuple[str, float, float], ...] = (
    ("OPEN_0_15", 0.0, 15.0),
    ("EARLY_15_60", 15.0, 60.0),
    ("MID_60_240", 60.0, 240.0),
    ("LATE_240_375", 240.0, 375.1),
)

INDEX_UNIVERSE: tuple[str, ...] = (
    "NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY", "FINNIFTY",
)

# Continuous readings that get tertile buckets fitted on development rows.
# Tertiles rather than fixed thresholds: a fixed threshold would encode this
# pool's own level and stop meaning anything in a calmer or louder year.
TERTILE_FIELDS: tuple[str, ...] = (
    "htf_strength",
    "risk_over_noise",
    "reward_risk",
    "trade_score",
    "confidence",
    "conviction_meter",
    "opportunity_score",
)

LOW, MID, HIGH = "LOW", "MID", "HIGH"


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _text(value: object) -> str | None:
    return value.upper() if isinstance(value, str) and value.strip() else None


def _tertiles(values: list[float]) -> tuple[float, float] | None:
    if len(values) < 30:
        return None
    ordered = sorted(values)
    lo = ordered[int(len(ordered) / 3)]
    hi = ordered[int(2 * len(ordered) / 3)]
    if not lo < hi:
        # A reading with no spread cannot be cut into thirds. Reporting one
        # third as an edge over another would be reporting a tie.
        return None
    return (lo, hi)


def fit_cuts(dev_rows: list[dict]) -> dict:
    """Freeze every bucket edge on the development rows.

    The volatility cut points are per instrument (phase19), because 20 points of
    noise is a different reading on MIDCPNIFTY than on BANKNIFTY.
    """
    tertiles: dict[str, tuple[float, float]] = {}
    for field in TERTILE_FIELDS:
        vals = [v for v in (_num(r.get(field)) for r in dev_rows)
                if v is not None]
        cut = _tertiles(vals)
        if cut is not None:
            tertiles[field] = cut
    return {
        "tertiles": tertiles,
        "volatility": vol.cut_points(dev_rows),
        "fitted_on": "DEVELOPMENT_ONLY",
        "note": ("bucket edges are frozen on development rows and applied "
                 "unchanged to validation and holdout, so no period chooses "
                 "the edges it is then measured against"),
    }


def _tertile_of(row: dict, field: str, cuts: dict) -> str | None:
    edges = cuts["tertiles"].get(field)
    value = _num(row.get(field))
    if edges is None or value is None:
        return None
    lo, hi = edges
    if value < lo:
        return LOW
    return MID if value < hi else HIGH


def session_period(row: dict, cuts: dict) -> str | None:
    minute = _num(row.get("entry_minute_ist"))
    if minute is None:
        return None
    for name, start, end in PERIODS:
        if start <= minute < end:
            return name
    # Outside 09:15-15:30 the stamp is not session-relative, so the row cannot
    # be placed in a session period at all.
    return None


def universe(row: dict, cuts: dict) -> str | None:
    name = _text(row.get("instrument"))
    if name is None:
        return None
    return "INDEX" if name in INDEX_UNIVERSE else "STOCK"


def instrument(row: dict, cuts: dict) -> str | None:
    return _text(row.get("instrument"))


def volatility_band(row: dict, cuts: dict) -> str | None:
    return vol.band_of(row, cuts["volatility"])


def regime(row: dict, cuts: dict) -> str | None:
    return _text(row.get("regime"))


def htf_alignment(row: dict, cuts: dict) -> str | None:
    """Is the candidate with the higher-timeframe trend, against it, or neither?

    This is the dimension the user calls HTF, and it is the one place a side and
    a trend have to be read together: LONG in an UP trend and SHORT in a DOWN
    trend are the same setup, not two.
    """
    side, trend = _text(row.get("side")), _text(row.get("htf_trend"))
    if side is None or trend is None:
        return None
    if trend in ("FLAT", "NONE", "SIDEWAYS"):
        return "NO_HTF_TREND"
    if (side, trend) in (("LONG", "UP"), ("SHORT", "DOWN")):
        return "WITH_HTF"
    if (side, trend) in (("LONG", "DOWN"), ("SHORT", "UP")):
        return "AGAINST_HTF"
    return None


def htf_strength(row: dict, cuts: dict) -> str | None:
    return _tertile_of(row, "htf_strength", cuts)


def setup_type(row: dict, cuts: dict) -> str | None:
    return _text(row.get("entry_trigger"))


def momentum(row: dict, cuts: dict) -> str | None:
    """Momentum state, read from the engine's own conviction reading."""
    return _tertile_of(row, "conviction_meter", cuts)


def extension(row: dict, cuts: dict) -> str | None:
    """How wide the stop is against the instrument's noise.

    risk_over_noise below 1 means the stop sits inside the noise the instrument
    generates anyway, which is the mechanical way a good direction still stops
    out.
    """
    return _tertile_of(row, "risk_over_noise", cuts)


def room(row: dict, cuts: dict) -> str | None:
    """Reward:risk at the plan, which is how much room the target has.

    Read the geometry column beside any T1 rate from this dimension: a bucket
    with a nearer target reaches T1 more often for a reason that has nothing to
    do with edge.
    """
    return _tertile_of(row, "reward_risk", cuts)


def entry_quality(row: dict, cuts: dict) -> str | None:
    return _text(row.get("opportunity_label"))


def risk_level(row: dict, cuts: dict) -> str | None:
    return _text(row.get("risk_level"))


def score_band(row: dict, cuts: dict) -> str | None:
    return _tertile_of(row, "trade_score", cuts)


def confidence_band(row: dict, cuts: dict) -> str | None:
    return _tertile_of(row, "confidence", cuts)


def engine_decision(row: dict, cuts: dict) -> str | None:
    """What production would have done with this bar.

    Kept as a dimension because the 5-year scan found the engine's BUY bars
    performing like the bars it refused; if that repeats here, the decision
    itself is not a selector and should not be treated as one.
    """
    taken = row.get("taken")
    if not isinstance(taken, bool):
        return None
    return "ENGINE_BUY" if taken else "ENGINE_REFUSED"


# The dimensions under test, in report order. This tuple is the hypothesis
# budget: every bucket of every dimension is one test, and the count is
# published beside the results so a winner can be read against how many
# comparisons produced it.
DIMENSIONS: tuple[tuple[str, Callable[[dict, dict], str | None]], ...] = (
    ("session_period", session_period),
    ("universe", universe),
    ("instrument", instrument),
    ("volatility_band", volatility_band),
    ("regime", regime),
    ("htf_alignment", htf_alignment),
    ("htf_strength", htf_strength),
    ("setup_type", setup_type),
    ("momentum", momentum),
    ("extension", extension),
    ("room", room),
    ("entry_quality", entry_quality),
    ("risk_level", risk_level),
    ("score_band", score_band),
    ("confidence_band", confidence_band),
    ("engine_decision", engine_decision),
)

DIMENSION_NAMES: tuple[str, ...] = tuple(name for name, _ in DIMENSIONS)

BUCKETERS: dict[str, Callable[[dict, dict], str | None]] = dict(DIMENSIONS)


def bucket(row: dict, dimension: str, cuts: dict) -> str | None:
    fn = BUCKETERS.get(dimension)
    if fn is None:
        raise ValueError(f"unknown dimension {dimension!r}")
    return fn(row, cuts)


def selector(conditions: dict[str, str], cuts: dict) -> Callable[[dict], bool]:
    """A keep-predicate for one setup: every named dimension in its bucket.

    A row that cannot be placed in one of the named dimensions is refused rather
    than kept, so a setup never quietly widens to include rows it could not
    measure.
    """
    for dimension in conditions:
        if dimension not in BUCKETERS:
            raise ValueError(f"unknown dimension {dimension!r}")

    def keep(row: dict) -> bool:
        for dimension, want in conditions.items():
            if bucket(row, dimension, cuts) != want:
                return False
        return True

    return keep


def label(conditions: dict[str, str]) -> str:
    return " + ".join(f"{k}={v}" for k, v in sorted(conditions.items()))
