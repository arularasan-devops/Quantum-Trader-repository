"""Measured target-before-stop rates, so the board can quote a real frequency
instead of a manufactured one.

The engine used to publish ``win_probability = 50 + 47 * strength * agreement``.
That number cannot fall below 50 whatever the market does, averaged 78.3 while
the event it named happened 42.8% of the time, and scored -0.675 against the
forecast you get for free by always saying the base rate — that is, acting on it
was worse than ignoring it. It was a conviction meter wearing a probability's
units, and sizing a position on it meant sizing for something half as frequent.

What replaces it is the frequency actually observed, keyed on the only thing that
was found to move that frequency: **how far the target sits from the stop**. Rank
correlations between every published score and target-before-stop collapse to
~0.00-0.07 once reward:risk is held still, so the score cannot forecast the hit
rate; reward:risk can, and monotonically.

Source: Phase 14 replay of this engine over NIFTY 1-minute history,
2021-08-27 → 2026-08-27, chronological holdout (5,752 candidates).

Two limits that must travel with these numbers:

* They are **underlying** outcomes. Option spread, theta and slippage are not in
  them, and they hurt the near-target bands hardest — a 0.8R target has the least
  room to absorb a spread. So treat these as a ceiling, not a forecast.
* A higher hit rate is **not** better. The 0.82R band is hit most often (53.2%)
  and is the only band with negative expectancy (-0.023R), while the 1.69R band
  is hit least often of the profitable ones (38.2%) and pays best (+0.034R).
  Publishing the rate without its expectancy would invite exactly the wrong
  optimisation.
"""

from __future__ import annotations

# (reward:risk from, to, observed target-before-stop %, expectancy R, candidates)
_BANDS: tuple[tuple[float, float, float, float, int], ...] = (
    (0.0, 1.0, 53.2, -0.023, 1128),
    (1.0, 1.5, 44.5, +0.015, 1997),
    (1.5, 2.0, 38.2, +0.034, 2040),
    (2.0, 3.0, 32.5, +0.027, 587),
)

WINDOW = ("measured on 5 yrs NIFTY 1-min (2021-08→2026-08) holdout, "
          "underlying only — option spread and theta not included")

# Whole-book rate, for when reward:risk is unknown.
BASE_RATE_PCT = 42.8


def observed_target_rate(reward_risk: float | None) -> dict | None:
    """How often a target this far away was actually reached before the stop.

    Returns ``None`` rather than guessing when there is no reward:risk to key on
    — a missing number is honest, an invented one is not.
    """
    if reward_risk is None or reward_risk <= 0:
        return None
    for lo, hi, rate, exp_r, n in _BANDS:
        if reward_risk < hi:
            return {
                "target_hit_rate_pct": rate,
                "expectancy_r": exp_r,
                "sample": n,
                "band": f"reward:risk {lo:g}-{hi:g}",
                "window": WINDOW,
            }
    lo, _hi, rate, exp_r, n = _BANDS[-1]
    # Beyond the widest measured band the nearest evidence is that band itself,
    # and it is labelled as an extrapolation so it cannot be read as measured.
    return {
        "target_hit_rate_pct": rate,
        "expectancy_r": exp_r,
        "sample": n,
        "band": f"reward:risk {lo:g}+ (extrapolated beyond measured range)",
        "window": WINDOW,
    }
