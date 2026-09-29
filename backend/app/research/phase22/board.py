"""CORE A+ PAPER — the live board for the frozen setup. Paper only.

One row per instrument the scan reached, carrying the same frozen definition the
study measures, the vehicle it would trade, and what that paper position is
doing now. When nothing qualifies the row says NO_TRADE, and NO_TRADE is a
first-class answer here: an empty board on a day with no setup is the board
working, not the board broken.

What this module may not do, by construction:

* it never places an order, live or simulated-through-the-broker path;
* it never reads back into the production decision — the Decision handed in is
  used, never modified, and nothing computed here returns to the engine;
* it never invents a price. A vehicle without a two-sided book is UNMEASURED and
  cannot be selected, however attractive its last traded premium looks.

Entry is the ASK and exit is the BID, both from the book on the signal's own
tick. The paper P&L on this board is therefore what the round trip would have
kept, not what a mid-to-mid screenshot would have shown.
"""
from __future__ import annotations

import threading
import time
from statistics import median

from app.analysis import option_costs
from app.config import settings
from app.models import Candle, Decision, OptionQuote
from app.research.phase14.replay_spot import NOISE_BARS
from app.research.phase19 import futcosts
from app.research.phase22 import definition as defn

CALL = "CE"
PUT = "PE"
FUTURES = "FUTURES"
NO_TRADE = "NO_TRADE"

WAIT = "WAIT"
BUY = "BUY"
HOLD = "HOLD"
PROTECT = "PROTECT"
EXIT = "EXIT"
T1 = "T1"
T2 = "T2"
T3 = "T3"
SL = "SL"
TIMEOUT = "TIMEOUT"
STATUSES = (WAIT, BUY, HOLD, PROTECT, EXIT, T1, T2, T3, SL, TIMEOUT)

MEASURED = "MEASURED"
UNMEASURED = "UNMEASURED"

LONG = "LONG"
SHORT = "SHORT"

# A paper position is timed out rather than left open indefinitely: an entry
# whose thesis was a continuation over minutes is not still that trade an hour
# later, and leaving it open flatters the book by deferring its loss.
MAX_HOLD_MINUTES = 90

# PROTECT is shown once the position has given back this share of its peak while
# still in profit. Display only — it moves no stop and closes no position.
PROTECT_GIVEBACK = 0.5

_LOCK = threading.Lock()
_ROWS: dict[str, dict] = {}
_OPEN: dict[str, dict] = {}
_CLOSED: list[dict] = []
_MAX_CLOSED = 500


def noise_points(candles: list[Candle]) -> float | None:
    """Median 1-minute range over the last bars — the study's own noise measure.

    Identical in formula to the pool builder's, so the live board and the study
    are refusing on the same number rather than on two similar ones.
    """
    recent = [c for c in candles[-NOISE_BARS:]]
    if not recent:
        return None
    return round(float(median(max(0.0, c.high - c.low) for c in recent)), 3)


def _side(dec: Decision) -> str | None:
    if dec.option_type == "CE":
        return LONG
    if dec.option_type == "PE":
        return SHORT
    if dec.htf_trend == "UP":
        return LONG
    if dec.htf_trend == "DOWN":
        return SHORT
    return None


def setup_row(instrument: str, dec: Decision, regime: str | None,
              candles: list[Candle], spot: float | None) -> dict:
    """The candidate as the frozen definition reads it. Underlying terms only."""
    side = _side(dec)
    trap = dec.buy_trap_prob if side == LONG else dec.sell_trap_prob
    fake = dec.fake_breakout_prob if side == LONG else dec.fake_breakdown_prob
    risk = None
    if (isinstance(spot, (int, float)) and spot
            and isinstance(dec.underlying_stop, (int, float))
            and dec.underlying_stop > 0):
        risk = abs(float(spot) - float(dec.underlying_stop))
    move = dec.expected_move_points
    reward_risk = (round(float(move) / risk, 3)
                   if risk and isinstance(move, (int, float)) and risk > 0
                   else None)
    return {
        "instrument": instrument,
        "side": side,
        "htf_trend": dec.htf_trend,
        "htf_strength": dec.htf_strength,
        "regime": regime,
        "entry_trigger": dec.entry_trigger,
        "trap_prob": trap,
        "fake_prob": fake,
        "conviction_meter": dec.conviction_meter,
        "risk": risk,
        "reward_risk": reward_risk,
        "noise_points": noise_points(candles),
        "expected_move_points": move,
    }


