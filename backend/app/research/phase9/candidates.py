"""Phase 9 §18 — the A+ candidate object, descriptive only. RESEARCH ONLY.

The spec asks for an object carrying "Expected Net R: +X". That field is deliberately
absent, and its absence is the design: producing a number that predicts the net R of
a trade *is* a model, which §594 of the same spec forbids in this phase, and a model
fitted to two contaminated sessions would be a confident wrong answer wearing the
clothes of a research result. What is emitted instead is the evidence a human needs to
judge the setup: every component, unhidden, with its raw value beside its rank.

One empirical field is included and it is labelled at the point of use:
``empirical_net_r_of_similar`` is the mean net R of the trades already recorded in the
same tradability class and entry class — a *description of a bucket the study has
already seen*, in-sample, with its sample size attached. It is not a forecast for the
row it sits on, and the field name and the accompanying label both say so.

Nothing here is a signal. The object is not produced live, not shown in the Signal
tab, and this module is imported by the report only.
"""
from __future__ import annotations

from collections import defaultdict

from .findings9 import label9

# A candidate is described, not selected. This is the only "cut" in the module and
# it exists so the report can show the top of the distribution rather than 337 rows;
# it is a display choice, not a trading threshold.
SHOW_TOP = 25


def empirical_buckets(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """Mean net R per (tradability, entry_quality) cell of the recorded sample."""
    cells: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        if r.get("net_r") is None:
            continue
        cells[(str(r.get("tradability")), str(r.get("entry_quality")))].append(
            float(r["net_r"]))
    return {
        key: {"n": len(v), "mean_net_r": round(sum(v) / len(v), 3)}
        for key, v in cells.items()
    }


def describe(trade: dict, entry_row: dict | None, signal: dict | None,
             buckets: dict[tuple[str, str], dict], sessions: int) -> dict:
    """One descriptive candidate vector. No prediction, by construction."""
    key = (str(trade.get("tradability")), str(trade.get("entry_quality")))
    cell = buckets.get(key)
    reasons: list[str] = []
    if entry_row:
        share = entry_row.get("spread_share_of_risk_pct")
        if share is not None and share <= 10.0:
            reasons.append("book takes under a tenth of the intended risk")
        if share is not None and share >= 25.0:
            reasons.append(f"book takes {share:.0f}% of the intended risk")
        if not entry_row.get("greeks_usable"):
            reasons.append("feed greeks degenerate on this leg — delta-derived "
                           "components unavailable")
        room = entry_row.get("room_ratio")
        if room is not None and room > 1.0:
            reasons.append("target1 needs more underlying than the ATR offered")
    if trade.get("entry_quality") in ("CHASED_ENTRY", "SEVERELY_CHASED"):
        reasons.append("entry was late in the post-signal move")
    if trade.get("data_flag") != "FRESH":
        reasons.append(f"data flagged {trade.get('data_flag')} at signal time")
    return {
        "instrument": trade["instrument"],
        "direction": trade["side"],
        "symbol": trade["symbol"],
        "strike": trade["strike"],
        "ts_ist": trade["ts_ist"],
        "session": trade["session"],
        "expiry_class": trade["expiry_class"],
        "displayed_confidence": trade["confidence"],
        "regime": trade["regime"],
        "strike_quality_components": None if not entry_row else {
            "premium": entry_row.get("premium"),
            "premium_band": entry_row.get("premium_band"),
            "spread_share_of_risk_pct": entry_row.get("spread_share_of_risk_pct"),
            "spread_pct_of_premium": entry_row.get("spread_pct_of_premium"),
            "delta": entry_row.get("delta"),
            "oi": entry_row.get("oi"),
            "volume": entry_row.get("volume"),
            "moneyness_pct": entry_row.get("moneyness_pct"),
            "steps_from_atm": entry_row.get("steps_from_atm"),
            "greeks_usable": entry_row.get("greeks_usable"),
            "ranks": {k[len("rank_"):]: v for k, v in entry_row.items()
                      if k.startswith("rank_")},
            "composite_rank": entry_row.get("entry_time_composite"),
            "composite_weighting": "equal weights — ARBITRARY, not fitted",
        },
        "entry_quality_components": {
            "class": trade.get("entry_quality"),
            "improvement_available_pct": trade.get("entry_improvement_pct"),
            "premium_expansion_before_entry_pct": (
                None if not entry_row else entry_row.get("past_expansion_pct")),
        },
        "room_components": None if not entry_row else {
            "required_underlying_move": entry_row.get("required_underlying_move"),
            "available_move_atr": entry_row.get("available_move_atr"),
            "room_ratio": entry_row.get("room_ratio"),
            "caveat": "ATR-derived; contaminated while one-minute bars are missing",
        },
        "tradability": trade.get("tradability"),
        "data_quality": {
            "flag": trade.get("data_flag"),
            "chain_age_sec": trade.get("chain_age_sec"),
        },
        "alternatives_at_entry_time": None if not signal else {
            "candidates_in_ladder": signal.get("candidates_available"),
            "better_scoring_count": signal.get("better_at_entry_time_count"),
            "best_scoring_symbol": signal.get("best_at_entry_time"),
        },
        "reasons": reasons,
        "empirical_net_r_of_similar": None if not cell else cell["mean_net_r"],
        "empirical_sample": None if not cell else cell["n"],
        "empirical_label": label9(0 if not cell else cell["n"], sessions),
        "empirical_disclaimer": (
            "in-sample mean net R of the recorded trades sharing this tradability and "
            "entry class. It describes that bucket; it is NOT a prediction for this "
            "row, NOT a probability, and NOT a signal"),
        "predicted_net_r": None,
        "why_no_prediction": "predicting net R requires a model, which this phase "
                             "forbids; the components above are given so a human can "
                             "judge, and no number here forecasts anything",
    }


def study(rows: list[dict], entry_rows: dict[tuple, dict],
          signals_by_key: dict[tuple, dict], sessions: int) -> dict:
    """§18 — the candidate vectors, ranked only for display."""
    buckets = empirical_buckets(rows)
    out = []
    for r in rows:
        key = (r["instrument"], r["symbol"], r["ts_ist"])
        out.append(describe(r, entry_rows.get(key), signals_by_key.get(key),
                            buckets, sessions))
    # Displayed in composite-rank order purely so the top of the distribution is
    # visible. This ordering is not a ranking of trade quality and is not used to
    # select anything.
    out.sort(key=lambda c: -((c["strike_quality_components"] or {}).get(
        "composite_rank") or 0.0))
    return {
        "candidates_described": len(out),
        "shown": min(SHOW_TOP, len(out)),
        "display_order": "entry-time composite rank, descending — a display choice, "
                         "not a quality ranking and not used to select anything",
        "empirical_cells": {f"{k[0]}|{k[1]}": v for k, v in sorted(buckets.items())},
        "candidates": out[:SHOW_TOP],
        "omitted_field": "expected/predicted net R is deliberately absent — see "
                         "why_no_prediction on every row",
        "label": label9(len(out), sessions, comparative=False),
    }
