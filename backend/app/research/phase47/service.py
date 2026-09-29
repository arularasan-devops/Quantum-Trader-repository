"""Phase 47 — building the live call board and recording its session tally.

**What a refresh costs.** One indexed read of the Phase 46 journal for the
session's calls, then one indexed read per distinct contract for that contract's
latest measured quote. No historical scan, no rescan of a path, no study. The
per-contract read is the reason calls are bounded per request: a board whose
cost grows with the store is a board nobody can afford to poll.

**Where the mark comes from.** The raw Phase 35 store, opened read-only, for the
same contract symbol in the same session, strictly after the entry instant. That
is the same source and the same session rule the Phase 45 resolver uses, so a
call marked here and the same call resolved later cannot disagree about which
quotes existed. Nothing is interpolated and nothing crosses a session boundary.

**The two columns.** The research column holds the instants the research
definition admitted. The production column holds the leg the production board
itself selected at instants it called a buy. Both are marked by the same
function on the same quotes with the same costs, so the columns differ in which
legs they contain and in nothing else. They are also **not independent
samples** — they overlap wherever both admitted the same instant — and every
payload says so.

This module has no INSERT into any other phase's tables, no write to the Phase
46 journal, and no import that reaches an order, a broker or an execution
module. It returns payloads to a read-only API and to a CLI.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

from app.research.phase35 import path as p35path
from app.research.phase35 import store as p35store
from app.research.phase45 import CE, PE, VEHICLE_LABELS
from app.research.phase45 import store as p45store
from app.research.phase46 import SUPPORTED
from app.research.phase46 import service as p46service
from app.research.phase46 import store as p46store
from app.research.phase47 import (
    ARM_PRODUCTION,
    ARM_RESEARCH,
    CALL_STATES,
    COLUMNS_REPORTED,
    COUNT_IS_A_FLOOR,
    COUNT_IS_COMPLETE,
    INSUFFICIENT_SAMPLE,
    MARKED,
    MIN_MARKED_FOR_A_COMPARISON,
    NO_CALLS_YET,
    NO_ORDER_PATH,
    NOT_A_PROMOTION,
    NOT_A_RECOMMENDATION,
    OPEN,
    PAPER_ONLY,
    PRODUCTION_UNCHANGED,
    RECOUNT,
    RESEARCH_CALL_BOARD,
    RESOLVED,
    VERSION,
)
from app.research.phase47 import mark as mark_mod
from app.research.phase47 import store as store_mod
from app.research.phase49 import (
    SUPERSEDED,
    SUPERSESSION_IS_NOT_A_DELETION,
    selection_fingerprint,
)
from app.research.phase49 import events as p49events
from app.research.phase49 import service as p49service

# The vehicles a call can be taken in. Options only, and CE and PE separately:
# the board answers "would this call have made money", and a futures leg at the
# same instant is a different instrument with a different round trip, which
# Phase 46 already shows side by side without ranking them.
CALL_VEHICLES: tuple[str, ...] = (CE, PE)

# How many calls one board read will price. A bound, not a filter: what each arm
# actually holds is counted separately and reported beside it, so a reading that
# stopped at the ceiling is published as a floor rather than as the session's
# count. Raised from 240 once a real session was found to exceed it — a whole
# session's legs are worth reading in one pass, and repeated observations of one
# strike collapse into an event anyway.
MAX_CALLS = 5000

# The ceiling a **read-only** pass may raise that bound to. Separate from
# MAX_CALLS because the two exist for different reasons: MAX_CALLS keeps the live
# board's per-read cost bounded, and a diagnostic that has to answer "is this
# count the session's or the limit's" cannot be capped at the number it is trying
# to see past. A coverage figure taken at exactly the bound is a floor, and a
# floor read as a measurement is the fault the superseded tally was written with.
MAX_READ = 200000

_LOCK = threading.Lock()
_CON = None

# When the tally was last journalled by the automatic recorder, and how often it
# is allowed to run. The board itself is cheap, but cheap times every decision
# instant is not: the interval makes the recorder's cost a property of the clock
# rather than of how densely the session is sampled.
_LAST_RECORD = 0.0
RECORD_INTERVAL_SEC = 120.0


def definition() -> str:
    """The Phase 46 definition the calls were admitted under.

    Phase 47 has no definition of its own, deliberately: it introduces no
    admission rule. It prices what another definition admitted, and inherits
    that fingerprint so a tally can never be compared across two of them.
    """
    return p46service.definition()


def _connection():
    global _CON
    if _CON is None:
        _CON = store_mod.connect()
    return _CON


def _num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _calls(
    *, session: str | None, instrument: str | None, limit: int,
) -> tuple[list[dict], list[dict], str | None, int]:
    """The research and production legs of one session, from the Phase 46 journal.

    Read-only, through Phase 46's own accessor. The session defaults to the
    newest one the journal holds rather than to today, so the board is
    inspectable out of hours instead of reading empty.
    """
    path = p46store.db_path()
    if not os.path.exists(path):
        return [], [], None, 0, {ARM_RESEARCH: 0, ARM_PRODUCTION: 0}
    con = p46store.connect(path)
    try:
        target = session
        if not target:
            row = con.execute(
                "SELECT session FROM overlay_event "
                "WHERE session IS NOT NULL ORDER BY decision_ts DESC LIMIT 1"
            ).fetchone()
            target = str(row["session"]) if row and row["session"] else None
        if not target:
            return [], [], None, 0, {ARM_RESEARCH: 0, ARM_PRODUCTION: 0}
        marks = ",".join("?" for _ in CALL_VEHICLES)
        params: list[object] = [target, *CALL_VEHICLES]
        clause = ""
        if instrument:
            clause = " AND instrument = ?"
            params.append(instrument)
        total = con.execute(
            f"SELECT COUNT(*) AS n FROM overlay_event WHERE session = ? "
            f"AND vehicle IN ({marks}){clause}",
            params,
        ).fetchone()
        # Each arm is selected by its own condition in SQL and bounded after
        # that condition, never before it. Taking the first rows of the session
        # and looking for admitted instants inside them finds only whatever the
        # session happened to open with — on a full day that is the warm-up,
        # and the board reads empty while the journal holds admissions.
        bound = max(1, min(int(limit), MAX_READ))
        research_where = (
            f"WHERE session = ? AND vehicle IN ({marks}){clause} "
            f"AND overlay_state = ?"
        )
        research_params = [*params, SUPPORTED]
        production_where = (
            f"WHERE session = ? AND vehicle IN ({marks}){clause} "
            f"AND UPPER(COALESCE(production_signal_normalised, '')) = 'BUY' "
            f"AND engine_selected_vehicle = vehicle"
        )
        research_rows = con.execute(
            f"SELECT * FROM overlay_event {research_where} "
            f"ORDER BY decision_ts ASC LIMIT ?",
            [*research_params, bound],
        ).fetchall()
        production_rows = con.execute(
            f"SELECT * FROM overlay_event {production_where} "
            f"ORDER BY decision_ts ASC LIMIT ?",
            [*params, bound],
        ).fetchall()
        # How many each arm *has*, not how many the bound let through. A count
        # that stopped at its own ceiling and does not say so is the same fault
        # as the tally this phase exists to correct.
        available = {
            ARM_RESEARCH: con.execute(
                f"SELECT COUNT(*) AS n FROM overlay_event {research_where}",
                research_params,
            ).fetchone(),
            ARM_PRODUCTION: con.execute(
                f"SELECT COUNT(*) AS n FROM overlay_event {production_where}",
                params,
            ).fetchone(),
        }
        counted = {
            arm: int((row["n"] if row else 0) or 0)
            for arm, row in available.items()
        }
    finally:
        con.close()
    return (
        [dict(r) for r in research_rows],
        [dict(r) for r in production_rows],
        target,
        int(total["n"] or 0) if total else 0,
        counted,
    )


def _latest_quotes(calls: list[dict]) -> dict[tuple[str, str], dict]:
    """The latest measured quote for each contract, same session, after entry.

    One read per distinct contract, not per call: several calls on the same
    contract at different instants are all marked against the same latest quote,
    which is also the only quote any of them could be marked at now.
    """
    wanted: dict[tuple[str, str], float] = {}
    for call in calls:
        contract = call.get("contract")
        vehicle = call.get("vehicle")
        after = _num(call.get("decision_ts"))
        if not contract or not vehicle or after is None:
            continue
        key = (str(contract), str(vehicle))
        wanted[key] = min(wanted.get(key, after), after)
    if not wanted:
        return {}
    raw_path = p35store.db_path()
    if not os.path.exists(raw_path):
        return {}
    try:
        raw = p45store.open_read_only(raw_path)
    except sqlite3.OperationalError:
        return {}
    out: dict[tuple[str, str], dict] = {}
    try:
        for (contract, vehicle), after in wanted.items():
            row = raw.execute(
                "SELECT ts, bid, ask, traded, evidence FROM raw_quote "
                "WHERE symbol = ? AND vehicle = ? AND ts > ? AND ts < ? "
                "ORDER BY ts DESC LIMIT 1",
                (contract, vehicle, float(after),
                 p35path.session_bounds(float(after))[1]),
            ).fetchone()
            if row is not None:
                out[(contract, vehicle)] = dict(row)
    finally:
        raw.close()
    return out


def _mark_arm(
    calls: list[dict], quotes: dict[tuple[str, str], dict], *, now: float,
) -> list[dict]:
    """Price one column's legs, keeping them in decision order for the drawdown."""
    marked: list[dict] = []
    for call in calls:
        key = (str(call.get("contract") or ""), str(call.get("vehicle") or ""))
        quote = quotes.get(key)
        # A quote is only a mark for a call that opened before it. Reusing one
        # contract's latest quote for a call taken after it would mark a leg at
        # a price from its own past.
        after = _num(call.get("decision_ts"))
        if quote is not None and after is not None:
            qts = _num(quote.get("ts"))
            if qts is None or qts <= after:
                quote = None
        row = mark_mod.mark(call, quote, now=now)
        row["lifecycle"] = _lifecycle(call, now=now)
        marked.append(row)
    return marked


