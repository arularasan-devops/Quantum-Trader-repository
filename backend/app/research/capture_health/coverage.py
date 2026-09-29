"""Measure captured / alive / ingested minutes per session, and name the gaps.

The whole module is a read: it opens the journals the capture already wrote and
the derived store the research phases read, both for reading only, and returns
plain dictionaries. There is deliberately no repair path here — a coverage
measurement that also "fixes" things cannot be trusted to report honestly.

Two implementation choices worth stating, because both were wrong in an earlier
attempt at this number:

* the observation journal is streamed line by line with a substring pre-check
  and two small regular expressions, never ``json.loads`` on every line. A
  rolled file is 256 MB and a session spans several of them; parsing every row
  in full turned a diagnostic into a ten-minute job, which is how it stopped
  being run;
* the session a row belongs to is derived from its own ``signal_ts`` in IST
  rather than read from its ``session`` field. The two agree, but only the
  timestamp is positionally reliable in a line this size, and a row that lost
  its session label is still evidence of a captured minute.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import time

from app.config import settings
from app.research.capture_health import (
    AFTER_LAST,
    ALIVE_NOT_CAPTURED,
    BEFORE_FIRST,
    FROM_HEARTBEAT,
    FROM_LIVENESS,
    FROM_NOTHING,
    HEARTBEAT_REACH_MIN,
    INSIDE,
    LIVENESS_REACH_MIN,
    MIN_REPORTED_GAP_MIN,
    NO_HEARTBEAT_FILE,
    NO_SESSION_EVIDENCE,
    DOWN_OR_STALLED,
    NOT_EVIDENCED,
    PROCESS_DOWN,
    PROCESS_REACH_MIN,
    SCAN_MISSED_IT,
    TICK_RAN_NO_ROW,
    TICK_STALLED,
    WEEKEND,
    WITH_PROCESS,
    WITHOUT_PROCESS,
)
from app.research.phase17 import store as p17store

IST_OFFSET_SEC = 5 * 3600 + 1800
MINUTE = 60

# ``"signal_ts": 1788803536.0`` — the first numeric field of every observation
# row, and the only one this module needs.
TS_RE = re.compile(rb'"signal_ts":\s*([0-9.]+)')
# The heartbeat rows are small, so they are parsed with the same cheap pair.
HB_TS_RE = re.compile(rb'"ts":\s*([0-9.]+)')
INST_RE = re.compile(rb'"instrument":\s*"([A-Z0-9_&\-]+)"')
REASON_RE = re.compile(rb'"reason":\s*"([A-Z0-9_]+)"')


def ist(ts: float) -> dt.datetime:
    """The IST wall clock for an epoch timestamp, as a naive datetime."""
    return dt.datetime.utcfromtimestamp(float(ts) + IST_OFFSET_SEC)


def clock(minute: int) -> str:
    """``HH:MM`` IST for an epoch minute."""
    return ist(minute * MINUTE).strftime("%H:%M")


def session_of(ts: float) -> str:
    """The IST calendar date a timestamp belongs to."""
    return ist(ts).strftime("%Y-%m-%d")


def exchange_of(instrument: str) -> str:
    """The exchange an instrument trades on, or an empty string when unknown."""
    from app.market.instruments import get_spec

    try:
        return str(get_spec(instrument).exchange or "")
    except Exception:
        return ""


def is_weekend(session: str) -> bool:
    """Whether an IST calendar date is a Saturday or a Sunday.

    The only part of the exchange calendar that can be known without a holiday
    list, and the part that was being graded as a trading session: a shut
    Saturday scored 0.0% coverage with the whole session charged to the capture.
    """
    try:
        day = dt.datetime.strptime(session, "%Y-%m-%d")
    except ValueError:
        return False
    return day.weekday() >= 5


def session_window(instrument: str, session: str) -> tuple[int, int] | None:
    """The instrument's own session as an inclusive pair of epoch minutes.

    Per exchange, because MCX runs to 23:30 IST while NSE and BSE stop at 15:30:
    measuring CRUDEOIL against an equity session would report 100% coverage of a
    day that was half missed, and measuring RELIANCE against MCX's would report
    40% of a day that was fully captured.
    """
    try:
        day = dt.datetime.strptime(session, "%Y-%m-%d")
    except ValueError:
        return None
    if day.weekday() >= 5:
        # No session at all, so no window to measure against. Reported as a
        # non-day rather than a failed one.
        return None
    open_ist, close_ist = settings.session_window_ist(exchange_of(instrument))
    try:
        oh, om = (int(x) for x in open_ist.split(":"))
        ch, cm = (int(x) for x in close_ist.split(":"))
    except ValueError:
        return None
    base = int(dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
               .timestamp()) - IST_OFFSET_SEC
    first = (base + (oh * 60 + om) * MINUTE) // MINUTE
    last = (base + (ch * 60 + cm) * MINUTE) // MINUTE
    return int(first), int(last)


def observation_files() -> list[str]:
    """Rolled Phase 17 observation files then the live one, oldest first."""
    return [p for p in p17store.series_paths(p17store.OBSERVATIONS)
            if os.path.exists(p)]


def heartbeat_files() -> list[str]:
    """Rolled Phase 17 heartbeat files then the live one, oldest first."""
    return [p for p in p17store.series_paths(p17store.COVERAGE)
            if os.path.exists(p)]


def liveness_files() -> list[str]:
    """Rolled Phase 17 liveness files then the live one, oldest first.

    Empty for every session captured before the liveness journal existed, which
    is why the caller falls back to the heartbeat rather than reading an absent
    file as "the process was never up".
    """
    return [p for p in p17store.series_paths(p17store.LIVENESS)
            if os.path.exists(p)]


def process_files() -> list[str]:
    """Rolled Phase 17 process-liveness files then the live one, oldest first.

    Empty for every session before the process journal existed. That absence is
    reported rather than read as "the process was down": the two journals ship
    on different days and a missing one decides nothing.
    """
    return [p for p in p17store.series_paths(p17store.PROCESS)
            if os.path.exists(p)]


def reached_minutes(
    instrument: str, *, paths: list[str] | None = None,
) -> dict[str, set[int]] | None:
    """Epoch minutes the tick ran FOR THIS INSTRUMENT in, per session.

    The same journal :func:`alive_minutes` is read from, filtered to the name.
    Pooled, a liveness line proves the capture process was ticking *something*;
    named, it proves the tick reached this instrument and whatever happened next
    happened inside the capture path. Those are opposite faults with opposite
    fixes, and without this filter both read as one cause — which is how 326
    alive-but-empty minutes got reported as "the scan never reached it" on an
    inference the journal was never asked for.

    ONLY rows written under the per-instrument rate limit, which say so in a
    ``scope`` field. The limiter was global first: one line a minute carrying
    whichever of 46 names ticked as it opened, so the absence of a name meant
    only that some other name won the race. Splitting a session on those rows
    would repeat the original error one layer down, with the file's own field
    as the alibi.

    ``None`` where no such row exists for a session, never an empty set: an
    absent journal must not read as "the tick never ran for this name".
    """
    files = liveness_files() if paths is None else paths
    if not files:
        return None
    want = instrument.upper().encode()
    scope = p17store.LIVENESS_PER_NAME.encode()
    out: dict[str, set[int]] = {}
    for path in files:
        try:
            handle = p17store.open_series(path)
        except OSError:
            continue
        with handle as fh:
            for line in fh:
                if scope not in line:
                    continue
                found = HB_TS_RE.search(line)
                named = INST_RE.search(line)
                if found is None or named is None:
                    continue
                ts = float(found.group(1))
                session = session_of(ts)
                # The session is keyed even for another instrument's row: it is
                # what proves the per-name journal covers the day, which is a
                # different fact from this name having been reached in it.
                minutes = out.setdefault(session, set())
                if named.group(1) == want:
                    minutes.add(int(ts // MINUTE))
    return out or None


def stall_files() -> list[str]:
    """Rolled Phase 17 stall-witness files then the live one, oldest first.

    Empty for every session before the witness shipped. That absence says the
    loop was never watched, not that it never stalled.
    """
    return [p for p in p17store.series_paths(p17store.STALLS)
            if os.path.exists(p)]


def stalls(*, paths: list[str] | None = None) -> dict[str, dict[int, dict]]:
    """Epoch minute -> where the capture loop was, per session.

    Not filtered to an instrument, and deliberately: a loop wedged inside
    SILVER's tick is why CRUDEOIL has a hole that minute. Filtering to the
    instrument the report is about would hide the only name worth knowing.

    The witness is written from the heartbeat thread, so these rows exist in
    exactly the minutes the tick path wrote none of its own — which is what a
    stall is, and why the tick path cannot be asked to describe it.
    """
    out: dict[str, dict[int, dict]] = {}
    for path in (stall_files() if paths is None else paths):
        try:
            handle = p17store.open_series(path)
        except OSError:
            continue
        with handle as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ts = row.get("ts")
                where = row.get("where")
                if not isinstance(ts, (int, float)) or not isinstance(where, str):
                    continue
                name = row.get("instrument")
                stuck = row.get("stuck_sec")
                bucket = out.setdefault(session_of(float(ts)), {})
                minute = int(float(ts) // MINUTE)
                seen = bucket.get(minute)
                # The longest witness in the minute: consecutive lines describe
                # one wedge getting older, and the last one is how bad it got.
                if seen is not None and float(seen["stuck_sec"]) >= float(stuck or 0.0):
                    continue
                bucket[minute] = {
                    "where": where,
                    "instrument": name if isinstance(name, str) else None,
                    "stuck_sec": float(stuck or 0.0),
                }
    return out


def gap_reason_files() -> list[str]:
    """Rolled Phase 17 capture-gap files then the live one, oldest first."""
    return [p for p in p17store.series_paths(p17store.GAPS)
            if os.path.exists(p)]


def gap_reasons(
    instrument: str, *, paths: list[str] | None = None,
) -> dict[str, dict[int, str]] | None:
    """Epoch minute -> the reason capture recorded nothing, per session.

    ``None`` when the capture that wrote the journals predates the reason file:
    an absent journal must not be read as "there was no reason", which is the
    kind of silent zero this whole package exists to stop.
    """
    files = gap_reason_files() if paths is None else paths
    if not files:
        return None
    want = instrument.upper().encode()
    out: dict[str, dict[int, str]] = {}
    for path in files:
        try:
            handle = p17store.open_series(path)
        except OSError:
            continue
        with handle as fh:
            for line in fh:
                if want not in line:
                    continue
                found = HB_TS_RE.search(line)
                named = INST_RE.search(line)
                why = REASON_RE.search(line)
                if found is None or why is None:
                    continue
                if named is None or named.group(1) != want:
                    continue
                ts = float(found.group(1))
                out.setdefault(session_of(ts), {})[int(ts // MINUTE)] = (
                    why.group(1).decode()
                )
    return out


def captured_minutes(
    instrument: str, *, paths: list[str] | None = None,
) -> dict[str, dict[int, int]]:
    """Epoch minute -> observation count, per session, for one instrument.

    The count is kept rather than a bare set of minutes because it separates two
    situations a set cannot: a minute with 45 rows (the instrument was being
    ticked continuously) and a minute with one (it was reached once by the
    round-robin and then not again).
    """
    want = f'"instrument": "{instrument.upper()}"'.encode()
    out: dict[str, dict[int, int]] = {}
    for path in (observation_files() if paths is None else paths):
        try:
            handle = p17store.open_series(path)
        except OSError:
            continue
        with handle as fh:
            for line in fh:
                if want not in line:
                    continue
                found = TS_RE.search(line)
                if found is None:
                    continue
                # The name appears in nested legs too; the row's own instrument
                # is the first occurrence after the timestamps.
                named = INST_RE.search(line)
                if named is None or named.group(1).decode() != instrument.upper():
                    continue
                ts = float(found.group(1))
                minute = int(ts // MINUTE)
                bucket = out.setdefault(session_of(ts), {})
                bucket[minute] = bucket.get(minute, 0) + 1
    return out


def alive_minutes(*, paths: list[str] | None = None) -> dict[str, set[int]]:
    """Epoch minutes a heartbeat was written in, per session, any instrument.

    Any instrument on purpose. The heartbeat says the capture process was alive
    and its writes were landing; which name it happened to be capturing at that
    moment does not change that, and it is the only evidence available for
    telling "the app was down" from "the app was up and skipped this name".
    """
    out: dict[str, set[int]] = {}
    for path in (heartbeat_files() if paths is None else paths):
        try:
            handle = p17store.open_series(path)
        except OSError:
            continue
        with handle as fh:
            for line in fh:
                found = HB_TS_RE.search(line)
                if found is None:
                    continue
                ts = float(found.group(1))
                out.setdefault(session_of(ts), set()).add(int(ts // MINUTE))
    return out


def ingested_minutes(db_path: str, instrument: str) -> dict[str, set[int]] | None:
    """Epoch minutes the derived Phase 35 store holds, per session.

    ``None`` when there is no store to read, which is different from an empty
    one: a missing database means the question was not asked, an empty one means
    the ingest has run and found nothing.
    """
    if not os.path.exists(db_path):
        return None
    uri = f"file:{db_path}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return None
    out: dict[str, set[int]] = {}
    try:
        rows = con.execute(
            "SELECT session, CAST(ts / 60 AS INTEGER) AS minute"
            " FROM raw_observation WHERE instrument = ? GROUP BY session, minute",
            (instrument.upper(),),
        )
        for session, minute in rows:
            out.setdefault(str(session), set()).add(int(minute))
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return out


def _runs(missing: list[int]) -> list[tuple[int, int]]:
    """Consecutive epoch minutes collapsed into inclusive (first, last) runs."""
    runs: list[tuple[int, int]] = []
    for minute in missing:
        if runs and minute == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], minute)
        else:
            runs.append((minute, minute))
    return runs


def alive_span(
    alive: set[int], *, reach: int = HEARTBEAT_REACH_MIN,
) -> set[int]:
    """The minutes the heartbeat journal PROVES the capture was running for.

    A heartbeat proves the process was up in its own minute. Everything else is
    inference, and only one inference is safe: a minute lying BETWEEN two
    heartbeats close enough together — no more than two of the heartbeat's own
    rate-limited intervals apart — had a running capture on both sides of it,
    which a process that died and came back cannot produce.

    Nothing is claimed forward from the last heartbeat or backward from the
    first. That is the conservative direction on purpose: the heartbeat is
    rate-limited, so silence just after one is uninformative, and reading it as
    "still alive" is what let a single heartbeat certify a five-hour hole as
    time the capture was up.
    """
    beats = sorted(alive)
    out: set[int] = set(beats)
    span = 2 * reach
    for start, end in zip(beats, beats[1:]):
        if end - start <= span:
            out.update(range(start, end + 1))
    return out


def gaps(
    covered: set[int], window: tuple[int, int], alive: set[int] | None,
    reasons: dict[int, str] | None = None,
    *, reach: int = HEARTBEAT_REACH_MIN,
    down_cause: str = NOT_EVIDENCED,
    process: set[int] | None = None,
    reached: set[int] | None = None,
    witness: dict[int, dict] | None = None,
) -> list[dict]:
    """Every stretch of the session with no captured minute in it, with a cause.

    The cause is the point of the function. A minute the journal vouches for was
    not downtime, so no amount of restart supervision or reconnect backoff will
    recover it — the capture was running and did not write this instrument.

    A minute no journal reaches is only *provably* downtime when the journal is
    the liveness one, which is written before anything can refuse a row. Hence
    ``down_cause``: with the success-only heartbeat it stays
    :data:`~app.research.capture_health.NOT_EVIDENCED`, because an up process
    capturing nothing at all writes exactly as few beats as a dead one, and
    only the caller knows which journal it read.

    A missing run is therefore SPLIT where that verdict changes, and each part
    reported on its own. Reporting the run whole meant a five-hour hole with a
    single heartbeat in the middle of it was charged, every minute of it, to the
    tick path; the same session's heartbeat count said that was impossible.

    ``process`` is the minutes the process journal PROVES an interpreter was up
    in, written from a thread that is not the tick's. Where it is available a
    minute with no liveness line splits again: the process was up and the loop
    was wedged (a stall, which restarting fixes and supervision does not), or the
    process itself was gone (which supervision does fix). Without it the same
    minute is :data:`~app.research.capture_health.DOWN_OR_STALLED` — undecided,
    not downtime.
    """
    first, last = window
    inside = sorted(m for m in covered if first <= m <= last)
    missing = [m for m in range(first, last + 1) if m not in covered]
    proven = None if alive is None else alive_span(alive, reach=reach)
    up = (None if process is None
          else alive_span(process, reach=PROCESS_REACH_MIN))
    # NOT bracketed, unlike the two above. Bracketing is sound for "the process
    # was up": a process running either side of a gap cannot have died and come
    # back. A scan can perfectly well drop a name for four minutes and return
    # to it, so filling the minutes between two of its lines would manufacture
    # exactly the reach the split exists to measure. Per name the limiter emits
    # a line in every minute the tick reached this instrument, so the raw
    # minutes are the evidence and no interpolation is needed.
    got = reached
    out: list[dict] = []
    for start, end in _runs(missing):
        if end - start + 1 < MIN_REPORTED_GAP_MIN:
            continue
        for run_start, run_end, cause in _by_cause(
                start, end, proven, down_cause, up, got):
            length = run_end - run_start + 1
            beats = (
                0 if alive is None
                else sum(1 for m in range(run_start, run_end + 1) if m in alive)
            )
            if not inside or run_end < inside[0]:
                where = BEFORE_FIRST
            elif run_start > inside[-1]:
                where = AFTER_LAST
            else:
                where = INSIDE
            tally: dict[str, int] = {}
            if reasons:
                for minute in range(run_start, run_end + 1):
                    why = reasons.get(minute)
                    if why is not None:
                        tally[why] = tally.get(why, 0) + 1
            saw = _witnessed(witness, run_start, run_end)
            out.append({
                "from": clock(run_start),
                "to": clock(run_end),
                "minutes": length,
                "where": where,
                "cause": cause,
                "heartbeats_inside": beats,
                "reasons": tally,
                # Where the loop was, from the thread that was still running.
                # Empty is not "it was fine": the witness only ships from the
                # session it was installed in, and a stall shorter than its
                # threshold leaves nothing behind.
                "witness": saw,
                # A gap the capture was alive through, with no recorded reason in
                # it, is the third case and the one no journal states outright:
                # the tick never reached this instrument at all, so nothing had a
                # refusal to write down. That is a scheduler finding, not a feed
                # one, and it is only readable once the reason journal exists.
                "unexplained_minutes": (
                    length - sum(tally.values())
                    if cause in _ALIVE_CAUSES and reasons is not None else None
                ),
            })
    return out


def _witnessed(
    witness: dict[int, dict] | None, start: int, end: int,
) -> list[dict]:
    """What the stall witness saw across one gap, by place and instrument.

    Counted in minutes rather than lines, because the witness writes on its own
    timer and a minute holding two lines is not two stalls.
    """
    if not witness:
        return []
    seen: dict[tuple[str, str | None], dict] = {}
    for minute in range(start, end + 1):
        row = witness.get(minute)
        if row is None:
            continue
        key = (str(row["where"]), row.get("instrument"))
        entry = seen.get(key)
        if entry is None:
            seen[key] = {
                "where": key[0],
                "instrument": key[1],
                "minutes": 1,
                "worst_sec": float(row.get("stuck_sec") or 0.0),
            }
            continue
        entry["minutes"] += 1
        entry["worst_sec"] = max(
            entry["worst_sec"], float(row.get("stuck_sec") or 0.0))
    return sorted(seen.values(),
                  key=lambda e: (-e["minutes"], -e["worst_sec"]))


def _by_cause(
    start: int, end: int, proven: set[int] | None,
    down_cause: str = NOT_EVIDENCED,
    process: set[int] | None = None,
    reached: set[int] | None = None,
) -> list[tuple[int, int, str]]:
    """A missing run split into inclusive sub-runs of one cause each.

    The parts tile the run exactly — no minute is dropped and none is counted
    twice — so the per-cause totals still add up to the missing minutes.
    """
    if proven is None:
        return [(start, end, NO_HEARTBEAT_FILE)]
    parts: list[tuple[int, int, str]] = []
    for minute in range(start, end + 1):
        cause = _cause_of(minute, proven, down_cause, process, reached)
        if parts and parts[-1][2] == cause and parts[-1][1] == minute - 1:
            parts[-1] = (parts[-1][0], minute, cause)
        else:
            parts.append((minute, minute, cause))
    return parts


# The three causes that all say "the capture process was up in this minute and
# this instrument still got no row", at the three strengths of evidence for it.
_ALIVE_CAUSES = (ALIVE_NOT_CAPTURED, SCAN_MISSED_IT, TICK_RAN_NO_ROW)


def _cause_of(
    minute: int, proven: set[int], down_cause: str,
    process: set[int] | None,
    reached: set[int] | None = None,
) -> str:
    """One minute's cause, at the strongest evidence available for it.

    The order is the order of the evidence. A liveness line beats everything: the
    tick ran and this instrument still got no row. Failing that, and only on a
    liveness-backed session, the process journal decides between a wedged loop
    and a dead process. On a heartbeat-only session nothing here can decide, and
    the weaker ``down_cause`` the caller passed stands.
    """
    if minute in proven:
        # Alive. Whether the tick reached THIS name is a second question, and
        # only the instrument-named liveness journal answers it; without one the
        # pooled verdict stands and stays deliberately vaguer than it looks.
        if reached is None:
            return ALIVE_NOT_CAPTURED
        return TICK_RAN_NO_ROW if minute in reached else SCAN_MISSED_IT
    if down_cause != PROCESS_DOWN:
        return down_cause
    if process is None:
        return DOWN_OR_STALLED
    return TICK_STALLED if minute in process else PROCESS_DOWN


def measure_session(
    instrument: str,
    session: str,
    minutes: dict[int, int],
    *,
    alive: set[int] | None,
    ingested: set[int] | None,
    reasons: dict[int, str] | None = None,
    reach: int = HEARTBEAT_REACH_MIN,
    alive_source: str = FROM_HEARTBEAT,
    process: set[int] | None = None,
    reached: set[int] | None = None,
    witness: dict[int, dict] | None = None,
    now: float | None = None,
) -> dict:
    """One session's coverage, its gaps and how much of it reached the store.

    ``reach`` is the rate limit of whichever journal ``alive`` came from, and
    ``alive_source`` names it. They travel together on purpose: a minute-by-
    minute liveness journal and a five-minute heartbeat support very different
    claims, and the number is not comparable across the two without saying so.

    Minutes that have not happened yet are not missing minutes. A session still
    running — or not yet open — is measured only up to the last COMPLETED
    minute and flagged ``in_progress``; the caller keeps it out of the median.
    Without that, the current day reads as a catastrophic outage the moment it
    is asked about: a report run at 03:00 IST charged the whole 871-minute MCX
    session to the capture as a 0.0% failure, and a mid-session run made a 98%
    day look like 93.5%.
    """
    window = session_window(instrument, session)
    observations = sum(minutes.values())
    row: dict = {
        "session": session,
        "instrument": instrument.upper(),
        "exchange": exchange_of(instrument),
        "observations": observations,
        "captured_minutes": len(minutes),
        "alive_minutes": None if alive is None else len(alive),
        "alive_source": FROM_NOTHING if alive is None else alive_source,
        "alive_reach_min": None if alive is None else reach,
        "process_source": WITH_PROCESS if process else WITHOUT_PROCESS,
        "process_minutes": None if process is None else len(process),
        "ingested_minutes": None if ingested is None else len(ingested),
    }
    if window is None:
        row["session_minutes"] = None
        row["coverage_pct"] = None
        row["gaps"] = []
        row["not_ingested_minutes"] = (
            None if ingested is None else len(set(minutes) - ingested))
        row["not_graded"] = WEEKEND if is_weekend(session) else None
        return row
    first, last = window
    now_minute = int((time.time() if now is None else now) // MINUTE)
    # The last minute that is over. A minute still ticking has no verdict yet.
    elapsed_last = min(last, now_minute - 1)
    covered = set(minutes)
    row["session_open"] = clock(first)
    row["session_close"] = clock(last)
    row["session_minutes"] = last - first + 1
    row["in_progress"] = now_minute <= last
    row["obs_per_captured_minute"] = (
        round(observations / len(minutes), 1) if minutes else None
    )
    row["first_captured"] = clock(min(covered)) if covered else None
    row["last_captured"] = clock(max(covered)) if covered else None
    if elapsed_last < first:
        # Not one minute of it has completed, so there is nothing to grade and
        # certainly nothing to call missing.
        row["measured_minutes"] = 0
        row["measured_close"] = None
        row["captured_in_session"] = 0
        row["captured_outside_session"] = len(covered)
        row["coverage_pct"] = None
        row["gaps"] = []
        row["not_ingested_minutes"] = (
            None if ingested is None else len(covered - ingested))
        return row
    expected = elapsed_last - first + 1
    in_window = {m for m in covered if first <= m <= elapsed_last}
    row["measured_minutes"] = expected
    row["measured_close"] = clock(elapsed_last)
    row["captured_in_session"] = len(in_window)
    row["captured_outside_session"] = len(covered) - len(in_window)
    row["not_graded"] = None
    alive_in = set() if alive is None else {
        m for m in alive if first <= m <= elapsed_last}
    process_in = set() if process is None else {
        m for m in process if first <= m <= elapsed_last}
    if not in_window and not alive_in and not process_in:
        # Nothing inside the window: no row, no liveness line, no process line.
        # An exchange holiday and a capture that never started that day leave
        # exactly this, so the session is reported and left ungraded instead of
        # being scored 0% against one of the two explanations.
        row["coverage_pct"] = None
        row["gaps"] = []
        row["not_graded"] = NO_SESSION_EVIDENCE
        row["not_ingested_minutes"] = (
            None if ingested is None else len(covered - ingested))
        return row
    row["coverage_pct"] = round(100.0 * len(in_window) / expected, 1)
    down_cause = (
        PROCESS_DOWN if alive_source == FROM_LIVENESS else NOT_EVIDENCED)
    found = gaps(covered, (first, elapsed_last), alive, reasons, reach=reach,
                 down_cause=down_cause, process=process, reached=reached,
                 witness=witness)
    row["gaps"] = found
    row["missing_minutes"] = expected - len(in_window)
    # The tick path did not run, whatever the reason. Kept as one number because
    # it is the total the coverage percentage is short by, then split below into
    # the parts that have different fixes.
    tick_causes = (down_cause, TICK_STALLED, DOWN_OR_STALLED)
    row["missing_tick_not_running"] = sum(
        g["minutes"] for g in found if g["cause"] in tick_causes)
    row["missing_process_down"] = sum(
        g["minutes"] for g in found if g["cause"] == PROCESS_DOWN)
    row["missing_tick_stalled"] = sum(
        g["minutes"] for g in found if g["cause"] == TICK_STALLED)
    row["missing_down_or_stalled"] = sum(
        g["minutes"] for g in found
        if g["cause"] in (DOWN_OR_STALLED, NOT_EVIDENCED))
    row["down_cause"] = down_cause
    row["missing_alive_not_captured"] = sum(
        g["minutes"] for g in found if g["cause"] in _ALIVE_CAUSES)
    row["missing_scan_missed_it"] = sum(
        g["minutes"] for g in found if g["cause"] == SCAN_MISSED_IT)
    row["missing_tick_ran_no_row"] = sum(
        g["minutes"] for g in found if g["cause"] == TICK_RAN_NO_ROW)
    row["reached_minutes"] = None if reached is None else len(reached)
    row["witness"] = _merge_witness(found)
    row["witnessed_minutes"] = (
        None if witness is None
        else sum(int(w["minutes"]) for w in row["witness"]))
    tally: dict[str, int] = {}
    for gap in found:
        for why, count in (gap.get("reasons") or {}).items():
            tally[why] = tally.get(why, 0) + count
    row["reasons"] = tally
    row["unexplained_alive_minutes"] = (
        None if reasons is None else
        sum(g["unexplained_minutes"] or 0 for g in found
            if g["cause"] in _ALIVE_CAUSES)
    )
    if ingested is None:
        row["not_ingested_minutes"] = None
    else:
        row["not_ingested_minutes"] = len(covered - ingested)
    return row


def _merge_witness(found: list[dict]) -> list[dict]:
    """Every gap's witness in one list, largest first."""
    seen: dict[tuple[str, str | None], dict] = {}
    for gap in found:
        for entry in gap.get("witness") or []:
            key = (entry["where"], entry["instrument"])
            held = seen.get(key)
            if held is None:
                seen[key] = dict(entry)
                continue
            held["minutes"] += entry["minutes"]
            held["worst_sec"] = max(held["worst_sec"], entry["worst_sec"])
    return sorted(seen.values(),
                  key=lambda e: (-e["minutes"], -e["worst_sec"]))


