"""Live capture of the frozen pair relationship. Record only.

Called from the tick loop, once per instrument. It writes what the feed said and
runs the FROZEN rule as a paper position. It reads no production state, returns
nothing the tick uses, places no order and swallows its own failures into
:func:`health` -- a research row is never worth a live tick.

Two things it will not do, because they are what made the first result
unusable:

* it never pairs two legs quoted at different timestamps -- an observation
  exists only when both instruments reported the SAME bar;
* it never invents a spread. When a leg has no two-sided book the trade is
  still recorded, but tagged ``UNMEASURED`` and excluded from the measured-cost
  sample the retest is allowed to judge.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.market.instruments import get_spec
from app.research.pairs import spec as pair_spec
from app.research.pairs import store

_IST = timezone(timedelta(hours=5, minutes=30))
_LOCK = threading.RLock()

MEASURED = "MEASURED"
UNMEASURED = "UNMEASURED"

LONG_A_SHORT_B = "LONG_A_SHORT_B"
SHORT_A_LONG_B = "SHORT_A_LONG_B"

_HEALTH = {
    "leg_rows": 0,
    "pair_rows": 0,
    "contract_rows": 0,
    "trades_opened": 0,
    "trades_closed": 0,
    "errors": 0,
    "last_error": None,
    "unpaired_legs": 0,
    "backfilled_bars": 0,
    "late_obs_not_traded": 0,
    "book_dropped_stale_bar": 0,
    "out_of_session_bars": 0,
    "samples_without_a_bar": 0,
}

BACKFILL = "CANDLE_BACKFILL"

# A book may be stamped onto the bar it was quoted inside, or onto the bar that
# had just closed when it was quoted -- and onto nothing else. Beyond that the
# quote belongs to a different minute than the price, and pairing the two would
# report a spread that never existed at that observation.
_BOOK_MAX_BAR_AGE_SEC = 120

# The bar each leg was last sampled at, so a sample that produced no new bar can
# be distinguished from one that did. Only this needs to live in memory; the
# counters themselves accumulate in the store.
_LAST_BAR: dict[str, int] = {}


def _session_bounds(instrument: str) -> tuple[int, int] | None:
    """Seconds-into-the-IST-day between which this instrument actually trades."""
    try:
        exchange = get_spec(instrument).exchange
    except Exception:
        return None
    try:
        opening, closing = settings.session_window_ist(exchange)
        oh, om = (int(x) for x in str(opening).split(":"))
        ch, cm = (int(x) for x in str(closing).split(":"))
    except Exception:
        return None
    return oh * 3600 + om * 60, ch * 3600 + cm * 60


def _in_session(instrument: str, bar_ts: int) -> bool:
    """Is this bar inside the window where the contract was tradable?

    The feed keeps answering after the bell -- REST history returns bars past
    the close and the quote cache keeps its last book -- so a capture left
    running produces observations for minutes in which neither leg could have
    been bought or sold. Pairing those minutes would let the frozen rule enter
    and exit at prices no order could have reached, which is the difference
    between recording the market and recording the feed.
    """
    bounds = _session_bounds(instrument)
    if bounds is None:
        return True
    opened, closed = bounds
    moment = datetime.fromtimestamp(int(bar_ts), _IST)
    if moment.weekday() >= 5:
        return False
    into_day = moment.hour * 3600 + moment.minute * 60 + moment.second
    # A bar is named by its opening minute, so the last valid one starts one
    # minute before the bell.
    return opened <= into_day <= closed - 60


def health() -> dict:
    out = dict(_HEALTH)
    try:
        out["legs"] = store.leg_diag()
    except Exception:  # pragma: no cover - reporting must not raise
        out["legs"] = {}
    try:
        out["store"] = store.counts()
        out["spec"] = pair_spec.as_dict()
    except Exception as exc:  # pragma: no cover - reporting must not raise
        out["store_error"] = str(exc)
    return out


def session_of(bar_ts: int) -> str:
    return datetime.fromtimestamp(int(bar_ts), _IST).strftime("%Y-%m-%d")


def _book_value(book: dict | None, key: str) -> float | None:
    if not isinstance(book, dict):
        return None
    value = book.get(key)
    if isinstance(value, (int, float)) and float(value) > 0:
        return float(value)
    return None


def _zscore(ratio: float, window: list[float]) -> float | None:
    n = len(window)
    if n < pair_spec.WINDOW:
        return None
    mean = sum(window) / n
    var = sum((x - mean) ** 2 for x in window) / n
    if var <= 0:
        return None
    return (ratio - mean) / var ** 0.5


def _leg_cost(entry: float, exit_price: float, qty: float,
              measured: bool) -> float:
    """Round-trip rupee cost of ONE futures leg.

    Brokerage is per order, statutory charges are a percentage of turnover, and
    the spread is charged ONLY as the modelled slippage when the book was not
    quoted. When both sides were quoted, the spread is already inside the fill
    prices (buy at ask, sell at bid) and charging slippage on top would double
    count it.
    """
    buy_value, sell_value = entry * qty, exit_price * qty
    cost = (
        settings.futures_brokerage_per_order * 2.0
        + sell_value * settings.futures_stt_sell_pct / 100.0
        + (buy_value + sell_value) * settings.futures_txn_pct / 100.0
    )
    if not measured:
        cost += 2.0 * settings.futures_slippage_points * qty
    return cost


def _fills(book: dict | None, price: float, side: str) -> tuple[float, bool]:
    """Executable price for ``side`` ('BUY'/'SELL') and whether it was measured."""
    bid, ask = _book_value(book, "bid"), _book_value(book, "ask")
    if bid is not None and ask is not None and ask >= bid:
        return (ask, True) if side == "BUY" else (bid, True)
    return price, False


def observe_leg(instrument: str, bar_ts: int | None, price: float | None, *,
                futures_book: dict | None = None,
                contract_books: list[dict] | None = None,
                history: list[tuple[int, float]] | None = None,
                now: float | None = None) -> None:
    """Record one leg of this bar, and advance the frozen rule if both exist.

    ``history`` is the tail of EARLIER closed bars from the same candle series.
    An instrument is only re-read when the scan reaches it and the feed's REST
    history is rate-limited, so a leg routinely skips minutes; the two legs skip
    different minutes and then share almost no timestamps, which is what makes
    pairs rare. Backfilling those bars from the leg's own closed candles fixes
    that without inventing anything: the price is the feed's own close for that
    bar. What a backfilled row does NOT have is a book -- the bid and ask only
    existed at the instant they were quoted -- so it is stored with no book and
    counted as unmeasured rather than being given the current spread.
    """
    if not settings.pair_capture_enabled:
        return
    if not bar_ts:
        # The feed had no candle at all for this leg: its REST history is
        # rate-limited and the socket has pushed no tick to build a bar from.
        # Recorded, because a leg sampled hundreds of times with no bar to be
        # sampled AT looks identical, in the stored rows, to a leg that is
        # barely sampled -- and the two need opposite fixes.
        _note_missing_bar(instrument, now)
        return
    try:
        with _LOCK:
            captured = float(now if now is not None else bar_ts)
            if contract_books:
                _HEALTH["contract_rows"] += store.write_contract_quotes(
                    instrument, int(bar_ts), contract_books)
            if instrument not in (pair_spec.LEG_A, pair_spec.LEG_B):
                return
            diag = {"samples": 1}
            bar_age = captured - float(bar_ts)
            last = {
                "last_bar_ts": int(bar_ts),
                "last_bar_age_sec": round(bar_age, 1),
                "last_source": (futures_book or {}).get("source"),
                "updated_ts": captured,
            }
            if not _in_session(instrument, bar_ts):
                diag["out_of_session"] = 1
                _HEALTH["out_of_session_bars"] += 1
                store.write_leg_diag(instrument, session_of(bar_ts), diag, last)
                return
            if not price or float(price) <= 0:
                diag["no_price"] = 1
                store.write_leg_diag(instrument, session_of(bar_ts), diag, last)
                return
            if _LAST_BAR.get(instrument) == int(bar_ts):
                diag["bar_unchanged"] = 1
            else:
                diag["new_bars"] = 1
            _LAST_BAR[instrument] = int(bar_ts)
            # The book is only ever attached to the bar it was quoted inside (or
            # the one that had just closed). A leg whose socket is quiet keeps
            # serving its last REST bar, which can be many minutes old while the
            # quote cache is current; stamping that quote onto that bar would
            # manufacture a synchronization the feed never had, and the retest
            # would then judge costs measured at a different instant.
            if futures_book is not None and bar_age > _BOOK_MAX_BAR_AGE_SEC:
                futures_book = None
                diag["book_dropped_stale_bar"] = 1
                _HEALTH["book_dropped_stale_bar"] += 1
            if (_book_value(futures_book, "bid") is not None
                    and _book_value(futures_book, "ask") is not None):
                diag["book_two_sided"] = 1
            else:
                diag["book_missing"] = 1
            store.write_leg_diag(instrument, session_of(bar_ts), diag, last)
            spec = get_spec(instrument)
            lot_size = getattr(spec, "lot_size", None)
            bars: list[int] = []
            for past_ts, past_close in sorted(history or []):
                if int(past_ts) >= int(bar_ts) or not past_close or past_close <= 0:
                    continue
                if store.leg_quote(instrument, int(past_ts)):
                    continue
                if not _in_session(instrument, int(past_ts)):
                    _HEALTH["out_of_session_bars"] += 1
                    continue
                store.write_leg_quote({
                    "instrument": instrument,
                    "bar_ts": int(past_ts),
                    "captured_ts": captured,
                    "session": session_of(past_ts),
                    "price": float(past_close),
                    "lot_size": lot_size,
                    "source": BACKFILL,
                })
                _HEALTH["backfilled_bars"] += 1
                bars.append(int(past_ts))
            store.write_leg_quote({
                "instrument": instrument,
                "bar_ts": int(bar_ts),
                "captured_ts": captured,
                "session": session_of(bar_ts),
                "price": float(price),
                "fut_symbol": (futures_book or {}).get("symbol"),
                "fut_expiry": (futures_book or {}).get("expiry"),
                "fut_dte": (futures_book or {}).get("days_to_expiry"),
                "lot_size": lot_size,
                "ltp": _book_value(futures_book, "ltp"),
                "bid": _book_value(futures_book, "bid"),
                "ask": _book_value(futures_book, "ask"),
                "oi": (futures_book or {}).get("oi"),
                "volume": (futures_book or {}).get("volume"),
                "feed_age_sec": (futures_book or {}).get("feed_age_sec"),
                "source": (futures_book or {}).get("source"),
            })
            _HEALTH["leg_rows"] += 1
            bars.append(int(bar_ts))
            for ts in bars:
                _pair_if_ready(ts)
    except Exception as exc:  # research must never break a live tick
        _HEALTH["errors"] += 1
        _HEALTH["last_error"] = str(exc)


def _note_missing_bar(instrument: str, now: float | None) -> None:
    if instrument not in (pair_spec.LEG_A, pair_spec.LEG_B):
        return
    _HEALTH["samples_without_a_bar"] += 1
    moment = float(now if now is not None else time.time())
    try:
        with _LOCK:
            store.write_leg_diag(instrument, session_of(int(moment)),
                                 {"samples": 1, "no_bar": 1},
                                 {"updated_ts": moment})
    except Exception as exc:  # pragma: no cover - research must never raise
        _HEALTH["errors"] += 1
        _HEALTH["last_error"] = str(exc)


def _pair_if_ready(bar_ts: int) -> None:
    a = store.leg_quote(pair_spec.LEG_A, bar_ts)
    b = store.leg_quote(pair_spec.LEG_B, bar_ts)
    if not a or not b:
        _HEALTH["unpaired_legs"] += 1
        return
    newest = store.max_pair_ts()
    ratio = float(a["price"]) / float(b["price"])
    window = store.recent_ratios(pair_spec.WINDOW, bar_ts)
    z = _zscore(ratio, window)
    measured = all(a[k] is not None and b[k] is not None for k in ("bid", "ask"))
    fresh = store.write_pair_obs({
        "bar_ts": bar_ts,
        "session": session_of(bar_ts),
        "fingerprint": pair_spec.fingerprint(),
        "a_price": a["price"], "b_price": b["price"], "ratio": ratio,
        "z": z, "window_n": len(window),
        "a_bid": a["bid"], "a_ask": a["ask"],
        "b_bid": b["bid"], "b_ask": b["ask"],
        "spread_status": MEASURED if measured else UNMEASURED,
    })
    if not fresh:
        return
    _HEALTH["pair_rows"] += 1
    if bar_ts < newest:
        # A bar that only completed both legs after a LATER bar was already
        # traded. It is kept as evidence, but the rule is not run backwards on
        # it: a position may only be opened or closed in the order the session
        # actually saw the prices.
        _HEALTH["late_obs_not_traded"] += 1
        return
    _advance(bar_ts, a, b, z)


def _advance(bar_ts: int, a: dict, b: dict, z: float | None) -> None:
    """Apply the frozen entry/exit rule to this observation. Paper only."""
    session = session_of(bar_ts)
    for trade in store.open_trades():
        _maybe_close(trade, bar_ts, session, a, b, z)
    if z is None or store.open_trades():
        return
    if abs(z) < pair_spec.THRESHOLD:
        return
    direction = SHORT_A_LONG_B if z > 0 else LONG_A_SHORT_B
    lot_a = int(a["lot_size"] or 0) or 1
    lot_b = int(b["lot_size"] or 0) or 1
    notional_ratio = (lot_a * float(a["price"])) / (lot_b * float(b["price"]))
    lots_b = max(1, round(notional_ratio))
    side_a = "BUY" if direction == LONG_A_SHORT_B else "SELL"
    side_b = "SELL" if direction == LONG_A_SHORT_B else "BUY"
    entry_a, ma = _fills(_book_of(a), float(a["price"]), side_a)
    entry_b, mb = _fills(_book_of(b), float(b["price"]), side_b)
    qty_a, qty_b = lot_a, lots_b * lot_b
    store.open_trade({
        "fingerprint": pair_spec.fingerprint(),
        "session": session,
        "direction": direction,
        "entry_ts": bar_ts,
        "lots_a": 1, "lots_b": lots_b,
        "qty_a": qty_a, "qty_b": qty_b,
        "entry_a": entry_a, "entry_b": entry_b,
        "z_entry": z,
        "notional_a": entry_a * qty_a,
        "notional_b": entry_b * qty_b,
        "cost_status": MEASURED if (ma and mb) else UNMEASURED,
        "cost_detail": {"entry_measured_a": ma, "entry_measured_b": mb},
    })
    _HEALTH["trades_opened"] += 1


def _book_of(leg: dict) -> dict:
    return {"bid": leg.get("bid"), "ask": leg.get("ask")}


def _exit_reason(trade: dict, bar_ts: int, session: str,
                 z: float | None) -> str | None:
    if session != trade["session"]:
        return "SESSION_END"
    held = _held(trade, bar_ts)
    if held >= pair_spec.MAX_HOLD:
        return "MAX_HOLD"
    if z is None:
        return None
    if abs(z) <= pair_spec.CONVERGE_Z:
        return "CONVERGED"
    if abs(z) >= abs(float(trade["z_entry"])) + pair_spec.DIVERGE_EXTRA:
        return "DIVERGED"
    return None


def _held(trade: dict, bar_ts: int) -> int:
    """Observations held, counted on the recorded pair series, not on clocks."""
    conn = store.connect()
    cur = conn.execute(
        "SELECT COUNT(*) FROM pair_obs WHERE bar_ts > ? AND bar_ts <= ?",
        (int(trade["entry_ts"]), int(bar_ts)))
    return int(cur.fetchone()[0] or 0)


def _maybe_close(trade: dict, bar_ts: int, session: str,
                 a: dict, b: dict, z: float | None) -> None:
    reason = _exit_reason(trade, bar_ts, session, z)
    if reason is None:
        return
    long_a = trade["direction"] == LONG_A_SHORT_B
    exit_a, ma = _fills(_book_of(a), float(a["price"]), "SELL" if long_a else "BUY")
    exit_b, mb = _fills(_book_of(b), float(b["price"]), "BUY" if long_a else "SELL")
    qty_a, qty_b = int(trade["qty_a"]), int(trade["qty_b"])
    sign = 1.0 if long_a else -1.0
    pnl_a = sign * (exit_a - float(trade["entry_a"])) * qty_a
    pnl_b = -sign * (exit_b - float(trade["entry_b"])) * qty_b
    detail = dict(_json(trade.get("cost_detail")))
    detail.update({"exit_measured_a": ma, "exit_measured_b": mb})
    measured = bool(detail.get("entry_measured_a") and detail.get("entry_measured_b")
                    and ma and mb)
    cost = (_leg_cost(float(trade["entry_a"]), exit_a, qty_a, measured)
            + _leg_cost(float(trade["entry_b"]), exit_b, qty_b, measured))
    gross = pnl_a + pnl_b
    # Notional-matched component vs the leftover one-sided exposure that integer
    # lots force on the pair -- reported apart so the pair is never credited with
    # a directional profit it did not hedge.
    notional_a = float(trade["entry_a"]) * qty_a
    notional_b = float(trade["entry_b"]) * qty_b
    matched = min(notional_a, notional_b)
    scale_a = matched / notional_a if notional_a else 0.0
    scale_b = matched / notional_b if notional_b else 0.0
    hedged = pnl_a * scale_a + pnl_b * scale_b
    store.close_trade(int(trade["id"]), {
        "exit_ts": bar_ts, "exit_a": exit_a, "exit_b": exit_b, "z_exit": z,
        "held_obs": _held(trade, bar_ts), "reason": reason,
        "gross": gross, "cost": cost, "net": gross - cost,
        "hedged_pnl": hedged, "residual_pnl": gross - hedged,
        "cost_status": MEASURED if measured else UNMEASURED,
        "cost_detail": detail,
    })
    _HEALTH["trades_closed"] += 1


def _json(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            out = json.loads(value)
            return out if isinstance(out, dict) else {}
        except ValueError:
            return {}
    return {}
