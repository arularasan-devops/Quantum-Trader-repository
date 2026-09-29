"""PHASE 27 — HIGHER-TIMEFRAME FUTURES DISCOVERY (5-MINUTE AND 15-MINUTE).

Additive and research-only. Nothing in this package reads, writes or influences
the production signal engine, the live BUY path, the gates, strike selection, the
live stop/target/exit, the Phase 23 hurdle shadow, Phase 24, Phase 25, Phase 26
or any order path. There is no tick hook and no write path into any book. The
option-book capture keeps running untouched, and option strategy logic is frozen.

Why this study exists, stated before any result:

* Phase 24 rejected 1-minute futures on the same five-year series. The stated
  reason was arithmetic rather than search quality: at a 1-ATR stop on 1-minute
  bars the round-trip charges consume most of one R, and the pool's own
  T1-before-SL rate sat within a hair of the pre-cost breakeven;
* the one thing that changes that arithmetic without inventing a new indicator is
  the **bar size**. A 15-minute ATR is several times a 1-minute ATR, so the same
  charges become a much smaller fraction of the risk taken;
* so the vocabulary, the geometry, the cost model, the chronological windows and
  the promotion gate are all deliberately inherited from Phase 24. The *only*
  intended difference is the timeframe. That is what makes the comparison a
  measurement instead of a new search.

The stopping rule was agreed with the operator **before** the run and is carried
in the report so it cannot be moved afterwards: if nothing survives development,
validation, the untouched holdout, walk-forward, cost/entry/exit stress and the
multiple-testing correction on either timeframe, the conclusion is that the
current data and execution environment does not contain a robust intraday
directional edge, and the search stops. ``REJECTED`` for everything is a valid
and expected answer, and it is not a failure of the study.
"""
from __future__ import annotations

VERSION = "PHASE27_HTF_FUTURES_V1"

# Vocabulary, identical to Phase 24 so two reports read side by side.
VALIDATED = "VALIDATED"
RESEARCH_ONLY = "RESEARCH_ONLY"
REJECTED = "REJECTED"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"

# The ceiling this study is allowed to reach. A cohort that clears every clause of
# the gate is reported as a lead for forward paper collection; there is no code
# path from here to a live decision, and no separately approved promotion policy.
RESEARCH_LEAD = "RESEARCH_LEAD"

# The pre-committed stopping rule, quoted in the report and in the CLI output.
STOP_RULE = (
    "agreed before the run: if 5-minute and 15-minute discovery both fail "
    "validation, the untouched holdout, walk-forward, cost/slippage stress or "
    "the multiple-testing correction, then the evidence says this data and "
    "execution environment contains no robust intraday directional edge and the "
    "search stops here — no further strategy phase"
)

# The one claim this study is allowed to make about its own execution model.
SPREAD_CLAIM = (
    "the futures feed publishes candles, not depth, so the bid/ask spread is "
    "unmeasured and every net figure below is optimistic by exactly one spread. "
    "A result that is only marginally positive is therefore not positive"
)

RESAMPLE_CLAIM = (
    "5-minute and 15-minute bars are aggregated from the same stored 1-minute "
    "series, never fetched from a second source and never allowed to span a "
    "session boundary. An aggregated bar's high and low are the true extremes of "
    "its own minutes, so the stop-before-target tie inside a bar is still "
    "unknowable and is still resolved as the stop"
)
