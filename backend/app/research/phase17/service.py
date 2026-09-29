"""The single call the live loop makes into Phase 17 — §5, §23, §25.

One function, ``observe``, called from the tick AFTER the decision is complete,
with the chain the decision was computed on. It is the only coupling between this
phase and the running application, and it is deliberately narrow so the safety
property is checkable by reading one file: nothing here returns a value the tick
uses, so no output of this module can change what the engine does.

The call is also failure-isolated. An exception inside research capture must never
break a tick — but it must not be invisible either, because a silently empty
evidence file is exactly the outcome this phase exists to prevent. Failures are
counted, the last one is kept, and ``health()`` publishes both.

Order of work, and why: quotes are noted into the premium-history ring FIRST, so
entry quality on the next tick has real history rather than starting cold; the
observation is scored before it is written, so the stored row and the board row
can never disagree; and the tracker/paper layers advance before new legs open, so
a resolution and an entry in the same tick cannot double-count the budget.
"""
from __future__ import annotations

import threading
import time

from app.config import settings
from app.models import Candle, Decision, IndicatorSnapshot, OptionQuote
from app.research.phase17 import (
    aplus,
    capture,
    economics,
    entry as entry_mod,
    futures as fut_mod,
    mcx,
    paper,
    quality,
    reach,
    schema,
    store,
    tracker,
)
from app.research.phase45 import service as p45
from app.research.phase50 import tiers as p50tiers

_LOCK = threading.Lock()
_STATE: dict[str, object] = {
    "observations": 0,
    "written": 0,
    "failures": 0,
    "last_error": None,
    "last_error_ts": None,
    "last_observation_ts": None,
    "skipped_ineligible": 0,
    "skipped_sampled": 0,
}
# Last capture time per instrument, for the §77 MCX sampling tiers only.
_LAST_CAPTURE: dict[str, float] = {}
# Today's observations, kept in memory for the board and the evidence panel.
# Bounded, because a full session of ticks would otherwise grow without limit.
_TODAY: dict[str, schema.Observation] = {}
_SESSION: str | None = None
# Per instrument, not a flat total: with a wide universe one flat cap would let
# the equities push the morning's index rows off the board, which reads as "the
# index was never captured". The file on disk keeps everything either way.
_MAX_TODAY_PER_INSTRUMENT = 80
_MAX_TODAY_FLOOR = 400


def _max_today() -> int:
    return max(_MAX_TODAY_FLOOR,
               _MAX_TODAY_PER_INSTRUMENT * len(capture.universe()))


_HEARTBEAT_STOP = threading.Event()
_HEARTBEAT: threading.Thread | None = None
# Half the journal's rate limit, so a scheduling hiccup cannot cost a minute's
# line and turn a live process into apparent downtime.
_HEARTBEAT_TICK_SEC = store.PROCESS_INTERVAL_SEC / 2.0


def _heartbeat_loop() -> None:
    """Write the process-liveness line until asked to stop. Never raises.

    Deliberately its own thread and not part of the tick: the whole value of the
    line is that it keeps being written when the capture loop has stopped
    writing its own, which is the difference between "restart supervision would
    have recovered these minutes" and "restarting fixes nothing".
    """
    while not _HEARTBEAT_STOP.wait(_HEARTBEAT_TICK_SEC):
        try:
            store.note_process_alive()
            # Same thread, and for the same reason: it is still running when
            # the tick path has wedged. "The tick path did not run" was as far
            # as the process journal could take a stall, and it named no
            # instrument and no line of code to go and look at.
            store.note_stall()
        except Exception as exc:  # a diagnostic must not kill its own thread
            _note_failure(exc)


