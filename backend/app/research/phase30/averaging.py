"""Phase 30 §9 — controlled averaging, research only, one extra entry maximum.

Production averaging stays OFF; nothing here can be reached from the order path.

The arms are frozen before any outcome is computed:

* ``BASELINE`` — one entry, stop at the band's ATR distance (1R), target 1.5R;
* ``AVG_0.5R`` / ``AVG_1.0R`` / ``AVG_1.5R`` — one additional entry of the same
  size when price trades ``k`` × R against the first entry, and **the stop for
  the whole position is placed at (k + 0.5)R from the first entry**.

That last clause is the honest part. With the stop at 1R, an add at 1.0R or 1.5R
adverse sits at or beyond the stop and can never fill — the arm would silently
collapse into the baseline and be reported as "no difference". So each arm widens
its stop by a pre-declared amount to make its own add reachable, which is what
averaging actually costs: the arms do **not** risk the same money, and every
table reports initial risk, second-entry risk and total risk in R so the extra
risk is visible next to the extra return. Net R is normalised by the *baseline*
1R so the arms are comparable, and net R per unit of total risk is reported next
to it because that is the number that decides whether averaging helped.

Both units are charged their own round-trip brokerage, statutory charges and
slippage. A same-bar tie between the target and the stop is resolved as the stop:
a 1-minute bar cannot say which came first and the optimistic reading is what
turns a losing rule into a winning backtest.
"""
from __future__ import annotations

import numpy as np

from app.market.instruments import get_spec
from app.research.phase24 import outcomes as p24out
from app.research.phase30.pool import Pool

BASELINE = "BASELINE"
ARMS: dict[str, float | None] = {
    BASELINE: None,
    "AVG_0.5R": 0.5,
    "AVG_1.0R": 1.0,
    "AVG_1.5R": 1.5,
}
STOP_WIDENING = 0.5   # arm stop = (k + STOP_WIDENING) x R from the first entry
MAX_EXTRA_ENTRIES = 1


class ArmResult:
    """Per-candidate arrays for one averaging arm, aligned to the pool's rows."""

    __slots__ = (
        "arm", "added", "t1_before_sl", "sl_hit", "net_points", "net_r",
        "net_rupees", "units", "avg_entry", "final_stop", "t1_price",
        "initial_risk_r", "second_risk_r", "total_risk_r", "max_capital",
        "resolved", "worst_loss_r",
    )


def _first_hit(
    high: np.ndarray,
    low: np.ndarray,
    fill_i: np.ndarray,
    side: np.ndarray,
    level: np.ndarray,
    allowed: np.ndarray,
    horizon: int,
    *,
    favourable: bool,
) -> np.ndarray:
    return p24out._first_hit(
        high, low, fill_i, side, level, allowed, horizon, favourable=favourable
    )


def simulate(p: Pool, arm: str, *, horizon: int = p24out.HORIZON_BARS) -> ArmResult:
    """Resolve one arm on the same candidates and the same recorded paths."""
    if arm not in ARMS:
        raise ValueError("unknown averaging arm")
    k = ARMS[arm]
    s = p.series
    o = p.out
    n = s.high.size
    side = p.side.astype(np.int64)
    entry = o.entry
    risk = o.risk                        # the band's ATR stop distance, in points
    t1 = o.t1                            # absolute target, identical across arms
    fill_i = np.minimum(p.idx + (2 if p.confirmed_arm else 1), n - 1)
    allowed = np.minimum(horizon, np.maximum(0, _session_room(p, fill_i)))

    lot = int(get_spec(p.instrument).lot_size or 1)
    cost_per_unit = o.cost_points        # round trip, one unit, from the shared model

    if k is None:
        add_level = np.full(entry.size, np.nan)
        stop = entry - side * risk
        add_bar = np.full(entry.size, -1, dtype=np.int32)
    else:
        add_level = entry - side * risk * float(k)
        stop = entry - side * risk * (float(k) + STOP_WIDENING)
        add_bar = _first_hit(
            s.high, s.low, fill_i, side, add_level, allowed, horizon, favourable=False
        )

    sl_bar = _first_hit(
        s.high, s.low, fill_i, side, stop, allowed, horizon, favourable=False
    )
    t1_bar = _first_hit(
        s.high, s.low, fill_i, side, t1, allowed, horizon, favourable=True
    )

    t1_ok = (t1_bar >= 0) & ((sl_bar < 0) | (t1_bar < sl_bar))
    sl_first = (sl_bar >= 0) & ~t1_ok
    # The add only counts if it filled before the trade left the book.
    added = (add_bar >= 0)
    if k is not None:
        end_bar = np.where(t1_ok, t1_bar, np.where(sl_first, sl_bar, allowed))
        added &= add_bar <= end_bar
    else:
        added = np.zeros(entry.size, dtype=bool)

    exit_bar = np.where(t1_ok, t1_bar, np.where(sl_first, sl_bar, allowed))
    exit_j = np.minimum(fill_i + np.maximum(exit_bar, 0), n - 1)
    exit_price = np.where(t1_ok, t1, np.where(sl_first, stop, s.open[exit_j]))

    gross = side * (exit_price - entry)
    gross = gross + np.where(added, side * (exit_price - add_level), 0.0)
    units = np.where(added, 2, 1)
    cost = cost_per_unit * units
    net = gross - cost

    r = ArmResult()
    r.arm = arm
    r.added = added
    r.t1_before_sl = t1_ok
    r.sl_hit = sl_first
    r.net_points = net
    r.net_r = net / np.where(risk > 0, risk, np.nan)
    r.net_rupees = net * float(lot)
    r.units = units
    r.avg_entry = np.where(added, (entry + add_level) / 2.0, entry)
    r.final_stop = stop
    r.t1_price = t1
    r.initial_risk_r = np.abs(entry - stop) / np.where(risk > 0, risk, np.nan)
    r.second_risk_r = np.where(
        added, np.abs(add_level - stop) / np.where(risk > 0, risk, np.nan), 0.0
    )
    r.total_risk_r = r.initial_risk_r + r.second_risk_r
    r.max_capital = np.abs(entry) * units * float(lot)
    r.resolved = o.resolved & (allowed > 0)
    r.worst_loss_r = np.minimum(r.net_r, 0.0)
    return r


