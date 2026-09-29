"""Read-path smoke for the Phase 17 evidence store.

The panels re-read these files every 20 seconds. A 784 MB observations file made
every endpoint time out, so the read path is now incremental and bounded. What
must hold, and is checked below:

  - a second read parses only the appended bytes and still returns every row;
  - replacing or truncating the file invalidates the cache instead of serving
    rows that no longer exist;
  - a file larger than the tail bound returns the recent rows and REPORTS that
    it is incomplete, so no table can present a partial read as a full day;
  - tail_bytes=None returns every row, because the offline audits must not be
    silently truncated;
  - a half-written last line costs that line, not the session.
"""
from __future__ import annotations

import json
import os
import tempfile
import time

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="p17store-"))

from app.research.phase17 import compact, store  # noqa: E402

FAIL: list[str] = []


def check(cond: bool, what: str) -> None:
    if not cond:
        FAIL.append(what)


def write(name: str, rows: list[dict], *, mode: str = "a") -> None:
    with open(store.path(name), mode, encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


NAME = "read_smoke.jsonl"
p = store.path(NAME)
store.drop_cache()
if os.path.exists(p):
    os.remove(p)

# absent file
check(store.read(NAME) == [], "absent file must read as no rows")

# first read, then an append: the second read must see both without re-parsing
write(NAME, [{"i": i} for i in range(10)])
first = store.read(NAME)
check([r["i"] for r in first] == list(range(10)), "first read returns all rows")
write(NAME, [{"i": i} for i in range(10, 15)])
second = store.read(NAME)
check([r["i"] for r in second] == list(range(15)), "append is picked up in order")
check(store.read_meta(NAME)["complete"] is True, "small file reads complete")

# limit trims the tail only
check([r["i"] for r in store.read(NAME, limit=3)] == [12, 13, 14], "limit keeps latest")

# truncation must not serve rows that are gone
write(NAME, [{"i": 99}], mode="w")
check([r["i"] for r in store.read(NAME)] == [99], "truncated file drops stale rows")

# replacement (new inode) must invalidate too
os.remove(p)
write(NAME, [{"i": 7}])
check([r["i"] for r in store.read(NAME)] == [7], "replaced file drops stale rows")

# a bounded read of a file past the bound: recent rows, and says it is partial
store.drop_cache()
write(NAME, [{"i": i, "pad": "x" * 200} for i in range(2_000)], mode="w")
size = os.path.getsize(p)
bounded = store.read(NAME, tail_bytes=size // 4)
meta = store.read_meta(NAME)
check(0 < len(bounded) < 2_000, "bounded read returns a subset")
check(meta["complete"] is False, "bounded read must report itself incomplete")
check(bounded[-1]["i"] == 1_999, "bounded read keeps the most recent row")

# the audit path must get everything
store.drop_cache()
full = store.read(NAME, tail_bytes=None)
check(len(full) == 2_000, f"full read returns every row, got {len(full)}")
check(full[0]["i"] == 0 and full[-1]["i"] == 1_999, "full read is in order")

# a half-written last line costs one row, not the file
store.drop_cache()
with open(p, "a", encoding="utf-8") as fh:
    fh.write('{"i": 2000, "pa')
check(len(store.read(NAME, tail_bytes=None)) == 2_000, "partial last line is skipped")
h = store.health()
check(
    isinstance(h.get("last_error"), str) and "unparseable" in h["last_error"],
    "the skipped line is reported in health, not hidden",
)

# a rolled/parked file is still evidence: the audit read must include it
store.drop_cache()
write(NAME, [{"i": i} for i in range(5)], mode="w")
os.rename(p, store.path("read_smoke.20260101-090000.jsonl"))
write(NAME, [{"i": i} for i in range(5, 9)], mode="w")
series = store.read(NAME, tail_bytes=None)
check([r["i"] for r in series] == list(range(9)), "audit read spans rolled files")
check(len(store.series_paths(NAME)) == 2, "series lists rolled + live")
check(
    [r["i"] for r in store.read(NAME)] == [5, 6, 7, 8],
    "the live read stays on the live file only",
)
os.remove(store.path("read_smoke.20260101-090000.jsonl"))

# compaction: a rolled file stored gzipped is still read, in the same place in
# the series, with every row — the disk saving must not shorten the record.
store.drop_cache()
write(NAME, [{"i": i} for i in range(5)], mode="w")
rolled = store.path("read_smoke.20260101-090000.jsonl")
os.rename(p, rolled)
write(NAME, [{"i": i} for i in range(5, 9)], mode="w")
packed = compact.run(names=(NAME,), apply=True)
check(packed["files_compressed"] == 0 and packed["files_eligible"] == 0,
      "a rolled file under MIN_BYTES is left alone: the saving is noise and "
      "each rewrite is a chance to lose a row")

compact.MIN_BYTES = 0
plan = compact.run(names=(NAME,))
check(plan["applied"] is False and not os.path.exists(rolled + ".gz"),
      "the default is a dry run that writes nothing")
check(
    [f["skipped_because"] for f in plan["files"] if f["path"] == p]
    == [compact.SKIP_LIVE],
    "the live file is never a candidate — it is still being appended to",
)
done = compact.run(names=(NAME,), apply=True)
check(done["files_compressed"] == 1 and done["lines_verified"] == 5,
      f"the rolled file is compressed and every line verified: {done}")
check(os.path.exists(rolled + ".gz") and not os.path.exists(rolled),
      "the original goes only after the gzip verified line-for-line")
check(os.path.exists(p), "and the live file is untouched")
store.drop_cache()
check([r["i"] for r in store.read(NAME, tail_bytes=None)] == list(range(9)),
      "the audit read spans the compressed file: no row and no order lost")
check(store.series_paths(NAME) == [rolled + ".gz", p],
      "and it keeps the position its uncompressed twin had")
os.remove(rolled + ".gz")
compact.MIN_BYTES = 1 * 1024 * 1024

# the cache must not make a growing file slow: 200k rows read twice
store.drop_cache()
big = "big_smoke.jsonl"
write(big, [{"i": i, "pad": "y" * 300} for i in range(50_000)], mode="w")
t0 = time.time()
store.read(big)
cold = time.time() - t0
write(big, [{"i": 50_000}])
t0 = time.time()
store.read(big)
warm = time.time() - t0
check(warm < max(0.05, cold), f"incremental read must be faster: {warm=} {cold=}")
os.remove(store.path(big))
os.remove(p)

if FAIL:
    for f in FAIL:
        print("FAIL:", f)
    raise SystemExit(1)
print("store read smoke: OK")
