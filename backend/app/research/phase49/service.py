"""Phase 49 — correcting a recorded tally by adding to the record, never editing it.

Two operations, and the asymmetry between them is the point.

:func:`annotate` is read-only. It takes recorded Phase 47 tallies and attaches
``record_status`` — ``CURRENT`` or ``SUPERSEDED`` — derived from the registry at
read time. The recorded rows themselves are returned with every value they were
written with, including the wrong count.

:func:`supersede` writes twice and edits nothing: it re-records the session's
tally under the corrected call selection, which lands as a **new** row because
the snapshot id now carries the selection fingerprint, and then appends a
registry entry naming the row it replaces and why. Run it again and both writes
converge on what is already there.

The Phase 47 service is imported inside the functions rather than at module
scope. Phase 47 reads this module to annotate its own tallies, and a module-level
import in both directions is a cycle — the kind that fails at start-up in one
import order and works in another.
"""
from __future__ import annotations

import threading
import time

from app.research.phase49 import (
    APPEND_ONLY,
    CLASSIFICATION,
    CURRENT,
    NO_ORDER_PATH,
    NOT_A_RESULT,
    PAPER_ONLY,
    PRODUCTION_UNCHANGED,
    REASON_ACCRUAL,
    REASON_BOUND,
    REASON_SELECTION,
    REASON_SELECTION_SAME_COUNT,
    REASON_UNRECORDED_BOUND,
    SELECTION,
    SELECTION_SUPERSEDED,
    SUPERSEDED,
    SUPERSESSION_IS_NOT_A_DELETION,
    VERSION,
    rule_fingerprint,
    selection_fingerprint,
)
from app.research.phase49 import store as store_mod

_LOCK = threading.Lock()
_CON = None


def _connection():
    global _CON
    if _CON is None:
        _CON = store_mod.connect()
    return _CON


def annotate(rows: list[dict]) -> list[dict]:
    """Attach ``record_status`` to recorded tallies. Never mutates the store.

    A row is ``SUPERSEDED`` when the registry names its snapshot id, and
    ``CURRENT`` otherwise. The superseding id and the reason travel with it, so
    a reader looking at an old count can see what replaced it without having to
    know this phase exists.
    """
    if not rows:
        return []
    registry = superseded_index()
    out: list[dict] = []
    for row in rows:
        record = registry.get(str(row.get("snapshot_id")))
        annotated = dict(row)
        annotated["record_status"] = SUPERSEDED if record else CURRENT
        annotated["superseded_by"] = (
            record.get("superseding_snapshot_id") if record else None
        )
        annotated["superseded_reason"] = record.get("reason") if record else None
        annotated["superseded_ts"] = record.get("recorded_ts") if record else None
        out.append(annotated)
    return out


def superseded_index() -> dict[str, dict]:
    """Snapshot ids the registry has superseded, by id. Read-only, may be empty."""
    try:
        with _LOCK:
            return store_mod.superseded_ids(_connection())
    except Exception:  # no registry yet means nothing has been superseded
        return {}


