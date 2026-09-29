"""News, geopolitical and US-inventory sentiment aggregation.

Turns a stream of headlines (from any news provider — the simulated feed today,
a real Reuters/Moneycontrol/EIA scraper or paid API later) into a single
signed sentiment score that the decision engine folds into its weighting.

Categories tracked:
  - macro        (demand / PMI / China / USD index)
  - geopolitical (Middle East, Iran, Russia, Saudi, US)
  - inventory    (EIA / API crude stocks)
  - opec         (OPEC+ supply decisions)
  - fed          (rates / dollar)
"""
from __future__ import annotations

import time

from app.models import NewsItem, NewsSentiment

# Category weights — geopolitical & inventory shocks move crude the most.
_CATEGORY_WEIGHT = {
    "geopolitical": 1.3,
    "inventory": 1.2,
    "opec": 1.15,
    "fed": 0.9,
    "macro": 0.8,
}

# Recent news matters more; decay older headlines.
_HALF_LIFE_SECONDS = 45 * 60


def aggregate(news: list[NewsItem], now: int | None = None) -> tuple[NewsSentiment, float]:
    """Return (overall sentiment, score in -1..1)."""
    if not news:
        return NewsSentiment.NEUTRAL, 0.0
    now = now or int(time.time())
    num = 0.0
    den = 0.0
    for item in news:
        age = max(0, now - item.time)
        decay = 0.5 ** (age / _HALF_LIFE_SECONDS)
        w = _CATEGORY_WEIGHT.get(item.category, 1.0) * decay
        num += item.impact * w
        den += w
    score = 0.0 if den == 0 else max(-1.0, min(1.0, num / den))
    if score > 0.15:
        return NewsSentiment.BULLISH, round(score, 3)
    if score < -0.15:
        return NewsSentiment.BEARISH, round(score, 3)
    return NewsSentiment.NEUTRAL, round(score, 3)


# Keywords that make a crude headline *materially* relevant to today's MCX session.
_MATERIAL_KEYWORDS = (
    "eia", "api", "inventory", "inventories", "stockpile", "opec", "opec+",
    "production", "output", "supply", "cut", "quota", "hormuz", "tanker",
    "iran", "israel", "russia", "ukraine", "saudi", "pipeline", "sanction",
    "fed", "rate", "dollar", "usd index", "brent", "wti", "demand", "china",
)


def session_impact(news: list[NewsItem], now: int | None = None) -> dict:
    """Level 6 — decide whether the news likely affects *today's* MCX crude
    session and quantify a bullish/bearish split + a confidence multiplier.

    Only recent (< 3h), crude-relevant headlines count toward materiality.
    Returns {material, bull_pct, bear_pct, factor, headline}. ``factor`` is a
    multiplier (0.6..1.15) the engine applies to confidence: strong aligned
    news lifts it slightly, a fresh high-impact shock with no price agreement
    dampens it (uncertainty).
    """
    now = now or int(time.time())
    bull = 0.0
    bear = 0.0
    material = False
    top: NewsItem | None = None
    for item in news:
        age = now - item.time
        if age > 3 * 3600 or age < 0:
            continue
        text = item.headline.lower()
        relevant = item.category in ("inventory", "opec", "geopolitical", "fed") or any(
            k in text for k in _MATERIAL_KEYWORDS
        )
        if not relevant:
            continue
        decay = 0.5 ** (age / _HALF_LIFE_SECONDS)
        w = _CATEGORY_WEIGHT.get(item.category, 1.0) * decay * abs(item.impact)
        if abs(item.impact) >= 0.4:
            material = True
            if top is None or abs(item.impact) > abs(top.impact):
                top = item
        if item.impact > 0:
            bull += w
        elif item.impact < 0:
            bear += w
    total = bull + bear
    if total <= 1e-9:
        return {"material": False, "bull_pct": 50.0, "bear_pct": 50.0,
                "factor": 1.0, "headline": None}
    bull_pct = round(bull / total * 100, 0)
    bear_pct = round(100 - bull_pct, 0)
    dominance = abs(bull - bear) / total  # 0..1 how one-sided
    if material:
        # a decisive, one-sided material read lifts confidence; a muddled one
        # (both sides firing) injects uncertainty and dampens it.
        factor = 1.0 + 0.15 * dominance if dominance >= 0.5 else 0.75
    else:
        factor = 1.0
    return {"material": material, "bull_pct": bull_pct, "bear_pct": bear_pct,
            "factor": round(factor, 3), "headline": top.headline if top else None}


def has_high_impact(news: list[NewsItem], now: int | None = None, window: int = 120) -> NewsItem | None:
    """Detect a very recent, high-impact headline (drives NEWS_MODE / alerts)."""
    now = now or int(time.time())
    for item in news:
        if now - item.time <= window and abs(item.impact) >= 0.55:
            return item
    return None
