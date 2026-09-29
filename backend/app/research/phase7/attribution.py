"""Phase 7 Part 22-23 — was it the signal or the execution? RESEARCH ONLY.

Part 22 splits every losing trade into a cause, because "the engine lost money"
is not actionable and the two possible fixes are opposites: a signal problem
means take fewer trades, an execution problem means keep the trades and change
the handling.

Part 23 compares manual fills against bot fills on the same legs. The rupee gap
between them is meaningless — position sizes differ by ~20× — so everything here
is normalised to points, to R, and to a one-lot equivalent.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

# Part 22 causes. A trade gets exactly one label, assigned by the first test that
# matches, so the ordering IS the definition and is stated rather than hidden.
CAUSES = (
    "SIGNAL_WRONG",        # nothing favourable was ever offered
    "STOP_TOO_TIGHT",      # stop taken out first, then the target was reached
    "EXIT_TOO_LATE",       # a full R or more was offered and given back
    "ENTRY_CHASED",        # a materially better entry existed moments later
    "COSTS",               # gross positive, net negative
    "UNCLASSIFIED",
)


def classify_loss(mfe_r: float, realised_r: float, target_after_stop: bool,
                  entry_improvement_pct: float, gross_r: float,
                  net_r: float) -> str:
    """One cause for one losing trade. Thresholds are stated, not tuned: 0.3R is
    "never went anywhere", 1.0R is "a full risk unit was on the table"."""
    if mfe_r < 0.3:
        return "SIGNAL_WRONG"
    if target_after_stop:
        return "STOP_TOO_TIGHT"
    if mfe_r >= 1.0 and realised_r <= 0.0:
        return "EXIT_TOO_LATE"
    if entry_improvement_pct >= 2.0:
        return "ENTRY_CHASED"
    if gross_r > 0 >= net_r:
        return "COSTS"
    return "UNCLASSIFIED"


def loss_attribution(rows: list[dict]) -> dict:
    """``rows`` carry the per-trade measurements; only losers are classified."""
    counts: dict[str, int] = {c: 0 for c in CAUSES}
    detail: list[dict] = []
    losers = 0
    for r in rows:
        if float(r.get("realised_r") or 0.0) > 0:
            continue
        losers += 1
        cause = classify_loss(
            float(r.get("mfe_r") or 0.0), float(r.get("realised_r") or 0.0),
            bool(r.get("target_after_stop")),
            float(r.get("entry_improvement_pct") or 0.0),
            float(r.get("gross_r") or 0.0), float(r.get("net_r") or 0.0))
        counts[cause] += 1
        detail.append({"symbol": r.get("symbol"), "cause": cause,
                       "mfe_r": r.get("mfe_r"), "realised_r": r.get("realised_r")})
    return {
        "losers": losers,
        "by_cause": counts,
        "by_cause_pct": {k: round(100.0 * v / losers, 1) if losers else 0.0
                         for k, v in counts.items()},
        "detail": detail,
        "reading": "signal-side causes (SIGNAL_WRONG) argue for taking fewer "
                   "trades; execution-side causes (EXIT_TOO_LATE, ENTRY_CHASED, "
                   "STOP_TOO_TIGHT) argue for keeping the same trades and "
                   "changing the handling. Do not average the two.",
    }


@dataclass
class Leg:
    """One side of the manual-vs-bot comparison on the same option symbol."""

    symbol: str
    entry: float
    exit: float
    lots: int = 1
    lot_size: int = 1

    @property
    def points(self) -> float:
        return self.exit - self.entry

    @property
    def pct(self) -> float:
        return 100.0 * self.points / self.entry if self.entry else 0.0


def load_manual_fills(path: str) -> dict[str, Leg]:
    """Manual fills as ``{symbol: Leg}`` from a JSON file the user supplies.

    The file is the user's own trade record; nothing is inferred and no default
    is invented, because a made-up manual fill would corrupt the only real
    comparison in the phase.
    """
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    rows = raw["fills"] if isinstance(raw, dict) else raw
    out: dict[str, Leg] = {}
    for row in rows:
        leg = Leg(symbol=str(row["symbol"]), entry=float(row["entry"]),
                  exit=float(row["exit"]), lots=int(row.get("lots") or 1),
                  lot_size=int(row.get("lot_size") or 1))
        out[leg.symbol] = leg
    return out


def compare_fills(manual: dict[str, Leg], bot: dict[str, Leg],
                  risk_pct: float = 25.0) -> dict:
    """Part 23 — manual vs bot on the legs BOTH traded, in points / % / R.

    ``risk_pct`` is the premium distance the production stop sits at, used only to
    express both sides in the same R unit. Legs only one side traded are reported
    separately: they are a difference in selection, not in execution, and mixing
    the two is how a selection edge gets mistaken for an execution edge.
    """
    shared = sorted(set(manual) & set(bot))
    rows = []
    for sym in shared:
        m, b = manual[sym], bot[sym]
        risk_m = max(0.01, m.entry * risk_pct / 100.0)
        risk_b = max(0.01, b.entry * risk_pct / 100.0)
        rows.append({
            "symbol": sym,
            "manual": {"entry": m.entry, "exit": m.exit,
                       "points": round(m.points, 2), "pct": round(m.pct, 2),
                       "r": round(m.points / risk_m, 3)},
            "bot": {"entry": b.entry, "exit": b.exit,
                    "points": round(b.points, 2), "pct": round(b.pct, 2),
                    "r": round(b.points / risk_b, 3)},
            "entry_gap_points": round(b.entry - m.entry, 2),
            "entry_gap_pct": round(100.0 * (b.entry - m.entry) / m.entry, 2),
            "exit_gap_points": round(b.exit - m.exit, 2),
            "gap_points": round(b.points - m.points, 2),
            "gap_r": round(b.points / risk_b - m.points / risk_m, 3),
        })
    entry_share = None
    if rows:
        entry_gap = sum(abs(r["entry_gap_points"]) for r in rows)
        exit_gap = sum(abs(r["exit_gap_points"]) for r in rows)
        total = entry_gap + exit_gap
        if total > 0:
            entry_share = round(100.0 * entry_gap / total, 1)
    return {
        "shared_legs": len(shared),
        "manual_only": sorted(set(manual) - set(bot)),
        "bot_only": sorted(set(bot) - set(manual)),
        "rows": rows,
        "attribution_pct": None if entry_share is None else {
            "entry_timing": entry_share, "exit_timing": round(100.0 - entry_share, 1),
        },
        "caveats": [
            "manual fills are real money at real timestamps; bot fills are paper "
            "marks at snapshot closes, so the bot's prices are neither better nor "
            "worse by construction — they are measured differently",
            "position sizes differ, so only points / % / R are comparable",
            f"n = {len(shared)} shared legs — a mechanism, not a measurement",
        ],
    }


def signal_vs_execution(rows: list[dict]) -> dict:
    """Part 22's headline split: of the money that was available, how much was
    lost because the call was wrong, and how much because of the handling?"""
    if not rows:
        return {"n": 0}
    offered = sum(max(0.0, float(r.get("mfe_r") or 0.0)) for r in rows)
    kept = sum(float(r.get("realised_r") or 0.0) for r in rows)
    never_offered = sum(1 for r in rows if float(r.get("mfe_r") or 0.0) < 0.3)
    return {
        "n": len(rows),
        "r_offered_total": round(offered, 2),
        "r_kept_total": round(kept, 2),
        "capture_pct": round(100.0 * kept / offered, 1) if offered > 0 else None,
        "trades_never_offering_0_3r_pct": round(100.0 * never_offered / len(rows), 1),
        "reading": "capture_pct near 100 with a negative total means the signals "
                   "were wrong; a healthy offered total with low capture means the "
                   "signals were right and the handling lost it",
    }
