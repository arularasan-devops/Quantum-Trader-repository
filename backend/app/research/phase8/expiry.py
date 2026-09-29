"""Phase 8 Part A/B — expiry classification from the recorded contract. RESEARCH ONLY.

The recorded chain payload has no ``expiry`` field: every leg in the 18-Aug dataset
carries ``expiry: None``, so the contract's expiry has to be recovered from the
tradingsymbol the broker returned, which encodes it. Nothing is hard-coded to a
date; two symbol grammars are parsed and anything else is reported as
``EXPIRY_UNKNOWN`` rather than guessed:

    NIFTY18AUG2624450PE      <underlying><DD><MMM><YY><strike><CE|PE>   NFO / MCX
    SENSEX2682077400CE       <underlying><YY><M><DD><strike><CE|PE>     BFO weekly

Classification is by days-to-expiry measured against the *session's own date*, so a
session is expiry for one instrument and not for another — which is the normal case
on any given day and the reason instruments are never pooled here.

    EXPIRY_DAY   dte == 0
    PRE_EXPIRY   1 <= dte <= 2
    NON_EXPIRY   dte >= 3

Minutes-to-expiry uses the venue's close (NSE/BSE 15:30 IST, MCX 23:30 IST) on the
expiry date. MCX options actually expire a few business days ahead of the futures
contract, so an MCX ``dte`` derived from the option symbol is the option's own
expiry and is right; the close time is the venue's, and is stated because it is an
assumption.
"""
from __future__ import annotations

import datetime as dt
import re

from app.market.instruments import REGISTRY
from app.research.phase7.dataset import IST
from app.research.phase7.policies import median

from .findings import label, note

EXPIRY_DAY = "EXPIRY_DAY"
PRE_EXPIRY = "PRE_EXPIRY"
NON_EXPIRY = "NON_EXPIRY"
UNKNOWN = "EXPIRY_UNKNOWN"
CLASSES = (EXPIRY_DAY, PRE_EXPIRY, NON_EXPIRY, UNKNOWN)

PRE_EXPIRY_MAX_DTE = 2

_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
     "NOV", "DEC"), start=1)}
# BFO weekly compresses the month to one character: 1-9 then O/N/D.
_BFO_MONTH = {**{str(i): i for i in range(1, 10)}, "O": 10, "N": 11, "D": 12}

_DDMMMYY = re.compile(r"^([A-Z]+)(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$")
_BFO = re.compile(r"^([A-Z]+)(\d{2})([1-9OND])(\d{2})(\d+)(CE|PE)$")

# Close of the venue's trading day, IST. An assumption, not recorded data.
CLOSE_HHMM = {"MCX": (23, 30), "NFO": (15, 30), "BFO": (15, 30), "NSE": (15, 30)}


def parse_expiry(symbol: str) -> dt.date | None:
    """Contract expiry recovered from the tradingsymbol, or None if unrecognised."""
    s = (symbol or "").strip().upper()
    m = _DDMMMYY.match(s)
    if m and m.group(3) in _MONTHS:
        day, mon, yy = int(m.group(2)), _MONTHS[m.group(3)], int(m.group(4))
        try:
            return dt.date(2000 + yy, mon, day)
        except ValueError:
            return None
    m = _BFO.match(s)
    if m:
        yy, mon, day = int(m.group(2)), _BFO_MONTH[m.group(3)], int(m.group(4))
        try:
            return dt.date(2000 + yy, mon, day)
        except ValueError:
            return None
    return None


def venue(instrument: str) -> str:
    spec = REGISTRY.get(instrument.upper())
    return spec.exchange if spec else "NFO"


def classify(dte: int | None) -> str:
    if dte is None or dte < 0:
        return UNKNOWN
    if dte == 0:
        return EXPIRY_DAY
    if dte <= PRE_EXPIRY_MAX_DTE:
        return PRE_EXPIRY
    return NON_EXPIRY


def describe(instrument: str, symbol: str, ts: int) -> dict:
    """Per-trade expiry record: expiry, dte, minutes to expiry, class, venue."""
    exp = parse_expiry(symbol)
    when = dt.datetime.fromtimestamp(ts, IST)
    ex = venue(instrument)
    if exp is None:
        return {"expiry": None, "days_to_expiry": None, "minutes_to_expiry": None,
                "expiry_class": UNKNOWN, "venue": ex,
                "session": when.date().isoformat(),
                "note": "tradingsymbol grammar not recognised; not guessed"}
    hh, mm = CLOSE_HHMM.get(ex, (15, 30))
    close = dt.datetime.combine(exp, dt.time(hh, mm), tzinfo=IST)
    dte = (exp - when.date()).days
    return {
        "expiry": exp.isoformat(),
        "days_to_expiry": dte,
        "minutes_to_expiry": round((close - when).total_seconds() / 60.0, 1),
        "expiry_class": classify(dte),
        "venue": ex,
        "session": when.date().isoformat(),
    }


