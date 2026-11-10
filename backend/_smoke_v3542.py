"""v3.5.42 smoke: old ledger sessions move to a gzip archive; nothing lost."""
import gzip, json, os, sys, tempfile, time
from datetime import datetime, timedelta, timezone

d = tempfile.mkdtemp()
os.environ["QT_DATA_DIR"] = d
from app.config import settings
settings.data_dir = d
from app.analysis import ledger_archive as la, signal_journal as sj

ok = True
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok &= bool(cond)

IST = timezone(timedelta(hours=5, minutes=30))
day = lambda n: (datetime.now(IST) - timedelta(days=n)).strftime("%Y-%m-%d")
rows = [{"session": day(n), "i": i} for n in (40, 10, 3, 2, 1, 0) for i in range(5)]
rows.append({"no_session": True})
for name in la.FILES:
    with open(os.path.join(d, name), "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
        fh.write("not json\n")
total = len(rows) + 1
res = {r["file"]: r for r in la.run(keep_days=3)}
r = res["signal_journal.jsonl"]
check("old sessions moved (40d, 10d, 3d ago)", r["moved"] == 15)
check("today, 1d, 2d ago + unknown rows kept", r["kept"] == 17)
kept = [json.loads(l) for l in open(os.path.join(d, "signal_journal.jsonl")) if l.startswith("{")]
check("live file has only recent sessions",
      {x.get("session") for x in kept} == {day(0), day(1), day(2), None})
arc = [json.loads(l) for l in gzip.open(os.path.join(d, "archive", "signal_journal.jsonl.gz"))]
check("archive holds the moved rows", len(arc) == 15 and min(x["session"] for x in arc) == day(40))
check("nothing lost (kept + archived = original)", r["moved"] + r["kept"] == total)
sj.reset_for_tests()
check("journal reader sees only recent rows", len(sj._read(sj.journal_path())) == len(kept))
again = {r["file"]: r for r in la.run(keep_days=3)}
check("rerun is a no-op", again["signal_journal.jsonl"]["moved"] == 0)
check("all 3 ledgers archived", all(res[f]["moved"] == 15 for f in la.FILES))
check("QT_LEDGER_KEEP_DAYS=0 disables", la.run(keep_days=0) == [])
check("default keep is 3 days", settings.ledger_keep_days == 3)
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
