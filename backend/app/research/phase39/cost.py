"""Phase 39 §1/§2 — the round trip, and the move it takes to break even.

The break-even move is the whole point of this phase, so the arithmetic is
written once, here, and used by every table:

    required_move = spread + brokerage + statutory charges

in points of the instrument's own price, and then as a percentage of the entry
**mid**. Three things about that formula are deliberate.

*The spread is charged in full.* A marketable round trip buys at the ask and
sells at the bid, so it pays the quoted width once. Movement is measured
mid-to-mid in :mod:`app.research.phase39.movement`, which means the spread is
not already inside the movement figure and must appear here — charging it in one
place and measuring movement in the other is the only arrangement in which
neither term double-counts nor quietly disappears.

*The fees come from the existing models, not from a new one.* Options are costed
by :mod:`app.analysis.option_costs` and futures by
:mod:`app.research.phase19.futcosts`, reached through Phase 35's ``book`` so a
figure here and a figure in Phase 36 describe the same charge. Phase 38's
de-overlap is reused as well, because Phase 19 reports futures brokerage inside
its statutory line and adding the published lines would charge it twice.

*The statutory charge is solved, not approximated.* It is a percentage of
turnover, so the cost of a break-even round trip depends on the exit price, which
depends on the required move. Charging at entry-level turnover would understate
the requirement — in the flattering direction — so the requirement is iterated
to a fixed point instead.

Nothing in this module reads an outcome, a later quote, or a path. A cost is a
property of the book at one instant, and that is all it is computed from.
"""
from __future__ import annotations

import json
import statistics

from app.market.instruments import REGISTRY
from app.research.phase35 import CE, FUTURES, PE, book, normalize_direction
from app.research.phase35 import path as p35path
from app.research.phase36 import triples as p36triples
from app.research.phase38 import money as p38money
from app.research.phase39 import (
    COST_FIXED_POINT_ITERATIONS,
    MAX_COST_SAMPLES_PER_KEY,
)

NO_LOT = "LOT_SIZE_UNKNOWN"
NO_FEES = "FEE_MODEL_RETURNED_NOTHING"
NO_BOOK = "UNMEASURED_NO_EXECUTABLE_BOOK"

# Where the lot came from, reported with every priced row. A lot size is a
# contract fact rather than a market observation, so a declared one is usable
# where a declared *price* would not be — but which of the three answered is
# still printed, because a registry lot can be stale after a SEBI revision and
# the store's own lot cannot.
LOT_FROM_QUOTE = "LOT_FROM_CAPTURED_QUOTE"
LOT_FROM_PLAN = "LOT_FROM_OBSERVATION_PLAN"
LOT_FROM_REGISTRY = "LOT_FROM_CONTRACT_REGISTRY"
LOT_SOURCES: tuple[str, ...] = (LOT_FROM_QUOTE, LOT_FROM_PLAN, LOT_FROM_REGISTRY)

# Long premium for options, and the recorded view for futures. An option's cost
# does not depend on the view, but a futures leg's does not either — the charge
# is symmetric — so the direction is carried for the movement side only.
OPTION_VEHICLES: tuple[str, ...] = (CE, PE)


