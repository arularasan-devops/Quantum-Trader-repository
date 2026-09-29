"""What the research definition would have said at one decision instant.

One function of interest, :func:`evaluate`. It takes a Phase 17 observation and
the trailing range accumulated before it, and returns one row per vehicle —
FUTURES, CE and PE — each row carrying the executable book it was priced from,
the round trip it would have paid, the gate quantity Phase 42 computes, and one
of four actions with a machine-readable reason.

Three properties are enforced by construction rather than asserted in prose:

**Nothing but the decision instant is read.** The only inputs are the captured
quotes, the plan, the context and a range built from minutes strictly before the
decision. No field describing what happened afterwards is referenced anywhere in
this module, and :mod:`_smoke_phase45` parses the file for the names in
:data:`app.research.phase45.OUTCOME_FIELDS` and fails if one appears.

**Nothing is substituted.** The entry price is the executable side or nothing:
:func:`app.research.phase35.book.entry_fill` returns UNMEASURED rather than the
traded price, and a missing option book produces an UNMEASURED option row — the
futures quote captured at the same instant never stands in for it, however
tempting a populated board is.

**A refusal is measured too.** A vehicle that opposes the read direction, an
instant the engine did not treat as a fresh candidate, and a ratio below the
live multiple are all WAIT rows with their evidence intact. Journalling only the
instants the definition liked would make its own base rate uncomputable.
"""
from __future__ import annotations

import hashlib

from app.research.phase17 import futures as fut_mod
from app.research.phase17 import quality, schema
from app.research.phase35 import (
    LOT_FROM_QUOTE,
    LOT_FROM_SPEC,
    MEASURED_EXECUTABLE,
    MEASURED_EXECUTABLE_SPEC_LOT,
    book as p35book,
    lots as p35lots,
)
from app.research.phase42 import LIVE_MULTIPLE
from app.research.phase42.exante import admits, leg_delta, ratio
from app.research.phase45 import (
    BELOW_LIVE_GATE,
    CE,
    COST_UNMEASURED,
    CROSSED_BOOK,
    DIRECTION_DISAGREES,
    FUTURES,
    MISSING_ASK,
    MISSING_BID,
    NO_DELTA,
    NO_DIRECTION,
    NO_LOT_SIZE,
    NO_QUOTE,
    NO_RANGE,
    NOT_A_BUY_CANDIDATE,
    OBSERVED,
    ONE_SIDED_BOOK,
    PE,
    SHADOW_BUY,
    SHADOW_SELL,
    SHADOW_SIGNAL,
    SHADOW_UNMEASURED,
    SHADOW_WAIT,
    SIGNALLED,
    STALE_QUOTE,
    VEHICLE_LABELS,
    VEHICLES,
    VERSION,
)

MEASURED = "MEASURED"
UNMEASURED = "UNMEASURED"
# The evidence labels a measured cost may carry, both of which price a real
# spread; the weaker one only says the multiplier came from the contract
# specification rather than from the quote.
COSTED: frozenset[str] = frozenset(
    {MEASURED_EXECUTABLE, MEASURED_EXECUTABLE_SPEC_LOT}
)
# The engine classes that represent a fresh candidate rather than a position
# already held or an instrument being stood aside from.
FRESH_CLASSES: frozenset[str] = frozenset({"BUY"})