def start_process_heartbeat() -> bool:
    """Start the process-liveness thread once. Returns whether it is running."""
    global _HEARTBEAT
    if not settings.phase17_capture:
        return False
    with _LOCK:
        if _HEARTBEAT is not None and _HEARTBEAT.is_alive():
            return True
        _HEARTBEAT_STOP.clear()
        _HEARTBEAT = threading.Thread(
            target=_heartbeat_loop, name="phase17-process-liveness", daemon=True)
        _HEARTBEAT.start()
    store.note_process_alive()
    return True


def stop_process_heartbeat() -> None:
    """Test and shutdown seam. Leaves the journal alone."""
    global _HEARTBEAT
    _HEARTBEAT_STOP.set()
    with _LOCK:
        thread, _HEARTBEAT = _HEARTBEAT, None
    if thread is not None:
        thread.join(timeout=2.0)


def _note_failure(exc: Exception) -> None:
    with _LOCK:
        _STATE["failures"] = int(_STATE["failures"]) + 1
        _STATE["last_error"] = f"{type(exc).__name__}: {exc}"
        _STATE["last_error_ts"] = time.time()


def _roll_session(session: str) -> None:
    global _SESSION
    if _SESSION != session:
        _SESSION = session
        _TODAY.clear()


def _futures_reference(
    obs: schema.Observation, bars: list[Candle],
) -> float | None:
    """The price the MCX research geometry is measured from.

    The futures book's own last trade first, because that is the instrument the
    geometry is in — the candles are futures bars and a stop in futures points
    has to hang off a futures price. The option row's recorded underlying and
    the last bar close follow as fallbacks; if none of the three is available
    the plan refuses rather than anchoring to a number from another series.
    """
    if obs.futures is not None and isinstance(obs.futures.premium, (int, float)):
        if float(obs.futures.premium) > 0:
            return float(obs.futures.premium)
    for q in (obs.selected, obs.opposite):
        if q is not None and isinstance(q.underlying_price, (int, float)):
            if float(q.underlying_price) > 0:
                return float(q.underlying_price)
    if bars and isinstance(bars[-1].close, (int, float)) and bars[-1].close > 0:
        return float(bars[-1].close)
    return None


def enrich(
    obs: schema.Observation,
    *,
    candles: list[Candle] | None = None,
) -> schema.Observation:
    """Attach economics, entry quality, reachability and the A+ score.

    Applied to BOTH sides' economics: the opposite side's spread is what makes
    §6 a comparison rather than an assertion, and it is free — the quote is
    already captured.

    ``candles`` are the instrument's own futures/commodity bars, and they are
    what the MCX research layer measures: with them an MCX row gets a
    continuation-basis target (§3) and a continuation-basis market edge (§4)
    instead of the two blanks that made 95% of commodity rows ungradeable.
    Without them the row is unchanged — nothing here invents a level.
    """
    obs.economics = economics.assess(obs.selected, obs.plan)
    if obs.opposite is not None:
        obs.economics["opposite"] = economics.assess(obs.opposite, obs.plan)
    obs.entry = entry_mod.assess(obs.selected, obs.plan)
    obs.reach = reach.assess(obs.selected, obs.plan, obs.economics)

    bars = list(candles or [])
    edge: tuple[float | None, str] | None = None
    if mcx.tier(obs.instrument) is not None:
        obs.mcx_plan = mcx.plan(
            obs.instrument, _futures_reference(obs, bars), bars,
            obs.direction or "",
        )
        edge = mcx.market_edge(obs.instrument, bars, obs.direction or "")
    obs.aplus = aplus.score(obs, market_edge_override=edge)
    # After the A+ score, because the futures geometry may borrow the option
    # plan's levels and must never be the thing that sets them.
    obs.futures_plan = fut_mod.geometry(obs, mcx_plan=obs.mcx_plan)
    return obs


