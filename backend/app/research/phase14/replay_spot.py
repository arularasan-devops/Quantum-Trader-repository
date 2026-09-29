"""Replay the live engine over underlying history. RESEARCH ONLY.

This grades the half of the tool that multi-year candles can honestly grade:
**market direction and timing**. It calls the same `compute_indicators` /
`classify_market` / `decide` the dashboard calls — no shadow copy — and scores
each BUY on the *underlying* path, using the engine's own ATR/structure stop
(``underlying_stop``) and its own expected favourable move.

What it deliberately does NOT claim:

* it is not an option backtest. The chain is empty, so no strike is chosen, no
  premium is paid and no spread is crossed; the R here is **gross of option
  economics**, which is where the recorded book says the money is actually lost.
  A good number here means "the direction was right", nothing more;
* it never holds overnight. A position is closed on the last bar of its IST
  session, so a result cannot come from a gap the tool would not have traded.

No-lookahead is structural: at bar *i* the engine is handed candles up to and
including *i* and nothing else, and every exit is decided from the bar being
replayed or a later one — never from the bar that opened the trade.
"""
from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from statistics import median

from app.config import settings
from app.engine import decision as eng
from app.models import Candle, Decision, Signal
from app.research.phase14 import paths
from app.research.phase14.coverage import session_of

LONG = "LONG"
SHORT = "SHORT"

EXIT_STOP = "STOP"
EXIT_TARGET = "TARGET"
EXIT_SIGNAL = "SIGNAL_EXIT"
EXIT_FLIP = "FLIP"
EXIT_TIME = "TIME_STOP"
EXIT_SESSION = "SESSION_END"

# Bars a replayed trade may live for before it is closed as unresolved. 240
# one-minute bars is most of a session; the recorded book's median time to the
# first target is ~8 minutes, so this only catches trades that went nowhere.
MAX_HOLD_BARS = 240

# Bars fed to the engine before the first decision is scored, so indicators and
# the HTF aggregation are warm rather than half-formed.
WARMUP_BARS = 120

# Bars visible to the engine at each replayed bar. Live scanning hands the engine
# `futures_candles(300)`, so replaying with the whole history would judge it on a
# view it never has, and would also make a multi-year run quadratic: every extra
# session would slow down every remaining bar.
LOOKBACK_BARS = 300

# How often a long run reports where it is. A five-year 1-minute replay is hours
# of silence otherwise, which is indistinguishable from a hang.
PROGRESS_EVERY_BARS = 10_000


@contextmanager
def htf_factor(factor: int | None) -> Iterator[None]:
    """Temporarily run the engine at a different HTF aggregation.

    The sweep needs to ask "what would 15-minute bias have decided?" without
    editing production defaults, and it must put the setting back even if the
    replay raises — otherwise one failed configuration silently contaminates
    every result after it.
    """
    if factor is None:
        yield
        return
    previous = settings.htf_factor
    settings.htf_factor = int(factor)
    try:
        yield
    finally:
        settings.htf_factor = previous


# Bars used to measure how much a 1-minute candle normally moves, so a stop can
# be judged against the market's own noise rather than against a fixed number.
NOISE_BARS = 14


def _noise(window: list[Candle], bars: int = NOISE_BARS) -> float:
    """Median high-low range of the last ``bars`` candles, in points."""
    recent = window[-bars:]
    if not recent:
        return 0.0
    return float(median(max(0.0, c.high - c.low) for c in recent))


def _minute_ist(ts: int) -> int:
    """Minutes since 09:15 IST, so time-of-day cohorts are session-relative."""
    ist = dt.datetime.fromtimestamp(int(ts), dt.timezone(dt.timedelta(hours=5, minutes=30)))
    return (ist.hour - 9) * 60 + ist.minute - 15


def _conviction(decision: Decision) -> float | None:
    """The conviction reading, from whichever field this tree carries.

    The engine's relabel renamed ``win_probability`` to ``conviction_meter`` —
    same number, honest name. The replay has to run on a tree from either side
    of that rename, so the field is read from the dumped model rather than
    assumed: a research script that crashes on an older engine cannot grade it.
    """
    fields = decision.model_dump()
    value = fields.get("conviction_meter", fields.get("win_probability"))
    return float(value) if isinstance(value, (int, float)) else None


def _side(spot: float, stop: float) -> str:
    return LONG if stop < spot else SHORT


