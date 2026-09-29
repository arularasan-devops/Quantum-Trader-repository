"""The study (§11-§20, §23-§31). One pass over the registered grid, no tuning.

Order of operations, which is the whole point:

1. resolve every exit configuration once over the whole panel;
2. evaluate every registered entry once, producing an event set;
3. cost each (entry, exit) pair's events at 1.0x, 1.5x and 2.0x the modelled
   schedule;
4. measure **discovery only**, apply Benjamini-Hochberg over the full registered
   denominator, and freeze the passing set;
5. measure validation on that frozen set;
6. measure the untouched holdout on what survived validation — once;
7. walk-forward inside discovery+validation as an independent stability check.

The holdout is read in step 6 and nowhere else. Nothing in steps 1-5 can see it,
because the partition boundaries are computed from the session axis before any
measurement and every statistic is taken on an explicitly sliced index range.

Separation of edges (§9) is reported rather than assumed: ENTRY_EDGE is the
mechanism's net expectancy against the same-horizon return of a random eligible
name on the same sessions; EXIT_EDGE is the spread between exit rules holding the
entry fixed; STOCK_SELECTION_EDGE is the entry's advantage over the eligible
universe mean; PORTFOLIO_CONSTRUCTION_EFFECT is what the portfolio layer adds on
top of the trade-level result.
"""
from __future__ import annotations

import numpy as np

from . import (
    COST_MULTIPLIERS,
    DISCOVERY_FRACTION,
    ENTRY_GRID,
    EXIT_GRID,
    FDR_ALPHA,
    MIN_SESSIONS,
    MIN_STOCKS,
    MIN_TRADES,
    TOTAL_HYPOTHESES,
    VALIDATION_FRACTION,
    WALK_FORWARD_FOLDS,
)
from . import stats as st
from .exits import REASONS, resolve
from .mechanisms import signal
from .panel import Panel
from .tradecosts import net_returns, regime_arrays

DISCOVERY = "DISCOVERY"
VALIDATION = "VALIDATION"
HOLDOUT = "UNTOUCHED_HOLDOUT"


class Partitions:
    """Chronological row-index ranges. Computed once, before any measurement."""

    def __init__(self, days: int):
        discovery_end = int(days * DISCOVERY_FRACTION)
        validation_end = int(days * (DISCOVERY_FRACTION + VALIDATION_FRACTION))
        self.bounds = {
            DISCOVERY: (0, discovery_end),
            VALIDATION: (discovery_end, validation_end),
            HOLDOUT: (validation_end, days),
        }
        self.days = days

    def mask(self, rows: np.ndarray, name: str) -> np.ndarray:
        start, end = self.bounds[name]
        return (rows >= start) & (rows < end)

    def as_dict(self, dates: np.ndarray) -> dict:
        out = {}
        for name, (start, end) in self.bounds.items():
            out[name] = {
                "sessions": int(end - start),
                "from": str(dates[start]) if end > start else "",
                "to": str(dates[end - 1]) if end > start else "",
            }
        return out


class Events:
    """One entry mechanism's event set, exit-independent."""

    __slots__ = ("name", "spec", "rows", "columns", "family")

    def __init__(self, name, spec, rows, columns):
        self.name = name
        self.spec = spec
        self.rows = rows
        self.columns = columns
        self.family = st.event_family(rows, columns)

    def __len__(self) -> int:
        return int(self.rows.size)


def build_events(panel: Panel, grid=ENTRY_GRID) -> list[Events]:
    out = []
    for spec in grid:
        mask = signal(panel, spec)
        rows, columns = np.nonzero(mask)
        out.append(Events(spec["name"], spec, rows.astype(np.int64), columns.astype(np.int64)))
    return out


