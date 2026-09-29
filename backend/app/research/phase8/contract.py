"""Phase 8 Part I — descriptive contract/strike quality. RESEARCH ONLY.

Records what the chain actually said about the leg the engine bought, and nothing
more: no scoring, no selector, no ranking. The strike selector is not redesigned and
is not even read here.

One judgement is applied, and it is a data-quality one rather than a trading one:
values that cannot be true are labelled instead of averaged. A leg quoted at
``iv <= 0.02`` with ``|delta| >= 0.999`` is not a 2% vol option — it is the feed
returning a degenerate value on a deep-ITM leg, and averaging it into an "IV vs
outcome" table would manufacture a relationship out of a data defect.
"""
from __future__ import annotations

from collections import defaultdict

from app.research.phase7.policies import median

from .findings import label

DEGENERATE_IV = 0.02
PEGGED_DELTA = 0.999


def describe(leg: dict | None, spot: float, strike: float, side: str) -> dict:
    """The recorded contract fields, plus moneyness and a usability verdict."""
    money = None
    if spot and strike:
        raw = (spot - strike) / spot if side == "CE" else (strike - spot) / spot
        money = round(100.0 * raw, 2)          # >0 = in the money
    if not leg:
        return {"recorded": False, "moneyness_pct": money,
                "greeks_usable": False, "reason": "no leg in the matched snapshot"}
    def num(key: str) -> float | None:
        try:
            return float(leg[key])
        except (KeyError, TypeError, ValueError):
            return None
    iv, delta = num("iv"), num("delta")
    bid, ask = num("bid"), num("ask")
    degenerate = bool(
        iv is not None and delta is not None
        and iv <= DEGENERATE_IV and abs(delta) >= PEGGED_DELTA)
    return {
        "recorded": True,
        "strike": strike,
        "premium": num("premium"),
        "bid": bid,
        "ask": ask,
        "spread": None if bid is None or ask is None else round(ask - bid, 2),
        "iv": iv,
        "delta": delta,
        "gamma": num("gamma"),
        "theta": num("theta"),
        "vega": num("vega"),
        "oi": num("oi"),
        "oi_change": num("oi_change"),
        "volume": num("volume"),
        "moneyness_pct": money,
        "greeks_usable": not degenerate,
        "reason": ("iv<=0.02 with |delta|>=0.999: degenerate feed value on a "
                   "deep-ITM leg, excluded from any greek aggregate"
                   if degenerate else ""),
    }


def study(rows: list[dict], sessions: int) -> dict:
    """Descriptive only: distributions, and outcome by bucket where the leg is usable."""
    have = [r for r in rows if r["contract"].get("recorded")]
    usable = [r for r in have if r["contract"].get("greeks_usable")]

    def dist(key: str, source: list[dict]) -> dict | None:
        vals = [r["contract"][key] for r in source
                if r["contract"].get(key) is not None]
        if not vals:
            return None
        return {"n": len(vals), "min": round(min(vals), 4),
                "median": median(vals), "max": round(max(vals), 4)}

    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in usable:
        d = abs(r["contract"].get("delta") or 0.0)
        band = ("delta<0.25" if d < 0.25 else "delta 0.25-0.45" if d < 0.45
                else "delta 0.45-0.65" if d < 0.65 else "delta>=0.65")
        buckets[band].append(r)

    return {
        "legs_recorded": len(have),
        "legs_with_usable_greeks": len(usable),
        "degenerate_greek_legs": len(have) - len(usable),
        "distributions": {k: dist(k, have) for k in
                          ("premium", "spread", "oi", "volume", "moneyness_pct")},
        "greek_distributions_usable_only": {
            k: dist(k, usable) for k in ("iv", "delta", "gamma", "theta", "vega")},
        "outcome_by_delta_band": {
            band: {
                "n": len(v),
                "expectancy_r": round(sum(r["realised_r"] for r in v) / len(v), 3),
                "target_before_stop_pct": round(100.0 * sum(
                    1 for r in v if r.get("target_before_stop_hit")) / len(v), 1),
                "label": label(len(v), sessions),
            } for band, v in sorted(buckets.items())},
        "caveat": "descriptive only. The strike selector is untouched and no band "
                  "above is a recommendation; single-day cells cannot rank strikes",
    }
