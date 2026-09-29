"""Phase 8 Part C/P — while a trade is in profit, hold or protect? RESEARCH ONLY.

This is the part of the spec most easily faked, so the discipline matters more than
the numbers. The question is asked one quote at a time: standing at quote *k* of a
trade that is currently up, **using only quotes 0..k**, what happened next?

Rules that make it a study rather than a curve fit:

* features are strictly causal. Extension, premium momentum, time in trade,
  give-back so far and time since the running peak are all computed from quotes at
  or before *k*. No future price is ever a feature — that is the one mistake that
  would make every number here worthless.
* the outcome is measured over the remainder of the recorded path: did the premium
  add another quarter risk unit (CONTINUED), or fall back to break-even or worse
  (REVERSED), or neither by the horizon.
* the resulting frequencies are **in-sample conditional frequencies, not
  probabilities**. With one session there is no holdout, so nothing here may be
  called a probability of anything; the report says so in the same table.

Part P's question — can the system tell "still running" from "about to give it back"
— is answered by whether the states separate at all, and by how thin the cells are.
"""
from __future__ import annotations

from collections import defaultdict

from .findings import label, note

CONTINUE_R = 0.25      # another quarter of a risk unit is "the move continued"
REVERSE_TO_R = 0.0     # back to break-even or worse is "it reversed"
MOMENTUM_LOOK = 3      # quotes used for the premium-momentum sign


def _state(ext_r: float, momentum: float, giveback_r: float,
           quotes_since_peak: int) -> str:
    """A coarse, deliberately hand-readable state label. Four buckets on extension
    × momentum sign, because finer buckets on one day would be single-digit cells."""
    band = ("EXT_LT_0.5R" if ext_r < 0.5 else
            "EXT_0.5_1R" if ext_r < 1.0 else
            "EXT_1_2R" if ext_r < 2.0 else "EXT_GE_2R")
    mom = "MOM_UP" if momentum > 0 else "MOM_FLAT" if momentum == 0 else "MOM_DOWN"
    age = "AT_PEAK" if quotes_since_peak == 0 else "OFF_PEAK"
    return f"{band} · {mom} · {age}"


def observations(ev, fill) -> list[dict]:
    """One row per in-profit quote of one trade, features causal, outcome forward."""
    if ev.path is None or not fill.entered:
        return []
    quotes = [(ts, px) for ts, px in ev.path.quotes if ts >= fill.entry_ts]
    if len(quotes) < 4:
        return []
    risk = max(0.01, fill.entry - fill.stop)
    out: list[dict] = []
    peak = fill.entry
    peak_k = 0
    for k, (ts, px) in enumerate(quotes):
        if px > peak:
            peak, peak_k = px, k
        ext_r = (px - fill.entry) / risk
        if ext_r <= 0:
            continue                      # only asked of a trade currently in profit
        past = quotes[max(0, k - MOMENTUM_LOOK + 1) : k + 1]
        momentum = round(past[-1][1] - past[0][1], 4)
        giveback_r = (peak - px) / risk
        forward = [p for _, p in quotes[k + 1 :]]
        if not forward:
            continue
        continued = max(forward) >= px + CONTINUE_R * risk
        reversed_ = min(forward) <= fill.entry + REVERSE_TO_R * risk
        out.append({
            "instrument": ev.instrument,
            "trade_id": f"{ev.instrument}:{ev.symbol}:{ev.ts}",
            "expiry_class": ev.expiry_class,
            "side": ev.side,
            "quote_index": k,
            "minutes_in": round((ts - fill.entry_ts) / 60.0, 1),
            "extension_r": round(ext_r, 3),
            "premium_momentum": momentum,
            "giveback_so_far_r": round(giveback_r, 3),
            "quotes_since_peak": k - peak_k,
            "state": _state(ext_r, momentum, giveback_r, k - peak_k),
            "continued": continued,
            "reversed_to_breakeven": reversed_,
            "remaining_mfe_r": round((max(forward) - px) / risk, 3),
            "final_r": round(fill.r, 3),
        })
    return out


def _block(rows: list[dict], sessions: int) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0, "label": label(0, sessions)}
    cont = sum(1 for r in rows if r["continued"])
    rev = sum(1 for r in rows if r["reversed_to_breakeven"])
    trades = len({r["trade_id"] for r in rows})
    return {
        "n_observations": n,
        "n": n,
        "continued_pct": round(100.0 * cont / n, 1),
        "reversed_to_breakeven_pct": round(100.0 * rev / n, 1),
        "median_remaining_mfe_r": _median([r["remaining_mfe_r"] for r in rows]),
        "median_giveback_so_far_r": _median([r["giveback_so_far_r"] for r in rows]),
        "trades_contributing": trades,
        "label": label(n, sessions),
        "note": note(n, sessions),
    }


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    mid = len(ys) // 2
    return round(ys[mid] if len(ys) % 2 else (ys[mid - 1] + ys[mid]) / 2.0, 3)


def study(rows: list[dict], sessions: int) -> dict:
    by_state: dict[str, list[dict]] = defaultdict(list)
    by_expiry: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_state[r["state"]].append(r)
        by_expiry[str(r["expiry_class"])].append(r)

    states = {k: _block(v, sessions) for k, v in sorted(by_state.items())}
    usable = {k: v for k, v in states.items() if v.get("n", 0) >= 20}
    spread_pp = None
    if len(usable) >= 2:
        vals = [v["continued_pct"] for v in usable.values()]
        spread_pp = round(max(vals) - min(vals), 1)

    return {
        "definitions": {
            "continued": f"premium later exceeded this quote by >= {CONTINUE_R}R",
            "reversed": "premium later fell to entry or below",
            "features": "extension, premium momentum over the last "
                        f"{MOMENTUM_LOOK} quotes, give-back so far, quotes since the "
                        "running peak — all computed from quotes at or before the "
                        "observation; no future price is used as a feature",
            "warning": "these are in-sample conditional frequencies on one session, "
                       "NOT probabilities. There is no holdout, so a state that looks "
                       "informative here may simply be describing this day's path",
        },
        "overall": _block(rows, sessions),
        "by_state": states,
        "by_expiry_class": {k: _block(v, sessions) for k, v in sorted(by_expiry.items())},
        "separation": {
            "states_with_20_plus_observations": len(usable),
            "continuation_spread_pp": spread_pp,
            "reading": (
                "no state carries 20+ observations, so the states do not separate "
                "measurably on this sample"
                if len(usable) < 2 else
                f"continuation frequency varies by {spread_pp}pp across states with "
                f"20+ observations — a candidate signal to test, not a probability"),
        },
    }
