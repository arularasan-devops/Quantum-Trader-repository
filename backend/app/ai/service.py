"""Live AI cycle — scan, decide, act on the paper book, resolve outcomes.

One cycle:

1. Ask the Phase 4 scanner which instruments deserve the expensive pass. That is
   ALL the scanner is used for. Its score was measured non-predictive in Phase 4
   (OPPORTUNITY bars resolved worse than NO_OPPORTUNITY on both instruments), so
   it allocates attention and never contributes to a decision.
2. Run the AI stack on each candidate, plus every instrument with an open paper
   position and the instrument being viewed — a position must never stop being
   monitored because something else looked more interesting.
3. Record the AI decision next to the production engine's simultaneous verdict.
   The baseline verdict is READ from the already-computed snapshot; this module
   never asks the production engine to run and never writes to it.
4. Open paper positions for BUY_NOW, mark open positions to the live feed, and
   apply exit advice — paper only.
5. Resolve past decisions once their research horizon has elapsed, building the
   outcome dataset for every decision including the NO_TRADEs.

Read-only with respect to production: no gate, threshold, stop, target or order
path is touched, and no real order can be placed from here (:mod:`app.ai.safety`).
"""
from __future__ import annotations

import threading
import time

from app.ai import exit_ai
from app.ai import features as F
from app.ai import journal as aij
from app.ai import orchestrator, paper, probability
from app.analysis import scan_service
from app.config import settings
from app.market.tick_quality import feed_quality

_HORIZON_BARS = 30          # same horizon the research measured
_RESOLVE_DELAY_SEC = 60.0   # extra slack so the forward bars exist in the cache
# After this, a decision that still has no forward bars never will (the session
# ended, or the bars were never recorded) and is marked UNRESOLVABLE rather than
# left at the head of the queue blocking every later row.
_GIVE_UP_SEC = 6 * 3600
# The resolver reads a long window of persisted bars per instrument, on the same
# SQLite file the live scan writes to. Running it every AI cycle put that read in
# front of the scan's writes; a backlog only needs draining on the order of a bar,
# not of a cycle. The forward window is also cached for a bar, for the same reason.
_RESOLVE_EVERY_SEC = 30.0
_FORWARD_CACHE_TTL = 60.0
_RESOLVE_BATCH = 50

_lock = threading.Lock()
_state: dict = {
    "last_cycle_ts": 0.0,
    "cycles": 0,
    "last_ms": 0.0,
    "decisions": [],        # most recent cycle's decisions, newest first
    "events": [],           # paper entries/exits from the last cycle
    "errors": [],
    "resolve": {},          # last resolver pass: pending/labelled/gave_up/skipped
}
_last_resolve_ts = 0.0
_forward_cache: dict[str, tuple[float, list]] = {}


def _resolve_state(instrument: str):
    from app.state import registry

    return registry.get(instrument)


def _cached(instrument: str):
    """(state, candles, feed_state, age_ms) using cached data only — no warm-up."""
    st = _resolve_state(instrument)
    try:
        candles = st.provider.futures_candles(F.WINDOW)
    except Exception:
        candles = []
    from app.market.instruments import get_spec

    try:
        root = get_spec(instrument).symbol
    except Exception:
        root = instrument
    fq = feed_quality.snapshot(root)
    return st, candles, str(fq.get("state") or "NO_DATA"), fq.get("last_tick_age_ms")


def _baseline(instrument: str) -> dict:
    """The production engine's current verdict, read from cache. Never recomputed."""
    from app.main import hub

    snap = hub.latest.get(instrument)
    if snap is None:
        return {"decision": None, "side": None, "confidence": None}
    d = snap.decision
    market = d.market_signal or d.signal
    ot = d.option_type
    return {
        "decision": market.value,
        "position_signal": d.signal.value,
        "side": ot.value if ot is not None else None,
        "confidence": d.confidence,
    }


def _candidates() -> list[str]:
    """Scanner top-N, plus open positions and the viewed instrument."""
    names: list[str] = []
    try:
        ranked = scan_service.sweep()
        names = [r.instrument for r in ranked[:max(1, settings.ai_top_candidates)]]
    except Exception:
        names = []
    for t in aij.journal().open_trades():
        if str(t["instrument"]) not in names:
            names.append(str(t["instrument"]))
    out: list[str] = []
    for n in names:
        if n not in out:
            out.append(n)
    return out


