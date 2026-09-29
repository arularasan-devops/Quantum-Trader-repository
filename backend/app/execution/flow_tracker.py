"""Flow Engine (Candle-Flow) record lifecycle + separate performance log.

For ANALYSIS ONLY. When the Flow engine issues a BUY we open a lightweight record
for the leg being ridden and, on every following tick, track the premium travel
(peak / last) until the engine says EXIT or SWITCH. Finished records are appended
to their OWN JSONL log (SEPARATE from Early Momentum / Early-Early / Quick Scalp /
confirmation) so the flow engine's win rate, average hold time, profit factor and
false-signal rate can be compared objectively after paper trading.

This NEVER places an order and NEVER touches the frozen engine. A logging failure
must never break a live tick.
"""
from __future__ import annotations

import json as _json
import os as _os
import time as _time
from datetime import datetime as _dt
from datetime import timedelta as _td
from datetime import timezone as _tz

from app import storage as _storage
from app.analysis import analyst as _analyst
from app.analysis import books as _books
from app.analysis import option_costs as _costs
from app.config import settings
from app.execution import flow_economics, flow_shadow
from app.market.instruments import get_spec
from app.models import Candle, FlowSignal, OptionQuote, OptionType

_LOG_NAME = "flow_signals.jsonl"
_REFUSED_NAME = "flow_refusals.jsonl"
_MAX_OPEN_SECONDS = 2 * 3600
_IST = _tz(_td(hours=5, minutes=30))
# The last candidate the economics gate turned away, so a leg refused on every
# tick of one candle is written once instead of hundreds of times.
_last_refusal: tuple[str, int] | None = None


def _session_of(ts: object) -> str | None:
    """The IST trading date of an epoch stamp, written as the journal writes it.

    Legs logged before this field existed are still placed in time here, so an
    old row can be dated and filtered instead of being dropped.
    """
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    return _dt.fromtimestamp(float(ts), _IST).strftime("%Y-%m-%d")


def _ist_time(ts: object) -> str | None:
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    return _dt.fromtimestamp(float(ts), _IST).strftime("%Y-%m-%d %H:%M:%S")


def _log_path() -> str:
    return _os.path.join(settings.data_dir, _LOG_NAME)


def _leg_premium(chain: list[OptionQuote], symbol: str | None) -> float | None:
    if not symbol:
        return None
    for q in chain:
        if q.symbol == symbol and q.premium and q.premium > 0:
            return float(q.premium)
    return None


def _pick_atm(chain: list[OptionQuote], spot: float, side: OptionType) -> OptionQuote | None:
    same = [q for q in chain if q.option_type == side and q.premium > 0]
    if not same:
        return None
    return min(same, key=lambda q: abs(q.strike - spot))


def _quoted_spread(q: OptionQuote) -> float | None:
    """Ask minus bid in premium points, or None when the feed carried no book."""
    bid, ask = q.bid, q.ask
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return round(float(ask) - float(bid), 2)


def _candles_since(candles: list[Candle], open_ctime: int | None) -> int:
    if open_ctime is None:
        return 0
    return sum(1 for c in candles if c.time > open_ctime)


def _open_for_side(
    instrument: str,
    side: OptionType,
    chain: list[OptionQuote],
    candles: list[Candle],
    spot: float,
    now: int,
) -> dict | None:
    q = _pick_atm(chain, spot, side)
    if q is None or q.premium <= 0:
        return None
    entry = round(float(q.premium), 2)
    return {
        "engine": "flow",
        # Real ask-minus-bid at entry when the feed carried a book, so this leg is
        # costed on what the market actually charged rather than a family median.
        "entry_spread": _quoted_spread(q),
        "instrument": instrument,
        "side": side.value,
        "option_symbol": q.symbol,
        "ts_open": int(now),
        "open_ctime": candles[-1].time if candles else None,
        "entry_premium": entry,
        # What the entry economics test said about this leg when it was opened, so
        # the paper book can later be split by ratio and by whether the cost was
        # measured from a real book or assumed from the family median.
        "entry_economics": flow_economics.assess(instrument, q, candles),
        # Shadow A/B: the path-measured 0.5R trailing giveback, measured on this
        # leg beside the live giveback rule. Observation only — see flow_shadow.
        "shadow_exit": flow_shadow.start(entry),
        "peak_premium": entry,
        "last_premium": entry,
        "ts_max": int(now),
        "agreement": "PENDING",
        "confirm_delay_candles": None,
        "ts_confirm": None,
    }


