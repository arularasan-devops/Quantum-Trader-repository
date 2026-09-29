"""Phase 43 §0 — what the five-year store can actually answer, per family.

This runs before any measurement, and it is the module that decides which of
the four requested families are real. The reason it exists first is that three
of the four ask for something the dataset may not contain: family A asks for
spread and liquidity conditions, family B asks for BANKNIFTY, family C asks for
options. A study that quietly substitutes a modelled spread for a quoted one
answers a different question and reports it under the original name.

So each family gets one of two verdicts here, with the counts behind it:
measurable on this data, or UNMEASURED with the specific missing field named.
"""
from __future__ import annotations

import os
import sqlite3

import numpy as np

from app.research import phase43
from app.research.phase24 import data as p24data

BANKNIFTY = "BANKNIFTY"


def _series_row(instrument: str) -> dict | None:
    s = p24data.load_series(instrument)
    if s is None or len(s) == 0:
        return None
    sessions = int(np.unique((s.ts + 19_800) // 86_400).size)
    return {
        "instrument": instrument,
        "bars": len(s),
        "sessions": sessions,
        "first_ts": int(s.ts[0]),
        "last_ts": int(s.ts[-1]),
        "granularity": "ONE_MINUTE",
        "fields": ["open", "high", "low", "close", "volume"],
        "has_quotes": False,
    }


def _captured_candles() -> dict[str, dict]:
    """Row counts per instrument in the captured (non-five-year) candle store.

    Read only to explain *why* an instrument is excluded. A busy live writer is
    tolerated by the shared read handle; a failure here leaves the dict empty
    and the caller reports the instrument as unmeasured rather than as absent.
    """
    path = p24data._resolve(p24data.HISTORY_DB)
    out: dict[str, dict] = {}
    if not os.path.exists(path):
        return out
    try:
        con = p24data.connect_readonly(path)
    except sqlite3.Error:
        return out
    try:
        rows = con.execute(
            "SELECT instrument, COUNT(*), MIN(ts), MAX(ts) "
            "FROM candles GROUP BY instrument"
        ).fetchall()
    except sqlite3.Error:
        return out
    finally:
        con.close()
    for name, bars, lo, hi in rows:
        out[str(name).upper()] = {
            "bars": int(bars),
            "first_ts": int(lo or 0),
            "last_ts": int(hi or 0),
        }
    return out


def _overlap_bars(a: str, b: str) -> int:
    """Timestamps present in both series — the sample a pair trade could use."""
    sa = p24data.load_series(a)
    sb = p24data.load_series(b)
    if sa is None or sb is None:
        return 0
    return int(np.intersect1d(sa.ts, sb.ts).size)


def map_families() -> dict:
    """One answerability verdict per requested family, with its evidence."""
    usable = [r for r in (_series_row(i) for i in sorted(p24data.BACKTEST_FILES))
              if r is not None]
    names = tuple(r["instrument"] for r in usable)
    captured = _captured_candles()
    bn = captured.get(BANKNIFTY, {})

    families: list[dict] = [
        {
            "family": phase43.FAMILY_COST,
            "measurable": bool(names),
            "on": list(names),
            "measurable_terms": [
                "expected move (trailing ATR) divided by modelled round-trip "
                "cost, both known at the decision bar's close",
                "trailing range relative to the same cost",
            ],
            "unmeasured_terms": [
                {
                    "term": "liquidity and spread conditions",
                    "status": phase43.UNMEASURED,
                    "reason": phase43.NO_QUOTED_DEPTH,
                }
            ],
        },
        {
            "family": phase43.FAMILY_RELVAL,
            "measurable": _overlap_bars(*names[:2]) > 0 if len(names) >= 2 else False,
            "on": list(names[:2]),
            "overlapping_bars": _overlap_bars(*names[:2]) if len(names) >= 2 else 0,
            "measurable_terms": [
                "divergence and reversion between the two instruments that do "
                "have five-year history, notional-neutral and beta-adjusted"
            ],
            "unmeasured_terms": [
                {
                    "term": "NIFTY versus BANKNIFTY",
                    "status": phase43.UNMEASURED,
                    "reason": phase43.NO_BANKNIFTY_HISTORY,
                    "banknifty_captured_bars": int(bn.get("bars") or 0),
                    "banknifty_first_ts": int(bn.get("first_ts") or 0),
                    "banknifty_last_ts": int(bn.get("last_ts") or 0),
                },
                {
                    "term": "index versus its own futures basis",
                    "status": phase43.UNMEASURED,
                    "reason": (
                        "the five-year series carry one price per instrument; "
                        "no separate cash-and-futures pair with contract "
                        "identity and expiry exists in this store, so a basis "
                        "relationship has nothing to be measured between"
                    ),
                },
            ],
        },
        {
            "family": phase43.FAMILY_VEHICLE,
            "measurable": bool(names),
            "on": list(names),
            "measurable_terms": [
                "which futures vehicle pays better after its own charges, "
                "measured on the same mechanism at the same decision times"
            ],
            "unmeasured_terms": [
                {
                    "term": "futures versus CE/PE after realistic cost",
                    "status": phase43.UNMEASURED,
                    "reason": phase43.NO_OPTION_HISTORY,
                },
                {
                    "term": "midpoint execution",
                    "status": "REFUSED_BY_DESIGN",
                    "reason": (
                        "no midpoint is available and none is assumed; the "
                        "futures fill is the next bar's open with slippage and "
                        "charges on both legs"
                    ),
                },
            ],
        },
        {
            "family": phase43.FAMILY_REGIME,
            "measurable": bool(names),
            "on": list(names),
            "measurable_terms": [
                "volatility regime, trend versus range, time of day, gap and "
                "opening state, expansion versus contraction — all from the "
                "candle series, all computed backwards only",
            ],
            "unmeasured_terms": [],
        },
    ]
    return {
        "five_year_series": usable,
        "instruments_with_five_year_history": list(names),
        "captured_only_instruments": sorted(
            k for k in captured if k not in names
        ),
        "families": families,
        "option_books": {
            "status": phase43.UNMEASURED,
            "reason": phase43.NO_OPTION_HISTORY,
        },
    }
