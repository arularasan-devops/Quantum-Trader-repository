"""Phase 29 §5 — the candidate pool of defined-risk structures.

One pool per instrument per structure per geometry. Every eligible snapshot
produces a candidate for the structure being built, whether or not the credit
looks attractive: a wide spread, a thin credit or brutal friction stays in the
pool, because those economics are exactly what the study is measuring. Filtering
them at build time would hide the answer.

Four counts are kept alongside the candidates, and they are the honest part of
the coverage report:

* ``no_structure`` — the captured ladder was not wide enough to quote both legs;
* ``no_paired_entry`` — the two legs were never quoted together again in the same
  session, so the structure could not be opened at a later timestamp than the
  decision;
* ``no_paired_exit`` — it could be opened but never closed on a paired quote.
  Those rows are dropped rather than credited with the premium they would have
  kept if the option expired worthless, which is not something this data knows;
* ``debit_or_unpriceable`` — the executable credit was not positive, so it was
  not a credit spread at all.
"""
from __future__ import annotations

import numpy as np

from app.market.instruments import get_spec
from app.research.phase24 import features as p24features
from app.research.phase24 import pool as p24pool
from app.research.phase25 import books, underlying
from app.research.phase29 import economics, legs, outcomes

IST_OFFSET = 19_800

# One candidate every STRIDE snapshots. Consecutive snapshots are ~1-2 minutes
# apart and would count the same structure many times over, which turns every
# sample size into a fiction.
STRIDE = 2

WARMUP_BARS = p24features.SLOW_VOL_WIN + p24features.PULLBACK_LOOKBACK

SPREAD_FEATURE_KEYS = (
    "credit_points", "credit_pct_of_width", "max_loss_points", "friction_pct",
    "short_abs_delta", "short_iv", "short_oi", "short_volume", "short_otm_pct",
    "structure_spread_points", "quotes_ahead",
)


class Prepared:
    """The quote matrices for one geometry, before any exit rule is applied.

    Kept as its own object so the stop-band sweep and the robustness grid
    re-resolve *the same* captured quotes instead of re-reading the store. Two
    variants that differ only in an exit rule must be built from identical data,
    or the comparison between them measures the loader rather than the rule.
    """

    __slots__ = ("instrument", "structure", "short_steps", "width_steps",
                 "width_points", "strike_step", "idx", "short_bid", "short_ask",
                 "long_bid", "long_ask", "path_short_ask", "path_long_bid",
                 "path_valid", "path_ts", "quotes_ahead", "rows", "feat_base",
                 "ts", "session", "entry_ts", "chain_stats", "counts", "hold",
                 "horizon_sec", "max_steps")

    def __len__(self) -> int:
        return int(self.idx.size)


class Pool:
    """Candidates for one instrument and one structure geometry."""

    __slots__ = ("instrument", "structure", "short_steps", "width_steps",
                 "width_points", "stop_credit_mult", "feat", "idx", "side",
                 "out", "ts", "session", "sessions", "short_symbols",
                 "long_symbols", "entry_ts", "exit_ts", "hold_sec", "coverage",
                 "hold")

    def __len__(self) -> int:
        return int(self.idx.size)


def _sessions(ts: np.ndarray) -> np.ndarray:
    return (ts + IST_OFFSET) // 86_400


def _leg_stat(rows: list[dict], key: str, reducer) -> np.ndarray:
    return np.asarray(
        [reducer([float(v) for v in r[key]]) if r[key] else float("nan")
         for r in rows],
        dtype=np.float64,
    )


def _nan_mean(values: list[float]) -> float:
    arr = np.asarray(values, dtype=np.float64)
    return float(np.nanmean(arr)) if arr.size else float("nan")


def _nan_min(values: list[float]) -> float:
    arr = np.asarray(values, dtype=np.float64)
    return float(np.nanmin(arr)) if arr.size else float("nan")


