"""Phase 9 §3/§8 — what BAD_STRIKE actually was. RESEARCH ONLY.

Phase 8 assigns BAD_STRIKE when the underlying moved favourably by at least half an
ATR and the option's premium still went nowhere (``mfe_r < 0.3``). That is a real
measurement, but as a *cause* it is a placeholder: it says the contract did not
transmit the move, without saying why. Five different defects produce that symptom
and they demand opposite fixes, so this module refuses to leave it as one label.

The decomposition is deterministic and ordered, exactly as Phase 8's is, and the
order encodes "what would have had to be different":

DEGENERATE_GREEKS       the feed's own delta/IV for this leg cannot be trusted, so
                        every delta-derived judgement below is unavailable. Reported
                        first because it disqualifies the analysis, not the trade.
BAD_SPREAD              the quoted book took an unacceptable share of the risk unit
LOW_OI / LOW_VOLUME     the contract was not being traded
PREMIUM_OVEREXPANSION   the premium had already run before the signal
POOR_DELTA              the leg was too insensitive to carry the move that came
WRONG_MONEYNESS         the leg was far enough OTM that the move could not reach it
INSUFFICIENT_ROOM       target1 needed more underlying than the ATR offered
OPTION_UNDERLYING_DIVERGENCE
                        the book was fine, the leg was liquid, the delta was
                        adequate — and the premium still did not follow. This is the
                        residual, and it is the only sub-cause that actually
                        indicts the *contract selection* rather than the market or
                        the feed.
OTHER                   nothing measurable applied

The last point is the answer to the spec's first question (§544). If the residual is
small, BAD_STRIKE is mostly execution economics and liquidity, and the selector is
not the thing to change. Every threshold below is a stated constant, printed with
the report, and each is a plain reading of the quantity it applies to — not a fitted
parameter.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from app.research.phase7.policies import median

from .findings9 import label9, note9

SUB_CAUSES = (
    "DEGENERATE_GREEKS",
    "BAD_SPREAD",
    "LOW_OI",
    "LOW_VOLUME",
    "PREMIUM_OVEREXPANSION",
    "POOR_DELTA",
    "WRONG_MONEYNESS",
    "INSUFFICIENT_ROOM",
    "OPTION_UNDERLYING_DIVERGENCE",
    "OTHER",
)

SPREAD_SHARE_OF_RISK_PCT = 25.0   # book takes a quarter of the intended risk
MIN_OI = 1000.0                   # contract barely open
MIN_VOLUME = 100.0                # contract barely traded today
OVEREXPANSION_PCT = 25.0          # premium already ran this much before the signal
POOR_DELTA_ABS = 0.25             # too insensitive to carry an ATR-sized move
FAR_OTM_PCT = -2.0                # moneyness below this is far out of the money
ROOM_RATIO_MAX = 1.0              # target needs more move than the ATR offers


def _entry_row(signal: dict) -> dict | None:
    """The selected leg's entry-time row from its ladder, if it is there."""
    if not signal:
        return None
    for row in signal.get("entry_time_rows", []):
        if row.get("is_selected"):
            return row
    return None


def sub_flags(entry_row: dict | None) -> dict[str, bool]:
    """Every sub-cause that applies, independent of the ranking below."""
    if entry_row is None:
        return {"OTHER": True}
    spread_share = entry_row.get("spread_share_of_risk_pct")
    oi = entry_row.get("oi")
    volume = entry_row.get("volume")
    expansion = entry_row.get("past_expansion_pct")
    delta = entry_row.get("delta")
    money = entry_row.get("moneyness_pct")
    room = entry_row.get("room_ratio")
    usable = bool(entry_row.get("greeks_usable"))
    return {
        "DEGENERATE_GREEKS": not usable,
        "BAD_SPREAD": bool(spread_share is not None
                           and spread_share >= SPREAD_SHARE_OF_RISK_PCT),
        "LOW_OI": bool(oi is not None and oi < MIN_OI),
        "LOW_VOLUME": bool(volume is not None and volume < MIN_VOLUME),
        "PREMIUM_OVEREXPANSION": bool(expansion is not None
                                      and expansion >= OVEREXPANSION_PCT),
        "POOR_DELTA": bool(usable and delta is not None
                           and abs(delta) < POOR_DELTA_ABS),
        "WRONG_MONEYNESS": bool(money is not None and money <= FAR_OTM_PCT),
        "INSUFFICIENT_ROOM": bool(room is not None and room > ROOM_RATIO_MAX),
        "OPTION_UNDERLYING_DIVERGENCE": True,   # residual, see module docstring
    }


def primary_sub(flagged: dict[str, bool]) -> str:
    for name in SUB_CAUSES:
        if name == "OTHER":
            break
        if flagged.get(name):
            return name
    return "OTHER"


