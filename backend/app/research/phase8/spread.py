"""Phase 8 Part 2/8 — what the real book costs. RESEARCH ONLY.

Every Phase 7 number is a mid-less premium: the entry and the exit both happen at
the recorded snapshot premium, so both are better than a real fill. The 18-Aug
chains are the first with ``bid`` and ``ask`` on every leg, which makes the size
of that flattery measurable instead of a caveat.

The model here is the plainest honest one, stated so it can be argued with:

* a buy pays the **ask**, a sell receives the **bid**;
* the cost of the round trip is therefore ``(ask_at_entry - premium_at_entry) +
  (premium_at_exit - bid_at_exit)`` in premium points — the distance between what
  Phase 7 assumed and what the book was actually showing at those two moments;
* it is expressed in R using the trade's own risk, so a ₹15 and a ₹2000 premium
  are comparable;
* brokerage is *not* included here — that is ``policies.COST_PER_ROUNDTRIP_OPTIONS``
  and still an assumption. Spread is measured; commission is assumed. They are
  never added into one number.

Nothing here changes an entry, an exit, a stop or a target: it re-prices trades
Phase 7 already simulated.
"""
from __future__ import annotations

from collections import defaultdict

from app.research.phase7.dataset import ChainSeries
from app.research.phase7.policies import median

from .findings import label, note


def leg_at(series: ChainSeries, symbol: str, when: int) -> dict | None:
    snap = series.at(when)
    if not snap:
        return None
    leg = snap.get(symbol)
    return leg if isinstance(leg, dict) else None


def _book(leg: dict | None) -> tuple[float | None, float | None]:
    if not leg:
        return None, None
    bid, ask = leg.get("bid"), leg.get("ask")
    try:
        b, a = float(bid), float(ask)
    except (TypeError, ValueError):
        return None, None
    if b <= 0 or a <= 0 or a < b:
        # A crossed or absent book is unmeasured, never silently treated as zero
        # cost: a zero here would understate friction exactly where it is worst.
        return None, None
    return b, a


def measure(ev, fill, series: ChainSeries) -> dict:
    """Spread cost of one already-simulated trade. Missing book -> measured=False."""
    risk = max(0.01, fill.entry - fill.stop)
    entry_leg = leg_at(series, ev.symbol, fill.entry_ts)
    exit_leg = leg_at(series, ev.symbol, fill.exit_ts)
    eb, ea = _book(entry_leg)
    xb, xa = _book(exit_leg)
    row = {
        "symbol": ev.symbol,
        "instrument": ev.instrument,
        "side": ev.side,
        "gross_r": round(fill.r, 3),
        "measured": bool(eb is not None and xb is not None),
        # Part H: an unmeasurable book is labelled, never filled in with a guess.
        "spread_status": ("MEASURED" if eb is not None and xb is not None
                          else "SPREAD_UNKNOWN"),
        "entry_premium": round(fill.entry, 2),
        "exit_premium": round(fill.exit, 2),
        "risk_premium": round(risk, 2),
    }
    if eb is not None:
        row |= {"entry_bid": eb, "entry_ask": ea,
                "entry_spread": round(ea - eb, 2),
                "entry_spread_pct_of_premium": round(
                    100.0 * (ea - eb) / max(0.01, fill.entry), 2),
                "entry_spread_cost": round(max(0.0, ea - fill.entry), 2)}
    if xb is not None:
        row |= {"exit_bid": xb, "exit_ask": xa,
                "exit_spread": round(xa - xb, 2),
                "exit_spread_cost": round(max(0.0, fill.exit - xb), 2)}
    if row["measured"]:
        total = row["entry_spread_cost"] + row["exit_spread_cost"]
        row |= {
            "total_spread_cost": round(total, 2),
            "total_spread_cost_r": round(total / risk, 3),
            "net_r": round(fill.r - total / risk, 3),
            # How much of the risk unit the book takes before the trade has done
            # anything. Above ~0.25R the stop is closer than the friction.
            "spread_share_of_risk_pct": round(100.0 * total / risk, 1),
        }
    return row


def _agg(rows: list[dict], sessions: int) -> dict:
    seen = [r for r in rows if r.get("measured")]
    if not seen:
        return {"n": len(rows), "measured": 0, "spread_status": "SPREAD_UNKNOWN",
                "label": label(0, sessions, comparative=False),
                "note": "no leg in this group had a usable bid/ask at both ends"}
    gross = [r["gross_r"] for r in seen]
    net = [r["net_r"] for r in seen]
    return {
        "n": len(rows),
        "measured": len(seen),
        "unmeasured": len(rows) - len(seen),
        "median_entry_spread_pct": median(
            [r["entry_spread_pct_of_premium"] for r in seen]),
        "median_entry_spread_cost": median([r["entry_spread_cost"] for r in seen]),
        "median_exit_spread_cost": median([r["exit_spread_cost"] for r in seen]),
        "median_total_spread_cost": median([r["total_spread_cost"] for r in seen]),
        "median_spread_cost_r": median([r["total_spread_cost_r"] for r in seen]),
        "median_spread_share_of_risk_pct": median(
            [r["spread_share_of_risk_pct"] for r in seen]),
        "gross_expectancy_r": round(sum(gross) / len(gross), 3),
        "net_expectancy_r": round(sum(net) / len(net), 3),
        "expectancy_lost_to_spread_r": round(
            (sum(gross) - sum(net)) / len(gross), 3),
        "gross_sum_r": round(sum(gross), 2),
        "net_sum_r": round(sum(net), 2),
        "winners_gross": sum(1 for r in gross if r > 0),
        "winners_net": sum(1 for r in net if r > 0),
        "flipped_to_loss_by_spread": sum(
            1 for r in seen if r["gross_r"] > 0 >= r["net_r"]),
        "label": label(len(seen), sessions),
        "note": note(len(seen), sessions),
    }


def study(rows: list[dict], sessions: int) -> dict:
    """Spread cost overall, by side, and by instrument."""
    by_side: dict[str, list[dict]] = defaultdict(list)
    by_inst: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_side[r["side"]].append(r)
        by_inst[r["instrument"]].append(r)
    return {
        "model": "buy at ask, sell at bid, against Phase 7's snapshot-premium "
                 "fills; brokerage excluded and still an assumption",
        "overall": _agg(rows, sessions),
        "by_side": {k: _agg(v, sessions) for k, v in sorted(by_side.items())},
        "by_instrument": {k: _agg(v, sessions) for k, v in sorted(by_inst.items())},
        "trades": rows,
    }
