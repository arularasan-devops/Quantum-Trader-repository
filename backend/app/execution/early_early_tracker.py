"""Early-Early (Stage 2.5) agreement tracker + comprehensive performance log.

For ANALYSIS ONLY. When the Early-Early add-on issues a Stage-2.5 advisory for
an instrument we open a lightweight record and, on every following tick:

  * track how far the option premium actually travelled (max favourable / min
    adverse excursion, which targets / the stop were reached);
  * record whether the FROZEN confirmation engine later agreed on the SAME side,
    and — if so — the confirmation DELAY in candles (Early-Early → frozen BUY);
  * mark the signal REJECTED if it invalidated (stop / evidence collapse) before
    the frozen engine ever confirmed.

When the leg resolves the finished record is appended to its own JSONL log,
kept SEPARATE from the Early Momentum and confirmation logs so the three engines
can be compared objectively after paper trading.

This NEVER places an order and NEVER touches the frozen engine — pure,
best-effort observability. A logging failure must never break a live tick.
"""
from __future__ import annotations

import json as _json
import os as _os
import time as _time

from app.config import settings
from app.models import Candle, EarlyEarly, OptionQuote

_LOG_NAME = "early_early_signals.jsonl"
_MAX_OPEN_SECONDS = 3 * 3600


def _log_path() -> str:
    return _os.path.join(settings.data_dir, _LOG_NAME)


def _leg_premium(chain: list[OptionQuote], symbol: str | None) -> float | None:
    if not symbol:
        return None
    for q in chain:
        if q.symbol == symbol and q.premium and q.premium > 0:
            return float(q.premium)
    return None


def _candles_since(candles: list[Candle], open_ctime: int | None) -> int | None:
    if open_ctime is None:
        return None
    return sum(1 for c in candles if c.time > open_ctime)


def _open_record(instrument: str, ee: EarlyEarly, candles: list[Candle], now: int) -> dict | None:
    plan = ee.plan
    if plan is None or not plan.option_symbol or not ee.entry_hint:
        return None
    entry = float(ee.entry_hint)
    if entry <= 0:
        return None
    return {
        "engine": "early_early",
        "instrument": instrument,
        "side": ee.side.value if ee.side else None,
        "option_symbol": plan.option_symbol,
        "ts_open": int(now),
        "open_ctime": candles[-1].time if candles else None,
        "entry_premium": round(entry, 2),
        "stop_loss": plan.stop_loss,
        "target1": plan.target1,
        "target2": plan.target2,
        "target3": plan.target3,
        "probability": ee.probability,
        "evidence_count": ee.evidence_count,
        "reward_risk": ee.reward_risk,
        "room_atr": ee.room_atr,
        "recommended_action": ee.recommended_action,
        "max_premium": round(entry, 2),
        "min_premium": round(entry, 2),
        "last_premium": round(entry, 2),
        "ts_max": int(now),
        "t1_hit": False,
        "t2_hit": False,
        "t3_hit": False,
        "stop_hit": False,
        "agreement": "PENDING",
        "confirm_delay_candles": None,
        "ts_confirm": None,
    }


def _finalize(rec: dict, reason: str, now: int) -> dict:
    entry = rec["entry_premium"]
    peak = rec["max_premium"]
    max_gain = round(peak - entry, 2)
    out = dict(rec)
    out["ts_close"] = int(now)
    out["close_reason"] = reason
    out["duration_sec"] = int(now) - rec["ts_open"]
    out["max_gain_points"] = max_gain
    out["max_gain_pct"] = round(max_gain / entry * 100.0, 1) if entry > 0 else None
    fp = rec["last_premium"]
    out["final_premium"] = fp
    out["final_points"] = round(fp - entry, 2) if fp is not None else None
    if rec.get("agreement") == "PENDING":
        out["agreement"] = "REJECTED"
    return out


def _append(rec: dict) -> None:
    try:
        _os.makedirs(settings.data_dir, exist_ok=True)
        with open(_log_path(), "a", encoding="utf-8") as fh:
            fh.write(_json.dumps(rec) + "\n")
    except Exception:
        pass


