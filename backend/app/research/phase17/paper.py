"""A+ paper execution — §22, §12, §24.

What this module is: an accountant. It takes an A+ candidate whose book was fresh
enough to fill against, records an entry at the ASK, follows the leg on recorded
quotes, records an exit at the BID, charges brokerage, statutory fees and modelled
slippage, and writes one JSONL row.

What it is not, and cannot become: an order path. There is no broker client
imported here, no order id, no quantity sent anywhere. The only side effect of a
paper episode is a line in a file that nothing trading reads.

One expectation to set before the first session: net numbers from this module
will look worse than every gross number in the earlier reports, and that is the
point. The measured sample said a Rs 56 round trip was chasing Rs 8 of gross
travel; a book that prices entries at the ask and exits at the bid will show that
instead of hiding it. A paper trade whose book was too stale to fill against is
refused rather than filled at the LTP — an invented fill is worse than a missing
row, because it enters the expectancy.
"""
from __future__ import annotations

import threading
import time

from app.config import settings
from app.research.phase17 import (
    aplus,
    economics,
    quality,
    schema,
    store,
    tracker,
)

WAIT = "WAIT"
BUY = "BUY"
HOLD = "HOLD"
PROTECT = "PROTECT"
EXIT = "EXIT"
STATUSES: tuple[str, ...] = (
    WAIT, BUY, HOLD, schema.T1, schema.T2, schema.T3, PROTECT, EXIT,
    schema.STOP, schema.TIMEOUT,
)

REFUSED_NOT_A_PLUS = "REFUSED_NOT_A_PLUS"
REFUSED_DATA = "REFUSED_DATA_QUALITY"
REFUSED_NO_BOOK = "REFUSED_NO_BOOK"
REFUSED_OPEN = "REFUSED_ALREADY_OPEN"
REFUSED_DISABLED = "REFUSED_DISABLED"
REFUSED_MODELLED_T1 = "REFUSED_MODELLED_T1"
# The published stop is not below the ask actually payable. Risk would be a
# negative distance, which does not scale a result but inverts its sign, so the
# trade is refused rather than booked with an R that reads a loss as a gain.
REFUSED_STOP_ABOVE_ENTRY = "REFUSED_STOP_ABOVE_ENTRY"
ENTERED = "ENTERED"

REFUSALS: tuple[str, ...] = (
    REFUSED_NOT_A_PLUS, REFUSED_DATA, REFUSED_NO_BOOK, REFUSED_OPEN,
    REFUSED_DISABLED, REFUSED_MODELLED_T1, REFUSED_STOP_ABOVE_ENTRY,
)

_LOCK = threading.Lock()
_OPEN: dict[str, dict] = {}
_STATE: dict[str, int] = {"entered": 0, "refused": 0, "closed": 0}
_REFUSED_BY_REASON: dict[str, int] = {}


def _episode_id(obs: schema.Observation) -> str:
    return f"P17-{obs.observation_id}"


def _refuse(out: dict, status: str, *, locked: bool = False) -> dict:
    """Record a refusal and name it.

    A single ``refused`` total cannot distinguish "A+ is selective" from "the
    book was never fillable" from "a leg on this symbol was already open", which
    are different problems with different fixes. Counting by reason is what makes
    an almost-empty paper book diagnosable instead of merely disappointing.
    """
    out["status"] = status
    if locked:
        _STATE["refused"] += 1
        _REFUSED_BY_REASON[status] = _REFUSED_BY_REASON.get(status, 0) + 1
        return out
    with _LOCK:
        _STATE["refused"] += 1
        _REFUSED_BY_REASON[status] = _REFUSED_BY_REASON.get(status, 0) + 1
    return out


