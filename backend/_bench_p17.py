"""Time the Phase 17 read endpoints against the evidence files.

Point QT_DATA_DIR at the directory to measure (it defaults to the configured
one) and run this after any change to the read path. The field failure this
guards against: an 840 MB observations file made every one of these endpoints
exceed a 10 second client timeout, so the panels sat on "Loading…" forever.
"""
from __future__ import annotations

import os
import time

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.research.phase17 import store

# Measure the read path itself rather than the size roll that would otherwise
# rescue it on the first write.
store.ROLL_AT_BYTES = 1 << 60

ENDPOINTS = ("health", "board", "evidence?limit=60", "paper", "summary", "journal")

obs = os.path.join(settings.data_dir, store.OBSERVATIONS)
size = os.path.getsize(obs) if os.path.exists(obs) else 0
print(f"{settings.data_dir}: observations {size / 1e6:.0f} MB")

with TestClient(app) as client:
    for label in ("cold", "warm"):
        print(f"-- {label}")
        for ep in ENDPOINTS:
            t0 = time.time()
            res = client.get(f"/api/phase17/{ep}")
            print(f"  {ep:20s} {res.status_code} {time.time() - t0:6.2f}s")
