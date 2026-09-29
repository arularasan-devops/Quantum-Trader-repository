"""Follow both option sides of a candidate to a resolution — §5, §16, §17.

An observation says what the vehicle looked like. This says what it then did,
which is the half no replay can reconstruct: an option's path is not a function
of the underlying's path, and the gap between the two is where the money went.

Three design decisions worth stating, because each one is a place this could
quietly produce a wrong number.

**Both sides are tracked, and the opposite side gets its own geometry.** The
published stop and targets belong to the selected contract; applying those rupee
levels to the other side would compare a 1.2R plan against a 4R one and call the
difference a CE/PE finding. So the opposite leg is given the same geometry in
PROPORTIONAL terms — the same stop distance and the same target distances as a
percentage of its own entry — and every row says ``geometry=PROPORTIONAL`` so no
reader mistakes it for a plan the engine published.

**The budget is enforced and the drop is recorded.** Path tracking re-reads every
open leg on every tick, so the cost is legs x sides x ticks, and the broker's
limiter is the binding constraint. Over budget, the new candidate is written with
``tracking=TRACKING_DROPPED`` — a visibly incomplete dataset, rather than one that
looks complete and is silently biased toward quiet days.

**Nothing is marked to a stale book.** A leg only updates against a quote whose
freshness passes, and an unresolved leg at shutdown is never written as a result:
it simply has no row, and the coverage file explains why.
"""
from __future__ import annotations

import threading
import time

from app.config import settings
from app.models import OptionQuote
from app.research.phase17 import economics, futures as fut_mod, quality, schema
from app.research.phase19 import futcosts

SELECTED = "SELECTED"
OPPOSITE = "OPPOSITE"
# The third side, opened only when a futures book was quoted AT the signal —
# Phase 21 §1. It shares ``observation_id`` with the two option sides, which is
# what makes the vehicle comparison a comparison: same instant, same market read,
# three prices. A futures leg is never opened from a later quote.
FUTURES = "FUTURES"

PUBLISHED = "PUBLISHED"
PROPORTIONAL = "PROPORTIONAL"

TRACKED = "TRACKED"
DROPPED = "TRACKING_DROPPED"
NOT_ELIGIBLE = "NOT_ELIGIBLE"

_LOCK = threading.Lock()
# observation_id -> side -> tracked state
_OPEN: dict[str, dict[str, dict]] = {}
_STATE: dict[str, int] = {"opened": 0, "dropped": 0, "resolved": 0}


def _fill_price(q: schema.Quote | None, *, buying: bool) -> tuple[float | None, str]:
    """Price a fill off a recorded book — §10, §12.

    A buy pays the ask and a sell receives the bid. When the book is absent or
    too stale to fill against, the LTP is used and the leg is flagged
    ``UNKNOWN``: the path is still worth recording, the COST is not claimed.
    """
    if q is None:
        return None, schema.COST_UNKNOWN
    if q.has_book and quality.fillable(q.data_quality):
        px = q.ask if buying else q.bid
        if isinstance(px, (int, float)) and float(px) > 0:
            return float(px), schema.COST_MEASURED
    return (float(q.premium) if q.premium else None), schema.COST_UNKNOWN


def _levels(entry: float, plan: schema.Plan) -> tuple[float | None, dict[str, float]]:
    """Published stop/targets, as given."""
    stop = float(plan.stop) if plan.stop else None
    targets: dict[str, float] = {}
    for name, level in ((schema.T1, plan.target1), (schema.T2, plan.target2),
                        (schema.T3, plan.target3)):
        if isinstance(level, (int, float)) and float(level) > entry:
            targets[name] = float(level)
    return stop, targets


def _proportional(entry: float, ref_entry: float, plan: schema.Plan
                  ) -> tuple[float | None, dict[str, float]]:
    """The published geometry re-expressed as percentages of a different entry."""
    if ref_entry <= 0:
        return None, {}
    stop = None
    if isinstance(plan.stop, (int, float)):
        stop = round(entry * (float(plan.stop) / ref_entry), 4)
    targets: dict[str, float] = {}
    for name, level in ((schema.T1, plan.target1), (schema.T2, plan.target2),
                        (schema.T3, plan.target3)):
        if isinstance(level, (int, float)) and float(level) > 0:
            scaled = round(entry * (float(level) / ref_entry), 4)
            if scaled > entry:
                targets[name] = scaled
    return stop, targets


