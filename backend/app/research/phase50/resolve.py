"""Phase 50 §5 — resolving one event's paper outcome. Pure: no store, no clock.

The arithmetic is Phase 35's, imported rather than restated:
:func:`app.research.phase35.book.exit_fill` picks the executable side,
:func:`~app.research.phase35.book.gross_pct` signs the move,
:func:`~app.research.phase35.book.net_pct` charges the round trip, and
:func:`app.research.phase35.path.resolve` walks the path for MFE, MAE, the
frozen T1/T2/T3 ladder and the giveback. A second copy of those formulas would
agree with the originals only until one of them was corrected.

What this module adds is the refusal. ``executable_only`` drops every forward
sample whose required side was not quoted **before** the path is walked, so a
traded print can reach neither the exit nor the excursions. Phase 35's resolver
keeps such samples for the path and labels the result ``SPREAD_MODELLED``; that
is right for a study of paths and wrong for a board that publishes a net, which
is the substitution this project has spent forty phases refusing. An event whose
forward samples are all non-executable therefore resolves to ``UNRESOLVED``,
which is a statement about the capture and not a flat outcome.

An event is priced as **one paper leg**: the entry of its first observation and
the exit available after its last. Its repeated observations are the same
opportunity, and charging one round trip per quote would multiply a single
opinion into a book of trades — the exact error the event layer exists to stop.
"""
from __future__ import annotations

from app.research.phase35 import (
    FUTURES,
    MEASURED_EXECUTABLE,
    SESSION_CLOSE,
)
from app.research.phase35 import book
from app.research.phase35 import path as p35path
from app.research.phase47 import MEASURED_COST, MODELLED_COST, NO_COST
from app.research.phase50 import (
    COVERAGE_ENDPOINT,
    ENTRY_RULE,
    EXIT_RULE,
    FROM_OBSERVATIONS,
    FROM_QUOTE_STORE,
    NO_MIDPOINT,
    RESOLVED,
    SAME_EXIT_BOTH_GROUPS,
    SAMPLE_FRESHNESS,
    SAMPLE_SOURCE,
    UNRESOLVED_EVENT_OPEN,
    UNRESOLVED_NO_COST,
    UNRESOLVED_NO_ENTRY,
    UNRESOLVED_NO_LATER_QUOTE,
    definition_fingerprint,
)
from app.research.phase50 import exits as p50exits


def executable_only(
    samples: list[dict], *, vehicle: str, direction: str,
) -> tuple[list[dict], int]:
    """Forward samples whose exit side was actually quoted, and how many were not.

    The dropped count is returned rather than discarded: an event resolved on
    three of two hundred samples is a different fact from one resolved on all
    two hundred, and the row says which.
    """
    kept: list[dict] = []
    dropped = 0
    for sample in samples:
        fill = book.exit_fill(sample, vehicle=vehicle, direction=direction)
        if fill.get("price") is None or fill.get("evidence") != MEASURED_EXECUTABLE:
            dropped += 1
            continue
        kept.append({**sample, "fill": fill})
    return kept, dropped


def cost_of(leg: dict) -> tuple[float | None, str]:
    """The round trip charged against the event, and which basis it came from.

    Measured when the instant had a two-sided book, modelled otherwise, and the
    basis travels with the number — a column half measured and half modelled is
    two measurements, and pooling them silently is how Phase 42 was poisoned.
    """
    measured = _num(leg.get("measured_cost_points"))
    if measured is not None:
        return measured, MEASURED_COST
    modelled = _num(leg.get("modelled_cost_points"))
    if modelled is not None:
        return modelled, MODELLED_COST
    return None, NO_COST


