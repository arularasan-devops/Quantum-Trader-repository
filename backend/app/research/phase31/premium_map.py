"""Phase 31 §4 — the stated bridge from an underlying move to a premium percent.

This module contains no measurement. It is arithmetic with two inputs that are
declared on every row it produces:

* ``delta`` — how much premium a one-point underlying move buys;
* ``premium_over_strike`` — the premium as a fraction of the strike, taken from
  the account's own recorded entries where available, so the bridge reflects the
  premiums actually paid rather than a textbook figure.

For a target premium gain ``P%``::

    required_underlying_move_pct = P% * premium_over_strike / delta

A +20% gain on a premium worth 0.44% of spot at delta 0.50 therefore needs about
a 0.18% underlying move — and *that* number is then looked up in the measured
five-year excursion table, which is where every probability and timing figure in
the report comes from.

The output is always labelled ``ASSUMED_PREMIUM_MAP``. It is not an option
result: it ignores theta, changing implied volatility, spread and the fact that a
premium does not move linearly. Those all make the real premium outcome worse
than this arithmetic, never better, so the mapping is an optimistic bound and is
reported as one.
"""
from __future__ import annotations

from app.research.phase31 import (
    ASSUMED_PREMIUM_MAP,
    DELTA_BANDS,
    HORIZONS,
    PCT_GRID,
    PREMIUM_TARGETS_PCT,
)

# Used when an instrument has no recorded entries to measure a ratio from. Stated
# rather than silently assumed, and flagged in the row.
DEFAULT_PREMIUM_OVER_STRIKE_PCT = {
    "NIFTY": 0.45,
    "BANKNIFTY": 0.55,
    "CRUDEOIL": 2.50,
    "NATURALGAS": 5.00,
}
FALLBACK_PREMIUM_OVER_STRIKE_PCT = 1.00

OPTIMISTIC_BOUND_NOTE = (
    "theta, implied-volatility change and the bid/ask spread are excluded, and "
    "premium response is treated as linear in delta; each of those makes the "
    "real premium outcome worse, so this is an optimistic bound"
)


def premium_over_strike_pct(instrument: str, measured: dict) -> tuple[float, str]:
    """Premium-to-strike ratio for an instrument, and where it came from."""
    row = (measured or {}).get(instrument.upper())
    if row and row.get("n") and row.get("median_premium_over_strike_pct"):
        return float(row["median_premium_over_strike_pct"]), (
            f"measured from {int(row['n'])} recorded entries"
        )
    fixed = DEFAULT_PREMIUM_OVER_STRIKE_PCT.get(instrument.upper())
    if fixed is not None:
        return fixed, "stated default, no recorded entries for this instrument"
    return FALLBACK_PREMIUM_OVER_STRIKE_PCT, "stated fallback ratio"


def required_underlying_pct(
    target_premium_pct: float, premium_ratio_pct: float, delta: float
) -> float:
    """Underlying move, in percent of price, implied by a premium target."""
    if delta <= 0:
        raise ValueError("delta must be positive")
    return target_premium_pct * premium_ratio_pct / 100.0 / delta


def _nearest_threshold(required_pct: float) -> float:
    """The measured threshold used to answer the row, always >= the requirement.

    Rounding *up* to a measured threshold keeps the reported reach rate
    conservative: the answer describes a move at least as large as the one the
    premium target needs.
    """
    for thr in PCT_GRID:
        if thr >= required_pct:
            return thr
    return PCT_GRID[-1]


def table(instrument: str, measured_ratio: dict, thresholds: dict) -> list[dict]:
    """One row per premium target and delta band, answered from measured data.

    ``thresholds`` is the ``thresholds`` block of an
    :func:`app.research.phase31.excursion.summarize` result for this instrument
    and side, so every probability and timing figure is measured even though the
    requirement that selected it is assumed.
    """
    ratio_pct, ratio_source = premium_over_strike_pct(instrument, measured_ratio)
    rows: list[dict] = []
    for target in PREMIUM_TARGETS_PCT:
        for delta in DELTA_BANDS:
            need = required_underlying_pct(target, ratio_pct, delta)
            thr = _nearest_threshold(need)
            off_grid = need > PCT_GRID[-1]
            m = {} if off_grid else (thresholds.get(f"{thr:.2f}") or {})
            row = {
                "basis": ASSUMED_PREMIUM_MAP,
                "instrument": instrument,
                "target_premium_pct": target,
                "delta": delta,
                "premium_over_strike_pct": round(ratio_pct, 4),
                "premium_ratio_source": ratio_source,
                "required_underlying_move_pct": round(need, 4),
                "measured_threshold_used_pct": None if off_grid else thr,
                "threshold_at_least_requirement": bool(
                    not off_grid and thr >= need),
                "measured_reach_pct_of_instants": m.get(
                    "reached_pct_of_instants"),
                "measured_median_minutes_to_reach": (
                    m.get("minutes_to_reach") or {}).get("p50"),
                "measured_adverse_same_size_first_pct": m.get(
                    "adverse_same_size_first_pct"),
                "note": OPTIMISTIC_BOUND_NOTE,
            }
            for h in HORIZONS:
                row[f"measured_reach_within_{h}m_pct"] = m.get(
                    f"reached_within_{h}m_pct")
            if off_grid:
                row["off_grid"] = (
                    f"needs a {need:.2f}% underlying move, beyond the frozen "
                    f"{PCT_GRID[-1]:.2f}% grid, so it is not answered rather "
                    "than extrapolated"
                )
            rows.append(row)
    return rows
