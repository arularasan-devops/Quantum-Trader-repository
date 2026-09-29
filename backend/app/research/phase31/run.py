"""Phase 31 — the authoritative run.

Assembles the three separated bases into one result: the measured five-year
underlying excursion, the measured realized premium of recorded calls, and the
labelled arithmetic bridge between them. Research only; nothing here is wired to
a decision, an order or a live gate.
"""
from __future__ import annotations

import time

from app.research.phase24 import data
from app.research.phase31 import (
    ASSUMED_PREMIUM_MAP,
    MEASURED_REALIZED,
    MEASURED_UNDERLYING,
    UNMEASURED,
    evidence,
    excursion,
    premium_map,
    realized,
)

INSTRUMENTS = ("NIFTY", "CRUDEOIL")


def _instrument(inst: str, ratio: dict) -> dict | None:
    s = data.load_series(inst)
    if s is None:
        return None
    out: dict = {
        "instrument": inst,
        "bars": len(s),
        "basis": MEASURED_UNDERLYING,
        "sides": {},
        "premium_map": {},
    }
    for side, name in ((excursion.LONG, "LONG"), (excursion.SHORT, "SHORT")):
        ex = excursion.build(s, side)
        summary = excursion.summarize(ex)
        out["sides"][name] = {
            "summary": summary,
            "by_period": excursion.by_period(ex),
        }
        out["premium_map"][name] = premium_map.table(
            inst, ratio, summary["thresholds"]
        )
        out["sessions"] = summary["sessions"]
    return out


def study() -> dict:
    """Run every measurement and return the complete result."""
    started = time.time()
    inv = evidence.inventory()
    real = realized.summary()
    ratio = real.get("premium_to_strike_ratio") or {}

    instruments = {}
    for inst in INSTRUMENTS:
        row = _instrument(inst, ratio)
        if row is not None:
            instruments[inst] = row

    return {
        "phase": 31,
        "scope": "RESEARCH_ONLY",
        "inventory": inv,
        "instruments": instruments,
        "realized": {"basis": MEASURED_REALIZED, **real},
        "premium_book_result": (
            UNMEASURED if not inv["premium_percentage_measurable"] else "MEASURED"
        ),
        "premium_book_reason": (
            f"{inv['real_two_sided_decision_instants']} decision instants in "
            f"this store carry a real two-sided premium; "
            f"{inv['min_required_for_premium_distribution']} are required "
            "before a premium distribution describes anything, so a premium "
            "percentage from stored chains is not reported"
        ),
        "bases_used": {
            MEASURED_UNDERLYING: "five-year one-minute series, real data",
            MEASURED_REALIZED: "recorded engine-resolved fills, small sample",
            ASSUMED_PREMIUM_MAP: "stated arithmetic, optimistic bound",
            UNMEASURED: "premium distribution from stored option chains",
        },
        "runtime_sec": round(time.time() - started, 1),
    }
