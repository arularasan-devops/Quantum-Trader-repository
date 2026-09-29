"""The A+ candidate judge — walk-forward, attribution, shadow list, promotion.

RESEARCH / SHADOW ONLY. Reads a saved candidate pool and grades it. Places no
order, reads no live feed, and changes no gate, threshold, stop, target or exit.

    .venv/bin/python phase15_aplus.py --trades ~/p14_pool5.json
    .venv/bin/python phase15_aplus.py --trades ~/p14_pool5.json --folds 5 \
        --out ~/phase15_aplus.json --md ~/phase15_aplus.md

The pool comes from ``phase14_rules.py --save-trades``, so the slow replay is
paid once and every study here re-reads it in seconds.

What each section answers, in the order the spec asks:

* §20 integrity guard — how many rows are unusable, and why
* §7  chronological split — already in the pool file, reported not re-cut
* §8  walk-forward — does a selector work in most periods or in one
* §18 attribution — what its refusals were worth, winners missed vs losers avoided
* §14 capture — how much of the available excursion the exit kept, and what
  alternative exit rules would have made on the same candidates
* §16 components / §15 shadow list — with a T1 SCORE, never an uncalibrated %
* §17 frequency — A+ candidates per day, and the share of days with none
* §23 promotion gate — may any of this touch production (usually: not yet)
"""
from __future__ import annotations

import argparse
import json
import os

from app.research.phase14 import rules as p14
from app.research.phase15 import (
    attribution,
    components,
    exits,
    folds as folds_mod,
    promotion,
    shadow,
)

# Selectors graded by walk-forward. The whole pool is always the baseline, and
# each named selector is one of the engine's own published gates, so the output
# says whether the gates the tool already applies survive a fold split.
def _selectors(names: list[str] | None) -> dict:
    chosen = names or list(p14.TESTABLE_RULES)
    return {n: p14.TESTABLE_RULES[n] for n in chosen if n in p14.TESTABLE_RULES}


def _arm(trades: list[dict]) -> dict:
    rs = [float(t["r"]) for t in trades]
    n = len(rs)
    if not n:
        return {"trades": 0, "expectancy_r": None, "profit_factor": None}
    gain = sum(r for r in rs if r > 0)
    loss = -sum(r for r in rs if r < 0)
    return {
        "trades": n,
        "expectancy_r": round(sum(rs) / n, 3),
        "profit_factor": round(gain / loss, 2) if loss > 0 else None,
    }


