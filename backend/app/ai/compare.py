"""Baseline vs AI comparison.

Two comparisons, kept apart on purpose because they answer different questions:

**Decision level (apples to apples).** Every journaled decision carries the
production engine's simultaneous verdict and, once the horizon passes, what BOTH
sides of the underlying did on identical levels. So baseline and AI are scored on
the same bars, at the same prices, on the same geometry — the only difference is
which bars each chose to act on. This is the honest comparison, and it is measured
on the UNDERLYING (no premium, no theta, no spread).

**Paper level (what the AI book actually returned).** Realised paper P&L with
spread, slippage and brokerage charged. Real prices, real costs, but only the AI
trades exist — the baseline has no matching paper book here, so this side is
reported, not compared.

Statistical honesty is enforced in the output rather than left to the reader:
:func:`compare` reports the sample size next to every metric and a
``verdict_allowed`` flag that stays False until enough resolved decisions exist to
say anything. Phase 5 measured the required samples: ~3,400 paired trades to
detect a 0.10R difference, ~13,700 for 0.05R. A week of data cannot answer this
and the function refuses to pretend otherwise.
"""
from __future__ import annotations

import math

from app.ai import journal as aij

# Minimum resolved paired decisions before a difference is reportable at all
# (0.10R effect, 80% power, from the Phase 5 power calculation).
MIN_PAIRED_FOR_VERDICT = 3400

_BASELINE_BUY = ("BUY", "BUY_NOW")
_AI_BUY = ("BUY_NOW",)


def _side_outcome(row: dict, side: str | None) -> dict | None:
    if side == "CE":
        return {"result": row.get("ce_result"), "r": row.get("ce_r"),
                "mfe": row.get("ce_mfe_r"), "mae": row.get("ce_mae_r")}
    if side == "PE":
        return {"result": row.get("pe_result"), "r": row.get("pe_r"),
                "mfe": row.get("pe_mfe_r"), "mae": row.get("pe_mae_r")}
    return None


def _metrics(rows: list[dict]) -> dict:
    """Population metrics for a list of ``{result, r, mfe, mae}`` outcomes."""
    rs = [float(r["r"]) for r in rows if r.get("r") is not None]
    n = len(rs)
    if not n:
        return {"trades": 0}
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in rs:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    mfes = [float(r["mfe"]) for r in rows if r.get("mfe") is not None]
    maes = [float(r["mae"]) for r in rows if r.get("mae") is not None]
    targets = sum(1 for r in rows if r.get("result") == "TARGET")
    sd = (math.sqrt(sum((x - sum(rs) / n) ** 2 for x in rs) / (n - 1))
          if n > 1 else 0.0)
    return {
        "trades": n,
        "target_before_stop_pct": round(100.0 * targets / n, 2),
        "win_rate_pct": round(100.0 * len(wins) / n, 2),
        "expectancy_r": round(sum(rs) / n, 4),
        "expectancy_ci95": [round(sum(rs) / n - 1.96 * sd / math.sqrt(n), 4),
                            round(sum(rs) / n + 1.96 * sd / math.sqrt(n), 4)] if n > 1 else None,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "max_drawdown_r": round(max_dd, 3),
        "avg_winner_r": round(sum(wins) / len(wins), 4) if wins else None,
        "avg_loser_r": round(sum(losses) / len(losses), 4) if losses else None,
        "avg_mfe_r": round(sum(mfes) / len(mfes), 4) if mfes else None,
        "avg_mae_r": round(sum(maes) / len(maes), 4) if maes else None,
        # Entry quality, defined on the outcome: a "peak" entry never went
        # anywhere before it was resolved; a "late" entry only worked after first
        # giving back most of the stop distance.
        "peak_entry_pct": round(100.0 * sum(
            1 for r in rows if (r.get("mfe") or 0) < 0.3) / n, 2),
        "late_entry_pct": round(100.0 * sum(
            1 for r in rows if (r.get("mae") or 0) >= 0.7
            and r.get("result") == "TARGET") / n, 2),
    }


