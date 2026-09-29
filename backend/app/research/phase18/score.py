"""The CAS research score — §12. A ranking, not a probability.

Ten components, each contributing a bounded number of points, each shown with
its own contribution so a reader can see *why* a card scored what it did and
disagree with any single part of it.

Two rules this module exists to enforce:

* It is **not** the normal Signal Score. That score was fitted to an intraday
  trend-following engine over months of ordinary sessions; the closing auction is
  a different mechanism weeks old. Reusing it would have silently imported
  assumptions that have never been tested here.
* It is **not** a probability. A 78/100 does not mean 78% — nothing in this phase
  has been validated out-of-sample yet, and the five-year study is a standing
  reminder of how convincingly noise ranks. The field is called ``score`` and the
  reports print ``rank``, never ``probability``.
"""
from __future__ import annotations

from app.research.phase18 import quality, schema

MAX_SCORE = 100

# Component ceilings. They sum to MAX_SCORE; changing one changes the ranking, so
# they live here rather than being scattered through the scoring code.
WEIGHTS: dict[str, int] = {
    "direction": 18,
    "momentum": 10,
    "volatility": 10,
    "strike_distance": 12,
    "spread": 14,
    "liquidity": 10,
    "premium": 8,
    "room": 8,
    "time_remaining": 5,
    "expiry": 5,
}