def prepare(
    instrument: str,
    *,
    structure: str,
    short_steps: int,
    width_steps: int,
    chain: books.Chain | None = None,
    series=None,
    stride: int = STRIDE,
    hold: str = outcomes.HOLD_1H,
) -> Prepared | None:
    """Collect one geometry's paired entry books and forward paths.

    ``chain`` and ``series`` are injectable so the sweep, the robustness grid and
    the smoke test all work from the *same* captured data rather than loading a
    different pool that would not be comparable.
    """
    inst = (instrument or "").upper()
    chain = books.load_chain(inst) if chain is None else chain
    if chain.snapshots == 0:
        return None
    series = underlying.load_series(inst) if series is None else series
    if series is None or len(series) <= WARMUP_BARS:
        return None

    step = legs.strike_step(chain)
    if step <= 0:
        return None

    cidx = underlying.align(series, chain.ts)
    feat = p24features.build(series)
    snap_sessions = _sessions(chain.ts)
    horizon_sec, max_steps = outcomes.HOLDS[hold]

    rows: list[dict] = []
    considered = 0
    no_structure = 0
    no_paired_entry = 0
    no_paired_exit = 0

    for i in range(0, chain.snapshots, max(1, int(stride))):
        c = int(cidx[i])
        if c < WARMUP_BARS:
            continue
        atr = float(feat["atr"][c])
        if not np.isfinite(atr) or atr <= 0:
            continue
        considered += 1
        spot = float(series.close[c])
        pair = legs.build_pair(
            chain.legs[i], spot=spot, step=step, structure=structure,
            short_steps=short_steps, width_steps=width_steps,
        )
        if pair is None:
            no_structure += 1
            continue

        symbols = pair.short_symbols + pair.long_symbols
        forward = legs.common_path(chain, symbols, i)
        if forward.size == 0:
            no_paired_entry += 1
            continue
        entry_pos = int(forward[0])
        if snap_sessions[entry_pos] != snap_sessions[i]:
            no_paired_entry += 1
            continue
        entry_ts = int(chain.ts[entry_pos])

        after = forward[1:]
        if after.size:
            keep = (
                (chain.ts[after] <= entry_ts + horizon_sec)
                & (snap_sessions[after] == snap_sessions[i])
            )
            after = after[keep][:max_steps]
        if after.size == 0:
            no_paired_exit += 1
            continue

        rows.append({
            "snapshot": i,
            "candle": c,
            "spot": spot,
            "entry_pos": entry_pos,
            "entry_ts": entry_ts,
            "path_pos": after,
            "short_symbols": pair.short_symbols,
            "long_symbols": pair.long_symbols,
            "short_strikes": pair.short_strikes,
            "delta": [leg.get("delta") for leg in pair.short_legs],
            "iv": [leg.get("iv") for leg in pair.short_legs],
            "oi": [leg.get("oi") for leg in pair.short_legs],
            "volume": [leg.get("volume") for leg in pair.short_legs],
            "width_points": pair.width_points,
        })

    if not rows:
        return None

    n = len(rows)
    n_short = len(rows[0]["short_symbols"])
    n_long = len(rows[0]["long_symbols"])
    idx = np.arange(n, dtype=np.int64)
    short_bid = np.zeros((n, n_short), dtype=np.float64)
    short_ask = np.zeros((n, n_short), dtype=np.float64)
    long_bid = np.zeros((n, n_long), dtype=np.float64)
    long_ask = np.zeros((n, n_long), dtype=np.float64)
    path_short_ask = np.full((n, n_short, max_steps), np.nan, dtype=np.float64)
    path_long_bid = np.full((n, n_long, max_steps), np.nan, dtype=np.float64)
    path_valid = np.zeros((n, max_steps), dtype=bool)
    path_ts = np.zeros((n, max_steps), dtype=np.int64)
    quotes_ahead = np.zeros(n, dtype=np.float64)

    for j, r in enumerate(rows):
        for k, symbol in enumerate(r["short_symbols"]):
            bid, ask = legs.quote_at(chain, symbol, r["entry_pos"])
            short_bid[j, k], short_ask[j, k] = bid, ask
        for k, symbol in enumerate(r["long_symbols"]):
            bid, ask = legs.quote_at(chain, symbol, r["entry_pos"])
            long_bid[j, k], long_ask[j, k] = bid, ask
        for m, pos in enumerate(r["path_pos"]):
            for k, symbol in enumerate(r["short_symbols"]):
                path_short_ask[j, k, m] = legs.quote_at(chain, symbol, int(pos))[1]
            for k, symbol in enumerate(r["long_symbols"]):
                path_long_bid[j, k, m] = legs.quote_at(chain, symbol, int(pos))[0]
            path_valid[j, m] = True
            path_ts[j, m] = int(chain.ts[int(pos)])
        quotes_ahead[j] = float(len(r["path_pos"]))

    width_points = float(rows[0]["width_points"])
    cand = np.asarray([r["candle"] for r in rows], dtype=np.int64)
    snap = np.asarray([r["snapshot"] for r in rows], dtype=np.int64)

    prep = Prepared()
    prep.instrument = inst
    prep.structure = structure
    prep.hold = hold
    prep.horizon_sec = int(horizon_sec)
    prep.max_steps = int(max_steps)
    prep.short_steps = int(short_steps)
    prep.width_steps = int(width_steps)
    prep.width_points = width_points
    prep.strike_step = step
    prep.idx = idx
    prep.short_bid, prep.short_ask = short_bid, short_ask
    prep.long_bid, prep.long_ask = long_bid, long_ask
    prep.path_short_ask, prep.path_long_bid = path_short_ask, path_long_bid
    prep.path_valid, prep.path_ts = path_valid, path_ts
    prep.quotes_ahead = quotes_ahead
    prep.rows = rows
    prep.ts = chain.ts[snap]
    prep.session = snap_sessions[snap]
    prep.entry_ts = np.asarray([r["entry_ts"] for r in rows], dtype=np.int64)
    prep.feat_base = {k: np.asarray(feat[k])[cand] for k in p24pool.FEATURE_KEYS}
    prep.chain_stats = {
        "snapshots": chain.snapshots,
        "sessions": chain.sessions,
        "simulator_snapshots_excluded": chain.simulator_snapshots,
        "one_sided_legs_excluded": chain.one_sided_legs,
    }
    prep.counts = {
        "hold": hold,
        "horizon_seconds": int(horizon_sec),
        "max_forward_paired_quotes": int(max_steps),
        "snapshots_considered": considered,
        "no_structure_ladder_too_narrow": no_structure,
        "no_paired_entry_quote": no_paired_entry,
        "no_paired_exit_quote": no_paired_exit,
    }
    return prep


