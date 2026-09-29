"""Shadow book — an UNGATED paper ledger of every Signal-board call.

The complaint this answers is "the board says BUY and the auto book takes
nothing". The gates that refuse those calls are not bugs, they are refusals with
reasons (reward:risk, no entry trigger, risky regime, spread wider than the stop,
cooldown, concurrency, trade cap). Whether they cost money or saved it is a
measurable question, and the honest way to settle it is to run the ungated book
beside the gated one and compare, not to switch the gates off and find out with
real capital.

So this module takes EVERY board call that carried a plan — the contract, the
premium, the stop and the first target — including every call the gated path
refused, records the blocker it would have died on, and follows it to an outcome
in its own ledger.

**Isolation, and why each piece of it matters**

* No order path. This module imports no broker, no provider, no order function.
  It cannot place, size, veto or delay a real or gated-paper position.
* Its own capital pot (``shadow_book_capital``), so an ungated book that takes
  fifty positions never competes for the option bot's money.
* Its own file (``shadow_book.jsonl``), append-only. The gated book's journal,
  the paper-trade table and the account equity are untouched by everything here.
* Nothing in the trading path reads it back. It is written after the decision is
  final and after the gated auto-trader has already had its turn.

**Ungated does not mean unbounded.** Three sanity limits stay, because a ledger
that books a thousand lots off a one-rupee stop measures nothing:

* a lot ceiling (``shadow_book_max_lots``),
* affordability against its own pot,
* one open shadow position per leg at a time — a repeat call on a leg already
  held is recorded as a REPEAT line rather than laddered into a second entry, so
  no call is lost and the ledger is not one entry per tick.

**Two prices, deliberately.** Entry is recorded at the mid and at the ask; exit
at the mid and at the bid. Mid-to-mid is what the other paper books report, and
on the recorded data it was the difference between an apparent profit and a real
loss — GOLD gained ~8 premium points per leg against a 250-point book. The
ledger therefore carries ``gross_rupees`` (mid) and ``net_rupees`` (crossing the
book) side by side, and neither one is hidden behind the other. Where no bid/ask
was quoted, the costed number is ``None`` — never silently equal to the gross.
"""
from __future__ import annotations

import json
import os
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone

from app.analysis import exec_funnel
from app.analysis import plan_validity
from app.config import settings
from app.market.instruments import REGISTRY
from app.models import Decision, OptionQuote, Signal

LEDGER_LOG = "shadow_book.jsonl"

ENTRY = "SHADOW_ENTRY"
EXIT = "SHADOW_EXIT"
REPEAT = "SHADOW_REPEAT"
SKIPPED = "SHADOW_SKIPPED"

# A board BUY that named a contract and a premium but no stop or target. It has
# no risk, so it cannot be sized or scored in R — but the premium can still be
# followed, so it is watched rather than thrown away. On the recorded data these
# are most of the calls the gated book never took, so counting them without
# measuring them would leave the comparison almost empty.
WATCH = "SHADOW_WATCH"
WATCH_END = "SHADOW_WATCH_END"
NO_LEVELS = "NO_STOP_OR_TARGET"
# The plan exists but cannot be traded as written: the first target is at or
# below the premium the call was printed at, or the stop is at or above it. On
# the recorded data this is the engine holding an entry plan while the premium
# moves away from it. Entering such a plan would resolve as a TARGET the instant
# it is opened, so the ledger follows the premium instead of claiming a win.
NO_UPSIDE = "TARGET_NOT_ABOVE_ENTRY"
STOP_ABOVE_ENTRY = "STOP_NOT_BELOW_ENTRY"

# Why a shadow entry did not happen even here. These are not gates on the call —
# they are the ledger admitting it could not measure it.
NO_PLAN = "NO_PLAN"
NO_RISK = "NON_POSITIVE_RISK"
UNAFFORDABLE = "UNAFFORDABLE_IN_SHADOW_POT"

# Not a gate — the gated book was already holding, so it never considered the
# call. On the recorded data this is the most common reason a board BUY produced
# no gated entry, and no gate records it.
ALREADY_IN_POSITION = "GATED_ALREADY_IN_POSITION"

