"""Phase 35 §5 — build the two paper books from raw observations.

``CURRENT_ENGINE_PAPER`` contains exactly one leg per engine BUY, in the vehicle
the engine itself selected. Nothing here re-picks the strike, re-times the entry
or improves the exit: the book exists to say what the existing logic would have
done, so any improvement written into it would destroy the only baseline the
phase has.

``FULL_MARKET_PAPER`` contains one leg per *vehicle with a real executable book*
at every observed decision instant, whether or not the engine wanted to act. It
is deliberately much larger and it is not a strategy — it is the denominator.
Without it, "the engine missed a winner" cannot be distinguished from "there was
nothing there", and those two have opposite implications for what to fix.

Both books are derived tables: a re-run rebuilds them from raw rows, so a change
to the cost model or the target ladder can be applied to history without
rewriting a single measured observation.
"""
from __future__ import annotations

import time

from app.research.phase35 import (
    CE,
    CURRENT_ENGINE_PAPER,
    FULL_MARKET_PAPER,
    FUTURES,
    LONG,
    MEASURED_EXECUTABLE,
    PE,
    SESSION_CLOSE,
    UNMEASURED,
    attrib,
    book,
    lots,
    normalize_direction,
    path,
    store,
)

VEHICLE_ORDER = (CE, PE, FUTURES)


def _direction_for(vehicle: str, obs: dict) -> str:
    """The direction the leg is expressed in.

    A long option is always long its own premium, so CE and PE legs are LONG in
    premium terms and their view lives in which side was bought. A futures leg
    carries the observation's direction, defaulting to LONG when the row never
    recorded one rather than guessing SHORT.

    The stored word is translated, not compared raw: capture preserves Phase 17's
    "BEARISH", and an untranslated "BEARISH" is not equal to SHORT, so the leg
    would have been priced and graded as a long.
    """
    if vehicle != FUTURES:
        return LONG
    return normalize_direction(obs.get("direction")) or LONG



def build_leg(
    con, obs: dict, quote: dict, *, bk: str,
    cache: path.ForwardCache | None = None,
) -> tuple[dict, list[dict]]:
    """One paper leg plus its horizon rows. Never raises on missing data."""
    vehicle = quote["vehicle"]
    direction = _direction_for(vehicle, obs)
    lid = book.leg_id(obs["obs_id"], bk, vehicle)
    entry = book.entry_fill(quote, vehicle=vehicle, direction=direction)
    lot_size, lot_source = lots.resolve(quote, obs)
    base = {
        "leg_id": lid,
        "obs_id": obs["obs_id"],
        "book": bk,
        "vehicle": vehicle,
        "instrument": obs["instrument"],
        "direction": direction,
        "entry_ts": float(obs["ts"]),
        "entry_price": entry["price"],
        "entry_side": entry["side"],
        "lot_size": lot_size,
        "lot_source": lot_source,
        "resolved": False,
        "evidence": entry["evidence"],
        "reason": entry["reason"],
        "cost_evidence": UNMEASURED,
    }
    if entry["price"] is None:
        return base, []

    reader = cache or path.ForwardCache()
    samples = reader.samples(
        con, symbol=quote.get("symbol"), vehicle=vehicle, direction=direction,
        after_ts=float(quote["ts"]),
    )
    # The cost is charged against the entry first so that an unresolved leg is
    # still costed: the round trip is committed the moment the position opens.
    cost = book.cost_of(
        vehicle, obs["instrument"], entry=float(entry["price"]),
        exit_price=None, lot_size=base["lot_size"],
        executable=entry["evidence"] == MEASURED_EXECUTABLE,
        lot_source=lot_source,
    )
    res = path.resolve(
        entry_price=float(entry["price"]),
        entry_ts=float(obs["ts"]),
        vehicle=vehicle,
        direction=direction,
        samples=samples,
        cost_points=cost.get("cost_points"),
    )
    base["cost_points"] = cost.get("cost_points")
    base["cost_evidence"] = cost.get("evidence") or UNMEASURED
    if not res.get("horizons"):
        base["reason"] = res.get("reason")
        return base, []

    close = res["horizons"][SESSION_CLOSE]
    base.update({
        "exit_ts": res.get("exit_ts"),
        "exit_price": res.get("exit_price"),
        "exit_side": res.get("exit_side"),
        "exit_reason": SESSION_CLOSE,
        "gross_pct": res.get("final_gross_pct"),
        "net_pct": res.get("final_net_pct"),
        "resolved": close.get("net_pct") is not None,
        "evidence": res.get("evidence") or base["evidence"],
    })
    rows = [
        {
            "leg_id": lid,
            "horizon": horizon,
            "gross_pct": snap.get("gross_pct"),
            "net_pct": snap.get("net_pct"),
            "mfe_pct": snap.get("mfe_pct"),
            "mae_pct": snap.get("mae_pct"),
            "t1": snap.get("t1"),
            "t2": snap.get("t2"),
            "t3": snap.get("t3"),
            "giveback": snap.get("giveback_pct"),
            "evidence": snap.get("evidence"),
        }
        for horizon, snap in res["horizons"].items()
    ]
    base["giveback"] = res.get("giveback")
    base["cost"] = cost
    base["targets"] = res.get("targets_pct")
    return base, rows


