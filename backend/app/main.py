"""FastAPI application: REST + WebSocket streaming for Quantum Trader.

Endpoints
  GET  /health              liveness probe
  GET  /api/snapshot        latest full analysis snapshot (one-shot)
  POST /api/buy             open a long-option position {option_symbol, lots}
  POST /api/sell            close the open position
  GET  /api/journal         completed-trade journal
  WS   /ws                  pushes a Snapshot every tick_interval_seconds
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.analysis import signal_visibility
from app.config import settings
from app.engine import weights as weights_store
from app.engine.account_risk import account as account_risk
from app.execution import futures_paper
from app.market import instruments as _inst_mod
from app.market.instruments import DEFAULT_INSTRUMENT
from app.research.pairs import spec as pair_spec
from app.research.phase17 import store as p17store
from app.state import registry

_log = logging.getLogger("quantum.api")

app = FastAPI(title="Quantum Trader Engine", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class BuyRequest(BaseModel):
    option_symbol: str
    lots: int = 1
    confirm: bool = False
    instrument: str | None = None


class SellRequest(BaseModel):
    confirm: bool = False
    instrument: str | None = None


class AutoTradeRequest(BaseModel):
    enabled: bool | None = None
    min_confidence: float | None = None
    max_lots: int | None = None
    safe_sizing: bool | None = None
    ride_trail: bool | None = None
    trail_pct: float | None = None
    capital: float | None = None
    risk_per_trade_pct: float | None = None
    daily_profit_target: float | None = None
    max_daily_loss: float | None = None
    allow_live: bool | None = None
    live_max_order_value: float | None = None
    live_max_lots: int | None = None
    zero_to_hero_enabled: bool | None = None
    zero_to_hero_budget: float | None = None
    max_concurrent: int | None = None
    momentum_entry: bool | None = None
    no_chase: bool | None = None
    pre_t1_trail: bool | None = None
    instrument_capital: dict[str, float] | None = None
    instrument_lots: dict[str, int] | None = None


class AutoBuyRequest(BaseModel):
    """Single on/off switch for the one-click LIVE auto-buy."""

    on: bool


class InstrumentRequest(BaseModel):
    instrument: str


class CaptureRequest(BaseModel):
    instrument: str | None = None
    steps: int = 400


class ReplayRequest(BaseModel):
    instrument: str | None = None
    speed: int = 100


class ShadowRequest(BaseModel):
    instrument: str | None = None
    horizon_minutes: int | None = None


class DownloadRequest(BaseModel):
    instrument: str | None = None
    days: int = 120


def _norm_instrument(name: str | None) -> str:
    return (name or DEFAULT_INSTRUMENT).upper()


def _exchange_open(instrument: str) -> bool:
    """Is THIS instrument's exchange trading right now?

    Per-instrument, because MCX runs to 23:30 IST while NSE/BSE stop at 15:30.
    ``ignore_market_hours`` (the simulated 24/7 demo default) keeps everything
    open, so the simulator and the smokes are unaffected.
    """
    if settings.ignore_market_hours:
        return True
    from app.market.instruments import get_spec

    try:
        exchange = get_spec(instrument).exchange
    except Exception:
        return True
    is_open, _ = settings.market_session(time.time(), exchange)
    return is_open


# ---- background tick loop: advance every ACTIVE instrument and cache its
# latest snapshot, then push it only to the clients watching that instrument.
# Ticking per-instrument (not a single global one) is what lets separate tabs
# stream Crude and Nifty at the same time. ----
def _mark_feed_stage(instrument: str, stage: str) -> None:
    """Stamp one stage of the tick->render chain for an instrument.

    Keyed by the provider's scrip root (what a tick actually carries), which is
    not always the registry key. Pure observability: a failure here can never
    affect a tick.
    """
    try:
        from app.market.instruments import get_spec
        from app.market.tick_quality import feed_quality

        feed_quality.mark_stage(get_spec(instrument).symbol, stage)
    except Exception:
        pass


class Hub:
    # How many screener-only (unwatched) instruments to refresh per background
    # turn. Small, so the instrument you're viewing always ticks first and fast.
    _BG_PER_CYCLE = 3
    # Background instruments only refresh every Nth loop, so the watched
    # instrument isn't dragged behind a queue of full-engine computes every
    # cycle — this is what keeps the on-screen LTP fast with many instruments.
    _BG_EVERY_N = 4
    # Fast refresh cadence (seconds) for the instrument you're watching. The
    # WebSocket has already cached the latest tick, so re-ticking this often just
    # recomputes from cache (cheap) — it does NOT add Angel REST load, because
    # the REST fallback stays internally throttled to >=3s.
    _WATCHED_INTERVAL = 0.4
    # Longest a SCANNED instrument may go without a tick while the auto-trader is
    # armed. The bot needs every instrument checked often enough not to miss an
    # entry, but it does NOT need the 0.4s cadence of the instrument on screen —
    # option quotes do not refresh that fast anyway.
    _SCAN_MAX_AGE = 2.0
    # Share of a cycle that may be spent on scanned instruments. Without this,
    # a 50-name universe pushes the watched instrument's refresh from 0.4s to
    # over a second: the scan itself becomes the LTP delay it was meant to find.
    _SCAN_BUDGET = 0.6
    # Scanned instruments ticked per cycle NO MATTER WHAT, even when the watched
    # ticks have already spent the whole budget. Without a floor the scan can
    # stop dead while every dial still reads "armed", which is exactly what
    # happened on a 46-name universe (median scan age 13 minutes).
    _SCAN_MIN_PER_CYCLE = 2
    # Threads that may run instrument ticks. A tick abandoned on the budget
    # leaves its thread wedged wherever it blocked, so this is also the number
    # of wedges the loop tolerates before it must stop starting ticks: more
    # than the one a healthy loop has in flight, and small enough that
    # exhaustion is reached and reported rather than hidden behind a growing
    # queue. Its own pool, not the default executor, because everything else
    # that runs off the loop — a report, the nightly snapshot, a history
    # fetch — would otherwise queue behind a wedged provider call it has
    # nothing to do with.
    _TICK_WORKERS = 4

    @property
    def scan_target_sec(self) -> float:
        """The staleness a scanned instrument is expected to stay within."""
        return self._SCAN_MAX_AGE

    @property
    def tick_cost_ms(self) -> float | None:
        """Measured cost of one instrument tick, in milliseconds."""
        return None if self._tick_cost is None else round(self._tick_cost * 1000, 1)

    @property
    def scanned_per_cycle(self) -> float:
        """Scanned (unwatched) instruments per cycle, averaged over recent ones."""
        return round(self._scan_rate, 2)

    @property
    def ticks_abandoned(self) -> int:
        """Ticks this process stopped waiting for, and never took a price from.

        Reported rather than absorbed: the budget bounds what one wedged
        instrument costs the other 45, it does not recover the minute, and a
        rising count here is a provider or tick-path fault that is still open.
        """
        return self._ticks_abandoned

    @property
    def ticks_wedged_now(self) -> int:
        """Tick workers still held by an abandoned tick, right now."""
        return len([f for f in self._wedged if not f.done()])

    @property
    def closed_skipped(self) -> int:
        """Instruments the last cycle skipped because their exchange was shut."""
        return self._closed_skipped

    def sweep_estimate_sec(self, n_scanned: int) -> float | None:
        """How long one full pass over ``n_scanned`` instruments really takes.

        The 2s target is what a scan should achieve; this is what the measured
        per-tick cost and the per-cycle scan floor actually deliver. Reporting
        only the target hid a scan that had stopped: "worst 907s against a 2s
        target" says something is wrong but not that 46 names cannot fit.
        """
        if n_scanned <= 0 or self._tick_cost is None:
            return None
        per_cycle = self._scan_rate if self._scan_rate > 0 else float(self._SCAN_MIN_PER_CYCLE)
        cycle_sec = max(self._WATCHED_INTERVAL, self._tick_cost * per_cycle)
        return round(n_scanned / per_cycle * cycle_sec, 1)

    def __init__(self) -> None:
        # ws -> instrument it subscribed to
        self.clients: dict[WebSocket, str] = {}
        # instrument -> latest snapshot
        self.latest: dict[str, object] = {}
        # instrument -> monotonic time of its last tick, for the scan cadence
        self._ticked_at: dict[str, float] = {}
        # Measured cost of one tick (EMA, seconds) and how many scanned
        # instruments the last cycle got through. Both are reported, because the
        # only honest answer to "why is my scan behind" is the real numbers.
        self._tick_cost: float | None = None
        # Scanned instruments per cycle, smoothed. A single cycle's count is a
        # misleading number to report: unarmed the scan runs every 4th cycle, so
        # the instantaneous value is 0 three times out of four.
        self._scan_rate: float = 0.0
        # How many instruments the last cycle skipped because their exchange was
        # shut — reported, so "only 6 scanned" reads as the session rather than as
        # a starved scan.
        self._closed_skipped: int = 0
        # Monotonic time of the last frozen-pair sample (research capture only).
        self._pair_captured_at: float = 0.0
        # Ticks the loop stopped waiting for, and the threads still held by
        # them. Counted because a bounded loss still has to be a measured one:
        # abandoning the wait keeps every other instrument ticking, it does not
        # make the wedged instrument's minute appear.
        self._ticks_abandoned: int = 0
        self._tick_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=self._TICK_WORKERS, thread_name_prefix="tick")
        self._wedged: set[concurrent.futures.Future] = set()

    def scan_ages(self) -> dict[str, float]:
        """Seconds since each active instrument was last ticked.

        Measured, not modelled: this is the only honest answer to "is my
        watchlist too big" — a name whose age exceeds ``_SCAN_MAX_AGE`` is one
        the scanner is not keeping up with, so a BUY on it can be seen late.
        """
        mono = time.monotonic()
        return {inst: round(mono - ts, 2) for inst, ts in list(self._ticked_at.items())}

    async def _tick_one(self, st, loop) -> None:
        """Advance one instrument, cache it, push it to its subscribers."""
        inst = st.instrument
        began = time.monotonic()
        # Breadcrumb for the stall witness on the heartbeat thread: in memory,
        # no I/O, and ended in a finally so a raising tick cannot leave the
        # loop looking permanently wedged. Diagnostic only — nothing reads it
        # to make a decision, and a failure here must not cost a tick.
        with contextlib.suppress(Exception):
            p17store.note_tick_begin(inst, mono=began)
        # run the (CPU-bound, sync) analysis off the event loop, under a budget
        #
        # The loop is serial, so before this an instrument whose tick blocked
        # held every other instrument with it: the stall journal carries a
        # 108-second wedge inside one MCX tick, and those minutes are missing
        # for all 46 names, not one. The wait is therefore bounded and the tick
        # abandoned, which costs the wedged instrument exactly the tick it had
        # already lost and returns the loop to the others.
        #
        # A thread in a blocking read cannot be cancelled, so the abandoned
        # tick keeps its worker until the call underneath it returns or times
        # out. That is why the pool is separate and small, and why exhausting
        # it refuses the tick outright: queueing behind four wedged workers
        # would reproduce the stall with the journal reading normally.
        self._wedged = {f for f in self._wedged if not f.done()}
        if len(self._wedged) >= self._TICK_WORKERS:
            with contextlib.suppress(Exception):
                p17store.note_tick_end()
                p17store.note_abandoned_tick(
                    inst, stuck_sec=0.0, where=p17store.TICK_WORKERS_WEDGED)
            return
        fut = loop.run_in_executor(self._tick_pool, st.tick)
        try:
            snapshot = await asyncio.wait_for(
                asyncio.shield(fut), timeout=p17store.TICK_BUDGET_SEC)
        except asyncio.TimeoutError:
            self._wedged.add(fut)
            self._ticks_abandoned += 1
            # Nothing awaits this future any more, so whatever it ends up
            # doing has to be collected here or the loop reports it as an
            # exception nobody retrieved.
            fut.add_done_callback(lambda f: f.exception())
            with contextlib.suppress(Exception):
                p17store.note_abandoned_tick(
                    inst, stuck_sec=time.monotonic() - began)
            # No snapshot is cached and none is pushed: whatever the wedged
            # thread eventually returns describes a market that has moved on,
            # and a late quote presented as current is worse than a gap the
            # coverage report can see.
            return
        finally:
            with contextlib.suppress(Exception):
                p17store.note_tick_end()
        took = time.monotonic() - began
        self._tick_cost = took if self._tick_cost is None else self._tick_cost * 0.9 + took * 0.1
        self._ticked_at[inst] = time.monotonic()
        self.latest[inst] = snapshot
        payload = snapshot.model_dump(mode="json")
        dead = []
        for ws, sub in list(self.clients.items()):
            if sub != inst:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
                continue
            # This signal reached a client. Recorded once per signal, so a
            # missing dashboard signal can be told from one nobody was watching.
            signal_visibility.mark_user_visible(inst, snapshot.decision, "ws")
        for ws in dead:
            self.clients.pop(ws, None)
        # Close the timestamp chain: this is the moment the price the engine used
        # actually left the server. Observability only.
        _mark_feed_stage(inst, "render")

    # Seconds between samples of the frozen pair's two legs. One minute bar is
    # the unit of the study, so a few seconds is enough to catch every bar while
    # the socket is alive; it is not a tick and computes no signal.
    _PAIR_CAPTURE_EVERY = 5.0

    def _capture_pair_legs(self, active: list) -> None:
        """Sample the frozen pair's legs on their own cadence (research only).

        Capture used to ride on the tick, which is the scan's clock, not the
        study's: a leg that is not being watched is ticked when the round-robin
        reaches it, so with a large universe one leg was read a handful of times
        an hour while the other -- the default instrument -- was read every
        cycle. A pair needs BOTH legs on the SAME minute, so the observations
        that carried a real book on both sides almost never happened (measured
        share 9.7% on the first live session).

        This reads only what the push feed has already delivered: no broker
        request, no engine evaluation, no change to which instruments the
        scanner prioritises or to when a production decision is computed. If a
        leg is absent from the active universe it is simply not sampled -- the
        coverage report already says which leg is missing.
        """
        if not settings.pair_capture_enabled:
            return
        now = time.monotonic()
        if now - self._pair_captured_at < self._PAIR_CAPTURE_EVERY:
            return
        self._pair_captured_at = now
        legs = {pair_spec.LEG_A, pair_spec.LEG_B}
        for st in active:
            if st.instrument in legs:
                try:
                    st.capture_pair_leg()
                except Exception:  # pragma: no cover - research must not break the loop
                    _log.exception("pair capture failed for %s", st.instrument)

    async def _resolve_shadow_paper(self, loop) -> None:
        """Let Phase 45's paper outcomes catch up with the clock (research only).

        The shadow journal records a decision when it happens; what the decision
        would have made can only be read once the forward window exists. This
        walks the pending events off the event loop, rate-limited inside the
        service so this call is cheap on the cycles it does nothing on, and
        never raising: an unresolvable batch is counted, not propagated. It
        writes only to the shadow store — no production state, no order path.
        """
        if not settings.phase45_shadow:
            return
        from app.research.phase45 import service as p45

        try:
            await loop.run_in_executor(None, p45.resolve_due)
        except Exception:  # pragma: no cover - research must not break the loop
            _log.exception("phase45 paper resolution failed")

    async def run(self) -> None:
        loop = asyncio.get_event_loop()
        rr = 0  # round-robin cursor over background (screener-only) instruments
        cycle = 0
        next_movers = 0.0  # monotonic time of the next auto day-movers check
        while True:
            cycle += 1
            # Re-run the daily auto-pick when the IST day rolls over on a
            # long-running server, then warm any newly-picked instruments.
            if settings.auto_pick_enabled and cycle % 120 == 1:
                changed = await loop.run_in_executor(None, _refresh_universe, False)
                if changed is not None:
                    app.state.warm = asyncio.create_task(_warm_all())
            # Auto day-movers: pick the day's handful of instruments and hold
            # them. Kept on its own timer rather than a cycle count so the
            # interval means seconds regardless of how long a cycle takes, and
            # run in the executor because it scores every candidate.
            if settings.day_movers_auto_enabled and time.monotonic() >= next_movers:
                next_movers = time.monotonic() + max(30.0, settings.day_movers_refresh_sec)
                picked = await loop.run_in_executor(None, _day_movers_cycle)
                if picked["action"] in ("picked", "swapped"):
                    _log.info("day-movers %s: %s", picked["action"], picked["reason"])
                    app.state.warm = asyncio.create_task(_warm_all())
            # Always keep the default instrument warm; all other instruments are
            # warmed by the background _warm_all task, after which they appear in
            # registry.active() and get ticked here (populating the screener).
            registry.get(DEFAULT_INSTRUMENT)
            active = list(registry.active())
            self._capture_pair_legs(active)
            await self._resolve_shadow_paper(loop)

            # PRIORITY = instruments a client is actually watching right now. These
            # tick EVERY cycle so the on-screen LTP stays fast, no matter how many
            # instruments exist. BACKGROUND = the rest (only feeding the screener);
            # we tick just a few per cycle in round-robin so they never starve the
            # watched instrument. This is what keeps live prices fast after adding
            # many instruments.
            watched = set(self.clients.values())
            watched.add(DEFAULT_INSTRUMENT)
            # Any instrument holding an open (paper/auto) position MUST tick every
            # cycle too — otherwise its auto-exit at Target 1 / stop would only be
            # checked on the slow round-robin, letting price run past the level.
            for st in active:
                if st.position.option_symbol:
                    watched.add(st.instrument)
                # An open FUTURES paper position needs the same treatment: its
                # stop and locked floor are only checked on a tick, so leaving it
                # on the slow round-robin lets price run through both.
                if futures_paper.store.get(st.instrument) is not None:
                    watched.add(st.instrument)
            priority = [st for st in active if st.instrument in watched]
            background = [st for st in active if st.instrument not in watched]
            # Skip the background scan for instruments whose EXCHANGE is shut.
            # A closed name cannot produce an entry and its price cannot move, so
            # scanning it only spends the cycle budget and Angel's rate limit that
            # the still-trading names need: after 15:30 IST that is ~40 frozen
            # NSE/BSE names competing with Crude and Nat Gas. Nothing is dropped
            # or disabled — they return by themselves at the next open, because
            # this is evaluated every cycle rather than latched at startup.
            # Watched instruments and anything holding a position are NOT skipped:
            # the dashboard must still say "CLOSED" for the name you opened, and a
            # position must stay monitored right up to its own close.
            closed_now = [st for st in background if not _exchange_open(st.instrument)]
            if closed_now:
                background = [st for st in background if st not in closed_now]
            self._closed_skipped = len(closed_now)

            scan_queue: list = []
            # When the auto-trader is armed every active instrument must be
            # scanned often enough that a BUY elsewhere is not missed. Earlier
            # this ticked ALL of them every cycle, which does not scale: at 50
            # instruments the watched one waited behind 49 full engine computes
            # and its LTP visibly lagged. Instead the most OVERDUE scans go
            # first, under a time budget, so scan coverage degrades gracefully
            # with universe size while the on-screen price never does.
            # Futures counts as armed here too. Keying this on the OPTION
            # auto-trader alone is why the futures tool only ever traded Crude:
            # with just futures enabled, every instrument except the one on
            # screen refreshed on the slow round-robin (~25-30s), and the futures
            # engine only sees a setup on a tick, so it saw Crude's and nothing
            # else's. Crude is the default instrument, hence 21 of 27 trades.
            if (settings.auto_trade_enabled or settings.futures_paper_enabled) and background:
                mono = time.monotonic()
                overdue = sorted(
                    (st for st in background if mono - self._ticked_at.get(st.instrument, 0.0) >= self._SCAN_MAX_AGE),
                    key=lambda st: self._ticked_at.get(st.instrument, 0.0),
                )
                scan_queue.extend(overdue)
                background = [st for st in background if st not in overdue]
            # Background (screener-only) instruments refresh only every Nth loop
            # and only a few at a time, so they never queue up ahead of the next
            # fast refresh of the instrument you're actually watching.
            if background and cycle % self._BG_EVERY_N == 0:
                take = min(self._BG_PER_CYCLE, len(background))
                for i in range(take):
                    scan_queue.append(background[(rr + i) % len(background)])
                rr = (rr + take) % len(background)

            # 1) Watched instruments: always, unbounded, first — the on-screen
            #    LTP must never wait behind the scan.
            for st in priority:
                await self._tick_one(st, loop)

            # 2) Scanned instruments: budgeted, but NEVER zero.
            #
            #    The budget clock starts HERE, after the watched ticks. It used
            #    to start at the top of the cycle, which made the scan collapse
            #    completely: the watched ticks alone cost more than the 0.24s
            #    window, so by the time the loop reached a scanned instrument the
            #    deadline was already past and it broke immediately — every
            #    cycle, forever. Measured on a 46-name universe: median scan age
            #    789s against a 2s target, i.e. only the 4 watched names were
            #    ever refreshed, which also starved the futures engine (it only
            #    evaluates a setup on a tick).
            #
            #    _SCAN_MIN_PER_CYCLE is the floor that makes that failure mode
            #    impossible: however slow the watched ticks are, the scan still
            #    advances, so full-sweep time is bounded by universe size rather
            #    than by whether any budget happened to be left over.
            scanned = 0
            deadline = time.monotonic() + self._WATCHED_INTERVAL * self._SCAN_BUDGET
            for st in scan_queue:
                if scanned >= self._SCAN_MIN_PER_CYCLE and time.monotonic() > deadline:
                    break
                await self._tick_one(st, loop)
                scanned += 1
            self._scan_rate = self._scan_rate * 0.8 + scanned * 0.2
            # Fast cadence for the watched instrument. Never slower than the
            # configured tick interval; capped fast so the LTP tracks the broker.
            await asyncio.sleep(min(settings.tick_interval_seconds, self._WATCHED_INTERVAL))


hub = Hub()

# ---- Live screener: scan EVERY registered instrument and expose its current
# fresh-entry call (signal/confidence/entry-zone/premium/stop/targets) so the
# UI can show "all BUYs in one screen". Cached briefly so rapid client polls
# don't re-tick the whole registry. Engine untouched — pure read/observability.
_screener_cache: dict[str, object] = {"ts": 0.0, "rows": []}
_SCREENER_TTL = 4.0


def _premium_hi_lo_today(
    candles: list, live: float | None = None
) -> tuple[float | None, float | None]:
    """Today's High / Low of the option premium. Built from the option candle
    series ("today" = IST calendar day of the latest candle) AND the current
    LIVE premium, so the range expands in real time as the price moves instead
    of lagging behind the 1-minute candles."""
    ist = 19800  # +5:30
    hi: float | None = None
    lo: float | None = None
    if candles:
        last_day = int((candles[-1].time + ist) // 86400)
        for c in candles:
            if int((c.time + ist) // 86400) != last_day:
                continue
            hi = c.high if hi is None or c.high > hi else hi
            lo = c.low if lo is None or c.low < lo else lo
    if live is not None and live > 0:
        hi = live if hi is None or live > hi else hi
        lo = live if lo is None or live < lo else lo
    return hi, lo


def _build_screener() -> list[dict]:
    """Read the latest cached snapshot for every instrument that is already
    warm. Never ticks/warms inline (that made the first call take minutes) —
    warming happens in the background loop, so instruments appear here within a
    few seconds of boot without ever blocking the request.

    Only instruments in the ACTIVE universe (manual watchlist / auto-pick /
    QT_INSTRUMENTS) are scanned — a previously-warmed instrument that the user
    has since disabled must not keep showing up here."""
    from app.market.instruments import UNIVERSE, get_spec

    active = set(UNIVERSE)
    rows: list[dict] = []
    for _inst, snap in list(hub.latest.items()):
        if _inst not in active:
            continue
        d = snap.decision
        # Mirror the Decision tab exactly (same signal/confidence + entry plan),
        # so a row here matches what the user sees when they click through.
        sig = d.signal.value
        conf = d.confidence
        prem = d.current_premium
        zone = d.entry_range
        zone_hi = zone[1] if zone else None
        prem_hi, prem_lo = _premium_hi_lo_today(snap.selected_option_candles, prem)
        is_buy = sig == "BUY"
        no_data = bool(is_buy and (prem is None or prem <= 0))
        chasing = bool(is_buy and not no_data and zone_hi is not None and prem is not None and prem > zone_hi * 1.002)
        # Early Momentum advisory (parallel engine) — surfaced so the Signals
        # screen can list which instruments are at EARLY BUY, separate from the
        # frozen confirmation BUYs. Never influences the frozen row above.
        em = d.early_momentum
        em_plan = em.plan if (em and em.plan) else None
        ee = d.early_early
        sc = d.scalp
        fl = d.flow
        rows.append({
            "symbol": snap.instrument,
            "display": snap.instrument_name,
            "signal": sig,
            "confidence": round(float(conf), 1),
            "option_type": d.option_type.value if d.option_type else None,
            "strike": d.strike,
            "current_premium": prem,
            "entry_low": zone[0] if zone else None,
            "entry_high": zone_hi,
            "stop_loss": d.stop_loss,
            "target1": d.target1,
            "target2": d.target2,
            "target3": d.target3,
            "htf_trend": d.htf_trend,
            "entry_trigger": d.entry_trigger,
            "risk_level": d.risk_level,
            "trade_score": round(float(d.trade_score), 0),
            # Real, already-computed ranking fields (used by the AI Command
            # Center to rank opportunities). None stays None → shown as N/A.
            "opportunity_score": round(float(d.opportunity_score), 0),
            "opportunity_label": d.opportunity_label,
            "trade_quality": d.trade_quality,
            "expected_move_points": d.expected_move_points,
            "conviction_meter": d.conviction_meter,
            "observed_target_rate": d.observed_target_rate,
            "moneyness": d.moneyness,
            "expected_holding_minutes": d.expected_holding_minutes,
            "spot_price": d.spot_price,
            "futures_price": snap.futures_price,
            "futures_change_pct": round(float(snap.futures_change_pct), 2),
            "premium_high": prem_hi,
            "premium_low": prem_lo,
            "lot_size": get_spec(snap.instrument).lot_size,
            "data_source": snap.data_source,
            "is_buy": is_buy,
            "no_live_data": no_data,
            "chasing": chasing,
            # --- Early Momentum advisory (parallel engine) ---
            "em_enabled": bool(em and em.enabled),
            "em_stage": em.stage_num if em else 0,
            "em_active": bool(em and em.active),
            "em_side": em.side.value if (em and em.side) else None,
            "em_score": round(float(em.score), 0) if em else 0,
            "em_option": em.option_symbol if em else None,
            "em_entry": em.entry_hint if em else None,
            "em_entry_low": em_plan.entry_low if em_plan else None,
            "em_entry_high": em_plan.entry_high if em_plan else None,
            "em_stop": em_plan.stop_loss if em_plan else None,
            "em_target1": em_plan.target1 if em_plan else None,
            "em_target2": em_plan.target2 if em_plan else None,
            "em_target3": em_plan.target3 if em_plan else None,
            # --- Early-Early (Stage 2.5) add-on — separate, advisory-only ---
            "ee_enabled": bool(ee and ee.enabled),
            "ee_active": bool(ee and ee.active),
            "ee_side": ee.side.value if (ee and ee.side) else None,
            "ee_probability": round(float(ee.probability), 0) if ee else 0,
            "ee_evidence": ee.evidence_count if ee else 0,
            "ee_action": ee.recommended_action if ee else "IGNORE",
            "ee_option": ee.option_symbol if ee else None,
            "ee_entry": ee.entry_hint if ee else None,
            "ee_agreement": ee.agreement if ee else "—",
            # --- Quick Scalp Engine add-on — separate, advisory-only ---
            "scalp_enabled": bool(sc and sc.enabled),
            "scalp_active": bool(sc and sc.active),
            "scalp_side": sc.side.value if (sc and sc.side) else None,
            "scalp_setup": sc.setup if sc else "—",
            "scalp_probability": round(float(sc.probability), 0) if sc else 0,
            "scalp_conditions": sc.condition_count if sc else 0,
            "scalp_action": sc.recommended_action if sc else "IGNORE",
            "scalp_option": sc.option_symbol if sc else None,
            "scalp_entry": sc.entry_hint if sc else None,
            "scalp_target": sc.plan.target1 if (sc and sc.plan) else None,
            "scalp_rr": sc.reward_risk if sc else None,
            "scalp_agreement": sc.agreement if sc else "—",
            "scalp_in_trade": bool(sc and sc.in_trade),
            "scalp_exit_now": bool(sc and sc.exit_now),
            "scalp_exit_urgency": sc.exit_urgency if sc else "NONE",
            "scalp_live_points": sc.live_points if sc else None,
            # --- Flow Engine (Candle-Flow) add-on — separate, advisory-only ---
            "flow_enabled": bool(fl and fl.enabled),
            "flow_state": fl.state if fl else "WAIT",
            "flow_side": fl.side.value if (fl and fl.side) else None,
            "flow_candle": fl.candle_color if fl else "—",
            "flow_pattern": fl.candle_pattern if fl else "NONE",
            "flow_projection": fl.projection if fl else "UNCLEAR",
            "flow_strength": round(float(fl.strength), 0) if fl else 0,
            "flow_option": fl.option_symbol if fl else None,
            "flow_points": fl.points if fl else None,
            "flow_in_trade": bool(fl and fl.in_trade),
            "flow_exit_now": bool(fl and fl.exit_now),
            "flow_switched": bool(fl and fl.switched),
        })
    return rows


@app.on_event("startup")
async def _startup() -> None:
    from app.analysis import watchlist

    # Attach a handler for OUR loggers when the host has not configured logging.
    # Under a bare `uvicorn app.main:app` our records propagate to a root logger
    # with no handler, so they are dropped: a real log from a user contained only
    # uvicorn's access lines, which made warm-up, day-movers and socket drops
    # undiagnosable. uvicorn's own config is left alone if it set one up.
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    loop = asyncio.get_event_loop()
    # Re-apply a user-saved manual watchlist first (survives restarts). If one is
    # active it governs the universe (auto-pick becomes a no-op below).
    watchlist.apply_startup()
    # A day's held set outlives a restart: re-apply it before warming so a
    # mid-session restart resumes the same concentrated list instead of silently
    # re-opening the whole universe.
    from app.analysis import day_movers

    # The SWITCH itself is restored too. It used to live in memory only, so every
    # restart reverted it to OFF and the toggle looked like it refused to stay
    # on. An explicit QT_DAY_MOVERS_AUTO_ENABLED in .env still wins, since an
    # ops-level setting should not be overridden by a remembered click.
    if "day_movers_auto_enabled" not in settings.model_fields_set:
        remembered = day_movers.movers.restore_mode()
        if remembered is not None:
            settings.day_movers_auto_enabled = remembered
    if settings.day_movers_auto_enabled:
        held = day_movers.movers.apply_held()
        if held:
            _log.info("day-movers resumed today's held set: %s", ", ".join(held))
    # Pick today's focused universe from the broker's biggest movers (if enabled)
    # BEFORE warming, so we only warm the instruments we'll actually watch.
    await loop.run_in_executor(None, _refresh_universe)
    # Warm the default instrument (login + scrip-master download + first candles)
    # off the event loop as soon as we boot, so the very first dashboard load
    # doesn't sit on "Connecting…" while that one-time work happens on demand.
    from app.market import instruments as _inst

    loop.run_in_executor(None, registry.get, _inst.DEFAULT_INSTRUMENT)
    # Parse the ledgers once here, off the event loop, so the first board of the
    # session does not pay for reading a journal that is now tens of megabytes.
    # Read-only, and a failure only costs that first request its old speed.
    loop.run_in_executor(None, _warm_ledgers)
    app.state.task = asyncio.create_task(hub.run())
    app.state.warm = asyncio.create_task(_warm_all())
    app.state.intel = asyncio.create_task(_refresh_intelligence())
    app.state.ai = asyncio.create_task(_ai_loop())
    app.state.feed_profile = asyncio.create_task(_feed_profile_loop())
    # A line a minute saying THIS PROCESS is up, written from a thread of its
    # own so it survives a wedged capture loop. Without it, a coverage hole can
    # only say "the tick path did not run", which reads identically for a dead
    # process and a stalled one and sends the operator to the wrong fix.
    # Diagnostic only: writes no price, decides nothing, places no order.
    from app.research.phase17 import service as _p17

    _p17.start_process_heartbeat()

    # Snapshot the research tabs once, after the last close of the day, from a
    # thread of its own. The floors are counted from these snapshots, and while
    # taking one was a manual command most captured sessions never became
    # countable sessions. It only ever files against the day it runs on: the
    # tabs report cumulative totals, so back-dating one would inflate the very
    # count the floors are measured against.
    # Read-only: fetches the research endpoints over loopback and writes JSON.
    from app.research.phase37 import schedule as _p37sched

    _p37sched.start()


def _warm_ledgers() -> None:
    """Read the journal, outcome and lifecycle ledgers once, at boot.

    Nothing here decides anything: it only populates the readers' parse caches so
    the first Signal-board or Reports request of the session is as fast as the
    ones after it.
    """
    from app.analysis import journal_stats, signal_journal, signal_lifecycle

    try:
        signal_journal._read(signal_journal.journal_path())
        signal_journal.resolutions()
        signal_lifecycle.read_ledger()
        journal_stats._missed_by_signal(journal_stats.resolve_session("TODAY"))
    except Exception:
        _log.info("ledger warm-up skipped", exc_info=True)


def _refresh_universe(force: bool = False) -> dict | None:
    """Apply the daily auto-pick to the active universe. No-op when disabled or
    when a static QT_INSTRUMENTS limit is set (that always wins). Safe to call
    repeatedly; the pick itself is cached once per IST day."""
    import os

    from app.market import instruments as _inst

    if os.environ.get("QT_INSTRUMENTS", "").strip():
        return None
    # Auto day-movers holds a deliberately small set for the session; the daily
    # gainers auto-pick must not widen it back out underneath.
    if settings.day_movers_auto_enabled:
        return None
    from app.analysis import watchlist

    # A user-saved manual watchlist takes precedence over the daily auto-pick.
    if watchlist.is_active():
        return None
    if not settings.auto_pick_enabled:
        return None
    from app.analysis import auto_pick

    result = auto_pick.get_daily(settings.auto_pick_count, settings.auto_pick_core, force=force)
    _inst.set_universe(result["universe"])
    return result


# Warm-up progress, so "the instruments are taking a long time to load" can be
# read off the screen instead of guessed at. Angel rate-limits the historical
# candle API, so this is genuinely slow on a big universe and a cold instrument
# is indistinguishable from a dead one until it lands.
_warm_state: dict[str, int] = {"total": 0, "done": 0}


async def _warm_all() -> None:
    """Warm every instrument in the active universe concurrently in the
    background so the live screener is fully populated within seconds of boot,
    instead of trickling in one-per-cycle. Failures are ignored per instrument."""
    from app.market.instruments import UNIVERSE

    loop = asyncio.get_event_loop()
    sem = asyncio.Semaphore(4)  # bound concurrency so we never starve the shared executor

    async def _one(sym: str) -> None:
        async with sem:
            try:
                await loop.run_in_executor(None, registry.get, sym)
            except Exception:
                pass
            finally:
                _warm_state["done"] += 1

    # On the live feed the one-time warm-up is throttled by Angel's historical
    # API rate limit, so instruments appear on the screener gradually. Warm the
    # most-traded ones FIRST so the board is useful within seconds instead of
    # waiting behind less-liquid stocks.
    priority = ["CRUDEOIL", "NIFTY", "SENSEX", "NATURALGAS", "BANKNIFTY"]
    # With the two-tier split configured, the DEEP names warm ahead of everything
    # else. Warm-up is the rate-limited phase, so ordering it by tier is what
    # actually buys back the missing one-minute bars: the names that need
    # complete candles are no longer queued behind 40-odd that do not.
    from app.market import tiers

    deep = [s for s in tiers.deep_names() if s in UNIVERSE] if tiers.enabled() else []
    ordered = [s for s in deep if s in priority] + [s for s in deep if s not in priority]
    ordered += [s for s in priority if s in UNIVERSE and s not in ordered]
    ordered += [s for s in UNIVERSE if s not in ordered]
    _warm_state["total"] = len(ordered)
    _warm_state["done"] = 0
    began = time.monotonic()
    _log.info("warm-up starting for %d instrument(s) — candles are REST and rate-limited", len(ordered))
    await asyncio.gather(*[_one(sym) for sym in ordered], return_exceptions=True)
    _warm_state["done"] = _warm_state["total"]
    _log.info("warm-up finished: %d instrument(s) in %.0fs", len(ordered), time.monotonic() - began)


async def _refresh_intelligence() -> None:
    """Background refresh of the Phase 3.1 Intelligence Layer's network context
    (India VIX + market breadth from NSE) so per-tick reads stay non-blocking.
    Best-effort and advisory only — never touches the frozen engine or orders."""
    from app.analysis import intelligence as intel

    loop = asyncio.get_event_loop()
    while True:
        try:
            await loop.run_in_executor(None, intel.market_context)
        except Exception:
            pass
        await asyncio.sleep(60.0)


async def _ai_loop() -> None:
    """Phase 6 AI engine cycle (LIVE PAPER ONLY).

    Runs beside the production engine and never inside it: it reads cached prices
    and the production engine's already-computed verdict, and writes only to the
    AI journal and the AI paper book. It cannot place a real order — see
    ``app/ai/safety.py``. Disabled unless QT_AI_ENABLED is set.
    """
    from app.ai import service as ai_service

    loop = asyncio.get_event_loop()
    while True:
        try:
            if settings.ai_enabled:
                await loop.run_in_executor(None, ai_service.cycle)
        except Exception:
            pass
        await asyncio.sleep(max(2.0, settings.ai_interval_sec))


async def _feed_profile_loop() -> None:
    """Periodic feed-profile snapshot (observability only).

    Writes what the feed cost and delivered, tagged with the deep/broad
    configuration that produced it, so the effect of changing that configuration
    is measured on real sessions instead of estimated. It reads the same numbers
    ``/api/scan-health`` reports and touches no decision, gate or order.
    """
    from app.analysis import feed_profile

    loop = asyncio.get_event_loop()
    while True:
        try:
            if settings.feed_profile_log:
                scan = await scan_health()
                await loop.run_in_executor(None, feed_profile.record, scan)
        except Exception:
            pass
        await asyncio.sleep(max(30.0, float(settings.feed_profile_interval_sec) / 2))


@app.on_event("shutdown")
async def _shutdown() -> None:
    from app.research.phase17 import service as _p17

    _p17.stop_process_heartbeat()
    from app.research.phase37 import schedule as _p37sched

    _p37sched.stop()
    app.state.task.cancel()
    app.state.intel.cancel()
    app.state.ai.cancel()
    app.state.feed_profile.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await app.state.task


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "provider": settings.data_provider,
        "trade_mode": "live" if registry.get(DEFAULT_INSTRUMENT).is_live else "paper",
    }


@app.get("/api/snapshot")
async def snapshot(instrument: str | None = None) -> dict:
    # Prefer the snapshot the background loop already produced for THIS
    # instrument. Only build one on demand when none exists yet, and do it OFF
    # the event loop — state.tick makes blocking broker calls, so running it
    # inline would freeze every other request (a cause of the dashboard slowness).
    inst = _norm_instrument(instrument)
    loop = asyncio.get_event_loop()
    # AppState construction logs into Angel + downloads the scrip master + pulls
    # the first candles — all blocking. Run it (and the first tick) OFF the event
    # loop so a not-yet-warm instrument can't freeze every other request, which
    # was making the dashboard slow to load after a restart / instrument switch.
    st = await loop.run_in_executor(None, registry.get, inst)
    snap = hub.latest.get(st.instrument)
    if snap is None:
        snap = await loop.run_in_executor(None, st.tick)
        hub.latest[st.instrument] = snap
    # Publication and visibility are different claims (Part 26): this is the
    # point at which a signal actually reaches a client. Recorded once per
    # signal, and it cannot affect the response.
    signal_visibility.mark_user_visible(st.instrument, snap.decision)
    return snap.model_dump(mode="json")


@app.post("/api/buy")
async def buy(req: BuyRequest) -> dict:
    st = registry.get(_norm_instrument(req.instrument))
    result = st.buy(req.option_symbol, req.lots, req.confirm)
    return {"result": result.model_dump(mode="json"), "position": st.position.model_dump(mode="json")}


@app.post("/api/sell")
async def sell(req: SellRequest | None = None) -> dict:
    st = registry.get(_norm_instrument(req.instrument if req else None))
    result = st.sell((req.confirm if req else False))
    return {"result": result.model_dump(mode="json"), "position": st.position.model_dump(mode="json")}


@app.post("/api/auto-trade")
async def auto_trade(req: AutoTradeRequest) -> dict:
    """Toggle/configure the PAPER auto-trader at runtime. Simulated fills only —
    it is hard-guarded off whenever trade_mode is live and never places a real
    order. Mutates the process-wide settings so it applies to every instrument."""
    from app.config import settings

    if req.enabled is not None:
        settings.auto_trade_enabled = bool(req.enabled)
    if req.min_confidence is not None:
        settings.auto_trade_min_confidence = max(75.0, min(99.0, float(req.min_confidence)))
    if req.max_lots is not None:
        settings.auto_trade_max_lots = max(1, min(50, int(req.max_lots)))
    if req.safe_sizing is not None:
        settings.auto_trade_safe_sizing = bool(req.safe_sizing)
    if req.ride_trail is not None:
        settings.auto_trade_ride_trail = bool(req.ride_trail)
    if req.trail_pct is not None:
        settings.auto_trade_trail_pct = max(2.0, min(50.0, float(req.trail_pct)))
    if req.capital is not None:
        settings.capital = max(1000.0, float(req.capital))
    if req.risk_per_trade_pct is not None:
        settings.risk_per_trade_pct = max(0.1, min(20.0, float(req.risk_per_trade_pct)))
    if req.daily_profit_target is not None:
        settings.daily_profit_target = max(0.0, float(req.daily_profit_target))
    if req.max_daily_loss is not None:
        settings.max_daily_loss = max(0.0, float(req.max_daily_loss))
    if req.allow_live is not None:
        settings.auto_trade_allow_live = bool(req.allow_live)
    if req.live_max_order_value is not None:
        settings.auto_trade_live_max_order_value = max(0.0, float(req.live_max_order_value))
    if req.live_max_lots is not None:
        settings.auto_trade_live_max_lots = max(1, min(50, int(req.live_max_lots)))
    if req.zero_to_hero_enabled is not None:
        settings.zero_to_hero_enabled = bool(req.zero_to_hero_enabled)
    if req.zero_to_hero_budget is not None:
        settings.zero_to_hero_budget = max(0.0, float(req.zero_to_hero_budget))
    if req.max_concurrent is not None:
        settings.auto_trade_max_concurrent = max(1, min(20, int(req.max_concurrent)))
    if req.momentum_entry is not None:
        settings.auto_trade_momentum_entry = bool(req.momentum_entry)
    if req.no_chase is not None:
        settings.auto_trade_no_chase = bool(req.no_chase)
    if req.pre_t1_trail is not None:
        settings.auto_trade_pre_t1_trail = bool(req.pre_t1_trail)
    if req.instrument_capital is not None:
        settings.auto_trade_instrument_capital = {
            str(k).upper(): max(0.0, float(v))
            for k, v in req.instrument_capital.items()
            if v is not None and float(v) > 0
        }
    if req.instrument_lots is not None:
        settings.auto_trade_instrument_lots = {
            str(k).upper(): max(1, min(100, int(v)))
            for k, v in req.instrument_lots.items()
            if v is not None and int(v) >= 1
        }
    live_armed = (
        settings.trade_mode.lower() == "live"
        and settings.auto_trade_enabled
        and settings.auto_trade_allow_live
    )
    return {
        "enabled": settings.auto_trade_enabled,
        "min_confidence": settings.auto_trade_min_confidence,
        "max_lots": settings.auto_trade_max_lots,
        "safe_sizing": settings.auto_trade_safe_sizing,
        "ride_trail": settings.auto_trade_ride_trail,
        "trail_pct": settings.auto_trade_trail_pct,
        "capital": settings.capital,
        "risk_per_trade_pct": settings.risk_per_trade_pct,
        "daily_profit_target": settings.daily_profit_target,
        "max_daily_loss": settings.max_daily_loss,
        "allow_live": settings.auto_trade_allow_live,
        "live_max_order_value": settings.auto_trade_live_max_order_value,
        "live_max_lots": settings.auto_trade_live_max_lots,
        "zero_to_hero_enabled": settings.zero_to_hero_enabled,
        "zero_to_hero_budget": settings.zero_to_hero_budget,
        "max_concurrent": settings.auto_trade_max_concurrent,
        "momentum_entry": settings.auto_trade_momentum_entry,
        "no_chase": settings.auto_trade_no_chase,
        "pre_t1_trail": settings.auto_trade_pre_t1_trail,
        "instrument_capital": settings.auto_trade_instrument_capital,
        "instrument_lots": settings.auto_trade_instrument_lots,
        "trade_mode": settings.trade_mode,
        "mode": "live" if live_armed else "paper",
    }


@app.post("/api/auto-buy")
async def auto_buy(req: AutoBuyRequest) -> dict:
    """ONE-CLICK LIVE auto-buy switch. Flipping ``on`` true wires the whole
    hands-off flow in a single action: enable the bot, arm it for live orders,
    size every entry from your REAL available balance scaled by the signal's
    confidence, and enter as soon as the Signal board says BUY (no momentum
    wait). Flipping it off disables the bot and disarms live.

    Safety backstops are kept and auto-derived from your balance: a per-order
    notional ceiling equal to your available cash (one order can never exceed
    what you hold) plus the existing live lot cap. Real orders still require
    ``QT_TRADE_MODE=live`` and a working Angel session; if that's missing this
    returns ``live_active: false`` so the UI can warn instead of silently paper
    trading."""
    from app.config import settings
    from app.market import angelone

    balance: float | None = None
    try:
        balance = angelone.available_cash(ttl=5.0)
    except Exception:
        balance = None

    if req.on:
        settings.auto_trade_enabled = True
        settings.auto_trade_allow_live = True
        settings.auto_trade_use_live_balance = True
        settings.auto_trade_safe_sizing = True
        settings.auto_trade_momentum_entry = False
        # --- V4 management (validated on 5y of 1-min data): keep the WIDE ATR stop
        # and RIDE winners on a ratcheting lock instead of booking T1. The 5-year
        # dry run showed tight stops / break-even / quick-scalp all cut the win rate
        # so hard they turned the edge NEGATIVE (PF < 0.6); the wide-stop + ride
        # variant was the only clear net winner (NIFTY PF 1.12 vs 1.05). Loss size
        # is controlled by LOT COUNT + the order-value cap, NOT by a tight stop.
        settings.auto_trade_ride_trail = True
        # LOCK-ANY-PROFIT exit (user-requested): do NOT wait for T1. As soon as the
        # premium is a little above entry and then stalls / ticks down from its peak,
        # exit and bank whatever profit is showing (e.g. entry 12 → stuck at 15 → exit;
        # or reaches 17 and rolls over → exit). Arm early (small profit) and use a
        # tight give-back so it exits near the stall instead of holding for the target.
        settings.auto_trade_pre_t1_trail = True
        settings.auto_trade_pre_t1_trail_arm_profit_pct = 4.0
        settings.auto_trade_pre_t1_trail_giveback_pct = 20.0
        settings.auto_trade_pre_t1_trail_min_giveback = 1.0
        settings.auto_trade_pre_t1_trail_min_giveback_pct = 0.5
        # Once green, never let it fall back below entry — protect the profit.
        settings.auto_trade_breakeven_after_arm = True
        settings.auto_trade_quick_scalp_lots = 0
        # Use the engine's regime ATR stop as-is (wide). 0 = no extra tightening.
        settings.auto_trade_max_stop_pct = 0.0
        # Blast-radius backstop: a single live order can never exceed the cash you
        # actually hold. Keep the prior cap if the balance can't be read.
        if balance and balance > 0:
            settings.auto_trade_live_max_order_value = round(balance, 2)
        # Let the confidence-scaled balance budget drive lot count (bounded by the
        # order-value cap above), rather than a tiny fixed lot ceiling.
        settings.auto_trade_live_max_lots = max(settings.auto_trade_live_max_lots, 50)
    else:
        settings.auto_trade_enabled = False
        settings.auto_trade_allow_live = False

    live_active = settings.trade_mode.lower() == "live" and balance is not None
    return {
        "on": settings.auto_trade_enabled and settings.auto_trade_allow_live,
        "live_active": live_active,
        "trade_mode": settings.trade_mode,
        "available_cash": balance,
        "use_live_balance": settings.auto_trade_use_live_balance,
        "momentum_entry": settings.auto_trade_momentum_entry,
        "live_max_order_value": settings.auto_trade_live_max_order_value,
        "min_confidence": settings.auto_trade_min_confidence,
        "note": (
            "Live auto-buy armed — sizing from your balance by signal confidence."
            if (req.on and live_active)
            else (
                "Enabled, but NOT live: set QT_TRADE_MODE=live and ensure your Angel "
                "session is connected, or it will paper-trade only."
                if req.on
                else "Auto-buy off."
            )
        ),
    }


@app.get("/api/settings")
async def get_settings() -> dict:
    """Read the live, user-toggleable engine settings (paper-safe, no secrets)."""
    from app.config import settings

    return {
        "entry_mode": settings.entry_mode,
        "buy_min_confidence": settings.buy_min_confidence,
        "min_reward_risk": settings.min_reward_risk,
        "target_scale": settings.decision_target_scale,
        "min_stop_pct_of_premium": settings.min_stop_pct_of_premium,
        "pre_t1_arm_profit_pct": settings.auto_trade_pre_t1_trail_arm_profit_pct,
        "pre_t1_giveback_min_pct": settings.auto_trade_pre_t1_trail_min_giveback_pct,
        "blocked_entry_hours": settings.blocked_entry_hours,
        "instrument_allowlist": settings.instrument_allowlist,
        "veto_premium_explosion": settings.veto_premium_explosion,
        "reversal_entry_enabled": settings.reversal_entry_enabled,
        "ignition_entry_enabled": settings.ignition_entry_enabled,
        "ignition_body_factor": settings.ignition_body_factor,
        "ignition_volume_factor": settings.ignition_volume_factor,
        "reversal_rsi_oversold": settings.reversal_rsi_oversold,
        "reversal_max_atr_from_swing": settings.reversal_max_atr_from_swing,
        "supertrend_entry_enabled": settings.supertrend_entry_enabled,
        "supertrend_period": settings.supertrend_period,
        "supertrend_multiplier": settings.supertrend_multiplier,
        "beyond_t3_ratchet": settings.auto_trade_beyond_t3_ratchet,
        "beyond_t3_step_pct": settings.auto_trade_beyond_t3_step_pct,
        "beyond_t3_min_step": settings.auto_trade_beyond_t3_min_step,
        "auto_trade": {
            "enabled": settings.auto_trade_enabled,
            "min_confidence": settings.auto_trade_min_confidence,
            "max_lots": settings.auto_trade_max_lots,
            "safe_sizing": settings.auto_trade_safe_sizing,
            "ride_trail": settings.auto_trade_ride_trail,
            "trail_pct": settings.auto_trade_trail_pct,
            "capital": settings.capital,
            "risk_per_trade_pct": settings.risk_per_trade_pct,
            "daily_profit_target": settings.daily_profit_target,
            "max_daily_loss": settings.max_daily_loss,
            "allow_live": settings.auto_trade_allow_live,
            "live_max_order_value": settings.auto_trade_live_max_order_value,
            "live_max_lots": settings.auto_trade_live_max_lots,
            "zero_to_hero_enabled": settings.zero_to_hero_enabled,
            "zero_to_hero_budget": settings.zero_to_hero_budget,
            "max_concurrent": settings.auto_trade_max_concurrent,
            "momentum_entry": settings.auto_trade_momentum_entry,
            "no_chase": settings.auto_trade_no_chase,
            "pre_t1_trail": settings.auto_trade_pre_t1_trail,
            "use_live_balance": settings.auto_trade_use_live_balance,
            "instrument_capital": settings.auto_trade_instrument_capital,
            "instrument_lots": settings.auto_trade_instrument_lots,
            "stop_points": settings.auto_trade_stop_points,
            "target_points": settings.auto_trade_target_points,
            "target_pct": settings.auto_trade_target_pct,
            "min_target_r": settings.auto_trade_min_target_r,
            "min_premium": settings.auto_trade_min_premium,
            "reentry_cooldown_sec": settings.auto_trade_reentry_cooldown_sec,
            "post_stop_cooldown_sec": settings.auto_trade_post_stop_cooldown_sec,
            "no_entry_open_minutes": settings.auto_trade_no_entry_open_minutes,
            "max_confidence": settings.auto_trade_max_confidence,
            "risk_stand_down_sec": settings.risk_stand_down_sec,
            # Cost model, editable so the P&L can be driven by YOUR contract
            # note rather than by my estimate of a discount broker's rates.
            "brokerage_per_lot": settings.brokerage_per_lot,
            "option_stt_sell_pct": settings.option_stt_sell_pct,
            "option_txn_pct": settings.option_txn_pct,
            "stale_feed_ticks": settings.auto_trade_stale_feed_ticks,
            "risk_balanced_lots": settings.auto_trade_risk_balanced_lots,
            "balanced_max_lots": settings.auto_trade_balanced_max_lots,
            "scale_out": settings.auto_trade_scale_out,
            "scale_out_frac": settings.auto_trade_scale_out_frac,
            "max_stop_pct": settings.auto_trade_max_stop_pct,
            "beyond_t3_ratchet": settings.auto_trade_beyond_t3_ratchet,
            "beyond_t3_step_pct": settings.auto_trade_beyond_t3_step_pct,
            "beyond_t3_min_step": settings.auto_trade_beyond_t3_min_step,
            "trade_mode": settings.trade_mode,
            "mode": (
                "live"
                if (settings.trade_mode.lower() == "live" and settings.auto_trade_enabled and settings.auto_trade_allow_live)
                else "paper"
            ),
        },
        # Premium-SELLING engine — PAPER ONLY; no live order path exists for it.
        "credit_spread": {
            "enabled": settings.credit_spread_enabled,
            "short_delta": settings.credit_spread_short_delta,
            "width_steps": settings.credit_spread_width_steps,
            "stop_multiple": settings.credit_spread_stop_multiple,
            "paper_only": True,
        },
        "flow": {
            "enabled": settings.flow_enabled,
            "paper_lots": settings.flow_paper_lots,
            "giveback_points": settings.flow_giveback_points,
            "min_body_frac": settings.flow_min_body_frac,
            "confirm_candles": settings.flow_confirm_candles,
            "sticky_candles": settings.flow_sticky_candles,
        },
        # FUTURES paper tool — a SEPARATE tool with its OWN capital pot and
        # point-based risk. PAPER ONLY: no live order path exists for it.
        "futures": _futures_settings_payload(),
    }


def _futures_settings_payload() -> dict:
    """Current settings of the separate futures paper tool."""
    return {
        "enabled": settings.futures_paper_enabled,
        "capital": settings.futures_capital,
        "risk_per_trade_pct": settings.futures_risk_per_trade_pct,
        "margin_pct": settings.futures_margin_pct,
        "paper_lots": settings.futures_paper_lots,
        "size_by_margin": settings.futures_size_by_margin,
        "max_concurrent": settings.futures_max_concurrent,
        "min_atr_pct": settings.futures_min_atr_pct,
        "min_atr_points": settings.futures_min_atr_points,
        "min_adx": settings.futures_min_adx,
        "stop_atr": settings.futures_stop_atr,
        "t1_r": settings.futures_t1_r,
        "t2_r": settings.futures_t2_r,
        "t3_r": settings.futures_t3_r,
        "t1_lock_r": settings.futures_t1_lock_r,
        "post_t1_trail_r": settings.futures_post_t1_trail_r,
        "max_cost_to_risk_pct": settings.futures_max_cost_to_risk_pct,
        "reentry_cooldown_sec": settings.futures_reentry_cooldown_sec,
        "flip_block_sec": settings.futures_flip_block_sec,
        "max_consecutive_losses": settings.futures_max_consecutive_losses,
        "stand_down_sec": settings.futures_stand_down_sec,
        "max_daily_loss": settings.futures_max_daily_loss,
        "max_trades_per_day": settings.futures_max_trades_per_day,
        "beyond_t3_step_atr": settings.futures_beyond_t3_step_atr,
        "slippage_points": settings.futures_slippage_points,
        "brokerage_per_order": settings.futures_brokerage_per_order,
        "stt_sell_pct": settings.futures_stt_sell_pct,
        "ctt_sell_pct": settings.futures_ctt_sell_pct,
        "txn_pct": settings.futures_txn_pct,
        "no_entry_minutes": settings.futures_no_entry_minutes,
        "paper_only": True,
    }


@app.post("/api/settings")
async def set_settings(req: dict) -> dict:
    """Update live engine settings at runtime. Currently supports ``entry_mode``
    ('pullback' | 'momentum' | 'pattern' | 'auto'). ``auto`` runs all three
    triggers together (union) and tags which one fired. Paper-safe: never
    touches orders."""
    from app.config import settings

    mode = req.get("entry_mode")
    if mode in ("pullback", "momentum", "pattern", "auto"):
        settings.entry_mode = mode

    # Profit-protecting entry gates (dashboard/runtime toggles).
    tsc = req.get("target_scale")
    if isinstance(tsc, (int, float)) and not isinstance(tsc, bool) and 0.2 <= tsc <= 3.0:
        settings.decision_target_scale = float(tsc)
    mrr = req.get("min_reward_risk")
    if isinstance(mrr, (int, float)) and not isinstance(mrr, bool) and 0 <= mrr <= 10:
        settings.min_reward_risk = float(mrr)
    msp = req.get("min_stop_pct_of_premium")
    if isinstance(msp, (int, float)) and not isinstance(msp, bool) and 0 <= msp <= 50:
        settings.min_stop_pct_of_premium = float(msp)
    arm = req.get("pre_t1_arm_profit_pct")
    if isinstance(arm, (int, float)) and not isinstance(arm, bool) and 0 <= arm <= 50:
        settings.auto_trade_pre_t1_trail_arm_profit_pct = float(arm)
    gbp = req.get("pre_t1_giveback_min_pct")
    if isinstance(gbp, (int, float)) and not isinstance(gbp, bool) and 0 <= gbp <= 20:
        settings.auto_trade_pre_t1_trail_min_giveback_pct = float(gbp)
    beh = req.get("blocked_entry_hours")
    if isinstance(beh, dict):
        cleaned: dict[str, list[int]] = {}
        for name, hours in beh.items():
            if isinstance(name, str) and isinstance(hours, list):
                valid = [
                    int(h) for h in hours
                    if isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= 23
                ]
                if valid:
                    cleaned[name.upper()] = sorted(set(valid))
        settings.blocked_entry_hours = cleaned
    allow = req.get("instrument_allowlist")
    if isinstance(allow, list):
        settings.instrument_allowlist = [
            s.upper() for s in allow if isinstance(s, str) and s.strip()
        ]
    so = req.get("scale_out")
    if isinstance(so, bool):
        settings.auto_trade_scale_out = so
    sof = req.get("scale_out_frac")
    if isinstance(sof, (int, float)) and not isinstance(sof, bool) and 0 < sof < 1:
        settings.auto_trade_scale_out_frac = float(sof)
    # Beyond-T3 ratchet — keep stepping the locked floor up past Target 3 so a
    # big run is not capped at T3, exiting only once the move rolls back a step.
    btr = req.get("beyond_t3_ratchet")
    if isinstance(btr, bool):
        settings.auto_trade_beyond_t3_ratchet = btr
    bts = req.get("beyond_t3_step_pct")
    if isinstance(bts, (int, float)) and not isinstance(bts, bool) and 0.5 <= bts <= 50.0:
        settings.auto_trade_beyond_t3_step_pct = float(bts)
    btm = req.get("beyond_t3_min_step")
    if isinstance(btm, (int, float)) and not isinstance(btm, bool) and 0 <= btm <= 100.0:
        settings.auto_trade_beyond_t3_min_step = float(btm)
    # SUPERTREND flip entry — the ATR trailing-band flip (the TradingView
    # BUY/SELL marker). Walk-forward replay measured PF ~0.89-0.97, i.e. LOSING,
    # and it entered LATER off the swing low than the pullback entry, so it is
    # OFF by default and labelled unproven; the toggle exists for paper testing.
    ste = req.get("supertrend_entry_enabled")
    if isinstance(ste, bool):
        settings.supertrend_entry_enabled = ste
    stp = req.get("supertrend_period")
    if isinstance(stp, int) and not isinstance(stp, bool) and 3 <= stp <= 50:
        settings.supertrend_period = stp
    stm = req.get("supertrend_multiplier")
    if isinstance(stm, (int, float)) and not isinstance(stm, bool) and 0.5 <= stm <= 10.0:
        settings.supertrend_multiplier = float(stm)
    # IGNITION entry — enter on the first expansion candle instead of waiting
    # for accumulated momentum. Replay measured PF 0.75-0.81 on CRUDEOIL and an
    # unstable 0.92-1.07 on NIFTY, so it is OFF by default and the dashboard
    # labels it as unproven; the toggle exists so it can be paper-tested live.
    ign = req.get("ignition_entry_enabled")
    if isinstance(ign, bool):
        settings.ignition_entry_enabled = ign
    ibf = req.get("ignition_body_factor")
    if isinstance(ibf, (int, float)) and not isinstance(ibf, bool) and 1.0 <= ibf <= 5.0:
        settings.ignition_body_factor = float(ibf)
    ivf = req.get("ignition_volume_factor")
    if isinstance(ivf, (int, float)) and not isinstance(ivf, bool) and 1.0 <= ivf <= 5.0:
        settings.ignition_volume_factor = float(ivf)
    # REVERSAL (bottom/top) entry — buys the turn at a support/resistance
    # extreme. Replay measured NIFTY PF ~0.90 (losing), so it is OFF by default
    # and labelled unproven; the toggle exists so it can be paper-tested live.
    rro = req.get("reversal_rsi_oversold")
    if isinstance(rro, (int, float)) and not isinstance(rro, bool) and 10 <= rro <= 50:
        settings.reversal_rsi_oversold = float(rro)
    rma = req.get("reversal_max_atr_from_swing")
    if isinstance(rma, (int, float)) and not isinstance(rma, bool) and 0.1 <= rma <= 5.0:
        settings.reversal_max_atr_from_swing = float(rma)

    vpe = req.get("veto_premium_explosion")
    if isinstance(vpe, bool):
        settings.veto_premium_explosion = vpe
    ree = req.get("reversal_entry_enabled")
    if isinstance(ree, bool):
        settings.reversal_entry_enabled = ree

    # --- Auto-Buy per-instrument FIXED point stop (dashboard-editable). A value
    # of 0 / null CLEARS that instrument (falls back to the % / engine stop). ---
    at = req.get("auto_trade")
    if isinstance(at, dict):
        from app.market.instruments import REGISTRY

        def _merge_points(existing: dict[str, float], incoming: dict) -> dict[str, float]:
            out = dict(existing)
            for k, v in incoming.items():
                sym = str(k).upper()
                if sym not in REGISTRY:
                    continue
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                    out[sym] = min(10000.0, float(v))
                else:
                    out.pop(sym, None)
            return out

        sp = at.get("stop_points")
        if isinstance(sp, dict):
            settings.auto_trade_stop_points = _merge_points(
                settings.auto_trade_stop_points, sp
            )
        tp = at.get("target_points")
        if isinstance(tp, dict):
            settings.auto_trade_target_points = _merge_points(
                settings.auto_trade_target_points, tp
            )
        tpc = at.get("target_pct")
        if isinstance(tpc, dict):
            settings.auto_trade_target_pct = _merge_points(
                settings.auto_trade_target_pct, tpc
            )
        mtr = at.get("min_target_r")
        if isinstance(mtr, (int, float)) and not isinstance(mtr, bool):
            settings.auto_trade_min_target_r = max(0.0, min(5.0, float(mtr)))
        mp = at.get("min_premium")
        if isinstance(mp, (int, float)) and not isinstance(mp, bool):
            settings.auto_trade_min_premium = max(0.0, min(1000.0, float(mp)))
        rc = at.get("reentry_cooldown_sec")
        if isinstance(rc, (int, float)) and not isinstance(rc, bool):
            settings.auto_trade_reentry_cooldown_sec = max(0.0, min(7200.0, float(rc)))
        psc = at.get("post_stop_cooldown_sec")
        if isinstance(psc, (int, float)) and not isinstance(psc, bool):
            settings.auto_trade_post_stop_cooldown_sec = max(0.0, min(14400.0, float(psc)))
        neo = at.get("no_entry_open_minutes")
        if isinstance(neo, (int, float)) and not isinstance(neo, bool):
            settings.auto_trade_no_entry_open_minutes = max(0.0, min(180.0, float(neo)))
        mxc = at.get("max_confidence")
        if isinstance(mxc, (int, float)) and not isinstance(mxc, bool):
            settings.auto_trade_max_confidence = max(50.0, min(100.0, float(mxc)))
        rsd = at.get("risk_stand_down_sec")
        if isinstance(rsd, (int, float)) and not isinstance(rsd, bool):
            settings.risk_stand_down_sec = max(0.0, min(28800.0, float(rsd)))
        bpl = at.get("brokerage_per_lot")
        if isinstance(bpl, (int, float)) and not isinstance(bpl, bool):
            settings.brokerage_per_lot = max(0.0, min(5000.0, float(bpl)))
        ost = at.get("option_stt_sell_pct")
        if isinstance(ost, (int, float)) and not isinstance(ost, bool):
            settings.option_stt_sell_pct = max(0.0, min(5.0, float(ost)))
        otx = at.get("option_txn_pct")
        if isinstance(otx, (int, float)) and not isinstance(otx, bool):
            settings.option_txn_pct = max(0.0, min(5.0, float(otx)))
        sft = at.get("stale_feed_ticks")
        if isinstance(sft, (int, float)) and not isinstance(sft, bool):
            settings.auto_trade_stale_feed_ticks = max(2, min(500, int(sft)))
        rbl = at.get("risk_balanced_lots")
        if isinstance(rbl, bool):
            settings.auto_trade_risk_balanced_lots = rbl
        bml = at.get("balanced_max_lots")
        if isinstance(bml, (int, float)) and not isinstance(bml, bool):
            settings.auto_trade_balanced_max_lots = max(1, min(100, int(bml)))

    # --- Flow Engine paper controls (PAPER ONLY — never sizes/places a real
    # order; only affects the advisory Candle-Flow read + its paper ₹ history). ---
    flow = req.get("flow")
    if isinstance(flow, dict):
        if isinstance(flow.get("enabled"), bool):
            settings.flow_enabled = flow["enabled"]
        pl = flow.get("paper_lots")
        if isinstance(pl, (int, float)) and not isinstance(pl, bool):
            settings.flow_paper_lots = max(1, min(100, int(pl)))
        gb = flow.get("giveback_points")
        if isinstance(gb, (int, float)) and not isinstance(gb, bool):
            settings.flow_giveback_points = max(1.0, min(50.0, float(gb)))
        mbf = flow.get("min_body_frac")
        if isinstance(mbf, (int, float)) and not isinstance(mbf, bool):
            settings.flow_min_body_frac = max(0.05, min(0.95, float(mbf)))
        cc = flow.get("confirm_candles")
        if isinstance(cc, (int, float)) and not isinstance(cc, bool):
            settings.flow_confirm_candles = max(1, min(5, int(cc)))
        sc = flow.get("sticky_candles")
        if isinstance(sc, (int, float)) and not isinstance(sc, bool):
            settings.flow_sticky_candles = max(1, min(10, int(sc)))

    # --- FUTURES paper tool (SEPARATE tool, PAPER ONLY). Every value is range
    # checked and the tool has no live order path, so nothing here can place an
    # order. ``paper_only`` is not settable — it is hard-locked True. ---
    fut = req.get("futures")
    if isinstance(fut, dict):
        if isinstance(fut.get("enabled"), bool):
            settings.futures_paper_enabled = fut["enabled"]

        def _num(key: str, lo: float, hi: float) -> float | None:
            v = fut.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi:
                return float(v)
            return None

        cap = _num("capital", 10000.0, 100000000.0)
        if cap is not None:
            settings.futures_capital = cap
        rpt = _num("risk_per_trade_pct", 0.1, 10.0)
        if rpt is not None:
            settings.futures_risk_per_trade_pct = rpt
        mgn = _num("margin_pct", 1.0, 100.0)
        if mgn is not None:
            settings.futures_margin_pct = mgn
        pl = _num("paper_lots", 0, 100)
        if pl is not None:
            settings.futures_paper_lots = int(pl)
        if isinstance(fut.get("size_by_margin"), bool):
            settings.futures_size_by_margin = fut["size_by_margin"]
        mc = _num("max_concurrent", 1, 10)
        if mc is not None:
            settings.futures_max_concurrent = int(mc)
        mapct = _num("min_atr_pct", 0.0, 5.0)
        if mapct is not None:
            settings.futures_min_atr_pct = mapct
        map_ = _num("min_atr_points", 0.0, 500.0)
        if map_ is not None:
            settings.futures_min_atr_points = map_
        madx = _num("min_adx", 0.0, 60.0)
        if madx is not None:
            settings.futures_min_adx = madx
        sa = _num("stop_atr", 0.3, 6.0)
        if sa is not None:
            settings.futures_stop_atr = sa
        for key, attr, lo, hi in (
            ("t1_r", "futures_t1_r", 0.3, 5.0),
            ("t2_r", "futures_t2_r", 0.5, 10.0),
            ("t3_r", "futures_t3_r", 1.0, 20.0),
        ):
            v = _num(key, lo, hi)
            if v is not None:
                setattr(settings, attr, v)
        ptt = _num("post_t1_trail_r", 0.0, 5.0)
        if ptt is not None:
            settings.futures_post_t1_trail_r = ptt
        bts = _num("beyond_t3_step_atr", 0.05, 5.0)
        if bts is not None:
            settings.futures_beyond_t3_step_atr = bts
        slp = _num("slippage_points", 0.0, 100.0)
        if slp is not None:
            settings.futures_slippage_points = slp
        brk = _num("brokerage_per_order", 0.0, 1000.0)
        if brk is not None:
            settings.futures_brokerage_per_order = brk
        stt = _num("stt_sell_pct", 0.0, 1.0)
        if stt is not None:
            settings.futures_stt_sell_pct = stt
        ctt = _num("ctt_sell_pct", 0.0, 1.0)
        if ctt is not None:
            settings.futures_ctt_sell_pct = ctt
        txn = _num("txn_pct", 0.0, 1.0)
        if txn is not None:
            settings.futures_txn_pct = txn
        nem = _num("no_entry_minutes", 0.0, 300.0)
        if nem is not None:
            settings.futures_no_entry_minutes = nem
        # Risk governor. Every one of these can be relaxed, but none can be set
        # to a value that disables the protection silently: the ranges are the
        # guard rails, and the board shows which cap stopped an entry.
        for key, attr, lo, hi in (
            ("t1_lock_r", "futures_t1_lock_r", 0.0, 1.0),
            ("max_cost_to_risk_pct", "futures_max_cost_to_risk_pct", 1.0, 50.0),
            ("reentry_cooldown_sec", "futures_reentry_cooldown_sec", 0.0, 7200.0),
            ("flip_block_sec", "futures_flip_block_sec", 0.0, 7200.0),
            ("stand_down_sec", "futures_stand_down_sec", 0.0, 28800.0),
            ("max_daily_loss", "futures_max_daily_loss", 0.0, 10_000_000.0),
        ):
            v = _num(key, lo, hi)
            if v is not None:
                setattr(settings, attr, v)
        mcl = _num("max_consecutive_losses", 1, 20)
        if mcl is not None:
            settings.futures_max_consecutive_losses = int(mcl)
        mtd = _num("max_trades_per_day", 1, 100)
        if mtd is not None:
            settings.futures_max_trades_per_day = int(mtd)

    # Echo back the applied values so the dashboard reflects what the engine
    # actually accepted (a rejected out-of-range value returns unchanged).
    return {
        "entry_mode": settings.entry_mode,
        "futures": _futures_settings_payload(),
        "ignition_entry_enabled": settings.ignition_entry_enabled,
        "ignition_body_factor": settings.ignition_body_factor,
        "ignition_volume_factor": settings.ignition_volume_factor,
        "reversal_entry_enabled": settings.reversal_entry_enabled,
        "reversal_rsi_oversold": settings.reversal_rsi_oversold,
        "reversal_max_atr_from_swing": settings.reversal_max_atr_from_swing,
        "supertrend_entry_enabled": settings.supertrend_entry_enabled,
        "supertrend_period": settings.supertrend_period,
        "supertrend_multiplier": settings.supertrend_multiplier,
        "beyond_t3_ratchet": settings.auto_trade_beyond_t3_ratchet,
        "beyond_t3_step_pct": settings.auto_trade_beyond_t3_step_pct,
        "beyond_t3_min_step": settings.auto_trade_beyond_t3_min_step,
        "flow": {
            "enabled": settings.flow_enabled,
            "paper_lots": settings.flow_paper_lots,
            "giveback_points": settings.flow_giveback_points,
            "min_body_frac": settings.flow_min_body_frac,
            "confirm_candles": settings.flow_confirm_candles,
            "sticky_candles": settings.flow_sticky_candles,
        },
    }



# In-memory ring buffer of alerts received from TradingView (paper-safe: these
# are only DISPLAYED — they never place, modify or cancel an order).
_tv_alerts: list[dict] = []


@app.post("/api/alerts/tradingview")
async def tradingview_alert(payload: dict) -> dict:
    """Receive a TradingView alert webhook and store it for display only.

    PAPER-SAFE: this endpoint NEVER places an order. It just records the alert
    (symbol / side / note / price) so the dashboard can show what your
    TradingView study fired. Point a TradingView alert's webhook URL here."""
    import time as _t

    entry = {
        "ts": int(_t.time()),
        "symbol": str(payload.get("symbol") or payload.get("ticker") or "")[:32],
        "side": str(payload.get("side") or payload.get("action") or "")[:8],
        "price": payload.get("price"),
        "note": str(payload.get("note") or payload.get("message") or "")[:280],
    }
    _tv_alerts.append(entry)
    del _tv_alerts[:-100]  # keep only the most recent 100
    return {"stored": True, "count": len(_tv_alerts)}