def resolve_prepared(prep: Prepared, **resolve_kw) -> Pool:
    """Apply one exit rule to a prepared geometry, producing a scored pool."""
    inst = prep.instrument
    structure = prep.structure
    rows = prep.rows
    n = len(rows)
    idx = prep.idx
    width_points = prep.width_points
    path_ts = prep.path_ts

    out = outcomes.resolve(
        inst,
        idx=idx,
        structure=structure,
        width_points=width_points,
        short_bid=prep.short_bid,
        short_ask=prep.short_ask,
        long_bid=prep.long_bid,
        long_ask=prep.long_ask,
        path_short_ask=prep.path_short_ask,
        path_long_bid=prep.path_long_bid,
        path_valid=prep.path_valid,
        **resolve_kw,
    )

    p = Pool()
    p.instrument = inst
    p.structure = structure
    p.hold = prep.hold
    p.short_steps = prep.short_steps
    p.width_steps = prep.width_steps
    p.width_points = width_points
    p.stop_credit_mult = float(
        resolve_kw.get("stop_credit_mult", outcomes.STOP_CREDIT_MULT)
    )
    p.idx = idx
    view = legs.VIEW[structure]
    p.side = np.full(n, 1 if view == "LONG_VIEW" else -1, dtype=np.int8)
    p.out = out
    p.short_symbols = np.asarray([",".join(r["short_symbols"]) for r in rows])
    p.long_symbols = np.asarray([",".join(r["long_symbols"]) for r in rows])
    p.entry_ts = prep.entry_ts
    p.exit_ts = np.where(
        out.resolved, path_ts[np.arange(n), np.maximum(out.exit_step, 0)], 0
    ).astype(np.int64)
    p.hold_sec = np.where(out.resolved, p.exit_ts - p.entry_ts, -1).astype(np.int64)
    p.ts = prep.ts
    p.session = prep.session
    p.sessions = int(np.unique(p.session).size)

    feat_rows = dict(prep.feat_base)
    spot_arr = np.asarray([r["spot"] for r in rows], dtype=np.float64)
    short_strike = np.asarray(
        [min(abs(s - r["spot"]) for s in r["short_strikes"]) for r in rows],
        dtype=np.float64,
    )
    feat_rows.update({
        "credit_points": out.credit,
        "credit_pct_of_width": np.where(
            width_points > 0, 100.0 * out.credit / width_points, np.nan
        ),
        "max_loss_points": out.max_loss,
        "friction_pct": out.hurdle_pct,
        "short_abs_delta": np.abs(_leg_stat(rows, "delta", _nan_mean)),
        "short_iv": _leg_stat(rows, "iv", _nan_mean),
        "short_oi": _leg_stat(rows, "oi", _nan_min),
        "short_volume": _leg_stat(rows, "volume", _nan_min),
        "short_otm_pct": np.where(
            spot_arr > 0, 100.0 * short_strike / spot_arr, np.nan
        ),
        "structure_spread_points": out.spread_points,
        "quotes_ahead": prep.quotes_ahead,
    })
    p.feat = feat_rows

    res = out.resolved
    p.coverage = {
        "structure": structure,
        "hold": prep.hold,
        "underlying_view": view,
        "short_steps_otm": prep.short_steps,
        "width_steps": prep.width_steps,
        "strike_step_measured": round(prep.strike_step, 4),
        "width_points": round(width_points, 2),
        **prep.chain_stats,
        **prep.counts,
        "candidates": n,
        "resolved": int(res.sum()),
        "debit_or_unpriceable": int(
            (~economics.priceable(out.credit, out.max_loss)).sum()
        ),
        "quotes_beyond_defined_loss": int(out.floor_breaches),
        "median_hold_sec": (
            round(float(np.median(p.hold_sec[res])), 1) if res.any() else None
        ),
        "lot_size_used": int(get_spec(inst).lot_size or 1),
        "lot_size_source": (
            "instrument registry seed — brokerage per premium point scales with "
            "it; verify against the broker's scrip master before reading rupee "
            "figures as exact"
        ),
    }
    return p


