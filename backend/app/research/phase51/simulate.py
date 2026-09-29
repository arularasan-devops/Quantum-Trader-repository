"""Phase 51 §7/§13 — costed simulation, and the three edges kept apart.

Execution is not re-implemented here. It is delegated to the Phase 24 resolver,
which already decides on a bar's close, fills at the next bar's open, flattens
at the session close, gives a same-bar stop/target tie to the **stop**, and
charges brokerage, statutory charges and slippage both ways through the Phase 19
cost model. Reusing it means Phase 51 cannot drift into a friendlier cost model
than the phases it hopes to feed, which is the usual way a research layer
produces numbers nobody can reconcile.

Two things this module adds, both of which change the answer:

* **trades from one rule never overlap.** A condition that holds for two hundred
  consecutive bars would otherwise contribute two hundred trades that are one
  observation of one move, and every confidence interval and p-value computed
  from that count would be wrong by the same factor. A rule may take a new
  position only once its previous one could no longer be open;
* **the exit grid is quarantined.** Entry discovery runs on **one** frozen
  geometry (a one-ATR stop, a 1.5R target, a bounded hold). Only rules that
  survive on that geometry are handed to the exit search, and the exit search's
  own trials are counted in their own denominator. Choosing the best of ten
  exits for every candidate and then reporting the entry as the discovery is how
  an exit fit gets published as a market relationship.

The vehicle edge is not simulated at all. Choosing between futures, a call and a
put requires the premium each alternative would have been filled at, and a
candle has no premium: ``VEHICLE_SELECTION_EDGE`` is therefore reported as
needing data, never as a result.
"""
from __future__ import annotations

import numpy as np

from app.research.phase24 import outcomes as p24outcomes
from app.research.phase51 import geometry

HORIZON_BARS = p24outcomes.HORIZON_BARS          # 180, from the shared resolver
ENTRY_DELAY_BARS = p24outcomes.ENTRY_DELAY_BARS  # decide on close, fill next open

# The frozen entry geometry. One stop, one target, one hold, declared here and
# never varied during entry discovery.
ENTRY_STOP_ATR = 1.0
ENTRY_TARGET_R = 1.5

# A measured fact about this data that dictates the geometry: on 1-minute NIFTY
# the median 14-bar ATR is about 7.8 points and the modelled round trip on one
# futures lot is about 7.6 points. A one-ATR stop with a 1.5R target therefore
# aims at a move worth roughly 1.5 costs, and **no** conditional relationship,
# however real, can be net positive on it — the search would return zero for an
# arithmetic reason that has nothing to do with the market.
#
# So the risk per trade carries a floor: at least MIN_RISK_COST_MULTIPLE times
# the modelled round-trip cost, which puts the 1.5R target at or beyond three
# costs — the same minimum-economics rule the earlier phases already apply to
# live plans. The floor is declared here, before anything is measured, and is
# not tuned per candidate.
MIN_RISK_COST_MULTIPLE = 2.0

# If honouring that floor would need a stop wider than this many ATR, the bar is
# not tradable at this cost on this instrument and the candidate is dropped
# rather than counted as a loss. That is a cost-feasibility refusal, and the
# count of refusals is reported.
MAX_RISK_ATR = 6.0

# A rule's next decision may not come within this many bars of its last, so two
# trades from one rule can never be open at once.
COOLDOWN_BARS = HORIZON_BARS

# §13's exit grid, applied only to entries that already survived. Each row is a
# stop multiple in ATR, a target in R, and a hold cap in bars.
EXIT_VARIANTS: tuple[tuple[str, float, float, int], ...] = (
    ("FIXED_STOP_1ATR_TARGET_1R", 1.0, 1.0, HORIZON_BARS),
    ("FIXED_STOP_1ATR_TARGET_1.5R", 1.0, 1.5, HORIZON_BARS),
    ("FIXED_STOP_1ATR_TARGET_2.5R", 1.0, 2.5, HORIZON_BARS),
    ("FIXED_STOP_1ATR_TARGET_4R", 1.0, 4.0, HORIZON_BARS),
    ("ATR_STOP_0.5_TARGET_1.5R", 0.5, 1.5, HORIZON_BARS),
    ("ATR_STOP_1.5_TARGET_1.5R", 1.5, 1.5, HORIZON_BARS),
    ("ATR_STOP_2_TARGET_2.5R", 2.0, 2.5, HORIZON_BARS),
    ("TIME_EXIT_30_BARS", 1.0, 1.5, 30),
    ("TIME_EXIT_60_BARS", 1.0, 1.5, 60),
    ("TIME_EXIT_120_BARS", 1.0, 1.5, 120),
)

# Trailing, breakeven, partial-profit and giveback rules need the path *between*
# the stop and the target, bar by bar, and a 1-minute candle cannot say whether
# the low or the high came first inside the bar it is reported on. They are
# named here so the report can say they were not silently dropped.
PATH_DEPENDENT_EXITS_REFUSED = {
    "TRAILING_STOP": "needs intrabar order, which a 1-minute candle does not carry",
    "BREAKEVEN_RATCHET": "needs intrabar order to know the ratchet armed before the pullback",
    "PARTIAL_PROFIT": "needs intrabar order for the first scale before the reversal",
    "MFE_GIVEBACK": "needs intrabar order to place the giveback trigger against the peak",
}


