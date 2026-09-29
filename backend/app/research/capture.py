"""Capture market data into the research store.

Two uses:
* ``capture_from_provider`` — step any :class:`MarketDataProvider` forward and
  record futures bars + option-chain snapshots. With the simulated feed this
  produces a self-contained dataset to exercise replay/analytics end-to-end;
  with a live/broker provider it captures the real session.
* ``record_live_snapshot`` — called from the live tick loop to persist the
  current futures bar + chain as they arrive (used by shadow mode / validation).

Nothing here fabricates prices: it only stores whatever the provider returns.
"""
from __future__ import annotations

from app.config import settings
from app.models import Candle, OptionQuote
from app.research.store import (
    SOURCE_REAL,
    SOURCE_SIM,
    SOURCE_UNKNOWN,
    ResearchStore,
    store,
)


def current_source() -> str:
    """Provenance label for data captured from the CONFIGURED feed.

    Phase 3 discovered that every stored chain snapshot was simulator output,
    which is only discoverable by heuristic after the fact. Labelling at write
    time is the fix; an unrecognised provider stays UNKNOWN rather than being
    optimistically called real.
    """
    name = (settings.data_provider or "").lower()
    if name in ("simulated", "sim", "demo"):
        return SOURCE_SIM
    if name in ("angelone", "groww"):
        return SOURCE_REAL
    return SOURCE_UNKNOWN


def _chain_rows(ts: int, chain: list[OptionQuote],
                source: str | None = None) -> list[dict]:
    rows = []
    src = source or current_source()
    for q in chain:
        rows.append({
            "ts": ts,
            "strike": q.strike,
            "option_type": q.option_type.value,
            "open": q.premium, "high": q.premium, "low": q.premium, "close": q.premium,
            "volume": q.volume, "oi": q.oi, "oi_change": q.oi_change,
            "iv": q.iv, "delta": q.delta, "theta": q.theta, "vega": q.vega,
            "gamma": q.gamma,
            # Real top-of-book when the broker sent it; None (not 0) otherwise.
            "bid": q.bid, "ask": q.ask,
            "source": src,
        })
    return rows


def _future_row(candle: Candle) -> dict:
    return {
        "ts": int(candle.time),
        "open": candle.open, "high": candle.high, "low": candle.low,
        "close": candle.close, "volume": candle.volume,
        "oi": None, "oi_change": None,
        "vwap": None, "atr": None,
        "spread": round(candle.high - candle.low, 4),
    }


def record_live_snapshot(instrument: str, candle: Candle,
                         chain: list[OptionQuote], *,
                         st: ResearchStore | None = None,
                         source: str | None = None) -> tuple[int, int]:
    """Persist ONE completed live bar + its option chain into the research store.

    Called from the live tick loop so replay/analytics can later run on REAL
    captured market history. Idempotent per (instrument, ts): the store uses
    INSERT-OR-REPLACE, so re-recording the same bar just refreshes it. Returns
    (futures_rows_written, option_rows_written).
    """
    st = st or store()
    ts = int(candle.time)
    fut = st.insert_futures(instrument, [_future_row(candle)])
    opt = st.insert_options(instrument, _chain_rows(ts, chain, source)) if chain else 0
    return fut, opt


def capture_from_provider(instrument: str, bars: int, *,
                          st: ResearchStore | None = None) -> dict:
    """Step the simulated provider until ``bars`` distinct 1-minute bars have
    been recorded, storing each completed bar and the option chain at that bar.

    The simulated feed advances one tick per ``step`` and only completes a bar
    every ``candle_interval_seconds``; this loops enough times to collect the
    requested number of bars regardless of the tick/candle ratio.
    """
    from app.market.provider import build_provider

    st = st or store()
    prov = build_provider("simulated", instrument)
    seen_ts: set[int] = set()
    fut_written = opt_written = 0
    # generous iteration cap so a small tick/candle ratio can't loop forever
    max_iters = bars * max(2, int(settings.candle_interval_seconds /
                                  max(1.0, settings.tick_interval_seconds))) + 200

    for _ in range(max_iters):
        if len(seen_ts) >= bars:
            break
        prov.step()
        candles = prov.futures_candles(3)
        if not candles:
            continue
        # use the last COMPLETED bar (not the still-forming one) so OHLC is final
        last = candles[-2] if len(candles) >= 2 else candles[-1]
        ts = int(last.time)
        if ts in seen_ts:
            continue
        seen_ts.add(ts)
        fut_written += st.insert_futures(instrument, [_future_row(last)])
        chain = prov.option_chain()
        if chain:
            # This helper builds the simulated provider itself, so the rows are
            # simulator data regardless of what QT_DATA_PROVIDER says.
            opt_written += st.insert_options(
                instrument, _chain_rows(ts, chain, SOURCE_SIM)
            )

    return {
        "instrument": instrument,
        "backend": st.backend,
        "futures_rows": fut_written,
        "option_rows": opt_written,
        "futures_total": st.futures_count(instrument),
    }
