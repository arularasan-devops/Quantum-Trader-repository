"""Groww live NSE/BSE equity + index option provider.

Implements :class:`MarketDataProvider` against Groww's official Trading API
(`growwapi` SDK) so NSE/BSE index and single-stock option instruments can run on
a **second, independent** broker account while Angel One keeps the MCX
commodities. Splitting the instruments across two accounts means neither feed is
overloaded (fewer tokens/quotes each), which is what keeps the live data fast.

Enable with ``QT_ENABLE_GROWW=true`` and supply credentials as environment
variables (never hard-code, never commit). Groww offers two API-key types — this
supports both, plus a ready token:
    GROWW_API_KEY          your Groww Trading-API key (both flows)
    GROWW_API_SECRET       API secret — for an "approval" API key (most common)
    GROWW_TOTP_SECRET      base32 TOTP seed — for a "TOTP" API key
    GROWW_ACCESS_TOKEN     (optional) a ready daily access token; if set it is
                           used directly and the key/secret are not needed

Honest limitations:
* Groww serves NSE (NFO) and BSE (BFO) derivatives only — MCX commodities stay
  on Angel One.
* This provider polls Groww's REST live-data endpoints (LTP / option chain).
  Because Groww carries only the equity/index names on its own rate budget, the
  watched instrument still refreshes about once a second — independent of Angel.
* Must be validated during market hours with your own Groww token; response
  field names are parsed defensively and any gap is surfaced via diagnostics.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import time
from dataclasses import dataclass

from app.config import settings
from app.market import options
from app.market.provider import MarketDataProvider
from app.models import Candle, NewsItem, OptionQuote, OptionType

_log = logging.getLogger("app.market.groww")

# Our instrument-registry exchange codes -> Groww exchange codes.
_EXCHANGE_MAP = {"NFO": "NSE", "BFO": "BSE", "NSE": "NSE", "BSE": "BSE"}

# Min seconds between Groww REST live-data polls for one instrument. Groww's
# account is separate from Angel's, so this budget is independent; ~1s keeps the
# watched instrument near real time without tripping Groww's own rate limits.
_QUOTE_MIN_INTERVAL = 1.0
# Historical candle calls are heavier; space them per instrument.
_CANDLE_MIN_INTERVAL = 60.0


@dataclass
class _GrowwOption:
    trading_symbol: str
    strike: float
    opt_type: OptionType
    expiry: dt.date
    lot_size: int


def _to_epoch(value: object) -> int | None:
    """Groww candle timestamps arrive as epoch seconds/millis or an ISO string."""
    if isinstance(value, (int, float)):
        v = int(value)
        return v // 1000 if v > 10_000_000_000 else v
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            v = int(text)
            return v // 1000 if v > 10_000_000_000 else v
        try:
            return int(dt.datetime.fromisoformat(text).timestamp())
        except ValueError:
            return None
    return None


def _to_candles(rows: list) -> list[Candle]:
    """Parse Groww's candle rows ([time, open, high, low, close, volume])."""
    out: list[Candle] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        ts = _to_epoch(row[0])
        if ts is None:
            continue
        try:
            out.append(Candle(
                time=ts,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5] or 0),
            ))
        except (ValueError, TypeError):
            continue
    return out


# --- one shared Groww login for the whole process ---------------------------
_client_lock = threading.Lock()
_client: object | None = None
_client_built = False


