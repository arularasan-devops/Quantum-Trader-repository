"""Phase 43 §4 — run the declared families in order and rank what survives.

Families run in the order they were asked for: A cost-aware selection, B
relative value, C vehicle selection, D regime and state. C adds no hypothesis of
its own — it compares the *same* family-A candidate across the two vehicles,
because a vehicle comparison in which each vehicle gets its own rule compares
rules, not vehicles.

Nothing in this module writes to any store. It returns a payload; the CLI prints
it and, only when asked, writes it under the research artefact directory.
"""
from __future__ import annotations

import numpy as np

from app.research import phase43
from app.research.phase24 import data as p24data
from app.research.phase43 import (
    answerability,
    evaluate,
    freeze,
    mechanisms,
    pair,
    registry,
)
from app.research.phase43.evaluate import DEV, HOLD, VAL


def _stress(book_by_mult: dict[float, object], candidate: dict) -> list[dict]:
    """The same candidate's holdout under each declared cost multiplier."""
    rows: list[dict] = []
    for mult, book in sorted(book_by_mult.items()):
        row = book.score(candidate, book.windows[HOLD])
        rows.append({
            "variant": f"cost x{mult:g}",
            "cost_multiplier": mult,
            "trades": row.get("trades", 0),
            "net_total": row.get("net_total"),
            "expectancy": row.get("expectancy"),
            "survives": bool((row.get("net_total") or 0) > 0),
        })
    return rows


def _pair_stress(a: str, b: str, candidate: dict,
                 window_sessions: np.ndarray) -> list[dict]:
    rows: list[dict] = []
    for mult in phase43.COST_MULTIPLIERS:
        pb = evaluate.PairBook(a, b, mult)
        row = pb.score(pb.resolved(candidate), window_sessions)
        rows.append({
            "variant": f"cost x{mult:g}",
            "cost_multiplier": mult,
            "trades": row.get("trades", 0),
            "net_total": row.get("net_total"),
            "expectancy": row.get("expectancy"),
            "survives": bool((row.get("net_total") or 0) > 0),
        })
    return rows


def _row(candidate: dict, splits: dict, stress: list[dict], sessions: int) -> dict:
    verdict = evaluate.status(splits, stress, sessions)
    return {
        **{k: v for k, v in candidate.items() if k != "conditions"},
        "conditions": list(candidate.get("conditions") or []),
        "splits": splits,
        "cost_stress": stress,
        "sessions": sessions,
        **verdict,
    }


def _families_ab(
    instruments: tuple[str, ...],
) -> tuple[list[dict], dict, list[dict]]:
    """Family A on every instrument, and family D on A's development winner."""
    rows: list[dict] = []
    chosen: dict[str, dict] = {}
    skipped: list[dict] = []
    for inst in instruments:
        books = {m: evaluate.Book(inst, m) for m in phase43.COST_MULTIPLIERS}
        base = books[1.0]
        declared = [c for c in mechanisms.declared((inst,))]
        scored: list[tuple[float, dict]] = []
        for cand in declared:
            splits = {w: base.score(cand, base.windows[w]) for w in evaluate.WINDOWS}
            sessions = int(np.unique(
                base.pool.session[base.cohort(cand)]
            ).size)
            stress = _stress(books, cand)
            row = _row(cand, splits, stress, sessions)
            rows.append(row)
            if not cand["is_control"] and splits[DEV].get("trades", 0) >= \
                    phase43.MIN_TRADES:
                scored.append((float(splits[DEV].get("net_total") or 0.0), cand))
        # Family D splits the arm that looked best on the DEVELOPMENT window
        # only. Choosing it on the holdout would make every state's holdout
        # number a selected one.
        if scored:
            scored.sort(key=lambda kv: kv[0], reverse=True)
            best = scored[0][1]
            chosen[inst] = best
            parent = base.cohort(best)
            for cand in mechanisms.regime_candidates(inst, best):
                # A state that selects the parent arm's whole cohort is the
                # parent arm under another name: it would print the same numbers
                # twice and add a hypothesis that tests nothing. Recorded as
                # skipped rather than measured, so the FDR denominator stays
                # honest in both directions.
                if np.array_equal(base.cohort(cand), parent):
                    skipped.append({
                        "candidate": cand["candidate"],
                        "reason": "identical cohort to its parent arm",
                    })
                    continue
                splits = {w: base.score(cand, base.windows[w])
                          for w in evaluate.WINDOWS}
                sessions = int(np.unique(
                    base.pool.session[base.cohort(cand)]
                ).size)
                rows.append(_row(cand, splits, _stress(books, cand), sessions))
    return rows, chosen, skipped


