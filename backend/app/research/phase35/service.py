"""Phase 35 orchestration — one pass from raw observations to every answer.

The order matters and is fixed: ingest raw (§21/§4) → build both paper books
(§5-§9) → attribute (§10/§18) → compare vehicles (§12/§13) → missed
opportunities (§11) → economic filter research (§19) → checkpoints (§16) →
frozen-candidate verification (§15/§17). Nothing downstream can write raw rows,
and everything downstream is rebuilt from raw on every pass, which is §21's
reproducibility requirement made operational: delete every derived table and the
same report comes back.

The read-only API and the CLI both call :func:`state`, so the dashboard cannot
show a number the CLI would not print.
"""
from __future__ import annotations

import json
import os

from app.research.phase24 import data as p24data
from app.research.phase35 import (
    CURRENT_ENGINE_PAPER,
    FULL_MARKET_PAPER,
    PAPER_ONLY,
    RESEARCH_ONLY,
    attrib,
    capture,
    filters,
    frozen,
    milestones,
    missed,
    paper,
    report,
    store,
    vehicle,
)

ARTEFACT_DIR = os.path.join("data", "phase35")


STAGES = ("RAW_INGEST", "PAPER_BOOKS", "VEHICLE_COMPARISON")


def rebuild(con, *, limit: int | None = None,
            instrument: str | None = None,
            since: str | None = None,
            resume: bool = True,
            redo: tuple[str, ...] = (),
            pause: float = 0.0,
            progress=None) -> dict:
    """Ingest what is new, then rebuild every derived table from raw.

    The raw half is the same streaming, resumable read as :func:`ingest_raw`, so
    a rebuild costs the capture files it has not read rather than the archive.

    ``instrument`` and ``since`` narrow only the *ingest* read. The derived tables
    are always rebuilt from the whole raw store, because a book that exists in raw
    must not disappear from the report for the accident of which flag this pass
    used.

    ``resume`` is the default because this pass is quadratic in how densely a
    session was sampled, so it runs for hours on a real store, and two full
    passes were already lost to an interruption that cost every session rather
    than the one in flight. With it, each session is rebuilt and checkpointed on
    its own and a re-run skips the sessions already done — unless raw has grown
    under one, which is detected by observation count and forces that session to
    be redone. ``resume=False`` clears every derived table and every checkpoint
    and starts over, which is what to use if the derived tables are suspected
    wrong rather than merely incomplete.

    ``redo`` names sessions to rebuild even though they are checkpointed as
    built. It forgets only their checkpoints, so the resumed pass drops each
    named session's derived rows and derives them again while every other
    session is left alone. It exists because a derived table can be complete and
    still not comparable: the first four gradeable sessions of a real store were
    costed two different ways — the earlier two by a modelled spread, the later
    two from a measured lot — and Phase 42's chronological cut fell between them,
    which is a difference in costing rather than in what was being tested.
    Putting the earlier sessions back through the current code is the repair, and
    ``resume=False`` would have discarded hours of correct derivation to do it.

    ``pause`` yields the machine between chunks. The box this runs on captures a
    live session on the same 8 GB VM, and a pass that saturates its disk costs
    market minutes that cannot be recaptured; a rebuild that takes longer costs
    nothing, because it is repeatable and now resumable. It is off by default.

    ``progress`` is called with ``{"stage", "done", "total"}`` as each stage
    advances. ``total`` is the denominator where one is known and ``None`` where
    it is not; a fabricated denominator would be a fabricated estimate.
    """
    def say(stage: str, done: int, total: int | None) -> None:
        if progress is not None:
            progress({"stage": stage, "done": done, "total": total})

    say(STAGES[0], 0, None)
    # The streaming reader, the same one :func:`ingest_raw` uses. The
    # materialising :func:`capture.ingest` reads every capture file in full and
    # holds every parsed row in memory before writing, which on a real archive
    # is millions of rows and gigabytes — it took the whole VM into swap and
    # froze it before the derived stages, which are the throttled ones, ever
    # ran. The raw cursor is always resumed here: it records only which bytes
    # have been read, and re-reading them cannot change the derived tables that
    # ``resume`` governs.
    ingested = capture.ingest_stream(
        con, limit=limit, instrument=instrument, since=since, resume=True,
        progress=lambda ev: say(
            STAGES[0], int(ev.get("observations_written") or 0), None
        ),
    )
    say(STAGES[0], int(ingested.get("observations_written") or 0), None)

    forgotten = {
        session: store.clear_session_progress(con, session)
        for session in dict.fromkeys(redo)
    } if resume else {}

    total = store.counts(con).get("raw_observation")
    say(STAGES[1], 0, total)
    built = paper.build(
        con, resume=resume, pause=pause,
        progress=lambda seen: say(STAGES[1], seen, total),
    )
    say(STAGES[2], 0, None)
    compared = vehicle.build(
        con, resume=resume, pause=pause,
        progress=lambda done, tot: say(STAGES[2], done, tot),
    )
    return {
        "ingested": ingested,
        "paper": built,
        "vehicle_comparisons": compared,
        "resumed": resume,
        "checkpoints_forgotten": forgotten,
        "pause_seconds": float(pause),
        "counts": store.counts(con),
        "paper_only": PAPER_ONLY,
    }


