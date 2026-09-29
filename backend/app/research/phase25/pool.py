"""Phase 25 §2 — the candidate pool over captured snapshots.

Every eligible snapshot produces **both** a CE candidate and a PE candidate, so
discovery has to earn the side from the features instead of inheriting it from
how the pool was built. The pool's own base rate is therefore close to what
buying an ATM option at random inside the captured window would have paid, and
any cohort found later is measured against that rather than against zero.

Nothing about quality enters eligibility. A snapshot qualifies when the market
context has formed, a usable leg of that side existed, and the same contract was
quoted again later inside its own session so the trade could actually be
resolved. Refused-looking rows — wide spread, tiny premium, brutal hurdle — stay
in the pool, because the economics are exactly what the study is measuring.
"""
from __future__ import annotations

import numpy as np

from app.analysis import option_costs
from app.market.instruments import get_spec
from app.research.phase24 import features as p24features
from app.research.phase24 import pool as p24pool
from app.research.phase25 import books, outcomes, select, underlying

IST_OFFSET = 19_800

# One candidate every STRIDE snapshots per side. Consecutive snapshots are ~1-2
# minutes apart and would count the same setup many times over, which turns
# every sample size into a fiction.
STRIDE = 2

# Market context must have formed before a snapshot can be a decision point.
WARMUP_BARS = p24features.SLOW_VOL_WIN + p24features.PULLBACK_LOOKBACK

OPTION_FEATURE_KEYS = (
    "spread_pct", "hurdle_pct", "premium_ask", "moneyness_pct", "abs_delta",
    "iv", "leg_oi", "leg_volume", "quotes_ahead",
)


class Pool:
    """Candidates for one instrument: features, side, vehicle and outcome."""

    __slots__ = ("instrument", "feat", "idx", "side", "option_type", "out", "ts",
                 "session", "sessions", "symbol", "strike", "entry_ts",
                 "entry_ask", "entry_bid", "spot", "exit_ts", "hold_sec",
                 "coverage")

    def __len__(self) -> int:
        return int(self.idx.size)


def _sessions(ts: np.ndarray) -> np.ndarray:
    return (ts + IST_OFFSET) // 86_400


def _decision_hurdle(instrument: str, bid: float, ask: float, qty: int) -> float:
    """Break-even hurdle of the decision-time book, as a % of the ask.

    Computed from the *measured* spread through the shared cost model. The
    family-median spread is never used in this study: a leg with no book is not
    a candidate at all.
    """
    cost = option_costs.round_trip(
        instrument, ask, None, qty, 1, quoted_spread=max(0.0, ask - bid),
    )
    if cost is None or cost.spread_source != option_costs.MEASURED or ask <= 0:
        return float("nan")
    return 100.0 * float(cost.cost_points) / float(ask)


