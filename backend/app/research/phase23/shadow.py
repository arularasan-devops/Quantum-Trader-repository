"""The shadow itself: observe every auto BUY opportunity, resolve what happened.

One row per signal episode, written the moment the opportunity exists, with the
measured book at that instant and the three arms' decisions frozen before the
outcome is known. The outcome is attached later from the engine's own closed
trade, so the shadow never scores itself on a path it invented.

It reads the decision and the chain, and it writes a file. It returns nothing the
tick uses, it cannot refuse an order, and it swallows its own failures into a
health counter — a research row is never worth a live tick.
"""
from __future__ import annotations

import threading
import time

from app.config import settings
from app.models import Decision, OptionQuote, Signal
from app.research.phase23 import hurdle as hurdle_mod, store

SIMULATED_PROVIDERS = ("simulated", "sim", "demo")

# One open trade per instrument, so a resolution can find the row it belongs to.
_OPEN: dict[str, str] = {}
# Last episode key recorded per instrument, so a BUY that persists for fifty
# ticks is one opportunity rather than fifty.
_LAST_KEY: dict[str, str] = {}
_LOCK = threading.Lock()
_STATS = {"observed": 0, "resolved": 0, "failures": 0, "last_error": None}

T1 = "T1_BEFORE_SL"
SL = "SL"
TRAIL = "TRAIL_OR_LOCK"
MANUAL = "MANUAL"
OTHER = "OTHER"
NO_TRADE = "NO_TRADE"


def real_feed() -> bool:
    """True only on a real broker feed.

    A row produced by the simulated provider is still written down, because
    deleting evidence is worse than labelling it, but it is excluded from the
    arms and the verdict: a hurdle measured on a synthetic book is not a
    measurement of anything.
    """
    return str(settings.data_provider).lower() not in SIMULATED_PROVIDERS


def episode_key(decision: Decision) -> str:
    otype = decision.option_type.value if decision.option_type else ""
    return f"{decision.recommended_option or ''}|{otype}"


def _quote_for(chain: list[OptionQuote], symbol: str) -> OptionQuote | None:
    for q in chain or []:
        if q.symbol == symbol:
            return q
    return None


def classify_exit(reason: str | None) -> str:
    r = (reason or "").upper()
    if not r:
        return OTHER
    if r.startswith("STOP") or "STOP LOSS" in r:
        return SL
    if "TARGET" in r or r.startswith("T1") or r.startswith("SCALE-OUT T1"):
        return T1
    if "TRAIL" in r or "LOCK" in r or "SCALP" in r:
        return TRAIL
    if "MANUAL" in r:
        return MANUAL
    return OTHER


