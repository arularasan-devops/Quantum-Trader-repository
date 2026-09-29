"""Audit the published scores against the outcome they claim to predict.

The five-year candidate pool showed ``win_probability`` reading a median of 82.8
while target-before-stop actually happened 42.8% of the time, and the
``win_probability >= 60`` gate lifting expectancy NEGATIVELY in both periods.
This asks the two questions that decide what to do about that:

  1. does the score RANK outcomes -- do high-scoring bars reach target more
     often than low-scoring ones?
  2. if it ranks, is its LEVEL right -- does a bar that says 83 reach target 83%
     of the time?

Only the second is repairable by recalibration. A score that fails the first
cannot be fixed by remapping, because remapping a number that carries no
information publishes the base rate for every bar.

It reads a pool saved by ``phase14_rules.py --save-trades``, so it costs
seconds, not hours:

    .venv/bin/python phase14_calibration.py --trades ~/p14_pool5.json
    .venv/bin/python phase14_calibration.py --trades ~/p14_pool5.json --remap win_probability

Nothing is replayed and nothing in production is changed. The remap table is a
PROPOSAL for what the score should publish, printed so it can be argued with
before any of it reaches the engine.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase14 import calibration as cal

VERDICT_ORDER = (cal.INVERTED, cal.CONFOUNDED, cal.NO_INFORMATION,
                 cal.MISCALIBRATED, cal.CALIBRATED, cal.NOT_ENOUGH_DATA)


def _print_curve(rel: dict, title: str) -> None:
    print(f"  {title}: {rel['trades']} rows, {rel['distinct_values']} distinct "
          f"values, base target-before-stop {rel['base_t1_pct']}%")
    print(f"    {'score range':<16}{'says':>7}{'n':>8}"
          f"{'actual T1':>11}{'gap':>8}{'exp R':>9}")
    for row in rel["buckets"]:
        thin = "  (thin)" if row["thin"] else ""
        rng = f"{row['score_min']}-{row['score_max']}"
        print(f"    {rng:<16}{row['mean_score']:>7}{row['trades']:>8}"
              f"{row['t1_pct']:>10}%{row['gap_pct']:>+8}"
              f"{row['expectancy_r']:>+9.3f}{thin}")


def _print_bands(ctrl: dict) -> None:
    """The control: same score, but compared only against equal targets."""
    print(f"  holdout with target distance held still (reward:risk bands) — "
          f"score-vs-R correlation overall {ctrl['r_correlation_overall']:+.3f}")
    print(f"    {'band':<12}{'n':>7}{'T1':>8}{'rank vs T1':>12}{'rank vs R':>11}")
    for b in ctrl["bands"]:
        thin = "  (thin)" if b["thin"] else ""
        print(f"    {b['band']:<12}{b['trades']:>7}{b['t1_pct']:>7}%"
              f"{b['rank_correlation_t1']:>+12.3f}"
              f"{b['rank_correlation_r']:>+11.3f}{thin}")


def _print_geometry(geo: dict) -> None:
    """What target distance alone does to the hit rate, before any score."""
    print("target-before-stop against TARGET DISTANCE, no score involved — "
          "this bounds what any filter can achieve:")
    print(f"    {'band':<12}{'n':>7}{'med rr':>9}{'T1':>8}{'exp R':>9}")
    for b in geo["bands"]:
        thin = "  (thin)" if b["thin"] else ""
        print(f"    {b['band']:<12}{b['trades']:>7}{b['median_reward_risk']:>9}"
              f"{b['t1_pct']:>7}%{b['expectancy_r']:>+9.3f}{thin}")
    if geo["t1_and_expectancy_conflict"]:
        print(f"  the highest hit rate ({geo['highest_t1_band']}) and the best "
              f"expectancy ({geo['best_expectancy_band']}) are NOT the same "
              f"geometry — a closer target is reached more often and pays less "
              f"each time, so 'raise the T1 rate' and 'raise expectancy' are "
              f"competing objectives here and one has to be chosen on purpose")
    elif geo["bands"]:
        print(f"  the highest hit rate and the best expectancy are both "
              f"{geo['highest_t1_band']}, so they do not conflict on this book")
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True,
                    help="pool saved by phase14_rules.py --save-trades")
    ap.add_argument("--min-bucket", type=int, default=cal.MIN_BUCKET)
    ap.add_argument("--curves", action="store_true",
                    help="print the full reliability table for every score")
    ap.add_argument("--remap", default=None,
                    help="print the proposed replacement curve for one score")
    ap.add_argument("--out", default=None, help="write the full audit as JSON")
    args = ap.parse_args()

    with open(args.trades) as fh:
        saved = json.load(fh)
    early, late = saved["in_sample"], saved["holdout"]
    print(f"auditing {len(early)} in-sample + {len(late)} holdout candidates "
          f"(htf_factor={saved.get('htf_factor')})")

    audit = cal.audit(early, late, min_bucket=args.min_bucket)
    base = audit["base_t1_pct"]
    print(f"the event being predicted — target reached BEFORE stop — happens "
          f"{base['in_sample']}% in-sample, {base['holdout']}% in holdout")
    print("a score is graded on RANK first (does it sort outcomes at all?) and "
          "on LEVEL second (does its number match the frequency that follows "
          "it?) — only the second is fixable by recalibration\n")

    _print_geometry(audit["geometry"]["holdout"])

    rows = sorted(audit["scores"],
                  key=lambda r: VERDICT_ORDER.index(r["verdict"]))
    for row in rows:
        kind = ("published as a probability" if row["is_probability"]
                else "inverse meter (higher should be worse)" if row["inverse"]
                else "0-100 meter")
        print(f"=== {row['field']}  [{row['verdict']}]  {kind}")
        b, a = row["holdout"], row["in_sample"]
        print(f"    rank correlation with target-before-stop: "
              f"in-sample {a['rank_correlation']:+.3f}  "
              f"holdout {b['rank_correlation']:+.3f}   "
              f"(T1 spans {b['spread_pct']} points across its holdout buckets)")
        if row["holdout_brier"] and row["holdout_brier"]["skill"] is not None:
            br = row["holdout_brier"]
            print(f"    it claims {br['mean_claim_pct']}% on average where "
                  f"{br['actual_pct']}% actually happened; skill against a flat "
                  f"{br['actual_pct']}% forecast: {br['skill']:+.3f} "
                  f"({'worse than saying nothing' if br['skill'] <= 0 else 'better than the base rate'})")
        print(f"    {row['why']}")
        if args.curves:
            _print_curve(a, "in-sample")
            _print_curve(b, "holdout")
            _print_bands(row["controlled"])

    print(f"\nby verdict: {audit['by_verdict']}")
    broken = [r["field"] for r in rows
              if r["verdict"] in (cal.INVERTED, cal.NO_INFORMATION)]
    if broken:
        print(f"scores that cannot be repaired by recalibration: "
              f"{', '.join(broken)} — they do not rank outcomes, so the only "
              f"honest options are to stop publishing them as a chance of "
              f"success and to stop gating on them")

    if args.remap:
        row = next((r for r in rows if r["field"] == args.remap), None)
        if row is None:
            raise SystemExit(f"{args.remap} is not an audited score")
        print(f"\nPROPOSED remap for {args.remap} (holdout frequencies) — "
              f"not applied anywhere:")
        table = cal.remap(row["holdout"])
        if not table:
            print("  no bucket had enough rows to fit a curve")
        for cell in table:
            print(f"  score {cell['score_min']}-{cell['score_max']} "
                  f"(publishes {cell['publishes_now']}) should publish "
                  f"{cell['should_publish']}   n={cell['trades']}")
        if row["verdict"] in (cal.INVERTED, cal.NO_INFORMATION):
            print("  WARNING: this score does not rank outcomes, so the table "
                  "above is close to flat by construction — applying it would "
                  "publish the base rate under the score's name and hide, not "
                  "fix, the fact that it carries no information")

    print("\nGraded on underlying candles: the event is target-before-stop on "
          "the index with the engine's own stop, gross of premium and spread. A "
          "calibrated direction score still says nothing about whether the "
          "option was tradable.")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"htf_factor": saved.get("htf_factor"),
                       "since": saved.get("since"), "audit": audit}, fh, indent=2)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
