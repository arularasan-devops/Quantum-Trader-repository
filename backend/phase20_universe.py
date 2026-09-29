"""Phase 20 research universe — what is studied, what may be claimed, what paid.

    .venv/bin/python phase20_universe.py
    .venv/bin/python phase20_universe.py --trades ~/market_scan_pool.json
    .venv/bin/python phase20_universe.py --json

RESEARCH ONLY. Reads rows already on disk, writes nothing, enters no paper leg,
changes no threshold, and cannot promote anything. Production instrument selection
does not read this file.

Three sections, deliberately kept apart:

* **the universe** — indexes, MCX commodities and optionable equities, with the
  evidence each one can actually produce. Historical option profitability is
  unclaimable for every name in it, because expired option chains are not
  fetchable; a five-year direction study is available only where a cash series
  exists, which excludes MCX.
* **direction, per instrument** — with ``--trades``, each instrument's own
  five-year outcomes, chronological holdout and folds, ranked separately. Plus
  coverage: how much of the universe the pool actually measured, and which names
  cannot be measured on these terms at all.
* **vehicle, per instrument** — Futures vs CE vs PE at the SAME signal, from the
  resolved paper books. A preference is withheld until there are enough
  same-signal comparisons, and every gap names itself.
"""
from __future__ import annotations

import argparse
import json

from app.analysis import instrument_family as fam
from app.research.phase17 import store as p17store
from app.research.phase19 import futbook
from app.research.phase20 import direction, ranking, universe, vehicle

BAR = "=" * 78


def _load_pool(path: str | None) -> list[dict]:
    """Rows from a saved candidate pool, in the format Phase 14/15 write."""
    if not path:
        return []
    with open(path, encoding="utf-8") as fh:
        blob = json.load(fh)
    if isinstance(blob, list):
        return [r for r in blob if isinstance(r, dict)]
    rows: list[dict] = []
    for part in ("in_sample", "holdout", "trades", "rows"):
        got = blob.get(part)
        if isinstance(got, list):
            rows.extend(r for r in got if isinstance(r, dict))
    return rows


def _print_universe() -> None:
    print(BAR)
    print("RESEARCH UNIVERSE — what each instrument can be asked")
    print(BAR)
    groups = universe.study_groups()
    for family in (fam.INDEX, fam.MCX, fam.STOCK):
        members = groups[family]
        print(f"\n{family}  ({len(members)} instruments)")
        print(f"  {'instrument':<14}{'history':<24}{'5y direction':<14}"
              f"{'prior':<26}admitted")
        for name in members:
            plan = universe.plan(name)
            print(f"  {name:<14}{plan['history_basis']:<24}"
                  f"{'yes' if plan['five_year_direction_study'] else 'no':<14}"
                  f"{plan['prior_label']:<26}"
                  f"{'yes' if plan['research_admitted'] else 'REFUSED'}")
    print("\n  historical option profitability: UNCLAIMABLE for every name above")
    print(f"  option economics come only from {universe.OPTION_EVIDENCE_SOURCE}")


def _print_direction(pool: list[dict]) -> None:
    print()
    print(BAR)
    print("DIRECTION, PER INSTRUMENT — underlying only, never pooled")
    print(BAR)
    if not pool:
        print("  no pool given (--trades); direction is unmeasured, not flat")
        return
    rep = direction.report(pool)
    print(f"  {'instrument':<14}{'rows':>7}{'mean R':>10}{'sigma':>8}"
          f"{'PF':>7}{'hold R':>9}  label")
    for row in rep["per_instrument"]:
        hold = (row["holdout"] or {}).get("mean_r")
        print(f"  {row['instrument']:<14}{row['rows']:>7}"
              f"{_num(row['mean_r']):>10}{_num(row['sigma']):>8}"
              f"{_num(row['profit_factor']):>7}{_num(hold):>9}  {row['label']}")
    cov = rep["coverage"]
    print(f"\n  measured {cov['measured_count']} of {cov['universe']} in the universe")
    print(f"  not collected yet:                    {len(cov['not_collected_yet'])}")
    print("  cannot be measured on these terms:    "
          f"{len(cov['cannot_be_measured_on_these_terms'])} (no cash series)")
    if rep["validated_positive"]:
        print(f"  validated positive: {', '.join(rep['validated_positive'])}")
    else:
        print("  validated positive: none — no instrument has a directional edge "
              "that survived out of sample")


