"""Phase 11 §6 — the costed ledger: decisions, fills and the flow paper book.

RESEARCH ONLY. Three questions, one module, because they are three views of the
same journey from a signal to a closed position.

**Why did the book take only N entries?** The execution funnel records every
refusal with the stage, the blocker, the value it saw and the threshold it failed
— but until Phase 11 it lived in memory, so the answer died with the process and
the 21 Aug session (7 entries against a cap of 50) can no longer be explained.
This module reads the persisted funnel where it exists and reports UNRECORDED
where it does not. An inferred answer would be a guess wearing a number's
clothes, and the funnel exists precisely so guessing stops.

**What did a filled trade actually cost?** The per-trade ledger carries the
signal and entry times, the book at entry, the stop and targets, the exit, the
excursion, what was kept and what was handed back, gross R and net R side by
side.

**Is the flow paper book's profit real?** The production flow ledger scores an
entry at ``premium`` and an exit at ``premium``: one mid price, both directions,
so it never crosses the spread it would have to cross. On the recorded book that
is not a small correction — for the instruments where flow showed its profit, one
crossing of the book exceeded the whole per-leg gain. This module recomputes each
leg at ask-in / bid-out from the *recorded chain for that exact option symbol*
and reports gross and net side by side. It changes nothing in production: the
live flow tracker, its lot count and its display are untouched.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from bisect import bisect_left
from collections.abc import Callable
from pathlib import Path

from app.research.phase7.dataset import connect
from app.research.phase7.policies import COST_PER_ROUNDTRIP_OPTIONS, median

from .families import FAMILIES, family_of, split

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# A recorded chain within this many seconds of the leg's timestamp is treated as
# that leg's book. The recorder's cycle is ~60s, so two cycles allows for one
# missed snapshot without silently pricing a leg off a book from ten minutes ago.
MAX_BOOK_AGE_SEC = 180

STAGES_IN_ORDER = ("SIGNAL", "RISK", "VALIDATION", "EXECUTION", "BROKER",
                   "ACCEPTED", "FILLED", "POSITION", "EXIT", "CLOSED")


def _session_of(ts: float) -> str:
    return dt.datetime.fromtimestamp(float(ts), IST).strftime("%Y-%m-%d")


def _ist(ts: float | None) -> str | None:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(float(ts), IST).strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- #
# §6a — the decision funnel, per session                                       #
# --------------------------------------------------------------------------- #
def funnel(path: str | Path | None, sessions_wanted: list[str]) -> dict:
    """Per-session funnel from the persisted execution funnel log.

    Returns UNRECORDED for any requested session the log does not cover, and
    names the reason. Nothing is inferred from trade counts: "7 entries" is
    consistent with a dozen different binding limits and choosing between them
    without the record would be invention.
    """
    rows: list[dict] = []
    p = Path(path) if path else None
    if p is not None and p.exists():
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue

    by_session: dict[str, list[dict]] = {}
    for r in rows:
        ts = r.get("ts")
        if ts is None:
            continue
        by_session.setdefault(_session_of(ts), []).append(r)

    out: dict[str, dict] = {}
    for session in sorted(set(sessions_wanted) | set(by_session)):
        recs = by_session.get(session) or []
        if not recs:
            out[session] = {
                "recorded": False,
                "stage_counts": None,
                "blockers": [],
                "binding_limit": "UNRECORDED",
                "why_unrecorded": "the execution funnel was in memory only while "
                                  "this session ran, so its refusals were lost at "
                                  "the next restart. Sessions recorded after the "
                                  "funnel log was added carry the answer",
            }
            continue
        counts = [r for r in recs if r.get("kind") == "COUNTS"]
        blocked = [r for r in recs if r.get("kind") == "BLOCKED"]
        league: dict[tuple[str, str], int] = {}
        for b in blocked:
            key = (str(b.get("stage")), str(b.get("primary_blocker")))
            league[key] = league.get(key, 0) + 1
        ordered = sorted(league.items(), key=lambda kv: -kv[1])
        top = ordered[0] if ordered else None
        stage_counts = counts[-1].get("reached") if counts else None
        out[session] = {
            "recorded": True,
            "stage_counts": stage_counts,
            "stages": list(STAGES_IN_ORDER),
            "refusals": len(blocked),
            "blockers": [
                {"stage": stage, "blocker": blocker, "count": n,
                 "instruments": sorted({str(b.get("instrument")) for b in blocked
                                        if b.get("stage") == stage
                                        and b.get("primary_blocker") == blocker
                                        and b.get("instrument")}),
                 "example": next(({"value": b.get("value"),
                                   "threshold": b.get("threshold"),
                                   "reason": b.get("reason"),
                                   "where": b.get("where"),
                                   "at_ist": _ist(b.get("ts"))}
                                  for b in blocked
                                  if b.get("stage") == stage
                                  and b.get("primary_blocker") == blocker), None)}
                for (stage, blocker), n in ordered],
            "binding_limit": (f"{top[0][0]}:{top[0][1]} refused {top[1]} "
                              f"candidate(s), the most of any blocker"
                              if top else "no refusal was recorded"),
            "first_refusal_ist": _ist(min(b["ts"] for b in blocked))
            if blocked else None,
            "last_refusal_ist": _ist(max(b["ts"] for b in blocked))
            if blocked else None,
        }
    return {
        "source": str(p) if p else None,
        "source_exists": bool(p and p.exists()),
        "records_read": len(rows),
        "by_session": out,
        "reading": "the most frequent blocker is the one that refused the most "
                   "candidates. It is not automatically the reason the DAY ended "
                   "with N entries — a cap that binds once at 11:00 refuses "
                   "everything after it while appearing only as many times as it "
                   "was tested",
    }


# --------------------------------------------------------------------------- #
# §6b — the per-trade costed ledger                                            #
# --------------------------------------------------------------------------- #
def trade_rows(rows: list[dict],
               entry_of: Callable[[dict], dict | None]) -> list[dict]:
    """§6's ledger record for every resolved trade."""
    out: list[dict] = []
    for r in rows:
        er = entry_of(r) or {}
        cap = r.get("capture") or {}
        mfe, realised = r.get("mfe_r"), r.get("realised_r")
        giveback = (None if mfe is None or realised is None or mfe <= 0
                    else round(max(0.0, float(mfe) - float(realised)), 3))
        out.append({
            "family": r.get("family"),
            "instrument": r.get("instrument"),
            "session": r.get("session"),
            "signal_time_ist": r.get("ts_ist"),
            "symbol": r.get("symbol"),
            "side": r.get("side"),
            "strike": r.get("strike"),
            "signal_score": r.get("confidence"),
            "data_flag": r.get("data_flag"),
            "chain_age_sec": r.get("chain_age_sec"),
            "entry_premium": er.get("premium"),
            "bid": er.get("bid"),
            "ask": er.get("ask"),
            "spread_pct_of_premium": er.get("spread_pct"),
            "spread_share_of_risk_pct": r.get("spread_share_of_risk_pct"),
            "spread_cost_r": r.get("spread_cost_r"),
            "oi": er.get("oi"),
            "volume": er.get("volume"),
            "delta": er.get("delta"),
            "stop": r.get("stop"),
            "target1": r.get("target1"),
            "target2": r.get("target2"),
            "target3": r.get("target3"),
            "entry_quality": r.get("entry_quality"),
            "exit_reason": r.get("exit_reason"),
            "held_min": r.get("held_min"),
            "mfe_r": mfe,
            "mae_r": r.get("mae_r"),
            "mfe_capture_pct": cap.get("capture_pct"),
            "giveback_r": giveback,
            "realised_r": realised,
            "net_r": r.get("net_r"),
            "target_before_stop_hit": r.get("target_before_stop_hit"),
            "a_plus_shadow": r.get("phase11_qualification"),
            "a_plus_trigger": r.get("phase11_qualification_trigger"),
            "loss_cause": r.get("loss_cause"),
            "loss_side": r.get("loss_side"),
            "brokerage_assumption_per_roundtrip": COST_PER_ROUNDTRIP_OPTIONS,
        })
    return out


