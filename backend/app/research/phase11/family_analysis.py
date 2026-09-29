"""Phase 11 §9-11 — premiums, entry quality and profit capture, per family.

RESEARCH ONLY. Each block reuses the Phase 9/10 measurement code and runs it
inside one family at a time, for the same reason the score block does: a cheap
premium means something different on a NIFTY weekly at ₹18 than on a GOLD leg at
₹2,200, and one pooled premium-band table answers neither question.

Nothing here changes the production premium floor, the strike selector, the entry
logic or any exit. §11 in particular collects the excursion data that a
hold-until-peak or partial-exit policy would need and explicitly does not
implement one: a policy fitted on five sessions of the same excursions it is
scored against is an in-sample result, and the spec forbids it until the
chronological evidence exists.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from app.research.phase9 import entryq
from app.research.phase9.strikes import PREMIUM_BANDS
from app.research.phase9.findings9 import band_label
from app.research.phase10 import capture10, premiums

from .families import FAMILIES, family_of, split

BANDS = tuple(band_label(lo, hi) for lo, hi in PREMIUM_BANDS)

# §11's list of policies that stay unimplemented until the evidence exists.
DEFERRED_POLICIES = ("HOLD_UNTIL_PEAK", "EXPIRY_SPECIFIC_EXIT", "TRAILING_EXIT",
                     "PARTIAL_EXIT")


def premiums_by_family(signals: list[dict], trade_rows: list[dict],
                       sessions: int) -> dict:
    """§9 — the premium-band table, per family."""
    sig_by_family: dict[str, list[dict]] = {f: [] for f in FAMILIES}
    for s in signals:
        sig_by_family[family_of(s.get("instrument"))].append(s)
    trades = split(trade_rows)
    out: dict[str, dict] = {}
    for fam in FAMILIES:
        sigs, rows = sig_by_family[fam], trades.get(fam) or []
        if not sigs and not rows:
            out[fam] = {"measurable": False,
                        "reason": "no signal or resolved trade in this family"}
            continue
        block = premiums.study(sigs, rows, sessions)
        out[fam] = {"measurable": True, "signals": len(sigs),
                    "resolved": len(rows), **block}
    return {
        "bands": list(BANDS),
        "by_family": out,
        "answer_index": (out["INDEX_OPTIONS"].get("cheap_premium_answer")
                         if out["INDEX_OPTIONS"].get("measurable")
                         else out["INDEX_OPTIONS"]["reason"]),
        "answer_mcx": (out["MCX_OPTIONS"].get("cheap_premium_answer")
                       if out["MCX_OPTIONS"].get("measurable")
                       else out["MCX_OPTIONS"]["reason"]),
        "production_premium_floor": "unchanged. This block measures what the floor "
                                    "excluded and what the excluded legs did; it "
                                    "does not move it",
    }


def entry_quality_by_family(trade_rows: list[dict], pairs: Sequence[tuple],
                            entry_of: Callable[[dict], dict | None],
                            sessions: int, contamination_block: dict) -> dict:
    """§10 — entry classification against outcome, per family."""
    rows = [entryq.row_of(r, ev, entry_of(r))
            for r, (ev, _) in zip(trade_rows, pairs, strict=True)]
    # entryq rows carry their own instrument, so they can be tagged directly.
    for row in rows:
        row["family"] = family_of(row.get("instrument"))
    by_family = split(rows)
    out: dict[str, dict] = {}
    for fam in FAMILIES:
        sub = by_family.get(fam) or []
        out[fam] = ({"measurable": False,
                     "reason": "no resolved trade in this family"} if not sub
                    else {"measurable": True, "resolved": len(sub),
                          **entryq.study(sub, sessions, contamination_block)})
    return {
        "classes": list(entryq.CLASSES),
        "by_family": out,
        "note": "chase classification is Phase 7's, unchanged. What is new is that "
                "an index chase and an MCX chase are no longer averaged together",
    }


def capture_by_family(trade_rows: list[dict], sessions: int,
                      missing_bar_pct: float | None) -> dict:
    """§11 — excursion, capture and give-back, per family."""
    by_family = split(trade_rows)
    out: dict[str, dict] = {}
    for fam in FAMILIES:
        sub = by_family.get(fam) or []
        out[fam] = ({"measurable": False,
                     "reason": "no resolved trade in this family"} if not sub
                    else {"measurable": True,
                          **capture10.study(sub, sessions, missing_bar_pct)})
    return {
        "by_family": out,
        "deferred_policies": list(DEFERRED_POLICIES),
        "why_deferred": "each of these is an exit policy, and an exit policy fitted "
                        "on the same excursions it is scored on always wins. They "
                        "stay unimplemented until they hold on sessions they have "
                        "never seen",
        "collected_for_later": ["mfe_r", "mae_r", "mfe_capture_pct", "giveback_r",
                                "time_to_mfe_sec", "time_from_mfe_to_exit_sec",
                                "premium_expansion", "expiry_class",
                                "spread_cost_r", "exit_reason"],
    }
