"""CE vs PE vs FUTURES for one candidate, on executable prices only.

The rule, stated before the numbers:

    an option is entered at the ASK and exited at the BID, on a book that was
    actually recorded at the signal's own timestamp. A vehicle whose book was
    never captured does not get a modelled spread and does not get a mid price —
    it is reported as UNMEASURED and excluded from every net figure. If no
    vehicle is measurable and positive after costs, the answer is NO_TRADE.

Mid prices are the reason an earlier read of this book looked profitable. A
round trip crossing a 5% spread twice is a real loss that a mid-to-mid
calculation never charges, so mid never appears as a result here — not as a
fallback, not as a fill for a gap in coverage, not "for comparison".

Futures are a separate honesty problem. The capture on file records a futures
price but no futures depth, so futures net R carries statutory charges and
configured slippage but no measured spread. That is stated on every futures row
as ``MODELLED_CHARGES_NO_DEPTH`` and futures are ranked only against the
disclosure, never presented as an equally-measured vehicle.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

CALL = "CALL"
PUT = "PUT"
FUTURES = "FUTURES"
VEHICLES = (CALL, PUT, FUTURES)

NO_TRADE = "NO_TRADE"

RECORDED_BOOK = "RECORDED_BOOK"
ASK_IN_BID_OUT = "ASK_IN_BID_OUT_RECORDED_BOOK"
MODELLED_NO_DEPTH = "MODELLED_CHARGES_NO_DEPTH"

MEASURED = "MEASURED"
UNMEASURED = "UNMEASURED"

# How close a captured vehicle comparison has to sit to a pool candidate before
# the two are treated as the same opportunity. Three minutes: the capture is
# written on the signal tick, and anything further away is a different setup.
JOIN_TOLERANCE_SEC = 180

# A candidate is only admitted to the paper book when the winning vehicle's net
# R after real costs clears this. Zero, not a fitted margin: the study is asking
# whether the setup pays for itself at all.
MIN_NET_R = 0.0


def load(path: str | Path) -> list[dict]:
    """Every captured CE/PE/FUTURES comparison row, oldest first."""
    file = Path(path)
    if not file.exists():
        return []
    rows: list[dict] = []
    with file.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return sorted(rows, key=lambda r: (str(r.get("session") or ""),
                                       int(r.get("signal_ts") or 0)))


def measurable(entrant: dict) -> bool:
    """True only when this vehicle's net R came from a recorded book."""
    return (entrant.get("book_source") == RECORDED_BOOK
            and entrant.get("cost_treatment") == ASK_IN_BID_OUT
            and isinstance(entrant.get("net_r"), (int, float)))


def status(entrant: dict) -> str:
    return MEASURED if measurable(entrant) else UNMEASURED


def choose(row: dict) -> dict:
    """The vehicle decision for one captured opportunity.

    Ranks only measured vehicles. Futures are listed with their disclosure and
    are eligible only when no measured option qualifies, because a futures net R
    computed without depth is not comparable to an option net R computed across
    a real spread — ranking them together would let the un-measured vehicle win
    by being cheaper to pretend about.
    """
    entrants = [e for e in (row.get("entrants") or []) if isinstance(e, dict)]
    table = []
    for e in entrants:
        table.append({
            "vehicle": e.get("vehicle"),
            "status": status(e),
            "net_r": e.get("net_r"),
            "gross_r": e.get("gross_r"),
            "cost_r": e.get("cost_r"),
            "spread_pct_of_risk": e.get("spread_pct_of_risk"),
            "entry_quality": e.get("entry_quality"),
            "target_before_stop": e.get("target_before_stop"),
            "outcome": e.get("outcome"),
            "mfe_r": e.get("mfe_r"),
            "mae_r": e.get("mae_r"),
            "minutes_to_resolution": e.get("minutes_to_resolution"),
            "tradingsymbol": e.get("tradingsymbol"),
            "strike": e.get("strike"),
            "expiry": e.get("expiry"),
            "cost_treatment": e.get("cost_treatment"),
            "book_source": e.get("book_source"),
        })
    measured = [e for e in table
                if e["status"] == MEASURED and e["vehicle"] in (CALL, PUT)]
    best = None
    reasons: list[str] = []
    if measured:
        best = max(measured, key=lambda e: float(e["net_r"]))
        if float(best["net_r"]) <= MIN_NET_R:
            reasons.append(f"BEST_MEASURED_NET_R_{best['net_r']}_NOT_POSITIVE")
            best = None
        else:
            reasons.append(f"{best['vehicle']}_MEASURED_NET_R_{best['net_r']}")
    else:
        reasons.append("NO_MEASURED_OPTION_BOOK")
    if best is None:
        fut = next((e for e in table if e["vehicle"] == FUTURES), None)
        if fut is not None and isinstance(fut.get("net_r"), (int, float)):
            reasons.append(f"FUTURES_NET_R_{fut['net_r']}_{MODELLED_NO_DEPTH}")
    return {
        "session": row.get("session"),
        "signal_ts": row.get("signal_ts"),
        "instrument": row.get("instrument"),
        "direction": row.get("direction"),
        "vehicle": best["vehicle"] if best else NO_TRADE,
        "net_r": best["net_r"] if best else None,
        "reasons": reasons,
        "entrants": table,
        "measured_vehicles": len([e for e in table if e["status"] == MEASURED]),
        "selection_rule": (
            "highest net R after real costs among options entered at the ask "
            "and exited at the bid on a recorded book; futures disclosed "
            "without depth and never ranked against a measured option; "
            "NO_TRADE when nothing measured is positive"
        ),
    }


