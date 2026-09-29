"""PULLBACK vs NON_PULLBACK over the candidate pool, chronologically.

    DEVELOPMENT   the oldest half of the sessions. Everything may be looked at
                  here, because nothing may be changed by what is seen: the
                  definition is frozen before this module runs
          |
    VALIDATION    the next fifth. The same numbers, re-measured. A setup that
                  does not repeat here is not carried forward
          |
    HOLDOUT       the newest three tenths, measured once, plus a walk-forward
                  across the whole pool so a single favourable regime cannot
                  carry the verdict

Two honesty rules are enforced by the code rather than by the reader:

* **Gross and net are never blurred.** The pool is an underlying-only book, so
  it has no option book and cannot produce a net number by itself. Net figures
  here are labelled with the cost they were charged and exist only to show the
  cost the setup has to clear; the study's final profitability number comes from
  the vehicle layer, priced on real bid/ask. Nothing on this page is allowed to
  be quoted as "the net result".
* **A thin cohort is not a result.** Any period with fewer than
  :data:`MIN_SAMPLE` selected candidates reports its numbers with
  ``measurable`` false, and the verdict layer treats it as REQUIRES_MORE_DATA
  rather than as a pass or a fail.
"""
from __future__ import annotations

from statistics import mean, median

from app.research.phase14 import paths as paths_mod
from app.research.phase15 import attribution, folds as folds_mod
from app.research.phase22 import definition as defn, pool as pool_mod

# Rows a cohort needs before its expectancy is quoted as evidence. The pool's
# own earlier studies used 100; this is the same bar, so the two are comparable.
MIN_SAMPLE = 100

# Chronological shares. Sessions, never rows: cutting inside a session leaks a
# day's outcome across the boundary.
DEV_SHARE = 0.5
VALIDATION_SHARE = 0.2

TARGET = "TARGET"
STOP = "STOP"

# Cost bands charged against the gross R, in units of the plan's own risk. These
# are ASSUMPTIONS, not measurements, and are labelled as such everywhere they
# appear. 0.72R is what the 1 Sep A+ cohort's round-trip cost actually was
# against its own risk, which is why the band goes that high.
COST_BANDS_R = (0.0, 0.1, 0.25, 0.5, 0.72)

MEASURED = "MEASURED"
THIN = "REQUIRES_MORE_DATA"


def _r(row: dict) -> float | None:
    value = row.get("r")
    return float(value) if isinstance(value, (int, float)) else None


def _num(row: dict, key: str) -> float | None:
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _drawdown(rs: list[float]) -> float:
    peak = equity = worst = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return round(worst, 2)


