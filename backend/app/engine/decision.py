"""The Decision Engine — the heart of Quantum Trader.

It never acts on a single indicator. It collects a *signed, weighted vote*
from every analysis module (trend, VWAP, momentum, RSI, MACD, SuperTrend,
Bollinger, ADX, market structure, breakout, price-action, volume, options
OI/PCR, institutional activity, news) and combines them into four scores:

    BUY_score, EXIT_score, HOLD_score, WAIT_score

The highest-probability decision is emitted with a confidence percentage, a
recommended option (strike selection), an entry range, an ATR-derived stop and
three targets, an expected holding time, a recovery probability, and a plain
list of the reasons that drove the call.

Every number is a transparent, documented heuristic — not a guarantee.
"""
from __future__ import annotations

import numpy as np

from app.analysis import indicators as ind
from app.analysis import options_analytics as oa
from app.analysis import premium as prem
from app.analysis import structure as st
from app.analysis import volume_profile as vp
from app.config import settings
from app.engine import weights
from app.engine.observed_rates import observed_target_rate
from app.models import (
    Candle,
    Decision,
    GateTrace,
    IndicatorSnapshot,
    MarketStatus,
    OptionQuote,
    OptionRecommendation,
    OptionType,
    ScoreBreakdown,
    Signal,
)


def _arrays(candles: list[Candle]):
    o = np.array([c.open for c in candles], dtype=float)
    h = np.array([c.high for c in candles], dtype=float)
    lo = np.array([c.low for c in candles], dtype=float)
    c = np.array([c.close for c in candles], dtype=float)
    v = np.array([c_.volume for c_ in candles], dtype=float)
    return o, h, lo, c, v


def _aggregate_htf(candles: list[Candle], factor: int = 5) -> list[Candle]:
    """Roll 1-min candles up into `factor`-min candles. Chunks are aligned to
    the END so the most recent HTF bar closes on the latest 1-min candle."""
    n = len(candles)
    if n < factor:
        return []
    out: list[Candle] = []
    start = n % factor
    for i in range(start, n, factor):
        grp = candles[i:i + factor]
        if not grp:
            continue
        out.append(
            Candle(
                time=grp[-1].time,
                open=grp[0].open,
                high=max(x.high for x in grp),
                low=min(x.low for x in grp),
                close=grp[-1].close,
                volume=float(sum(x.volume for x in grp)),
            )
        )
    return out


def _htf_trend(candles: list[Candle]) -> tuple[str, float]:
    """Direction + strength from the higher-timeframe chart (structure & EMA
    slope). The aggregation factor is `settings.htf_factor` (default 5 → 5-min).

    Returns (UP|DOWN|SIDEWAYS, strength 0..1). This GATES the trade direction —
    CE only in an UP-trend, PE only in a DOWN-trend, AVOID when flat.
    """
    factor = max(1, int(settings.htf_factor))
    htf = _aggregate_htf(candles, factor)
    if len(htf) < 6:
        return "SIDEWAYS", 0.0
    closes = np.array([c.close for c in htf], dtype=float)
    price = float(closes[-1]) or 1.0
    ema_fast = ind.ema(closes, min(9, len(closes)))
    ema_slow = ind.ema(closes, min(21, len(closes)))
    if ema_fast is None or ema_slow is None:
        return "SIDEWAYS", 0.0
    sep = (ema_fast - ema_slow) / price
    look = min(4, len(closes) - 1)
    slope = (closes[-1] - closes[-1 - look]) / price
    # structure: are the last swing highs/lows stepping up or down?
    seg = htf[-8:]
    hi = [c.high for c in seg]
    lo = [c.low for c in seg]
    hh_hl = hi[-1] >= max(hi[:-1] or [hi[-1]]) and lo[-1] >= min(lo[:-1] or [lo[-1]])
    lh_ll = hi[-1] <= max(hi[:-1] or [hi[-1]]) and lo[-1] <= min(lo[:-1] or [lo[-1]])
    up = ema_fast > ema_slow and slope > 0
    down = ema_fast < ema_slow and slope < 0
    strength = min(1.0, abs(sep) * 350.0 + abs(slope) * 120.0)
    if up and not lh_ll:
        return "UP", strength if hh_hl else strength * 0.7
    if down and not hh_hl:
        return "DOWN", strength if lh_ll else strength * 0.7
    return "SIDEWAYS", 0.0


def compute_indicators(
    candles: list[Candle],
    chain: list[OptionQuote],
    price_change: float = 0.0,
    get_opt_candles=None,
) -> IndicatorSnapshot:
    # No candles yet (feed warming up / historical API rate-limited): return an
    # empty snapshot rather than running numpy reductions on empty arrays.
    if not candles:
        return IndicatorSnapshot()
    o, h, lo, c, v = _arrays(candles)
    ema9 = ind.ema(c[-60:], 9) if len(c) >= 9 else None
    ema20 = ind.ema(c[-80:], 20) if len(c) >= 20 else None
    ema50 = ind.ema(c[-120:], 50) if len(c) >= 50 else None
    macd_line, macd_sig, macd_hist = ind.macd(c)
    atr_val = ind.atr(h, lo, c)
    stl, stdir = ind.supertrend(
        h, lo, c, settings.supertrend_period, settings.supertrend_multiplier
    )
    bb_u, bb_m, bb_l = ind.bollinger(c)
    support, resistance = st.support_resistance(h, lo)
    sh, sl = st.swing_points(h, lo)
    trend = st.trend_from_emas(ema9, ema20, ema50)
    ms = st.market_structure(h, lo)
    call_strike, put_strike = oa.max_oi_strikes(chain)
    inst_dir, inst_strength = oa.institutional_activity(chain)
    inst_label = "Strong" if inst_strength >= 0.5 else "Moderate" if inst_strength >= 0.2 else "Weak"
    chart_pat, chart_dir = st.chart_pattern(h, lo, c, atr_val)
    chart_bias = "BULLISH" if chart_dir > 0 else "BEARISH" if chart_dir < 0 else "NEUTRAL"

    vol_level = "Normal"
    if len(v) >= 21:
        avg = v[-21:-1].mean()
        if avg > 0:
            ratio = v[-1] / avg
            vol_level = "Very High" if ratio >= 2.0 else "High" if ratio >= 1.3 else "Low" if ratio < 0.6 else "Normal"

    # --- Level 1: price-action traps / continuation ---
    fake = st.fake_breakout(h, lo, c, support, resistance, atr_val)
    sweep = st.liquidity_sweep(h, lo, c, atr_val)
    pull = st.pullback(c, ema20, trend, atr_val)
    # A FRESH Supertrend flip = the BUY/SELL marker on a Supertrend chart.
    st_flip = ind.supertrend_flip(
        h, lo, c,
        settings.supertrend_period,
        settings.supertrend_multiplier,
        settings.supertrend_confirm_bars,
    )

    # --- Level 2: option premium behaviour (ATM CE + ATM PE analysed independently) ---
    prem_state = prem_bias = prem_ce = prem_pe = None
    ce_vel = ce_acc = pe_vel = pe_acc = None
    if get_opt_candles is not None and chain:
        spot = float(c[-1])
        atm_ce = min((q for q in chain if q.option_type == OptionType.CALL),
                     key=lambda q: abs(q.strike - spot), default=None)
        atm_pe = min((q for q in chain if q.option_type == OptionType.PUT),
                     key=lambda q: abs(q.strike - spot), default=None)
        if atm_ce and atm_pe:
            ce_beh = prem.analyse(get_opt_candles(atm_ce.symbol))
            pe_beh = prem.analyse(get_opt_candles(atm_pe.symbol))
            prem_ce, prem_pe = ce_beh["state"], pe_beh["state"]
            ce_vel, ce_acc = ce_beh["velocity"], ce_beh["acceleration"]
            pe_vel, pe_acc = pe_beh["velocity"], pe_beh["acceleration"]
            prem_bias, _pstr, _pdetail = prem.underlying_bias(ce_beh, pe_beh)
            # headline premium state = the more active leg
            prem_state = ce_beh["state"] if abs(ce_beh["velocity"]) >= abs(pe_beh["velocity"]) else pe_beh["state"]

    # --- Level 3: OI state ---
    oi = oa.oi_state(chain, price_change) if chain else {}

    # --- Level 5: estimated smart money (honest proxy) ---
    sm = oa.estimated_smart_money(chain, vol_level, price_change) if chain else {}

    # --- Volume profile: POC / VAH / VAL over the RECENT balance (last ~90
    # bars) so a fresh directional push shows up as acceptance outside value,
    # instead of the value area spanning the entire lookback. ---
    prof = vp.volume_profile(candles[-90:])
    poc = prof["poc"] if prof else None
    vah = prof["vah"] if prof else None
    val = prof["val"] if prof else None
    va_pos = vp.value_area_position(float(c[-1]), vah, val)

    return IndicatorSnapshot(
        ema9=_r(ema9), ema20=_r(ema20), ema50=_r(ema50),
        vwap=_r(ind.vwap(h, lo, c, v)),
        rsi=_r(ind.rsi(c)),
        macd=_r(macd_line), macd_signal=_r(macd_sig), macd_hist=_r(macd_hist),
        atr=_r(atr_val), adx=_r(ind.adx(h, lo, c)),
        supertrend=_r(stl), supertrend_dir=stdir,
        bb_upper=_r(bb_u), bb_mid=_r(bb_m), bb_lower=_r(bb_l),
        support=_r(support), resistance=_r(resistance),
        swing_high=_r(sh[-1][1]) if sh else None,
        swing_low=_r(sl[-1][1]) if sl else None,
        trend=trend, market_structure=ms,
        momentum=_r(ind.momentum(c)),
        volume_spike=ind.volume_spike(v),
        breakout=st.breakout_state(c, support, resistance, atr_val),
        candle_pattern=st.candle_pattern(o, h, lo, c),
        chart_pattern=chart_pat,
        chart_pattern_bias=chart_bias,
        pcr=oa.put_call_ratio(chain),
        max_oi_call_strike=call_strike, max_oi_put_strike=put_strike,
        expected_move=oa.expected_move(c[-1], chain, settings.days_to_expiry),
        institutional=inst_dir,
        institutional_label=inst_label,
        volume_level=vol_level,
        fake_signal=fake,
        liquidity_sweep=sweep,
        pullback=pull,
        ignition=st.ignition(
            o, h, lo, c, v,
            body_factor=settings.ignition_body_factor,
            volume_factor=settings.ignition_volume_factor,
        ),
        supertrend_flip=st_flip,
        premium_state=prem_state,
        premium_bias=prem_bias,
        premium_call_state=prem_ce,
        premium_put_state=prem_pe,
        premium_ce_velocity=_r(ce_vel) if ce_vel is not None else None,
        premium_ce_acceleration=_r(ce_acc) if ce_acc is not None else None,
        premium_pe_velocity=_r(pe_vel) if pe_vel is not None else None,
        premium_pe_acceleration=_r(pe_acc) if pe_acc is not None else None,
        oi_state=oi.get("state"),
        oi_bias=oi.get("bias"),
        oi_writing=oi.get("writing"),
        oi_change_pct=oi.get("oi_change_pct"),
        smart_money=sm.get("bias"),
        smart_money_label=sm.get("label"),
        smart_money_detail=sm.get("detail"),
        poc=poc, vah=vah, val=val, value_area_position=va_pos,
    )


