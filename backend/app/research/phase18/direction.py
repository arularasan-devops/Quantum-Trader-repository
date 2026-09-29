"""A CAS-only directional classifier, using only what was knowable — §10.

Research only. It reads pre-decision information exclusively: the trend and
momentum going into 15:10, volatility, the day's own structure, and the part of
the auction window that has already happened at the moment of the call. Nothing
after the decision instant reaches it, which is the difference between a
classifier and a description of the answer.

It is allowed to say ``CAS_UNCLEAR``, and on most days it should. §10 is explicit
that no predictive power may be claimed until this is validated independently,
so the output carries its own disclaimer field and every report repeats it.
"""
from __future__ import annotations

from app.models import Candle, IndicatorSnapshot
from app.research.phase18 import schema

# A prep move worth calling a direction, as a fraction of the day's ATR. Below
# it the window has not committed to anything yet.
PREP_ATR_FRACTION = 0.15
# Minimum agreement among the inputs before the call is anything but UNCLEAR.
MIN_AGREEMENT = 2


def _f(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return None if f != f else f


def _pre_window_trend(candles: list[Candle] | None, spot: float | None) -> tuple[str, str]:
    """Direction of the session going into the window, from closed candles only."""
    if not candles or len(candles) < 6 or spot is None:
        return schema.CAS_NEUTRAL, "not enough closed candles before 15:10"
    closes = [float(c.close) for c in candles[-12:]]
    first, last = closes[0], closes[-1]
    if first <= 0:
        return schema.CAS_NEUTRAL, "no usable reference close"
    drift_pct = 100.0 * (last - first) / first
    if drift_pct > 0.10:
        return schema.CAS_BULLISH, f"session drifting up {drift_pct:.2f}%"
    if drift_pct < -0.10:
        return schema.CAS_BEARISH, f"session drifting down {drift_pct:.2f}%"
    return schema.CAS_NEUTRAL, f"session flat ({drift_pct:.2f}%)"


def _momentum(ind: IndicatorSnapshot | None) -> tuple[str, str]:
    if ind is None:
        return schema.CAS_NEUTRAL, "no indicator snapshot"
    rsi = _f(ind.rsi)
    macd = _f(ind.macd_hist)
    votes = 0
    parts: list[str] = []
    if rsi is not None:
        parts.append(f"RSI {rsi:.0f}")
        votes += 1 if rsi > 55 else (-1 if rsi < 45 else 0)
    if macd is not None:
        parts.append(f"MACD hist {macd:+.2f}")
        votes += 1 if macd > 0 else (-1 if macd < 0 else 0)
    if votes > 0:
        return schema.CAS_BULLISH, ", ".join(parts)
    if votes < 0:
        return schema.CAS_BEARISH, ", ".join(parts)
    return schema.CAS_NEUTRAL, ", ".join(parts) or "momentum flat"


def _htf(htf_trend: str | None, ind: IndicatorSnapshot | None) -> tuple[str, str]:
    """The 5-minute context that gates direction in the live engine.

    Read here as one vote among four rather than as a gate: CAS is a mechanical
    event in the last twenty minutes and there is no evidence yet that the HTF
    trend governs it. Falls back to the 1-minute trend when the caller has no
    higher-timeframe call to pass.
    """
    trend = (htf_trend or (ind.trend if ind else None) or "").upper()
    if trend == "UP":
        return schema.CAS_BULLISH, f"trend {trend}"
    if trend == "DOWN":
        return schema.CAS_BEARISH, f"trend {trend}"
    return schema.CAS_NEUTRAL, f"trend {trend or 'unknown'}"


def _prep_move(
    window_prices: list[float] | None, atr: float | None
) -> tuple[str, str]:
    """What the window itself has done so far, up to the decision instant."""
    if not window_prices or len(window_prices) < 2:
        return schema.CAS_NEUTRAL, "no in-window move yet"
    move = window_prices[-1] - window_prices[0]
    if atr and atr > 0:
        frac = move / atr
        if abs(frac) < PREP_ATR_FRACTION:
            return schema.CAS_NEUTRAL, f"in-window move {frac:+.2f} ATR — small"
        return (
            schema.CAS_BULLISH if move > 0 else schema.CAS_BEARISH,
            f"in-window move {frac:+.2f} ATR",
        )
    ref = window_prices[0]
    pct = 100.0 * move / ref if ref else 0.0
    if abs(pct) < 0.05:
        return schema.CAS_NEUTRAL, f"in-window move {pct:+.2f}% — small"
    return (
        schema.CAS_BULLISH if move > 0 else schema.CAS_BEARISH,
        f"in-window move {pct:+.2f}%",
    )


def classify(
    *,
    candles: list[Candle] | None = None,
    ind: IndicatorSnapshot | None = None,
    spot: float | None = None,
    window_prices: list[float] | None = None,
    atr: float | None = None,
    htf_trend: str | None = None,
) -> dict:
    """The §10 call, with every component that produced it.

    ``window_prices`` must contain only samples at or before the decision
    instant. The caller owns that guarantee; passing the full window afterwards
    would turn this into a look-ahead and the reports would be worthless.
    """
    components = {
        "pre_cas_trend": _pre_window_trend(candles, spot),
        "momentum": _momentum(ind),
        "htf": _htf(htf_trend, ind),
        "in_window_move": _prep_move(window_prices, atr),
    }
    bull = sum(1 for v, _ in components.values() if v == schema.CAS_BULLISH)
    bear = sum(1 for v, _ in components.values() if v == schema.CAS_BEARISH)

    if bull >= MIN_AGREEMENT and bull > bear:
        call = schema.CAS_BULLISH
    elif bear >= MIN_AGREEMENT and bear > bull:
        call = schema.CAS_BEARISH
    elif bull == bear and (bull or bear):
        call = schema.CAS_UNCLEAR
    else:
        call = schema.CAS_NEUTRAL

    return {
        "direction": call,
        "bullish_votes": bull,
        "bearish_votes": bear,
        "components": {k: {"vote": v, "detail": d}
                       for k, (v, d) in components.items()},
        "atr": atr,
        "causal": True,
        "disclaimer": (
            "Research only. No predictive power is claimed: this classifier has "
            "not been validated out-of-sample on CAS sessions, and CAS itself is "
            "weeks old."
        ),
    }


def side_for(direction: str) -> str | None:
    """The option side a direction implies, or ``None`` — which is a valid day."""
    if direction == schema.CAS_BULLISH:
        return schema.CE
    if direction == schema.CAS_BEARISH:
        return schema.PE
    return None
