"""§12 — the opportunity CLI.

    python -m app.research.opportunity.cli cycle
    python -m app.research.opportunity.cli status

Everything here is read-or-research only. No subcommand places an order, none
touches production signal code, and none can promote anything to live money —
the strongest verdict available is a readiness label for a human review.
"""
from __future__ import annotations

import argparse
import json

from app.research.opportunity import (
    NO_CANDIDATE,
    NO_TRADE_ANYWHERE,
    STANDING_LIMITS,
    VERSION,
)
from app.research.opportunity import (
    cycle,
    generator,
    htf,
    paper,
    promotion,
    ranking,
    registry,
    screen,
    shadow,
    universe,
)


def _hr(title: str) -> None:
    print(f"\n{title}")


def _pct(value: object) -> str:
    if value is None:
        return "UNMEASURED"
    return f"{float(value):+.3f}%"


def cmd_cycle(args: argparse.Namespace) -> int:
    out = cycle.run(stride=args.stride, screen_limit=args.screen_limit,
                    shadow_limit=args.shadow_limit, session=args.session,
                    skip_generate=args.no_generate,
                    remeasure=args.remeasure)
    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0
    _hr(f"OPPORTUNITY CYCLE — {VERSION}   {out['elapsed_sec']:.1f}s")
    s = out["stages"]
    gen = s["1_generate"]
    print(f"  1 generate           : {gen.get('generated', 'skipped')} "
          f"candidate(s) in the bounded set, {gen.get('created', 0)} newly "
          f"registered, {gen.get('existing', 0)} already on the record")
    scr = s["2_historical_screen"]
    print(f"  2 historical screen  : {scr['screened']} screened, "
          f"{scr['historically_rejected']} rejected, "
          f"{scr['not_screenable']} not screenable")
    print(f"                         FDR q={scr['fdr']['q']} over "
          f"m={scr['fdr']['m']} -> {scr['survives_correction']} survive "
          f"correction, {scr['uncorrected_only']} uncorrected only")
    if scr.get("remeasured"):
        print(f"                         {scr['remeasured']} already-rejected "
              f"candidate(s) re-measured for gross and cost — the rejection "
              f"stands, none is admitted, none enters the correction")
    sha = s["3_live_shadow"]
    print(f"  3 live shadow        : {sha['signals']} signal(s), "
          f"{sha['unpriced']} wanted but unpriceable, "
          f"{sha['no_signal']} no-signal")
    pap = s["4_paper_journal"]
    print(f"  4 paper journal      : {pap['written']} new leg(s), "
          f"{pap['resolved']} resolved, {pap['unresolved']} unresolved, "
          f"{pap['refused_by_limit']} refused by a loss limit")
    pro = s["5_promotion"]
    print(f"  5 promotion          : {pro['verdict']}   "
          f"{pro['promotable']} of {pro['evaluated']} meet every gate")
    print(f"                         champion: {pro['champion'].get('state')}")
    rnk = s["6_market_ranking"]
    print(f"  6 market ranking     : {rnk['verdict']}   "
          f"{rnk['opportunities']} opportunity of {rnk['ranked']} ranked, "
          f"{rnk['unranked']} unranked for want of data")
    _limits()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    out = cycle.status()
    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0
    _hr(f"OPPORTUNITY STATUS — {VERSION}")
    print(f"  candidates            : {out['candidates_total']}")
    for status, n in sorted(out["candidates_by_status"].items()):
        print(f"      {status:<32} {n}")
    u = out["universe"]
    print(f"  universe              : {u['requested']} requested, "
          f"{u['screenable']} screenable, {u['no_history']} with no history")
    for row in u["not_screenable"]:
        print(f"      {row['instrument']:<14} {row['absence']}")
    h = out["historical_screen"]
    print(f"  historical screen     : {h['rows']} row(s), "
          f"{h['rejected']} rejected, {h['not_screenable']} not screenable, "
          f"{h['eligible_for_shadow']} eligible for shadow")
    sh = out["live_shadow"]
    print(f"  live shadow           : {sh['rows']} row(s), "
          f"{sh['signals']} signal(s), {sh['unpriced']} unpriceable")
    for state, n in sorted(sh["evidence"].items()):
        print(f"      {state:<32} {n}")
    p = out["paper"]
    print(f"  paper journal         : {p['legs']} leg(s), {p['resolved']} "
          f"resolved over {p['sessions']} session(s)")
    print(f"  promotion             : {out['promotion']['verdict']}")
    print(f"  champion              : {out['promotion']['champion'].get('state')}")
    for f in out["files"]:
        # parsed is printed beside lines, not instead of it: a line that no
        # longer parses is a torn write, and it is the one file fault an
        # append-only store cannot repair by rewriting.
        torn = "" if f["parsed"] == f["lines"] else \
            f"  {f['lines'] - f['parsed']} UNPARSEABLE"
        print(f"      {f['file']:<26} {f['lines']} line(s)  "
              f"{f['parsed']} parsed  {f['bytes']:,} bytes{torn}")
    _limits()
    return 0