def _book(quote: OptionQuote | None) -> dict | None:
    if quote is None:
        return None
    bid, ask = quote.bid, quote.ask
    if not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)):
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (float(ask) + float(bid)) / 2.0
    return {"bid": round(float(bid), 2), "ask": round(float(ask), 2),
            "spread": round(float(ask) - float(bid), 3),
            "spread_pct": round(100.0 * (float(ask) - float(bid)) / mid, 2)}


def plan_shape(dec: Decision) -> dict | None:
    """The production plan's stop/target distances as shares of its own premium.

    The engine prices stop and targets for ONE option. To compare a second
    option against it those levels have to be expressed in a form that
    transfers, and the only honest transferable form is the shape of the plan:
    stop at x% below the entry premium, T1/T2/T3 at y% above it. The alternative
    vehicle's levels are then that same shape applied to ITS OWN measured ask.

    This is a stated modelling step and it is labelled as one on every row it
    produces (``levels_basis``). It is used for the comparison and for the paper
    board; the study's net numbers come from recorded books, not from here.
    """
    base = dec.plan_premium if isinstance(dec.plan_premium, (int, float)) \
        else dec.current_premium
    if not isinstance(base, (int, float)) or base <= 0:
        return None
    if not isinstance(dec.stop_loss, (int, float)) or dec.stop_loss <= 0:
        return None
    stop_pct = (float(base) - float(dec.stop_loss)) / float(base)
    if stop_pct <= 0:
        return None
    shape = {"stop_pct": round(stop_pct, 6)}
    for key, level in (("t1_pct", dec.target1), ("t2_pct", dec.target2),
                       ("t3_pct", dec.target3)):
        if isinstance(level, (int, float)) and level > 0:
            pct = (float(level) - float(base)) / float(base)
            shape[key] = round(pct, 6) if pct > 0 else None
        else:
            shape[key] = None
    if not shape.get("t1_pct"):
        return None
    return shape


def _levels_for(entry: float, dec: Decision, shape: dict | None,
                own_plan: bool) -> dict:
    """Stop/T1/T2/T3 for one vehicle, and which basis produced them."""
    if own_plan:
        return {"stop": dec.stop_loss, "target1": dec.target1,
                "target2": dec.target2, "target3": dec.target3,
                "levels_basis": "PRODUCTION_PLAN"}
    if not shape:
        return {"stop": None, "target1": None, "target2": None,
                "target3": None, "levels_basis": "NO_PLAN_SHAPE"}

    def _at(pct: float | None, sign: float) -> float | None:
        if not isinstance(pct, (int, float)):
            return None
        return round(entry * (1.0 + sign * float(pct)), 2)

    return {
        "stop": _at(shape["stop_pct"], -1.0),
        "target1": _at(shape.get("t1_pct"), 1.0),
        "target2": _at(shape.get("t2_pct"), 1.0),
        "target3": _at(shape.get("t3_pct"), 1.0),
        "levels_basis": "PLAN_SHAPE_APPLIED_TO_OWN_ASK",
    }


