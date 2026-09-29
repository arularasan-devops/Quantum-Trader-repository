"""Read-side equivalence check: the SQL report against the materialising one.

The report sections were rewritten to aggregate in SQLite instead of reading
``paper_leg``, ``leg_path``, ``leg_attribution`` and ``raw_quote`` into memory.
This loads the previous implementations out of the last shipped overlay and
asserts, on a store built by the smoke fixture, that every figure is identical.

Run: .venv/bin/python _equiv_report.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _smoke_phase35 as smoke  # noqa: E402
from app.research.phase35 import (  # noqa: E402
    CURRENT_ENGINE_PAPER,
    FULL_MARKET_PAPER,
    attrib,
    filters,
    milestones,
    missed,
    paper,
    store,
    vehicle,
)

OVERLAY = "/home/ubuntu/qt_crudeoil_vehicle.zip"
OLD = ["milestones", "missed", "filters", "attrib", "vehicle"]


def _load_old(tmp: str) -> dict:
    """Import the shipped copies of the rewritten modules under other names."""
    with zipfile.ZipFile(OVERLAY) as z:
        for name in OLD:
            member = f"backend/app/research/phase35/{name}.py"
            with z.open(member) as src, open(
                os.path.join(tmp, f"old_{name}.py"), "wb"
            ) as dst:
                dst.write(src.read())
    mods = {}
    for name in OLD:
        spec = importlib.util.spec_from_file_location(
            f"old_{name}", os.path.join(tmp, f"old_{name}.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods


DAY = 24 * 60 * 60


def _triple(con, day: int, n: int, base: float, vol: float) -> None:
    """FUTURES/CE/PE quoted at one instant, plus the forward path for each."""
    ts = smoke.T0 + day * DAY + n * 60 * smoke.MIN
    obs_id = f"obs-d{day}-{n}"
    rows = [smoke._obs(obs_id, ts=ts, engine=(n % 2 == 0),
                       source="ENGINE" if n % 2 == 0 else "BOARD")]
    quotes = []
    legs = (("CE", base, 1.0), ("PE", base * 0.8, 0.6), ("FUTURES", 24000.0, 4.0))
    for veh, px, spread in legs:
        q = smoke._quote(obs_id, ts=ts, bid=px - spread, ask=px, vehicle=veh,
                         symbol=f"NIFTY-{veh}-{n}")
        q["volume"] = vol
        quotes.append(q)
    for i in range(1, 5):
        fid = f"{obs_id}-f{i}"
        fts = ts + i * 10 * smoke.MIN
        rows.append(smoke._obs(fid, ts=fts, engine=False, source="BOARD"))
        for veh, px, spread in legs:
            drift = 1.0 + (0.04 * i if n % 3 else -0.03 * i)
            q = smoke._quote(fid, ts=fts, bid=px * drift, ask=px * drift + spread,
                             vehicle=veh, symbol=f"NIFTY-{veh}-{n}")
            q["volume"] = vol
            quotes.append(q)
    store.save_observations(con, rows)
    store.save_quotes(con, quotes)


def _fixture():
    con = store.connect(":memory:")
    smoke._seed(con)
    smoke._seed(con, engine=False, symbol="NIFTY25SEP24100CE",
                ladder=(50.0, 44.0, 40.0, 41.0))
    smoke._seed(con, engine=True, symbol="NIFTY25SEP23900PE",
                ladder=(80.0, 92.0, 88.0, 95.0))
    smoke._seed(con, engine=False, symbol="NIFTY25SEP24200CE", lot=None,
                ladder=(12.0, 13.0, 11.0, 14.0))
    # Several sessions, both books, band-spanning premiums and volumes, and
    # same-instant triples so the vehicle comparison is not empty.
    for day in range(4):
        for n in range(6):
            _triple(con, day, n, base=[8.0, 40.0, 120.0, 260.0, 700.0, 30.0][n],
                    vol=float(100 * (n + 1) ** 3))
    paper.build(con)
    vehicle.build(con)
    return con


def main() -> int:
    con = _fixture()
    counts = store.counts(con)
    print("fixture:", json.dumps(
        {k: v for k, v in counts.items() if not isinstance(v, dict)}))
    with tempfile.TemporaryDirectory() as tmp:
        old = _load_old(tmp)
        cases = []
        for book in (CURRENT_ENGINE_PAPER, FULL_MARKET_PAPER):
            cases.append((
                f"aggregate[{book}]",
                old["milestones"].aggregate(con, book=book),
                milestones.aggregate(con, book=book),
            ))
            sessions = [r[0] for r in con.execute(
                "SELECT DISTINCT session FROM raw_observation"
                " WHERE session IS NOT NULL AND session <> '' ORDER BY session")]
            probes = [{"vehicle": "CE"}, {"vehicle": "FUTURES"},
                      {"source": "BOARD"}, {"source": "ENGINE"},
                      {"setup": "DIRECTIONAL"}, {"instrument": "NIFTY"},
                      {"instrument": "ABSENT"}]
            probes += [{"session": s} for s in sessions]
            probes.append({"session": sessions[0], "vehicle": "PE"})
            for f in probes:
                cases.append((
                    f"aggregate[{book}]{f}",
                    old["milestones"].aggregate(con, book=book, filters=f),
                    milestones.aggregate(con, book=book, filters=f),
                ))
        cases += [
            ("checkpoints", old["milestones"].checkpoints(con),
             milestones.checkpoints(con)),
            ("cost_coverage", old["milestones"].cost_coverage_pct(
                con, book=FULL_MARKET_PAPER),
             milestones.cost_coverage_pct(con, book=FULL_MARKET_PAPER)),
            ("missed.rows", old["missed"].rows(con), missed.rows(con)),
            ("missed.summary", old["missed"].summary(con), missed.summary(con)),
            ("economics", old["filters"].study(con, book=FULL_MARKET_PAPER),
             filters.study(con, book=FULL_MARKET_PAPER)),
            ("economics[both]", old["filters"].study(con), filters.study(con)),
            ("vehicle.summary", old["vehicle"].summary(con),
             vehicle.summary(con)),
            ("attribution", old["attrib"].histogram(store.attributions(con)),
             attrib.histogram_from_store(con)),
        ]
    failed = 0
    for name, before, after in cases:
        same = json.dumps(before, sort_keys=True, default=str) == json.dumps(
            after, sort_keys=True, default=str)
        if not same:
            failed += 1
            print(f"DIFFERS  {name}")
            print("  before:", json.dumps(before, default=str)[:1200])
            print("  after :", json.dumps(after, default=str)[:1200])
        else:
            print(f"same     {name}")
    print(f"\n{len(cases) - failed} identical, {failed} differing")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
