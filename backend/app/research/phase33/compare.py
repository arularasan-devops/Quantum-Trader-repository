"""Phase 33 §3/§12 — the engine's pool against the raw board pool.

This is the only part of the phase that can say something about the engine rather
than about the market, and it is built to be able to embarrass it:

``SOURCE_A``
    every decision the engine actually recorded, with its own label — ``BUY``,
    ``WAIT``, ``AVOID``, ``HOLD`` — and its own score, regime and geometry;
``SOURCE_B``
    the raw option-board observations, read independently of whether a BUY was
    ever produced. Source B is *not* filtered by the engine's opinion, which is
    the whole point: it is the pool the engine was choosing from.

The two pools are then compared at the same timestamps on **measured** data only:

* the option premium leg is priced ask-in/bid-out and only where a real two-sided
  quote exists at both instants. §4 forbids midpoint, LTP, an estimated spread, a
  nearby timestamp or a family median, so a board row without a real quote is
  ``UNMEASURED`` and is counted, not priced;
* the underlying leg is measured on real one-minute bars, and is labelled as
  underlying movement — never as an option outcome.

Everything here is ``COUNTERFACTUAL`` by construction: "the board offered a
better row" is hindsight about one recorded window, not a rule. The report says so
next to every number.
"""
from __future__ import annotations

import json
import sqlite3

import numpy as np

from app.research.phase24 import data as p24data
from app.research.phase33 import COUNTERFACTUAL, MEASURED, REQUIRES_MORE_DATA, UNMEASURED

RESEARCH_DB = "data/research.db"

ENGINE_SQL = (
    "SELECT instrument, ts, signal, confidence, trade_score, opportunity, risk, "
    "trend, market_regime, premium_quality, entry, stop, target1, target2, "
    "target3, reasoning FROM signal_snapshots ORDER BY ts"
)
BOARD_SQL = (
    "SELECT instrument, ts, strike, option_type, close, bid, ask, iv, delta, oi, "
    "volume, source FROM mcx_options ORDER BY ts"
)

BUY = "BUY"


def _rows(sql: str) -> list[tuple] | None:
    path = p24data._resolve(RESEARCH_DB)
    try:
        con = p24data.connect_readonly(path)
    except sqlite3.Error:
        return None
    try:
        return list(con.execute(sql))
    except sqlite3.Error:
        return None
    finally:
        con.close()


def engine_pool() -> list[dict]:
    """Source A: every recorded engine decision, with all metadata it carries."""
    rows = _rows(ENGINE_SQL) or []
    out: list[dict] = []
    for r in rows:
        setup = None
        try:
            reasons = json.loads(r[15]) if r[15] else []
        except json.JSONDecodeError:
            reasons = []
        for text in reasons if isinstance(reasons, list) else []:
            if isinstance(text, str) and "entry_trigger" in text:
                setup = text
        out.append({
            "source": "SOURCE_A",
            "instrument": r[0],
            "ts": int(r[1]),
            "signal": r[2],
            "confidence": r[3],
            "trade_score": r[4],
            "opportunity": r[5],
            "risk": r[6],
            "trend": r[7],
            "regime": r[8],
            "premium_quality": r[9],
            "entry": r[10],
            "stop": r[11],
            "t1": r[12],
            "t2": r[13],
            "t3": r[14],
            "setup_note": setup,
            "reason_count": len(reasons) if isinstance(reasons, list) else 0,
        })
    return out


def board_pool() -> list[dict]:
    """Source B: raw board observations, independent of any engine decision."""
    rows = _rows(BOARD_SQL) or []
    out: list[dict] = []
    for r in rows:
        bid, ask = r[5], r[6]
        two_sided = (
            bid is not None and ask is not None
            and float(bid) > 0 and float(ask) > float(bid)
        )
        out.append({
            "source": "SOURCE_B",
            "instrument": r[0],
            "ts": int(r[1]),
            "strike": r[2],
            "option_type": r[3],
            "premium": r[4],
            "bid": bid,
            "ask": ask,
            "spread_pct": (
                round(100.0 * (float(ask) - float(bid)) / float(ask), 6)
                if two_sided else None
            ),
            "iv": r[7],
            "delta": r[8],
            "oi": r[9],
            "volume": r[10],
            "provenance": r[11] or "UNKNOWN",
            "pricing": MEASURED if two_sided else UNMEASURED,
        })
    return out


