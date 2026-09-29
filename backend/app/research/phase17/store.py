"""Append-only evidence files for Phase 17 — §33.

Four files under ``data_dir``, all JSONL, all append-only, none read by anything
that trades:

``phase17_observations.jsonl``
    one row per captured candidate: both option sides, the strike window, the
    plan and the market context at the instant of the decision.
``phase17_legs.jsonl``
    one row per RESOLVED side of an observation — the costed path.
``phase17_paper.jsonl``
    one row per A+ paper episode, entry at the ask and exit at the bid.
``phase17_coverage.jsonl``
    a periodic heartbeat saying capture was alive.
``phase17_capture_gaps.jsonl``
    one rate-limited row per (instrument, reason) saying why a tick that DID
    run recorded no observation.
``phase17_liveness.jsonl``
    one row a minute saying the tick path ran at all, written before anything
    can refuse it, so a coverage hole is charged to downtime or to the tick
    path on evidence rather than on inference.

The gap file exists because the heartbeat answers only half the question. A
session with 12% coverage and heartbeats through the holes says the process was
up and wrote nothing for that instrument — a different problem from downtime,
with a different fix — but the reason was never written down anywhere, so it
could only be guessed at. It records the refusal and nothing else: no price, no
quote, no decision, so no phase can mistake it for evidence of a market state.

The coverage file is not in the spec and is here for a reason found in the
earlier studies: capture only exists while the app is up, so a dataset silently
describes the days someone remembered to run it. Without a record of which
minutes were observed, "no A+ candidates on Tuesday" and "the process was down on
Tuesday" are the same row, and every per-day rate is quietly biased.

Writes are best-effort and never raise into a tick: a failed append is counted
and published by :func:`health` rather than swallowed, because an empty evidence
file with no explanation is exactly the failure this phase exists to end.

Reads are bounded and incremental. One observation row carries both option
sides and the strike window, so it costs about 12 KB; a wide capture universe
writes hundreds of them a minute and the file reached 784 MB in a single
session in the field. Re-parsing that on every panel refresh starved the whole
process, so a read now (a) parses each line at most once, keeping only a
bounded tail in memory, and (b) can be asked for the last ``tail_bytes`` of a
file instead of all of it. A caller that receives a bounded read is told so —
:func:`read_meta` reports whether rows were dropped — because a KPI computed
over part of the day must not be presented as the day.

The live file is also rolled once it passes :data:`ROLL_AT_BYTES`, so no single
file can grow without limit. Rolled files are never deleted: the audit tools
read the whole series.

A rolled file may be stored gzipped (``*.jsonl.gz``). That is a change to how
the bytes are held and to nothing else: every row is still there, still in
order, still never rewritten, and :func:`series_paths` and :func:`open_series`
make the two forms indistinguishable to a reader. It exists because the field
deployment reached 28 GB of journals at ~1.5 GB a day, and a full disk stops
capture — which costs sessions the same way the missing snapshots did. The live
file is never compressed; only files nothing appends to any more.
"""
from __future__ import annotations

import glob
import gzip
import json
import os
import threading
import time

from app.config import settings
from app.research.phase17 import capture

OBSERVATIONS = "phase17_observations.jsonl"
LEGS = "phase17_legs.jsonl"
PAPER = "phase17_paper.jsonl"
COVERAGE = "phase17_coverage.jsonl"
GAPS = "phase17_capture_gaps.jsonl"
LIVENESS = "phase17_liveness.jsonl"
PROCESS = "phase17_process.jsonl"
STALLS = "phase17_stalls.jsonl"

# Where the capture loop was when it stopped writing liveness lines. The
# process journal says a stall was a stall and not downtime, and then stops:
# "the tick path did not run" names no line of code. These two say whether the
# loop was stuck INSIDE one instrument's tick, or not scheduling ticks at all.
STUCK_IN_TICK = "THE_LOOP_WAS_INSIDE_THIS_INSTRUMENTS_TICK_AND_DID_NOT_RETURN"
STUCK_BETWEEN = "NO_TICK_WAS_IN_FLIGHT_THE_LOOP_ITSELF_STOPPED_SCHEDULING"

