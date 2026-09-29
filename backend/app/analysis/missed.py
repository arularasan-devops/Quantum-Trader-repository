"""Missed-opportunity recorder — measurement only, never a trading input.

Every WAIT is an opinion the system is never held to. This module holds it to
it: when the engine refuses a fresh entry, the leg it *would* have bought is
followed for a fixed window and the best move it made is written down, together
with the gate that refused it.

That produces the one number the tuning argument has been missing — "when
CHASE/OI/ADX blocked a trade, what did the trade go on to do?" — and it produces
it for the gate that actually did the blocking, not for a guess.

Nothing here can open, close, size or veto a trade. A failure is swallowed: a
measurement must never break a live tick.
"""
from __future__ import annotations

import json as _json
import os as _os
import time as _time

from app.config import settings
from app.models import Decision, OptionQuote

_LOG_NAME = "missed_opportunities.jsonl"

# How long a refused setup is followed. Long enough for an intraday leg to play
# out, short enough that the record is about THIS decision and not the day.
_WINDOW_SEC = 30 * 60
# A refused setup is only worth recording when a real leg and premium existed —
# otherwise "missed" would include bars with nothing to buy.
_MIN_PREMIUM = 1.0


def _log_path() -> str:
    return _os.path.join(settings.data_dir, _LOG_NAME)


def _leg_premium(chain: list[OptionQuote], symbol: str | None) -> float | None:
    if not symbol:
        return None
    for q in chain:
        if q.symbol == symbol and q.premium and q.premium > 0:
            return float(q.premium)
    return None


def _open_record(instrument: str, dec: Decision, now: int) -> dict | None:
    gates = dec.gates
    prem = float(dec.current_premium or 0.0)
    if gates is None or not dec.recommended_option or prem < _MIN_PREMIUM:
        return None
    if not gates.blockers:
        return None
    return {
        "instrument": instrument,
        "ts_open": int(now),
        "option_symbol": dec.recommended_option,
        "side": dec.option_type.value if dec.option_type else None,
        "strike": dec.strike,
        "signal": dec.signal.value if dec.signal else None,
        "ref_premium": round(prem, 2),
        "peak_premium": round(prem, 2),
        "trough_premium": round(prem, 2),
        "ts_peak": int(now),
        "primary_blocker": gates.primary_blocker,
        "secondary_blocker": gates.secondary_blocker,
        "blockers": list(gates.blockers),
        "confidence": gates.confidence,
        "confidence_gate": gates.confidence_gate,
        "entry_trigger": gates.entry_trigger,
        "reward_risk": gates.reward_risk,
        "directional_strength": gates.directional_strength,
    }


def _finalize(rec: dict, now: int) -> dict:
    ref = rec["ref_premium"] or 0.0
    mfe = round(rec["peak_premium"] - ref, 2)
    mae = round(ref - rec["trough_premium"], 2)
    out = dict(rec)
    out["ts_close"] = int(now)
    out["duration_sec"] = int(now) - rec["ts_open"]
    out["mfe_points"] = mfe
    out["mae_points"] = mae
    out["mfe_pct"] = round(100.0 * mfe / ref, 2) if ref > 0 else None
    out["mae_pct"] = round(100.0 * mae / ref, 2) if ref > 0 else None
    out["seconds_to_peak"] = int(rec["ts_peak"]) - int(rec["ts_open"])
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
    dec: Decision,
    chain: list[OptionQuote],
    now: int,
    rec: dict | None,
) -> dict | None:
    """Advance the recorder one tick; returns the still-open record, or None.

    Only ONE refused setup per instrument is tracked at a time — a new one is
    opened after the previous window closes. Following every bar would log the
    same move dozens of times and make one missed trade look like fifty.
    """
    try:
        if not settings.missed_opportunity_log:
            return None
        if rec is None:
            if dec.signal is not None and dec.signal.value in ("BUY", "HOLD", "EXIT"):
                return None
            return _open_record(instrument, dec, now)

        prem = _leg_premium(chain, rec["option_symbol"])
        if prem is not None:
            if prem > rec["peak_premium"]:
                rec["peak_premium"] = round(prem, 2)
                rec["ts_peak"] = int(now)
            if prem < rec["trough_premium"]:
                rec["trough_premium"] = round(prem, 2)

        if int(now) - int(rec["ts_open"]) >= _WINDOW_SEC:
            _append(_finalize(rec, now))
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


def summary(limit: int = 200, thresholds_pct: tuple[float, ...] = (5.0, 10.0, 20.0)) -> dict:
    """Blocker league table + the recent refused setups behind it.

    Counts are reported per threshold as a PERCENTAGE of the premium, not in
    points: ten points is a rounding error on a ₹500 crude leg and a double on a
    ₹10 one, so a points-only table would rank instruments, not gates.
    """
    rows = _read_all()
    by_gate: dict[str, dict] = {}
    for r in rows:
        gate = r.get("primary_blocker") or "UNATTRIBUTED"
        agg = by_gate.setdefault(
            gate,
            {
                "gate": gate,
                "refusals": 0,
                "sum_mfe_pct": 0.0,
                "best_mfe_pct": None,
                **{f"over_{int(t)}pct": 0 for t in thresholds_pct},
            },
        )
        agg["refusals"] += 1
        mfe = r.get("mfe_pct")
        if mfe is None:
            continue
        agg["sum_mfe_pct"] += mfe
        if agg["best_mfe_pct"] is None or mfe > agg["best_mfe_pct"]:
            agg["best_mfe_pct"] = mfe
        for t in thresholds_pct:
            if mfe >= t:
                agg[f"over_{int(t)}pct"] += 1

    gates = []
    for agg in by_gate.values():
        n = max(1, agg["refusals"])
        item = dict(agg)
        item["avg_mfe_pct"] = round(agg["sum_mfe_pct"] / n, 2)
        item.pop("sum_mfe_pct", None)
        gates.append(item)
    gates.sort(key=lambda x: x["refusals"], reverse=True)

    recent = sorted(rows, key=lambda r: r.get("ts_close", 0), reverse=True)[:limit]
    return {
        "total": len(rows),
        "window_minutes": _WINDOW_SEC // 60,
        "thresholds_pct": list(thresholds_pct),
        "gates": gates,
        "recent": recent,
        "as_of": int(_time.time()),
        "note": (
            "Measurement only: what the refused leg went on to do within the "
            "window. A high 'missed' count is evidence to investigate a gate, "
            "not proof it should be removed — the same gate also refuses the "
            "setups that would have stopped out."
        ),
    }
