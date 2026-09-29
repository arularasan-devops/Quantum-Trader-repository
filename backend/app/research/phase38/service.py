"""Phase 38 — build the diagnostic once, write the two required artefacts.

The resolution step reuses Phase 36's own triple builder and resolver rather
than reading its written artefacts. That is a deliberate choice and worth being
explicit about: the Phase 36 artefacts hold aggregate tables, and §2 and §4 of
this diagnostic need a *category per leg* and a peak per leg, which no aggregate
can reconstruct. Resolution is deterministic over an append-only store, so the
legs measured here are the same legs Phase 36 measured — and the run prints its
own coverage so a reader can check that against the Phase 36 report rather than
taking it on trust.

Nothing here writes to the research store, resolves anything at a horizon Phase
36 does not already resolve, or touches production.
"""
from __future__ import annotations

import json
import os
import time

from app.research.phase35 import store
from app.research.phase36 import (
    ENTRY_OFFSETS_MIN,
)
from app.research.phase36 import outcome as p36outcome
from app.research.phase36 import service as p36service
from app.research.phase36 import tables as p36tables
from app.research.phase36 import triples as p36triples
from app.research.phase36 import validate as p36validate
from app.research.phase38 import (
    ARTEFACT_DIR,
    DEFAULT_INSTRUMENT,
    JSON_NAME,
    MD_NAME,
    PAPER_ONLY,
    PHASE,
    RESEARCH_ONLY,
    VERSION,
)
from app.research.phase38 import diagnosis as p38diagnosis
from app.research.phase38 import money as p38money
from app.research.phase38 import report as p38report
from app.research.phase38 import sections as p38sections


def run(
    con,
    *,
    instrument: str = DEFAULT_INSTRUMENT,
    limit: int | None = None,
    with_entry_timing: bool = True,
) -> dict:
    """The whole diagnostic for one instrument, in one pass."""
    started = time.time()
    instrument = (instrument or DEFAULT_INSTRUMENT).strip().upper()
    built = p36triples.build(con, instrument=instrument, limit=limit)
    triples = built.pop("triples")
    coverage = dict(built)

    rows = p36outcome.resolve_all(con, triples)
    entered = [r for r in rows if r.get("entry_price") is not None]
    unmeasured = sum(
        1 for r in entered if (r.get("cost") or {}).get("cost_points") is None
    )
    coverage.update({
        "vehicle_rows": len(rows),
        "vehicle_rows_entered": len(entered),
        "entered_without_measured_cost": unmeasured,
    })
    sessions = sorted({p36validate.session_of(t["ts"]) for t in triples})
    horizon = p36service.reference_horizon(p36tables.compare(rows))

    by_offset: dict[str, list[dict]] = {"0.0": rows}
    if with_entry_timing:
        for offset in ENTRY_OFFSETS_MIN:
            if offset == 0.0:
                continue
            by_offset[str(offset)] = p36outcome.resolve_all(
                con, triples, offset=offset,
            )

    move_cost = p38sections.move_vs_cost(rows, horizon=horizon)
    hold = p38sections.hold_time(rows)
    give = p38sections.giveback(rows, horizon=horizon)
    entry = p38sections.entry_timing(by_offset, horizon=horizon)
    ms, skipped = p38money.money_rows(p38sections.directional(rows), horizon)
    vehicle_gain = p38diagnosis.vehicle_opportunity(rows, horizon=horizon)
    exit_gain = p38diagnosis.exit_opportunity(hold, horizon=horizon)
    ranked = p38diagnosis.rank(
        move_cost=move_cost, giveback=give, hold=hold, entry=entry,
        vehicle_gain=vehicle_gain, exit_gain=exit_gain,
        sessions=len(sessions),
    )
    unmeasured_pct = (
        round(100.0 * unmeasured / len(entered), 2) if entered else None
    )

    state: dict = {
        "phase": PHASE,
        "version": VERSION,
        "instrument": instrument,
        "session_count": len(sessions),
        "sessions": sessions,
        "reference_horizon": horizon,
        "reference_horizon_source": (
            "the FUTURES row's best hold over the whole sample, as Phase 36 "
            "chose it; not re-chosen here"
        ),
        "coverage": coverage,
        "unmeasured_cost_pct": unmeasured_pct,
        "legs_excluded_from_rupees": skipped,
        "vehicle_pnl": p38sections.vehicle_pnl(rows, horizon=horizon),
        "move_vs_cost": move_cost,
        "hold_time": hold,
        "giveback": give,
        "ce_vs_pe": p38sections.ce_vs_pe(rows, horizon=horizon),
        "futures_vs_options": p38sections.futures_vs_options(
            rows, horizon=horizon,
        ),
        "direction_split": p38sections.direction_split(rows, horizon=horizon),
        "entry_timing": entry,
        "premium_bands": p38sections.premium_bands(rows, horizon=horizon),
        "dte_and_moneyness": p38sections.dte_and_moneyness(
            rows, horizon=horizon,
        ),
        "waterfall": p38money.waterfall(ms),
        "vehicle_opportunity": vehicle_gain,
        "exit_opportunity": exit_gain,
        "ranked_causes": ranked,
        "research_only": RESEARCH_ONLY,
        "paper_only": PAPER_ONLY,
    }
    state["entry_note"] = p38diagnosis.entry_note(entry)
    state["diagnosis"] = p38diagnosis.primary_and_secondary(ranked)
    state["action"] = p38diagnosis.action(
        sessions=len(sessions), coverage=coverage, ranked=ranked,
        unmeasured_pct=unmeasured_pct,
    )
    state["headline"] = p38report.headline(state)
    state["elapsed_sec"] = round(time.time() - started, 2)
    return state


def artefact_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))
    path = os.path.join(root, ARTEFACT_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def write_artefacts(state: dict) -> list[str]:
    """The two files §13 names, and only those two."""
    base = artefact_dir()
    md_path = os.path.join(base, MD_NAME)
    json_path = os.path.join(base, JSON_NAME)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(p38report.render(state))
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, default=str, sort_keys=True)
    return [md_path, json_path]


def open_store():
    return store.connect()


def summary(state: dict) -> dict:
    """The small dict the CLI prints as JSON, with nothing hidden."""
    diag = state.get("diagnosis") or {}
    return {
        "phase": state.get("phase"),
        "instrument": state.get("instrument"),
        "session_count": state.get("session_count"),
        "reference_horizon": state.get("reference_horizon"),
        "eligible_triples": (state.get("coverage") or {}).get("eligible"),
        "legs_priced": (state.get("waterfall") or {}).get("n_legs"),
        "final_net_rupees": p38report.final_net(state),
        "primary_loss_driver": diag.get("primary"),
        "secondary_loss_driver": diag.get("secondary"),
        "current_action": (state.get("action") or {}).get("action"),
        "research_only": True,
        "paper_only": True,
        "elapsed_sec": state.get("elapsed_sec"),
    }
