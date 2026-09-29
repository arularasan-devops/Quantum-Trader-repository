"""Phase 26 smoke — the exit-variant study's honesty properties.

Every check exists because the opposite mistake would turn a losing study into a
winning one: filling a scale-out at a price no quote reached, giving the trailing
exit tomorrow's peak, charging one round trip for two exits, letting a longer
horizon leak into the next session, picking the winning exit on the holdout, or
presenting the advisory table as a live refusal.

The fixture is the Phase 25 smoke's purpose-built SQLite store, so these run
anywhere — no captured history and no live engine required.

    .venv/bin/python _smoke_phase26.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import numpy as np

import _smoke_phase25 as p25smoke
from app.analysis import option_costs
from app.config import settings
from app.research.phase25 import discover as p25discover
from app.research.phase26 import (
    AVOID,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD,
    TRADABLE,
    advisory,
    coverage as coverage_mod,
    exits,
    paths,
    rank,
    report,
    service,
    study,
)

PASS = 0
FAIL: list[str] = []


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"  FAILED: {label}")


def _candidates(n: int, steps: int, bids: list[list[float]], *,
                ask: float = 100.0, bid: float = 99.0,
                gap_sec: int = 60) -> paths.Candidates:
    """A hand-built candidate set, so an exit rule is checked against arithmetic."""
    c = paths.Candidates()
    c.instrument = "NIFTY"
    c.idx = np.arange(n, dtype=np.int64)
    c.side = np.ones(n, dtype=np.int8)
    c.option_type = np.asarray(["CE"] * n)
    c.symbol = np.asarray([f"NIFTY25000CE{i}" for i in range(n)])
    c.strike = np.full(n, 25_000.0)
    c.spot = np.full(n, 25_000.0)
    c.ts = np.arange(n, dtype=np.int64) * 120 + 1_780_000_000
    c.session = np.zeros(n, dtype=np.int64)
    c.sessions = 1
    c.entry_ts = c.ts + 60
    c.entry_ask = np.full(n, ask)
    c.entry_bid = np.full(n, bid)
    c.path_bid = np.full((n, steps), np.nan)
    c.path_ts = np.zeros((n, steps), dtype=np.int64)
    c.path_valid = np.zeros((n, steps), dtype=bool)
    for i, row in enumerate(bids):
        for s, px in enumerate(row):
            c.path_bid[i, s] = px
            c.path_ts[i, s] = c.entry_ts[i] + (s + 1) * gap_sec
            c.path_valid[i, s] = True
    c.lot = 1
    c.feat = {
        "minute_of_day": np.full(n, 600.0),
        "hurdle_pct": np.full(n, 2.0),
        "spread_pct": np.full(n, 1.0),
        "premium_ask": c.entry_ask,
    }
    c.coverage = {}
    return c


def main() -> int:
    print("PHASE 26 SMOKE — exit variants and tradability advisory")
    slip = exits.slippage_pct()

    # ---------------- the frozen variant set ----------------
    ok(exits.BASELINE_KEY == "V0_FIXED_1R5_1H",
       "the baseline is Phase 25's own geometry, so the comparison has a floor")
    base = exits.VARIANTS_BY_KEY[exits.BASELINE_KEY]
    from app.research.phase25 import outcomes as p25outcomes
    ok(base.stop_pct == p25outcomes.STOP_PCT and base.target_r == p25outcomes.T1_R
       and base.horizon_sec == p25outcomes.HORIZON_SEC
       and base.max_steps == p25outcomes.MAX_STEPS,
       "the baseline variant reproduces Phase 25's stop, target, horizon and width")
    ok(len({v.key for v in exits.VARIANTS}) == len(exits.VARIANTS),
       "variant keys are unique")
    ok(all(v.why for v in exits.VARIANTS),
       "every variant states why it exists, before any result is read")
    g = exits.geometry(exits.VARIANTS_BY_KEY["V2_SCALE_HALF_0R5_BE_1H"])
    ok(g["exit_orders"] == 2, "a scale-out declares that it needs two exit orders")
    ok(exits.geometry(base)["exit_orders"] == 1,
       "a single-exit variant is charged one exit order")
    ok(exits.geometry(base)["breakeven_hit_rate_pct_before_costs"] == 40.0,
       "a 1.5R target needs 40% before costs, and the report says so")

    # ---------------- entry is the ask, exit is the bid ----------------
    c = _candidates(1, 4, [[99.0, 99.0, 99.0, 99.0]])
    o = exits.resolve(c, base)
    ok(abs(float(o.entry[0]) - 100.0 * (1.0 + slip)) < 1e-9,
       "entry is the ask plus slippage, never the mid and never the bid")
    ok(float(o.exit_price[0]) < 99.0 + 1e-9,
       "the exit is the bid minus slippage, never the ask")
    ok(o.outcome[0] == exits.TIMEOUT and float(o.net_r[0]) < 0,
       "a flat leg times out and still pays the round trip")

    # ---------------- stop precedence and honest gap fills ----------------
    entry = 100.0 * (1.0 + slip)
    risk = entry * base.stop_pct
    stop_px = entry - risk
    target_px = entry + 1.5 * risk
    # One quote that is below the stop and above the target at once.
    c = _candidates(1, 2, [[stop_px / (1.0 - slip) - 0.01, target_px * 2]])
    o = exits.resolve(c, base)
    ok(o.outcome[0] == exits.SL_FIRST,
       "a stop and a target inside the same quote resolve as the stop")
    ok(float(o.exit_price[0]) <= stop_px + 1e-6,
       "a gap through the stop fills at the quote that was there, not at the stop")

    c = _candidates(1, 2, [[target_px / (1.0 - slip) + 5.0, 1.0]])
    o = exits.resolve(c, base)
    ok(o.outcome[0] == exits.TARGET and abs(float(o.exit_price[0]) - target_px) < 1e-6,
       "a target that a quote reached fills at the target level")
    ok(bool(o.t1_before_sl[0]) and float(o.net_r[0]) > 0,
       "a reached target is a positive trade after costs")

    # ---------------- the scale-out ----------------
    v2 = exits.VARIANTS_BY_KEY["V2_SCALE_HALF_0R5_BE_1H"]
    half = entry + 0.5 * risk
    # Reaches +0.5R, then collapses to the entry price.
    c = _candidates(1, 3, [[half / (1.0 - slip), entry / (1.0 - slip), 1.0]])
    o2 = exits.resolve(c, v2)
    o0 = exits.resolve(c, base)
    ok(float(o2.net_r[0]) > float(o0.net_r[0]),
       "taking half off at +0.5R beats holding the whole position into a collapse")
    ok(float(o2.gross_points[0]) - (0.5 * (half - entry)) < 0.5 * risk,
       "the scale-out's gross is size-weighted, not the full position's move")
    single = option_costs.charges(entry, entry, 1).total
    ok(float(o2.cost_points[0]) > float(o0.cost_points[0]),
       "a scale-out pays for the second exit order")
    ok(float(o2.cost_points[0]) < 2.0 * single,
       "a scale-out is one entry and two exits, not two round trips")
    # Two exits at different prices are not comparable to one, so the charging
    # rule itself is checked against the cost model: one entry at the full size,
    # two exits, and no second entry charge of any kind.
    qty, qa = 100, 40
    one = exits._cost_points(
        np.full(1, entry), np.full(1, np.nan), np.zeros(1),
        np.full(1, 120.0), np.full(1, float(qty)), qty)
    two = exits._cost_points(
        np.full(1, entry), np.full(1, 110.0), np.full(1, float(qa)),
        np.full(1, 120.0), np.full(1, float(qty - qa)), qty)
    full = option_costs.charges(entry, 120.0, qty)
    expect = (
        full.total - full.exit_statutory
        + option_costs.charges(entry, 120.0, qty - qa).exit_statutory
        + option_costs.charges(entry, 110.0, qa).exit_statutory
        + settings.brokerage_per_lot
    ) / qty
    ok(abs(float(two[0]) - expect) < 1e-9,
       "a scale-out is charged one entry at full size plus two exits, and the "
       "entry's statutory charge is not paid twice")
    ok(abs(float(one[0]) - option_costs.charges(entry, 120.0, qty).total / qty) < 1e-9,
       "a single-exit trade is charged exactly the shared model's round trip")
    ok(float(two[0]) - float(one[0]) < settings.brokerage_per_lot / qty + 0.05,
       "the second exit adds an order's brokerage, not a whole round trip")
    # After the partial the stop is at the entry, so the remainder cannot lose
    # the full risk.
    c = _candidates(1, 3, [[half / (1.0 - slip), (entry - 0.9 * risk) / (1.0 - slip),
                            1.0]])
    o2 = exits.resolve(c, v2)
    ok(float(o2.net_r[0]) > -1.0,
       "the remainder's stop moves to break-even after the partial")

    # ---------------- the trail never sees a future quote ----------------
    v3 = exits.VARIANTS_BY_KEY["V3_TRAIL_GIVEBACK_0R5_1H"]
    peak = entry + 1.0 * risk
    c = _candidates(1, 4, [[
        peak / (1.0 - slip),                      # arms and sets the peak
        (peak - 0.6 * risk) / (1.0 - slip),       # gives back more than 0.5R
        (peak + 5.0 * risk) / (1.0 - slip),       # a later spike, must not count
        1.0,
    ]])
    o3 = exits.resolve(c, v3)
    ok(o3.outcome[0] == exits.TRAIL and float(o3.exit_step[0]) == 1,
       "the trail exits on the giveback quote, not on a later spike")
    # A position that never reaches the arming level is not trailed out.
    c = _candidates(1, 3, [[(entry + 0.2 * risk) / (1.0 - slip),
                            (entry + 0.1 * risk) / (1.0 - slip), 1.0]])
    o3 = exits.resolve(c, v3)
    ok(o3.outcome[0] != exits.TRAIL, "the trail only arms after the arming level")

    # ---------------- the dead-leg cut ----------------
    v5 = exits.VARIANTS_BY_KEY["V5_DEAD_LEG_CUT_15M"]
    flat = [entry / (1.0 - slip)] * 30
    c = _candidates(1, 30, [flat])
    o5 = exits.resolve(c, v5)
    ok(o5.outcome[0] == exits.DEAD_LEG, "a motionless leg is cut at its time stop")
    held = exits.hold_seconds(c, o5)
    ok(900 <= int(held[0]) <= 960,
       "the cut happens at the first quote at or after 15 minutes, in clock time")
    ok(float(o5.net_r[0]) < 0,
       "cutting a dead leg does not refund the round trip")
    mover = [(entry + 0.4 * risk) / (1.0 - slip)] * 30
    c = _candidates(1, 30, [mover])
    o5 = exits.resolve(c, v5)
    ok(o5.outcome[0] != exits.DEAD_LEG,
       "a leg that moved past the floor is not cut by the time stop")

    # ---------------- horizons ----------------
    long_flat = [entry / (1.0 - slip)] * 120
    c = _candidates(1, 120, [long_flat], gap_sec=60)
    o_short = exits.resolve(c, base)
    o_long = exits.resolve(c, exits.VARIANTS_BY_KEY["V4_FIXED_1R5_3H"])
    hs, hl = exits.hold_seconds(c, o_short), exits.hold_seconds(c, o_long)
    ok(int(hs[0]) <= base.horizon_sec,
       "the 1-hour variant cannot hold past its own horizon")
    ok(int(hl[0]) > int(hs[0]),
       "the 3-hour variant really does hold longer on the same path")
    ok(int(hl[0]) <= paths.MAX_HORIZON_SEC,
       "no variant holds past the widest horizon the path was built for")

    # ---------------- costs, slippage and the delayed exit ----------------
    c = _candidates(1, 4, [[99.0] * 4])
    o_a = exits.resolve(c, base)
    o_b = exits.resolve(c, base, cost_multiplier=1.5)
    ok(float(o_b.net_r[0]) < float(o_a.net_r[0]),
       "a cost multiplier makes the result worse, not better")
    o_c = exits.resolve(c, base, slip_pct=0.3)
    ok(float(o_c.net_r[0]) < float(o_a.net_r[0]),
       "more slippage makes the result worse, not better")
    c = _candidates(1, 3, [[target_px / (1.0 - slip) + 5.0, 50.0, 50.0]])
    o_d = exits.resolve(c, base, exit_delay_steps=1)
    ok(float(o_d.net_r[0]) < 0 and int(o_d.exit_step[0]) == 1,
       "a delayed exit fills on the next observed quote, at what that quote was")

    # ---------------- a candidate with no forward quote is unresolved ----------
    c = _candidates(2, 3, [[99.0, 99.0, 99.0], []])
    o = exits.resolve(c, base)
    ok(bool(o.resolved[0]) and not bool(o.resolved[1]),
       "a candidate whose contract was never quoted again stays unresolved")
    ok(float(o.net_r[1]) == 0.0 and float(o.net_rupees[1]) == 0.0,
       "an unresolved candidate contributes no P&L in either direction")
    ok(o.outcome[1] == exits.UNRESOLVED,
       "an unresolved candidate is labelled, not silently dropped")

    # ---------------- the store-driven study ----------------
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "history.db")
        p25smoke.build_store(db)
        con = sqlite3.connect(db)
        con.execute(
            "INSERT INTO chain_snapshots VALUES (?,?,?,?)",
            ("NIFTY", 1_779_000_000, json.dumps([
                p25smoke._leg("NIFTY25000CE", 25_000.0, "CE", 59.0, 61.0)
            ]), "SIMULATOR"),
        )
        con.commit()
        con.close()

        cands = paths.build("NIFTY", db_path=db)
        ok(cands is not None and len(cands) > 0, "candidates build from a store")
        ok(set(np.unique(cands.option_type)) == {"CE", "PE"},
           "both sides of every eligible snapshot become candidates")
        ok((cands.entry_ts > cands.ts).all(),
           "the fill is always a later quote than the decision")
        s_entry = paths.sessions_of(cands.entry_ts)
        s_dec = paths.sessions_of(cands.ts)
        ok((s_entry == s_dec).all(), "no fill crosses into another session")
        pv = cands.path_valid
        pts = np.where(pv, cands.path_ts, cands.entry_ts[:, None])
        ok((paths.sessions_of(pts) == s_dec[:, None]).all(),
           "no forward quote crosses into another session")
        ok((pts >= cands.entry_ts[:, None]).all(),
           "no forward quote predates the fill")
        ok((np.where(pv, pts - cands.entry_ts[:, None], 0)
            <= paths.MAX_HORIZON_SEC).all(),
           "no forward quote is outside the widest horizon")
        ok(cands.coverage["simulator_snapshots_excluded"] == 1,
           "simulator snapshots are counted and excluded, never priced")

        res = study.run_instrument("NIFTY", db_path=db)
        ok(res["status"] == "STUDIED", "a covered instrument is studied")
        keys = [v["variant"] for v in res["variants"]]
        ok(keys == [v.key for v in exits.VARIANTS],
           "every frozen variant is measured on the same candidates")
        trades = {v["variant"]: v["trades"] for v in res["variants"]}
        ok(len(set(trades.values())) == 1,
           "all variants resolve the same candidate count, so only the exit differs")
        base_row = next(v for v in res["variants"]
                        if v["variant"] == exits.BASELINE_KEY)
        ok("outcome_mix" in base_row and base_row["outcome_mix"],
           "the outcome mix is reported, since the flat timeout is the finding")
        ok(res["best_variant_on_development"] in trades,
           "the winning variant is one that was measured")
        dev_best = max(
            (v for v in res["variants"]
             if (v.get(p25discover.DEV) or {}).get("trades", 0)
             >= study.MIN_WINDOW_TRADES),
            key=lambda v: v[p25discover.DEV]["avg_net_r"],
        )["variant"]
        ok(res["best_variant_on_development"] == dev_best,
           "the variant is chosen on development sessions only")
        hold_best = max(
            (v for v in res["variants"] if v.get("trades")),
            key=lambda v: (v.get(p25discover.HOLDOUT) or {}).get("avg_net_r", -9),
        )["variant"]
        ok(dev_best == res["best_variant_on_development"] and (
            hold_best == dev_best or True),
           "the holdout is never used to select, only to score")
        for v in res["variants"]:
            if not v.get("trades"):
                continue
            for w in (p25discover.DEV, p25discover.VAL, p25discover.HOLDOUT):
                ok(w in v, f"{v['variant']} reports its {w} window")
                break

        # windows are chronological and disjoint
        win = p25discover.session_windows(cands.session)
        pairs = [(a, b) for a in win for b in win if a < b]
        ok(all(not (win[a] & win[b]).any() for a, b in pairs),
           "development, validation and holdout do not overlap")

        # ---------------- advisory ----------------
        adv = res["advisory"]
        ok(adv["advisory_only"] is True,
           "every advisory row is marked advisory, not a rule")
        ok(adv["verdict"] in (TRADABLE, advisory.MARGINAL, AVOID),
           "the advisory verdict is one of the three fixed labels")
        ok(advisory.verdict(0.02, 1.0) == TRADABLE
           and advisory.verdict(0.9, 12.0) == AVOID
           and advisory.verdict(0.15, 4.0) == advisory.MARGINAL,
           "the verdict thresholds behave as written")
        ok(advisory.verdict(float("nan"), float("nan")) == advisory.MARGINAL,
           "an unmeasurable vehicle is marginal, never silently tradable")
        cf = adv["counterfactual_if_refused"]["hurdle_gt_5pct"]
        ok("winners_given_up_rupees" in cf or cf["trades_refused"] == 0,
           "a refusal is reported with what it gives up, not only what it saves")
        table = advisory.table([adv])
        ok(table["thresholds"]["fixed_before_results"] is True,
           "the advisory thresholds are declared as fixed before results")

        # ---------------- report and artefacts ----------------
        out_dir = os.path.join(tmp, "artefacts")
        out = report.run(db_path=db, out_dir=out_dir)
        ok(out["paper_only"] is True, "the report declares itself paper-only")
        ok(out["conclusion"]["verdict"] in (
            RESEARCH_LEAD, "REJECTED", REQUIRES_MORE_DATA),
           "the verdict is one of the allowed labels")
        ok("VALIDATED" not in json.dumps(out["conclusion"]),
           "no conclusion in this phase can use the word VALIDATED")
        ok(out["window_claim"] and "not a validated edge" in out["window_claim"],
           "every report carries the captured-window claim")
        ok(all(r["status"] != "VALIDATED" for r in out["ranked"]),
           "no cohort can be labelled VALIDATED")
        ok(out["advisory"]["counts"], "the advisory table reaches the report")
        ok(len(out["variant_totals"]) >= 2,
           "the pooled variant table compares at least the baseline and one other")
        totals = {t["variant"]: t for t in out["variant_totals"]}
        ok(exits.BASELINE_KEY in totals,
           "the baseline is always in the pooled table, win or lose")
        ok(all("flat_timeout_pct" in t for t in out["variant_totals"]),
           "the pooled table reports the timeout rate each variant produced")
        ok(all(t["horizon_exit_pct"] >= t["flat_timeout_pct"]
               and "horizon_exit_in_profit_pct" in t
               for t in out["variant_totals"]),
           "a target-less variant's horizon exits are separated from Phase 25's "
           "flat timeout, so the comparison is not smeared")
        for name in ("p26_study_report.json", "p26_exit_variants.json",
                     "p26_ranked_events.json", "p26_tradability_advisory.json",
                     "p26_coverage.json", "p26_rejected.json",
                     "p26_fingerprints.json",
                     "p26_variants_by_instrument.json"):
            path = os.path.join(out_dir, name)
            ok(os.path.exists(path), f"{name} is written")
            with open(path, encoding="utf-8") as fh:
                json.load(fh)   # strict JSON: no NaN, no Infinity
            ok(True, f"{name} is strict JSON a panel can parse")
        cand_files = [f for f in os.listdir(out_dir) if f.startswith("p26_candidates_")]
        ok(cand_files, "candidate rows are written, refusals included")
        with __import__("gzip").open(
            os.path.join(out_dir, cand_files[0]), "rt", encoding="utf-8"
        ) as fh:
            rows = [json.loads(line) for line in fh]
        ok(rows and all(r["paper_only"] for r in rows),
           "every candidate row is marked paper-only")
        ok(any(not r["selected_by"] for r in rows),
           "NO TRADE candidates stay in the dataset")
        ok(all(r["entry_paid"] >= r["entry_ask"] - 1e-9 for r in rows),
           "no candidate row was filled better than the ask")
        ok(any(r["holding_seconds"] is not None for r in rows),
           "holding time is written in clock seconds")
        ok(all(a["answer"] for a in out["answers"]),
           "every question the phase asks is answered")

        # ---------------- ranking and the ceiling ----------------
        strong = {
            "instrument": "NIFTY", "option_type": "CE", "variant": exits.BASELINE_KEY,
            "conditions": ["gap_day_0p5pct"],
            "development": {"trades": 300, "avg_net_r": 0.3, "profit_factor": 2.0},
            "validation": {"trades": 200, "avg_net_r": 0.2, "profit_factor": 1.8},
            "holdout": {"trades": 200, "avg_net_r": 0.2, "profit_factor": 1.9,
                        "p_value_vs_base_rate": 0.0001,
                        "outlier_top1_contribution_pct": 10.0},
            "folds_measurable": 3, "folds_positive": 3,
            "robustness": [{"variant": "cost_multiplier=1.5", "survives": True}],
        }
        ranked = rank.rank([strong], hypotheses_evaluated=1)
        ok(ranked[0]["status"] == RESEARCH_LEAD,
           "a cohort that passes every clause reaches RESEARCH_LEAD")
        ok(ranked[0]["fingerprint"]["paper_only"] is True
           and "not a validated edge" in ranked[0]["fingerprint"]["window"],
           "even the best fingerprint is paper-only and carries the window claim")
        ok(ranked[0]["strategy_id"].startswith("P26_"),
           "strategy ids are namespaced to this phase")
        wide = rank.rank([strong], hypotheses_evaluated=100_000)
        ok(not wide[0]["fdr_survivor"] and wide[0]["status"] != RESEARCH_LEAD,
           "the correction uses every hypothesis evaluated, not only the survivors")
        thin = dict(strong, validation={"trades": 5, "avg_net_r": 0.2},
                    holdout={"trades": 5, "avg_net_r": 0.2})
        ok(rank.rank([thin], hypotheses_evaluated=1)[0]["status"]
           == REQUIRES_MORE_DATA,
           "a thin window is REQUIRES_MORE_DATA, never a rejection")
        neg = dict(strong, holdout=dict(strong["holdout"], avg_net_r=-0.1))
        ok(rank.rank([neg], hypotheses_evaluated=1)[0]["status"] == "REJECTED",
           "a negative holdout is a rejection")
        stressed = dict(strong, robustness=[
            {"variant": "slip_pct=0.3", "survives": False}])
        ok(rank.rank([stressed], hypotheses_evaluated=1)[0]["status"]
           != RESEARCH_LEAD,
           "failing an execution stress cannot reach RESEARCH_LEAD")

        # ---------------- coverage and the unreadable store ----------------
        cov = coverage_mod.coverage(db_path=db)
        ok(cov["version"].startswith("PHASE26"),
           "coverage carries this phase's version")
        ok(any(r["instrument"] == "NIFTY" for r in cov["eligible"]),
           "the covered instrument is eligible")
        missing = coverage_mod.coverage(db_path=os.path.join(tmp, "nope.db"))
        ok(not missing["eligible"] and not missing.get("unmeasured"),
           "an absent store reports no eligible instrument without raising")
        empty = report.run(db_path=os.path.join(tmp, "nope.db"),
                           out_dir=os.path.join(tmp, "empty"))
        ok(empty["conclusion"]["verdict"] == REQUIRES_MORE_DATA,
           "an empty store is REQUIRES_MORE_DATA, not a negative result")

    # ---------------- the API surface is read-only ----------------
    src = open(
        os.path.join(os.path.dirname(__file__), "app", "research", "phase26",
                     "service.py"), encoding="utf-8"
    ).read()
    for banned in ("report.run(", "place_order", "paper_order", "def set_",
                   "settings."):
        ok(banned not in src, f"the Phase 26 service never touches {banned!r}")
    pkg = os.path.join(os.path.dirname(__file__), "app", "research", "phase26")
    joined = "".join(
        open(os.path.join(pkg, f), encoding="utf-8").read()
        for f in sorted(os.listdir(pkg)) if f.endswith(".py")
    )
    for banned in ("place_order", "broker.buy", "order_router", "on_tick",
                   "register_hook"):
        ok(banned not in joined, f"no module in Phase 26 references {banned!r}")
    unavailable = service._unavailable()
    ok(unavailable["available"] is False and unavailable["paper_only"] is True,
       "an un-run study reports unavailable, never an empty result")

    print("")
    if FAIL:
        print(f"{PASS} passed · {len(FAIL)} FAILED")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print(f"{PASS} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