def open_legs(obs: schema.Observation, *, now: float | None = None,
              budget: int | None = None) -> str:
    """Start following both sides of ``obs``. Returns the tracking label.

    Idempotent per observation: a repeated candidate does not reopen a leg, so a
    setup that persists for twenty ticks contributes one path, not twenty.
    """
    now = time.time() if now is None else now
    budget = int(settings.phase17_track_budget if budget is None else budget)
    sides: list[tuple[str, schema.Quote | None]] = [
        (SELECTED, obs.selected), (OPPOSITE, obs.opposite),
    ]
    with _LOCK:
        if obs.observation_id in _OPEN:
            return TRACKED
        live = sum(len(v) for v in _OPEN.values())
        wanted = len([q for _, q in sides if q]) + (
            1 if (obs.futures is not None and obs.futures_plan) else 0
        )
        if live + wanted > budget:
            _STATE["dropped"] += 1
            return DROPPED
        entry_ref: float | None = None
        states: dict[str, dict] = {}
        for side, q in sides:
            if q is None:
                continue
            entry, cost_status = _fill_price(q, buying=True)
            if entry is None or entry <= 0:
                continue
            if side == SELECTED:
                entry_ref = entry
                stop, targets = _levels(entry, obs.plan)
                geometry = PUBLISHED
                if stop is None or not targets:
                    stop, targets = _proportional(entry, entry, obs.plan)
                    geometry = PROPORTIONAL
            else:
                ref = entry_ref if entry_ref else entry
                stop, targets = _proportional(entry, ref, obs.plan)
                geometry = PROPORTIONAL
            states[side] = {
                "observation_id": obs.observation_id,
                "instrument": obs.instrument,
                "vehicle": q.vehicle,
                "side": side,
                "symbol": q.symbol,
                "signal_ts": obs.signal_ts,
                "entry_ts": now,
                "entry": entry,
                "cost_status": cost_status,
                "entry_bid": q.bid,
                "entry_ask": q.ask,
                "session": obs.session,
                "entry_quality": (obs.entry or {}).get("entry_quality")
                if isinstance(obs.entry, dict) else None,
                "vehicle_class": (obs.economics or {}).get("vehicle_class")
                if isinstance(obs.economics, dict) else None,
                "a_plus_label": (obs.aplus or {}).get("a_plus_label")
                if isinstance(obs.aplus, dict) else None,
                "a_plus_score": (obs.aplus or {}).get("a_plus_score")
                if isinstance(obs.aplus, dict) else None,
                "t1_rank": (obs.reach or {}).get("t1_rank")
                if isinstance(obs.reach, dict) else None,
                "geometry": geometry,
                "stop": stop,
                "targets": targets,
                "risk": (entry - stop) if stop is not None else None,
                "peak": entry,
                "peak_ts": now,
                "trough": entry,
                "trough_ts": now,
                "last": entry,
                "hit": {},
                "samples": 1,
                "data_quality": q.data_quality,
                "underlying_entry": q.underlying_price,
                "underlying_last": q.underlying_price,
                "underlying_best": q.underlying_price,
                "underlying_worst": q.underlying_price,
                "lot_size": obs.plan.lot_size,
            }
        fut = _futures_state(obs, now)
        if fut is not None:
            states[FUTURES] = fut
        if not states:
            return NOT_ELIGIBLE
        _OPEN[obs.observation_id] = states
        _STATE["opened"] += len(states)
    return TRACKED