def _lifecycle(call: dict, *, now: float) -> str:
    """OPEN while the call's own session is still running, RESOLVED after it."""
    ts = _num(call.get("decision_ts"))
    if ts is None:
        return OPEN
    return OPEN if now < p35path.session_bounds(ts)[1] else RESOLVED


def selection_key(
    selection: str, truncated: bool, limit: int, *, recount: bool = False,
) -> str:
    """The selection a tally was taken under, including its bound when binding.

    Two complete readings of the same instant are the same fact whatever bound
    they were read at, so they share an id and the second converges on the
    first. A reading that stopped at its bound is a *different, weaker* fact and
    gets its own id — otherwise re-reading with a larger limit would be swallowed
    as a duplicate of the truncated row, which is exactly how the broken 8
    survived its own fix.

    A *recount* is a third case: a row written before the bound was stored on it
    cannot be told from a floor, so a reading that disagrees with it is given its
    own identity rather than colliding with the row it contradicts.
    """
    key = f"{selection}|{RECOUNT}" if recount else selection
    return f"{key}|FLOOR{int(limit)}" if truncated else key


def _bound(available: int, selected: int, limit: int) -> dict:
    """What this arm holds, what the bound let through, and which is reported.

    Read as a floor when the bound was reached: the count is then a property of
    the limit, not of the session, and calling it the session's count is the
    fault the superseded tally was recorded with.
    """
    hit = selected >= limit and available > selected
    return {
        "available": available,
        "selected": selected,
        "limit": limit,
        "truncated": hit,
        "count_is": COUNT_IS_A_FLOOR if hit else COUNT_IS_COMPLETE,
        "reported": f"at least {selected}" if hit else str(selected),
    }


