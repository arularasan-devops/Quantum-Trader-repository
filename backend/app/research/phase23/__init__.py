"""Phase 23 — the break-even hurdle, as a PAPER SHADOW.

Study 1 found the one filter its own journal supports: legs whose round-trip cost
was a small share of the premium made money (n=73, +₹64,027, PF 2.69) and legs
whose cost was a large share did not (n=62, −₹69,383, 3 of 62 ever finished
positive). But that split was computed with the *assumed* family-median spread,
because the old journal rows carry no book. An assumption is not grounds for a
production gate, so this package does not add one.

What it does instead: for every auto BUY opportunity, record the hurdle computed
from the **live measured bid/ask** and carry three parallel outcomes —

    EXISTING        what the live engine actually did (unchanged)
    SHADOW <= 5%    the same signal, refused when the measured hurdle exceeds 5%
    SHADOW <= 3%    the same signal, refused when it exceeds 3%

Nothing here places, blocks or alters an order. The live auto-buy path, the
direction logic, strike selection, stops, targets, pullback rules, confidence and
the exit logic are untouched, and the shadow's own decision never reaches them.

Three rules this package will not break:

* **No family-median spread in a shadow decision.** When the feed carried no
  usable two-sided book the row's hurdle is ``None``, its arms are ``ABSTAIN``
  and it is excluded from every verdict — never costed at an assumed spread,
  because the whole point is to test the filter on measured economics.
* **Options only.** The futures book had a ~0.05% hurdle and still lost on 16 of
  20 legs, so its problem is signal quality, not transaction cost. Futures rows
  are not recorded here and no hurdle filter is applied to them anywhere.
* **No threshold is promoted, and none is assumed.** 3% and 5% are the two
  bracketing candidates; the sweep reports every cutoff between them and the
  final number is left to be discovered from the shadow data under the promotion
  rule in :mod:`app.research.phase23.verdict`.
"""