def _futures_state(obs: schema.Observation, now: float) -> dict | None:
    """The futures leg of this observation, or None when there isn't one.

    Everything comes from ``obs.futures_plan``, which was built at the signal
    instant from the book quoted at that instant. Nothing here reads a price, so
    a futures leg cannot be opened at a timestamp other than its signal's.
    """
    plan = obs.futures_plan
    q = obs.futures
    if not plan or q is None:
        return None
    side = plan.get("side")
    entry = plan.get("entry")
    stop = plan.get("stop")
    if side not in (fut_mod.LONG, fut_mod.SHORT):
        return None
    if not isinstance(entry, (int, float)) or float(entry) <= 0:
        return None
    if not isinstance(stop, (int, float)):
        return None
    targets = {
        name: float(plan[key])
        for name, key in ((schema.T1, "target1"), (schema.T2, "target2"),
                          (schema.T3, "target3"))
        if isinstance(plan.get(key), (int, float))
    }
    if schema.T1 not in targets:
        return None
    entry = float(entry)
    risk = abs(entry - float(stop))
    if risk <= 0:
        return None
    # A fill can only be claimed off a two-sided book that was fresh enough to
    # fill against. Priced off the LTP the path is still recorded and the cost is
    # not claimed — the same rule the option sides follow.
    cost_status = (
        schema.COST_MEASURED
        if plan.get("entry_source") == "BOOK" and quality.fillable(q.data_quality)
        else schema.COST_UNKNOWN
    )
    return {
        "observation_id": obs.observation_id,
        "instrument": obs.instrument,
        "vehicle": schema.FUTURES,
        "side": FUTURES,
        "position": side,
        "symbol": q.symbol,
        "signal_ts": obs.signal_ts,
        "entry_ts": now,
        "entry": entry,
        "cost_status": cost_status,
        "entry_bid": q.bid,
        "entry_ask": q.ask,
        "session": obs.session,
        "entry_quality": (obs.entry or {}).get("entry_quality")
        if isinstance(obs.entry, dict) else None,
        "vehicle_class": None,
        "a_plus_label": (obs.aplus or {}).get("a_plus_label")
        if isinstance(obs.aplus, dict) else None,
        "a_plus_score": (obs.aplus or {}).get("a_plus_score")
        if isinstance(obs.aplus, dict) else None,
        "t1_rank": (obs.reach or {}).get("t1_rank")
        if isinstance(obs.reach, dict) else None,
        "geometry": plan.get("basis"),
        "stop": round(float(stop), 4),
        "targets": targets,
        "risk": round(risk, 4),
        "peak": entry,
        "peak_ts": now,
        "trough": entry,
        "trough_ts": now,
        "last": entry,
        "hit": {},
        "samples": 1,
        "data_quality": q.data_quality,
        "underlying_entry": entry,
        "underlying_last": entry,
        "underlying_best": entry,
        "underlying_worst": entry,
        "lot_size": plan.get("lot_size") or obs.plan.lot_size,
    }


def _mark(st: dict, name: str, px: float, now: float) -> None:
    if name in st["hit"]:
        return
    st["hit"][name] = {
        "ts": now,
        "premium": px,
        "minutes": round((now - st["signal_ts"]) / 60.0, 3),
    }


def _sign(st: dict) -> float:
    """+1 when profit is price going UP, -1 when it is price going DOWN.

    Every option leg here is bought, so it is always +1 and the arithmetic below
    is unchanged from the two-sided version. A futures leg on a bearish read is a
    SHORT: without this the sign of its MFE, its stop test and its net R would
    all be inverted, which is a wrong number rather than a missing one.
    """
    return -1.0 if st.get("position") == fut_mod.SHORT else 1.0


def _r_of(st: dict, px: float) -> float | None:
    risk = st.get("risk")
    if not isinstance(risk, (int, float)) or float(risk) <= 0:
        return None
    return round(_sign(st) * (px - st["entry"]) / float(risk), 4)


