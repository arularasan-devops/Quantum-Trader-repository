"""Phase 31 §3 — what the tool's own recorded calls actually returned.

This is the only *measured* premium evidence in the store: real recorded entries
and exits on real option symbols. Every number carries two limits rather than
having them mentioned once:

* the sample is small, so a percentile describes recorded history and is not a
  forecast;
* each row was cut short by whatever exit rule was live at the time, so it
  measures what was **taken**, not what was **available**. A modest realized
  figure is as much a statement about the exit as about the market.

Rows are classified before anything is averaged. The journal contains manual
panel entries whose "exit" is a repeated constant rather than a fill, and futures
rows sit in the same table as options. Averaging those together would produce a
confident-looking percentage from bookkeeping artefacts, so each class is counted
and reported, and only engine-resolved option fills feed the headline figure.
"""
from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict

import numpy as np

from app.research.phase24 import data

JOURNAL_DB = "data/history.db"
RESEARCH_DB = "data/research.db"

LIVE_MODES = ("live", "paper")
PCTILES = (10, 25, 50, 75, 90)

# An exit price that repeats across many rows and more than one symbol is a panel
# placeholder, not a fill. Detected from the data rather than hardcoded, so a new
# placeholder value is caught without a code change.
PLACEHOLDER_MIN_ROWS = 5
PLACEHOLDER_MIN_SYMBOLS = 2

# Exit reasons written by the engine's own exit path. Anything else is a manual
# panel action, which tells us about the operator, not the market.
ENGINE_REASONS = (
    "STOP LOSS", "TARGET POINTS", "TARGET 1 lock", "TARGET 2 lock",
    "TARGET 3 lock", "T3+ RATCHET lock", "PRE-T1 PEAK TRAIL",
    "BREAKEVEN RATCHET", "GIVEBACK TRAIL", "SESSION CLOSE",
    "flow faded/reversed",
)

# Classes, in the order a row is tested against them.
DUPLICATE = "DUPLICATE_ROW"
PLACEHOLDER = "PLACEHOLDER_EXIT_PRICE"
NO_FILL = "NO_FILL_RECORDED"
FUTURES = "FUTURES_ROW"
MANUAL = "MANUAL_EXIT_NOT_ENGINE"
USABLE = "ENGINE_RESOLVED_OPTION_FILL"

# Below this the realized half cannot describe a distribution and says so.
MIN_USABLE_TRADES = 30


class Trade:
    __slots__ = ("instrument", "symbol", "side", "entry", "exit", "minutes",
                 "reason", "mode", "confidence", "ts", "pnl", "klass")

    def __init__(self, **kw) -> None:
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def pct(self) -> float | None:
        if not self.entry or self.exit is None:
            return None
        return 100.0 * (float(self.exit) - float(self.entry)) / float(self.entry)


def _rows(path: str, sql: str) -> list[tuple]:
    try:
        con = data.connect_readonly(data._resolve(path))
    except sqlite3.Error:
        return []
    try:
        return list(con.execute(sql))
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _placeholders(rows: list[Trade]) -> tuple[set[float], set[tuple]]:
    """Exit prices and entry/exit pairs that are bookkeeping, not fills.

    Two independent signatures, both data-driven: one price repeated across
    several symbols (the panel's last-shown premium written as an exit), and one
    entry/exit *pair* repeated many times (a demo fill re-saved by the manual
    path). Either would otherwise dominate a percentage.
    """
    per_value: dict[float, set[str]] = defaultdict(set)
    counts: Counter = Counter()
    pairs: Counter = Counter()
    for t in rows:
        if t.exit is None:
            continue
        counts[float(t.exit)] += 1
        per_value[float(t.exit)].add(str(t.symbol))
        if t.entry is not None:
            pairs[(float(t.entry), float(t.exit))] += 1
    prices = {
        v for v, n in counts.items()
        if n >= PLACEHOLDER_MIN_ROWS
        and len(per_value[v]) >= PLACEHOLDER_MIN_SYMBOLS
    }
    repeated = {p for p, n in pairs.items() if n >= PLACEHOLDER_MIN_ROWS}
    return prices, repeated


def _is_futures(symbol: str | None, side: str | None) -> bool:
    sym = (symbol or "").upper()
    return "FUT" in sym or not (side or "").strip()


