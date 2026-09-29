"""Phase 29 §9 — run the study and write the artefacts.

One run produces: coverage including the instruments that were reported rather
than studied, the unconditional sweep across every structure and geometry, the
credit-to-width band table, the time-of-day table, the ranked cohorts with their
failed clauses, per-candidate rows including every structure no rule selected,
the buyer-versus-seller comparison against the last Phase 25 report if one exists
on this machine, and a conclusion allowed to say the window cannot answer the
question.

Every artefact is strict JSON: non-finite floats are refused by the writer and
carried as a sentence instead, because an infinite profit factor is what a cohort
with no losing trade produces and it must never reach a reader as a number.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import sqlite3
import time

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase25 import books, underlying
from app.research.phase25 import report as p25report
from app.research.phase29 import (
    ASSIGNMENT_CLAIM,
    MARGIN_CLAIM,
    REJECTED,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD,
    VERSION,
    WINDOW_CLAIM,
    conditions,
    discover,
    economics,
    legs,
    outcomes,
    pool,
    rank,
    study,
)

ARTEFACT_DIR = "data/phase29"
CANDIDATE_ROWS_TOP_N = 10
NO_LOSING_TRADE = "no losing trade in the cohort"


def _abs(rel: str) -> str:
    backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))
    return os.path.join(backend, rel)


def artefact_dir(out_dir: str | None = None) -> str:
    """Where artefacts are written: the backend folder, or an override.

    The override exists so a test run cannot overwrite a real study's report.
    """
    return out_dir or _abs(ARTEFACT_DIR)


def jsonable(payload: object) -> object:
    """The payload with every non-finite float replaced by a readable value."""
    if isinstance(payload, dict):
        return {k: jsonable(v) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [jsonable(v) for v in payload]
    if isinstance(payload, (np.integer,)):
        return int(payload)
    if isinstance(payload, (np.floating,)):
        payload = float(payload)
    if isinstance(payload, (np.bool_,)):
        return bool(payload)
    if isinstance(payload, float) and not math.isfinite(payload):
        return NO_LOSING_TRADE if payload > 0 else None
    return payload


def jsonable_dict(payload: dict) -> dict:
    """:func:`jsonable` for a mapping, so a caller keeps its dict type."""
    return {k: jsonable(v) for k, v in payload.items()}


def _write(name: str, payload: object, out_dir: str | None = None) -> str:
    d = artefact_dir(out_dir)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(jsonable(payload), fh, indent=1, default=str, allow_nan=False)
    return path


def coverage(*, db_path: str | None = None) -> dict:
    """Which instruments have enough captured books, and how wide the ladder is.

    The extra question this phase has to ask, beyond Phase 25's: an instrument can
    have plenty of snapshots and still be unstudiable here, because a spread needs
    a *second* strike quoted in the same snapshot. That is reported per instrument
    as the measured strike step and the number of distinct strikes, so an
    unstudiable instrument is explained rather than just missing.
    """
    try:
        counts = books.instruments_with_books(db_path=db_path)
    except sqlite3.Error as exc:
        return {
            "unmeasured": True,
            "cause": p24data.cause(exc),  # classified, never the raw message
            "note": (
                "the stored option books could not be counted; this is an "
                "unmeasured store, not an empty one. Retry with the engine "
                "stopped."
            ),
            "eligible": [],
            "reported_only": [],
        }
    eligible: list[dict] = []
    reported: list[dict] = []
    for inst, snapshots in sorted(counts.items()):
        if snapshots < books.MIN_SNAPSHOTS:
            reported.append({
                "instrument": inst,
                "real_broker_snapshots": snapshots,
                "status": REQUIRES_MORE_DATA,
                "reasons": [
                    f"only {snapshots:,} real-broker snapshots stored (needs "
                    f"{books.MIN_SNAPSHOTS:,})"
                ],
            })
            continue
        chain = books.load_chain(inst, db_path=db_path)
        elig = dict(books.eligibility(chain))
        step = legs.strike_step(chain)
        strikes = {q.strike for q in chain.quotes.values()}
        # The binding question is the width of a *single* snapshot: strikes summed
        # across a capture whose ladder moved with spot still leaves every
        # snapshot with one strike, and one strike cannot hedge itself.
        widest = 0
        for book in chain.legs:
            for otype in (legs.CE, legs.PE):
                n = len({
                    float(v["strike"]) for v in book.values()
                    if v["option_type"] == otype
                })
                widest = max(widest, n)
        elig["strike_step_measured"] = round(step, 4)
        elig["distinct_strikes_captured"] = len(strikes)
        elig["max_strikes_in_one_snapshot"] = widest
        if step <= 0 or widest < 2:
            elig["eligible"] = False
            elig["status"] = REQUIRES_MORE_DATA
            elig["reasons"] = list(elig.get("reasons") or []) + [
                f"the widest single snapshot holds {widest} strike(s) on one "
                "side, so a protective leg cannot be paired with a short leg in "
                "the same snapshot and no defined-risk structure exists in this "
                "store"
            ]
        (eligible if elig.get("eligible") else reported).append(elig)
    return {
        "unmeasured": False,
        "eligible": eligible,
        "reported_only": reported,
        "window_claim": WINDOW_CLAIM,
        "note": (
            f"{len(eligible)} instrument(s) have a captured ladder wide enough to "
            f"price a defined-risk spread; {len(reported)} are reported and not "
            "scored"
        ),
    }


def buyer_versus_seller() -> dict:
    """The seller's numbers against the buyer's, when both exist on this machine.

    Phase 25 measured buying the option at the ask; this phase measures selling a
    defined-risk spread on the same books. The comparison is only printed if a
    Phase 25 report is actually present, and it is labelled as read from that
    report rather than recomputed here — two studies on the same store are
    comparable, a study and a remembered number are not.
    """
    prior = p25report.latest()
    if not prior:
        return {
            "available": False,
            "note": (
                "no Phase 25 report on this machine, so the buyer's side is not "
                "quoted; run the Phase 25 study on the same store to compare"
            ),
        }
    geo = [
        g for g in (prior.get("geometry_sweep") or [])
        if isinstance(g.get("avg_net_r"), (int, float))
    ]
    if not geo:
        geo = [
            g
            for r in (prior.get("instruments") or [])
            for g in (r.get("geometry_sweep") or [])
            if isinstance(g.get("avg_net_r"), (int, float))
        ]
    if not geo:
        return {"available": False, "note": "the Phase 25 report has no scored "
                "geometry rows to compare against"}
    best = max(geo, key=lambda g: g["avg_net_r"])
    return {
        "available": True,
        "source": "the last Phase 25 report written on this machine",
        "buyer_best_avg_net_r": best["avg_net_r"],
        "buyer_best_row": {
            "instrument": best.get("instrument"),
            "stop_band_pct_of_premium": best.get("stop_band_pct_of_premium"),
            "trades": best.get("trades"),
        },
        "buyer_verdict": (prior.get("conclusion") or {}).get("verdict"),
        "note": (
            "the buyer and the seller pay the same spread and the same brokerage. "
            "Decay favours the seller and the spread does not, so a seller's "
            "result better than the buyer's mirror image is expected and is not "
            "by itself evidence of an edge"
        ),
    }


def write_candidate_rows(p: pool.Pool, masks: dict, ranked: list[dict],
                         out_dir: str | None = None,
                         key: str | None = None) -> str:
    """Every structure priced, including the ones no rule ever selected.

    Rows with an empty ``selected_by`` are the refused opportunities — the NO
    TRADE side of the study — and are kept so the table can be audited rather
    than trusted.
    """
    o = p.out
    picks: list[tuple[str, np.ndarray]] = []
    for r in ranked[:CANDIDATE_ROWS_TOP_N]:
        if r["instrument"] != p.instrument or r["structure"] != p.structure:
            continue
        if r["short_steps_otm"] != p.short_steps or r["width_steps"] != p.width_steps:
            continue
        if float(r["stop_credit_multiple"]) != float(p.stop_credit_mult):
            continue
        if r.get("hold") is not None and r["hold"] != p.hold:
            continue
        m = np.ones(len(p), dtype=bool)
        for c in r["conditions"]:
            m = m & masks[c]
        picks.append((r["strategy_id"], m))

    stem = key or (
        f"candidates_{p.instrument}_{p.structure}"
        f"_{p.short_steps}x{p.width_steps}_{p.hold}"
    )
    name = f"p29_{stem}.jsonl.gz"
    d = artefact_dir(out_dir)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)

    def num(value: float, places: int) -> float | None:
        return round(float(value), places) if np.isfinite(value) else None

    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for k in range(len(p)):
            fh.write(json.dumps({
                "instrument": p.instrument,
                "structure": p.structure,
                "hold": p.hold,
                "underlying_view": legs.VIEW[p.structure],
                "decision_ts": int(p.ts[k]),
                "entry_ts": int(p.entry_ts[k]),
                "session": int(p.session[k]),
                "short_legs": str(p.short_symbols[k]),
                "protective_legs": str(p.long_symbols[k]),
                "width_points": round(p.width_points, 2),
                "credit_received": num(o.credit[k], 2),
                "credit_pct_of_width": num(p.feat["credit_pct_of_width"][k], 2),
                "defined_max_loss_points": num(o.max_loss[k], 2),
                "stop_credit_multiple": p.stop_credit_mult,
                "risk_points": num(o.risk[k], 2),
                "target_points": num(o.t1[k], 2),
                "measured_spread_points": num(o.spread_points[k], 3),
                "friction_pct_of_credit": num(o.hurdle_pct[k], 2),
                "cost_to_close": num(o.exit_cost[k], 2),
                "outcome": str(o.outcome[k]),
                "resolved": bool(o.resolved[k]),
                "exit_ts": int(p.exit_ts[k]) or None,
                "holding_seconds": int(p.hold_sec[k]) if p.hold_sec[k] >= 0 else None,
                "quotes_held": int(o.bars_held[k]),
                "paired_quotes_ahead": int(p.feat["quotes_ahead"][k]),
                "cost_points": num(o.cost_points[k], 3),
                "net_points": num(o.net_points[k], 3),
                "net_r": num(o.net_r[k], 4),
                "return_on_defined_risk_pct": num(100.0 * o.ror_defined_risk[k], 3),
                "net_rupees": num(o.net_rupees[k], 2),
                "conditions_true": sorted(n for n, m in masks.items() if bool(m[k])),
                "selected_by": [sid for sid, m in picks if bool(m[k])],
                "paper_only": True,
                "assignment": ASSIGNMENT_CLAIM,
            }) + "\n")
    return path


def hold_comparison(instruments: list[dict]) -> list[dict]:
    """The two frozen holds side by side, pooled over the priced geometries.

    A one-hour hold measures almost no decay, so the honest way to read the
    seller's case is against a hold that runs to the session's last paired quote.
    Rows are pooled across geometries by trade count, which is a description of
    the sweep and not a strategy.
    """
    rows: list[dict] = []
    for name in outcomes.HOLDS:
        scored = [
            g
            for r in instruments if r.get("status") == "STUDIED"
            for g in (r.get("unconditional") or [])
            if g.get("hold") == name
            and isinstance(g.get("avg_net_r"), (int, float))
        ]
        if not scored:
            rows.append({"hold": name, "scored_geometries": 0,
                         "status": REQUIRES_MORE_DATA})
            continue
        trades = sum(int(g["trades"]) for g in scored)
        weight = max(trades, 1)
        rows.append({
            "hold": name,
            "horizon_seconds": outcomes.HOLDS[name][0],
            "scored_geometries": len(scored),
            "positive_geometries": sum(1 for g in scored if g["avg_net_r"] > 0),
            "trades": trades,
            "trade_weighted_avg_net_r": round(
                sum(g["avg_net_r"] * int(g["trades"]) for g in scored) / weight, 4
            ),
            "trade_weighted_target_before_stop_pct": round(
                sum(
                    float(g["target_before_stop_pct"]) * int(g["trades"])
                    for g in scored
                ) / weight, 2
            ),
            "trade_weighted_timeout_pct": round(
                sum(
                    float(g.get("timeout_pct") or 0.0) * int(g["trades"])
                    for g in scored
                ) / weight, 2
            ),
            "best_avg_net_r": max(g["avg_net_r"] for g in scored),
            "status": "OK",
        })
    return rows


def conclusion(instruments: list[dict], ranked: list[dict]) -> dict:
    """The verdict, including the honest 'this window cannot answer it' one."""
    studied = [r for r in instruments if r.get("status") == "STUDIED"]
    leads = [r for r in ranked if r["status"] == RESEARCH_LEAD]
    sweep = [
        g for r in studied for g in (r.get("unconditional") or [])
        if isinstance(g.get("avg_net_r"), (int, float))
    ]
    best = max(sweep, key=lambda g: g["avg_net_r"], default=None)
    if not studied:
        return {
            "verdict": REQUIRES_MORE_DATA,
            "headline": (
                "no instrument has a captured ladder wide enough to price a "
                "defined-risk credit spread; the study did not run, which is not "
                "a negative result"
            ),
            "window": WINDOW_CLAIM,
            "leads": 0,
        }
    if leads:
        return {
            "verdict": RESEARCH_LEAD,
            "headline": (
                f"{len(leads)} credit-spread cohort(s) survived every clause the "
                "captured window can test. That makes them leads for live paper "
                "collection, not validated edges and not promotion candidates"
            ),
            "window": WINDOW_CLAIM,
            "margin": MARGIN_CLAIM,
            "assignment": ASSIGNMENT_CLAIM,
            "leads": len(leads),
            "next_step": (
                "record these structures as paper spreads on live books and "
                "re-run this study on a longer window; a short spread's tail "
                "risk means nothing here justifies a live order"
            ),
        }
    closest = ranked[0] if ranked else None
    return {
        "verdict": REJECTED,
        "headline": (
            "no credit-spread cohort survived on the captured window: selling "
            "the short leg at the bid and buying the protective leg at the ask "
            "did not pay after brokerage, statutory charges and slippage"
        ),
        "window": WINDOW_CLAIM,
        "margin": MARGIN_CLAIM,
        "assignment": ASSIGNMENT_CLAIM,
        "leads": 0,
        "best_unconditional_row": None if best is None else {
            "structure": best.get("structure"),
            "hold": best.get("hold"),
            "short_steps_otm": best.get("short_steps_otm"),
            "width_steps": best.get("width_steps"),
            "stop_credit_multiple": best.get("stop_credit_multiple"),
            "trades": best.get("trades"),
            "target_before_stop_pct": best.get("target_before_stop_pct"),
            "breakeven_target_pct_before_costs": best.get(
                "breakeven_target_pct_before_costs"
            ),
            "avg_net_r": best.get("avg_net_r"),
            "median_friction_pct_of_credit": best.get(
                "median_friction_pct_of_credit"
            ),
        },
        "closest_candidate": None if closest is None else {
            "strategy_id": closest.get("strategy_id"),
            "instrument": closest.get("instrument"),
            "structure": closest.get("structure"),
            "conditions": closest.get("conditions"),
            "status": closest.get("status"),
            "failed_clauses": closest.get("failed_clauses"),
        },
    }


def _hold_answer(rows: list[dict]) -> str:
    scored = [r for r in rows if r.get("status") == "OK"]
    if len(scored) < 2:
        return (
            "only one hold reached a scored sample, so the two cannot be "
            "compared on this window"
        )
    parts = ", ".join(
        f"{r['hold']} {r['trade_weighted_avg_net_r']}R on {r['trades']:,} trades "
        f"(target kept {r['trade_weighted_target_before_stop_pct']}%, "
        f"timeout {r['trade_weighted_timeout_pct']}%)"
        for r in scored
    )
    best = max(scored, key=lambda r: r["trade_weighted_avg_net_r"])
    return (
        f"{parts}. Best trade-weighted hold: {best['hold']}. Longer holds let "
        "more of the credit decay, and they also give the underlying more time "
        "to reach the short strike; whichever of those dominates is what these "
        "numbers report."
    )


def answers(out: dict) -> list[dict]:
    """Direct answers to the questions this study was built to settle."""
    rows = out["instruments"]
    studied = [r for r in rows if r.get("status") == "STUDIED"]
    sweep = [g for r in studied for g in (r.get("unconditional") or [])]
    scored = [g for g in sweep if isinstance(g.get("avg_net_r"), (int, float))]
    best = max(scored, key=lambda g: g["avg_net_r"], default=None)
    positive = [g for g in scored if g["avg_net_r"] > 0]
    friction = [
        g["median_friction_pct_of_credit"] for g in scored
        if isinstance(g.get("median_friction_pct_of_credit"), (int, float))
    ]
    condors = [g for g in scored if g["structure"] == legs.IRON_CONDOR]

    def q(question: str, answer: str) -> dict:
        return {"question": question, "answer": answer}

    return [
        q("Can a defined-risk credit spread be priced from the stored books at all?",
          f"Yes for {len(studied)} instrument(s): both legs quoted two-sided in "
          f"the same snapshot, short sold at the bid and protective bought at the "
          f"ask. {len(rows) - len(studied)} instrument(s) were reported and not "
          "scored."),
        q("Is selling premium profitable on the captured window?",
          out["conclusion"]["headline"]),
        q("Is the seller's side better than the buyer's?",
          (out.get("buyer_versus_seller") or {}).get("note", "not comparable")
          + f" Best seller row: {best['avg_net_r'] if best else 'none scored'}R."),
        q("How high is the win rate, and does it matter?",
          "none scored" if best is None else
          f"the best unconditional row kept its target credit "
          f"{best['target_before_stop_pct']}% of the time against "
          f"{best['breakeven_target_pct_before_costs']}% needed before costs, at "
          f"{best['avg_net_r']}R per trade. A high win rate on a credit spread is "
          "the geometry, not the result: the target is half a credit and the stop "
          "is one or more."),
        q("How much of the credit does the round trip cost?",
          "not measurable" if not friction else
          f"median friction is {min(friction):.1f}%–{max(friction):.1f}% of the "
          "credit collected across the priced geometries, paid on four legs "
          "(two on the way in, two on the way out)."),
        q("Does holding to the session close beat a one-hour hold?",
          _hold_answer(out.get("hold_comparison") or [])),
        q("Does a direction-neutral structure do better than a directional one?",
          "no iron condor row reached a scored sample" if not condors else
          f"{len(condors)} iron-condor row(s) scored, best "
          f"{max(c['avg_net_r'] for c in condors)}R; the directional verticals are "
          "in the same table and neither shape is claimed superior on a window "
          "this short."),
        q("How many geometries were positive before any condition?",
          f"{len(positive)} of {len(scored)} scored rows. A cohort inside a "
          "negative pool is a subset of a losing population, not a finding."),
        q("Is any margin or return-on-capital figure produced?",
          "No. " + MARGIN_CLAIM),
        q("Is any expiry, assignment or settlement outcome assumed?",
          "No. " + ASSIGNMENT_CLAIM),
        q("Was anything promoted or wired to an order path?",
          "No. Nothing in Phase 29 writes a signal, a paper book or an order, "
          "the capture and the existing option strategy logic are untouched, and "
          "the best verdict this study can produce is a research lead."),
        q("What would change the answer?",
          "A longer capture. Every result here is bounded by a window of weeks, "
          "and the same code re-run on a longer store is the only honest route to "
          "a stronger word than RESEARCH_LEAD."),
    ]


def _geometry_block() -> dict:
    return {
        "structures": list(legs.STRUCTURES),
        "short_steps_out_of_the_money": list(legs.SHORT_STEPS),
        "width_steps": list(legs.WIDTH_STEPS),
        "strike_step": "measured from the captured ladder, never assumed",
        "stop_credit_multiples": list(outcomes.STOP_CREDIT_MULTIPLES),
        "take_profit_fractions_of_credit": [
            outcomes.TAKE_1, outcomes.TAKE_2, outcomes.TAKE_3
        ],
        "holds": {
            name: {
                "horizon_seconds": horizon,
                "max_forward_paired_quotes": steps,
            }
            for name, (horizon, steps) in outcomes.HOLDS.items()
        },
        "overnight": (
            "no position is carried past the session it was opened in: a gap "
            "through the short strike cannot be managed and this store cannot "
            "price it either"
        ),
        "entry": (
            "short leg sold at the bid, protective leg bought at the ask, both "
            "from a later snapshot than the decision and both quoted in the same "
            "snapshot as each other"
        ),
        "exit": (
            "short leg bought back at the ask, protective leg sold at the bid, "
            "both at one paired snapshot"
        ),
        "slippage_pct_per_side_per_leg": round(100.0 * economics.slippage_pct(), 4),
        "costs_charged": (
            "brokerage + statutory per leg, two orders per leg; the spread is "
            "already paid in the prices above, so charging the cost model's "
            "spread term as well would pay it twice"
        ),
        "defined_loss": "width minus net credit, a hard floor on every row",
        "split": (
            f"chronological by session: {int(100 * discover.DEV_FRAC)}% "
            f"development / {int(100 * discover.VAL_FRAC)}% validation / "
            "remainder untouched holdout"
        ),
        "margin": MARGIN_CLAIM,
        "assignment": ASSIGNMENT_CLAIM,
    }


def run(*, quick: bool = False, instruments: list[str] | None = None,
        db_path: str | None = None, out_dir: str | None = None) -> dict:
    """Run the credit-spread study and write every artefact.

    ``db_path`` is injectable so the pipeline can be exercised end to end on a
    purpose-built store; the default is the live capture store, read-only.
    """
    started = time.time()
    cov = coverage(db_path=db_path)
    if cov.get("unmeasured"):
        out = {
            "version": VERSION,
            "generated_at": int(started),
            "runtime_seconds": round(time.time() - started, 1),
            "quick_mode": bool(quick),
            "coverage": cov,
            "instruments": [],
            "ranked": [],
            "hypotheses_evaluated": 0,
            "conclusion": {
                "verdict": REQUIRES_MORE_DATA,
                "headline": cov["note"],
                "window": WINDOW_CLAIM,
                "leads": 0,
            },
            "answers": [],
            "paper_only": True,
        }
        _write("p29_study_report.json", out, out_dir)
        return out

    names = [r["instrument"] for r in cov["eligible"]]
    if instruments:
        wanted = {i.upper() for i in instruments}
        names = [n for n in names if n in wanted]

    rows: list[dict] = []
    ranked_all: list[dict] = []
    tests = 0
    for inst in names:
        res = study.run_instrument(inst, quick=quick, db_path=db_path)
        rows.append(res)
        tests += int(res.get("hypotheses_evaluated") or 0)
        ranked_all.extend(res.get("rules") or [])
    for r in cov["reported_only"]:
        rows.append({
            "instrument": r["instrument"],
            "coverage": r,
            "status": REQUIRES_MORE_DATA,
            "rules": [],
            "unconditional": [],
        })

    ranked = rank.rank(ranked_all)
    out = {
        "version": VERSION,
        "generated_at": int(started),
        "quick_mode": bool(quick),
        "coverage": cov,
        "window_claim": WINDOW_CLAIM,
        "geometry": _geometry_block(),
        "instruments": rows,
        "ranked": ranked,
        "buyer_versus_seller": buyer_versus_seller(),
        "hold_comparison": hold_comparison(rows),
        "hypotheses_evaluated": tests,
        "paper_only": True,
    }
    out["conclusion"] = conclusion(rows, ranked)
    out["answers"] = answers(out)
    out["runtime_seconds"] = round(time.time() - started, 1)

    artefacts = {
        "report": _write("p29_study_report.json", out, out_dir),
        "coverage": _write("p29_coverage.json", cov, out_dir),
        "ranked": _write("p29_ranked_cohorts.json", ranked, out_dir),
        "unconditional": _write("p29_unconditional_sweep.json", [
            {"instrument": r["instrument"], **g}
            for r in rows for g in (r.get("unconditional") or [])
        ], out_dir),
        "credit_bands": _write("p29_credit_bands.json", [
            {"instrument": r["instrument"], "bands": r.get("credit_bands"),
             "time_of_day": r.get("time_of_day")}
            for r in rows if r.get("status") == "STUDIED"
        ], out_dir),
        "buyer_versus_seller": _write(
            "p29_buyer_vs_seller.json", out["buyer_versus_seller"], out_dir
        ),
        "hold_comparison": _write(
            "p29_hold_comparison.json", out["hold_comparison"], out_dir
        ),
        "rejected_log": _write("p29_rejected.json", [
            {"strategy_id": r["strategy_id"], "instrument": r["instrument"],
             "structure": r["structure"], "conditions": r["conditions"],
             "stop_credit_multiple": r["stop_credit_multiple"],
             "status": r["status"], "failed_clauses": r["failed_clauses"]}
            for r in ranked if r["status"] != RESEARCH_LEAD
        ], out_dir),
        "fingerprints": _write("p29_fingerprints.json", [
            r["fingerprint"] for r in ranked if r["status"] == RESEARCH_LEAD
        ] or {"fingerprints": [], "note": "no cohort reached RESEARCH_LEAD, so "
              "nothing is published even as a paper lead"}, out_dir),
    }

    if not quick:
        for r in rows:
            if r.get("status") != "STUDIED":
                continue
            inst = r["instrument"]
            chain = books.load_chain(inst, db_path=db_path)
            series = underlying.load_series(inst, db_path=db_path)
            for hold in study.holds():
                for structure, short, width in study.geometries():
                    p = pool.build(
                        inst, structure=structure, short_steps=short,
                        width_steps=width, chain=chain, series=series, hold=hold,
                    )
                    if p is None:
                        continue
                    masks = conditions.masks(p.feat, p.side, p.structure)
                    key = f"candidates_{inst}_{structure}_{short}x{width}_{hold}"
                    artefacts[key] = write_candidate_rows(
                        p, masks, ranked, out_dir, key=key
                    )

    out["artefacts"] = artefacts
    _write("p29_study_report.json", out, out_dir)
    return out


def latest() -> dict | None:
    path = os.path.join(_abs(ARTEFACT_DIR), "p29_study_report.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def latest_ranked() -> list[dict]:
    path = os.path.join(_abs(ARTEFACT_DIR), "p29_ranked_cohorts.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    return rows if isinstance(rows, list) else []


def latest_sweep() -> list[dict]:
    path = os.path.join(_abs(ARTEFACT_DIR), "p29_unconditional_sweep.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    return rows if isinstance(rows, list) else []


__all__ = [
    "run", "latest", "latest_ranked", "latest_sweep", "coverage", "conclusion",
    "answers", "write_candidate_rows", "buyer_versus_seller", "artefact_dir",
    "jsonable", "jsonable_dict",
]
