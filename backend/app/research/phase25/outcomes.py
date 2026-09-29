"""Phase 25 §3 — ask in, bid out, on stored quotes only.

Geometry is frozen here, in code, before any discovery runs: the stop is a
percentage of the entry premium, the targets are 1.5R / 2.5R / 4R, the hold is
bounded and a snapshot showing both the stop and a target resolves as the stop.
The stop *band* is swept (20/30/40/50% of premium) because a band is part of the
searched space and is counted as a hypothesis; the target multiples and the
tie-break are not tuned afterwards.

Five honesty properties, each of which makes the result worse and truer:

* **the fill is the ask of a later snapshot**, never the snapshot the decision
  was taken on. Deciding and filling on the same quote is the most common
  look-ahead in option research;
* **the exit is the bid**, so the spread is paid in the prices. Because of that
  the cost model is charged for brokerage and statutory charges only — adding
  its spread term would charge the spread twice, a bug this project has already
  made once;
* **slippage is charged against the trade on both legs** at the configured
  per-side percentage of premium: the entry pays above the ask, the exit
  receives below the bid;
* **a winner fills at its target, a loser fills at the observed bid.** A gap
  through the stop is charged in full at the quote that was actually there,
  which is worse than the stop level and is what a real exit would have got;
* **nothing is assumed between snapshots.** A move that happened and reversed
  inside a gap in the capture is not counted, in either direction.
"""
from __future__ import annotations

import numpy as np

from app.analysis import option_costs
from app.config import settings
from app.market.instruments import get_spec

# Frozen research geometry.
STOP_PCT = 0.30           # stop at 30% of the entry premium
T1_R = 1.5
T2_R = 2.5
T3_R = 4.0
HORIZON_SEC = 3_600       # a bounded intraday hold; unresolved is a real outcome
MAX_STEPS = 60            # at most this many later quotes of the same contract

# Swept stop bands, as a fraction of the entry premium.
STOP_BANDS = (0.20, 0.30, 0.40, 0.50)

CE, PE = "CE", "PE"

T1_BEFORE_SL = "T1_BEFORE_SL"
SL_FIRST = "SL"
TIMEOUT = "TIMEOUT"
UNRESOLVED = "UNRESOLVED"


class Outcomes:
    """Resolved outcomes for one aligned block of option candidates.

    Attribute names match Phase 24's so ``phase24.metrics.summarise`` can score
    these rows unchanged — one statistics implementation for both studies.
    """

    __slots__ = (
        "idx", "side", "entry", "stop", "risk", "t1", "t2", "t3",
        "t1_before_sl", "t2_before_sl", "t3_before_sl", "sl_hit",
        "exit_price", "exit_step", "bars_to_t1", "bars_to_sl", "bars_held",
        "mfe_r", "mae_r", "gross_points", "net_points", "net_r",
        "net_rupees", "cost_points", "resolved", "outcome",
        "hurdle_pct", "spread_pct", "qty",
    )

    def __len__(self) -> int:
        return int(self.idx.size)


def slippage_pct() -> float:
    """Configured per-side slippage, as a fraction of premium."""
    return max(0.0, float(settings.ai_slippage_pct)) / 100.0


def _first_true(mask: np.ndarray) -> np.ndarray:
    """Column index of the first True per row, or -1 when there is none."""
    any_hit = mask.any(axis=1)
    first = mask.argmax(axis=1)
    return np.where(any_hit, first, -1).astype(np.int32)


def _cost_points(
    instrument: str,
    entry: np.ndarray,
    exit_price: np.ndarray,
    qty: int,
) -> np.ndarray:
    """Brokerage + statutory charges per trade, in premium points.

    Called through the shared model for every trade rather than reconstructed
    from a probe: the sample here is thousands of rows, not hundreds of
    thousands, so the exact model is affordable and no linearity is assumed. The
    spread term of ``option_costs.round_trip`` is deliberately not used — the
    spread is already paid by buying at the ask and selling at the bid.
    """
    out = np.zeros(entry.size, dtype=np.float64)
    q = max(1, int(qty))
    for i in range(entry.size):
        e = float(entry[i])
        x = float(exit_price[i]) if np.isfinite(exit_price[i]) else e
        if not np.isfinite(e) or e <= 0:
            continue
        ch = option_costs.charges(e, x, q)
        out[i] = ch.total / q
    return out