def observe(
    instrument: str,
    dec: Decision,
    chain: list[OptionQuote],
    *,
    spot: float | None,
    ind: IndicatorSnapshot | None = None,
    status: str | None = None,
    candles: list[Candle] | None = None,
    signal_ts: float | None = None,
    expiry: str | None = None,
    days_to_expiry: int | None = None,
    source: str = quality.UNKNOWN_SOURCE,
    quote_age_ms: float | None = None,
    futures_book: dict | None = None,
) -> str | None:
    """Record one candidate. Returns its observation id, or ``None``.

    ``signal_ts`` should be the timestamp the decision was computed at. Passing
    it explicitly rather than reading the clock here is what makes
    ``signal_to_snapshot_ms`` a measurement instead of a formality.

    ``futures_book`` is the contract's two-sided book AS QUOTED AT THIS SIGNAL,
    from :meth:`app.market.provider.MarketDataProvider.futures_book`. It is a
    parameter rather than something fetched here for the reason the whole of §1
    exists: the futures leg has to be the same instant as the option legs, and
    the only place that instant is available is the caller that already holds
    the chain. A book fetched a tick later would describe a different market.
    """
    if not settings.phase17_capture:
        return None
    # Before eligibility, sampling and the build, all of which can refuse the
    # row: the question a coverage hole raises is whether the tick path ran at
    # all, and only a line written unconditionally answers it. Rate-limited to
    # one a minute, so a wide universe costs one line per minute, not per tick.
    store.note_liveness(instrument)
    if not capture.eligible(instrument):
        with _LOCK:
            _STATE["skipped_ineligible"] = int(_STATE["skipped_ineligible"]) + 1
        store.note_gap(instrument, store.NOT_IN_UNIVERSE)
        return None
    # §77: CRUDEOIL every tick, the other four MCX names sampled. They stay in
    # the universe — a single day of vehicle evidence is not grounds for
    # removing an instrument — but they no longer write a row per tick each.
    #
    # The sampled gate is now the Phase 50 research tier: 15 seconds instead of
    # 60, because the 60-second one is what left 47 of 86 events unable to
    # answer a 15-second observation grid. Same signature, same inputs — the
    # instrument, the clock and the last capture instant, never the overlay
    # state — and reverting the flag restores the previous gate. This is the
    # research recorder; no production scanner, signal or order path reads it.
    with _LOCK:
        last = _LAST_CAPTURE.get((instrument or "").upper())
    if not p50tiers.due(instrument, now=time.time(), last_capture_ts=last):
        with _LOCK:
            _STATE["skipped_sampled"] = int(_STATE["skipped_sampled"]) + 1
        store.note_gap(instrument, store.SAMPLED_TIER)
        return None
    try:
        now = time.time()
        effective_signal_ts = signal_ts if signal_ts is not None else now
        obs = capture.build(
            instrument, dec, chain,
            spot=spot, ind=ind, status=status, candles=candles,
            signal_ts=effective_signal_ts,
            capture_ts=now,
            expiry=expiry, days_to_expiry=days_to_expiry,
            source=source, quote_age_ms=quote_age_ms,
            futures_quote=fut_mod.quote(
                instrument, futures_book,
                signal_ts=effective_signal_ts,
                capture_ts=now,
                source=source,
            ),
            window_steps=int(settings.phase17_window_steps),
        )
        if obs is None:
            # The tick ran and produced nothing. Which of the two reasons it
            # was matters: an empty chain is a feed problem, a chain with no
            # usable contract is a strike-window problem, and the coverage
            # hole they leave behind is identical.
            store.note_gap(
                instrument,
                store.NO_OPTION_CHAIN if not chain else store.NO_CONTRACT,
            )
            return None
        _roll_session(obs.session or "")
        # Which cadence this session was captured under, journalled once per
        # session. The fresh-sample rule needs the boundary in the data: a
        # comparison counts sessions carrying the new fingerprint rather than
        # assuming the sessions it holds share one policy.
        p50tiers.note_session(obs.session or "", now=now)

        # Premium history first — see the module docstring.
        entry_mod.note_chain([obs.selected] if obs.selected else [], ts=obs.capture_ts)
        if obs.opposite is not None:
            entry_mod.note_chain([obs.opposite], ts=obs.capture_ts)
        entry_mod.note_chain(obs.window, ts=obs.capture_ts)

        # Advance what is already open before anything new is admitted.
        for row in tracker.update(
            instrument, chain, now=now, underlying=spot,
            quality_label=obs.data_quality,
            futures_book=futures_book,
        ):
            store.write_leg(row)
        quotes = list(obs.window)
        if obs.selected is not None:
            quotes.append(obs.selected)
        if obs.opposite is not None:
            quotes.append(obs.opposite)
        paper.advance(instrument, quotes, now=now)

        enrich(obs, candles=candles)
        obs.tracking = tracker.open_legs(obs, now=now)
        paper.consider(obs, now=now)

        with _LOCK:
            _STATE["observations"] = int(_STATE["observations"]) + 1
            _STATE["last_observation_ts"] = now
            _LAST_CAPTURE[obs.instrument] = now
            if len(_TODAY) >= _max_today():
                # Drop the oldest rather than the newest: the board is about now.
                oldest = min(_TODAY.values(), key=lambda o: o.signal_ts)
                _TODAY.pop(oldest.observation_id, None)
            _TODAY[obs.observation_id] = obs
        if store.write_observation(obs.as_dict()):
            with _LOCK:
                _STATE["written"] = int(_STATE["written"]) + 1
        store.note_coverage(instrument, now=now)
        # Last, after the raw row is on disk and every production side effect
        # has already happened, so the shadow board reads evidence it cannot
        # have altered. It returns counts, never a decision, and never raises.
        p45.observe(obs)
        return obs.observation_id
    except Exception as exc:  # research capture must never break a tick
        _note_failure(exc)
        try:
            store.note_gap(instrument, store.CAPTURE_RAISED)
        except Exception:  # the diagnostic must not become the failure
            pass
        return None


