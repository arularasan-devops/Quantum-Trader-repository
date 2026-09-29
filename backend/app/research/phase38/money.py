"""Phase 38 — points to rupees, and the rupee waterfall.

Phase 36 works in percent of entry and in points, which is the right unit for
comparing an ₹80 option with an ₹8,700 future. It is the wrong unit for the
question "where did the money go", so this module converts once, in one place,
and refuses rather than guesses when it cannot.

The conversion is ``points x lot_size``. Nothing else: no notional assumption,
no position sizing, no leverage model. One lot of each vehicle, per leg, because
the sizing rule is production logic and this diagnostic does not touch it. That
makes the rupee figures comparable *within* a vehicle and honest as a
composition of the loss, and it makes cross-vehicle rupee totals a statement
about one lot of each, which every table says out loud.

A leg whose lot size is unknown contributes to no rupee total and is counted in
``unmeasured_legs`` instead. A silent zero there would understate the loss and
look like good news.
"""
from __future__ import annotations

from app.research.phase35 import FUTURES
from app.research.phase36 import outcome as p36outcome

COMPONENTS: tuple[str, ...] = (
    "spread_points", "brokerage_points", "tax_points", "slippage_points",
)
COMPONENT_LABELS: dict[str, str] = {
    "spread_points": "SPREAD",
    "brokerage_points": "BROKERAGE",
    "tax_points": "STATUTORY",
    "slippage_points": "SLIPPAGE",
}


def cost_components(row: dict) -> tuple[dict[str, float], float]:
    """The four cost lines in points, de-overlapped, plus what is left over.

    One wrinkle has to be handled here rather than papered over. Phase 19's
    futures decomposition puts brokerage *inside* ``statutory_points``
    (``(brokerage + tax + txn) / qty``), while the option decomposition keeps
    the two apart. Adding the lines as published therefore charges futures
    brokerage twice, which in a loss waterfall reads as a cost problem that is
    not there. Brokerage is subtracted from the statutory line for futures so
    the four lines sum to the round trip Phase 36 actually charged.

    The leftover is returned rather than absorbed: if that decomposition changes
    again, the waterfall reports a residual instead of quietly mis-attributing
    it to whichever line is listed last.
    """
    cost = row.get("cost") or {}
    vals = {c: float(cost.get(c) or 0.0) for c in COMPONENTS}
    if row.get("vehicle") == FUTURES:
        vals["tax_points"] = round(
            vals["tax_points"] - vals["brokerage_points"], 6,
        )
    parts = {COMPONENT_LABELS[c]: vals[c] for c in COMPONENTS}
    total = cost.get("cost_points")
    residual = (
        round(float(total) - sum(parts.values()), 6)
        if isinstance(total, (int, float)) else 0.0
    )
    return parts, residual


def lot_of(row: dict) -> int | None:
    lot = row.get("lot_size")
    return int(lot) if isinstance(lot, int) and lot > 0 else None


def rupees(points: float | None, row: dict) -> float | None:
    """``points x lot_size``, or None when the lot size was never captured."""
    lot = lot_of(row)
    if lot is None or not isinstance(points, (int, float)):
        return None
    return round(float(points) * lot, 2)


def pct_to_rupees(pct: float | None, row: dict) -> float | None:
    """A percent-of-entry figure as rupees on one lot of this leg."""
    entry = row.get("entry_price")
    if not isinstance(pct, (int, float)) or not isinstance(
        entry, (int, float),
    ) or float(entry) <= 0:
        return None
    return rupees(float(pct) / 100.0 * float(entry), row)


def snap_of(row: dict, horizon: str) -> dict | None:
    snap = (row.get("horizons") or {}).get(horizon)
    return snap if isinstance(snap, dict) else None


def net_pct(row: dict, horizon: str, *, cost_multiple: float = 1.0):
    """The leg's net at one horizon, recomputed by Phase 36's own formula."""
    snap = snap_of(row, horizon)
    entry = row.get("entry_price")
    if snap is None or not isinstance(entry, (int, float)):
        return None
    return p36outcome.net_from_gross(
        snap.get("gross_pct"),
        cost_points=(row.get("cost") or {}).get("cost_points"),
        entry=float(entry), cost_multiple=cost_multiple,
    )


