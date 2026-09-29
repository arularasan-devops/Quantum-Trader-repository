"""Exit-rule counterfactuals over the candidate pool. RESEARCH ONLY.

Fifteen entry conditions have now been graded across five years and not one of
them clears the promotion gate. Meanwhile the same holdout book shows a mean
maximum favourable excursion of about +1R against a mean adverse excursion of
about -0.9R, and roughly a fifth of candidates reach +0.5R and still end as
losses. That is where the money is being left: not in which candidates are
taken, but in what happens after entry.

This module re-prices every candidate under alternative exit rules. When the
pool was replayed with per-bar path capture the row carries ``alt_exits`` — the
variant's outcome simulated in the order the bars actually happened — and that
is used in preference to everything below. A pool saved before path capture has
only the excursions, so the estimates below are used instead and the report says
which basis it ran on. The two can differ materially in one direction: an
excursion estimate credits a breakeven stop with every loser it rescues and
charges it for none of the winners it scratches out.

**The assumption the excursion estimates rest on, stated once.** A pool row carries
``mfe_r`` (best the trade ever was), ``mae_r`` (worst it ever was) and ``r``
(what it actually made), but not the *path*. So the ordering of the peak and the
trough inside the trade is unknown in general. For a trade that ended at its
stop the peak necessarily happened *before* the exit, which is what makes an
earlier target answerable: if a loser was ever up 0.6R, a 0.5R target would have
been filled first. For a trade that ended at its target the peak is the target
itself. Any rule whose outcome depends on the order of two *interior* points —
a trailing stop, a re-entry, a time stop — is not answerable from these fields,
and this module refuses those rather than guessing: they return ``None`` with
``REQUIRES_PATH_DATA``.

Every figure is gross. An earlier target lowers R per trade without lowering the
per-trade cost, so a variant that wins here on gross R can still lose net, and
the report says so.
"""
from __future__ import annotations

from app.research.phase15 import folds as folds_mod

REQUIRES_PATH_DATA = "REQUIRES_PATH_DATA"

# Which evidence a variant's numbers came from. Never mixed within one variant:
# a book where some rows were replayed with paths and some were not would
# average two different questions.
PATH_MEASURED = "PATH_MEASURED"
EXCURSION_ESTIMATE = "EXCURSION_ESTIMATE"


def path_priced(row: dict, name: str) -> tuple[float | None, str]:
    """The variant's outcome as simulated bar by bar during the replay."""
    alt = row.get("alt_exits")
    if not isinstance(alt, dict) or name not in alt:
        return None, "this pool was replayed without per-bar path capture"
    try:
        return float(alt[name]), "simulated on the recorded bar path"
    except (TypeError, ValueError):
        return None, "the recorded path outcome is not a number"


def path_coverage(trades: list[dict], name: str) -> float:
    """Share of rows carrying a path-measured outcome for ``name``."""
    if not trades:
        return 0.0
    have = sum(1 for t in trades if path_priced(t, name)[0] is not None)
    return have / len(trades)

# Excursion levels the variants are built around. Kept few and round on purpose:
# a grid of forty exit thresholds fitted to one book is how a curve gets fitted.
TARGET_LEVELS = (0.5, 0.75, 1.0, 1.5)
BREAKEVEN_LEVELS = (0.5, 1.0)
PARTIAL_LEVELS = (0.5, 1.0)
TRAIL_GIVEBACKS = (0.5, 1.0)


def _floats(row: dict) -> tuple[float, float, float] | None:
    try:
        r = float(row["r"])
        mfe = float(row["mfe_r"])
        mae = float(row["mae_r"])
    except (KeyError, TypeError, ValueError):
        return None
    if r != r or mfe != mfe or mae != mae:
        return None
    return r, mfe, mae


def baseline(row: dict) -> tuple[float | None, str]:
    """What the trade actually made, unchanged."""
    vals = _floats(row)
    if vals is None:
        return None, "the row is missing r, mfe_r or mae_r"
    return vals[0], "the book as traded"