def board(
    *,
    instrument: str | None = None,
    session: str | None = None,
    limit: int = 60,
    now: float | None = None,
) -> dict:
    """The live call board: the research's own paper calls, priced right now.

    Every call carries its entry side and price, the executable side it is
    marked on, the round trip charged against it and its running net — and where
    it cannot be priced, the reason, in the same list rather than dropped from
    it.
    """
    clock = float(now if now is not None else time.time())
    bound = max(1, min(int(limit), MAX_CALLS))
    research_calls, production_calls, target, journalled, available = _calls(
        session=session, instrument=instrument, limit=bound,
    )
    quotes = _latest_quotes([*research_calls, *production_calls])
    research = _mark_arm(research_calls, quotes, now=clock)
    production = _mark_arm(production_calls, quotes, now=clock)
    research_tally = mark_mod.tally(research)
    production_tally = mark_mod.tally(production)
    shared = _shared(research_calls, production_calls)
    return {
        "session": target,
        "instrument": instrument,
        "state": NO_CALLS_YET if not research and not production else MARKED,
        "calls": list(reversed(research)),
        "production_calls": list(reversed(production)),
        "arms": {
            ARM_RESEARCH: research_tally,
            ARM_PRODUCTION: production_tally,
        },
        # Both counts, side by side, per arm. Nine ticks on one strike inside a
        # minute are nine legs and one opportunity; a win rate or a sample bar
        # taken over the legs claims nine times the evidence it has.
        "events": {
            ARM_RESEARCH: p49events.group(_armed(research, ARM_RESEARCH)),
            ARM_PRODUCTION: p49events.group(_armed(production, ARM_PRODUCTION)),
        },
        "event_counts": {
            ARM_RESEARCH: p49events.counts(_armed(research, ARM_RESEARCH)),
            ARM_PRODUCTION: p49events.counts(_armed(production, ARM_PRODUCTION)),
        },
        "selection": selection_fingerprint(),
        # The bound this reading was taken under, per arm, and whether it was
        # reached. A count that stopped at its ceiling is reported as a floor.
        "bound": {
            "limit": bound,
            "max": MAX_CALLS,
            ARM_RESEARCH: _bound(available[ARM_RESEARCH], len(research_calls),
                                 bound),
            ARM_PRODUCTION: _bound(available[ARM_PRODUCTION],
                                   len(production_calls), bound),
        },
        "comparison": _comparison(research_tally, production_tally, shared),
        "journalled_rows_in_session": journalled,
        "truncated": (len(research_calls) >= bound
                      or len(production_calls) >= bound),
        "limit": bound,
        "marked_through_ts": max(
            [r["mark_ts"] for r in [*research, *production]
             if _num(r.get("mark_ts")) is not None] or [None]
        ),
        "as_of_ts": clock,
        "call_states": list(CALL_STATES),
        "vehicle_labels": VEHICLE_LABELS,
        "vehicles": list(CALL_VEHICLES),
        "definition": definition(),
        "classification": RESEARCH_CALL_BOARD,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
        "not_a_recommendation": NOT_A_RECOMMENDATION,
        "not_a_promotion": NOT_A_PROMOTION,
        "version": VERSION,
    }


