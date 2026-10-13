"""Smoke: research store schema versioning + /api/db/status (Phase B).

Runs against SQLite by default. If QT_DB_URL is a PostgreSQL URL (e.g. inside
`docker compose run backend`), the same checks run against PostgreSQL and also
verify a trade row survives a fresh connection.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid

if not os.environ.get("QT_DATA_DIR"):
    os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_db_smoke_")

from fastapi.testclient import TestClient  # noqa: E402

from app.research import store as store_mod  # noqa: E402
from app.research.db import Database  # noqa: E402

passed = failed = 0
fails: list[str] = []


def ok(cond: bool, msg: str) -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS: {msg}")
    else:
        failed += 1
        fails.append(msg)
        print(f"FAIL: {msg}")


s1 = store_mod.ResearchStore()
expected_backend = "postgresql" if s1.db.is_postgres else "sqlite"
ok(s1.backend == expected_backend, f"backend reported as {expected_backend}")
ok(s1.schema_version() == store_mod.STORE_SCHEMA_VERSION,
   "schema_migrations records the current schema version")
s2 = store_mod.ResearchStore()
ok(s2.schema_version() == store_mod.STORE_SCHEMA_VERSION,
   "re-initialising is idempotent (no duplicate-version error)")
st = s2.status()
ok(set(st["row_counts"]) >= {"trade_history", "signal_snapshots", "shadow_log"},
   "status() reports row counts for the core tables")

marker = f"SMOKE-{uuid.uuid4().hex[:8]}"
tid = s1.insert_trade({"instrument": marker, "source": "paper_live",
                       "entry_ts": 1, "exit_ts": 2, "entry": 10.0, "exit": 11.0,
                       "pnl": 100.0, "win": True})
fresh = store_mod.ResearchStore(Database())
rows = fresh.trades(marker)
ok(len(rows) == 1 and rows[0]["source"] == "paper_live",
   "a trade written on one connection is readable from a fresh connection")
fresh.db.execute("DELETE FROM trade_history WHERE id=?", (tid,))

from app.main import app  # noqa: E402

store_mod._store = None
with TestClient(app) as client:
    r = client.get("/api/db/status")
    ok(r.status_code == 200, "/api/db/status returns 200")
    body = r.json()
    ok(body.get("backend") == expected_backend, "/api/db/status names the active backend")
    ok(body.get("schema_version") == store_mod.STORE_SCHEMA_VERSION,
       "/api/db/status reports the schema version")
    ok("postgresql://" not in r.text and "password" not in r.text.lower(),
       "/api/db/status never leaks the connection URL")

print(f"\nDB STATUS SMOKE — {passed} passed, {failed} failed")
for f in fails:
    print(f"  - {f}")
sys.exit(1 if failed else 0)
