"""Phase 35 §2/§4 — turn captured market rows into opportunity observations.

This module does not talk to the feed. It reads what the existing Phase 17
capture already wrote at the decision instant (both option sides, the futures
book, the plan and the context) and re-expresses each row as an *opportunity*:
one per decision, tagged ENGINE when the system asked to act and BOARD when it
did not. §2 is explicit that research must not restrict itself to engine BUYs,
and that is what makes the engine-vs-market comparison possible at all — the
declined rows are the counterfactual.

Why re-express rather than analyse Phase 17 in place: Phase 17's row is
engine-centric (there is a "selected" side and an "opposite" side, which only
means something if the engine selected). Phase 35 needs a vehicle-symmetric row
where CE, PE and FUTURES stand on equal footing, because §12 and §13 compare
them against each other and against standing aside.

Three rules are enforced here rather than downstream, because a rule applied at
read time can be forgotten by the next reader:

* an executable price is a **quoted bid or ask**. The traded price is carried in
  its own column and is never copied into ``bid``/``ask`` to fill a gap;
* a quote with no two-sided book is stored with ``evidence=UNMEASURED`` and a
  reason, not dropped. Absence of a book at the moment the engine wanted to act
  is one of the findings, so deleting those rows would erase it;
* nothing is interpolated or carried forward. A quote belongs to the timestamp
  it was measured at, and paths are built only from other measured timestamps.
"""
from __future__ import annotations

import json
import os
import re
import time

from app.research.phase17 import schema as p17schema
from app.research.phase17 import store as p17store
from app.research.phase35 import (
    CE,
    EXECUTABLE_BOOK_MAX_AGE_MS,
    FIXTURE_ROW,
    FUTURES,
    MEASURED_EXECUTABLE,
    MEASURED_TRADED_PRICE,
    NO_BOOK,
    NO_QUOTE,
    PE,
    REAL_QUOTE_SOURCES,
    SIMULATED_BOOK,
    STALE_QUOTE,
    UNMEASURED,
    identity,
    store,
)

# The smoke fixtures use sequential placeholder ids. They carry a complete
# two-sided book, so without this guard the only "executable" evidence in the
# whole store would be test data — which is how an earlier audit was misled.
FIXTURE_ID = re.compile(r"^(ep|gs|obs)-\d+$")

ORIGIN_PHASE17 = "PHASE17_OBSERVATIONS"

# Phase 17's reader is a tail of the *live* file, which is right for a dashboard
# and wrong here: §21 wants every raw observation ever captured, and the rotated
# files are where the earliest two-sided books live. So the rotated set is read
# in full, oldest first, and de-duplication is left to the content-addressed id.
#
# The file list comes from :func:`p17store.series_paths` rather than a glob of
# this module's own. A glob written here was ``phase17_observations*.jsonl``,
# which stopped matching a roll the moment the journal compaction gzipped it and
# removed the original — the ingest then reported every file complete while a
# third of a session's captured minutes were in a ``.gz`` it could not see. The
# series helper knows about the compressed twin, and :func:`p17store.open_series`
# reads either, which is the whole reason both exist.


def source_files(*, since: str | None = None) -> list[str]:
    """Live and rotated Phase 17 observation files, oldest first.

    Compressed rolls included: a gzipped roll holds exactly the lines its
    uncompressed twin held, verified line for line before the original was
    removed, so skipping it is evidence loss and nothing else.

    ``since`` is a ``YYYY-MM-DD`` local date that drops files last written before
    that day began. It is a throughput filter on this pass only: a rotated file
    stopped being written when it was rolled, so one that was last written before
    the date cannot hold a later observation, and the rows it does hold stay
    ingestable by a later unfiltered pass.
    """
    files = [p for p in p17store.series_paths(p17store.OBSERVATIONS)
             if os.path.exists(p)]
    floor = _since_epoch(since)
    if floor is None:
        return files
    kept = []
    for fp in files:
        try:
            if os.path.getmtime(fp) >= floor:
                kept.append(fp)
        except OSError:
            kept.append(fp)
    return kept


def _since_epoch(since: str | None) -> float | None:
    """Local midnight of a ``YYYY-MM-DD`` date, or None when not supplied."""
    text = (since or "").strip()
    if not text:
        return None
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        raise ValueError("since must be YYYY-MM-DD")
    parts = time.strptime(text, "%Y-%m-%d")
    return time.mktime(
        (parts.tm_year, parts.tm_mon, parts.tm_mday, 0, 0, 0, 0, 0, -1)
    )