def consider(obs: schema.Observation, *, now: float | None = None) -> dict:
    """Open a paper episode if — and only if — this candidate qualifies.

    Returns the decision either way, with the refusal reason named, because the
    §26 missed-opportunity study needs the refusals as much as the entries: a
    refusal that is never recorded cannot later be shown to have been correct.
    """
    now = time.time() if now is None else float(now)
    label = (obs.aplus or {}).get("a_plus_label")
    out = {
        "episode_id": _episode_id(obs),
        "observation_id": obs.observation_id,
        "instrument": obs.instrument,
        "action": WAIT,
        "status": REFUSED_NOT_A_PLUS,
        "a_plus_label": label,
        "ts": now,
        "paper_only": True,
        "no_real_order": True,
    }
    if not settings.phase17_paper:
        return _refuse(out, REFUSED_DISABLED)
    if label != aplus.A_PLUS:
        return _refuse(out, REFUSED_NOT_A_PLUS)
    if not (obs.aplus or {}).get("promotable"):
        # A+ against a target the engine never published. It belongs on the
        # research board, not in the costed book that the promotion sample is
        # drawn from, because its T1 was derived rather than committed to.
        out["t1_basis"] = (obs.aplus or {}).get("t1_basis")
        return _refuse(out, REFUSED_MODELLED_T1)
    q = obs.selected
    if q is None or not q.has_book:
        return _refuse(out, REFUSED_NO_BOOK)
    if not quality.fillable(q.data_quality):
        out["data_quality"] = q.data_quality
        return _refuse(out, REFUSED_DATA)
    ask = float(q.ask) if isinstance(q.ask, (int, float)) else None
    if ask is None or ask <= 0:
        return _refuse(out, REFUSED_NO_BOOK)
    risk = (
        ask - float(obs.plan.stop)
        if isinstance(obs.plan.stop, (int, float)) else None
    )
    if risk is not None and risk <= 0:
        out["stop"] = obs.plan.stop
        out["entry_price"] = ask
        return _refuse(out, REFUSED_STOP_ABOVE_ENTRY)

    with _LOCK:
        if any(
            st["instrument"] == obs.instrument and st["symbol"] == q.symbol
            for st in _OPEN.values()
        ):
            return _refuse(out, REFUSED_OPEN, locked=True)
        ep = {
            "episode_id": out["episode_id"],
            "observation_id": obs.observation_id,
            "instrument": obs.instrument,
            "vehicle": obs.selected_vehicle,
            "symbol": q.symbol,
            "strike": q.strike,
            "expiry": q.expiry,
            "days_to_expiry": q.days_to_expiry,
            "expiry_class": q.expiry_class,
            "direction": obs.direction,
            "signal_ts": obs.signal_ts,
            "entry_ts": now,
            "entry_price": ask,
            "entry_side": "ASK",
            "entry_bid": q.bid,
            "entry_ask": q.ask,
            "entry_spread": q.spread,
            "stop": obs.plan.stop,
            "target1": obs.plan.target1,
            "target2": obs.plan.target2,
            "target3": obs.plan.target3,
            "risk": risk,
            "a_plus_score": (obs.aplus or {}).get("a_plus_score"),
            "t1_score": (obs.reach or {}).get("t1_score"),
            "t1_rank": (obs.reach or {}).get("t1_rank"),
            "entry_quality": (obs.entry or {}).get("entry_quality"),
            "vehicle_class": (obs.economics or {}).get("vehicle_class"),
            "data_quality": q.data_quality,
            "peak": ask,
            "trough": ask,
            "last": ask,
            "peak_ts": now,
            "hit": {},
            "status": BUY,
            "samples": 1,
        }
        _OPEN[ep["episode_id"]] = ep
        _STATE["entered"] += 1
    out["action"] = BUY
    out["status"] = ENTERED
    out["entry_price"] = ask
    return out


def _r_of(ep: dict, px: float) -> float | None:
    risk = ep.get("risk")
    if not isinstance(risk, (int, float)) or float(risk) <= 0:
        return None
    return round((px - float(ep["entry_price"])) / float(risk), 4)


