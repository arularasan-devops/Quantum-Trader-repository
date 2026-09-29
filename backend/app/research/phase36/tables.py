"""Phase 36 §8-§13/§16/§17 — the comparison tables and every cut of them.

One aggregation function does all of it. That is deliberate: the headline vehicle
table, the direction split, the premium bands, the DTE bands, the moneyness
table and the time-of-day table are the same statistics over different subsets,
and computing them six ways would eventually make two of them disagree.

Three properties every table here has, because §8 and §22 demand them:

* the sample size is never hidden, and a row below
  :data:`app.research.phase36.MIN_TRIPLES_PER_VEHICLE` is marked
  ``REQUIRES_MORE_DATA`` rather than ranked;
* the "best hold" is chosen for the *whole subset*, from mean net across
  horizons, never per trade. Picking each trade's best horizon is hindsight and
  produces a number nobody can trade;
* a mean is always reported beside a median and a confidence interval, because a
  vehicle whose mean is carried by one abnormal option move is exactly what §21
  exists to catch.
"""
from __future__ import annotations

import datetime as dt
import math

from app.research.phase35 import CE, FUTURES, LONG, PE, SHORT
from app.research.phase36 import (
    CLOSE,
    COMPARISON_HORIZONS,
    COST_MOVE_BANDS,
    DIRECTIONAL,
    DTE_BANDS,
    MIN_TRIPLES_PER_VEHICLE,
    OPENING_MINUTES,
    PREMIUM_BANDS,
    TOD_AFTERNOON,
    TOD_LATE,
    TOD_MIDDAY,
    TOD_MORNING,
    TOD_OPENING,
)
from app.research.phase36 import (
    outcome as p36outcome,
)

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
HORIZON_KEYS: tuple[str, ...] = tuple(
    [str(h) for h in COMPARISON_HORIZONS] + [CLOSE]
)


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    mid = len(s) // 2
    return round(
        s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0, 4,
    )


def _ci95(xs: list[float]) -> tuple[float, float] | None:
    """Normal-approximation 95% CI on the mean. ``None`` below 5 observations.

    Not a bootstrap: the point of printing an interval here is to stop a
    six-trade row being read as a result, and for that a crude interval is
    honest as long as it is not offered on samples too small to have one.
    """
    n = len(xs)
    if n < 5:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    half = 1.96 * math.sqrt(var / n)
    return round(m - half, 4), round(m + half, 4)


def _profit_factor(xs: list[float]) -> float | None:
    gains = sum(x for x in xs if x > 0)
    losses = -sum(x for x in xs if x < 0)
    if losses <= 0:
        return None if gains <= 0 else float("inf")
    return round(gains / losses, 4)


def time_of_day(ts: float, *, session_open: float, session_close: float) -> str:
    """Bucket by position within the instrument's own captured session.

    Fractions of the measured session rather than a fixed clock, because MCX
    trades for fourteen hours and NFO for six: "afternoon" as an absolute time
    would put the MCX evening in the same bucket as the NFO close.
    """
    if (session_close - session_open) <= 0:
        return TOD_OPENING
    minutes = (ts - session_open) / 60.0
    if minutes <= OPENING_MINUTES:
        return TOD_OPENING
    frac = (ts - session_open) / (session_close - session_open)
    if frac < 0.35:
        return TOD_MORNING
    if frac < 0.6:
        return TOD_MIDDAY
    if frac < 0.85:
        return TOD_AFTERNOON
    return TOD_LATE


def session_bounds(rows: list[dict]) -> dict[str, tuple[float, float]]:
    """First and last captured decision instant per session date."""
    out: dict[str, tuple[float, float]] = {}
    for r in rows:
        ts = r.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        day = dt.datetime.fromtimestamp(float(ts), _IST).strftime("%Y-%m-%d")
        lo, hi = out.get(day, (float(ts), float(ts)))
        out[day] = (min(lo, float(ts)), max(hi, float(ts)))
    return out


def band_of(
    value: float | None, bands: tuple[tuple[str, float, float], ...],
) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    v = float(value)
    for name, lo, hi in bands:
        if lo <= v < hi:
            return name
    return None


def dte_band(dte: object) -> str | None:
    if not isinstance(dte, (int, float)) or isinstance(dte, bool):
        return None
    d = int(dte)
    for name, lo, hi in DTE_BANDS:
        if lo <= d <= hi:
            return name
    return None


def _at(row: dict, horizon: str) -> dict | None:
    h = (row.get("horizons") or {}).get(horizon)
    return h if isinstance(h, dict) else None


