"""Phase 11 §3 — instrument families. RESEARCH ONLY.

The correction this phase is built on: pooling index options with MCX options
produced statements that were true of neither. They are different products with
different books, different lot sizes, different tick economics and different
expiries, and the only thing that made them look comparable was appearing in the
same column of the same table.

So a family is attached to every row, once, at the point the population is built,
and every downstream block groups by it. The classification is derived from the
production instrument registry rather than hand-listed per report, so a newly
configured instrument cannot silently land in the wrong table — and the two index
families NSE and BSE quote (NFO and BFO) are one family here because they are one
product type, not because their exchange strings match.
"""
from __future__ import annotations

from app.market.instruments import REGISTRY

INDEX_OPTIONS = "INDEX_OPTIONS"
INDEX_FUTURES = "INDEX_FUTURES"
MCX_OPTIONS = "MCX_OPTIONS"
MCX_FUTURES = "MCX_FUTURES"
OTHER = "OTHER"

FAMILIES = (INDEX_OPTIONS, INDEX_FUTURES, MCX_OPTIONS, MCX_FUTURES, OTHER)

# The vehicle a row describes. A row that does not state one is an option row,
# which is what every pre-Phase-11 row is: the futures branch is new here.
OPTION = "OPTION"
FUTURES = "FUTURES"

_BY_VEHICLE = {
    (True, OPTION): INDEX_OPTIONS,
    (True, FUTURES): INDEX_FUTURES,
    (False, OPTION): MCX_OPTIONS,
    (False, FUTURES): MCX_FUTURES,
}

# The index underlyings quoted as index options. Explicit because "is an index"
# is not derivable from the exchange: NFO carries both NIFTY and every single
# stock option, and a stock option is not an index option in any respect that
# matters to a spread.
INDEX_UNDERLYINGS = frozenset({
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX",
})

# §3's target deep watchlist. Held here as the *recommendation the evidence
# supports*, never as a default the code applies: the live list comes from
# QT_DEEP_WATCHLIST and nothing in research may set it.
RECOMMENDED_DEEP = ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY",
                    "CRUDEOIL", "NATURALGAS")


def family_of(instrument: str | None, vehicle: str | None = None) -> str:
    """The family of one instrument in one vehicle. Unknown names are OTHER.

    ``vehicle`` splits the same underlying into its options and its futures
    family, because the two are not the same product: NIFTY options and the
    NIFTY future share a direction and share nothing else that a spread, a lot
    size or an expiry cares about. Omitting it keeps the pre-Phase-11 meaning
    (options), so no existing caller changes behaviour.
    """
    sym = (instrument or "").strip().upper()
    if not sym:
        return OTHER
    veh = (vehicle or OPTION).strip().upper()
    if veh in ("CE", "PE", "CALL", "PUT"):
        veh = OPTION
    if veh not in (OPTION, FUTURES):
        return OTHER
    if sym in INDEX_UNDERLYINGS:
        return _BY_VEHICLE[(True, veh)]
    spec = REGISTRY.get(sym)
    if spec is not None and spec.exchange == "MCX":
        return _BY_VEHICLE[(False, veh)]
    return OTHER


def tag(rows: list[dict]) -> list[dict]:
    """Attach ``family`` in place and return the same list.

    Mutating rather than copying is deliberate: every later block reads the same
    row objects, and a copy would let one table be family-aware while another
    silently kept the untagged original.
    """
    for r in rows:
        r["family"] = family_of(r.get("instrument"), r.get("vehicle"))
    return rows


def split(rows: list[dict]) -> dict[str, list[dict]]:
    """``{family: rows}`` for every family, including the empty ones.

    Empty families are present on purpose: a missing table reads as "not
    applicable", and an empty one reads as "we looked and there was nothing",
    which is the true statement about, for example, BANKEX on this dataset.
    """
    out: dict[str, list[dict]] = {f: [] for f in FAMILIES}
    for r in rows:
        out.setdefault(
            r.get("family") or family_of(r.get("instrument"), r.get("vehicle")),
            []).append(r)
    return out


def members(instruments: list[str], vehicle: str = OPTION) -> dict[str, list[str]]:
    """Which of ``instruments`` belong to each family, order preserved."""
    out: dict[str, list[str]] = {f: [] for f in FAMILIES}
    for name in instruments:
        out[family_of(name, vehicle)].append(name)
    return out


def universe() -> dict[str, list[str]]:
    """Every registered instrument, by family. The population Phase 11 could
    analyse if it were all recorded, as distinct from what a given replay held."""
    out: dict[str, list[str]] = {f: [] for f in FAMILIES}
    for name in REGISTRY:
        for veh in (OPTION, FUTURES):
            out[family_of(name, veh)].append(name)
    return {k: sorted(set(v)) for k, v in out.items()}


def summary(rows: list[dict]) -> dict:
    """Row counts and instruments per family, for the report header."""
    by = split(rows)
    return {
        "families": list(FAMILIES),
        "recommended_deep_watchlist": list(RECOMMENDED_DEEP),
        "recommended_deep_by_family": members(list(RECOMMENDED_DEEP)),
        "registered_universe_by_family": universe(),
        "rows_by_family": {f: len(v) for f, v in by.items()},
        "instruments_by_family": {
            f: sorted({r["instrument"] for r in v}) for f, v in by.items()},
        "pooling_rule": "no metric in this phase is reported across families. The "
                        "pooled figures in earlier phases were MCX-weighted because "
                        "the default population was chosen by chain-snapshot count",
    }
