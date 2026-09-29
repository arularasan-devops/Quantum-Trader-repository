"""Phase 56 stage-3 CLI. Research and paper only; no order path anywhere.

    python -m app.research.phase56.equity.cli prereg
    python -m app.research.phase56.equity.cli study [--write] [--symbols A,B]
    python -m app.research.phase56.equity.cli scan [--as-of YYYY-MM-DD] [--write]
    python -m app.research.phase56.equity.cli journal
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import PREREG, ROBUST_CANDIDATE
from .paper import Journal, scan
from .panel import load_panel
from .report import rank_key, report_text
from .run import ARTEFACT_ROOT, DEFAULT_ROOT, run, validation_is_untouched


def _cmd_prereg(args) -> str:
    payload = {**PREREG.as_dict(), "fingerprint": PREREG.fingerprint()}
    return json.dumps(payload, indent=1, sort_keys=True)


def _cmd_study(args) -> str:
    symbols = args.symbols.split(",") if args.symbols else None
    result = run(
        data_root=Path(args.data),
        out_root=Path(args.out),
        symbols=symbols,
        verbose=not args.quiet,
    )
    text = report_text(result)
    check = validation_is_untouched(result)
    text += "\nSELECTION SELF-CHECK (§18)\n"
    text += f"  selected by discovery+validation only: {len(check['selected_without_holdout'])}\n"
    text += f"  promoted after holdout:                {len(check['promoted'])}\n"
    text += f"  promoted is a subset of the selection: {check['promoted_subset_of_selection']}\n"
    if args.write:
        text += f"\nartefacts: {json.dumps(result['paths'], indent=1)}\n"
    return text


def _frozen_candidates(out_root: Path) -> list[dict]:
    path = Path(out_root) / "study_rows.json"
    if not path.exists():
        raise SystemExit(f"no study rows at {path}; run `study --write` first")
    rows = json.loads(path.read_text())
    for row in rows:
        row["entry_spec"] = {
            "name": row["entry"],
            "family": row["entry_family"],
            "params": row["entry_params"],
        }
        row["study_fingerprint"] = PREREG.fingerprint()
    return rows


def _cmd_scan(args) -> str:
    rows = _frozen_candidates(Path(args.out))
    robust = [row for row in rows if row["status"] == ROBUST_CANDIDATE]
    panel = load_panel(Path(args.data))
    journal = Journal(Path(args.out))
    if not robust:
        # Fail-closed: with nothing promoted, the scanner emits NO_TRADE and says why.
        best = sorted(rows, key=rank_key, reverse=True)[0]
        result = scan(panel, best, as_of=args.as_of)
        if args.write:
            journal.record_scan(result)
        return (
            "NO ROBUST CANDIDATE IS PROMOTED — the scanner is fail-closed.\n"
            f"best-ranked registered row: {best['entry']} / {best['exit']} ({best['status']})\n"
            + json.dumps(result, indent=1, default=str)
        )
    out = []
    for candidate in robust:
        result = scan(panel, candidate, as_of=args.as_of)
        if args.write:
            journal.record_scan(result)
        out.append(result)
    return json.dumps(out, indent=1, default=str)


def _cmd_journal(args) -> str:
    rows = Journal(Path(args.out)).rows()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.get("decision", "UNKNOWN")] = counts.get(row.get("decision", "UNKNOWN"), 0) + 1
    return json.dumps({"rows": len(rows), "decisions": counts, "last": rows[-3:]}, indent=1, default=str)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser("phase56-equity")
    parser.add_argument("--data", default=str(DEFAULT_ROOT))
    parser.add_argument("--out", default=str(ARTEFACT_ROOT))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("prereg").set_defaults(func=_cmd_prereg)

    study = sub.add_parser("study")
    study.add_argument("--write", action="store_true")
    study.add_argument("--quiet", action="store_true")
    study.add_argument("--symbols", default="")
    study.set_defaults(func=_cmd_study)

    scan_parser = sub.add_parser("scan")
    scan_parser.add_argument("--as-of", dest="as_of", default=None)
    scan_parser.add_argument("--write", action="store_true")
    scan_parser.set_defaults(func=_cmd_scan)

    sub.add_parser("journal").set_defaults(func=_cmd_journal)

    args = parser.parse_args(argv)
    print(args.func(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
