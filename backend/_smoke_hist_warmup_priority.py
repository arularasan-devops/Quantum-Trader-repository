"""Smoke: instruments with no history get the Angel history slot first, and a
rate-limit deferral is shown as a queue, not an error. No network, no broker."""
import time

from app.market import angelone as a

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAIL.append(name)


Prov = next(c for c in vars(a).values()
            if isinstance(c, type) and hasattr(c, "_refresh_futures_candles"))


class _Shard:
    hist = a._HistLimiter()
    smart = None


class _Fut:
    token = "1"


def make(root: str, cached: bool, age: float = 10.0):
    p = Prov.__new__(Prov)
    p._future, p._exchange, p._root, p._shard = _Fut(), "NFO", root, _Shard
    p._fut_candles = a._to_candles([["2026-10-16T09:15:00+05:30", 1, 2, 0.5, 1.5, 100]]) \
        if cached else []
    p._fut_candles_cached_at = time.time() - age if cached else 0.0
    p._fut_rest_attempts = p._fut_rest_ok = p._fut_rest_deferred = 0
    p._candle_diag = ""
    return p


calls: list[str] = []
mode = {"deferred": True}


def fake(shard, params, blocking):
    calls.append(params["symboltoken"])
    if mode["deferred"]:
        return [], "deferred: historical budget busy, no slot within 0.5s"
    return [["2026-10-16T09:16:00+05:30", 1, 2, 0.5, 1.6, 100]], ""


a._historical_candles = fake

cold = make("ICICIBANK", cached=False)
cold._refresh_futures_candles()
check("cold instrument deferred -> marked warming", _Shard.hist.warming_count() == 1)
check("deferral shown as a queue, not the raw error",
      "queued" in cold._candle_diag and "deferred" not in cold._candle_diag, cold._candle_diag)

warm = make("SBIN", cached=True, age=10.0)
n = len(calls)
warm._refresh_futures_candles()
check("cached instrument yields its slot while another warms", len(calls) == n)

old = make("TCS", cached=True, age=a._HIST_YIELD_MAX_AGE + 5)
n = len(calls)
old._refresh_futures_candles()
check("cache older than the yield bound still refreshes", len(calls) == n + 1)

mode["deferred"] = False
cold._refresh_futures_candles()
check("cold instrument loads -> no longer warming",
      bool(cold._fut_candles) and _Shard.hist.warming_count() == 0 and not cold._candle_diag)
n = len(calls)
warm._refresh_futures_candles()
check("cached refresh resumes once nothing is warming", len(calls) == n + 1)
check("limiter stats report warming count", "instruments_warming" in _Shard.hist.stats())

print("\n" + ("All checks passed!" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
raise SystemExit(1 if FAIL else 0)
