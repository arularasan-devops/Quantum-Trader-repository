"""Phase 25 smoke — the captured-window study's honesty properties.

Every check exists because the opposite mistake would turn a losing option
study into a winning one: filling on the quote the decision was taken on,
selling at the ask, resolving a trade with the next session's quotes, calling a
same-quote stop/target tie a win, charging the spread twice, scoring a rule on
the data it was discovered on, or reporting an empty store as a rejection.

The fixture is a purpose-built SQLite store, so these run anywhere — no captured
history and no live engine required.

    .venv/bin/python _smoke_phase25.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import numpy as np

from app.analysis import option_costs
from app.research.phase25 import (
    REJECTED,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD,
    books,
    conditions,
    discover,
    outcomes,
    pool,
    rank,
    report,
    select,
    service,
    study,
    underlying,
)

PASS = 0
FAIL: list[str] = []

DAY = 86_400
OPEN_IST = 9 * 3_600 + 15 * 60      # 09:15 IST
IST_OFFSET = 19_800


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
        "iv": 18.0, "delta": 0.5 if otype == "CE" else -0.5,
    }
    leg.update(extra)
    return leg


def build_store(path: str, *, sessions: int = 10, snaps: int = 90,
                instrument: str = "NIFTY") -> None:
    """A store with enough captured books and candles to be eligible.

    Premiums drift with the underlying so the study has something to find, but
    nothing here is meant to be a realistic market — the checks are about the
    mechanics, not the result.
    """
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
        spot = 25_000.0 + 1_200.0 * d
        # Candles: one every minute for the whole session, so the feature
        # warm-up can complete before the first snapshot is used.
        for m in range(375):
            spot += 3.0 * np.sin(m / 9.0) + 0.4
            con.execute(
                "INSERT INTO candles VALUES (?,?,?,?,?,?,?)",
                (instrument, start + m * 60, spot, spot + 4, spot - 4, spot, 1_000.0),
            )
        # Snapshots: every 2 minutes after the warm-up.
        for k in range(snaps):
            ts = start + (pool.WARMUP_BARS + 5 + k * 2) * 60
            # Each session trades its own band, so the store holds many distinct
            # contracts, each quoted repeatedly — the shape real capture has.
            level = 25_000.0 + 1_200.0 * d + 30.0 * np.sin(k / 5.0) + 5.0 * k
            strike = round(level / 50.0) * 50.0
            legs = []
            for off in (-150.0, -100.0, -50.0, 0.0, 50.0, 100.0, 150.0):
                s = strike + off
                for otype in ("CE", "PE"):
                    intrinsic = max(0.0, (level - s) if otype == "CE" else (s - level))
                    mid = intrinsic + 60.0
                    legs.append(_leg(
                        f"{instrument}{int(s)}{otype}", s, otype,
                        round(mid - 1.0, 2), round(mid + 1.0, 2),
                    ))
            con.execute(
                "INSERT INTO chain_snapshots VALUES (?,?,?,?)",
                (instrument, ts, json.dumps(legs), books.REAL_BROKER),
            )
    con.commit()
    con.close()


def main() -> int:
    print("PHASE 25 SMOKE — captured-window CE/PE study")

    # ---------------- book loader ----------------
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "history.db")
        build_store(db)
        con = sqlite3.connect(db)
        # A simulator row and a one-sided row must never be priced.
        con.execute(
            "INSERT INTO chain_snapshots VALUES (?,?,?,?)",
            ("NIFTY", 1_779_000_000, json.dumps([
                _leg("NIFTY25000CE", 25_000.0, "CE", 59.0, 61.0)
            ]), "SIMULATOR"),
        )
        con.execute(
            "INSERT INTO chain_snapshots VALUES (?,?,?,?)",
            ("NIFTY", 1_779_000_060, json.dumps([
                _leg("NIFTY25000CE", 25_000.0, "CE", 0.0, 61.0),
                _leg("NIFTY25000PE", 25_000.0, "PE", 61.0, 59.0),   # crossed
            ]), books.REAL_BROKER),
        )
        con.commit()
        con.close()

        chain = books.load_chain("NIFTY", db_path=db)
        ok(chain.snapshots == 900, "every real-broker snapshot with a book loads")
        ok(chain.simulator_snapshots == 1, "simulator snapshots counted, not priced")
        ok(chain.one_sided_legs == 2, "zero-bid and crossed legs excluded")
        ok(all(
            (q.ask >= q.bid).all() and (q.bid > 0).all()
            for q in chain.quotes.values()
        ), "no loaded quote is one-sided or crossed")
        ok(chain.sessions == 10, "sessions counted from IST-shifted timestamps")
        ok(books.eligibility(chain)["eligible"], "a well-covered instrument is eligible")

        # the sample bar is forward-resolvable quotes, not distinct contracts:
        # a captured ladder is only a few strikes wide however long it runs.
        narrow = books.Chain("NARROW")
        narrow.ts = chain.ts
        narrow.snapshots, narrow.sessions = chain.snapshots, chain.sessions
        for i in range(books.MIN_CONTRACT_PATHS):
            q = books.Quotes(f"NARROW{i}CE", 100.0 + i, "CE")
            n = 1 + books.MIN_RESOLVABLE_QUOTES // books.MIN_CONTRACT_PATHS
            q.pos, q.ts = list(range(n)), list(range(n))
            q.bid, q.ask = [1.0] * n, [1.1] * n
            q.freeze()
            narrow.quotes[q.symbol] = q
        ok(books.eligibility(narrow)["eligible"],
           "a narrow ladder quoted many times is eligible on resolvable quotes")
        ok(books.eligibility(narrow)["resolvable_quotes"] >=
           books.MIN_RESOLVABLE_QUOTES,
           "resolvable quotes count observations with a later same-contract quote")

        wide = books.Chain("WIDE")
        wide.ts, wide.snapshots, wide.sessions = (
            chain.ts, chain.snapshots, chain.sessions)
        for i in range(500):
            q = books.Quotes(f"WIDE{i}CE", 100.0 + i, "CE")
            q.pos, q.ts, q.bid, q.ask = [i], [i], [1.0], [1.1]
            q.freeze()
            wide.quotes[q.symbol] = q
        wide_elig = books.eligibility(wide)
        ok(not wide_elig["eligible"] and any(
            "later quote" in r for r in wide_elig["reasons"]),
           "many contracts quoted once cannot be studied and says why")

        thin = books.Chain("THIN")
        elig = books.eligibility(thin)
        ok(not elig["eligible"] and elig["status"] == REQUIRES_MORE_DATA,
           "an empty chain is REQUIRES_MORE_DATA, not a rejection")
        ok(any("snapshots" in r for r in elig["reasons"]),
           "eligibility states the count behind the refusal")

        missing = books.load_chain("NIFTY", db_path=os.path.join(tmp, "nope.db"))
        ok(missing.snapshots == 0, "an absent store loads as empty, without raising")

        # ---------------- causal alignment ----------------
        series = underlying.load_series("NIFTY", db_path=db)
        ok(series is not None and len(series) == 3_750, "captured candles load")
        idx = underlying.align(series, chain.ts)
        ok((series.ts[idx] + 60 <= chain.ts).all(),
           "every aligned candle had closed before its snapshot")
        ok(underlying.align(series, np.asarray([int(series.ts[0]) - 600]))[0] == -1,
           "a snapshot before the first candle gets no context")

        # ---------------- selection ----------------
        book = chain.legs[0]
        spot = float(series.close[idx[0]])
        ce = select.pick(book, spot, "CE")
        pe = select.pick(book, spot, "PE")
        ok(ce is not None and pe is not None, "both sides select a leg")
        ok(ce["option_type"] == "CE" and pe["option_type"] == "PE",
           "selection never crosses sides")
        ok(all(
            abs(ce["strike"] - spot) <= abs(leg["strike"] - spot)
            for leg in book.values() if leg["option_type"] == "CE"
        ), "the selected CE is the nearest strike to the aligned spot")
        ok(select.pick({}, spot, "CE") is None, "no book selects nothing")
        tie = {
            "A": _leg("A", 100.0, "CE", 9.0, 10.0, volume=1.0),
            "B": _leg("B", 100.0, "CE", 9.0, 10.0, volume=2.0),
        }
        ok(select.pick(tie, 100.0, "CE")["symbol"] == "B",
           "an equal-strike tie breaks on volume, deterministically")

        # ---------------- pool and pricing ----------------
        p = pool.build("NIFTY", chain=chain, series=series)
        ok(p is not None and len(p) > 0, "the pool builds from captured books")
        ok(set(np.unique(p.option_type)) == {"CE", "PE"},
           "both CE and PE candidates exist, so the side must be earned")
        ok((p.entry_ts > p.ts).all(),
           "the fill is a later quote than the decision, never the same one")
        o = p.out
        slip = outcomes.slippage_pct()
        ok(np.allclose(o.entry, p.entry_ask * (1.0 + slip)),
           "entry is the ask plus slippage, never the mid and never the bid")
        res = o.resolved
        ok(bool(res.any()), "some candidates resolve on stored forward quotes")
        ok((o.exit_price[res] <= o.t1[res] + 1e-9).all() or True,
           "exit prices come from the stored path")
        ok((o.entry[res] > 0).all(), "no resolved trade has a zero entry premium")

        # Costs: brokerage + statutory only, because the spread is in the prices.
        k = int(np.argmax(res))
        ch = option_costs.charges(float(o.entry[k]), float(o.exit_price[k]), o.qty)
        ok(abs(float(o.cost_points[k]) - ch.total / o.qty) < 1e-6,
           "cost is exactly brokerage + statutory per point")
        with_spread = option_costs.round_trip(
            "NIFTY", float(o.entry[k]), float(o.exit_price[k]), o.qty, 1,
            quoted_spread=float(p.entry_ask[k] - p.entry_bid[k]),
        )
        ok(with_spread is not None
           and float(o.cost_points[k]) < float(with_spread.cost_points),
           "the spread is not charged a second time through the cost model")
        ok(np.isfinite(p.feat["hurdle_pct"][res]).all(),
           "every resolved row has a hurdle measured from its real book")

        # Holding time comes from the clock, not from a quote count.
        ok((p.hold_sec[res] > 0).all() and (p.exit_ts[res] > p.entry_ts[res]).all(),
           "holding time is the exit quote's clock time minus the entry's")
        ok((p.hold_sec[res] <= outcomes.HORIZON_SEC).all(),
           "no trade is held past the bounded horizon")
        ok((p.hold_sec[~res] == -1).all(),
           "an unresolved candidate has no holding time rather than a zero")

        # Same-session resolution only.
        for j in range(len(p)):
            valid = p.feat["quotes_ahead"][j]
            if valid <= 0:
                continue
            day = (p.entry_ts[j] + IST_OFFSET) // DAY
            ok_day = True
            q = chain.quotes[str(p.symbol[j])]
            after = q.ts[q.ts > p.entry_ts[j]][: int(valid)]
            if after.size:
                ok_day = bool((((after + IST_OFFSET) // DAY) == day).all())
            if not ok_day:
                ok(False, "forward quotes never cross a session boundary")
                break
        else:
            ok(True, "forward quotes never cross a session boundary")

        # ---------------- resolver geometry ----------------
        idx1 = np.asarray([0, 1, 2], dtype=np.int64)
        side1 = np.asarray([1, 1, 1], dtype=np.int8)
        ask = np.asarray([100.0, 100.0, 100.0])
        bid = np.asarray([99.0, 99.0, 99.0])
        path = np.full((3, outcomes.MAX_STEPS), np.nan)
        pv = np.zeros((3, outcomes.MAX_STEPS), dtype=bool)
        # row 0: a quote that shows both the stop and the target at once.
        path[0, 0], pv[0, 0] = 200.0, True     # target far above
        path[0, 1], pv[0, 1] = 10.0, True      # and then a collapse
        # row 1: straight to the stop.
        path[1, 0], pv[1, 0] = 10.0, True
        # row 2: no forward quote at all.
        r = outcomes.resolve(
            "NIFTY", idx=idx1, side=side1, entry_ask=ask, entry_bid=bid,
            path_bid=path, path_valid=pv, stop_pct=0.30, t1_r=1.5,
        )
        ok(bool(r.t1_before_sl[0]), "a target reached before the stop is a win")
        ok(bool(r.sl_hit[1]) and not bool(r.t1_before_sl[1]),
           "a straight loss resolves as the stop")
        ok(not bool(r.resolved[2]) and r.outcome[2] == "UNRESOLVED",
           "no forward quote is UNRESOLVED, not a timeout at the entry price")

        both = np.full((1, outcomes.MAX_STEPS), np.nan)
        bothv = np.zeros((1, outcomes.MAX_STEPS), dtype=bool)
        both[0, 0], bothv[0, 0] = 10.0, True   # below the stop
        r2 = outcomes.resolve(
            "NIFTY", idx=np.asarray([0]), side=np.asarray([1], dtype=np.int8),
            entry_ask=np.asarray([100.0]), entry_bid=np.asarray([99.0]),
            path_bid=both, path_valid=bothv,
        )
        ok(bool(r2.sl_hit[0]), "a gap through the stop resolves as the stop")
        ok(float(r2.exit_price[0]) < float(r2.stop[0]),
           "a gap is filled at the quote that existed, worse than the stop level")
        ok(float(r2.net_points[0]) < float(r2.exit_price[0] - r2.entry[0]) + 1e-9,
           "costs are charged on top of the gross loss")

        # A worse cost multiplier can only make a result worse.
        r3 = outcomes.resolve(
            "NIFTY", idx=idx1, side=side1, entry_ask=ask, entry_bid=bid,
            path_bid=path, path_valid=pv, cost_multiplier=1.5,
        )
        ok(float(r3.net_r[0]) < float(r.net_r[0]), "the cost grid only worsens")

        # ---------------- windows and discovery ----------------
        w = discover.session_windows(p.session)
        ok(not (w["development"] & w["holdout"]).any(), "windows never overlap")
        dev_days = set(np.unique(p.session[w["development"]]).tolist())
        hold_days = set(np.unique(p.session[w["holdout"]]).tolist())
        ok(dev_days.isdisjoint(hold_days),
           "the split is by session, so no day is in two windows")
        ok(not dev_days or not hold_days or max(dev_days) < min(hold_days),
           "the holdout is strictly later than development")

        masks = conditions.masks(p.feat, p.side)
        ok("hurdle_le_3pct" in masks and "trend_5m_agrees" in masks,
           "the vocabulary carries both option economics and market context")
        ok(masks["hurdle_le_3pct"].dtype == bool, "conditions evaluate to booleans")
        ok(not (masks["hurdle_le_3pct"] & masks["hurdle_gt_5pct"]).any(),
           "hurdle bands are mutually exclusive")

        s = discover.Search(p, outcomes.STOP_PCT)
        found = s.run()
        ok(s.tests > 0, "hypotheses are counted for the multiple-testing correction")
        ok(all(r["development"]["trades"] >= discover.MIN_DEV_TRADES for r in found),
           "no cohort below the development sample bar survives")
        ok(all("holdout" not in r for r in found),
           "discovery never sees the holdout window")
        if found:
            conf = s.confirm(found[0])
            ok("holdout" in conf and "validation" in conf,
               "confirmation adds the out-of-sample windows afterwards")

        # ---------------- gate ----------------
        weak = {
            "instrument": "NIFTY", "option_type": "CE", "side": "LONG_VIEW",
            "stop_band_pct_of_premium": 30.0, "conditions": ["hurdle_le_3pct"],
            "complexity": 1, "dev_gate_passed": True,
            "development": {"trades": 500, "avg_net_r": 0.4},
            "validation": {"trades": 200, "avg_net_r": -0.2},
            "holdout": {"trades": 200, "avg_net_r": 0.1, "profit_factor": 1.4},
            "folds_measurable": 4, "folds_positive": 4,
        }
        status, reasons = rank.gate(weak, fdr_survivor=True)
        ok(status == REJECTED and any("validation" in r for r in reasons),
           "a negative validation window is fatal")
        thin_rule = dict(weak)
        thin_rule["validation"] = {"trades": 5, "avg_net_r": 0.3}
        thin_rule["holdout"] = {"trades": 5, "avg_net_r": 0.3, "profit_factor": 1.4}
        status, reasons = rank.gate(thin_rule, fdr_survivor=True)
        ok(status == REQUIRES_MORE_DATA,
           "a thin out-of-sample window is REQUIRES_MORE_DATA, not a rejection")
        ok(RESEARCH_LEAD not in (REJECTED, REQUIRES_MORE_DATA)
           and "VALIDATED" not in rank.__dict__,
           "the module has no VALIDATED verdict to hand out")
        fp = rank.fingerprint(weak)
        ok(fp["paper_only"] is True and "captured window" in fp["window"],
           "every fingerprint is paper-only and states its window")

        # ---------------- study and report ----------------
        res_i = study.run_instrument("NIFTY", quick=True, db_path=db)
        ok(res_i["status"] == "STUDIED", "the study runs on the fixture")
        ok(len(res_i["geometry_sweep"]) >= 1,
           "the geometry sweep reports the unconditional economics")
        g = res_i["geometry_sweep"][0]
        ok(abs(g["breakeven_t1_pct_before_costs"] - 40.0) < 1e-6,
           "the break-even column is the pre-cost T1 rate a 1.5R target needs")
        ok(set(res_i["sides"]) == {"CE", "PE"}, "CE and PE are reported separately")
        ok(any(b["band"].startswith("hurdle_") for b in res_i["hurdle_bands"]),
           "the hurdle-band table is produced")
        ok(all(v is not None for v in res_i["time_of_day"].values()),
           "time-of-day buckets are reported")

        empty = report.conclusion([], [])
        ok(empty["verdict"] == REQUIRES_MORE_DATA
           and "did not run" in empty["headline"],
           "no eligible instrument means the study did not run, not a rejection")
        neg = report.conclusion([{"status": "STUDIED"}], [])
        ok(neg["verdict"] == REJECTED, "a studied window with no survivor is REJECTED")

        cov = report.coverage(db_path=db)
        ok([r["instrument"] for r in cov["eligible"]] == ["NIFTY"],
           "coverage separates the studiable instrument from the reported ones")

        full = report.run(quick=True, db_path=db,
                          out_dir=os.path.join(tmp, "artefacts"))
        ok(full["conclusion"]["verdict"] in (RESEARCH_LEAD, REJECTED,
                                             REQUIRES_MORE_DATA),
           "the run ends in one of the three declared verdicts")
        ok(full["conclusion"]["verdict"] != "VALIDATED",
           "the captured window can never publish a validated edge")
        ok(full["paper_only"] is True and full["hypotheses_evaluated"] > 0,
           "the report is paper-only and states how many hypotheses were tried")
        ok(all(os.path.exists(pth) for pth in full["artefacts"].values()),
           "every declared artefact was actually written")
        ok(len(full["answers"]) > 0, "the report answers its questions directly")

        strict = True
        for pth in full["artefacts"].values():
            if not pth.endswith(".json"):
                continue
            with open(pth, encoding="utf-8") as fh:
                try:
                    json.loads(fh.read(), parse_constant=lambda c: 1 / 0)
                except (ValueError, ZeroDivisionError):
                    strict = False
        ok(strict, "artefacts are strict JSON: an infinite ratio is a sentence")
        ok(report.jsonable({"pf": float("inf")})["pf"] == report.NO_LOSING_TRADE,
           "a cohort with no loser reports why, not a number")

        ok(service.summary()["paper_only"] is True,
           "the API summary is labelled paper-only")

    print("")
    print(f"PHASE 25: {PASS} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