def _vehicle(name: str, symbol: str | None, quote: OptionQuote | None,
             dec: Decision, lot_size: int, *, shape: dict | None = None,
             own_plan: bool = True, wrong_side: bool = False) -> dict:
    """One vehicle's executable economics, or a stated reason it has none."""
    book = _book(quote)
    row = {
        "vehicle": name,
        "symbol": symbol,
        "status": MEASURED if book else UNMEASURED,
        "bid": book["bid"] if book else None,
        "ask": book["ask"] if book else None,
        "spread": book["spread"] if book else None,
        "spread_pct": book["spread_pct"] if book else None,
        "entry": book["ask"] if book else None,
        "premium": quote.premium if quote else None,
        "reasons": [] if book else ["NO_TWO_SIDED_BOOK"],
        "pricing": "ASK_IN_BID_OUT",
    }
    if wrong_side:
        row["reasons"].append("WRONG_VEHICLE_FOR_DIRECTION")
    if not book:
        return row
    entry = float(book["ask"])
    levels = _levels_for(entry, dec, shape, own_plan)
    row.update(levels)
    stop = levels["stop"]
    target1 = levels["target1"]
    if target1 is None or stop is None:
        row["reasons"].append("NO_TRANSFERABLE_PLAN")
        return row
    charges = option_costs.charges(entry, target1, lot_size)
    slip = 2.0 * entry * float(settings.ai_slippage_pct) / 100.0
    cost_points = float(book["spread"]) + slip + (
        charges.total / lot_size if lot_size else 0.0)
    row["cost_points"] = round(cost_points, 3)
    row["cost_rupees"] = round(charges.total + (float(book["spread"]) + slip)
                               * lot_size, 2)
    row["brokerage"] = charges.brokerage
    row["statutory"] = charges.statutory
    row["slippage_points"] = round(slip, 3)
    if isinstance(target1, (int, float)) and target1 > 0:
        room = float(target1) - entry
        row["room_points"] = round(room, 3)
        row["room_after_cost"] = round(room - cost_points, 3)
        row["cost_over_room"] = (round(cost_points / room, 3)
                                 if room > 0 else None)
    if isinstance(stop, (int, float)) and stop > 0:
        risk = entry - float(stop)
        row["risk_points"] = round(risk, 3)
        row["cost_over_risk"] = (round(cost_points / risk, 3)
                                 if risk > 0 else None)
        if risk <= 0:
            row["reasons"].append("STOP_AT_OR_ABOVE_ENTRY")
    return row


def net_rr(v: dict) -> float | None:
    """Room left after this vehicle's own cost, in units of its own risk.

    The comparison metric across vehicles, and it has to be this rather than
    room in points: an option's points are premium and a futures contract's are
    underlying, so ranking the two on points would compare rupees with index
    levels. Dividing each by its own risk makes them the same unit.
    """
    room = v.get("room_after_cost")
    risk = v.get("risk_points")
    if not isinstance(room, (int, float)) or not isinstance(risk, (int, float)):
        return None
    if risk <= 0:
        return None
    return round(float(room) / float(risk), 3)


def _viable(v: dict) -> bool:
    """A vehicle worth paper-trading: measured, and the room outlives the cost."""
    if v["status"] != MEASURED or v.get("reasons"):
        return False
    rr = net_rr(v)
    room = v.get("room_after_cost")
    return (isinstance(rr, (int, float)) and rr > 0
            and isinstance(room, (int, float)) and room > 0)


def choose(vehicles: list[dict]) -> tuple[dict | None, list[str]]:
    """Best vehicle by net reward:risk after its own cost, else NO_TRADE."""
    for v in vehicles:
        v["net_rr_after_cost"] = net_rr(v)
    viable = [v for v in vehicles if _viable(v)]
    if not viable:
        reasons = [f"{v['vehicle']}_{(v['reasons'] or ['NO_ROOM_AFTER_COST'])[0]}"
                   for v in vehicles] or ["NO_VEHICLE_QUOTED"]
        return None, reasons
    best = max(viable, key=lambda v: float(v["net_rr_after_cost"]))
    reasons = [f"{best['vehicle']}_NET_RR_{best['net_rr_after_cost']}"]
    for v in vehicles:
        if v is best:
            continue
        why = (v["reasons"] or [f"NET_RR_{v.get('net_rr_after_cost')}"])[0]
        reasons.append(f"{v['vehicle']}_{why}")
    return best, reasons


def nearest(chain: list[OptionQuote], option_type: str,
            strike: float | None) -> OptionQuote | None:
    """The quote of the given type closest to ``strike``.

    Used to give the side the engine did NOT recommend a concrete quote, so the
    comparison is CE against PE against futures on real books rather than one
    vehicle against two blanks. Strike distance only — no scoring, no selection
    rule, and nothing here reaches the production strike selector.
    """
    same = [q for q in chain if q.option_type.value == option_type]
    if not same:
        return None
    if not isinstance(strike, (int, float)):
        return None
    return min(same, key=lambda q: abs(float(q.strike) - float(strike)))


