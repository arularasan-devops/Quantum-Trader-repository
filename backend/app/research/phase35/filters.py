"""Phase 35 §19 — where an opportunity is economically viable. Measured, not armed.

Nothing in this module enables anything. It produces tables: net expectancy by
entry-premium band, by spread band, by cost/expected-move ratio, by room/cost
ratio and by liquidity, over the paper books. Each table is a description of a
population, and §19's warning is applied literally in :func:`verdict` — a band is
not called viable because it loses less. It has to be positive after cost, and it
has to have enough trades for that to mean anything.

The reason this is the *first* place to look rather than the last: Phase 34
measured, on real premium candles, that the entire gross edge of the live signal
family sits in sub-₹25 premiums where per-order brokerage alone is ~10.5% of
premium, and that on ₹25+ premiums the gross edge is about zero. If that repeats
here on executable books, then "the system is picking the wrong opportunities" is
the wrong diagnosis and "the system is expressing them in a vehicle that cannot
pay for itself" is the right one. Those two conclusions imply completely
different work, which is why the bands are measured per book instead of assumed.

The bands themselves are frozen in :mod:`app.research.phase35` — identical to the
Phase 34 premium bands, so the two studies' tables can be read row against row.
"""
from __future__ import annotations

from array import array

from app.research.phase35 import (
    MEASURED_EXECUTABLE,
    PREMIUM_BANDS,
    REQUIRES_MORE_DATA,
    ROOM_COST_MULTIPLES,
    SPREAD_PCT_BANDS,
    store,
)

# A band needs this many resolved legs before its mean is quoted as anything but
# a description. Lower than the §16 evidence floor on purpose: this is a
# within-sample breakdown of an already-collected pool, not a claim about a rule.
MIN_BAND_TRADES = 25


