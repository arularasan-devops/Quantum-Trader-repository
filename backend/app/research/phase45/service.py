"""Phase 45 — the live hook, the counters, and the board payload.

One entry point from the tick path, :func:`observe`, called with the Phase 17
observation *after* it has been captured and persisted. It cannot change what
Phase 17 wrote, it takes no decision back to the caller, and it swallows its own
failures: a research board that can break a tick is a production change wearing
a research label. Every exception is counted and dropped.

Observability is aggregate, held in memory and read through :func:`stats`.
Per-tick logging would put a line in the log for every vehicle of every
instrument of every minute, which on a 46-name universe is how a log becomes
unreadable and a disk becomes full — the failure mode that leaves no trace in
the capture journals.

Live and replay both come through :func:`evaluate_observation`, so a stored
session and a live minute are scored by the same code. The only difference is
where the observation came from and what the clock says.

Nothing in here runs on the tick path except a queue put. The first version
evaluated and journalled inline and shared one lock with the board reads, so a
dashboard poll — 170ms over 22k rows, and growing with the journal — stalled the
scan loop that refreshes prices, and the outcome resolver's walk of the raw store
stalled it for as long as that walk took. A research board that can slow the
quotes is a production change wearing a research label, so the work moved to a
single background writer and the reads to their own connection.
"""
from __future__ import annotations

import queue
import threading
import time

from app.config import settings
from app.research.phase17 import schema
from app.research.phase45 import (
    CE,
    FUTURES,
    NO_ORDER_PATH,
    PAPER_ONLY,
    PE,
    RESEARCH_ONLY,
    SHADOW_BUY,
    SHADOW_SELL,
    SHADOW_UNMEASURED,
    VEHICLE_LABELS,
    VEHICLES,
    evaluator,
    ranges,
    resolver,
    store,
)
from app.research.phase45.freeze import definition

# How often the resolver is allowed to walk the raw store looking for forward
# paths. Bounded because that walk reads the same database the capture writes to,
# and a research board is not permitted to compete with the capture for it.
RESOLVE_EVERY_SEC = 600.0
RESOLVE_BATCH = 200

# The tick path hands observations over and walks away. Bounded, because an
# unbounded queue in front of a slow disk is a memory leak that takes the whole
# process with it: past this depth the shadow board drops observations and counts
# the drops, which costs research rows and costs the capture nothing.
QUEUE_MAX = 2000
_WORKER_IDLE_SEC = 1.0

_LOCK = threading.Lock()          # the counters, and nothing else
_WRITE_LOCK = threading.Lock()    # the writer connection
_READ_LOCK = threading.Lock()     # the reader connection
_RANGES = ranges.LiveRanges()
_WCON = None
_RCON = None
_QUEUE: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
_WORKER: threading.Thread | None = None
_STOP = threading.Event()

# Aggregate counters only. Named for what they distinguish: an evaluation that
# produced nothing and an evaluation that never ran look the same in a total.
_STATE: dict[str, float] = {
    "evaluations": 0,
    "rows": 0,
    "signals": 0,
    "waits": 0,
    "unmeasured": 0,
    "journal_written": 0,
    "journal_duplicate": 0,
    "failures": 0,
    "last_error": "",
    "last_observation_ts": 0.0,
    "eval_ms_total": 0.0,
    "eval_ms_max": 0.0,
    "resolve_runs": 0,
    "resolved": 0,
    "resolve_considered": 0,
    "last_resolve_ts": 0.0,
    "queued": 0,
    "dropped": 0,
    "queue_depth_max": 0,
}


def enabled() -> bool:
    return bool(settings.phase45_shadow)


def _writer_connection():
    """The connection the background writer and the resolver write through.

    Opened once and reused: a connect-per-observation on a 13 GB data directory
    is a cost this board is not worth. Held under ``_WRITE_LOCK``, which the
    board reads deliberately do not take.
    """
    global _WCON
    if _WCON is None:
        _WCON = store.connect()
        _WCON.execute("PRAGMA journal_mode=WAL")
    return _WCON


