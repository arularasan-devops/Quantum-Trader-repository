"""Capture-coverage smoke — the properties that make the number trustworthy.

Each check exists because its absence has already misled this project, or would:

* a coverage percentage measured against the wrong exchange's session, which
  reports an MCX day that stopped at 15:30 as fully captured;
* a gap with no cause attached, which is how "the app was down" and "the app
  was up and skipped this instrument" were treated as one problem for months;
* a gap the capture was alive through and had no refusal to write down — the
  scan never reaching the instrument — counted as if it had been explained;
* a diagnostic that writes. Anything in this package that opens a journal for
  appending would contaminate the evidence it is measuring;
* a reason journal that carries a price, a quote or a decision, which a later
  phase could read as an observation;
* the derived store confused with the capture: minutes that exist on disk but
  were never ingested look exactly like minutes that were never captured, and
  the fix for each is different.

    .venv/bin/python _smoke_capture_health.py
"""
from __future__ import annotations

import asyncio
import gzip
import inspect
import json
import os
import sqlite3
import sys
import tempfile
import time

from app.config import settings
from app.research.capture_health import freshness
from app.research.capture_health import (
    ALIVE_NOT_CAPTURED,
    DOWN_OR_STALLED,
    FROM_HEARTBEAT,
    FROM_LIVENESS,
    HEARTBEAT_REACH_MIN,
    LIVENESS_REACH_MIN,
    NO_HEARTBEAT_FILE,
    NO_SESSION_EVIDENCE,
    NOT_EVIDENCED,
    PROCESS_DOWN,
    SCAN_MISSED_IT,
    TICK_RAN_NO_ROW,
    TICK_STALLED,
    WEEKEND,
    WITH_PROCESS,
    WITHOUT_PROCESS,
    coverage,
    report,
)
from app import main as app_main
from app.research.phase17 import service as p17service, store as p17store

PASS = 0
FAIL: list[str] = []


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


def minute_of(session: str, hhmm: str) -> int:
    """The epoch minute of an IST wall-clock time on a session date."""
    import datetime as dt

    day = dt.datetime.strptime(session, "%Y-%m-%d")
    hour, mins = (int(x) for x in hhmm.split(":"))
    base = int(dt.datetime(day.year, day.month, day.day,
                           tzinfo=dt.timezone.utc).timestamp())
    return (base - coverage.IST_OFFSET_SEC + (hour * 60 + mins) * 60) // 60


def write_observations(path: str, instrument: str, minutes: list[int],
                       per_minute: int = 1) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for minute in minutes:
            for n in range(per_minute):
                fh.write(json.dumps({
                    "observation_id": f"{instrument}-{minute}-{n}",
                    "signal_ts": minute * 60 + n,
                    "capture_ts": minute * 60 + n,
                    "instrument": instrument,
                    "session": coverage.session_of(minute * 60),
                }) + "\n")


def write_lines(path: str, rows: list[dict]) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def main() -> int:
    session = "2026-09-10"
    tmp = tempfile.mkdtemp(prefix="capture_health_smoke_")
    original_dir = settings.data_dir
    settings.data_dir = tmp
    try:
        return run(session, tmp)
    finally:
        settings.data_dir = original_dir
        p17store.reset_health()