def _stats(values) -> dict:
    if not values:
        return {"trades": 0, "mean_pct": None, "median_pct": None,
                "win_rate_pct": None, "profit_factor": None}
    ordered = sorted(values)
    n = len(ordered)
    median = (
        ordered[n // 2] if n % 2
        else (ordered[n // 2 - 1] + ordered[n // 2]) / 2.0
    )
    wins = [v for v in values if v > 0]
    losses = [-v for v in values if v < 0]
    gain, loss = sum(wins), sum(losses)
    return {
        "trades": n,
        "mean_pct": round(sum(values) / n, 4),
        "median_pct": round(median, 4),
        "win_rate_pct": round(100.0 * len(wins) / n, 2),
        "profit_factor": round(gain / loss, 3) if loss > 0 else None,
    }


def verdict(stats: dict) -> str:
    """Viable, not-viable, or not enough data. Avoiding losses is not viability."""
    if stats["trades"] < MIN_BAND_TRADES or stats["mean_pct"] is None:
        return REQUIRES_MORE_DATA
    return "ECONOMICALLY_VIABLE" if stats["mean_pct"] > 0 else "NOT_VIABLE_AFTER_COST"


def _resolved(con, book: str | None) -> list[dict]:
    legs = store.legs(con, book=book, resolved=True)
    return [
        leg for leg in legs
        if leg.get("net_pct") is not None and leg.get("entry_price")
    ]


def _quote_index(con) -> dict[str, dict]:
    return {
        f"{r['obs_id']}|{r['vehicle']}": dict(r)
        for r in con.execute("SELECT * FROM raw_quote").fetchall()
    }


def premium_bands(legs: list[dict]) -> list[dict]:
    """§19 premium bands. The charge percentage is the number that decides these."""
    out = []
    for lo, hi in PREMIUM_BANDS:
        band = [
            leg for leg in legs
            if lo <= float(leg["entry_price"]) < hi
        ]
        nets = [float(leg["net_pct"]) for leg in band]
        cost_pcts = [
            100.0 * float(leg["cost_points"]) / float(leg["entry_price"])
            for leg in band if leg.get("cost_points")
        ]
        stats = _stats(nets)
        out.append({
            "entry_premium_from": lo,
            "entry_premium_to": None if hi == float("inf") else hi,
            "cost_pct_mean": (
                round(sum(cost_pcts) / len(cost_pcts), 4) if cost_pcts else None
            ),
            **stats,
            "verdict": verdict(stats),
        })
    return out


def spread_bands(legs: list[dict], quotes: dict[str, dict]) -> list[dict]:
    out = []
    for lo, hi in SPREAD_PCT_BANDS:
        band = []
        for leg in legs:
            q = quotes.get(f"{leg['obs_id']}|{leg['vehicle']}")
            sp = None if not q else q.get("spread_pct")
            if sp is not None and lo <= float(sp) < hi:
                band.append(leg)
        stats = _stats([float(leg["net_pct"]) for leg in band])
        out.append({
            "spread_pct_from": lo,
            "spread_pct_to": None if hi == float("inf") else hi,
            **stats,
            "verdict": verdict(stats),
        })
    return out


def room_cost_bands(con, legs: list[dict]) -> list[dict]:
    """Room/cost: was the best look ever worth N round trips?

    Measured from the path rather than from the plan, so it says what the market
    actually offered rather than what the plan hoped for. Read at the session
    close because that is the only horizon every leg reaches.
    """
    peaks = {
        r["leg_id"]: r["peak_pct"]
        for r in con.execute(
            "SELECT leg_id, peak_pct FROM leg_attribution"
        ).fetchall()
    }
    out = []
    bounds = list(ROOM_COST_MULTIPLES) + [float("inf")]
    for lo, hi in zip(bounds, bounds[1:]):
        band = []
        for leg in legs:
            cost = leg.get("cost_points")
            peak = peaks.get(leg["leg_id"])
            if not cost or peak is None:
                continue
            cost_pct = 100.0 * float(cost) / float(leg["entry_price"])
            if cost_pct <= 0:
                continue
            ratio = float(peak) / cost_pct
            if lo <= ratio < hi:
                band.append(leg)
        stats = _stats([float(leg["net_pct"]) for leg in band])
        out.append({
            "room_over_cost_from": lo,
            "room_over_cost_to": None if hi == float("inf") else hi,
            **stats,
            "verdict": verdict(stats),
        })
    return out


def liquidity_bands(legs: list[dict], quotes: dict[str, dict]) -> list[dict]:
    """OI and volume terciles, cut on the pool itself rather than on a guess."""
    vols, nets = array("d"), array("d")
    for leg in legs:
        q = quotes.get(f"{leg['obs_id']}|{leg['vehicle']}")
        vol = None if not q else q.get("volume")
        if vol is None:
            continue
        vols.append(float(vol))
        nets.append(float(leg["net_pct"]))
    return _liquidity(vols, nets)


def _liquidity(vols, nets) -> list[dict]:
    """Terciles over volume, cut on the pool itself.

    Takes two parallel sequences rather than pairs so the caller can stream
    them into ``array('d')`` — on the real store this is a million legs, and a
    million two-tuples is a hundred megabytes of nothing.
    """
    n = len(vols)
    if n < 3:
        return [{
            "band": "ALL", "trades": n, "mean_pct": None,
            "verdict": REQUIRES_MORE_DATA,
            "note": "no volume recorded on enough legs to cut terciles",
        }]
    order = sorted(range(n), key=vols.__getitem__)
    third = n // 3
    cuts = {
        "BOTTOM_THIRD": order[:third],
        "MIDDLE_THIRD": order[third:2 * third],
        "TOP_THIRD": order[2 * third:],
    }
    out = []
    for name, idx in cuts.items():
        stats = _stats([nets[i] for i in idx])
        out.append({
            "band": name,
            "volume_from": round(vols[idx[0]], 2) if idx else None,
            "volume_to": round(vols[idx[-1]], 2) if idx else None,
            **stats,
            "verdict": verdict(stats),
        })
    return out


def _band_of(value: float, bands) -> int | None:
    for i, (lo, hi) in enumerate(bands):
        if lo <= value < hi:
            return i
    return None


def _stream(con, book: str | None):
    """Every resolved leg with the four numbers the bands need, one row at a time.

    The tables below used to be built from ``store.legs`` plus a dictionary of
    the whole ``raw_quote`` table — two million rows on this store, held while
    the rest of the report was computed. The join gives the same pairing (the
    quote is keyed by observation and vehicle, which is its primary key) without
    the index, and the rows are consumed as they arrive.
    """
    sql = (
        "SELECT l.leg_id, l.entry_price, l.net_pct, l.cost_points,"
        " q.spread_pct AS spread_pct, q.volume AS volume, a.peak_pct AS peak_pct"
        " FROM paper_leg l"
        " LEFT JOIN raw_quote q ON q.obs_id = l.obs_id AND q.vehicle = l.vehicle"
        " LEFT JOIN leg_attribution a ON a.leg_id = l.leg_id"
        " WHERE l.resolved = 1 AND l.net_pct IS NOT NULL"
        " AND l.entry_price IS NOT NULL AND l.entry_price <> 0"
    )
    args: list = []
    if book:
        sql += " AND l.book = ?"
        args.append(book)
    return con.execute(sql + " ORDER BY l.entry_ts", args)


def study(con, *, book: str | None = None) -> dict:
    """All five §19 tables for one book, with nothing enabled anywhere."""
    premium_bounds = list(PREMIUM_BANDS)
    spread_bounds = list(SPREAD_PCT_BANDS)
    room_edges = list(ROOM_COST_MULTIPLES) + [float("inf")]
    room_bounds = list(zip(room_edges, room_edges[1:]))

    n = 0
    prem_nets = [array("d") for _ in premium_bounds]
    prem_costs = [array("d") for _ in premium_bounds]
    spread_nets = [array("d") for _ in spread_bounds]
    room_nets = [array("d") for _ in room_bounds]
    vols, vol_nets = array("d"), array("d")

    for r in _stream(con, book):
        n += 1
        entry = float(r["entry_price"])
        net = float(r["net_pct"])
        cost = r["cost_points"]
        cost_pct = 100.0 * float(cost) / entry if cost else None

        i = _band_of(entry, premium_bounds)
        if i is not None:
            prem_nets[i].append(net)
            if cost_pct is not None:
                prem_costs[i].append(cost_pct)

        if r["spread_pct"] is not None:
            i = _band_of(float(r["spread_pct"]), spread_bounds)
            if i is not None:
                spread_nets[i].append(net)

        if cost_pct is not None and cost_pct > 0 and r["peak_pct"] is not None:
            i = _band_of(float(r["peak_pct"]) / cost_pct, room_bounds)
            if i is not None:
                room_nets[i].append(net)

        if r["volume"] is not None:
            vols.append(float(r["volume"]))
            vol_nets.append(net)

    premium = []
    for (lo, hi), nets, costs in zip(premium_bounds, prem_nets, prem_costs):
        stats = _stats(nets)
        premium.append({
            "entry_premium_from": lo,
            "entry_premium_to": None if hi == float("inf") else hi,
            "cost_pct_mean": (
                round(sum(costs) / len(costs), 4) if costs else None
            ),
            **stats,
            "verdict": verdict(stats),
        })
    spread = []
    for (lo, hi), nets in zip(spread_bounds, spread_nets):
        stats = _stats(nets)
        spread.append({
            "spread_pct_from": lo,
            "spread_pct_to": None if hi == float("inf") else hi,
            **stats,
            "verdict": verdict(stats),
        })
    room = []
    for (lo, hi), nets in zip(room_bounds, room_nets):
        stats = _stats(nets)
        room.append({
            "room_over_cost_from": lo,
            "room_over_cost_to": None if hi == float("inf") else hi,
            **stats,
            "verdict": verdict(stats),
        })

    return {
        "book": book or "BOTH",
        "resolved_legs": n,
        "premium_bands": premium,
        "spread_bands": spread,
        "room_cost_bands": room,
        "liquidity_bands": _liquidity(vols, vol_nets),
        "min_band_trades": MIN_BAND_TRADES,
        "evidence": MEASURED_EXECUTABLE if n else REQUIRES_MORE_DATA,
        "enabled_anywhere": False,
        "note": (
            "a band is only called viable when its mean net is positive after cost "
            "with enough trades; avoiding losses is not the same as being "
            "profitable, and none of these bands is wired to a live filter"
        ),
    }