def _cards(pool: list[dict], bar: float, limit: int,
           ranking: dict | None = None) -> dict:
    built = []
    for row in pool[:limit]:
        scored = components.score_row(row)
        c = shadow.card(row, scored["components"], calibration=None)
        c["component_reasons"] = scored["reasons"]
        built.append(c)
    return shadow.shadow_list(built, bar=bar, ranking=ranking)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True,
                    help="pool saved by phase14_rules.py --save-trades")
    ap.add_argument("--folds", type=int, default=folds_mod.DEFAULT_FOLDS)
    ap.add_argument("--min-fold-trades", type=int,
                    default=folds_mod.MIN_FOLD_TRADES)
    ap.add_argument("--selectors", default=None,
                    help="comma-separated subset of the engine's gates to grade")
    ap.add_argument("--a-plus-bar", type=float, default=75.0,
                    help="A+ composite score a candidate must reach")
    ap.add_argument("--shadow-sample", type=int, default=400,
                    help="candidates to build shadow cards from")
    ap.add_argument("--out", default=None, help="write the study as JSON")
    ap.add_argument("--md", default=None, help="write the report as markdown")
    ap.add_argument("--artefacts", default=None,
                    help="directory for the four §22 report pairs")
    args = ap.parse_args()

    with open(args.trades) as fh:
        saved = json.load(fh)
    in_sample = saved["in_sample"]
    holdout = saved["holdout"]

    # §20 first: nothing downstream may see a row with risk <= 0 or a
    # non-finite R, because one such row rewrites every aggregate above it.
    g_in = attribution.guard(in_sample)
    g_out = attribution.guard(holdout)
    in_sample, holdout = g_in["clean_rows"], g_out["clean_rows"]
    pool = in_sample + holdout

    selectors = _selectors(
        [s.strip() for s in args.selectors.split(",")] if args.selectors else None)

    print(f"pool: {g_in['rows']} in-sample + {g_out['rows']} holdout rows; "
          f"{g_in['data_errors'] + g_out['data_errors']} failed the integrity "
          f"guard and were excluded")
    if g_in["by_failure"] or g_out["by_failure"]:
        print(f"  integrity failures: {g_in['by_failure']} / {g_out['by_failure']}")

    # §8
    wf = folds_mod.compare(pool, selectors, folds=args.folds,
                           min_fold_trades=args.min_fold_trades)
    base = wf["baseline"]
    print(f"\n§8 walk-forward over {base['folds']} chronological folds "
          f"(session-aligned, no fold shares a session)")
    print(f"  baseline (every candidate): {base['verdict']} — {base['note']}")
    ranked = sorted(wf["selectors"].items(),
                    key=lambda kv: -(kv[1].get("median_oos_expectancy_r") or -9))
    for name, row in ranked:
        med = row.get("median_oos_expectancy_r")
        print(f"  {name:32s} {row['verdict']:26s} "
              f"median {med if med is not None else '—':>7} R  "
              f"{row.get('positive_folds')}/{row.get('measured_folds')} folds+  "
              f"{'beats baseline' if row.get('beats_baseline') else 'no better than baseline'}")

    # §18 + §14, for the selectors that walk-forward did not already reject.
    print("\n§18 what each selector's refusals were worth "
          "(missed winners vs avoided losers)")
    attributions = {}
    captures = {}
    for name in selectors:
        keep = selectors[name]
        att = attribution.attribute(holdout, keep, rules=selectors)
        attributions[name] = att
        captures[name] = attribution.capture([t for t in holdout if keep(t)])
        print(f"  {name:32s} keeps {att['selection_pct']:>5}%  "
              f"missed {att['missed_winners']:>5} winners  "
              f"avoided {att['avoided_losers']:>5} losers  "
              f"refusal {att['rejection_r_per_candidate']}R/candidate")

    # Both directions are in the pool already — a SHORT underlying candidate is
    # the PE the engine would buy — but they were only ever graded together, so
    # a one-sided book would have been invisible.
    print("\n§8b by direction (a SHORT candidate is the PE side, not a sale)")
    by_side = {}
    for side in ("LONG", "SHORT"):
        rows = [t for t in holdout if t.get("side") == side]
        wf_side = folds_mod.walk_forward(
            rows, lambda _t: True, folds=args.folds,
            min_fold_trades=args.min_fold_trades)
        cap_side = attribution.capture(rows)
        by_side[side] = {"walk_forward": _drop_rows(wf_side),
                         "capture": cap_side}
        print(f"  {side:5s} n={len(rows):<6} T1 {cap_side['t1_pct']}%  "
              f"median fold {wf_side.get('median_oos_expectancy_r')}R  "
              f"{wf_side.get('positive_folds')}/{wf_side.get('measured_folds')}"
              f" folds+  {wf_side.get('verdict')}")
    print("  a side is only a filter if its verdict is stable across folds; one "
          "side reading better in total is what noise looks like")

    print("\n§14 profit capture on the whole holdout book (no exit changed)")
    cap = attribution.capture(holdout)
    print(f"  T1 {cap['t1_pct']}%  SL {cap['sl_pct']}%  "
          f"mean MFE {cap['mean_mfe_r']}R  mean MAE {cap['mean_mae_r']}R  "
          f"capture {cap['capture_pct']}% of the excursion offered")
    print(f"  reached +0.5R then ended a loss: "
          f"{cap['reached_half_r_then_lost']} "
          f"({cap['reached_half_r_then_lost_pct']}%)")

    # The exit counterfactuals. Run on the holdout only: an exit rule chosen on
    # the same rows it is measured on is fitted, not tested.
    ex = exits.evaluate(holdout, folds=args.folds,
                        min_fold_trades=args.min_fold_trades)
    print("\n§14b what the same candidates would have made under other exits "
          "(holdout, gross)")
    ex_ranked = sorted(ex["variants"].items(),
                       key=lambda kv: -(kv[1].get("expectancy_r") or -9))
    for name, row in ex_ranked:
        if not row.get("priced"):
            print(f"  {name:24s} not priced — {row.get('note')}")
            continue
        print(f"  {name:24s} {row['basis']:19s} "
              f"exp {row['expectancy_r']:+.4f}R  "
              f"PF {row['profit_factor']}  win {row['win_pct']}%  "
              f"median fold {row['median_oos_expectancy_r']}R  "
              f"{row['positive_folds']}/{row['measured_folds']} folds+  "
              f"{row['verdict']:26s} "
              f"{'beats the book as traded' if row['beats_as_traded'] else ''}")
    print(f"  {ex['cost_warning']}")

    # §16 first, because whether the composite sorts outcomes at all decides
    # what the §15 list is allowed to claim about its own ordering.
    ranking = shadow.rank_cohort(holdout, _card_score)
    cards = _cards(holdout, args.a_plus_bar, args.shadow_sample, ranking)
    print(f"\n§15 shadow list at an A+ bar of {args.a_plus_bar}: "
          f"{cards['qualified']} of {cards['candidates_considered']} candidates "
          f"qualified, {len(cards['preferred'])} shown, "
          f"{cards['unscored']} unscorable")
    for c in cards["preferred"]:
        print(f"  {c['instrument']} {c['side']:5s} score {c['a_plus_score']} "
              f"rank {c['t1_rank']}  probability {c['t1_probability']} "
              f"({c['probability_status']})  basis {c['basis']}")
        print(f"      unmeasured: {', '.join(c['unmeasured_components']) or 'none'}")
    if not cards["preferred"]:
        print("  no candidate cleared the bar — that is a valid result and the "
              "list is not padded from the watch rows")
    print(f"  ranking: {cards['rank_status']} — {cards['rank_note']}")

    # §17
    freq = shadow.frequency(
        holdout, lambda t: _card_score(t) is not None
        and _card_score(t) >= args.a_plus_bar)
    print(f"\n§17 A+ frequency over {freq.get('sessions')} holdout sessions: "
          f"mean {freq.get('mean_per_day')}/day, median "
          f"{freq.get('median_per_day')}, p25 {freq.get('p25_per_day')}, "
          f"p75 {freq.get('p75_per_day')}")
    print(f"  days with none: {freq.get('days_with_none_pct')}%  "
          f"distribution {freq.get('days_with')}")

    print("\n§16 does the A+ composite sort outcomes?")
    for band in ranking.get("bands", []):
        print(f"  band {band['band']} score {band['score_from']}-{band['score_to']}"
              f"  n={band['candidates']:<6} T1 {band['t1_pct']}%  "
              f"exp {band['expectancy_r']:+}R")
    print(f"  sorts outcomes: {ranking.get('sorts_outcomes')} — "
          f"{ranking.get('note')}")

    # §23
    print("\n§23 promotion gate")
    gates = {}
    base_hold = _arm(holdout)
    for name, keep in selectors.items():
        dev = _arm([t for t in in_sample if keep(t)])
        hold = _arm([t for t in holdout if keep(t)])
        verdict = promotion.evaluate(
            name=name, dev=dev, holdout=hold, baseline_holdout=base_hold,
            walk_forward=wf["selectors"][name],
            attribution_=attributions[name],
            integrity={"data_errors": g_in["data_errors"] + g_out["data_errors"]},
            costed=False)
        gates[name] = verdict
        print(f"  {name:32s} {verdict['verdict']}")
        if verdict["failed_requirements"]:
            print(f"      failed: {', '.join(verdict['failed_requirements'])}")

    promotable = [n for n, v in gates.items()
                  if v["verdict"] == promotion.PRODUCTION_CANDIDATE]
    print(f"\npromotable rules: {len(promotable)}"
          + (f" ({', '.join(promotable)})" if promotable else
             " — nothing in this pool may gate production yet"))
    print("every figure above is UNDERLYING_ONLY: gross of spread, brokerage and "
          "taxes, so no rule here can be called profitable")

    study = {
        "integrity": {"in_sample": _drop_rows(g_in),
                      "holdout": _drop_rows(g_out)},
        "walk_forward": wf,
        "by_direction": by_side,
        "attribution": attributions,
        "capture": {"holdout": cap, "by_selector": captures},
        "shadow_list": cards,
        "frequency": freq,
        "composite_ranking": ranking,
        "promotion": gates,
        "exits": ex,
        "baseline_holdout": base_hold,
        "basis": "UNDERLYING_ONLY",
    }
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(study, fh, indent=1)
        print(f"\nwrote {args.out}")
    if args.md:
        with open(args.md, "w") as fh:
            fh.write(_markdown(study, args))
        print(f"wrote {args.md}")
    if args.artefacts:
        for path in _artefacts(args.artefacts, study, args):
            print(f"wrote {path}")