def horizon_stats(rows: list[dict], horizon: str, *,
                  cost_multiple: float = 1.0) -> dict:
    """Every §8 statistic for one subset at one horizon.

    ``cost_multiple`` re-charges the same trades for §20 rather than resolving
    them again, so a stress row is provably the same sample as its base row.
    """
    nets: list[float] = []
    grosses: list[float] = []
    costs: list[float] = []
    mfes: list[float] = []
    maes: list[float] = []
    t1 = 0
    counted = 0
    for r in rows:
        snap = _at(r, horizon)
        entry = r.get("entry_price")
        if snap is None or not isinstance(entry, (int, float)):
            continue
        gross = snap.get("gross_pct")
        net = p36outcome.net_from_gross(
            gross, cost_points=(r.get("cost") or {}).get("cost_points"),
            entry=float(entry), cost_multiple=cost_multiple,
        )
        if net is None or gross is None:
            continue
        counted += 1
        grosses.append(float(gross))
        nets.append(float(net))
        costs.append(round(float(gross) - float(net), 4))
        if isinstance(snap.get("mfe_pct"), (int, float)):
            mfes.append(float(snap["mfe_pct"]))
        if isinstance(snap.get("mae_pct"), (int, float)):
            maes.append(float(snap["mae_pct"]))
        # T1 is "did the leg ever clear its own round trip", which is the only
        # target definition that means the same thing for a ₹300 option and an
        # ₹8,700 future. A fixed percentage would make one of them unreachable.
        cost_pct = r.get("cost_pct")
        if isinstance(cost_pct, (int, float)) and isinstance(
            snap.get("mfe_pct"), (int, float),
        ):
            if float(snap["mfe_pct"]) >= float(cost_pct) * cost_multiple:
                t1 += 1
    ci = _ci95(nets)
    return {
        "horizon": horizon,
        "n": counted,
        "win_pct": (
            round(100.0 * sum(1 for x in nets if x > 0) / counted, 2)
            if counted else None
        ),
        "t1_pct": round(100.0 * t1 / counted, 2) if counted else None,
        "gross_mean_pct": _mean(grosses),
        "cost_mean_pct": _mean(costs),
        "net_mean_pct": _mean(nets),
        "net_median_pct": _median(nets),
        "net_ci95": list(ci) if ci else None,
        "profit_factor": _profit_factor(nets),
        "mfe_mean_pct": _mean(mfes),
        "mae_mean_pct": _mean(maes),
        "sufficient": counted >= MIN_TRIPLES_PER_VEHICLE,
    }


def vehicle_row(rows: list[dict], *, cost_multiple: float = 1.0) -> dict:
    """§8 — one vehicle's row, with the best hold chosen for the whole subset."""
    per_horizon = {
        h: horizon_stats(rows, h, cost_multiple=cost_multiple)
        for h in HORIZON_KEYS
    }
    ranked = [
        (h, s) for h, s in per_horizon.items()
        if s["net_mean_pct"] is not None and s["sufficient"]
    ]
    best = max(ranked, key=lambda kv: kv[1]["net_mean_pct"], default=None)
    sessions = {
        dt.datetime.fromtimestamp(float(r["ts"]), _IST).strftime("%Y-%m-%d")
        for r in rows if isinstance(r.get("ts"), (int, float))
    }
    entered = [r for r in rows if r.get("entry_price") is not None]
    return {
        "n_offered": len(rows),
        "n_entered": len(entered),
        "sessions": len(sessions),
        "spread_pct_mean": _mean([
            float(r["spread_pct"]) for r in entered
            if isinstance(r.get("spread_pct"), (int, float))
        ]),
        "cost_pct_mean": _mean([
            float(r["cost_pct"]) for r in entered
            if isinstance(r.get("cost_pct"), (int, float))
        ]),
        "by_horizon": per_horizon,
        "best_hold": best[0] if best else None,
        "best_hold_net_mean_pct": best[1]["net_mean_pct"] if best else None,
        "best_hold_note": (
            "chosen from the subset's mean net across horizons, not per trade"
        ),
    }


def _directional(rows: list[dict]) -> list[dict]:
    """Only the expressions a system could actually have taken on the view."""
    return [r for r in rows if r.get("role") == DIRECTIONAL]


def compare(rows: list[dict], *, cost_multiple: float = 1.0) -> dict:
    """§8 — FUTURES vs CE vs PE over the directional expressions.

    The counterfactual option side is excluded from this table by construction
    and reported separately by :func:`counterfactual`: letting it in would let
    "the put paid on a long signal" be reported as a vehicle advantage when it is
    a statement about the direction being wrong.
    """
    directional = _directional(rows)
    table = {
        v: vehicle_row(
            [r for r in directional if r["vehicle"] == v],
            cost_multiple=cost_multiple,
        )
        for v in (FUTURES, CE, PE)
    }
    return {
        "table": table,
        "ranking": _rank(table),
        "cost_multiple": cost_multiple,
        "note": (
            "directional expressions only; the opposite option side is reported "
            "separately as a counterfactual and can never win this table"
        ),
    }