def _favourable(side: str, price: float, entry: float) -> float:
    return price - entry if side == LONG else entry - price


def _track_excursion(trade: dict, bar: Candle,
                     path: dict | None = None) -> None:
    """Record how far a trade ran for and against, in R, while it was open.

    MFE says whether the target was the binding constraint or the move was
    always going to be small; MAE says how much heat a winner took, which is the
    difference between a stop that survives live and one that does not.
    """
    best = _favourable(trade["side"], bar.high if trade["side"] == LONG else bar.low,
                       trade["entry"])
    worst = _favourable(trade["side"], bar.low if trade["side"] == LONG else bar.high,
                        trade["entry"])
    trade["mfe_r"] = round(max(trade["mfe_r"], best / trade["risk"]), 3)
    trade["mae_r"] = round(min(trade["mae_r"], worst / trade["risk"]), 3)
    # The excursions above say how far the trade ran; they do not say in which
    # order, which is what every alternative exit rule turns on.
    if path is not None:
        paths.step(path, trade, bar)


def _exit_on_bar(trade: dict, bar: Candle) -> tuple[str, float] | None:
    """Stop/target resolution for one bar, resolving ambiguity against the trade.

    When a bar's range covers both the stop and the target, minute OHLC cannot
    say which came first, so the stop is taken. Assuming the target would flatter
    every result in the report by exactly the amount that matters most.
    """
    if trade["side"] == LONG:
        hit_stop = bar.low <= trade["stop"]
        hit_target = bar.high >= trade["target"]
    else:
        hit_stop = bar.high >= trade["stop"]
        hit_target = bar.low <= trade["target"]
    if hit_stop:
        return EXIT_STOP, trade["stop"]
    if hit_target:
        return EXIT_TARGET, trade["target"]
    return None


def _flip(trade: dict, decision) -> bool:
    if decision.signal != Signal.BUY or decision.underlying_stop is None:
        return False
    if decision.spot_price is None:
        return False
    return _side(decision.spot_price, decision.underlying_stop) != trade["side"]