def futures_vehicle(instrument: str, book: dict | None, dec: Decision,
                    side: str | None, lot_size: int) -> dict:
    """The futures leg, priced on the executable side when depth was recorded.

    A futures book with a real bid and ask is MEASURED and competes with the
    options on equal terms — entry at the ask to go long, at the bid to go
    short. Without depth the leg is UNMEASURED: its charges can still be
    charged, but its spread cannot be, so it is disclosed and never selected.
    Levels are the underlying plan's own stop and expected move, in points.
    """
    bid = (book or {}).get("bid")
    ask = (book or {}).get("ask")
    two_sided = (isinstance(bid, (int, float)) and isinstance(ask, (int, float))
                 and float(bid) > 0 and float(ask) > 0 and float(ask) >= float(bid))
    row: dict = {
        "vehicle": FUTURES,
        "symbol": (book or {}).get("tradingsymbol"),
        "status": MEASURED if two_sided else UNMEASURED,
        "bid": bid if isinstance(bid, (int, float)) else None,
        "ask": ask if isinstance(ask, (int, float)) else None,
        "premium": (book or {}).get("ltp") if isinstance(book, dict) else None,
        "reasons": [] if two_sided else ["FUTURES_DEPTH_NOT_CAPTURED"],
        "pricing": ("EXECUTABLE_SIDE_RECORDED_BOOK" if two_sided
                    else futcosts.COST_MODELLED),
        "levels_basis": "UNDERLYING_PLAN_POINTS",
    }
    if not two_sided:
        return row
    spread = float(ask) - float(bid)
    entry = float(ask) if side != SHORT else float(bid)
    stop = dec.underlying_stop
    move = dec.expected_move_points
    row.update({"spread": round(spread, 3),
                "spread_pct": round(200.0 * spread / (float(ask) + float(bid)), 3),
                "entry": round(entry, 2)})
    if not isinstance(stop, (int, float)) or stop <= 0 \
            or not isinstance(move, (int, float)) or move <= 0:
        row["reasons"].append("NO_UNDERLYING_PLAN")
        return row
    risk = abs(entry - float(stop))
    target1 = entry + float(move) if side != SHORT else entry - float(move)
    costs = futcosts.round_trip(instrument, entry=entry, exit_price=target1,
                                lot_size=lot_size, spread_points=spread)
    cost_points = costs["cost_points"]
    if not isinstance(cost_points, (int, float)):
        row["reasons"].append("COST_NOT_CHARGEABLE")
        return row
    room = abs(target1 - entry)
    row.update({
        "stop": round(float(stop), 2),
        "target1": round(target1, 2),
        "target2": None, "target3": None,
        "cost_points": cost_points,
        "cost_rupees": costs["cost_rupees"],
        "brokerage": costs["brokerage_rupees"],
        "statutory": round(float(costs["tax_rupees"])
                           + float(costs["txn_rupees"]), 2),
        "slippage_points": costs["slippage_points"],
        "room_points": round(room, 3),
        "room_after_cost": round(room - float(cost_points), 3),
        "cost_over_room": (round(float(cost_points) / room, 3)
                           if room > 0 else None),
        "risk_points": round(risk, 3),
        "cost_over_risk": (round(float(cost_points) / risk, 3)
                           if risk > 0 else None),
        "cost_status": costs["cost_status"],
    })
    if risk <= 0:
        row["reasons"].append("STOP_AT_OR_ABOVE_ENTRY")
    return row


def _exit_mark(ep: dict, quotes: dict[str, OptionQuote],
               futures_book: dict | None) -> float | None:
    """What this open paper leg could be closed at right now, exit side only.

    A long option is marked at the BID, a short futures leg at the ASK it would
    have to pay to buy back. When the exit side is not quoted the leg is left
    untouched for this tick rather than marked at a price nobody was showing.
    """
    if ep["vehicle"] == FUTURES:
        if not isinstance(futures_book, dict):
            return None
        want = "ask" if ep.get("side") == SHORT else "bid"
        value = futures_book.get(want)
        return (float(value) if isinstance(value, (int, float)) and value > 0
                else None)
    quote = quotes.get(str(ep["symbol"]))
    if quote is None:
        return None
    return (float(quote.bid) if isinstance(quote.bid, (int, float))
            and quote.bid > 0 else None)


