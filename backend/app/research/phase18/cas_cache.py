"""Serve the CAS report from a cache that is bound to the journals it was built
from, and rebuild it off the request path.

Why this exists: ``/api/cas-report`` rebuilds every CAS payload from the whole
observation, paper and coverage journals on each request, so its response time is
a property of the store rather than of the route — measured at ~30s on
2026-09-15, when it timed out and cost that session its CAS tab, and ~56s a day
later. The 240s tab budget bought room; it did not stop the number growing, and
the failure it postpones is silent: a session simply does not count.

The rebuild is unchanged. What changes is *when* it runs. The payload is written
once per state of the journals, fingerprinted against them, and the nightly
snapshot reads the finished answer instead of racing it.

Two refusals hold the honesty of that:

**A cache hit requires an exact fingerprint match**, on every source journal:
size and mtime, with an absent file recorded as absent rather than as empty. Any
difference rebuilds. A CAS report describing yesterday's journals while presented
as today's would be a fabricated observation — the one thing this platform may
never do — so the fingerprint is compared whole and a mismatch is never served.

**The served payload says where it came from.** ``built_at``, how long the
rebuild took, and whether this response was a hit or a rebuild are carried in the
payload, so a stale-looking figure can always be traced to the state of the store
that produced it rather than guessed at.

Read-only research bookkeeping: it reads three append-only journals, writes one
cache file, and touches no capture path, no signal and no order path.
"""
from __future__ import annotations

import json
import os
import time

from app.config import settings
from app.research.phase18 import reports, store

CACHE_FILE = "phase18_cas_report_cache.json"

# The journals the report is built from. If a payload ever starts depending on a
# fourth store, it belongs here too: a fingerprint that omits an input cannot
# detect that input changing, and would serve a stale report as a current one.
SOURCES: tuple[str, ...] = (store.OBSERVATIONS, store.PAPER, store.COVERAGE)

HIT = "SERVED_FROM_CACHE_THE_JOURNAL_FINGERPRINT_MATCHES"
REBUILT_STALE = "REBUILT_THE_JOURNALS_CHANGED_SINCE_THE_CACHE_WAS_WRITTEN"
REBUILT_ABSENT = "REBUILT_NO_CACHE_EXISTED_FOR_THIS_STATE_OF_THE_JOURNALS"

ABSENT = "ABSENT"


def path() -> str:
    return os.path.join(settings.data_dir, CACHE_FILE)


def fingerprint() -> dict[str, dict]:
    """What the source journals look like right now.

    Size and mtime, per file. An append-only journal cannot change without its
    size changing, and a file that does not exist is recorded as ABSENT — not as
    a zero-length one, because "no journal" and "an empty journal" are different
    states of evidence and a cache built under one must not be served under the
    other.
    """
    out: dict[str, dict] = {}
    for name in SOURCES:
        p = store.path(name)
        try:
            st = os.stat(p)
        except OSError:
            out[name] = {"state": ABSENT}
            continue
        out[name] = {
            "state": "PRESENT",
            "bytes": int(st.st_size),
            "mtime": round(float(st.st_mtime), 3),
        }
    return out


def _payloads() -> dict:
    """The CAS report itself. Identical inputs and calls to the endpoint's."""
    payloads = reports.build_payloads(
        observations=store.observations(),
        paper_rows=store.paper(),
        coverage=store.coverage(),
    )
    return {
        "daily": payloads["cas_daily_report"],
        "strategy": payloads["cas_strategy_report"],
        "capture": payloads["phase18_cas_capture"],
        "paper": payloads["phase18_cas_paper"],
        "validation": payloads["phase18_cas_validation"],
        "questions": reports.answers(payloads),
        "paper_only": True,
        "no_real_order": True,
    }


def build() -> dict:
    """Rebuild the report and describe what it was built from."""
    before = fingerprint()
    started = time.time()
    body = _payloads()
    took = time.time() - started
    after = fingerprint()
    return {
        "built_at": started,
        "build_sec": round(took, 3),
        # The state the journals were in when the build STARTED is the state the
        # payload describes. A write that landed mid-build is recorded so the
        # next read rebuilds rather than treating this payload as current for a
        # store it only partly saw.
        "fingerprint": before,
        "changed_during_build": after != before,
        "report": body,
    }


def save(entry: dict) -> bool:
    """Write the cache atomically. A failed write is not an error to the caller.

    The endpoint's answer does not depend on the cache existing, so a store that
    cannot be written costs speed and nothing else — it must not cost the report.
    """
    if entry.get("changed_during_build"):
        return False
    tmp = path() + ".tmp"
    try:
        os.makedirs(settings.data_dir, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(entry, fh, default=str)
        os.replace(tmp, path())
    except OSError:
        return False
    return True


def load() -> dict | None:
    """The cached entry, or None when there isn't a readable one."""
    try:
        with open(path(), encoding="utf-8") as fh:
            entry = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get("report"), dict):
        return None
    return entry


def fresh() -> dict | None:
    """The cached entry only if it matches the journals as they are now."""
    entry = load()
    if entry is None:
        return None
    if entry.get("fingerprint") != fingerprint():
        return None
    return entry


def ensure() -> dict:
    """Make the cache current, rebuilding only if it isn't. Off the request path.

    Returns the bookkeeping, not the report: callers that want the payload go
    through ``report()``.
    """
    entry = fresh()
    if entry is not None:
        return {
            "state": HIT,
            "built_at": entry.get("built_at"),
            "build_sec": entry.get("build_sec"),
            "rebuilt": False,
        }
    had = load() is not None
    entry = build()
    written = save(entry)
    return {
        "state": REBUILT_STALE if had else REBUILT_ABSENT,
        "built_at": entry.get("built_at"),
        "build_sec": entry.get("build_sec"),
        "rebuilt": True,
        "written": written,
        "changed_during_build": entry.get("changed_during_build"),
    }


def report() -> dict:
    """The CAS report, from the cache when it is current and a rebuild when not.

    The payload carries a ``cache`` block naming which of the two happened, when
    the numbers were computed and what that cost, so the age of the answer is
    always readable from the answer.
    """
    entry = fresh()
    served = HIT
    if entry is None:
        had = load() is not None
        entry = build()
        save(entry)
        served = REBUILT_STALE if had else REBUILT_ABSENT

    body = dict(entry["report"])
    body["cache"] = {
        "served": served,
        "built_at": entry.get("built_at"),
        "built_at_iso": (
            time.strftime(
                "%Y-%m-%dT%H:%M:%S",
                time.localtime(float(entry["built_at"])),
            ) if isinstance(entry.get("built_at"), (int, float)) else None
        ),
        "build_sec": entry.get("build_sec"),
        "fingerprint": entry.get("fingerprint"),
        "note": (
            "This report is rebuilt from the whole CAS journal, so it is cached "
            "against that journal's exact size and mtime. A cache is served only "
            "on an exact match; any change rebuilds. The figures therefore "
            "describe the store as of built_at and never a later state."
        ),
        "research_only": True,
        "paper_only": True,
    }
    return body
