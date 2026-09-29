"""Would taking something off at T1 have helped? — Phase 13 Part 9. RESEARCH ONLY.

The pathology this measures is the one on the user's chart: a call reaches its first
target, then hands it all back. On the 24-26 Aug book the median giveback is around
0.9R, and a resolution that latched T2 before finishing at -1.08R is in the ledger.

Three policies are priced from latched milestones alone:

* **A_BASELINE** — the production behaviour, held to target, stop or timeout;
* **B_HALF_AT_T1** — half the position exits at T1, the rest resolves as it did;
* **C_HALF_AT_T1_THEN_BREAKEVEN** — half at T1, and the remainder exits flat if the
  premium came back to the entry after T1 rather than following it down.

Trailing stops and partials at T2 are deliberately absent: pricing them needs the
minute premium path, which lives in :mod:`app.research.phase7`, and inventing a
trail from three latched timestamps would produce a number that looks measured and
is not. C's breakeven leg is itself an approximation and is flagged as one — it
detects the return to entry from the latched MAE and its timestamp, so it can only
see a pullback that was the deepest point of the trade after T1.
"""
from __future__ import annotations

POLICIES = ("A_BASELINE", "B_HALF_AT_T1", "C_HALF_AT_T1_THEN_BREAKEVEN")
NEEDS_PATH = ("D_TRAIL_AFTER_T1", "E_PARTIAL_AT_T2", "F_ATR_TRAIL")


def _num(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _t1_r(row: dict) -> float | None:
    """R at the first target, from the plan's own recorded levels."""
    entry = _num(row.get("entry"))
    risk = _num(row.get("risk_points"))
    target = _num(row.get("target1"))
    if entry is None or target is None or not risk or risk <= 0:
        return None
    return round((target - entry) / risk, 4)


def _returned_to_entry_after_t1(row: dict) -> bool:
    t1 = _num(row.get("minutes_to_t1"))
    mae_min = _num(row.get("minutes_to_mae"))
    mae_r = _num(row.get("mae_r"))
    return bool(t1 is not None and mae_min is not None and mae_r is not None
                and mae_min > t1 and mae_r <= 0.0)


def simulate(row: dict) -> dict:
    """One resolved call under each priceable protection policy, in gross R."""
    gross = _num(row.get("gross_r"))
    if gross is None:
        return {}
    reached_t1 = _num(row.get("minutes_to_t1")) is not None
    t1_r = _t1_r(row)
    out = {"A_BASELINE": gross}
    if not reached_t1 or t1_r is None:
        out["B_HALF_AT_T1"] = gross
        out["C_HALF_AT_T1_THEN_BREAKEVEN"] = gross
        return out
    out["B_HALF_AT_T1"] = round(0.5 * t1_r + 0.5 * gross, 4)
    remainder = 0.0 if _returned_to_entry_after_t1(row) else gross
    out["C_HALF_AT_T1_THEN_BREAKEVEN"] = round(0.5 * t1_r + 0.5 * remainder, 4)
    return out


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def study(rows: list[dict]) -> dict:
    """Part 9 — protection policies against the production baseline."""
    sims = [(r, simulate(r)) for r in rows]
    priced = [(r, s) for r, s in sims if s]
    results = []
    for name in POLICIES:
        gross = [s[name] for _, s in priced if name in s]
        # Cost is the same round trip in every policy: the exits differ, the legs
        # traded do not, so a partial is not credited with half the spread.
        net = [round(s[name] - (_num(r.get("cost_r")) or 0.0), 4)
               for r, s in priced
               if name in s and _num(r.get("cost_r")) is not None]
        wins = [v for v in net if v > 0]
        results.append({
            "policy": name,
            "n": len(gross),
            "expectancy_gross_r": _mean(gross),
            "expectancy_net_r": _mean(net),
            "win_rate_net_pct": (round(100.0 * len(wins) / len(net), 1)
                                 if net else None),
            "costed": len(net),
        })
    reached = [r for r, _ in priced if _num(r.get("minutes_to_t1")) is not None]
    gave_back = [r for r in reached if (_num(r.get("gross_r")) or 0.0) <= 0]
    return {
        "part": "13 Part 9",
        "research_only": True,
        "resolved": len(rows),
        "priced": len(priced),
        "policies": results,
        "reached_t1": len(reached),
        "reached_t1_then_finished_negative": len(gave_back),
        "reached_t1_then_finished_negative_pct": (
            round(100.0 * len(gave_back) / len(reached), 1) if reached else None),
        "policies_needing_premium_path": list(NEEDS_PATH),
        "notes": [
            "every policy pays the same round-trip cost as the baseline; a partial "
            "is not flattered by being charged half the spread",
            "the breakeven leg is detected from the latched MAE after T1, so it "
            "only sees a return to entry that was the deepest point of the trade",
            "trailing and T2 partials need the minute premium path and are not "
            "estimated here",
            "nothing in this module changes a production exit",
        ],
    }
