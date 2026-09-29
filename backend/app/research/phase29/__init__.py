"""PHASE 29 — DEFINED-RISK OPTION CREDIT SPREADS (RESEARCH ONLY).

The first study in this project that sells option premium instead of buying it,
and the last untested payoff shape. It exists because every earlier phase found
the same thing from the *buyer's* side — the option does not move enough to pay
its round trip — and that sentence is a statement about the seller's side of the
same book. This package measures whether the seller's side survives the same
standard. It is not an assertion that it does.

Separate from Phase 23, 25, 26, 27 and 28. Nothing in here reads, writes or
influences the hurdle shadow, the production signal engine, the live BUY path,
the paper books or the broker order path. Everything it borrows is read-only:
Phase 25's captured-book loader, Phase 24's causal features and statistics, the
shared option cost model and the instrument registry.

What the data supports, stated before any result:

* **only defined risk.** A structure enters the study only when a protective leg
  was quoted two-sided in the *same* snapshot as the short leg. A naked short is
  not a candidate, is counted in coverage, and can never be priced here. The
  maximum loss is therefore bounded by construction, not by a stop working;
* **executable sides, per leg.** The short leg is sold at the **bid** and bought
  back at the **ask**; the protective leg is bought at the **ask** and sold at
  the **bid**. Both directions pay slippage against the trade. A spread priced
  on mid-to-mid is roughly twice as profitable as it can actually be, which is
  the single most common error in credit-spread research;
* **both legs at one timestamp, or no candidate.** A spread is resolved only on
  snapshots where *both* contracts were quoted. Filling one leg from a nearby
  timestamp invents a credit that never existed;
* **expiry is never assumed worthless.** A position with no paired forward quote
  to close against is ``UNRESOLVED``. It is never marked as keeping the full
  credit. Assuming an out-of-the-money short expires worthless is how a losing
  seller's book prints a 90% win rate;
* **margin is not modelled.** Real SPAN plus exposure margin on a short spread
  exceeds its maximum loss and is broker- and day-dependent. Risk is therefore
  quoted against **defined loss**, and no return-on-margin figure is produced;
* **the window is weeks.** The captured books are a window, not five years, so
  the ceiling on any verdict is ``RESEARCH_LEAD`` and the code refuses to print
  a stronger word.

``NO TRADE`` stays a first-class outcome: an instrument, a structure or a cohort
with too little captured history is ``REQUIRES_MORE_DATA`` rather than scored,
and nothing in this package promotes anything.
"""
from __future__ import annotations

from app.research.phase25 import books

VERSION = "PHASE29_CREDIT_SPREAD_V1"

# Shared vocabulary, so two reports read side by side.
RESEARCH_LEAD = "RESEARCH_LEAD"
RESEARCH_ONLY = "RESEARCH_ONLY"
REJECTED = "REJECTED"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"
NO_TRADE = "NO_TRADE"

# The claim this study can ever make, kept as a constant so the report, the CLI
# and the API cannot disagree about it.
WINDOW_CLAIM = (
    "captured window only (weeks of stored option books, not five years); a "
    "survivor here is a research lead for live paper collection, not a "
    "validated edge and not a promotion candidate"
)

MARGIN_CLAIM = (
    "returns are quoted against defined loss (width minus credit). Real margin "
    "for a short spread is SPAN plus exposure, is set by the broker and exceeds "
    "the defined loss, so no return-on-capital or return-on-margin figure is "
    "produced here and none should be inferred"
)

ASSIGNMENT_CLAIM = (
    "no settlement, assignment or expiry-worthless outcome is modelled. A "
    "position that has no paired forward quote to close against is UNRESOLVED "
    "and contributes to no result; it is never credited with the full premium"
)


def books_min_note() -> str:
    """The captured-store minimums an instrument has to clear to be studied.

    Read from Phase 25's loader rather than restated, so the printed minimums
    cannot drift away from the ones actually enforced.
    """
    return (
        f"minimums: {books.MIN_SNAPSHOTS:,} snapshots · {books.MIN_SESSIONS} "
        f"sessions · {books.MIN_RESOLVABLE_QUOTES:,} quotes with a later quote of "
        f"the same contract · {books.MIN_CONTRACT_PATHS} distinct contracts · at "
        "least two distinct strikes, without which no protective leg exists"
    )
