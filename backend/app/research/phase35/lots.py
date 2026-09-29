"""Phase 35 — where a paper leg's lot size comes from, and what that proves.

A round trip cannot be charged without the contract multiplier: option charges
are dominated by a flat per-order fee, and a fee only becomes a number in
premium points once it is divided by the quantity it was paid on. So a leg with
no lot size has no cost, and a leg with no cost has no net result — which is
how a store of two million entered legs can resolve none of them.

The lot size is not a price. It is a property of the contract, published in the
scrip master and revised by the exchange, and it is the same integer for every
observation of the same contract in a session. That makes the instrument
specification a legitimate source for it where the quote did not carry one —
and makes it illegitimate to *call* that measured. This module therefore
returns the integer and where it came from, together, and the caller records
both: :data:`LOT_FROM_QUOTE` and :data:`LOT_FROM_PLAN` were captured at the
decision instant, :data:`LOT_FROM_SPEC` was read from the registry afterwards.

What this deliberately does not do is rescue a leg that has no executable
price. A missing ask at the decision instant, a missing bid at the exit, an
uncaptured quote and an uncaptured forward path are all still refusals: supply
a lot size to one of those and it stays UNMEASURED with its own reason, because
the multiplier was never what was missing.
"""
from __future__ import annotations

import json

from app.research.phase35 import (
    LOT_FROM_PLAN,
    LOT_FROM_QUOTE,
    LOT_FROM_SPEC,
)


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    ivalue = int(value)
    return ivalue if ivalue > 0 and float(value) == float(ivalue) else None


def from_quote(quote: dict) -> int | None:
    """The lot size the capture stored beside the quote, if it stored one."""
    return _positive_int((quote or {}).get("lot_size"))


def from_plan(obs: dict) -> int | None:
    """The lot size the engine's plan carried at the decision instant."""
    ctx = (obs or {}).get("context_json")
    if isinstance(ctx, dict):
        plan = ctx.get("plan") or {}
    elif isinstance(ctx, str) and ctx:
        try:
            plan = (json.loads(ctx) or {}).get("plan") or {}
        except ValueError:
            return None
    else:
        return None
    return _positive_int(plan.get("lot_size")) if isinstance(plan, dict) else None


def from_spec(instrument: str | None) -> int | None:
    """The registry's contract lot size for ``instrument``, or None if unknown.

    Imported lazily and read through ``REGISTRY`` rather than ``get_spec`` so an
    unknown symbol answers None instead of resolving to the default instrument's
    contract — costing a leg on another instrument's multiplier would be a
    fabricated number wearing a real label.
    """
    from app.market.instruments import REGISTRY

    spec = REGISTRY.get(str(instrument or "").upper())
    return _positive_int(getattr(spec, "lot_size", None)) if spec else None


def resolve(quote: dict, obs: dict) -> tuple[int | None, str | None]:
    """``(lot_size, source)`` for one leg, most-measured source first.

    ``(None, None)`` means no source could name the contract multiplier at all,
    and the leg stays uncosted with reason ``LOT_SIZE_UNKNOWN``.
    """
    lot = from_quote(quote)
    if lot is not None:
        return lot, LOT_FROM_QUOTE
    lot = from_plan(obs)
    if lot is not None:
        return lot, LOT_FROM_PLAN
    lot = from_spec((obs or {}).get("instrument"))
    if lot is not None:
        return lot, LOT_FROM_SPEC
    return None, None
