"""Phase 3.1 — Intelligence Layer (advisory, READ-ONLY).

This module adds market-context intelligence that the *frozen* Phase 1.5 signal
engine does not compute:

  - India VIX (equity-market implied volatility)          [NSE, best-effort]
  - Market Breadth (NIFTY 50 advances / declines)         [NSE, best-effort]
  - Max Pain (option-writer pain minimum from the chain)  [pure compute]
  - PCR reading with a plain-English bias                 [pure compute]
  - Trade Quality Filter (0..100 -> A+/Excellent/Good/…)  [pure compute]

CRITICAL: nothing here feeds back into ``engine/decision.py``. It only *reads*
the decision + indicators the frozen engine already produced and layers extra
context on top for the dashboard. No orders, no engine mutation.

Network calls (VIX / breadth) reuse the same best-effort NSE pattern as
``fundamentals.py`` — every failure degrades to ``available=False`` and never
raises into the per-tick request path. Results are cached briefly.
"""
from __future__ import annotations

import time

import httpx

from app.models import (
    IndiaVix,
    MarketBreadth,
    MarketStatus,
    MaxPain,
    OptionQuote,
    OptionType,
    PcrReading,
    QualityComponent,
    TradeQuality,
)

_HOME = "https://www.nseindia.com"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

_TTL = 60.0
_indices_cache: tuple[float, IndiaVix, MarketBreadth] | None = None


def _to_float(v: object) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _to_int(v: object) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


# --------------------------------------------------------------------------- #
#  India VIX + Market Breadth (one NSE call feeds both)
# --------------------------------------------------------------------------- #
def _vix_level(last: float) -> str:
    if last < 11:
        return "LOW"
    if last < 15:
        return "MODERATE"
    if last < 20:
        return "ELEVATED"
    if last < 28:
        return "HIGH"
    return "EXTREME"


def market_context() -> tuple[IndiaVix, MarketBreadth]:
    """Fetch India VIX + NIFTY-50 breadth from NSE's public ``allIndices``.

    Best-effort and cached: on any failure both come back ``available=False``
    with an honest note (never fabricated). Equity-market wide — for MCX
    commodities this is context only, not a per-instrument metric."""
    global _indices_cache
    now = time.time()
    if _indices_cache and now - _indices_cache[0] < _TTL:
        return _indices_cache[1], _indices_cache[2]

    vix = IndiaVix(available=False, note="India VIX unavailable (rate-limited or offline).")
    breadth = MarketBreadth(available=False, note="Market breadth unavailable (rate-limited or offline).")
    try:
        c = httpx.Client(headers=_HEADERS, timeout=6.0, follow_redirects=True)
        try:
            c.get(_HOME)  # seed cookies
            resp = c.get(f"{_HOME}/api/allIndices")
        finally:
            c.close()
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            for row in data:
                name = str(row.get("index", "")).upper()
                if name == "INDIA VIX":
                    last = _to_float(row.get("last"))
                    if last is not None:
                        vix = IndiaVix(
                            available=True,
                            last=round(last, 2),
                            change_pct=_to_float(row.get("percentChange")),
                            level=_vix_level(last),
                            note=None,
                        )
                elif name == "NIFTY 50":
                    adv = _to_int(row.get("advances"))
                    dec = _to_int(row.get("declines"))
                    unch = _to_int(row.get("unchanged"))
                    if adv is not None and dec is not None:
                        total = adv + dec
                        ratio = round(adv / dec, 2) if dec else float(adv)
                        if total == 0:
                            bias = "NEUTRAL"
                        elif adv / total >= 0.62:
                            bias = "BULLISH"
                        elif adv / total <= 0.38:
                            bias = "BEARISH"
                        else:
                            bias = "NEUTRAL"
                        breadth = MarketBreadth(
                            available=True,
                            advances=adv,
                            declines=dec,
                            unchanged=unch,
                            ratio=ratio,
                            bias=bias,
                            note=None,
                        )
    except Exception:
        pass

    _indices_cache = (now, vix, breadth)
    return vix, breadth


def cached_context() -> tuple[IndiaVix, MarketBreadth]:
    """Non-blocking: return the last fetched VIX/breadth (or 'unavailable'
    placeholders if nothing has been fetched yet). Safe to call every tick —
    the network refresh happens in a background task via ``market_context()``."""
    if _indices_cache:
        return _indices_cache[1], _indices_cache[2]
    return (
        IndiaVix(available=False, note="India VIX not fetched yet."),
        MarketBreadth(available=False, note="Market breadth not fetched yet."),
    )


