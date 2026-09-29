"""MCX history CLI — probe, inventory, collect, export, audit. Nothing else.

There is no command here that places an order, enables a signal, writes to a
production journal or touches a Phase 41-54 artefact, because there is no code in
this package that could.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

from app.research.mcxhist import ONE_MINUTE, ROOTS, fingerprint, preregistration
from app.research.mcxhist import collector as mcxcollector
from app.research.mcxhist import contracts as mcxcontracts
from app.research.mcxhist import export as mcxexport
from app.research.mcxhist import probe as mcxprobe
from app.research.mcxhist import report as mcxreport
from app.research.mcxhist.store import Store

DEFAULT_START = "2021-08-02"


def _login_fetcher():
    """Log in with the runtime SMARTAPI_* configuration. Never prints a value."""
    from app.backtest import angel_history as ah

    smart = ah.login_smart()
    return mcxcollector.angel_fetcher(smart)


def _cmd_prereg() -> str:
    pre = preregistration()
    lines = [f"MCX HISTORY LAYER — fingerprint {fingerprint()}", f"  {pre['purpose']}"]
    for key in (
        "interval", "chunk_days", "min_interval_sec", "max_attempts",
        "backoff_start_sec", "session_minutes_full", "session_coverage_ok",
        "session_coverage_partial", "jump_flag_multiple",
        "min_sessions_intraday", "min_sessions_daily", "data_class",
        "series_class",
    ):
        lines.append(f"  {key:<24s} {pre[key]}")
    for key in ("roll_policy", "price_adjustment", "look_ahead",
                "live_capture_boundary", "options", "orders"):
        lines.append(f"  {key}: {pre[key]}")
    return "\n".join(lines)


def _cmd_probe(args) -> str:
    fetch = _login_fetcher()
    master = mcxcontracts.load_master()
    out: dict = {"fingerprint": fingerprint(), "roots": {}}
    lines = [f"MCX EXPIRED-CONTRACT PROBE — fingerprint {fingerprint()}"]
    for root, exchange in ROOTS.items():
        listed = mcxcontracts.listed_futures(master, root, exchange)
        result = mcxprobe.probe_root(fetch, root, exchange, listed)
        out["roots"][root] = result
        lines.append(
            f"\n{root} ({exchange}) — {result['contracts_probed']} contracts "
            f"listed, {result['contracts_with_pre_listing_history']} answer "
            f"pre-listing windows -> {result['verdict']}"
        )
        for contract in result["per_contract"]:
            lines.append(
                f"  {contract['trading_symbol']:<20s} token "
                f"{contract['token']:<8s} expiry {contract['expiry']}  "
                f"windows with rows {contract['windows_with_rows']}/"
                f"{contract['windows_requested']}"
            )
            for window in contract["windows"]:
                lines.append(
                    f"    {window['window_from']}..{window['window_to']}  "
                    f"{window['response']:<7s} rows {window['rows']:>6,d}  "
                    f"sessions {window['sessions']:>2d}  med/ses "
                    f"{window['median_bars_per_session']:>4d}  close "
                    f"{window['close_min']}..{window['close_max']}  "
                    f"dup {window['duplicate_ts']}  {window['timestamp_quality']}"
                    + (f"  {window['error']}" if window["error"] else "")
                )
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    return "\n".join(lines)


def _cmd_collect(args) -> str:
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    fetch = _login_fetcher()
    master = mcxcontracts.load_master()
    store = Store(args.db)
    lines = [f"MCX COLLECTION — fingerprint {fingerprint()}  {start} .. {end}"]
    roots = [r.upper() for r in (args.roots or list(ROOTS))]
    for root in roots:
        exchange = ROOTS.get(root, "MCX")
        listed = mcxcontracts.listed_futures(master, root, exchange)
        probe_result = mcxprobe.probe_root(
            fetch, root, exchange, listed,
            windows=(mcxprobe.DEFAULT_PROBE_WINDOWS[0],),
        )
        reach = mcxprobe.reachability(probe_result)
        inv = mcxcontracts.inventory(master, root, exchange, start, end, reach)
        chosen = mcxcontracts.history_token(inv)
        if chosen is None:
            lines.append(
                f"{root}: no single token carries pre-listing history "
                f"({inv['contracts_reachable']} reachable) — nothing collected"
            )
            continue
        lines.append(
            f"{root}: collecting {chosen['trading_symbol']} token "
            f"{chosen['token']}  ({inv['contracts_listed']} listed, "
            f"{inv['contracts_unresolvable']} expired tokens unresolvable)"
        )
        summary = mcxcollector.collect(
            fetch,
            store,
            root=root,
            exchange=exchange,
            token=chosen["token"],
            trading_symbol=chosen["trading_symbol"],
            expiry=chosen["expiry"],
            start=start,
            end=end,
            interval=args.interval,
            progress=(lambda text: print(text, flush=True)) if args.progress else None,
        )
        lines.append(
            f"  windows ok {summary['windows_ok']}  empty "
            f"{summary['windows_empty']}  failed {summary['windows_failed']}  "
            f"skipped {summary['windows_skipped_already_stored']}  "
            f"bars {summary['bars_stored_total']:,}  rate-limit waits "
            f"{summary['rate_limit_waits']}"
        )
        for failure in store.failures(root, chosen["token"], args.interval)[:10]:
            lines.append(
                f"  {failure['status']} {failure['chunk_from']}.."
                f"{failure['chunk_to']}: {failure['reason']}"
            )
    store.close()
    return "\n".join(lines)


def _cmd_export(args) -> str:
    store = Store(args.db)
    lines = []
    for root in [r.upper() for r in (args.roots or list(ROOTS))]:
        result = mcxexport.export_root(store, root, token=args.token)
        lines.append(f"{root}: {result['status']}  {result.get('written', 0):,} bars "
                     f"-> {result.get('jsonl', '-')}")
    store.close()
    return "\n".join(lines)


def _cmd_audit(args) -> str:
    store = Store(args.db)
    bundle = mcxreport.audit_all(store)
    inventories: dict = {}
    if args.with_inventory:
        master = mcxcontracts.load_master()
        start = dt.date.fromisoformat(args.start)
        end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
        for root, exchange in ROOTS.items():
            tokens = store.tokens(root)
            reach = {t: True for t in tokens}
            inventories[root] = mcxcontracts.inventory(
                master, root, exchange, start, end, reach
            )
    probes: dict = {}
    if args.probe_json:
        with open(args.probe_json, encoding="utf-8") as handle:
            probes = json.load(handle)
    verdict = mcxreport.stage_verdict(bundle, inventories)
    written = []
    if args.write:
        written = mcxreport.write_artefacts(
            bundle, verdict, inventories, probes, args.out
        )
    store.close()
    text = [
        mcxreport.render_table(bundle),
        "",
        *[mcxreport.render_detail(row) for row in bundle["instruments"]],
        "",
        mcxreport.render_verdict(verdict),
    ]
    if written:
        text.append("\nartefacts:\n" + "\n".join(f"  {p}" for p in written))
    return "\n\n".join(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mcxhist", description="MCX futures history collection and audit"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("prereg", help="print the declaration and fingerprint")

    probe = sub.add_parser("probe", help="probe expired-contract reachability")
    probe.add_argument("--json", action="store_true")

    collect = sub.add_parser("collect", help="collect history for GOLD/SILVER")
    collect.add_argument("--start", default=DEFAULT_START)
    collect.add_argument("--end", default=None)
    collect.add_argument("--interval", default=ONE_MINUTE)
    collect.add_argument("--roots", nargs="*", default=None)
    collect.add_argument("--db", default=None)
    collect.add_argument("--progress", action="store_true")

    export = sub.add_parser("export", help="write the 1-minute file + provenance")
    export.add_argument("--roots", nargs="*", default=None)
    export.add_argument("--token", default=None)
    export.add_argument("--db", default=None)

    audit = sub.add_parser("audit", help="four-instrument audit and stage verdict")
    audit.add_argument("--db", default=None)
    audit.add_argument("--start", default=DEFAULT_START)
    audit.add_argument("--end", default=None)
    audit.add_argument("--with-inventory", action="store_true")
    audit.add_argument("--probe-json", default=None, help="probe --json output to embed")
    audit.add_argument("--write", action="store_true")
    audit.add_argument("--out", default="data/research/mcxhist")

    args = parser.parse_args(argv)
    if args.command == "prereg":
        print(_cmd_prereg())
    elif args.command == "probe":
        print(_cmd_probe(args))
    elif args.command == "collect":
        print(_cmd_collect(args))
    elif args.command == "export":
        print(_cmd_export(args))
    elif args.command == "audit":
        print(_cmd_audit(args))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
