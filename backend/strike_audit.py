"""OFFLINE strike / leg-selection audit (Phase 3) — research only.

Phase 2 showed the engine buys on the bar the setup becomes valid (median delay
0 bars) yet still lands late in the leg. That points the finger at *which
contract* it commits to, not *when*. This tool answers that question.

Method, and its honest limits — read before quoting any number:

* **Underlying is real.** Angel one-minute candles from the backtest cache.
* **The option ladder is MODELLED.** No historical option chain exists in this
  install: every ``chain_snapshots`` row in ``history.db`` was written by the
  *simulated* provider (its ``oi_change``/``volume`` are Gaussian draws), so it
  cannot be used to judge strike choice without the answer being circular.
  The ladder here is priced with the repo's own Black-Scholes
  (``app.market.options``) at a **trailing-realised volatility** measured from
  the previous ``_VOL_LOOKBACK`` bars — future-independent, and per-strike
  rather than Phase 2's single fixed 0.6 delta.
* **Flat surface.** One sigma for all strikes: no skew, no smile, no term
  structure. This is the load-bearing assumption. It biases OTM/ITM comparisons
  (real skew makes OTM puts richer), so every ATM-vs-OTM conclusion must be
  read as conditional.
* **IV frozen after entry.** Forward premiums re-price on spot and decay only.
  A real vol crush or spike is not modelled.
* **OI / volume / spread do not exist.** They are left at zero rather than
  invented: fabricating them would manufacture correlations. Consequence: the
  engine's own selector scores every candidate's liquidity equally here, so its
  choice reduces to its delta preference — which is what we want to audit.

    .venv/bin/python strike_audit.py --instrument CRUDEOIL --bars 120000
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict

import numpy as np

from app.backtest import angel_history as ah
from app.config import settings
from app.engine.decision import classify_market, compute_indicators, decide
from app.market import options
from app.market.instruments import REGISTRY
from app.models import Candle, OptionQuote, OptionType, Signal
from entry_timing_audit import classify as classify_timing

_WINDOW = 240          # lookback fed to the engine
_VOL_LOOKBACK = 120    # bars used for the trailing realised-vol estimate
_LEG_LOOKBACK = 30     # bars searched for the leg origin before a BUY
_STEPS_DEFAULT = 2     # strike ladder half-width, in strike steps, either side of ATM
_MIN_SIGMA = 0.05
_MAX_SIGMA = 2.00
_DAY_MINUTES = 1440.0
_YEAR_DAYS = 365.0
_IST_OFFSET = 5.5 * 3600
_TIE_R = 0.02          # two candidates within this much R are the same outcome
_EXPANDED_PCT = 25.0   # "already expanded" threshold for the §7/§8 buckets

# Minutes of trading per year, per exchange, for annualising realised vol.
_BARS_PER_YEAR = {"MCX": 252 * 870, "NFO": 252 * 375, "NSE": 252 * 375,
                  "BFO": 252 * 375, "BSE": 252 * 375, "CDS": 252 * 375}


# ---------------------------------------------------------------- pricing ----
def realised_sigma(candles: list[Candle], i: int, bars_per_year: int) -> float:
    """Annualised volatility from the *previous* ``_VOL_LOOKBACK`` closes only."""
    seg = candles[max(1, i - _VOL_LOOKBACK) : i + 1]
    rets = [
        math.log(seg[k].close / seg[k - 1].close)
        for k in range(1, len(seg))
        if seg[k].close > 0 and seg[k - 1].close > 0
    ]
    if len(rets) < 20:
        return 0.30
    sd = statistics.pstdev(rets)
    return min(_MAX_SIGMA, max(_MIN_SIGMA, sd * math.sqrt(bars_per_year)))


def leg_premium(spot: float, strike: float, side: OptionType, sigma: float,
                dte_days: float, r: float) -> float:
    t = max(1e-6, dte_days / _YEAR_DAYS)
    return options.price(spot, strike, r, sigma, t, side == OptionType.CALL)


def ladder(root: str, spot: float, step: float, sigma: float, dte_days: float,
           r: float, half_width: int = _STEPS_DEFAULT) -> list[OptionQuote]:
    """``half_width`` strikes either side of ATM, both sides, at ``sigma``.

    ``oi``/``volume``/spread are zero — see the module docstring. They are
    *absent* data, not zero liquidity.
    """
    atm = round(spot / step) * step
    t = max(1e-6, dte_days / _YEAR_DAYS)
    out: list[OptionQuote] = []
    for k in range(-half_width, half_width + 1):
        strike = float(atm + k * step)
        if strike <= 0:
            continue
        for side in (OptionType.CALL, OptionType.PUT):
            is_call = side == OptionType.CALL
            g = options.greeks(spot, strike, r, sigma, t, is_call)
            out.append(
                OptionQuote(
                    symbol=f"{root}{int(strike)}{side.value}",
                    strike=strike,
                    option_type=side,
                    premium=round(options.price(spot, strike, r, sigma, t, is_call), 2),
                    iv=round(sigma, 4),
                    delta=g["delta"], gamma=g["gamma"],
                    theta=g["theta"], vega=g["vega"],
                    oi=0, oi_change=0, volume=0,
                )
            )
    return out


# ------------------------------------------------------------- outcome sim ----
def walk(candles: list[Candle], i: int, horizon: int, strike: float,
         side: OptionType, entry: float, stop: float, target: float,
         sigma: float, dte_days: float, r: float) -> dict:
    """Forward-walk one candidate on identical terms to every other candidate.

    A bar that spans both stop and target is scored **STOP** — the optimistic
    reading would flatter every entry in this report equally, and the
    pessimistic one is what a real order would suffer.
    """
    want_call = side == OptionType.CALL
    risk = max(0.01, entry - stop)
    mfe = 0.0
    mae = 0.0
    hit = "OPEN"
    bars_to_target = None
    last = entry
    for n, c in enumerate(candles[i + 1 : i + 1 + horizon], start=1):
        dte = max(1e-4, dte_days - n / _DAY_MINUTES)
        hi_spot = c.high if want_call else c.low
        lo_spot = c.low if want_call else c.high
        hi = leg_premium(hi_spot, strike, side, sigma, dte, r)
        lo = leg_premium(lo_spot, strike, side, sigma, dte, r)
        last = leg_premium(c.close, strike, side, sigma, dte, r)
        mfe = max(mfe, hi - entry)
        mae = min(mae, lo - entry)
        if hit == "OPEN":
            if lo <= stop:
                hit = "STOP"
            elif hi >= target:
                hit = "TARGET"
                bars_to_target = n
    if hit == "TARGET":
        realised = (target - entry) / risk
    elif hit == "STOP":
        realised = -1.0
    else:
        realised = (last - entry) / risk
    return {
        "hit": hit,
        "r": realised,
        "mfe_r": mfe / risk,
        "mae_r": mae / risk,
        "mfe_pct": 100.0 * mfe / max(0.01, entry),
        "mae_pct": 100.0 * mae / max(0.01, entry),
        "bars_to_target": bars_to_target,
        "target_first": hit == "TARGET",
    }


# ------------------------------------------------------------- statistics ----
def med(xs: list[float]) -> float:
    return round(statistics.median(xs), 4) if xs else 0.0


def expectancy(rs: list[float]) -> float:
    return round(sum(rs) / len(rs), 4) if rs else 0.0


def profit_factor(rs: list[float]) -> float:
    win = sum(x for x in rs if x > 0)
    loss = -sum(x for x in rs if x < 0)
    if loss <= 0:
        return round(win, 2) if win else 0.0
    return round(win / loss, 2)


def max_drawdown(rs: list[float]) -> float:
    peak = 0.0
    eq = 0.0
    dd = 0.0
    for x in rs:
        eq += x
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return round(dd, 2)


def pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson r; with a binary ``ys`` this is the point-biserial correlation."""
    if len(xs) < 30:
        return 0.0
    a = np.asarray(xs, dtype=float)
    b = np.asarray(ys, dtype=float)
    if a.std() < 1e-12 or b.std() < 1e-12:
        return 0.0
    return round(float(np.corrcoef(a, b)[0, 1]), 4)