def resolve(
    instrument: str,
    *,
    idx: np.ndarray,
    side: np.ndarray,
    entry_ask: np.ndarray,
    entry_bid: np.ndarray,
    path_bid: np.ndarray,
    path_valid: np.ndarray,
    lots: int = 1,
    stop_pct: float = STOP_PCT,
    t1_r: float = T1_R,
    slip_pct: float | None = None,
    cost_multiplier: float = 1.0,
    exit_delay_steps: int = 0,
) -> Outcomes:
    """Resolve every candidate on its own contract's stored forward quotes.

    ``path_bid`` is ``candidates x MAX_STEPS`` of the same contract's later bids
    and ``path_valid`` marks which of those cells are real quotes inside the
    hold window and the same session. Everything outside is unobservable and is
    never used to resolve anything.

    ``cost_multiplier``, ``stop_pct`` and ``exit_delay_steps`` exist for the
    robustness grid; the defaults are the honest baseline, not the best case.
    """
    slip = slippage_pct() if slip_pct is None else max(0.0, float(slip_pct))
    spec = get_spec(instrument)
    qty = int(spec.lot_size or 1) * max(1, int(lots))

    entry = entry_ask * (1.0 + slip)                     # pay above the offer
    obs = np.where(path_valid, path_bid * (1.0 - slip), np.nan)  # receive below the bid

    risk = np.maximum(entry * float(stop_pct), 1e-9)
    stop = entry - risk
    t1 = entry + risk * float(t1_r)
    t2 = entry + risk * T2_R
    t3 = entry + risk * T3_R

    valid = path_valid & np.isfinite(obs)
    at_or_below = valid & (obs <= stop[:, None])
    sl_step = _first_true(at_or_below)
    t1_step = _first_true(valid & (obs >= t1[:, None]))
    t2_step = _first_true(valid & (obs >= t2[:, None]))
    t3_step = _first_true(valid & (obs >= t3[:, None]))

    def before(step: np.ndarray) -> np.ndarray:
        """Target reached strictly before the stop; a same-quote tie is a loss."""
        hit = step >= 0
        no_sl = sl_step < 0
        return hit & (no_sl | (step < sl_step))

    t1_ok = before(t1_step)
    t2_ok = before(t2_step)
    t3_ok = before(t3_step)
    sl_first = (sl_step >= 0) & ~t1_ok

    steps = valid.shape[1]
    last_valid = np.where(valid.any(axis=1), steps - 1 - valid[:, ::-1].argmax(axis=1), -1)
    delay = max(0, int(exit_delay_steps))
    exit_step = np.where(t1_ok, t1_step, np.where(sl_first, sl_step, last_valid))
    exit_step = np.where(exit_step >= 0, np.minimum(exit_step + delay, last_valid), -1)

    rows = np.arange(idx.size)
    safe_step = np.maximum(exit_step, 0)
    observed = np.where(exit_step >= 0, obs[rows, safe_step], np.nan)

    # A winner is filled at its target, because the quote was there to sell into.
    # A loser is filled at the quote that actually existed, which on a gap is
    # below the stop; the shortfall is charged rather than assumed away.
    exit_price = np.where(t1_ok & (delay == 0), t1, observed)

    # Excursions are measured only over quotes that were observed up to the
    # exit; a row with no observed quote has no excursion rather than a zero
    # that would read as a flat trade.
    after_exit = (
        np.arange(steps)[None, :]
        > np.where(exit_step >= 0, exit_step, steps)[:, None]
    )
    seen = valid & ~after_exit
    mfe_pts = np.where(seen, obs - entry[:, None], 0.0).max(axis=1)
    mae_pts = np.where(seen, entry[:, None] - obs, 0.0).max(axis=1)
    mfe_pts = np.maximum(mfe_pts, 0.0)
    mae_pts = np.maximum(mae_pts, 0.0)

    resolved = (exit_step >= 0) & np.isfinite(exit_price) & np.isfinite(entry) & (entry > 0)
    gross = np.where(resolved, exit_price - entry, 0.0)
    cost = _cost_points(instrument, entry, np.where(resolved, exit_price, entry), qty)
    cost = cost * float(cost_multiplier)
    net = np.where(resolved, gross - cost, 0.0)

    o = Outcomes()
    o.idx = idx
    o.side = side
    o.entry = entry
    o.stop = stop
    o.risk = risk
    o.t1, o.t2, o.t3 = t1, t2, t3
    o.t1_before_sl = t1_ok & resolved
    o.t2_before_sl = t2_ok & resolved
    o.t3_before_sl = t3_ok & resolved
    o.sl_hit = sl_first & resolved
    o.exit_price = exit_price
    # Which observed quote the trade left on, so the caller can recover the exit
    # timestamp and report holding time in clock time rather than in quotes.
    o.exit_step = exit_step.astype(np.int32)
    o.bars_to_t1 = np.where(t1_ok, t1_step, -1).astype(np.int32)
    o.bars_to_sl = np.where(sl_first, sl_step, -1).astype(np.int32)
    o.bars_held = np.where(resolved, np.maximum(exit_step, 0) + 1, 0).astype(np.int32)
    o.mfe_r = mfe_pts / risk
    o.mae_r = mae_pts / risk
    o.gross_points = gross
    o.net_points = net
    o.net_r = np.where(resolved, net / risk, 0.0)
    o.net_rupees = net * float(qty)
    o.cost_points = cost
    o.resolved = resolved
    o.outcome = np.where(
        ~resolved, UNRESOLVED,
        np.where(o.t1_before_sl, T1_BEFORE_SL, np.where(o.sl_hit, SL_FIRST, TIMEOUT)),
    )
    # Recorded per row so the report can show the economics the trade faced
    # without recomputing them from a different model.
    o.hurdle_pct = np.where(entry > 0, 100.0 * (cost + (entry_ask - entry_bid)) / entry, np.nan)
    o.spread_pct = np.where(entry_ask > 0, 100.0 * (entry_ask - entry_bid) / entry_ask, np.nan)
    o.qty = qty
    return o
