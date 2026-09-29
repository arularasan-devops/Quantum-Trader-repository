"""Stage-2 CLI. Four commands, in the order §36 forces.

    python -m app.research.phase56.nse.cli prereg
    python -m app.research.phase56.nse.cli ingest --start 2020-07-01 --end 2026-03-31
    python -m app.research.phase56.nse.cli build
    python -m app.research.phase56.nse.cli audit --write

``ingest`` is resumable and safe to interrupt: sessions already stored are
skipped, unreachable days stay marked unfinished and are retried on the next run.
``build`` derives the adjusted series and the break ledger from what is on disk
and makes no network call at all. ``audit`` re-runs the §36 gate over the ingested
dataset and is the only command that emits a go/stop verdict; no mechanism,
feature, portfolio or strategy verdict exists anywhere in this stage.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from . import PREREG, VERSION
from .adjust import build_all, unmapped_actions
from .archive import Archive
from .audit import audit as run_audit
from .indices import IndexStore, ingest_indices
from .ingest import ingest_range
from .store import RawStore

DEFAULT_ROOT = Path("data/research/phase56/nse")
DEFAULT_CACHE = Path("data/research/phase56/nse/cache")


def _store(args) -> RawStore:
    return RawStore(Path(args.root))


def _cmd_prereg(args) -> str:
    store = _store(args)
    payload = {"version": VERSION, "fingerprint": PREREG.fingerprint(), "prereg": PREREG.as_dict()}
    path = store.root / "preregistration_stage2.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return f"PREREG_FINGERPRINT {payload['fingerprint']}\nwrote {path}"


def _cmd_ingest(args) -> str:
    store = _store(args)
    archive = Archive(Path(args.cache))
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    seen = {"n": 0}

    def progress(day, status, summary):
        seen["n"] += 1
        if seen["n"] % 25 == 0 or status not in ("SESSION_OK", "SESSION_NOT_PUBLISHED"):
            print(
                f"  {day} {status:24s} ok={summary.ok} holiday={summary.not_published} "
                f"unavailable={summary.unavailable} rows={summary.rows}",
                flush=True,
            )

    summary = ingest_range(
        start,
        end,
        archive,
        store,
        with_delivery=not args.no_delivery,
        with_actions=not args.no_actions,
        progress=None if args.quiet else progress,
    )
    return "INGEST " + json.dumps(summary.as_dict(), indent=2, sort_keys=True)


def _cmd_indices(args) -> str:
    """§17 benchmark closes for every session already in the raw store."""
    store = _store(args)
    archive = Archive(Path(args.cache))
    counts = ingest_indices(store.sessions(), archive, IndexStore(store.root))
    return "INDICES " + json.dumps(counts, indent=2, sort_keys=True)


def _cmd_build(args) -> str:
    store = _store(args)
    symbol_rows = store.symbol_series(series="EQ")
    actions = store.read_actions()
    series, ledger = build_all(symbol_rows, actions)
    out_dir = store.adjusted_dir
    written = 0
    for symbol, rows in series.items():
        if not rows:
            continue
        path = out_dir / f"{symbol}.csv"
        header = ",".join(rows[0].as_dict().keys())
        lines = [header]
        for row in rows:
            lines.append(",".join("" if value is None else str(value) for value in row.as_dict().values()))
        path.write_text("\n".join(lines) + "\n")
        written += 1
    ledger_path = store.root / "break_ledger.json"
    ledger_path.write_text(json.dumps([item.as_dict() for item in ledger], indent=2))
    orphans = unmapped_actions(symbol_rows, actions)
    orphan_path = store.root / "unmapped_actions.json"
    orphan_path.write_text(json.dumps(orphans, indent=2, sort_keys=True))
    by_status: dict[str, int] = {}
    for item in ledger:
        by_status[item.status] = by_status.get(item.status, 0) + 1
    return (
        f"BUILD symbols={written} breaks={len(ledger)} unmapped={len(orphans)} "
        + json.dumps(dict(sorted(by_status.items())))
        + f"\nwrote {out_dir}, {ledger_path} and {orphan_path}"
    )


def _cmd_audit(args) -> str:
    store = _store(args)
    payload = run_audit(store)
    text = _render(payload)
    if args.write:
        (store.root / "audit_stage2.json").write_text(json.dumps(payload, indent=2, sort_keys=True))
        (store.root / "audit_stage2_report.txt").write_text(text)
    return text


def _render(payload: dict) -> str:
    lines = [
        "PHASE 56 STAGE 2 — NSE ARCHIVE DATASET, §36 GATE",
        f"VERSION              {payload['version']}",
        f"PREREG_FINGERPRINT   {payload['prereg_fingerprint']}",
        f"WINDOW               {payload.get('window', ['?', '?'])[0]}..{payload.get('window', ['?', '?'])[1]}",
        f"GATE                 {payload['gate']}",
        f"SURVIVORSHIP         {payload.get('survivorship_status', '?')}",
        f"FINGERPRINT          {payload.get('fingerprint', '?')}",
        "",
    ]
    for finding in payload["findings"]:
        lines.append(f"{finding['status']:14s} {finding['name']}")
        lines.append(f"               {finding['detail']}")
    if payload.get("blocking_failures"):
        lines.append("")
        lines.append("BLOCKING: " + ", ".join(payload["blocking_failures"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser("phase56.nse")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--cache", default=str(DEFAULT_CACHE))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("prereg").set_defaults(func=_cmd_prereg)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--start", required=True)
    ingest.add_argument("--end", required=True)
    ingest.add_argument("--no-delivery", action="store_true")
    ingest.add_argument("--no-actions", action="store_true")
    ingest.add_argument("--quiet", action="store_true")
    ingest.set_defaults(func=_cmd_ingest)

    sub.add_parser("indices").set_defaults(func=_cmd_indices)
    sub.add_parser("build").set_defaults(func=_cmd_build)

    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--write", action="store_true")
    audit_parser.set_defaults(func=_cmd_audit)

    args = parser.parse_args(argv)
    print(args.func(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