# --------------------------------------------------------------------------- #
#  Max Pain (pure compute from the option chain)
# --------------------------------------------------------------------------- #
def max_pain(chain: list[OptionQuote], spot: float | None) -> MaxPain:
    """Strike at which total option-writer payout to buyers is minimised — the
    price options tend to gravitate toward into expiry. Pure OI arithmetic."""
    if not chain:
        return MaxPain(available=False, note="No option chain.")
    strikes = sorted({q.strike for q in chain})
    if len(strikes) < 3:
        return MaxPain(available=False, note="Insufficient strikes for max pain.")

    calls = [q for q in chain if q.option_type == OptionType.CALL]
    puts = [q for q in chain if q.option_type == OptionType.PUT]

    best_strike = None
    best_loss = None
    for k in strikes:
        total = 0.0
        for q in calls:
            if k > q.strike:
                total += (k - q.strike) * q.oi
        for q in puts:
            if q.strike > k:
                total += (q.strike - k) * q.oi
        if best_loss is None or total < best_loss:
            best_loss = total
            best_strike = k

    if best_strike is None:
        return MaxPain(available=False, note="Max pain could not be computed (no OI).")

    dist_pct = None
    bias = None
    if spot:
        dist_pct = round((spot - best_strike) / spot * 100, 2)
        if dist_pct > 0.2:
            bias = "Spot above max pain — pin pressure downward toward it."
        elif dist_pct < -0.2:
            bias = "Spot below max pain — pin pressure upward toward it."
        else:
            bias = "Spot near max pain — magnet zone."
    return MaxPain(
        available=True,
        strike=best_strike,
        spot=spot,
        distance_pct=dist_pct,
        bias=bias,
        note=None,
    )


# --------------------------------------------------------------------------- #
#  PCR reading (pure compute)
# --------------------------------------------------------------------------- #
def pcr_reading(chain: list[OptionQuote]) -> PcrReading:
    """Put/Call OI ratio with a plain-English contrarian bias reading."""
    if not chain:
        return PcrReading(available=False, note="No option chain.")
    call_oi = sum(q.oi for q in chain if q.option_type == OptionType.CALL)
    put_oi = sum(q.oi for q in chain if q.option_type == OptionType.PUT)
    if call_oi <= 0 or put_oi <= 0:
        return PcrReading(available=False, note="No open-interest data from the feed.")
    pcr = round(put_oi / call_oi, 3)
    # High PCR (heavy put writing) is conventionally bullish; low PCR bearish.
    if pcr >= 1.3:
        bias = "BULLISH"
        note = "Heavy put writing (PCR ≥ 1.3) — support building below."
    elif pcr <= 0.7:
        bias = "BEARISH"
        note = "Heavy call writing (PCR ≤ 0.7) — resistance capping above."
    else:
        bias = "NEUTRAL"
        note = "Balanced option positioning."
    return PcrReading(available=True, pcr=pcr, bias=bias, note=note)


# --------------------------------------------------------------------------- #
#  Trade Quality Filter (0..100 -> grade). Advisory ranking only.
# --------------------------------------------------------------------------- #
_GRADE_BANDS = [
    (95.0, "A+"),
    (85.0, "EXCELLENT"),
    (75.0, "GOOD"),
    (60.0, "AVERAGE"),
    (0.0, "SKIP"),
]


def _grade(score: float) -> str:
    for cut, label in _GRADE_BANDS:
        if score >= cut:
            return label
    return "SKIP"


def _volume_score(level: str | None, spike: bool) -> float:
    if spike:
        return 90.0
    return {"Very High": 100.0, "High": 82.0, "Normal": 60.0, "Low": 30.0}.get(level or "", 55.0)


def _vix_vol_score(vix_last: float) -> float:
    # Prefer a moderate vol regime for option BUYING (not too dead, not chaotic).
    if vix_last < 10:
        return 55.0
    if vix_last < 15:
        return 100.0
    if vix_last < 20:
        return 78.0
    if vix_last < 28:
        return 48.0
    return 25.0


def _atr_vol_score(atr: float | None, spot: float | None) -> float:
    if not atr or not spot:
        return 60.0
    pct = atr / spot * 100.0
    if pct < 0.15:
        return 50.0
    if pct < 0.6:
        return 100.0
    if pct < 1.2:
        return 75.0
    return 45.0


def _regime_score(status: MarketStatus) -> float:
    return {
        MarketStatus.TRENDING: 100.0,
        MarketStatus.BREAKOUT: 90.0,
        MarketStatus.REVERSAL: 62.0,
        MarketStatus.RANGING: 50.0,
        MarketStatus.VOLATILE: 45.0,
        MarketStatus.NEWS_MODE: 40.0,
        MarketStatus.LOW_VOLUME: 30.0,
    }.get(status, 55.0)


def _time_of_day_score(now: int, is_mcx: bool) -> float:
    # IST minutes from epoch: IST = UTC + 5:30.
    ist = time.gmtime(now + 5 * 3600 + 1800)
    mins = ist.tm_hour * 60 + ist.tm_min
    if is_mcx:
        # MCX evening liquidity is best ~17:00-23:00; thin near the 23:30 close.
        if 17 * 60 <= mins < 23 * 60:
            return 100.0
        if 9 * 60 <= mins < 17 * 60:
            return 70.0
        if 23 * 60 <= mins <= 23 * 60 + 30:
            return 45.0
        return 55.0
    # Equity/index: avoid the first 15m chop and the last 15m; lunch lull is soft.
    open_m, close_m = 9 * 60 + 15, 15 * 60 + 30
    if mins < open_m or mins > close_m:
        return 40.0
    if mins < open_m + 15:
        return 55.0
    if mins > close_m - 15:
        return 55.0
    if 12 * 60 <= mins < 13 * 60:
        return 65.0
    return 100.0