def leg_money(row: dict, horizon: str) -> dict | None:
    """One leg's whole rupee story at one horizon.

    ``None`` when the leg was not entered, had no measured horizon, or has no
    lot size to convert with — the caller counts those rather than filling them.
    """
    snap = snap_of(row, horizon)
    entry = row.get("entry_price")
    if snap is None or not isinstance(entry, (int, float)):
        return None
    cost = row.get("cost") or {}
    gross_r = pct_to_rupees(snap.get("gross_pct"), row)
    cost_r = rupees(cost.get("cost_points"), row)
    if gross_r is None or cost_r is None:
        return None
    points, residual = cost_components(row)
    parts = {
        label: (rupees(value, row) or 0.0) for label, value in points.items()
    }
    return {
        "vehicle": row.get("vehicle"),
        "obs_id": row.get("obs_id"),
        "lot_size": lot_of(row),
        "entry_price": float(entry),
        "gross_rupees": gross_r,
        "cost_rupees": round(cost_r, 2),
        "components_rupees": parts,
        "cost_residual_rupees": rupees(residual, row) or 0.0,
        "net_rupees": round(gross_r - cost_r, 2),
        "gross_pct": snap.get("gross_pct"),
        "net_pct": net_pct(row, horizon),
        "mfe_pct": snap.get("mfe_pct"),
        "mae_pct": snap.get("mae_pct"),
        "mfe_rupees": pct_to_rupees(snap.get("mfe_pct"), row),
        "mae_rupees": pct_to_rupees(snap.get("mae_pct"), row),
        "cost_pct": row.get("cost_pct"),
        "hold_min": snap.get("hold_min"),
    }


def money_rows(rows: list[dict], horizon: str) -> tuple[list[dict], int]:
    """Every convertible leg at one horizon, plus a count of the rest."""
    out: list[dict] = []
    skipped = 0
    for r in rows:
        if r.get("entry_price") is None:
            continue
        m = leg_money(r, horizon)
        if m is None:
            skipped += 1
            continue
        out.append(m)
    return out, skipped


def _total(ms: list[dict], key: str) -> float:
    return round(sum(float(m[key]) for m in ms), 2)


def waterfall(ms: list[dict], *, giveback_rupees: float | None = None) -> dict:
    """§10 — TOTAL GROSS OPPORTUNITY down to FINAL NET, in rupees.

    "Total gross opportunity" is the sum of *favourable excursions* (MFE), not
    the sum of realised gross: the question the waterfall answers is how much
    the market offered and where it was lost, and realised gross has already had
    the giveback taken out of it. The giveback step is therefore the gap between
    what was offered and what was held, and the arithmetic closes exactly:

        MFE - each cost component - giveback = net

    The steps are printed in the order §10 specifies, with GIVEBACK last before
    the net. Order does not change the total — these are all subtractions from
    the same offered figure — so the required layout costs nothing in accuracy.
    """
    offered = _total(ms, "mfe_rupees")
    realised = _total(ms, "gross_rupees")
    given = round(offered - realised, 2) if giveback_rupees is None else round(
        float(giveback_rupees), 2
    )
    steps = []
    running = offered
    steps.append({"step": "TOTAL GROSS OPPORTUNITY", "rupees": offered,
                  "running_rupees": running,
                  "note": "sum of measured favourable excursions, one lot each"})
    for label in ("SPREAD", "BROKERAGE", "STATUTORY", "SLIPPAGE"):
        amount = round(sum(
            float(m["components_rupees"].get(label) or 0.0) for m in ms
        ), 2)
        running = round(running - amount, 2)
        steps.append({"step": label, "rupees": -amount,
                      "running_rupees": running})
    residual = round(sum(
        float(m.get("cost_residual_rupees") or 0.0) for m in ms
    ), 2)
    running = round(running - residual, 2)
    running = round(running - given, 2)
    steps.append({"step": "GIVEBACK", "rupees": -given, "running_rupees": running,
                  "note": "offered minus what the horizon actually held"})
    net = _total(ms, "net_rupees")
    steps.append({"step": "FINAL NET", "rupees": net, "running_rupees": net})
    return {
        "n_legs": len(ms),
        "order": ["TOTAL GROSS OPPORTUNITY", "SPREAD", "BROKERAGE",
                  "STATUTORY", "SLIPPAGE", "GIVEBACK", "FINAL NET"],
        "steps": steps,
        "closes": abs(running - net) < 0.05,
        "residual_rupees": round(running - net, 2),
        "cost_lines_unexplained_rupees": residual,
        "note": (
            "one lot per leg; sizing is production logic and is not modelled "
            "here. Legs without a captured lot size are excluded and counted."
        ),
    }
