"""Phase 35 §24 — the one question, answered from the numbers or not at all.

> Is our current system losing money because there is no profitable opportunity,
> or because the system is selecting, entering, expressing, or exiting the
> opportunity incorrectly?

This module refuses to answer that from a mood. The verdict is a function of the
attribution histogram and the evidence floors, and there is a fourth answer —
``INSUFFICIENT_EVIDENCE`` — which is the honest one until Checkpoint 2 is met.
Phase 33 already had to walk back a claim built on 30 board rows with a real
quote; the answerability map exists so that cannot happen quietly again.

Also here: the answerability map (which of the 24 sections have data *today*),
and the explicit list of what a positive live number still would not license.
"""
from __future__ import annotations

from app.research.phase35 import (
    CHANNELS,
    CURRENT_ENGINE_PAPER,
    FULL_MARKET_PAPER,
    GENERAL_MIN_SESSIONS,
    GENERAL_MIN_TRADES,
    MEASURED_EXECUTABLE,
    REQUIRES_MORE_DATA,
)

NO_OPPORTUNITY = "NO_PROFITABLE_OPPORTUNITY_IN_MEASURED_POOL"
WRONG_EXPRESSION = "OPPORTUNITY_EXISTS_BUT_IS_EXPRESSED_INCORRECTLY"
COST_DOMINATED = "OPPORTUNITY_EXISTS_BUT_IS_CONSUMED_BY_EXECUTION_COST"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

VERDICTS = (NO_OPPORTUNITY, WRONG_EXPRESSION, COST_DOMINATED, INSUFFICIENT)

# The expression channels — the ones a different decision could have changed.
# COST is deliberately not among them: cost is not a decision the engine makes
# per trade, it is the toll on the vehicle it chose, and conflating the two is
# how "we just need better entries" survives contact with a spread.
EXPRESSION_CHANNELS = ("DIRECTION", "ENTRY", "VEHICLE", "EXIT", "GIVEBACK")


def verdict(state: dict) -> dict:
    """§24, decided by the histogram rather than by narrative."""
    engine = state["books"][CURRENT_ENGINE_PAPER]
    resolved = int(engine.get("paper_resolved") or 0)
    sessions = int(engine.get("sessions") or 0)
    hist = state.get("attribution") or {}
    channels = dict(hist.get("by_channel") or {})
    graded = int(hist.get("graded") or 0)

    if (
        resolved < GENERAL_MIN_TRADES
        or sessions < GENERAL_MIN_SESSIONS
        or graded == 0
    ):
        return {
            "verdict": INSUFFICIENT,
            "resolved_trades": resolved,
            "independent_sessions": sessions,
            "required_trades": GENERAL_MIN_TRADES,
            "required_sessions": GENERAL_MIN_SESSIONS,
            "graded_legs": graded,
            "reason": (
                "the engine's own executable record is below the general evidence "
                "floor; every channel share below is a description of the sample, "
                "not an answer about the system"
            ),
            "shares_pct": _shares(channels, graded),
        }

    cost = int(channels.get("COST") or 0)
    expression = sum(int(channels.get(c) or 0) for c in EXPRESSION_CHANNELS)
    clean = int(channels.get("PROFITABLE_NO_LOSS_CHANNEL") or 0)
    if clean >= graded - clean:
        chosen = NO_OPPORTUNITY if expression + cost == 0 else WRONG_EXPRESSION
    elif cost >= expression:
        chosen = COST_DOMINATED
    else:
        chosen = WRONG_EXPRESSION
    return {
        "verdict": chosen,
        "resolved_trades": resolved,
        "independent_sessions": sessions,
        "graded_legs": graded,
        "cost_channel": cost,
        "expression_channels": expression,
        "profitable_legs": clean,
        "shares_pct": _shares(channels, graded),
        "note": (
            "measured on the engine's own executable legs only; the board pool is "
            "counterfactual and is never mixed into this verdict"
        ),
    }


def _shares(channels: dict, graded: int) -> dict:
    if not graded:
        return {c: None for c in CHANNELS}
    return {
        c: round(100.0 * int(channels.get(c) or 0) / graded, 2) for c in CHANNELS
    }


