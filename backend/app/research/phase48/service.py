"""Phase 48 — reading the Phase 46 journal and reporting where instants died.

One indexed read of the overlay journal per request, bounded, then pure
aggregation. No re-classification, no re-pricing, no historical scan, and no
write to any table: this phase owns no store, because it has nothing to record
that the journal does not already hold. The report artefact is the only file it
produces, and it is derived output that can be deleted and regenerated.
"""
from __future__ import annotations

import json
import os
import time

from app.research.phase45 import CE, FUTURES, PE
from app.research.phase46 import service as p46service
from app.research.phase46 import store as p46store
from app.research.phase48 import (
    ARTEFACT_DIR,
    CLASSIFICATION,
    JSON_NAME,
    MD_NAME,
    NO_ORDER_PATH,
    NOT_A_RESULT,
    NOT_A_THRESHOLD_SEARCH,
    PRODUCTION_UNCHANGED,
    READ_ONLY,
    STAGE_NOTES,
    STAGES,
    VERSION,
)
from app.research.phase48 import funnel as funnel_mod

# How many journalled rows one request will attribute. The session's true row
# count is reported beside the funnel, so a bounded read says so rather than
# looking like a smaller session.
MAX_ROWS = 200_000

OPTION_VEHICLES: tuple[str, ...] = (CE, PE)
ALL_VEHICLES: tuple[str, ...] = (FUTURES, CE, PE)


def definition() -> str:
    """The Phase 46 definition the journalled rows were classified under.

    Read from Phase 46 rather than restated. Rows written under a different
    definition are a different measurement and are never pooled with these.
    """
    return p46service.definition()


def _rows(
    *, session: str | None, instrument: str | None, vehicles: tuple[str, ...],
) -> tuple[list[dict], str | None, int]:
    path = p46store.db_path()
    if not os.path.exists(path):
        return [], None, 0
    con = p46store.connect(path)
    try:
        target = session
        if not target:
            row = con.execute(
                "SELECT session FROM overlay_event WHERE session IS NOT NULL "
                "ORDER BY decision_ts DESC LIMIT 1"
            ).fetchone()
            target = str(row["session"]) if row and row["session"] else None
        if not target:
            return [], None, 0
        marks = ",".join("?" for _ in vehicles)
        params: list[object] = [target, *vehicles]
        clause = ""
        if instrument:
            clause = " AND instrument = ?"
            params.append(instrument)
        total = con.execute(
            f"SELECT COUNT(*) AS n FROM overlay_event WHERE session = ? "
            f"AND vehicle IN ({marks}){clause}",
            params,
        ).fetchone()
        rows = con.execute(
            f"SELECT * FROM overlay_event WHERE session = ? "
            f"AND vehicle IN ({marks}){clause} ORDER BY decision_ts ASC "
            f"LIMIT ?",
            [*params, MAX_ROWS],
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows], target, int(total["n"] or 0) if total else 0


def report(
    *,
    session: str | None = None,
    instrument: str | None = None,
    options_only: bool = False,
    now: float | None = None,
) -> dict:
    """The funnel for one session: overall, per instrument, per vehicle.

    ``options_only`` answers the Phase 47 question specifically — that board
    takes CE and PE calls, so the funnel that explains its empty column is the
    one over option rows. The default covers all three vehicles, because
    futures dying at a different stage is itself informative.
    """
    vehicles = OPTION_VEHICLES if options_only else ALL_VEHICLES
    rows, target, journalled = _rows(
        session=session, instrument=instrument, vehicles=vehicles)
    overall = funnel_mod.summarise(rows)
    return {
        "session": target,
        "instrument": instrument,
        "vehicles": list(vehicles),
        "journalled_rows_in_session": journalled,
        "rows_attributed": len(rows),
        "truncated": journalled > len(rows),
        "overall": overall,
        "by_instrument": funnel_mod.by_key(rows, "instrument"),
        "by_vehicle": funnel_mod.by_key(rows, "vehicle"),
        "stages": list(STAGES),
        "stage_notes": STAGE_NOTES,
        "definition": definition(),
        "as_of": float(now if now is not None else time.time()),
        "classification": CLASSIFICATION,
        "read_only": READ_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
        "not_a_threshold_search": NOT_A_THRESHOLD_SEARCH,
        "not_a_result": NOT_A_RESULT,
        "version": VERSION,
    }


