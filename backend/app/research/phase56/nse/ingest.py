"""Ingest orchestration: one calendar day at a time, resumable, idempotent.

A calendar day that the archive does not publish is a holiday or a weekend, not a
failure — but a day the *host* could not be reached is a failure, and the two are
recorded as different statuses so that a partial ingest can never be mistaken for
a complete one. ``UNAVAILABLE`` days stay in the manifest as unfinished work and
are retried on the next run; ``NOT_PUBLISHED`` days are settled.

Prices are mandatory, delivery is optional enrichment, corporate actions are
mandatory-but-independent: a session whose PR archive is missing still yields its
prices, and the audit counts the sessions whose action file was not obtained
rather than letting the gap pass as "no actions that day".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from . import CorporateAction
from .archive import Archive, NOT_PUBLISHED, UNAVAILABLE, read_member
from .bhav import enrich_with_delivery, parse_bhavcopy, parse_delivery
from .corpact import dedupe, parse_bc
from .store import CONFLICT, RawStore

SESSION_OK = "SESSION_OK"
SESSION_NOT_PUBLISHED = "SESSION_NOT_PUBLISHED"
SESSION_UNAVAILABLE = "SESSION_UNAVAILABLE"
SESSION_PARSE_FAILED = "SESSION_PARSE_FAILED"
SESSION_CONFLICT = "SESSION_CONFLICT"


@dataclass
class IngestSummary:
    requested: int = 0
    ok: int = 0
    not_published: int = 0
    unavailable: int = 0
    parse_failed: int = 0
    conflicts: int = 0
    already_present: int = 0
    rows: int = 0
    delivery_sessions: int = 0
    action_sessions: int = 0
    action_sessions_missing: int = 0
    actions_parsed: int = 0
    rejected_rows: int = 0
    reject_reasons: dict[str, int] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "requested": self.requested,
            "ok": self.ok,
            "not_published": self.not_published,
            "unavailable": self.unavailable,
            "parse_failed": self.parse_failed,
            "conflicts": self.conflicts,
            "already_present": self.already_present,
            "rows": self.rows,
            "delivery_sessions": self.delivery_sessions,
            "action_sessions": self.action_sessions,
            "action_sessions_missing": self.action_sessions_missing,
            "actions_parsed": self.actions_parsed,
            "rejected_rows": self.rejected_rows,
            "reject_reasons": dict(sorted(self.reject_reasons.items())),
        }


def calendar_days(start: date, end: date) -> list[date]:
    """Every Monday-to-Friday date in range. Holidays fall out as NOT_PUBLISHED."""
    out = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def ingest_day(
    day: date,
    archive: Archive,
    store: RawStore,
    *,
    with_delivery: bool = True,
    with_actions: bool = True,
) -> tuple[str, list[CorporateAction]]:
    """Ingest one session. Returns (status, actions published that day)."""
    existing = store.session_status(day)
    if (
        existing
        and existing.get("status") == SESSION_OK
        and (existing.get("rows") or 0) > 0
        and store.session_path(day).exists()
    ):
        actions = _actions_for(day, archive, store) if with_actions else []
        return SESSION_OK, actions

    fetched = archive.bhavcopy(day)
    if fetched.status == NOT_PUBLISHED:
        store.record_session(day, status=SESSION_NOT_PUBLISHED, rows=0)
        return SESSION_NOT_PUBLISHED, []
    if fetched.status == UNAVAILABLE or fetched.path is None:
        store.record_session(day, status=SESSION_UNAVAILABLE, detail=fetched.detail)
        return SESSION_UNAVAILABLE, []

    try:
        _, text = read_member(fetched.path.read_bytes())
        report = parse_bhavcopy(text)
    except Exception as exc:
        store.record_session(day, status=SESSION_PARSE_FAILED, detail=type(exc).__name__)
        return SESSION_PARSE_FAILED, []

    if not report.rows:
        # An archive that parsed but yielded nothing is a schema the parser does
        # not actually understand, not a session in which nothing traded. Storing
        # it as an empty OK session would hide a whole day behind a valid status.
        store.record_session(
            day,
            status=SESSION_PARSE_FAILED,
            rows=0,
            schema=report.schema,
            rejected=report.rejected,
            reject_reasons=dict(sorted(report.reject_reasons.items())),
            detail="NO_ROWS_PARSED",
        )
        return SESSION_PARSE_FAILED, []

    rows = report.rows
    delivery_ok = False
    delivery_detail = "NOT_REQUESTED" if not with_delivery else ""
    if with_delivery:
        delivered = archive.delivery(day)
        if not delivered.ok or delivered.path is None:
            delivery_detail = delivered.status
        else:
            # Delivery is enrichment on top of prices: a malformed delivery file
            # must cost the delivery columns for that session and nothing else,
            # never the session's prices, and it has to say so on the record.
            try:
                mapping = parse_delivery(delivered.path.read_text(errors="replace"))
            except Exception as exc:
                mapping = {}
                delivery_detail = f"UNREADABLE:{type(exc).__name__}"
            if mapping:
                rows = enrich_with_delivery(rows, mapping)
                delivery_ok = True
            elif not delivery_detail:
                delivery_detail = "EMPTY"

    write = store.write_session(day, rows)
    status = SESSION_CONFLICT if write.status == CONFLICT else SESSION_OK
    store.record_session(
        day,
        status=status,
        rows=len(rows),
        digest=write.digest,
        schema=report.schema,
        rejected=report.rejected,
        reject_reasons=dict(sorted(report.reject_reasons.items())),
        delivery=delivery_ok,
        delivery_detail=delivery_detail,
    )

    actions = _actions_for(day, archive, store) if with_actions else []
    return status, actions


def _actions_for(day: date, archive: Archive, store: RawStore) -> list[CorporateAction]:
    fetched = archive.corporate_actions(day)
    if not fetched.ok or fetched.path is None:
        store.record_session(day, actions="MISSING", action_detail=fetched.status)
        return []
    try:
        name, text = read_member(fetched.path.read_bytes(), prefix="bc")
    except Exception as exc:
        store.record_session(day, actions="UNREADABLE", action_detail=type(exc).__name__)
        return []
    try:
        actions, counters = parse_bc(text, source_file=name)
    except Exception as exc:
        store.record_session(day, actions="UNREADABLE", action_detail=type(exc).__name__)
        return []
    store.record_session(day, actions="OK", action_rows=counters["rows"])
    return actions


def ingest_range(
    start: date,
    end: date,
    archive: Archive,
    store: RawStore,
    *,
    with_delivery: bool = True,
    with_actions: bool = True,
    progress=None,
) -> IngestSummary:
    summary = IngestSummary()
    collected: list[CorporateAction] = list(store.read_actions()) if with_actions else []
    for day in calendar_days(start, end):
        summary.requested += 1
        before = store.session_status(day)
        status, actions = ingest_day(
            day, archive, store, with_delivery=with_delivery, with_actions=with_actions
        )
        summary.statuses[day.isoformat()] = status
        entry = store.session_status(day) or {}
        if status == SESSION_OK:
            summary.ok += 1
            summary.rows += int(entry.get("rows") or 0)
            if entry.get("delivery"):
                summary.delivery_sessions += 1
            if before and before.get("status") == SESSION_OK:
                summary.already_present += 1
        elif status == SESSION_NOT_PUBLISHED:
            summary.not_published += 1
        elif status == SESSION_UNAVAILABLE:
            summary.unavailable += 1
        elif status == SESSION_PARSE_FAILED:
            summary.parse_failed += 1
        elif status == SESSION_CONFLICT:
            summary.conflicts += 1
        summary.rejected_rows += int(entry.get("rejected") or 0)
        for reason, count in (entry.get("reject_reasons") or {}).items():
            summary.reject_reasons[reason] = summary.reject_reasons.get(reason, 0) + count
        if with_actions and status in (SESSION_OK, SESSION_CONFLICT):
            if entry.get("actions") == "OK":
                summary.action_sessions += 1
            else:
                summary.action_sessions_missing += 1
        collected.extend(actions)
        if progress is not None:
            progress(day, status, summary)

    if with_actions:
        deduped = dedupe(collected)
        summary.actions_parsed = store.write_actions(deduped)
    store.save_manifest()
    return summary
