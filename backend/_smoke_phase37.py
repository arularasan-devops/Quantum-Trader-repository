"""Phase 37 smoke — the properties that make a session countable.

Each check exists because the opposite mistake would inflate the sample the
floors are measured against, which is worse than having no bookkeeping at all:

* treating a session whose tabs failed to fetch as a collected session, so a day
  the backend was down counts toward 20/50/60;
* overwriting an earlier snapshot on re-collection, so evidence recorded before a
  fix disappears after it;
* reading an unknown count as zero, which turns "the shape changed" into "the
  session was empty";
* summing counts that are already running totals, or reading a coverage-row
  count as a number of resolved paper trades;
* letting the rollup emit anything that reads as a result rather than as sample
  size, or promoting on a met floor;
* writing to a raw store, or reaching an order path, from the collector;
* a scheduler that back-dates a snapshot, so tonight's cumulative totals land
  in a session that had fewer of them;
* journal evidence for an un-snapshotted day being counted as a session, which
  would re-define a floor after seeing that the count is inconvenient.

    .venv/bin/python _smoke_phase37.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import contextlib
import gzip
import inspect
import io
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# Before app.config is imported: importing the app opens the history store, and
# against the real data dir that is the RUNNING app's file, which fails the
# import outright rather than merely sharing it.
os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="p37smoke-"))

from app import main as appmain  # noqa: E402
from app.research import phase37  # noqa: E402
from app.research.phase37 import (
    COLLECTED,
    DEFAULT_TIMEOUT_S,
    FAILED,
    MISSING,
    REQUIRED_TABS,
    SESSION_COMPLETE,
    SESSION_PARTIAL,
    TABS,
)
from app.research.phase37 import cli as p37cli
from app.research.phase37 import collect as p37collect
from app.research.phase37 import durable as p37durable
from app.research.phase37 import rollup as p37rollup
from app.research.phase37 import schedule as p37schedule

PASS = 0
FAIL: list[str] = []


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


# --------------------------------------------------------------------------
# A stand-in backend. Serves a payload per path, or 500 for the ones named as
# broken, so a partial session can be produced deterministically.
# --------------------------------------------------------------------------
BROKEN: set[str] = set()
# Shaped like the live payloads: the paths below were read off a running backend,
# not guessed, because a count_path that matches nothing makes every count
# UNKNOWN and the whole exercise silently pointless.
PAYLOADS: dict[str, dict] = {
    "/api/daily-best": {"every_signal": {"sessions": 3, "graded": 30}},
    "/api/phase17/paper": {
        "report": {"options": {"entries": 7}}, "open": [], "resolved": [1] * 7,
    },
    "/api/phase17/summary": {"candidates": 120},
    "/api/cas-report": {"paper": {"legs": 2}},
    "/api/phase17/evidence": {"counts": {"total": 41}, "rows": [1] * 41},
    "/api/phase19/readiness": {"calibration": {"rows": 5}, "rows": []},
    "/api/feed-health": {"status": "OK"},
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        route = self.path.split("?", 1)[0]
        if route in BROKEN:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        body = json.dumps(PAYLOADS.get(route, {})).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:  # noqa: D102 - silence the test server
        return


def main() -> int:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    base_url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    tmp = tempfile.mkdtemp(prefix="p37_")
    original_root = p37collect.root_dir
    original_backoff = p37collect.TAB_RETRY_BACKOFF_S
    p37collect.root_dir = lambda: tmp  # type: ignore[assignment]
    # The retry spacing is real seconds in production and is asserted on its
    # own below; every other case here would otherwise pay for it in wall time.
    p37collect.TAB_RETRY_BACKOFF_S = 0.0  # type: ignore[assignment]
    try:
        # --- the tab set ---------------------------------------------------
        ok(len(TABS) == 7, "seven tabs are collected")
        ok({t["tab"] for t in TABS} >= {
            "ONE_A_DAY", "A_PLUS_PAPER", "CAS", "VEHICLE_EVIDENCE", "READINESS",
        }, "the five tabs asked for are all in the collection set")
        ok("FEED_HEALTH" not in REQUIRED_TABS,
           "feed health is context, so its absence does not void a session")
        ok(all(
            p in {t["path"] for t in TABS}
            for p in ("/api/daily-best", "/api/phase17/paper", "/api/cas-report",
                      "/api/phase17/evidence", "/api/phase19/readiness")
        ), "each tab points at an endpoint that exists in main.py")
        routes = {
            r.path for r in appmain.app.routes if hasattr(r, "path")
        }
        ok(all(t["path"] in routes for t in TABS),
           "every collected path is a real route on the app")
        ok("/api/phase37/evidence" in routes,
           "the rollup is readable from the dashboard")

        # --- a complete session -------------------------------------------
        m = p37collect.collect(base_url=base_url, session="2026-09-07")
        ok(m["session_status"] == SESSION_COMPLETE,
           "all tabs present makes the session complete")
        ok(m["missing_required"] == [],
           "a complete session names no missing tab")
        ok(all(t["status"] == COLLECTED for t in m["tabs"]),
           "every tab in a complete manifest is COLLECTED")
        ok(m["research_only"] and m["paper_only"],
           "the manifest states research-only and paper-only")
        session_dir = os.path.join(tmp, "2026-09-07")
        ok(os.path.exists(os.path.join(session_dir, "a_plus_paper.json")),
           "each tab's payload is written to its own file")

        state = p37rollup.rollup(tmp)
        ok(state["sessions_complete"] == 1 and state["sessions_partial"] == 0,
           "the rollup counts one complete session")
        ok(state["resolved_paper_trades"] == 7
           and state["resolved_paper_trades_source"] == "A_PLUS_PAPER",
           "resolved paper trades come from the A+ paper book alone")
        ok(state["per_tab"]["VEHICLE_EVIDENCE"]["latest"] == 41
           and state["resolved_paper_trades"] != 41 + 7,
           "coverage counts are reported separately, never folded into trades")
        ok(all(t["cumulative"] for t in state["per_tab"].values())
           and not any("total" in t for t in state["per_tab"].values()),
           "tab counts are reported as cumulative latest values, never summed")

        # --- append-only ---------------------------------------------------
        PAYLOADS["/api/phase17/paper"] = {"report": {"options": {"entries": 9}}}
        p37collect.collect(base_url=base_url, session="2026-09-07")
        ok(os.path.exists(os.path.join(session_dir, "a_plus_paper.2.json")),
           "re-collecting writes a new file instead of replacing the first")
        with open(os.path.join(session_dir, "a_plus_paper.json"),
                  encoding="utf-8") as fh:
            first = json.load(fh)
        ok(first["payload"]["report"]["options"]["entries"] == 7,
           "the earlier snapshot still says what it said before")
        state = p37rollup.rollup(tmp)
        ok(state["rows"][0]["counts"]["A_PLUS_PAPER"] == 9,
           "the rollup reads the latest attempt")
        ok(state["sessions_complete"] == 1,
           "a re-collection is not a second session")
        ok(p37rollup._attempts(session_dir, "A_PLUS_PAPER") == [
               "a_plus_paper.json", "a_plus_paper.2.json"],
           "attempts are ordered by attempt number, not by filename")
        # ".10" sorts before ".2" as a string, so a lexical sort would read the
        # tenth snapshot of a day as the second-oldest one.
        for _ in range(8):
            p37collect.collect(base_url=base_url, session="2026-09-07")
        ok(p37rollup._attempts(session_dir, "A_PLUS_PAPER")[-1]
           == "a_plus_paper.10.json",
           "the tenth snapshot of a tab is the one the rollup reads")

        # --- a failed fetch ------------------------------------------------
        BROKEN.add("/api/cas-report")
        m = p37collect.collect(
            base_url=base_url, session="2026-09-08", backoff_s=0)
        ok(m["session_status"] == SESSION_PARTIAL,
           "a failed required tab makes the session partial")
        ok(m["missing_required"] == ["CAS"], "the manifest names which tab failed")
        cas = next(t for t in m["tabs"] if t["tab"] == "CAS")
        ok(cas["status"] == FAILED and cas.get("error"),
           "the failure is recorded with its error, not dropped")
        state = p37rollup.rollup(tmp)
        ok(state["sessions_collected"] == 2 and state["sessions_complete"] == 1,
           "a partial session is collected but does not count")
        BROKEN.clear()

        # --- an unknown count is not zero ----------------------------------
        PAYLOADS["/api/phase17/paper"] = {"report": {"shape": "changed"}}
        p37collect.collect(base_url=base_url, session="2026-09-09")
        state = p37rollup.rollup(tmp)
        row = next(r for r in state["rows"] if r["session"] == "2026-09-09")
        ok("A_PLUS_PAPER" not in row["counts"],
           "a count that cannot be found is absent, not zero")
        ok(state["resolved_paper_trades"] == 9
           and state["per_tab"]["A_PLUS_PAPER"]["latest_from_session"]
           == "2026-09-07",
           "an unknown count leaves the last known value standing, not a zero")
        ok(p37rollup._number({"report": {"n": True}}, ("report.n",)) is None,
           "a boolean is not read as a count")
        ok(p37rollup._number({"rows": [1, 2, 3]}, ("rows",)) == 3,
           "a list of rows counts as its length")
        PAYLOADS["/api/phase17/paper"] = {"report": {"options": {"entries": 9}}}

        # --- the floors ----------------------------------------------------
        ok(state["status"] == "BELOW_OBSERVATION_CHECKPOINT",
           "two sessions is below the 20-session observation checkpoint")
        ok(all(
            spec["count_paths"] or spec["tab"] == "FEED_HEALTH"
            for spec in TABS
        ), "every evidence tab names where its count lives")
        floors = state["floors"]
        ok(floors["observation_checkpoint"]["min_sessions"] == 20
           and floors["general_question"]["min_sessions"] == 50
           and floors["general_question"]["min_trades"] == 100
           and floors["relative_value_question"]["min_sessions"] == 60
           and floors["relative_value_question"]["min_trades"] == 200,
           "the floors are the ones frozen in Phase 35, not local ones")
        ok(floors["general_question"]["sessions_short_by"] == 48,
           "the rollup says how far short the sample is")
        unknown = p37rollup._floor(
            "general_question", n_sessions=99, n_trades=None)
        ok(not unknown["met"] and "TRADE_COUNT_UNKNOWN" in unknown["blocked_by"],
           "an unknown trade count cannot satisfy a trade floor")
        ok(p37rollup._floor(
            "observation_checkpoint", n_sessions=20, n_trades=None)["met"],
           "a floor with no trade requirement is met by sessions alone")

        # --- what it must never say ---------------------------------------
        blob = json.dumps(state) + p37rollup.headline(state)
        ok("VALIDATED" not in blob,
           "collecting evidence never reports anything as VALIDATED")
        for word in ("PROMOTE", "ENABLE_VEHICLE", "PRODUCTION_CANDIDATE"):
            ok(word not in blob, f"the rollup never emits {word}")
        ok(state["note"] and "never what the answer is" in state["note"],
           "the payload states the limit of what it can support")
        ok("UNKNOWN" in p37rollup.headline(
            {**state, "resolved_paper_trades": None}),
           "an unknown trade count prints as UNKNOWN, not as 0")

        # --- refusals and safety ------------------------------------------
        bad_date = False
        try:
            p37collect.collect(base_url=base_url, session="07-09-2026")
        except ValueError:
            bad_date = True
        ok(bad_date, "a malformed session date is refused, not guessed")
        bad_scheme = p37collect.fetch(
            "file:///etc", {"tab": "X", "label": "x", "path": "/passwd"})
        ok(bad_scheme["status"] == FAILED,
           "a non-http base url is refused rather than read from disk")
        offline = p37collect.collect(
            base_url="http://127.0.0.1:1", session="2026-09-10", write=False)
        ok(offline["session_status"] == SESSION_PARTIAL
           and not os.path.exists(os.path.join(tmp, "2026-09-10")),
           "a dry run writes nothing and an unreachable backend is not a crash")
        ok(p37rollup.rollup(os.path.join(tmp, "does-not-exist"))[
               "sessions_collected"] == 0,
           "no snapshots yet is zero sessions, not an error")
        missing_row = p37rollup.sessions(tmp)
        ok(all(
            r["tabs"][t]["status"] in {COLLECTED, FAILED, MISSING}
            for r in missing_row for t in r["tabs"]
        ), "every tab of every session carries an explicit status")

        # --- the nightly scheduler ----------------------------------------
        _IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

        def at(day: str, hhmm: str) -> float:
            hh, mm = (int(x) for x in hhmm.split(":"))
            y, m, d = (int(x) for x in day.split("-"))
            return dt.datetime(y, m, d, hh, mm, tzinfo=_IST).timestamp()

        os.environ["QT_PHASE37_BASE_URL"] = base_url
        ok(p37schedule.TRIGGER_IST == "23:40",
           "the snapshot fires after 23:30 IST, the last close of the day")
        ok(not p37schedule.in_window(at("2026-09-16", "15:31"))
           and not p37schedule.in_window(at("2026-09-16", "23:39"))
           and p37schedule.in_window(at("2026-09-16", "23:41")),
           "the window opens at the trigger and not before, so a mid-session"
           " snapshot cannot be filed as the session's own")
        ok(p37schedule.due(at("2026-09-16", "15:31")) is None,
           "nothing is due before the last close")
        ok(p37schedule.run_once(at("2026-09-16", "15:31")) is None
           and not os.path.exists(os.path.join(tmp, "2026-09-16")),
           "and running early writes no snapshot at all")
        ok(p37schedule.due(at("2026-09-16", "23:41")) == "2026-09-16",
           "inside the window the current session is due")
        fired = p37schedule.run_once(at("2026-09-16", "23:41"))
        ok(fired is not None and fired["session"] == "2026-09-16"
           and fired["session_status"] == SESSION_COMPLETE,
           "the scheduler collects that session by itself")
        ok(p37schedule.due(at("2026-09-16", "23:55")) is None,
           "and does not collect it twice once it is complete")
        ok(p37schedule.status(at("2026-09-16", "23:55"))["waiting_for"]
           == "ALREADY_COLLECTED_THIS_SESSION_IS_COMPLETE",
           "the status says why it is idle rather than looking stuck")
        # Back-dating is the one thing it must never do: 09-14 left no snapshot,
        # and tonight's cumulative tabs are not that day's evidence.
        ok(p37schedule.due(at("2026-09-16", "23:41")) is None
           and not os.path.exists(os.path.join(tmp, "2026-09-14")),
           "a missed session stays missed — no run back-fills an earlier date")
        sched_src = inspect.getsource(p37schedule)
        ok("session=session" in sched_src
           and "session_date" in sched_src,
           "the only session it ever passes is the one the clock is in")
        # A partial night is retried, spaced out, and then left alone.
        BROKEN.add("/api/cas-report")
        first = at("2026-09-17", "23:41")
        ok(p37schedule.run_once(first)["session_status"] == SESSION_PARTIAL,
           "a tab that is down makes the session partial, not a crash")
        ok(p37schedule.run_once(first + 60.0) is None,
           "the next minute is not a retry — three attempts a minute apart"
           " would all fail for the same reason")
        for i in range(1, p37schedule.MAX_ATTEMPTS + 2):
            p37schedule.run_once(first + i * p37schedule.RETRY_AFTER_SEC)
        BROKEN.discard("/api/cas-report")
        ok(p37schedule.attempts_made("2026-09-17")
           == p37schedule.MAX_ATTEMPTS,
           "a partial session is retried up to the budget and then left alone")
        ok(p37schedule.status(at("2026-09-17", "23:50"))["waiting_for"]
           == "OUT_OF_ATTEMPTS_THIS_SESSION_STAYS_PARTIAL_ON_THE_RECORD",
           "and stays partial on the record instead of being dropped")
        minutes_left = 24 * 60 - (23 * 60 + 40)
        ok((p37schedule.MAX_ATTEMPTS - 1) * p37schedule.RETRY_AFTER_SEC
           <= minutes_left * 60,
           "the retry budget is spendable before midnight closes the window,"
           " so a spacing change cannot silently leave attempts unused")
        ok(p37schedule.start() and p37schedule.start(),
           "starting the scheduler twice runs one thread, not two")
        p37schedule.stop()
        ok(not p37schedule.status()["running"], "and it stops on shutdown")
        ok("_p37sched.start()" in inspect.getsource(appmain._startup)
           and "_p37sched.stop()" in inspect.getsource(appmain._shutdown),
           "the app starts and stops it, so no operator has to remember")
        ok(asyncio.run(appmain.phase37_schedule())["waiting_for"]
           == p37schedule.status()["waiting_for"],
           "the app exposes its own scheduler state, which a second process"
           " cannot report on")
        route_names = appmain.phase37_schedule.__code__.co_names
        ok("status" in route_names
           and not {"run_once", "collect", "start", "stop"} & set(route_names),
           "and that route reports only — no request can trigger a"
           " collection or restart the thread")

        # --- durable journal evidence, counted separately ------------------
        from app.research.phase17 import store as p17store

        for day, n in (("2026-09-13", 3), ("2026-09-14", 5)):
            for i in range(n):
                p17store.write_observation(
                    {"session": day, "ts": at(day, "10:00") + i,
                     "instrument": "CRUDEOIL"})
        tally = p37durable.tally()
        rows = {r["session"]: r for r in tally["sessions"]}
        ok(rows.get("2026-09-14", {}).get("observations") == 5
           and rows.get("2026-09-13", {}).get("observations") == 3,
           "journal rows are counted per session from the append-only files")
        ok(tally["sessions_with_observations"] == 2,
           "a day with measured observations is visible even with no snapshot")
        ok(tally["counts_toward_floors"] is False
           and "NOT_COUNTED_TOWARD_ANY_FLOOR" in tally["why_not"],
           "and it says of itself that it counts toward no floor")
        api_state = p37rollup.rollup(tmp)
        ok(api_state["durable_evidence"]["available"] is False
           and "NOT_FREE" in api_state["durable_evidence"]["not_read"],
           "the dashboard path does not read every journal row to draw a count")
        state = p37rollup.rollup(tmp, with_durable=True)
        ok(state["durable_evidence"]["sessions_with_observations"] == 2
           and state["durable_evidence"]["counts_toward_floors"] is False,
           "the rollup reports it beside the snapshot count")
        ok(state["floors"]["observation_checkpoint"]["sessions"]
           == state["sessions_complete"],
           "every floor is still measured against snapshot sessions only")
        ok("NOT countable sessions" in p37rollup.headline(state),
           "the headline refuses to let the larger number read as progress")
        ok(p37durable.MIN_OBSERVATIONS >= 1,
           "a coverage line alone is not evidence that a session was measured")

        # A rolled file is evidence too, and the scan must not materialise it.
        rolled = os.path.join(
            os.path.dirname(p17store.path(p17store.OBSERVATIONS)),
            "phase17_observations.20260908-010203.jsonl")
        with open(rolled, "w", encoding="utf-8") as fh:
            for i in range(4):
                fh.write(json.dumps({
                    "session": "2026-09-08", "ts": at("2026-09-08", "10:00") + i,
                    "instrument": "CRUDEOIL", "ladder": "x" * 500}) + "\n")
        tally = p37durable.tally()
        rows = {r["session"]: r for r in tally["sessions"]}
        ok(rows.get("2026-09-08", {}).get("observations") == 4,
           "a rolled journal file is counted, so the record does not shorten"
           " itself every time the live file rolls")

        # And once that rolled file is stored gzipped to reclaim disk, it must
        # still count for exactly the same rows.
        with open(rolled, "rb") as raw, gzip.open(rolled + ".gz", "wb") as out:
            out.write(raw.read())
        os.unlink(rolled)
        packed = p37durable.tally()
        ok({r["session"]: r["observations"] for r in packed["sessions"]}
           == {r["session"]: r["observations"] for r in tally["sessions"]},
           "a compressed rolled file counts identically: reclaiming disk must "
           "not drop a session from the record")

        def _refuse(*a: object, **k: object) -> list[dict]:
            raise AssertionError("the tally must not materialise journal rows")

        original_read = p17store.read
        p17store.read = _refuse  # type: ignore[assignment]
        try:
            streamed = p37durable.tally()
        finally:
            p17store.read = original_read  # type: ignore[assignment]
        ok(streamed["sessions_with_observations"] == 3
           and streamed["complete"] is True,
           "the scan streams the files: 28 GB of ladders is not 28 GB of dicts")
        ok(streamed["bytes_scanned"]["observations"] > 0,
           "and it reports how much it read, so a number can be trusted or not")

        # A settled paper leg has no session field and no `ts` — only the
        # instants it was priced at. Attributing it by entry is what makes the
        # paper journal visible at all.
        p17store.write_paper({
            "signal_ts": at("2026-09-09", "10:00"),
            "entry_ts": at("2026-09-09", "10:01"),
            "exit_ts": at("2026-09-09", "11:30"),
            "symbol": "CRUDEOIL", "outcome": "T1", "cost_status": "MEASURED"})
        legs = p37durable.tally()
        by_day = {r["session"]: r for r in legs["sessions"]}
        ok(by_day.get("2026-09-09", {}).get("paper_legs") == 1,
           "a paper leg is counted on the day it was entered, not dropped for"
           " lacking a session field — that zero hid a journal full of them")
        ok(legs["total_paper_legs"] == 1
           and legs["rows_with_no_session"]["paper_legs"] == 0,
           "and a row whose day cannot be established is reported, never"
           " silently skipped")

        capped = p37durable.tally(max_bytes=1)
        ok(capped["complete"] is False
           and "SCAN WAS CAPPED" in p37durable.headline(capped),
           "a capped scan says so instead of reading as a smaller record")
        ok(p37durable.tally(max_bytes=10 ** 9)["complete"] is True,
           "and a cap that does not bite leaves the tally complete")

        # --- the per-tab budget and the per-tab retry ----------------------
        # 2026-09-15 was lost because one slow route timed out at the shared
        # 30s and every whole-collection retry hit the same wall inside the
        # same minute. A tab that needs longer says so, and only the tab that
        # failed is retried. Run last: these write sessions of their own, and
        # the counts asserted above are absolute.
        cas_spec = next(t for t in TABS if t["tab"] == "CAS")
        ok(p37collect.tab_timeout(cas_spec) > DEFAULT_TIMEOUT_S,
           "the CAS tab carries a longer budget than the shared default")
        ok(p37collect.tab_timeout({"tab": "X"}) == DEFAULT_TIMEOUT_S,
           "a tab without its own budget keeps the default")
        ok(p37collect.tab_timeout({"tab": "X", "timeout_s": 0}) ==
           DEFAULT_TIMEOUT_S,
           "a nonsense budget falls back rather than fetching with no timeout")
        ok(all(
            p37collect.tab_timeout(t) >= DEFAULT_TIMEOUT_S for t in TABS
        ), "no tab is given less time than the default")
        ok(phase37.TAB_ATTEMPTS >= 2 and phase37.TAB_RETRY_BACKOFF_S > 0,
           "the retry is bounded and spaced, not a tight loop on a slow route")
        ok(phase37.TAB_ATTEMPTS * p37collect.tab_timeout(cas_spec)
           + phase37.TAB_ATTEMPTS * phase37.TAB_RETRY_BACKOFF_S
           <= (24 * 60 - (23 * 60 + 40)) * 60,
           "a whole collection, retries and waits included, still fits inside"
           " the window before midnight closes it")

        before_complete = p37rollup.rollup(tmp)["sessions_complete"]
        slow_dir = os.path.join(tmp, "2026-10-05")
        calls: list[str] = []
        real_fetch = p37collect.fetch

        def _flaky(base: str, spec: dict, **kw: object) -> dict:
            calls.append(spec["tab"])
            if spec["tab"] == "CAS" and calls.count("CAS") < 3:
                return {
                    "tab": "CAS", "label": spec["label"], "path": spec["path"],
                    "url": "x", "status": FAILED,
                    "error": "TimeoutError: timed out",
                    "elapsed_ms": 30000.0, "timeout_s": 30.0,
                }
            return real_fetch(base, spec, **kw)

        slept: list[float] = []
        p37collect.fetch = _flaky  # type: ignore[assignment]
        try:
            m = p37collect.collect(
                base_url=base_url, session="2026-10-05",
                backoff_s=1.5, sleep=slept.append)
        finally:
            p37collect.fetch = real_fetch  # type: ignore[assignment]
        ok(m["session_status"] == SESSION_COMPLETE,
           "a tab that answers on a later attempt makes the session complete")
        ok(calls.count("CAS") == 3 and calls.count("READINESS") == 1,
           "only the failed tab is retried — the six that answered are not"
           " made to pay for the one that did not")
        ok(slept == [1.5, 1.5],
           "a retry waits before hammering the same slow route again")
        ok(m["tab_attempts"] == {"CAS": 3},
           "the manifest says which tab needed more than one attempt")
        cas = next(t for t in m["tabs"] if t["tab"] == "CAS")
        ok(len(cas["earlier_failures"]) == 2
           and all(f["error"] for f in cas["earlier_failures"]),
           "the earlier timeouts stay on the record, not erased by the success")
        ok(os.path.exists(os.path.join(slow_dir, "cas.3.json"))
           and not os.path.exists(os.path.join(slow_dir, "readiness.2.json")),
           "each attempt is its own file and an answered tab is not re-fetched")
        ok(m["slowest_tab"]["tab"] and m["slowest_tab"]["elapsed_ms"] >= 0,
           "the manifest records the slowest tab, so the next timeout is seen"
           " coming instead of arriving as a lost session")

        BROKEN.add("/api/cas-report")
        exhausted = p37collect.collect(
            base_url=base_url, session="2026-10-06", backoff_s=0)
        BROKEN.clear()
        ok(exhausted["session_status"] == SESSION_PARTIAL,
           "a tab that never answers still leaves the session partial")
        ok(os.path.exists(os.path.join(tmp, "2026-10-06", "cas.3.json")),
           "the retries are bounded and every one of them is on disk")
        ok(p37rollup.rollup(tmp)["sessions_complete"] == before_complete + 1,
           "retrying a tab never makes an unanswered session countable")

        src = "".join(
            inspect.getsource(mod)
            for mod in (p37collect, p37rollup, p37cli, phase37, p37schedule,
                        p37durable)
        )
        for forbidden in ("place_order", "placeOrder", "auto_buy", ".buy("):
            ok(forbidden not in src,
               f"the collector has no reference to {forbidden}")
        ok("INSERT" not in src.upper() and "DELETE" not in src.upper(),
           "the collector writes no rows to any store")
        ok(inspect.signature(p37collect.collect).parameters["write"].default
           is True and "dry-run" in inspect.getsource(p37cli),
           "collection can be rehearsed without writing")

        # --- the CLI -------------------------------------------------------
        # Swallowed, deliberately. These run against a fixture tree, and printing
        # report-shaped JSON on the way past let a synthetic "3 complete sessions,
        # last_session 2026-09-17" be read off the terminal as the operator's real
        # count. A smoke reports checks; it must not emit anything that looks like
        # evidence.
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            rc = p37cli.main(["collect", "--base-url", base_url,
                              "--session", "2026-09-11", "--dry-run"])
            rc_rollup = p37cli.main(["rollup"])
        ok(rc == 0, "collect returns 0 even when a tab is missing")
        ok(rc_rollup == 0, "rollup runs over the fixture tree without raising")
        ok("PHASE 37 SESSION EVIDENCE" in sink.getvalue(),
           "the CLI still prints its report — only the smoke keeps it off the"
           " terminal, where a fixture count could be mistaken for the real one")
    finally:
        p37collect.root_dir = original_root  # type: ignore[assignment]
        p37collect.TAB_RETRY_BACKOFF_S = original_backoff  # type: ignore[assignment]
        server.shutdown()

    print(f"\nPHASE 37 SMOKE — {PASS} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
