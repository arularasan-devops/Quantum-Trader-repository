"""Quick Scalp Engine agreement tracker + comprehensive performance log.

For ANALYSIS ONLY. When the scalp engine issues an advisory for an instrument we
open a lightweight record and, on every following tick:

  * track how far the option premium actually travelled (max favourable / min
    adverse excursion, whether the fixed target or the stop was reached);
  * apply the dedicated scalp EXIT model — fixed target, a move to BREAKEVEN
    after partial progress, and a hard TIME-STOP in candles;
  * record whether the FROZEN confirmation engine later agreed on the SAME side
    (for objective comparison across engines).

Finished records are appended to their own JSONL log (SEPARATE from Early
Momentum / Early-Early / confirmation) so win rate, average hold time, profit
factor and false-signal rate can be compared objectively after paper trading.

This NEVER places an order and NEVER touches the frozen engine. A logging
failure must never break a live tick.
"""
from __future__ import annotations

import json as _json
import os as _os
import time as _time

from app.config import settings
from app.models import Candle, OptionQuote, ScalpSignal

_LOG_NAME = "scalp_signals.jsonl"
_MAX_OPEN_SECONDS = 2 * 3600


def _log_path() -> str:
    return _os.path.join(settings.data_dir, _LOG_NAME)


def _leg_premium(chain: list[OptionQuote], symbol: str | None) -> float | None:
    if not symbol:
        return None
    for q in chain:
        if q.symbol == symbol and q.premium and q.premium > 0:
            return float(q.premium)
    return None


def _candles_since(candles: list[Candle], open_ctime: int | None) -> int:
    if open_ctime is None:
        return 0
    return sum(1 for c in candles if c.time > open_ctime)