def _armed(rows: list[dict], arm: str) -> list[dict]:
    """Rows tagged with their column, so one arm's legs never group into another's."""
    return [{**row, "arm": arm} for row in rows]


def arms(
    *,
    instrument: str | None = None,
    session: str | None = None,
    limit: int = MAX_CALLS,
) -> dict:
    """The raw journalled legs of both arms, tagged, with the bound they came under.

    Public so a later phase can resolve or re-group these legs **through this
    selection** rather than writing its own. The selection is the one fault this
    board has already been corrected for once — bounding the session before
    filtering it by arm — and a second copy of that SQL somewhere else is a
    second chance to reintroduce it.

    Returns the legs exactly as journalled, unmarked: what a caller needs to
    price an event is the entry side, the entry price and the round trip
    measured at the instant, all of which are on the row.
    """
    bound = max(1, min(int(limit), MAX_READ))
    research, production, target, journalled, available = _calls(
        session=session, instrument=instrument, limit=bound,
    )
    return {
        "session": target,
        "instrument": instrument,
        "journalled_rows_in_session": journalled,
        "legs": {
            ARM_RESEARCH: _armed(research, ARM_RESEARCH),
            ARM_PRODUCTION: _armed(production, ARM_PRODUCTION),
        },
        "bound": {
            "limit": bound,
            "max": MAX_CALLS,
            "read_ceiling": MAX_READ,
            ARM_RESEARCH: _bound(available[ARM_RESEARCH], len(research), bound),
            ARM_PRODUCTION: _bound(available[ARM_PRODUCTION], len(production),
                                   bound),
        },
        "selection": selection_fingerprint(),
        "definition": definition(),
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "version": VERSION,
    }


def _shared(research: list[dict], production: list[dict]) -> int:
    """How many legs the two columns have in common.

    Reported with the comparison because it is what stops the two columns being
    read as independent samples: where both admitted the same instant, the same
    leg is in both totals.
    """
    ids = {str(r.get("event_id")) for r in research}
    return sum(1 for p in production if str(p.get("event_id")) in ids)