def _lot_size(instrument: str) -> int:
    try:
        return int(get_spec(instrument).lot_size)
    except Exception:
        return 1


def _finalize(rec: dict, reason: str, now: int) -> dict:
    entry = rec["entry_premium"]
    peak = rec["peak_premium"]
    fp = rec.get("last_premium")
    out = dict(rec)
    out["ts_close"] = int(now)
    out["session"] = _session_of(rec.get("ts_open"))
    out["open_time_ist"] = _ist_time(rec.get("ts_open"))
    out["close_time_ist"] = _ist_time(now)
    out["close_reason"] = reason
    out["duration_sec"] = int(now) - rec["ts_open"]
    out["max_gain_points"] = round(peak - entry, 2)
    out["final_premium"] = fp
    final_points = round(fp - entry, 2) if fp is not None else None
    out["final_points"] = final_points
    out["win"] = bool(fp is not None and fp > entry)
    # Paper P&L in ₹ = points × lot_size × paper_lots (advisory/paper only).
    lots = max(1, int(settings.flow_paper_lots))
    lot_size = _lot_size(rec.get("instrument") or "")
    out["lots"] = lots
    out["lot_size"] = lot_size
    out["final_rupees"] = (
        round(final_points * lot_size * lots, 2) if final_points is not None else None
    )
    out["max_gain_rupees"] = round((peak - entry) * lot_size * lots, 2)
    # Cost of the round trip this leg just paid. Charged on every leg, because a
    # 20-second leg pays the same brokerage, tax and spread as an hour-long one
    # and it is the *count* of legs, not their quality, that this book was losing
    # to. ``final_points`` stays gross so older readers keep their meaning; the
    # net figures sit beside it.
    out.update(_cost_fields(rec.get("instrument") or "", entry, fp,
                            lot_size, lots, rec.get("entry_spread")))
    out["shadow_exit"] = _shadow_fields(rec, entry, fp, lot_size, lots, now)
    if rec.get("agreement") == "PENDING":
        out["agreement"] = "REJECTED"
    return out


def _shadow_fields(rec: dict, entry: float | None, final_premium: float | None,
                   lot_size: int, lots: int, now: int) -> dict | None:
    """Settle the shadow exit for this leg and cost it the same way as the live one.

    Both rules trade the same entry and the same number of orders, so the only
    cost that can differ between them is the exit fill — which is exactly why
    this is measured on live paper legs rather than declared from history.
    """
    sh = rec.get("shadow_exit")
    if not isinstance(sh, dict) or not isinstance(entry, (int, float)):
        return None
    out = flow_shadow.settle(sh, float(entry), final_premium, now)
    out.update(_cost_fields(rec.get("instrument") or "", entry,
                            out.get("exit_premium"), lot_size, lots,
                            rec.get("entry_spread")))
    live_points = (round(float(final_premium) - float(entry), 2)
                   if isinstance(final_premium, (int, float)) else None)
    pts = out.get("points")
    out["vs_live_points"] = (round(pts - live_points, 2)
                            if isinstance(pts, (int, float))
                            and live_points is not None else None)
    return out


def _cost_fields(instrument: str, entry: float | None, exit_px: float | None,
                 lot_size: int, lots: int,
                 quoted_spread: float | None = None) -> dict:
    """Cost/net block for one leg. Empty when the leg cannot honestly be costed.

    Returning nothing rather than zeros matters: a leg with no entry premium has
    an *unknown* cost, and a zero would let it into a total as a free trade.
    """
    if not isinstance(entry, (int, float)) or entry <= 0:
        return {}
    c = _costs.round_trip(instrument, float(entry), exit_px, lot_size, lots,
                          quoted_spread=quoted_spread)
    if c is None:
        return {}
    gross = (round(float(exit_px) - float(entry), 2)
             if isinstance(exit_px, (int, float)) else None)
    net_points = round(gross - c.cost_points, 2) if gross is not None else None
    return {
        "cost_points": c.cost_points,
        "cost_rupees": c.cost_rupees,
        "cost_spread_points": c.spread_points,
        "cost_spread_source": c.spread_source,
        "net_points": net_points,
        "net_rupees": (round(net_points * lot_size * lots, 2)
                       if net_points is not None else None),
        "net_win": (bool(net_points > 0) if net_points is not None else None),
    }


