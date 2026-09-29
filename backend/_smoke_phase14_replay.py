"""Phase 14 replay smoke: no-lookahead, exits, holdout split. RESEARCH ONLY.

Runs the real engine over synthetic sessions. What matters here is not the P&L of
made-up candles but that the harness cannot cheat: the engine must never see a
future bar, an ambiguous bar must resolve against the trade, nothing may be held
overnight, and the HTF override must always be put back.
"""
from __future__ import annotations

import datetime as dt
import math
import os
import random
import sys
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import settings  # noqa: E402
from app.engine import decision as eng  # noqa: E402
from app.models import Candle, Decision, Signal  # noqa: E402
from app.research.phase14 import paths, replay_spot, sweep  # noqa: E402
from app.research.phase14.coverage import IST  # noqa: E402

CHECKS = 0


def ok(cond: bool, what: str) -> None:
    global CHECKS
    CHECKS += 1
    assert cond, what


def session(day: dt.date, seed: int, drift: float, bars: int = 375) -> list[Candle]:
    rng = random.Random(seed)
    start = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    px = 24000.0 + 40.0 * math.sin(seed)
    out: list[Candle] = []
    for i in range(bars):
        px += rng.gauss(drift, 7.0)
        high = px + abs(rng.gauss(0, 4.0))
        low = px - abs(rng.gauss(0, 4.0))
        out.append(Candle(time=int((start + dt.timedelta(minutes=i)).timestamp()),
                          open=px, high=high, low=low, close=px, volume=1000.0))
    return out


def make_book(days: int = 12) -> list[Candle]:
    base = dt.date(2026, 3, 2)
    candles: list[Candle] = []
    for d in range(days):
        day = base + dt.timedelta(days=d)
        if day.weekday() >= 5:
            continue
        candles.extend(session(day, seed=d + 1, drift=0.5 if d % 2 else -0.4))
    return candles


