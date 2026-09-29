"""Phase 28 §2 — what a multi-day round trip actually costs, per vehicle.

Two vehicles, two cost models, never pooled:

**Futures.** The Phase 19 decomposition is reused unchanged rather than restated,
so a swing futures figure and the intraday futures figures from Phase 24/27 are
charged by the same arithmetic. One thing is added, and it is the thing an
intraday study never had to think about: a position held across a contract expiry
is rolled, and a roll is a real round trip. It is charged per crossing.

**Cash equity delivery.** There is no premium, no option spread and no theta —
which is the reason for testing it — but there is STT on both legs, and it is ten
times the futures rate. The rates below are the standard Indian discount-broker
delivery schedule; they are constants rather than hidden inside a formula so a
reader can check each one against their own broker's contract note, and they are
written into every artefact.

Costs come back in **points per share/unit**, which is what the outcome resolver
subtracts from a move measured in points. That keeps the R arithmetic identical
across a ₹120 stock and a ₹78,000 index contract.
"""
from __future__ import annotations

import math

from app.config import settings
from app.market.instruments import get_spec
from app.research.phase19 import futcosts
from app.research.phase28 import EQUITY_DELIVERY, FUTURES

# --- cash equity delivery, per cent of turnover unless stated ---------------
# Zero-brokerage delivery is the market norm; kept as a constant so a reader on a
# full-service broker can see exactly which number to change.
EQUITY_BROKERAGE_PER_ORDER = 0.0
EQUITY_STT_PCT = 0.10             # both legs, delivery
EQUITY_TXN_PCT = 0.00297          # NSE exchange transaction charge, both legs
EQUITY_SEBI_PCT = 0.0001          # SEBI turnover fee, both legs
EQUITY_STAMP_PCT = 0.015          # buy leg only
EQUITY_GST_PCT = 18.0             # on brokerage + exchange + SEBI
EQUITY_DP_RUPEES_PER_SELL = 15.93  # depository charge, per scrip per sell day
EQUITY_SLIPPAGE_PCT_PER_SIDE = 0.03

# Futures rollover. A monthly contract is crossed roughly every 30 calendar days;
# charging by calendar days held rather than by a contract calendar is an
# approximation, and it is deliberately the conservative direction — it never
# charges fewer rolls than the calendar would.
ROLL_CALENDAR_DAYS = 30

COST_MODELLED = futcosts.COST_MODELLED


def rolls_held(calendar_days: float) -> int:
    """Contract crossings implied by a hold of ``calendar_days``."""
    if calendar_days <= 0:
        return 0
    return int(math.floor(float(calendar_days) / float(ROLL_CALENDAR_DAYS)))


def futures_round_trip(
    instrument: str,
    *,
    entry: float,
    exit_price: float,
    calendar_days_held: float = 0.0,
    slippage_points: float | None = None,
    spread_multiplier: float = 1.0,
) -> dict:
    """Points per unit for one futures swing trade, rolls included."""
    spec = get_spec(instrument)
    base = futcosts.round_trip(
        instrument,
        entry=float(entry),
        exit_price=float(exit_price),
        lot_size=int(spec.lot_size),
        slippage_points=slippage_points,
    )
    if base.get("cost_points") is None:
        return {**base, "rolls": 0, "roll_cost_points": None, "vehicle": FUTURES}
    rolls = rolls_held(calendar_days_held)
    one_trip = float(base["cost_points"])
    # A roll pays the same charges again. The multiplier is the stress handle: the
    # futures feed publishes no depth, so an unmeasured spread is explored by
    # scaling the whole round trip rather than by inventing a bid and an ask.
    roll_points = one_trip * rolls
    total = (one_trip + roll_points) * float(spread_multiplier)
    return {
        **base,
        "vehicle": FUTURES,
        "rolls": rolls,
        "roll_cost_points": round(roll_points, 4),
        "spread_multiplier": float(spread_multiplier),
        "cost_points": round(total, 4),
        "cost_rupees": round(total * float(spec.lot_size), 2),
        "roll_note": (
            f"{rolls} contract crossing(s) charged at one full round trip each, "
            f"one per {ROLL_CALENDAR_DAYS} calendar days held"
        ),
    }