def _rank(table: dict[str, dict]) -> list[dict]:
    """Vehicles ordered by net mean at their own best hold, floors respected."""
    out = []
    for vehicle, row in table.items():
        net = row.get("best_hold_net_mean_pct")
        out.append({
            "vehicle": vehicle,
            "n": row.get("n_entered"),
            "best_hold": row.get("best_hold"),
            "net_mean_pct": net,
            "eligible": net is not None,
        })
    out.sort(
        key=lambda r: (r["eligible"], r["net_mean_pct"] or float("-inf")),
        reverse=True,
    )
    return out


def by_direction(rows: list[dict], *, cost_multiple: float = 1.0) -> dict:
    """§9 — LONG: FUTURES vs CE. SHORT: FUTURES vs PE."""
    out: dict[str, dict] = {}
    for view in (LONG, SHORT):
        subset = [r for r in _directional(rows) if r.get("direction") == view]
        want = CE if view == LONG else PE
        out[view] = {
            "n_triples": len({r["obs_id"] for r in subset}),
            FUTURES: vehicle_row(
                [r for r in subset if r["vehicle"] == FUTURES],
                cost_multiple=cost_multiple,
            ),
            want: vehicle_row(
                [r for r in subset if r["vehicle"] == want],
                cost_multiple=cost_multiple,
            ),
            "option_side": want,
        }
    return out


def counterfactual(rows: list[dict], *, cost_multiple: float = 1.0) -> dict:
    """§9 — the non-directional CE/PE counterfactual, reported apart."""
    subset = [r for r in rows if r.get("role") != DIRECTIONAL]
    return {
        "n": len(subset),
        "by_vehicle": {
            v: vehicle_row(
                [r for r in subset if r["vehicle"] == v],
                cost_multiple=cost_multiple,
            )
            for v in (CE, PE)
        },
        "note": (
            "the option side opposite the recorded view; a win here is evidence "
            "about direction, not about vehicle selection"
        ),
    }


def _cut(
    rows: list[dict], key, *, horizon: str, cost_multiple: float = 1.0,
    vehicles: tuple[str, ...] = (FUTURES, CE, PE),
) -> dict:
    """Group directional rows by a labelling function, then per vehicle.

    ``vehicles`` is narrowed for the option-only cuts (§10-§12): printing an
    empty FUTURES row under a premium band would suggest the future had been
    measured there and found wanting, when a future simply has no premium.
    """
    buckets: dict[str, list[dict]] = {}
    for r in _directional(rows):
        label = key(r)
        if label is None:
            continue
        buckets.setdefault(str(label), []).append(r)
    out: dict[str, dict] = {}
    for label, subset in buckets.items():
        out[label] = {
            "n": len(subset),
            "by_vehicle": {
                v: horizon_stats(
                    [r for r in subset if r["vehicle"] == v], horizon,
                    cost_multiple=cost_multiple,
                )
                for v in vehicles
            },
            "spread_pct_mean": _mean([
                float(r["spread_pct"]) for r in subset
                if isinstance(r.get("spread_pct"), (int, float))
            ]),
            "cost_pct_mean": _mean([
                float(r["cost_pct"]) for r in subset
                if isinstance(r.get("cost_pct"), (int, float))
            ]),
        }
    return dict(sorted(out.items()))


def by_premium_band(rows: list[dict], *, horizon: str) -> dict:
    """§10 — premium bands. Options only: a future has no premium."""
    options = [r for r in rows if r["vehicle"] in (CE, PE)]
    return _cut(
        options, lambda r: band_of(r.get("premium"), PREMIUM_BANDS),
        horizon=horizon, vehicles=(CE, PE),
    )


def by_dte(rows: list[dict], *, horizon: str) -> dict:
    """§11 — DTE bands, options only."""
    options = [r for r in rows if r["vehicle"] in (CE, PE)]
    return _cut(options, lambda r: dte_band(r.get("dte")), horizon=horizon,
                vehicles=(CE, PE))


def by_moneyness(rows: list[dict], *, horizon: str) -> dict:
    """§12 — which strike converts the move into the best net return."""
    options = [r for r in rows if r["vehicle"] in (CE, PE)]
    return _cut(options, lambda r: r.get("atm_bucket"), horizon=horizon,
                vehicles=(CE, PE))