# --------------------------------------------------------------------------- #
# §6c — the flow paper book, costed                                            #
# --------------------------------------------------------------------------- #
def _book_index(db: str, instruments: set[str]) -> dict[str, dict[str, list]]:
    """``{instrument: {symbol: ([ts...], [(bid, ask, premium)...])}}``.

    Built once per instrument from the recorded chains so each flow leg is priced
    against the book that was actually quoted for its own option symbol.
    """
    idx: dict[str, dict[str, list]] = {}
    if not instruments or not os.path.exists(db):
        # A missing database means every leg is reported unpriced. Assuming a book
        # would be the whole error this block exists to correct.
        return idx
    conn = connect(db)
    try:
        for name in sorted(instruments):
            per_symbol: dict[str, list] = {}
            cur = conn.execute(
                "SELECT ts, payload FROM chain_snapshots WHERE instrument = ? "
                "ORDER BY ts", (name,))
            for ts, payload in cur:
                try:
                    quotes = json.loads(payload)
                except ValueError:
                    continue
                if not isinstance(quotes, list):
                    continue
                for q in quotes:
                    sym = q.get("symbol")
                    if not sym:
                        continue
                    bid, ask = q.get("bid"), q.get("ask")
                    if bid is None or ask is None:
                        continue
                    slot = per_symbol.setdefault(sym, [[], []])
                    slot[0].append(int(ts))
                    slot[1].append((float(bid), float(ask),
                                    float(q.get("premium") or 0.0)))
            idx[name] = per_symbol
    finally:
        conn.close()
    return idx


