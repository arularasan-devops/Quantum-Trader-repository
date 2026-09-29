"""Phase 25 §2 — the frozen vehicle-selection rule.

Written down before any outcome is measured, because a selection rule chosen
after seeing the results is the strategy, not a convention:

* the vehicle for an up-view is the **nearest-strike CE**, for a down-view the
  **nearest-strike PE**, measured against the aligned underlying close;
* ties (two strikes equidistant) resolve to the higher traded volume, then the
  higher open interest, then the lower ask, then the symbol — deterministic, so
  a rerun cannot pick a different leg and change the study;
* a leg is only selectable if it carried a real two-sided book in that snapshot.
  Nothing else is filtered here. Spread, premium level and break-even hurdle are
  recorded as *features* so discovery can test them; filtering them out at
  selection would hide the very economics the study is meant to measure.
"""
from __future__ import annotations

CE, PE = "CE", "PE"


def _rank(leg: dict, spot: float) -> tuple:
    """Sort key: nearest strike first, then the deterministic tie-breaks."""
    strike = float(leg.get("strike") or 0.0)
    volume = float(leg.get("volume") or 0.0)
    oi = float(leg.get("oi") or 0.0)
    ask = float(leg.get("ask") or 0.0)
    return (abs(strike - spot), -volume, -oi, ask, str(leg.get("symbol") or ""))


def pick(book: dict[str, dict], spot: float, option_type: str) -> dict | None:
    """The selected leg of one side from one snapshot's book, or ``None``.

    ``None`` is a real answer: a snapshot that quoted no usable leg of that side
    produces no candidate rather than a candidate priced off the other side.
    """
    side = (option_type or "").upper()
    best: dict | None = None
    best_key: tuple | None = None
    for leg in book.values():
        if str(leg.get("option_type") or "").upper() != side:
            continue
        key = _rank(leg, float(spot))
        if best_key is None or key < best_key:
            best, best_key = leg, key
    return best
