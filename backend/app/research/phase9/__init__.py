"""Phase 9 — strike quality, entry quality and tradability. RESEARCH ONLY.

Phase 8 said BAD_STRIKE explains ~40% of losses on two independent sessions. That
is a symptom, not a cause: the label fires when the underlying moved favourably and
the premium did not follow, which can happen because the book was too wide, the leg
was illiquid, the premium had already expanded, the delta was too small to carry the
move, or the target was never reachable in the available time. Phase 9 exists to
separate those, and to answer the only question that matters before anyone touches
the selector: *was a better contract actually available at the moment of the signal?*

Modules:

``strikes``      the neighbouring-strike ladder at the signal timestamp, scored on
                entry-time information only, plus what each candidate went on to do
                (kept in a separate block, because hindsight must not leak into the
                entry-time score)
``badstrike``    decomposition of each BAD_STRIKE loss into a measurable sub-cause
``tradability``  empirical GREEN/YELLOW/RED distributions and *proposed* thresholds
``entryq``       entry-quality classes against outcome, net of the recorded book
``shadow``       a research qualifier run beside the baseline, blocking nothing
``candidates``   descriptive A+ component vectors — no predicted R, by design
``gates9``       the extra acceptance gates, including data contamination

Production safety: nothing in this package is imported by any production module, it
holds no order-placing code, and it never writes to the trading database. BUY/WAIT/
NO_TRADE, the gates and their thresholds, the confidence formula, stops, targets,
R:R, the chase guard, direction logic, the strike selector, Early Momentum,
Early-Early, production exits and broker execution are read-only inputs here.

All arithmetic is Phase 7's: ``paths.walk`` for every forward path, ``policies``
for every fill and exit, ``phase8.spread.measure`` for every cost. Phase 9 adds no
second implementation of an R.
"""
