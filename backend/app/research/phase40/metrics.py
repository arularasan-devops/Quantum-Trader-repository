"""Phase 40 §6/§7/§8/§11/§12 — the tables the diagnostic is judged from.

Three separations are kept everywhere in this module, because collapsing any of
them is how a first-event study reaches the wrong conclusion:

*Classified against uncovered.* Shares are taken over legs the store could
actually follow to the end of the window. A leg whose window ran past the last
captured quote is counted, printed, and excluded from the denominator — folding
it into "neither reached" would quietly turn missing data into evidence that the
market did not move.

*The race against the money.* A leg whose first event happened before its quotes
ran out is classified — nothing arriving later can precede it — but its net at
that horizon was measured early, so it is excluded from every economic column
and counted as ``truncated_economics``. Both denominators are printed, because a
first-event share and a net taken over different legs is exactly the kind of
quiet mismatch that makes a diagnostic unfalsifiable.

*Two frames, deliberately.* The race and the excursions are measured exit side
to exit side — where the leg could have been closed at entry against where it
could have been closed later — because the threshold they are compared with
already contains the quoted width. The money columns are measured from the entry
fill, because that is the price that was paid, and charge brokerage, statutory
and slippage on top of a gross figure that already carries the spread once.
Neither frame charges the width twice, and which one a column belongs to is
named in that column's own docstring.

*Peak against end.* The peak is what the leg offered; the end is what it kept.
The difference between them is the giveback, and it is the number §13 needs. A
peak is never treated as an achievable exit: no rule here knows when the peak
is, which is exactly why it is a ceiling.
"""
from __future__ import annotations

import statistics

from app.research.phase40 import (
    ADVERSE_FIRST,
    FAVOURABLE_FIRST,
    GIVEN_BACK,
    NEITHER_REACHED,
    RECOVERED,
    RETAINED,
    STAYED_ADVERSE,
    UNCOVERED_WINDOW,
)
from app.research.phase40 import firstevent as fe


def _pct(part: int, whole: int) -> float | None:
    return None if whole <= 0 else round(100.0 * part / whole, 2)


def median(values: list[float]) -> float | None:
    return None if not values else round(statistics.median(values), 4)


def p75(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(0.75 * (len(ordered) - 1))))
    return round(float(ordered[int(idx)]), 4)


def _profit_factor(nets: list[float]) -> float | None:
    """Gross profit over gross loss. ``None`` when there is no loss to divide by.

    Reported as ``None`` rather than infinity, because a profit factor with an
    empty denominator is not a large number — it is an unanswered question about
    a sample with no losers in it.
    """
    wins = sum(n for n in nets if n > 0)
    losses = -sum(n for n in nets if n < 0)
    if losses <= 0:
        return None
    return round(wins / losses, 4)


