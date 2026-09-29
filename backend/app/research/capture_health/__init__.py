"""Capture coverage — how much of a session the evidence layer actually saw.

Every study in this project is rate-limited by one number nobody has ever
measured: the fraction of a trading session that the capture was writing rows
for. Phase 44's first live preview made the cost of not knowing it obvious.
19,983 decision instants were recorded on 2026-09-10 and they fell inside 444
distinct minutes, against a CRUDEOIL session of 870. Every calendar estimate
derived from that session — "69 sessions to a sample floor", "400 admissions" —
silently assumed the other 426 minutes did not exist.

This package answers three separate questions that were previously collapsed
into one. They have different causes and different fixes, and reading the wrong
one is how a capture problem gets misdiagnosed as a market fact:

``CAPTURED``
    minutes for which the Phase 17 observation journal holds at least one row
    for the instrument. This is what the capture truly recorded.
``ALIVE``
    minutes the tick path is PROVEN to have been running in. Preferably from
    the liveness journal, which is written once a minute before anything can
    refuse a row; otherwise from the older coverage heartbeat, which fires only
    after an observation has been persisted and therefore cannot tell "the
    process was down" from "the process was up and captured nothing at all".
    A gap the process was alive through is *not* downtime, and restarting the
    app will not recover it.

    Aliveness is decided PER MINUTE, not per gap, and only where the journal
    proves it. The first version of this module classified a whole missing run
    by whether it contained any heartbeat at all, which charged a 322-minute
    hole with one heartbeat in it entirely to the tick path — 665 "alive, not
    captured" minutes on a session that held 59 heartbeats in total. The same
    session now reads 675 downtime against 93 alive, and the 93 are minutes
    bracketed by heartbeats on both sides.
``INGESTED``
    minutes present in the derived Phase 35 raw store. This is what the research
    phases can see. It is a subset of ``CAPTURED``: the ingest is a separate
    manual step and can lag by days, which looks exactly like a capture outage
    to anything reading ``opportunity.db``.

**Read-only, always.** Nothing here opens a file for writing, changes a setting
the live engine reads, or touches the capture path. It parses the journal the
capture already wrote and prints numbers. It cannot repair coverage; it can only
say where the missing minutes went, which is the part that was guesswork.

**No interpolation, no assumption of continuity.** A minute with no row is
missing, not "probably fine". A gap is reported with its clock times so it can
be recognised — a 570-minute hole that ends at 09:00 IST is an overnight
shutdown, four 9-minute holes an hour apart are something else entirely.
"""
from __future__ import annotations

from app.research.phase17.store import (
    COVERAGE_INTERVAL_SEC,
    LIVENESS_INTERVAL_SEC,
    PROCESS_INTERVAL_SEC,
)

# Why a gap has no rows in it. Ordered by what the operator would do about it.
# Neither journal wrote in the minute: the interpreter itself was gone. This is
# the one cause restart supervision recovers, which is why it is not allowed to
# absorb the stall below.
PROCESS_DOWN = "NO_LIVENESS_AND_NO_PROCESS_LINE_THE_PROCESS_WAS_NOT_RUNNING"
ALIVE_NOT_CAPTURED = "ALIVE_IN_THE_GAP_THE_CAPTURE_RAN_AND_WROTE_NOTHING_HERE"
NO_HEARTBEAT_FILE = "NO_HEARTBEAT_JOURNAL_TO_DECIDE_WITH"

# ALIVE_NOT_CAPTURED above says the capture process was up. It does NOT say the
# tick ran for THIS instrument, because the journal it is read from pools every
# name: a minute spent ticking NIFTY and never reaching CRUDEOIL looks exactly
# like a minute CRUDEOIL was ticked in and refused silently. Those are opposite
# faults — one is the scan's reach, one is the capture path — and the liveness
# journal names the instrument, so the two can be separated by measurement
# instead of by the inference an earlier build printed as if it were evidence.
SCAN_MISSED_IT = (
    "ALIVE_BUT_NO_TICK_RAN_FOR_THIS_INSTRUMENT_THE_SCAN_NEVER_REACHED_IT"
)
TICK_RAN_NO_ROW = (
    "THE_TICK_RAN_FOR_THIS_INSTRUMENT_AND_WROTE_NEITHER_A_ROW_NOR_A_REASON"
)

# The same silence, when all that is available is the success-only heartbeat.
# It is NOT downtime: that heartbeat is written after an observation persists,
# so a process that is up and capturing nothing at all — an expired broker
# session, a dead feed, a persistence failure — writes exactly as few beats as
# a process that is dead. Calling it downtime sends the operator to restart
# supervision for a fault supervision cannot touch, which is the misdiagnosis
# this package exists to prevent, and an earlier build of this very module made
# it in its own label.
NOT_EVIDENCED = "NO_HEARTBEAT_AND_NO_ROW_EITHER_DOWN_OR_CAPTURING_NOTHING"

