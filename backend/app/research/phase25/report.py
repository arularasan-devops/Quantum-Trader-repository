"""Phase 25 §8 — run the study and write the artefacts.

One run produces everything: coverage with the instruments that were reported
rather than studied, the geometry sweep, the CE-versus-PE table, the hurdle-band
table, the ranked rules with their failed clauses, the per-candidate rows
including every opportunity no rule selected, and a conclusion that is allowed
to say the captured window cannot answer the question.
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
from app.research.phase25 import (
    REJECTED,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD,
    VERSION,
    WINDOW_CLAIM,
    books,
    conditions,
    discover,
    outcomes,
    pool,
    rank,
    study,
    underlying,
)

ARTEFACT_DIR = "data/phase25"
CANDIDATE_ROWS_TOP_N = 10


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


NO_LOSING_TRADE = "no losing trade in the cohort"


def jsonable(payload: object) -> object:
    """The payload with every non-finite float replaced by a readable value.

    A cohort with no losing trade has an infinite profit factor, which is not
    valid JSON and would either break a strict reader or be silently coerced to
    a number. It is carried as a sentence instead, so a reader cannot mistake it
    for a measured ratio.
    """
    if isinstance(payload, dict):
        return {k: jsonable(v) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [jsonable(v) for v in payload]
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
    """Which instruments have enough captured books to be studied at all."""
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
        # The cheap count decides whether it is worth loading payloads at all:
        # an instrument with one book cannot become eligible.
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
        elig = books.eligibility(books.load_chain(inst, db_path=db_path))
        (eligible if elig["eligible"] else reported).append(elig)
    return {
        "unmeasured": False,
        "eligible": eligible,
        "reported_only": reported,
        "window_claim": WINDOW_CLAIM,
        "note": (
            f"{len(eligible)} instrument(s) have enough captured real books to "
            f"study; {len(reported)} are reported and not scored"
        ),
    }


def write_candidate_rows(p: pool.Pool, masks: dict, band: float,
                         ranked: list[dict], out_dir: str | None = None) -> str:
    """Every candidate row, including the ones no rule ever selected.

    Rows with an empty ``selected_by`` are the refused opportunities — the NO
    TRADE side of the study — and are kept so the table can be audited rather
    than trusted.
    """
    o = p.out
    picks: list[tuple[str, np.ndarray]] = []
    for r in ranked[:CANDIDATE_ROWS_TOP_N]:
        if r["instrument"] != p.instrument:
            continue
        if float(r["stop_band_pct_of_premium"]) != round(100.0 * band, 1):
            continue
        m = p.option_type == r["option_type"]
        for c in r["conditions"]:
            m = m & masks[c]
        picks.append((r["strategy_id"], m))

    name = f"p25_candidates_{p.instrument}_{round(100 * band):g}pct.jsonl.gz"
    d = artefact_dir(out_dir)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for k in range(len(p)):
            fh.write(json.dumps({
                "instrument": p.instrument,
                "decision_ts": int(p.ts[k]),
                "entry_ts": int(p.entry_ts[k]),
                "session": int(p.session[k]),
                "option_type": str(p.option_type[k]),
                "symbol": str(p.symbol[k]),
                "strike": float(p.strike[k]),
                "underlying_at_decision": round(float(p.spot[k]), 2),
                "entry_bid": round(float(p.entry_bid[k]), 2),
                "entry_ask": round(float(p.entry_ask[k]), 2),
                "measured_spread_pct": (
                    round(float(o.spread_pct[k]), 3)
                    if np.isfinite(o.spread_pct[k]) else None
                ),
                "break_even_hurdle_pct": (
                    round(float(p.feat["hurdle_pct"][k]), 3)
                    if np.isfinite(p.feat["hurdle_pct"][k]) else None
                ),
                "entry_paid": round(float(o.entry[k]), 2),
                "stop": round(float(o.stop[k]), 2),
                "t1": round(float(o.t1[k]), 2),
                "exit_received": (
                    round(float(o.exit_price[k]), 2)
                    if np.isfinite(o.exit_price[k]) else None
                ),
                "outcome": str(o.outcome[k]),
                "resolved": bool(o.resolved[k]),
                "exit_ts": int(p.exit_ts[k]) or None,
                "holding_seconds": (
                    int(p.hold_sec[k]) if p.hold_sec[k] >= 0 else None
                ),
                "quotes_held": int(o.bars_held[k]),
                "quotes_ahead": int(p.feat["quotes_ahead"][k]),
                "cost_points": round(float(o.cost_points[k]), 3),
                "net_points": round(float(o.net_points[k]), 3),
                "net_r": round(float(o.net_r[k]), 4),
                "net_rupees": round(float(o.net_rupees[k]), 2),
                "conditions_true": sorted(
                    n for n, m in masks.items() if bool(m[k])
                ),
                "selected_by": [sid for sid, m in picks if bool(m[k])],
                "paper_only": True,
            }) + "\n")
    return path


def conclusion(instruments: list[dict], ranked: list[dict]) -> dict:
    """The verdict, including the honest 'this window cannot answer it' one."""
    studied = [r for r in instruments if r.get("status") == "STUDIED"]
    leads = [r for r in ranked if r["status"] == RESEARCH_LEAD]
    if not studied:
        return {
            "verdict": REQUIRES_MORE_DATA,
            "headline": (
                "no instrument has enough captured real option books to study; "
                "the study did not run, which is not a negative result"
            ),
            "window": WINDOW_CLAIM,
            "leads": 0,
        }
    if leads:
        return {
            "verdict": RESEARCH_LEAD,
            "headline": (
                f"{len(leads)} cohort(s) survived every clause the captured "
                "window can test. That makes them leads for live paper "
                "collection, not validated edges and not promotion candidates"
            ),
            "window": WINDOW_CLAIM,
            "leads": len(leads),
            "next_step": (
                "keep the live capture running and re-run this study on a longer "
                "window; nothing here should change a live decision"
            ),
        }
    closest = ranked[0] if ranked else None
    return {
        "verdict": REJECTED,
        "headline": (
            "no cohort survived on the captured window: buying the ATM option "
            "at the ask and selling it at the bid did not pay after brokerage, "
            "statutory charges and slippage"
        ),
        "window": WINDOW_CLAIM,
        "leads": 0,
        "closest_candidate": None if closest is None else {
            "strategy_id": closest.get("strategy_id"),
            "instrument": closest.get("instrument"),
            "option_type": closest.get("option_type"),
            "conditions": closest.get("conditions"),
            "status": closest.get("status"),
            "failed_clauses": closest.get("failed_clauses"),
        },
    }


def answers(out: dict) -> list[dict]:
    """Direct answers to the questions this study was built to settle."""
    rows = out["instruments"]
    studied = [r for r in rows if r.get("status") == "STUDIED"]
    sides = [(r["instrument"], r.get("sides") or {}) for r in studied]
    geo = [g for r in studied for g in (r.get("geometry_sweep") or [])]
    hurdle = [(r["instrument"], r.get("hurdle_bands") or []) for r in studied]

    def q(question: str, answer: str) -> dict:
        return {"question": question, "answer": answer}

    ce_better = [
        inst for inst, s in sides
        if (s.get("CE") or {}).get("avg_net_r") is not None
        and (s.get("PE") or {}).get("avg_net_r") is not None
        and s["CE"]["avg_net_r"] > s["PE"]["avg_net_r"]
    ]
    best_geo = min(
        (g for g in geo if g.get("avg_net_r") is not None),
        key=lambda g: -g["avg_net_r"], default=None,
    )
    cheap = [
        (inst, b) for inst, bands in hurdle for b in bands
        if b.get("band", "").startswith("hurdle_0") and b.get("trades", 0) > 0
    ]
    return [
        q("Can a CE/PE strategy be priced from the stored books at all?",
          f"Yes for {len(studied)} instrument(s) on real ask-in/bid-out quotes; "
          f"{len(rows) - len(studied)} were reported and not scored."),
        q("Is buying the ATM option profitable on the captured window?",
          out["conclusion"]["headline"]),
        q("CE or PE?",
          f"CE was better on {len(ce_better)} of {len(sides)} studied instruments; "
          "the per-instrument table carries both, and neither side is claimed as "
          "generally superior on a window this short."),
        q("Which stop band came closest?",
          "none produced a positive base rate" if best_geo is None else
          f"{best_geo['instrument']} at {best_geo['stop_band_pct_of_premium']}% of "
          f"premium: T1-first {best_geo['t1_before_sl_pct']}% against "
          f"{best_geo['breakeven_t1_pct_before_costs']}% needed before costs, "
          f"avg net {best_geo['avg_net_r']}R."),
        q("Does the ≤3% break-even hurdle finding from the journal reproduce here?",
          "no cheap-hurdle cohort had a sample at all" if not cheap else
          "; ".join(
              f"{inst} {b['band']}: {b.get('trades')} trades, "
              f"avg net {b.get('avg_net_r')}R"
              for inst, b in cheap[:6]
          )),
        q("How much of the cost is the spread itself?",
          "; ".join(
              f"{g['instrument']} {g['stop_band_pct_of_premium']}%: spread is "
              f"{g['spread_as_fraction_of_risk']}x risk, all costs "
              f"{g['cost_as_fraction_of_risk']}x risk"
              for g in geo[:6]
          ) or "not measurable"),
        q("Was any strategy promoted?",
          "No. Nothing in Phase 25 writes to a signal, a book or the order path, "
          "and the best verdict this study can produce is a research lead."),
        q("What would change the answer?",
          "A longer capture. Every result here is bounded by a window of weeks, "
          "and the same code re-run on a longer store is the only route to a "
          "validated answer."),
    ]


def run(*, quick: bool = False, instruments: list[str] | None = None,
        db_path: str | None = None, out_dir: str | None = None) -> dict:
    """Run the captured-window study and write every artefact.

    ``db_path`` is injectable so the pipeline can be exercised end to end on a
    purpose-built store; the default is the live capture store.
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
            "paper_only": True,
        }
        out["answers"] = []
        _write("p25_study_report.json", out, out_dir)
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
            "pool": None,
        })

    ranked = rank.rank(ranked_all)
    out = {
        "version": VERSION,
        "generated_at": int(started),
        "quick_mode": bool(quick),
        "coverage": cov,
        "window_claim": WINDOW_CLAIM,
        "geometry": {
            "stop_pct_of_premium_bands": [
                round(100.0 * b, 1) for b in outcomes.STOP_BANDS
            ],
            "t1_r": outcomes.T1_R,
            "t2_r": outcomes.T2_R,
            "t3_r": outcomes.T3_R,
            "horizon_seconds": outcomes.HORIZON_SEC,
            "max_forward_quotes": outcomes.MAX_STEPS,
            "entry": "ask of a later stored quote, plus configured slippage",
            "exit": "bid of a stored quote, minus configured slippage",
            "slippage_pct_per_side": round(100.0 * outcomes.slippage_pct(), 4),
            "costs_charged": (
                "brokerage + statutory only; the spread is paid in the prices, "
                "so charging the cost model's spread term as well would pay it "
                "twice"
            ),
            "split": (
                f"chronological by session: {int(100 * discover.DEV_FRAC)}% "
                f"development / {int(100 * discover.VAL_FRAC)}% validation / "
                "remainder holdout"
            ),
        },
        "instruments": rows,
        "ranked": ranked,
        "hypotheses_evaluated": tests,
        "paper_only": True,
    }
    out["conclusion"] = conclusion(rows, ranked)
    out["answers"] = answers(out)
    out["runtime_seconds"] = round(time.time() - started, 1)

    artefacts = {
        "report": _write("p25_study_report.json", out, out_dir),
        "coverage": _write("p25_coverage.json", cov, out_dir),
        "ranked": _write("p25_ranked_cohorts.json", ranked, out_dir),
        "sides": _write("p25_ce_vs_pe.json", [
            {"instrument": r["instrument"], "sides": r.get("sides"),
             "time_of_day": r.get("time_of_day")}
            for r in rows if r.get("status") == "STUDIED"
        ], out_dir),
        "geometry_sweep": _write("p25_geometry_sweep.json", [
            g for r in rows for g in (r.get("geometry_sweep") or [])
        ], out_dir),
        "hurdle_bands": _write("p25_hurdle_bands.json", [
            {"instrument": r["instrument"], "bands": r.get("hurdle_bands")}
            for r in rows if r.get("status") == "STUDIED"
        ], out_dir),
        "rejected_log": _write("p25_rejected.json", [
            {"strategy_id": r["strategy_id"], "instrument": r["instrument"],
             "option_type": r["option_type"],
             "stop_band_pct_of_premium": r["stop_band_pct_of_premium"],
             "conditions": r["conditions"], "status": r["status"],
             "failed_clauses": r["failed_clauses"]}
            for r in ranked if r["status"] != RESEARCH_LEAD
        ], out_dir),
        "fingerprints": _write("p25_fingerprints.json", [
            r["fingerprint"] for r in ranked if r["status"] == RESEARCH_LEAD
        ] or {"fingerprints": [], "note": "no cohort reached RESEARCH_LEAD, so "
              "nothing is published even as a paper lead"}, out_dir),
    }

    # Per-candidate rows for the studied instruments, at the frozen base band.
    if not quick:
        for r in rows:
            if r.get("status") != "STUDIED":
                continue
            inst = r["instrument"]
            chain = books.load_chain(inst, db_path=db_path)
            series = underlying.load_series(inst, db_path=db_path)
            p = pool.build(inst, chain=chain, series=series,
                           stop_pct=outcomes.STOP_PCT)
            if p is None:
                continue
            masks = conditions.masks(p.feat, p.side)
            artefacts[f"candidates_{inst}"] = write_candidate_rows(
                p, masks, outcomes.STOP_PCT, ranked, out_dir
            )
    out["artefacts"] = artefacts
    _write("p25_study_report.json", out, out_dir)
    return out


def latest() -> dict | None:
    path = os.path.join(_abs(ARTEFACT_DIR), "p25_study_report.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def latest_ranked() -> list[dict]:
    path = os.path.join(_abs(ARTEFACT_DIR), "p25_ranked_cohorts.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    return rows if isinstance(rows, list) else []


def latest_geometry() -> list[dict]:
    path = os.path.join(_abs(ARTEFACT_DIR), "p25_geometry_sweep.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    return rows if isinstance(rows, list) else []


__all__ = [
    "run", "latest", "latest_ranked", "latest_geometry", "coverage",
    "conclusion", "answers", "write_candidate_rows", "artefact_dir",
    "jsonable", "jsonable_dict",
]
