"""Phase 11 §12 — the shadow baseline, per family, with no model. RESEARCH ONLY.

§12's point is that the outcome record must exist whether or not a model does.
Every production BUY becomes a PRODUCTION_SIGNAL row with a PAPER_SHADOW_OUTCOME
attached: what the trade did, what it kept, what the book cost, what the A+ rule
thought of it. The probability column stays empty, because no validated mapping
exists and inventing one is how a research artefact becomes an unearned claim.

Phase 10's book code is reused per family. The status stays
``WATCH / NO_VALIDATED_MODEL`` for as long as the calibration gates fail, which
on this dataset is all of it.
"""
from __future__ import annotations

from app.research.phase10 import paperbook

from .families import FAMILIES, split

STATUS_NO_MODEL = f"{paperbook.WATCH} / {paperbook.NO_VALIDATED_MODEL}"


def build(trade_rows: list[dict], *, model_available: bool,
          model_status: str) -> dict:
    """§12 — the three-column shadow book, per family."""
    status = model_status if model_available else paperbook.NO_VALIDATED_MODEL
    by_family = split(trade_rows)
    out: dict[str, dict] = {}
    for fam in FAMILIES:
        sub = by_family.get(fam) or []
        out[fam] = ({"rows": 0,
                     "reason": "no production BUY resolved in this family"}
                    if not sub else paperbook.book(sub, model_status=status))
    return {
        "record": ["PRODUCTION_SIGNAL", "PAPER_SHADOW_OUTCOME"],
        "model_available": model_available,
        "status": status if model_available else STATUS_NO_MODEL,
        "by_family": out,
        "probability_column": "empty by design. A probability is emitted only from "
                              "a mapping that beat a constant base-rate forecast on "
                              "sessions it never saw",
        "independence": "the shadow book records outcomes for signals production "
                        "already emitted. It does not open, close, size or suppress "
                        "anything, and no production module reads it",
    }