def _artefacts(directory: str, study: dict, args) -> list[str]:
    """The four §22 report pairs. Each pair is one question, JSON and markdown."""
    os.makedirs(directory, exist_ok=True)
    pool = {
        "basis": study["basis"],
        "integrity": study["integrity"],
        "capture": study["capture"]["holdout"],
        "frequency": study["frequency"],
    }
    tests = {
        "basis": study["basis"],
        "walk_forward": study["walk_forward"],
        "attribution": study["attribution"],
        "capture_by_selector": study["capture"]["by_selector"],
    }
    aplus = {
        "basis": study["basis"],
        "shadow_list": study["shadow_list"],
        "composite_ranking": study["composite_ranking"],
        "frequency": study["frequency"],
    }
    oos = {
        "basis": study["basis"],
        "folds": study["walk_forward"],
        "promotion": study["promotion"],
        "exits": study["exits"],
        "answers": _answers(study, args),
    }
    written = []
    for name, payload, body in (
            ("phase15_candidate_pool", pool, _md_pool(study)),
            ("phase15_rule_tests", tests, _md_tests(study)),
            ("phase15_a_plus_shadow", aplus, _md_shadow(study, args)),
            ("phase15_oos", oos, _md_oos(study, args))):
        j = os.path.join(directory, f"{name}.json")
        m = os.path.join(directory, f"{name}.md")
        with open(j, "w") as fh:
            json.dump(payload, fh, indent=1)
        with open(m, "w") as fh:
            fh.write(body)
        written += [j, m]
    return written