# What an operator would do about the dominant cause. The whole point of
# splitting the causes was that they need opposite fixes, so the rollup names
# the fix rather than leaving the reader to map a constant to an action.
FIX_SUPERVISION = "RESTART_SUPERVISION_WOULD_HAVE_RECOVERED_THESE_MINUTES"
FIX_TICK_PATH = "FIX_THE_TICK_PATH_A_RESTART_RECOVERS_NONE_OF_THESE_MINUTES"
FIX_UNDECIDED = "NOT_DECIDABLE_FROM_THESE_JOURNALS_NO_FIX_IS_INDICATED_YET"
# Both live in the tick path, and pointing at the wrong one wastes the work:
# widening the scan's reach recovers nothing from a capture that was reached and
# wrote nothing, and instrumenting the capture recovers nothing from a name the
# scan never got to.
FIX_SCAN_REACH = "GIVE_THE_SCAN_A_REACH_THIS_INSTRUMENT_IS_INSIDE_EVERY_MINUTE"
FIX_CAPTURE_PATH = "THE_TICK_REACHED_IT_FIX_WHAT_SWALLOWED_THE_ROW_AND_RECORD_IT"

_FIX_OF: dict[str, str] = {
    PROCESS_DOWN: FIX_SUPERVISION,
    TICK_STALLED: FIX_TICK_PATH,
    ALIVE_NOT_CAPTURED: FIX_TICK_PATH,
    SCAN_MISSED_IT: FIX_SCAN_REACH,
    TICK_RAN_NO_ROW: FIX_CAPTURE_PATH,
    DOWN_OR_STALLED: FIX_UNDECIDED,
    NOT_EVIDENCED: FIX_UNDECIDED,
    NO_HEARTBEAT_FILE: FIX_UNDECIDED,
}