def supersede(
    *,
    session: str | None = None,
    reason: str | None = None,
    limit: int | None = None,
    now: float | None = None,
) -> dict:
    """Re-record one session's tally under the corrected selection and register it.

    Returns the old and new counts per arm explicitly, because "superseded" is
    only meaningful next to the two numbers it stands between.
    """
    from app.research.phase47 import service as p47service
    from app.research.phase47 import store as p47store

    clock = float(now if now is not None else time.time())
    bound = p47service.MAX_CALLS if limit is None else int(limit)
    # Read the board first, only to find arms whose recorded tally predates the
    # bound columns and disagrees with what the board now counts. Those have to
    # be re-recorded under their own selection identity or the new row collides
    # with the row it contradicts and is dropped as a duplicate.
    stale = _unrecorded_bound_arms(
        session=session, limit=bound, now=clock,
    )
    fresh = p47service.record(
        session=session, limit=bound, now=clock,
        recount_arms=frozenset(stale),
    )
    target = fresh.get("session")
    if not target:
        return {
            "session": None, "superseded": 0, "written": 0,
            "arms": [], "note": "NO_SESSION_IN_THE_CALL_JOURNAL",
            **_chrome(),
        }
    current_ids = {str(v) for v in (fresh.get("snapshot_ids") or {}).values()}
    with p47service._LOCK:  # the journal's own lock, so a concurrent recorder
        recorded = p47store.snapshots(  # cannot append between read and write
            p47service._connection(), session=str(target), limit=500,
        )
    newest = {str(r.get("snapshot_id")): r for r in recorded}
    with _LOCK:
        already = store_mod.superseded_ids(_connection())
    rows: list[dict] = []
    arms: list[dict] = []
    for arm, new_id in (fresh.get("snapshot_ids") or {}).items():
        new_row = newest.get(str(new_id), {})
        for old_id, old_row in newest.items():
            if (
                old_id in current_ids
                or str(old_row.get("arm")) != str(arm)
                or old_id in already
                or (old_id not in stale.get(str(arm), set())
                    and _under_current_selection(old_row, old_id))
            ):
                continue
            bounds = (fresh.get("bound") or {}).get(str(arm), {})
            # A weaker reading never replaces a stronger one: a recount that
            # stopped at its own bound and counted no more than the row it
            # would supersede is read with a smaller limit, not a better one.
            if (bounds.get("truncated")
                    and int(new_row.get("calls") or 0)
                    <= int(old_row.get("calls") or 0)):
                continue
            # The reason has to be true of *this* pair. A recount that returned
            # the same number was not recorded under a count that was wrong —
            # only under a selection that has since been corrected — and saying
            # otherwise puts a claim on the record the numbers do not support.
            why = reason or _why(old_row, new_row, stale, str(arm))
            rows.append({
                "supersession_id": store_mod.supersession_id(old_id, str(new_id)),
                "superseded_snapshot_id": old_id,
                "superseding_snapshot_id": str(new_id),
                "session": str(target),
                "arm": str(arm),
                "definition": str(fresh.get("definition") or ""),
                "superseded_calls": old_row.get("calls"),
                "superseding_calls": new_row.get("calls"),
                "reason": why,
                "superseded_selection": SELECTION_SUPERSEDED,
                "selection": SELECTION,
                "selection_fingerprint": selection_fingerprint(),
                "recorded_ts": clock,
                "version": VERSION,
            })
            arms.append({
                "arm": str(arm),
                "old_snapshot_id": old_id,
                "old_calls": old_row.get("calls"),
                "old_status": SUPERSEDED,
                "new_snapshot_id": str(new_id),
                "new_calls": new_row.get("calls"),
                "new_legs": new_row.get("legs"),
                "new_events": new_row.get("events"),
                "new_status": CURRENT,
                "reason": why,
                # Whether the new count is the arm's or the reader's bound.
                "bound": bounds.get("limit"),
                "available": bounds.get("available"),
                "truncated": bool(bounds.get("truncated")),
                "count_is": bounds.get("count_is"),
                "reported": bounds.get("reported"),
            })
    with _LOCK:
        counts = store_mod.insert(_connection(), rows)
    return {
        "session": str(target),
        "recorded": {"written": fresh.get("written"),
                     "duplicate": fresh.get("duplicate")},
        "superseded": counts["written"],
        "already_registered": counts["duplicate"],
        "arms": arms,
        "bound": fresh.get("bound", {}),
        "event_counts": fresh.get("event_counts", {}),
        **_chrome(),
    }


def _unrecorded_bound_arms(
    *, session: str | None, limit: int, now: float,
) -> dict[str, set[str]]:
    """Recorded rows that carry no bound and disagree with the board, by arm.

    A tally written before ``count_is`` existed cannot be read as a total or as
    a floor — what bounded it was never recorded. So it is left alone while the
    board agrees with it, and treated as supersedable the moment it does not:
    the disagreement is the evidence, not a guess about which it was.

    Read-only. The returned ids are the rows a recount will supersede, and the
    arms they belong to are the ones whose recount needs its own identity.
    """
    from app.research.phase47 import service as p47service
    from app.research.phase47 import store as p47store

    board = p47service.board(session=session, limit=limit, now=now)
    target = board.get("session")
    if not target:
        return {}
    with p47service._LOCK:
        recorded = p47store.snapshots(
            p47service._connection(), session=str(target), limit=500,
        )
    out: dict[str, set[str]] = {}
    for row in recorded:
        arm = str(row.get("arm") or "")
        if str(row.get("count_is") or ""):
            continue  # its bound is on the record; the normal path handles it
        tally = (board.get("arms") or {}).get(arm)
        if not tally:
            continue
        if int(tally.get("calls") or 0) != int(row.get("calls") or 0):
            out.setdefault(arm, set()).add(str(row.get("snapshot_id")))
    return out


