"""Phase 14 collection smoke: resolution, resumability, coverage. RESEARCH ONLY.

No network and no credentials: the scrip master is a handful of rows and the
fetcher is a stub, so what is checked here is the part that would otherwise only
be discovered eight hours into a real download — that a resumed run asks for the
windows it is missing and no others, and that a failed window comes back.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.research.db import Database  # noqa: E402
from app.research.phase14 import collector, coverage, tokens  # noqa: E402
from app.research.phase14.spot_store import (  # noqa: E402
    STATUS_FAILED,
    STATUS_OK,
    SpotStore,
)

CHECKS = 0
IST = coverage.IST

MASTER = [
    {"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY",
     "exch_seg": "NSE", "instrumenttype": "AMXIDX"},
    {"token": "99919000", "symbol": "SENSEX", "name": "SENSEX",
     "exch_seg": "BSE", "instrumenttype": "AMXIDX"},
    {"token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE",
     "exch_seg": "NSE", "instrumenttype": ""},
    {"token": "8003", "symbol": "SILVER-EQ", "name": "SILVER",
     "exch_seg": "NSE", "instrumenttype": ""},
]


def ok(cond: bool, what: str) -> None:
    global CHECKS
    CHECKS += 1
    assert cond, what


def _session_rows(day: dt.date, bars: int) -> list[dict]:
    start = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    return [
        {"ts": int((start + dt.timedelta(minutes=i)).timestamp()),
         "open": 100.0 + i, "high": 100.5 + i, "low": 99.5 + i,
         "close": 100.2 + i, "volume": 1000.0}
        for i in range(bars)
    ]


def make_fetch(days: dict[dt.date, int], fail_on: set[str] | None = None):
    """A fetcher over synthetic sessions, recording every window it was asked for."""
    asked: list[tuple[str, str]] = []
    failing = fail_on or set()

    def fetch(exchange, token, interval, frm, to):
        key = frm.date().isoformat()
        asked.append((token, key))
        if key in failing:
            raise RuntimeError("historical API rate-limited (AB1021)")
        rows: list[dict] = []
        for day, bars in days.items():
            if frm.date() <= day <= to.date():
                rows.extend(_session_rows(day, bars))
        return rows

    return fetch, asked


def make_flaky_fetch(days: dict[dt.date, int], *, fails: int, message: str):
    """A fetcher that refuses the first ``fails`` calls, then serves the window."""
    state = {"left": fails}

    def fetch(exchange, token, interval, frm, to):
        if state["left"] > 0:
            state["left"] -= 1
            raise RuntimeError(message)
        rows: list[dict] = []
        for day, bars in days.items():
            if frm.date() <= day <= to.date():
                rows.extend(_session_rows(day, bars))
        return rows

    return fetch, state


def main() -> int:
    # --- token resolution -------------------------------------------------
    nifty = tokens.resolve("NIFTY", MASTER)
    ok(nifty.token == "99926000" and nifty.kind == tokens.KIND_INDEX,
       f"NIFTY must resolve to the cash index, got {nifty}")
    sensex = tokens.resolve("SENSEX", MASTER)
    ok(sensex.exchange == "BSE", "SENSEX cash index lives in the BSE segment")
    rel = tokens.resolve("RELIANCE", MASTER)
    ok(rel.token == "2885" and rel.kind == tokens.KIND_EQUITY,
       "RELIANCE must resolve to its NSE cash row")

    # The trap this exists for: an MCX metal and an NSE-listed company share a
    # name, and collecting the company as the metal's history would silently
    # backtest the wrong market.
    try:
        tokens.resolve("SILVER", MASTER)
        raise AssertionError("MCX SILVER must not resolve to NSE SILVER-EQ")
    except tokens.SeriesUnavailable:
        ok(True, "MCX names are refused a cash series")
    try:
        tokens.resolve("NIFTYY", MASTER)
        raise AssertionError("an unregistered name must not fall back to a default")
    except tokens.SeriesUnavailable:
        ok(True, "unknown instruments are refused, not defaulted")

    found, missing = tokens.resolve_many(["NIFTY", "SILVER", "RELIANCE"], MASTER)
    ok(len(found) == 2 and "SILVER" in missing,
       "resolve_many reports what it could not resolve")

    # --- windows ----------------------------------------------------------
    start, end = dt.date(2026, 1, 1), dt.date(2026, 3, 1)
    windows = collector.chunk_windows(start, end, collector.ONE_MINUTE)
    ok(windows[0][0].date() == start and windows[-1][1].date() == end,
       "windows must span exactly the requested range")
    ok(all(w[0] <= w[1] for w in windows), "no window may end before it starts")
    contiguous = all(
        (later[0].date() - earlier[1].date()).days == 1
        for earlier, later in zip(windows, windows[1:]))
    ok(contiguous, "windows must be contiguous — a seam is a silent data hole")
    ok(collector.chunk_windows(start, end, collector.ONE_MINUTE) == windows,
       "the same range must always produce the same windows (resume safety)")

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(f"sqlite:///{os.path.join(tmp, 'p14.db')}")
        st = SpotStore(db)
        days = {dt.date(2026, 1, 5): 375, dt.date(2026, 1, 6): 375,
                dt.date(2026, 2, 10): 120}

        # --- first pass, one window rate-limited --------------------------
        fetch, asked = make_fetch(days, fail_on={windows[1][0].date().isoformat()})
        res = collector.collect_series(
            tokens.resolve("NIFTY", MASTER), start=start, end=end,
            fetch=fetch, st=st, sleep=lambda _s: None)
        ok(res["windows_fetched"] == len(windows),
           "a first pass must ask for every window")
        ok(res["rows_written"] == 750,
           f"expected 750 bars from two full sessions, got {res['rows_written']}")
        ok(res["windows_failed"] == [windows[1][0].date().isoformat()],
           "a rate-limited window must be reported, not raised")
        stats = st.chunk_stats("NIFTY", collector.ONE_MINUTE)
        ok(stats.get(STATUS_FAILED) == 1 and stats.get(STATUS_OK) == 1,
           f"chunk bookkeeping must record both outcomes, got {stats}")

        # --- resumed pass: only the missing window is re-asked ------------
        fetch2, asked2 = make_fetch(days)
        res2 = collector.collect_series(
            tokens.resolve("NIFTY", MASTER), start=start, end=end,
            fetch=fetch2, st=st, sleep=lambda _s: None)
        ok(res2["windows_fetched"] == 1,
           f"a resumed run must fetch only what is missing, fetched "
           f"{res2['windows_fetched']}")
        ok(res2["windows_skipped"] == len(windows) - 1,
           "already-stored windows must be skipped")
        ok([k for _t, k in asked2] == [windows[1][0].date().isoformat()],
           "the re-asked window must be exactly the one that failed")
        ok(res2["rows_written"] == 120,
           f"the recovered window must land its 120 bars, got {res2['rows_written']}")
        ok(res2["rows_stored"] == 870,
           f"stored bars must be the sum of both passes, got {res2['rows_stored']}")

        # --- re-running a complete instrument costs no calls --------------
        fetch3, asked3 = make_fetch(days)
        res3 = collector.collect_series(
            tokens.resolve("NIFTY", MASTER), start=start, end=end,
            fetch=fetch3, st=st, sleep=lambda _s: None)
        ok(res3["windows_fetched"] == 0 and not asked3,
           "a complete instrument must not be re-downloaded")

        # --- a re-fetched bar overwrites rather than duplicating ----------
        st.insert_candles("NIFTY", _session_rows(dt.date(2026, 1, 5), 375))
        ok(st.coverage("NIFTY")["rows"] == 870,
           "re-inserting a stored session must not duplicate bars")

        # --- a rate limit is waited out, not surrendered to ----------------
        # This is what turned a five-year NIFTY pull into 27 holes: the provider
        # answers a burst with "exceeding access rate", and a window written off
        # on the first refusal becomes a month-long gap in the history.
        ok(collector.is_retryable(
            RuntimeError("Access denied because of exceeding access rate")),
           "the provider's rate-limit reply must be recognised as retryable")
        ok(collector.is_retryable(RuntimeError("connect timeout=7 timed out")),
           "a dropped connection under load must be retryable")
        ok(not collector.is_retryable(RuntimeError("Invalid symboltoken")),
           "a permanent error must not burn the rate budget on retries")

        waits: list[float] = []
        flaky, state = make_flaky_fetch(
            {dt.date(2026, 1, 5): 375}, fails=2,
            message="Access denied because of exceeding access rate")
        res_r = collector.collect_series(
            tokens.resolve("RELIANCE", MASTER), start=start,
            end=dt.date(2026, 1, 20), fetch=flaky, st=st,
            backoff_start_sec=1.0, sleep=waits.append)
        ok(state["left"] == 0 and res_r["rows_written"] == 375,
           f"a twice-refused window must still land its bars, got {res_r}")
        ok(res_r["windows_failed"] == [] and res_r["windows_retried"] == 2,
           f"the retries must be reported, not hidden, got {res_r}")
        backoffs = [w for w in waits if w >= 1.0]
        ok(backoffs == [1.0, 2.0],
           f"the backoff must double per attempt, got {backoffs}")

        hard, _hard_state = make_flaky_fetch({}, fails=99,
                                             message="Invalid symboltoken")
        hard_waits: list[float] = []
        res_h = collector.collect_series(
            tokens.resolve("SENSEX", MASTER), start=start,
            end=dt.date(2026, 1, 20), fetch=hard, st=st,
            backoff_start_sec=1.0, sleep=hard_waits.append)
        ok(len(res_h["windows_failed"]) == 1 and res_h["windows_retried"] == 0,
           f"a permanent error must fail once and be recorded, got {res_h}")

        # --- coverage ------------------------------------------------------
        cov = coverage.instrument_coverage("NIFTY", st)
        ok(cov["sessions"] == 3 and cov["bars"] == 870,
           f"coverage must count sessions and bars, got {cov['sessions']}/{cov['bars']}")
        ok(cov["first_session"] == "2026-01-05" and cov["last_session"] == "2026-02-10",
           "coverage must report the real first and last IST session")
        ok(cov["thin_sessions"] == 1 and cov["thin_session_dates"] == ["2026-02-10"],
           f"the 120-bar session must be flagged thin, got {cov['thin_session_dates']}")
        ok(any(g["after"] == "2026-01-06" for g in cov["session_gaps"]),
           "a month-long hole between sessions must be named")

        # --- watchlist collection reports the unavailable ------------------
        fetch4, _asked4 = make_fetch(days)
        run = collector.collect(["NIFTY", "SILVER", "RELIANCE"], start=start, end=end,
                                master=MASTER, fetch=fetch4, st=st,
                                sleep=lambda _s: None)
        ok(run["instruments_collected"] == 2 and "SILVER" in run["instruments_unavailable"],
           "a watchlist run must say which instruments have no history")
        rep = coverage.report(st)
        ok(rep["instruments"] == 2 and rep["bars_total"] > 0,
           f"the coverage report must cover what was stored, got {rep['instruments']}")

    print(f"checked {CHECKS}")
    print("phase 14 collection smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
