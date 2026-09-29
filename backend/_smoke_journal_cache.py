"""The ledger readers must be fast without ever serving a stale ledger.

The boards read the journal several times per request and the files only grow, so
the readers keep their parse between calls. The whole risk of that is staleness,
so this checks the invalidation rather than the speed: a line appended a moment
ago has to appear, a half-written line must not be lost, and a replaced file must
never be answered out of the cache.

Read-only: it writes only to its own temporary ledgers and trades nothing.
"""
import json
import os
import tempfile
import time

from app.analysis import journal_stats as js
from app.analysis import signal_journal as sj

failures: list[str] = []
checked = 0


def check(cond: bool, msg: str) -> None:
    global checked
    checked += 1
    if not cond:
        failures.append(msg)


def write(path: str, text: str, mode: str = "a") -> None:
    with open(path, mode, encoding="utf-8") as fh:
        fh.write(text)
    # Two writes inside one filesystem timestamp tick would look like no change.
    time.sleep(0.01)


d = tempfile.mkdtemp(prefix="qt_ledger_cache_")
p = os.path.join(d, "ledger.jsonl")
line = lambda i: json.dumps({"i": i, "pad": "x" * 200}) + "\n"  # noqa: E731

write(p, "".join(line(i) for i in range(5)), "w")
check([r["i"] for r in sj._read(p)] == [0, 1, 2, 3, 4], "first read must be the file")

write(p, line(5))
check([r["i"] for r in sj._read(p)] == [0, 1, 2, 3, 4, 5],
      "an appended line must appear on the next read")

write(p, '{"i": 6')
check([r["i"] for r in sj._read(p)] == [0, 1, 2, 3, 4, 5],
      "a half-written line must not be read as a record")
write(p, ', "pad": "x"}\n')
check([r["i"] for r in sj._read(p)] == [0, 1, 2, 3, 4, 5, 6],
      "the completed line must be read once it is whole, not dropped")

write(p, "\nnot json\n" + line(7))
check([r["i"] for r in sj._read(p)] == [0, 1, 2, 3, 4, 5, 6, 7],
      "a blank or corrupt line must be skipped without losing the next record")

# A replaced file that is no smaller than the old one is the case a size/mtime
# check alone would get wrong.
write(p, "".join(json.dumps({"i": 100 + i, "pad": "y" * 400}) + "\n"
                 for i in range(10)), "w")
check([r["i"] for r in sj._read(p)] == list(range(100, 110)),
      "a replaced ledger must be reparsed, never served from the cache")

sj._read_cache.clear()
check([r["i"] for r in sj._read(p)] == list(range(100, 110)),
      "a cold read and a cached read must agree")

os.remove(p)
check(sj._read(p) == [], "a missing ledger reads as empty and drops its cache")
check(p not in sj._read_cache, "the cache must not keep a deleted file")

# history() pages from the newest row and stops there; the page it returns must
# be the same rows the whole-book scan would have ended with.
rows = sj._read(sj.journal_path())
if rows:
    full = js.history(10 ** 9, {})
    for limit in (1, 7, 60):
        page = js.history(limit, {})
        check([r["signal_id"] for r in page] == [r["signal_id"] for r in full[:limit]],
              f"the newest {limit} rows must match the full scan's newest {limit}")
    sessions = js.sessions_recorded()
    if sessions:
        day = sessions[-1]
        one = js.history(10 ** 9, {"session": day})
        check(all(r.get("session") == day for r in one),
              "a one-session board must contain only that session")
        check([r["signal_id"] for r in one]
              == [r["signal_id"] for r in full if r.get("session") == day][:len(one)],
              "filtering while reading must select the same rows as filtering after")
        check(isinstance(js.buy_calls_without_plan(session=day), int),
              "the without-plan count must be reportable for one session")

print(f"checked {checked}")
if failures:
    for f in failures:
        print("FAIL:", f)
    raise SystemExit(1)
print("journal cache smoke: OK")