def decompose(cause_rows: list[dict], signals_by_key: dict[tuple, dict]) -> list[dict]:
    """One decomposed row per loss whose Phase 8 primary cause is BAD_STRIKE."""
    out: list[dict] = []
    for row in cause_rows:
        if row.get("primary_cause") != "BAD_STRIKE" or row.get("outcome") != "LOSS":
            continue
        signal = signals_by_key.get((row["instrument"], row["symbol"], row["ts_ist"]))
        entry_row = _entry_row(signal or {})
        flagged = sub_flags(entry_row)
        better = 0 if not signal else signal.get("better_at_entry_time_count") or 0
        out.append({
            "instrument": row["instrument"],
            "symbol": row["symbol"],
            "side": row["side"],
            "ts_ist": row["ts_ist"],
            "expiry_class": row.get("expiry_class"),
            "realised_r": row.get("realised_r"),
            "net_r": row.get("net_r"),
            "mfe_r": row.get("mfe_r"),
            "underlying_favourable": (row.get("underlying_move") or {}).get("favourable"),
            "primary_sub_cause": primary_sub(flagged),
            "sub_flags": [k for k, v in flagged.items() if v and k != "OPTION_UNDERLYING_DIVERGENCE"],
            "premium": None if not entry_row else entry_row.get("premium"),
            "premium_band": None if not entry_row else entry_row.get("premium_band"),
            "spread_share_of_risk_pct": (
                None if not entry_row else entry_row.get("spread_share_of_risk_pct")),
            "delta": None if not entry_row else entry_row.get("delta"),
            "oi": None if not entry_row else entry_row.get("oi"),
            "volume": None if not entry_row else entry_row.get("volume"),
            "moneyness_pct": None if not entry_row else entry_row.get("moneyness_pct"),
            "room_ratio": None if not entry_row else entry_row.get("room_ratio"),
            "past_expansion_pct": (
                None if not entry_row else entry_row.get("past_expansion_pct")),
            "steps_from_atm": None if not entry_row else entry_row.get("steps_from_atm"),
            "entry_time_composite": (
                None if not entry_row else entry_row.get("entry_time_composite")),
            "better_at_entry_time_count": better,
            "ladder_available": signal is not None,
        })
    return out


def study(rows: list[dict], total_losses: int, sessions: int,
          contamination: dict) -> dict:
    """§8 — where the BAD_STRIKE losses are concentrated."""
    if not rows:
        return {"bad_strike_losses": 0, "total_losses": total_losses,
                "label": label9(0, sessions),
                "reading": "no loss carried BAD_STRIKE as its primary cause"}
    primary = Counter(r["primary_sub_cause"] for r in rows)
    flags: Counter = Counter()
    for r in rows:
        flags.update(r["sub_flags"])

    table = []
    for name in SUB_CAUSES:
        n = primary.get(name, 0)
        table.append({
            "sub_cause": name,
            "primary": n,
            "pct_of_bad_strike": round(100.0 * n / len(rows), 1),
            "flagged": flags.get(name, 0),
            "label": label9(n, sessions),
        })
    table.sort(key=lambda r: (-r["primary"], r["sub_cause"]))
    ranked = [r for r in table if r["primary"]]
    residual = primary.get("OPTION_UNDERLYING_DIVERGENCE", 0)

    by_inst: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_inst[r["instrument"]][r["primary_sub_cause"]] += 1

    better = [r["better_at_entry_time_count"] for r in rows]
    return {
        "bad_strike_losses": len(rows),
        "total_losses": total_losses,
        "share_of_all_losses_pct": round(100.0 * len(rows) / max(1, total_losses), 1),
        "ordering_is_the_definition": list(SUB_CAUSES),
        "thresholds": {
            "spread_share_of_risk_pct": SPREAD_SHARE_OF_RISK_PCT,
            "min_oi": MIN_OI, "min_volume": MIN_VOLUME,
            "overexpansion_pct": OVEREXPANSION_PCT,
            "poor_delta_abs": POOR_DELTA_ABS, "far_otm_pct": FAR_OTM_PCT,
            "room_ratio_max": ROOM_RATIO_MAX,
        },
        "table": table,
        "top_sub_cause": ranked[0]["sub_cause"] if ranked else None,
        "residual_divergence": residual,
        "residual_pct": round(100.0 * residual / len(rows), 1),
        "medians": {
            "spread_share_of_risk_pct": median(
                [r["spread_share_of_risk_pct"] for r in rows
                 if r["spread_share_of_risk_pct"] is not None]),
            "abs_delta": median([abs(r["delta"]) for r in rows
                                 if r["delta"] is not None]),
            "oi": median([r["oi"] for r in rows if r["oi"] is not None]),
            "volume": median([r["volume"] for r in rows if r["volume"] is not None]),
            "moneyness_pct": median([r["moneyness_pct"] for r in rows
                                     if r["moneyness_pct"] is not None]),
            "room_ratio": median([r["room_ratio"] for r in rows
                                  if r["room_ratio"] is not None]),
            "past_expansion_pct": median([r["past_expansion_pct"] for r in rows
                                          if r["past_expansion_pct"] is not None]),
        },
        "by_instrument_top": {k: v.most_common(1)[0][0]
                              for k, v in sorted(by_inst.items()) if v},
        "entry_time_alternatives": {
            "median_better_scoring_legs_available": median([float(x) for x in better]),
            "losses_where_nothing_scored_better_pct": round(100.0 * sum(
                1 for x in better if x == 0) / len(better), 1),
            "reading": "a BAD_STRIKE loss where nothing in the ladder scored better "
                       "at entry time cannot be blamed on contract selection: the "
                       "selector picked the best available leg by these components",
        },
        "data_contamination": contamination,
        "label": label9(len(rows), sessions),
        "label_note": note9(len(rows), sessions),
        "reading": (
            "the residual OPTION_UNDERLYING_DIVERGENCE count is the only part of "
            "BAD_STRIKE that indicts contract selection; everything above it in the "
            "ranking is an execution-economics, liquidity, feed or reachability "
            "problem, and those are fixed by refusing the trade or by recording "
            "better data, not by changing the strike selector"),
    }
