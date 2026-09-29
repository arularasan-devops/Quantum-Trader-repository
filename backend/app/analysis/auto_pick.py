"""Daily auto-pick — choose today's most active F&O stocks from the broker feed.

This selects *which* instruments the dashboard watches; it never influences the
frozen signal engine. Each trading day it asks Angel's ``gainersLosers`` market
API for the biggest derivative movers (by % price, both up and down — a strong
down-move is a PE opportunity), keeps only names we have specs for, and returns
the top ``count`` plus an always-on core list.

If the broker call is unavailable (simulated feed, no live login, market API
error) it falls back to the configured core so the app still works.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading

from app.market.instruments import REGISTRY, FNO_STOCKS

_log = logging.getLogger("quantum.autopick")

# datatype values Angel accepts for gainersLosers; we pull both directions.
_DATATYPES = ("PercPriceGainers", "PercPriceLosers")
_EXPIRY = "NEAR"

_lock = threading.Lock()
_cache_day: dt.date | None = None
_cache_result: dict | None = None


def _root_to_symbol() -> list[tuple[str, str]]:
    """(scrip-master root, our REGISTRY key) for every F&O stock, longest root
    first so 'BAJAJ-AUTO' matches before a shorter prefix would."""
    pairs = [(REGISTRY[k].symbol.upper(), k) for k in FNO_STOCKS]
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def _match_symbol(trading_symbol: str, pairs: list[tuple[str, str]]) -> str | None:
    ts = (trading_symbol or "").upper()
    for root, key in pairs:
        if ts.startswith(root):
            return key
    return None


def _movers_from_angel(count: int) -> list[dict]:
    """Return the biggest F&O movers (one dict per name: symbol, percent_change,
    direction, ltp), ranked by |%change|. Raises on any failure so the caller can
    fall back to the core list."""
    from app.market.angelone import _get_shards

    shards = _get_shards()
    smart = shards[0].smart
    pairs = _root_to_symbol()
    # key -> {"pct": signed %, "abs": magnitude, "ltp": float}
    scored: dict[str, dict[str, float]] = {}
    for datatype in _DATATYPES:
        resp = smart.gainersLosers({"datatype": datatype, "expirytype": _EXPIRY})
        rows = (resp or {}).get("data") or []
        for row in rows:
            sym = row.get("tradingSymbol") or row.get("tradingsymbol") or ""
            key = _match_symbol(sym, pairs)
            if key is None:
                continue
            raw = row.get("percentChange", row.get("percChange", 0.0))
            try:
                pct = float(raw)
            except (TypeError, ValueError):
                pct = 0.0
            try:
                ltp = float(row.get("ltp", row.get("lastTradedPrice", 0.0)) or 0.0)
            except (TypeError, ValueError):
                ltp = 0.0
            if abs(pct) > scored.get(key, {}).get("abs", 0.0):
                scored[key] = {"pct": pct, "abs": abs(pct), "ltp": ltp}
    ranked = sorted(scored, key=lambda k: scored[k]["abs"], reverse=True)[:count]
    return [
        {
            "symbol": k,
            "percent_change": round(scored[k]["pct"], 2),
            "direction": "UP" if scored[k]["pct"] >= 0 else "DOWN",
            "ltp": scored[k]["ltp"],
        }
        for k in ranked
    ]


def _core_list(core_csv: str) -> list[str]:
    return [s.strip().upper() for s in core_csv.replace(";", ",").split(",") if s.strip()]


def compute(count: int, core_csv: str) -> dict:
    """Compute today's universe: core + top movers (deduped). Returns a dict with
    the resulting ``universe`` and human-readable ``picks`` / ``source``."""
    core = [s for s in _core_list(core_csv) if s in REGISTRY]
    movers: list[dict] = []
    source = "broker"
    try:
        movers = _movers_from_angel(max(0, count))
    except Exception as exc:
        source = "core-only (movers unavailable)"
        _log.info("auto-pick movers unavailable, using core only: %s", exc)

    mover_syms = [m["symbol"] for m in movers]
    universe: list[str] = []
    for s in core + mover_syms:
        if s not in universe:
            universe.append(s)
    return {
        "universe": universe,
        "core": core,
        "movers": movers,
        "source": source,
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def get_daily(count: int, core_csv: str, force: bool = False) -> dict:
    """Cached once per calendar day (IST). ``force`` recomputes immediately."""
    global _cache_day, _cache_result
    today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)).date()
    with _lock:
        if force or _cache_result is None or _cache_day != today:
            _cache_result = compute(count, core_csv)
            _cache_day = today
        return _cache_result