def build(
    instrument: str,
    *,
    chain: books.Chain | None = None,
    series=None,
    stride: int = STRIDE,
    **resolve_kw,
) -> Pool | None:
    """Build and resolve the candidate pool for one instrument.

    ``chain`` and ``series`` are injectable so the robustness grid and the smoke
    test can re-resolve the *same* captured data under worse assumptions instead
    of loading a different pool that would not be comparable.
    """
    inst = (instrument or "").upper()
    chain = books.load_chain(inst) if chain is None else chain
    if chain.snapshots == 0:
        return None
    series = underlying.load_series(inst) if series is None else series
    if series is None or len(series) <= WARMUP_BARS:
        return None

    cidx = underlying.align(series, chain.ts)
    feat = p24features.build(series)
    snap_sessions = _sessions(chain.ts)

    lot = int(get_spec(inst).lot_size or 1)
    steps = outcomes.MAX_STEPS

    rows: list[dict] = []
    considered = 0
    no_leg = 0
    no_forward_quote = 0

    for i in range(0, chain.snapshots, max(1, int(stride))):
        c = int(cidx[i])
        if c < WARMUP_BARS:
            continue
        atr = float(feat["atr"][c])
        if not np.isfinite(atr) or atr <= 0:
            continue
        spot = float(series.close[c])
        book = chain.legs[i]
        for otype in (select.CE, select.PE):
            considered += 1
            leg = select.pick(book, spot, otype)
            if leg is None:
                no_leg += 1
                continue
            q = chain.quotes[str(leg["symbol"])]
            k = int(np.searchsorted(q.pos, i, side="right"))
            if k >= len(q):
                no_forward_quote += 1
                continue
            entry_ts = int(q.ts[k])
            if _sessions(np.asarray([entry_ts]))[0] != snap_sessions[i]:
                no_forward_quote += 1
                continue
            rows.append({
                "snapshot": i,
                "candle": c,
                "option_type": otype,
                "symbol": str(leg["symbol"]),
                "strike": float(leg["strike"]),
                "spot": spot,
                "decision_bid": float(leg["bid"]),
                "decision_ask": float(leg["ask"]),
                "iv": float(leg.get("iv") or float("nan")),
                "delta": float(leg.get("delta") or float("nan")),
                "oi": float(leg.get("oi") or 0.0),
                "volume": float(leg.get("volume") or 0.0),
                "entry_pos": k,
                "entry_ts": entry_ts,
            })

    if not rows:
        return None

    n = len(rows)
    idx = np.arange(n, dtype=np.int64)
    side = np.asarray(
        [1 if r["option_type"] == select.CE else -1 for r in rows], dtype=np.int8
    )
    snap = np.asarray([r["snapshot"] for r in rows], dtype=np.int64)
    cand = np.asarray([r["candle"] for r in rows], dtype=np.int64)
    entry_ask = np.asarray([0.0] * n, dtype=np.float64)
    entry_bid = np.asarray([0.0] * n, dtype=np.float64)
    entry_ts = np.asarray([r["entry_ts"] for r in rows], dtype=np.int64)
    path_bid = np.full((n, steps), np.nan, dtype=np.float64)
    path_ts = np.zeros((n, steps), dtype=np.int64)
    path_valid = np.zeros((n, steps), dtype=bool)
    quotes_ahead = np.zeros(n, dtype=np.float64)

    for j, r in enumerate(rows):
        q = chain.quotes[r["symbol"]]
        k = r["entry_pos"]
        entry_ask[j] = float(q.ask[k])
        entry_bid[j] = float(q.bid[k])
        lo, hi = k + 1, min(len(q), k + 1 + steps)
        if hi <= lo:
            continue
        pts = q.ts[lo:hi]
        keep = (pts <= r["entry_ts"] + outcomes.HORIZON_SEC) & (
            _sessions(pts) == _sessions(np.asarray([r["entry_ts"]]))[0]
        )
        m = int(keep.sum())
        if m == 0:
            continue
        path_bid[j, :m] = q.bid[lo:hi][keep]
        path_ts[j, :m] = pts[keep]
        path_valid[j, :m] = True
        quotes_ahead[j] = float(m)

    out = outcomes.resolve(
        inst,
        idx=idx,
        side=side,
        entry_ask=entry_ask,
        entry_bid=entry_bid,
        path_bid=path_bid,
        path_valid=path_valid,
        **resolve_kw,
    )

    p = Pool()
    p.instrument = inst
    p.idx = idx
    p.side = side
    p.option_type = np.asarray([r["option_type"] for r in rows])
    p.symbol = np.asarray([r["symbol"] for r in rows])
    p.strike = np.asarray([r["strike"] for r in rows], dtype=np.float64)
    p.spot = np.asarray([r["spot"] for r in rows], dtype=np.float64)
    p.entry_ts = entry_ts
    p.entry_ask = entry_ask
    p.entry_bid = entry_bid
    p.out = out
    # Clock time of the quote the trade left on, so holding time is reported in
    # seconds actually observed rather than in a quote count.
    p.exit_ts = np.where(
        out.resolved, path_ts[np.arange(n), np.maximum(out.exit_step, 0)], 0
    ).astype(np.int64)
    p.hold_sec = np.where(out.resolved, p.exit_ts - entry_ts, -1).astype(np.int64)
    p.ts = chain.ts[snap]
    p.session = snap_sessions[snap]
    p.sessions = int(np.unique(p.session).size)

    feat_rows = {k: np.asarray(feat[k])[cand] for k in p24pool.FEATURE_KEYS}
    spread_pct = np.where(
        entry_ask > 0, 100.0 * (entry_ask - entry_bid) / np.maximum(entry_ask, 1e-9),
        np.nan,
    )
    hurdle = np.asarray([
        _decision_hurdle(inst, r["decision_bid"], r["decision_ask"], lot)
        for r in rows
    ], dtype=np.float64)
    feat_rows.update({
        "spread_pct": spread_pct,
        "hurdle_pct": hurdle,
        "premium_ask": entry_ask,
        "moneyness_pct": np.asarray([
            100.0 * (r["strike"] - r["spot"]) / max(1e-9, r["spot"]) for r in rows
        ], dtype=np.float64),
        "abs_delta": np.abs(np.asarray([r["delta"] for r in rows], dtype=np.float64)),
        "iv": np.asarray([r["iv"] for r in rows], dtype=np.float64),
        "leg_oi": np.asarray([r["oi"] for r in rows], dtype=np.float64),
        "leg_volume": np.asarray([r["volume"] for r in rows], dtype=np.float64),
        "quotes_ahead": quotes_ahead,
    })
    p.feat = feat_rows
    p.coverage = {
        "snapshots": chain.snapshots,
        "sessions": chain.sessions,
        "snapshot_sides_considered": considered,
        "no_usable_leg": no_leg,
        "no_forward_quote_same_session": no_forward_quote,
        "candidates": n,
        "resolved": int(out.resolved.sum()),
        "avg_hold_sec": (
            round(float(p.hold_sec[out.resolved].mean()), 1)
            if out.resolved.any() else None
        ),
        "median_hold_sec": (
            round(float(np.median(p.hold_sec[out.resolved])), 1)
            if out.resolved.any() else None
        ),
        "simulator_snapshots_excluded": chain.simulator_snapshots,
        "one_sided_legs_excluded": chain.one_sided_legs,
        "lot_size_used": lot,
        "lot_size_source": (
            "instrument registry seed — brokerage per premium point scales with "
            "it; verify against the broker's scrip master before reading rupee "
            "figures as exact"
        ),
    }
    return p