def target_at(row: dict, level: float) -> tuple[float | None, str]:
    """Take profit at ``level`` R instead of the plan's target.

    A trade that peaked at or above ``level`` would have been filled there. For
    a loser that is a strict improvement, because its peak preceded its stop.
    For a winner whose plan target was further out it is a strict reduction.
    """
    vals = _floats(row)
    if vals is None:
        return None, "the row is missing r, mfe_r or mae_r"
    r, mfe, _mae = vals
    if mfe >= level:
        return level, f"peaked at {mfe:+.2f}R, so {level}R was reached first"
    return r, f"never reached {level}R (peak {mfe:+.2f}R), so it ran to its own end"


def breakeven_after(row: dict, level: float) -> tuple[float | None, str]:
    """Move the stop to entry once the trade has been ``level`` R in profit.

    Deliberately conservative: a trade that reached the trigger and still ended
    a loss is credited 0R, not the peak. The real rule would sometimes be taken
    out at entry before running again, and this counts every one of those as a
    scratch rather than as a saved winner.
    """
    vals = _floats(row)
    if vals is None:
        return None, "the row is missing r, mfe_r or mae_r"
    r, mfe, _mae = vals
    if r >= 0:
        return r, "already a winner, unaffected by a breakeven stop"
    if mfe >= level:
        return 0.0, (f"was {mfe:+.2f}R up before losing {r:+.2f}R, so the "
                     "breakeven stop scratches it")
    return r, f"never reached {level}R (peak {mfe:+.2f}R), so the loss stands"


def partial_at(row: dict, level: float) -> tuple[float | None, str]:
    """Bank half the position at ``level`` R, let the rest run to its own end.

    The remaining half is credited the trade's actual outcome. That is the
    conservative reading: in practice the runner would usually be managed to
    breakeven, which this does not assume.
    """
    vals = _floats(row)
    if vals is None:
        return None, "the row is missing r, mfe_r or mae_r"
    r, mfe, _mae = vals
    if mfe >= level:
        return round(0.5 * level + 0.5 * r, 4), (
            f"half banked at {level}R, half ran to {r:+.2f}R")
    return r, f"never reached {level}R (peak {mfe:+.2f}R), so nothing was banked"


def trailing_stop(row: dict, giveback: float) -> tuple[float | None, str]:
    """Not answerable from this pool, and reported as such.

    A trailing stop's outcome depends on when the peak happened relative to
    every later dip. The pool records the peak and the trough but not their
    order, so any number here would be manufactured. The fields needed are the
    per-bar path from entry to exit.
    """
    return None, (f"{REQUIRES_PATH_DATA}: a {giveback}R trailing giveback "
                  "depends on the order of the peak and the dips inside the "
                  "trade, which the pool does not record")


def estimators() -> dict:
    """The excursion-only fallback for each variant, row -> (R, reason)."""
    out: dict = {"as_traded": baseline}
    for lvl in TARGET_LEVELS:
        out[f"target_at_{lvl}R"] = (lambda row, lvl=lvl: target_at(row, lvl))
    for lvl in BREAKEVEN_LEVELS:
        out[f"breakeven_after_{lvl}R"] = (
            lambda row, lvl=lvl: breakeven_after(row, lvl))
    for lvl in PARTIAL_LEVELS:
        out[f"partial_half_at_{lvl}R"] = (
            lambda row, lvl=lvl: partial_at(row, lvl))
    for g in TRAIL_GIVEBACKS:
        out[f"trail_giveback_{g}R"] = (
            lambda row, g=g: trailing_stop(row, g))
    return out


def variants(trades: list[dict] | None = None) -> dict:
    """Every exit rule under test, each a callable row -> (R, reason).

    A variant is priced from the recorded path when the whole book carries one,
    and from the excursion estimate otherwise. The choice is made per variant on
    the *whole* book rather than per row, so one book never mixes a simulated
    outcome with an estimated one inside a single expectancy.
    """
    est = estimators()
    if not trades:
        return est
    out: dict = {}
    for name, fallback in est.items():
        if name != "as_traded" and path_coverage(trades, name) >= 1.0:
            out[name] = (lambda row, name=name: path_priced(row, name))
        else:
            out[name] = fallback
    return out


