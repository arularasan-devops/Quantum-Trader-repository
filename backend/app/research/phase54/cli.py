"""Phase 54 CLI — read historical files, print, write artefacts. Nothing else.

Five read-only commands. There is no command that places an order, enables a
signal, writes to a production journal or touches a Phase 41-53 artefact,
because there is no code in this package that could.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase54 import fingerprint, preregistration, variants
from app.research.phase54 import report as p54report
from app.research.phase54 import stats as p54stats
from app.research.phase54 import study as p54study

DEFAULT_INSTRUMENTS = ("CRUDEOIL", "NIFTY")


def _cmd_prereg() -> str:
    pre = preregistration()
    lines = [
        f"PHASE 54 PRE-REGISTRATION  fingerprint {fingerprint()}",
        f"  mechanism      {pre['mechanism']}",
        f"  source         {pre['source']}",
        f"  vehicle        {pre['vehicle']}",
        f"  decisions on   {pre['decision_timeframe']}",
        f"  paths walked   {pre['resolution_timeframe_minutes']}-minute bars",
        f"  lookback       {pre['lookbacks']} completed sessions, current "
        f"session excluded",
        f"  pullback       within {pre['pullback_windows']} completed sessions",
        f"  stop           pullback extreme +/- {pre['stop_buffer_atr']} ATR, "
        f"refused above {pre['max_risk_atr']} ATR20",
        f"  target         {pre['target_r_multiples']} R",
        f"  hold           {pre['max_hold_sessions']} completed sessions",
        f"  cost gate      {pre['cost_gate_multiples']}x round trip",
        f"  positions      {pre['max_open_positions']} at a time",
        f"  atr            {pre['atr_basis']}",
        f"  partitions     {pre['partitions']} {pre['partition_shares']}",
        f"  trade floors   {pre['min_trades']}, discovery sessions >= "
        f"{pre['min_sessions_discovery']}, promotion floor "
        f"{pre['min_trades_for_promotion']} trades",
        f"  promotion      PF >= {pre['min_profit_factor']}, drawdown <= "
        f"{pre['max_drawdown_r']}R, top trade <= "
        f"{pre['max_top_trade_share']}, >= {pre['min_years_positive']} "
        f"positive years, year share <= {pre['max_year_share']}, quarter share "
        f"<= {pre['max_quarter_share']}, positive at "
        f"{pre['cost_stress_multiples']}x cost",
        f"  parameterizations {len(variants())} per instrument",
    ]
    for key in ("pullback_basis", "reclaim_basis", "strict_reclaim_is_a_diagnostic",
                "pullback_extreme_basis", "hold_basis", "reentry_basis",
                "partition_basis"):
        lines.append(f"  {pre[key]}")
    lines.extend(f"  {v}" for v in pre["honesty"].values())
    return "\n".join(lines)


def _cmd_grid() -> str:
    lines = [f"PHASE 54 GRID — {len(variants())} parameterizations per instrument"]
    for v in variants():
        lines.append(
            f"  {v['variant_id']:<52s} lookback {v['lookback']:>3}  "
            f"pullback {v['pullback_window']}  buf "
            f"{v['stop_buffer_atr']:.2f}  risk <= {v['max_risk_atr']:.1f} ATR  "
            f"target {v['target_r']:.1f}R  hold {v['max_hold_sessions']:>2}  "
            f"gate {v['cost_gate']:.0f}x"
        )
    lines.append(
        "  only the lookback and the pullback window change which instants are "
        "entered — four event tables; the other five dimensions change what "
        "happens to those instants and, through the one-position rule, which "
        "of them are reachable at all"
    )
    return "\n".join(lines)


def _cmd_universe(instruments: tuple[str, ...]) -> str:
    lines = ["PHASE 54 UNIVERSE"]
    for name in instruments:
        row = p54stats.eligibility(name)
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
    lines = ["PHASE 54 FUNNEL"]
    for name in instruments:
        tables = p54study.event_tables(name)
        if not tables:
            lines.append(f"  {name}  no series")
            continue
        for key, table in sorted(tables.items()):
            lines.append(
                f"  {name}  lookback {key[0]} | pullback window {key[1]}"
            )
            for field, value in table["funnel"].items():
                lines.append(f"    {field:<66s} {value:>6,}")
    return "\n".join(lines)


def _cmd_study(instruments: tuple[str, ...], *, top: int, write: bool) -> str:
    result = p54study.run(instruments)
    text = p54report.render(result, top=top)
    if write:
        paths = p54report.write_artefacts(result, text=text)
        text += "\n\nARTEFACTS\n" + "\n".join(f"  {p}" for p in paths)
    return text


def main_argv(argv: list[str] | None = None) -> str:
    parser = argparse.ArgumentParser(prog="phase54", description=__doc__)
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
        return json.dumps(p54study.run(instruments)["totals"], indent=2)
    return _cmd_study(instruments, top=args.top, write=args.write)


def main() -> None:
    print(main_argv())


if __name__ == "__main__":
    main()
