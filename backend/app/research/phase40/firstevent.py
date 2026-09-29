"""Phase 40 §4/§5 — the predeclared thresholds and the race between them.

For every sampled instant Phase 39 could price, this module walks the same
stored contract-session forward and records the *first* of two events:

* the leg could have been closed ``+COST_CLEARING_MOVE`` above its entry fill —
  the move that pays for the round trip, taken from
  :func:`app.research.phase39.cost.required_move` and not recomputed here;
* it could only have been closed ``-COST_CLEARING_MOVE`` below it — the same
  distance the other way.

Both thresholds are set from the book at the entry instant, before any later
quote is read, so nothing about the outcome can influence the distance. The two
are equal in size by construction: an asymmetric pair would decide the race
before it was run.

**The race is run on executable prices, never on the mid — and both ends are the
same side of the book.** The origin is where the leg could have been closed at
the entry instant and each later sample is where it could have been closed then:
bid-to-bid for a long, ask-to-ask for a short future.

That origin is the correction of a real error, and it changed the answer. The
threshold is Phase 39's ``required_move`` — ``spread + brokerage + statutory`` —
so the quoted width is *inside the threshold*. Racing from the entry **fill**
put the width inside the movement as well: a leg that never moved at all already
stood a full spread down and tripped the adverse threshold on its first quote.
The favourable side was charged the spread twice and the adverse side was handed
it free. Exit-side to exit-side charges it exactly once, in the threshold, where
Phase 39 put it, and a market that did not move now scores zero.

The race is decided in **points** against ``required_points`` rather than in
percent, so no denominator has to be chosen between the fill and the mid.
Percentages are derived afterwards, for reading only.

Two further consequences are deliberate:

* the money is still counted from the fill. :func:`leg_gross_pct` and
  :func:`leg_net_pct` run entry-fill to exit side, so the width is paid there
  too — a leg can clear its favourable threshold and still be worth little;
* a quote with no executable exit side contributes no sample. It is not
  bridged, forward-filled or replaced by its mid — an instant nobody could have
  traded out of is not evidence about a threshold being reached.

Everything else this module records — peak, time to peak, MAE, the exit-side
price at each horizon — is measured on the same single pass, because a second
pass over the same rows is a second chance to disagree with the first.
"""
from __future__ import annotations

from app.research.phase35 import book
from app.research.phase39 import cost as p39cost
from app.research.phase39 import movement as p39movement
from app.research.phase40 import (
    ADVERSE_FIRST,
    BOARD,
    CLOSE,
    ENGINE,
    FAVOURABLE_FIRST,
    FIRST_EVENT_HORIZONS,
    NEITHER_REACHED,
    UNCOVERED_WINDOW,
)

NO_PATH = p39movement.NO_PATH
NO_SYMBOL = "NO_CONTRACT_SYMBOL"


def _key(h: float) -> str:
    return str(int(h)) if h == int(h) else str(h)


HORIZON_KEYS: tuple[str, ...] = (
    *(_key(h) for h in FIRST_EVENT_HORIZONS), CLOSE,
)


def cohort(row: dict) -> str:
    """§10 — the observation's own recorded selection, never inferred.

    ``engine_selected`` is the field the engine wrote when it chose to act, so
    a leg is ENGINE only when the engine actually selected it; everything else
    the board offered is BOARD_ONLY. ``source`` is deliberately not consulted
    as a substitute: a label saying where an opportunity came from is not the
    engine's record of having selected it.
    """
    return ENGINE if row.get("engine_selected") else BOARD


def selections(con, obs_ids: list[str]) -> dict[str, bool]:
    """``engine_selected`` read from ``raw_observation`` for these instants.

    Read here rather than taken from Phase 39's projection so §10 cannot be
    silently answered by a column another phase stopped carrying: a missing
    field would put every leg in BOARD_ONLY and read as "the engine selected
    nothing", which is a finding rather than an absence. Read-only, and
    parameterised — the ids come from the store, but never as text in SQL.
    """
    out: dict[str, bool] = {}
    unique = list(dict.fromkeys(obs_ids))
    for i in range(0, len(unique), 400):
        chunk = unique[i:i + 400]
        marks = ",".join("?" for _ in chunk)
        rows = con.execute(
            "SELECT obs_id, engine_selected FROM raw_observation "  # noqa: S608
            f"WHERE obs_id IN ({marks})",
            tuple(chunk),
        ).fetchall()
        for r in rows:
            out[str(r["obs_id"])] = bool(r["engine_selected"])
    return out


