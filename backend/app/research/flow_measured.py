"""Re-price the recorded Flow book against the book the market actually quoted.

Every cost number this system has published so far carried the same asterisk: the
spread was a family median, because no captured chain row had a bid/ask to read.
That asterisk is now removable on any leg whose entry candle exists in the
captured chain — the coverage audit reports a usable book on ~99.5% of captured
rows — so this module stops modelling the spread and reads it.

What it does, and only reads:

* joins each recorded Flow leg (``data/flow_signals.jsonl``) to the captured
  option row for its own strike and side at its own entry candle;
* charges that leg the round trip built from the **quoted** ask-minus-bid, and
  reports the same leg's assumed-median cost beside it, so the size of the
  modelling error is visible rather than asserted;
* replays the entry-economics gate on those measured terms — delta from the
  captured chain, one-minute range from the captured futures bars — and attributes
  the book's net result to the legs the gate would have refused and the legs it
  would have allowed.

Three refusals are deliberate:

* a leg whose entry candle is not in the captured chain is ``UNMATCHED`` and is
  excluded from every measured total instead of being back-filled with a median;
* a matched row whose book is missing, zero or crossed is ``ASSUMED`` — a zero
  spread is the most flattering number in the model and it is never real;
* a leg with no delta or no range is ``UNMEASURED`` and is never counted as one
  the gate would have refused, because an absent measurement is not evidence
  against a trade.

The gate counterfactual is an attribution of what did happen, not a backtest of
what would have happened: refusing a leg also changes which leg comes next, and
that second-order effect is not modelled here. It is stated in the output.
"""
from __future__ import annotations

import json
import re
from bisect import bisect_left
from pathlib import Path
from statistics import median

from app.analysis import option_costs as _costs
from app.config import settings
from app.market.instruments import get_spec
from app.research.chain_audit import usable_spread
from app.research.store import ResearchStore, store

MATCHED = "MATCHED"
UNMATCHED = "UNMATCHED"
OK = "ECONOMIC"
REFUSED = "UNECONOMIC"
UNMEASURED = "UNMEASURED"

_SYMBOL = re.compile(r"(\d+(?:\.\d+)?)(CE|PE)$")


def parse_symbol(option_symbol: str) -> tuple[float, str] | None:
    """Strike and side out of a Flow option symbol, or None if it is unreadable.

    Flow writes symbols like ``NIFTY23850PE``; the expiry-bearing variants end the
    same way. Only the trailing strike+side is parsed, because that plus the
    instrument and the candle is what identifies a captured chain row. An
    unreadable symbol returns None rather than a guessed strike, since matching
    the wrong strike would price the leg against a different contract.
    """
    if not isinstance(option_symbol, str):
        return None
    m = _SYMBOL.search(option_symbol.strip().upper())
    if m is None:
        return None
    return float(m.group(1)), m.group(2)


def load_legs(path: str | Path) -> list[dict]:
    """Completed Flow legs from the JSONL ledger. Skips rows that never closed."""
    out: list[dict] = []
    p = Path(path)
    if not p.exists():
        return out
    with p.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("ts_close") is not None:
                out.append(row)
    return out


def chain_series(instrument: str, *, st: ResearchStore | None = None) -> dict:
    """Captured option rows grouped by (strike, side), each sorted by ts."""
    st = st or store()
    rows = st.db.query(
        "SELECT ts, strike, option_type, close, delta, bid, ask, source "
        "FROM mcx_options WHERE instrument=? ORDER BY ts",
        (instrument,),
    )
    series: dict[tuple[float, str], dict] = {}
    for r in rows:
        try:
            key = (float(r["strike"]), str(r["option_type"]).upper())
        except (TypeError, ValueError, KeyError):
            continue
        slot = series.setdefault(key, {"ts": [], "rows": []})
        slot["ts"].append(int(r["ts"]))
        slot["rows"].append(r)
    return series


def futures_ranges(instrument: str, *, st: ResearchStore | None = None) -> dict:
    """Captured futures bar ranges, sorted by ts, for the typical-range term."""
    st = st or store()
    rows = st.db.query(
        "SELECT ts, high, low FROM mcx_futures WHERE instrument=? ORDER BY ts",
        (instrument,),
    )
    stamps: list[int] = []
    spans: list[float] = []
    for r in rows:
        hi, lo = r.get("high"), r.get("low")
        if not isinstance(hi, (int, float)) or not isinstance(lo, (int, float)):
            continue
        if hi < lo:
            continue
        stamps.append(int(r["ts"]))
        spans.append(float(hi) - float(lo))
    return {"ts": stamps, "span": spans}


