"""Checks for the frozen pair capture. Run: .venv/bin/python _smoke_pair_capture.py

Every check exists because getting it wrong would fake evidence:
the spec must be immutable, legs must pair only on an EXACT shared timestamp,
a measured spread must not be charged twice, an unmeasured one must be flagged,
and no verdict may appear before the pre-declared sample exists.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
import time

from app.config import settings

_TMP = tempfile.mkdtemp(prefix="pair_smoke_")
settings.data_dir = _TMP

from app.main import Hub  # noqa: E402
from app.research.pairs import capture, report  # noqa: E402
from app.research.pairs import spec as pair_spec  # noqa: E402
from app.research.pairs import store  # noqa: E402
from app.state import AppState  # noqa: E402


def fresh_store() -> None:
    """Empty database for the next check, so no check inherits another's rows."""
    capture._LAST_BAR.clear()
    store.reset_for_tests()
    try:
        os.remove(store.path())
    except FileNotFoundError:
        pass


fresh_store()

# 2026-01-01 09:20 IST, a Thursday: inside the NSE session, because a bar the
# contract could not have been traded in is refused (see the check below).
BASE = 1767239400
# The socket-fed checks below must use the CURRENT minute: a book may only be
# stamped onto the bar it was quoted inside, so a historical bar would (rightly)
# be recorded without one.
NOW_BAR = int(time.time() // 60) * 60


@contextlib.contextmanager
def ignoring_the_session_window():
    """Let a check use the real current minute whatever the clock says.

    Only for the two checks about socket-vs-REST bar provenance: they must stamp
    a book onto a bar that is genuinely current, so their timestamps are real
    wall-clock ones and would otherwise pass or fail depending on the hour the
    suite happens to run. The session rule itself has its own check.
    """
    original = capture._in_session
    capture._in_session = lambda _instrument, _bar_ts: True
    try:
        yield
    finally:
        capture._in_session = original


def book(bid: float | None, ask: float | None, ltp: float,
         symbol: str = "X", expiry: str | None = None, dte: int | None = None) -> dict:
    return {"symbol": symbol, "bid": bid, "ask": ask, "ltp": ltp,
            "expiry": expiry, "days_to_expiry": dte, "lot_size": 1}


def check_spec_frozen() -> None:
    fp = pair_spec.fingerprint()
    assert fp == pair_spec.fingerprint(), "fingerprint must be stable"
    assert pair_spec.MEASURE == "RATIO" and pair_spec.HEDGE == "CONTRACT_VALUE"
    assert pair_spec.THRESHOLD == 1.5 and pair_spec.WINDOW == 120
    try:
        pair_spec.FROZEN["threshold_sd"] = 2.0  # type: ignore[index]
    except TypeError:
        pass
    else:  # pragma: no cover - only if FROZEN stops being read-only
        raise AssertionError("the frozen spec must not be mutable at runtime")
    assert pair_spec.as_dict()["fingerprint"] == fp


def check_pairs_only_on_exact_timestamp() -> None:
    fresh_store()
    capture.observe_leg(pair_spec.LEG_A, BASE, 50000.0, futures_book=book(1, 2, 50000.0))
    assert store.counts()["pair_obs"] == 0, "one leg is not an observation"
    capture.observe_leg(pair_spec.LEG_B, BASE + 60, 22000.0,
                        futures_book=book(1, 2, 22000.0))
    assert store.counts()["pair_obs"] == 0, "different bars must never be paired"
    capture.observe_leg(pair_spec.LEG_B, BASE, 22000.0, futures_book=book(1, 2, 22000.0))
    assert store.counts()["pair_obs"] == 1, "same bar on both legs makes one row"


def check_z_is_causal_and_needs_a_full_window() -> None:
    fresh_store()
    for i in range(pair_spec.WINDOW - 1):
        ts = BASE + i * 60
        capture.observe_leg(pair_spec.LEG_A, ts, 50000.0 + i)
        capture.observe_leg(pair_spec.LEG_B, ts, 22000.0)
    conn = store.connect()
    zs = [r[0] for r in conn.execute("SELECT z FROM pair_obs ORDER BY bar_ts")]
    assert all(z is None for z in zs), "no z before a full estimation window"
    assert store.counts()["trades_open"] == 0, "no trade can open without a z"


def _feed(n: int, ratio_shift_at: int | None = None, *, measured: bool = True,
          start: int = BASE) -> None:
    """Quiet ratio for n bars, then a dislocation and a convergence back."""
    for i in range(n):
        ts = start + i * 60
        a_px, b_px = 50000.0, 22000.0
        if ratio_shift_at is not None and i >= ratio_shift_at:
            a_px = 50000.0 + 400.0 if i < ratio_shift_at + 3 else 50000.0
        wobble = 1.0 if i % 2 else -1.0
        a_px += wobble
        ba = book(a_px - 0.5, a_px + 0.5, a_px) if measured else book(None, None, a_px)
        bb = book(b_px - 0.5, b_px + 0.5, b_px) if measured else book(None, None, b_px)
        capture.observe_leg(pair_spec.LEG_A, ts, a_px, futures_book=ba)
        capture.observe_leg(pair_spec.LEG_B, ts, b_px, futures_book=bb)


def check_frozen_rule_opens_and_closes() -> None:
    fresh_store()
    _feed(pair_spec.WINDOW + 40, ratio_shift_at=pair_spec.WINDOW + 5)
    closed = store.closed_trades()
    assert closed, "the frozen threshold should trigger on a real dislocation"
    t = closed[0]
    assert t["direction"] in (capture.LONG_A_SHORT_B, capture.SHORT_A_LONG_B)
    assert abs(float(t["z_entry"])) >= pair_spec.THRESHOLD
    assert t["reason"] in ("CONVERGED", "DIVERGED", "MAX_HOLD", "SESSION_END")
    assert t["cost_status"] == capture.MEASURED
    assert abs(float(t["net"]) - (float(t["gross"]) - float(t["cost"]))) < 1e-6


def check_measured_spread_is_not_charged_twice() -> None:
    qty = 15.0
    measured = capture._leg_cost(50000.0, 50010.0, qty, True)
    modelled = capture._leg_cost(50000.0, 50010.0, qty, False)
    expected_extra = 2.0 * settings.futures_slippage_points * qty
    assert abs((modelled - measured) - expected_extra) < 1e-6, (
        "slippage is the proxy for an UNMEASURED spread only; a quoted book "
        "already pays the spread inside the fill prices")
    assert measured > 0


def check_unmeasured_book_is_flagged_not_invented() -> None:
    fresh_store()
    _feed(pair_spec.WINDOW + 40, ratio_shift_at=pair_spec.WINDOW + 5, measured=False)
    closed = store.closed_trades()
    assert closed, "an unmeasured book must still be recorded, not dropped"
    assert all(t["cost_status"] == capture.UNMEASURED for t in closed)
    conn = store.connect()
    rows = conn.execute("SELECT a_bid, a_ask FROM pair_obs").fetchall()
    assert all(r[0] is None and r[1] is None for r in rows), "no invented quotes"


def check_fills_cross_the_spread() -> None:
    buy, m1 = capture._fills({"bid": 99.0, "ask": 101.0}, 100.0, "BUY")
    sell, m2 = capture._fills({"bid": 99.0, "ask": 101.0}, 100.0, "SELL")
    assert (buy, sell) == (101.0, 99.0) and m1 and m2, "buy at ask, sell at bid"
    px, m3 = capture._fills({"bid": None, "ask": None}, 100.0, "BUY")
    assert px == 100.0 and not m3, "no book means LTP, marked unmeasured"


def check_no_verdict_before_the_pre_declared_sample() -> None:
    fresh_store()
    _feed(pair_spec.WINDOW + 40, ratio_shift_at=pair_spec.WINDOW + 5)
    out = report.retest()
    assert out["verdict"] == report.REQUIRES_MORE_DATA
    assert out["gate_met"] is False
    assert out["gate"]["trades_required"] == pair_spec.MIN_TRADES
    assert "hard_pass_checks" not in out, "no pass/fail may be shown under the bar"
    assert out["measured_sample"]["trades"] >= 1, "the sample is still reported"


def check_off_spec_trades_are_excluded_not_pooled() -> None:
    fresh_store()
    store.open_trade({
        "fingerprint": "deadbeefdeadbeef", "session": "2026-01-01",
        "direction": capture.LONG_A_SHORT_B, "entry_ts": BASE,
        "lots_a": 1, "lots_b": 2, "qty_a": 15, "qty_b": 50,
        "entry_a": 50000.0, "entry_b": 22000.0, "z_entry": 2.0,
        "notional_a": 750000.0, "notional_b": 1100000.0,
        "cost_status": capture.MEASURED, "cost_detail": {},
    })
    tid = store.open_trades()[0]["id"]
    store.close_trade(tid, {
        "exit_ts": BASE + 600, "exit_a": 50100.0, "exit_b": 22000.0, "z_exit": 0.1,
        "held_obs": 10, "reason": "CONVERGED", "gross": 1500.0, "cost": 500.0,
        "net": 1000.0, "hedged_pnl": 1000.0, "residual_pnl": 0.0,
        "cost_status": capture.MEASURED, "cost_detail": {},
    })
    out = report.retest()
    assert out["off_spec_trades_excluded"] == 1
    assert out["measured_sample"]["trades"] == 0, "another spec is not this spec"


def check_verdict_path_runs_once_the_bar_is_met() -> None:
    """Exercise the graded path on synthetic rows, so the gate is not the only
    code ever executed. Synthetic rows prove the arithmetic, not the market."""
    fresh_store()
    fp = pair_spec.fingerprint()
    per_session = max(1, pair_spec.MIN_TRADES // pair_spec.MIN_SESSIONS + 1)
    n = 0
    for day in range(pair_spec.MIN_SESSIONS):
        session = f"2026-02-{(day % 28) + 1:02d}-{day}"
        for k in range(per_session):
            ts = BASE + day * 86400 + k * 600
            store.open_trade({
                "fingerprint": fp, "session": session,
                "direction": capture.LONG_A_SHORT_B, "entry_ts": ts,
                "lots_a": 1, "lots_b": 2, "qty_a": 15, "qty_b": 50,
                "entry_a": 50000.0, "entry_b": 22000.0, "z_entry": 2.0,
                "notional_a": 750000.0, "notional_b": 1100000.0,
                "cost_status": capture.MEASURED, "cost_detail": {},
            })
            tid = store.open_trades()[-1]["id"]
            gross = 3000.0 if k % 3 else -900.0
            store.close_trade(tid, {
                "exit_ts": ts + 600, "exit_a": 50100.0, "exit_b": 22000.0,
                "z_exit": 0.2, "held_obs": 12, "reason": "CONVERGED",
                "gross": gross, "cost": 800.0, "net": gross - 800.0,
                "hedged_pnl": gross * 0.7, "residual_pnl": gross * 0.3,
                "cost_status": capture.MEASURED, "cost_detail": {},
            })
            n += 1
    out = report.retest()
    assert out["gate_met"] is True, f"gate should be met with {n} measured trades"
    assert out["verdict"] in (report.RESEARCH_LEAD, report.REJECTED)
    assert set(out["splits"]) == {"development", "validation", "holdout"}
    assert len(out["walk_forward"]) == 5
    assert set(out["cost_stress"]) == {"1.0x", "1.5x", "2.0x"}
    assert set(out["slippage_stress"]) == {"1.0pt", "2.0pt", "3.0pt"}
    assert set(out["hard_pass_checks"]) >= {
        "positive_net_expectancy", "positive_holdout", "walk_forward_majority",
        "survives_cost_stress", "survives_slippage_stress",
        "not_outlier_dependent", "sufficient_sample"}
    # Holdout sessions must be the LAST sessions chronologically, never sampled.
    dev = out["splits"]["development"]["trades"]
    hold = out["splits"]["holdout"]["trades"]
    assert dev > hold > 0, "60/20/20 by session, chronological"


def check_basis_needs_two_real_expiries() -> None:
    fresh_store()
    store.write_contract_quotes("NIFTY", BASE, [
        book(21999.0, 22001.0, 22000.0, symbol="N-JAN", expiry="2026-01-29", dte=28),
    ])
    assert report.basis_rows() == [], "one contract is not a basis"
    store.write_contract_quotes("NIFTY", BASE + 60, [
        book(21999.0, 22001.0, 22000.0, symbol="N-JAN", expiry="2026-01-29", dte=28),
        book(22049.0, 22051.0, 22050.0, symbol="N-FEB", expiry="2026-02-26", dte=56),
    ])
    rows = report.basis_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["near_expiry"] == "2026-01-29" and row["next_expiry"] == "2026-02-26"
    assert abs(row["raw_basis"] - 50.0) < 1e-6
    assert row["spread_status"] == capture.MEASURED
    status = report.basis_status()
    assert status["measurable"] is False
    assert status["verdict"] == report.REQUIRES_MORE_DATA


def check_a_missing_leg_is_reported_not_silent() -> None:
    fresh_store()
    cov = report.leg_coverage()
    assert set(cov["missing_legs"]) == {pair_spec.LEG_A, pair_spec.LEG_B}
    assert cov["capture_possible"] is False
    capture.observe_leg(pair_spec.LEG_A, BASE, 50000.0, futures_book=book(1, 2, 50000.0))
    cov = report.leg_coverage()
    assert cov["missing_legs"] == [pair_spec.LEG_B], "one leg is not coverage"
    assert cov["capture_possible"] is False
    assert pair_spec.LEG_B in cov["note"]
    capture.observe_leg(pair_spec.LEG_B, BASE, 22000.0, futures_book=book(1, 2, 22000.0))
    cov = report.leg_coverage()
    assert cov["capture_possible"] is True and cov["missing_legs"] == []
    assert cov["legs"][pair_spec.LEG_A]["two_sided_pct"] == 100.0


def check_backfill_fills_skipped_bars_without_a_book() -> None:
    """A leg read late must still contribute its own closed bars, unmeasured."""
    fresh_store()
    # Leg B reports every minute; leg A is only scanned on the third bar and
    # carries the two it missed as candle history.
    for i in range(3):
        capture.observe_leg(pair_spec.LEG_B, BASE + i * 60, 22000.0,
                            futures_book=book(1, 2, 22000.0))
    assert store.counts()["pair_obs"] == 0, "one leg alone is never an observation"
    capture.observe_leg(
        pair_spec.LEG_A, BASE + 120, 50002.0,
        futures_book=book(1, 2, 50002.0),
        history=[(BASE, 50000.0), (BASE + 60, 50001.0)])
    counts = store.counts()
    assert counts["pair_obs"] == 3, "the backfilled bars must pair too"
    assert counts["leg_quotes_backfilled"] == 2
    conn = store.connect()
    rows = {r["bar_ts"]: r["spread_status"]
            for r in conn.execute("SELECT bar_ts, spread_status FROM pair_obs")}
    assert rows[BASE] == "UNMEASURED" and rows[BASE + 60] == "UNMEASURED", (
        "a bar whose book was never quoted cannot be called measured")
    assert rows[BASE + 120] == "MEASURED", "the live bar keeps its real book"
    assert counts["pair_obs_measured"] == 1
    filled = conn.execute(
        "SELECT price, bid, ask, source FROM leg_quotes WHERE bar_ts = ? AND"
        " instrument = ?", (BASE, pair_spec.LEG_A)).fetchone()
    assert filled["price"] == 50000.0, "the feed's own close, not a nearby price"
    assert filled["bid"] is None and filled["ask"] is None, (
        "a spread must never be carried backwards onto an older bar")
    assert filled["source"] == capture.BACKFILL


def check_a_bar_is_never_traded_twice_or_backwards() -> None:
    fresh_store()
    for i in range(3):
        ts = BASE + i * 60
        capture.observe_leg(pair_spec.LEG_A, ts, 50000.0 + i,
                            futures_book=book(1, 2, 50000.0 + i))
        capture.observe_leg(pair_spec.LEG_B, ts, 22000.0,
                            futures_book=book(1, 2, 22000.0))
    before = dict(store.counts())
    # The same bars arrive again (both legs re-tick inside the same minute).
    for i in range(3):
        ts = BASE + i * 60
        capture.observe_leg(pair_spec.LEG_A, ts, 50000.0 + i,
                            futures_book=book(1, 2, 50000.0 + i))
    assert store.counts()["pair_obs"] == before["pair_obs"], "no duplicate rows"
    # A bar that completes only AFTER later bars were already processed is kept
    # as evidence but must not run the rule backwards.
    health_before = capture.health()["late_obs_not_traded"]
    capture.observe_leg(pair_spec.LEG_A, BASE + 180, 50003.0,
                        futures_book=book(1, 2, 50003.0))
    capture.observe_leg(pair_spec.LEG_B, BASE + 180, 22000.0,
                        futures_book=book(1, 2, 22000.0))
    capture.observe_leg(pair_spec.LEG_A, BASE + 240, 50004.0,
                        futures_book=book(1, 2, 50004.0),
                        history=[(BASE + 150, 50002.5)])
    capture.observe_leg(pair_spec.LEG_B, BASE + 240, 22000.0,
                        futures_book=book(1, 2, 22000.0),
                        history=[(BASE + 150, 22000.0)])
    assert capture.health()["late_obs_not_traded"] > health_before, (
        "a late bar must be recorded but not traded in retrospect")


class _FakeCandle:
    def __init__(self, t: int, close: float) -> None:
        self.time, self.close = t, close
        self.open = self.high = self.low = close
        self.volume = 0


class _FakeProvider:
    """A leg whose REST history is stale (rate-limited) but whose socket knows
    the current minute, which is the live situation the capture must handle."""

    def __init__(self, price: float) -> None:
        self.price = price
        self.stale_calls = 0

    def futures_candles(self, limit: int = 240) -> list:
        self.stale_calls += 1
        return [_FakeCandle(NOW_BAR - 60, self.price)]

    def futures_candles_live(self, limit: int = 240) -> list:
        return [_FakeCandle(NOW_BAR - 60, self.price), _FakeCandle(NOW_BAR, self.price)]

    def futures_price(self) -> float:
        return self.price

    def futures_book(self) -> dict:
        return book(1, 2, self.price)

    def futures_book_chain(self) -> list:
        return []


class _FakeLeg:
    """The minimum state surface ``capture_pair_leg`` uses."""

    def __init__(self, instrument: str, price: float) -> None:
        self.instrument = self._instrument = instrument
        self.provider = _FakeProvider(price)

    capture_pair_leg = AppState.capture_pair_leg
    _observe_pair_capture = AppState._observe_pair_capture


def check_capture_needs_no_tick_and_uses_the_research_series() -> None:
    """Capture must not depend on the scan reaching a leg, and must stamp the
    freshest bar the feed knows rather than a rate-limited REST one."""
    fresh_store()
    legs = [_FakeLeg(pair_spec.LEG_A, 50000.0), _FakeLeg(pair_spec.LEG_B, 22000.0)]
    with ignoring_the_session_window():
        for leg in legs:
            leg.capture_pair_leg()
    conn = store.connect()
    rows = conn.execute(
        "SELECT bar_ts, spread_status FROM pair_obs ORDER BY bar_ts").fetchall()
    assert [r["bar_ts"] for r in rows] == [NOW_BAR - 60, NOW_BAR], (
        "both legs must pair on the socket's current bar, not only the stale one")
    assert rows[-1]["spread_status"] == "MEASURED", (
        "the current bar is where a real book exists, so cost must be measured")
    assert all(leg.provider.stale_calls == 0 for leg in legs), (
        "research capture must not read the production indicator series")


def check_the_capture_cadence_is_throttled_and_leg_only() -> None:
    """The sampler runs on its own clock over the two frozen legs only."""
    fresh_store()
    hub = Hub()
    legs = [_FakeLeg(pair_spec.LEG_A, 50000.0), _FakeLeg(pair_spec.LEG_B, 22000.0),
            _FakeLeg("CRUDEOIL", 6000.0)]
    with ignoring_the_session_window():
        hub._capture_pair_legs(legs)
        first = store.counts()["leg_quotes"]
        assert first == 4, "two legs x two bars; a third instrument is not the pair"
        hub._capture_pair_legs(legs)
        assert store.counts()["leg_quotes"] == first, (
            "a second call inside the interval must not re-sample")
        hub._pair_captured_at = 0.0
        hub._capture_pair_legs(legs)
        assert store.counts()["leg_quotes"] == first, "same bars, so no new rows"


def check_a_leg_with_no_bar_at_all_is_recorded() -> None:
    """The worst coverage failure is the one that writes nothing.

    A leg whose REST history is rate-limited and whose socket is silent has no
    candle to be sampled at, so it produces no row however often it is sampled
    -- indistinguishable, afterwards, from a leg that is barely sampled. They
    need opposite fixes, so the empty series is counted.
    """
    fresh_store()
    before = capture.health()["samples_without_a_bar"]
    for _ in range(3):
        capture.observe_leg(pair_spec.LEG_A, None, 50000.0, now=BASE)
    assert store.counts()["leg_quotes"] == 0, "no bar means no row, correctly"
    diag = store.leg_diag()[pair_spec.LEG_A]
    assert diag["no_bar"] == 3 and diag["samples"] == 3
    assert capture.health()["samples_without_a_bar"] == before + 3
    # A later real bar must keep its own metadata; the empty samples must not
    # have overwritten it with nulls.
    capture.observe_leg(pair_spec.LEG_A, BASE, 50000.0,
                        futures_book=book(1, 2, 50000.0), now=BASE)
    capture.observe_leg(pair_spec.LEG_A, None, 50000.0, now=BASE + 5)
    diag = store.leg_diag()[pair_spec.LEG_A]
    assert diag["last_bar_ts"] == BASE, "a sample with no bar erases nothing"
    assert diag["last_bar_age_sec"] is not None


def check_bars_outside_the_trading_session_are_refused() -> None:
    """The feed answers after the bell; the market does not.

    Left running past 15:30 the feed keeps returning bars and keeps serving its
    last book, so capture would record minutes in which neither leg could be
    bought or sold -- and the frozen rule would enter and exit at prices no
    order could have reached.
    """
    fresh_store()
    after_close = BASE + 7 * 3600  # 16:20 IST, half an hour past the close
    before_open = BASE - 3600      # 08:20 IST, before the bell
    saturday = BASE + 2 * 86400    # 2026-01-03, a Saturday
    for ts in (after_close, before_open, saturday):
        capture.observe_leg(pair_spec.LEG_A, ts, 50000.0,
                            futures_book=book(1, 2, 50000.0), now=ts)
        capture.observe_leg(pair_spec.LEG_B, ts, 22000.0,
                            futures_book=book(1, 2, 22000.0), now=ts)
    counts = store.counts()
    assert counts["leg_quotes"] == 0 and counts["pair_obs"] == 0, (
        "an untradable minute is not an observation")
    refused = store.connect().execute(
        "SELECT SUM(out_of_session) FROM leg_diag WHERE instrument = ?",
        (pair_spec.LEG_A,)).fetchone()[0]
    assert refused == 3, "the refusal must be counted, not silent"
    # Backfilled history is held to the same rule.
    capture.observe_leg(pair_spec.LEG_A, BASE + 60, 50001.0,
                        futures_book=book(1, 2, 50001.0), now=BASE + 60,
                        history=[(before_open, 49999.0), (BASE, 50000.0)])
    kept = [r["bar_ts"] for r in store.connect().execute(
        "SELECT bar_ts FROM leg_quotes ORDER BY bar_ts")]
    assert kept == [BASE, BASE + 60], "the pre-open bar must not be backfilled"
    # And a bar inside the session is recorded normally.
    capture.observe_leg(pair_spec.LEG_B, BASE + 60, 22000.0,
                        futures_book=book(1, 2, 22000.0), now=BASE + 60)
    assert store.counts()["pair_obs"] == 1


def check_a_book_is_never_stamped_onto_a_stale_bar() -> None:
    """A quote is evidence about the minute it was quoted in and no other.

    A leg whose socket is quiet keeps serving its last REST bar, which can be
    many minutes old while the quote cache is current. Recording that book
    against that bar would invent a synchronization the feed never had, and the
    retest would then treat those costs as measured.
    """
    fresh_store()
    before = capture.health()["book_dropped_stale_bar"]
    quoted_at = BASE + 900  # the book is fifteen minutes newer than the bar
    capture.observe_leg(pair_spec.LEG_A, BASE, 50000.0,
                        futures_book=book(1, 2, 50000.0), now=quoted_at)
    capture.observe_leg(pair_spec.LEG_B, BASE, 22000.0,
                        futures_book=book(1, 2, 22000.0), now=quoted_at)
    conn = store.connect()
    row = conn.execute(
        "SELECT bid, ask FROM leg_quotes WHERE instrument = ? AND bar_ts = ?",
        (pair_spec.LEG_A, BASE)).fetchone()
    assert row["bid"] is None and row["ask"] is None, (
        "a book quoted in another minute must not be attached to this bar")
    obs = conn.execute("SELECT spread_status FROM pair_obs").fetchone()
    assert obs["spread_status"] == "UNMEASURED", (
        "cost is only measured where the book belongs to the observation")
    assert capture.health()["book_dropped_stale_bar"] > before, (
        "dropping a stale book must be counted, not silent")
    # The bar the book WAS quoted inside keeps it.
    capture.observe_leg(pair_spec.LEG_A, quoted_at, 50000.0,
                        futures_book=book(1, 2, 50000.0), now=quoted_at)
    kept = conn.execute(
        "SELECT bid, ask FROM leg_quotes WHERE instrument = ? AND bar_ts = ?",
        (pair_spec.LEG_A, quoted_at)).fetchone()
    assert kept["bid"] == 1 and kept["ask"] == 2


def check_sampling_diagnostics_separate_the_causes_of_low_coverage() -> None:
    """Low measured coverage must name its own cause.

    Few samples, samples landing on a bar already recorded, and a bar too old to
    carry a book are three different faults with three different fixes, and none
    of them can be told apart from the stored rows alone.
    """
    fresh_store()
    capture.observe_leg(pair_spec.LEG_A, BASE, 50000.0,
                        futures_book=book(1, 2, 50000.0), now=BASE)
    capture.observe_leg(pair_spec.LEG_A, BASE, 50000.0,
                        futures_book=book(1, 2, 50000.0), now=BASE + 5)
    capture.observe_leg(pair_spec.LEG_A, BASE, 50000.0,
                        futures_book=book(1, 2, 50000.0), now=BASE + 900)
    diag = report.status()["sampling"][pair_spec.LEG_A]
    assert diag["samples"] == 3
    assert diag["new_bars"] == 1 and diag["bar_unchanged"] == 2, (
        "a sample that produced no new bar must be visible as such")
    assert diag["book_two_sided"] == 2 and diag["book_missing"] == 1
    assert diag["book_dropped_stale_bar"] == 1
    assert diag["last_bar_age_sec"] == 900.0 and diag["last_bar_ts"] == BASE


def check_health_and_status_never_raise() -> None:
    assert isinstance(capture.health()["store"], dict)
    out = report.status()
    assert out["store_path"].endswith("pair_capture.db")
    assert out["pair"]["spec"]["fingerprint"] == pair_spec.fingerprint()
    assert "basis" in out


def main() -> int:
    checks = [v for k, v in sorted(globals().items()) if k.startswith("check_")]
    for fn in checks:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nALL PAIR CAPTURE CHECKS PASSED ({len(checks)})")
    print(f"temp store: {os.path.join(_TMP, 'pair_capture.db')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
