"""Where, if anywhere, the replayed edge lives.

The first Phase 14 result was that the engine's *average* replayed trade has no
edge: +0.017R in-sample became -0.007R in holdout at the live HTF factor, on
~2,000 holdout trades, at 15-20 trades a day. Changing the timeframe did not fix
that -- 15-minute HTF looked better in-sample and was equally negative out of
sample, which is what a holdout is for.

So the question this module asks is not "which timeframe" but "is the engine
taking a mixture of a few good setups and a great many indifferent ones?". It
slices the replayed trades by the properties the engine already knew at entry --
confidence, regime, trigger, HTF agreement, time of day, and how wide the stop
was relative to the market's own noise -- and reports each slice in-sample and in
holdout separately.

Two honesty rules are built in, because this is exactly the machinery that
produces convincing nonsense:

* **Only entry-time properties are sliced.** Nothing here may condition on how a
  trade turned out; every field used was on the decision before the outcome was
  known, so a surviving cohort is a rule the live engine could actually apply.
* **Multiple comparisons are counted and reported.** Slicing ~40 cohorts at a 5%
  false-positive rate is expected to produce ~2 winners from pure chance, so the
  report states how many cohorts were tested. A cohort that clears the bar is a
  *hypothesis to confirm on the full history and on other instruments*, never a
  finding, and never a gate change on its own.
"""
from __future__ import annotations

from collections.abc import Callable
from statistics import median

# Trades a cohort needs in BOTH periods before its numbers are quoted. Cohort
# slicing multiplies the number of small samples, which is where noise wins.
MIN_COHORT_TRADES = 100

# Expectancy a cohort must clear in holdout to be worth confirming. Zero is not
# enough: the live trade pays a spread, so a cohort that is merely non-negative
# on the underlying still loses money as an option.
MIN_HOLDOUT_EXPECTANCY = 0.05


