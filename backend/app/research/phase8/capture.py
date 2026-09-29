"""Phase 8 Part D/E/F/G — profit capture, give-back, and where they interact.

RESEARCH ONLY. Every policy here is executed by ``phase7.policies.apply_exit``: the
Phase 8 contribution is *which* policies to run and *how to slice* the results, not
a second exit engine. The extra fixed levels and trail widths the spec asks for are
new names in the existing grammar (``B_FIXED_4PCT``, ``C_TRAIL_25``), parsed by the
Phase 7 code unchanged — so a Phase 8 policy cannot behave differently from the
Phase 7 policy of the same shape.

Two rules make the comparison honest, and both cost the flattering answer:

* every policy is priced through the recorded book with ``spread.measure``, so the
  headline is **net** expectancy, not gross. A policy that exits more often pays the
  spread more often, and fixed-percent capture is exactly that kind of policy.
* a policy that exits earlier is also charged with what it *gave up*: premature-exit
  rate is reported beside capture, because "captures more of the move" is trivially
  achievable by exiting at the first tick and would look excellent on capture alone.
"""
from __future__ import annotations

from collections import defaultdict

from app.research.phase7 import paths, policies

from . import spread
from .findings import label, note

# Baseline first; the rest are the spec's families. Names are Phase 7 grammar.
POLICIES = (
    "A_BASELINE",
    "B_FIXED_2PCT", "B_FIXED_3PCT", "B_FIXED_4PCT", "B_FIXED_5PCT", "B_FIXED_7PCT",
    "C_TRAIL_25", "C_TRAIL_30", "C_TRAIL_40", "C_TRAIL_50",
    "D_MOMENTUM_FAIL",
    "E_TIMEOUT_10", "E_STAGNATION",
    "F_HYBRID_HALF",
)

FAMILY = {"A": "BASELINE", "B": "FIXED_CAPTURE", "C": "TRAILING",
          "D": "MOMENTUM", "E": "TIME", "F": "HYBRID_PARTIAL"}


def family_of(name: str) -> str:
    return FAMILY.get(name.split("_", 1)[0], "OTHER")


def run(events: list, series_by_instrument: dict, sessions: int) -> dict:
    """Simulate every policy on every event, gross and net of the recorded book."""
    per_policy: dict[str, list[dict]] = {}
    for name in POLICIES:
        rows: list[dict] = []
        for ev in events:
            fill = policies.simulate(ev, "CONTROL", name)
            if not fill.entered:
                continue
            series = series_by_instrument[ev.instrument]
            sp = spread.measure(ev, fill, series)
            cap = paths.mfe_capture(fill.r, fill.mfe_r)
            rows.append({
                "instrument": ev.instrument,
                "side": ev.side,
                "regime": ev.regime,
                "symbol": ev.symbol,
                "entry_quality": ev.chase.get("class"),
                "expiry_class": ev.expiry_class,
                "policy": name,
                "family": family_of(name),
                "exit_reason": fill.exit_reason,
                "realised_r": round(fill.r, 3),
                "net_r": sp.get("net_r"),
                "spread_measured": bool(sp.get("measured")),
                "mfe_r": round(fill.mfe_r, 3),
                "capture_pct": cap.get("capture_pct"),
                "giveback_r": cap.get("giveback_r"),
                "held_min": fill.held_min,
                # A premature exit is one the market went on to invalidate: the
                # policy left while the premium later exceeded its exit price by a
                # quarter of a risk unit. Measured on the recorded path, so it is
                # hindsight — used ONLY to penalise early exits, never as a signal.
                "premature": _premature(ev, fill),
            })
        per_policy[name] = rows

    return {
        "policies": {name: _block(rows, sessions) for name, rows in per_policy.items()},
        "by_expiry_class": _split(per_policy, "expiry_class", sessions),
        "by_entry_quality": _split(per_policy, "entry_quality", sessions),
        "by_instrument": _split(per_policy, "instrument", sessions),
        "by_side": _split(per_policy, "side", sessions),
        "by_regime": _split(per_policy, "regime", sessions),
        "ranking_caveat": "policies are ranked on NET expectancy after the recorded "
                          "spread; a ranking on gross R rewards the policies that "
                          "trade most and is not reported as a result",
    }


