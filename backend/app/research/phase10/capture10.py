"""Phase 10 §12 — zero-to-hero profit-capture collection. RESEARCH ONLY.

The question behind §12 is "how much of the move do we actually keep?", and it is
asked because a trade that reaches +3R and exits at +0.4R is a *different* defect
from one that never goes anywhere: the first is an exit problem, the second is a
selection problem, and they need opposite fixes.

This module collects, per trade: the favourable and adverse excursion, the share of
the favourable excursion kept, the give-back after the peak, how long the peak took
to arrive, premium expansion, the underlying's own excursion, time to expiry and
the spread cost. Then it splits by give-back rather than by outcome, because a
population sorted by result tells you what happened and a population sorted by
give-back tells you where the money went.

Explicitly NOT here, per §12: no HOLD-UNTIL-PEAK behaviour, no expiry-day rule, no
change to production exits, no trailing logic. Knowing the peak in hindsight is not
a strategy — every number in this module is computed with information the trade did
not have at the time, and any exit rule derived from it must be proven forward on
sessions it never saw.
"""
from __future__ import annotations

from app.research.phase7.policies import median
from app.research.phase9.findings9 import contamination, label9

# Give-back bands as a share of the favourable excursion that was handed back.
GIVEBACK_BANDS = ((0.0, 25.0), (25.0, 50.0), (50.0, 75.0), (75.0, 1e12))
# A trade whose MFE never reached this cannot have a give-back problem.
MIN_MFE_R_TO_JUDGE_GIVEBACK = 0.5


def band_of(pct: float | None) -> str:
    if pct is None:
        return "UNKNOWN"
    for lo, hi in GIVEBACK_BANDS:
        if lo <= pct < hi:
            return f"{lo:.0f}-{hi:.0f}%" if hi < 1e12 else f">{lo:.0f}%"
    return ">75%"


def row_of(trade: dict) -> dict:
    """§12's collection record for one trade. Every field already measured."""
    cap = trade.get("capture") or {}
    mfe = trade.get("mfe_r")
    realised = trade.get("realised_r")
    giveback_r = (None if mfe is None or realised is None or mfe <= 0
                  else round(max(0.0, mfe - realised), 3))
    giveback_pct = (None if giveback_r is None or not mfe
                    else round(100.0 * giveback_r / mfe, 1))
    return {
        "ts_ist": trade.get("ts_ist"),
        "session": trade.get("session"),
        "instrument": trade.get("instrument"),
        "symbol": trade.get("symbol"),
        "side": trade.get("side"),
        "regime": trade.get("regime"),
        "signal_score": trade.get("confidence"),
        "mfe_r": mfe,
        "mae_r": trade.get("mae_r"),
        "realised_r": realised,
        "net_r": trade.get("net_r"),
        "mfe_capture_pct": cap.get("capture_pct"),
        "giveback_r": giveback_r,
        "giveback_pct_of_mfe": giveback_pct,
        "giveback_band": band_of(giveback_pct),
        "min_to_mfe": trade.get("min_to_mfe"),
        "min_to_mae": trade.get("min_to_mae"),
        "min_from_mfe_to_exit": (
            None if trade.get("min_to_mfe") is None or trade.get("held_min") is None
            else round(float(trade["held_min"]) - float(trade["min_to_mfe"]), 1)),
        "held_min": trade.get("held_min"),
        "premium_expansion_pct": trade.get("premium_expansion_pct"),
        "underlying_favourable": trade.get("underlying_favourable"),
        "underlying_adverse": trade.get("underlying_adverse"),
        "minutes_to_expiry": trade.get("minutes_to_expiry"),
        "expiry_class": trade.get("expiry_class"),
        "days_to_expiry": trade.get("days_to_expiry"),
        "spread_cost_r": trade.get("spread_cost_r"),
        "spread_share_of_risk_pct": trade.get("spread_share_of_risk_pct"),
        "exit_reason": trade.get("exit_reason"),
        "data_flag": trade.get("data_flag"),
        "peak_known_only_in_hindsight": True,
    }


def _block(rows: list[dict], sessions: int) -> dict:
    nets = [r["net_r"] for r in rows if r["net_r"] is not None]
    return {
        "n": len(rows),
        "median_mfe_r": median([r["mfe_r"] for r in rows if r["mfe_r"] is not None]),
        "median_mae_r": median([r["mae_r"] for r in rows if r["mae_r"] is not None]),
        "median_mfe_capture_pct": median([r["mfe_capture_pct"] for r in rows
                                          if r["mfe_capture_pct"] is not None]),
        "median_giveback_r": median([r["giveback_r"] for r in rows
                                     if r["giveback_r"] is not None]),
        "median_min_to_mfe": median([r["min_to_mfe"] for r in rows
                                     if r["min_to_mfe"] is not None]),
        "median_min_from_mfe_to_exit": median([r["min_from_mfe_to_exit"] for r in rows
                                              if r["min_from_mfe_to_exit"] is not None]),
        "net_expectancy_r": round(sum(nets) / len(nets), 3) if nets else None,
        "label": label9(len(rows), sessions),
    }


def study(trade_rows: list[dict], sessions: int,
          missing_bar_pct: float | None) -> dict:
    rows = [row_of(t) for t in trade_rows]
    judgeable = [r for r in rows
                 if r["mfe_r"] is not None and r["mfe_r"] >= MIN_MFE_R_TO_JUDGE_GIVEBACK]
    by_band: dict[str, list[dict]] = {}
    for r in judgeable:
        by_band.setdefault(r["giveback_band"], []).append(r)
    by_exit: dict[str, list[dict]] = {}
    for r in rows:
        by_exit.setdefault(str(r["exit_reason"]), []).append(r)
    by_expiry: dict[str, list[dict]] = {}
    for r in rows:
        by_expiry.setdefault(str(r["expiry_class"]), []).append(r)

    reached = [r for r in rows if (r["mfe_r"] or 0.0) >= 1.0]
    reached_but_lost = [r for r in reached if (r["net_r"] or 0.0) <= 0]
    never_moved = [r for r in rows if (r["mfe_r"] or 0.0) < 0.5]

    return {
        "collected": len(rows),
        "rows": rows,
        "overall": _block(rows, sessions),
        "by_giveback_band": {k: _block(v, sessions) for k, v in sorted(by_band.items())},
        "by_exit_reason": {k: _block(v, sessions) for k, v in sorted(by_exit.items())},
        "by_expiry_class": {k: _block(v, sessions)
                            for k, v in sorted(by_expiry.items())},
        "reached_1r_favourable": len(reached),
        "reached_1r_then_finished_negative_net": len(reached_but_lost),
        "never_reached_half_r": len(never_moved),
        "diagnosis": (
            f"{len(reached_but_lost)} of {len(reached)} trades that saw at least +1R "
            f"favourable finished negative net, and {len(never_moved)} of {len(rows)} "
            f"never reached +0.5R at all. The first group is an exit-and-cost "
            f"problem; the second is a selection problem. They are counted "
            f"separately because a single 'improve exits' conclusion would be wrong "
            f"for the second group"
            if rows else "no trades collected"),
        "atr_contamination": contamination(missing_bar_pct, atr_dependent=True),
        "hindsight_warning": "MFE, the peak and the time to it are hindsight values. "
                             "No exit rule is derived, proposed or enabled from them "
                             "here",
        "guarantee": "no HOLD UNTIL PEAK behaviour, no expiry-day rule, no trailing "
                     "change and no production exit change is made by this phase",
    }