def resolve_event(event: dict, legs: list[dict], samples: list[dict]) -> dict:
    """One event's outcome from captured data only, or an explicit UNRESOLVED.

    ``legs`` are the event's observations in decision order and ``samples`` the
    measured books for that contract after the entry, same session — the event's
    own later observations and the quote store both, assembled by the caller.
    Entry comes from the first observation, the instant the opportunity was
    first admitted, and the path runs from there: a leg opened then and held was
    exposed to every book that followed, not only to the ones that arrived after
    the board stopped re-observing it.
    """
    ordered = sorted(legs, key=lambda r: _num(r.get("decision_ts")) or 0.0)
    first = ordered[0] if ordered else {}
    vehicle = str(event.get("vehicle") or first.get("vehicle") or "")
    direction = str(event.get("direction") or first.get("direction") or "")
    entry = _num(first.get("entry_price"))
    entry_ts = _num(first.get("decision_ts"))
    entry_side = first.get("entry_side")
    cost, basis = cost_of(first)
    base = {
        "resolution_status": RESOLVED,
        "entry_side": entry_side,
        "entry_price": entry,
        "entry_ts": entry_ts,
        "exit_side": None,
        "exit_price": None,
        "exit_ts": None,
        "gross_pct": None,
        "net_pct": None,
        "net_points": None,
        "cost_points": cost,
        "cost_basis": basis,
        "cost_pct_of_entry": _num(first.get("cost_pct_of_entry")),
        "charges_points": cost if basis == MEASURED_COST else None,
        "spread_points": _num(first.get("measured_spread_points")),
        "spread_note": (
            "measured at the instant and already paid inside the ask-in/bid-out "
            "fills, so it is reported and not added a second time"
        ),
        "cost_evidence": first.get("cost_evidence"),
        "mfe_pct": None,
        "mae_pct": None,
        "t1_hit": None,
        "t2_hit": None,
        "t3_hit": None,
        "targets_pct": None,
        "hold_minutes": None,
        "time_to_favorable_sec": None,
        "time_to_adverse_sec": None,
        "time_to_peak_min": None,
        "giveback_pct": None,
        "pct_of_mfe_given_back": None,
        "returned_to_entry": None,
        "turned_negative_after_profitable": None,
        "adverse_first": None,
        "forward_samples": 0,
        "forward_samples_dropped": 0,
        "forward_samples_from_observations": 0,
        "forward_samples_from_quote_store": 0,
        "entry_rule": ENTRY_RULE,
        "exit_rule": EXIT_RULE,
        "no_midpoint": NO_MIDPOINT,
        "sample_source": SAMPLE_SOURCE,
        "sample_freshness": SAMPLE_FRESHNESS,
        "resolution_fingerprint": definition_fingerprint(),
        "reason": None,
    }
    if entry is None or entry <= 0 or entry_ts is None:
        return {**base, "resolution_status": UNRESOLVED_NO_ENTRY,
                "reason": "the first observation of this event recorded no "
                          "executable entry price"}
    if cost is None:
        return {**base, "resolution_status": UNRESOLVED_NO_COST,
                "reason": "no round trip was measured or modelled for this "
                          "leg, and a gross figure is not a result"}
    kept, dropped = executable_only(samples, vehicle=vehicle, direction=direction)
    if not kept:
        return {**base, "resolution_status": UNRESOLVED_NO_LATER_QUOTE,
                "forward_samples_dropped": dropped,
                "reason": "no later measured executable quote for this contract "
                          "in this session — a statement about the capture, not "
                          "about the event"}
    walk = p35path.resolve(
        entry_price=entry,
        entry_ts=entry_ts,
        vehicle=vehicle,
        direction=direction,
        samples=kept,
        cost_points=cost,
    )
    giveback = walk.get("giveback") or {}
    close = (walk.get("horizons") or {}).get(SESSION_CLOSE) or {}
    targets = walk.get("targets_pct") or {}
    favorable, adverse = _first_moves(
        kept, entry=entry, entry_ts=entry_ts, vehicle=vehicle,
        direction=direction,
    )
    net = walk.get("final_net_pct")
    return {
        **base,
        "resolution_status": RESOLVED,
        "exit_side": walk.get("exit_side"),
        "exit_price": walk.get("exit_price"),
        "exit_ts": walk.get("exit_ts"),
        "gross_pct": walk.get("final_gross_pct"),
        "net_pct": net,
        "net_points": (
            None if net is None else round(float(net) * entry / 100.0, 4)
        ),
        "mfe_pct": giveback.get("peak_pct"),
        "mae_pct": giveback.get("mae_pct"),
        "t1_hit": close.get("t1"),
        "t2_hit": close.get("t2"),
        "t3_hit": close.get("t3"),
        "targets_pct": {
            "t1": targets.get("t1"), "t2": targets.get("t2"),
            "t3": targets.get("t3"),
            "basis": (
                "PERCENT_OF_ENTRY_PREMIUM" if vehicle != FUTURES
                else "MULTIPLES_OF_THE_MEASURED_ROUND_TRIP"
            ),
        },
        "hold_minutes": giveback.get("hold_minutes"),
        "time_to_favorable_sec": favorable,
        "time_to_adverse_sec": adverse,
        "time_to_peak_min": giveback.get("time_to_peak_min"),
        "giveback_pct": giveback.get("max_giveback_pct"),
        "pct_of_mfe_given_back": giveback.get("pct_of_mfe_given_back"),
        "returned_to_entry": giveback.get("returned_to_entry"),
        "turned_negative_after_profitable": giveback.get(
            "turned_negative_after_profitable"),
        "adverse_first": giveback.get("adverse_first"),
        "forward_samples": len(kept),
        "forward_samples_dropped": dropped,
        **_by_source(kept),
        "reason": None,
    }


