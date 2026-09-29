"""Phase 34 §3 — the same exit arms, run on *real option premium* candles.

This is the module the chart demanded. Until now every exit claim was made on
underlying bars, because the store held 51 two-sided option quotes. The harvested
premium candles change that: a flip rule can now be run on the premium series
itself, exactly as the indicator on the chart is, and the resulting leg measured in
premium percent.

Two labels are attached to every number and neither is negotiable:

``MEASURED_TRADED_PRICE``
    The excursion, the peak, the timing. These come from executed trades in the
    contract's own candles.
``SPREAD_MODELLED``
    Anything net. A candle has no bid and no ask, so the round-trip spread is a
    *stated* grid — 0.5%, 1%, 2% of premium — and brokerage plus statutory charges
    come from the shared option cost model. A net premium number here is therefore
    a model, and the spread grid is reported beside it so the reader can pick.

Sample honesty: the reachable history is the life of contracts that are still
listed, so this is weeks, not years. Every result carries its contract count,
session count and trade count, and the verdict stays ``REQUIRES_MORE_DATA`` below
the frozen floors no matter how good the numbers look.
"""
from __future__ import annotations

import json
import os
import sqlite3

import numpy as np

from app.analysis import option_costs
from app.research.phase24 import data as p24data
from app.research.phase31.excursion import LONG
from app.research.phase34 import (
    MEASURED_TRADED_PRICE,
    MIN_BARS_PER_CONTRACT,
    REQUIRES_MORE_DATA,
    SPREAD_MODELLED,
)
from app.research.phase34 import adaptive, signals
from app.research.phase34 import store as p34store
from app.research.phase34.study import ARTEFACT_DIR, _stats

# Stated round-trip spread grid, as a percentage of premium.
SPREAD_GRID_PCT = (0.5, 1.0, 2.0)

# Floors below which a premium result is described but never graded.
MIN_CONTRACTS = 20
MIN_SESSIONS = 20
MIN_TRADES = 200

# Entry-premium bands, in rupees. Brokerage is charged per order, not per rupee, so
# the same 20-rupee ticket is a different percentage on a 5-rupee premium than on a
# 200-rupee one, and pooling them hides which band can pay its own costs at all.
PREMIUM_BANDS = ((0.0, 25.0), (25.0, 100.0), (100.0, float("inf")))


class PremiumSeries:
    """A single contract's premium candles, shaped like ``phase24.data.Series``.

    Only the attributes the exit walk touches are provided. Sessions are derived
    from the bar timestamps rather than assumed, so a contract with a missing day
    does not silently join two sessions into one.
    """

    __slots__ = ("instrument", "ts", "open", "high", "low", "close", "volume")

    def __init__(self, instrument: str, rows: list[tuple]) -> None:
        self.instrument = instrument
        self.ts = np.asarray([int(r[0]) for r in rows], dtype=np.int64)
        self.open = np.asarray([float(r[1]) for r in rows], dtype=np.float64)
        self.high = np.asarray([float(r[2]) for r in rows], dtype=np.float64)
        self.low = np.asarray([float(r[3]) for r in rows], dtype=np.float64)
        self.close = np.asarray([float(r[4]) for r in rows], dtype=np.float64)
        self.volume = np.asarray([float(r[5]) for r in rows], dtype=np.float64)

    def __len__(self) -> int:
        return int(self.ts.size)


def load_contract(con: sqlite3.Connection, token: str, symbol: str) -> PremiumSeries | None:
    rows = con.execute(
        "SELECT ts, open, high, low, close, volume FROM option_bars "
        "WHERE token = ? ORDER BY ts",
        (token,),
    ).fetchall()
    if len(rows) < MIN_BARS_PER_CONTRACT:
        return None
    clean = [r for r in rows if r[1] and r[2] and r[3] and r[4]]
    if len(clean) < MIN_BARS_PER_CONTRACT:
        return None
    return PremiumSeries(symbol, clean)


def charges_pct(entry: np.ndarray, lot: int = 75) -> np.ndarray:
    """Brokerage plus statutory charges as a percentage of premium, per round trip."""
    out = np.zeros(entry.size, dtype=np.float64)
    q = max(1, int(lot))
    for i in range(entry.size):
        e = float(entry[i])
        if not np.isfinite(e) or e <= 0:
            continue
        out[i] = 100.0 * option_costs.charges(e, e, q).total / q / e
    return out


def contracts_with_bars(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        "SELECT c.token, c.symbol, c.root, c.option_type, c.strike, c.expiry, "
        "COUNT(b.ts) AS bars FROM contracts c JOIN option_bars b ON b.token = c.token "
        "GROUP BY c.token HAVING bars >= ? ORDER BY c.expiry, c.strike",
        (MIN_BARS_PER_CONTRACT,),
    ).fetchall()
    return [
        {"token": r[0], "symbol": r[1], "root": r[2], "option_type": r[3],
         "strike": float(r[4]), "expiry": r[5], "bars": int(r[6])}
        for r in rows
    ]