def update(instrument: str, chain: list[OptionQuote], *,
           now: float | None = None, underlying: float | None = None,
           quality_label: str = quality.EXACT,
           futures_book: dict | None = None) -> list[dict]:
    """Advance every open leg on ``instrument``. Returns rows just resolved.

    Resolved rows are returned rather than written here so the caller decides
    persistence — which is what lets the smoke suite exercise a full path without
    touching the evidence files.

    ``futures_book`` advances the futures side. Without it a futures leg has no
    price to mark against and will resolve on TIMEOUT alone — visibly incomplete,
    which is the correct outcome for a feed that quotes no contract.
    """
    now = time.time() if now is None else now
    if not quality.usable(quality_label):
        # A stale chain advances nothing. Marking a path against a book that
        # stopped updating invents both the MFE and the exit.
        return []
    prices = {q.symbol: float(q.premium) for q in chain if q.symbol and q.premium}
    books = {q.symbol: q for q in chain if q.symbol}
    fut_px, fut_book = _futures_mark(futures_book)
    follow_sec = max(1, int(settings.phase17_follow_minutes)) * 60
    done: list[dict] = []
    with _LOCK:
        for obs_id, sides in list(_OPEN.items()):
            for side, st in list(sides.items()):
                if st["instrument"] != instrument:
                    continue
                px = fut_px if side == FUTURES else prices.get(st["symbol"])
                if side == FUTURES and px is not None:
                    st["underlying_last"] = px
                    st["underlying_best"] = max(st["underlying_best"], px)
                    st["underlying_worst"] = min(st["underlying_worst"], px)
                elif underlying is not None:
                    st["underlying_last"] = float(underlying)
                    if st["underlying_best"] is None:
                        st["underlying_best"] = float(underlying)
                        st["underlying_worst"] = float(underlying)
                    else:
                        st["underlying_best"] = max(st["underlying_best"], float(underlying))
                        st["underlying_worst"] = min(st["underlying_worst"], float(underlying))
                if px is not None:
                    st["samples"] += 1
                    st["last"] = px
                    if px > st["peak"]:
                        st["peak"], st["peak_ts"] = px, now
                    if px < st["trough"]:
                        st["trough"], st["trough_ts"] = px, now
                    sign = _sign(st)
                    r = _r_of(st, px)
                    if r is not None:
                        if r >= 0.5:
                            _mark(st, "HALF_R", px, now)
                        if r >= 1.0:
                            _mark(st, "ONE_R", px, now)
                    for name in (schema.T1, schema.T2, schema.T3):
                        level = st["targets"].get(name)
                        if level is not None and sign * (px - float(level)) >= 0:
                            _mark(st, name, px, now)
                    stop = st["stop"]
                    if isinstance(stop, (int, float)) and sign * (px - float(stop)) <= 0:
                        _mark(st, schema.STOP, px, now)
                        done.append(_resolve(obs_id, side, st, px, now,
                                             schema.STOP, books, fut_book))
                        continue
                    if schema.T3 in st["hit"]:
                        done.append(_resolve(obs_id, side, st, px, now,
                                             schema.T3, books, fut_book))
                        continue
                if now - st["signal_ts"] >= follow_sec:
                    done.append(_resolve(obs_id, side, st, px, now,
                                         schema.TIMEOUT, books, fut_book))
    return [r for r in done if r]


def _futures_mark(book: dict | None) -> tuple[float | None, dict | None]:
    """The futures PATH mark for this tick, and the book it came from.

    This price tracks the path — MFE, MAE, and whether a level was touched —
    and is deliberately not the exit price. Exits are side-aware and priced in
    :func:`_resolve`: a long is closed on the bid, a short bought back on the
    ask, and a leg with no two-sided book resolves ``COST_UNKNOWN`` rather than
    being charged a spread nobody quoted.

    The last trade is preferred because that is where the contract actually
    traded; the mid of a genuinely two-sided book is the fallback. Neither is
    invented, and a book carrying neither marks nothing at all.
    """
    if not book:
        return None, None
    ltp = book.get("ltp")
    bid, ask = book.get("bid"), book.get("ask")
    if isinstance(ltp, (int, float)) and float(ltp) > 0:
        return float(ltp), book
    if quality.two_sided(bid, ask):
        return round((float(bid) + float(ask)) / 2.0, 4), book
    return None, book