def today() -> list[schema.Observation]:
    with _LOCK:
        return sorted(_TODAY.values(), key=lambda o: -o.signal_ts)


def board() -> dict:
    """§21 A+ board over today's observations."""
    return aplus.board(
        today(),
        preferred=int(settings.phase17_board_preferred),
        watch=int(settings.phase17_board_watch),
    )


def evidence(limit: int = 60) -> dict:
    """§25 Vehicle Evidence panel: every measurable candidate, A+ or not."""
    rows: list[dict] = []
    for obs in today()[: max(1, limit)]:
        econ = obs.economics or {}
        opp_econ = econ.get("opposite") or {}
        sel, opp = obs.selected, obs.opposite
        rows.append({
            "observation_id": obs.observation_id,
            "signal_ts": obs.signal_ts,
            "instrument": obs.instrument,
            "candidate_class": obs.candidate_class,
            "direction": obs.direction,
            "market_signal": obs.context.market_signal,
            "regime": obs.context.regime,
            "htf_alignment": obs.context.htf_alignment,
            "selected_vehicle": obs.selected_vehicle,
            "ce_pe": {
                "selected": {
                    "symbol": sel.symbol if sel else None,
                    "vehicle": sel.vehicle if sel else None,
                    "strike": sel.strike if sel else None,
                    "premium": sel.premium if sel else None,
                    "bid": sel.bid if sel else None,
                    "ask": sel.ask if sel else None,
                    "spread": sel.spread if sel else None,
                    "spread_pct": sel.spread_pct if sel else None,
                    "oi": sel.oi if sel else None,
                    "volume": sel.volume if sel else None,
                    "iv": sel.iv if sel else None,
                    "delta": sel.delta if sel else None,
                    "moneyness": sel.moneyness if sel else None,
                    "vehicle_class": econ.get("vehicle_class"),
                    "cost_points": econ.get("cost_points"),
                    "data_quality": sel.data_quality if sel else quality.MISSING,
                },
                "opposite": {
                    "symbol": opp.symbol if opp else None,
                    "vehicle": opp.vehicle if opp else None,
                    "strike": opp.strike if opp else None,
                    "premium": opp.premium if opp else None,
                    "bid": opp.bid if opp else None,
                    "ask": opp.ask if opp else None,
                    "spread": opp.spread if opp else None,
                    "spread_pct": opp.spread_pct if opp else None,
                    "oi": opp.oi if opp else None,
                    "volume": opp.volume if opp else None,
                    "iv": opp.iv if opp else None,
                    "delta": opp.delta if opp else None,
                    "moneyness": opp.moneyness if opp else None,
                    "vehicle_class": opp_econ.get("vehicle_class"),
                    "cost_points": opp_econ.get("cost_points"),
                    "data_quality": opp.data_quality if opp else quality.MISSING,
                },
            },
            "futures": obs.futures.as_dict() if obs.futures else None,
            "futures_plan": obs.futures_plan,
            "futures_capture": (obs.futures_plan or {}).get(
                "capture", fut_mod.MISSING
            ),
            "mcx_plan": obs.mcx_plan,
            "room": {
                "t1_score": (obs.reach or {}).get("t1_score"),
                "t1_rank": (obs.reach or {}).get("t1_rank"),
                "not_a_probability": True,
                "underlying_move_required": (obs.reach or {}).get(
                    "underlying_move_required"
                ),
                "room_after_spread": econ.get("room_after_spread"),
            },
            "entry_quality": (obs.entry or {}).get("entry_quality"),
            "a_plus_score": (obs.aplus or {}).get("a_plus_score"),
            "a_plus_label": (obs.aplus or {}).get("a_plus_label"),
            "both_sides": obs.both_sides,
            "data_quality": obs.data_quality,
            "tracking": obs.tracking,
        })
    return {
        "rows": rows,
        "counts": quality.tally([r["data_quality"] for r in rows]),
        # Separate from ``counts``: option data quality and futures capture are
        # different failures and the 1 Sep report hid the second inside the
        # first, reading 100% EXACT while the futures column was empty.
        "futures_capture": fut_mod.tally(
            [str(r["futures_capture"]) for r in rows]
        ),
        "note": (
            "Every eligible candidate, A+ or not. A+ is a label over this feed, "
            "not the filter that produced it."
        ),
        "research_only": True,
    }


