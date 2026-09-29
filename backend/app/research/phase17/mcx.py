"""MCX targets and market edge, from the futures contract itself — Phase 21 §3/§4.

RESEARCH ONLY. Nothing here changes a production stop, target, score or order.

Two holes in the 1 Sep evidence produced this module, and both come from the same
place: **an MCX commodity has no comparable five-year cash underlying.**

* 9,855 of 10,407 captured rows were graded against a T1 *modelled* from the
  expected move, which by construction never enters the costed book — so 95% of
  MCX rows could not become promotable however good their book was;
* ``market_edge`` was unmeasured on 10,382 rows, because the five-year prior in
  :mod:`app.research.phase17.aplus` is a **cash-index** statistic and there is no
  cash series for CRUDEOIL to put in it.

The wrong fix for both is to borrow the index numbers. A commodity futures
contract rolls, carries, and has its own session; the cash-index prior is not a
weaker version of that, it is a different measurement, and pooling the two would
put a number on MCX that no MCX data produced.

So both are computed from the **futures contract's own recent path** and carry
the basis label :data:`BASIS` everywhere they travel, so a reader can never mix
them with a cash-prior row by accident.

What is measured, and what it is not
------------------------------------
For every bar in a trailing window that closed in the direction being asked
about, the *next* ``horizon`` bars are measured for favourable excursion (how far
it continued) and adverse excursion (how far it went against first). That gives,
per direction, a measured distribution of continuation and of noise. T1 is the
median continuation; T2 and T3 are its 70th and 90th percentiles; the stop is the
70th percentile of the adverse excursion, so ordinary noise does not stop a leg
out. The expected move is the median continuation.

This is a **descriptive trailing statistic, not a validated edge**:

* it is computed only from bars STRICTLY BEFORE the signal, so there is no
  look-ahead, but the window is recent and overlapping, so the samples are not
  independent and no significance is claimed from them;
* ``not_validated`` is True on every output and the reports print it;
* it orders and sizes research candidates. It does not authorise a trade, and an
  MCX candidate still has to clear vehicle economics, spread, entry and data
  quality — which, on the 1 Sep evidence, is what actually refuses MCX: GOLD's
  round trip cost 5.56x the move it expected.
"""
from __future__ import annotations

from app.analysis import instrument_family as fam
from app.models import Candle

# The basis label. Attached to every target, every score and every report row
# this module produces, and deliberately not equal to any cash-prior label.
BASIS = "MCX_FUTURES_CONTINUATION_BASIS"
CASH_BASIS = "UNDERLYING_ONLY_NOT_SIGNIFICANT"  # what this is NOT

UP = "UP"
DOWN = "DOWN"
DIRECTIONS: tuple[str, str] = (UP, DOWN)

INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

# §77 capture priority. CRUDEOIL was the only MCX name whose round trip was
# smaller than the move it expected (0.38x, against 0.56-5.56x for the rest), so
# it gets every tick and the other four get a sampled one. None is removed: four
# thin days are still evidence, and a one-session vehicle reading is not grounds
# for deleting an instrument from the research universe.
DEEP = "DEEP"
SAMPLED = "SAMPLED"
DEEP_CAPTURE: frozenset[str] = frozenset({"CRUDEOIL"})
SAMPLED_CAPTURE: frozenset[str] = frozenset({
    "NATURALGAS", "GOLD", "SILVER", "COPPER",
})
# One row a minute for a sampled name. Enough to keep a spread series and to
# notice a regime change; not enough to dominate the file, which is how a day of
# capture reached 784 MB.
#
# SUPERSEDED for the research recorder, and kept as the number it used to be so
# a report can print the change rather than describe it. The recorder's gate is
# now :func:`app.research.phase50.tiers.due`, at 15 seconds: measured over
# 2026-09-17 this 60-second one left 47 of 86 eligible events unable to answer a
# 15-second observation grid, and a 61-second recording cannot be replayed at 15
# without the backfill the policy refuses. Nothing here decides a stop, a target
# or an order either way.
SAMPLE_INTERVAL_SEC = 60.0