def summary(p: Pool) -> dict:
    """Pool-level counts, including the base rate any cohort must beat."""
    o = p.out
    res = o.resolved
    total = int(res.sum())
    base = {
        "instrument": p.instrument,
        "candidates": int(len(p)),
        "resolved": total,
        "unresolved": int((~res).sum()),
        "sessions": p.sessions,
        "ce": int((p.option_type == select.CE).sum()),
        "pe": int((p.option_type == select.PE).sum()),
        "first_ts": int(p.ts.min()) if len(p) else 0,
        "last_ts": int(p.ts.max()) if len(p) else 0,
    }
    if total == 0:
        return base
    base.update({
        "base_t1_before_sl_pct": round(100.0 * float(o.t1_before_sl[res].mean()), 2),
        "base_avg_net_r": round(float(o.net_r[res].mean()), 4),
        "base_avg_net_rupees": round(float(o.net_rupees[res].mean()), 2),
        "base_total_net_rupees": round(float(o.net_rupees[res].sum()), 2),
        "base_timeout_pct": round(
            100.0 * float((o.outcome[res] == outcomes.TIMEOUT).mean()), 2
        ),
        "median_measured_spread_pct": round(
            float(np.nanmedian(o.spread_pct[res])), 3
        ),
        "median_hurdle_pct": round(float(np.nanmedian(p.feat["hurdle_pct"][res])), 3),
        "median_premium_ask": round(float(np.nanmedian(p.entry_ask[res])), 2),
    })
    return base
