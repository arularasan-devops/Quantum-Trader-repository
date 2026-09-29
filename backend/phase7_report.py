"""Phase 7 — one command, one report. RESEARCH ONLY, replay only, no orders.

Runs the whole Phase 7 chain over recorded history and writes a JSON report plus
a readable markdown summary:

    Part 1-2   is the recorded data fit to answer anything?
    Part 3-5   production BUYs (unchanged engine), real premium paths, entry chase
    Part 7/21  MFE capture and give-back
    Part 6/8/14 research-only entry, exit and combined policies
    Part 22    losing-trade cause attribution
    Part 23    manual fills vs bot fills, normalised (needs --manual)
    Part 24-25 acceptance gates, which decide what the report may claim

    .venv/bin/python phase7_report.py --instruments CRUDEOIL,NIFTY \
        --out ~/phase7_report.json --md ~/phase7_report.md

Nothing here imports the order path, and the production engine is called only to
*read* its decision on a recorded bar.
"""
from __future__ import annotations

import argparse
import json
import os

from app.config import settings
from app.market import tick_quality
from app.research.phase7 import attribution, gates, paths, policies, quality
from app.research.phase7.dataset import (
    REAL,
    SIM,
    UNKNOWN,
    instruments_with_chains,
    load_candles,
    load_chains,
)


def _per_instrument(db: str, instrument: str, horizon: int, limit: int) -> dict:
    series = load_chains(db, instrument)
    candles = load_candles(db, instrument, limit)
    fresh_ms = tick_quality.FRESH_MS

    chain = quality.chain_audit(series)
    candle = quality.candle_audit(candles)
    fresh = quality.freshness_audit(candles, series[REAL], fresh_ms)

    real = series[REAL]
    # The reconstruction runs on REAL_BROKER snapshots only. If there are none,
    # it is run on the simulator series and labelled as such — a plumbing test,
    # explicitly worth nothing as evidence.
    source = REAL if len(real) else SIM
    used = series[source]
    events, recon = ([], {"skipped": "no chain snapshots"}) if not len(used) else \
        paths.reconstruct(instrument, candles, used, fresh_ms, horizon)

    for ev in events:
        ev.chase = paths.measure_chase(ev, used)
    cuts = paths.chase_thresholds(events)
    chase_classes: dict[str, int] = {c: 0 for c in paths.CHASE_CLASSES}
    chase_classes["UNKNOWN"] = 0
    for ev in events:
        label = paths.classify_chase(ev.chase.get("consumed_frac"), cuts.get("cuts"))
        ev.chase["class"] = label
        chase_classes[label] += 1

    baseline = [policies.simulate(ev, "CONTROL", "A_BASELINE") for ev in events]
    trade_rows = []
    for ev, fill in zip(events, baseline, strict=True):
        if not fill.entered or ev.path is None:
            continue
        after = (ev.path.first_target_ts is not None
                 and ev.path.first_stop_ts is not None
                 and ev.path.first_target_ts > ev.path.first_stop_ts)
        gross_r = fill.r
        cost_r = policies.COST_PER_ROUNDTRIP_OPTIONS / max(
            1.0, (ev.entry - ev.stop) * 100.0)
        trade_rows.append({
            "symbol": ev.symbol, "side": ev.side, "regime": ev.regime,
            "confidence": ev.confidence, "data_flag": ev.data_flag,
            "mfe_r": round(ev.path.mfe_r, 3), "mae_r": round(ev.path.mae_r, 3),
            "realised_r": round(fill.r, 3), "exit_reason": fill.exit_reason,
            "target_after_stop": after,
            "entry_improvement_pct": ev.chase.get("improvement_available_pct") or 0.0,
            "gross_r": round(gross_r, 3), "net_r": round(gross_r - cost_r, 3),
            "capture": paths.mfe_capture(fill.r, ev.path.mfe_r),
            "held_min": fill.held_min,
            "entry": round(fill.entry, 2), "exit": round(fill.exit, 2),
        })

    per_cell = _min_cell(trade_rows)
    return {
        "instrument": instrument,
        "chain_source_used": source,
        "part1_chain_dataset": chain,
        "part2_underlying_quality": candle,
        "part2_data_freshness": fresh,
        "part3_4_reconstruction": recon,
        "part5_entry_chase": {"thresholds": cuts, "classes": chase_classes},
        "part7_21_capture": _capture_block(trade_rows),
        "part6_entry_policies": policies.entry_study(events),
        "part8_exit_policies": policies.exit_study(events),
        "part14_combinations": policies.combination_study(events),
        "part22_loss_attribution": attribution.loss_attribution(trade_rows),
        "part22_signal_vs_execution": attribution.signal_vs_execution(trade_rows),
        "trades": trade_rows,
        "events": [ev.as_dict() for ev in events],
        "_min_cell": per_cell,
        "_events": len(events),
        "_chain": chain,
        "_candle": candle,
    }


