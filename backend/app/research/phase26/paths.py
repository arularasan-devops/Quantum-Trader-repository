"""Phase 26 §1 — candidates and their forward quote paths, built once.

Phase 25 builds a pool and resolves it in the same call, with the hold window
frozen at one hour and 60 forward quotes. This phase has to compare exits, and
one of the exits is *a longer hold*, so the path has to be built wider than any
single variant needs and then trimmed per variant. Building it once also means
every variant is measured on exactly the same rows: a difference between two
variants can only come from the exit rule.

The rules that make a captured-window result honest are the same ones Phase 25
uses, deliberately re-stated rather than re-derived:

* a candidate exists for **both** sides of every eligible snapshot, so nothing
  about the side is inherited from how the pool was built;
* the decision is taken on one snapshot and the fill is the **ask of a later
  stored quote** of the selected contract, inside the same session;
* the forward path is that same contract's later stored bids, inside the same
  session and inside the widest horizon, with a per-cell timestamp so a variant
  can cut the path at its own horizon and holding time can be reported in clock
  seconds rather than in a quote count;
* nothing between two stored quotes is observable, so nothing between them is
  ever counted, in either direction.
"""
from __future__ import annotations

import numpy as np

from app.analysis import option_costs
from app.market.instruments import get_spec
from app.research.phase24 import features as p24features
from app.research.phase24 import pool as p24pool
from app.research.phase25 import books, select, underlying

IST_OFFSET = 19_800

# One candidate every STRIDE snapshots per side, as in Phase 25: consecutive
# snapshots are a minute or two apart and would count one setup many times.
STRIDE = 2

# The widest path any variant may use. The longest-hold variant is three hours;
# a variant with a shorter horizon masks the tail rather than reloading.
MAX_STEPS = 180
MAX_HORIZON_SEC = 10_800

WARMUP_BARS = p24features.SLOW_VOL_WIN + p24features.PULLBACK_LOOKBACK

OPTION_FEATURE_KEYS = (
    "spread_pct", "hurdle_pct", "premium_ask", "moneyness_pct", "abs_delta",
    "iv", "leg_oi", "leg_volume", "quotes_ahead",
)


class Candidates:
    """One instrument's candidates plus the forward quotes that resolve them."""

    __slots__ = (
        "instrument", "feat", "idx", "side", "option_type", "symbol", "strike",
        "spot", "ts", "session", "sessions", "entry_ts", "entry_ask",
        "entry_bid", "path_bid", "path_ts", "path_valid", "lot", "coverage",
    )

    def __len__(self) -> int:
        return int(self.idx.size)


def sessions_of(ts: np.ndarray) -> np.ndarray:
    """IST session day of each timestamp."""
    return (ts + IST_OFFSET) // 86_400


def _decision_hurdle(instrument: str, bid: float, ask: float, qty: int) -> float:
    """Break-even hurdle of the decision-time book, as a % of the ask.

    Measured spread only. A leg with no book is not a candidate, so the
    family-median spread is never substituted here.
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
    steps: int = MAX_STEPS,
    horizon_sec: int = MAX_HORIZON_SEC,
    db_path: str | None = None,
) -> Candidates | None:
    """Build every candidate for one instrument, with its forward quote path.

    ``chain`` and ``series`` are injectable so a smoke test and the robustness
    grid re-use the *same* captured data instead of loading something else that
    would not be comparable.
    """
    inst = (instrument or "").upper()
    chain = books.load_chain(inst, db_path=db_path) if chain is None else chain
    if chain.snapshots == 0:
        return None
    series = underlying.load_series(inst, db_path=db_path) if series is None else series
    if series is None or len(series) <= WARMUP_BARS:
        return None

    cidx = underlying.align(series, chain.ts)
    feat = p24features.build(series)
    snap_sessions = sessions_of(chain.ts)
    lot = int(get_spec(inst).lot_size or 1)

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
            if sessions_of(np.asarray([entry_ts]))[0] != snap_sessions[i]:
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
    steps = max(1, int(steps))
    entry_ask = np.zeros(n, dtype=np.float64)
    entry_bid = np.zeros(n, dtype=np.float64)
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
        keep = (pts <= r["entry_ts"] + int(horizon_sec)) & (
            sessions_of(pts) == sessions_of(np.asarray([r["entry_ts"]]))[0]
        )
        m = int(keep.sum())
        if m == 0:
            continue
        path_bid[j, :m] = q.bid[lo:hi][keep]
        path_ts[j, :m] = pts[keep]
        path_valid[j, :m] = True
        quotes_ahead[j] = float(m)

    snap = np.asarray([r["snapshot"] for r in rows], dtype=np.int64)
    cand = np.asarray([r["candle"] for r in rows], dtype=np.int64)

    c_out = Candidates()
    c_out.instrument = inst
    c_out.idx = np.arange(n, dtype=np.int64)
    c_out.side = np.asarray(
        [1 if r["option_type"] == select.CE else -1 for r in rows], dtype=np.int8
    )
    c_out.option_type = np.asarray([r["option_type"] for r in rows])
    c_out.symbol = np.asarray([r["symbol"] for r in rows])
    c_out.strike = np.asarray([r["strike"] for r in rows], dtype=np.float64)
    c_out.spot = np.asarray([r["spot"] for r in rows], dtype=np.float64)
    c_out.ts = chain.ts[snap]
    c_out.session = snap_sessions[snap]
    c_out.sessions = int(np.unique(c_out.session).size)
    c_out.entry_ts = entry_ts
    c_out.entry_ask = entry_ask
    c_out.entry_bid = entry_bid
    c_out.path_bid = path_bid
    c_out.path_ts = path_ts
    c_out.path_valid = path_valid
    c_out.lot = lot

    feat_rows = {k: np.asarray(feat[k])[cand] for k in p24pool.FEATURE_KEYS}
    spread_pct = np.where(
        entry_ask > 0,
        100.0 * (entry_ask - entry_bid) / np.maximum(entry_ask, 1e-9),
        np.nan,
    )
    feat_rows.update({
        "spread_pct": spread_pct,
        "hurdle_pct": np.asarray([
            _decision_hurdle(inst, r["decision_bid"], r["decision_ask"], lot)
            for r in rows
        ], dtype=np.float64),
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
    c_out.feat = feat_rows
    c_out.coverage = {
        "snapshots": chain.snapshots,
        "sessions": chain.sessions,
        "snapshot_sides_considered": considered,
        "no_usable_leg": no_leg,
        "no_forward_quote_same_session": no_forward_quote,
        "candidates": n,
        "max_forward_quotes_kept": steps,
        "max_horizon_seconds": int(horizon_sec),
        "median_quotes_ahead": round(float(np.median(quotes_ahead)), 1),
        "simulator_snapshots_excluded": chain.simulator_snapshots,
        "one_sided_legs_excluded": chain.one_sided_legs,
        "lot_size_used": lot,
        "lot_size_source": (
            "instrument registry seed — brokerage per premium point scales with "
            "it; verify against the broker's scrip master before reading rupee "
            "figures as exact"
        ),
    }
    return c_out