def horizon_row(legs: list[dict], *, horizon: str) -> dict:
    """§6 — every required metric for one cohort at one horizon."""
    snaps = [(leg, leg["horizons"][horizon]) for leg in legs]
    events = [s["event"] for _, s in snaps]
    counts = {
        name: events.count(name) for name in (
            FAVOURABLE_FIRST, ADVERSE_FIRST, NEITHER_REACHED, UNCOVERED_WINDOW,
        )
    }
    classified = (
        counts[FAVOURABLE_FIRST] + counts[ADVERSE_FIRST]
        + counts[NEITHER_REACHED]
    )
    covered = [(leg, s) for leg, s in snaps if s["event"] != UNCOVERED_WINDOW]
    priced = [(leg, s) for leg, s in covered if not s.get("truncated")]

    fav_times = [
        float(s["time_to_favourable_min"]) for _, s in covered
        if s["event"] == FAVOURABLE_FIRST
        and s["time_to_favourable_min"] is not None
    ]
    adv_times = [
        float(s["time_to_adverse_min"]) for _, s in covered
        if s["event"] == ADVERSE_FIRST and s["time_to_adverse_min"] is not None
    ]
    nets, grosses = [], []
    for leg, snap in priced:
        net = fe.leg_net_pct(leg, snap)
        gross = fe.leg_gross_pct(leg, snap)
        if net is not None:
            nets.append(net)
        if gross is not None:
            grosses.append(gross)
    mfes = [float(s["mfe_pct"]) for _, s in priced]
    required = [float(leg["required_pct"]) for leg, _ in covered]
    # Taken in points, not percent: required_points and the excursion are both
    # in the instrument's own units, so the ratio needs no denominator chosen
    # for it and cannot pick up the half-spread difference between the fill and
    # the mid. Per instant, then the median — a ratio of two medians pairs a
    # move with a spread no instant experienced.
    cost_over_move = [
        round(float(leg["required_points"]) / float(s["mfe_points"]), 4)
        for leg, s in priced if float(s["mfe_points"]) > 0
    ]
    return {
        "horizon": horizon,
        "n": len(legs),
        "classified": classified,
        "sessions": len({str(leg["session"]) for leg in legs}),
        "counts": counts,
        "favourable_first_pct": _pct(counts[FAVOURABLE_FIRST], classified),
        "adverse_first_pct": _pct(counts[ADVERSE_FIRST], classified),
        "neither_pct": _pct(counts[NEITHER_REACHED], classified),
        "uncovered_pct": _pct(counts[UNCOVERED_WINDOW], len(legs)),
        "median_time_to_favourable_min": median(fav_times),
        "p75_time_to_favourable_min": p75(fav_times),
        "median_time_to_adverse_min": median(adv_times),
        "p75_time_to_adverse_min": p75(adv_times),
        "mfe_pct_median": median(mfes),
        "mae_pct_median": median([float(s["mae_pct"]) for _, s in priced]),
        "gross_pct_median": median(grosses),
        "net_pct_median": median(nets),
        "net_pct_total": None if not nets else round(sum(nets), 4),
        "profit_factor": _profit_factor(nets),
        "win_pct": _pct(len([n for n in nets if n > 0]), len(nets)),
        "priced_net": len(nets),
        "cost_pct_median": median(required),
        "cost_over_move_median": median(cost_over_move),
        "cost_over_move_unmeasured": len(priced) - len(cost_over_move),
        "priced_economics": len(priced),
        "truncated_economics": len(covered) - len(priced),
    }


def giveback_row(legs: list[dict], *, horizon: str) -> dict:
    """§7 — for favourable-first legs, what the peak was and what survived.

    Legs whose quotes stopped before the horizon are left out here as well as
    in §6: a peak and an end measured two minutes apart are not a giveback over
    thirty.

    ``retained`` and ``given_back`` split on the sign of the net at the horizon,
    which is a measurement rather than a chosen giveback percentage: the leg
    either ended above its own round-trip cost or it did not. The giveback
    fraction is reported separately as a distribution, so a reader can see how
    much was lost as well as how often.

    ``returned_to_entry`` is the end of the executable excursion at or below
    zero — the exit side back at the price the fill was paid, so the quoted
    width is already spent and nothing but fees separates it from a scratch.
    ``turned_negative`` is the stricter statement on net, after fees. The two
    are deliberately measured on different frames and both are printed.
    """
    rows = [
        (leg, leg["horizons"][horizon]) for leg in legs
        if leg["horizons"][horizon]["event"] == FAVOURABLE_FIRST
        and not leg["horizons"][horizon].get("truncated")
    ]
    retained, given_back = [], []
    fractions, peaks, ends, peak_nets, finals, times = [], [], [], [], [], []
    returned_to_entry = turned_negative = 0
    for leg, snap in rows:
        peak = float(snap["peak_pct"])
        end = float(snap["end_pct"])
        peaks.append(peak)
        ends.append(end)
        times.append(float(snap["time_to_peak_min"]))
        if peak > 0:
            fractions.append(round(100.0 * max(0.0, peak - end) / peak, 4))
        if end <= 0:
            returned_to_entry += 1
        net = fe.leg_net_pct(leg, snap)
        peak_net = fe.leg_net_pct(leg, snap, at_peak=True)
        if peak_net is not None:
            peak_nets.append(peak_net)
        if net is None:
            continue
        finals.append(net)
        if net < 0:
            turned_negative += 1
            given_back.append(net)
        else:
            retained.append(net)
    graded = len(retained) + len(given_back)
    return {
        "horizon": horizon,
        "favourable_first": len(rows),
        "graded": graded,
        RETAINED: len(retained),
        GIVEN_BACK: len(given_back),
        "retained_pct": _pct(len(retained), graded),
        "given_back_pct": _pct(len(given_back), graded),
        "peak_pct_median": median(peaks),
        "time_to_peak_min_median": median(times),
        "end_pct_median": median(ends),
        "given_back_fraction_of_mfe_pct_median": median(fractions),
        "given_back_fraction_of_mfe_pct_p75": p75(fractions),
        "net_at_peak_pct_median": median(peak_nets),
        "net_at_horizon_pct_median": median(finals),
        "returned_to_entry": returned_to_entry,
        "returned_to_entry_pct": _pct(returned_to_entry, len(rows)),
        "turned_negative": turned_negative,
        "turned_negative_pct": _pct(turned_negative, graded),
        "retained_net_pct_median": median(retained),
        "given_back_net_pct_median": median(given_back),
    }


