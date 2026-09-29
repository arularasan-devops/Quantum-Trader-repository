"""Realistic simulated MCX Crude Oil feed.

Generates intraday futures ticks via a regime-switching geometric Brownian
motion with occasional shocks (to exercise breakout / crash logic), builds a
synthetic option chain priced with Black-Scholes (premiums, IV, Greeks, OI,
volume), and emits a stream of mock macro / geopolitical / inventory news.

This is a *development* feed. It is deterministic when QT_SIM_SEED != 0 so
runs are reproducible for testing the decision engine.
"""
from __future__ import annotations

import random
import time

from app.config import settings
from app.market import options
from app.market.instruments import DEFAULT_INSTRUMENT, InstrumentSpec, get_spec
from app.market.provider import MarketDataProvider
from app.market.tick_quality import feed_quality
from app.models import Candle, NewsItem, NewsSentiment, OptionQuote, OptionType

_NEWS_TEMPLATES = [
    ("Reuters", "geopolitical", "Middle East tensions escalate as tanker traffic disrupted near Hormuz", 0.7),
    ("Reuters", "geopolitical", "Ceasefire talks progress, easing supply-risk premium", -0.5),
    ("EIA", "inventory", "US crude inventories drew {n}M barrels vs {e}M expected", 0.6),
    ("EIA", "inventory", "US crude inventories built {n}M barrels, larger than expected", -0.6),
    ("API", "inventory", "API reports surprise crude draw of {n}M barrels", 0.5),
    ("OPEC", "opec", "OPEC+ signals extension of production cuts", 0.65),
    ("OPEC", "opec", "OPEC+ considers raising output quotas next quarter", -0.55),
    ("Federal Reserve", "fed", "Fed holds rates; dollar softens, supporting crude", 0.35),
    ("Federal Reserve", "fed", "Hawkish Fed tone lifts USD index, pressuring crude", -0.4),
    ("Moneycontrol", "macro", "China demand outlook improves on stimulus package", 0.45),
    ("Investing", "macro", "Weak global PMI data weighs on demand expectations", -0.35),
    ("Trading Economics", "macro", "Brent-WTI spread widens amid freight cost spike", 0.25),
]


