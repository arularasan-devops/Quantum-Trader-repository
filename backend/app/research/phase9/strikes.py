"""Phase 9 §4-5 — the strike ladder at the signal timestamp. RESEARCH ONLY.

The central discipline of this module is the separation the spec demands: an
entry-time score may only use what the chain showed at the signal, and what each
candidate went on to do is computed afterwards, into a different block. Mixing them
produces the most common research error there is — "the best strike" chosen with
hindsight, which is unavailable at the moment a trade has to be taken.

Two design choices are worth stating because they decide what the numbers mean:

1. **Candidate quality is a rank inside its own ladder, not an absolute score.**
   Every component is the percentile rank of that leg among the legs quoted at the
   same instant on the same side. This avoids inventing absolute thresholds (the
   spec forbids choosing them here) and answers the question actually being asked:
   was something *better available at that moment*? A composite is reported as an
   equal-weighted mean of the components and is labelled arbitrary, because any
   weighting learned from two sessions would be fitted to those two sessions.
2. **The ladder is the recorded ladder.** ATM is the quoted strike nearest spot and
   distance is measured in ladder steps, not rupees, so an instrument that records
   only 4 strikes (GOLD here) is handled honestly: it simply has fewer candidates,
   and ``candidates_available`` says so rather than the study silently comparing
   against strikes that were never recorded.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from app.research.phase7 import paths, policies
from app.research.phase7.dataset import ChainSeries, ist
from app.research.phase8 import spread
from app.research.phase8.contract import DEGENERATE_IV, PEGGED_DELTA

from .findings9 import band_label, label9

# How far along the recorded ladder a neighbour may sit and still be a candidate.
LADDER_STEPS = 3

# Premium bands, in rupees. The lowest band exists because the production floor
# (``settings.auto_trade_min_premium``) sits at 20 and the question "what would a
# sub-floor leg have done?" is answerable from the recorded book.
PREMIUM_BANDS = ((0.0, 20.0), (20.0, 50.0), (50.0, 200.0), (200.0, 1e12))

# Past-only premium expansion is measured over this many prior snapshots (~60s each).
EXPANSION_LOOKBACK = 5

# Components that make up the entry-time composite. Each is (field, higher_is_better).
COMPONENTS = (
    ("spread_pct_of_premium", False),
    ("oi", True),
    ("volume", True),
    ("room_ratio", False),
    ("past_expansion_pct", False),
)


def premium_band(premium: float) -> str:
    for lo, hi in PREMIUM_BANDS:
        if lo <= premium < hi:
            return band_label(lo, hi)
    return band_label(*PREMIUM_BANDS[-1])


def _num(leg: dict, key: str) -> float | None:
    try:
        return float(leg[key])
    except (KeyError, TypeError, ValueError):
        return None


def _book(leg: dict) -> tuple[float | None, float | None]:
    bid, ask = _num(leg, "bid"), _num(leg, "ask")
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None, None
    return bid, ask


def _past_expansion(series: ChainSeries, symbol: str, when: int,
                    lookback: int = EXPANSION_LOOKBACK) -> float | None:
    """Premium change over the snapshots *before* ``when``, in percent.

    Strictly past-only: this is what a decision at ``when`` could have seen. A leg
    that has already run 40% before the signal is a different proposition from one
    that has not, and that distinction is available at entry time.
    """
    i = series.index_at(when)
    if i is None or i == 0:
        return None
    j = max(0, i - lookback)
    then = series.legs[j].get(symbol)
    now = series.legs[i].get(symbol)
    if not isinstance(then, dict) or not isinstance(now, dict):
        return None
    a, b = _num(then, "premium"), _num(now, "premium")
    if not a or not b or a <= 0:
        return None
    return round(100.0 * (b - a) / a, 2)


def _ranks(values: list[float | None], higher_is_better: bool) -> list[float | None]:
    """Percentile rank of each value among the non-missing ones, 0-100.

    A missing value ranks nowhere — it stays None rather than being imputed to the
    median, because an absent OI and a low OI are different facts.
    """
    known = sorted(v for v in values if v is not None)
    if len(known) < 2:
        return [None if v is None else 50.0 for v in values]
    out: list[float | None] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        below = sum(1 for k in known if k < v)
        equal = sum(1 for k in known if k == v)
        pct = 100.0 * (below + 0.5 * equal) / len(known)
        out.append(round(pct if higher_is_better else 100.0 - pct, 1))
    return out


def ladder(series: ChainSeries, ev, steps: int = LADDER_STEPS) -> list[dict]:
    """Entry-time candidate rows for ``ev``: the same-side legs around ATM.

    Nothing in a row depends on anything after ``ev.ts``.
    """
    snap = series.at(ev.ts)
    if not snap:
        return []
    same_side = [(sym, leg) for sym, leg in snap.items()
                 if str(leg.get("option_type")) == ev.side
                 and (_num(leg, "premium") or 0.0) > 0
                 and _num(leg, "strike") is not None]
    if not same_side:
        return []
    same_side.sort(key=lambda kv: float(kv[1]["strike"]))
    strikes = [float(leg["strike"]) for _, leg in same_side]
    atm_i = min(range(len(strikes)), key=lambda i: abs(strikes[i] - ev.spot))

    risk_frac = max(0.01, ev.entry - ev.stop) / ev.entry
    rr = (ev.target1 - ev.entry) / max(0.01, ev.entry - ev.stop)
    available_move = paths.expected_move(ev.atr) if ev.atr else None
    age = series.age_at(ev.ts)

    rows: list[dict] = []
    lo, hi = max(0, atm_i - steps), min(len(same_side) - 1, atm_i + steps)
    for i in range(lo, hi + 1):
        sym, leg = same_side[i]
        premium = _num(leg, "premium") or 0.0
        bid, ask = _book(leg)
        delta = _num(leg, "delta")
        iv = _num(leg, "iv")
        degenerate = bool(iv is not None and delta is not None
                          and iv <= DEGENERATE_IV and abs(delta) >= PEGGED_DELTA)
        risk = premium * risk_frac
        target = premium + risk * rr
        required = None if not delta else (target - premium) / max(1e-9, abs(delta))
        rows.append({
            "symbol": sym,
            "strike": float(leg["strike"]),
            "side": ev.side,
            "steps_from_atm": i - atm_i,
            "is_selected": sym == ev.symbol,
            "premium": round(premium, 2),
            "premium_band": premium_band(premium),
            "bid": bid,
            "ask": ask,
            "spread": None if bid is None else round(ask - bid, 2),
            "spread_pct_of_premium": (
                None if bid is None else round(100.0 * (ask - bid) / max(0.01, premium), 2)),
            # The book measured against the risk the engine itself would have taken
            # on this leg: above 100% the spread is wider than the stop distance.
            "spread_share_of_risk_pct": (
                None if bid is None else round(100.0 * (ask - bid) / max(0.01, risk), 1)),
            "delta": delta,
            "iv": iv,
            "oi": _num(leg, "oi"),
            "volume": _num(leg, "volume"),
            "greeks_usable": not degenerate,
            "moneyness_pct": (
                round(100.0 * ((ev.spot - float(leg["strike"])) / ev.spot
                               if ev.side == "CE"
                               else (float(leg["strike"]) - ev.spot) / ev.spot), 2)
                if ev.spot else None),
            "required_underlying_move": None if required is None else round(required, 2),
            "available_move_atr": None if available_move is None else round(available_move, 2),
            "room_ratio": (None if required is None or not available_move
                           else round(required / available_move, 3)),
            "past_expansion_pct": _past_expansion(series, sym, ev.ts),
            "quote_age_sec": age,
        })

    for field, higher in COMPONENTS:
        for row, rank in zip(rows, _ranks([r[field] for r in rows], higher),
                             strict=True):
            row[f"rank_{field}"] = rank
    for row in rows:
        parts = [row[f"rank_{f}"] for f, _ in COMPONENTS if row[f"rank_{f}"] is not None]
        row["entry_time_composite"] = (
            round(sum(parts) / len(parts), 1) if parts else None)
        row["components_used"] = len(parts)
    return rows


def outcomes(series: ChainSeries, ev, rows: list[dict],
             horizon: int = paths.HORIZON_BARS) -> list[dict]:
    """What each candidate went on to do — computed *after* the entry-time score.

    Every candidate is given the risk fraction and R:R the engine chose for the leg
    it actually bought, so the comparison is like-for-like and no candidate benefits
    from a wider stop. The fill and the exit are Phase 7's; the cost is Phase 8's.
    """
    risk_frac = max(0.01, ev.entry - ev.stop) / ev.entry
    rr = (ev.target1 - ev.entry) / max(0.01, ev.entry - ev.stop)
    out: list[dict] = []
    for row in rows:
        premium = row["premium"]
        stop = premium * (1.0 - risk_frac)
        target = premium + (premium - stop) * rr
        cand = replace(ev, symbol=row["symbol"], strike=row["strike"],
                       entry=premium, stop=stop, target1=target,
                       leg_delta=abs(row["delta"] or 0.0), path=None)
        cand.path = paths.walk(series, row["symbol"], ev.ts, premium, stop, target,
                               horizon)
        if len(cand.path.quotes) < 3:
            out.append({**{k: row[k] for k in ("symbol", "strike", "steps_from_atm",
                                               "is_selected", "premium_band",
                                               "entry_time_composite")},
                        "measurable": False,
                        "reason": "fewer than 3 forward quotes for this leg"})
            continue
        fill = policies.simulate(cand, "CONTROL", "A_BASELINE")
        if not fill.entered:
            out.append({**{k: row[k] for k in ("symbol", "strike", "steps_from_atm",
                                               "is_selected", "premium_band",
                                               "entry_time_composite")},
                        "measurable": False, "reason": "no fill"})
            continue
        sp = spread.measure(cand, fill, series)
        cap = paths.mfe_capture(fill.r, fill.mfe_r)
        out.append({
            "symbol": row["symbol"],
            "strike": row["strike"],
            "steps_from_atm": row["steps_from_atm"],
            "is_selected": row["is_selected"],
            "premium_band": row["premium_band"],
            "entry_time_composite": row["entry_time_composite"],
            "measurable": True,
            "outcome": cand.path.outcome,
            "realised_r": round(fill.r, 3),
            "net_r": sp.get("net_r"),
            "spread_status": sp.get("spread_status"),
            "spread_cost_r": sp.get("total_spread_cost_r"),
            "mfe_r": round(fill.mfe_r, 3),
            "mae_r": round(fill.mae_r, 3),
            "mfe_capture_pct": cap.get("capture_pct"),
            "held_min": fill.held_min,
        })
    return out


def per_signal(series: ChainSeries, ev, steps: int = LADDER_STEPS,
               horizon: int = paths.HORIZON_BARS) -> dict:
    """Both blocks for one signal, kept separate on purpose."""
    rows = ladder(series, ev, steps)
    if not rows:
        return {"symbol": ev.symbol, "instrument": ev.instrument,
                "candidates_available": 0,
                "reason": "no same-side legs in the snapshot at signal time"}
    res = outcomes(series, ev, rows, horizon)
    sel = next((r for r in rows if r["is_selected"]), None)
    ranked = [r for r in rows if r["entry_time_composite"] is not None]
    ranked.sort(key=lambda r: -r["entry_time_composite"])
    better = ([r["symbol"] for r in ranked
               if sel is not None and sel["entry_time_composite"] is not None
               and r["entry_time_composite"] > sel["entry_time_composite"]]
              if sel else [])
    measurable = [r for r in res if r.get("measurable")]
    best_net = max((r for r in measurable if r["net_r"] is not None),
                   key=lambda r: r["net_r"], default=None)
    sel_res = next((r for r in measurable if r["is_selected"]), None)
    return {
        "symbol": ev.symbol,
        "instrument": ev.instrument,
        "side": ev.side,
        "ts_ist": ist(ev.ts),
        "candidates_available": len(rows),
        "selected_in_ladder": sel is not None,
        "selected_entry_time_composite": None if not sel else sel["entry_time_composite"],
        "selected_steps_from_atm": None if not sel else sel["steps_from_atm"],
        "selected_premium_band": None if not sel else sel["premium_band"],
        "selected_spread_share_of_risk_pct": (
            None if not sel else sel["spread_share_of_risk_pct"]),
        "better_at_entry_time": better,
        "better_at_entry_time_count": len(better),
        "best_at_entry_time": None if not ranked else ranked[0]["symbol"],
        "entry_time_rows": rows,
        # Hindsight block. Named so it cannot be mistaken for a decision input.
        "hindsight_best_net_symbol": None if not best_net else best_net["symbol"],
        "hindsight_best_net_r": None if not best_net else best_net["net_r"],
        "selected_net_r": None if not sel_res else sel_res["net_r"],
        "outcome_rows": res,
    }


def _agg(rows: list[dict], sessions: int) -> dict:
    """Outcome aggregate for a group of candidate-outcome rows."""
    seen = [r for r in rows if r.get("measurable")]
    if not seen:
        return {"n": len(rows), "measurable": 0, "label": label9(0, sessions)}
    rs = [r["realised_r"] for r in seen]
    nets = [r["net_r"] for r in seen if r["net_r"] is not None]
    wins = [r for r in rs if r > 0]
    bad = -sum(r for r in rs if r <= 0)
    return {
        "n": len(rows),
        "measurable": len(seen),
        "target_before_stop_pct": round(100.0 * sum(
            1 for r in seen if r["outcome"] == "TARGET_FIRST") / len(seen), 1),
        "gross_expectancy_r": round(sum(rs) / len(rs), 3),
        "net_expectancy_r": round(sum(nets) / len(nets), 3) if nets else None,
        "profit_factor": round(sum(wins) / bad, 3) if bad > 0 else None,
        "median_spread_cost_r": policies.median(
            [r["spread_cost_r"] for r in seen if r["spread_cost_r"] is not None]),
        "median_mfe_r": policies.median([r["mfe_r"] for r in seen]),
        "median_mae_r": policies.median([r["mae_r"] for r in seen]),
        "label": label9(len(seen), sessions),
    }


def study(signals: list[dict], sessions: int) -> dict:
    """§4 — the ladder study across all signals, entry-time and outcome apart."""
    usable = [s for s in signals if s.get("candidates_available")]
    all_rows: list[dict] = []
    all_out: list[dict] = []
    for s in usable:
        all_rows.extend(s["entry_time_rows"])
        all_out.extend(s["outcome_rows"])

    by_band: dict[str, list[dict]] = defaultdict(list)
    by_steps: dict[str, list[dict]] = defaultdict(list)
    for r in all_out:
        by_band[r["premium_band"]].append(r)
        by_steps[f"{r['steps_from_atm']:+d}"].append(r)

    band_entry: dict[str, dict] = {}
    for band, _ in ((band_label(lo, hi), None) for lo, hi in PREMIUM_BANDS):
        legs = [r for r in all_rows if r["premium_band"] == band]
        if not legs:
            band_entry[band] = {"legs_quoted": 0}
            continue
        chosen = sum(1 for r in legs if r["is_selected"])
        band_entry[band] = {
            "legs_quoted": len(legs),
            "times_selected_by_engine": chosen,
            "selection_rate_pct": round(100.0 * chosen / len(legs), 2),
            "median_premium": policies.median([r["premium"] for r in legs]),
            "median_spread_pct_of_premium": policies.median(
                [r["spread_pct_of_premium"] for r in legs
                 if r["spread_pct_of_premium"] is not None]),
            "median_spread_share_of_risk_pct": policies.median(
                [r["spread_share_of_risk_pct"] for r in legs
                 if r["spread_share_of_risk_pct"] is not None]),
            "median_abs_delta": policies.median(
                [abs(r["delta"]) for r in legs if r["delta"] is not None]),
            "median_oi": policies.median(
                [r["oi"] for r in legs if r["oi"] is not None]),
            "median_volume": policies.median(
                [r["volume"] for r in legs if r["volume"] is not None]),
            "median_room_ratio": policies.median(
                [r["room_ratio"] for r in legs if r["room_ratio"] is not None]),
            "legs_with_no_book_pct": round(100.0 * sum(
                1 for r in legs if r["bid"] is None) / len(legs), 1),
        }

    sel_better = [s["better_at_entry_time_count"] for s in usable
                  if s.get("selected_entry_time_composite") is not None]
    sel_comp = [s["selected_entry_time_composite"] for s in usable
                if s.get("selected_entry_time_composite") is not None]
    return {
        "signals": len(signals),
        "signals_with_ladder": len(usable),
        # The counterfactual needs the CHOSEN leg inside the reconstructed ladder,
        # not merely a ladder: a signal whose leg sits outside +-3 steps of ATM has
        # no comparable neighbours here, and is counted separately rather than
        # inflating the coverage number.
        "signals_with_selected_leg_in_ladder": sum(
            1 for s in usable if s.get("selected_in_ladder")),
        "selected_leg_in_ladder_pct": round(100.0 * sum(
            1 for s in usable if s.get("selected_in_ladder")) / max(1, len(signals)), 1),
        "median_candidates_per_signal": policies.median(
            [float(s["candidates_available"]) for s in usable]),
        "ladder_steps": LADDER_STEPS,
        "entry_time_scoring": {
            "method": "percentile rank within the same-instant same-side ladder; "
                      "no absolute threshold is invented and no future information "
                      "is used",
            "components": [c[0] for c in COMPONENTS],
            "composite": "equal-weighted mean of the available component ranks — "
                         "the weighting is ARBITRARY and is not fitted, because a "
                         "weighting learned from two sessions would describe those "
                         "two sessions",
            "median_selected_composite": policies.median(sel_comp),
            "median_better_available_count": policies.median(
                [float(x) for x in sel_better]),
            "signals_where_something_scored_better_pct": round(100.0 * sum(
                1 for x in sel_better if x > 0) / max(1, len(sel_better)), 1),
        },
        "by_premium_band_entry_time": band_entry,
        "by_premium_band_outcome": {k: _agg(v, sessions)
                                    for k, v in sorted(by_band.items())},
        "by_steps_from_atm_outcome": {k: _agg(v, sessions)
                                      for k, v in sorted(by_steps.items())},
        "selected_vs_ladder_outcome": {
            "selected": _agg([r for r in all_out if r["is_selected"]], sessions),
            "neighbours": _agg([r for r in all_out if not r["is_selected"]], sessions),
            "caveat": "the neighbour aggregate is every alternative leg, so it is a "
                      "description of the ladder, NOT a policy: choosing the best "
                      "neighbour requires knowing the outcome in advance",
        },
        "hindsight_note": "any 'best' chosen on realised or net R is hindsight and "
                          "cannot be executed; it is reported only to show whether a "
                          "better contract existed at all",
    }
