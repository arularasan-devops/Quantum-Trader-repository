"""PHASE 24 — 5-YEAR DATA-DRIVEN STRATEGY DISCOVERY (RESEARCH ONLY).

Separate from Phase 23. Nothing in this package reads, writes or influences the
hurdle shadow, the production signal engine, the live BUY path or the broker
order path. The only code it borrows is the read-only futures cost model.

What the data on disk actually supports, stated before any result:

* five years of 1-minute candles exist for **NIFTY and CRUDEOIL only**
  (2021-08-02 → 2026-08-05). Every other instrument has a few weeks of captured
  candles, which cannot carry a Y1-3 / Y4 / Y5 chronological split, so those
  instruments are reported ``INSUFFICIENT_HISTORY`` rather than pooled in;
* there is **no historical option book**. Of the stored chain snapshots exactly
  one came from a real broker with a two-sided quote. So CE and PE discovery
  (§11) and the CE/PE half of vehicle selection (§12) return
  ``REQUIRES_MORE_DATA``. A premium is never modelled to fill the gap;
* the tradable vehicle that history *can* price is FUTURES, on executable-side
  pricing with brokerage, statutory charges and configured slippage charged. The
  futures feed publishes candles, not depth, so the spread is unmeasured and the
  net result is optimistic by exactly one spread — declared on every row.

Discovery is bounded and interpretable, the final year is a holdout that no
condition is tuned on, and every candidate is stress-tested for cost, entry
delay, exit delay, parameter perturbation, regime and outlier dependence. The
verdict may be ``REJECTED`` for everything, and that is a valid answer.
"""
from __future__ import annotations

VERSION = "PHASE24_5Y_DISCOVERY_V1"

# Vocabulary, kept identical to the earlier phases so two reports can be read
# side by side without a translation table.
VALIDATED = "VALIDATED"
RESEARCH_ONLY = "RESEARCH_ONLY"
REJECTED = "REJECTED"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"
