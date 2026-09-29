"""Phase 29 §3 — resolving a credit spread on stored quotes only.

Geometry is frozen here, in code, before any discovery runs. A credit spread's
exits are not the buyer's targets, so they are stated in the seller's own terms:

* **profit is a fraction of the credit kept.** T1 is 50% of the credit, T2 75%,
  T3 the whole credit. Those three are fixed and are not tuned afterwards;
* **the stop is a multiple of the credit**, swept over 1.5x / 2.0x / 2.5x / 3.0x,
  so a 2x stop risks one credit to make half of one. The band is swept, so every
  value is counted as a hypothesis;
* **the defined loss is a hard floor.** Whatever the stop says, the structure
  cannot lose more than width minus credit, and the resolver clips to that and
  counts how often a stored quote implied worse — a quote crossing the width is a
  data-quality event, not a trade that lost more than its maximum.

Five honesty properties, each of which makes the result worse and truer:

* **the fill is a later snapshot than the decision.** Deciding and filling on the
  same book is the most common look-ahead in option research;
* **both legs must be quoted at the same timestamp**, on entry and on exit. A
  timestamp where only one leg was quoted is unobservable for this structure;
* **slippage is charged against every leg in both directions**;
* **a winner is filled at its level, a loser at the quote that was there.** A gap
  through the stop is charged in full at the observed cost to close, which is
  worse than the stop level and is what a real exit would have got;
* **an unclosable position is UNRESOLVED, never a full-credit win.** No expiry,
  settlement or assignment outcome is modelled. Assuming the short expires
  worthless is precisely how a losing seller's book prints a 90% win rate.
"""
from __future__ import annotations

import numpy as np

from app.research.phase29 import economics

# Frozen exit geometry, in fractions of the credit collected.
TAKE_1 = 0.50
TAKE_2 = 0.75
TAKE_3 = 1.00

# Swept stop bands, as a multiple of the credit. 2.0 means "risk one credit".
STOP_CREDIT_MULTIPLES = (1.5, 2.0, 2.5, 3.0)
STOP_CREDIT_MULT = 2.0

HORIZON_SEC = 3_600      # a bounded intraday hold; unresolved is a real outcome
MAX_STEPS = 60           # at most this many later paired quotes

# A one-hour hold is not how premium selling is usually run, so a second hold is
# declared here — to the last paired quote of the *same* session. Both are frozen
# before any measurement and both count as hypotheses. Neither carries the
# position overnight: a gap through the short strike cannot be managed, and this
# store cannot price it honestly either.
SESSION_HORIZON_SEC = 8 * 3_600
SESSION_MAX_STEPS = 240

HOLD_1H = "HOLD_1H"
HOLD_SESSION = "HOLD_TO_SESSION_CLOSE"
HOLDS: dict[str, tuple[int, int]] = {
    HOLD_1H: (HORIZON_SEC, MAX_STEPS),
    HOLD_SESSION: (SESSION_HORIZON_SEC, SESSION_MAX_STEPS),
}

T1_BEFORE_SL = "T1_BEFORE_SL"
SL_FIRST = "SL"
TIMEOUT = "TIMEOUT"
UNRESOLVED = "UNRESOLVED"


class Outcomes:
    """Resolved outcomes for one block of credit-spread candidates.

    The attribute names deliberately match Phase 24's so ``metrics.summarise``
    scores these rows unchanged — one statistics implementation for every study,
    with ``t1_before_sl`` meaning "kept half the credit before being stopped"
    rather than "the bought option reached 1.5R".
    """

    __slots__ = (
        "idx", "structure", "credit", "max_loss", "risk", "t1", "t2", "t3",
        "t1_before_sl", "t2_before_sl", "t3_before_sl", "sl_hit",
        "exit_cost", "exit_step", "bars_to_t1", "bars_to_sl", "bars_held",
        "mfe_r", "mae_r", "gross_points", "net_points", "net_r", "net_rupees",
        "cost_points", "spread_points", "hurdle_pct", "ror_defined_risk",
        "resolved", "outcome", "qty", "floor_breaches", "entry",
    )

    def __len__(self) -> int:
        return int(self.idx.size)


def _first_true(mask: np.ndarray) -> np.ndarray:
    """Column index of the first True per row, or -1 when there is none."""
    any_hit = mask.any(axis=1)
    first = mask.argmax(axis=1)
    return np.where(any_hit, first, -1).astype(np.int32)