def study(*, out_dir: str | None = None, progress: bool = True) -> dict:
    """Every frozen exit arm, on every harvested contract, in premium percent."""
    con = p34store.connect()
    try:
        contracts = contracts_with_bars(con)
        per_arm: dict[tuple[str, float], dict] = {}
        sessions: set[int] = set()
        for c in contracts:
            s = load_contract(con, c["token"], c["symbol"])
            if s is None:
                continue
            sessions.update({int(t) // 86_400 for t in s.ts})
            # A bought option is the vehicle, so only the long side of the premium
            # is taken: the flip rule buys the contract and later sells it.
            for family in signals.FAMILIES:
                sign = signals.sign_of(s, family)
                ent = signals.entries(sign, LONG)
                gross_cost = np.zeros(len(s), dtype=np.float64)
                for kind, param in adaptive.arms():
                    res = adaptive.resolve(
                        s, LONG, ent, kind=kind, param=param, cost=gross_cost,
                        flip_sign=sign,
                    )
                    if not res["n"]:
                        continue
                    key = (family, kind, param)
                    slot = per_arm.setdefault(
                        key,
                        {"family": family, "exit_kind": kind, "exit_param": param,
                         "gross_pct": [], "entry": [], "hold": [], "peak": [],
                         "reasons": [], "contracts": 0},
                    )
                    slot["gross_pct"].append(res["net_pct"])  # cost was zero
                    slot["hold"].append(res["hold_minutes"])
                    slot["peak"].append(res["peak_pct"])
                    slot["reasons"].extend(res["reasons"])
                    slot["contracts"] += 1
                    slot["entry"].append(res["entry_price"])
            if progress:
                print(f"  {c['symbol']}: {c['bars']} premium bars", flush=True)

        rows: list[dict] = []
        for (family, kind, param), slot in sorted(per_arm.items()):
            gross = np.concatenate(slot["gross_pct"])
            entry = np.concatenate(slot["entry"])
            hold = np.concatenate(slot["hold"])
            peak = np.concatenate(slot["peak"])
            ch = charges_pct(entry)
            row = {
                "family": family,
                "exit_kind": kind,
                "exit_param": param,
                "evidence": MEASURED_TRADED_PRICE,
                "contracts": slot["contracts"],
                "trades": int(gross.size),
                "gross_premium_pct": _stats(gross),
                "median_hold_minutes": int(np.median(hold)) if hold.size else None,
                "mean_peak_premium_pct": round(float(np.nanmean(peak)), 4)
                if peak.size else None,
                "exit_reason_mix": {
                    r: round(slot["reasons"].count(r) / max(len(slot["reasons"]), 1), 4)
                    for r in sorted(set(slot["reasons"]))
                },
                "charges_pct_median": round(float(np.median(ch)), 4)
                if ch.size else None,
                "net_by_spread": {
                    f"spread_{sp:g}pct": {
                        "evidence": SPREAD_MODELLED,
                        **_stats(gross - sp - ch),
                    }
                    for sp in SPREAD_GRID_PCT
                },
                "by_premium_band": [
                    {
                        "entry_premium_from": lo,
                        "entry_premium_to": None if hi == float("inf") else hi,
                        "trades": int(np.count_nonzero(band)),
                        "charges_pct_median": round(float(np.median(ch[band])), 4)
                        if band.any() else None,
                        "gross_premium_pct": _stats(gross[band]),
                        "net_at_1pct_spread": {
                            "evidence": SPREAD_MODELLED,
                            **_stats(gross[band] - 1.0 - ch[band]),
                        },
                    }
                    for lo, hi in PREMIUM_BANDS
                    for band in [(entry >= lo) & (entry < hi)]
                ],
            }
            rows.append(row)

        enough = (
            len(contracts) >= MIN_CONTRACTS
            and len(sessions) >= MIN_SESSIONS
            and any(r["trades"] >= MIN_TRADES for r in rows)
        )
        out = {
            "contracts_used": len(contracts),
            "sessions": len(sessions),
            "spread_grid_pct": list(SPREAD_GRID_PCT),
            "status": MEASURED_TRADED_PRICE if enough else REQUIRES_MORE_DATA,
            "floors": {"contracts": MIN_CONTRACTS, "sessions": MIN_SESSIONS,
                       "trades": MIN_TRADES},
            "rows": rows,
            "production_changed": False,
        }
        target = out_dir or p24data._resolve(ARTEFACT_DIR)
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, "p34_premium_exit.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(out, fh, indent=2, default=str)
        return out
    finally:
        con.close()
