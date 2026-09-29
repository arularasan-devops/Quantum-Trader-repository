"""Phase 52 CLI — read historical files, print, write artefacts. Nothing else.

Four read-only commands. There is no command that places an order, enables a
signal, writes to a production journal or touches a Phase 41-51 artefact,
because there is no code in this package that could.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase52 import fingerprint, preregistration, variants
from app.research.phase52 import report as p52report
from app.research.phase52 import study as p52study

DEFAULT_INSTRUMENTS = ("CRUDEOIL", "NIFTY")


def _cmd_prereg() -> str:
    pre = preregistration()
    lines = [
        f"PHASE 52 PRE-REGISTRATION  fingerprint {fingerprint()}",
        f"  mechanism      {pre['mechanism']}",
        f"  source         {pre['source']}",
        f"  vehicle        {pre['vehicle']}",
        f"  decision bars  {pre['decision_timeframe_minutes']}m, "
        f"wait {pre['wait_minutes']}m",
        f"  gap thresholds {pre['gap_atr_thresholds']} ATR",
        f"  confirmations  {pre['confirmations']}",
        f"  stop           failure extreme +/- {pre['stop_buffer_atr']} ATR, "
        f"capped at {pre['max_stop_atr']} ATR",
        f"  target         {pre['target']}",
        f"  cost gate      {pre['cost_gate_multiples']}x round trip",
        f"  hold           {pre['max_hold_minutes']} minutes max, "
        f"{pre['max_trades_per_session']} trade per session",
        f"  atr            {pre['atr_basis']}",
        f"  partitions     {pre['partitions']} {pre['partition_shares']}",
        f"  trade floors   {pre['min_trades']}",
        f"  promotion      PF >= {pre['min_profit_factor']}, drawdown <= "
        f"{pre['max_drawdown_r']}R, top trade <= "
        f"{pre['max_top_trade_share']}, >= {pre['min_years_positive']} "
        f"positive years, positive at {pre['cost_stress_multiples']}x cost",
        f"  hypotheses     {len(variants())}",
    ]
    lines.extend(f"  {v}" for v in pre["honesty"].values())
    return "\n".join(lines)


def _cmd_grid() -> str:
    lines = [f"PHASE 52 GRID — {len(variants())} parameterizations"]
    for v in variants():
        lines.append(
            f"  {v['variant_id']:<46s} gap {v['gap_atr']:.2f}  "
            f"{v['confirmation']:<24s} stop {v['max_stop_atr']:.2f}  "
            f"gate {v['cost_gate']:.0f}x"
        )
    lines.append(
        "  these are filters over one event table per confirmation timeframe, "
        "not thirty-six independent searches"
    )
    return "\n".join(lines)


def _cmd_funnel(instruments: tuple[str, ...]) -> str:
    lines = ["PHASE 52 FUNNEL"]
    for name in instruments:
        for conf in ("CLOSE_5M_INSIDE_RANGE", "CLOSE_15M_INSIDE_RANGE"):
            table = p52study.resolved_table(name, conf)
            if not table["eligible"]:
                lines.append(f"  {name}  no series")
                break
            lines.append(f"  {name}  {conf}")
            for key, value in table["funnel"].items():
                lines.append(f"    {key:<46s} {value:>6,}")
    return "\n".join(lines)


def _cmd_study(instruments: tuple[str, ...], *, top: int, write: bool) -> str:
    result = p52study.run(instruments)
    text = p52report.render(result, top=top)
    if write:
        paths = p52report.write_artefacts(result, text=text)
        text += "\n\nARTEFACTS\n" + "\n".join(f"  {p}" for p in paths)
    return text


def main_argv(argv: list[str] | None = None) -> str:
    parser = argparse.ArgumentParser(prog="phase52", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prereg", help="print the frozen definition and fingerprint")
    sub.add_parser("grid", help="print the bounded parameterization grid")
    for name in ("funnel", "study"):
        p = sub.add_parser(name)
        p.add_argument("--instrument", action="append", default=None)
        if name == "study":
            p.add_argument("--top", type=int, default=20)
            p.add_argument("--write", action="store_true")
            p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    instruments = tuple(args.instrument) if getattr(args, "instrument", None) \
        else DEFAULT_INSTRUMENTS
    if args.command == "prereg":
        return _cmd_prereg()
    if args.command == "grid":
        return _cmd_grid()
    if args.command == "funnel":
        return _cmd_funnel(instruments)
    if getattr(args, "json", False):
        return json.dumps(p52study.run(instruments)["totals"], indent=2)
    return _cmd_study(instruments, top=args.top, write=args.write)


def main() -> None:
    print(main_argv())


if __name__ == "__main__":
    main()
