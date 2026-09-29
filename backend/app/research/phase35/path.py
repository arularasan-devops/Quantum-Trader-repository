"""Phase 35 §8/§9 — the forward path of a paper leg, and its giveback.

A path here is made only of measured quotes for the *same* contract at later
timestamps. Nothing is interpolated and no gap is filled: if the capture saw the
23900 CE at 10:04:12 and again at 10:09:40, the 15-minute horizon is answered
from whatever was actually measured at or before 15 minutes, and the row records
which timestamp answered it. A horizon with no sample is UNMEASURED, not the
previous value carried forward — a forward-filled path turns a gap in coverage
into a flat stretch of P&L, which is the most flattering possible lie about an
illiquid contract.

The giveback block (§9) is the part earlier phases kept finding and never
isolated: 56-60% of instants that went profitable came back to entry and 50-56%
turned net negative after having been profitable. Those two numbers are what
this module computes per leg, so the aggregate can be split by vehicle, premium
band, hold window and setup rather than quoted as one average.

**Diagnostic only.** Nothing here creates, arms or recommends an exit rule. §9
is explicit about that, and the earlier adaptive-exit study (Phase 34, 96
hypotheses, none validated) is the reason: measuring giveback is not the same as
having something that captures it.
"""
from __future__ import annotations

import bisect
import datetime as dt

from app.research.phase35 import (
    FUTURES,
    HORIZONS,
    MEASURED_EXECUTABLE,
    NO_PATH,
    SESSION_CLOSE,
    SHORT,
    UNMEASURED,
    book,
)

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# Frozen target ladder, written down before any outcome is measured.
#
# Options are laddered in premium percent (+10/+20/+30%), the same levels Phase
# 31 measured time-to-reach on, so the two studies are comparable. Futures are
# laddered in multiples of their own round-trip cost, because +10% on an index
# future is not a trade anyone waits for and a percentage ladder would make T1
# unreachable by construction.
OPTION_TARGETS_PCT: tuple[float, float, float] = (10.0, 20.0, 30.0)
FUTURES_TARGET_COST_MULTIPLES: tuple[float, float, float] = (3.0, 5.0, 8.0)


def session_date(ts: float) -> str:
    return dt.datetime.fromtimestamp(float(ts), _IST).strftime("%Y-%m-%d")


def _session_start_ts(ts: float) -> float:
    """First instant of the IST day holding ``ts``."""
    start = dt.datetime.fromtimestamp(float(ts), _IST)
    return start.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _session_end_ts(ts: float) -> float:
    """First instant of the next IST day — the exclusive upper bound of a session.

    Used to bound the forward-path query in SQL instead of reading every later
    quote for the contract and discarding the out-of-session ones in Python.
    """
    start = dt.datetime.fromtimestamp(float(ts), _IST)
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight + dt.timedelta(days=1)).timestamp()


def session_bounds(ts: float) -> tuple[float, float]:
    """[start, end) of the IST session holding ``ts``, for SQL-bounded reads."""
    return _session_start_ts(ts), _session_end_ts(ts)


def forward_samples(con, *, symbol: str, vehicle: str, after_ts: float) -> list[dict]:
    """Every measured quote for one contract after the entry, same session only.

    Same session because an overnight gap is not something a paper leg held to
    "120 minutes" experienced; Phase 34 had to remove exactly this bug from its
    resolver, where a fallback exit could land on the next session's first bar.

    The path is reconstructed from later *observations* of the same contract
    symbol, not from a separate path feed: the capture layer quotes every vehicle
    at every decision instant, so a contract that keeps appearing keeps being
    priced. The consequence is honest and worth stating — a contract quoted once
    and never again has no path and resolves to ``NO_FORWARD_PATH_CAPTURED``
    rather than to an inferred exit. Matching on symbol (not strike) is what keeps
    two contracts that share a strike across expiries out of each other's paths.
    """
    rows = con.execute(
        "SELECT ts, bid, ask, traded, evidence FROM raw_quote "
        "WHERE symbol = ? AND vehicle = ? AND ts > ? AND ts < ? ORDER BY ts",
        (symbol, vehicle, float(after_ts), _session_end_ts(after_ts)),
    ).fetchall()
    return [dict(r) for r in rows]