def main() -> int:
    candles = make_book()
    ok(len(candles) > 3000, "need a few sessions of bars to replay")

    # --- no lookahead: the engine may only ever see bars up to the decision ---
    # Recording the last timestamp of each window is the check that matters: a
    # window whose newest bar is the bar being decided cannot contain the future,
    # whatever its length.
    seen: list[tuple[int, int]] = []
    real_compute = eng.compute_indicators

    def spy(window, chain, price_change=0.0, **kw):
        seen.append((len(window), int(window[-1].time)))
        return real_compute(window, chain, price_change=price_change, **kw)

    eng.compute_indicators = spy
    try:
        res = replay_spot.run("NIFTY", candles, factor=5)
    finally:
        eng.compute_indicators = real_compute
    ok(res["ok"], f"replay must run, got {res}")
    expected_ts = [int(c.time) for c in candles[replay_spot.WARMUP_BARS:]]
    ok([ts for _, ts in seen] == expected_ts,
       "each window must end exactly on the bar being decided, in order")
    ok(max(length for length, _ in seen) <= replay_spot.LOOKBACK_BARS,
       "the engine must not be shown more history than it gets live")
    ok(res["lookback_bars"] == replay_spot.LOOKBACK_BARS,
       "the replay must report the window size it used")

    trades = res["trades"]
    ok(res["decisions"] == len(candles) - replay_spot.WARMUP_BARS,
       "every bar after warm-up must produce a decision")
    ok(trades, "the engine must take at least one trade on this book")

    for t in trades:
        ok(t["exit_ts"] > t["entry_ts"], "a trade cannot exit on or before entry")
        ok(t["risk"] > 0, "risk must be positive")
        ok(t["mfe_r"] >= 0.0 >= t["mae_r"],
           f"MFE must be favourable and MAE adverse, got {t['mfe_r']}/{t['mae_r']}")
        ok(t["mfe_r"] + 1e-6 >= t["r"] >= t["mae_r"] - 1e-6,
           f"realised R must sit inside the excursion range, got {t['r']}")
        ok(t["session"] == replay_spot.session_of(t["exit_ts"]),
           f"no overnight holds allowed: {t['session']} -> {t['exit_ts']}")
        if t["exit_reason"] == replay_spot.EXIT_STOP:
            ok(round(t["r"], 2) == -1.0,
               f"a stop must be exactly -1R, got {t['r']}")
        if t["exit_reason"] == replay_spot.EXIT_TARGET:
            ok(t["r"] > 0, "a target must be a win")
    ok(len({t["entry_ts"] for t in trades}) == len(trades),
       "one entry per bar at most")

    # --- noise diagnostics: is the stop inside one candle's range? --------
    for t in trades:
        ok(t["bars_held"] >= 1, f"a trade must be held at least a bar, got {t}")
        ok(t["noise_points"] >= 0.0, "measured noise cannot be negative")
        if t["noise_points"] > 0:
            ok(abs(t["risk_over_noise"] - round(t["risk"] / t["noise_points"], 2))
               < 0.011,
               f"risk/noise must be the stop in candle-ranges, got {t}")
        ok(0 <= t["entry_minute_ist"] < 400,
           f"entry must be inside an IST session, got {t['entry_minute_ist']}")
        # The Research tab proposes gates on these scores, so every replayed
        # trade must carry them or every rule silently grades NOT_ENOUGH_DATA
        # and the tab's proposals would look untestable rather than untested.
        for field in ("trade_score", "opportunity_score", "risk_score",
                      "conviction_meter", "trap_prob", "fake_prob"):
            ok(field in t, f"replayed trade must record {field} for rule grading")
            if t[field] is not None:
                ok(0.0 <= float(t[field]) <= 100.0,
                   f"{field} must be a 0-100 score, got {t[field]}")
        ok("premium_health" not in t,
           "history has no option chain — a premium field here would be fabricated")
    summary_n = replay_spot.summarise([res])
    ok(summary_n["median_risk_over_noise"] > 0,
       "the summary must report how wide the stop is against market noise")
    next_bar = 100.0 * sum(1 for t in trades if t["bars_held"] == 1) / len(trades)
    ok(abs(summary_n["resolved_next_bar_pct"] - next_bar) < 0.05,
       f"next-bar resolution share must match the trades, got {summary_n}")
    inside = 100.0 * sum(1 for t in trades if t["risk_over_noise"] < 1.0) / len(trades)
    ok(abs(summary_n["stop_inside_one_bar_pct"] - inside) < 0.05,
       f"the share of stops inside one candle must be reported, got {summary_n}")

    # --- a long run must report where it is, not sit silent ---------------
    beats: list[tuple[int, int, int]] = []
    res_p = replay_spot.run("NIFTY", candles, factor=5,
                            progress=lambda d, t, n: beats.append((d, t, n)),
                            progress_every=10)
    ok(res_p["trades"] == trades,
       "progress reporting must not change a single replayed trade")
    ok(len(beats) == res_p["decisions"] // 10,
       f"progress must fire once per 10 decisions, got {len(beats)}")
    ok(all(t == len(candles) and 0 < d <= t for d, t, _ in beats),
       f"each beat must report a real position in the book, got {beats[:3]}")
    ok([n for _, _, n in beats] == sorted(n for _, _, n in beats),
       "the reported trade count must never go backwards")
    ok(replay_spot.run("NIFTY", candles, factor=5,
                       progress=None)["trades"] == trades,
       "no progress callback must remain the default, silent behaviour")

    # overlapping trades would double-count the same market
    ordered = sorted(trades, key=lambda t: t["entry_ts"])
    ok(all(a["exit_ts"] <= b["entry_ts"] for a, b in zip(ordered, ordered[1:])),
       "trades must not overlap")

    # --- ambiguous bar resolves against the trade -------------------------
    long_trade = {"side": replay_spot.LONG, "stop": 99.0, "target": 101.0}
    both = Candle(time=0, open=100.0, high=101.5, low=98.5, close=100.0, volume=1.0)
    ok(replay_spot._exit_on_bar(long_trade, both)[0] == replay_spot.EXIT_STOP,
       "a bar covering both levels must be scored as the stop")
    short_trade = {"side": replay_spot.SHORT, "stop": 101.0, "target": 99.0}
    ok(replay_spot._exit_on_bar(short_trade, both)[0] == replay_spot.EXIT_STOP,
       "same for a short")
    ok(replay_spot._exit_on_bar(
        long_trade,
        Candle(time=0, open=100.0, high=100.2, low=99.8, close=100.0, volume=1.0)
    ) is None, "an inside bar must not resolve a trade")

    # --- the HTF override must always be restored ------------------------
    live = settings.htf_factor
    with replay_spot.htf_factor(15):
        ok(settings.htf_factor == 15, "override must apply inside the block")
    ok(settings.htf_factor == live, "override must be restored on exit")
    try:
        with replay_spot.htf_factor(30):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    ok(settings.htf_factor == live, "override must be restored after an exception")

    # --- factor actually changes what the engine sees --------------------
    fast = replay_spot.run("NIFTY", candles, factor=3)
    slow = replay_spot.run("NIFTY", candles, factor=30)
    ok(fast["htf_factor"] == 3 and slow["htf_factor"] == 30,
       "runs must report the factor they used")
    ok((len(fast["trades"]), fast["buys"]) != (len(slow["trades"]), slow["buys"]),
       "3-min and 30-min bias must not produce identical books")
    ok(settings.htf_factor == live, "the live setting must be untouched after a sweep")

    # --- summary arithmetic ----------------------------------------------
    summary = replay_spot.summarise([res])
    ok(summary["trades"] == len(trades), "summary must count every trade")
    ok(abs(summary["net_r"] - round(sum(t["r"] for t in trades), 2)) < 0.01,
       "net R must be the sum of trade R")
    ok(0.0 <= summary["win_rate"] <= 100.0, "win rate must be a percentage")
    ok(summary["max_drawdown_r"] <= 0.0, "drawdown is reported as a negative R")
    ok(summary["sessions_traded"] >= 1, "summary must count sessions traded")
    ok(summary["expectancy_r"] == summary["avg_r"],
       "expectancy and average R must be the same number, reported once each")

    # frequency must be measured, not implied: a config that fires rarely has to
    # look rare next to its per-trade edge
    ok(summary["calendar_years"] > 0, "the replayed span must be reported in years")
    span_days = (dt.date.fromisoformat(summary["last_session"])
                 - dt.date.fromisoformat(summary["first_session"])).days + 1
    expected_rate = summary["trades"] / (span_days / 365.25)
    ok(abs(summary["signals_per_year"] - expected_rate) <= 1.0,
       f"signals/year must be trades over the calendar span, got "
       f"{summary['signals_per_year']} vs {expected_rate:.1f}")
    ok(abs(summary["signals_per_month"] * 12.0
           - summary["signals_per_year"]) <= 1.0,
       "monthly and yearly signal rates must agree")
    ok(0.0 <= summary["session_participation_pct"] <= 100.0,
       "participation must be a percentage of replayed sessions")
    ok(summary["trades_per_day"] <= summary["trades_per_session_traded"] + 1e-9,
       "per-replayed-day rate cannot exceed the per-traded-day rate")

    gross_win = sum(t["r"] for t in trades if t["r"] > 0)
    gross_loss = -sum(t["r"] for t in trades if t["r"] < 0)
    if gross_loss > 0:
        ok(abs(summary["profit_factor"] - round(gross_win / gross_loss, 2)) < 0.02,
           "profit factor must be gross win over gross loss")
    ok(replay_spot._profit_factor([0.5, 1.0]) is None,
       "profit factor must be withheld rather than infinite when nothing lost")
    ok(summary["p90_minutes_hold"] >= summary["median_minutes_hold"],
       "the p90 hold cannot be shorter than the median")
    ok(replay_spot._percentile([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0],
                               90.0) == 9.0,
       "p90 of 1..10 must be 9")
    ok(replay_spot._percentile([], 90.0) is None, "an empty percentile must be None")
    ok(summary["median_mfe_r"] >= 0.0 >= summary["median_mae_r"],
       "median MFE/MAE must keep their signs")
    ok("gross of option premium" in summary["note"],
       "the summary must say what the R excludes")
    ok(replay_spot.summarise([])["trades"] == 0, "an empty summary must not raise")

    # --- holdout split is by date, oldest in-sample ----------------------
    early, late, span = sweep.split_sessions(candles, 0.7)
    ok(early and late, "the split must produce both halves")
    early_days = {replay_spot.session_of(c.time) for c in early}
    late_days = {replay_spot.session_of(c.time) for c in late}
    ok(not (early_days & late_days), "a session may not appear in both halves")
    ok(max(early_days) < min(late_days),
       "the holdout must be strictly later than the in-sample period")
    ok(span["in_sample_sessions"] + span["holdout_sessions"]
       == len(early_days | late_days), "the split must account for every session")
    ok(len(early) + len(late) == len(candles), "no bar may be lost in the split")

    # --- --years slices by session, keeping the most recent history -------
    class _Stub:
        def candles(self, _instrument: str) -> list[dict]:
            return [{"ts": c.time, "open": c.open, "high": c.high,
                     "low": c.low, "close": c.close, "volume": c.volume}
                    for c in candles]

    all_days = sorted({replay_spot.session_of(c.time) for c in candles})
    cutoff = all_days[len(all_days) // 2]
    sliced = sweep.load_candles("NIFTY", _Stub(), since=cutoff)
    ok(len(sweep.load_candles("NIFTY", _Stub())) == len(candles),
       "no slice must load the whole stored history")
    ok(sliced and len(sliced) < len(candles),
       f"a since-slice must drop the oldest sessions, got {len(sliced)}")
    ok(min(replay_spot.session_of(c.time) for c in sliced) == cutoff,
       "the slice must start exactly on the requested session, not mid-day")
    ok(max(replay_spot.session_of(c.time) for c in sliced) == all_days[-1],
       "the slice must keep the newest session — recency is the point")

    # --- short data is refused, not guessed ------------------------------
    ok(replay_spot.run("NIFTY", candles[:50])["ok"] is False,
       "too few bars must be refused")

    # --- the candidate pool must contain what the engine REFUSED ----------
    # A gate cannot be graded on the taken book alone: the engine scores before it
    # emits a BUY, so the failing arm was never recorded. The pool exists to hold
    # both arms, and these checks pin that it really does.
    pooled = replay_spot.pool("NIFTY", candles)
    ok(pooled["ok"] and pooled["candidates"] > 0, "the candidate pool must run")
    ok(pooled["candidates_refused"] > 0,
       "the pool must contain candidates the engine refused, or it is just the "
       "taken book again and no gate can be compared")
    ok(pooled["candidates_taken"] > 0,
       "the pool must also contain the trades the engine did take")
    ok(pooled["candidates_taken"] + pooled["candidates_refused"]
       == pooled["candidates"], "every candidate must be taken or refused")
    ok(all(c["taken"] is (c["signal"] == "BUY") for c in pooled["trades"]),
       "'taken' must mean exactly 'the engine said BUY here'")
    ok({c["signal"] for c in pooled["trades"]} - {"BUY"},
       "the pool must record non-BUY signals, not silently drop them")

    # Both arms must be measured with the SAME stop rule, or the comparison
    # measures the harness rather than the gate.
    for c in pooled["trades"]:
        ok(c["risk"] > 0, "every candidate must carry a positive risk")
        ok(("r" in c) and c["exit_reason"], "every candidate must be resolved")
        side_ok = (c["stop"] < c["entry"] if c["side"] == "LONG"
                   else c["stop"] > c["entry"])
        ok(side_ok, "the stop must sit on the losing side of the entry")
    taken_rn = [c["risk"] / c["noise_points"] for c in pooled["trades"]
                if c["taken"] and c["noise_points"]]
    refused_rn = [c["risk"] / c["noise_points"] for c in pooled["trades"]
                  if not c["taken"] and c["noise_points"]]
    if taken_rn and refused_rn:
        ok(abs(median(taken_rn) - median(refused_rn)) < 1.0,
           "refused candidates must get a comparable stop to taken ones — a "
           "different stop rule per arm would fake every gate's lift")

    # The reconstructed stop must equal the engine's published one wherever the
    # engine published one at all.
    checked_stops = 0
    for i in range(replay_spot.WARMUP_BARS, len(candles), 97):
        window = candles[max(0, i + 1 - replay_spot.LOOKBACK_BARS): i + 1]
        snap = eng.compute_indicators(
            window, [], price_change=candles[i].close - candles[i - 1].close)
        status = eng.classify_market(snap, False)
        dec, _ = eng.decide(window, [], snap, 0.0, False, status, False, None,
                            spot=candles[i].close)
        if dec.underlying_stop is None:
            continue
        side = ("LONG" if dec.underlying_stop < candles[i].close else "SHORT")
        mine = replay_spot._harness_stop(snap, status, side, candles[i].close)
        ok(mine is not None and abs(mine - float(dec.underlying_stop)) <= 0.05,
           f"the pool's stop must reproduce the engine's own stop exactly, got "
           f"{mine} vs {dec.underlying_stop}")
        checked_stops += 1
    ok(checked_stops > 0, "the stop reconstruction must actually be exercised")

    # No lookahead: a candidate is entered at its bar's close and every exit is
    # dated after entry.
    ok(all(c["exit_ts"] >= c["entry_ts"] for c in pooled["trades"]),
       "no candidate may exit before it was entered")
    ok(all(c["bars_held"] >= 1 for c in pooled["trades"]),
       "a candidate must be held at least one bar")

    # Sampling must not silently become every bar: overlapping near-duplicates
    # would make each arm look far more precise than it is.
    strides = {c["entry_index"] % replay_spot.CANDIDATE_STRIDE
               for c in pooled["trades"]}
    ok(len(strides) == 1,
       "candidates must be sampled on a fixed stride, not on every bar")
    ok(replay_spot.pool("NIFTY", candles[:50])["ok"] is False,
       "the pool must refuse too-few bars as well")

    # Every candidate carries the conviction reading under both the current and
    # the pre-relabel key, so a pool graded on either engine version reads it.
    ok(all(c["conviction_meter"] == c["win_probability"]
           for c in pooled["trades"]),
       "the conviction reading is written under both keys, with one value")
    stub = dict(signal=Signal.WAIT, confidence=0.0, signal_strength=0.0,
                trade_quality="LOW")
    ok(replay_spot._conviction(
        Decision(conviction_meter=71.5, **stub)) == 71.5,
       "the conviction reading is read from the relabelled field")
    ok(replay_spot._conviction(Decision(**stub)) is None,
       "an absent conviction reading stays absent rather than becoming a zero")

    # Every trade must carry the path-simulated alternative exits, because the
    # exit study silently falls back to excursion estimates without them.
    ok(all(t["alt_exits_basis"] == paths.PATH_MEASURED
           and set(t["alt_exits"]) == set(paths.names())
           for t in pooled["trades"]),
       "every candidate carries a path-simulated outcome for every variant")
    ok(all(t["alt_exits"][f"target_at_{lvl}R"] in (lvl, -1.0, t["r"])
           for t in pooled["trades"] for lvl in paths.TARGET_LEVELS),
       "an earlier target fills at its level, stops out, or rides to the close")
    ok(all(t["mfe_r"] >= 0.5 for t in pooled["trades"]
           if t["alt_exits"]["breakeven_after_0.5R"] == 0.0 and t["r"] != 0.0),
       "a breakeven scratch requires the trade to have reached its trigger")
    ok(all(min(t["r"], -1.0) <= t["alt_exits"][name] <= max(t["mfe_r"], 0.0) + 1e-6
           for t in pooled["trades"] for name in paths.names()),
       "no variant may print an outcome the trade's own excursion never offered")
    ok(all(t["path_facts"]["bars_to_peak"] <= t["bars_held"] + 1
           for t in pooled["trades"]),
       "the peak cannot be recorded on a bar after the trade closed")

    print(f"checked {CHECKS}")
    print("phase 14 replay smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
