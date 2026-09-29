"""Phase 7 Part 1-2 — is the recorded data fit to answer anything? RESEARCH ONLY.

Order matters here: Part 1 audits the option-chain dataset, Part 2 audits the
underlying series and the age of the data a decision was taken on. Both run
before any entry/exit measurement, because a study built on a 38%-incomplete
minute series measures the gaps, not the market.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from app.models import Candle

from .dataset import (
    REAL,
    SIM,
    UNKNOWN,
    ChainSeries,
    session_of,
)

# A gap longer than this is a session boundary (overnight / weekend / holiday),
# not a missing bar. MCX runs to 23:30 IST and equities to 15:30, so the largest
# legitimate intraday pause is well under an hour.
SESSION_BREAK_SEC = 3600


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def _quantiles(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    xs = sorted(values)

    def q(f: float) -> float:
        return round(xs[min(len(xs) - 1, int(f * len(xs)))], 2)

    return {"n": len(xs), "min": round(xs[0], 2), "p10": q(0.10), "p50": q(0.50),
            "p90": q(0.90), "max": round(xs[-1], 2)}


def chain_audit(series: dict[str, ChainSeries]) -> dict:
    """Part 1 — the REAL_BROKER chain dataset, never mixed with the simulator."""
    out: dict = {"by_provenance": {k: len(v) for k, v in series.items()}}
    for source in (REAL, SIM, UNKNOWN):
        s = series[source]
        if not len(s):
            out[source] = {"snapshots": 0}
            continue
        sessions = sorted({session_of(t) for t in s.ts})
        cadence = [s.ts[i] - s.ts[i - 1] for i in range(1, len(s.ts))]
        intraday = [d for d in cadence if 0 < d <= SESSION_BREAK_SEC]
        dupes = sum(1 for d in cadence if d == 0)
        gaps = sorted(
            ((d, s.ts[i]) for i, d in enumerate(cadence, start=1)
             if SESSION_BREAK_SEC >= d > 3 * max(1, int(_median(intraday) or 60))),
            reverse=True)[:5]

        fields = Counter()
        legs_total = 0
        strikes: set[float] = set()
        expiries: set[str] = set()
        sides = Counter()
        per_snapshot_strikes: list[float] = []
        for snap in s.legs:
            per_snapshot_strikes.append(float(len({
                float(leg.get("strike") or 0.0) for leg in snap.values()})))
            for leg in snap.values():
                legs_total += 1
                strikes.add(float(leg.get("strike") or 0.0))
                sides[str(leg.get("option_type") or "?")] += 1
                exp = leg.get("expiry")
                if exp:
                    expiries.add(str(exp))
                for name in ("premium", "oi", "volume", "iv", "delta", "bid",
                             "ask", "spread", "ltp"):
                    val = leg.get(name)
                    if val is not None and val != 0:
                        fields[name] += 1
        out[source] = {
            "snapshots": len(s),
            "sessions": len(sessions),
            "dates": sessions[:20],
            "duplicate_timestamps": dupes,
            "cadence_sec": _quantiles([float(d) for d in intraday]),
            "largest_intraday_gaps_sec": [g[0] for g in gaps],
            "legs": legs_total,
            "distinct_strikes": len(strikes),
            "strikes_per_snapshot": _quantiles(per_snapshot_strikes),
            "ce_legs": sides.get("CE", 0),
            "pe_legs": sides.get("PE", 0),
            "expiries_recorded": sorted(expiries)[:10],
            "field_availability_pct": {
                name: _pct(fields.get(name, 0), legs_total)
                for name in ("premium", "ltp", "oi", "volume", "iv", "delta",
                             "bid", "ask", "spread")
            },
        }
    real = out[REAL]
    out["verdict"] = {
        "real_chain_present": real["snapshots"] > 0,
        "real_sessions": real.get("sessions", 0),
        # The two fields that decide whether an exit study can be believed: a
        # premium path without a spread understates the cost of every exit, and
        # one session cannot separate a policy from that session's character.
        "spread_measurable": bool(
            real.get("field_availability_pct", {}).get("bid")
            and real.get("field_availability_pct", {}).get("ask")),
        "single_session_only": real.get("sessions", 0) <= 1,
    }
    return out


def _median(values: list) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    mid = len(xs) // 2
    return float(xs[mid]) if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def candle_audit(candles: list[Candle]) -> dict:
    """Part 2 — the underlying minute series: gaps, duplicates, ordering."""
    if not candles:
        return {"bars": 0}
    ts = [c.time for c in candles]
    dupes = sum(1 for i in range(1, len(ts)) if ts[i] == ts[i - 1])
    out_of_order = sum(1 for i in range(1, len(ts)) if ts[i] < ts[i - 1])
    missing = 0
    expected = 0
    biggest: list[tuple[int, int]] = []
    sessions: dict[str, int] = defaultdict(int)
    for i in range(1, len(ts)):
        d = ts[i] - ts[i - 1]
        sessions[session_of(ts[i])] += 1
        if d <= 0 or d > SESSION_BREAK_SEC:
            continue
        expected += d // 60
        if d > 60:
            gap = int(d // 60) - 1
            missing += gap
            biggest.append((gap, ts[i - 1]))
    biggest.sort(reverse=True)
    zero_range = sum(1 for c in candles if c.high == c.low)
    bad = sum(1 for c in candles
              if c.high < c.low or c.close > c.high or c.close < c.low)
    return {
        "bars": len(candles),
        "sessions": len(sessions),
        "expected_bars_in_sessions": expected,
        "missing_bars": missing,
        "missing_pct": _pct(missing, expected),
        "duplicate_timestamps": dupes,
        "out_of_order": out_of_order,
        "zero_range_bars": zero_range,
        "impossible_bars": bad,
        "largest_gaps_bars": [g[0] for g in biggest[:5]],
        "first_ist": None if not ts else session_of(ts[0]),
        "last_ist": None if not ts else session_of(ts[-1]),
    }


def freshness_audit(candles: list[Candle], real: ChainSeries,
                    fresh_ms: float) -> dict:
    """Part 2 — the age of the data a decision on each bar would have used.

    Phase 6 measured 1,769 ms median exchange-to-receive latency while the FRESH
    gate is defined on last-tick age, so a decision can be labelled FRESH on a
    quote already older than the FRESH bar. That comparison cannot be made from
    the recorded DB alone (it has no receive timestamp), so what is measurable is
    reported and what is not is named rather than estimated.
    """
    ages: list[float] = []
    matched = 0
    for c in candles:
        age = real.age_at(c.time)
        if age is None:
            continue
        matched += 1
        ages.append(float(age))
    return {
        "bars": len(candles),
        "bars_with_real_chain": matched,
        "chain_age_sec": _quantiles(ages),
        "chain_older_than_fresh_gate_pct": _pct(
            sum(1 for a in ages if a * 1000.0 > fresh_ms), len(ages)),
        "fresh_gate_ms": fresh_ms,
        "not_measurable_from_db": [
            "exchange_to_receive latency (no receive timestamp is stored)",
            "REST vs WebSocket route per tick",
            "intrabar option high/low (snapshots are ~60s closes)",
            "bid/ask spread (the stored leg carries a single premium)",
        ],
    }


def stale_flag(chain_age_sec: int | None, underlying_age_sec: int | None,
               fresh_ms: float) -> str:
    """Label, never a filter: STALE observations stay in the population and are
    reported separately, so a comparison is not quietly built on fresh rows only."""
    if chain_age_sec is None or underlying_age_sec is None:
        return "NO_DATA"
    worst = max(chain_age_sec, underlying_age_sec) * 1000.0
    if worst <= fresh_ms:
        return "FRESH"
    if worst <= 2 * fresh_ms:
        return "AGEING"
    return "STALE"
