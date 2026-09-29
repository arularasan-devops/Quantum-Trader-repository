"""§8 walk-forward validation. RESEARCH ONLY.

A single chronological split has one failure mode it cannot see: if the market
spent most of the history in one regime, both halves contain that regime, and a
rule that only works in it passes the holdout. Walk-forward asks the same
question of several consecutive slices of time, so a rule that worked in one
stretch and nowhere else is visible as exactly that.

The measure that matters here is not the mean across folds — one enormous fold
carries a mean past zero while the rule loses money in most periods someone
would have traded it. The verdict is driven by the *share of folds that were
positive* and by the *worst* fold, because the worst fold is the drawdown the
account actually has to survive.
"""
from __future__ import annotations

import math

# Folds the walk-forward uses by default. Five over five years is roughly a year
# each: long enough to contain more than one regime, short enough that a single
# quiet stretch does not dominate every fold.
DEFAULT_FOLDS = 5

# Candidates a fold needs before its expectancy is quoted. Below this the fold
# is reported as thin rather than counted as positive or negative, because a
# 12-trade fold decides "stability" on noise.
MIN_FOLD_TRADES = 30

# Share of folds that must be positive before a selector is called stable.
# Three in five is deliberately not a majority-of-one: at 5 folds, 3/5 is what a
# coin flip produces half the time, so the bar is set above it.
MIN_POSITIVE_SHARE = 0.7

STABLE = "STABLE_ACROSS_FOLDS"
ONE_REGIME = "WORKED_IN_ONE_REGIME_ONLY"
UNSTABLE = "UNSTABLE"
NEGATIVE = "NEGATIVE_ACROSS_FOLDS"
THIN = "REQUIRES_MORE_DATA"


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[mid], 3)
    return round((ordered[mid - 1] + ordered[mid]) / 2.0, 3)


def _max_drawdown(rs: list[float]) -> float:
    peak = equity = worst = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return round(worst, 2)


def _stats(trades: list[dict]) -> dict:
    """Expectancy, T1 rate and drawdown for one fold's kept candidates."""
    rs = [float(t["r"]) for t in trades]
    n = len(rs)
    if n == 0:
        return {"trades": 0, "expectancy_r": None, "t1_pct": None,
                "profit_factor": None, "net_r": 0.0, "max_drawdown_r": 0.0}
    mean = sum(rs) / n
    gain = sum(r for r in rs if r > 0)
    loss = -sum(r for r in rs if r < 0)
    t1 = sum(1 for t in trades if t.get("exit_reason") == "TARGET")
    return {
        "trades": n,
        "expectancy_r": round(mean, 3),
        "t1_pct": round(100.0 * t1 / n, 1),
        "profit_factor": round(gain / loss, 2) if loss > 0 else None,
        "net_r": round(sum(rs), 2),
        "max_drawdown_r": _max_drawdown(rs),
        "std_r": round(math.sqrt(sum((r - mean) ** 2 for r in rs) / n), 3),
    }


def split_folds(trades: list[dict], folds: int = DEFAULT_FOLDS) -> list[list[dict]]:
    """Cut the pool into consecutive equal-size slices, oldest first.

    Split by *session boundary*, not by row index: a fold that starts halfway
    through a trading day shares that day's candidates with the previous fold,
    and adjacent candidates from one session are the most correlated rows in the
    book. Slicing through them would leak the fold's own outcome into its
    neighbour and make every fold look more alike than it is.
    """
    if folds < 2:
        raise ValueError("walk-forward needs at least 2 folds")
    # entry_ts is an epoch int in replayed pools and an ISO string in live
    # journals; both sort correctly within a session as zero-padded text.
    dated = sorted(
        (t for t in trades if t.get("session")),
        key=lambda t: (str(t["session"]), str(t.get("entry_ts") or "").zfill(20)),
    )
    sessions = sorted({str(t["session"]) for t in dated})
    if len(sessions) < folds:
        return []
    per = len(sessions) / folds
    bounds = [sessions[min(len(sessions) - 1, int(round(i * per)))]
              for i in range(folds)]
    out: list[list[dict]] = [[] for _ in range(folds)]
    for t in dated:
        s = str(t["session"])
        idx = 0
        for i, start in enumerate(bounds):
            if s >= start:
                idx = i
        out[idx].append(t)
    return out


