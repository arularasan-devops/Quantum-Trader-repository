"""Phase 10 — feed quality, score calibration and A+ shadow validation. RESEARCH ONLY.

Phase 9 ended with four failed gates and one finding large enough to reorder the
whole programme: over 393 replayed trades the direction was roughly break-even
gross (+0.169R) and the *book* took it to −4.297R net, with the bid/ask wider than
the intended risk on 44% of legs. At the same time 46.5% of one-minute bars were
missing and 38% of production signals fired on data already flagged stale, because
50 instruments shared one rate-limited historical API. Nothing measured under those
conditions can be promoted, and no amount of new logic fixes a stale tick.

So Phase 10 measures rather than adds. Its five questions, in dependency order:

  1. is the data any good, per instrument, live?                (``dataquality``)
  2. how many production BUY/WAIT decisions fire on fresh data?  (``freshness``)
  3. does the displayed SIGNAL SCORE order outcomes at all?      (``score``)
  4. can that score be mapped to an honest probability?          (``probability``)
  5. does A+ filtering beat baseline on sessions it never saw?    (``aplus``)

with the supporting measurements the answers depend on: ``premiums`` (is the
low-premium floor excluding profitable trades, or protecting us from them?),
``capture`` (how much of the favourable excursion is actually kept), ``capacity``
(what the tier split costs the feed), ``tiers10`` (current versus proposed deep
list), ``export`` (the nightly dataset), and ``gates10`` (what this sample is not
allowed to conclude).

Naming, deliberately: the number on the board is a SIGNAL SCORE, not a win
probability. Phase 10 relabels it in research and diagnostics only — the
production Signal tab is untouched — because calling a score a probability is how
"92% confidence" came to describe a bucket that delivered 2.2%.

Production safety. Nothing in this package is imported by any production module.
It holds no order-placing code, writes to no trading table, and reads BUY/WAIT/
NO_TRADE, the gates and their thresholds, the confidence formula, stops, targets,
R:R, the chase guard, direction logic, the strike selector, the premium floor,
Early Momentum/Early-Early, production exits and broker execution as read-only
inputs. ``_smoke_phase10`` fails the build if a production module imports it.

Two things this package must never be read as doing. It promotes nothing: every
threshold it derives is stamped ``PROPOSED_ONLY`` and is applied nowhere. And it
predicts nothing it cannot support: where the sample is too thin to calibrate,
``probability`` returns ``MODEL_UNAVAILABLE`` rather than a fitted curve, because
an unsupported probability is the exact defect this phase exists to correct.

All arithmetic is Phase 7's — ``paths.walk`` for every forward path, ``policies``
for every fill and exit, ``phase8.spread.measure`` for every cost, and Phase 9's
population, unchanged, for every trade. Phase 10 adds no second implementation of
an R.
"""
