"""Separated capture / vehicle report — Phase 21 §78. RESEARCH ONLY.

Prints the two things the 1 Sep report could not say:

* the FUTURES capture rate, separately from the option book quality that was
  hiding it (100% EXACT option books, zero futures legs, one headline number);
* per family — INDEX, MCX, STOCK — with nothing pooled across them.

Reads the whole stored series by default, including files parked by hand, so the
rates describe the day rather than the tail. Places no orders and writes nothing.

    .venv/bin/python phase21_vehicle_report.py
    .venv/bin/python phase21_vehicle_report.py --tail 20000   # last N rows only
    .venv/bin/python phase21_vehicle_report.py --json
"""
from __future__ import annotations

import argparse
import json

from app.research.phase17 import futures as fut_mod, store
from app.research.phase21 import report as rep


def _line(label: str, value: object) -> str:
    return f"  {label:<34} {value}"


def _capture_block(title: str, section: dict) -> list[str]:
    out = [f"{title}"]
    opt = section[rep.OPTION]
    fut = section[rep.FUTURES]
    out.append(_line("rows", opt["rows"]))
    out.append(_line(
        "OPTION book quality",
        ", ".join(f"{k} {v}" for k, v in opt["quality"]["counts"].items()),
    ))
    out.append(_line(
        "OPTION both sides",
        f"{opt['both_sides']} ({opt['both_sides_pct']}%)",
    ))
    out.append(_line(
        "FUTURES capture",
        f"EXACT {fut['counts'][fut_mod.EXACT]} "
        f"({fut['exact_pct']}%) · NEAR {fut['counts'][fut_mod.NEAR]} "
        f"({fut['near_pct']}%) · MISSING {fut['counts'][fut_mod.MISSING]} "
        f"({fut['missing_pct']}%)",
    ))
    if fut["missing_reasons"]:
        out.append(_line("FUTURES absent because", json.dumps(fut["missing_reasons"])))
    edge = section["market_edge"]
    out.append(_line(
        "market_edge measured",
        f"{edge['measured']} measured / {edge['missing']} missing "
        f"({edge['measured_pct']}%)",
    ))
    if edge["bases"]:
        out.append(_line("market_edge bases", json.dumps(edge["bases"])))
    if "mcx_geometry" in section:
        g = section["mcx_geometry"]
        out.append(_line(
            "MCX research T1",
            f"{g['measured']}/{g['rows_with_plan']} measured "
            f"({g['measured_pct']}%) on {g['basis']} — NOT VALIDATED",
        ))
        if g["refused_reasons"]:
            out.append(_line("MCX T1 refused because",
                             json.dumps(g["refused_reasons"])))
    ss = section["same_signal"]
    out.append(_line(
        "same-signal comparisons",
        f"{ss['comparisons']} costed · preferred: {ss['preferred_vehicle']}",
    ))
    for veh, row in ss["by_vehicle"].items():
        out.append(_line(
            f"  {veh}",
            f"{row['legs']} legs · mean net R {row['mean_net_r']} · "
            f"{row['positive']} positive",
        ))
    if ss["preferred_vehicle"] == rep.WITHHELD:
        out.append(_line("  preference withheld", ss["note"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tail", type=int, default=None,
                    help="read only the last N rows (default: the whole series)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    obs = store.observations(limit=args.tail, tail_bytes=None)
    legs = store.legs(limit=None, tail_bytes=None)
    out = rep.build(
        obs, legs,
        complete=args.tail is None,
        rows_dropped=0,
    )
    if args.json:
        print(json.dumps(out, indent=2))
        return

    cov = out["evidence_coverage"]
    print("PHASE 21 — CAPTURE AND VEHICLE REPORT (research only)")
    print(f"scope: {cov['scope']} · {cov['rows_read']} observations · "
          f"{cov['legs_read']} legs · complete: {cov['complete']}")
    print()
    for ln in _capture_block("ALL FAMILIES POOLED (headline only)", out["totals"]):
        print(ln)
    for name, section in out["families"].items():
        if not section["rows"] and not section["legs"]:
            print(f"\n{name}: no rows and no legs in scope")
            continue
        print()
        for ln in _capture_block(
            f"{name} — {len(section['instruments'])} instruments", section
        ):
            print(ln)
        if section["legs_without_observation_in_scope"]:
            print(_line(
                "legs whose observation rolled",
                f"{section['legs_without_observation_in_scope']} of "
                f"{section['legs']} — economics measured, originating row "
                f"outside this read",
            ))
    print()
    print(out["note"])


if __name__ == "__main__":
    main()
