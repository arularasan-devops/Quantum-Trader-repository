"""The audit artefacts: four-instrument table, per-root detail, stage verdict.

The stage deliverable is not a strategy and not an expectancy. It is a pair of
yes/no answers about whether GOLD and SILVER history exists in usable form, with
the counts that justify each answer and the caveats that survive them. This
module produces exactly that, as text for reading and JSON for keeping.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import numpy as np

from app.research.mcxhist import (
    AUDIT_INSTRUMENTS,
    DATA_CLASS,
    SERIES_CLASS,
    VALID_HISTORY_NO,
    VALID_HISTORY_YES,
    fingerprint,
    preregistration,
)
from app.research.mcxhist import audit as mcxaudit
from app.research.mcxhist.store import Store

#: Existing instruments are audited from the files the previous phases ran on,
#: not re-collected, so the table compares like with like.
EXISTING_FULL_MINUTES = {"NIFTY": 375, "CRUDEOIL": 870}
MCX_FULL_MINUTES = 870


def _existing_series(instrument: str):
    from app.research.phase24 import data as p24data

    return p24data.load_series(instrument)


def audit_all(store: Store | None = None) -> dict:
    """Audit all four instruments: two from disk, two from the MCX store."""
    rows: list[dict] = []
    for instrument in AUDIT_INSTRUMENTS:
        if instrument in EXISTING_FULL_MINUTES:
            series = _existing_series(instrument)
            if series is None or len(series) == 0:
                rows.append(_absent(instrument, "no five-year file on disk"))
                continue
            rows.append(
                mcxaudit.audit_series(
                    instrument,
                    series.ts,
                    series.open,
                    series.high,
                    series.low,
                    series.close,
                    full_minutes=EXISTING_FULL_MINUTES[instrument],
                    source="data/backtest/<INST>_ONE_MINUTE.jsonl (pre-existing)",
                )
            )
            continue

        if store is None:
            rows.append(_absent(instrument, "MCX store not opened"))
            continue
        tokens = store.tokens(instrument)
        if not tokens:
            rows.append(_absent(instrument, "no bars collected"))
            continue
        token = max(tokens, key=lambda t: store.bar_count(instrument, t))
        bars = store.bars(instrument, token)
        array = np.asarray(bars, dtype=np.float64)
        rows.append(
            mcxaudit.audit_series(
                instrument,
                array[:, 0].astype(np.int64),
                array[:, 1],
                array[:, 2],
                array[:, 3],
                array[:, 4],
                full_minutes=MCX_FULL_MINUTES,
                source="angel_smartapi_getCandleData -> data/mcxhist.db",
                tokens=[token],
            )
        )

    return {
        "fingerprint": fingerprint(),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "data_class": DATA_CLASS,
        "series_class": SERIES_CLASS,
        "preregistration": preregistration(),
        "instruments": rows,
    }


def _absent(instrument: str, reason: str) -> dict:
    return {
        "instrument": instrument,
        "source": "",
        "tokens": [],
        "history_start": None,
        "history_end": None,
        "bars": 0,
        "sessions": 0,
        "graded_sessions": 0,
        "median_bars_per_session": 0,
        "coverage_pct": 0.0,
        "sessions_ok": 0,
        "sessions_partial": 0,
        "sessions_not_graded": 0,
        "duplicate_timestamps": 0,
        "monotonic": True,
        "impossible_bars": 0,
        "missing_weekday_sessions": 0,
        "longest_absent_run": 0,
        "absent_runs": [],
        "by_year": [],
        "boundary_scan": {"boundaries": 0, "flagged": 0},
        "expiry_cycle_scan": {"reading": "NO_FLAGS"},
        "timeframes": {},
        "data_quality": mcxaudit.QUALITY_INSUFFICIENT,
        "valid_history": VALID_HISTORY_NO,
        "absent_reason": reason,
    }


def stage_verdict(bundle: dict, inventories: dict | None = None) -> dict:
    """The §15 deliverable values, straight off the audit."""
    by_name = {row["instrument"]: row for row in bundle["instruments"]}
    inventories = inventories or {}
    out: dict = {}
    for root in ("GOLD", "SILVER"):
        row = by_name.get(root, {})
        inv = inventories.get(root, {})
        out[root] = {
            "valid_history": row.get("valid_history", VALID_HISTORY_NO),
            "contracts_collected": len(row.get("tokens") or []),
            "contracts_listed": inv.get("contracts_listed"),
            "contracts_expected_for_window": inv.get("contracts_expected_for_window"),
            "contracts_unresolvable": inv.get("contracts_unresolvable"),
            "identity_resolved": bool(inv),
            "sessions": row.get("sessions", 0),
            "graded_sessions": row.get("graded_sessions", 0),
            "one_minute_bars": row.get("bars", 0),
            "date_range": (
                f"{row.get('history_start')} -> {row.get('history_end')}"
                if row.get("history_start")
                else "NONE"
            ),
            "rollover_count": 0,
            "rollover_basis": (
                "no roll was constructed: contract identity is not exposed by "
                "the source, so the count is zero by construction, not by "
                "absence of rolls in the underlying market"
            ),
            "data_quality": row.get("data_quality"),
        }
    qualities = [out[r]["data_quality"] for r in ("GOLD", "SILVER")]
    out["data_quality_status"] = (
        mcxaudit.QUALITY_SUFFICIENT
        if all(q == mcxaudit.QUALITY_SUFFICIENT for q in qualities)
        else mcxaudit.QUALITY_PARTIAL
        if any(q in (mcxaudit.QUALITY_SUFFICIENT, mcxaudit.QUALITY_PARTIAL) for q in qualities)
        else mcxaudit.QUALITY_INSUFFICIENT
    )
    out["phase55_gate"] = (
        "OPEN"
        if all(out[r]["valid_history"] == VALID_HISTORY_YES for r in ("GOLD", "SILVER"))
        else "CLOSED"
    )
    return out


# --------------------------------------------------------------------- text


def render_table(bundle: dict) -> str:
    lines = [
        f"MCX HISTORY AUDIT — fingerprint {bundle['fingerprint']}   "
        f"{bundle['generated_at']}",
        f"  data class {bundle['data_class']}   series class {bundle['series_class']}",
        "",
        f"{'INSTRUMENT':<10s} {'HISTORY_START':<14s} {'HISTORY_END':<14s} "
        f"{'SESSIONS':>9s} {'1M_BARS':>10s} {'COV%':>7s} {'MED/SES':>8s} "
        f"{'TOKENS':>10s} {'ROLLS':>6s}  DATA_QUALITY",
    ]
    for row in bundle["instruments"]:
        lines.append(
            f"{row['instrument']:<10s} {str(row['history_start'] or '-'):<14s} "
            f"{str(row['history_end'] or '-'):<14s} {row['sessions']:>9,d} "
            f"{row['bars']:>10,d} {row['coverage_pct']:>7.1f} "
            f"{row['median_bars_per_session']:>8,d} "
            f"{len(row['tokens'] or []) or '-':>10} {0:>6d}  {row['data_quality']}"
        )
    lines += [
        "",
        "  TOKENS is provider series counted, not exchange contracts: the "
        "source exposes no expired-contract identity, so ROLLS is 0 by "
        "construction rather than measured as absent.",
    ]
    return "\n".join(lines)


def render_detail(row: dict) -> str:
    lines = [
        f"{row['instrument']} — {row['source'] or 'ABSENT'}",
        f"  bars {row['bars']:,}   sessions {row['sessions']:,}   "
        f"graded {row['graded_sessions']:,}   coverage {row['coverage_pct']}%",
        f"  sessions OK {row['sessions_ok']:,}   partial {row['sessions_partial']:,}"
        f"   NOT_GRADED {row['sessions_not_graded']:,}",
        f"  duplicate timestamps {row['duplicate_timestamps']}   monotonic "
        f"{row['monotonic']}   impossible bars {row['impossible_bars']}",
        f"  missing weekday sessions {row['missing_weekday_sessions']}   "
        f"longest absent run {row['longest_absent_run']}",
    ]
    for run in (row.get("absent_runs") or [])[:5]:
        lines.append(
            f"    absent {run['from']} -> {run['to']} ({run['sessions']} "
            "weekday sessions; holiday or hole, undistinguishable)"
        )
    for year in row.get("by_year") or []:
        lines.append(
            f"    {year['year']}  sessions {year['sessions']:>4}  bars "
            f"{year['bars']:>8,}  OK {year['ok']:>4}  partial "
            f"{year['partial']:>4}  not graded {year['not_graded']:>4}"
        )
    scan = row.get("boundary_scan") or {}
    if scan.get("boundaries"):
        lines.append(
            f"  overnight boundaries {scan['boundaries']:,}   flagged "
            f"{scan['flagged']}   median |overnight| "
            f"{scan.get('median_abs_overnight_pct')}%   threshold "
            f"{scan.get('threshold_pct')}%"
        )
        cycle = row.get("expiry_cycle_scan") or {}
        lines.append(
            f"  expiry-week clustering {cycle.get('flagged_in_expiry_week')}/"
            f"{cycle.get('flagged')} -> {cycle.get('reading')}"
        )
        for flag in (scan.get("flags") or [])[:5]:
            lines.append(
                f"    {flag['session_end']} -> {flag['session_start']}: "
                f"{flag['last_close']:.1f} -> {flag['next_open']:.1f} "
                f"({flag['overnight_pct']:+.2f}%)"
            )
    for timeframe, state in (row.get("timeframes") or {}).items():
        lines.append(
            f"  {timeframe:<9s} {state['status']:<20s} "
            f"{state['graded_sessions']:,} / {state['required_sessions']:,} sessions"
        )
    if row.get("absent_reason"):
        lines.append(f"  ABSENT: {row['absent_reason']}")
    return "\n".join(lines)


def render_verdict(verdict: dict) -> str:
    lines = ["STAGE DELIVERABLE"]
    for root in ("GOLD", "SILVER"):
        row = verdict[root]
        lines.append(f"  VALID_{root}_HISTORY = {row['valid_history']}")
    lines.append("")
    for root in ("GOLD", "SILVER"):
        row = verdict[root]
        identity = (
            f"(listed {row['contracts_listed']}, a genuine per-contract history "
            f"would need ~{row['contracts_expected_for_window']}, unresolvable "
            f"{row['contracts_unresolvable']})"
            if row.get("identity_resolved")
            else "(token-level series; contract identity was not re-resolved "
            "in this run — see the probe artefact)"
        )
        lines += [
            f"  {root}_CONTRACTS_COLLECTED   {row['contracts_collected']} {identity}",
            f"  {root}_SESSIONS             {row['sessions']:,} "
            f"(graded {row['graded_sessions']:,})",
            f"  {root}_1M_BARS             {row['one_minute_bars']:,}",
            f"  {root}_DATE_RANGE          {row['date_range']}",
            f"  ROLLOVER_COUNT_{root}      {row['rollover_count']}  "
            f"[{row['rollover_basis']}]",
        ]
    lines += [
        "",
        f"  DATA_QUALITY_STATUS     {verdict['data_quality_status']}",
        f"  PHASE_55_GATE           {verdict['phase55_gate']}",
    ]
    return "\n".join(lines)


def write_artefacts(
    bundle: dict,
    verdict: dict,
    inventories: dict,
    probes: dict,
    out_dir: str = "data/research/mcxhist",
) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    written: list[str] = []

    def _json(name: str, payload) -> None:
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, default=str)
        written.append(path)

    def _text(name: str, payload: str) -> None:
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
        written.append(path)

    _json("preregistration.json", {"fingerprint": fingerprint(), **preregistration()})
    _json("probe.json", probes)
    _json("contract_inventory.json", inventories)
    _json("audit.json", bundle)
    _json("stage_verdict.json", verdict)

    text = [
        render_table(bundle),
        "",
        *[render_detail(row) for row in bundle["instruments"]],
        "",
        render_verdict(verdict),
    ]
    _text("audit_report.txt", "\n\n".join(t for t in text if t is not None))
    return written


def valid_history(bundle: dict, instrument: str) -> str:
    for row in bundle["instruments"]:
        if row["instrument"] == instrument:
            return row["valid_history"]
    return VALID_HISTORY_NO