def advance(instrument: str, quotes: list[schema.Quote], *,
            now: float | None = None) -> list[dict]:
    """Mark open episodes against fresh captured quotes; close what resolved.

    Only EXACT/GOOD/DEGRADED quotes advance an episode, and only a two-sided book
    can close one — an exit priced off an LTP would be a sale at a price nobody
    was bidding.
    """
    now = time.time() if now is None else float(now)
    books = {
        q.symbol: q for q in quotes
        if q.symbol and quality.usable(q.data_quality)
    }
    closed: list[dict] = []
    with _LOCK:
        for eid, ep in list(_OPEN.items()):
            if ep["instrument"] != instrument:
                continue
            q = books.get(ep["symbol"])
            if q is None:
                if now - ep["signal_ts"] >= max(1, int(settings.phase17_follow_minutes)) * 60:
                    closed.append(_close(eid, ep, None, now, schema.TIMEOUT))
                continue
            px = float(q.premium) if isinstance(q.premium, (int, float)) else None
            bid = float(q.bid) if isinstance(q.bid, (int, float)) else None
            mark = px if px is not None else bid
            if mark is None:
                continue
            ep["samples"] += 1
            ep["last"] = mark
            if mark > ep["peak"]:
                ep["peak"], ep["peak_ts"] = mark, now
            if mark < ep["trough"]:
                ep["trough"] = mark
            r = _r_of(ep, mark)
            if r is not None and r >= 0.5:
                ep["hit"].setdefault("HALF_R", {"ts": now, "premium": mark})
            for name, level in (
                (schema.T1, ep["target1"]), (schema.T2, ep["target2"]),
                (schema.T3, ep["target3"]),
            ):
                if isinstance(level, (int, float)) and mark >= float(level):
                    ep["hit"].setdefault(name, {"ts": now, "premium": mark})
                    ep["status"] = name
            stop = ep["stop"]
            if isinstance(stop, (int, float)) and mark <= float(stop):
                closed.append(_close(eid, ep, q, now, schema.STOP))
                continue
            if schema.T3 in ep["hit"]:
                closed.append(_close(eid, ep, q, now, schema.T3))
                continue
            if now - ep["signal_ts"] >= max(1, int(settings.phase17_follow_minutes)) * 60:
                closed.append(_close(eid, ep, q, now, schema.TIMEOUT))
    return [c for c in closed if c]


def _close(eid: str, ep: dict, q: schema.Quote | None, now: float,
           outcome: str) -> dict | None:
    """Sell at the bid, charge the round trip, write the row. Caller holds lock."""
    _OPEN.pop(eid, None)
    _STATE["closed"] += 1
    entry = float(ep["entry_price"])
    exit_px: float | None = None
    exit_side = "UNKNOWN"
    if q is not None and q.has_book and isinstance(q.bid, (int, float)):
        exit_px, exit_side = float(q.bid), "BID"
    elif q is not None and isinstance(q.premium, (int, float)):
        exit_px, exit_side = float(q.premium), "LTP"
    costs = economics.cost_leg(
        ep["instrument"], entry, exit_px,
        quoted_spread=ep.get("entry_spread"),
        lot_size=None,
    )
    risk = ep.get("risk") if isinstance(ep.get("risk"), (int, float)) else None
    if risk is not None and float(risk) <= 0:
        risk = None
    gross = costs["gross_points"]
    net = costs["net_points"]
    peak = float(ep["peak"])
    mfe = round(peak - entry, 4)
    mae = round(float(ep["trough"]) - entry, 4)
    hold = round((now - ep["entry_ts"]) / 60.0, 3)
    hit = ep["hit"]

    def mins(name: str) -> float | None:
        h = hit.get(name)
        return round((float(h["ts"]) - ep["entry_ts"]) / 60.0, 3) if h else None

    row = {
        "episode_id": eid,
        "observation_id": ep["observation_id"],
        "instrument": ep["instrument"],
        "vehicle": ep["vehicle"],
        "symbol": ep["symbol"],
        "strike": ep["strike"],
        "expiry": ep["expiry"],
        "days_to_expiry": ep["days_to_expiry"],
        "expiry_class": ep["expiry_class"],
        "direction": ep["direction"],
        "signal_ts": ep["signal_ts"],
        "entry_ts": ep["entry_ts"],
        "entry_price": entry,
        "entry_side": ep["entry_side"],
        "exit_ts": now,
        "exit_price": exit_px,
        "exit_side": exit_side,
        "outcome": outcome,
        "exit_reason": outcome,
        "stop": ep["stop"],
        "target1": ep["target1"],
        "target2": ep["target2"],
        "target3": ep["target3"],
        "gross_points": gross,
        "cost_points": costs["cost_points"],
        "cost_rupees": costs["cost_rupees"],
        "slippage_points": costs["slippage_points"],
        "net_points": net,
        "cost_status": costs["cost_status"],
        "spread_source": costs["spread_source"],
        "gross_r": None if gross is None or not risk else round(gross / risk, 4),
        "net_r": None if net is None or not risk else round(net / risk, 4),
        "risk": risk,
        "mfe": mfe,
        "mae": mae,
        "mfe_capture_pct": (
            round(100.0 * gross / mfe, 2) if gross is not None and mfe > 0 else None
        ),
        "giveback": None if gross is None else round(mfe - gross, 4),
        "minutes_to_t1": mins(schema.T1),
        "minutes_to_t2": mins(schema.T2),
        "minutes_to_t3": mins(schema.T3),
        "minutes_to_stop": mins(schema.STOP),
        "minutes_to_mfe": round((float(ep["peak_ts"]) - ep["entry_ts"]) / 60.0, 3),
        "hold_minutes": hold,
        "hold_bucket": schema.hold_bucket(hold),
        "reached_half_r_then_reversed": (
            "HALF_R" in hit and outcome in (schema.STOP, schema.TIMEOUT)
        ),
        "reached_t1_then_reversed": (
            schema.T1 in hit and outcome in (schema.STOP, schema.TIMEOUT)
        ),
        "reached_t2_then_reversed": (
            schema.T2 in hit and outcome in (schema.STOP, schema.TIMEOUT)
        ),
        "a_plus_score": ep["a_plus_score"],
        "t1_score": ep["t1_score"],
        "t1_rank": ep["t1_rank"],
        "not_a_probability": True,
        "entry_quality": ep["entry_quality"],
        "vehicle_class": ep["vehicle_class"],
        "data_quality": ep["data_quality"],
        "samples": ep["samples"],
        "paper_only": True,
        "no_real_order": True,
    }
    store.write_paper(row)
    return row


