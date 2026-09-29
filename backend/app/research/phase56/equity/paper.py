"""§27-§30 paper scanner and journal. Paper only, fail-closed, no order path.

This module answers the six operational questions for a *frozen* candidate:
which share, when to buy, at what price condition, where it is invalidated, when
to sell, and when to say `NO_TRADE`. It is deliberately the thinnest possible
layer over the frozen research spec, so a paper row cannot describe a rule the
study did not test.

Two fail-closed properties, both asserted by the smoke suite:

* a `PAPER_BUY` is emitted only for a candidate whose status is
  `ROBUST_CANDIDATE`. Anything else — including the best-looking row in the grid
  — yields `NO_TRADE` with the reason naming the status. Zero robust candidates
  therefore produces a scanner that emits nothing but `NO_TRADE`, which is the
  correct behaviour, not a broken one;
* there is no broker import, no order function and no network call anywhere in
  this file. The journal is a local append-only JSONL, and every row carries
  `PAPER_ONLY` and the `HISTORICAL_LEAD` evidence ceiling.

The entry price is expressed as a *condition* ("market order at the next session
open"), never as a promise, because daily bars carry no executable book and the
fill cannot be known in advance.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import (
    EVIDENCE_CEILING,
    EXIT_STOP,
    EXIT_TARGET,
    EXIT_TIME,
    HOLD,
    NO_TRADE,
    PAPER_BUY,
    PAPER_CAPITAL_INR,
    POSITION_NOTIONAL_INR,
    ROBUST_CANDIDATE,
)
from .mechanisms import signal
from .panel import Panel

PAPER_ONLY = "PAPER_ONLY"
JOURNAL_NAME = "paper_journal.jsonl"

STATE_OPEN = "OPEN"
STATE_CLOSED = "CLOSED"
SIGNAL_INVALIDATED = "SIGNAL_INVALIDATED"


def _stop_and_target(panel: Panel, row: int, column: int, exit_config: dict) -> tuple[float | None, float | None]:
    atr = float(panel.features["atr"][row, column])
    reference = float(panel.features["prev_close"][row, column])
    stop = None
    target = None
    if np.isfinite(atr) and atr > 0:
        if exit_config.get("stop_atr"):
            stop = reference - exit_config["stop_atr"] * atr
        if exit_config.get("target_atr"):
            target = reference + exit_config["target_atr"] * atr
    if exit_config.get("prev_low_stop"):
        low = float(panel.features["prev_low"][row, column])
        if np.isfinite(low):
            stop = low if stop is None else min(stop, low)
    return stop, target


def scan(
    panel: Panel,
    candidate: dict,
    *,
    as_of: str | None = None,
    max_positions: int = 5,
    capital: float = PAPER_CAPITAL_INR,
) -> dict:
    """Decide for the session *after* the last stored session.

    ``candidate`` is a frozen row from the study, carrying `entry_spec`,
    `exit_config`, `status` and the fingerprint of the run that produced it.
    """
    dates = list(map(str, panel.dates))
    stamp = as_of or dates[-1]
    if stamp not in dates:
        return _no_trade(stamp, candidate, "SESSION_NOT_IN_DATASET")
    row = dates.index(stamp)

    status = candidate.get("status")
    if status != ROBUST_CANDIDATE:
        return _no_trade(stamp, candidate, f"CANDIDATE_STATUS_{status}_NOT_{ROBUST_CANDIDATE}")

    matrix = signal(panel, candidate["entry_spec"])
    columns = np.nonzero(matrix[row])[0]
    if columns.size == 0:
        return _no_trade(stamp, candidate, "NO_ELIGIBLE_NAME_MET_THE_REGISTERED_CONDITION")

    # Deterministic rationing, identical to the portfolio layer.
    order = np.argsort(-np.nan_to_num(panel.turnover[row, columns], nan=-np.inf))
    chosen = columns[order][:max_positions]
    exit_config = candidate["exit_config"]
    notional = min(POSITION_NOTIONAL_INR, capital / max(max_positions, 1))

    picks = []
    for column in chosen:
        stop, target = _stop_and_target(panel, row, int(column), exit_config)
        reference = float(panel.features["prev_close"][row, int(column)])
        picks.append(
            {
                "decision": PAPER_BUY,
                "book": PAPER_ONLY,
                "symbol": str(panel.symbols[column]),
                "signal_session": stamp,
                "entry_condition": "MARKET_ORDER_AT_NEXT_SESSION_OPEN",
                "expected_entry_reference_price": reference,
                "expected_entry_range_note": "NO_EXECUTABLE_BOOK_IN_DAILY_DATA_FILL_PRICE_UNKNOWN_IN_ADVANCE",
                "initial_stop": stop,
                "invalidation_rule": _invalidation_text(exit_config),
                "target": target,
                "exit_rule": _exit_text(exit_config),
                "max_holding_sessions": int(exit_config["hold"]),
                "position_notional_inr": notional,
                "reason": _reason_text(candidate),
                "evidence_reference": {
                    "entry": candidate.get("entry"),
                    "exit": candidate.get("exit"),
                    "event_family": candidate.get("event_family"),
                    "study_fingerprint": candidate.get("study_fingerprint"),
                    "holdout_net_expectancy": candidate.get("partitions", {})
                    .get("UNTOUCHED_HOLDOUT", {})
                    .get("net_expectancy"),
                },
                "evidence_ceiling": EVIDENCE_CEILING,
            }
        )
    return {
        "session": stamp,
        "decision": PAPER_BUY,
        "candidates": picks,
        "book": PAPER_ONLY,
        "mode": PAPER_ONLY,
        "evidence_ceiling": EVIDENCE_CEILING,
        "study_fingerprint": candidate.get("study_fingerprint"),
    }


def _no_trade(stamp: str, candidate: dict, reason: str) -> dict:
    return {
        "session": stamp,
        "decision": NO_TRADE,
        "reason": reason,
        "candidate": candidate.get("entry"),
        "exit": candidate.get("exit"),
        "book": PAPER_ONLY,
        "mode": PAPER_ONLY,
        "evidence_ceiling": EVIDENCE_CEILING,
        "study_fingerprint": candidate.get("study_fingerprint"),
        "candidates": [],
    }


def _invalidation_text(exit_config: dict) -> str:
    if exit_config.get("prev_low_stop"):
        return "CLOSE_OR_TRADE_BELOW_SIGNAL_SESSION_LOW"
    if exit_config.get("stop_atr"):
        return f"TRADE_BELOW_ENTRY_MINUS_{exit_config['stop_atr']}_ATR"
    return "NO_PRICE_INVALIDATION_TIME_EXIT_ONLY"


def _exit_text(exit_config: dict) -> str:
    parts = [f"TIME_EXIT_AFTER_{exit_config['hold']}_SESSIONS"]
    if exit_config.get("target_atr"):
        parts.append(f"TARGET_AT_ENTRY_PLUS_{exit_config['target_atr']}_ATR")
    if exit_config.get("ma_exit"):
        parts.append(f"EXIT_ON_CLOSE_BELOW_MA_{exit_config['ma_exit']}")
    if exit_config.get("trail_atr"):
        parts.append(f"TRAIL_{exit_config['trail_atr']}_ATR_FROM_HIGHEST_CLOSE")
    return " | ".join(parts)


def _reason_text(candidate: dict) -> str:
    params = candidate.get("entry_spec", {}).get("params", {})
    return (
        f"{candidate.get('entry_family')} {json.dumps(params, sort_keys=True)} "
        f"measured on {candidate.get('partitions', {}).get('DISCOVERY', {}).get('trades', 0)} discovery trades"
    )


class Journal:
    """Append-only paper journal. One JSON object per line, never rewritten."""

    def __init__(self, root: Path):
        self.path = Path(root) / JOURNAL_NAME
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lines: set[str] | None = None

    def append(self, payload: dict) -> bool:
        """Append unless this exact row is already journalled.

        Re-running a scan for the same session must not inflate the record, so
        an identical serialisation is a no-op rather than a second row.
        """
        line = json.dumps({**payload, "book": PAPER_ONLY}, sort_keys=True, default=str)
        if line in self._existing():
            return False
        with self.path.open("a") as handle:
            handle.write(line + "\n")
        self._lines.add(line)
        return True

    def _existing(self) -> set[str]:
        if self._lines is None:
            self._lines = set(
                line for line in (self.path.read_text().splitlines() if self.path.exists() else []) if line.strip()
            )
        return self._lines

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def record_scan(self, result: dict) -> int:
        """Journal a scan: every NO_TRADE and every PAPER_BUY, both first class."""
        if result["decision"] == NO_TRADE:
            return int(self.append({"type": "DECISION", **result}))
        written = 0
        for pick in result["candidates"]:
            written += int(self.append({"type": "DECISION", "session": result["session"], **pick}))
        return written


def post_entry_state(
    panel: Panel,
    position: dict,
    *,
    as_of: str,
) -> dict:
    """Current state of an open paper position: HOLD / STOP / TARGET / TIME_EXIT.

    Evaluated on stored sessions only. A stop and a target touched in the same
    session resolve to the stop, matching the study's resolution rule.
    """
    dates = list(map(str, panel.dates))
    symbols = list(map(str, panel.symbols))
    if position["symbol"] not in symbols:
        # A name the dataset does not carry cannot be marked or exited honestly.
        return {"state": SIGNAL_INVALIDATED, "reason": "SYMBOL_NOT_IN_DATASET"}
    if as_of not in dates or position["entry_session"] not in dates:
        return {"state": SIGNAL_INVALIDATED, "reason": "SESSION_NOT_IN_DATASET"}
    column = symbols.index(position["symbol"])
    entry_row = dates.index(position["entry_session"])
    row = dates.index(as_of)
    stop = position.get("initial_stop")
    target = position.get("target")
    hold = int(position.get("max_holding_sessions", 0))

    for step in range(entry_row, min(row, panel.shape[0] - 1) + 1):
        low = float(panel.low[step, column])
        high = float(panel.high[step, column])
        if stop is not None and np.isfinite(low) and low <= stop:
            return {"state": EXIT_STOP, "session": dates[step], "price": float(min(stop, panel.open[step, column]))}
        if target is not None and np.isfinite(high) and high >= target:
            return {"state": EXIT_TARGET, "session": dates[step], "price": float(max(target, panel.open[step, column]))}
        if hold and step - entry_row + 1 >= hold:
            return {"state": EXIT_TIME, "session": dates[step], "price": float(panel.close[step, column])}
    if not np.isfinite(panel.close[row, column]):
        return {"state": SIGNAL_INVALIDATED, "reason": "SYMBOL_STOPPED_PRINTING"}
    return {"state": HOLD, "session": as_of, "mark": float(panel.close[row, column])}
