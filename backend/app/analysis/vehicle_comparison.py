"""Option vs futures vs no-trade on the same market idea — §5, RESEARCH ONLY.

25 Aug said 54% of resolved calls were VEHICLE_FAILURE: the read was right and
the premium still lost. That is an argument for asking whether the *other*
vehicle would have carried the same read better — and the only honest way to ask
it is on the same idea, at the same moment, with both results costed.

Two rules this module exists to enforce:

* **Same idea, or nothing.** A pair is formed on ``market_signal_id``, or failing
  that on instrument + direction inside a short window. Two unrelated trades in
  the same name are not a comparison, and the module says ``UNKNOWN`` rather than
  inventing one.
* **Never sum the units.** Options resolve in premium, futures in index points.
  R is the only common ground and even that is only compared *within* a pair,
  never as two aggregate P&L numbers.

``NO_TRADE`` is a real competitor, not a null: an idea whose only expression was
untradable and whose futures plan was invalid is a pair where standing aside won,
and that is recorded as ``BOTH_BAD`` rather than as a signal failure.

The verdict is one of ``OPTION_BETTER`` / ``FUTURES_BETTER`` / ``BOTH_VALID`` /
``BOTH_BAD`` / ``UNKNOWN``, per pair and never applied: production selects the
option leg exactly as before, and there is no futures order path at all.
"""
from __future__ import annotations

import json
import os
import statistics

from app.analysis import futures_outcomes, instrument_family, signal_journal
from app.config import settings

OPTION_BETTER = "OPTION_BETTER"
FUTURES_BETTER = "FUTURES_BETTER"
BOTH_VALID = "BOTH_VALID"
BOTH_BAD = "BOTH_BAD"
UNKNOWN = "UNKNOWN"

VERDICTS = (OPTION_BETTER, FUTURES_BETTER, BOTH_VALID, BOTH_BAD, UNKNOWN)

NO_TRADE = "NO_TRADE"

# Two records describe the same idea if they are the same instrument and
# direction and were written within this many seconds of each other. Wider than
# this and it is a second idea in the same name.
PAIR_WINDOW_SEC = 15 * 60
# A pair needs a decisive margin before one vehicle is called better; inside it
# the two did the same thing and the honest answer is BOTH_VALID or BOTH_BAD.
MATERIAL_R = 0.25
# Below this many pairs the aggregate is reported with its shortfall and no
# vehicle recommendation is made at all (§15).
MIN_SAMPLE = 30

REPORT_JSON = "vehicle_comparison.json"


def _direction(row: dict) -> str | None:
    d = row.get("direction") or row.get("direction_of_idea")
    if d in ("BULLISH", "BEARISH"):
        return d
    if d == futures_outcomes.LONG:
        return "BULLISH"
    if d == futures_outcomes.SHORT:
        return "BEARISH"
    return None


def _option_leg(row: dict) -> dict:
    """One resolved option outcome, reduced to the facts a comparison needs."""
    sel = ((row.get("vehicle_selection") or {}).get("vehicle_alternatives") or [])
    opt = next((a for a in sel if a.get("vehicle") == instrument_family.OPTIONS), {})
    return {
        "vehicle": instrument_family.OPTIONS,
        "unit": "PREMIUM",
        "contract": row.get("symbol"),
        "entry": row.get("entry"),
        "stop": row.get("stop"),
        "targets": row.get("targets"),
        "spread": opt.get("spread"),
        "spread_pct_of_premium": opt.get("spread_pct_of_premium"),
        "expected_room_r": opt.get("expected_room_r"),
        "tradability_status": opt.get("tradability_status"),
        "liquidity": opt.get("liquidity"),
        "mfe_r": row.get("mfe_r"),
        "mae_r": row.get("mae_r"),
        "target_before_stop": row.get("order") == "TARGET_FIRST",
        "gross_r": row.get("realized_r"),
        # The option book's costed result is produced by the shadow research
        # layer, not by the journal, so it is absent here rather than guessed.
        "net_r": None,
        "outcome": row.get("outcome"),
        "minutes_to_resolution": row.get("minutes_to_resolution"),
    }