def ingest_raw(con, *, limit: int | None = None,
               instrument: str | None = None,
               since: str | None = None,
               resume: bool = True,
               progress=None) -> dict:
    """Append raw observations and quotes only, leaving derived tables alone.

    Phases 39, 40 and 41 read ``raw_quote`` and ``raw_observation`` and nothing
    else, so a diagnostic re-run after a session needs this and not
    :func:`rebuild`. It is separated because the derived rebuild walks the whole
    raw store while this walks only what the capture newly wrote, and because
    ``clear_derived`` empties tables a Phase 35 report still needs — an
    interrupted rebuild used to leave them that way.

    The read is :func:`capture.ingest_stream`, which resumes from a recorded byte
    offset per capture file. That is what keeps this pass proportional to the day
    rather than to the archive: the rolled files were read once and are not read
    again. ``resume=False`` forces a full rescan, which is only needed if the
    offsets are suspected wrong — re-reading is harmless either way, since raw ids
    are content-addressed.
    """
    ingested = capture.ingest_stream(
        con, limit=limit, instrument=instrument, since=since,
        resume=resume, progress=progress,
    )
    return {
        "ingested": ingested,
        "derived": "NOT_REBUILT_THIS_PASS",
        "reads_raw_only": ("PHASE39", "PHASE40", "PHASE41"),
        "needs_rebuild": ("PHASE35_REPORT",),
        "counts": store.counts(con),
        "paper_only": PAPER_ONLY,
    }


def stagea_artefact(*, out_dir: str | None = None) -> dict:
    """The last written Stage A table, or why there is none.

    Stage A grinds 1.4m bars and takes minutes; the API reads what the CLI wrote
    rather than starting a study that would starve the engine.
    """
    p = os.path.join(artefact_dir(out_dir), "phase35_stagea.json")
    try:
        with open(p, encoding="utf-8") as fh:
            body = json.load(fh)
    except (OSError, ValueError):
        return {
            "stage": "A_HISTORICAL_DISCOVERY",
            "available": False,
            "reason": "no Stage A artefact written yet",
            "research_only": RESEARCH_ONLY,
        }
    if not isinstance(body, dict):
        return {
            "stage": "A_HISTORICAL_DISCOVERY",
            "available": False,
            "reason": "artefact is not a Stage A payload",
            "research_only": RESEARCH_ONLY,
        }
    body["available"] = True
    return body


def state(con, *, filters_in: dict | None = None) -> dict:
    """Everything the OPPORTUNITY PAPER section and the CLI report read.

    Every section below is an aggregate query rather than a table read. It used
    to read ``leg_attribution``, ``leg_path``, ``raw_quote`` and both paper books
    into memory at once, which is fine on a fixture and fatal on a real store —
    at 2m legs and 6.7m path rows it took the machine into swap and killed it
    before the report printed. The figures are unchanged.
    """
    base = {
        "coverage": capture.coverage(con),
        "books": {
            book: milestones.aggregate(con, book=book, filters=filters_in)
            for book in (CURRENT_ENGINE_PAPER, FULL_MARKET_PAPER)
        },
        "attribution": attrib.histogram_from_store(con),
        "vehicle": vehicle.summary(con),
        "missed": missed.summary(con),
        "economics": filters.study(con, book=FULL_MARKET_PAPER),
        "checkpoints": milestones.checkpoints(con),
        "frozen": frozen.verify(os.path.dirname(store.db_path())),
        "research_only": RESEARCH_ONLY,
        "paper_only": PAPER_ONLY,
        "production_changed": False,
    }
    # The verdict is derived from the same dict the panel renders, so a section
    # cannot show a number the headline disagrees with.
    base["report"] = report.summary(base)
    return base


def panel(*, filters_in: dict | None = None) -> dict:
    """§20 OPPORTUNITY PAPER payload, read-only, on its own connection."""
    con = store.connect()
    try:
        return state(con, filters_in=filters_in)
    finally:
        con.close()


def artefact_dir(out_dir: str | None = None) -> str:
    target = out_dir or p24data._resolve(ARTEFACT_DIR)
    os.makedirs(target, exist_ok=True)
    return target


def write_artefacts(payload: dict, *, out_dir: str | None = None) -> list[str]:
    """One JSON per section, so a claim can always be traced to its numbers."""
    target = artefact_dir(out_dir)
    written: list[str] = []
    for name, body in payload.items():
        if not isinstance(body, dict):
            continue
        p = os.path.join(target, f"phase35_{name}.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(body, fh, indent=2, default=str, sort_keys=True)
        written.append(p)
    return written
