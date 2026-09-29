"""Phase 10 §10-11 — spread monitoring and the cheap-premium question. RESEARCH ONLY.

Two questions, one dataset.

§10: is the spread cost of the instruments actually traded — GOLD, SILVER,
CRUDEOIL, NIFTY, BANKNIFTY — stable enough to classify? Phase 9 measured a RED
class with a median spread cost of 258% of intended risk; this splits that by
instrument so the reader can see whether the problem is a market condition or a
particular contract.

§11: the operator's own report is that low-premium options produce no signals. The
recorded data answers a sharper version of that question, and the answer has two
halves that must be read together:

* **were cheap legs available at signal time?** Counted from the recorded ladder,
  not from what was traded — the ladder is what the selector was choosing from.
* **when a cheap leg WAS selected, what happened net of the book?** A ₹1 tick on a
  ₹16 option is 6% of the premium, so a cheap leg can be directionally right and
  still unprofitable. Gross and net are reported side by side, always, because the
  gross number is the one that makes lowering the floor look attractive.

The premium floor is not changed here. §11 forbids it, and the evidence would not
support it in either direction: the correct output of this module is a measurement
and the sample size that measurement is entitled to.
"""
from __future__ import annotations

from app.research.phase7.policies import median
from app.research.phase9.findings9 import label9
from app.research.phase9.strikes import PREMIUM_BANDS, premium_band

# §10's named list. Kept explicit rather than derived from the traded universe so
# a monitored instrument that produced no trades shows up as a zero, not a gap.
MONITORED = ("GOLD", "SILVER", "CRUDEOIL", "NIFTY", "BANKNIFTY")

BAND_ORDER = [premium_band(lo + 0.01) for lo, _ in PREMIUM_BANDS]


def _num(rows: list[dict], key: str) -> float | None:
    return median([r[key] for r in rows if r.get(key) is not None])


def _leg_block(legs: list[dict], sessions: int) -> dict:
    """Entry-time facts for a set of ladder legs. No outcome here by design."""
    return {
        "legs_available": len(legs),
        "selected": sum(1 for r in legs if r.get("is_selected")),
        "median_premium": _num(legs, "premium"),
        "median_spread_share_of_risk_pct": _num(legs, "spread_share_of_risk_pct"),
        "median_spread_pct_of_premium": _num(legs, "spread_pct"),
        "median_abs_delta": median([abs(r["delta"]) for r in legs
                                    if r.get("delta") is not None]),
        "median_oi": _num(legs, "oi"),
        "median_volume": _num(legs, "volume"),
        "two_sided_book_pct": round(
            100.0 * sum(1 for r in legs
                        if r.get("spread_share_of_risk_pct") is not None)
            / len(legs), 1) if legs else None,
        "label": label9(len(legs), sessions),
    }


def _outcome_block(trades: list[dict], sessions: int) -> dict:
    gross = [t["realised_r"] for t in trades if t.get("realised_r") is not None]
    net = [t["net_r"] for t in trades if t.get("net_r") is not None]
    win = [r for r in net if r > 0]
    bad = -sum(r for r in net if r <= 0)
    return {
        "trades": len(trades),
        "target_before_stop_pct": round(
            100.0 * sum(1 for t in trades if t.get("target_before_stop_hit"))
            / len(trades), 1) if trades else None,
        "gross_expectancy_r": round(sum(gross) / len(gross), 3) if gross else None,
        "net_expectancy_r": round(sum(net) / len(net), 3) if net else None,
        "net_profit_factor": round(sum(win) / bad, 3) if bad > 0 else None,
        "total_net_r": round(sum(net), 3) if net else None,
        "median_spread_cost_r": median([t["spread_cost_r"] for t in trades
                                        if t.get("spread_cost_r") is not None]),
        "median_spread_share_of_risk_pct": _num(trades, "spread_share_of_risk_pct"),
        "label": label9(len(trades), sessions),
    }


def study(signals: list[dict], trade_rows: list[dict], sessions: int) -> dict:
    """§10-11. ``signals`` carry the recorded ladder; ``trade_rows`` the outcomes."""
    all_legs: list[dict] = []
    for sig in signals:
        for leg in sig.get("entry_time_rows", []):
            all_legs.append({**leg, "instrument": sig.get("instrument")})

    by_band_legs = {band: _leg_block([r for r in all_legs
                                      if r.get("premium_band") == band], sessions)
                    for band in BAND_ORDER}
    by_band_out = {band: _outcome_block([t for t in trade_rows
                                         if t.get("premium_band") == band], sessions)
                   for band in BAND_ORDER}

    monitored: dict[str, dict] = {}
    for name in MONITORED:
        legs = [r for r in all_legs if r.get("instrument") == name]
        trades = [t for t in trade_rows if t.get("instrument") == name]
        monitored[name] = {
            "entry_time": _leg_block(legs, sessions),
            "outcome": _outcome_block(trades, sessions),
            "recorded": bool(legs or trades),
            "note": None if (legs or trades) else
            "no recorded signal for this instrument in this window, so its spread "
            "class is unmeasured rather than good",
        }

    cheap = BAND_ORDER[0]
    cheap_legs = by_band_legs[cheap]
    cheap_out = by_band_out[cheap]
    best = max(
        ((b, by_band_out[b]) for b in BAND_ORDER
         if by_band_out[b]["net_expectancy_r"] is not None
         and by_band_out[b]["trades"] >= 10),
        key=lambda kv: kv[1]["net_expectancy_r"], default=(None, None))

    if cheap_legs["legs_available"] == 0:
        cheap_answer = (
            f"no leg under the {cheap} band appeared in the recorded ladder at any "
            f"signal instant, so the absence of cheap signals is an availability "
            f"fact and not a filter deciding against them")
    elif cheap_legs["selected"] == 0:
        cheap_answer = (
            f"{cheap_legs['legs_available']} cheap leg(s) were available at signal "
            f"time and none was selected — the selector, not availability, is what "
            f"excludes them. Whether that exclusion helps is answered by the net "
            f"column of the neighbouring bands, not by this count")
    else:
        cheap_answer = (
            f"cheap legs DO fire: {cheap_legs['selected']} selected out of "
            f"{cheap_legs['legs_available']} available, producing "
            f"{cheap_out['trades']} trade(s) at gross "
            f"{cheap_out['gross_expectancy_r']}R and net "
            f"{cheap_out['net_expectancy_r']}R. The gap between those two numbers is "
            f"the bid/ask on a small premium, and it is the whole of the answer")

    return {
        "monitored_instruments": monitored,
        "by_premium_band_entry_time": by_band_legs,
        "by_premium_band_outcome": by_band_out,
        "band_order": BAND_ORDER,
        "cheap_band": cheap,
        "cheap_premium_answer": cheap_answer,
        "best_band_by_net_expectancy": best[0],
        "best_band_note": None if best[0] is None else
        f"{best[0]} carried the best net expectancy among bands with 10+ trades. "
        f"This is a measurement over {sessions} session(s), not a recommendation to "
        f"move the floor",
        "premium_floor_status": "UNCHANGED — no floor, minimum premium or band "
                                "preference is modified by this phase",
        "thresholds_status": "PROPOSED_ONLY",
        "guarantee": "the strike selector, the premium floor and every gate are "
                     "read-only inputs here",
    }
