"""Phase 53 CLI — read historical files, print, write artefacts. Nothing else.

Five read-only commands. There is no command that places an order, enables a
signal, writes to a production journal or touches a Phase 41-52 artefact,
because there is no code in this package that could.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase53 import fingerprint, preregistration, variants
from app.research.phase53 import report as p53report
from app.research.phase53 import stats as p53stats
from app.research.phase53 import study as p53study

DEFAULT_INSTRUMENTS = ("CRUDEOIL", "NIFTY")


def _cmd_prereg() -> str:
    pre = preregistration()
    lines = [
        f"PHASE 53 PRE-REGISTRATION  fingerprint {fingerprint()}",
        f"  mechanism      {pre['mechanism']}",
        f"  source         {pre['source']}",
        f"  vehicle        {pre['vehicle']}",
        f"  decision bars  {pre['decision_timeframe_minutes']}m, opening range "
        f"{pre['opening_range_minutes']}m",
        f"  breakout       {pre['breakout_confirmations']}",
        f"  retest         within {pre['retest_window_minutes']}m, tolerance "
        f"{pre['retest_tolerances_atr']} ATR",
        f"  confirmation   {pre['retest_confirmations']} within "
        f"{pre['confirmation_window_minutes']}m of the retest",
        f"  stop           retest extreme +/- {pre['stop_buffer_atr']} ATR, "
        f"refused above {pre['max_stop_atr']} ATR",
        f"  target         {pre['target_r_multiples']} R",
        f"  cost gate      {pre['cost_gate_multiples']}x round trip",
        f"  hold           {pre['max_hold_minutes']} minutes max, "
        f"{pre['max_long_per_session']} long and "
        f"{pre['max_short_per_session']} short per session",
        f"  atr            {pre['atr_basis']}",
        f"  partitions     {pre['partitions']} {pre['partition_shares']}",
        f"  trade floors   {pre['min_trades']}, discovery sessions >= "
        f"{pre['min_sessions_discovery']}",
        f"  promotion      PF >= {pre['min_profit_factor']}, drawdown <= "
        f"{pre['max_drawdown_r']}R, top trade <= "
        f"{pre['max_top_trade_share']}, >= {pre['min_years_positive']} "
        f"positive years, year share <= {pre['max_year_share']}, quarter share "
        f"<= {pre['max_quarter_share']}, positive at "
        f"{pre['cost_stress_multiples']}x cost",
        f"  parameterizations {len(variants())}",
    ]
    lines.extend(f"  {v}" for v in pre["honesty"].values())
    return "\n".join(lines)


def _cmd_grid() -> str:
    lines = [f"PHASE 53 GRID — {len(variants())} parameterizations"]
    for v in variants():
        lines.append(
            f"  {v['variant_id']:<52s} {v['breakout_confirmation']:<20s} "
            f"{v['retest_confirmation']:<18s} tol "
            f"{v['retest_tolerance_atr']:.2f}  buf "
            f"{v['stop_buffer_atr']:.2f}  target {v['target_r']:.1f}R  "
            f"gate {v['cost_gate']:.0f}x"
        )
    lines.append(
        "  only breakout, retest confirmation and tolerance change which "
        "instants are entered — eight event tables, thirty-two resolved "
        "geometries, two cost gates over them"
    )
    return "\n".join(lines)


def _cmd_universe(instruments: tuple[str, ...]) -> str:
    lines = ["PHASE 53 UNIVERSE"]
    for name in instruments:
        row = p53stats.eligibility(name)
        lines.append(
            f"  {'ELIGIBLE' if row['eligible'] else 'REFUSED':<9s} "
            f"{row['instrument']:<10s} {row['bars']:>9,} bars  "
            f"{row['sessions']:>6,} sessions  {row['source']}"
        )
        if row.get("reason"):
            lines.append(f"    {row['reason']}")
        lines.append(f"    {row['execution_note']}")
    return "\n".join(lines)


def _cmd_funnel(instruments: tuple[str, ...]) -> str:
    lines = ["PHASE 53 FUNNEL"]
    for name in instruments:
        tables = p53study.event_tables(name)
        if not tables:
            lines.append(f"  {name}  no series")
            continue
        for key, table in sorted(tables.items()):
            lines.append(f"  {name}  {key[0]} | {key[1]} | tol {key[2]:.2f}")
            for field, value in table["funnel"].items():
                lines.append(f"    {field:<64s} {value:>6,}")
    return "\n".join(lines)


def _cmd_study(instruments: tuple[str, ...], *, top: int, write: bool) -> str:
    result = p53study.run(instruments)
    text = p53report.render(result, top=top)
    if write:
        paths = p53report.write_artefacts(result, text=text)
        text += "\n\nARTEFACTS\n" + "\n".join(f"  {p}" for p in paths)
    return text


def main_argv(argv: list[str] | None = None) -> str:
    parser = argparse.ArgumentParser(prog="phase53", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prereg", help="print the frozen definition and fingerprint")
    sub.add_parser("grid", help="print the registered parameterization grid")
    for name in ("universe", "funnel", "study"):
        p = sub.add_parser(name)
        p.add_argument("--instrument", action="append", default=None)
        if name == "study":
            p.add_argument("--top", type=int, default=16)
            p.add_argument("--write", action="store_true")
            p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    instruments = tuple(args.instrument) if getattr(args, "instrument", None) \
        else DEFAULT_INSTRUMENTS
    if args.command == "prereg":
        return _cmd_prereg()
    if args.command == "grid":
        return _cmd_grid()
    if args.command == "universe":
        return _cmd_universe(instruments)
    if args.command == "funnel":
        return _cmd_funnel(instruments)
    if getattr(args, "json", False):
        return json.dumps(p53study.run(instruments)["totals"], indent=2)
    return _cmd_study(instruments, top=args.top, write=args.write)


def main() -> None:
    print(main_argv())


if __name__ == "__main__":
    main()