def source_rows(
    *, instrument: str | None = None, since: str | None = None
) -> list[dict]:
    """Every captured Phase 17 observation, from every rotated file.

    Unparseable lines are skipped rather than aborting the ingest: one truncated
    trailing line (a process killed mid-write) must not cost the other 100k rows.

    ``instrument`` narrows *this pass* to one instrument. It is a throughput
    filter and nothing else: the rows it skips are neither deleted nor marked,
    so a later unfiltered pass ingests them normally. The substring pre-check
    is what makes it worth having — it decides most lines without paying for
    ``json.loads``, which is where the whole walk spends its time.

    ``since`` narrows which *files* are opened; see :func:`source_files`. Once
    the archive is several gigabytes the byte scan dominates, and rolls that were
    already ingested cost scan time and contribute nothing.
    """
    want = (instrument or "").strip().upper() or None
    needle = f'"instrument": "{want}"' if want else None
    rows: list[dict] = []
    for fp in source_files(since=since):
        try:
            with p17store.open_series(fp) as fh:
                for raw in fh:
                    line = raw.decode("utf-8", "ignore")
                    if needle is not None and needle not in line:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    # The pre-check is a hint, not the decision: a nested field
                    # could carry the same text. The parsed row is authoritative.
                    if want and str(row.get("instrument") or "").upper() != want:
                        continue
                    rows.append(row)
        except OSError:
            continue
    rows.sort(key=lambda r: float(r.get("signal_ts") or r.get("capture_ts") or 0.0))
    return rows


def is_fixture(row: dict) -> bool:
    """True for a smoke-fixture observation, which is not market evidence."""
    for key in ("market_signal_id", "global_signal_id", "episode_id"):
        value = row.get(key)
        if isinstance(value, str) and FIXTURE_ID.match(value):
            return True
    return False


def _book_is_fresh(q: p17schema.Quote) -> bool:
    """True only when the feed dated this bid/ask inside the tolerance.

    An undated book is refused rather than assumed live: every observation
    captured before the feed started stamping the book falls here, which is
    honest — those rows cannot distinguish a book quoted at the instant from
    one carried forward for twenty minutes.
    """
    age = getattr(q, "book_age_ms", None)
    if not isinstance(age, (int, float)):
        return False
    return 0.0 <= float(age) <= EXECUTABLE_BOOK_MAX_AGE_MS


def _quote_row(obs_id: str, vehicle: str, q: p17schema.Quote | None,
               *, lot_size: int | None, fixture: bool) -> dict:
    """One raw_quote row, with its evidence label decided by provenance.

    A two-sided book is only executable evidence when a real feed produced it
    AND the feed dated that book inside the freshness tolerance. Provenance
    alone is not enough: the market provider keeps the last book on a tick that
    arrives without depth, so a real WEBSOCKET bid/ask can be minutes stale and
    still be non-null here — which would be the "nearby quote" §4 forbids,
    wearing executable provenance.

    Simulator, fixture and stale rows are still stored, with their reason, so
    the gap is visible; their bid/ask is not copied into the executable columns,
    because a column is the only thing a later reader looks at. The untouched
    Phase 17 capture files remain the raw record of what was written.
    """
    if q is None:
        return {
            "obs_id": obs_id, "vehicle": vehicle, "ts": 0.0,
            "evidence": UNMEASURED, "reason": NO_QUOTE, "lot_size": lot_size,
        }
    # The multiplier the feed published for THIS contract beats the plan's,
    # which is one number for the whole observation and can only be right for
    # one of its three vehicles. Falls back to the plan when the capture predates
    # per-quote lot sizes.
    if isinstance(q.lot_size, int) and q.lot_size > 0:
        lot_size = q.lot_size
    real_feed = (q.source or "").upper() in REAL_QUOTE_SOURCES and not fixture
    fresh_book = _book_is_fresh(q)
    two_sided = bool(q.has_book) and real_feed and fresh_book
    if not real_feed:
        reason = FIXTURE_ROW if fixture else SIMULATED_BOOK
        evidence = UNMEASURED
    elif bool(q.has_book) and not fresh_book:
        # A real book that cannot be proved to have existed at the decision
        # instant. Not a missing book, and not an executable one either.
        reason = STALE_QUOTE
        evidence = MEASURED_TRADED_PRICE if q.premium else UNMEASURED
    else:
        reason = None if two_sided else NO_BOOK
        evidence = MEASURED_EXECUTABLE if two_sided else (
            MEASURED_TRADED_PRICE if q.premium else UNMEASURED
        )
    return {
        "obs_id": obs_id,
        "vehicle": vehicle,
        "ts": float(q.snapshot_ts or 0.0),
        "symbol": q.symbol,
        "strike": q.strike,
        "expiry": q.expiry,
        "dte": q.days_to_expiry,
        "bid": q.bid if two_sided else None,
        "ask": q.ask if two_sided else None,
        "traded": q.premium,
        "spread": q.spread,
        "spread_pct": q.spread_pct,
        "delta": q.delta,
        "iv": q.iv,
        "oi": q.oi,
        "volume": q.volume,
        "underlying": q.underlying_price,
        "basis": (
            round(float(q.premium) - float(q.underlying_price), 4)
            if vehicle == FUTURES and q.premium and q.underlying_price else None
        ),
        "lot_size": lot_size,
        "evidence": evidence,
        "reason": reason,
    }


