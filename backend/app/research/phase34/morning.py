"""Phase 34 §4/§5 — morning direction persistence, and picking today's instrument.

Two questions from the chart, both answerable on the five-year series and neither
answerable by looking at a chart after the fact:

§4 *Can the direction be called from the morning?*
    Take the sign of the first ``OPEN_WINDOW`` minutes of the session and hold it to
    a set of horizons and to the close. Every session is counted — the ones that
    reversed as well as the ones that ran. Reported per instrument, per direction,
    with the adverse excursion beside the favourable one, chronologically split, and
    net of the same round-trip cost every other phase charges.

§5 *Which instrument is hot today?*
    Rank sessions by information available at ``09:45`` only — opening-range
    expansion against the instrument's own trailing baseline, and realised range in
    the open window. Then measure what the top-ranked bucket actually did for the
    *rest* of the session. Using the day's full range to rank the day would be the
    obvious way to produce a beautiful, useless table, so the ranking features are
    computed from the open window alone and the outcome from what follows it.

Nothing here is wired into production and nothing selects an instrument for a live
order. The output is a measurement, and it is allowed to say the morning tells you
nothing.
"""
from __future__ import annotations

import json
import os

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase31.excursion import _prepare
from app.research.phase32.reach import cost_pct
from app.research.phase34.study import ARTEFACT_DIR, _stats

# Frozen before any result was computed.
OPEN_WINDOW = 30          # minutes of the session used to read the direction
HORIZONS = (30, 60, 120)  # minutes held after the open window, plus SESSION_CLOSE
BASELINE_SESSIONS = 20    # trailing sessions for the volatility baseline
VOL_BUCKETS = (("BOTTOM_THIRD", 0.0, 1 / 3), ("MIDDLE_THIRD", 1 / 3, 2 / 3),
               ("TOP_THIRD", 2 / 3, 1.000001))
MIN_SESSIONS = 200

UP, DOWN = "UP", "DOWN"
NO_PERSISTENCE = "NO_MORNING_DIRECTION_EDGE_FOUND"
PERSISTENCE = "MORNING_DIRECTION_LEAD"


def _excursion(s: object, seg: slice, entry: float, sgn: float) -> tuple[float, float]:
    """Favourable and adverse excursion in percent, signed by the held direction.

    A short's favourable side is the segment low and its adverse side the high, so
    both are read from the extreme that would actually have hit the trader.
    """
    hi = float(np.max(s.high[seg]))
    lo = float(np.min(s.low[seg]))
    fav_extreme, adv_extreme = (hi, lo) if sgn > 0 else (lo, hi)
    return (
        100.0 * sgn * (fav_extreme - entry) / entry,
        100.0 * sgn * (adv_extreme - entry) / entry,
    )


class Sessions:
    """Per-session open-window features and forward outcomes, one row per session."""

    __slots__ = ("instrument", "day", "direction", "open_range_pct", "expansion",
                 "fav", "adv", "net", "gross", "cost", "cost_pct")

    def __init__(self, instrument: str) -> None:
        self.instrument = instrument


