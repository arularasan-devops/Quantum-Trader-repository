"""Phase 34 — real option premium history, and adaptive exits. Research only.

Every earlier phase had to answer option questions with ``UNMEASURED`` because the
store held 51 two-sided quotes. A probe of the broker's historical endpoint changed
that: ``getCandleData`` serves ONE_MINUTE candles for an *option contract* token,
about 375 bars a session for a traded strike, from the contract's first trade until
it expires. That is genuine premium history and it is what this phase collects.

Two limits are structural and are labelled, never argued away:

``MEASURED_TRADED_PRICE``
    A candle is built from executed trades. It carries no bid and no ask, so an
    entry-at-ask / exit-at-bid claim cannot be made from it. Premium *movement* is
    measured; execution is not.
``SPREAD_MODELLED``
    Where a net number is needed, the spread comes from the live two-sided captures
    as a stated distribution, not from the candle. Every such number is a model.

And one hard deadline: the scrip master lists **no expired contracts**, so once a
contract expires its token cannot be resolved any more. Tokens must therefore be
snapshotted while the contract is alive, which is why ``scripmaster`` runs daily
and is not optional.
"""
from __future__ import annotations

MEASURED_TRADED_PRICE = "MEASURED_TRADED_PRICE"
SPREAD_MODELLED = "SPREAD_MODELLED"
UNMEASURED = "UNMEASURED"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"

# Harvest window around the money. A signal takes a near-the-money strike, so the
# wings are collected only wide enough to cover strike drift within a session.
STRIKE_WINDOW_PCT = 3.0

# A signal takes the front contract, so only contracts expiring inside this many
# days are harvested. The far-dated strikes carry more calendar history but they
# are not the vehicle the tool trades, and each one costs rate-limited calls.
MAX_DTE_DAYS = 45

# A contract that has not traded returns an empty window. Walking backwards from
# expiry and stopping after this many consecutive empties bounds the wasted calls
# per contract instead of probing a year of pre-listing dates.
MAX_EMPTY_WINDOWS = 2

# The broker caps one ONE_MINUTE response near 8000 rows; an option session is
# ~375 bars, so a 15-session chunk stays under it. Spacing matches the historical
# rate limit that app.backtest.angel_history already respects.
CHUNK_DAYS = 15
MIN_CALL_INTERVAL_SEC = 3.0

OPTION_SESSION_BARS = 375
MIN_BARS_PER_CONTRACT = 200

DB_NAME = "optpremium.db"
