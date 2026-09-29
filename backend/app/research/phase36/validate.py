"""Phase 36 §18-§21 — placebo, chronological validation, stress, outliers.

These four are what separate a vehicle *finding* from a vehicle *coincidence*,
and each answers a specific way the headline table can be wrong:

* **§18 placebo.** If random entries in the same instrument, over the same
  horizons, with the same costs, show the same vehicle ranking, then the ranking
  is a property of how CRUDEOIL moves and not of anything the system detected.
  That would still be useful — it would say "express any view in the future" —
  but it would not be evidence about the signal, and the two must not be
  confused.
* **§19 chronological validation.** The vehicle rule is chosen on development
  sessions only, checked once on validation, and evaluated once on an untouched
  holdout. Choosing the best vehicle on the whole dataset and then reporting its
  edge is the single most common way a study like this lies.
* **§20 stress.** Costs 1x/1.5x/2x and slippage 1x/2x/3x. A vehicle whose
  advantage disappears at 1.5x cost is a vehicle whose advantage is a cost
  assumption.
* **§21 outliers.** Full, top 1% removed, top 5% removed. One option that
  tripled can carry an entire sample, and a rule built on it will not survive the
  month it does not happen.
"""
from __future__ import annotations

import datetime as dt
import random

from app.research.phase35 import CE, FUTURES, LONG, PE, SHORT
from app.research.phase36 import (
    COST_MULTIPLES,
    DEV,
    DEV_FRACTION,
    DIRECTIONAL,
    HOLDOUT,
    MIN_HOLDOUT_TRIPLES,
    MIN_TRIPLES_PER_VEHICLE,
    OUTLIER_TRIMS_PCT,
    PLACEBO,
    PLACEBO_DRAWS_PER_SESSION,
    PLACEBO_SEED,
    SLIPPAGE_MULTIPLES,
    VALIDATION,
    VALIDATION_FRACTION,
)
from app.research.phase36 import outcome as p36outcome
from app.research.phase36 import tables as p36tables
from app.research.phase36 import triples as p36triples

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def session_of(ts: float) -> str:
    return dt.datetime.fromtimestamp(float(ts), _IST).strftime("%Y-%m-%d")


def _nets(
    rows: list[dict], vehicle: str, horizon: str, *,
    cost_multiple: float = 1.0, slippage_multiple: float = 1.0,
) -> list[float]:
    """Net figures for one vehicle, re-charged for the stress grid.

    Slippage is expressed in units of the leg's own measured spread: on an
    executable fill the spread is already paid once, so ``1x`` adds nothing and
    ``2x`` adds one further spread per round trip. Charging a fixed rupee
    slippage instead would penalise a ₹20 option and a ₹8,700 future by wildly
    different fractions and call it the same stress.
    """
    out: list[float] = []
    for r in rows:
        if r.get("vehicle") != vehicle or r.get("role") != DIRECTIONAL:
            continue
        snap = (r.get("horizons") or {}).get(horizon)
        entry = r.get("entry_price")
        if not isinstance(snap, dict) or not isinstance(entry, (int, float)):
            continue
        spread = r.get("spread")
        extra = (
            float(spread) * (float(slippage_multiple) - 1.0)
            if isinstance(spread, (int, float)) else 0.0
        )
        net = p36outcome.net_from_gross(
            snap.get("gross_pct"),
            cost_points=(r.get("cost") or {}).get("cost_points"),
            entry=float(entry), cost_multiple=cost_multiple, extra_points=extra,
        )
        if net is not None:
            out.append(net)
    return out


def _summary(xs: list[float]) -> dict:
    return {
        "n": len(xs),
        "net_mean_pct": round(sum(xs) / len(xs), 4) if xs else None,
        "win_pct": (
            round(100.0 * sum(1 for x in xs if x > 0) / len(xs), 2) if xs else None
        ),
        "sufficient": len(xs) >= MIN_TRIPLES_PER_VEHICLE,
    }


