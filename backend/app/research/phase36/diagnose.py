"""Why a decision instant was not a comparable triple, field by field.

`coverage` says how many instants were refused and under which of the five
comparison-level reasons. That is the right output for the study and the wrong
one for fixing capture: `NO_EXECUTABLE_FUTURES_BOOK` ×13,110 could equally mean
the futures quote was never recorded at that instant, or was recorded from the
simulator, or arrived with one side of the book, or was 4 seconds stale. Those
have four different fixes, and guessing between them is how a capture bug gets
"fixed" by loosening the executable test instead.

So this module counts the reason each quote already carries — capture writes an
explicit reason when it cannot measure something, and those reasons are raw
evidence, not re-derived here — and separates *absent* from *present but not
executable*. It reads and counts. It never writes, never relaxes the test, and
has no verdict of its own: a diagnosis is not a finding.
"""
from __future__ import annotations

from app.research.phase35 import (
    CE,
    FUTURES,
    MEASURED_EXECUTABLE,
    PE,
    normalize_direction,
)
from app.research.phase36 import triples as p36triples

VEHICLES: tuple[str, ...] = (FUTURES, CE, PE)

# What is wrong with a quote that claims to be an executable book. Ordered, and
# the first that applies is the one counted, so the totals sum to the population.
ABSENT = "QUOTE_ROW_ABSENT"
NOT_MEASURED_EXECUTABLE = "NOT_MEASURED_EXECUTABLE"
NO_BID = "NO_BID"
NO_ASK = "NO_ASK"
NON_POSITIVE = "BID_OR_ASK_NOT_POSITIVE"
CROSSED = "CROSSED_OR_LOCKED"
EXECUTABLE = "EXECUTABLE"


def _classify(quote: dict | None) -> str:
    if quote is None:
        return ABSENT
    if quote.get("evidence") != MEASURED_EXECUTABLE:
        return NOT_MEASURED_EXECUTABLE
    bid, ask = quote.get("bid"), quote.get("ask")
    if bid is None:
        return NO_BID
    if ask is None:
        return NO_ASK
    if p36triples._positive(bid) is None or p36triples._positive(ask) is None:
        return NON_POSITIVE
    if float(bid) >= float(ask):
        return CROSSED
    return EXECUTABLE


def _bump(bag: dict, key: object) -> None:
    k = str(key)
    bag[k] = bag.get(k, 0) + 1


def _sorted(bag: dict) -> dict:
    return dict(sorted(bag.items(), key=lambda kv: -kv[1]))


def diagnose(con, *, instrument: str, limit: int | None = None) -> dict:
    """Per-vehicle failure counts, plus what the direction-less rows are."""
    obs_rows = con.execute(
        "SELECT * FROM raw_observation WHERE instrument = ? ORDER BY ts",
        (instrument,),
    ).fetchall()
    if limit:
        obs_rows = obs_rows[:limit]

    per_vehicle: dict[str, dict] = {
        v: {"classification": {}, "evidence": {}, "capture_reason": {},
            "symbols": {}}
        for v in VEHICLES
    }
    direction_missing: dict[str, dict] = {
        "by_source": {}, "by_opportunity_type": {}, "by_engine_class": {},
        "by_selected_vehicle": {}, "raw_value": {},
    }
    directions: dict[str, int] = {}
    direction_read_as: dict[str, int] = {}
    both_books_no_direction = 0
    all_three_books = 0

    for row in obs_rows:
        obs = dict(row)
        quotes = {
            q["vehicle"]: dict(q) for q in con.execute(
                "SELECT * FROM raw_quote WHERE obs_id = ?", (obs["obs_id"],),
            ).fetchall()
        }
        verdicts = {}
        for vehicle in VEHICLES:
            quote = quotes.get(vehicle)
            verdict = _classify(quote)
            verdicts[vehicle] = verdict
            bag = per_vehicle[vehicle]
            _bump(bag["classification"], verdict)
            if quote is not None:
                _bump(bag["evidence"], quote.get("evidence"))
                if quote.get("reason"):
                    _bump(bag["capture_reason"], quote.get("reason"))
                if verdict == EXECUTABLE and quote.get("symbol"):
                    _bump(bag["symbols"], quote.get("symbol"))

        raw_direction = obs.get("direction")
        direction = normalize_direction(raw_direction)
        _bump(directions, str(raw_direction or "(none)").strip().upper())
        if direction is not None:
            _bump(direction_read_as, direction)
        if direction is None:
            _bump(direction_missing["by_source"], obs.get("source"))
            _bump(direction_missing["by_opportunity_type"],
                  obs.get("opportunity_type"))
            _bump(direction_missing["by_engine_class"], obs.get("engine_class"))
            _bump(direction_missing["by_selected_vehicle"],
                  obs.get("selected_vehicle"))
            _bump(direction_missing["raw_value"], repr(obs.get("direction")))

        if all(v == EXECUTABLE for v in verdicts.values()):
            all_three_books += 1
            if direction is None:
                both_books_no_direction += 1

    for vehicle in VEHICLES:
        bag = per_vehicle[vehicle]
        for key in ("classification", "evidence", "capture_reason"):
            bag[key] = _sorted(bag[key])
        # Only the contracts that produced an executable book, and only the ten
        # most frequent: this is here to show *which* future was quoted, not to
        # become a contract inventory.
        bag["symbols"] = dict(
            sorted(bag["symbols"].items(), key=lambda kv: -kv[1])[:10]
        )
        bag["executable"] = bag["classification"].get(EXECUTABLE, 0)

    return {
        "instrument": instrument,
        "observations": len(obs_rows),
        "per_vehicle": per_vehicle,
        "instants_with_all_three_books": all_three_books,
        "instants_with_all_three_books_but_no_direction": both_books_no_direction,
        "direction_values": _sorted(directions),
        # The stored word next to the position this study reads it as. Kept
        # visible because the two vocabularies differ, and a silent translation
        # is the kind of thing that should be inspectable rather than trusted.
        "direction_read_as": _sorted(direction_read_as),
        "direction_missing": {
            k: _sorted(v) for k, v in direction_missing.items()
        },
        "research_only": True,
        "note": (
            "Counts of why quotes were not usable, taken from the reasons "
            "capture already recorded. A diagnosis is not a finding, and "
            "nothing here relaxes the executable-book test."
        ),
    }


def headline(state: dict) -> str:
    lines = [
        f"CRUDEOIL-STYLE TRIPLE DIAGNOSIS — {state['instrument']}",
        f"  observations              : {state['observations']}",
        f"  all three books executable: {state['instants_with_all_three_books']}"
        f" (of which no direction: "
        f"{state['instants_with_all_three_books_but_no_direction']})",
    ]
    for vehicle in VEHICLES:
        bag = state["per_vehicle"][vehicle]
        lines.append(
            f"  {vehicle:<8} executable {bag['executable']}"
            f"  top failures: "
            + ", ".join(
                f"{k}={v}" for k, v in list(bag["classification"].items())[:4]
                if k != EXECUTABLE
            )
        )
    lines.append(
        "  direction values          : "
        + ", ".join(f"{k}={v}" for k, v in
                    list(state["direction_values"].items())[:6])
    )
    lines.append(
        "  read as                   : "
        + (", ".join(f"{k}={v}" for k, v in
                     state["direction_read_as"].items()) or "nothing readable")
    )
    return "\n".join(lines)
