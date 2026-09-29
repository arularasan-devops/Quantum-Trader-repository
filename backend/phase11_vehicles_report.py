"""Multi-vehicle report — FUTURES vs CALL vs PUT vs NO_TRADE. RESEARCH ONLY.

Replays the production engine over recorded bars (Phase 7's reconstruction: bar
``i`` sees only ``candles[:i+1]``), then for every BUY it actually produced,
follows both vehicles over the same window on the same production levels and
records which one paid after costs.

What this can and cannot answer is fixed by the recording, not by the code:

* the **option** side pays its recorded bid/ask. That is real.
* the **futures** side pays modelled statutory charges and slippage, because the
  futures book was never recorded. Futures is therefore flattered by an unknown
  amount, and the report says so next to every futures win.
* no vehicle preference is stated as more than a measurement until a family has
  100 events and 5 chronological holdout sessions behind it.

    .venv/bin/python phase11_vehicles_report.py --db data/history.db \\
        --out phase11_vehicles.json --md PHASE11_VEHICLES.md
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.config import settings
from app.research.phase7 import dataset, paths
from app.research.phase11 import families, vehicles, vehiclerun
from phase11_report import DEFAULT_INSTRUMENTS
from phase9_report import collect

HOLDOUT_SESSIONS = 5


def build(db: str, names: list[str], horizon: int, limit: int,
          sessions_wanted: set[str]) -> dict:
    coll = collect(db, names, horizon, limit, sessions_wanted)
    per = coll["per"]
    sessions = sorted({r["session"] for r in coll["trade_rows"] if r.get("session")})
    # The holdout is chronological: the newest sessions, never a random split.
    # With no more sessions than the holdout wants, there is no holdout at all —
    # reserving every session would leave nothing the comparison was derived on,
    # which is not a holdout, it is a relabelling.
    holdout = sessions[-HOLDOUT_SESSIONS:] if len(sessions) > HOLDOUT_SESSIONS else []
    out = vehiclerun.build(per=per, sessions=len(sessions),
                           holdout_sessions=len(holdout))
    rep = out["report"]
    rep["sessions"] = sessions
    rep["holdout_sessions"] = holdout
    rep["instruments"] = sorted(per)
    rep["holdout_note"] = (
        f"no holdout exists yet: {len(sessions)} sessions are recorded and the "
        f"holdout wants {HOLDOUT_SESSIONS}, so reserving them would leave nothing "
        f"behind the numbers"
        if not holdout else
        f"the newest {len(holdout)} sessions are held out chronologically")
    rep["evidence_gate"] = {
        "sessions_required": 20, "sessions_present": len(sessions),
        "holdout_required": HOLDOUT_SESSIONS, "holdout_present": len(holdout),
        "status": ("EXPLORATORY_ONLY" if len(sessions) < 20 or len(holdout) < HOLDOUT_SESSIONS
                   else "READY_FOR_HOLDOUT_TEST"),
    }
    return {"report": rep, "rows": out["rows"]}


def render_md(rep: dict) -> str:
    lines = [
        "# MULTI-VEHICLE COMPARISON — RESEARCH / SHADOW ONLY",
        "",
        f"Status: **{rep['evidence_gate']['status']}**. "
        f"{rep['events_compared']} production BUY events over "
        f"{len(rep['sessions'])} sessions "
        f"({', '.join(rep['sessions']) or 'none'}); "
        f"{len(rep['holdout_sessions'])} chronological holdout sessions — "
        f"{rep['holdout_note']}.",
        "",
        "Nothing here changes the Signal tab, the strike selector, the stops, the "
        "targets or any order path. No futures order and no options order is "
        "placed by any of it.",
        "",
        "## Selection rule",
        "",
        f"`{rep['selection_rule']}`",
        "",
        f"**Cost asymmetry:** {rep['futures_cost_caveat']}",
        "",
    ]
    for fam, block in rep["families"].items():
        lines += [f"## {fam}", "",
                  f"{block['events']} events — verdict **{block['verdict']}**.", "",
                  "| vehicle | events comparable | costable | avg net R | "
                  "total net R | target before stop | chosen best |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for veh in (vehicles.FUTURES, vehicles.CALL, vehicles.PUT,
                    vehicles.NO_TRADE):
            v = block["by_vehicle"][veh]

            def num(x: object) -> str:
                return "—" if x is None else f"{x}"

            lines.append(
                f"| {veh} | {num(v['events_evaluated'])} | "
                f"{num(v['events_costable'])} | {num(v['avg_net_r'])} | "
                f"{num(v['total_net_r'])} | {num(v['target_before_stop_pct'])} | "
                f"{num(v['chosen_count'])} |")
        h2h = block["futures_better_than_production_leg_pct"]
        lines += ["",
                  f"Head to head on the {block['head_to_head_events']} events where "
                  f"both vehicles were costable: futures paid better on "
                  f"{'—' if h2h is None else str(h2h) + '%'} of them.",
                  "", f"_{block['verdict_basis']}_", ""]
    lines += ["## Events that could not be measured", ""]
    if rep["not_measurable"]:
        for reason, n in sorted(rep["not_measurable"].items(),
                                key=lambda kv: -kv[1]):
            lines.append(f"- {n} × {reason}")
    else:
        lines.append("- none")
    lines += ["",
              "## What this does not say", "",
              "- it does not say to trade futures. A measured net-R advantage over "
              "a sample this size, with the futures book unrecorded, is a reason "
              "to record the futures book — not a reason to switch vehicle.",
              "- it does not calibrate anything. No probability is produced here.",
              "- it does not pool index with MCX, so there is no single "
              "\"best vehicle\" line to read.", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(settings.data_dir, "history.db"))
    ap.add_argument("--instruments", default="")
    ap.add_argument("--horizon", type=int, default=paths.HORIZON_BARS)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sessions", default="")
    ap.add_argument("--out", default="phase11_vehicles.json")
    ap.add_argument("--md", default="")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        raise SystemExit(f"no history db at {args.db}")
    names = [s.strip().upper() for s in args.instruments.split(",") if s.strip()]
    if not names:
        recorded = set(dataset.instruments_with_chains(args.db))
        names = [n for n in DEFAULT_INSTRUMENTS if n in recorded]
    sessions_wanted = {s.strip() for s in args.sessions.split(",") if s.strip()}

    out = build(args.db, names, args.horizon, args.limit, sessions_wanted)
    Path(args.out).write_text(json.dumps(out["report"], indent=2, default=str))
    if args.md:
        Path(args.md).write_text(render_md(out["report"]) + "\n")
    fam_counts = {f: b["events"] for f, b in out["report"]["families"].items()}
    print(f"wrote {args.out}: {out['report']['events_compared']} events "
          f"{fam_counts} families={list(families.FAMILIES)}")


if __name__ == "__main__":
    main()