def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


def _atr_multiples(status: MarketStatus) -> tuple[float, list[float]]:
    """(stop_atr_mult, [t1,t2,t3]_atr_mults) tuned per regime: give a trend room
    to run, keep a range trade tight, respect wide breakout/volatile swings."""
    table: dict[MarketStatus, tuple[float, list[float]]] = {
        MarketStatus.TRENDING: (1.3, [1.8, 3.0, 4.5]),
        MarketStatus.BREAKOUT: (1.5, [2.0, 3.5, 5.5]),
        MarketStatus.RANGING: (1.0, [1.0, 1.6, 2.2]),
        MarketStatus.REVERSAL: (1.1, [1.4, 2.4, 3.6]),
        MarketStatus.VOLATILE: (1.6, [1.5, 2.5, 3.8]),
        MarketStatus.NEWS_MODE: (1.6, [1.5, 2.5, 3.8]),
        MarketStatus.LOW_VOLUME: (1.0, [1.0, 1.6, 2.2]),
    }
    return table.get(status, (1.2, [1.5, 2.5, 3.8]))


# Map every vote factor onto one of the five dashboard categories the user asked
# for. Anything price-action or indicator-based rolls up under "trend".
_SCORE_CATEGORY = {
    "Market Structure": "trend", "Breakout": "trend", "Retest": "trend",
    "Fake Breakout": "trend", "Fake Breakdown": "trend", "Liquidity Sweep": "trend",
    "Pullback": "trend", "Value Area": "trend", "Chart Pattern": "trend",
    "Candle Pattern": "trend", "Momentum": "trend", "Trend Strength (ADX)": "trend",
    "EMA Trend": "trend", "VWAP": "trend", "SuperTrend": "trend", "MACD": "trend",
    "RSI": "trend", "Bollinger": "trend", "Estimated Smart Money": "trend",
    "Premium Behaviour": "premium",
    "OI State": "oi", "Option Writing": "oi", "Put/Call Ratio": "oi",
    "Volume Spike": "volume",
    "News": "news",
}

# Candlestick shapes grouped by direction — used both for scoring and for the
# "Pattern" (confirmation-candle) entry mode. Kept in sync with the weights in
# _compute_confidence's _cp_bull / _cp_bear tables.
_BULLISH_CANDLES = frozenset({
    "MORNING_STAR", "THREE_WHITE_SOLDIERS", "BULLISH_ENGULFING", "PIERCING",
    "TWEEZER_BOTTOM", "BULLISH_HARAMI", "HAMMER", "MARUBOZU_UP",
})
_BEARISH_CANDLES = frozenset({
    "EVENING_STAR", "THREE_BLACK_CROWS", "BEARISH_ENGULFING", "DARK_CLOUD_COVER",
    "TWEEZER_TOP", "BEARISH_HARAMI", "SHOOTING_STAR", "MARUBOZU_DOWN",
})


def _category_scores(breakdown: list[ScoreBreakdown], want_call: bool) -> dict[str, float]:
    """Break the Trade Score into Trend / Premium / OI / Volume / News (0..100).

    Each category score is 50 = neutral, >50 = the evidence in that bucket
    supports the direction we'd trade, <50 = it argues against us. Transparent:
    it is just the net favourable weight over the active weight in that bucket.
    """
    want_dir = 1 if want_call else -1
    fav: dict[str, float] = {k: 0.0 for k in ("trend", "premium", "oi", "volume", "news")}
    tot: dict[str, float] = {k: 0.0 for k in fav}
    for b in breakdown:
        cat = _SCORE_CATEGORY.get(b.name)
        if cat is None or b.weight <= 0:
            continue
        fav[cat] += b.contribution * want_dir  # +ve when it backs our side
        tot[cat] += b.weight
    out = {}
    for k in fav:
        if tot[k] <= 0:
            out[k] = 50.0
        else:
            out[k] = round(max(0.0, min(100.0, 50.0 + 50.0 * (fav[k] / tot[k]))), 1)
    return out


def _opportunity_label(score: float, signal: Signal) -> str:
    if signal in (Signal.AVOID, Signal.NO_TRADE):
        return "AVOID"
    if score >= 80:
        label = "VERY_HIGH"
    elif score >= 64:
        label = "HIGH"
    elif score >= 45:
        label = "MEDIUM"
    elif score >= 25:
        label = "LOW"
    else:
        label = "AVOID"
    # a non-actionable WAIT should never read as a top-tier opportunity
    if signal == Signal.WAIT and label in ("VERY_HIGH", "HIGH"):
        label = "MEDIUM"
    return label


def _risk_meter(score: float) -> str:
    if score <= 20:
        return "VERY_LOW"
    if score <= 35:
        return "LOW"
    if score <= 55:
        return "MEDIUM"
    if score <= 72:
        return "HIGH"
    return "EXTREME"


def _trap_probabilities(snap: IndicatorSnapshot, want_call: bool, over_extended: bool) -> dict[str, float]:
    """Probability (0..100) the current move is a trap. Derived only from the
    detected fake-break / liquidity-sweep price action + volume confirmation."""
    confirmed_vol = snap.volume_spike or snap.volume_level in ("High", "Very High")
    fake_bo = 78.0 if snap.fake_signal == "FAKE_BREAKOUT" else (
        45.0 if snap.breakout == "BREAKOUT" and not confirmed_vol else 8.0)
    fake_bd = 78.0 if snap.fake_signal == "FAKE_BREAKDOWN" else (
        45.0 if snap.breakout == "BREAKDOWN" and not confirmed_vol else 8.0)
    buy_trap = max(fake_bo, 70.0 if snap.liquidity_sweep == "SWEEP_HIGH" else 0.0)
    sell_trap = max(fake_bd, 70.0 if snap.liquidity_sweep == "SWEEP_LOW" else 0.0)
    if over_extended:
        if want_call:
            buy_trap = min(100.0, buy_trap + 12.0)
        else:
            sell_trap = min(100.0, sell_trap + 12.0)
    return {
        "buy_trap": round(min(100.0, buy_trap), 1),
        "sell_trap": round(min(100.0, sell_trap), 1),
        "fake_breakout": round(min(100.0, fake_bo), 1),
        "fake_breakdown": round(min(100.0, fake_bd), 1),
    }