@app.get("/api/alerts/tradingview")
async def tradingview_alerts() -> dict:
    """Return the most recent TradingView alerts (newest first), display-only."""
    return {"alerts": list(reversed(_tv_alerts))}


@app.get("/api/events")
async def economic_events() -> dict:
    """Upcoming high-impact scheduled events (calendar) + the current guard.
    Read-only informational data; never places an order."""
    from app.analysis import events as events_mod

    items = events_mod.upcoming()
    return {
        "events": [
            {"ts": e.ts, "name": e.name, "category": e.category,
             "impact": e.impact, "affects": e.affects}
            for e in items
        ],
        "guard": events_mod.guard(),
    }


@app.get("/api/fundamentals")
async def fundamentals(instrument: str | None = None) -> dict:
    """Best-effort NSE fundamentals for a stock (delivery %, results date) plus
    market-wide FII/DII. Read-only public data; degrades to unavailable."""
    from app.analysis import fundamentals as fund
    from app.config import settings
    from app.market.instruments import get_spec

    if not settings.fundamentals_enabled:
        return {"stock": {"available": False, "note": "Fundamentals disabled."},
                "flows": {"available": False}}
    spec = get_spec(_norm_instrument(instrument))
    stock = fund.stock(spec.symbol) if spec.exchange in ("NFO", "NSE") else {
        "available": False, "symbol": spec.symbol,
        "note": "Fundamentals apply to NSE stocks only.",
    }
    return {"stock": stock, "flows": fund.fii_dii()}