def _horizon_reference(panel: Panel, hold: int) -> np.ndarray:
    """Eligible-universe mean forward return over ``hold`` sessions (§9, §17).

    The honest comparator for "did the selection do anything": buy every eligible
    name at the same open, hold the same number of sessions, cost it the same way.
    """
    entry = np.where(panel.open > 0, panel.open, np.nan).astype(np.float64)
    exit_close = np.full_like(entry, np.nan)
    days = entry.shape[0]
    if hold <= days:
        exit_close[: days - hold + 1] = panel.close[hold - 1 :].astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        forward = exit_close / entry - 1.0
    forward = np.where(panel.eligible, forward, np.nan)
    finite = np.isfinite(forward)
    counts = finite.sum(axis=1)
    totals = np.where(finite, forward, 0.0).sum(axis=1)
    return np.where(counts > 0, totals / np.maximum(counts, 1), np.nan)


def evaluate(panel: Panel, *, grid=ENTRY_GRID, exits=EXIT_GRID, progress=None) -> dict:
    """Trade-level results for every registered (entry, exit) pair."""
    days = panel.shape[0]
    partitions = Partitions(days)
    rates = regime_arrays(panel.dates)
    years = panel.years()
    events = build_events(panel, grid)
    reference = {}

    rows_out: list[dict] = []
    for exit_config in exits:
        outcome = resolve(panel, exit_config)
        hold = int(exit_config["hold"])
        if hold not in reference:
            reference[hold] = _horizon_reference(panel, hold)
        for event in events:
            rows_out.append(
                _row(panel, partitions, rates, years, event, outcome, reference[hold])
            )
            if progress:
                progress(exit_config["name"], event.name, rows_out[-1])
    return {
        "partitions": partitions.as_dict(panel.dates),
        "total_hypotheses": TOTAL_HYPOTHESES,
        "evaluated_rows": len(rows_out),
        "rows": rows_out,
        "event_families": _families(events),
    }


def _families(events: list[Events]) -> dict:
    """§19 deduplication: grid rows sharing an identical event set are one family."""
    grouped: dict[str, list[str]] = {}
    for event in events:
        grouped.setdefault(event.family, []).append(event.name)
    return {
        "unique_event_families": len(grouped),
        "registered_entries": len(events),
        "duplicate_groups": {key: names for key, names in grouped.items() if len(names) > 1},
    }


def _row(panel, partitions, rates, years, event: Events, outcome, reference) -> dict:
    rows, columns = event.rows, event.columns
    entry_price = outcome.entry_price[rows, columns]
    exit_price = outcome.exit_price[rows, columns]
    reason = outcome.reason[rows, columns]
    holding = outcome.holding[rows, columns]
    mfe = outcome.mfe[rows, columns]
    mae = outcome.mae[rows, columns]

    resolved = (reason > 0) & np.isfinite(entry_price) & np.isfinite(exit_price) & (entry_price > 0)
    rows_r = rows[resolved]
    columns_r = columns[resolved]
    entry_price = entry_price[resolved]
    exit_price = exit_price[resolved]
    holding_r = np.maximum(holding[resolved].astype(np.int64), 1)
    reason_r = reason[resolved]
    sell_index = np.minimum(rows_r + holding_r - 1, panel.shape[0] - 1)

    gross = exit_price / entry_price - 1.0
    net = {
        multiplier: net_returns(
            entry_price=entry_price,
            exit_price=exit_price,
            buy_rate_index=rows_r,
            sell_rate_index=sell_index,
            rates=rates,
            multiplier=multiplier,
        )
        for multiplier in COST_MULTIPLIERS
    }
    base = net[1.0]

    out: dict = {
        "entry": event.name,
        "entry_family": event.spec["family"],
        "entry_params": dict(event.spec.get("params", {})),
        "exit": outcome.name,
        "exit_config": outcome.config,
        "event_family": event.family,
        "signals": len(event),
        "resolved_trades": int(resolved.sum()),
        "unresolved_trades": int((~resolved).sum()),
        "partitions": {},
        "cost_stress": {},
        "exit_reasons": {
            REASONS[code]: int((reason_r == code).sum()) for code in np.unique(reason_r)
        },
        "median_holding_sessions": float(np.median(holding_r)) if holding_r.size else 0.0,
        "mfe_median": float(np.nanmedian(mfe[resolved])) if resolved.any() else 0.0,
        "mae_median": float(np.nanmedian(mae[resolved])) if resolved.any() else 0.0,
    }

    for name in (DISCOVERY, VALIDATION, HOLDOUT):
        inside = partitions.mask(rows_r, name)
        block = st.summary(base[inside], gross[inside])
        block.update(st.equity_curve(base[inside]))
        block["stocks"] = int(np.unique(columns_r[inside]).size)
        block["sessions"] = int(np.unique(rows_r[inside]).size)
        if inside.any():
            block["benchmark_same_horizon"] = float(np.nanmean(reference[rows_r[inside]]))
            block["stock_selection_edge"] = block.get("net_expectancy", 0.0) - block[
                "benchmark_same_horizon"
            ]
            block["stock_concentration"] = st.concentration(
                base[inside], panel.symbols[columns_r[inside]]
            )
            block["year_concentration"] = st.concentration(base[inside], years[rows_r[inside]])
            block["per_year"] = _per_year(base[inside], years[rows_r[inside]])
        out["partitions"][name] = block

    discovery_mask = partitions.mask(rows_r, DISCOVERY)
    for multiplier, values in net.items():
        stressed = st.summary(values[discovery_mask])
        out["cost_stress"][f"{multiplier:.1f}x"] = {
            "net_expectancy": stressed.get("net_expectancy", 0.0),
            "profit_factor": stressed.get("profit_factor", 0.0),
            "trades": stressed.get("trades", 0),
        }
    out["walk_forward"] = _walk_forward(partitions, rows_r, base)
    return out


