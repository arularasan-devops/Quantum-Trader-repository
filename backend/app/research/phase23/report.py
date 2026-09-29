"""Daily and cumulative reporting for the hurdle shadow.

Three arms are scored on the SAME resolved trades: the live engine's book, and
the two shadow books that drop the trades their cutoff refuses. So the difference
between two arms is only ever the trades one of them declined — which is what
makes MONEY SAVED and MONEY MISSED meaningful rather than a comparison of two
different strategies.

Every number here is computed only from rows whose hurdle was MEASURED and whose
trade RESOLVED. Unmeasured rows are counted and reported separately; they are
never costed at an assumed spread to fill a gap in the table.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

from app.research.phase23 import hurdle as hurdle_mod, store

IST = timezone(timedelta(hours=5, minutes=30))

EXISTING = "EXISTING"
SHADOW_5 = "SHADOW_LE_5"
SHADOW_3 = "SHADOW_LE_3"

_ARM_FIELD = {
    EXISTING: "arm_existing",
    SHADOW_5: "arm_shadow_5",
    SHADOW_3: "arm_shadow_3",
}

# Session windows in IST. MCX runs into the night, so the last bucket is wide
# rather than absent; a bucket with no trades is reported as empty, not dropped.
_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("09:15-10:00", 555, 600),
    ("10:00-11:30", 600, 690),
    ("11:30-13:00", 690, 780),
    ("13:00-14:30", 780, 870),
    ("14:30-15:30", 870, 930),
    ("15:30-19:00", 930, 1140),
    ("19:00-23:59", 1140, 1440),
)


def ist_date(ts: int | float | None) -> str | None:
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    return datetime.fromtimestamp(int(ts), IST).strftime("%Y-%m-%d")


def time_bucket(ts: int | float | None) -> str:
    if not isinstance(ts, (int, float)) or ts <= 0:
        return "UNKNOWN"
    dt = datetime.fromtimestamp(int(ts), IST)
    minutes = dt.hour * 60 + dt.minute
    for label, start, end in _BUCKETS:
        if start <= minutes < end:
            return label
    return "PRE_OPEN"


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def measured(rows: list[dict]) -> list[dict]:
    return [r for r in rows
            if r.get("hurdle_status") == hurdle_mod.MEASURED
            and _num(r.get("hurdle_pct")) is not None]


def real_feed_rows(rows: list[dict]) -> list[dict]:
    """Only rows recorded against a real broker feed.

    A simulated tick can produce a perfectly formed book that means nothing, so
    those rows stay on the file and in coverage but never reach an arm, the sweep
    or the verdict.
    """
    return [r for r in rows if r.get("real_feed") is True]


def taken(rows: list[dict]) -> list[dict]:
    """Rows the live engine actually bought AND that have closed."""
    return [r for r in real_feed_rows(rows)
            if r.get("engine_would_buy") and r.get("resolved")
            and _num(r.get("net_pnl")) is not None]


def arm_rows(rows: list[dict], arm: str, threshold: float | None = None) -> list[dict]:
    """The resolved trades one arm would hold.

    ``EXISTING`` holds everything the engine took. A shadow arm holds the subset
    its cutoff allows; a row it abstains on (no measured book) is not held and not
    counted against it either — those rows are reported as coverage.
    """
    pool = taken(rows)
    if arm == EXISTING:
        return pool
    field = _ARM_FIELD.get(arm)
    out = []
    for r in pool:
        if field is not None:
            decision = r.get(field)
        else:
            h = _num(r.get("hurdle_pct"))
            decision = (hurdle_mod.ABSTAIN if h is None else
                        hurdle_mod.ALLOW if h <= float(threshold or 0)
                        else hurdle_mod.REFUSE)
        if decision == hurdle_mod.ALLOW:
            out.append(r)
    return out


def _max_drawdown(pnls: list[float]) -> float:
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return round(worst, 1)


def _losing_streak(pnls: list[float]) -> int:
    worst = 0
    run = 0
    for p in pnls:
        if p < 0:
            run += 1
            worst = max(worst, run)
        else:
            run = 0
    return worst


def metrics(rows: list[dict]) -> dict:
    """The metric block for one arm's held trades, in chronological order."""
    rows = sorted(rows, key=lambda r: int(r.get("ts") or 0))
    pnls = [float(r["net_pnl"]) for r in rows]
    rs = [_num(r.get("net_r")) for r in rows]
    rs = [x for x in rs if x is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    t1 = [r for r in rows if r.get("outcome") == "T1_BEFORE_SL"]
    sl = [r for r in rows if r.get("outcome") == "SL"]
    hurdles = [_num(r.get("hurdle_pct")) for r in rows]
    hurdles = [h for h in hurdles if h is not None]
    spreads = [_num(r.get("measured_spread_pct")) for r in rows]
    spreads = [s for s in spreads if s is not None]
    holds = [_num(r.get("hold_minutes")) for r in rows]
    holds = [h for h in holds if h is not None]
    gross_loss = abs(sum(losses))
    return {
        "trades": len(rows),
        "t1_before_sl_pct": (round(100.0 * len(t1) / (len(t1) + len(sl)), 1)
                             if (t1 or sl) else None),
        "t1": len(t1),
        "sl": len(sl),
        "win_pct": round(100.0 * len(wins) / len(rows), 1) if rows else None,
        "profit_factor": (round(sum(wins) / gross_loss, 2)
                          if gross_loss > 0 else None),
        "net_pnl": round(sum(pnls), 1),
        "avg_net_r": round(statistics.fmean(rs), 3) if rs else None,
        "median_net_r": round(statistics.median(rs), 3) if rs else None,
        "max_drawdown": _max_drawdown(pnls),
        "max_losing_streak": _losing_streak(pnls),
        "avg_hurdle_pct": round(statistics.fmean(hurdles), 3) if hurdles else None,
        "median_measured_spread_pct": (round(statistics.median(spreads), 3)
                                       if spreads else None),
        "median_hold_minutes": (round(statistics.median(holds), 1)
                                if holds else None),
        "avg_winner": round(statistics.fmean(wins), 1) if wins else None,
        "avg_loser": round(statistics.fmean(losses), 1) if losses else None,
    }


def attribution(rows: list[dict], arm: str,
                threshold: float | None = None) -> dict:
    """What one cutoff refused, split into money saved and money missed.

    Saved is the loss the arm did not take; missed is the profit it gave up. Both
    are reported, always, and the net is their difference — a filter that only
    ever showed its savings would be a sales pitch.
    """
    held = {id(r) for r in arm_rows(rows, arm, threshold)}
    pool = taken(rows)
    refused = [r for r in pool if id(r) not in held]
    refused_measured = [r for r in refused
                        if r.get("hurdle_status") == hurdle_mod.MEASURED]
    abstained = [r for r in refused
                 if r.get("hurdle_status") != hurdle_mod.MEASURED]
    saved = -sum(float(r["net_pnl"]) for r in refused_measured
                 if float(r["net_pnl"]) < 0)
    missed = sum(float(r["net_pnl"]) for r in refused_measured
                 if float(r["net_pnl"]) > 0)
    biggest = max((abs(float(r["net_pnl"])) for r in refused_measured), default=0.0)
    return {
        "arm": arm,
        "threshold_pct": threshold,
        "refused_trades": len(refused_measured),
        "abstained_unmeasured": len(abstained),
        "money_saved_by_refusing_high_hurdle_trades": round(saved, 1),
        "money_missed_by_refusing_them": round(missed, 1),
        "net_effect": round(saved - missed, 1),
        "largest_single_refused_abs_pnl": round(biggest, 1),
        "net_effect_excluding_largest": round(
            _net_effect_excluding_largest(refused_measured), 1),
    }


def _net_effect_excluding_largest(refused: list[dict]) -> float:
    """The same net effect with the single biggest refused trade removed.

    A filter whose whole benefit is one avoided disaster has not been shown to
    work; this column is what makes that visible instead of arguable.
    """
    if not refused:
        return 0.0
    ordered = sorted(refused, key=lambda r: abs(float(r["net_pnl"])),
                     reverse=True)[1:]
    saved = -sum(float(r["net_pnl"]) for r in ordered if float(r["net_pnl"]) < 0)
    missed = sum(float(r["net_pnl"]) for r in ordered if float(r["net_pnl"]) > 0)
    return saved - missed


def _group(rows: list[dict], key) -> dict:
    out: dict[str, dict] = {}
    for r in rows:
        k = key(r)
        if k is None:
            k = "UNKNOWN"
        out.setdefault(str(k), []).append(r)
    return {k: metrics(v) for k, v in sorted(out.items())}


def breakdowns(rows: list[dict], arm: str) -> dict:
    held = arm_rows(rows, arm)
    return {
        "by_instrument": _group(held, lambda r: r.get("instrument")),
        "by_option_type": _group(held, lambda r: r.get("option_type")),
        "by_time_of_day": _group(held, lambda r: time_bucket(r.get("ts"))),
    }


def coverage(rows: list[dict]) -> dict:
    """How much of the shadow is actually decidable on a measured book."""
    real = real_feed_rows(rows)
    total = len(real)
    meas = len(measured(real))
    resolved = len([r for r in real if r.get("resolved")])
    return {
        "opportunities": total,
        "rows_on_file": len(rows),
        "simulated_feed_rows_excluded": len(rows) - total,
        "measured_book": meas,
        "unmeasured_book": total - meas,
        "measured_pct": round(100.0 * meas / total, 1) if total else None,
        "engine_would_buy": len([r for r in real if r.get("engine_would_buy")]),
        "engine_refused": len([r for r in real if not r.get("engine_would_buy")]),
        "resolved_trades": resolved,
        "open_or_unresolved": total - resolved,
    }


def arm_report(rows: list[dict], arm: str,
               threshold: float | None = None) -> dict:
    held = arm_rows(rows, arm, threshold)
    block = metrics(held)
    block["arm"] = arm
    block["accepted"] = len(held)
    pool = taken(rows)
    block["signals"] = len(pool)
    block["rejected"] = len(pool) - len(held)
    if arm != EXISTING:
        block["attribution"] = attribution(rows, arm, threshold)
    return block


def sweep(rows: list[dict]) -> list[dict]:
    """Every cutoff between 3% and 5%, so the threshold can be discovered.

    No row here is a recommendation. The sweep exists to show whether the result
    is a plateau (a real effect) or a spike at one cutoff (a fitted number), and
    the last two columns show whether it survives dropping its best trade.
    """
    out = []
    baseline = metrics(arm_rows(rows, EXISTING))
    for t in hurdle_mod.SWEEP_PCT:
        held = arm_rows(rows, "SWEEP", t)
        block = metrics(held)
        att = attribution(rows, "SWEEP", t)
        out.append({
            "threshold_pct": t,
            "accepted": len(held),
            "rejected": att["refused_trades"],
            "abstained_unmeasured": att["abstained_unmeasured"],
            "net_pnl": block["net_pnl"],
            "vs_existing_net_pnl": round(
                block["net_pnl"] - baseline["net_pnl"], 1),
            "profit_factor": block["profit_factor"],
            "win_pct": block["win_pct"],
            "t1_before_sl_pct": block["t1_before_sl_pct"],
            "avg_net_r": block["avg_net_r"],
            "median_net_r": block["median_net_r"],
            "max_drawdown": block["max_drawdown"],
            "money_saved": att["money_saved_by_refusing_high_hurdle_trades"],
            "money_missed": att["money_missed_by_refusing_them"],
            "net_effect": att["net_effect"],
            "net_effect_excluding_largest": att["net_effect_excluding_largest"],
        })
    return out


def stability(rows: list[dict], threshold: float) -> dict:
    """First-half vs second-half of the shadow, chronologically.

    A cutoff that only works in the half it was chosen on has not been
    validated. With too few trades to split, this reports INSUFFICIENT_DATA
    instead of a number.
    """
    pool = sorted(taken(rows), key=lambda r: int(r.get("ts") or 0))
    if len(pool) < 4:
        return {"status": "INSUFFICIENT_DATA", "trades": len(pool),
                "threshold_pct": threshold}
    cut = len(pool) // 2
    halves = {}
    for name, subset in (("first_half", pool[:cut]), ("second_half", pool[cut:])):
        halves[name] = {
            "existing": metrics(subset),
            "shadow": metrics(arm_rows(subset, "SWEEP", threshold)),
            "attribution": attribution(subset, "SWEEP", threshold),
        }
    first = halves["first_half"]["attribution"]["net_effect"]
    second = halves["second_half"]["attribution"]["net_effect"]
    return {
        "status": "OK",
        "threshold_pct": threshold,
        "trades": len(pool),
        "halves": halves,
        "net_effect_first_half": first,
        "net_effect_second_half": second,
        "same_sign": bool((first > 0) == (second > 0)) if first and second else False,
    }


def daily(rows: list[dict]) -> list[dict]:
    """One block per IST session date, oldest first."""
    days: dict[str, list[dict]] = {}
    for r in rows:
        d = ist_date(r.get("ts"))
        if d:
            days.setdefault(d, []).append(r)
    out = []
    for day, subset in sorted(days.items()):
        out.append({
            "date": day,
            "coverage": coverage(subset),
            "arms": {
                EXISTING: arm_report(subset, EXISTING),
                SHADOW_5: arm_report(subset, SHADOW_5),
                SHADOW_3: arm_report(subset, SHADOW_3),
            },
        })
    return out


def build(rows: list[dict] | None = None) -> dict:
    """The whole shadow report: cumulative, daily, sweep, stability, coverage."""
    data = rows if rows is not None else store.rows()
    return {
        "vehicle": "OPTION",
        "futures_excluded": True,
        "futures_note": (
            "Futures are not recorded or filtered here: their measured hurdle was "
            "~0.05% and 16 of 20 legs still stopped out, so the problem there is "
            "signal quality, not transaction cost."
        ),
        "spread_source": "MEASURED_BID_ASK_ONLY",
        "coverage": coverage(data),
        "cumulative": {
            EXISTING: arm_report(data, EXISTING),
            SHADOW_5: arm_report(data, SHADOW_5),
            SHADOW_3: arm_report(data, SHADOW_3),
        },
        "breakdowns": {
            EXISTING: breakdowns(data, EXISTING),
            SHADOW_5: breakdowns(data, SHADOW_5),
            SHADOW_3: breakdowns(data, SHADOW_3),
        },
        "sweep": sweep(data),
        "stability": {
            f"{t:g}": stability(data, t) for t in hurdle_mod.GATES
        },
        "daily": daily(data),
    }
