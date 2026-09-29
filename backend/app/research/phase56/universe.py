"""What the source says the NSE cash-equity universe is — and when it says it.

One fact governs this module: the published scrip master is a *snapshot of what
is tradable now*. It carries token, symbol, name, lot and tick, and it carries no
listing date, no delisting date, no status field and no corporate-action history.
So the universe it yields is dated at the moment it was fetched, and a study that
treats it as the universe of 2021 has silently selected for survival.

The module therefore returns two universes and never merges them:

* ``CURRENT_SURVIVOR_UNIVERSE`` — the master's ``-EQ`` rows, which is all the
  source supports. §29 calls this diagnostic only;
* ``HISTORICALLY_CORRECT_UNIVERSE`` — the one §29 requires for the conclusion,
  returned empty with the reason attached, because no date-aware membership or
  delisting information exists to build it from.

Everything is derived from a passed-in master list, so the whole module is
testable without network access.
"""
from __future__ import annotations

import datetime as dt

from app.research.phase56 import (
    ALL_SECURITY_STATUSES,
    EQ_SUFFIX,
    NSE,
    STATUS_ACTIVE,
)

CURRENT_SURVIVOR = "CURRENT_SURVIVOR_UNIVERSE"
HISTORICALLY_CORRECT = "HISTORICALLY_CORRECT_UNIVERSE"

#: Fields §2/§6 require per security, mapped to whether the master supplies them.
REQUIRED_SECURITY_FIELDS = (
    "symbol",
    "token",
    "first_trade_date",
    "last_known_trade_date",
    "listing_status",
    "delisting_status",
    "corporate_action_status",
)

MASTER_SUPPLIED_FIELDS = ("symbol", "token")


def _nse_rows(master: list[dict]) -> list[dict]:
    return [row for row in master if (row.get("exch_seg") or "").upper() == NSE]


def equity_rows(master: list[dict]) -> list[dict]:
    """The cash-equity rows, deduplicated on token.

    Matched on the ``-EQ`` trading series rather than on a blank instrument type:
    NSE publishes ETFs, government securities, SME scrips, bonds and T-bills in
    the same segment with the same blank type, and pooling those into an equity
    universe would put a treasury bill in a momentum ranking.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for row in _nse_rows(master):
        symbol = (row.get("symbol") or "").upper()
        if not symbol.endswith(EQ_SUFFIX):
            continue
        token = str(row.get("token"))
        if token in seen:
            continue
        seen.add(token)
        out.append(
            {
                "symbol": symbol[: -len(EQ_SUFFIX)],
                "trading_symbol": symbol,
                "token": token,
                "exchange": NSE,
                "tick_size": row.get("tick_size"),
                "lot_size": row.get("lotsize"),
                # The only status the source can justify. Assigned as of the
                # fetch date, not as of any historical date.
                "status": STATUS_ACTIVE,
                "status_as_of": None,
                "first_trade_date": None,
                "last_known_trade_date": None,
                "delisting_status": None,
                "corporate_action_status": None,
            }
        )
    out.sort(key=lambda r: r["symbol"])
    return out


def other_nse_series(master: list[dict]) -> dict[str, int]:
    """Counts by trading series, so the exclusions above are auditable."""
    counts: dict[str, int] = {}
    for row in _nse_rows(master):
        symbol = (row.get("symbol") or "").upper()
        series = symbol.rsplit("-", 1)[-1] if "-" in symbol else "<none>"
        counts[series] = counts.get(series, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def status_assignability() -> list[dict]:
    """Which of the seven §2 statuses the source can actually assign, and why.

    ``INSUFFICIENT_HISTORY`` is derivable because it depends only on the candles
    we can fetch. Every other non-active status depends on an event the source
    never publishes.
    """
    out = []
    for status in ALL_SECURITY_STATUSES:
        if status == STATUS_ACTIVE:
            assignable, reason = True, "present in the master at fetch time"
        elif status == "INSUFFICIENT_HISTORY":
            assignable, reason = (
                True,
                "derivable from the candle count the provider returns",
            )
        else:
            assignable, reason = (
                False,
                "the master publishes no status, no delisting date and no "
                "corporate-action record; the symbol is simply absent once it "
                "stops being tradable, which is indistinguishable from never "
                "having existed",
            )
        out.append({"status": status, "assignable": assignable, "reason": reason})
    return out


def field_availability() -> list[dict]:
    """Per-security field availability against §2/§6."""
    return [
        {
            "field": field,
            "available": field in MASTER_SUPPLIED_FIELDS,
            "source": "scrip master" if field in MASTER_SUPPLIED_FIELDS else None,
        }
        for field in REQUIRED_SECURITY_FIELDS
    ]


def membership_sources(master: list[dict]) -> list[dict]:
    """§3's preferred source hierarchy, each checked rather than assumed.

    The hierarchy is: historical listed-equity universe, then historical NIFTY
    500 membership, then historical F&O equity universe. All three are *dated*
    memberships. What the master supplies in each case is the present-day set,
    which is the one thing a survivorship control cannot use.
    """
    nfo_names = {
        (row.get("name") or "").upper()
        for row in master
        if (row.get("exch_seg") or "").upper() == "NFO"
        and (row.get("instrumenttype") or "").upper().startswith("FUT")
    }
    index_rows = [
        row
        for row in _nse_rows(master)
        if (row.get("instrumenttype") or "").upper() == "AMXIDX"
    ]
    index_names = sorted({(row.get("name") or "").upper() for row in index_rows})
    nifty500 = [n for n in index_names if "500" in n]
    return [
        {
            "rank": 1,
            "source": "HISTORICAL_NSE_LISTED_EQUITY_UNIVERSE",
            "dated_membership_available": False,
            "present_day_count": len(equity_rows(master)),
            "reason": (
                "the master is a present-day snapshot; it has no membership "
                "start or end date, so no historical as-of universe is derivable"
            ),
        },
        {
            "rank": 2,
            "source": "HISTORICAL_NIFTY_500_MEMBERSHIP",
            "dated_membership_available": False,
            "present_day_count": None,
            "reason": (
                "the provider publishes the index price series"
                + (f" ({', '.join(nifty500)})" if nifty500 else " but no 500 series")
                + ", never its constituent list, current or historical"
            ),
        },
        {
            "rank": 3,
            "source": "HISTORICAL_FNO_EQUITY_UNIVERSE",
            "dated_membership_available": False,
            "present_day_count": len(nfo_names),
            "reason": (
                "derivable for today from live NFO futures rows; the F&O ban "
                "list and inclusion/exclusion dates are not published here, and "
                "an expired contract is not nameable from this provider"
            ),
        },
    ]


def universes(master: list[dict], as_of: dt.date | None = None) -> dict:
    """Both universes of §29, kept apart."""
    rows = equity_rows(master)
    return {
        "as_of": (as_of or dt.date.today()).isoformat(),
        CURRENT_SURVIVOR: {
            "securities": len(rows),
            "usable_for_conclusion": False,
            "role": "DIAGNOSTIC_ONLY (§29 B)",
        },
        HISTORICALLY_CORRECT: {
            "securities": 0,
            "usable_for_conclusion": True,
            "role": "REQUIRED_FOR_CONCLUSION (§29 A)",
            "reason": (
                "cannot be constructed: no dated membership, no delisting "
                "dates, and no record of securities that stopped trading"
            ),
        },
    }