def _answers(study: dict, args) -> dict:
    """The spec's 17 questions, answered only from what this run measured.

    Where the pool cannot answer a question, the answer says so and names the
    data that would answer it. An unanswerable question with a confident answer
    is the failure mode this whole phase exists to prevent.
    """
    cap = study["capture"]["holdout"]
    wf = study["walk_forward"]
    freq = study["frequency"]
    rank = study["composite_ranking"]
    gates = study["promotion"]
    att = study["attribution"]
    promotable = [n for n, v in gates.items()
                  if v["verdict"] == promotion.PRODUCTION_CANDIDATE]
    best = max(wf["selectors"].items(),
               key=lambda kv: (kv[1].get("median_oos_expectancy_r") or -9),
               default=(None, {}))
    beats = [n for n, r in wf["selectors"].items() if r.get("beats_baseline")]
    stable = [n for n, r in wf["selectors"].items()
              if r.get("verdict") == folds_mod.STABLE]
    # A feature "matters" only if it beats the baseline AND holds across folds.
    # Beating the baseline median in one stretch is how a curve-fit looks.
    matters = [n for n in beats if n in stable]
    no_effect = [n for n, r in wf["selectors"].items()
                 if not r.get("beats_baseline")]
    worst_missed = sorted(att.items(),
                          key=lambda kv: -(kv[1]["missed_winners"] or 0))
    chain = ("cannot be answered from this pool: it is underlying-only, with no "
             "bid/ask, OI, IV or delta. Live chain capture is the only thing "
             "that answers it")
    return {
        "1_baseline_t1_before_sl": f"{cap['t1_pct']}% of holdout candidates "
        f"reached T1 before SL ({cap['sl_pct']}% stopped first)",
        "2_best_selective_subset":
            (f"{best[0]} at a median out-of-sample "
             f"{best[1].get('median_oos_expectancy_r')}R — walk-forward "
             f"{best[1].get('verdict')}, so this is the best of the pool, not a "
             "validated subset" if best[0] else
             "none — no selector was measurable"),
        "3_trades_per_day": f"A+ bar {args.a_plus_bar}: mean "
        f"{freq.get('mean_per_day')}/day, median {freq.get('median_per_day')}, "
        f"{freq.get('days_with_none_pct')}% of sessions produced none",
        "4_oos_t1_rate": f"{cap['t1_pct']}% on the holdout book; no subset "
        "cleared the promotion gate, so no subset's T1 rate is validated",
        "5_oos_net_expectancy": "not measured — every R here is gross. Net "
        "expectancy requires the option cost model against a captured chain",
        "6_oos_profit_factor": f"{study['baseline_holdout'].get('profit_factor')} "
        f"over the whole holdout book "
        f"({study['baseline_holdout'].get('trades')} candidates), gross",
        "7_max_drawdown": f"worst fold drawdown "
        f"{wf['baseline'].get('worst_fold_drawdown_r')}R on the unchanged book",
        "8_missed_winners": (f"{worst_missed[0][0]} refuses the most: "
                             f"{worst_missed[0][1]['missed_winners']} winners"
                             if worst_missed else "no selector graded"),
        "9_avoided_losers": (f"{worst_missed[0][0]} avoided "
                             f"{worst_missed[0][1]['avoided_losers']} losers"
                             if worst_missed else "no selector graded"),
        "10_features_that_matter": (
            ", ".join(matters) if matters else
            "none. " + (f"{len(beats)} selector(s) beat the baseline median "
                        f"({', '.join(beats)}) but none held across folds, and "
                        "beating a median in one stretch is what a curve-fit "
                        "looks like" if beats else
                        "no selector beat the baseline out of sample")),
        "11_decoration_features": (", ".join(no_effect) if no_effect else
                                   "none"),
        "12_ce_vs_pe": chain,
        "13_option_vs_futures": "not answered: fewer than 30 resolved futures "
        "outcomes exist, and the spec forbids declaring a vehicle winner below "
        "that",
        "14_entry_quality": "partly: risk_over_noise (stop outside one candle's "
        "range) is measurable and is scored; the premium half of entry quality "
        "— was the option chased — is not on an underlying row",
        "15_spread_and_vehicle_economics": chain + ". The Flow audit already "
        "showed it decides the outcome: 0% of legs whose round trip cost more "
        "than 10% of premium were net winners",
        "16_pullback": ("pullback_entry: "
                        + str(wf["selectors"].get("pullback_entry", {})
                              .get("verdict", "not graded"))),
        "17_simplest_a_plus_rule": (
            f"{promotable[0]} — the only rule that cleared every promotion "
            f"requirement" if promotable else
            "none. No rule in this pool cleared the promotion gate, so the "
            "honest answer is that no A+ rule is supported by out-of-sample "
            "evidence yet"),
        "18_exit_rules": _exit_answer(study),
        "composite_sorts_outcomes": rank.get("sorts_outcomes"),
        "stable_selectors": stable,
    }


