"""Live paper executor and position manager for the AI engine.

Paper only, structurally. This module has no import path to a broker order API:
it never touches ``provider.place_order``, and the one real-order function in the
process refuses while paper mode is on anyway (:mod:`app.ai.safety`). What it does
use the live feed for is the thing that makes paper trading worth anything — real
current prices, real spreads where the feed carries them, and real time.

Two decisions worth stating because they change the numbers:

* **Fills are pessimistic.** An entry crosses the spread (buys the ask) when the
  feed gives top-of-book, and both sides pay slippage as a % of premium.
  Brokerage is charged per lot per side. A paper book that fills at the mid and
  ignores costs is the easiest way to manufacture an edge that evaporates live.
* **A bar that could have hit both stop and target counts as a stop.** Same rule
  as the research, for the same reason: tick data would be needed to know which
  came first, and assuming the win is how a backtest flatters itself.

Every entry must pass the deterministic pre-trade checks in :func:`pretrade_checks`.
Any single failure means NO PAPER TRADE — the checks are not scored or weighted.
"""
from __future__ import annotations

import time

from app.ai import journal as aij
from app.analysis import option_costs
from app.config import settings
from app.market.instruments import get_spec
from app.models import OptionType

# Exit reasons (the Phase 6 vocabulary).
TARGET = "TARGET"
STOP = "STOP"
TIMEOUT = "TIMEOUT"
AI_EXIT = "AI_EXIT"
TREND_REVERSAL = "TREND_REVERSAL"
MOMENTUM_FAILURE = "MOMENTUM_FAILURE"
DATA_FAILURE = "DATA_FAILURE"
RISK_LIMIT = "RISK_LIMIT"

NO_PAPER_TRADE = "NO_PAPER_TRADE"
PAPER_ENTERED = "PAPER_ENTERED"

_MIN_REWARD_RISK = 1.2      # matches ai_t1_r; a trade must be able to pay for itself
_DATA_FAILURE_SEC = 120.0   # no usable quote for this long => close on data failure


# --------------------------------------------------------------- contracts
def select_contract(chain: list, spot: float, side: str):
    """Nearest-the-money contract of the requested side.

    ATM, deliberately. The Phase 3 audit scored 25 strikes per BUY and found the
    entire expectancy range across ATM±12 was 0.035R–0.054R, with ATM/ATM+1 at
    the top on both instruments — and that a five-line "always ATM" rule matched
    the full delta-fitting selector to within 0.001R. With no real OI, IV or
    spread in the archive there is no evidence that would justify anything more
    elaborate, and the production selector is not touched by this choice.
    """
    want = OptionType.CALL if side.upper() == "CE" else OptionType.PUT
    legs = [q for q in chain if q.option_type == want and (q.premium or 0) > 0]
    if not legs or not spot:
        return None
    return min(legs, key=lambda q: abs(q.strike - spot))


def _fill_price(quote, buying: bool) -> tuple[float, float | None]:
    """(fill, spread) for a paper fill. Crosses the spread when top-of-book exists."""
    px = float(quote.premium or 0.0)
    spread = None
    if settings.ai_cross_spread and quote.bid and quote.ask and quote.ask > quote.bid > 0:
        spread = float(quote.ask - quote.bid)
        px = float(quote.ask if buying else quote.bid)
    slip = px * settings.ai_slippage_pct / 100.0
    fill = px + slip if buying else max(0.05, px - slip)
    return round(fill, 2), (round(spread, 2) if spread is not None else None)


def _costs(lots: int) -> float:
    """Brokerage on the round trip. Flat per ORDER — the lot count does not
    change what the broker bills for one order, and charging it per lot put 40x
    the true brokerage on a 39-lot leg."""
    del lots  # kept in the signature: every call site passes it.
    return option_costs.charges(None, None, 0).brokerage


# ------------------------------------------------------------ pre-trade gates
def pretrade_checks(instrument: str, side: str, quote, feats: dict,
                    feed_state: str, data_age_ms: float | None,
                    probability: float | None) -> list[str]:
    """Deterministic entry checks. Returns the list of FAILED check names.

    Non-empty means NO PAPER TRADE. No check can be overridden, weighted or
    traded off against a high probability.
    """
    j = aij.journal()
    fails: list[str] = []

    if not settings.paper_mode:
        fails.append("PAPER_MODE_OFF")
    if not settings.ai_paper_enabled:
        fails.append("AI_PAPER_DISABLED")
    if feed_state not in ("FRESH", "AGING"):
        fails.append(f"FEED_{feed_state or 'NO_DATA'}")
    if data_age_ms is not None and data_age_ms > settings.ai_max_data_age_ms:
        fails.append("DATA_STALE")
    try:
        spec = get_spec(instrument)
    except Exception:
        spec = None
    if spec is None:
        fails.append("INVALID_INSTRUMENT")
    else:
        is_open, _ = settings.market_session(time.time(), spec.exchange)
        if not (is_open or settings.ignore_market_hours):
            fails.append("MARKET_CLOSED")
    if quote is None:
        fails.append("NO_CONTRACT")
    elif not (quote.premium and quote.premium > 0):
        fails.append("INVALID_PRICE")
    if not feats or not feats.get("_atr"):
        fails.append("NO_ATR")
    if probability is None:
        fails.append("NO_PROBABILITY")
    elif probability < settings.ai_min_probability:
        fails.append("PROBABILITY_BELOW_THRESHOLD")

    if j.has_open(instrument, side.upper()):
        fails.append("DUPLICATE_POSITION")
    if len(j.open_trades()) >= settings.ai_max_open_positions:
        fails.append("POSITION_LIMIT")

    today = j.trades_today(aij.day_start_ts())
    if len(today) >= settings.ai_max_trades_per_day:
        fails.append("TRADE_COUNT_LIMIT")
    day_pnl = sum(float(t.get("realized_pnl") or 0.0) for t in today)
    if day_pnl <= -abs(settings.ai_max_daily_loss):
        fails.append("DAILY_LOSS_LIMIT")

    return fails


