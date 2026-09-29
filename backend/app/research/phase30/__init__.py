"""PHASE 30 — CANDLE / CONTEXT / CONTROLLED-AVERAGING VALIDATION (RESEARCH ONLY).

One question: does the five-year data on disk contain a repeatable candlestick
effect that survives realistic cost and a chronological holdout, and does one
controlled second entry improve it? ``NO_CANDLE_PATTERN_EDGE_FOUND`` and
``NO_AVERAGING_EDGE_FOUND`` are permitted answers and are the expected ones.

Declared before any result exists:

* the only five-year 1-minute series on disk are **NIFTY and CRUDEOIL**
  (2021-08-02 → 2026-08-05). Every other instrument has a few weeks of captured
  candles, which cannot carry a 60/20/20 chronological split, so it is reported
  ``INSUFFICIENT_HISTORY`` rather than pooled in and compared as an equal;
* there is **no five-year option book**, and premium behaviour cannot be
  inferred from underlying movement. §10 is therefore answered only inside the
  captured window, from real two-sided books, entry at ask and exit at bid;
  outside it every option figure is ``UNMEASURED`` and is never modelled;
* the vehicle history *can* price over five years is FUTURES, on executable-side
  pricing with brokerage, statutory charges and configured slippage charged both
  ways. The futures feed publishes candles rather than depth, so the spread is
  unmeasured and every net figure is optimistic by exactly one spread — stated
  on every row rather than left for the reader to discover;
* pattern definitions, the confirmation rule, the stop grid, the target
  geometry, the entry-quality bands and the averaging arms are frozen in code in
  :mod:`patterns`, :mod:`context` and :mod:`averaging` **before** any outcome is
  computed, and are not edited after a result is seen. The frozen set carries a
  fingerprint so a later run cannot silently claim to have tested this one;
* every hypothesis evaluated is counted, and Benjamini-Hochberg is applied to
  that honest denominator. Asking 29 patterns × 2 sides × confirmation × 5 stops
  is expensive by construction: it raises the bar a pattern must clear, and the
  denominator is reported next to every verdict.

Nothing in this package touches production: no gate, entry, stop, target,
sizing, averaging or order path. It reads history and writes report artefacts.
"""
from __future__ import annotations

VERSION = "PHASE30_CANDLE_CONTEXT_AVERAGING_V1"

# One status per candidate, §20.
VALIDATED = "VALIDATED"
RESEARCH_LEAD = "RESEARCH_LEAD"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"
REJECTED = "REJECTED"

# Study-level stopping-rule verdicts, §22.
NO_CANDLE_PATTERN_EDGE_FOUND = "NO_CANDLE_PATTERN_EDGE_FOUND"
NO_AVERAGING_EDGE_FOUND = "NO_AVERAGING_EDGE_FOUND"

# Coverage vocabulary, kept identical to Phase 24/25 so two reports read side by
# side without a translation table.
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
UNMEASURED = "UNMEASURED"