def _exit_answer(study: dict) -> str:
    """Whether any alternative exit beat the book as traded, and by how much.

    Asked because fifteen entry conditions have now failed the promotion gate
    while the book's capture of its own favourable excursion is a low
    single-digit percentage. If an exit variant beats the traded book across
    folds, it is the largest measured improvement available and it needs no
    better signal.
    """
    ex = study["exits"]
    priced = {n: r for n, r in ex["variants"].items() if r.get("priced")}
    if not priced:
        return "no exit variant could be priced from this pool"
    winners = [(n, r) for n, r in priced.items()
               if n != "as_traded" and r.get("beats_as_traded")
               and r.get("verdict") == folds_mod.STABLE]
    unpriceable = [n for n, r in ex["variants"].items() if not r.get("priced")]
    base = priced.get("as_traded", {})
    if not winners:
        return ("none of the tested exits beat the book as traded across folds "
                f"(as traded: {base.get('expectancy_r')}R, median fold "
                f"{ex.get('as_traded_median_r')}R). Not priceable without the "
                f"per-bar path: {', '.join(unpriceable) or 'none'}")
    best = max(winners, key=lambda kv: kv[1]["expectancy_r"])
    return (f"{best[0]} ({best[1]['basis']}): {best[1]['expectancy_r']}R against "
            f"{base.get('expectancy_r')}R as traded, PF "
            f"{best[1]['profit_factor']}, "
            f"{best[1]['positive_folds']}/{best[1]['measured_folds']} folds "
            f"positive, {best[1]['verdict']}. Gross: an earlier target cuts R "
            "per trade without cutting cost per trade, so this must be re-run "
            "against a captured chain before it can be called profitable")