def run(session: str, tmp: str) -> int:
    obs_path = os.path.join(tmp, p17store.OBSERVATIONS)
    hb_path = os.path.join(tmp, p17store.COVERAGE)
    gap_path = os.path.join(tmp, p17store.GAPS)

    # 09:00-09:59 captured densely, 10:00-10:59 missing entirely, 11:00 on
    # captured again. The instrument is MCX, so the session runs to 23:30.
    captured = (
        [minute_of(session, "09:00") + i for i in range(60)]
        + [minute_of(session, "11:00") + i for i in range(30)]
    )
    write_observations(obs_path, "CRUDEOIL", captured, per_minute=3)
    # A different instrument in the same file, to prove the filter works.
    write_observations(obs_path, "NIFTY",
                       [minute_of(session, "10:00") + i for i in range(30)])

    # --------------------------------------------- the window is per exchange
    window = coverage.session_window("CRUDEOIL", session)
    ok(window is not None and window[1] - window[0] + 1 == 871,
       "an MCX instrument is measured against the MCX session, not the "
       "equity one")
    equity = coverage.session_window("NIFTY", session)
    ok(equity is not None and window is not None
       and equity[1] - equity[0] < window[1] - window[0],
       "and an NSE instrument against the shorter equity session")

    # --------------------------------------------- minutes, not rows
    minutes = coverage.captured_minutes("CRUDEOIL", paths=[obs_path])
    ok(len(minutes.get(session, {})) == 90,
       "90 captured minutes are counted once each, not 270 times for their rows")
    ok(sum(minutes[session].values()) == 270,
       "while the row count is kept, because one row a minute and forty are "
       "different captures")
    other = coverage.captured_minutes("NIFTY", paths=[obs_path])
    ok(len(other.get(session, {})) == 30,
       "another instrument's rows in the same file are not counted as this "
       "instrument's coverage")

    # --------------------------------------------- gaps and their causes
    row = coverage.measure_session(
        "CRUDEOIL", session, minutes[session], alive=None, ingested=None)
    ok(row["coverage_pct"] == round(100.0 * 90 / 871, 1),
       "coverage is captured minutes over the instrument's own session length")
    ok(any(g["minutes"] == 60 and g["from"] == "10:00" for g in row["gaps"]),
       "the missing hour is reported as one 60-minute gap with its clock times")
    ok(all(g["cause"] == NO_HEARTBEAT_FILE for g in row["gaps"]),
       "with no heartbeat journal the cause is refused, not guessed at")

    # Two heartbeats one interval apart at 10:20 and 10:25, and a lone one at
    # 10:50 with nothing near it.
    write_lines(hb_path, [
        {"ts": minute_of(session, "10:20") * 60, "instrument": "NIFTY"},
        {"ts": minute_of(session, "10:25") * 60, "instrument": "NIFTY"},
        {"ts": minute_of(session, "10:50") * 60, "instrument": "NIFTY"},
    ])
    alive = coverage.alive_minutes(paths=[hb_path])
    row = coverage.measure_session(
        "CRUDEOIL", session, minutes[session],
        alive=alive.get(session, set()), ingested=None)
    inside_hour = [g for g in row["gaps"] if "10:00" <= g["from"] <= "10:59"]
    ok(sum(g["minutes"] for g in inside_hour) == 60,
       "the split parts of a gap tile it exactly, so no minute is dropped or "
       "counted twice")
    ok(any(g["cause"] == ALIVE_NOT_CAPTURED and g["from"] == "10:20"
           and g["minutes"] == 6 for g in inside_hour),
       "minutes bracketed by two heartbeats an interval apart had a running "
       "capture on both sides, so that stretch is not downtime")
    ok(row["missing_alive_not_captured"] == 7,
       "the lone heartbeat proves only its own minute — one heartbeat cannot "
       "certify the hour it happens to sit inside as time the capture was up")
    ok(sum(1 for g in inside_hour if g["cause"] == NOT_EVIDENCED) == 3
       and row["missing_down_or_stalled"] > 0
       and row["missing_process_down"] == 0,
       "the unproven minutes are reported as their own stretches rather than "
       "absorbed into the alive one")
    ok(all(g["cause"] != PROCESS_DOWN for g in row["gaps"])
       and row["down_cause"] == NOT_EVIDENCED,
       "and are NOT called downtime on heartbeat evidence: that heartbeat "
       "fires only after a row persists, so an up process capturing nothing "
       "leaves the same silence as a dead one")
    span = coverage.alive_span(alive[session])
    ok(minute_of(session, "10:22") in span
       and minute_of(session, "10:26") not in span
       and minute_of(session, "10:51") not in span,
       "aliveness is never claimed forward from the last heartbeat, only "
       "between two of them")
    ok(len(coverage.alive_span(set(), reach=HEARTBEAT_REACH_MIN)) == 0,
       "and an empty heartbeat journal proves no minute alive")

    # --------------------------------------------- the reason, when recorded
    write_lines(gap_path, [
        {"ts": (minute_of(session, "10:00") + i) * 60,
         "session": session, "instrument": "CRUDEOIL",
         "reason": p17store.NO_OPTION_CHAIN}
        for i in range(20)
    ])
    reasons = coverage.gap_reasons("CRUDEOIL", paths=[gap_path])
    ok(reasons is not None and len(reasons.get(session, {})) == 20,
       "the reason journal is read at minute resolution")
    row = coverage.measure_session(
        "CRUDEOIL", session, minutes[session],
        alive=alive.get(session, set()),
        ingested=None, reasons=reasons[session] if reasons else None)
    ok(row["reasons"].get(p17store.NO_OPTION_CHAIN) == 20,
       "an explained minute names the refusal the capture actually recorded")
    ok(row["unexplained_alive_minutes"] == 7,
       "the alive minutes with no refusal recorded are counted apart — the "
       "tick never reached the instrument, which the feed cannot explain")
    ok(row["unexplained_alive_minutes"] < 60,
       "and a refusal recorded in a downtime stretch is never credited to the "
       "tick path, which is what made this number 665 on a 59-heartbeat day")
    ok(coverage.gap_reasons("CRUDEOIL", paths=[]) is None,
       "no reason journal returns None, never an empty tally that would read "
       "as 'there was no reason'")

    # --------------------------------------------- captured is not ingested
    db_path = os.path.join(tmp, "opportunity.db")
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE raw_observation (session TEXT, ts REAL, "
                "instrument TEXT)")
    con.executemany(
        "INSERT INTO raw_observation (session, ts, instrument) VALUES (?, ?, ?)",
        [(session, m * 60, "CRUDEOIL") for m in captured[:10]])
    con.commit()
    con.close()
    ingested = coverage.ingested_minutes(db_path, "CRUDEOIL")
    ok(ingested is not None and len(ingested.get(session, set())) == 10,
       "the derived store is read separately from the capture journal")
    row = coverage.measure_session(
        "CRUDEOIL", session, minutes[session], alive=None,
        ingested=ingested[session] if ingested else None)
    ok(row["not_ingested_minutes"] == 80,
       "80 captured minutes never reached the research store, which is an "
       "ingest lag and not a capture gap")
    ok(coverage.ingested_minutes(os.path.join(tmp, "absent.db"), "CRUDEOIL")
       is None,
       "a missing store returns None rather than zero ingested minutes")

    # --------------------------------------------- the gap journal is not evidence
    p17store.reset_health()
    wrote = p17store.note_gap("CRUDEOIL", p17store.NO_OPTION_CHAIN,
                              now=time.time())
    ok(wrote, "the capture can record why it captured nothing")
    again = p17store.note_gap("CRUDEOIL", p17store.NO_OPTION_CHAIN,
                              now=time.time())
    ok(not again,
       "and is rate-limited per instrument and reason, so a per-tick refusal "
       "cannot outnumber the observations whose absence it explains")
    third = p17store.note_gap("CRUDEOIL", p17store.NO_CONTRACT, now=time.time())
    ok(third,
       "a different reason for the same instrument is a different finding and "
       "is not suppressed")
    with open(p17store.path(p17store.GAPS), encoding="utf-8") as fh:
        recorded = [json.loads(line) for line in fh if line.strip()]
    forbidden = ("bid", "ask", "premium", "price", "strike", "decision",
                 "plan", "atr")
    ok(all(not any(key in row for key in forbidden) for row in recorded),
       "a gap row carries no price, quote or decision, so no phase can read "
       "it as an observation")
    ok(all(set(row) <= {"ts", "session", "instrument", "reason", "provider"}
           for row in recorded),
       "and carries nothing beyond when, which name, why and which provider")

    # --------------------------------------------- the liveness journal
    p17store.reset_health()
    # On a minute boundary: these fixtures assert which minute BUCKET a line
    # lands in, and a base at :59 would put base+1 in the next one.
    base = time.time() // 60 * 60
    ok(p17store.note_liveness("CRUDEOIL", now=base),
       "the tick path records that it ran at all, before anything can refuse "
       "the row")
    ok(not p17store.note_liveness("CRUDEOIL", now=base + 10),
       "rate-limited, so a wide universe costs a couple of lines a minute per "
       "name and not one per tick")
    ok(p17store.LIVENESS_INTERVAL_SEC <= 30,
       "and limited to HALF a minute: lines a full 60s apart drift over the "
       "boundary and leave a bucket empty while the tick was running all "
       "through it, which the report would charge to a stall or to the scan")
    ok(p17store.note_liveness("NIFTY", now=base + 1),
       "the limit is PER INSTRUMENT: a global one wrote a line a minute "
       "carrying whichever of 46 names won the race, which made the absence "
       "of a name mean nothing at all")
    ok(not p17store.note_liveness("NIFTY", now=base + 10)
       and p17store.note_liveness("CRUDEOIL", now=base + 61),
       "and each name is limited on its own clock, so one busy instrument "
       "cannot silence another's line")
    with open(p17store.path(p17store.LIVENESS), encoding="utf-8") as fh:
        beats = [json.loads(line) for line in fh if line.strip()]
    ok(all(b.get("scope") == p17store.LIVENESS_PER_NAME for b in beats),
       "every row says which limiter wrote it, because rows from the global "
       "one look identical and their silence cannot be read the same way")
    ok(all(set(b) <= {"ts", "session", "instrument", "tick_ran", "scope"}
           for b in beats),
       "a liveness row carries no price, quote or decision, so no phase can "
       "read it as an observation")
    live_src = inspect.getsource(p17service.observe)
    ok(live_src.index("note_liveness") < live_src.index("capture.eligible"),
       "it is written before eligibility, sampling and the build, all of "
       "which can refuse a row the process was up for")

    live = coverage.alive_minutes(paths=[p17store.path(p17store.LIVENESS)])
    live_session = coverage.session_of(base)
    ok(len(live.get(live_session, set())) == 2,
       "the diagnostic reads the liveness journal at minute resolution")
    strong = coverage.measure_session(
        "CRUDEOIL", live_session, {}, alive=live[live_session], ingested=None,
        reach=LIVENESS_REACH_MIN, alive_source=FROM_LIVENESS)
    ok(strong["alive_source"] == FROM_LIVENESS
       and strong["alive_reach_min"] == LIVENESS_REACH_MIN,
       "and reports which journal decided aliveness, because a minute-by-"
       "minute one and a five-minute one support different claims")
    ok(LIVENESS_REACH_MIN < HEARTBEAT_REACH_MIN,
       "the liveness journal is the sharper evidence of the two")

    # ------------------------- the process journal: gone, or up and wedged
    p17store.reset_health()
    ok(p17store.note_process_alive(now=base),
       "the process records that it is up, from a thread that is not the tick's")
    ok(not p17store.note_process_alive(now=base + 5),
       "rate-limited, so a session costs a couple of thousand short lines")
    with open(p17store.path(p17store.PROCESS), encoding="utf-8") as fh:
        beats = [json.loads(line) for line in fh if line.strip()]
    ok(all(set(b) <= {"ts", "session", "process_alive"} for b in beats),
       "a process row carries no instrument, price or decision — it is "
       "evidence of an interpreter, not of a market")
    ok("instrument" not in inspect.signature(p17store.note_process_alive)
       .parameters,
       "and cannot be attributed to an instrument, because a wedged loop is "
       "not a fact about any one name")
    hb_src = inspect.getsource(p17service._heartbeat_loop)
    ok("note_process_alive" in hb_src
       and "observe" not in hb_src
       and p17service._HEARTBEAT_TICK_SEC <= p17store.PROCESS_INTERVAL_SEC,
       "the writer is its own loop, so it keeps writing when the tick path "
       "has stopped — which is the only case it exists for")

    # ------------------------- the stall witness: WHERE the loop was
    p17store.reset_health()
    ok(not p17store.note_stall(now=base, mono=1000.0),
       "a process that has never ticked is not a stall: there is nothing to "
       "say the loop was ever getting on with it")
    p17store.note_tick_begin("SILVER", mono=1000.0, now=base)
    ok(not p17store.note_stall(now=base + 1, mono=1001.0),
       "a tick in flight for a second is a tick, not a wedge")
    ok(p17store.note_stall(now=base + 40, mono=1040.0),
       "a tick that has not returned well inside the shortest reportable gap "
       "is the finding: 13 minutes of THE_TICK_PATH_DID_NOT_RUN named no "
       "instrument and no line of code to go and look at")
    ok(not p17store.note_stall(now=base + 50, mono=1050.0),
       "rate-limited while the same wedge persists")
    ok(p17store.note_stall(now=base + 80, mono=1080.0),
       "and written again as it gets older, so a 3-minute wedge cannot read "
       "as a 20-second one")
    p17store.note_tick_end(mono=1100.0)
    ok(p17store.note_stall(now=base + 140, mono=1140.0),
       "a loop that is scheduling no ticks at all stalls too, and that is a "
       "different fault from one instrument's tick not returning")
    with open(p17store.path(p17store.STALLS), encoding="utf-8") as fh:
        wedges = [json.loads(line) for line in fh if line.strip()]
    ok([w["where"] for w in wedges] == [
        p17store.STUCK_IN_TICK, p17store.STUCK_IN_TICK, p17store.STUCK_BETWEEN],
       "the witness says which of the two it saw")
    ok([w["instrument"] for w in wedges] == ["SILVER", "SILVER", None],
       "and names the instrument the loop went into, which is the whole point "
       "— a wedge in SILVER's tick is why CRUDEOIL has a hole")
    ok(all(set(w) <= {"ts", "session", "instrument", "where", "stuck_sec",
                      "since_ts"} for w in wedges)
       and all(not any(k in w for k in ("bid", "ask", "premium", "decision"))
               for w in wedges),
       "a stall row carries no price, quote or decision, so no phase can read "
       "it as an observation")
    wedge_src = inspect.getsource(p17service._heartbeat_loop)
    ok("note_stall" in wedge_src,
       "written from the heartbeat thread, which is still running in exactly "
       "the case the tick path cannot describe")
    main_src = inspect.getsource(app_main.Hub._tick_one)
    ok("note_tick_begin" in main_src and "note_tick_end" in main_src
       and "finally" in main_src,
       "the tick path only drops an in-memory breadcrumb, and ends it in a "
       "finally so a raising tick cannot read as a permanent wedge")

    # ---------------- the loop stops waiting: a wedge costs one name, not 46
    p17store.reset_health()
    ok(p17store.note_abandoned_tick("CRUDEOIL", stuck_sec=15.2),
       "an abandoned tick is journalled where it happens: a tick the loop "
       "waited out and one it gave up on are different events, and a hole "
       "cannot be told apart from either afterwards")
    with open(p17store.path(p17store.STALLS), encoding="utf-8") as fh:
        gaveup = [json.loads(line) for line in fh if line.strip()]
    ok(gaveup[-1]["where"] == p17store.TICK_ABANDONED
       and gaveup[-1]["instrument"] == "CRUDEOIL"
       and gaveup[-1]["stuck_sec"] == 15.2,
       "beside the stall witness, naming the instrument and what the wait "
       "cost")
    ok(all(set(row) <= {"ts", "session", "instrument", "where", "stuck_sec",
                        "since_ts"} for row in gaveup),
       "and carrying no price, quote or decision, so it is a diagnostic and "
       "never an observation")

    class _Snap:
        decision = "WAIT"

        def model_dump(self, mode: str | None = None) -> dict:
            return {"decision": self.decision}

    class _Stuck:
        instrument = "SILVER"

        def tick(self):
            time.sleep(30.0)
            raise AssertionError("the loop must not have waited for this")

    class _Fine:
        instrument = "NIFTY"

        def tick(self):
            return _Snap()

    async def _drive() -> tuple[float, dict, int, int]:
        hub = app_main.Hub()
        loop = asyncio.get_running_loop()
        began = time.monotonic()
        await hub._tick_one(_Stuck(), loop)
        wedged_after = hub.ticks_wedged_now
        await hub._tick_one(_Fine(), loop)
        return (time.monotonic() - began, dict(hub.latest),
                hub.ticks_abandoned, wedged_after)

    p17store.reset_health()
    budget = p17store.TICK_BUDGET_SEC
    p17store.TICK_BUDGET_SEC = 0.3
    try:
        waited, latest, abandoned, wedged_after = asyncio.run(_drive())
    finally:
        p17store.TICK_BUDGET_SEC = budget
    ok(waited < 5.0 and abandoned == 1,
       "a tick that blocks for 30s holds the loop for the budget and no "
       "longer — the journal's 108-second wedge cost every one of 46 names "
       "those minutes, because the loop is serial")
    ok("SILVER" not in latest,
       "and no snapshot is cached from it: a quote the loop stopped waiting "
       "for describes a market that has moved on, and a late price presented "
       "as current is worse than a hole the coverage report can see")
    ok("NIFTY" in latest,
       "while the next instrument ticks normally, which is the whole repair")
    ok(wedged_after == 1,
       "the abandoned thread is still counted as held, because a blocking "
       "read cannot be cancelled and pretending otherwise would overrun the "
       "pool silently")
    tick_src = inspect.getsource(app_main.Hub._tick_one)
    ok("TICK_WORKERS_WEDGED" in tick_src
       and "_TICK_WORKERS" in tick_src,
       "and when every worker is held the tick is refused outright rather "
       "than queued behind the wedges, which would reproduce the stall with "
       "the journals reading normally")
    ok("self._tick_pool" in tick_src and app_main.Hub._TICK_WORKERS > 1,
       "ticks run in their own bounded pool, so a wedged provider call does "
       "not queue the nightly snapshot or a report behind it")

    p17store.reset_health()
    seen = coverage.stalls(paths=[p17store.path(p17store.STALLS)])
    stall_session = coverage.session_of(base)
    ok(len(seen.get(stall_session, {})) >= 2,
       "the diagnostic reads the witness at minute resolution")
    # A session captured to 09:59, a hole, and back at 10:20 — with the process
    # journal up throughout, so the hole is a stall and the witness has
    # something to describe.
    held = {m: 1 for m in range(minute_of(session, "09:00"),
                                minute_of(session, "10:00"))}
    held[minute_of(session, "10:20")] = 1
    held_up = set(range(minute_of(session, "09:00"),
                        minute_of(session, "11:00")))
    witnessed = coverage.measure_session(
        "CRUDEOIL", session, held, alive=set(held), ingested=None,
        reach=LIVENESS_REACH_MIN, alive_source=FROM_LIVENESS,
        process=held_up,
        witness={minute_of(session, "10:05"): {
            "where": p17store.STUCK_IN_TICK, "instrument": "SILVER",
            "stuck_sec": 92.0}},
        now=minute_of(session, "10:21") * 60)
    ok(witnessed["witness"]
       and witnessed["witness"][0]["instrument"] == "SILVER"
       and witnessed["witness"][0]["worst_sec"] == 92.0,
       "and carries it onto the session that has the hole, so the stall names "
       "a name instead of a category")
    ok(witnessed["witnessed_minutes"] == 1
       and witnessed["missing_tick_stalled"] == 20,
       "the witness explains one minute of a 20-minute stall and says so — it "
       "writes on its own timer and is not a second measure of the hole")
    blind = coverage.measure_session(
        "CRUDEOIL", session, held, alive=set(held), ingested=None,
        reach=LIVENESS_REACH_MIN, alive_source=FROM_LIVENESS, process=held_up,
        now=minute_of(session, "10:21") * 60)
    ok(blind["witness"] == [] and blind["witnessed_minutes"] is None,
       "a session the witness never ran in reports that it was not watching, "
       "not that the loop was fine")

    # A 20-minute hole with no liveness line. The process journal decides it.
    # Captured from the open to 09:59, nothing for 20 minutes, back at 10:20.
    covered = {m: 1 for m in range(minute_of(session, "09:00"),
                                   minute_of(session, "10:00"))}
    covered[minute_of(session, "10:20")] = 1
    ticks = set(covered)
    up_all = set(range(minute_of(session, "09:00"), minute_of(session, "11:00")))
    stalled = coverage.measure_session(
        "CRUDEOIL", session, covered, alive=ticks, ingested=None,
        reach=LIVENESS_REACH_MIN, alive_source=FROM_LIVENESS, process=up_all,
        now=minute_of(session, "10:21") * 60)
    hole = [g for g in stalled["gaps"] if g["from"] == "10:00"]
    ok(hole and all(g["cause"] == TICK_STALLED for g in hole)
       and stalled["missing_tick_stalled"] == 20
       and stalled["missing_process_down"] == 0,
       "a process that kept writing through a silent tick path is a STALL: "
       "restart supervision would have found nothing to restart")
    gone = coverage.measure_session(
        "CRUDEOIL", session, covered, alive=ticks, ingested=None,
        reach=LIVENESS_REACH_MIN, alive_source=FROM_LIVENESS,
        process=ticks, now=minute_of(session, "10:21") * 60)
    ok(all(g["cause"] == PROCESS_DOWN
           for g in gone["gaps"] if g["from"] == "10:00")
       and gone["missing_process_down"] == 20
       and gone["missing_tick_stalled"] == 0,
       "and a process journal silent through the same hole is the interpreter "
       "being gone, which is the one case supervision recovers")
    undecided = coverage.measure_session(
        "CRUDEOIL", session, covered, alive=ticks, ingested=None,
        reach=LIVENESS_REACH_MIN, alive_source=FROM_LIVENESS, process=None,
        now=minute_of(session, "10:21") * 60)
    ok(all(g["cause"] == DOWN_OR_STALLED
           for g in undecided["gaps"] if g["from"] == "10:00")
       and undecided["missing_process_down"] == 0
       and undecided["missing_down_or_stalled"] == 20,
       "without the process journal the same hole is UNDECIDED, not proven "
       "downtime: an earlier report called 77 such minutes proven down")
    ok(undecided["process_source"] == WITHOUT_PROCESS
       and stalled["process_source"] == WITH_PROCESS,
       "and each session says whether the journal was there to decide with")
    for row in (stalled, gone, undecided):
        ok(sum(g["minutes"] for g in row["gaps"]) == row["missing_minutes"]
           and row["missing_tick_not_running"]
           + row["missing_alive_not_captured"] == row["missing_minutes"],
           "the three-way split still tiles the missing minutes exactly")
    text = report.render({
        "instrument": "CRUDEOIL", "sessions": [stalled], "session_count": 1,
        "graded_session_count": 0, "process_available": True,
        "usage": {}})
    ok(TICK_STALLED in text and "stalled while up" in text,
       "and the report names a stall as a stall rather than as downtime")

    # ------------- alive, and whether the tick reached THIS name in it
    # Pooled, a liveness line says the loop ran. Named, it says the loop ran
    # HERE. Both leave an empty minute for this instrument, and the fixes are
    # opposite ones, so the split is measured rather than assumed.
    live_path = [p17store.path(p17store.LIVENESS)]
    got = coverage.reached_minutes("CRUDEOIL", paths=live_path)
    ok(got is not None
       and len(got.get(live_session, set())) == 2
       and coverage.reached_minutes(
           "NIFTY", paths=live_path).get(live_session) != got.get(live_session),
       "the liveness journal names the instrument, so the minutes the tick "
       "ran FOR ONE NAME can be read out of the same file the pooled "
       "aliveness comes from")
    ok(coverage.reached_minutes("CRUDEOIL", paths=[]) is None,
       "and with no liveness file it returns nothing rather than an empty "
       "set, because 'the tick never reached this name' is the strongest "
       "claim here and an absent file is the weakest evidence there is")
    legacy = os.path.join(tmp, "legacy_liveness.jsonl")
    with open(legacy, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": base, "session": live_session, "instrument": "NIFTY",
            "tick_ran": True}) + "\n")
    ok(coverage.reached_minutes("CRUDEOIL", paths=[legacy]) is None,
       "a row from the global limiter is refused outright: under it a minute "
       "holding only NIFTY's line says the scan reached SOMETHING, so "
       "charging CRUDEOIL's empty minute to the scan would repeat the "
       "original error with the file as the alibi")
    ok(coverage.reached_minutes("NIFTY", paths=[legacy]) is None,
       "and refused for the instrument it does name too, because one line a "
       "minute cannot show the minutes a name was NOT reached in")
    alive_through = set(range(minute_of(session, "09:00"),
                              minute_of(session, "10:21")))
    hole_min = set(range(minute_of(session, "10:00"),
                         minute_of(session, "10:20")))
    at_1021 = minute_of(session, "10:21") * 60
    unreached = coverage.measure_session(
        "CRUDEOIL", session, covered, alive=alive_through, ingested=None,
        reach=LIVENESS_REACH_MIN, alive_source=FROM_LIVENESS,
        process=up_all, reasons={}, reached=alive_through - hole_min,
        now=at_1021)
    bracketed = coverage.gaps(
        covered, (minute_of(session, "09:00"), minute_of(session, "10:21")),
        alive_through, {}, reach=LIVENESS_REACH_MIN,
        down_cause=PROCESS_DOWN, process=up_all,
        # Two lines for this name, four minutes either side of the hole.
        reached={min(hole_min) - 4, max(hole_min) + 4})
    ok(bracketed
       and all(g["cause"] == SCAN_MISSED_IT
               for g in bracketed if g["from"] == "10:00"),
       "reached minutes are NOT bracketed like the aliveness ones: a process "
       "running either side of a hole cannot have died in it, but a scan can "
       "drop a name and return to it, so filling between two lines would "
       "manufacture the very reach being measured")
    ok(all(g["cause"] == SCAN_MISSED_IT
           for g in unreached["gaps"] if g["from"] == "10:00")
       and unreached["missing_scan_missed_it"] == 20
       and unreached["missing_tick_ran_no_row"] == 0,
       "a minute with a liveness line for another name and none for this one "
       "is the scan never reaching it — no refusal was recorded because "
       "nothing was there to refuse")
    dropped = coverage.measure_session(
        "CRUDEOIL", session, covered, alive=alive_through, ingested=None,
        reach=LIVENESS_REACH_MIN, alive_source=FROM_LIVENESS,
        process=up_all, reasons={}, reached=alive_through, now=at_1021)
    ok(all(g["cause"] == TICK_RAN_NO_ROW
           for g in dropped["gaps"] if g["from"] == "10:00")
       and dropped["missing_tick_ran_no_row"] == 20
       and dropped["missing_scan_missed_it"] == 0,
       "and a liveness line naming THIS instrument in the same hole means "
       "the tick did reach it and the row was swallowed — widening the scan "
       "would recover none of those minutes")
    pooled = coverage.measure_session(
        "CRUDEOIL", session, covered, alive=alive_through, ingested=None,
        reach=LIVENESS_REACH_MIN, alive_source=FROM_LIVENESS,
        process=up_all, reasons={}, reached=None, now=at_1021)
    ok(all(g["cause"] == ALIVE_NOT_CAPTURED
           for g in pooled["gaps"] if g["from"] == "10:00")
       and pooled["reached_minutes"] is None,
       "without the named journal the verdict stays the vaguer pooled one "
       "instead of guessing which of the two it was")
    for row in (unreached, dropped, pooled):
        ok(row["missing_alive_not_captured"] == 20
           and row["missing_tick_not_running"]
           + row["missing_alive_not_captured"] == row["missing_minutes"],
           "all three still total the same 20 alive-and-empty minutes, so "
           "the finer split cannot change the coverage arithmetic")
    ok(coverage._FIX_OF[SCAN_MISSED_IT] == coverage.FIX_SCAN_REACH
       and coverage._FIX_OF[TICK_RAN_NO_ROW] == coverage.FIX_CAPTURE_PATH
       and coverage.decided(SCAN_MISSED_IT)
       and coverage.decided(TICK_RAN_NO_ROW),
       "and each names its own fix: the scan's reach, or the capture path "
       "that returned without writing a row or a reason")
    split_text = report.render({
        "instrument": "CRUDEOIL", "sessions": [unreached], "session_count": 1,
        "graded_session_count": 0, "process_available": True, "usage": {}})
    ok("never ticked for this name" in split_text
       and SCAN_MISSED_IT in split_text,
       "and the session block prints the split, because 'alive and empty' "
       "was the line that sent the work to the wrong place")

    # ------------------- which cause owns the minutes, across the sessions
    # The same 20-minute hole, but on a session that has CLOSED: a rollup is
    # what decides where work goes, and an unfinished day cannot be in it.
    first_min, last_min = coverage.session_window("CRUDEOIL", session)
    whole = {m: 1 for m in range(first_min, last_min + 1)
             if not minute_of(session, "10:00") <= m
             < minute_of(session, "10:20")}
    after_close = (last_min + 2) * 60
    closed_stall = coverage.measure_session(
        "CRUDEOIL", session, whole, alive=set(whole), ingested=None,
        reach=LIVENESS_REACH_MIN, alive_source=FROM_LIVENESS,
        process=set(range(first_min, last_min + 1)), now=after_close)
    closed_gone = coverage.measure_session(
        "CRUDEOIL", session, whole, alive=set(whole), ingested=None,
        reach=LIVENESS_REACH_MIN, alive_source=FROM_LIVENESS,
        process=set(whole), now=after_close)
    # A stalled day and a day the process was gone, rolled up together: the
    # per-session blocks cannot say which of the two to work on.
    roll = coverage.rollup([closed_stall, closed_gone])
    ok(roll["sessions_rolled_up"] == 2
       and roll["missing_minutes"] == closed_stall["missing_minutes"]
       + closed_gone["missing_minutes"],
       "the rollup totals the missing minutes of the graded sessions only")
    ok(roll["by_cause"].get(TICK_STALLED) == 20
       and roll["by_cause"].get(PROCESS_DOWN) == 20,
       "and keeps the causes apart in the total, because a stall and a dead "
       "process are fixed by different work")
    ok(roll["attributed_minutes"] + roll["unattributed_minutes"]
       == roll["missing_minutes"],
       "minutes in gaps too short to be reported are counted as missing and "
       "left unattributed rather than charged to whichever cause is largest")
    only_stall = coverage.rollup([closed_stall])
    ok(only_stall["dominant_cause"] == TICK_STALLED
       and only_stall["indicated_fix"] == coverage.FIX_TICK_PATH,
       "a stall-dominated range indicates fixing the tick path — a restart "
       "recovers none of those minutes")
    only_gone = coverage.rollup([closed_gone])
    ok(only_gone["dominant_cause"] == PROCESS_DOWN
       and only_gone["indicated_fix"] == coverage.FIX_SUPERVISION,
       "and a downtime-dominated one indicates supervision, which is the "
       "only cause supervision recovers")
    closed_undecided = coverage.measure_session(
        "CRUDEOIL", session, whole, alive=set(whole), ingested=None,
        reach=LIVENESS_REACH_MIN, alive_source=FROM_LIVENESS, process=None,
        now=after_close)
    ok(coverage.rollup([closed_undecided])["dominant_cause"] == DOWN_OR_STALLED
       and coverage.rollup([closed_undecided])["indicated_fix"]
       == coverage.FIX_UNDECIDED,
       "while an undecided range indicates NO fix: the journals cannot tell "
       "a dead process from a wedged loop, and guessing picks the wrong work")
    running = coverage.measure_session(
        "CRUDEOIL", session, minutes[session], alive=None, ingested=None,
        now=minute_of(session, "10:30") * 60)
    weekend = coverage.measure_session(
        "CRUDEOIL", "2026-09-12", {minute_of("2026-09-12", "00:20"): 40},
        alive=None, ingested=None)
    ok(coverage.rollup([running])["sessions_rolled_up"] == 0
       and coverage.rollup([weekend])["sessions_rolled_up"] == 0
       and coverage.rollup([])["dominant_cause"] == coverage.NOTHING_MISSING,
       "an unfinished session, a weekend and an empty range contribute no "
       "missing minute and name no dominant cause")
    skipped = coverage.measure_session(
        "CRUDEOIL", session, minutes[session],
        alive=alive.get(session, set()),
        ingested=None, reasons=reasons[session] if reasons else None)
    with_reasons = coverage.rollup([skipped])
    ok(with_reasons["refusal_reasons"].get(p17store.NO_OPTION_CHAIN) == 20,
       "the refusals the tick actually recorded are totalled beside the "
       "causes, so a feed problem is not read as a scan problem")
    ok(with_reasons["refusal_reasons"].get(coverage.SCAN_NEVER_REACHED)
       == skipped["unexplained_alive_minutes"],
       "and alive minutes with NO recorded refusal get their own line, named "
       "for what the journal holds — no row and no reason — because which of "
       "the two tick-path faults it was is the cause split's question")
    # A day from before the journals shipped: recorded, and unattributable for
    # good. Pooled with a decidable day it owns the bigger pile of minutes.
    pre_journal = coverage.measure_session(
        "CRUDEOIL", session, {first_min: 1}, alive={first_min}, ingested=None,
        alive_source=FROM_HEARTBEAT, now=after_close)
    mixed = coverage.rollup([pre_journal, closed_stall])
    ok(mixed["dominant_cause"] == NOT_EVIDENCED
       and mixed["dominant_minutes"] > mixed["minutes_with_a_named_cause"],
       "a range reaching back before the liveness journal is dominated by "
       "minutes no evidence can ever attribute")
    ok(mixed["minutes_no_journal_can_attribute"]
       == pre_journal["missing_minutes"]
       and mixed["minutes_with_a_named_cause"] == 20
       and mixed["minutes_no_journal_can_attribute"]
       + mixed["minutes_with_a_named_cause"] == mixed["attributed_minutes"],
       "so the minutes carrying a named cause are totalled apart from the "
       "ones that never will, and the two still sum to the attributed total")
    ok(mixed["dominant_named_cause"] == TICK_STALLED
       and mixed["indicated_fix"] == coverage.FIX_TICK_PATH,
       "and the fix follows the dominant DECIDED cause: an undecidable pile "
       "no longer suppresses work that is indicated")
    only_unnamed = coverage.rollup([pre_journal])
    ok(only_unnamed["dominant_named_cause"] is None
       and only_unnamed["minutes_with_a_named_cause"] == 0
       and only_unnamed["indicated_fix"] == coverage.FIX_UNDECIDED,
       "with nothing decided in range no fix is indicated at all, because "
       "naming one there would be guessing")
    ok(coverage.decided(ALIVE_NOT_CAPTURED)
       and not coverage.decided(NOT_EVIDENCED)
       and not coverage.decided(DOWN_OR_STALLED),
       "a heartbeat minute with no row stays decided whatever journals "
       "shipped later, while gone-or-stalled never becomes decidable")
    mixed_text = report.render({
        "instrument": "CRUDEOIL", "sessions": [pre_journal, closed_stall],
        "session_count": 2, "graded_session_count": 0,
        "process_available": True,
        "where_the_minutes_went": mixed, "usage": {}})
    ok("a cause can be named for at all" in mixed_text
       and coverage.FIX_TICK_PATH in mixed_text,
       "and the report prints both totals, so an undecided headline cannot "
       "hide a cause that is decided and fixable")
    ok("no fix is indicated" in report.render({
        "instrument": "CRUDEOIL", "sessions": [pre_journal],
        "session_count": 1, "graded_session_count": 0,
        "process_available": False,
        "where_the_minutes_went": only_unnamed, "usage": {}}),
       "and says so in words when nothing in range is decidable, rather "
       "than printing no second total and looking like a missing feature")
    roll_text = report.render({
        "instrument": "CRUDEOIL", "sessions": [closed_stall],
        "session_count": 1, "graded_session_count": 0,
        "process_available": True,
        "where_the_minutes_went": only_stall, "usage": {}})
    ok("WHERE THE MISSING MINUTES WENT" in roll_text
       and coverage.FIX_TICK_PATH in roll_text,
       "and the report leads with where the minutes went and what it "
       "indicates, not with the largest single hole")

    # --------------------------------------------- a minute that has not
    # happened yet is not a missing minute
    mid = coverage.measure_session(
        "CRUDEOIL", session, minutes[session], alive=None, ingested=None,
        now=minute_of(session, "10:30") * 60)
    ok(mid["in_progress"] and mid["measured_minutes"] == 90
       and mid["measured_close"] == "10:29",
       "a session still running is measured to its last COMPLETED minute, "
       "not to a close that has not arrived")
    ok(mid["coverage_pct"] == round(100.0 * 60 / 90, 1),
       "so its coverage is graded on the part of the day that has happened — "
       "reading a 98% day mid-session as 93.5% is the same error as reading "
       "an unfinished one as failed")
    ok(all(g["to"] <= "10:29" for g in mid["gaps"])
       and mid["missing_minutes"] == 30,
       "and the hours still to come are neither gaps nor missing minutes")
    early = coverage.measure_session(
        "CRUDEOIL", session, minutes[session], alive=None, ingested=None,
        now=minute_of(session, "03:00") * 60)
    ok(early["in_progress"] and early["coverage_pct"] is None
       and not early["gaps"] and early["measured_minutes"] == 0,
       "a session that has not opened yet is not a 0.0% capture failure: "
       "reported that way, today's date looked like a total outage every "
       "time the report was run before the open")
    live_now = coverage.measure(
        "CRUDEOIL", sessions=[session], db_path=db_path,
        now=minute_of(session, "10:30") * 60)
    ok(live_now["graded_session_count"] == 0
       and live_now["median_coverage_pct"] is None
       and live_now["in_progress_sessions"] == [session],
       "and an unfinished session is kept out of the median it would "
       "otherwise drag down, while still being printed")

    # --------------------------------------------- a day the exchange was shut
    # is not a day the capture missed
    saturday = "2026-09-12"
    ok(coverage.is_weekend(saturday)
       and not coverage.is_weekend(session)
       and coverage.session_window("CRUDEOIL", saturday) is None,
       "a Saturday has no session window, because no exchange was open in it")
    shut = coverage.measure_session(
        "CRUDEOIL", saturday, {minute_of(saturday, "00:20"): 40},
        alive=None, ingested=None)
    ok(shut["coverage_pct"] is None and shut["not_graded"] == WEEKEND
       and not shut["gaps"],
       "so a weekend is reported as a non-day rather than graded 0.0% with "
       "the whole session charged to the capture — one shut Saturday was "
       "setting 'worst 0.0%' for the entire summary")
    holiday = "2026-09-14"
    blank = coverage.measure_session(
        "CRUDEOIL", holiday, {minute_of(holiday, "00:30"): 12},
        alive=set(), ingested=None, process=None,
        now=minute_of(holiday, "23:59") * 60)
    ok(blank["coverage_pct"] is None
       and blank["not_graded"] == NO_SESSION_EVIDENCE
       and not blank["gaps"],
       "and a weekday with no row, no liveness line and no process line in "
       "the whole session is left undecided: an exchange holiday and a "
       "capture that never started that day leave identical evidence")
    ran = coverage.measure_session(
        "CRUDEOIL", holiday, {minute_of(holiday, "09:00"): 3},
        alive=set(), ingested=None, process=None,
        now=minute_of(holiday, "23:59") * 60)
    ok(ran["coverage_pct"] is not None and ran["not_graded"] is None,
       "while one captured minute inside the session is enough evidence that "
       "the day traded, so the rest of it IS missing and is graded")
    # The Saturday exists in the journal only as post-midnight rows carried
    # over from the Friday session, which is exactly how it appeared in the
    # live report.
    write_observations(obs_path, "CRUDEOIL", [minute_of(saturday, "00:20")])
    summary = coverage.measure(
        "CRUDEOIL", sessions=[session, saturday], db_path=db_path)
    ok([u["session"] for u in summary["ungraded_sessions"]] == [saturday]
       and summary["graded_session_count"] == 1,
       "the summary names what it could not grade instead of silently "
       "averaging it in")
    shut_text = report.render(summary)
    ok(WEEKEND in shut_text and "the exchange was shut" in shut_text,
       "and the report says why on the session's own line")

    # ------------------------------------------ was the book live or replayed
    shut = "2026-09-12"        # a Saturday, and it holds rows on the real box
    fresh_path = os.path.join(tmp, "fresh_" + p17store.OBSERVATIONS)
    frozen_rows = []
    moving_rows = []
    for n in range(40):
        ts = minute_of(shut, "10:00") * 60 + n * 30
        frozen_rows.append({
            "observation_id": f"frozen-{n}",
            "signal_ts": ts,
            "instrument": "CRUDEOIL",
            "selected": {
                "symbol": "CRUDEOIL25SEP5800CE",
                "bid": 100.0, "ask": 101.0, "premium": 100.5,
                "data_quality": "EXACT", "source": "CHAIN",
                "book_age_ms": None,
            },
        })
        moving_rows.append({
            "observation_id": f"moving-{n}",
            "signal_ts": ts,
            "instrument": "NIFTY",
            "selected": {
                "symbol": "NIFTY25SEP25000CE",
                "bid": 100.0 + n, "ask": 101.0 + n, "premium": 100.5 + n,
                "data_quality": "EXACT", "source": "CHAIN",
                "book_age_ms": 12.0,
            },
        })
    write_lines(fresh_path, frozen_rows + moving_rows)

    audit = freshness.audit(shut, paths=[fresh_path])
    by_name = {row["instrument"]: row for row in audit["instruments"]}
    ok(audit["rows_examined"] == 80,
       "the freshness audit parses only the session it was asked about")
    ok(audit["calendar"] == freshness.CLOSED,
       "and says outright that the day was a Saturday, rows or no rows")
    ok(by_name["CRUDEOIL"]["verdict"] == freshness.FROZEN,
       "40 polls of one unchanging bid/ask is the feed replaying a shut "
       "market, not 40 executable prices")
    ok(by_name["NIFTY"]["verdict"] == freshness.LIVE,
       "a book that moves every poll is not accused of being a replay")
    ok(by_name["CRUDEOIL"]["book_age_ms_absent"] == 40,
       "and a book the feed never dated is counted, because an undated book "
       "cannot be proven fresh either")

    # A frozen strike inside an otherwise-live instrument: the case an
    # instrument-level verdict swallowed on 2026-09-15.
    mixed_path = os.path.join(tmp, "mixed.jsonl")
    dead_leg = []
    for n in range(40):
        ts = minute_of(shut, "10:00") * 60 + n * 30
        dead_leg.append({
            "observation_id": f"deadleg-{n}",
            "signal_ts": ts,
            "instrument": "NIFTY",
            "selected": {
                "symbol": "NIFTY25SEP26000CE",
                "bid": 2.0, "ask": 2.5, "premium": 2.25,
                "data_quality": "EXACT", "source": "CHAIN",
                "book_age_ms": 12.0,
            },
        })
    write_lines(mixed_path, moving_rows + dead_leg)
    mixed = freshness.audit(shut, paths=[mixed_path])
    nifty = mixed["instruments"][0]
    ok(nifty["verdict"] == freshness.MIXED and mixed["verdict"] == freshness.MIXED,
       "one moving strike no longer certifies the whole instrument: a frozen "
       "strike beside it makes the verdict MIXED, not LIVE")
    ok([f["symbol"] for f in nifty["frozen_symbols"]] == ["NIFTY25SEP26000CE"],
       "and the frozen strike is named, because that is the one a paper entry "
       "would have been priced off")
    ok(mixed["frozen_symbols"] == 1
       and "FROZEN NIFTY25SEP26000CE" in freshness.render(mixed),
       "the count and the rendered line both carry it, on its own line rather "
       "than inside an instrument summary that reads LIVE")
    ok(nifty["duplicate_rows"] == 39,
       "rows that re-recorded a book already on disk are counted, which is "
       "where the journal's size comes from")

    # A compressed rolled file is still evidence and must still be read.
    gz_path = mixed_path + ".gz"
    with open(mixed_path, "rb") as raw, gzip.open(gz_path, "wb") as out:
        out.write(raw.read())
    ok(freshness.audit(shut, paths=[gz_path]) == mixed,
       "a gzipped rolled journal reads identically to the plain one, so "
       "reclaiming disk cannot shorten the record")

    absent = freshness.audit(shut, paths=[os.path.join(tmp, "absent.jsonl")])
    ok(absent["verdict"] == freshness.NO_ROWS,
       "a session with no rows is NO_OBSERVATION_ROW, never 'the book moved'")

    few_path = os.path.join(tmp, "few.jsonl")
    write_lines(few_path, frozen_rows[:5])
    few = freshness.audit(shut, paths=[few_path])
    ok(few["instruments"][0]["verdict"] == freshness.TOO_FEW,
       "five polls decide nothing, and are reported as deciding nothing "
       "rather than as a frozen book")

    other_day = freshness.audit("2026-09-11", paths=[fresh_path])
    ok(other_day["rows_examined"] == 0 and other_day["calendar"] == freshness.OPEN,
       "rows are attributed to their own IST day, so one session's verdict "
       "cannot be built from another's rows")

    fresh_text = freshness.render(audit)
    ok(freshness.FROZEN in fresh_text
       and "not executable" in fresh_text.lower(),
       "the rendered line says the frozen rows are not executable prices")
    ok("No row is excluded or altered" in fresh_text,
       "and that the measurement changes no raw evidence")

    # --------------------------------------------- the diagnostic never writes
    for module in (coverage, report, freshness):
        src = inspect.getsource(module)
        ok('"a"' not in src and "'a'" not in src and '"w"' not in src,
           f"{module.__name__} opens no file for writing")
    ok("mode=ro" in inspect.getsource(coverage.ingested_minutes),
       "and the derived store is opened read-only, so a diagnostic run during "
       "a session cannot lock or alter it")

    # --------------------------------------------- capture wiring
    service_src = inspect.getsource(p17service.observe)
    ok(service_src.count("note_gap") == 4,
       "every path that leaves observe() without an observation records why")
    ok("store.note_gap" in service_src and "return None" in service_src,
       "the reason is written before the tick returns, not reconstructed later")
    ok("try:" in service_src.split("CAPTURE_RAISED")[0][-200:]
       or "except Exception" in service_src,
       "and the diagnostic inside the failure path cannot itself break a tick")

    # --------------------------------------------- the report says what it is
    text = report.render(coverage.measure(
        "CRUDEOIL", sessions=[session], db_path=db_path))
    ok("Read-only" in text, "the report states that it measures and cannot fix")
    ok(ALIVE_NOT_CAPTURED in text and PROCESS_DOWN in text,
       "and names both causes, so the reader cannot collapse them into "
       "'the capture was bad'")
    ok(SCAN_MISSED_IT in text and TICK_RAN_NO_ROW in text
       and "an inference, not a measurement" in text,
       "including the two the earlier build reported as one, and it says "
       "outright that reading 'alive and empty' as the scan's fault was an "
       "inference the pooled journal could not support")

    print()
    print(f"CAPTURE HEALTH SMOKE — {PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