def _levels(entry: float, quote, feats: dict, side: str) -> dict | None:
    """Stop/targets on the PREMIUM, derived from the underlying stop.

    The underlying stop is ``ai_stop_atr`` ATR (the geometry every Phase 5 number
    was measured on). It is converted to a premium distance through the leg's
    delta, so the paper trade risks what the research says it risks. Delta comes
    from the feed when present; 0.5 is used for an ATM leg when it does not, and
    the caller records which.
    """
    atr = float(feats.get("_atr") or 0.0)
    if not atr or entry <= 0:
        return None
    feed_delta = abs(float(quote.delta or 0.0))
    delta = feed_delta or 0.5
    underlying_stop = settings.ai_stop_atr * atr
    prem_risk = max(0.05, underlying_stop * delta)
    if prem_risk >= entry:
        # A stop below zero premium is not a stop. Refused rather than clamped.
        return None
    return {
        "delta_used": round(delta, 3),
        "delta_source": "FEED" if feed_delta else "ASSUMED_0.5",
        "underlying_stop": round(underlying_stop, 2),
        "premium_risk": round(prem_risk, 2),
        "stop": round(entry - prem_risk, 2),
        "target1": round(entry + settings.ai_t1_r * prem_risk, 2),
        "target2": round(entry + settings.ai_t2_r * prem_risk, 2),
        "target3": round(entry + settings.ai_t3_r * prem_risk, 2),
    }