class SimulatedProvider(MarketDataProvider):
    def __init__(self, spec: InstrumentSpec | None = None) -> None:
        self._spec = spec or get_spec(DEFAULT_INSTRUMENT)
        self._rng = random.Random(settings.sim_seed or None)
        self._price = self._spec.start_price
        self._now = int(time.time())
        self._tick = 0
        self._regime = "trend_up"
        self._regime_ticks = 0
        # per-minute candles for the future
        self._fut_candles: list[Candle] = []
        self._cur_candle: dict | None = None
        # option candle history keyed by symbol
        self._opt_candles: dict[str, list[Candle]] = {}
        self._oi: dict[str, int] = {}
        self._news: list[NewsItem] = []
        # Warm up with enough ticks to build ~300 one-minute candles so every
        # indicator (incl. EMA50 / ADX) has full history from the first tick.
        warmup_ticks = int(300 * settings.candle_interval_seconds / settings.tick_interval_seconds)
        opt_seed_from = warmup_ticks - int(240 * settings.candle_interval_seconds / settings.tick_interval_seconds)
        for n in range(warmup_ticks):
            self._advance_price()
            # Seed recent option-premium candles so the option chart has history.
            if n >= opt_seed_from:
                self.option_chain()
        self._maybe_news(force=True)

    # ----- price process -----
    def _pick_regime(self) -> None:
        self._regime = self._rng.choices(
            ["trend_up", "trend_down", "range", "volatile", "shock"],
            weights=[26, 26, 30, 15, 3],
        )[0]
        self._regime_ticks = self._rng.randint(45, 180)

    def _advance_price(self) -> None:
        if self._regime_ticks <= 0:
            self._pick_regime()
        self._regime_ticks -= 1
        dt = settings.tick_interval_seconds / (252 * 6.25 * 3600)
        vol = self._spec.annual_vol
        drift = 0.0
        if self._regime == "trend_up":
            drift = 0.9 * vol
        elif self._regime == "trend_down":
            drift = -0.9 * vol
        elif self._regime == "volatile":
            vol *= 2.4
        elif self._regime == "shock":
            vol *= 6.0
            drift = self._rng.choice([-1, 1]) * 5.0 * vol
        shock = self._rng.gauss(0, 1)
        ret = (drift - 0.5 * vol * vol) * dt + vol * (dt ** 0.5) * shock
        self._price = max(self._spec.start_price * 0.1, self._price * (1.0 + ret))
        self._now += int(settings.tick_interval_seconds)
        self._roll_candle()
        self._tick += 1

    def _roll_candle(self) -> None:
        bucket = self._now - (self._now % settings.candle_interval_seconds)
        vol_unit = self._rng.randint(80, 260)
        if self._regime in ("volatile", "shock"):
            vol_unit *= 3
        if self._cur_candle is None or self._cur_candle["time"] != bucket:
            if self._cur_candle is not None:
                self._fut_candles.append(Candle(**self._cur_candle))
                self._fut_candles = self._fut_candles[-600:]
            self._cur_candle = {
                "time": bucket,
                "open": self._price,
                "high": self._price,
                "low": self._price,
                "close": self._price,
                "volume": vol_unit,
            }
        else:
            c = self._cur_candle
            c["high"] = max(c["high"], self._price)
            c["low"] = min(c["low"], self._price)
            c["close"] = self._price
            c["volume"] += vol_unit

    # ----- news -----
    def _maybe_news(self, force: bool = False) -> None:
        # Realistic cadence: MCX crude sees only a handful of headlines a
        # session, so a high-impact item should be occasional — not constant
        # (which would otherwise pin the engine in NEWS_MODE all day).
        if not force and self._rng.random() > 0.0035:
            return
        src, cat, tmpl, impact = self._rng.choice(_NEWS_TEMPLATES)
        n = self._rng.randint(1, 6)
        e = max(0, n - self._rng.randint(0, 3))
        headline = tmpl.format(n=n, e=e)
        sent = (
            NewsSentiment.BULLISH if impact > 0.15 else NewsSentiment.BEARISH if impact < -0.15 else NewsSentiment.NEUTRAL
        )
        self._news.insert(0, NewsItem(time=self._now, source=src, headline=headline, category=cat, sentiment=sent, impact=impact))
        self._news = self._news[:25]

    # ----- provider interface -----
    def step(self) -> None:
        self._advance_price()
        self._maybe_news()
        # Record the synthetic tick so feed-age/quality reporting works offline
        # too. Labelled "simulated" so a demo age is never mistaken for a broker
        # latency measurement.
        feed_quality.record_tick(
            self._spec.symbol, "sim", ltp=self._price, source="simulated",
        )

    def futures_price(self) -> float:
        return round(self._price, 1)

    def futures_candles(self, limit: int = 240) -> list[Candle]:
        out = list(self._fut_candles)
        if self._cur_candle is not None:
            out = out + [Candle(**self._cur_candle)]
        return out[-limit:]

    def futures_book(self) -> dict | None:
        """The synthetic futures contract, with NO depth.

        Deliberately no bid/ask, for the same reason the simulated option chain
        quotes none: a fabricated spread would make the vehicle comparison read
        GREEN on a demo feed, and the one thing that study must not do is invent
        the number it exists to measure. The row is still returned so the capture
        path is exercised offline and reports as SPREAD_UNMEASURED.
        """
        return {
            "symbol": f"{self._spec.symbol}FUT",
            "expiry": None,
            "days_to_expiry": int(self._spec.days_to_expiry),
            "lot_size": int(self._spec.lot_size) or None,
            "exchange": self._spec.exchange,
            "ltp": round(self._price, 1),
            "bid": None,
            "ask": None,
            "oi": None,
            "volume": None,
            "feed_age_sec": 0.0,
            "quote_ts": float(self._now),
            "source": "simulated",
        }

    def _strikes(self) -> list[float]:
        step = self._spec.strike_step
        atm = round(self._price / step) * step
        n = settings.strikes_each_side
        return [atm + i * step for i in range(-n, n + 1)]

    def option_chain(self) -> list[OptionQuote]:
        r = settings.risk_free_rate
        t = self._spec.days_to_expiry / 365.0
        chain: list[OptionQuote] = []
        for strike in self._strikes():
            for is_call in (True, False):
                otype = OptionType.CALL if is_call else OptionType.PUT
                # skew: OTM puts richer (typical crude fear skew)
                moneyness = (strike - self._price) / self._price
                sigma = self._spec.annual_vol * (1.0 + 0.6 * abs(moneyness) + (0.1 if not is_call else 0.0))
                prem = options.price(self._price, strike, r, sigma, t, is_call)
                g = options.greeks(self._price, strike, r, sigma, t, is_call)
                sym = self._opt_symbol(strike, otype)
                base_oi = int(max(200, 9000 * (1.0 - min(0.95, abs(moneyness) * 6))))
                oi = self._oi.get(sym, base_oi)
                oi_change = int(self._rng.gauss(0, base_oi * 0.03))
                oi = max(50, oi + oi_change)
                self._oi[sym] = oi
                q = OptionQuote(
                    symbol=sym,
                    strike=strike,
                    option_type=otype,
                    premium=round(prem, 1),
                    iv=round(sigma, 4),
                    delta=g["delta"],
                    gamma=g["gamma"],
                    theta=g["theta"],
                    vega=g["vega"],
                    oi=oi,
                    oi_change=oi_change,
                    volume=int(max(0, self._rng.gauss(base_oi * 0.4, base_oi * 0.1))),
                    # The contract multiplier is the one field a demo feed can
                    # state honestly: it is a published property of the contract,
                    # not a price. It still cannot make these rows evidence —
                    # they quote no book and stay SIMULATED_BOOK_NOT_EVIDENCE.
                    lot_size=int(self._spec.lot_size) or None,
                )
                chain.append(q)
                self._record_opt_candle(sym, prem)
        return chain

    def _opt_symbol(self, strike: float, otype: OptionType) -> str:
        return f"{self._spec.symbol}{int(strike)}{otype.value}"

    def _record_opt_candle(self, sym: str, prem: float) -> None:
        bucket = self._now - (self._now % settings.candle_interval_seconds)
        hist = self._opt_candles.setdefault(sym, [])
        if hist and hist[-1].time == bucket:
            c = hist[-1]
            c.high = max(c.high, prem)
            c.low = min(c.low, prem)
            c.close = prem
            c.volume += self._rng.randint(5, 40)
        else:
            hist.append(Candle(time=bucket, open=prem, high=prem, low=prem, close=prem, volume=self._rng.randint(5, 40)))
            self._opt_candles[sym] = hist[-600:]

    def option_candles(self, symbol: str, limit: int = 240) -> list[Candle]:
        return self._opt_candles.get(symbol, [])[-limit:]

    def news(self) -> list[NewsItem]:
        return list(self._news)
