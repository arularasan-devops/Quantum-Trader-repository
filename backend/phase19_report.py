"""Phase 19 CLI — readiness, T1 calibration, futures paper book.

    .venv/bin/python phase19_report.py --outdir ~/phase19

Writes three pairs (JSON + Markdown) and nothing else. It deliberately does not
re-emit any Phase 17 or Phase 18 table: those reports are the source this reads,
and duplicating them under new filenames would produce two answers to the same
question that drift apart.

Read the capture line first. Every performance number below it is computed over
the trades that were actually recorded, so a low capture rate means the tables
describe a fraction of what happened and are not yet worth acting on.
"""
from __future__ import annotations

import argparse
import json
import os

from app.research.phase19 import service


def _f(v: object, dp: int = 2) -> str:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return "—"
    return f"{float(v):.{dp}f}"


def _pct(v: object) -> str:
    return "—" if not isinstance(v, (int, float)) or isinstance(v, bool) else f"{float(v):.1f}%"


def readiness_md(board: dict) -> str:
    cap = board.get("capture", {})
    out = [
        "# Phase 19 — production readiness",
        "",
        f"**{board.get('banner', '')}**",
        "",
        "## Capture — read this before anything below",
        "",
        f"- option exact/near: {_pct(cap.get('option_exact_near_pct'))} "
        f"(target {_pct(cap.get('option_target_pct'))})",
        f"- CAS capture: {_pct(cap.get('cas_match_pct'))}",
        f"- futures feed freshness: {_pct(cap.get('futures_freshness_pct'))}",
        "",
        "## Strategies",
        "",
        "| strategy | status | sample | holdout | net exp (R) | PF | max DD (R) "
        "| capture | last validated |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in board.get("rows", []):
        out.append(
            f"| {row.get('strategy')} | {row.get('status')} | {row.get('sample_size')} "
            f"| {row.get('holdout_size')} | {_f(row.get('net_expectancy_r'))} "
            f"| {_f(row.get('profit_factor'))} | {_f(row.get('max_drawdown_r'))} "
            f"| {_pct(row.get('capture_pct'))} | {row.get('last_validation') or '—'} |"
        )
    out += ["", "## Blocking reasons", ""]
    for row in board.get("rows", []):
        out.append(f"### {row.get('strategy')}")
        blockers = row.get("blockers") or []
        if not blockers:
            out.append("- none — the evidence gate is met, and a human must still approve it.")
        else:
            out += [f"- {b}" for b in blockers]
        out.append("")
    out += [
        "## Promotion",
        "",
        f"- production candidates: {', '.join(board.get('production_candidates') or []) or 'none'}",
        "- PRODUCTION_CANDIDATE is a request for review. Nothing in Phase 19 "
        "activates, sizes or routes a trade, and real-money execution stays off.",
        "",
        board.get("note", ""),
        "",
    ]
    return "\n".join(out)


def calibration_md(cal: dict) -> str:
    out = [
        "# Phase 19 — T1 calibration",
        "",
        f"- verdict: **{cal.get('verdict')}**",
        f"- display permitted: **{cal.get('display')}**",
        f"- publish probability: {cal.get('may_publish_probability')}",
        "",
        f"- resolved rows: {cal.get('rows')} "
        f"(development {cal.get('dev_rows')}, holdout {cal.get('holdout_rows')})",
        f"- base rate: {_f(cal.get('base_rate'), 4)}",
        f"- Brier: {_f(cal.get('brier'), 4)} "
        f"vs base-rate {_f(cal.get('brier_base_rate'), 4)} "
        f"(skill {_f(cal.get('brier_skill'), 4)})",
        f"- ECE: {_f(cal.get('ece'), 4)}",
        f"- monotone across bins: {cal.get('monotone')}",
        "",
        "## Reliability curve",
        "",
        "| score bin | predicted | observed | gap | n |",
        "|---|---|---|---|---|",
    ]
    for b in cal.get("reliability_curve", []):
        out.append(
            f"| {b.get('bin')} | {_f(b.get('predicted'), 3)} | {_f(b.get('observed'), 3)} "
            f"| {_f(b.get('gap'), 3)} | {b.get('n')} |"
        )
    out += ["", "## Blockers", ""]
    blockers = cal.get("blockers") or []
    out += [f"- {b}" for b in blockers] or ["- none"]
    out += ["", cal.get("note", ""), ""]
    return "\n".join(out)


def futures_md(board: dict) -> str:
    h = board.get("health", {})
    ev = board.get("evaluation", {})
    out = [
        "# Phase 19 — futures paper book",
        "",
        f"**{board.get('banner', '')}**",
        "",
        f"- enabled: {h.get('enabled')}",
        f"- valid plans seen: {h.get('plans_seen')}",
        f"- paper entries: {h.get('entries')}",
        f"- paper exits: {h.get('exits')}",
        f"- resolved: {h.get('resolved')}",
        f"- open now: {h.get('open')}",
        f"- feed freshness: {_pct(board.get('freshness_pct'))}",
        "",
        "## Refusals",
        "",
    ]
    skipped = h.get("skipped") or {}
    out += [f"- {k}: {v}" for k, v in sorted(skipped.items())] or ["- none"]
    out += [
        "",
        "## Chronological folds (net of costs, in R)",
        "",
        "| fold | trades | expectancy | PF | win % | max DD |",
        "|---|---|---|---|---|---|",
    ]
    for name in ("development", "validation", "holdout"):
        f = ev.get(name) or {}
        out.append(
            f"| {name} | {f.get('trades', 0)} | {_f(f.get('expectancy_r'))} "
            f"| {_f(f.get('profit_factor'))} | {_pct(f.get('win_pct'))} "
            f"| {_f(f.get('max_drawdown_r'))} |"
        )
    out += [
        "",
        f"- rows considered: {ev.get('rows_considered')}",
        f"- rows excluded as uncostable: {ev.get('rows_excluded_uncosted')}",
        f"- rows with a measured futures spread: {ev.get('rows_with_measured_spread')}",
        "",
        ev.get("note", ""),
        "",
    ]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", default="phase19_out")
    args = ap.parse_args()
    outdir = os.path.expanduser(args.outdir)
    os.makedirs(outdir, exist_ok=True)

    board = service.readiness()
    cal = service.calibration()
    fut = service.futures_board()

    artefacts = {
        "phase19_readiness": (board, readiness_md(board)),
        "phase19_calibration": (cal, calibration_md(cal)),
        "phase19_futures_paper": (fut, futures_md(fut)),
    }
    for name, (payload, md) in artefacts.items():
        with open(os.path.join(outdir, f"{name}.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
        with open(os.path.join(outdir, f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(md)

    cap = board.get("capture", {})
    print(f"option exact/near capture: {_pct(cap.get('option_exact_near_pct'))} "
          f"(target {_pct(cap.get('option_target_pct'))})")
    for row in board.get("rows", []):
        print(f"{row.get('strategy'):>16}: {row.get('status'):<22} "
              f"sample {row.get('sample_size'):<5} holdout {row.get('holdout_size'):<5} "
              f"blockers {len(row.get('blockers') or [])}")
    print(f"T1 calibration: {cal.get('verdict')} — display {cal.get('display')}")
    print(f"wrote {len(artefacts) * 2} artefacts to {outdir}")


if __name__ == "__main__":
    main()
