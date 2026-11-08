"""v3.5.48: ZINC removed from the instrument list; the active cap is 51 so MAZDOCK fits."""
from app.market import instruments as I

fails = 0
def ok(c, m):
    global fails
    print(("PASS " if c else "FAIL ") + m)
    fails += 0 if c else 1

ok("ZINC" not in I.REGISTRY, "ZINC is not in the instrument list")
ok("MAZDOCK" in I.REGISTRY and I.REGISTRY["MAZDOCK"].lot_size == 225, "MAZDOCK listed, lot 225")
ok(I.MAX_ACTIVE == 51, "active cap is 51")
names = [s for s in I.REGISTRY if s != "MAZDOCK"][:50] + ["MAZDOCK"]
got = I.set_universe(names)
ok(len(got) == 51 and "MAZDOCK" in got, "a 51-name watchlist keeps MAZDOCK")
got = I.set_universe(list(I.REGISTRY) + ["ZINC"])
ok(len(got) == 51 and "ZINC" not in got, "ZINC is ignored, cap still 51")
print("ALL PASS" if not fails else f"{fails} FAILED")