def event_id(obs_id: str, vehicle: str, contract: str | None) -> str:
    """Stable identity for one (observation, vehicle, contract).

    Derived from provenance rather than from a counter, so re-observing or
    replaying the same raw instant converges on the same row instead of
    appending a second one. The contract is part of it because the same
    observation prices three different contracts.
    """
    material = f"{obs_id}|{vehicle}|{contract or ''}"
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def _px(value: object) -> float | None:
    """A price that could be traded on, or ``None``."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    f = float(value)
    if f != f or f in (float("inf"), float("-inf")) or f <= 0:
        return None
    return f


def _size(value: object) -> int | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    v = int(value)
    return v if v > 0 else None


def _vehicle_agrees(vehicle: str, side: str | None) -> bool:
    """Whether buying this vehicle expresses the direction that was read.

    A call expresses a long view and a put a short one; a futures contract
    expresses either, by taking the corresponding side of its own book.
    """
    if vehicle == FUTURES:
        return side in (fut_mod.LONG, fut_mod.SHORT)
    if vehicle == CE:
        return side == fut_mod.LONG
    return side == fut_mod.SHORT


def _quote_dict(q: schema.Quote) -> dict:
    """The subset :mod:`app.research.phase35.book` prices a fill from."""
    return {"bid": q.bid, "ask": q.ask, "traded": q.premium}


def evaluate(
    obs: schema.Observation,
    *,
    trailing_range: dict,
    definition: str,
    ingest_ts: float,
    gate_multiple: float = LIVE_MULTIPLE,
) -> list[dict]:
    """One row per vehicle for one observation, in board order.

    ``trailing_range`` is the dict :class:`app.research.phase45.ranges.
    LiveRanges` returns: the range in points, or the reason there is none.
    """
    side = fut_mod.side_of(obs.direction or "")
    quotes: dict[str, schema.Quote | None] = {
        FUTURES: obs.futures,
        CE: _option(obs, CE),
        PE: _option(obs, PE),
    }
    return [
        _row(
            obs, vehicle, quotes.get(vehicle), side=side,
            trailing_range=trailing_range, definition=definition,
            ingest_ts=ingest_ts, gate_multiple=float(gate_multiple),
        )
        for vehicle in VEHICLES
    ]


def _option(obs: schema.Observation, vehicle: str) -> schema.Quote | None:
    """The captured quote for one option side, whichever slot holds it.

    Phase 17 records the side the engine selected and its opposite; which of
    the two is the call depends on the direction that was read, so the vehicle
    is matched rather than the slot. Collapsing them would be the substitution
    this board exists to refuse.
    """
    for q in (obs.selected, obs.opposite):
        if q is not None and q.vehicle == vehicle:
            return q
    return None


def _row(
    obs: schema.Observation,
    vehicle: str,
    q: schema.Quote | None,
    *,
    side: str | None,
    trailing_range: dict,
    definition: str,
    ingest_ts: float,
    gate_multiple: float,
) -> dict:
    row = _skeleton(
        obs, vehicle, q, side=side, definition=definition,
        ingest_ts=ingest_ts, gate_multiple=gate_multiple,
    )
    if q is None:
        return _unmeasured(row, NO_QUOTE)

    bid, ask = _px(q.bid), _px(q.ask)
    row.update({
        "bid": bid, "ask": ask, "last_traded_price": _px(q.premium),
        "bid_size": _size(q.bid_size), "ask_size": _size(q.ask_size),
        "spread": q.spread, "spread_pct": q.spread_pct,
        "feed_age_ms": q.feed_age_ms, "book_age_ms": q.book_age_ms,
        "quote_quality": q.data_quality,
    })
    if bid is None and ask is None:
        return _unmeasured(row, NO_QUOTE)
    if bid is None:
        row["book_state"] = ONE_SIDED_BOOK
        return _unmeasured(row, MISSING_BID)
    if ask is None:
        row["book_state"] = ONE_SIDED_BOOK
        return _unmeasured(row, MISSING_ASK)
    if ask < bid:
        return _unmeasured(row, CROSSED_BOOK)
    row["book_state"] = "TWO_SIDED"
    # The same bar a costed paper fill is held to: EXACT or GOOD, i.e. a book
    # no more than ten seconds old. Anything slower is a fabricated fill.
    if q.data_quality not in quality.FILLABLE:
        return _unmeasured(row, STALE_QUOTE)

    lot, lot_source = _lot(q, obs)
    row.update({"lot_size": lot, "lot_source": lot_source})
    if lot is None:
        return _unmeasured(row, NO_LOT_SIZE)

    fill = p35book.entry_fill(
        _quote_dict(q), vehicle=vehicle, direction=side or fut_mod.LONG,
    )
    row.update({"entry_side": fill["side"], "entry_price": fill["price"]})
    entry = _px(fill["price"])
    if entry is None:
        return _unmeasured(row, fill.get("reason") or NO_QUOTE)

    measured = p35book.cost_of(
        vehicle, obs.instrument, entry=entry, exit_price=entry,
        lot_size=lot, executable=True, lot_source=lot_source,
    )
    modelled = p35book.cost_of(
        vehicle, obs.instrument, entry=entry, exit_price=entry,
        lot_size=lot, executable=False, lot_source=lot_source,
    )
    spread_points = round(ask - bid, 4)
    charges = measured.get("cost_points")
    row.update({
        "measured_charges_points": charges,
        "measured_spread_points": spread_points,
        # The round trip a marketable entry and exit would actually pay: the
        # charges, plus the spread crossed once. Kept beside the modelled figure
        # rather than replacing it, because the difference between the two is
        # what the historical studies could not see.
        "measured_cost_points": (
            None if charges is None else round(float(charges) + spread_points, 4)
        ),
        "modelled_cost_points": modelled.get("cost_points"),
        "cost_evidence": measured.get("evidence"),
    })
    if measured.get("evidence") not in COSTED or charges is None:
        return _unmeasured(row, COST_UNMEASURED)

    delta = leg_delta(vehicle, q.delta)
    row["delta"] = delta
    if delta is None:
        return _unmeasured(row, NO_DELTA)

    points = trailing_range.get("trailing_range_points")
    row.update({
        "trailing_range_points": points,
        "range_source": trailing_range.get("source"),
    })
    if points is None:
        return _unmeasured(row, trailing_range.get("reason") or NO_RANGE)

    computed = ratio(
        delta=delta, trailing_range_points=points,
        cost_points=row["measured_cost_points"], entry_price=entry,
    )
    modelled_ratio = ratio(
        delta=delta, trailing_range_points=points,
        cost_points=row["modelled_cost_points"], entry_price=entry,
    )
    row.update({
        "expected_move_points": computed.get("expected_move_points"),
        "ratio_measured": computed.get("ratio"),
        "ratio_modelled": modelled_ratio.get("ratio"),
        "cost_pct_of_entry": computed.get("cost_pct_of_entry"),
    })
    if computed.get("ratio") is None:
        return _unmeasured(row, COST_UNMEASURED)

    row["evidence"] = MEASURED
    row["gate_result"] = admits(computed.get("ratio"), gate_multiple)
    return _action(row, obs, vehicle, side=side)


def _action(
    row: dict, obs: schema.Observation, vehicle: str, *, side: str | None,
) -> dict:
    """The action a fully measured row carries, and why."""
    if side is None:
        return _unmeasured(row, NO_DIRECTION)
    if not _vehicle_agrees(vehicle, side):
        row.update({"shadow_action": SHADOW_WAIT, "reason": DIRECTION_DISAGREES})
        return row
    if (obs.candidate_class or "").upper() not in FRESH_CLASSES:
        row.update({"shadow_action": SHADOW_WAIT, "reason": NOT_A_BUY_CANDIDATE})
        return row
    if row.get("gate_result") is not True:
        row.update({"shadow_action": SHADOW_WAIT, "reason": BELOW_LIVE_GATE})
        return row
    row.update({
        "shadow_action": (
            SHADOW_SELL if vehicle == FUTURES and side == fut_mod.SHORT
            else SHADOW_BUY
        ),
        "reason": None,
        "lifecycle": SIGNALLED,
    })
    return row


def _unmeasured(row: dict, reason: str) -> dict:
    row.update({
        "evidence": UNMEASURED,
        "shadow_action": SHADOW_UNMEASURED,
        "reason": reason,
        "gate_result": None,
    })
    return row


def _lot(q: schema.Quote, obs: schema.Observation) -> tuple[int | None, str | None]:
    """The contract multiplier, and where it came from.

    The quote's own value first; the contract specification second, labelled, so
    a session whose book was captured without a multiplier is not discarded
    while the modelled cost reads the same specification anyway.
    """
    from_quote = p35lots.from_quote({"lot_size": q.lot_size})
    if from_quote is not None:
        return from_quote, LOT_FROM_QUOTE
    from_spec = p35lots.from_spec(obs.instrument)
    if from_spec is not None:
        return from_spec, LOT_FROM_SPEC
    return None, None


def _skeleton(
    obs: schema.Observation,
    vehicle: str,
    q: schema.Quote | None,
    *,
    side: str | None,
    definition: str,
    ingest_ts: float,
    gate_multiple: float,
) -> dict:
    """Every column, present on every row, whatever the row concludes.

    A row that omits a column when it has nothing to put in it is a row a later
    reader has to guess about; here the provenance is always complete and the
    measurement may be ``None``.
    """
    return {
        "event_id": event_id(obs.observation_id, vehicle, q.symbol if q else None),
        "obs_id": obs.observation_id,
        "decision_ts": obs.signal_ts,
        "capture_ts": obs.capture_ts,
        "ingest_ts": ingest_ts,
        "session": obs.session,
        "instrument": obs.instrument,
        "family": obs.family,
        "vehicle": vehicle,
        "vehicle_label": VEHICLE_LABELS[vehicle],
        "contract": q.symbol if q else None,
        "strike": q.strike if q else None,
        "expiry": q.expiry if q else None,
        "dte": q.days_to_expiry if q else None,
        "underlying_price": q.underlying_price if q else None,
        "moneyness": q.moneyness if q else None,
        "direction": side,
        "read_direction": obs.direction,
        "shadow_action": SHADOW_UNMEASURED,
        "reason": None,
        "evidence": UNMEASURED,
        "book_state": None,
        "bid": None, "ask": None, "bid_size": None, "ask_size": None,
        "last_traded_price": None, "spread": None, "spread_pct": None,
        "feed_age_ms": None, "book_age_ms": None,
        "quote_quality": quality.MISSING,
        "lot_size": None, "lot_source": None,
        "entry_side": None, "entry_price": None,
        "atr_points": obs.context.atr_points,
        "trailing_range_points": None, "range_source": None,
        "delta": None, "expected_move_points": None,
        "modelled_cost_points": None, "measured_cost_points": None,
        "measured_charges_points": None, "measured_spread_points": None,
        "cost_pct_of_entry": None, "cost_evidence": None,
        "ratio_modelled": None, "ratio_measured": None,
        "gate_multiple": gate_multiple, "gate_result": None,
        "engine_class": obs.candidate_class,
        "engine_selected_vehicle": obs.selected_vehicle,
        "production_signal": obs.context.market_signal,
        "session_period": obs.context.session_period,
        "regime": obs.context.regime,
        "classification": SHADOW_SIGNAL,
        "data_quality": obs.data_quality,
        "lifecycle": OBSERVED,
        "definition": definition,
        "version": VERSION,
        "source": obs.context.basis,
    }


def comparison(rows: list[dict]) -> dict:
    """FUTURES vs CE vs PE at one instant, where all three were priced.

    Reported only when the vehicles being compared were measured at the same
    decision instant. A comparison against an unmeasured leg would read as a
    verdict about the vehicle when it is a verdict about the feed, so the
    unmeasured vehicles are named instead.
    """
    by_vehicle = {r["vehicle"]: r for r in rows}
    measured = {v: r for v, r in by_vehicle.items() if r["evidence"] == MEASURED}
    ranked = sorted(
        measured.values(),
        key=lambda r: (-(r["ratio_measured"] or 0.0), r["vehicle"]),
    )
    return {
        "obs_id": rows[0]["obs_id"] if rows else None,
        "decision_ts": rows[0]["decision_ts"] if rows else None,
        "instrument": rows[0]["instrument"] if rows else None,
        "direction": rows[0]["direction"] if rows else None,
        "synchronized": sorted(measured),
        "unmeasured": {
            v: r["reason"] for v, r in by_vehicle.items()
            if r["evidence"] != MEASURED
        },
        "complete": len(measured) == len(VEHICLES),
        "best_ratio_vehicle": ranked[0]["vehicle"] if ranked else None,
        "vehicles": {
            v: {
                "contract": r["contract"],
                "strike": r["strike"],
                "expiry": r["expiry"],
                "entry_side": r["entry_side"],
                "entry_price": r["entry_price"],
                "spread": r["measured_spread_points"],
                "measured_cost_points": r["measured_cost_points"],
                "modelled_cost_points": r["modelled_cost_points"],
                "expected_move_points": r["expected_move_points"],
                "ratio_measured": r["ratio_measured"],
                "shadow_action": r["shadow_action"],
            }
            for v, r in measured.items()
        },
    }