def summarise(rows: list[dict], *, cost_r: float = 0.0) -> dict:
    """Every primary metric for one cohort, at one cost assumption.

    ``t1_before_sl_pct`` counts the candidates whose target was reached before
    their stop — read off the replay's own exit reason, not inferred from the
    excursions, because a trade can be up 1R and still end at its stop.
    """
    rs = [r for r in (_r(x) for x in rows) if r is not None]
    n = len(rs)
    sess = pool_mod.sessions(rows)
    base = {
        "trades": n,
        "sessions": len(sess),
        "first_session": sess[0] if sess else None,
        "last_session": sess[-1] if sess else None,
        "measurable": n >= MIN_SAMPLE,
        "cost_r_charged": cost_r,
        "cost_basis": "ASSUMED_COST_BAND" if cost_r else "GROSS",
    }
    if not n:
        return {**base, "expectancy_r": None, "t1_before_sl_pct": None,
                "profit_factor": None, "net_r": 0.0, "max_drawdown_r": 0.0,
                "avg_win_r": None, "avg_loss_r": None}
    net = [r - cost_r for r in rs]
    wins = [r for r in net if r > 0]
    losses = [r for r in net if r < 0]
    gain = sum(wins)
    loss = -sum(losses)
    t1 = sum(1 for x in rows if str(x.get("exit_reason")).upper() == TARGET)
    sl = sum(1 for x in rows if str(x.get("exit_reason")).upper() == STOP)
    mfe = [v for v in (_num(x, "mfe_r") for x in rows) if v is not None]
    mae = [v for v in (_num(x, "mae_r") for x in rows) if v is not None]
    t1_min = [_num(x, "minutes") for x in rows
              if str(x.get("exit_reason")).upper() == TARGET]
    sl_min = [_num(x, "minutes") for x in rows
              if str(x.get("exit_reason")).upper() == STOP]
    t1_min = [v for v in t1_min if v is not None]
    sl_min = [v for v in sl_min if v is not None]
    return {
        **base,
        "expectancy_r": round(sum(net) / n, 3),
        "gross_expectancy_r": round(sum(rs) / n, 3),
        "t1_before_sl_pct": round(100.0 * t1 / n, 1),
        "sl_pct": round(100.0 * sl / n, 1),
        "profit_factor": round(gain / loss, 2) if loss > 0 else None,
        "net_r": round(sum(net), 2),
        "max_drawdown_r": _drawdown(net),
        "avg_win_r": round(mean(wins), 3) if wins else None,
        "avg_loss_r": round(mean(losses), 3) if losses else None,
        "win_pct": round(100.0 * len(wins) / n, 1),
        "median_mfe_r": round(median(mfe), 3) if mfe else None,
        "median_mae_r": round(median(mae), 3) if mae else None,
        "mean_mfe_r": round(mean(mfe), 3) if mfe else None,
        "mean_mae_r": round(mean(mae), 3) if mae else None,
        "median_minutes_to_t1": round(median(t1_min), 1) if t1_min else None,
        "median_minutes_to_sl": round(median(sl_min), 1) if sl_min else None,
        "trades_per_day": round(n / len(sess), 2) if sess else None,
    }


def cost_curve(rows: list[dict]) -> list[dict]:
    """The cohort's expectancy at each assumed cost band.

    The band where expectancy crosses zero is the number that matters: it is the
    round-trip cost this setup can afford, and the vehicle layer then says
    whether any real option or future is that cheap.
    """
    return [{"cost_r": band, **{k: v for k, v in summarise(rows, cost_r=band).items()
                                if k in ("expectancy_r", "profit_factor",
                                         "net_r", "max_drawdown_r")}}
            for band in COST_BANDS_R]


def affordable_cost_r(rows: list[dict]) -> float | None:
    """The largest assumed cost band at which the cohort is still positive."""
    best: float | None = None
    for row in cost_curve(rows):
        exp = row.get("expectancy_r")
        if isinstance(exp, (int, float)) and exp > 0:
            best = float(row["cost_r"])
    return best


def periods(rows: list[dict]) -> dict:
    """Cut the pool into development / validation / holdout by session."""
    sess = pool_mod.sessions(rows)
    if not sess:
        return {"development": [], "validation": [], "holdout": [],
                "spans": {}, "sessions": 0}
    n = len(sess)
    dev_end = max(1, int(round(n * DEV_SHARE)))
    val_end = max(dev_end + 1, int(round(n * (DEV_SHARE + VALIDATION_SHARE))))
    dev_set = set(sess[:dev_end])
    val_set = set(sess[dev_end:val_end])
    out = {"development": [], "validation": [], "holdout": []}
    for row in rows:
        s = str(row.get("session"))
        if s in dev_set:
            out["development"].append(row)
        elif s in val_set:
            out["validation"].append(row)
        else:
            out["holdout"].append(row)
    out["spans"] = {
        name: {"sessions": len(pool_mod.sessions(part)),
               "first": (pool_mod.sessions(part) or [None])[0],
               "last": (pool_mod.sessions(part) or [None])[-1]}
        for name, part in out.items() if isinstance(part, list)
    }
    out["sessions"] = n
    return out


