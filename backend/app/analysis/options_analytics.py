"""Option-chain analytics: PCR, OI concentration, expected move.

Consumes the option chain from any provider and produces the derivative
metrics the decision engine and strike-selection logic rely on.
"""
from __future__ import annotations

from app.models import OptionQuote, OptionType


def put_call_ratio(chain: list[OptionQuote]) -> float | None:
    """PCR, or ``None`` when the chain carries no OI.

    Returning 0.0 for "no data" made absence indistinguishable from a real
    reading, and 0.0 reads as an extreme bearish PCR — so a chain without OI
    scored a confident bearish vote on every bar. Missing data must abstain.
    """
    call_oi = sum(q.oi for q in chain if q.option_type == OptionType.CALL)
    put_oi = sum(q.oi for q in chain if q.option_type == OptionType.PUT)
    if call_oi <= 0 or put_oi <= 0:
        return None
    return round(put_oi / call_oi, 3)


def max_oi_strikes(chain: list[OptionQuote]) -> tuple[float | None, float | None]:
    """Highest-OI call and put strike, or ``None`` when the feed carries no OI.

    Without the OI guard ``max()`` over all-zero OI returns an arbitrary strike,
    which the UI then presents as a real resistance/support wall.
    """
    calls = [q for q in chain if q.option_type == OptionType.CALL and q.oi > 0]
    puts = [q for q in chain if q.option_type == OptionType.PUT and q.oi > 0]
    call_strike = max(calls, key=lambda q: q.oi).strike if calls else None
    put_strike = max(puts, key=lambda q: q.oi).strike if puts else None
    return call_strike, put_strike


def expected_move(spot: float, chain: list[OptionQuote], days: float) -> float | None:
    """ATM straddle-implied expected move over the horizon."""
    if not chain:
        return None
    atm = min(chain, key=lambda q: abs(q.strike - spot))
    atm_iv = atm.iv
    t = max(days, 0.5) / 365.0
    return round(spot * atm_iv * (t ** 0.5), 1)


def oi_state(chain: list[OptionQuote], price_change: float) -> dict:
    """Level 3 — classify the option-chain Open-Interest build-up.

    Combines OI change with the concurrent futures price move to read the
    classic four states, plus who is *writing* (selling) options:

      LONG_BUILDUP   price up   + OI up   (bullish)
      SHORT_BUILDUP  price down + OI up   (bearish)
      SHORT_COVERING price up   + OI down (bullish, less strong)
      LONG_UNWINDING price down + OI down (bearish, less strong)

    Writing: rising PUT OI = put writers defending a floor (bullish);
    rising CALL OI = call writers capping upside (bearish).

    Returns {state, bias, writing, oi_change_pct, pcr, detail}.
    """
    call_oi = sum(q.oi for q in chain if q.option_type == OptionType.CALL)
    put_oi = sum(q.oi for q in chain if q.option_type == OptionType.PUT)
    call_add = sum(q.oi_change for q in chain if q.option_type == OptionType.CALL)
    put_add = sum(q.oi_change for q in chain if q.option_type == OptionType.PUT)
    total_oi = call_oi + put_oi
    total_add = call_add + put_add
    oi_change_pct = round(total_add / total_oi * 100, 2) if total_oi else 0.0
    pcr = round(put_oi / call_oi, 3) if call_oi > 0 and put_oi > 0 else None

    # No OI, or no OI CHANGE, means the four-state read has nothing to classify.
    # The old code fell through to LONG_UNWINDING/BEARISH, turning missing broker
    # data into a confident bearish vote on every bar. Abstain instead.
    if total_oi <= 0 or total_add == 0:
        return {
            "state": None,
            "bias": None,
            "writing": "NONE",
            "oi_change_pct": oi_change_pct,
            "pcr": pcr,
            "detail": "no open-interest data from the feed — OI read unavailable",
        }

    oi_up = total_add > 0
    price_up = price_change > 0
    if price_up and oi_up:
        state, bias = "LONG_BUILDUP", "BULLISH"
    elif (not price_up) and oi_up:
        state, bias = "SHORT_BUILDUP", "BEARISH"
    elif price_up and (not oi_up):
        state, bias = "SHORT_COVERING", "BULLISH"
    else:
        state, bias = "LONG_UNWINDING", "BEARISH"

    # who is writing (net OI added on each side)
    if put_add > 0 and put_add > abs(call_add):
        writing = "PUT_WRITING"  # bullish — writers see support
    elif call_add > 0 and call_add > abs(put_add):
        writing = "CALL_WRITING"  # bearish — writers cap upside
    else:
        writing = "NONE"

    pcr_txt = "n/a" if pcr is None else str(pcr)
    detail = f"{state.replace('_', ' ').lower()}, ΔOI {oi_change_pct:+.1f}%, PCR {pcr_txt}"
    return {"state": state, "bias": bias, "writing": writing,
            "oi_change_pct": oi_change_pct, "pcr": pcr, "detail": detail}


def estimated_smart_money(chain: list[OptionQuote], volume_level: str,
                          price_change: float) -> dict:
    """Level 5 — an HONEST *proxy* for smart-money positioning.

    SmartAPI does not expose order-by-order flow, iceberg orders or
    aggressor tags, so we NEVER claim true institutional detection. This
    estimate blends three observable, public signals only:
      - net OI build-up direction (positioning)
      - relative volume (participation)
      - concurrent futures price move (confirmation)

    Returns {bias, label, confidence, detail}. Always labelled "Estimated".
    """
    call_add = sum(q.oi_change for q in chain if q.option_type == OptionType.CALL)
    put_add = sum(q.oi_change for q in chain if q.option_type == OptionType.PUT)
    total = abs(call_add) + abs(put_add)
    # net positioning: puts built => bearish, calls built => bullish lean
    net = (call_add - put_add) / total if total else 0.0

    heavy = volume_level in ("High", "Very High")
    conf = min(0.9, abs(net) * (0.8 if heavy else 0.5))
    # require volume participation AND price agreement to call it directional
    agree = (net > 0) == (price_change > 0)
    if abs(net) > 0.2 and heavy and agree:
        bias = "BULLISH" if net > 0 else "BEARISH"
    else:
        bias = "NEUTRAL"
        conf *= 0.5

    detail = (f"OI lean {net:+.2f}, {volume_level.lower()} volume"
              + (", price-confirmed" if agree else ", unconfirmed"))
    return {"bias": bias, "label": "Estimated (OI+volume proxy — not order-flow)",
            "confidence": round(conf, 2), "detail": detail}


def institutional_activity(chain: list[OptionQuote]) -> tuple[str, float]:
    """Read aggressive OI build-up as a proxy for smart-money positioning.

    Rising put OI (vs call) => bearish institutional lean, and vice-versa.
    Returns (BULLISH|BEARISH|NEUTRAL, strength 0..1).
    """
    call_add = sum(q.oi_change for q in chain if q.option_type == OptionType.CALL)
    put_add = sum(q.oi_change for q in chain if q.option_type == OptionType.PUT)
    total = abs(call_add) + abs(put_add)
    if total < 1:
        return "NEUTRAL", 0.0
    net = (put_add - call_add) / total  # +ve => bearish (puts built)
    strength = min(1.0, abs(net))
    if net > 0.15:
        return "BEARISH", strength
    if net < -0.15:
        return "BULLISH", strength
    return "NEUTRAL", strength
