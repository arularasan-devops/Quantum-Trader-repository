"""Smoke test: Phase 12A Track A — futures feed trace, WS-built bars, audit.

The 26 Aug session refused 1,321 futures plans with ``STALE_FEED`` while the
option feed was healthy. Futures freshness is measured on the newest 1-minute
BAR, and bars came only from Angel's historical endpoint, whose budget every
instrument on a key shares — so a routine refresh is deferred and the newest bar
can be minutes old while the socket pushes every second.

What is proved here:

* ticks assemble 1-minute bars, and those bars only ever EXTEND a REST series
  past its last bar (REST stays authoritative for the minutes it covers);
* the freshest series is served on its own accessor, so the production option
  engine's indicator input (``futures_candles``) is byte-for-byte unchanged —
  a feed fix must not alter what a production BUY is computed from;
* the audit labels ages FRESH/AGING/STALE/DEAD/NO_DATA, samples at most once a
  minute per instrument, names the measured cause of an old bar, and never
  raises into the scan;
* the research engine's own 90s refusal is not read from, or relaxed by, the
  audit's bands.
"""
from __future__ import annotations

import shutil
import tempfile
import time

from app.analysis import futures_feed_audit as audit
from app.analysis import futures_signal
from app.config import settings
from app.market.angelone import _TickBars
from app.models import Candle

checks = 0


def ok(cond: bool, msg: str) -> None:
    global checks
    checks += 1
    assert cond, msg