def _min_cell(rows: list[dict]) -> int:
    if not rows:
        return 0
    cells: dict[str, int] = {}
    for r in rows:
        key = f"{r['regime']}|{r['side']}"
        cells[key] = cells.get(key, 0) + 1
    return min(cells.values())


def _capture_block(rows: list[dict]) -> dict:
    caps = [r["capture"]["capture_pct"] for r in rows
            if r["capture"].get("capture_pct") is not None]
    gives = [r["capture"]["giveback_r"] for r in rows]
    big = [r for r in rows if r["mfe_r"] >= 1.0 and r["realised_r"] <= 0.0]
    return {
        "n": len(rows),
        "median_capture_pct": policies.median(caps),
        "median_giveback_r": policies.median(gives),
        "offered_1r_then_lost": len(big),
        "offered_1r_then_lost_symbols": [r["symbol"] for r in big][:20],
    }


def _markdown(report: dict) -> str:
    g = report["acceptance"]
    lines = [
        "# Phase 7 — real-chain entry/exit intelligence (research only)",
        "",
        f"**Status: {g['status']}** — {g['meaning']}",
        "",
        "## Acceptance gates (Part 24)",
        "",
        "| gate | need | have | pass |",
        "| --- | --- | --- | --- |",
    ]
    for row in g["gates"]:
        lines.append(f"| {row['gate']} | {row['need']} | {row['have']} | "
                     f"{'PASS' if row['pass'] else 'FAIL'} |")
    lines += ["", "Failing gates forbid these conclusions:", ""]
    lines += [f"- **{row['gate']}** — {row['forbids']}"
              for row in g["gates"] if not row["pass"]] or ["- none"]
    for inst, block in report["per_instrument"].items():
        chain = block["part1_chain_dataset"]
        real = chain.get(REAL, {})
        cand = block["part2_underlying_quality"]
        lines += [
            "", f"## {inst}", "",
            f"- chain used: **{block['chain_source_used']}** "
            f"(REAL {chain['by_provenance'].get(REAL, 0)} / "
            f"SIM {chain['by_provenance'].get(SIM, 0)} / "
            f"UNKNOWN {chain['by_provenance'].get(UNKNOWN, 0)})",
            f"- real sessions: {real.get('sessions', 0)}; cadence p50 "
            f"{real.get('cadence_sec', {}).get('p50', 'n/a')} s; "
            f"bid/ask available: {chain['verdict']['spread_measurable']}",
            f"- underlying bars {cand.get('bars')} · missing "
            f"{cand.get('missing_bars')} ({cand.get('missing_pct')}%) · "
            f"duplicates {cand.get('duplicate_timestamps')} · "
            f"out-of-order {cand.get('out_of_order')}",
            f"- production BUYs reconstructed: {block['_events']}",
        ]
        cap = block["part7_21_capture"]
        if cap["n"]:
            lines += [
                f"- MFE capture median {cap['median_capture_pct']}% · give-back "
                f"median {cap['median_giveback_r']}R · offered 1R then lost: "
                f"{cap['offered_1r_then_lost']}",
            ]
            sve = block["part22_signal_vs_execution"]
            lines.append(f"- offered {sve.get('r_offered_total')}R, kept "
                         f"{sve.get('r_kept_total')}R "
                         f"(capture {sve.get('capture_pct')}%)")
            lines += ["", "| exit policy | entered | expectancy R | PF | "
                      "target first % | stopped % | median hold |",
                      "| --- | --- | --- | --- | --- | --- | --- |"]
            for name, blk in block["part8_exit_policies"].items():
                lines.append(
                    f"| {name} | {blk['entered']} | {blk['expectancy_r']} | "
                    f"{blk['profit_factor']} | {blk['target_before_stop_pct']} | "
                    f"{blk['stopped_pct']} | {blk['median_hold_min']} |")
            overshoot = block["part8_exit_policies"]["A_BASELINE"][
                "median_stop_overshoot_r"]
            lines += ["", f"- median stop overshoot: {overshoot}R \u2014 a stop can "
                      "only be acted on at the next recorded quote, so a stopped "
                      "trade loses more than the 1R it was risked at"]
            lines += ["", "| entry policy | entered | missed % | avg improvement % | "
                      "expectancy R |", "| --- | --- | --- | --- | --- |"]
            for name, blk in block["part6_entry_policies"].items():
                lines.append(
                    f"| {name} | {blk['entered']} | {blk['missed_pct']} | "
                    f"{blk['avg_entry_improvement_pct']} | {blk['expectancy_r']} |")
            chase = block["part5_entry_chase"]["classes"]
            lines += ["", "- entry chase classes: " + ", ".join(
                f"{k} {v}" for k, v in chase.items() if v)]
    if report.get("part23_manual_vs_bot"):
        cmp_ = report["part23_manual_vs_bot"]
        lines += ["", "## Part 23 — manual fills vs bot fills", "",
                  f"- shared legs: {cmp_['shared_legs']}; "
                  f"attribution {cmp_['attribution_pct']}"]
        for row in cmp_["rows"]:
            lines.append(
                f"- `{row['symbol']}` manual {row['manual']['points']:+.2f} pts / "
                f"{row['manual']['r']:+.2f}R vs bot {row['bot']['points']:+.2f} pts / "
                f"{row['bot']['r']:+.2f}R · entry gap {row['entry_gap_pct']:+.2f}%")
        lines += [""] + [f"- caveat: {c}" for c in cmp_["caveats"]]
    lines += ["", "## Limits carried into every number above", ""]
    lines += [f"- {x}" for x in report["limits"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruments", default="",
                    help="comma separated; default = every instrument with chains")
    ap.add_argument("--db", default="")
    ap.add_argument("--horizon", type=int, default=paths.HORIZON_BARS)
    ap.add_argument("--limit", type=int, default=0,
                    help="use only the newest N candles per instrument")
    ap.add_argument("--manual", default="",
                    help="JSON file of your own fills for Part 23")
    ap.add_argument("--out", default="")
    ap.add_argument("--md", default="")
    args = ap.parse_args()

    db = args.db or os.path.join(settings.data_dir, "history.db")
    if not os.path.exists(db):
        raise SystemExit(f"no history db at {db}")
    names = [x.strip().upper() for x in args.instruments.split(",") if x.strip()] \
        or instruments_with_chains(db)[:5]

    per: dict[str, dict] = {}
    for name in names:
        per[name] = _per_instrument(db, name, args.horizon, args.limit)

    events = sum(b["_events"] for b in per.values())
    worst_missing = max((b["_candle"].get("missing_pct") or 0.0
                         for b in per.values()), default=0.0)
    best_chain = max(per.values(), key=lambda b: b["_chain"][REAL]["snapshots"]
                     if b["_chain"].get(REAL, {}).get("snapshots") else 0)
    min_cell = min((b["_min_cell"] for b in per.values()), default=0)

    manual_block = None
    if args.manual:
        manual = attribution.load_manual_fills(args.manual)
        # The bot side of the comparison is the CONTROL/A_BASELINE fill — the
        # engine's own entry and its own exit, not a best case.
        bot = {}
        for block in per.values():
            for row in block["trades"]:
                bot[row["symbol"]] = attribution.Leg(
                    symbol=row["symbol"], entry=float(row["entry"]),
                    exit=float(row["exit"]))
        manual_block = attribution.compare_fills(manual, bot)

    acceptance = gates.evaluate(
        best_chain["_chain"], {"missing_pct": worst_missing}, events, min_cell,
        manual_block["shared_legs"] if manual_block else 0)

    # The limits are measured, not assumed: a caveat that contradicts the audit is
    # worse than no caveat, and the earlier hardcoded "no bid/ask is recorded" line
    # said exactly the opposite of the SPREAD_AVAILABLE gate once real chains with a
    # book arrived.
    cadences = [c for c in (
        b["_chain"].get(b["chain_source_used"], {}).get("cadence_sec", {}).get("p50")
        for b in per.values()) if c]
    cadence_note = (f"~{min(cadences):.0f}-{max(cadences):.0f}s"
                    if cadences else "irregular")
    spread_note = (
        "bid/ask IS recorded on the legs, but the fills modelled here are taken at "
        "the snapshot premium, not across the spread, so they remain optimistic"
        if any(g["gate"] == "SPREAD_AVAILABLE" and g["pass"]
               for g in acceptance["gates"])
        else "no bid/ask is recorded, so every entry and exit price here is better "
             "than a real fill would have been")

    report = {
        "phase": 7,
        "scope": "RESEARCH ONLY — replay of recorded history. No production rule, "
                 "gate, stop, target, strike selector or order path is touched by "
                 "this report, and no order can be placed from it.",
        "db": db,
        "instruments": names,
        "acceptance": acceptance,
        "per_instrument": {k: {kk: vv for kk, vv in v.items()
                               if not kk.startswith("_")}
                           for k, v in per.items()},
        "part23_manual_vs_bot": manual_block,
        "limits": [
            f"option premiums are {cadence_note} snapshot closes: a stop touch or "
            "target touch between two snapshots is invisible",
            spread_note,
            "costs are an assumption "
            f"(₹{policies.COST_PER_ROUNDTRIP_OPTIONS:.0f}/round trip options, "
            f"₹{policies.COST_PER_ROUNDTRIP_FUTURES:.0f} futures) until a contract "
            "note is supplied",
            "the reconstruction replays the production engine on stored bars; live "
            "gates such as cooldowns, concurrency and the risk governor are not "
            "applied, so it produces MORE BUYs than the live funnel did",
        ],
    }
    text = json.dumps(report, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    md = _markdown({**report, "per_instrument": per})
    if args.md:
        with open(args.md, "w", encoding="utf-8") as fh:
            fh.write(md)
    print(md)
    print(f"status={acceptance['status']} events={events} "
          f"failed_gates={','.join(acceptance['failed']) or 'none'}")


if __name__ == "__main__":
    main()