def _comparison(research: dict, production: dict, shared: int) -> dict:
    """The two columns beside each other, with the sample honesty attached."""
    enough = (
        int(research.get("marked") or 0) >= MIN_MARKED_FOR_A_COMPARISON
        and int(production.get("marked") or 0) >= MIN_MARKED_FOR_A_COMPARISON
    )
    return {
        "verdict": COLUMNS_REPORTED if enough else INSUFFICIENT_SAMPLE,
        "min_marked_for_a_comparison": MIN_MARKED_FOR_A_COMPARISON,
        "research_marked": research.get("marked"),
        "production_marked": production.get("marked"),
        "shared_legs": shared,
        "not_independent": (
            "THE_TWO_COLUMNS_OVERLAP: where the research admitted an instant "
            "the production board also bought, the same paper leg is counted "
            "in both totals. The difference between the columns describes "
            "which legs each admitted, and is not a test of one against the "
            "other."
        ),
        "session_only": (
            "ONE_SESSION_IS_NOT_A_SAMPLE: a session's running net is one draw "
            "from one definition. The standing research answer is still MIXED "
            "on entry and direction, so losing sessions are expected and are "
            "evidence, not a fault."
        ),
    }


def record(
    *,
    instrument: str | None = None,
    session: str | None = None,
    limit: int = MAX_CALLS,
    now: float | None = None,
    recount_arms: frozenset[str] = frozenset(),
) -> dict:
    """Journal what the board says now, once per session, arm and mark instant.

    ``recount_arms`` names arms whose recorded tally carries no bound, so this
    reading must land beside it instead of converging on it. Phase 49 passes it
    when it finds such a row disagreeing with the board; nothing else sets it.

    Idempotent: called twice with no new quote in between, the second call
    writes nothing. That is why the snapshot id carries the instant marked
    through — a tally is only new when the market it was taken on has moved.
    """
    payload = board(
        instrument=instrument, session=session, limit=limit, now=now,
    )
    target = payload.get("session")
    if not target:
        return {"written": 0, "duplicate": 0, "session": None,
                "state": NO_CALLS_YET}
    fingerprint = definition()
    selection = selection_fingerprint()
    through = payload.get("marked_through_ts")
    rows = []
    ids: dict[str, str] = {}
    for arm, tally in payload["arms"].items():
        if not int(tally.get("calls") or 0):
            continue
        bounds = payload["bound"][arm]
        ids[arm] = store_mod.snapshot_id(
            str(target), arm, fingerprint, through,
            selection_key(selection, bounds["truncated"], bounds["limit"],
                          recount=arm in recount_arms),
        )
        rows.append({
            "snapshot_id": ids[arm],
            "session": str(target),
            "arm": arm,
            "definition": fingerprint,
            "snapshot_ts": payload["as_of_ts"],
            "marked_through_ts": through,
            "instrument": instrument,
            "calls": int(tally.get("calls") or 0),
            "marked": int(tally.get("marked") or 0),
            "unmarkable": int(tally.get("unmarkable") or 0),
            "stale_marks": int(tally.get("stale_marks") or 0),
            "net_pct_total": tally.get("net_pct_total"),
            "net_pct_mean": tally.get("net_pct_mean"),
            "winners": tally.get("winners"),
            "losers": tally.get("losers"),
            "flat": tally.get("flat"),
            "win_rate_pct": tally.get("win_rate_pct"),
            "profit_factor": tally.get("profit_factor"),
            "best_pct": tally.get("best_pct"),
            "worst_pct": tally.get("worst_pct"),
            "avg_cost_points": tally.get("avg_cost_points"),
            "drawdown_pct": tally.get("drawdown_pct"),
            "lifecycle": _session_lifecycle(payload),
            # Recorded together so a stored tally can never be read as a
            # sample of independent opportunities it does not hold.
            "legs": int(
                payload["event_counts"][arm][p49events.LEG_COUNT]),
            "events": int(
                payload["event_counts"][arm][p49events.EVENT_COUNT]),
            "grouping_rule": payload["event_counts"][arm]["grouping_rule"],
            # And the bound it was taken under, so a stored count that stopped
            # at the ceiling is readable as a floor forever after, instead of
            # having to be recognised by someone noticing it equals the limit.
            "selection_bound": int(payload["bound"][arm]["limit"]),
            "legs_available": int(payload["bound"][arm]["available"]),
            "count_is": payload["bound"][arm]["count_is"],
            "status": PAPER_ONLY,
            "order_path": NO_ORDER_PATH,
            "version": VERSION,
        })
    with _LOCK:
        counts = store_mod.insert_snapshots(_connection(), rows)
    return {**counts, "session": target, "rows": len(rows),
            "definition": fingerprint, "selection": selection,
            "snapshot_ids": ids,
            "bound": payload.get("bound", {}),
            "event_counts": payload.get("event_counts", {})}