def nearest(stamps: list[int], want: int) -> int | None:
    """Index of the ts closest to ``want``, or None on an empty series."""
    if not stamps:
        return None
    i = bisect_left(stamps, want)
    if i == 0:
        return 0
    if i >= len(stamps):
        return len(stamps) - 1
    before, after = stamps[i - 1], stamps[i]
    return i - 1 if (want - before) <= (after - want) else i


def range_before(fut: dict, ts: int, bars: int) -> float | None:
    """Median range of the captured bars at or before ``ts``. None if too few."""
    stamps = fut["ts"]
    if not stamps:
        return None
    i = bisect_left(stamps, ts + 1)
    window = [s for s in fut["span"][max(0, i - max(1, bars)):i] if s > 0]
    if not window:
        return None
    return round(median(window), 3)


def _lot_size(instrument: str, leg: dict) -> int | None:
    size = leg.get("lot_size")
    if isinstance(size, int) and size > 0:
        return size
    try:
        spec = int(get_spec(instrument).lot_size)
    except (AttributeError, TypeError, ValueError):
        return None
    return spec if spec > 0 else None


def _entry_stamps(leg: dict) -> list[int]:
    """The candidate entry timestamps a leg can be matched on.

    The ledger carries both a wall-clock open and the candle time it was opened
    against, and the two use different conventions in older rows. Both are tried
    and the closer match wins, with the gap reported, so a bad join shows up as a
    large gap instead of quietly pricing the leg against another minute.
    """
    out = []
    for key in ("ts_open", "open_ctime"):
        v = leg.get(key)
        if isinstance(v, (int, float)):
            out.append(int(v))
    return out


def reprice(
    legs: list[dict],
    *,
    st: ResearchStore | None = None,
    tolerance_sec: int = 180,
    bars: int = 0,
) -> dict:
    """Charge each leg the quoted spread where one exists; attribute the gate.

    Returns per-leg rows plus cohort totals. Nothing is written anywhere.
    """
    st = st or store()
    bars = bars or int(settings.flow_expected_move_candles)
    multiple = float(settings.flow_cost_multiple)
    chains: dict[str, dict] = {}
    futs: dict[str, dict] = {}
    priced: list[dict] = []

    for leg in legs:
        instrument = str(leg.get("instrument") or "")
        parsed = parse_symbol(str(leg.get("option_symbol") or ""))
        entry = leg.get("entry_premium")
        final = leg.get("final_premium")
        lots = int(leg.get("lots") or settings.flow_paper_lots or 1)
        lot_size = _lot_size(instrument, leg)
        if not instrument or parsed is None or not isinstance(entry, (int, float)):
            continue
        strike, side = parsed

        if instrument not in chains:
            chains[instrument] = chain_series(instrument, st=st)
            futs[instrument] = futures_ranges(instrument, st=st)
        slot = chains[instrument].get((strike, side))

        row = None
        gap = None
        if slot is not None:
            for want in _entry_stamps(leg):
                i = nearest(slot["ts"], want)
                if i is None:
                    continue
                d = abs(slot["ts"][i] - want)
                if gap is None or d < gap:
                    gap, row = d, slot["rows"][i]
        if row is not None and gap is not None and gap > tolerance_sec:
            row, gap = None, gap

        quoted = usable_spread(row.get("bid"), row.get("ask")) if row else None
        measured = _costs.round_trip(instrument, float(entry), final, lot_size or 0,
                                     lots, quoted_spread=quoted)
        assumed = _costs.round_trip(instrument, float(entry), final, lot_size or 0,
                                    lots)
        qty = (lot_size or 0) * max(1, lots)
        gross = leg.get("final_rupees")
        gross = float(gross) if isinstance(gross, (int, float)) else None

        delta = row.get("delta") if row else None
        delta = (abs(float(delta))
                 if isinstance(delta, (int, float)) and delta != 0 else None)
        rng = (range_before(futs[instrument], int(row["ts"]), bars)
               if row is not None else None)
        expected = round(delta * rng, 2) if (delta and rng) else None
        cost_points = measured.cost_points if measured else None
        ratio = (round(expected / cost_points, 2)
                 if expected is not None and cost_points else None)
        verdict = UNMEASURED
        if ratio is not None:
            verdict = OK if ratio >= multiple else REFUSED

        priced.append({
            "instrument": instrument,
            "option_symbol": leg.get("option_symbol"),
            "side": side,
            "ts_open": leg.get("ts_open"),
            "entry_premium": float(entry),
            "final_premium": final,
            "qty": qty,
            "match": MATCHED if row is not None else UNMATCHED,
            "match_gap_sec": gap if row is not None else None,
            "chain_source": (row.get("source") if row else None),
            "quoted_spread_points": quoted,
            "cost_basis": (measured.spread_source if measured else None),
            "cost_points": cost_points,
            "cost_rupees": measured.cost_rupees if measured else None,
            "assumed_cost_points": assumed.cost_points if assumed else None,
            "assumed_cost_rupees": assumed.cost_rupees if assumed else None,
            "gross_rupees": gross,
            "net_rupees": (round(gross - measured.cost_rupees, 2)
                           if gross is not None and measured else None),
            "delta": round(delta, 3) if delta else None,
            "range_points": rng,
            "expected_move_points": expected,
            "ratio": ratio,
            "verdict": verdict,
        })

    return {"legs": priced, **_totals(priced, multiple)}