@app.get("/api/journal")
async def journal(instrument: str | None = None) -> dict:
    st = registry.get(_norm_instrument(instrument))
    return {"trades": st.journal, "today_profit": st.today_profit, "today_loss": st.today_loss}


@app.get("/api/signal-stats")
async def signal_stats(instrument: str | None = None, days: int = 7) -> dict:
    """Daily scoreboard of the frozen engine's BUY signals and their outcomes
    (Target / Stop / Open). Pure observability — recorded automatically as the
    engine runs; never places or influences a trade."""
    from app.analysis import signal_tracker

    return signal_tracker.stats(_norm_instrument(instrument), days=days)


@app.get("/api/signal-log")
async def signal_log(instrument: str | None = None):
    """Download the tracked signals as JSON, straight from the live store.

    The filename carries the newest signal's IST date and the download time, so a
    file that turns out to be days old is obvious before it is analysed rather
    than after — a hand-copied export cannot show whether the tracker is still
    recording.
    """
    from datetime import datetime, timedelta, timezone

    from fastapi.responses import JSONResponse

    from app.analysis import signal_tracker

    ist = timezone(timedelta(hours=5, minutes=30))
    rows = signal_tracker.export_rows(_norm_instrument(instrument) if instrument else None)
    newest = signal_tracker.latest_ts()
    last = datetime.fromtimestamp(newest, ist).strftime("%Y%m%d-%H%M") if newest else "empty"
    stamp = datetime.now(ist).strftime("%Y%m%d-%H%M")
    name = f"signal_log_last-{last}_taken-{stamp}.json"
    return JSONResponse(
        rows,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/api/signal-board-history")
async def signal_board_history(
    limit: int = 300,
    session: str | None = None,
    instrument: str | None = None,
    action: str | None = None,
    position_signal: str | None = None,
    vehicle: str | None = None,
    market: str | None = None,
    signal_vehicle: str | None = None,
    status: str | None = None,
    option_type: str | None = None,
    setup_type: str | None = None,
    volatility_class: str | None = None,
    outcome: str | None = None,
    exchange: str | None = None,
    speed: str | None = None,
    t1_hit: bool | None = None,
    t2_hit: bool | None = None,
    t3_hit: bool | None = None,
    stop_hit: bool | None = None,
    followed: bool | None = None,
    expiry_day: bool | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
    max_minutes_to_resolution: float | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """The Signal Board Journal: what the board said, and what it went on to do.

    Read-only over the append-only journal files. Every row is the record made at
    signal time with its outcome attached beside it, never merged into it, so the
    original call cannot be edited by hindsight.
    """
    from app.analysis import journal_stats, signal_journal

    # A live board asks for TODAY and the server decides which IST date that is:
    # the browser's clock is not the session's.
    session = journal_stats.resolve_session(session)
    if session and not journal_stats.valid_date(session):
        raise HTTPException(status_code=400, detail="session must be YYYY-MM-DD")
    filters = {
        "session": session,
        "instrument": _norm_instrument(instrument) if instrument else None,
        "action": action, "position_signal": position_signal,
        "vehicle": vehicle, "option_type": option_type,
        # MARKET (OPTIONS/FUTURES), VEHICLE (CE/PE/FUTURES) and STATUS
        # (ACTIVE/RESOLVED/INVALIDATED/MISSED_DISPLAY/MISSED_EXECUTION).
        "market": market, "signal_vehicle": signal_vehicle, "status": status,
        "setup_type": setup_type, "volatility_class": volatility_class,
        "outcome": outcome, "exchange": exchange, "speed": speed,
        "t1_hit": t1_hit, "t2_hit": t2_hit, "t3_hit": t3_hit,
        "stop_hit": stop_hit, "followed": followed, "expiry_day": expiry_day,
        "min_score": min_score, "max_score": max_score,
        "max_minutes_to_resolution": max_minutes_to_resolution,
        "date_from": date_from, "date_to": date_to,
    }
    for value in (date_from, date_to):
        if not journal_stats.valid_date(value):
            raise HTTPException(status_code=400,
                                detail="dates must be YYYY-MM-DD")
    rows = journal_stats.history(limit=max(1, min(limit, 2000)), filters=filters)
    return {
        "rows": rows,
        "count": len(rows),
        # BUY calls the range holds that carried no contract, stop or target, so
        # the count of what a followed-only view leaves out is never hidden.
        "buy_calls_without_plan": journal_stats.buy_calls_without_plan(
            date_from, date_to,
            _norm_instrument(instrument) if instrument else None,
            session=session),
        "sessions": journal_stats.sessions_recorded(),
        "statistics": journal_stats.statistics(session),
        # Why the list is empty, when it is empty. A journal that throws on every
        # tick and a session with nothing to say both return zero rows, and
        # without this the tab cannot tell the reader which one happened.
        "recording": signal_journal.journal_health(),
    }


@app.get("/api/signal-board-report.csv")
async def signal_board_report_csv(
    date_from: str | None = None,
    date_to: str | None = None,
    instrument: str | None = None,
    limit: int = 5000,
) -> PlainTextResponse:
    """The BUY calls of a date range as CSV. Read-only over the journal files.

    Oldest first, one row per call, with the outcome recorded beside it. An
    unresolved call keeps its outcome cells empty instead of being written as a
    flat result.
    """
    from app.analysis import journal_stats

    for value in (date_from, date_to):
        if not journal_stats.valid_date(value):
            raise HTTPException(status_code=400, detail="dates must be YYYY-MM-DD")
    body = journal_stats.buy_report_csv(
        date_from=date_from or None, date_to=date_to or None,
        instrument=_norm_instrument(instrument) if instrument else None,
        limit=max(1, min(limit, 20000)),
    )
    span = f"{date_from or 'start'}_to_{date_to or 'latest'}"
    name = f"buy_signals_{span}.csv"
    return PlainTextResponse(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/api/signal-board-daily")
async def signal_board_daily(session: str | None = None) -> dict:
    """Daily statistics for the journal, from recorded outcomes only."""
    from app.analysis import journal_stats

    if session:
        return journal_stats.daily_report(session)
    return journal_stats.daily_summary()


@app.get("/api/shadow-book")
async def shadow_book_ledger(limit: int = 500, session: str | None = None) -> dict:
    """The ungated shadow ledger, newest first. Read-only, paper-only.

    Every Signal-board BUY call is here, including the ones the gated book
    refused, with the blocker recorded beside each row. No order exists behind
    any of it.
    """
    from app.analysis import journal_stats, shadow_book

    if session and not journal_stats.valid_date(session):
        raise HTTPException(status_code=400, detail="session must be YYYY-MM-DD")
    rows = shadow_book.read_ledger(
        limit=max(1, min(limit, 20000)), session=session)
    return {
        "rows": rows,
        "count": len(rows),
        "open_positions": shadow_book.open_count(),
        "enabled": settings.shadow_book_enabled,
        "capital": settings.shadow_book_capital,
        "ledger": shadow_book.ledger_path(),
    }


@app.get("/api/shadow-vs-gated")
async def shadow_vs_gated(session: str | None = None) -> dict:
    """Gated paper book vs the ungated shadow book, and what the refused calls did.

    The gated trades are read here and passed in, so the shadow module itself
    never touches the trading side's storage.
    """
    from datetime import datetime, timedelta, timezone

    from app import storage
    from app.analysis import journal_stats, shadow_book

    if session and not journal_stats.valid_date(session):
        raise HTTPException(status_code=400, detail="session must be YYYY-MM-DD")
    since: int | None = None
    until: int | None = None
    if session:
        ist = timezone(timedelta(hours=5, minutes=30))
        day = datetime.strptime(session, "%Y-%m-%d").replace(tzinfo=ist)
        since = int(day.timestamp())
        until = since + 86400
    gated = storage.store.paper_trades(since=since, until=until)
    return shadow_book.compare(gated, session=session)


@app.get("/api/signal-reconciliation")
async def signal_reconciliation(session: str | None = None) -> dict:
    """Do the engine, the dashboard, the journal and the book agree? (Part 27).

    Returns the counts for the window plus both directions of disagreement — a
    journalled BUY with no dashboard publication, and a dashboard BUY with no
    journal row — each carrying the recorded reason. A refusal with a reason is
    reported as a refusal, not as a lost signal.
    """
    from app.analysis import journal_stats, signal_reconciliation as recon

    if session and not journal_stats.valid_date(session):
        raise HTTPException(status_code=400, detail="session must be YYYY-MM-DD")
    return recon.reconcile(session=session)


@app.get("/api/missed-signals")
async def missed_signals(session: str | None = None,
                         reason: str | None = None) -> dict:
    """Every BUY that stopped short of the user or the book, and where (Part 32).

    ``classification`` separates a plan the system correctly refused from a
    signal that stopped before the user could see it. Nothing here is called a
    missed opportunity on the strength of a later price move.
    """
    from app.analysis import journal_stats, signal_reconciliation as recon

    # TODAY is resolved against the journal's own IST clock; anything else must
    # still be an explicit trading date.
    session = journal_stats.resolve_session(session)
    if session and not journal_stats.valid_date(session):
        raise HTTPException(status_code=400, detail="session must be YYYY-MM-DD")
    if reason is not None and reason not in recon.lc.MISS_REASONS:
        raise HTTPException(status_code=400, detail="unknown reason")
    rows = recon.missed_signals(session=session, reason=reason)
    return {
        "rows": rows,
        "count": len(rows),
        "reasons": list(recon.lc.MISS_REASONS),
        "note": ("a later favourable move does not make a correctly refused "
                 "plan a missed opportunity"),
    }


@app.get("/api/signal-churn")
async def signal_churn(limit: int = 200) -> dict:
    """Raw events versus publications per episode (Phase 12 §1).

    One live setup restated on every tick produced thousands of raw events on 25
    Aug. A repeat is now published only when something material changed, and the
    counters here are the before/after: ``raw_events`` is every tick, published
    or not, so throttling restatement cannot hide a call.
    """
    from app.analysis import signal_churn as churn

    return churn.report(limit=max(1, min(limit, 2000)))


@app.post("/api/signal-reconciliation/daily")
async def signal_reconciliation_daily(session: str | None = None) -> dict:
    """Write the day's reconciliation to JSON + Markdown under the data dir."""
    from app.analysis import journal_stats, signal_reconciliation as recon

    if session and not journal_stats.valid_date(session):
        raise HTTPException(status_code=400, detail="session must be YYYY-MM-DD")
    return recon.write_daily(session=session)


@app.get("/api/futures-signal")
async def futures_signal_card(instrument: str | None = None) -> dict:
    """The RESEARCH futures plan for one instrument (Parts 22-23).

    A separate vehicle with its own validation, not the option card with the
    strike removed. ``status`` is ``VALID_FUTURES_PLAN`` only when every check in
    ``checks`` passed; a refusal states which check failed, on what number,
    against what threshold. Nothing here is executable: no order route exists for
    this vehicle, and it never replaces an option BUY.
    """
    loop = asyncio.get_event_loop()
    inst = _norm_instrument(instrument)
    st = await loop.run_in_executor(None, registry.get, inst)
    snap = hub.latest.get(st.instrument)
    if snap is None:
        snap = await loop.run_in_executor(None, st.tick)
        hub.latest[st.instrument] = snap
    card = snap.futures_signal
    signal_visibility.mark_futures_visible(st.instrument, card)
    return {
        "instrument": st.instrument,
        "card": card.model_dump(mode="json") if card is not None else None,
        "research_only": True,
        "executable": False,
        "note": ("research only: futures plans are published for comparison and "
                 "have no order path — paper or live — from this endpoint"),
    }


@app.get("/api/phase12-report")
async def phase12_report_endpoint(session: str | None = None,
                                  write: bool = False) -> dict:
    """The Phase 12 §16 report: eight artefacts and the ten questions.

    ``write=true`` persists all of them under the data directory. Questions the
    recorded book cannot yet support — notably "is futures better than options"
    and "is there enough evidence to begin production A+ validation" — are
    answered INSUFFICIENT_SAMPLE with the exact shortfall.
    """
    from app.analysis import phase12_report as p12

    loop = asyncio.get_event_loop()
    if write:
        written = await loop.run_in_executor(None, p12.write_all, session)
        body = await loop.run_in_executor(None, p12.build, session)
        body["written"] = written
        return body
    return await loop.run_in_executor(None, p12.build, session)


@app.get("/api/phase12a-report")
async def phase12a_report_endpoint(session: str | None = None,
                                   write: bool = False) -> dict:
    """The Phase 12A §17–§18 report: seven artefacts and the thirteen questions.

    ``write=true`` persists all of them under the data directory. Questions the
    recorded book cannot yet support answer INSUFFICIENT_SAMPLE or
    REQUIRES_MORE_DATA with the exact shortfall — notably "did the feed fix
    improve freshness", which is measurable only on a live broker session.
    """
    from app.analysis import phase12a_report as p12a

    loop = asyncio.get_event_loop()
    if write:
        written = await loop.run_in_executor(None, p12a.write_all, session)
        body = await loop.run_in_executor(None, p12a.build, session)
        body["written"] = written
        return body
    return await loop.run_in_executor(None, p12a.build, session)


@app.get("/api/phase13-report")
async def phase13_report_endpoint(session: str | None = None,
                                  write: bool = False) -> dict:
    """The Phase 13A/13B report: entry location and hold-time intelligence.

    ``write=true`` persists all eight artefacts under the data directory.
    Research only: entry states and hold windows in here are measured from the
    recorded outcome ledger and are display evidence — no gate, stop, target,
    exit or time stop consults them. Every candidate is IN_SAMPLE_ONLY until the
    validation floor of 20 sessions with 5 chronological holdout is met.
    """
    from app.research.phase13 import report as p13

    loop = asyncio.get_event_loop()
    if write:
        written = await loop.run_in_executor(None, p13.write_all, session)
        body = await loop.run_in_executor(None, p13.build, session)
        body["written"] = written
        return body
    return await loop.run_in_executor(None, p13.build, session)


@app.get("/api/hold-window")
async def hold_window_endpoint(instrument: str, side: str | None = None) -> dict:
    """The measured expected-hold window for one instrument and option side.

    Interquartile time-to-T1 of resolved calls in the cohort, with the p90 long
    tail and the median time losers took to reach the stop. Not a probability
    that the target arrives, and not a production time stop.
    """
    from app.analysis import hold_window as hw

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, hw.window, instrument, side)


@app.get("/api/label-outcomes")
async def label_outcomes_report(session: str | None = None,
                                write: bool = False) -> dict:
    """Do the research labels predict anything (§8/§9/§11/§13).

    Tradability, A+, entry-quality, family and CE/PE cohorts joined signal_id to
    resolved outcome, with each call's own recorded spread charged to its net R.
    Research only: no cohort in here gates, ranks or suppresses a signal.
    """
    from app.analysis import label_outcomes as lo

    loop = asyncio.get_event_loop()
    if write:
        await loop.run_in_executor(None, lo.write_report, session)
    return await loop.run_in_executor(None, lo.report, session)


@app.get("/api/daily-best")
async def daily_best_history(limit_days: int = 120) -> dict:
    """One signal a day, and how often it reached its target.

    A record of what a one-trade-a-day schedule would have produced over the
    recorded sessions, under three entry-time selection policies (top score, top
    conviction, first call of the day) with the uncapped book beside them as the
    comparator. The day is always chosen on the card as it was written, never on
    how it resolved.

    Read-only and research only: nothing in here caps, suppresses, ranks or sizes
    a live signal, R is gross of brokerage/taxes/spread, and every summary under
    20 sessions is stamped REQUIRES_MORE_DATA.
    """
    from app.analysis import daily_best as db

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, db.report, max(1, min(limit_days, 400)))


@app.get("/api/a-plus-shadow")
async def a_plus_shadow_report(write: bool = False) -> dict:
    """A+ shadow label distribution over recorded evidence (§13).

    A deterministic composite of data quality, tradability, family, entry
    quality, vehicle quality, room and spread — no fitted probability. Shadow
    only: no production gate, size or route consults these labels, and §15
    still requires 20 sessions with 5 chronological holdout before any of it is
    validated.
    """
    from app.analysis import a_plus_shadow as ap

    loop = asyncio.get_event_loop()
    if write:
        await loop.run_in_executor(None, ap.write_report)
    return await loop.run_in_executor(None, ap.report)


@app.get("/api/vehicle-outcome-comparison")
async def vehicle_outcome_comparison(session: str | None = None,
                                     write: bool = False) -> dict:
    """Option vs futures vs no-trade on the same market idea, by outcome (§5).

    Distinct from ``/api/vehicle-comparison``, which compares the two *plans* as
    written. This compares what each of them actually did after resolution.

    One record per idea with both expressions of it, each in its own unit, and a
    verdict of OPTION_BETTER / FUTURES_BETTER / BOTH_VALID / BOTH_BAD / UNKNOWN.
    Research only: nothing here selects a vehicle, production still trades the
    option leg, and the futures side has no order path.
    """
    from app.analysis import vehicle_comparison as vc

    loop = asyncio.get_event_loop()
    if write:
        await loop.run_in_executor(None, vc.write_report, session)
    return await loop.run_in_executor(None, vc.report, session)


@app.get("/api/futures-outcomes")
async def futures_outcomes_report(session: str | None = None,
                                  write: bool = False) -> dict:
    """What the futures research plans actually did, in index points (§12).

    Entry, stop, T1/T2/T3, MFE, MAE, target-before-stop, time-to-target,
    time-to-stop, spread, gross and net R for every ``VALID_FUTURES_PLAN``
    followed in shadow. Observation only: no order was placed, paper or live, and
    these numbers are never added to or compared against option premium results
    until ``comparison_ready`` is true.
    """
    from app.analysis import futures_outcomes as fo

    loop = asyncio.get_event_loop()
    if write:
        await loop.run_in_executor(None, fo.write_report, session)
    return await loop.run_in_executor(None, fo.report, session)


@app.get("/api/instrument-studies")
async def instrument_studies_report(session: str | None = None,
                                    write: bool = False) -> dict:
    """Per-instrument economics: stocks (§8), indices (§9), MCX (§10).

    Median / p75 / p90 spread, spread over risk, gross and net R, win rate and
    sample size per name, read from the recorded journal and outcome ledgers,
    grouped by family and never pooled across them. Net R charges the recorded
    spread plus the board's own round-trip cost model. Research only: no verdict
    here blocks or promotes an instrument.
    """
    from app.analysis import instrument_studies

    loop = asyncio.get_event_loop()
    if write:
        await loop.run_in_executor(None, instrument_studies.write_report, session)
    return await loop.run_in_executor(None, instrument_studies.report, session)


@app.get("/api/instrument-tradability")
async def instrument_tradability_report(write: bool = False) -> dict:
    """Was each signal economically takeable, by instrument and family (§3/§4).

    Research only: spread, spread over the plan's own risk, liquidity, quote
    freshness, contract affordability and expected room, per instrument and per
    family, with TRADABLE / CAUTION / UNTRADABLE counts. No production gate
    consults any of it. ``write=true`` also persists ``instrument_tradability.json``.
    """
    from app.analysis import tradability as tr

    loop = asyncio.get_event_loop()
    if write:
        await loop.run_in_executor(None, tr.write_report)
    return tr.report()


@app.get("/api/direction-attribution")
async def direction_attribution_report(write: bool = False) -> dict:
    """Is the market read directional? Five named definitions, no single figure (§6).

    The retired definition counted a call correct if the underlying ever moved
    the called way by any amount, which is definition A here. A/B/C/D/E are
    reported side by side with their answerable sample sizes; a definition below
    the minimum sample reports INSUFFICIENT_SAMPLE rather than a rate.
    """
    from app.analysis import direction_attribution as da
    from app.analysis import signal_journal as sj

    loop = asyncio.get_event_loop()
    outcomes = await loop.run_in_executor(None, lambda: sj.read_outcomes(limit=200_000))
    body = da.summarise_outcomes(outcomes)
    if write:
        await loop.run_in_executor(None, lambda: da.write_report(body))
    return body


@app.get("/api/futures-rollover")
async def futures_rollover_report(write: bool = False) -> dict:
    """Which futures contract each research plan is written on, and why (§11).

    Research only. The quoted contract — the one the price feed and the
    WebSocket subscription are built on — is never changed by this; inside the
    configured days-to-expiry the research plan is written on the next contract
    instead, and a rolled row states that its levels still come from the quoted
    contract's candles. ``write=true`` also persists ``futures_rollover.json``.
    """
    from app.analysis import futures_rollover as roll

    loop = asyncio.get_event_loop()
    if write:
        await loop.run_in_executor(None, roll.write_report)
    return roll.report()


@app.get("/api/futures-feed-audit")
async def futures_feed_audit_report(write: bool = False) -> dict:
    """Why the futures bars are the age they are, hop by hop (Phase 12A §1-§2).

    Diagnostics only: subscription state, socket, REST fallback, cache and
    processing timestamps per contract, with the measured cause of an old bar
    (deferred historical budget, rate limit, unsubscribed token, silent socket).
    The futures research engine's own 90-second freshness refusal is unchanged
    and does not read these bands. ``write=true`` also persists
    ``futures_feed_audit.json``.
    """
    from app.analysis import futures_feed_audit as audit

    loop = asyncio.get_event_loop()
    if write:
        await loop.run_in_executor(None, audit.write_report)
    return audit.report()


@app.get("/api/vehicle-comparison")
async def vehicle_comparison(instrument: str | None = None) -> dict:
    """Option plan vs futures plan on the same market event (Part 34).

    A research label only — OPTION_BETTER / FUTURES_BETTER / BOTH_VALID /
    BOTH_BAD / UNKNOWN — with the numbers behind it. No winner is selected and
    neither vehicle replaces the other: a preference needs resolved outcomes
    across sessions, not one snapshot.
    """
    from app.analysis import futures_signal as fut
    from app.analysis import journal_stats

    loop = asyncio.get_event_loop()
    inst = _norm_instrument(instrument)
    st = await loop.run_in_executor(None, registry.get, inst)
    snap = hub.latest.get(st.instrument)
    if snap is None:
        snap = await loop.run_in_executor(None, st.tick)
        hub.latest[st.instrument] = snap
    if snap.futures_signal is None:
        raise HTTPException(status_code=503,
                            detail="futures research not available yet")
    liq = await loop.run_in_executor(
        None,
        lambda: fut.option_liquidity(st.provider.option_chain(),
                                     snap.decision.recommended_option),
    )
    resolved = await loop.run_in_executor(
        None, lambda: journal_stats.vehicle_resolved_evidence(st.instrument))
    return {
        "instrument": st.instrument,
        **fut.compare(snap.decision, snap.futures_signal,
                      opt_spread_pct=liq["spread_pct"],
                      opt_volume=liq["volume"],
                      opt_oi=liq["oi"],
                      resolved=resolved),
    }


@app.get("/api/instruments")
async def instruments() -> dict:
    from app.market.instruments import universe_specs

    # Return the ACTIVE universe (post auto-pick / QT_INSTRUMENTS), so the
    # dropdown + screener show exactly the instruments the app is watching today.
    return {
        "instruments": [
            {"symbol": s.symbol, "display": s.display} for s in universe_specs()
        ],
    }


@app.get("/api/watchlist")
async def get_watchlist() -> dict:
    """All registered instruments with an ``enabled`` flag for the active
    universe, so the user can toggle each on/off from the dashboard instead of
    editing QT_INSTRUMENTS. ``env_locked`` = an ops-level QT_INSTRUMENTS pin is
    set (dashboard toggles are then read-only)."""
    from app.analysis import watchlist
    from app.market.instruments import REGISTRY, UNIVERSE

    active = set(UNIVERSE)
    return {
        "instruments": [
            {
                # registry KEY (what toggling uses), not spec.symbol which can
                # differ (e.g. BAJAJAUTO -> scrip name "BAJAJ-AUTO").
                "symbol": sym,
                "display": spec.display,
                "exchange": spec.exchange,
                "enabled": sym in active,
            }
            for sym, spec in REGISTRY.items()
        ],
        "manual": watchlist.is_active(),
        "env_locked": watchlist.env_locked(),
    }


@app.post("/api/watchlist")
async def set_watchlist(req: dict) -> dict:
    """Enable exactly the given instruments (paper-safe; never touches orders).
    An empty list clears the manual watchlist and reverts to auto-pick / full."""
    from app.analysis import watchlist

    enabled = req.get("enabled")
    if not isinstance(enabled, list):
        enabled = []
    # save() applies the new universe and returns it (read the return value, not a
    # pre-imported UNIVERSE binding, which would be stale after the reassignment).
    applied = watchlist.save([str(s) for s in enabled])
    # An explicit manual save wins over the auto picker: otherwise the next
    # day-movers cycle would silently overwrite the list the user just chose,
    # which reads as the dashboard ignoring them.
    auto_was_on = settings.day_movers_auto_enabled
    settings.day_movers_auto_enabled = False
    # force the screener to rebuild against the new universe on the next poll
    _screener_cache["ts"] = 0.0
    # warm any newly-enabled instruments in the background so they start showing
    # up in the screener without waiting for the next daily auto-pick cycle.
    app.state.warm = asyncio.create_task(_warm_all())
    return {
        "enabled": applied,
        "manual": watchlist.is_active(),
        "env_locked": watchlist.env_locked(),
        "auto_day_movers_disabled": auto_was_on,
    }


# Share of the active universe that must have a measured move before the
# shortlist may prune. Warm-up is rate-limited by Angel's historical API, so a
# cold list looks identical to a quiet one.
_MOVERS_MIN_COVERAGE = 0.6


def _candidate_pool() -> list[str]:
    """Instruments the mover ranking is allowed to choose from.

    The user's saved watchlist is the reference list (that is its job now that
    the engine can pick for itself); if none is saved, the full master. The
    currently active names are always included, so a held instrument is still
    scored after the universe has been narrowed to the day's set — otherwise the
    ranking could never see anything outside its own pick.
    """
    from app.analysis import watchlist
    from app.market.instruments import REGISTRY, UNIVERSE

    saved = watchlist.load()
    pool = list(saved) if saved else list(REGISTRY.keys())
    for sym in UNIVERSE:
        if sym not in pool:
            pool.append(sym)
    return pool


def _mover_rows(pool: list[str] | None = None) -> list[dict]:
    """Rank instruments by how far the premium can actually travel.

    Reuses the opportunity score (percent of premium, net of round-trip costs)
    rather than percent change on the underlying: 1% on a Rs7,900 commodity and
    1% on a Rs16 stock option are not the same trade. An instrument with no
    snapshot yet is returned as unmeasured, never as quiet — a name that has not
    been warmed must not be pruned for looking still.
    """
    from app.analysis import opportunity
    from app.market.instruments import REGISTRY, UNIVERSE

    ages = hub.scan_ages()
    rows: list[dict] = []
    for sym in pool if pool is not None else list(UNIVERSE):
        spec = REGISTRY.get(sym)
        if spec is None:
            continue
        snap = hub.latest.get(sym)
        row: dict = {
            "symbol": sym,
            "display": spec.display,
            "exchange": spec.exchange,
            "measured": False,
            "net_move_pct": None,
            "score": None,
            "premium": None,
            "atr_pct": None,
            "change_pct": None,
            "seconds_since_tick": ages.get(sym),
            # Whether THIS instrument's exchange is trading now. A shut market is
            # not a quiet instrument, and must not be ranked, pruned or counted
            # against scan coverage as though it were.
            "session_open": _exchange_open(sym),
            "pinned": sym in _inst_mod.PINNED,
            "active": sym in UNIVERSE,
        }
        if snap is not None:
            try:
                opp = opportunity.evaluate(snap)
            except Exception:
                opp = None
            if opp is not None and opp.get("net_move_pct") is not None:
                row["measured"] = True
                row["net_move_pct"] = opp["net_move_pct"]
                row["score"] = opp["score"]
                row["premium"] = opp["premium"]
                row["atr_pct"] = opp["atr_pct"]
            row["change_pct"] = snap.futures_change_pct
        rows.append(row)
    rows.sort(
        key=lambda r: (r["measured"], r["net_move_pct"] or 0.0, r["score"] or 0.0),
        reverse=True,
    )
    return rows


@app.get("/api/watchlist/movers")
async def watchlist_movers(count: int = 15) -> dict:
    """Preview the shortlist of instruments that actually move enough to trade.

    Read-only: it proposes, it does not apply. Pinned names (indices +
    commodities) are always kept so the core board never empties, and every row
    carries the number it was judged on so a pruned name can be argued with.
    """
    keep_n = max(1, min(count, _inst_mod.MAX_ACTIVE))
    rows = _mover_rows()
    measured = [r for r in rows if r["measured"]]
    unmeasured = [r for r in rows if not r["measured"]]
    keep: list[str] = [r["symbol"] for r in rows if r["pinned"]]
    for r in measured:
        if len(keep) >= keep_n:
            break
        if r["symbol"] not in keep:
            keep.append(r["symbol"])
    for r in rows:
        r["keep"] = r["symbol"] in keep
    coverage = len(measured) / len(rows) if rows else 0.0
    # Pruning cannot be justified from data that does not exist yet. Right after
    # a restart only the default instrument is warm, so "keep the movers" would
    # keep the pinned names and drop everything else for being quiet when it was
    # only cold — the shortlist therefore waits for real coverage.
    ready = coverage >= _MOVERS_MIN_COVERAGE and len(measured) >= keep_n
    return {
        "rows": rows,
        "keep": keep,
        "requested": keep_n,
        "measured_count": len(measured),
        "unmeasured_count": len(unmeasured),
        "active_count": len(rows),
        "coverage_pct": round(100.0 * coverage, 1),
        "min_coverage_pct": round(100.0 * _MOVERS_MIN_COVERAGE, 1),
        "min_move_pct": settings.opportunity_min_move_pct,
        "env_locked": bool(os.environ.get("QT_INSTRUMENTS", "").strip()),
        "ready": ready,
    }


@app.post("/api/watchlist/movers")
async def apply_watchlist_movers(req: dict) -> dict:
    """Apply the movers shortlist as the manual watchlist (paper-safe).

    Refuses while nothing has been measured yet, rather than silently keeping
    only the pinned names — that would look like the shortlist deleted the
    watchlist.
    """
    raw = req.get("count", 15)
    count = int(raw) if isinstance(raw, (int, float, str)) and str(raw).strip().isdigit() else 15
    preview = await watchlist_movers(count=count)
    if preview["env_locked"]:
        return {
            **preview,
            "applied": False,
            "reason": "QT_INSTRUMENTS pins the universe at the ops level — the dashboard cannot override it",
        }
    if not preview["ready"]:
        return {
            **preview,
            "applied": False,
            "reason": (
                f"only {preview['measured_count']} of {preview['active_count']} instruments "
                f"({preview['coverage_pct']}%) have a measured move — under the "
                f"{preview['min_coverage_pct']}% needed to prune. The rest are still warming, "
                "and a cold instrument looks exactly like a quiet one. Let it run a few minutes."
            ),
        }
    result = await set_watchlist({"enabled": preview["keep"]})
    return {**preview, "applied": True, **result}


class DayMoversRequest(BaseModel):
    enabled: bool | None = None
    replace_dead: bool | None = None
    pick_after_ist_min: int | None = None
    # Re-pick now, discarding today's held set (used when the user disagrees
    # with the morning's choice).
    repick: bool = False


def _day_movers_cycle() -> dict:
    """Rank the candidate pool and let the day-movers engine pick or maintain.

    Runs off the event loop; reads cached snapshots only, so it never competes
    with the tick loop for the broker feed.
    """
    from app.analysis import day_movers

    rows = _mover_rows(_candidate_pool())
    result = day_movers.movers.evaluate(rows)
    if result["action"] in ("picked", "swapped"):
        _screener_cache["ts"] = 0.0
    return result


@app.get("/api/day-movers")
async def day_movers_status() -> dict:
    """Today's auto-held instruments, why they were chosen, and the change log.

    Also returns the current candidate ranking so the choice can be checked
    against the numbers rather than taken on trust.
    """
    from app.analysis import day_movers, watchlist

    snap = day_movers.movers.snapshot()
    rows = _mover_rows(_candidate_pool())
    snap["candidates"] = rows[:25]
    snap["candidate_count"] = len(rows)
    snap["measured_count"] = sum(1 for r in rows if r["measured"])
    # The saved watchlist is untouched by auto mode; say so explicitly, because
    # "the engine changed my instruments" would otherwise look like data loss.
    snap["manual_watchlist"] = watchlist.load()
    snap["manual_watchlist_active"] = watchlist.is_active()
    snap["env_locked"] = watchlist.env_locked()
    return snap


@app.post("/api/day-movers")
async def set_day_movers(req: DayMoversRequest) -> dict:
    """Turn auto day-movers on/off, or force a re-pick.

    There is no count to set: the engine holds however many names clear the
    movement floor, bounded to 5-10.

    Paper-safe: this only decides which instruments are watched. Turning it OFF
    restores the user's saved manual watchlist, so the switch is reversible.
    """
    from app.analysis import day_movers, watchlist

    if req.replace_dead is not None:
        settings.day_movers_replace_dead = bool(req.replace_dead)
    if req.pick_after_ist_min is not None:
        settings.day_movers_pick_after_ist_min = max(0, int(req.pick_after_ist_min))
    if req.repick:
        day_movers.movers.reset()
    turning_off = req.enabled is False and settings.day_movers_auto_enabled
    if req.enabled is not None:
        settings.day_movers_auto_enabled = bool(req.enabled)
    # Remember the choice on disk, so it survives the next restart.
    day_movers.movers.remember_mode(settings.day_movers_auto_enabled)

    loop = asyncio.get_event_loop()
    if turning_off:
        # Hand the universe back to whatever the user had chosen by hand.
        await loop.run_in_executor(None, watchlist.apply_startup)
        if not watchlist.is_active():
            _inst_mod.set_universe([])
        _screener_cache["ts"] = 0.0
        app.state.warm = asyncio.create_task(_warm_all())
    elif settings.day_movers_auto_enabled:
        await loop.run_in_executor(None, _day_movers_cycle)
    return await day_movers_status()


@app.get("/api/scan-health")
async def scan_health() -> dict:
    """How stale the scan actually is, per instrument.

    The watched instrument and any open position tick every cycle, so the
    on-screen LTP is unaffected by universe size; what degrades is SCAN
    coverage, and a late scan is a late BUY. This reports the measured lag
    instead of leaving the user to infer it.
    """
    from app.market.instruments import UNIVERSE

    ages = hub.scan_ages()
    active = list(UNIVERSE)
    watched = set(hub.clients.values()) | {DEFAULT_INSTRUMENT}
    n_scanned = len([s for s in active if s not in watched])
    vals = sorted(ages[s] for s in active if s in ages)
    stale_limit = float(hub.scan_target_sec)
    over = [s for s in active if ages.get(s, 0.0) > stale_limit * 5]
    median = vals[len(vals) // 2] if vals else None
    return {
        "active_count": len(active),
        "ticked_count": len(vals),
        "watched": sorted(set(hub.clients.values())),
        "median_age_sec": median,
        "worst_age_sec": vals[-1] if vals else None,
        "scan_target_sec": stale_limit,
        # What the loop actually delivers, next to what it aims for: measured
        # tick cost, how many scanned names the last cycle got through, and the
        # resulting full-sweep time. A sweep far above the target means the
        # universe is too big for the cadence, not that the scan is broken.
        # Warm-up is a separate, rate-limited phase from scanning; reported so a
        # cold board is visibly loading rather than looking broken or quiet.
        "warm_total": _warm_state["total"],
        "warm_done": min(_warm_state["done"], _warm_state["total"]),
        "tick_cost_ms": hub.tick_cost_ms,
        "scanned_per_cycle": hub.scanned_per_cycle,
        "sweep_estimate_sec": hub.sweep_estimate_sec(n_scanned),
        # Instruments whose exchange is shut are skipped rather than scanned, and
        # resume by themselves at the next open. Reported so a small scan count in
        # the evening reads as the session, not as a starved loop.
        "closed_skipped": hub.closed_skipped,
        # Ticks the loop stopped waiting for, and workers still held by one.
        # A wedged tick no longer stops the scan, so without these the repair
        # would make the fault invisible instead of visible and bounded.
        "ticks_abandoned": hub.ticks_abandoned,
        "ticks_wedged_now": hub.ticks_wedged_now,
        "behind": sorted(over),
        "behind_count": len(over),
        "armed": bool(settings.auto_trade_enabled or settings.futures_paper_enabled),
        "ages": {s: ages[s] for s in active if s in ages},
    }


@app.get("/api/fills")
async def fills() -> dict:
    """Positions that are actually OPEN right now, both books, across instruments.

    This is what an alert must be driven by. A row on the signal board is an
    opinion; a fill is a fact, and the two are not the same — a BUY can be
    surfaced and then refused by the risk governor, the premium floor or the
    concurrency cap. Each entry carries the id the caller keys its "already
    alerted" set on, so an alert fires once per real fill and never on a poll.

    Read-only: it reports state, it does not create or change any order.
    """
    entries: list[dict] = []
    for inst, snap in list(hub.latest.items()):
        pos = snap.position
        if not pos.option_symbol or not pos.entry_time:
            continue
        entries.append(
            {
                "book": "options",
                "id": f"options:{pos.option_symbol}:{pos.entry_time}",
                "instrument": inst,
                "display": snap.instrument_name,
                "symbol": pos.option_symbol,
                "side": pos.side or "LONG",
                "entry": pos.entry_premium,
                "lots": pos.quantity_lots,
                "entry_time": pos.entry_time,
                "pnl": pos.net_pnl,
            }
        )
    for p in futures_paper.store.open_positions():
        entries.append(
            {
                "book": "futures",
                "id": f"futures:{p['instrument']}:{p['opened']}",
                "instrument": p["instrument"],
                "display": p["instrument"],
                "symbol": f"{p['instrument']} FUT",
                "side": p["side"],
                "entry": p["entry"],
                "lots": p["lots"],
                "entry_time": p["opened"],
                "pnl": p.get("net_pnl"),
            }
        )
    entries.sort(key=lambda e: e["entry_time"] or 0, reverse=True)
    return {"open": entries, "open_count": len(entries), "as_of": int(time.time())}


@app.get("/api/auto-pick")
async def auto_pick_status(refresh: bool = False) -> dict:
    """Today's auto-picked instruments (broker's biggest F&O movers + core) and
    whether the feature is on. ``refresh=true`` recomputes and re-applies now."""
    from app.market.instruments import UNIVERSE, REGISTRY

    if not settings.auto_pick_enabled:
        return {"enabled": False, "universe": list(UNIVERSE), "rows": []}
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _refresh_universe, bool(refresh))
    picks = result or {}
    movers = picks.get("movers", [])
    universe = picks.get("universe", list(UNIVERSE))

    def _live(sym: str) -> tuple[float | None, float | None]:
        """Latest %change + price for a symbol from the live hub (no warm)."""
        snap = hub.latest.get(sym)
        if snap is None:
            return None, None
        return snap.futures_change_pct, snap.futures_price

    core_rows = []
    for s in picks.get("core", []):
        if s not in REGISTRY:
            continue
        pct, price = _live(s)
        core_rows.append(
            {
                "symbol": s,
                "display": REGISTRY[s].display,
                "type": "CORE",
                "percent_change": round(pct, 2) if pct is not None else None,
                "direction": None if pct is None else ("UP" if pct >= 0 else "DOWN"),
                "ltp": price,
                "in_universe": s in universe,
            }
        )
    mover_rows = [
        {
            "symbol": m["symbol"],
            "display": REGISTRY[m["symbol"]].display if m["symbol"] in REGISTRY else m["symbol"],
            "type": "MOVER",
            "percent_change": m.get("percent_change"),
            "direction": m.get("direction"),
            "ltp": m.get("ltp"),
            "in_universe": m["symbol"] in universe,
        }
        for m in movers
    ]
    return {
        "enabled": True,
        "count": settings.auto_pick_count,
        "core": [{"symbol": r["symbol"], "display": r["display"]} for r in core_rows],
        "movers": mover_rows,
        "rows": core_rows + mover_rows,
        "universe": universe,
        "source": picks.get("source"),
        "as_of": picks.get("as_of"),
    }


@app.get("/api/screener")
async def screener() -> dict:
    """Live multi-instrument screener: current fresh-entry call for EVERY
    registered instrument. Cached for a few seconds and built off the event
    loop. Read-only observability — never places or influences a trade."""
    import time

    now = time.time()
    # _build_screener only reads already-cached snapshots (pure, fast), so it is
    # safe to run inline without the shared executor — this keeps the screener
    # responsive even while background warming is using executor threads.
    if now - float(_screener_cache["ts"]) > _SCREENER_TTL:  # type: ignore[arg-type]
        _screener_cache["rows"] = _build_screener()
        _screener_cache["ts"] = now
    return {"rows": _screener_cache["rows"], "as_of": _screener_cache["ts"]}


@app.get("/api/command-center")
async def command_center() -> dict:
    """AI Command Center (V4) — a read-only aggregation for the new glanceable
    dashboard tab. It RANKS the instruments already being watched by their
    real, already-computed ``opportunity_score`` (no new model, no fabricated
    numbers). Off unless ``QT_AI_COMMAND_CENTER`` is set → returns
    ``{"enabled": false}`` and the tab shows a disabled card. Read-only:
    never places or influences a trade."""
    import time

    if not settings.ai_command_center:
        return {"enabled": False, "opportunities": []}

    now = time.time()
    if now - float(_screener_cache["ts"]) > _SCREENER_TTL:  # type: ignore[arg-type]
        _screener_cache["rows"] = _build_screener()
        _screener_cache["ts"] = now
    rows = list(_screener_cache["rows"])  # type: ignore[arg-type]
    ranked = sorted(
        rows,
        key=lambda r: (r.get("opportunity_score") or 0.0),
        reverse=True,
    )
    return {"enabled": True, "opportunities": ranked, "as_of": _screener_cache["ts"]}


@app.get("/api/signal")
async def quantum_signal(instrument: str | None = None) -> dict:
    """Quantum Signal — the single-page dashboard's one endpoint. It takes the
    frozen engine's latest snapshot for this instrument and collapses it into
    one honest answer (BUY / WAIT / HOLD / EXIT) gated by a regime + ADX +
    expected-move filter so signals stop firing in chop. Read-only: it never
    changes the engine, and never places a trade."""
    from app.engine import signal_gate

    inst = _norm_instrument(instrument)
    loop = asyncio.get_event_loop()
    st = await loop.run_in_executor(None, registry.get, inst)
    snap = hub.latest.get(st.instrument)
    if snap is None:
        snap = await loop.run_in_executor(None, st.tick)
        hub.latest[st.instrument] = snap
    view = signal_gate.evaluate(snap, adx_min=settings.signal_gate_adx_min)
    view["instruments"] = [i.model_dump(mode="json") for i in snap.instruments]
    view["candles"] = [c.model_dump(mode="json") for c in snap.selected_option_candles[-120:]]
    view["position"] = snap.position.model_dump(mode="json")
    view["auto_trade"] = snap.auto_trade.model_dump(mode="json") if snap.auto_trade else None
    return view


@app.get("/api/signal/scan")
async def quantum_signal_scan() -> dict:
    """Auto-scan: evaluate the Quantum Signal gate for EVERY watched instrument
    (from cached snapshots) and rank them, so the single-page dashboard can
    auto-surface whichever instrument currently has the best actionable BUY
    without the user picking one. Read-only; never places a trade."""
    from app.engine import signal_gate
    from app.market.instruments import UNIVERSE

    active = set(UNIVERSE)
    rows: list[dict] = []
    for inst, snap in list(hub.latest.items()):
        if inst not in active:
            continue
        v = signal_gate.evaluate(snap, adx_min=settings.signal_gate_adx_min)
        rows.append(
            {
                "instrument": v["instrument"],
                "instrument_name": v["instrument_name"],
                "action": v["action"],
                "actionable": v["actionable"],
                "side": v["side"],
                "confidence": v["confidence"],
                "regime": v["regime"],
                "spot": v["spot"],
                "change_pct": v["change_pct"],
            }
        )
    # Rank: actionable BUYs first, then by confidence.
    rows.sort(key=lambda r: (r["actionable"], r["confidence"]), reverse=True)
    best = next((r["instrument"] for r in rows if r["actionable"]), None)
    return {"rows": rows, "best_actionable": best, "as_of": _screener_cache["ts"]}


@app.get("/api/opportunities")
async def opportunities() -> dict:
    """Rank every watched instrument by how much the PREMIUM can actually move.

    Answers "where is the opportunity right now" in the only unit that pays —
    percent of premium, net of round-trip costs — instead of underlying points,
    which are not comparable across a ₹7,900 commodity and a ₹16 stock option.
    Read-only: it ranks and explains, it never places or forces a trade.
    """
    from app.analysis import opportunity
    from app.engine import signal_gate
    from app.market.instruments import UNIVERSE

    active = set(UNIVERSE)
    rows: list[dict] = []
    for inst, snap in list(hub.latest.items()):
        if inst not in active:
            continue
        try:
            opp = opportunity.evaluate(snap)
            gate = signal_gate.evaluate(snap, adx_min=settings.signal_gate_adx_min)
        except Exception:
            continue
        health = snap.feed_health.model_dump() if snap.feed_health else None
        rows.append(
            {
                **opp,
                "instrument_name": snap.instrument_name,
                "action": gate["action"],
                "actionable": gate["actionable"],
                "side": gate["side"],
                # Why there is no BUY on a name that IS moving — the blocking
                # gate is shown rather than left for the user to guess.
                "withheld_because": None if gate["actionable"] else (gate["reasons"] or [None])[0],
                "confidence": gate["confidence"],
                "spot": gate["spot"],
                "change_pct": snap.futures_change_pct,
                "feed": health,
            }
        )
    rows.sort(key=lambda r: (r["actionable"], r["score"]), reverse=True)
    return {
        "rows": rows,
        "as_of": int(time.time()),
        "gate_enabled": settings.opportunity_gate_enabled,
        "min_score": settings.opportunity_min_score,
        "min_move_pct": settings.opportunity_min_move_pct,
    }


@app.get("/api/signal-diagnostics")
async def signal_diagnostics(limit: int = 100) -> dict:
    """Which gate refused each setup, and what the refused setup then did.

    Read-only measurement. Everything is data-gated: with no logged refusals it
    returns empty lists rather than placeholder statistics, because an invented
    blocker table is worse than none — it would be acted on.
    """
    from app.analysis import exec_funnel, missed

    data = missed.summary(limit=limit)
    live: list[dict] = []
    for st in registry.active():
        dec = st.last_decision
        gates = dec.gates if dec is not None else None
        if gates is None:
            continue
        live.append(
            {
                "instrument": st.instrument,
                "signal": dec.signal.value if dec.signal else None,
                "primary_blocker": gates.primary_blocker,
                "secondary_blocker": gates.secondary_blocker,
                "blockers": gates.blockers,
                "confidence": gates.confidence,
                "confidence_gate": gates.confidence_gate,
                "entry_trigger": gates.entry_trigger,
            }
        )
    return {
        "enabled": settings.missed_opportunity_log,
        "live": live,
        # The other half of the answer: where a BUY died between the signal and
        # the fill, so "BUY but no execution" is never unexplained.
        "execution": exec_funnel.summary(limit=min(limit, 50)),
        **data,
    }


@app.get("/api/momentum-outcomes")
async def momentum_outcomes_report(limit: int = 200) -> dict:
    """Per-instrument record of how far each EARLY BUY (Stage 3) actually
    reached — max favourable move, which targets/stop were hit — for analysis.
    Read-only, data-gated (empty until the engine has logged real signals)."""
    from app.execution import momentum_outcomes

    return momentum_outcomes.summary(limit=limit)


@app.get("/api/early-early-report")
async def early_early_report(limit: int = 200) -> dict:
    """Early-Early (Stage 2.5) performance + frozen-agreement report, alongside
    the Early Momentum outcomes, so the three engines (Early-Early, Early
    Momentum, Confirmation BUY) can be compared objectively after paper trading.
    Read-only, data-gated (empty until real signals log). No fabricated stats."""
    from app.execution import early_early_tracker, momentum_outcomes

    return {
        "enabled": settings.early_early_enabled,
        "early_early": early_early_tracker.summary(limit=limit),
        "early_momentum": momentum_outcomes.summary(limit=limit),
    }


@app.get("/api/scalp-report")
async def scalp_report(limit: int = 200) -> dict:
    """Quick Scalp Engine performance report (win rate, average hold time,
    profit factor, false-signal rate), alongside the Early-Early and Early
    Momentum outcomes so all engines can be compared objectively after paper
    trading. Read-only, data-gated (empty until real signals log). No fabricated
    statistics."""
    from app.execution import early_early_tracker, momentum_outcomes, scalp_tracker

    return {
        "enabled": settings.scalp_enabled,
        "scalp": scalp_tracker.summary(limit=limit),
        "early_early": early_early_tracker.summary(limit=limit),
        "early_momentum": momentum_outcomes.summary(limit=limit),
    }


@app.get("/api/risk-guard")
async def risk_guard() -> dict:
    """Account-wide option risk state: day P&L against the cap, the trade count,
    the loss streak and any stand-down in force, plus the per-instrument
    cooldowns.

    This is deliberately visible: on 13 Aug the caps did nothing (they were
    per-instrument and in memory) and there was no way to see that from the
    dashboard — an engine refusing to trade and an engine with a broken cap look
    identical from outside.
    """
    now = time.time()
    snap = account_risk.snapshot(now)
    snap["cooldowns"] = {
        inst: {
            "seconds_since_exit": int(now - ts),
            "stopped_out": inst in account_risk.last_stop,
            "blocked": account_risk.instrument_blocked(inst, now),
        }
        for inst, ts in sorted(account_risk.last_exit.items())
    }
    snap["opening_window_minutes"] = settings.auto_trade_no_entry_open_minutes
    snap["reentry_cooldown_sec"] = settings.auto_trade_reentry_cooldown_sec
    snap["post_stop_cooldown_sec"] = settings.auto_trade_post_stop_cooldown_sec
    return snap


@app.get("/api/futures-report")
async def futures_report(limit: int = 500) -> dict:
    """Scoreboard of the SEPARATE futures paper tool — trades, win rate, profit
    factor, net points and rupees, broken down by exit reason.

    Read-only and data-gated: empty until the tool has actually paper-traded. No
    fabricated statistics, and profit factor is reported next to win rate because
    a leveraged tool can win most of its trades and still lose money.
    """
    from app.execution import futures_paper

    limit = max(1, min(5000, int(limit)))
    deployed = futures_paper.store.deployed_margin()
    return {
        "report": futures_paper.report(limit),
        "trades": list(reversed(futures_paper.read_log(limit))),
        # What the bot is holding right now, across ALL instruments — an
        # instrument merely blocked by the concurrency cap otherwise looks idle.
        "open_positions": futures_paper.store.open_positions(),
        # What the risk governor has spent today: day P&L against the cap, trades
        # per instrument, and any instrument currently standing down. Without this
        # a refusal looks like the tool being idle.
        "guard": futures_paper.guard.snapshot(int(time.time())),
        "capital": {
            "pot": settings.futures_capital,
            "deployed_margin": round(deployed, 0),
            "free": round(settings.futures_capital - deployed, 0),
        },
        "settings": _futures_settings_payload(),
    }


@app.get("/api/flow-report")
async def flow_report(limit: int = 200, only_watchlist: bool = True,
                      session: str | None = None) -> dict:
    """Flow Engine (Candle-Flow) performance report — win rate, average hold
    time, profit factor and false-signal rate — for objective comparison with
    the other engines after paper trading. Read-only, data-gated (empty until
    real signals log). No fabricated statistics.

    By default the report is limited to instruments currently ENABLED in the
    Watchlist (active universe), so disabled instruments the bot no longer
    paper-trades drop out. Pass ``only_watchlist=false`` for the all-time view."""
    from app.analysis import journal_stats
    from app.execution import flow_tracker
    from app.market.instruments import REGISTRY, UNIVERSE

    # ``session=TODAY`` is resolved here, on the IST clock the log is stamped
    # with; an explicit date must still be a real one.
    session = journal_stats.resolve_session(session)
    if session and not journal_stats.valid_date(session):
        raise HTTPException(status_code=400, detail="session must be YYYY-MM-DD")
    lot_sizes: dict[str, int] = {}
    for sym, spec in REGISTRY.items():
        try:
            lot_sizes[sym] = int(spec.lot_size)
        except Exception:
            lot_sizes[sym] = 1

    active = {s.upper() for s in UNIVERSE} if only_watchlist else None
    return {
        "enabled": settings.flow_enabled,
        "paper_lots": max(1, int(settings.flow_paper_lots)),
        "lot_sizes": lot_sizes,
        "only_watchlist": only_watchlist,
        "flow": flow_tracker.summary(limit=limit, active=active,
                                     session=session),
    }


def _filter_book(rows: list[dict], book: str) -> list[dict]:
    """Split the journal into the option book and the futures book.

    Futures paper trades are mirrored in here alongside the option trades so the
    two can be compared, but a combined win rate mixes two different instruments
    with different risk models and means very little — hence the filter.
    """
    b = book.lower()
    if b == "flow":
        return rows
    if b == "futures":
        return [r for r in rows if (r.get("entry_trigger") or "") == "FUT"]
    if b == "options":
        return [r for r in rows if (r.get("entry_trigger") or "") != "FUT"]
    return rows


@app.get("/api/trade-journal")
async def trade_journal(limit: int = 500, book: str = "all") -> dict:
    """Complete trade journal — the FULL persisted record of every closed trade
    (paper AND live) with entry/exit, ₹ P&L, whether the bot opened it, the exit
    reason, entry confidence and a plain-English analyst note. Read-only. Survives
    restarts (SQLite). Newest first. Empty until real trades close."""
    from app import storage

    if book.lower() not in ("all", "options", "futures", "flow"):
        raise HTTPException(
            status_code=400,
            detail="book must be all, options, futures or flow",
        )
    from app.analysis import trade_provenance as prov

    # "flow" is the Flow book on its own; every other view holds it out. Pooled,
    # its 4,647 one-minute legs decide each aggregate by weight of numbers.
    flow_only = book.lower() == "flow"
    all_rows = _filter_book(
        storage.store.journal(
            limit=max(1, min(5000, limit)),
            exclude_flow=not flow_only, only_flow=flow_only,
        ),
        book,
    )
    # Seeded demonstration rows are held out of every figure below. Averaged in,
    # they reported ₹1,94,034 at an 83.5% win rate off 82 copies of one leg.
    provenance = prov.provenance(all_rows)
    rows, _seeded = prov.split(all_rows)
    wins = sum(1 for r in rows if r["win"])
    net = round(sum(float(r["net_pnl"] or 0.0) for r in rows), 1)

    # Per-trigger breakdown so PULLBACK / REVERSAL / IGNITION entries can be
    # judged against each other on REAL fills. Profit factor is the number that
    # matters (win rate alone hides the size of the losses); it is reported as
    # null until there is at least one loss to divide by.
    by_trigger: dict[str, dict] = {}
    for r in rows:
        # Manually-entered and pre-tagging trades have no trigger recorded.
        key = r.get("entry_trigger") or "UNTAGGED"
        b = by_trigger.setdefault(
            key, {"trades": 0, "wins": 0, "net_pnl": 0.0, "gross_win": 0.0, "gross_loss": 0.0}
        )
        pnl = float(r["net_pnl"] or 0.0)
        b["trades"] += 1
        b["wins"] += 1 if r["win"] else 0
        b["net_pnl"] += pnl
        if pnl > 0:
            b["gross_win"] += pnl
        else:
            b["gross_loss"] -= pnl
    for b in by_trigger.values():
        b["net_pnl"] = round(b["net_pnl"], 1)
        b["win_rate"] = round(b["wins"] / b["trades"] * 100, 1) if b["trades"] else 0.0
        b["profit_factor"] = (
            round(b["gross_win"] / b["gross_loss"], 3) if b["gross_loss"] > 0 else None
        )

    return {
        "count": len(rows),
        "wins": wins,
        "losses": len(rows) - wins,
        "net_pnl": net,
        "by_trigger": by_trigger,
        "provenance": provenance,
        # Which book these figures are drawn from, so a number can never be
        # read as "the whole book" when the Flow legs are held out of it.
        "book": book.lower(),
        "flow_excluded": not flow_only,
        "trades": rows,
    }


@app.get("/api/trade-journal.csv")
async def trade_journal_csv(limit: int = 5000, book: str = "all"):
    """Download the complete trade journal as CSV (spreadsheet-friendly).
    Read-only; same data as /api/journal."""
    import csv
    import io

    from fastapi.responses import Response

    from app import storage

    if book.lower() not in ("all", "options", "futures", "flow"):
        raise HTTPException(
            status_code=400,
            detail="book must be all, options, futures or flow",
        )
    from app.analysis import trade_provenance as prov

    flow_only = book.lower() == "flow"
    rows, _seeded = prov.split(
        _filter_book(
            storage.store.journal(
                limit=max(1, min(20000, limit)),
                exclude_flow=not flow_only, only_flow=flow_only,
            ),
            book,
        )
    )
    # entry_trigger lets you compare PULLBACK vs REVERSAL vs IGNITION entries
    # against each other on real fills; FUT marks the futures paper book.
    # capital_used answers "how much money was tied up to earn this": the premium
    # paid on an option, the margin blocked on a future. return_on_capital_pct
    # makes a rupee figure comparable across a Rs17k option and a Rs94k futures
    # margin instead of being read as if both risked the same.
    cols = ["ts", "instrument", "option", "option_type", "entry", "exit", "lots",
            "lot_size", "net_pnl", "win", "capital_used", "notional",
            "risk_rupees", "cost_rupees", "return_on_capital_pct",
            "holding_minutes", "mode", "auto", "entry_trigger",
            "exit_reason", "confidence", "note",
            # Costs itemised (brokerage is per ORDER) and the captured book, so a
            # row can be checked against a contract note and a re-costing can
            # tell a measured spread from an absent one. Rows written before the
            # correction carry cost_model COST_MODEL_OLD, unaltered.
            "book", "cost_model", "brokerage", "brokerage_per_order", "statutory",
            "cost_status", "entry_bid", "entry_ask", "exit_bid", "exit_ask",
            "spread_cost_rupees"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trade_journal.csv"},
    )


@app.get("/api/data-quality-summary")
async def data_quality_summary(
    deep_only: bool = False,
    bar_limit: int = Query(1200, ge=60, le=20000),
) -> dict:
    """READ-ONLY: is the data each instrument is analysed on complete and current?

    Per instrument: one-minute bar completeness, tick duplicates / out-of-order /
    gaps, feed state with exchange and receive timestamps, recorded chain age,
    tier, and a quality verdict. Contaminated instruments are LISTED with the
    reason, never filtered out — the point is to make degradation visible while
    it is happening, not to produce a clean-looking board.

    ``deep_only=true`` reports Tier 1 alone, which is the fair comparison when
    judging the two-tier split: Tier 2 is not supposed to have complete bars.

    Consulted by nothing. No gate, threshold, signal or order reads this.
    """
    from app.analysis import data_health

    return await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: data_health.summary(deep_only=deep_only, bar_limit=bar_limit),
    )