def adverse_row(legs: list[dict], *, horizon: str) -> dict:
    """§8 — for adverse-first legs, what preceded the adverse move and what came
    after it.

    Both readings are printed and neither is assumed: a leg that reached the
    favourable threshold after the adverse one may have been an entry that was
    early, or an entry that was wrong and got rescued by a swing it could not
    have relied on. This phase measures the sequence; it does not interpret it.
    """
    rows = [
        (leg, leg["horizons"][horizon]) for leg in legs
        if leg["horizons"][horizon]["event"] == ADVERSE_FIRST
        and not leg["horizons"][horizon].get("truncated")
    ]
    recovered, stayed = [], []
    adv_times, before, maes, waits, finals = [], [], [], [], []
    for leg, snap in rows:
        if snap["time_to_adverse_min"] is not None:
            adv_times.append(float(snap["time_to_adverse_min"]))
        if snap["mfe_before_adverse_pct"] is not None:
            before.append(float(snap["mfe_before_adverse_pct"]))
        maes.append(float(snap["mae_pct"]))
        net = fe.leg_net_pct(leg, snap)
        if net is not None:
            finals.append(net)
        if snap["favourable_after_adverse_min"] is not None:
            waits.append(float(snap["favourable_after_adverse_min"]))
            recovered.append(net)
        else:
            stayed.append(net)
    return {
        "horizon": horizon,
        "adverse_first": len(rows),
        RECOVERED: len(recovered),
        STAYED_ADVERSE: len(stayed),
        "recovered_pct": _pct(len(recovered), len(rows)),
        "median_time_to_adverse_min": median(adv_times),
        "p75_time_to_adverse_min": p75(adv_times),
        "mfe_before_adverse_pct_median": median(before),
        "mae_pct_median": median(maes),
        "median_wait_to_favourable_min": median(waits),
        "p75_wait_to_favourable_min": p75(waits),
        "net_pct_median": median(finals),
        "recovered_net_pct_median": median([n for n in recovered
                                             if n is not None]),
        "stayed_net_pct_median": median([n for n in stayed if n is not None]),
    }