def _append(rec: dict) -> None:
    try:
        _os.makedirs(settings.data_dir, exist_ok=True)
        with open(_log_path(), "a", encoding="utf-8") as fh:
            fh.write(_json.dumps(rec) + "\n")
    except Exception:
        pass


def _log_refusal(
    instrument: str,
    sig: FlowSignal,
    candles: list[Candle],
    spot: float,
    now: int,
) -> None:
    """Write one row per candidate the economics gate turned away.

    A refused leg is never opened, so it would otherwise leave no trace — and a
    gate whose refusals are invisible cannot be graded later against what those
    legs would have done. The row carries the underlying price and time so the
    counterfactual can be replayed from history.
    """
    global _last_refusal
    try:
        symbol = sig.option_symbol or ""
        ctime = int(candles[-1].time) if candles else int(now)
        key = (symbol, ctime)
        if key == _last_refusal:
            return
        _last_refusal = key
        _os.makedirs(settings.data_dir, exist_ok=True)
        row = {
            "kind": "REFUSED_UNECONOMIC",
            "ts": int(now),
            "ist_time": _ist_time(now),
            "session": _session_of(now),
            "instrument": instrument,
            "option_symbol": sig.option_symbol,
            "side": sig.side.value if sig.side is not None else None,
            "spot": round(float(spot), 2),
            "candle_time": ctime,
            "entry_hint": sig.entry_hint,
            "strength": sig.strength,
            "cost_points": sig.economics_cost_points,
            "cost_spread_source": sig.economics_cost_source,
            "expected_move_points": sig.economics_expected_points,
            "ratio": sig.economics_ratio,
            "required_multiple": sig.economics_required,
            "note": sig.economics_note,
        }
        with open(_os.path.join(settings.data_dir, _REFUSED_NAME), "a",
                  encoding="utf-8") as fh:
            fh.write(_json.dumps(row) + "\n")
    except Exception:
        pass


def _record_journal(fin: dict) -> None:
    """Mirror a finished Flow leg into the unified trade Journal (tagged
    FLOW · paper), so paper Flow trades sit alongside the engine/Auto-Buy record
    with the same analyst note and CSV export. Best-effort — never raises."""
    try:
        side = fin.get("side")
        otype = "CE" if side == OptionType.CALL.value else (
            "PE" if side == OptionType.PUT.value else None
        )
        dur = fin.get("duration_sec")
        reason_map = {"EXIT": "flow faded/reversed", "SWITCH": "confirmed reversal switch",
                      "TIME": "max hold time"}
        reason = reason_map.get(fin.get("close_reason") or "", fin.get("close_reason"))
        trade = {
            "time": fin.get("ts_close") or int(_time.time()),
            "instrument": fin.get("instrument"),
            "option": fin.get("option_symbol"),
            "option_type": otype,
            "entry": fin.get("entry_premium"),
            "exit": fin.get("final_premium"),
            "lots": fin.get("lots"),
            # The journal's P&L column is net of the round trip when the leg could
            # be costed. A gross figure here is what made 20-second legs look
            # survivable.
            "net_pnl": (fin.get("net_rupees") if fin.get("net_rupees") is not None
                        else fin.get("final_rupees")),
            "win": bool(fin.get("net_win") if fin.get("net_win") is not None
                        else fin.get("win")),
            "holding_minutes": int(dur / 60) if isinstance(dur, int) else None,
            "mode": "paper",
            "auto": True,
            "engine": "flow",
            "exit_reason": reason,
            "confidence": None,
            # Flow keeps its own book. 4,647 of these legs are in the journal at
            # a median hold of one minute for -Rs164,543, two orders of magnitude
            # more rows than every tagged cohort combined, so any statistic that
            # pools them answers a question about Flow's churn rather than about
            # the setup being studied. Tagged at the source, not inferred from the
            # note text, so a reader can separate the books with certainty.
            "book": _books.FLOW,
            "context": {"engine": "flow", "side": side, "book": _books.FLOW},
        }
        _storage.store.record_journal(
            fin.get("instrument") or "", trade, _analyst.note_for_trade(trade)
        )
    except Exception:
        pass


