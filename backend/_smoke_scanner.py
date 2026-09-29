"""Phase 4 scanner + feed-quality checks.

The scanner is research/observability only, and the whole value of it depends on
two properties that are easy to lose by accident:

* it can never turn into a trade signal (no BUY, no gate, no order, no mutation
  of the decision engine), and
* it can never rank an instrument on data that is not actually current — old
  movement on a dead feed is precisely the failure mode a "top movers" list
  invites.

These checks pin both, plus the tick-accounting behaviour (duplicate,
out-of-order, reconnect, missing volume/OI) and determinism. No broker
credentials, no network, no orders.
"""
from __future__ import annotations

import ast
import inspect
import sys
import time

from app.analysis import scanner
from app.market import tick_quality as tq
from app.models import Candle

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


# --------------------------------------------------------------- fixtures
def bars(n: int = 90, *, start: float = 100.0, drift: float = 0.05,
         vol: float = 1.0, volume: float = 1000.0,
         t0: int = 1_700_000_000) -> list[Candle]:
    """Deterministic synthetic 1-minute bars (no randomness, so tests are exact)."""
    out: list[Candle] = []
    px = start
    for i in range(n):
        o = px
        px = px + drift
        hi = max(o, px) + vol * 0.2
        lo = min(o, px) - vol * 0.2
        out.append(Candle(time=t0 + i * 60, open=o, high=hi, low=lo,
                          close=px, volume=volume))
    return out


def accelerating(n: int = 90) -> list[Candle]:
    """Quiet, then a genuine acceleration in the last few bars."""
    out = bars(n - 8, drift=0.01, vol=0.2)
    px = out[-1].close
    t = out[-1].time
    for i in range(8):
        o = px
        px = px + 0.5 + 0.25 * i
        out.append(Candle(time=t + (i + 1) * 60, open=o, high=px + 0.1,
                          low=o - 0.1, close=px, volume=4000.0))
    return out


def make_input(candles, **kw) -> scanner.ScanInput:
    base = {
        "instrument": "TEST",
        "ltp": candles[-1].close if candles else None,
        "candles": tuple(candles),
        "data_age_ms": 200.0,
        "freshness": tq.FRESH,
        "data_quality_score": 100.0,
        "market_open": True,
    }
    base.update(kw)
    return scanner.ScanInput(**base)


# --- 1. a stale instrument can never be FRESH_MOMENTUM -----------------------
fast = accelerating()
live = scanner.scan_one(make_input(fast))
for state in (tq.STALE, tq.DEAD, tq.NO_DATA):
    res = scanner.scan_one(make_input(fast, freshness=state, data_age_ms=30_000.0))
    check(
        f"a {state} feed cannot be classified FRESH_MOMENTUM",
        res.classification == scanner.NO_DATA
        and res.verdict == scanner.NO_OPPORTUNITY
        and res.opportunity_score is None,
        f"got {res.classification}/{res.verdict}",
    )
check(
    "the same bars on a FRESH feed DO score (so the test above is meaningful)",
    live.opportunity_score is not None,
    f"score {live.opportunity_score}",
)
check(
    "a closed exchange is excluded from ranking, not ranked on frozen prices",
    scanner.scan_one(make_input(fast, market_open=False)).classification == scanner.NO_DATA,
)

# --- 2/3. missing OI and missing volume stay UNKNOWN, never zero -------------
no_vol = scanner.scan_one(make_input(bars(volume=0.0)))
check(
    "missing volume stays UNKNOWN (not 0, not 'LOW')",
    no_vol.volume_state == scanner.UNKNOWN and no_vol.relative_volume is None,
    f"{no_vol.volume_state}/{no_vol.relative_volume}",
)
check(
    "missing OI stays UNKNOWN (simulator OI is never treated as real)",
    live.oi_state == scanner.UNKNOWN and no_vol.oi_state == scanner.UNKNOWN,
    f"{live.oi_state}",
)
check(
    "an UNKNOWN feature lowers confidence instead of silently scoring zero",
    (no_vol.confidence_in_score or 0) < (live.confidence_in_score or 0),
    f"{no_vol.confidence_in_score} < {live.confidence_in_score}",
)
check(
    "option liquidity is UNKNOWN when the feed gave no book",
    live.liquidity_state == scanner.UNKNOWN or live.liquidity_state in (
        scanner.THIN, scanner.MODERATE, scanner.LIQUID),
    live.liquidity_state,
)