# The tick path did not run AND the process journal says the interpreter was up
# through it. A stall, not downtime: restart supervision would have restarted
# nothing, because nothing had exited. Split out because the fix is the opposite
# one — find what wedged the loop.
TICK_STALLED = "PROCESS_WAS_UP_BUT_THE_TICK_PATH_DID_NOT_RUN_A_STALL_NOT_DOWNTIME"

# The tick path did not run and there is no process journal for that session to
# say whether the interpreter was alive. The honest label for every session
# captured before the process journal shipped: a dead process and a wedged loop
# leave the same absence of liveness lines, and PROCESS_DOWN above claims more
# than that absence supports.
DOWN_OR_STALLED = "NO_LIVENESS_LINE_AND_NO_PROCESS_JOURNAL_DOWN_OR_STALLED_UNDECIDED"

# Not a gap and not a fault: the exchange was shut, so there is no session to
# be short of. Saturdays and Sundays are the part of the calendar that is known
# for certain without a holiday list, and they were being graded as sessions: a
# closed Saturday read as 0.0% coverage with 871 minutes charged to PROCESS_DOWN
# above, and that one non-day set "worst 0.0%" for the whole summary.
WEEKEND = "SATURDAY_OR_SUNDAY_THE_EXCHANGE_WAS_SHUT_THERE_IS_NO_SESSION"

# A weekday with no observation and no journal line anywhere inside the session
# window. An exchange holiday and a capture that was down for the entire day
# leave identical evidence — none — so neither may be claimed. Reported rather
# than scored, and kept out of the median either way.
NO_SESSION_EVIDENCE = (
    "NO_ROW_AND_NO_JOURNAL_LINE_ALL_SESSION_HOLIDAY_OR_A_FULL_DAY_OUTAGE_UNDECIDED"
)

# Why a session carries no coverage percentage.
UNGRADED: tuple[str, ...] = (WEEKEND, NO_SESSION_EVIDENCE)

CAUSES: tuple[str, ...] = (
    PROCESS_DOWN, TICK_STALLED, DOWN_OR_STALLED, ALIVE_NOT_CAPTURED,
    SCAN_MISSED_IT, TICK_RAN_NO_ROW, NOT_EVIDENCED, NO_HEARTBEAT_FILE)

# Where a gap sits relative to the instrument's own exchange session. The edges
# are kept apart from the interior because they usually mean "started late" and
# "stopped early", which is a different conversation from "dropped out midway".
BEFORE_FIRST = "BEFORE_THE_FIRST_CAPTURED_MINUTE"
INSIDE = "INSIDE_THE_CAPTURED_SPAN"
AFTER_LAST = "AFTER_THE_LAST_CAPTURED_MINUTE"

# A gap shorter than this is not worth a line of report. One minute is the
# resolution of the whole exercise, and a single missed minute is ordinary tick
# scheduling rather than an outage.
MIN_REPORTED_GAP_MIN = 2

# How far apart two heartbeats may be and still be read as one continuous run,
# expressed as the reach of a single beat in minutes. The journal is rate-limited
# to one line per interval, so a running capture cannot go longer than that
# without writing one; two beats within twice this span bracket the minutes
# between them, and a minute outside any such bracket has no evidence of a
# running process and must not borrow one.
HEARTBEAT_REACH_MIN = max(1, int(COVERAGE_INTERVAL_SEC // 60))

# The liveness journal is written at least twice a minute, so its evidence is
# that much sharper: a hole longer than a couple of minutes is downtime
# outright, and no minute the tick ran in can be empty on a rate limit.
LIVENESS_REACH_MIN = max(1, int(LIVENESS_INTERVAL_SEC // 60))

# Which journal a session's aliveness was decided from. Reported per session,
# because the liveness journal starts the day it ships: every session captured
# before that can only be judged by the weaker heartbeat, and a report that hid
# the difference would present the two as equally well evidenced.
FROM_LIVENESS = "LIVENESS_JOURNAL_THE_TICK_PATH_WROTE_A_LINE_EVERY_MINUTE"
FROM_HEARTBEAT = "COVERAGE_HEARTBEAT_ONLY_WHICH_FIRES_AFTER_A_PERSISTED_ROW"
FROM_NOTHING = "NO_JOURNAL_FOR_THIS_SESSION"

# The process journal is written every half minute, so a minute without one is
# a minute no running interpreter can account for.
PROCESS_REACH_MIN = max(1, int(round(PROCESS_INTERVAL_SEC / 60.0)) or 1)

# Whether a session has the process journal behind it, and so whether its
# tick-path silence could be decided at all.
WITH_PROCESS = "PROCESS_JOURNAL_A_LINE_EVERY_MINUTE_FROM_A_THREAD_OF_ITS_OWN"
WITHOUT_PROCESS = "NO_PROCESS_JOURNAL_FOR_THIS_SESSION"
