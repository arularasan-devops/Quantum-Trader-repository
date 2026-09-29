"""§15 — the machine-readable registry of imported datasets.

Append-only JSONL. One line per import attempt, including rejected ones: a
dataset that failed validation is part of the record of what was tried, and
deleting the line is how a rejected file quietly becomes a mystery six months
later.

Lookups read the whole file and **fold** the lines for each ``dataset_id``
together, a later line overriding only the fields it actually carries, so a
re-validation can append a corrected status without any line being rewritten
and without a one-line note blanking the import that established the dataset.
"""
from __future__ import annotations

import json
import os

from app.research.historical_import import REGISTRY_FILE, SCHEMA_VERSION
from app.research.historical_import import store

FIELDS = (
    "dataset_id", "source", "file_hash", "instrument", "exchange", "timeframe",
    "timezone", "start", "end", "rows", "sessions", "coverage",
    "data_quality_status", "contract_detail_status", "option_detail_status",
    "bid_ask_status", "research_eligibility", "import_timestamp",
    "schema_version",
)


def path() -> str:
    # Resolved through the module rather than a bound name, so the store stays
    # the single authority on where data lives and a test can redirect it.
    return os.path.join(store.data_dir(), REGISTRY_FILE)


def append(record: dict) -> dict:
    """Append one registry line. Never rewrites an earlier one."""
    row = {k: record.get(k) for k in FIELDS}
    row["schema_version"] = SCHEMA_VERSION
    extra = {k: v for k, v in record.items() if k not in FIELDS}
    if extra:
        row["detail"] = extra
    os.makedirs(store.data_dir(), exist_ok=True)
    with open(path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
    return row


def all_lines() -> list[dict]:
    p = path()
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
            except Exception:
                continue
    return out


def datasets() -> list[dict]:
    """Current state: the lines for each dataset folded together, in import order.

    A later line updates only the fields it actually carries. A revalidation
    note that mentions a dataset and nothing else must not blank the import
    that established it — the log is append-only, so the alternative is that a
    one-line note erases an instrument's eligibility.
    """
    latest: dict[str, dict] = {}
    for row in all_lines():
        did = row.get("dataset_id")
        if not did:
            continue
        cur = latest.setdefault(did, {})
        detail = {**(cur.get("detail") or {}), **(row.get("detail") or {})}
        cur.update({k: v for k, v in row.items() if v is not None})
        if detail:
            cur["detail"] = detail
    return list(latest.values())


def rejections() -> list[dict]:
    """Import attempts that were refused, in the order they were attempted.

    Kept visible for the same reason they are kept on disk: a file that was
    rejected is evidence about the file, and an audit that shows only successes
    invites the same broken export to be tried again next quarter.
    """
    return [row for row in all_lines() if not row.get("dataset_id")]


def by_hash(file_hash: str) -> dict | None:
    """The dataset already imported from these exact bytes, if any.

    This is the idempotency check: same bytes, same dataset, no second copy.
    """
    for row in reversed(all_lines()):
        if row.get("file_hash") == file_hash and row.get("dataset_id"):
            return row
    return None


def get(dataset_id: str) -> dict | None:
    for row in datasets():
        if row.get("dataset_id") == dataset_id:
            return row
    return None


def by_instrument() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in datasets():
        out.setdefault((row.get("instrument") or "?").upper(), []).append(row)
    return out