def load() -> tuple[list[Trade], dict]:
    """Every recorded call, each labelled with why it is or is not usable."""
    raw: list[Trade] = []
    for (inst, ts, sym, otype, entry, exit_, pnl, mins, mode, reason,
         conf) in _rows(
        JOURNAL_DB,
        "select instrument, ts, option, option_type, entry, exit, net_pnl,"
        " holding_minutes, mode, exit_reason, confidence from trade_journal",
    ):
        if mode not in LIVE_MODES:
            continue
        raw.append(Trade(
            instrument=inst, symbol=sym, side=otype, ts=ts,
            entry=None if entry is None else float(entry),
            exit=None if exit_ is None else float(exit_),
            minutes=None if mins is None else float(mins),
            reason=reason, mode=mode, confidence=conf, pnl=pnl,
        ))
    for (inst, src, sym, otype, ets, xts, entry, exit_, pnl, mins,
         reason) in _rows(
        RESEARCH_DB,
        "select instrument, source, option_symbol, option_type, entry_ts,"
        " exit_ts, entry, exit, pnl, duration_min, exit_reason"
        " from trade_history",
    ):
        if src != "paper_live":
            continue
        raw.append(Trade(
            instrument=inst, symbol=sym, side=otype, ts=xts or ets,
            entry=None if entry is None else float(entry),
            exit=None if exit_ is None else float(exit_),
            minutes=None if mins is None else float(mins),
            reason=reason, mode="paper", confidence=None, pnl=pnl,
        ))

    placeholders, repeated_pairs = _placeholders(raw)
    seen: set[tuple] = set()
    counts: Counter = Counter()
    for t in raw:
        key = (t.ts, t.instrument, t.symbol, t.entry, t.exit, t.reason)
        if key in seen:
            t.klass = DUPLICATE
        elif t.entry in (None, 0) or t.exit is None:
            t.klass = NO_FILL
        elif (float(t.exit) in placeholders
              or (float(t.entry), float(t.exit)) in repeated_pairs):
            t.klass = PLACEHOLDER
        elif _is_futures(t.symbol, t.side):
            t.klass = FUTURES
        elif (t.reason or "") not in ENGINE_REASONS:
            t.klass = MANUAL
        else:
            t.klass = USABLE
        seen.add(key)
        counts[t.klass] += 1
    return raw, {
        "by_class": dict(counts),
        "placeholder_exit_prices": sorted(placeholders),
        "repeated_entry_exit_pairs": sorted(repeated_pairs),
    }


def _dist(vals) -> dict:
    a = np.array([v for v in vals if v is not None], dtype=np.float64)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return {"n": 0}
    qs = np.percentile(a, PCTILES)
    return {
        "n": int(a.size),
        "mean": round(float(a.mean()), 3),
        **{f"p{p}": round(float(q), 3) for p, q in zip(PCTILES, qs)},
        "best": round(float(a.max()), 3),
        "worst": round(float(a.min()), 3),
        "positive_pct": round(100.0 * float((a > 0).sum()) / a.size, 2),
    }


def _group(trades: list[Trade], key) -> list[dict]:
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        buckets[str(key(t))].append(t)
    return [
        {
            "group": name,
            "trades": len(rows),
            "premium_pct": _dist([t.pct for t in rows]),
            "hold_minutes": _dist([t.minutes for t in rows]),
        }
        for name, rows in sorted(buckets.items(), key=lambda kv: -len(kv[1]))
    ]


def summary() -> dict:
    """Realized premium percentage, and how long it took, on real recorded calls."""
    raw, prov = load()
    usable = [t for t in raw if t.klass == USABLE]
    futures = [t for t in raw if t.klass == FUTURES]
    manual = [t for t in raw if t.klass == MANUAL]
    return {
        "rows_seen": len(raw),
        "classification": prov,
        "usable_option_trades": len(usable),
        "sufficient": len(usable) >= MIN_USABLE_TRADES,
        "min_required": MIN_USABLE_TRADES,
        "option_premium_pct": _dist([t.pct for t in usable]),
        "option_hold_minutes": _dist([t.minutes for t in usable]),
        "option_winners_premium_pct": _dist(
            [t.pct for t in usable if (t.pct or 0) > 0]),
        "option_losers_premium_pct": _dist(
            [t.pct for t in usable if (t.pct or 0) <= 0]),
        "by_exit_reason": _group(usable, lambda t: t.reason or "UNKNOWN"),
        "by_side": _group(usable, lambda t: t.side or "UNKNOWN"),
        "futures_rows_kept_separate": len(futures),
        "futures_pct_of_price": _dist([t.pct for t in futures]),
        "futures_hold_minutes": _dist([t.minutes for t in futures]),
        "manual_rows_excluded": len(manual),
        "manual_premium_pct_for_reference": _dist([t.pct for t in manual]),
        "premium_to_strike_ratio": premium_ratio(
            [t for t in raw if t.klass in (USABLE, MANUAL)]),
        "limits": [
            "a percentile here describes recorded history; it is not a forecast",
            "every row is truncated by the exit rule live at the time, so this "
            "is what was taken, not what was available",
            "placeholder-priced and manual-panel rows are classified out rather "
            "than averaged in, and futures rows are reported separately because "
            "a percentage of a futures price is not a premium percentage",
        ],
    }


def premium_ratio(trades: list[Trade]) -> dict:
    """Entry premium as a fraction of the strike, per instrument.

    Measured from real recorded calls, and used later as the *stated* input to the
    premium mapping, so the arithmetic bridge is anchored on premiums this account
    actually paid rather than a textbook number.
    """
    by: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        strike = strike_from_symbol(t.symbol)
        if strike and t.entry:
            by[t.instrument].append(t.entry / strike)
    out = {}
    for inst, vals in sorted(by.items()):
        a = np.array(vals, dtype=np.float64)
        out[inst] = {
            "n": int(a.size),
            "median_premium_over_strike_pct": round(
                float(np.median(a)) * 100.0, 4),
        }
    return out


def strike_from_symbol(symbol: str | None) -> float | None:
    """Strike embedded in a broker option symbol, or None if not parseable."""
    if not symbol:
        return None
    tail = symbol.upper().strip()
    if not (tail.endswith("CE") or tail.endswith("PE")):
        return None
    core = tail[:-2]
    run = ""
    while core and core[-1].isdigit():
        run = core[-1] + run
        core = core[:-1]
    try:
        return float(run) if run else None
    except ValueError:
        return None
