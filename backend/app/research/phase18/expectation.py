"""What the card is allowed to say the move might be — and on what evidence.

These are the numbers the CAS card shows beside the live book: expected move in
points, the range that expectation came from, move %, move / ATR, the expected
option move, T1/T2/T3 and an estimated time to T1.

Every one of them is an extrapolation, and the honest part of this module is the
``basis`` and ``samples`` that travel with them. CAS is weeks old, so on day one
the expectation is derived from ATR — the volatility the instrument already has —
and is labelled ``ATR_PRIOR``. Only once measured CAS windows exist does the
basis become ``CAS_MEASURED``, and only once §26's minimum sample exists does it
stop carrying a warning. A card that printed "expected move 180 points" from two
observations, with no way to see that it came from two observations, is the exact
failure this phase was built to avoid.

Time to T1 is the strictest of them: it is a median of *resolved paper legs that
actually reached T1*, and it is ``None`` until enough of those exist. There is no
model estimate, because a modelled time-to-target in a twenty-minute auction
would be a number invented to fill a field.
"""
from __future__ import annotations

from statistics import median

from app.research.phase18 import schema

ATR_PRIOR = "ATR_PRIOR"
CAS_MEASURED = "CAS_MEASURED"
NO_BASIS = "NO_BASIS"

# The share of a day's ATR the closing auction is assumed to be worth before any
# CAS window has been measured. Deliberately modest and deliberately visible: it
# is a placeholder for evidence, not a finding.
ATR_SHARE = 0.35
# Below this many measured windows the measured basis still carries a warning.
MIN_WINDOWS = 20
# Below this many resolved T1 legs, time-to-T1 is not shown at all.
MIN_T1_LEGS = 10


