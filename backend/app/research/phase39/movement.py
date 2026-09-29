"""Phase 39 §3 — the movement that is already on disk, measured mid-to-mid.

This module answers "how far did it actually go" for the same instants
:mod:`app.research.phase39.cost` priced. Two frames are computed on every leg,
deliberately, and both are reported:

**The mid frame** is the movement yardstick. The favourable excursion is the
best mid reached after the entry instant, as a percentage of the entry mid. It
carries no execution cost at all, which is exactly why it can be compared
against a required move that carries all of it.

**The realised frame** is Phase 36's arithmetic — enter on the executable side,
exit on the executable side — recomputed here on the same legs so §10 can print
the residual between the two. The mid frame's error is the change in the quoted
width between entry and exit, and an unstated error is worse than a measured
one.

Three rules the walk keeps, inherited from Phase 35 §4 and restated because this
is where they would be cheapest to break: a path never leaves its own session, a
gap is never forward-filled, and a horizon with no quote is unanswered rather
than answered with the previous value. Every horizon therefore carries its own
sample count.

The peak is the **first** quote at the best price, matching
:mod:`app.research.phase36.pathindex`, so a Phase 39 time-to-peak and a Phase 36
time-to-peak mean the same thing.
"""
from __future__ import annotations

from app.research.phase35 import LONG, MEASURED_EXECUTABLE, SHORT, book
from app.research.phase35 import path as p35path
from app.research.phase39 import (
    CLOSE,
    FEASIBILITY_HORIZONS,
    MAX_MOVE_INSTANTS_PER_SESSION_KEY,
)
from app.research.phase39 import cost as p39cost

NO_PATH = "NO_FORWARD_PATH_IN_SESSION"
NO_MID = "NO_TWO_SIDED_BOOK_ON_THE_PATH"


class _ContractSession:
    """One contract's quotes for one session, read once and kept as arrays."""

    def __init__(self, rows: list[dict], *, vehicle: str, direction: str) -> None:
        self.ts: list[float] = []
        self.mid: list[float] = []
        self.exit_px: list[float | None] = []
        self.exit_executable: list[bool] = []
        for r in rows:
            bid = p39cost.positive(r.get("bid"))
            ask = p39cost.positive(r.get("ask"))
            if bid is None or ask is None or not ask > bid:
                # A one-sided or crossed quote is not a mid. It is skipped from
                # the yardstick rather than repaired, and the count of what was
                # skipped is what the coverage figures are built from.
                continue
            fill = book.exit_fill(r, vehicle=vehicle, direction=direction)
            self.ts.append(float(r["ts"]))
            self.mid.append((bid + ask) / 2.0)
            self.exit_px.append(
                None if fill["price"] is None else float(fill["price"]),
            )
            self.exit_executable.append(fill["evidence"] == MEASURED_EXECUTABLE)

    def __len__(self) -> int:
        return len(self.ts)