def _get_client() -> object | None:
    """Log in once and reuse the GrowwAPI client across all Groww instruments."""
    global _client, _client_built
    with _client_lock:
        if _client_built:
            return _client
        _client_built = True
        try:
            from growwapi import GrowwAPI

            token = os.environ.get("GROWW_ACCESS_TOKEN")
            if not token:
                api_key = os.environ.get("GROWW_API_KEY")
                api_secret = os.environ.get("GROWW_API_SECRET")
                totp_secret = os.environ.get("GROWW_TOTP_SECRET")
                if not api_key or not (api_secret or totp_secret):
                    raise RuntimeError(
                        "Groww enabled but missing credentials: set GROWW_API_KEY "
                        "plus GROWW_API_SECRET (approval key) or GROWW_TOTP_SECRET "
                        "(TOTP key) — or a ready GROWW_ACCESS_TOKEN."
                    )
                if api_secret:
                    # "Approval" API key: SDK signs a checksum from the secret.
                    token = GrowwAPI.get_access_token(api_key, secret=api_secret)
                else:
                    # "TOTP" API key: current 6-digit code from the base32 seed.
                    import pyotp

                    token = GrowwAPI.get_access_token(
                        api_key, totp=pyotp.TOTP(totp_secret).now()
                    )
            _client = GrowwAPI(token)
        except Exception as exc:
            _log.warning("Groww login failed, its instruments fall back: %s", exc)
            _client = None
        return _client