def _session_room(p: Pool, fill_i: np.ndarray) -> np.ndarray:
    """Bars left in the fill bar's own session — no result crosses a session end."""
    sess = ((p.series.ts + 19_800) // 86_400)
    n = sess.size
    last = np.empty(n, dtype=np.int64)
    start = 0
    for k in range(1, n + 1):
        if k == n or sess[k] != sess[start]:
            last[start:k] = k - 1
            start = k
    return last[fill_i] - fill_i


def summarise(r: ArmResult, mask: np.ndarray) -> dict:
    """Every §9 number for one arm over one cohort, in chronological order."""
    m = mask & r.resolved & np.isfinite(r.net_r)
    n = int(m.sum())
    if n == 0:
        return {"arm": r.arm, "trades": 0, "status": "EMPTY"}
    net_r = r.net_r[m]
    eq = np.cumsum(net_r)
    peak = np.maximum.accumulate(eq)
    total_risk = r.total_risk_r[m]
    return {
        "arm": r.arm,
        "trades": n,
        "second_entry_rate_pct": round(100.0 * float(r.added[m].mean()), 2),
        "t1_before_sl_pct": round(100.0 * float(r.t1_before_sl[m].mean()), 2),
        "avg_initial_risk_r": round(float(r.initial_risk_r[m].mean()), 3),
        "avg_second_entry_risk_r": round(float(r.second_risk_r[m].mean()), 3),
        "avg_total_risk_r": round(float(total_risk.mean()), 3),
        "avg_max_capital": round(float(r.max_capital[m].mean()), 0),
        "avg_units": round(float(r.units[m].mean()), 3),
        "expectancy_r": round(float(net_r.mean()), 4),
        "expectancy_per_total_risk_r": round(
            float((net_r / np.where(total_risk > 0, total_risk, np.nan)).mean()), 4
        ),
        "total_net_r": round(float(net_r.sum()), 2),
        "total_net_rupees": round(float(r.net_rupees[m].sum()), 0),
        "profit_factor": _pf(net_r),
        "max_drawdown_r": round(float(np.max(peak - eq)), 3),
        "worst_loss_r": round(float(net_r.min()), 3),
        "positive_net_pct": round(100.0 * float((net_r > 0).mean()), 2),
    }


def _pf(net_r: np.ndarray) -> float | None:
    wins = float(net_r[net_r > 0].sum())
    losses = float(-net_r[net_r < 0].sum())
    if losses <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / losses, 3)


def compare(p: Pool, mask: np.ndarray) -> dict:
    """All four arms on the same cohort, plus the verdict against the baseline.

    Averaging counts as helping only if it improves expectancy **per unit of
    total risk** and does not deepen the drawdown; a bigger absolute return bought
    with a bigger position is not an edge.
    """
    rows = {arm: summarise(simulate(p, arm), mask) for arm in ARMS}
    base = rows[BASELINE]
    verdict: dict[str, dict] = {}
    for arm, row in rows.items():
        if arm == BASELINE or not row.get("trades") or not base.get("trades"):
            continue
        better_risk_adj = (
            row["expectancy_per_total_risk_r"] > base["expectancy_per_total_risk_r"]
        )
        worse_dd = row["max_drawdown_r"] > base["max_drawdown_r"]
        verdict[arm] = {
            "improves_expectancy_r": row["expectancy_r"] > base["expectancy_r"],
            "improves_risk_adjusted": bool(better_risk_adj),
            "drawdown_deeper": bool(worse_dd),
            "worse_worst_loss": row["worst_loss_r"] < base["worst_loss_r"],
            "helped": bool(better_risk_adj and not worse_dd),
        }
    return {"arms": rows, "vs_baseline": verdict, "max_extra_entries": MAX_EXTRA_ENTRIES}