def update(
    instrument: str,
    sig: FlowSignal,
    chain: list[OptionQuote],
    candles: list[Candle],
    spot: float,
    frozen_buy_same_side: bool,
    now: int,
    rec: dict | None,
    allow_open: bool = True,
) -> dict | None:
    """Advance the flow tracker one tick. Returns the open record (or None once
    closed & written). Pure/best-effort — never raises.

    ``allow_open`` gates opening a NEW paper leg: when False (e.g. the instrument
    is not enabled in the Watchlist) no new leg is opened, but any already-open
    leg is still managed to its EXIT so it is never orphaned."""
    try:
        if rec is None:
            if allow_open and sig.state in ("BUY", "SWITCH") and sig.side is not None:
                return _open_for_side(instrument, sig.side, chain, candles, spot, now)
            if sig.economics_verdict == flow_economics.REFUSED:
                _log_refusal(instrument, sig, candles, spot, now)
            return None

        # Advance premium travel on the currently-tracked leg.
        prem = _leg_premium(chain, rec["option_symbol"])
        if prem is not None:
            sh = rec.get("shadow_exit")
            if isinstance(sh, dict):
                flow_shadow.step(sh, float(rec["entry_premium"]),
                                 float(rec["peak_premium"]), float(prem), now)
            rec["last_premium"] = round(prem, 2)
            if prem > rec["peak_premium"]:
                rec["peak_premium"] = round(prem, 2)
                rec["ts_max"] = int(now)

        if rec.get("agreement") == "PENDING" and frozen_buy_same_side:
            rec["agreement"] = "CONFIRMED"
            rec["confirm_delay_candles"] = _candles_since(candles, rec.get("open_ctime"))
            rec["ts_confirm"] = int(now)

        timed_out = int(now) - rec["ts_open"] >= _MAX_OPEN_SECONDS

        if sig.state == "SWITCH":
            fin = _finalize(rec, "SWITCH", now)
            _append(fin)
            _record_journal(fin)
            # Immediately open the new side we switched into (only if still enabled).
            if allow_open and sig.side is not None:
                return _open_for_side(instrument, sig.side, chain, candles, spot, now)
            return None
        if sig.state == "EXIT" or timed_out:
            fin = _finalize(rec, "TIME" if timed_out and sig.state != "EXIT" else "EXIT", now)
            _append(fin)
            _record_journal(fin)
            return None
        return rec
    except Exception:
        return rec


