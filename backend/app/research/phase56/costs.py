"""Date-aware NSE cash-equity delivery round-trip cost — §8.

This is the one §36 field that does *not* depend on the data provider, because
the statutory components are published rates rather than market data. So it is
built here, decomposed as §8 requires, and every component carries its own
provenance label. Nothing is a single modern number applied to all five years:
the exchange transaction charge and the broker's own delivery brokerage both
changed inside the window, and the schedule brackets those changes by date.

Two honesty rules:

* every rate is ``MODELLED_COST``. They are transcribed from published
  schedules, not read back from contract notes, so they are a declared model and
  labelled as one. §8 permits exactly this and requires the label;
* the schedule is conservative where it is uncertain. Slippage is charged on
  both sides and never improves a result, and the DP charge is charged in full
  on every sell even though some brokers waive it, because a cost model that
  guesses in the strategy's favour is how a negative mechanism reads positive.

A cash-equity *delivery* trade pays no STT symmetry break: STT is 0.1% on both
buy and sell of delivery, which is an order of magnitude above intraday equity
and is the reason a short-hold long-only stock mechanism needs a large move.
"""
from __future__ import annotations

import datetime as dt

MODELLED = "MODELLED_COST"

#: Brokerage per executed order, by regime start date. Angel One moved from zero
#: delivery brokerage to a flat ₹20 (or 0.1%, whichever lower) on 2023-11-01.
BROKERAGE_REGIMES: tuple[tuple[dt.date, float, float], ...] = (
    (dt.date(2021, 1, 1), 0.0, 0.0),
    (dt.date(2023, 11, 1), 20.0, 0.001),
)

#: NSE cash-segment transaction charge on turnover, by regime start date.
EXCHANGE_CHARGE_REGIMES: tuple[tuple[dt.date, float], ...] = (
    (dt.date(2021, 1, 1), 0.0000325),
    (dt.date(2024, 10, 1), 0.0000297),
)

#: Securities transaction tax on delivery turnover, charged on both legs.
STT_DELIVERY = 0.001
#: SEBI turnover fee.
SEBI_FEE = 0.000001
#: Stamp duty, buy side only, since the 2020-07-01 uniform regime.
STAMP_DUTY_BUY = 0.00015
#: GST on (brokerage + exchange charge + SEBI fee).
GST = 0.18
#: Depository participant charge per sell scrip, plus GST. Charged in full.
DP_CHARGE_PER_SELL = 13.0
#: Declared slippage per side, as a fraction of price. Cash equity in the liquid
#: universe is tighter than this; it is charged anyway.
SLIPPAGE_PER_SIDE = 0.0005


def _regime(regimes, on: dt.date):
    chosen = regimes[0]
    for entry in regimes:
        if entry[0] <= on:
            chosen = entry
    return chosen


def brokerage(turnover: float, on: dt.date) -> float:
    _start, flat, pct = _regime(BROKERAGE_REGIMES, on)
    if flat <= 0:
        return 0.0
    return min(flat, turnover * pct)


def exchange_charge(turnover: float, on: dt.date) -> float:
    return turnover * _regime(EXCHANGE_CHARGE_REGIMES, on)[1]


def round_trip(
    *,
    buy_price: float,
    sell_price: float,
    quantity: int,
    buy_date: dt.date,
    sell_date: dt.date,
) -> dict:
    """Full decomposed round trip in rupees for one delivery position."""
    if quantity <= 0 or buy_price <= 0 or sell_price <= 0:
        raise ValueError("quantity and both prices must be positive")
    buy_turnover = buy_price * quantity
    sell_turnover = sell_price * quantity
    turnover = buy_turnover + sell_turnover

    brk = brokerage(buy_turnover, buy_date) + brokerage(sell_turnover, sell_date)
    exch = exchange_charge(buy_turnover, buy_date) + exchange_charge(sell_turnover, sell_date)
    sebi = turnover * SEBI_FEE
    stt = turnover * STT_DELIVERY
    stamp = buy_turnover * STAMP_DUTY_BUY
    gst = (brk + exch + sebi) * GST
    dp = DP_CHARGE_PER_SELL * (1 + GST)
    slippage = (buy_turnover + sell_turnover) * SLIPPAGE_PER_SIDE

    total = brk + exch + sebi + stt + stamp + gst + dp + slippage
    gross = sell_turnover - buy_turnover
    return {
        "cost_status": MODELLED,
        "buy_turnover": round(buy_turnover, 2),
        "sell_turnover": round(sell_turnover, 2),
        "brokerage": round(brk, 2),
        "exchange_charges": round(exch, 4),
        "regulatory_charges": round(sebi, 4),
        "transaction_taxes": round(stt, 2),
        "stamp_duty": round(stamp, 2),
        "gst": round(gst, 4),
        "dp_charges": round(dp, 2),
        "slippage": round(slippage, 2),
        "total_cost": round(total, 2),
        "gross_pnl": round(gross, 2),
        "net_pnl": round(gross - total, 2),
        "cost_as_pct_of_buy_turnover": round(100.0 * total / buy_turnover, 4),
        "breakeven_move_pct": round(100.0 * total / buy_turnover, 4),
    }


def schedule() -> list[dict]:
    """The schedule as an artefact row set, so the audit can print it."""
    rows = [
        {
            "component": "brokerage",
            "regimes": [
                {"from": start.isoformat(), "flat_inr": flat, "pct_cap": pct}
                for start, flat, pct in BROKERAGE_REGIMES
            ],
            "provenance": MODELLED,
        },
        {
            "component": "exchange_charges",
            "regimes": [
                {"from": start.isoformat(), "rate_on_turnover": rate}
                for start, rate in EXCHANGE_CHARGE_REGIMES
            ],
            "provenance": MODELLED,
        },
    ]
    for name, rate, note in (
        ("transaction_taxes_stt_delivery", STT_DELIVERY, "both legs"),
        ("regulatory_charges_sebi", SEBI_FEE, "both legs"),
        ("stamp_duty", STAMP_DUTY_BUY, "buy leg only"),
        ("gst", GST, "on brokerage + exchange + SEBI"),
        ("slippage", SLIPPAGE_PER_SIDE, "per side, declared, never favourable"),
    ):
        rows.append({"component": name, "rate": rate, "applies": note, "provenance": MODELLED})
    rows.append(
        {
            "component": "dp_charges",
            "rate_inr_per_sell": DP_CHARGE_PER_SELL,
            "applies": "per sell scrip, plus GST, never waived here",
            "provenance": MODELLED,
        }
    )
    return rows


def breakeven_example(price: float = 1000.0, quantity: int = 100) -> dict:
    """One worked round trip at a flat price, i.e. the pure cost hurdle."""
    return round_trip(
        buy_price=price,
        sell_price=price,
        quantity=quantity,
        buy_date=dt.date(2026, 1, 1),
        sell_date=dt.date(2026, 1, 8),
    )