def reprice(trades: list[dict], variant) -> dict:
    """Apply one exit rule to every candidate, returning re-priced rows.

    The re-priced row keeps its session and timestamp so the walk-forward can
    fold it chronologically, and its ``exit_reason`` is rewritten to match what
    the variant actually did, so target rates are not inherited from the old
    exit.
    """
    rows = []
    unpriceable = 0
    reason = ""
    for t in trades:
        r, why = variant(t)
        if r is None:
            unpriceable += 1
            reason = reason or why
            continue
        original = float(t["r"])
        rows.append({
            **t,
            "r": r,
            "exit_reason": "TARGET" if r > 0 else "STOP" if r < 0 else "SCRATCH",
            "exit_variant_reason": why,
            "r_as_traded": original,
        })
    return {"rows": rows, "unpriceable": unpriceable,
            "unpriceable_reason": reason}


def evaluate(trades: list[dict], *, folds: int = folds_mod.DEFAULT_FOLDS,
             min_fold_trades: int = folds_mod.MIN_FOLD_TRADES) -> dict:
    """Grade every exit variant chronologically against the book as traded.

    Each variant is walked forward over the same folds as the entry study, so a
    variant that only helped in one regime is visible as exactly that. A variant
    is only interesting if it beats the unchanged book's *median* fold, not its
    total: one enormous fold can carry a total while the rule loses money in
    most periods someone would have traded it.
    """
    base = reprice(trades, baseline)
    base_wf = folds_mod.walk_forward(
        base["rows"], lambda _t: True, folds=folds,
        min_fold_trades=min_fold_trades)
    base_med = base_wf.get("median_oos_expectancy_r")
    out = {}
    chosen = variants(trades)
    measured = [n for n in chosen
                if n != "as_traded" and path_coverage(trades, n) >= 1.0]
    for name, fn in chosen.items():
        priced = reprice(trades, fn)
        if not priced["rows"]:
            out[name] = {"verdict": folds_mod.THIN,
                         "priced": 0,
                         "unpriceable": priced["unpriceable"],
                         "basis": (PATH_MEASURED if name in measured
                                   else EXCURSION_ESTIMATE),
                         "note": priced["unpriceable_reason"]}
            continue
        wf = folds_mod.walk_forward(
            priced["rows"], lambda _t: True, folds=folds,
            min_fold_trades=min_fold_trades)
        med = wf.get("median_oos_expectancy_r")
        rs = [float(r["r"]) for r in priced["rows"]]
        gain = sum(r for r in rs if r > 0)
        loss = -sum(r for r in rs if r < 0)
        wins = sum(1 for r in rs if r > 0)
        out[name] = {
            "priced": len(rs),
            "basis": (PATH_MEASURED if name in measured
                      else EXCURSION_ESTIMATE),
            "unpriceable": priced["unpriceable"],
            "expectancy_r": round(sum(rs) / len(rs), 4),
            "profit_factor": round(gain / loss, 2) if loss > 0 else None,
            "win_pct": round(100.0 * wins / len(rs), 1),
            "median_oos_expectancy_r": med,
            "positive_folds": wf.get("positive_folds"),
            "measured_folds": wf.get("measured_folds"),
            "worst_fold_expectancy_r": (
                wf["worst_fold"]["expectancy_r"] if wf.get("worst_fold") else None),
            "max_drawdown_r": wf.get("worst_fold_drawdown_r"),
            "verdict": wf.get("verdict"),
            "beats_as_traded": bool(
                med is not None and base_med is not None and med > base_med),
            "note": wf.get("note"),
            "rows": wf.get("rows"),
        }
    all_measured = len(measured) == len(chosen) - 1
    return {
        "as_traded_median_r": base_med,
        "variants": out,
        "basis": "UNDERLYING_ONLY",
        "path_measured_variants": sorted(measured),
        "assumption": (
            "every variant simulated bar by bar on the recorded path, so a "
            "breakeven stop is charged for the winners it scratches out as well "
            "as credited with the losers it rescues, and a same-bar ambiguity is "
            "resolved against the variant" if all_measured else
            "re-priced from the recorded excursions, not from a re-replayed "
            "path: an earlier target is credited whenever the trade's peak "
            "reached it, which is exact for trades that ended at their stop and "
            "for trades that ended at their target, and unavailable for any rule "
            "that depends on the order of two interior points. A breakeven stop "
            "priced this way is biased UPWARD, because it is not charged for the "
            "winners it would have scratched out"),
        "cost_warning": (
            "gross. An earlier target cuts R per trade without cutting the cost "
            "per trade, so a variant that wins on gross R here can still lose "
            "net — that comparison needs the option cost model against a "
            "captured chain"),
    }