def equity_round_trip(
    instrument: str,
    *,
    entry: float,
    exit_price: float,
    slippage_pct: float | None = None,
    spread_multiplier: float = 1.0,
) -> dict:
    """Points per share for one cash-delivery round trip, itemised.

    Quantity cancels out of every percentage component, so the only line that
    depends on size is the flat depository charge. It is charged against a
    one-share position, which is the harshest possible reading of it, so it is
    reported separately and excluded from the headline points figure with the
    reason stated rather than quietly dropped.
    """
    entry = float(entry)
    exit_price = float(exit_price)
    if entry <= 0 or exit_price <= 0:
        return {
            "vehicle": EQUITY_DELIVERY,
            "cost_points": None,
            "cost_status": futcosts.COST_UNKNOWN,
            "note": "entry and exit are required to charge a round trip",
        }
    slip_pct = (
        EQUITY_SLIPPAGE_PCT_PER_SIDE if slippage_pct is None else float(slippage_pct)
    )
    slip_pct = max(0.0, slip_pct)

    buy, sell = entry, exit_price
    stt = (buy + sell) * EQUITY_STT_PCT / 100.0
    txn = (buy + sell) * EQUITY_TXN_PCT / 100.0
    sebi = (buy + sell) * EQUITY_SEBI_PCT / 100.0
    stamp = buy * EQUITY_STAMP_PCT / 100.0
    brokerage = EQUITY_BROKERAGE_PER_ORDER * 2.0
    gst = (brokerage + txn + sebi) * EQUITY_GST_PCT / 100.0
    slip = (buy + sell) * slip_pct / 100.0
    statutory = stt + txn + sebi + stamp + gst + brokerage
    total = (statutory + slip) * float(spread_multiplier)
    return {
        "vehicle": EQUITY_DELIVERY,
        "cost_points": round(total, 6),
        "statutory_points": round(statutory, 6),
        "stt_points": round(stt, 6),
        "txn_points": round(txn, 6),
        "sebi_points": round(sebi, 6),
        "stamp_points": round(stamp, 6),
        "gst_points": round(gst, 6),
        "brokerage_points": round(brokerage, 6),
        "slippage_points": round(slip, 6),
        "slippage_pct_per_side": slip_pct,
        "spread_multiplier": float(spread_multiplier),
        "dp_charge_rupees_per_sell": EQUITY_DP_RUPEES_PER_SELL,
        "cost_status": COST_MODELLED,
        "rolls": 0,
        "roll_cost_points": 0.0,
        "note": (
            "delivery charges only: no premium, no option spread and no theta, "
            "which is the point of testing this vehicle. The flat depository "
            "charge is per scrip per sell day and does not scale with price, so it "
            "is reported in rupees and excluded from the per-share points figure "
            "instead of being divided by an assumed position size"
        ),
    }


def round_trip(
    instrument: str,
    vehicle: str,
    *,
    entry: float,
    exit_price: float,
    calendar_days_held: float = 0.0,
    slippage_points: float | None = None,
    slippage_pct: float | None = None,
    spread_multiplier: float = 1.0,
) -> dict:
    """Dispatch to the right cost model for the vehicle."""
    if vehicle == EQUITY_DELIVERY:
        return equity_round_trip(
            instrument,
            entry=entry,
            exit_price=exit_price,
            slippage_pct=slippage_pct,
            spread_multiplier=spread_multiplier,
        )
    return futures_round_trip(
        instrument,
        entry=entry,
        exit_price=exit_price,
        calendar_days_held=calendar_days_held,
        slippage_points=slippage_points,
        spread_multiplier=spread_multiplier,
    )


def model_note() -> dict:
    """The whole cost model as data, for the artefacts."""
    return {
        "futures": {
            "brokerage_per_order_rupees": settings.futures_brokerage_per_order,
            "stt_sell_pct_index": settings.futures_stt_sell_pct,
            "ctt_sell_pct_mcx": settings.futures_ctt_sell_pct,
            "txn_pct_both_sides": settings.futures_txn_pct,
            "slippage_points_per_side": settings.futures_slippage_points,
            "spread": "UNMEASURED — the futures feed publishes candles, not depth",
            "rollover": (
                f"one extra full round trip per {ROLL_CALENDAR_DAYS} calendar days "
                "held"
            ),
            "source": "app.research.phase19.futcosts, unchanged",
        },
        "equity_delivery": {
            "brokerage_per_order_rupees": EQUITY_BROKERAGE_PER_ORDER,
            "stt_pct_both_legs": EQUITY_STT_PCT,
            "exchange_txn_pct_both_legs": EQUITY_TXN_PCT,
            "sebi_pct_both_legs": EQUITY_SEBI_PCT,
            "stamp_pct_buy_leg": EQUITY_STAMP_PCT,
            "gst_pct_on_chargeables": EQUITY_GST_PCT,
            "dp_rupees_per_sell": EQUITY_DP_RUPEES_PER_SELL,
            "slippage_pct_per_side": EQUITY_SLIPPAGE_PCT_PER_SIDE,
            "shorting": "not permitted overnight in cash delivery; LONG only",
        },
    }


__all__ = [
    "round_trip", "futures_round_trip", "equity_round_trip", "rolls_held",
    "model_note", "ROLL_CALENDAR_DAYS", "EQUITY_SLIPPAGE_PCT_PER_SIDE",
]
