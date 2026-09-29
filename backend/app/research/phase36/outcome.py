"""Phase 36 §6/§7 — resolve one vehicle of a triple over the 12 horizons.

The whole point of this module is that all three vehicles are resolved by the
*same* code over the *same* horizon list from the *same* entry instant. Any
asymmetry between them then has to come from the market rather than from two
resolvers that disagree about what a 15-minute hold is.

Execution semantics, once, here:

* a long option enters at the ASK and exits at the BID;
* long futures enter at the ASK and exit at the BID;
* short futures enter at the BID and exit at the ASK.

The cost decomposition (§7) is kept as *points* plus the entry price rather than
as a single net figure, and that is not tidiness: every stress in §20 and every
band in §17 needs to re-charge the same trade at a different cost, and
recomputing net from stored gross is the only way to guarantee the stressed
number is the same trade rather than a second measurement.

Nothing here reads a quote from a later session, forward-fills a gap, or
substitutes a traded price for a missing side. A horizon with no measured quote
is ``None``, which is why the tables carry a sample size per horizon instead of
one sample size per vehicle.
"""
from __future__ import annotations

from app.research.phase35 import (
    CE,
    FUTURES,
    LONG,
    MEASURED_EXECUTABLE,
    NO_PATH,
    PE,
    SHORT,
    UNMEASURED,
    book,
    normalize_direction,
)
from app.research.phase36 import (
    CLOSE,
    COMPARISON_HORIZONS,
    COUNTERFACTUAL,
    DIRECTIONAL,
    ENTRY_CONFIRMATION,
)
from app.research.phase36 import pathindex as p36pathindex

# A futures tick, used only by the §20 slippage stress. Measured per instrument
# from the captured book rather than assumed, and defaulted to nothing: a stress
# that invents a tick size is stressing an assumption.
NO_TICK = "TICK_SIZE_UNKNOWN"


def leg_direction(vehicle: str, direction: str) -> str:
    """A long option is long its own premium; only futures carry the view.

    Normalised rather than upper-cased so an upstream "BEARISH" cannot arrive
    here, fail an equality test against SHORT, and be priced as a long.
    """
    if vehicle != FUTURES:
        return LONG
    return normalize_direction(direction) or LONG


def entry_side_price(quote: dict, *, vehicle: str, direction: str) -> dict:
    return book.entry_fill(quote, vehicle=vehicle, direction=leg_direction(
        vehicle, direction,
    ))


def _cost(
    *, vehicle: str, instrument: str, entry: float, exit_price: float | None,
    lot_size: int | None,
) -> dict:
    """Round-trip cost, decomposed, on an executable fill.

    ``executable=True`` is passed because a triple is executable by construction
    — that is what :func:`app.research.phase36.triples.executable` guarantees —
    so the spread is already inside the two prices and must not be added again.
    """
    out = book.cost_of(
        vehicle, instrument, entry=entry, exit_price=exit_price,
        lot_size=lot_size, executable=True,
    )
    return {
        "cost_points": out.get("cost_points"),
        "spread_points": out.get("spread_points") or 0.0,
        "brokerage_points": out.get("brokerage_points"),
        "tax_points": out.get("statutory_points"),
        "slippage_points": out.get("slippage_points") or 0.0,
        "evidence": out.get("evidence"),
        "reason": out.get("reason"),
        "spread_note": out.get("spread_note"),
    }


def net_from_gross(
    gross_pct: float | None, *, cost_points: float | None, entry: float,
    cost_multiple: float = 1.0, extra_points: float = 0.0,
) -> float | None:
    """NET = GROSS − (SPREAD + BROKERAGE + TAX + SLIPPAGE), all in entry percent.

    One function, used by the headline table and by every stress row, so a
    stressed number cannot drift from the base number by a different formula.
    """
    if gross_pct is None or cost_points is None or entry <= 0:
        return None
    charged = float(cost_points) * float(cost_multiple) + float(extra_points)
    return round(float(gross_pct) - 100.0 * charged / entry, 4)


