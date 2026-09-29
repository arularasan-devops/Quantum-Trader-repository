"""Instrument families and vehicle categories — Phase 12 §3.

Pooling a Nifty option with a Kalyan Jewellers option and reporting one win rate
is how the 25 Aug loss stayed invisible: the medians were 0.80% of premium on
index, 1.61% on MCX and 9.52% on single stocks, and 95 of 109 shadow closes were
single stocks carrying -Rs 3,75,258 of a -Rs 3,91,302 loss. Every research
aggregate in Phase 12 is keyed by family so that cannot happen again.

Classification is read from the instrument registry (exchange plus the index
roots), never guessed from the symbol text, and an instrument the registry does
not know is ``UNKNOWN`` rather than quietly filed as a stock.
"""
from __future__ import annotations

from app.market.instruments import REGISTRY

INDEX = "INDEX"
MCX = "MCX"
STOCK = "STOCK"
UNKNOWN = "UNKNOWN"
FAMILIES = (INDEX, MCX, STOCK)

OPTIONS = "OPTIONS"
FUTURES = "FUTURES"
VEHICLES = (OPTIONS, FUTURES)

# Index roots, i.e. the F&O underlyings that are not a single company. The
# registry cannot be asked this: an index option and a stock option both sit on
# NFO/BFO.
INDEX_ROOTS: frozenset[str] = frozenset({
    "NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY", "FINNIFTY", "BANKEX",
})

# The named slices §9 and §10 ask for, in report order.
INDEX_STUDY: tuple[str, ...] = (
    "NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY", "FINNIFTY")
MCX_STUDY: tuple[str, ...] = (
    "CRUDEOIL", "NATURALGAS", "GOLD", "SILVER", "COPPER")


def family(instrument: str) -> str:
    """INDEX / MCX / STOCK, or UNKNOWN when the registry cannot say."""
    key = (instrument or "").upper()
    if key in INDEX_ROOTS:
        return INDEX
    spec = REGISTRY.get(key)
    if spec is None:
        return UNKNOWN
    if spec.exchange.upper() == "MCX":
        return MCX
    if spec.exchange.upper() in ("NFO", "BFO"):
        return STOCK
    return UNKNOWN


def members(fam: str) -> tuple[str, ...]:
    """Registry names in a family, so a study cannot silently miss instruments."""
    return tuple(sorted(k for k in REGISTRY if family(k) == fam))


def group_by_family(rows: list[dict], key: str = "instrument") -> dict[str, list[dict]]:
    """Split rows by family, keeping UNKNOWN as its own bucket."""
    out: dict[str, list[dict]] = {f: [] for f in (*FAMILIES, UNKNOWN)}
    for row in rows:
        out[family(str(row.get(key) or ""))].append(row)
    return out
