"""Phase 10 §15 — capacity and the tier-split before/after. RESEARCH ONLY.

The mechanism is arithmetic: a rate-limited historical API shared by 50 instruments
gives each instrument a fiftieth of the budget, and the recorded consequence in this
dataset is 46.5% of one-minute bars missing, 38% of signals firing on data the
engine itself flagged stale, ``AB1021`` rate-limit errors during warm-up, and a
2.5-minute startup. Six to eight instruments would each get six to eight times the
budget.

That is a reason to expect an improvement. It is not a measurement of one, and this
module is written to keep those two things apart, because "obviously better" is how
an unmeasured change gets promoted.

So ``compare`` requires a *recorded* after-session and returns
``AFTER_NOT_MEASURED`` without one, however large the before/after arithmetic looks.
The estimate is still computed and shown — labelled ``ARITHMETIC_ESTIMATE`` — since
the operator needs it to decide whether the experiment is worth running.

The deep watchlist is not chosen here. §2 reserves that for the operator, and a
default list invented in research would become the de facto configuration.
"""
from __future__ import annotations

BEFORE = "BEFORE_TIER_SPLIT"
AFTER = "AFTER_TIER_SPLIT"
NOT_MEASURED = "AFTER_NOT_MEASURED"
ESTIMATE = "ARITHMETIC_ESTIMATE"

METRICS = ("instruments_deep", "instruments_broad", "warm_calls_per_cycle",
           "chain_calls_per_cycle", "missing_bar_pct", "stale_signal_pct",
           "startup_seconds", "scanner_cycle_seconds", "cpu_pct", "memory_mb",
           "rest_polls", "websocket_ticks", "rate_limit_errors")


def snapshot(*, label: str, **values: float | int | None) -> dict:
    """One measurement point. Unsupplied metrics stay ``None``, never zero.

    Zero and unmeasured are different facts and a report that conflates them will
    claim a rate-limit error count of zero for a session that never counted them.
    """
    unknown = [m for m in METRICS if values.get(m) is None]
    return {
        "label": label,
        "metrics": {m: values.get(m) for m in METRICS},
        "unmeasured": unknown,
        "complete": not unknown,
    }


def estimate_from_before(before: dict, deep_count: int) -> dict:
    """What the budget arithmetic alone predicts. Labelled as an estimate."""
    b = before["metrics"]
    deep_before = b.get("instruments_deep")
    factor = (None if not deep_before or not deep_count
              else round(float(deep_before) / float(deep_count), 2))
    return {
        "status": ESTIMATE,
        "deep_before": deep_before,
        "deep_after": deep_count,
        "per_instrument_budget_multiple": factor,
        "warm_calls_per_cycle": None if deep_count is None else deep_count,
        "chain_calls_per_cycle": None if deep_count is None else deep_count,
        "reasoning": (
            f"the same shared rate-limited budget spread over {deep_count} deep "
            f"instruments instead of {deep_before} gives each of them {factor}x the "
            f"calls per cycle" if factor else
            "not computable without a recorded deep-instrument count"),
        "does_not_predict": [
            "missing_bar_pct", "stale_signal_pct", "scanner_cycle_seconds",
            "rate_limit_errors"],
        "why_not": "bar completeness depends on the provider's behaviour under a "
                   "lighter load, not only on the call count. The direction is "
                   "expected; the magnitude must be measured",
    }


def compare(before: dict, after: dict | None, *, deep_count: int) -> dict:
    """The §15 table. Without a recorded after-session, no improvement is claimed."""
    est = estimate_from_before(before, deep_count)
    if after is None:
        return {
            "status": NOT_MEASURED,
            "before": before,
            "after": None,
            "estimate": est,
            "deltas": None,
            "verdict": (
                "the before-session is recorded and the after-session does not "
                "exist: every session in this dataset ran with the full watchlist. "
                "No claim that the tier split improved bar completeness, stale rate, "
                "latency, startup or capacity is permitted, and none is made"),
            "what_would_change_this": (
                f"one session recorded with a deep watchlist of {deep_count} "
                f"instrument(s) configured, exported nightly, then this comparison "
                f"re-run. That is a single session of work and it converts the "
                f"estimate above into a measurement"),
        }

    deltas = {}
    for m in METRICS:
        b, a = before["metrics"].get(m), after["metrics"].get(m)
        deltas[m] = (None if b is None or a is None
                     else {"before": b, "after": a,
                           "delta": round(float(a) - float(b), 3),
                           "pct_change": None if not b else
                           round(100.0 * (float(a) - float(b)) / float(b), 1)})
    improved = [m for m in ("missing_bar_pct", "stale_signal_pct", "startup_seconds",
                            "scanner_cycle_seconds", "rate_limit_errors")
                if deltas.get(m) and deltas[m]["delta"] < 0]
    worsened = [m for m in ("missing_bar_pct", "stale_signal_pct", "startup_seconds",
                            "scanner_cycle_seconds", "rate_limit_errors")
                if deltas.get(m) and deltas[m]["delta"] > 0]
    return {
        "status": "MEASURED_SINGLE_SESSION_PAIR",
        "before": before,
        "after": after,
        "estimate": est,
        "deltas": deltas,
        "improved_metrics": improved,
        "worsened_metrics": worsened,
        "verdict": ("measured on one before/after pair. A single pair confounds the "
                    "change with the day: two sessions differ in volatility, "
                    "provider load and instrument mix as well as in configuration. "
                    "Directional evidence, not a validated improvement"),
    }
