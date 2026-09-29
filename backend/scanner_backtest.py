"""Phase 4 Part W/X — does the scanner's score actually identify better
opportunity, measured causally?

Method (chronological, no future information anywhere):

* walk the 1-minute history forward one bar at a time;
* at bar ``t`` the scanner sees ONLY bars ``<= t`` — the same window the live
  service gives it;
* the forward outcome is then measured on bars ``> t`` with fixed, symmetric
  levels (ATR-based stop, 1.2R target) that are IDENTICAL for every bucket, so a
  bucket cannot win by being scored on easier terms;
* buckets are compared: every bar (the population the deep engine sees today)
  against the scanner's own OPPORTUNITY / WATCH / NO_OPPORTUNITY verdicts and
  against score deciles.

What this CANNOT answer: the cross-sectional "top 5 of 50 instruments" question.
That needs many instruments recorded simultaneously, and the archive holds two.
The honest substitute is the within-instrument bucket comparison below, and the
cross-sectional test stays open until a multi-instrument recorder has run.

Direction convention: the outcome is measured in the direction the scanner said
the move was going. A NO_OPPORTUNITY bar has no direction of its own, so it uses
the prevailing 5-bar drift — otherwise refused bars would be scored on a
different rule from accepted ones.

    python scanner_backtest.py --instrument CRUDEOIL --out ~/scanner_bt.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time

from app.analysis import scanner
from app.models import Candle

_DATA = "data/backtest/{}_ONE_MINUTE.jsonl"
_WINDOW = 90        # bars handed to the scanner (its own requirement)
_HORIZON = 30       # forward bars the outcome is measured over
_STOP_ATR = 0.8     # stop distance in ATR
_RR = 1.2           # target = _RR x stop, so one win pays 1.2 losses


def load(instrument: str, limit: int | None = None) -> list[Candle]:
    path = _DATA.format(instrument)
    out: list[Candle] = []
    with open(path) as fh:
        for line in fh:
            r = json.loads(line)
            out.append(Candle(time=int(r["time"]), open=r["open"], high=r["high"],
                              low=r["low"], close=r["close"],
                              volume=float(r.get("volume") or 0.0)))
            if limit and len(out) >= limit:
                break
    return out


def atr(candles: list[Candle], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(len(candles) - period, len(candles)):
        prev = candles[i - 1].close
        trs.append(max(candles[i].high - candles[i].low,
                       abs(candles[i].high - prev), abs(candles[i].low - prev)))
    return sum(trs) / len(trs) if trs else None


def outcome(fwd: list[Candle], entry: float, stop_dist: float, up: bool) -> dict:
    """Target-before-stop, MFE and MAE in R, on the forward bars only."""
    target = entry + _RR * stop_dist * (1 if up else -1)
    stop = entry - stop_dist * (1 if up else -1)
    mfe = mae = 0.0
    result = "OPEN"
    for c in fwd:
        fav = (c.high - entry) if up else (entry - c.low)
        adv = (entry - c.low) if up else (c.high - entry)
        mfe = max(mfe, fav)
        mae = max(mae, adv)
        hit_t = (c.high >= target) if up else (c.low <= target)
        hit_s = (c.low <= stop) if up else (c.high >= stop)
        if hit_t and hit_s:
            # Both touched inside one bar: 1-minute OHLC cannot say which came
            # first, so this counts as a loss. Assuming the win is how a
            # backtest flatters itself.
            result = "STOP"
            break
        if hit_t:
            result = "TARGET"
            break
        if hit_s:
            result = "STOP"
            break
    return {
        "result": result,
        "r": (_RR if result == "TARGET" else -1.0 if result == "STOP"
              else (mfe - mae) / stop_dist),
        "mfe_r": mfe / stop_dist,
        "mae_r": mae / stop_dist,
    }


def stats(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    rs = [r["r"] for r in rows]
    wins = [r for r in rows if r["result"] == "TARGET"]
    losses = [r for r in rows if r["result"] == "STOP"]
    gross_win = sum(r["r"] for r in wins)
    gross_loss = -sum(r["r"] for r in losses)
    return {
        "n": len(rows),
        "target_before_stop_pct": round(100.0 * len(wins) / len(rows), 2),
        "expectancy_r": round(statistics.mean(rs), 4),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "mfe_r_median": round(statistics.median(r["mfe_r"] for r in rows), 3),
        "mae_r_median": round(statistics.median(r["mae_r"] for r in rows), 3),
        "unresolved_pct": round(
            100.0 * sum(1 for r in rows if r["result"] == "OPEN") / len(rows), 2),
    }


def run(instrument: str, step: int, limit: int | None) -> dict:
    candles = load(instrument, limit)
    rows: list[dict] = []
    i = _WINDOW
    while i + _HORIZON < len(candles):
        window = candles[i - _WINDOW:i + 1]
        res = scanner.scan_one(scanner.ScanInput(
            instrument=instrument, ltp=window[-1].close, candles=tuple(window),
            # Replay data is by definition not a live feed; declaring it FRESH is
            # what lets the scanner score at all. It is stated, not hidden.
            data_age_ms=0.0, freshness="FRESH", data_quality_score=100.0,
        ))
        a = atr(window)
        if a and a > 0 and res.opportunity_score is not None:
            if res.direction == "UP":
                up = True
            elif res.direction == "DOWN":
                up = False
            else:
                up = window[-1].close >= window[-6].close
            out = outcome(candles[i + 1:i + 1 + _HORIZON], window[-1].close,
                          _STOP_ATR * a, up)
            out.update(score=res.opportunity_score, verdict=res.verdict,
                       classification=res.classification, ts=window[-1].time)
            rows.append(out)
        i += step

    rows.sort(key=lambda r: r["ts"])
    third = len(rows) // 3
    by_score = sorted(rows, key=lambda r: r["score"], reverse=True)
    n = len(rows)
    out = {
        "instrument": instrument,
        "bars_evaluated": n,
        "step_bars": step,
        "horizon_bars": _HORIZON,
        "levels": {"stop_atr": _STOP_ATR, "reward_risk": _RR},
        "ALL_BARS": stats(rows),
        "by_verdict": {
            v: stats([r for r in rows if r["verdict"] == v])
            for v in (scanner.OPPORTUNITY, scanner.WATCH, scanner.NO_OPPORTUNITY)
        },
        "by_classification": {
            c: stats([r for r in rows if r["classification"] == c])
            for c in sorted({r["classification"] for r in rows})
        },
        "top_score_slices": {
            "top_1_pct": stats(by_score[:max(1, n // 100)]),
            "top_5_pct": stats(by_score[:max(1, n // 20)]),
            "top_10_pct": stats(by_score[:max(1, n // 10)]),
            "top_20_pct": stats(by_score[:max(1, n // 5)]),
            "bottom_50_pct": stats(by_score[n // 2:]),
        },
        # Chronological thirds: the weights were never fitted, so this is a
        # stability check rather than a train/test split.
        "chronological": {
            "first_third": stats(rows[:third]),
            "second_third": stats(rows[third:2 * third]),
            "final_third_out_of_sample": stats(rows[2 * third:]),
        },
    }
    opp = [r for r in rows if r["verdict"] == scanner.OPPORTUNITY]
    if opp:
        out["false_positive_rate_pct"] = round(
            100.0 * sum(1 for r in opp if r["result"] == "STOP") / len(opp), 2)
    missed = [r for r in rows
              if r["verdict"] == scanner.NO_OPPORTUNITY and r["result"] == "TARGET"]
    out["missed_by_scanner_pct_of_all_targets"] = (
        round(100.0 * len(missed) / max(1, sum(1 for r in rows if r["result"] == "TARGET")), 2)
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="CRUDEOIL")
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    res = run(args.instrument, args.step, args.limit or None)
    print(json.dumps({k: v for k, v in res.items()
                      if k in ("instrument", "bars_evaluated", "ALL_BARS",
                               "by_verdict", "top_score_slices",
                               "false_positive_rate_pct")}, indent=2))
    if args.out:
        path = os.path.expanduser(args.out)
        with open(path, "w") as fh:
            json.dump({"as_of": int(time.time()), **res}, fh, indent=2)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