def _vehicle_quotes(obs: p17schema.Observation) -> dict[str, p17schema.Quote | None]:
    """Re-key Phase 17's selected/opposite/futures onto CE / PE / FUTURES.

    The window is not folded in: those are the neighbouring strikes the capture
    also saw, and treating them as the opportunity's own vehicle would let a
    later study pick the strike that worked, which §13 forbids.
    """
    out: dict[str, p17schema.Quote | None] = {CE: None, PE: None, FUTURES: None}
    for q in (obs.selected, obs.opposite, obs.futures):
        if q is None:
            continue
        if q.vehicle in out and out[q.vehicle] is None:
            out[q.vehicle] = q
    return out


def observation_rows(row: dict, *, now: float | None = None) -> tuple[dict, list[dict]]:
    """Build one observation row and its vehicle quote rows from a Phase 17 row."""
    obs = p17schema.Observation.from_dict(row)
    ts = float(obs.signal_ts or obs.capture_ts or 0.0)
    source = identity.source_of(obs.candidate_class)
    otype = identity.classify_type(
        row.get("setup") or row.get("market_signal") or obs.context.market_signal,
        direction=obs.direction,
    )
    obs_id = identity.opportunity_id(
        instrument=obs.instrument,
        ts=ts,
        source=source,
        opportunity_type=otype,
        direction=obs.direction,
    )
    observation = {
        "obs_id": obs_id,
        "ts": ts,
        "session": obs.session or obs.context.session,
        "instrument": obs.instrument,
        "family": obs.family,
        "source": source,
        "opportunity_type": otype,
        "direction": obs.direction,
        "engine_class": obs.candidate_class,
        "engine_selected": obs.candidate_class == p17schema.BUY,
        "selected_vehicle": obs.selected_vehicle,
        "context": {
            **obs.context.as_dict(),
            "plan": obs.plan.as_dict(),
            "data_quality": obs.data_quality,
            "both_sides": obs.both_sides,
        },
        "origin": ORIGIN_PHASE17,
        "origin_id": obs.observation_id,
        "ingest_ts": float(now if now is not None else time.time()),
    }
    lot = obs.plan.lot_size if isinstance(obs.plan.lot_size, int) else None
    fixture = is_fixture(row)
    observation["context"]["fixture_row"] = fixture
    quotes = [
        _quote_row(obs_id, vehicle, q, lot_size=lot, fixture=fixture)
        for vehicle, q in _vehicle_quotes(obs).items()
    ]
    return observation, quotes


INGEST_BATCH = 2000


