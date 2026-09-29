"""Readable renderings of the inspect, validate and coverage payloads.

Written for an operator deciding whether to trust a file, so the layout puts
the refusal reason and the execution caveat where they cannot be skimmed past.
"""
from __future__ import annotations

from app.research.historical_import import (
    ALREADY_IMPORTED,
    BID_ASK_ABSENT,
    EXECUTION_UNMEASURED,
    HISTORICAL_CANDLE_DATA,
    IMPORTED,
    IMPORT_REJECTED,
)


def _line(k: str, v) -> str:
    return f"  {k:<22}: {v}"


def inspect_text(rep: dict) -> str:
    out = [
        "",
        f"HISTORICAL CSV INSPECT — {rep.get('file')}",
        _line("classification", HISTORICAL_CANDLE_DATA + "  (NOT an executable book)"),
    ]
    if rep.get("reject_reason"):
        out += [
            _line("verdict", "WOULD BE REJECTED"),
            _line("reason", rep["reject_reason"]),
        ]
        if rep.get("header"):
            out.append(_line("header", ", ".join(rep["header"][:12])))
        if rep.get("mapping"):
            out.append(_line("mapped so far", _fmt_map(rep["mapping"])))
        out.append("")
        return "\n".join(out)

    sess = rep.get("sessions") or {}
    out += [
        _line("verdict", "WOULD IMPORT"),
        _line("instrument", rep.get("instrument")),
        _line("timeframe", f"{rep.get('timeframe')}m"),
        _line("timezone", (rep.get("timezone") or {}).get("established_by")),
        _line("rows", f"{rep.get('rows'):,}"),
        _line("span", f"{sess.get('first')} → {sess.get('last')} IST"),
        _line("sessions", sess.get("session_count")),
        _line("missing bars", f"{sess.get('missing_bars')}  "
                              f"({sess.get('gap_pct')}% within session spans)"),
        _line("not graded", f"{sess.get('not_graded_weekday_count')} absent weekday(s)"
                            " — no calendar in a candle file, so not called missing"),
        _line("ohlc quality", (rep.get("quality") or {}).get("status")),
        _line("contract detail", rep.get("contract_detail_status")),
        _line("option detail", rep.get("option_detail_status")),
        _line("bid/ask", rep.get("bid_ask_status")),
        _line("execution", rep.get("execution_note")),
        _line("suitability", rep.get("research_eligibility_if_imported")),
        _line("mapping", _fmt_map(rep.get("mapping") or {})),
    ]
    anomalies = (rep.get("quality") or {}).get("anomalies") or {}
    if anomalies:
        out.append("  anomalies (counted, never repaired):")
        for kind, n in sorted(anomalies.items(), key=lambda kv: -kv[1]):
            line = (rep["quality"].get("first_examples") or {}).get(kind)
            where = f"  first at source line {line}" if line else ""
            out.append(f"      {n:>8}  {kind}{where}")
    missing = rep.get("missing") or {}
    if missing.get("unparsed_rows"):
        out.append(_line("unparsed rows",
                         f"{missing['unparsed_rows']} of {missing['source_rows']}"
                         " — dropped, not interpolated"))
    out.append("")
    return "\n".join(out)


def _fmt_map(mapping: dict) -> str:
    return ", ".join(f"{f}<-{c}" for f, c in sorted(mapping.items())) or "none"


def import_text(res: dict) -> str:
    status = res.get("status")
    if status == ALREADY_IMPORTED:
        return (f"\n{ALREADY_IMPORTED} — {res.get('dataset_id')}\n"
                f"  {res.get('reason')}\n"
                f"  first imported at {res.get('imported_at')}\n")
    if status == IMPORT_REJECTED:
        # Say which of the two happened. The audit either carries this refusal
        # or it does not, and a message that claims a line the coverage report
        # cannot show is worse than no message.
        recorded = ("the refusal is on the record in the registry; no rows, "
                    "no dataset" if res.get("recorded") else
                    "nothing was written at all — no bytes were read, so there "
                    "is nothing to record about this file")
        return (f"\n{IMPORT_REJECTED} — {res.get('file')}\n"
                f"  {res.get('reason')}\n"
                f"  {recorded}\n")
    man = res.get("manifest") or {}
    span = man.get("span", {})
    return "\n".join([
        "",
        f"{IMPORTED} — {res.get('dataset_id')}",
        _line("rows", f"{res.get('rows'):,}"),
        _line("sessions", res.get("sessions")),
        _line("span", f"{span.get('start')} → {span.get('end')} IST"),
        _line("fingerprint", (res.get("fingerprint") or "")[:16]),
        _line("bid/ask", res.get("bid_ask_status")),
        _line("suitability", res.get("research_eligibility")),
        _line("promotion ceiling", "HISTORICAL_LEAD — live executable "
                                   "validation is still required"),
        "",
    ])