def _why(
    old_row: dict,
    new_row: dict,
    stale: dict[str, set[str]] | None = None,
    arm: str = "",
) -> str:
    """The reason this pair is superseded, true of these two rows specifically.

    Five different situations get five different names, and only one of them is
    a fault in the old row. The broken selection truncated some arms and not
    others, so a row whose count did not move was not recorded under a wrong
    count; a row that stopped at its own bound is a weaker reading rather than a
    mistaken one; a row written before the bound was stored cannot be called
    either, so it is named for what is missing from it; and a row that counted
    everything its arm held at the time was not wrong at all — the session kept
    accruing, which is the commonest case and was previously reported as if the
    earlier reading had been mis-selected.
    """
    from app.research.phase47 import COUNT_IS_A_FLOOR

    if str(old_row.get("count_is") or "") == COUNT_IS_A_FLOOR:
        return REASON_BOUND
    if _accrued(old_row, new_row):
        return REASON_ACCRUAL
    if str(old_row.get("snapshot_id")) in (stale or {}).get(arm, set()):
        return REASON_UNRECORDED_BOUND
    if new_row.get("calls") != old_row.get("calls"):
        return REASON_SELECTION
    return REASON_SELECTION_SAME_COUNT


def _accrued(old_row: dict, new_row: dict) -> bool:
    """True when the old reading was complete for what existed when it was taken.

    Three things must be on the record for this to be assertable, and if any of
    them is missing the answer is no rather than a guess: the old row's own
    count of how many legs its arm held at the time, agreement between that and
    the count it published, and a later reading that holds more. Together they
    say the journal grew — and a reading that counted everything there was to
    count is not an incorrect reading.
    """
    from app.research.phase47 import COUNT_IS_COMPLETE

    held = old_row.get("legs_available")
    grew = new_row.get("legs_available")
    if not isinstance(held, int) or not isinstance(grew, int):
        return False
    if str(old_row.get("count_is") or "") != COUNT_IS_COMPLETE:
        return False
    if int(old_row.get("calls") or 0) != held:
        return False
    return grew > held and int(new_row.get("calls") or 0) > held


def _under_current_selection(row: dict, snapshot_id: str) -> bool:
    """True when this recorded row was already taken under the current selection.

    The selection is not stored as a column — it is baked into the snapshot id —
    so it is recovered by recomputing the id the row *would* have had under the
    corrected selection and comparing. Without this, an earlier recount taken
    under the corrected selection would be superseded by a later one and stamped
    with a reason about a bug it was never recorded under.
    """
    from app.research.phase47 import COUNT_IS_A_FLOOR
    from app.research.phase47 import store as p47store

    # A reading that stopped at its bound is never the current answer, however
    # recent it is: its count is a property of the limit. It stays supersedable
    # so that a later, fuller recount replaces it instead of being skipped.
    if str(row.get("count_is") or "") == COUNT_IS_A_FLOOR:
        return False
    return snapshot_id == p47store.snapshot_id(
        str(row.get("session") or ""),
        str(row.get("arm") or ""),
        str(row.get("definition") or ""),
        row.get("marked_through_ts"),
        # The fingerprint, because that is what Phase 47 stamps ids with; the
        # SELECTION text is what the registry records for a human reader.
        selection_fingerprint(),
    )


def registry(*, session: str | None = None, limit: int = 200) -> dict:
    """The supersession registry — every correction, in the order it was made."""
    with _LOCK:
        con = _connection()
        rows = store_mod.records(con, session=session, limit=limit)
        totals = store_mod.counts(con)
    return {"records": rows, "count": len(rows), "totals": totals, **_chrome()}


def status() -> dict:
    """Where the registry lives, and what this phase is allowed to do."""
    with _LOCK:
        totals = store_mod.counts(_connection())
    return {
        "db_path": store_mod.db_path(),
        "records": totals,
        "version": VERSION,
        **_chrome(),
    }


def _chrome() -> dict:
    return {
        "classification": CLASSIFICATION,
        "rule_fingerprint": rule_fingerprint(),
        "selection_fingerprint": selection_fingerprint(),
        "append_only": APPEND_ONLY,
        "supersession_is_additive": SUPERSESSION_IS_NOT_A_DELETION,
        "not_a_result": NOT_A_RESULT,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
    }


def reset() -> None:
    """Drop the connection. For the smoke and the CLI."""
    global _CON
    with _LOCK:
        if _CON is not None:
            try:
                _CON.close()
            except Exception:  # pragma: no cover - closing a dead handle
                pass
        _CON = None