# ---------------------------------------------------------------------------
# §18 placebo
# ---------------------------------------------------------------------------
def placebo(
    con, triples: list[dict], *, horizon: str, seed: int = PLACEBO_SEED,
) -> dict:
    """Random-entry control over the same instrument, horizons and costs.

    The draw is uniform over *captured executable instants*, not over wall-clock
    time, and the direction is drawn at random because a placebo has no view.
    Both limitations are stated rather than smoothed over: the capture samples
    some parts of the session more densely than others, so this control shares
    that bias with the signal it is controlling for — which is the point.
    """
    if not triples:
        return {"control": PLACEBO, "n": 0,
                "reason": "no eligible triples to draw from"}
    rng = random.Random(seed)  # noqa: S311 - reproducible control, not crypto
    by_session: dict[str, list[dict]] = {}
    for t in triples:
        by_session.setdefault(session_of(t["ts"]), []).append(t)

    drawn: list[dict] = []
    for _day, pool in sorted(by_session.items()):
        draws = min(PLACEBO_DRAWS_PER_SESSION, len(pool))
        for t in rng.sample(pool, draws):
            direction = rng.choice((LONG, SHORT))
            fake = dict(t)
            fake["direction"] = direction
            fake["directional_option"] = p36triples.directional_option(direction)
            fake["engine_selected"] = False
            drawn.append(fake)

    rows = p36outcome.resolve_all(con, drawn)
    table = p36tables.compare(rows)
    return {
        "control": PLACEBO,
        "seed": seed,
        "n_draws": len(drawn),
        "horizon": horizon,
        "by_vehicle": {
            v: _summary(_nets(rows, v, horizon)) for v in (FUTURES, CE, PE)
        },
        "ranking": table["ranking"],
        "note": (
            "random instants and random direction over the same contracts; if "
            "the vehicle ranking matches the signal's, the ranking describes the "
            "instrument, not the signal"
        ),
    }


# ---------------------------------------------------------------------------
# §19 chronological validation
# ---------------------------------------------------------------------------
def split_sessions(rows: list[dict]) -> dict[str, list[str]]:
    """Chronological session split — by date, never by row index."""
    days = sorted({
        session_of(r["ts"]) for r in rows
        if isinstance(r.get("ts"), (int, float))
    })
    n = len(days)
    if n == 0:
        return {DEV: [], VALIDATION: [], HOLDOUT: []}
    dev_end = int(n * DEV_FRACTION)
    val_end = dev_end + int(n * VALIDATION_FRACTION)
    return {
        DEV: days[:dev_end] or days[:1],
        VALIDATION: days[dev_end:val_end],
        HOLDOUT: days[val_end:],
    }


def chronological(rows: list[dict], *, horizon: str) -> dict:
    """Pick the vehicle on development sessions, then check it forward once.

    The rule this validates is intentionally the simplest one that answers the
    task's final question: *always express the view through vehicle X, held for
    horizon H.* A richer rule would need its own hypothesis count, and with a
    handful of sessions the honest answer is not a richer rule.
    """
    splits = split_sessions(rows)
    by_split = {
        name: [
            r for r in rows
            if isinstance(r.get("ts"), (int, float))
            and session_of(r["ts"]) in set(days)
        ]
        for name, days in splits.items()
    }
    dev_rows = by_split[DEV]
    dev_scores = {
        v: _summary(_nets(dev_rows, v, horizon)) for v in (FUTURES, CE, PE)
    }
    eligible = {
        v: s["net_mean_pct"] for v, s in dev_scores.items()
        if s["net_mean_pct"] is not None and s["sufficient"]
    }
    chosen = max(eligible, key=lambda v: eligible[v]) if eligible else None

    out = {
        "horizon": horizon,
        "sessions": {k: len(v) for k, v in splits.items()},
        "development": dev_scores,
        "chosen_vehicle": chosen,
        "chosen_on": DEV,
    }
    if chosen is None:
        out["status"] = "NO_VEHICLE_MET_THE_DEVELOPMENT_FLOOR"
        return out
    out[VALIDATION] = _summary(_nets(by_split[VALIDATION], chosen, horizon))
    holdout = _summary(_nets(by_split[HOLDOUT], chosen, horizon))
    holdout["evaluated_once"] = True
    holdout["sufficient"] = holdout["n"] >= MIN_HOLDOUT_TRIPLES
    out[HOLDOUT] = holdout
    out["status"] = (
        "HOLDOUT_CONFIRMS" if (
            holdout["sufficient"] and (holdout["net_mean_pct"] or 0) > 0
        ) else (
            "HOLDOUT_TOO_SMALL" if not holdout["sufficient"]
            else "HOLDOUT_DOES_NOT_CONFIRM"
        )
    )
    return out