def validate_text(res: dict) -> str:
    out = [
        "",
        f"VALIDATE — {res.get('dataset_id')}",
        _line("status", res.get("status")),
        _line("rows", res.get("rows")),
        _line("source file", res.get("source_state")),
        _line("fingerprint", "MATCHES THE MANIFEST"
              if res.get("fingerprint_recomputed") == res.get("fingerprint_in_manifest")
              else "DOES NOT MATCH THE MANIFEST"),
        _line("bid/ask", res.get("bid_ask_status")),
        _line("execution", res.get("execution_note")),
    ]
    for f in res.get("findings") or []:
        out.append(f"      FINDING  {f}")
    out.append("")
    return "\n".join(out)


def list_text(rows: list[dict]) -> str:
    if not rows:
        return ("\nNo imported datasets. Nothing here invents one — supply a "
                "licensed CSV and run `import`.\n")
    out = ["", f"HISTORICAL DATASETS — {len(rows)}", ""]
    out.append(f"  {'INSTRUMENT':<12}{'TF':>4}  {'START':<17}{'END':<17}"
               f"{'SESS':>6}{'BARS':>10}  {'QUALITY':<22}{'SUITABILITY'}")
    for r in rows:
        if r.get("status") != "IMPORTED":
            out.append(f"  {str(r.get('instrument') or '?'):<12}"
                       f"   -  REJECTED  {r.get('reject_reason')}")
            continue
        out.append(
            f"  {str(r['instrument']):<12}{str(r['timeframe'])+'m':>4}  "
            f"{str(r['start'])[:17]:<17}{str(r['end'])[:17]:<17}"
            f"{int(r['sessions'] or 0):>6}{int(r['bars'] or 0):>10}  "
            f"{str(r['ohlc_quality']):<22}{r['research_suitability']}"
        )
    out += ["", "  " + HISTORICAL_CANDLE_DATA + " — NOT EXECUTABLE QUOTES", ""]
    return "\n".join(out)


def coverage_text(rep: dict) -> str:
    t = rep.get("totals", {})
    out = [
        "",
        "HISTORICAL COVERAGE AUDIT",
        _line("datasets", f"{t.get('datasets')} over {t.get('instruments')} instrument(s)"),
        _line("rejected imports", t.get("rejected_imports")),
        _line("bars", f"{int(t.get('bars') or 0):,}"),
        _line("sessions", t.get("sessions")),
        "",
    ]
    out.append(list_text([r for r in rep.get("datasets") or []]).strip("\n"))
    q = rep.get("questions", {})
    out += ["", "  THE EIGHT QUESTIONS", ""]
    for key in sorted(q):
        if key.startswith("8_"):
            continue
        val = q[key]
        out.append(f"    {key}")
        out.append(f"        {', '.join(str(v) for v in val)}")
    out += ["", "    8_which_research_gaps_are_now_closed"]
    for g in q.get("8_which_research_gaps_are_now_closed") or []:
        legs = ", ".join(f"{k}={v}" for k, v in sorted(g["sessions_per_leg"].items()))
        out.append(f"        {g['gap']}")
        out.append(f"            {g['state']}")
        out.append(f"            sessions per leg: {legs}")
        out.append(f"            execution: {g['execution']}")
    out += ["", "  STANDING LIMITS"]
    for lim in rep.get("standing_limits") or []:
        out.append(f"    - {lim}")
    bidless = [r for r in rep.get("datasets") or []
               if r.get("bid_ask") == BID_ASK_ABSENT]
    if bidless:
        out.append(f"    - {len(bidless)} dataset(s) are OHLC only: every "
                   f"FUTURES vs CE vs PE answer on them is {EXECUTION_UNMEASURED}")
    out.append("")
    return "\n".join(out)
