"""Which past sessions left durable evidence on disk — counted separately.

The snapshot count is small because the snapshot depended on someone running a
command at night. The *underlying* evidence is not small: the Phase 17 journals
are append-only, carry the session on every row, and were never overwritten. So a
day with no snapshot may still be a day with thousands of timestamped
observations, resolved paper legs and coverage lines.

This module counts that, and the whole design of it is the separation:

**It is not merged into ``sessions_complete``.** The floors — 20 sessions to
look, 50 and 100 trades to speak, 60 and 200 for a relative-value claim — were
pre-registered against the snapshot artefact before any of this data existed.
Re-defining what counts as a session *after* seeing that the count is
inconvenient is exactly how a bar stops meaning anything, and the fact that the
new definition is defensible does not make the re-definition honest. So this
number sits beside the snapshot count under its own name, and no status or
verdict anywhere reads it.

**It is weaker in one specific way and stronger in another.** Stronger: these are
the raw rows, not a screen's summary of them. Weaker: a snapshot preserved what
five different tabs *said*, including derived state — readiness blocking reasons,
the CAS verdict, feed health at that moment — and those are not recoverable from
the journals, because the tabs recompute them from live state that is gone. So a
reconstructed session is genuinely not the same object, which is the other reason
it is not allowed to impersonate one.

What it is good for: knowing how much real evidence the last month actually holds
before deciding whether to keep waiting.

Read-only, and streaming. The first version of this asked the store for every
row and counted the sessions it got back, which was fine on a test tree and
indefensible in the field: the observation series is 28 GB over sixty rolled
files, one row is about 12 KB, and materialising that as dicts would take the
machine down. Nothing here needs the rows — only which day each line belongs to
— so the scan reads line by line, keeps one integer per session, and holds no
row at all.
"""
from __future__ import annotations

import datetime as dt
import os
import re

from app.research.phase17 import store as p17store

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# The journals worth counting, and what a row in each one is evidence of.
SOURCES: tuple[dict, ...] = (
    {
        "journal": p17store.OBSERVATIONS,
        "key": "observations",
        "is": "a candidate measured at an exact tick with its book",
    },
    {
        "journal": p17store.PAPER,
        "key": "paper_legs",
        "is": "a paper leg priced ask-in/bid-out with measured costs",
    },
    {
        "journal": p17store.LEGS,
        "key": "tracked_legs",
        "is": "a forward path walked for MFE/MAE and giveback",
    },
)

# A session is only interesting here if it holds at least one measured
# observation. Coverage and liveness lines alone mean the capture ran, which the
# capture-health report already says better.
MIN_OBSERVATIONS = 1

NOT_A_SNAPSHOT = (
    "DURABLE_JOURNAL_EVIDENCE_NOT_A_TAB_SNAPSHOT_AND_NOT_COUNTED_TOWARD_ANY_FLOOR"
)

# The session, pulled straight out of the raw line. A row is ~12 KB of option
# ladder and this needs eleven characters of it, so json.loads on every line
# would spend the whole scan building objects nothing reads.
_SESSION_RE = re.compile(rb'"session"\s*:\s*"(\d{4}-\d{2}-\d{2})"')

# Not every journal writes a session field. A settled paper leg carries the
# instants it was priced at and nothing else, so the day has to come from one of
# them — the entry, because that is when the decision was made. Counting these as
# unattributable is how the first run reported zero paper legs beside 317 KB of
# them on disk.
_TS_KEYS = (b"ts", b"entry_ts", b"signal_ts", b"exit_ts")
_TS_RES = tuple(
    re.compile(rb'"' + key + rb'"\s*:\s*(\d+(?:\.\d+)?)') for key in _TS_KEYS
)

# How much of a line can hold one of those keys. They are written near the front
# of every row, so the tail of a 12 KB ladder is not worth searching.
_HEAD_BYTES = 4096


