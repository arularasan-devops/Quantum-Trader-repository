#!/usr/bin/env python3
"""Quantum Trader v3.5.48 add-on (apply after qt_v3547_patch.py, from the repo root):
  * LIVE never auto-buys a PULLBACK setup (QT_AUTO_TRADE_LIVE_BLOCKED_TRIGGERS, default ["PULLBACK"]); paper unchanged
  * ZINC removed from the instrument list; active cap 50 -> 51 so MAZDOCK fits
Changes files only. Checks every file first; if any check fails nothing is written."""
import base64, os, shutil, sys, time
def rd(f):
    with open(f, encoding="utf-8", newline="") as h:
        return h.read().replace("\r\n", "\n")
need = ["backend/app/market/instruments.py", "backend/app/state.py", "backend/app/config.py"]
for f in need:
    if not os.path.isfile(f):
        sys.exit("REFUSED: run this from the Quantum-Trader-repository folder")
out = {}
# instruments.py
s = rd(need[0]); lines = s.split("\n")
if '"MAZDOCK": InstrumentSpec(' not in s:
    sys.exit("REFUSED (nothing changed): MAZDOCK missing - apply qt_v3547_patch.py first")
zinc = [l for l in lines if l.strip().startswith('"ZINC": InstrumentSpec(')]
cap = [l for l in lines if l.startswith("MAX_ACTIVE = ")]
if len(cap) != 1 or len(zinc) > 1:
    sys.exit("REFUSED (nothing changed): instruments.py layout differs")
new = [l for l in lines if l not in zinc]
new = [("MAX_ACTIVE = 51  # v3.5.48: 51 so MAZDOCK fits beside the 50 already watched"
        if l.startswith("MAX_ACTIVE = ") else l) for l in new]
out[need[0]] = (s, "\n".join(new))
# state.py
s = rd(need[1])
A = """            trig_ok = trig in allowed_t or any(
                t.upper() in allowed_t for t in (decision.entry_triggers or []))
"""
B = """            if live and trig in {t.upper() for t in settings.auto_trade_live_blocked_triggers}:
                trig_ok = False
"""
if "auto_trade_live_blocked_triggers" in s:
    out[need[1]] = (s, s)
elif s.count(A) == 1:
    out[need[1]] = (s, s.replace(A, A + B))
else:
    sys.exit("REFUSED (nothing changed): state.py layout differs")
# config.py
s = rd(need[2])
A = '    auto_trade_allowed_regimes: list[str] = ["TRENDING"]\n'
B = ('    # v3.5.48: LIVE never auto-buys a tick whose setup is one of these, even when\n'
     '    # another allowed trigger is also satisfied (paper is unchanged).\n'
     '    auto_trade_live_blocked_triggers: list[str] = ["PULLBACK"]\n')
if "auto_trade_live_blocked_triggers" in s:
    out[need[2]] = (s, s)
elif s.count(A) == 1:
    out[need[2]] = (s, s.replace(A, A + B))
else:
    sys.exit("REFUSED (nothing changed): config.py layout differs")
stamp = time.strftime("%Y%m%d%H%M%S")
for f, (old, nw) in out.items():
    if nw != old:
        shutil.copy2(f, f + ".pre3548.%s.bak" % stamp)
        with open(f, "w", encoding="utf-8", newline="\n") as h:
            h.write(nw)