class ForwardCache:
    """One read per contract-session, reused by every leg on that contract.

    A leg's forward path is a suffix of its contract's session quotes, so a
    per-leg query re-reads the same rows once per leg and recomputes the same
    exit fill for each of them. On a session with thousands of decision instants
    that is quadratic twice over, which is what made a rebuild on a real store
    stop finishing. The cache reads each contract-session once, precomputes the
    exit fill, and hands out the suffix after the entry.

    The fill depends on the exit side, which is fixed by vehicle and direction,
    so it is part of the key rather than recomputed: a long and a short leg on
    the same futures contract exit on opposite sides of the book. The rows and
    the arithmetic are identical to the uncached path.
    """

    def __init__(self) -> None:
        self._sessions: dict[tuple, tuple[list[float], list[dict]]] = {}
        self._day: str | None = None

    def samples(
        self, con, *, symbol: str | None, vehicle: str, direction: str,
        after_ts: float,
    ) -> list[dict]:
        if not symbol:
            return []
        after = float(after_ts)
        day = session_date(after)
        if day != self._day:
            # Legs are rebuilt in time order and a path never leaves its session,
            # so nothing cached for an earlier day can be needed again. Dropping
            # it keeps the cache the size of one session rather than the store.
            self._sessions.clear()
            self._day = day
        key = (symbol, vehicle, direction, day)
        cached = self._sessions.get(key)
        if cached is None:
            rows = con.execute(
                "SELECT ts, bid, ask, traded, evidence FROM raw_quote "
                "WHERE symbol = ? AND vehicle = ? AND ts >= ? AND ts < ? "
                "ORDER BY ts",
                (symbol, vehicle, _session_start_ts(after), _session_end_ts(after)),
            ).fetchall()
            samples = []
            for r in rows:
                s = dict(r)
                s["fill"] = book.exit_fill(s, vehicle=vehicle, direction=direction)
                samples.append(s)
            cached = ([s["ts"] for s in samples], samples)
            self._sessions[key] = cached
        stamps, samples = cached
        return samples[bisect.bisect_right(stamps, after):]


def _targets(vehicle: str, *, cost_pct: float | None) -> tuple[float, float, float]:
    if vehicle != FUTURES:
        return OPTION_TARGETS_PCT
    base = cost_pct if isinstance(cost_pct, (int, float)) and cost_pct > 0 else None
    if base is None:
        return (0.0, 0.0, 0.0)
    return tuple(round(base * m, 4) for m in FUTURES_TARGET_COST_MULTIPLES)


def targets_pct(vehicle: str, *, cost_pct: float | None) -> tuple[float, float, float]:
    """The frozen T1/T2/T3 ladder for one leg, in percent of its entry price.

    Public because a leg costed after the fact has to be graded against the same
    ladder the original pass used, and for futures that ladder is a function of
    the round-trip cost the leg was refused at the time.
    """
    return _targets(vehicle, cost_pct=cost_pct)


