"""Live glue for the Phase 4 opportunity scanner (RESEARCH / OBSERVABILITY).

Builds :class:`~app.analysis.scanner.ScanInput` for every warm instrument from
data that is ALREADY in memory — cached candles, the cached LTP and the feed
quality tracker — and never triggers a broker call, a warm-up or an engine
tick. That is what makes it safe to run over a broad universe ahead of the
expensive strategy engine.

It cannot produce a BUY. Its output is a ranking plus OPPORTUNITY / WATCH /
NO_OPPORTUNITY, handed to the (unchanged) decision engine for the deep work.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from app.analysis import scanner
from app.config import settings
from app.market.tick_quality import feed_quality

# Scan tiers (Part S). Tick-level fields are recomputed on demand from cached
# ticks; the candle-derived work is memoised per instrument on the timestamp of
# the latest 1-minute bar, so a 50-name sweep costs one pass per new bar rather
# than one per request.
_RESULT_TTL = 2.0

_lock = threading.Lock()
_cache: dict[str, tuple[float, int, scanner.ScanResult]] = {}
_sweep: dict[str, object] = {"ts": 0.0, "rows": []}

# Part R: scanner -> engine handoff log. Records what the scanner ranked and
# what the engine subsequently decided, so it can later be MEASURED whether
# scanner-selected instruments produce better setups. Nothing reads it back
# into a trading decision.
_HANDOFF_MAX = 2000
_handoff: deque[dict] = deque(maxlen=_HANDOFF_MAX)


def _exchange_open(instrument: str) -> bool:
    if settings.ignore_market_hours:
        return True
    from app.market.instruments import get_spec

    try:
        exchange = get_spec(instrument).exchange
    except Exception:
        return True
    is_open, _ = settings.market_session(time.time(), exchange)
    return is_open


def _option_inputs(st) -> dict:
    """ATM option liquidity/spread from the cached chain. All optional.

    Anything the feed does not genuinely provide stays None/UNKNOWN — Phase 3
    established that the stored OI is simulator output, so OI is never treated
    as known here unless a real broker feed supplied it.
    """
    out: dict = {
        "option_liquidity": scanner.UNKNOWN,
        "option_spread_pct": None,
        "atm_premium": None,
        "oi_known": False,
        "relative_oi": None,
    }
    try:
        chain = st.provider.option_chain()
        spot = st.provider.futures_price()
    except Exception:
        return out
    if not chain or not spot:
        return out
    atm = min(chain, key=lambda q: abs(q.strike - spot))
    out["atm_premium"] = atm.premium
    if atm.bid and atm.ask and atm.ask > 0 and atm.bid > 0:
        mid = (atm.bid + atm.ask) / 2.0
        if mid > 0:
            out["option_spread_pct"] = round((atm.ask - atm.bid) / mid * 100.0, 3)
    real_feed = settings.data_provider.lower() not in ("simulated", "sim", "demo")
    vols = [q.volume for q in chain if q.volume]
    if real_feed and vols:
        v = atm.volume
        med = sorted(vols)[len(vols) // 2]
        if med > 0:
            out["option_liquidity"] = (
                scanner.LIQUID if v >= med * 1.5
                else scanner.MODERATE if v >= med * 0.5
                else scanner.THIN
            )
    return out


def build_input(st) -> scanner.ScanInput:
    """Assemble one instrument's scan input from in-memory state only."""
    from app.market.instruments import get_spec

    inst = st.instrument
    spec = get_spec(inst)
    try:
        candles = st.provider.futures_candles(90)
        ltp = st.provider.futures_price() or None
    except Exception:
        candles, ltp = [], None
    # Feed quality is keyed by the provider's scrip-master root (what the tick
    # carries), which is not always the registry key (e.g. BAJAJAUTO ->
    # BAJAJ-AUTO), so translate rather than silently reporting NO_DATA.
    fq = feed_quality.snapshot(spec.symbol)
    opt = _option_inputs(st)
    return scanner.ScanInput(
        instrument=inst,
        display=spec.display,
        exchange=spec.exchange,
        ltp=ltp,
        candles=tuple(candles),
        data_age_ms=fq.get("last_tick_age_ms"),
        freshness=fq.get("state", scanner.NO_DATA),
        data_quality_score=fq.get("data_quality_score"),
        ticks_per_second=fq.get("ticks_per_second"),
        market_open=_exchange_open(inst),
        **opt,
    )


def scan_instrument(st, weights: dict[str, float] | None = None) -> scanner.ScanResult:
    """Scan one instrument, memoised on its latest bar (Part S tiering)."""
    inst = st.instrument
    inp = build_input(st)
    bar_ts = int(inp.candles[-1].time) if inp.candles else 0
    now = time.monotonic()
    if weights is None:
        with _lock:
            hit = _cache.get(inst)
        if hit and hit[1] == bar_ts and (now - hit[0]) < _RESULT_TTL:
            return hit[2]
    res = scanner.scan_one(inp, weights)
    if weights is None:
        with _lock:
            _cache[inst] = (now, bar_ts, res)
    return res


def sweep(weights: dict[str, float] | None = None,
          force: bool = False) -> list[scanner.ScanResult]:
    """Scan every warm instrument. Cheap enough to call from a request."""
    from app.state import registry

    now = time.monotonic()
    if not force and weights is None:
        with _lock:
            if _sweep["rows"] and (now - float(_sweep["ts"])) < _RESULT_TTL:
                return list(_sweep["rows"])  # type: ignore[arg-type]
    rows = [scan_instrument(st, weights) for st in registry.active()]
    ranked = scanner.rank(rows)
    if weights is None:
        with _lock:
            _sweep["ts"] = now
            _sweep["rows"] = ranked
    return ranked


def record_handoff(ranked: list[scanner.ScanResult], top_n: int) -> list[dict]:
    """Part R: log the top-N handoff and pair it with the engine's own verdict.

    The engine verdict is read from the ALREADY-COMPUTED snapshot cache; the
    scanner never asks the engine to run, and nothing here feeds back into a
    decision.
    """
    from app.main import hub

    out: list[dict] = []
    ts = time.time()
    for i, r in enumerate(ranked[:top_n], start=1):
        snap = hub.latest.get(r.instrument)
        decision_score = None
        final_signal = None
        if snap is not None:
            d = getattr(snap, "decision", None)
            if d is not None:
                decision_score = getattr(d, "signal_strength", None)
                sig = getattr(d, "signal", None)
                final_signal = getattr(sig, "value", None)
        row = {
            "ts": int(ts),
            "instrument": r.instrument,
            "scanner_rank": i,
            "scanner_score": r.opportunity_score,
            "scanner_state": r.classification,
            "scanner_verdict": r.verdict,
            "decision_score": (
                round(float(decision_score), 1) if decision_score is not None else None
            ),
            "final_signal": final_signal,
        }
        out.append(row)
        _handoff.append(row)
    return out


def handoff_log(limit: int = 200) -> list[dict]:
    return list(_handoff)[-limit:]


def reset() -> None:
    """Test hook: clear caches so a scan is recomputed deterministically."""
    with _lock:
        _cache.clear()
        _sweep["ts"] = 0.0
        _sweep["rows"] = []
    _handoff.clear()