def _f(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return None if f != f else f


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _direction_points(direction: str, votes: tuple[int, int]) -> tuple[float, str]:
    bull, bear = votes
    w = WEIGHTS["direction"]
    if direction in (schema.CAS_UNCLEAR, schema.CAS_NEUTRAL):
        return 0.0, f"{direction} — no side to back"
    agree = max(bull, bear)
    pts = w * _clamp(agree / 4.0, 0.0, 1.0)
    return pts, f"{direction} on {agree}/4 components"


def _momentum_points(momentum_atr: float | None) -> tuple[float, str]:
    w = WEIGHTS["momentum"]
    if momentum_atr is None:
        return 0.0, "no pre-CAS momentum reading"
    frac = _clamp(abs(momentum_atr) / 0.5, 0.0, 1.0)
    return w * frac, f"pre-CAS momentum {momentum_atr:+.2f} ATR"


def _volatility_points(atr_pct: float | None) -> tuple[float, str]:
    """Volatility is rewarded up to a point and then penalised.

    A dead tape cannot produce the move the whole setup depends on; an extreme
    one widens the spread faster than it moves the premium, which is precisely
    how the 44x screenshots fail to be sellable.
    """
    w = WEIGHTS["volatility"]
    if atr_pct is None:
        return 0.0, "no ATR"
    if atr_pct < 0.2:
        return w * 0.2, f"ATR {atr_pct:.2f}% of price — quiet tape"
    if atr_pct <= 1.2:
        return w, f"ATR {atr_pct:.2f}% of price"
    return w * 0.5, f"ATR {atr_pct:.2f}% — wide, spreads likely to widen with it"


def _strike_points(rung: str) -> tuple[float, str]:
    """No rung is assumed good. ATM costs the most and moves the least in
    percentage terms; the very far rungs are where premium dies worthless.
    """
    w = WEIGHTS["strike_distance"]
    table = {
        schema.ATM: (0.55, "ATM — highest cost, lowest percentage travel"),
        schema.OTM_1: (1.00, "ATM+1"),
        schema.OTM_2: (0.90, "ATM+2"),
        schema.OTM_3: (0.70, "ATM+3"),
        schema.FAR_OTM: (0.40, "far OTM — lottery geometry"),
        schema.VERY_FAR_OTM: (0.20, "very far OTM — usually expires worthless"),
    }
    frac, why = table.get(rung, (0.0, f"unknown rung {rung}"))
    return w * frac, why


def _spread_points(spread_pct: float | None) -> tuple[float, str]:
    """The single largest term, because the measured book says cost is what kills
    these trades: Rs 56 of round trip against Rs 8 of gross edge.
    """
    w = WEIGHTS["spread"]
    if spread_pct is None:
        return 0.0, "no two-sided book — spread unknown"
    if spread_pct <= 1.0:
        return w, f"spread {spread_pct:.1f}% of premium"
    if spread_pct <= 3.0:
        return w * 0.7, f"spread {spread_pct:.1f}%"
    if spread_pct <= 8.0:
        return w * 0.3, f"spread {spread_pct:.1f}% — expensive round trip"
    return 0.0, f"spread {spread_pct:.1f}% — the round trip eats the move"


def _liquidity_points(oi: float | None, volume: float | None) -> tuple[float, str]:
    w = WEIGHTS["liquidity"]
    if oi is None and volume is None:
        return 0.0, "no OI or volume"
    score = 0.0
    parts: list[str] = []
    if oi is not None:
        score += 0.5 * _clamp(oi / 100_000.0, 0.0, 1.0)
        parts.append(f"OI {oi:,.0f}")
    if volume is not None:
        score += 0.5 * _clamp(volume / 50_000.0, 0.0, 1.0)
        parts.append(f"vol {volume:,.0f}")
    return w * score, ", ".join(parts)


def _premium_points(premium: float | None) -> tuple[float, str]:
    """Cheap enough that the loss is bounded, expensive enough that brokerage and
    the tick are not the whole trade.
    """
    w = WEIGHTS["premium"]
    if premium is None or premium <= 0:
        return 0.0, "no premium"
    if premium < 1.0:
        return w * 0.2, f"premium {premium:.2f} — below the tick's resolution"
    if premium <= 60.0:
        return w, f"premium {premium:.2f}"
    if premium <= 150.0:
        return w * 0.6, f"premium {premium:.2f}"
    return w * 0.3, f"premium {premium:.2f} — large capital per lot"


def _room_points(
    expected_move_points: float | None, needed_points: float | None
) -> tuple[float, str]:
    w = WEIGHTS["room"]
    if not expected_move_points or not needed_points or needed_points <= 0:
        return 0.0, "cannot compare expected move to what T1 needs"
    ratio = expected_move_points / needed_points
    return (
        w * _clamp(ratio / 2.0, 0.0, 1.0),
        f"expected move is {ratio:.1f}x what T1 needs",
    )


def _time_points(remaining_sec: float | None) -> tuple[float, str]:
    """Time left inside the window. Entering at 15:29 leaves nothing to happen."""
    w = WEIGHTS["time_remaining"]
    if remaining_sec is None:
        return 0.0, "outside the window"
    mins = remaining_sec / 60.0
    return w * _clamp(mins / 15.0, 0.0, 1.0), f"{mins:.0f} min left in the window"


def _expiry_points(expiry_class: str | None) -> tuple[float, str]:
    w = WEIGHTS["expiry"]
    table = {
        schema.EXPIRY_DAY: (1.0, "expiry day — maximum convexity and maximum decay"),
        schema.PRE_EXPIRY: (0.7, "day before expiry"),
        schema.NON_EXPIRY: (0.3, "not near expiry"),
    }
    frac, why = table.get(expiry_class or "", (0.0, "expiry unknown"))
    return w * frac, why


def score(
    *,
    direction: str,
    direction_votes: tuple[int, int] = (0, 0),
    momentum_atr: float | None = None,
    atr_pct: float | None = None,
    rung: str = schema.ATM,
    spread_pct: float | None = None,
    oi: float | None = None,
    volume: float | None = None,
    premium: float | None = None,
    expected_move_points: float | None = None,
    t1_needs_points: float | None = None,
    remaining_sec: float | None = None,
    expiry_class: str | None = None,
    data_quality: str = quality.MISSING,
) -> dict:
    """Rank one CAS candidate, with every component's contribution attached."""
    comps: dict[str, tuple[float, str]] = {
        "direction": _direction_points(direction, direction_votes),
        "momentum": _momentum_points(momentum_atr),
        "volatility": _volatility_points(atr_pct),
        "strike_distance": _strike_points(rung),
        "spread": _spread_points(spread_pct),
        "liquidity": _liquidity_points(oi, volume),
        "premium": _premium_points(premium),
        "room": _room_points(expected_move_points, t1_needs_points),
        "time_remaining": _time_points(remaining_sec),
        "expiry": _expiry_points(expiry_class),
    }
    total = sum(p for p, _ in comps.values())

    # A card priced off a stale or one-sided book is not scored down, it is
    # capped: whatever the other components say, the number it was ranked on may
    # not have been available to trade.
    capped = False
    if data_quality not in quality.FILLABLE:
        total = min(total, 40.0)
        capped = True

    return {
        "score": round(total, 1),
        "max": MAX_SCORE,
        "components": {
            k: {"points": round(p, 1), "max": WEIGHTS[k], "detail": d}
            for k, (p, d) in comps.items()
        },
        "capped_by_data_quality": capped,
        "data_quality": data_quality,
        "is_probability": False,
        "disclaimer": (
            "A ranking within CAS candidates, not a probability and not the "
            "normal Signal Score. Nothing in this phase has been validated "
            "out-of-sample yet."
        ),
    }


def rank_label(total: float) -> str:
    """A coarse bucket for the board. Deliberately not a percentage."""
    if total >= 75:
        return "TOP"
    if total >= 60:
        return "STRONG"
    if total >= 45:
        return "MODERATE"
    if total >= 30:
        return "WEAK"
    return "POOR"