def sessions(*, limit: int = 40, options_only: bool = False) -> dict:
    """The funnel per session, newest first — so a blocker moving is visible.

    The interesting reading is not one session's counts but whether the
    dominant blocker is the same one every day: a warm-up blocker that repeats
    every session is not warm-up, it is a definition that never has inputs.
    """
    path = p46store.db_path()
    if not os.path.exists(path):
        return _sessions_payload([], options_only)
    con = p46store.connect(path)
    try:
        names = [
            str(r["session"]) for r in con.execute(
                "SELECT DISTINCT session FROM overlay_event "
                "WHERE session IS NOT NULL ORDER BY session DESC LIMIT ?",
                [max(1, min(int(limit), 400))],
            ) if r["session"]
        ]
    finally:
        con.close()
    out = []
    for name in names:
        payload = report(session=name, options_only=options_only)
        overall = payload["overall"]
        out.append({
            "session": name,
            "observed": overall["observed"],
            "admitted": overall["admitted"],
            "feed_could_not_speak": overall["feed_could_not_speak"],
            "definition_said_no": overall["definition_said_no"],
            "dominant_blocker": overall["dominant_blocker"],
            "stages": overall["stages"],
        })
    return _sessions_payload(out, options_only)


def _sessions_payload(rows: list[dict], options_only: bool) -> dict:
    return {
        "sessions": rows,
        "count": len(rows),
        "vehicles": list(OPTION_VEHICLES if options_only else ALL_VEHICLES),
        "definition": definition(),
        "classification": CLASSIFICATION,
        "read_only": READ_ONLY,
        "order_path": NO_ORDER_PATH,
        "not_a_threshold_search": NOT_A_THRESHOLD_SEARCH,
        "not_a_result": NOT_A_RESULT,
        "version": VERSION,
    }


def status() -> dict:
    path = p46store.db_path()
    return {
        "overlay_db_path": path,
        "overlay_db_exists": os.path.exists(path),
        "definition": definition(),
        "stages": list(STAGES),
        "classification": CLASSIFICATION,
        "read_only": READ_ONLY,
        "order_path": NO_ORDER_PATH,
        "production_effect": PRODUCTION_UNCHANGED,
        "not_a_threshold_search": NOT_A_THRESHOLD_SEARCH,
        "not_a_result": NOT_A_RESULT,
        "version": VERSION,
    }


# ----------------------------------------------------------------- artefacts
def _line(stage: str, count: int, total: int) -> str:
    share = f"{100.0 * count / total:5.1f}%" if total else "    —"
    return f"  {stage:<34} {count:>8} {share}  {STAGE_NOTES.get(stage, '')}"


def render(payload: dict) -> str:
    """The report as text. Derived from the payload, never recomputed."""
    overall = payload["overall"]
    total = overall["observed"]
    out = [
        "# ADMISSION FUNNEL — WHERE EVERY OBSERVATION DIED",
        "",
        f"session {payload['session']}  definition {payload['definition']}  "
        f"vehicles {'/'.join(payload['vehicles'])}",
        f"journalled rows in session {payload['journalled_rows_in_session']}  "
        f"attributed {payload['rows_attributed']}",
        "",
        "## STAGES",
    ]
    for stage in STAGES:
        if stage in overall["stages"]:
            out.append(_line(stage, overall["stages"][stage], total))
    out += [
        "",
        f"  feed could not speak {overall['feed_could_not_speak']}   "
        f"definition said no {overall['definition_said_no']}   "
        f"admitted {overall['admitted']}",
        f"  dominant blocker: {overall['dominant_blocker']}",
        "",
        "## HOW SHORT THE COST-REFUSED INSTANTS FELL",
    ]
    dist = overall["cost_distance"]
    if not dist["n"]:
        out.append("  no cost-refused instant carried both a ratio and a gate")
    else:
        out.append(
            f"  n {dist['n']}  median {dist['median']} of the gate  "
            f"max {dist['max']}")
        for bin_name, count in dist["at_or_above"].items():
            out.append(f"  reached >= {bin_name} of the gate: {count}")
    out += [
        "",
        "## PER INSTRUMENT",
    ]
    for name, group in payload["by_instrument"].items():
        out.append(
            f"  {name:<14} observed {group['observed']:>7}  "
            f"admitted {group['admitted']:>5}  "
            f"blocker {group['dominant_blocker']}")
    out += ["", "## PER VEHICLE"]
    for name, group in payload["by_vehicle"].items():
        out.append(
            f"  {name:<14} observed {group['observed']:>7}  "
            f"admitted {group['admitted']:>5}  "
            f"blocker {group['dominant_blocker']}")
    out += [
        "",
        "## READ THIS THE RIGHT WAY",
        f"  {payload['not_a_result']}",
        f"  {payload['not_a_threshold_search']}",
        f"  {payload['production_effect']}",
        "",
    ]
    return "\n".join(out)


def write_artefacts(payload: dict, *, root: str) -> list[str]:
    """The report and its payload on disk, under the phase's own directory."""
    directory = os.path.join(root, ARTEFACT_DIR)
    os.makedirs(directory, exist_ok=True)
    md_path = os.path.join(directory, MD_NAME)
    json_path = os.path.join(directory, JSON_NAME)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(render(payload))
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    return [md_path, json_path]
