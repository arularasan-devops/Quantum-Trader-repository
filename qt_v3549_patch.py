#!/usr/bin/env python3
"""Quantum Trader v3.5.49 (apply after qt_v3548_patch.py, from the repo root):
LIVE buys a tick that is PULLBACK + another allowed setup (e.g. MOMENTUM) again;
PULLBACK alone still never auto-buys live. Paper unchanged. Changes files only;
every file is checked first and nothing is written if a check fails."""
import base64, os, shutil, sys, time
def rd(f):
    with open(f, encoding="utf-8", newline="") as h:
        return h.read().replace("\r\n", "\n")
S, C = "backend/app/state.py", "backend/app/config.py"
for f in (S, C):
    if not os.path.isfile(f):
        sys.exit("REFUSED: run this from the Quantum-Trader-repository folder")
s, c = rd(S), rd(C)
OLD_S = """            if live and trig in {t.upper() for t in settings.auto_trade_live_blocked_triggers}:
                trig_ok = False
"""
A = """            allowed_t = {t.upper() for t in settings.auto_trade_allowed_triggers}
"""
NEW_S = """            if live:  # v3.5.49: a blocked setup alone never buys live; with another allowed one it does
                allowed_t -= {t.upper() for t in settings.auto_trade_live_blocked_triggers}
"""
OLD_C = """    # v3.5.48: LIVE never auto-buys a tick whose setup is one of these, even when
    # another allowed trigger is also satisfied (paper is unchanged).
"""
NEW_C = """    # v3.5.49: LIVE never auto-buys on these setups alone; a tick that also meets
    # another allowed setup (e.g. PULLBACK + MOMENTUM) still buys. Paper unchanged.
"""
if NEW_S in s:
    ns = s
else:
    if s.count(OLD_S) != 1:
        sys.exit("REFUSED (nothing changed): state.py layout differs - apply qt_v3548_patch.py first")
    ns = s.replace(OLD_S, "")
    g = ns.find("if settings.auto_trade_strategy_gate:")
    i = ns.find(A, g)
    if g < 0 or i < 0:
        sys.exit("REFUSED (nothing changed): state.py layout differs")
    ns = ns[:i + len(A)] + NEW_S + ns[i + len(A):]
if NEW_C in c:
    nc = c
elif c.count(OLD_C) == 1:
    nc = c.replace(OLD_C, NEW_C)
else:
    sys.exit("REFUSED (nothing changed): config.py layout differs - apply qt_v3548_patch.py first")
stamp = time.strftime("%Y%m%d%H%M%S")
for f, old, new in ((S, s, ns), (C, c, nc)):
    if new != old:
        shutil.copy2(f, f + ".pre3549.%s.bak" % stamp)
        with open(f, "w", encoding="utf-8", newline="\n") as h:
            h.write(new)
if os.path.isfile("backend/_smoke_v3548b.py"):
    os.remove("backend/_smoke_v3548b.py")  # replaced by _smoke_v3549.py
