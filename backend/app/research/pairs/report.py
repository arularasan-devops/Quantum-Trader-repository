"""Status and retest for the frozen pair, plus Test B basis coverage.

The retest re-runs nothing: the trades were produced live by the frozen rule, so
this only measures them. It refuses to publish a verdict until the pre-declared
evidence bar in :mod:`app.research.pairs.spec` is met -- that refusal is the
point, because the first result failed on sample size and on an unmeasured
spread, and both are only fixed by waiting.
"""
from __future__ import annotations

import statistics

from app.config import settings
from app.research.pairs import spec as pair_spec
from app.research.pairs import store
from app.research.pairs.capture import MEASURED

REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"
RESEARCH_LEAD = "RESEARCH_LEAD"
REJECTED = "REJECTED"


def _pf(nets: list[float]) -> float | None:
    wins = sum(n for n in nets if n > 0)
    losses = -sum(n for n in nets if n < 0)
    if losses <= 0:
        return None if wins <= 0 else float("inf")
    return wins / losses


def _max_drawdown(nets: list[float]) -> float:
    peak = run = 0.0
    worst = 0.0
    for n in nets:
        run += n
        peak = max(peak, run)
        worst = min(worst, run - peak)
    return worst


def metrics(trades: list[dict]) -> dict:
    nets = [float(t["net"]) for t in trades]
    gross = [float(t["gross"]) for t in trades]
    costs = [float(t["cost"]) for t in trades]
    if not trades:
        return {"trades": 0}
    converged = sum(1 for t in trades if t["reason"] == "CONVERGED")
    stopped = sum(1 for t in trades if t["reason"] in ("DIVERGED", "SESSION_END",
                                                       "MAX_HOLD"))
    beat_cost = sum(1 for t in trades if abs(float(t["gross"])) > float(t["cost"]))
    return {
        "trades": len(trades),
        "sessions": len({t["session"] for t in trades}),
        "gross_mean": statistics.fmean(gross),
        "gross_median": statistics.median(gross),
        "cost_mean": statistics.fmean(costs),
        "cost_median": statistics.median(costs),
        "cost_to_convergence": (statistics.fmean(costs)
                                / statistics.fmean([abs(g) for g in gross])
                                if any(gross) else None),
        "net_total": sum(nets),
        "net_expectancy": statistics.fmean(nets),
        "profit_factor": _pf(nets),
        "max_drawdown": _max_drawdown(nets),
        "avg_hold_obs": statistics.fmean([int(t["held_obs"] or 0) for t in trades]),
        "pct_converged": 100.0 * converged / len(trades),
        "pct_stopped": 100.0 * stopped / len(trades),
        "convergence_beat_cost": beat_cost,
        "convergence_failed_cost": len(trades) - beat_cost,
        "hedged_net": sum(float(t["hedged_pnl"] or 0.0) for t in trades),
        "residual_net": sum(float(t["residual_pnl"] or 0.0) for t in trades),
    }


def _split(trades: list[dict]) -> dict[str, list[dict]]:
    """Chronological 60/20/20 by SESSION, never by trade index."""
    sessions = sorted({t["session"] for t in trades})
    n = len(sessions)
    dev_end = int(n * 0.60)
    val_end = dev_end + int(n * 0.20)
    dev = set(sessions[:dev_end])
    val = set(sessions[dev_end:val_end])
    hold = set(sessions[val_end:])
    return {
        "development": [t for t in trades if t["session"] in dev],
        "validation": [t for t in trades if t["session"] in val],
        "holdout": [t for t in trades if t["session"] in hold],
    }


def _walk_forward(trades: list[dict], folds: int = 5) -> list[dict]:
    sessions = sorted({t["session"] for t in trades})
    if len(sessions) < folds:
        return []
    size = len(sessions) // folds
    out = []
    for i in range(folds):
        lo = i * size
        hi = (i + 1) * size if i < folds - 1 else len(sessions)
        keep = set(sessions[lo:hi])
        fold = [t for t in trades if t["session"] in keep]
        nets = [float(t["net"]) for t in fold]
        out.append({"fold": i + 1, "trades": len(fold),
                    "net": sum(nets),
                    "expectancy": statistics.fmean(nets) if nets else None})
    return out


def _cost_stress(trades: list[dict], multiples=(1.0, 1.5, 2.0)) -> dict:
    out = {}
    for m in multiples:
        nets = [float(t["gross"]) - m * float(t["cost"]) for t in trades]
        out[f"{m}x"] = {"net": sum(nets),
                        "expectancy": statistics.fmean(nets) if nets else None}
    return out