def _agg(rows: list[dict]) -> dict:
    net = [r["net_rupees"] for r in rows if r["net_rupees"] is not None]
    gross = [r["gross_rupees"] for r in rows if r["gross_rupees"] is not None]
    cost = [r["cost_rupees"] for r in rows if r["cost_rupees"] is not None]
    return {
        "legs": len(rows),
        "gross_rupees": round(sum(gross), 2) if gross else None,
        "cost_rupees": round(sum(cost), 2) if cost else None,
        "net_rupees": round(sum(net), 2) if net else None,
        "net_win_pct": (round(100.0 * sum(1 for v in net if v > 0) / len(net), 1)
                        if net else None),
        "median_cost_pct_of_premium": (
            round(median([100.0 * r["cost_points"] / r["entry_premium"]
                          for r in rows
                          if r["cost_points"] and r["entry_premium"] > 0]), 2)
            if any(r["cost_points"] for r in rows) else None
        ),
    }


def _totals(priced: list[dict], multiple: float) -> dict:
    matched = [r for r in priced if r["match"] == MATCHED]
    measured = [r for r in matched if r["cost_basis"] == _costs.MEASURED]
    gaps = [r["match_gap_sec"] for r in matched if r["match_gap_sec"] is not None]
    err = [r["cost_points"] - r["assumed_cost_points"]
           for r in measured
           if r["cost_points"] is not None and r["assumed_cost_points"] is not None]
    refused = [r for r in measured if r["verdict"] == REFUSED]
    allowed = [r for r in measured if r["verdict"] == OK]
    unmeasured = [r for r in measured if r["verdict"] == UNMEASURED]
    book_net = _agg(measured)["net_rupees"]
    saved = _agg(refused)["net_rupees"]
    return {
        "join": {
            "legs": len(priced),
            "matched": len(matched),
            "unmatched": len(priced) - len(matched),
            "measured_book": len(measured),
            "matched_but_no_book": len(matched) - len(measured),
            "median_match_gap_sec": (round(median(gaps), 1) if gaps else None),
        },
        "cost": {
            "measured": _agg(measured),
            "assumed_cost_rupees": (
                round(sum(r["assumed_cost_rupees"] for r in measured
                          if r["assumed_cost_rupees"] is not None), 2)
                if measured else None
            ),
            "median_error_points": (round(median(err), 2) if err else None),
            "quoted_spread_median_points": (
                round(median([r["quoted_spread_points"] for r in measured
                              if r["quoted_spread_points"] is not None]), 2)
                if measured else None
            ),
        },
        "gate": {
            "required_multiple": round(multiple, 2),
            "refused": _agg(refused),
            "allowed": _agg(allowed),
            "unmeasured": _agg(unmeasured),
            "book_net_rupees": book_net,
            "net_without_refused_rupees": (
                round(book_net - saved, 2)
                if book_net is not None and saved is not None else None
            ),
        },
        "caveat": (
            "an attribution of the legs that were taken, not a backtest: refusing a "
            "leg also changes which leg comes next, and that is not modelled"
        ),
    }