TARGET_HIT = "TARGET"
STOP_HIT = "STOP"
TIMEOUT = "TIMEOUT"
SESSION_END = "SESSION_END"

_IST = timezone(timedelta(hours=5, minutes=30))
_LOCK = threading.Lock()

# In-memory open set, like the signal journal: a restart leaves those positions
# unresolved in the ledger rather than closed at an invented price.
_open: dict[str, dict] = {}
# Last call recorded per leg, so an unchanged BUY reprinted every few seconds
# becomes one REPEAT line rather than a flood of them.
_REPEAT_SEC = 60.0
_last_repeat: dict[str, float] = {}


def ledger_path() -> str:
    return os.path.join(settings.data_dir, LEDGER_LOG)


def _append(rec: dict) -> None:
    """Best-effort write. A logging failure never raises into a live tick."""
    try:
        os.makedirs(settings.data_dir, exist_ok=True)
        with open(ledger_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass


def _ist(ts: float) -> str:
    return datetime.fromtimestamp(ts, _IST).strftime("%Y-%m-%d %H:%M:%S")


def _session(ts: float) -> str:
    return datetime.fromtimestamp(ts, _IST).strftime("%Y-%m-%d")


def _leg(chain: list[OptionQuote], symbol: str | None) -> OptionQuote | None:
    if not symbol:
        return None
    for quote in chain:
        if quote.symbol == symbol:
            return quote
    return None


def _lot_size(instrument: str) -> int:
    spec = REGISTRY.get(instrument)
    return int(spec.lot_size) if spec and spec.lot_size else 1


def _lots(entry: float, risk: float, lot_size: int) -> tuple[int, str | None]:
    """Lots from risk budget, then clamped by the ceiling and by affordability.

    Returns ``(0, reason)`` when the shadow pot cannot carry even one lot, so an
    unaffordable call is recorded as SKIPPED with a reason instead of appearing
    as a position that never existed.
    """
    budget = settings.shadow_book_capital * settings.shadow_book_risk_per_trade_pct / 100.0
    per_lot_risk = risk * lot_size
    if per_lot_risk <= 0:
        return 0, NO_RISK
    lots = int(budget // per_lot_risk)
    if settings.shadow_book_max_lots > 0:
        lots = min(lots, settings.shadow_book_max_lots)
    cost_per_lot = entry * lot_size
    if cost_per_lot > 0:
        lots = min(lots, int(settings.shadow_book_capital // cost_per_lot))
    if lots < 1:
        return 0, UNAFFORDABLE
    return lots, None


def _attribution(blocker: dict | None, gated_in_position: bool) -> str | None:
    """Why the gated book did not take this call, from observed facts only.

    A funnel refusal names itself. Otherwise the commonest reason is the one no
    gate records: the gated book was already holding, so it never looked. When
    neither is true the row says nothing rather than guessing — an unattributed
    call is reported as UNRECORDED downstream.
    """
    if blocker:
        return blocker.get("primary_blocker")
    return ALREADY_IN_POSITION if gated_in_position else None


def observe(
    instrument: str,
    decision: Decision,
    chain: list[OptionQuote],
    now: float,
    *,
    gated_taken: bool,
    gated_in_position: bool = False,
    tick_started: float | None = None,
) -> None:
    """Record this board call in the ungated ledger. Never raises, never trades.

    ``gated_taken`` is what the gated auto-trader did with the same call on this
    tick, recorded on the row so the comparison is per-call rather than two
    totals that happen to cover the same session. ``gated_in_position`` is why it
    most often does nothing — it was already holding — which no gate records.
    ``tick_started`` bounds the blocker lookup to this tick.
    """
    if not settings.shadow_book_enabled:
        return
    try:
        _observe(instrument, decision, chain, now,
                 gated_taken=gated_taken, gated_in_position=gated_in_position,
                 tick_started=tick_started)
    except Exception:  # noqa: BLE001 - measurement must never break a tick
        pass


def _called_buy(decision: Decision) -> bool:
    """A BUY on the board, whichever field carried it.

    ``market_signal`` is the position-independent scan the board prints while a
    position is open, so a BUY that appears only there is still a call the user
    saw — and is exactly the kind the gated book refuses.
    """
    return decision.signal == Signal.BUY or decision.market_signal == Signal.BUY


def _observe(
    instrument: str,
    decision: Decision,
    chain: list[OptionQuote],
    now: float,
    *,
    gated_taken: bool,
    gated_in_position: bool,
    tick_started: float | None,
) -> None:
    if not _called_buy(decision):
        return
    symbol = decision.recommended_option
    entry = decision.current_premium
    stop = decision.stop_loss
    blocker = exec_funnel.last_block(
        instrument, since=tick_started or (now - 60), option=symbol or "")
    if not symbol or not entry:
        # The board printed BUY with no contract or no premium. There is no leg
        # and no price, so it is recorded as unmeasurable rather than entered at
        # a made-up one.
        _append({
            "event": SKIPPED, "reason": NO_PLAN, "ts": int(now),
            "time_ist": _ist(now), "session": _session(now),
            "instrument": instrument, "symbol": symbol,
            "premium": entry, "stop": stop, "target1": decision.target1,
            "gated_taken": gated_taken,
            "gated_in_position": gated_in_position,
            "would_be_blocker": _attribution(blocker, gated_in_position),
        })
        return

    why = _unusable(float(entry), stop, decision.target1)
    if why is not None:
        _watch(instrument, decision, chain, now, symbol, float(entry),
               gated_taken=gated_taken, gated_in_position=gated_in_position,
               blocker=blocker, why=why)
        return

    key = f"{instrument}|{symbol}"
    with _LOCK:
        held = key in _open
    if held:
        last = _last_repeat.get(key, 0.0)
        if now - last >= _REPEAT_SEC:
            _last_repeat[key] = now
            _append({
                "event": REPEAT, "ts": int(now), "time_ist": _ist(now),
                "session": _session(now), "instrument": instrument,
                "symbol": symbol, "premium": entry,
                "confidence": decision.confidence,
                "gated_taken": gated_taken,
                "gated_in_position": gated_in_position,
                "would_be_blocker": _attribution(blocker, gated_in_position),
                "note": ("the board repeated this call while the shadow book "
                         "already held the leg; recorded, not laddered"),
            })
        return

    risk = float(entry) - float(stop)
    lots, refusal = _lots(float(entry), risk, _lot_size(instrument))
    if refusal is not None:
        _append({
            "event": SKIPPED, "reason": refusal, "ts": int(now),
            "time_ist": _ist(now), "session": _session(now),
            "instrument": instrument, "symbol": symbol, "premium": entry,
            "stop": stop, "risk_points": round(risk, 2),
            "lot_size": _lot_size(instrument),
            "shadow_capital": settings.shadow_book_capital,
            "gated_taken": gated_taken,
            "gated_in_position": gated_in_position,
            "would_be_blocker": _attribution(blocker, gated_in_position),
        })
        return

    quote = _leg(chain, symbol)
    gates = decision.gates
    row = {
        "event": ENTRY,
        "shadow_id": f"SH-{instrument}-{int(now * 1000)}",
        "ts": int(now),
        "time_ist": _ist(now),
        "session": _session(now),
        "instrument": instrument,
        "symbol": symbol,
        "option_type": decision.option_type.value if decision.option_type else None,
        "strike": decision.strike,
        "lot_size": _lot_size(instrument),
        "lots": lots,
        # Entry at the mid, and at the ask a buyer would actually pay. Absent
        # rather than assumed when the book was not quoted.
        "entry_mid": round(float(entry), 2),
        "entry_ask": quote.ask if quote else None,
        "bid_at_entry": quote.bid if quote else None,
        "stop": float(stop),
        "risk_points": round(risk, 2),
        "target1": decision.target1,
        "target2": decision.target2,
        "target3": decision.target3,
        "confidence": decision.confidence,
        "signal_strength": decision.signal_strength,
        "trade_quality": decision.trade_quality,
        "risk_level": decision.risk_level,
        "conviction_meter": decision.conviction_meter,
        "entry_trigger": decision.entry_trigger,
        "reasons": list(decision.reasons),
        # What the gated book did with the same call, and the gate it died on.
        # This pairing is the whole point of the ledger.
        "gated_taken": gated_taken,
        "gated_in_position": gated_in_position,
        "would_be_blocker": _attribution(blocker, gated_in_position),
        "would_be_blocker_stage": (blocker or {}).get("stage"),
        "would_be_blocker_reason": (blocker or {}).get("reason"),
        "would_be_blocker_where": (blocker or {}).get("where"),
        "gate_primary_blocker": gates.primary_blocker if gates else None,
        "gate_secondary_blocker": gates.secondary_blocker if gates else None,
        "gate_blockers": list(gates.blockers) if gates else [],
        "board_signal": decision.signal.value,
        "market_signal": (decision.market_signal.value
                          if decision.market_signal else None),
        "paper_only": True,
        "note": ("ungated research ledger: no order exists, nothing is routed "
                 "anywhere, and the gated book is unaffected by this row"),
    }
    _append(row)
    with _LOCK:
        _open[key] = {
            "shadow_id": row["shadow_id"],
            "instrument": instrument,
            "symbol": symbol,
            "session": row["session"],
            "ts": now,
            "lots": lots,
            "lot_size": row["lot_size"],
            "entry": float(entry),
            "entry_ask": quote.ask if quote else None,
            "stop": float(stop),
            "risk": risk,
            "targets": {"T1": decision.target1, "T2": decision.target2,
                        "T3": decision.target3},
            "peak": float(entry),
            "trough": float(entry),
            "peak_ts": now,
            "trough_ts": now,
            "hit": [],
            "gated_taken": gated_taken,
            "would_be_blocker": _attribution(blocker, gated_in_position),
        }
        _last_repeat[key] = now


def _unusable(entry: float, stop: float | None, target1: float | None) -> str | None:
    """Why this plan cannot be measured as a trade, or None if it can.

    A target at or below the entry, or a stop at or above it, is not a thin plan
    — it is a plan that cannot be entered at the printed premium at all.

    The test itself lives in ``plan_validity`` so the ledger, the dashboard and
    the signal journal cannot disagree about what is enterable; only the label
    for missing levels stays local, because this file's rows already carry it.
    """
    why = plan_validity.unusable(entry, stop, target1)
    return NO_LEVELS if why == plan_validity.NO_LEVELS else why


def _watch(
    instrument: str,
    decision: Decision,
    chain: list[OptionQuote],
    now: float,
    symbol: str,
    entry: float,
    *,
    gated_taken: bool,
    gated_in_position: bool,
    blocker: dict | None,
    why: str = NO_LEVELS,
) -> None:
    """Follow a call whose plan cannot be traded as printed.

    Deliberately not a position: no lots, no rupees and no R, because a stop is
    what those are computed from and inventing one would turn an unlevelled call
    into a fabricated trade. What can honestly be measured — what the premium did
    from the moment the call was printed — is measured.
    """
    key = f"{instrument}|{symbol}"
    with _LOCK:
        if key in _open:
            return
    quote = _leg(chain, symbol)
    row = {
        "event": WATCH,
        "shadow_id": f"SW-{instrument}-{int(now * 1000)}",
        "ts": int(now),
        "time_ist": _ist(now),
        "session": _session(now),
        "instrument": instrument,
        "symbol": symbol,
        "option_type": decision.option_type.value if decision.option_type else None,
        "strike": decision.strike,
        "reason": why,
        "entry_mid": round(entry, 2),
        "entry_ask": quote.ask if quote else None,
        "bid_at_entry": quote.bid if quote else None,
        # Kept as printed so the plan that could not be traded is visible, while
        # the follow below uses no level at all.
        "plan_stop": decision.stop_loss,
        "plan_target1": decision.target1,
        "stop": None,
        "target1": None,
        "lots": None,
        "confidence": decision.confidence,
        "signal_strength": decision.signal_strength,
        "trade_quality": decision.trade_quality,
        "reasons": list(decision.reasons),
        "gated_taken": gated_taken,
        "gated_in_position": gated_in_position,
        "would_be_blocker": _attribution(blocker, gated_in_position),
        "board_signal": decision.signal.value,
        "market_signal": (decision.market_signal.value
                          if decision.market_signal else None),
        "paper_only": True,
        "note": ("this call had no usable stop or target, so the premium is "
                 "followed but no size, rupee result or R is claimed"),
    }
    _append(row)
    with _LOCK:
        _open[key] = {
            "watch": True,
            "watch_reason": why,
            "shadow_id": row["shadow_id"],
            "instrument": instrument,
            "symbol": symbol,
            "session": row["session"],
            "ts": now,
            "lots": 0,
            "lot_size": _lot_size(instrument),
            "entry": entry,
            "entry_ask": quote.ask if quote else None,
            "stop": None,
            "risk": None,
            "targets": {},
            "peak": entry,
            "trough": entry,
            "peak_ts": now,
            "trough_ts": now,
            "hit": [],
            "gated_taken": gated_taken,
            "would_be_blocker": _attribution(blocker, gated_in_position),
        }
        _last_repeat[key] = now


def follow(instrument: str, chain: list[OptionQuote], now: float) -> None:
    """Advance every open shadow position on this instrument. Never trades."""
    if not settings.shadow_book_enabled:
        return
    try:
        _follow(instrument, chain, now)
    except Exception:  # noqa: BLE001 - measurement must never break a tick
        pass


def _follow(instrument: str, chain: list[OptionQuote], now: float) -> None:
    prices = {q.symbol: q for q in chain}
    follow_sec = max(1, settings.shadow_book_follow_minutes) * 60
    with _LOCK:
        items = [(k, v) for k, v in _open.items() if v["instrument"] == instrument]
    for key, st in items:
        quote = prices.get(st["symbol"])
        premium = float(quote.premium) if quote and quote.premium else None
        if premium is not None:
            if premium > st["peak"]:
                st["peak"], st["peak_ts"] = premium, now
            if premium < st["trough"]:
                st["trough"], st["trough_ts"] = premium, now
        if st.get("watch"):
            # No levels to hit, so it ends on time or with the session only.
            if now - st["ts"] >= follow_sec:
                _close(key, st, premium, quote, now, TIMEOUT)
            elif _session(now) != st["session"]:
                _close(key, st, premium, quote, now, SESSION_END)
            continue
        if premium is not None:
            for name in ("T1", "T2", "T3"):
                level = st["targets"].get(name)
                if level is not None and name not in st["hit"] and premium >= float(level):
                    st["hit"].append(name)
            # The stop is checked before the exit on a target so a bar that
            # touched both is not scored as a win.
            if premium <= st["stop"]:
                _close(key, st, premium, quote, now, STOP_HIT)
                continue
            # Books at the last target reached, like the gated book's ladder:
            # T3 ends it, T1/T2 keep running with the reached level as a floor.
            if "T3" in st["hit"]:
                _close(key, st, premium, quote, now, TARGET_HIT)
                continue
            floors = [st["targets"][n] for n in st["hit"]
                      if st["targets"].get(n) is not None]
            if floors and premium <= float(max(floors)):
                _close(key, st, premium, quote, now, TARGET_HIT)
                continue
        if now - st["ts"] >= follow_sec:
            _close(key, st, premium, quote, now, TIMEOUT)
            continue
        if _session(now) != st["session"]:
            _close(key, st, premium, quote, now, SESSION_END)


def _close(key: str, st: dict, premium: float | None,
           quote: OptionQuote | None, now: float, outcome: str) -> None:
    """Write the exit row and drop the position. Bookkeeping only."""
    exit_mid = premium if premium is not None else st["peak"]
    units = st["lots"] * st["lot_size"]
    gross = (exit_mid - st["entry"]) * units
    if st.get("watch"):
        _close_watch(key, st, exit_mid, quote, now, outcome)
        return
    # Costed: paid the ask, sold the bid. None when either side was unquoted —
    # a missing book is not a free one.
    bid = quote.bid if quote else None
    net = ((float(bid) - float(st["entry_ask"])) * units
           if bid is not None and st["entry_ask"] is not None else None)
    mfe = st["peak"] - st["entry"]
    mae = st["trough"] - st["entry"]
    _append({
        "event": EXIT,
        "shadow_id": st["shadow_id"],
        "ts": int(now),
        "time_ist": _ist(now),
        "session": st["session"],
        "instrument": st["instrument"],
        "symbol": st["symbol"],
        "outcome": outcome,
        "targets_reached": st["hit"],
        "entry_mid": round(st["entry"], 2),
        "exit_mid": round(exit_mid, 2),
        "lots": st["lots"],
        "lot_size": st["lot_size"],
        "net_points": round(exit_mid - st["entry"], 2),
        "realized_r": round((exit_mid - st["entry"]) / st["risk"], 3) if st["risk"] else None,
        "mfe_points": round(mfe, 2),
        "mae_points": round(mae, 2),
        "minutes_to_mfe": round((st["peak_ts"] - st["ts"]) / 60.0, 1),
        "minutes_held": round((now - st["ts"]) / 60.0, 1),
        "gross_rupees": round(gross, 2),
        "net_rupees": round(net, 2) if net is not None else None,
        "costed": net is not None,
        "gated_taken": st["gated_taken"],
        "would_be_blocker": st["would_be_blocker"],
        "paper_only": True,
        "note": ("gross_rupees is mid-to-mid; net_rupees pays the ask and sells "
                 "the bid. Where the book was unquoted net_rupees is null, not "
                 "equal to gross."),
    })
    with _LOCK:
        _open.pop(key, None)
        _last_repeat.pop(key, None)


def _close_watch(key: str, st: dict, exit_mid: float,
                 quote: OptionQuote | None, now: float, outcome: str) -> None:
    """End a levelless call: what the premium did, and nothing it did not.

    No rupees and no R, because there was never a stop to size against. The
    costed points still pay the book, so the row cannot be read as a free trade.
    """
    bid = quote.bid if quote else None
    costed_points = (round(float(bid) - float(st["entry_ask"]), 2)
                     if bid is not None and st["entry_ask"] is not None else None)
    _append({
        "event": WATCH_END,
        "shadow_id": st["shadow_id"],
        "ts": int(now),
        "time_ist": _ist(now),
        "session": st["session"],
        "instrument": st["instrument"],
        "symbol": st["symbol"],
        "outcome": outcome,
        "reason": NO_LEVELS,
        "entry_mid": round(st["entry"], 2),
        "exit_mid": round(exit_mid, 2),
        "net_points": round(exit_mid - st["entry"], 2),
        "costed_points": costed_points,
        "mfe_points": round(st["peak"] - st["entry"], 2),
        "mae_points": round(st["trough"] - st["entry"], 2),
        "minutes_to_mfe": round((st["peak_ts"] - st["ts"]) / 60.0, 1),
        "minutes_held": round((now - st["ts"]) / 60.0, 1),
        "lots": None,
        "realized_r": None,
        "gross_rupees": None,
        "net_rupees": None,
        "gated_taken": st["gated_taken"],
        "would_be_blocker": st["would_be_blocker"],
        "paper_only": True,
        "note": ("no stop existed, so this row reports premium movement only: "
                 "no size, no rupee result and no R are claimed for it"),
    })
    with _LOCK:
        _open.pop(key, None)
        _last_repeat.pop(key, None)


def open_count() -> int:
    """Open shadow positions. Diagnostics only."""
    with _LOCK:
        return len(_open)


def read_ledger(limit: int = 2000, session: str | None = None) -> list[dict]:
    """Newest ledger rows first. Read-only; the file is never modified."""
    rows: list[dict] = []
    path = ledger_path()
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and (not session or rec.get("session") == session):
                rows.append(rec)
    return rows[-limit:][::-1]


def reset_for_tests() -> None:
    """Clear process state. Used by the smoke; never called by the app."""
    with _LOCK:
        _open.clear()
        _last_repeat.clear()


def _rate(hits: int, total: int) -> float | None:
    """A rate with no denominator is None, never 0.0."""
    return round(100.0 * hits / total, 1) if total else None


def compare(gated_trades: list[dict], session: str | None = None) -> dict:
    """Gated book vs shadow book, plus what the refused calls actually did.

    ``gated_trades`` are the closed gated paper trades (from the trade store) —
    passed in rather than read here, so this module keeps no dependency on the
    trading side's storage. Nothing in the return value feeds a decision.
    """
    rows = read_ledger(limit=20000, session=session)
    exits = [r for r in rows if r.get("event") == EXIT]
    entries = [r for r in rows if r.get("event") == ENTRY]
    skipped = [r for r in rows if r.get("event") == SKIPPED]
    watched = [r for r in rows if r.get("event") == WATCH]
    watch_ends = [r for r in rows if r.get("event") == WATCH_END]
    refused = [r for r in exits if not r.get("gated_taken")]
    costed = [r for r in exits if r.get("net_rupees") is not None]

    def book(trades: list[dict], pnl_key: str) -> dict:
        wins = [t for t in trades if (t.get(pnl_key) or 0) > 0]
        total = sum(float(t.get(pnl_key) or 0.0) for t in trades)
        return {
            "trades": len(trades),
            "wins": len(wins),
            "win_rate_pct": _rate(len(wins), len(trades)),
            "total_rupees": round(total, 2),
            "avg_rupees": round(total / len(trades), 2) if trades else None,
        }

    blockers: dict[str, dict] = {}
    for row in refused:
        name = row.get("would_be_blocker") or "UNRECORDED"
        agg = blockers.setdefault(name, {"trades": 0, "gross_rupees": 0.0,
                                         "net_rupees": 0.0, "costed": 0})
        agg["trades"] += 1
        agg["gross_rupees"] = round(agg["gross_rupees"]
                                    + float(row.get("gross_rupees") or 0.0), 2)
        if row.get("net_rupees") is not None:
            agg["net_rupees"] = round(agg["net_rupees"]
                                      + float(row["net_rupees"]), 2)
            agg["costed"] += 1
    for agg in blockers.values():
        if not agg["costed"]:
            # No leg under this blocker had a quoted book, so the costed total is
            # unknown rather than zero.
            agg["net_rupees"] = None

    watch_costed = [r for r in watch_ends if r.get("costed_points") is not None]
    return {
        "session": session,
        "shadow": {
            "entries": len(entries),
            "closed": len(exits),
            "open": open_count(),
            "unmeasurable_calls": len(skipped),
            "gross": book(exits, "gross_rupees"),
            "costed": book(costed, "net_rupees"),
            "costed_coverage_pct": _rate(len(costed), len(exits)),
        },
        # Calls the engine printed with no stop or target. Kept apart from the
        # sized book on purpose: without a stop there is no position size to
        # compare, so these are reported in premium points only.
        "levelless_calls": {
            "watched": len(watched),
            "followed_to_an_end": len(watch_ends),
            "mid_points": round(sum(float(r.get("net_points") or 0.0)
                                    for r in watch_ends), 2),
            "costed_points": (round(sum(float(r["costed_points"])
                                        for r in watch_costed), 2)
                              if watch_costed else None),
            "costed_coverage_pct": _rate(len(watch_costed), len(watch_ends)),
            # Which defect in the printed plan sent the call here.
            "by_reason": dict(Counter(str(r.get("reason")) for r in watched)),
            "note": ("points only: these calls had no usable stop or target, so "
                     "no size, rupee result or R is claimed for them"),
        },
        "gated": book(gated_trades, "net_pnl"),
        "refused_by_a_gate": {
            "closed": len(refused),
            "gross": book(refused, "gross_rupees"),
            "by_blocker": blockers,
        },
        "reading": (
            "'refused_by_a_gate' is the money question: these are the calls the "
            "gated book would not take. Compare its COSTED total, not its gross "
            "one — mid-to-mid ignores the bid/ask, and on the recorded data that "
            "difference turned an apparent profit into a loss. A blocker whose "
            "costed total is positive is a candidate for removal; one that is "
            "negative was protecting the book."
        ),
        "safety": (
            "Shadow rows are paper-only research. This ledger has no order path, "
            "its own capital pot, and the gated book behaves exactly as it would "
            "if this module did not exist."
        ),
    }