def _family_b(a: str, b: str) -> list[dict]:
    rows: list[dict] = []
    pb = evaluate.PairBook(a, b, 1.0)
    windows = evaluate.split_sessions(pb.aligned.session)
    session_sets = {
        w: np.unique(pb.aligned.session[windows[w]]) for w in evaluate.WINDOWS
    }
    for cand in pair.declared(a, b):
        res = pb.resolved(cand)
        splits = {w: pb.score(res, session_sets[w]) for w in evaluate.WINDOWS}
        sessions = int(np.unique(res["session"]).size) if res["net"].size else 0
        stress = _pair_stress(a, b, cand, session_sets[HOLD])
        rows.append(_row(cand, splits, stress, sessions))
    return rows


def _family_c(rows: list[dict], chosen: dict[str, dict]) -> dict:
    """Family C: the same mechanism, priced on each vehicle it can be traded on.

    Only the futures vehicles are compared, because that is all this dataset
    has. The option half is UNMEASURED and says why, rather than being modelled.
    """
    by_id = {r["candidate"]: r for r in rows}
    compared: list[dict] = []
    ids = sorted({c["candidate"].rsplit("_", 1)[0] for c in chosen.values()})
    for stem in ids:
        for inst in sorted(chosen):
            key = f"{stem}_{inst}"
            row = by_id.get(key)
            if row is None:
                continue
            compared.append({
                "mechanism": stem,
                "vehicle": f"{inst}_FUTURES",
                "instrument": inst,
                "holdout_net": row["splits"][HOLD].get("net_total"),
                "holdout_expectancy": row["splits"][HOLD].get("expectancy"),
                "holdout_trades": row["splits"][HOLD].get("trades", 0),
                "status": row["status"],
            })
    return {
        "measured": compared,
        "unmeasured": [{
            "comparison": "futures versus CE/PE at the same decision instants",
            "status": phase43.UNMEASURED,
            "reason": phase43.NO_OPTION_HISTORY,
        }, {
            "comparison": "any midpoint-executed alternative",
            "status": "REFUSED_BY_DESIGN",
            "reason": (
                "no quoted book exists in this dataset, so a midpoint would be "
                "invented rather than observed"
            ),
        }],
    }


def ranking(rows: list[dict]) -> list[dict]:
    """The one concise table, ordered leads first then by holdout net."""
    order = {phase43.HISTORICAL_LEAD: 0, phase43.REQUIRES_MORE_DATA: 1,
             phase43.REJECTED: 2}
    def key(r: dict) -> tuple:
        return (order.get(r["status"], 3),
                -float(r["splits"][HOLD].get("net_total") or 0.0))
    out = []
    for r in sorted(rows, key=key):
        stressed = [s for s in r["cost_stress"] if s["cost_multiplier"] > 1.0]
        # "survives" is only worth printing for a candidate that was positive at
        # the baseline cost; for the rest the column would read as a pass on
        # something that had already failed.
        if (r["splits"][HOLD].get("net_total") or 0) <= 0:
            stress_label = "n/a"
        elif stressed and all(s["survives"] for s in stressed):
            stress_label = "survives"
        else:
            stress_label = "fails"
        out.append({
            "candidate": r["candidate"],
            "instrument": r["instrument"],
            "mechanism": r["mechanism"],
            "train_net": r["splits"][DEV].get("net_total"),
            "validation_net": r["splits"][VAL].get("net_total"),
            "holdout_net": r["splits"][HOLD].get("net_total"),
            "profit_factor": r["splits"][HOLD].get("profit_factor"),
            "trades": r["splits"][HOLD].get("trades", 0),
            "sessions": r["sessions"],
            "cost_stress": stress_label,
            "status": r["status"],
            "reason": r["reason"],
        })
    return out


def run() -> dict:
    """The whole study: answerability, four families, FDR, ranking, registry."""
    amap = answerability.map_families()
    instruments = tuple(amap["instruments_with_five_year_history"])
    rows, chosen, skipped = _families_ab(instruments)
    if len(instruments) >= 2:
        rows += _family_b(instruments[0], instruments[1])
    evaluate.fdr(rows)
    leads = [r for r in rows if r["status"] == phase43.HISTORICAL_LEAD]
    return {
        "phase": "43",
        "version": phase43.VERSION,
        "status": phase43.RESEARCH_ONLY,
        "fingerprint": freeze.fingerprint(),
        "answerability": amap,
        "candidates": rows,
        "ranking": ranking(rows),
        "family_c_vehicle_comparison": _family_c(rows, chosen),
        "development_selected_arm": {k: v["candidate"] for k, v in chosen.items()},
        "skipped_as_redundant": skipped,
        "leads": [r["candidate"] for r in leads],
        "registry": [registry.entry(r) for r in leads],
        "hypotheses_evaluated": sum(1 for r in rows if r["fdr_tested"]),
        "dataset": {
            "series": amap["five_year_series"],
            "source": sorted(p24data.BACKTEST_FILES.values()),
        },
    }