# Nothing is missing, so naming a dominant cause would be inventing one.
NOTHING_MISSING = "NO_MISSING_MINUTE_IN_THE_GRADED_SESSIONS"
# An alive minute with no row AND no refusal reason. It gets its own line
# beside the recorded refusals or it would be invisible among them, but it is
# named for what the journals hold and no further: an earlier build called this
# "the scan never reached it", which the pooled heartbeat cannot say. Which of
# the two it was is SCAN_MISSED_IT vs TICK_RAN_NO_ROW in the cause split, and
# only sessions with the instrument-named liveness journal can be split at all.
SCAN_NEVER_REACHED = "ALIVE_AND_EMPTY_NO_ROW_AND_NO_REFUSAL_REASON_WAS_WRITTEN"


def decided(cause: str) -> bool:
    """True when this cause names something, rather than naming the ignorance.

    Cut by cause and not by session on purpose. A day whose only journal is the
    heartbeat still *proves* ALIVE_NOT_CAPTURED for a minute the capture was up
    in and wrote nothing — that minute is decided, and no journal shipped later
    can change it. What such a day cannot decide is its missing-and-not-alive
    minutes, and those stay undecided forever: a session that is over grows no
    new evidence.

    Splitting per session got this wrong, and silently: on a range where every
    completed session predates the liveness journal it set aside the proven
    minutes along with the unprovable ones and named no fix at all, while the
    proven cause sat in the total underneath.
    """
    return _FIX_OF.get(cause, FIX_UNDECIDED) != FIX_UNDECIDED