def waterfall(legs: list[dict], *, horizon: str) -> dict:
    """§12 — where the money went, in percent of the entry fill and in rupees.

    The favourable-first branch is offered → peak → giveback → cost → net; the
    adverse-first branch is entry → adverse → recovery → net. Rupees are per
    one lot and are median figures, not a book total: a total would depend on
    sizing, which this phase has no view on.
    """
    def rupees(value: float | None, lots: list[dict]) -> float | None:
        if value is None or not lots:
            return None
        per = [
            float(leg["entry_fill"]) * float(leg["lot_size"]) * value / 100.0
            for leg in lots if leg.get("lot_size")
        ]
        return None if not per else round(statistics.median(per), 2)

    fav = [
        leg for leg in legs
        if leg["horizons"][horizon]["event"] == FAVOURABLE_FIRST
        and not leg["horizons"][horizon].get("truncated")
    ]
    adv = [
        leg for leg in legs
        if leg["horizons"][horizon]["event"] == ADVERSE_FIRST
        and not leg["horizons"][horizon].get("truncated")
    ]
    g = giveback_row(legs, horizon=horizon)
    a = adverse_row(legs, horizon=horizon)
    cost = median([float(leg["required_pct"]) for leg in fav])
    peak = g["peak_pct_median"]
    end = g["end_pct_median"]
    return {
        "horizon": horizon,
        "favourable_first": {
            "n": len(fav),
            "favourable_move_pct": peak,
            "peak_pct": peak,
            "giveback_pct": (
                None if peak is None or end is None
                else round(max(0.0, peak - end), 4)
            ),
            "cost_pct": cost,
            "final_net_pct": g["net_at_horizon_pct_median"],
            "peak_rupees_one_lot": rupees(peak, fav),
            "giveback_rupees_one_lot": rupees(
                None if peak is None or end is None
                else max(0.0, peak - end), fav,
            ),
            "cost_rupees_one_lot": rupees(cost, fav),
            "final_net_rupees_one_lot": rupees(
                g["net_at_horizon_pct_median"], fav,
            ),
        },
        "adverse_first": {
            "n": len(adv),
            "adverse_move_pct": a["mae_pct_median"],
            "recovered_pct_of_legs": a["recovered_pct"],
            "final_net_pct": a["net_pct_median"],
            "adverse_rupees_one_lot": rupees(a["mae_pct_median"], adv),
            "final_net_rupees_one_lot": rupees(a["net_pct_median"], adv),
        },
    }


def by_key(legs: list[dict], field: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for leg in legs:
        out.setdefault(str(leg.get(field)), []).append(leg)
    return out


def vehicle_comparison(legs: list[dict], *, horizon: str) -> dict:
    """§11 — the three vehicles on the instants where all three were quoted.

    Grouped by observation id, which is Phase 35's identity for one decision
    instant, so this is the same-timestamp comparison Phase 36 makes rather
    than three separately-sampled populations laid side by side.
    """
    per_obs: dict[str, dict[str, dict]] = {}
    for leg in legs:
        per_obs.setdefault(str(leg["obs_id"]), {})[str(leg["vehicle"])] = leg
    complete = [
        v for v in per_obs.values()
        if len(v) >= 3 and all(
            v[k]["horizons"][horizon]["event"] != UNCOVERED_WINDOW for k in v
        )
    ]
    vehicles = sorted({k for v in per_obs.values() for k in v})
    rows = {}
    for vehicle in vehicles:
        sub = [v[vehicle] for v in complete if vehicle in v]
        if not sub:
            continue
        row = horizon_row(sub, horizon=horizon)
        give = giveback_row(sub, horizon=horizon)
        rows[vehicle] = {
            "n": row["n"],
            "favourable_first_pct": row["favourable_first_pct"],
            "adverse_first_pct": row["adverse_first_pct"],
            "neither_pct": row["neither_pct"],
            "mfe_pct_median": row["mfe_pct_median"],
            "mae_pct_median": row["mae_pct_median"],
            "net_pct_median": row["net_pct_median"],
            "given_back_pct": give["given_back_pct"],
            "given_back_fraction_of_mfe_pct_median": (
                give["given_back_fraction_of_mfe_pct_median"]
            ),
            "cost_pct_median": row["cost_pct_median"],
        }
    return {
        "horizon": horizon,
        "instants_with_all_vehicles": len(complete),
        "instants_seen": len(per_obs),
        "vehicles": rows,
    }