@app.get("/api/watchlist-tiers")
async def watchlist_tiers() -> dict:
    """READ-ONLY: the current two-tier feed split and what it costs.

    Reports the configured Tier 1 (deep) list, the Tier 2 (broad) remainder, any
    live research promotions, and the measured per-cycle call cost of each.
    ``split_enabled=false`` means QT_DEEP_WATCHLIST is unset and every active
    instrument is captured deeply — the behaviour that produced 46.5% missing
    one-minute bars over four recorded sessions.

    This reports capacity; it does NOT claim the split improved anything. That
    claim needs a session actually run at the smaller Tier 1.
    """
    from app.market import tiers

    cap = tiers.capacity()
    return {
        **cap,
        "configured_deep": tiers.configured_deep(),
        "broad": tiers.broad_names(),
        "promotion_enabled": bool(settings.tier_promotion_enabled),
        "promotion_log": tiers.promotions.log(50),
        "note": "feed routing only — no gate, threshold, signal, strike or order "
                "path reads the tier of an instrument",
    }


@app.get("/api/feed-health")
async def feed_health() -> dict:
    """READ-ONLY diagnostic: which Angel keys logged in, whether each key's live
    WebSocket is connected/fresh, and which instruments each key is serving.
    Helps confirm multi-account sharding is actually splitting the load.
    Credentials are masked; only meaningful when the live Angel provider is in
    use (returns provider=<name> otherwise)."""
    provider_name = getattr(settings, "data_provider", "") or ""
    if provider_name.lower() != "angelone":
        return {"provider": provider_name or "unknown", "angel_active": False}
    try:
        from app.market import angelone

        health = await asyncio.get_event_loop().run_in_executor(
            None, angelone.feed_health
        )
    except Exception as exc:
        return {"provider": provider_name, "angel_active": False, "error": str(exc)[:200]}
    return {"provider": provider_name, "angel_active": True, **health}


