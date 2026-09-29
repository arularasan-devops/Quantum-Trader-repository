"""Fetch each research tab once and write the payloads down, append-only.

Why over HTTP rather than by importing the services: what needs preserving is
what the tab *said*, and each tab is assembled in its route handler from live
state and per-request caches. Re-assembling it here would be a second
implementation of the same screen, free to drift from the one you actually read.
The cost is that the backend must be up, which is also the condition under which
the numbers exist at all.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from app.research.phase37 import (
    COLLECTED,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_S,
    FAILED,
    REQUIRED_TABS,
    SESSION_COMPLETE,
    SESSION_PARTIAL,
    TAB_ATTEMPTS,
    TAB_RETRY_BACKOFF_S,
    TABS,
    VERSION,
)

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _backend_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )))


def root_dir() -> str:
    """Where snapshots live. One directory per session, files never replaced."""
    return os.path.join(_backend_root(), "data", "session_evidence")


def session_date(ts: float | None = None) -> str:
    """The IST calendar day a collection belongs to.

    MCX trades to 23:30 IST, so a collection started at 23:45 still describes the
    day that just ended in every practical sense — but guessing that would mean
    inventing a session boundary here that no other phase uses. The date is the
    IST date of the clock, and `--session` overrides it when a late run needs to
    be filed against the day it describes.
    """
    when = dt.datetime.fromtimestamp(
        float(ts) if ts is not None else time.time(), _IST)
    return when.strftime("%Y-%m-%d")


def _url(base_url: str, path: str, params: dict | None) -> str:
    url = base_url.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return url


def tab_timeout(spec: dict, *, default: float = DEFAULT_TIMEOUT_S) -> float:
    """How long this tab is allowed, which is a property of the tab."""
    own = spec.get("timeout_s")
    if isinstance(own, (int, float)) and float(own) > 0:
        return float(own)
    return float(default)


def fetch(base_url: str, spec: dict, *, timeout: float = DEFAULT_TIMEOUT_S) -> dict:
    """One tab. Never raises: a failure is evidence about the session too."""
    url = _url(base_url, spec["path"], spec.get("params"))
    timeout = tab_timeout(spec, default=timeout)
    started = time.time()
    row: dict = {
        "tab": spec["tab"],
        "label": spec["label"],
        "path": spec["path"],
        "url": url,
        "fetched_at": dt.datetime.fromtimestamp(started, _IST).isoformat(),
        "timeout_s": timeout,
    }
    try:
        # Fixed, locally-built URL over http(s) to the configured backend; no
        # part of the path or query comes from a caller.
        if not url.startswith(("http://", "https://")):
            raise ValueError("base url must be http or https")
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            body = resp.read()
            row["http_status"] = int(resp.status)
        row["payload"] = json.loads(body.decode("utf-8"))
        row["status"] = COLLECTED
        row["bytes"] = len(body)
    except (urllib.error.URLError, OSError, ValueError,
            json.JSONDecodeError) as exc:
        row["status"] = FAILED
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["elapsed_ms"] = round((time.time() - started) * 1000.0, 1)
    return row


def _next_path(directory: str, tab: str) -> str:
    """A free filename for this tab, so an earlier attempt is never overwritten."""
    base = os.path.join(directory, tab.lower())
    candidate = f"{base}.json"
    n = 2
    while os.path.exists(candidate):
        candidate = f"{base}.{n}.json"
        n += 1
    return candidate


def collect(
    *,
    base_url: str = DEFAULT_BASE_URL,
    session: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    write: bool = True,
    attempts: int | None = None,
    backoff_s: float | None = None,
    sleep=time.sleep,
) -> dict:
    """Snapshot every tab for one session and return the manifest.

    A required tab that fails is retried on its own. The tabs that answered are
    not re-fetched: re-running the whole collection to recover one tab makes the
    other six pay for it, and on 2026-09-15 it meant every nightly attempt hit
    the same slow route within the same minute and the session was lost.
    """
    # Read at call time, not bound as defaults, so the budget is one value a
    # caller can see and change rather than frozen into the signature.
    attempts = TAB_ATTEMPTS if attempts is None else attempts
    backoff_s = TAB_RETRY_BACKOFF_S if backoff_s is None else backoff_s
    day = (session or session_date()).strip()
    dt.datetime.strptime(day, "%Y-%m-%d")  # refuse a malformed session date
    directory = os.path.join(root_dir(), day)
    if write:
        os.makedirs(directory, exist_ok=True)

    def _once(spec: dict, attempt: int) -> dict:
        row = fetch(base_url, spec, timeout=timeout)
        row["contributes"] = spec["contributes"]
        row["attempt"] = attempt
        if write:
            target = _next_path(directory, spec["tab"])
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(row, fh, indent=2, default=str)
            row["file"] = os.path.relpath(target, _backend_root())
        return row

    tabs: list[dict] = [_once(spec, 1) for spec in TABS]

    tries = max(1, int(attempts))
    for attempt in range(2, tries + 1):
        stuck = [
            i for i, row in enumerate(tabs)
            if row["status"] != COLLECTED and row["tab"] in REQUIRED_TABS
        ]
        if not stuck:
            break
        if backoff_s > 0:
            sleep(float(backoff_s))
        for i in stuck:
            spec = next(s for s in TABS if s["tab"] == tabs[i]["tab"])
            retried = _once(spec, attempt)
            # Keep the earlier failure visible: it is evidence about the route.
            retried["earlier_failures"] = [
                *(tabs[i].get("earlier_failures") or []),
                {
                    "attempt": tabs[i].get("attempt"),
                    "error": tabs[i].get("error"),
                    "elapsed_ms": tabs[i].get("elapsed_ms"),
                    "timeout_s": tabs[i].get("timeout_s"),
                },
            ]
            tabs[i] = retried

    collected = {t["tab"] for t in tabs if t["status"] == COLLECTED}
    absent = [t for t in REQUIRED_TABS if t not in collected]
    manifest = {
        "phase": "PHASE37_SESSION_EVIDENCE",
        "version": VERSION,
        "session": day,
        "base_url": base_url,
        "collected_at": dt.datetime.now(_IST).isoformat(),
        "tabs": [
            {k: v for k, v in t.items() if k != "payload"} for t in tabs
        ],
        "collected": sorted(collected),
        "missing_required": absent,
        "tab_attempts": {
            t["tab"]: int(t.get("attempt") or 1)
            for t in tabs if int(t.get("attempt") or 1) > 1
        },
        "slowest_tab": max(
            ({"tab": t["tab"], "elapsed_ms": float(t.get("elapsed_ms") or 0.0),
              "timeout_s": t.get("timeout_s")} for t in tabs),
            key=lambda t: t["elapsed_ms"],
        ),
        "session_status": SESSION_PARTIAL if absent else SESSION_COMPLETE,
        "research_only": True,
        "paper_only": True,
        "note": (
            "A snapshot is a record of what the tabs said, not a conclusion. "
            "Only sessions with every required tab count toward a floor."
        ),
    }
    if write:
        # The manifest is an index of the latest attempt and is rewritten; the
        # per-tab payload files are the evidence and are never replaced, so the
        # earlier attempts remain readable beside it.
        with open(os.path.join(directory, "manifest.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, default=str)
    return manifest
