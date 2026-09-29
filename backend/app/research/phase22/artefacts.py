"""Phase 22 report artefacts: JSON for machines, Markdown for people.

Both are written from the same computed dicts, so the prose cannot drift away
from the numbers. Every Markdown file opens with the frozen definition and its
fingerprint, then the coverage, then the verdict — in that order, because a
reader who skips the first two will misread the third.
"""
from __future__ import annotations

import json
import os
import time

from app.research.phase22 import definition as defn, verdict as verdict_mod

FILES = ("definition", "pullback_vs_non_pullback", "stop_bands",
         "vehicles", "verdict")


def _write(outdir: str, name: str, payload: dict) -> str:
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"phase22_{name}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
    return path


def _md(outdir: str, name: str, text: str) -> str:
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"phase22_{name}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _header(title: str) -> str:
    return (f"# {title}\n\n"
            f"Definition: `{defn.VERSION}`  \n"
            f"Fingerprint: `{defn.fingerprint()}`  \n"
            f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}  \n"
            "Execution: PAPER ONLY — nothing here places an order or changes a "
            "production gate.\n\n")


def _cohort_table(cmp_: dict) -> str:
    keys = ("trades", "t1_before_sl_pct", "expectancy_r", "profit_factor",
            "avg_win_r", "avg_loss_r", "max_drawdown_r", "median_mfe_r",
            "median_mae_r", "median_minutes_to_t1", "median_minutes_to_sl",
            "trades_per_day", "measurable")
    lines = ["| metric | PULLBACK | NON_PULLBACK |", "| --- | --- | --- |"]
    for key in keys:
        a = cmp_[defn.LABEL].get(key)
        b = cmp_[defn.OTHER].get(key)
        lines.append(f"| {key} | {a} | {b} |")
    return "\n".join(lines) + "\n"


def definition_md() -> str:
    spec = defn.spec()
    body = _header("Phase 22 — the frozen setup")
    body += (defn.__doc__ or "").strip() + "\n\n```json\n"
    body += json.dumps(spec, indent=2) + "\n```\n"
    return body


def study_md(result: dict) -> str:
    body = _header("Phase 22 — PULLBACK vs NON_PULLBACK")
    pool = result["pool"]
    body += (f"Pool: {pool['candidates']} candidates over {pool['sessions']} "
             f"sessions; {pool['engine_buy']} the production engine would have "
             f"taken and {pool['engine_refused']} it refused. Refused "
             "candidates are kept: a setup graded only on trades that were "
             "taken is graded through the gates that took them.\n\n")
    body += "## Where the definition refuses\n\n"
    body += "| condition | refused | % of pool | sole refusal |\n| --- | --- | --- | --- |\n"
    for row in result["refusal_census"]:
        body += (f"| {row['condition']} | {row['refused']} | "
                 f"{row['refused_pct']} | {row['sole_refusal']} |\n")
    for period in ("development", "validation", "holdout"):
        span = (result["pool"]["spans"] or {}).get(period) or {}
        body += (f"\n## {period.upper()} — {span.get('first')} to "
                 f"{span.get('last')}\n\n")
        body += _cohort_table(result["periods"][period])
    wf = result["walk_forward"]
    body += f"\n## Walk-forward\n\nVerdict: **{wf.get('verdict')}**\n\n"
    body += ("\n## Assumed cost bands\n\nThese are assumptions, not prices. The "
             "band where expectancy crosses zero is the round-trip cost this "
             "setup can afford; the vehicle report says whether anything real "
             "is that cheap.\n\n")
    body += "| cost R | dev expectancy | holdout expectancy |\n| --- | --- | --- |\n"
    dev = {r["cost_r"]: r for r in result["cost_curve"]["development"]}
    hold = {r["cost_r"]: r for r in result["cost_curve"]["holdout"]}
    for band in sorted(dev):
        body += (f"| {band} | {dev[band].get('expectancy_r')} | "
                 f"{hold.get(band, {}).get('expectancy_r')} |\n")
    return body


