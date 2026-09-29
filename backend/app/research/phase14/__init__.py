"""Phase 14 — multi-year underlying history: collection, coverage, replay.

Everything in this package fetches or reads *historical* market data and returns
tables. No module here computes a production signal, changes a gate, a score, a
strike, a stop, a target or an exit, and none of it reaches an order path.

Two limits of the data source are structural and are reported rather than
papered over:

* Angel One's scrip master only carries **live** contracts, so an expired future
  or an expired strike cannot be re-fetched at any price. Multi-year history is
  therefore *underlying* history — index and equity cash candles — and MCX names,
  which have no cash series, can only be collected as far back as their current
  contract goes.
* The historical candle endpoint returns OHLCV only. Open interest, IV, greeks,
  bid and ask are absent, so a replay over this data can grade market direction
  and cannot grade the option that would have been bought.
"""
