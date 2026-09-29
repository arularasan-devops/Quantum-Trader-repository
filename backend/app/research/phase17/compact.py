"""Store rolled journal files gzipped, losing no row — storage only.

The field deployment holds ~28 GB of Phase 17 journals and grows ~1.5 GB a day.
A full disk stops capture, and a stopped capture costs sessions exactly the way
the missing snapshots did, so the space has to come from somewhere. It does NOT
come from deleting observations, and it does not come from de-duplicating them
either: 52% of the rows on 2026-09-15 re-recorded an unchanged book, but "this
book was still unchanged at 14:31" is itself an observation, and dropping it
would be an edit to raw evidence made for the convenience of a disk.

So this compresses instead. Every row survives, in the same order, in a file
whose name keeps the same roll stamp; readers go through
:func:`store.open_series` and cannot tell the difference. Repetitive JSON
gzips around 10-20x, which is more space than de-duplication would have freed
anyway.

Three rules the implementation keeps, in this order:

1. **The live file is never touched.** Only rolled files, which nothing appends
   to again. Compressing a file under an open append handle would lose the
   rows written after the copy.
2. **Verify before removing.** The gzip is written to a temporary name,
   fsynced, then read back and compared line for line with the original. The
   original is unlinked only if every line matches. A failed verification
   leaves both files and reports it.
3. **Dry run by default.** :func:`plan` says what would be compressed and what
   it would save; :func:`run` needs ``apply=True``.

Research-only and paper-only: no signal, no order path, no broker.
"""
from __future__ import annotations

import gzip
import os
import shutil

from app.research.phase17 import store

# Files below this are not worth a rewrite: the saving is noise and each rewrite
# is a chance to get something wrong.
MIN_BYTES = 1 * 1024 * 1024

ALL_SERIES = (
    store.OBSERVATIONS,
    store.LEGS,
    store.PAPER,
    store.COVERAGE,
    store.GAPS,
    store.LIVENESS,
    store.PROCESS,
)

SKIP_LIVE = "LIVE_FILE_STILL_BEING_APPENDED_TO_NEVER_COMPRESSED"
SKIP_SMALL = "BELOW_MIN_BYTES_NOT_WORTH_A_REWRITE"
SKIP_DONE = "ALREADY_COMPRESSED"
COMPRESSED = "COMPRESSED_EVERY_LINE_VERIFIED_ORIGINAL_REMOVED"
FAILED = "VERIFY_FAILED_BOTH_FILES_LEFT_IN_PLACE_NOTHING_REMOVED"


def _candidates(names: tuple[str, ...]) -> list[dict]:
    out: list[dict] = []
    for name in names:
        series = store.series_paths(name)
        live = series[-1] if series else None
        for p in series:
            if not os.path.exists(p):
                continue
            size = os.path.getsize(p)
            if p == live:
                skip = SKIP_LIVE
            elif p.endswith(store.GZ):
                skip = SKIP_DONE
            elif size < MIN_BYTES:
                skip = SKIP_SMALL
            else:
                skip = None
            out.append({
                "series": name,
                "path": p,
                "bytes": size,
                "eligible": skip is None,
                "skipped_because": skip,
            })
    return out


def plan(*, names: tuple[str, ...] = ALL_SERIES) -> dict:
    """What compaction would do. Reads sizes only; writes nothing."""
    rows = _candidates(names)
    eligible = [r for r in rows if r["eligible"]]
    return {
        "phase": "PHASE17_JOURNAL_COMPACTION",
        "files_seen": len(rows),
        "files_eligible": len(eligible),
        "bytes_eligible": sum(r["bytes"] for r in eligible),
        "bytes_total": sum(r["bytes"] for r in rows),
        "files": rows,
        "applied": False,
        "rows_lost": 0,
        "note": (
            "Compression only. No row is removed, de-duplicated, reordered or "
            "rewritten, and the live file is never touched."
        ),
        "research_only": True,
        "paper_only": True,
    }


def _verify(original: str, packed: str) -> tuple[bool, int]:
    """Every line of ``original``, in order, present in ``packed``."""
    lines = 0
    with open(original, "rb") as raw, gzip.open(packed, "rb") as out:
        while True:
            a = raw.readline()
            b = out.readline()
            if a != b:
                return False, lines
            if not a:
                return True, lines
            lines += 1


def _compress_one(p: str) -> dict:
    packed = p + store.GZ
    partial = packed + ".partial"
    try:
        with open(p, "rb") as raw, gzip.open(partial, "wb") as out:
            shutil.copyfileobj(raw, out, length=4 * 1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        ok, lines = _verify(p, partial)
    except OSError as exc:
        if os.path.exists(partial):
            os.unlink(partial)
        return {"path": p, "status": FAILED, "error": f"{type(exc).__name__}: {exc}"}

    if not ok:
        # Leave the original untouched. A saving is never worth an unverified
        # copy of the evidence.
        os.unlink(partial)
        return {"path": p, "status": FAILED, "lines_verified": lines}

    before = os.path.getsize(p)
    os.rename(partial, packed)
    os.unlink(p)
    return {
        "path": p,
        "packed": packed,
        "status": COMPRESSED,
        "lines_verified": lines,
        "bytes_before": before,
        "bytes_after": os.path.getsize(packed),
    }


def run(*, names: tuple[str, ...] = ALL_SERIES, apply: bool = False) -> dict:
    """Compress the eligible rolled files. ``apply=False`` only plans."""
    state = plan(names=names)
    if not apply:
        return state

    done: list[dict] = []
    for row in state["files"]:
        if not row["eligible"]:
            continue
        done.append(_compress_one(row["path"]))

    ok = [d for d in done if d["status"] == COMPRESSED]
    state["applied"] = True
    state["results"] = done
    state["files_compressed"] = len(ok)
    state["files_failed"] = len(done) - len(ok)
    state["bytes_before"] = sum(d["bytes_before"] for d in ok)
    state["bytes_after"] = sum(d["bytes_after"] for d in ok)
    state["bytes_saved"] = state["bytes_before"] - state["bytes_after"]
    state["lines_verified"] = sum(d["lines_verified"] for d in ok)
    store.drop_cache()
    return state


def _gb(n: int) -> str:
    return f"{n / (1024 ** 3):.2f} GB"


def render(state: dict) -> str:
    lines = [
        "PHASE 17 JOURNAL COMPACTION — "
        + ("APPLIED" if state["applied"] else "DRY RUN, NOTHING WRITTEN"),
    ]
    if state["applied"]:
        lines.append(
            f"  {state['files_compressed']} file(s) compressed, "
            f"{state['lines_verified']} lines verified line-for-line"
        )
        lines.append(
            f"  {_gb(state['bytes_before'])} → {_gb(state['bytes_after'])}, "
            f"saved {_gb(state['bytes_saved'])}"
        )
        if state["files_failed"]:
            lines.append(
                f"  {state['files_failed']} FAILED verification — original and "
                f"gzip both left in place, nothing removed"
            )
    else:
        lines.append(
            f"  {state['files_eligible']} of {state['files_seen']} file(s) "
            f"eligible, {_gb(state['bytes_eligible'])} would be rewritten"
        )
        lines.append("  re-run with --apply to compress")
    lines.append(f"  {state['note']}")
    return "\n".join(lines)