def ingest_stream(
    con,
    *,
    instrument: str | None = None,
    since: str | None = None,
    limit: int | None = None,
    now: float | None = None,
    resume: bool = True,
    batch: int = INGEST_BATCH,
    progress=None,
) -> dict:
    """The same ingest, file by file, resuming where the last pass stopped.

    :func:`ingest` reads every capture file in full on every pass, parses every
    line, holds every row in memory and writes once at the end. That is affordable
    while the archive is small. It stops being affordable when the live file rolls
    at 256 MB and the rolls are kept: the cost of a nightly pass then grows with
    the whole history rather than with the day, and the pass says nothing until it
    is finished.

    Two things change here and nothing else does:

    * **Resume.** A byte offset per file is recorded in ``capture_cursor`` once
      the rows before it are committed. A rolled file stopped being written when
      it was rolled, so an offset at its end means it is finished and never
      reopened; the live file resumes at its own offset and reads only what was
      appended since. A trailing line without its newline is a write in progress:
      it is left unread and the offset stops before it, so the next pass sees it
      whole. The offset is written *after* the rows, so an interruption re-reads a
      batch and can never skip one. A compressed roll cannot use "offset reached
      the size" as its completion test, because the offset counts decompressed
      bytes and the size is compressed; end of file is recorded explicitly
      instead, and honoured only while size and mtime still match.
    * **Streaming.** Rows are converted and written in batches instead of being
      accumulated. Insert order carries no meaning — ids are content-addressed and
      every reader sorts by ``ts`` itself — so the store this produces is the same
      store, and ``progress`` lets a long pass report while it runs.

    ``limit`` here caps how many source rows *this pass* ingests, oldest file
    first, rather than taking the newest N of a fully materialised list. Anything
    not reached stays ingestable by the next pass.

    An ``instrument``-filtered pass neither reads nor writes an offset. An offset
    it wrote would say "these bytes are done" when only one instrument's rows in
    them were ingested, and the next unfiltered pass would skip every other
    instrument in that range — a filter for throughput would have silently become
    a filter on the evidence. ``since`` is safe by contrast: it skips whole files
    and leaves their offsets untouched.
    """
    want = (instrument or "").strip().upper() or None
    needle = want.encode() if want else None
    use_cursor = want is None
    cursors = store.capture_cursors(con) if (resume and use_cursor) else {}
    stamp = float(now if now is not None else time.time())
    files_read = 0
    files_skipped = 0
    source_seen = 0
    skipped = 0
    offered_obs = 0
    offered_quotes = 0
    written_obs = 0
    written_quotes = 0
    stopped_early = False

    for fp in source_files(since=since):
        try:
            st = os.stat(fp)
        except OSError:
            continue
        packed = fp.endswith(p17store.GZ)
        prior = cursors.get(fp)
        start = 0
        same_file = prior is not None and (
            int(prior["size"]) == st.st_size
            and abs(float(prior["mtime"]) - st.st_mtime) < 1e-6
        )
        if prior is not None:
            if packed:
                # Nothing about a gzip's own size bounds its decompressed
                # offset, so the only safe resume is one for a file that has not
                # changed at all since the offset was taken.
                start = int(prior["offset"]) if same_file else 0
            else:
                # A path whose file is now shorter than the recorded offset is
                # not the file that was measured: read it from the beginning
                # rather than from the middle of a line.
                start = (
                    0 if st.st_size < int(prior["offset"])
                    else int(prior["offset"])
                )
        if same_file and int(prior.get("complete") or 0):
            files_skipped += 1
            continue
        if not packed and start >= st.st_size:
            files_skipped += 1
            continue
        rows_before = int(prior["rows_seen"]) if prior is not None else 0
        obs_rows: list[dict] = []
        quote_rows: list[dict] = []
        offset = start
        rows_this_file = 0

        def flush(fp=fp, st=st, done: bool = False) -> None:
            nonlocal written_obs, written_quotes, obs_rows, quote_rows
            if obs_rows or quote_rows:
                written_obs += store.save_observations(con, obs_rows)
                written_quotes += store.save_quotes(con, quote_rows)
                obs_rows = []
                quote_rows = []
            if use_cursor:
                store.save_capture_cursor(
                    con, fp, size=st.st_size, mtime=st.st_mtime, offset=offset,
                    rows_seen=rows_before + rows_this_file, now=stamp,
                    complete=done,
                )

        reached_eof = False
        try:
            with p17store.open_series(fp) as fh:
                if start:
                    # Forward-only on a gzip, which decompresses and discards;
                    # a plain file seeks outright.
                    fh.seek(start)
                for raw in fh:
                    if not raw.endswith(b"\n"):
                        break            # a line still being written
                    offset += len(raw)
                    if needle is not None and needle not in raw.upper():
                        continue
                    text = raw.decode("utf-8", "ignore").strip()
                    if not text:
                        continue
                    try:
                        row = json.loads(text)
                    except ValueError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    if want and str(row.get("instrument") or "").upper() != want:
                        continue
                    source_seen += 1
                    rows_this_file += 1
                    if not row.get("instrument") or not (
                        row.get("signal_ts") or row.get("capture_ts")
                    ):
                        skipped += 1
                    else:
                        obs, qs = observation_rows(row, now=now)
                        obs_rows.append(obs)
                        quote_rows.extend(qs)
                        offered_obs += 1
                        offered_quotes += len(qs)
                    if batch and len(obs_rows) >= batch:
                        flush()
                        if progress is not None:
                            progress({
                                "file": os.path.basename(fp),
                                "source_rows": source_seen,
                                "observations_written": written_obs,
                            })
                    if limit is not None and source_seen >= limit:
                        stopped_early = True
                        break
                else:
                    reached_eof = True
        except OSError:
            continue
        # Complete only for a file nothing appends to again. The live file is
        # read to its end on every pass and grows afterwards, so recording it as
        # finished would skip whatever the capture wrote next.
        flush(done=reached_eof and fp != p17store.path(p17store.OBSERVATIONS))
        files_read += 1
        if progress is not None:
            progress({
                "file": os.path.basename(fp),
                "source_rows": source_seen,
                "observations_written": written_obs,
                "file_done": True,
            })
        if stopped_early:
            break

    return {
        "origin": ORIGIN_PHASE17,
        "mode": (
            ("RESUMABLE_STREAM" if resume else "FULL_RESCAN_STREAM")
            if use_cursor else "FULL_READ_NO_CURSOR_INSTRUMENT_FILTERED"
        ),
        "instrument_filter": want,
        "since_filter": (since or "").strip() or None,
        "files_read": files_read,
        "files_already_complete": files_skipped,
        "source_rows": source_seen,
        "skipped_rows": skipped,
        "observations_offered": offered_obs,
        "observations_written": written_obs,
        "quotes_offered": offered_quotes,
        "quotes_written": written_quotes,
        "stopped_at_limit": stopped_early,
        "note": (
            "written counts exclude rows already present; offsets are recorded "
            "after the rows they cover, so an interruption re-reads and never skips"
        ),
    }


