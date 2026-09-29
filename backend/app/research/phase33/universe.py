"""Phase 33 §1/§19 — what data exists, and which questions it can answer.

The request asks 21 questions across futures and options, engine signals and the
raw option board. Some are answerable on five years of bars; some rest on real
two-sided option books that exist for days rather than years. Answering all of
them in the same voice would be the dishonest part, so this module publishes the
answerability map *before* any result is computed:

* futures/underlying instruments, split into the same two tiers Phase 32 used, so
  an instrument cannot change tier between phases;
* option evidence, counted as real two-sided observations — the only kind §4
  allows to be priced — with everything else counted for coverage and never
  priced;
* the engine's own recorded signals and the raw option-board observations, as two
  independent pools, so §12 can compare them instead of grading the engine on the
  rows it already selected.

Nothing here loads a full series more than once and nothing widens, gap-fills or
resamples a series to reach a tier.
"""
from __future__ import annotations

import sqlite3

from app.research.phase24 import data as p24data
from app.research.phase31 import evidence as p31evidence
from app.research.phase33 import (
    MIN_BARS_FOR_SPLIT,
    MIN_OPTION_INSTANTS,
    MIN_SESSIONS_FOR_SPLIT,
    REQUIRES_MORE_DATA,
)

FIVE_YEAR = "FIVE_YEAR"
SHORT_CAPTURE = "SHORT_CAPTURE"

MEASURABLE = "MEASURABLE"
DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"

RESEARCH_DB = "data/research.db"

# The engine's own recorded decisions. ``signal`` carries the engine's label, so
# §2's "do not invent new strategy labels" is satisfied by reading them.
SIGNAL_SQL = (
    "SELECT signal, count(*) FROM signal_snapshots GROUP BY 1"
)
SIGNAL_INSTRUMENT_SQL = (
    "SELECT instrument, count(*) FROM signal_snapshots GROUP BY 1"
)
SIGNAL_SESSION_SQL = (
    "SELECT count(DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes')) "
    "FROM signal_snapshots"
)
# Raw option-board observations: option rows recorded independently of whether a
# BUY was ever produced. Only a real two-sided quote can be priced.
BOARD_SQL = (
    "SELECT count(*), "
    "sum(CASE WHEN bid IS NOT NULL AND ask IS NOT NULL AND bid > 0 AND ask > bid "
    "THEN 1 ELSE 0 END), "
    "count(DISTINCT date(ts,'unixepoch','+5 hours','+30 minutes')) "
    "FROM mcx_options"
)
BOARD_SOURCE_SQL = "SELECT source, count(*) FROM mcx_options GROUP BY 1"


