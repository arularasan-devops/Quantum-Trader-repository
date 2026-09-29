"""Phase 11 §4–5 — the vehicle comparison. RESEARCH ONLY. No order path.

For one market event the same view can be expressed four ways: the futures
contract, a CALL, a PUT, or not at all. This block puts all four side by side on
the **same event, same window, same cost treatment**, and records which one paid.

The selection rule is deliberately dull, and it is stated on every row so nobody
mistakes it for a model:

1. a vehicle that cannot be measured on the recorded data does not compete;
2. a vehicle with **no recorded book** does not compete — a gross figure is not
   comparable to a costed one, which is precisely the error that made the flow
   book look profitable;
3. of what remains, the vehicle with the **higher net R after costs** wins;
4. if nothing remains, or the winner's net R is not positive, the answer is
   ``NO_TRADE`` — the honest outcome for most events in this dataset.

Every choice carries ``vehicle_reason_codes``: the facts that decided it, not a
narrative. Nothing here is fed back into the Signal tab, the strike selector or
any order path, and no production module imports it: the output is a table that
says "on this evidence, this vehicle would have paid better", which is an input
to a decision *you* make, after a holdout has confirmed it.
"""
from __future__ import annotations

import json
import os

from app.config import settings

from .families import FUTURES as FUT_VEHICLE
from .families import OPTION, family_of

CALL = "CALL"
PUT = "PUT"
FUTURES = "FUTURES"
NO_TRADE = "NO_TRADE"

VEHICLES = (FUTURES, CALL, PUT, NO_TRADE)

LOG = "vehicle_comparison.jsonl"

RULE = ("higher net R after costs among vehicles that are both measurable and "
        "have a recorded book; NO_TRADE when none qualifies or the best net R is "
        "not positive")


def log_path() -> str:
    return os.path.join(settings.data_dir, LOG)


def _entry(row: dict | None, vehicle: str) -> dict | None:
    """One comparable entrant, or None when the row cannot compete."""
    if not row or not row.get("measurable"):
        return None
    return {
        "vehicle": vehicle,
        "net_r": row.get("net_r"),
        "gross_r": row.get("gross_r"),
        "cost_r": row.get("cost_r"),
        "spread_pct_of_risk": row.get("spread_pct_of_risk"),
        "entry_quality": row.get("entry_quality"),
        "target_before_stop": row.get("target_before_stop"),
        "first_event": row.get("first_event"),
        "outcome": row.get("outcome"),
        "mfe_r": row.get("mfe_r"),
        "mae_r": row.get("mae_r"),
        "minutes_to_resolution": row.get("minutes_to_resolution"),
        "minutes_to_target": row.get("minutes_to_target"),
        "minutes_to_stop": row.get("minutes_to_stop"),
        "book_source": row.get("book_source"),
        "data_source": row.get("data_source"),
        "tradingsymbol": row.get("tradingsymbol"),
        "strike": row.get("strike"),
        "expiry": row.get("expiry"),
    }


def compare(*, instrument: str, session: str, signal_ts: int,
            direction: str | None, futures_row: dict | None,
            options_result: dict | None,
            signal_id: str | None = None,
            episode_id: str | None = None,
            production_action: str | None = None,
            production_score: float | None = None,
            minutes_to_expiry: int | None = None) -> dict:
    """Compare FUTURES / CALL / PUT / NO_TRADE for one recorded event."""
    entrants: list[dict] = []
    codes: list[str] = []

    fut = _entry(futures_row, FUTURES)
    if fut is not None:
        # Futures has no recorded book, so rule 2 would exclude it outright. It is
        # admitted with its cost model instead, and the row says so: statutory
        # charges and slippage are charged, depth is unknown.
        fut["book_source"] = futures_row.get("spread_source") if futures_row else None
        fut["cost_treatment"] = "MODELLED_CHARGES_NO_DEPTH"
        entrants.append(fut)
    elif futures_row is not None:
        codes.append("FUTURES_NOT_MEASURABLE")
    else:
        codes.append("FUTURES_NOT_EVALUATED")

    opt_rows = (options_result or {}).get("candidates") or []
    prod_leg = (options_result or {}).get("production_leg")
    for row in opt_rows:
        if row.get("role") != "PRODUCTION_LEG":
            continue
        side = CALL if row.get("vehicle") == CALL else PUT
        ent = _entry(row, side)
        if ent is None:
            codes.append("OPTION_NOT_MEASURABLE")
            continue
        ent["cost_treatment"] = "ASK_IN_BID_OUT_RECORDED_BOOK"
        if ent["net_r"] is None:
            codes.append("OPTION_BOOK_UNAVAILABLE_SO_NOT_COMPARABLE")
            continue
        entrants.append(ent)
    if not opt_rows:
        codes.append("OPTION_NOT_EVALUATED")

    ranked = sorted((e for e in entrants if e.get("net_r") is not None),
                    key=lambda e: e["net_r"], reverse=True)
    best = ranked[0] if ranked else None
    if best is None:
        chosen = NO_TRADE
        codes.append("NO_VEHICLE_COMPARABLE_ON_RECORDED_DATA")
    elif best["net_r"] <= 0:
        chosen = NO_TRADE
        codes.append("BEST_VEHICLE_NET_R_NOT_POSITIVE")
        codes.append(f"BEST_WAS_{best['vehicle']}_AT_{best['net_r']}R")
    else:
        chosen = best["vehicle"]
        codes.append(f"{chosen}_HIGHEST_NET_R_{best['net_r']}")
        if len(ranked) > 1:
            margin = round(best["net_r"] - ranked[1]["net_r"], 3)
            codes.append(f"MARGIN_OVER_{ranked[1]['vehicle']}_{margin}R")
            if margin < 0.1:
                codes.append("MARGIN_WITHIN_NOISE_NOT_A_PREFERENCE")
        if best.get("entry_quality") in ("POOR", "UNPAYABLE"):
            codes.append(f"WINNER_BOOK_{best['entry_quality']}")
    if minutes_to_expiry is not None and minutes_to_expiry <= 0:
        codes.append("EXPIRED_CONTRACT_AT_SIGNAL")

    veh_family = FUT_VEHICLE if chosen == FUTURES else OPTION
    return {
        "signal_id": signal_id,
        "episode_id": episode_id,
        "session": session,
        "signal_ts": signal_ts,
        "instrument": instrument.upper(),
        "direction": direction,
        "production_action": production_action,
        "production_score": production_score,
        "minutes_to_expiry": minutes_to_expiry,
        "options_family": family_of(instrument, OPTION),
        "futures_family": family_of(instrument, FUT_VEHICLE),
        "family": family_of(instrument, veh_family),
        "best_vehicle": chosen,
        "vehicle_reason_codes": codes,
        "selection_rule": RULE,
        "entrants": entrants,
        "ranked": [e["vehicle"] for e in ranked],
        "production_leg_net_r": (prod_leg or {}).get("net_r"),
        "futures_net_r": (fut or {}).get("net_r"),
        "research_only": True,
        "applied_to_production": False,
    }


