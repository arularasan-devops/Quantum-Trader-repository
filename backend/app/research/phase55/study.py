"""Run the frozen Phase 53 and Phase 54 families across the audited universe.

This module contains no mechanism of its own. It calls
``phase53.study.run`` and ``phase54.study.run`` with the instruments the audit
admitted, and then does the two things those phases cannot do for themselves
because they were frozen when the universe was two names:

* **re-corrects over the whole universe.** Phase 53 corrects within one
  instrument's registered grid; searching twenty instruments is twenty times as
  many chances at the same fluke. So every row's discovery p-value is put
  through Benjamini–Hochberg once more with the denominator set to the total
  number of rows graded across the universe, and the result is recorded as a
  separate ``universe_fdr_pass`` field. The phases' own verdicts are left
  exactly as they computed them; nothing is overwritten;
* **answers A-versus-B.** The question is whether four zero results came from
  weak mechanisms or from a universe too narrow to grade them. That is a
  statement about event counts and about how many instruments show the effect in
  the same direction — not about the best row. So the summary reports the event
  count per instrument, how many instruments are gradeable at all, and how many
  are holdout-positive, and it refuses to promote anything on a pooled number.

A candidate is promoted only if it is ``ROBUST_CANDIDATE`` in its own phase
**and** survives the universe-wide correction **and** is not the only instrument
showing the effect. The last clause is deliberate: a single instrument out of
twenty is what a false positive looks like at this denominator.
"""
from __future__ import annotations

from app.research.phase53 import FDR_ALPHA as P53_ALPHA
from app.research.phase53 import ROBUST_CANDIDATE as P53_ROBUST
from app.research.phase53 import stats as p53stats
from app.research.phase53 import study as p53study
from app.research.phase54 import ROBUST_CANDIDATE as P54_ROBUST
from app.research.phase54 import study as p54study
from app.research.phase55 import FAMILIES, fingerprint

FAMILY_P53, FAMILY_P54 = FAMILIES

PROMOTED = "PROMOTED_ROBUST_CANDIDATE"
REJECTED_UNIVERSE_FDR = "REJECTED_ON_UNIVERSE_WIDE_CORRECTION"
REJECTED_SINGLE_INSTRUMENT = "REJECTED_SINGLE_INSTRUMENT_EFFECT"
NOT_A_CANDIDATE = "NOT_A_CANDIDATE_IN_ITS_OWN_PHASE"

DISCOVERY = "DISCOVERY"


def _discovery_p(row: dict) -> float:
    cell = (row.get("per_partition") or {}).get(DISCOVERY) or {}
    value = cell.get("p_value_one_sided")
    return float(value) if value is not None else 1.0


def universe_correction(rows: list[dict], alpha: float = P53_ALPHA) -> list[bool]:
    """Benjamini-Hochberg over every row graded anywhere in the universe."""
    p_values = [_discovery_p(r) for r in rows]
    return p53stats.benjamini_hochberg(p_values, tests=len(rows), alpha=alpha)


def _rows_of(result: dict) -> list[dict]:
    return [row for inst in result.get("instruments", []) for row in inst.get("rows", [])]


def _robust_label(family: str) -> str:
    return P53_ROBUST if family == FAMILY_P53 else P54_ROBUST


