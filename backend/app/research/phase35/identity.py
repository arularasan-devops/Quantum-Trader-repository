"""Phase 35 §3 — deterministic opportunity identity.

An opportunity needs an id that two independent runs agree on, or the store
cannot converge and "the same opportunity" cannot be joined across the engine
book, the market book and the vehicle comparison. So the id is a fingerprint of
the fields that make an opportunity what it is, and of nothing else.

What is deliberately *not* in the fingerprint: any price, any outcome, any
score. If the ask were part of the identity then the same opportunity captured
twice a second apart would be two opportunities, and the missed-winner count
would be a function of capture cadence. What *is* in it: the instrument, the
decision second, the source, the type, the direction and — for options — the
strike and expiry, because a 23900 CE and a 24000 CE at the same instant are
genuinely different opportunities and must not collapse into one row.

The timestamp is floored to the second. The capture path can see the same
decision twice within one second (a tick hook firing on two ticks of the same
signal), and those are one opportunity, not two.
"""
from __future__ import annotations

import hashlib

from app.research.phase35 import (
    BOARD,
    BREAKOUT,
    CONTINUATION,
    DIRECTIONAL,
    ENGINE,
    OPPORTUNITY_TYPES,
    PULLBACK,
    RANGE_EXPANSION,
    RELATIVE_VALUE,
    REVERSAL,
    SOURCES,
    UNCLASSIFIED,
    VOLATILITY_EXPANSION,
)

FINGERPRINT_LEN = 16


def opportunity_id(
    *,
    instrument: str,
    ts: float,
    source: str,
    opportunity_type: str,
    direction: str | None,
    vehicle: str | None = None,
    strike: float | None = None,
    expiry: str | None = None,
) -> str:
    """Stable 16-hex identity for one opportunity observation."""
    if source not in SOURCES:
        raise ValueError(f"unknown opportunity source: {source!r}")
    if opportunity_type not in OPPORTUNITY_TYPES:
        raise ValueError(f"unknown opportunity type: {opportunity_type!r}")
    parts = (
        instrument.strip().upper(),
        str(int(float(ts))),
        source,
        opportunity_type,
        (direction or "").upper(),
        (vehicle or ""),
        "" if strike is None else f"{float(strike):.2f}",
        expiry or "",
    )
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:FINGERPRINT_LEN]


# The words the upstream engine uses for its own setups, mapped onto the frozen
# §3 vocabulary. Mapping rather than passing through, because the engine's
# vocabulary changes with its own releases and a report grouped by free-form
# strings silently splits one cohort into three when a label is renamed.
_SETUP_MAP: dict[str, str] = {
    "PULLBACK": PULLBACK,
    "PULLBACK_CONTINUATION": PULLBACK,
    "DIP": PULLBACK,
    "REVERSAL": REVERSAL,
    "MEAN_REVERSION": REVERSAL,
    "FAILED_BREAKOUT": REVERSAL,
    "BREAKOUT": BREAKOUT,
    "OPENING_RANGE": BREAKOUT,
    "RANGE_BREAK": BREAKOUT,
    "CONTINUATION": CONTINUATION,
    "TREND": CONTINUATION,
    "TREND_CONTINUATION": CONTINUATION,
    "MOMENTUM": CONTINUATION,
    "VOLATILITY_EXPANSION": VOLATILITY_EXPANSION,
    "SQUEEZE": VOLATILITY_EXPANSION,
    "RANGE_EXPANSION": RANGE_EXPANSION,
    "PAIR": RELATIVE_VALUE,
    "RELATIVE_VALUE": RELATIVE_VALUE,
    "SPREAD": RELATIVE_VALUE,
}


def classify_type(setup: str | None, *, direction: str | None = None) -> str:
    """Map an upstream setup name onto the frozen vocabulary.

    An unmapped name becomes DIRECTIONAL when a direction is known and
    UNCLASSIFIED otherwise. It never becomes a new label: §3 says the vocabulary
    is for attribution, and a taxonomy that grows itself at write time cannot
    support a stable group-by.
    """
    key = (setup or "").strip().upper().replace("-", "_").replace(" ", "_")
    if key in _SETUP_MAP:
        return _SETUP_MAP[key]
    for word, label in _SETUP_MAP.items():
        if word and word in key:
            return label
    return DIRECTIONAL if direction else UNCLASSIFIED


def source_of(engine_class: str | None) -> str:
    """ENGINE when the system itself asked to act, BOARD otherwise.

    A WAIT or an AVOID is a board observation, not an engine one: the engine did
    not select it, so counting it as an engine row would flatter the engine's
    capture rate with opportunities it explicitly declined.
    """
    return ENGINE if (engine_class or "").upper() == "BUY" else BOARD
