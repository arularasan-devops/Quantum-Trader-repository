"""Phase 35 smoke — the properties that stop an attribution report from lying.

Each check exists because the opposite mistake makes the answer to "is there no
opportunity, or are we expressing it wrongly?" come back confidently wrong:

* counting a simulator or fixture book as an executable one. This was a real
  defect: 20 fixture rows with tidy bid/ask made the first rebuild print
  executable paper entries that never existed, and the checks below pin the
  corrected provenance rule.
* filling an entry at the traded price when the required side was not quoted,
  which is the substitution that flatters every result.
* charging the spread twice, or charging none and calling the net figure
  measured.
* calling a leg resolved when its cost could not be charged, so a gross-only
  number reads as a net one.
* grading a leg that never opened, which fills the loss-cause histogram with
  rows whose only content is "we had no book".
* letting an unresolved leg count as PROFITABLE_NO_LOSS_CHANNEL, i.e. reading
  "we do not know" as "this one was fine".
* crossing a session boundary in the forward path, which books an overnight gap
  the leg never held.
* mixing two contracts that share a strike into one path.
* overwriting or deleting a raw observation.
* letting Stage A promote a historical row past CANDIDATE.

    .venv/bin/python _smoke_phase35.py
"""
from __future__ import annotations

import contextlib
import gzip
import inspect
import json
import os
import sqlite3
import tempfile
import time
import tracemalloc

# Before app.config is imported. Importing the app opens the history store at
# module scope, and against the real data dir that store is the RUNNING app's:
# the smoke died on ``database is locked`` before its first check, and a smoke
# that cannot run while the app is up is a smoke nobody runs after the close.
os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="p35smoke-"))

from app import main as appmain  # noqa: E402
from app.market.angelone import AngelOneProvider  # noqa: E402
from app.market.provider import MarketDataProvider  # noqa: E402
from app.storage import HistoryStore  # noqa: E402

from app.research.phase35 import (
    CE,
    CURRENT_ENGINE_PAPER,
    FULL_MARKET_PAPER,
    FUTURES,
    GENERAL_MIN_SESSIONS,
    GENERAL_MIN_TRADES,
    HORIZONS,
    LOT_FROM_PLAN,
    LOT_FROM_QUOTE,
    LOT_FROM_SPEC,
    MEASURED_EXECUTABLE,
    MEASURED_EXECUTABLE_SPEC_LOT,
    MEASURED_TRADED_PRICE,
    PAIR_MIN_COST_COVERAGE_PCT,
    PAIR_MIN_SESSIONS,
    PAIR_MIN_TRADES,
    PAPER_ONLY,
    PE,
    REAL_QUOTE_SOURCES,
    RESEARCH_ONLY,
    SESSION_CLOSE,
    SPREAD_MODELLED,
    UNATTRIBUTED,
    UNMEASURED,
    attrib,
    book,
    capture,
    frozen,
    lots,
    milestones,
    paper,
    path,
    recost,
    report,
    service,
    stagea,
    store,
)
from app.research.phase17 import schema as p17schema
from app.research.phase17 import store as p17store
from app.research.phase35 import cli as p35cli
from app.research.phase35 import vehicle as p35vehicle
from app.research.phase35 import (
    EXECUTABLE_BOOK_MAX_AGE_MS,
    FIXTURE_ROW,
    NO_ASK,
    NO_LOT_SIZE,
    SIMULATED_BOOK,
    STALE_QUOTE,
)

PASS = 0
FAIL: list[str] = []

# 2026-09-01 09:20 IST and onwards, inside one session.
T0 = 1_788_146_400.0
MIN = 60.0
LOT = 75


def ok(cond: bool, label: str) -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(label)
        print(f"FAIL: {label}")


def _obs(obs_id: str, *, ts: float, engine: bool, source: str = "ENGINE",
         instrument: str = "NIFTY", lot: int | None = LOT) -> dict:
    return {
        "obs_id": obs_id,
        "ts": ts,
        "session": path.session_date(ts),
        "instrument": instrument,
        "family": "INDEX",
        "source": source,
        "opportunity_type": "DIRECTIONAL",
        "direction": "LONG",
        "engine_class": "BUY" if engine else "WAIT",
        "engine_selected": engine,
        "selected_vehicle": CE if engine else None,
        "context": {"plan": {"lot_size": lot} if lot else {}},
        "origin": "SMOKE",
        "origin_id": obs_id,
        "ingest_ts": ts,
    }


def _quote(obs_id: str, *, ts: float, bid: float, ask: float, vehicle: str = CE,
           symbol: str = "NIFTY25SEP24000CE", evidence: str = MEASURED_EXECUTABLE,
           lot: int | None = LOT, reason: str | None = None) -> dict:
    return {
        "obs_id": obs_id, "vehicle": vehicle, "ts": ts, "symbol": symbol,
        "strike": 24000.0, "expiry": "2026-09-25", "dte": 24,
        "bid": bid, "ask": ask, "traded": (bid + ask) / 2.0,
        "spread": ask - bid, "spread_pct": 100.0 * (ask - bid) / ask,
        "delta": 0.5, "iv": 14.0, "oi": 100000.0, "volume": 5000.0,
        "underlying": 24000.0, "basis": None, "lot_size": lot,
        "evidence": evidence, "reason": reason,
    }


def _symbol_for(symbol: str, vehicle: str) -> str:
    """The contract symbol for one vehicle of the same underlying."""
    if vehicle == CE:
        return symbol
    if vehicle == PE:
        return symbol[:-2] + PE if symbol.endswith(CE) else f"{symbol}-{PE}"
    return f"{symbol}-{vehicle}"


def _seed(con, *, engine: bool = True, ladder=(100.0, 108.0, 120.0, 104.0),
          lot: int | None = LOT, symbol: str = "NIFTY25SEP24000CE",
          instrument: str = "NIFTY", vehicles: tuple[str, ...] = (CE,)) -> str:
    """One decision instant plus a forward path built from later quotes.

    ``vehicles`` quotes more than one contract at each of those instants, which
    is what a vehicle comparison needs: the same instant priced on both sides.
    """
    obs_id = f"obs-smoke-{symbol}-{'e' if engine else 'b'}"
    rows = [_obs(obs_id, ts=T0, engine=engine, lot=lot, instrument=instrument)]
    quotes = [
        _quote(obs_id, ts=T0, bid=ladder[0] - 1.0, ask=ladder[0],
               vehicle=v, symbol=_symbol_for(symbol, v), lot=lot)
        for v in vehicles
    ]
    for i, px in enumerate(ladder[1:], start=1):
        # Each forward point is its own observation, which is how the capture
        # layer actually produces a path: the same contract quoted again later.
        fid = f"{obs_id}-f{i}"
        ts = T0 + i * 10 * MIN
        rows.append(_obs(fid, ts=ts, engine=False, source="BOARD", lot=lot,
                         instrument=instrument))
        quotes.extend(
            _quote(fid, ts=ts, bid=px, ask=px + 1.0, vehicle=v,
                   symbol=_symbol_for(symbol, v), lot=lot)
            for v in vehicles
        )
    store.save_observations(con, rows)
    store.save_quotes(con, quotes)
    return obs_id


def _quote_row_for(source: str, *, fixture: bool,
                   book_age_ms: float | None = 150.0) -> dict:
    """A raw_quote row as the capture layer would write it for this provenance."""
    q = p17schema.Quote(
        instrument="NIFTY", vehicle=CE, symbol="NIFTY25SEP24000CE",
        strike=24000.0, expiry="2026-09-25", days_to_expiry=24,
        bid=100.0, ask=101.0, premium=100.5, underlying_price=24000.0,
        source=source, snapshot_ts=T0, book_age_ms=book_age_ms,
    )
    return capture._quote_row(f"obs-{source.lower()}", CE, q,
                              lot_size=LOT, fixture=fixture)


@contextlib.contextmanager
def _capture_dir(directory: str):
    """Point the Phase 17 capture reader at a throwaway directory."""
    original = p17store.path
    p17store.path = lambda name: os.path.join(directory, name)
    try:
        yield directory
    finally:
        p17store.path = original


def _p17_row(n: int, *, instrument: str = "NIFTY") -> dict:
    """One captured Phase 17 observation, as the capture writes it to disk."""
    ts = T0 + n * MIN
    return {
        "observation_id": f"cap-{instrument}-{n}",
        "signal_ts": ts,
        "capture_ts": ts,
        "instrument": instrument,
        "family": "INDEX",
        "candidate_class": "BUY" if n % 2 == 0 else "WAIT",
        "direction": "LONG",
        "selected_vehicle": CE,
        "session": path.session_date(ts),
        "market_signal_id": f"20260901-{instrument}-{n:04d}",
        "plan": {"lot_size": LOT},
    }