FLUSH_EVERY = 2000

# Observations per checkpointed chunk. A session is the largest unit whose
# arithmetic is self-contained, but it is not a small unit: the real store holds
# two sessions of ~208k observations each, so checkpointing only whole sessions
# would still put hours between save points. A chunk bounds what an interruption
# costs; it changes nothing that is measured, because a leg's forward path is
# read from the session's quotes in the database and not from the chunk.
CHUNK_OBSERVATIONS = 5000


def session_chunks(
    con, session: dict, *, chunk: int = CHUNK_OBSERVATIONS,
) -> list[dict]:
    """Cut one session into ``[start_ts, end_ts)`` windows of ~``chunk`` rows.

    Boundaries fall between distinct timestamps, never inside a group of rows
    sharing one, so every observation lands in exactly one window: two vehicles
    quoted at the same instant are built together, which §12/§13's peer
    comparison requires.
    """
    cur = con.execute(
        "SELECT ts FROM raw_observation WHERE ts >= ? AND ts < ? ORDER BY ts",
        (float(session["start_ts"]), float(session["end_ts"])),
    )
    try:
        stamps = [float(r["ts"]) for r in cur]
    finally:
        cur.close()
    if not stamps:
        return []
    out: list[dict] = []
    start = float(session["start_ts"])
    count = 0
    for i, ts in enumerate(stamps):
        count += 1
        nxt = stamps[i + 1] if i + 1 < len(stamps) else None
        if count >= chunk and nxt is not None and nxt > ts:
            out.append({"start_ts": start, "end_ts": nxt, "observations": count})
            start = nxt
            count = 0
    if count:
        out.append({
            "start_ts": start,
            "end_ts": float(session["end_ts"]),
            "observations": count,
        })
    return out


def _accumulator(payload: dict) -> dict:
    """A session's running counts, seeded from a partial checkpoint if there is one.

    A resumed session continues these numbers rather than starting them at zero,
    so what the checkpoint reports is the whole session and not the fragment this
    pass happened to build.
    """
    books = payload.get("books") or {}
    return {
        "observations": int(payload.get("observations") or 0),
        "legs_written": int(payload.get("legs_written") or 0),
        "path_rows_written": int(payload.get("path_rows_written") or 0),
        "attribution_rows_written":
            int(payload.get("attribution_rows_written") or 0),
        "not_entered_by_reason": {
            str(k): int(v)
            for k, v in (payload.get("not_entered_by_reason") or {}).items()
        },
        "books": {
            bk: {
                k: int((books.get(bk) or {}).get(k) or 0)
                for k in ("eligible", "entered", "resolved")
            }
            for bk in (CURRENT_ENGINE_PAPER, FULL_MARKET_PAPER)
        },
    }


