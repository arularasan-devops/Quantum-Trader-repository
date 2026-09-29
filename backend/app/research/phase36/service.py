"""Phase 36 — run the whole vehicle study once, in one pass.

The order here is the order the task states, and it is also a dependency order:
coverage before triples, triples before outcomes, outcomes before every table,
tables before the verdict. Nothing recomputes an outcome, so every table in the
report is provably describing the same legs.

The reference horizon deserves a note, because it is the one place a choice is
made. Cut tables (§10-§13, §17) need a single horizon or they become
unreadable, and that horizon is taken from the *futures* row's best hold on the
whole sample. Not from each cut's own best hold — that would let every bucket
pick its own favourable hold and turn a diagnostic into an optimiser.
"""
from __future__ import annotations

import json
import os
import time

from app.research.phase35 import FUTURES, store
from app.research.phase36 import (
    ARTEFACT_DIR,
    CLOSE,
    DEFAULT_INSTRUMENT,
    ENTRY_CONFIRMATION,
    ENTRY_OFFSETS_MIN,
    PAPER_ONLY,
    RESEARCH_ONLY,
    VERSION,
)
from app.research.phase36 import engine as p36engine
from app.research.phase36 import outcome as p36outcome
from app.research.phase36 import report as p36report
from app.research.phase36 import tables as p36tables
from app.research.phase36 import triples as p36triples
from app.research.phase36 import validate as p36validate

REFERENCE_FALLBACK = "30"


def reference_horizon(comparison: dict) -> str:
    """The one horizon the cut tables are read at."""
    row = (comparison.get("table") or {}).get(FUTURES) or {}
    best = row.get("best_hold")
    return str(best) if best else REFERENCE_FALLBACK


def run(
    con,
    *,
    instrument: str = DEFAULT_INSTRUMENT,
    limit: int | None = None,
    with_entry_timing: bool = True,
) -> dict:
    """The complete study for one instrument.

    ``with_entry_timing`` is the only optional part: §15 re-resolves every leg
    three more times, which triples the work, and on a large capture it is worth
    being able to get the headline tables first.
    """
    started = time.time()
    instrument = (instrument or DEFAULT_INSTRUMENT).strip().upper()
    built = p36triples.build(con, instrument=instrument, limit=limit)
    triples = built.pop("triples")
    coverage = dict(built)

    rows = p36outcome.resolve_all(con, triples)
    entered = [r for r in rows if r.get("entry_price") is not None]
    coverage.update({
        "vehicle_rows": len(rows),
        "vehicle_rows_entered": len(entered),
        "unresolved": len(rows) - len(entered),
        "unresolved_by_reason": _reasons(rows),
        # An entered leg whose cost could not be measured (no lot size) is
        # counted here rather than being silently absent from every table: a
        # sample of zero with no reason is indistinguishable from a bug.
        "entered_without_measured_cost": sum(
            1 for r in entered
            if (r.get("cost") or {}).get("cost_points") is None
        ),
        "average_book_age_ms": _avg_book_age(con, instrument),
        "direction_mix": p36outcome.short_direction_count(triples),
        "sessions": sorted({
            p36validate.session_of(t["ts"]) for t in triples
        }),
    })

    comparison = p36tables.compare(rows)
    horizon = reference_horizon(comparison)

    state: dict = {
        "phase": "36",
        "version": VERSION,
        "instrument": instrument,
        "reference_horizon": horizon,
        "coverage": coverage,
        "vehicle_comparison": comparison,
        "by_direction": p36tables.by_direction(rows),
        "counterfactual": p36tables.counterfactual(rows),
        "by_premium_band": p36tables.by_premium_band(rows, horizon=horizon),
        "by_dte": p36tables.by_dte(rows, horizon=horizon),
        "by_moneyness": p36tables.by_moneyness(rows, horizon=horizon),
        "by_time_of_day": p36tables.by_time_of_day(rows, horizon=horizon),
        "by_cost_over_move": p36tables.by_cost_over_move(rows, horizon=horizon),
        "giveback": p36tables.giveback(rows),
        "cost_decomposition": p36tables.cost_decomposition(rows, horizon=horizon),
        "engine": p36engine.attribute(rows, triples, horizon=horizon),
        "placebo": p36validate.placebo(con, triples, horizon=horizon),
        "chronological": p36validate.chronological(rows, horizon=horizon),
        "walk_forward": p36validate.walk_forward(rows, horizon=horizon),
        "stress": p36validate.stress(rows, horizon=horizon),
        "outliers": p36validate.outliers(rows, horizon=horizon),
    }
    if with_entry_timing:
        by_offset = {str(o): rows if o == 0.0 else p36outcome.resolve_all(
            con, triples, offset=o,
        ) for o in ENTRY_OFFSETS_MIN}
        by_offset[ENTRY_CONFIRMATION] = p36outcome.resolve_all(
            con, triples, offset=ENTRY_CONFIRMATION,
        )
        state["entry_timing"] = p36engine.entry_timing(
            by_offset, horizon=horizon,
        )

    state["verdict"] = p36report.verdict(state)
    state["sections"] = p36report.sections(state)
    state["headline"] = p36report.headline(state)
    state["elapsed_sec"] = round(time.time() - started, 2)
    return state