def _resolve(obs_id: str, side: str, st: dict, px: float | None, now: float,
             outcome: str, books: dict[str, OptionQuote],
             futures_book: dict | None = None) -> dict | None:
    """Close one leg and shape its row. Caller holds the lock."""
    sides = _OPEN.get(obs_id)
    if sides is not None:
        sides.pop(side, None)
        if not sides:
            _OPEN.pop(obs_id, None)
    _STATE["resolved"] += 1

    sign = _sign(st)
    exit_px: float | None = None
    cost_status = st["cost_status"]
    if side == FUTURES:
        # A long is closed on the bid and a short is bought back on the ask — the
        # short pays the spread on the way out, not the way in, and charging it
        # to the wrong leg would make one direction look systematically better.
        if futures_book:
            b, a = futures_book.get("bid"), futures_book.get("ask")
            if quality.two_sided(b, a):
                exit_px = float(b) if sign > 0 else float(a)
    else:
        book = books.get(st["symbol"])
        if book is not None and quality.two_sided(book.bid, book.ask):
            # A sale receives the bid. This is the single line that makes the
            # paper book honest, and the reason net numbers will read worse than
            # the gross ones the earlier reports showed.
            exit_px = float(book.bid) if book.bid else None
    if exit_px is None:
        exit_px = px if isinstance(px, (int, float)) else None
        cost_status = schema.COST_UNKNOWN

    entry = float(st["entry"])
    risk = st.get("risk") if isinstance(st.get("risk"), (int, float)) else None
    hit = st["hit"]

    def mins(name: str) -> float | None:
        h = hit.get(name)
        return h["minutes"] if h else None

    # Favourable and adverse are read in the direction of the position, so a
    # short's MFE is how far the contract FELL. With sign +1 (every option leg)
    # this is the same arithmetic as before.
    peak, trough = float(st["peak"]), float(st["trough"])
    best = peak if sign > 0 else trough
    worst = trough if sign > 0 else peak
    best_ts = st["peak_ts"] if sign > 0 else st["trough_ts"]
    mfe = round(sign * (best - entry), 4)
    mae = round(sign * (worst - entry), 4)
    gross = None if exit_px is None else round(sign * (float(exit_px) - entry), 4)
    hold = round((now - st["signal_ts"]) / 60.0, 3)

    # The round trip is charged here, on the two prices that were actually
    # quoted. This is why net will read worse than every gross number the
    # earlier reports printed, and the two are kept side by side so the
    # difference is visible rather than argued about.
    spread_ref = st.get("entry_ask")
    spread_bid = st.get("entry_bid")
    quoted_spread = (
        round(float(spread_ref) - float(spread_bid), 4)
        if isinstance(spread_ref, (int, float))
        and isinstance(spread_bid, (int, float))
        else None
    )
    lot = st.get("lot_size") if isinstance(st.get("lot_size"), int) else None
    if side == FUTURES:
        # Futures pay CTT/STT on turnover, not the option cost table. Reusing the
        # Phase 19 helper rather than restating it keeps the two futures books
        # charging the same round trip, and the quoted spread — newly available
        # now that the contract's book is captured — upgrades the status from
        # MODELLED to MEASURED.
        charged = futcosts.round_trip(
            st["instrument"], entry=entry, exit_price=exit_px,
            lot_size=lot, spread_points=quoted_spread,
        )
        costs = {
            "cost_points": charged["cost_points"],
            "cost_rupees": charged["cost_rupees"],
            "spread_source": charged["spread_source"],
            "cost_status": (
                schema.COST_MEASURED
                if charged["cost_status"] == futcosts.COST_MEASURED
                and exit_px is not None
                else schema.COST_UNKNOWN
            ),
        }
        net = (
            None if gross is None or charged["cost_points"] is None
            else round(gross - float(charged["cost_points"]), 4)
        )
    else:
        costs = economics.cost_leg(
            st["instrument"], entry, exit_px,
            quoted_spread=quoted_spread,
            lot_size=lot,
        )
        net = costs["net_points"]
    if cost_status == schema.COST_MEASURED:
        cost_status = costs["cost_status"]

    capture = None
    if gross is not None and mfe > 0:
        capture = round(100.0 * gross / mfe, 2)
    giveback = None if gross is None else round(mfe - gross, 4)

    row = schema.LegOutcome(
        observation_id=obs_id,
        instrument=st["instrument"],
        vehicle=st["vehicle"],
        side=side,
        # LONG/SHORT for a futures leg, None for an option leg. Without it a
        # resolved short cannot be told from a resolved long in the file, and
        # every sign in the comparison would have to be guessed on replay.
        position=st.get("position"),
        geometry=st.get("geometry"),
        symbol=st["symbol"],
        signal_ts=st["signal_ts"],
        session=st.get("session"),
        entry_ts=st["entry_ts"],
        entry_premium=entry,
        entry_quality=st.get("entry_quality"),
        exit_ts=now,
        exit_premium=exit_px,
        outcome=outcome,
        exit_reason=outcome,
        stop=st["stop"],
        target1=st["targets"].get(schema.T1),
        target2=st["targets"].get(schema.T2),
        target3=st["targets"].get(schema.T3),
        risk=risk,
        vehicle_class=st.get("vehicle_class"),
        a_plus_label=st.get("a_plus_label"),
        a_plus_score=st.get("a_plus_score"),
        t1_rank=st.get("t1_rank"),
        mfe=mfe,
        mae=mae,
        mfe_ts=best_ts,
        minutes_to_mfe=round((float(best_ts) - st["signal_ts"]) / 60.0, 3),
        minutes_to_t1=mins(schema.T1),
        minutes_to_t2=mins(schema.T2),
        minutes_to_t3=mins(schema.T3),
        minutes_to_stop=mins(schema.STOP),
        hold_minutes=hold,
        hold_bucket_name=schema.hold_bucket(hold),
        reached_half_r_then_reversed=(
            "HALF_R" in hit and outcome in (schema.STOP, schema.TIMEOUT)
        ),
        reached_1r_then_reversed=(
            "ONE_R" in hit and outcome in (schema.STOP, schema.TIMEOUT)
        ),
        reached_t1_then_reversed=(
            schema.T1 in hit and outcome in (schema.STOP, schema.TIMEOUT)
        ),
        reached_t2_then_reversed=(
            schema.T2 in hit and outcome in (schema.STOP, schema.TIMEOUT)
        ),
        mfe_capture_pct=capture,
        giveback=giveback,
        gross_points=gross,
        gross_r=None if gross is None or not risk else round(gross / float(risk), 4),
        cost_points=costs["cost_points"],
        cost_rupees=costs["cost_rupees"],
        net_points=net,
        net_r=None if net is None or not risk else round(net / float(risk), 4),
        spread_source=costs["spread_source"],
        underlying_entry=st["underlying_entry"],
        underlying_exit=st["underlying_last"],
        underlying_best=st["underlying_best"],
        underlying_worst=st["underlying_worst"],
        samples=int(st["samples"]),
        data_quality=st["data_quality"],
        cost_status=cost_status,
        resolved=True,
    )
    return row.as_dict()