def _f(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return None if f != f else f


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def measured_ranges(
    per_session: list[dict], instrument: str
) -> list[float]:
    """Observed CAS ranges in points for one instrument, from §6 output."""
    out: list[float] = []
    for row in per_session:
        if str(row.get("instrument") or "") != instrument.upper():
            continue
        rng = _f(row.get("range_points"))
        if rng is not None and rng > 0:
            out.append(rng)
    return out


def expected_move(
    *,
    instrument: str,
    underlying: float | None,
    atr: float | None,
    per_session: list[dict] | None = None,
) -> dict:
    """The four underlying fields on the card, with their basis attached."""
    ranges = measured_ranges(per_session or [], instrument)
    if ranges:
        points = median(ranges)
        low = _quantile(ranges, 0.25)
        high = _quantile(ranges, 0.75)
        basis = CAS_MEASURED
        samples = len(ranges)
    elif atr and atr > 0:
        points = float(atr) * ATR_SHARE
        low, high = points * 0.5, points * 1.5
        basis = ATR_PRIOR
        samples = 0
    else:
        return {
            "expected_move_points": None,
            "move_range_low_points": None,
            "move_range_high_points": None,
            "expected_move_pct": None,
            "expected_move_atr": None,
            "basis": NO_BASIS,
            "samples": 0,
            "warning": "No ATR and no measured CAS window — nothing to state.",
        }

    warning = None
    if basis == ATR_PRIOR:
        warning = (
            f"No measured CAS window for {instrument.upper()} yet. This is "
            f"{ATR_SHARE:.0%} of ATR standing in for evidence, not a forecast."
        )
    elif samples < MIN_WINDOWS:
        warning = (
            f"Median of {samples} measured CAS window(s); §26 asks for "
            f"{MIN_WINDOWS} before this is treated as a distribution."
        )

    return {
        "expected_move_points": round(points, 1),
        "move_range_low_points": round(low, 1) if low is not None else None,
        "move_range_high_points": round(high, 1) if high is not None else None,
        "expected_move_pct": (
            round(100.0 * points / underlying, 3)
            if underlying and underlying > 0 else None
        ),
        "expected_move_atr": (
            round(points / atr, 2) if atr and atr > 0 else None
        ),
        "basis": basis,
        "samples": samples,
        "warning": warning,
    }


def expected_option_move(
    *,
    expected_move_points: float | None,
    delta: float | None,
    premium: float | None,
    gamma: float | None = None,
) -> dict:
    """Premium points the expected underlying move should be worth.

    First-order: ``|delta| x points``. Gamma is added as a second-order term when
    the feed supplies it, because a far-OTM strike in a 2,200-point print is
    exactly where the linear estimate understates the move — but it is reported
    separately so nobody mistakes the convex part for a measurement.
    """
    pts = _f(expected_move_points)
    d = _f(delta)
    if pts is None or d is None:
        return {
            "expected_option_move_points": None,
            "linear_points": None,
            "convex_points": None,
            "expected_option_move_pct": None,
            "note": "No delta or no expected move — left blank rather than guessed.",
        }
    linear = abs(d) * pts
    g = _f(gamma)
    convex = 0.5 * g * pts * pts if g else 0.0
    total = linear + max(0.0, convex)
    prem = _f(premium)
    return {
        "expected_option_move_points": round(total, 2),
        "linear_points": round(linear, 2),
        "convex_points": round(convex, 2) if g else None,
        "expected_option_move_pct": (
            round(100.0 * total / prem, 1) if prem and prem > 0 else None
        ),
        "note": (
            "Delta x expected underlying move, plus a gamma term where the feed "
            "quotes one. An estimate of the move, not of what it sells for — the "
            "bid decides that."
        ),
    }


def targets(
    *,
    entry: float | None,
    expected_option_move_points: float | None,
    spread: float | None,
    stop: float | None = None,
) -> dict:
    """T1/T2/T3 and the stop, in premium points.

    Targets are anchored to the expected option move rather than to fixed
    percentages, and T1 is floored at twice the measured spread: a target inside
    the round trip is not a target, it is a way to report a high hit rate. The
    5-year study showed exactly that trade — ``room=LOW`` hit T1 51.7% at a
    reward:risk of 0.92 — so the floor is deliberate.
    """
    e = _f(entry)
    move = _f(expected_option_move_points)
    sp = _f(spread) or 0.0
    if e is None or e <= 0:
        return {"stop": None, "t1": None, "t2": None, "t3": None,
                "risk": None, "reward_risk": None, "note": "no entry price"}

    # Default stop: half the entry premium, which is the practical floor for a
    # short-dated option that can lose most of its value inside the window.
    s = _f(stop)
    if s is None:
        s = round(e * 0.5, 2)
    risk = max(0.05, e - s)

    if move is None or move <= 0:
        return {"stop": s, "t1": None, "t2": None, "t3": None,
                "risk": round(risk, 2), "reward_risk": None,
                "note": "no expected option move — targets left blank"}

    t1 = max(e + 0.5 * move, e + 2.0 * sp)
    t2 = e + 1.0 * move
    t3 = e + 1.75 * move
    # Keep them ordered even when the spread floor pushed T1 past T2.
    t2 = max(t2, t1 + 0.05)
    t3 = max(t3, t2 + 0.05)
    return {
        "stop": round(s, 2),
        "t1": round(t1, 2),
        "t2": round(t2, 2),
        "t3": round(t3, 2),
        "risk": round(risk, 2),
        "reward_risk": round((t1 - e) / risk, 2) if risk > 0 else None,
        "t1_floored_by_spread": bool(e + 0.5 * move < e + 2.0 * sp),
        "note": (
            "T1 is floored at twice the measured spread so a target cannot sit "
            "inside its own round trip."
        ),
    }


def time_to_t1(paper_rows: list[dict], *, instrument: str | None = None) -> dict:
    """Median seconds from entry to T1, from resolved CAS paper legs only."""
    secs: list[float] = []
    for r in paper_rows:
        if instrument and str(r.get("instrument") or "") != instrument.upper():
            continue
        v = _f(r.get("seconds_to_t1"))
        if v is not None and v >= 0:
            secs.append(v)
    if len(secs) < MIN_T1_LEGS:
        return {
            "estimated_seconds_to_t1": None,
            "samples": len(secs),
            "note": (
                f"{len(secs)} resolved leg(s) reached T1; at least {MIN_T1_LEGS} "
                "are needed before an estimate is shown. No model stands in."
            ),
        }
    return {
        "estimated_seconds_to_t1": round(median(secs)),
        "p25_seconds": round(_quantile(secs, 0.25) or 0.0),
        "p75_seconds": round(_quantile(secs, 0.75) or 0.0),
        "samples": len(secs),
        "note": "Median of resolved CAS paper legs that reached T1.",
    }


def card_block(
    *,
    instrument: str,
    underlying: float | None,
    atr: float | None,
    premium: float | None,
    delta: float | None,
    gamma: float | None,
    spread: float | None,
    per_session: list[dict] | None,
    paper_rows: list[dict] | None,
) -> dict:
    """Everything the card needs, in one call, with its evidence attached."""
    em = expected_move(
        instrument=instrument, underlying=underlying, atr=atr,
        per_session=per_session,
    )
    eom = expected_option_move(
        expected_move_points=em["expected_move_points"],
        delta=delta, premium=premium, gamma=gamma,
    )
    tg = targets(
        entry=premium,
        expected_option_move_points=eom["expected_option_move_points"],
        spread=spread,
    )
    t1 = time_to_t1(paper_rows or [], instrument=instrument)
    return {
        "current_underlying": underlying,
        "cas_expected_move_points": em["expected_move_points"],
        "cas_move_range": {
            "low": em["move_range_low_points"], "high": em["move_range_high_points"],
        },
        "move_pct": em["expected_move_pct"],
        "move_atr": em["expected_move_atr"],
        "expectation_basis": em["basis"],
        "expectation_samples": em["samples"],
        "expectation_warning": em["warning"],
        "current_option_premium": premium,
        "expected_option_move_points": eom["expected_option_move_points"],
        "expected_option_move_pct": eom["expected_option_move_pct"],
        "t1": tg["t1"],
        "t2": tg["t2"],
        "t3": tg["t3"],
        "stop": tg["stop"],
        "risk": tg["risk"],
        "reward_risk": tg["reward_risk"],
        "estimated_seconds_to_t1": t1["estimated_seconds_to_t1"],
        "time_to_t1_samples": t1["samples"],
        "time_to_t1_note": t1["note"],
        "strategy": schema.STRATEGY,
        "paper_only": True,
    }