def resolve(
    *,
    entry_price: float,
    entry_ts: float,
    vehicle: str,
    direction: str,
    samples: list[dict],
    cost_points: float | None,
) -> dict:
    """Path, horizon table and giveback for one leg.

    Every horizon row carries the gross and net move, the running MFE and MAE,
    the three target flags and the giveback so far. ``net`` is ``None`` wherever
    the cost could not be charged, because a gross-only net figure is the number
    that made the Flow book look like a small loss when it was a large one.
    """
    if entry_price <= 0:
        return {"evidence": UNMEASURED, "reason": "entry price not executable",
                "horizons": {}, "giveback": {}}
    if not samples:
        return {"evidence": UNMEASURED, "reason": NO_PATH, "horizons": {},
                "giveback": {}}

    cost_pct = (
        None if cost_points is None else round(100.0 * cost_points / entry_price, 4)
    )
    t1, t2, t3 = _targets(vehicle, cost_pct=cost_pct)

    running_mfe = 0.0
    running_mae = 0.0
    hit = [False, False, False]
    peak = 0.0
    peak_ts = entry_ts
    trough_ts = entry_ts
    last_gross = 0.0
    last_ts = entry_ts
    last_price: float | None = None
    last_side: str | None = None
    executable_all = True

    # One pass over the samples, snapshotting the running state at each horizon.
    horizons: dict[str, dict] = {}
    pending = list(HORIZONS)
    # The sign is fixed by vehicle and direction for the whole leg, so it is
    # resolved once here and the move is computed inline. The expression is the
    # one in book.gross_pct, in the same order, so the values are identical —
    # this loop runs once per forward sample of every leg and the call overhead
    # was the largest single cost of a rebuild.
    sign = -1.0 if (vehicle == FUTURES and direction == SHORT) else 1.0
    for s in samples:
        fill = s.get("fill") or book.exit_fill(s, vehicle=vehicle, direction=direction)
        px = fill["price"]
        if px is None:
            continue
        if fill["evidence"] != MEASURED_EXECUTABLE:
            executable_all = False
        ts = float(s["ts"])
        g = round(100.0 * sign * (float(px) - entry_price) / entry_price, 4)
        if g > running_mfe:
            running_mfe = g
        if g < running_mae:
            running_mae, trough_ts = g, ts
        if g > peak:
            peak, peak_ts = g, ts
        for i, level in enumerate((t1, t2, t3)):
            if level > 0 and running_mfe >= level:
                hit[i] = True
        last_gross, last_ts = g, ts
        last_price, last_side = float(px), fill["side"]
        elapsed_min = (ts - entry_ts) / 60.0
        while pending and elapsed_min >= pending[0]:
            h = pending.pop(0)
            horizons[str(h)] = _snapshot(
                g, cost_points, entry_price, running_mfe, running_mae, hit, peak,
                executable_all,
            )
    # SESSION_CLOSE is the last measured quote of the entry session, which is
    # what a paper leg held to the close would actually have exited on.
    horizons[SESSION_CLOSE] = _snapshot(
        last_gross, cost_points, entry_price, running_mfe, running_mae, hit, peak,
        executable_all,
    )

    returned_to_entry = peak > 0 and last_gross <= 0
    net_last = book.net_pct(last_gross, cost_points, entry_price)
    turned_negative = peak > 0 and net_last is not None and net_last < 0
    giveback = {
        "peak_pct": round(peak, 4),
        "time_to_peak_min": round((peak_ts - entry_ts) / 60.0, 2),
        "final_pct": round(last_gross, 4),
        "max_giveback_pct": round(max(0.0, peak - last_gross), 4),
        "pct_of_mfe_given_back": (
            round(100.0 * max(0.0, peak - last_gross) / peak, 2) if peak > 0 else None
        ),
        "returned_to_entry": returned_to_entry,
        "turned_negative_after_profitable": turned_negative,
        "mae_pct": round(running_mae, 4),
        # Which side of the trade arrived first. This is what separates "the idea
        # was wrong" from "the instant was wrong": a leg that goes adverse first
        # and only later clears its target was entered too early, not misread.
        "adverse_first": bool(running_mae < 0 and trough_ts < peak_ts),
        "hold_minutes": round((last_ts - entry_ts) / 60.0, 2),
    }
    return {
        "evidence": MEASURED_EXECUTABLE if executable_all else "SPREAD_MODELLED",
        "reason": None,
        "samples": len(samples),
        "targets_pct": {"t1": t1, "t2": t2, "t3": t3},
        "cost_pct": cost_pct,
        "horizons": horizons,
        "giveback": giveback,
        "final_gross_pct": round(last_gross, 4),
        "final_net_pct": net_last,
        "exit_ts": last_ts,
        "exit_price": last_price,
        "exit_side": last_side,
    }


def _snapshot(gross: float, cost_points: float | None, entry: float, mfe: float,
              mae: float, hit: list[bool], peak: float,
              executable: bool) -> dict:
    return {
        "gross_pct": round(gross, 4),
        "net_pct": book.net_pct(gross, cost_points, entry),
        "mfe_pct": round(mfe, 4),
        "mae_pct": round(mae, 4),
        "t1": hit[0],
        "t2": hit[1],
        "t3": hit[2],
        "giveback_pct": round(max(0.0, peak - gross), 4),
        "evidence": MEASURED_EXECUTABLE if executable else "SPREAD_MODELLED",
    }
