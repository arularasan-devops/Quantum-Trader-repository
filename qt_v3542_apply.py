#!/usr/bin/env python3
"""v3.5.42: archive old signal-ledger sessions at boot (fixes the 8 GB backend)."""
import os, py_compile, sys
B = "backend/app"
def rd(p): return open(p, encoding="utf-8").read()
def wr(p, s): open(p, "w", encoding="utf-8", newline="\n").write(s)
MOD = '"""Move past sessions out of the always-growing signal ledgers.\n\nThe journal readers keep every parsed row in memory, so a ledger that only ever\ngrows (938 MB of journal and 853 MB of lifecycle after ~2 months) became several\nGB of RAM and starved WSL. At boot, before anything appends, rows from sessions\nolder than ``QT_LEDGER_KEEP_DAYS`` are appended to a gzip archive under\n``data/archive/`` and the live file keeps only the recent sessions. Nothing is\ndeleted. The Journal/Reports tabs read PostgreSQL and are not affected; nothing\nhere is read by the strategy.\n"""\nfrom __future__ import annotations\n\nimport gzip\nimport json\nimport logging\nimport os\nfrom datetime import datetime, timedelta, timezone\n\nfrom app.config import settings\n\nlog = logging.getLogger(__name__)\n_IST = timezone(timedelta(hours=5, minutes=30))\nFILES = ("signal_journal.jsonl", "signal_outcomes.jsonl", "signal_lifecycle.jsonl")\n\n\ndef _cutoff(keep_days: int, now: float | None = None) -> str:\n    today = datetime.fromtimestamp(now, _IST) if now else datetime.now(_IST)\n    return (today - timedelta(days=keep_days - 1)).strftime("%Y-%m-%d")\n\n\ndef archive_file(name: str, cutoff: str) -> dict:\n    src = os.path.join(settings.data_dir, name)\n    if not os.path.exists(src):\n        return {"file": name, "moved": 0, "kept": 0}\n    os.makedirs(os.path.join(settings.data_dir, "archive"), exist_ok=True)\n    arc = os.path.join(settings.data_dir, "archive", name + ".gz")\n    tmp = src + ".keep.tmp"\n    moved = kept = 0\n    with open(src, "rb") as fh, open(tmp, "wb") as keep:\n        out = None\n        try:\n            for line in fh:\n                if not line.endswith(b"\\n"):\n                    line += b"\\n"\n                try:\n                    sess = str(json.loads(line).get("session") or "")\n                except Exception:\n                    sess = ""\n                if sess and sess < cutoff:\n                    if out is None:\n                        out = gzip.open(arc, "ab")\n                    out.write(line)\n                    moved += 1\n                else:\n                    keep.write(line)\n                    kept += 1\n        finally:\n            if out is not None:\n                out.close()\n        keep.flush()\n        os.fsync(keep.fileno())\n    if moved:\n        os.replace(tmp, src)\n    else:\n        os.remove(tmp)\n    return {"file": name, "moved": moved, "kept": kept}\n\n\ndef run(keep_days: int | None = None) -> list[dict]:\n    """Archive every ledger. Never fatal: a failure leaves the file as it was."""\n    days = settings.ledger_keep_days if keep_days is None else keep_days\n    if not days or days <= 0:\n        return []\n    cut = _cutoff(int(days))\n    out = []\n    for name in FILES:\n        try:\n            res = archive_file(name, cut)\n        except Exception as exc:\n            log.warning("ledger archive skipped for %s: %s", name, exc)\n            res = {"file": name, "error": str(exc)[:200]}\n        out.append(res)\n        if res.get("moved"):\n            log.info("ledger archive %s: moved %d rows before %s, kept %d",\n                     name, res["moved"], cut, res["kept"])\n    return out\n\n\nif __name__ == "__main__":\n    for r in run():\n        print(r)\n'
SMOKE = '"""v3.5.42 smoke: old ledger sessions move to a gzip archive; nothing lost."""\nimport gzip, json, os, sys, tempfile, time\nfrom datetime import datetime, timedelta, timezone\n\nd = tempfile.mkdtemp()\nos.environ["QT_DATA_DIR"] = d\nfrom app.config import settings\nsettings.data_dir = d\nfrom app.analysis import ledger_archive as la, signal_journal as sj\n\nok = True\ndef check(name, cond):\n    global ok\n    print(("PASS " if cond else "FAIL ") + name); ok &= bool(cond)\n\nIST = timezone(timedelta(hours=5, minutes=30))\nday = lambda n: (datetime.now(IST) - timedelta(days=n)).strftime("%Y-%m-%d")\nrows = [{"session": day(n), "i": i} for n in (40, 10, 3, 2, 1, 0) for i in range(5)]\nrows.append({"no_session": True})\nfor name in la.FILES:\n    with open(os.path.join(d, name), "w") as fh:\n        for r in rows:\n            fh.write(json.dumps(r) + "\\n")\n        fh.write("not json\\n")\ntotal = len(rows) + 1\nres = {r["file"]: r for r in la.run(keep_days=3)}\nr = res["signal_journal.jsonl"]\ncheck("old sessions moved (40d, 10d, 3d ago)", r["moved"] == 15)\ncheck("today, 1d, 2d ago + unknown rows kept", r["kept"] == 17)\nkept = [json.loads(l) for l in open(os.path.join(d, "signal_journal.jsonl")) if l.startswith("{")]\ncheck("live file has only recent sessions",\n      {x.get("session") for x in kept} == {day(0), day(1), day(2), None})\narc = [json.loads(l) for l in gzip.open(os.path.join(d, "archive", "signal_journal.jsonl.gz"))]\ncheck("archive holds the moved rows", len(arc) == 15 and min(x["session"] for x in arc) == day(40))\ncheck("nothing lost (kept + archived = original)", r["moved"] + r["kept"] == total)\nsj.reset_for_tests()\ncheck("journal reader sees only recent rows", len(sj._read(sj.journal_path())) == len(kept))\nagain = {r["file"]: r for r in la.run(keep_days=3)}\ncheck("rerun is a no-op", again["signal_journal.jsonl"]["moved"] == 0)\ncheck("all 3 ledgers archived", all(res[f]["moved"] == 15 for f in la.FILES))\ncheck("QT_LEDGER_KEEP_DAYS=0 disables", la.run(keep_days=0) == [])\ncheck("default keep is 3 days", settings.ledger_keep_days == 3)\nprint("ALL PASS" if ok else "SOME FAILED")\nsys.exit(0 if ok else 1)\n'
cfg, main = rd(f"{B}/config.py"), rd(f"{B}/main.py")
if "ledger_keep_days" in cfg or os.path.exists(f"{B}/analysis/ledger_archive.py"):
    sys.exit("v3.5.42 already applied - nothing changed")
