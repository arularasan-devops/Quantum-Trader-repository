"""Market-data provider abstraction.

Every concrete feed (simulated, Zerodha Kite, Upstox, Angel One, TrueData)
implements this interface. The decision engine and API only ever depend on
this contract, so switching to a live broker feed later requires **no** change
anywhere else in the application.
"""
from __future__ import annotations

import abc
import time

from app.ai import safety
from app.analysis import futures_feed_audit
from app.models import Candle, NewsItem, OptionQuote, OrderResult


class MarketDataProvider(abc.ABC):
    """Contract for a source of MCX Crude Oil futures + option-chain data."""

    def supports_trading(self) -> bool:
        """Whether this provider can place real orders. Data-only feeds
        (e.g. the simulated feed) return False and are paper-trade only."""
        return False

    def place_order(self, option_symbol: str, side: str, lots: int) -> OrderResult:
        """Place a real order (side = 'BUY' or 'SELL'). Providers that support
        trading override this. ``side`` refers to the option leg direction.

        Overrides MUST call ``app.ai.safety.assert_order_allowed`` first: it is
        the single kill switch that keeps real-money execution disabled while
        paper mode is on.
        """
        safety.assert_order_allowed("provider.place_order", option_symbol, side, lots)
        raise NotImplementedError("This provider does not support live orders.")

    def feed_mode(self) -> str:
        """How prices are currently arriving: 'push' (live WebSocket, fastest),
        'rest' (polled quotes, ~3s), or 'simulated'. Live providers override."""
        return "simulated"

    def feed_health(self) -> dict:
        """Freshness of the prices this provider is serving.

        Reported rather than inferred: a feed that has silently dropped from the
        push socket to the ~3s REST poll shows the same numbers on the board as
        a live one, and an illiquid contract that has stopped ticking shows the
        same numbers as a quiet one. Live providers override with real ages.
        """
        return {"mode": self.feed_mode(), "live": False, "note": "Simulated feed."}

    def futures_candles_live(self, limit: int = 240) -> list[Candle]:
        """Freshest available futures series, for the futures RESEARCH path.

        Separate from ``futures_candles`` on purpose: the production option
        engine's indicators read that one, and a feed-plumbing change must not
        alter what a production BUY is computed from. Feeds with nothing fresher
        than their history return the same series.
        """
        return self.futures_candles(limit)

    def futures_feed_trace(self) -> dict:
        """Phase 12A §1: the futures data path, hop by hop, for ONE contract.

        The futures research engine refuses on feed age, so "the feed is stale"
        has to be answerable with where the age came from — subscription, socket,
        REST fallback, cache or processing — instead of a single number that
        could have been produced by any of them. Feeds that cannot see their own
        path report ``NO_DATA``; nothing in production reads this.
        """
        candles = self.futures_candles_live(1)
        age = round(time.time() - candles[-1].time, 1) if candles else None
        return {
            "symbol": None,
            "token": None,
            "exchange": None,
            "expiry": None,
            "dte": None,
            "subscription_state": "UNKNOWN",
            "source": "REST" if candles else "NONE",
            "last_tick_at": None,
            "tick_age_sec": None,
            "exchange_bar_ts": candles[-1].time if candles else None,
            "cache_ts": None,
            "processing_ts": round(time.time(), 3),
            "age_seconds": age,
            "freshness": futures_feed_audit.state(age),
            "note": f"{type(self).__name__} does not trace its feed path.",
        }

    @abc.abstractmethod
    def step(self) -> None:
        """Advance the feed by one tick (pull latest data)."""

    @abc.abstractmethod
    def futures_price(self) -> float:
        ...

    @abc.abstractmethod
    def futures_candles(self, limit: int = 240) -> list[Candle]:
        ...

    @abc.abstractmethod
    def option_chain(self) -> list[OptionQuote]:
        ...

    @abc.abstractmethod
    def option_candles(self, symbol: str, limit: int = 240) -> list[Candle]:
        ...

    @abc.abstractmethod
    def news(self) -> list[NewsItem]:
        ...

    def day_ohlc(self, symbol: str | None = None) -> dict | None:
        """Official exchange day OHLC for the current session, so the dashboard
        can show the SAME day High/Low the broker app shows (not a value derived
        from a sliding candle window). ``symbol=None`` = the futures/underlying;
        otherwise a specific option symbol. Returns a dict with keys ``high``,
        ``low``, ``open`` and ``prev_close`` (any may be None), or None if the
        feed can't provide it (e.g. the simulated feed)."""
        return None

    def futures_contract(self) -> dict | None:
        """The futures contract this provider is actually quoting, or None.

        A futures plan has to name its own contract — symbol, expiry and lot size
        — rather than borrow the option leg's, because the two can sit on
        different expiries and a plan that cannot name what it would trade is not
        a plan. Keys: ``symbol``, ``expiry`` (ISO date str), ``lot_size``,
        ``exchange``. Feeds that cannot say return None.
        """
        return None

    def futures_book(self) -> dict | None:
        """The futures contract's two-sided book AT THIS INSTANT, or None.

        Separate from :meth:`futures_contract` (static contract meta) and from
        :meth:`futures_price` (one number): the vehicle question — futures or CE
        or PE for the same market read — cannot be answered from an LTP, because
        the whole comparison is about what each vehicle costs to enter and leave.
        A day of capture with 10,407 option rows produced 0 same-signal futures
        rows for exactly this reason, and the futures paper book has been charging
        a MODELLED spread rather than a quoted one.

        Keys, all optional except ``symbol``: ``symbol``, ``expiry`` (ISO date),
        ``days_to_expiry``, ``lot_size``, ``exchange``, ``ltp``, ``bid``, ``ask``,
        ``oi``, ``volume``, ``feed_age_sec``, ``quote_ts``. A feed that quotes no
        depth returns the row with ``bid``/``ask`` absent rather than fabricating
        them — an unmeasured spread must stay unmeasured.
        """
        return None

    def futures_book_chain(self) -> list[dict]:
        """Two-sided books for the near AND next futures contracts, near first.

        :meth:`futures_book` answers "what is the quoted contract worth"; this
        answers "what are the two contracts worth AT THE SAME INSTANT", which is
        the only form in which a calendar basis is a measurement rather than an
        inference. A feed that quotes only one contract returns just that one,
        and a contract with no depth keeps ``bid``/``ask`` absent: an unmeasured
        basis must stay unmeasured. Same keys as :meth:`futures_book`.
        """
        book = self.futures_book()
        return [book] if book else []

    def futures_contract_chain(self) -> list[dict]:
        """Every futures contract the feed knows about, nearest expiry first.

        The quoted contract (``futures_contract``) is the near month because that
        is where the price feed lives. The futures *research* engine needs the
        rest of the chain as well: on 25 Aug it refused 51 of 76 plans with
        ``EXPIRY_TOO_CLOSE`` because it could only ever score the near month in
        its final week, and the answer to that is to roll the research plan onto
        the next contract, not to loosen the expiry check. Same keys as
        ``futures_contract``. Feeds that cannot say return the quoted contract
        alone, so a caller never has to special-case an empty chain.
        """
        near = self.futures_contract()
        return [near] if near else []

    def nearest_expiry(self):
        """Nearest option-expiry date the feed is quoting, or None when the feed
        can't supply it (e.g. the simulated feed). Used to detect expiry day for
        the high-risk zero-to-hero sleeve. Return type: datetime.date | None."""
        return None

    def diagnostics(self) -> str:
        """Optional human-readable reason the feed is currently degraded (e.g.
        the historical-candle API is empty/rate-limited). Empty string means
        healthy. Surfaced on the dashboard so a blank instrument is explainable
        rather than mysterious. Data-only/sim feeds return ""."""
        return ""