def _book_at(idx: dict[str, dict[str, list]], instrument: str, symbol: str,
             ts: float | None) -> tuple[float, float] | None:
    if ts is None:
        return None
    per_symbol = idx.get(instrument) or {}
    slot = per_symbol.get(symbol)
    if not slot:
        return None
    times, books = slot
    i = bisect_left(times, int(ts))
    best: tuple[float, float] | None = None
    best_age = MAX_BOOK_AGE_SEC + 1
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(times):
            age = abs(times[j] - int(ts))
            if age < best_age:
                best_age = age
                best = (books[j][0], books[j][1])
    return best if best_age <= MAX_BOOK_AGE_SEC else None


def _flow_agg(legs: list[dict]) -> dict:
    gross = [leg["gross_rupees"] for leg in legs
             if leg.get("gross_rupees") is not None]
    net = [leg["net_rupees"] for leg in legs if leg.get("net_rupees") is not None]
    priced = [leg for leg in legs if leg.get("net_rupees") is not None]
    return {
        "legs": len(legs),
        "legs_with_recorded_book": len(priced),
        "book_coverage_pct": round(100.0 * len(priced) / len(legs), 1)
        if legs else None,
        "gross_rupees": round(sum(gross), 2) if gross else None,
        "net_rupees": round(sum(net), 2) if net else None,
        "gross_win_rate_pct": round(
            100.0 * sum(1 for leg in legs if (leg.get("gross_points") or 0) > 0)
            / len(legs), 1) if legs else None,
        "net_win_rate_pct": round(
            100.0 * sum(1 for leg in priced if (leg.get("net_points") or 0) > 0)
            / len(priced), 1) if priced else None,
        "median_gross_points": median([leg.get("gross_points") for leg in legs
                                       if leg.get("gross_points") is not None]),
        "median_spread_points": median([leg.get("entry_spread_points")
                                        for leg in priced
                                        if leg.get("entry_spread_points")
                                        is not None]),
        "median_spread_pct_of_premium": median(
            [leg.get("entry_spread_pct_of_premium") for leg in priced
             if leg.get("entry_spread_pct_of_premium") is not None]),
    }