def _slippage_stress(trades: list[dict], points=(1.0, 2.0, 3.0)) -> dict:
    """Extra rupees per side per unit on BOTH legs, on top of the recorded cost."""
    out = {}
    for p in points:
        nets = []
        for t in trades:
            extra = 2.0 * p * (int(t["qty_a"]) + int(t["qty_b"]))
            nets.append(float(t["net"]) - extra)
        out[f"{p}pt"] = {"net": sum(nets),
                         "expectancy": statistics.fmean(nets) if nets else None}
    return out


def _outliers(trades: list[dict]) -> dict:
    ordered = sorted(trades, key=lambda t: float(t["net"]), reverse=True)
    out = {}
    for pct in (1, 5):
        drop = max(1, int(len(ordered) * pct / 100)) if ordered else 0
        rest = ordered[drop:]
        nets = [float(t["net"]) for t in rest]
        out[f"ex_top_{pct}pct"] = {
            "trades": len(rest),
            "net": sum(nets),
            "expectancy": statistics.fmean(nets) if nets else None,
        }
    return out


def retest() -> dict:
    """Measure the frozen configuration on everything captured so far."""
    fp = pair_spec.fingerprint()
    all_trades = store.closed_trades()
    on_spec = [t for t in all_trades if t["fingerprint"] == fp]
    off_spec = len(all_trades) - len(on_spec)
    measured = [t for t in on_spec if t["cost_status"] == MEASURED]
    measured_pct = (100.0 * len(measured) / len(on_spec)) if on_spec else 0.0
    sessions = len({t["session"] for t in measured})

    gate = {
        "trades": len(measured),
        "trades_required": pair_spec.MIN_TRADES,
        "sessions": sessions,
        "sessions_required": pair_spec.MIN_SESSIONS,
        "measured_cost_pct": round(measured_pct, 2),
        "measured_cost_pct_required": pair_spec.MIN_MEASURED_COST_PCT,
    }
    enough = (len(measured) >= pair_spec.MIN_TRADES
              and sessions >= pair_spec.MIN_SESSIONS
              and measured_pct >= pair_spec.MIN_MEASURED_COST_PCT)

    result = {
        "spec": pair_spec.as_dict(),
        "off_spec_trades_excluded": off_spec,
        "gate": gate,
        "gate_met": enough,
        # Reported at every sample size for transparency; NOT a verdict.
        "measured_sample": metrics(measured),
        "all_sample_including_unmeasured": metrics(on_spec),
    }
    if not enough:
        result["verdict"] = REQUIRES_MORE_DATA
        result["verdict_reason"] = (
            "pre-declared evidence bar not met; no verdict is computed and no "
            "promotion is possible until it is")
        return result

    splits = _split(measured)
    result["splits"] = {k: metrics(v) for k, v in splits.items()}
    result["walk_forward"] = _walk_forward(measured)
    result["cost_stress"] = _cost_stress(measured)
    result["slippage_stress"] = _slippage_stress(measured)
    result["outliers"] = _outliers(measured)

    holdout = splits["holdout"]
    folds = result["walk_forward"]
    positive_folds = sum(1 for f in folds if (f["net"] or 0) > 0)
    checks = {
        "positive_net_expectancy": result["measured_sample"]["net_expectancy"] > 0,
        "positive_holdout": bool(holdout) and metrics(holdout)["net_total"] > 0,
        "walk_forward_majority": bool(folds) and positive_folds > len(folds) / 2,
        "survives_cost_stress": all(
            (v["net"] or 0) > 0 for v in result["cost_stress"].values()),
        "survives_slippage_stress": all(
            (v["net"] or 0) > 0 for v in result["slippage_stress"].values()),
        "not_outlier_dependent": all(
            (v["expectancy"] or 0) > 0 for v in result["outliers"].values()),
        "sufficient_sample": True,
    }
    result["hard_pass_checks"] = checks
    result["verdict"] = RESEARCH_LEAD if all(checks.values()) else REJECTED
    result["verdict_reason"] = (
        "all pre-declared hard pass criteria met on measured execution costs"
        if all(checks.values())
        else "failed: " + ", ".join(k for k, v in checks.items() if not v))
    return result


