"""Phase 9 labels — Phase 8's evidence labels plus data contamination. RESEARCH ONLY.

Phase 8's ``label``/``note``/``validated`` are reused unchanged so a Phase 9 block
cannot claim a stronger label than a Phase 8 block on the same sample. Phase 9 adds
one label of its own, because this dataset needs it: ``DATA_CONTAMINATED``.

The spec (§77) forbids using contaminated ATR/VWAP/regime features as if valid, and
in this dataset 64% of one-minute bars are missing after the watchlist grew to 48
instruments. Anything derived from ATR — room-to-target, extension, expected move —
is therefore suspect, and every block that depends on it is stamped rather than
silently reported. The rows are never dropped (§83): they are labelled.
"""
from __future__ import annotations

from app.research.phase7.gates import MAX_MISSING_BAR_PCT
from app.research.phase8.findings import (
    HYPOTHESIS,
    NEEDS_DATA,
    OBSERVATION,
    VALIDATED,
    label,
    note,
    validated,
)

CONTAMINATED = "DATA_CONTAMINATED"

__all__ = ["CONTAMINATED", "HYPOTHESIS", "NEEDS_DATA", "OBSERVATION", "VALIDATED",
           "band_label", "contamination", "label9", "note9", "validated"]


def label9(cell_n: int, sessions: int, *, comparative: bool = True) -> str:
    return label(cell_n, sessions, comparative=comparative)


def note9(cell_n: int, sessions: int) -> str:
    return note(cell_n, sessions)


def band_label(lo: float, hi: float) -> str:
    if hi >= 1e12:
        return f"₹{lo:.0f}+"
    if lo == 0.0:
        return f"<₹{hi:.0f}"
    return f"₹{lo:.0f}-{hi:.0f}"


def contamination(missing_bar_pct: float | None, *, atr_dependent: bool) -> dict:
    """Whether a block's inputs are trustworthy, and what that costs the reader.

    ``atr_dependent`` is the caller's honest declaration that the block uses ATR,
    VWAP, extension or expected-move anywhere in its derivation. A block that only
    reads the recorded book (spread, OI, volume, premium) is not contaminated by
    missing bars, and saying so is as important as flagging the ones that are.
    """
    pct = 0.0 if missing_bar_pct is None else float(missing_bar_pct)
    bad = pct > MAX_MISSING_BAR_PCT
    if not atr_dependent:
        return {"status": "CLEAN_INPUTS", "missing_bar_pct": round(pct, 2),
                "reading": "this block reads the recorded option book only, so "
                           "missing underlying bars do not affect it"}
    if not bad:
        return {"status": "CLEAN_INPUTS", "missing_bar_pct": round(pct, 2),
                "reading": f"missing bars {pct:.1f}% within the "
                           f"{MAX_MISSING_BAR_PCT}% gate"}
    return {
        "status": CONTAMINATED,
        "missing_bar_pct": round(pct, 2),
        "gate_pct": MAX_MISSING_BAR_PCT,
        "reading": (f"{pct:.1f}% of one-minute bars are missing against a "
                    f"{MAX_MISSING_BAR_PCT}% gate, and this block derives from ATR / "
                    f"expected move, so its magnitudes are contaminated. Rows are "
                    f"labelled, never dropped; the direction may survive, the size "
                    f"does not"),
    }
