"""Phase 35 — charge the round trip on legs that were built without a lot size.

A store can hold two million entered legs and resolve none of them, and this is
how: the capture recorded no contract multiplier, a flat per-order fee cannot be
converted into premium points without one, so every leg was refused a cost
(``LOT_SIZE_UNKNOWN``) and a leg with no cost has no net. Nothing about the
prices was wrong. One integer per contract was missing.

That integer is a published property of the contract, so it can be recovered
after the fact — and the arithmetic it unlocks is a *re-computation over rows
that already exist*, not a re-measurement. The gross move at every horizon, the
running MFE and MAE, the entry and exit fills and their evidence labels were all
measured on the original pass and are stored. ``net = gross - cost`` needs none
of the raw quotes again, which is why this runs in minutes where a rebuild runs
in hours.

What it refuses to do is the more important half:

* It never invents a price. A leg with no executable ask at the decision
  instant, no bid at the exit, no captured quote or no forward path keeps its
  reason and stays unresolved — a lot size was never what those were missing.
* It never upgrades evidence. A cost computed on a registry lot size is
  labelled ``MEASURED_EXECUTABLE_LOT_FROM_CONTRACT_SPEC``, below the fully
  measured label, so no reader can quote it as an executable-verified cost.
* It never touches a raw row. The append-only triggers would abort it, and the
  numbers here are all derived.

Attribution is rewritten for the legs whose net changed, because §10's causes
are read off the net. Its inputs come back from the stored horizon rows, and the
one field they cannot carry at full resolution is *which side arrived first*:
the original pass compared the trough and peak timestamps, and the stored rows
only place each inside a horizon bucket. Reconstruction is therefore made at
horizon resolution and the row says so, rather than being asserted as measured
or dropped as unknowable.
"""
from __future__ import annotations

import json

from app.research.phase35 import (
    FUTURES,
    HORIZONS,
    MEASURED_EXECUTABLE,
    NO_LOT_SIZE,
    SESSION_CLOSE,
    UNMEASURED,
    attrib,
    book,
    lots,
    path,
    store,
    vehicle,
)

# Legs per batch. A batch is closed on an obs_id boundary so that every vehicle
# of one observation is graded together — §10's peer comparison reads the other
# vehicles' net figures at the same instant, and a batch that split an
# observation would show a leg fewer peers than it had.
BATCH_LEGS = 4000

RECONSTRUCTED = "GIVEBACK_RECONSTRUCTED_AT_HORIZON_RESOLUTION"

# Horizon column order: the numeric horizons in minutes, then the close.
_ORDER: tuple[str, ...] = tuple(str(h) for h in HORIZONS) + (SESSION_CLOSE,)


def _horizon_rank(horizon: str) -> int:
    try:
        return _ORDER.index(str(horizon))
    except ValueError:
        return len(_ORDER)


def candidate_count(con) -> int:
    """Legs that have an entry fill but no round-trip cost charged against it."""
    row = con.execute(
        "SELECT COUNT(*) AS n FROM paper_leg"
        " WHERE entry_price IS NOT NULL AND cost_points IS NULL"
    ).fetchone()
    return int(row["n"] or 0)


def _leg_batches(con, *, batch: int):
    """Yield lists of legs to recost, cut on observation boundaries."""
    cur = con.execute(
        "SELECT * FROM paper_leg"
        " WHERE entry_price IS NOT NULL AND cost_points IS NULL"
        " ORDER BY obs_id, book, vehicle"
    )
    try:
        pending: list[dict] = []
        for row in cur:
            leg = dict(row)
            if (
                len(pending) >= batch
                and pending[-1]["obs_id"] != leg["obs_id"]
            ):
                yield pending
                pending = []
            pending.append(leg)
        if pending:
            yield pending
    finally:
        cur.close()


def _quote_lots(con, obs_ids: list[str]) -> dict[tuple[str, str], dict]:
    """``(obs_id, vehicle) -> {"lot_size": n}`` from the stored raw quotes."""
    out: dict[tuple[str, str], dict] = {}
    marks = ",".join("?" * len(obs_ids))
    cur = con.execute(
        "SELECT obs_id, vehicle, lot_size FROM raw_quote"  # noqa: S608 - marks only
        f" WHERE obs_id IN ({marks})",
        obs_ids,
    )
    try:
        for row in cur:
            out[(row["obs_id"], row["vehicle"])] = {"lot_size": row["lot_size"]}
    finally:
        cur.close()
    return out