def _entry_quote(
    contract, *, offset: float | str, entry_ts: float,
    quote: dict, vehicle: str, direction: str, base_entry: float | None,
) -> tuple[dict | None, float | None]:
    """The quote a delayed or confirmation-gated entry would have used.

    ``offset`` of 0 is the decision instant itself. A numeric offset takes the
    first measured quote at least that many minutes later — not a quote
    interpolated to exactly that minute, because that minute may never have been
    quoted. ``AFTER_CONFIRMATION`` takes the first later quote at which the leg
    was already showing a gross profit from the original entry; it is a
    diagnostic definition, stated here so nobody reads it as a rule the system
    has.
    """
    if offset == 0.0:
        return quote, entry_ts
    if offset == ENTRY_CONFIRMATION:
        if base_entry is None:
            return None, None
        # A price above the entry is the candidate; the rounded percent settles
        # it, because a move too small to survive 4dp is not a profit the walk
        # would have reported either.
        floor = float(base_entry)
        while True:
            s = contract.first_above(entry_ts, price=floor)
            if s is None:
                return None, None
            px = float(s["fill"]["price"])
            if book.gross_pct(
                base_entry, px, vehicle=vehicle,
                direction=leg_direction(vehicle, direction),
            ) > 0:
                return s, float(s["ts"])
            floor = px
    s = contract.first_after_minutes(entry_ts, minutes=float(offset))
    if s is None:
        return None, None
    return s, float(s["ts"])


def resolve(
    con,
    triple: dict,
    vehicle: str,
    *,
    role: str,
    offset: float | str = 0.0,
    paths: p36pathindex.PathIndex | None = None,
    walk: str = "index",
) -> dict:
    """One vehicle's outcome table for one triple.

    The returned row carries the entry, the cost decomposition and a per-horizon
    gross/net/MFE/MAE/giveback table. It never carries a "best" figure chosen
    across horizons — that choice belongs to the aggregate tables, where it can
    be made once for the whole sample instead of per trade with hindsight.
    """
    leg = triple[vehicle]
    instrument = triple["instrument"]
    direction = triple["direction"]
    dirn = leg_direction(vehicle, direction)
    quote = {
        "bid": leg.get("bid"), "ask": leg.get("ask"), "traded": None,
        "evidence": MEASURED_EXECUTABLE, "reason": None,
    }
    base = {
        "obs_id": triple["obs_id"],
        "instrument": instrument,
        "vehicle": vehicle,
        "role": role,
        "direction": direction,
        "leg_direction": dirn,
        "entry_offset": offset,
        "session": triple.get("session"),
        "ts": triple["ts"],
        "symbol": leg.get("symbol"),
        "expiry": leg.get("expiry"),
        "dte": leg.get("dte"),
        "strike": leg.get("strike"),
        "atm_bucket": leg.get("atm_bucket"),
        "atm_steps": leg.get("atm_steps"),
        "premium": leg.get("premium"),
        "spread": leg.get("spread"),
        "spread_pct": leg.get("spread_pct"),
        "delta": leg.get("delta"),
        "iv": leg.get("iv"),
        "oi": leg.get("oi"),
        "volume": leg.get("volume"),
        "engine_selected": triple.get("engine_selected"),
        "engine_vehicle": triple.get("engine_vehicle"),
        "horizons": {},
        "evidence": UNMEASURED,
        "reason": None,
    }

    lot = leg.get("lot_size") or triple.get("lot_size")
    contract = (paths or p36pathindex.PathIndex()).contract(
        con, symbol=leg.get("symbol"), vehicle=vehicle, direction=dirn,
        at_ts=float(triple["ts"]),
    )
    if contract is None or not contract.count_after(float(triple["ts"])):
        base["reason"] = NO_PATH
        return base

    first = entry_side_price(quote, vehicle=vehicle, direction=direction)
    entry_quote, entry_ts = _entry_quote(
        contract, offset=offset, entry_ts=float(triple["ts"]), quote=quote,
        vehicle=vehicle, direction=direction, base_entry=first.get("price"),
    )
    if entry_quote is None or entry_ts is None:
        base["reason"] = "NO_QUOTE_AT_REQUESTED_ENTRY_OFFSET"
        return base
    fill = entry_side_price(entry_quote, vehicle=vehicle, direction=direction)
    if fill["price"] is None:
        base["reason"] = fill["reason"]
        return base

    entry = float(fill["price"])
    cost = _cost(
        vehicle=vehicle, instrument=instrument, entry=entry, exit_price=None,
        lot_size=lot,
    )
    base.update({
        "entry_price": entry,
        "entry_side": fill["side"],
        "entry_ts": entry_ts,
        "lot_size": lot,
        "cost": cost,
        "cost_pct": (
            round(100.0 * float(cost["cost_points"]) / entry, 4)
            if cost.get("cost_points") is not None else None
        ),
    })

    if walk == "scan":
        walked = p36pathindex.walk_scan(
            contract.rows_after(entry_ts), entry_ts=entry_ts,
            sign=book.gross_sign(vehicle, dirn),
            horizons=COMPARISON_HORIZONS,
        )
    else:
        walked = contract.walk(
            entry_ts=entry_ts, horizons=COMPARISON_HORIZONS,
        )
    if not walked["forward"]:
        base["reason"] = NO_PATH
        return base

    def gross(px: float | None) -> float:
        """Zero when the stretch held no priced quote, as the walk reported."""
        if px is None:
            return 0.0
        return book.gross_pct(entry, float(px), vehicle=vehicle, direction=dirn)

    horizons: dict[str, dict] = {}
    for key, (px, best_px, worst_px, ts) in walked["points"].items():
        peak_at = max(0.0, gross(best_px))
        horizons[key] = _snap(
            gross(px), peak_at, min(0.0, gross(worst_px)), peak_at, entry, cost,
            ts, entry_ts, float(px),
        )
    last_px, best_px, worst_px, last_ts = walked["close"]
    last_g = gross(last_px)
    peak = max(0.0, gross(best_px))
    mae = min(0.0, gross(worst_px))
    horizons[CLOSE] = _snap(last_g, peak, mae, peak, entry, cost, last_ts,
                            entry_ts, None)

    # A traded-price exit is not executable evidence; the path keeps it for
    # shape but the row is degraded so no table can present it as a bid-out
    # fill.
    executable_all = walked["executable_all"]
    peak_ts = walked["best"][1] if peak > 0 else entry_ts
    trough_ts = walked["worst"][1] if mae < 0 else entry_ts

    base.update({
        "horizons": horizons,
        "samples": walked["forward"],
        "evidence": MEASURED_EXECUTABLE if executable_all else "SPREAD_MODELLED",
        "giveback": {
            "peak_pct": round(peak, 4),
            "time_to_peak_min": round((peak_ts - entry_ts) / 60.0, 2),
            "final_pct": round(last_g, 4),
            "max_giveback_pct": round(max(0.0, peak - last_g), 4),
            "pct_of_mfe_given_back": (
                round(100.0 * max(0.0, peak - last_g) / peak, 2)
                if peak > 0 else None
            ),
            "returned_to_entry": peak > 0 and last_g <= 0,
            "turned_negative_after_profitable": (
                peak > 0 and (net_from_gross(
                    last_g, cost_points=cost.get("cost_points"), entry=entry,
                ) or 0.0) < 0
            ),
            "adverse_first": bool(mae < 0 and trough_ts < peak_ts),
            "mae_pct": round(mae, 4),
            "hold_minutes": round((last_ts - entry_ts) / 60.0, 2),
        },
    })
    return base