def _print_vehicle(pool_names: set[str]) -> None:
    del pool_names
    print()
    print(BAR)
    print("VEHICLE, PER INSTRUMENT — Futures vs CE vs PE at the same signal")
    print(BAR)
    option_rows = [r for r in p17store.paper(tail_bytes=None) if isinstance(r, dict)]
    futures_rows = [r for r in futbook.resolved() if isinstance(r, dict)]
    pairs = vehicle.pair(option_rows, futures_rows)
    if not pairs:
        print("  no resolved legs on disk; vehicle choice is unmeasured")
        return
    for family, records in vehicle.by_family(pairs).items():
        if not records:
            continue
        print(f"\n{family}")
        for rec in records:
            print(f"  {rec['instrument']:<14}signals {rec['signals']:>4}"
                  f"   comparable {rec['comparable_signals']:>4}"
                  f"   preferred: {rec['preferred_vehicle'] or 'WITHHELD'}")
            for name in vehicle.VEHICLES:
                cell = rec["by_vehicle"][name]
                print(f"      {name:<9}resolved {cell['resolved']:>4}"
                      f"   mean R {_num(cell['mean_net_r']):>9}"
                      f"   PF {_num(cell['profit_factor']):>7}"
                      f"   best on {cell['best_on_signals']:>4} signals")
            for why, n in rec["missing_because"].items():
                print(f"      missing: {why:<34}{n:>5}")
    print(f"\n  a preference needs {vehicle.MIN_COMPARISONS} same-signal "
          "comparisons; below that it is withheld, not guessed")


def _print_ranking() -> None:
    rows = [r for r in p17store.paper(tail_bytes=None) if isinstance(r, dict)]
    legs = [dict(r, r=r.get("net_r")) for r in rows
            if isinstance(r.get("net_r"), (int, float))]
    print()
    print(BAR)
    print("COSTED PAPER RANKING — per instrument, no pooled statistic")
    print(BAR)
    if not legs:
        print("  no costed resolved legs; nothing may be called profitable")
        return
    for rec in ranking.rank(legs):
        print(f"  #{rec['rank']:<3}{rec['instrument']:<14}{rec['family']:<8}"
              f"legs {rec['costed_rows']:>5}   mean R {_num(rec['mean_net_r']):>9}"
              f"   PF {_num(rec['profit_factor']):>7}   {rec['verdict']}"
              f"{'   PROFITABLE' if rec['may_be_called_profitable'] else ''}")
    print(f"\n  {ranking.pooled_statistic_refused(legs)['reason']}")


def _num(value: object) -> str:
    return "-" if not isinstance(value, (int, float)) else f"{float(value):.4f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trades", help="saved candidate pool for the direction study")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()
    pool = _load_pool(args.trades)
    if args.json:
        option_rows = [r for r in p17store.paper(tail_bytes=None) if isinstance(r, dict)]
        futures_rows = [r for r in futbook.resolved() if isinstance(r, dict)]
        print(json.dumps({
            "universe": universe.universe(),
            "study_groups": universe.study_groups(),
            "direction": direction.report(pool) if pool else None,
            "vehicle": vehicle.by_instrument(
                vehicle.pair(option_rows, futures_rows)),
            "research_only": True,
            "production_selection_unchanged": True,
        }, indent=2, default=str))
        return
    _print_universe()
    _print_direction(pool)
    _print_ranking()
    _print_vehicle({str(r.get("instrument") or "").upper() for r in pool})
    print("\nresearch only — production instrument selection unchanged, "
          "real orders disabled")


if __name__ == "__main__":
    main()