def _per_year(net: np.ndarray, years: np.ndarray) -> dict:
    out = {}
    for year in np.unique(years):
        inside = years == year
        values = net[inside]
        out[str(year)] = {
            "trades": int(values.size),
            "net_expectancy": float(values.mean()) if values.size else 0.0,
            "total_net": float(values.sum()),
        }
    return out


def _walk_forward(partitions: Partitions, rows: np.ndarray, net: np.ndarray) -> dict:
    """Expanding-origin folds inside discovery+validation only (§18)."""
    _, end = partitions.bounds[VALIDATION]
    if end <= 0:
        return {"folds": [], "positive_folds": 0}
    edges = np.linspace(0, end, WALK_FORWARD_FOLDS + 1).astype(int)
    folds = []
    for index in range(WALK_FORWARD_FOLDS):
        start, stop = edges[index], edges[index + 1]
        inside = (rows >= start) & (rows < stop)
        values = net[inside]
        folds.append(
            {
                "fold": index + 1,
                "sessions": int(stop - start),
                "trades": int(values.size),
                "net_expectancy": float(values.mean()) if values.size else 0.0,
            }
        )
    return {
        "folds": folds,
        "positive_folds": sum(1 for fold in folds if fold["net_expectancy"] > 0),
        "graded_folds": sum(1 for fold in folds if fold["trades"] >= 20),
    }


def apply_fdr(rows: list[dict], *, alpha: float = FDR_ALPHA, denominator: int = TOTAL_HYPOTHESES) -> dict:
    """§19 over the whole denominator, on discovery p-values only.

    A row with too few trades is not tested; it is labelled INSUFFICIENT_SAMPLE
    and still occupies a slot in the denominator, which is the conservative
    direction — testing fewer rows against the same denominator cannot make a
    survivor appear.
    """
    gradeable = []
    for row in rows:
        block = row["partitions"][DISCOVERY]
        eligible = (
            block.get("trades", 0) >= MIN_TRADES
            and block.get("stocks", 0) >= MIN_STOCKS
            and block.get("sessions", 0) >= MIN_SESSIONS
        )
        row["discovery_gradeable"] = bool(eligible)
        if eligible:
            gradeable.append(row)
    p_values = [row["partitions"][DISCOVERY].get("p_value", 1.0) for row in gradeable]
    passed = st.benjamini_hochberg(p_values, alpha, denominator)
    for row, verdict in zip(gradeable, passed):
        row["discovery_fdr_pass"] = bool(verdict)
    for row in rows:
        row.setdefault("discovery_fdr_pass", False)
    return {
        "denominator": denominator,
        "alpha": alpha,
        "gradeable_rows": len(gradeable),
        "fdr_survivors": sum(1 for row in rows if row["discovery_fdr_pass"]),
        "min_p_value": min(p_values) if p_values else 1.0,
    }