def stop_bands_md(result: dict) -> str:
    bands = result["stop_bands"]
    body = _header("Phase 22 — research stop distance")
    body += (f"Coverage: {bands['with_bands']} of {bands['candidates']} "
             f"candidates carry path-measured bands ({bands.get('coverage_pct')}%)."
             "\n\nA band wider than the plan's own stop cannot be graded on a "
             "trade the plan stopped out of — those bars were never walked. "
             "Those rows are counted as unresolved, never estimated.\n\n")
    if bands.get("note"):
        body += f"> {bands['note']}\n\n"
    body += ("| band | resolved | unresolved | expectancy R | T1 before SL % | "
             "PF | max DD R |\n| --- | --- | --- | --- | --- | --- | --- |\n")
    for row in bands["bands"]:
        body += (f"| {row['band']} | {row['resolved']} | {row['unresolved']} | "
                 f"{row.get('expectancy_r')} | {row.get('t1_before_sl_pct')} | "
                 f"{row.get('profit_factor')} | {row.get('max_drawdown_r')} |\n")
    return body


def vehicles_md(vehicle: dict | None) -> str:
    body = _header("Phase 22 — CE vs PE vs FUTURES")
    if not vehicle:
        body += ("No captured vehicle comparison was supplied, so no net figure "
                 "on real prices exists. REQUIRES_MORE_DATA.\n")
        return body
    cov = vehicle["coverage"]
    body += (f"Captured opportunities: {cov['captured_opportunities']}; "
             f"{cov['with_measured_option_book']} had a recorded two-sided "
             f"option book ({cov['measured_book_pct']}%); "
             f"{cov['no_trade']} resolved to NO_TRADE "
             f"({cov['no_trade_pct']}%).\n\nOptions are entered at the ask and "
             "exited at the bid on a recorded book. Futures carry statutory "
             "charges and configured slippage but no measured depth, so they "
             "are disclosed and never ranked against a measured option. Mid "
             "prices appear nowhere in this table.\n\n")
    body += ("| vehicle | captured | measured | pricing | net expectancy R | PF "
             "| win % |\n| --- | --- | --- | --- | --- | --- | --- |\n")
    for row in vehicle["by_vehicle"]:
        body += (f"| {row['vehicle']} | {row['captured_legs']} | "
                 f"{row['measured_legs']} | {row['pricing']} | "
                 f"{row.get('net_expectancy_r')} | {row.get('profit_factor')} | "
                 f"{row.get('win_pct')} |\n")
    sel = vehicle["selected"]
    body += (f"\nSelected book: {sel.get('legs')} legs, net expectancy "
             f"{sel.get('net_expectancy_r')}R, PF {sel.get('profit_factor')}, "
             f"{sel.get('trades_per_day')} trades/day.\n")
    return body


def verdict_md(final: dict) -> str:
    body = _header("Phase 22 — verdict")
    body += f"## {final['verdict']}\n\n"
    body += ("| condition | state | detail |\n| --- | --- | --- |\n")
    for check in final["checks"]:
        body += f"| {check['condition']} | {check['state']} | {check['detail']} |\n"
    body += ("\nA FAIL outranks an UNKNOWN: a setup is not rescued from a clear "
             "negative by something else being unmeasured. "
             f"{final['passed']} passed, {final['failed']} failed, "
             f"{final['unknown']} unanswerable.\n\n"
             "Execution stays PAPER_ONLY whatever this says. Promotion to "
             "production is a manual human decision and nothing in Phase 22 "
             "can make it automatically.\n")
    return body


def write_all(outdir: str, payload: dict) -> dict[str, str]:
    """Write every artefact. Returns name -> path for both formats."""
    result, vehicle = payload["study"], payload.get("vehicles")
    final = payload.get("verdict") or verdict_mod.assess(result, vehicle)
    written: dict[str, str] = {}
    written["definition.json"] = _write(outdir, "definition", {
        "definition": defn.spec(), "fingerprint": defn.fingerprint()})
    written["definition.md"] = _md(outdir, "definition", definition_md())
    written["study.json"] = _write(outdir, "pullback_vs_non_pullback", result)
    written["study.md"] = _md(outdir, "pullback_vs_non_pullback",
                              study_md(result))
    written["stop_bands.json"] = _write(outdir, "stop_bands",
                                        result["stop_bands"])
    written["stop_bands.md"] = _md(outdir, "stop_bands", stop_bands_md(result))
    written["vehicles.json"] = _write(outdir, "vehicles", vehicle or {})
    written["vehicles.md"] = _md(outdir, "vehicles", vehicles_md(vehicle))
    written["verdict.json"] = _write(outdir, "verdict", final)
    written["verdict.md"] = _md(outdir, "verdict", verdict_md(final))
    return written