def walk_forward(rows: list[dict], *, horizon: str) -> dict:
    """Train on sessions 1..k, test on k+1, for every k. Never overlapping.

    A single fold that confirms is a coincidence; the useful output is how often
    the vehicle chosen yesterday was still the best vehicle today, which is the
    question a live selection rule would actually face every morning.
    """
    days = sorted({
        session_of(r["ts"]) for r in rows
        if isinstance(r.get("ts"), (int, float))
    })
    folds: list[dict] = []
    for k in range(1, len(days)):
        train_days = set(days[:k])
        test_day = days[k]
        train = [r for r in rows if session_of(r["ts"]) in train_days]
        test = [r for r in rows if session_of(r["ts"]) == test_day]
        scores = {
            v: _summary(_nets(train, v, horizon)) for v in (FUTURES, CE, PE)
        }
        eligible = {
            v: s["net_mean_pct"] for v, s in scores.items()
            if s["net_mean_pct"] is not None and s["sufficient"]
        }
        if not eligible:
            folds.append({"test_session": test_day, "chosen": None,
                          "reason": "training fold below floor"})
            continue
        chosen = max(eligible, key=lambda v: eligible[v])
        realised = _summary(_nets(test, chosen, horizon))
        actual_best = {
            v: _summary(_nets(test, v, horizon))["net_mean_pct"]
            for v in (FUTURES, CE, PE)
        }
        best_on_test = max(
            (v for v, s in actual_best.items() if s is not None),
            key=lambda v: actual_best[v], default=None,
        )
        folds.append({
            "test_session": test_day,
            "chosen": chosen,
            "realised": realised,
            "best_on_test": best_on_test,
            "choice_was_best": chosen == best_on_test,
        })
    decided = [f for f in folds if f.get("chosen")]
    return {
        "horizon": horizon,
        "folds": folds,
        "n_folds": len(folds),
        "n_decided": len(decided),
        "hit_rate_pct": (
            round(100.0 * sum(1 for f in decided if f.get("choice_was_best"))
                  / len(decided), 2) if decided else None
        ),
        "positive_folds_pct": (
            round(100.0 * sum(
                1 for f in decided
                if (f.get("realised") or {}).get("net_mean_pct") is not None
                and (f["realised"]["net_mean_pct"] or 0) > 0
            ) / len(decided), 2) if decided else None
        ),
    }


# ---------------------------------------------------------------------------
# §20 stress, §21 outliers
# ---------------------------------------------------------------------------
def stress(rows: list[dict], *, horizon: str) -> dict:
    """Cost 1x/1.5x/2x against slippage 1x/2x/3x, per vehicle."""
    grid: dict[str, dict] = {}
    for v in (FUTURES, CE, PE):
        cells: dict[str, dict] = {}
        for cm in COST_MULTIPLES:
            for sm in SLIPPAGE_MULTIPLES:
                cells[f"cost_{cm}x_slip_{sm}x"] = _summary(_nets(
                    rows, v, horizon, cost_multiple=cm, slippage_multiple=sm,
                ))
        survives = all(
            (s["net_mean_pct"] or 0) > 0
            for s in cells.values() if s["net_mean_pct"] is not None
        ) and any(s["net_mean_pct"] is not None for s in cells.values())
        grid[v] = {
            "cells": cells,
            "positive_everywhere": survives,
        }
    return {
        "horizon": horizon,
        "by_vehicle": grid,
        "slippage_unit": (
            "one further measured spread per round trip per multiple above 1x"
        ),
    }


def outliers(rows: list[dict], *, horizon: str) -> dict:
    """Full sample, top 1% removed, top 5% removed — §21."""
    out: dict[str, dict] = {}
    for v in (FUTURES, CE, PE):
        nets = sorted(_nets(rows, v, horizon))
        cells: dict[str, dict] = {}
        for trim in OUTLIER_TRIMS_PCT:
            keep = nets[:len(nets) - int(len(nets) * trim / 100.0)] if trim else nets
            cells[f"trim_top_{trim:g}pct"] = _summary(keep)
        base = cells["trim_top_0pct"]["net_mean_pct"]
        trimmed = cells["trim_top_5pct"]["net_mean_pct"]
        cells["carried_by_outliers"] = bool(
            base is not None and trimmed is not None and base > 0 >= trimmed
        )
        out[v] = cells
    return {"horizon": horizon, "by_vehicle": out}
