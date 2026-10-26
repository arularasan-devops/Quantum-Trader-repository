"""Scrip master: today's saved copy skips the download; a slow/failed download
falls back to an older saved copy; with no copy the download is waited out."""
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(__file__))

from app.market import angelone as a  # noqa: E402

ROWS = [{"token": str(i), "symbol": f"S{i}"} for i in range(1500)]
RAW = json.dumps(ROWS).encode()
calls = {"n": 0}
mode = {"m": "ok"}


class _Resp:
    def raise_for_status(self):
        if mode["m"] == "http_error":
            raise RuntimeError("503")

    def iter_bytes(self):
        for i in range(0, len(RAW), 4096):
            if mode["m"] == "slow":
                time.sleep(0.02)
            yield RAW[i:i + 4096]


@contextmanager
def _stream(method, url, timeout):
    calls["n"] += 1
    yield _Resp()

a.httpx.stream = _stream
fails = 0


def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    fails += 0 if cond else 1


path = a._scrip_cache_path()
check("no saved copy: downloads and returns rows", len(a._load_scrip_master()) == 1500 and calls["n"] == 1)
check("download saved a local copy", os.path.exists(path))
check("today's copy is reused with no download", len(a._load_scrip_master()) == 1500 and calls["n"] == 1)

old = time.time() - 2 * 86400
os.utime(path, (old, old))
mode["m"] = "http_error"
check("old copy + failed download -> old copy used", len(a._load_scrip_master()) == 1500 and calls["n"] == 2)

a._SCRIP_DOWNLOAD_DEADLINE_SEC = 0.05
mode["m"] = "slow"
check("old copy + slow download -> deadline hit, old copy used", len(a._load_scrip_master()) == 1500)
check("old copy not overwritten by a partial download", abs(os.path.getmtime(path) - old) < 5)

mode["m"] = "ok"
check("old copy + good download -> refreshed copy", len(a._load_scrip_master()) == 1500 and os.path.getmtime(path) > old + 3600)

os.remove(path)
mode["m"] = "slow"
check("no copy: deadline not applied, slow download still completes", len(a._load_scrip_master()) == 1500)

os.remove(path)
mode["m"] = "http_error"
try:
    a._load_scrip_master()
    check("no copy + failed download raises (no invented contracts)", False)
except Exception:
    check("no copy + failed download raises (no invented contracts)", True)

with open(path, "w") as fh:
    fh.write("[1,2")
mode["m"] = "ok"
check("corrupt copy is ignored and re-downloaded", len(a._load_scrip_master()) == 1500)
print(f"{10 - fails} passed, {fails} failed")
sys.exit(1 if fails else 0)