def positive(v: object) -> float | None:
    """The value as a float when it is a usable price, else ``None``."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    f = float(v)
    return f if f == f and f not in (float("inf"), float("-inf")) and f > 0 else None


def _lot(value: object) -> int | None:
    return int(value) if isinstance(value, int) and value > 0 else None


def registry_lot(instrument: str) -> int | None:
    """The contract lot from the instrument registry, or nothing.

    Deliberately not :func:`app.market.instruments.get_spec`, which falls back to
    the default instrument for an unknown symbol — that would quietly charge
    CRUDEOIL's lot of 100 to a name nobody has a spec for.
    """
    spec = REGISTRY.get((instrument or "").upper())
    return None if spec is None else _lot(getattr(spec, "lot_size", None))


def plan_lot(context_json: object) -> int | None:
    """The lot the plan carried at the decision instant, if the store kept one."""
    if not isinstance(context_json, str) or not context_json:
        return None
    try:
        body = json.loads(context_json) or {}
    except ValueError:
        return None
    plan = body.get("plan")
    return _lot(plan.get("lot_size")) if isinstance(plan, dict) else None


def resolve_lot(row: dict) -> tuple[int | None, str | None]:
    """Lot size for one quote, and which source answered.

    Quote first, then the observation's own plan — the source Phase 36 priced
    13,730 triples from — then the registry. Refusing the round trip because the
    feed left ``lot_size`` null would report an unmeasurable cost for a contract
    whose lot is a published, checkable number.
    """
    lot = _lot(row.get("lot_size"))
    if lot is not None:
        return lot, LOT_FROM_QUOTE
    lot = plan_lot(row.get("context_json"))
    if lot is not None:
        return lot, LOT_FROM_PLAN
    lot = registry_lot(str(row.get("instrument") or ""))
    if lot is not None:
        return lot, LOT_FROM_REGISTRY
    return None, None


def fee_points(
    vehicle: str, instrument: str, *, ask: float, exit_price: float,
    lot_size: int | None,
) -> dict | None:
    """Brokerage and statutory charges for one round trip, in points.

    ``executable=True`` because both fills come from a real quoted side, so the
    spread is already accounted for by this phase separately and must not be
    added again inside the fee model.
    """
    out = book.cost_of(
        vehicle, instrument, entry=ask, exit_price=exit_price,
        lot_size=lot_size, executable=True,
    )
    total = out.get("cost_points")
    if total is None:
        return None
    parts, residual = p38money.cost_components({
        "vehicle": vehicle,
        "cost": {
            "spread_points": out.get("spread_points") or 0.0,
            "brokerage_points": out.get("brokerage_points"),
            "tax_points": out.get("statutory_points"),
            "slippage_points": out.get("slippage_points") or 0.0,
            "cost_points": total,
        },
    })
    return {
        "brokerage_points": round(float(parts["BROKERAGE"]), 6),
        "statutory_points": round(float(parts["STATUTORY"]), 6),
        "slippage_points": round(float(parts["SLIPPAGE"]), 6),
        "residual_points": round(float(residual), 6),
        # The fee model's own total, minus whatever spread it thought it was
        # charging (zero on an executable fill), so this phase's spread term is
        # the only spread in the sum.
        "fees_points": round(float(total) - float(parts["SPREAD"]), 6),
    }


def required_move(
    *, vehicle: str, instrument: str, bid: float, ask: float,
    lot_size: int | None,
) -> dict:
    """The favourable move a round trip needs just to break even.

    Returned in points and as a percentage of the entry mid, with the three
    components kept separate — an instrument can be uneconomic because its
    spread is wide or because a flat per-order fee is large against a cheap
    contract, and those have different answers.
    """
    b, a = positive(bid), positive(ask)
    mid = round((b + a) / 2.0, 6) if b is not None and a is not None else None
    if b is None or a is None or mid is None or not a > b:
        return {"required_points": None, "reason": p36triples.CROSSED_BOOK}
    lot = int(lot_size) if isinstance(lot_size, int) and lot_size > 0 else None
    if lot is None:
        return {"required_points": None, "reason": NO_LOT}

    spread_points = round(a - b, 6)
    # Fixed point: charge the fees on the turnover a break-even exit would
    # actually generate, starting from a flat exit and re-solving.
    required = spread_points
    fees: dict | None = None
    for _ in range(COST_FIXED_POINT_ITERATIONS):
        fees = fee_points(
            vehicle, instrument, ask=a, exit_price=a + required,
            lot_size=lot,
        )
        if fees is None:
            return {"required_points": None, "reason": NO_FEES}
        required = round(spread_points + fees["fees_points"], 6)
    assert fees is not None  # noqa: S101 - the loop runs at least once

    return {
        "mid": mid,
        "bid": b,
        "ask": a,
        "lot_size": lot,
        "spread_points": spread_points,
        "spread_pct_of_mid": round(100.0 * spread_points / mid, 6),
        "brokerage_points": fees["brokerage_points"],
        "statutory_points": fees["statutory_points"],
        "slippage_points": fees["slippage_points"],
        "fee_residual_points": fees["residual_points"],
        "fees_points": fees["fees_points"],
        "required_points": required,
        "required_pct_of_mid": round(100.0 * required / mid, 6),
        "reason": None,
    }


def executable_quotes(
    con, *, instrument: str | None = None,
) -> list[dict]:
    """Every real, fresh, two-sided, non-crossed quote in the store.

    Read through one SQL pass with the evidence and book conditions pushed down,
    then re-checked in Python by Phase 36's own predicate so the two definitions
    of "executable" cannot drift apart.
    """
    sql = (
        "SELECT o.instrument AS instrument, o.direction AS direction, "
        "o.obs_id AS obs_id, q.vehicle AS vehicle, q.ts AS ts, "
        "q.symbol AS symbol, q.bid AS bid, q.ask AS ask, q.traded AS traded, "
        "q.lot_size AS lot_size, q.strike AS strike, q.dte AS dte, "
        "q.evidence AS evidence, o.context_json AS context_json, "
        "o.source AS source, o.engine_selected AS engine_selected "
        "FROM raw_quote q JOIN raw_observation o ON o.obs_id = q.obs_id "
        "WHERE q.bid IS NOT NULL AND q.ask IS NOT NULL AND q.ask > q.bid "
        "AND q.bid > 0"
    )
    args: list[object] = []
    if instrument:
        sql += " AND o.instrument = ?"
        args.append(instrument)
    sql += " ORDER BY o.instrument, q.vehicle, q.ts"
    rows = []
    for r in con.execute(sql, args).fetchall():
        row = dict(r)
        if not p36triples.executable(row):
            continue
        row["session"] = p35path.session_date(float(row["ts"]))
        row["lot_size"], row["lot_source"] = resolve_lot(row)
        rows.append(row)
    return rows


def coverage(con, *, instrument: str | None = None) -> dict:
    """How much of the store is executable, and why the rest is not.

    Printed beside every sampled table so a reader can tell a thin result from
    a thin sample: an instrument with two executable quotes is not an
    instrument that cannot pay its costs.
    """
    sql = (
        "SELECT o.instrument AS instrument, q.vehicle AS vehicle, "
        "q.evidence AS evidence, q.reason AS reason, COUNT(*) AS n "
        "FROM raw_quote q JOIN raw_observation o ON o.obs_id = q.obs_id"
    )
    args: list[object] = []
    if instrument:
        sql += " WHERE o.instrument = ?"
        args.append(instrument)
    sql += " GROUP BY 1, 2, 3, 4"
    per_key: dict[tuple[str, str], dict] = {}
    for r in con.execute(sql, args).fetchall():
        key = (str(r["instrument"]), str(r["vehicle"]))
        slot = per_key.setdefault(key, {"quotes": 0, "by_reason": {}})
        slot["quotes"] += int(r["n"])
        if r["evidence"] != "MEASURED_EXECUTABLE":
            label = str(r["reason"] or r["evidence"])
            slot["by_reason"][label] = slot["by_reason"].get(label, 0) + int(r["n"])
    return {
        f"{k[0]}|{k[1]}": {
            "quotes": v["quotes"],
            "not_executable_by_reason": dict(
                sorted(v["by_reason"].items(), key=lambda kv: -kv[1]),
            ),
        }
        for k, v in sorted(per_key.items())
    }


def inventory(con, *, instrument: str | None = None) -> list[tuple[str, str]]:
    """Every instrument/vehicle pair the store holds a quote for, executable or
    not — the denominator of "every F&O instrument/vehicle".
    """
    sql = (
        "SELECT DISTINCT o.instrument AS instrument, q.vehicle AS vehicle "
        "FROM raw_quote q JOIN raw_observation o ON o.obs_id = q.obs_id"
    )
    args: list[object] = []
    if instrument:
        sql += " WHERE o.instrument = ?"
        args.append(instrument)
    return sorted(
        (str(r["instrument"]), str(r["vehicle"]))
        for r in con.execute(sql, args).fetchall()
    )


def stride(rows: list[dict], cap: int) -> list[dict]:
    """At most ``cap`` rows, evenly spaced through a time-ordered list.

    Deterministic and declared, so a re-run reads the same rows and a sampled
    figure can be checked. Even spacing rather than the first ``cap`` rows
    because the first rows of a session are the open, and the open is the least
    representative part of the day for a spread.
    """
    n = len(rows)
    if cap <= 0 or n <= cap:
        return rows
    step = n / float(cap)
    return [rows[min(n - 1, int(i * step))] for i in range(cap)]


def _summary(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median": round(statistics.median(ordered), 4),
        "mean": round(statistics.fmean(ordered), 4),
        "p25": round(percentile(ordered, 25.0), 4),
        "p75": round(percentile(ordered, 75.0), 4),
        "p90": round(percentile(ordered, 90.0), 4),
        "min": round(ordered[0], 4),
        "max": round(ordered[-1], 4),
    }


def percentile(ordered: list[float], pct: float) -> float:
    """Nearest-rank percentile of an already-sorted list."""
    if not ordered:
        raise ValueError("percentile of an empty sample")
    if len(ordered) == 1:
        return float(ordered[0])
    idx = min(len(ordered) - 1, max(0, round(pct / 100.0 * (len(ordered) - 1))))
    return float(ordered[int(idx)])


def cost_table(con, *, instrument: str | None = None) -> dict:
    """Per instrument and vehicle: what a round trip costs, from real books.

    Full coverage counts, a sampled distribution, and the median required move
    that the ranking later divides movement by. Every pair the store holds
    appears, including the ones with no executable book at all — an absent row
    would read as an instrument nobody tried.
    """
    quotes = executable_quotes(con, instrument=instrument)
    grouped: dict[tuple[str, str], list[dict]] = {}
    for q in quotes:
        grouped.setdefault((str(q["instrument"]), str(q["vehicle"])), []).append(q)

    out: dict[str, dict] = {}
    for (inst, vehicle), rows in sorted(grouped.items()):
        sampled = stride(rows, MAX_COST_SAMPLES_PER_KEY)
        priced: list[dict] = []
        refused: dict[str, int] = {}
        lot_sources: dict[str, int] = {}
        for r in sampled:
            req = required_move(
                vehicle=vehicle, instrument=inst, bid=r["bid"], ask=r["ask"],
                lot_size=r.get("lot_size"),
            )
            if req.get("required_points") is None:
                label = str(req.get("reason"))
                refused[label] = refused.get(label, 0) + 1
                continue
            src = str(r.get("lot_source"))
            lot_sources[src] = lot_sources.get(src, 0) + 1
            priced.append(req)
        out[f"{inst}|{vehicle}"] = {
            "instrument": inst,
            "vehicle": vehicle,
            "executable_quotes": len(rows),
            "sampled": len(sampled),
            "priced": len(priced),
            "unpriced_by_reason": dict(
                sorted(refused.items(), key=lambda kv: -kv[1]),
            ),
            "lot_size_sources": dict(
                sorted(lot_sources.items(), key=lambda kv: -kv[1]),
            ),
            "sessions": sorted({str(r["session"]) for r in rows}),
            "price_mid": _summary([float(p["mid"]) for p in priced]),
            "spread_pct_of_mid": _summary(
                [float(p["spread_pct_of_mid"]) for p in priced],
            ),
            "required_pct_of_mid": _summary(
                [float(p["required_pct_of_mid"]) for p in priced],
            ),
            "required_points": _summary(
                [float(p["required_points"]) for p in priced],
            ),
            # One lot, because a lot is the smallest thing that can actually be
            # bought; scaling it to a position would be a sizing claim.
            "required_rupees_one_lot": _summary([
                float(p["required_points"]) * float(p["lot_size"])
                for p in priced
            ]),
            "components_pct_of_mid": {
                name: _summary([
                    round(100.0 * float(p[field]) / p["mid"], 6)
                    for p in priced
                ])
                for name, field in (
                    ("spread", "spread_points"),
                    ("brokerage", "brokerage_points"),
                    ("statutory", "statutory_points"),
                    ("slippage", "slippage_points"),
                    ("residual", "fee_residual_points"),
                )
            },
            "formula": (
                "required_move = spread + brokerage + statutory, charged on "
                "break-even turnover, as a percent of the entry mid"
            ),
        }

    # Pairs the store holds but could never price. They are kept as rows rather
    # than dropped: an absent instrument reads as one nobody looked at, and the
    # distinction between "cannot pay its costs" and "its costs were never
    # measurable" is the one this phase most needs to preserve.
    for inst, vehicle in inventory(con, instrument=instrument):
        out.setdefault(f"{inst}|{vehicle}", {
            "instrument": inst,
            "vehicle": vehicle,
            "executable_quotes": 0,
            "sampled": 0,
            "priced": 0,
            "unpriced_by_reason": {},
            "sessions": [],
            "evidence": NO_BOOK,
        })
    return dict(sorted(out.items()))


def leg_direction(vehicle: str, direction: object) -> str | None:
    """The side the movement is measured on, decidable at the entry instant.

    A long option is long its own premium whichever way the view is expressed.
    A futures leg takes the direction the observation recorded — and where none
    was recorded the answer is ``None`` rather than LONG, because measuring the
    favourable excursion of an assumed direction is how a coin flip becomes a
    finding.
    """
    if vehicle in OPTION_VEHICLES:
        return "LONG"
    if vehicle != FUTURES:
        return None
    return normalize_direction(direction)