def _futures_leg(row: dict) -> dict:
    return {
        "vehicle": instrument_family.FUTURES,
        "unit": "INDEX_POINTS",
        "contract": row.get("contract"),
        "entry": row.get("entry"),
        "stop": row.get("stop"),
        "targets": row.get("targets"),
        "spread": row.get("spread_points"),
        "expected_room_r": None,
        "rolled": row.get("rolled"),
        "days_to_expiry": row.get("days_to_expiry"),
        "mfe_r": row.get("mfe_r"),
        "mae_r": row.get("mae_r"),
        "target_before_stop": bool(row.get("target_before_stop")),
        "gross_r": row.get("gross_r"),
        "net_r": row.get("net_r"),
        "outcome": row.get("outcome"),
        "minutes_to_resolution": row.get("minutes_to_resolution"),
    }


def _best_available(leg: dict) -> float | None:
    """The costed result if there is one, otherwise the gross, else nothing.

    Recorded alongside so a pair decided on gross is never read as costed.
    """
    if leg.get("net_r") is not None:
        return float(leg["net_r"])
    if leg.get("gross_r") is not None:
        return float(leg["gross_r"])
    return None


def classify(option: dict | None, futures: dict | None) -> dict:
    """Which vehicle carried this idea better, or that neither did."""
    o_r = _best_available(option) if option else None
    f_r = _best_available(futures) if futures else None
    basis = {
        "option_r": o_r,
        "futures_r": f_r,
        "option_costed": bool(option and option.get("net_r") is not None),
        "futures_costed": bool(futures and futures.get("net_r") is not None),
        "material_r": MATERIAL_R,
    }
    if o_r is None and f_r is None:
        # No result on either side. If both expressions were refused, standing
        # aside was the whole outcome and that is a real answer, not a gap.
        both_refused = bool(option is None and futures is None)
        return {
            "verdict": BOTH_BAD if both_refused else UNKNOWN,
            "reason": ("neither vehicle produced a takeable plan, so NO_TRADE won "
                       "the idea" if both_refused
                       else "no resolved result on either vehicle"),
            "winner": NO_TRADE if both_refused else None,
            "basis": basis,
        }
    if o_r is None or f_r is None:
        return {
            "verdict": UNKNOWN,
            "reason": ("only the %s side resolved; a one-sided pair is not a "
                       "comparison" % (instrument_family.OPTIONS if o_r is not None
                                       else instrument_family.FUTURES)),
            "winner": None,
            "basis": basis,
        }
    if o_r <= 0 and f_r <= 0:
        return {
            "verdict": BOTH_BAD,
            "reason": "both vehicles lost on this idea; NO_TRADE was better than either",
            "winner": NO_TRADE,
            "basis": basis,
        }
    if abs(o_r - f_r) < MATERIAL_R:
        return {
            "verdict": BOTH_VALID,
            "reason": "the two vehicles finished within the material margin",
            "winner": None,
            "basis": basis,
        }
    better = OPTION_BETTER if o_r > f_r else FUTURES_BETTER
    return {
        "verdict": better,
        "reason": "decided on R, and only inside this pair",
        "winner": (instrument_family.OPTIONS if better == OPTION_BETTER
                   else instrument_family.FUTURES),
        "basis": basis,
    }


def _key(row: dict) -> str | None:
    mid = row.get("market_signal_id") or row.get("global_signal_id")
    return str(mid) if mid else None