# ---------------------------------------------------------------- tick bars
bars = _TickBars()
base = (int(time.time()) // 60) * 60
for i, px in enumerate((100.0, 103.0, 98.0, 101.0)):
    bars.add(base + i * 5, px)
built = bars.bars_after(base - 60)
ok(len(built) == 1, f"four ticks in one minute must build one bar, got {len(built)}")
b = built[0]
ok((b.open, b.high, b.low, b.close) == (100.0, 103.0, 98.0, 101.0),
   f"OHLC must come from tick order, got {b}")
ok(b.time == base, "a bar must be stamped at its minute start")

bars.add(base + 60, 105.0)
ok(len(bars.bars_after(base - 60)) == 2, "a later tick must open a new minute")
ok(len(bars.bars_after(base)) == 1,
   "bars_after must exclude the minute REST already covers, so REST stays authoritative")

bars.add(base + 120, 0.0)
ok(len(bars.bars_after(base - 60)) == 2, "a non-positive tick must be ignored")

trimmed = _TickBars(keep_minutes=3)
for i in range(10):
    trimmed.add(base + i * 60, 50.0 + i)
ok(len(trimmed.bars_after(0)) == 3, "the ring must not grow past keep_minutes")


# ------------------------------------------------- research vs production series
class _Prov:
    """Stands in for the live provider: REST history plus a fresher tick tail."""

    def __init__(self) -> None:
        self.rest = [
            Candle(time=base - 600, open=1.0, high=1.0, low=1.0, close=1.0, volume=0.0),
            Candle(time=base - 540, open=1.0, high=1.0, low=1.0, close=1.0, volume=0.0),
        ]
        self.tail = [Candle(time=base, open=2.0, high=2.0, low=2.0, close=2.0, volume=0.0)]

    def futures_candles(self, limit: int = 240) -> list[Candle]:
        return self.rest[-limit:]

    def futures_candles_live(self, limit: int = 240) -> list[Candle]:
        return (self.rest + self.tail)[-limit:]


prov = _Prov()
ok(prov.futures_candles(300) == prov.rest,
   "production indicator input must stay REST-only after the feed fix")
ok(prov.futures_candles_live(300)[-1].time > prov.futures_candles(300)[-1].time,
   "the research series must be fresher than the production one")

# The base provider contract must answer both accessors for every feed, so the
# futures research path never has to know which provider it is talking to.
from app.market.provider import MarketDataProvider  # noqa: E402  (after stub above)

ok(hasattr(MarketDataProvider, "futures_candles_live"),
   "every provider must expose the research accessor")
ok(hasattr(MarketDataProvider, "futures_feed_trace"),
   "every provider must expose a feed trace, even if it cannot see its path")


# -------------------------------------------------------------------- bands
audit.reset_for_tests()
ok(audit.state(None) == audit.NO_DATA, "no age is NO_DATA, not FRESH")
ok(audit.state(0.0) == audit.FRESH, "a current bar is FRESH")
ok(audit.state(settings.futures_feed_aging_sec) == audit.FRESH, "the band edge is inclusive")
ok(audit.state(settings.futures_feed_aging_sec + 1) == audit.AGING, "past aging is AGING")
ok(audit.state(settings.futures_feed_stale_sec + 1) == audit.STALE, "past stale is STALE")
ok(audit.state(settings.futures_feed_dead_sec + 1) == audit.DEAD, "past dead is DEAD")
ok(futures_signal._MAX_FEED_AGE_SEC == 90.0,
   "the research engine's freshness refusal must stay at 90s: the audit describes "
   "an age, it does not excuse one")


def trace(age: float | str | None, **over: object) -> dict:
    row = {
        "symbol": "CRUDEOIL26SEPFUT",
        "token": "426293",
        "exchange": "MCX",
        "expiry": "2026-09-17",
        "dte": 22,
        "subscription_state": "SUBSCRIBED",
        "source": audit.REST if age is not None else audit.NONE,
        "last_tick_at": None,
        "tick_age_sec": 1.0,
        "exchange_bar_ts": time.time() - age if isinstance(age, float) else None,
        "cache_ts": None,
        "processing_ts": time.time(),
        "age_seconds": age,
        "ticks": 0,
        "rest_attempts": 0,
        "rest_deferred": 0,
        "hist_budget": {"rate_limits": 0},
    }
    row.update(over)
    return row


REQUIRED = (
    "symbol", "token", "exchange", "expiry", "dte", "subscription_state",
    "source", "exchange_bar_ts", "cache_ts", "processing_ts", "age_seconds",
)

# §1 requires every one of these per contract; a trace missing one cannot say
# which hop produced the age.
row = audit.observe("CRUDEOIL", trace(12.0), now=1_000.0)
ok(row is not None, "the first observation must be sampled")
for field in REQUIRED:
    ok(field in row, f"the trace must carry {field}")
ok(row["freshness"] == audit.FRESH, "a 12s bar must record FRESH")

ok(audit.observe("CRUDEOIL", trace(20.0), now=1_010.0) is None,
   "a second sample inside the interval must be skipped, not stored")
ok(audit.observe("CRUDEOIL", trace(20.0), now=1_070.0) is not None,
   "a sample past the interval must be stored")
ok(audit.observe("CRUDEOIL", None, now=1_200.0) is None, "a missing trace must be ignored")
ok(audit.observe("", trace(1.0), now=1_200.0) is None, "a blank instrument must be ignored")

rep = audit.report()
ok(rep["scope"] == "DIAGNOSTICS_ONLY", "the artefact must declare its scope")
ok(rep["engine_refusal_threshold_sec"] == 90.0,
   "the artefact must state the unchanged engine threshold")
ok(rep["instruments"] == 1, f"one instrument observed, got {rep['instruments']}")
crude = rep["rows"][0]
ok(crude["age"]["samples"] == 2, f"two samples stored, got {crude['age']['samples']}")
ok(crude["age"]["median_age_sec"] == 16.0, f"median of 12/20, got {crude['age']}")
ok(crude["age"]["stale_pct"] == 0.0, "a fresh feed must report 0% stale")
ok(crude["cause"]["verdict"] == "OK", f"a healthy path is OK, got {crude['cause']}")

# ------------------------------------------------------------------- causes
audit.reset_for_tests()
audit.observe("NIFTY", trace(1_800.0, subscription_state="NOT_SUBSCRIBED"), now=2_000.0)
ok(audit.report()["rows"][0]["cause"]["verdict"] == "TOKEN_NOT_SUBSCRIBED",
   "an unsubscribed token must be named, not guessed at")

audit.reset_for_tests()
audit.observe("NIFTY", trace(1_800.0, subscription_state="SOCKET_DOWN"), now=3_000.0)
ok(audit.report()["rows"][0]["cause"]["verdict"].startswith("WEBSOCKET_UNAVAILABLE"),
   "a dead socket must be named")

audit.reset_for_tests()
audit.observe("NIFTY", trace(None, source=audit.NONE), now=4_000.0)
r = audit.report()["rows"][0]
ok(r["latest"]["freshness"] == audit.NO_DATA, "no bars is NO_DATA")
ok("NO_BARS" in r["cause"]["reasons"], f"no bars must be named, got {r['cause']}")

# A deferred historical budget is the 26 Aug hypothesis; it must be read off the
# key's own counters across two samples, never asserted.
audit.reset_for_tests()
audit.observe("BANKNIFTY", trace(1_700.0, rest_attempts=4, rest_deferred=2, ticks=10),
              now=5_000.0)
audit.observe("BANKNIFTY", trace(1_760.0, rest_attempts=9, rest_deferred=7, ticks=130),
              now=5_060.0)
r = audit.report()["rows"][0]
ok(r["cause"]["verdict"] == "HISTORICAL_BUDGET_DEFERRED",
   f"a deferring budget must be the named cause, got {r['cause']}")
ok(r["throughput"]["rest_polls"] == 5, f"polls are a delta, got {r['throughput']}")
ok(r["throughput"]["rest_deferred"] == 5, f"deferrals are a delta, got {r['throughput']}")
ok(r["throughput"]["ticks_per_sec"] == 2.0, f"ticks/sec over the window, got {r['throughput']}")

audit.reset_for_tests()
audit.observe("SILVER", trace(400.0, hist_budget={"rate_limits": 1}), now=6_000.0)
audit.observe("SILVER", trace(460.0, hist_budget={"rate_limits": 4}), now=6_060.0)
r = audit.report()["rows"][0]
ok(r["throughput"]["rate_limit_errors"] == 3, f"rate limits are a delta, got {r['throughput']}")
ok("HISTORICAL_RATE_LIMITED" in r["cause"]["reasons"], "rate limits must be named")

# A subscribed token whose socket has gone quiet looks identical to a healthy one
# on a single age; it must be separable.
audit.reset_for_tests()
audit.observe("GOLD", trace(1_000.0, tick_age_sec=None), now=7_000.0)
ok("SUBSCRIBED_BUT_NOT_TICKING" in audit.report()["rows"][0]["cause"]["reasons"],
   "a silent subscription must be named")

# ------------------------------------------------------------ off / on switch
audit.reset_for_tests()
settings.futures_feed_audit = False
try:
    ok(audit.observe("CRUDEOIL", trace(5.0), now=8_000.0) is None,
       "the audit must record nothing while switched off")
finally:
    settings.futures_feed_audit = True

# Diagnostics may never break a scan: a trace holding junk is recorded, not raised.
audit.reset_for_tests()
ok(audit.observe("CRUDEOIL", trace("not-a-number", ticks=None), now=9_000.0) is not None,
   "a malformed trace must still be recorded")
ok(audit.report()["rows"][0]["latest"]["freshness"] == audit.NO_DATA,
   "an unreadable age is NO_DATA, never silently FRESH")
audit.reset_for_tests()

# --------------------------------------------- §5 futures paper lifecycle
# 26 Aug produced 1,633 futures rows, 2 valid plans and 1 resolved outcome. One
# RESOLVED row cannot say whether the sample is small because plans are rare or
# because following them breaks after the plan, so every stage is stamped.
from app.analysis import futures_outcomes as fo  # noqa: E402
from app.models import FuturesSignalCard  # noqa: E402

_fo_dir = tempfile.mkdtemp(prefix="qt_p12a_fut_")
_orig_dir = settings.data_dir
settings.data_dir = _fo_dir


def fut_card() -> FuturesSignalCard:
    return FuturesSignalCard(
        status=fo.VALID, signal="BUY", direction="LONG", instrument="NIFTY",
        contract="NIFTY26SEPFUT", expiry="2026-09-24", days_to_expiry=29,
        lot_size=65, price=24_000.0, entry=24_000.0, stop=23_950.0,
        target1=24_050.0, target2=24_100.0, target3=24_150.0,
        risk_points=50.0, spread_points=1.0, cost_points=2.0,
        futures_signal_id="fut-12a", episode_id="NIFTY-FUT-EP",
        signal_score=71.0, setup_type="BREAKOUT", atr=60.0,
    )


try:
    t0 = time.time()
    fo.reset_for_tests()
    fo.observe("NIFTY", fut_card(), 24_000.0, t0)
    fo.observe("NIFTY", fut_card(), 24_080.0, t0 + 60)    # best excursion +80
    fo.observe("NIFTY", fut_card(), 24_160.0, t0 + 120)   # through T3 -> resolved
    res = fo.read_outcomes()[-1]
    ok(res["stages_reached"] == list(fo.STAGES),
       f"a followed plan must stamp every §5 stage in order, got {res['stages_reached']}")
    ok(all(s["ts"] >= int(t0) for s in res["lifecycle"]),
       "every stage must carry its own timestamp")
    ok(res["mfe_capture_pct"] == 100.0,
       f"a plan that finishes at its best captured all of it, got {res['mfe_capture_pct']}")
    ok(res["giveback_r"] == 0.0, f"nothing given back, got {res['giveback_r']}")

    # The 0.9R option give-back has to be measurable in points too, or the two
    # vehicles can never be compared on capture.
    fo.reset_for_tests()
    fo.observe("NIFTY", fut_card(), 24_000.0, t0)
    fo.observe("NIFTY", fut_card(), 24_100.0, t0 + 60)   # +100, through T1/T2
    fo.observe("NIFTY", fut_card(), 23_940.0, t0 + 120)  # stopped out
    gave = fo.read_outcomes()[-1]
    ok(gave["outcome"] == "STOP", f"the plan must stop out, got {gave['outcome']}")
    ok(gave["mfe_points"] == 100.0 and gave["giveback_points"] == 160.0,
       f"give-back is measured from the best point, got {gave}")
    ok(gave["giveback_r"] == 3.2, f"give-back in R, got {gave['giveback_r']}")
    ok(gave["mfe_capture_pct"] == -60.0,
       f"a plan that gave back more than its best captures negatively, "
       f"got {gave['mfe_capture_pct']}")

    summary = fo.summarise(fo.read_outcomes())
    ok(summary["lifecycle_funnel"] == {s: 2 for s in fo.STAGES},
       f"both follows must appear at every stage, got {summary['lifecycle_funnel']}")
    ok(summary["giveback_r_median"] == 1.6,
       f"median give-back over 0.0 and 3.2, got {summary['giveback_r_median']}")
    ok(summary["comparison_status"] == "INSUFFICIENT_SAMPLE",
       "two outcomes must not be called ready for an options-vs-futures verdict")
    ok(summary["shortfall"] == fo.MIN_COMPARISON_SAMPLE - 2,
       f"the shortfall against 30 resolved must be stated, got {summary['shortfall']}")
    ok(summary["research_only"] and summary["unit"] == "INDEX_POINTS",
       "futures results stay research-only and in points, never summed with premium")
finally:
    fo.reset_for_tests()
    settings.data_dir = _orig_dir
    shutil.rmtree(_fo_dir, ignore_errors=True)



# ------------------------------------------- socket subscription cap (union)
# A live session measured NIFTY as "SUBSCRIBED" with ONE websocket tick in a
# whole session while MCX on the same socket ticked 2,772 times: the union of
# every instrument's tokens exceeded the per-socket cap, and the truncation took
# whole instruments' FUTURES tokens -- invisibly, because a provider reported
# itself subscribed from its own wish list rather than from the socket.
import threading  # noqa: E402

from app.market.angelone import _MAX_WS_TOKENS, _LiveStream  # noqa: E402


class _FakeSws:
    def __init__(self) -> None:
        self.sent: list[tuple[str, list]] = []

    def subscribe(self, _cid, _mode, payload) -> None:
        self.sent.append(("subscribe", payload))

    def unsubscribe(self, _cid, _mode, payload) -> None:
        self.sent.append(("unsubscribe", payload))


stream = object.__new__(_LiveStream)
stream._routes, stream._token_exch, stream._provider_tokens = {}, {}, {}
stream._union_wanted, stream._subscribed = 0, set()
stream._connected, stream._lock = False, threading.Lock()
stream._drops = stream._rebuilds = stream._reconnects = 0
stream._sws = _FakeSws()

instruments = 60          # a day-movers universe
strikes = 40              # ATM option window per instrument
for i in range(instruments):
    stream.set_provider_tokens(
        i, 2, [f"FUT{i}"] + [f"OPT{i}_{j}" for j in range(strikes)], lambda *a: None)

desired = stream._desired_union()
ok(len(desired) == _MAX_WS_TOKENS,
   f"the cap must still be respected, got {len(desired)}")
missing = [i for i in range(instruments) if f"FUT{i}" not in desired]
ok(not missing,
   f"every instrument's futures token must survive the cap, missing {missing[:5]}")
counts = stream.subscription_counts()
ok(counts["wanted"] == instruments * (strikes + 1)
   and counts["dropped_by_cap"] == counts["wanted"] - _MAX_WS_TOKENS,
   f"what the cap drops must be reported, got {counts}")

ok(not stream.is_subscribed("FUT7"),
   "a token wanted by a provider is not on the socket until it is sent")
stream._connected = True
stream._on_open(None)
ok(stream.is_subscribed("FUT7"),
   "on connect the socket must carry the futures tokens")
ok(len(stream._subscribed) == _MAX_WS_TOKENS,
   "a reconnect must ask for the CAPPED union, not every routed token")
sent = [p for a, p in stream._sws.sent if a == "subscribe"]
ok(sent and sum(len(g["tokens"]) for g in sent[-1]) == _MAX_WS_TOKENS,
   "the subscribe payload must carry exactly the capped union")
ok(all(isinstance(g["exchangeType"], int) for g in sent[-1]),
   "tokens must be grouped by exchange type")



# ------------------------------------- a fresh instrument reports no cause
# A live MCX row read HISTORICAL_BUDGET_DEFERRED at a bar age of 5 seconds while
# the socket pushed a tick a second: the deferral was real but cost it nothing,
# and naming it as the cause sends the reader after the wrong thing.
_fresh = {
    "instrument": "SILVER", "ts": int(time.time()), "subscription_state": "SUBSCRIBED",
    "source": "WS", "age_seconds": 5.3, "tick_age_sec": 0.3, "rest_deferred": 3,
    "freshness": "FRESH", "diag": None,
}
_fresh0 = dict(_fresh, ts=_fresh["ts"] - 60, rest_deferred=0)
c = audit._cause([_fresh0, _fresh], _fresh)
ok(c["verdict"] == "OK", f"a current bar has no cause to explain, got {c['verdict']}")
ok("HISTORICAL_BUDGET_DEFERRED" in c["not_causal"],
   f"a real but harmless condition must still be reported, got {c}")

_old = dict(_fresh, age_seconds=1001.6, tick_age_sec=3051.2, freshness="DEAD")
c = audit._cause([dict(_old, ts=_old["ts"] - 60, rest_deferred=0), _old], _old)
ok(c["verdict"] == "SUBSCRIBED_BUT_NOT_TICKING",
   f"an old bar must still name its cause, got {c['verdict']}")
ok(c["not_causal"] == [], "a causal verdict must not also file reasons as harmless")

_capped = dict(_old, subscription_state="WANTED_BUT_NOT_ON_SOCKET")
c = audit._cause([dict(_capped, ts=_capped["ts"] - 60), _capped], _capped)
ok(c["verdict"] == "TOKEN_DROPPED_BY_SOCKET_CAP",
   f"a token the socket does not carry must be named, got {c['verdict']}")

print(f"\nPHASE 12A FEED SMOKE PASSED ({checks} checks)")
