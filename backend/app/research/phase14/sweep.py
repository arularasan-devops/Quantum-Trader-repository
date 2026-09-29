"""HTF-factor comparison with a holdout. RESEARCH ONLY.

The question this exists to answer is the one that cannot be argued, only
measured: is the engine's 5-minute higher-timeframe bias the right one for these
instruments, or would 3 / 15 / 30 minutes have decided better?

Two rules make the answer worth reading:

* **the holdout is split by date, not by trade.** The later fraction of sessions
  is held back, so a configuration cannot win by fitting the same days it is
  scored on. Shuffled splits leak: two trades minutes apart on one day are the
  same market;
* **the winner is the configuration that survives the holdout**, and a
  configuration whose in-sample and holdout results disagree is reported as
  unstable rather than quietly averaged. Sweeping four factors over years of
  candles will always produce a best curve; that curve is only evidence if it
  repeats out of sample;
* **ranking is on R per year, not R per trade.** +0.30 R on one signal a day is
  worth less than +0.20 R on twenty, so the report shows expectancy and frequency
  side by side and ranks on the product — while still refusing any configuration
  whose per-trade expectancy is negative.

Nothing here writes to a production setting. The comparison runs the engine at a
temporary ``htf_factor`` and puts the live value back.
"""
from __future__ import annotations

from collections.abc import Callable

from app.config import settings
from app.models import Candle
from app.research.phase14 import replay_spot
from app.research.phase14.coverage import session_of
from app.research.phase14.spot_store import SpotStore

# The factors worth asking about: 3-min (faster than live), 5-min (live), 15-min
# and 30-min (the upper rows of the timeframe table).
DEFAULT_FACTORS = (3, 5, 15, 30)

# Fraction of sessions, oldest first, used to choose. The rest is holdout.
IN_SAMPLE_FRACTION = 0.7

# Minimum trades before a configuration's numbers are treated as a result rather
# than noise, in-sample and in holdout alike.
MIN_TRADES = 30


def load_candles(instrument: str, st: SpotStore, *,
                 since: str | None = None) -> list[Candle]:
    """Stored candles as engine candles, optionally from an IST session onward."""
    return [
        Candle(time=int(r["ts"]), open=r["open"], high=r["high"], low=r["low"],
               close=r["close"], volume=r["volume"] or 0.0)
        for r in st.candles(instrument)
        if r["open"] is not None and r["close"] is not None
        and (since is None or session_of(int(r["ts"])) >= since)
    ]


def split_sessions(candles: list[Candle],
                   fraction: float = IN_SAMPLE_FRACTION
                   ) -> tuple[list[Candle], list[Candle], dict]:
    """Split by IST session, oldest sessions in-sample, newest held out."""
    sessions = sorted({session_of(c.time) for c in candles})
    if len(sessions) < 2:
        return candles, [], {"in_sample_sessions": len(sessions),
                             "holdout_sessions": 0}
    cut = max(1, int(len(sessions) * fraction))
    in_sample_days = set(sessions[:cut])
    boundary = sessions[cut]
    return (
        [c for c in candles if session_of(c.time) in in_sample_days],
        [c for c in candles if session_of(c.time) not in in_sample_days],
        {"in_sample_sessions": cut,
         "holdout_sessions": len(sessions) - cut,
         "in_sample_first": sessions[0],
         "in_sample_last": sessions[cut - 1],
         "holdout_first": boundary,
         "holdout_last": sessions[-1]},
    )


def _r_per_year(leg: dict) -> float:
    """Expectancy times frequency: the edge a configuration actually delivers."""
    return round(leg["expectancy_r"] * leg["signals_per_year"], 1)


def _leg(instruments: list[str], candles: dict[str, list[Candle]],
         factor: int, label: str,
         report: Callable[[str], None] | None = None) -> dict:
    runs = []
    for name in instruments:
        if not candles.get(name):
            continue

        def note(done: int, total: int, trades: int, _n: str = name) -> None:
            if report is not None:
                report(f"    htf={factor} {label} {_n}: bar {done}/{total} "
                       f"({done * 100 // max(1, total)}%), {trades} trades")

        runs.append(replay_spot.run(name, candles[name], factor=factor,
                                    progress=note))
        if report is not None:
            report(f"    htf={factor} {label} {name}: done, "
                   f"{len(runs[-1].get('trades') or [])} trades")
    return replay_spot.summarise(runs)


def compare(instruments: list[str], st: SpotStore, *,
            factors: tuple[int, ...] = DEFAULT_FACTORS,
            fraction: float = IN_SAMPLE_FRACTION,
            min_trades: int = MIN_TRADES,
            since: str | None = None,
            report: Callable[[str], None] | None = None) -> dict:
    """Replay every factor over the same history and report both halves."""
    in_sample: dict[str, list[Candle]] = {}
    holdout: dict[str, list[Candle]] = {}
    spans: dict[str, dict] = {}
    for name in instruments:
        candles = load_candles(name, st, since=since)
        early, late, span = split_sessions(candles, fraction)
        in_sample[name] = early
        holdout[name] = late
        spans[name] = span

    live = int(settings.htf_factor)
    results: list[dict] = []
    for factor in factors:
        if report is not None:
            report(f"  htf_factor={factor}: replaying")
        early = _leg(instruments, in_sample, factor, "in-sample", report)
        late = _leg(instruments, holdout, factor, "holdout", report)
        agrees = (early["trades"] >= min_trades and late["trades"] >= min_trades
                  and (early["expectancy_r"] > 0) == (late["expectancy_r"] > 0))
        results.append({
            "htf_factor": factor,
            "is_live_setting": factor == live,
            "in_sample": early,
            "holdout": late,
            "in_sample_r_per_year": _r_per_year(early),
            "holdout_r_per_year": _r_per_year(late),
            "enough_trades": early["trades"] >= min_trades
            and late["trades"] >= min_trades,
            "stable": agrees,
        })

    ranked = [r for r in results
              if r["stable"] and r["holdout"]["expectancy_r"] > 0]
    ranked.sort(key=lambda r: r["holdout_r_per_year"], reverse=True)
    best = ranked[0]["htf_factor"] if ranked else None
    return {
        "live_htf_factor": live,
        "factors": list(factors),
        "since": since,
        "min_trades": min_trades,
        "spans": spans,
        "results": results,
        "best_holdout_factor": best,
        "verdict": _verdict(best, live, ranked),
    }


def _verdict(best: int | None, live: int, ranked: list[dict]) -> str:
    if best is None:
        return ("No configuration cleared the holdout with enough trades and a "
                "positive expectancy — the sweep is not evidence to change "
                f"htf_factor from {live}.")
    if best == live:
        return (f"The live setting (htf_factor={live}) is the best of the tested "
                f"configurations out of sample; nothing to change.")
    top = ranked[0]
    gap = top["holdout"]["expectancy_r"]
    rate = top["holdout"]["signals_per_year"]
    return (f"htf_factor={best} led out of sample at {gap:+.3f} R/trade over "
            f"{rate:.0f} signals/year ({top['holdout_r_per_year']:+.1f} R/year) "
            f"against the live {live}. Underlying direction only — it says "
            f"nothing about the option that would have been bought, so it is a "
            f"candidate for review, not a change to make on this evidence alone.")