def auc(scores: list[float], labels: list[float]) -> float:
    """Rank-based AUC (Mann-Whitney), ties averaged. 0.5 == coin flip."""
    pos = [s for s, y in zip(scores, labels) if y > 0.5]
    neg = [s for s, y in zip(scores, labels) if y <= 0.5]
    if not pos or not neg:
        return 0.5
    order = np.argsort(np.asarray(scores, dtype=float), kind="mergesort")
    ranks = np.empty(len(scores), dtype=float)
    srt = np.asarray(scores, dtype=float)[order]
    r = np.arange(1, len(scores) + 1, dtype=float)
    k = 0
    while k < len(srt):
        j = k
        while j + 1 < len(srt) and srt[j + 1] == srt[k]:
            j += 1
        r[k : j + 1] = r[k : j + 1].mean()
        k = j + 1
    ranks[order] = r
    y = np.asarray(labels, dtype=float) > 0.5
    return round(float((ranks[y].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))), 4)


# --------------------------------------------------------- research model ----
FEATURES = (
    "steps_from_atm", "moneyness_pct", "abs_delta", "theta_pct_per_day",
    "gamma_leverage", "premium_pct_of_spot", "room_atr", "mom5_atr",
    "mom15_atr", "vwap_dist_atr", "atr_pct", "minute_of_session", "dte_days",
    "confidence", "pre_expansion_ratio",
)