class Paths:
    """Contract-sessions for one pass, one session held at a time.

    Instants are walked in session order, so an earlier day can never be needed
    again; keeping it would grow the cache to the size of the store.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple, _ContractSession] = {}
        self._session: str | None = None

    def get(
        self, con, *, symbol: str, vehicle: str, direction: str, at_ts: float,
    ) -> _ContractSession:
        session = p35path.session_date(at_ts)
        if session != self._session:
            self._cache.clear()
            self._session = session
        key = (symbol, vehicle, direction, session)
        found = self._cache.get(key)
        if found is None:
            start, end = p35path.session_bounds(at_ts)
            rows = [
                dict(r) for r in con.execute(
                    "SELECT ts, bid, ask, traded, evidence FROM raw_quote "
                    "WHERE symbol = ? AND vehicle = ? AND ts >= ? AND ts < ? "
                    "ORDER BY ts",
                    (symbol, vehicle, start, end),
                ).fetchall()
            ]
            found = _ContractSession(rows, vehicle=vehicle, direction=direction)
            self._cache[key] = found
        return found


def _sign(vehicle: str, direction: str) -> float:
    return book.gross_sign(vehicle, direction)


def walk(
    contract: _ContractSession, *, entry_ts: float, entry_mid: float,
    entry_fill: float, vehicle: str, direction: str,
) -> dict:
    """Per-horizon favourable excursion in both frames, for one leg.

    ``mfe_pct`` is the best mid reached in the window as a percent of the entry
    mid, floored at zero: "the best it offered" cannot be negative, and a leg
    that only ever went against the view offered nothing rather than offering a
    loss. ``end_pct`` is the mid at the horizon and is signed, so the two
    together say whether the offer was still there when the horizon arrived.

    A horizon is answered by the last quote **at or before** it, exactly as
    Phase 35 §8 answers one, and is absent when no such quote exists. Answering
    the 1-minute horizon from a quote that arrived at minute five would be
    look-ahead, and on a thinly-quoted contract it is look-ahead that always
    points the same way — towards whichever direction the price had by the time
    it was next seen.
    """
    sign = _sign(vehicle, direction)
    horizons: dict[str, dict] = {}
    pending = [float(h) for h in FEASIBILITY_HORIZONS]
    best_mid: float | None = None
    best_ts = entry_ts
    last: tuple[float, float, float] | None = None
    realised_best: float | None = None
    priced = 0

    def snap(mid: float, ts: float, exit_px: float | None) -> dict:
        offered = max(0.0, 100.0 * sign * (best_mid - entry_mid) / entry_mid)
        return {
            "mfe_pct": round(offered, 6),
            "end_pct": round(100.0 * sign * (mid - entry_mid) / entry_mid, 6),
            "time_to_peak_min": round((best_ts - entry_ts) / 60.0, 2),
            "hold_min": round((ts - entry_ts) / 60.0, 2),
            "realised_end_pct": (
                None if exit_px is None else book.gross_pct(
                    entry_fill, exit_px, vehicle=vehicle, direction=direction,
                )
            ),
            "realised_mfe_pct": (
                None if realised_best is None else round(max(0.0, book.gross_pct(
                    entry_fill, realised_best, vehicle=vehicle,
                    direction=direction,
                )), 6)
            ),
        }

    def key(h: float) -> str:
        return str(int(h)) if h == int(h) else str(h)

    unanswered: list[str] = []
    start = _first_after(contract.ts, entry_ts)
    for i in range(start, len(contract)):
        ts = contract.ts[i]
        mid = contract.mid[i]
        elapsed = (ts - entry_ts) / 60.0
        # Horizons that closed before this quote arrived are answered from the
        # state as it stood at the previous quote — or not at all.
        while pending and pending[0] < elapsed:
            h = pending.pop(0)
            if last is None:
                unanswered.append(key(h))
            else:
                prev_mid, prev_ts, prev_i = last
                horizons[key(h)] = snap(
                    prev_mid, prev_ts, contract.exit_px[prev_i],
                )
        priced += 1
        if best_mid is None or sign * mid > sign * best_mid:
            best_mid, best_ts = mid, ts
        exit_px = contract.exit_px[i]
        if exit_px is not None and (
            realised_best is None or sign * exit_px > sign * realised_best
        ):
            realised_best = exit_px
        last = (mid, ts, i)
        while pending and pending[0] <= elapsed:
            h = pending.pop(0)
            horizons[key(h)] = snap(mid, ts, exit_px)
    if last is None or best_mid is None:
        return {"horizons": {}, "priced": 0, "unanswered": [], "reason": NO_PATH}
    # Horizons still pending are beyond the last quote of the session. They stay
    # unanswered; the close is answered by that last quote, which is what a
    # session close is.
    unanswered += [key(h) for h in pending]
    mid, ts, i = last
    horizons[CLOSE] = snap(mid, ts, contract.exit_px[i])
    return {
        "horizons": horizons, "priced": priced, "unanswered": unanswered,
        "reason": None,
    }


def _first_after(ts: list[float], after: float) -> int:
    """First index strictly after ``after`` — a bisect without the import cost."""
    lo, hi = 0, len(ts)
    while lo < hi:
        mid = (lo + hi) // 2
        if ts[mid] <= after:
            lo = mid + 1
        else:
            hi = mid
    return lo


def instants(con, *, instrument: str | None = None) -> dict:
    """Sampled entry instants per instrument, vehicle and session.

    The sample is a fixed stride through the session's time-ordered executable
    quotes (:func:`app.research.phase39.cost.stride`), capped per session so one
    deeply-captured instrument cannot starve the rest of the pass. Nothing about
    the row's later behaviour is consulted to decide whether it is sampled.
    """
    quotes = p39cost.executable_quotes(con, instrument=instrument)
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for q in quotes:
        direction = p39cost.leg_direction(str(q["vehicle"]), q.get("direction"))
        if direction not in (LONG, SHORT):
            continue
        q["leg_direction"] = direction
        key = (str(q["instrument"]), str(q["vehicle"]), str(q["session"]))
        grouped.setdefault(key, []).append(q)

    sampled: list[dict] = []
    skipped_no_direction = len([
        q for q in quotes
        if p39cost.leg_direction(str(q["vehicle"]), q.get("direction"))
        not in (LONG, SHORT)
    ])
    for key in sorted(grouped):
        rows = p39cost.stride(
            grouped[key], MAX_MOVE_INSTANTS_PER_SESSION_KEY,
        )
        sampled.extend(rows)
    return {
        "instants": sampled,
        "executable_quotes": len(quotes),
        "sampled": len(sampled),
        "skipped_no_direction": skipped_no_direction,
        "cap_per_instrument_vehicle_session": MAX_MOVE_INSTANTS_PER_SESSION_KEY,
    }


def legs(con, *, instrument: str | None = None) -> dict:
    """Every sampled instant priced and walked, or counted as unmeasurable."""
    picked = instants(con, instrument=instrument)
    paths = Paths()
    out: list[dict] = []
    refused: dict[str, int] = {}
    unanswered: dict[str, int] = {}
    lot_sources: dict[str, int] = {}

    def refuse(reason: str) -> None:
        refused[reason] = refused.get(reason, 0) + 1

    for q in picked["instants"]:
        vehicle = str(q["vehicle"])
        inst = str(q["instrument"])
        direction = str(q["leg_direction"])
        req = p39cost.required_move(
            vehicle=vehicle, instrument=inst, bid=q["bid"], ask=q["ask"],
            lot_size=q.get("lot_size"),
        )
        if req.get("required_points") is None:
            refuse(str(req.get("reason")))
            continue
        symbol = q.get("symbol")
        if not symbol:
            refuse("NO_CONTRACT_SYMBOL")
            continue
        fill = book.entry_fill(q, vehicle=vehicle, direction=direction)
        if fill["price"] is None:
            refuse(str(fill["reason"]))
            continue
        contract = paths.get(
            con, symbol=str(symbol), vehicle=vehicle, direction=direction,
            at_ts=float(q["ts"]),
        )
        walked = walk(
            contract, entry_ts=float(q["ts"]), entry_mid=float(req["mid"]),
            entry_fill=float(fill["price"]), vehicle=vehicle,
            direction=direction,
        )
        if walked.get("reason") is not None:
            refuse(str(walked["reason"]))
            continue
        src = str(q.get("lot_source"))
        lot_sources[src] = lot_sources.get(src, 0) + 1
        out.append({
            "instrument": inst,
            "vehicle": vehicle,
            "lot_source": src,
            "session": str(q["session"]),
            "ts": float(q["ts"]),
            "symbol": str(symbol),
            "leg_direction": direction,
            "dte": q.get("dte"),
            "mid": req["mid"],
            "entry_fill": float(fill["price"]),
            "required_pct": float(req["required_pct_of_mid"]),
            "required_points": float(req["required_points"]),
            "spread_pct": float(req["spread_pct_of_mid"]),
            "priced_forward": walked["priced"],
            "unanswered_horizons": walked["unanswered"],
            "horizons": walked["horizons"],
        })
        for h in walked["unanswered"]:
            unanswered[h] = unanswered.get(h, 0) + 1
    return {
        "legs": out,
        "coverage": {
            "executable_quotes": picked["executable_quotes"],
            "sampled_instants": picked["sampled"],
            "walked": len(out),
            "skipped_no_direction": picked["skipped_no_direction"],
            "unwalkable_by_reason": dict(
                sorted(refused.items(), key=lambda kv: -kv[1]),
            ),
            # A horizon nobody quoted inside is missing from that leg rather
            # than carried forward, so the count of what went unanswered is
            # part of the coverage rather than invisible in an average.
            "unanswered_horizons": dict(sorted(unanswered.items())),
            # Which source each walked leg's lot came from. A round trip priced
            # on a registry lot is only as current as the registry, and that is
            # a different confidence from a lot the feed itself reported.
            "lot_size_sources": dict(
                sorted(lot_sources.items(), key=lambda kv: -kv[1]),
            ),
            "cap_per_instrument_vehicle_session": (
                picked["cap_per_instrument_vehicle_session"]
            ),
        },
    }