# What the loop did about a wedged tick. The stall witness above says a tick
# went in and did not come back; these say the loop stopped waiting for it and
# carried on, so the minutes it cost are bounded and countable. Written where
# the abandonment happens rather than derived later: a tick the loop waited out
# and a tick it gave up on are different events, and a coverage hole cannot be
# told apart from either afterwards.
TICK_ABANDONED = "THE_LOOP_STOPPED_WAITING_FOR_THIS_TICK_AND_WENT_ON_TO_THE_NEXT"
TICK_WORKERS_WEDGED = "EVERY_TICK_WORKER_IS_STILL_HELD_BY_AN_ABANDONED_TICK"

# How long one instrument's tick may hold the loop. The slowest REST call on
# the path is capped at 4s and a tick makes a handful of them, so this is
# several times a legitimately slow tick and well under the shortest gap the
# coverage report attributes. It bounds the damage; it does not repair the
# tick, and the abandonment is recorded rather than smoothed over.
TICK_BUDGET_SEC = 15.0

# How long a tick must be in flight before it counts as stuck. A normal tick is
# tens of milliseconds and the slowest REST call on the path is capped at 4s,
# so 20s cannot be a slow tick; the shortest stall gap the report gives a cause
# to is a minute, and this has to fire well inside one.
STALL_AFTER_SEC = 20

# One stall line per interval while the same tick stays stuck, so a 3-minute
# wedge leaves a handful of lines with a growing elapsed rather than one line
# that could as easily have been 20 seconds.
STALL_INTERVAL_SEC = 30

# Why a tick that ran produced no observation. Constants, so the diagnostic
# reads exactly the strings the capture writes.
NO_OPTION_CHAIN = "NO_OPTION_CHAIN_AT_THIS_TICK"
NO_CONTRACT = "CHAIN_HELD_NO_CONTRACT_TO_MEASURE_AGAINST"
NOT_IN_UNIVERSE = "INSTRUMENT_NOT_IN_THE_CAPTURE_UNIVERSE"
SAMPLED_TIER = "SAMPLED_TIER_INTERVAL_NOT_ELAPSED"
CAPTURE_RAISED = "CAPTURE_RAISED_AND_WAS_SWALLOWED"

_LOCK = threading.Lock()
_STATE: dict[str, object] = {
    "written": 0,
    "failures": 0,
    "last_error": None,
    "last_error_ts": None,
    "last_write_ts": None,
    "last_coverage_ts": 0.0,
    "last_liveness_ts": 0.0,
    "last_process_ts": 0.0,
}

# (instrument, reason) -> when that pair was last journalled. Per pair rather
# than one global timer: two instruments failing for two reasons are two
# findings, and a shared timer would report only whichever came first.
_LAST_GAP: dict[tuple[str, str], float] = {}

# instrument -> when that name last wrote a liveness line.
_LAST_LIVENESS: dict[str, float] = {}

# The tick currently in flight, if any, and when the last one finished. Both
# monotonic for the elapsed and wall-clock for the journal: a stall is measured
# in elapsed seconds, which a clock that can be stepped must not decide.
_IN_FLIGHT: dict[str, object] = {
    "instrument": None,
    "began_mono": None,
    "began_ts": None,
    "ended_mono": None,
    "noted_mono": None,
}

# One heartbeat every 5 minutes. Frequent enough to bound a gap to the length of
# one entry decision; rare enough that a session costs ~75 lines.
COVERAGE_INTERVAL_SEC = 300

# One liveness line per instrument per minute, which is the resolution the
# coverage report measures in.
#
# Per instrument, not per process. A single global timer wrote one line a
# minute carrying whichever name happened to tick first after it opened, and
# with a universe of 46 that made the instrument field nearly meaningless: the
# absence of a CRUDEOIL line said the scan reached SOMETHING, not that it
# missed CRUDEOIL. The coverage report read that absence as "the scan never
# reached it" anyway. Per name the absence is evidence, and the cost is a few
# tens of thousands of short lines a session against 885 GB free.
#
# Half a minute, not a minute, for the same reason the process journal uses
# half: consecutive lines a full 60s apart drift across the minute boundary and
# leave a bucket empty while the tick was running the whole time — a one-minute
# hole the report would have charged to a stall or to the scan. At 30s a minute
# the tick reached this name in always holds a line.
LIVENESS_INTERVAL_SEC = 30

