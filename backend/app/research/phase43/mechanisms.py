"""Phase 43 §1 — every candidate written down before anything is measured.

This is a fixed list, not a search. Phase 24 already searched this vocabulary
exhaustively on the same five years and published the result; re-running that
search and reading the best of it as a new finding is the mistake this phase was
told not to repeat. What is new here is a *predeclared* question — does
cost-aware selection improve a mechanism that is otherwise held constant — and
the arms that answer it are chosen for being round numbers, not for what they do
to the outcome.

Two things are shared by every candidate:

* **one baseline direction rule.** ``BASE`` is trend agreement plus momentum
  agreement with the candidate's own side. It is deliberately ordinary and
  deliberately not tuned: the families are tested as *filters on it*, so a
  family's effect is the difference it makes, not the mechanism it hides inside.
  The baseline is also measured alone, as the control every arm competes with;
* **every quantity comes from the decision bar's close or earlier.** That
  includes the cost: :func:`cost_estimate` prices a round trip at the decision
  close, not at the fill, because the fill is the next bar's open and a trader
  at the decision instant does not have it. Using the realised fill would leak
  one bar of the future into the ratio the arms threshold.
"""
from __future__ import annotations

import numpy as np

from app.market.instruments import get_spec
from app.research import phase43
from app.research.phase24 import outcomes as p24out

# The one baseline direction rule, in the Phase 24 condition vocabulary.
BASE = ("trend_5m_and_15m_agree", "momentum_agrees_0p5atr")
BASE_ID = "BASE_TREND_MOMENTUM"

# Cost multiples, declared before measurement. Round numbers spanning "barely
# covers the charges" to "the move has to be eight times them".
RATIO_ARMS = (2.0, 4.0, 8.0)

# The regime states of family D. Named, and taken from the existing vocabulary
# so none of them is a new indicator invented to raise the hypothesis count.
#
# `trend_15m_agrees` is deliberately absent: BASE already requires the 5- and
# 15-minute trends to agree, so a "15-minute trend aligned" state would select
# the parent arm's entire cohort and print its numbers a second time under a
# different name. The trend/range distinction family D asks for is carried by
# TREND_STACKED (a longer alignment BASE does not imply) against
# RANGE_NEAR_VWAP (price inside one ATR of the session's VWAP).
REGIME_STATES = (
    ("VOL_EXPANDING", "volatility_expanding"),
    ("VOL_COMPRESSED", "volatility_compressed"),
    ("TREND_STACKED", "ema_stack_agrees"),
    ("RANGE_NEAR_VWAP", "near_vwap_1atr"),
    ("GAP_OPEN_DAY", "gap_day_0p5pct"),
    ("FLAT_OPEN_DAY", "flat_open_day"),
    ("CANDLE_EXPANSION", "candle_expansion_1p5x"),
)

# Time of day is asked for separately by family D and is already bucketed.
TIME_STATES = (
    ("TIME_OPEN_0930_1030", "time_open_0930_1030"),
    ("TIME_MORNING_1030_1200", "time_morning_1030_1200"),
    ("TIME_MIDDAY_1200_1400", "time_midday_1200_1400"),
    ("TIME_LATE_1400_CLOSE", "time_late_1400_close"),
)


def cost_estimate(instrument: str, close: np.ndarray) -> np.ndarray:
    """Round-trip cost in points, priced at the decision bar's own close.

    The shared Phase 19 futures cost model is the only source of charges here;
    nothing about brokerage or statutory rates is restated. Entry and exit are
    both the decision close, which is what a trader can price at the moment of
    deciding: it assumes a flat round trip rather than a favourable one, so the
    estimate is neutral rather than optimistic.

    The spread is *not* included, because this dataset has no quoted book. Every
    net figure downstream is therefore optimistic by exactly one spread, which is
    what :data:`phase43.COST_MULTIPLIERS` exists to stress.
    """
    lot = int(get_spec(instrument).lot_size or 1)
    return p24out.cost_points_per_trade(
        instrument, close, close, lot_size=lot, slippage_points=None
    )


def ratios(instrument: str, feat: dict) -> dict[str, np.ndarray]:
    """The two decision-time cost ratios of family A.

    * ``expected_move_over_cost`` — trailing ATR(14) divided by the estimated
      round trip. "How many charges wide is the move this market has been
      making?"
    * ``leg_range_over_cost`` — the size of the most recent 30-bar expansion leg,
      in points, divided by the same cost. A different question from the first:
      ATR is an average bar, a leg is a whole move.

    Both are NaN wherever their inputs are not formed; a NaN never passes an arm.
    """
    cost = cost_estimate(instrument, np.asarray(feat["close"], dtype=np.float64))
    atr = np.asarray(feat["atr"], dtype=np.float64)
    leg = np.asarray(feat["expansion_atr"], dtype=np.float64) * atr
    safe = np.where(np.isfinite(cost) & (cost > 0), cost, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        return {
            "expected_move_over_cost": atr / safe,
            "leg_range_over_cost": leg / safe,
            "cost_points": cost,
        }


def declared(instruments: tuple[str, ...]) -> list[dict]:
    """Every candidate this phase will measure, in family order A, B, C, D.

    Family B is declared in :mod:`pair` because it spans two instruments rather
    than one, and family C is derived by comparing the same family-A candidate
    across instruments rather than by adding hypotheses — a like-for-like vehicle
    comparison must not be a different rule on each vehicle.
    """
    rows: list[dict] = []
    for inst in instruments:
        rows.append({
            "candidate": f"A0_CONTROL_{inst}",
            "family": phase43.FAMILY_COST,
            "instrument": inst,
            "mechanism": (
                "control: the baseline direction rule with no cost-aware "
                "selection, so every arm below has something to beat"
            ),
            "conditions": list(BASE),
            "ratio": None,
            "threshold": None,
            "is_control": True,
        })
        for key, label in (
            ("expected_move_over_cost", "A1_EXPECTED_MOVE_OVER_COST"),
            ("leg_range_over_cost", "A2_LEG_RANGE_OVER_COST"),
        ):
            for arm in RATIO_ARMS:
                rows.append({
                    "candidate": f"{label}_{arm:g}X_{inst}",
                    "family": phase43.FAMILY_COST,
                    "instrument": inst,
                    "mechanism": (
                        f"baseline direction rule, taken only when {key} at the "
                        f"decision close is at least {arm:g}x the modelled "
                        "round-trip cost"
                    ),
                    "conditions": list(BASE),
                    "ratio": key,
                    "threshold": float(arm),
                    "is_control": False,
                })
    return rows


def regime_candidates(instrument: str, best_arm: dict) -> list[dict]:
    """Family D: the surviving family-A arm split by predeclared state.

    The arm being split is chosen on the development window only, by
    :mod:`evaluate`. Splitting the *best* arm rather than every arm is what keeps
    the hypothesis count at ten states instead of ten times every arm.
    """
    rows: list[dict] = []
    for label, cond in REGIME_STATES + TIME_STATES:
        rows.append({
            "candidate": f"D_{label}_{instrument}",
            "family": phase43.FAMILY_REGIME,
            "instrument": instrument,
            "mechanism": (
                f"{best_arm['candidate']} restricted to {label}, to test whether "
                "opportunity quality is a property of the state rather than of "
                "the rule"
            ),
            "conditions": list(best_arm["conditions"]) + [cond],
            "ratio": best_arm["ratio"],
            "threshold": best_arm["threshold"],
            "is_control": False,
            "state": label,
            "derived_from": best_arm["candidate"],
        })
    return rows