def _session_of_line(line: bytes) -> str | None:
    """The session a raw journal line belongs to: its field, else its IST ts."""
    head = line[:_HEAD_BYTES]
    hit = _SESSION_RE.search(head)
    if hit is not None:
        return hit.group(1).decode("ascii")
    for pattern in _TS_RES:
        hit = pattern.search(head)
        if hit is not None:
            return dt.datetime.fromtimestamp(
                float(hit.group(1)), _IST).strftime("%Y-%m-%d")
    return None


def tally(*, max_bytes: int | None = None) -> dict:
    """Per-session counts from the append-only journals, scanned in a stream.

    The whole series is read by default, rolled files included: a count that
    silently stopped at the cached tail would under-report exactly the older days
    this exists to make visible. ``max_bytes`` caps the newest bytes scanned per
    journal for a quick look, and a capped scan says so in ``complete``.
    """
    per_session: dict[str, dict] = {}
    read: dict[str, int] = {}
    scanned: dict[str, int] = {}
    unattributed: dict[str, int] = {}
    complete = True
    for spec in SOURCES:
        lines = 0
        skipped = 0
        seen = 0
        paths = p17store.series_paths(spec["journal"])
        if max_bytes is not None:
            # Newest first, so a capped scan keeps the recent days rather than
            # the oldest ones.
            budget = max_bytes
            kept: list[str] = []
            for q in reversed(paths):
                if budget <= 0:
                    complete = False
                    break
                kept.append(q)
                try:
                    budget -= os.path.getsize(q)
                except OSError:
                    continue
            paths = list(reversed(kept))
        for q in paths:
            try:
                with p17store.open_series(q) as fh:
                    for line in fh:
                        seen += len(line)
                        session = _session_of_line(line)
                        if session is None:
                            if line.strip():
                                skipped += 1
                            continue
                        lines += 1
                        bucket = per_session.setdefault(
                            session, {s["key"]: 0 for s in SOURCES})
                        bucket[spec["key"]] += 1
            except OSError:
                continue
        read[spec["key"]] = lines
        scanned[spec["key"]] = seen
        unattributed[spec["key"]] = skipped

    sessions = [
        {"session": day, **counts}
        for day, counts in sorted(per_session.items())
    ]
    evidenced = [
        s for s in sessions if s["observations"] >= MIN_OBSERVATIONS
    ]
    return {
        "phase": "PHASE37_DURABLE_EVIDENCE",
        "what_a_row_is": {s["key"]: s["is"] for s in SOURCES},
        "rows_read": read,
        "bytes_scanned": scanned,
        # A row whose day cannot be established. Reported rather than dropped:
        # a silent zero beside a journal full of rows is what hid the paper legs.
        "rows_with_no_session": unattributed,
        "complete": complete,
        "sessions": sessions,
        "sessions_with_observations": len(evidenced),
        "first_session": evidenced[0]["session"] if evidenced else None,
        "last_session": evidenced[-1]["session"] if evidenced else None,
        "total_observations": sum(s["observations"] for s in sessions),
        "total_paper_legs": sum(s["paper_legs"] for s in sessions),
        "counts_toward_floors": False,
        "why_not": NOT_A_SNAPSHOT,
        "research_only": True,
        "paper_only": True,
        "note": (
            "Days with real journal evidence but no tab snapshot. Reported so "
            "the evidence on disk is visible; not countable as a session, "
            "because the floors were pre-registered against the snapshot and "
            "the derived tab state for those days no longer exists."
        ),
    }


def headline(state: dict) -> str:
    snap = state.get("snapshot_sessions_complete")
    lines = [
        "PHASE 37 DURABLE EVIDENCE — "
        f"{state['sessions_with_observations']} sessions hold measured "
        "observations on disk",
        f"  span {state['first_session']} to {state['last_session']}"
        f"  observations {state['total_observations']}"
        f"  paper legs {state['total_paper_legs']}",
    ]
    if snap is not None:
        lines.append(
            f"  countable snapshot sessions: {snap} — the number above does NOT "
            "raise it")
    if not state.get("complete", True):
        lines.append(
            "  SCAN WAS CAPPED — older days are missing from this tally, so it"
            " is a floor on the evidence, not a count of it")
    lines.append(f"  {state['why_not']}")
    return "\n".join(lines)