@app.get("/api/feed-quality")
async def feed_quality_report() -> dict:
    """READ-ONLY per-instrument tick accounting and end-to-end latency chain.

    Reports what was measured — tick ages, duplicate / out-of-order / invalid
    counts, reconnects, exchange->receive lag percentiles and the stage timings
    — so the freshness thresholds can be tuned on real numbers. The thresholds
    are flagged provisional until they have been.
    """
    from app.market.tick_quality import feed_quality

    rows = await asyncio.get_event_loop().run_in_executor(
        None, feed_quality.all_snapshots
    )
    return {
        "summary": feed_quality.summary(),
        "instruments": rows,
        "tick_loop": {
            "watched_interval_sec": hub._WATCHED_INTERVAL,
            "tick_cost_sec": (
                round(hub._tick_cost, 4) if hub._tick_cost is not None else None
            ),
            "scan_rate_per_cycle": round(hub._scan_rate, 2),
            "closed_market_skipped": hub._closed_skipped,
            "warm_instruments": len(hub.latest),
        },
    }


@app.get("/api/feed-profile")
async def feed_profile_report(limit: int = 50) -> dict:
    """READ-ONLY feed profiles: what the feed cost and delivered, per snapshot.

    Each record carries the deep/broad configuration live at the time, so a
    session recorded at a reduced deep watchlist can be compared against one
    recorded at the full watchlist without assuming which setting was active.
    This endpoint reports; it does not conclude that either setting is better.
    """
    from app.analysis import feed_profile

    rows = await asyncio.get_event_loop().run_in_executor(
        None, feed_profile.recent, max(1, min(500, int(limit)))
    )
    return {
        "enabled": settings.feed_profile_log,
        "interval_sec": int(settings.feed_profile_interval_sec),
        "now": feed_profile.snapshot(await scan_health()),
        "records": rows,
        "note": (
            "Snapshots are observability only. A before/after comparison needs "
            "records from sessions recorded at both deep-watchlist settings; "
            "until both exist, no improvement is claimed."
        ),
    }


