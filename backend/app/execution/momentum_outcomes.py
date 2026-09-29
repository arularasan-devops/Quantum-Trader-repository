"""Early-Momentum outcome recorder — for ANALYSIS ONLY.

When the Early Momentum engine issues an EARLY BUY (Stage 3) for an instrument,
we open a lightweight outcome record for that option leg and, on every following
tick, track how far the premium actually travelled (max favourable / min adverse
excursion, which targets or the stop were reached). When the leg is done (stop
hit, Target-3 reached, the early thesis regresses, or a time-stop) the finished
record is appended to a per-instrument JSONL log so the user can later review
"when Early Momentum said BUY, how much did each instrument actually reach?".

This NEVER places an order and NEVER touches the frozen engine — it is pure,
best-effort observability. A logging failure must never break a live tick.
"""
from __future__ import annotations

import json as _json
import os as _os
import time as _time

from app.config import settings
from app.models import EarlyMomentum, OptionQuote

_LOG_NAME = "momentum_outcomes.jsonl"
# A live EARLY-BUY record is force-closed after this long without resolution, so
# a stale leg can't stay "open" forever (intraday horizon).
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


def _open_record(instrument: str, em: EarlyMomentum, now: int) -> dict | None:
    plan = em.plan
    if plan is None or not plan.option_symbol or not em.entry_hint:
        return None
    entry = float(em.entry_hint)
    if entry <= 0:
        return None
    return {
        "instrument": instrument,
        "side": em.side.value if em.side else None,
        "option_symbol": plan.option_symbol,
        "ts_open": int(now),
        "entry_premium": round(entry, 2),
        "stop_loss": plan.stop_loss,
        "target1": plan.target1,
        "target2": plan.target2,
        "target3": plan.target3,
        "score": em.score,
        "regime": em.regime,
        "max_premium": round(entry, 2),
        "min_premium": round(entry, 2),
        "last_premium": round(entry, 2),
        "ts_max": int(now),
        "t1_hit": False,
        "t2_hit": False,
        "t3_hit": False,
        "stop_hit": False,
    }


def _finalize(rec: dict, reason: str, now: int) -> dict:
    entry = rec["entry_premium"]
    peak = rec["max_premium"]
    max_gain = round(peak - entry, 2)
    rec_out = dict(rec)
    rec_out["ts_close"] = int(now)
    rec_out["close_reason"] = reason
    rec_out["duration_sec"] = int(now) - rec["ts_open"]
    rec_out["max_gain_points"] = max_gain
    rec_out["max_gain_pct"] = round(max_gain / entry * 100.0, 1) if entry > 0 else None
    rec_out["final_premium"] = rec["last_premium"]
    fp = rec["last_premium"]
    rec_out["final_points"] = round(fp - entry, 2) if fp is not None else None
    return rec_out


def _append(rec: dict) -> None:
    try:
        _os.makedirs(settings.data_dir, exist_ok=True)
        with open(_log_path(), "a", encoding="utf-8") as fh:
            fh.write(_json.dumps(rec) + "\n")
    except Exception:
        pass


def update(
    instrument: str,
    em: EarlyMomentum,
    chain: list[OptionQuote],
    now: int,
    rec: dict | None,
) -> dict | None:
    """Advance the outcome tracker one tick. `rec` is the currently-open record
    for this instrument (or None). Returns the new open record (or None once it
    is closed and written). Pure/best-effort — never raises."""
    try:
        # No open record yet → open one only when a fresh EARLY BUY is live.
        if rec is None:
            if em.active and em.stage_num == 3:
                return _open_record(instrument, em, now)
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

        # Decide whether the leg is resolved.
        reason: str | None = None
        if rec["stop_hit"]:
            reason = "STOP"
        elif rec["t3_hit"]:
            reason = "TARGET3"
        elif em.stage_num <= 1:  # early thesis gone (dropped to Building/No-Setup)
            reason = "STAGE_EXIT"
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
    """Per-instrument aggregates + the most recent finished EARLY-BUY outcomes,
    for the analysis view. Read-only; data-gated (empty until real signals log)."""
    rows = _read_all()
    by_inst: dict[str, dict] = {}
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
                "avg_max_pct": round(agg["sum_max_pct"] / n, 1),
                "best_pct": agg["best_pct"],
            }
        )
    instruments.sort(key=lambda x: x["signals"], reverse=True)

    recent = sorted(rows, key=lambda r: r.get("ts_close", 0), reverse=True)[:limit]
    return {
        "total": len(rows),
        "instruments": instruments,
        "recent": recent,
        "as_of": int(_time.time()),
    }
