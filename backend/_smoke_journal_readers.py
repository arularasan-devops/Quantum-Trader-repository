"""Journal-reader smoke — every consumer of a rolled journal sees the whole series.

This exists because of a defect that lost evidence silently. Journal compaction
gzips a rolled Phase 17 file, verifies it line for line, and removes the
original. Readers are supposed to go through :func:`store.series_paths` and
:func:`store.open_series`, which make the compressed and plain forms
indistinguishable. Two readers did not: the Phase 35 ingest and the Phase 31
evidence inventory each carried their own ``phase17_observations*.jsonl`` glob
and a plain ``open``. A compressed roll therefore stopped existing for them the
moment it was compressed — and, worse than an error, the ingest then reported
every file it could still see as complete, so the pass looked clean while a
third of a session's captured minutes sat in a ``.gz`` nothing opened.

Two kinds of check here, because fixing the two readers does not stop the third
one being written next month:

1. **Behavioural.** One journal's rows are split across a plain rolled file, a
   gzipped rolled file and the live file, and every canonical consumer is asked
   how many rows it sees. Anything that answers with less than the whole series
   is skipping evidence.
2. **Structural.** The source tree is scanned for code that names a Phase 17
   journal in a glob or opens one directly, outside the store that owns them.
   A reader that bypasses the accessor fails silently and reports success,
   which is exactly the failure mode that makes it worth detecting statically.

    .venv/bin/python _smoke_journal_readers.py
"""
from __future__ import annotations

import contextlib
import gzip
import json
import os
import re
import tempfile

# Before app.config is imported, so the real data directory is never touched:
# these checks write journals, and writing them beside the live ones would put
# fixture rows into the evidence this repository exists to keep clean.
os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="jreaders-"))

from app.research.phase17 import store as p17store  # noqa: E402
from app.research.phase31 import evidence as p31evidence  # noqa: E402
from app.research.phase35 import capture as p35capture  # noqa: E402
from app.research.phase35 import store as p35store  # noqa: E402
from app.research.phase37 import durable  # noqa: E402
from app.research.capture_health import coverage as caphealth  # noqa: E402

PASSED = 0
FAILED = 0

HERE = os.path.dirname(os.path.abspath(__file__))

# 2026-09-15 09:00 IST, inside an MCX session, so the rows land in one day.
T0 = 1789450200.0
MIN = 60.0

# Three rows in a plain rolled file, three in a gzipped rolled file, three live.
# The middle group is the whole point: it is the shape compaction leaves behind.
PLAIN_ROLL = 3
PACKED_ROLL = 3
LIVE = 3
TOTAL = PLAIN_ROLL + PACKED_ROLL + LIVE


def ok(cond: object, label: str) -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
        print(f"  FAIL  {label}")


@contextlib.contextmanager
def _journal_dir(directory: str):
    """Point every Phase 17 journal reader at a throwaway directory."""
    original = p17store.path
    p17store.path = lambda name: os.path.join(directory, name)
    p17store.drop_cache()
    try:
        yield directory
    finally:
        p17store.path = original
        p17store.drop_cache()


def _row(n: int, *, instrument: str = "CRUDEOIL") -> dict:
    """One observation as the capture writes it, with a two-sided book."""
    ts = T0 + n * MIN
    return {
        "observation_id": f"jr-{instrument}-{n}",
        "signal_ts": ts,
        "capture_ts": ts,
        "ts": ts,
        "instrument": instrument,
        "family": "MCX",
        "candidate_class": "BUY",
        "direction": "LONG",
        "selected_vehicle": "CE",
        "session": "2026-09-15",
        "market_signal_id": f"20260915-{instrument}-{n:04d}",
        "selected": {
            "source": "BOARD",
            "data_quality": "EXACT",
            "bid": 100.0 + n,
            "ask": 100.5 + n,
        },
        "plan": {"lot_size": 100},
    }