def _aggregate(graded: list[dict]) -> dict:
    """Total the missing minutes of already-graded sessions by cause."""
    by_cause: dict[str, int] = {}
    reasons: dict[str, int] = {}
    measured = captured = missing = unexplained = 0
    unexplained_known = False
    for row in graded:
        measured += int(row.get("measured_minutes") or 0)
        captured += int(row.get("captured_in_session") or 0)
        missing += int(row.get("missing_minutes") or 0)
        for gap in row.get("gaps") or []:
            by_cause[gap["cause"]] = (
                by_cause.get(gap["cause"], 0) + int(gap["minutes"]))
        for why, count in (row.get("reasons") or {}).items():
            reasons[why] = reasons.get(why, 0) + int(count)
        if row.get("unexplained_alive_minutes") is not None:
            unexplained_known = True
            unexplained += int(row["unexplained_alive_minutes"])
    if unexplained:
        reasons[SCAN_NEVER_REACHED] = unexplained
    top = max(by_cause.items(), key=lambda kv: kv[1], default=None)
    attributed = sum(by_cause.values())
    return {
        "sessions_rolled_up": len(graded),
        "measured_minutes": measured,
        "captured_minutes": captured,
        "missing_minutes": missing,
        # A gap shorter than MIN_REPORTED_GAP_MIN is not reported as a gap, so
        # the attributed total can fall short of the missing total. Said out
        # loud rather than folded into a cause that did not earn it.
        "attributed_minutes": attributed,
        "unattributed_minutes": missing - attributed,
        "by_cause": by_cause,
        "refusal_reasons": reasons,
        "unexplained_alive_minutes": unexplained if unexplained_known else None,
        "dominant_cause": NOTHING_MISSING if top is None else top[0],
        "dominant_minutes": 0 if top is None else top[1],
        "dominant_share_pct": (
            None if top is None or not missing
            else round(100.0 * top[1] / missing, 1)),
        "indicated_fix": (
            FIX_UNDECIDED if top is None
            else _FIX_OF.get(top[0], FIX_UNDECIDED)),
    }