def _record(dec: orchestrator.AIDecision, base: dict, scan: dict,
            paper_result: dict | None) -> str:
    j = aij.journal()
    prob = dec.prob or {}
    return j.insert_decision({
        "ts": int(time.time()),
        "instrument": dec.instrument,
        "price": dec.price,
        "atr": dec.atr,
        "feed_state": dec.feed_state,
        "data_age_ms": dec.data_age_ms,
        "scan_state": scan.get("state"),
        "scan_verdict": scan.get("verdict"),
        "scan_score": scan.get("score"),
        "regime": (dec.regime or {}).get("state"),
        "regime_conf": (dec.regime or {}).get("confidence"),
        "direction_side": (dec.direction or {}).get("preferred_side"),
        "p_ce": (dec.direction or {}).get("p_ce"),
        "p_pe": (dec.direction or {}).get("p_pe"),
        "entry_quality": (dec.entry or {}).get("verdict"),
        "entry_score": (dec.entry or {}).get("score"),
        "ai_probability": dec.probability,
        "expected_r": dec.expected_r,
        "ai_decision": dec.decision,
        "ai_side": dec.side,
        "paper_decision": (paper_result or {}).get("result"),
        "blocked_by": (dec.blocked_by
                       or ",".join((paper_result or {}).get("failed_checks") or []) or None),
        "baseline_decision": base.get("decision"),
        "baseline_side": base.get("side"),
        "baseline_conf": base.get("confidence"),
        "model_version": prob.get("model_version"),
        "reasons": list(dec.reasons),
        "features": {k: v for k, v in (dec.features or {}).items()
                     if not k.startswith("_")},
    })


def _apply_exit_advice(events: list[dict]) -> None:
    """Exit advice for still-open paper positions (paper book only)."""
    j = aij.journal()
    for t in j.open_trades():
        inst = str(t["instrument"])
        try:
            _st, candles, feed_state, _age = _cached(inst)
        except Exception:
            continue
        if feed_state in ("DEAD", "NO_DATA") or not candles:
            continue
        feats = F.compute(list(candles)[-F.WINDOW:])
        if feats is None:
            continue
        from app.ai import regime as reg_engine

        reg = reg_engine.classify(feats, None)
        mark = float(t.get("current_premium") or t.get("entry_premium") or 0.0)
        advice = exit_ai.advise(dict(t), feats, reg.state, reg.direction_bias, mark)
        if advice.action == exit_ai.EXIT:
            events.append(paper.close_paper(
                str(t["id"]), advice.exit_reason or paper.AI_EXIT, _resolve_state))
        elif advice.action == exit_ai.TIGHTEN_STOP and advice.new_stop:
            if advice.new_stop > float(t.get("stop") or 0.0):
                j.set_levels(str(t["id"]), stop=advice.new_stop)
                events.append({"trade_id": t["id"], "action": exit_ai.TIGHTEN_STOP,
                               "new_stop": advice.new_stop,
                               "reasons": list(advice.reasons)})


def _forward_candles(instrument: str) -> list:
    """Bars for resolution: the provider's in-memory window UNION the persisted
    history, newest last, de-duplicated on timestamp.

    The in-memory window alone was why the resolver wrote nothing: it belongs to a
    live provider instance, so it is empty for any instrument that is no longer
    warm and for every instrument after a restart. The persisted candles outlive
    both.
    """
    hit = _forward_cache.get(instrument)
    if hit is not None and time.time() - hit[0] < _FORWARD_CACHE_TTL:
        return hit[1]
    bars: dict[int, object] = {}
    try:
        for c in _resolve_state(instrument).provider.futures_candles(1200) or []:
            bars[int(c.time)] = c
    except Exception:
        pass
    try:
        from app import storage

        for c in storage.store.candles(instrument, 1200) or []:
            bars.setdefault(int(c.time), c)
    except Exception:
        pass
    out = [bars[k] for k in sorted(bars)]
    _forward_cache[instrument] = (time.time(), out)
    return out


