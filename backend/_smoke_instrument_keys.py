"""Dropdown sends registry keys; scrip names (BAJAJ-AUTO, M&M) resolve to the
same instrument instead of falling back to the default (Crude)."""
import os, sys
os.environ.setdefault("QT_DATA_DIR", "/tmp/qt_keys_smoke")
os.environ["QT_DATA_PROVIDER"] = "simulated"
from app.market import instruments as I
from app.state import StateRegistry

fails = []
def check(n, ok):
    print(("PASS " if ok else "FAIL ") + n)
    if not ok: fails.append(n)

check("BAJAJ-AUTO -> BAJAJAUTO", I.resolve_key("bajaj-auto") == "BAJAJAUTO")
check("BAJAJAUTO -> BAJAJAUTO", I.resolve_key("BAJAJAUTO") == "BAJAJAUTO")
check("M&M -> MM", I.resolve_key("M&M") == "MM")
check("unknown stays unresolved", I.resolve_key("NOPE") == "NOPE")
check("get_spec(BAJAJ-AUTO) is Bajaj", I.get_spec("BAJAJ-AUTO").display == "Bajaj Auto")
check("ABB + SHRIRAMFIN registered", "ABB" in I.REGISTRY and "SHRIRAMFIN" in I.REGISTRY)
reg = StateRegistry()
st = reg.get("BAJAJ-AUTO")
check("registry opens Bajaj, not Crude", st.instrument == "BAJAJAUTO")
snap = st.tick()
keys = [o.symbol for o in snap.instruments]
check("dropdown values are registry keys", bool(keys) and all(k in I.REGISTRY for k in keys))
check("snapshot underlying is Bajaj", "BAJAJ" in (snap.underlying or ""))
print("ALL INSTRUMENT KEY CHECKS PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
