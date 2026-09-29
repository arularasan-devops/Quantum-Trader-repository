"""Phase 36 §14/§15 — engine vs full market, and entry-timing counterfactuals.

§14 exists because "the trade lost" is not a diagnosis. If the engine bought a
call, the call lost ₹400 and the future made ₹2,800 on the same instant, then the
engine read the market correctly and expressed it badly, and the fix is vehicle
selection, not the signal. Calling that WRONG_DIRECTION would send the next month
of work to the wrong place.

So direction is judged by the *future*, not by the engine's own vehicle. The
future is the cleanest available expression of the view: it has no strike, no
theta, no convexity and a spread of one to three points. If the directional
future was profitable net of cost, the view was right, whatever the option did.

§15 is a counterfactual on entry timing and nothing else — no production entry
rule changes, and "after confirmation" is defined here as a measurement rather
than imported from a rule the system does not have.
"""
from __future__ import annotations

from app.research.phase35 import CE, FUTURES, PE
from app.research.phase36 import (
    DIRECTIONAL,
    ENGINE_MISSED,
    ENGINE_SELECTED,
    INSUFFICIENT,
    NO_VEHICLE_PROFITABLE,
    VEHICLE_OK,
    WRONG_DIRECTION,
    WRONG_VEHICLE,
)
from app.research.phase36 import outcome as p36outcome


def _net(row: dict, horizon: str, *, cost_multiple: float = 1.0) -> float | None:
    snap = (row.get("horizons") or {}).get(horizon)
    entry = row.get("entry_price")
    if not isinstance(snap, dict) or not isinstance(entry, (int, float)):
        return None
    return p36outcome.net_from_gross(
        snap.get("gross_pct"),
        cost_points=(row.get("cost") or {}).get("cost_points"),
        entry=float(entry), cost_multiple=cost_multiple,
    )


def classify(
    rows_for_obs: list[dict], *, engine_vehicle: str | None, horizon: str,
) -> dict:
    """One triple's §14 verdict.

    ``rows_for_obs`` are the resolved vehicle rows for a single decision instant.
    The classification only uses net figures at one horizon, and the horizon is
    passed in rather than chosen here, so the caller cannot get a different
    verdict by letting each observation pick its own hold.
    """
    directional = {
        r["vehicle"]: r for r in rows_for_obs if r.get("role") == DIRECTIONAL
    }
    nets = {v: _net(r, horizon) for v, r in directional.items()}
    fut = nets.get(FUTURES)
    measured = {v: n for v, n in nets.items() if n is not None}
    if fut is None or not measured:
        return {
            "label": INSUFFICIENT,
            "direction_correct": None,
            "nets": nets,
            "best_vehicle": None,
            "engine_vehicle": engine_vehicle,
        }

    best_vehicle = max(measured, key=lambda v: measured[v])
    best_net = measured[best_vehicle]
    direction_correct = fut > 0

    if not direction_correct and best_net <= 0:
        label = WRONG_DIRECTION
    elif best_net <= 0:
        # The future lost too, or every measured expression did: there was no
        # vehicle that made money, which is a statement about the opportunity
        # rather than about the choice between vehicles.
        label = NO_VEHICLE_PROFITABLE
    elif engine_vehicle is None:
        label = VEHICLE_OK if best_net > 0 else NO_VEHICLE_PROFITABLE
    else:
        engine_net = nets.get(engine_vehicle)
        if engine_net is None:
            label = INSUFFICIENT
        elif engine_net > 0 and engine_vehicle == best_vehicle:
            label = VEHICLE_OK
        elif engine_net <= 0 < best_net:
            label = WRONG_VEHICLE
        else:
            label = VEHICLE_OK
    return {
        "label": label,
        "direction_correct": direction_correct,
        "nets": nets,
        "best_vehicle": best_vehicle,
        "best_net_pct": best_net,
        "engine_vehicle": engine_vehicle,
        "engine_net_pct": nets.get(engine_vehicle) if engine_vehicle else None,
    }


