"""Contract inventory for an MCX root — what is reachable and what is not.

The inventory is deliberately two-sided. The reachable half is the contracts the
provider will name: those come from the scrip master, with token, trading symbol
and expiry preserved exactly as published. The unreachable half is the part that
matters for honesty: a five-year history needs roughly thirty GOLD and thirty
SILVER contracts, the master publishes only the live ones, and there is no
endpoint that names an expired MCX contract. Those are recorded, with the reason,
rather than left out of the count — an inventory that silently lists five
contracts implies five is all there were.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from app.research.mcxhist import (
    CONTRACT_NO_HISTORY,
    CONTRACT_REACHABLE,
    CONTRACT_UNRESOLVABLE,
)

#: () -> scrip master rows, as published.
MasterLoader = Callable[[], list[dict]]

#: (exchange, token, interval, from_date, to_date) -> provider candle rows.
Fetcher = Callable[[str, str, str, dt.date, dt.date], list[list]]

SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)


def parse_expiry(value: str) -> dt.date | None:
    """``05OCT2026`` -> date. Anything else is None, never a guess."""
    try:
        return dt.datetime.strptime((value or "").strip().upper(), "%d%b%Y").date()
    except Exception:
        return None


def load_master() -> list[dict]:
    """Fetch the published scrip master. Network; injected everywhere in tests."""
    import httpx

    return httpx.get(SCRIP_MASTER_URL, timeout=180.0).json()


def listed_futures(master: list[dict], root: str, exchange: str) -> list[dict]:
    """Futures rows for ``root`` on ``exchange``, exactly as published.

    The match is on the published ``name`` field rather than a prefix of the
    trading symbol, because ``GOLD`` and ``GOLDM``/``GOLDPETAL``/``GOLDTEN`` are
    different contracts with different lot sizes and a prefix match would pool
    them into one instrument.
    """
    out: list[dict] = []
    for row in master:
        if (row.get("name") or "") != root:
            continue
        if (row.get("exch_seg") or "").upper() != exchange.upper():
            continue
        if not (row.get("instrumenttype") or "").upper().startswith("FUT"):
            continue
        expiry = parse_expiry(row.get("expiry", ""))
        if expiry is None:
            continue
        out.append(
            {
                "root": root,
                "exchange": exchange.upper(),
                "trading_symbol": row.get("symbol"),
                "token": str(row.get("token")),
                "expiry": expiry.isoformat(),
                "lot_size": row.get("lotsize"),
                "tick_size": row.get("tick_size"),
            }
        )
    # Deduplicate on token: the master repeats rows, and a repeated row must not
    # become a second contract in the count.
    seen: dict[str, dict] = {}
    for row in out:
        seen.setdefault(row["token"], row)
    return sorted(seen.values(), key=lambda r: (r["expiry"], r["token"]))


def expected_contract_count(start: dt.date, end: dt.date, root: str) -> int:
    """How many contracts a genuine per-contract history would have needed.

    GOLD and SILVER both run a roughly bi-monthly expiry cycle on MCX, so the
    count is the number of two-month slots in the window. This is an expectation
    used to size the gap between what a real per-contract collection would
    require and what the provider will name — it is never used to invent a
    contract.
    """
    months = (end.year - start.year) * 12 + (end.month - start.month) + 1
    return max(1, round(months / 2))


def inventory(
    master: list[dict],
    root: str,
    exchange: str,
    start: dt.date,
    end: dt.date,
    reachability: dict[str, bool] | None = None,
) -> dict:
    """Reachable contracts, unreachable count, and the reason for each status.

    ``reachability`` maps token -> whether a pre-listing window returned rows,
    as measured by the probe. A token that answers pre-listing windows is
    carrying the root-level series; a token that does not has only its own
    listed life, which for a contract expiring in the future is no history at
    all.
    """
    listed = listed_futures(master, root, exchange)
    reachability = reachability or {}
    rows: list[dict] = []
    for row in listed:
        has_history = reachability.get(row["token"])
        if has_history is True:
            status, reason = CONTRACT_REACHABLE, (
                "token answers pre-listing windows: it carries the root-level "
                "series, not this contract's traded life"
            )
        elif has_history is False:
            status, reason = CONTRACT_NO_HISTORY, (
                "token answers only its own listed life; expiry is in the "
                "future so it contributes no history"
            )
        else:
            status, reason = CONTRACT_NO_HISTORY, "reachability not probed"
        rows.append({**row, "status": status, "reason": reason})

    expected = expected_contract_count(start, end, root)
    unresolvable = max(0, expected - len(rows))
    return {
        "root": root,
        "exchange": exchange.upper(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "contracts_listed": len(rows),
        "contracts_reachable": sum(1 for r in rows if r["status"] == CONTRACT_REACHABLE),
        "contracts_expected_for_window": expected,
        "contracts_unresolvable": unresolvable,
        "unresolvable_status": CONTRACT_UNRESOLVABLE,
        "unresolvable_reason": (
            "the scrip master and searchScrip publish live contracts only; no "
            "endpoint names an expired MCX contract, so these tokens cannot be "
            "resolved and their per-contract history cannot be requested"
        ),
        "contracts": rows,
    }


def history_token(inv: dict) -> dict | None:
    """The single reachable contract whose token carries the long series."""
    reachable = [r for r in inv["contracts"] if r["status"] == CONTRACT_REACHABLE]
    if len(reachable) != 1:
        return None
    return reachable[0]
