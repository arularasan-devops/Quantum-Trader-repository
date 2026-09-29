"""Phase 7 Part 3-5, 7, 21 — production BUYs and what really happened to the
option afterwards. RESEARCH ONLY.

Part 3 reconstructs the CONTROL population by running the *existing* production
decision function over recorded bars — no new signal, no changed threshold, no
lookahead: bar ``i`` sees ``candles[:i+1]`` and the chain snapshot matched to bar
``i`` only. Part 4 then walks the chosen leg forward on its real quoted premium.
Parts 5/7/21 are pure measurement on that walk.

The limits are structural and are carried into every output rather than being
mentioned once: snapshots are ~60s closes, so an intrabar stop touch is invisible
and MFE/MAE are snapshot extremes; and no bid/ask is stored, so every entry and
exit here is a mid-less single price and therefore optimistic.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

from app.analysis import indicators as ind_lib
from app.analysis import structure
from app.engine.decision import classify_market, compute_indicators, decide
from app.market.instruments import REGISTRY
from app.models import Candle, OptionType, Signal

from .dataset import ChainSeries, ist
from .quality import stale_flag

WINDOW = 60          # bars of context handed to the production engine
HORIZON_BARS = 30    # forward window for the path, in minutes

# Part 5 asks for chase classes and forbids invented thresholds. These names are
# assigned from the *measured* distribution of "fraction of the post-signal move
# already consumed at the entry price" — the cut points come from
# ``chase_thresholds()`` below, computed per run, not from taste.
CHASE_CLASSES = ("IDEAL_ENTRY", "GOOD_ENTRY", "ACCEPTABLE_ENTRY",
                 "CHASED_ENTRY", "SEVERELY_CHASED")


@dataclass
class OptionPath:
    """The real quoted path of one leg after one production BUY."""

    symbol: str
    entry: float
    stop: float
    target1: float
    quotes: list[tuple[int, float]] = field(default_factory=list)
    best: float = 0.0            # highest quoted premium in the horizon
    worst: float = 0.0           # lowest
    ts_best: int = 0
    ts_worst: int = 0
    first_stop_ts: int | None = None
    first_target_ts: int | None = None
    last: float = 0.0

    @property
    def risk(self) -> float:
        return max(0.01, self.entry - self.stop)

    @property
    def mfe_r(self) -> float:
        return (self.best - self.entry) / self.risk

    @property
    def mae_r(self) -> float:
        return (self.worst - self.entry) / self.risk

    @property
    def outcome(self) -> str:
        """Which came first on the recorded series. TARGET_FIRST / STOP_FIRST /
        OPEN, decided by timestamp, never by which is bigger."""
        st, tg = self.first_stop_ts, self.first_target_ts
        if tg is not None and (st is None or tg < st):
            return "TARGET_FIRST"
        if st is not None:
            return "STOP_FIRST"
        return "OPEN"

    def premium_at(self, when: int) -> float | None:
        for ts, px in self.quotes:
            if ts >= when:
                return px
        return None

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol, "entry": round(self.entry, 2),
            "stop": round(self.stop, 2), "target1": round(self.target1, 2),
            "quotes": len(self.quotes),
            "mfe_premium": round(self.best - self.entry, 2),
            "mae_premium": round(self.worst - self.entry, 2),
            "mfe_r": round(self.mfe_r, 3), "mae_r": round(self.mae_r, 3),
            "mfe_pct": round(100.0 * (self.best - self.entry) / self.entry, 2),
            "mae_pct": round(100.0 * (self.worst - self.entry) / self.entry, 2),
            "min_to_mfe": None if not self.ts_best else round(
                (self.ts_best - self.quotes[0][0]) / 60.0 + 1, 1),
            "min_to_mae": None if not self.ts_worst else round(
                (self.ts_worst - self.quotes[0][0]) / 60.0 + 1, 1),
            "first_stop_ist": None if self.first_stop_ts is None else ist(self.first_stop_ts),
            "first_target_ist": None if self.first_target_ts is None else ist(self.first_target_ts),
            "outcome": self.outcome,
        }


@dataclass
class BuyEvent:
    """One reconstructed production BUY — the CONTROL row of Part 3."""

    instrument: str
    ts: int
    bar: int
    side: str
    symbol: str
    strike: float
    spot: float
    entry: float
    stop: float
    target1: float
    target2: float
    target3: float
    confidence: float
    trade_score: float
    regime: str
    atr: float
    leg_delta: float
    vwap: float
    ema9: float
    ema20: float
    prev_mid: float
    breakout_level: float
    chain_age_sec: int | None
    data_flag: str
    # Retest levels added for the 13A entry comparison. Defaulted so an event
    # built without them (older smokes, hand-built fixtures) simply declines the
    # policies that need them.
    swing_low: float = 0.0
    swing_high: float = 0.0
    fvg_bull: float = 0.0
    fvg_bear: float = 0.0
    path: OptionPath | None = None
    chase: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        row = {k: v for k, v in asdict(self).items() if k not in ("path", "chase")}
        row["ts_ist"] = ist(self.ts)
        row["path"] = None if self.path is None else self.path.as_dict()
        row["chase"] = dict(self.chase)
        return row


def walk(series: ChainSeries, symbol: str, after: int, entry: float, stop: float,
         target: float, horizon: int = HORIZON_BARS) -> OptionPath:
    """Part 4 — forward-walk one leg on its own recorded premium series."""
    path = OptionPath(symbol=symbol, entry=entry, stop=stop, target1=target)
    path.best = path.worst = path.last = entry
    for ts, px in series.forward(after, horizon, symbol):
        path.quotes.append((ts, px))
        path.last = px
        if px > path.best:
            path.best, path.ts_best = px, ts
        if px < path.worst:
            path.worst, path.ts_worst = px, ts
        if path.first_stop_ts is None and px <= stop:
            path.first_stop_ts = ts
        if path.first_target_ts is None and px >= target:
            path.first_target_ts = ts
    return path


def _levels(window: list[Candle]) -> dict[str, float]:
    """Every retest level an entry policy may aim at, read from the window only.

    A level that does not exist in the window is returned as 0.0 and the policy
    that wants it declines the signal rather than falling back to another level.
    """
    closes = [c.close for c in window]
    highs = [c.high for c in window]
    lows = [c.low for c in window]
    vols = [c.volume for c in window]
    ema9 = ind_lib.ema(np.array(closes), 9) or closes[-1]
    ema20 = ind_lib.ema(np.array(closes), 20) or closes[-1]
    vwap = ind_lib.vwap(np.array(highs), np.array(lows), np.array(closes),
                        np.array(vols)) or closes[-1]
    prev_mid = (window[-2].high + window[-2].low) / 2.0 if len(window) > 1 else closes[-1]
    breakout = max(highs[-20:-1]) if len(highs) > 20 else max(highs[:-1] or highs)
    swing_highs, swing_lows = structure.swing_points(np.array(highs), np.array(lows))
    bull, bear = structure.fair_value_gap(np.array(highs), np.array(lows))
    return {
        "vwap": float(vwap),
        "ema9": float(ema9),
        "ema20": float(ema20),
        "prev_mid": float(prev_mid),
        "breakout_level": float(breakout),
        "swing_low": float(swing_lows[-1][1]) if swing_lows else 0.0,
        "swing_high": float(swing_highs[-1][1]) if swing_highs else 0.0,
        "fvg_bull": 0.0 if bull is None else float(bull),
        "fvg_bear": 0.0 if bear is None else float(bear),
    }


def reconstruct(instrument: str, candles: list[Candle], real: ChainSeries,
                fresh_ms: float, horizon: int = HORIZON_BARS,
                window: int = WINDOW) -> tuple[list[BuyEvent], dict]:
    """Part 3 + 4 — production BUYs on recorded bars, each with its real path.

    Returns the events and a coverage note: how many bars were inspected, how
    many had a chain snapshot at all, and why the rest were dropped. The note
    matters more than the events when coverage is thin.
    """
    spec = REGISTRY.get(instrument)
    events: list[BuyEvent] = []
    stats = {"bars_inspected": 0, "bars_with_chain": 0, "buys": 0,
             "buys_without_leg_quote": 0, "buys_with_path": 0,
             "dropped_short_forward": 0}
    for i in range(window, len(candles) - 1):
        stats["bars_inspected"] += 1
        bar = candles[i]
        snap = real.at(bar.time)
        if not snap:
            continue
        stats["bars_with_chain"] += 1
        chain = [q for q in (_safe_quote(leg) for leg in snap.values()) if q]
        if not chain:
            continue
        ctx = candles[i - window : i + 1]
        spot = float(ctx[-1].close)
        indi = compute_indicators(ctx, chain)
        status = classify_market(indi, False)
        dec, _ = decide(ctx, chain, indi, 0.0, False, status, False, None, 1.0,
                        spot=spot)
        if dec.signal != Signal.BUY or dec.option_type is None:
            continue
        stats["buys"] += 1
        entry = float(dec.current_premium or 0.0)
        symbol = str(dec.recommended_option or "")
        if entry <= 0 or not symbol or dec.stop_loss is None or dec.target1 is None:
            stats["buys_without_leg_quote"] += 1
            continue
        levels = _levels(ctx)
        strike = 0.0
        leg_delta = 0.0
        leg = snap.get(symbol)
        if leg:
            strike = float(leg.get("strike") or 0.0)
            leg_delta = abs(float(leg.get("delta") or 0.0))
        ev = BuyEvent(
            instrument=instrument, ts=int(bar.time), bar=i,
            side="CE" if dec.option_type == OptionType.CALL else "PE",
            symbol=symbol, strike=strike, spot=spot, entry=entry,
            stop=float(dec.stop_loss), target1=float(dec.target1),
            target2=float(dec.target2 or 0.0), target3=float(dec.target3 or 0.0),
            confidence=float(dec.gates.confidence if dec.gates else 0.0),
            trade_score=float(dec.trade_score or 0.0),
            regime=status.value if hasattr(status, "value") else str(status),
            atr=float(indi.atr or 0.0), leg_delta=leg_delta,
            **levels,
            chain_age_sec=real.age_at(bar.time),
            data_flag=stale_flag(real.age_at(bar.time), 0, fresh_ms),
        )
        ev.path = walk(real, symbol, bar.time, entry, ev.stop, ev.target1, horizon)
        if len(ev.path.quotes) < 3:
            stats["dropped_short_forward"] += 1
            continue
        stats["buys_with_path"] += 1
        events.append(ev)
    if spec is not None:
        stats["strike_step"] = spec.strike_step
    return events, stats


def _safe_quote(leg: dict):
    from .dataset import as_quote

    try:
        q = as_quote(leg)
    except (KeyError, TypeError, ValueError):
        return None
    return q if q.premium > 0 else None


def chase_thresholds(events: list[BuyEvent]) -> dict:
    """Part 5 — cut points for the chase classes, measured from this run.

    ``consumed`` = how much of the move that was still available after the signal
    had already been given up at the entry price. It is scale-free, so the same
    cuts hold for a ₹15 and a ₹100 premium.
    """
    consumed = [ev.chase["consumed_frac"] for ev in events
                if ev.chase.get("consumed_frac") is not None]
    if not consumed:
        return {"n": 0, "cuts": None}
    xs = sorted(consumed)

    def q(f: float) -> float:
        return xs[min(len(xs) - 1, int(f * len(xs)))]

    return {"n": len(xs), "cuts": {"ideal": round(q(0.2), 4),
                                   "good": round(q(0.4), 4),
                                   "acceptable": round(q(0.6), 4),
                                   "chased": round(q(0.8), 4)},
            "note": "quintiles of the measured distribution — the labels rank "
                    "entries within this dataset and are not absolute quality"}


def measure_chase(ev: BuyEvent, series: ChainSeries, look: int = 5) -> dict:
    """Part 5 — signal price vs the best entry actually available afterwards."""
    if ev.path is None or not ev.path.quotes:
        return {}
    window = ev.path.quotes[:look]
    best_after = min(px for _, px in window)
    peak_after = max(px for _, px in ev.path.quotes)
    room = peak_after - best_after
    consumed = None if room <= 0 else max(0.0, min(1.0, (ev.entry - best_after) / room))
    return {
        "signal_premium": round(ev.entry, 2),
        "best_entry_next_bars": round(best_after, 2),
        "peak_in_horizon": round(peak_after, 2),
        "improvement_available": round(ev.entry - best_after, 2),
        "improvement_available_pct": round(
            100.0 * (ev.entry - best_after) / ev.entry, 2),
        "consumed_frac": None if consumed is None else round(consumed, 4),
        "entered_at_peak": bool(abs(ev.entry - peak_after) < 1e-9),
    }


def classify_chase(consumed: float | None, cuts: dict | None) -> str:
    if consumed is None or not cuts:
        return "UNKNOWN"
    if consumed <= cuts["ideal"]:
        return "IDEAL_ENTRY"
    if consumed <= cuts["good"]:
        return "GOOD_ENTRY"
    if consumed <= cuts["acceptable"]:
        return "ACCEPTABLE_ENTRY"
    if consumed <= cuts["chased"]:
        return "CHASED_ENTRY"
    return "SEVERELY_CHASED"


def mfe_capture(realised_r: float, mfe_r: float) -> dict:
    """Part 7 + 21 — how much of what the trade offered was actually kept."""
    if mfe_r <= 0:
        return {"mfe_r": round(mfe_r, 3), "realised_r": round(realised_r, 3),
                "capture_pct": None, "giveback_r": 0.0,
                "note": "nothing favourable was ever offered"}
    return {
        "mfe_r": round(mfe_r, 3),
        "realised_r": round(realised_r, 3),
        "capture_pct": round(100.0 * max(0.0, realised_r) / mfe_r, 1),
        "giveback_r": round(mfe_r - realised_r, 3),
    }


def expected_move(atr: float, bars: int = HORIZON_BARS) -> float:
    return atr * math.sqrt(bars)
