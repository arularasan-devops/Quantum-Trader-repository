"""§18 missed winners vs avoided losers, and §20 the data-integrity guard.

A selector is usually judged by what it kept. That hides the only question that
decides whether selectivity is worth anything: a filter that avoids 100 losers
by also refusing 100 winners has done nothing, and it looks *excellent* when
only the kept trades are reported.

So every rejection is priced here. For each rejected candidate that would have
reached target, the reason it was rejected is recorded; for each selected
candidate that failed, how it failed. The two are reported against each other,
in R, so "this filter avoided more than it missed" is a number rather than a
claim.

The integrity guard sits in this module because it has to run before any of
those figures are computed: a single row with risk 0 produces an infinite R and
silently rewrites every aggregate above it. Prior reports in this system
carried impossible multi-million-R cohorts for exactly that reason. Bad rows are
counted and set aside, never repaired and never averaged.
"""
from __future__ import annotations

import math

DATA_ERROR = "DATA_ERROR"

# The outcome fields a graded candidate cannot be missing. A row without a
# resolved outcome is not a zero-R trade, it is an unmeasured one.
REQUIRED = ("r", "risk", "entry", "stop", "target", "session")


def _finite(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def row_errors(row: dict) -> list[str]:
    """Every integrity failure in one candidate row, named."""
    bad = []
    for field in REQUIRED:
        if row.get(field) is None:
            bad.append(f"missing_{field}")
    risk = row.get("risk")
    if risk is not None:
        if not _finite(risk):
            bad.append("risk_not_finite")
        elif float(risk) <= 0:
            # Risk is the denominator of R. Zero or negative means the stop sat
            # at or beyond the entry, which is not a trade that could have been
            # placed.
            bad.append("risk_not_positive")
    r = row.get("r")
    if r is not None and not _finite(r):
        bad.append("r_not_finite")
    entry, stop, target = row.get("entry"), row.get("stop"), row.get("target")
    if _finite(entry) and _finite(stop) and float(entry) == float(stop):
        bad.append("stop_equals_entry")
    if _finite(entry) and _finite(target) and float(entry) == float(target):
        bad.append("target_equals_entry")
    return bad


def guard(rows: list[dict]) -> dict:
    """Split a pool into rows fit to grade and rows that are corrupt.

    Returns both, plus a breakdown by failure kind, so a pool that is 30%
    unusable is visible as that rather than as a confident result computed from
    the remaining 70%.
    """
    clean, errors = [], []
    kinds: dict[str, int] = {}
    for row in rows:
        bad = row_errors(row)
        if bad:
            errors.append({"session": row.get("session"),
                           "entry_ts": row.get("entry_ts"),
                           "instrument": row.get("instrument"),
                           "failures": bad})
            for kind in bad:
                kinds[kind] = kinds.get(kind, 0) + 1
        else:
            clean.append(row)
    total = len(rows)
    return {
        "rows": total,
        "clean": len(clean),
        "data_errors": len(errors),
        "data_error_pct": round(100.0 * len(errors) / total, 2) if total else 0.0,
        "by_failure": dict(sorted(kinds.items())),
        "examples": errors[:10],
        "clean_rows": clean,
        "note": (
            "rows failing the guard are excluded from expectancy, profit factor, "
            "scoring and every A+ verdict, and counted here instead. They are "
            "never repaired: a candidate whose risk was zero had no placeable "
            "trade behind it, so inventing one would put a fabricated outcome "
            "into the evidence"),
    }


def _r(row: dict) -> float:
    return float(row["r"])


def _reached_t1(row: dict) -> bool:
    return row.get("exit_reason") == "TARGET"


def _hit_sl(row: dict) -> bool:
    return row.get("exit_reason") == "STOP"


def rejection_reasons(row: dict, rules: dict) -> list[str]:
    """Which of the selector's conditions this candidate failed.

    Reported as every failed condition rather than the first, because a
    candidate refused by five conditions is a different kind of rejection from
    one refused by a single marginal reading, and 'reason_rejected' collapsing
    to whichever condition happened to be checked first is how a filter gets
    blamed for rejections it did not cause.
    """
    return sorted(name for name, pred in rules.items() if not pred(row))


def attribute(trades: list[dict], keep, rules: dict | None = None) -> dict:
    """Price a selector's rejections against its selections.

    ``rules`` is the selector's individual conditions, used only to attribute
    each rejection to the condition(s) responsible. Omit it and the rejection
    cohort is still priced, just without the per-condition breakdown.
    """
    selected = [t for t in trades if keep(t)]
    rejected = [t for t in trades if not keep(t)]

    missed_winners = [t for t in rejected if _reached_t1(t)]
    avoided_losers = [t for t in rejected if _hit_sl(t)]
    selected_winners = [t for t in selected if _reached_t1(t)]
    selected_losers = [t for t in selected if _hit_sl(t)]

    missed_r = round(sum(_r(t) for t in missed_winners), 2)
    avoided_r = round(sum(_r(t) for t in avoided_losers), 2)

    by_reason: dict[str, dict] = {}
    if rules:
        for t in missed_winners:
            for reason in rejection_reasons(t, rules):
                slot = by_reason.setdefault(
                    reason, {"missed_winners": 0, "missed_r": 0.0,
                             "avoided_losers": 0, "avoided_r": 0.0})
                slot["missed_winners"] += 1
                slot["missed_r"] = round(slot["missed_r"] + _r(t), 2)
        for t in avoided_losers:
            for reason in rejection_reasons(t, rules):
                slot = by_reason.setdefault(
                    reason, {"missed_winners": 0, "missed_r": 0.0,
                             "avoided_losers": 0, "avoided_r": 0.0})
                slot["avoided_losers"] += 1
                slot["avoided_r"] = round(slot["avoided_r"] - _r(t), 2)
        for slot in by_reason.values():
            slot["net_r"] = round(slot["avoided_r"] - slot["missed_r"], 2)
            slot["worth_keeping"] = bool(slot["net_r"] > 0)

    # What refusing this cohort was worth per candidate refused. This is the
    # figure that decides whether the filter earns its place: the R the rejected
    # cohort would have produced, negated. Positive means refusing them helped.
    refused_r = round(sum(_r(t) for t in rejected), 2)
    return {
        "candidates": len(trades),
        "selected": len(selected),
        "rejected": len(rejected),
        "selection_pct": (round(100.0 * len(selected) / len(trades), 1)
                          if trades else 0.0),
        "missed_winners": len(missed_winners),
        "missed_winner_r": missed_r,
        "avoided_losers": len(avoided_losers),
        "avoided_loser_r": round(-avoided_r, 2),
        "net_rejection_r": round(-refused_r, 2),
        "rejection_r_per_candidate": (round(-refused_r / len(rejected), 4)
                                      if rejected else None),
        "selected_t1_pct": (round(100.0 * len(selected_winners) / len(selected), 1)
                            if selected else None),
        "selected_sl_pct": (round(100.0 * len(selected_losers) / len(selected), 1)
                            if selected else None),
        "rejected_t1_pct": (round(100.0 * len(missed_winners) / len(rejected), 1)
                            if rejected else None),
        "by_rejection_reason": dict(sorted(by_reason.items())),
        "missed_examples": [
            {"session": t.get("session"), "instrument": t.get("instrument"),
             "side": t.get("side"), "r": round(_r(t), 2),
             "mfe_r": t.get("mfe_r"), "reward_risk": t.get("reward_risk"),
             "reason_rejected": rejection_reasons(t, rules) if rules else None}
            for t in sorted(missed_winners, key=_r, reverse=True)[:10]
        ],
        "verdict": _verdict(missed_winners, avoided_losers, refused_r, rejected),
    }


def _verdict(missed: list[dict], avoided: list[dict], refused_r: float,
             rejected: list[dict]) -> str:
    if not rejected:
        return ("this selector rejects nothing, so it is not selecting — it "
                "cannot avoid a loser and cannot miss a winner")
    per = -refused_r / len(rejected)
    if abs(per) < 0.01:
        return (f"refusing {len(rejected)} candidates was worth "
                f"{per:+.4f}R each — the rejected cohort performed like the "
                "book as a whole, so the filtering is cosmetic: it avoided "
                f"{len(avoided)} losers and missed {len(missed)} winners of "
                "the same size")
    if per > 0:
        return (f"refusing {len(rejected)} candidates saved {per:+.3f}R each "
                f"({-refused_r:+.1f}R total): {len(avoided)} losers avoided "
                f"against {len(missed)} winners missed")
    return (f"refusing {len(rejected)} candidates COST {per:+.3f}R each "
            f"({-refused_r:+.1f}R total) — the cohort it refuses is better than "
            f"the one it keeps, so this selector is backwards: it missed "
            f"{len(missed)} winners to avoid {len(avoided)} losers")


def capture(trades: list[dict]) -> dict:
    """§14 hold/profit-capture facts for a selected cohort. Changes no exit.

    The two rows that matter are at the bottom: how often a candidate reached
    target and reversed anyway, and how often one went +0.5R and still ended a
    loss. Those are the trades where the signal was right and the exit gave the
    money back, and they are invisible in an expectancy figure.
    """
    n = len(trades)
    if not n:
        return {"trades": 0}
    mfes = [float(t["mfe_r"]) for t in trades if _finite(t.get("mfe_r"))]
    maes = [float(t["mae_r"]) for t in trades if _finite(t.get("mae_r"))]
    t1_then_lost = sum(1 for t in trades
                       if _reached_t1(t) and _r(t) <= 0)
    half_r_then_lost = sum(1 for t in trades
                           if _finite(t.get("mfe_r"))
                           and float(t["mfe_r"]) >= 0.5 and _r(t) <= 0)
    return {
        "trades": n,
        "t1_pct": round(100.0 * sum(1 for t in trades if _reached_t1(t)) / n, 1),
        "sl_pct": round(100.0 * sum(1 for t in trades if _hit_sl(t)) / n, 1),
        "mean_mfe_r": round(sum(mfes) / len(mfes), 3) if mfes else None,
        "mean_mae_r": round(sum(maes) / len(maes), 3) if maes else None,
        # Of the best excursion the trade offered, how much the exit actually
        # kept. Below ~50% the exit is the constraint, not the entry.
        "capture_pct": (round(100.0 * sum(_r(t) for t in trades) / sum(mfes), 1)
                        if mfes and sum(mfes) > 0 else None),
        "reached_t1_then_ended_negative": t1_then_lost,
        "reached_half_r_then_lost": half_r_then_lost,
        "reached_half_r_then_lost_pct": round(100.0 * half_r_then_lost / n, 1),
    }
