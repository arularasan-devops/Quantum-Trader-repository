"""Observational position-sizing comparison.

Pure functions: given one entry (premium, stop, lot size, simulated account),
compute what each sizing method WOULD trade. Nothing here changes the engine's
actual quantity; the caller records the engine's real lots separately as
``ENGINE_ACTUAL``.

Rules applied to every method:
* never more than the simulated available funds can pay for (options are
  bought outright: premium x lot size + estimated costs per lot);
* futures margin is an ESTIMATE (``margin_pct`` of notional) unless a verified
  per-contract margin is supplied — it is labelled as such, never as verified;
* a method that cannot be computed (e.g. risk budget without a stop below
  entry) returns 0 lots with constraint ``UNAVAILABLE`` and the reason.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

METHODS = ("FIXED_1", "FIXED_2", "CAPITAL_BASED", "AGGRESSIVE", "RISK_BUDGET")


@dataclass(frozen=True)
class SizingInputs:
    instrument: str
    contract: str | None
    vehicle: str                 # OPTION_BUY | FUTURES
    entry_price: float
    stop_price: float | None
    lot_size: int
    lot_size_source: str
    lot_size_ts: float | None
    starting_capital: float
    equity: float
    available_funds: float
    est_cost_per_lot: float      # estimated round-trip costs for one lot
    cost_status: str = "ESTIMATED"
    capital_fraction_pct: float = 10.0
    aggressive_multiplier: float = 2.0
    risk_budget_pct: float = 1.0
    margin_pct: float | None = None          # futures: % of notional (estimate)
    verified_margin_per_lot: float | None = None


@dataclass(frozen=True)
class SizingResult:
    method: str
    proposed_lots: int
    actual_lots: int
    margin_per_lot: float | None
    margin_status: str
    total_margin: float | None
    cost_per_lot: float
    stop_exposure: float | None
    est_costs: float
    constraint_name: str
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def _margin(inp: SizingInputs) -> tuple[float | None, str]:
    if inp.vehicle == "OPTION_BUY":
        return None, "NOT_APPLICABLE"
    if inp.verified_margin_per_lot is not None:
        return float(inp.verified_margin_per_lot), "VERIFIED"
    if inp.margin_pct is not None:
        return inp.entry_price * inp.lot_size * inp.margin_pct / 100.0, "ESTIMATED"
    return None, "UNAVAILABLE"


def cost_per_lot(inp: SizingInputs) -> float:
    """Cash one lot ties up: premium paid (options) or margin (futures), plus costs."""
    margin, _ = _margin(inp)
    base = inp.entry_price * inp.lot_size if inp.vehicle == "OPTION_BUY" else (margin or 0.0)
    return round(base + max(0.0, inp.est_cost_per_lot), 2)


def _proposed(inp: SizingInputs, method: str, per_lot: float) -> tuple[int, str]:
    if method == "FIXED_1":
        return 1, "fixed 1-lot baseline"
    if method == "FIXED_2":
        return 2, "fixed 2-lot baseline"
    if per_lot <= 0:
        return 0, "cost per lot unknown"
    if method == "CAPITAL_BASED":
        budget = inp.equity * inp.capital_fraction_pct / 100.0
        return int(budget // per_lot), (
            f"{inp.capital_fraction_pct:g}% of equity ₹{inp.equity:,.0f} = ₹{budget:,.0f} "
            f"/ ₹{per_lot:,.0f} per lot")
    if method == "AGGRESSIVE":
        base = int((inp.equity * inp.capital_fraction_pct / 100.0) // per_lot)
        lots = int(math.floor(base * inp.aggressive_multiplier))
        return lots, f"capital-based {base} lot(s) x {inp.aggressive_multiplier:g} (simulation only)"
    if method == "RISK_BUDGET":
        if inp.stop_price is None or inp.stop_price >= inp.entry_price:
            return -1, "no stop below entry — risk budget cannot be computed"
        risk_per_lot = (inp.entry_price - inp.stop_price) * inp.lot_size + inp.est_cost_per_lot
        budget = inp.equity * inp.risk_budget_pct / 100.0
        return int(budget // risk_per_lot), (
            f"risk {inp.risk_budget_pct:g}% of equity = ₹{budget:,.0f} / ₹{risk_per_lot:,.0f} "
            "stop-loss + costs per lot")
    raise ValueError(f"unknown sizing method {method}")


def size(inp: SizingInputs, method: str) -> SizingResult:
    if inp.lot_size <= 0 or inp.entry_price <= 0:
        raise ValueError("lot_size and entry_price must be positive")
    margin, margin_status = _margin(inp)
    per_lot = cost_per_lot(inp)
    proposed, why = _proposed(inp, method, per_lot)
    if proposed < 0:
        return SizingResult(method, 0, 0, margin, margin_status, None, per_lot, None, 0.0,
                            "UNAVAILABLE", why)
    if inp.vehicle != "OPTION_BUY" and margin_status == "UNAVAILABLE":
        return SizingResult(method, proposed, 0, None, margin_status, None, per_lot, None, 0.0,
                            "UNAVAILABLE", why + "; futures margin unknown — not sized")
    affordable = int(max(0.0, inp.available_funds) // per_lot) if per_lot > 0 else 0
    actual = min(proposed, affordable)
    if proposed < 1:
        constraint = "BELOW_ONE_LOT"
        why += f"; method yields {proposed} lot(s)"
    elif affordable < 1:
        constraint = "INSUFFICIENT_FUNDS"
        why += (f"; available ₹{inp.available_funds:,.0f} cannot pay for one lot "
                f"(₹{per_lot:,.0f})")
    elif affordable < proposed:
        constraint = "AVAILABLE_FUNDS"
        why += f"; capped at {affordable} lot(s) by available ₹{inp.available_funds:,.0f}"
    else:
        constraint = "NONE"
        why += (f"; {affordable} lot(s) affordable — larger size limited by the method, "
                "not by funds")
    stop_exposure = None
    if inp.stop_price is not None and inp.stop_price < inp.entry_price:
        stop_exposure = round((inp.entry_price - inp.stop_price) * inp.lot_size * actual
                              + inp.est_cost_per_lot * actual, 2)
    total_margin = round(margin * actual, 2) if margin is not None else None
    return SizingResult(method, proposed, actual, margin, margin_status, total_margin, per_lot,
                        stop_exposure, round(inp.est_cost_per_lot * actual, 2), constraint, why)


def compare(inp: SizingInputs) -> list[SizingResult]:
    return [size(inp, m) for m in METHODS]