def walk(
    contract: p39movement._ContractSession, *, entry_ts: float,
    entry_mid: float, entry_fill: float, entry_exit_px: float,
    vehicle: str, direction: str, threshold_points: float,
    threshold_pct: float,
) -> dict:
    """One leg's race, its excursions, and its exit-side price per horizon.

    The favourable and adverse thresholds are crossed by a *quote*, not by a
    candle, so the first-event answer needs no assumption about the order of
    events inside a bar. A quote either offered an exit past the threshold or
    it did not.

    ``entry_exit_px`` is the origin of the race: the side that could have been
    hit at the entry instant, from the same book the fill came from. The
    excursion is that price against the same side later, in points, so the
    quoted width is charged once — inside ``threshold_points`` — and never twice.
    ``entry_mid`` and ``entry_fill`` are carried for reporting and for the money
    columns, which are measured from the fill because that is what was paid.

    ``covered_min`` is how far the store could actually follow this leg. A
    horizon beyond it is :data:`UNCOVERED_WINDOW` rather than
    :data:`NEITHER_REACHED`, because "the move did not happen" and "nobody was
    watching" are different findings and only one of them is about the market.
    """
    sign = book.gross_sign(vehicle, direction)
    fav_min: float | None = None
    adv_min: float | None = None
    best_pts, best_min = 0.0, 0.0
    worst_pts, worst_min = 0.0, 0.0
    best_exit: float | None = None
    # (elapsed minutes, move %, move points, exit-side price)
    samples: list[tuple[float, float, float, float | None]] = []

    start = p39movement._first_after(contract.ts, entry_ts)
    for i in range(start, len(contract)):
        exit_px = contract.exit_px[i]
        if exit_px is None or not contract.exit_executable[i]:
            continue
        ts = contract.ts[i]
        elapsed = (ts - entry_ts) / 60.0
        move_pts = sign * (exit_px - entry_exit_px)
        move = 100.0 * move_pts / entry_fill
        samples.append((elapsed, move, move_pts, exit_px))
        if move_pts > best_pts:
            best_pts, best_min = move_pts, elapsed
            best_exit = exit_px
        if move_pts < worst_pts:
            worst_pts, worst_min = move_pts, elapsed
        if fav_min is None and move_pts >= threshold_points:
            fav_min = elapsed
        if adv_min is None and move_pts <= -threshold_points:
            adv_min = elapsed
    if not samples:
        return {"reason": NO_PATH}

    covered_min = samples[-1][0]
    horizons: dict[str, dict] = {}
    for hkey in HORIZON_KEYS:
        limit = covered_min if hkey == CLOSE else float(hkey)
        inside = [s for s in samples if s[0] <= limit]
        fav = fav_min if fav_min is not None and fav_min <= limit else None
        adv = adv_min if adv_min is not None and adv_min <= limit else None
        if fav is not None and (adv is None or fav < adv):
            event = FAVOURABLE_FIRST
        elif adv is not None and (fav is None or adv < fav):
            event = ADVERSE_FIRST
        elif not inside or (hkey != CLOSE and covered_min < limit):
            # Nothing happened and the window was not watched to its end.
            event = UNCOVERED_WINDOW
        else:
            event = NEITHER_REACHED
        window = inside or [(0.0, 0.0, 0.0, None)]
        mfe = max(0.0, max(s[1] for s in window))
        mae = min(0.0, min(s[1] for s in window))
        end_move = window[-1][1]
        end_exit = window[-1][3]
        peak = max(window, key=lambda s: s[2])
        peak_exit = peak[3] if peak[2] > 0 else None
        horizons[hkey] = {
            "event": event,
            "hold_min": round(window[-1][0], 2),
            "covered": event != UNCOVERED_WINDOW,
            # The race can be decided before the window is covered: nothing
            # arriving later can precede an event that already happened. The
            # *money* at such a horizon, though, was measured early — a leg
            # whose quotes stop at minute two has no thirty-minute net. The
            # flag keeps those legs in the first-event shares and out of every
            # economic column, rather than reporting one hold as another.
            "truncated": bool(hkey != CLOSE and covered_min < limit),
            "time_to_favourable_min": None if fav is None else round(fav, 2),
            "time_to_adverse_min": None if adv is None else round(adv, 2),
            "mfe_pct": round(mfe, 6),
            "mae_pct": round(mae, 6),
            "end_pct": round(end_move, 6),
            # The race is decided on these, and §6's cost/movement is taken
            # from them, because points share the units of required_points and
            # so need no denominator chosen for them.
            "mfe_points": round(max(0.0, max(s[2] for s in window)), 6),
            "mae_points": round(min(0.0, min(s[2] for s in window)), 6),
            "end_points": round(window[-1][2], 6),
            "time_to_peak_min": round(peak[0], 2),
            "peak_pct": round(max(0.0, peak[1]), 6),
            "peak_points": round(max(0.0, peak[2]), 6),
            "peak_exit_px": peak_exit,
            "end_exit_px": end_exit,
            # §8 — for an adverse-first leg, what happened before the adverse
            # threshold and whether the favourable one arrived afterwards.
            "mfe_before_adverse_pct": (
                None if adv is None else round(max(
                    [0.0] + [s[1] for s in samples if s[0] < adv],
                ), 6)
            ),
            "favourable_after_adverse_min": (
                None if adv is None or fav is None or fav <= adv
                else round(fav - adv, 2)
            ),
        }
    return {
        "reason": None,
        "threshold_pct": round(threshold_pct, 6),
        "threshold_points": round(threshold_points, 6),
        "entry_exit_px": round(entry_exit_px, 6),
        "covered_min": round(covered_min, 2),
        "priced_forward": len(samples),
        "first_favourable_min": None if fav_min is None else round(fav_min, 2),
        "first_adverse_min": None if adv_min is None else round(adv_min, 2),
        "session_mfe_pct": round(100.0 * max(0.0, best_pts) / entry_fill, 6),
        "session_mae_pct": round(100.0 * min(0.0, worst_pts) / entry_fill, 6),
        "session_time_to_peak_min": round(best_min, 2),
        "session_time_to_trough_min": round(worst_min, 2),
        "session_peak_exit_px": best_exit,
        "horizons": horizons,
    }