def classify_market(snap: IndicatorSnapshot, news_high_impact: bool) -> MarketStatus:
    """Regime detection (Phase 1): Trend / Range / Breakout / Reversal (+ the
    Volatile / News / Low-volume safety states).

    Priority order is deliberate:
      1. NEWS       — a high-impact release dominates everything.
      2. BREAKOUT   — a fresh break of a level *confirmed by volume* (a break on
                      thin volume is more likely a fake, so it is NOT a breakout).
      3. REVERSAL   — a reversal candle rejecting the edge of the value area /
                      an S-R level against the prior push, or a detected trap.
      4. TRENDING   — ADX strong AND price accepted OUTSIDE the value area.
      5. VOLATILE / LOW_VOLUME — wide-range churn / dead tape.
      6. RANGING    — the default: balanced, price inside the value area.
    """
    if news_high_impact:
        return MarketStatus.NEWS_MODE

    strong_adx = snap.adx is not None and snap.adx >= 25
    vol_expansion = snap.volume_spike or snap.volume_level in ("High", "Very High")
    reversal_candle = snap.candle_pattern in (
        "BULLISH_ENGULFING", "BEARISH_ENGULFING", "HAMMER", "SHOOTING_STAR",
        "MORNING_STAR", "EVENING_STAR", "PIERCING", "DARK_CLOUD_COVER",
        "TWEEZER_BOTTOM", "TWEEZER_TOP",
    )

    ema_aligned = snap.trend in ("UP", "DOWN")

    # 2. BREAKOUT — only when the break is backed by a volume expansion.
    if snap.breakout in ("BREAKOUT", "BREAKDOWN") and vol_expansion:
        return MarketStatus.BREAKOUT

    # 3. REVERSAL — a trap, or a reversal candle while momentum is NOT strongly
    #    trending (a reversal candle mid-trend is just a pullback, not a regime
    #    change).
    if snap.fake_signal in ("FAKE_BREAKOUT", "FAKE_BREAKDOWN"):
        return MarketStatus.REVERSAL
    if reversal_candle and not strong_adx:
        return MarketStatus.REVERSAL

    # 4. TRENDING — momentum confirmed (strong ADX with EMA alignment). Price
    #    accepted outside the recent value area reinforces it but isn't required
    #    (a healthy trend can ride along its value area).
    if strong_adx and ema_aligned:
        return MarketStatus.TRENDING

    # 5. Volatility safety states.
    if snap.atr and snap.bb_mid and snap.atr > 0.011 * snap.bb_mid:
        return MarketStatus.VOLATILE
    if snap.volume_level == "Low" and not strong_adx:
        return MarketStatus.LOW_VOLUME

    # 6. Default: balance / range (inside value, no strong momentum).
    return MarketStatus.RANGING


# ------- weighted voting (7-level priority hierarchy) -------
# Level 1 Futures price action (dominant) → 2 Premium behaviour → 3 Open interest
# → 4 Volume → 5 Estimated smart money → 6 News → 7 Indicators (CONFIRM ONLY).
# Each vote is (name, direction{-1,0,1}, weight, detail, level, confirm_only).
def _gather_votes(snap: IndicatorSnapshot, close: float, news_score: float, chain: list[OptionQuote]):
    votes: list[tuple[str, float, float, str, int, bool]] = []

    def add(name, direction, weight, detail, level, confirm_only=False):
        # apply the self-learned multiplier so proven factors carry more weight
        votes.append((name, direction, weight * weights.store.multiplier(name), detail, level, confirm_only))

    # ================= LEVEL 1 — FUTURES PRICE ACTION (highest weight) =================
    if snap.market_structure == "HH_HL":
        add("Market Structure", 1, 18, "higher-highs / higher-lows", 1)
    elif snap.market_structure == "LH_LL":
        add("Market Structure", -1, 18, "lower-highs / lower-lows", 1)

    if snap.breakout == "BREAKOUT":
        add("Breakout", 1, 16, "broke resistance", 1)
    elif snap.breakout == "BREAKDOWN":
        add("Breakout", -1, 16, "broke support", 1)
    elif snap.breakout == "RETEST":
        add("Retest", 0, 3, "retesting level", 1)

    # traps — these OPPOSE the naive breakout read (a fake move reverses)
    if snap.fake_signal == "FAKE_BREAKOUT":
        add("Fake Breakout", -1, 15, "poked above resistance, rejected back", 1)
    elif snap.fake_signal == "FAKE_BREAKDOWN":
        add("Fake Breakdown", 1, 15, "poked below support, reclaimed", 1)

    if snap.liquidity_sweep == "SWEEP_HIGH":
        add("Liquidity Sweep", -1, 13, "swept highs then reversed (stop-hunt)", 1)
    elif snap.liquidity_sweep == "SWEEP_LOW":
        add("Liquidity Sweep", 1, 13, "swept lows then reversed (stop-hunt)", 1)

    if snap.pullback == "PULLBACK_UP":
        add("Pullback", 1, 9, "uptrend pullback to mean (continuation)", 1)
    elif snap.pullback == "PULLBACK_DOWN":
        add("Pullback", -1, 9, "downtrend pop to mean (continuation)", 1)

    # Volume profile: acceptance ABOVE the value area is bullish, BELOW is
    # bearish (price trading away from where it was fairly valued).
    if snap.value_area_position == "ABOVE_VALUE":
        add("Value Area", 1, 8, f"accepted above value (VAH {snap.vah})", 1)
    elif snap.value_area_position == "BELOW_VALUE":
        add("Value Area", -1, 8, f"accepted below value (VAL {snap.val})", 1)

    _strong_shapes = {"V_REVERSAL", "INVERTED_V", "DOUBLE_BOTTOM", "DOUBLE_TOP"}
    if snap.chart_pattern and snap.chart_pattern_bias in ("BULLISH", "BEARISH"):
        cdir = 1 if snap.chart_pattern_bias == "BULLISH" else -1
        cw = 12 if snap.chart_pattern in _strong_shapes else 7
        add("Chart Pattern", cdir, cw, snap.chart_pattern.replace("_", " ").lower(), 1)

    # Classic candlestick shapes as a price-action vote. Weighted by how much
    # information each carries: three-candle reversals (star / soldiers) strongest,
    # two-candle (engulfing / piercing / harami / tweezer) next, single-candle last.
    _cp_bull = {
        "MORNING_STAR": 11, "THREE_WHITE_SOLDIERS": 10,
        "BULLISH_ENGULFING": 8, "PIERCING": 7, "TWEEZER_BOTTOM": 7, "BULLISH_HARAMI": 5,
        "HAMMER": 5, "MARUBOZU_UP": 5,
    }
    _cp_bear = {
        "EVENING_STAR": 11, "THREE_BLACK_CROWS": 10,
        "BEARISH_ENGULFING": 8, "DARK_CLOUD_COVER": 7, "TWEEZER_TOP": 7, "BEARISH_HARAMI": 5,
        "SHOOTING_STAR": 5, "MARUBOZU_DOWN": 5,
    }
    if snap.candle_pattern in _cp_bull:
        add("Candle Pattern", 1, _cp_bull[snap.candle_pattern], snap.candle_pattern.replace("_", " ").lower(), 1)
    elif snap.candle_pattern in _cp_bear:
        add("Candle Pattern", -1, _cp_bear[snap.candle_pattern], snap.candle_pattern.replace("_", " ").lower(), 1)

    if snap.momentum is not None:
        d = 1 if snap.momentum > 0.05 else -1 if snap.momentum < -0.05 else 0
        add("Momentum", d, 8, f"10-bar momentum {snap.momentum:.2f}%", 1)

    if snap.adx is not None and snap.adx >= 25 and snap.trend in ("UP", "DOWN"):
        add("Trend Strength (ADX)", 1 if snap.trend == "UP" else -1, 7, f"ADX {snap.adx:.0f} strong", 1)

    # ================= LEVEL 2 — OPTION PREMIUM BEHAVIOUR =================
    if snap.premium_bias in ("BULLISH", "BEARISH"):
        pdir = 1 if snap.premium_bias == "BULLISH" else -1
        detail = f"CE {snap.premium_call_state}/PE {snap.premium_put_state}".lower()
        add("Premium Behaviour", pdir, 14, detail, 2)

    # ================= LEVEL 3 — OPEN INTEREST =================
    if snap.oi_bias in ("BULLISH", "BEARISH"):
        odir = 1 if snap.oi_bias == "BULLISH" else -1
        add("OI State", odir, 12, (snap.oi_state or "").replace("_", " ").lower(), 3)
    if snap.oi_writing == "PUT_WRITING":
        add("Option Writing", 1, 7, "put writing (support building)", 3)
    elif snap.oi_writing == "CALL_WRITING":
        add("Option Writing", -1, 7, "call writing (upside capped)", 3)
    if snap.pcr is not None:
        if snap.pcr > 1.2:
            add("Put/Call Ratio", 1, 5, f"PCR {snap.pcr} (bullish extreme)", 3)
        elif snap.pcr < 0.7:
            add("Put/Call Ratio", -1, 5, f"PCR {snap.pcr} (bearish extreme)", 3)

    # ================= LEVEL 4 — VOLUME =================
    if snap.volume_spike:
        d = 1 if (snap.momentum or 0) >= 0 else -1
        add("Volume Spike", d, 6, "volume surge confirms move", 4)

    # ================= LEVEL 5 — ESTIMATED SMART MONEY (proxy, not order-flow) =========
    if snap.smart_money in ("BULLISH", "BEARISH"):
        sdir = 1 if snap.smart_money == "BULLISH" else -1
        add("Estimated Smart Money", sdir, 8, snap.smart_money_detail or "OI+volume proxy", 5)

    # ================= LEVEL 6 — NEWS =================
    if abs(news_score) > 0.15:
        add("News", 1 if news_score > 0 else -1, 10 * min(1.0, abs(news_score) + 0.3), f"news score {news_score:+.2f}", 6)

    # ================= LEVEL 7 — INDICATORS (CONFIRM ONLY — never trigger a BUY) =======
    if snap.trend == "UP":
        add("EMA Trend", 1, 6, "EMA9>EMA20>EMA50", 7, confirm_only=True)
    elif snap.trend == "DOWN":
        add("EMA Trend", -1, 6, "EMA9<EMA20<EMA50", 7, confirm_only=True)
    if snap.vwap is not None:
        add("VWAP", 1 if close > snap.vwap else -1, 5, "price " + ("above" if close > snap.vwap else "below") + " VWAP", 7, confirm_only=True)
    if snap.supertrend_dir is not None:
        add("SuperTrend", snap.supertrend_dir, 5, "SuperTrend " + ("up" if snap.supertrend_dir > 0 else "down"), 7, confirm_only=True)
    if snap.macd_hist is not None:
        add("MACD", 1 if snap.macd_hist > 0 else -1, 3, f"MACD hist {snap.macd_hist:.2f}", 7, confirm_only=True)
    if snap.rsi is not None:
        if snap.rsi > 70:
            add("RSI", -1, 3, f"RSI {snap.rsi:.0f} overbought", 7, confirm_only=True)
        elif snap.rsi < 30:
            add("RSI", 1, 3, f"RSI {snap.rsi:.0f} oversold", 7, confirm_only=True)
    if snap.bb_upper and snap.bb_lower:
        if close >= snap.bb_upper:
            add("Bollinger", -1, 2, "tag upper band", 7, confirm_only=True)
        elif close <= snap.bb_lower:
            add("Bollinger", 1, 2, "tag lower band", 7, confirm_only=True)

    return votes