def attribute(rows: list[dict], triples: list[dict], *, horizon: str) -> dict:
    """§14 — every triple split ENGINE_SELECTED / ENGINE_MISSED, then classified.

    The ENGINE_MISSED half is the part no earlier phase could compute: it is the
    counterfactual value of the opportunities the engine declined, which is the
    only way "the engine is too selective" can be distinguished from "the engine
    is right to refuse".
    """
    by_obs: dict[str, list[dict]] = {}
    for r in rows:
        by_obs.setdefault(r["obs_id"], []).append(r)

    counts: dict[str, dict[str, int]] = {
        ENGINE_SELECTED: {}, ENGINE_MISSED: {},
    }
    missed_value: list[float] = []
    selected_value: list[float] = []
    wrong_vehicle_rows: list[dict] = []
    for t in triples:
        group = by_obs.get(t["obs_id"]) or []
        if not group:
            continue
        side = ENGINE_SELECTED if t.get("engine_selected") else ENGINE_MISSED
        out = classify(
            group, engine_vehicle=t.get("engine_vehicle"), horizon=horizon,
        )
        counts[side][out["label"]] = counts[side].get(out["label"], 0) + 1
        best = out.get("best_net_pct")
        if isinstance(best, (int, float)):
            (selected_value if side == ENGINE_SELECTED else missed_value).append(
                float(best)
            )
        if out["label"] == WRONG_VEHICLE:
            wrong_vehicle_rows.append({
                "obs_id": t["obs_id"],
                "ts": t["ts"],
                "direction": t["direction"],
                "engine_vehicle": out["engine_vehicle"],
                "engine_net_pct": out["engine_net_pct"],
                "best_vehicle": out["best_vehicle"],
                "best_net_pct": out["best_net_pct"],
            })

    def summarise(xs: list[float]) -> dict:
        return {
            "n": len(xs),
            "mean_best_net_pct": (
                round(sum(xs) / len(xs), 4) if xs else None
            ),
            "positive_pct": (
                round(100.0 * sum(1 for x in xs if x > 0) / len(xs), 2)
                if xs else None
            ),
        }

    return {
        "horizon": horizon,
        "by_side": counts,
        "engine_selected_best_available": summarise(selected_value),
        "engine_missed_best_available": summarise(missed_value),
        "wrong_vehicle_examples": wrong_vehicle_rows[:20],
        "wrong_vehicle_n": len(wrong_vehicle_rows),
        "note": (
            "direction is judged by the directional FUTURES leg, so an option "
            "that lost while the future paid is WRONG_VEHICLE, not "
            "WRONG_DIRECTION"
        ),
    }


def entry_timing(
    by_offset: dict[str, list[dict]], *, horizon: str,
) -> dict:
    """§15 — does the best vehicle depend on when the entry was taken?

    Reports each offset's own vehicle table so the comparison is between like
    samples: a delayed entry that could not be filled at all is a smaller sample,
    and averaging it against the immediate entry without saying so would make
    waiting look free.
    """
    out: dict[str, dict] = {}
    for label, rows in by_offset.items():
        directional = [r for r in rows if r.get("role") == DIRECTIONAL]
        per_vehicle: dict[str, dict] = {}
        for v in (FUTURES, CE, PE):
            nets = [
                n for n in (
                    _net(r, horizon)
                    for r in directional if r["vehicle"] == v
                ) if n is not None
            ]
            per_vehicle[v] = {
                "n": len(nets),
                "net_mean_pct": (
                    round(sum(nets) / len(nets), 4) if nets else None
                ),
                "win_pct": (
                    round(100.0 * sum(1 for x in nets if x > 0) / len(nets), 2)
                    if nets else None
                ),
            }
        eligible = {
            v: s["net_mean_pct"] for v, s in per_vehicle.items()
            if s["net_mean_pct"] is not None
        }
        out[str(label)] = {
            "by_vehicle": per_vehicle,
            "best_vehicle": (
                max(eligible, key=lambda v: eligible[v]) if eligible else None
            ),
        }
    return {
        "horizon": horizon,
        "by_offset": out,
        "note": (
            "counterfactual only; no production entry rule is changed and "
            "AFTER_CONFIRMATION is a measurement, not a rule the engine has"
        ),
    }