def _write_series(directory: str, name: str) -> dict[str, str]:
    """One journal's rows split across plain roll, gzipped roll and live file.

    Written in roll-stamp order, so the series is the same sequence of rows
    whichever form each file happens to be in.
    """
    base, ext = os.path.splitext(name)
    plain = os.path.join(directory, f"{base}.20260915-080000{ext}")
    packed = os.path.join(directory, f"{base}.20260915-090000{ext}{p17store.GZ}")
    live = os.path.join(directory, name)
    n = 0
    with open(plain, "w", encoding="utf-8") as fh:
        for _ in range(PLAIN_ROLL):
            fh.write(json.dumps(_row(n)) + "\n")
            n += 1
    with gzip.open(packed, "wt", encoding="utf-8") as fh:
        for _ in range(PACKED_ROLL):
            fh.write(json.dumps(_row(n)) + "\n")
            n += 1
    with open(live, "w", encoding="utf-8") as fh:
        for _ in range(LIVE):
            fh.write(json.dumps(_row(n)) + "\n")
            n += 1
    return {"plain": plain, "packed": packed, "live": live}


def _behavioural_checks() -> None:
    with tempfile.TemporaryDirectory() as directory:
        files = _write_series(directory, p17store.OBSERVATIONS)
        # The paper and leg journals matter too: the durable tally counts all
        # three, and a session reconstructed from two of them is short.
        _write_series(directory, p17store.PAPER)
        _write_series(directory, p17store.LEGS)

        with _journal_dir(directory):
            series = p17store.series_paths(p17store.OBSERVATIONS)
            ok(series == [files["plain"], files["packed"], files["live"]],
               "the series lists the plain roll, the compressed roll and the "
               "live file in roll order: a compressed file keeps the position "
               "its uncompressed twin held, so the rows stay in sequence")

            ok(len(p17store.read(p17store.OBSERVATIONS, tail_bytes=None)) == TOTAL,
               "the whole-series read returns every row across all three "
               "forms, which is what the offline audits are asking for when "
               "they pass tail_bytes=None")

            inv = p31evidence.captured_observations()
            ok(inv["files"] == 3 and inv["rows"] == TOTAL
               and inv["two_sided_at_decision"] == TOTAL,
               "the Phase 31 evidence inventory counts the compressed roll: "
               "this is the reader that under-reported its own evidence")

            obs_files = caphealth.observation_files()
            ok(len(obs_files) == 3
               and any(p.endswith(p17store.GZ) for p in obs_files),
               "capture-health reads the compressed roll, so captured minutes "
               "are counted from the whole series")

            tally = durable.tally()
            row = next((s for s in tally["sessions"]
                        if s["session"] == "2026-09-15"), None)
            ok(row is not None
               and row["observations"] == TOTAL
               and row["paper_legs"] == TOTAL
               and row["tracked_legs"] == TOTAL,
               "the durable session tally counts every journal across every "
               "form, so a day's evidence is not shortened by compaction")

            ok(len(p35capture.source_files()) == 3
               and any(p.endswith(p17store.GZ)
                       for p in p35capture.source_files()),
               "the Phase 35 ingest lists the compressed roll: the reader "
               "whose own glob made a gzipped file invisible")

            ok(len(p35capture.source_rows()) == TOTAL,
               "and its materialising reader returns every row, so an "
               "inventory cannot report fewer rows than were captured")

            con = p35store.connect(":memory:")
            first = p35capture.ingest_stream(con, now=T0)
            ok(first["source_rows"] == TOTAL
               and first["observations_written"] == TOTAL
               and p35store.counts(con)["raw_observation"] == TOTAL,
               "an ingest pass reaches the store with every row of the split "
               "series, compressed roll included")

            second = p35capture.ingest_stream(con, now=T0)
            ok(second["observations_written"] == 0
               and p35store.counts(con)["raw_observation"] == TOTAL,
               "a second pass writes nothing: reading a roll twice does not "
               "duplicate evidence, so the fix is safe to re-run")

            # The live file grows after a pass. Recording it as finished would
            # skip whatever the capture wrote next — the mirror image of the
            # bug this file is about.
            with open(files["live"], "a", encoding="utf-8") as fh:
                fh.write(json.dumps(_row(TOTAL)) + "\n")
            third = p35capture.ingest_stream(con, now=T0)
            ok(third["observations_written"] == 1
               and p35store.counts(con)["raw_observation"] == TOTAL + 1,
               "and a row appended to the live file after that pass is "
               "ingested by the next one")
            con.close()