# Blocker order is DELIBERATE: the earliest gate in the pipeline that failed is
# the primary blocker, because removing a later one would not have produced a
# trade anyway. Statistics built on a mis-attributed blocker point at the wrong
# gate, which is how a useful gate gets removed and a useless one kept.
def _gate_states(trace: GateTrace) -> list[tuple[str, bool]]:
    """Every gate as (label, satisfied), in pipeline order."""
    return [
        ("RISKY_REGIME", trace.not_risky),
        ("HTF_TREND", trace.htf_trend_ok),
        ("RANGING", trace.not_ranging),
        ("TRAP", trace.no_trap),
        ("ONE_MIN_CONFLICT", trace.one_min_ok),
        ("DIRECTIONAL_STRENGTH", trace.strength_ok),
        ("ENTRY_TRIGGER", trace.entry_ready),
        ("OVER_EXTENDED", trace.not_over_extended),
        ("PREMIUM_EXPLOSION", trace.premium_not_exploding),
        ("INDICATOR_CONFLICT", trace.indicators_not_conflicting),
        ("CONFIDENCE", trace.confidence_ok),
        ("REWARD_RISK", trace.reward_risk_ok),
        ("NEWS_AGAINST", trace.news_not_opposing),
        ("PREMIUM_QUALITY", trace.premium_quality_ok),
        ("TRAP_PROBABILITY", trace.trap_probability_ok),
        ("BOARD_REGIME", trace.board_regime_ok),
        ("BOARD_ADX", trace.board_adx_ok),
        ("BOARD_EXPECTED_MOVE", trace.board_move_ok),
        ("BOARD_5MIN_AGREEMENT", trace.board_htf_agrees),
        ("BOARD_15MIN_BIAS", trace.board_bias15_agrees),
        ("BOARD_OPPORTUNITY", trace.board_opportunity_ok),
    ]


def rank_gate_trace(trace: GateTrace) -> GateTrace:
    """Fill in ``blockers`` / ``primary_blocker`` / ``secondary_blocker``.

    Mutates and returns the same object so callers that add their own gates (the
    board layer) can re-rank after appending. Read-only with respect to any
    trading decision.
    """
    failed = [label for label, ok in _gate_states(trace) if not ok]
    trace.blockers = failed
    trace.primary_blocker = failed[0] if failed else None
    trace.secondary_blocker = failed[1] if len(failed) > 1 else None
    return trace


