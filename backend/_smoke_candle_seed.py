"""Seeding Angel futures candles from history.db (no broker, no network)."""
# ruff: noqa
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import time, types
from app import storage
from app.models import Candle
from app.market.angelone import AngelOneProvider
from app.market.instruments import get_spec
now = int(time.time()) // 60 * 60
bars = [Candle(time=now - 60 * i, open=1, high=2, low=0.5, close=1.5, volume=10) for i in range(120, 0, -1)]
asked = []
class Fake:
    def __init__(self, rows): self.rows = rows
    def candles(self, key, limit=600): asked.append(key); return self.rows
def mk(spec):
    p = object.__new__(AngelOneProvider)
    p._root = spec.symbol; p._fut_candles = []; p._fut_candles_cached_at = 5.0; p._candle_diag = "x"
    from app.market.instruments import REGISTRY
    p._store_key = next((k for k, v in REGISTRY.items() if v is spec), p._root)
    return p
ok = True
def check(n, c):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n)
storage.store = Fake(bars); p = mk(get_spec("BAJAJAUTO")); p._seed_candles_from_disk()
check("seeds 120 fresh bars", len(p._fut_candles) == 120 and p._candle_diag == "")
check("uses registry key BAJAJAUTO", asked[-1] == "BAJAJAUTO")
check("cached_at=0 so next REST refresh replaces seed", p._fut_candles_cached_at == 0.0)
storage.store = Fake(bars[:30]); p = mk(get_spec("CRUDEOIL")); p._seed_candles_from_disk()
check("too few bars -> no seed", p._fut_candles == [])
old = [Candle(time=c.time - 10 * 86400, open=1, high=1, low=1, close=1, volume=1) for c in bars]
storage.store = Fake(old); p = mk(get_spec("CRUDEOIL")); p._seed_candles_from_disk()
check("stale (10 days) -> no seed", p._fut_candles == [])
class Boom:
    def candles(self, *a, **k): raise RuntimeError("db locked")
storage.store = Boom(); p = mk(get_spec("CRUDEOIL")); p._seed_candles_from_disk()
check("broken store -> no crash, no seed", p._fut_candles == [])
print("ALL PASSED" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