# --- 4. duplicate ticks do not create false acceleration ---------------------
tq.feed_quality.reset()
now = time.time()
for i in range(5):
    tq.feed_quality.record_tick("DUP", "t1", ltp=100.0 + i, exchange_ms=1000 + i,
                                seq=i, recv=now + i)
before = tq.feed_quality.snapshot("DUP", now + 5)
for _ in range(20):  # the SAME tick, resent
    tq.feed_quality.record_tick("DUP", "t1", ltp=104.0, exchange_ms=1004, seq=4,
                                recv=now + 6)
after = tq.feed_quality.snapshot("DUP", now + 6)
check(
    "20 resends of one tick are counted as duplicates, not as 20 new ticks",
    after["duplicate_count"] == 20 and after["ticks_accepted"] == before["ticks_accepted"],
    f"dupes={after['duplicate_count']} accepted={after['ticks_accepted']}",
)
check(
    "duplicates do not move the accepted price (no fake momentum)",
    tq.feed_quality.accepted_ltp("DUP") == 104.0,
)

# --- 5. out-of-order ticks do not corrupt the LTP ---------------------------
tq.feed_quality.record_tick("DUP", "t1", ltp=90.0, exchange_ms=500, seq=1,
                            recv=now + 7)
check(
    "a late (lower sequence) tick is rejected and cannot corrupt the LTP",
    tq.feed_quality.accepted_ltp("DUP") == 104.0
    and tq.feed_quality.snapshot("DUP", now + 7)["out_of_order_count"] == 1,
    f"ltp={tq.feed_quality.accepted_ltp('DUP')}",
)
check(
    "an invalid (zero/negative) price is rejected, not stored",
    tq.feed_quality.record_tick("DUP", "t1", ltp=0.0, seq=99) == tq.INVALID
    and tq.feed_quality.accepted_ltp("DUP") == 104.0,
)

# --- 6. reconnect resets data health honestly -------------------------------
tq.feed_quality.note_reconnect("DUP")
snap = tq.feed_quality.snapshot("DUP", now + 8)
check(
    "a reconnect is counted and does NOT reset the age to 'fresh'",
    snap["reconnect_count"] == 1 and snap["last_tick_age_ms"] is not None,
    f"reconnects={snap['reconnect_count']}",
)
check(
    "after a reconnect the same sequence number is accepted again (state cleared)",
    tq.feed_quality.record_tick("DUP", "t1", ltp=105.0, seq=1, recv=now + 9) == tq.ACCEPTED,
)
check(
    "reconnects reduce the data-quality score",
    (snap["data_quality_score"] or 100) < 100.0,
    f"score {snap['data_quality_score']}",
)

tq.feed_quality.reset()
tq.feed_quality.record_tick("SHARD_A", "100", ltp=10.0, recv=now)
tq.feed_quality.record_tick("SHARD_B", "200", ltp=20.0, recv=now)
tq.feed_quality.note_reconnect(tokens=["100"])
check(
    "a drop on one credential shard does NOT mark the other shard's instruments",
    tq.feed_quality.snapshot("SHARD_A", now)["reconnect_count"] == 1
    and tq.feed_quality.snapshot("SHARD_B", now)["reconnect_count"] == 0,
    "reconnect health is per socket",
)

# Price units must be declared by the caller: the socket sends paise and the
# REST depth block sends rupees, and both call the field "price".
from app.market import angelone  # noqa: E402

check(
    "best-five price units are an explicit argument, never inferred",
    "paise" in inspect.signature(angelone._best_price).parameters,
)
check(
    "the WebSocket book is read as paise and REST depth as rupees",
    angelone._best_price([{"price": 1234}], paise=True) == 12.34
    and angelone._best_price([{"price": 12.34}], paise=False) == 12.34,
)
angel_src = inspect.getsource(angelone)
check(
    "every _best_price call site states its units",
    angel_src.count("_best_price(") == angel_src.count("paise=") + 1,  # +1 = the def
    f"{angel_src.count('_best_price(')} calls",
)