def _status_of(ep: dict, last: float) -> str:
    entry = float(ep["entry"])
    short = ep.get("side") == SHORT
    sign = -1.0 if short else 1.0
    stop, t1, t2, t3 = ep["stop"], ep["target1"], ep["target2"], ep["target3"]
    if isinstance(stop, (int, float)) and sign * (last - float(stop)) <= 0:
        return SL
    for level, name in ((t3, T3), (t2, T2), (t1, T1)):
        if isinstance(level, (int, float)) and sign * (last - float(level)) >= 0:
            return name
    if (time.time() - float(ep["entry_ts"])) / 60.0 >= MAX_HOLD_MINUTES:
        return TIMEOUT
    # In favour-of-the-trade terms, so a short gives back from its own low.
    best_seen = float(ep["trough"] if short else ep["peak"])
    gain_peak = sign * (best_seen - entry)
    gain_now = sign * (last - entry)
    if gain_peak > 0 and gain_now > 0 and \
            (gain_peak - gain_now) >= PROTECT_GIVEBACK * gain_peak:
        return PROTECT
    return HOLD


def _closing(status: str) -> bool:
    return status in (T1, T2, T3, SL, TIMEOUT, EXIT)


def vehicle_set(instrument: str, dec: Decision, chain: list[OptionQuote],
                lot_size: int, side: str | None,
                futures_book: dict | None) -> list[dict]:
    """CE, PE and FUTURES for this tick, each on its own measured book.

    The engine's recommended option keeps the production plan's own levels; the
    opposite side is quoted at the nearest strike and carries the plan's shape
    applied to its own ask, marked as such. The side that cannot express the
    direction is priced anyway and refused with WRONG_VEHICLE_FOR_DIRECTION, so
    the board can show *why* it was the wrong vehicle rather than omitting it.
    """
    quotes = {q.symbol: q for q in chain}
    recommended = quotes.get(dec.recommended_option or "")
    shape = plan_shape(dec)
    strike = dec.strike if isinstance(dec.strike, (int, float)) else dec.atm_strike
    out: list[dict] = []
    for name, opt_type in ((CALL, "CE"), (PUT, "PE")):
        own = recommended is not None and dec.option_type == opt_type
        quote = recommended if own else nearest(chain, opt_type, strike)
        if quote is None:
            continue
        wrong = (side == LONG and opt_type == "PE") or \
                (side == SHORT and opt_type == "CE")
        out.append(_vehicle(name, quote.symbol, quote, dec, lot_size,
                            shape=shape, own_plan=own, wrong_side=wrong))
    if isinstance(futures_book, dict):
        out.append(futures_vehicle(instrument, futures_book, dec, side,
                                   lot_size))
    return out


