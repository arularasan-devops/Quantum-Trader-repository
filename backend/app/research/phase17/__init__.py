"""Phase 17 — real-chain vehicle evidence + A+ paper trading. RESEARCH ONLY.

Phase 15B closed the underlying question: over 140k candidate bars and 218
compared cohorts, no selective market setup survived to a chronological holdout.
The edge that is missing is not directional — it is economic. The last measured
option sample said so plainly: 3,448 recorded legs, 48 of which had a real book,
and on those 48 a Rs 56 round trip chased Rs 8 of gross travel.

So this phase measures the vehicle rather than the market, and it exists because
of one specific failure it must not repeat. Those 3,400 unmatched legs were not a
feed problem: the chain was being written against the last COMPLETED bar, up to
60 seconds away from the signal, so 98.6% of the dataset described a book that
was not the one the decision was made on. Phase 17 captures instead at the tick
instant, from the chain the engine has already fetched to make its decision, so
the quote and the signal share a timestamp by construction and cost nothing extra
in broker calls.

Three rules the package is built around:

* **A+ is a label, not a collection filter.** Every eligible candidate is
  recorded, both sides, whether or not it qualifies. Gating capture on A+ would
  take 3-5 months to reach 100 resolved outcomes; recording everything reaches
  the same evidence in weeks, and the CE/PE counterfactual comes free.
* **Nothing is fabricated to fill a field.** A missing book is MISSING, a late
  one is STALE, an uncosted leg is ``cost_status=UNKNOWN``. Every row carries its
  own data quality and every report prints its unmatched count, because a
  dataset that hides its gaps is worse than a smaller honest one.
* **Gross P&L is never the answer.** Where a two-sided quote exists, a paper leg
  buys at the ask and sells at the bid and reports net. Expect the paper book to
  look worse once this lands — that is the measurement working.

Nothing in this package changes BUY/WAIT/NO_TRADE, gates, scores, confidence,
strike selection, the premium floor, stops, targets, exits, Flow, or the order
path. There is no order path here at all: the simulator prices fills off recorded
quotes and writes files, and no output of this package is read by anything that
trades.
"""
