"""Phase 27 — the execution model, restated so a reader can audit it.

No arithmetic happens here. The resolver itself is Phase 24's
:mod:`app.research.phase24.outcomes`, unchanged and shared, because the point of
this study is that only the bar size differs. What this module does is state, in
one place and in the report, exactly what that resolver charges and assumes — the
things a net R figure is meaningless without.
"""
from __future__ import annotations

from app.research.phase24 import outcomes

# Re-exported geometry, so the report quotes the constants that were actually used
# rather than a copy that could drift from them.
STOP_ATR = outcomes.STOP_ATR
T1_R = outcomes.T1_R
T2_R = outcomes.T2_R
T3_R = outcomes.T3_R
ENTRY_DELAY_BARS = outcomes.ENTRY_DELAY_BARS


def execution_note() -> dict:
    """What was charged, what was assumed, and what is unmeasured."""
    return {
        "signal": "taken on the close of the decision bar, never inside it",
        "entry": (
            f"the open of the next bar ({ENTRY_DELAY_BARS} bar later), unadjusted; "
            "slippage is charged by the cost model on both legs and is not also "
            "added to the fill price, which would charge the entry twice"
        ),
        "stop": f"{STOP_ATR} x ATR at entry, in points",
        "targets": {"t1_r": T1_R, "t2_r": T2_R, "t3_r": T3_R},
        "resolution": (
            "read forward from later bars only; if a single bar's range contains "
            "both the stop and the target, the stop is taken, because the order of "
            "the two inside one bar is unknowable — and at 5 or 15 minutes that "
            "ambiguity is wider than at one minute, not narrower"
        ),
        "session": (
            "every trade is flattened at its own session's close; no intraday "
            "result is ever settled by a bar on the other side of an overnight gap"
        ),
        "costs": (
            "brokerage per order plus STT/CTT and exchange/SEBI/stamp/GST on "
            "turnover, from the shared futures cost model, charged per trade"
        ),
        "spread": (
            "unmeasured — the futures feed publishes candles, not depth. It is "
            "reported as unmeasured rather than as zero, and the slippage grid is "
            "the honest stand-in for it"
        ),
        "cost_model": "app.research.phase19.futcosts",
        "resolver": "app.research.phase24.outcomes (shared, unmodified)",
    }
