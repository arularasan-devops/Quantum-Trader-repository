"""Promotion gates and the one status per candidate (§31, §34).

Every gate is checked and every failure is named. A row does not become
`ROBUST_CANDIDATE` because it looks best; it becomes one only if it clears the
sample floor, survives correction over the full registered denominator, stays
positive after costs, repeats out of sample twice (validation, then the untouched
holdout, read once), repeats across stocks and years rather than living in one of
either, holds up at 2x costs, and keeps its drawdown inside the declared limit.

Ordering matters for honesty: `COST_BLOCKED` is checked before `NO_EDGE`, because
"the mechanism moved price in the right direction but the round trip ate it" is a
different finding from "there is nothing here", and collapsing the two would hide
that costs are the binding constraint on short-hold cash equity.
"""
from __future__ import annotations

import math

from . import (
    BROAD_UNIVERSE,
    COST_BLOCKED,
    HISTORICAL_LEAD,
    INSUFFICIENT_SAMPLE,
    MAX_DRAWDOWN_LIMIT,
    MAX_SINGLE_STOCK_PROFIT_SHARE,
    MAX_SINGLE_YEAR_PROFIT_SHARE,
    MIN_SESSIONS,
    MIN_STOCKS,
    MIN_TRADES,
    MULTI_STOCK,
    NO_EDGE,
    OVERFIT_RISK,
    PROMISING_NEEDS_DATA,
    ROBUST_CANDIDATE,
    SINGLE_STOCK,
    SMALL_CLUSTER,
)
from .engine import DISCOVERY, HOLDOUT, VALIDATION


def replication_breadth(stocks: int) -> str:
    if stocks <= 1:
        return SINGLE_STOCK
    if stocks < 10:
        return SMALL_CLUSTER
    if stocks < MIN_STOCKS:
        return MULTI_STOCK
    return BROAD_UNIVERSE


def grade(row: dict) -> dict:
    """Assign exactly one status, with every failed gate named."""
    discovery = row["partitions"][DISCOVERY]
    validation = row["partitions"][VALIDATION]
    holdout = row["partitions"][HOLDOUT]
    failures: list[str] = []

    trades = discovery.get("trades", 0)
    if trades < MIN_TRADES:
        failures.append(f"DISCOVERY_TRADES_{trades}_BELOW_{MIN_TRADES}")
    if discovery.get("stocks", 0) < MIN_STOCKS:
        failures.append(f"DISCOVERY_STOCKS_{discovery.get('stocks', 0)}_BELOW_{MIN_STOCKS}")
    if discovery.get("sessions", 0) < MIN_SESSIONS:
        failures.append(f"DISCOVERY_SESSIONS_{discovery.get('sessions', 0)}_BELOW_{MIN_SESSIONS}")

    status = None
    if failures:
        status = INSUFFICIENT_SAMPLE
    else:
        net = discovery.get("net_expectancy", 0.0)
        gross = discovery.get("gross_expectancy", net)
        if net <= 0 and gross > 0:
            failures.append("NET_NEGATIVE_WHILE_GROSS_POSITIVE_COSTS_CONSUME_THE_MOVE")
            status = COST_BLOCKED
        elif net <= 0:
            failures.append("DISCOVERY_NET_EXPECTANCY_NOT_POSITIVE")
            status = NO_EDGE
        elif not row.get("discovery_fdr_pass"):
            failures.append("FAILS_FDR_OVER_FULL_REGISTERED_DENOMINATOR")
            status = NO_EDGE

    if status is None:
        # Out-of-sample repetition, in order, each failure named.
        if validation.get("trades", 0) < 20:
            failures.append("VALIDATION_SAMPLE_TOO_SMALL_TO_GRADE")
            status = PROMISING_NEEDS_DATA
        elif validation.get("net_expectancy", 0.0) <= 0:
            failures.append("VALIDATION_NET_EXPECTANCY_NOT_POSITIVE")
            status = OVERFIT_RISK
        elif holdout.get("trades", 0) < 20:
            failures.append("HOLDOUT_SAMPLE_TOO_SMALL_TO_GRADE")
            status = PROMISING_NEEDS_DATA
        elif holdout.get("net_expectancy", 0.0) <= 0:
            failures.append("HOLDOUT_NET_EXPECTANCY_NOT_POSITIVE")
            status = OVERFIT_RISK

    if status is None:
        stressed = row["cost_stress"].get("2.0x", {})
        if stressed.get("net_expectancy", 0.0) <= 0:
            failures.append("NEGATIVE_AT_2X_MODELLED_COSTS")
        stock_share = discovery.get("stock_concentration", {}).get("top_share", math.inf)
        if stock_share > MAX_SINGLE_STOCK_PROFIT_SHARE:
            failures.append(f"SINGLE_STOCK_PROFIT_SHARE_{stock_share:.2f}")
        year_share = discovery.get("year_concentration", {}).get("top_share", math.inf)
        if year_share > MAX_SINGLE_YEAR_PROFIT_SHARE:
            failures.append(f"SINGLE_YEAR_PROFIT_SHARE_{year_share:.2f}")
        # The drawdown gate reads the capital-constrained book, never the
        # overlapping trade list. A row with no simulated book cannot be promoted.
        book = row.get("portfolio_drawdown")
        if book is None:
            failures.append("PORTFOLIO_DRAWDOWN_UNMEASURED")
        elif book > MAX_DRAWDOWN_LIMIT:
            failures.append(f"PORTFOLIO_DRAWDOWN_{book:.2f}_ABOVE_{MAX_DRAWDOWN_LIMIT}")
        walk = row.get("walk_forward", {})
        graded = walk.get("graded_folds", 0)
        if graded and walk.get("positive_folds", 0) < graded - 1:
            failures.append(
                f"WALK_FORWARD_POSITIVE_IN_{walk.get('positive_folds', 0)}_OF_{graded}_FOLDS"
            )
        status = PROMISING_NEEDS_DATA if failures else ROBUST_CANDIDATE

    row["status"] = status
    row["gate_failures"] = failures
    row["replication_breadth"] = replication_breadth(discovery.get("stocks", 0))
    row["evidence_ceiling"] = HISTORICAL_LEAD
    return row


def needs_portfolio(row: dict) -> bool:
    """True when the only thing standing between a row and promotion is the book."""
    return row.get("gate_failures") == ["PORTFOLIO_DRAWDOWN_UNMEASURED"]


def grade_all(rows: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        grade(row)
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "status_counts": dict(sorted(counts.items())),
        "robust_candidates": counts.get(ROBUST_CANDIDATE, 0),
    }