def _resolve_outcomes() -> int:
    """Label decisions whose research horizon has passed.

    Both sides are labelled on identical levels at the decision's own price, which
    is what makes the dataset usable for a side model later: the row records what
    CE and PE each did, not only the side the AI happened to pick.

    A decision that still cannot be labelled once ``_GIVE_UP_SEC`` has passed is
    written as UNRESOLVABLE with no labels. Without that, one unlabellable row at
    the head of an ``ORDER BY ts ASC LIMIT n`` queue blocks every later row
    forever — which is exactly how 13,276 journaled decisions produced 0 outcomes.
    """
    global _last_resolve_ts
    if time.time() - _last_resolve_ts < _RESOLVE_EVERY_SEC:
        return 0
    _last_resolve_ts = time.time()
    j = aij.journal()
    now = int(time.time())
    cutoff = int(now - _HORIZON_BARS * 60 - _RESOLVE_DELAY_SEC)
    pending = j.unresolved_decisions(cutoff, limit=_RESOLVE_BATCH)
    skips: dict[str, int] = {}
    if not pending:
        _note_resolve(len(pending), 0, 0, skips)
        return 0
    cache: dict[str, list] = {}
    done = 0
    gave_up = 0
    for row in pending:
        inst = str(row["instrument"])
        if inst not in cache:
            cache[inst] = _forward_candles(inst)
        candles = cache[inst]
        atr = float(row.get("atr") or 0.0)
        price = float(row.get("price") or 0.0)
        ts = int(row["ts"])
        fwd = [c for c in candles if c.time > ts][:_HORIZON_BARS]
        reason = None
        if not atr or not price:
            reason = "NO_PRICE_OR_ATR"
        elif not candles:
            reason = "NO_CANDLES"
        elif len(fwd) < _HORIZON_BARS:
            reason = "SHORT_FORWARD_WINDOW"
        if reason is not None:
            skips[reason] = skips.get(reason, 0) + 1
            if now - ts >= _GIVE_UP_SEC:
                j.insert_outcome({
                    "decision_id": row["id"], "resolved_ts": now,
                    "horizon_bars": _HORIZON_BARS,
                    "basis": f"UNRESOLVABLE_{reason}",
                })
                gave_up += 1
            continue
        stop_dist = settings.ai_stop_atr * atr
        ce = _outcome(fwd, price, stop_dist, True)
        pe = _outcome(fwd, price, stop_dist, False)
        taken = str(row.get("ai_side") or "") or None
        tk = ce if taken == "CE" else pe if taken == "PE" else None
        j.insert_outcome({
            "decision_id": row["id"],
            "resolved_ts": int(time.time()),
            "horizon_bars": _HORIZON_BARS,
            "basis": "UNDERLYING move (no premium, no theta, no spread)",
            "ce_result": ce["result"], "ce_r": ce["r"],
            "ce_mfe_r": ce["mfe_r"], "ce_mae_r": ce["mae_r"],
            "pe_result": pe["result"], "pe_r": pe["r"],
            "pe_mfe_r": pe["mfe_r"], "pe_mae_r": pe["mae_r"],
            "taken_side": taken,
            "taken_result": tk["result"] if tk else None,
            "taken_r": tk["r"] if tk else None,
        })
        done += 1
    _note_resolve(len(pending), done, gave_up, skips)
    return done


def _note_resolve(pending: int, done: int, gave_up: int, skips: dict) -> None:
    """Publish why the resolver did or did not label rows. The old resolver skipped
    silently, so "0 outcomes" was indistinguishable from "never ran"."""
    with _lock:
        _state["resolve"] = {
            "last_ts": int(time.time()),
            "pending": pending,
            "labelled": done,
            "gave_up": gave_up,
            "skipped": dict(skips),
        }


def _outcome(fwd: list, entry: float, stop_dist: float, up: bool) -> dict:
    """Target-before-stop on the underlying. A bar touching both counts as STOP."""
    rr = settings.ai_t1_r
    target = entry + rr * stop_dist * (1 if up else -1)
    stop = entry - stop_dist * (1 if up else -1)
    mfe = mae = 0.0
    result = "OPEN"
    for c in fwd:
        mfe = max(mfe, (c.high - entry) if up else (entry - c.low))
        mae = max(mae, (entry - c.low) if up else (c.high - entry))
        hit_t = (c.high >= target) if up else (c.low <= target)
        hit_s = (c.low <= stop) if up else (c.high >= stop)
        if hit_s:
            result = "STOP"
            break
        if hit_t:
            result = "TARGET"
            break
    return {
        "result": result,
        "r": round(rr if result == "TARGET" else -1.0 if result == "STOP"
                   else (mfe - mae) / stop_dist, 4),
        "mfe_r": round(mfe / stop_dist, 4),
        "mae_r": round(mae / stop_dist, 4),
    }


