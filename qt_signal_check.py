"""Read-only: why each watchlist instrument is (not) giving a BUY, live vs paper. Sends no order."""
import json, urllib.request, collections
def get(port, path):
    try:
        return json.load(urllib.request.urlopen(f"http://localhost:{port}{path}", timeout=60))
    except Exception as e:
        return {"_err": str(e)}
wl = get(8000, "/api/watchlist")
enabled = [i["symbol"] for i in wl.get("instruments", []) if i.get("enabled")]
rows, ages, refus = {}, {}, {}
for p in (8000, 8001):
    d = get(p, "/api/signal-diagnostics?limit=50")
    rows[p] = {r["instrument"]: r for r in d.get("live", [])}
    ages[p] = get(p, "/api/scan-health").get("ages", {})
    c = collections.defaultdict(collections.Counter)
    for e in d.get("execution", {}).get("recent", []):
        c[e.get("instrument")][f'{e.get("blocker")}={e.get("value")}'] += 1
    refus[p] = c
def cell(p, s):
    r = rows[p].get(s)
    if not r:
        return "NO DECISION (not ticked)"
    b = r.get("primary_blocker") or "-"
    return f'{r.get("signal")} block={b} trig={r.get("entry_trigger") or "-"} conf={r.get("confidence")}'
print(f"{'INSTR':11} {'LIVE (8000)':58} | PAPER (8001)")
for s in enabled:
    a = ages[8000].get(s)
    print(f"{s:11} {cell(8000, s)[:50]:50} age={'-' if a is None else int(a):>4} | {cell(8001, s)[:60]}")
    for p in (8000, 8001):
        if refus[p].get(s):
            print(f"{'':11}   {'live' if p == 8000 else 'paper'} refused: {dict(refus[p][s].most_common(3))}")
for p in (8000, 8001):
    sig = collections.Counter(r.get("signal") for r in rows[p].values())
    blk = collections.Counter(r.get("primary_blocker") for r in rows[p].values() if r.get("signal") != "BUY")
    print(f"\n{p}: decisions {len(rows[p])}, signals {dict(sig)}\n  top blockers {blk.most_common(8)}")