@app.get("/api/market-opportunities")
async def market_opportunities(top: int = 10, view: str = "all") -> dict:
    """RESEARCH-ONLY fast opportunity scan of every warm instrument.

    Ranks where the market is actually moving now, on fresh data, before the
    expensive strategy engine runs. It CANNOT emit a trading signal: its output
    vocabulary is OPPORTUNITY / WATCH / NO_OPPORTUNITY plus an opportunity
    state, and it never touches a gate, a stop, a target or an order. Stale,
    dead or closed-market instruments are reported and excluded from ranking
    rather than ranked on old prices.
    """
    from app.analysis import scan_service
    from app.analysis.scanner import filter_view
    from app.market.tick_quality import feed_quality

    top = max(1, min(100, int(top)))
    loop = asyncio.get_event_loop()
    ranked = await loop.run_in_executor(None, scan_service.sweep)
    rows = filter_view(ranked, view)[:top]
    handoff = scan_service.record_handoff(ranked, top)
    summary = feed_quality.summary()
    scored = [r for r in ranked if r.opportunity_score is not None]
    return {
        "as_of": int(time.time()),
        "view": (view or "all").lower(),
        "market_health": {
            "instruments_scanned": len(ranked),
            "ranked": len(scored),
            "opportunities": sum(1 for r in scored if r.verdict == "OPPORTUNITY"),
            "watch": sum(1 for r in scored if r.verdict == "WATCH"),
            "fresh_instruments": summary.get("fresh"),
            "aging_instruments": summary.get("aging"),
            "stale_instruments": (summary.get("stale") or 0) + (summary.get("dead") or 0),
            "no_data_instruments": summary.get("no_data"),
        },
        "rows": [r.as_dict() for r in rows],
        "excluded": [
            {
                "instrument": r.instrument,
                "feed_state": r.feed_state,
                "classification": r.classification,
                "reason": (r.reasons[0] if r.reasons else "not scored"),
            }
            for r in ranked if r.opportunity_score is None
        ],
        "handoff": handoff,
        "note": (
            "Research/observability only. The scanner ranks opportunity quality; "
            "it does not generate BUY/WAIT — the unchanged decision engine does."
        ),
    }


@app.post("/api/instrument")
async def select_instrument(req: InstrumentRequest) -> dict:
    # Per-tab selection: warm up (build) the requested instrument's provider off
    # the event loop and return ok. It no longer mutates a shared "active"
    # instrument, so switching in one tab does NOT change any other tab.
    loop = asyncio.get_event_loop()
    inst = _norm_instrument(req.instrument)
    try:
        st = await loop.run_in_executor(None, registry.get, inst)
    except (ValueError, RuntimeError) as exc:
        return {"ok": False, "message": str(exc), "active": inst}
    return {"ok": True, "active": st.instrument}


@app.get("/api/backtest")
async def backtest(instrument: str | None = None) -> dict:
    return registry.get(_norm_instrument(instrument)).backtest().model_dump(mode="json")


@app.get("/api/account")
async def account() -> dict:
    """Paper-trading account summary across ALL instruments: balance/equity,
    trades per day, daily & monthly P&L, and recent trades. Paper-only."""
    from app import storage
    from app.analysis import account as acct

    is_real = settings.data_provider.lower() not in ("simulated", "sim", "demo")
    trades = storage.store.paper_trades()
    return acct.summary(trades, capital=settings.paper_capital, is_real_feed=is_real)


@app.get("/api/account/live")
async def account_live() -> dict:
    """READ-ONLY real Angel account snapshot: available cash, used margin, net
    balance and open-position P&L. Only reads funds/positions from Angel — it
    never places or modifies an order. Requires QT_DATA_PROVIDER=angelone with
    valid credentials; otherwise returns enabled=false with a reason."""
    if settings.data_provider.lower() != "angelone":
        return {
            "enabled": False,
            "reason": "Live balance needs QT_DATA_PROVIDER=angelone with your "
                      "Angel credentials. Currently on the "
                      f"'{settings.data_provider}' feed.",
        }
    from app.market import angelone

    loop = asyncio.get_event_loop()
    try:
        info = await loop.run_in_executor(None, angelone.account_info)
    except Exception as exc:
        return {"enabled": True, "ok": False, "reason": str(exc)}
    return {"enabled": True, "ok": True, "read_only": True, **info}


@app.get("/api/account/report.csv")
async def account_report_csv(month: str | None = None):
    """Download a CSV statement. `month` = YYYY-MM (defaults to current month)."""
    import datetime as _dt

    from fastapi.responses import Response

    from app import storage
    from app.analysis import account as acct

    m = month or _dt.datetime.utcnow().strftime("%Y-%m")
    csv_text = acct.monthly_csv(
        storage.store.paper_trades(), m, capital=settings.paper_capital
    )
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="quantum-trader-paper-{m}.csv"'
        },
    )


@app.get("/api/learning")
async def learning(instrument: str | None = None) -> dict:
    """Per-factor learned vote weights + their closed-trade performance.

    `recommendations` are ADVISORY weight adjustments for you to review; in the
    default advisory mode they are NOT applied to the live engine (the core
    rules stay fixed). Switch QT_LEARNING_MODE=auto only after reviewing them.
    """
    return {
        "mode": settings.learning_mode,
        "min_trades": settings.learning_min_trades,
        "factors": registry.get(_norm_instrument(instrument)).learning_summary(),
        "recommendations": weights_store.store.recommendations(),
    }


@app.get("/api/performance")
async def performance(instrument: str | None = None) -> dict:
    """Validation / performance report.

    Real metrics (win rate, profit factor, Sharpe, drawdown, expectancy, …) are
    only computed from trades recorded against a REAL broker feed once enough
    have been collected. On the simulated demo feed, or before the threshold,
    this returns an honest "insufficient real data" placeholder rather than
    fabricating performance numbers from synthetic ticks.
    """
    return registry.get(_norm_instrument(instrument)).performance_report()


# --------------------------------------------------------------------------
# Phase 2 — research & validation platform (data / replay / analytics /
# shadow / data-quality). The signal engine is FROZEN; these endpoints only
# collect data and measure/report on the existing engine.
# --------------------------------------------------------------------------
@app.get("/api/research/status")
async def research_status(instrument: str | None = None) -> dict:
    from app.research.store import store

    inst = _norm_instrument(instrument)
    st = store()
    real_feed = settings.data_provider.lower() not in ("simulated", "sim", "demo")
    return {
        "instrument": inst,
        "backend": st.backend,
        "futures_rows": st.futures_count(inst),
        "signals_recorded": st.signal_count(inst),
        "replay_trades": len(st.trades(inst, "replay")),
        "shadow_mode": settings.shadow_mode,
        "data_provider": settings.data_provider,
        "auto_capture_active": settings.research_autocapture and real_feed,
    }


@app.post("/api/research/capture")
async def research_capture(req: CaptureRequest) -> dict:
    """Capture simulated-feed data into the research store so replay/analytics
    can run end-to-end without a live broker (labelled as simulated data)."""
    from app.research import capture

    inst = _norm_instrument(req.instrument)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: capture.capture_from_provider(inst, req.steps))


@app.post("/api/research/replay")
async def research_replay(req: ReplayRequest) -> dict:
    from app.research import replay

    inst = _norm_instrument(req.instrument)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: replay.replay(inst, source="replay", speed=req.speed)
    )


@app.get("/api/research/report")
async def research_report(instrument: str | None = None, source: str = "replay") -> dict:
    from app.research import analytics

    return analytics.report(_norm_instrument(instrument), source=source)


@app.get("/api/research/setups")
async def research_setups(
    instrument: str | None = None,
    source: str = "paper_live",
    outcome: str = "all",
    regime: str | None = None,
    min_confidence: float | None = None,
    min_trade_score: float | None = None,
) -> dict:
    """Setup Library — searchable list of completed trades (read-only). Defaults
    to live paper trades; filter by win/loss, regime, confidence, trade score."""
    from app.research import analytics

    return analytics.setup_library(
        _norm_instrument(instrument), source=source, outcome=outcome,
        regime=regime, min_confidence=min_confidence,
        min_trade_score=min_trade_score,
    )


@app.get("/api/research/data-quality")
async def research_data_quality(instrument: str | None = None) -> dict:
    from app.research import data_quality

    return data_quality.validate_stored(_norm_instrument(instrument), record=False)


@app.post("/api/research/shadow/run")
async def research_shadow_run(req: ShadowRequest) -> dict:
    from app.research import shadow

    inst = _norm_instrument(req.instrument)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: shadow.run_over_stored(inst, horizon_min=req.horizon_minutes)
    )


@app.get("/api/research/shadow")
async def research_shadow(instrument: str | None = None) -> dict:
    from app.research import shadow

    return shadow.summary(_norm_instrument(instrument))


@app.post("/api/research/download")
async def research_download(req: DownloadRequest) -> dict:
    """Download REAL Angel One history into the store. Requires
    QT_DATA_PROVIDER=angelone + credentials; refuses on the simulated feed."""
    from app.research import history

    inst = _norm_instrument(req.instrument)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: history.download(inst, days=req.days)
    )


@app.websocket("/ws")
async def ws(websocket: WebSocket, instrument: str | None = None) -> None:
    await websocket.accept()
    inst = _norm_instrument(instrument)
    # Build off the event loop — a fresh instrument's login/scrip-master fetch
    # would otherwise block every other request while the socket connects.
    loop = asyncio.get_event_loop()
    st = await loop.run_in_executor(None, registry.get, inst)  # ensure active/ticked
    hub.clients[websocket] = st.instrument
    seed = hub.latest.get(st.instrument)
    if seed is not None:
        await websocket.send_json(seed.model_dump(mode="json"))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.clients.pop(websocket, None)
    except Exception:
        hub.clients.pop(websocket, None)