def ingest(
    con,
    *,
    limit: int | None = None,
    now: float | None = None,
    instrument: str | None = None,
    since: str | None = None,
) -> dict:
    """Copy Phase 17 captures into the Phase 35 raw store, idempotently.

    Returns what was actually written rather than what was offered, so running
    this twice reports zero new rows instead of double the evidence.

    ``instrument`` restricts which captured rows this pass reads. Every
    calculation applied to the rows that pass is identical — the filter runs
    before any labelling, so a row cannot be classified differently for having
    been ingested alone. ``since`` does the same for whole files by their last
    write time, which is what makes a nightly pass cheap once the archive is
    large.
    """
    rows = source_rows(instrument=instrument, since=since)
    if limit is not None and limit >= 0:
        rows = rows[-limit:]
    observations: list[dict] = []
    quotes: list[dict] = []
    skipped = 0
    for row in rows:
        if not row.get("instrument") or not (row.get("signal_ts") or row.get("capture_ts")):
            skipped += 1
            continue
        obs, qs = observation_rows(row, now=now)
        observations.append(obs)
        quotes.extend(qs)
    written_obs = store.save_observations(con, observations)
    written_quotes = store.save_quotes(con, quotes)
    return {
        "origin": ORIGIN_PHASE17,
        "instrument_filter": (instrument or "").strip().upper() or None,
        "since_filter": (since or "").strip() or None,
        "files_read": len(source_files(since=since)),
        "source_rows": len(rows),
        "skipped_rows": skipped,
        "observations_offered": len(observations),
        "observations_written": written_obs,
        "quotes_offered": len(quotes),
        "quotes_written": written_quotes,
        "note": "written counts exclude rows already present; the store converges",
    }


def coverage(con) -> dict:
    """How much of the store can answer an executable question at all — §4/§16."""
    total = con.execute("SELECT COUNT(*) AS n FROM raw_observation").fetchone()["n"]
    sessions = con.execute(
        "SELECT COUNT(DISTINCT session) AS n FROM raw_observation "
        "WHERE session IS NOT NULL"
    ).fetchone()["n"]
    by_source = {
        r["source"]: int(r["n"])
        for r in con.execute(
            "SELECT source, COUNT(*) AS n FROM raw_observation GROUP BY source"
        ).fetchall()
    }
    by_vehicle: dict[str, dict] = {}
    for vehicle in (CE, PE, FUTURES):
        row = con.execute(
            "SELECT COUNT(*) AS n, "
            "SUM(CASE WHEN evidence = ? THEN 1 ELSE 0 END) AS executable "
            "FROM raw_quote WHERE vehicle = ?",
            (MEASURED_EXECUTABLE, vehicle),
        ).fetchone()
        n = int(row["n"] or 0)
        ex = int(row["executable"] or 0)
        by_vehicle[vehicle] = {
            "quotes": n,
            "executable": ex,
            "executable_pct": round(100.0 * ex / n, 2) if n else None,
        }
    by_reason = {
        str(r["reason"]): int(r["n"])
        for r in con.execute(
            "SELECT reason, COUNT(*) AS n FROM raw_quote "
            "WHERE reason IS NOT NULL GROUP BY reason ORDER BY n DESC"
        ).fetchall()
    }
    return {
        "observations": int(total or 0),
        "sessions": int(sessions or 0),
        "by_source": by_source,
        "by_vehicle": by_vehicle,
        "unmeasured_by_reason": by_reason,
        "note": (
            "executable means a real feed quoted both sides at the decision "
            "instant; simulator and fixture books are stored but never counted"
        ),
    }
