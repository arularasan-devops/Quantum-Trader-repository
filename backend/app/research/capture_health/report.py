"""Render a coverage measurement as text. No measurement happens here.

The layout follows what the operator has to decide. Coverage percentage first,
because that is the number every calendar estimate in the project multiplies
against; then the split of the missing minutes into downtime and
alive-but-not-captured, because those have different fixes; then the individual
gaps with clock times, because a hole is usually recognisable on sight.
"""
from __future__ import annotations

from app.research.capture_health import (
    ALIVE_NOT_CAPTURED,
    DOWN_OR_STALLED,
    FROM_LIVENESS,
    HEARTBEAT_REACH_MIN,
    LIVENESS_REACH_MIN,
    NO_HEARTBEAT_FILE,
    NOT_EVIDENCED,
    NO_SESSION_EVIDENCE,
    PROCESS_DOWN,
    PROCESS_REACH_MIN,
    SCAN_MISSED_IT,
    TICK_RAN_NO_ROW,
    TICK_STALLED,
    WEEKEND,
    WITH_PROCESS,
)
from app.research.phase17 import store as p17store

GAP_LIMIT = 12


def _mb(value: int | None) -> str:
    return "unknown" if value is None else f"{value / (1024 * 1024):,.0f} MB"


def _session_block(row: dict) -> list[str]:
    lines = [f"  {row['session']}  {row['instrument']} ({row['exchange'] or '?'})"
             + ("   IN PROGRESS" if row.get("in_progress") else "")]
    if row.get("session_minutes") is None:
        lines.append(
            "    not graded        : the exchange was shut — a Saturday or a"
            " Sunday is not a session, so none of it is missing"
            if row.get("not_graded") == WEEKEND else
            "    session window unknown for this instrument; "
            "coverage not graded")
        lines.append(f"    captured minutes  : {row['captured_minutes']}"
                     f"   observations {row['observations']}")
        return lines
    if row.get("not_graded") == NO_SESSION_EVIDENCE:
        lines.append(
            "    not graded        : no row, no liveness line and no process"
            " line anywhere in the session")
        lines.append(
            "                        an exchange holiday and a capture that"
            " never ran that day leave the same absence, so neither is claimed")
        lines.append(f"    captured minutes  : {row['captured_minutes']}"
                     f"   ({row['observations']} observations, all outside the"
                     " session window)")
        return lines
    lines.append(
        f"    session           : {row['session_open']}-{row['session_close']} IST"
        f"   {row['session_minutes']} minutes"
    )
    if row.get("in_progress"):
        lines.append(
            "    measured          : "
            + (f"{row['session_open']}-{row['measured_close']} IST"
               f"   {row['measured_minutes']} completed minutes so far"
               " — graded on those only, and kept out of the median"
               if row.get("measured_minutes")
               else "nothing yet — the session has not opened, so no minute of"
                    " it is missing")
        )
    if not row.get("measured_minutes") and row.get("in_progress"):
        lines.append(f"    captured          : {row['captured_minutes']} minutes"
                     f"   ({row['observations']} observations, outside the"
                     " session window)")
        return lines
    lines.append(
        f"    captured          : {row['captured_in_session']} minutes"
        f"   = {row['coverage_pct']}%"
        f"   ({row['observations']} observations,"
        f" {row['obs_per_captured_minute']} per captured minute)"
    )
    lines.append(
        f"    span              : {row['first_captured']}-{row['last_captured']} IST"
    )
    if row.get("captured_outside_session"):
        lines.append(
            f"    outside session   : {row['captured_outside_session']} minutes"
            " captured before the open or after the close, not counted above"
        )
    down = row.get("down_cause")
    alive_part = f"{row['missing_alive_not_captured']} while the capture was alive"
    if row.get("reached_minutes") is not None:
        alive_part += (
            f" ({row.get('missing_scan_missed_it', 0)} never ticked for this"
            f" name, {row.get('missing_tick_ran_no_row', 0)} ticked and dropped)")
    parts = [alive_part]
    if down == PROCESS_DOWN:
        # A liveness-backed session. The tick-path total is split only as far as
        # the process journal can carry it, which on a session captured before
        # that journal existed is not at all.
        if row.get("missing_process_down"):
            parts.insert(0, f"{row['missing_process_down']} process gone")
        if row.get("missing_tick_stalled"):
            parts.insert(0, f"{row['missing_tick_stalled']} stalled while up")
        if row.get("missing_down_or_stalled"):
            parts.insert(
                0,
                f"{row['missing_down_or_stalled']} tick not running,"
                " gone or stalled undecided")
    else:
        parts.insert(0, f"{row.get('missing_down_or_stalled', 0)} unevidenced")
    lines.append(
        f"    missing           : {row['missing_minutes']} minutes"
        f"   ({', '.join(parts)})"
    )
    lines.append(
        f"    aliveness from    : {row.get('alive_source')}"
        + ("" if row.get("alive_reach_min") is None
           else f"   ({row['alive_reach_min']}-minute resolution)")
    )
    lines.append(
        f"    process journal   : {row.get('process_source')}"
        + ("" if row.get("process_minutes") is None
           else f"   ({row['process_minutes']} minutes with a line)")
    )
    reasons = row.get("reasons") or {}
    if reasons:
        lines.append("    recorded reasons  :")
        for why, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            lines.append(f"      {count:>5} minute(s)  {why}")
    if row.get("unexplained_alive_minutes"):
        lines.append(
            f"    unexplained       : {row['unexplained_alive_minutes']} minutes"
            " alive, no observation and no recorded refusal"
            + (" — split above by whether the tick reached this name"
               if row.get("reached_minutes") is not None else
               " — and no per-instrument liveness line to say whether the"
               " tick reached this name at all")
        )
    if row.get("ingested_minutes") is not None:
        lines.append(
            f"    reached the store : {row['ingested_minutes']} minutes"
            f"   ({row['not_ingested_minutes']} captured minutes not ingested)"
        )
        if row.get("not_ingested_minutes"):
            # Not a capture gap and not evidence loss: the ingest is a batch
            # pass over the journals, so a day still being captured is always
            # behind it. Said here because a shortfall next to the coverage
            # numbers reads like missing observations, and these are on disk.
            lines.append(
                "                      "
                "THE_ROWS_ARE_ON_DISK_THE_BATCH_INGEST_IS_BEHIND_RERUN_IT"
            )
    saw = row.get("witness") or []
    if saw:
        lines.append(
            "    where the loop was: (from the heartbeat thread, which keeps"
            " running when the tick does not)")
        for entry in saw[:GAP_LIMIT]:
            name = entry.get("instrument") or "—"
            lines.append(
                f"      {entry['minutes']:>5} minute(s)  {name:<12}"
                f"  worst {entry['worst_sec']:.0f}s  {entry['where']}"
            )
    elif row.get("witnessed_minutes") == 0 and row.get("missing_tick_stalled"):
        lines.append(
            "    where the loop was: NOTHING_WITNESSED_THE_WEDGE_WAS_SHORTER"
            "_THAN_THE_THRESHOLD_OR_ELSEWHERE")
    gaps = row.get("gaps") or []
    if gaps:
        shown = sorted(gaps, key=lambda g: -g["minutes"])[:GAP_LIMIT]
        lines.append(f"    gaps ({len(gaps)}), largest first:")
        for gap in shown:
            lines.append(
                f"      {gap['from']}-{gap['to']}  {gap['minutes']:>4}m"
                f"  {gap['where']:<30} {gap['cause']}"
            )
        if len(gaps) > len(shown):
            lines.append(f"      ... {len(gaps) - len(shown)} smaller gap(s)")
    return lines