def _md_pool(study: dict) -> str:
    cap = study["capture"]["holdout"]
    integ = study["integrity"]
    return "\n".join([
        "# Phase 15 — the candidate pool",
        "",
        "RESEARCH ONLY. Every BUY, WAIT and AVOID bar the replay evaluated, not "
        "only the trades the engine took — a gate cannot be graded against the "
        "trades it would have thrown away unless those rows exist.",
        "",
        f"Basis: **{study['basis']}**.",
        "",
        "## Integrity (§20)",
        "",
        "| period | rows | excluded | failures |",
        "|---|---|---|---|",
        f"| in-sample | {integ['in_sample']['rows']} | "
        f"{integ['in_sample']['data_errors']} | "
        f"{integ['in_sample']['by_failure'] or '—'} |",
        f"| holdout | {integ['holdout']['rows']} | "
        f"{integ['holdout']['data_errors']} | "
        f"{integ['holdout']['by_failure'] or '—'} |",
        "",
        "## Outcomes on the unchanged holdout book (§14)",
        "",
        f"- target before stop: **{cap['t1_pct']}%**; stopped first "
        f"{cap['sl_pct']}%",
        f"- mean MFE {cap['mean_mfe_r']}R, mean MAE {cap['mean_mae_r']}R",
        f"- capture {cap['capture_pct']}% of the excursion the book offered",
        f"- reached +0.5R and still ended a loss: "
        f"{cap['reached_half_r_then_lost']} ({cap['reached_half_r_then_lost_pct']}%)",
        "",
        "Target-before-stop is not a signal quality to select for — it is set "
        "mostly by how far the target sits. It is reported here as the baseline "
        "any subset has to beat, not as an objective.",
        "",
    ])


def _md_tests(study: dict) -> str:
    wf = study["walk_forward"]
    lines = [
        "# Phase 15 — rule tests",
        "",
        "Each of the engine's own published gates, graded across chronological "
        "folds and against the trades it refused. No gate is changed by this.",
        "",
        f"Basis: **{study['basis']}**.",
        "",
        "| selector | walk-forward verdict | median OOS R | folds+ | keeps | "
        "missed winners | avoided losers |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, row in sorted(wf["selectors"].items(),
                            key=lambda kv: -(kv[1].get("median_oos_expectancy_r")
                                             or -9)):
        att = study["attribution"].get(name, {})
        lines.append(
            f"| {name} | {row['verdict']} | "
            f"{row.get('median_oos_expectancy_r')} | "
            f"{row.get('positive_folds')}/{row.get('measured_folds')} | "
            f"{att.get('selection_pct')}% | {att.get('missed_winners')} | "
            f"{att.get('avoided_losers')} |")
    lines += [
        "",
        "A selector that keeps ~100% of the book rejects nothing and cannot be "
        "the reason for a result, whatever its kept arm reads.",
        "",
    ]
    return "\n".join(lines)


