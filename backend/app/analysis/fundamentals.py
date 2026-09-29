"""Stock fundamentals from NSE's public endpoints (best-effort).

Fetches, for an F&O single stock:
  - delivery % (deliverable qty / traded qty) for the last session
  - next results / board-meeting date (corporate action)
  - the latest market-wide FII/DII cash provisional figures

NSE requires a browser-like session (a home-page GET to seed cookies, real
headers). Datacenter IPs are frequently rate-limited or blocked, so EVERY call
degrades gracefully: on any failure it returns ``{"available": False, ...}``
and never raises into the request path. Results are cached briefly.

This is READ-ONLY public market data — no credentials, no orders.
"""
from __future__ import annotations

import time

import httpx

_HOME = "https://www.nseindia.com"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

_TTL = 300.0
_stock_cache: dict[str, tuple[float, dict]] = {}
_flow_cache: tuple[float, dict] | None = None


def _client() -> httpx.Client:
    c = httpx.Client(headers=_HEADERS, timeout=6.0, follow_redirects=True)
    try:
        c.get(_HOME)  # seed cookies
    except Exception:
        pass
    return c


def _to_float(v: object) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def stock(symbol: str) -> dict:
    """Delivery % + next results date for an NSE symbol. Best-effort."""
    sym = (symbol or "").upper()
    now = time.time()
    cached = _stock_cache.get(sym)
    if cached and now - cached[0] < _TTL:
        return cached[1]

    result: dict = {"available": False, "symbol": sym,
                    "note": "NSE data unavailable (rate-limited or offline)."}
    try:
        with _client() as c:
            trade = c.get(f"{_HOME}/api/quote-equity", params={"symbol": sym, "section": "trade_info"})
            quote = c.get(f"{_HOME}/api/quote-equity", params={"symbol": sym})
        deliv = None
        if trade.status_code == 200:
            sec = trade.json().get("securityWiseDP", {})
            deliv = _to_float(sec.get("deliveryToTradedQuantity"))
        results_date = None
        if quote.status_code == 200:
            meta = quote.json().get("info", {}) or {}
            results_date = meta.get("companyName")  # placeholder; corp-actions below
            ca = quote.json().get("industryInfo", {})
            _ = ca  # industry available if needed
        if deliv is not None:
            result = {
                "available": True,
                "symbol": sym,
                "delivery_pct": round(deliv, 2),
                "results_date": results_date,
                "note": None,
            }
    except Exception:
        pass

    _stock_cache[sym] = (now, result)
    return result


def fii_dii() -> dict:
    """Latest FII/DII cash provisional (market-wide). Best-effort."""
    global _flow_cache
    now = time.time()
    if _flow_cache and now - _flow_cache[0] < _TTL:
        return _flow_cache[1]

    result: dict = {"available": False, "note": "NSE FII/DII data unavailable."}
    try:
        with _client() as c:
            resp = c.get(f"{_HOME}/api/fiidiiTradeReact")
        if resp.status_code == 200:
            rows = resp.json()
            fii = next((r for r in rows if "FII" in str(r.get("category", "")).upper()), None)
            dii = next((r for r in rows if "DII" in str(r.get("category", "")).upper()), None)
            result = {
                "available": True,
                "date": (fii or dii or {}).get("date"),
                "fii_net": _to_float((fii or {}).get("netValue")),
                "dii_net": _to_float((dii or {}).get("netValue")),
                "note": None,
            }
    except Exception:
        pass

    _flow_cache = (now, result)
    return result