def _block(rows: list[dict], sessions: int) -> dict:
    """Part B's comparison for one group. Every quantity is read off the Phase 7
    per-trade row; nothing is recomputed here."""
    if not rows:
        return {"n": 0, "label": label(0, sessions)}
    rs = [r["realised_r"] for r in rows]
    wins = [r for r in rs if r > 0]
    bad = -sum(r for r in rs if r <= 0)
    out = {
        "n": len(rows),
        "sessions": len({r["session"] for r in rows}),
        "target_before_stop_pct": round(
            100.0 * sum(1 for r in rows if r.get("target_before_stop_hit")) / len(rows), 1),
        "win_rate_pct": round(100.0 * len(wins) / len(rs), 1),
        "expectancy_r": round(sum(rs) / len(rs), 3),
        "profit_factor": round(sum(wins) / bad, 3) if bad > 0 else None,
        "median_mfe_r": median([r["mfe_r"] for r in rows]),
        "median_mae_r": median([r["mae_r"] for r in rows]),
        "median_mfe_capture_pct": median(
            [r["capture"]["capture_pct"] for r in rows
             if r["capture"].get("capture_pct") is not None]),
        "median_giveback_r": median(
            [r["capture"]["giveback_r"] for r in rows
             if r["capture"].get("giveback_r") is not None]),
        "median_min_to_mfe": median(
            [r["min_to_mfe"] for r in rows if r.get("min_to_mfe") is not None]),
        "median_min_to_mae": median(
            [r["min_to_mae"] for r in rows if r.get("min_to_mae") is not None]),
        "median_held_min": median([r["held_min"] for r in rows]),
        "median_favourable_move": median(
            [r["underlying_favourable"] for r in rows
             if r.get("underlying_favourable") is not None]),
        "median_adverse_move": median(
            [r["underlying_adverse"] for r in rows
             if r.get("underlying_adverse") is not None]),
        "median_spread_cost_r": median(
            [r["spread_cost_r"] for r in rows if r.get("spread_cost_r") is not None]),
        "median_premium_expansion_pct": median(
            [r["premium_expansion_pct"] for r in rows
             if r.get("premium_expansion_pct") is not None]),
        "label": label(len(rows), sessions),
        "note": note(len(rows), sessions),
    }
    return out


def study(rows: list[dict], sessions: int) -> dict:
    """Part A + B: coverage of the classification, then expiry vs non-expiry."""
    by_class: dict[str, list[dict]] = {c: [] for c in CLASSES}
    for r in rows:
        by_class[r.get("expiry_class", UNKNOWN)].append(r)

    per_instrument: dict[str, dict] = {}
    for inst in sorted({r["instrument"] for r in rows}):
        sub = [r for r in rows if r["instrument"] == inst]
        per_instrument[inst] = {
            "venue": sub[0].get("venue"),
            "expiries_seen": sorted({r["expiry"] for r in sub if r.get("expiry")}),
            "by_class": {c: _block([r for r in sub if r.get("expiry_class") == c],
                                   sessions)
                         for c in CLASSES if any(
                             r.get("expiry_class") == c for r in sub)},
        }

    venues = {}
    for v in sorted({r.get("venue") for r in rows if r.get("venue")}):
        sub = [r for r in rows if r.get("venue") == v]
        venues[v] = {c: _block([r for r in sub if r.get("expiry_class") == c],
                               sessions)
                     for c in CLASSES if any(r.get("expiry_class") == c for r in sub)}

    resolved_expiry_sessions = len({
        (r["instrument"], r["session"]) for r in rows
        if r.get("expiry_class") == EXPIRY_DAY})
    return {
        "classification": {
            "rule": f"dte==0 EXPIRY_DAY · 1..{PRE_EXPIRY_MAX_DTE} PRE_EXPIRY · "
                    f">={PRE_EXPIRY_MAX_DTE + 1} NON_EXPIRY, from the expiry encoded "
                    f"in the tradingsymbol (the recorded leg has no expiry field)",
            "coverage_pct": round(100.0 * sum(
                1 for r in rows if r.get("expiry_class") != UNKNOWN)
                / max(1, len(rows)), 1),
            "counts": {c: len(by_class[c]) for c in CLASSES},
            "instrument_expiry_sessions": resolved_expiry_sessions,
            "close_time_assumption": {k: f"{v[0]:02d}:{v[1]:02d} IST"
                                      for k, v in CLOSE_HHMM.items()},
        },
        "by_class": {c: _block(by_class[c], sessions) for c in CLASSES},
        "by_venue": venues,
        "by_instrument": per_instrument,
        "warning": "an instrument-session is expiry for one instrument and not for "
                   "another on the same date; instruments are never pooled in the "
                   "per-instrument tables above",
    }