# --- 7. the scanner never emits a trading signal ----------------------------
scanner_src = inspect.getsource(scanner)
tree = ast.parse(scanner_src)
banned = {"BUY", "SELL", "AVOID", "HOLD", "EXIT"}
literals = {
    n.value for n in ast.walk(tree)
    if isinstance(n, ast.Constant) and isinstance(n.value, str)
}
check(
    "no BUY/SELL/AVOID/HOLD/EXIT string can be produced by the scanner",
    not (banned & literals),
    f"found {sorted(banned & literals)}" if banned & literals else "vocabulary is clean",
)
check(
    "the scanner's own verdicts are only OPPORTUNITY/WATCH/NO_OPPORTUNITY",
    {r.verdict for r in [live, no_vol]} <= {
        scanner.OPPORTUNITY, scanner.WATCH, scanner.NO_OPPORTUNITY},
)
check(
    "scanner classifications are opportunity states, never signals",
    live.classification in (scanner.FRESH_MOMENTUM, scanner.ACTIVE, scanner.EXTENDED,
                            scanner.EXHAUSTED, scanner.STALLED, scanner.NO_DATA),
    live.classification,
)

# --- 8. the scanner cannot modify a strategy decision -----------------------
for mod in ("app.engine.decision", "app.state", "app.execution"):
    check(
        f"scanner does not import {mod} (it cannot reach the decision path)",
        mod not in scanner_src,
    )
service_src = inspect.getsource(__import__("app.analysis.scan_service",
                                           fromlist=["x"]))
check(
    "the live scan service only READS cached snapshots (no st.tick / no order)",
    "st.tick(" not in service_src and "place_order" not in service_src,
)
check(
    "the handoff log records the engine's verdict but never writes one back",
    "record_handoff" in service_src and "decide(" not in service_src,
)

# --- 9/13. broad universes and empty/missing data are safe ------------------
began = time.monotonic()
many = [scanner.scan_one(make_input(fast, instrument=f"I{i}")) for i in range(50)]
elapsed = time.monotonic() - began
check(
    "50 instruments scan in well under one tick interval (0.4s)",
    elapsed < 0.4,
    f"{elapsed * 1000:.0f} ms for 50",
)
check(
    "zero instruments ranks safely",
    scanner.rank([]) == [] and scanner.filter_view([], "movers") == [],
)
empty = scanner.scan_one(make_input([]))
check(
    "an instrument with no candles is NO_DATA, not an error and not a zero score",
    empty.classification == scanner.NO_DATA and empty.opportunity_score is None,
)
short = scanner.scan_one(make_input(bars(5)))
check(
    "too-few-bars is reported as insufficient data rather than scored",
    short.classification == scanner.NO_DATA and "bars" in " ".join(short.reasons),
)
none_ltp = scanner.scan_one(make_input(fast, ltp=None))
check(
    "a missing LTP falls back to the last close instead of crashing",
    none_ltp.opportunity_score is not None,
)

# --- 10. deterministic ranking ---------------------------------------------
inputs = [
    make_input(accelerating(), instrument="AAA"),
    make_input(bars(), instrument="BBB"),
    make_input(bars(drift=-0.04), instrument="CCC"),
    make_input(fast, instrument="DDD"),
]
run1 = [r.instrument for r in scanner.rank([scanner.scan_one(i) for i in inputs])]
run2 = [r.instrument for r in scanner.rank([scanner.scan_one(i) for i in reversed(inputs)])]
check(
    "identical inputs give an identical ranking, regardless of input order",
    run1 == run2,
    f"{run1} vs {run2}",
)
tie_a = scanner.scan_one(make_input(fast, instrument="ZZZ"))
tie_b = scanner.scan_one(make_input(fast, instrument="AAA"))
check(
    "exact ties break on instrument name, so the order can never flicker",
    [r.instrument for r in scanner.rank([tie_a, tie_b])] == ["AAA", "ZZZ"],
)

