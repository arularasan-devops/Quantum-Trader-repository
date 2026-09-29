"""§3/§7 — where a normalised dataset lives, and why it cannot be written twice.

Layout, one directory per dataset:

    data/historical_import/<dataset_id>/bars.jsonl      normalised rows
    data/historical_import/<dataset_id>/dataset.json    provenance + audit
    data/historical_import/registry.jsonl               append-only index

Three properties are load-bearing:

* the operator's CSV is **never** written to, moved or renamed. This package
  opens it read-only and stores its SHA-256, so the bytes a number came from
  can be re-hashed years later and shown to be the same bytes;
* a dataset directory is written **once**, atomically, via a temporary file and
  a rename. A half-written bars file that a study reads as a short series is
  worse than no file;
* the registry is **append-only**. A re-import does not rewrite the earlier
  entry, so the history of what was imported when survives — including a
  dataset later marked rejected.

The normalised row carries only what the source carried. There is no default
bid, ask, size or spread anywhere in this module, by construction: the writer
copies a fixed field list and every executable field is absent unless the
source genuinely had it.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile

from app.research.historical_import import (
    BARS_FILE,
    DATA_DIR,
    EXECUTABLE_FIELDS,
    MANIFEST_FILE,
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
    SCHEMA_VERSION,
)

_CHUNK = 1 << 20


def root() -> str:
    """Backend directory, so the CLI works from any working directory."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))


def data_dir() -> str:
    return os.path.join(root(), DATA_DIR)


def dataset_dir(dataset_id: str) -> str:
    return os.path.join(data_dir(), dataset_id)


def file_hash(path: str) -> str:
    """SHA-256 of the source file, read in chunks and never held in memory."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(_CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def fingerprint(rows: list[dict], identity: dict) -> str:
    """A fingerprint of the normalised dataset, not of the source bytes.

    Two different exports of the same series should fingerprint the same, and a
    single changed bar should change it — so it hashes the normalised rows plus
    the identity that decides how they are read (instrument, timeframe,
    timezone treatment, contract). The schema version is included because the
    same rows read under a different normalisation are a different dataset.
    """
    h = hashlib.sha256()
    h.update(SCHEMA_VERSION.encode())
    for key in sorted(identity):
        h.update(f"|{key}={identity[key]}".encode())
    for r in rows:
        h.update(
            f"\n{r['time']}|{r['open']:.6f}|{r['high']:.6f}|"
            f"{r['low']:.6f}|{r['close']:.6f}|{r.get('volume', '')}".encode()
        )
    return h.hexdigest()


def dataset_id(instrument: str, timeframe: int, source: str,
               src_hash: str) -> str:
    """Stable, readable, and unique to the bytes it came from."""
    safe = "".join(
        ch if ch.isalnum() else "_" for ch in (source or "unknown")
    ).strip("_").lower() or "unknown"
    return f"{instrument.upper()}_{int(timeframe)}M_{safe}_{src_hash[:8]}"


def _row_out(r: dict) -> dict:
    """The row as stored: required fields, then only what the source carried.

    ``bid``/``ask``/sizes/spread pass through **only** when present in the
    parsed row, which only happens when the CSV had those columns. Nothing here
    computes one.
    """
    out = {k: r[k] for k in REQUIRED_FIELDS}
    for k in OPTIONAL_FIELDS:
        if r.get(k) is not None:
            out[k] = r[k]
    for k in EXECUTABLE_FIELDS:
        if r.get(k) is not None:
            out[k] = r[k]
    for k in ("symbol", "contract", "expiry", "strike", "option_type"):
        if r.get(k) not in (None, ""):
            out[k] = r[k]
    return out


def write_dataset(dataset_id_: str, rows: list[dict], manifest: dict) -> dict:
    """Write bars and manifest atomically. Returns paths."""
    d = dataset_dir(dataset_id_)
    os.makedirs(d, exist_ok=True)
    bars_path = os.path.join(d, BARS_FILE)
    man_path = os.path.join(d, MANIFEST_FILE)
    _atomic(bars_path, "".join(
        json.dumps(_row_out(r), separators=(",", ":")) + "\n" for r in rows
    ))
    _atomic(man_path, json.dumps(manifest, indent=2, default=str) + "\n")
    return {"dir": d, "bars": bars_path, "manifest": man_path}


def _atomic(path: str, text: str) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_manifest(dataset_id_: str) -> dict | None:
    path = os.path.join(dataset_dir(dataset_id_), MANIFEST_FILE)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_bars(dataset_id_: str) -> list[dict]:
    """Normalised rows, in file order. Nothing is filled on the way out either."""
    path = os.path.join(dataset_dir(dataset_id_), BARS_FILE)
    if not os.path.exists(path):
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out
