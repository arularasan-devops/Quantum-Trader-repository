"""Phase 41 — freeze the Phase 40 diagnostic, then accumulate sessions under it.

Phase 40 answered MIXED on one session: favourable-first and adverse-first were
inside sampling noise of each other, and so was the giveback split. The correct
next step is not another mechanism — it is more sessions run through the *same*
diagnostic. This phase exists to make "the same" checkable rather than assumed,
and to show the answer moving as the store deepens.

It changes no Phase 40 definition. It reads Phase 40's own modules for their
definitions, hashes them, and re-runs Phase 40 unchanged.

**Why a fingerprint.** Phase 40's first release raced from the entry fill and
reported ENTRY / DIRECTION at 69.76% adverse-first. The corrected release races
exit side to exit side and reports MIXED at 51.28% on the same data. Nothing in
either payload said which definition produced it. A reader holding both reports
would have compared two different measurements and called it a trend. The
fingerprint stamps the definition into the payload so that comparison is
impossible to make by accident.

**What the fingerprint is taken over.** Not the declared constants alone — a
constant list would not have moved when the race origin changed, because the
change was in a function body. It hashes the source of the functions that decide
the race and the money, together with the thresholds, horizons, caps and test
parameters. Anything that could change an answer is inside it; formatting,
reports and docstrings are not, because they cannot.

**What the fingerprint does not mean.** A definition change does not spoil the
data. The raw store is never overwritten (Phase 35 §21), so every session can be
recomputed under the current definition, and this phase always does exactly
that: every figure it prints comes from one pass over the whole store under one
definition. What the fingerprint protects against is comparing a *stored
artefact* written under an older definition with a new one. Those are marked
:data:`STALE_ARTEFACT` and never pooled.

**What accumulation shows.** The same verdict per session and cumulatively in
date order, with the deviate recomputed at each step, so a verdict that is
drifting toward resolution looks different from one that is sitting at 50/50.
Two further checks come with it, because a cumulative curve alone can be driven
by one day: each session is left out in turn, and the whole thing is repeated on
the non-overlapping subsample.

**Which frame decides the published verdict.** The non-overlapping one, and only
it. Sampled instants walked forward from one session describe the same few price
swings many times over, so the all-legs count is not a count of independent
trials and a deviate computed over it grows with the sampling density rather than
with the evidence. That is not a theoretical worry: between the first and second
session the all-legs sample went from 587 legs to 27,532 (47x) and its |z| went
from 1.13 to 4.75 — almost exactly the sqrt(47) that pure inflation predicts —
while the non-overlapping frame stayed MIXED and its effect got *smaller*. Read
as a verdict, that would have promoted an artefact of counting to a finding. The
all-legs frame is still printed, because it is the descriptive picture of every
leg the engine could have taken, and its deviate is labelled
:data:`Z_NOT_A_TEST` wherever it appears.

This changes no measured number, so the frozen definition does not move: Phase 40
computes both frames exactly as before. What changed is which of the two Phase 41
quotes as the answer, and that is recorded separately as
:data:`REPORTING_RULE` in the ledger, so a table written under the old rule is
still identifiable.

Nothing here promotes, enables, validates or proposes anything, and no verdict
it prints is evidence that any strategy is profitable.
"""
from __future__ import annotations

from app.research.phase40 import (
    MATERIALITY_Z,
    MIN_CLASSIFIED_FOR_VERDICT,
    REFERENCE,
)

VERSION = "41.1"
PHASE = "PHASE41_FROZEN_DIAGNOSTIC_ACCUMULATION"

ARTEFACT_DIR = "data/phase41"
MD_NAME = "FROZEN_DIAGNOSTIC_ACCUMULATION.md"
JSON_NAME = "phase41_accumulation.json"
# The definition ledger. Append-only, outside the raw store, and written here
# rather than in the store because a diagnostic that writes to the evidence it
# reads is not a diagnostic.
LEDGER_NAME = "definition_ledger.jsonl"

# Re-exported so a Phase 41 row can be laid beside a Phase 40 row without asking
# which hold, floor or deviate either used. They are read, never redeclared: a
# second copy of a threshold is a second threshold.
HORIZON = REFERENCE
Z = MATERIALITY_Z
MIN_CLASSIFIED = MIN_CLASSIFIED_FOR_VERDICT

# The state of a run against the ledger.
FIRST_RUN = "FIRST_RUN_OF_THIS_DEFINITION"
UNCHANGED = "DEFINITION_UNCHANGED"
CHANGED = "DEFINITION_CHANGED"
STALE_ARTEFACT = "ARTEFACT_WRITTEN_UNDER_AN_EARLIER_DEFINITION"

# How the accumulated answer is behaving as sessions arrive. These describe the
# sequence of verdicts; none of them is a prediction and none authorises a
# change of mechanism.
STABLE = "STABLE_ACROSS_SESSIONS"
UNSTABLE = "VERDICT_CHANGED_AS_SESSIONS_WERE_ADDED"
ONE_SESSION = "SINGLE_SESSION_NOT_YET_A_SEQUENCE"
DOMINATED = "ONE_SESSION_DECIDES_THE_POOLED_ANSWER"

# Which frame the published verdict is taken from, and which is descriptive only.
# Versioned separately from the definition fingerprint because choosing a frame
# changes no measured number: both frames were always computed and printed.
GOVERNING_FRAME = "non_overlapping"
DESCRIPTIVE_FRAME = "all_legs"
REPORTING_RULE = "VERDICT_FROM_NON_OVERLAPPING_FRAME"
Z_NOT_A_TEST = (
    "DEVIATE_NOT_A_TEST_OVERLAPPING_WINDOWS: these windows are drawn from the "
    "same sessions and mostly re-measure the same price swings, so the count is "
    "not a count of independent trials. The deviate over it rises with the "
    "square root of the sampling density whether or not anything is there, and "
    "it is reported as description, never as a test."
)

RESEARCH_ONLY = "RESEARCH_ONLY"
PAPER_ONLY = "PAPER_ONLY"
READ_ONLY = "READ_ONLY_NO_CAPTURE_NO_PRODUCTION_CHANGE"
NOT_A_STRATEGY = (
    "DIAGNOSTIC_ONLY_NOT_A_STRATEGY: this re-runs a frozen measurement over "
    "more sessions. It tests no exit rule, proposes no entry, changes no "
    "definition, and is not evidence that any strategy is profitable."
)