def decide(
    candles: list[Candle],
    chain: list[OptionQuote],
    snap: IndicatorSnapshot,
    news_score: float,
    news_high_impact: bool,
    market_status: MarketStatus,
    in_position: bool,
    position_option_type: OptionType | None,
    news_factor: float = 1.0,
    spot: float | None = None,
) -> tuple[Decision, list[ScoreBreakdown]]:
    # No candles yet (feed warming up / historical API rate-limited): emit a
    # neutral WAIT instead of indexing an empty array (which would 500 the snapshot).
    if not candles:
        return Decision(
            signal=Signal.WAIT,
            confidence=0.0,
            signal_strength=0.0,
            trade_quality="C",
            market_signal=Signal.WAIT,
            market_confidence=0.0,
            spot_price=round(float(spot), 1) if spot is not None else None,
            reasons=["Waiting for market data — feed warming up or rate-limited."],
        ), []
    _, h, lo, c, _ = _arrays(candles)
    close = float(c[-1])
    # The live price (used to build the option chain / shown in the header) can
    # differ from the last completed candle's close; strike selection, ATM and
    # the displayed spot must all use that same live price so they stay
    # consistent. Fall back to the candle close when no live price is supplied.
    spot_ref = float(spot) if spot is not None else close
    atr_val = snap.atr or (0.004 * close)

    votes = _gather_votes(snap, close, news_score, chain)
    breakdown: list[ScoreBreakdown] = []
    # Primary evidence (levels 1-6) drives the decision; indicators (level 7)
    # may only CONFIRM — they can never, by themselves, generate a BUY.
    p_bull = p_bear = p_weight = 0.0
    conf_bull = conf_bear = 0.0
    for name, direction, weight, detail, level, confirm_only in votes:
        contribution = direction * weight
        if confirm_only:
            if contribution > 0:
                conf_bull += contribution
            elif contribution < 0:
                conf_bear += -contribution
        else:
            p_weight += weight
            if contribution > 0:
                p_bull += contribution
            elif contribution < 0:
                p_bear += -contribution
        breakdown.append(
            ScoreBreakdown(
                name=name,
                signal="BULLISH" if direction > 0 else "BEARISH" if direction < 0 else "NEUTRAL",
                weight=round(weight, 1),
                contribution=round(contribution, 1),
                detail=detail,
                level=level,
                confirm_only=confirm_only,
            )
        )

    p_weight = max(p_weight, 1.0)
    net = (p_bull - p_bear) / p_weight  # -1..1 (PRIMARY evidence only)
    directional_strength = abs(net)  # 0..1
    agreement = (max(p_bull, p_bear)) / max(p_bull + p_bear, 1e-9)  # 0.5..1

    # ======================= DIRECTION IS SET BY THE 5-MIN CHART ==============
    # Redesigned engine: the higher-timeframe (5-min) futures structure/trend
    # decides CE vs PE. The 1-min chart is used ONLY to time the execution
    # (pullback entry). If the 5-min has no clear trend we AVOID — no forced
    # BUY/SELL on a low-quality setup.
    htf_dir, htf_str = _htf_trend(candles)
    htf_trending = htf_dir in ("UP", "DOWN")
    if htf_trending:
        want_call = htf_dir == "UP"          # direction gated by 5-min trend
    else:
        want_call = net >= 0                 # display only; setup will AVOID

    # --- Confirmed bottom/top REVERSAL (early-turn entry) --------------------
    # A turn is "confirmed" (not a blind knife-catch) when price is AT a swing
    # extreme AND one of: a stop-run reclaim (liquidity sweep), a V-reversal, or
    # a reversal candle at a stretched (oversold/overbought) extreme. This lets a
    # fresh entry land near the LOW (CE) / HIGH (PE) instead of the peak of a run.
    rsi_v = snap.rsi
    bull_reversal = settings.reversal_entry_enabled and (
        snap.liquidity_sweep == "SWEEP_LOW"
        or snap.chart_pattern == "V_REVERSAL"
        or (
            snap.candle_pattern in _BULLISH_CANDLES
            and snap.swing_low is not None
            and atr_val > 0
            and 0 <= spot_ref - snap.swing_low <= settings.reversal_max_atr_from_swing * atr_val
            and (rsi_v is None or rsi_v <= settings.reversal_rsi_oversold)
        )
    )
    bear_reversal = settings.reversal_entry_enabled and (
        snap.liquidity_sweep == "SWEEP_HIGH"
        or snap.chart_pattern == "INVERTED_V"
        or (
            snap.candle_pattern in _BEARISH_CANDLES
            and snap.swing_high is not None
            and atr_val > 0
            and 0 <= snap.swing_high - spot_ref <= settings.reversal_max_atr_from_swing * atr_val
            and (rsi_v is None or rsi_v >= 100.0 - settings.reversal_rsi_oversold)
        )
    )
    # When the 5-min trend is undecided, a confirmed reversal SETS the side so we
    # can take the turn early; it never fights an already-established 5-min trend.
    reversal_entry = False
    if not htf_trending:
        if bull_reversal and not bear_reversal:
            want_call = True
            reversal_entry = True
        elif bear_reversal and not bull_reversal:
            want_call = False
            reversal_entry = True

    # --- SUPERTREND FLIP (the TradingView BUY/SELL marker) -------------------
    # The ATR trailing band flipped on the just-closed candle. This is a
    # trend-CHANGE signal, so unlike every other trigger it is allowed to SET the
    # side against the still-established 5-min trend — that is the entire point:
    # at the flip the 5-min chart still reads the OLD direction, and waiting for
    # it to agree is what delayed the entry until the move had already run.
    # Counter-trend by construction, therefore default-off and paper-first.
    supertrend_entry = False
    if settings.supertrend_entry_enabled and not in_position:
        if snap.supertrend_flip == "FLIP_UP":
            want_call = True
            supertrend_entry = True
        elif snap.supertrend_flip == "FLIP_DOWN":
            want_call = False
            supertrend_entry = True

    # 1-min primary evidence must AGREE with the 5-min direction to execute.
    one_min_agrees = (net >= 0) == want_call

    # Do the lagging indicators CONFIRM the traded direction? (confirm-only)
    conf_net = conf_bull - conf_bear
    if abs(conf_net) < 1e-6:
        confirm = "silent"
    elif (conf_net > 0) == want_call:
        confirm = "confirms"
    else:
        confirm = "conflicts"

    # Confidence (0..100), built ADDITIVELY from independent evidence so a
    # genuinely clean setup can actually clear the 75-80 gate (a multiplicative
    # form stacks too many <1 factors and never gets there). Components:
    #   base 40  — a valid 5-min trend exists (the precondition to trade at all)
    #   +25*htf_strength           — how strong/clean that 5-min trend is
    #   +20*agreement_scaled       — how one-sided the 1-min primary evidence is
    #   +10*directional_push       — magnitude of the 1-min directional lean
    #   +/- confirmation           — lagging indicators confirm (+5) / conflict (-18)
    #   - counter-trend penalty    — 1-min pushing against the trend
    agree_scaled = max(0.0, (agreement - 0.5) / 0.5)  # 0.5..1 -> 0..1
    push = min(1.0, directional_strength / 0.5)
    if htf_trending:
        base_conf = 40.0 + 25.0 * htf_str + 20.0 * agree_scaled + 10.0 * push
        if confirm == "confirms":
            base_conf += 5.0
        elif confirm == "conflicts":
            base_conf -= 18.0
        if not one_min_agrees:
            # a shallow counter-move is the pullback we want; only a STRONG
            # opposition should bite.
            base_conf -= 12.0 if directional_strength >= 0.30 else 5.0
    else:
        base_conf = 30.0 + 20.0 * directional_strength  # no trend -> capped low
    confidence = max(0.0, min(98.0, base_conf * news_factor))

    trade_score = 100.0 * min(1.0, directional_strength * 1.3) * (0.6 + 0.4 * agreement)
    wait_score = 100.0 * (1.0 - directional_strength) * (0.7 if market_status == MarketStatus.RANGING else 0.5)
    exit_score = 0.0

    # Volatile / news with no clear direction => stand aside
    risky = market_status in (MarketStatus.VOLATILE, MarketStatus.NEWS_MODE) and directional_strength < 0.4

    reasons = _top_reasons(breakdown, want_call)

    # ---- Confidence gate: never below the 75-80% floor the user asked for.
    gate = max(75.0, settings.buy_min_confidence)
    if market_status in (MarketStatus.TRENDING, MarketStatus.BREAKOUT):
        gate = max(75.0, gate - 3.0)
    elif market_status == MarketStatus.RANGING:
        gate += 6.0

    # The option to trade for a fresh entry, aligned to the 5-min direction.
    rec = _select_option(chain, spot_ref, want_call, atr_val)

    # ---- EXECUTION TIMING on the 1-min chart: enter on a PULLBACK, never chase
    # the breakout candle. Also reject traps that run against our direction.
    close_px = spot_ref
    pullback_ok = (want_call and snap.pullback == "PULLBACK_UP") or (
        not want_call and snap.pullback == "PULLBACK_DOWN"
    )
    # Shallow-pullback entry: price has ticked 0.25-1.6 ATR back off the recent
    # swing extreme while still on the trend side of EMA20 — a "buy the dip in
    # the trend" entry that also catches trends too steep for price to reach
    # EMA20 (over-extension is still filtered separately, so this never chases).
    if atr_val and atr_val > 0 and len(h) >= 8:
        if want_call:
            retr = float(h[-8:].max()) - close_px
            on_trend_side = snap.ema20 is None or close_px >= snap.ema20
        else:
            retr = close_px - float(lo[-8:].min())
            on_trend_side = snap.ema20 is None or close_px <= snap.ema20
        if on_trend_side and 0.25 * atr_val <= retr <= 1.6 * atr_val:
            pullback_ok = True
    retest_ok = snap.breakout == "RETEST"
    fresh_breakout_candle = snap.breakout in ("BREAKOUT", "BREAKDOWN")
    # Over-extension = buying the climax spike. Use STRICT extremes (a strong
    # trend legitimately holds RSI 70+/30-, so 68/32 would block every trend
    # entry) AND, critically, a confirmed pullback/retest already relieves the
    # extension — so it never vetoes the very dip we want to buy.
    if want_call:
        raw_ext = (snap.rsi is not None and snap.rsi >= 80) or (
            snap.bb_upper is not None and close_px >= snap.bb_upper
        )
    else:
        raw_ext = (snap.rsi is not None and snap.rsi <= 20) or (
            snap.bb_lower is not None and close_px <= snap.bb_lower
        )
    over_extended = raw_ext and not (pullback_ok or retest_ok)
    trap_against = (
        (want_call and snap.fake_signal == "FAKE_BREAKOUT")
        or (not want_call and snap.fake_signal == "FAKE_BREAKDOWN")
        or (want_call and snap.liquidity_sweep == "SWEEP_HIGH")
        or (not want_call and snap.liquidity_sweep == "SWEEP_LOW")
    )
    # Conservative (pullback) readiness: enter only on a pullback / retest, and
    # NOT on the raw breakout candle.
    base_ready = (pullback_ok or retest_ok) and not (fresh_breakout_candle and not retest_ok)
    # MOMENTUM mode: also take a CONFIRMED trend continuation (5-min trend + 1-min
    # agree, strong enough, and not over-extended) so clean one-directional runs
    # that never pull back are still caught. Over-extension still vetoes climax
    # tops, so this follows trends without buying blow-off spikes.
    momentum_ok = (
        settings.entry_mode in ("momentum", "auto")
        and htf_trending
        and one_min_agrees
        and directional_strength >= settings.momentum_min_strength
        and not over_extended
    )
    # PATTERN mode (the "confirmation-candle" method): enter when the just-closed
    # candle is a recognised pattern that agrees with the 5-min trend direction —
    # bullish shapes for CE, bearish for PE. Fires far more often than pullback/
    # momentum, but still trend-aligned and never on a climax (over-extended) bar.
    pattern_agrees = (want_call and snap.candle_pattern in _BULLISH_CANDLES) or (
        not want_call and snap.candle_pattern in _BEARISH_CANDLES
    )
    pattern_ok = (
        settings.entry_mode in ("pattern", "auto")
        and htf_trending
        and pattern_agrees
        and not over_extended
    )
    # IGNITION: the first expansion candle of a move. Momentum only fires once
    # ``directional_strength`` has accumulated, which takes several candles and
    # lands near the top of the run; this joins the move on candle 1. It still
    # requires agreement with the higher-timeframe bias, so it is a continuation
    # entry, never a counter-trend guess.
    ignition_ok = (
        settings.ignition_entry_enabled
        and htf_trending
        and not over_extended
        and snap.ignition == ("IGNITION_UP" if want_call else "IGNITION_DOWN")
    )
    # SUPERTREND: the ATR trailing band just flipped on the closed candle — the
    # BUY/SELL marker on a Supertrend chart. Unlike momentum this commits on the
    # flip bar itself, so the entry sits at the start of the expansion rather than
    # after strength has accumulated. HTF agreement is required by default; turn
    # ``supertrend_require_htf`` off for the raw indicator behaviour.
    supertrend_ok = (
        supertrend_entry
        and not over_extended
        and (not settings.supertrend_require_htf or htf_trending)
    )
    entry_ready = base_ready or momentum_ok or pattern_ok or ignition_ok or supertrend_ok
    if supertrend_ok:
        entry_trigger = "SUPERTREND"
    elif ignition_ok:
        entry_trigger = "IGNITION"
    elif pullback_ok:
        entry_trigger = "PULLBACK"
    elif retest_ok:
        entry_trigger = "BREAKOUT_RETEST"
    elif momentum_ok:
        entry_trigger = "MOMENTUM"
    elif pattern_ok:
        entry_trigger = "PATTERN"
    elif htf_trending:
        entry_trigger = "WAIT_PATTERN" if settings.entry_mode == "pattern" else "WAIT_SETUP"
    else:
        entry_trigger = "NONE"

    # ---- Position-INDEPENDENT market scan -> BUY / WAIT / AVOID (+ NO_TRADE).
    # Gate trace (observability only): filled in as each gate is evaluated below,
    # so a WAIT can name the condition that refused it instead of being read as
    # "the engine saw nothing". Nothing here feeds back into a decision.
    trace = GateTrace()
    ranging_block = settings.ranging_blocks_entry and market_status == MarketStatus.RANGING
    directional_ok = directional_strength >= max(
        settings.buy_min_primary_strength, settings.neutral_dead_band
    )

    # A HARD 1-min conflict (strong momentum against the 5-min trend) invalidates
    # the setup. A SHALLOW counter-move is just the pullback we want to enter on,
    # so it must NOT block the BUY (that was the old contradiction: "wait for a
    # pullback" but "only buy when the 1-min agrees" can never both be true).
    one_min_conflict_hard = (not one_min_agrees) and directional_strength >= 0.35

    # AVOID = there is no tradeable thesis at all (not merely "wait for entry").
    avoid = (
        not htf_trending          # no 5-min trend -> no direction
        or trap_against           # a trap is running against our side
        or one_min_conflict_hard  # 1-min fights the trend hard
        or ranging_block
    )
    # Don't CHASE a premium that's already spiking vertically (buying the top).
    leg_prem_state = snap.premium_call_state if want_call else snap.premium_put_state
    premium_exploding = (
        settings.veto_premium_explosion and leg_prem_state == "EXPLOSION"
    )
    # Every flag is forced to a plain bool: several of these conditions come out
    # of numpy comparisons as np.bool_, which pydantic cannot serialise, and a
    # diagnostic must never be the thing that breaks a response.
    trace.htf_trend_ok = bool(htf_trending)
    trace.one_min_ok = bool(not one_min_conflict_hard)
    trace.no_trap = bool(not trap_against)
    trace.not_ranging = bool(not ranging_block)
    trace.not_risky = bool(not risky)
    trace.strength_ok = bool(directional_ok)
    trace.entry_ready = bool(entry_ready)
    trace.not_over_extended = bool(not over_extended)
    trace.premium_not_exploding = bool(not premium_exploding)
    trace.confidence_ok = bool(confidence >= gate)
    trace.indicators_not_conflicting = bool(confirm != "conflicts")
    trace.entry_trigger = entry_trigger
    trace.confidence = round(float(confidence), 1)
    trace.confidence_gate = round(float(gate), 1)
    trace.directional_strength = round(float(directional_strength), 3)
    market_buy_ok = (
        not risky
        and not avoid
        and directional_ok
        and entry_ready
        and not over_extended
        and not premium_exploding
        and confidence >= gate
        and confirm != "conflicts"
    )
    # --- Confirmed-reversal BUY (early-turn entry, may fire with no 5-min trend).
    # Uses its own confirmation-based confidence + a slightly lower gate, but
    # still refuses traps, ranging chop, over-extension and a chased premium.
    reversal_confidence = 0.0
    if reversal_entry and not in_position:
        reversal_confidence = 60.0
        if snap.liquidity_sweep in ("SWEEP_LOW", "SWEEP_HIGH"):
            reversal_confidence += 12.0
        if snap.chart_pattern in ("V_REVERSAL", "INVERTED_V"):
            reversal_confidence += 8.0
        if (want_call and snap.candle_pattern in _BULLISH_CANDLES) or (
            (not want_call) and snap.candle_pattern in _BEARISH_CANDLES
        ):
            reversal_confidence += 8.0
        if rsi_v is not None:
            if want_call and rsi_v <= settings.reversal_rsi_oversold:
                reversal_confidence += min(10.0, (settings.reversal_rsi_oversold - rsi_v) * 0.6)
            elif (not want_call) and rsi_v >= 100.0 - settings.reversal_rsi_oversold:
                reversal_confidence += min(
                    10.0, (rsi_v - (100.0 - settings.reversal_rsi_oversold)) * 0.6
                )
        reversal_confidence = min(88.0, reversal_confidence * news_factor)
    # A Supertrend flip carries its own confidence: the flip itself, plus how much
    # the flip bar and volume back it. It deliberately does NOT require the 5-min
    # trend, so it uses a slightly lower gate — but it still refuses traps, chop,
    # over-extension and a chased premium.
    supertrend_confidence = 0.0
    if supertrend_ok:
        supertrend_confidence = 62.0
        if (want_call and snap.ignition == "IGNITION_UP") or (
            (not want_call) and snap.ignition == "IGNITION_DOWN"
        ):
            supertrend_confidence += 10.0  # the flip bar is itself an expansion
        if snap.volume_level in ("High", "Very High"):
            supertrend_confidence += 6.0
        if (want_call and snap.candle_pattern in _BULLISH_CANDLES) or (
            (not want_call) and snap.candle_pattern in _BEARISH_CANDLES
        ):
            supertrend_confidence += 6.0
        if market_status in (MarketStatus.TRENDING, MarketStatus.BREAKOUT):
            supertrend_confidence += 6.0
        supertrend_confidence = min(88.0, supertrend_confidence * news_factor)
    supertrend_gate = max(70.0, gate - 8.0)
    supertrend_buy_ok = (
        supertrend_ok
        and not in_position
        and not risky
        and not trap_against
        and not ranging_block
        and not over_extended
        and not premium_exploding
        and supertrend_confidence >= supertrend_gate
    )

    reversal_gate = max(70.0, gate - 8.0)
    reversal_buy_ok = (
        reversal_entry
        and not in_position
        and not risky
        and not trap_against
        and not ranging_block
        and not over_extended
        and not premium_exploding
        and reversal_confidence >= reversal_gate
    )
    if risky:
        market_signal = Signal.NO_TRADE
        market_confidence = 50 + 30 * (1 - directional_strength)
    elif market_buy_ok:
        market_signal = Signal.BUY
        market_confidence = confidence
    elif supertrend_buy_ok:
        market_signal = Signal.BUY
        market_confidence = supertrend_confidence
        confidence = supertrend_confidence
        entry_trigger = "SUPERTREND"
    elif reversal_buy_ok:
        market_signal = Signal.BUY
        market_confidence = reversal_confidence
        confidence = reversal_confidence
        entry_trigger = "REVERSAL"
    elif avoid:
        market_signal = Signal.AVOID
        market_confidence = confidence
    else:
        market_signal = Signal.WAIT   # valid trend, waiting for a clean entry
        market_confidence = confidence

    # ---- Position-aware signal (what to do with what YOU hold) ----
    reversal = False
    reversal_option: str | None = None
    reversal_option_type: OptionType | None = None

    if in_position:
        aligned = (position_option_type == OptionType.CALL and want_call) or (
            position_option_type == OptionType.PUT and not want_call
        )
        # A CONFIRMED reversal now requires the 5-MIN TREND ITSELF to have
        # flipped against the held side (not just a 1-min wiggle), plus genuine
        # opposite strength and non-conflicting indicators. The trailing stop
        # (in state.py) — not a CE<->PUT flip — handles minor adverse moves.
        held_is_call = position_option_type == OptionType.CALL
        htf_against = (held_is_call and htf_dir == "DOWN") or (
            not held_is_call and htf_dir == "UP"
        )
        confirmed_reversal = (
            not aligned
            and htf_against
            and directional_strength >= settings.reversal_min_strength
            and confirm != "conflicts"
            and market_status != MarketStatus.RANGING
        )
        if not confirmed_reversal:
            # Stay with the position (ride minor noise; stop-loss handles risk).
            signal = Signal.HOLD
            confidence = max(confidence, 55 + 40 * agreement * max(directional_strength, 0.2))
            if not aligned:
                reasons = [
                    "Minor move against you — HOLDING (no confirmed reversal); "
                    "your stop-loss protects the downside"
                ] + reasons[:2]
        else:
            # Confirmed opposite edge -> EXIT and flag the FLIP to the other side.
            signal = Signal.EXIT
            exit_score = 60 + 40 * (1 - agreement + directional_strength)
            confidence = min(97, exit_score)
            if rec is not None:
                reversal = True
                reversal_option = rec.option_symbol
                reversal_option_type = rec.option_type
                held = position_option_type.value if position_option_type else "position"
                flip = "PUT" if reversal_option_type == OptionType.PUT else "CALL"
                reasons = [
                    f"Confirmed reversal — EXIT your {held} and consider {flip} {reversal_option}",
                ] + reasons[:3]
            else:
                reasons = ["Confirmed reversal — signal no longer supports the position"] + reasons[:3]
    else:
        signal = market_signal
        confidence = market_confidence
        if risky:
            reasons = [f"Market {market_status.value.lower()} with no clear edge — stand aside"] + reasons[:2]
        elif signal == Signal.AVOID:
            if not htf_trending:
                why = "no clear 5-min trend — no CE/PE edge (don't force a trade)"
            elif trap_against:
                why = "a trap (fake breakout / liquidity sweep) is running against the trend"
            elif ranging_block:
                why = "market is ranging/choppy — standing aside until a clean trend or breakout"
            else:
                why = "1-min price action is fighting the 5-min trend"
            reasons = [f"AVOID — {why}"] + reasons[:2]
        elif signal == Signal.WAIT:
            side = "CE" if want_call else "PE"
            if over_extended:
                why = f"5-min trend is {htf_dir} ({side}) but price is over-extended — waiting for a pullback (no chasing)"
            elif not entry_ready:
                why = f"5-min trend is {htf_dir} ({side}) — waiting for a pullback/retest to time the entry"
            elif confirm == "conflicts":
                why = "indicators conflict with the price-action read — waiting for confirmation"
            elif confidence < gate:
                why = f"setup forming but confidence {confidence:.0f}% is below the {gate:.0f}% quality gate"
            elif not directional_ok:
                why = f"5-min trend is {htf_dir} ({side}) but the 1-min push is too weak to commit"
            else:
                why = f"5-min trend is {htf_dir} ({side}) — waiting for a cleaner entry"
            reasons = [f"WAIT — {why}"] + reasons[:2]

    signal_strength = round(min(100.0, directional_strength * 100 * (0.7 + 0.3 * agreement)), 1)
    quality = _quality(signal_strength, agreement, market_status)

    # ---- ATR-based DYNAMIC targets & stop-loss, adapted to the regime -------
    # Stop/target distances are measured in UNDERLYING points (ATR multiples),
    # widened in trend/breakout and tightened in range, then anchored to nearby
    # structure (swing S/R) and mapped to option premium via the option delta.
    premium = rec.premium if rec else None
    entry_range = sl = t1 = t2 = t3 = None
    hold_minutes = None
    recovery_prob = None
    sl_mult, tgt_mults = _atr_multiples(market_status)
    stop_dist_u = sl_mult * atr_val
    # structure-aware stop: never place it inside the level price must break.
    if want_call and snap.support and 0 < spot_ref - snap.support < 2.5 * atr_val:
        stop_dist_u = max(stop_dist_u, spot_ref - snap.support + 0.15 * atr_val)
    elif (not want_call) and snap.resistance and 0 < snap.resistance - spot_ref < 2.5 * atr_val:
        stop_dist_u = max(stop_dist_u, snap.resistance - spot_ref + 0.15 * atr_val)
    underlying_stop = round(spot_ref - stop_dist_u if want_call else spot_ref + stop_dist_u, 1)
    if rec is not None and signal in (Signal.BUY, Signal.HOLD):
        q = next((x for x in chain if x.symbol == rec.option_symbol), None)
        delta = abs(q.delta) if q else 0.4
        prem_stop = max(1.0, stop_dist_u * delta)
        # ``ATR × delta`` alone can place the stop INSIDE the option's own noise
        # (a ₹339 premium given a 4-point / 1.2% stop is knocked out by ordinary
        # wobble even when the direction is right). Enforce a floor of
        # ``min_stop_pct_of_premium`` and widen the targets by the SAME factor so
        # the setup's reward:risk is unchanged — the R:R gate below then rejects
        # any trade whose target cannot justify a survivable stop.
        level_scale = 1.0
        if settings.min_stop_pct_of_premium > 0 and premium and premium > 0:
            floor_stop = premium * settings.min_stop_pct_of_premium / 100.0
            if floor_stop > prem_stop:
                level_scale = floor_stop / prem_stop
                prem_stop = floor_stop
        # Entry zone: a tight band around the current premium (buy at/near the
        # live price). The engine only reaches a BUY on a pullback/retest (or a
        # confirmed momentum continuation), so this band is already a good entry.
        # The half-width is capped at 30% of the premium-stop distance so a HIGH
        # premium (e.g. a ~₹280 crude CE) can't open an ₹8-wide band that makes a
        # top-of-band fill risk far more to the stop than a mid fill — the stop
        # then stays consistent with the entry regardless of where you fill.
        band = max(0.5, min(premium * 0.015, 0.30 * prem_stop))
        entry_range = (round(premium - band, 1), round(premium + band, 1))
        sl = round(max(0.5, premium - prem_stop), 1)
        # Target scale. T1 landed a median 11% above the premium while the median
        # signal only ever travelled 2% and just 12% of signals reached 11% at
        # all, so T1 was a level the market rarely visited and the target-hit
        # statistics were meaningless. This pulls all three levels in (or pushes
        # them out) together, leaving the stop alone; the R:R gate still refuses
        # anything the shortened target can no longer justify.
        tscale = max(0.2, min(3.0, settings.decision_target_scale)) * level_scale
        t1 = round(premium + tgt_mults[0] * atr_val * delta * tscale, 1)
        t2 = round(premium + tgt_mults[1] * atr_val * delta * tscale, 1)
        t3 = round(premium + tgt_mults[2] * atr_val * delta * tscale, 1)
        # A wider stop/target needs proportionally longer to play out.
        hold_minutes = int(max(5, min(
            240, (30 / max(0.15, directional_strength)) * min(level_scale, 4.0)
        )))
        recovery_prob = round(0.5 + 0.4 * net * (1 if want_call else -1), 3)
        recovery_prob = max(0.05, min(0.95, recovery_prob))
        rr = round((t1 - premium) / max(0.5, premium - sl), 2)
        trace.reward_risk = float(rr)
        reasons = reasons[:3] + [
            f"ATR stop/targets ({market_status.value.split('_')[0].lower()} regime, "
            f"ATR≈{round(atr_val, 1)} pts, R:R≈{rr})"
        ]
        # Reward:Risk gate — refuse a FRESH BUY that risks more than it can make
        # (e.g. the 0.64 R:R chase). Downgrade to WAIT rather than take bad math.
        if (
            not in_position
            and signal == Signal.BUY
            and settings.min_reward_risk > 0
            and rr < settings.min_reward_risk
        ):
            signal = Signal.WAIT
            trace.reward_risk_ok = False
            confidence = min(confidence, gate - 1.0)
            reasons = [
                f"WAIT — reward:risk {rr} is below the {settings.min_reward_risk:g} "
                f"minimum (target too close vs the stop); not chasing this entry"
            ] + reasons[:2]

    # ---- richer dashboard fields ----
    expected_move_pts = round(atr_val * (1.2 + directional_strength), 1)
    # A conviction meter, not a probability: the arithmetic has no floor under 50
    # and no link to an observed frequency. Named for what it actually is.
    conviction_meter = round(min(97.0, 50.0 + 47.0 * directional_strength * agreement), 1)
    opportunity_score = round(min(100.0, 0.45 * signal_strength + 0.35 * confidence + 0.20 * conviction_meter), 1)

    vol_component = 1.0 if market_status in (MarketStatus.VOLATILE, MarketStatus.NEWS_MODE) else \
        0.25 if market_status in (MarketStatus.TRENDING, MarketStatus.BREAKOUT) else 0.5
    risk_score = round(min(100.0, 100.0 * (0.55 * (1.0 - agreement) + 0.45 * vol_component)
                           + (10.0 if confirm == "conflicts" else 0.0)), 1)
    if risk_score >= 60:
        risk_level = "HIGH"
    elif risk_score <= 32:
        risk_level = "LOW"
    else:
        risk_level = "MEDIUM"

    do_not_buy_above = round(entry_range[1] * 1.012, 1) if entry_range else None
    emergency_exit = round(sl * 0.94, 1) if sl else None

    # ---- Phase 1.5 decision-support meters --------------------------------
    cat_scores = _category_scores(breakdown, want_call)
    opportunity_label = _opportunity_label(opportunity_score, signal)
    risk_meter = _risk_meter(risk_score)
    traps = _trap_probabilities(snap, want_call, over_extended)
    # Premium Quality of the exact leg we'd buy (healthy vs. dangerous premium).
    if want_call:
        leg_beh = {"state": snap.premium_call_state or "CONSOLIDATION",
                   "velocity": snap.premium_ce_velocity or 0.0,
                   "acceleration": snap.premium_ce_acceleration or 0.0}
    else:
        leg_beh = {"state": snap.premium_put_state or "CONSOLIDATION",
                   "velocity": snap.premium_pe_velocity or 0.0,
                   "acceleration": snap.premium_pe_acceleration or 0.0}
    pq = prem.leg_quality(leg_beh)
    expected_recovery = hold_minutes if (recovery_prob and recovery_prob >= 0.5) else None

    # ---- SAFETY VETO: prefer WAIT over a low-quality BUY -------------------
    # The premium-quality and trap meters GATE the signal, not merely decorate
    # it. A fresh BUY is downgraded to WAIT when the exact option we'd buy has a
    # DANGEROUS premium, or when the move is a likely trap against the buyer.
    if signal == Signal.BUY and not in_position:
        trap_against_buyer = traps["buy_trap"] if want_call else traps["sell_trap"]
        # A strong headline pushing AGAINST the side we'd buy is a hard block:
        # never buy a CE into strongly bearish news (or a PE into bullish news).
        news_opposes = (want_call and news_score <= -0.5) or (
            not want_call and news_score >= 0.5
        )
        veto = None
        if news_opposes:
            side = "CE" if want_call else "PE"
            veto = (
                f"strong {'bearish' if want_call else 'bullish'} news ({news_score:+.2f}) "
                f"against a {side} — standing aside until it settles"
            )
        elif pq["health"] == "DANGEROUS":
            side = "CE" if want_call else "PE"
            veto = f"{side} premium looks dangerous (quality {pq['quality']}) — waiting for a healthier premium"
        elif trap_against_buyer >= 60.0:
            veto = f"high trap probability ({int(trap_against_buyer)}%) — standing aside"
        trace.news_not_opposing = bool(not news_opposes)
        trace.premium_quality_ok = bool(pq["health"] != "DANGEROUS")
        trace.trap_probability_ok = bool(trap_against_buyer < 60.0)
        if veto:
            signal = Signal.WAIT
            market_signal = Signal.WAIT
            opportunity_label = _opportunity_label(opportunity_score, signal)
            reasons = [veto, *reasons][:6]

    atm_strike = None
    if chain:
        atm_strike = min(chain, key=lambda q: abs(q.strike - spot_ref)).strike
    moneyness = None
    if rec is not None and atm_strike is not None:
        if rec.strike == atm_strike:
            moneyness = "ATM"
        elif rec.option_type == OptionType.CALL:
            moneyness = "ITM" if rec.strike < spot_ref else "OTM"
        else:
            moneyness = "ITM" if rec.strike > spot_ref else "OTM"

    rank_gate_trace(trace)

    decision = Decision(
        signal=signal,
        confidence=round(confidence, 1),
        signal_strength=signal_strength,
        trade_quality=quality,
        market_signal=market_signal,
        market_confidence=round(market_confidence, 1),
        reversal=reversal,
        reversal_option=reversal_option,
        reversal_option_type=reversal_option_type,
        htf_trend=htf_dir,
        htf_strength=round(htf_str * 100, 1),
        entry_trigger=entry_trigger,
        atr_points=round(atr_val, 1),
        underlying_stop=underlying_stop if signal in (Signal.BUY, Signal.HOLD) else None,
        recommended_option=rec.option_symbol if rec else None,
        strike=rec.strike if rec else None,
        option_type=rec.option_type if rec else None,
        current_premium=premium,
        spot_price=round(spot_ref, 1),
        atm_strike=atm_strike,
        moneyness=moneyness,
        entry_range=entry_range,
        stop_loss=sl,
        target1=t1, target2=t2, target3=t3,
        expected_holding_minutes=hold_minutes,
        recovery_probability=recovery_prob,
        reasons=reasons,
        buy_score=round(trade_score if signal == Signal.BUY else 0.0, 1),
        sell_score=round(p_bear / p_weight * 100, 1),
        wait_score=round(wait_score, 1),
        exit_score=round(exit_score, 1),
        risk_level=risk_level,
        risk_score=risk_score,
        trade_score=round(trade_score, 1),
        conviction_meter=conviction_meter,
        observed_target_rate=observed_target_rate(trace.reward_risk),
        expected_move_points=expected_move_pts,
        opportunity_score=opportunity_score,
        opportunity_label=opportunity_label,
        risk_meter=risk_meter,
        trade_score_breakdown=cat_scores,
        premium_quality=pq["quality"],
        premium_health=pq["health"],
        premium_momentum=pq["momentum"],
        premium_velocity=pq["velocity"],
        premium_acceleration=pq["acceleration"],
        buy_trap_prob=traps["buy_trap"],
        sell_trap_prob=traps["sell_trap"],
        fake_breakout_prob=traps["fake_breakout"],
        fake_breakdown_prob=traps["fake_breakdown"],
        smart_money=snap.smart_money,
        smart_money_label=snap.smart_money_label,
        expected_recovery_minutes=expected_recovery,
        do_not_buy_above=do_not_buy_above,
        emergency_exit=emergency_exit,
        next_review_seconds=45,
        gates=trace,
    )
    return decision, breakdown


