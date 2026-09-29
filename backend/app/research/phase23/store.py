"""Append-only store for the hurdle shadow.

Two record kinds share one file: an ``OBS`` written when the opportunity is seen,
and a ``RES`` written when the engine's trade closed. Nothing is rewritten — a
resolution is a new line that names the observation it resolves, so a row's
history stays readable and a crash cannot leave a half-edited record.
"""
from __future__ import annotations

import json
import os
import threading

from app.config import settings

OBS = "OBS"
RES = "RES"

_LOCK = threading.Lock()


def path() -> str:
    return os.path.join(settings.data_dir, "phase23_hurdle_shadow.jsonl")


def append(record: dict) -> None:
    line = json.dumps(record, separators=(",", ":"), default=str)
    with _LOCK:
        os.makedirs(settings.data_dir, exist_ok=True)
        with open(path(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def rows() -> list[dict]:
    """Observations with their resolution merged in, oldest first.

    A malformed line is skipped rather than raising: the file is written by a
    live tick and read by a report, and a report that cannot open is worse than
    a report missing one row. The count of skipped lines travels in the result
    of :func:`health` so it cannot be silently large.
    """
    obs: dict[str, dict] = {}
    res: dict[str, dict] = {}
    bad = 0
    try:
        with open(path(), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                if not isinstance(rec, dict) or not rec.get("id"):
                    bad += 1
                    continue
                if rec.get("kind") == RES:
                    res[str(rec["id"])] = rec
                elif rec.get("kind") == OBS:
                    obs[str(rec["id"])] = rec
                else:
                    bad += 1
    except FileNotFoundError:
        return []
    out: list[dict] = []
    for row_id, row in obs.items():
        merged = dict(row)
        resolution = res.get(row_id)
        if resolution:
            merged.update({k: v for k, v in resolution.items()
                           if k not in ("kind", "id")})
            merged["resolved"] = True
        else:
            merged["resolved"] = False
        out.append(merged)
    out.sort(key=lambda r: int(r.get("ts") or 0))
    if bad:
        out.append({"kind": "MALFORMED_LINES", "count": bad, "ts": 0,
                    "resolved": False})
    return [r for r in out if r.get("kind") != "MALFORMED_LINES"] or []


def health() -> dict:
    total = 0
    bad = 0
    try:
        with open(path(), encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                total += 1
                try:
                    json.loads(line)
                except ValueError:
                    bad += 1
    except FileNotFoundError:
        pass
    return {"file": path(), "lines": total, "malformed_lines": bad}