def write(rows: list[dict], path: str | None = None) -> str:
    """Append the comparisons to ``vehicle_comparison.jsonl``. Append-only."""
    target = path or log_path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")
    return target


def _mean(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 3) if vals else None


def _vehicle_block(rows: list[dict]) -> dict:
    """Per-vehicle economics over the events where that vehicle was comparable."""
    out: dict[str, dict] = {}
    for veh in (FUTURES, CALL, PUT):
        nets = [e["net_r"] for r in rows for e in r["entrants"]
                if e["vehicle"] == veh and e.get("net_r") is not None]
        wins = [e for r in rows for e in r["entrants"]
                if e["vehicle"] == veh and e.get("target_before_stop")]
        comparable = [e for r in rows for e in r["entrants"] if e["vehicle"] == veh]
        out[veh] = {
            "events_evaluated": len(comparable),
            "events_costable": len(nets),
            "avg_net_r": _mean(nets),
            "total_net_r": round(sum(nets), 3) if nets else None,
            # A rate needs a denominator. Where nothing was comparable it is
            # absent, not 0%.
            "target_before_stop_pct": (round(100.0 * len(wins) / len(comparable), 1)
                                       if comparable else None),
            "chosen_count": sum(1 for r in rows if r["best_vehicle"] == veh),
        }
    out[NO_TRADE] = {
        "chosen_count": sum(1 for r in rows if r["best_vehicle"] == NO_TRADE),
        "events_evaluated": len(rows),
        "events_costable": None, "avg_net_r": None, "total_net_r": None,
        "target_before_stop_pct": None,
    }
    return out


def report(rows: list[dict], *, sessions: int = 0,
           holdout_sessions: int = 0) -> dict:
    """The §5/§23 comparison, split by family and never pooled across families.

    The verdict is gated on the same evidence bar as the rest of the phase: a
    family with too few events, or with no chronological holdout behind it, is
    reported as UNPROVEN however good the numbers look.
    """
    by_family: dict[str, list[dict]] = {}
    for r in rows:
        by_family.setdefault(r["options_family"], []).append(r)

    families: dict[str, dict] = {}
    for fam, frows in sorted(by_family.items()):
        vb = _vehicle_block(frows)
        fut_beats = [r for r in frows
                     if r.get("futures_net_r") is not None
                     and r.get("production_leg_net_r") is not None
                     and r["futures_net_r"] > r["production_leg_net_r"]]
        head_to_head = [r for r in frows
                        if r.get("futures_net_r") is not None
                        and r.get("production_leg_net_r") is not None]
        enough = len(frows) >= 100 and holdout_sessions >= 5
        families[fam] = {
            "events": len(frows),
            "by_vehicle": vb,
            "head_to_head_events": len(head_to_head),
            "futures_better_than_production_leg_pct": (
                round(100.0 * len(fut_beats) / len(head_to_head), 1)
                if head_to_head else None),
            "verdict": ("UNPROVEN_INSUFFICIENT_EVIDENCE" if not enough
                        else "MEASURED_ON_HOLDOUT"),
            "verdict_basis": (
                f"{len(frows)} events over {sessions} sessions with "
                f"{holdout_sessions} chronological holdout sessions; the bar is "
                f"100 events and 5 holdout sessions before a vehicle preference "
                f"is stated as more than a measurement"),
        }

    return {
        "families": families,
        "selection_rule": RULE,
        "pooling_rule": "index and MCX are never pooled: their books differ by an "
                        "order of magnitude, and a pooled vehicle preference would "
                        "be MCX's book applied to an index trade",
        "research_only": True,
        "applied_to_production": False,
        "futures_cost_caveat": "the futures book is not recorded, so futures pays "
                               "modelled charges and slippage while options pay "
                               "their recorded bid/ask. Futures is therefore "
                               "flattered by an unknown amount and a futures win "
                               "inside that margin is not a result",
    }