def _lift(setup: dict, other: dict) -> dict:
    """PULLBACK minus NON_PULLBACK, with both sides' samples attached."""
    a, b = setup.get("expectancy_r"), other.get("expectancy_r")
    t1a, t1b = setup.get("t1_before_sl_pct"), other.get("t1_before_sl_pct")
    return {
        "expectancy_lift_r": (round(a - b, 3)
                              if isinstance(a, (int, float))
                              and isinstance(b, (int, float)) else None),
        "t1_lift_pct": (round(t1a - t1b, 1)
                        if isinstance(t1a, (int, float))
                        and isinstance(t1b, (int, float)) else None),
        "setup_trades": setup.get("trades"),
        "other_trades": other.get("trades"),
        "both_measurable": bool(setup.get("measurable")
                                and other.get("measurable")),
    }


def compare_period(rows: list[dict], *, cost_r: float = 0.0) -> dict:
    parts = pool_mod.split(rows)
    setup = summarise(parts[defn.LABEL], cost_r=cost_r)
    other = summarise(parts[defn.OTHER], cost_r=cost_r)
    return {defn.LABEL: setup, defn.OTHER: other, "lift": _lift(setup, other),
            "selection_pct": (round(100.0 * setup["trades"] / len(rows), 2)
                              if rows else None)}


def stop_bands(rows: list[dict]) -> dict:
    """The frozen setup regraded at each research stop distance.

    Path-measured only. A pool built before the bands existed, or a row whose
    path was truncated by the plan's own stop before a wider band could
    resolve, is reported as unresolved rather than estimated: crediting a wider
    stop with a recovery that was never walked is exactly the error that made an
    earlier grid read a widened stop as an improvement.
    """
    setup = pool_mod.split(rows)[defn.LABEL]
    have = [r for r in setup if isinstance(r.get("stop_bands"), dict)]
    out = {
        "candidates": len(setup),
        "with_bands": len(have),
        "coverage_pct": (round(100.0 * len(have) / len(setup), 1)
                         if setup else None),
        "basis": paths_mod.PATH_MEASURED if have else paths_mod.PATH_TRUNCATED,
        "bands": [],
    }
    if not have:
        out["note"] = ("no candidate carries stop_bands: rebuild the pool with "
                       "the current replay to grade stop distance")
        return out
    for name in paths_mod.stop_band_names():
        graded = [(r, r["stop_bands"].get(name)) for r in have]
        values = [(r, float(v)) for r, v in graded
                  if isinstance(v, (int, float))]
        unresolved = sum(1 for _r, v in graded if v is None)
        if not values:
            out["bands"].append({"band": name, "resolved": 0,
                                 "unresolved": unresolved,
                                 "expectancy_r": None})
            continue
        rs = [v for _r, v in values]
        wins = [v for v in rs if v > 0]
        losses = [v for v in rs if v < 0]
        loss = -sum(losses)
        out["bands"].append({
            "band": name,
            "resolved": len(rs),
            "unresolved": unresolved,
            "resolved_pct": round(100.0 * len(rs) / len(graded), 1),
            "expectancy_r": round(sum(rs) / len(rs), 3),
            "t1_before_sl_pct": round(100.0 * len(wins) / len(rs), 1),
            "profit_factor": round(sum(wins) / loss, 2) if loss > 0 else None,
            "avg_win_r": round(mean(wins), 3) if wins else None,
            "avg_loss_r": round(mean(losses), 3) if losses else None,
            "max_drawdown_r": _drawdown(rs),
            "median_bars_to_exit": _median_bars(have, name),
        })
    return out


def _median_bars(rows: list[dict], band: str) -> float | None:
    bars = []
    for row in rows:
        table = row.get("stop_band_bars")
        if isinstance(table, dict):
            value = table.get(band)
            if isinstance(value, (int, float)):
                bars.append(float(value))
    return round(median(bars), 1) if bars else None


