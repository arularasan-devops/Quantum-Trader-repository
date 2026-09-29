"""Phase 33 §11/§13 — option cohorts, and the honest refusal to price most of them.

§11 asks for CE/PE, moneyness, DTE, expiry-day and family cohorts; §13 asks for
futures against CE against PE at the same instant. Both are answerable only where
a real two-sided quote exists at the entry *and* the exit instant, because §4
forbids every substitute: no midpoint, no LTP, no nearby timestamp, no estimated
spread, no family median.

So this module does the one useful thing it can do with the evidence that exists:
it splits the board pool into the requested cohorts, counts real two-sided rows in
each, and returns ``REQUIRES_MORE_DATA`` for every cohort under the floor rather
than a number that would read like a measurement. The counts are the deliverable —
they say exactly how much capture is still needed before §11 can be answered.
"""
from __future__ import annotations

import datetime as dt

from app.research.phase33 import (
    MEASURED,
    MIN_OPTION_INSTANTS,
    REQUIRES_MORE_DATA,
    UNMEASURED,
)

CE, PE = "CE", "PE"
IST_OFFSET_SEC = 19_800  # the board timestamps are epoch seconds; sessions are IST

# Moneyness bands as a fraction of the underlying, frozen before any result.
NEAR_ATM_PCT = 0.5
OTM_PCT = 2.0


def _day(ts: int) -> str:
    return dt.datetime.utcfromtimestamp(ts + IST_OFFSET_SEC).strftime("%Y-%m-%d")


def moneyness(strike: float | None, underlying: float | None, kind: str) -> str:
    """ATM / NEAR_ATM / OTM / ITM, or UNKNOWN without an underlying reference.

    Guessing the underlying from the option itself is what §4 forbids, so a row
    with no reference price is ``UNKNOWN`` and lands in no band.
    """
    if strike is None or underlying is None or underlying <= 0:
        return "UNKNOWN"
    diff_pct = 100.0 * (float(strike) - float(underlying)) / float(underlying)
    if abs(diff_pct) <= NEAR_ATM_PCT:
        return "ATM"
    out_of_money = diff_pct > 0 if kind == CE else diff_pct < 0
    if not out_of_money:
        return "ITM"
    return "OTM" if abs(diff_pct) >= OTM_PCT else "NEAR_ATM"


def split(board: list[dict], underlying: dict[tuple[str, int], float]) -> dict:
    """Cohort counts over the raw board pool, with per-cohort answerability.

    ``underlying`` maps ``(instrument, minute)`` to a measured one-minute close and
    is used only to label moneyness; a missing entry leaves the row ``UNKNOWN``
    rather than assigning it a band.
    """
    cohorts: dict[str, dict[str, dict]] = {
        "option_type": {}, "moneyness": {}, "session": {}, "instrument": {},
    }

    def bump(dim: str, key: str, row: dict) -> None:
        cell = cohorts[dim].setdefault(key, {"rows": 0, "two_sided": 0})
        cell["rows"] += 1
        if row["pricing"] == MEASURED:
            cell["two_sided"] += 1

    for r in board:
        kind = (r.get("option_type") or "").upper()
        minute = r["ts"] - (r["ts"] % 60)
        und = underlying.get((r["instrument"], minute))
        bump("option_type", kind or "UNKNOWN", r)
        bump("moneyness", moneyness(r.get("strike"), und, kind), r)
        bump("session", _day(r["ts"]), r)
        bump("instrument", r["instrument"], r)

    for cells in cohorts.values():
        for cell in cells.values():
            cell["status"] = (
                MEASURED if cell["two_sided"] >= MIN_OPTION_INSTANTS
                else REQUIRES_MORE_DATA
            )
            cell["shortfall_two_sided_rows"] = max(
                MIN_OPTION_INSTANTS - cell["two_sided"], 0
            )

    total_two_sided = sum(
        c["two_sided"] for c in cohorts["option_type"].values()
    )
    return {
        "cohorts": cohorts,
        "total_rows": len(board),
        "total_two_sided_rows": total_two_sided,
        "min_required_per_cohort": MIN_OPTION_INSTANTS,
        "premium_hold_status": (
            MEASURED if total_two_sided >= MIN_OPTION_INSTANTS
            else REQUIRES_MORE_DATA
        ),
        "dte_status": UNMEASURED,
        "dte_reason": (
            "the board rows carry no expiry column, so DTE and expiry-day cohorts "
            "cannot be formed from stored evidence; capturing expiry alongside the "
            "quote is what makes this cohort answerable"
        ),
        "vehicle_comparison_status": REQUIRES_MORE_DATA,
        "vehicle_comparison_reason": (
            "futures-versus-CE-versus-PE needs a real two-sided option quote at the "
            "same instant as the futures quote, at entry and at exit; the captured "
            f"window has {total_two_sided} two-sided option rows in total"
        ),
    }
