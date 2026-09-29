"""Phase 4 Part T — measure where the tick budget actually goes.

Answers, with numbers rather than assumptions:

* how much CPU one DEEP engine pass costs (indicators + decision) vs one FAST
  scanner pass, on identical data;
* how long a full sweep of 10 / 25 / 50 / 75 / 100 instruments therefore takes
  at the live loop's own cadence;
* at what universe size the price the engine is reasoning about stops being
  FRESH by the Phase 4 thresholds;
* how much event-loop lag the deep pass causes when run the way the app runs it
  (``run_in_executor`` from the tick loop).

No broker calls, no orders, no network: the cost being measured is our own
compute, so synthetic-but-realistic candles are the right input and the numbers
are not distorted by Angel's latency.

    python scan_profile.py --out ~/scan_profile.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import statistics
import time

from app.analysis import scanner
from app.config import settings
from app.engine import decision as eng
from app.market import options
from app.market.instruments import UNIVERSE, get_spec
from app.market.tick_quality import AGING_MS, FRESH_MS
from app.models import Candle, OptionQuote, OptionType

SIZES = (10, 25, 50, 75, 100)


def synth_candles(n: int, start: float, vol: float, seed: int) -> list[Candle]:
    """Deterministic random-walk 1-minute bars (fixed seed => comparable runs)."""
    import random

    rng = random.Random(seed)
    out: list[Candle] = []
    px = start
    t0 = int(time.time()) - n * 60
    for i in range(n):
        o = px
        px = max(start * 0.5, px * (1.0 + rng.gauss(0, vol / 100.0)))
        hi = max(o, px) * (1.0 + abs(rng.gauss(0, vol / 400.0)))
        lo = min(o, px) * (1.0 - abs(rng.gauss(0, vol / 400.0)))
        out.append(Candle(time=t0 + i * 60, open=o, high=hi, low=lo, close=px,
                          volume=float(rng.randint(500, 5000))))
    return out


def synth_chain(spot: float, step: float, dte: float) -> list[OptionQuote]:
    chain: list[OptionQuote] = []
    n = settings.strikes_each_side
    atm = round(spot / step) * step
    t = max(dte, 0.5) / 365.0
    for i in range(-n, n + 1):
        k = atm + i * step
        for ot in (OptionType.CALL, OptionType.PUT):
            is_call = ot is OptionType.CALL
            g = options.greeks(spot, k, settings.risk_free_rate, 0.3, t, is_call)
            prem = options.price(spot, k, settings.risk_free_rate, 0.3, t, is_call)
            chain.append(OptionQuote(
                symbol=f"X{int(k)}{ot.value}", strike=k, option_type=ot,
                premium=max(0.05, prem), iv=0.3, delta=g["delta"], gamma=g["gamma"],
                theta=g["theta"], vega=g["vega"], oi=10_000, oi_change=0,
                volume=5_000,
            ))
    return chain


def build_universe(size: int) -> list[tuple[str, list[Candle], list[OptionQuote]]]:
    names = (UNIVERSE * ((size // max(1, len(UNIVERSE))) + 1))[:size]
    out = []
    for i, name in enumerate(names):
        spec = get_spec(name)
        candles = synth_candles(300, spec.start_price, spec.annual_vol * 100 / 16, i)
        chain = synth_chain(candles[-1].close, spec.strike_step, spec.days_to_expiry)
        out.append((name, candles, chain))
    return out


def time_deep(items) -> list[float]:
    """One full engine pass per instrument — what Hub._tick_one pays today."""
    costs = []
    for name, candles, chain in items:
        began = time.perf_counter()
        ind = eng.compute_indicators(candles, chain, price_change=0.0)
        eng.decide(candles, chain, ind, 0.0, False,
                   eng.classify_market(ind, False), False, None, spot=candles[-1].close)
        costs.append(time.perf_counter() - began)
        del name
    return costs


def time_scan(items) -> list[float]:
    costs = []
    for name, candles, _chain in items:
        inp = scanner.ScanInput(
            instrument=name, ltp=candles[-1].close, candles=tuple(candles[-90:]),
            data_age_ms=200.0, freshness="FRESH", data_quality_score=100.0,
        )
        began = time.perf_counter()
        scanner.scan_one(inp)
        costs.append(time.perf_counter() - began)
    return costs


async def measure_loop_lag(items, deep: bool) -> float:
    """Worst event-loop delay while one sweep runs the way the app runs it."""
    loop = asyncio.get_event_loop()
    worst = 0.0
    stop = False

    async def probe():
        nonlocal worst
        while not stop:
            t0 = time.perf_counter()
            await asyncio.sleep(0.01)
            worst = max(worst, (time.perf_counter() - t0) - 0.01)

    task = asyncio.ensure_future(probe())
    fn = time_deep if deep else time_scan
    await loop.run_in_executor(None, fn, items)
    stop = True
    await asyncio.sleep(0.02)
    task.cancel()
    return round(worst * 1000.0, 2)


def profile(size: int) -> dict:
    items = build_universe(size)
    deep = time_deep(items)
    scan = time_scan(items)
    deep_total = sum(deep)
    scan_total = sum(scan)
    lag_deep = asyncio.get_event_loop().run_until_complete(
        measure_loop_lag(items, True)
    )
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # The live loop ticks serially (each _tick_one awaits its executor call), so
    # a full sweep costs the sum, not the max.
    age_at_sweep_ms = deep_total * 1000.0
    return {
        "instruments": size,
        "deep_engine_ms_per_instrument": round(statistics.mean(deep) * 1000, 2),
        "deep_engine_p95_ms": round(sorted(deep)[int(len(deep) * 0.95) - 1] * 1000, 2),
        "deep_sweep_sec": round(deep_total, 3),
        "scanner_ms_per_instrument": round(statistics.mean(scan) * 1000, 3),
        "scanner_sweep_sec": round(scan_total, 4),
        "deep_over_scan_ratio": round(deep_total / scan_total, 1) if scan_total else None,
        "event_loop_worst_lag_ms_deep": lag_deep,
        "worst_price_age_after_full_sweep_ms": round(age_at_sweep_ms, 1),
        "freshness_after_full_sweep": (
            "FRESH" if age_at_sweep_ms <= FRESH_MS
            else "AGING" if age_at_sweep_ms <= AGING_MS else "STALE_OR_WORSE"
        ),
        "max_rss_mb": round(rss_kb / 1024.0, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--sizes", default=",".join(str(s) for s in SIZES))
    args = ap.parse_args()
    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    rows = []
    print(f"{'N':>4} {'deep/inst':>10} {'deep sweep':>11} {'scan/inst':>10} "
          f"{'scan sweep':>11} {'ratio':>6} {'loop lag':>9} {'freshness':>16}")
    for n in sizes:
        r = profile(n)
        rows.append(r)
        print(f"{r['instruments']:>4} {r['deep_engine_ms_per_instrument']:>9.2f}ms "
              f"{r['deep_sweep_sec']:>10.2f}s {r['scanner_ms_per_instrument']:>9.3f}ms "
              f"{r['scanner_sweep_sec']:>10.3f}s {str(r['deep_over_scan_ratio']):>6} "
              f"{r['event_loop_worst_lag_ms_deep']:>8.1f}ms "
              f"{r['freshness_after_full_sweep']:>16}")
    out = {
        "as_of": int(time.time()),
        "note": (
            "Own-CPU cost only: synthetic candles isolate our compute from broker "
            "latency. Sweep times assume the live loop's serial tick, which is what "
            "Hub._tick_one does today."
        ),
        "thresholds_ms": {"fresh": FRESH_MS, "aging": AGING_MS},
        "rows": rows,
    }
    if args.out:
        path = os.path.expanduser(args.out)
        with open(path, "w") as fh:
            json.dump(out, fh, indent=2)
        print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