def legs(con, *, instrument: str | None = None) -> dict:
    """Every instant Phase 39 could price, raced and decomposed.

    The instants, the executability predicate, the lot resolution and the cost
    arithmetic are all Phase 39's — this phase adds the race and nothing else,
    so a disagreement between the two reports cannot come from a second
    definition of the same word.
    """
    picked = p39movement.instants(con, instrument=instrument)
    paths = p39movement.Paths()
    out: list[dict] = []
    refused: dict[str, int] = {}

    for q in picked["instants"]:
        vehicle = str(q["vehicle"])
        inst = str(q["instrument"])
        direction = str(q["leg_direction"])
        req = p39cost.required_move(
            vehicle=vehicle, instrument=inst, bid=q["bid"], ask=q["ask"],
            lot_size=q.get("lot_size"),
        )
        if req.get("required_points") is None:
            reason = str(req.get("reason"))
            refused[reason] = refused.get(reason, 0) + 1
            continue
        symbol = q.get("symbol")
        if not symbol:
            refused[NO_SYMBOL] = refused.get(NO_SYMBOL, 0) + 1
            continue
        fill = book.entry_fill(q, vehicle=vehicle, direction=direction)
        if fill["price"] is None:
            reason = str(fill["reason"])
            refused[reason] = refused.get(reason, 0) + 1
            continue
        # The origin of the race: the side that could have been hit at the
        # entry instant, from the same book the fill came from. Taken through
        # Phase 35 so the entry and the exit cannot disagree about which side
        # of the book belongs to which direction.
        out_at_entry = book.exit_fill(q, vehicle=vehicle, direction=direction)
        if out_at_entry["price"] is None:
            reason = str(out_at_entry["reason"])
            refused[reason] = refused.get(reason, 0) + 1
            continue
        raced = walk(
            paths.get(
                con, symbol=str(symbol), vehicle=vehicle, direction=direction,
                at_ts=float(q["ts"]),
            ),
            entry_ts=float(q["ts"]), entry_mid=float(req["mid"]),
            entry_fill=float(fill["price"]),
            entry_exit_px=float(out_at_entry["price"]), vehicle=vehicle,
            direction=direction,
            threshold_points=float(req["required_points"]),
            threshold_pct=float(req["required_pct_of_mid"]),
        )
        if raced.get("reason") is not None:
            reason = str(raced["reason"])
            refused[reason] = refused.get(reason, 0) + 1
            continue
        out.append({
            "obs_id": str(q["obs_id"]),
            "instrument": inst,
            "vehicle": vehicle,
            "cohort": cohort(q),
            "source": q.get("source"),
            "session": str(q["session"]),
            "ts": float(q["ts"]),
            "symbol": str(symbol),
            "leg_direction": direction,
            "lot_size": req["lot_size"],
            "lot_source": q.get("lot_source"),
            "entry_mid": float(req["mid"]),
            "entry_fill": float(fill["price"]),
            "required_pct": float(req["required_pct_of_mid"]),
            "required_points": float(req["required_points"]),
            "spread_pct": float(req["spread_pct_of_mid"]),
            "fees_points": float(req["fees_points"]),
            **{k: v for k, v in raced.items() if k != "reason"},
        })
    # §10 read from the observations themselves, for the instants actually
    # raced, so the split cannot depend on which columns another phase's query
    # happened to project.
    chosen = selections(con, [str(leg["obs_id"]) for leg in out])
    for leg in out:
        leg["cohort"] = cohort({
            "engine_selected": chosen.get(str(leg["obs_id"]), False),
        })
    return {
        "legs": out,
        "coverage": {
            "executable_quotes": picked["executable_quotes"],
            "sampled_instants": picked["sampled"],
            "raced": len(out),
            "skipped_no_direction": picked["skipped_no_direction"],
            "unraceable_by_reason": dict(
                sorted(refused.items(), key=lambda kv: -kv[1]),
            ),
            "cap_per_instrument_vehicle_session": (
                picked["cap_per_instrument_vehicle_session"]
            ),
        },
    }


