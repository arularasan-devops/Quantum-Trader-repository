"""READ-ONLY backtest of the PREMIUM-SELLING (defined-risk credit spread) engine.

This is the only structure that genuinely produces an 80-90% win rate, so the
question this script must answer is NOT "what is the win rate" (that is set by
the short-leg delta) but "does the 10-20% tail give it all back".

Method
------
* Cached 1-minute candles are grouped into sessions; one spread is opened per
  session at the open and held to the session close (a short-dated expiry
  proxy), so every trade is independent and there is no overlap.
* Legs are priced with Black-Scholes using REALISED volatility from the trailing
  window as the IV proxy. Real IV normally trades above realised vol, so the
  credit collected here is UNDER-stated -- the conservative direction.
* Structure follows the regime: a bull-put spread with an up bias, a bear-call
  spread with a down bias, an iron condor when the trend is flat (the regime the
  option-BUYING engine has to skip).
* Risk is always defined: the hedge leg is bought ``width`` strikes further out,
  so the worst case per trade is capped and known before entry.
* A stop is applied when the loss reaches ``stop_multiple`` x the credit, which
  is how a real seller caps the tail instead of praying into expiry.

Everything here is MODELLED (no historical option chain exists). It measures the
SHAPE of the payoff -- win rate, profit factor, worst trade, max drawdown --
which is exactly what decides whether this is fundable.

    .venv/bin/python spread_backtest.py --instrument NIFTY
"""
from __future__ import annotations

import argparse
import statistics

from app.analysis import blackscholes as bs
from app.backtest import angel_history as ah
from app.config import settings
from app.market.instruments import REGISTRY

_TRADING_DAYS = 252.0
_MIN_VOL = 0.06


def _sessions(candles) -> list[list]:
    """Split the candle stream into per-day sessions."""
    out: list[list] = []
    cur: list = []
    day = None
    for c in candles:
        d = c.time // 86400
        if day is None:
            day = d
        if d != day:
            if len(cur) > 30:
                out.append(cur)
            cur, day = [], d
        cur.append(c)
    if len(cur) > 30:
        out.append(cur)
    return out


def _pf(vals: list[float]) -> float | None:
    win = sum(v for v in vals if v > 0)
    loss = -sum(v for v in vals if v <= 0)
    return round(win / loss, 3) if loss > 0 else None


def _max_drawdown(vals: list[float]) -> float:
    peak = eq = 0.0
    worst = 0.0
    for v in vals:
        eq += v
        peak = max(peak, eq)
        worst = min(worst, eq - peak)
    return round(worst, 1)


def run(
    candles,
    instrument: str,
    *,
    short_delta: float,
    width_steps: int,
    stop_multiple: float,
    vol_lookback: int = 20,
    hold_sessions: int = 1,
    slippage_per_leg: float = 0.0,
    vol_shock: float = 0.0,
):
    """``hold_sessions`` > 1 keeps the spread open across session boundaries so
    OVERNIGHT GAP risk is included -- the biggest killer of option sellers, and
    invisible in an intraday-only test. ``vol_shock`` expands the IV used to mark
    the position when price runs at the short strike, modelling the volatility
    spike that makes a losing spread cost far more to close than a static-vol
    price implies. ``slippage_per_leg`` charges the bid-ask crossing on every
    leg, at entry and at exit.
    """
    spec = REGISTRY.get(instrument)
    step = spec.strike_step if spec else 50.0

    sessions = _sessions(candles)
    daily_closes = [s[-1].close for s in sessions]
    results: list[float] = []
    detail: list[dict] = []

    for idx in range(vol_lookback, len(sessions)):
        sess = sessions[idx]
        hist = daily_closes[idx - vol_lookback: idx]
        vol = max(_MIN_VOL, bs.realised_vol(hist, periods_per_year=_TRADING_DAYS))
        window = [b for s in sessions[idx: idx + hold_sessions] for b in s]
        if len(window) < len(sess):
            break
        spot = sess[0].close
        years = hold_sessions / _TRADING_DAYS

        # Regime: where did the trailing window drift? Sell the side the market
        # is NOT moving toward; sell both when it is going nowhere.
        drift = (hist[-1] - hist[0]) / max(hist[0], 1e-9)
        if drift > 0.004:
            legs = ["put"]
        elif drift < -0.004:
            legs = ["call"]
        else:
            legs = ["put", "call"]  # iron condor

        credit = 0.0
        max_loss = 0.0
        built: list[tuple[str, float, float]] = []  # kind, short_k, long_k
        for kind in legs:
            is_call = kind == "call"
            short_k = bs.strike_for_delta(
                spot, years, vol, short_delta, is_call=is_call, step=step
            )
            long_k = short_k + width_steps * step if is_call else short_k - width_steps * step
            if long_k <= 0:
                continue
            c_short = bs.price(spot, short_k, years, vol, is_call=is_call)
            c_long = bs.price(spot, long_k, years, vol, is_call=is_call)
            leg_credit = c_short - c_long
            if leg_credit <= 0:
                continue
            credit += leg_credit
            max_loss += width_steps * step - leg_credit
            built.append((kind, short_k, long_k))
        # the bid-ask is crossed on every leg, at entry and again at exit
        credit -= slippage_per_leg * len(built) * 2 * 2
        if not built or credit <= 0:
            continue

        # Intra-session management: stop out when the mark-to-market loss on the
        # spread reaches stop_multiple x credit. Priced at each bar with the
        # remaining time decayed linearly across the session.
        n = len(window)
        stop_loss_at = stop_multiple * credit
        pnl = None
        for i, bar in enumerate(window):
            remaining = max(1e-6, years * (1.0 - i / max(1, n - 1)))
            # IV expands as price runs at the short strike: the seller pays the
            # directional move AND the vol spike on the way out.
            adverse = max(
                abs(bar.close - spot) / max(spot, 1e-9) / max(vol / 16.0, 1e-6) - 1.0,
                0.0,
            )
            mark_vol = vol * (1.0 + vol_shock * min(adverse, 3.0))
            cur_cost = 0.0
            for kind, short_k, long_k in built:
                is_call = kind == "call"
                cur_cost += bs.price(bar.close, short_k, remaining, mark_vol, is_call=is_call)
                cur_cost -= bs.price(bar.close, long_k, remaining, mark_vol, is_call=is_call)
            mtm = credit - cur_cost  # positive = spread has decayed in our favour
            if -mtm >= stop_loss_at:
                pnl = -min(stop_loss_at, max_loss)
                break
        if pnl is None:
            settle = window[-1].close
            cost = 0.0
            for kind, short_k, long_k in built:
                is_call = kind == "call"
                cost += bs.price(settle, short_k, 0.0, 0.0, is_call=is_call)
                cost -= bs.price(settle, long_k, 0.0, 0.0, is_call=is_call)
            pnl = credit - cost

        results.append(pnl)
        detail.append({"credit": credit, "max_loss": max_loss, "pnl": pnl})

    return results, detail