def walk_forward(trades: list[dict], keep, *, folds: int = DEFAULT_FOLDS,
                 min_fold_trades: int = MIN_FOLD_TRADES) -> dict:
    """Grade one selector across consecutive chronological folds.

    ``keep`` is the selector under test: a callable returning True for the
    candidates it would trade. Passing ``lambda _t: True`` grades the whole
    pool, which is the baseline every selector has to beat.
    """
    sliced = split_folds(trades, folds)
    if not sliced:
        return {"folds": 0, "verdict": THIN,
                "note": (f"the pool spans fewer than {folds} sessions, so it "
                         "cannot be cut into folds")}
    rows = []
    for i, fold in enumerate(sliced, start=1):
        kept = [t for t in fold if keep(t)]
        st = _stats(kept)
        sessions = sorted({str(t["session"]) for t in fold})
        rows.append({
            "fold": i,
            "from": sessions[0] if sessions else None,
            "to": sessions[-1] if sessions else None,
            "candidates_in_fold": len(fold),
            "selected": len(kept),
            "selection_pct": (round(100.0 * len(kept) / len(fold), 1)
                              if fold else 0.0),
            "trades_per_day": (round(len(kept) / len(sessions), 2)
                               if sessions else 0.0),
            # A fold thinner than the floor is not evidence in either direction,
            # and is excluded from the positive/negative count rather than
            # counted as a loss.
            "measured": bool(len(kept) >= min_fold_trades),
            **st,
        })
    measured = [r for r in rows if r["measured"]]
    exps = [float(r["expectancy_r"]) for r in measured
            if r["expectancy_r"] is not None]
    positive = sum(1 for e in exps if e > 0)
    worst = min(measured, key=lambda r: r["expectancy_r"]) if measured else None
    best = max(measured, key=lambda r: r["expectancy_r"]) if measured else None
    share = (positive / len(exps)) if exps else 0.0
    return {
        "folds": len(rows),
        "measured_folds": len(measured),
        "min_fold_trades": min_fold_trades,
        "rows": rows,
        "positive_folds": positive,
        "negative_folds": len(exps) - positive,
        "positive_share": round(share, 2),
        "median_oos_expectancy_r": _median(exps),
        "mean_oos_expectancy_r": (round(sum(exps) / len(exps), 3)
                                  if exps else None),
        "worst_fold": worst,
        "best_fold": best,
        # Summed across folds this is the drawdown of trading the selector
        # continuously, which is the number that decides whether it is
        # survivable rather than merely positive.
        "worst_fold_drawdown_r": (min(r["max_drawdown_r"] for r in measured)
                                  if measured else None),
        "verdict": _verdict(exps, share),
        "note": _note(exps, share, worst, best),
    }


def _verdict(exps: list[float], share: float) -> str:
    if len(exps) < 3:
        return THIN
    single_fold_carries = (
        sum(1 for e in exps if e > 0) == 1
        and sum(exps) > 0
        and max(exps) >= sum(exps)
    )
    if single_fold_carries:
        # One fold carrying the whole result is the regime-dependence signature,
        # and it needs its own verdict because the total stays positive.
        return ONE_REGIME
    if share >= MIN_POSITIVE_SHARE:
        return STABLE
    if not any(e > 0 for e in exps):
        return NEGATIVE
    return UNSTABLE


def _note(exps: list[float], share: float,
          worst: dict | None, best: dict | None) -> str:
    if len(exps) < 3:
        return (f"only {len(exps)} of the folds held enough selected candidates "
                "to be measured; walk-forward cannot rule either way on this "
                "sample")
    parts = [f"{sum(1 for e in exps if e > 0)}/{len(exps)} measured folds "
             f"positive (median {_median(exps):+}R)"]
    if worst is not None:
        parts.append(f"worst fold {worst['from']}..{worst['to']} at "
                     f"{worst['expectancy_r']:+}R over {worst['selected']} "
                     f"candidates, drawdown {worst['max_drawdown_r']}R")
    if best is not None and best is not worst:
        parts.append(f"best fold {best['from']}..{best['to']} at "
                     f"{best['expectancy_r']:+}R")
    if share < MIN_POSITIVE_SHARE:
        parts.append("a selector that loses in this many periods is not "
                     "tradable even when its total is positive, because the "
                     "losing stretches are what the account has to sit through")
    return "; ".join(parts)


def compare(trades: list[dict], selectors: dict, *,
            folds: int = DEFAULT_FOLDS,
            min_fold_trades: int = MIN_FOLD_TRADES) -> dict:
    """Walk-forward every named selector, plus the whole-pool baseline.

    The baseline is always included and always reported, because "stable across
    folds" is meaningless unless it also beats taking every candidate.
    """
    base = walk_forward(trades, lambda _t: True, folds=folds,
                        min_fold_trades=min_fold_trades)
    out = {}
    for name, keep in selectors.items():
        row = walk_forward(trades, keep, folds=folds,
                           min_fold_trades=min_fold_trades)
        base_med = base.get("median_oos_expectancy_r")
        med = row.get("median_oos_expectancy_r")
        row["beats_baseline"] = bool(
            med is not None and base_med is not None and med > base_med)
        row["baseline_median_r"] = base_med
        out[name] = row
    return {"baseline": base, "selectors": out}