def logistic(x_tr: np.ndarray, y_tr: np.ndarray, epochs: int = 400,
             lr: float = 0.5, l2: float = 1e-3) -> np.ndarray:
    """Plain L2 logistic regression by gradient descent (no sklearn here)."""
    n, d = x_tr.shape
    w = np.zeros(d + 1)
    xb = np.hstack([np.ones((n, 1)), x_tr])
    for _ in range(epochs):
        p = 1.0 / (1.0 + np.exp(-np.clip(xb @ w, -30, 30)))
        grad = xb.T @ (p - y_tr) / n
        grad[1:] += l2 * w[1:]
        w -= lr * grad
    return w


def predict(w: np.ndarray, x: np.ndarray) -> np.ndarray:
    xb = np.hstack([np.ones((x.shape[0], 1)), x])
    return 1.0 / (1.0 + np.exp(-np.clip(xb @ w, -30, 30)))


def standardise(x: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    return (x - mu) / np.where(sd < 1e-9, 1.0, sd)


# -------------------------------------------------------------------- main ----
def main() -> None:  # noqa: PLR0912, PLR0915 — one research report, read top-down
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="CRUDEOIL")
    ap.add_argument("--interval", default="ONE_MINUTE")
    ap.add_argument("--bars", type=int, default=120000)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--horizon", type=int, default=30)
    ap.add_argument("--dte", type=float, default=7.0,
                    help="days to expiry assumed at every bar (sensitivity knob)")
    ap.add_argument("--steps", type=int, default=_STEPS_DEFAULT,
                    help="candidate strikes each side of ATM. The default 2 is the "
                         "window production searches; a wider one tests whether "
                         "strike distance matters at all")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    horizon = args.horizon
    r_rate = settings.risk_free_rate

    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)
    candles = ah.load_candles(path)
    if not candles:
        raise SystemExit(f"no cached candles at {path}")
    candles = candles[-args.bars :]
    spec = REGISTRY.get(args.instrument)
    step = spec.strike_step if spec else 50.0
    root = spec.symbol if spec else args.instrument
    bpy = _BARS_PER_YEAR.get(spec.exchange if spec else "NFO", 252 * 375)

    # ------------------------------------------------ pass 1: replay + choose
    buys: list[dict] = []
    n_bars = 0
    for i in range(_WINDOW, len(candles) - horizon - 1, args.stride):
        n_bars += 1
        window = candles[i - _WINDOW : i + 1]
        spot = float(window[-1].close)
        sigma = realised_sigma(candles, i, bpy)
        chain = ladder(root, spot, step, sigma, args.dte, r_rate, args.steps)
        snap = compute_indicators(window, chain)
        status = classify_market(snap, False)
        dec, _ = decide(window, chain, snap, 0.0, False, status, False, None, 1.0,
                        spot=spot)
        if dec.signal != Signal.BUY or dec.option_type is None or dec.strike is None:
            continue
        entry = float(dec.current_premium or 0.0)
        if entry <= 0 or dec.stop_loss is None or dec.target1 is None:
            continue
        side = dec.option_type
        # The engine's own risk terms, expressed as ratios so every candidate is
        # walked on identical terms (Phase 2D's rule, applied across strikes).
        stop_pct = max(0.01, (entry - dec.stop_loss) / entry)
        rr = max(0.1, (dec.target1 - entry) / max(0.01, entry - dec.stop_loss))
        atr = float(snap.atr or 0.0) or max(0.01, 0.001 * spot)
        vwap = float(snap.vwap) if snap.vwap is not None else spot
        mom5 = (spot - candles[i - 5].close) / atr if i >= 5 else 0.0
        mom15 = (spot - candles[i - 15].close) / atr if i >= 15 else 0.0
        sess_min = ((candles[i].time + _IST_OFFSET) % 86400) / 60.0
        # +1 for a CE, -1 for a PE: every "move" below is in the trade's own
        # direction, so a favourable move is positive on both sides.
        dirn = 1.0 if side == OptionType.CALL else -1.0

        cands: list[dict] = []
        atm_strike = round(spot / step) * step
        for q in chain:
            if q.option_type != side:
                continue
            k_off = int(round((q.strike - atm_strike) / step))
            c_entry = q.premium
            if c_entry <= 0.05:
                continue
            c_stop = c_entry * (1.0 - stop_pct)
            c_target = c_entry + (c_entry - c_stop) * rr
            out = walk(candles, i, horizon, q.strike, side, c_entry, c_stop,
                       c_target, sigma, args.dte, r_rate)
            # --- pre-entry expansion: what this leg had already done -------
            j0 = max(0, i - _LEG_LOOKBACK)
            base_spot = candles[j0].close
            base_prem = leg_premium(base_spot, q.strike, side, sigma,
                                    args.dte + _LEG_LOOKBACK / _DAY_MINUTES, r_rate)
            und_move_pct = dirn * 100.0 * (spot - base_spot) / max(1e-9, base_spot)
            prem_move_pct = 100.0 * (c_entry - base_prem) / max(0.01, base_prem)
            expansion = prem_move_pct / und_move_pct if abs(und_move_pct) > 0.01 else 0.0
            # --- Phase 2's timing class for the leg the engine actually took,
            # so "was it a peak buy?" and "was it the wrong strike?" can be
            # cross-tabulated instead of argued about.
            timing_cls = ""
            if q.symbol == dec.recommended_option:
                lows = [
                    leg_premium(candles[j].close, q.strike, side, sigma,
                                args.dte + (i - j) / _DAY_MINUTES, r_rate)
                    for j in range(max(0, i - _LEG_LOOKBACK), i + 1)
                ]
                leg_low = min(lows)
                peak_prem = c_entry + max(0.0, out["mfe_r"]) * (c_entry - c_stop)
                consumed = min(1.0, max(0.0, (c_entry - leg_low)
                                        / max(0.01, peak_prem - leg_low)))
                timing_cls = classify_timing(out["mfe_r"], consumed,
                                             out["hit"] == "STOP")
            # --- room left in the underlying, at entry, no future data -----
            expected_move = atr * math.sqrt(horizon)
            dist_to_strike = (q.strike - spot) if side == OptionType.CALL else (spot - q.strike)
            cands.append({
                "symbol": q.symbol, "strike": q.strike, "k_off": k_off,
                "entry": round(c_entry, 2), "stop": round(c_stop, 2),
                "target": round(c_target, 2),
                "delta": abs(q.delta), "gamma": q.gamma, "theta": q.theta,
                "iv": q.iv,
                "chosen": q.symbol == dec.recommended_option,
                "features": {
                    "steps_from_atm": float(k_off),
                    "moneyness_pct": 100.0 * (spot - q.strike) / spot * (1 if side == OptionType.CALL else -1),
                    "abs_delta": abs(q.delta),
                    "theta_pct_per_day": 100.0 * abs(q.theta) / max(0.01, c_entry),
                    "gamma_leverage": q.gamma * spot * spot / max(0.01, c_entry) / 100.0,
                    "premium_pct_of_spot": 100.0 * c_entry / spot,
                    "room_atr": (expected_move - dist_to_strike) / atr,
                    "mom5_atr": mom5,
                    "mom15_atr": mom15,
                    "vwap_dist_atr": (spot - vwap) / atr * (1 if side == OptionType.CALL else -1),
                    "atr_pct": 100.0 * atr / spot,
                    "minute_of_session": sess_min,
                    "dte_days": args.dte,
                    "confidence": float(dec.gates.confidence if dec.gates else 0.0),
                    "pre_expansion_ratio": max(-50.0, min(50.0, expansion)),
                },
                "timing_class": timing_cls,
                "und_move_pct_before": und_move_pct,
                "prem_move_pct_before": prem_move_pct,
                "expansion_ratio": expansion,
                **out,
            })
        if not cands or not any(c["chosen"] for c in cands):
            continue
        # forward underlying room, hindsight — used only to label divergence
        post = candles[i + 1 : i + 1 + horizon]
        fwd_und = max((c.high - spot) if side == OptionType.CALL else (spot - c.low)
                      for c in post) if post else 0.0
        buys.append({
            "i": i, "ts": candles[i].time, "spot": spot, "side": side.value,
            "sigma": round(sigma, 4), "atr": atr,
            "regime": status.value if hasattr(status, "value") else str(status),
            "candidates": cands,
            "fwd_und_atr": fwd_und / atr,
        })

    if not buys:
        raise SystemExit("no BUYs produced — nothing to audit")

    # ------------------------------------------------------ §2/§3 aggregate
    chosen_rows = [next(c for c in b["candidates"] if c["chosen"]) for b in buys]
    by_off: dict[int, list[dict]] = defaultdict(list)
    for b in buys:
        for c in b["candidates"]:
            by_off[c["k_off"]].append(c)

    def block(rows: list[dict]) -> dict:
        rs = [c["r"] for c in rows]
        return {
            "n": len(rows),
            "target_before_stop_pct": round(
                100.0 * sum(1 for c in rows if c["target_first"]) / max(1, len(rows)), 1),
            "expectancy_r": expectancy(rs),
            "profit_factor": profit_factor(rs),
            "max_drawdown_r": max_drawdown(rs),
            "median_mfe_r": med([c["mfe_r"] for c in rows]),
            "median_mae_r": med([c["mae_r"] for c in rows]),
            "median_mfe_pct": med([c["mfe_pct"] for c in rows]),
            "median_entry_premium": med([c["entry"] for c in rows]),
            "median_delta": med([c["delta"] for c in rows]),
            "median_bars_to_target": med(
                [float(c["bars_to_target"]) for c in rows if c["bars_to_target"]]),
        }

    # ------------------------------------------------------ §5 delta bands
    bands = ((0.30, 0.40), (0.40, 0.50), (0.50, 0.60), (0.60, 0.70),
             (0.70, 0.80), (0.80, 1.01))
    delta_bands = {}
    for lo, hi in bands:
        rows = [c for off in by_off.values() for c in off if lo <= c["delta"] < hi]
        if rows:
            delta_bands[f"{lo:.2f}-{hi:.2f}"] = block(rows)
    low_delta = [c for off in by_off.values() for c in off if c["delta"] < 0.30]
    if low_delta:
        delta_bands["<0.30"] = block(low_delta)

    # ------------------------------------------- §6 moneyness, per side
    def bucket(k_off: int, side: str) -> str:
        # a CE above spot is OTM; a PE above spot is ITM
        signed = k_off if side == "CE" else -k_off
        return {(-2): "ITM2", (-1): "ITM1", 0: "ATM", 1: "OTM1", 2: "OTM2"}.get(signed, "FAR")

    moneyness: dict[str, dict] = {}
    for side in ("CE", "PE"):
        for name in ("ITM2", "ITM1", "ATM", "OTM1", "OTM2"):
            rows = [c for b in buys if b["side"] == side
                    for c in b["candidates"] if bucket(c["k_off"], side) == name]
            if rows:
                moneyness[f"{side}_{name}"] = block(rows)

    # ------------------------------------------- §4 feature correlations
    all_c = [c for b in buys for c in b["candidates"]]
    labels = [1.0 if c["target_first"] else 0.0 for c in all_c]
    corr = {
        f: {"r_vs_target_before_stop": pearson([c["features"][f] for c in all_c], labels),
            "r_vs_realised_r": pearson([c["features"][f] for c in all_c],
                                       [c["r"] for c in all_c])}
        for f in FEATURES
    }

    # ------------------------------------ §7/§8 expansion + divergence
    exp_ratios = [c["expansion_ratio"] for c in chosen_rows if c["expansion_ratio"]]
    # Future-independent: bucket entries by how far the chosen leg had ALREADY
    # run before the engine bought it. This is the §7 question — is the engine
    # buying options that have already spent their move? — and it is answerable
    # without any forward information.
    exp_buckets = {}
    for name, lo, hi in (("<0%", -1e9, 0.0), ("0-10%", 0.0, 10.0),
                        ("10-25%", 10.0, 25.0), ("25-50%", 25.0, 50.0),
                        ("50-100%", 50.0, 100.0), (">100%", 100.0, 1e9)):
        rows_b = [c for c in chosen_rows if lo <= c["prem_move_pct_before"] < hi]
        if rows_b:
            exp_buckets[name] = block(rows_b)
    # The divergence set below is defined partly with hindsight (the forward
    # underlying travel), so its outcome stats are conditioned on the future and
    # must not be read as an edge. Only its FREQUENCY is a finding.
    divergence = [
        b for b in buys
        if next(c for c in b["candidates"] if c["chosen"])["prem_move_pct_before"] >= _EXPANDED_PCT
        and b["fwd_und_atr"] >= 1.0
    ]
    div_rows = [next(c for c in b["candidates"] if c["chosen"]) for b in divergence]
    already_expanded = [c for c in chosen_rows if c["prem_move_pct_before"] >= _EXPANDED_PCT]

    # ---------------------------- §9/§11 was the chosen leg the best one?
    # Ties matter here. When every candidate stops out they all score -1R, and
    # ranking them would grade an unavoidable loss as a selection failure. A
    # candidate within _TIE_R of the best outcome *is* the best outcome.
    grade: Counter[str] = Counter()
    grade_by_off: dict[int, Counter[str]] = defaultdict(Counter)
    grade_by_timing: dict[str, Counter[str]] = defaultdict(Counter)
    better_alt_gain: list[float] = []
    all_tied = 0
    for b in buys:
        rows = b["candidates"]
        ch = next(c for c in rows if c["chosen"])
        best_r = max(c["r"] for c in rows)
        worst_r = min(c["r"] for c in rows)
        if best_r - worst_r <= _TIE_R:
            all_tied += 1
        # rank = how many candidates beat the chosen one by more than a tie
        pos = sum(1 for c in rows if c["r"] > ch["r"] + _TIE_R)
        g = ("BEST", "GOOD", "ACCEPTABLE", "SUBOPTIMAL", "SUBOPTIMAL")[min(pos, 4)]
        grade[g] += 1
        grade_by_off[ch["k_off"]][g] += 1
        grade_by_timing[ch["timing_class"]][g] += 1
        if pos > 0:
            better_alt_gain.append(best_r - ch["r"])

    # ------------------------- §12/§13 chronological model, no shuffling
    x_all = np.asarray([[c["features"][f] for f in FEATURES] for c in all_c], dtype=float)
    y_all = np.asarray(labels, dtype=float)
    per_buy = [len(b["candidates"]) for b in buys]
    starts = np.cumsum([0] + per_buy)
    n_buy = len(buys)
    cut_tr, cut_va, cut_oos = int(0.50 * n_buy), int(0.70 * n_buy), int(0.85 * n_buy)

    def rows_for(a: int, b: int) -> np.ndarray:
        return np.arange(starts[a], starts[b])

    idx_tr, idx_va, idx_oos = (rows_for(0, cut_tr), rows_for(cut_tr, cut_va),
                               rows_for(cut_va, cut_oos))
    mu, sd = x_all[idx_tr].mean(axis=0), x_all[idx_tr].std(axis=0)
    w = logistic(standardise(x_all[idx_tr], mu, sd), y_all[idx_tr])
    model = {
        "train_auc": auc(list(predict(w, standardise(x_all[idx_tr], mu, sd))), list(y_all[idx_tr])),
        "validation_auc": auc(list(predict(w, standardise(x_all[idx_va], mu, sd))), list(y_all[idx_va])),
        "out_of_sample_auc": auc(list(predict(w, standardise(x_all[idx_oos], mu, sd))), list(y_all[idx_oos])),
        "coefficients": {f: round(float(w[n + 1]), 4) for n, f in enumerate(FEATURES)},
        "split_buys": {"train": cut_tr, "validation": cut_va - cut_tr,
                       "out_of_sample": cut_oos - cut_va, "walk_forward": n_buy - cut_oos},
    }
    # walk-forward: expanding train, next block tested, never the reverse
    folds = []
    for k in range(4):
        tr_end = cut_oos + int((n_buy - cut_oos) * k / 4)
        te_end = cut_oos + int((n_buy - cut_oos) * (k + 1) / 4)
        if te_end - tr_end < 20 or tr_end < 50:
            continue
        i_tr, i_te = rows_for(0, tr_end), rows_for(tr_end, te_end)
        m2, s2 = x_all[i_tr].mean(axis=0), x_all[i_tr].std(axis=0)
        w2 = logistic(standardise(x_all[i_tr], m2, s2), y_all[i_tr])
        folds.append({
            "fold": k + 1, "train_buys": tr_end, "test_rows": int(len(i_te)),
            "auc": auc(list(predict(w2, standardise(x_all[i_te], m2, s2))), list(y_all[i_te])),
        })
    model["walk_forward"] = folds

    # ------------------------- §14 current selector vs research selector
    def selector_stats(pick: list[dict]) -> dict:
        return block(pick) | {"median_entry_premium": med([c["entry"] for c in pick])}

    oos_buys = buys[cut_va:]
    cur_pick, res_pick, rot_pick, atm_pick, cheap_pick, deep_pick = [], [], [], [], [], []
    for n, b in enumerate(oos_buys, start=cut_va):
        rows = b["candidates"]
        xb = standardise(
            np.asarray([[c["features"][f] for f in FEATURES] for c in rows], dtype=float),
            mu, sd)
        p = predict(w, xb)
        cur_pick.append(next(c for c in rows if c["chosen"]))
        res_pick.append(rows[int(np.argmax(p))])
        # Three fixed rules a five-line selector could implement, as the honest
        # bar the research model has to clear: rotate, always ATM, always the
        # cheapest leg, always the highest-delta leg.
        rot_pick.append(rows[n % len(rows)])
        atm_pick.append(min(rows, key=lambda c: abs(c["k_off"])))
        cheap_pick.append(min(rows, key=lambda c: c["entry"]))
        deep_pick.append(max(rows, key=lambda c: c["delta"]))
    selector = {
        "note": "out-of-sample + walk-forward segment only; the model never saw these bars",
        "current_selector": selector_stats(cur_pick),
        "research_selector": selector_stats(res_pick),
        "baseline_rotating": selector_stats(rot_pick),
        "baseline_always_atm": selector_stats(atm_pick),
        "baseline_cheapest_leg": selector_stats(cheap_pick),
        "baseline_highest_delta": selector_stats(deep_pick),
        "agreement_pct": round(100.0 * sum(
            1 for a, b2 in zip(cur_pick, res_pick) if a["symbol"] == b2["symbol"]
        ) / max(1, len(cur_pick)), 1),
    }

    # ------------------------------------------------- §10 worked examples
    examples = []
    for b in buys:
        ch = next(c for c in b["candidates"] if c["chosen"])
        alt = max(b["candidates"], key=lambda c: c["r"])
        if ch["hit"] == "STOP" and alt["target_first"] and len(examples) < 12:
            examples.append({
                "COUNTERFACTUAL_RESEARCH_ONLY": True,
                "ts": b["ts"], "underlying": round(b["spot"], 2), "side": b["side"],
                "regime": b["regime"], "modelled_iv": b["sigma"],
                "actual": {"strike": ch["strike"], "steps_from_atm": ch["k_off"],
                           "entry": ch["entry"], "delta": round(ch["delta"], 3),
                           "mfe_points": round(ch["mfe_r"] * (ch["entry"] - ch["stop"]), 2),
                           "result": ch["hit"]},
                "best_alternative": {"strike": alt["strike"], "steps_from_atm": alt["k_off"],
                                     "entry": alt["entry"], "delta": round(alt["delta"], 3),
                                     "mfe_points": round(alt["mfe_r"] * (alt["entry"] - alt["stop"]), 2),
                                     "result": alt["hit"],
                                     "bars_to_target": alt["bars_to_target"]},
            })

    report = {
        "instrument": args.instrument,
        "premium_basis": "MODELLED — Black-Scholes on trailing realised vol, flat "
                         "surface, IV frozen after entry, no OI/volume/spread data",
        "bars_replayed": n_bars,
        "buys": len(buys),
        "horizon_bars": horizon,
        "assumed_dte_days": args.dte,
        "ladder_half_width_steps": args.steps,
        "candidate_span_pct_of_spot": med([
            100.0 * args.steps * step / b["spot"] for b in buys]),
        "median_modelled_iv": med([b["sigma"] for b in buys]),
        "engine_selector": "0.6*delta_fit(target 0.52) + 0.4*min(1, oi/6000) — "
                           "decision.py:_select_option; with no OI data the "
                           "liquidity term is constant, so the choice is pure delta fit",
        "chosen_leg": block(chosen_rows),
        "chosen_steps_from_atm": {str(k): v for k, v in sorted(
            Counter(c["k_off"] for c in chosen_rows).items())},
        "by_steps_from_atm": {str(k): block(v) for k, v in sorted(by_off.items())},
        "delta_bands": delta_bands,
        "moneyness_by_side": moneyness,
        "feature_correlations": corr,
        "premium_expansion_before_entry": {
            "median_ratio_premium_pct_per_underlying_pct": med(exp_ratios),
            "median_underlying_move_pct_before_entry": med(
                [c["und_move_pct_before"] for c in chosen_rows]),
            "median_premium_move_pct_before_entry": med(
                [c["prem_move_pct_before"] for c in chosen_rows]),
            "note": "both moves measured over the 30 bars before entry, signed in "
                    "the trade's own direction",
            "already_expanded_share_pct": round(
                100.0 * len(already_expanded) / len(buys), 1),
            "outcome_by_pre_entry_expansion": exp_buckets,
        },
        "option_underlying_divergence": {
            "definition": f"chosen leg already +{_EXPANDED_PCT:.0f}% or more before "
                          "entry while the underlying still went on to travel >=1 ATR "
                          "in the trade's direction",
            "n": len(divergence),
            "share_of_buys_pct": round(100.0 * len(divergence) / len(buys), 1),
            "outcome": block(div_rows) if div_rows else {},
            "outcome_is_conditioned_on_the_future": "the >=1 ATR leg of this filter "
                "uses forward data, so these stats are NOT an edge and must not be "
                "read as one — only the frequency is a finding. The future-independent "
                "version is outcome_by_pre_entry_expansion above.",
        },
        "strike_grade": {
            "note": "hindsight rank of the chosen leg among the 5 candidates by "
                    "realised R — counterfactual, not a claim the engine could know it",
            "counts": dict(grade),
            "pct": {k: round(100.0 * v / len(buys), 1) for k, v in grade.items()},
            "tie_tolerance_r": _TIE_R,
            "buys_where_every_candidate_tied_pct": round(
                100.0 * all_tied / len(buys), 1),
            "median_r_left_on_the_table_when_not_best": med(better_alt_gain),
            "by_chosen_steps_from_atm": {str(k): dict(v) for k, v in sorted(grade_by_off.items())},
            "by_phase2_timing_class": {
                k: dict(v) | {
                    "poor_strike_pct": round(100.0 * (v["ACCEPTABLE"] + v["SUBOPTIMAL"])
                                             / max(1, sum(v.values())), 1),
                }
                for k, v in sorted(grade_by_timing.items())
            },
        },
        "phase2_timing_classes": dict(Counter(c["timing_class"] for c in chosen_rows)),
        "entry_quality_model": model,
        "selector_comparison": selector,
        "counterfactual_examples": examples,
    }
    out = json.dumps(report, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out)
    print(out)


if __name__ == "__main__":
    main()
