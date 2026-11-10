"""Smoke: a failed start-up warm-up is remembered, reported and retried."""
import asyncio
import os

os.environ.setdefault("QT_TRADE_MODE", "paper")
from app import main  # noqa: E402
from app.market import instruments  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {info}"))
    fails += 0 if ok else 1


calls: dict[str, int] = {}


def fake_get(sym):
    calls[sym] = calls.get(sym, 0) + 1
    if sym == "GOLD" and calls[sym] < 3:
        raise RuntimeError("scrip master not loaded yet")
    return object()


main.registry.get = fake_get
instruments.UNIVERSE[:] = ["CRUDEOIL", "GOLD"] if isinstance(instruments.UNIVERSE, list) else None
main._WARM_RETRY_SEC = 0.0
seen_failed = {}
orig_sleep = asyncio.sleep


async def spy_sleep(t):
    seen_failed.update(main._warm_state["failed"])
    await orig_sleep(0)

asyncio.sleep = spy_sleep
asyncio.run(main._warm_all())
check("failure recorded with its reason", "scrip master" in seen_failed.get("GOLD", ""), seen_failed)
check("failed instrument retried until it warmed", calls.get("GOLD") == 3, calls)
check("healthy instrument warmed once", calls.get("CRUDEOIL") == 1, calls)
check("nothing left failed", main._warm_state["failed"] == {}, main._warm_state["failed"])
r = main._warm_report()
check("report lists instruments without a price", set(r["no_price_yet"]) >= {"CRUDEOIL", "GOLD"}, r)
print("ALL PASS" if not fails else f"{fails} FAILED")
raise SystemExit(1 if fails else 0)
