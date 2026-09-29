"""Which token carries each instrument's history, and why.

Three resolution rules, one per instrument class, each stated rather than
inferred:

* **index** — the scrip master publishes one ``AMXIDX`` row per index on NSE,
  matched on the published ``name`` (``NIFTY``, ``BANKNIFTY``, ``FINNIFTY``,
  ``MIDCPNIFTY``). A cash index has no contract and no roll, so the token is the
  series;
* **equity** — the NSE cash row, matched on ``name`` with the ``-EQ`` trading
  symbol. Also roll-free. The instrument is costed at its F&O lot later, but the
  *price series* collected here is the cash series, and that difference is
  recorded in the provenance rather than glossed;
* **MCX futures** — no expired contract is resolvable from this provider (the
  mcxhist stage measured that). The token that answers windows preceding its own
  listing is the one carrying the root-level series, and it is labelled
  ``PROVIDER_CONTINUOUS_UNVERIFIED_ROLL``. Finding it costs one probe request
  per listed contract.

An instrument that resolves to no token, or to more than one candidate the rule
cannot choose between, is returned unresolved with the reason attached. It is
never resolved by guessing the closest name.
"""
from __future__ import annotations

import datetime as dt

from app.research.mcxhist import contracts as mcxcontracts
from app.research.mcxhist import probe as mcxprobe
from app.research.phase55 import (
    CLASS_EQUITY,
    CLASS_INDEX,
    CLASS_MCX,
    SERIES_CLASS_CASH,
    SERIES_CLASS_FUTURES,
    UNIVERSE,
)

RESOLVED = "RESOLVED"
UNRESOLVED = "UNRESOLVED"

INDEX_INSTRUMENT_TYPE = "AMXIDX"


def _rows_for(master: list[dict], name: str, exchange: str) -> list[dict]:
    return [
        row
        for row in master
        if (row.get("name") or "").upper() == name.upper()
        and (row.get("exch_seg") or "").upper() == exchange.upper()
    ]


def resolve_index(master: list[dict], instrument: str, exchange: str) -> dict:
    candidates = [
        row
        for row in _rows_for(master, instrument, exchange)
        if (row.get("instrumenttype") or "").upper() == INDEX_INSTRUMENT_TYPE
    ]
    if len(candidates) != 1:
        return _unresolved(
            instrument,
            f"{len(candidates)} AMXIDX rows on {exchange} carry the name "
            f"{instrument!r}; the rule resolves exactly one or nothing",
        )
    row = candidates[0]
    return {
        "instrument": instrument,
        "status": RESOLVED,
        "token": str(row.get("token")),
        "trading_symbol": row.get("symbol"),
        "exchange": exchange,
        "expiry": None,
        "series_class": SERIES_CLASS_CASH,
        "basis": "cash index: one published AMXIDX row, no contract, no roll",
    }


def resolve_equity(master: list[dict], instrument: str, exchange: str) -> dict:
    candidates = [
        row
        for row in _rows_for(master, instrument, exchange)
        if (row.get("symbol") or "").upper().endswith("-EQ")
    ]
    if len(candidates) != 1:
        return _unresolved(
            instrument,
            f"{len(candidates)} '-EQ' rows on {exchange} carry the name "
            f"{instrument!r}; the rule resolves exactly one or nothing",
        )
    row = candidates[0]
    return {
        "instrument": instrument,
        "status": RESOLVED,
        "token": str(row.get("token")),
        "trading_symbol": row.get("symbol"),
        "exchange": exchange,
        "expiry": None,
        "series_class": SERIES_CLASS_CASH,
        "basis": "cash equity: the NSE '-EQ' row, no contract, no roll",
    }


def resolve_mcx(
    master: list[dict],
    instrument: str,
    exchange: str,
    fetch,
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> dict:
    """Probe the listed contracts and take the one carrying the long series."""
    listed = mcxcontracts.listed_futures(master, instrument, exchange)
    if not listed:
        return _unresolved(instrument, f"no futures rows published for {instrument} on {exchange}")
    probed = mcxprobe.probe_root(
        fetch, instrument, exchange, listed,
        windows=(mcxprobe.DEFAULT_PROBE_WINDOWS[0],),
    )
    inv = mcxcontracts.inventory(
        master,
        instrument,
        exchange,
        start or dt.date(2021, 8, 2),
        end or dt.date.today(),
        mcxprobe.reachability(probed),
    )
    chosen = mcxcontracts.history_token(inv)
    if chosen is None:
        return _unresolved(
            instrument,
            f"{inv['contracts_reachable']} of {inv['contracts_listed']} listed "
            "contracts answer pre-listing windows; the rule needs exactly one "
            "token carrying the root series",
            extra={"probe_verdict": probed["verdict"], "inventory": inv},
        )
    return {
        "instrument": instrument,
        "status": RESOLVED,
        "token": chosen["token"],
        "trading_symbol": chosen["trading_symbol"],
        "exchange": exchange,
        "expiry": chosen["expiry"],
        "series_class": SERIES_CLASS_FUTURES,
        "basis": (
            "provider root-level series under one live token; no expired "
            "contract identity is exposed, so the roll is the provider's and "
            "is not verifiable from this data"
        ),
        "probe_verdict": probed["verdict"],
        "contracts_listed": inv["contracts_listed"],
        "contracts_unresolvable": inv["contracts_unresolvable"],
    }


def _unresolved(instrument: str, reason: str, extra: dict | None = None) -> dict:
    out = {
        "instrument": instrument,
        "status": UNRESOLVED,
        "token": None,
        "trading_symbol": None,
        "exchange": UNIVERSE.get(instrument, ("", "", 0))[1],
        "expiry": None,
        "series_class": None,
        "basis": reason,
    }
    out.update(extra or {})
    return out


def resolve(
    master: list[dict],
    instruments: list[str],
    fetch=None,
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> list[dict]:
    """Resolve every requested instrument, recording failures rather than dropping them."""
    out: list[dict] = []
    for instrument in instruments:
        spec = UNIVERSE.get(instrument)
        if spec is None:
            out.append(_unresolved(instrument, "not in the declared universe"))
            continue
        klass, exchange, _minutes = spec
        if klass == CLASS_INDEX:
            out.append(resolve_index(master, instrument, exchange))
        elif klass == CLASS_EQUITY:
            out.append(resolve_equity(master, instrument, exchange))
        elif klass == CLASS_MCX:
            if fetch is None:
                out.append(_unresolved(
                    instrument,
                    "MCX resolution needs a fetcher: the carrying token is "
                    "identified by probing, not by the master",
                ))
            else:
                out.append(resolve_mcx(
                    master, instrument, exchange, fetch, start=start, end=end
                ))
        else:  # pragma: no cover - the universe declares only three classes
            out.append(_unresolved(instrument, f"unknown instrument class {klass!r}"))
        out[-1]["instrument_class"] = klass
    return out
