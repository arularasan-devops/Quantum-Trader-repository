"""Fast market-opportunity scanner (Phase 4, RESEARCH / OBSERVABILITY ONLY).

Answers one question cheaply, for a broad universe, before the expensive
strategy engine runs:

    "which instruments are moving strongly RIGHT NOW, on fresh data, with
     participation and liquidity, and plausibly still have room?"

Hard boundaries (enforced by ``_smoke_scanner.py``):

* it NEVER emits BUY/WAIT/AVOID — its vocabulary is OPPORTUNITY / WATCH /
  NO_OPPORTUNITY plus a state (FRESH_MOMENTUM / ACTIVE / EXTENDED / EXHAUSTED /
  STALLED / NO_DATA);
* it never mutates a decision, a gate, a stop, a target or an order;
* it uses ONLY data available at the current bar — no future highs, volume or
  premium;
* stale or dead data can never be ranked as a live opportunity;
* missing inputs stay UNKNOWN. Nothing is fabricated, in particular OI, which
  Phase 3 established is simulator-generated in the stored history.

Everything here is a pure function of its inputs, so identical input gives
identical output (deterministic ranking) and it is trivially testable offline.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.market.tick_quality import (
    NO_DATA as FEED_NO_DATA,
)
from app.market.tick_quality import (
    is_tradable_freshness,
)
from app.models import Candle

# ---------------------------------------------------------------- vocabulary
OPPORTUNITY = "OPPORTUNITY"
WATCH = "WATCH"
NO_OPPORTUNITY = "NO_OPPORTUNITY"

FRESH_MOMENTUM = "FRESH_MOMENTUM"
ACTIVE = "ACTIVE"
EXTENDED = "EXTENDED"
EXHAUSTED = "EXHAUSTED"
STALLED = "STALLED"
NO_DATA = "NO_DATA"

UNKNOWN = "UNKNOWN"
LOW = "LOW"
NORMAL = "NORMAL"
ELEVATED = "ELEVATED"
HIGH = "HIGH"
EXTREME = "EXTREME"
MEDIUM = "MEDIUM"

THIN = "THIN"
MODERATE = "MODERATE"
LIQUID = "LIQUID"

# Research default weights. These are a STARTING POINT chosen by reasoning about
# what the feature means, not by fitting — Part N explicitly forbids treating
# them as validated. They are overridable per call so the backtest can sweep
# them without touching this module.
DEFAULT_WEIGHTS: dict[str, float] = {
    "movement": 0.20,
    "acceleration": 0.15,
    "volatility": 0.15,
    "participation": 0.15,
    "momentum": 0.15,
    "liquidity": 0.10,
    "freshness": 0.05,
    "extension_penalty": 0.05,
}

# Minimum bars needed before a feature group is trustworthy.
_MIN_BARS = 20
_ATR_LEN = 14
_BASE_LOOKBACK = 60      # "normal" baseline for ATR / volume comparison
_SWING_LOOKBACK = 30     # recent swing window for extension
_PERSIST_LOOKBACK = 5    # bars used for momentum persistence


# ------------------------------------------------------------------ helpers
def _pct(a: float, b: float) -> float | None:
    """Percentage change from ``b`` to ``a``."""
    if b is None or a is None or b == 0:
        return None
    return (a - b) / abs(b) * 100.0


def _atr(candles: list[Candle], length: int = _ATR_LEN) -> float | None:
    if len(candles) < length + 1:
        return None
    trs: list[float] = []
    for prev, cur in zip(candles[-(length + 1):-1], candles[-length:]):
        trs.append(max(
            cur.high - cur.low,
            abs(cur.high - prev.close),
            abs(cur.low - prev.close),
        ))
    return sum(trs) / len(trs) if trs else None


def _ema(values: list[float], length: int) -> float | None:
    if len(values) < length:
        return None
    k = 2.0 / (length + 1.0)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1.0 - k)
    return e


def _session_start_index(candles: list[Candle]) -> int:
    """Index of the first candle of the latest IST calendar day."""
    if not candles:
        return 0
    ist = 19800
    day = int((candles[-1].time + ist) // 86400)
    for i in range(len(candles) - 1, -1, -1):
        if int((candles[i].time + ist) // 86400) != day:
            return i + 1
    return 0


def _vwap(candles: list[Candle]) -> float | None:
    vol = sum(c.volume for c in candles)
    if not candles:
        return None
    if vol <= 0:
        # No volume (index feeds often report none) — a typical-price mean is a
        # documented fallback, not a real VWAP, so callers treat it as weaker.
        return sum((c.high + c.low + c.close) / 3.0 for c in candles) / len(candles)
    return sum(((c.high + c.low + c.close) / 3.0) * c.volume for c in candles) / vol


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _scale(value: float | None, full: float) -> float | None:
    """Map ``0..full`` onto ``0..100`` (absolute magnitude), clamped."""
    if value is None or full <= 0:
        return None
    return _clamp(abs(value) / full * 100.0)


# -------------------------------------------------------------------- inputs
@dataclass(frozen=True)
class ScanInput:
    """Everything the scanner is allowed to look at, all of it entry-time."""

    instrument: str
    display: str = ""
    exchange: str = ""
    ltp: float | None = None
    candles: tuple[Candle, ...] = ()
    # feed health (from app.market.tick_quality)
    data_age_ms: float | None = None
    freshness: str = FEED_NO_DATA
    data_quality_score: float | None = None
    ticks_per_second: float | None = None
    market_open: bool = True
    # option-side inputs; UNKNOWN unless genuinely measured
    option_liquidity: str = UNKNOWN
    option_spread_pct: float | None = None
    atm_premium: float | None = None
    oi_known: bool = False
    relative_oi: float | None = None
    # typical daily range in price units, if known (used for ROOM only)
    typical_day_range: float | None = None


@dataclass(frozen=True)
class ScanResult:
    instrument: str
    display: str
    exchange: str
    ltp: float | None
    direction: str                      # UP | DOWN | FLAT | UNKNOWN
    # movement
    move_1m_pct: float | None = None
    move_3m_pct: float | None = None
    move_5m_pct: float | None = None
    move_15m_pct: float | None = None
    session_change_pct: float | None = None
    # acceleration
    roc: float | None = None
    roc_delta: float | None = None
    momentum_persistence: float | None = None
    # volatility
    atr: float | None = None
    atr_expansion: float | None = None
    range_expansion: float | None = None
    realized_vol_pct: float | None = None
    volatility_state: str = UNKNOWN
    # momentum / structure
    vwap: float | None = None
    vwap_distance_atr: float | None = None
    ema_slope_atr: float | None = None
    structure: str = UNKNOWN            # HIGHER_HIGHS | LOWER_LOWS | MIXED
    breakout_strength_atr: float | None = None
    momentum_state: str = UNKNOWN       # FRESH | ACTIVE | WEAKENING | EXHAUSTED
    # participation
    relative_volume: float | None = None
    volume_acceleration: float | None = None
    volume_state: str = UNKNOWN
    oi_state: str = UNKNOWN
    # liquidity
    liquidity_state: str = UNKNOWN
    # extension / room
    extension_pct_of_swing: float | None = None
    extension_atr: float | None = None
    room: str = UNKNOWN
    # scores (0-100; None when not computable)
    movement_score: float | None = None
    acceleration_score: float | None = None
    volatility_score: float | None = None
    participation_score: float | None = None
    momentum_score: float | None = None
    liquidity_score: float | None = None
    freshness_score: float | None = None
    extension_penalty: float | None = None
    opportunity_score: float | None = None
    data_quality_score: float | None = None
    confidence_in_score: float | None = None
    data_age_ms: float | None = None
    feed_state: str = FEED_NO_DATA
    classification: str = NO_DATA
    verdict: str = NO_OPPORTUNITY
    bars_available: int = 0
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["reasons"] = list(self.reasons)
        return d


# ------------------------------------------------------------------ features
def _movement(inp: ScanInput, closes: list[float]) -> dict:
    ltp = inp.ltp if inp.ltp else (closes[-1] if closes else None)
    out: dict = {
        "move_1m_pct": None, "move_3m_pct": None,
        "move_5m_pct": None, "move_15m_pct": None,
        "session_change_pct": None,
    }
    if ltp is None or not closes:
        return out
    for label, back in (("move_1m_pct", 1), ("move_3m_pct", 3),
                        ("move_5m_pct", 5), ("move_15m_pct", 15)):
        if len(closes) > back:
            out[label] = _pct(ltp, closes[-1 - back])
    idx = _session_start_index(list(inp.candles))
    if idx < len(inp.candles):
        out["session_change_pct"] = _pct(ltp, inp.candles[idx].open)
    return out


def _acceleration(closes: list[float], atr: float | None) -> dict:
    out: dict = {"roc": None, "roc_delta": None, "momentum_persistence": None}
    if len(closes) < 8:
        return out
    # ROC over the last 3 bars vs the 3 before them, in ATR units so it is
    # comparable across instruments of very different price scale.
    unit = atr if atr and atr > 0 else None
    recent = closes[-1] - closes[-4]
    prior = closes[-4] - closes[-7]
    out["roc"] = round(recent / unit, 3) if unit else None
    out["roc_delta"] = round((recent - prior) / unit, 3) if unit else None
    steps = [closes[i] - closes[i - 1] for i in range(len(closes) - _PERSIST_LOOKBACK, len(closes))]
    if steps:
        up = sum(1 for s in steps if s > 0)
        down = sum(1 for s in steps if s < 0)
        out["momentum_persistence"] = round(max(up, down) / len(steps), 3)
    return out


def _volatility(candles: list[Candle], atr: float | None) -> dict:
    out: dict = {
        "atr_expansion": None, "range_expansion": None,
        "realized_vol_pct": None, "volatility_state": UNKNOWN,
    }
    if atr is None or len(candles) < _BASE_LOOKBACK:
        return out
    base = _atr(candles[:-_ATR_LEN] if len(candles) > _BASE_LOOKBACK else candles, _ATR_LEN)
    if base and base > 0:
        out["atr_expansion"] = round(atr / base, 3)
    recent_rng = max(c.high for c in candles[-5:]) - min(c.low for c in candles[-5:])
    base_rng = max(c.high for c in candles[-_BASE_LOOKBACK:]) - min(c.low for c in candles[-_BASE_LOOKBACK:])
    if base_rng > 0:
        out["range_expansion"] = round(recent_rng / base_rng, 3)
    closes = [c.close for c in candles[-_BASE_LOOKBACK:]]
    rets = [
        (b - a) / a for a, b in zip(closes, closes[1:]) if a
    ]
    if rets:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        out["realized_vol_pct"] = round((var ** 0.5) * 100.0, 4)
    exp = out["atr_expansion"]
    if exp is not None:
        if exp < 0.7:
            out["volatility_state"] = LOW
        elif exp < 1.15:
            out["volatility_state"] = NORMAL
        elif exp < 1.6:
            out["volatility_state"] = ELEVATED
        elif exp < 2.5:
            out["volatility_state"] = HIGH
        else:
            out["volatility_state"] = EXTREME
    return out


def _momentum(candles: list[Candle], closes: list[float], atr: float | None,
              direction: str) -> dict:
    out: dict = {
        "vwap": None, "vwap_distance_atr": None, "ema_slope_atr": None,
        "structure": UNKNOWN, "breakout_strength_atr": None,
    }
    if not candles:
        return out
    idx = _session_start_index(candles)
    vw = _vwap(candles[idx:] or candles)
    out["vwap"] = round(vw, 2) if vw else None
    if vw and atr and atr > 0:
        out["vwap_distance_atr"] = round((closes[-1] - vw) / atr, 3)
    e9, e21 = _ema(closes[-30:], 9), _ema(closes[-30:], 21)
    if e9 is not None and e21 is not None and atr and atr > 0:
        out["ema_slope_atr"] = round((e9 - e21) / atr, 3)
    win = candles[-_SWING_LOOKBACK:]
    if len(win) >= 6:
        highs = [c.high for c in win]
        lows = [c.low for c in win]
        half = len(win) // 2
        hh = max(highs[half:]) > max(highs[:half])
        ll = min(lows[half:]) < min(lows[:half])
        out["structure"] = (
            "HIGHER_HIGHS" if hh and not ll else
            "LOWER_LOWS" if ll and not hh else "MIXED"
        )
        if atr and atr > 0:
            prior_high = max(highs[:-1])
            prior_low = min(lows[:-1])
            out["breakout_strength_atr"] = round(
                ((closes[-1] - prior_high) if direction != "DOWN" else (prior_low - closes[-1])) / atr,
                3,
            )
    return out


def _participation(candles: list[Candle], inp: ScanInput) -> dict:
    out: dict = {
        "relative_volume": None, "volume_acceleration": None,
        "volume_state": UNKNOWN, "oi_state": UNKNOWN,
    }
    vols = [c.volume for c in candles]
    if vols and any(v > 0 for v in vols) and len(vols) >= _BASE_LOOKBACK:
        recent = sum(vols[-5:]) / 5.0
        base_slice = vols[-_BASE_LOOKBACK:-5] or vols[-_BASE_LOOKBACK:]
        base = sum(base_slice) / max(1, len(base_slice))
        if base > 0:
            rel = recent / base
            out["relative_volume"] = round(rel, 2)
            out["volume_state"] = (
                LOW if rel < 0.7 else NORMAL if rel < 1.5 else HIGH
            )
        prev = sum(vols[-10:-5]) / 5.0 if len(vols) >= 10 else None
        if prev and prev > 0:
            out["volume_acceleration"] = round(recent / prev, 2)
    # OI: only ever real. Phase 3 proved the stored chain OI is simulator
    # output, and the live FULL quote does not expose an OI delta, so this
    # stays UNKNOWN rather than being invented or defaulted to zero.
    if inp.oi_known and inp.relative_oi is not None:
        out["oi_state"] = (
            LOW if inp.relative_oi < 0.7 else NORMAL if inp.relative_oi < 1.5 else HIGH
        )
    return out


def _liquidity(inp: ScanInput) -> tuple[str, float | None]:
    """Liquidity state + score. UNKNOWN when we genuinely cannot tell."""
    known: list[float] = []
    state = inp.option_liquidity if inp.option_liquidity in (THIN, MODERATE, LIQUID) else UNKNOWN
    if state == LIQUID:
        known.append(100.0)
    elif state == MODERATE:
        known.append(60.0)
    elif state == THIN:
        known.append(15.0)
    if inp.option_spread_pct is not None:
        # 0% spread -> 100, 5%+ -> 0. Options wider than that are not tradable
        # at the sizes this tool contemplates.
        known.append(_clamp(100.0 - (inp.option_spread_pct / 5.0) * 100.0))
    if inp.ticks_per_second is not None:
        known.append(_clamp(inp.ticks_per_second / 2.0 * 100.0))
    if not known:
        return UNKNOWN, None
    score = sum(known) / len(known)
    if state == UNKNOWN:
        state = THIN if score < 30 else MODERATE if score < 70 else LIQUID
    return state, round(score, 1)


def _extension(candles: list[Candle], closes: list[float], atr: float | None,
               direction: str) -> dict:
    out: dict = {"extension_pct_of_swing": None, "extension_atr": None}
    win = candles[-_SWING_LOOKBACK:]
    if len(win) < 6:
        return out
    hi = max(c.high for c in win)
    lo = min(c.low for c in win)
    rng = hi - lo
    px = closes[-1]
    if rng > 0:
        frac = (px - lo) / rng if direction != "DOWN" else (hi - px) / rng
        out["extension_pct_of_swing"] = round(_clamp(frac * 100.0), 1)
    if atr and atr > 0:
        travelled = (px - lo) if direction != "DOWN" else (hi - px)
        out["extension_atr"] = round(travelled / atr, 2)
    return out


def _room(inp: ScanInput, candles: list[Candle], atr: float | None,
          extension_atr: float | None, vol_state: str) -> str:
    """Coarse remaining-room bucket from entry-time data only.

    Deliberately coarse and NOT presented as a point estimate: Phase 4 forbids
    claiming exact future points without validation, and nothing here has been
    validated as predictive of the remaining move.
    """
    if atr is None or extension_atr is None:
        return UNKNOWN
    day = inp.typical_day_range
    if day is None and len(candles) >= _BASE_LOOKBACK:
        idx = _session_start_index(candles)
        sess = candles[idx:] or candles
        day = max(c.high for c in sess) - min(c.low for c in sess)
    if not day or day <= 0:
        return UNKNOWN
    used = (extension_atr * atr) / day
    if used < 0.35 and vol_state in (ELEVATED, HIGH, NORMAL, EXTREME):
        return HIGH
    if used < 0.7:
        return MEDIUM
    return LOW


# --------------------------------------------------------------------- score
def _score(inp: ScanInput, f: dict, weights: dict[str, float]) -> dict:
    """Composite 0-100 score from the sub-scores. Unknown groups are dropped and
    the remaining weights renormalised, so a missing feature lowers CONFIDENCE
    rather than silently scoring zero."""
    atr = f.get("atr")
    px = inp.ltp or (inp.candles[-1].close if inp.candles else None)
    atr_pct = (atr / px * 100.0) if (atr and px) else None

    # movement: the 5m move measured in ATR-equivalents, so a 0.3% move on a
    # quiet name is not ranked next to 0.3% on a violent one.
    mv = None
    if f.get("move_5m_pct") is not None and atr_pct:
        mv = _scale(f["move_5m_pct"] / max(atr_pct, 1e-9), 3.0)
    acc = None
    if f.get("roc_delta") is not None:
        acc = _scale(f["roc_delta"], 1.5)
        if f.get("momentum_persistence") is not None:
            acc = (acc + f["momentum_persistence"] * 100.0) / 2.0
    vol = None
    if f.get("atr_expansion") is not None:
        exp = f["atr_expansion"]
        # Peak around 1.6x: expansion is good, EXTREME is not automatically
        # better (Part H is explicit that high vol is not itself a signal).
        vol = _clamp(100.0 - abs(exp - 1.6) / 1.2 * 100.0) if exp else None
    part = None
    if f.get("relative_volume") is not None:
        part = _scale(f["relative_volume"], 3.0)
        if f.get("volume_acceleration") is not None:
            part = (part + _scale(f["volume_acceleration"], 2.5)) / 2.0
    mom = None
    mom_parts: list[float] = []
    if f.get("ema_slope_atr") is not None:
        mom_parts.append(_scale(f["ema_slope_atr"], 1.0) or 0.0)
    if f.get("vwap_distance_atr") is not None:
        mom_parts.append(_scale(f["vwap_distance_atr"], 2.0) or 0.0)
    if f.get("structure") in ("HIGHER_HIGHS", "LOWER_LOWS"):
        mom_parts.append(80.0)
    elif f.get("structure") == "MIXED":
        mom_parts.append(35.0)
    if mom_parts:
        mom = sum(mom_parts) / len(mom_parts)
    liq = f.get("liquidity_score")
    fresh = None
    if inp.data_age_ms is not None:
        fresh = _clamp(100.0 - (inp.data_age_ms / 4000.0) * 100.0)
    ext_pen = None
    if f.get("extension_pct_of_swing") is not None:
        ext_pen = round(f["extension_pct_of_swing"], 1)

    groups = {
        "movement": mv, "acceleration": acc, "volatility": vol,
        "participation": part, "momentum": mom, "liquidity": liq,
        "freshness": fresh,
    }
    usable = {k: v for k, v in groups.items() if v is not None}
    total_w = sum(weights.get(k, 0.0) for k in usable)
    score = None
    if usable and total_w > 0:
        score = sum(weights.get(k, 0.0) * v for k, v in usable.items()) / total_w
        if ext_pen is not None:
            score -= weights.get("extension_penalty", 0.0) * ext_pen
        score = round(_clamp(score), 1)
    # Confidence: how much of the model actually had data, degraded by feed
    # quality. This is what stops a two-feature score reading like a full one.
    have = len(usable) / 7.0
    dq = (inp.data_quality_score if inp.data_quality_score is not None else 50.0) / 100.0
    bars = min(1.0, len(inp.candles) / float(_BASE_LOOKBACK))
    unknown_penalty = 1.0 - (0.1 if not inp.oi_known else 0.0)
    conf = round(_clamp(have * dq * bars * unknown_penalty * 100.0), 1)
    return {
        "movement_score": None if mv is None else round(mv, 1),
        "acceleration_score": None if acc is None else round(acc, 1),
        "volatility_score": None if vol is None else round(vol, 1),
        "participation_score": None if part is None else round(part, 1),
        "momentum_score": None if mom is None else round(mom, 1),
        "liquidity_score": None if liq is None else round(liq, 1),
        "freshness_score": None if fresh is None else round(fresh, 1),
        "extension_penalty": ext_pen,
        "opportunity_score": score,
        "confidence_in_score": conf,
    }


def _classify(f: dict, scores: dict) -> tuple[str, str, str]:
    """Return (momentum_state, classification, verdict).

    Opportunity QUALITY only. It is explicitly NOT a reversal prediction and
    NOT a trade signal — the engine decides that, unchanged.
    """
    acc = f.get("roc_delta")
    persist = f.get("momentum_persistence")
    ext = f.get("extension_pct_of_swing")
    mv5 = f.get("move_5m_pct")
    atr_exp = f.get("atr_expansion")
    moving = bool(mv5 is not None and atr_exp is not None and abs(mv5) > 0.0 and (
        f.get("extension_atr") or 0.0) >= 0.5)

    if not moving:
        mom_state = "WEAKENING" if persist and persist < 0.6 else UNKNOWN
        return mom_state, STALLED, NO_OPPORTUNITY

    accelerating = acc is not None and acc > 0.15
    decaying = acc is not None and acc < -0.15
    if accelerating and (ext is None or ext < 55.0):
        mom_state, cls = "FRESH", FRESH_MOMENTUM
    elif decaying and ext is not None and ext >= 70.0:
        mom_state, cls = "EXHAUSTED", EXHAUSTED
    elif ext is not None and ext >= 70.0:
        mom_state, cls = ("ACTIVE" if not decaying else "WEAKENING"), EXTENDED
    else:
        mom_state, cls = "ACTIVE", ACTIVE

    score = scores.get("opportunity_score")
    if score is None:
        verdict = NO_OPPORTUNITY
    elif cls in (FRESH_MOMENTUM, ACTIVE) and score >= 60.0:
        verdict = OPPORTUNITY
    elif score >= 40.0:
        verdict = WATCH
    else:
        verdict = NO_OPPORTUNITY
    return mom_state, cls, verdict


def scan_one(inp: ScanInput, weights: dict[str, float] | None = None) -> ScanResult:
    """Score ONE instrument. Pure, cheap, deterministic, no future data."""
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)
    candles = list(inp.candles)
    closes = [c.close for c in candles]
    base = ScanResult(
        instrument=inp.instrument,
        display=inp.display or inp.instrument,
        exchange=inp.exchange,
        ltp=inp.ltp,
        direction=UNKNOWN,
        data_age_ms=inp.data_age_ms,
        feed_state=inp.freshness,
        data_quality_score=inp.data_quality_score,
        bars_available=len(candles),
    )

    # --- safety filters first. A stale/dead feed or a shut market can never be
    # ranked as a live opportunity, whatever the (old) prices say.
    if not is_tradable_freshness(inp.freshness):
        return replace(base, classification=NO_DATA, verdict=NO_OPPORTUNITY,
                       reasons=(f"feed {inp.freshness} — not ranked",))
    if not inp.market_open:
        return replace(base, classification=NO_DATA, verdict=NO_OPPORTUNITY,
                       reasons=("market closed for this exchange",))
    if len(candles) < _MIN_BARS:
        return replace(base, classification=NO_DATA, verdict=NO_OPPORTUNITY,
                       reasons=(f"only {len(candles)} bars — need {_MIN_BARS}",))

    atr = _atr(candles)
    mv = _movement(inp, closes)
    direction = UNKNOWN
    ref = mv.get("move_5m_pct")
    if ref is not None:
        direction = "UP" if ref > 0 else "DOWN" if ref < 0 else "FLAT"

    f: dict = {"atr": atr}
    f.update(mv)
    f.update(_acceleration(closes, atr))
    f.update(_volatility(candles, atr))
    f.update(_momentum(candles, closes, atr, direction))
    f.update(_participation(candles, inp))
    liq_state, liq_score = _liquidity(inp)
    f["liquidity_score"] = liq_score
    f.update(_extension(candles, closes, atr, direction))

    scores = _score(inp, f, w)
    mom_state, cls, verdict = _classify(f, scores)
    room = _room(inp, candles, atr, f.get("extension_atr"), f.get("volatility_state", UNKNOWN))

    reasons: list[str] = []
    if f.get("atr_expansion") is not None:
        reasons.append(f"ATR {f['atr_expansion']:.2f}x baseline ({f.get('volatility_state')})")
    if f.get("relative_volume") is not None:
        reasons.append(f"volume {f['relative_volume']:.2f}x baseline")
    else:
        reasons.append("volume UNKNOWN (feed reports none)")
    if not inp.oi_known:
        reasons.append("OI UNKNOWN — never assumed")
    if f.get("extension_pct_of_swing") is not None:
        reasons.append(f"{f['extension_pct_of_swing']:.0f}% through the recent swing")

    return replace(
        base,
        direction=direction,
        atr=None if atr is None else round(atr, 4),
        move_1m_pct=_r(f.get("move_1m_pct")),
        move_3m_pct=_r(f.get("move_3m_pct")),
        move_5m_pct=_r(f.get("move_5m_pct")),
        move_15m_pct=_r(f.get("move_15m_pct")),
        session_change_pct=_r(f.get("session_change_pct")),
        roc=f.get("roc"),
        roc_delta=f.get("roc_delta"),
        momentum_persistence=f.get("momentum_persistence"),
        atr_expansion=f.get("atr_expansion"),
        range_expansion=f.get("range_expansion"),
        realized_vol_pct=f.get("realized_vol_pct"),
        volatility_state=f.get("volatility_state", UNKNOWN),
        vwap=f.get("vwap"),
        vwap_distance_atr=f.get("vwap_distance_atr"),
        ema_slope_atr=f.get("ema_slope_atr"),
        structure=f.get("structure", UNKNOWN),
        breakout_strength_atr=f.get("breakout_strength_atr"),
        momentum_state=mom_state,
        relative_volume=f.get("relative_volume"),
        volume_acceleration=f.get("volume_acceleration"),
        volume_state=f.get("volume_state", UNKNOWN),
        oi_state=f.get("oi_state", UNKNOWN),
        liquidity_state=liq_state,
        extension_pct_of_swing=f.get("extension_pct_of_swing"),
        extension_atr=f.get("extension_atr"),
        room=room,
        classification=cls,
        verdict=verdict,
        reasons=tuple(reasons),
        **scores,
    )


def _r(v: float | None, nd: int = 4) -> float | None:
    return None if v is None else round(v, nd)


def rank(results: list[ScanResult], top_n: int | None = None) -> list[ScanResult]:
    """Deterministic ranking: score desc, then instrument name asc.

    Unscored rows (stale feed, too few bars, shut market) sort last regardless of
    any movement they may show — old movement must never outrank live data.
    """
    ordered = sorted(
        results,
        key=lambda r: (
            0 if r.opportunity_score is not None else 1,
            -(r.opportunity_score or 0.0),
            r.instrument,
        ),
    )
    return ordered if top_n is None else ordered[:top_n]


VIEWS = {
    "all": None,
    "movers": None,
    "high_volatility": (HIGH, EXTREME),
    "fresh_momentum": (FRESH_MOMENTUM,),
    "active": (ACTIVE,),
    "extended": (EXTENDED,),
    "exhausted": (EXHAUSTED,),
    "stalled": (STALLED,),
}


def filter_view(results: list[ScanResult], view: str) -> list[ScanResult]:
    view = (view or "all").lower()
    if view in ("all", ""):
        return list(results)
    if view == "movers":
        return sorted(
            [r for r in results if r.move_5m_pct is not None],
            key=lambda r: -abs(r.move_5m_pct or 0.0),
        )
    if view == "high_volatility":
        return [r for r in results if r.volatility_state in (HIGH, EXTREME)]
    wanted = VIEWS.get(view)
    if not wanted:
        return list(results)
    return [r for r in results if r.classification in wanted]