# ---------------------------------------------------------------------------
# Structural: nothing outside the store may name a journal file itself.

# The modules allowed to name the journals directly, and why. Everything else
# must go through series_paths/open_series.
OWNERS = {
    # Defines the names, writes them, rolls them, and provides the accessors.
    os.path.join("app", "research", "phase17", "store.py"),
    # The compaction itself, which must open both forms to verify a rewrite.
    os.path.join("app", "research", "phase17", "compact.py"),
}

# A journal base name appearing inside a glob pattern or an open() call is the
# signature of a reader that has built its own file list.
_BASE_NAMES = ("phase17_observations", "phase17_legs", "phase17_paper",
               "phase17_coverage", "phase17_capture_gaps", "phase17_liveness",
               "phase17_process", "phase17_stalls")
_GLOB_CALL = re.compile(r"\b(?:glob\.(?:i?glob)|rglob|iglob)\s*\(")
_JOURNAL_LITERAL = re.compile(
    "|".join(re.escape(n) for n in _BASE_NAMES))


def _python_sources() -> list[str]:
    out: list[str] = []
    for root, dirs, names in os.walk(os.path.join(HERE, "app")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        out.extend(os.path.join(root, n) for n in sorted(names)
                   if n.endswith(".py"))
    out.extend(os.path.join(HERE, n) for n in sorted(os.listdir(HERE))
               if n.endswith(".py"))
    return out


def bypassing_readers() -> list[dict]:
    """Source lines that name a Phase 17 journal outside the store that owns it.

    Deliberately syntactic and deliberately narrow: a line is reported only if
    it names a journal *and* globs or opens, because the aim is to catch a
    reader building its own file list, not every mention of a filename.
    """
    found: list[dict] = []
    for fp in _python_sources():
        rel = os.path.relpath(fp, HERE)
        if rel in OWNERS or os.path.basename(rel).startswith("_smoke_"):
            continue
        try:
            with open(fp, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            code = line.split("#", 1)[0]
            if not _JOURNAL_LITERAL.search(code):
                continue
            if _GLOB_CALL.search(code) or re.search(r"\bopen\s*\(", code):
                found.append({"file": rel, "line": i, "code": line.strip()})
    return found


def _structural_checks() -> None:
    offenders = bypassing_readers()
    for row in offenders:
        print(f"  BYPASS  {row['file']}:{row['line']}  {row['code']}")
    ok(not offenders,
       "no module outside the Phase 17 store builds its own journal file list: "
       "a reader that globs *.jsonl stops seeing a roll the moment compaction "
       "gzips it, and reports success while doing so")

    # The guard has to be able to fail, or it is decoration. A synthetic
    # offending line must be recognised by the same matcher.
    sample = 'files = glob.glob("data/phase17_observations*.jsonl")'
    ok(_JOURNAL_LITERAL.search(sample) and _GLOB_CALL.search(sample),
       "and the detector recognises the exact pattern that caused the "
       "incident, so a clean result means something")

    src = open(os.path.join(HERE, "app", "research", "phase35", "capture.py"),
               encoding="utf-8").read()
    ok("open_series" in src and "series_paths" in src,
       "the Phase 35 ingest uses the canonical accessors")
    src31 = open(os.path.join(HERE, "app", "research", "phase31", "evidence.py"),
                 encoding="utf-8").read()
    ok("open_series" in src31 and "series_paths" in src31,
       "and so does the Phase 31 evidence inventory")


def main() -> int:
    _behavioural_checks()
    _structural_checks()
    print(f"JOURNAL READER SMOKE — {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
