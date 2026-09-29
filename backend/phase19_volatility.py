"""Which instrument, in which volatility state, reached T1. RESEARCH ONLY.

Reads a candidate pool saved by ``phase14_rules.py --save-trades`` and grades
every instrument x volatility band: measured T1-before-SL rate, gross
expectancy, walk-forward stability across chronological folds, and how many
sessions the band could even furnish a card for.

Nothing here changes production. No probability is published, and no band is
labelled A+: the T1 model is not calibrated, so a percentage badge would be
decoration.

    python phase19_volatility.py --trades ~/p14_pool5_paths.json \
        --out ~/vol_bands.json --md ~/vol_bands.md
"""
from __future__ import annotations

import argparse
import json

from app.research.phase15 import attribution
from app.research.phase19 import volatility as vol


def _r(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.3f}R"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value}%"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value}"


def _render(study: dict) -> list[str]:
    out: list[str] = []
    base = study["baseline"]
    out.append("§1 the pool, and the bands it was cut into")
    out.append(f"  rows={study['rows']}  sessions={study['sessions']}  "
               f"no volatility reading={study['rows_without_a_volatility_reading']}")
    out.append(f"  cut points fitted on {study['cut_points_fitted_on']}")
    for inst, cuts in study["cut_points_bps"].items():
        out.append(f"  {inst:10s} quartiles at {cuts[0]} / {cuts[1]} / {cuts[2]} bps "
                   "of the underlying")
    out.append("")
    out.append("§2 the baseline every band has to beat (every candidate, no filter)")
    out.append(f"  {base['verdict']}  median {_r(base.get('median_oos_expectancy_r'))}"
               f"  {base.get('positive_folds')}/{base.get('measured_folds')} folds+")
    out.append("")

    out.append("§3 the instrument on its own, before volatility is split out")
    out.append(f"  {'instrument':10s} {'rows':>6} {'T1':>7} {'exp':>9} "
               f"{'med bps':>8} {'move':>7}  verdict")
    for row in study["instrument_summary"]:
        out.append(f"  {row['instrument']:10s} {row['rows']:>6} "
                   f"{_pct(row['t1_pct']):>7} {_r(row['expectancy_r']):>9} "
                   f"{_num(row['median_vol_bps']):>8} "
                   f"{_num(row['median_mfe_points']):>7}  "
                   f"{row['walk_forward_verdict']}"
                   f"{'  beats baseline' if row['beats_baseline'] else ''}")
    out.append("  if an instrument's whole book grades the same as its loudest band,")
    out.append("  the volatility split found nothing and the instrument was the story")
    out.append("")

    out.append("§4 instrument x volatility band, graded")
    out.append(f"  {'instrument':10s} {'band':7s} {'range':>14} {'dev':>5} "
               f"{'out':>5} {'T1 out':>7} {'exp out':>9} {'move':>7} "
               f"{'days':>6}  verdict")
    for row in study["bands"]:
        out.append(
            f"  {row['instrument']:10s} {row['band']:7s} "
            f"{row['vol_bps_range']:>14} {row['dev_rows']:>5} "
            f"{row['holdout_rows']:>5} {_pct(row['holdout']['t1_pct']):>7} "
            f"{_r(row['holdout']['expectancy_r']):>9} "
            f"{_num(row['combined']['median_mfe_points']):>7} "
            f"{_pct(row['session_share_pct']):>6}  {row['verdict']}")
    thin = [r for r in study["bands"] if r["evidence"] != vol.ENOUGH]
    if thin:
        out.append(f"  {len(thin)} of {len(study['bands'])} bands are under the "
                   f"{study['min_dev_rows']}/{study['min_holdout_rows']} sample bar "
                   "and read REQUIRES_MORE_DATA")
    out.append("")

    out.append("§5 which instrument suits this setup, ranked on measured holdout rows")
    if not study["ranking_is_comparable"]:
        out.append(f"  only {study['instruments_graded']} instrument has frozen "
                   "bands in this pool, so there is nothing to rank against it")
        out.append("  collect a second instrument and re-run: the ranking is the "
                   "answer to 'which instrument', and one instrument cannot answer it")
    else:
        out.append(f"  {'rank':>4} {'instrument':10s} {'band':7s} {'out':>5} "
                   f"{'T1 out':>7} {'exp out':>9} {'move':>7} {'days':>6}  verdict")
        for row in study["ranking"]:
            rank = "-" if row["rank"] is None else str(row["rank"])
            out.append(
                f"  {rank:>4} {row['instrument']:10s} {row['band']:7s} "
                f"{row['holdout_rows']:>5} {_pct(row['holdout_t1_pct']):>7} "
                f"{_r(row['holdout_expectancy_r']):>9} "
                f"{_num(row['median_mfe_points']):>7} "
                f"{_pct(row['session_share_pct']):>6}  {row['verdict']}")
        out.append("  unranked rows did not clear the sample bar: a cohort is not "
                   "allowed to win the ranking on a handful of rows")
        out.append("  read the move column beside the R column — cost is fixed in "
                   "rupees, so points, not R, decide whether a small target pays")
    out.append("")

    out.append("§6 what these numbers are not")
    for note in study["notes"]:
        out.append(f"  - {note}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trades", required=True,
                   help="pool saved by phase14_rules.py --save-trades")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--min-fold-trades", type=int, default=30)
    ap.add_argument("--out", default=None, help="write the study as JSON")
    ap.add_argument("--md", default=None, help="write the report as markdown")
    args = ap.parse_args()

    with open(args.trades) as fh:
        saved = json.load(fh)
    dev = saved["in_sample"]
    holdout = saved["holdout"]

    # §20 integrity guard first: one row with risk <= 0 or a non-finite R
    # rewrites every aggregate above it.
    g_dev = attribution.guard(dev)
    g_out = attribution.guard(holdout)
    dev, holdout = g_dev["clean_rows"], g_out["clean_rows"]
    print(f"pool: {g_dev['rows']} development + {g_out['rows']} holdout rows; "
          f"{g_dev['data_errors'] + g_out['data_errors']} failed the integrity "
          "guard and were excluded")

    study = vol.grade(dev, holdout, folds=args.folds,
                      min_fold_trades=args.min_fold_trades)
    lines = _render(study)
    print("\n" + "\n".join(lines))

    if args.out:
        with open(args.out, "w") as fh:
            json.dump(study, fh, indent=1)
        print(f"\nwrote {args.out}")
    if args.md:
        with open(args.md, "w") as fh:
            fh.write("# Instrument x volatility — RESEARCH ONLY\n\n```\n")
            fh.write("\n".join(lines))
            fh.write("\n```\n")
        print(f"wrote {args.md}")


if __name__ == "__main__":
    main()
