"""Phase 29 §2 — what a credit spread actually collects, and what it can lose.

Four things this module refuses to do, each of which makes the study worse and
truer:

* **it never prices a leg at the mid.** The credit is the short leg's *bid* minus
  the protective leg's *ask*, and closing is the short leg's *ask* minus the
  protective leg's *bid*. On a two-leg structure that is four half-spreads paid,
  and it is the reason mid-priced credit-spread research is roughly twice as
  profitable as reality;
* **it never charges the cost model's spread term.** The spread is already paid
  in the prices above. Charging it again would double-count it — a mistake this
  project has made once already and now tests against;
* **it never charges STT on the wrong side.** The short leg is *sold* to open, so
  its sell-side statutory charge falls on the credit, not on the buy-back. The
  shared model is called with the sold premium in the sell position, per leg;
* **it never treats a defined loss as a stop.** ``max_loss`` is width minus
  credit and is a property of the structure. It applies whether or not a stop
  ever fires, whether or not the market gaps, and whether or not the position can
  be closed — which is the entire point of using a spread instead of a naked
  short.

Margin is deliberately absent. A short spread's real requirement is SPAN plus
exposure, set by the broker and larger than the defined loss, so every return
here is quoted against defined loss and no return-on-capital figure is produced.
"""
from __future__ import annotations

import numpy as np

from app.analysis import option_costs
from app.config import settings
from app.market.instruments import get_spec

# Orders per leg per round trip: one to open, one to close.
ORDERS_PER_LEG = 2


def slippage_pct() -> float:
    """Configured per-side slippage, as a fraction of premium."""
    return max(0.0, float(settings.ai_slippage_pct)) / 100.0


def lot_qty(instrument: str, lots: int = 1) -> int:
    """Contracts per structure, from the registry seed."""
    spec = get_spec(instrument)
    return int(spec.lot_size or 1) * max(1, int(lots))


def entry_credit(
    short_bid: np.ndarray,
    long_ask: np.ndarray,
    *,
    slip: float,
) -> np.ndarray:
    """Net credit received per unit, executable and slipped against the trade.

    ``short_bid`` and ``long_ask`` are ``rows x legs``: a vertical has one column
    each, an iron condor two. Selling below the bid and buying above the ask is
    the adverse direction on every leg simultaneously, which is what a real
    multi-leg fill does when it is not filled as a package.
    """
    sold = short_bid * (1.0 - slip)
    bought = long_ask * (1.0 + slip)
    return sold.sum(axis=1) - bought.sum(axis=1)


def close_cost(
    short_ask: np.ndarray,
    long_bid: np.ndarray,
    *,
    slip: float,
) -> np.ndarray:
    """What it costs per unit to buy the structure back, adverse on every leg."""
    bought = short_ask * (1.0 + slip)
    sold = long_bid * (1.0 - slip)
    return bought.sum(axis=1) - sold.sum(axis=1)


def max_loss(width_points: float, credit: np.ndarray) -> np.ndarray:
    """Defined loss per unit: the width the short is protected across, less credit.

    For an iron condor the width is **one** wing, not the sum of both: the two
    wings cannot both finish in the money, so charging both would flatter every
    R-multiple in the study.
    """
    return np.maximum(float(width_points) - credit, 0.0)


def priceable(credit: np.ndarray, loss: np.ndarray) -> np.ndarray:
    """Rows where the structure is a real credit spread and can be scored.

    A non-positive credit is a debit spread wearing the wrong name and is
    excluded rather than sign-flipped; a non-positive defined loss would mean the
    credit exceeded the width, which is arbitrage and in practice a bad quote.
    """
    return np.isfinite(credit) & np.isfinite(loss) & (credit > 0) & (loss > 0)


def _leg_points(open_px: float, close_px: float, qty: int, *, short: bool) -> float:
    """Brokerage plus statutory charges for one leg's round trip, in points.

    The shared model's signature is buy-then-sell. A short leg sells first, so
    its premiums are passed in swapped: the sold premium goes in the sell
    position, which is where STT is charged. That is not a trick — it is the
    same arithmetic the broker performs, and getting it backwards would charge
    STT on the cheaper side of every short leg in the study.
    """
    q = max(1, int(qty))
    if short:
        ch = option_costs.charges(close_px, open_px, q)
    else:
        ch = option_costs.charges(open_px, close_px, q)
    return float(ch.total) / q


def charge_points(
    short_open: np.ndarray,
    short_close: np.ndarray,
    long_open: np.ndarray,
    long_close: np.ndarray,
    *,
    qty: int,
) -> np.ndarray:
    """Charges for the whole structure's round trip, per unit, in points.

    Every leg is charged: a two-leg vertical pays four orders and an iron condor
    eight. The exact model is called per leg rather than approximated from a
    probe, because a flat per-order brokerage is a wall on a cheap protective
    wing and a rounding error on the short leg, and a linear approximation hides
    exactly that asymmetry.
    """
    rows = short_open.shape[0]
    out = np.zeros(rows, dtype=np.float64)
    for i in range(rows):
        total = 0.0
        for j in range(short_open.shape[1]):
            o, c = float(short_open[i, j]), float(short_close[i, j])
            if not (np.isfinite(o) and o > 0):
                continue
            total += _leg_points(o, c if np.isfinite(c) else o, qty, short=True)
        for j in range(long_open.shape[1]):
            o, c = float(long_open[i, j]), float(long_close[i, j])
            if not (np.isfinite(o) and o > 0):
                continue
            total += _leg_points(o, c if np.isfinite(c) else o, qty, short=False)
        out[i] = total
    return out


def spread_cost_points(
    short_bid: np.ndarray,
    short_ask: np.ndarray,
    long_bid: np.ndarray,
    long_ask: np.ndarray,
) -> np.ndarray:
    """The quoted spread the structure crosses on a round trip, per unit.

    Reported rather than charged — it is already inside the prices — because it
    is the number that decides whether a structure is tradable at all, and on a
    four-leg round trip it is four half-spreads wide.
    """
    return (short_ask - short_bid).sum(axis=1) + (long_ask - long_bid).sum(axis=1)


def hurdle_pct_of_credit(
    charges: np.ndarray,
    spread: np.ndarray,
    credit: np.ndarray,
) -> np.ndarray:
    """Round-trip friction as a percentage of the credit collected.

    The credit-spread analogue of the option hurdle the earlier phases measure,
    and the number that decides most rows before any market view is expressed: a
    structure whose friction is 60% of its credit needs the market to be kind
    almost every time.
    """
    total = charges + spread
    return np.where(credit > 0, 100.0 * total / credit, np.nan)