def session_last_bar_index(sess: np.ndarray) -> np.ndarray:
    """For every bar, the index of the last bar of its own session."""
    out = np.empty(sess.size, dtype=np.int64)
    for a, b in geometry.session_bounds(sess):
        out[a:b] = b - 1
    return out


def non_overlapping(mask: np.ndarray, cooldown: int = COOLDOWN_BARS) -> np.ndarray:
    """Indices where ``mask`` fires, thinned so no two are within ``cooldown``.

    Greedy and chronological: the first qualifying bar is taken and the next is
    the first one far enough after it. Taking the *best* of a cluster instead
    would be a look-ahead, and taking all of them would count one move many
    times.
    """
    fires = np.nonzero(mask)[0]
    if fires.size == 0:
        return fires
    kept: list[int] = []
    last = -(10**9)
    for i in fires:
        if i - last >= cooldown:
            kept.append(int(i))
            last = int(i)
    return np.array(kept, dtype=np.int64)


def effective_atr(
    f: dict[str, np.ndarray],
    *,
    stop_atr: float = ENTRY_STOP_ATR,
) -> tuple[np.ndarray, np.ndarray]:
    """Per bar: the risk-defining ATR after the cost floor, and feasibility.

    Both inputs are known at the decision bar — the ATR of the bars up to it and
    the cost the model charges on its close — so applying the floor introduces
    no look-ahead.
    """
    atr = f["atr"]
    cost = f["estimated_cost_points"]
    floor = (MIN_RISK_COST_MULTIPLE * cost) / max(float(stop_atr), 1e-9)
    atr_eff = np.where(np.isfinite(floor), np.maximum(atr, floor), atr)
    feasible = (
        np.isfinite(atr) & (atr > 0)
        & np.isfinite(atr_eff)
        & (atr_eff <= MAX_RISK_ATR * atr)
    )
    return atr_eff, feasible


def resolve(
    instrument: str,
    f: dict[str, np.ndarray],
    idx: np.ndarray,
    side: int,
    *,
    stop_atr: float = ENTRY_STOP_ATR,
    target_r: float = ENTRY_TARGET_R,
    horizon: int = HORIZON_BARS,
    spread_multiplier: float = 1.0,
    entry_delay_bars: int = ENTRY_DELAY_BARS,
) -> p24outcomes.Outcomes:
    """Resolve one rule's candidates through the shared Phase 24 resolver.

    The resolver derives risk as ``stop_atr * atr[idx]``, so the cost floor is
    applied by handing it an **effective** ATR: the larger of the real ATR and
    the ATR that a cost-respecting stop would imply. Every other price the
    resolver reads is the real bar.
    """
    sess = f["session"].astype(np.int64)
    atr_eff, feasible = effective_atr(f, stop_atr=stop_atr)
    # Bars where no cost-respecting stop fits inside the declared ATR bound are
    # dropped, not counted as losses: the refusal is an economic fact about the
    # instrument, and charging it to the rule would defame the rule.
    idx = idx[feasible[idx]] if idx.size else idx
    sides = np.full(idx.size, int(side), dtype=np.int64)
    # Per candidate, the last bar of the session it was decided in: the resolver
    # flattens there, so an intraday trade can never be settled by a bar on the
    # far side of an overnight gap.
    last_bar = (session_last_bar_index(sess)[idx] if idx.size
                else np.zeros(0, dtype=np.int64))
    return p24outcomes.resolve(
        instrument,
        f["high"], f["low"], f["open"], f["ts"].astype(np.int64),
        idx, sides, atr_eff, last_bar,
        horizon=horizon,
        entry_delay_bars=entry_delay_bars,
        slippage_points=None,
        spread_multiplier=spread_multiplier,
        stop_atr=stop_atr,
        t1_r=target_r,
    )


def tradable(o: p24outcomes.Outcomes) -> np.ndarray:
    """Candidates that actually resolved into a measurable net result."""
    return o.resolved & np.isfinite(o.net_r) & np.isfinite(o.net_points)


def exit_variant_results(
    instrument: str,
    f: dict[str, np.ndarray],
    idx: np.ndarray,
    side: int,
) -> list[tuple[str, p24outcomes.Outcomes]]:
    """§13's exit grid on one fixed entry set, as its own separate search.

    The entries are frozen before this runs: the same bars, the same side. Only
    the exit changes, so whatever this finds is a profit-capture effect and not
    an entry effect wearing a different stop.
    """
    out: list[tuple[str, p24outcomes.Outcomes]] = []
    for name, stop_atr, target_r, horizon in EXIT_VARIANTS:
        out.append((name, resolve(
            instrument, f, idx, side,
            stop_atr=stop_atr, target_r=target_r, horizon=horizon,
        )))
    return out
