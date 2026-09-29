"""Phase 15 — the A+ candidate judge. RESEARCH / SHADOW ONLY.

Phase 14 answered "does this condition lift expectancy, in sample and out".
That is necessary and not sufficient for the A+ objective, which is a *small*
set of candidates that keeps working. Three things can still be wrong with a
condition that passed Phase 14:

* it worked in one regime and the chronological split happened to put that
  regime in both halves (walk-forward, :mod:`folds`);
* it avoided losers by refusing winners in equal measure, which nets to nothing
  while looking selective (:mod:`attribution`);
* it produces a number that gets printed as a probability without ever having
  been calibrated (:mod:`shadow`, which is why the shadow card carries a SCORE
  and a RANK and refuses to carry a percentage).

And one thing can be right about every condition and still leave the money on
the table: the exit. :mod:`exits` re-prices the same pool under alternative exit
rules, because the book's mean favourable excursion is about +1R while its
capture of that excursion is a low single-digit percentage.

Nothing in this package reads or writes a production setting, places an order,
or changes a gate. It grades a saved candidate pool and prints verdicts.
"""
from __future__ import annotations

from app.research.phase15 import attribution, exits, folds, promotion, shadow

__all__ = ["attribution", "exits", "folds", "promotion", "shadow"]