def build(instrument: str, series: object | None = None) -> Sessions:
    """Open-window features and forward returns for every session in the series."""
    s = series if series is not None else p24data.load_series(instrument)
    if s is None:
        raise ValueError(f"no five-year series for {instrument}")
    sess, minute = _prepare(s)
    out = Sessions(instrument)
    day: list[int] = []
    direction: list[int] = []
    open_range: list[float] = []
    fav: dict[str, list[float]] = {str(h): [] for h in HORIZONS}
    adv: dict[str, list[float]] = {str(h): [] for h in HORIZONS}
    net: dict[str, list[float]] = {str(h): [] for h in HORIZONS}
    gross: dict[str, list[float]] = {str(h): [] for h in HORIZONS}
    fav["CLOSE"], adv["CLOSE"] = [], []
    net["CLOSE"], gross["CLOSE"] = [], []
    costs = cost_pct(instrument, s.close)
    charged: list[float] = []

    bounds = np.flatnonzero(np.diff(sess)) + 1
    starts = np.concatenate(([0], bounds))
    ends = np.concatenate((bounds, [len(s)]))
    for a, b in zip(starts, ends):
        win = np.flatnonzero(minute[a:b] < OPEN_WINDOW) + a
        if win.size < OPEN_WINDOW // 2:
            continue
        last_open = int(win[-1])
        if last_open + 1 >= b:
            continue
        entry = float(s.close[last_open])
        if not np.isfinite(entry) or entry <= 0:
            continue
        first_open = float(s.open[int(win[0])])
        sgn = 1.0 if entry > first_open else -1.0
        rng = float(np.max(s.high[win]) - np.min(s.low[win]))
        day.append(int(s.ts[a]))
        direction.append(int(sgn))
        open_range.append(100.0 * rng / entry)
        c = float(costs[last_open]) if np.isfinite(costs[last_open]) else 0.0
        charged.append(c)
        for h in HORIZONS:
            stop = min(last_open + int(h), b - 1)
            seg = slice(last_open + 1, stop + 1)
            if not s.high[seg].size:
                fav[str(h)].append(np.nan)
                adv[str(h)].append(np.nan)
                net[str(h)].append(np.nan)
                gross[str(h)].append(np.nan)
                continue
            f, d = _excursion(s, seg, entry, sgn)
            fav[str(h)].append(f)
            adv[str(h)].append(d)
            r = 100.0 * sgn * (float(s.close[stop]) - entry) / entry
            gross[str(h)].append(r)
            net[str(h)].append(r - c)
        end_i = b - 1
        r = 100.0 * sgn * (float(s.close[end_i]) - entry) / entry
        f, d = _excursion(s, slice(last_open + 1, b), entry, sgn)
        fav["CLOSE"].append(f)
        adv["CLOSE"].append(d)
        gross["CLOSE"].append(r)
        net["CLOSE"].append(r - c)

    out.day = np.asarray(day, dtype=np.int64)
    out.direction = np.asarray(direction, dtype=np.int32)
    out.open_range_pct = np.asarray(open_range, dtype=np.float64)
    out.fav = {k: np.asarray(v, dtype=np.float64) for k, v in fav.items()}
    out.adv = {k: np.asarray(v, dtype=np.float64) for k, v in adv.items()}
    out.net = {k: np.asarray(v, dtype=np.float64) for k, v in net.items()}
    out.gross = {k: np.asarray(v, dtype=np.float64) for k, v in gross.items()}
    out.cost = np.asarray(charged, dtype=np.float64)
    out.expansion = expansion(out.open_range_pct)
    out.cost_pct = float(np.nanmedian(costs))
    return out


def expansion(open_range_pct: np.ndarray) -> np.ndarray:
    """Open range against the median of the previous ``BASELINE_SESSIONS`` opens.

    Strictly trailing: session ``i`` uses sessions ``i-20 .. i-1`` and never itself,
    so the ranking a live morning could compute is the ranking measured here.
    """
    out = np.full(open_range_pct.size, np.nan)
    for i in range(BASELINE_SESSIONS, open_range_pct.size):
        base = np.nanmedian(open_range_pct[i - BASELINE_SESSIONS : i])
        if np.isfinite(base) and base > 0:
            out[i] = open_range_pct[i] / base
    return out


def _chronological(x: np.ndarray) -> dict:
    n = x.size
    dev, val = int(n * 0.60), int(n * 0.80)
    return {
        "development": _stats(x[:dev]),
        "validation": _stats(x[dev:val]),
        "untouched_holdout": _stats(x[val:]),
    }