def build_provider(name: str, instrument: str | None = None):
    """Factory: return a provider instance for the configured name.

    ``instrument`` selects which tradable symbol to analyse (see
    ``app.market.instruments``); defaults to Crude Oil.
    """
    from app.market.instruments import get_spec

    from app.config import settings

    name = (name or "simulated").lower()
    spec = get_spec(instrument or "")
    if name == "simulated":
        from app.market.simulated import SimulatedProvider

        return SimulatedProvider(spec)
    if name == "groww":
        from app.market.groww import GrowwProvider

        return GrowwProvider(spec)
    if name == "angelone":
        # Optional split: when Groww is enabled it serves the NSE/BSE (NFO/BFO)
        # equity + index options on its own account, while Angel keeps MCX
        # commodities — so neither feed is overloaded. If the Groww login is
        # unavailable, fall back to Angel so the instrument still works.
        if settings.enable_groww and spec.exchange in ("NFO", "BFO"):
            from app.market.groww import GrowwProvider

            gp = GrowwProvider(spec)
            if gp.feed_mode() != "simulated":
                return gp
        from app.market.angelone import AngelOneProvider

        return AngelOneProvider(spec)
    # Real providers are stubbed until credentials are supplied. They raise a
    # clear error so misconfiguration is obvious rather than silently faked.
    raise NotImplementedError(
        f"Provider '{name}' is not yet wired. Supply broker credentials and "
        f"implement app/market/{name}.py against MarketDataProvider. "
        f"Use QT_DATA_PROVIDER=simulated to run without a live feed."
    )