def pair(option_rows: list[dict], futures_rows: list[dict]) -> list[dict]:
    """Form one record per market idea, with whichever vehicles resolved on it."""
    pairs: list[dict] = []
    used: set[int] = set()
    by_key: dict[str, int] = {}
    for idx, frow in enumerate(futures_rows):
        key = _key(frow)
        if key and key not in by_key:
            by_key[key] = idx

    for orow in option_rows:
        okey = _key(orow)
        match_idx: int | None = None
        match_on: str | None = None
        if okey and okey in by_key and by_key[okey] not in used:
            match_idx, match_on = by_key[okey], "market_signal_id"
        else:
            # Fall back to the same name, the same direction and a short window.
            # Deliberately narrow: a loose match invents comparisons.
            for idx, frow in enumerate(futures_rows):
                if idx in used:
                    continue
                if (frow.get("instrument") == orow.get("instrument")
                        and _direction(frow) == _direction(orow)
                        and _direction(orow) is not None
                        and abs(int(frow.get("signal_ts") or 0)
                                - int(orow.get("signal_ts") or 0)) <= PAIR_WINDOW_SEC):
                    match_idx, match_on = idx, "instrument_direction_window"
                    break
        futures_leg = None
        if match_idx is not None:
            used.add(match_idx)
            futures_leg = _futures_leg(futures_rows[match_idx])
        option_leg = _option_leg(orow)
        pairs.append({
            "market_signal_id": okey,
            "episode_id": orow.get("episode_id"),
            "instrument": orow.get("instrument"),
            "family": orow.get("family") or instrument_family.family(
                str(orow.get("instrument"))),
            "direction": _direction(orow),
            "session": orow.get("session"),
            "signal_time_ist": orow.get("signal_time_ist"),
            "matched_on": match_on,
            "option": option_leg,
            "futures": futures_leg,
            "no_trade": {"vehicle": NO_TRADE, "r": 0.0,
                         "note": "standing aside neither wins nor loses"},
            **classify(option_leg, futures_leg),
            "research_only": True,
        })

    # Futures ideas with no option expression at all are still evidence: they are
    # the cases where the option leg was refused and futures had a plan.
    for idx, frow in enumerate(futures_rows):
        if idx in used:
            continue
        futures_leg = _futures_leg(frow)
        pairs.append({
            "market_signal_id": _key(frow),
            "episode_id": frow.get("episode_id"),
            "instrument": frow.get("instrument"),
            "family": instrument_family.family(str(frow.get("instrument"))),
            "direction": _direction(frow),
            "session": frow.get("session"),
            "signal_time_ist": frow.get("signal_time_ist"),
            "matched_on": None,
            "option": None,
            "futures": futures_leg,
            "no_trade": {"vehicle": NO_TRADE, "r": 0.0,
                         "note": "standing aside neither wins nor loses"},
            **classify(None, futures_leg),
            "research_only": True,
        })
    return pairs


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def summarise(pairs: list[dict]) -> dict:
    """The §5 answer, with the sample it rests on stated first."""
    counts = {v: 0 for v in VERDICTS}
    for p in pairs:
        counts[p.get("verdict", UNKNOWN)] = counts.get(p.get("verdict", UNKNOWN), 0) + 1
    decided = [p for p in pairs
               if p.get("verdict") in (OPTION_BETTER, FUTURES_BETTER, BOTH_VALID)]
    two_sided = [p for p in pairs if p.get("option") and p.get("futures")
                 and p["option"].get("gross_r") is not None
                 and p["futures"].get("gross_r") is not None]
    by_family: dict[str, dict] = {}
    for p in pairs:
        fam = by_family.setdefault(str(p.get("family")),
                                   {v: 0 for v in VERDICTS} | {"pairs": 0})
        fam["pairs"] += 1
        fam[p.get("verdict", UNKNOWN)] += 1
    enough = len(two_sided) >= MIN_SAMPLE
    return {
        "pairs": len(pairs),
        "two_sided_pairs": len(two_sided),
        "verdict_counts": counts,
        "decided": len(decided),
        "option_r_median": _median([float(p["option"]["gross_r"])
                                    for p in two_sided]),
        "futures_r_median": _median([float(p["futures"]["gross_r"])
                                     for p in two_sided]),
        "by_family": by_family,
        "min_sample": MIN_SAMPLE,
        "sample_sufficient": enough,
        "shortfall": max(0, MIN_SAMPLE - len(two_sided)),
        "answer": (
            "INSUFFICIENT_SAMPLE" if not enough else
            FUTURES_BETTER if counts[FUTURES_BETTER] > counts[OPTION_BETTER] else
            OPTION_BETTER if counts[OPTION_BETTER] > counts[FUTURES_BETTER] else
            BOTH_VALID),
        "recommendation": None,
        "research_only": True,
        "note": ("premium R and index-point R are compared only inside a pair on "
                 "one idea and never summed into two books; no vehicle is "
                 "selected from this report and no futures order path exists"),
    }


def report(session: str | None = None) -> dict:
    option_rows = signal_journal.resolutions(session)
    futures_rows = [r for r in futures_outcomes.read_outcomes(session)
                    if r.get("event") == "RESOLVED"]
    pairs = pair(option_rows, futures_rows)
    out = summarise(pairs)
    out["session"] = session
    out["option_resolutions"] = len(option_rows)
    out["futures_resolutions"] = len(futures_rows)
    out["pair_window_sec"] = PAIR_WINDOW_SEC
    out["examples"] = pairs[:25]
    return out


def write_report(session: str | None = None) -> str:
    path = os.path.join(settings.data_dir, REPORT_JSON)
    try:
        os.makedirs(settings.data_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report(session), fh, indent=2, default=str)
    except OSError:
        return path
    return path