def _report(label: str, results: list[float], detail: list[dict]) -> None:
    if not results:
        print(f"\n== {label} ==\n   (no trades)")
        return
    wins = [v for v in results if v > 0]
    losses = [v for v in results if v <= 0]
    avg_credit = statistics.mean(d["credit"] for d in detail)
    avg_maxloss = statistics.mean(d["max_loss"] for d in detail)
    print(f"\n== {label} ==")
    print(f"   trades        : {len(results)}")
    print(f"   WIN RATE      : {round(len(wins) / len(results) * 100, 1)}%")
    print(f"   total pts     : {round(sum(results), 1)}")
    print(f"   profit factor : {_pf(results)}")
    print(f"   avg win       : {round(statistics.mean(wins), 1) if wins else 0}")
    print(f"   avg loss      : {round(statistics.mean(losses), 1) if losses else 0}")
    print(f"   WORST trade   : {round(min(results), 1)}")
    print(f"   max drawdown  : {_max_drawdown(results)}")
    print(f"   avg credit    : {round(avg_credit, 1)}   avg defined risk: {round(avg_maxloss, 1)}")
    print(f"   worst / avg win ratio: {round(abs(min(results)) / (statistics.mean(wins) if wins else 1), 1)}x")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", default="NIFTY")
    ap.add_argument("--interval", default="ONE_MINUTE")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    path = ah.cache_path(settings.data_dir, args.instrument, args.interval)
    candles = ah.load_candles(path)
    if args.limit and len(candles) > args.limit:
        candles = candles[-args.limit:]
    print(f"{args.instrument}: {len(candles):,} candles  ({len(_sessions(candles))} sessions)")

    slip = (REGISTRY.get(args.instrument).strike_step if REGISTRY.get(args.instrument) else 50.0) * 0.02
    scenarios = (
        ("IDEAL (no slippage, no vol spike, intraday only)", 1, 0.0, 0.0),
        ("+ slippage", 1, slip, 0.0),
        ("+ slippage + IV spike", 1, slip, 1.0),
        ("REALISTIC: weekly hold (GAP RISK) + slippage + IV spike", 5, slip, 1.0),
        ("STRESS: weekly hold + slippage + severe IV spike", 5, slip, 2.0),
    )
    for label, hold, sl, shock in scenarios:
        res, det = run(
            candles, args.instrument,
            short_delta=0.20, width_steps=2, stop_multiple=2.0,
            hold_sessions=hold, slippage_per_leg=sl, vol_shock=shock,
        )
        _report(label, res, det)


if __name__ == "__main__":
    main()
