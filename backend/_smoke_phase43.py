"""Phase 43 smoke — the properties that stop a historical study from lying.

Every check below exists because its absence has already produced a wrong
answer somewhere in this project, or would have:

* a selection variable reading a field that only exists after the trade
  (Phase 42's peak-based ratio), including the *cost*, which is knowable only
  after the fill unless it is estimated at the decision close;
* a trailing statistic that includes the bar it is used on, so the move being
  forecast contributes to its own forecast — the classic pair-trading beta;
* a chronological split that cuts inside a session, so a window is judged on
  trades it could not have seen;
* the holdout being read to *choose* something, which turns the only honest
  frame into another development window;
* a label published below the declared sample floor, which Phase 42 did: the
  floor was declared, fingerprinted, and never applied to the verdict;
* a positive total that is one session, or one trade, printed as a mechanism;
* profit factor printing `inf` when there are no losses yet, which reads as
  strength and means "too few trades";
* a hypothesis counted twice under two names, which inflates a family's
  apparent breadth and deflates the correction that prices the search;
* an unmeasurable family reported as a negative result rather than unmeasured;
* a fingerprint that ignores function bodies, or moves on a comment;
* importing Phase 41, Phase 42 or anything on the order path from a research
  module, which is how a study becomes a trading change by accident.

    .venv/bin/python _smoke_phase43.py
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sys

import numpy as np

from app.research import phase43
from app.research.phase24 import conditions as p24cond
from app.research.phase43 import (
    answerability,
    cli,
    evaluate,
    freeze,
    mechanisms,
    pair,
    registry,
    report,
    stats,
    study,
)
from app.research.phase43.evaluate import DEV, HOLD, VAL

PASS = 0
FAIL: list[str] = []


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


# Fields that exist only once a trade is over. None of them may appear in any
# function that decides whether a candidate is taken.
FUTURE_FIELDS = (
    "mfe", "mae", "peak", "exit_price", "exit_ts", "net_points", "net_pct",
    "gross_points", "t1_before_sl", "sl_hit", "bars_to_t1", "bars_to_sl",
    "outcome", "giveback",
)
SELECTION_FUNCTIONS = (
    mechanisms.cost_estimate, mechanisms.ratios, mechanisms.declared,
    mechanisms.regime_candidates, evaluate.Book.cohort, pair.signals,
    pair._trailing_beta, pair._trailing_sigma,
)
PHASE43_MODULES = (
    answerability, cli, evaluate, freeze, mechanisms, pair, registry, report,
    stats, study, phase43,
)
FORBIDDEN_IMPORTS = ("phase41", "phase42", "execution", "broker", "order",
                     "angelone", "groww")


def _names(fn) -> set[str]:
    tree = ast.parse(inspect.getsource(fn).lstrip())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.add(node.value)
    return out


def _synthetic_aligned(n: int = 2_000) -> pair.Aligned:
    """Two well-formed series on shared timestamps, one session per 400 bars."""
    rng = np.random.default_rng(43)
    ts = np.arange(n, dtype=np.int64) * 60 + 1_600_000_000
    al = pair.Aligned()
    al.names = ("NIFTY", "CRUDEOIL")
    al.ts = ts
    closes = []
    for scale in (20_000.0, 6_000.0):
        walk = scale * np.exp(np.cumsum(rng.normal(0, 2e-4, n)))
        closes.append(walk)
    al.close = (closes[0], closes[1])
    al.open = (closes[0] * 1.0001, closes[1] * 1.0001)
    al.high = (closes[0] * 1.001, closes[1] * 1.001)
    al.low = (closes[0] * 0.999, closes[1] * 0.999)
    al.session = ts // (400 * 60)
    return al


def _splits(train: float, val: float, hold: float, *, trades: int = 500,
            sessions: int = 200, expectancy_r: float = 0.2,
            share: float | None = 10.0, excl_top: float = 1.0,
            p_value: float = 0.001) -> dict:
    def w(net: float) -> dict:
        return {
            "trades": trades, "sessions": sessions, "net_total": net,
            "expectancy_r": expectancy_r, "best_session_share_pct": share,
            "net_total_excl_top1pct": excl_top, "p_value": p_value,
        }
    return {DEV: w(train), VAL: w(val), HOLD: w(hold)}


def main() -> int:
    # ------------------------------------------- nothing reads the future
    for fn in SELECTION_FUNCTIONS:
        names = _names(fn)
        leaked = sorted(n for n in names if n in FUTURE_FIELDS)
        ok(not leaked,
           f"{fn.__qualname__} names no post-trade field (found {leaked})")
    ok("close" in _names(mechanisms.ratios)
       and "entry" not in _names(mechanisms.ratios),
       "the cost ratio is priced at the decision bar's close, not at the fill "
       "it cannot know")

    # ------------------------------------------- no forbidden neighbours
    for mod in PHASE43_MODULES:
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        bad = sorted({m for m in imported
                      for f in FORBIDDEN_IMPORTS if f in m})
        ok(not bad,
           f"{mod.__name__} imports nothing from Phase 41/42 or the order "
           f"path (found {bad})")

    # ------------------------------------------- the declared vocabulary
    vocab = set(p24cond.build())
    ok(all(c in vocab for c in mechanisms.BASE),
       "the baseline rule is built from the existing condition vocabulary")
    declared_states = [c for _, c in mechanisms.REGIME_STATES + mechanisms.TIME_STATES]
    ok(all(c in vocab for c in declared_states),
       "every family-D state is an existing condition, not a new indicator "
       "invented to raise the hypothesis count")
    ok("trend_15m_agrees" not in declared_states,
       "no family-D state is already implied by the baseline rule, which would "
       "reprint the parent arm's numbers under a second name")
    cands = mechanisms.declared(("NIFTY",))
    ok(sum(1 for c in cands if c["is_control"]) == 1,
       "exactly one unfiltered control per instrument, so every arm is read "
       "against the base rate rather than against zero")
    ok(len({c["candidate"] for c in cands}) == len(cands),
       "no candidate id appears twice")

    # ------------------------------------------- the statistics
    ok(stats.profit_factor(np.array([1.0, 2.0])) is None,
       "profit factor is None rather than inf when nothing has lost yet")
    ok(stats.profit_factor(np.array([2.0, -1.0])) == 2.0,
       "profit factor is gross win over gross loss")
    ok(stats.max_drawdown(np.array([1.0, -3.0, 1.0])) == 3.0,
       "drawdown is measured on the chronological curve")
    ok(stats.max_drawdown(np.array([-3.0, 1.0, 1.0])) == 3.0,
       "and it does not depend on where the loss sits")
    conc = stats.session_concentration(np.array([10.0, 1.0]), np.array([1, 2]))
    ok(conc["best_session_share_pct"] == 90.9
       and conc["total_excl_best_session"] == 1.0,
       "single-session concentration is reported as a share of the positive "
       "total, with the total without it")
    ok(stats.session_concentration(
        np.array([-2.0, 1.0]), np.array([1, 2]))["best_session_share_pct"]
       is None,
       "a negative total has no concentration share to report")
    net = np.array([5.0, -1.0, -1.0, -1.0, 3.0])
    summary = stats.summarise(
        gross=net + 1.0, net=net, cost=np.ones(5), mfe=np.full(5, 2.0),
        mae=np.full(5, 1.0), win=net > 0, session=np.array([1, 1, 2, 2, 3]),
        order=np.arange(5),
    )
    ok(summary["gross_total"] == 10.0 and summary["net_total"] == 5.0,
       "gross and net are both reported, so the reader can see what the "
       "charges took")
    ok(summary["longest_losing_streak"] == 3, "the losing streak is the run")
    ok(summary["net_total_excl_top1pct"] == 0.0,
       "the total is re-reported without its best trade")
    ok(summary["trades"] == 5 and summary["sessions"] == 3,
       "trades and sessions are counted separately, because one session with "
       "many trades is not many samples")
    ok(stats.summarise(
        gross=np.array([]), net=np.array([]), cost=np.array([]),
        mfe=np.array([]), mae=np.array([]), win=np.array([], dtype=bool),
        session=np.array([]), order=np.array([], dtype=int),
    )["status"] == stats.EMPTY,
       "an empty cohort is EMPTY rather than a zero result")
    ok(stats.t_statistic(np.array([1.0])) is None,
       "one trade has no t statistic")
    ok(stats.p_value_one_sided(0.0, 100) == 0.5
       and stats.p_value_one_sided(4.0, 100) < 0.001,
       "the p-value is one-sided against a zero mean")
    ok(0.0 < (stats.wilson_low(50, 100) or 0.0) < 50.0,
       "the win rate carries a lower confidence bound, not just a point")

    # ------------------------------------------- the chronological split
    session = np.repeat(np.arange(100), 7)
    masks = evaluate.split_sessions(session)
    dev, val, hold = masks[DEV], masks[VAL], masks[HOLD]
    ok(int(dev.sum() + val.sum() + hold.sum()) == session.size,
       "the three windows partition the sample exactly")
    ok(not (dev & val).any() and not (val & hold).any() and not (dev & hold).any(),
       "the windows do not overlap")
    ok(session[dev].max() < session[val].min() < session[hold].min(),
       "the windows are in time order, so validation and holdout are always "
       "later than what chose them")
    for name, mask in masks.items():
        whole = all(
            bool(mask[session == s].all()) or not bool(mask[session == s].any())
            for s in np.unique(session)
        )
        ok(whole, f"the {name} window contains whole sessions only")
    ok(np.unique(session[dev]).size == int(round(100 * phase43.DEV_SHARE)),
       "the split honours the declared development share")

    # ------------------------------------------- the label
    lead = evaluate.status(_splits(10.0, 10.0, 10.0), [], 200)
    ok(lead["status"] == phase43.HISTORICAL_LEAD,
       "a candidate positive in all three windows, unconcentrated and "
       "unstressed is a lead")
    ok(evaluate.status(_splits(10.0, 10.0, 10.0), [], 4)["status"]
       == phase43.REQUIRES_MORE_DATA,
       "four sessions is REQUIRES_MORE_DATA and not a verdict — the exact "
       "mistake Phase 42 made when its declared floor was never applied")
    ok(evaluate.status(_splits(10.0, 10.0, 10.0, trades=10), [], 200)["status"]
       == phase43.REQUIRES_MORE_DATA,
       "a window below the trade floor is too small rather than a result")
    ok(evaluate.status(_splits(-1.0, 10.0, 10.0), [], 200)["status"]
       == phase43.REJECTED,
       "a candidate negative in its own train window is rejected even when "
       "the holdout is positive")
    ok(evaluate.status(_splits(10.0, -1.0, 10.0), [], 200)["status"]
       == phase43.REJECTED,
       "a candidate that fails validation is rejected even when the holdout "
       "is positive, so a lucky third window cannot rescue it")
    ok(evaluate.status(_splits(10.0, 10.0, -1.0), [], 200)["reason"]
       == "did not survive the chronological holdout",
       "the holdout is the frame that decides")
    ok(evaluate.status(
        _splits(10.0, 10.0, 10.0, expectancy_r=0.0001), [], 200)["status"]
       == phase43.REJECTED,
       "an edge below the declared economic floor is rejected rather than "
       "reported as small but real")
    ok(evaluate.status(_splits(10.0, 10.0, 10.0, share=90.0), [], 200)["status"]
       == phase43.REJECTED,
       "a holdout total that is 90% one session is not a mechanism")
    ok(evaluate.status(
        _splits(10.0, 10.0, 10.0, excl_top=-1.0), [], 200)["status"]
       == phase43.REJECTED,
       "a holdout that turns negative without its best 1% of trades is not a "
       "mechanism either")
    stressed = [{"variant": "cost x2", "survives": False}]
    ok(evaluate.status(_splits(10.0, 10.0, 10.0), stressed, 200)["status"]
       == phase43.REJECTED,
       "a candidate that dies at a worse cost is rejected, because the "
       "baseline cost here omits a spread this dataset cannot see")

    # ------------------------------------------- the correction
    rows = [
        {"candidate": f"C{i}", "status": phase43.HISTORICAL_LEAD,
         "reason": "positive", "splits": _splits(1.0, 1.0, 1.0, p_value=p)}
        for i, p in enumerate((0.0001, 0.04, 0.2, 0.5, 0.9))
    ]
    evaluate.fdr(rows)
    ok(all(r["fdr_tested"] for r in rows),
       "every measured candidate enters the correction, not only the good ones")
    ok(rows[0]["status"] == phase43.HISTORICAL_LEAD,
       "a strongly significant candidate survives the correction")
    ok(rows[-1]["status"] == phase43.REJECTED
       and "false-discovery" in rows[-1]["reason"],
       "a candidate that is positive but explicable by the size of the search "
       "loses its lead label and says so")

    # ------------------------------------------- the pair, without look-ahead
    al = _synthetic_aligned()
    base_sig = pair.signals(al, beta_adjusted=True)
    cut = 1_500
    tampered = _synthetic_aligned()
    tampered.close = (tampered.close[0].copy(), tampered.close[1].copy())
    tampered.close[0][cut:] *= 1.5
    after = pair.signals(tampered, beta_adjusted=True)
    ok(np.allclose(base_sig["z"][:cut], after["z"][:cut], equal_nan=True),
       "changing every price after a bar leaves that bar's divergence score "
       "untouched — the hedge ratio and the spread's scale are trailing")
    ok(np.isnan(base_sig["z"][:pair.TRAIL_BARS]).all(),
       "no score exists before its trailing window is formed")
    res = pair.trades(al, beta_adjusted=False, threshold=0.5)
    ok(res["net"].size > 0, "the synthetic pair produces resolvable trades")
    ok(float(res["cost"].min()) > 0.0,
       "both legs of every pair trade are charged")
    ok(np.all(res["net"] < res["gross"]),
       "net is always below gross, so no leg is filled free")
    ok(np.all(res["mfe"] >= 0) and np.all(res["mae"] >= 0),
       "excursions are magnitudes, reported descriptively")
    dearer = pair.trades(al, beta_adjusted=False, threshold=0.5,
                         cost_multiplier=2.0)
    ok(float(dearer["net"].sum()) < float(res["net"].sum()),
       "the cost multiplier makes the same trades worse, never better")
    tight = pair.trades(al, beta_adjusted=False, threshold=3.0)
    ok(tight["net"].size <= res["net"].size,
       "a wider divergence requirement cannot produce more trades")
    ok(pair.align("NIFTY", "NO_SUCH_INSTRUMENT") is None,
       "a pair with a missing series is None rather than a half-priced pair")
    ok(len(pair.declared("NIFTY", "CRUDEOIL")) == 2 * len(pair.Z_ARMS),
       "family B declares both sizings across every arm, in advance")

    # ------------------------------------------- unmeasured is not negative
    amap = answerability.map_families()
    fams = {f["family"]: f for f in amap["families"]}
    ok(set(fams) == set(phase43.FAMILIES),
       "every requested family appears in the answerability map, including the "
       "ones that cannot be measured")
    unmeasured = [u for f in fams.values() for u in f["unmeasured_terms"]]
    ok(any("BANKNIFTY" in u["term"] for u in unmeasured),
       "NIFTY versus BANKNIFTY is reported as unmeasured rather than silently "
       "dropped or proxied")
    ok(all(u["status"] in (phase43.UNMEASURED, phase43.REQUIRES_MORE_DATA,
                           "REFUSED_BY_DESIGN") for u in unmeasured),
       "an unmeasurable term never carries a negative verdict")
    ok(all(u.get("reason") for u in unmeasured),
       "every unmeasured term states which data is missing")
    ok("BANKNIFTY" in amap["captured_only_instruments"],
       "the instruments with weeks rather than years of history are named")

    # ------------------------------------------- the fingerprint
    fp = freeze.fingerprint()
    ok(len(fp["definition"]) == 16 and set(fp["components"]),
       "the definition hashes to one value with a per-component breakdown")
    ok(fp == freeze.fingerprint(), "the fingerprint is stable across calls")
    ok(fp["declared"]["stop_atr"] == phase43.STOP_ATR
       and fp["declared"]["min_sessions"] == phase43.MIN_SESSIONS,
       "the declared numbers are read from the module rather than restated in "
       "the freeze, which is how a fingerprint stops matching its own code")
    covered = {c.rsplit(".", 1)[-1] for group in fp["covers"].values()
               for c in group}
    ok("ratios" in covered and "status" in covered and "trades" in covered,
       "the hash covers what a candidate is, how the pair resolves, and how a "
       "label is assigned")
    bodies = inspect.getsource(evaluate.status)
    ok("MIN_SESSIONS" in bodies,
       "the floor is applied inside the labelling function, not only declared")

    # ------------------------------------------- the registry entry
    row = {
        "candidate": "A1_EXPECTED_MOVE_OVER_COST_4X_NIFTY",
        "family": phase43.FAMILY_COST, "instrument": "NIFTY",
        "status": phase43.HISTORICAL_LEAD, "conditions": list(mechanisms.BASE),
        "ratio": "expected_move_over_cost", "threshold": 4.0,
        "mechanism": "smoke", "splits": _splits(1.0, 2.0, 3.0),
        "fdr_survives": True,
    }
    entry = registry.entry(row)
    ok(entry["promoted"] is False and entry["paper_only"] is True,
       "a registry entry is explicit that a lead is not promoted")
    ok(entry["definition"]["threshold"] == 4.0
       and entry["definition"]["conditions"] == list(mechanisms.BASE)
       and entry["definition"]["stop_atr"] == phase43.STOP_ATR,
       "the entry carries the rule, its arm and its geometry, so a shadow arm "
       "can be frozen from it without being redefined")
    ok(entry["fingerprint"]["definition"] == fp["definition"],
       "the entry records the definition it was measured under")
    ok(entry["measured_on"]["cost_basis"].startswith("MODELLED"),
       "the entry states that its cost basis was modelled, not executable")
    ok({"bid", "ask", "lot_size", "fill_price"} <= set(
        entry["live_validation"]["required_fields"]),
       "the entry names the executable fields the live capture must add")
    ok(json.loads(json.dumps(entry)) == entry,
       "the entry is JSON round-trippable, so it can be stored and compared "
       "rather than re-derived")

    # ------------------------------------------- the report
    payload = {
        "phase": "43", "version": phase43.VERSION,
        "status": phase43.RESEARCH_ONLY, "fingerprint": fp,
        "answerability": amap, "candidates": [], "ranking": [],
        "family_c_vehicle_comparison": {"measured": [], "unmeasured": []},
        "development_selected_arm": {}, "skipped_as_redundant": [],
        "leads": [], "registry": [], "hypotheses_evaluated": 0,
        "dataset": {"series": amap["five_year_series"], "source": []},
    }
    text = report.render(payload)
    ok("TOP 3 HISTORICAL LEADS" in text and "NONE." in text,
       "a run with no lead says so plainly instead of promoting its least bad "
       "candidate")
    ok("Nothing here is a promotion" in text,
       "the report states what a lead is not")
    ok(phase43.NO_OPTION_HISTORY[:40] in text,
       "the report carries the reason the option families are unmeasured")
    near = {
        "candidate": "N", "instrument": "CRUDEOIL", "mechanism": "m",
        "family": phase43.FAMILY_COST, "status": phase43.REQUIRES_MORE_DATA,
        "reason": "only 53 sessions carry trades", "sessions": 53,
        "splits": _splits(1.0, 1.0, 9.0, trades=238, sessions=53),
        "cost_stress": [],
    }
    with_near = dict(payload, candidates=[near])
    near_text = report.render(with_near)
    ok("not a lead" in near_text and "bid" in near_text
       and "executable trades" in near_text,
       "with no lead the report still states the fields and the sample an "
       "executable validation of the nearest miss would need, and states that "
       "the nearest miss is not a lead")
    negative = {
        "candidate": "X", "instrument": "NIFTY", "mechanism": "m",
        "family": phase43.FAMILY_COST, "status": phase43.REJECTED,
        "reason": "r", "sessions": 100,
        "splits": _splits(1.0, 1.0, -5.0),
        "cost_stress": [{"variant": "cost x2", "cost_multiplier": 2.0,
                         "survives": True}],
    }
    ranked = study.ranking([negative])
    ok(ranked[0]["cost_stress"] == "n/a",
       "cost stress reads n/a for a candidate that was already negative at the "
       "baseline cost, rather than 'survives'")

    # ------------------------------------------- the CLI
    ok(cli.main(["frozen"]) == 0,
       "the CLI prints the frozen definition without touching any store")
    ok(cli.main(["answerability"]) == 0,
       "and prints the answerability map on its own")
    ok(not any(
        isinstance(node, ast.Attribute) and node.attr in {"execute", "commit"}
        for node in ast.walk(ast.parse(inspect.getsource(study)))
    ), "the study module runs no SQL at all — it reads files and returns a "
       "payload")
    ok(os.path.basename(report.ARTEFACT_DIR) == "phase43",
       "artefacts are written beside the other phases', under their own name")

    print()
    print(f"PHASE 43 SMOKE — {PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