def by_time_of_day(rows: list[dict], *, horizon: str) -> dict:
    """§13 — same vehicle, different hour."""
    bounds = session_bounds(rows)

    def label(r: dict) -> str | None:
        ts = r.get("ts")
        if not isinstance(ts, (int, float)):
            return None
        day = dt.datetime.fromtimestamp(float(ts), _IST).strftime("%Y-%m-%d")
        lo, hi = bounds.get(day, (None, None))
        if lo is None or hi is None:
            return None
        return time_of_day(float(ts), session_open=lo, session_close=hi)

    return _cut(rows, label, horizon=horizon)


def by_cost_over_move(rows: list[dict], *, horizon: str) -> dict:
    """§17 — is positive net concentrated in an economic band?

    The denominator is the leg's own measured MFE at the horizon, not a forecast
    of the move. That makes the table descriptive rather than predictive, which
    is what §17 asks for: it says where the money was, not where it will be.
    """
    def label(r: dict) -> str | None:
        snap = _at(r, horizon)
        cost_pct = r.get("cost_pct")
        if snap is None or not isinstance(cost_pct, (int, float)):
            return None
        mfe = snap.get("mfe_pct")
        if not isinstance(mfe, (int, float)) or float(mfe) <= 0:
            return "GE_100PCT"  # no favourable excursion: cost is all of it
        return band_of(100.0 * float(cost_pct) / float(mfe), COST_MOVE_BANDS)

    return _cut(rows, label, horizon=horizon)


def giveback(rows: list[dict]) -> dict:
    """§16 — per vehicle: peak, when, how much came back, and how often."""
    out: dict[str, dict] = {}
    for v in (FUTURES, CE, PE):
        subset = [
            r["giveback"] for r in _directional(rows)
            if r["vehicle"] == v and isinstance(r.get("giveback"), dict)
        ]
        if not subset:
            out[v] = {"n": 0}
            continue
        peaks = [float(g["peak_pct"]) for g in subset
                 if isinstance(g.get("peak_pct"), (int, float))]
        went_green = [g for g in subset if (g.get("peak_pct") or 0) > 0]
        out[v] = {
            "n": len(subset),
            "peak_mean_pct": _mean(peaks),
            "time_to_peak_median_min": _median([
                float(g["time_to_peak_min"]) for g in subset
                if isinstance(g.get("time_to_peak_min"), (int, float))
            ]),
            "max_giveback_mean_pct": _mean([
                float(g["max_giveback_pct"]) for g in subset
                if isinstance(g.get("max_giveback_pct"), (int, float))
            ]),
            "pct_of_mfe_given_back_median": _median([
                float(g["pct_of_mfe_given_back"]) for g in subset
                if isinstance(g.get("pct_of_mfe_given_back"), (int, float))
            ]),
            "returned_to_entry_pct": (
                round(100.0 * sum(
                    1 for g in went_green if g.get("returned_to_entry")
                ) / len(went_green), 2) if went_green else None
            ),
            "turned_negative_pct": (
                round(100.0 * sum(
                    1 for g in went_green
                    if g.get("turned_negative_after_profitable")
                ) / len(went_green), 2) if went_green else None
            ),
            "adverse_first_pct": round(
                100.0 * sum(1 for g in subset if g.get("adverse_first"))
                / len(subset), 2,
            ),
            "went_green_n": len(went_green),
        }
    return out


def cost_decomposition(rows: list[dict], *, horizon: str) -> dict:
    """§7/§11 of the report — GROSS → SPREAD/BROKERAGE/TAX/SLIPPAGE → NET."""
    out: dict[str, dict] = {}
    for v in (FUTURES, CE, PE):
        subset = [
            r for r in _directional(rows)
            if r["vehicle"] == v and r.get("entry_price")
        ]
        if not subset:
            out[v] = {"n": 0}
            continue

        def pct(field: str, rows_in: list[dict] = subset) -> float | None:
            vals = []
            for r in rows_in:
                pts = (r.get("cost") or {}).get(field)
                entry = r.get("entry_price")
                if isinstance(pts, (int, float)) and isinstance(
                    entry, (int, float),
                ) and float(entry) > 0:
                    vals.append(100.0 * float(pts) / float(entry))
            return _mean(vals)

        stats = horizon_stats(subset, horizon)
        out[v] = {
            "n": stats["n"],
            "gross_mean_pct": stats["gross_mean_pct"],
            "spread_mean_pct": pct("spread_points"),
            "brokerage_mean_pct": pct("brokerage_points"),
            "tax_mean_pct": pct("tax_points"),
            "slippage_mean_pct": pct("slippage_points"),
            "net_mean_pct": stats["net_mean_pct"],
            "spread_note": (
                (subset[0].get("cost") or {}).get("spread_note")
            ),
        }
    return out