def _rollup_block(roll: dict, heading: str) -> list[str]:  # noqa: C901
    """Where the missing minutes went across every graded session.

    The per-session blocks below say where a hole is. This says which cause owns
    the most minutes, because that — not the largest single gap — is what decides
    where the work goes: one 97-minute outage and ninety scattered
    alive-but-skipped minutes look equally bad session by session and need
    entirely different fixes.
    """
    if not roll:
        return []
    lines = [
        f"  {heading}"
        f"   ({roll.get('sessions_rolled_up')} graded session(s),"
        " in-progress and ungraded ones excluded)",
        f"    measured {roll.get('measured_minutes')}m"
        f"   captured {roll.get('captured_minutes')}m"
        f"   missing {roll.get('missing_minutes')}m",
    ]
    by_cause = roll.get("by_cause") or {}
    if not by_cause:
        lines.append("    no reported gap in range — nothing to attribute")
    for cause, minutes in sorted(by_cause.items(), key=lambda kv: -kv[1]):
        lines.append(f"      {minutes:>6}m  {cause}")
    left = roll.get("unattributed_minutes") or 0
    if left:
        lines.append(
            f"      {left:>6}m  spread over gaps too short to be reported"
            " individually — missing, and not charged to any cause")
    reasons = roll.get("refusal_reasons") or {}
    if reasons:
        lines.append("    of the alive minutes, what the tick recorded:")
        for why, minutes in sorted(reasons.items(), key=lambda kv: -kv[1]):
            lines.append(f"      {minutes:>6}m  {why}")
    lines.append(
        f"    dominant            : {roll.get('dominant_cause')}"
        + (f"   {roll.get('dominant_minutes')}m"
           f"   {roll.get('dominant_share_pct')}% of the missing total"
           if roll.get("dominant_minutes") else ""))
    # A cause that names the ignorance directs no work, and on any range that
    # reaches back before the liveness journal it owns the most minutes. So the
    # fix follows the dominant cause among the minutes that ARE decided — a
    # heartbeat minute with no row is proven whatever journals shipped later,
    # while a missing-and-not-alive minute on a finished day never will be.
    named = roll.get("by_named_cause") or {}
    unnamed = roll.get("minutes_no_journal_can_attribute") or 0
    if named:
        lines.append(
            f"    of the {roll.get('minutes_with_a_named_cause')}m a cause can"
            f" be named for at all ({unnamed}m never can be — a finished"
            " session grows no new evidence):")
        for cause, minutes in sorted(named.items(), key=lambda kv: -kv[1]):
            lines.append(f"      {minutes:>6}m  {cause}")
        lines.append(
            f"    dominant, decided   : {roll.get('dominant_named_cause')}"
            f"   {roll.get('dominant_named_minutes')}m"
            f"   {roll.get('dominant_named_share_pct')}% of those")
    elif unnamed:
        lines.append(
            f"    not one of the {unnamed}m carries a cause any journal can"
            " name, so no fix is indicated")
    lines.extend([
        f"    indicated fix       : {roll.get('indicated_fix')}",
        "",
    ])
    return lines