def _minute(ts: int) -> int:
    return ts - (ts % 60)


def coverage(engine: list[dict], board: list[dict]) -> dict:
    """What each pool contains, and how much of it can be priced at all."""
    buys = [r for r in engine if r["signal"] == BUY]
    priceable = [r for r in board if r["pricing"] == MEASURED]
    board_minutes = {_minute(r["ts"]) for r in board}
    buy_minutes = {_minute(r["ts"]) for r in buys}
    return {
        "engine_rows": len(engine),
        "engine_buys": len(buys),
        "engine_labels": {
            lbl: sum(1 for r in engine if r["signal"] == lbl)
            for lbl in sorted({r["signal"] for r in engine if r["signal"]})
        },
        "board_rows": len(board),
        "board_rows_priceable": len(priceable),
        "board_minutes": len(board_minutes),
        "board_minutes_with_engine_buy": len(board_minutes & buy_minutes),
        "board_minutes_without_engine_buy": len(board_minutes - buy_minutes),
        "premium_outcome_basis": (
            MEASURED if len(priceable) >= 2 else UNMEASURED
        ),
        "note": (
            "a board minute without an engine BUY is an opportunity the engine did "
            "not take; whether it was worth taking can only be answered where a "
            "real two-sided quote exists at both the entry and the exit instant"
        ),
    }


def underlying_diagnosis(
    engine: list[dict], board: list[dict], paths: dict[str, dict[int, object]],
    costs: dict[str, np.ndarray], horizon: int,
) -> dict:
    """Movement after engine BUYs against movement after board minutes.

    Both cohorts are measured on the same one-minute bars with the same fill rule,
    so the comparison is like for like. It answers "did the engine pick the better
    minutes" on the underlying, and says nothing about premiums.
    """
    out: dict = {
        "basis": MEASURED,
        "vehicle": "UNDERLYING_ONLY",
        "interpretation": COUNTERFACTUAL,
        "horizon": horizon,
        "instruments": {},
    }
    for inst, by_side in paths.items():
        buy_ts = {_minute(r["ts"]) for r in engine
                  if r["signal"] == BUY and r["instrument"] == inst}
        board_ts = {_minute(r["ts"]) for r in board if r["instrument"] == inst}
        if not buy_ts and not board_ts:
            continue
        row: dict = {"engine_buy_minutes": len(buy_ts),
                     "board_minutes": len(board_ts)}
        for side, p in by_side.items():
            ts_min = p.ts_minute
            for name, wanted in (("engine_buys", buy_ts),
                                 ("board_not_taken", board_ts - buy_ts)):
                if wanted:
                    keys = np.fromiter(wanted, dtype=np.int64, count=len(wanted))
                    sel = np.isin(ts_min, keys)
                else:
                    sel = np.zeros(ts_min.size, dtype=bool)
                sel &= p.eligible
                ret = p.ret[horizon][sel].astype(np.float64)
                c = costs[inst][sel].astype(np.float64)
                ok = np.isfinite(ret) & np.isfinite(c)
                net = ret[ok] - c[ok]
                key = f"{'LONG' if side > 0 else 'SHORT'}_{name}"
                row[key] = {
                    "n": int(net.size),
                    "net_expectancy_pct": (
                        round(float(np.mean(net)), 6) if net.size else None
                    ),
                    "status": (
                        MEASURED if net.size >= 30 else REQUIRES_MORE_DATA
                    ),
                }
        out["instruments"][inst] = row
    return out