# Marks a liveness row written under the per-instrument limiter. Rows from
# before it exist without the field, and the reader must not split a session on
# them: under the global limiter their silence meant nothing.
LIVENESS_PER_NAME = "PER_INSTRUMENT_TWICE_A_MINUTE_PER_NAME"

# One process line every half minute, so a full minute cannot pass without one
# and a stalled tick cannot be mistaken for a dead process on a rate limit.
PROCESS_INTERVAL_SEC = 30

# One gap line per instrument, per reason, per minute. Minute resolution is what
# the coverage report measures in, and a line per tick would write more rows
# than the observations whose absence it is explaining.
GAP_INTERVAL_SEC = 60

# Roll the live file past this size. 256 MB is ~20k observation rows: large
# enough that a normal session never rolls, small enough that a full re-parse
# after a restart stays in the seconds.
ROLL_AT_BYTES = 256 * 1024 * 1024

# The most rows any one file keeps parsed in memory. At ~12 KB a row this caps
# the cache near 60 MB per file, which is the point of the exercise: the old
# path held the entire day and then re-read it 3 times a minute.
MAX_CACHED_ROWS = 5_000

# Default bound for a caller that does not name one. Two thirds of a rolled
# file; enough that a normal day is complete, and a hard ceiling when it is not.
DEFAULT_TAIL_BYTES = 64 * 1024 * 1024

_CACHE_LOCK = threading.Lock()
# name -> (device, inode, bytes_parsed, rows, dropped_rows)
_CACHE: dict[str, tuple[int, int, int, list[dict], int]] = {}
_META: dict[str, dict[str, object]] = {}


# Suffix of a compressed rolled file. Only ever applied to a file that has been
# rolled, i.e. that nothing will append to again.
GZ = ".gz"


def path(name: str) -> str:
    return os.path.join(settings.data_dir, name)


def open_series(p: str):
    """Open one series file for binary line reading, compressed or not.

    Every reader goes through this, so compressing a rolled file cannot make a
    reader skip it — the failure that would turn a storage saving into silent
    evidence loss.
    """
    if p.endswith(GZ):
        return gzip.open(p, "rb")
    return open(p, "rb")


def _roll_if_large(name: str) -> None:
    """Rename the live file once it is too large to re-read cheaply.

    Best effort and never fatal: failing to roll costs speed, losing the row
    would cost evidence.
    """
    p = path(name)
    try:
        if os.path.getsize(p) < ROLL_AT_BYTES:
            return
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        base, ext = os.path.splitext(name)
        target = path(f"{base}.{stamp}{ext}")
        n = 1
        while os.path.exists(target):  # two rolls in one second must not collide
            target = path(f"{base}.{stamp}-{n}{ext}")
            n += 1
        os.rename(p, target)
    except OSError:
        return
    with _CACHE_LOCK:
        _CACHE.pop(name, None)


