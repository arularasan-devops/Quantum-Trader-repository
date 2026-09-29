"""Phase 29 smoke — the credit-spread study's honesty properties.

Every check exists because the opposite mistake turns a losing option-selling
study into a winning one: pricing a leg at the mid, filling one leg from a nearby
timestamp, assuming an out-of-the-money short expires worthless, charging the
spread twice, charging STT on the wrong side of a short leg, treating a high
win rate as a result, letting a loss exceed the defined loss, summing both wings
of a condor into its risk, scoring a rule on the data it was discovered on, or
reporting an unstudiable store as a rejection.

The fixture is a purpose-built SQLite store, so these run anywhere — no captured
history and no live engine required.

    .venv/bin/python _smoke_phase29.py
"""
from __future__ import annotations

import inspect
import json
import math
import os
import sqlite3
import tempfile

import numpy as np

from app.analysis import option_costs
from app.research.phase24 import conditions as p24conditions
from app.research.phase29 import (
    REJECTED,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD,
    RESEARCH_ONLY,
    conditions,
    discover,
    economics,
    legs,
    outcomes,
    pool,
    rank,
    report,
    service,
    study,
)
from app.research.phase25 import books

PASS = 0
FAIL: list[str] = []

DAY = 86_400
OPEN_IST = 9 * 3_600 + 15 * 60      # 09:15 IST
IST_OFFSET = 19_800
STEP = 50.0


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"  FAILED: {label}")


def _leg(symbol: str, strike: float, otype: str, bid: float, ask: float,
         **extra) -> dict:
    leg = {
        "symbol": symbol, "strike": strike, "option_type": otype,
        "bid": bid, "ask": ask, "volume": 5_000.0, "oi": 50_000.0,
        "iv": 18.0, "delta": 0.4 if otype == "CE" else -0.4,
    }
    leg.update(extra)
    return leg


def _premium(strike: float, level: float, otype: str, decay: float) -> float:
    """Intrinsic plus an extrinsic that falls with distance and with time.

    Extrinsic has to fall with distance or every vertical collects a zero credit,
    and it has to fall with time or the seller can never win — the fixture would
    then test nothing about the resolver.
    """
    intrinsic = max(0.0, (level - strike) if otype == "CE" else (strike - level))
    extrinsic = 70.0 * math.exp(-abs(strike - level) / 140.0) * decay
    return intrinsic + extrinsic