def render(measurement: dict) -> str:
    """The whole report as one string."""
    usage = measurement.get("usage") or {}
    out = [
        f"CAPTURE COVERAGE — {measurement['instrument']}",
        "  what the capture wrote, what it was alive for, and what reached the",
        "  research store. Read-only: this measures the journals, it cannot",
        "  repair a missing minute and it never writes one.",
        "",
        f"  sessions measured   : {measurement['session_count']}"
        + ("" if not measurement.get("in_progress_sessions") else
           "   (" + ", ".join(measurement["in_progress_sessions"])
           + " still running — measured to the last completed minute,"
             " excluded from the summary below)"),
        "  coverage, median    : "
        + (f"{measurement['median_coverage_pct']}%"
           f"   worst {measurement['worst_coverage_pct']}%"
           f"   best {measurement['best_coverage_pct']}%"
           f"   over {measurement.get('graded_session_count')}"
           " completed session(s)"
           if measurement.get("graded_session_count")
           else "not graded — no completed session in range"),
        "  not graded          : "
        + (", ".join(f"{u['session']} {u['why']}"
                     for u in measurement["ungraded_sessions"])
           if measurement.get("ungraded_sessions")
           else "none — every session in range had a window and some evidence"),
        "  heartbeat journal   : "
        + ("present" if measurement.get("heartbeat_available")
           else "MISSING — gaps cannot be split into downtime and skipped"),
        "  liveness journal    : "
        + (f"present, {LIVENESS_REACH_MIN}-minute resolution"
           if measurement.get("liveness_available")
           else "NOT YET WRITTEN — sessions before it fall back to the"
               " heartbeat, which only fires after a persisted row"),
        "  process journal     : "
        + (f"present, {PROCESS_REACH_MIN}-minute resolution — {WITH_PROCESS}"
           if measurement.get("process_available")
           else "NOT YET WRITTEN — without it a tick path that wrote nothing"
               " cannot be told from a process that had exited"),
        "  gap-reason journal  : "
        + ("present" if measurement.get("reasons_available")
           else "NOT YET WRITTEN — sessions captured before this journal existed"
               " carry no reasons, and their gaps stay unattributed"),
        "  derived store       : "
        + ("read" if measurement.get("ingest_checked") else "not checked"),
        f"  journal files       : {usage.get('journal_files')}"
        f"   {_mb(usage.get('journal_bytes'))} in {usage.get('data_dir')}"
        f"   free {_mb(usage.get('free_bytes'))}",
        "",
        "  A full disk is the one capture failure that leaves no trace in the",
        "  journals: the writer counts the error into its health state and",
        "  returns, so the hole looks like a clean shutdown.",
        "",
        "  CAUSES",
        f"    {NOT_EVIDENCED}",
        "      no heartbeat and no row. This is NOT a downtime finding: the",
        "      heartbeat fires only after an observation persists, so a process",
        "      that is up and capturing nothing at all — expired broker session,",
        "      dead feed, failing write — leaves the same silence as a dead one.",
        "      Restarting may fix nothing here. The liveness journal separates",
        "      the two; until a session has one, this stays undecided.",
        f"    {PROCESS_DOWN}",
        "      the tick path wrote no liveness line AND the process journal",
        "      wrote none either: the interpreter itself was gone. Restart",
        "      supervision recovers these minutes and only these.",
        f"    {TICK_STALLED}",
        "      the process journal kept writing from its own thread while the",
        "      tick path wrote nothing: the loop was wedged, not dead. Nothing",
        "      for supervision to restart — a restart clears it by hand, and the",
        "      fix is whatever blocked the loop.",
        f"    {p17store.STUCK_IN_TICK}",
        "      the stall witness, written from the heartbeat thread: the loop",
        "      went into this instrument's tick and had not come back. It names",
        "      the instrument and how long, which is as close to a line of code",
        "      as a journal can get.",
        f"    {p17store.STUCK_BETWEEN}",
        "      no tick was in flight and none had started for longer than the",
        "      threshold: the loop was not scheduling ticks at all, so the fault",
        "      is in the scheduler and not in any one instrument's tick.",
        f"    {DOWN_OR_STALLED}",
        "      no liveness line and no process journal for that session, so",
        "      gone and stalled are not distinguishable. Every session captured",
        "      before the process journal shipped reads this way; an earlier",
        "      build called them proven downtime, which the liveness journal",
        "      alone does not support.",
        f"    {ALIVE_NOT_CAPTURED}",
        "      the capture was running and wrote nothing for this instrument.",
        "      Restarting or supervising the process cannot recover these; the",
        "      cause is in the tick path. WHICH part of it is the split below,",
        "      and it needs the liveness journal, which names the instrument.",
        f"    {SCAN_MISSED_IT}",
        "      a liveness line exists for that minute but not for this name:",
        "      the process was ticking something else and never got here. The",
        "      scan's reach is the fix; the feed and the chain are not at fault",
        "      and nothing was there to record a refusal.",
        f"    {TICK_RAN_NO_ROW}",
        "      the liveness line names THIS instrument, so the tick did reach",
        "      it, and neither an observation nor a refusal reason was written.",
        "      Widening the scan recovers none of these: something inside the",
        "      capture returned silently, and that path is the fix.",
        f"    {NO_HEARTBEAT_FILE}",
        "      no heartbeat journal for that session, so the two are not",
        "      distinguishable and the gap is left unattributed.",
        "",
        f"    {FROM_LIVENESS}",
        "      the strong evidence: the tick path writes a line a minute before",
        "      anything can refuse a row, so downtime is measured rather than",
        "      inferred from the absence of a success.",
        "",
        "  A heartbeat proves the capture was up in its own minute. Beyond that,",
        "  only minutes lying BETWEEN two heartbeats no more than"
        f" {2 * HEARTBEAT_REACH_MIN} minutes",
        "  apart are called alive: a capture running on both sides cannot have",
        "  died and returned in between. Nothing is claimed forward from the last",
        "  heartbeat, because the heartbeat is rate-limited and silence just",
        "  after one says nothing. A missing stretch is split where that verdict",
        "  changes, so one heartbeat cannot certify a five-hour hole it happens",
        "  to sit inside as time the capture was up.",
        "",
        "  An alive gap with a recorded reason says the tick ran and the capture",
        "  refused the row. An alive gap with NO recorded reason was read as",
        "  \"the scan never reached it\" — an inference, not a measurement: the",
        "  heartbeat pools every instrument, so a minute spent on another name",
        "  and a minute this one was reached in and silently dropped looked",
        "  identical. The liveness journal names the instrument — but it was",
        "  rate-limited per PROCESS, one line a minute carrying whichever of",
        "  46 names ticked as the limiter opened, so the name's absence still",
        "  meant nothing. It is limited per name now, and only sessions whose",
        "  rows say so are split; the rest stay pooled and undecided rather",
        "  than decided on a field that cannot bear it.",
        "",
    ]
    out.extend(_rollup_block(
        measurement.get("where_the_minutes_went") or {},
        "WHERE THE MISSING MINUTES WENT"))
    out.append("  SESSIONS, oldest first")
    for row in measurement.get("sessions") or []:
        out.extend(_session_block(row))
        out.append("")
    if not measurement.get("sessions"):
        out.append("    none — the observation journal holds no row for this "
                   "instrument")
    return "\n".join(out)