def _reader_connection():
    """A second connection, for reads only.

    Separate from the writer's on purpose. Sharing one meant a dashboard poll
    and the background write queued behind the same lock, so the board's cost —
    which grows with the journal — was charged to whatever else held it. WAL
    lets a reader read while a writer writes, so the two no longer wait on each
    other at all.
    """
    global _RCON
    if _RCON is None:
        _RCON = store.connect()
        _RCON.execute("PRAGMA journal_mode=WAL")
    return _RCON


def note_price(instrument: str, ts: float, price: float | None) -> None:
    """Feed the pre-decision range. Safe to call on every tick."""
    _RANGES.note(instrument, ts, price)


def evaluate_observation(obs: schema.Observation, *, ingest_ts: float) -> list[dict]:
    """The rows one observation produces. Pure: no journal, no counters.

    Shared by the live hook and the replay CLI, which is what makes a replayed
    session comparable to the live one rather than a second implementation of
    the same idea.
    """
    _RANGES.note(obs.instrument, obs.signal_ts, _underlying(obs))
    return evaluator.evaluate(
        obs,
        trailing_range=_RANGES.before(obs.instrument, obs.signal_ts),
        definition=definition(),
        ingest_ts=ingest_ts,
    )


def observe(obs: schema.Observation) -> dict:
    """Hand one captured observation to the shadow board. Never raises.

    This is the only thing Phase 45 does on the tick path, and all it does is
    put the observation on a queue: no evaluation, no SQLite, no lock the board
    reads can be holding. Returns whether it was queued, never a decision; the
    tick path ignores the return.
    """
    if not enabled():
        return {"queued": False, "dropped": False}
    try:
        _ensure_worker()
        _QUEUE.put_nowait(obs)
        depth = _QUEUE.qsize()
        with _LOCK:
            _STATE["queued"] = float(_STATE["queued"]) + 1
            if depth > float(_STATE["queue_depth_max"]):
                _STATE["queue_depth_max"] = depth
        return {"queued": True, "dropped": False}
    except queue.Full:
        # The writer is behind. Dropping a research row is the correct loss here
        # — the alternative is making the capture wait for a board.
        with _LOCK:
            _STATE["dropped"] = float(_STATE["dropped"]) + 1
        return {"queued": False, "dropped": True}
    except Exception as exc:  # a research board must not break a tick
        with _LOCK:
            _STATE["failures"] = float(_STATE["failures"]) + 1
            _STATE["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
        return {"queued": False, "dropped": False}


def _ensure_worker() -> None:
    """Start the single background writer, once."""
    global _WORKER
    if _WORKER is not None and _WORKER.is_alive():
        return
    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _STOP.clear()
        _WORKER = threading.Thread(
            target=_run_worker, name="phase45-shadow-writer", daemon=True,
        )
        _WORKER.start()


def _run_worker() -> None:
    """Evaluate and journal queued observations, one at a time, forever.

    One worker, not a pool: the trailing range is a sequence, and two threads
    scoring the same instrument's observations out of order would measure a
    range that never existed. FIFO preserves the order the capture saw.
    """
    while not _STOP.is_set():
        try:
            obs = _QUEUE.get(timeout=_WORKER_IDLE_SEC)
        except queue.Empty:
            continue
        try:
            _process(obs)
        except Exception as exc:  # one bad observation must not end the worker
            with _LOCK:
                _STATE["failures"] = float(_STATE["failures"]) + 1
                _STATE["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
        finally:
            _QUEUE.task_done()


def _process(obs: schema.Observation) -> dict:
    """The work the worker does: evaluate, journal, count. Off the tick path."""
    started = time.time()
    rows = evaluate_observation(obs, ingest_ts=started)
    with _WRITE_LOCK:
        counts = store.insert_events(_writer_connection(), rows)
    _tally(rows, counts, started)
    _hand_over(rows)
    return {"rows": len(rows), **counts}


def _hand_over(rows: list[dict]) -> None:
    """Give the journalled rows to the Phase 46 overlay, after journalling.

    Last, and deliberately so: the evidence is already on disk, this phase's
    counters are already updated, and the overlay is a view of what was just
    written rather than a participant in writing it. Imported locally to keep
    the dependency one-directional at module level — nothing in Phase 45's
    admission path can see Phase 46 — and wrapped because a downstream view
    losing its own row must not cost this journal an observation.
    """
    try:
        from app.research.phase46 import service as p46

        p46.record(rows)
    except Exception:  # pragma: no cover - a view must not break the journal
        pass


def drain(timeout: float = 30.0) -> bool:
    """Wait for the queue to be written. For the smoke suite and the CLI.

    Live callers never need this: the board reads whatever is on disk, and a row
    still in flight simply is not on it yet.
    """
    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        if _QUEUE.unfinished_tasks == 0:
            return True
        time.sleep(0.01)
    return _QUEUE.unfinished_tasks == 0


def _tally(rows: list[dict], counts: dict, started: float) -> None:
    elapsed_ms = (time.time() - started) * 1000.0
    signals = sum(1 for r in rows
                  if r["shadow_action"] in (SHADOW_BUY, SHADOW_SELL))
    unmeasured = sum(1 for r in rows
                     if r["shadow_action"] == SHADOW_UNMEASURED)
    with _LOCK:
        _STATE["evaluations"] = float(_STATE["evaluations"]) + 1
        _STATE["rows"] = float(_STATE["rows"]) + len(rows)
        _STATE["signals"] = float(_STATE["signals"]) + signals
        _STATE["unmeasured"] = float(_STATE["unmeasured"]) + unmeasured
        _STATE["waits"] = float(_STATE["waits"]) + len(rows) - signals - unmeasured
        _STATE["journal_written"] = (
            float(_STATE["journal_written"]) + counts["written"]
        )
        _STATE["journal_duplicate"] = (
            float(_STATE["journal_duplicate"]) + counts["duplicate"]
        )
        _STATE["last_observation_ts"] = started
        _STATE["eval_ms_total"] = float(_STATE["eval_ms_total"]) + elapsed_ms
        if elapsed_ms > float(_STATE["eval_ms_max"]):
            _STATE["eval_ms_max"] = elapsed_ms


def _underlying(obs: schema.Observation) -> float | None:
    """The price the trailing range is measured on.

    The underlying, or the futures mid where the underlying was not quoted —
    a range is a volatility measurement, not a fill, so a mid is legitimate
    here and would not be in an entry price. Option premiums are never used:
    a premium range mixes the move with the decay.
    """
    for quote in (obs.selected, obs.opposite):
        if quote is not None and isinstance(quote.underlying_price, (int, float)):
            return float(quote.underlying_price)
    fut = obs.futures
    if fut is not None and isinstance(fut.mid, (int, float)):
        return float(fut.mid)
    return None


def stats() -> dict:
    """Aggregate observability, read by the status endpoint."""
    with _LOCK:
        state = dict(_STATE)
    evaluations = float(state["evaluations"]) or 1.0
    return {
        "enabled": enabled(),
        "definition": definition(),
        "evaluations": int(state["evaluations"]),
        "rows": int(state["rows"]),
        "signals": int(state["signals"]),
        "waits": int(state["waits"]),
        "unmeasured": int(state["unmeasured"]),
        "journal_written": int(state["journal_written"]),
        "journal_duplicate": int(state["journal_duplicate"]),
        "failures": int(state["failures"]),
        "last_error": state["last_error"] or None,
        "last_observation_ts": state["last_observation_ts"] or None,
        "eval_ms_avg": round(float(state["eval_ms_total"]) / evaluations, 3),
        "eval_ms_max": round(float(state["eval_ms_max"]), 3),
        "resolve_runs": int(state["resolve_runs"]),
        "resolved": int(state["resolved"]),
        "resolve_considered": int(state["resolve_considered"]),
        "last_resolve_ts": state["last_resolve_ts"] or None,
        # What the tick path handed over, what the writer could not keep up
        # with, and the deepest the backlog has been. A non-zero drop count is
        # the board losing rows, never the capture losing a minute.
        "queued": int(state["queued"]),
        "dropped": int(state["dropped"]),
        "queue_depth": _QUEUE.qsize(),
        "queue_depth_max": int(state["queue_depth_max"]),
        "paper_only": True,
        "order_path": False,
    }


def reset() -> None:
    """Counters, queue, worker and ranges back to empty. For tests only."""
    global _WCON, _RCON, _WORKER
    _STOP.set()
    worker = _WORKER
    if worker is not None and worker.is_alive():
        worker.join(2.0)
    _WORKER = None
    while True:
        try:
            _QUEUE.get_nowait()
        except queue.Empty:
            break
        else:
            _QUEUE.task_done()
    with _LOCK:
        for key in _STATE:
            _STATE[key] = "" if key == "last_error" else 0
    with _WRITE_LOCK:
        if _WCON is not None:
            _WCON.close()
            _WCON = None
    with _READ_LOCK:
        if _RCON is not None:
            _RCON.close()
            _RCON = None
    _RANGES.reset()


def resolve_due(*, now: float | None = None, force: bool = False) -> dict:
    """Resolve paper outcomes whose forward window has had time to happen.

    Rate-limited and skipped entirely when another pass is too recent, so this
    can be called from a periodic task without the caller tracking when it last
    ran. Never raises: an unresolvable batch is counted, not propagated.
    """
    clock = float(now if now is not None else time.time())
    with _LOCK:
        last = float(_STATE["last_resolve_ts"])
        if not force and last and clock - last < RESOLVE_EVERY_SEC:
            return {"skipped": True, "considered": 0, "resolved": 0}
        _STATE["last_resolve_ts"] = clock
    delay_min = max(0, int(settings.phase45_resolve_after_minutes))
    try:
        # Under the writer's lock, not the counters' and not the readers': this
        # walk reads the raw store and can take seconds, and the tick path must
        # not be able to end up behind it.
        with _WRITE_LOCK:
            out = resolver.resolve_pending(
                _writer_connection(), limit=RESOLVE_BATCH,
                min_age_sec=delay_min * 60.0, now=clock,
            )
        with _LOCK:
            _STATE["resolve_runs"] = float(_STATE["resolve_runs"]) + 1
            _STATE["resolved"] = float(_STATE["resolved"]) + out["resolved"]
            _STATE["resolve_considered"] = (
                float(_STATE["resolve_considered"]) + out["considered"]
            )
        return {"skipped": False, **out}
    except Exception as exc:  # resolution is bookkeeping, never a blocker
        with _LOCK:
            _STATE["failures"] = float(_STATE["failures"]) + 1
            _STATE["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
        return {"skipped": False, "considered": 0, "resolved": 0,
                "error": type(exc).__name__}


def performance(summary: dict) -> dict:
    """The tally, plus the ratios a reader would otherwise compute wrongly.

    A profit factor over zero resolved events is ``None``, not zero and not
    infinity: the quantity does not exist yet, and a board that prints 0.00 for
    it is making a claim the sample cannot support.
    """
    resolved = int(summary.get("resolved") or 0)
    gains = float(summary.get("gain_sum") or 0.0)
    losses = float(summary.get("loss_sum") or 0.0)
    wins = int(summary.get("wins") or 0)
    net = summary.get("net_points")
    out = dict(summary)
    out.update({
        "profit_factor": round(gains / losses, 3) if losses > 0 else None,
        "win_rate_pct": round(100.0 * wins / resolved, 2) if resolved else None,
        "avg_net_points": (
            round(float(net) / resolved, 4)
            if resolved and isinstance(net, (int, float)) else None
        ),
        "t1_hit_rate_pct": _rate(summary.get("t1_hits"), resolved),
        "t2_hit_rate_pct": _rate(summary.get("t2_hits"), resolved),
        "t3_hit_rate_pct": _rate(summary.get("t3_hits"), resolved),
        "paper_only": True,
        "promoted": False,
    })
    return out


def _rate(hits: object, resolved: int) -> float | None:
    if not resolved or not isinstance(hits, (int, float)):
        return None
    return round(100.0 * float(hits) / resolved, 2)


def journal(*, filters: dict | None = None, limit: int = 200) -> dict:
    """The journalled events themselves, newest first, with their outcomes."""
    with _READ_LOCK:
        rows = store.events(_reader_connection(), filters=filters, limit=limit)
    return {
        "rows": rows,
        "count": len(rows),
        "definition": definition(),
        "status": PAPER_ONLY,
        "research": RESEARCH_ONLY,
        "order_path": NO_ORDER_PATH,
    }


def boards(*, filters: dict | None = None, limit: int = 240) -> dict:
    """The dashboard payload: two boards, the comparison, and the counters.

    Read-only. The futures board and the options board are built from the same
    journal rows, split by vehicle rather than by query, so a row can never
    appear on one board with a different price than on the other.
    """
    with _READ_LOCK:
        con = _reader_connection()
        rows = store.latest_per_vehicle(con, filters=filters, limit=limit)
        summary = store.tally(con, filters=filters)
        options = store.choices(con)
    return {
        "definition": definition(),
        "vehicle_labels": VEHICLE_LABELS,
        "futures": [r for r in rows if r["vehicle"] == FUTURES],
        "calls": [r for r in rows if r["vehicle"] == CE],
        "puts": [r for r in rows if r["vehicle"] == PE],
        "comparison": comparison(rows),
        "performance": performance(summary),
        "filters": options,
        "stats": stats(),
        "status": PAPER_ONLY,
        "research": RESEARCH_ONLY,
        "order_path": NO_ORDER_PATH,
    }


def comparison(rows: list[dict]) -> list[dict]:
    """FUTURES vs CE vs PE where all three exist at the same instant.

    Grouped on ``(instrument, decision_ts)`` — the same instant, not the same
    minute — because the whole claim of a vehicle comparison is that the legs
    were priced together. A group missing a vehicle is reported with that
    vehicle absent rather than filled from a neighbouring timestamp.
    """
    groups: dict[tuple[str, float], dict] = {}
    for row in rows:
        key = (str(row["instrument"]), float(row["decision_ts"]))
        group = groups.setdefault(key, {
            "instrument": row["instrument"],
            "decision_ts": row["decision_ts"],
            "session": row["session"],
            "direction": row.get("direction"),
            "vehicles": {},
        })
        group["vehicles"][str(row["vehicle"])] = row
    out: list[dict] = []
    for group in groups.values():
        present = group["vehicles"]
        measured = {
            v: r for v, r in present.items()
            if isinstance(r.get("ratio_measured"), (int, float))
        }
        group["complete"] = all(v in present for v in VEHICLES)
        group["measured_vehicles"] = sorted(measured)
        group["missing"] = [
            v for v in VEHICLES
            if v not in present or v not in measured
        ]
        # The best-priced vehicle by measured expected-move-over-cost, and only
        # among vehicles that were actually measured. Reported, not chosen: this
        # is a description of one instant, not a selection rule.
        group["best_measured_vehicle"] = (
            max(measured, key=lambda v: float(measured[v]["ratio_measured"]))
            if measured else None
        )
        out.append(group)
    return sorted(out, key=lambda g: -float(g["decision_ts"]))