def _session_lifecycle(payload: dict) -> str:
    """RESOLVED only when every call on the board belongs to a finished session."""
    rows = [*payload.get("calls", []), *payload.get("production_calls", [])]
    if not rows:
        return OPEN
    return RESOLVED if all(r.get("lifecycle") == RESOLVED for r in rows) else OPEN


def maybe_record(*, now: float | None = None) -> dict:
    """Journal the session tally if the interval has elapsed. Never raises.

    Called by the Phase 46 writer *after* it has written its own rows, on the
    shadow worker thread, inside its own exception barrier — the same one-way
    arrangement Phase 46 has with Phase 45. This phase is the last link in that
    chain: it can lose its own snapshot and nothing upstream notices, and it
    cannot delay a tick because it does not run on one.

    A missed interval is not a missed number: the snapshot is a photograph of a
    tally that can always be recomputed from the journals, and only the
    photograph is lost.
    """
    global _LAST_RECORD
    clock = float(now if now is not None else time.time())
    with _LOCK:
        if clock - _LAST_RECORD < RECORD_INTERVAL_SEC:
            return {"skipped": True, "written": 0, "duplicate": 0}
        _LAST_RECORD = clock
    try:
        return {"skipped": False, **record(now=clock)}
    except Exception as exc:  # research must not break the writer above it
        return {"skipped": False, "written": 0, "duplicate": 0,
                "error": f"{type(exc).__name__}: {exc}"}


def sessions(*, limit: int = 40) -> dict:
    """The recorded session tallies — what the board said, session by session."""
    with _LOCK:
        con = _connection()
        rows = store_mod.latest_per_session(con, limit=limit)
        totals = store_mod.counts(con)
        # A superseded tally is not the latest row for its session and arm, so
        # the reduction above drops it — and a corrected count shown with no
        # trace of the count it corrects is the mistake edited out of the
        # record by omission rather than by an UPDATE. The rows the registry
        # names are read back and listed beside their replacements.
        registered = p49service.superseded_index()
        if registered:
            for day in {str(r.get("session")) for r in rows}:
                for row in store_mod.snapshots(con, session=day, limit=500):
                    if str(row.get("snapshot_id")) in registered:
                        rows.append(row)
    # CURRENT or SUPERSEDED is derived at read time from an append-only
    # registry; no recorded row is edited to carry its own status.
    rows = p49service.annotate(rows)
    rows.sort(
        key=lambda r: (
            str(r.get("session")), str(r.get("arm")),
            r.get("record_status") == SUPERSEDED,
        ),
        reverse=True,
    )
    return {
        "sessions": rows,
        "count": len(rows),
        "totals": totals,
        "arms": [ARM_RESEARCH, ARM_PRODUCTION],
        "superseded": [r for r in rows if r.get("record_status") == SUPERSEDED],
        "supersession_is_additive": SUPERSESSION_IS_NOT_A_DELETION,
        "selection": selection_fingerprint(),
        # Recorded rows carry the bound they were taken under, so a count that
        # stopped at its ceiling reads as a floor without the reader having to
        # notice it equals the limit.
        "count_is_complete": COUNT_IS_COMPLETE,
        "count_is_a_floor": COUNT_IS_A_FLOOR,
        "definition": definition(),
        "classification": RESEARCH_CALL_BOARD,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "not_a_promotion": NOT_A_PROMOTION,
        "version": VERSION,
    }


def status() -> dict:
    """Where the board reads from and what it is allowed to do."""
    with _LOCK:
        totals = store_mod.counts(_connection())
    return {
        "enabled": p46service.enabled(),
        "definition": definition(),
        "version": VERSION,
        "db_path": store_mod.db_path(),
        "overlay_db_path": p46store.db_path(),
        "raw_store_path": p35store.db_path(),
        "recorded": totals,
        "vehicles": list(CALL_VEHICLES),
        "classification": RESEARCH_CALL_BOARD,
        "status": PAPER_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
        "not_a_recommendation": NOT_A_RECOMMENDATION,
        "not_a_promotion": NOT_A_PROMOTION,
    }


def reset() -> None:
    """Drop the connection and the recorder's clock. For the smoke and the CLI."""
    global _CON, _LAST_RECORD
    with _LOCK:
        _LAST_RECORD = 0.0
        if _CON is not None:
            try:
                _CON.close()
            except Exception:  # pragma: no cover - closing a dead handle
                pass
        _CON = None