with open("backend/_smoke_v3549.py", "wb") as h:
    h.write(base64.b64decode("IiIidjMuNS40OTogTElWRSBuZXZlciBhdXRvLWJ1eXMgUFVMTEJBQ0sgYWxvbmU7IFBVTExCQUNLICsgTU9NRU5UVU0gc3RpbGwgYnV5cy4gUGFwZXIgdW5jaGFuZ2VkLiBObyBicm9rZXIuIiIiCmltcG9ydCBvcwppbXBvcnQgdGVtcGZpbGUKZnJvbSB0eXBlcyBpbXBvcnQgU2ltcGxlTmFtZXNwYWNlCgpvcy5lbnZpcm9uLnNldGRlZmF1bHQoIlFUX0RBVEFfRElSIiwgdGVtcGZpbGUubWtkdGVtcCgpKQoKZnJvbSBhcHAuY29uZmlnIGltcG9ydCBzZXR0aW5ncyAgIyBub3FhOiBFNDAyCmZyb20gYXBwLm1vZGVscyBpbXBvcnQgU2lnbmFsICAjIG5vcWE6IEU0MDIKZnJvbSBhcHAuc3RhdGUgaW1wb3J0IEFwcFN0YXRlICAjIG5vcWE6IEU0MDIKCkZBSUw6IGxpc3Rbc3RyXSA9IFtdCgoKZGVmIGNoZWNrKG5hbWUsIG9rLCBpbmZvPSIiKToKICAgIHByaW50KCgiUEFTUyAiIGlmIG9rIGVsc2UgIkZBSUwgIikgKyBuYW1lICsgKCIiIGlmIG9rIGVsc2UgZiIg4oCUIHtpbmZvfSIpKQogICAgaWYgbm90IG9rOgogICAgICAgIEZBSUwuYXBwZW5kKG5hbWUpCgoKc2V0dGluZ3MuYXV0b190cmFkZV9lbmFibGVkID0gVHJ1ZQpzZXR0aW5ncy5hdXRvX3RyYWRlX2FsbG93X2xpdmUgPSBUcnVlCnNldHRpbmdzLmF1dG9fdHJhZGVfaW5zdHJ1bWVudHMgPSAiIgpzZXR0aW5ncy5hdXRvX3RyYWRlX3N0cmF0ZWd5X2dhdGUgPSBUcnVlCnNldHRpbmdzLmF1dG9fdHJhZGVfYWxsb3dlZF90cmlnZ2VycyA9IFsiU1VQRVJUUkVORCIsICJJR05JVElPTiIsICJCUkVBS09VVF9SRVRFU1QiLCAiTU9NRU5UVU0iXQpzZXR0aW5ncy5hdXRvX3RyYWRlX2FsbG93ZWRfcmVnaW1lcyA9IFsiVFJFTkRJTkciXQoKCmRlZiBnYXRlKGxpdmUsIHRyaWcsIHRyaWdzKToKICAgIEFwcFN0YXRlLmlzX2xpdmUgPSBwcm9wZXJ0eShsYW1iZGEgczogbGl2ZSkKICAgIGVuZyA9IEFwcFN0YXRlKGluc3RydW1lbnQ9IkNSVURFT0lMIikKICAgIGVuZy5fYXV0b19saXZlX2FybWVkID0gbGFtYmRhOiBsaXZlCiAgICBlbmcuX2xpdmVfc3F1YXJlb2ZmX2R1ZSA9IGxhbWJkYSBub3c6IEZhbHNlCiAgICBlbmcuX2xhc3RfbWFya2V0X3N0YXR1cyA9ICJUUkVORElORyIKICAgIGVuZy5fYXV0b19sYXN0X2FjdGlvbiA9ICIiCiAgICBkID0gU2ltcGxlTmFtZXNwYWNlKHNpZ25hbD1TaWduYWwuQlVZLCByZWNvbW1lbmRlZF9vcHRpb249IkNSVURFT0lMODYwMFBFIiwgY3VycmVudF9wcmVtaXVtPTI1MC4wLAogICAgICAgICAgICAgICAgICAgICAgICBzdG9wX2xvc3M9MjMwLjAsIHRhcmdldDE9MjcwLjAsIG9wdGlvbl90eXBlPU5vbmUsCiAgICAgICAgICAgICAgICAgICAgICAgIGVudHJ5X3RyaWdnZXI9dHJpZywgZW50cnlfdHJpZ2dlcnM9dHJpZ3MpCiAgICB0cnk6CiAgICAgICAgZW5nLl9hdXRvX3RyYWRlKGQsIEZhbHNlLCAwKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICBwYXNzICAjIGFueXRoaW5nIHBhc3QgdGhlIHN0cmF0ZWd5IGdhdGUgaXMgbm90IHVuZGVyIHRlc3QKICAgIHJldHVybiAic3RyYXRlZ3kgbmVlZHMiIGluIChlbmcuX2F1dG9fbGFzdF9hY3Rpb24gb3IgIiIpCgoKY2hlY2soImRlZmF1bHQgYmxvY2tzIFBVTExCQUNLIGluIGxpdmUiLCBzZXR0aW5ncy5hdXRvX3RyYWRlX2xpdmVfYmxvY2tlZF90cmlnZ2VycyA9PSBbIlBVTExCQUNLIl0pCmNoZWNrKCJsaXZlOiBQVUxMQkFDSyArIE1PTUVOVFVNIHBhc3NlcyB0aGUgZ2F0ZSIsIG5vdCBnYXRlKFRydWUsICJQVUxMQkFDSyIsIFsiUFVMTEJBQ0siLCAiTU9NRU5UVU0iXSkpCmNoZWNrKCJsaXZlOiBQVUxMQkFDSyBhbG9uZSBpcyBza2lwcGVkIiwgZ2F0ZShUcnVlLCAiUFVMTEJBQ0siLCBbIlBVTExCQUNLIl0pKQpzZXR0aW5ncy5hdXRvX3RyYWRlX2FsbG93ZWRfdHJpZ2dlcnMgPSBbIlBVTExCQUNLIiwgIk1PTUVOVFVNIl0KY2hlY2soImxpdmU6IFBVTExCQUNLIGFsb25lIHNraXBwZWQgZXZlbiBpZiBsaXN0ZWQgYXMgYWxsb3dlZCIsIGdhdGUoVHJ1ZSwgIlBVTExCQUNLIiwgWyJQVUxMQkFDSyJdKSkKY2hlY2soInBhcGVyOiBQVUxMQkFDSyBhbG9uZSBhbGxvd2VkIHdoZW4gbGlzdGVkIiwgbm90IGdhdGUoRmFsc2UsICJQVUxMQkFDSyIsIFsiUFVMTEJBQ0siXSkpCnNldHRpbmdzLmF1dG9fdHJhZGVfYWxsb3dlZF90cmlnZ2VycyA9IFsiU1VQRVJUUkVORCIsICJJR05JVElPTiIsICJCUkVBS09VVF9SRVRFU1QiLCAiTU9NRU5UVU0iXQpjaGVjaygibGl2ZTogTU9NRU5UVU0gc3RpbGwgcGFzc2VzIHRoZSBnYXRlIiwgbm90IGdhdGUoVHJ1ZSwgIk1PTUVOVFVNIiwgWyJNT01FTlRVTSJdKSkKY2hlY2soImxpdmU6IFNVUEVSVFJFTkQgKCtQVUxMQkFDSykgc3RpbGwgcGFzc2VzIiwgbm90IGdhdGUoVHJ1ZSwgIlNVUEVSVFJFTkQiLCBbIlNVUEVSVFJFTkQiLCAiUFVMTEJBQ0siXSkpCmNoZWNrKCJwYXBlcjogUFVMTEJBQ0sgKCtNT01FTlRVTSkgdW5jaGFuZ2VkLCBwYXNzZXMiLCBub3QgZ2F0ZShGYWxzZSwgIlBVTExCQUNLIiwgWyJQVUxMQkFDSyIsICJNT01FTlRVTSJdKSkKc2V0dGluZ3MuYXV0b190cmFkZV9saXZlX2Jsb2NrZWRfdHJpZ2dlcnMgPSBbXQpzZXR0aW5ncy5hdXRvX3RyYWRlX2FsbG93ZWRfdHJpZ2dlcnMgPSBbIlBVTExCQUNLIl0KY2hlY2soInNldHRpbmcgZW1wdGllZCAtPiBsaXZlIFBVTExCQUNLIGFsb25lIHBhc3NlcyB3aGVuIGxpc3RlZCIsIG5vdCBnYXRlKFRydWUsICJQVUxMQkFDSyIsIFsiUFVMTEJBQ0siXSkpCnByaW50KCJBTEwgUEFTUyIgaWYgbm90IEZBSUwgZWxzZSBmIntsZW4oRkFJTCl9IEZBSUw6IHtGQUlMfSIpCg=="))
print("v3.5.49 applied OK (backups: *.pre3549.%s.bak)" % stamp)
