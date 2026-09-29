"""Phase 55 CLI.

    python -m app.research.phase55.cli universe
    python -m app.research.phase55.cli resolve [--json]
    python -m app.research.phase55.cli collect [--instrument NAME ...] [--start DATE]
    python -m app.research.phase55.cli register
    python -m app.research.phase55.cli audit [--write]
    python -m app.research.phase55.cli study [--family ORB_RETEST_P53] [--write]

``universe`` and ``resolve`` write nothing. ``collect`` is the only command that
talks to the broker's historical endpoint, is resumable, and stores nothing but
candles and provenance. ``study`` refuses to run on an instrument the audit did
not admit, so a thin series cannot reach a grade by being asked for directly.

Credentials are read from the environment inside ``_login`` and nowhere else in
this package; no value is ever printed or written to an artefact.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time

from app.research.mcxhist import contracts as mcxcontracts
from app.research.phase55 import (
    ALREADY_SHIPPED,
    DEFAULT_START,
    MIN_INTERVAL_SEC,
    UNIVERSE,
    fingerprint,
    preregistration,
)
from app.research.phase55 import audit as p55audit
from app.research.phase55 import collect as p55collect
from app.research.phase55 import report as p55report
from app.research.phase55 import study as p55study
from app.research.phase55 import universe as p55universe
from app.research.phase55.collect import default_db
from app.research.mcxhist.store import Store

OUT_DIR = "data/research/phase55"
CSV_DIR = "data/research/phase55/csv"


def _login():
    """SmartAPI session from the existing secure runtime configuration."""
    from app.backtest.angel_history import login_smart

    return login_smart()


def _fetcher(smart):
    last = [0.0]

    def fetch(params: dict) -> dict:
        wait = MIN_INTERVAL_SEC - (time.time() - last[0])
        if wait > 0:
            time.sleep(wait)
        last[0] = time.time()
        return smart.getCandleData(params)

    return fetch


def _series_loader():
    from app.research.phase24 import data as p24data

    return p24data.load_series


def _resolutions(names: list[str], *, need_mcx_probe: bool) -> list[dict]:
    master = mcxcontracts.load_master()
    fetch = None
    if need_mcx_probe:
        fetch = _fetcher(_login())
    return p55universe.resolve(master, names, fetch)


def _write(name: str, payload) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as handle:
        if isinstance(payload, str):
            handle.write(payload)
        else:
            json.dump(payload, handle, indent=2, sort_keys=True)
    return path


def _cmd_universe() -> str:
    lines = [f"PHASE 55 UNIVERSE — fingerprint {fingerprint()}", ""]
    for name, (klass, exchange, minutes) in sorted(
        UNIVERSE.items(), key=lambda kv: (kv[1][0], kv[0])
    ):
        shipped = " (five-year file already shipped)" if name in ALREADY_SHIPPED else ""
        lines.append(f"  {name:<12}{klass:<15}{exchange:<5}{minutes:>5} min{shipped}")
    lines += ["", f"{len(UNIVERSE)} instruments declared before any collection."]
    return "\n".join(lines)


def _cmd_resolve(names: list[str], as_json: bool) -> str:
    rows = _resolutions(names, need_mcx_probe=True)
    _write("resolution.json", rows)
    if as_json:
        return json.dumps(rows, indent=2)
    out = ["PHASE 55 TOKEN RESOLUTION", ""]
    for row in rows:
        out.append(
            f"  {row['instrument']:<12}{row['status']:<12}"
            f"{str(row.get('token') or '-'):<10}"
            f"{str(row.get('trading_symbol') or '-'):<22}{row.get('basis', '')}"
        )
    return "\n".join(out)


def _cmd_collect(names: list[str], start: str, end: str | None) -> str:
    plan = p55collect.plan(names)
    if not plan:
        return "nothing to collect: every requested name already ships a five-year file"
    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end) if end else dt.date.today() - dt.timedelta(days=1)
    rows = _resolutions(plan, need_mcx_probe=any(
        UNIVERSE[n][0] == "MCX_FUTURES" for n in plan
    ))
    fetch = _fetcher(_login())
    store = Store(default_db())
    summaries = []
    for res in rows:
        print(f"[phase55] {res['instrument']} {res['status']}", flush=True)
        summary = p55collect.collect_instrument(
            fetch, store, res, start=start_d, end=end_d,
            progress=lambda **kw: print(f"[phase55]   {kw}", flush=True),
        )
        summaries.append(summary)
        print(f"[phase55] {res['instrument']} -> {summary.get('status')}", flush=True)
    _write("collection.json", summaries)
    return json.dumps(summaries, indent=2, default=str)


def _cmd_register(names: list[str]) -> str:
    store = Store(default_db())
    out = []
    for name in p55collect.plan(names):
        written = p55collect.write_csv(store, name, CSV_DIR)
        if written.get("status") != "WRITTEN":
            out.append(written)
            continue
        out.append(p55collect.register(
            name, written["path"], UNIVERSE[name][1]
        ))
    _write("registration.json", out)
    return json.dumps(out, indent=2, default=str)


def _cmd_audit(names: list[str], write: bool) -> str:
    resolution_path = os.path.join(OUT_DIR, "resolution.json")
    resolutions: dict[str, dict] = {}
    if os.path.exists(resolution_path):
        with open(resolution_path, encoding="utf-8") as handle:
            resolutions = {r["instrument"]: r for r in json.load(handle)}
    result = p55audit.run(_series_loader(), names, resolutions)
    text = p55report.render_history(result)
    if write:
        _write("audit.json", result)
        _write("audit_report.txt", text)
    return text


def _cmd_study(names: list[str], families: list[str] | None, write: bool) -> str:
    audit_result = p55audit.run(_series_loader(), names)
    admitted = audit_result["included"]
    if not admitted:
        return p55report.render_history(audit_result) + (
            "\n\nno instrument passed the history audit; nothing was graded"
        )
    result = p55study.run(admitted, families)
    text = p55report.render(audit_result, result)
    if write:
        _write("study.json", result)
        _write("study_report.txt", text)
        _write("preregistration.json", preregistration())
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="phase55",
        description="market-wide instrument research: history, audit, study",
    )
    ap.add_argument("--instrument", action="append",
                    help="restrict to these names, repeatable")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("universe", help="the declared universe; writes nothing")
    r = sub.add_parser("resolve", help="resolve the carrying token per instrument")
    r.add_argument("--json", action="store_true")
    c = sub.add_parser("collect", help="pull 1-minute history; resumable")
    c.add_argument("--start", default=DEFAULT_START)
    c.add_argument("--end", default=None)
    sub.add_parser("register", help="export CSVs and import them for research")
    a = sub.add_parser("audit", help="per-instrument coverage audit and gate")
    a.add_argument("--write", action="store_true")
    s = sub.add_parser("study", help="run the frozen families over admitted names")
    s.add_argument("--family", action="append", default=None)
    s.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    names = [n.upper() for n in (args.instrument or list(UNIVERSE))]
    if args.cmd == "universe":
        print(_cmd_universe())
    elif args.cmd == "resolve":
        print(_cmd_resolve(names, args.json))
    elif args.cmd == "collect":
        print(_cmd_collect(names, args.start, args.end))
    elif args.cmd == "register":
        print(_cmd_register(names))
    elif args.cmd == "audit":
        print(_cmd_audit(names, args.write))
    else:
        print(_cmd_study(names, args.family, args.write))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