def _size(prem_risk: float, lot_size: int) -> int:
    allowance = settings.ai_paper_capital * settings.ai_risk_per_trade_pct / 100.0
    per_lot = prem_risk * max(1, lot_size)
    if per_lot <= 0:
        return 0
    return int(allowance // per_lot)


# ------------------------------------------------------------------- entry
def open_paper(instrument: str, side: str, quote, feats: dict, feed_state: str,
               data_age_ms: float | None, probability: float | None,
               regime: str, direction: str, entry_quality: str,
               decision_id: str | None = None) -> dict:
    """Attempt a paper entry. Always returns a verdict; never raises to the caller."""
    fails = pretrade_checks(instrument, side, quote, feats, feed_state,
                            data_age_ms, probability)
    if fails:
        return {"result": NO_PAPER_TRADE, "failed_checks": fails}

    entry, spread = _fill_price(quote, buying=True)
    lv = _levels(entry, quote, feats, side)
    if lv is None:
        return {"result": NO_PAPER_TRADE, "failed_checks": ["INVALID_STOP"]}
    rr = (lv["target1"] - entry) / (entry - lv["stop"]) if entry > lv["stop"] else 0.0
    if rr < _MIN_REWARD_RISK - 1e-9:
        return {"result": NO_PAPER_TRADE, "failed_checks": ["MIN_REWARD_RISK"]}

    try:
        spec = get_spec(instrument)
        lot_size = max(1, int(spec.lot_size))
    except Exception:
        lot_size = 1
    lots = _size(lv["premium_risk"], lot_size)
    if lots < 1:
        return {"result": NO_PAPER_TRADE, "failed_checks": ["RISK_LIMIT"]}

    j = aij.journal()
    tid = j.insert_trade({
        "decision_id": decision_id,
        "instrument": instrument,
        "symbol": quote.symbol,
        "side": side.upper(),
        "strike": float(quote.strike),
        "expiry": None,
        "entry_ts": int(time.time()),
        "entry_premium": entry,
        "entry_quote": float(quote.premium),
        "entry_spread": spread,
        "entry_slippage": round(entry - float(quote.premium), 2),
        "underlying_entry": feats.get("_price"),
        "stop": lv["stop"],
        "target1": lv["target1"],
        "target2": lv["target2"],
        "target3": lv["target3"],
        "lots": lots,
        "lot_size": lot_size,
        "risk_amount": round(lv["premium_risk"] * lots * lot_size, 2),
        "probability": probability,
        "regime": regime,
        "direction": direction,
        "entry_quality": entry_quality,
        "status": "OPEN",
        "current_premium": entry,
        "notes": (f"delta {lv['delta_used']} ({lv['delta_source']}), "
                  f"underlying stop {lv['underlying_stop']}"),
    })
    return {"result": PAPER_ENTERED, "trade_id": tid, "entry": entry,
            "lots": lots, "levels": lv, "spread": spread, "failed_checks": []}


# ------------------------------------------------------------- management
def _quote_for(st, symbol: str):
    try:
        chain = st.provider.option_chain()
    except Exception:
        return None
    return next((q for q in chain if q.symbol == symbol), None)


def _close(trade: dict, premium: float, reason: str) -> dict:
    j = aij.journal()
    lots = int(trade.get("lots") or 1)
    lot_size = int(trade.get("lot_size") or 1)
    entry = float(trade.get("entry_premium") or 0.0)
    fill = premium
    gross = (fill - entry) * lots * lot_size
    costs = _costs(lots)
    realized = round(gross - costs, 2)
    risk = float(trade.get("risk_amount") or 0.0)
    r = round(realized / risk, 3) if risk else 0.0
    hold = max(0.0, time.time() - float(trade.get("entry_ts") or time.time()))
    j.close_trade(str(trade["id"]), int(time.time()), round(fill, 2), reason,
                  realized, costs, r, hold)
    return {"trade_id": trade["id"], "exit": round(fill, 2), "reason": reason,
            "realized_pnl": realized, "r_multiple": r, "hold_sec": round(hold, 1)}


def manage(resolve_state) -> list[dict]:
    """Mark every open paper position to the live feed and exit those that must.

    ``resolve_state(instrument)`` returns the cached engine state for an
    instrument (or None). Exit precedence is STOP before TARGET on the same
    observation, matching the research convention.
    """
    j = aij.journal()
    events: list[dict] = []
    for t in j.open_trades():
        inst = str(t["instrument"])
        st = None
        try:
            st = resolve_state(inst)
        except Exception:
            st = None
        q = _quote_for(st, str(t["symbol"])) if st is not None else None

        age = time.time() - float(t.get("entry_ts") or 0)
        if q is None or not (q.premium and q.premium > 0):
            # No usable quote. Held briefly (a gap is normal), then closed on the
            # last known price and labelled DATA_FAILURE rather than silently
            # marked flat — an unmeasurable position is not a flat one.
            if age >= _DATA_FAILURE_SEC:
                last = float(t.get("current_premium") or t.get("entry_premium") or 0.0)
                events.append(_close(t, last, DATA_FAILURE))
            continue

        entry = float(t["entry_premium"])
        lots, lot_size = int(t["lots"] or 1), int(t["lot_size"] or 1)
        stop, tgt1 = float(t["stop"]), float(t["target1"])
        mark, _ = _fill_price(q, buying=False)   # exits pay the bid + slippage
        mfe = max(float(t.get("mfe") or 0.0), (mark - entry))
        mae = min(float(t.get("mae") or 0.0), (mark - entry))
        unreal = round((mark - entry) * lots * lot_size - _costs(lots), 2)
        j.update_live(str(t["id"]), mark, unreal, round(mfe, 2), round(mae, 2),
                      stop, tgt1)

        if mark <= stop:
            events.append(_close(t, max(mark, 0.05), STOP))
            continue
        if mark >= tgt1:
            events.append(_close(t, mark, TARGET))
            continue
        if age >= settings.ai_max_hold_min * 60.0:
            events.append(_close(t, mark, TIMEOUT))
            continue
    return events


def close_paper(trade_id: str, reason: str, resolve_state) -> dict:
    """Close one paper position now, at the live bid (or last known premium)."""
    j = aij.journal()
    t = j.get_trade(trade_id)
    if not t:
        return {"ok": False, "message": "unknown paper trade"}
    if t.get("status") != "OPEN":
        return {"ok": False, "message": "already closed"}
    st = None
    try:
        st = resolve_state(str(t["instrument"]))
    except Exception:
        st = None
    q = _quote_for(st, str(t["symbol"])) if st is not None else None
    if q is not None and q.premium:
        mark, _ = _fill_price(q, buying=False)
    else:
        mark = float(t.get("current_premium") or t.get("entry_premium") or 0.0)
    out = _close(t, mark, reason or AI_EXIT)
    out["ok"] = True
    return out


def pnl_summary() -> dict:
    """Realised + open paper P&L for the AI book."""
    j = aij.journal()
    open_rows = j.open_trades()
    closed = j.closed_trades()
    realized = sum(float(t.get("realized_pnl") or 0.0) for t in closed)
    unreal = sum(float(t.get("unrealized_pnl") or 0.0) for t in open_rows)
    today = j.trades_today(aij.day_start_ts())
    return {
        "paper_mode": settings.paper_mode,
        "capital": settings.ai_paper_capital,
        "open_positions": len(open_rows),
        "closed_trades": len(closed),
        "realized_pnl": round(realized, 2),
        "unrealized_pnl": round(unreal, 2),
        "total_pnl": round(realized + unreal, 2),
        "trades_today": len(today),
        "realized_today": round(
            sum(float(t.get("realized_pnl") or 0.0) for t in today), 2),
        "costs_charged": round(sum(float(t.get("costs") or 0.0) for t in closed), 2),
    }
