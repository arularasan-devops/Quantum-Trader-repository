"""The CAS experiments — §8 entry timing, §9 strike distance, §17 expiry cohorts.

Each experiment replays the *recorded ladder* rather than a live book: enter at
the ask at a chosen clock and rung, mark forward on the bid, settle with costs.
Because every cell is run over the same observations, the comparison between
15:10 and 15:20, or between ATM and far OTM, is a comparison of decisions rather
than of days.

Three disciplines carried over from the five-year study, which found nothing once
they were applied:

* **Every cell is counted.** ``comparisons`` is reported so a reader can see how
  many chances noise had to produce the best-looking row.
* **Never optimise and evaluate on the same rows.** Cells are computed on the
  development split; the winner must repeat on validation before anything is
  said about it. Selection on the full sample is not offered as an option.
* **Nothing here changes production.** Strike selection, entry timing and the
  premium floor in the live engine are untouched by every number in this file.
"""
from __future__ import annotations

from statistics import median

from app.research.phase18 import execution, paper, quality, schema, session

# A cell below this many resolved legs is reported but never ranked.
MIN_CELL = 20


def _f(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return None if f != f else f


def _clock_minutes(label: str) -> int:
    hh, mm = label.split(":")
    return int(hh) * 60 + int(mm)


def _rows_by_key(observations: list[dict]) -> dict[tuple[str, str], list[dict]]:
    out: dict[tuple[str, str], list[dict]] = {}
    for o in observations:
        key = (str(o.get("session") or ""), str(o.get("instrument") or ""))
        if key[0] and key[1]:
            out.setdefault(key, []).append(o)
    for rows in out.values():
        rows.sort(key=lambda r: _f(r.get("intent_ts")) or 0.0)
    return out


def _quote(obs: dict, rung: str, side: str) -> dict | None:
    for lr in obs.get("ladder") or []:
        if lr.get("rung") == rung:
            q = lr.get("ce" if side == schema.CE else "pe")
            return q if isinstance(q, dict) else None
    return None


def _series(rows: list[dict], rung: str, side: str) -> list[dict]:
    out = []
    for o in rows:
        q = _quote(o, rung, side)
        if q:
            out.append({"ts": _f(o.get("intent_ts")) or 0.0,
                        "bid": _f(q.get("bid")), "quote": q})
    return out


def replay_leg(
    rows: list[dict],
    *,
    entry_clock: str,
    rung: str,
    side: str,
    lots: int = 1,
    slippage_ticks: int = 0,
) -> dict | None:
    """One synthetic leg: enter at ``entry_clock``, hold to the window's end."""
    want = _clock_minutes(entry_clock)
    entry_obs = next(
        (o for o in rows
         if session.minute_of_day(_f(o.get("intent_ts")) or 0.0) >= want),
        None,
    )
    if entry_obs is None:
        return None
    eq = _quote(entry_obs, rung, side)
    fill = execution.entry_fill(eq)
    if fill["executability"] != schema.EXECUTABLE:
        return {
            "entry_clock": entry_clock, "rung": rung, "side": side,
            "executability": schema.EXECUTABILITY_UNKNOWN,
            "reason": fill.get("reason"),
            "session": entry_obs.get("session"),
            "instrument": entry_obs.get("instrument"),
            "expiry_class": entry_obs.get("expiry_class"),
        }

    entry_ts = _f(entry_obs.get("intent_ts")) or 0.0
    after = [o for o in rows if (_f(o.get("intent_ts")) or 0.0) >= entry_ts]
    ser = _series(after, rung, side)
    exit_q = next(
        (s["quote"] for s in reversed(ser)
         if s["bid"] is not None
         and str(s["quote"].get("data_quality") or quality.MISSING)
         in quality.FILLABLE),
        None,
    )
    peak_q = None
    best = None
    for s in ser:
        if s["bid"] is not None and (best is None or s["bid"] > best):
            best, peak_q = s["bid"], s["quote"]

    settled = execution.settle(
        instrument=str(entry_obs.get("instrument") or ""),
        entry_quote=eq, exit_quote=exit_q, peak_quote=peak_q,
        lots=lots, slippage_ticks=slippage_ticks,
    )
    entry_px = _f(settled.get("entry_price")) or _f(fill.get("price")) or 0.0
    bids = [s["bid"] for s in ser if s["bid"] is not None]
    return {
        "session": entry_obs.get("session"),
        "instrument": entry_obs.get("instrument"),
        "expiry_class": entry_obs.get("expiry_class"),
        "entry_clock": entry_clock,
        "rung": rung,
        "side": side,
        "entry_price": entry_px,
        "entry_spread": fill.get("spread"),
        "entry_spread_pct": _f((eq or {}).get("spread_pct")),
        "executability": settled.get("executability"),
        "net_rupees": settled.get("net_rupees"),
        "net_points": settled.get("net_points"),
        "gross_rupees": settled.get("gross_rupees"),
        "theoretical_rupees": settled.get("theoretical_rupees"),
        "vanished_rupees": settled.get("vanished_rupees"),
        "mfe_points": (
            None if not bids or not entry_px else round(max(bids) - entry_px, 2)
        ),
        "mae_points": (
            None if not bids or not entry_px else round(min(bids) - entry_px, 2)
        ),
        "peak_return_pct": (
            None if not bids or not entry_px
            else round(100.0 * (max(bids) - entry_px) / entry_px, 1)
        ),
        "worthless": bool(bids and max(bids) <= 0.05),
        "net_r": _net_r(settled, entry_px),
        "exit_variants_available": exit_q is not None,
    }


def _net_r(settled: dict, entry_px: float) -> float | None:
    net = _f(settled.get("net_points"))
    if net is None or entry_px <= 0:
        return None
    risk = max(0.05, entry_px * 0.5)
    return round(net / risk, 3)


def _cell(rows: list[dict]) -> dict:
    priced = [r for r in rows if r.get("executability") == schema.EXECUTABLE]
    nets = [_f(r.get("net_rupees")) for r in priced]
    nets = [n for n in nets if n is not None]
    rs = [_f(r.get("net_r")) for r in priced]
    rs = [r for r in rs if r is not None]
    mfes = [_f(r.get("mfe_points")) for r in priced]
    mfes = [m for m in mfes if m is not None]
    maes = [_f(r.get("mae_points")) for r in priced]
    maes = [m for m in maes if m is not None]
    spreads = [_f(r.get("entry_spread_pct")) for r in priced]
    spreads = [s for s in spreads if s is not None]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    return {
        "attempted": len(rows),
        "priced": len(priced),
        "unpriceable": len(rows) - len(priced),
        "net_expectancy_r": round(sum(rs) / len(rs), 4) if rs else None,
        "mean_net_rupees": round(sum(nets) / len(nets), 2) if nets else None,
        "positive_close_pct": (
            round(100.0 * len(wins) / len(nets), 1) if nets else None
        ),
        "loss_pct": round(100.0 * len(losses) / len(nets), 1) if nets else None,
        "profit_factor": (
            round(sum(wins) / abs(sum(losses)), 2) if wins and losses else None
        ),
        "median_mfe_points": round(median(mfes), 2) if mfes else None,
        "median_mae_points": round(median(maes), 2) if maes else None,
        "median_entry_spread_pct": (
            round(median(spreads), 2) if spreads else None
        ),
        "worthless_pct": (
            round(100.0 * sum(1 for r in priced if r.get("worthless"))
                  / len(priced), 1) if priced else None
        ),
        "rankable": len(nets) >= MIN_CELL,
    }


def entry_timing(
    observations: list[dict], *, rung: str = schema.OTM_1
) -> dict:
    """§8: does the clock matter, at a fixed rung?"""
    per_clock: dict[str, list[dict]] = {c: [] for c in paper.ENTRY_TIMES}
    for rows in _rows_by_key(observations).values():
        for clock in paper.ENTRY_TIMES:
            for side in schema.SIDES:
                leg = replay_leg(rows, entry_clock=clock, rung=rung, side=side)
                if leg:
                    per_clock[clock].append(leg)
    cells = {c: _cell(rows) for c, rows in per_clock.items()}
    return {
        "dimension": "entry_timing",
        "rung": rung,
        "cells": cells,
        "comparisons": len(cells),
        "best": _best(cells),
        "note": (
            "Both sides are replayed at every clock, so a clock cannot look good "
            "merely because the direction engine preferred it that day."
        ),
    }


def strike_distance(observations: list[dict], *, entry_clock: str = "15:15") -> dict:
    """§9: run separately for CE and PE, because they are not symmetric."""
    out: dict[str, dict] = {}
    total_cells = 0
    for side in schema.SIDES:
        per_rung: dict[str, list[dict]] = {r: [] for r in schema.RUNGS}
        for rows in _rows_by_key(observations).values():
            for rung in schema.RUNGS:
                leg = replay_leg(
                    rows, entry_clock=entry_clock, rung=rung, side=side,
                )
                if leg:
                    per_rung[rung].append(leg)
        cells = {r: _cell(rows) for r, rows in per_rung.items()}
        total_cells += len(cells)
        out[side] = {"cells": cells, "best": _best(cells)}
    return {
        "dimension": "strike_distance",
        "entry_clock": entry_clock,
        "by_side": out,
        "comparisons": total_cells,
        "production_untouched": True,
        "note": (
            "Research only. The live strike selector, premium floor and stop "
            "geometry are not modified by anything measured here."
        ),
    }


def ce_vs_pe(observations: list[dict], *, rung: str = schema.OTM_1,
             entry_clock: str = "15:15") -> dict:
    """Both sides bought at the same instant on the same ladder."""
    per_side: dict[str, list[dict]] = {s: [] for s in schema.SIDES}
    for rows in _rows_by_key(observations).values():
        for side in schema.SIDES:
            leg = replay_leg(rows, entry_clock=entry_clock, rung=rung, side=side)
            if leg:
                per_side[side].append(leg)
    cells = {s: _cell(rows) for s, rows in per_side.items()}
    return {
        "dimension": "ce_vs_pe",
        "rung": rung,
        "entry_clock": entry_clock,
        "cells": cells,
        "comparisons": len(cells),
        "best": _best(cells),
    }


def expiry_cohorts(observations: list[dict], *, rung: str = schema.OTM_1,
                   entry_clock: str = "15:15") -> dict:
    """§17: expiry day, the day before, and everything else — kept apart."""
    per_class: dict[str, list[dict]] = {c: [] for c in schema.EXPIRY_CLASSES}
    for rows in _rows_by_key(observations).values():
        for side in schema.SIDES:
            leg = replay_leg(rows, entry_clock=entry_clock, rung=rung, side=side)
            if not leg:
                continue
            cls = str(leg.get("expiry_class") or "")
            if cls in per_class:
                per_class[cls].append(leg)
    cells = {c: _cell(rows) for c, rows in per_class.items()}
    return {
        "dimension": "expiry_cohort",
        "rung": rung,
        "entry_clock": entry_clock,
        "cells": cells,
        "comparisons": len(cells),
        "best": _best(cells),
    }


def _best(cells: dict[str, dict]) -> dict:
    rankable = {
        k: v for k, v in cells.items()
        if v.get("rankable") and v.get("net_expectancy_r") is not None
    }
    if not rankable:
        return {
            "cell": None,
            "verdict": schema.REQUIRES_MORE_DATA,
            "reason": (
                f"no cell has the {MIN_CELL} priced legs needed before it is "
                "ranked at all"
            ),
        }
    key = max(rankable, key=lambda k: float(rankable[k]["net_expectancy_r"]))
    top = rankable[key]
    return {
        "cell": key,
        "net_expectancy_r": top["net_expectancy_r"],
        "priced": top["priced"],
        "verdict": schema.RESEARCH,
        "reason": (
            "Best of the cells on this split only. It has not been confirmed on "
            "a later, untouched period, so it is a candidate to test, not a "
            "finding."
        ),
    }
