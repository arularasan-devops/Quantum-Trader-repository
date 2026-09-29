"""Does every book agree about what the board said? — Parts 27, 28, 29, 33, 37.

The complaint this answers is a specific one: a BUY appears in the Reports
journal and not on the dashboard, or on the dashboard and not in the paper book,
and no report can say which step dropped it. Reconciliation reads the lifecycle
ledger (what each stage recorded) and the signal journal (what the board wrote
down) and produces the counts, the differences, and the exact reason for every
difference.

Three rules the implementation keeps, because they are what make the output
trustworthy rather than alarming:

* **Duplicates are not misses (Part 29).** Many raw events collapsing into one
  episode with one dashboard signal is correct de-duplication. Only an episode
  with journal rows and no ``DASHBOARD_PUBLISHED`` is
  ``MISSING_DASHBOARD_PUBLICATION``.
* **A refusal with a recorded reason is not a miss.** A plan correctly refused as
  unenterable is a working system, and it is reported separately from a signal
  that vanished.
* **Nothing is inferred from price.** A later favourable move does not make a
  refused signal a missed opportunity; that judgement is left to the research
  panel, which has the outcome data.

Read-only: this module opens no position, changes no state and is not imported by
anything in the trading path.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter

from app.analysis import signal_journal
from app.analysis import signal_lifecycle as lc
from app.config import settings

DAILY_JSON = "daily_signal_reconciliation.json"
DAILY_MD = "daily_signal_reconciliation.md"

# An episode with journal rows and no publication.
MISSING_DASHBOARD_PUBLICATION = "MISSING_DASHBOARD_PUBLICATION"
# A dashboard BUY with no journal row (the reverse failure).
MISSING_JOURNAL_ROW = "MISSING_JOURNAL_ROW"
# An execution attempt whose episode is not in the ledger at all.
ORPHAN_EXECUTION = "ORPHAN_EXECUTION"

_BUY = "BUY"


def _is_buy(reason: str | None) -> bool:
    return (reason or "").upper() == _BUY


def _episode_rows(records: list[dict]) -> dict[str, dict]:
    """Fold the ledger into one entry per episode.

    The episode, not the event, is the unit a human counts in: an episode is one
    call regardless of how many ticks repeated it.
    """
    out: dict[str, dict] = {}
    for rec in records:
        ep = rec.get("episode_id")
        if not ep:
            continue
        entry = out.get(ep)
        if entry is None:
            entry = out[ep] = {
                "episode_id": ep,
                "global_signal_id": rec.get("global_signal_id"),
                "instrument": rec.get("instrument"),
                "vehicle": rec.get("vehicle"),
                "market": rec.get("market") or lc.OPTIONS,
                "session": rec.get("session"),
                "first_ts": rec.get("ts"),
                "first_time_ist": rec.get("time_ist"),
                "raw_events": 0,
                "stages": {},
                "misses": [],
                "buy": False,
                "signal_price": None,
            }
        entry["raw_events"] += 1
        entry["last_ts"] = rec.get("ts")
        stage = rec.get("stage")
        status = rec.get("status")
        entry["stages"].setdefault(stage, []).append(status)
        if status == lc.MISSED:
            entry["misses"].append({
                "missed_stage": stage,
                "missed_reason": rec.get("reason"),
                "time_ist": rec.get("time_ist"),
                "value": rec.get("value"),
                "threshold": rec.get("threshold"),
                "source": rec.get("source"),
                "detail": rec.get("detail"),
            })
        if stage == lc.GENERATED and _is_buy(rec.get("reason")):
            entry["buy"] = True
        if stage == lc.DASHBOARD_PUBLISHED and entry["signal_price"] is None:
            entry["signal_price"] = rec.get("value")
    return out


def _has(entry: dict, stage: str, status: str = lc.OK) -> bool:
    return status in entry["stages"].get(stage, [])


def reconcile(session: str | None = None, limit: int | None = None) -> dict:
    """Counts and differences for one session, per Part 27.

    ``session`` is an IST date (``YYYY-MM-DD``); the default is every session in
    the ledger. The ledger file is read rather than the in-memory tracker so a
    report survives a restart.

    Complete by default (Phase 12 §2). ``limit`` exists only for a caller that
    deliberately wants a sample, and whatever it leaves out is counted in
    ``coverage.records_omitted`` and fails the report rather than being dropped
    silently — the previous 20,000-row default cut the market open out of every
    daily artefact without saying so.
    """
    records, coverage = lc.scan_ledger(session=session, limit=limit)
    episodes = _episode_rows(records)
    journal = signal_journal.read_journal(limit=limit, session=session)

    journal_by_episode: dict[str, list[dict]] = {}
    for row in journal:
        ep = row.get("episode_id")
        if ep:
            journal_by_episode.setdefault(ep, []).append(row)

    buys = [e for e in episodes.values() if e["buy"]]
    option_buys = [e for e in buys if e["market"] == lc.OPTIONS]
    futures_buys = [e for e in buys if e["market"] == lc.MARKET_FUTURES]

    published = [e for e in buys if _has(e, lc.DASHBOARD_PUBLISHED)]
    # The two dashboards are counted apart: an option BUY missing from the Option
    # Signal tab and a futures plan missing from the Futures tab are different
    # faults with different causes.
    published_option = [e for e in published if e["market"] == lc.OPTIONS]
    published_futures = [e for e in published if e["market"] == lc.MARKET_FUTURES]
    unpublished = [e for e in buys if not _has(e, lc.DASHBOARD_PUBLISHED)]
    visible = [e for e in buys if _has(e, lc.USER_VISIBLE)]
    paper_eligible = [e for e in buys if _has(e, lc.PAPER_ELIGIBLE)]
    attempts = [e for e in buys if _has(e, lc.EXECUTION_CHECK)
                or _has(e, lc.EXECUTION_ACCEPTED)]
    filled = [e for e in buys if _has(e, lc.FILLED)]
    rejected = [e for e in buys if any(
        m["missed_stage"] in (lc.EXECUTION_ACCEPTED, lc.FILLED,
                              lc.EXECUTION_CHECK, lc.PAPER_ELIGIBLE)
        for m in e["misses"])]
    journalled = [e for e in buys if e["episode_id"] in journal_by_episode]

    # A journal row whose episode has no dashboard publication, unless the row
    # itself says why it was never dashboard-eligible (Part 28).
    missing_publication: list[dict] = []
    for ep, rows in journal_by_episode.items():
        if not any((r.get("board_action") or "").upper() == _BUY for r in rows):
            continue
        entry = episodes.get(ep)
        if entry is not None and _has(entry, lc.DASHBOARD_PUBLISHED):
            continue
        exempt = next((r.get("dashboard_exempt_reason") for r in rows
                       if r.get("dashboard_exempt_reason")), None)
        if exempt in lc.EXEMPT_REASONS:
            continue
        missing_publication.append({
            "episode_id": ep,
            "instrument": rows[0].get("instrument"),
            "market": rows[0].get("market") or lc.OPTIONS,
            "vehicle": rows[0].get("signal_vehicle") or rows[0].get("vehicle"),
            "option": (rows[0].get("contract") or {}).get("symbol")
            if isinstance(rows[0].get("contract"), dict) else None,
            "time_ist": rows[0].get("recorded_at"),
            "journal_rows": len(rows),
            "raw_events": (entry or {}).get("raw_events", 0),
            "failure": MISSING_DASHBOARD_PUBLICATION,
            # An episode absent from the ledger entirely is a different fault
            # from one present but unpublished, and the two need different fixes.
            "in_lifecycle_ledger": entry is not None,
            "reason": (entry or {}).get("misses", [{}])[0].get("missed_reason")
            if (entry or {}).get("misses") else lc.NOT_PUBLISHED,
        })

    # A dashboard BUY with no journal row: the reverse direction of the same test.
    missing_journal = [{
        "episode_id": e["episode_id"],
        "global_signal_id": e["global_signal_id"],
        "instrument": e["instrument"],
        "market": e["market"],
        "vehicle": e["vehicle"],
        "time_ist": e["first_time_ist"],
        "failure": MISSING_JOURNAL_ROW,
    } for e in published if e["episode_id"] not in journal_by_episode]

    miss_reasons: Counter[str] = Counter()
    for e in buys:
        for m in e["misses"]:
            miss_reasons[str(m["missed_reason"])] += 1

    unresolved = [e for e in filled if not (_has(e, lc.EXITED)
                                            or _has(e, lc.RESOLVED))]

    complete = (coverage["records_omitted"] == 0
                and not coverage["truncated"]
                and coverage["read_error"] is None)

    return {
        "session": session,
        # A report that could not read its whole input is not a report. This is
        # stated first so no reader has to notice a count is short.
        "report_status": "COMPLETE" if complete else "INCOMPLETE",
        "complete": complete,
        "coverage": {
            "records_seen": coverage["records_seen"],
            "records_processed": coverage["records_processed"],
            "records_omitted": coverage["records_omitted"],
            "records_unparsable": coverage["records_unparsable"],
            "file_lines": coverage["file_lines"],
            "limit": coverage["limit"],
            "read_error": coverage["read_error"],
            "journal_rows": len(journal),
        },
        "window": {
            "ledger_events": len(records),
            "journal_rows": len(journal),
        },
        "counts": {
            "engine_buys": len(buys),
            "option_buys": len(option_buys),
            "futures_buys": len(futures_buys),
            "dashboard_published": len(published),
            "option_dashboard_buys": len(published_option),
            "futures_dashboard_buys": len(published_futures),
            # generated but never published: measured on the lifecycle ledger, so
            # it only covers episodes the engine recorded from generation onward.
            "dashboard_missing": len(unpublished),
            # the same disagreement seen from the journal and from the dashboard.
            # These are reported apart because a journal row with no ledger entry
            # predates lifecycle logging and is not evidence of a lost signal.
            "journal_without_dashboard": len(missing_publication),
            "dashboard_without_journal": len(missing_journal),
            "user_visible": len(visible),
            "journal_recorded": len(journalled),
            "paper_eligible": len(paper_eligible),
            "execution_attempts": len(attempts),
            "filled": len(filled),
            "rejected": len(rejected),
            "duplicate_raw_events": sum(max(0, e["raw_events"] - 1) for e in buys),
            "unique_episodes": len(episodes),
            "unresolved": len(unresolved),
        },
        "missed_reasons": dict(miss_reasons.most_common()),
        # The two directions of disagreement, each with the reason attached, and
        # capped at the 20 the report shows (Part 37).
        "missing_dashboard_publication": missing_publication[:20],
        "missing_journal_row": missing_journal[:20],
        "note": ("a refusal with a recorded reason is a working gate, not a lost "
                 "signal; only an episode with no stage at all is unexplained. "
                 "journal_without_dashboard counts journal BUYs whose episode has "
                 "no publication stage — rows written before lifecycle logging "
                 "existed carry in_lifecycle_ledger=false and are a coverage gap, "
                 "not a lost signal"),
    }


def missed_signals(session: str | None = None, limit: int | None = None,
                   reason: str | None = None) -> list[dict]:
    """Every BUY episode that stopped short, with its stage and reason (Part 32).

    Deliberately does NOT judge whether the signal was worth taking: a later
    favourable move is not evidence that a correctly refused plan was an
    opportunity. It reports where the signal stopped and why, and leaves the
    outcome to the research panel that has the price data.
    """
    episodes = _episode_rows(lc.read_ledger(limit=limit, session=session))
    out: list[dict] = []
    for e in episodes.values():
        if not e["buy"] or not e["misses"]:
            continue
        if (_has(e, lc.USER_VISIBLE)
                and all(m["missed_reason"] == lc.EPISODE_SUPERSEDED
                        for m in e["misses"])):
            # The user saw this call and the board then moved on to a newer one.
            # Nothing about it went missing, so it is not a missed signal.
            continue
        last = e["misses"][-1]
        if reason and last["missed_reason"] != reason:
            continue
        out.append({
            "episode_id": e["episode_id"],
            "global_signal_id": e["global_signal_id"],
            "instrument": e["instrument"],
            "market": e["market"],
            "vehicle": e["vehicle"],
            "time_ist": e["first_time_ist"],
            "signal_price": e["signal_price"],
            "raw_events": e["raw_events"],
            "engine_generated": True,
            "dashboard_published": _has(e, lc.DASHBOARD_PUBLISHED),
            "user_visible": _has(e, lc.USER_VISIBLE),
            "paper_eligible": _has(e, lc.PAPER_ELIGIBLE),
            "filled": _has(e, lc.FILLED),
            "missed_stage": last["missed_stage"],
            "missed_reason": last["missed_reason"],
            "detail": last.get("detail"),
            "all_misses": e["misses"],
            # A refused plan and a vanished signal look the same in a count and
            # are not the same event, so the distinction is on every row.
            "classification": ("CORRECTLY_REFUSED"
                               if last["missed_reason"] in (
                                   lc.PLAN_INVALID, lc.PLAN_STALE,
                                   lc.DUPLICATE_EPISODE, lc.EPISODE_SUPERSEDED,
                                   lc.ALREADY_TRADED)
                               else "STOPPED_BEFORE_USER"),
        })
    out.sort(key=lambda r: r.get("time_ist") or "")
    return out


def daily_markdown(report: dict) -> str:
    """The same reconciliation as a page a human reads (Part 37)."""
    c = report["counts"]
    lines = [
        f"# Daily signal reconciliation — {report.get('session') or 'all sessions'}",
        "",
    ]
    cov = report.get("coverage") or {}
    if not report.get("complete", True):
        lines += [
            f"**REPORT INCOMPLETE — {cov.get('records_omitted', 0)} of "
            f"{cov.get('records_seen', 0)} lifecycle rows were not processed "
            f"(read_error={cov.get('read_error')}). Every count below is a "
            f"sample, not the session.**",
            "",
        ]
    lines += [
        f"Lifecycle rows seen: {cov.get('records_seen', 0)}   "
        f"processed: {cov.get('records_processed', 0)}   "
        f"omitted: {cov.get('records_omitted', 0)}   "
        f"journal rows: {report['window']['journal_rows']}",
        "",
        "| stage | signals |",
        "| --- | --- |",
        f"| engine BUYs | {c['engine_buys']} |",
        f"| option BUYs | {c['option_buys']} |",
        f"| futures BUYs | {c['futures_buys']} |",
        f"| dashboard published | {c['dashboard_published']} |",
        f"| on the Option Signal dashboard | {c['option_dashboard_buys']} |",
        f"| on the Futures Signal dashboard | {c['futures_dashboard_buys']} |",
        f"| dashboard missing | {c['dashboard_missing']} |",
        f"| journal BUYs with no publication | {c['journal_without_dashboard']} |",
        f"| dashboard BUYs with no journal row | {c['dashboard_without_journal']} |",
        f"| seen by a client | {c['user_visible']} |",
        f"| journal recorded | {c['journal_recorded']} |",
        f"| paper eligible | {c['paper_eligible']} |",
        f"| execution attempts | {c['execution_attempts']} |",
        f"| filled | {c['filled']} |",
        f"| rejected | {c['rejected']} |",
        f"| unique episodes | {c['unique_episodes']} |",
        f"| repeat events folded into episodes | {c['duplicate_raw_events']} |",
        f"| unresolved | {c['unresolved']} |",
        "",
    ]
    if report["missed_reasons"]:
        lines += ["## Why signals stopped", "",
                  "| reason | count |", "| --- | --- |"]
        lines += [f"| {k} | {v} |" for k, v in report["missed_reasons"].items()]
        lines.append("")
    rows = report["missing_dashboard_publication"]
    lines += [f"## Journal BUYs with no dashboard publication "
              f"(showing {len(rows)} of {c['journal_without_dashboard']})", ""]
    if not rows:
        lines += ["None — every journalled BUY was published.", ""]
    else:
        lines += ["| time | instrument | vehicle | journal rows | in ledger | reason |",
                  "| --- | --- | --- | --- | --- | --- |"]
        lines += [
            f"| {r['time_ist']} | {r['instrument']} | {r['vehicle'] or '-'} | "
            f"{r['journal_rows']} | {'yes' if r['in_lifecycle_ledger'] else 'NO'} "
            f"| {r['reason']} |"
            for r in rows
        ]
        lines.append("")
    if report["missing_journal_row"]:
        lines += ["## Dashboard BUYs with no journal row", "",
                  "| time | instrument | episode |", "| --- | --- | --- |"]
        lines += [f"| {r['time_ist']} | {r['instrument']} | {r['episode_id']} |"
                  for r in report["missing_journal_row"]]
        lines.append("")
    lines += ["", f"_{report['note']}._", ""]
    return "\n".join(lines)


def write_daily(session: str | None = None) -> dict:
    """Write the day's reconciliation as JSON and as Markdown (Part 37).

    Named per session so a day's report is not overwritten by the next one, plus
    an unsuffixed copy for whatever reads "the latest".
    """
    session = session or lc.session_of(time.time())
    report = reconcile(session=session)
    report["missed_signals"] = missed_signals(session=session)[:20]
    if not report["complete"]:
        report["failure"] = (
            "records_omitted > 0: the reconciliation could not read the whole "
            "session, so these counts are a sample and must not be quoted as "
            "the day"
        )
    os.makedirs(settings.data_dir, exist_ok=True)
    written: list[str] = []
    body = daily_markdown(report)
    for name, text in (
        (DAILY_JSON, json.dumps(report, indent=2, default=str)),
        (DAILY_MD, body),
        (f"daily_signal_reconciliation_{session}.json",
         json.dumps(report, indent=2, default=str)),
        (f"daily_signal_reconciliation_{session}.md", body),
    ):
        path = os.path.join(settings.data_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        written.append(path)
    return {
        "session": session,
        "written": written,
        "report_status": report["report_status"],
        "complete": report["complete"],
        "coverage": report["coverage"],
        "counts": report["counts"],
    }