if "auto_trade_metal_mini" not in cfg:
    sys.exit("ABORT: v3.5.41 is not applied here")
A1 = "    signal_lifecycle_log: bool = True\n"
A2 = """    if settings.risk_rebuild_from_journal:
        try:
            from app.engine import account_risk"""
for name, s, a in (("config.py", cfg, A1), ("main.py", main, A2)):
    if s.count(a) != 1:
        sys.exit(f"ABORT: anchor not found in {name} - nothing changed")
cfg = cfg.replace(A1, A1 + "    # Sessions kept in the live signal ledgers; older rows move to\n"
                  "    # data/archive/*.gz at boot (nothing deleted). 0 = never archive.\n"
                  "    ledger_keep_days: int = 3\n", 1)
main = main.replace(A2, """    # Before any tick appends to the ledgers: move old sessions to the archive so
    # the readers' in-memory caches hold days, not months.
    from app.analysis import ledger_archive

    await asyncio.get_running_loop().run_in_executor(None, ledger_archive.run)

""" + A2, 1)
wr(f"{B}/config.py", cfg); wr(f"{B}/main.py", main)
wr(f"{B}/analysis/ledger_archive.py", MOD)
wr("backend/_smoke_v3542.py", SMOKE)
for f in ("config.py", "main.py", "analysis/ledger_archive.py"):
    py_compile.compile(f"{B}/{f}", doraise=True)
print("v3.5.42 applied OK")