def _premature(ev, fill) -> bool:
    if ev.path is None or fill.exit_reason in ("STOP", "TARGET1"):
        return False
    risk = max(0.01, fill.entry - fill.stop)
    later = [px for ts, px in ev.path.quotes if ts > fill.exit_ts]
    return bool(later and max(later) >= fill.exit + 0.25 * risk)


def _block(rows: list[dict], sessions: int) -> dict:
    if not rows:
        return {"n": 0, "label": label(0, sessions)}
    rs = [r["realised_r"] for r in rows]
    nets = [r["net_r"] for r in rows if r["net_r"] is not None]
    wins = [r for r in rs if r > 0]
    bad = -sum(r for r in rs if r <= 0)
    net_wins = [r for r in nets if r > 0]
    net_bad = -sum(r for r in nets if r <= 0)
    caps = [r["capture_pct"] for r in rows if r["capture_pct"] is not None]
    return {
        "n": len(rows),
        "gross_expectancy_r": round(sum(rs) / len(rs), 3),
        "net_expectancy_r": round(sum(nets) / len(nets), 3) if nets else None,
        "gross_profit_factor": round(sum(wins) / bad, 3) if bad > 0 else None,
        "net_profit_factor": round(sum(net_wins) / net_bad, 3) if net_bad > 0 else None,
        "win_rate_pct": round(100.0 * len(wins) / len(rs), 1),
        "median_mfe_capture_pct": policies.median(caps),
        "mean_mfe_capture_pct": round(sum(caps) / len(caps), 1) if caps else None,
        "median_giveback_r": policies.median([r["giveback_r"] for r in rows]),
        "median_held_min": policies.median([r["held_min"] for r in rows]),
        "stopped_pct": round(100.0 * sum(
            1 for r in rows if r["exit_reason"] == "STOP") / len(rows), 1),
        "target_pct": round(100.0 * sum(
            1 for r in rows if r["exit_reason"] == "TARGET1") / len(rows), 1),
        "premature_exit_pct": round(100.0 * sum(
            1 for r in rows if r["premature"]) / len(rows), 1),
        "spread_measured_pct": round(100.0 * sum(
            1 for r in rows if r["spread_measured"]) / len(rows), 1),
        "label": label(len(rows), sessions),
        "note": note(len(rows), sessions),
    }


def _split(per_policy: dict[str, list[dict]], key: str, sessions: int) -> dict:
    groups: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for name, rows in per_policy.items():
        for r in rows:
            groups[str(r.get(key))][name].append(r)
    return {g: {name: _block(rows, sessions) for name, rows in by_policy.items()}
            for g, by_policy in sorted(groups.items())}


def best_by_net(block: dict[str, dict], min_n: int = 10) -> dict | None:
    """The best policy on NET expectancy, with the margin over baseline stated.

    Returned as a *candidate*, never a recommendation: the margin is meaningless
    until it survives out-of-sample sessions, and the caller must print it as such.
    """
    ok = [(name, b) for name, b in block.items()
          if b.get("n", 0) >= min_n and b.get("net_expectancy_r") is not None]
    if not ok:
        return None
    name, b = max(ok, key=lambda kv: kv[1]["net_expectancy_r"])
    base = block.get("A_BASELINE", {})
    margin = None
    if base.get("net_expectancy_r") is not None:
        margin = round(b["net_expectancy_r"] - base["net_expectancy_r"], 3)
    return {
        "policy": name,
        "n": b["n"],
        "net_expectancy_r": b["net_expectancy_r"],
        "baseline_net_expectancy_r": base.get("net_expectancy_r"),
        "margin_over_baseline_r": margin,
        "median_mfe_capture_pct": b["median_mfe_capture_pct"],
        "median_giveback_r": b["median_giveback_r"],
        "premature_exit_pct": b["premature_exit_pct"],
        "status": "CANDIDATE_ONLY — a within-sample ranking on one day's trades; "
                  "not authorised for production and not validated",
    }