def _md_shadow(study: dict, args) -> str:
    lst = study["shadow_list"]
    rank = study["composite_ranking"]
    freq = study["frequency"]
    lines = [
        "# Phase 15 — the A+ shadow list",
        "",
        "SHADOW ONLY. Nothing here is routed, ordered or shown as a production "
        "call.",
        "",
        f"Bar {lst['bar']}: **{lst['qualified']}** of "
        f"{lst['candidates_considered']} candidates qualified; "
        f"{len(lst['preferred'])} listed, {lst['unscored']} unscorable.",
        "",
        f"Ordering: **{lst['rank_status']}** — {lst['rank_note']}.",
        "",
        "| instrument | side | A+ score | T1 rank | T1 probability | "
        "unmeasured | basis |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in lst["preferred"]:
        lines.append(
            f"| {c['instrument']} | {c['side']} | {c['a_plus_score']} | "
            f"{c['t1_rank']} | {c['t1_probability'] or 'not published'} | "
            f"{', '.join(c['unmeasured_components']) or 'none'} | "
            f"{c['basis']} |")
    if not lst["preferred"]:
        lines.append("| — | — | — | — | — | — | — |")
        lines.append("")
        lines.append("No candidate cleared the bar. That is a valid result and "
                     "the list is not padded from the watch rows.")
    lines += [
        "",
        "## Does the composite sort outcomes? (§16)",
        "",
        "| band | score range | candidates | T1 | expectancy |",
        "|---|---|---|---|---|",
    ]
    for b in rank.get("bands", []):
        lines.append(f"| {b['band']} | {b['score_from']}–{b['score_to']} | "
                     f"{b['candidates']} | {b['t1_pct']}% | "
                     f"{b['expectancy_r']:+}R |")
    lines += [
        "",
        f"Sorts outcomes: **{rank.get('sorts_outcomes')}**. Until that ordering "
        "holds out of sample, the score carries no percentage — the code "
        "refuses to emit one without a calibration record over at least "
        f"{shadow.MIN_CALIBRATION_SAMPLE} resolved out-of-sample outcomes.",
        "",
        "## Frequency (§17)",
        "",
        f"- mean {freq.get('mean_per_day')}/day, median "
        f"{freq.get('median_per_day')}, p25 {freq.get('p25_per_day')}, "
        f"p75 {freq.get('p75_per_day')}, max {freq.get('max_per_day')}",
        f"- {freq.get('days_with_none_pct')}% of sessions produced no A+ "
        f"candidate: {freq.get('days_with')}",
        "",
        f"Bar used: {args.a_plus_bar}.",
        "",
    ]
    return "\n".join(lines)


def _md_oos(study: dict, args) -> str:
    wf = study["walk_forward"]
    gates = study["promotion"]
    lines = [
        "# Phase 15 — out-of-sample validation and promotion",
        "",
        f"Basis: **{study['basis']}** — gross of spread, brokerage and taxes.",
        "",
        "## Folds (§8)",
        "",
        "| fold | from | to | candidates | selected | expectancy | T1 | "
        "drawdown |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in wf["baseline"].get("rows", []):
        lines.append(
            f"| {row['fold']} | {row['from']} | {row['to']} | "
            f"{row['candidates_in_fold']} | {row['selected']} | "
            f"{row.get('expectancy_r')} | {row.get('t1_pct')}% | "
            f"{row.get('max_drawdown_r')} |")
    lines += [
        "",
        f"Baseline verdict: **{wf['baseline']['verdict']}** — "
        f"{wf['baseline']['note']}",
        "",
        "## Promotion gate (§23)",
        "",
        "| rule | verdict | failed requirements |",
        "|---|---|---|",
    ]
    for name, v in gates.items():
        lines.append(f"| {name} | {v['verdict']} | "
                     f"{', '.join(v['failed_requirements']) or '—'} |")
    ex = study["exits"]
    lines += [
        "",
        "## Exit counterfactuals on the same candidates",
        "",
        f"Assumption: {ex['assumption']}.",
        "",
        "| exit rule | priced from | expectancy | PF | win | median fold | "
        "folds + | verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, row in sorted(ex["variants"].items(),
                            key=lambda kv: -(kv[1].get("expectancy_r") or -9)):
        if not row.get("priced"):
            lines.append(f"| {name} | {row.get('basis')} | not priced | — | — | "
                         f"— | — | {exits.REQUIRES_PATH_DATA} |")
            continue
        lines.append(
            f"| {name} | {row['basis']} | "
            f"{row['expectancy_r']:+}R | {row['profit_factor']} | "
            f"{row['win_pct']}% | {row['median_oos_expectancy_r']}R | "
            f"{row['positive_folds']}/{row['measured_folds']} | "
            f"{row['verdict']} |")
    lines += ["", ex["cost_warning"] + ".", ""]
    lines += ["", "## The 17 questions (§22)", ""]
    for key, value in _answers(study, args).items():
        lines.append(f"- **{key}**: {value}")
    lines.append("")
    return "\n".join(lines)


def _card_score(row: dict) -> float | None:
    return shadow.composite(components.score_row(row)["components"])["score"]


def _drop_rows(guard: dict) -> dict:
    return {k: v for k, v in guard.items() if k != "clean_rows"}


def _markdown(study: dict, args) -> str:
    wf = study["walk_forward"]
    base = wf["baseline"]
    freq = study["frequency"]
    cap = study["capture"]["holdout"]
    rank = study["composite_ranking"]
    gates = study["promotion"]
    promotable = [n for n, v in gates.items()
                  if v["verdict"] == promotion.PRODUCTION_CANDIDATE]
    lines = [
        "# Phase 15 — A+ candidate judge",
        "",
        "RESEARCH / SHADOW ONLY. No gate, threshold, stop, target, exit or order "
        "path is changed by this study.",
        "",
        f"Basis: **{study['basis']}** — every R below is gross of spread, "
        "brokerage and taxes.",
        "",
        "## §20 integrity",
        "",
        f"- in-sample: {study['integrity']['in_sample']['data_errors']} of "
        f"{study['integrity']['in_sample']['rows']} rows excluded",
        f"- holdout: {study['integrity']['holdout']['data_errors']} of "
        f"{study['integrity']['holdout']['rows']} rows excluded",
        "",
        "## §8 walk-forward",
        "",
        f"Baseline over {base['folds']} folds: **{base['verdict']}** — {base['note']}",
        "",
        "| selector | verdict | median OOS R | folds+ | beats baseline |",
        "|---|---|---|---|---|",
    ]
    for name, row in sorted(wf["selectors"].items(),
                            key=lambda kv: -(kv[1].get("median_oos_expectancy_r")
                                             or -9)):
        lines.append(
            f"| {name} | {row['verdict']} | "
            f"{row.get('median_oos_expectancy_r')} | "
            f"{row.get('positive_folds')}/{row.get('measured_folds')} | "
            f"{'yes' if row.get('beats_baseline') else 'no'} |")
    lines += [
        "",
        "## §18 refusal value",
        "",
        "| selector | keeps | missed winners | avoided losers | R per refusal |",
        "|---|---|---|---|---|",
    ]
    for name, att in study["attribution"].items():
        lines.append(
            f"| {name} | {att['selection_pct']}% | {att['missed_winners']} | "
            f"{att['avoided_losers']} | {att['rejection_r_per_candidate']} |")
    lines += [
        "",
        "## §14 capture (holdout, exits unchanged)",
        "",
        f"- T1 {cap['t1_pct']}%, SL {cap['sl_pct']}%",
        f"- mean MFE {cap['mean_mfe_r']}R, mean MAE {cap['mean_mae_r']}R, "
        f"capture {cap['capture_pct']}% of the excursion offered",
        f"- reached +0.5R then ended a loss: {cap['reached_half_r_then_lost']} "
        f"({cap['reached_half_r_then_lost_pct']}%)",
        "",
        "## §15-§17 shadow list and frequency",
        "",
        f"- A+ bar {args.a_plus_bar}: {study['shadow_list']['qualified']} of "
        f"{study['shadow_list']['candidates_considered']} candidates qualified",
        f"- frequency: mean {freq.get('mean_per_day')}/day, median "
        f"{freq.get('median_per_day')}, {freq.get('days_with_none_pct')}% of "
        "sessions produced none",
        f"- composite sorts outcomes: {rank.get('sorts_outcomes')}",
        "",
        "T1 is published as a SCORE and a RANK. No candidate carries a "
        "percentage: no calibration record has matched a score to an observed "
        "out-of-sample target-before-stop rate, and the code refuses to emit a "
        "probability without one.",
        "",
        "## §23 promotion",
        "",
        f"Promotable rules: **{len(promotable)}**"
        + (f" ({', '.join(promotable)})" if promotable else
           " — nothing here may gate production."),
        "",
        "| rule | verdict | failed requirements |",
        "|---|---|---|",
    ]
    for name, v in gates.items():
        lines.append(f"| {name} | {v['verdict']} | "
                     f"{', '.join(v['failed_requirements']) or '—'} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
