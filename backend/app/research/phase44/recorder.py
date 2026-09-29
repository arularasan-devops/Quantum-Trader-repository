"""Phase 44 §4 — journalling a session from the raw store, after the close.

Nothing here runs while the market is open. The Phase 17 capture already writes
both sides of the book at the decision instant and Phase 35's raw store is
never overwritten, so replaying raw after the close sees precisely what a
tick-time hook would have seen — with no code on the live path and therefore no
way to disturb the Track A capture that everything else is waiting on.

Two entry points, and only one of them writes:

* :func:`preview` evaluates a session and returns the rows. It is allowed while
  dormant, because reading evidence is not recording it, and it is how an
  operator can see what the arm *would* journal before deciding to arm it;
* :func:`record` calls :func:`preview` and then writes, and refuses unless
  :mod:`switch` says the recorder is armed under the current definition.

The rows are decision-time only. No outcome is resolved, no leg is opened, no
return is computed — this phase answers "how often does the arm fire live, and
how far apart are its two costs", and nothing about whether it makes money.
"""
from __future__ import annotations

import json
import sqlite3
import time

from app.research import phase44
from app.research.phase44 import freeze, rule, store, switch

# A cost was measured from a quoted book under either label. They are counted
# separately as well, because the weaker one leans on the contract spec for the
# lot and a later phase may want only the stronger.
MEASURED_LABELS = (phase44.MEASURED, phase44.SPEC_LOT)


def raw_sessions(raw: sqlite3.Connection, instrument: str) -> list[str]:
    """Sessions the raw store holds for the instrument, oldest first."""
    return [str(r["session"]) for r in raw.execute(
        "SELECT DISTINCT session FROM raw_observation"
        " WHERE instrument = ? AND session IS NOT NULL ORDER BY session",
        (instrument,),
    )]


def _price_samples(
    raw: sqlite3.Connection, session: str, instrument: str,
) -> list[tuple[float, float]]:
    """Every timestamped futures price the session captured, for the bars.

    The mid is preferred and the traded price is the fallback: a mid is what a
    two-sided book says the instrument is worth, while an LTP is where somebody
    else traded, possibly on the other side and possibly minutes ago. Both are
    fine for a *trailing range*; neither is ever used as an executable price.
    """
    rows = raw.execute(
        "SELECT q.ts AS ts, q.bid AS bid, q.ask AS ask, q.traded AS traded"
        " FROM raw_quote q JOIN raw_observation o ON o.obs_id = q.obs_id"
        " WHERE o.session = ? AND o.instrument = ? AND q.vehicle = ?"
        " ORDER BY q.ts",
        (session, instrument, phase44.VEHICLE),
    )
    out: list[tuple[float, float]] = []
    for r in rows:
        bid, ask, traded = r["bid"], r["ask"], r["traded"]
        if bid and ask and bid > 0 and ask > 0:
            out.append((float(r["ts"]), (float(bid) + float(ask)) / 2.0))
        elif traded and traded > 0:
            out.append((float(r["ts"]), float(traded)))
    return out


def _decisions(
    raw: sqlite3.Connection, session: str, instrument: str,
) -> list[sqlite3.Row]:
    """The session's decision instants with their futures quote, if any.

    A LEFT JOIN, deliberately: a decision the capture recorded with no book is
    still a decision the arm was asked about, and dropping it here would make
    the live firing rate look better than it is by hiding the instants that
    could not be measured.
    """
    return list(raw.execute(
        "SELECT o.obs_id AS obs_id, o.ts AS ts, o.source AS source,"
        " o.direction AS direction, o.engine_selected AS engine_selected,"
        " q.bid AS bid, q.ask AS ask, q.symbol AS symbol, q.expiry AS expiry,"
        " q.lot_size AS lot_size"
        " FROM raw_observation o"
        " LEFT JOIN raw_quote q ON q.obs_id = o.obs_id AND q.vehicle = ?"
        " WHERE o.session = ? AND o.instrument = ?"
        " ORDER BY o.ts",
        (phase44.VEHICLE, session, instrument),
    ))


def preview(
    raw: sqlite3.Connection, session: str, *, instrument: str | None = None,
) -> dict:
    """Evaluate the frozen arm across one session. Writes nothing, ever.

    Returns the rows plus a per-session tally, so a caller can see the firing
    rate and the two costs without the journal existing at all.
    """
    inst = instrument or phase44.INSTRUMENT
    definition = freeze.fingerprint()["definition"]
    ctx = rule.Context(inst, rule.minute_bars(_price_samples(raw, session, inst)))
    now = time.time()
    rows: list[dict] = []
    for d in _decisions(raw, session, inst):
        verdict = rule.evaluate(
            ctx, ts=float(d["ts"]), direction=d["direction"], bid=d["bid"],
            ask=d["ask"], lot_size=d["lot_size"], instrument=inst,
            symbol=d["symbol"],
        )
        rows.append({
            "obs_id": str(d["obs_id"]), "session": session,
            "ts": float(d["ts"]), "instrument": inst,
            "vehicle": phase44.VEHICLE, "source": d["source"],
            "direction": d["direction"], "contract": d["symbol"],
            "expiry": d["expiry"], "lot_size": d["lot_size"],
            "bid": d["bid"], "ask": d["ask"],
            "base_admits": _flag(verdict["base_admits"]),
            "expected_move_points": verdict["expected_move_points"],
            "decision_close": verdict["decision_close"],
            "modelled_cost_points": verdict["modelled_cost_points"],
            "measured_cost_points": verdict["measured_cost_points"],
            "measured_charges_points": verdict["measured_charges_points"],
            "measured_spread_points": verdict["measured_spread_points"],
            "ratio_modelled": verdict["ratio_modelled"],
            "ratio_measured": verdict["ratio_measured"],
            "admits_modelled": _flag(verdict["admits_modelled"]),
            "admits": _flag(verdict["admits"]),
            "evidence": verdict["evidence"], "reason": verdict["reason"],
            "definition": definition, "recorded_ts": now,
        })
    return {
        "session": session, "instrument": inst, "definition": definition,
        "minutes": ctx.n, "rows": rows, "tally": summarise(rows),
    }