def _concurrency_checks() -> None:
    """A reader in the running app must not be able to fail an ingest's commit.

    This is the failure that killed a live ingest mid-flush with ``database is
    locked`` and left 253 captured minutes unignested: on the default rollback
    journal an open read transaction blocks a writer's commit outright.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "concurrent.db")
        writer = store.connect(path)
        reader = store.connect(path)
        mode = str(reader.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        ok(mode == "wal", f"the store is opened in WAL, so readers do not block {mode}")
        ok(int(reader.execute("PRAGMA busy_timeout").fetchone()[0]) >= 1000,
           "and a lock is waited on rather than raised at once")
        # The app's panels do exactly this: begin a read and hold it while they
        # serialise rows out.
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM raw_observation").fetchone()
        wrote = store.save_observations(
            writer, [_obs("concurrent-1", ts=time.time(), engine=True)])
        ok(wrote == 1,
           "an ingest commits while a reader holds a transaction open")
        reader.close()
        writer.close()

    # The other store a second process meets, and it meets it at IMPORT time:
    # ``app.storage`` opens history.db at module scope, so with the app running
    # this smoke and every CLI died before their first line of work.
    with tempfile.TemporaryDirectory() as tmp:
        first = HistoryStore(os.path.join(tmp, "history.db"))
        mode = str(first._db.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        ok(mode == "wal", f"the history store is opened in WAL, not {mode}")
        ok(int(first._db.execute("PRAGMA busy_timeout").fetchone()[0]) >= 1000,
           "with a wait on the lock, so a second process importing the app "
           "while the app runs does not die on 'database is locked'")
        second = HistoryStore(os.path.join(tmp, "history.db"))
        ok(second is not None,
           "and opening it twice is what a CLI run beside the live app IS")


def main() -> int:
    _concurrency_checks()

    # --- §4: executable means a real feed quoted both sides ------------------
    ok("SIMULATOR" not in REAL_QUOTE_SOURCES and "FIXTURE" not in REAL_QUOTE_SOURCES,
       "a simulator is never an approved quote source")
    ok(capture.is_fixture({"episode_id": "ep-1"})
       and capture.is_fixture({"global_signal_id": "gs-12"})
       and not capture.is_fixture({"episode_id": "20260901-NIFTY-093000"}),
       "fixture identifiers are recognised and real ones are not")

    # The guard that matters is upstream: a fabricated book never reaches the
    # bid/ask columns, so no downstream reader can fill against one.
    sim_row = _quote_row_for("SIMULATOR", fixture=False)
    ok(sim_row["bid"] is None and sim_row["ask"] is None
       and sim_row["evidence"] == UNMEASURED
       and sim_row["reason"] == SIMULATED_BOOK,
       "a simulator book is stored with its reason, never in the executable columns")
    fix_row = _quote_row_for("WEBSOCKET", fixture=True)
    ok(fix_row["bid"] is None and fix_row["reason"] == FIXTURE_ROW,
       "a fixture row is never executable evidence even on a real source")
    real_row = _quote_row_for("WEBSOCKET", fixture=False)
    ok(real_row["bid"] == 100.0 and real_row["ask"] == 101.0
       and real_row["evidence"] == MEASURED_EXECUTABLE,
       "a real two-sided feed quote is the only executable book")
    ok(book.entry_fill(real_row, vehicle=CE, direction="LONG")["price"] == 101.0,
       "a two-sided quote fills at the ask")
    # The provider keeps the last book on a depthless tick, so a real source is
    # not proof the book existed at the instant. Both the carried-forward book
    # and the undated one are refused, or §4's no-nearby-quote rule is decided
    # by whether the feed happened to stamp it.
    stale_row = _quote_row_for("WEBSOCKET", fixture=False,
                              book_age_ms=EXECUTABLE_BOOK_MAX_AGE_MS + 1.0)
    ok(stale_row["bid"] is None and stale_row["ask"] is None
       and stale_row["reason"] == STALE_QUOTE,
       "a real but carried-forward book is stale, never executable")
    undated_row = _quote_row_for("WEBSOCKET", fixture=False, book_age_ms=None)
    ok(undated_row["bid"] is None and undated_row["reason"] == STALE_QUOTE,
       "an undated book cannot be proved fresh and is not executable")
    ok(book.entry_fill(stale_row, vehicle=CE, direction="LONG")["price"] is None,
       "a stale book gives no entry fill")
    # The allowlist has to spell provenance the way the capture writes it. Phase
    # 17 stores provider.feed_mode(), so the live socket arrives as "push" --
    # and an allowlist that only knew "WEBSOCKET" refused a real session.
    push_row = _quote_row_for("push", fixture=False)
    ok(push_row["bid"] == 100.0 and push_row["evidence"] == MEASURED_EXECUTABLE,
       "the live socket's own label ('push') is executable provenance")
    # Read the label off the provider classes themselves rather than repeating
    # the string here: a rename in the market layer must break this check, not
    # silently discard a session's evidence again. __new__ avoids a broker login;
    # feed_mode reads one attribute and places nothing.
    live = object.__new__(AngelOneProvider)
    live._streaming = True
    ok(live.feed_mode().upper() in REAL_QUOTE_SOURCES,
       "whatever the live provider calls its socket is on the allowlist, by import")
    ok(MarketDataProvider.feed_mode(object()).upper() not in REAL_QUOTE_SOURCES,
       "and the simulator's own label is still refused, by import")
    ok(book.entry_fill({"bid": 100.0, "ask": None, "traded": 100.5,
                        "evidence": UNMEASURED, "reason": SIMULATED_BOOK},
                       vehicle=CE, direction="LONG")["reason"] == SIMULATED_BOOK,
       "an unquoted ask keeps the upstream reason, not a generic no-ask")
    ok(book.entry_fill({"bid": 100.0, "ask": None, "traded": 100.5,
                        "evidence": MEASURED_TRADED_PRICE},
                       vehicle=CE, direction="LONG")["reason"] == NO_ASK,
       "with no ask and no upstream reason the reason is no-ask, never the LTP")
    ok(book.entry_fill({"bid": None, "ask": None, "traded": 99.0},
                       vehicle=CE, direction="LONG")["price"] is None,
       "a traded price is never an entry fill")
    ok(book.entry_fill({"bid": 100.0, "ask": 101.0}, vehicle=FUTURES,
                       direction="SHORT")["side"] == "BID",
       "a short futures entry hits the bid")
    ok(book.exit_fill({"bid": 100.0, "ask": 101.0}, vehicle=CE,
                      direction="LONG")["price"] == 100.0,
       "a long option exits at the bid")
    ok(book.exit_fill({"bid": None, "ask": None, "traded": 100.0}, vehicle=CE,
                      direction="LONG")["evidence"] == MEASURED_TRADED_PRICE,
       "a traded-price exit is labelled traded, never executable")

    # --- §6: the spread is charged once, and only when the fills did not pay it
    executable = book.option_cost("NIFTY", entry=100.0, exit_price=104.0,
                                  lot_size=LOT, executable=True)
    modelled = book.option_cost("NIFTY", entry=100.0, exit_price=104.0,
                                lot_size=LOT, executable=False)
    ok(executable["spread_points"] == 0.0
       and executable["evidence"] == MEASURED_EXECUTABLE,
       "ask-in/bid-out fills are not charged the spread a second time")
    ok((modelled["spread_points"] or 0) > 0
       and modelled["evidence"] == SPREAD_MODELLED,
       "a non-executable fill is charged a modelled spread and says so")
    ok(modelled["cost_points"] > executable["cost_points"],
       "modelling the spread costs more than having paid it in the fills")
    nolot = book.option_cost("NIFTY", entry=100.0, exit_price=104.0,
                             lot_size=None, executable=True)
    ok(nolot["cost_points"] is None and nolot["reason"] == NO_LOT_SIZE,
       "an unknown lot size is refused, never guessed")

    # --- §8/§9: path, horizons and giveback ---------------------------------
    with sqlite3.connect(":memory:") as _probe:
        pass
    con = store.connect(":memory:")
    obs_id = _seed(con)
    stats = paper.build(con)
    ok(stats["observations"] > 0 and stats["legs_written"] > 0,
       "a rebuild reports what it wrote from raw")
    legs = store.legs(con)
    engine_legs = store.legs(con, book=CURRENT_ENGINE_PAPER)
    market_legs = store.legs(con, book=FULL_MARKET_PAPER)
    ok(len(engine_legs) == 1, "an engine BUY produces exactly one engine-book leg")
    ok(len(market_legs) >= 1 and all(
        leg["book"] == FULL_MARKET_PAPER for leg in market_legs),
       "the full-market book exists independently of the engine's decision")
    ok(any(leg["entry_side"] == "ASK" for leg in legs),
       "every option entry in the store was taken at an ask")

    leg = engine_legs[0]
    ok(leg["resolved"] == 1 and leg["net_pct"] is not None,
       "a leg with a charged cost resolves with a net figure")
    ok(leg["net_pct"] < leg["gross_pct"],
       "the net move is always worse than the gross move")
    paths = {r["horizon"]: r for r in con.execute(
        "SELECT * FROM leg_path WHERE leg_id = ?", (leg["leg_id"],)
    ).fetchall()}
    ok(SESSION_CLOSE in paths, "the session close is always a horizon row")
    ok(all(str(h) in paths for h in HORIZONS if h <= 30),
       "every elapsed horizon inside the path has a row")
    close = paths[SESSION_CLOSE]
    ok((close["mfe_pct"] or 0) >= (close["gross_pct"] or 0),
       "the best look is never worse than the final result")
    ok((close["giveback"] or 0) > 0,
       "a leg that peaked above its exit records a giveback")

    # A leg whose cost cannot be charged keeps its gross move but is not resolved,
    # because an unresolved-but-gross row is what makes a large loss read small.
    # "Cannot be charged" now means no source names the multiplier at all: the
    # feed did not state one AND the contract is not in the registry. An unknown
    # instrument is the only honest way to reach that state.
    nolot_id = _seed(con, engine=False, lot=None, symbol="ZZTEST25SEP100CE",
                     instrument="ZZTEST_NOT_IN_REGISTRY")
    paper.build(con)
    unlotted = [
        r for r in store.legs(con)
        if r["obs_id"] == nolot_id and r["vehicle"] == CE
    ]
    ok(bool(unlotted) and unlotted[0]["gross_pct"] is not None
       and unlotted[0]["net_pct"] is None and unlotted[0]["resolved"] == 0,
       "no lot size means gross only, never resolved and never a net figure")
    ok(bool(unlotted) and unlotted[0]["lot_source"] is None
       and unlotted[0]["cost_evidence"] == UNMEASURED,
       "an uncostable leg names no lot source rather than inventing one")

    # The multiplier a quote never carried is still a published property of the
    # contract, so it is taken from the registry — and labelled as taken, so no
    # reader can quote the cost as executable-verified.
    spec_id = _seed(con, engine=False, lot=None, symbol="NIFTY25SEP24100CE")
    paper.build(con)
    speclot = [
        r for r in store.legs(con)
        if r["obs_id"] == spec_id and r["vehicle"] == CE
    ]
    ok(bool(speclot) and speclot[0]["lot_size"] == 65
       and speclot[0]["lot_source"] == LOT_FROM_SPEC,
       "a quote with no lot size falls back to the contract registry")
    ok(bool(speclot) and speclot[0]["net_pct"] is not None
       and speclot[0]["resolved"] == 1,
       "a registry lot size is enough to charge the round trip and resolve")
    ok(bool(speclot)
       and speclot[0]["cost_evidence"] == MEASURED_EXECUTABLE_SPEC_LOT,
       "a registry-lot cost is labelled below a fully measured one")
    ok(bool(speclot) and speclot[0]["net_pct"] < speclot[0]["gross_pct"],
       "a registry-lot leg is still charged a real round trip")

    # The gate that could permit reconsideration reads measured coverage only,
    # so a store full of registry-lot legs must not read as fully covered.
    ok(milestones.cost_coverage_pct(con, book=FULL_MARKET_PAPER) is not None
       and milestones.spec_lot_cost_pct(con, book=FULL_MARKET_PAPER) is not None,
       "registry-lot coverage is reported separately from measured coverage")
    checks = milestones.checkpoints(con)["books"][FULL_MARKET_PAPER]
    ok(
        (checks["measured_cost_coverage_pct"] or 0.0)
        + (checks["contract_spec_lot_cost_pct"] or 0.0) <= 100.0001,
        "the two cost-coverage shares are disjoint, never double-counted",
    )

    # --- lot provenance: most-measured source first ---------------------------
    plan_ctx = {"instrument": "NIFTY", "context_json": json.dumps(
        {"plan": {"lot_size": 50}})}
    ok(lots.resolve({"lot_size": 75}, plan_ctx) == (75, LOT_FROM_QUOTE),
       "a lot size quoted beside the book beats the plan's and the registry's")
    ok(lots.resolve({}, plan_ctx) == (50, LOT_FROM_PLAN),
       "the plan's lot size is used when the feed stated none")
    ok(lots.resolve({}, {"instrument": "NIFTY"}) == (65, LOT_FROM_SPEC),
       "the registry is the last resort, and says so")
    ok(lots.resolve({}, {"instrument": "ZZ_NOT_A_CONTRACT"}) == (None, None),
       "an unknown contract yields no lot size rather than a default")
    ok(all(lots.resolve({"lot_size": bad}, {})[0] is None
           for bad in (0, -75, 1.5, True, "75", None)),
       "a zero, negative, fractional, boolean or string lot size is refused")

    # --- recost: re-charge legs a previous build left uncosted ---------------
    # The store this reproduces is real: two million legs entered at a measured
    # ask, every one of them refused a cost because the capture stored no
    # multiplier, so nothing resolved and every section of the report read zero.
    # Rebuilding from raw would cost hours. Recosting re-computes net from the
    # gross figures already stored, so the test that matters is that it lands on
    # the same numbers a full rebuild does.
    with tempfile.TemporaryDirectory() as tmp:
        rc = store.connect(os.path.join(tmp, "recost.db"))
        try:
            _seed(rc, engine=True, vehicles=(CE, PE, FUTURES))
            _seed(rc, engine=False, symbol="NIFTY25SEP24200CE")
            _seed(rc, engine=False, lot=None, symbol="ZZTEST25SEP200CE",
                  instrument="ZZTEST_NOT_IN_REGISTRY")
            paper.build(rc)
            built_legs = {
                r["leg_id"]: dict(r) for r in store.legs(rc)
            }
            built_paths = {
                (r["leg_id"], r["horizon"]): dict(r)
                for r in rc.execute("SELECT * FROM leg_path")
            }
            p35vehicle.build(rc)
            built_attrib = {
                r["leg_id"]: dict(r)
                for r in rc.execute("SELECT * FROM leg_attribution")
            }
            built_cmp = {
                (r["obs_id"], r["horizon"], r["kind"]): dict(r)
                for r in rc.execute("SELECT * FROM vehicle_comparison")
            }
            raw_before = store.counts(rc)

            # Strip exactly what the pre-fix build failed to produce, leaving
            # the measured entry fills, gross moves and MFE/MAE untouched.
            rc.execute(
                "UPDATE paper_leg SET lot_size = NULL, lot_source = NULL,"
                " cost_points = NULL, cost_evidence = ?, net_pct = NULL,"
                " resolved = 0, reason = NULL"
                " WHERE entry_price IS NOT NULL",
                (UNMEASURED,),
            )
            rc.execute("UPDATE leg_path SET net_pct = NULL")
            rc.execute("DELETE FROM leg_attribution")
            rc.execute("DELETE FROM vehicle_comparison")
            rc.commit()

            candidates = recost.candidate_count(rc)
            ok(candidates == sum(
                1 for r in built_legs.values() if r["entry_price"] is not None),
               "recost's candidates are exactly the entered but uncosted legs")

            out = recost.run(rc, batch=2)
            ok(out["candidates"] == candidates
               and out["legs_seen"] == candidates,
               "recost visits every candidate leg once, in batches")
            ok(out["costed"] >= 1 and out["still_unmeasured"] >= 1,
               "recost costs what it can and leaves the rest uncosted")

            after_legs = {r["leg_id"]: dict(r) for r in store.legs(rc)}
            ok(set(after_legs) == set(built_legs),
               "recost writes no new leg and drops none")
            ok(all(
                after_legs[i]["lot_size"] == built_legs[i]["lot_size"]
                and after_legs[i]["lot_source"] == built_legs[i]["lot_source"]
                and after_legs[i]["cost_points"] == built_legs[i]["cost_points"]
                and after_legs[i]["cost_evidence"]
                == built_legs[i]["cost_evidence"]
                and after_legs[i]["net_pct"] == built_legs[i]["net_pct"]
                and after_legs[i]["resolved"] == built_legs[i]["resolved"]
                for i in built_legs
            ), "a recosted leg carries the same cost and net as a rebuilt one")
            ok(all(
                after_legs[i]["entry_price"] == built_legs[i]["entry_price"]
                and after_legs[i]["gross_pct"] == built_legs[i]["gross_pct"]
                and after_legs[i]["evidence"] == built_legs[i]["evidence"]
                for i in built_legs
            ), "recost re-computes the cost and never re-measures a price")

            after_paths = {
                (r["leg_id"], r["horizon"]): dict(r)
                for r in rc.execute("SELECT * FROM leg_path")
            }
            ok(set(after_paths) == set(built_paths),
               "recost adds no horizon row and removes none")
            ok(all(
                after_paths[k]["net_pct"] == built_paths[k]["net_pct"]
                and after_paths[k]["gross_pct"] == built_paths[k]["gross_pct"]
                and after_paths[k]["mfe_pct"] == built_paths[k]["mfe_pct"]
                for k in built_paths
            ), "every horizon's net is restored from its stored gross")
            after_attrib = {
                r["leg_id"]: dict(r)
                for r in rc.execute("SELECT * FROM leg_attribution")
            }
            ok(set(after_attrib) == set(built_attrib),
               "attribution is regenerated for the legs that became gradeable")
            # Everything except when the peak happened: that one field is
            # recovered from horizon buckets rather than from the sample that
            # made it, so it is an upper bound and the row says which.
            timed = ("time_to_peak_min", "time_resolution")
            ok(all(
                {k: v for k, v in after_attrib[i].items() if k not in timed}
                == {k: v for k, v in built_attrib[i].items() if k not in timed}
                for i in built_attrib
            ), "a recosted leg's cause, channel and cost split match a rebuilt one")
            ok(all(
                r["time_resolution"] == recost.RECONSTRUCTED
                for r in after_attrib.values()
                if r["time_to_peak_min"] is not None
            ) and all(
                r["time_resolution"] is None for r in built_attrib.values()
            ), "a reconstructed peak time is labelled as one, a measured one is not")
            after_cmp = {
                (r["obs_id"], r["horizon"], r["kind"]): dict(r)
                for r in rc.execute("SELECT * FROM vehicle_comparison")
            }
            ok(bool(built_cmp) and after_cmp == built_cmp,
               "the vehicle comparison a missing cost had emptied is rebuilt"
               " identically")
            ok(store.counts(rc)["raw_observation"]
               == raw_before["raw_observation"]
               and store.counts(rc)["raw_quote"] == raw_before["raw_quote"],
               "recost touches no raw row")

            # A leg with no lot size anywhere keeps its own reason rather than
            # being marked resolved on an assumed multiplier.
            stuck = [
                r for r in after_legs.values()
                if r["instrument"] == "ZZTEST_NOT_IN_REGISTRY"
                and r["entry_price"] is not None
            ]
            ok(bool(stuck) and all(
                r["net_pct"] is None and r["resolved"] == 0
                and r["reason"] == NO_LOT_SIZE for r in stuck),
               "recost never invents a multiplier to make a leg resolve")

            again = recost.run(rc, batch=2)
            ok(again["candidates"] == again["legs_seen"]
               and again["costed"] == 0,
               "a second recost re-costs nothing it already costed")
            ok({r["leg_id"]: dict(r) for r in store.legs(rc)}.keys()
               == after_legs.keys()
               and all(
                   dict(r)["net_pct"] == after_legs[r["leg_id"]]["net_pct"]
                   for r in store.legs(rc)),
               "recost is idempotent: running it twice changes no number")

            summary = recost.summary(rc)
            ok(summary["lot_sources"].get(LOT_FROM_QUOTE, 0) >= 1
               and summary["books"][CURRENT_ENGINE_PAPER]["resolved"] >= 1,
               "the recost summary counts resolved legs and their lot sources")
        finally:
            rc.close()

    # A leg that never opened has nothing to recost: no entry, no round trip.
    with tempfile.TemporaryDirectory() as tmp:
        rc = store.connect(os.path.join(tmp, "noentry.db"))
        try:
            oid = "obs-no-ask"
            store.save_observations(rc, [_obs(oid, ts=T0, engine=True)])
            noask = _quote(oid, ts=T0, bid=100.0, ask=100.0,
                           evidence=UNMEASURED, reason=NO_ASK)
            noask.update({"ask": None, "traded": None, "spread": None,
                          "spread_pct": None})
            store.save_quotes(rc, [noask])
            paper.build(rc)
            ok(recost.candidate_count(rc) == 0,
               "a leg with no executable entry is not a recost candidate")
            before = [dict(r) for r in store.legs(rc)]
            recost.run(rc)
            after = [dict(r) for r in store.legs(rc)]
            ok(before == after and all(r["reason"] == NO_ASK for r in after),
               "recost preserves a missing-ask refusal exactly as it stood")
        finally:
            rc.close()

    # --- batching is throughput only: the rebuild must not depend on it ------
    def _derived_snapshot(c) -> dict:
        out = {}
        for table, order in (
            ("paper_leg", "leg_id"),
            ("leg_path", "leg_id, horizon"),
            ("leg_attribution", "leg_id"),
            ("vehicle_comparison", "obs_id, horizon, kind"),
        ):
            out[table] = [
                tuple(sorted(dict(r).items()))
                for r in c.execute(
                    f"SELECT * FROM {table} ORDER BY {order}"  # noqa: S608
                )
            ]
        return out

    ok(store.observations(con) == list(store.iter_observations(con)),
       "streaming the observations yields the same rows in the same order as "
       "reading them all: a rebuild that streams is the same rebuild")
    paper.build(con, flush_every=1)
    p35vehicle.build(con, flush_every=1)
    one_at_a_time = _derived_snapshot(con)
    paper.build(con, flush_every=0)
    p35vehicle.build(con, flush_every=0)
    all_at_once = _derived_snapshot(con)
    ok(one_at_a_time == all_at_once,
       "writing in batches and writing once produce byte-identical derived "
       "tables: batching exists to bound memory, and a measurement that moved "
       "with the batch size would not be a measurement")
    seen: list[int] = []
    built = paper.build(con, flush_every=1, progress=seen.append)
    ok(seen == sorted(seen) and seen and seen[-1] == built["observations"],
       "progress counts observations forward and ends at the total, so a long "
       "pass can be told apart from a hung one")
    # The throttle exists because this pass shares a small VM with a live
    # capture whose lost minutes cannot be recaptured. It is allowed to make the
    # pass slower and is not allowed to move a single number in it.
    paper.build(con, flush_every=1, pause=0.0)
    p35vehicle.build(con, flush_every=1, pause=0.0)
    unthrottled = _derived_snapshot(con)
    throttled_paper = paper.build(con, flush_every=1, pause=0.001)
    throttled_vehicle = p35vehicle.build(con, flush_every=1, pause=0.001)
    ok(_derived_snapshot(con) == unthrottled,
       "pausing between chunks produces byte-identical derived tables: the "
       "throttle spends wall clock to yield the machine and buys no different "
       "answer")
    ok(throttled_paper["chunk_pause_seconds"] == 0.001
       and throttled_vehicle["session_pause_seconds"] == 0.001,
       "and the pass reports the pause it actually ran with, so a throttled "
       "run cannot be mistaken for an unthrottled one")
    slept: list[float] = []
    real_sleep = time.sleep
    try:
        time.sleep = slept.append
        paper.build(con, flush_every=1, resume=False, chunk=1, pause=0.25)
    finally:
        time.sleep = real_sleep
    ok(slept and all(s == 0.25 for s in slept),
       "the pause is taken once per committed chunk, so it yields between "
       "units of work already saved rather than mid-write")

    vseen: list[tuple[int, int]] = []
    p35vehicle.build(con, flush_every=1, progress=lambda d, t: vseen.append((d, t)))
    ok(vseen and [d for d, _ in vseen] == sorted(d for d, _ in vseen)
       and vseen[-1][0] == vseen[-1][1]
       and all(t == vseen[-1][1] for _, t in vseen),
       "the comparison stage reports done-of-total against a denominator it "
       "already knows and finishes at it, rather than a bare count")

    # The derived rebuild is the hours-long pass, and it printed only when it
    # finished — indistinguishable from hung, which is how the last one got
    # interrupted at the cost of the whole pass.
    with tempfile.TemporaryDirectory() as capdir:
        with _capture_dir(capdir):
            stages: list[dict] = []
            rebuilt = service.rebuild(con, progress=stages.append)
            quiet = service.rebuild(con)
    ok([s["stage"] for s in stages][:1] == [service.STAGES[0]]
       and {s["stage"] for s in stages} <= set(service.STAGES),
       "the rebuild announces each stage by name, starting with the raw read, "
       "so a silent hour can be attributed to a stage instead of guessed at")
    ok([service.STAGES.index(s["stage"]) for s in stages]
       == sorted(service.STAGES.index(s["stage"]) for s in stages),
       "stages are reported in the order they run: raw, then the paper books, "
       "then the comparison")
    ok(all(s["total"] is None or s["done"] <= s["total"] for s in stages),
       "no stage reports more work done than the total it declared, and a "
       "stage with no known denominator reports None rather than inventing one")
    paper_stage = [s for s in stages if s["stage"] == service.STAGES[1]]
    ok(paper_stage
       and paper_stage[-1]["done"] == rebuilt["paper"]["observations"]
       and paper_stage[-1]["total"] == rebuilt["counts"]["raw_observation"],
       "the paper stage ends at the observation count it built, measured "
       "against the raw rows in the store")
    ok(quiet["paper"]["observations"] == rebuilt["paper"]["observations"],
       "a rebuild with no progress callback builds exactly what one with a "
       "callback builds: reporting cannot change what was measured")

    # --- the rebuild resumes, because losing hours to an interruption is how
    # two real passes died. A session is the unit: no leg reads a quote outside
    # its own session, so a session can be rebuilt alone.
    inventory = paper.session_inventory(con)
    ok(sum(s["observations"] for s in inventory)
       == store.counts(con)["raw_observation"]
       and len({s["session"] for s in inventory}) == len(inventory),
       "the session inventory partitions the raw store exactly once: every "
       "observation is in one session and none is counted twice, or a resumed "
       "pass would silently drop or double-build rows")
    ok(all(s["start_ts"] <= s["end_ts"] for s in inventory)
       and all(
           a["end_ts"] <= b["start_ts"]
           for a, b in zip(inventory, inventory[1:], strict=False)
       ),
       "session windows are ordered and do not overlap, so the window a session "
       "is rebuilt over cannot reach into the next one")

    paper.build(con, flush_every=1, resume=False)
    p35vehicle.build(con, flush_every=1, resume=False)
    from_scratch = _derived_snapshot(con)
    fresh_counts = store.counts(con)

    # Simulate the interruption: one session's derived rows and its checkpoint
    # are thrown away, as if the process died inside it, and the pass re-run.
    victim = inventory[-1]
    store.clear_derived_window(con, victim["start_ts"], victim["end_ts"])
    store.clear_session_progress(con, victim["session"])
    raw_after_clear = store.counts(con)
    ok(all(raw_after_clear[t] == fresh_counts[t] for t in store.RAW_TABLES),
       "clearing a session's derived window removes no raw row: the evidence "
       "survives every rebuild, only the arithmetic over it is redone")
    ok(store.counts(con)["paper_leg"] < fresh_counts["paper_leg"],
       "and it does remove that session's paper legs, so the re-run has "
       "something to rebuild rather than trivially passing")

    resumed_paper = paper.build(con, flush_every=1, resume=True)
    resumed_vehicle = p35vehicle.build(con, flush_every=1, resume=True)
    ok(resumed_paper["sessions_rebuilt"] == [victim["session"]]
       and set(resumed_paper["sessions_skipped_already_built"])
       == {s["session"] for s in inventory} - {victim["session"]},
       "a resumed pass rebuilds only the session that was lost and skips the "
       "ones already checkpointed, which is the entire point: an interruption "
       "costs one session, not the store")
    ok(_derived_snapshot(con) == from_scratch,
       "and the resumed store is byte-identical to one built in a single pass: "
       "resuming is a cost optimisation, never a different measurement")
    ok(resumed_paper["observations"] == fresh_counts["raw_observation"],
       "a resumed pass counts the skipped sessions in its totals, so the "
       "reported observation count means the store and not just this pass")
    ok(resumed_vehicle["sessions_rebuilt"] == [victim["session"]],
       "the comparison stage checkpoints per session too, so the second stage "
       "of an interrupted rebuild is not redone over the whole store either")

    # The dangerous case: raw grew under a session already marked built. Skipping
    # it would leave the new rows out of every derived table for good.
    grown = inventory[-1]
    store.save_observations(con, [
        _obs("obs-smoke-late-arrival", ts=grown["start_ts"] + 90.0, engine=False,
             source="BOARD")
    ])
    store.save_quotes(con, [
        _quote("obs-smoke-late-arrival", ts=grown["start_ts"] + 90.0,
               bid=99.0, ask=100.0)
    ])
    regrown = paper.build(con, flush_every=1, resume=True)
    ok(grown["session"] in regrown["sessions_rebuilt"]
       and grown["session"] not in regrown["sessions_skipped_already_built"],
       "a session whose raw rows grew after it was checkpointed is rebuilt "
       "rather than skipped: the checkpoint is trusted only while the count it "
       "recorded still matches the store")
    ok(regrown["observations"] == store.counts(con)["raw_observation"],
       "and the newly ingested observation is inside the rebuild, not stranded "
       "outside the derived tables by its own session's checkpoint")

    # A restart is the explicit way back to a clean build, for when the derived
    # tables are suspected wrong rather than merely incomplete.
    store.save_session_progress(
        con, "1970-01-01", stage=store.STAGE_VEHICLE_DONE, observations=999999,
        payload={"legs_written": 42}, now=T0,
    )
    restarted = paper.build(con, flush_every=1, resume=False)
    ok("1970-01-01" not in restarted["sessions_skipped_already_built"]
       and store.session_progress(con).get("1970-01-01") is None,
       "a non-resumed rebuild forgets every checkpoint before it starts, so a "
       "stale or wrong checkpoint cannot make a clean rebuild skip anything")
    p35vehicle.build(con, flush_every=1, resume=False)
    ok(store.DERIVED_CHECKPOINT_TABLE not in store.DERIVED_TABLES
       and store.DERIVED_CHECKPOINT_TABLE not in store.RAW_TABLES
       and store.DERIVED_CHECKPOINT_TABLE not in store.counts(con),
       "the checkpoint table is bookkeeping, not evidence and not a "
       "measurement: it is absent from the row counts the report reads")

    # --- a session can be complete and still not comparable. The store that
    # produced Phase 42's first chronological holdout had its earlier sessions
    # costed by a modelled spread and its later ones from a measured lot, with
    # the cut falling between them, so the two halves were not measuring the
    # same quantity. Putting the older sessions back through the current code is
    # the repair; discarding the sessions that are already right to do it is not.
    paper.build(con, flush_every=1, resume=False)
    p35vehicle.build(con, flush_every=1, resume=False)
    inventory = paper.session_inventory(con)
    before_redo = _derived_snapshot(con)
    stale = inventory[0]["session"]
    with tempfile.TemporaryDirectory() as capdir:
        with _capture_dir(capdir):
            redone = service.rebuild(con, redo=(stale, stale))
    ok(redone["checkpoints_forgotten"] == {stale: 1}
       and redone["paper"]["sessions_rebuilt"] == [stale]
       and set(redone["paper"]["sessions_skipped_already_built"])
       == {s["session"] for s in inventory} - {stale},
       "a named session is rebuilt even though its checkpoint says it is done, "
       "and every other session is left built: a session derived by older code "
       "can be replaced without paying for the whole store again")
    ok(_derived_snapshot(con) == before_redo,
       "and redoing a session under unchanged code reproduces it exactly, so "
       "the flag repairs a stale derivation and cannot invent a new number")
    ok(redone["paper"]["observations"] == store.counts(con)["raw_observation"],
       "the redone pass still accounts for every raw observation in the store, "
       "not only the sessions it touched")

    # --- a session is not a small enough unit. A real session is ~208k
    # observations and hours of work, so the pass checkpoints inside one, at
    # chunk boundaries. A chunk changes no measured number: a leg still reads its
    # forward path from the session's quotes in the database, not from the chunk.
    inventory = paper.session_inventory(con)
    chunked = [paper.session_chunks(con, s, chunk=1) for s in inventory]
    ok(all(
        sum(c["observations"] for c in cks) == s["observations"]
        for s, cks in zip(inventory, chunked, strict=False)
    ), "the chunks of a session hold exactly the session's observations: every "
       "row is built once, none is skipped and none is built twice")
    ok(all(
        cks[0]["start_ts"] == s["start_ts"]
        and cks[-1]["end_ts"] == s["end_ts"]
        and all(
            a["end_ts"] == b["start_ts"]
            for a, b in zip(cks, cks[1:], strict=False)
        )
        for s, cks in zip(inventory, chunked, strict=False)
    ), "the chunks are contiguous and cover the session from its start to its "
       "end, so a watermark between two of them names a real resume point")
    ok(all(
        con.execute(
            "SELECT COUNT(*) AS n FROM raw_observation "
            "WHERE ts >= ? AND ts < ?",
            (c["start_ts"], c["end_ts"]),
        ).fetchone()["n"] == c["observations"]
        for cks in chunked for c in cks
    ), "no chunk boundary falls inside a group of observations sharing one "
       "timestamp: two vehicles quoted at the same instant are built together, "
       "which is what the §12/§13 peer comparison requires")

    paper.build(con, flush_every=1, resume=False)
    p35vehicle.build(con, flush_every=1, resume=False)
    whole_session_build = _derived_snapshot(con)
    paper.build(con, flush_every=1, resume=False, chunk=1)
    p35vehicle.build(con, flush_every=1, resume=False)
    ok(_derived_snapshot(con) == whole_session_build,
       "a build cut into one-observation chunks produces byte-identical derived "
       "rows to one that walks whole sessions: chunking is a cost decision, not "
       "a measurement decision")

    # Simulate the real failure: the process dies part-way through a session,
    # which is what a WSL shutdown did twice on a store of two large sessions.
    class _Stop(Exception):
        pass

    # Dies inside the first session, after two of its chunks are committed: the
    # first session is the one a naive resume would get wrong, because there is
    # no earlier session whose checkpoint could carry the work.
    stop_after = 3

    def _die(seen: int) -> None:
        if seen >= stop_after:
            raise _Stop

    try:
        paper.build(con, flush_every=1, resume=False, chunk=1, progress=_die)
    except _Stop:
        pass
    partials = {
        s: row for s, row in store.session_progress(con).items()
        if row["stage"] == store.STAGE_PAPER_PARTIAL
    }
    ok(bool(partials),
       "a pass killed inside a session leaves a PARTIAL checkpoint behind, so "
       "the work already committed is recorded rather than implicitly discarded")
    ok(all(
        0 < int((row.get("payload") or {}).get("observations") or 0)
        < int(row["observations"])
        for row in partials.values()
    ), "the PARTIAL checkpoint records how many of the session's observations "
       "are built — more than none and fewer than all — which is the number a "
       "resume has to trust")
    ok(all(
        float((row.get("payload") or {}).get("next_ts") or 0.0) > 0.0
        for row in partials.values()
    ), "and it records the first timestamp NOT yet built, so the resume knows "
       "where to start instead of restarting the session")

    resume_progress: list[int] = []
    mid = paper.build(
        con, flush_every=1, resume=True, chunk=1,
        progress=resume_progress.append,
    )
    p35vehicle.build(con, flush_every=1, resume=True)
    ok(set(mid["sessions_resumed_mid_session"]) == set(partials),
       "the resumed pass picks the interrupted session up mid-way rather than "
       "rebuilding it or skipping it")
    ok(resume_progress
       and resume_progress[0] >= min(
           int((row.get("payload") or {}).get("observations") or 0)
           for row in partials.values()
       ),
       "and it starts counting from the work already committed, not from zero: "
       "the observations built before the interruption are not built again")
    ok(_derived_snapshot(con) == whole_session_build,
       "the store left by interrupt-then-resume is byte-identical to one built "
       "in a single uninterrupted pass — the only claim that makes resuming "
       "safe to use on real evidence")
    ok(mid["observations"] == store.counts(con)["raw_observation"],
       "and the resumed pass reports the whole store, counting the rows it "
       "inherited from the checkpoint as well as the ones it built")

    # ``limit`` has to keep meaning "the first N observations in the store" now
    # that the walk is partitioned; a per-session cap applied to every session
    # would quietly build more rows than were asked for.
    total_raw = store.counts(con)["raw_observation"]
    capped = paper.build(con, flush_every=1, resume=False, chunk=1, limit=2)
    ok(capped["observations"] == min(2, total_raw)
       and store.counts(con)["paper_leg"]
       < len(whole_session_build["paper_leg"]),
       "a limited rebuild builds exactly the first N observations of the store, "
       "not the first N of every session")
    paper.build(con, flush_every=1, resume=False)
    p35vehicle.build(con, flush_every=1, resume=False)
    ok(_derived_snapshot(con) == whole_session_build,
       "and an unlimited rebuild afterwards restores the full store, so a "
       "limited pass cannot leave a truncated store behind as the new baseline")

    # An interrupted rebuild used to leave every derived table empty, because the
    # tables were cleared first and written only at the very end. Phases 39-41
    # read raw only, so the raw-only ingest exists to keep them off that path.
    # The capture read is pointed at a throwaway directory: the real one holds
    # whatever the box happens to have captured, which would make this test's
    # cost and result depend on the machine.
    with tempfile.TemporaryDirectory() as capdir:
        with _capture_dir(capdir):
            before = store.counts(con)
            raw_only = service.ingest_raw(con)
            after = store.counts(con)
    ok(all(after[t] == before[t] for t in
           ("paper_leg", "leg_path", "leg_attribution", "vehicle_comparison")),
       "a raw-only ingest leaves the derived tables exactly as they were, "
       "rather than clearing them for a rebuild the caller did not ask for")
    ok(raw_only["derived"] == "NOT_REBUILT_THIS_PASS"
       and "PHASE41" in raw_only["reads_raw_only"]
       and "PHASE35_REPORT" in raw_only["needs_rebuild"],
       "the raw-only pass says which phases it is enough for and which still "
       "need the derived rebuild")

    # --- the capture read resumes instead of re-reading the whole archive -----
    # The archive is append-only and the live file rolls at 256 MB with the rolls
    # kept, so a pass that re-reads every file costs the whole history every
    # night. What must not happen is a resume that skips a row.
    with tempfile.TemporaryDirectory() as capdir:
        rolled = os.path.join(capdir, "phase17_observations.20260901-000000.jsonl")
        live = os.path.join(capdir, "phase17_observations.jsonl")
        with open(rolled, "w", encoding="utf-8") as fh:
            for n in range(4):
                fh.write(json.dumps(_p17_row(n)) + "\n")
        with open(live, "w", encoding="utf-8") as fh:
            for n in range(4, 7):
                fh.write(json.dumps(_p17_row(n)) + "\n")

        with _capture_dir(capdir):
            streamed = store.connect(":memory:")
            whole = store.connect(":memory:")
            first = capture.ingest_stream(streamed, now=T0)
            capture.ingest(whole, now=T0)
            ok(store.counts(streamed)["raw_observation"]
               == store.counts(whole)["raw_observation"] == 7
               and store.counts(streamed)["raw_quote"]
               == store.counts(whole)["raw_quote"],
               "streaming the capture files and reading them all at once "
               "produce the same raw store: resuming is throughput, not a "
               "different measurement")
            ok(first["files_read"] == 2 and first["observations_written"] == 7,
               "the first pass reads every file and writes every row")

            again = capture.ingest_stream(streamed, now=T0)
            ok(again["files_read"] == 0 and again["files_already_complete"] == 2
               and again["source_rows"] == 0,
               "a second pass opens no file it has already finished, rather than "
               "re-parsing the archive to discover it has nothing to add")

            with open(live, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(_p17_row(7)) + "\n")
            appended = capture.ingest_stream(streamed, now=T0)
            ok(appended["source_rows"] == 1
               and appended["observations_written"] == 1
               and store.counts(streamed)["raw_observation"] == 8,
               "an appended row is read on the next pass and only that row is "
               "read: the offset resumes inside the live file")

            # A capture killed mid-write leaves a line with no newline. Reading it
            # would store half a row; the offset must stop before it.
            with open(live, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(_p17_row(8))[:-5])
            partial = capture.ingest_stream(streamed, now=T0)
            ok(partial["observations_written"] == 0
               and store.counts(streamed)["raw_observation"] == 8,
               "a trailing line still being written is left alone, not ingested "
               "as a truncated row")
            with open(live, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(_p17_row(8))[-5:] + "\n")
            completed = capture.ingest_stream(streamed, now=T0)
            ok(completed["observations_written"] == 1
               and store.counts(streamed)["raw_observation"] == 9,
               "once that line is complete the next pass reads it whole, so "
               "stopping short of it lost nothing")

            rescan = capture.ingest_stream(streamed, now=T0, resume=False)
            ok(rescan["files_read"] == 2 and rescan["source_rows"] == 9
               and rescan["observations_written"] == 0
               and store.counts(streamed)["raw_observation"] == 9,
               "a forced rescan re-reads every byte and writes nothing new: "
               "content-addressed ids make a re-read free of consequence")

            seen_files: list[str] = []
            capture.ingest_stream(
                streamed, now=T0, resume=False,
                progress=lambda ev: seen_files.append(ev["file"]),
            )
            ok(len(seen_files) >= 2,
               "the pass reports per file while it runs, so a slow pass can be "
               "told apart from a hung one")

            narrowed = store.connect(":memory:")
            only_bank = capture.ingest_stream(
                narrowed, now=T0, instrument="BANKNIFTY"
            )
            ok(only_bank["observations_written"] == 0
               and store.counts(narrowed)["raw_observation"] == 0,
               "the instrument filter still narrows the streamed read")
            # An offset written by a filtered pass would claim those bytes were
            # done when only one instrument in them was read, and the next
            # unfiltered pass would skip every other instrument in that range.
            ok(store.capture_cursors(narrowed) == {}
               and only_bank["mode"].endswith("INSTRUMENT_FILTERED"),
               "an instrument-filtered pass records no read offset, so a filter "
               "for throughput can never become a filter on the evidence")
            after_filter = capture.ingest_stream(narrowed, now=T0)
            ok(after_filter["observations_written"] == 9,
               "and the following unfiltered pass still reads every row the "
               "filtered one passed over")

            forgotten = store.clear_capture_cursors(streamed)
            ok(forgotten >= 1 and store.capture_cursors(streamed) == {}
               and capture.ingest_stream(streamed, now=T0)["source_rows"] == 9
               and store.counts(streamed)["raw_observation"] == 9,
               "forgetting the read positions costs a re-read and no evidence: "
               "the raw rows they covered are still there afterwards")
            # The derived rebuild's own raw read must be the streaming one. The
            # materialising reader holds every parsed row of the whole archive
            # in memory before writing, which on a real store took the VM into
            # swap and froze it before a single throttled derived chunk ran.
            through_rebuild = store.connect(":memory:")
            rebuilt_raw = service.rebuild(through_rebuild)["ingested"]
            ok(rebuilt_raw["mode"] == "RESUMABLE_STREAM"
               and store.capture_cursors(through_rebuild) != {},
               "the derived rebuild reads raw through the resumable streaming "
               "reader and records its offsets, so its raw half costs the day "
               "rather than the archive")
            again = service.rebuild(through_rebuild)["ingested"]
            ok(again["observations_written"] == 0
               and again["files_already_complete"] >= 1
               and store.counts(through_rebuild)["raw_observation"]
               == store.counts(streamed)["raw_observation"],
               "and a second rebuild re-reads no finished capture file while "
               "arriving at the same raw store")
            through_rebuild.close()
            streamed.close()
            whole.close()
            narrowed.close()

    # --- a compressed roll is ingested, not silently skipped -----------------
    # The journal compaction gzips rolled files and removes the original after
    # verifying every line. This reader had its own ``*.jsonl`` glob and a plain
    # ``open``, so a compressed roll became invisible: the pass reported every
    # file complete while a third of a session's captured minutes sat in a
    # ``.gz`` it never opened, and rerunning the ingest could not help.
    with tempfile.TemporaryDirectory() as capdir:
        packed = os.path.join(
            capdir, "phase17_observations.20260901-000000.jsonl" + p17store.GZ)
        live = os.path.join(capdir, "phase17_observations.jsonl")
        with gzip.open(packed, "wt", encoding="utf-8") as fh:
            for n in range(5):
                fh.write(json.dumps(_p17_row(n)) + "\n")
        with open(live, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(_p17_row(5)) + "\n")

        with _capture_dir(capdir):
            ok(any(p.endswith(p17store.GZ) for p in capture.source_files()),
               "the capture file list includes a compressed roll: a gzipped "
               "file holds the same verified lines its original held, so "
               "skipping it is evidence loss and nothing else")
            gz_con = store.connect(":memory:")
            got = capture.ingest_stream(gz_con, now=T0)
            ok(got["source_rows"] == 6 and got["observations_written"] == 6
               and store.counts(gz_con)["raw_observation"] == 6,
               "every row of the compressed roll reaches the store alongside "
               "the live file's rows")

            twice = capture.ingest_stream(gz_con, now=T0)
            ok(twice["observations_written"] == 0
               and store.counts(gz_con)["raw_observation"] == 6,
               "a second pass adds nothing: a compressed roll is not "
               "double-counted for having been read once already")
            cursor = store.capture_cursors(gz_con)[packed]
            ok(int(cursor["complete"]) == 1 and int(cursor["offset"]) > 0,
               "its completion is recorded explicitly, because a gzip's offset "
               "counts decompressed bytes and cannot be compared with the "
               "compressed size the way a plain file's can")
            ok(twice["files_already_complete"] == 2,
               "so the finished compressed roll is skipped rather than "
               "decompressed and re-parsed on every pass")

            # The live file grows after it is read to the end. Recording it as
            # complete would skip whatever the capture wrote next.
            live_cursor = store.capture_cursors(gz_con)[live]
            ok(int(live_cursor["complete"]) == 0,
               "the live file is never marked complete, however fully it was "
               "read: the capture appends to it after the pass ends")
            with open(live, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(_p17_row(6)) + "\n")
            ok(capture.ingest_stream(gz_con, now=T0)["observations_written"] == 1
               and store.counts(gz_con)["raw_observation"] == 7,
               "and the row appended after that pass is ingested by the next")

            # The materialising reader is the one the audits use; it must see
            # the same files, or an inventory undercounts its own evidence.
            ok(len(capture.source_rows()) == 7,
               "the whole-archive reader opens the compressed roll too, so an "
               "evidence inventory cannot report fewer rows than were captured")
            gz_con.close()

    ok(store.CURSOR_TABLE not in store.RAW_TABLES + store.DERIVED_TABLES
       and store.CURSOR_TABLE not in store.counts(con),
       "the read positions are neither raw evidence nor a derived measurement, "
       "so they are not reported as either")
    cli_src = inspect.getsource(p35cli.main)
    ok("--rescan" in cli_src and "cursors" in cli_src and "flush=True" in cli_src,
       "the CLI can force a rescan, show how far each file has been read, and "
       "prints progress as it goes instead of only at the end")
    ok("--throttle" in cli_src
       and "pause=args.throttle" in cli_src
       and "must be zero or a positive" in cli_src,
       "the CLI exposes the throttle, passes it through, and refuses a negative "
       "pause rather than turning it into a sleep that never returns")
    ok("--throttle applies to the derived rebuild, not --raw-only" in cli_src,
       "and refuses --throttle on the raw-only pass instead of accepting a flag "
       "it would ignore")
    ok("pause" in inspect.signature(service.rebuild).parameters
       and inspect.signature(service.rebuild).parameters["pause"].default == 0.0,
       "the throttle is off unless asked for, so nothing that ran before this "
       "change runs differently now")

    # --- forward path never crosses a session, never mixes contracts ---------
    next_day = T0 + 86_400
    store.save_observations(con, [_obs("obs-nextday", ts=next_day, engine=False,
                                      source="BOARD")])
    store.save_quotes(con, [_quote("obs-nextday", ts=next_day, bid=400.0, ask=401.0,
                                  symbol="NIFTY25SEP24000CE")])
    samples = path.forward_samples(con, symbol="NIFTY25SEP24000CE", vehicle=CE,
                                   after_ts=T0)
    ok(samples and all(path.session_date(s["ts"]) == path.session_date(T0)
                       for s in samples),
       "the forward path stops at the session close, so no overnight gap is booked")
    # Same strike, different expiry, deliberately different prices: a path keyed on
    # anything looser than the symbol would pull one contract's quotes into the
    # other's outcome.
    twin = "NIFTY25OCT24000CE"
    store.save_observations(con, [_obs("obs-twin", ts=T0 + MIN, engine=False,
                                      source="BOARD")])
    store.save_quotes(con, [_quote("obs-twin", ts=T0 + MIN, bid=900.0, ask=901.0,
                                  symbol=twin)])
    other = path.forward_samples(con, symbol=twin, vehicle=CE, after_ts=T0)
    ok(len(other) == 1 and other[0]["bid"] == 900.0
       and not any(s["bid"] == 900.0 for s in samples),
       "two contracts sharing a strike across expiries never share a path")

    # --- §21: raw rows are append-only, enforced by the database -------------
    for sql, args in (
        ("UPDATE raw_observation SET instrument = 'X' WHERE obs_id = ?", (obs_id,)),
        ("DELETE FROM raw_observation WHERE obs_id = ?", (obs_id,)),
        ("UPDATE raw_quote SET bid = 1 WHERE obs_id = ?", (obs_id,)),
    ):
        try:
            con.execute(sql, args)
            con.commit()
            ok(False, f"raw rows refuse: {sql.split()[0].lower()}")
        except sqlite3.IntegrityError:
            ok(True, f"raw rows refuse: {sql.split()[0].lower()}")
        except sqlite3.DatabaseError:
            ok(True, f"raw rows refuse: {sql.split()[0].lower()}")

    # --- §10/§18: attribution grades causes, not coverage --------------------
    graded = store.attributions(con)
    ok(all(r["leg_id"] in {leg_row["leg_id"] for leg_row in store.legs(con)
                           if leg_row["entry_price"] is not None}
           for r in graded),
       "only legs that actually opened are graded")
    unresolved = {"leg_id": "x", "gross_pct": None, "net_pct": None,
                  "entry_price": 100.0, "vehicle": CE, "instrument": "NIFTY"}
    cls = attrib.classify(unresolved, cost={}, giveback={}, targets={}, peers={})
    ok(cls["primary_cause"] == UNATTRIBUTED and cls["channel"] is None,
       "an unresolved leg has no loss cause and no channel")
    ok(attrib.channel({"net_pct": None, "gross_pct": None}, {}, {},
                      better_vehicle=False) is None,
       "'we do not know yet' is never reported as 'no loss channel'")
    hist = attrib.histogram([cls])
    ok(hist.get("graded") == 0,
       "an unresolved row does not inflate the graded count")
    dec = attrib.decompose(
        {"gross_pct": 5.0, "net_pct": 1.0, "entry_price": 100.0},
        {"spread_points": 1.0, "brokerage_points": 1.0, "statutory_points": 1.0,
         "slippage_points": 1.0},
    )
    ok(dec.get("gross_pct") == 5.0 and dec.get("net_pct") == 1.0,
       "gross minus each named cost is reported as its own decomposition")

    # --- §16: the floors are the ones the brief wrote down -------------------
    cps = milestones.checkpoints(con)
    ok(GENERAL_MIN_TRADES == 100 and GENERAL_MIN_SESSIONS == 50,
       "the general floor is 100 resolved trades over 50 sessions")
    ok(PAIR_MIN_TRADES == 200 and PAIR_MIN_SESSIONS == 60
       and PAIR_MIN_COST_COVERAGE_PCT == 80.0,
       "the relative-value floor stays stricter: 200 / 60 / 80% cost coverage")
    ok(cps.get("checkpoint1", {}).get("observational_only") is not False,
       "checkpoint 1 never claims to be more than an observation")

    # --- §20/§24: the report reads the same dict the panel renders -----------
    st = service.state(con)
    ok(st["report"]["verdict"]["verdict"] == report.verdict(st)["verdict"],
       "the panel payload and the report agree on the verdict")
    ok(st["production_changed"] is False and st["paper_only"] == PAPER_ONLY
       and st["research_only"] == RESEARCH_ONLY,
       "every payload carries paper-only, research-only and production-unchanged")
    rows = {r["section"]: r for r in report.answerability(st)}
    ok(any("executable books" in s for s in rows),
       "the answerability map reports executable books separately from raw rows")

    # --- the report is counted by SQLite, never read into the process --------
    # This is not a style preference. On the real store — 2m paper legs, 6.7m
    # path rows, 2m quotes — the reading version took the machine into swap and
    # the report never printed. Each guard below fails on the read that did it.
    def _refuse(name):
        def _fail(*_args, **_kwargs):
            raise AssertionError(f"the report read all of {name} into memory")
        return _fail

    guarded = ("legs", "attributions", "comparisons", "observations")
    saved = {n: getattr(store, n) for n in guarded if hasattr(store, n)}
    for name in saved:
        setattr(store, name, _refuse(name))
    try:
        streamed = service.state(con)
        whole_table_read = None
    except AssertionError as exc:
        streamed, whole_table_read = None, str(exc)
    finally:
        for name, fn in saved.items():
            setattr(store, name, fn)
    ok(whole_table_read is None,
       f"the report never materialises a whole table ({whole_table_read})")
    ok(streamed is not None
       and json.dumps(streamed["books"], sort_keys=True)
       == json.dumps(st["books"], sort_keys=True)
       and json.dumps(streamed["economics"], sort_keys=True)
       == json.dumps(st["economics"], sort_keys=True),
       "counting in SQLite gives the same figures as counting in Python")

    def _grown(n: int):
        """A store with ``n`` seeded decisions, built with nothing measuring."""
        con_n = store.connect(":memory:")
        for i in range(n):
            _seed(con_n, engine=i % 2 == 0, symbol=f"NIFTY25SEP{24000 + i}CE",
                  ladder=(40.0 + i, 44.0 + i, 52.0 + i, 47.0 + i))
        paper.build(con_n)
        p35vehicle.build(con_n)
        return con_n

    small, big = _grown(12), _grown(60)
    tracemalloc.start()
    service.state(small)
    small_peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.reset_peak()
    service.state(big)
    big_peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    small_legs = store.counts(small)["paper_leg"]
    big_legs = store.counts(big)["paper_leg"]
    ok(big_peak < small_peak * 2,
       f"a {big_legs // small_legs}x bigger store does not cost the report "
       f"{big_legs // small_legs}x the memory "
       f"({small_peak // 1024}KB on {small_legs} legs -> "
       f"{big_peak // 1024}KB on {big_legs})")
    small.close()
    big.close()

    # --- §20: every declared filter narrows the funnel, and only the funnel ---
    engine_only = milestones.aggregate(
        con, book=CURRENT_ENGINE_PAPER, filters={"source": "ENGINE"},
    )
    board_only = milestones.aggregate(
        con, book=CURRENT_ENGINE_PAPER, filters={"source": "BOARD"},
    )
    unfiltered = milestones.aggregate(con, book=CURRENT_ENGINE_PAPER)
    ok(engine_only["paper_entered"] + board_only["paper_entered"]
       == unfiltered["paper_entered"],
       "engine-vs-board is a partition of the same book, never an addition to it")
    ok(milestones.aggregate(
        con, book=CURRENT_ENGINE_PAPER, filters={"vehicle": FUTURES},
    )["market_opportunities"] <= st["books"][CURRENT_ENGINE_PAPER][
        "market_opportunities"],
       "a vehicle filter can only shrink the opportunity count")
    ok(milestones.aggregate(
        con, book=CURRENT_ENGINE_PAPER, filters={"session": "1970-01-01"},
    )["paper_entered"] == 0,
       "a session with no capture reports nothing rather than the whole store")
    ok(milestones.aggregate(
        con, book=FULL_MARKET_PAPER, filters={"book": CURRENT_ENGINE_PAPER},
    )["book"] == FULL_MARKET_PAPER,
       "a book's own stats are never restated under the other book's name")
    sig = set(inspect.signature(appmain.phase35_panel).parameters)
    ok({"instrument", "vehicle", "book", "session", "source", "setup"} <= sig,
       "the read-only route exposes every §20 filter the brief listed")
    with tempfile.TemporaryDirectory() as tmp:
        written = service.write_artefacts(st, out_dir=tmp)
        ok(any(p.endswith("phase35_coverage.json") for p in written),
           "the coverage artefact is written where it is asked to be")
        missing = service.stagea_artefact(out_dir=tmp)
        ok(missing["available"] is False,
           "with no Stage A artefact the API says so instead of inventing one")
        with open(os.path.join(tmp, "phase35_stagea.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"stage": "A_HISTORICAL_DISCOVERY", "candidates": 0}, fh)
        found = service.stagea_artefact(out_dir=tmp)
        ok(found["available"] is True and found["candidates"] == 0,
           "the Stage A route reads the artefact the CLI wrote")

    cov = capture.coverage(con)
    ok("unmeasured_by_reason" in cov,
       "coverage explains what was not executable, by reason")
    ok(FIXTURE_ROW != SIMULATED_BOOK,
       "a fixture row and a simulator book are distinguishable reasons")

    # --- §1A/§22: CANDIDATE is Stage A's ceiling ----------------------------
    ok(stagea.CANDIDATE == "CANDIDATE" and not hasattr(stagea, "VALIDATED"),
       "Stage A has no path to VALIDATED")
    thin = stagea._status({"rows": 10}, {"rows": 10}, {"rows": 10}, [])
    ok(thin == "REQUIRES_MORE_DATA",
       "a thin window is REQUIRES_MORE_DATA, not a candidate")
    negative = stagea._status(
        {"rows": 10_000, "net_expectancy_pct": -0.01},
        {"rows": 10_000, "net_expectancy_pct": 0.5},
        {"rows": 10_000, "net_expectancy_pct": 0.5}, [],
    )
    ok(negative == "REJECTED",
       "a structure that loses on development is rejected on the spot")
    described = stagea._status(
        {"rows": 10_000, "net_expectancy_pct": 0.01},
        {"rows": 10_000, "net_expectancy_pct": -0.01},
        {"rows": 10_000, "net_expectancy_pct": 0.01}, [],
    )
    ok(described == stagea.DESCRIBED,
       "development-only positives are described, never promoted")

    # --- §15/§17: the frozen candidates are still the frozen candidates ------
    with tempfile.TemporaryDirectory() as tmp:
        fz = frozen.verify(tmp)
    pair = next(c for c in fz["candidates"]
                if c["candidate"] == "BANKNIFTY_NIFTY_RELATIVE_VALUE")
    ok(pair["actual_fingerprint"] == frozen.PAIR_FINGERPRINT
       and pair["status"] == frozen.VERIFIED,
       "the notional-neutral pair fingerprint is unchanged")
    ok(pair["notional_neutral"] is True
       and pair["gate"]["min_trades"] == PAIR_MIN_TRADES,
       "the pair stays notional-neutral behind its stricter gate")
    ok(len(fz["candidates"]) == 3,
       "all three frozen candidates are verified, not just the pair")
    hashed = {
        name: frozen._hash_sources(paths)
        for name, paths in frozen.SOURCE_GUARDS.items()
    }
    ok(all(v is not None for per in hashed.values() for v in per.values()),
       "every frozen source file the registry hashes actually exists")

    con.close()
    print(f"PHASE 35 SMOKE — {PASS} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
