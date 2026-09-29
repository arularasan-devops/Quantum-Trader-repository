"""Phase 11 — instrument-aware validation, clean deep-feed measurement and a
costed flow ledger. RESEARCH / SHADOW / PAPER ONLY.

Phase 11 exists because Phase 10's headline numbers were wrong in a specific,
correctable way: the replay defaulted to the instruments with the most recorded
chain snapshots, MCX records chains at roughly twice the index cadence, and so
every pooled statement Phase 10 made was an MCX statement wearing a whole-book
label. Split by family, the same code on the same database says opposite things —
index options carried a book worth 1.6-8.5% of intended risk and roughly
break-even net expectancy, while GOLD's book alone exceeded the stop it was
traded with.

So the organising rule of this phase is: **nothing is pooled across families.**
Every table is INDEX_OPTIONS, MCX_OPTIONS or OTHER, and a number without a family
is treated as a bug rather than a summary.

The three questions it adds to Phase 10:

* is the SIGNAL SCORE, and is the A+ shadow qualifier, useful *per family*?
* what does the flow paper book look like once it pays the book it trades in? The
  production ledger scores entries and exits at the same mid premium, which is
  why an instrument whose spread is 9% of premium can show a profit;
* how much of the loss is a wrong opinion (signal-side) and how much is an
  unpayable contract (economic/execution)? Those need opposite fixes, and a
  pooled loss column cannot tell them apart.

Isolation is enforced mechanically, not promised: no module under
``app.research.phase11`` is imported by any production module, and
``_smoke_phase11`` fails if that ever stops being true.
"""
