"""Phase 56 CLI — stage 1.

    python -m app.research.phase56.cli prereg [--write]
    python -m app.research.phase56.cli universe [--json]
    python -m app.research.phase56.cli audit [--write]
    python -m app.research.phase56.cli costs

``universe`` needs the scrip master only. ``audit`` is the only command that
talks to the historical endpoint: eleven spaced requests, no collection, nothing
stored but the artefacts. There is deliberately no ``collect`` and no ``study``
command in stage 1 — §36 stops the phase before discovery, and a command that
could run a study would make the gate advisory.

Credentials are read from the environment inside ``_login`` and nowhere else; no
value is printed or written to any artefact.
"""
from __future__ import annotations

import argparse
import json
import os
import time

from app.research.phase56 import (
    MIN_INTERVAL_SEC,
    RATE_LIMIT_ATTEMPTS,
    RATE_LIMIT_BACKOFF_SEC,
    SCRIP_MASTER_URL,
    VERSION,
    fingerprint,
    preregistration,
)
from app.research.phase56 import audit as p56audit
from app.research.phase56 import costs as p56costs
from app.research.phase56 import probe as p56probe
from app.research.phase56 import report as p56report
from app.research.phase56 import universe as p56universe

OUT_DIR = "data/research/phase56"


def _load_master() -> list[dict]:
    import httpx

    return httpx.get(SCRIP_MASTER_URL, timeout=180.0).json()


def _login():
    from app.backtest.angel_history import login_smart

    return login_smart()


def _fetcher(smart, *, attempts: int = RATE_LIMIT_ATTEMPTS):
    """Spaced ONE_DAY fetcher with backoff on the provider's rate limiter.

    A throttled request is retried rather than returned, because a probe that
    reports ABSENT on a rate limit is the single worst failure mode this stage
    has: it would label a present dataset missing. Only when every attempt is
    throttled does the error reach the probe, which then records INCONCLUSIVE.
    """
    last = [0.0]

    def once(params: dict) -> list[list]:
        wait = MIN_INTERVAL_SEC - (time.time() - last[0])
        if wait > 0:
            time.sleep(wait)
        last[0] = time.time()
        response = smart.getCandleData(params)
        if not response or not response.get("status"):
            raise RuntimeError(str((response or {}).get("message", "no response")))
        return response.get("data") or []

    def fetch(params: dict) -> list[list]:
        delay = RATE_LIMIT_BACKOFF_SEC
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return once(params)
            except Exception as exc:  # provider transport or throttle
                last_error = exc
                if not p56probe.is_rate_limited(str(exc)) or attempt == attempts - 1:
                    raise
                time.sleep(delay)
                delay *= 2
        raise last_error  # unreachable; kept so the type is honest

    return fetch


def _write(name: str, body: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "w") as handle:
        handle.write(body)
    return path


def _cmd_prereg(args) -> str:
    doc = {"fingerprint": fingerprint(), **preregistration()}
    body = json.dumps(doc, indent=2)
    if args.write:
        return f"wrote {_write('preregistration.json', body)}"
    return body


def _cmd_universe(args) -> str:
    master = _load_master()
    rows = p56universe.equity_rows(master)
    payload = {
        "version": VERSION,
        "securities": len(rows),
        "sample": [r["symbol"] for r in rows[:15]],
        "other_nse_series": p56universe.other_nse_series(master),
        "status_assignability": p56universe.status_assignability(),
        "field_availability": p56universe.field_availability(),
        "membership_sources": p56universe.membership_sources(master),
        "universes": p56universe.universes(master),
    }
    if args.json:
        return json.dumps(payload, indent=2)
    lines = [f"NSE '-EQ' securities in the present-day master: {len(rows)}"]
    lines.append("")
    lines.append("§2 STATUS ASSIGNABILITY")
    for row in payload["status_assignability"]:
        lines.append(f"  {row['status']:<22}{'YES' if row['assignable'] else 'NO':<5}{row['reason']}")
    lines.append("")
    lines.append("§2/§6 FIELD AVAILABILITY")
    for row in payload["field_availability"]:
        lines.append(f"  {row['field']:<26}{'YES' if row['available'] else 'NO'}")
    lines.append("")
    lines.append("§3 MEMBERSHIP SOURCE HIERARCHY")
    for row in payload["membership_sources"]:
        lines.append(f"  {row['rank']}. {row['source']:<40}dated={row['dated_membership_available']}")
        lines.append(f"     {row['reason']}")
    return "\n".join(lines)


def _cmd_audit(args) -> str:
    master = _load_master()
    tokens = {row["symbol"]: row["token"] for row in p56universe.equity_rows(master)}
    master_symbols = set(tokens)
    fetch = _fetcher(_login())

    coverage = p56probe.probe_coverage(fetch, tokens)
    actions = p56probe.probe_corporate_actions(fetch, tokens)
    vanished = p56probe.probe_vanished(fetch, master_symbols, tokens=tokens)
    assessment = p56audit.assess(master, coverage, actions, vanished)
    text = p56report.render(assessment)
    if args.write:
        _write("audit.json", json.dumps(assessment, indent=2, default=str))
        _write("audit_report.txt", text)
        _write("preregistration.json", json.dumps({"fingerprint": fingerprint(), **preregistration()}, indent=2))
        return text + f"\nwrote {OUT_DIR}/audit.json, audit_report.txt, preregistration.json"
    return text


def _cmd_costs(args) -> str:
    example = p56costs.breakeven_example()
    lines = ["§8 DATED CASH-EQUITY DELIVERY COST SCHEDULE (all MODELLED_COST)"]
    for row in p56costs.schedule():
        lines.append(f"  {json.dumps(row)}")
    lines.append("")
    lines.append("ROUND TRIP AT A FLAT PRICE (₹1000 x 100, 2026 regime)")
    for key, value in example.items():
        lines.append(f"  {key:<32}{value}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phase56")
    sub = parser.add_subparsers(dest="cmd", required=True)

    prereg = sub.add_parser("prereg", help="frozen declaration + fingerprint")
    prereg.add_argument("--write", action="store_true")

    universe = sub.add_parser("universe", help="what the master supplies (no history calls)")
    universe.add_argument("--json", action="store_true")

    audit = sub.add_parser("audit", help="run the §36 probes and the gate")
    audit.add_argument("--write", action="store_true")

    sub.add_parser("costs", help="print the dated cost schedule and the hurdle")

    args = parser.parse_args(argv)
    handler = {
        "prereg": _cmd_prereg,
        "universe": _cmd_universe,
        "audit": _cmd_audit,
        "costs": _cmd_costs,
    }[args.cmd]
    print(handler(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