# ---------------------------------------------------------------------------
# Phase 6 — AI engine (LIVE PAPER ONLY)
#
# Every endpoint below is read-only with respect to production and paper-only
# with respect to money. There is deliberately no endpoint that places, modifies
# or cancels a real broker order: the AI's only execution surface is the paper
# book, and the one real-order path in the process refuses while paper mode is on
# (app/ai/safety.py).
# ---------------------------------------------------------------------------
class AIPaperCloseRequest(BaseModel):
    trade_id: str
    reason: str = "AI_EXIT"


class AIPaperLevelRequest(BaseModel):
    trade_id: str
    stop: float | None = None
    target1: float | None = None


@app.get("/api/ai/status")
async def ai_status() -> dict:
    from app.ai import service as ai_service

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, ai_service.status)


@app.get("/api/ai/safety")
async def ai_safety() -> dict:
    """Kill-switch state and every real-order attempt it has refused."""
    from app.ai import safety

    return {**safety.status(), "refusals": safety.order_refusals(50)}


@app.get("/api/ai/decision")
async def ai_decision(instrument: str | None = None) -> dict:
    """One fresh AI decision for an instrument, computed on cached prices."""
    from app.ai import features as F
    from app.ai import orchestrator
    from app.ai import service as ai_service

    inst = _norm_instrument(instrument)
    loop = asyncio.get_event_loop()

    def _run() -> dict:
        st, candles, feed_state, age = ai_service.cached_inputs(inst)
        scan = None
        try:
            from app.analysis import scan_service

            res = scan_service.scan_instrument(st)
            scan = {"state": res.classification, "verdict": res.verdict,
                    "score": res.opportunity_score}
        except Exception:
            scan = None
        dec = orchestrator.evaluate(inst, candles, feed_state, age,
                                    (scan or {}).get("state"),
                                    (scan or {}).get("verdict"))
        out = dec.as_dict()
        out["scan"] = scan
        out["baseline"] = ai_service.baseline_verdict(inst)
        out["bars"] = len(candles or [])
        out["feature_window"] = F.WINDOW
        return out

    return await loop.run_in_executor(None, _run)


@app.get("/api/ai/decisions")
async def ai_decisions(instrument: str | None = None, limit: int = 100) -> dict:
    """Journaled AI decisions with the baseline verdict recorded alongside."""
    from app.ai import journal as aij

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(
        None, lambda: aij.journal().decisions(
            _norm_instrument(instrument) if instrument else None,
            max(1, min(1000, int(limit)))))
    return {"as_of": int(time.time()), "rows": rows}


@app.post("/api/ai/cycle")
async def ai_cycle() -> dict:
    """Run one AI cycle now (paper only). Useful when the loop is disabled."""
    from app.ai import service as ai_service

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, ai_service.cycle)


@app.get("/api/ai/paper/positions")
async def ai_paper_positions() -> dict:
    from app.ai import journal as aij
    from app.ai import paper

    loop = asyncio.get_event_loop()

    def _run() -> dict:
        j = aij.journal()
        return {"open": j.open_trades(), "recent": j.trades(50),
                "pnl": paper.pnl_summary()}

    return await loop.run_in_executor(None, _run)


@app.get("/api/ai/paper/pnl")
async def ai_paper_pnl() -> dict:
    from app.ai import paper

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, paper.pnl_summary)


@app.post("/api/ai/paper/close")
async def ai_paper_close(req: AIPaperCloseRequest) -> dict:
    """Close a PAPER position. There is no real-position equivalent by design."""
    from app.ai import paper
    from app.ai import service as ai_service

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: paper.close_paper(req.trade_id, req.reason,
                                        ai_service.resolve_state))


@app.post("/api/ai/paper/levels")
async def ai_paper_levels(req: AIPaperLevelRequest) -> dict:
    """Modify a PAPER position's stop/target."""
    from app.ai import journal as aij

    loop = asyncio.get_event_loop()

    def _run() -> dict:
        j = aij.journal()
        t = j.get_trade(req.trade_id)
        if not t or t.get("status") != "OPEN":
            return {"ok": False, "message": "no such open paper trade"}
        j.set_levels(req.trade_id, req.stop, req.target1)
        return {"ok": True, "trade": j.get_trade(req.trade_id)}

    return await loop.run_in_executor(None, _run)


@app.get("/api/ai/comparison")
async def ai_comparison() -> dict:
    """Baseline engine vs AI engine on identical bars, plus the AI paper book."""
    from app.ai import compare

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, compare.compare)


@app.get("/api/ai/model")
async def ai_model() -> dict:
    """What probability model is loaded, its OOS metrics and its limitations."""
    from app.ai import probability

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, probability.status)


# ---------------------------------------------------------------------------
# Phase 17 — vehicle evidence and the A+ paper board (RESEARCH / PAPER ONLY)
#
# Read-only. Every endpoint below reports what was captured; none of them can
# change a decision, and there is deliberately no endpoint that opens, closes or
# promotes anything. The paper episodes reported here are simulated fills against
# the recorded book — no broker order path is reachable from this module.
# ---------------------------------------------------------------------------
# The A+ Paper tab asks for four of these at once every 20 seconds, and the
# heavy ones re-derive the day from the evidence files. One shared 15-second
# memo means N open tabs cost what one costs; nothing here is a trading value,
# so serving a reading a few seconds old is honest as long as it is labelled.
_P17_TTL_SEC = 15.0
_p17_memo: dict[str, tuple[float, dict]] = {}
_p17_memo_lock = threading.Lock()


def _p17_cached(key: str, build: Callable[[], dict]) -> dict:
    now = time.time()
    with _p17_memo_lock:
        hit = _p17_memo.get(key)
        if hit is not None and now - hit[0] < _P17_TTL_SEC:
            return {**hit[1], "age_sec": round(now - hit[0], 1)}
    out = build()
    with _p17_memo_lock:
        _p17_memo[key] = (now, out)
    return {**out, "age_sec": 0.0}


@app.get("/api/phase17/health")
async def phase17_health() -> dict:
    """Capture health and the EXACT-match KPI that gates every other table."""
    from app.research.phase17 import service as p17

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p17.health)


@app.get("/api/phase17/board")
async def phase17_board() -> dict:
    """§21 A+ board — at most 5 preferred + 5 watch. Zero is a valid day."""
    from app.research.phase17 import service as p17

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p17.board)


@app.get("/api/phase17/evidence")
async def phase17_evidence(limit: int = 60) -> dict:
    """§25 Vehicle Evidence — every eligible candidate today, A+ or not."""
    from app.research.phase17 import service as p17

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: p17.evidence(limit=max(1, min(400, limit)))
    )


@app.get("/api/phase17/paper")
async def phase17_paper() -> dict:
    """Open paper episodes plus the costed book of the resolved ones."""
    from app.research.phase17 import paper as p17_paper, reports, store

    loop = asyncio.get_event_loop()

    def _run() -> dict:
        rows = store.paper()
        return {
            "open": p17_paper.open_rows(),
            "resolved": rows[-200:],
            "report": reports.paper_report(rows),
            "evidence_coverage": store.read_meta(store.PAPER),
            "paper_only": True,
            "no_real_order": True,
        }

    return await loop.run_in_executor(None, lambda: _p17_cached("paper", _run))


@app.get("/api/phase17/summary")
async def phase17_summary() -> dict:
    """§24 one-screen daily summary, built from the persisted evidence."""
    from app.research.phase17 import artefacts, store

    loop = asyncio.get_event_loop()

    def _run() -> dict:
        payloads = artefacts.build_payloads(
            observations=store.observations(),
            legs=store.legs(),
            paper_rows=store.paper(),
            coverage_rows=store.coverage(),
        )
        out = artefacts.summary(payloads)
        out["questions_detail"] = payloads["capture"].get("questions") or []
        # Say what the numbers were computed over. A bounded read means these
        # are rates over the recent tail, not over the whole session.
        out["evidence_coverage"] = {
            name: store.read_meta(name)
            for name in (store.OBSERVATIONS, store.LEGS, store.PAPER)
        }
        return out

    return await loop.run_in_executor(None, lambda: _p17_cached("summary", _run))


@app.get("/api/phase17/journal")
async def phase17_journal(limit: int = 200) -> dict:
    """§33 journal rows — one flat row per captured candidate, read-only.

    Serves the same join the CSV artefact writes, so the daily review does not
    have to run the CLI to see it. Unresolved candidates are included: excluding
    them would bias the record towards the legs that fitted the tracking budget.
    """
    from app.research.phase17 import journal, store

    loop = asyncio.get_event_loop()
    n = max(1, min(2_000, limit))

    def _run() -> dict:
        rows = journal.rows(store.observations(), store.legs(), store.paper())
        return {
            "fields": list(journal.FIELDS),
            "rows": rows[-2_000:],
            "total": len(rows),
            "evidence_coverage": store.read_meta(store.OBSERVATIONS),
            "research_only": True,
            "paper_only": True,
            "no_real_order": True,
        }

    def _serve() -> dict:
        # Cache the widest slice any caller can ask for, then trim per request,
        # so a narrower limit still hits the same memo.
        out = _p17_cached("journal", _run)
        return {**out, "rows": out["rows"][-n:]}

    return await loop.run_in_executor(None, _serve)


# ---------------------------------------------------------------------------
# Phase 18 — the closing auction session, 15:10-15:30 IST (RESEARCH / PAPER ONLY)
#
# CAS began on 2025-08-03, so the entire evidence base is a few weeks old and
# every verdict below is expected to read REQUIRES_MORE_DATA for some time. That
# is the honest state of the question, not a defect.
#
# Read-only, like the Phase 17 block: nothing here opens, closes, promotes or
# sizes anything, no CAS value reaches the Option Signal, and there is no path
# from this module to a broker order.
# ---------------------------------------------------------------------------
@app.get("/api/cas-health")
async def cas_health() -> dict:
    """Window state, capture counts and the exact-match rate that gates the rest."""
    from app.research.phase18 import service as p18

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p18.health)


@app.get("/api/cas-board")
async def cas_board() -> dict:
    """The CAS cards — one per instrument, WAIT and NO_TRADE included."""
    from app.research.phase18 import service as p18

    loop = asyncio.get_event_loop()

    def _run() -> dict:
        return {
            "cards": p18.cards(),
            "open": p18.open_positions(),
            "resolved": p18.resolved()[-100:],
            "window": "15:10-15:30 IST",
            "strategy": "CAS",
            "paper_only": True,
            "no_real_order": True,
            "banner": "CAS PAPER ONLY",
        }

    return await loop.run_in_executor(None, _run)


@app.get("/api/cas-observations")
async def cas_observations(limit: int = 120) -> dict:
    """Today's captured ladders, newest last. Missing books are preserved."""
    from app.research.phase18 import service as p18

    loop = asyncio.get_event_loop()
    n = max(1, min(600, limit))

    def _run() -> dict:
        rows = p18.observations_today()
        return {
            "rows": rows[-n:],
            "total": len(rows),
            "paper_only": True,
            "no_real_order": True,
        }

    return await loop.run_in_executor(None, _run)


@app.get("/api/cas-reconciliation")
async def cas_reconciliation() -> dict:
    """§23 — the arithmetic that has to add up before any table is believed."""
    from app.research.phase18 import service as p18

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p18.reconciliation)


@app.get("/api/cas-report")
async def cas_report() -> dict:
    """The persisted CAS evidence: quality gate, paper economics, verdict.

    Rebuilt from the whole CAS journal, so it is served from a cache bound to
    that journal's exact size and mtime — a mismatch rebuilds and is never
    served stale. See app/research/phase18/cas_cache.py.
    """
    from app.research.phase18 import cas_cache

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, cas_cache.report)


@app.get("/api/cas-journal")
async def cas_journal(limit: int = 200) -> dict:
    """§22 journal rows for the CAS paper book, unresolved legs included."""
    from app.research.phase18 import journal, store

    loop = asyncio.get_event_loop()
    n = max(1, min(2_000, limit))

    def _run() -> dict:
        rows = journal.rows(store.paper())
        return {
            "fields": list(journal.COLUMNS),
            "rows": rows[-n:],
            "total": len(rows),
            "paper_only": True,
            "no_real_order": True,
        }

    return await loop.run_in_executor(None, _run)


@app.get("/api/cas-safety")
async def cas_safety() -> dict:
    """The no-order-path scan, run over the installed Phase 18 source."""
    from app.research.phase18 import safety

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, safety.scan)


# ---------------------------------------------------------------------------
# Phase 19 — futures paper book, T1 calibration, production readiness.
#
# The thin delta over Phase 17/18: the three things that did not exist. Futures
# Signal had no costed paper lifecycle, so "resolved futures trades" accrued at
# zero per session; the T1 score had no way to earn the right to be printed as a
# percentage; and the three strategies' evidence lived in three separate reports
# with no single answer to "is anything close".
#
# Read-only, and there is deliberately no endpoint that activates, promotes or
# sizes anything. PRODUCTION_CANDIDATE here is a request for review — no code
# path exists from this module to a broker order.
# ---------------------------------------------------------------------------
@app.get("/api/phase19/futures-paper")
async def phase19_futures_paper() -> dict:
    """The futures paper book: health, open trades, resolved rows, folds."""
    from app.research.phase19 import service as p19

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p19.futures_board)


@app.get("/api/phase19/calibration")
async def phase19_calibration() -> dict:
    """Brier / ECE / reliability for T1, fitted on development sessions only.

    While the verdict is not CALIBRATED the caller must keep showing the T1 score
    and rank band; ``display`` says which of the two is permitted.
    """
    from app.research.phase19 import service as p19

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p19.calibration)


@app.get("/api/phase19/readiness")
async def phase19_readiness() -> dict:
    """Option A+ / Futures A+ / CAS in one shape, with explicit blocking reasons."""
    from app.research.phase19 import service as p19

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p19.readiness)

# ---------------------------------------------------------------------------
# Phase 22 — CORE A+ PAPER (research and paper only)
#
# One setup, frozen before it was measured, on one board. Read-only: there is no
# endpoint here that takes a trade, promotes a candidate or changes a production
# gate, and the board's own rows carry paper_only/no_real_order so a client
# cannot render them as anything else. NO_TRADE is a result, not an empty state.
# ---------------------------------------------------------------------------
@app.get("/api/phase22/board")
async def phase22_board() -> dict:
    """CORE A+ PAPER: one row per scanned instrument, or NO TRADE."""
    from app.research.phase22 import board as p22_board

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p22_board.board)


@app.get("/api/phase22/health")
async def phase22_health() -> dict:
    """Observation count, failures and the frozen definition's fingerprint."""
    from app.research.phase22 import service as p22

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p22.health)


@app.get("/api/phase22/definition")
async def phase22_definition() -> dict:
    """The frozen setup as data. Its fingerprint appears on every artefact."""
    from app.research.phase22 import definition as p22_defn

    return {"definition": p22_defn.spec(),
            "fingerprint": p22_defn.fingerprint(),
            "version": p22_defn.VERSION}


@app.get("/api/phase22/closed")
async def phase22_closed(limit: int = 200) -> dict:
    """Resolved paper legs from this session's board, newest last."""
    from app.research.phase22 import board as p22_board

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, p22_board.closed_rows)
    capped = max(1, min(int(limit), 500))
    return {"rows": rows[-capped:], "count": len(rows), "paper_only": True}


@app.get("/api/phase22/study")
async def phase22_study() -> dict:
    """The last written study artefacts, if the CLI has been run.

    The study itself is a command-line run over a saved pool — it is far too
    heavy for a request, and running it on demand would let a dashboard refresh
    re-measure a holdout. The directory is fixed rather than a query parameter:
    a caller-supplied path here would be a file read of the caller's choosing.
    """
    from app.research.phase22 import artefacts as p22_art

    outdir = "data/phase22"

    def _read() -> dict:
        out: dict = {"outdir": outdir, "available": [], "missing": []}
        for name in p22_art.FILES:
            path = os.path.join(outdir, f"phase22_{name}.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as fh:
                    out[name] = json.load(fh)
                out["available"].append(name)
            else:
                out["missing"].append(name)
        return out

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read)


# ---------------------------------------------------------------------------
# FROZEN PAIR CAPTURE — BANKNIFTY/NIFTY RELATIVE VALUE + NEAR/NEXT BASIS
#
# Evidence accumulation for ONE frozen configuration. No endpoint here selects a
# threshold, fits a hedge ratio or promotes anything, and the retest withholds a
# verdict until the pre-declared sample exists.
# ---------------------------------------------------------------------------
@app.get("/api/pairs/status")
async def pairs_status() -> dict:
    """Frozen pair capture: counts, the retest so far, and basis coverage.

    Read-only. The retest publishes no verdict until the pre-declared evidence
    bar is met, so this endpoint cannot become a promotion signal.
    """
    from app.research.pairs import report as pair_report

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, pair_report.status)


@app.get("/api/pairs/spec")
async def pairs_spec() -> dict:
    """The frozen relationship and its fingerprint."""
    from app.research.pairs import spec as pair_spec

    return pair_spec.as_dict()


@app.get("/api/pairs/health")
async def pairs_health() -> dict:
    """Capture counters and the last research-side error, if any."""
    from app.research.pairs import capture as pair_capture

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, pair_capture.health)


# ---------------------------------------------------------------------------
# PHASE 23 — BREAK-EVEN HURDLE, PAPER SHADOW
#
# Read-only in the strongest sense: there is no endpoint here that arms a gate,
# sets a threshold or touches the auto-buy path. The shadow only records what the
# live engine did and what a <=3% / <=5% measured-hurdle rule would have done, so
# the cutoff can be discovered from data instead of assumed.
# ---------------------------------------------------------------------------
@app.get("/api/phase23/summary")
async def phase23_summary() -> dict:
    """Cumulative + daily arms, sweep, stability, coverage and the verdict."""
    from app.research.phase23 import service as p23

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p23.summary)


@app.get("/api/phase23/rows")
async def phase23_rows(limit: int = 200) -> dict:
    """Recorded auto BUY opportunities, newest first."""
    from app.research.phase23 import service as p23

    capped = max(1, min(int(limit), 1000))
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, lambda: p23.rows(capped))
    return {"rows": rows, "count": len(rows), "paper_only": True,
            "spread_source": "MEASURED_BID_ASK_ONLY"}


@app.get("/api/phase23/health")
async def phase23_health() -> dict:
    """Observation/resolution counts, failures and the store's line counts."""
    from app.research.phase23 import service as p23

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p23.health)

# ---------------------------------------------------------------------------
# PHASE 24 — FIVE-YEAR STRATEGY DISCOVERY (RESEARCH ONLY)
#
# Separate from Phase 23 and from production: these endpoints only read report
# artefacts the CLI wrote. There is no tick hook, no paper order, no gate and no
# threshold here, and a study run is deliberately not exposed over HTTP.
# ---------------------------------------------------------------------------
@app.get("/api/phase24/coverage")
async def phase24_coverage() -> dict:
    """What five-year history exists, and why CE/PE cannot be studied on it."""
    from app.research.phase24 import service as p24

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p24.coverage)


@app.get("/api/phase24/summary")
async def phase24_summary() -> dict:
    """Windows, pool base rates, the fifteen answers and the conclusion."""
    from app.research.phase24 import service as p24

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p24.summary)


@app.get("/api/phase24/strategies")
async def phase24_strategies(limit: int = 25) -> dict:
    """Ranked discovered strategies, best first, with their failed clauses."""
    from app.research.phase24 import service as p24

    capped = max(1, min(int(limit), 200))
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, lambda: p24.strategies(capped))
    return {"rows": rows, "count": len(rows), "research_only": True,
            "vehicles_measured": ["FUTURES"],
            "vehicles_requiring_more_data": ["CE", "PE"]}


# ---------------------------------------------------------------------------
# PHASE 25 — CAPTURED-WINDOW CE/PE STUDY (RESEARCH ONLY)
#
# Separate from Phase 23, Phase 24 and production. These endpoints only read
# report artefacts the Phase 25 CLI wrote: no tick hook, no paper order, no
# gate, no threshold, and a study run is deliberately not exposed over HTTP.
# Every payload carries the captured-window claim so a panel cannot present a
# lead as a validated edge.
# ---------------------------------------------------------------------------
@app.get("/api/phase25/coverage")
async def phase25_coverage() -> dict:
    """Which instruments have enough captured real books to be studied."""
    from app.research.phase25 import service as p25

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p25.coverage)


@app.get("/api/phase25/summary")
async def phase25_summary() -> dict:
    """Execution model, per-instrument pools, CE vs PE, hurdle bands, verdict."""
    from app.research.phase25 import service as p25

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p25.summary)


@app.get("/api/phase25/cohorts")
async def phase25_cohorts(limit: int = 25) -> dict:
    """Ranked captured-window cohorts, best first, with their failed clauses."""
    from app.research.phase25 import WINDOW_CLAIM
    from app.research.phase25 import service as p25

    capped = max(1, min(int(limit), 200))
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, lambda: p25.cohorts(capped))
    return {"rows": rows, "count": len(rows), "research_only": True,
            "paper_only": True, "window_claim": WINDOW_CLAIM}


# ---------------------------------------------------------------------------
# PHASE 26 — EXIT MANAGEMENT AND TRADABILITY (RESEARCH / ADVISORY ONLY)
#
# Separate from Phase 23, 24, 25 and production. These endpoints only read
# report artefacts the Phase 26 CLI wrote: no tick hook, no paper order, no
# gate, no threshold, and a study run is deliberately not exposed over HTTP.
# The advisory table is served with the sentence that says it is not wired to
# any live refusal, so a panel cannot present it as one.
# ---------------------------------------------------------------------------
@app.get("/api/phase26/coverage")
async def phase26_coverage() -> dict:
    """Which instruments have enough captured real books to be studied."""
    from app.research.phase26 import service as p26

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p26.coverage)


@app.get("/api/phase26/summary")
async def phase26_summary() -> dict:
    """Exit-variant comparison, per-instrument winners and the verdict."""
    from app.research.phase26 import service as p26

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p26.summary)


@app.get("/api/phase26/variants")
async def phase26_variants() -> dict:
    """Pooled result per exit variant, baseline included for comparison."""
    from app.research.phase26 import service as p26

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p26.variants)


@app.get("/api/phase26/advisory")
async def phase26_advisory() -> dict:
    """Measured tradability per instrument. Advisory only; nothing is wired."""
    from app.research.phase26 import service as p26

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p26.advisory)


@app.get("/api/phase26/cohorts")
async def phase26_cohorts(limit: int = 25) -> dict:
    """Ranked event cohorts under the winning exit, with their failed clauses."""
    from app.research.phase26 import WINDOW_CLAIM as P26_WINDOW_CLAIM
    from app.research.phase26 import service as p26

    capped = max(1, min(int(limit), 200))
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, lambda: p26.cohorts(capped))
    return {"rows": rows, "count": len(rows), "research_only": True,
            "paper_only": True, "window_claim": P26_WINDOW_CLAIM}


# ---------------------------------------------------------------------------
# PHASE 27 — HIGHER-TIMEFRAME FUTURES RESEARCH (5m / 15m, RESEARCH ONLY)
#
# Separate from Phase 23, 24, 25, 26 and production. These endpoints only read
# artefacts the Phase 27 CLI wrote: no tick hook, no paper order, no gate, no
# order path, and a study run is deliberately not exposed over HTTP. Every
# payload carries the pre-committed stopping rule and the sentence saying the
# futures spread is unmeasured, so a panel cannot present a research number as
# a tradable one. The option-book capture is untouched and is not read here.
# ---------------------------------------------------------------------------
@app.get("/api/phase27/coverage")
async def phase27_coverage() -> dict:
    """Which 1-minute series can be aggregated, and how many 5m/15m bars."""
    from app.research.phase27 import service as p27

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p27.coverage)


@app.get("/api/phase27/summary")
async def phase27_summary() -> dict:
    """The last study: pools per timeframe, the answers and the verdict."""
    from app.research.phase27 import service as p27

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p27.summary)


@app.get("/api/phase27/comparison")
async def phase27_comparison() -> dict:
    """1-minute vs 5-minute vs 15-minute pool economics, side by side."""
    from app.research.phase27 import service as p27

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p27.comparison)


@app.get("/api/phase27/strategies")
async def phase27_strategies(limit: int = 25) -> dict:
    """Ranked cohorts with each window, the walk-forward and failed clauses."""
    from app.research.phase27 import STOP_RULE as P27_STOP_RULE
    from app.research.phase27 import service as p27

    capped = max(1, min(int(limit), 200))
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, lambda: p27.strategies(capped))
    return {"rows": rows, "count": len(rows), "research_only": True,
            "paper_only": True, "stop_rule": P27_STOP_RULE}


@app.get("/api/phase27/geometry")
async def phase27_geometry() -> dict:
    """Pool economics per timeframe, stop band and target, with no entry rule."""
    from app.research.phase27 import SPREAD_CLAIM as P27_SPREAD_CLAIM
    from app.research.phase27 import service as p27

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, p27.geometry)
    return {"rows": rows, "count": len(rows), "research_only": True,
            "paper_only": True, "spread_claim": P27_SPREAD_CLAIM}


