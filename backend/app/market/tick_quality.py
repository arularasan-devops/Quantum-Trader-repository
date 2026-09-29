"""Per-instrument feed quality, tick accounting and end-to-end latency chain.

Pure observability: nothing here is consulted by the decision engine, the gates
or the execution layer. It answers questions the app previously could not:

* how old is the price this instrument is being analysed on (in ms, not "live")
* did we drop, duplicate or receive ticks out of order
* where in the chain the time actually goes (exchange -> receive -> cache ->
  process -> decision -> render)

Design notes
------------
* A tick is only counted once. Angel's SNAP_QUOTE carries ``sequence_number``
  and ``exchange_timestamp``; when present they are used to detect duplicates
  and out-of-order delivery properly instead of guessing from the price.
* ``record_tick`` returns a verdict and the caller decides what to do with it.
  The live provider currently stores every tick it is given, so the verdict is
  reported, not enforced — see the Phase 4 report. The scanner's own cache
  (``accepted_ltp``) DOES enforce ordering, so a late/duplicate tick can never
  move a scanner input.
* Nothing is fabricated. Unknown stays ``None``; a missing exchange timestamp
  yields ``None`` latency rather than a zero.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

# Freshness thresholds (ms). PROVISIONAL defaults derived from the code's own
# cadences, NOT from a measured live session:
#   * push feed: Angel SNAP_QUOTE arrives per trade/edge, sub-second on a liquid
#     contract, so anything under ~1.5s is indistinguishable from real time;
#   * REST fallback: internally floored at one poll per 3s, so a healthy REST
#     instrument sits at 0-3s and must NOT be called stale;
#   * the provider already treats 8s without a push as "socket gone quiet"
#     (_WS_STALE_SECONDS), which is the natural STALE boundary;
#   * DEAD is deliberately far out (60s) so a quiet-but-alive illiquid contract
#     is not declared dead merely for not trading.
# ``/api/feed-quality`` reports the measured age distribution so these can be
# retuned on real data instead of by taste.
FRESH_MS = 1500.0
AGING_MS = 4000.0
STALE_MS = 8000.0
DEAD_MS = 60000.0

FRESH = "FRESH"
AGING = "AGING"
STALE = "STALE"
DEAD = "DEAD"
NO_DATA = "NO_DATA"

# Verdicts from record_tick
ACCEPTED = "ACCEPTED"
DUPLICATE = "DUPLICATE"
OUT_OF_ORDER = "OUT_OF_ORDER"
INVALID = "INVALID"

_STAGES = ("receive", "cache", "process", "decision", "render")


def classify_age(age_ms: float | None,
                 *,
                 fresh_ms: float = FRESH_MS,
                 aging_ms: float = AGING_MS,
                 stale_ms: float = STALE_MS,
                 dead_ms: float = DEAD_MS) -> str:
    """Classify a feed age in milliseconds. ``None`` (never ticked) is NO_DATA.

    Deliberately total and monotonic: a larger age can never map to a fresher
    class, which is what test 11 pins (old movement must not outrank live data).
    """
    if age_ms is None:
        return NO_DATA
    if age_ms < 0:
        # A clock skew between the exchange stamp and our clock is not freshness.
        return NO_DATA
    if age_ms >= dead_ms:
        return DEAD
    if age_ms > stale_ms:
        return STALE
    if age_ms > aging_ms:
        return AGING
    if age_ms <= fresh_ms:
        return FRESH
    return AGING


def is_tradable_freshness(state: str) -> bool:
    """FRESH/AGING data may be ranked; STALE/DEAD/NO_DATA may not."""
    return state in (FRESH, AGING)


@dataclass
class _TokenState:
    last_seq: int | None = None
    last_exch_ms: int | None = None
    last_price: float | None = None
    last_recv: float = 0.0


@dataclass
class InstrumentFeed:
    """Mutable per-instrument tick accounting. Guarded by the tracker's lock."""

    instrument: str
    ticks: int = 0
    accepted: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    invalid: int = 0
    reconnects: int = 0
    rest_polls: int = 0
    first_tick_at: float = 0.0
    last_tick_at: float = 0.0
    last_accepted_price: float | None = None
    last_exchange_ms: int | None = None
    max_gap_ms: float = 0.0
    # rolling stage timestamps (monotonic-independent wall clock, seconds)
    stages: dict[str, float] = field(default_factory=dict)
    # measured ages (ms) for percentile reporting; bounded ring
    age_samples: list[float] = field(default_factory=list)
    tokens: dict[str, _TokenState] = field(default_factory=dict)
    source: str = "unknown"  # push | rest | unknown

    def age_ms(self, now: float) -> float | None:
        if not self.last_tick_at:
            return None
        return max(0.0, (now - self.last_tick_at) * 1000.0)

    def exchange_lag_ms(self) -> float | None:
        """exchange_timestamp -> our receive time, for the last accepted tick."""
        if self.last_exchange_ms is None or not self.last_tick_at:
            return None
        lag = self.last_tick_at * 1000.0 - float(self.last_exchange_ms)
        # A negative lag means the broker clock is ahead of ours; report None
        # rather than a fictional "0 ms" or a negative latency.
        return round(lag, 1) if lag >= 0 else None