def _append(name: str, rec: dict) -> bool:
    try:
        os.makedirs(settings.data_dir, exist_ok=True)
        _roll_if_large(name)
        with open(path(name), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except OSError as exc:
        with _LOCK:
            _STATE["failures"] = int(_STATE["failures"]) + 1
            _STATE["last_error"] = f"{type(exc).__name__}: {exc}"
            _STATE["last_error_ts"] = time.time()
        return False
    with _LOCK:
        _STATE["written"] = int(_STATE["written"]) + 1
        _STATE["last_write_ts"] = time.time()
    return True


def write_observation(row: dict) -> bool:
    return _append(OBSERVATIONS, row)


def write_leg(row: dict) -> bool:
    return _append(LEGS, row)


def write_paper(row: dict) -> bool:
    return _append(PAPER, row)


def note_coverage(instrument: str, *, now: float | None = None,
                  interval_sec: int = COVERAGE_INTERVAL_SEC) -> bool:
    """Record that capture was alive. Rate-limited to one line per interval."""
    now = time.time() if now is None else now
    with _LOCK:
        last = float(_STATE["last_coverage_ts"] or 0.0)
        if now - last < interval_sec:
            return False
        _STATE["last_coverage_ts"] = now
    return _append(COVERAGE, {
        "ts": now,
        "session": capture.ist_parts(now)[0],
        "instrument": instrument,
        "provider": settings.data_provider,
    })


def note_liveness(instrument: str, *, now: float | None = None,
                  interval_sec: int = LIVENESS_INTERVAL_SEC) -> bool:
    """Record that the tick path RAN, whatever it went on to record.

    Separate from :func:`note_coverage`, which is written only after an
    observation has been persisted and so cannot tell "the process was down"
    from "the process was up and captured nothing at all" — and which is
    rate-limited to five minutes, far coarser than the minute the coverage
    report measures in. Charging a coverage hole to uptime work or to the tick
    path is the whole question, and a heartbeat that only fires on success
    cannot answer it.

    Rate-limited per instrument, so a minute in which this name was not ticked
    holds no line for it. Under the earlier global limiter one line a minute
    carried whichever name won the race, and the coverage report then read the
    absence of a name as the scan having skipped it — a claim that limiter could
    not support at all.

    Carries no price, no quote and no decision, exactly like :func:`note_gap`,
    so no phase can read it as a market state or count it as evidence.
    """
    now = time.time() if now is None else now
    name = (instrument or "").upper()
    with _LOCK:
        last = float(_LAST_LIVENESS.get(name) or 0.0)
        if now - last < interval_sec:
            return False
        _LAST_LIVENESS[name] = now
        _STATE["last_liveness_ts"] = now
    return _append(LIVENESS, {
        "ts": now,
        "session": capture.ist_parts(now)[0],
        "instrument": name,
        "tick_ran": True,
        "scope": LIVENESS_PER_NAME,
    })


def note_process_alive(*, now: float | None = None,
                       interval_sec: int = PROCESS_INTERVAL_SEC) -> bool:
    """Record that the PROCESS is up, whether or not the tick path is running.

    The distinction this exists for: :func:`note_liveness` is written by the tick
    path, so its absence means "the tick did not run" and nothing more. A process
    that is up with a wedged capture loop leaves exactly the same silence as one
    that is dead — and those need opposite fixes, restart supervision recovering
    only the second. With only the liveness journal a report called 77 minutes
    "proven down" that may well have been a stall, which is the same class of
    overclaim the success-only heartbeat label already made once.

    Written from a thread of its own for that reason: sharing the capture's
    thread would make it silent in exactly the case it exists to name.

    Carries no instrument, no price and no decision — it is not evidence of a
    market, only of a running interpreter.
    """
    now = time.time() if now is None else now
    with _LOCK:
        last = float(_STATE.get("last_process_ts") or 0.0)
        if now - last < interval_sec:
            return False
        _STATE["last_process_ts"] = now
    return _append(PROCESS, {
        "ts": now,
        "session": capture.ist_parts(now)[0],
        "process_alive": True,
    })


def note_tick_begin(instrument: str, *, mono: float | None = None,
                    now: float | None = None) -> None:
    """Mark one instrument's tick as in flight. In memory only, no I/O.

    A breadcrumb for :func:`note_stall`, which runs on the heartbeat thread and
    so is still running in exactly the case this exists for. Writing a line
    here instead would cost a file append per tick on the live path, which is
    the one path that must stay cheap.
    """
    with _LOCK:
        _IN_FLIGHT["instrument"] = (instrument or "").upper()
        _IN_FLIGHT["began_mono"] = time.monotonic() if mono is None else mono
        _IN_FLIGHT["began_ts"] = time.time() if now is None else now
        _IN_FLIGHT["noted_mono"] = None


def note_tick_end(*, mono: float | None = None) -> None:
    """Mark the in-flight tick as returned, however it returned."""
    with _LOCK:
        _IN_FLIGHT["instrument"] = None
        _IN_FLIGHT["began_mono"] = None
        _IN_FLIGHT["began_ts"] = None
        _IN_FLIGHT["ended_mono"] = time.monotonic() if mono is None else mono
        _IN_FLIGHT["noted_mono"] = None


def note_stall(*, now: float | None = None, mono: float | None = None,
               after_sec: float = STALL_AFTER_SEC,
               interval_sec: float = STALL_INTERVAL_SEC) -> bool:
    """Record WHERE the capture loop was, if it has stopped getting on with it.

    Called from the process-heartbeat thread, which keeps running when the tick
    path has wedged — the whole point, exactly as for :func:`note_process_alive`.
    That journal can already say a hole was a stall and not downtime, and then
    it stops: 13 minutes of "the tick path did not run" on a real session named
    no instrument and no line of code, so there was nothing to fix from it.

    Two findings, and they are not the same bug: a tick that went into one
    instrument and never came back (a provider call, a lock, a blocking read),
    against a loop that is not scheduling ticks at all.

    Carries no price, no quote and no decision. Never raises.
    """
    now = time.time() if now is None else now
    mono = time.monotonic() if mono is None else mono
    with _LOCK:
        began = _IN_FLIGHT["began_mono"]
        ended = _IN_FLIGHT["ended_mono"]
        noted = _IN_FLIGHT["noted_mono"]
        if began is not None:
            since, where = mono - float(began), STUCK_IN_TICK
            name = _IN_FLIGHT["instrument"]
            began_ts = _IN_FLIGHT["began_ts"]
        elif ended is not None:
            since, where, name = mono - float(ended), STUCK_BETWEEN, None
            began_ts = None
        else:
            # No tick has ever run in this process. Not a stall: there is
            # nothing to say the loop was ever getting on with it.
            return False
        if since < after_sec:
            return False
        if noted is not None and mono - float(noted) < interval_sec:
            return False
        _IN_FLIGHT["noted_mono"] = mono
    return _append(STALLS, {
        "ts": now,
        "session": capture.ist_parts(now)[0],
        "instrument": name,
        "where": where,
        "stuck_sec": round(since, 1),
        "since_ts": began_ts,
    })


def note_abandoned_tick(instrument: str, *, stuck_sec: float,
                        where: str = TICK_ABANDONED,
                        now: float | None = None) -> bool:
    """Record that the loop gave up waiting for one instrument's tick.

    Written to the stall journal beside :func:`note_stall`, because it answers
    the same question — where the loop was — and the two only mean anything
    read together: a stall line says a tick wedged, and this says what the cost
    of it was allowed to be.

    A wedged tick used to hold the whole serial loop, so one instrument's
    blocked provider call cost every other instrument those minutes. That is
    the 108-second line in the journal and most of the missing coverage with
    it. Abandoning the wait bounds that, and it fixes nothing: the thread is
    still wedged, the instrument still captured nothing for that minute, and
    the line is here so the count of abandonments is visible rather than
    absorbed into an unattributed hole.

    Not rate-limited: each line is a distinct decision by the loop, and the
    number of them is the measurement. Never raises.
    """
    now = time.time() if now is None else now
    return _append(STALLS, {
        "ts": now,
        "session": capture.ist_parts(now)[0],
        "instrument": (instrument or "").upper() or None,
        "where": where,
        "stuck_sec": round(float(stuck_sec), 1),
        "since_ts": None,
    })


def note_gap(instrument: str, reason: str, *, now: float | None = None,
             interval_sec: int = GAP_INTERVAL_SEC) -> bool:
    """Record that a tick which ran captured nothing, and why.

    Deliberately not an observation. It carries no price, no quote and no
    decision, so no phase can read it as a market state or count it as evidence
    — it exists so a coverage hole has its cause attached at the moment it
    happens rather than being reconstructed from a guess weeks later.

    Rate-limited per (instrument, reason) pair, and never raises: the caller is
    a live tick, and a diagnostic that can break capture is worse than no
    diagnostic.
    """
    now = time.time() if now is None else now
    key = ((instrument or "").upper(), reason)
    with _LOCK:
        last = float(_LAST_GAP.get(key, 0.0))
        if now - last < interval_sec:
            return False
        _LAST_GAP[key] = now
    return _append(GAPS, {
        "ts": now,
        "session": capture.ist_parts(now)[0],
        "instrument": key[0],
        "reason": reason,
        "provider": settings.data_provider,
    })


def _parse(chunk: bytes) -> tuple[list[dict], int]:
    rows: list[dict] = []
    bad = 0
    for raw in chunk.splitlines():
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows, bad


def series_paths(name: str) -> list[str]:
    """Rolled files then the live one, oldest first.

    A rolled or manually parked file is still evidence. Reading only the live
    file after a roll would silently shorten the record, which is the same
    failure as not capturing at all.
    """
    base, ext = os.path.splitext(name)
    # The directory the live file is in, not the configured one directly: the
    # two are the same in the deployment, and taking it from :func:`path` keeps
    # the rolled set and the live file pointing at one place for any caller that
    # has redirected the journals.
    here = os.path.dirname(path(name)) or settings.data_dir
    found = [
        *glob.glob(os.path.join(here, f"{base}.*{ext}")),
        *glob.glob(os.path.join(here, f"{base}.*{ext}{GZ}")),
    ]
    rolled = sorted(
        # Ordered by the roll stamp in the name, so a compressed file keeps the
        # position its uncompressed twin had.
        (q for q in found if os.path.basename(q) != name),
        key=lambda q: os.path.basename(q).removesuffix(GZ),
    )
    return [*rolled, path(name)]


def _read_all(name: str, *, limit: int | None) -> list[dict]:
    """Every row of the whole series, in order. Used by the offline audits."""
    rows: list[dict] = []
    bad = 0
    for q in series_paths(name):
        try:
            with open_series(q) as fh:
                part, part_bad = _parse(fh.read())
        except OSError:
            continue
        rows.extend(part)
        bad += part_bad
    if bad:
        with _LOCK:
            _STATE["last_error"] = f"{bad} unparseable line(s) in {name}"
            _STATE["last_error_ts"] = time.time()
    if limit is not None and limit >= 0:
        return rows[-limit:]
    return rows


def read(
    name: str,
    *,
    limit: int | None = None,
    tail_bytes: int | None = DEFAULT_TAIL_BYTES,
) -> list[dict]:
    """Read a JSONL evidence file, parsing each line at most once.

    The files are append-only, so a second call re-reads only the bytes added
    since the first. Only the last :data:`MAX_CACHED_ROWS` rows are kept; ask
    :func:`read_meta` whether older rows were dropped rather than assuming the
    result is the whole file.

    ``tail_bytes=None`` forces the whole file — for the offline audit tools,
    which have the time. A corrupt line is skipped rather than fatal, since a
    half-written last line after a hard kill must not cost the session's
    evidence, and the count is published by :func:`health`.
    """
    p = path(name)
    if tail_bytes is None:
        # The offline tools ask for the whole series and must get every row —
        # a truncated audit is worse than a slow one. Deliberately uncached:
        # this path is minutes of CLI work, not a panel refresh.
        return _read_all(name, limit=limit)
    try:
        st = os.stat(p)
    except OSError:
        with _CACHE_LOCK:
            _CACHE.pop(name, None)
            _META[name] = {"rows": 0, "dropped": 0, "bytes": 0, "complete": True}
        return []

    with _CACHE_LOCK:
        cached = _CACHE.get(name)

    rows: list[dict] = []
    dropped = 0
    start = 0
    if (
        cached is not None
        and cached[0] == st.st_dev
        and cached[1] == st.st_ino
        and cached[2] <= st.st_size
    ):
        # Same file, only grown: resume where the last read stopped.
        _, _, start, rows, dropped = cached
        rows = list(rows)
    elif tail_bytes is not None and st.st_size > tail_bytes:
        # Too large to hold: read the tail and say so.
        start = st.st_size - tail_bytes

    bad = 0
    try:
        with open(p, "rb") as fh:
            if start:
                fh.seek(start)
                if cached is None:
                    fh.readline()  # discard the partial line the seek landed in
                    dropped = -1  # older rows exist; how many is unknown
            chunk = fh.read()
            end = fh.tell()
    except OSError as exc:
        with _LOCK:
            _STATE["last_error"] = f"{type(exc).__name__}: {exc}"
            _STATE["last_error_ts"] = time.time()
        return rows[-limit:] if limit is not None and limit >= 0 else rows

    fresh, bad = _parse(chunk)
    rows.extend(fresh)
    if len(rows) > MAX_CACHED_ROWS:
        cut = len(rows) - MAX_CACHED_ROWS
        rows = rows[cut:]
        dropped = -1 if dropped < 0 else dropped + cut

    with _CACHE_LOCK:
        _CACHE[name] = (st.st_dev, st.st_ino, end, rows, dropped)
        _META[name] = {
            "rows": len(rows),
            "dropped": dropped,
            "bytes": st.st_size,
            "complete": dropped == 0,
        }

    if bad:
        with _LOCK:
            _STATE["last_error"] = f"{bad} unparseable line(s) in {name}"
            _STATE["last_error_ts"] = time.time()
    if limit is not None and limit >= 0:
        return rows[-limit:]
    return rows


def read_meta(name: str) -> dict:
    """What the last :func:`read` of ``name`` actually covered.

    ``complete`` false means rows were left out and any rate computed from the
    result describes the tail, not the day.
    """
    with _CACHE_LOCK:
        meta = dict(_META.get(name) or {})
    meta.setdefault("rows", 0)
    meta.setdefault("dropped", 0)
    meta.setdefault("bytes", 0)
    meta.setdefault("complete", True)
    return meta


def drop_cache() -> None:
    """Test seam, and the recovery path if a file is replaced under us."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _META.clear()


def observations(*, limit: int | None = None,
                 tail_bytes: int | None = DEFAULT_TAIL_BYTES) -> list[dict]:
    return read(OBSERVATIONS, limit=limit, tail_bytes=tail_bytes)


def legs(*, limit: int | None = None,
         tail_bytes: int | None = DEFAULT_TAIL_BYTES) -> list[dict]:
    return read(LEGS, limit=limit, tail_bytes=tail_bytes)


def paper(*, limit: int | None = None,
          tail_bytes: int | None = DEFAULT_TAIL_BYTES) -> list[dict]:
    return read(PAPER, limit=limit, tail_bytes=tail_bytes)


def coverage(*, limit: int | None = None,
             tail_bytes: int | None = DEFAULT_TAIL_BYTES) -> list[dict]:
    return read(COVERAGE, limit=limit, tail_bytes=tail_bytes)


def health() -> dict:
    with _LOCK:
        state = dict(_STATE)
    state["files"] = {
        name: {
            "path": path(name),
            "exists": os.path.exists(path(name)),
            "bytes": os.path.getsize(path(name)) if os.path.exists(path(name)) else 0,
        }
        for name in (OBSERVATIONS, LEGS, PAPER, COVERAGE, GAPS, LIVENESS,
                     PROCESS, STALLS)
    }
    state["read_coverage"] = {
        name: read_meta(name) for name in (OBSERVATIONS, LEGS, PAPER, COVERAGE)
    }
    return state


def reset_health() -> None:
    """Test seam. Clears the counters, never the files."""
    drop_cache()
    with _LOCK:
        _STATE.update({
            "written": 0,
            "failures": 0,
            "last_error": None,
            "last_error_ts": None,
            "last_write_ts": None,
            "last_coverage_ts": 0.0,
            "last_liveness_ts": 0.0,
            "last_process_ts": 0.0,
        })
        _LAST_GAP.clear()
        _LAST_LIVENESS.clear()
        _IN_FLIGHT.update({
            "instrument": None,
            "began_mono": None,
            "began_ts": None,
            "ended_mono": None,
            "noted_mono": None,
        })