def compare(limit: int = 100000) -> dict:
    """Baseline vs AI on the resolved decision journal, plus the AI paper book."""
    j = aij.journal()
    rows = j.dataset(limit)
    paired = len(rows)

    base_rows: list[dict] = []
    ai_rows: list[dict] = []
    both, base_only, ai_only, neither = 0, 0, 0, 0
    missed_by_ai = 0
    avoided_losses = 0

    for r in rows:
        b_buy = (r.get("baseline_decision") or "") in _BASELINE_BUY
        a_buy = (r.get("ai_decision") or "") in _AI_BUY
        b_out = _side_outcome(r, r.get("baseline_side")) if b_buy else None
        a_out = _side_outcome(r, r.get("ai_side")) if a_buy else None
        if b_out and b_out.get("r") is not None:
            base_rows.append(b_out)
        if a_out and a_out.get("r") is not None:
            ai_rows.append(a_out)
        if b_buy and a_buy:
            both += 1
        elif b_buy:
            base_only += 1
            if b_out and b_out.get("result") == "TARGET":
                missed_by_ai += 1
            elif b_out and b_out.get("result") == "STOP":
                avoided_losses += 1
        elif a_buy:
            ai_only += 1
        else:
            neither += 1

    base_m = _metrics(base_rows)
    ai_m = _metrics(ai_rows)
    diff = None
    if base_m.get("trades") and ai_m.get("trades"):
        diff = round(ai_m["expectancy_r"] - base_m["expectancy_r"], 4)

    # Paper book (real prices, real costs, AI only).
    closed = j.closed_trades()
    prs = [float(t["r_multiple"]) for t in closed if t.get("r_multiple") is not None]
    wins = [x for x in prs if x > 0]
    losses = [x for x in prs if x <= 0]
    holds = [float(t["hold_sec"]) for t in closed if t.get("hold_sec")]
    reasons: dict[str, int] = {}
    for t in closed:
        key = str(t.get("exit_reason") or "UNKNOWN")
        reasons[key] = reasons.get(key, 0) + 1

    return {
        "resolved_decisions": paired,
        "verdict_allowed": paired >= MIN_PAIRED_FOR_VERDICT,
        "min_paired_for_verdict": MIN_PAIRED_FOR_VERDICT,
        "basis": "UNDERLYING move on identical levels (no premium, theta or spread)",
        "overlap": {"both_bought": both, "baseline_only": base_only,
                    "ai_only": ai_only, "neither": neither},
        "baseline": base_m,
        "ai": ai_m,
        "expectancy_difference_r": diff,
        "ai_missed_baseline_winners": missed_by_ai,
        "ai_avoided_baseline_losers": avoided_losses,
        "paper_book": {
            "closed_trades": len(closed),
            "realized_pnl": round(sum(
                float(t.get("realized_pnl") or 0.0) for t in closed), 2),
            "costs_charged": round(sum(
                float(t.get("costs") or 0.0) for t in closed), 2),
            "win_rate_pct": round(100.0 * len(wins) / len(prs), 2) if prs else None,
            "expectancy_r": round(sum(prs) / len(prs), 4) if prs else None,
            "profit_factor": (round(sum(wins) / -sum(losses), 3)
                              if losses and sum(losses) else None),
            "avg_winner_r": round(sum(wins) / len(wins), 4) if wins else None,
            "avg_loser_r": round(sum(losses) / len(losses), 4) if losses else None,
            "avg_hold_min": round(sum(holds) / len(holds) / 60.0, 2) if holds else None,
            "exit_reasons": reasons,
            "note": ("Paper fills cross the spread and pay slippage plus brokerage. "
                     "There is no matching baseline paper book, so this section is "
                     "reported, not a comparison."),
        },
        "warning": (
            "Win rate alone says nothing here and one week of data cannot settle "
            "this comparison. Until resolved_decisions reaches "
            f"{MIN_PAIRED_FOR_VERDICT}, treat every difference above as noise."),
    }