# ---------------------------------------------------------------------------
# PHASE 28 — MULTI-DAY FUTURES & CASH-EQUITY RESEARCH (RESEARCH ONLY)
#
# Separate from Phase 23-27 and from production. These endpoints only read
# artefacts the Phase 28 CLI wrote: no tick hook, no paper order, no gate, no
# order path, and a study run is deliberately not exposed over HTTP. Every
# payload carries the pre-committed stopping rule and the buy-and-hold claim, so
# a long-only row cannot be read as an edge without the passive comparison next
# to it, and the cash-equity history status travels separately from the strategy
# numbers so a machine with no stock history renders UNANSWERED rather than an
# empty table that looks like a failed stock study. Option strategy logic and
# the option-book capture are untouched and are not read here.
# ---------------------------------------------------------------------------
@app.get("/api/phase28/coverage")
async def phase28_coverage() -> dict:
    """Which instruments have five years of daily bars, and which do not."""
    from app.research.phase28 import service as p28

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p28.coverage)


@app.get("/api/phase28/summary")
async def phase28_summary() -> dict:
    """The last multi-day study: the answers, the verdict and the stop rule."""
    from app.research.phase28 import service as p28

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p28.summary)


@app.get("/api/phase28/funnel")
async def phase28_funnel() -> dict:
    """How far the search got per vehicle, clause by clause."""
    from app.research.phase28 import service as p28

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p28.funnel)


@app.get("/api/phase28/economics")
async def phase28_economics() -> dict:
    """Multi-day pool economics against Phase 27's intraday cost-per-risk."""
    from app.research.phase28 import service as p28

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p28.economics)


@app.get("/api/phase28/baselines")
async def phase28_baselines() -> dict:
    """Buy-and-hold and the textbook rules, measured under the same costs."""
    from app.research.phase28 import service as p28

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p28.baselines)


@app.get("/api/phase28/strategies")
async def phase28_strategies(limit: int = 25) -> dict:
    """Ranked multi-day cohorts with every window and the failed clauses."""
    from app.research.phase28 import STOP_RULE as P28_STOP_RULE
    from app.research.phase28 import service as p28

    capped = max(1, min(int(limit), 200))
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, lambda: p28.strategies(capped))
    return {"rows": rows, "count": len(rows), "research_only": True,
            "paper_only": True, "stop_rule": P28_STOP_RULE}


# ---------------------------------------------------------------------------
# Phase 29 — defined-risk credit spreads, read-only.
#
# The seller's side of the option book, priced the only honest way: the short leg
# sold at the bid, the protective leg bought at the ask, both quoted in the same
# snapshot, four legs of charges. Every route below reads artefacts the CLI
# wrote; none of them can start a study (a study takes minutes and would starve
# the engine), and none of them touches a signal, a paper book or an order path.
# No margin figure is produced and no expiry outcome is assumed, so the payload
# carries those two claims next to any number a panel might render.
# ---------------------------------------------------------------------------
@app.get("/api/phase29/coverage")
async def phase29_coverage() -> dict:
    """Which instruments quoted two strikes in one snapshot, so a spread prices."""
    from app.research.phase29 import service as p29

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p29.coverage)


@app.get("/api/phase29/summary")
async def phase29_summary() -> dict:
    """The last credit-spread study: verdict, answers and the three claims."""
    from app.research.phase29 import service as p29

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p29.summary)


@app.get("/api/phase29/sweep")
async def phase29_sweep(limit: int = 60) -> dict:
    """Unconditional structure economics, read before any cohort table."""
    from app.research.phase29 import service as p29

    capped = max(1, min(int(limit), 500))
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, lambda: p29.sweep(capped))
    return {"rows": rows, "count": len(rows), "research_only": True,
            "paper_only": True}


@app.get("/api/phase29/cohorts")
async def phase29_cohorts(limit: int = 25) -> dict:
    """Ranked spread cohorts with every window and the clauses they failed."""
    from app.research.phase29 import service as p29

    capped = max(1, min(int(limit), 200))
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, lambda: p29.cohorts(capped))
    return {"rows": rows, "count": len(rows), "research_only": True,
            "paper_only": True}


@app.get("/api/phase29/holds")
async def phase29_holds() -> dict:
    """One-hour hold against holding to the session close, as a description."""
    from app.research.phase29 import service as p29

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p29.holds)


@app.get("/api/phase29/leads")
async def phase29_leads() -> dict:
    """Cohorts that cleared every clause. Paper collection only, never promoted."""
    from app.research.phase29 import service as p29

    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, p29.leads)
    return {"rows": rows, "count": len(rows), "research_only": True,
            "paper_only": True, "promoted": False}


# ---------------------------------------------------------------------------
# Phase 35 — OPPORTUNITY PAPER (§20), read-only.
# Whether the money is lost because there was no opportunity or because the
# engine selected, entered, expressed or exited it wrongly. Both routes read the
# opportunity store and the artefacts the CLI wrote; neither can ingest, rebuild
# or start Stage A (a rebuild walks 100k+ raw rows and Stage A 1.4m bars, either
# would starve the engine), and neither touches a signal, a gate or an order
# path. Every payload carries research_only/paper_only next to its numbers.
# ---------------------------------------------------------------------------
@app.get("/api/phase35/panel")
async def phase35_panel(
    instrument: str | None = None,
    vehicle: str | None = None,
    book: str | None = None,
    session: str | None = None,
    source: str | None = None,
    setup: str | None = None,
) -> dict:
    """Opportunity funnel, both paper books, attribution and giveback."""
    from app.research import phase35 as p35const
    from app.research.phase35 import service as p35

    allowed_vehicles = {"CE", "PE", "FUTURES"}
    allowed_books = {"CURRENT_ENGINE_PAPER", "FULL_MARKET_PAPER"}
    filters_in: dict[str, str] = {}
    if instrument and instrument.isalnum() and len(instrument) <= 24:
        filters_in["instrument"] = instrument.upper()
    if vehicle and vehicle.upper() in allowed_vehicles:
        filters_in["vehicle"] = vehicle.upper()
    if book and book.upper() in allowed_books:
        filters_in["book"] = book.upper()
    if session and len(session) <= 10 and session.replace("-", "").isdigit():
        filters_in["session"] = session
    if source and source.upper() in {"ENGINE", "BOARD"}:
        filters_in["source"] = source.upper()
    if setup and setup.upper() in set(p35const.OPPORTUNITY_TYPES):
        filters_in["setup"] = setup.upper()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: p35.panel(filters_in=filters_in or None)
    )


# ---------------------------------------------------------------------------
# Phase 36 — CRUDEOIL vehicle selection (FUTURES vs CE vs PE), read-only.
# The route reads the artefacts the CLI wrote and nothing else: a run resolves
# every leg at twelve horizons plus a placebo and a stress grid, so computing it
# inside a request would starve the capture the evidence comes from. No signal,
# gate, strike, target, stop, exit, sizing or order path is reachable from here,
# and the study can never return VALIDATED.
# ---------------------------------------------------------------------------
@app.get("/api/phase36/report")
async def phase36_report(
    instrument: str = "CRUDEOIL",
    artefact: str = "verdict",
) -> dict:
    """One written Phase 36 artefact, or a statement that none exists yet."""
    from app.research.phase36 import service as p36

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: p36.artefact(artefact, instrument=instrument)
    )


@app.get("/api/phase37/evidence")
async def phase37_evidence() -> dict:
    """How many sessions of tab evidence exist, and what that sample permits.

    Reads only what ``python -m app.research.phase37.cli collect`` wrote; it does
    not collect, because collecting means fetching every research tab and doing
    that inside a request would have the app fetch itself. Sample-size
    bookkeeping only — nothing here promotes a candidate or enables a vehicle.
    """
    from app.research.phase37 import rollup as p37

    loop = asyncio.get_event_loop()
    state = await loop.run_in_executor(None, p37.rollup)
    state["headline"] = p37.headline(state)
    return state


@app.get("/api/phase37/schedule")
async def phase37_schedule() -> dict:
    """Whether *this* process's nightly snapshot thread is alive and waiting.

    The CLI cannot answer this. ``cli schedule-status`` starts a second process
    with no scheduler in it, so its ``running`` is always false however healthy
    the app is; only the process that owns the thread can report on it. Read-only
    status — this route cannot trigger a collection, and the scheduler it
    describes only ever files a snapshot against the day it runs on.
    """
    from app.research.phase37 import schedule as p37s

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p37s.status)


@app.get("/api/historical-import/datasets")
async def historical_import_datasets() -> dict:
    """Which manually supplied historical CSVs have been imported, and their limits.

    Read-only over the append-only import registry. There is deliberately no
    route that imports: an import reads a file path off the local disk, and an
    endpoint that took one would let a request name any file on the host. The
    importer is a CLI an operator runs, and this is the window onto its result.

    Every row here is ``HISTORICAL_CANDLE_DATA``. A candle carries no bid and no
    ask, so nothing on this page is an executable quote, none of it is a trading
    signal, and the execution answer for all of it stays ``EXECUTION_UNMEASURED``.
    """
    from app.research.historical_import import coverage as hi

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, hi.report)


@app.get("/api/opportunity/status")
async def opportunity_status() -> dict:
    """Where every candidate in the market-wide search currently stands.

    Read-only over the append-only candidate, shadow and journal files. There is
    deliberately no route that runs a cycle: a cycle generates candidates,
    screens years of history and writes evidence, which is an operator's
    decision taken at a CLI and not something a page refresh should start.

    Nothing here is a trading signal, nothing here is promoted to live money,
    and a candidate that qualifies reaches production *paper* and stops there.
    """
    from app.research.opportunity import cycle as oppcycle

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, oppcycle.status)


@app.get("/api/opportunity/ranking")
async def opportunity_ranking() -> dict:
    """Where the market currently presents the best *measurable* net opportunity.

    An eligibility ranking over measured inputs, not a prediction and not a
    confidence: an instrument with no history is returned unranked with the
    absence named, because ranking it low would imply a comparison that never
    happened. ``NO_TRADE_ANYWHERE`` is a first-class answer.
    """
    from app.research.opportunity import ranking as oppranking

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, oppranking.rank)


@app.get("/api/opportunity/promotion")
async def opportunity_promotion() -> dict:
    """The promotion gates, candidate by candidate, and the current champion.

    Every gate must pass; there is no composite score, so a large historical
    sample cannot pay for a failed cost-stress or an untouched holdout. The
    strongest label this route can return is a request for human review of a
    controlled live test — it authorises nothing by itself.
    """
    from app.research.opportunity import promotion as opppromotion

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, opppromotion.report)


@app.get("/api/opportunity/timeframes")
async def opportunity_timeframes() -> dict:
    """Gross edge against the round trip at each bar length.

    Read-only over the screening rows already on disk; it starts no study. The
    column that carries the answer is ``cost_multiple``: below 1.0 a candidate
    earns more than its own execution, above 1.0 it does not, and the first
    cycle's one-minute family sat at roughly 3.

    A reading below 1.0 here is a lead and nothing more. The cost is modelled
    because a candle carries no book, each bar length is counted as its own
    hypothesis, and the holdout column is shown beside the others rather than
    used to admit anything.
    """
    from app.research.opportunity import htf as opphtf

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, opphtf.report)


@app.get("/api/phase35/stagea")
async def phase35_stagea() -> dict:
    """The last written five-year discovery table. CANDIDATE is its ceiling."""
    from app.research.phase35 import service as p35

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p35.stagea_artefact)


# ---------------------------------------------------------------------------
# Phase 45 — SHADOW SIGNALS: the futures board, the options board (CE and PE
# separately), the synchronized vehicle comparison, the paper journal and the
# shadow performance summary. RESEARCH ONLY, PAPER ONLY.
#
# Read-only in the strongest sense available here: these routes read a journal
# written by the capture path and can neither evaluate, re-evaluate, restate nor
# delete an event. There is deliberately no endpoint that enters, exits, sizes,
# promotes or arms anything, and no order path is reachable from the module they
# import. The production BUY/SELL/WAIT signal is not read, not written and not
# affected by anything below.
# ---------------------------------------------------------------------------
_P45_VEHICLES = {"FUTURES", "CE", "PE"}
_P45_ACTIONS = {"SHADOW_BUY", "SHADOW_SELL", "SHADOW_WAIT", "SHADOW_UNMEASURED"}
_P45_DIRECTIONS = {"LONG", "SHORT"}


def _p45_filters(
    instrument: str | None,
    vehicle: str | None,
    direction: str | None,
    action: str | None,
    session: str | None,
    definition: str | None,
    since: float | None,
    until: float | None,
) -> dict:
    """Validated filters, allowlisted by value as well as by column name.

    The store allowlists the column; this allowlists what may be compared to it,
    so a query string cannot put an arbitrary string into a parameter that a
    later reader would mistake for a captured value.
    """
    out: dict[str, object] = {}
    if instrument and instrument.replace("_", "").isalnum() and len(instrument) <= 24:
        out["instrument"] = instrument.upper()
    if vehicle and vehicle.upper() in _P45_VEHICLES:
        out["vehicle"] = vehicle.upper()
    if direction and direction.upper() in _P45_DIRECTIONS:
        out["direction"] = direction.upper()
    if action and action.upper() in _P45_ACTIONS:
        out["shadow_action"] = action.upper()
    if session and len(session) <= 10 and session.replace("-", "").isdigit():
        out["session"] = session
    if definition and definition.isalnum() and len(definition) <= 32:
        out["definition"] = definition
    if isinstance(since, (int, float)) and since > 0:
        out["since"] = float(since)
    if isinstance(until, (int, float)) and until > 0:
        out["until"] = float(until)
    return out


@app.get("/api/phase45/board")
async def phase45_board(
    instrument: str | None = None,
    vehicle: str | None = None,
    direction: str | None = None,
    action: str | None = None,
    session: str | None = None,
    definition: str | None = None,
    since: float | None = None,
    until: float | None = None,
    limit: int = 240,
) -> dict:
    """Both boards, the synchronized comparison and the shadow performance."""
    from app.research.phase45 import service as p45

    filters = _p45_filters(instrument, vehicle, direction, action, session,
                           definition, since, until)
    bounded = max(3, min(600, int(limit)))
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: p45.boards(filters=filters or None, limit=bounded)
    )


@app.get("/api/phase45/journal")
async def phase45_journal(
    instrument: str | None = None,
    vehicle: str | None = None,
    direction: str | None = None,
    action: str | None = None,
    session: str | None = None,
    definition: str | None = None,
    since: float | None = None,
    until: float | None = None,
    limit: int = 200,
) -> dict:
    """The immutable shadow journal itself, newest first, with paper outcomes."""
    from app.research.phase45 import service as p45

    filters = _p45_filters(instrument, vehicle, direction, action, session,
                           definition, since, until)
    bounded = max(1, min(1000, int(limit)))
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: p45.journal(filters=filters or None, limit=bounded)
    )


@app.get("/api/phase45/status")
async def phase45_status() -> dict:
    """Aggregate observability: evaluations, refusals, journal writes, latency."""
    from app.research.phase45 import service as p45

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p45.stats)


# --------------------------------------------------------------------------
# Phase 46 — the research overlay beside the production signal board.
#
# Additive, and only additive: the Option Signal endpoints above are untouched,
# nothing here is read by the engine, and a client that never calls these
# routes sees exactly the board it saw before. Each payload carries its own
# PAPER_ONLY / NO_ORDER_PATH / PRODUCTION_UNCHANGED labels so a row cannot be
# quoted out of the context that makes it honest.
# --------------------------------------------------------------------------
def _p46_instrument(instrument: str | None) -> str | None:
    """A validated instrument name, or None. Never interpolated into SQL."""
    if (
        instrument
        and instrument.replace("_", "").isalnum()
        and len(instrument) <= 24
    ):
        return instrument.upper()
    return None


@app.get("/api/phase46/overlay")
async def phase46_overlay(
    instrument: str | None = None,
    limit: int = 10,
) -> dict:
    """The overlay for recent decision instants, grouped by instant.

    One indexed read of the overlay journal, bounded. No historical scan runs
    on a refresh — the rows were classified when the instant happened.
    """
    from app.research.phase46 import service as p46

    name = _p46_instrument(instrument)
    bounded = max(1, min(60, int(limit)))
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: p46.board(instrument=name, limit=bounded)
    )


@app.get("/api/phase46/journal")
async def phase46_journal(
    instrument: str | None = None,
    vehicle: str | None = None,
    state: str | None = None,
    session: str | None = None,
    limit: int = 200,
) -> dict:
    """The overlay journal itself, newest decision instant first."""
    from app.research.phase46 import service as p46

    filters: dict[str, object] = {}
    name = _p46_instrument(instrument)
    if name:
        filters["instrument"] = name
    if vehicle in ("FUTURES", "CE", "PE"):
        filters["vehicle"] = vehicle
    if state and state.replace("_", "").isalpha() and len(state) <= 24:
        filters["state"] = state.upper()
    if session and len(session) <= 10 and session.replace("-", "").isdigit():
        filters["session"] = session
    bounded = max(1, min(1000, int(limit)))
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: p46.journal(filters=filters or None, limit=bounded)
    )


@app.get("/api/phase46/outcomes")
async def phase46_outcomes() -> dict:
    """Arm A (current signal alone) against arm B (signal + overlay).

    B is a subset of A, and the payload says so: this is a description of
    which legs the overlay would have stood beside, not a test of two
    strategies, and it promotes nothing at any sample size.
    """
    from app.research.phase46 import service as p46

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p46.outcomes)


@app.get("/api/phase46/status")
async def phase46_status() -> dict:
    """Writer observability for the overlay: hand-overs, writes, duplicates."""
    from app.research.phase46 import service as p46

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p46.stats)


# --------------------------------------------------------------------------
# Phase 47 — the research call board. Read-only, additive, paper only.
#
# Prices the calls Phase 46 already admitted against the latest measured quote
# for the same contract, so "is the research making money" is a number that
# moves during the session instead of a claim. Nothing here admits a call,
# changes a signal or reaches an order path, and every payload carries the
# PAPER_ONLY / NO_ORDER_PATH labels.
# --------------------------------------------------------------------------
@app.get("/api/phase47/callboard")
async def phase47_callboard(
    instrument: str | None = None,
    session: str | None = None,
    limit: int = 60,
) -> dict:
    """Open research paper calls, marked to market, with the session tally.

    One indexed read of the overlay journal plus one per distinct contract for
    its latest quote. No historical scan and no path rebuild on a refresh.
    """
    from app.research.phase47 import service as p47

    name = _p46_instrument(instrument)
    day = (
        session
        if session and len(session) <= 10 and session.replace("-", "").isdigit()
        else None
    )
    # The panel asks for 60 and a caller may ask for more. Whatever is granted
    # comes back with the bound beside it, so a reading that stopped here is
    # published as a floor rather than as the session's count.
    bounded = max(1, min(1000, int(limit)))
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: p47.board(instrument=name, session=day, limit=bounded),
    )


@app.get("/api/phase47/sessions")
async def phase47_sessions(limit: int = 40) -> dict:
    """The recorded session tallies — what the board said, session by session."""
    from app.research.phase47 import service as p47

    bounded = max(1, min(400, int(limit)))
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: p47.sessions(limit=bounded))


@app.get("/api/phase47/status")
async def phase47_status() -> dict:
    """Where the call board reads from, and what it is allowed to do."""
    from app.research.phase47 import service as p47

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p47.status)


# --------------------------------------------------------------------------
# PHASE 48 — ADMISSION FUNNEL (read-only attribution over the Phase 46 journal)
# Why an instant was not admitted, counted per stage in the same order Phase 46
# asks the questions. It re-classifies nothing, holds no threshold of its own,
# writes to no journal, and has no order path. A funnel that admits more
# instants is a wider funnel, not a better one, and every payload says so.
# --------------------------------------------------------------------------
@app.get("/api/phase48/funnel")
async def phase48_funnel(
    instrument: str | None = None,
    session: str | None = None,
    options: bool = False,
) -> dict:
    """Where the session's observations stopped: feed, warm-up, direction, cost."""
    from app.research.phase48 import service as p48

    name = _p46_instrument(instrument)
    day = (
        session
        if session and len(session) <= 10 and session.replace("-", "").isdigit()
        else None
    )
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: p48.report(
            instrument=name, session=day, options_only=bool(options)),
    )


@app.get("/api/phase48/sessions")
async def phase48_sessions(limit: int = 20, options: bool = False) -> dict:
    """One funnel per session — so a blocker that never moves is visible."""
    from app.research.phase48 import service as p48

    bounded = max(1, min(120, int(limit)))
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: p48.sessions(limit=bounded, options_only=bool(options))
    )


@app.get("/api/phase48/status")
async def phase48_status() -> dict:
    """What the funnel reads, and what it is not allowed to do."""
    from app.research.phase48 import service as p48

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p48.status)


# --------------------------------------------------------------------------
# PHASE 49 — EVENT COUNTS AND THE SUPERSESSION REGISTRY (read-only here)
# The board's own payloads already carry the event counts. What this block
# exposes is the correction record: which recorded tally was superseded, by
# which, and why. Read-only — the supersession itself is written by the CLI,
# deliberately, so a correction to the durable record is an act someone took
# rather than something a dashboard poll can do on its own.
# --------------------------------------------------------------------------
@app.get("/api/phase49/registry")
async def phase49_registry(session: str | None = None, limit: int = 50) -> dict:
    """Every tally correction on the record, oldest first, nothing removed."""
    from app.research.phase49 import service as p49

    day = (
        session
        if session and len(session) <= 10 and session.replace("-", "").isdigit()
        else None
    )
    bounded = max(1, min(400, int(limit)))
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: p49.registry(session=day, limit=bounded)
    )


@app.get("/api/phase49/status")
async def phase49_status() -> dict:
    """The grouping rule, its fingerprint, and what this layer may not do."""
    from app.research.phase49 import service as p49

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p49.status)


# --------------------------------------------------------------------------
# PHASE 50 — EVENTS, THEIR SESSION STATUS AND THEIR RESOLVED OUTCOMES
#
# Read-only, all four. The event journal and the tally reading are written by
# the CLI and not by a dashboard poll: a durable row that a page refresh can
# create is a row nobody chose to write, and this phase exists because an
# unattributable count is expensive to explain later.
# --------------------------------------------------------------------------
def _p50_session(session: str | None) -> str | None:
    """A session filter accepted only in the journal's own date form."""
    if session and len(session) <= 10 and session.replace("-", "").isdigit():
        return session
    return None


def _p50_limit(limit: int) -> int:
    from app.research.phase47 import service as p47

    return max(1, min(int(p47.MAX_CALLS), int(limit)))


@app.get("/api/phase50/board")
async def phase50_board(
    session: str | None = None,
    instrument: str | None = None,
    limit: int = 5000,
) -> dict:
    """Active events: one row per opportunity, with the market status it ran in."""
    from app.research.phase50 import service as p50

    day, bound = _p50_session(session), _p50_limit(limit)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: p50.dashboard(
            session=day, instrument=instrument, limit=bound,
        ),
    )


@app.get("/api/phase50/events")
async def phase50_events(
    session: str | None = None,
    instrument: str | None = None,
    limit: int = 5000,
) -> dict:
    """Both arms as events, with leg counts beside event counts and each outcome."""
    from app.research.phase50 import service as p50

    day, bound = _p50_session(session), _p50_limit(limit)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: p50.events(session=day, instrument=instrument, limit=bound),
    )


@app.get("/api/phase50/compare")
async def phase50_compare(
    session: str | None = None,
    instrument: str | None = None,
    limit: int = 5000,
) -> dict:
    """Production against production plus the overlay, counted in events."""
    from app.research.phase50 import service as p50

    day, bound = _p50_session(session), _p50_limit(limit)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: p50.compare(session=day, instrument=instrument, limit=bound),
    )


@app.get("/api/phase50/filter")
async def phase50_filter(
    session: str | None = None,
    instrument: str | None = None,
    limit: int = 5000,
) -> dict:
    """The events the overlay selected against the ones it declined, per exit policy."""
    from app.research.phase50 import service as p50

    day, bound = _p50_session(session), _p50_limit(limit)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: p50.filter_compare(
            session=day, instrument=instrument, limit=bound,
        ),
    )


@app.get("/api/phase50/readings")
async def phase50_readings(session: str | None = None, limit: int = 200) -> dict:
    """Every recorded tally with its accrual state and the evidence for it."""
    from app.research.phase50 import service as p50

    day = _p50_session(session)
    bounded = max(1, min(500, int(limit)))
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: p50.readings(session=day, limit=bounded)
    )


@app.get("/api/phase50/status")
async def phase50_status() -> dict:
    """What this layer reads, what it froze, and what it cannot reach."""
    from app.research.phase50 import service as p50

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, p50.status)
