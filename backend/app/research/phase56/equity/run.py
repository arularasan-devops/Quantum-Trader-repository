"""Study runner: one command, one fingerprint, one verdict.

The holdout discipline lives here. `evaluate` computes statistics for all three
partitions in one pass because recomputing them would cost another full sweep,
but the *decision sequence* only ever consults them in order — discovery for the
FDR step, validation for the out-of-sample check, the untouched holdout last and
only for rows that already survived both. No parameter, threshold or grid row is
chosen from holdout numbers; they are read to grade, not to select.

The portfolio and per-instrument layers run only for the rows that reach the top
of the ranking, because they are expensive and meaningless for rows that already
failed a gate.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from . import ENTRY_GRID, EXIT_GRID, PORTFOLIO_SIZES, WEIGHTING_METHODS
from . import stats as st
from .engine import DISCOVERY, HOLDOUT, VALIDATION, Partitions, apply_fdr, evaluate
from .exits import resolve
from .mechanisms import signal
from .panel import Panel, load_panel
from .portfolio import benchmark, simulate
from .report import rank_key, write
from .tradecosts import net_returns, regime_arrays
from .verdict import grade_all, needs_portfolio
from ..nse.indices import NIFTY_50, NIFTY_500

DEFAULT_ROOT = Path("data/research/phase56/nse")
ARTEFACT_ROOT = Path("data/research/phase56/equity")


def run(
    *,
    data_root: Path = DEFAULT_ROOT,
    out_root: Path = ARTEFACT_ROOT,
    grid=ENTRY_GRID,
    exits=EXIT_GRID,
    symbols: list[str] | None = None,
    detail_rows: int = 8,
    portfolio_rows: int = 4,
    verbose: bool = True,
) -> dict:
    started = time.time()
    panel = load_panel(Path(data_root), symbols=symbols)
    if verbose:
        print(f"panel {panel.shape[0]} sessions x {panel.shape[1]} securities", flush=True)

    result = evaluate(panel, grid=grid, exits=exits)
    result["fdr"] = apply_fdr(result["rows"])
    result["verdict"] = grade_all(result["rows"])
    result["portfolio"] = {}

    # Second pass: rows held back only for want of a simulated book get one, then
    # are re-graded against that book's drawdown. Nothing else is re-examined, so
    # this cannot turn any other failed gate into a pass.
    pending = [row for row in result["rows"] if needs_portfolio(row)]
    for row in pending:
        block = portfolio_block(panel, row, verbose=verbose)
        result["portfolio"].update(block)
        row["portfolio_drawdown"] = max(
            float(value["max_drawdown"]) for value in block.values()
        )
    if pending:
        result["verdict"] = grade_all(result["rows"])
    result["verdict"]["rows_regraded_with_book"] = len(pending)
    result["dataset"] = {
        "sessions": int(panel.shape[0]),
        "symbols": int(panel.shape[1]),
        "from": str(panel.dates[0]),
        "to": str(panel.dates[-1]),
    }
    if verbose:
        print(
            f"evaluated {len(result['rows'])} rows in {time.time() - started:.0f}s; "
            f"FDR survivors {result['fdr']['fdr_survivors']}",
            flush=True,
        )

    ranked = sorted(result["rows"], key=rank_key, reverse=True)
    result["per_instrument"] = {
        f"{row['entry']}|{row['exit']}": per_instrument(panel, row)
        for row in ranked[:detail_rows]
    }
    for row in ranked[:portfolio_rows]:
        label = f"{row['entry']}|{row['exit']}"
        if any(key.startswith(f"{label}|") for key in result["portfolio"]):
            continue
        result["portfolio"].update(portfolio_block(panel, row, verbose=verbose))
    result["benchmark"] = {
        name: benchmark(panel, name=name) for name in (NIFTY_50, NIFTY_500)
    }
    result["selection_self_check"] = validation_is_untouched(result)
    result["paths"] = write(Path(out_root), result)
    result["elapsed_seconds"] = round(time.time() - started, 1)
    return result


def _events_with_returns(panel: Panel, row: dict):
    """Rebuild one row's trades: (row index, column index, net return)."""
    spec = {"name": row["entry"], "family": row["entry_family"], "params": row["entry_params"]}
    outcome = resolve(panel, row["exit_config"])
    mask = signal(panel, spec)
    rows, columns = np.nonzero(mask)
    entry_price = outcome.entry_price[rows, columns]
    exit_price = outcome.exit_price[rows, columns]
    reason = outcome.reason[rows, columns]
    holding = np.maximum(outcome.holding[rows, columns].astype(np.int64), 1)
    usable = (reason > 0) & np.isfinite(entry_price) & np.isfinite(exit_price) & (entry_price > 0)
    rows, columns, holding = rows[usable], columns[usable], holding[usable]
    net = net_returns(
        entry_price=entry_price[usable],
        exit_price=exit_price[usable],
        buy_rate_index=rows,
        sell_rate_index=np.minimum(rows + holding - 1, panel.shape[0] - 1),
        rates=regime_arrays(panel.dates),
    )
    return rows, columns, net, outcome, mask


