"""Where the opportunity package keeps its files, and how it writes them.

Every file here is append-only JSONL. A candidate's worth is its history — what
it was declared to be, when it started observing, what it produced session by
session — and a store that rewrites rows in place cannot answer any of that. It
also removes the temptation that matters most: with no update path, a
disappointing run cannot be quietly re-labelled.

Writes are atomic at the line level (one ``write`` call of one line), which is
what keeps a concurrently-reading report from seeing half a record.
"""
from __future__ import annotations

import hashlib
import json
import os

from app.research.opportunity import DATA_SUBDIR


def backend_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))


def data_dir() -> str:
    """Resolved per call, so a test can redirect the whole package by env var."""
    override = os.environ.get("QT_OPPORTUNITY_DIR")
    if override:
        return override
    return os.path.join(backend_root(), "data", DATA_SUBDIR)


def path_of(filename: str) -> str:
    return os.path.join(data_dir(), filename)


def append(filename: str, record: dict) -> dict:
    """Append one record. Never rewrites an earlier one."""
    os.makedirs(data_dir(), exist_ok=True)
    line = json.dumps(record, separators=(",", ":"), sort_keys=True, default=str)
    with open(path_of(filename), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return record


def read(filename: str) -> list[dict]:
    """Every well-formed record, in the order written.

    A malformed line is skipped rather than fatal: a torn last line from a
    killed process must not make the whole history unreadable. It is counted by
    :func:`integrity` so the loss is visible instead of silent.
    """
    p = path_of(filename)
    if not os.path.exists(p):
        return []
    out: list[dict] = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def keys(filename: str, field: str) -> set[str]:
    """Every value of ``field`` already on the record.

    Read once by a caller that is about to write many rows, so an idempotent
    writer costs one pass over the file rather than one pass per row.
    """
    return {str(r.get(field)) for r in read(filename) if r.get(field) is not None}


def append_unique(filename: str, record: dict, *, field: str,
                  seen: set[str] | None = None) -> bool:
    """Append unless ``field``'s value is already present. True when written.

    The journal must be able to be rebuilt by re-running a session without
    doubling its own evidence: two rows for one paper leg would count one
    outcome twice in every metric downstream, and with an append-only file
    there is no later pass that could remove the copy.
    """
    value = str(record.get(field))
    known = seen if seen is not None else keys(filename, field)
    if value in known:
        return False
    append(filename, record)
    known.add(value)
    return True


def integrity(filename: str) -> dict:
    """Line counts against parsed counts, so a torn write is reportable."""
    p = path_of(filename)
    if not os.path.exists(p):
        return {"file": filename, "exists": False, "lines": 0, "parsed": 0,
                "bytes": 0}
    with open(p, encoding="utf-8") as fh:
        lines = sum(1 for line in fh if line.strip())
    return {
        "file": filename,
        "exists": True,
        "lines": lines,
        "parsed": len(read(filename)),
        "bytes": os.path.getsize(p),
    }


def digest(payload: object) -> str:
    """A short stable hash of a canonical JSON form.

    Sorted keys and no whitespace, so the same content hashes identically on
    two machines and across interpreter versions — the identity of a candidate
    has to be reproducible or the frozen definitions are worthless.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