def cmd_rank(args: argparse.Namespace) -> int:
    out = ranking.rank()
    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0
    _hr("MARKET-WIDE OPPORTUNITY RANKING")
    print(f"  verdict               : {out['verdict']}")
    if out["verdict"] == NO_TRADE_ANYWHERE:
        print("      nothing currently clears the cost bar with historical "
              "support and a priceable book. This is a real answer.")
    for row in out["ranked"]:
        print(f"      {row['instrument']:<14} {row['label']:<12} "
              f"move {row['expected_move_pct']:.3f}% vs cost "
              f"{row['round_trip_cost_pct']}% "
              f"({row['movement_cost_multiple']:.2f}x)")
        print(f"          {row['why']}")
    for row in out["unranked"]:
        print(f"      {row['instrument']:<14} UNRANKED     {row['absence']}")
    print(f"\n  {out['not_a_prediction']}")
    _limits()
    return 0


def cmd_promotion(args: argparse.Namespace) -> int:
    out = promotion.report()
    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0
    _hr("PROMOTION READINESS")
    print(f"  verdict               : {out['verdict']}")
    if out["verdict"] == NO_CANDIDATE:
        print("      no candidate meets every production-paper gate. With a "
              "sample this size that is the expected answer.")
    print(f"  evaluated             : {out['evaluated']}")
    print(f"  champion              : {out['champion'].get('state')}")
    for row in out["rows"][:args.top]:
        print(f"\n      {row['candidate_name']}  [{row['status']}]")
        print(f"          {row['resolved_trades']} trade(s) over "
              f"{row['sessions']} session(s), net "
              f"{_pct(row['net_total_pct'])}, readiness {row['readiness']}")
        print(f"          milestone: {row['milestone']['milestone']}")
        for name, gate in row["gates"].items():
            mark = "pass" if gate["pass"] else "FAIL"
            print(f"          {mark:<5} {name:<22} {gate['detail']}")
    print("\n  every gate must pass; there is no composite score and nothing "
          "here authorises live money")
    _limits()
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    out = generator.generate()
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_screen(args: argparse.Namespace) -> int:
    out = screen.run(stride=args.stride, limit=args.limit,
                     remeasure=args.remeasure)
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_shadow(args: argparse.Namespace) -> int:
    out = shadow.run(session=args.session, limit=args.limit,
                     instrument=args.instrument)
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_paper(args: argparse.Namespace) -> int:
    out = paper.run(session=args.session)
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_universe(args: argparse.Namespace) -> int:
    out = universe.funnel()
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_candidates(args: argparse.Namespace) -> int:
    rows = registry.candidates()
    if args.status:
        rows = [r for r in rows if str(r.get("status")) == args.status]
    print(json.dumps({"count": len(rows), "candidates": rows}, indent=2,
                     default=str))
    return 0


def _limits() -> None:
    print("\n  STANDING LIMITS")
    for line in STANDING_LIMITS:
        print(f"    - {line}")