def observe(instrument: str, dec: Decision, regime: str | None,
            candles: list[Candle], spot: float | None,
            chain: list[OptionQuote], lot_size: int,
            futures_book: dict | None = None) -> dict:
    """Evaluate one instrument on this tick and advance its paper position.

    Returns the board row. Never raises into the caller's tick: the service
    wrapper catches, and a research row is not worth a live tick.
    """
    row = setup_row(instrument, dec, regime, candles, spot)
    refusals = defn.refusals(row)
    quotes = {q.symbol: q for q in chain}
    side = row["side"]
    vehicles = vehicle_set(instrument, dec, chain, lot_size, side, futures_book)
    best, reasons = choose(vehicles)
    setup_ok = not refusals
    signal_ok = str(dec.signal).upper() == "BUY"

    out = {
        "instrument": instrument,
        "direction": side,
        "setup": defn.LABEL if setup_ok else defn.OTHER,
        "setup_refusals": refusals,
        "definition": defn.VERSION,
        "vehicle": best["vehicle"] if best else NO_TRADE,
        "vehicles": vehicles,
        "vehicle_reasons": reasons,
        "entry": best.get("entry") if best else None,
        "stop": best.get("stop") if best else dec.stop_loss,
        "target1": best.get("target1") if best else dec.target1,
        "target2": best.get("target2") if best else dec.target2,
        "target3": best.get("target3") if best else dec.target3,
        "levels_basis": best.get("levels_basis") if best else None,
        "net_rr_after_cost": best.get("net_rr_after_cost") if best else None,
        "current": best.get("premium") if best else None,
        "spread": best.get("spread") if best else None,
        "spread_pct": best.get("spread_pct") if best else None,
        "room_points": best.get("room_points") if best else None,
        "room_after_cost": best.get("room_after_cost") if best else None,
        "expected_move_points": row["expected_move_points"],
        "t1_rank": dec.trade_score,
        "t1_rank_basis": "TRADE_SCORE_RANK_NOT_A_PROBABILITY",
        "expected_hold_minutes": dec.expected_holding_minutes,
        "noise_points": row["noise_points"],
        "status": WAIT,
        "paper_pnl": None,
        "mfe": None,
        "mae": None,
        "paper_only": True,
        "no_real_order": True,
        "updated_ts": int(time.time()),
    }

    with _LOCK:
        ep = _OPEN.get(instrument)
        if ep is not None:
            last = _exit_mark(ep, quotes, futures_book)
            if last is not None:
                ep["last"] = last
                ep["peak"] = max(float(ep["peak"]), last)
                ep["trough"] = min(float(ep["trough"]), last)
                status = _status_of(ep, last)
                ep["status"] = status
                entry = float(ep["entry"])
                sign = -1.0 if ep.get("side") == SHORT else 1.0
                out.update({
                    "vehicle": ep["vehicle"], "symbol": ep["symbol"],
                    "entry": entry, "current": last, "status": status,
                    "stop": ep["stop"], "target1": ep["target1"],
                    "target2": ep["target2"], "target3": ep["target3"],
                    "paper_pnl": round(sign * (last - entry)
                                       * float(ep["lot_size"]), 2),
                    "mfe": round(sign * ((ep["peak"] if sign > 0
                                          else ep["trough"]) - entry), 3),
                    "mae": round(sign * ((ep["trough"] if sign > 0
                                          else ep["peak"]) - entry), 3),
                    "held_minutes": round((time.time() - float(ep["entry_ts"]))
                                          / 60.0, 1),
                })
                if _closing(status):
                    ep["exit"] = last
                    ep["exit_ts"] = time.time()
                    ep["net_rupees"] = round(
                        sign * (last - entry) * float(ep["lot_size"])
                        - float(ep.get("cost_rupees") or 0.0), 2)
                    _CLOSED.append(dict(ep))
                    del _CLOSED[:-_MAX_CLOSED]
                    _OPEN.pop(instrument, None)
        elif setup_ok and signal_ok and best is not None:
            entry = float(best["entry"])
            ep = {
                "instrument": instrument, "vehicle": best["vehicle"],
                "symbol": best["symbol"], "entry": entry, "last": entry,
                "peak": entry, "trough": entry, "stop": best.get("stop"),
                "target1": best.get("target1"), "target2": best.get("target2"),
                "target3": best.get("target3"), "lot_size": lot_size,
                "side": SHORT if (best["vehicle"] == FUTURES and side == SHORT)
                        else LONG,
                "entry_ts": time.time(), "status": BUY,
                "entry_spread": best.get("spread"),
                "levels_basis": best.get("levels_basis"),
                "cost_rupees": best.get("cost_rupees"),
                "definition": defn.VERSION,
                "paper_only": True, "no_real_order": True,
            }
            _OPEN[instrument] = ep
            out.update({"status": BUY, "symbol": best["symbol"],
                        "paper_pnl": 0.0, "mfe": 0.0, "mae": 0.0})
        _ROWS[instrument] = out
    return out


def rows() -> list[dict]:
    with _LOCK:
        return sorted(_ROWS.values(), key=lambda r: str(r["instrument"]))


def board() -> dict:
    """The CORE A+ PAPER payload. NO_TRADE when nothing qualifies."""
    current = rows()
    trading = [r for r in current if r["vehicle"] != NO_TRADE]
    with _LOCK:
        closed = list(_CLOSED)
        open_n = len(_OPEN)
    resolved = [r for r in closed
                if isinstance(r.get("net_rupees"), (int, float))]
    net = sum(float(r["net_rupees"]) for r in resolved)
    return {
        "definition": defn.VERSION,
        "definition_fingerprint": defn.fingerprint(),
        "rows": current,
        "no_trade": not trading,
        "candidates": len(current),
        "selected": len(trading),
        "open": open_n,
        "closed": len(closed),
        "net_paper_rupees": round(net, 2),
        "statuses": list(STATUSES),
        "paper_only": True,
        "no_real_order": True,
        "promotion": "manual review only; this board cannot place an order",
    }


def closed_rows() -> list[dict]:
    with _LOCK:
        return list(_CLOSED)


def reset() -> None:
    with _LOCK:
        _ROWS.clear()
        _OPEN.clear()
        _CLOSED.clear()