def session_inventory(con, *, limit: int | None = None) -> list[dict]:
    """The sessions in raw, in time order, with the observation count of each.

    The session is derived from the observation timestamp rather than read from
    the ``session`` column, because that column is nullable and a row that never
    recorded one still belongs to a real IST day. Deriving it means the rebuild
    partitions the whole store with no gaps, which is what a checkpoint has to be
    able to promise.

    ``limit`` keeps the semantics it has everywhere else in this module: the first
    ``limit`` observations by timestamp, here expressed as a per-session cap so
    that the sessions it spans hold exactly those rows.
    """
    sql = "SELECT ts FROM raw_observation ORDER BY ts"
    args: list[object] = []
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    out: list[dict] = []
    index: dict[str, dict] = {}
    cur = con.execute(sql, args)
    try:
        for row in cur:
            ts = float(row["ts"])
            day = path.session_date(ts)
            entry = index.get(day)
            if entry is None:
                start, end = path.session_bounds(ts)
                entry = {"session": day, "observations": 0,
                         "start_ts": start, "end_ts": end}
                index[day] = entry
                out.append(entry)
            entry["observations"] += 1
    finally:
        cur.close()
    return out


def build(
    con,
    *,
    limit: int | None = None,
    flush_every: int = FLUSH_EVERY,
    progress=None,
    resume: bool = False,
    chunk: int = CHUNK_OBSERVATIONS,
    pause: float = 0.0,
    now: float | None = None,
) -> dict:
    """Rebuild both books over every raw observation in the store.

    Rows are written in batches rather than accumulated and written once. The
    output is identical either way — every insert is keyed by leg id and
    replaces — but a full store used to hold millions of leg and path dicts in
    memory before writing any of them, which on a small box is the whole cost of
    the pass.

    The pass is **partitioned by session, and within a session by chunk, with a
    checkpoint after each chunk**. A leg never reads a quote outside its own
    session, so a session can be rebuilt alone; within one, a chunk is a window
    of observations whose legs still read the whole session's quotes from the
    database, so cutting there changes no measured number. This exists because
    the pass is quadratic in how densely a session was sampled — two full passes
    on a real store were already lost to an interruption that cost every session.

    ``resume=True`` restarts at the first chunk not recorded as built. A session
    is only skipped whole when its checkpoint says so *and* its raw observation
    count still matches the checkpoint, so rows ingested into an already-built
    session are never silently left out of the derived tables — that session is
    rebuilt from its first chunk instead.

    ``progress`` is called with the cumulative observation count — including
    sessions and chunks skipped as already built, so the number means the same
    thing on a resumed pass as on a fresh one.

    ``pause`` sleeps that many seconds after each chunk. It exists because this
    pass shares a 8 GB WSL VM with the live capture, and a pass that saturates
    the disk makes the whole VM unresponsive — including the capture, whose
    minutes cannot be recovered. Sleeping between chunks yields the disk and lets
    the chunk's forward-path reader be released before the next one allocates.
    It makes the pass longer by construction and changes nothing it measures.
    """
    started = float(time.time() if now is None else now)
    sessions = session_inventory(con, limit=limit)
    done = store.session_progress(con) if resume else {}
    if not resume:
        store.clear_derived(con)
        store.clear_session_progress(con)
    seen = 0
    written_legs = 0
    written_paths = 0
    written_attrib = 0
    rebuilt_sessions: list[str] = []
    skipped_sessions: list[str] = []
    partial_sessions: list[str] = []
    not_entered: dict[str, int] = {}
    pending_legs: list[dict] = []
    pending_paths: list[dict] = []
    stats = {
        CURRENT_ENGINE_PAPER: {"eligible": 0, "entered": 0, "resolved": 0},
        FULL_MARKET_PAPER: {"eligible": 0, "entered": 0, "resolved": 0},
    }

    def flush(acc: dict) -> None:
        """Write the pending rows and count them against this session."""
        if not pending_legs and not pending_paths:
            return
        acc["legs_written"] += store.save_legs(con, pending_legs)
        acc["path_rows_written"] += store.save_leg_paths(con, pending_paths)
        # Attribution is only written for legs that actually got an executable
        # entry. Grading a leg that never opened would fill the histogram with
        # rows whose only content is "we had no book", and §10 is about causes,
        # not coverage — coverage is reported here instead, by reason.
        entered = [
            leg for leg in pending_legs if leg.get("entry_price") is not None
        ]
        acc["attribution_rows_written"] += store.save_attributions(
            con, attrib.rows(entered)
        )
        for leg in pending_legs:
            if leg.get("entry_price") is None:
                key = str(leg.get("reason") or UNMEASURED)
                acc["not_entered_by_reason"][key] = (
                    acc["not_entered_by_reason"].get(key, 0) + 1
                )
        pending_legs.clear()
        pending_paths.clear()

    def merge(payload: dict) -> None:
        """Fold an already-built session's counts into this pass's totals."""
        nonlocal written_legs, written_paths, written_attrib
        written_legs += int(payload.get("legs_written") or 0)
        written_paths += int(payload.get("path_rows_written") or 0)
        written_attrib += int(payload.get("attribution_rows_written") or 0)
        for reason, n in (payload.get("not_entered_by_reason") or {}).items():
            not_entered[str(reason)] = not_entered.get(str(reason), 0) + int(n)
        for bk, book_stats in (payload.get("books") or {}).items():
            if bk in stats:
                for k in ("eligible", "entered", "resolved"):
                    stats[bk][k] += int(book_stats.get(k) or 0)

    for sess in sessions:
        prior = done.get(sess["session"]) if resume else None
        payload = (prior or {}).get("payload") or {}
        # A checkpoint is believed only while the session's raw row count is the
        # one it recorded. If rows were ingested into the session afterwards the
        # session is rebuilt from its first chunk, because a partial checkpoint
        # cannot say which side of its watermark the new rows fell on.
        usable = bool(prior) and int(
            prior.get("observations") or -1
        ) == sess["observations"]
        if (
            usable
            and prior.get("stage") in (store.STAGE_PAPER_DONE,
                                       store.STAGE_VEHICLE_DONE)
        ):
            # Already derived, and raw has not grown under it. Its measured
            # counts are merged from the checkpoint so a resumed pass reports
            # totals over the store rather than over this pass.
            skipped_sessions.append(sess["session"])
            seen += sess["observations"]
            merge(payload)
            if progress is not None:
                progress(seen)
            continue

        resume_from: float | None = None
        if usable and prior.get("stage") == store.STAGE_PAPER_PARTIAL:
            # Pick up at the watermark. The chunks below it are written and
            # committed, and their counts are carried in the checkpoint rather
            # than recomputed, so the totals still describe the whole session.
            resume_from = float(payload.get("next_ts") or sess["start_ts"])
            acc = _accumulator(payload)
            seen += acc["observations"]
            partial_sessions.append(sess["session"])
        else:
            # Rebuilt from the top, so the session's earlier derived rows go
            # first. Without this a partial session left by an interruption would
            # be topped up rather than replaced, and a leg whose inputs changed
            # would keep its old row.
            store.clear_derived_window(con, sess["start_ts"], sess["end_ts"])
            acc = _accumulator({})
        remaining = (
            sess["observations"] - acc["observations"] if limit else None
        )
        for ck in session_chunks(con, sess, chunk=chunk):
            if resume_from is not None and ck["end_ts"] <= resume_from:
                continue
            if remaining is not None and remaining <= 0:
                break
            # One forward-path reader per chunk: legs on the same contract share
            # one read of that contract's session instead of one read each. What
            # it reads is the session's quotes in the database, not the chunk, so
            # a chunk boundary cannot shorten a leg's forward path.
            cache = path.ForwardCache()
            for obs in store.iter_observations(
                con, start_ts=ck["start_ts"], end_ts=ck["end_ts"],
                # Only binds on the last chunk a ``limit`` reaches into: the
                # inventory already counted how many rows are inside the limit
                # and the order is the same, so these are exactly the rows an
                # unpartitioned limited pass would have read.
                limit=min(remaining, ck["observations"])
                if remaining is not None else None,
            ):
                quotes = {
                    q["vehicle"]: q for q in store.quotes(con, obs["obs_id"])
                }
                built: dict[str, dict] = {}
                for bk in (CURRENT_ENGINE_PAPER, FULL_MARKET_PAPER):
                    if bk == CURRENT_ENGINE_PAPER:
                        if not obs.get("engine_selected"):
                            continue
                        want = (
                            [obs.get("selected_vehicle")]
                            if obs.get("selected_vehicle") else []
                        )
                    else:
                        want = list(VEHICLE_ORDER)
                    for vehicle in want:
                        quote = quotes.get(vehicle)
                        if quote is None:
                            continue
                        acc["books"][bk]["eligible"] += 1
                        leg, rows = build_leg(
                            con, obs, quote, bk=bk, cache=cache
                        )
                        pending_legs.append(leg)
                        pending_paths.extend(rows)
                        built.setdefault(f"{bk}|{vehicle}", leg)
                        if leg.get("entry_price") is not None:
                            acc["books"][bk]["entered"] += 1
                        if leg.get("resolved"):
                            acc["books"][bk]["resolved"] += 1
                # Peers are the other vehicles measured at the SAME observation,
                # which is what §12/§13 require: a comparison across timestamps
                # would be comparing two opportunities and calling one a mistake.
                for key, leg in built.items():
                    bk = key.split("|", 1)[0]
                    leg["peers"] = {
                        other.split("|", 1)[1]: built[other].get("net_pct")
                        for other in built
                        if other != key and other.startswith(f"{bk}|")
                    }
                seen += 1
                acc["observations"] += 1
                if remaining is not None:
                    remaining -= 1
                # Flushed here rather than mid-observation: peers are only
                # complete once every vehicle at this instant has been built, and
                # a leg written before its peers would be missing the comparison.
                if flush_every and len(pending_legs) >= flush_every:
                    flush(acc)
                    if progress is not None:
                        progress(seen)
            flush(acc)
            # Written after the rows and committed with them, so an interruption
            # between the two costs this chunk again rather than skipping it. The
            # watermark is the chunk's end: the first timestamp not yet built.
            store.save_session_progress(
                con, sess["session"], stage=store.STAGE_PAPER_PARTIAL,
                observations=sess["observations"],
                payload={**acc, "next_ts": ck["end_ts"]},
                now=started,
            )
            if progress is not None:
                progress(seen)
            if pause > 0:
                time.sleep(float(pause))
        flush(acc)
        store.save_session_progress(
            con, sess["session"], stage=store.STAGE_PAPER_DONE,
            observations=sess["observations"],
            payload={**acc, "next_ts": sess["end_ts"]},
            now=started,
        )
        merge(acc)
        rebuilt_sessions.append(sess["session"])
        if progress is not None:
            progress(seen)
    if progress is not None:
        progress(seen)
    return {
        "observations": seen,
        "legs_written": written_legs,
        "path_rows_written": written_paths,
        "attribution_rows_written": written_attrib,
        "not_entered_by_reason": dict(
            sorted(not_entered.items(), key=lambda kv: -kv[1])
        ),
        "books": stats,
        "sessions_rebuilt": rebuilt_sessions,
        "sessions_skipped_already_built": skipped_sessions,
        "sessions_resumed_mid_session": partial_sessions,
        "chunk_observations": int(chunk),
        "chunk_pause_seconds": float(pause),
        "resumable": True,
        "paper_only": True,
    }