def cmd_timeframes(args: argparse.Namespace) -> int:
    out = htf.report()
    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0
    _hr(f"TIMEFRAME COMPARISON — {VERSION}")
    if out["status"] != "MEASURED":
        print(f"  {out['status']}")
        print(f"  {out['reason']}")
        return 0
    period = out[args.period]
    print(f"  period {args.period}, round trip {period['round_trip_pct']:.4f}%, "
          f"a per-trade mean is read only at "
          f"{period['usable_trade_floor']}+ trades")
    print(f"\n  {'bar':<6} {'cands':>6} {'usable':>7} {'no gross':>9} "
          f"{'median gross':>13} {'best gross':>11} {'cost x':>7} "
          f"{'hold min':>9} {'o/night':>8}  best candidate")
    for row in period["timeframes"]:
        mult = row["best_cost_multiple"]
        hold = row["median_hold_min"]
        night = row["best_overnight_share"]
        print(f"  {row['timeframe']:<6} {row['candidates']:>6} "
              f"{row['usable_candidates']:>7} "
              f"{row['gross_unrecorded']:>9} "
              f"{_pct(row['median_gross_pct']):>13} "
              f"{_pct(row['best_gross_pct']):>11} "
              f"{(f'{mult:.2f}' if mult else 'n/a'):>7} "
              f"{(f'{hold:.0f}' if hold is not None else '—'):>9} "
              f"{(f'{night * 100:.0f}%' if night is not None else '—'):>8}  "
              f"{row['best_candidate'] or 'NONE_WITH_A_USABLE_SAMPLE'} "
              f"({row['best_trades'] or 0} trades)")
    if out["gross_unrecorded_rows"]:
        # Not a zero edge. These rows were screened before gross and cost were
        # recorded apart, so their gross was never written down; re-screening
        # is what produces the figure, not this report.
        print(f"\n  {out['gross_unrecorded_rows']} row(s) carry no gross "
              f"figure and are excluded from every mean above — they were "
              f"screened before gross and cost were recorded separately, and "
              f"a missing gross is not a zero gross")
    print(f"\n  {out['verdict']}")
    print("\n  HOW TO READ THIS")
    print("    cost x below 1.00 is the only reading in which a candidate pays "
          "for its own execution")
    print("    o/night is the share of the best row's trades that were still "
          "open at a close — an overnight future is not a longer intraday "
          "trade, and the modelled round trip does not price its gap")
    for line in out["limits"]:
        print(f"    - {line}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.research.opportunity.cli",
        description="Market-wide opportunity discovery. Research and paper only.")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("cycle", help="one full pass through the funnel")
    c.add_argument("--stride", type=int, default=screen.DEFAULT_STRIDE)
    c.add_argument("--screen-limit", type=int, default=None)
    c.add_argument("--shadow-limit", type=int, default=None)
    c.add_argument("--session", default=None)
    c.add_argument("--no-generate", action="store_true",
                   help="screen and evaluate the existing candidates only")
    c.add_argument("--remeasure", action="store_true",
                   help="also re-measure already-rejected candidates for the "
                        "gross and cost columns; the rejection stands and "
                        "nothing can be admitted from it")
    c.set_defaults(func=cmd_cycle)

    s = sub.add_parser("status", help="where the funnel stands, running nothing")
    s.set_defaults(func=cmd_status)

    r = sub.add_parser("rank", help="market-wide ranking, may be NO_TRADE_ANYWHERE")
    r.set_defaults(func=cmd_rank)

    pr = sub.add_parser("promotion", help="promotion readiness, gate by gate")
    pr.add_argument("--top", type=int, default=10)
    pr.set_defaults(func=cmd_promotion)

    g = sub.add_parser("generate", help="register the bounded candidate set")
    g.set_defaults(func=cmd_generate)

    sc = sub.add_parser("screen", help="historical fast filter")
    sc.add_argument("--stride", type=int, default=screen.DEFAULT_STRIDE)
    sc.add_argument("--limit", type=int, default=None)
    sc.add_argument("--remeasure", action="store_true",
                   help="also re-measure already-rejected candidates for the "
                        "gross and cost columns; the rejection stands and "
                        "nothing can be admitted from it")
    sc.set_defaults(func=cmd_screen)

    sd = sub.add_parser("shadow", help="live shadow evaluation over raw observations")
    sd.add_argument("--session", default=None)
    sd.add_argument("--limit", type=int, default=None)
    sd.add_argument("--instrument", default=None)
    sd.set_defaults(func=cmd_shadow)

    pa = sub.add_parser("paper", help="resolve shadow signals into paper legs")
    pa.add_argument("--session", default=None)
    pa.set_defaults(func=cmd_paper)

    un = sub.add_parser("universe", help="the funnel's coverage map")
    un.set_defaults(func=cmd_universe)

    tf = sub.add_parser(
        "timeframes",
        help="gross edge against cost per bar length, running nothing")
    tf.add_argument("--period", default="train",
                    choices=("train", "validation", "holdout"))
    tf.set_defaults(func=cmd_timeframes)

    ca = sub.add_parser("candidates", help="registered candidates")
    ca.add_argument("--status", default=None)
    ca.set_defaults(func=cmd_candidates)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