def open_rows() -> list[dict]:
    """Live paper positions for the dashboard — §23."""
    with _LOCK:
        out = []
        for ep in _OPEN.values():
            entry = float(ep["entry_price"])
            last = float(ep["last"])
            out.append({
                "episode_id": ep["episode_id"],
                "observation_id": ep["observation_id"],
                "instrument": ep["instrument"],
                "vehicle": ep["vehicle"],
                "symbol": ep["symbol"],
                "entry": entry,
                "current": last,
                "stop": ep["stop"],
                "target1": ep["target1"],
                "target2": ep["target2"],
                "target3": ep["target3"],
                "a_plus_score": ep["a_plus_score"],
                "t1_score": ep["t1_score"],
                "t1_rank": ep["t1_rank"],
                "not_a_probability": True,
                "spread": ep["entry_spread"],
                "entry_quality": ep["entry_quality"],
                "vehicle_class": ep["vehicle_class"],
                "elapsed_minutes": round((time.time() - ep["entry_ts"]) / 60.0, 2),
                "unrealised_points": round(last - entry, 4),
                "unrealised_r": _r_of(ep, last),
                "mfe": round(float(ep["peak"]) - entry, 4),
                "mae": round(float(ep["trough"]) - entry, 4),
                "giveback": round(float(ep["peak"]) - last, 4),
                "status": ep["status"],
                "data_quality": ep["data_quality"],
                "samples": ep["samples"],
                "paper_only": True,
                "no_real_order": True,
            })
        return out


def health() -> dict:
    with _LOCK:
        return {
            "open": len(_OPEN),
            "entered": _STATE["entered"],
            "refused": _STATE["refused"],
            "refused_by_reason": dict(_REFUSED_BY_REASON),
            "closed": _STATE["closed"],
            "enabled": bool(settings.phase17_paper),
            "tracker": tracker.health(),
        }


def reset() -> None:
    """Test seam."""
    with _LOCK:
        _OPEN.clear()
        _REFUSED_BY_REASON.clear()
        _STATE.update({"entered": 0, "refused": 0, "closed": 0})