def resolve_policies(
    event: dict, legs: list[dict], samples: list[dict],
) -> dict:
    """Every declared exit policy's outcome for one event, keyed by policy id.

    One entry, one cost, one set of executable forward books — then each policy
    walks those same books under its own pre-registered condition. Sharing the
    inputs is what makes the policies comparable to each other, and applying the
    identical set to every event is what makes the selected and declined groups
    comparable under §5.

    A refusal is a property of the event, not of the policy: with no executable
    entry, no measured round trip or no later executable book, every policy is
    unresolved for the same reason rather than some of them silently resolving
    off a different source.
    """
    ordered = sorted(legs, key=lambda r: _num(r.get("decision_ts")) or 0.0)
    first = ordered[0] if ordered else {}
    vehicle = str(event.get("vehicle") or first.get("vehicle") or "")
    direction = str(event.get("direction") or first.get("direction") or "")
    entry = _num(first.get("entry_price"))
    entry_ts = _num(first.get("decision_ts"))
    cost, basis = cost_of(first)
    shell = {
        "entry_side": first.get("entry_side"),
        "entry_price": entry,
        "entry_ts": entry_ts,
        "cost_points": cost,
        "cost_basis": basis,
        "entry_rule": ENTRY_RULE,
        "no_midpoint": NO_MIDPOINT,
        "sample_source": SAMPLE_SOURCE,
        "sample_freshness": SAMPLE_FRESHNESS,
        "same_exit_both_groups": SAME_EXIT_BOTH_GROUPS,
        "resolution_fingerprint": definition_fingerprint(),
        "exit_policy_set_fingerprint": p50exits.set_fingerprint(),
    }
    refusal: str | None = None
    if entry is None or entry <= 0 or entry_ts is None:
        refusal = UNRESOLVED_NO_ENTRY
    elif cost is None:
        refusal = UNRESOLVED_NO_COST
    kept: list[dict] = []
    dropped = 0
    if refusal is None:
        kept, dropped = executable_only(
            samples, vehicle=vehicle, direction=direction)
        if not kept:
            refusal = UNRESOLVED_NO_LATER_QUOTE
    if refusal is not None:
        return {
            policy: {
                **shell,
                "exit_policy_id": policy,
                "exit_policy_fingerprint": p50exits.fingerprint(policy),
                "resolution_status": refusal,
                "forward_samples": len(kept),
                "forward_samples_dropped": dropped,
                **_by_source(kept),
            }
            for policy in p50exits.ALL_POLICIES
        }
    out: dict[str, dict] = {}
    for policy in p50exits.POLICIES:
        outcome = p50exits.apply(
            policy, entry_price=float(entry), entry_ts=float(entry_ts),
            vehicle=vehicle, direction=direction, cost_points=cost,
            samples=kept,
        )
        net = _num(outcome.get("net_pct"))
        out[policy] = {
            **shell, **outcome,
            "net_points": (
                None if net is None else round(net * float(entry) / 100.0, 4)
            ),
            "forward_samples_dropped": dropped,
            **_by_source(kept),
        }
    coverage = p50exits.coverage_endpoint(
        entry_price=float(entry), entry_ts=float(entry_ts), vehicle=vehicle,
        direction=direction, cost_points=cost, samples=kept,
    )
    out[COVERAGE_ENDPOINT] = {
        **shell, **coverage, "forward_samples_dropped": dropped,
        **_by_source(kept),
    }
    return out


def _by_source(kept: list[dict]) -> dict:
    """How many of the samples the exit was chosen from came from where.

    On the record because the two sources answer different questions about the
    capture: an event priced entirely off its own observations says the board
    kept looking at it, and one priced off the quote store says the contract
    kept being quoted after the board stopped. A row that does not say which
    cannot be audited for either.
    """
    own = sum(1 for s in kept if s.get("sample_source") == FROM_OBSERVATIONS)
    store = sum(1 for s in kept if s.get("sample_source") == FROM_QUOTE_STORE)
    return {
        "forward_samples_from_observations": own,
        "forward_samples_from_quote_store": store,
    }


def _first_moves(
    samples: list[dict], *, entry: float, entry_ts: float, vehicle: str,
    direction: str,
) -> tuple[float | None, float | None]:
    """Seconds to the first favourable and first adverse executable quote.

    Both, not one: an event that went adverse at ten seconds and favourable at
    nine minutes was entered early, and an event that only ever went one way is
    a different shape. A ``None`` means that side never happened.
    """
    favorable: float | None = None
    adverse: float | None = None
    for sample in samples:
        fill = sample.get("fill") or book.exit_fill(
            sample, vehicle=vehicle, direction=direction)
        price = fill.get("price")
        ts = _num(sample.get("ts"))
        if price is None or ts is None:
            continue
        move = book.gross_pct(entry, float(price), vehicle=vehicle,
                              direction=direction)
        if move > 0 and favorable is None:
            favorable = round(ts - entry_ts, 1)
        if move < 0 and adverse is None:
            adverse = round(ts - entry_ts, 1)
        if favorable is not None and adverse is not None:
            break
    return favorable, adverse


def still_open(event: dict, *, session_end_ts: float, now: float) -> bool:
    """True while the event's own session is still running.

    An event in a live session may still gain observations, so its resolution is
    published as provisional rather than final — the same distinction Phase 47
    draws between an open and a resolved lifecycle.
    """
    return float(now) < float(session_end_ts)


def open_row(event: dict) -> dict:
    """The UNRESOLVED shape for an event that cannot be priced yet."""
    return {
        "resolution_status": UNRESOLVED_EVENT_OPEN,
        "reason": "the event's session is still running and it may gain "
                  "observations, so no outcome is claimed for it yet",
        "resolution_fingerprint": definition_fingerprint(),
    }


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