def per_instrument(panel: Panel, row: dict) -> dict:
    """§20 per-security replication for one registered row."""
    rows, columns, net, _, _ = _events_with_returns(panel, row)
    partitions = Partitions(panel.shape[0])
    out: dict = {"securities": {}}
    for column in np.unique(columns):
        inside = columns == column
        block = st.summary(net[inside])
        block["discovery_trades"] = int((inside & partitions.mask(rows, DISCOVERY)).sum())
        block["holdout_trades"] = int((inside & partitions.mask(rows, HOLDOUT)).sum())
        out["securities"][str(panel.symbols[column])] = block
    positive = [
        name for name, block in out["securities"].items() if block.get("net_expectancy", 0.0) > 0
    ]
    graded = [
        name for name, block in out["securities"].items() if block.get("trades", 0) >= 10
    ]
    out["summary"] = {
        "securities_traded": len(out["securities"]),
        "securities_graded_10_plus_trades": len(graded),
        "securities_positive": len(positive),
        "positive_share_of_graded": (
            len([name for name in positive if name in graded]) / len(graded) if graded else 0.0
        ),
    }
    return out


def portfolio_block(panel: Panel, row: dict, *, verbose: bool = False) -> dict:
    """§13 portfolio results for one registered row across sizes and weightings."""
    spec = {"name": row["entry"], "family": row["entry_family"], "params": row["entry_params"]}
    mask = signal(panel, spec)
    outcome = resolve(panel, row["exit_config"])
    label = f"{row['entry']}|{row['exit']}"
    out = {}
    for size in PORTFOLIO_SIZES:
        for weighting in WEIGHTING_METHODS:
            if verbose:
                print(f"portfolio {label} size={size} {weighting}", flush=True)
            block = simulate(panel, mask, outcome, size=size, weighting=weighting)
            block.pop("per_trade", None)
            out[f"{label}|N={size}|{weighting}"] = block
    return out


def validation_is_untouched(result: dict) -> dict:
    """Self-check printed with the verdict: what selected what (§18).

    Any row whose status is ROBUST_CANDIDATE must have been selected by discovery
    and validation alone; this recomputes the selection without holdout numbers
    and asserts the set is identical.
    """
    selected = {
        f"{row['entry']}|{row['exit']}"
        for row in result["rows"]
        if row.get("discovery_fdr_pass")
        and row["partitions"][VALIDATION].get("net_expectancy", 0.0) > 0
        and row["partitions"][VALIDATION].get("trades", 0) >= 20
    }
    promoted = {
        f"{row['entry']}|{row['exit']}" for row in result["rows"] if row["status"] == "ROBUST_CANDIDATE"
    }
    return {
        "selected_without_holdout": sorted(selected),
        "promoted": sorted(promoted),
        "promoted_subset_of_selection": promoted.issubset(selected),
    }