def _select_option(chain: list[OptionQuote], spot: float, want_call: bool, atr_val: float) -> OptionRecommendation | None:
    otype = OptionType.CALL if want_call else OptionType.PUT
    candidates = [q for q in chain if q.option_type == otype]
    if not candidates:
        return None
    # Prefer slightly ITM/ATM with healthy OI and delta ~0.45-0.6 for intraday.
    def score(q: OptionQuote) -> float:
        target_delta = 0.52
        delta_fit = 1.0 - min(1.0, abs(abs(q.delta) - target_delta) / 0.4)
        liquidity = min(1.0, q.oi / 6000.0)
        return 0.6 * delta_fit + 0.4 * liquidity

    best = max(candidates, key=score)
    return OptionRecommendation(
        option_symbol=best.symbol,
        strike=best.strike,
        option_type=best.option_type,
        premium=best.premium,
        confidence=round(min(99, score(best) * 100), 1),
    )


def _top_reasons(breakdown: list[ScoreBreakdown], want_call: bool) -> list[str]:
    side = "BULLISH" if want_call else "BEARISH"
    aligned = [b for b in breakdown if b.signal == side]
    aligned.sort(key=lambda b: abs(b.contribution), reverse=True)
    return [f"{b.name}: {b.detail}" for b in aligned[:5]] or ["Mixed signals"]


def _quality(strength: float, agreement: float, status: MarketStatus) -> str:
    s = strength / 100.0
    penalty = 0.15 if status in (MarketStatus.VOLATILE, MarketStatus.NEWS_MODE) else 0.0
    score = 0.6 * s + 0.4 * agreement - penalty
    if score >= 0.8:
        return "A+"
    if score >= 0.65:
        return "A"
    if score >= 0.5:
        return "B"
    return "C"


def watchlist(chain: list[OptionQuote], spot: float, want_call: bool, atr_val: float) -> list[OptionRecommendation]:
    otype = OptionType.CALL if want_call else OptionType.PUT
    cands = [q for q in chain if q.option_type == otype]

    def score(q: OptionQuote) -> float:
        delta_fit = 1.0 - min(1.0, abs(abs(q.delta) - 0.52) / 0.4)
        liquidity = min(1.0, q.oi / 6000.0)
        return 0.6 * delta_fit + 0.4 * liquidity

    ranked = sorted(cands, key=score, reverse=True)[:3]
    return [
        OptionRecommendation(
            option_symbol=q.symbol, strike=q.strike, option_type=q.option_type,
            premium=q.premium, confidence=round(min(99, score(q) * 100), 1),
        )
        for q in ranked
    ]
