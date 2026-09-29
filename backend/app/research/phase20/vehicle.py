"""Phase 20 §3 — Futures vs CE vs PE at the SAME market signal. RESEARCH ONLY.

Five years of history said no instrument has a validated directional edge, which
leaves the vehicle and the execution as the only places expectancy can come from.
This module answers the question that follows: given one market signal, which
vehicle actually paid after costs — the futures contract, the call, or the put?

Two joins are refused rather than approximated, because either would manufacture
a comparison:

* **different signals.** A futures leg and an option leg belong to the same signal
  only when they are the same instrument, the same direction, and their signal
  timestamps are within :data:`SAME_SIGNAL_WINDOW_SEC`. A futures entry an hour
  later is a different read of the market and is reported as its own row with the
  option side ``NOT_CAPTURED``.
* **different cost bases.** A leg counts as measured only when its round trip was
  actually costed. Comparing a costed option leg against a gross futures leg would
  hand futures a free win of the exact size of the thing being studied.

Nothing here decides, sizes or places anything, and the comparison is read from
the paper books after resolution — never from a live decision.
"""
from __future__ import annotations

from app.analysis import instrument_family as fam
from app.research.phase17 import capture as p17capture
from app.research.phase17 import schema as p17schema
from app.research.phase20 import ranking as p20rank

CE = p17schema.CE
PE = p17schema.PE
FUTURES = p17schema.FUTURES
VEHICLES: tuple[str, ...] = (FUTURES, CE, PE)

# How far apart two legs' signal timestamps may be and still be one signal. Five
# minutes: the option and futures observers run off the same tick loop but resolve
# their plans independently, so a small skew is the same signal while a large one
# is a fresh read of a market that has since moved.
SAME_SIGNAL_WINDOW_SEC = 300

NOT_CAPTURED = "NOT_CAPTURED"
COST_NOT_MEASURED = "COST_NOT_MEASURED"
DIRECTION_UNKNOWN = "DIRECTION_UNKNOWN"

UP = "UP"
DOWN = "DOWN"
# The two books name their direction differently — the option capture stores the
# market read (BULLISH/BEARISH), the futures book the position (LONG/SHORT). The
# map is explicit so an unrecognised word becomes DIRECTION_UNKNOWN and refuses to
# join, rather than defaulting to one side and pairing a call against a short.
_DIRECTIONS: dict[str, str] = {
    "BULLISH": UP, "LONG": UP, "BUY": UP, "UP": UP,
    "BEARISH": DOWN, "SHORT": DOWN, "SELL": DOWN, "DOWN": DOWN,
}

# Below this many resolved same-signal comparisons an instrument gets no
# vehicle preference at all. A vehicle "winning" 3 of 5 signals is noise, and
# publishing it as a finding is how a tool starts recommending puts on Tuesdays.
MIN_COMPARISONS = p20rank.MIN_COSTED_PAPER_ROWS

INSUFFICIENT_DATA = p20rank.INSUFFICIENT_DATA


def _net_r(row: dict) -> tuple[float | None, str | None]:
    """Net R for one resolved leg, or why it cannot be compared."""
    if row.get("cost_status") not in (None, p17schema.COST_MEASURED):
        return None, COST_NOT_MEASURED
    value = row.get("net_r")
    if not isinstance(value, (int, float)):
        return None, COST_NOT_MEASURED
    # R is net points divided by risk, so a risk that is not a positive distance
    # inverts the sign rather than scaling it: rows recorded before the tracker
    # refused a stop above its own entry report losses as gains. They exist on
    # disk, so they are refused here rather than compared.
    risk = row.get("risk")
    if isinstance(risk, (int, float)) and float(risk) <= 0:
        return None, COST_NOT_MEASURED
    return float(value), None


def _signal_ts(row: dict) -> float | None:
    for key in ("signal_ts", "entry_ts"):
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _session_of(row: dict, ts: float) -> str:
    """IST session date for a leg.

    Futures legs store one; option legs do not, so it is derived from the signal
    timestamp with the same IST clock the capture layer uses — deriving both ways
    from one function is what stops a 15:20 leg landing in two different days.
    """
    value = row.get("session")
    if isinstance(value, str) and value:
        return value
    return p17capture.ist_parts(ts)[0]


def direction_of(row: dict) -> str | None:
    """UP / DOWN, or None when the row's direction cannot be read."""
    return _DIRECTIONS.get(str(row.get("direction") or "").upper())


def _key(row: dict, ts: float, direction: str) -> tuple[str, str, str]:
    return (
        str(row.get("instrument") or "").upper(),
        _session_of(row, ts),
        direction,
    )


