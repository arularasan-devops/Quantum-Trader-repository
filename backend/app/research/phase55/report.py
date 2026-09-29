"""Phase 55 text artefacts: the audit table, the event-count table, the verdict.

Three tables, in the order the argument has to be made:

1. **history** — what data exists per instrument and whether it was admitted.
   An excluded instrument keeps its row and its reason;
2. **events** — trades and event families per instrument per mechanism. This is
   the table that answers whether four prior zeros were a sample problem;
3. **verdict** — the promotion line, with the universe-wide correction
   denominator printed beside it so nobody has to take the number on trust.
"""
from __future__ import annotations

from app.research.phase55 import (
    GATE_INCLUDED,
    MIN_BARS,
    MIN_GRADED_SESSIONS,
    fingerprint,
)
from app.research.phase55.study import PROMOTED


def _fmt(value: int | float | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{value:,}"


def render_history(audit: dict) -> str:
    lines = [
        "PHASE 55 — HISTORY AUDIT",
        f"fingerprint {fingerprint()}",
        "",
        f"floors: {MIN_GRADED_SESSIONS:,} graded sessions, {MIN_BARS:,} bars",
        "",
        f"{'INSTRUMENT':<12}{'CLASS':<15}{'START':<12}{'END':<12}"
        f"{'SESSIONS':>9}{'GRADED':>8}{'1M_BARS':>11}{'COV%':>7}{'MED/SES':>8}  GATE",
    ]
    for row in audit.get("instruments", []):
        lines.append(
            f"{row['instrument']:<12}{row.get('instrument_class', ''):<15}"
            f"{(row.get('history_start') or '-'):<12}"
            f"{(row.get('history_end') or '-'):<12}"
            f"{_fmt(row.get('sessions') or 0):>9}"
            f"{_fmt(row.get('graded_sessions') or 0):>8}"
            f"{_fmt(row.get('bars', 0)):>11}"
            f"{_fmt(row.get('coverage_pct')):>7}"
            f"{_fmt(row.get('median_bars_per_session')):>8}  {row['gate']}"
        )
    excluded = audit.get("excluded") or []
    if excluded:
        lines += ["", "EXCLUDED — reason on the record, not replaced by another name:"]
        lines += [
            f"  {e['instrument']:<12}{e['gate']:<32}{e['reason']}" for e in excluded
        ]
    lines += [
        "",
        f"ADMITTED {len(audit.get('included') or [])} of "
        f"{len(audit.get('requested') or [])} requested instruments",
        "",
        "CLASSIFICATION",
        "  cash index and cash equity series have no contract and no roll.",
        "  MCX futures are PROVIDER_CONTINUOUS_UNVERIFIED_ROLL: one provider",
        "  token per root, no expired contract identity, roll not verifiable",
        "  from this data. No series here is executable bid/ask history.",
    ]
    return "\n".join(lines)


def render_events(study: dict) -> str:
    lines = [
        "PHASE 55 — EVENT COUNTS PER INSTRUMENT",
        "",
        "The A-vs-B evidence: whether the prior zeros were weak mechanisms or",
        "too few events to grade. Read the trade columns before the verdicts.",
        "",
        f"{'FAMILY':<24}{'INSTRUMENT':<12}{'PARAMS':>7}{'FAMILIES':>9}"
        f"{'MED_TRD':>8}{'MAX_TRD':>8}{'DISC':>6}{'VAL':>5}{'HOLD':>6}{'ROBUST':>8}",
    ]
    for row in study.get("event_counts", []):
        lines.append(
            f"{row['family']:<24}{row['instrument']:<12}"
            f"{_fmt(row['parameterizations']):>7}{_fmt(row['event_families']):>9}"
            f"{_fmt(row['median_trades']):>8}{_fmt(row['max_trades']):>8}"
            f"{_fmt(row['discovery_leads']):>6}{_fmt(row['validation_positive']):>5}"
            f"{_fmt(row['holdout_positive']):>6}{_fmt(row['robust_own_phase']):>8}"
        )
    return "\n".join(lines)


def render_verdict(study: dict) -> str:
    totals = study.get("totals") or {}
    lines = [
        "PHASE 55 — VERDICT",
        f"fingerprint {study.get('fingerprint')}",
        "",
    ]
    for key in (
        "INSTRUMENTS_REQUESTED", "INSTRUMENTS_GRADED", "PARAMETERIZATIONS",
        "HOLDOUT_POSITIVE", "ROBUST_IN_OWN_PHASE", "PROMOTED",
    ):
        lines.append(f"{key:<28}{_fmt(totals.get(key)):>10}")
    for fam in study.get("families", []):
        lines += [
            "",
            f"{fam['family']}",
            f"  universe-wide FDR denominator   {_fmt(fam['fdr_denominator_universe'])}",
            f"  rows surviving that correction  {_fmt(fam['totals'].get('UNIVERSE_FDR_PASS'))}",
            f"  instruments graded              {len(fam['instruments_graded'])}",
            f"  holdout-positive instruments    {', '.join(fam['holdout_positive_instruments']) or 'none'}",
            f"  promoted                        {_fmt(fam['totals'].get('PROMOTED'))}",
        ]
        for row in fam["promoted"]:
            lines.append(
                f"    {row['candidate_id']}  {row.get('human_readable_rule', '')}"
            )
    if not totals.get("PROMOTED"):
        lines += [
            "",
            "PROMOTED = 0. No row is substituted for a candidate: a best-looking",
            "row in a grid this wide is what a false positive looks like. A",
            "promotion here requires the row to be robust in its own phase, to",
            "survive correction over every row graded across the universe, and",
            "for the effect to appear on more than one instrument.",
        ]
    return "\n".join(lines)


def render(audit: dict, study: dict | None = None) -> str:
    parts = [render_history(audit)]
    if study:
        parts += [render_events(study), render_verdict(study)]
    return "\n\n".join(parts)


def gate_open(audit: dict) -> bool:
    """Whether any instrument was admitted at all."""
    return any(r["gate"] == GATE_INCLUDED for r in audit.get("instruments", []))


def promoted_ids(study: dict) -> list[str]:
    return [
        row["candidate_id"]
        for fam in study.get("families", [])
        for row in fam.get("promoted", [])
        if row.get("phase55_verdict") == PROMOTED
    ]