def update(
    instrument: str,
    ee: EarlyEarly,
    chain: list[OptionQuote],
    candles: list[Candle],
    frozen_buy_same_side: bool,
    now: int,
    rec: dict | None,
) -> dict | None:
    """Advance the Early-Early tracker one tick. `frozen_buy_same_side` is True
    when the FROZEN engine currently signals BUY on the SAME side as the tracked
    Early-Early leg. Returns the open record (or None once closed & written).
    Pure/best-effort — never raises."""
    try:
        if rec is None:
            if ee.active:
                return _open_record(instrument, ee, candles, now)
            return None

        prem = _leg_premium(chain, rec["option_symbol"])
        if prem is not None:
            rec["last_premium"] = round(prem, 2)
            if prem > rec["max_premium"]:
                rec["max_premium"] = round(prem, 2)
                rec["ts_max"] = int(now)
            if prem < rec["min_premium"]:
                rec["min_premium"] = round(prem, 2)
            if rec["target1"] and rec["max_premium"] >= rec["target1"]:
                rec["t1_hit"] = True
            if rec["target2"] and rec["max_premium"] >= rec["target2"]:
                rec["t2_hit"] = True
            if rec["target3"] and rec["max_premium"] >= rec["target3"]:
                rec["t3_hit"] = True
            if rec["stop_loss"] and prem <= rec["stop_loss"]:
                rec["stop_hit"] = True

        # Agreement tracking: did the frozen engine confirm the same side?
        if rec.get("agreement") == "PENDING" and frozen_buy_same_side:
            rec["agreement"] = "CONFIRMED"
            rec["confirm_delay_candles"] = _candles_since(candles, rec.get("open_ctime"))
            rec["ts_confirm"] = int(now)

        # Resolve the leg.
        reason: str | None = None
        if rec["stop_hit"]:
            reason = "STOP"
        elif rec["t3_hit"]:
            reason = "TARGET3"
        elif int(now) - rec["ts_open"] >= _MAX_OPEN_SECONDS:
            reason = "TIME"

        if reason is not None:
            _append(_finalize(rec, reason, now))
            return None
        return rec
    except Exception:
        return rec


def _read_all() -> list[dict]:
    path = _log_path()
    if not _os.path.exists(path):
        return []
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(_json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out


def summary(limit: int = 200) -> dict:
    """Per-instrument Early-Early performance + agreement aggregates, plus the
    most recent finished signals. Read-only; data-gated (empty until real
    signals log). No fabricated statistics."""
    rows = _read_all()
    by_inst: dict[str, dict] = {}
    confirmed = 0
    rejected = 0
    delay_sum = 0
    delay_n = 0
    for r in rows:
        inst = r.get("instrument") or "?"
        agg = by_inst.setdefault(
            inst,
            {
                "instrument": inst,
                "signals": 0,
                "t1": 0,
                "t2": 0,
                "t3": 0,
                "stopped": 0,
                "confirmed": 0,
                "rejected": 0,
                "sum_max_pct": 0.0,
                "best_pct": None,
            },
        )
        agg["signals"] += 1
        if r.get("t1_hit"):
            agg["t1"] += 1
        if r.get("t2_hit"):
            agg["t2"] += 1
        if r.get("t3_hit"):
            agg["t3"] += 1
        if r.get("stop_hit"):
            agg["stopped"] += 1
        if r.get("agreement") == "CONFIRMED":
            agg["confirmed"] += 1
            confirmed += 1
            d = r.get("confirm_delay_candles")
            if isinstance(d, int):
                delay_sum += d
                delay_n += 1
        elif r.get("agreement") == "REJECTED":
            agg["rejected"] += 1
            rejected += 1
        mp = r.get("max_gain_pct")
        if mp is not None:
            agg["sum_max_pct"] += mp
            if agg["best_pct"] is None or mp > agg["best_pct"]:
                agg["best_pct"] = mp

    instruments = []
    for agg in by_inst.values():
        n = max(1, agg["signals"])
        instruments.append(
            {
                "instrument": agg["instrument"],
                "signals": agg["signals"],
                "t1_rate": round(agg["t1"] / n * 100.0, 0),
                "t2_rate": round(agg["t2"] / n * 100.0, 0),
                "t3_rate": round(agg["t3"] / n * 100.0, 0),
                "stop_rate": round(agg["stopped"] / n * 100.0, 0),
                "confirm_rate": round(agg["confirmed"] / n * 100.0, 0),
                "reject_rate": round(agg["rejected"] / n * 100.0, 0),
                "avg_max_pct": round(agg["sum_max_pct"] / n, 1),
                "best_pct": agg["best_pct"],
            }
        )
    instruments.sort(key=lambda x: x["signals"], reverse=True)

    total = len(rows)
    recent = sorted(rows, key=lambda r: r.get("ts_close", 0), reverse=True)[:limit]
    return {
        "total": total,
        "confirmed": confirmed,
        "rejected": rejected,
        "confirm_rate": round(confirmed / max(1, total) * 100.0, 0),
        "avg_confirm_delay_candles": round(delay_sum / delay_n, 1) if delay_n else None,
        "instruments": instruments,
        "recent": recent,
        "as_of": int(_time.time()),
    }