def _sessions_of(ts_list: list[int]) -> int:
    return len({t // 86_400 for t in ts_list})


def _read(sql: str, db: str = RESEARCH_DB) -> list[tuple] | None:
    """Read-only query against a store the engine may be writing.

    Returns ``None`` rather than raising: an unavailable store must be reported
    as unavailable, not turned into a zero that reads like an empty capture.
    """
    path = p24data._resolve(db)
    try:
        con = p24data.connect_readonly(path)
    except sqlite3.Error:
        return None
    try:
        return list(con.execute(sql))
    except sqlite3.Error:
        return None
    finally:
        con.close()


def futures_inventory() -> list[dict]:
    """Every instrument with candles, its tier, bars and sessions."""
    out: list[dict] = []
    thin = p24data._thin_instruments()
    for name in sorted(set(p24data.BACKTEST_FILES) | set(thin)):
        if name in p24data.BACKTEST_FILES:
            s = p24data.load_series(name)
            bars = len(s) if s is not None else 0
            sessions = _sessions_of([int(t) for t in s.ts]) if s is not None else 0
        else:
            bars = int(thin[name]["bars"])
            sessions = 0
        five = bars >= MIN_BARS_FOR_SPLIT and sessions >= MIN_SESSIONS_FOR_SPLIT
        out.append({
            "instrument": name,
            "bars": bars,
            "sessions": sessions or None,
            "tier": FIVE_YEAR if five else SHORT_CAPTURE,
            "hold_time_study": MEASURABLE if five else REQUIRES_MORE_DATA,
            "why": (
                "enough history to select a holding window on development data "
                "and confirm it on an untouched holdout"
                if five else
                f"{bars} bars over {sessions or 0} session(s) is below the "
                f"{MIN_BARS_FOR_SPLIT} bars / {MIN_SESSIONS_FOR_SPLIT} sessions a "
                "chronological split needs, so its movement is described only"
            ),
        })
    return out


def option_evidence() -> dict:
    """Real two-sided option observations, and everything not priceable."""
    ev = p31evidence.inventory()
    board = _read(BOARD_SQL)
    board_rows = board_two_sided = board_sessions = 0
    if board and board[0]:
        board_rows = int(board[0][0] or 0)
        board_two_sided = int(board[0][1] or 0)
        board_sessions = int(board[0][2] or 0)
    sources = _read(BOARD_SOURCE_SQL) or []
    real = int(ev["real_two_sided_decision_instants"]) + board_two_sided
    return {
        "chain_snapshots_by_source": ev["option_books"]["by_source"],
        "chain_real_broker_snapshots": ev["option_books"]["real_broker_snapshots"],
        "captured_two_sided_at_decision":
            ev["captured_observations"]["two_sided_at_decision"],
        "board_rows": board_rows,
        "board_rows_by_source": {(s or "UNKNOWN"): int(n) for s, n in sources},
        "board_two_sided_rows": board_two_sided,
        "board_sessions": board_sessions,
        "real_two_sided_observations": real,
        "min_required": MIN_OPTION_INSTANTS,
        "premium_hold_study": (
            MEASURABLE if real >= MIN_OPTION_INSTANTS else REQUIRES_MORE_DATA
        ),
        "why": (
            "enough real two-sided observations to price ask-in/bid-out"
            if real >= MIN_OPTION_INSTANTS else
            f"{real} real two-sided observation(s) against a floor of "
            f"{MIN_OPTION_INSTANTS}; every other stored row is simulated, "
            "one-sided or predates the provenance label, and \u00a74 forbids "
            "substituting midpoint, LTP or a nearby timestamp for an executable "
            "price, so premium movement stays unmeasured rather than estimated"
        ),
    }


def engine_signals() -> dict:
    """The engine's own recorded decisions, by label and instrument."""
    by_signal = _read(SIGNAL_SQL)
    if by_signal is None:
        return {"store_readable": False, "rows": 0}
    by_inst = _read(SIGNAL_INSTRUMENT_SQL) or []
    sessions = _read(SIGNAL_SESSION_SQL) or [(0,)]
    labels = {(s or "UNKNOWN"): int(n) for s, n in by_signal}
    actionable = int(labels.get("BUY", 0))
    return {
        "store_readable": True,
        "rows": sum(labels.values()),
        "by_label": labels,
        "by_instrument": {(i or "UNKNOWN"): int(n) for i, n in by_inst},
        "sessions": int(sessions[0][0] or 0),
        "actionable_buys": actionable,
        "engine_vs_board_study": (
            DIAGNOSTIC_ONLY if actionable else REQUIRES_MORE_DATA
        ),
        "why": (
            f"{actionable} recorded BUY decision(s) over "
            f"{int(sessions[0][0] or 0)} session(s): enough to describe where the "
            "engine acted and where the board offered something it did not, and "
            "far too few to validate a rule from"
            if actionable else
            "no recorded BUY decisions, so captured-versus-missed cannot be "
            "computed"
        ),
    }


# The 21 final questions, mapped to what the data can support. Written before any
# result exists so a question cannot quietly be reclassified once its answer is
# inconvenient.
QUESTION_SOURCES: dict[str, str] = {
    "how_much_does_each_signal_move": "FUTURES_FIVE_YEAR",
    "how_quickly_does_it_move": "FUTURES_FIVE_YEAR",
    "which_move_is_reached_most_often": "FUTURES_FIVE_YEAR",
    "which_move_has_the_best_net_expectancy": "FUTURES_FIVE_YEAR",
    "when_does_the_trade_peak": "FUTURES_FIVE_YEAR",
    "when_does_giveback_become_significant": "FUTURES_FIVE_YEAR",
    "is_15m_better_than_30m": "FUTURES_FIVE_YEAR",
    "is_30m_better_than_60m": "FUTURES_FIVE_YEAR",
    "is_60m_better_than_120m": "FUTURES_FIVE_YEAR",
    "which_setup_should_be_held_longer": "FUTURES_FIVE_YEAR",
    "which_setup_should_be_exited_quickly": "FUTURES_FIVE_YEAR",
    "does_the_engine_enter_too_early": "ENGINE_VS_BOARD",
    "does_the_engine_enter_too_late": "ENGINE_VS_BOARD",
    "what_does_the_board_contain_that_the_engine_misses": "ENGINE_VS_BOARD",
    "ce_or_pe_better_net_economics": "REAL_OPTION_BOOKS",
    "is_futures_better_than_options": "REAL_OPTION_BOOKS",
    "strongest_net_movement_combination": "REAL_OPTION_BOOKS",
    "which_holding_period_has_the_best_net_expectancy": "FUTURES_FIVE_YEAR",
    "which_target_has_the_highest_repeatable_reach_rate": "FUTURES_FIVE_YEAR",
    "does_any_result_survive_holdout_and_walk_forward": "FUTURES_FIVE_YEAR",
    "does_any_result_survive_cost_and_slippage_stress": "FUTURES_FIVE_YEAR",
}


def answerability() -> dict:
    """Per-question: which source answers it, and whether that source exists."""
    fut = futures_inventory()
    opt = option_evidence()
    eng = engine_signals()
    five = [r["instrument"] for r in fut if r["tier"] == FIVE_YEAR]
    available = {
        "FUTURES_FIVE_YEAR": MEASURABLE if five else REQUIRES_MORE_DATA,
        "REAL_OPTION_BOOKS": opt["premium_hold_study"],
        "ENGINE_VS_BOARD": eng.get("engine_vs_board_study", REQUIRES_MORE_DATA),
    }
    return {
        "futures": fut,
        "five_year_instruments": five,
        "short_capture_instruments": [
            r["instrument"] for r in fut if r["tier"] == SHORT_CAPTURE
        ],
        "options": opt,
        "engine": eng,
        "source_status": available,
        "questions": {
            q: {"source": src, "status": available[src]}
            for q, src in QUESTION_SOURCES.items()
        },
        "answerable_now": sum(
            1 for src in QUESTION_SOURCES.values()
            if available[src] in (MEASURABLE, DIAGNOSTIC_ONLY)
        ),
        "questions_total": len(QUESTION_SOURCES),
    }