def _open_record(instrument: str, sc: ScalpSignal, candles: list[Candle], now: int) -> dict | None:
    plan = sc.plan
    if plan is None or not plan.option_symbol or not sc.entry_hint:
        return None
    entry = float(sc.entry_hint)
    if entry <= 0:
        return None
    return {
        "engine": "scalp",
        "instrument": instrument,
        "side": sc.side.value if sc.side else None,
        "setup": sc.setup,
        "option_symbol": plan.option_symbol,
        "ts_open": int(now),
        "open_ctime": candles[-1].time if candles else None,
        "entry_premium": round(entry, 2),
        "stop_loss": plan.stop_loss,
        "target1": plan.target1,
        "target_points": sc.target_points,
        "breakeven_at": sc.breakeven_at,
        "time_stop_candles": sc.time_stop_candles,
        "reward_risk": sc.reward_risk,
        "probability": sc.probability,
        "condition_count": sc.condition_count,
        "recommended_action": sc.recommended_action,
        "max_premium": round(entry, 2),
        "min_premium": round(entry, 2),
        "last_premium": round(entry, 2),
        "ts_max": int(now),
        "target_hit": False,
        "stop_hit": False,
        "breakeven_armed": False,
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
    out["win"] = reason == "TARGET"
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
    sc: ScalpSignal,
    chain: list[OptionQuote],
    candles: list[Candle],
    frozen_buy_same_side: bool,
    now: int,
    rec: dict | None,
) -> dict | None:
    """Advance the scalp tracker one tick. Returns the open record (or None once
    closed & written). Pure/best-effort — never raises."""
    try:
        if rec is None:
            if sc.active:
                return _open_record(instrument, sc, candles, now)
            return None

        entry = rec["entry_premium"]
        prem = _leg_premium(chain, rec["option_symbol"])
        if prem is not None:
            rec["last_premium"] = round(prem, 2)
            if prem > rec["max_premium"]:
                rec["max_premium"] = round(prem, 2)
                rec["ts_max"] = int(now)
            if prem < rec["min_premium"]:
                rec["min_premium"] = round(prem, 2)
            if rec["target1"] and rec["max_premium"] >= rec["target1"]:
                rec["target_hit"] = True
            # Breakeven exit model: once premium travels past the breakeven mark,
            # raise the effective stop to entry (protect the scalp).
            if rec.get("breakeven_at") and rec["max_premium"] >= rec["breakeven_at"]:
                rec["breakeven_armed"] = True
            eff_stop = rec["stop_loss"]
            if rec.get("breakeven_armed") and eff_stop is not None and entry > eff_stop:
                eff_stop = entry
            if eff_stop is not None and prem <= eff_stop:
                rec["stop_hit"] = True
                rec["_stop_was_breakeven"] = bool(rec.get("breakeven_armed"))

        # Agreement tracking (for cross-engine comparison).
        if rec.get("agreement") == "PENDING" and frozen_buy_same_side:
            rec["agreement"] = "CONFIRMED"
            rec["confirm_delay_candles"] = _candles_since(candles, rec.get("open_ctime"))
            rec["ts_confirm"] = int(now)

        # Resolve — target first, then stop/breakeven, then the candle time-stop.
        reason: str | None = None
        if rec["target_hit"]:
            reason = "TARGET"
        elif rec["stop_hit"]:
            reason = "BREAKEVEN" if rec.get("_stop_was_breakeven") else "STOP"
        else:
            held = _candles_since(candles, rec.get("open_ctime"))
            tsc = rec.get("time_stop_candles") or settings.scalp_max_hold_candles
            if held >= tsc or int(now) - rec["ts_open"] >= _MAX_OPEN_SECONDS:
                reason = "TIME"

        if reason is not None:
            rec.pop("_stop_was_breakeven", None)
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
    """Per-instrument scalp performance: win rate, average hold time, profit
    factor and false-signal rate. Read-only, data-gated (empty until real
    signals log). No fabricated statistics."""
    rows = _read_all()
    by_inst: dict[str, dict] = {}
    wins = 0
    losses = 0
    gross_win = 0.0
    gross_loss = 0.0
    hold_sum = 0
    hold_n = 0
    for r in rows:
        inst = r.get("instrument") or "?"
        agg = by_inst.setdefault(
            inst,
            {
                "instrument": inst,
                "signals": 0,
                "wins": 0,
                "stopped": 0,
                "timed_out": 0,
                "sum_final_pts": 0.0,
                "sum_hold": 0,
                "best_pts": None,
            },
        )
        agg["signals"] += 1
        fp = r.get("final_points")
        won = bool(r.get("win"))
        if won:
            agg["wins"] += 1
            wins += 1
        reason = r.get("close_reason")
        if reason in ("STOP", "BREAKEVEN"):
            agg["stopped"] += 1
        elif reason == "TIME":
            agg["timed_out"] += 1
        if fp is not None:
            agg["sum_final_pts"] += fp
            if agg["best_pts"] is None or fp > agg["best_pts"]:
                agg["best_pts"] = fp
            if fp > 0:
                gross_win += fp
            else:
                gross_loss += -fp
                if not won:
                    losses += 1
        d = r.get("duration_sec")
        if isinstance(d, int):
            agg["sum_hold"] += d
            hold_sum += d
            hold_n += 1

    instruments = []
    for agg in by_inst.values():
        n = max(1, agg["signals"])
        instruments.append(
            {
                "instrument": agg["instrument"],
                "signals": agg["signals"],
                "win_rate": round(agg["wins"] / n * 100.0, 0),
                "stop_rate": round(agg["stopped"] / n * 100.0, 0),
                "timeout_rate": round(agg["timed_out"] / n * 100.0, 0),
                "avg_final_pts": round(agg["sum_final_pts"] / n, 1),
                "avg_hold_min": round(agg["sum_hold"] / n / 60.0, 1),
                "best_pts": agg["best_pts"],
            }
        )
    instruments.sort(key=lambda x: x["signals"], reverse=True)

    total = len(rows)
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else None
    recent = sorted(rows, key=lambda r: r.get("ts_close", 0), reverse=True)[:limit]
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / max(1, total) * 100.0, 0),
        "profit_factor": profit_factor,
        "avg_hold_min": round(hold_sum / hold_n / 60.0, 1) if hold_n else None,
        "false_signal_rate": round(losses / max(1, total) * 100.0, 0),
        "instruments": instruments,
        "recent": recent,
        "as_of": int(_time.time()),
    }
