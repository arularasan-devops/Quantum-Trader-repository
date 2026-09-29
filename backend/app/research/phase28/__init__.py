"""PHASE 28 — MULTI-DAY SWING VALIDATION: FUTURES AND CASH EQUITY.

Additive and research-only. Nothing in this package reads, writes or influences
the production signal engine, the live BUY path, the gates, strike selection, the
live stop/target/exit, the Phase 23 hurdle shadow, or Phase 24/25/26/27. There is
no tick hook and no write path into any book. The option-book capture keeps
running untouched and option strategy logic stays frozen.

Why this study exists, stated before any result:

* every phase of this programme measured an **intraday** hold. Phase 24 rejected
  1-minute futures, Phase 27 rejected 5-minute and 15-minute futures, Phase 25 and
  Phase 26 rejected ATM option buying and every exit variant of it. Not one of the
  four ever held a position overnight;
* the one number that improved monotonically across all of it was **cost as a
  fraction of the risk taken**: 0.3805 at one minute, 0.1532 at five, 0.0835 at
  fifteen. That is the toll a strategy pays before direction matters at all, and
  it falls because the stop gets wider, not because anything got smarter;
* extrapolating that curve is not evidence, it is a hypothesis, and it is the one
  hypothesis this programme has never tested: on a daily bar the stop is an order
  of magnitude wider again, so the toll should be a low single-digit percentage of
  risk. At that level a rule no longer has to beat the coin by much;
* cash equity adds a second thing intraday never had: **no expiry, no premium
  spread, no theta and no rollover**. Whether that is enough to matter is a
  measurement, and it is made here.

What this study is NOT allowed to do, because the temptation is obvious:

* it is not allowed to credit itself with market drift. A long-only swing rule on
  a rising index will show a profit that has nothing to do with the rule, so
  **buy-and-hold is computed per instrument, per window, and every long cohort is
  compared against it per day of exposure**. A rule that does not beat holding the
  thing is reported as not worth trading, however positive its net R;
* it is not allowed to pretend a daily stop is a limit order. An overnight gap can
  open through the stop, and the honest fill is the open, not the level. Both
  directions of that are charged: a gap through the stop fills worse, and a gap
  through the target fills better, and neither is silently improved;
* it is not allowed to hold a futures position across an expiry for free. Each
  crossing is charged as a real round trip;
* it is not allowed to short cash equity overnight, because Indian cash delivery
  does not permit it. The short side of an equity instrument is therefore absent
  rather than modelled.

The pre-committed stopping rule, agreed before the run so it cannot be moved
afterwards: if no cohort survives development, the validation year, the untouched
holdout, walk-forward, the cost/gap/timing stress, the multiple-testing correction
**and** the buy-and-hold comparison, then the conclusion is that this data and
execution environment contains no multi-day directional edge either, and the
search over holding periods is finished — no Phase 29. ``REJECTED`` for everything
is a valid and expected answer.
"""
from __future__ import annotations

VERSION = "PHASE28_SWING_FUTURES_EQUITY_V1"

# Vocabulary, identical to Phase 24/27 so three reports read side by side.
VALIDATED = "VALIDATED"
RESEARCH_ONLY = "RESEARCH_ONLY"
REJECTED = "REJECTED"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"

# The ceiling this study can reach. A cohort clearing every clause is an argument
# for forward paper collection; there is no code path from here to a live order.
RESEARCH_LEAD = "RESEARCH_LEAD"

# Vehicles. The two are priced by different cost models and one of them cannot be
# sold short overnight, so they are never pooled.
FUTURES = "FUTURES"
EQUITY_DELIVERY = "EQUITY_DELIVERY"

NO_EDGE = "NO_MULTI_DAY_DIRECTIONAL_EDGE_FOUND"

STOP_RULE = (
    "agreed before the run: if no multi-day cohort survives development, the "
    "validation year, the untouched holdout, walk-forward, the cost/gap/timing "
    "stress, the multiple-testing correction and the buy-and-hold comparison, "
    "then the evidence says this data and execution environment contains no "
    "multi-day directional edge either and the search over holding periods stops "
    "here — no further strategy phase"
)

GAP_CLAIM = (
    "a daily stop is not a limit order. Every bar after entry is checked at its "
    "OPEN first: if the open has already gapped through the stop the fill is the "
    "open and the extra loss is real, and if it has gapped through the target the "
    "fill is also the open. Only when the open is inside the bracket is an "
    "intrabar touch used, and a bar containing both levels is still resolved as "
    "the stop"
)

ROLLOVER_CLAIM = (
    "a futures position held across a contract expiry is charged one additional "
    "round trip per crossing, priced by the same Phase 19 cost model. A swing "
    "study that holds for weeks and charges one entry and one exit is understating "
    "its own costs"
)

DELIVERY_CLAIM = (
    "cash equity is priced as delivery: STT on both legs, exchange transaction "
    "charges, SEBI turnover fee, stamp duty on the buy, GST on the chargeable "
    "components and a per-scrip depository charge on the sell, plus configured "
    "slippage on both legs. There is no premium, no spread on a quoted option and "
    "no theta, which is the point of testing it"
)

SHORT_CLAIM = (
    "cash delivery cannot be held short overnight in the Indian market, so equity "
    "instruments produce LONG candidates only. Their short side is absent from "
    "this study rather than modelled from a futures contract whose per-stock "
    "history is not on disk"
)

BUY_HOLD_CLAIM = (
    "a long-only rule on a rising instrument earns market drift it did not "
    "create, so buy-and-hold over the same window is the benchmark every long "
    "cohort is measured against per day of exposure. Beating zero is not the bar; "
    "beating the passive alternative is"
)

SPREAD_CLAIM = (
    "the futures series publishes candles, not depth, so the bid/ask spread is "
    "unmeasured and every futures net figure is optimistic by exactly one spread. "
    "On a daily bar that spread is a far smaller fraction of the risk than it was "
    "intraday, which is an argument for looking here — not for ignoring it"
)

SAMPLE_CLAIM = (
    "a daily study cannot produce intraday sample sizes: one instrument yields "
    "roughly 250 candidates per side per year, so a conditioned cohort's untouched "
    "holdout is measured in tens, not thousands. The 100-trade bar for VALIDATED "
    "is kept as it stands, and a cohort that is positive everywhere on a smaller "
    "holdout is labelled REQUIRES_MORE_DATA rather than either promoted or "
    "discarded. Lowering the bar to fit the timeframe would be fitting the "
    "standard to the answer"
)