# How many continuation samples before a distribution is reported at all. Below
# this the percentiles are being read off a handful of overlapping windows.
MIN_SAMPLES = 40
# Trailing window and forward horizon, in bars of whatever interval the caller's
# candles are on. Stated, not fitted: 12 bars is about the hold the futures paper
# book already assumes, 480 is roughly two sessions of 1-minute bars.
HORIZON_BARS = 12
LOOKBACK_BARS = 480


def tier(instrument: str) -> str | None:
    """DEEP / SAMPLED for an MCX name, None when it is not MCX."""
    key = (instrument or "").upper()
    if key in DEEP_CAPTURE:
        return DEEP
    if key in SAMPLED_CAPTURE:
        return SAMPLED
    return SAMPLED if fam.family(key) == fam.MCX else None


def due(instrument: str, *, now: float, last_capture_ts: float | None) -> bool:
    """May this instrument be captured on this tick?

    DEEP names and every non-MCX instrument are always due — this gate exists to
    thin the four expensive MCX books, not to skip anything else.

    Superseded for the research recorder by :func:`app.research.phase50.tiers.due`,
    which is this function with the interval lifted to 15 seconds and the tier
    membership below left alone. Kept because it is the previous policy, and the
    rollback path returns to exactly this interval.
    """
    if tier(instrument) != SAMPLED:
        return True
    if last_capture_ts is None:
        return True
    return (float(now) - float(last_capture_ts)) >= SAMPLE_INTERVAL_SEC