def health() -> dict:
    with _LOCK:
        state = dict(_STATE)
    state.update({
        "enabled": bool(settings.phase17_capture),
        "universe": list(capture.universe()),
        "mcx_tiers": {
            "deep": sorted(mcx.DEEP_CAPTURE),
            "sampled": sorted(mcx.SAMPLED_CAPTURE),
            # What the gate actually is now, beside the number it used to be, so
            # a reader of the health payload can tell which cadence a running
            # process is capturing under without reading the config.
            "sample_interval_sec": p50tiers.slow_tier_sec(),
            "previous_sample_interval_sec": mcx.SAMPLE_INTERVAL_SEC,
            "basis": mcx.BASIS,
        },
        "tier_cadence": p50tiers.policy(),
        "today_rows": len(_TODAY),
        "session": _SESSION,
        "tracker": tracker.health(),
        "paper": paper.health(),
        "entry_history": entry_mod.health(),
        "store": store.health(),
    })
    return state


def reset() -> None:
    """Test seam. Clears in-memory state only."""
    global _SESSION
    with _LOCK:
        _TODAY.clear()
        _LAST_CAPTURE.clear()
        _SESSION = None
        _STATE.update({
            "observations": 0,
            "written": 0,
            "failures": 0,
            "last_error": None,
            "last_error_ts": None,
            "last_observation_ts": None,
            "skipped_ineligible": 0,
            "skipped_sampled": 0,
        })
    tracker.reset()
    paper.reset()
    entry_mod.reset()