def costed_flow(flow_path: str | Path | None, db: str, sessions_wanted: list[str]
                ) -> dict:
    """Recompute the flow paper book at ask-in / bid-out from the recorded book."""
    p = Path(flow_path) if flow_path else None
    if p is None or not p.exists():
        return {
            "available": False,
            "source": str(p) if p else None,
            "reason": "no flow signal log supplied, so the flow book is not "
                      "recomputed. The production flow ledger is unchanged either "
                      "way",
        }
    raw: list[dict] = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                raw.append(json.loads(line))
            except ValueError:
                continue

    wanted = set(sessions_wanted)
    legs_in: list[dict] = []
    for rec in raw:
        ts_open = rec.get("ts_open")
        if ts_open is None:
            continue
        if wanted and _session_of(ts_open) not in wanted:
            continue
        if rec.get("final_premium") is None or rec.get("entry_premium") is None:
            continue
        legs_in.append(rec)

    idx = _book_index(db, {str(r.get("instrument")) for r in legs_in
                           if r.get("instrument")})

    legs: list[dict] = []
    for rec in legs_in:
        inst = str(rec.get("instrument"))
        sym = str(rec.get("option_symbol"))
        entry_mid = float(rec["entry_premium"])
        exit_mid = float(rec["final_premium"])
        lot = int(rec.get("lot_size") or 1)
        lots = int(rec.get("lots") or 1)
        entry_book = _book_at(idx, inst, sym, rec.get("ts_open"))
        exit_book = _book_at(idx, inst, sym, rec.get("ts_close"))
        gross_points = round(exit_mid - entry_mid, 4)
        leg = {
            "family": family_of(inst),
            "instrument": inst,
            "session": _session_of(rec["ts_open"]),
            "symbol": sym,
            "side": rec.get("side"),
            "entry_time_ist": _ist(rec.get("ts_open")),
            "exit_time_ist": _ist(rec.get("ts_close")),
            "duration_sec": rec.get("duration_sec"),
            "agreement": rec.get("agreement"),
            "close_reason": rec.get("close_reason"),
            "lot_size": lot,
            "lots": lots,
            # What production records: one mid price for both sides.
            "entry_mid": entry_mid,
            "exit_mid": exit_mid,
            "gross_points": gross_points,
            "gross_rupees": round(gross_points * lot * lots, 2),
        }
        if entry_book and exit_book:
            entry_ask, exit_bid = entry_book[1], exit_book[0]
            spread = round(entry_book[1] - entry_book[0], 4)
            net_points = round(exit_bid - entry_ask, 4)
            leg.update({
                "entry_bid": entry_book[0], "entry_ask": entry_book[1],
                "exit_bid": exit_book[0], "exit_ask": exit_book[1],
                "entry_spread_points": spread,
                "entry_spread_pct_of_premium": round(100.0 * spread / entry_mid, 2)
                if entry_mid else None,
                "net_points": net_points,
                "net_rupees": round(net_points * lot * lots
                                    - COST_PER_ROUNDTRIP_OPTIONS, 2),
                "cost_basis": "entry at the recorded ask, exit at the recorded bid, "
                              f"less ₹{COST_PER_ROUNDTRIP_OPTIONS:.0f} brokerage per "
                              f"round trip",
            })
        else:
            leg.update({
                "net_points": None, "net_rupees": None,
                "unpriced_reason": "no recorded two-sided book for this symbol "
                                   f"within {MAX_BOOK_AGE_SEC}s of the leg",
            })
        legs.append(leg)

    by_family = split(legs)
    by_inst: dict[str, list[dict]] = {}
    for leg in legs:
        by_inst.setdefault(leg["instrument"], []).append(leg)

    overall = _flow_agg(legs)
    return {
        "available": True,
        "source": str(p),
        "legs_read": len(raw),
        "legs_in_window": len(legs),
        "sessions": sorted({leg["session"] for leg in legs}),
        "overall": overall,
        "by_family": {fam: _flow_agg(by_family.get(fam) or []) for fam in FAMILIES},
        "by_instrument": {k: _flow_agg(v) for k, v in sorted(by_inst.items())},
        "production_ledger_pricing": "entry and exit both at `premium` (the mid), so "
                                     "the live flow P&L never crosses the book it "
                                     "would have to cross",
        "what_changed_here": "nothing in production. This is a second, costed view "
                             "of the same legs; flow_paper_lots, the flow tracker "
                             "and the flow display are untouched",
        "reading": (
            "compare gross_rupees with net_rupees per instrument. Where the gap "
            "exceeds the gross figure, the displayed profit is an artefact of "
            "mid-to-mid pricing rather than an edge, and adding lots multiplies "
            "the artefact"),
    }


def build(*, funnel_path: str | Path | None, flow_path: str | Path | None,
          db: str, rows: list[dict], sessions: list[str],
          entry_of: Callable[[dict], dict | None]) -> dict:
    """§6 — the whole ledger: decisions, fills, and the costed flow book."""
    ledger_rows = trade_rows(rows, entry_of)
    return {
        "stages": list(STAGES_IN_ORDER),
        "decision_funnel": funnel(funnel_path, sessions),
        "filled_trades": {
            "n": len(ledger_rows),
            "fields": sorted(ledger_rows[0]) if ledger_rows else [],
            "rows": ledger_rows,
        },
        "costed_flow_book": costed_flow(flow_path, db, sessions),
        "scope": "RESEARCH / PAPER ONLY. No order, no gate, no threshold and no "
                 "display is changed by this ledger",
    }