def build_store(path: str, *, sessions: int = 12, snaps: int = 120,
                instrument: str = "NIFTY", strikes: tuple[float, ...] =
                (-200.0, -150.0, -100.0, -50.0, 0.0, 50.0, 100.0, 150.0, 200.0),
                ) -> None:
    """A store with a ladder wide enough to pair a protective leg."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE candles (instrument TEXT, ts INTEGER, open REAL, "
        "high REAL, low REAL, close REAL, volume REAL, "
        "PRIMARY KEY (instrument, ts))"
    )
    con.execute(
        "CREATE TABLE chain_snapshots (instrument TEXT, ts INTEGER, "
        "payload TEXT NOT NULL, source TEXT, PRIMARY KEY (instrument, ts))"
    )
    base_day = 1_780_000_000 // DAY * DAY
    for d in range(sessions):
        day = base_day + d * DAY
        start = day + OPEN_IST - IST_OFFSET
        spot = 25_000.0 + 900.0 * d
        for m in range(375):
            spot += 3.0 * math.sin(m / 9.0) + 0.4
            con.execute(
                "INSERT INTO candles VALUES (?,?,?,?,?,?,?)",
                (instrument, start + m * 60, spot, spot + 4, spot - 4, spot, 1_000.0),
            )
        for k in range(snaps):
            ts = start + (pool.WARMUP_BARS + 5 + k * 2) * 60
            level = 25_000.0 + 900.0 * d + 25.0 * math.sin(k / 7.0) + 2.0 * k
            atm = round(level / STEP) * STEP
            decay = 1.0 - 0.4 * (k / max(1, snaps - 1))
            rows = []
            for off in strikes:
                s = atm + off
                for otype in ("CE", "PE"):
                    mid = _premium(s, level, otype, decay)
                    rows.append(_leg(
                        f"{instrument}{int(s)}{otype}", s, otype,
                        round(max(0.05, mid - 0.6), 2), round(mid + 0.6, 2),
                    ))
            con.execute(
                "INSERT INTO chain_snapshots VALUES (?,?,?,?)",
                (instrument, ts, json.dumps(rows), books.REAL_BROKER),
            )
    con.commit()
    con.close()


def _arr(*rows: list[float]) -> np.ndarray:
    return np.asarray(rows, dtype=np.float64)


def main() -> int:  # noqa: C901 - a flat list of independent checks
    print("PHASE 29 SMOKE — defined-risk credit spreads")

    # ---------------- pairing ----------------
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "history.db")
        build_store(db)
        chain = books.load_chain("NIFTY", db_path=db)
        ok(chain.snapshots > 0, "the fixture store loads")
        ok(legs.strike_step(chain) == STEP,
           "the strike step is measured from the ladder, not assumed")

        book = chain.legs[0]
        puts = {
            float(v["strike"]) for v in book.values()
            if v["option_type"] == "PE"
        }
        atm = legs.nearest_strike({k: {} for k in puts}, 25_000.0)
        ok(atm in puts, "the nearest strike comes from the quoted ladder")

        pair = legs.build_pair(
            book, spot=min(puts) + 200.0, step=STEP, structure=legs.BULL_PUT,
            short_steps=1, width_steps=1,
        )
        ok(pair is not None, "a bull put spread pairs two quoted put strikes")
        if pair is not None:
            ok(len(pair.short_legs) == 1 and len(pair.long_legs) == 1,
               "a vertical has exactly one short and one protective leg")
            ok(pair.short_strikes[0] > pair.long_strikes[0],
               "the protective put is below the short put")
            ok(abs(pair.width_points - STEP) < 1e-9,
               "the width is the measured step times the width in steps")

        condor = legs.build_pair(
            book, spot=25_000.0, step=STEP, structure=legs.IRON_CONDOR,
            short_steps=1, width_steps=1,
        )
        ok(condor is not None and len(condor.short_legs) == 2,
           "an iron condor pairs a short and a protective leg on both sides")
        if condor is not None:
            ok(abs(condor.width_points - STEP) < 1e-9,
               "a condor's defined risk is one wing's width, not both summed")

        far = legs.build_pair(
            book, spot=25_000.0, step=STEP, structure=legs.BULL_PUT,
            short_steps=9, width_steps=2,
        )
        ok(far is None,
           "a structure whose protective strike is off the captured ladder is "
           "not a candidate, rather than a naked short")

        one_sided = {
            k: v for k, v in book.items()
            if not (v["option_type"] == "PE" and float(v["strike"]) == atm - STEP)
        }
        missing = legs.build_pair(
            one_sided, spot=atm, step=STEP, structure=legs.BULL_PUT,
            short_steps=1, width_steps=1,
        )
        ok(missing is None,
           "a snapshot missing one leg cannot produce a structure")

        symbols = list(chain.quotes)[:2]
        path = legs.common_path(chain, symbols, 0)
        both = set(chain.quotes[symbols[0]].pos) & set(chain.quotes[symbols[1]].pos)
        ok(set(path.tolist()) == {p for p in both if p > 0},
           "the forward path is the intersection of both legs' quotes")
        ok(legs.common_path(chain, [symbols[0], "NOT_A_SYMBOL"], 0).size == 0,
           "an unquoted leg leaves no forward path at all")
        try:
            legs.quote_at(chain, symbols[0], -5)
            raised = False
        except KeyError:
            raised = True
        ok(raised, "a quote that was not stored raises, never substitutes a "
                   "nearby timestamp")

        # ---------------- economics ----------------
        slip = 0.01
        short_bid, short_ask = _arr([100.0]), _arr([104.0])
        long_bid, long_ask = _arr([40.0]), _arr([44.0])
        credit = economics.entry_credit(short_bid, long_ask, slip=slip)
        mid_credit = (102.0 - 42.0)
        ok(credit[0] < mid_credit,
           "the executable credit is smaller than the mid-to-mid credit")
        ok(abs(credit[0] - (100.0 * 0.99 - 44.0 * 1.01)) < 1e-9,
           "credit is short bid minus protective ask, slipped against the trade")
        cost = economics.close_cost(short_ask, long_bid, slip=slip)
        ok(cost[0] > mid_credit,
           "closing costs more than the mid, on the other two sides of the book")
        cap = economics.max_loss(100.0, credit)
        ok(abs(cap[0] - (100.0 - credit[0])) < 1e-9,
           "defined loss is width minus credit")
        ok(bool(economics.priceable(credit, cap)[0]),
           "a positive credit inside its width is priceable")
        ok(not bool(economics.priceable(_arr([-1.0])[0], cap)[0]),
           "a debit structure is excluded, not sign-flipped")
        ok(not bool(economics.priceable(credit, economics.max_loss(
            float(credit[0]) - 1.0, credit))[0]),
           "a credit larger than the width it is sold against is impossible and "
           "is excluded rather than treated as free money")

        qty = 65
        vertical = economics.charge_points(
            _arr([100.0]), _arr([50.0]), _arr([40.0]), _arr([20.0]), qty=qty
        )
        four_leg = economics.charge_points(
            _arr([100.0, 100.0]), _arr([50.0, 50.0]),
            _arr([40.0, 40.0]), _arr([20.0, 20.0]), qty=qty
        )
        ok(vertical[0] > 0, "every leg's round trip is charged")
        ok(four_leg[0] > vertical[0] * 1.9,
           "a four-leg condor is charged about twice a two-leg vertical")
        short_leg = option_costs.charges(50.0, 100.0, qty).total
        long_leg = option_costs.charges(40.0, 20.0, qty).total
        ok(abs(vertical[0] - (short_leg + long_leg) / qty) < 1e-9,
           "a short leg is charged with the premium it sold on the sell side, "
           "and a protective leg with the premium it bought")
        spread = economics.spread_cost_points(
            short_bid, short_ask, long_bid, long_ask
        )
        ok(abs(spread[0] - 8.0) < 1e-9,
           "the reported spread is every leg's quoted width, not charged twice")
        hurdle = economics.hurdle_pct_of_credit(vertical, spread, credit)
        ok(hurdle[0] > 0, "friction is expressed against the credit collected")

        # ---------------- resolver ----------------
        idx = np.arange(3)
        sb, sa = _arr([100.0], [100.0], [100.0]), _arr([101.0], [101.0], [101.0])
        lb, la = _arr([50.0], [50.0], [50.0]), _arr([51.0], [51.0], [51.0])
        # row 0 decays to a win, row 1 gaps far through the stop, row 2 has no
        # paired quote at all.
        ps = np.full((3, 1, 3), np.nan)
        pl = np.full((3, 1, 3), np.nan)
        pv = np.zeros((3, 3), dtype=bool)
        ps[0, 0, :2], pl[0, 0, :2] = (80.0, 60.0), (50.0, 48.0)
        ps[1, 0, :2], pl[1, 0, :2] = (400.0, 400.0), (52.0, 52.0)
        pv[0, :2] = True
        pv[1, :2] = True
        res = outcomes.resolve(
            "NIFTY", idx=idx, structure=legs.BULL_PUT, width_points=100.0,
            short_bid=sb, short_ask=sa, long_bid=lb, long_ask=la,
            path_short_ask=ps, path_long_bid=pl, path_valid=pv,
            slip_pct=0.0, stop_credit_mult=2.0,
        )
        ok(abs(res.credit[0] - 49.0) < 1e-9,
           "the resolver's credit is bid minus ask, per leg")
        ok(abs(res.t1[0] - 0.5 * res.credit[0]) < 1e-9,
           "the target is half the credit, frozen before discovery")
        ok(abs(res.risk[0] - res.credit[0]) < 1e-9,
           "a 2x stop risks exactly one credit")
        ok(str(res.outcome[2]) == outcomes.UNRESOLVED and not res.resolved[2],
           "a structure with no paired closing quote is UNRESOLVED")
        ok(res.net_r[2] == 0.0 and res.net_rupees[2] == 0.0,
           "an unresolved structure is never credited with the premium it kept")
        ok(str(res.outcome[0]) == outcomes.T1_BEFORE_SL,
           "decay into the target resolves as a win")
        ok(res.net_points[0] < res.t1[0],
           "even a winner pays brokerage out of the credit it kept")
        ok(str(res.outcome[1]) == outcomes.SL_FIRST,
           "a gap through the stop resolves as a loss")
        ok(res.gross_points[1] < -res.risk[1],
           "the gap is charged at the quote that existed, not at the stop level")
        ok(res.gross_points[1] >= -res.max_loss[1] - 1e-9,
           "no row loses more than the structure's defined loss")
        ok(res.floor_breaches >= 1,
           "a quote implying worse than the defined loss is counted as a data "
           "event, not booked as a deeper loss")
        ok(abs(res.ror_defined_risk[0] - res.net_points[0] / res.max_loss[0]) < 1e-9,
           "return is quoted against defined loss")
        ok(not hasattr(res, "return_on_margin"),
           "no return-on-margin figure exists on an outcome row")

        # Stop breached first, target reached later: the stop must win, because
        # in real time the position is already closed when the target prints.
        seq_pv = np.ones((1, 2), dtype=bool)
        seq_ps = np.full((1, 1, 2), np.nan)
        seq_pl = np.full((1, 1, 2), np.nan)
        seq_ps[0, 0, :], seq_pl[0, 0, :] = (200.0, 5.0), (50.0, 1.0)
        seq = outcomes.resolve(
            "NIFTY", idx=np.arange(1), structure=legs.BULL_PUT, width_points=100.0,
            short_bid=_arr([100.0]), short_ask=_arr([101.0]),
            long_bid=_arr([50.0]), long_ask=_arr([51.0]),
            path_short_ask=seq_ps, path_long_bid=seq_pl, path_valid=seq_pv,
            slip_pct=0.0,
        )
        ok(str(seq.outcome[0]) == outcomes.SL_FIRST and seq.net_points[0] < 0,
           "a stop breached before the target is a loss, however good the "
           "later quote looks")
        ok(seq.bars_held[0] == 1,
           "the loser is closed on the quote that breached, not held on")

        worse = outcomes.resolve(
            "NIFTY", idx=idx, structure=legs.BULL_PUT, width_points=100.0,
            short_bid=sb, short_ask=sa, long_bid=lb, long_ask=la,
            path_short_ask=ps, path_long_bid=pl, path_valid=pv,
            slip_pct=0.0, cost_multiplier=1.5,
        )
        ok(worse.net_points[0] < res.net_points[0],
           "a higher cost multiplier can only make a row worse")
        slipped = outcomes.resolve(
            "NIFTY", idx=idx, structure=legs.BULL_PUT, width_points=100.0,
            short_bid=sb, short_ask=sa, long_bid=lb, long_ask=la,
            path_short_ask=ps, path_long_bid=pl, path_valid=pv,
            slip_pct=2.0,
        )
        ok(slipped.credit[0] < res.credit[0],
           "slippage is adverse on both legs of the entry")

        # ---------------- pool ----------------
        p = pool.build(
            "NIFTY", structure=legs.BULL_PUT, short_steps=1, width_steps=1,
            chain=chain, series=None if False else _series(db),
        )
        ok(p is not None and len(p) > 0, "the pool prices structures on the store")
        assert p is not None
        ok(bool((p.entry_ts > p.ts).all()),
           "every fill is a later snapshot than the decision that produced it")
        ok(bool((p.hold_sec[p.out.resolved] > 0).all()),
           "every resolved structure was closed after it was opened")
        cov = p.coverage
        ok(cov["no_paired_exit_quote"] >= 0 and "no_structure_ladder_too_narrow" in cov,
           "coverage counts the structures that could not be built or closed")
        ok(cov["width_points"] == STEP, "coverage reports the measured width")
        sessions_dev = discover.session_windows(p.session)
        overlap = (
            (sessions_dev[discover.DEV] & sessions_dev[discover.HOLDOUT]).any()
        )
        ok(not overlap, "no row is in both development and holdout")
        dev_sessions = set(p.session[sessions_dev[discover.DEV]].tolist())
        hold_sessions = set(p.session[sessions_dev[discover.HOLDOUT]].tolist())
        ok(not (dev_sessions & hold_sessions),
           "the split is by whole session, so no day straddles the boundary")
        ok(max(dev_sessions or {0}) <= min(hold_sessions or {10**9}),
           "the holdout is chronologically after development")

        # ---------------- hold periods ----------------
        ok(p.hold == outcomes.HOLD_1H,
           "the default hold is the one-hour hold, declared before measurement")
        p_session = pool.build(
            "NIFTY", structure=legs.BULL_PUT, short_steps=1, width_steps=1,
            chain=chain, series=_series(db), hold=outcomes.HOLD_SESSION,
        )
        ok(p_session is not None, "the session hold prices the same structures")
        assert p_session is not None
        ok(p_session.hold == outcomes.HOLD_SESSION,
           "the pool records which hold produced it")
        ok(float(np.median(p_session.feat["quotes_ahead"]))
           >= float(np.median(p.feat["quotes_ahead"])),
           "the session hold sees at least as many forward paired quotes as the "
           "one-hour hold")
        res_1h, res_sess = p.out.resolved, p_session.out.resolved
        ok(bool(res_sess.any()) and bool(res_1h.any()),
           "both holds resolve structures on the same captured books")
        ok(int(p_session.hold_sec[res_sess].max())
           <= outcomes.HOLDS[outcomes.HOLD_SESSION][0],
           "no structure is held longer than its declared horizon")
        entry_day = (p_session.entry_ts + pool.IST_OFFSET) // 86_400
        exit_day = (p_session.exit_ts[res_sess] + pool.IST_OFFSET) // 86_400
        ok(bool((exit_day == entry_day[res_sess]).all()),
           "no position is carried overnight, on either hold")
        rule_1h = {
            "instrument": "NIFTY", "structure": legs.BULL_PUT,
            "hold": outcomes.HOLD_1H, "short_steps_otm": 1, "width_steps": 1,
            "stop_credit_multiple": 2.0, "conditions": ["credit_pct_25_40"],
        }
        rule_sess = dict(rule_1h, hold=outcomes.HOLD_SESSION)
        ok(rank.strategy_id(rule_1h) != rank.strategy_id(rule_sess),
           "two holds of the same rule are two hypotheses, not one")
        ok(len(study.holds()) == 2 and len(study.holds(quick=True)) == 1,
           "the full study prices both holds and the quick mode prices one")

        # A store that only ever quoted one strike per snapshot has no protective
        # leg available, and must be reported rather than scored on a naked short.
        narrow_db = os.path.join(tmp, "narrow.db")
        build_store(narrow_db, sessions=6, snaps=120, strikes=(0.0,))
        narrow_cov = report.coverage(db_path=narrow_db)
        rows = narrow_cov["eligible"] + narrow_cov["reported_only"]
        ok(all(row.get("max_strikes_in_one_snapshot", 0) < 2 for row in rows),
           "coverage measures the widest single snapshot, not strikes summed "
           "across the whole capture")
        ok(any(
            any("protective leg" in r for r in (row.get("reasons") or []))
            for row in rows
        ), "a single-strike store is reported as unstudiable, with the reason")
        narrow_run = study.run_instrument("NIFTY", quick=True, db_path=narrow_db)
        ok(narrow_run["status"] == REQUIRES_MORE_DATA,
           "an unstudiable store is REQUIRES_MORE_DATA, never a rejection of "
           "credit spreads")

        # ---------------- vocabulary ----------------
        masks_vertical = conditions.masks(p.feat, p.side, legs.BULL_PUT)
        masks_condor = conditions.masks(p.feat, p.side, legs.IRON_CONDOR)
        ok("trend_15m_agrees" in masks_vertical,
           "a directional vertical gets the directional vocabulary")
        ok("trend_15m_agrees" not in masks_condor,
           "a range structure is never given a direction to agree with")
        ok(all(k in masks_condor for k in conditions.spread_conditions()),
           "every structure gets the seller's economics vocabulary")
        ok(set(conditions.NON_DIRECTIONAL) <= set(p24conditions.build()),
           "the non-directional list names real Phase 24 conditions")
        ok(all(m.dtype == bool for m in masks_vertical.values()),
           "every condition is boolean")

        # ---------------- discovery and gates ----------------
        s = discover.Search(p)
        found = s.run()
        ok(s.tests > 0, "every hypothesis evaluated is counted")
        ok(all(len(r["conditions"]) <= discover.MAX_CONDITIONS for r in found),
           "no rule exceeds the complexity ceiling")
        ok(all(r["development"]["trades"] >= discover.MIN_DEV_TRADES for r in found),
           "no rule survives development on a sample below the bar")
        if found:
            row = dict(found[0])
            row["dev_gate_passed"] = True
            conf = s.confirm(row)
            ok("validation" in conf and "holdout" in conf,
               "a survivor is confirmed on validation and holdout, never selected there")
            ok(len(conf["session_folds"]) in (0, discover.WALK_FOLDS),
               "walk-forward uses equal chronological session folds")

        high_win = {
            "instrument": "NIFTY", "structure": legs.BULL_PUT,
            "underlying_view": "LONG_VIEW", "short_steps_otm": 1, "width_steps": 1,
            "width_points": 50.0, "stop_credit_multiple": 2.0,
            "conditions": ["credit_ge_5_points"], "complexity": 1,
            "dev_gate_passed": True, "median_friction_pct_of_credit": 30.0,
            "development": {"trades": 500, "avg_net_r": 0.1,
                            "t1_before_sl_pct": 88.0},
            "validation": {"trades": 200, "avg_net_r": -0.05,
                           "t1_before_sl_pct": 86.0, "profit_factor": 0.8},
            "holdout": {"trades": 200, "avg_net_r": -0.08, "t1_before_sl_pct": 85.0,
                        "profit_factor": 0.7, "p_value_vs_base_rate": 0.9},
            "folds_positive": 1, "folds_measurable": 4,
        }
        status, reasons = rank.gate(high_win, fdr_survivor=False)
        ok(status == REJECTED, "an 85%-win-rate loser is rejected")
        ok(any("despite keeping the target credit" in r for r in reasons),
           "the refusal says the win rate did not make it profitable")

        broke = dict(high_win)
        broke["median_friction_pct_of_credit"] = 120.0
        _, why = rank.gate(broke, fdr_survivor=True)
        ok(any("cannot be profitable at any hit rate" in r for r in why),
           "friction above the whole credit is refused on arithmetic")

        thin = dict(high_win)
        thin["validation"] = {"trades": 5, "avg_net_r": 0.2}
        thin["holdout"] = {"trades": 4, "avg_net_r": 0.3, "profit_factor": 2.0,
                           "p_value_vs_base_rate": 0.01}
        thin["development"] = {"trades": 300, "avg_net_r": 0.2,
                               "t1_before_sl_pct": 70.0}
        status_thin, _ = rank.gate(thin, fdr_survivor=True)
        ok(status_thin == REQUIRES_MORE_DATA,
           "a thin sample is REQUIRES_MORE_DATA, not a rejection and not a lead")

        ranked = rank.rank([dict(high_win)])
        ok(ranked[0]["status"] in (REJECTED, RESEARCH_ONLY, REQUIRES_MORE_DATA),
           "ranking assigns one of the honest statuses")
        ok(ranked[0]["fingerprint"]["paper_only"] is True
           and ranked[0]["fingerprint"]["defined_risk"] is True,
           "every fingerprint is paper-only and defined-risk")
        ok(ranked[0]["strategy_id"] == rank.strategy_id(high_win),
           "the strategy id comes from the definition, not from the result")
        allowed = {RESEARCH_LEAD, RESEARCH_ONLY, REJECTED, REQUIRES_MORE_DATA}
        seen = set()
        for row in (high_win, broke, thin):
            for survivor in (True, False):
                seen.add(rank.gate(dict(row), fdr_survivor=survivor)[0])
        ok(seen <= allowed,
           "the ranking gate cannot produce a word stronger than RESEARCH_LEAD")

        # ---------------- report ----------------
        out_dir = os.path.join(tmp, "artefacts")
        out = report.run(quick=True, db_path=db, out_dir=out_dir)
        verdict = out["conclusion"]["verdict"]
        ok(verdict in (RESEARCH_LEAD, REJECTED, REQUIRES_MORE_DATA),
           "the study produces one of the honest verdicts")
        ok(out["paper_only"] is True, "the report is marked paper-only")
        ok(out["conclusion"].get("window") == out["window_claim"]
           or verdict == REQUIRES_MORE_DATA,
           "the verdict carries the captured-window claim")
        written = os.listdir(out_dir)
        ok("p29_study_report.json" in written, "the report artefact is written")
        for name in written:
            if not name.endswith(".json"):
                continue
            with open(os.path.join(out_dir, name), encoding="utf-8") as fh:
                text = fh.read()
            ok("NaN" not in text and "Infinity" not in text,
               f"{name} is strict JSON with no non-finite value")
            json.loads(text)
        blob = json.dumps(out, default=str)
        ok("return_on_margin" not in blob and "return_on_capital" not in blob,
           "no return-on-margin figure appears anywhere in the report")
        ok(any("assignment" in (a["question"] + a["answer"]).lower()
               for a in out["answers"]),
           "the answers state that no expiry or assignment outcome is modelled")
        ok(any("margin" in (a["question"] + a["answer"]).lower()
               for a in out["answers"]),
           "the answers state that margin is not modelled")
        ok(isinstance(out.get("buyer_versus_seller"), dict),
           "the buyer-versus-seller comparison is present, even when unavailable")
        ok(out["hypotheses_evaluated"] >= 0
           and isinstance(out["geometry"]["stop_credit_multiples"], list),
           "the frozen geometry travels with the report")

        # ---------------- safety ----------------
        joined = "".join(inspect.getsource(m) for m in (
            legs, economics, outcomes, pool, conditions, discover, rank, study,
            report, service,
        ))
        for banned in ("place_order", "broker.buy", "order_router", "on_tick",
                       "register_hook", "paper_entry", "app.execution"):
            ok(banned not in joined,
               f"no module in Phase 29 references {banned!r}: the study cannot "
               f"reach an order path or the existing advisory engine")
        ok("phase26" not in joined,
           "Phase 29 leaves the existing option strategy logic frozen")
        ok("collector" not in joined,
           "Phase 29 reads the captured books and never changes the capture")
        service_src = inspect.getsource(service)
        ok("report.run(" not in service_src and "study." not in service_src
           and "pool.build" not in service_src
           and "discover.Search" not in service_src,
           "the read-only service cannot start a study")
        ok("paper_only" in service_src, "the service marks its reads paper-only")
        summary = service.summary()
        ok(isinstance(summary, dict) and summary.get("paper_only") is True,
           "the service answers without a study having been run")
        ok(isinstance(service.sweep(), list) and isinstance(service.cohorts(), list),
           "the service tables read as lists")
        ok(all(r.get("promoted") is False for r in service.leads()),
           "no lead is ever marked promoted")

    print("")
    print(f"PHASE 29 SMOKE — {PASS} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


def _series(db: str):
    from app.research.phase25 import underlying

    return underlying.load_series("NIFTY", db_path=db)


if __name__ == "__main__":
    raise SystemExit(main())