# ------------------------------------------------------------------ Test B
def basis_rows() -> list[dict]:
    """Near vs next futures basis at instants where BOTH were quoted.

    A row exists only when two DIFFERENT expiries of the same instrument were
    captured at the same instant. No synthetic second contract, no midpoint: a
    two-sided row also records the executable basis (sell near at bid, buy next
    at ask, and the reverse).
    """
    out: list[dict] = []
    for (instrument, ts), books in store.contract_snapshots().items():
        dated = [b for b in books if b.get("expiry")]
        expiries = sorted({b["expiry"] for b in dated})
        if len(expiries) < 2:
            continue
        near = min((b for b in dated if b["expiry"] == expiries[0]),
                   key=lambda b: b["seq"])
        nxt = min((b for b in dated if b["expiry"] == expiries[1]),
                  key=lambda b: b["seq"])
        near_px, next_px = near.get("ltp"), nxt.get("ltp")
        if not near_px or not next_px:
            continue
        two_sided = all(b.get("bid") and b.get("ask") for b in (near, nxt))
        raw = float(next_px) - float(near_px)
        out.append({
            "instrument": instrument,
            "captured_ts": ts,
            "near_symbol": near["symbol"], "next_symbol": nxt["symbol"],
            "near_expiry": near["expiry"], "next_expiry": nxt["expiry"],
            "near_dte": near.get("dte"), "next_dte": nxt.get("dte"),
            "near_price": float(near_px), "next_price": float(next_px),
            "raw_basis": raw,
            "basis_pct": 100.0 * raw / float(near_px),
            "near_bid": near.get("bid"), "near_ask": near.get("ask"),
            "next_bid": nxt.get("bid"), "next_ask": nxt.get("ask"),
            "spread_status": MEASURED if two_sided else "UNMEASURED",
        })
    return sorted(out, key=lambda r: (r["instrument"], r["captured_ts"]))


def basis_status() -> dict:
    rows = basis_rows()
    two_sided = [r for r in rows if r["spread_status"] == MEASURED]
    sessions = {r["captured_ts"] // 86400 for r in rows}
    pcts = sorted(r["basis_pct"] for r in rows)

    def pctile(p: float) -> float | None:
        if not pcts:
            return None
        idx = min(len(pcts) - 1, max(0, int(round(p / 100.0 * (len(pcts) - 1)))))
        return pcts[idx]

    enough = (len(two_sided) >= pair_spec.BASIS_MIN_PAIRED_SNAPSHOTS
              and len(sessions) >= pair_spec.BASIS_MIN_SESSIONS)
    return {
        "paired_snapshots": len(rows),
        "two_sided_snapshots": len(two_sided),
        "distinct_days": len(sessions),
        "required_two_sided": pair_spec.BASIS_MIN_PAIRED_SNAPSHOTS,
        "required_days": pair_spec.BASIS_MIN_SESSIONS,
        "instruments": sorted({r["instrument"] for r in rows}),
        "basis_pct_percentiles": {
            "p10": pctile(10), "p50": pctile(50), "p90": pctile(90),
        },
        "measurable": enough,
        "verdict": REQUIRES_MORE_DATA,
        "verdict_reason": (
            "capture is running; the basis question stays unanswered until the "
            "pre-declared coverage exists" if not enough else
            "coverage reached -- a pre-registered Test B may now be specified"),
    }


def leg_coverage() -> dict:
    """Which legs the running session is actually feeding.

    Both legs must be in the live universe for a single observation to exist. A
    session running only one of them produces no pair rows at all, which reads
    identically to "the market was quiet" unless it is stated.
    """
    seen = store.leg_coverage()
    missing = [leg for leg in (pair_spec.LEG_A, pair_spec.LEG_B) if leg not in seen]
    return {
        "legs": {leg: seen.get(leg, {"rows": 0, "sessions": 0,
                                     "last_bar_ts": 0, "two_sided_rows": 0,
                                     "two_sided_pct": 0.0})
                 for leg in (pair_spec.LEG_A, pair_spec.LEG_B)},
        "missing_legs": missing,
        "capture_possible": not missing,
        "note": (
            "both legs are being fed" if not missing else
            f"no observation can exist while {', '.join(missing)} is absent from "
            "the running universe (QT_INSTRUMENTS / day-movers pick)"),
    }


def status() -> dict:
    """One read-only summary for the API and the CLI."""
    return {
        "enabled": settings.pair_capture_enabled,
        "next_expiry_capture": settings.pair_next_expiry_capture,
        "store_path": store.path(),
        "counts": store.counts(),
        "coverage": leg_coverage(),
        # Why coverage is what it is. A leg is only measurable on a sample whose
        # bar is current, so low measured coverage has three distinguishable
        # causes -- few samples, samples landing on a bar already recorded, or a
        # bar too old to carry the book -- and these counters separate them.
        "sampling": store.leg_diag(),
        "pair": retest(),
        "basis": basis_status(),
    }