def _snap(
    gross: float, mfe: float, mae: float, peak: float, entry: float, cost: dict,
    ts: float, entry_ts: float, exit_price: float | None,
) -> dict:
    return {
        "gross_pct": round(gross, 4),
        "net_pct": net_from_gross(
            gross, cost_points=cost.get("cost_points"), entry=entry,
        ),
        "mfe_pct": round(mfe, 4),
        "mae_pct": round(mae, 4),
        "giveback_pct": round(max(0.0, peak - gross), 4),
        "exit_price": exit_price,
        "hold_min": round((ts - entry_ts) / 60.0, 2),
    }


def vehicles_of(triple: dict) -> list[tuple[str, str]]:
    """The vehicles to resolve for a triple, with their §9 role.

    FUTURES and the direction-matching option are DIRECTIONAL: they are what a
    system would actually take on this view. The other option side is resolved
    too, as a COUNTERFACTUAL, because §9 asks for it — but it is tagged so no
    aggregate can let it win a vehicle comparison it was never eligible for.
    """
    want = triple["directional_option"]
    other = PE if want == CE else CE
    return [
        (FUTURES, DIRECTIONAL),
        (want, DIRECTIONAL),
        (other, COUNTERFACTUAL),
    ]


def resolve_all(
    con, triples: list[dict], *, offset: float | str = 0.0,
    walk: str = "index",
) -> list[dict]:
    """Resolve every vehicle of every triple at one entry offset.

    One :class:`~app.research.phase36.pathindex.PathIndex` for the whole pass:
    every leg on a contract shares that contract's session read and its range
    extremes, which is the difference between a pass that finishes and one that
    does not. Triples arrive in time order, so the index holds one session.
    """
    rows: list[dict] = []
    paths = p36pathindex.PathIndex()
    for t in triples:
        for vehicle, role in vehicles_of(t):
            rows.append(resolve(con, t, vehicle, role=role, offset=offset,
                                paths=paths, walk=walk))
    return rows


def short_direction_count(triples: list[dict]) -> dict[str, int]:
    out = {LONG: 0, SHORT: 0}
    for t in triples:
        d = normalize_direction(t.get("direction")) or ""
        if d in out:
            out[d] += 1
    return out