def independent(legs_in: list[dict], *, horizon: str) -> list[dict]:
    """The subset whose windows do not overlap another leg's, per contract.

    Six hundred instants walked thirty minutes forward from one session are not
    six hundred observations of six hundred price swings; they are a few swings
    counted many times. Chosen greedily in time order — the earliest leg first,
    then the next whose entry is at least one horizon later — which is a rule
    about timestamps only, so no outcome can influence who survives the cut.
    """
    if horizon == CLOSE:
        # One window per contract-session covers the whole session, so the
        # non-overlapping subsample is one leg per contract per day.
        gap = float("inf")
    else:
        gap = float(horizon)
    keep: list[dict] = []
    last: dict[tuple[str, str, str], float] = {}
    for leg in sorted(legs_in, key=lambda x: (x["symbol"], x["vehicle"], x["ts"])):
        key = (str(leg["symbol"]), str(leg["vehicle"]), str(leg["session"]))
        prev = last.get(key)
        if prev is None or (leg["ts"] - prev) / 60.0 >= gap:
            keep.append(leg)
            last[key] = float(leg["ts"])
    return keep


def leg_gross_pct(leg: dict, snap: dict, *, at_peak: bool = False) -> float | None:
    """Gross percentage on the executable frame, before fees but after spread.

    The quoted width is inside this figure already, because the entry is a fill
    on one side and the exit a fill on the other. That is why the fee model is
    asked for ``executable=True`` in :func:`leg_net_pct` — adding a modelled
    spread on top would charge it twice.
    """
    px = snap.get("peak_exit_px") if at_peak else snap.get("end_exit_px")
    if px is None:
        return None
    return book.gross_pct(
        float(leg["entry_fill"]), float(px), vehicle=str(leg["vehicle"]),
        direction=str(leg["leg_direction"]),
    )


def leg_net_pct(leg: dict, snap: dict, *, at_peak: bool = False) -> float | None:
    """Net percentage on the executable frame: real fill in, real side out.

    Entry is the side Phase 35 says was available and exit is the side that
    could have been hit, so the quoted width is already inside the gross figure
    and only the fees are charged on top of it. ``at_peak`` prices the same leg
    at its best exit-side quote instead of the horizon's — used by §7 to say
    what the giveback cost, never as an exit rule.
    """
    px = snap.get("peak_exit_px") if at_peak else snap.get("end_exit_px")
    if px is None:
        return None
    entry = float(leg["entry_fill"])
    gross = book.gross_pct(
        entry, float(px), vehicle=str(leg["vehicle"]),
        direction=str(leg["leg_direction"]),
    )
    fees = p39cost.fee_points(
        str(leg["vehicle"]), str(leg["instrument"]), ask=entry,
        exit_price=float(px), lot_size=leg.get("lot_size"),
    )
    if fees is None:
        return None
    return round(gross - 100.0 * float(fees["fees_points"]) / entry, 6)
