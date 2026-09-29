#!/usr/bin/env python
"""Phase 15B — discover selective market setups on the 5-year candidate pool.

RESEARCH ONLY, UNDERLYING_ONLY. Reads a saved candidate pool, runs the
development -> validation -> holdout discovery, and writes the artefacts.

    .venv/bin/python phase15b_discover.py \
        --trades ~/market_scan_pool.json \
        --outdir ~/phase15b

No Angel call, no order, and safe to run while the paper app is up.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.research.phase15 import folds as folds_mod
from app.research.phase15b import discovery, dimensions as dims


def _r(value: object) -> str:
    return "n/a" if not isinstance(value, (int, float)) else f"{float(value):+.4f}R"


def _pct(value: object) -> str:
    return "n/a" if not isinstance(value, (int, float)) else f"{float(value):.1f}%"


def _num(value: object) -> str:
    return "n/a" if not isinstance(value, (int, float)) else f"{float(value):.3f}"


def questions(study: dict) -> list[dict]:
    """The questions this phase exists to answer, answered from the study."""
    base = study["baselines"]["holdout"]
    ranked = study["ranking"]
    top = ranked[0] if ranked else None
    survivors = [r for r in ranked if r["verdict"] == discovery.SURVIVES]
    dim = {s["dimension"]: s for s in study["dimensions"]}

    def bucket(dimension: str, name: str) -> dict:
        for row in dim[dimension]["buckets"]:
            if row["bucket"] == name:
                return row
        return {}

    def best_of(dimension: str) -> str:
        rows = [r for r in dim[dimension]["buckets"] if r["measurable"]]
        if not rows:
            return f"no {dimension} bucket reached {discovery.MIN_COHORT} rows"
        best = max(rows, key=lambda r: r.get("expectancy_r") or -9.0)
        return (f"{best['bucket']} at {_r(best.get('expectancy_r'))} over "
                f"{best['trades']} development rows, T1 "
                f"{_pct(best.get('t1_pct'))}, lift {_r(best.get('lift_r'))} "
                f"({best.get('lift_sigma')} sigma)")

    open_cohort = bucket("session_period", "OPEN_0_15")
    index_cohort = bucket("universe", "INDEX")
    stock_cohort = bucket("universe", "STOCK")
    engine_buy = bucket("engine_decision", "ENGINE_BUY")
    engine_no = bucket("engine_decision", "ENGINE_REFUSED")
    mt = study["multiple_testing"]

    pairs = [
        ("Can a materially better subset of historical opportunities be found "
         "at all?",
         (f"{len(ranked)} setup(s) held on the holdout, of which "
          f"{len(survivors)} were positive in every walk-forward fold. Best is "
          f"{top['setup']} at {_r(top['expectancy_r'])}, T1 "
          f"{_pct(top['t1_pct'])}, PF {_num(top['profit_factor'])} over "
          f"{top['holdout_trades']} holdout rows"
          if top else
          "no setup survived development, validation and the holdout. The "
          "whole-pool baseline is "
          f"{_r(base.get('expectancy_r'))} and nothing beat it durably")),
        ("How many comparisons produced that answer?",
         (f"{mt['hypotheses']} comparisons; about "
          f"{mt['expected_false_positives_unadjusted']} would look significant "
          f"on noise alone at 5%, so the bar was raised to "
          f"{mt['z_bonferroni']} sigma and every proposal then had to repeat on "
          "validation and again on the holdout")),
        ("What is the best time of day?", best_of("session_period")),
        ("Does the opening 15 minutes help or hurt?",
         (f"OPEN_0_15 measures {_r(open_cohort.get('expectancy_r'))} against "
          f"the pool's {_r(open_cohort.get('baseline_expectancy_r'))} over "
          f"{open_cohort.get('trades')} development rows (lift "
          f"{_r(open_cohort.get('lift_r'))}, "
          f"{open_cohort.get('lift_sigma')} sigma) — this is the cohort the "
          "production skip_first_15_minutes gate refuses"
          if open_cohort else
          "the pool carried no rows inside the first 15 minutes")),
        ("Index or stock?",
         (f"INDEX {_r(index_cohort.get('expectancy_r'))} over "
          f"{index_cohort.get('trades')} rows against STOCK "
          f"{_r(stock_cohort.get('expectancy_r'))} over "
          f"{stock_cohort.get('trades')} rows; median move "
          f"{_num(index_cohort.get('median_mfe_points'))} vs "
          f"{_num(stock_cohort.get('median_mfe_points'))} points, which is what "
          "a fixed rupee cost is charged against")),
        ("Which instrument is strongest?", best_of("instrument")),
        ("Which volatility band is strongest?", best_of("volatility_band")),
        ("Which regime is strongest?", best_of("regime")),
        ("Does higher-timeframe alignment matter?", best_of("htf_alignment")),
        ("Does momentum matter?", best_of("momentum")),
        ("Does extension (stop width against noise) matter?",
         best_of("extension")),
        ("Does room (reward:risk) matter?",
         best_of("room") + " — a nearer target raises T1 without improving "
         "expectancy, so read this bucket's reward:risk beside its T1 rate"),
        ("Does setup type matter?", best_of("setup_type")),
        ("Do the engine's own score and confidence select?",
         f"score_band: {best_of('score_band')}; confidence_band: "
         f"{best_of('confidence_band')}"),
        ("Does the engine's own BUY decision select?",
         (f"ENGINE_BUY {_r(engine_buy.get('expectancy_r'))} over "
          f"{engine_buy.get('trades')} rows against ENGINE_REFUSED "
          f"{_r(engine_no.get('expectancy_r'))} over "
          f"{engine_no.get('trades')} rows. If those are the same number, the "
          "decision is not a selector and must not be treated as one"
          if engine_buy and engine_no else
          "the pool did not carry both decisions")),
        ("Does any conjunction of instrument, volatility, regime and HTF work?",
         (", ".join(f"{s['label']} holdout {_r(s['holdout'].get('expectancy_r'))}"
                    f" ({s['verdict']})"
                    for s in study["setups"] if s["kind"] == "conjunction")
          or (f"none of the {study['conjunction']['cells_measured']} measured "
              "cells cleared the adjusted threshold on development, so no "
              "conjunction reached validation"))),
        ("Do combinations beat their parts?",
         (", ".join(f"{s['label']} {_r(s['holdout'].get('expectancy_r'))} "
                    f"({s['verdict']})"
                    for s in study["setups"] if s["kind"] != "single")
          or "no combination cleared development and validation together")),
        ("How many candidates per day would the best surviving setup give?",
         (f"{_num(top['candidates_per_day'])} per session over "
          f"{study['periods']['holdout_sessions']} holdout sessions"
          if top else "no surviving setup, so there is nothing to size")),
        ("Does it survive every walk-forward fold?",
         (f"{top['setup']}: {top['walk_forward']}"
          if top else "nothing reached the walk-forward stage")),
        ("Is drawdown survivable?",
         (f"{top['setup']} max drawdown {_num(top['max_drawdown_r'])}R against "
          f"the pool's {_num(base.get('max_drawdown_r'))}R (drawdown of an "
          "overlapping candidate book, not of an account)"
          if top else "nothing survived to measure")),
        ("Is any of this option profitability?",
         ("no. every row is gross " + discovery.UNDERLYING_ONLY + ": no "
          "premium, spread, brokerage, slippage or decay has been applied. A "
          "positive underlying cohort can still lose money once they are")),
        ("What must happen before a surviving setup becomes a paper trade?",
         study["handoff_to_live"]["paper_rule"]),
    ]
    return [{"question": q, "answer": a} for q, a in pairs]


def render(study: dict) -> list[str]:
    out: list[str] = []
    per = study["periods"]
    out.append("PHASE 15B — historical market-setup discovery on the candidate "
               "pool")
    out.append("RESEARCH ONLY. " + study["cost_basis"] + ". No order is placed "
               "and no production behaviour changes.")
    out.append("")

    out.append("§1 the three periods")
    out.append(f"  development {per['development_rows']} rows over "
               f"{per['development_sessions']} sessions")
    out.append(f"  validation  {per['validation_rows']} rows over "
               f"{per['validation_sessions']} sessions")
    out.append(f"  holdout     {per['holdout_rows']} rows over "
               f"{per['holdout_sessions']} sessions")
    out.append(f"  {per['split_note']}")
    integ = study["integrity"]
    out.append(f"  integrity: {integ['data_errors']} rows failed the guard and "
               "were excluded from every cohort")
    for name in ("development", "validation", "holdout"):
        b = study["baselines"][name]
        out.append(f"  baseline {name:11s} n={b.get('trades')} "
                   f"T1={_pct(b.get('t1_pct'))} exp={_r(b.get('expectancy_r'))} "
                   f"PF={_num(b.get('profit_factor'))} "
                   f"RR={_num(b.get('mean_reward_risk'))}")
    out.append("")

    mt = study["multiple_testing"]
    out.append("§2 what the search cost in comparisons")
    out.append(f"  {mt['note']}")
    out.append(f"  proposals from development: {study['proposals']}; confirmed "
               f"on validation: {study['confirmed']}")
    out.append("")

    out.append("§3 every dimension, every bucket, on development rows only")
    for scan in study["dimensions"]:
        out.append(f"  {scan['dimension']}  "
                   f"({scan['buckets_tested']} buckets, "
                   f"{scan['rows_without_a_reading']} rows without a reading)")
        out.append(f"    {'bucket':16s} {'n':>7} {'T1':>7} {'exp':>10} "
                   f"{'lift':>10} {'sig':>7} {'PF':>6} {'RR':>6} {'move':>7}")
        for row in scan["buckets"]:
            flag = "" if row["measurable"] else "  (under the sample bar)"
            out.append(f"    {str(row['bucket'])[:16]:16s} {row['trades']:>7} "
                       f"{_pct(row.get('t1_pct')):>7} "
                       f"{_r(row.get('expectancy_r')):>10} "
                       f"{_r(row.get('lift_r')):>10} "
                       f"{str(row.get('lift_sigma')):>7} "
                       f"{_num(row.get('profit_factor')):>6} "
                       f"{_num(row.get('mean_reward_risk')):>6} "
                       f"{_num(row.get('median_mfe_points')):>7}{flag}")
    out.append("  read RR and move beside T1: a bucket with a nearer target")
    out.append("  reaches T1 more often without being a better setup, and cost")
    out.append("  is charged in rupees against the move, not against R")
    out.append("")

    grid = study["conjunction"]
    out.append(f"§4 the fixed conjunction: {' x '.join(grid['grid'])}")
    out.append(f"  {grid['cells_populated']} cells populated, "
               f"{grid['cells_measured']} reached the sample bar, "
               f"{grid['cells_under_the_bar']} did not and were not tested")
    out.append(f"  {grid['grid_choice_note']}")
    out.append(f"  {grid['note']}")
    out.append(f"    {'cell':64s} {'n':>7} {'T1':>7} {'exp':>10} {'lift':>10} "
               f"{'sig':>7} {'PF':>6}")
    for row in grid["buckets"][:15]:
        out.append(f"    {str(row['bucket'])[:64]:64s} {row['trades']:>7} "
                   f"{_pct(row.get('t1_pct')):>7} "
                   f"{_r(row.get('expectancy_r')):>10} "
                   f"{_r(row.get('lift_r')):>10} "
                   f"{str(row.get('lift_sigma')):>7} "
                   f"{_num(row.get('profit_factor')):>6}")
    if len(grid["buckets"]) > 15:
        out.append(f"    ... {len(grid['buckets']) - 15} further measured "
                   "cells in phase15b_dimensions.json. Every one of them is "
                   "counted in the threshold above")
    out.append("")

    out.append("§5 the shortlist on the holdout (measured once)")
    if not study["setups"]:
        out.append("  nothing cleared development and validation, so the "
                   "holdout was not consulted for any setup")
    out.append("  | Setup | Kind | n | T1 % | Exp | Lift | PF | Max DD | "
               "Cand/day | Walk-forward | Verdict |")
    out.append("  |---|---|---|---|---|---|---|---|---|---|---|")
    for s in study["setups"]:
        h = s["holdout"]
        out.append(f"  | {s['label']} | {s['kind']} | {h.get('trades')} | "
                   f"{_pct(h.get('t1_pct'))} | {_r(h.get('expectancy_r'))} | "
                   f"{_r(h.get('lift_r'))} | {_num(h.get('profit_factor'))} | "
                   f"{_num(h.get('max_drawdown_r'))} | "
                   f"{_num(h.get('signals_per_day'))} | "
                   f"{s['walk_forward'].get('verdict')} | {s['verdict']} |")
    for s in study["setups"]:
        out.append(f"    {s['verdict']}: {s['note']}")
        if not s["holdout"].get("geometry_comparable", True):
            out.append(f"    geometry: {s['holdout'].get('geometry_note')}")
        if s.get("geometry_selection"):
            out.append("    this selects on plan geometry, not on the market")
    out.append("")

    out.append("§6 dropped before the holdout")
    if not study["rejected"]:
        out.append("  nothing was dropped: no proposal reached validation")
    for row in study["rejected"]:
        out.append(f"  {row['dropped_at']:12s} {row['reason']}")
    out.append("")

    out.append("§7 ranking of survivors")
    if not study["ranking"]:
        out.append("  no survivors to rank. That is a result, not a gap: the "
                   "pool does not contain a durable selective setup at this "
                   "sample size")
    for row in study["ranking"]:
        out.append(f"  {row['rank']}. {row['setup']}  "
                   f"n={row['holdout_trades']} T1={_pct(row['t1_pct'])} "
                   f"exp={_r(row['expectancy_r'])} "
                   f"PF={_num(row['profit_factor'])} "
                   f"cand/day={_num(row['candidates_per_day'])} "
                   f"move={_num(row['median_mfe_points'])}pts "
                   f"[{row['verdict']}, {row['cost_basis']}]")
    out.append("")

    hand = study["handoff_to_live"]
    out.append("§8 handoff to the live option layer")
    out.append(f"  rule: {hand['paper_rule']}")
    for check in hand["must_verify_live"]:
        out.append(f"    - {check}")
    out.append(f"  not established here: {hand['not_established_here']}")
    out.append("")

    out.append("§9 the questions")
    for i, item in enumerate(questions(study), start=1):
        out.append(f"  {i:2d}. {item['question']}")
        out.append(f"      {item['answer']}")
    out.append("")

    out.append("§10 what these numbers are not")
    for line in study["disclaimers"]:
        out.append(f"  - {line}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trades", required=True,
                    help="candidate pool saved by market_scan.py / "
                         "phase14_rules.py --save-trades")
    ap.add_argument("--outdir", required=True, help="where artefacts are written")
    ap.add_argument("--dev-share", type=float, default=0.7,
                    help="share of in-sample sessions used for development")
    ap.add_argument("--folds", type=int, default=folds_mod.DEFAULT_FOLDS)
    ap.add_argument("--min-fold-trades", type=int,
                    default=folds_mod.MIN_FOLD_TRADES)
    args = ap.parse_args()

    saved = json.loads(Path(args.trades).read_text())
    in_sample = saved.get("in_sample") or []
    holdout = saved.get("holdout") or []
    if not in_sample or not holdout:
        raise SystemExit(
            "the pool needs both in_sample and holdout rows: rerun "
            "market_scan.py so the holdout sessions exist, because a discovery "
            "without untouched later sessions cannot be validated")
    print(f"pool: {len(in_sample)} in-sample + {len(holdout)} holdout rows")
    print(f"dimensions under test: {len(dims.DIMENSION_NAMES)}")

    study = discovery.run(in_sample, holdout, dev_share=args.dev_share,
                          folds=args.folds,
                          min_fold_trades=args.min_fold_trades)
    lines = render(study)
    print("\n" + "\n".join(lines))

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    payload = dict(study)
    payload["questions"] = questions(study)
    for name, body in (
            ("phase15b_discovery.json", payload),
            ("phase15b_dimensions.json", {"cuts": study["cuts"],
                                          "dimensions": study["dimensions"],
                                          "multiple_testing":
                                              study["multiple_testing"]}),
            ("phase15b_setups.json", {"setups": study["setups"],
                                      "rejected": study["rejected"],
                                      "ranking": study["ranking"]}),
            ("phase15b_handoff.json", study["handoff_to_live"]),
    ):
        path = out / name
        path.write_text(json.dumps(body, indent=2, sort_keys=True))
        written.append(path)
    md = out / "phase15b_discovery.md"
    md.write_text("\n".join(lines) + "\n")
    written.append(md)
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