def observe(instrument: str, decision: Decision, chain: list[OptionQuote], *,
            lot_size: int, lots: int, engine_bought: bool,
            engine_fill: float | None, in_position_before: bool,
            now: int | None = None) -> str | None:
    """Record one auto BUY opportunity. Returns the row id, or ``None``.

    Only a BUY carrying a tradeable plan is an opportunity — a WAIT is not a
    refusal by any gate and is not counted as one. Nothing is recorded while a
    position is already open, because the engine could not have taken a second
    one and crediting a gate for refusing an unavailable trade would be false.
    """
    global _STATS
    try:
        if decision.signal != Signal.BUY or not decision.recommended_option:
            return None
        if in_position_before and not engine_bought:
            return None
        key = episode_key(decision)
        with _LOCK:
            if not engine_bought and _LAST_KEY.get(instrument) == key:
                return None
            _LAST_KEY[instrument] = key
        ts = int(now or time.time())
        symbol = decision.recommended_option
        quote = _quote_for(chain, symbol)
        h = hurdle_mod.compute(
            instrument,
            bid=(quote.bid if quote else None),
            ask=(quote.ask if quote else None),
            lot_size=lot_size,
            lots=max(1, int(lots or 1)),
        )
        row = {
            "kind": store.OBS,
            "id": f"{instrument}|{symbol}|{ts}",
            "ts": ts,
            "entry_timestamp": ts,
            "instrument": instrument,
            "option": symbol,
            "option_type": (decision.option_type.value
                            if decision.option_type else None),
            "strike": decision.strike,
            "premium_last": decision.current_premium,
            "signal": decision.signal.value,
            "confidence": decision.confidence,
            "entry_trigger": decision.entry_trigger,
            "stop_loss": decision.stop_loss,
            "target1": decision.target1,
            "lots": max(1, int(lots or 1)),
            "lot_size": int(lot_size),
            "engine_would_buy": bool(engine_bought),
            "engine_fill_premium": engine_fill,
            "vehicle": "OPTION",
            "feed": str(settings.data_provider),
            "real_feed": real_feed(),
        }
        row.update(h.as_dict())
        # The arms, decided before the outcome exists.
        row["arm_existing"] = (
            hurdle_mod.ALLOW if engine_bought else hurdle_mod.REFUSE
        )
        row["arm_shadow_5"] = h.decide(hurdle_mod.GATE_LOOSE_PCT)
        row["arm_shadow_3"] = h.decide(hurdle_mod.GATE_STRICT_PCT)
        row["sweep"] = {f"{t:g}": h.decide(t) for t in hurdle_mod.SWEEP_PCT}
        row["outcome"] = NO_TRADE
        store.append(row)
        with _LOCK:
            _STATS["observed"] += 1
            if engine_bought:
                _OPEN[instrument] = row["id"]
        return row["id"]
    except Exception as exc:  # research must never break a live tick
        with _LOCK:
            _STATS["failures"] += 1
            _STATS["last_error"] = str(exc)
        return None


def resolve(instrument: str, trade_row: dict) -> None:
    """Attach the engine's own closed trade to the observation that opened it."""
    global _STATS
    try:
        with _LOCK:
            row_id = _OPEN.pop(instrument, None)
            # The episode is over, so the same option may legitimately be a new
            # opportunity later; clearing the dedup key keeps that visible
            # without letting a persisting BUY write a row per tick.
            _LAST_KEY.pop(instrument, None)
        if not row_id or not isinstance(trade_row, dict):
            return
        net = trade_row.get("net_pnl")
        risk = trade_row.get("risk_rupees")
        net_r = None
        if isinstance(net, (int, float)) and isinstance(risk, (int, float)) \
                and risk > 0:
            net_r = round(float(net) / float(risk), 3)
        store.append({
            "kind": store.RES,
            "id": row_id,
            "resolved_ts": int(trade_row.get("time") or time.time()),
            "exit_premium": trade_row.get("exit"),
            "exit_reason": trade_row.get("exit_reason"),
            "outcome": classify_exit(trade_row.get("exit_reason")),
            "net_pnl": net,
            "net_r": net_r,
            "risk_rupees": risk,
            "hold_minutes": trade_row.get("holding_minutes"),
            "lots_closed": trade_row.get("lots"),
            "exit_cost_status": trade_row.get("cost_status"),
            "exit_bid": trade_row.get("exit_bid"),
            "exit_ask": trade_row.get("exit_ask"),
            "entry_bid_at_fill": trade_row.get("entry_bid"),
            "entry_ask_at_fill": trade_row.get("entry_ask"),
        })
        with _LOCK:
            _STATS["resolved"] += 1
    except Exception as exc:
        with _LOCK:
            _STATS["failures"] += 1
            _STATS["last_error"] = str(exc)


def health() -> dict:
    with _LOCK:
        stats = dict(_STATS)
        stats["open_trades"] = len(_OPEN)
    stats.update(store.health())
    return stats


def _reset_for_tests() -> None:
    with _LOCK:
        _OPEN.clear()
        _LAST_KEY.clear()
        _STATS.update({"observed": 0, "resolved": 0, "failures": 0,
                       "last_error": None})
