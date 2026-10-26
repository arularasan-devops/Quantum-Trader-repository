"""Concurrent registry.get() must build exactly one AppState per instrument."""
import threading, time
import app.state as S

built = []
class Slow:
    def __init__(self, name):
        built.append(name); time.sleep(0.5); self.instrument = name

real = S.AppState
S.AppState = Slow
ok = fail = 0
def check(cond, msg):
    global ok, fail
    if cond: ok += 1
    else: fail += 1; print("FAIL:", msg)
try:
    reg = S.StateRegistry()
    names = [n for n in S.REGISTRY][:2]
    out = []
    ts = [threading.Thread(target=lambda n=n: out.append(reg.get(n))) for n in names * 6]
    t0 = time.time(); [t.start() for t in ts]; [t.join() for t in ts]
    dt = time.time() - t0
    for n in names:
        check(built.count(n) == 1, f"{n} built {built.count(n)} times")
        check(len({id(x) for x in out if x.instrument == n}) == 1, f"{n}: callers got different instances")
    check(dt < 0.9, f"different instruments should build in parallel ({dt:.2f}s)")
    check(reg.get(names[0]) is out[[x.instrument for x in out].index(names[0])], "cached instance returned")
finally:
    S.AppState = real
print(f"REGISTRY RACE SMOKE — {ok} passed, {fail} failed")
