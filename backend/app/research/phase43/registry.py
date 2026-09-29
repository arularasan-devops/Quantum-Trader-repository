"""Phase 43 §5 — the machine-readable registry entry for a lead.

The point of this file is that a lead can later become a frozen shadow arm
*without being redefined*. Every field a shadow arm needs to reconstruct the
candidate is written down here: the conditions, the ratio and its threshold, the
geometry, the cost basis, the split that produced the result, and the definition
fingerprint under which it was measured. If any of those moved, the entry no
longer matches the code and the mismatch is visible instead of silent.

It also records what the historical measurement could *not* see, because that is
what the live capture has to supply before the lead can be validated: a quoted
book. Nothing here is a promotion, and the entry says so in its own payload.
"""
from __future__ import annotations

from app.research import phase43
from app.research.phase43 import freeze, mechanisms, pair
from app.research.phase43.evaluate import DEV, HOLD, VAL

# What a live capture must record, per decision instant, before any of these
# leads can be validated on executable prices. Named as fields rather than as
# prose so the capture can be checked against it mechanically.
REQUIRED_LIVE_FIELDS = (
    "instrument",
    "decision_ts",
    "bid",
    "ask",
    "bid_size",
    "ask_size",
    "last_traded_price",
    "contract",
    "expiry",
    "lot_size",
    "feed_age_ms",
    "atr_14_at_decision",
    "expansion_leg_points_at_decision",
    "modelled_round_trip_cost_at_decision",
    "measured_round_trip_cost_from_book",
    "fill_side_used",
    "fill_ts",
    "fill_price",
    "exit_side_used",
    "exit_ts",
    "exit_price",
    "cost_evidence_label",
)


def entry(row: dict) -> dict:
    """One registry entry for one lead, sufficient to re-create it verbatim."""
    fp = freeze.fingerprint()
    hold = row["splits"][HOLD]
    return {
        "candidate_id": row["candidate"],
        "phase": "43",
        "family": row["family"],
        "instrument": row["instrument"],
        "status": row["status"],
        "promoted": False,
        "paper_only": True,
        "definition": {
            "conditions": list(row.get("conditions") or []),
            "ratio": row.get("ratio"),
            "threshold": row.get("threshold"),
            "beta_adjusted": row.get("beta_adjusted"),
            "baseline_rule": list(mechanisms.BASE),
            "stop_atr": phase43.STOP_ATR,
            "t1_r": phase43.T1_R,
            "entry": "next bar open after the decision close",
            "exit": (
                "target, stop, or flat at the session close, whichever comes "
                "first; a bar containing both target and stop counts as the stop"
            ),
            "pair_geometry": {
                "return_bars": pair.RET_BARS,
                "trailing_bars": pair.TRAIL_BARS,
                "hold_bars": pair.HOLD_BARS,
                "notional_per_leg": pair.NOTIONAL_PER_LEG,
            } if row.get("beta_adjusted") is not None else None,
        },
        "measured_on": {
            "granularity": "ONE_MINUTE",
            "cost_basis": "MODELLED_CHARGES_NO_QUOTED_SPREAD",
            "cost_multipliers_stressed": list(phase43.COST_MULTIPLIERS),
            "split": {
                "train_sessions_share": phase43.DEV_SHARE,
                "validation_sessions_share": phase43.VAL_SHARE,
                "train_net": row["splits"][DEV].get("net_total"),
                "validation_net": row["splits"][VAL].get("net_total"),
                "holdout_net": hold.get("net_total"),
                "holdout_trades": hold.get("trades", 0),
                "holdout_sessions": hold.get("sessions", 0),
                "holdout_profit_factor": hold.get("profit_factor"),
                "holdout_expectancy": hold.get("expectancy"),
                "holdout_p_value": hold.get("p_value"),
            },
            "fdr_survives": row.get("fdr_survives"),
        },
        "fingerprint": {
            "definition": fp["definition"],
            "components": fp["components"],
        },
        "live_validation": {
            "required_fields": list(REQUIRED_LIVE_FIELDS),
            "unmeasured_historically": [
                phase43.NO_QUOTED_DEPTH,
                "slippage, which is modelled here and must be measured live",
            ],
        },
        "note": (
            "HISTORICAL_LEAD is not a promotion and not a signal. It means this "
            "definition is worth the cost of executable live capture, and it "
            "must be run as a record-only shadow arm first"
        ),
    }
