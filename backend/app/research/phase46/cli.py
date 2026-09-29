"""Phase 46 CLI — read the overlay, write the artefact, print the fingerprint.

    .venv/bin/python -m app.research.phase46.cli board --instrument CRUDEOIL
    .venv/bin/python -m app.research.phase46.cli journal --limit 40
    .venv/bin/python -m app.research.phase46.cli outcomes
    .venv/bin/python -m app.research.phase46.cli frozen

No subcommand here evaluates an instant, places anything, or promotes
anything. ``outcomes`` reads the Phase 45 paper outcomes and compares a set
with its own subset, which is the only comparison this phase makes and is
labelled as such in the output.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from app.research.phase46 import (
    ARM_SIGNAL_ONLY,
    ARM_SIGNAL_PLUS_OVERLAY,
    ARTEFACT_DIR,
    MD_NAME,
    NOT_A_PROMOTION,
)
from app.research.phase46 import freeze, service
from app.research.phase46 import store as store_mod


def _artefact_dir() -> str:
    base = os.path.dirname(store_mod.db_path())
    os.makedirs(base, exist_ok=True)
    return base


def _fmt(value: object, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _board(args: argparse.Namespace) -> int:
    payload = service.board(instrument=args.instrument, limit=args.limit)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print("RESEARCH OVERLAY — SHADOW / PAPER ONLY")
    print(f"definition {payload['definition']}  rows {payload['totals']['rows']}"
          f"  instants {payload['totals']['instants']}")
    for instant in payload["instants"]:
        prod = instant["production"]
        print()
        print(f"{prod['instrument']}  {prod['session']}  "
              f"CURRENT SIGNAL {prod['signal']}  "
              f"engine vehicle {prod['selected_vehicle'] or '—'}")
        print("  vehicle   state          contract              "
              "spread%   move/cost  data")
        for row in instant["vehicles"]:
            print("  {:8}  {:13}  {:20}  {:8}  {:9}  {}".format(
                str(row.get("vehicle")), str(row.get("overlay_state")),
                str(row.get("contract") or "—")[:20],
                _fmt(row.get("spread_pct"), 3),
                _fmt(row.get("expected_move_over_modelled_cost"), 3),
                str(row.get("data_quality") or "—"),
            ))
    print()
    print(NOT_A_PROMOTION)
    return 0


def _journal(args: argparse.Namespace) -> int:
    filters = {"instrument": args.instrument, "state": args.state}
    payload = service.journal(filters=filters, limit=args.limit)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print(f"OVERLAY JOURNAL — {payload['count']} row(s) of "
          f"{payload['totals']['rows']}")
    for state, n in payload["state_counts"].items():
        print(f"  {state:22} {n}")
    print(NOT_A_PROMOTION)
    return 0


def _outcomes(args: argparse.Namespace) -> int:
    payload = service.outcomes()
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    arms = payload["arms"]
    print("OUTCOME COMPARISON — PAPER ONLY")
    print(f"resolved legs available {payload['resolved_legs_available']}  "
          f"verdict {payload['verdict']}")
    for key in (ARM_SIGNAL_ONLY, ARM_SIGNAL_PLUS_OVERLAY):
        arm = arms[key]
        print()
        print(key)
        for field in ("resolved_trades", "net_pct_total", "expectancy_net_pct",
                      "profit_factor", "win_rate_pct", "max_drawdown_net_pct",
                      "avg_cost_points", "avg_mfe_pct", "avg_mae_pct",
                      "avg_giveback_pct", "sessions", "sample_label"):
            print(f"  {field:24} {_fmt(arm.get(field))}")
    print()
    print(arms["not_independent"])
    print(NOT_A_PROMOTION)
    _write_markdown(payload)
    return 0


def _write_markdown(payload: dict) -> str:
    """The artefact, so a result read once is still readable later."""
    arms = payload["arms"]
    lines = [
        "# RESEARCH OVERLAY — OUTCOME COMPARISON",
        "",
        "SHADOW / PAPER ONLY. No order path, no broker, no real money.",
        "",
        f"- definition: `{payload.get('definition', freeze.definition())}`",
        f"- resolved legs available: {payload['resolved_legs_available']}",
        f"- verdict: **{payload['verdict']}**",
        "",
        "| Metric | A. CURRENT SIGNAL ALONE | B. CURRENT SIGNAL + OVERLAY |",
        "| --- | --- | --- |",
    ]
    fields = (
        ("resolved_trades", "Resolved trades"),
        ("net_pct_total", "Net (sum of net %)"),
        ("expectancy_net_pct", "Expectancy (mean net %)"),
        ("profit_factor", "Profit factor"),
        ("win_rate_pct", "Win rate %"),
        ("max_drawdown_net_pct", "Max drawdown (net %)"),
        ("avg_cost_points", "Average cost (points)"),
        ("avg_mfe_pct", "Average MFE %"),
        ("avg_mae_pct", "Average MAE %"),
        ("avg_giveback_pct", "Average giveback %"),
        ("sessions", "Sessions"),
        ("sample_label", "Sample"),
    )
    for key, label in fields:
        lines.append(
            f"| {label} | {_fmt(arms[ARM_SIGNAL_ONLY].get(key))} "
            f"| {_fmt(arms[ARM_SIGNAL_PLUS_OVERLAY].get(key))} |"
        )
    lines += [
        "",
        arms["not_independent"],
        "",
        NOT_A_PROMOTION,
        "",
    ]
    path = os.path.join(_artefact_dir(), MD_NAME)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"  wrote {os.path.join(ARTEFACT_DIR, MD_NAME)}")
    return path


def _frozen(args: argparse.Namespace) -> int:
    print(json.dumps(freeze.fingerprint(), indent=2, default=str))
    return 0


def _status(args: argparse.Namespace) -> int:
    print(json.dumps(service.stats(), indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser("phase46")
    sub = ap.add_subparsers(dest="cmd", required=True)

    board = sub.add_parser("board", help="the overlay beside the current signal")
    board.add_argument("--instrument")
    board.add_argument("--limit", type=int, default=10)
    board.add_argument("--json", action="store_true")
    board.set_defaults(fn=_board)

    jr = sub.add_parser("journal", help="the overlay journal, newest first")
    jr.add_argument("--instrument")
    jr.add_argument("--state")
    jr.add_argument("--limit", type=int, default=50)
    jr.add_argument("--json", action="store_true")
    jr.set_defaults(fn=_journal)

    oc = sub.add_parser("outcomes", help="arm A against arm B on resolved legs")
    oc.add_argument("--json", action="store_true")
    oc.set_defaults(fn=_outcomes)

    fz = sub.add_parser("frozen", help="this phase's fingerprint")
    fz.set_defaults(fn=_frozen)

    st = sub.add_parser("status", help="writer counters for this process")
    st.set_defaults(fn=_status)

    args = ap.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
