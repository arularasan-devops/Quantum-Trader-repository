"""Phase 30 §10 — CE and PE on real captured books, or ``UNMEASURED``.

There is no five-year option book on disk and premium behaviour cannot be derived
from underlying movement: two identical index moves pay differently depending on
implied volatility, time to expiry and where the strike sat. So this module never
models a premium. It measures option outcomes only where the engine actually
captured a real two-sided book, over that captured window alone, and everything
outside it stays ``UNMEASURED``.

Frozen rules:

* entry is the **ask** at the first real book at or after the decision, exit is
  the **bid** at the first real book at or after the underlying trade's own exit
  time. Configured slippage is paid on both legs, brokerage and statutory charges
  come from the shared option cost model, and the spread is paid by construction
  (buy at ask, sell at bid) rather than charged twice;
* the strike is the one nearest the underlying price at the decision — chosen by
  distance, before any outcome is known;
* CE and PE are resolved for the **same** decision, so §19's "which patterns
  behave differently on CE vs PE" is a paired comparison rather than two
  unrelated samples;
* a decision with no book inside the tolerance is dropped and counted, never
  filled at a nearby price.
"""
from __future__ import annotations

import sqlite3

import numpy as np

from app.analysis import option_costs
from app.config import settings
from app.market.instruments import get_spec
from app.research.phase24 import data as p24data
from app.research.phase24 import features as p24feat
from app.research.phase24 import outcomes as p24out
from app.research.phase24.data import Series
from app.research.phase25 import books
from app.research.phase30 import UNMEASURED, patterns

# How close a real book has to be to the moment it is meant to price.
ENTRY_TOLERANCE_SEC = 120
EXIT_TOLERANCE_SEC = 600
# Below this an option row is reported as a sample, not as a result.
MIN_PAIRED_DECISIONS = 30

CE, PE = "CE", "PE"