def answerability(state: dict) -> list[dict]:
    """Which sections have data today, section by section, with the N behind it.

    Written before any result is read, and printed beside every report, because a
    section answered from four rows and a section answered from four thousand look
    identical in a table of percentages.
    """
    cov = state.get("coverage") or {}
    veh = cov.get("by_vehicle") or {}
    engine = state["books"][CURRENT_ENGINE_PAPER]
    board = state["books"][FULL_MARKET_PAPER]
    missed_s = state.get("missed") or {}
    comp = state.get("vehicle") or {}
    econ = state.get("economics") or {}

    def _row(section: str, n: int | None, floor: int, what: str) -> dict:
        have = int(n or 0)
        return {
            "section": section,
            "n": have,
            "floor": floor,
            "status": MEASURED_EXECUTABLE if have >= floor else REQUIRES_MORE_DATA,
            "measures": what,
        }

    ce = int((veh.get("CE") or {}).get("executable") or 0)
    pe = int((veh.get("PE") or {}).get("executable") or 0)
    fut = int((veh.get("FUTURES") or {}).get("executable") or 0)
    return [
        _row("§4/§21 raw capture", cov.get("observations"), 1,
             "raw observations retained, never overwritten"),
        _row("§4 executable books", ce + pe + fut, 1,
             "decision instants where a real feed quoted both sides"),
        _row("§5 CURRENT_ENGINE_PAPER", engine.get("paper_resolved"),
             GENERAL_MIN_TRADES, "the engine's own executable record"),
        _row("§5 FULL_MARKET_PAPER", board.get("paper_resolved"),
             GENERAL_MIN_TRADES, "independently observed opportunities"),
        _row("§6 option economics", ce + pe, GENERAL_MIN_TRADES,
             "ask-in / bid-out legs on real two-sided books"),
        _row("§7 futures economics", fut, GENERAL_MIN_TRADES,
             "executable futures sides"),
        _row("§10 engine attribution",
             (state.get("attribution") or {}).get("graded"),
             GENERAL_MIN_TRADES, "primary loss cause per losing engine leg"),
        _row("§11 missed opportunities",
             missed_s.get("declined_with_measured_outcome"),
             GENERAL_MIN_TRADES, "counterfactual outcome of what the engine declined"),
        _row("§12 CE vs PE",
             (comp.get("CE_VS_PE") or {}).get("comparable_instants"), 30,
             "same-instant CE against PE, both two-sided"),
        _row("§13 futures vs options",
             (comp.get("FUTURES_VS_OPTIONS") or {}).get("comparable_instants"), 30,
             "same-instant vehicle comparison"),
        _row("§19 economic filters", econ.get("resolved_legs"), GENERAL_MIN_TRADES,
             "premium / spread / room-cost / liquidity bands, none enabled"),
    ]


def questions(state: dict) -> list[dict]:
    """The questions §24 and §18 ask, each answered only where N supports it."""
    v = verdict(state)
    engine = state["books"][CURRENT_ENGINE_PAPER]
    board = state["books"][FULL_MARKET_PAPER]
    missed_s = state.get("missed") or {}
    thin = v["verdict"] == INSUFFICIENT
    unknown = "REQUIRES_MORE_DATA"

    def ans(value) -> object:
        return unknown if thin else value

    return [
        {"q": "Is there no opportunity, or is it expressed incorrectly?",
         "a": v["verdict"], "n": v["resolved_trades"]},
        {"q": "Where is profitability lost (COST/DIRECTION/ENTRY/VEHICLE/EXIT/GIVEBACK)?",
         "a": v["shares_pct"], "n": v["graded_legs"]},
        {"q": "What does the engine's own paper book net?",
         "a": ans(engine.get("net_pnl_pct_mean")), "n": engine.get("paper_resolved")},
        {"q": "What does the independently observed board pool net?",
         "a": ans(board.get("net_pnl_pct_mean")), "n": board.get("paper_resolved")},
        {"q": "Does the engine miss winners, or avoid losers?",
         "a": ans({
             "missed_winners": missed_s.get("missed_winners"),
             "avoided_losers": missed_s.get("avoided_losers"),
             "capture_rate_pct": missed_s.get("engine_capture_rate_pct"),
         }),
         "n": missed_s.get("declined_with_measured_outcome")},
        {"q": "How much of the best look is handed back?",
         "a": ans(engine.get("giveback_pct_mean")), "n": engine.get("paper_resolved")},
        {"q": "Which hold window was best on collected legs?",
         "a": ans((engine.get("best_hold_window") or {}).get("horizon")),
         "n": engine.get("paper_resolved")},
        {"q": "Which vehicle was cheapest and which netted most at the same instant?",
         "a": {
             kind: ((state.get("vehicle") or {}).get(kind) or {}).get("status")
             for kind in ("CE_VS_PE", "FUTURES_VS_OPTIONS")
         },
         "n": sum(
             int(((state.get("vehicle") or {}).get(kind) or {})
                 .get("comparable_instants") or 0)
             for kind in ("CE_VS_PE", "FUTURES_VS_OPTIONS")
         )},
        {"q": "Where is the opportunity economically viable?",
         "a": (state.get("economics") or {}).get("evidence"),
         "n": (state.get("economics") or {}).get("resolved_legs")},
        {"q": "Are the frozen candidates still unchanged?",
         "a": (state.get("frozen") or {}).get("all_verified"), "n": 3},
    ]


def summary(state: dict) -> dict:
    return {
        "verdict": verdict(state),
        "answerability": answerability(state),
        "questions": questions(state),
        "does_not_license": [
            "no real-money order",
            "no production signal, gate, strike, target, stop or exit change",
            "no averaging and no position-sizing change",
            "no promotion from live paper P&L alone",
            "no adaptive exit armed from the giveback diagnostic",
        ],
        "production_changed": False,
    }


def headline(state: dict) -> str:
    v = verdict(state)
    if v["verdict"] == INSUFFICIENT:
        return (
            f"PHASE 35: {v['verdict']} — {v['resolved_trades']} resolved engine legs "
            f"over {v['independent_sessions']} sessions against a floor of "
            f"{GENERAL_MIN_TRADES}/{GENERAL_MIN_SESSIONS}"
        )
    return f"PHASE 35: {v['verdict']} on {v['graded_legs']} graded legs"