def grade_family(family: str, result: dict) -> dict:
    """Attach the universe-wide correction and the promotion verdict."""
    rows = _rows_of(result)
    passes = universe_correction(rows)
    robust = _robust_label(family)

    # Which instruments show a positive untouched holdout at all. A candidate
    # that is the only one of twenty is not promoted, however good its row.
    holdout_instruments = {
        inst["instrument"]
        for inst in result.get("instruments", [])
        if int(inst.get("totals", {}).get("HOLDOUT_POSITIVE") or 0) > 0
    }

    graded: list[dict] = []
    for row, ok in zip(rows, passes):
        row["universe_fdr_pass"] = bool(ok)
        own = row.get("final_status")
        if own != robust:
            verdict = NOT_A_CANDIDATE
        elif not ok:
            verdict = REJECTED_UNIVERSE_FDR
        elif len(holdout_instruments) < 2:
            verdict = REJECTED_SINGLE_INSTRUMENT
        else:
            verdict = PROMOTED
        row["phase55_verdict"] = verdict
        graded.append(row)

    return {
        "family": family,
        "fdr_denominator_universe": len(rows),
        "instruments_graded": [
            i["instrument"] for i in result.get("instruments", []) if i.get("eligible")
        ],
        "instruments_ineligible": [
            {"instrument": i["instrument"], "reason": i.get("eligibility")}
            for i in result.get("instruments", [])
            if not i.get("eligible")
        ],
        "holdout_positive_instruments": sorted(holdout_instruments),
        "promoted": [r for r in graded if r["phase55_verdict"] == PROMOTED],
        "totals": {
            **result.get("totals", {}),
            "UNIVERSE_FDR_PASS": sum(1 for r in graded if r["universe_fdr_pass"]),
            "PROMOTED": sum(1 for r in graded if r["phase55_verdict"] == PROMOTED),
        },
        "phase_fingerprint": result.get("fingerprint"),
        "result": result,
    }


def event_counts(family: str, result: dict) -> list[dict]:
    """Trades and independent sessions per instrument — the A-vs-B evidence."""
    out: list[dict] = []
    for inst in result.get("instruments", []):
        rows = inst.get("rows") or []
        trades = [int(r.get("trades") or 0) for r in rows]
        out.append({
            "family": family,
            "instrument": inst["instrument"],
            "eligible": bool(inst.get("eligible")),
            "parameterizations": len(rows),
            "event_families": int(inst.get("totals", {}).get("UNIQUE_EVENT_FAMILIES") or 0),
            "max_trades": max(trades) if trades else 0,
            "median_trades": sorted(trades)[len(trades) // 2] if trades else 0,
            "discovery_leads": int(inst.get("totals", {}).get("DISCOVERY_LEADS") or 0),
            "validation_positive": int(inst.get("totals", {}).get("VALIDATION_POSITIVE") or 0),
            "holdout_positive": int(inst.get("totals", {}).get("HOLDOUT_POSITIVE") or 0),
            "robust_own_phase": int(inst.get("totals", {}).get("ROBUST_CANDIDATES") or 0),
        })
    return out


def run(instruments: list[str], families: list[str] | None = None) -> dict:
    """The whole Phase 55 study over the admitted instruments."""
    wanted = list(families or FAMILIES)
    names = tuple(instruments)
    out: dict = {
        "fingerprint": fingerprint(),
        "instruments": list(names),
        "families": [],
        "event_counts": [],
    }
    for family in wanted:
        if family == FAMILY_P53:
            result = p53study.run(names)
        elif family == FAMILY_P54:
            result = p54study.run(names)
        else:
            raise ValueError(f"unknown family {family!r}")
        out["families"].append(grade_family(family, result))
        out["event_counts"].extend(event_counts(family, result))

    out["totals"] = {
        "INSTRUMENTS_REQUESTED": len(names),
        "INSTRUMENTS_GRADED": len({
            i for fam in out["families"] for i in fam["instruments_graded"]
        }),
        "PARAMETERIZATIONS": sum(
            int(f["totals"].get("TOTAL_PARAMETERIZATIONS") or 0)
            for f in out["families"]
        ),
        "HOLDOUT_POSITIVE": sum(
            int(f["totals"].get("HOLDOUT_POSITIVE") or 0) for f in out["families"]
        ),
        "ROBUST_IN_OWN_PHASE": sum(
            int(f["totals"].get("ROBUST_CANDIDATES") or 0) for f in out["families"]
        ),
        "PROMOTED": sum(int(f["totals"].get("PROMOTED") or 0) for f in out["families"]),
    }
    return out
