"""Historical data downloader (Angel One SmartAPI).

Fetches historical 1-minute MCX futures and option candles over a date range and
stores them in the research store, so replay/analytics can run on REAL market
history. Requires Angel One credentials in the environment (see `.env.example`);
credentials are never logged or persisted by this module.

Honest scope: Angel One's `getCandleData` returns OHLCV only. Open Interest, IV
and Greeks are NOT available from the historical candle API, so those columns are
left NULL for imported history (they are captured live from the quote feed). This
is a limitation of the data source, not something to be fabricated.
"""
from __future__ import annotations

import datetime as dt
import time

from app.config import settings
from app.market.instruments import get_spec
from app.research.store import ResearchStore, store

_CHUNK_DAYS = 25  # Angel limits ONE_MINUTE history per request; page in chunks


def _parse_ts(raw: str) -> int:
    # Angel returns e.g. "2024-01-02T09:15:00+05:30"
    return int(dt.datetime.fromisoformat(raw).timestamp())


def _fetch_candles(smart, exchange: str, token: str,
                   frm: dt.datetime, to: dt.datetime,
                   interval: str = "ONE_MINUTE") -> list[dict]:
    out: list[dict] = []
    cursor = frm
    while cursor < to:
        chunk_end = min(cursor + dt.timedelta(days=_CHUNK_DAYS), to)
        try:
            resp = smart.getCandleData({
                "exchange": exchange,
                "symboltoken": token,
                "interval": interval,
                "fromdate": cursor.strftime("%Y-%m-%d %H:%M"),
                "todate": chunk_end.strftime("%Y-%m-%d %H:%M"),
            })
        except Exception as exc:  # pragma: no cover - network/credential dependent
            raise RuntimeError(f"getCandleData failed for token {token}: {exc}") from exc
        for row in ((resp or {}).get("data") or []):
            out.append({
                "ts": _parse_ts(row[0]),
                "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]),
                "volume": float(row[5]),
            })
        cursor = chunk_end
        time.sleep(max(0.4, settings.tick_interval_seconds))  # respect rate limits
    return out


def download(instrument: str, *, days: int = 120,
             include_options: bool = True,
             st: ResearchStore | None = None) -> dict:
    """Download `days` of history for `instrument` into the research store.

    This logs in to Angel One (via the existing provider) and pages 1-minute
    candles for the current futures contract and the near-ATM option strikes.
    """
    if settings.data_provider.lower() != "angelone":
        raise RuntimeError(
            "Historical download requires QT_DATA_PROVIDER=angelone and valid "
            "SmartAPI credentials. Refusing to run against the simulated feed."
        )
    st = st or store()
    from app.market.angelone import AngelOneProvider

    prov = AngelOneProvider(get_spec(instrument))
    smart = prov._smart
    exchange = prov._exchange
    to = dt.datetime.now()
    frm = to - dt.timedelta(days=days)

    fut_rows = _fetch_candles(smart, exchange, prov._future.token, frm, to)
    fut_written = st.insert_futures(instrument, fut_rows)

    opt_written = 0
    strikes_done = 0
    if include_options and prov._options:
        tokens = set(prov._atm_option_tokens())
        for o in prov._options:
            if o.token not in tokens:
                continue
            rows = _fetch_candles(smart, exchange, o.token, frm, to)
            otype = "CE" if (o.opt_type and o.opt_type.value == "CE") else "PE"
            opt_written += st.insert_options(instrument, [
                {**r, "strike": o.strike, "option_type": otype,
                 "oi": None, "oi_change": None, "iv": None,
                 "delta": None, "theta": None, "vega": None, "gamma": None}
                for r in rows
            ])
            strikes_done += 1

    return {
        "instrument": instrument,
        "backend": st.backend,
        "days": days,
        "futures_rows": fut_written,
        "option_rows": opt_written,
        "option_strikes": strikes_done,
        "note": "OHLCV imported; OI/IV/Greeks are NULL (not in Angel historical API).",
    }
