"""The frozen BANKNIFTY/NIFTY relative-value configuration.

Frozen on the instruction "freeze the notional-neutral relationship exactly as
specified, do not optimize it further". Every value here is copied from the
pre-registration (``/home/ubuntu/STRUCTURAL_EDGE_PREREGISTRATION.md``) and from
the notional-neutral arm reported in ``STRUCTURAL_EDGE_RESULT.md``. Nothing in
this package may choose a different value at runtime:

* one measure (RATIO), not three;
* one hedge (CONTRACT_VALUE, integer lots), not two;
* one threshold (1.5 sd), not a set.

The retest is only a retest if the configuration cannot move, so the fingerprint
below is stored on every captured row and every paper trade. A row whose
fingerprint differs from the current spec is reported separately rather than
pooled -- that is how a silent edit shows up as evidence instead of vanishing.

The MIN_* gate is the pre-declared stopping rule: no verdict is computed, and no
promotion is possible, until this much independent evidence with MEASURED
execution costs exists. It exists to stop the sample being peeked at.
"""
from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

# --- the relationship ------------------------------------------------------
LEG_A = "BANKNIFTY"
LEG_B = "NIFTY"
MEASURE = "RATIO"           # leg A price / leg B price
HEDGE = "CONTRACT_VALUE"    # integer lots, notional-matched; no beta arm
THRESHOLD = 1.5             # entry at |z| >= 1.5 standard deviations
WINDOW = 120                # observations used to estimate mean/sd, causal only
CONVERGE_Z = 0.5            # exit when |z| <= 0.5
DIVERGE_EXTRA = 1.5         # stop when |z| >= |z_entry| + 1.5
MAX_HOLD = 60               # observations
SESSION_END_FLAT = True     # never carried overnight
ONE_POSITION = True         # no overlapping positions

# --- the pre-declared evidence bar ----------------------------------------
# Nothing is judged before all three are met. 50 trades in a single six-week
# window is what made the first result REQUIRES_MORE_DATA; these numbers say
# what would make it answerable, and they are declared before the data exists.
MIN_TRADES = 200
MIN_SESSIONS = 60
MIN_MEASURED_COST_PCT = 80.0   # % of trades whose both legs were quoted two-sided

# --- Test B (near/next futures basis) -------------------------------------
# Capture-only thresholds: what has to exist before the basis question can be
# asked at all. No entry rule is frozen here because Test B was never
# measurable -- freezing an untested rule would be a search, not a retest.
BASIS_MIN_PAIRED_SNAPSHOTS = 5000
BASIS_MIN_SESSIONS = 60

FROZEN = MappingProxyType({
    "leg_a": LEG_A,
    "leg_b": LEG_B,
    "measure": MEASURE,
    "hedge": HEDGE,
    "threshold_sd": THRESHOLD,
    "window": WINDOW,
    "converge_z": CONVERGE_Z,
    "diverge_extra": DIVERGE_EXTRA,
    "max_hold": MAX_HOLD,
    "session_end_flat": SESSION_END_FLAT,
    "one_position": ONE_POSITION,
    "min_trades": MIN_TRADES,
    "min_sessions": MIN_SESSIONS,
    "min_measured_cost_pct": MIN_MEASURED_COST_PCT,
})


def fingerprint() -> str:
    """Stable short hash of the frozen configuration."""
    blob = json.dumps(dict(FROZEN), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def as_dict() -> dict:
    """The frozen spec plus its fingerprint, for the API and the report."""
    out = dict(FROZEN)
    out["fingerprint"] = fingerprint()
    return out
