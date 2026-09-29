"""Phase 26 §2 — the exit variants, resolved on the same stored quotes.

Phase 25's exit was fixed: a stop at a percentage of premium and a single 1.5R
target. On the captured books that combination resolved 82.5% of trades as a flat
timeout, which is the finding this module exists to attack. Each variant here
changes the exit and nothing else — same candidates, same entry fill, same
slippage, same cost model — so a difference between two rows is the exit rule and
cannot be anything else.

Rules that hold for every variant, because relaxing any of them would make the
comparison flattering rather than useful:

* **every exit is a bid minus slippage.** A target order is filled at the target
  level only when a stored quote actually reached it; a stop is filled at the
  quote that was really there, which on a gap is worse than the stop level;
* **a stop and a target inside the same quote resolve as the stop.** Between two
  stored quotes nothing is observable and nothing is assumed;
* **a scale-out pays for the extra exit order.** Brokerage is per order, so
  taking half off and exiting the rest later is charged as one entry and two
  exits, with statutory charges on each exit's own quantity. A scale-out that
  looks better than a single exit has already paid for the privilege;
* **the trail is a giveback from the observed peak bid**, armed only after the
  position has actually reached its arming level. It never looks at a quote that
  had not happened yet;
* **an early "dead leg" exit is charged in full.** Cutting a motionless position
  does not refund the round trip; it only stops the position from drifting into
  its stop, which is exactly the effect being measured.

``resolve`` returns a Phase 25 ``Outcomes`` object so Phase 24's statistics
module scores these rows unchanged — one implementation of the metrics for three
studies. Where a variant has no single fixed target, ``t1_before_sl`` carries its
**primary profit exit** reached before the stop, and the report names the column
accordingly rather than implying a 1.5R target that was not used.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.analysis import option_costs
from app.config import settings
from app.market.instruments import get_spec
from app.research.phase25 import outcomes as p25outcomes
from app.research.phase26 import paths

# Outcome labels. TIMEOUT matches Phase 25's spelling so the shared statistics
# module counts timeouts for both studies without a translation table.
TARGET = "TARGET_BEFORE_SL"
SL_FIRST = p25outcomes.SL_FIRST
TIMEOUT = p25outcomes.TIMEOUT
TRAIL = "TRAIL_GIVEBACK"
DEAD_LEG = "DEAD_LEG_CUT"
UNRESOLVED = p25outcomes.UNRESOLVED

# Excursion milestones kept for comparability with Phase 25's T2/T3 columns.
T2_R = p25outcomes.T2_R
T3_R = p25outcomes.T3_R


@dataclass(frozen=True)
class Variant:
    """One exit rule. Every field is frozen before any result is looked at."""

    key: str
    label: str
    why: str
    stop_pct: float = p25outcomes.STOP_PCT
    target_r: float | None = p25outcomes.T1_R
    # Scale-out: take ``partial_frac`` off at ``partial_r``, then optionally move
    # the stop on the remainder to the entry price.
    partial_r: float | None = None
    partial_frac: float = 0.5
    breakeven_after_partial: bool = True
    # Giveback trail: once the observed peak reaches ``arm_r``, exit the open
    # size when the bid gives back ``trail_giveback_r`` from that peak.
    trail_giveback_r: float | None = None
    arm_r: float = 0.5
    # Dead-leg cut: exit at ``dead_leg_sec`` when the position never reached
    # ``dead_leg_min_mfe_r``.
    dead_leg_sec: int | None = None
    dead_leg_min_mfe_r: float = 0.25
    horizon_sec: int = p25outcomes.HORIZON_SEC
    max_steps: int = p25outcomes.MAX_STEPS
    tags: tuple[str, ...] = field(default_factory=tuple)


# The variant set, fixed here before any of them is measured. V0 reproduces
# Phase 25's geometry exactly and is the baseline every other row is read
# against; a variant that cannot beat V0 is reported as failing, not dropped.
VARIANTS: tuple[Variant, ...] = (
    Variant(
        key="V0_FIXED_1R5_1H",
        label="Phase 25 baseline: fixed 1.5R target, 1-hour hold",
        why="the frozen geometry every other variant has to beat",
        tags=("baseline",),
    ),
    Variant(
        key="V1_TARGET_0R75_1H",
        label="lower target: 0.75R, 1-hour hold",
        target_r=0.75,
        why=(
            "a nearer target is reached far more often; it also needs a higher "
            "hit rate to break even, so this row settles which effect wins on "
            "the measured books instead of assuming one"
        ),
    ),
    Variant(
        key="V2_SCALE_HALF_0R5_BE_1H",
        label="scale half out at +0.5R, remainder to break-even, 1.5R target",
        target_r=p25outcomes.T1_R,
        partial_r=0.5,
        partial_frac=0.5,
        breakeven_after_partial=True,
        why=(
            "attacks the flat-timeout majority directly: half the position is "
            "paid for by a move the books actually produce, and the remainder "
            "can no longer give back the full risk"
        ),
        tags=("scale_out",),
    ),
    Variant(
        key="V3_TRAIL_GIVEBACK_0R5_1H",
        label="no fixed target: arm at +0.5R, exit on a 0.5R giveback",
        target_r=None,
        trail_giveback_r=0.5,
        arm_r=0.5,
        why=(
            "removes the target entirely, so a winner is capped by what the "
            "premium gives back rather than by a level chosen in advance"
        ),
        tags=("trail",),
    ),
    Variant(
        key="V4_FIXED_1R5_3H",
        label="fixed 1.5R target, 3-hour hold",
        horizon_sec=paths.MAX_HORIZON_SEC,
        max_steps=paths.MAX_STEPS,
        why=(
            "tests whether the target was unreachable or merely too slow: same "
            "geometry, three times the time to get there"
        ),
        tags=("long_hold",),
    ),
    Variant(
        key="V5_DEAD_LEG_CUT_15M",
        label="fixed 1.5R target, cut at 15 minutes if it never reached +0.25R",
        dead_leg_sec=900,
        dead_leg_min_mfe_r=0.25,
        why=(
            "a leg that has not moved in 15 minutes is the population that "
            "later times out or stops; cutting it cannot save the round trip "
            "but can stop the drift into the stop"
        ),
        tags=("time_stop",),
    ),
    Variant(
        key="V6_SCALE_HALF_THEN_TRAIL_3H",
        label="scale half at +0.5R, trail the remainder, 3-hour hold",
        target_r=None,
        partial_r=0.5,
        partial_frac=0.5,
        breakeven_after_partial=True,
        trail_giveback_r=0.5,
        arm_r=0.5,
        horizon_sec=paths.MAX_HORIZON_SEC,
        max_steps=paths.MAX_STEPS,
        why=(
            "the combination the two best single changes imply; carried so the "
            "combination is measured rather than assumed to add up"
        ),
        tags=("scale_out", "trail", "long_hold"),
    ),
)

VARIANTS_BY_KEY = {v.key: v for v in VARIANTS}
BASELINE_KEY = VARIANTS[0].key


def slippage_pct() -> float:
    """Configured per-side slippage, as a fraction of premium."""
    return max(0.0, float(settings.ai_slippage_pct)) / 100.0


def _cost_points(
    entry: np.ndarray,
    exit_a: np.ndarray,
    qty_a: np.ndarray,
    exit_b: np.ndarray,
    qty_b: np.ndarray,
    qty: int,
) -> np.ndarray:
    """Brokerage + statutory charges per trade, in premium points.

    ``exit_a``/``qty_a`` is the scale-out leg when there was one and is zero
    otherwise; ``exit_b``/``qty_b`` is the final exit. Composed from the shared
    cost model so this study cannot drift away from the one the live path uses.
    Two calls charge two entries, so the second call's entry brokerage *and* its
    entry-side statutory charge are removed: the entry happened once, for the
    full size, and a scale-out is one entry and two exits rather than two round
    trips. The spread term of the cost model is deliberately unused — the spread
    is already paid by buying at the ask and selling at the bid.
    """
    out = np.zeros(entry.size, dtype=np.float64)
    per_order = max(0.0, float(settings.brokerage_per_lot))
    q_total = max(1, int(qty))
    for i in range(entry.size):
        e = float(entry[i])
        if not np.isfinite(e) or e <= 0:
            continue
        qb = int(round(float(qty_b[i])))
        xb = float(exit_b[i]) if np.isfinite(exit_b[i]) else e
        qa = int(round(float(qty_a[i])))
        # The final exit's call also carries the one entry, at the full size.
        full = option_costs.charges(e, xb, max(1, qb + qa))
        total = full.total - full.exit_statutory
        total += option_costs.charges(e, xb, max(1, qb)).exit_statutory
        if qa > 0:
            xa = float(exit_a[i]) if np.isfinite(exit_a[i]) else e
            partial = option_costs.charges(e, xa, qa)
            total += partial.exit_statutory + per_order
        out[i] = total / q_total
    return out


def resolve(
    c: paths.Candidates,
    variant: Variant,
    *,
    lots: int = 1,
    slip_pct: float | None = None,
    cost_multiplier: float = 1.0,
    exit_delay_steps: int = 0,
) -> p25outcomes.Outcomes:
    """Resolve every candidate under one exit variant.

    ``slip_pct``, ``cost_multiplier`` and ``exit_delay_steps`` exist for the
    robustness grid. The defaults are the honest baseline, not the best case.
    """
    slip = slippage_pct() if slip_pct is None else max(0.0, float(slip_pct))
    qty = int(get_spec(c.instrument).lot_size or 1) * max(1, int(lots))
    n = len(c)

    entry = c.entry_ask * (1.0 + slip)
    risk = np.maximum(entry * float(variant.stop_pct), 1e-9)
    base_stop = entry - risk
    target = (
        entry + risk * float(variant.target_r)
        if variant.target_r is not None else np.full(n, np.inf)
    )
    partial_level = (
        entry + risk * float(variant.partial_r)
        if variant.partial_r is not None else np.full(n, np.inf)
    )

    steps = min(int(variant.max_steps), c.path_valid.shape[1])
    obs = np.where(
        c.path_valid[:, :steps], c.path_bid[:, :steps] * (1.0 - slip), np.nan
    )
    held = c.path_ts[:, :steps] - c.entry_ts[:, None]
    valid = (
        c.path_valid[:, :steps]
        & np.isfinite(obs)
        & (held <= int(variant.horizon_sec))
        & (held >= 0)
    )

    delay = max(0, int(exit_delay_steps))
    frac_open = np.ones(n, dtype=np.float64)
    realized = np.zeros(n, dtype=np.float64)      # premium points, size-weighted
    peak = np.full(n, -np.inf, dtype=np.float64)  # highest observed bid so far
    mfe = np.zeros(n, dtype=np.float64)
    mae = np.zeros(n, dtype=np.float64)
    partial_done = np.zeros(n, dtype=bool)
    partial_px = np.full(n, np.nan, dtype=np.float64)
    partial_step = np.full(n, -1, dtype=np.int32)
    exit_step = np.full(n, -1, dtype=np.int32)
    exit_px = np.full(n, np.nan, dtype=np.float64)
    target_step = np.full(n, -1, dtype=np.int32)
    sl_step = np.full(n, -1, dtype=np.int32)
    label = np.full(n, UNRESOLVED, dtype=object)
    profit_exit_first = np.zeros(n, dtype=bool)
    last_step = np.full(n, -1, dtype=np.int32)
    last_px = np.full(n, np.nan, dtype=np.float64)
    pending = np.full(n, -1, dtype=np.int32)      # steps still to wait on a delay

    trail_on = variant.trail_giveback_r is not None
    give = float(variant.trail_giveback_r or 0.0) * risk
    arm = entry + risk * float(variant.arm_r)
    dead_on = variant.dead_leg_sec is not None
    dead_after = int(variant.dead_leg_sec or 0)
    dead_floor = entry + risk * float(variant.dead_leg_min_mfe_r)

    for s in range(steps):
        v = valid[:, s]
        if not v.any():
            continue
        px = obs[:, s]
        open_now = v & (frac_open > 0)

        # Excursions and the trail's reference peak are updated from observed
        # quotes only, before any exit decision on the same quote.
        peak = np.where(open_now, np.maximum(peak, px), peak)
        mfe = np.where(open_now, np.maximum(mfe, px - entry), mfe)
        mae = np.where(open_now, np.maximum(mae, entry - px), mae)
        last_step = np.where(open_now, s, last_step)
        last_px = np.where(open_now, px, last_px)

        # A delayed exit leaves on the first observed quote after the decision,
        # at whatever that quote actually was.
        due = open_now & (pending == 0)
        if due.any():
            realized = np.where(due, realized + frac_open * (px - entry), realized)
            exit_step = np.where(due, s, exit_step)
            exit_px = np.where(due, px, exit_px)
            frac_open = np.where(due, 0.0, frac_open)
        pending = np.where(open_now & (pending > 0), pending - 1, pending)

        live = v & (frac_open > 0) & (pending < 0)
        stop_lvl = np.where(
            partial_done & variant.breakeven_after_partial, entry, base_stop
        )
        hit_stop = live & (px <= stop_lvl)
        hit_trail = (
            live & ~hit_stop & (peak >= arm) & (px <= peak - give)
            if trail_on else np.zeros(n, dtype=bool)
        )
        hit_target = live & ~hit_stop & ~hit_trail & (px >= target)
        hit_dead = (
            live & ~hit_stop & ~hit_trail & ~hit_target
            & (held[:, s] >= dead_after) & (peak < dead_floor)
            if dead_on else np.zeros(n, dtype=bool)
        )
        closing = hit_stop | hit_trail | hit_target | hit_dead

        if closing.any():
            # A winner is filled at its level because a quote was there to sell
            # into; every other exit is filled at the observed quote.
            px_out = np.where(hit_target, target, px)
            sl_step = np.where(hit_stop & (sl_step < 0), s, sl_step)
            target_step = np.where(hit_target & (target_step < 0), s, target_step)
            label = np.where(
                closing & (exit_step < 0) & (pending < 0),
                np.where(
                    hit_stop, SL_FIRST,
                    np.where(hit_target, TARGET,
                             np.where(hit_trail, TRAIL, DEAD_LEG)),
                ),
                label,
            )
            profit_exit_first = np.where(
                closing & ~profit_exit_first & (exit_step < 0),
                hit_target | (hit_trail & (px > entry)) | partial_done,
                profit_exit_first,
            )
            if delay:
                # The decision is taken here and the fill is a later quote:
                # ``delay`` counts observed quotes, so one step of delay fills on
                # the next quote of this contract rather than on this one.
                pending = np.where(closing & (pending < 0), delay - 1, pending)
            else:
                realized = np.where(
                    closing, realized + frac_open * (px_out - entry), realized
                )
                exit_step = np.where(closing, s, exit_step)
                exit_px = np.where(closing, px_out, exit_px)
                frac_open = np.where(closing, 0.0, frac_open)

        if variant.partial_r is not None:
            take = (
                v & (frac_open > 0) & (pending < 0) & ~partial_done & ~closing
                & (px >= partial_level)
            )
            if take.any():
                cut = float(variant.partial_frac)
                realized = np.where(
                    take, realized + cut * (partial_level - entry), realized
                )
                partial_px = np.where(take, partial_level, partial_px)
                partial_step = np.where(take, s, partial_step)
                frac_open = np.where(take, np.maximum(frac_open - cut, 0.0), frac_open)
                partial_done = partial_done | take

    # Anything still open when the path runs out leaves at the last quote that
    # was actually observed. That is a real outcome, not a discarded row.
    stale = (frac_open > 0) & (last_step >= 0)
    realized = np.where(stale, realized + frac_open * (last_px - entry), realized)
    exit_step = np.where(stale, last_step, exit_step)
    exit_px = np.where(stale, last_px, exit_px)
    label = np.where(stale & (label == UNRESOLVED), TIMEOUT, label)
    frac_open = np.where(stale, 0.0, frac_open)

    resolved = (last_step >= 0) & np.isfinite(entry) & (entry > 0)
    qty_a = np.where(partial_done, round(float(variant.partial_frac) * qty), 0.0)
    qty_b = np.maximum(qty - qty_a, 1.0)
    cost = _cost_points(entry, partial_px, qty_a, exit_px, qty_b, qty)
    cost = cost * float(cost_multiplier)
    gross = np.where(resolved, realized, 0.0)
    net = np.where(resolved, gross - cost, 0.0)

    o = p25outcomes.Outcomes()
    o.idx = c.idx
    o.side = c.side
    o.entry = entry
    o.stop = base_stop
    o.risk = risk
    o.t1 = np.where(np.isfinite(target), target, np.nan)
    o.t2 = entry + risk * T2_R
    o.t3 = entry + risk * T3_R
    # The primary profit exit reached before the stop. For a variant with a
    # fixed target that is the target; for a trailing variant it is a trail exit
    # above the entry, or a scale-out that was taken. The report never calls
    # this a 1.5R hit rate for a variant that had no 1.5R target.
    o.t1_before_sl = profit_exit_first & resolved
    o.t2_before_sl = resolved & (mfe >= T2_R * risk) & ~(label == SL_FIRST)
    o.t3_before_sl = resolved & (mfe >= T3_R * risk) & ~(label == SL_FIRST)
    o.sl_hit = resolved & (label == SL_FIRST)
    o.exit_price = exit_px
    o.exit_step = exit_step
    o.bars_to_t1 = target_step
    o.bars_to_sl = sl_step
    o.bars_held = np.where(resolved, np.maximum(exit_step, 0) + 1, 0).astype(np.int32)
    o.mfe_r = np.maximum(mfe, 0.0) / risk
    o.mae_r = np.maximum(mae, 0.0) / risk
    o.gross_points = gross
    o.net_points = net
    o.net_r = np.where(resolved, net / risk, 0.0)
    o.net_rupees = net * float(qty)
    o.cost_points = cost
    o.resolved = resolved
    o.outcome = np.where(resolved, label.astype(str), UNRESOLVED)
    o.hurdle_pct = np.where(
        entry > 0,
        100.0 * (cost + (c.entry_ask - c.entry_bid)) / entry,
        np.nan,
    )
    o.spread_pct = np.where(
        c.entry_ask > 0, 100.0 * (c.entry_ask - c.entry_bid) / c.entry_ask, np.nan
    )
    o.qty = qty
    return o


def hold_seconds(c: paths.Candidates, o: p25outcomes.Outcomes) -> np.ndarray:
    """Clock seconds from the entry fill to the exit quote, or -1 unresolved."""
    steps = c.path_ts.shape[1]
    step = np.clip(o.exit_step, 0, steps - 1)
    exit_ts = c.path_ts[np.arange(len(c)), step]
    return np.where(o.resolved & (exit_ts > 0), exit_ts - c.entry_ts, -1).astype(
        np.int64
    )


def geometry(variant: Variant) -> dict:
    """The variant's rule set, written out for the report and the panel."""
    return {
        "key": variant.key,
        "label": variant.label,
        "why": variant.why,
        "stop_pct_of_premium": round(100.0 * variant.stop_pct, 1),
        "target_r": variant.target_r,
        "partial_at_r": variant.partial_r,
        "partial_fraction": variant.partial_frac if variant.partial_r else None,
        "stop_to_breakeven_after_partial": (
            bool(variant.breakeven_after_partial) if variant.partial_r else None
        ),
        "trail_giveback_r": variant.trail_giveback_r,
        "trail_arms_at_r": variant.arm_r if variant.trail_giveback_r else None,
        "dead_leg_cut_seconds": variant.dead_leg_sec,
        "dead_leg_min_mfe_r": (
            variant.dead_leg_min_mfe_r if variant.dead_leg_sec else None
        ),
        "horizon_seconds": variant.horizon_sec,
        "max_forward_quotes": variant.max_steps,
        "breakeven_hit_rate_pct_before_costs": (
            round(100.0 / (1.0 + float(variant.target_r)), 2)
            if variant.target_r else None
        ),
        "exit_orders": 2 if variant.partial_r else 1,
        "tags": list(variant.tags),
    }
