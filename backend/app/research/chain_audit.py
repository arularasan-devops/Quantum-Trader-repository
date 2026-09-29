"""Does the captured option chain actually carry a book? Measure it, don't assume.

Every "net" number in this system rests on one question: was the spread we charged
a leg the spread the market quoted, or a family median we invented? The Flow cost
audit found 0 of 1,169 recorded legs carrying a quoted book, which has exactly two
possible explanations — the feed never sent one, or capture never stored it — and
the two lead to opposite fixes. This module answers that from the stored rows
instead of arguing about it.

It reads only what capture already writes (``app.research.capture`` →
``mcx_options``) and reports, per instrument:

* how many option snapshots exist and over what span;
* how many came from a real broker feed vs the simulator vs an unknown legacy row;
* how many carry a usable bid AND ask, and what those spreads look like;
* how many carry the greeks the economics gate needs (delta) and IV/OI;
* how many rows would therefore be costed ``MEASURED`` vs ``ASSUMED_FAMILY_MEDIAN``.

A row is only counted as quoted when bid and ask are both present, both positive
and ask >= bid. A zero or crossed book is counted as unusable rather than as a
free spread, because a zero spread is the single most flattering number in the
whole cost model and it is never real.
"""
from __future__ import annotations

from statistics import median

from app.research.store import (
    SOURCE_REAL,
    SOURCE_SIM,
    SOURCE_UNKNOWN,
    ResearchStore,
    store,
)

MEASURED = "MEASURED"
ASSUMED = "ASSUMED_FAMILY_MEDIAN"


def _pct(n: int, total: int) -> float | None:
    return round(100.0 * n / total, 2) if total else None


def usable_spread(bid: object, ask: object) -> float | None:
    if not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)):
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return float(ask) - float(bid)


def option_rows(instrument: str, *, st: ResearchStore | None = None,
                limit: int = 500_000) -> list[dict]:
    st = st or store()
    return st.db.query(
        "SELECT ts, strike, option_type, close, volume, oi, iv, delta, bid, ask, "
        "source FROM mcx_options WHERE instrument=? ORDER BY ts LIMIT ?",
        (instrument, int(limit)),
    )


def audit(instrument: str, *, st: ResearchStore | None = None) -> dict:
    """Coverage of the captured chain for one instrument. Reads only; never writes."""
    rows = option_rows(instrument, st=st)
    total = len(rows)
    out: dict = {
        "instrument": instrument,
        "option_rows": total,
        "snapshots": 0,
        "first_ts": None,
        "last_ts": None,
        "by_source": {},
        "quoted_rows": 0,
        "quoted_pct": None,
        "unusable_book_rows": 0,
        "no_book_rows": 0,
        "spread_points_median": None,
        "spread_pct_of_premium_median": None,
        "delta_rows": 0,
        "iv_rows": 0,
        "oi_rows": 0,
        "cost_basis": {MEASURED: 0, ASSUMED: 0},
        "verdict": "NO_CAPTURE",
        "note": "",
    }
    if total == 0:
        out["note"] = (
            "no option chain has been captured for this instrument, so every cost "
            "in the system is an assumed family median and no net result can be "
            "called measured"
        )
        return out

    stamps = {int(r["ts"]) for r in rows if r.get("ts") is not None}
    out["snapshots"] = len(stamps)
    out["first_ts"] = min(stamps) if stamps else None
    out["last_ts"] = max(stamps) if stamps else None

    counts = {SOURCE_REAL: 0, SOURCE_SIM: 0, SOURCE_UNKNOWN: 0}
    spreads: list[float] = []
    spread_pcts: list[float] = []
    for r in rows:
        src = r.get("source") or SOURCE_UNKNOWN
        counts[src] = counts.get(src, 0) + 1
        if isinstance(r.get("delta"), (int, float)):
            out["delta_rows"] += 1
        if isinstance(r.get("iv"), (int, float)):
            out["iv_rows"] += 1
        if isinstance(r.get("oi"), (int, float)):
            out["oi_rows"] += 1
        bid, ask = r.get("bid"), r.get("ask")
        sp = usable_spread(bid, ask)
        if sp is not None:
            out["quoted_rows"] += 1
            spreads.append(sp)
            premium = r.get("close")
            if isinstance(premium, (int, float)) and premium > 0:
                spread_pcts.append(100.0 * sp / float(premium))
        elif bid is None and ask is None:
            out["no_book_rows"] += 1
        else:
            out["unusable_book_rows"] += 1

    out["by_source"] = {k: {"rows": v, "pct": _pct(v, total)}
                        for k, v in counts.items() if v}
    out["quoted_pct"] = _pct(out["quoted_rows"], total)
    out["cost_basis"] = {MEASURED: out["quoted_rows"],
                         ASSUMED: total - out["quoted_rows"]}
    if spreads:
        out["spread_points_median"] = round(median(spreads), 3)
    if spread_pcts:
        out["spread_pct_of_premium_median"] = round(median(spread_pcts), 3)

    real = counts.get(SOURCE_REAL, 0)
    if real == 0:
        out["verdict"] = "SIMULATED_ONLY"
        out["note"] = ("rows exist but none came from a broker feed, so nothing "
                       "here can price a real fill")
    elif out["quoted_rows"] == 0:
        out["verdict"] = "REAL_BUT_NO_BOOK"
        out["note"] = ("the broker feed is being captured but it carried no usable "
                       "bid/ask, so spread stays an assumed family median")
    elif out["quoted_pct"] is not None and out["quoted_pct"] < 50.0:
        out["verdict"] = "PARTIAL_BOOK"
        out["note"] = ("under half the captured rows carry a usable book; costs "
                       "are measured only on those and assumed elsewhere")
    else:
        out["verdict"] = "BOOK_AVAILABLE"
        out["note"] = ("a usable book is present on most captured rows, so option "
                       "costs can be measured rather than assumed")
    return out


def audit_all(instruments: list[str], *, st: ResearchStore | None = None) -> dict:
    st = st or store()
    per = [audit(name, st=st) for name in instruments]
    rows = sum(a["option_rows"] for a in per)
    quoted = sum(a["quoted_rows"] for a in per)
    return {
        "instruments": per,
        "totals": {
            "option_rows": rows,
            "quoted_rows": quoted,
            "quoted_pct": _pct(quoted, rows),
            "cost_basis": {MEASURED: quoted, ASSUMED: rows - quoted},
        },
        "reading": (
            "a measured cost is one the market quoted; an assumed one is a family "
            "median standing in for a book we never saw. Only the first can call a "
            "result net."
        ),
    }