def _confidence(t: dict) -> str:
    c = t.get("confidence")
    if c is None:
        return "conf:unknown"
    lo = int(c // 10) * 10
    return f"conf:{lo}-{lo + 9}"


def _time_of_day(t: dict) -> str:
    m = t.get("entry_minute_ist")
    if m is None:
        return "tod:unknown"
    if m < 30:
        return "tod:09:15-09:45 open"
    if m < 75:
        return "tod:09:45-10:30"
    if m < 165:
        return "tod:10:30-12:00"
    if m < 255:
        return "tod:12:00-13:30"
    if m < 345:
        return "tod:13:30-15:00"
    return "tod:15:00-close"


def _risk_over_noise(t: dict) -> str:
    """How wide the stop was in units of a normal 1-minute candle range."""
    v = t.get("risk_over_noise")
    if v is None:
        return "stop/noise:unknown"
    if v < 1.0:
        return "stop/noise:<1 (inside one candle)"
    if v < 2.0:
        return "stop/noise:1-2"
    if v < 4.0:
        return "stop/noise:2-4"
    return "stop/noise:>=4"


def _htf_agreement(t: dict) -> str:
    trend = (t.get("htf_trend") or "NONE").upper()
    side = t["side"]
    if trend in ("UP", "CE", "LONG"):
        return "htf:with trend" if side == "LONG" else "htf:against trend"
    if trend in ("DOWN", "PE", "SHORT"):
        return "htf:with trend" if side == "SHORT" else "htf:against trend"
    return "htf:flat/none"


def _htf_strength(t: dict) -> str:
    s = t.get("htf_strength")
    if s is None:
        return "htf_strength:unknown"
    lo = int(float(s) // 20) * 20
    return f"htf_strength:{lo}-{lo + 19}"


DIMENSIONS: dict[str, Callable[[dict], str]] = {
    "confidence": _confidence,
    "regime": lambda t: f"regime:{t.get('regime') or 'UNKNOWN'}",
    "entry_trigger": lambda t: f"trigger:{t.get('entry_trigger') or 'NONE'}",
    "htf_agreement": _htf_agreement,
    "htf_strength": _htf_strength,
    "time_of_day": _time_of_day,
    "stop_vs_noise": _risk_over_noise,
    "side": lambda t: f"side:{t['side']}",
    "reward_risk": lambda t: (
        f"rr:{'<1' if t['reward_risk'] < 1 else '1-2' if t['reward_risk'] < 2 else '>=2'}"),
}


def _stats(trades: list[dict]) -> dict:
    rs = [t["r"] for t in trades]
    if not rs:
        return {"trades": 0, "expectancy_r": 0.0, "win_rate": 0.0,
                "profit_factor": None, "net_r": 0.0, "median_mfe_r": None}
    gain = sum(r for r in rs if r > 0)
    loss = -sum(r for r in rs if r < 0)
    return {
        "trades": len(rs),
        "expectancy_r": round(sum(rs) / len(rs), 3),
        "win_rate": round(100.0 * sum(1 for r in rs if r > 0) / len(rs), 1),
        "profit_factor": round(gain / loss, 2) if loss > 0 else None,
        "net_r": round(sum(rs), 2),
        "median_mfe_r": round(median([t["mfe_r"] for t in trades]), 3),
    }


def study(in_sample: list[dict], holdout: list[dict], *,
          min_trades: int = MIN_COHORT_TRADES,
          min_holdout_expectancy: float = MIN_HOLDOUT_EXPECTANCY) -> dict:
    """Slice both periods by entry-time properties and report each cohort."""
    dimensions: list[dict] = []
    tested = 0
    candidates: list[dict] = []

    for dim, key in DIMENSIONS.items():
        early: dict[str, list[dict]] = {}
        late: dict[str, list[dict]] = {}
        for trade in in_sample:
            early.setdefault(key(trade), []).append(trade)
        for trade in holdout:
            late.setdefault(key(trade), []).append(trade)

        rows = []
        for label in sorted(early.keys() | late.keys()):
            a, b = _stats(early.get(label, [])), _stats(late.get(label, []))
            enough = a["trades"] >= min_trades and b["trades"] >= min_trades
            survives = (enough and a["expectancy_r"] > 0
                        and b["expectancy_r"] >= min_holdout_expectancy)
            if enough:
                tested += 1
            row = {"cohort": label, "in_sample": a, "holdout": b,
                   "enough_trades": enough, "survives_holdout": survives,
                   "share_of_holdout_pct": (
                       round(100.0 * b["trades"] / len(holdout), 1)
                       if holdout else 0.0)}
            rows.append(row)
            if survives:
                candidates.append({"dimension": dim, **row})

        dimensions.append({"dimension": dim, "cohorts": rows})

    candidates.sort(key=lambda r: r["holdout"]["expectancy_r"], reverse=True)
    # At a 5% false-positive rate, this many cohorts are expected to look good by
    # chance alone. If the candidate count is at or below it, the honest reading
    # is "nothing here", not "we found something".
    by_chance = round(0.05 * tested, 1)
    return {
        "in_sample_trades": len(in_sample),
        "holdout_trades": len(holdout),
        "min_cohort_trades": min_trades,
        "min_holdout_expectancy": min_holdout_expectancy,
        "cohorts_tested": tested,
        "expected_false_positives": by_chance,
        "candidates": candidates,
        "verdict": (
            "no cohort survived the holdout — the engine's problem is not which "
            "subset it takes" if not candidates else
            f"{len(candidates)} cohort(s) survived against ~{by_chance} expected "
            "by chance — treat as hypotheses to confirm on full history and other "
            "instruments, not as gate changes"),
        "dimensions": dimensions,
        "note": ("Cohorts are cut on entry-time properties only, so a surviving "
                 "rule is one the live engine could apply. R is on the underlying, "
                 "gross of premium and spread."),
    }