def run(instrument: str, candles: list[Candle], *,
        factor: int | None = None,
        warmup: int = WARMUP_BARS,
        max_hold_bars: int = MAX_HOLD_BARS,
        lookback: int = LOOKBACK_BARS,
        progress: Callable[[int, int, int], None] | None = None,
        progress_every: int = PROGRESS_EVERY_BARS) -> dict:
    """Replay ``candles`` and return every trade the engine would have taken."""
    n = len(candles)
    if n <= warmup + 10:
        return {"ok": False, "reason": "insufficient_data", "bars": n,
                "instrument": instrument}

    trades: list[dict] = []
    counted = {"decisions": 0, "buys": 0, "buys_without_stop": 0}
    open_trade: dict | None = None
    open_path: dict = {}
    # Indices that close an IST session. A trade is flattened on its own
    # session's last bar and never opened on one, so no result can come from a
    # hold across the close the tool would not have taken.
    session_close = {
        i for i in range(n)
        if i + 1 >= n or session_of(candles[i + 1].time) != session_of(candles[i].time)
    }

    with htf_factor(factor):
        for i in range(warmup, n):
            bar = candles[i]
            window = candles[max(0, i + 1 - lookback): i + 1]
            price_change = bar.close - candles[i - 1].close
            snap = eng.compute_indicators(window, [], price_change=price_change)
            status = eng.classify_market(snap, False)
            decision, _ = eng.decide(
                window, [], snap, 0.0, False, status,
                open_trade is not None,
                None,
                spot=bar.close,
            )
            counted["decisions"] += 1
            if progress is not None and progress_every > 0 \
                    and counted["decisions"] % progress_every == 0:
                progress(i + 1, n, len(trades))
            if decision.signal == Signal.BUY:
                counted["buys"] += 1
                if decision.underlying_stop is None:
                    counted["buys_without_stop"] += 1

            if open_trade is not None:
                _track_excursion(open_trade, bar, open_path)
                resolved = _exit_on_bar(open_trade, bar)
                reason, price = resolved if resolved else (None, bar.close)
                if reason is None:
                    if decision.signal == Signal.EXIT:
                        reason = EXIT_SIGNAL
                    elif _flip(open_trade, decision):
                        reason = EXIT_FLIP
                    elif i - open_trade["entry_index"] >= max_hold_bars:
                        reason = EXIT_TIME
                    elif i in session_close:
                        reason = EXIT_SESSION
                if reason is not None:
                    open_trade["exit_reason"] = reason
                    open_trade["exit_ts"] = int(bar.time)
                    open_trade["exit_price"] = price
                    open_trade["minutes"] = max(
                        1, int((bar.time - open_trade["entry_ts"]) // 60))
                    open_trade["bars_held"] = i - open_trade["entry_index"]
                    open_trade["r"] = round(
                        _favourable(open_trade["side"], price, open_trade["entry"])
                        / open_trade["risk"], 3)
                    paths.finish(open_path, open_trade)
                    trades.append(open_trade)
                    open_trade = None
                continue

            if decision.signal != Signal.BUY or decision.underlying_stop is None:
                continue
            if i in session_close:
                continue
            entry = bar.close
            noise = _noise(window)
            stop = float(decision.underlying_stop)
            risk = abs(entry - stop)
            if risk <= 0:
                continue
            side = _side(entry, stop)
            move = decision.expected_move_points
            reward = float(move) if move else 1.5 * risk
            target = entry + reward if side == LONG else entry - reward
            open_trade = {
                "instrument": instrument,
                "session": session_of(bar.time),
                "entry_ts": int(bar.time),
                "entry_index": i,
                "side": side,
                "entry": round(entry, 2),
                "stop": round(stop, 2),
                "target": round(target, 2),
                "risk": round(risk, 3),
                "reward_risk": round(reward / risk, 2),
                "confidence": decision.confidence,
                "regime": status.value,
                "htf_trend": decision.htf_trend,
                "htf_strength": decision.htf_strength,
                "entry_trigger": decision.entry_trigger,
                # The scores the Research tab proposes production rules on. Only
                # the ones derived from the underlying are recorded: premium
                # health, spread and OI need the chain, which history has none
                # of, so those rules stay untestable here rather than be faked.
                "trade_score": decision.trade_score,
                "opportunity_score": decision.opportunity_score,
                "opportunity_label": decision.opportunity_label,
                "risk_score": decision.risk_score,
                "risk_level": decision.risk_level,
                # Published under its real name since the relabel: a
                # conviction reading, not a probability. Written under the
                # old key as well, so a pool stays readable by graders on
                # either side of the rename.
                "conviction_meter": _conviction(decision),
                "win_probability": _conviction(decision),
                "trap_prob": (decision.buy_trap_prob if side == LONG
                              else decision.sell_trap_prob),
                "fake_prob": (decision.fake_breakout_prob if side == LONG
                              else decision.fake_breakdown_prob),
                "smart_money": decision.smart_money,
                # A stop tighter than one candle's usual range is resolved by tick
                # noise rather than by direction, however good the read was.
                "noise_points": round(noise, 3),
                "risk_over_noise": round(risk / noise, 2) if noise > 0 else None,
                "entry_minute_ist": _minute_ist(bar.time),
                "mfe_r": 0.0,
                "mae_r": 0.0,
            }
            open_path = paths.start(open_trade)

    sessions = sorted({session_of(c.time) for c in candles})
    return {"ok": True, "instrument": instrument, "bars": n,
            "htf_factor": int(factor) if factor else int(settings.htf_factor),
            "lookback_bars": int(lookback),
            "sessions_replayed": len(sessions),
            "first_session": sessions[0], "last_session": sessions[-1],
            "trades": trades, **counted}


# Bars between sampled candidates in the pool. Every bar would work, but
# consecutive candidates share almost all of their forward path, so their results
# are near-duplicates: the arms would look far more precise than they are. One
# sample per 15 minutes keeps overlap low without thinning the book to nothing.
CANDIDATE_STRIDE = 15


def _harness_stop(snap, status, side: str, spot: float) -> float | None:
    """The stop the engine would have set here, computed for ANY signal.

    ``decide`` calculates this for every bar but only publishes it on BUY/HOLD
    (it would be wrong to show a stop next to a WAIT), so a refused candidate has
    no stop to replay. Rather than invent one — a different stop rule on the
    rejected arm would make every gate comparison meaningless — this repeats the
    engine's own arithmetic: regime ATR multiple, then the same structure anchor.
    """
    atr = snap.atr
    if atr is None or atr <= 0:
        return None
    want_call = side == LONG
    sl_mult, _ = eng._atr_multiples(status)
    dist = sl_mult * float(atr)
    support, resistance = snap.support, snap.resistance
    if want_call and support and 0 < spot - support < 2.5 * atr:
        dist = max(dist, spot - support + 0.15 * atr)
    elif (not want_call) and resistance and 0 < resistance - spot < 2.5 * atr:
        dist = max(dist, resistance - spot + 0.15 * atr)
    if dist <= 0:
        return None
    return round(spot - dist if want_call else spot + dist, 1)


def _resolve_forward(trade: dict, candles: list[Candle], start: int,
                     session_close: set[int], max_hold_bars: int) -> dict:
    """Walk a hypothetical candidate forward under the replay's exit rules."""
    path = paths.start(trade)
    for j in range(start, len(candles)):
        bar = candles[j]
        _track_excursion(trade, bar, path)
        resolved = _exit_on_bar(trade, bar)
        reason, price = resolved if resolved else (None, bar.close)
        if reason is None:
            if j - trade["entry_index"] >= max_hold_bars:
                reason = EXIT_TIME
            elif j in session_close:
                reason = EXIT_SESSION
        if reason is not None:
            trade["exit_reason"] = reason
            trade["exit_ts"] = int(bar.time)
            trade["exit_price"] = price
            trade["minutes"] = max(1, int((bar.time - trade["entry_ts"]) // 60))
            trade["bars_held"] = j - trade["entry_index"]
            trade["r"] = round(
                _favourable(trade["side"], price, trade["entry"])
                / trade["risk"], 3)
            paths.finish(path, trade)
            return trade
    return trade


def pool(instrument: str, candles: list[Candle], *,
         factor: int | None = None,
         warmup: int = WARMUP_BARS,
         max_hold_bars: int = MAX_HOLD_BARS,
         lookback: int = LOOKBACK_BARS,
         stride: int = CANDIDATE_STRIDE,
         progress: Callable[[int, int, int], None] | None = None,
         progress_every: int = PROGRESS_EVERY_BARS) -> dict:
    """Replay every CANDIDATE, not only the trades the engine agreed to take.

    A gate cannot be graded on the taken book. The engine applies its scores
    *before* a BUY exists, so trades that would fail a score gate were never
    recorded, and "trades above the threshold did better" is then measured
    against an arm that is nearly empty — the conditional win rates the Research
    tab reports are that artefact.

    So this scores what the engine *refused* as well: at every sampled bar the
    hypothetical trade is opened regardless of the signal, with the engine's own
    stop rule and target, and resolved forward on the underlying. ``signal`` and
    ``taken`` are recorded, which is what finally makes the honest comparison
    possible — and it prices refusals, the cost the daily loop can never see.

    Candidates deliberately overlap in time and are NOT a tradable equity curve:
    nobody could hold all of them. This book answers "does this condition select
    better trades", not "what would this have earned".
    """
    n = len(candles)
    if n <= warmup + 10:
        return {"ok": False, "reason": "insufficient_data", "bars": n,
                "instrument": instrument}

    cands: list[dict] = []
    counted = {"decisions": 0, "buys": 0, "no_side": 0, "no_stop": 0}
    session_close = {
        i for i in range(n)
        if i + 1 >= n or session_of(candles[i + 1].time) != session_of(candles[i].time)
    }

    with htf_factor(factor):
        for i in range(warmup, n, max(1, stride)):
            if i in session_close:
                continue
            bar = candles[i]
            window = candles[max(0, i + 1 - lookback): i + 1]
            price_change = bar.close - candles[i - 1].close
            snap = eng.compute_indicators(window, [], price_change=price_change)
            status = eng.classify_market(snap, False)
            decision, _ = eng.decide(
                window, [], snap, 0.0, False, status, False, None,
                spot=bar.close,
            )
            counted["decisions"] += 1
            if progress is not None and progress_every > 0 \
                    and counted["decisions"] % progress_every == 0:
                progress(i + 1, n, len(cands))
            if decision.signal == Signal.BUY:
                counted["buys"] += 1

            # The engine gates direction on the 5-min trend, so that is the side
            # a refused candidate would have taken. With no HTF trend there is no
            # defined side, and guessing one would invent the very signal under
            # test — those bars are counted and skipped instead.
            trend = (decision.htf_trend or "").upper()
            if trend == "UP":
                side = LONG
            elif trend == "DOWN":
                side = SHORT
            else:
                counted["no_side"] += 1
                continue

            entry = bar.close
            stop = (float(decision.underlying_stop)
                    if decision.underlying_stop is not None
                    else _harness_stop(snap, status, side, entry))
            if stop is None or _side(entry, stop) != side:
                counted["no_stop"] += 1
                continue
            risk = abs(entry - stop)
            if risk <= 0:
                counted["no_stop"] += 1
                continue
            noise = _noise(window)
            move = decision.expected_move_points
            reward = float(move) if move else 1.5 * risk
            target = entry + reward if side == LONG else entry - reward
            cand = {
                "instrument": instrument,
                "session": session_of(bar.time),
                "entry_ts": int(bar.time),
                "entry_index": i,
                "side": side,
                "entry": round(entry, 2),
                "stop": round(stop, 2),
                "target": round(target, 2),
                "risk": round(risk, 3),
                "reward_risk": round(reward / risk, 2),
                # What the engine decided here, so a gate can be graded against
                # the refused arm and the refusals can be priced.
                "signal": decision.signal.value,
                "taken": decision.signal == Signal.BUY,
                "confidence": decision.confidence,
                "regime": status.value,
                "htf_trend": decision.htf_trend,
                "htf_strength": decision.htf_strength,
                "entry_trigger": decision.entry_trigger,
                "trade_score": decision.trade_score,
                "opportunity_score": decision.opportunity_score,
                "opportunity_label": decision.opportunity_label,
                "risk_score": decision.risk_score,
                "risk_level": decision.risk_level,
                # Published under its real name since the relabel: a
                # conviction reading, not a probability. Written under the
                # old key as well, so a pool stays readable by graders on
                # either side of the rename.
                "conviction_meter": _conviction(decision),
                "win_probability": _conviction(decision),
                "trap_prob": (decision.buy_trap_prob if side == LONG
                              else decision.sell_trap_prob),
                "fake_prob": (decision.fake_breakout_prob if side == LONG
                              else decision.fake_breakdown_prob),
                "smart_money": decision.smart_money,
                "noise_points": round(noise, 3),
                "risk_over_noise": round(risk / noise, 2) if noise > 0 else None,
                "entry_minute_ist": _minute_ist(bar.time),
                "mfe_r": 0.0,
                "mae_r": 0.0,
            }
            resolved = _resolve_forward(cand, candles, i + 1, session_close,
                                       max_hold_bars)
            if "r" in resolved:
                cands.append(resolved)

    sessions = sorted({session_of(c.time) for c in candles})
    taken = sum(1 for c in cands if c["taken"])
    return {"ok": True, "instrument": instrument, "bars": n,
            "htf_factor": int(factor) if factor else int(settings.htf_factor),
            "lookback_bars": int(lookback), "stride_bars": int(max(1, stride)),
            "sessions_replayed": len(sessions),
            "first_session": sessions[0], "last_session": sessions[-1],
            "candidates": len(cands),
            "candidates_taken": taken,
            "candidates_refused": len(cands) - taken,
            "trades": cands, **counted}


def _max_drawdown(rs: list[float]) -> float:
    peak = equity = worst = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return round(worst, 2)


def _percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile. Small samples make interpolation false precision."""
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(pct / 100.0 * len(ordered))
    return round(ordered[min(len(ordered), max(1, rank)) - 1], 1)


def _profit_factor(rs: list[float]) -> float | None:
    """Gross win / gross loss. ``None`` when nothing lost, because dividing by
    zero would print an infinite edge off a handful of trades."""
    gain = sum(r for r in rs if r > 0)
    loss = -sum(r for r in rs if r < 0)
    if loss <= 0:
        return None
    return round(gain / loss, 2)


def _calendar_years(runs: list[dict]) -> float:
    """Calendar span the replay covers, for a per-year signal rate.

    Frequency is measured against the calendar, not against sessions that
    happened to trade, otherwise a configuration that fires on 5 days a year
    looks as busy as one that fires daily.
    """
    firsts = [r["first_session"] for r in runs if r.get("ok") and r.get("first_session")]
    lasts = [r["last_session"] for r in runs if r.get("ok") and r.get("last_session")]
    if not firsts or not lasts:
        return 0.0
    start = dt.date.fromisoformat(min(firsts))
    end = dt.date.fromisoformat(max(lasts))
    return max((end - start).days + 1, 1) / 365.25


def summarise(runs: list[dict]) -> dict:
    """Aggregate one or more replay runs into the numbers a report can quote.

    Frequency sits next to expectancy deliberately: +0.30 R on one trade a day is
    worth less than +0.20 R on twenty, and ranking on R/trade alone hides that.
    """
    good = [r for r in runs if r.get("ok")]
    trades = [t for r in good for t in r["trades"]]
    rs = [t["r"] for t in trades]
    wins = [t for t in trades if t["r"] > 0]
    losses = [t for t in trades if t["r"] < 0]
    holds = [float(t["minutes"]) for t in trades]
    to_target = [t["minutes"] for t in trades if t["exit_reason"] == EXIT_TARGET]
    by_reason: dict[str, int] = {}
    for t in trades:
        by_reason[t["exit_reason"]] = by_reason.get(t["exit_reason"], 0) + 1
    sessions = {t["session"] for t in trades}
    replayed = sum(r.get("sessions_replayed", 0) for r in good)
    years = _calendar_years(good)
    return {
        "instruments": sorted({r["instrument"] for r in good}),
        "first_session": min((r["first_session"] for r in good), default=None),
        "last_session": max((r["last_session"] for r in good), default=None),
        "calendar_years": round(years, 2),
        "sessions_replayed": replayed,
        "decisions": sum(r.get("decisions", 0) for r in good),
        "buys": sum(r.get("buys", 0) for r in good),
        "buys_without_stop": sum(r.get("buys_without_stop", 0) for r in good),
        "trades": len(trades),
        "sessions_traded": len(sessions),
        "signals_per_year": round(len(trades) / years, 1) if years else 0.0,
        "signals_per_month": round(len(trades) / (years * 12.0), 1) if years else 0.0,
        "trades_per_day": round(len(trades) / replayed, 2) if replayed else 0.0,
        "trades_per_session_traded": (round(len(trades) / len(sessions), 2)
                                      if sessions else 0.0),
        "session_participation_pct": (round(100.0 * len(sessions) / replayed, 1)
                                      if replayed else 0.0),
        "win_rate": round(100.0 * len(wins) / len(trades), 1) if trades else 0.0,
        "net_r": round(sum(rs), 2),
        "expectancy_r": round(sum(rs) / len(rs), 3) if rs else 0.0,
        "avg_r": round(sum(rs) / len(rs), 3) if rs else 0.0,
        "median_r": round(median(rs), 3) if rs else 0.0,
        "avg_win_r": round(sum(t["r"] for t in wins) / len(wins), 3) if wins else 0.0,
        "avg_loss_r": (round(sum(t["r"] for t in losses) / len(losses), 3)
                       if losses else 0.0),
        "profit_factor": _profit_factor(rs),
        "max_drawdown_r": _max_drawdown(rs),
        # Is the stop wide enough to survive the market's own noise? A stop
        # inside one candle's usual range is resolved by ticks, not direction,
        # and a book that mostly resolves on the next bar is measuring that.
        "median_risk_over_noise": (
            round(median([t["risk_over_noise"] for t in trades
                          if t.get("risk_over_noise")]), 2)
            if any(t.get("risk_over_noise") for t in trades) else None),
        "stop_inside_one_bar_pct": (
            round(100.0 * sum(1 for t in trades
                              if (t.get("risk_over_noise") or 99) < 1.0)
                  / len(trades), 1) if trades else 0.0),
        "resolved_next_bar_pct": (
            round(100.0 * sum(1 for t in trades if t.get("bars_held") == 1)
                  / len(trades), 1) if trades else 0.0),
        "median_minutes_hold": round(median(holds), 1) if holds else None,
        "p90_minutes_hold": _percentile(holds, 90.0),
        "median_minutes_to_target": round(median(to_target), 1) if to_target else None,
        "median_mfe_r": (round(median([t["mfe_r"] for t in trades]), 3)
                         if trades else None),
        "median_mae_r": (round(median([t["mae_r"] for t in trades]), 3)
                         if trades else None),
        "median_mae_r_winners": (round(median([t["mae_r"] for t in wins]), 3)
                                 if wins else None),
        "exits": by_reason,
        "note": "underlying-only R, gross of option premium, spread and slippage",
    }