def resolve(
    instrument: str,
    *,
    idx: np.ndarray,
    structure: str,
    width_points: float,
    short_bid: np.ndarray,
    short_ask: np.ndarray,
    long_bid: np.ndarray,
    long_ask: np.ndarray,
    path_short_ask: np.ndarray,
    path_long_bid: np.ndarray,
    path_valid: np.ndarray,
    lots: int = 1,
    stop_credit_mult: float = STOP_CREDIT_MULT,
    take_1: float = TAKE_1,
    slip_pct: float | None = None,
    cost_multiplier: float = 1.0,
    exit_delay_steps: int = 0,
) -> Outcomes:
    """Resolve every structure on its own legs' paired forward quotes.

    ``short_*`` and ``long_*`` are ``rows x legs`` entry books;
    ``path_short_ask`` and ``path_long_bid`` are ``rows x legs x steps`` of the
    same contracts' later quotes, and ``path_valid`` (``rows x steps``) marks the
    steps where *every* leg was quoted inside the hold window and the same
    session. Everything outside is unobservable and resolves nothing.

    ``cost_multiplier``, ``stop_credit_mult``, ``slip_pct`` and
    ``exit_delay_steps`` exist for the robustness grid; the defaults are the
    honest baseline, not the best case.
    """
    slip = economics.slippage_pct() if slip_pct is None else max(0.0, float(slip_pct))
    qty = economics.lot_qty(instrument, lots)

    credit = economics.entry_credit(short_bid, long_ask, slip=slip)
    cap = economics.max_loss(width_points, credit)
    ok = economics.priceable(credit, cap)

    # The stop can never be wider than the structure's defined loss: the spread
    # simply cannot get there.
    risk = np.minimum(np.maximum(stop_credit_mult - 1.0, 0.0) * credit, cap)
    risk = np.maximum(risk, 1e-9)

    t1 = take_1 * credit
    t2 = TAKE_2 * credit
    t3 = TAKE_3 * credit

    # Cost to buy the structure back at each observed step, adverse on every leg.
    cost_path = (
        (path_short_ask * (1.0 + slip)).sum(axis=1)
        - (path_long_bid * (1.0 - slip)).sum(axis=1)
    )
    gross = np.where(path_valid, credit[:, None] - cost_path, np.nan)

    # A stored quote implying a loss deeper than the defined loss is a bad book,
    # not a trade that lost more than its maximum. Clipped, and counted.
    floor = -cap[:, None]
    breaches = int(np.nansum((gross < floor - 1e-9) & path_valid))
    gross = np.where(path_valid, np.maximum(gross, floor), np.nan)

    valid = path_valid & np.isfinite(gross)
    sl_step = _first_true(valid & (gross <= -risk[:, None]))
    t1_step = _first_true(valid & (gross >= t1[:, None]))
    t2_step = _first_true(valid & (gross >= t2[:, None]))
    t3_step = _first_true(valid & (gross >= t3[:, None]))

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
    last_valid = np.where(
        valid.any(axis=1), steps - 1 - valid[:, ::-1].argmax(axis=1), -1
    )
    delay = max(0, int(exit_delay_steps))
    exit_step = np.where(t1_ok, t1_step, np.where(sl_first, sl_step, last_valid))
    exit_step = np.where(exit_step >= 0, np.minimum(exit_step + delay, last_valid), -1)

    rows = np.arange(idx.size)
    safe = np.maximum(exit_step, 0)
    observed = np.where(exit_step >= 0, gross[rows, safe], np.nan)

    # A winner books its level, because the paired quote was there to close into.
    # A loser books the quote that actually existed, which on a gap is beyond the
    # stop; the shortfall is charged rather than assumed away.
    gross_exit = np.where(t1_ok & (delay == 0), t1, observed)

    after = (
        np.arange(steps)[None, :]
        > np.where(exit_step >= 0, exit_step, steps)[:, None]
    )
    seen = valid & ~after
    mfe = np.maximum(np.where(seen, gross, -np.inf).max(axis=1), 0.0)
    mae = np.maximum(np.where(seen, -gross, -np.inf).max(axis=1), 0.0)
    mfe = np.where(np.isfinite(mfe), mfe, 0.0)
    mae = np.where(np.isfinite(mae), mae, 0.0)

    # The prices the charges are computed on: what each leg was opened at and
    # what it was closed at, per leg, at the resolved step.
    short_close = path_short_ask[rows, :, safe] if steps else short_ask
    long_close = path_long_bid[rows, :, safe] if steps else long_bid
    charges = economics.charge_points(
        short_bid, short_close, long_ask, long_close, qty=qty
    ) * float(cost_multiplier)

    resolved = ok & (exit_step >= 0) & np.isfinite(gross_exit)
    net = np.where(resolved, gross_exit - charges, 0.0)

    o = Outcomes()
    o.idx = idx
    o.structure = structure
    o.credit = credit
    # ``entry`` is the credit, kept under Phase 24's attribute name so the shared
    # reporting code can read a "position size" without a special case.
    o.entry = credit
    o.max_loss = cap
    o.risk = risk
    o.t1, o.t2, o.t3 = t1, t2, t3
    o.t1_before_sl = t1_ok & resolved
    o.t2_before_sl = t2_ok & resolved
    o.t3_before_sl = t3_ok & resolved
    o.sl_hit = sl_first & resolved
    o.exit_cost = np.where(resolved, credit - gross_exit, np.nan)
    o.exit_step = exit_step.astype(np.int32)
    o.bars_to_t1 = np.where(t1_ok, t1_step, -1).astype(np.int32)
    o.bars_to_sl = np.where(sl_first, sl_step, -1).astype(np.int32)
    o.bars_held = np.where(resolved, np.maximum(exit_step, 0) + 1, 0).astype(np.int32)
    o.mfe_r = mfe / risk
    o.mae_r = mae / risk
    o.gross_points = np.where(resolved, gross_exit, 0.0)
    o.net_points = net
    o.net_r = np.where(resolved, net / risk, 0.0)
    o.ror_defined_risk = np.where(resolved & (cap > 0), net / np.maximum(cap, 1e-9), 0.0)
    o.net_rupees = net * float(qty)
    o.cost_points = charges
    o.spread_points = economics.spread_cost_points(
        short_bid, short_ask, long_bid, long_ask
    )
    o.hurdle_pct = economics.hurdle_pct_of_credit(charges, o.spread_points, credit)
    o.resolved = resolved
    o.outcome = np.where(
        ~resolved, UNRESOLVED,
        np.where(o.t1_before_sl, T1_BEFORE_SL, np.where(o.sl_hit, SL_FIRST, TIMEOUT)),
    )
    o.qty = qty
    o.floor_breaches = breaches
    return o