def _pct(values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile of a sorted-able list, or None if empty."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(float(ordered[0]), 4)
    pos = (len(ordered) - 1) * max(0.0, min(1.0, q))
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return round(float(ordered[lo]) * (1 - frac) + float(ordered[hi]) * frac, 4)


def continuation(
    candles: list[Candle],
    *,
    horizon: int = HORIZON_BARS,
    lookback: int = LOOKBACK_BARS,
) -> dict:
    """Measured continuation and adverse excursion, per direction.

    Every sample uses only bars that closed before the sample's own forward
    window, and the whole series ends at the last CLOSED bar the caller passed —
    the caller is responsible for not handing in a bar from the future.
    """
    out: dict = {
        "basis": BASIS,
        "not_validated": True,
        "horizon_bars": horizon,
        "lookback_bars": lookback,
        "bars_available": len(candles),
        "by_direction": {},
    }
    series = list(candles)[-(lookback + horizon):]
    for direction in DIRECTIONS:
        favourable: list[float] = []
        adverse: list[float] = []
        for i in range(len(series) - horizon):
            bar = series[i]
            moved_up = float(bar.close) > float(bar.open)
            moved_down = float(bar.close) < float(bar.open)
            if direction == UP and not moved_up:
                continue
            if direction == DOWN and not moved_down:
                continue
            fwd = series[i + 1: i + 1 + horizon]
            if not fwd:
                continue
            ref = float(bar.close)
            highs = max(float(c.high) for c in fwd)
            lows = min(float(c.low) for c in fwd)
            if direction == UP:
                favourable.append(max(0.0, highs - ref))
                adverse.append(max(0.0, ref - lows))
            else:
                favourable.append(max(0.0, ref - lows))
                adverse.append(max(0.0, highs - ref))
        samples = len(favourable)
        med_fav = _pct(favourable, 0.5)
        med_adv = _pct(adverse, 0.5)
        out["by_direction"][direction] = {
            "samples": samples,
            "enough": samples >= MIN_SAMPLES,
            "median_continuation": med_fav,
            "p70_continuation": _pct(favourable, 0.70),
            "p90_continuation": _pct(favourable, 0.90),
            "median_adverse": med_adv,
            "p70_adverse": _pct(adverse, 0.70),
            # Payoff of the measured continuation against the measured noise. Not
            # a win rate and not an expectancy: no trade is simulated here.
            "continuation_over_adverse": (
                round(med_fav / med_adv, 4)
                if med_fav is not None and med_adv else None
            ),
        }
    return out


def plan(
    instrument: str,
    price: float | None,
    candles: list[Candle],
    direction: str,
    *,
    horizon: int = HORIZON_BARS,
    lookback: int = LOOKBACK_BARS,
) -> dict:
    """Research target geometry for one MCX signal, in futures points.

    ``direction`` accepts the market read (BULLISH/BEARISH) or the position word
    (LONG/SHORT); anything else refuses rather than assuming a side.
    """
    side = _side(direction)
    out: dict = {
        "basis": BASIS,
        "not_validated": True,
        "research_only": True,
        "instrument": (instrument or "").upper(),
        "direction": side,
        "entry_reference": None,
        "expected_move_points": None,
        "stop_points": None,
        "stop": None,
        "target1": None,
        "target2": None,
        "target3": None,
        "room_points": None,
        "reward_to_risk": None,
        "samples": 0,
        "verdict": INSUFFICIENT_DATA,
        "reasons": [],
    }
    if fam.family(instrument) != fam.MCX:
        out["reasons"].append("NOT_MCX")
        return out
    if side is None:
        out["reasons"].append("DIRECTION_UNKNOWN")
        return out
    if not isinstance(price, (int, float)) or float(price) <= 0:
        out["reasons"].append("NO_PRICE")
        return out
    stats = continuation(candles, horizon=horizon, lookback=lookback)
    leg = stats["by_direction"][side]
    out["samples"] = leg["samples"]
    out["continuation"] = leg
    if not leg["enough"]:
        out["reasons"].append(f"ONLY_{leg['samples']}_SAMPLES_NEED_{MIN_SAMPLES}")
        return out
    t1 = leg["median_continuation"]
    t2 = leg["p70_continuation"]
    t3 = leg["p90_continuation"]
    risk = leg["p70_adverse"]
    if not t1 or not risk:
        out["reasons"].append("NO_MEASURED_EXCURSION")
        return out
    ref = float(price)
    sign = 1.0 if side == UP else -1.0
    out["entry_reference"] = round(ref, 4)
    out["expected_move_points"] = round(t1, 4)
    out["stop_points"] = round(risk, 4)
    out["stop"] = round(ref - sign * risk, 4)
    out["target1"] = round(ref + sign * t1, 4)
    out["target2"] = round(ref + sign * t2, 4) if t2 else None
    out["target3"] = round(ref + sign * t3, 4) if t3 else None
    out["room_points"] = round(t1, 4)
    out["reward_to_risk"] = round(t1 / risk, 4) if risk else None
    out["verdict"] = "MEASURED"
    return out


def market_edge(
    instrument: str,
    candles: list[Candle],
    direction: str,
    *,
    horizon: int = HORIZON_BARS,
    lookback: int = LOOKBACK_BARS,
) -> tuple[float | None, str]:
    """0-100 market edge for an MCX name, on the futures-continuation basis.

    Returns ``(score, note)`` with the same shape as the cash-prior helper it
    sits beside, so the A+ grader reads one interface — but the note always
    carries :data:`BASIS`, because the two numbers are NOT interchangeable and a
    report that prints them in one column has to be able to say which is which.

    The score is the measured continuation divided by the measured adverse
    excursion in the same window: 1.0 (a commodity that continues as far as it
    retraces) maps to 50, and 2.0 or better to 100. Linear and stated. A name
    with too few samples scores ``None`` — unmeasured, which costs it the
    component's full weight, rather than a neutral 50 it did not earn.
    """
    side = _side(direction)
    if fam.family(instrument) != fam.MCX:
        return None, "NOT_MCX"
    if side is None:
        return None, f"DIRECTION_UNKNOWN_{BASIS}"
    leg = continuation(candles, horizon=horizon, lookback=lookback)[
        "by_direction"][side]
    if not leg["enough"]:
        return None, f"{INSUFFICIENT_DATA}_{leg['samples']}_SAMPLES_{BASIS}"
    ratio = leg["continuation_over_adverse"]
    if ratio is None:
        return None, f"NO_RATIO_{BASIS}"
    score = max(0.0, min(100.0, 50.0 * float(ratio)))
    return round(score, 2), f"{BASIS}_RATIO_{float(ratio):.3f}"


def _side(direction: str | None) -> str | None:
    word = str(direction or "").upper()
    if word in ("BULLISH", "LONG", "BUY", "UP"):
        return UP
    if word in ("BEARISH", "SHORT", "SELL", "DOWN"):
        return DOWN
    return None