def missed_winners(rows: list[dict]) -> dict:
    """What the definition threw away, and what it avoided.

    Reuses the Phase 15 attribution so the two studies count a missed winner
    the same way. Reported as counts and R, never as a regret score.
    """
    graded = [r for r in rows if isinstance(r.get("r"), (int, float))]
    result = attribution.attribute(graded, pool_mod.selector)
    return result


def walk_forward(rows: list[dict], *, folds: int = folds_mod.DEFAULT_FOLDS) -> dict:
    """The frozen setup graded across consecutive chronological folds."""
    return folds_mod.walk_forward(rows, pool_mod.selector, folds=folds)


def by_family(rows: list[dict], *, cost_r: float = 0.0) -> list[dict]:
    """Per-family comparison. Never pooled across families."""
    out = []
    for name, part in sorted(pool_mod.families(rows).items()):
        cmp_ = compare_period(part, cost_r=cost_r)
        out.append({"family": name, "candidates": len(part), **cmp_})
    return out


def by_instrument(rows: list[dict], *, cost_r: float = 0.0) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("instrument")), []).append(row)
    out = []
    for name, part in sorted(grouped.items()):
        setup = pool_mod.split(part)[defn.LABEL]
        out.append({
            "instrument": name,
            "family": pool_mod.family(name),
            "candidates": len(part),
            **summarise(setup, cost_r=cost_r),
        })
    return sorted(out, key=lambda r: -(r.get("trades") or 0))


def engine_counterfactual(rows: list[dict]) -> dict:
    """Does the definition select inside what the engine already refused?

    If the setup's expectancy on candidates the engine refused is the same as on
    the ones it took, the gates are not selecting and the setup is doing the
    work — or neither is.
    """
    out = {}
    for view in (pool_mod.ENGINE_BUY, pool_mod.ENGINE_REFUSED):
        part = [r for r in rows if r.get("p22_engine") == view]
        out[view] = compare_period(part)
    return out


def run(rows: list[dict], *, folds: int = folds_mod.DEFAULT_FOLDS) -> dict:
    """The whole underlying-leg study. No option price is touched here."""
    labelled = pool_mod.label_rows(rows)
    parts = periods(labelled)
    study = {
        "definition": defn.spec(),
        "definition_fingerprint": defn.fingerprint(),
        "pool": {
            "candidates": len(labelled),
            "sessions": parts["sessions"],
            "spans": parts["spans"],
            "engine_buy": sum(1 for r in labelled
                              if r.get("p22_engine") == pool_mod.ENGINE_BUY),
            "engine_refused": sum(1 for r in labelled
                                  if r.get("p22_engine")
                                  == pool_mod.ENGINE_REFUSED),
            "families": {k: len(v) for k, v in
                         sorted(pool_mod.families(labelled).items())},
        },
        "refusal_census": pool_mod.refusal_census(labelled),
        "periods": {
            name: compare_period(parts[name])
            for name in ("development", "validation", "holdout")
        },
        "cost_curve": {
            "note": ("assumed round-trip cost in units of the plan's own risk; "
                     "these are not measured prices"),
            "development": cost_curve(pool_mod.split(parts["development"])[defn.LABEL]),
            "holdout": cost_curve(pool_mod.split(parts["holdout"])[defn.LABEL]),
        },
        "affordable_cost_r": {
            "development": affordable_cost_r(
                pool_mod.split(parts["development"])[defn.LABEL]),
            "holdout": affordable_cost_r(
                pool_mod.split(parts["holdout"])[defn.LABEL]),
        },
        "walk_forward": walk_forward(labelled, folds=folds),
        "stop_bands": stop_bands(labelled),
        "attribution": missed_winners(labelled),
        "by_family": by_family(labelled),
        "by_instrument": by_instrument(labelled),
        "engine_counterfactual": engine_counterfactual(labelled),
    }
    return study
