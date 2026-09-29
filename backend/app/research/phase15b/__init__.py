"""Phase 15B — historical A+ market-setup discovery on the 5-year candidate pool.

RESEARCH ONLY. This package answers "does this market setup select better
trades" from underlying replay. It cannot answer "is the option tradable" —
premium, spread, bid/ask, OI, IV and delta are not in the pool, so every result
here is labelled UNDERLYING_ONLY and every surviving setup leaves with a list of
what the live option layer must still verify before a paper trade is placed.

Nothing here changes routing, sizing, exits or the order path.
"""