def _read_all() -> list[dict]:
    path = _log_path()
    if not _os.path.exists(path):
        return []
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(_json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out


def _cost_block(total: int, costed: int, measured: int, gross_points: float,
                cost_points: float, cost_rupees: float, net_wins: int) -> dict:
    """What the round trips cost, next to what the legs actually earned.

    The comparison that matters is cost per leg against *gross* edge per leg: an
    engine whose average leg gains less than it pays to open and close is losing
    to its own trade count, and no signal improvement can reach that. It is
    reported as a ratio so the verdict does not depend on the size of the book.
    """
    if not costed:
        return {
            "costed_legs": 0,
            "coverage_pct": 0.0,
            "verdict": "NOT_COSTED",
            "note": ("No leg carried an entry premium, so the round trip cannot be "
                     "priced. This is not the same as a free trade."),
        }
    gross_per_leg = gross_points / costed
    cost_per_leg = cost_points / costed
    ratio = (round(cost_per_leg / abs(gross_per_leg), 2)
             if abs(gross_per_leg) > 1e-9 else None)
    if gross_per_leg <= 0:
        verdict = "GROSS_NEGATIVE"
    elif cost_per_leg >= gross_per_leg:
        verdict = "COST_EXCEEDS_EDGE"
    elif cost_per_leg >= 0.5 * gross_per_leg:
        verdict = "COST_HALVES_EDGE"
    else:
        verdict = "COST_COVERED"
    return {
        "costed_legs": costed,
        "coverage_pct": round(100.0 * costed / max(1, total), 1),
        "measured_spread_legs": measured,
        "assumed_spread_legs": costed - measured,
        "cost_points": round(cost_points, 1),
        "cost_rupees": round(cost_rupees, 0),
        "gross_points": round(gross_points, 1),
        "net_points": round(gross_points - cost_points, 1),
        "cost_per_leg_points": round(cost_per_leg, 2),
        "gross_per_leg_points": round(gross_per_leg, 2),
        "cost_to_gross_edge": ratio,
        "net_win_rate": round(net_wins / costed * 100.0, 0),
        "verdict": verdict,
        "note": ("Spread is measured where the feed quoted a book and taken from "
                 "the family median otherwise; brokerage and statutory charges "
                 "are computed from each leg's own premiums."),
    }


def summary(limit: int = 200, active: set[str] | None = None,
            session: str | None = None) -> dict:
    """Per-instrument flow performance: win rate, average hold time, profit
    factor and false-signal rate. Read-only, data-gated (empty until real signals
    log). No fabricated statistics.

    ``session`` narrows the report to one IST trading date, which is what the
    live board asks for: yesterday's paper legs are history, not today's book.

    When ``active`` is given, only paper legs for those instruments are counted,
    so the report reflects the currently-enabled Watchlist rather than every
    instrument ever traded."""
    rows = _read_all()
    if active is not None:
        rows = [r for r in rows if (r.get("instrument") or "").upper() in active]
    # Every row is dated, including legs logged before the field existed, so a
    # single-session view can never quietly count an undated old trade.
    for r in rows:
        if not r.get("session"):
            r["session"] = _session_of(r.get("ts_open"))
        if not r.get("open_time_ist"):
            r["open_time_ist"] = _ist_time(r.get("ts_open"))
        if not r.get("close_time_ist"):
            r["close_time_ist"] = _ist_time(r.get("ts_close"))
        # Legs logged before costs were charged are costed here from their own
        # stored premiums, so the history reads at its true size instead of
        # staying flattered by the version of the code that wrote it.
        if r.get("net_points") is None and r.get("final_points") is not None:
            lots = max(1, int(r.get("lots") or settings.flow_paper_lots))
            ls = int(r.get("lot_size") or _lot_size(r.get("instrument") or ""))
            r.update(_cost_fields(r.get("instrument") or "", r.get("entry_premium"),
                                  r.get("final_premium"), ls, lots,
                                  r.get("entry_spread")))
    sessions = sorted({str(r["session"]) for r in rows if r.get("session")})
    if session:
        rows = [r for r in rows if r.get("session") == session]
    by_inst: dict[str, dict] = {}
    wins = 0
    losses = 0
    gross_win = 0.0
    gross_loss = 0.0
    gross_rupees = 0.0
    hold_sum = 0
    hold_n = 0
    net_rupees = 0.0
    cost_rupees = 0.0
    cost_points = 0.0
    gross_points = 0.0
    net_wins = 0
    costed_legs = 0
    measured_spreads = 0
    for r in rows:
        inst = r.get("instrument") or "?"
        agg = by_inst.setdefault(
            inst,
            {
                "instrument": inst,
                "signals": 0,
                "wins": 0,
                "switched": 0,
                "sum_final_pts": 0.0,
                "sum_rupees": 0.0,
                "sum_hold": 0,
                "best_pts": None,
                "sum_net_pts": 0.0,
                "sum_cost_pts": 0.0,
                "sum_net_rupees": 0.0,
                "sum_cost_rupees": 0.0,
                "net_wins": 0,
                "costed": 0,
            },
        )
        agg["signals"] += 1
        fp = r.get("final_points")
        rup = r.get("final_rupees")
        won = bool(r.get("win"))
        if won:
            agg["wins"] += 1
            wins += 1
        if r.get("close_reason") == "SWITCH":
            agg["switched"] += 1
        if isinstance(rup, (int, float)):
            agg["sum_rupees"] += rup
            gross_rupees += rup
        np_, cp = r.get("net_points"), r.get("cost_points")
        if isinstance(np_, (int, float)) and isinstance(cp, (int, float)):
            costed_legs += 1
            agg["costed"] += 1
            gross_points += float(fp or 0.0)
            cost_points += float(cp)
            agg["sum_net_pts"] += float(np_)
            agg["sum_cost_pts"] += float(cp)
            if np_ > 0:
                net_wins += 1
                agg["net_wins"] += 1
            nr, cr = r.get("net_rupees"), r.get("cost_rupees")
            if isinstance(nr, (int, float)):
                net_rupees += float(nr)
                agg["sum_net_rupees"] += float(nr)
            if isinstance(cr, (int, float)):
                cost_rupees += float(cr)
                agg["sum_cost_rupees"] += float(cr)
            if r.get("cost_spread_source") == _costs.MEASURED:
                measured_spreads += 1
        if fp is not None:
            agg["sum_final_pts"] += fp
            if agg["best_pts"] is None or fp > agg["best_pts"]:
                agg["best_pts"] = fp
            if fp > 0:
                gross_win += fp
            else:
                gross_loss += -fp
                if not won:
                    losses += 1
        d = r.get("duration_sec")
        if isinstance(d, int):
            agg["sum_hold"] += d
            hold_sum += d
            hold_n += 1

    instruments = []
    for agg in by_inst.values():
        n = max(1, agg["signals"])
        instruments.append(
            {
                "instrument": agg["instrument"],
                "signals": agg["signals"],
                "win_rate": round(agg["wins"] / n * 100.0, 0),
                "switch_rate": round(agg["switched"] / n * 100.0, 0),
                "avg_final_pts": round(agg["sum_final_pts"] / n, 1),
                # ``net_rupees`` is now net of the round trip. The gross figure it
                # used to hold is kept beside it under its right name so the two
                # can be compared instead of one being mistaken for the other.
                "gross_rupees": round(agg["sum_rupees"], 0),
                "net_rupees": (round(agg["sum_net_rupees"], 0)
                               if agg["costed"] else None),
                "cost_rupees": (round(agg["sum_cost_rupees"], 0)
                                if agg["costed"] else None),
                "avg_net_pts": (round(agg["sum_net_pts"] / agg["costed"], 2)
                                if agg["costed"] else None),
                "avg_cost_pts": (round(agg["sum_cost_pts"] / agg["costed"], 2)
                                 if agg["costed"] else None),
                "net_win_rate": (round(agg["net_wins"] / agg["costed"] * 100.0, 0)
                                 if agg["costed"] else None),
                "costed_legs": agg["costed"],
                "avg_hold_min": round(agg["sum_hold"] / n / 60.0, 1),
                "best_pts": agg["best_pts"],
            }
        )
    instruments.sort(key=lambda x: x["signals"], reverse=True)

    total = len(rows)
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else None
    recent = sorted(rows, key=lambda r: r.get("ts_close", 0), reverse=True)[:limit]
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / max(1, total) * 100.0, 0),
        "profit_factor": profit_factor,
        "avg_hold_min": round(hold_sum / hold_n / 60.0, 1) if hold_n else None,
        "false_signal_rate": round(losses / max(1, total) * 100.0, 0),
        # Headline P&L is net of brokerage, statutory charges and the spread. The
        # gross figure is published beside it, not instead of it: the gap between
        # the two is what a high leg count actually costs.
        "net_rupees": round(net_rupees, 0) if costed_legs else None,
        "gross_rupees": round(gross_rupees, 0),
        "costs": _cost_block(total, costed_legs, measured_spreads, gross_points,
                             cost_points, cost_rupees, net_wins),
        "instruments": instruments,
        "recent": recent,
        # Which day these numbers are, and every day the log holds. None here
        # means all recorded sessions, not "today".
        "session": session,
        "sessions": sessions,
        "as_of": int(_time.time()),
    }
