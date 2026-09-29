#!/usr/bin/env python
"""Does the measured re-pricing join the right row and refuse to guess?

    .venv/bin/python _smoke_flow_measured.py

Builds a throwaway SQLite research store with a known chain and a known Flow
ledger, so every assertion is about the join, the cost basis and the attribution
rather than about whatever happens to be in the real capture.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.analysis import option_costs as _costs
from app.config import settings
from app.research import flow_measured as fm
from app.research.db import Database
from app.research.store import SOURCE_REAL, ResearchStore

CHECKS = 0


def ok(cond: bool, what: str) -> None:
    global CHECKS
    CHECKS += 1
    assert cond, what


def leg(**kw) -> dict:
    base = dict(
        engine="flow", instrument="NIFTY", side="CE",
        option_symbol="NIFTY23850CE", ts_open=1_786_084_400,
        entry_premium=150.0, final_premium=149.0, ts_close=1_786_084_460,
        final_points=-1.0, lots=2, lot_size=65, final_rupees=-130.0,
    )
    base.update(kw)
    return base


def build_store(tmp: Path) -> ResearchStore:
    st = ResearchStore(Database(f"sqlite:///{tmp / 'research_smoke.db'}"))
    # ten one-minute futures bars of 20 points each, ending at the entry candle
    st.insert_futures("NIFTY", [
        {"ts": 1_786_084_400 - 60 * i, "open": 23800.0, "high": 23810.0,
         "low": 23790.0, "close": 23800.0}
        for i in range(10)
    ])
    # the leg's own strike, quoted book 1.0 point wide, delta 0.50
    st.insert_options("NIFTY", [{
        "ts": 1_786_084_400, "strike": 23850.0, "option_type": "CE",
        "close": 150.0, "delta": 0.50, "bid": 149.5, "ask": 150.5,
        "source": SOURCE_REAL,
    }])
    # a cheap leg with a wide book: same candle, different strike
    st.insert_options("NIFTY", [{
        "ts": 1_786_084_400, "strike": 24500.0, "option_type": "CE",
        "close": 8.0, "delta": 0.08, "bid": 7.6, "ask": 8.4,
        "source": SOURCE_REAL,
    }])
    # a row with no book at all — must never become a free spread
    st.insert_options("NIFTY", [{
        "ts": 1_786_084_400, "strike": 23900.0, "option_type": "PE",
        "close": 120.0, "delta": 0.45, "bid": None, "ask": None,
        "source": SOURCE_REAL,
    }])
    # a crossed book — unusable, not a negative cost
    st.insert_options("NIFTY", [{
        "ts": 1_786_084_400, "strike": 24000.0, "option_type": "PE",
        "close": 90.0, "delta": 0.40, "bid": 91.0, "ask": 90.0,
        "source": SOURCE_REAL,
    }])
    return st


def main() -> int:
    # ---- symbol parsing ----
    ok(fm.parse_symbol("NIFTY23850PE") == (23850.0, "PE"), "strike and side parse")
    ok(fm.parse_symbol("BANKNIFTY25AUG52000CE") == (52000.0, "CE"),
       "an expiry-bearing symbol still yields the trailing strike and side")
    ok(fm.parse_symbol("NIFTYFUT") is None,
       "an unreadable symbol is refused, not guessed at a strike")
    ok(fm.parse_symbol(None) is None, "a missing symbol is refused")

    # ---- nearest / range helpers ----
    ok(fm.nearest([], 10) is None, "an empty series has no nearest row")
    ok(fm.nearest([100, 200, 300], 100) == 0, "an exact hit is the row itself")
    ok(fm.nearest([100, 200, 300], 149) == 0, "a tie-break rounds to the earlier row")
    ok(fm.nearest([100, 200, 300], 151) == 1, "the closer of two rows wins")
    ok(fm.nearest([100, 200], 9_999) == 1, "a want beyond the series clamps")
    ok(fm.range_before({"ts": [], "span": []}, 10, 5) is None,
       "no captured bars means no range, not a zero range")
    ok(fm.range_before({"ts": [10, 20, 30], "span": [4.0, 6.0, 100.0]}, 20, 5) == 5.0,
       "the range is the median of bars at or before the entry, so a gap candle "
       "cannot licence a day of cheap legs")
    ok(fm.range_before({"ts": [10, 20], "span": [0.0, 0.0]}, 20, 5) is None,
       "zero-range bars are not a measurement")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        st = build_store(tmp)

        # ---- ledger loading ----
        ledger = tmp / "flow_signals.jsonl"
        rows = [leg(), leg(ts_close=None), {"junk": True}]
        ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
        loaded = fm.load_legs(ledger)
        ok(len(loaded) == 1, "only completed legs are priced; open legs are skipped")
        ok(fm.load_legs(tmp / "absent.jsonl") == [],
           "a missing ledger reads as no legs rather than raising")

        # ---- the join ----
        rep = fm.reprice([leg()], st=st)
        row = rep["legs"][0]
        ok(row["match"] == fm.MATCHED, "a leg whose entry candle was captured matches")
        ok(row["match_gap_sec"] == 0, "the matched row is the leg's own candle")
        ok(row["quoted_spread_points"] == 1.0,
           "the spread charged is the quoted ask minus bid")
        ok(row["cost_basis"] == _costs.MEASURED, "that leg is costed MEASURED")
        ok(rep["join"]["measured_book"] == 1, "and counts toward the measured book")

        far = fm.reprice([leg(ts_open=1_786_084_400 + 5_000,
                              open_ctime=1_786_084_400 + 5_000)], st=st)
        ok(far["legs"][0]["match"] == fm.UNMATCHED,
           "a leg outside the tolerance is UNMATCHED, not priced against a "
           "distant candle")
        ok(far["join"]["measured_book"] == 0 and far["cost"]["measured"]["legs"] == 0,
           "and is excluded from every measured total instead of being back-filled")

        other = fm.reprice([leg(option_symbol="NIFTY99999CE")], st=st)
        ok(other["legs"][0]["match"] == fm.UNMATCHED,
           "a strike that was never captured does not match some other strike")

        # ---- books that must not become free ----
        nobook = fm.reprice([leg(option_symbol="NIFTY23900PE", side="PE",
                                 entry_premium=120.0)], st=st)
        nb = nobook["legs"][0]
        ok(nb["match"] == fm.MATCHED and nb["cost_basis"] == _costs.ASSUMED,
           "a matched row with no book is costed on the labelled family median")
        ok(nb["quoted_spread_points"] is None and nb["cost_points"] > 0,
           "an absent book is never a zero spread")
        ok(nobook["join"]["matched_but_no_book"] == 1,
           "and is reported as matched-but-unquoted rather than as measured")

        crossed = fm.reprice([leg(option_symbol="NIFTY24000PE", side="PE",
                                  entry_premium=90.0)], st=st)
        ok(crossed["legs"][0]["cost_basis"] == _costs.ASSUMED,
           "a crossed book is unusable, not a negative spread")

        # ---- the cost the market charged vs the one we assumed ----
        rich = fm.reprice([leg()], st=st)["legs"][0]
        assumed_spread = 150.0 * _costs.assumed_spread_pct("NIFTY") / 100.0
        ok(assumed_spread > 1.0,
           "the family median is wider than this leg's quoted book, so the "
           "assumed cost overstated it")
        ok(rich["cost_points"] < rich["assumed_cost_points"],
           "and the measured round trip is therefore cheaper")
        ok(rich["net_rupees"] == round(rich["gross_rupees"] - rich["cost_rupees"], 2),
           "net is gross minus the cost that was charged, nothing else")

        # ---- the gate, replayed on measured terms ----
        # 20-point candles at delta 0.50 => 10 points expected, against a ~1.6
        # point round trip: comfortably economic.
        ok(rich["expected_move_points"] == 10.0,
           "expected move is the option's delta times the captured candle range")
        ok(rich["verdict"] == fm.OK and rich["ratio"] >= settings.flow_cost_multiple,
           "an expensive leg with a tight book is ECONOMIC")

        cheap = fm.reprice([leg(option_symbol="NIFTY24500CE", entry_premium=8.0,
                                final_premium=7.0, final_rupees=-130.0)], st=st)
        cl = cheap["legs"][0]
        ok(cl["verdict"] == fm.REFUSED,
           "the cheap leg with a wide book is UNECONOMIC — the cohort the audit "
           "found had a 0% net win rate")
        ok(cheap["gate"]["refused"]["legs"] == 1
           and cheap["gate"]["allowed"]["legs"] == 0,
           "and it lands in the refused cohort")

        # a captured row with a placeholder delta cannot be graded either way
        st.insert_options("NIFTY", [{
            "ts": 1_786_084_400, "strike": 23700.0, "option_type": "CE",
            "close": 200.0, "delta": 0.0, "bid": 199.0, "ask": 201.0,
            "source": SOURCE_REAL,
        }])
        flat = fm.reprice([leg(option_symbol="NIFTY23700CE",
                               entry_premium=200.0)], st=st)
        ok(flat["legs"][0]["verdict"] == fm.UNMEASURED,
           "a placeholder delta of exactly zero is absent, not a refusal")
        ok(flat["gate"]["refused"]["legs"] == 0
           and flat["gate"]["unmeasured"]["legs"] == 1,
           "an unmeasurable leg is never counted as one the gate would refuse")

        # ---- attribution arithmetic ----
        both = fm.reprice([
            leg(final_rupees=500.0),                                   # allowed
            leg(option_symbol="NIFTY24500CE", entry_premium=8.0,
                final_premium=6.0, final_rupees=-260.0),               # refused
        ], st=st)
        g = both["gate"]
        ok(g["allowed"]["legs"] == 1 and g["refused"]["legs"] == 1,
           "the two cohorts are graded separately")
        ok(g["book_net_rupees"] == round(
            g["allowed"]["net_rupees"] + g["refused"]["net_rupees"], 2),
           "the cohorts sum to the measured book, so nothing is double counted")
        ok(g["net_without_refused_rupees"] == g["allowed"]["net_rupees"],
           "removing the refused legs leaves exactly the allowed cohort")
        ok("not a backtest" in both["caveat"],
           "the second-order effect of refusing a leg is disclosed, not hidden")

        # ---- an empty capture says so instead of modelling ----
        empty = ResearchStore(Database(f"sqlite:///{tmp / 'empty.db'}"))
        none = fm.reprice([leg()], st=empty)
        ok(none["join"]["matched"] == 0 and none["cost"]["measured"]["legs"] == 0,
           "with no captured chain nothing is measured")
        ok(none["gate"]["book_net_rupees"] is None,
           "and no net figure is published from an empty capture")

    print(f"checked {CHECKS}")
    print("flow measured re-pricing smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
