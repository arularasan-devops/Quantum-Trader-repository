"""PHASE 26 — EXIT MANAGEMENT AND TRADABILITY (RESEARCH ONLY).

Phase 25 answered the question it was built for and the answer was no: buying the
ATM option at the ask and selling it at the bid did not pay on any instrument,
side, stop band or hurdle band. The most informative row in that result was not
the loss, it was **82.5% of trades expiring flat at the horizon**. A trade that
neither reaches its target nor its stop is not a wrong direction call; it is a
position that paid a round trip for nothing. No entry rule, indicator or model
can fix that, because the losing rows are not wrong — they are motionless.

So this phase changes the only two things Phase 25 held frozen, and changes
nothing else:

* **the exit.** Fixed 1.5R against a 20-50%-of-premium stop needs a +30-75%
  premium move inside an hour. Here that geometry is one variant among several —
  a lower target, a scale-out with a break-even remainder, a giveback trail, a
  longer horizon, and an early exit for a leg that has not moved — each resolved
  on the *same* stored quotes so the comparison is about the exit and nothing
  else;
* **the vehicle's tradability.** Half the captured universe cannot be traded
  profitably at any hit rate: ZINC's measured spread alone is ~0.97x the risk of
  a 30%-of-premium stop. That is knowable before entry, from the book, and it is
  published here as a **measured advisory table**, not as a live refusal.

Three properties are inherited from Phase 25 unchanged, because they are what
make a negative result trustworthy: entry is the **ask** of a later stored quote
plus slippage, every exit is a **bid** minus slippage, and the cost model is
charged brokerage and statutory charges only — the spread is already paid in the
prices, and charging it again would pay it twice. A scale-out is charged for the
extra exit order it really needs.

What this package must never be read as doing:

* it does not change production logic, the live BUY path, gates, strike
  selection, the live stop, target or exit, futures logic, the Phase 23 shadow,
  Phase 24 or Phase 25. It imports from Phase 24/25 read-only;
* it has no tick hook, no order path, no write path into any book and no
  promotion step. The best verdict it can produce is ``RESEARCH_LEAD``;
* the advisory table is advisory. Nothing in here switches a refusal on, and the
  report states the measured cost of switching one on so that decision stays
  with the operator.
"""
from __future__ import annotations

VERSION = "PHASE26_EXIT_AND_TRADABILITY_V1"

# Vocabulary shared with Phase 24/25 so two reports read side by side.
RESEARCH_LEAD = "RESEARCH_LEAD"
RESEARCH_ONLY = "RESEARCH_ONLY"
REJECTED = "REJECTED"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"
NO_TRADE = "NO_TRADE"

# Tradability verdicts for the advisory table. A verdict is about the *vehicle's
# economics*, never about a direction or a forecast.
TRADABLE = "TRADABLE_ECONOMICS"
MARGINAL = "MARGINAL_ECONOMICS"
AVOID = "AVOID_ECONOMICS"

WINDOW_CLAIM = (
    "captured window only (weeks of stored option books, not five years); a "
    "surviving exit variant here is a research lead for live paper collection, "
    "not a validated edge and not a promotion candidate"
)

ADVISORY_CLAIM = (
    "advisory only: this table is measured from stored books and is not wired "
    "to any live refusal. Turning any row into a live rule is an explicit "
    "operator decision, and the report states what that rule would have cost "
    "and saved on the captured window"
)