def _load_captured_series(instrument: str, db_path: str | None = None) -> Series | None:
    """Captured 1-minute candles for one instrument, from the live store."""
    path = db_path or books.store_path()
    try:
        con = p24data.connect_readonly(path)
    except sqlite3.Error:
        return None
    try:
        rows = con.execute(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE instrument = ? ORDER BY ts",
            (instrument.upper(),),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if len(rows) < 200:
        return None
    return Series(instrument.upper(), [
        {"time": int(r[0]), "open": r[1], "high": r[2], "low": r[3],
         "close": r[4], "volume": r[5] or 0.0}
        for r in rows
    ])


def _nearest_strike(book: dict[str, dict], spot: float, otype: str) -> dict | None:
    best, best_d = None, None
    for leg in book.values():
        if str(leg.get("option_type") or "").upper() != otype:
            continue
        d = abs(float(leg["strike"]) - spot)
        if best_d is None or d < best_d:
            best, best_d = leg, d
    return best


def _first_at_or_after(ts: np.ndarray, want: int, tol: int) -> int:
    """Index of the first timestamp in ``[want, want+tol]``, or -1."""
    if ts.size == 0:
        return -1
    k = int(np.searchsorted(ts, want, side="left"))
    if k >= ts.size or ts[k] > want + tol:
        return -1
    return k


def measure(instrument: str, *, db_path: str | None = None) -> dict:
    """Per-pattern CE and PE outcomes inside the captured window."""
    out: dict = {
        "instrument": instrument.upper(),
        "status": UNMEASURED,
        "note": "",
        "coverage": {},
        "patterns": {},
    }
    series = _load_captured_series(instrument, db_path)
    if series is None:
        out["note"] = (
            "no captured 1-minute candles for this instrument in the live store, "
            "so no option decision timestamp could be formed"
        )
        return out
    try:
        chain = books.load_chain(instrument, db_path=db_path)
    except sqlite3.Error as exc:
        out["note"] = (
            "the stored option books could not be read: "
            f"{p24data.cause(exc)}. This is an unmeasured store, not an empty one"
        )
        return out

    out["coverage"] = books.eligibility(chain)
    if chain.snapshots == 0:
        out["note"] = (
            "no real-broker option snapshot is stored for this instrument; CE/PE "
            "stays UNMEASURED and no premium is modelled"
        )
        return out

    feat = p24feat.build(series)
    atr = feat["atr"]
    det = patterns.detect(series, atr)
    n = len(series)
    sess = (series.ts + 19_800) // 86_400
    last_bar = np.empty(n, dtype=np.int64)
    start = 0
    for k in range(1, n + 1):
        if k == n or sess[k] != sess[start]:
            last_bar[start:k] = k - 1
            start = k

    lot = int(get_spec(instrument).lot_size or 1)
    slip = max(0.0, float(settings.ai_slippage_pct)) / 100.0

    dropped_no_entry_book = 0
    dropped_no_exit_book = 0
    measured_rows = 0

    for name in patterns.NAMES:
        bars = np.flatnonzero(det[name] & np.isfinite(atr) & (atr > 0))
        bars = bars[(last_bar[bars] - bars) >= 30]
        if bars.size == 0:
            continue
        side = np.full(bars.size, patterns.SIDES[name] or p24out.LONG, dtype=np.int8)
        u = p24out.resolve(
            instrument, series.high, series.low, series.open, series.ts,
            bars, side, atr, last_bar[bars],
        )
        fill_i = np.minimum(bars + 1, n - 1)
        exit_i = np.minimum(fill_i + np.maximum(u.bars_held, 0), n - 1)

        rows: dict[str, list[dict]] = {CE: [], PE: []}
        for j in range(bars.size):
            decision_ts = int(series.ts[bars[j]] + 60)
            k = _first_at_or_after(chain.ts, decision_ts, ENTRY_TOLERANCE_SEC)
            if k < 0:
                dropped_no_entry_book += 1
                continue
            spot = float(series.close[bars[j]])
            exit_ts = int(series.ts[exit_i[j]] + 60)
            for otype in (CE, PE):
                leg = _nearest_strike(chain.legs[k], spot, otype)
                if leg is None:
                    dropped_no_entry_book += 1
                    continue
                q = chain.quotes.get(str(leg.get("symbol") or ""))
                if q is None or len(q) < 2:
                    dropped_no_exit_book += 1
                    continue
                e = _first_at_or_after(q.ts, decision_ts, ENTRY_TOLERANCE_SEC)
                x = _first_at_or_after(q.ts, exit_ts, EXIT_TOLERANCE_SEC)
                if e < 0 or x < 0 or x <= e:
                    dropped_no_exit_book += 1
                    continue
                entry = float(q.ask[e]) * (1.0 + slip)
                exit_px = float(q.bid[x]) * (1.0 - slip)
                if entry <= 0:
                    continue
                ch = option_costs.charges(entry, exit_px, lot)
                cost_points = ch.total / lot
                net = exit_px - entry - cost_points
                rows[otype].append({
                    "net_points": net,
                    "net_pct_of_premium": 100.0 * net / entry,
                    "win": net > 0,
                    "entry": entry,
                    "exit": exit_px,
                    "strike": float(leg["strike"]),
                    "moneyness_points": float(leg["strike"]) - spot,
                    "underlying_t1": bool(u.t1_before_sl[j]),
                    "hold_sec": int(q.ts[x] - q.ts[e]),
                    "cost_points": cost_points,
                })
                measured_rows += 1

        if not rows[CE] and not rows[PE]:
            continue
        out["patterns"][name] = {
            otype: _summarise(rows[otype]) for otype in (CE, PE)
        }
        paired = min(len(rows[CE]), len(rows[PE]))
        out["patterns"][name]["paired_decisions"] = paired
        out["patterns"][name]["status"] = (
            "MEASURED_SMALL_SAMPLE" if paired < MIN_PAIRED_DECISIONS else "MEASURED"
        )

    out["status"] = "MEASURED_CAPTURED_WINDOW" if measured_rows else UNMEASURED
    out["dropped_no_entry_book"] = dropped_no_entry_book
    out["dropped_no_exit_book"] = dropped_no_exit_book
    out["measured_rows"] = measured_rows
    out["window"] = {
        "first_ts": int(chain.ts.min()) if chain.snapshots else 0,
        "last_ts": int(chain.ts.max()) if chain.snapshots else 0,
        "sessions": chain.sessions,
    }
    out["note"] = (
        "measured only inside the captured book window; every figure outside it "
        "is UNMEASURED and no premium is modelled. The window is weeks long, so "
        "these rows are a sample, not a chronological validation"
        if measured_rows else
        "no decision could be priced on a real two-sided book at both entry and "
        "exit; CE/PE stays UNMEASURED"
    )
    return out


def _summarise(rows: list[dict]) -> dict:
    if not rows:
        return {"decisions": 0, "status": UNMEASURED}
    net = np.array([r["net_points"] for r in rows], dtype=np.float64)
    pct = np.array([r["net_pct_of_premium"] for r in rows], dtype=np.float64)
    wins = net[net > 0]
    losses = net[net < 0]
    return {
        "decisions": len(rows),
        "win_pct": round(100.0 * float((net > 0).mean()), 2),
        "avg_net_points": round(float(net.mean()), 3),
        "median_net_points": round(float(np.median(net)), 3),
        "avg_net_pct_of_premium": round(float(pct.mean()), 2),
        "total_net_points": round(float(net.sum()), 2),
        "profit_factor": (
            round(float(wins.sum() / -losses.sum()), 3) if losses.size else None
        ),
        "avg_cost_points": round(
            float(np.mean([r["cost_points"] for r in rows])), 3
        ),
        "avg_hold_sec": int(np.mean([r["hold_sec"] for r in rows])),
        "avg_entry_premium": round(float(np.mean([r["entry"] for r in rows])), 2),
    }
