"""Does a modelled T1 change the grade? — the `room` A/B, research only.

The grading audit found that ``room`` was the sole blocker on candidates that were
otherwise clear, and the cause was structural rather than statistical: ``room``
scores the option's distance to T1, T1 came only from the production plan, and the
production plan exists only for the leg the engine is currently recommending. Every
other recorded candidate was therefore unmeasurable by construction, and A+ — which
requires all six components measured — could never be awarded to it.

``reach`` now derives a T1 from the setup's own expected move when the engine
published none, and stamps ``t1_basis``. This module measures what that changed, on
rows already on disk, so the effect can be read before another session is spent:

  v1  the label recorded at the time
  v2  the label the same row gets when the derived target is allowed

Two guards make the answer trustworthy rather than merely encouraging:

* **Regression.** On rows that already had an engine target, v2 must reproduce v1
  exactly. Any disagreement is reported as a mismatch and invalidates the run —
  it would mean the change altered grading where it was supposed to be inert.
* **Promotability.** A row graded against a derived target is counted separately
  and is never promotable. It cannot enter the costed book, so it cannot reach a
  promotion sample; widening what can be *graded* must not widen what can be
  *believed*.

Nothing here writes, and no threshold is touched.
"""
from __future__ import annotations

from app.research.phase17 import aplus, reach, schema

# Verdicts for the run as a whole.
CHANGED = "MODELLED_T1_ADDS_CANDIDATES"
NO_CHANGE = "MODELLED_T1_CHANGES_NOTHING"
INVALID = "REGRESSION_ON_ENGINE_TARGET_ROWS"


def _regrade(row: dict) -> tuple[schema.Observation, dict]:
    """Re-run reach and A+ on one recorded row under the current rule."""
    obs = schema.Observation.from_dict(row)
    recorded_reach = row.get("reach") or {}
    mfe = recorded_reach.get("option_mfe")
    obs.reach = reach.assess(
        obs.selected, obs.plan, obs.economics,
        mfe_history=mfe if isinstance(mfe, (int, float)) else None,
    )
    graded = aplus.score(obs)
    obs.aplus = graded
    return obs, graded


def replay(rows: list[dict]) -> dict:
    """v1 (recorded) versus v2 (modelled T1 allowed) over recorded observations."""
    basis_tally: dict[str, int] = {}
    label_moves: dict[str, int] = {}
    mismatches: list[dict] = []
    v1_a_plus: set[str] = set()
    v2_a_plus: set[str] = set()
    v2_promotable: set[str] = set()
    room_measured_v1 = room_measured_v2 = 0
    still_unmeasured: dict[str, int] = {}
    considered = 0

    for row in rows:
        recorded = str((row.get("aplus") or {}).get("a_plus_label") or "")
        if not recorded:
            # Never graded at the time — there is no v1 to compare against.
            continue
        considered += 1
        key = _opportunity(row)
        if recorded == aplus.A_PLUS:
            v1_a_plus.add(key)
        if isinstance((row.get("reach") or {}).get("t1_score"), (int, float)):
            room_measured_v1 += 1

        obs, graded = _regrade(row)
        basis = str(graded.get("t1_basis") or reach.BASIS_NONE)
        basis_tally[basis] = basis_tally.get(basis, 0) + 1
        now = str(graded.get("a_plus_label") or "")
        if isinstance((obs.reach or {}).get("t1_score"), (int, float)):
            room_measured_v2 += 1
        else:
            # A derived target does not make every row measurable: a row whose
            # book was never captured is still refused, and saying which reason
            # remains is the difference between "the fix did nothing" and "the fix
            # works but the capture is the next constraint".
            for why in ((obs.reach or {}).get("reasons") or ["UNKNOWN"]):
                if why == "T1_MODELLED_FROM_EXPECTED_MOVE":
                    continue
                still_unmeasured[str(why)] = still_unmeasured.get(str(why), 0) + 1
        if now == aplus.A_PLUS:
            v2_a_plus.add(key)
            if graded.get("promotable"):
                v2_promotable.add(key)
        if now != recorded:
            label_moves[f"{recorded}->{now}"] = (
                label_moves.get(f"{recorded}->{now}", 0) + 1
            )
            if basis == reach.BASIS_ENGINE:
                # The rule was supposed to be inert here.
                mismatches.append({
                    "observation_id": row.get("observation_id"),
                    "recorded": recorded,
                    "regraded": now,
                    "t1_basis": basis,
                })

    if mismatches:
        verdict = INVALID
    elif v2_a_plus - v1_a_plus:
        verdict = CHANGED
    else:
        verdict = NO_CHANGE

    return {
        "question": "Does allowing a modelled T1 change the grade?",
        "rows_considered": considered,
        "t1_basis": basis_tally,
        "room_measured_v1": room_measured_v1,
        "room_measured_v2": room_measured_v2,
        "room_still_unmeasured_because": dict(sorted(still_unmeasured.items())),
        "label_moves": dict(sorted(label_moves.items())),
        "a_plus_opportunities_v1": len(v1_a_plus),
        "a_plus_opportunities_v2": len(v2_a_plus),
        "a_plus_gained": sorted(v2_a_plus - v1_a_plus),
        "a_plus_lost": sorted(v1_a_plus - v2_a_plus),
        "promotable_opportunities_v2": len(v2_promotable),
        "regression_mismatches": mismatches[:20],
        "regression_mismatch_count": len(mismatches),
        "verdict": verdict,
        "research_only": True,
        "thresholds_unchanged": True,
        "note": (
            "A row graded against a derived target is research evidence only: it "
            "is refused admission to the costed paper book and cannot reach a "
            "promotion sample. Only ENGINE_TARGET rows are promotable."
        ),
    }


def _opportunity(row: dict) -> str:
    return "|".join(str(x) for x in (
        row.get("session"),
        row.get("instrument"),
        (row.get("selected") or {}).get("symbol"),
        row.get("direction"),
    ))