def rollup(rows: list[dict]) -> dict:
    """Missing minutes by cause across sessions, and which cause dominates.

    A per-session block is what a hole is recognised from; this is what decides
    where the work goes. Graded, completed sessions only — a weekend, a holiday
    and a session three minutes old have no missing minutes to attribute, and
    admitting them is how a closed Saturday once set the worst number for a
    whole week.

    It attributes and invents nothing. Minutes whose journals cannot separate a
    dead process from a wedged loop stay in their undecided bucket and can be
    the dominant cause of the total, which is the honest answer when it is
    true — but because that answer directs no work, the minutes that DO carry a
    named cause are totalled again on their own, and the indicated fix follows
    the dominant one of those. See :func:`decided`.
    """
    graded = [r for r in rows
              if r.get("coverage_pct") is not None and not r.get("in_progress")]
    out = _aggregate(graded)
    named = {c: m for c, m in out["by_cause"].items() if decided(c)}
    unnamed = sum(m for c, m in out["by_cause"].items() if not decided(c))
    top = max(named.items(), key=lambda kv: kv[1], default=None)
    total = sum(named.values())
    out["minutes_with_a_named_cause"] = total
    out["minutes_no_journal_can_attribute"] = unnamed
    out["by_named_cause"] = named
    out["dominant_named_cause"] = None if top is None else top[0]
    out["dominant_named_minutes"] = 0 if top is None else top[1]
    out["dominant_named_share_pct"] = (
        None if top is None or not total else round(100.0 * top[1] / total, 1))
    # The fix follows the dominant DECIDED cause. Undecided minutes cannot
    # indicate work, so they no longer suppress the work that is indicated.
    out["indicated_fix"] = (
        FIX_UNDECIDED if top is None else _FIX_OF[top[0]])
    return out