def _align_score(bias: str | None, want_call: bool) -> float:
    if not bias:
        return 55.0
    b = bias.upper()
    if "BULL" in b:
        return 100.0 if want_call else 25.0
    if "BEAR" in b:
        return 25.0 if want_call else 100.0
    return 55.0


def trade_quality(
    *,
    signal: str,
    option_type: OptionType | None,
    htf_trend: str | None,
    htf_strength: float | None,
    adx: float | None,
    volume_level: str | None,
    volume_spike: bool,
    oi_bias: str | None,
    institutional: str | None,
    smart_money: str | None,
    entry_range: tuple[float, float] | None,
    stop_loss: float | None,
    target1: float | None,
    atr_points: float | None,
    spot: float | None,
    market_status: MarketStatus,
    now: int,
    is_mcx: bool,
    vix: IndiaVix,
) -> TradeQuality:
    """Composite 0..100 setup-quality score from data the frozen engine already
    produced. Advisory ranking ONLY — never changes the BUY/WAIT decision."""
    has_setup = signal in ("BUY", "HOLD") and option_type is not None
    if option_type is not None:
        want_call = option_type == OptionType.CALL
    else:
        want_call = (htf_trend or "").upper() == "UP"

    comps: list[QualityComponent] = []

    # 1. Trend quality
    trend_q = htf_strength if htf_strength is not None else (min(100.0, (adx or 0) * 3.3))
    comps.append(QualityComponent(
        name="Trend quality", score=round(trend_q, 1), weight=0.15,
        detail=f"HTF {htf_trend or 'n/a'} strength {round(trend_q)}",
    ))

    # 2. Liquidity / volume level
    comps.append(QualityComponent(
        name="Liquidity", score=_volume_score(volume_level, False), weight=0.10,
        detail=f"Volume level {volume_level or 'n/a'}",
    ))

    # 3. Volatility regime
    if not is_mcx and vix.available and vix.last is not None:
        vol_s = _vix_vol_score(vix.last)
        vol_detail = f"India VIX {vix.last} ({vix.level})"
    else:
        vol_s = _atr_vol_score(atr_points, spot)
        vol_detail = "ATR-based volatility"
    comps.append(QualityComponent(name="Volatility", score=vol_s, weight=0.10, detail=vol_detail))

    # 4. Option-chain (OI) confirmation
    comps.append(QualityComponent(
        name="Option-chain confirm", score=_align_score(oi_bias, want_call), weight=0.12,
        detail=f"OI bias {oi_bias or 'n/a'} vs {'CE' if want_call else 'PE'}",
    ))

    # 5. Volume confirmation
    comps.append(QualityComponent(
        name="Volume confirm", score=_volume_score(volume_level, volume_spike), weight=0.10,
        detail="Volume spike" if volume_spike else f"Volume {volume_level or 'n/a'}",
    ))

    # 6. Institutional confirmation
    inst_bias = institutional or smart_money
    comps.append(QualityComponent(
        name="Institutional confirm", score=_align_score(inst_bias, want_call), weight=0.10,
        detail=f"{inst_bias or 'n/a'} (estimated)",
    ))

    # 7. Risk / reward
    rr = None
    if entry_range and stop_loss is not None and target1 is not None:
        entry = (entry_range[0] + entry_range[1]) / 2.0
        risk = entry - stop_loss
        reward = target1 - entry
        if risk > 0 and reward > 0:
            rr = reward / risk
    if rr is None:
        rr_score = 55.0
        rr_detail = "R:R n/a (no active setup)"
    else:
        rr_score = max(20.0, min(100.0, 40.0 + (rr - 1.0) * 40.0))
        rr_detail = f"R:R ≈ 1:{round(rr, 2)}"
    comps.append(QualityComponent(name="Risk/reward", score=round(rr_score, 1), weight=0.15, detail=rr_detail))

    # 8. Time of day
    comps.append(QualityComponent(
        name="Time of day", score=_time_of_day_score(now, is_mcx), weight=0.08,
        detail="Session timing",
    ))

    # 9. Market regime
    comps.append(QualityComponent(
        name="Market regime", score=_regime_score(market_status), weight=0.10,
        detail=market_status.value,
    ))

    # 10. Historical similarity — Milestone 3, gated on real paper-trade data.
    comps.append(QualityComponent(
        name="Historical similarity", score=0.0, weight=0.0, detail="Insufficient historical data",
        available=False,
    ))

    total_w = sum(c.weight for c in comps if c.available and c.weight > 0)
    score = sum(c.score * c.weight for c in comps if c.available and c.weight > 0)
    score = round(score / total_w, 1) if total_w else 0.0

    if not has_setup:
        note = "No active BUY setup — score reflects current market context only."
        grade = "NO SETUP"
    else:
        note = "Fewer, higher-quality trades: prefer GOOD or better; SKIP below 60."
        grade = _grade(score)

    return TradeQuality(score=score, grade=grade, components=comps, note=note)
