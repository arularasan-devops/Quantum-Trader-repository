"""The promotion rule, written down before the data arrives.

A single good day is not evidence, and neither is a good cumulative number that
rests on one avoided disaster. This module states the bar once, so the answer
cannot be negotiated later, and it is allowed to return NOT_PROVEN forever.
"""
from __future__ import annotations

from app.research.phase23 import hurdle as hurdle_mod, report, store

PROMOTE = "READY_FOR_HUMAN_REVIEW"
NOT_PROVEN = "NOT_PROVEN"
INSUFFICIENT = "INSUFFICIENT_DATA"
HARMFUL = "VALIDATED_NEGATIVE"

# Deliberately conservative and fixed in advance.
MIN_TRADES = 60          # resolved, measured, engine-taken option legs
MIN_REFUSED = 15         # trades the cutoff actually declined
MIN_SESSIONS = 5         # distinct session dates, so it is never one day
MIN_MEASURED_PCT = 60.0  # share of opportunities with a usable book


def evaluate(rows: list[dict] | None = None) -> dict:
    data = rows if rows is not None else store.rows()
    built = report.build(data)
    cov = built["coverage"]
    pool = [r for r in report.taken(data)
            if r.get("hurdle_status") == hurdle_mod.MEASURED]
    sessions = {report.ist_date(r.get("ts")) for r in pool}
    sessions.discard(None)

    candidates = []
    for row in built["sweep"]:
        t = row["threshold_pct"]
        stab = report.stability(data, t)
        blocking = []
        if len(pool) < MIN_TRADES:
            blocking.append(
                f"sample {len(pool)} measured resolved trades < {MIN_TRADES}")
        if len(sessions) < MIN_SESSIONS:
            blocking.append(f"{len(sessions)} session(s) < {MIN_SESSIONS}")
        if (cov.get("measured_pct") or 0) < MIN_MEASURED_PCT:
            blocking.append(
                f"measured book on {cov.get('measured_pct')}% of opportunities "
                f"< {MIN_MEASURED_PCT}%")
        if row["rejected"] < MIN_REFUSED:
            blocking.append(f"only {row['rejected']} refused < {MIN_REFUSED}")
        if row["net_effect"] <= 0:
            blocking.append("net effect not positive")
        if row["net_effect_excluding_largest"] <= 0:
            blocking.append("net effect depends on one outlier")
        if row["vs_existing_net_pnl"] <= 0:
            blocking.append("does not beat the unchanged live baseline")
        if (row["profit_factor"] or 0) <= 1:
            blocking.append("profit factor not above 1")
        if stab.get("status") != "OK" or not stab.get("same_sign"):
            blocking.append("not chronologically stable across both halves")
        candidates.append({
            "threshold_pct": t,
            "net_effect": row["net_effect"],
            "vs_existing_net_pnl": row["vs_existing_net_pnl"],
            "profit_factor": row["profit_factor"],
            "blocking_reasons": blocking,
            "passes": not blocking,
        })

    passing = [c for c in candidates if c["passes"]]
    if not pool:
        status = INSUFFICIENT
        chosen = None
    elif passing:
        status = PROMOTE
        # Among passing cutoffs prefer the middle of the surviving plateau
        # rather than the single best number, which is how a threshold gets
        # fitted to noise.
        thresholds = sorted(c["threshold_pct"] for c in passing)
        chosen = thresholds[len(thresholds) // 2]
    else:
        chosen = None
        harmful = all((c["net_effect"] < 0) for c in candidates) and \
            len(pool) >= MIN_TRADES
        status = HARMFUL if harmful else (
            INSUFFICIENT if len(pool) < MIN_TRADES else NOT_PROVEN)

    return {
        "status": status,
        "discovered_threshold_pct": chosen,
        "promotion_requires_human_review": True,
        "bar": {
            "min_trades": MIN_TRADES,
            "min_refused": MIN_REFUSED,
            "min_sessions": MIN_SESSIONS,
            "min_measured_pct": MIN_MEASURED_PCT,
            "rules": [
                "hurdle computed from measured bid/ask only",
                "positive net effect (saved minus missed)",
                "still positive with the single largest refused trade removed",
                "beats the unchanged live baseline net P&L",
                "profit factor above 1",
                "same sign in both chronological halves",
            ],
        },
        "measured_resolved_trades": len(pool),
        "sessions": len(sessions),
        "coverage": cov,
        "candidates": candidates,
        "note": (
            "No threshold is applied to the live auto-buy path by this package. "
            "Promotion is a separate, explicitly approved change."
        ),
    }