_SAMPLE_CAP = 600


class FeedQuality:
    """Process-wide tick-quality tracker. Thread-safe; cheap per tick."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_inst: dict[str, InstrumentFeed] = {}

    # ------------------------------------------------------------- recording
    def _feed(self, instrument: str) -> InstrumentFeed:
        f = self._by_inst.get(instrument)
        if f is None:
            f = InstrumentFeed(instrument=instrument)
            self._by_inst[instrument] = f
        return f

    def record_tick(
        self,
        instrument: str,
        token: str,
        *,
        ltp: float | None,
        exchange_ms: int | None = None,
        seq: int | None = None,
        recv: float | None = None,
        source: str = "push",
    ) -> str:
        """Account for one inbound tick and return a verdict.

        The verdict is advisory for the production cache (which stores what the
        broker sends) and authoritative for :meth:`accepted_ltp`, which the
        scanner reads.
        """
        now = recv if recv is not None else time.time()
        with self._lock:
            f = self._feed(instrument)
            f.source = source
            f.ticks += 1
            if source == "rest":
                f.rest_polls += 1
            ts = f.tokens.get(token)
            if ts is None:
                ts = _TokenState()
                f.tokens[token] = ts

            if ltp is None or ltp <= 0:
                f.invalid += 1
                return INVALID

            verdict = ACCEPTED
            if seq is not None and ts.last_seq is not None:
                if seq == ts.last_seq:
                    verdict = DUPLICATE
                elif seq < ts.last_seq:
                    verdict = OUT_OF_ORDER
            elif exchange_ms is not None and ts.last_exch_ms is not None:
                if exchange_ms == ts.last_exch_ms and ltp == ts.last_price:
                    verdict = DUPLICATE
                elif exchange_ms < ts.last_exch_ms:
                    verdict = OUT_OF_ORDER

            if verdict == DUPLICATE:
                f.duplicates += 1
                return DUPLICATE
            if verdict == OUT_OF_ORDER:
                f.out_of_order += 1
                return OUT_OF_ORDER

            # accepted
            if f.last_tick_at:
                gap = (now - f.last_tick_at) * 1000.0
                if gap > f.max_gap_ms:
                    f.max_gap_ms = gap
            else:
                f.first_tick_at = now
            f.accepted += 1
            f.last_tick_at = now
            f.last_accepted_price = float(ltp)
            f.last_exchange_ms = exchange_ms
            ts.last_seq = seq if seq is not None else ts.last_seq
            ts.last_exch_ms = exchange_ms if exchange_ms is not None else ts.last_exch_ms
            ts.last_price = float(ltp)
            ts.last_recv = now
            if exchange_ms:
                lag = now * 1000.0 - float(exchange_ms)
                if lag >= 0:
                    f.age_samples.append(lag)
                    if len(f.age_samples) > _SAMPLE_CAP:
                        del f.age_samples[: len(f.age_samples) - _SAMPLE_CAP]
            f.stages["receive"] = now
            return ACCEPTED

    def note_reconnect(
        self,
        instrument: str | None = None,
        tokens: Iterable[str] | None = None,
    ) -> None:
        """A socket drop/rebuild invalidates freshness for everything it served.

        The per-instrument age is NOT reset to "fresh" — the whole point is that
        after a reconnect we genuinely do not have a current price until the
        next tick arrives.

        ``tokens`` scopes the damage to one socket's own subscriptions. With
        several credential shards, one shard's drop must not mark the
        instruments of a healthy shard as reconnected: that would report a
        broken feed where none exists. With neither argument the whole book is
        marked, which is only correct for a single-socket deployment.
        """
        with self._lock:
            if instrument is not None:
                targets = [self._feed(instrument)]
            elif tokens is not None:
                owned = {str(t) for t in tokens}
                targets = [
                    f for f in self._by_inst.values()
                    if owned & set(f.tokens.keys())
                ]
            else:
                targets = list(self._by_inst.values())
            for f in targets:
                f.reconnects += 1
                f.tokens.clear()
                f.last_exchange_ms = None

    def mark_stage(self, instrument: str, stage: str, at: float | None = None) -> None:
        if stage not in _STAGES:
            return
        with self._lock:
            self._feed(instrument).stages[stage] = at if at is not None else time.time()

    def accepted_ltp(self, instrument: str) -> float | None:
        """Last price that passed the ordering/validity checks.

        This is what the scanner reads, so a duplicate or a late tick cannot
        move a scanner input even though the production quote cache keeps its
        own (unguarded) copy.
        """
        with self._lock:
            f = self._by_inst.get(instrument)
            return f.last_accepted_price if f else None

    # -------------------------------------------------------------- reporting
    @staticmethod
    def _quality_score(f: InstrumentFeed, age_ms: float | None) -> float | None:
        """0-100 data-quality score. ``None`` when there is nothing to judge."""
        if f.ticks == 0:
            return None
        total = float(f.ticks)
        penalties = 0.0
        penalties += 25.0 * (f.duplicates / total)
        penalties += 40.0 * (f.out_of_order / total)
        penalties += 40.0 * (f.invalid / total)
        penalties += min(15.0, 5.0 * f.reconnects)
        state = classify_age(age_ms)
        if state == AGING:
            penalties += 10.0
        elif state == STALE:
            penalties += 35.0
        elif state in (DEAD, NO_DATA):
            penalties += 60.0
        return round(max(0.0, 100.0 - penalties), 1)

    def snapshot(self, instrument: str, now: float | None = None) -> dict:
        now = now if now is not None else time.time()
        with self._lock:
            f = self._by_inst.get(instrument)
            if f is None:
                return {
                    "instrument": instrument,
                    "ticks_received": 0,
                    "state": NO_DATA,
                    "data_quality_score": None,
                    "last_tick_age_ms": None,
                }
            age = f.age_ms(now)
            elapsed = max(1e-6, now - (f.first_tick_at or now))
            samples = sorted(f.age_samples)
            chain: dict[str, float | None] = {}
            order = list(_STAGES)
            for a, b in zip(order, order[1:]):
                ta, tb = f.stages.get(a), f.stages.get(b)
                chain[f"{a}_to_{b}_ms"] = (
                    round((tb - ta) * 1000.0, 1) if ta and tb and tb >= ta else None
                )
            return {
                "instrument": instrument,
                "source": f.source,
                "ticks_received": f.ticks,
                "ticks_accepted": f.accepted,
                "ticks_per_second": round(f.accepted / elapsed, 2),
                "last_tick_age_ms": None if age is None else round(age, 1),
                "max_tick_gap_ms": round(f.max_gap_ms, 1) if f.max_gap_ms else None,
                "duplicate_count": f.duplicates,
                "out_of_order_count": f.out_of_order,
                "invalid_count": f.invalid,
                "reconnect_count": f.reconnects,
                "rest_polls": f.rest_polls,
                "state": classify_age(age),
                "data_quality_score": self._quality_score(f, age),
                # Both ends of the latency pair, not just their difference: a
                # signal audit has to be able to say WHEN the exchange stamped
                # the price and WHEN we received it, because a plausible lag
                # computed from two wrong clocks still reads plausible.
                "last_exchange_ts": (f.last_exchange_ms / 1000.0
                                     if f.last_exchange_ms is not None else None),
                "last_receive_ts": f.last_tick_at or None,
                "exchange_to_receive_ms": f.exchange_lag_ms(),
                "exchange_lag_p50_ms": round(samples[len(samples) // 2], 1) if samples else None,
                "exchange_lag_p95_ms": (
                    round(samples[min(len(samples) - 1, int(len(samples) * 0.95))], 1)
                    if samples else None
                ),
                "latency_chain": chain,
                "total_end_to_end_ms": (
                    round((f.stages["render"] - f.stages["receive"]) * 1000.0, 1)
                    if f.stages.get("render") and f.stages.get("receive")
                    and f.stages["render"] >= f.stages["receive"] else None
                ),
            }

    def all_snapshots(self, now: float | None = None) -> list[dict]:
        now = now if now is not None else time.time()
        with self._lock:
            names = sorted(self._by_inst.keys())
        return [self.snapshot(n, now) for n in names]

    def summary(self, now: float | None = None) -> dict:
        rows = self.all_snapshots(now)
        counts = {FRESH: 0, AGING: 0, STALE: 0, DEAD: 0, NO_DATA: 0}
        for r in rows:
            counts[r.get("state", NO_DATA)] = counts.get(r.get("state", NO_DATA), 0) + 1
        return {
            "instruments": len(rows),
            "fresh": counts[FRESH],
            "aging": counts[AGING],
            "stale": counts[STALE],
            "dead": counts[DEAD],
            "no_data": counts[NO_DATA],
            "thresholds_ms": {
                "fresh": FRESH_MS, "aging": AGING_MS,
                "stale": STALE_MS, "dead": DEAD_MS,
            },
            "thresholds_provisional": True,
        }

    def reset(self) -> None:
        with self._lock:
            self._by_inst.clear()


# Process-wide singleton used by the provider (push) and the tick loop (REST).
feed_quality = FeedQuality()
