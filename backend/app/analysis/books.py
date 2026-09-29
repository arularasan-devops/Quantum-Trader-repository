"""Which book a journal row belongs to.

The journal holds 4,807 rows and 4,647 of them were written by the Flow engine:
one-minute auto-paper legs, 30% win, -Rs164,543, with 4,300 exiting on "flow
faded/reversed" and 839 whose entry equals their exit and still lost Rs20,637 in
pure cost. Pooled with the 156 tagged setup rows, they decide every aggregate by
weight of numbers alone, and a PULLBACK statistic computed over them is not a
PULLBACK statistic.

So the rows are separated rather than deleted — Flow stays fully queryable in its
own book, and nothing is dropped silently: a caller asking for the option book is
told how many Flow rows were held out.

Classification is by the tag the writer put on the row. The note-text prefix is
only a fallback for rows written before the tag existed.
"""
from __future__ import annotations

FLOW = "FLOW"
SIGNAL = "SIGNAL"

# What Flow's analyst note has always started with, which is the only marker the
# rows already on disk carry.
_LEGACY_NOTE_PREFIX = "candle-flow"


def classify(row: dict, note: str | None, context: dict | None) -> str:
    """The book a stored journal row belongs to."""
    for source in (context, row):
        if isinstance(source, dict):
            tag = source.get("book")
            if isinstance(tag, str) and tag.strip():
                return tag.strip().upper()
            if str(source.get("engine") or "").lower() == "flow":
                return FLOW
    if isinstance(note, str) and note.strip().lower().startswith(_LEGACY_NOTE_PREFIX):
        return FLOW
    return SIGNAL