for f, data in (("backend/_smoke_v3548.py", "IiIidjMuNS40ODogWklOQyByZW1vdmVkIGZyb20gdGhlIGluc3RydW1lbnQgbGlzdDsgdGhlIGFjdGl2ZSBjYXAgaXMgNTEgc28gTUFaRE9DSyBmaXRzLiIiIgpmcm9tIGFwcC5tYXJrZXQgaW1wb3J0IGluc3RydW1lbnRzIGFzIEkKCmZhaWxzID0gMApkZWYgb2soYywgbSk6CiAgICBnbG9iYWwgZmFpbHMKICAgIHByaW50KCgiUEFTUyAiIGlmIGMgZWxzZSAiRkFJTCAiKSArIG0pCiAgICBmYWlscyArPSAwIGlmIGMgZWxzZSAxCgpvaygiWklOQyIgbm90IGluIEkuUkVHSVNUUlksICJaSU5DIGlzIG5vdCBpbiB0aGUgaW5zdHJ1bWVudCBsaXN0IikKb2soIk1BWkRPQ0siIGluIEkuUkVHSVNUUlkgYW5kIEkuUkVHSVNUUllbIk1BWkRPQ0siXS5sb3Rfc2l6ZSA9PSAyMjUsICJNQVpET0NLIGxpc3RlZCwgbG90IDIyNSIpCm9rKEkuTUFYX0FDVElWRSA9PSA1MSwgImFjdGl2ZSBjYXAgaXMgNTEiKQpuYW1lcyA9IFtzIGZvciBzIGluIEkuUkVHSVNUUlkgaWYgcyAhPSAiTUFaRE9DSyJdWzo1MF0gKyBbIk1BWkRPQ0siXQpnb3QgPSBJLnNldF91bml2ZXJzZShuYW1lcykKb2sobGVuKGdvdCkgPT0gNTEgYW5kICJNQVpET0NLIiBpbiBnb3QsICJhIDUxLW5hbWUgd2F0Y2hsaXN0IGtlZXBzIE1BWkRPQ0siKQpnb3QgPSBJLnNldF91bml2ZXJzZShsaXN0KEkuUkVHSVNUUlkpICsgWyJaSU5DIl0pCm9rKGxlbihnb3QpID09IDUxIGFuZCAiWklOQyIgbm90IGluIGdvdCwgIlpJTkMgaXMgaWdub3JlZCwgY2FwIHN0aWxsIDUxIikKcHJpbnQoIkFMTCBQQVNTIiBpZiBub3QgZmFpbHMgZWxzZSBmIntmYWlsc30gRkFJTEVEIikK"), ("backend/_smoke_v3548b.py", "IiIidjMuNS40ODogTElWRSBuZXZlciBhdXRvLWJ1eXMgYSBQVUxMQkFDSyBzZXR1cDsgcGFwZXIgdW5jaGFuZ2VkLiBObyBicm9rZXIuIiIiCmltcG9ydCBvcwppbXBvcnQgdGVtcGZpbGUKZnJvbSB0eXBlcyBpbXBvcnQgU2ltcGxlTmFtZXNwYWNlCgpvcy5lbnZpcm9uLnNldGRlZmF1bHQoIlFUX0RBVEFfRElSIiwgdGVtcGZpbGUubWtkdGVtcCgpKQoKZnJvbSBhcHAuY29uZmlnIGltcG9ydCBzZXR0aW5ncyAgIyBub3FhOiBFNDAyCmZyb20gYXBwLm1vZGVscyBpbXBvcnQgU2lnbmFsICAjIG5vcWE6IEU0MDIKZnJvbSBhcHAuc3RhdGUgaW1wb3J0IEFwcFN0YXRlICAjIG5vcWE6IEU0MDIKCkZBSUw6IGxpc3Rbc3RyXSA9IFtdCgoKZGVmIGNoZWNrKG5hbWUsIG9rLCBpbmZvPSIiKToKICAgIHByaW50KCgiUEFTUyAiIGlmIG9rIGVsc2UgIkZBSUwgIikgKyBuYW1lICsgKCIiIGlmIG9rIGVsc2UgZiIg4oCUIHtpbmZvfSIpKQogICAgaWYgbm90IG9rOgogICAgICAgIEZBSUwuYXBwZW5kKG5hbWUpCgoKc2V0dGluZ3MuYXV0b190cmFkZV9lbmFibGVkID0gVHJ1ZQpzZXR0aW5ncy5hdXRvX3RyYWRlX2FsbG93X2xpdmUgPSBUcnVlCnNldHRpbmdzLmF1dG9fdHJhZGVfaW5zdHJ1bWVudHMgPSAiIgpzZXR0aW5ncy5hdXRvX3RyYWRlX3N0cmF0ZWd5X2dhdGUgPSBUcnVlCnNldHRpbmdzLmF1dG9fdHJhZGVfYWxsb3dlZF90cmlnZ2VycyA9IFsiU1VQRVJUUkVORCIsICJJR05JVElPTiIsICJCUkVBS09VVF9SRVRFU1QiLCAiTU9NRU5UVU0iXQpzZXR0aW5ncy5hdXRvX3RyYWRlX2FsbG93ZWRfcmVnaW1lcyA9IFsiVFJFTkRJTkciXQoKCmRlZiBnYXRlKGxpdmUsIHRyaWcsIHRyaWdzKToKICAgIEFwcFN0YXRlLmlzX2xpdmUgPSBwcm9wZXJ0eShsYW1iZGEgczogbGl2ZSkKICAgIGVuZyA9IEFwcFN0YXRlKGluc3RydW1lbnQ9IkNSVURFT0lMIikKICAgIGVuZy5fYXV0b19saXZlX2FybWVkID0gbGFtYmRhOiBsaXZlCiAgICBlbmcuX2xpdmVfc3F1YXJlb2ZmX2R1ZSA9IGxhbWJkYSBub3c6IEZhbHNlCiAgICBlbmcuX2xhc3RfbWFya2V0X3N0YXR1cyA9ICJUUkVORElORyIKICAgIGVuZy5fYXV0b19sYXN0X2FjdGlvbiA9ICIiCiAgICBkID0gU2ltcGxlTmFtZXNwYWNlKHNpZ25hbD1TaWduYWwuQlVZLCByZWNvbW1lbmRlZF9vcHRpb249IkNSVURFT0lMODYwMFBFIiwgY3VycmVudF9wcmVtaXVtPTI1MC4wLAogICAgICAgICAgICAgICAgICAgICAgICBzdG9wX2xvc3M9MjMwLjAsIHRhcmdldDE9MjcwLjAsIG9wdGlvbl90eXBlPU5vbmUsCiAgICAgICAgICAgICAgICAgICAgICAgIGVudHJ5X3RyaWdnZXI9dHJpZywgZW50cnlfdHJpZ2dlcnM9dHJpZ3MpCiAgICB0cnk6CiAgICAgICAgZW5nLl9hdXRvX3RyYWRlKGQsIEZhbHNlLCAwKQogICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAgICBwYXNzICAjIGFueXRoaW5nIHBhc3QgdGhlIHN0cmF0ZWd5IGdhdGUgaXMgbm90IHVuZGVyIHRlc3QKICAgIHJldHVybiAic3RyYXRlZ3kgbmVlZHMiIGluIChlbmcuX2F1dG9fbGFzdF9hY3Rpb24gb3IgIiIpCgoKY2hlY2soImRlZmF1bHQgYmxvY2tzIFBVTExCQUNLIGluIGxpdmUiLCBzZXR0aW5ncy5hdXRvX3RyYWRlX2xpdmVfYmxvY2tlZF90cmlnZ2VycyA9PSBbIlBVTExCQUNLIl0pCmNoZWNrKCJsaXZlOiBQVUxMQkFDSyAoK01PTUVOVFVNIGFsc28gdHJ1ZSkgaXMgc2tpcHBlZCIsIGdhdGUoVHJ1ZSwgIlBVTExCQUNLIiwgWyJQVUxMQkFDSyIsICJNT01FTlRVTSJdKSkKY2hlY2soImxpdmU6IE1PTUVOVFVNIHN0aWxsIHBhc3NlcyB0aGUgZ2F0ZSIsIG5vdCBnYXRlKFRydWUsICJNT01FTlRVTSIsIFsiTU9NRU5UVU0iXSkpCmNoZWNrKCJsaXZlOiBTVVBFUlRSRU5EICgrUFVMTEJBQ0spIHN0aWxsIHBhc3NlcyIsIG5vdCBnYXRlKFRydWUsICJTVVBFUlRSRU5EIiwgWyJTVVBFUlRSRU5EIiwgIlBVTExCQUNLIl0pKQpjaGVjaygicGFwZXI6IFBVTExCQUNLICgrTU9NRU5UVU0pIHVuY2hhbmdlZCwgcGFzc2VzIiwgbm90IGdhdGUoRmFsc2UsICJQVUxMQkFDSyIsIFsiUFVMTEJBQ0siLCAiTU9NRU5UVU0iXSkpCnNldHRpbmdzLmF1dG9fdHJhZGVfbGl2ZV9ibG9ja2VkX3RyaWdnZXJzID0gW10KY2hlY2soInNldHRpbmcgZW1wdGllZCAtPiBsaXZlIFBVTExCQUNLK01PTUVOVFVNIHBhc3NlcyBhZ2FpbiIsIG5vdCBnYXRlKFRydWUsICJQVUxMQkFDSyIsIFsiUFVMTEJBQ0siLCAiTU9NRU5UVU0iXSkpCnByaW50KCJBTEwgUEFTUyIgaWYgbm90IEZBSUwgZWxzZSBmIntsZW4oRkFJTCl9IEZBSUw6IHtGQUlMfSIpCg==")):
    with open(f, "wb") as h:
        h.write(base64.b64decode(data))
print("v3.5.48 applied OK (backups: *.pre3548.%s.bak)" % stamp)
