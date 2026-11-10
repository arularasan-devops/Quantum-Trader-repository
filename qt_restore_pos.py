"""Undo a mistaken confirm_flat: put the saved live position back into the
order ledger as an UNVERIFIED recovery claim, so the operator can adopt it.
Run ONLY with the live backend STOPPED. Sends nothing to Angel."""
import json, os, sys, time
sys.path.insert(0, os.getcwd())
from app.config import settings
from app.execution import order_ledger as L

EXPECT = {"CRUDEOIL": "CRUDEOIL17NOV268350PE", "BANKNIFTY": "BANKNIFTY23NOV2656900CE"}
path = os.path.join(settings.data_dir, L.EVENTS_FILE)
last = {}
with open(path, encoding="utf-8") as fh:
    for line in fh:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("instrument") in EXPECT and e.get("kind") == "OPERATOR_CONFIRMED_FLAT":
            last[e["instrument"]] = e
ok = True
for inst, sym in EXPECT.items():
    e = last.get(inst)
    pos = (e or {}).get("saved_position")
    if not pos or pos.get("symbol") != sym or not pos.get("lots") or not pos.get("entry"):
        print(f"SKIP {inst}: no saved {sym} with lots+entry in the confirm_flat event ({pos})")
        ok = False
        continue
    if L.load(inst).get("position"):
        print(f"SKIP {inst}: ledger already holds a position")
        continue
    pos = dict(pos, verified=False)
    L.save(inst, {"position": pos, "recovery": {
        "reason": "confirm_flat sent in error - Angel still holds the position",
        "since": time.time(), "manual_only": True}})
    L.record_event(inst, "OPERATOR_REOPENED_RECOVERY", saved_position=pos,
                   note="confirm_flat was a mistake; Angel still open")
    print(f"RESTORED {inst}: {pos['symbol']} lots={pos['lots']} entry={pos['entry']}")
print("DONE" if ok else "DONE WITH SKIPS - paste this output")
