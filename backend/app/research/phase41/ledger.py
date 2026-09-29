"""Phase 41 — the append-only record of which definition produced which run.

This is the only thing Phase 41 writes, and it is written to a file beside the
artefacts, never to the store. The store holds evidence; a diagnostic that
appends to the evidence it reads can no longer be re-run against it.

Append-only is the point. An overwritten record would let a definition change
disappear the moment the next run succeeded, which is exactly the failure it
exists to catch. Each line is one run: when, which definition, which components,
how many sessions and legs it saw, and what it concluded.

What a changed fingerprint does and does not mean is worth stating where the
change is recorded. It does **not** invalidate the sessions: Phase 35 never
overwrites a raw observation, so every session is recomputed under the current
definition on every run and the pooled figures are always internally consistent.
It invalidates *artefacts* — a markdown file or a pasted table written under the
old definition, which is precisely how the 69.76% adverse-first figure from
Phase 40's first release would otherwise have been laid beside the 51.28% from
its second and read as movement in the market.

The reporting rule — which of the two frames a run *quoted* as its verdict — is
recorded beside the definition and compared separately. It is not part of the
definition hash, because choosing which of two already-computed frames to quote
moves no measured number. It still has to be on the record: a table headlining
the overlapping frame and one headlining the non-overlapping frame can differ in
verdict while every figure underneath them agrees.
"""
from __future__ import annotations

import json
import os
import time

from app.research.phase41 import (
    CHANGED,
    FIRST_RUN,
    LEDGER_NAME,
    REPORTING_RULE,
    STALE_ARTEFACT,
    UNCHANGED,
)


def path(directory: str) -> str:
    return os.path.join(directory, LEDGER_NAME)


def read(directory: str) -> list[dict]:
    """Every run recorded so far, oldest first. A missing ledger is empty."""
    p = path(directory)
    if not os.path.exists(p):
        return []
    out: list[dict] = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def reporting_rule_state(previous: list[dict]) -> dict:
    """Whether the frame quoted as the verdict changed since the last run.

    Separate from :func:`compare` on purpose. A changed reporting rule does not
    invalidate a figure — both frames were computed under both rules — but it
    does explain a headline that moved while the numbers did not, which is the
    one thing a reader is otherwise certain to misattribute to the market.
    """
    earlier = [r.get("reporting_rule") for r in previous
               if r.get("reporting_rule")]
    last = earlier[-1] if earlier else None
    if last == REPORTING_RULE:
        return {"reporting_rule": REPORTING_RULE, "changed": False,
                "previous_reporting_rule": last}
    return {
        "reporting_rule": REPORTING_RULE,
        "changed": bool(previous),
        "previous_reporting_rule": last,
        "because": (
            "the verdict on record before this run was quoted from the "
            "overlapping all-legs frame, whose deviate grows with sampling "
            "density; it is now quoted from the non-overlapping frame. No "
            "measured number changed and the definition hash is unaffected, so "
            "a headline that moved between those runs moved because of this, "
            "not because of the market"
        ) if previous else (
            "no earlier run is on record, so this reporting rule is the "
            "baseline every later run will be compared against"
        ),
    }


def compare(previous: list[dict], current: dict) -> dict:
    """This run's definition against the last one recorded."""
    if not previous:
        return {
            "state": FIRST_RUN,
            "definition": current["definition"],
            "changed_components": [],
            "because": (
                "no earlier run is on record, so this definition is the "
                "baseline every later run will be compared against"
            ),
        }
    last = previous[-1]
    if last.get("definition") == current["definition"]:
        return {
            "state": UNCHANGED,
            "definition": current["definition"],
            "changed_components": [],
            "runs_under_this_definition": sum(
                1 for r in previous if r.get("definition")
                == current["definition"]
            ) + 1,
            "because": (
                "the race, cost, money and test definitions hash identically "
                "to the previous run, so its figures and these are the same "
                "measurement"
            ),
        }
    was = last.get("components", {})
    now = current["components"]
    moved = sorted(k for k in now if was.get(k) != now[k])
    return {
        "state": CHANGED,
        "definition": current["definition"],
        "previous_definition": last.get("definition"),
        "changed_components": moved,
        "stale_artefacts": STALE_ARTEFACT,
        "because": (
            f"{', '.join(moved)} changed since the run recorded at "
            f"{last.get('at_iso')}. Every session is recomputed under the "
            f"current definition here, so the figures in this run are "
            f"consistent with each other — but any artefact or pasted table "
            f"from before that change is a different measurement and must not "
            f"be compared with these."
        ),
    }


def append(directory: str, *, fingerprint: dict, payload: dict) -> dict:
    """Record this run. Creates the directory and the ledger if absent."""
    os.makedirs(directory, exist_ok=True)
    now = time.time()
    entry = {
        "at": round(now, 3),
        "at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "definition": fingerprint["definition"],
        "components": fingerprint["components"],
        "declared": fingerprint["declared"],
        "sessions": payload["sessions"],
        "legs": payload["legs"],
        "reporting_rule": REPORTING_RULE,
        # The quoted verdict, from the governing frame. The overlapping one is
        # kept beside it as description so an old row stays readable.
        "verdict": payload["accumulation"]["verdict"],
        "verdict_all_legs_descriptive": (
            payload["accumulation"]["all_legs"]["pooled"]["verdict"]
        ),
    }
    with open(path(directory), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry
