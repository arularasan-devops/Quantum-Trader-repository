"""Futures paper book — Phase 19 §6, PAPER ONLY.

Why this exists when ``futures_outcomes`` already follows futures plans: that
module records what the *plan* did from its stated entry, with one aggregate cost
number and no execution model. It answers "was the level reached". It cannot
answer "would the trade have paid", because it never charges an executable side
and never separates the spread from the statutory charges from the slippage.

This book does. One row per resolved futures paper trade, with:

* an **executable entry**: the trade crosses to the offer to go long and to the
  bid to go short. The futures feed publishes candles, not depth, so where no
  book exists the crossing is *modelled* from the configured slippage and the row
  says ``EXEC_MODELLED`` — it is never called a measured fill;
* costs decomposed (brokerage / statutory / txn / spread / slippage) via
  :mod:`app.research.phase19.futcosts`;
* MFE, MAE, give-back, T1/T2/T3, time-to-each, hold bucket, gross and NET R.

Hard limits. There is no order route of any kind here, paper or live. Nothing in
this module places, sizes for, modifies or proposes a trade, nothing production
reads its output, and it can only ever be reached from the research observer.
A failure inside it is swallowed by the caller so a research book can never break
a live tick.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models import FuturesSignalCard
from app.research.phase19 import futcosts

VALID = "VALID_FUTURES_PLAN"
LONG, SHORT = "LONG", "SHORT"

LOG_NAME = "phase19_futures_paper.jsonl"
OPEN_NAME = "phase19_futures_open.json"

T1, T2, T3 = "T1", "T2", "T3"
STOP = "STOP"
TIMEOUT = "TIMEOUT"
SESSION_END = "SESSION_END"
OUTCOMES = (T1, T2, T3, STOP, TIMEOUT, SESSION_END)

# Provenance of the entry fill. A modelled crossing is research evidence about
# the plan; only a booked crossing is evidence about the fill.
EXEC_BOOK = "EXEC_BOOK"
EXEC_MODELLED = "EXEC_MODELLED"

STAGE_PLAN = "VALID_FUTURES_PLAN"
STAGE_ELIGIBLE = "PAPER_ELIGIBLE"
STAGE_ENTRY = "PAPER_ENTRY"
STAGE_EXIT = "PAPER_EXIT"
STAGE_RESOLVED = "RESOLVED"
STAGES = (STAGE_PLAN, STAGE_ELIGIBLE, STAGE_ENTRY, STAGE_EXIT, STAGE_RESOLVED)

# Refusal reasons, so a session with no futures paper trades says why instead of
# reading as "the feed was down".
SKIP_DISABLED = "PAPER_DISABLED"
SKIP_NOT_VALID = "NO_VALID_PLAN"
SKIP_NO_RISK = "RISK_NOT_POSITIVE"
SKIP_STALE = "FEED_STALE"
SKIP_ALREADY_OPEN = "ALREADY_OPEN"
SKIP_NO_LOT = "LOT_SIZE_UNKNOWN"
SKIP_PLAN_DONE = "PLAN_ALREADY_RESOLVED"

# A plan whose feed is older than this is not entered: an entry priced off a
# minute-old candle is a backtest, not a paper fill.
MAX_FEED_AGE_SEC = 90.0
# Nothing is held past this. A futures paper trade that has neither targeted nor
# stopped in four hours is resolved TIMEOUT so the sample cannot be inflated by
# rows that never conclude.
MAX_HOLD_MINUTES = 240.0

HOLD_BUCKETS = ("<5", "5-15", "15-30", "30-60", "60-120", "120+")

_LOCK = threading.Lock()
_OPEN: dict[str, dict] = {}
# Plans already traded to a resolution, per instrument and session. A valid plan
# persists across ticks, so without this a stopped trade re-enters on the very
# next tick and one plan becomes a dozen correlated rows — the fastest way to
# manufacture a sample that looks large and means nothing.
_DONE: dict[str, set[str]] = {}
_RESOLVED: list[dict] = []
_STATE: dict[str, object] = {
    "plans_seen": 0,
    "entries": 0,
    "exits": 0,
    "resolved": 0,
    "skipped": {},
    "last_error": None,
}
_LOADED = False


def hold_bucket(minutes: float | None) -> str | None:
    if not isinstance(minutes, (int, float)):
        return None
    m = float(minutes)
    if m < 5:
        return "<5"
    if m < 15:
        return "5-15"
    if m < 30:
        return "15-30"
    if m < 60:
        return "30-60"
    if m < 120:
        return "60-120"
    return "120+"


def _ist(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone(timedelta(hours=5, minutes=30))).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _session(ts: float) -> str:
    return _ist(ts)[:10]


def log_path() -> str:
    return os.path.join(settings.data_dir, LOG_NAME)


def _open_path() -> str:
    return os.path.join(settings.data_dir, OPEN_NAME)


def _note_skip(reason: str) -> None:
    skipped = _STATE["skipped"]
    if isinstance(skipped, dict):
        skipped[reason] = int(skipped.get(reason, 0)) + 1


def _append(row: dict) -> None:
    try:
        os.makedirs(settings.data_dir, exist_ok=True)
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except OSError as exc:
        _STATE["last_error"] = f"{type(exc).__name__}: {exc}"


def _save_open() -> None:
    try:
        os.makedirs(settings.data_dir, exist_ok=True)
        with open(_open_path(), "w", encoding="utf-8") as fh:
            json.dump(list(_OPEN.values()), fh, default=str)
    except OSError as exc:
        _STATE["last_error"] = f"{type(exc).__name__}: {exc}"


def _load_open() -> None:
    """Reload positions that were mid-trade when the process stopped.

    Without this a restart silently drops exactly the trades that were running,
    which biases the sample toward short ones.
    """
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        with open(_open_path(), encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return
    if not isinstance(raw, list):
        return
    for rec in raw:
        if isinstance(rec, dict) and isinstance(rec.get("instrument"), str):
            _OPEN[rec["instrument"]] = rec


def _signed(direction: str, points: float) -> float:
    return points if direction == LONG else -points


def _cross(direction: str, price: float, spread: float | None,
           slippage: float) -> tuple[float, str]:
    """Executable entry price and its provenance.

    Long crosses to the offer, short crosses to the bid. With a recorded book the
    crossing is half the spread; without one it is the configured slippage and is
    labelled modelled, never measured.
    """
    if isinstance(spread, (int, float)) and float(spread) > 0:
        half = float(spread) / 2.0
        return (price + half if direction == LONG else price - half), EXEC_BOOK
    slip = max(0.0, float(slippage))
    return (price + slip if direction == LONG else price - slip), EXEC_MODELLED


def _plan_key(card: FuturesSignalCard) -> str:
    return "|".join(
        str(v) for v in (card.direction, card.entry, card.stop, card.target1)
    )


def observe(
    instrument: str,
    card: FuturesSignalCard | None,
    price: float | None,
    *,
    now: float | None = None,
    feed_age_sec: float | None = None,
) -> None:
    """Step any open paper trade, then open one if a new valid plan is present.

    Best-effort. Raises nothing the caller has to handle: the research observer
    that calls it must never be able to break a tick.
    """
    ts = time.time() if now is None else float(now)
    with _LOCK:
        _load_open()
        try:
            _step(instrument, price, ts)
            _maybe_enter(instrument, card, price, ts, feed_age_sec)
        except (KeyError, TypeError, ValueError) as exc:
            _STATE["last_error"] = f"{type(exc).__name__}: {exc}"


def _maybe_enter(instrument: str, card: FuturesSignalCard | None,
                 price: float | None, now: float,
                 feed_age_sec: float | None) -> None:
    if not settings.phase19_futures_paper:
        _note_skip(SKIP_DISABLED)
        return
    if card is None or card.status != VALID or card.direction not in (LONG, SHORT):
        _note_skip(SKIP_NOT_VALID)
        return
    _STATE["plans_seen"] = int(_STATE["plans_seen"]) + 1
    if card.entry is None or card.stop is None:
        _note_skip(SKIP_NOT_VALID)
        return
    risk = abs(float(card.entry) - float(card.stop))
    if risk <= 0:
        # A stop at the entry divides by zero and produces the 1e8 R rows an
        # earlier study had to throw away. Refused, not clamped.
        _note_skip(SKIP_NO_RISK)
        return
    if not isinstance(card.lot_size, int) or card.lot_size <= 0:
        _note_skip(SKIP_NO_LOT)
        return
    if isinstance(feed_age_sec, (int, float)) and float(feed_age_sec) > MAX_FEED_AGE_SEC:
        _note_skip(SKIP_STALE)
        return
    key = _plan_key(card)
    if key in _DONE.get(f"{instrument}|{_session(now)}", set()):
        _note_skip(SKIP_PLAN_DONE)
        return
    if instrument in _OPEN:
        existing = _OPEN[instrument]
        if existing.get("plan_key") != key:
            existing["restatements"] = int(existing.get("restatements", 0)) + 1
        _note_skip(SKIP_ALREADY_OPEN)
        return

    mark = float(price) if isinstance(price, (int, float)) and price else float(card.entry)
    fill, exec_basis = _cross(
        card.direction, mark, card.spread_points, settings.futures_slippage_points
    )
    st = {
        "instrument": instrument,
        "session": _session(now),
        "signal_id": card.futures_signal_id or f"{instrument}-FUT-{int(now * 1000)}",
        "episode_id": card.episode_id,
        "plan_key": key,
        "contract": card.contract,
        "expiry": card.expiry,
        "days_to_expiry": card.days_to_expiry,
        "rolled": bool(card.rolled),
        "lot_size": int(card.lot_size),
        "lots": 1,
        "direction": card.direction,
        "plan_entry": float(card.entry),
        "entry": round(fill, 4),
        "exec_basis": exec_basis,
        "mark_at_entry": round(mark, 4),
        "stop": float(card.stop),
        "risk_points": round(risk, 4),
        "targets": {T1: card.target1, T2: card.target2, T3: card.target3},
        "spread_points": card.spread_points,
        "signal_score": card.signal_score,
        "setup_type": card.setup_type,
        "regime": card.regime,
        "atr": card.atr,
        "entry_ts": now,
        "entry_time_ist": _ist(now),
        # Kept so a resolved row can prove its own freshness after a restart:
        # the in-memory refusal counters do not survive the process.
        "entry_feed_age_sec": (
            round(float(feed_age_sec), 3)
            if isinstance(feed_age_sec, (int, float)) else None
        ),
        "best": 0.0,
        "worst": 0.0,
        "best_ts": now,
        "hit": {},
        "restatements": 0,
        "lifecycle": [
            {"stage": s, "ts": int(now), "time_ist": _ist(now)}
            for s in (STAGE_PLAN, STAGE_ELIGIBLE, STAGE_ENTRY)
        ],
    }
    _OPEN[instrument] = st
    _STATE["entries"] = int(_STATE["entries"]) + 1
    _save_open()


def _step(instrument: str, price: float | None, now: float) -> None:
    st = _OPEN.get(instrument)
    if st is None:
        return
    if not isinstance(price, (int, float)) or not price:
        return
    px = float(price)
    direction = str(st["direction"])
    move = _signed(direction, px - float(st["entry"]))
    if move > float(st["best"]):
        st["best"] = round(move, 4)
        st["best_ts"] = now
    if move < float(st["worst"]):
        st["worst"] = round(move, 4)

    hit = st["hit"]
    if isinstance(hit, dict):
        for name in (T1, T2, T3):
            target = st["targets"].get(name)
            if name in hit or not isinstance(target, (int, float)):
                continue
            reached = px >= float(target) if direction == LONG else px <= float(target)
            if reached:
                hit[name] = {"ts": now}

        stopped = (
            px <= float(st["stop"]) if direction == LONG else px >= float(st["stop"])
        )
        if stopped:
            _resolve(instrument, px, now, STOP)
            return
        if T3 in hit:
            _resolve(instrument, px, now, T3)
            return

    held = (now - float(st["entry_ts"])) / 60.0
    if held >= MAX_HOLD_MINUTES:
        _resolve(instrument, px, now, TIMEOUT)


def close_session(instrument: str, price: float | None, now: float | None = None) -> None:
    """Resolve an open trade at the session close. Called by the observer."""
    ts = time.time() if now is None else float(now)
    with _LOCK:
        _load_open()
        st = _OPEN.get(instrument)
        if st is None:
            return
        px = float(price) if isinstance(price, (int, float)) and price else float(st["entry"])
        _resolve(instrument, px, ts, SESSION_END)


def _resolve(instrument: str, price: float, now: float, outcome: str) -> None:
    st = _OPEN.pop(instrument, None)
    if st is None:
        return
    direction = str(st["direction"])
    entry = float(st["entry"])
    risk = float(st["risk_points"])
    hit = st["hit"] if isinstance(st["hit"], dict) else {}

    # The exit crosses back: a long sells into the bid, a short buys the offer.
    exit_px, exit_basis = _cross(
        SHORT if direction == LONG else LONG,
        price,
        st.get("spread_points"),
        settings.futures_slippage_points,
    )
    gross = _signed(direction, exit_px - entry)
    costs = futcosts.round_trip(
        instrument,
        entry=entry,
        exit_price=exit_px,
        lot_size=int(st["lot_size"]),
        lots=int(st["lots"]),
        spread_points=st.get("spread_points"),
        # The crossing above already charged the configured slippage on both
        # legs; charging it again in the cost model would double-count it.
        slippage_points=0.0,
    )
    cost_points = costs["cost_points"]
    net = None if cost_points is None else gross - float(cost_points)
    mfe = float(st["best"])
    mae = float(st["worst"])
    hold = (now - float(st["entry_ts"])) / 60.0

    def mins(name: str) -> float | None:
        rec = hit.get(name)
        return round((float(rec["ts"]) - float(st["entry_ts"])) / 60.0, 3) if rec else None

    lifecycle = list(st["lifecycle"]) + [
        {"stage": s, "ts": int(now), "time_ist": _ist(now)}
        for s in (STAGE_EXIT, STAGE_RESOLVED)
    ]
    row = {
        "vehicle": "FUTURES",
        "strategy": "FUTURES_A_PLUS",
        "paper_only": True,
        "no_real_order": True,
        "instrument": instrument,
        "session": st["session"],
        "signal_id": st["signal_id"],
        "episode_id": st["episode_id"],
        "contract": st["contract"],
        "expiry": st["expiry"],
        "days_to_expiry": st["days_to_expiry"],
        "rolled": st["rolled"],
        "direction": direction,
        "lot_size": st["lot_size"],
        "lots": st["lots"],
        "plan_entry": st["plan_entry"],
        "entry_price": round(entry, 4),
        "entry_basis": st["exec_basis"],
        "exit_basis": exit_basis,
        "exec_basis": st["exec_basis"],
        "entry_ts": int(st["entry_ts"]),
        "entry_time_ist": st["entry_time_ist"],
        "entry_feed_age_sec": st.get("entry_feed_age_sec"),
        "max_feed_age_sec": MAX_FEED_AGE_SEC,
        "exit_ts": int(now),
        "exit_time_ist": _ist(now),
        "exit_price": round(exit_px, 4),
        "mark_at_exit": round(price, 4),
        "outcome": outcome,
        "exit_reason": outcome,
        "stop": st["stop"],
        "target1": st["targets"].get(T1),
        "target2": st["targets"].get(T2),
        "target3": st["targets"].get(T3),
        "risk_points": risk,
        "gross_points": round(gross, 4),
        "net_points": None if net is None else round(net, 4),
        "gross_r": round(gross / risk, 4) if risk else None,
        "net_r": None if net is None or not risk else round(net / risk, 4),
        "gross_rupees": round(gross * st["lot_size"] * st["lots"], 2),
        "net_rupees": (
            None if net is None else round(net * st["lot_size"] * st["lots"], 2)
        ),
        "mfe_points": mfe,
        "mae_points": mae,
        "mfe_r": round(mfe / risk, 4) if risk else None,
        "mae_r": round(mae / risk, 4) if risk else None,
        "mfe_capture_pct": (
            round(100.0 * gross / mfe, 2) if mfe > 0 else None
        ),
        "giveback_points": round(mfe - gross, 4),
        "reached_t1": T1 in hit,
        "reached_t2": T2 in hit,
        "reached_t3": T3 in hit,
        "t1_before_stop": T1 in hit and outcome != STOP,
        "reached_t1_then_reversed": T1 in hit and outcome in (STOP, TIMEOUT, SESSION_END),
        "minutes_to_t1": mins(T1),
        "minutes_to_t2": mins(T2),
        "minutes_to_t3": mins(T3),
        "minutes_to_mfe": round((float(st["best_ts"]) - float(st["entry_ts"])) / 60.0, 3),
        "hold_minutes": round(hold, 3),
        "hold_bucket": hold_bucket(hold),
        "restatements": st["restatements"],
        "signal_score": st["signal_score"],
        "setup_type": st["setup_type"],
        "regime": st["regime"],
        "atr": st["atr"],
        "lifecycle": lifecycle,
        "cost_points": cost_points,
        "cost_rupees": costs["cost_rupees"],
        "brokerage_rupees": costs["brokerage_rupees"],
        "tax_rupees": costs["tax_rupees"],
        "txn_rupees": costs["txn_rupees"],
        "spread_cost_points": costs["spread_points"],
        "slippage_points": round(2.0 * max(0.0, settings.futures_slippage_points), 4),
        "cost_status": costs["cost_status"],
        "spread_source": costs["spread_source"],
        "cost_note": costs["note"],
    }
    _DONE.setdefault(f"{instrument}|{st['session']}", set()).add(str(st["plan_key"]))
    _RESOLVED.append(row)
    if len(_RESOLVED) > 2000:
        del _RESOLVED[:-2000]
    _STATE["exits"] = int(_STATE["exits"]) + 1
    _STATE["resolved"] = int(_STATE["resolved"]) + 1
    _append(row)
    _save_open()


def open_positions() -> list[dict]:
    with _LOCK:
        _load_open()
        return [dict(v) for v in _OPEN.values()]


def resolved(limit: int | None = None) -> list[dict]:
    with _LOCK:
        rows = list(_RESOLVED)
    return rows if limit is None else rows[-limit:]


def read_log(limit: int | None = None) -> list[dict]:
    """Resolved rows from disk — what the report reads, across restarts."""
    out: list[dict] = []
    try:
        with open(log_path(), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return []
    return out if limit is None else out[-limit:]


def health() -> dict:
    with _LOCK:
        state = dict(_STATE)
        state["skipped"] = dict(_STATE["skipped"]) if isinstance(
            _STATE["skipped"], dict) else {}
        state["open"] = len(_OPEN)
    state.update({
        "enabled": bool(settings.phase19_futures_paper),
        "log": LOG_NAME,
        "paper_only": True,
        "no_real_order": True,
        "max_hold_minutes": MAX_HOLD_MINUTES,
        "max_feed_age_sec": MAX_FEED_AGE_SEC,
    })
    return state


def reset_for_tests() -> None:
    global _LOADED
    with _LOCK:
        _OPEN.clear()
        _RESOLVED.clear()
        _DONE.clear()
        _LOADED = True  # do not reload disk state into a test
        _STATE.update({
            "plans_seen": 0,
            "entries": 0,
            "exits": 0,
            "resolved": 0,
            "skipped": {},
            "last_error": None,
        })
