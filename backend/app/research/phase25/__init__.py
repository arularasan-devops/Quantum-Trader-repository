"""PHASE 25 — CAPTURED-WINDOW CE/PE STUDY (RESEARCH ONLY).

Separate from Phase 23 and from Phase 24. Nothing in this package reads, writes
or influences the hurdle shadow, the five-year discovery layer, the production
signal engine, the live BUY path or the broker order path. The only code it
borrows is read-only: the shared option cost model, the instrument registry and
Phase 24's causal feature and statistics modules.

What the data on disk actually supports, stated before any result:

* the option books are a **captured window of weeks, not years**. Every result
  here is therefore a lead, never a validated edge, and the report says so on
  every row. A positive captured-window number cannot clear a five-year bar it
  was never measured against;
* only snapshots the broker actually returned are used (``REAL_BROKER``), and
  only legs carrying a genuine two-sided book. Simulator rows are counted in
  coverage and excluded from every result. No premium, spread or book is ever
  modelled to fill a gap, and the family-median spread is never used here;
* entry is at the **ask**, exit at the **bid**, both from real stored quotes at
  real timestamps, with the configured per-side slippage applied against the
  trade. Because the spread is paid in the prices themselves, the cost model is
  charged for brokerage and statutory charges only — charging its spread term as
  well would pay the spread twice;
* the forward path is the same contract's later stored quotes inside its own
  session. Between two snapshots nothing is observable, so a target reached and
  given back inside a gap is not counted, and a snapshot that shows both the
  stop and a target resolves as the **stop**.

``NO TRADE`` remains a first-class outcome: an instrument, a side or a cohort
with too little captured history is reported ``REQUIRES_MORE_DATA`` rather than
scored, and nothing in here promotes anything.
"""
from __future__ import annotations

VERSION = "PHASE25_CAPTURED_OPTION_V1"

# Vocabulary shared with the earlier phases so two reports read side by side.
RESEARCH_LEAD = "RESEARCH_LEAD"
RESEARCH_ONLY = "RESEARCH_ONLY"
REJECTED = "REJECTED"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"
NO_TRADE = "NO_TRADE"

# The window this study can ever speak about. Kept as a constant so the report
# and the API cannot disagree about the claim being made.
WINDOW_CLAIM = (
    "captured window only (weeks of stored option books, not five years); "
    "a survivor here is a research lead for live paper collection, not a "
    "validated edge and not a promotion candidate"
)