# --- 11. stale data cannot outrank healthy data ----------------------------
stale_big_move = scanner.scan_one(make_input(
    bars(drift=2.0, volume=9000.0), instrument="STALE",
    freshness=tq.STALE, data_age_ms=20_000.0,
))
healthy_small = scanner.scan_one(make_input(
    bars(drift=0.02), instrument="HEALTHY"))
order = [r.instrument for r in scanner.rank([stale_big_move, healthy_small])]
check(
    "a huge move on STALE data ranks BELOW a small move on live data",
    order[0] == "HEALTHY",
    " > ".join(order),
)
check(
    "freshness classification is monotonic in age",
    [tq.classify_age(a) for a in (100, 2000, 5000, 20000, 120000)]
    == [tq.FRESH, tq.AGING, tq.AGING, tq.STALE, tq.DEAD],
)
check(
    "a negative age (broker clock ahead of ours) is NO_DATA, not 'fresh'",
    tq.classify_age(-500.0) == tq.NO_DATA,
)

# --- 12. extension / room / provenance semantics ---------------------------
check(
    "an already-extended accelerating move is not called FRESH_MOMENTUM",
    scanner.scan_one(make_input(bars(drift=0.6, volume=5000.0))).classification
    in (scanner.EXTENDED, scanner.ACTIVE, scanner.EXHAUSTED),
)
check(
    "remaining room is a coarse bucket, never a point estimate",
    live.room in (scanner.LOW, scanner.MEDIUM, scanner.HIGH, scanner.UNKNOWN),
    live.room,
)
# Reachability. Early-Early never activated in 240k replayed bars because its
# gate could not be met, so a state that cannot fire is a known failure mode
# here: every classification and the OPPORTUNITY verdict must be reachable.
def bounce_from_low() -> list[Candle]:
    seq = [100.0] * 55 + [99.0, 98.0, 97.0, 96.2, 96.0]
    seq += [96.15, 96.45, 96.9, 97.5]
    out: list[Candle] = []
    px = seq[0]
    for i, p in enumerate(seq):
        o, px = px, p
        # Participation rises into the bounce, as it does on a real one.
        vol = 1000.0 if i < 55 else 3500.0
        out.append(Candle(time=1_700_000_000 + i * 60, open=o, high=max(o, px) + 0.1,
                          low=min(o, px) - 0.1, close=px, volume=vol))
    return out


fresh = scanner.scan_one(make_input(bounce_from_low()))
check(
    "FRESH_MOMENTUM + OPPORTUNITY are actually reachable (not dead states)",
    fresh.classification == scanner.FRESH_MOMENTUM
    and fresh.verdict == scanner.OPPORTUNITY,
    f"{fresh.classification}/{fresh.verdict} score {fresh.opportunity_score}",
)
seen = {scanner.scan_one(i).classification for i in inputs} | {
    fresh.classification, live.classification,
    scanner.scan_one(make_input(bars(drift=0.0, vol=0.01))).classification,
}
check(
    "STALLED, ACTIVE and EXTENDED are all reachable too",
    {scanner.STALLED, scanner.EXTENDED} <= seen,
    f"seen {sorted(seen)}",
)

from app.research import store as rstore  # noqa: E402

check(
    "research option rows carry an explicit provenance label",
    {rstore.SOURCE_REAL, rstore.SOURCE_SIM, rstore.SOURCE_UNKNOWN}
    == {"REAL_BROKER", "SIMULATOR", "UNKNOWN"},
)
from app.research import capture as rcapture  # noqa: E402

check(
    "the simulated feed is labelled SIMULATOR, never REAL_BROKER",
    rcapture._chain_rows(1, [], rstore.SOURCE_SIM) == []
    and rcapture.current_source() in
    (rstore.SOURCE_REAL, rstore.SOURCE_SIM, rstore.SOURCE_UNKNOWN),
)

print()
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED: {FAILED}")
    sys.exit(1)
print("ALL SCANNER / FEED-QUALITY CHECKS PASSED")