def cycle() -> dict:
    """Run one AI cycle. Safe to call from a background task or an endpoint."""
    t0 = time.perf_counter()
    decisions: list[dict] = []
    events: list[dict] = []
    errors: list[str] = []

    # Manage first: an open position matters more than a new idea.
    try:
        events.extend(paper.manage(_resolve_state))
        _apply_exit_advice(events)
    except Exception as exc:
        errors.append(f"manage: {type(exc).__name__}")

    scans: dict[str, dict] = {}
    try:
        for r in scan_service.sweep():
            scans[r.instrument] = {"state": r.classification, "verdict": r.verdict,
                                   "score": r.opportunity_score}
    except Exception:
        scans = {}

    for inst in _candidates():
        try:
            _st, candles, feed_state, age = _cached(inst)
            sc = scans.get(inst, {})
            dec = orchestrator.evaluate(inst, candles, feed_state, age,
                                        sc.get("state"), sc.get("verdict"))
            base = _baseline(inst)
            paper_result = None
            if dec.decision == orchestrator.BUY_NOW and settings.ai_paper_enabled:
                st = _resolve_state(inst)
                try:
                    chain = st.provider.option_chain()
                    spot = st.provider.futures_price()
                except Exception:
                    chain, spot = [], None
                quote = paper.select_contract(chain, spot or 0.0, dec.side or "CE")
                paper_result = paper.open_paper(
                    inst, dec.side or "CE", quote, dec.features, feed_state, age,
                    dec.probability, (dec.regime or {}).get("state", ""),
                    dec.side or "", (dec.entry or {}).get("verdict", ""))
                if paper_result.get("result") == paper.PAPER_ENTERED:
                    events.append({"trade_id": paper_result["trade_id"],
                                   "action": "PAPER_ENTRY", "instrument": inst,
                                   "side": dec.side, "entry": paper_result["entry"]})
            elif dec.decision == orchestrator.BUY_NOW:
                paper_result = {"result": paper.NO_PAPER_TRADE,
                                "failed_checks": ["AI_PAPER_DISABLED"]}
            did = _record(dec, base, sc, paper_result)
            row = dec.as_dict()
            row["id"] = did
            row["baseline"] = base
            row["scan"] = sc
            row["paper"] = paper_result
            decisions.append(row)
        except Exception as exc:
            errors.append(f"{inst}: {type(exc).__name__}: {exc}")

    resolved = 0
    try:
        resolved = _resolve_outcomes()
    except Exception as exc:
        errors.append(f"resolve: {type(exc).__name__}")

    ms = (time.perf_counter() - t0) * 1000.0
    with _lock:
        _state.update({
            "last_cycle_ts": time.time(),
            "cycles": int(_state["cycles"]) + 1,
            "last_ms": round(ms, 1),
            "decisions": decisions,
            "events": events,
            "errors": errors,
        })
    return {"decisions": decisions, "events": events, "resolved": resolved,
            "errors": errors, "ms": round(ms, 1)}


def status() -> dict:
    from app.ai import safety

    with _lock:
        snap = dict(_state)
    j = aij.journal()
    return {
        "enabled": settings.ai_enabled,
        "paper_enabled": settings.ai_paper_enabled,
        "safety": safety.status(),
        "model": probability.status(),
        "interval_sec": settings.ai_interval_sec,
        "cycles": snap["cycles"],
        "last_cycle_ts": int(snap["last_cycle_ts"]),
        "last_cycle_ms": snap["last_ms"],
        "decisions_recorded": j.decision_count(),
        "outcomes": j.outcome_counts(),
        "resolver": snap.get("resolve") or {},
        "open_positions": len(j.open_trades()),
        "errors": snap["errors"],
        "thresholds": {
            "min_probability": settings.ai_min_probability,
            "min_entry_quality": settings.ai_min_entry_quality,
            "min_regime_confidence": settings.ai_min_regime_confidence,
            "min_direction_edge": settings.ai_min_direction_edge,
            "max_open_positions": settings.ai_max_open_positions,
            "max_trades_per_day": settings.ai_max_trades_per_day,
            "risk_per_trade_pct": settings.ai_risk_per_trade_pct,
            "stop_atr": settings.ai_stop_atr,
            "t1_r": settings.ai_t1_r,
            "max_hold_min": settings.ai_max_hold_min,
        },
    }


# Public aliases for the API layer (the underscore names stay for internal use).
resolve_state = _resolve_state
cached_inputs = _cached
baseline_verdict = _baseline


def last_decisions(limit: int = 20) -> list[dict]:
    with _lock:
        return list(_state["decisions"])[:limit]