def open_count() -> int:
    with _LOCK:
        return sum(len(v) for v in _OPEN.values())


def open_rows() -> list[dict]:
    """Live view for the dashboard — never a source for any analysis."""
    with _LOCK:
        out: list[dict] = []
        for sides in _OPEN.values():
            for st in sides.values():
                entry = float(st["entry"])
                last = float(st["last"])
                out.append({
                    "observation_id": st["observation_id"],
                    "instrument": st["instrument"],
                    "vehicle": st["vehicle"],
                    "side": st["side"],
                    "symbol": st["symbol"],
                    "entry": entry,
                    "last": last,
                    "stop": st["stop"],
                    "target1": st["targets"].get(schema.T1),
                    "target2": st["targets"].get(schema.T2),
                    "target3": st["targets"].get(schema.T3),
                    "geometry": st["geometry"],
                    "unrealised_points": round(last - entry, 4),
                    "unrealised_r": _r_of(st, last),
                    "mfe": round(float(st["peak"]) - entry, 4),
                    "mae": round(float(st["trough"]) - entry, 4),
                    "giveback": round(float(st["peak"]) - last, 4),
                    "elapsed_minutes": round((time.time() - st["signal_ts"]) / 60.0, 2),
                    "hit": sorted(st["hit"].keys()),
                    "samples": st["samples"],
                    "data_quality": st["data_quality"],
                    "cost_status": st["cost_status"],
                })
        return out


def health() -> dict:
    with _LOCK:
        return {
            "open_legs": sum(len(v) for v in _OPEN.values()),
            "budget": int(settings.phase17_track_budget),
            "opened": _STATE["opened"],
            "dropped": _STATE["dropped"],
            "resolved": _STATE["resolved"],
            "follow_minutes": int(settings.phase17_follow_minutes),
        }


def reset() -> None:
    """Test seam."""
    with _LOCK:
        _OPEN.clear()
        _STATE.update({"opened": 0, "dropped": 0, "resolved": 0})
