"""Resolve the historical series to collect for an instrument. RESEARCH ONLY.

The rule is deliberately explicit rather than clever, because collecting the
wrong token silently produces a beautiful backtest of the wrong market:

* an **index** (NIFTY, BANKNIFTY, SENSEX) is the cash index row — instrument type
  ``AMXIDX``, matched on the scrip master's ``name``;
* an **equity** (RELIANCE, SBIN, ...) is its NSE cash row — instrument type empty
  and symbol exactly ``<NAME>-EQ``;
* an **MCX commodity** has no cash series at all. ``SILVER`` in the NSE cash
  segment is a listed *company*, not the metal, so a cash lookup for an MCX name
  is refused outright and the caller is told to use the futures contract, whose
  history only reaches back to that contract's listing.

Nothing here logs or persists credentials; the scrip master is a public file.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.market.instruments import REGISTRY, InstrumentSpec

INDEX_TYPE = "AMXIDX"
CASH_SEGMENTS = ("NSE", "BSE")

# Segment the cash series of an index lives in, by the derivative exchange the
# instrument trades on.
_INDEX_SEGMENT = {"NFO": "NSE", "BFO": "BSE"}

KIND_INDEX = "INDEX"
KIND_EQUITY = "EQUITY"
KIND_FUTURES = "FUTURES"


@dataclass(frozen=True)
class Series:
    """One historical series: what to ask Angel for, and what it actually is."""

    instrument: str
    kind: str
    exchange: str
    token: str
    symbol: str
    note: str = ""


class SeriesUnavailable(RuntimeError):
    """No historical *underlying* series exists for this instrument."""


def _spec(instrument: str) -> InstrumentSpec:
    """The spec for ``instrument``, refusing the registry's default fallback.

    ``get_spec`` answers an unknown name with the default instrument, which is the
    right thing for a dashboard and the wrong thing here: it would collect Crude
    Oil history under a typo'd name and nothing downstream would know.
    """
    spec = REGISTRY.get((instrument or "").upper())
    if spec is None:
        raise SeriesUnavailable(f"{instrument!r} is not a registered instrument")
    return spec


def _index_series(spec: InstrumentSpec, master: list[dict]) -> Series | None:
    segment = _INDEX_SEGMENT.get(spec.exchange)
    if not segment:
        return None
    for row in master:
        if row.get("exch_seg") != segment:
            continue
        if (row.get("instrumenttype") or "").upper() != INDEX_TYPE:
            continue
        if (row.get("name") or "").upper() != spec.symbol.upper():
            continue
        return Series(spec.symbol, KIND_INDEX, segment, str(row.get("token")),
                      str(row.get("symbol") or ""),
                      "cash index; the derivative's own contracts are not fetchable")
    return None


def _equity_series(spec: InstrumentSpec, master: list[dict]) -> Series | None:
    want = f"{spec.symbol.upper()}-EQ"
    for row in master:
        if row.get("exch_seg") != "NSE":
            continue
        if (row.get("instrumenttype") or "").upper():
            continue
        if (row.get("symbol") or "").upper() != want:
            continue
        return Series(spec.symbol, KIND_EQUITY, "NSE", str(row.get("token")),
                      str(row.get("symbol") or ""),
                      "NSE cash series; option premiums are not fetchable")
    return None


def resolve(instrument: str, master: list[dict]) -> Series:
    """The underlying series for ``instrument``, or raise :class:`SeriesUnavailable`.

    ``master`` is the Angel scrip master as a list of rows; it is passed in so a
    collection run downloads it once and a test can supply a handful of rows.
    """
    spec = _spec(instrument)
    if spec.exchange == "MCX":
        raise SeriesUnavailable(
            f"{spec.symbol} is an MCX commodity and has no cash series. A same-named "
            f"row in the NSE cash segment is a different asset (a listed company) "
            f"and must not be collected as its history; use the current futures "
            f"contract, which only reaches back to its listing."
        )
    found = _index_series(spec, master) or _equity_series(spec, master)
    if found is None:
        raise SeriesUnavailable(
            f"no cash {INDEX_TYPE}/-EQ row for {spec.symbol} in the scrip master "
            f"(derivative exchange {spec.exchange})"
        )
    return found


def resolve_many(instruments: list[str],
                 master: list[dict]) -> tuple[list[Series], dict[str, str]]:
    """Resolve a watchlist. Returns the series found and why the rest were not.

    Unresolvable names are returned, never dropped: a coverage report that hides
    the instruments it could not collect is how a book ends up validated on half
    the market without anyone noticing.
    """
    series: list[Series] = []
    skipped: dict[str, str] = {}
    for name in instruments:
        try:
            series.append(resolve(name, master))
        except SeriesUnavailable as exc:
            skipped[name] = str(exc)
    return series, skipped
