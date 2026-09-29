"""Phase 40 — the pass that assembles the diagnostic, read-only throughout.

Nothing in this module writes to the store, collects a quote, or reads a
production setting. It groups the raced legs by instrument, vehicle, cohort and
horizon, and hands each group to :mod:`app.research.phase40.metrics` and
:mod:`app.research.phase40.verdict` unchanged. CRUDEOIL is run first because
Phase 39 established its economics are measurable, and every other instrument
present is run by the same code with no threshold of its own — an instrument
either has enough classified legs for the floor or it is reported
:data:`app.research.phase40.INSUFFICIENT`.
"""
from __future__ import annotations

from app.research.phase40 import (
    BOARD,
    ENGINE,
    INSUFFICIENT,
    NO_RACEABLE_LEG,
    NOT_A_STRATEGY,
    NOT_IN_STORE,
    PAPER_ONLY,
    PHASE,
    READ_ONLY,
    REFERENCE,
    RESEARCH_ONLY,
    UNMEASURED,
    VERSION,
)
from app.research.phase40 import firstevent as fe
from app.research.phase40 import metrics as m
from app.research.phase40 import verdict as v

FIRST = "CRUDEOIL"

# §9 — the instruments the task named. They are listed whether or not the store
# holds them, because an instrument that silently vanishes from a report reads
# as one that was looked at and found wanting, which is the opposite of the
# truth when nothing was captured.
REQUESTED: tuple[str, ...] = (
    FIRST, "NIFTY", "BANKNIFTY", "SENSEX", "GOLD", "SILVER", "COPPER",
    "NATURALGAS",
)


def _in_store(con, *, instrument: str | None) -> list[str]:
    """Distinct instruments the raw observations carry. Read-only."""
    sql = "SELECT DISTINCT instrument FROM raw_observation"
    args: tuple = ()
    if instrument:
        sql += " WHERE instrument = ?"
        args = (instrument,)
    return sorted(
        str(r[0]) for r in con.execute(sql, args).fetchall() if r[0]
    )


def _unmeasured(
    con, *, measured: list[str], instrument: str | None,
) -> list[dict]:
    """§2/§9 — every named or stored instrument that produced no raced leg.

    The distinction kept here is the one the task asked for: an instrument the
    capture never held is not the same finding as one whose quotes were held
    and could not be raced. Neither is evidence about the instrument.
    """
    stored = _in_store(con, instrument=instrument)
    names = [
        n for n in dict.fromkeys((*REQUESTED, *stored))
        if n not in measured and (instrument is None or n == instrument)
    ]
    return [
        {
            "instrument": name,
            "status": UNMEASURED,
            "reason": NO_RACEABLE_LEG if name in stored else NOT_IN_STORE,
            "verdict": INSUFFICIENT,
        }
        for name in _order(names)
    ]


def _order(instruments: list[str]) -> list[str]:
    """CRUDEOIL first (§9), then the rest alphabetically."""
    rest = sorted(i for i in instruments if i != FIRST)
    return ([FIRST] if FIRST in instruments else []) + rest


def _thresholds(legs: list[dict]) -> dict:
    """§4 — the predeclared distance, reported per vehicle as it was applied.

    Each leg's threshold is its own book's required move, so this is a summary
    of distances already used rather than a distance chosen for the group. The
    median is printed because a single number is what a reader will quote.
    """
    out: dict[str, dict] = {}
    for vehicle, rows in m.by_key(legs, "vehicle").items():
        pcts = [float(r["required_pct"]) for r in rows]
        out[vehicle] = {
            "cost_clearing_move_pct_median": m.median(pcts),
            "adverse_move_pct_median": (
                None if not pcts else -1.0 * float(m.median(pcts) or 0.0)
            ),
            "equal_sized": True,
            "n": len(rows),
            "source": "PHASE39_REQUIRED_MOVE_SPREAD_PLUS_FEES_PER_INSTANT",
        }
    return out


