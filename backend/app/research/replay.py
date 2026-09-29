"""Historical replay engine.

Replays stored futures + option data candle-by-candle through the FROZEN
Phase 1.5 engine and simulates the exact trades it would have taken, recording
every signal snapshot and completed trade to the research store.

Guarantees:
* **No lookahead.** At bar ``i`` the engine only ever sees ``candles[:i+1]`` and
  option data with ``ts <= candles[i].time``. This is enforced centrally here.
* **Same decision as live.** It calls the same ``compute_indicators`` /
  ``classify_market`` / ``decide`` used in production — no shadow copy of the
  logic, so replay == live by construction.
* Option P&L uses the REAL stored premium path of the exact recommended leg
  (close-to-close), with modelled brokerage + slippage (clearly labelled costs,
  not synthetic performance).

Replay ``speed`` (1x/5x/20x/100x) throttles a *streamed* run; the analytics run
executes at full speed. Speed never changes the decisions, only wall-clock pace.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone

from app.config import settings
from app.engine import decision as eng
from app.market.instruments import get_spec
from app.models import Candle, MarketStatus, OptionQuote, OptionType, Signal
from app.research.store import ResearchStore, store

ALLOWED_SPEEDS = (1, 5, 20, 100)
_BROKERAGE_RT = 40.0     # modelled round-trip cost (₹) — NOT a market price
_SLIPPAGE_PREMIUM = 0.05  # modelled entry+exit slippage in premium points


def _sym(instrument: str, strike: float, otype: str) -> str:
    return f"{instrument}{int(strike)}{otype}"


def _load(instrument: str, st: ResearchStore):
    """Load futures bars and, per timestamp, the option chain rows."""
    fut = st.futures_rows(instrument)
    opt_rows = st.db.query(
        "SELECT ts, strike, option_type, close, oi, oi_change, volume, iv, delta, "
        "gamma, theta, vega FROM mcx_options WHERE instrument=? ORDER BY ts ASC",
        (instrument,),
    )
    by_ts: dict[int, list[dict]] = defaultdict(list)
    for r in opt_rows:
        by_ts[int(r["ts"])].append(r)
    return fut, by_ts


def _chain_at(rows: list[dict], instrument: str) -> tuple[list[OptionQuote], dict[str, tuple[float, str]]]:
    chain: list[OptionQuote] = []
    sym_map: dict[str, tuple[float, str]] = {}
    for r in rows:
        otype = OptionType.CALL if r["option_type"] == "CE" else OptionType.PUT
        symbol = _sym(instrument, r["strike"], r["option_type"])
        sym_map[symbol] = (r["strike"], r["option_type"])
        chain.append(OptionQuote(
            symbol=symbol, strike=r["strike"], option_type=otype,
            premium=r["close"] or 0.0, iv=r["iv"] or 0.0, delta=r["delta"] or 0.0,
            gamma=r["gamma"] or 0.0, theta=r["theta"] or 0.0, vega=r["vega"] or 0.0,
            oi=int(r["oi"] or 0), oi_change=int(r["oi_change"] or 0),
            volume=int(r["volume"] or 0),
        ))
    return chain, sym_map


def replay(instrument: str, *, source: str = "replay", start: int = 60,
           speed: int = 100, stream: bool = False,
           st: ResearchStore | None = None) -> dict:
    st = st or store()
    if speed not in ALLOWED_SPEEDS:
        speed = 100
    spec = get_spec(instrument)
    fut, opt_by_ts = _load(instrument, st)
    n = len(fut)
    if n <= start + 10:
        return {"ok": False, "reason": "insufficient_data", "futures_rows": n,
                "instrument": instrument}

    st.clear_trades(instrument, source)

    candles = [Candle(time=int(r["ts"]), open=r["open"], high=r["high"],
                      low=r["low"], close=r["close"], volume=r["volume"] or 0.0)
               for r in fut]
    # premium path per option symbol, indexed by ts (for exact-leg P&L)
    prem_series: dict[str, dict[int, float]] = defaultdict(dict)
    for ts, rows in opt_by_ts.items():
        for r in rows:
            prem_series[_sym(instrument, r["strike"], r["option_type"])][ts] = r["close"] or 0.0

    throttle = (settings.candle_interval_seconds / speed) if stream else 0.0

    in_trade = False
    entry = {}
    trades = 0
    signals = 0

    for i in range(start, n):
        window = candles[: i + 1]            # NO future candles
        ts = int(candles[i].time)
        rows = opt_by_ts.get(ts, [])
        chain, _ = _chain_at(rows, instrument)
        price_change = candles[i].close - candles[i - 1].close if i > 0 else 0.0
        snap = eng.compute_indicators(window, chain, price_change=price_change)
        status = eng.classify_market(snap, False)
        decision, _ = eng.decide(
            window, chain, snap, 0.0, False, status, in_trade,
            entry.get("option_type") if in_trade else None,
            spot=candles[i].close,
        )
        signals += 1
        _record_signal(st, instrument, ts, source, decision, snap, status)

        if not in_trade:
            if decision.signal == Signal.BUY and decision.recommended_option:
                sym = decision.recommended_option
                px = prem_series.get(sym, {}).get(ts, decision.current_premium or 0.0)
                entry = {
                    "symbol": sym,
                    "option_type": decision.option_type,
                    "entry_ts": ts, "entry_px": px + _SLIPPAGE_PREMIUM,
                    "stop": decision.stop_loss, "t1": decision.target1,
                    "t2": decision.target2, "t3": decision.target3,
                    "mfe": 0.0, "mae": 0.0,
                    "trade_score": decision.trade_score, "regime": status.value,
                    "context": {
                        "opportunity": decision.opportunity_label,
                        "risk": decision.risk_meter,
                        "htf_trend": decision.htf_trend,
                        "entry_trigger": decision.entry_trigger,
                        "premium_health": decision.premium_health,
                        "reasons": list(decision.reasons),
                    },
                }
                in_trade = True
        else:
            sym = entry["symbol"]
            px = prem_series.get(sym, {}).get(ts)
            if px is not None:
                move = px - entry["entry_px"]
                entry["mfe"] = max(entry["mfe"], move)
                entry["mae"] = min(entry["mae"], move)
            exit_reason = _exit_reason(entry, px, decision, ts, i, n)
            if exit_reason:
                exit_px = (px if px is not None else entry["entry_px"]) - _SLIPPAGE_PREMIUM
                _record_trade(st, instrument, spec, source, entry, exit_px, ts, exit_reason, candles)
                trades += 1
                in_trade = False
                entry = {}

        if throttle:
            time.sleep(throttle)

    return {
        "ok": True, "instrument": instrument, "source": source,
        "backend": st.backend, "speed": speed,
        "futures_rows": n, "signals_recorded": signals, "trades": trades,
    }


def _exit_reason(entry: dict, px: float | None, decision, ts: int, i: int, n: int) -> str | None:
    want_call = entry["option_type"] == OptionType.CALL
    if px is not None:
        if entry["stop"] and px <= entry["stop"]:
            return "STOP"
        if entry["t3"] and px >= entry["t3"]:
            return "TARGET3"
        if entry["t2"] and px >= entry["t2"]:
            return "TARGET2"
        if entry["t1"] and px >= entry["t1"]:
            return "TARGET1"
    if decision.signal == Signal.EXIT:
        return "SIGNAL_EXIT"
    # flip: engine now recommends the opposite side while we hold
    if decision.signal == Signal.BUY and decision.option_type is not None:
        if (want_call and decision.option_type == OptionType.PUT) or \
           (not want_call and decision.option_type == OptionType.CALL):
            return "FLIP"
    if i >= n - 1:
        return "END_OF_DATA"
    return None


def _record_signal(st: ResearchStore, instrument: str, ts: int, source: str,
                   decision, snap, status: MarketStatus) -> None:
    trap = max(decision.buy_trap_prob, decision.sell_trap_prob,
               decision.fake_breakout_prob, decision.fake_breakdown_prob)
    st.insert_signal({
        "instrument": instrument, "ts": ts, "source": source,
        "signal": decision.signal.value, "confidence": decision.confidence,
        "trade_score": decision.trade_score,
        "opportunity": decision.opportunity_label, "risk": decision.risk_meter,
        "trend": decision.htf_trend, "market_regime": status.value,
        "premium_quality": decision.premium_quality, "trap_score": trap,
        "quantum_score": decision.signal_strength,
        "entry": decision.entry_range[0] if decision.entry_range else None,
        "stop": decision.stop_loss, "target1": decision.target1,
        "target2": decision.target2, "target3": decision.target3,
        "reasoning": list(decision.reasons),
    })


def _record_trade(st: ResearchStore, instrument: str, spec, source: str,
                  entry: dict, exit_px: float, exit_ts: int, reason: str,
                  candles: list[Candle]) -> None:
    pts = exit_px - entry["entry_px"]
    gross = pts * spec.lot_size
    pnl = gross - _BROKERAGE_RT
    duration_min = (exit_ts - entry["entry_ts"]) / 60.0
    hour = datetime.fromtimestamp(entry["entry_ts"], tz=timezone.utc).hour
    st.insert_trade({
        "instrument": instrument, "source": source,
        "option_symbol": entry["symbol"],
        "option_type": entry["option_type"].value if entry["option_type"] else None,
        "entry_ts": entry["entry_ts"], "exit_ts": exit_ts,
        "entry": round(entry["entry_px"], 2), "exit": round(exit_px, 2),
        "pnl": round(pnl, 1), "duration_min": round(duration_min, 1),
        "exit_reason": reason,
        "mfe": round(entry["mfe"] * spec.lot_size, 1),
        "mae": round(entry["mae"] * spec.lot_size, 1),
        "slippage": round(_SLIPPAGE_PREMIUM * 2 * spec.lot_size, 1),
        "brokerage": _BROKERAGE_RT,
        "trade_score": entry["trade_score"], "market_regime": entry["regime"],
        "entry_hour": hour, "win": pnl >= 0, "context": entry["context"],
    })