def data_dir_usage() -> dict:
    """Bytes the observation journals occupy and bytes left on their filesystem.

    Here because it is the one capture failure that leaves no trace in the
    journals themselves: :func:`app.research.phase17.store._append` counts an
    ``OSError`` into its health state and returns, so a full disk produces a
    coverage hole that looks exactly like a clean shutdown.
    """
    files = observation_files()
    total = 0
    for path in files:
        try:
            total += os.path.getsize(path)
        except OSError:
            continue
    free: int | None = None
    try:
        stat = os.statvfs(settings.data_dir)
        free = int(stat.f_bavail) * int(stat.f_frsize)
    except (OSError, AttributeError):
        free = None
    return {
        "journal_files": len(files),
        "journal_bytes": total,
        "free_bytes": free,
        "data_dir": settings.data_dir,
    }


def measure(
    instrument: str,
    *,
    sessions: list[str] | None = None,
    limit: int | None = None,
    db_path: str | None = None,
    now: float | None = None,
) -> dict:
    """Coverage for every session the observation journal holds, newest last."""
    minutes = captured_minutes(instrument)
    alive = alive_minutes()
    live = alive_minutes(paths=liveness_files())
    up = alive_minutes(paths=process_files())
    got = reached_minutes(instrument)
    reasons = gap_reasons(instrument)
    saw = stalls()
    ingested = None if db_path is None else ingested_minutes(db_path, instrument)
    keys = sorted(minutes)
    if sessions:
        wanted = set(sessions)
        keys = [k for k in keys if k in wanted]
    if limit is not None and limit > 0:
        keys = keys[-limit:]
    rows = []
    for key in keys:
        # The liveness journal where the session has one, the heartbeat where it
        # does not. Per session rather than per run: the journal starts the day
        # it ships, and the sessions either side of that are not equally well
        # evidenced.
        if live.get(key):
            beats, reach, source = live[key], LIVENESS_REACH_MIN, FROM_LIVENESS
        elif alive:
            beats, reach, source = (
                alive.get(key, set()), HEARTBEAT_REACH_MIN, FROM_HEARTBEAT)
        else:
            beats, reach, source = None, HEARTBEAT_REACH_MIN, FROM_NOTHING
        rows.append(measure_session(
            instrument, key, minutes[key],
            alive=beats,
            # Only for a session the PER-NAME journal covers. Keyed presence,
            # not truth: an empty set there is real evidence — the journal ran
            # all day and never named this instrument — while a missing key is
            # a day the file cannot speak for, and reading that as "never
            # reached" is the strongest claim in the module made from the
            # weakest evidence there is.
            reached=got.get(key) if got is not None and key in got else None,
            ingested=None if ingested is None else ingested.get(key, set()),
            reasons=None if reasons is None else reasons.get(key, {}),
            # ``None`` for a session the witness journal does not cover at all,
            # so "nothing was seen" is never confused with "it was not watching".
            witness=saw.get(key) if key in saw else None,
            reach=reach,
            alive_source=source,
            # ``None``, not an empty set: a session with no process line at all
            # is one the journal cannot speak for, and passing an empty set
            # would read every silent minute as a dead process.
            process=up.get(key) or None,
            now=now,
        ))
    # A session still running is measured and printed, but it is not allowed
    # into the summary: a day three minutes old would drag the median down as
    # if the capture had failed for it.
    graded = [r for r in rows
              if r.get("coverage_pct") is not None and not r.get("in_progress")]
    return {
        "instrument": instrument.upper(),
        "sessions": rows,
        "session_count": len(rows),
        "median_coverage_pct": (
            sorted(r["coverage_pct"] for r in graded)[len(graded) // 2]
            if graded else None
        ),
        "worst_coverage_pct": (
            min(r["coverage_pct"] for r in graded) if graded else None
        ),
        "best_coverage_pct": (
            max(r["coverage_pct"] for r in graded) if graded else None
        ),
        "graded_session_count": len(graded),
        "in_progress_sessions": [
            r["session"] for r in rows if r.get("in_progress")],
        "ungraded_sessions": [
            {"session": r["session"], "why": r["not_graded"]}
            for r in rows if r.get("not_graded")],
        "heartbeat_available": bool(alive),
        "liveness_available": bool(live),
        "process_available": bool(up),
        "reasons_available": reasons is not None,
        "ingest_checked": ingested is not None,
        "where_the_minutes_went": rollup(rows),
        "usage": data_dir_usage(),
    }
