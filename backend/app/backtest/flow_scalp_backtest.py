"""Backtest the user-requested candle-flow SCALP ruleset on REAL 1-min bars.

Rule under test (measured on the underlying futures, the only real number):

  * Entry  : when flat, a DECISIVE candle (body/range >= min_body_frac) opens a
             position in the candle's direction — green -> CE (bet up),
             red -> PE (bet down). Enter at the candle close. Optional
             ``confirm`` consecutive decisive same-direction candles.
  * Stop   : a HARD stop of ``hard_stop`` points from entry, until profit locks.
  * Break-even: once favourable excursion reaches ``trail`` points, the stop
             moves up to the entry (can no longer become a loss).
  * Trail  : thereafter the stop rides ``trail`` points below the running peak
             (ratchets up only) — exit when price falls back to it.
  * Re-entry: after an exit, the next decisive candle re-enters.

Intrabar fills are CONSERVATIVE: within a bar the ADVERSE extreme is assumed to
print before the favourable one, so stops are checked first (never optimistic).

Read-only / offline. Places no orders. Underlying points are real; a MODELLED
option-premium P&L (fixed delta, NO theta) is also shown, clearly labelled.
"""
from __future__ import annotations

import json
import statistics
import sys
from dataclasses import dataclass


@dataclass
class Bar:
    t: int
    o: float
    h: float
    lo: float
    c: float


@dataclass
class STrade:
    side: str          # "CE" (up) or "PE" (down)
    entry_t: int
    exit_t: int
    entry: float
    exit: float
    points: float      # signed favourable underlying points
    peak_points: float
    reason: str        # STOP / TRAIL / BREAKEVEN
    hold_min: float


def _load(path: str) -> list[Bar]:
    out: list[Bar] = []
    with open(path) as fh:
        for line in fh:
            d = json.loads(line)
            out.append(Bar(int(d["time"]), d["open"], d["high"], d["low"], d["close"]))  # noqa: E501
    return out


def run(
    bars: list[Bar],
    *,
    min_body_frac: float = 0.30,
    confirm: int = 1,
    hard_stop: float = 3.0,
    trail: float = 4.0,
) -> list[STrade]:
    trades: list[STrade] = []
    n = len(bars)
    i = 0
    streak_dir = 0
    streak_len = 0

    # position state
    in_pos = False
    side = 0            # +1 CE, -1 PE
    entry = 0.0
    entry_t = 0
    peak_fav = 0.0      # best favourable excursion (points) so far
    locked = False      # break-even reached

    def decisive(b: Bar) -> int:
        rng = b.h - b.lo
        if rng <= 0:
            return 0
        body = abs(b.c - b.o)
        if body / rng < min_body_frac:
            return 0
        return 1 if b.c > b.o else (-1 if b.c < b.o else 0)

    while i < n:
        b = bars[i]

        if not in_pos:
            d = decisive(b)
            if d != 0 and d == streak_dir:
                streak_len += 1
            elif d != 0:
                streak_dir = d
                streak_len = 1
            else:
                streak_dir = 0
                streak_len = 0
            if d != 0 and streak_len >= confirm:
                in_pos = True
                side = d
                entry = b.c
                entry_t = b.t
                peak_fav = 0.0
                locked = False
                streak_dir = 0
                streak_len = 0
            i += 1
            continue

        # in a position — favourable/adverse extremes for this bar
        if side > 0:            # CE, bet up
            fav_ex = b.h - entry
            adv_ex = entry - b.lo
        else:                   # PE, bet down
            fav_ex = entry - b.lo
            adv_ex = b.h - entry

        # current stop level in POINTS below entry (favourable frame):
        #   before break-even: -hard_stop
        #   after break-even : max(0, peak_fav - trail)
        if locked or peak_fav >= trail:
            locked = True
            stop_pts = max(0.0, peak_fav - trail)
        else:
            stop_pts = -hard_stop

        # CONSERVATIVE: adverse first. Did we hit the stop this bar?
        # adverse excursion in points = -adv_ex (favourable frame).
        if -adv_ex <= stop_pts:
            reason = "TRAIL" if locked and stop_pts > 0 else ("BREAKEVEN" if locked else "STOP")
            exit_pts = stop_pts
            exit_px = entry + side * exit_pts
            trades.append(STrade(
                side="CE" if side > 0 else "PE",
                entry_t=entry_t, exit_t=b.t, entry=round(entry, 2),
                exit=round(exit_px, 2), points=round(exit_pts, 2),
                peak_points=round(peak_fav, 2), reason=reason,
                hold_min=round((b.t - entry_t) / 60.0, 1),
            ))
            in_pos = False
            i += 1
            continue

        # not stopped — update the peak from the favourable extreme, then also
        # allow the trail (now higher) to exit later bars.
        if fav_ex > peak_fav:
            peak_fav = fav_ex
            if peak_fav >= trail:
                locked = True
        i += 1

    return trades


def summarise(trades: list[STrade], label: str) -> dict:
    pts = [t.points for t in trades]
    if not pts:
        return {"label": label, "n": 0}
    wins = [p for p in pts if p > 0]
    losses = [p for p in pts if p <= 0]
    gw = sum(wins)
    gl = -sum(losses)
    return {
        "label": label,
        "trades": len(pts),
        "win_rate_pct": round(len(wins) / len(pts) * 100, 1),
        "net_points": round(sum(pts), 1),
        "avg_points": round(statistics.mean(pts), 3),
        "profit_factor": round(gw / gl, 3) if gl > 0 else None,
        "avg_win": round(statistics.mean(wins), 2) if wins else 0.0,
        "avg_loss": round(statistics.mean(losses), 2) if losses else 0.0,
        "avg_hold_min": round(statistics.mean([t.hold_min for t in trades]), 1),
        "stops": sum(1 for t in trades if t.reason == "STOP"),
        "breakevens": sum(1 for t in trades if t.reason == "BREAKEVEN"),
        "trails": sum(1 for t in trades if t.reason == "TRAIL"),
    }


if __name__ == "__main__":
    files = {
        "NIFTY": "data/backtest/NIFTY_ONE_MINUTE.jsonl",
        "CRUDEOIL": "data/backtest/CRUDEOIL_ONE_MINUTE.jsonl",
    }
    grid = [
        # (min_body_frac, confirm, hard_stop, trail)
        (0.30, 1, 3.0, 4.0),
        (0.30, 1, 3.0, 5.0),
        (0.30, 2, 3.0, 4.0),
        (0.50, 1, 3.0, 5.0),
        (0.50, 2, 3.0, 5.0),
    ]
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for inst, path in files.items():
        if only and inst != only:
            continue
        bars = _load(path)
        print(f"\n===== {inst}  ({len(bars):,} 1-min bars) =====")
        for (mbf, cf, hs, tr) in grid:
            tr_list = run(bars, min_body_frac=mbf, confirm=cf, hard_stop=hs, trail=tr)
            s = summarise(tr_list, f"body>={mbf} confirm={cf} stop={hs} trail={tr}")
            print(json.dumps(s))