def _observations(con, obs_ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    marks = ",".join("?" * len(obs_ids))
    cur = con.execute(
        "SELECT obs_id, instrument, context_json FROM raw_observation"  # noqa: S608
        f" WHERE obs_id IN ({marks})",
        obs_ids,
    )
    try:
        for row in cur:
            out[row["obs_id"]] = dict(row)
    finally:
        cur.close()
    return out


def _obs_legs(con, obs_ids: list[str]) -> list[dict]:
    """Every leg at these observations, whatever book or vehicle it belongs to.

    §12/§13's peer comparison is "the other vehicles quoted at this same
    instant", so it has to see legs this pass is not recosting too — a leg
    already costed on an earlier pass is still a peer.
    """
    marks = ",".join("?" * len(obs_ids))
    cur = con.execute(
        "SELECT leg_id, obs_id, book, vehicle, net_pct FROM paper_leg"  # noqa: S608
        f" WHERE obs_id IN ({marks})",
        obs_ids,
    )
    try:
        return [dict(r) for r in cur]
    finally:
        cur.close()


def _paths(con, leg_ids: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    marks = ",".join("?" * len(leg_ids))
    cur = con.execute(
        "SELECT * FROM leg_path"  # noqa: S608 - marks only
        f" WHERE leg_id IN ({marks})",
        leg_ids,
    )
    try:
        for row in cur:
            out.setdefault(row["leg_id"], []).append(dict(row))
    finally:
        cur.close()
    for rows in out.values():
        rows.sort(key=lambda r: _horizon_rank(r["horizon"]))
    return out


def _giveback(rows: list[dict]) -> dict:
    """Rebuild §9's giveback block from the stored horizon rows.

    ``mfe_pct``/``mae_pct`` on each row are the running extremes at that
    horizon, so the peak is the last row's MFE and the first row whose extreme
    moved tells us which side arrived first — to within one horizon. Where both
    first appear in the same bucket the order is genuinely not recoverable, and
    ``adverse_first`` stays False rather than being guessed either way.
    """
    if not rows:
        return {}
    last = rows[-1]
    peak = float(last["mfe_pct"] or 0.0)
    final = float(last["gross_pct"] or 0.0)
    mae = float(last["mae_pct"] or 0.0)
    first_adverse = next(
        (i for i, r in enumerate(rows) if (r["mae_pct"] or 0.0) < 0), None,
    )
    first_favourable = next(
        (i for i, r in enumerate(rows) if (r["mfe_pct"] or 0.0) > 0), None,
    )
    adverse_first = (
        first_adverse is not None
        and (first_favourable is None or first_adverse < first_favourable)
    )
    peak_at = next(
        (r["horizon"] for r in rows if float(r["mfe_pct"] or 0.0) >= peak), None,
    )
    minutes = None
    if peak > 0 and peak_at is not None and str(peak_at) != SESSION_CLOSE:
        try:
            minutes = float(peak_at)
        except ValueError:
            minutes = None
    return {
        "peak_pct": round(peak, 4),
        # An upper bound at horizon resolution, not the measured instant: the
        # peak happened at or before the first horizon that reported it.
        "time_to_peak_min": minutes,
        "final_pct": round(final, 4),
        "max_giveback_pct": round(max(0.0, peak - final), 4),
        "pct_of_mfe_given_back": (
            round(100.0 * max(0.0, peak - final) / peak, 2) if peak > 0 else None
        ),
        "returned_to_entry": peak > 0 and final <= 0,
        "mae_pct": round(mae, 4),
        "adverse_first": bool(adverse_first),
        "resolution": RECONSTRUCTED,
    }


def _targets(vehicle: str, *, cost_pct: float | None) -> dict:
    t1, t2, t3 = path.targets_pct(vehicle, cost_pct=cost_pct)
    return {"t1": t1, "t2": t2, "t3": t3}


def _plan_context(obs: dict) -> dict:
    ctx = obs.get("context_json")
    if isinstance(ctx, str) and ctx:
        return {"context_json": ctx}
    return {}


def recost_batch(con, legs: list[dict]) -> dict:
    """Recost one batch of legs, returning the rows to write and a tally."""
    obs_ids = sorted({leg["obs_id"] for leg in legs})
    quotes = _quote_lots(con, obs_ids)
    observations = _observations(con, obs_ids)
    paths = _paths(con, [leg["leg_id"] for leg in legs])

    out_legs: list[dict] = []
    out_paths: list[dict] = []
    entered: list[dict] = []
    tally = {
        "legs_seen": len(legs),
        "costed": 0,
        "still_unmeasured": 0,
        "resolved": 0,
        "path_rows": 0,
        "lot_sources": {},
        "cost_evidence": {},
    }

    for leg in legs:
        obs_row = observations.get(leg["obs_id"]) or {}
        obs = {
            "instrument": leg["instrument"],
            **_plan_context(obs_row),
        }
        quote = quotes.get((leg["obs_id"], leg["vehicle"])) or {}
        lot_size, lot_source = lots.resolve(quote, obs)
        entry = float(leg["entry_price"])
        cost = book.cost_of(
            leg["vehicle"], leg["instrument"], entry=entry, exit_price=None,
            lot_size=lot_size,
            executable=leg["evidence"] == MEASURED_EXECUTABLE,
            lot_source=lot_source,
        )
        cost_points = cost.get("cost_points")
        cost_evidence = cost.get("evidence") or UNMEASURED
        updated = dict(leg)
        updated["lot_size"] = lot_size
        updated["lot_source"] = lot_source
        updated["cost_points"] = cost_points
        updated["cost_evidence"] = cost_evidence
        tally["lot_sources"][str(lot_source)] = (
            tally["lot_sources"].get(str(lot_source), 0) + 1
        )
        tally["cost_evidence"][cost_evidence] = (
            tally["cost_evidence"].get(cost_evidence, 0) + 1
        )
        rows = paths.get(leg["leg_id"]) or []
        cost_pct = (
            None if cost_points is None
            else round(100.0 * float(cost_points) / entry, 4)
        )
        targets = _targets(leg["vehicle"], cost_pct=cost_pct)

        if cost_points is None:
            # Still uncostable: no source names this contract's multiplier. The
            # leg keeps its own reason when it already had one — a missing exit
            # bid is a different fact about the feed than a missing lot size —
            # and its horizon rows are left exactly as they were measured.
            tally["still_unmeasured"] += 1
            updated["reason"] = leg.get("reason") or NO_LOT_SIZE
        else:
            tally["costed"] += 1
            for row in rows:
                gross = row.get("gross_pct")
                new = dict(row)
                new["net_pct"] = (
                    None if gross is None
                    else book.net_pct(float(gross), cost_points, entry)
                )
                if leg["vehicle"] == FUTURES:
                    # The futures ladder is defined in multiples of the round
                    # trip, so charging a cost is what makes T1-T3 exist at all.
                    # The flags are read off the stored running MFE, the same
                    # comparison the original pass made.
                    mfe = float(row.get("mfe_pct") or 0.0)
                    for key in ("t1", "t2", "t3"):
                        level = targets[key]
                        new[key] = bool(level > 0 and mfe >= level)
                out_paths.append(new)
            tally["path_rows"] += len(rows)

            close = next(
                (r for r in rows if str(r["horizon"]) == SESSION_CLOSE), None,
            )
            if close is not None and close.get("gross_pct") is not None:
                updated["net_pct"] = book.net_pct(
                    float(close["gross_pct"]), cost_points, entry,
                )
                updated["resolved"] = updated["net_pct"] is not None
                if updated["resolved"]:
                    tally["resolved"] += 1

        out_legs.append(updated)
        # Every leg with an entry fill is attributed, costed or not, which is
        # what the rebuild does: §10 asks why this leg lost, and "its cost could
        # not be charged" is one of the answers it is allowed to give.
        # A leg with no stored horizon rows is attributed exactly as the rebuild
        # attributes it: with no cost breakdown, no giveback and no ladder. Its
        # cost is charged on the leg either way, but §10 read nothing else off a
        # leg it could not follow forward, and a store recosted here has to
        # aggregate identically to one that was rebuilt.
        entered.append({
            **updated,
            "cost": cost if rows else {},
            "giveback": _giveback(rows),
            "targets": targets if rows else {},
        })

    _attach_peers(_obs_legs(con, obs_ids), entered)
    return {
        "legs": out_legs,
        "paths": out_paths,
        "attributions": attrib.rows(entered) if entered else [],
        "tally": tally,
    }


def _attach_peers(all_legs: list[dict], entered: list[dict]) -> None:
    """Give each leg the net figures of the other vehicles at the same instant.

    Only the same book is compared: a FULL_MARKET leg is not a peer of an engine
    leg, and mixing them would let "the engine picked the wrong vehicle" be
    concluded from a leg the engine never had.
    """
    fresh = {str(leg["leg_id"]): leg.get("net_pct") for leg in entered}
    by_obs: dict[tuple[str, str], dict[str, float | None]] = {}
    for leg in all_legs:
        key = (str(leg["book"]), str(leg["obs_id"]))
        lid = str(leg["leg_id"])
        by_obs.setdefault(key, {})[str(leg["vehicle"])] = (
            fresh[lid] if lid in fresh else leg.get("net_pct")
        )
    for leg in entered:
        key = (str(leg["book"]), str(leg["obs_id"]))
        peers = dict(by_obs.get(key) or {})
        peers.pop(str(leg["vehicle"]), None)
        leg["peers"] = peers


def recompare(con, obs_ids: list[str]) -> int:
    """Rebuild §12/§13's vehicle comparison at these observations.

    A comparison needs a net figure on each side, so an uncosted store produces
    none of them however complete its quotes were. Once the legs at an
    observation carry a net the comparison becomes computable, and it is rebuilt
    here rather than by another full pass over the store. Writes replace by
    ``(obs_id, kind, horizon)``, so re-running cannot double-count.
    """
    written = 0
    pending: list[dict] = []
    for obs_id in obs_ids:
        vehicles = {
            leg["vehicle"]: leg
            for leg in (
                dict(r) for r in con.execute(
                    "SELECT * FROM paper_leg WHERE obs_id = ? ORDER BY entry_ts",
                    (obs_id,),
                )
            )
        }
        rows = con.execute(
            "SELECT p.* FROM leg_path p JOIN paper_leg l"
            " ON l.leg_id = p.leg_id WHERE l.obs_id = ?",
            (obs_id,),
        )
        paths = {f"{r['leg_id']}|{r['horizon']}": dict(r) for r in rows}
        pending.extend(vehicle.compare(obs_id, vehicles, paths))
        if len(pending) >= vehicle.FLUSH_EVERY:
            written += store.save_comparisons(con, pending)
            pending.clear()
    if pending:
        written += store.save_comparisons(con, pending)
    return written


def run(con, *, batch: int = BATCH_LEGS, progress=None) -> dict:
    """Recost every leg that has an entry fill and no cost. Idempotent.

    Idempotent by construction rather than by bookkeeping: the candidate set is
    "entry filled, cost not charged", so a leg this pass costs leaves the set
    and a second pass has nothing to do with it. Re-running after a *new*
    session is built therefore costs only that session's legs.
    """
    total = candidate_count(con)
    tally = {
        "candidates": total,
        "legs_seen": 0,
        "costed": 0,
        "still_unmeasured": 0,
        "resolved": 0,
        "legs_written": 0,
        "path_rows_written": 0,
        "attribution_rows_written": 0,
        "comparison_rows_written": 0,
        "lot_sources": {},
        "cost_evidence": {},
    }
    for legs in _leg_batches(con, batch=batch):
        result = recost_batch(con, legs)
        tally["legs_written"] += store.save_legs(con, result["legs"])
        tally["path_rows_written"] += store.save_leg_paths(con, result["paths"])
        tally["attribution_rows_written"] += store.save_attributions(
            con, result["attributions"],
        )
        # After the legs are written, so the comparison reads the nets this
        # batch just charged rather than the nulls they replaced.
        tally["comparison_rows_written"] += recompare(
            con, sorted({str(leg["obs_id"]) for leg in legs}),
        )
        con.commit()
        batch_tally = result["tally"]
        for key in ("legs_seen", "costed", "still_unmeasured", "resolved"):
            tally[key] += int(batch_tally[key])
        for key in ("lot_sources", "cost_evidence"):
            for name, n in batch_tally[key].items():
                tally[key][name] = tally[key].get(name, 0) + int(n)
        if progress is not None:
            progress(dict(tally))
    return tally


def summary(con) -> dict:
    """What the store now holds, counted in SQLite rather than in Python."""
    rows = con.execute(
        "SELECT book, COUNT(*) AS legs,"
        " SUM(CASE WHEN resolved = 1 THEN 1 ELSE 0 END) AS resolved,"
        " SUM(CASE WHEN net_pct IS NOT NULL THEN 1 ELSE 0 END) AS netted,"
        " SUM(CASE WHEN lot_size IS NOT NULL THEN 1 ELSE 0 END) AS lotted"
        " FROM paper_leg GROUP BY book"
    ).fetchall()
    lot_sources = con.execute(
        "SELECT lot_source, COUNT(*) AS n FROM paper_leg GROUP BY lot_source"
    ).fetchall()
    evidence = con.execute(
        "SELECT cost_evidence, COUNT(*) AS n FROM paper_leg GROUP BY cost_evidence"
    ).fetchall()
    return {
        "books": {
            r["book"]: {
                "legs": int(r["legs"]),
                "resolved": int(r["resolved"] or 0),
                "net_pct_present": int(r["netted"] or 0),
                "lot_size_present": int(r["lotted"] or 0),
            }
            for r in rows
        },
        "lot_sources": {str(r["lot_source"]): int(r["n"]) for r in lot_sources},
        "cost_evidence": {str(r["cost_evidence"]): int(r["n"]) for r in evidence},
        "attributions": int(
            con.execute("SELECT COUNT(*) AS n FROM leg_attribution").fetchone()["n"]
        ),
    }


def as_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)
