"""Angel One historical downloader for backtesting — READ-ONLY.

Pulls 1-minute (or any interval) candles for a single instrument token over a
long window by chunking the request under Angel's per-call row cap (~8000 rows)
and its strict historical rate limit (AB1021). Results are de-duplicated, sorted
and cached to a JSONL file so a backtest can be re-run offline without hitting
the API again.

This module NEVER places an order and NEVER touches the live engine. It is only
used by the offline backtest tooling.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time

import httpx

from app.models import Candle

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)

# Angel caps a single ONE_MINUTE getCandleData response at ~8000 rows. MCX trades
# ~09:00-23:30 IST (~870 1-min bars/day), so a 5-trading-day window stays well
# under the cap. Space calls out to respect the historical rate limit (AB1021).
_CHUNK_DAYS = 5
_MIN_INTERVAL = 3.0     # seconds between historical calls
_COOLDOWN = 20.0        # pause this long after a rate-limit response
_MAX_RETRIES = 6


def _parse_expiry(s: str) -> dt.date | None:
    try:
        return dt.datetime.strptime((s or "").strip().upper(), "%d%b%Y").date()
    except Exception:
        return None


def _is_rate_limited(text: str) -> bool:
    t = (text or "").lower()
    return (
        "ab1021" in t
        or "too many" in t
        or "exceeding access rate" in t
        or "access denied" in t
        or "parse the json" in t
    )


def login_smart():
    """Log in to Angel SmartAPI using SMARTAPI_* env vars. Returns the client."""
    import pyotp
    from SmartApi import SmartConnect

    key = os.environ["SMARTAPI_KEY"]
    client = os.environ["SMARTAPI_CLIENT_CODE"]
    pin = os.environ["SMARTAPI_PIN"]
    totp_secret = os.environ["SMARTAPI_TOTP_SECRET"]
    smart = SmartConnect(api_key=key)
    session = smart.generateSession(client, pin, pyotp.TOTP(totp_secret).now())
    if not session or not session.get("status"):
        raise RuntimeError(f"Angel login failed: {session}")
    return smart


def front_future_token(root: str, exchange: str) -> tuple[str, str, dt.date]:
    """Resolve the nearest-expiry (front-month) futures (token, symbol, expiry)
    for ``root`` on ``exchange`` from the scrip master."""
    scrip = httpx.get(SCRIP_MASTER_URL, timeout=120.0).json()
    today = dt.date.today()
    futs: list[tuple[dt.date, str, str]] = []
    for r in scrip:
        if r.get("name") != root:
            continue
        if (r.get("exch_seg") or "").upper() != exchange.upper():
            continue
        if not (r.get("instrumenttype") or "").upper().startswith("FUT"):
            continue
        exp = _parse_expiry(r.get("expiry", ""))
        if exp is not None:
            futs.append((exp, r.get("token"), r.get("symbol")))
    if not futs:
        raise RuntimeError(f"No {root} futures found on {exchange} in scrip master.")
    futs.sort()
    front = next((f for f in futs if f[0] >= today), futs[-1])
    exp, token, symbol = front
    return token, symbol, exp


def index_token(root: str, exchange: str) -> tuple[str, str]:
    """Resolve the spot INDEX (token, symbol) for ``root`` (e.g. NIFTY/BANKNIFTY
    on NSE, SENSEX on BSE). Indices keep years of daily history, unlike a single
    futures contract. Matches scrip-master rows that have no expiry and an
    index-style instrument type."""
    scrip = httpx.get(SCRIP_MASTER_URL, timeout=120.0).json()
    root_u = root.upper()
    cands: list[tuple[str, str]] = []
    for r in scrip:
        if (r.get("exch_seg") or "").upper() != exchange.upper():
            continue
        itype = (r.get("instrumenttype") or "").upper()
        if itype not in ("", "AMXIDX", "INDEX"):
            continue
        name = (r.get("name") or "").upper()
        sym = (r.get("symbol") or "").upper()
        # index rows carry no expiry; match on the root name/symbol
        if (r.get("expiry") or "").strip():
            continue
        if root_u in name or root_u in sym:
            cands.append((r.get("token"), r.get("symbol")))
    if not cands:
        raise RuntimeError(
            f"No {root} index found on {exchange}. Candidates need instrumenttype "
            "AMXIDX/INDEX and no expiry — check the scrip master."
        )
    # prefer the shortest symbol (the plain index, not a variant)
    cands.sort(key=lambda t: len(t[1] or ""))
    return cands[0]


def list_future_contracts(root: str, exchange: str) -> list[tuple[dt.date, str, str]]:
    """All (expiry, token, symbol) futures contracts for ``root``, sorted by
    expiry. Used to stitch a continuous front-month series for commodities that
    have no spot index (Crude, Natural Gas)."""
    scrip = httpx.get(SCRIP_MASTER_URL, timeout=120.0).json()
    futs: list[tuple[dt.date, str, str]] = []
    for r in scrip:
        if r.get("name") != root:
            continue
        if (r.get("exch_seg") or "").upper() != exchange.upper():
            continue
        if not (r.get("instrumenttype") or "").upper().startswith("FUT"):
            continue
        exp = _parse_expiry(r.get("expiry", ""))
        if exp is not None:
            futs.append((exp, r.get("token"), r.get("symbol")))
    futs.sort()
    return futs


def _fetch_chunk(smart, params: dict) -> list:
    cooldown = 0.0
    for _ in range(_MAX_RETRIES):
        if cooldown:
            time.sleep(cooldown)
        try:
            resp = smart.getCandleData(params)
        except Exception as exc:  # SmartConnect raises on the gateway rate-limit
            if _is_rate_limited(str(exc)):
                cooldown = _COOLDOWN
                continue
            raise
        if isinstance(resp, dict) and _is_rate_limited(str(resp.get("message"))):
            cooldown = _COOLDOWN
            continue
        return (resp or {}).get("data") or []
    return []


def download_candles(
    smart,
    token: str,
    exchange: str,
    interval: str,
    start: dt.date,
    end: dt.date,
    *,
    progress: bool = True,
) -> list[Candle]:
    """Download ``interval`` candles for ``token`` from ``start`` to ``end`` by
    chunking under the row cap and spacing calls for the rate limit."""
    out: dict[int, Candle] = {}
    cur = start
    last_call = 0.0
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=_CHUNK_DAYS), end)
        params = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": interval,
            "fromdate": cur.strftime("%Y-%m-%d 09:00"),
            "todate": chunk_end.strftime("%Y-%m-%d 23:59"),
        }
        wait = _MIN_INTERVAL - (time.time() - last_call)
        if wait > 0:
            time.sleep(wait)
        rows = _fetch_chunk(smart, params)
        last_call = time.time()
        for row in rows:
            # row = [iso_time, open, high, low, close, volume]
            ts = int(dt.datetime.fromisoformat(row[0]).timestamp())
            out[ts] = Candle(
                time=ts,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
        if progress:
            print(f"  {cur} .. {chunk_end}: +{len(rows)} rows (total {len(out)})", flush=True)
        cur = chunk_end + dt.timedelta(days=1)
    return [out[k] for k in sorted(out)]


def download_continuous_futures(
    smart,
    contracts: list[tuple[dt.date, str, str]],
    exchange: str,
    interval: str,
    start: dt.date,
    end: dt.date,
    *,
    progress: bool = True,
) -> list[Candle]:
    """Stitch a continuous FRONT-month series across many futures contracts.

    For each contract we keep only the bars while it was the front month — i.e.
    from the previous contract's expiry up to its own expiry — so the series has
    no overlap and always reflects the most-liquid contract. This is the honest
    way to get multi-year commodity history when there is no spot index."""
    contracts = [c for c in contracts if c[0] >= start]
    out: dict[int, Candle] = {}
    prev_expiry = start
    for exp, token, symbol in contracts:
        win_start = max(prev_expiry, start)
        win_end = min(exp, end)
        prev_expiry = exp
        if win_start > win_end:
            continue
        if progress:
            print(f" contract {symbol} (exp {exp}): {win_start}..{win_end}", flush=True)
        rows = download_candles(
            smart, token, exchange, interval, win_start, win_end, progress=progress
        )
        for c in rows:
            out[c.time] = c
        if exp >= end:
            break
    return [out[k] for k in sorted(out)]


def cache_path(data_dir: str, root: str, interval: str) -> str:
    return os.path.join(data_dir, "backtest", f"{root}_{interval}.jsonl")


def save_candles(path: str, candles: list[Candle]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for c in candles:
            fh.write(json.dumps(c.model_dump()) + "\n")


def load_candles(path: str) -> list[Candle]:
    if not os.path.exists(path):
        return []
    out: list[Candle] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(Candle(**json.loads(line)))
    return out