def join(candidates: list[dict], captures: list[dict], *,
         tolerance_sec: int = JOIN_TOLERANCE_SEC) -> list[dict]:
    """Attach each captured vehicle comparison to the pool candidate it belongs to.

    An unjoined capture is not discarded — it is returned with
    ``candidate`` None and counted in the coverage report, because the gap
    between "opportunities the setup found" and "opportunities a real book was
    captured for" is the single biggest limit on this study and hiding it would
    make the sample look complete.
    """
    index: dict[tuple[str, str], list[dict]] = {}
    for c in candidates:
        key = (str(c.get("instrument") or "").upper(), str(c.get("session") or ""))
        index.setdefault(key, []).append(c)
    out = []
    for cap in captures:
        key = (str(cap.get("instrument") or "").upper(),
               str(cap.get("session") or ""))
        ts = cap.get("signal_ts")
        match = None
        if isinstance(ts, (int, float)):
            best_delta = None
            for c in index.get(key, []):
                c_ts = c.get("entry_ts")
                if not isinstance(c_ts, (int, float)):
                    continue
                delta = abs(float(c_ts) - float(ts))
                if delta <= tolerance_sec and (best_delta is None
                                               or delta < best_delta):
                    best_delta, match = delta, c
        decision = choose(cap)
        decision["candidate"] = match
        decision["joined"] = match is not None
        out.append(decision)
    return out


def _stats(values: list[float]) -> dict:
    if not values:
        return {"legs": 0, "net_expectancy_r": None, "profit_factor": None,
                "win_pct": None, "avg_win_r": None, "avg_loss_r": None}
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    loss = -sum(losses)
    return {
        "legs": len(values),
        "net_expectancy_r": round(mean(values), 3),
        "net_total_r": round(sum(values), 2),
        "profit_factor": round(sum(wins) / loss, 2) if loss > 0 else None,
        "win_pct": round(100.0 * len(wins) / len(values), 1),
        "avg_win_r": round(mean(wins), 3) if wins else None,
        "avg_loss_r": round(mean(losses), 3) if losses else None,
    }


def by_vehicle(decisions: list[dict]) -> list[dict]:
    """Per-vehicle net economics, measured legs only, with coverage beside them."""
    out = []
    for vehicle in VEHICLES:
        legs = [e for d in decisions for e in d["entrants"]
                if e["vehicle"] == vehicle]
        measured = [float(e["net_r"]) for e in legs
                    if e["status"] == MEASURED
                    and isinstance(e["net_r"], (int, float))]
        out.append({
            "vehicle": vehicle,
            "captured_legs": len(legs),
            "measured_legs": len(measured),
            "measured_pct": (round(100.0 * len(measured) / len(legs), 1)
                             if legs else None),
            "pricing": (ASK_IN_BID_OUT if vehicle in (CALL, PUT)
                        else MODELLED_NO_DEPTH),
            **_stats(measured),
        })
    return out


def coverage(decisions: list[dict]) -> dict:
    """How much of this study rests on real books. Printed before any verdict."""
    total = len(decisions)
    joined = sum(1 for d in decisions if d["joined"])
    with_book = sum(1 for d in decisions if d["measured_vehicles"] > 0)
    chosen = [d for d in decisions if d["vehicle"] != NO_TRADE]
    return {
        "captured_opportunities": total,
        "joined_to_candidate": joined,
        "joined_pct": round(100.0 * joined / total, 1) if total else None,
        "with_measured_option_book": with_book,
        "measured_book_pct": (round(100.0 * with_book / total, 1)
                              if total else None),
        "selected": len(chosen),
        "no_trade": total - len(chosen),
        "no_trade_pct": (round(100.0 * (total - len(chosen)) / total, 1)
                         if total else None),
    }


def selected_stats(decisions: list[dict]) -> dict:
    """The book the vehicle rule would actually have traded, net, on real prices."""
    values = [float(d["net_r"]) for d in decisions
              if d["vehicle"] != NO_TRADE and isinstance(d["net_r"], (int, float))]
    sessions = sorted({str(d["session"]) for d in decisions if d.get("session")})
    return {
        **_stats(values),
        "sessions": len(sessions),
        "trades_per_day": (round(len(values) / len(sessions), 2)
                           if sessions else None),
        "pricing": ASK_IN_BID_OUT,
        "mid_used_anywhere": False,
    }


def run(candidates: list[dict], captures: list[dict]) -> dict:
    decisions = join(candidates, captures)
    setup_only = [d for d in decisions
                  if d["joined"] and d["candidate"].get("p22_label") == "PULLBACK"]
    return {
        "coverage": coverage(decisions),
        "by_vehicle": by_vehicle(decisions),
        "selected": selected_stats(decisions),
        "setup_joined": {
            "opportunities": len(setup_only),
            "note": ("captures that joined a candidate the frozen definition "
                     "labels PULLBACK; the live capture window and the 5-year "
                     "pool overlap only where both exist"),
            **(selected_stats(setup_only) if setup_only else {}),
        },
        "decisions": decisions,
    }