def persistence(sess: Sessions) -> list[dict]:
    """Rows for both answers the morning allows: hold the direction, or fade it.

    ``FADE`` is not a second search over the same data dressed as a new idea — it is
    the arithmetic complement of ``CONTINUE`` on the identical sessions, and it costs
    the identical round trip. Both are counted as hypotheses because a reader who is
    told only the losing half of a mirrored pair has been told half the truth.
    """
    rows: list[dict] = []
    for name, want in ((UP, 1), (DOWN, -1)):
        mask = sess.direction == want
        for horizon in [str(h) for h in HORIZONS] + ["CLOSE"]:
            gross = sess.gross[horizon][mask]
            cost = sess.cost[mask]
            fav = sess.fav[horizon][mask]
            adv = sess.adv[horizon][mask]
            for stance, sign in (("CONTINUE", 1.0), ("FADE", -1.0)):
                net = sign * gross - cost
                finite = np.isfinite(net)
                f, d = (fav, adv) if sign > 0 else (-adv, -fav)
                rows.append({
                    "instrument": sess.instrument,
                    "open_direction": name,
                    "stance": stance,
                    "horizon_minutes": horizon,
                    "sessions": int(finite.sum()),
                    "win_rate_pct": round(float(np.mean(net[finite] > 0) * 100.0), 3)
                    if finite.any() else None,
                    "median_favourable_pct": round(float(np.nanmedian(f)), 4)
                    if f.size else None,
                    "median_adverse_pct": round(float(np.nanmedian(d)), 4)
                    if d.size else None,
                    "net_after_cost": _stats(net[finite]),
                    **_chronological(net[finite]),
                })
    return rows


def volatility_ranking(sess: Sessions) -> list[dict]:
    """What the top-ranked morning-volatility bucket did for the rest of the day."""
    exp = sess.expansion
    ok = np.isfinite(exp)
    if not ok.any():
        return []
    order = np.argsort(exp[ok])
    ranked = np.flatnonzero(ok)[order]
    rows: list[dict] = []
    for label, lo, hi in VOL_BUCKETS:
        a, b = int(len(ranked) * lo), int(len(ranked) * hi)
        pick = np.sort(ranked[a:b])
        if not pick.size:
            continue
        net = sess.net["CLOSE"][pick]
        finite = np.isfinite(net)
        rows.append({
            "instrument": sess.instrument,
            "bucket": label,
            "sessions": int(pick.size),
            "median_expansion_x": round(float(np.nanmedian(exp[pick])), 4),
            "median_open_range_pct": round(
                float(np.nanmedian(sess.open_range_pct[pick])), 4),
            "median_rest_of_day_favourable_pct": round(
                float(np.nanmedian(sess.fav["CLOSE"][pick])), 4),
            "median_rest_of_day_adverse_pct": round(
                float(np.nanmedian(sess.adv["CLOSE"][pick])), 4),
            "net_after_cost": _stats(net[finite]),
            **_chronological(net[finite]),
        })
    return rows


def run(instruments: tuple[str, ...] = ("NIFTY", "CRUDEOIL"), *,
        out_dir: str | None = None, progress: bool = True) -> dict:
    """The authoritative morning study. Counts every session and every hypothesis."""
    persist: list[dict] = []
    vol: list[dict] = []
    inventory: list[dict] = []
    for inst in instruments:
        if progress:
            print(f"[{inst}] building sessions", flush=True)
        s = build(inst)
        inventory.append({
            "instrument": inst,
            "sessions": int(s.day.size),
            "up_sessions": int((s.direction > 0).sum()),
            "down_sessions": int((s.direction < 0).sum()),
            "round_trip_cost_pct": round(s.cost_pct, 5),
            "sufficient": bool(s.day.size >= MIN_SESSIONS),
        })
        persist.extend(persistence(s))
        vol.extend(volatility_ranking(s))

    hypotheses = len(persist) + len(vol)
    leads = [
        r for r in persist
        if r["sessions"] >= MIN_SESSIONS
        and r["development"]["net_expectancy_pct"] is not None
        and r["development"]["net_expectancy_pct"] > 0
        and r["validation"]["net_expectancy_pct"] is not None
        and r["validation"]["net_expectancy_pct"] > 0
        and r["untouched_holdout"]["net_expectancy_pct"] is not None
        and r["untouched_holdout"]["net_expectancy_pct"] > 0
    ]
    out = {
        "open_window_minutes": OPEN_WINDOW,
        "horizons_minutes": list(HORIZONS) + ["CLOSE"],
        "baseline_sessions": BASELINE_SESSIONS,
        "inventory": inventory,
        "hypotheses_counted": hypotheses,
        "direction_persistence": persist,
        "volatility_ranking": vol,
        "leads": leads,
        "verdict": PERSISTENCE if leads else NO_PERSISTENCE,
        "production_changed": False,
    }
    target = out_dir or p24data._resolve(ARTEFACT_DIR)
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "p34_morning.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    return out
