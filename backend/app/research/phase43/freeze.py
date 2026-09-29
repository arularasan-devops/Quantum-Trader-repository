"""Phase 43 — the definition, hashed before the study is run.

Same mechanism as Phase 42's freeze, and here for the same reason: two runs that
report different numbers must be able to say which definition each was measured
under. The hash covers the functions that decide what a candidate *is* and what
its label means, plus the declared constants; it does not cover rendering, so a
reworded report cannot invalidate a stored artefact.

Nothing in this module imports Phase 41 or Phase 42, so no change here can move
their fingerprints.
"""
from __future__ import annotations

import sys
from collections.abc import Callable

from app.research import fingerprint as fp
from app.research import phase43
from app.research.phase43 import evaluate, mechanisms, pair, stats

# What a candidate is: the ratios, the baseline rule and the cohort selection.
DEFINITION: tuple[Callable, ...] = (
    mechanisms.cost_estimate, mechanisms.ratios, mechanisms.declared,
    mechanisms.regime_candidates, evaluate.Book.cohort,
)
# What the pair relationship is, hashed apart: a change to it cannot move a
# family A, C or D number.
PAIR: tuple[Callable, ...] = (
    pair.align, pair.signals, pair.trades, pair._trailing_beta,
    pair._trailing_sigma, pair.declared,
)
# How a measured cohort becomes statistics.
METRICS: tuple[Callable, ...] = (
    stats.summarise, stats.profit_factor, stats.max_drawdown,
    stats.session_concentration, stats.t_statistic, stats.p_value_one_sided,
)
# How statistics become a label. Separated because a change here changes which
# answer is published without changing a single measured figure.
LABEL: tuple[Callable, ...] = (
    evaluate.split_sessions, evaluate.status, evaluate._meaningful, evaluate.fdr,
)

COMPONENTS: dict[str, tuple[Callable, ...]] = {
    "definition": DEFINITION, "pair": PAIR, "metrics": METRICS, "label": LABEL,
}


def declared() -> dict:
    """The declared numbers, read from the modules rather than restated here."""
    return {
        "stop_atr": phase43.STOP_ATR,
        "t1_r": phase43.T1_R,
        "dev_share": phase43.DEV_SHARE,
        "val_share": phase43.VAL_SHARE,
        "min_trades": phase43.MIN_TRADES,
        "min_sessions": phase43.MIN_SESSIONS,
        "min_expectancy_r": phase43.MIN_EXPECTANCY_R,
        "min_expectancy_bps": phase43.MIN_EXPECTANCY_BPS,
        "max_single_session_share_pct": phase43.MAX_SINGLE_SESSION_SHARE_PCT,
        "cost_multipliers": list(phase43.COST_MULTIPLIERS),
        "fdr_alpha": phase43.FDR_ALPHA,
        "base_rule": list(mechanisms.BASE),
        "ratio_arms": list(mechanisms.RATIO_ARMS),
        "regime_states": [s for s, _ in mechanisms.REGIME_STATES],
        "time_states": [s for s, _ in mechanisms.TIME_STATES],
        "pair_return_bars": pair.RET_BARS,
        "pair_trailing_bars": pair.TRAIL_BARS,
        "pair_hold_bars": pair.HOLD_BARS,
        "pair_z_arms": list(pair.Z_ARMS),
        "pair_notional_per_leg": pair.NOTIONAL_PER_LEG,
        "version": phase43.VERSION,
    }


def fingerprint() -> dict:
    """One hash for the definition, one per component so a move is diagnosable."""
    hashed = fp.components(COMPONENTS)
    parts: dict[str, str] = dict(hashed["components"])
    numbers = declared()
    parts["declared"] = fp.digest([f"{k}={numbers[k]}" for k in sorted(numbers)])
    return {
        "definition": fp.digest([f"{k}={parts[k]}" for k in sorted(parts)]),
        "components": parts,
        "covers": hashed["covers"],
        "declared": numbers,
        "hashed": (
            "syntax trees of the functions that define each candidate, resolve "
            "the pair, compute the statistics and assign the label, with "
            "docstrings removed, plus every declared constant"
        ),
        "not_hashed": (
            "docstrings, comments, formatting, report rendering and CLI output "
            "— none of which can change a measured number"
        ),
        "interpreter": "%d.%d.%d" % sys.version_info[:3],
    }