def build(
    instrument: str,
    *,
    structure: str,
    short_steps: int,
    width_steps: int,
    chain: books.Chain | None = None,
    series=None,
    stride: int = STRIDE,
    hold: str = outcomes.HOLD_1H,
    **resolve_kw,
) -> Pool | None:
    """Prepare one geometry and resolve it under one exit rule."""
    prep = prepare(
        instrument, structure=structure, short_steps=short_steps,
        width_steps=width_steps, chain=chain, series=series, stride=stride,
        hold=hold,
    )
    if prep is None:
        return None
    return resolve_prepared(prep, **resolve_kw)


def summary(p: Pool) -> dict:
    """Pool-level counts, including the base rate any cohort must beat."""
    o = p.out
    res = o.resolved
    total = int(res.sum())
    base = {
        "instrument": p.instrument,
        "structure": p.structure,
        "hold": p.hold,
        "short_steps_otm": p.short_steps,
        "width_steps": p.width_steps,
        "width_points": round(p.width_points, 2),
        "stop_credit_multiple": p.stop_credit_mult,
        "candidates": int(len(p)),
        "resolved": total,
        "unresolved": int((~res).sum()),
        "sessions": p.sessions,
        "first_ts": int(p.ts.min()) if len(p) else 0,
        "last_ts": int(p.ts.max()) if len(p) else 0,
    }
    if total == 0:
        return base
    base.update({
        "base_target_before_stop_pct": round(
            100.0 * float(o.t1_before_sl[res].mean()), 2
        ),
        "base_avg_net_r": round(float(o.net_r[res].mean()), 4),
        "base_avg_return_on_defined_risk_pct": round(
            100.0 * float(o.ror_defined_risk[res].mean()), 3
        ),
        "base_avg_net_rupees": round(float(o.net_rupees[res].mean()), 2),
        "base_total_net_rupees": round(float(o.net_rupees[res].sum()), 2),
        "base_timeout_pct": round(
            100.0 * float((o.outcome[res] == outcomes.TIMEOUT).mean()), 2
        ),
        "median_credit_points": round(float(np.median(o.credit[res])), 2),
        "median_credit_pct_of_width": round(
            float(np.median(100.0 * o.credit[res] / max(p.width_points, 1e-9))), 2
        ),
        "median_defined_loss_points": round(float(np.median(o.max_loss[res])), 2),
        "median_friction_pct_of_credit": round(
            float(np.nanmedian(o.hurdle_pct[res])), 2
        ),
        "median_structure_spread_points": round(
            float(np.nanmedian(o.spread_points[res])), 3
        ),
    })
    return base
