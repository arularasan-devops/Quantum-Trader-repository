"""Phase 31 §1 — what premium evidence exists, before any statistic is computed.

The whole phase turns on one count: how many recorded calls have a *real*
two-sided premium at the decision instant and a later real premium to measure the
move against. This module reports that count and the provenance of everything it
looked at, so a premium percentage can never be quoted off simulated books by
accident — which is exactly how an earlier audit was misled.
"""
from __future__ import annotations

import datetime as dt
import json
import os

from app.research.phase17 import store as p17store
from app.research.phase24 import data

CHAIN_TABLE = "chain_snapshots"

# Provenance labels the store writes. Rows predating the label are UNKNOWN, and
# UNKNOWN is treated as unusable rather than assumed real.
REAL = "REAL_BROKER"
UNKNOWN = "UNKNOWN"

# A premium-percentage distribution needs more than a handful of paths before it
# describes anything. Below this the option half stays UNMEASURED.
MIN_REAL_PATH_INSTANTS = 500


def _resolve(rel: str) -> str:
    return data._resolve(rel)


def _iso(ts: int) -> str:
    """Epoch second as an IST calendar date, the timezone the data was taken in."""
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    return dt.datetime.fromtimestamp(ts, ist).strftime("%Y-%m-%d %H:%M")


def option_books() -> dict:
    """Provenance of every stored option-chain snapshot."""
    path = _resolve(data.HISTORY_DB)
    out: dict = {
        "store": os.path.basename(path),
        "present": os.path.exists(path),
        "snapshots": 0,
        "by_source": {},
        "real_broker_snapshots": 0,
        "sessions": 0,
        "legs_sampled": 0,
        "legs_with_two_sided_quote": 0,
    }
    if not out["present"]:
        return out
    con = data.connect_readonly(path)
    try:
        out["snapshots"] = int(
            con.execute(f"select count(*) from {CHAIN_TABLE}").fetchone()[0]
        )
        for src, n in con.execute(
            f"select source, count(*) from {CHAIN_TABLE} group by 1"
        ):
            out["by_source"][src or UNKNOWN] = int(n)
        out["real_broker_snapshots"] = int(out["by_source"].get(REAL, 0))
        out["sessions"] = int(con.execute(
            f"select count(distinct date(ts,'unixepoch','+5 hours','+30 minutes'))"
            f" from {CHAIN_TABLE}"
        ).fetchone()[0])
        legs = two_sided = 0
        for (payload,) in con.execute(
            f"select payload from {CHAIN_TABLE} order by ts limit 4000"
        ):
            try:
                rows = json.loads(payload)
            except json.JSONDecodeError:
                continue
            for leg in rows if isinstance(rows, list) else []:
                legs += 1
                if leg.get("bid") is not None and leg.get("ask") is not None:
                    two_sided += 1
        out["legs_sampled"] = legs
        out["legs_with_two_sided_quote"] = two_sided
    finally:
        con.close()
    return out


def captured_observations() -> dict:
    """Exact-timestamp captures: how many carry a real book, how many a path."""
    # The series helper, not a glob: a glob of ``*.jsonl`` silently stops
    # matching a roll once the journal compaction gzips it, and an inventory
    # that undercounts its own evidence is worse than no inventory.
    files = [p for p in p17store.series_paths(p17store.OBSERVATIONS)
             if os.path.exists(p)]
    out: dict = {
        "files": len(files),
        "rows": 0,
        "unparsable": 0,
        "by_source": {},
        "by_data_quality": {},
        "two_sided_at_decision": 0,
        "tracked_paths": 0,
    }
    for fp in files:
        with p17store.open_series(fp) as fh:
            for raw in fh:
                line = raw.decode("utf-8", "ignore").strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    out["unparsable"] += 1
                    continue
                out["rows"] += 1
                sel = r.get("selected") or {}
                src = sel.get("source") or UNKNOWN
                out["by_source"][src] = out["by_source"].get(src, 0) + 1
                q = sel.get("data_quality") or UNKNOWN
                out["by_data_quality"][q] = out["by_data_quality"].get(q, 0) + 1
                if sel.get("bid") is not None and sel.get("ask") is not None:
                    out["two_sided_at_decision"] += 1
                if r.get("tracking") == "TRACKED" or isinstance(
                    r.get("tracking"), dict
                ):
                    out["tracked_paths"] += 1
    return out


def underlying_series() -> dict:
    """The five-year one-minute series the measured half of the phase runs on."""
    rows = {}
    for inst in sorted(data.BACKTEST_FILES):
        s = data.load_series(inst)
        if s is None:
            rows[inst] = {"present": False}
            continue
        rows[inst] = {
            "present": True,
            "bars": len(s),
            "first_ts": int(s.ts[0]),
            "last_ts": int(s.ts[-1]),
            "first": _iso(int(s.ts[0])),
            "last": _iso(int(s.ts[-1])),
        }
    return rows


def inventory() -> dict:
    """Everything the phase is allowed to conclude from, and what it is not."""
    books = option_books()
    obs = captured_observations()
    real_instants = books["real_broker_snapshots"] + obs["two_sided_at_decision"]
    return {
        "underlying": underlying_series(),
        "option_books": books,
        "captured_observations": obs,
        "real_two_sided_decision_instants": real_instants,
        "min_required_for_premium_distribution": MIN_REAL_PATH_INSTANTS,
        "premium_percentage_measurable": real_instants >= MIN_REAL_PATH_INSTANTS,
        "note": (
            "stored option chains are provenance-labelled; snapshots labelled "
            "SIMULATOR or predating the label are not evidence of a premium "
            "move, so a premium percentage is only quoted from real recorded "
            "fills or carries the ASSUMED_PREMIUM_MAP label"
        ),
    }
