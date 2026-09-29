"""Does the Research tab actually improve production signals? — graded on history.

The Research tab reports conditional win rates ("pullback entries won more",
"trade score >= 80 won more") on a few sessions, unsplit, and those statements
have been the basis for production gates. This replays the engine over years of
stored history, splits it chronologically, and asks of each proposal: did the
trades it KEEPS beat the trades it REJECTS in the early period, and did that
still hold in the later holdout?

By default it grades the CANDIDATE POOL, not the taken book. The engine applies
its scores before it emits a BUY, so on the taken book a gate like
``trade_score >= 80`` is true of almost every row and its rejected arm is nearly
empty — the tab's conditional win rates are largely that artefact. The pool
replays the refused bars too, so a gate can be measured against the trades it
would actually have thrown away. ``--taken-only`` reproduces the old, flattering
view for comparison.

    .venv/bin/python phase14_rules.py --instruments NIFTY --years 1.5
    .venv/bin/python phase14_rules.py --instruments NIFTY --save-trades ~/p14_trades.json
    .venv/bin/python phase14_rules.py --trades ~/p14_trades.json   # re-grade, no replay

``--save-trades`` / ``--trades`` exist because the replay is the slow part: once a
period is replayed, every rule can be re-graded on it in a second, so thresholds
can be argued about without paying for the replay again.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time

from app.config import settings
from app.research.db import Database
from app.research.phase14 import replay_spot, rules, sweep
from app.research.phase14.spot_store import SpotStore

VERDICT_ORDER = (rules.CONFIRMED, rules.HARMFUL, rules.REJECTED_OUT_OF_SAMPLE,
                 rules.NO_EFFECT, rules.NOT_SELECTIVE, rules.NOT_ENOUGH_DATA)


def _replay(args) -> tuple[list[dict], list[dict], dict, int, str | None]:
    st = SpotStore(Database())
    names = ([n.strip().upper() for n in args.instruments.split(",") if n.strip()]
             if args.instruments else st.instruments())
    if not names:
        raise SystemExit("no stored history — run phase14_collect.py first")

    since = None
    if args.years:
        since = (dt.date.today()
                 - dt.timedelta(days=int(args.years * 365.25))).isoformat()
    factor = args.factor or int(settings.htf_factor)
    started = time.time()

    early_trades: list[dict] = []
    late_trades: list[dict] = []
    spans: dict[str, dict] = {}
    for name in names:
        candles = sweep.load_candles(name, st, since=since)
        early, late, span = sweep.split_sessions(candles, args.in_sample_fraction)
        spans[name] = span
        for label, leg in (("in-sample", early), ("holdout", late)):
            def note(done: int, total: int, trades: int,
                     _l: str = label, _n: str = name) -> None:
                print(f"[{int(time.time() - started)}s]   htf={factor} {_l} {_n}: "
                      f"bar {done}/{total} ({done * 100 // max(1, total)}%), "
                      f"{trades} trades", flush=True)

            if args.taken_only:
                res = replay_spot.run(name, leg, factor=factor, progress=note)
            else:
                res = replay_spot.pool(name, leg, factor=factor, progress=note,
                                       stride=args.stride)
            if not res.get("ok"):
                print(f"  {name} {label}: skipped ({res.get('reason')})")
                continue
            if not args.taken_only:
                print(f"  {name} {label}: {res['candidates']} candidates "
                      f"({res['candidates_taken']} the engine took, "
                      f"{res['candidates_refused']} it refused)", flush=True)
            (early_trades if label == "in-sample" else late_trades).extend(
                res["trades"])
    return early_trades, late_trades, spans, factor, since


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", default=None, help="comma-separated")
    ap.add_argument("--factor", type=int, default=None,
                    help="HTF factor to replay at (default: the live setting)")
    ap.add_argument("--years", type=float, default=None,
                    help="replay only the most recent N years of stored history")
    ap.add_argument("--in-sample-fraction", type=float,
                    default=sweep.IN_SAMPLE_FRACTION)
    ap.add_argument("--min-arm-trades", type=int, default=rules.MIN_ARM_TRADES)
    ap.add_argument("--min-holdout-expectancy", type=float,
                    default=rules.MIN_HOLDOUT_EXPECTANCY)
    ap.add_argument("--trades", default=None,
                    help="grade a previously saved replay instead of replaying")
    ap.add_argument("--save-trades", default=None,
                    help="write the replayed trades so rules can be re-graded")
    ap.add_argument("--taken-only", action="store_true",
                    help="grade only trades the engine took (the Research tab's "
                         "own view: gates look good because their failing arm "
                         "was never recorded)")
    ap.add_argument("--stride", type=int, default=replay_spot.CANDIDATE_STRIDE,
                    help="bars between sampled candidates in the pool")
    ap.add_argument("--out", default=None, help="write the full study as JSON")
    args = ap.parse_args()

    started = time.time()
    if args.trades:
        with open(args.trades) as fh:
            saved = json.load(fh)
        early_trades, late_trades = saved["in_sample"], saved["holdout"]
        spans, factor, since = saved.get("spans", {}), saved.get("htf_factor"), \
            saved.get("since")
        print(f"grading {len(early_trades)} + {len(late_trades)} saved trades "
              f"(htf_factor={factor}) — no replay")
    else:
        early_trades, late_trades, spans, factor, since = _replay(args)

    if not early_trades or not late_trades:
        raise SystemExit("not enough replayed trades in both periods to compare")

    if args.save_trades:
        with open(args.save_trades, "w") as fh:
            json.dump({"htf_factor": factor, "since": since, "spans": spans,
                       "in_sample": early_trades, "holdout": late_trades}, fh)
        print(f"saved replayed trades to {args.save_trades}")

    study = rules.study(early_trades, late_trades,
                        min_arm_trades=args.min_arm_trades,
                        min_holdout_expectancy=args.min_holdout_expectancy)

    refused = sum(1 for t in early_trades + late_trades if t.get("taken") is False)
    book = ("taken trades only — the engine's gates already filtered these, so a "
            "gate's rejected arm is nearly empty and every lift here flatters it"
            if args.taken_only else
            f"candidate pool: {refused} of "
            f"{len(early_trades) + len(late_trades)} rows are bars the engine "
            f"REFUSED, which is what makes a gate comparison possible")
    print(f"\nhtf_factor={factor}  in-sample {study['in_sample_trades']} rows  "
          f"holdout {study['holdout_trades']} rows\n{book}")
    print(f"a rule must lift expectancy in BOTH periods and leave the kept "
          f"trades at >= {study['min_holdout_expectancy']:+}R out of sample "
          f"(a spread-paying trade needs that much)\n")

    ranked = sorted(study["rules"],
                    key=lambda r: (VERDICT_ORDER.index(r["verdict"]),
                                   -r["holdout"]["lift_r"]))
    for row in ranked:
        a, b = row["in_sample"], row["holdout"]
        print(f"=== {row['rule']}  [{row['verdict']}]")
        if row["verdict"] in (rules.NOT_ENOUGH_DATA, rules.NOT_SELECTIVE):
            # Quoting expectancy per arm here would invite reading a number off a
            # comparison that was never valid.
            print(f"    kept in={a['kept']['trades']} out={b['kept']['trades']}, "
                  f"rejected in={a['rejected']['trades']} "
                  f"out={b['rejected']['trades']} "
                  f"(keeps {row['keeps_pct_of_holdout']}% of holdout) — "
                  f"{row['why']}")
            continue
        for label, leg in (("in ", a), ("out", b)):
            print(f"    {label} kept n={leg['kept']['trades']:<6} "
                  f"exp={leg['kept']['expectancy_r']:+.3f}R "
                  f"win={leg['kept']['win_rate']}% PF={leg['kept']['profit_factor']}"
                  f"  |  rejected n={leg['rejected']['trades']:<6} "
                  f"exp={leg['rejected']['expectancy_r']:+.3f}R"
                  f"  |  lift={leg['lift_r']:+.3f}R "
                  f"({leg['sigma']}sigma"
                  f"{', beats noise' if leg['beats_noise'] else ', within noise'})")
        print(f"    keeps {row['keeps_pct_of_holdout']}% of the holdout book — "
              f"{row['why']}")

    base = study["t1_baseline"]
    print(f"\ntarget-before-stop on the whole book: "
          f"in-sample {base['in_sample']['t1_pct']}%  "
          f"holdout {base['holdout']['t1_pct']}%  "
          f"(stopped {base['holdout']['sl_pct']}%, "
          f"neither {base['holdout']['neither_pct']}% — timed out or flattened)")

    fr = study["frontier"]
    print("\nA+ frontier: how high can target-before-stop go by selection "
          "before the sample collapses?")
    print(f"  searched {fr['combinations_searched']} combinations of "
          f"{len(fr['conditions_used'])} conditions that actually split the book, "
          f"{fr['combinations_with_enough_sample']} kept "
          f">= {fr['min_subset_trades']} candidates in both periods")
    print(f"  {fr['expected_false_positives']} of them are expected to clear any "
          f"bar by chance — read the survivor count against that number")
    for row in fr["rows"][:10]:
        mark = "OK " if row["holds_up"] and row["tradable_frequency"] else "   "
        print(f"  {mark}{' + '.join(row['conditions'])}")
        print(f"      T1 in={row['in_sample']['t1_pct']}% "
              f"out={row['holdout']['t1_pct']}%  "
              f"exp out={row['holdout']['expectancy_r']:+.3f}R  "
              f"n={row['holdout']['trades']} "
              f"({row['holdout_trades_per_day']}/day, "
              f"{row['keeps_pct_of_holdout']}% of book)")
    print(f"  => {fr['verdict']}")

    sel = study.get("engine_selectivity")
    if sel:
        print("\nis the ENGINE'S OWN decision selective? (holdout candidates)")
        for label, arm in (("BUY (taken)", sel["taken"]),
                           ("refused", sel["refused"])):
            print(f"  {label:<14} n={arm['trades']:<6} "
                  f"exp={arm['expectancy_r']:+.3f}R win={arm['win_rate']}% "
                  f"PF={arm['profit_factor']}")
        for sig, arm in sel["by_signal"].items():
            print(f"    {sig:<12} n={arm['trades']:<6} "
                  f"exp={arm['expectancy_r']:+.3f}R win={arm['win_rate']}%")
        print(f"  lift {sel['lift_r']:+.3f}R ({sel['sigma']} sigma) — "
              f"{sel['selectivity']}")

    print("\nscore spread across the replayed book "
          "(a saturated score cannot gate anything):")
    for row in study["score_spread"]:
        if not row["recorded"]:
            print(f"  {row['score']:<20} {row.get('note')}")
            continue
        flag = "  <== SATURATED" if row["saturated"] else ""
        print(f"  {row['score']:<20} min={row['min']:<6} med={row['median']:<6} "
              f"max={row['max']:<6} distinct={row['distinct_values']:<5} "
              f"largest bucket={row['largest_bucket_pct']}%{flag}")

    print("\nuntestable on underlying history (need the live option chain):")
    for row in study["untestable"]:
        print(f"  {row['rule']:<24} {row['reason']}")

    print(f"\nrules compared: {study['rules_compared']}  "
          f"expected to clear the bar by chance: "
          f"~{study['expected_false_positives']}")
    print(f"verdict: {study['verdict']}")
    print(f"\n{study['note']}")
    print(f"\nstudy took {int(time.time() - started)}s")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"htf_factor": factor, "since": since, "spans": spans,
                       "study": study}, fh, indent=2)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