def _vehicle_block(legs: list[dict]) -> dict:
    horizons = [m.horizon_row(legs, horizon=h) for h in fe.HORIZON_KEYS]
    return {
        "n": len(legs),
        "sessions": sorted({str(x["session"]) for x in legs}),
        "horizons": horizons,
        "giveback": [
            m.giveback_row(legs, horizon=h) for h in fe.HORIZON_KEYS
        ],
        "adverse_first": [
            m.adverse_row(legs, horizon=h) for h in fe.HORIZON_KEYS
        ],
        "waterfall": m.waterfall(legs, horizon=REFERENCE),
        "verdict": v.classify(legs, horizon=REFERENCE),
        # The same verdict on windows that do not overlap each other. A
        # disagreement between the two is a statement about the sample, and it
        # is left visible rather than reconciled.
        "verdict_non_overlapping": v.classify(
            fe.independent(legs, horizon=REFERENCE), horizon=REFERENCE,
        ),
    }


def _cohorts(legs: list[dict]) -> dict:
    """§10 — ENGINE_SELECTED against BOARD_ONLY, where both were recorded."""
    split = m.by_key(legs, "cohort")
    out: dict[str, dict] = {}
    for cohort in (ENGINE, BOARD):
        rows = split.get(cohort, [])
        # An absent cohort keeps the same shape as a measured one, carrying the
        # INSUFFICIENT_DATA verdict its own emptiness earns. A reader — or a
        # caller — should not have to branch on whether the engine happened to
        # select anything in this store.
        out[cohort] = {
            "n": len(rows),
            "status": "MEASURED" if rows else UNMEASURED,
            "by_vehicle": {
                vehicle: {
                    "row": m.horizon_row(sub, horizon=REFERENCE),
                    "giveback": m.giveback_row(sub, horizon=REFERENCE),
                }
                for vehicle, sub in sorted(m.by_key(rows, "vehicle").items())
            },
            "verdict": v.classify(rows, horizon=REFERENCE),
        }
    both = all(out[c]["n"] > 0 for c in (ENGINE, BOARD))
    out["comparable"] = both
    out["note"] = (
        "Both cohorts measured; first-event shares are directly comparable."
        if both else
        "Only one cohort is present in this store, so the engine-versus-board "
        "question is UNMEASURED rather than answered either way."
    )
    return out


def run(con, *, instrument: str | None = None) -> dict:
    """The whole diagnostic. Reads; never writes."""
    raced = fe.legs(con, instrument=instrument)
    legs = raced["legs"]
    per_instrument: dict[str, dict] = {}
    for inst in _order(sorted({str(x["instrument"]) for x in legs})):
        rows = [x for x in legs if str(x["instrument"]) == inst]
        per_instrument[inst] = {
            "n": len(rows),
            "sessions": sorted({str(x["session"]) for x in rows}),
            "thresholds": _thresholds(rows),
            "vehicles": {
                vehicle: _vehicle_block(sub)
                for vehicle, sub in sorted(m.by_key(rows, "vehicle").items())
            },
            "cohorts": _cohorts(rows),
            "vehicle_comparison": m.vehicle_comparison(
                rows, horizon=REFERENCE,
            ),
            "verdict": v.classify(rows, horizon=REFERENCE),
        }
    return {
        "phase": PHASE,
        "version": VERSION,
        "mode": [RESEARCH_ONLY, PAPER_ONLY, READ_ONLY],
        "not_a_strategy": NOT_A_STRATEGY,
        "instrument_filter": instrument,
        "reference_horizon": REFERENCE,
        "horizons": list(fe.HORIZON_KEYS),
        "sessions": sorted({str(x["session"]) for x in legs}),
        "legs": len(legs),
        "coverage": raced["coverage"],
        "instruments": per_instrument,
        "unmeasured_instruments": _unmeasured(
            con, measured=list(per_instrument), instrument=instrument,
        ),
        # The primary answer is taken over every measured leg at the
        # pre-declared horizon, then repeated per instrument above, so a single
        # deeply-captured instrument cannot be mistaken for the market.
        "primary": v.classify(legs, horizon=REFERENCE),
        "primary_non_overlapping": v.classify(
            fe.independent(legs, horizon=REFERENCE), horizon=REFERENCE,
        ),
        "cross_instrument": [
            {
                "instrument": inst,
                "n": block["n"],
                "sessions": len(block["sessions"]),
                "verdict": block["verdict"]["verdict"],
                "favourable_first_pct": (
                    block["verdict"]["favourable_first_pct"]
                ),
                "adverse_first_pct": block["verdict"]["adverse_first_pct"],
                "given_back_pct": block["verdict"]["given_back_pct"],
                "net_pct_median": block["verdict"]["net_pct_median"],
            }
            for inst, block in per_instrument.items()
        ],
    }