class GrowwProvider(MarketDataProvider):
    """NSE/BSE index + single-stock options via Groww's Trading API."""

    def __init__(self, spec=None) -> None:
        self._root = (spec.symbol if spec else None) or "NIFTY"
        self._display = (spec.display if spec else None) or self._root
        reg_exchange = (spec.exchange if spec else None) or "NFO"
        self._exchange = _EXCHANGE_MAP.get(reg_exchange, "NSE")
        self._strike_step = spec.strike_step if spec else settings.strike_step
        self._lot_size = spec.lot_size if spec else settings.lot_size

        self._client = _get_client()

        self._spot: float = 0.0
        self._options: list[_GrowwOption] = []
        self._expiry: dt.date | None = None
        self._quotes: dict[str, dict] = {}  # trading_symbol -> {ltp, oi, volume}
        self._fut_candles: list[Candle] = []
        self._opt_candles: dict[str, list[Candle]] = {}
        self._last_quote_at = 0.0
        self._last_candles_at = 0.0
        self._diag = ""

        self._segments()
        self._load_contracts()
        self._refresh_candles(blocking=True)
        self._last_candles_at = time.time()
        self.step()

    # ------------------------------------------------------------ SDK constants
    def _segments(self) -> None:
        from growwapi import GrowwAPI

        self._seg_fno = GrowwAPI.SEGMENT_FNO
        self._seg_cash = GrowwAPI.SEGMENT_CASH

    # ------------------------------------------------------- instrument setup
    def _nearest_expiry(self) -> dt.date | None:
        if self._client is None:
            return None
        try:
            resp = self._client.get_expiries(self._exchange, self._root)
        except Exception as exc:
            self._diag = f"{self._root}/{self._exchange}: get_expiries failed — {exc}"
            return None
        raw = resp.get("expiries") or resp.get("expiry_dates") or resp.get("data") or []
        today = dt.date.today()
        dates: list[dt.date] = []
        for item in raw:
            text = item if isinstance(item, str) else (
                item.get("expiry") or item.get("expiry_date") if isinstance(item, dict) else None
            )
            if not text:
                continue
            try:
                d = dt.datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if d >= today:
                dates.append(d)
        return min(dates) if dates else None

    def _load_contracts(self) -> None:
        if self._client is None:
            return
        expiry = self._nearest_expiry()
        if expiry is None:
            if not self._diag:
                self._diag = f"{self._root}/{self._exchange}: no expiries returned"
            return
        self._expiry = expiry
        try:
            resp = self._client.get_contracts(
                self._exchange, self._root, expiry.strftime("%Y-%m-%d")
            )
        except Exception as exc:
            self._diag = f"{self._root}/{self._exchange}: get_contracts failed — {exc}"
            return
        rows = resp.get("contracts") or resp.get("data") or []
        parsed: list[_GrowwOption] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ts = row.get("trading_symbol") or row.get("tradingsymbol") or row.get("groww_symbol")
            opt_raw = str(
                row.get("option_type") or row.get("instrument_type") or row.get("type") or ""
            ).upper()
            strike_raw = row.get("strike_price") or row.get("strike") or 0
            if not ts or opt_raw not in ("CE", "PE", "CALL", "PUT"):
                continue
            try:
                strike = float(strike_raw)
            except (ValueError, TypeError):
                continue
            opt_type = OptionType.CALL if opt_raw in ("CE", "CALL") else OptionType.PUT
            lot_raw = row.get("lot_size") or row.get("lotsize") or self._lot_size
            try:
                lot = int(float(lot_raw))
            except (ValueError, TypeError):
                lot = self._lot_size
            parsed.append(_GrowwOption(str(ts), strike, opt_type, expiry, lot))
        self._options = parsed
        if not parsed and not self._diag:
            self._diag = f"{self._root}/{self._exchange}: no option contracts parsed"

    # ----------------------------------------------------------- live pulls
    def _atm_options(self) -> list[_GrowwOption]:
        if not self._options:
            return []
        spot = self._spot or self._median_strike()
        step = self._strike_step
        atm = round(spot / step) * step
        n = settings.strikes_each_side
        lo, hi = atm - n * step, atm + n * step
        return [o for o in self._options if lo <= o.strike <= hi]

    def _median_strike(self) -> float:
        strikes = sorted(o.strike for o in self._options)
        return strikes[len(strikes) // 2] if strikes else 0.0

    def _exch_symbol(self, trading_symbol: str) -> str:
        return f"{self._exchange}_{trading_symbol}"

    def _refresh_quotes(self) -> None:
        if self._client is None:
            return
        # Underlying spot (index/cash) drives the ATM window and structure.
        under = self._exch_symbol(self._root)
        try:
            resp = self._client.get_ltp((under,), segment=self._seg_cash)
            price = self._extract_ltp(resp, under, self._root)
            if price and price > 0:
                self._spot = price
        except Exception as exc:
            # Surface the real reason (e.g. 403 = the API key is missing the
            # Live-Data scope) instead of silently showing spot 0.
            self._diag = f"{self._root}/{self._exchange}: live quote failed — {exc}"

        atm = self._atm_options()
        if not atm:
            return
        symbols = tuple(self._exch_symbol(o.trading_symbol) for o in atm)
        try:
            resp = self._client.get_ltp(symbols, segment=self._seg_fno)
        except Exception:
            return
        for o in atm:
            key = self._exch_symbol(o.trading_symbol)
            price = self._extract_ltp(resp, key, o.trading_symbol)
            if price is None:
                continue
            existing = self._quotes.get(o.trading_symbol, {})
            self._quotes[o.trading_symbol] = {
                "ltp": price,
                "oi": existing.get("oi", 0),
                "volume": existing.get("volume", 0),
            }

    @staticmethod
    def _extract_ltp(resp: object, exch_key: str, plain_symbol: str) -> float | None:
        """Groww's LTP payload maps 'EXCHANGE_SYMBOL' -> price; accept a few shapes."""
        if not isinstance(resp, dict):
            return None
        for key in (exch_key, plain_symbol):
            val = resp.get(key)
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, dict):
                inner = val.get("ltp") or val.get("last_price")
                if isinstance(inner, (int, float)):
                    return float(inner)
        return None

    def _refresh_candles(self, *, blocking: bool) -> None:
        if self._client is None:
            return
        start, end = _candle_window()
        try:
            resp = self._client.get_historical_candle_data(
                trading_symbol=self._root,
                exchange=self._exchange,
                segment=self._seg_cash,
                start_time=start,
                end_time=end,
                interval_in_minutes=1,
            )
        except Exception as exc:
            if not self._fut_candles:
                self._diag = f"{self._root}/{self._exchange}: candles failed — {exc}"
            return
        rows = resp.get("candles") or resp.get("data") or []
        candles = _to_candles(rows)
        if candles:
            self._fut_candles = candles
            self._diag = ""
        elif not self._fut_candles:
            self._diag = f"{self._root}/{self._exchange}: 0 candles returned"

    # ------------------------------------------------- provider interface
    def step(self) -> None:
        now = time.time()
        if now - self._last_quote_at >= _QUOTE_MIN_INTERVAL:
            self._refresh_quotes()
            self._last_quote_at = now
        if now - self._last_candles_at >= _CANDLE_MIN_INTERVAL:
            self._refresh_candles(blocking=False)
            self._last_candles_at = now

    def feed_mode(self) -> str:
        # Groww live data arrives via ~1s REST polling on its own account budget.
        return "rest" if self._client is not None else "simulated"

    def feed_health(self) -> dict:
        # Groww has no push socket here, so the age of the last successful poll
        # IS the freshness — a failed or throttled poll ages visibly instead of
        # leaving a stale price looking live.
        age = round(time.time() - self._last_quote_at, 1) if self._last_quote_at else None
        return {
            "mode": self.feed_mode(),
            "live": False,
            "underlying_age_sec": age,
            "option_age_sec": age,
            "stale_after_sec": _QUOTE_MIN_INTERVAL,
            "note": f"Groww REST poll every ~{_QUOTE_MIN_INTERVAL:g}s (no push socket).",
        }

    def futures_price(self) -> float:
        if self._spot:
            return round(self._spot, 2)
        if self._fut_candles:
            return round(self._fut_candles[-1].close, 2)
        return 0.0

    def futures_candles(self, limit: int = 240) -> list[Candle]:
        return self._fut_candles[-limit:]

    def option_chain(self) -> list[OptionQuote]:
        r = settings.risk_free_rate
        t = self._time_to_expiry()
        chain: list[OptionQuote] = []
        for o in self._atm_options():
            q = self._quotes.get(o.trading_symbol)
            if not q or q["ltp"] <= 0 or self._spot <= 0:
                continue
            is_call = o.opt_type == OptionType.CALL
            iv = options.implied_vol(q["ltp"], self._spot, o.strike, r, t, is_call)
            g = options.greeks(self._spot, o.strike, r, iv, t, is_call)
            chain.append(OptionQuote(
                symbol=o.trading_symbol,
                strike=o.strike,
                option_type=o.opt_type,
                premium=round(q["ltp"], 2),
                iv=iv,
                delta=g["delta"],
                gamma=g["gamma"],
                theta=g["theta"],
                vega=g["vega"],
                oi=q["oi"],
                oi_change=0,
                volume=q["volume"],
                lot_size=int(o.lot_size or 0) or None,
            ))
        return chain

    def option_candles(self, symbol: str, limit: int = 240) -> list[Candle]:
        if self._client is None:
            return []
        cached = self._opt_candles.get(symbol)
        if cached:
            return cached[-limit:]
        start, end = _candle_window()
        try:
            resp = self._client.get_historical_candle_data(
                trading_symbol=symbol,
                exchange=self._exchange,
                segment=self._seg_fno,
                start_time=start,
                end_time=end,
                interval_in_minutes=1,
            )
        except Exception:
            return []
        candles = _to_candles(resp.get("candles") or resp.get("data") or [])
        if candles:
            self._opt_candles[symbol] = candles
        return candles[-limit:]

    def news(self) -> list[NewsItem]:
        return []

    def diagnostics(self) -> str:
        return self._diag

    def _time_to_expiry(self) -> float:
        if self._expiry is None:
            return settings.days_to_expiry / 365.0
        days = max(0.5, (self._expiry - dt.date.today()).days + 0.5)
        return days / 365.0


def _candle_window() -> tuple[str, str]:
    """Last ~48h as 'yyyy-MM-dd HH:mm:ss' strings for Groww's candle range API."""
    now = dt.datetime.now()
    start = now - dt.timedelta(hours=48)
    fmt = "%Y-%m-%d %H:%M:%S"
    return start.strftime(fmt), now.strftime(fmt)