def pair(option_rows: list[dict], futures_rows: list[dict], *,
         window_sec: int = SAME_SIGNAL_WINDOW_SEC) -> list[dict]:
    """One record per market signal, with whichever vehicles were captured.

    ``option_rows`` are resolved Phase 17 paper legs (CE and PE at the same
    instant), ``futures_rows`` resolved Phase 19 futures paper legs.
    """
    buckets: dict[tuple[str, str, str], list[dict]] = {}

    def bucket(row: dict, ts: float, direction: str) -> dict:
        base = _key(row, ts, direction)
        same = buckets.setdefault(base, [])
        for rec in same:
            if abs(rec["signal_ts"] - ts) <= window_sec:
                return rec
        rec = {
            "instrument": base[0],
            "family": fam.family(base[0]),
            "session": base[1],
            "direction": base[2],
            "signal_ts": ts,
            "legs": {},
            "detail": {},
            "uncosted": {},
            "not_comparable_because": {},
        }
        same.append(rec)
        return rec

    for index, row in enumerate([*option_rows, *futures_rows]):
        ts = _signal_ts(row)
        if ts is None:
            continue
        vehicle = str(row.get("vehicle") or "").upper()
        if vehicle not in VEHICLES:
            continue
        direction = direction_of(row)
        # An unreadable direction gets its own bucket: it is still reported as an
        # outcome, but it never pairs with another vehicle.
        rec = bucket(row, ts, direction or f"{DIRECTION_UNKNOWN}_{index}")
        net_r, why = _net_r(row)
        if net_r is None:
            # Held aside rather than recorded straight away: another leg of the
            # same vehicle at this signal may well be costed, and "uncosted" would
            # then be the wrong reason to print.
            rec["uncosted"][vehicle] = why
            continue
        rec["detail"].setdefault(vehicle, []).append({
            "net_r": net_r,
            "gross_r": row.get("gross_r"),
            "entry_price": row.get("entry_price"),
            "exit_price": row.get("exit_price"),
            "strike": row.get("strike"),
            "days_to_expiry": row.get("days_to_expiry"),
            "mfe_r": row.get("mfe_r", row.get("mfe")),
            "mae_r": row.get("mae_r", row.get("mae")),
            "exit_reason": row.get("exit_reason"),
            "entry_quality": row.get("entry_quality"),
        })

    out: list[dict] = []
    for rec in [r for group in buckets.values() for r in group]:
        legs = rec["legs"]
        # One signal can carry several legs of the same vehicle — two call strikes,
        # say. The vehicle is represented by the MEAN of them, not by its best: a
        # comparison that took the luckiest strike per vehicle would be a
        # comparison of strike selection dressed up as a comparison of vehicles.
        for name, own in rec["detail"].items():
            values = [leg["net_r"] for leg in own]
            legs[name] = {
                "legs": len(own),
                "net_r": round(sum(values) / len(values), 4),
                "best_leg_net_r": max(values),
                "worst_leg_net_r": min(values),
                "detail": own,
            }
        for vehicle in VEHICLES:
            if vehicle in legs:
                continue
            rec["not_comparable_because"][vehicle] = rec["uncosted"].get(
                vehicle, NOT_CAPTURED)
        rec.pop("uncosted")
        # One vehicle alone is an outcome, not a comparison.
        rec["comparable"] = len(legs) >= 2
        rec["best_vehicle"] = (
            max(legs, key=lambda v: legs[v]["net_r"]) if rec["comparable"] else None
        )
        rec["net_r_by_vehicle"] = {v: legs[v]["net_r"] for v in sorted(legs)}
        out.append(rec)
    out.sort(key=lambda r: (r["instrument"], r["signal_ts"]))
    return out


def by_instrument(pairs: list[dict]) -> list[dict]:
    """Per instrument, how each vehicle did on the signals it was captured for.

    No cross-instrument total: a vehicle preference is a property of one market's
    spread and lot size, and averaging CRUDEOIL with KALYANKJIL describes neither.
    """
    per: dict[str, list[dict]] = {}
    for rec in pairs:
        per.setdefault(rec["instrument"], []).append(rec)
    out: list[dict] = []
    for name, own in sorted(per.items()):
        comparable = [r for r in own if r["comparable"]]
        vehicles: dict[str, dict] = {}
        for vehicle in VEHICLES:
            # One value per SIGNAL — the mean of that signal's legs of this
            # vehicle, so a signal on which five calls were open does not outvote
            # four signals on which one was.
            values = [r["legs"][vehicle]["net_r"] for r in own
                      if vehicle in r["legs"]]
            legs = sum(r["legs"][vehicle]["legs"] for r in own
                       if vehicle in r["legs"])
            wins = sum(1 for r in comparable if r["best_vehicle"] == vehicle)
            gains = sum(v for v in values if v > 0)
            losses = -sum(v for v in values if v < 0)
            vehicles[vehicle] = {
                "resolved": len(values),
                "legs": legs,
                "mean_net_r": (round(sum(values) / len(values), 4)
                               if values else None),
                "positive_pct": (round(100.0 * sum(1 for v in values if v > 0)
                                       / len(values), 2) if values else None),
                "profit_factor": (round(gains / losses, 3) if losses > 0
                                  else None),
                "best_on_signals": wins,
            }
        enough = len(comparable) >= MIN_COMPARISONS
        ranked = [v for v in VEHICLES
                  if vehicles[v]["mean_net_r"] is not None]
        ranked.sort(key=lambda v: vehicles[v]["mean_net_r"], reverse=True)
        out.append({
            "instrument": name,
            "family": fam.family(name),
            "signals": len(own),
            "comparable_signals": len(comparable),
            "enough_to_prefer_a_vehicle": enough,
            "by_vehicle": vehicles,
            # Only stated when there is enough same-signal evidence; otherwise the
            # ordering is printed for reading and the preference is withheld.
            "preferred_vehicle": ranked[0] if (enough and ranked) else None,
            "ordering_so_far": ranked,
            "verdict": None if enough else INSUFFICIENT_DATA,
            "missing_because": _missing_tally(own),
        })
    return out


def _missing_tally(pairs: list[dict]) -> dict[str, int]:
    """Why vehicles were absent, so a thin comparison names its own gap."""
    tally: dict[str, int] = {}
    for rec in pairs:
        for vehicle, why in rec["not_comparable_because"].items():
            tally[f"{vehicle}_{why}"] = tally.get(f"{vehicle}_{why}", 0) + 1
    return dict(sorted(tally.items()))


def by_family(pairs: list[dict]) -> dict[str, list[dict]]:
    """The same per-instrument records, grouped INDEX / MCX / STOCK."""
    records = by_instrument(pairs)
    out: dict[str, list[dict]] = {f: [] for f in (*fam.FAMILIES, fam.UNKNOWN)}
    for rec in records:
        out[rec["family"]].append(rec)
    return out