def _reasons(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        if r.get("entry_price") is None:
            key = str(r.get("reason") or "UNKNOWN")
            out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _avg_book_age(con, instrument: str) -> float | None:
    """Mean book age of the executable quotes behind this instrument's triples.

    Reported because §25.1 asks for it and because a mean age creeping toward the
    two-second freshness ceiling is the early warning that the next capture day
    will produce fewer triples, not more.
    """
    row = con.execute(
        "SELECT AVG(json_extract(o.context_json, '$.book_age_ms')) AS a "
        "FROM raw_observation o WHERE o.instrument = ?",
        (instrument,),
    ).fetchone()
    val = row["a"] if row else None
    return round(float(val), 1) if isinstance(val, (int, float)) else None


def artefact_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))
    path = os.path.join(root, ARTEFACT_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def write_artefacts(state: dict) -> list[str]:
    """Eight artefacts, one per readable concern, plus the machine-readable state.

    Split by concern rather than dumped as one file because these get read by a
    person at 23:40 who wants one answer, and because a diff between two runs of
    ``vehicle_comparison.json`` is a useful thing while a diff of a 4 MB blob is
    not.
    """
    out: list[str] = []
    base = artefact_dir()
    inst = state.get("instrument", "UNKNOWN")
    files = {
        f"{inst}_state.json": state,
        f"{inst}_coverage.json": state.get("coverage"),
        f"{inst}_vehicle_comparison.json": {
            "reference_horizon": state.get("reference_horizon"),
            "comparison": state.get("vehicle_comparison"),
            "by_direction": state.get("by_direction"),
            "counterfactual": state.get("counterfactual"),
        },
        f"{inst}_cuts.json": {
            "premium_band": state.get("by_premium_band"),
            "dte": state.get("by_dte"),
            "moneyness": state.get("by_moneyness"),
            "time_of_day": state.get("by_time_of_day"),
            "cost_over_move": state.get("by_cost_over_move"),
        },
        f"{inst}_hold_and_giveback.json": {
            "giveback": state.get("giveback"),
            "entry_timing": state.get("entry_timing"),
        },
        f"{inst}_engine_attribution.json": state.get("engine"),
        f"{inst}_robustness.json": {
            "placebo": state.get("placebo"),
            "chronological": state.get("chronological"),
            "walk_forward": state.get("walk_forward"),
            "stress": state.get("stress"),
            "outliers": state.get("outliers"),
        },
        f"{inst}_verdict.json": {
            "verdict": state.get("verdict"),
            "sections": state.get("sections"),
            "headline": state.get("headline"),
        },
    }
    for name, body in files.items():
        target = os.path.join(base, name)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(body, fh, indent=2, default=str, sort_keys=True)
        out.append(target)
    return out


def open_store():
    return store.connect()


ARTEFACT_KINDS: tuple[str, ...] = (
    "coverage", "vehicle_comparison", "cuts", "hold_and_giveback",
    "engine_attribution", "robustness", "verdict", "state",
)


def artefact(kind: str, *, instrument: str = DEFAULT_INSTRUMENT) -> dict:
    """Read one written artefact. The API never starts a study.

    A run resolves every leg at twelve horizons and repeats the whole thing for
    the placebo and the stress grid; doing that inside a request would starve
    the live capture the evidence comes from. So the reader is deliberately
    dumb: it returns what the CLI wrote, or says nothing has been written yet.
    """
    if kind not in ARTEFACT_KINDS:
        return {"available": False, "reason": f"unknown artefact {kind!r}",
                "kinds": list(ARTEFACT_KINDS)}
    inst = (instrument or DEFAULT_INSTRUMENT).strip().upper()
    if not inst.isalnum() or len(inst) > 24:
        return {"available": False, "reason": "instrument name not accepted"}
    target = os.path.join(artefact_dir(), f"{inst}_{kind}.json")
    try:
        with open(target, encoding="utf-8") as fh:
            body = json.load(fh)
    except (OSError, ValueError):
        return {
            "available": False,
            "instrument": inst,
            "artefact": kind,
            "reason": "no phase36 artefact written yet; run the CLI first",
            "research_only": RESEARCH_ONLY,
            "paper_only": PAPER_ONLY,
        }
    return {
        "available": True,
        "instrument": inst,
        "artefact": kind,
        "written_at": os.path.getmtime(target),
        "body": body,
        "research_only": RESEARCH_ONLY,
        "paper_only": PAPER_ONLY,
    }


def summary(state: dict) -> dict:
    """The small dict an API or a CLI prints, with nothing hidden in it."""
    cov = state.get("coverage") or {}
    return {
        "instrument": state.get("instrument"),
        "verdict": (state.get("verdict") or {}).get("verdict"),
        "leading_vehicle": (state.get("verdict") or {}).get("leading_vehicle"),
        "reference_horizon": state.get("reference_horizon"),
        "observations": cov.get("observations"),
        "eligible_triples": cov.get("eligible"),
        "sessions": len(cov.get("sessions") or []),
        "close_horizon_key": CLOSE,
        "gates_failed": [
            g["gate"] for g in (state.get("verdict") or {}).get("gates", [])
            if not g["passed"]
        ],
        "elapsed_sec": state.get("elapsed_sec"),
    }
