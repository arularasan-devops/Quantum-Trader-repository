"""Phase 11 §8 — spread and tradability, per instrument and family. RESEARCH ONLY.

Phase 9 and 10 reported a median spread share of intended risk. A median hides
the case that actually costs money: an instrument whose median book is payable but
whose upper tail is not, so most trades look fine and the tail removes the
session's profit. §8 therefore asks for the distribution — p50, p75, p90, max —
and for the share of trades where the book was wider than the stop, which is the
one arithmetic statement that needs no model: when the round-trip spread exceeds
the intended risk, the stop sits inside the spread and the trade cannot win
whatever the signal said.

Classes are research monitoring labels. No threshold here is applied to
production, and no instrument is removed from any universe by this module — a
sample this size can say "unpayable on these sessions", never "unpayable".
"""
from __future__ import annotations

from app.research.phase9.findings9 import label9
from app.research.phase9.tradability import (
    UNTRADABLE_SPREAD_SHARE_PCT,
    deciles,
)

from .families import FAMILIES, split

TRADABLE = "TRADABLE"
CAUTION = "CAUTION"
UNTRADABLE_ON_SAMPLE = "UNTRADABLE_ON_SAMPLE"
UNMEASURED = "UNMEASURED"

# Research monitoring bands on the median book as a share of intended risk.
# TRADABLE is set where the book is a minor tax on the trade; CAUTION where it is
# a material share of the stop; UNTRADABLE_ON_SAMPLE at the arithmetic ceiling.
TRADABLE_MAX_MEDIAN_PCT = 10.0
CAUTION_MAX_MEDIAN_PCT = 50.0
MIN_TRADES_TO_CLASSIFY = 20


def _classify(median_pct: float | None, n: int) -> str:
    if median_pct is None or n < MIN_TRADES_TO_CLASSIFY:
        return UNMEASURED
    if median_pct >= UNTRADABLE_SPREAD_SHARE_PCT:
        return UNTRADABLE_ON_SAMPLE
    if median_pct <= TRADABLE_MAX_MEDIAN_PCT:
        return TRADABLE
    if median_pct <= CAUTION_MAX_MEDIAN_PCT:
        return CAUTION
    return UNTRADABLE_ON_SAMPLE


def _block(rows: list[dict], sessions: int) -> dict:
    shares = [float(r["spread_share_of_risk_pct"]) for r in rows
              if r.get("spread_share_of_risk_pct") is not None]
    d = deciles(shares)
    gross = [r["realised_r"] for r in rows if r.get("realised_r") is not None]
    net = [r["net_r"] for r in rows if r.get("net_r") is not None]
    over = [s for s in shares if s >= UNTRADABLE_SPREAD_SHARE_PCT]
    med = d.get("p50") if shares else None
    return {
        "trades": len(rows),
        "trades_with_two_sided_book": len(shares),
        "book_coverage_pct": round(100.0 * len(shares) / len(rows), 1) if rows
        else None,
        "median_spread_share_of_risk_pct": med,
        "p75_spread_share_of_risk_pct": d.get("p75") if shares else None,
        "p90_spread_share_of_risk_pct": d.get("p90") if shares else None,
        "max_spread_share_of_risk_pct": d.get("max") if shares else None,
        "pct_of_trades_spread_exceeded_risk": round(
            100.0 * len(over) / len(shares), 1) if shares else None,
        "gross_expectancy_r": round(sum(gross) / len(gross), 3) if gross else None,
        "net_expectancy_r": round(sum(net) / len(net), 3) if net else None,
        "total_net_r": round(sum(net), 3) if net else None,
        "spread_cost_r_median": (
            None if not rows else
            deciles([float(r["spread_cost_r"]) for r in rows
                     if r.get("spread_cost_r") is not None]).get("p50")),
        "class": _classify(med, len(rows)),
        "class_status": "RESEARCH_MONITORING_ONLY",
        "label": label9(len(rows), sessions),
    }


def study(rows: list[dict], sessions: int) -> dict:
    """§8 — the spread distribution per family and per instrument."""
    by_family = split(rows)
    families = {fam: _block(by_family.get(fam) or [], sessions)
                for fam in FAMILIES}

    by_inst: dict[str, list[dict]] = {}
    for r in rows:
        by_inst.setdefault(r["instrument"], []).append(r)
    instruments = {}
    for name, sub in sorted(by_inst.items()):
        instruments[name] = {"family": sub[0].get("family"),
                             **_block(sub, sessions)}

    def named(cls: str) -> list[str]:
        return [k for k, v in instruments.items() if v["class"] == cls]

    return {
        "by_family": families,
        "by_instrument": instruments,
        "tradable": named(TRADABLE),
        "caution": named(CAUTION),
        "untradable_on_sample": named(UNTRADABLE_ON_SAMPLE),
        "unmeasured": named(UNMEASURED),
        "bands_pct": {"tradable_median_at_or_below": TRADABLE_MAX_MEDIAN_PCT,
                      "caution_median_at_or_below": CAUTION_MAX_MEDIAN_PCT,
                      "untradable_median_above": CAUTION_MAX_MEDIAN_PCT,
                      "arithmetic_ceiling": UNTRADABLE_SPREAD_SHARE_PCT},
        "min_trades_to_classify": MIN_TRADES_TO_CLASSIFY,
        "thresholds_status": "PROPOSED_ONLY — not applied as a gate, a warning, a "
                             "filter or a universe change anywhere in production",
        "caveat": f"an UNTRADABLE_ON_SAMPLE class is a statement about "
                  f"{sessions} recorded session(s) and the legs the selector "
                  f"actually chose, not a permanent property of the contract",
    }