def _flag(value: bool | None) -> int | None:
    """``None`` stays ``None``: unmeasurable is not the same as false."""
    return None if value is None else int(bool(value))


# How far short of the frozen 8.0 threshold the ratio actually gets. Reported,
# never applied: these are descriptive statistics over numbers the frozen rule
# already computed per row, so nothing here can change an admission. They exist
# because "the arm never fired" is unreadable without knowing whether it missed
# by a little or by an order of magnitude.
RATIO_RUNGS: tuple[float, ...] = (2.0, 4.0, 6.0, 8.0)


def _spread(values: list[float]) -> dict:
    """Median/p90/p99/max of a ratio, plus how many instants clear each rung."""
    if not values:
        return {"n": 0, "median": None, "p90": None, "p99": None, "max": None,
                "at_or_above": {}}
    ordered = sorted(values)

    def pick(q: float) -> float:
        idx = min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))
        return round(ordered[idx], 4)

    return {
        "n": len(ordered),
        "median": pick(0.5), "p90": pick(0.9), "p99": pick(0.99),
        "max": round(ordered[-1], 4),
        "at_or_above": {f"{r:g}x": sum(1 for v in ordered if v >= r)
                        for r in RATIO_RUNGS},
    }


def summarise(rows: list[dict]) -> dict:
    """Counts for one batch of rows, including why the unmeasurable ones were.

    The cost gap is reported as a median-free mean over the instants where
    *both* costs exist; comparing a mean modelled cost over all rows with a
    mean measured cost over the measurable subset would compare two different
    populations and flatter whichever had the quieter minutes.
    """
    reasons: dict[str, int] = {}
    both = [r for r in rows if r["modelled_cost_points"]
            and r["measured_cost_points"]]
    for r in rows:
        if r["evidence"] not in MEASURED_LABELS:
            reasons[str(r["reason"])] = reasons.get(str(r["reason"]), 0) + 1
    gap = None
    if both:
        gap = round(sum(r["measured_cost_points"] / r["modelled_cost_points"]
                        for r in both) / len(both), 4)
    return {
        "decisions": len(rows),
        "measurable": sum(1 for r in rows if r["evidence"] in MEASURED_LABELS),
        "measured_captured_lot": sum(
            1 for r in rows if r["evidence"] == phase44.MEASURED),
        "measured_spec_lot": sum(
            1 for r in rows if r["evidence"] == phase44.SPEC_LOT),
        "base_admits": sum(1 for r in rows if r["base_admits"] == 1),
        "admits": sum(1 for r in rows if r["admits"] == 1),
        "admits_modelled": sum(1 for r in rows if r["admits_modelled"] == 1),
        "comparable_costs": len(both),
        "measured_over_modelled_cost": gap,
        # Restricted to instants the base conditions admitted, because that is
        # the population the threshold is ever asked about; pooling the rest
        # would report the distribution of a rule nobody evaluates.
        "ratio_measured_spread": _spread(
            [float(r["ratio_measured"]) for r in rows
             if r["base_admits"] == 1 and r["ratio_measured"] is not None]),
        "ratio_modelled_spread": _spread(
            [float(r["ratio_modelled"]) for r in rows
             if r["base_admits"] == 1 and r["ratio_modelled"] is not None]),
        "unmeasurable_reasons": dict(sorted(reasons.items())),
    }


def record(
    con: sqlite3.Connection, raw: sqlite3.Connection, session: str, *,
    instrument: str | None = None,
) -> dict:
    """Journal one session, or refuse and say why.

    The refusal is checked before the evaluation is written and the write is
    the only thing it guards; the arithmetic is identical either way, so an
    operator who is refused sees the same numbers they would have recorded.
    """
    permission = switch.state(con)
    result = preview(raw, session, instrument=instrument)
    if not permission["may_record"]:
        return {**result, "written": 0, "recorded": False,
                "state": permission["state"],
                "refusal": permission["refusal"]}
    written = store.insert(con, result["rows"])
    return {**result, "written": written, "recorded": True,
            "state": permission["state"], "refusal": None}


def record_all(
    con: sqlite3.Connection, raw: sqlite3.Connection, *,
    instrument: str | None = None, sessions: list[str] | None = None,
) -> dict:
    """Every session the raw store holds, oldest first."""
    inst = instrument or phase44.INSTRUMENT
    targets = sessions if sessions is not None else raw_sessions(raw, inst)
    done = [record(con, raw, s, instrument=inst) for s in targets]
    return {
        "instrument": inst,
        "sessions": [{k: d[k] for k in
                      ("session", "written", "recorded", "state", "refusal",
                       "tally")} for d in done],
        "written": sum(d["written"] for d in done),
        "state": done[0]["state"] if done else switch.state(con)["state"],
    }


def as_json(payload: dict) -> str:
    """Artefact text; rows are dropped because a session is tens of thousands."""
    trimmed = {k: v for k, v in payload.items() if k != "rows"}
    return json.dumps(trimmed, indent=2, sort_keys=True, default=str)
