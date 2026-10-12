"""Glue between the trading state and the v2 journal. Record only: every hook
swallows its own errors (counted in ``status()``) so journalling can never
block, delay or change a trade, and nothing here feeds back into a decision."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid

from app.analysis import exec_funnel, option_costs
from app.config import settings
from app.journal import sizing as sz
from app.journal.store import session_date, store
from app.market.instruments import get_spec

log = logging.getLogger(__name__)

STRATEGY = "TREND_PULLBACK"
STRATEGY_VERSION = "trend_pullback.v1"
_errors: dict = {"count": 0, "last": None, "last_ts": None}
_fp_cache: str | None = None


def status() -> dict:
    return dict(_errors)


def _fail(where: str, exc: Exception) -> None:
    _errors["count"] += 1
    _errors["last"] = f"{where}: {type(exc).__name__}: {exc}"[:300]
    _errors["last_ts"] = time.time()
    log.warning("journal v2 %s failed: %s", where, exc)


def config_fingerprint() -> str:
    """Hash of the settings that shape entries, exits and sizing."""
    global _fp_cache
    if _fp_cache is None:
        dump = settings.model_dump()
        keys = sorted(k for k in dump if k.startswith(("auto_trade_", "risk_", "sizing_",
                                                       "sim_", "capital")))
        raw = json.dumps({k: dump[k] for k in keys}, sort_keys=True, default=str)
        _fp_cache = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return _fp_cache


def _mode(st) -> str:
    return "live" if st.is_live else "paper"


def _session(st, now: float) -> str:
    mode = _mode(st)
    sid = f"{st.instrument}:{session_date(now)}:{mode}"
    if getattr(st, "_jr_session_id", None) != sid or now - getattr(st, "_jr_touch", 0.0) >= 60:
        store().upsert_session({
            "session_id": sid, "instrument": st.instrument, "mode": mode, "ts": now,
            "strategy_version": STRATEGY_VERSION, "config_fingerprint": config_fingerprint(),
            "data_source": type(st.provider).__name__,
        })
        st._jr_session_id = sid
        st._jr_touch = now
    return sid


def _lot_meta(st, symbol: str) -> tuple[int, str, float | None]:
    """Lot size with its provenance. Broker scrip master when the live feed
    has the contract loaded; otherwise the registry seed, labelled unverified."""
    inst = next((o for o in getattr(st.provider, "_options", []) or []
                 if getattr(o, "symbol", None) == symbol), None)
    if inst is not None and getattr(inst, "lot_size", None):
        return int(inst.lot_size), "BROKER_SCRIP_MASTER", getattr(
            st.provider, "_instruments_loaded_at", None)
    return int(get_spec(st.instrument).lot_size), "REGISTRY_SEED_UNVERIFIED", time.time()


def after_tick(st, decision, chain, was_in_pos: bool, now: float) -> None:
    if not settings.journal_v2_enabled or decision is None:
        return
    try:
        sid = _session(st, float(now))
        _record_signal(st, decision, chain, was_in_pos, float(now), sid)
        # Event time is wall-clock (sub-second) so it orders correctly against
        # ENTRY/EXIT events written inside the same tick.
        _track_levels(st, time.time(), sid)
    except Exception as exc:  # never let the journal touch trading
        _fail("after_tick", exc)


def _record_signal(st, d, chain, was_in_pos: bool, now: float, sid: str) -> None:
    sig = d.signal.value if hasattr(d.signal, "value") else str(d.signal)
    in_pos = bool(st.position.option_symbol)
    q = next((x for x in chain or [] if x.symbol == d.recommended_option), None)
    trade_id = None
    if sig == "BUY":
        feed_ok = bool(st._feed_ready())
        if in_pos and not was_in_pos:
            outcome, reason = "EXECUTED", "entered"
            trade_id = getattr(st, "_jr_trade_id", None)
        elif was_in_pos:
            outcome, reason = "SKIPPED", "already in a position"
        elif not feed_ok:
            outcome, reason = "NOT_EXECUTABLE", "feed not ready (stale or disconnected)"
        elif q is None or q.bid is None or q.ask is None:
            outcome, reason = "NOT_EXECUTABLE", "no executable two-sided quote"
        else:
            blk = exec_funnel.last_block(st.instrument, since=now - 1.0,
                                         option=d.recommended_option or "")
            if blk:
                outcome = "REJECTED" if blk.get("stage") == "ACCEPTED" else "SKIPPED"
                reason = f"{blk.get('primary_blocker')}: {blk.get('reason') or ''}".strip()
            elif not settings.auto_trade_enabled:
                outcome, reason = "SKIPPED", "auto-trader off"
            else:
                outcome, reason = "SKIPPED", "not taken; no gate reported"
        st._jr_last_signal = sig
    else:
        if getattr(st, "_jr_last_signal", None) == sig:
            return  # record transitions only, not every tick of a WAIT
        st._jr_last_signal = sig
        outcome = "FAILED_CONDITIONS"
        reason = "; ".join((d.reasons or [])[:3]) or sig
    status = getattr(st, "_last_status", None)
    store().record_signal({
        "session_id": sid, "trade_id": trade_id, "instrument": st.instrument, "ts": now,
        "signal": sig, "outcome": outcome, "reason": (reason or "")[:500],
        "strategy": STRATEGY, "setup": d.entry_trigger,
        "regime": status.value if status is not None else None, "htf_trend": d.htf_trend,
        "confidence": d.confidence, "option_symbol": d.recommended_option,
        "quote_ltp": q.premium if q else None, "bid": q.bid if q else None,
        "ask": q.ask if q else None, "expected_entry": d.current_premium,
        "stop": d.stop_loss, "target1": d.target1, "target2": d.target2,
        "target3": d.target3, "mode": _mode(st), "strategy_version": STRATEGY_VERSION,
        "config_fingerprint": config_fingerprint(),
        "data_quality": f"feed_ready={bool(st._feed_ready())}",
        "explanation": "; ".join((d.reasons or [])[:5])[:1000] or None,
    })


def _track_levels(st, now: float, sid: str) -> None:
    tid = getattr(st, "_jr_trade_id", None)
    if not tid or not st.position.option_symbol:
        return
    stop = getattr(st, "_auto_stop", None)
    floor = getattr(st, "_auto_tgt_floor", None)
    prev = getattr(st, "_jr_levels", (None, None))
    if stop is not None and (prev[0] is None or abs(stop - prev[0]) > 1e-9):
        why = f"stop set at {stop:.2f}" if prev[0] is None else f"stop {prev[0]:.2f} -> {stop:.2f}"
        store().record_trade_event({
            "trade_id": tid, "session_id": sid, "instrument": st.instrument, "ts": now,
            "kind": "STOP_CHANGE", "option_symbol": st.position.option_symbol,
            "stop": round(stop, 4), "reason": why, "mode": _mode(st)})
    if floor is not None and floor != prev[1]:
        lvl = "T1"
        t2, t3 = getattr(st, "_auto_target2", None), getattr(st, "_auto_target3", None)
        if t3 is not None and floor >= t3:
            lvl = "T3+"
        elif t2 is not None and floor >= t2:
            lvl = "T2"
        store().record_trade_event({
            "trade_id": tid, "session_id": sid, "instrument": st.instrument, "ts": now,
            "kind": "TARGET_LEVEL", "option_symbol": st.position.option_symbol,
            "target": round(floor, 4), "reason": f"{lvl} reached; floor {floor:.2f}",
            "mode": _mode(st)})
    st._jr_levels = (stop if stop is not None else prev[0], floor)


def on_entry(st, q, lots: int, entry: float, manual: bool) -> None:
    if not settings.journal_v2_enabled:
        return
    try:
        now = time.time()
        sid = _session(st, now)
        mode = _mode(st)
        d = st._last_decision
        tid = uuid.uuid4().hex
        st._jr_trade_id = tid
        lot_size, source, lot_ts = _lot_meta(st, q.symbol)
        stop = d.stop_loss if d else None
        store().open_trade({
            "trade_id": tid, "session_id": sid, "instrument": st.instrument, "mode": mode,
            "strategy": STRATEGY, "setup": d.entry_trigger if d else None,
            "regime": st._last_status.value if getattr(st, "_last_status", None) else None,
            "option_symbol": q.symbol, "lot_size": lot_size, "entry_ts": now,
            "entry_price": entry, "lots": lots, "capital_used": round(entry * lot_size * lots, 2),
            "initial_stop": stop, "target1": d.target1 if d else None,
            "target2": d.target2 if d else None, "target3": d.target3 if d else None,
            "entry_explanation": ("manual entry; " if manual else "auto entry; ") + (
                "; ".join((d.reasons or [])[:4]) if d else ""),
            "mfe_mae_status": "PREMIUM_PEAK_TROUGH_FROM_LIVE_MARKS",
            "strategy_version": STRATEGY_VERSION, "config_fingerprint": config_fingerprint(),
        })
        store().record_trade_event({
            "trade_id": tid, "session_id": sid, "instrument": st.instrument, "ts": now,
            "kind": "ENTRY", "option_symbol": q.symbol, "lots": lots, "price": entry,
            "stop": stop, "target": d.target1 if d else None,
            "reason": "manual" if manual else "auto", "mode": mode,
            "detail": {"bid": q.bid, "ask": q.ask, "quote_ltp": q.premium}})
        st._jr_levels = (stop, None)
        _record_sizing(st, tid, sid, q.symbol, entry, stop, lots, lot_size, source, lot_ts,
                       now, mode)
    except Exception as exc:
        _fail("on_entry", exc)


def _record_sizing(st, tid, sid, symbol, entry, stop, lots, lot_size, source, lot_ts,
                   now, mode) -> None:
    from app.state import registry  # call-time import: state imports this module

    equity = float(settings.sim_starting_capital) + store().realised_net(mode)
    deployed = 0.0
    for other in registry.active():
        if other is st or not other.position.option_symbol:
            continue
        p = other.position
        deployed += (p.entry_premium or 0.0) * get_spec(other.instrument).lot_size * p.quantity_lots
    available = equity - deployed
    est_cost = option_costs.charges(entry, entry, lot_size).total
    inp = sz.SizingInputs(
        instrument=st.instrument, contract=symbol, vehicle="OPTION_BUY", entry_price=entry,
        stop_price=stop, lot_size=lot_size, lot_size_source=source, lot_size_ts=lot_ts,
        starting_capital=float(settings.sim_starting_capital), equity=equity,
        available_funds=available, est_cost_per_lot=est_cost,
        capital_fraction_pct=settings.sizing_capital_fraction_pct,
        aggressive_multiplier=settings.sizing_aggressive_multiplier,
        risk_budget_pct=settings.sizing_risk_budget_pct)
    common = {"trade_id": tid, "session_id": sid, "instrument": st.instrument, "ts": now,
              "contract": symbol, "vehicle": inp.vehicle, "starting_capital": inp.starting_capital,
              "equity": equity, "available_funds": available, "entry_price": entry,
              "stop_price": stop, "lot_size": lot_size, "lot_size_source": source,
              "lot_size_ts": lot_ts, "cost_status": inp.cost_status, "mode": mode}
    for r in sz.compare(inp):
        store().record_sizing({**common, **r.as_dict(), "applied": False})
    per_lot = sz.cost_per_lot(inp)
    exposure = (round((entry - stop) * lot_size * lots + est_cost * lots, 2)
                if stop is not None and stop < entry else None)
    store().record_sizing({
        **common, "method": "ENGINE_ACTUAL", "applied": True, "proposed_lots": lots,
        "actual_lots": lots, "margin_per_lot": None, "margin_status": "NOT_APPLICABLE",
        "total_margin": None, "cost_per_lot": per_lot, "stop_exposure": exposure,
        "est_costs": round(est_cost * lots, 2), "constraint_name": "ENGINE",
        "reason": "quantity the engine actually used (unchanged by journal v2)"})


def on_exit(st, trade_row: dict, partial: bool, lots: int) -> None:
    if not settings.journal_v2_enabled:
        return
    try:
        now = time.time()
        sid = _session(st, now)
        mode = trade_row.get("mode") or _mode(st)
        tid = getattr(st, "_jr_trade_id", None)
        if tid is None:  # position opened before journal v2 (e.g. pre-upgrade)
            ctx = trade_row.get("context") or {}
            entry_ts = float(ctx.get("entry_time") or now)
            tid = f"legacy-{st.instrument}-{int(entry_ts)}"
            store().open_trade({
                "trade_id": tid, "session_id": sid, "instrument": st.instrument, "mode": mode,
                "strategy": STRATEGY, "option_symbol": trade_row.get("option"),
                "entry_ts": entry_ts, "entry_price": trade_row.get("entry"),
                "lots": trade_row.get("lots"), "entry_explanation": "opened before journal v2",
                "mfe_mae_status": "UNAVAILABLE"})
            st._jr_trade_id = tid
        net = trade_row.get("net_pnl")
        costs = trade_row.get("total")
        gross = round(net + costs, 2) if net is not None and costs is not None else None
        store().record_trade_event({
            "trade_id": tid, "session_id": sid, "instrument": st.instrument, "ts": now,
            "kind": "PARTIAL_EXIT" if partial else "EXIT",
            "option_symbol": trade_row.get("option"), "lots": lots,
            "price": trade_row.get("exit"), "reason": trade_row.get("exit_reason"),
            "gross_pnl": gross, "costs": costs, "net_pnl": net, "mode": mode,
            "detail": {k: trade_row.get(k) for k in (
                "brokerage", "statutory", "cost_model", "holding_minutes", "capital_used",
                "return_on_capital_pct", "cost_status")}})
        if not partial:
            st._jr_trade_id = None
            st._jr_levels = (None, None)
    except Exception as exc:
        _fail("on_exit", exc)
