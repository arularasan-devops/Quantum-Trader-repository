"""Optional real news via public RSS feeds.

Enabled with QT_NEWS_SOURCE=rss and QT_NEWS_RSS_FEEDS set to comma-separated
feed URLs. Fetches headlines, scores sentiment from a crude-oil keyword lexicon,
and caches for a short TTL. If a feed is unreachable the caller falls back to the
provider's own news, so the dashboard never blocks on the network.

Note: this is a lightweight keyword sentiment, not a licensed NLP news feed.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.models import NewsItem, NewsSentiment

_BULLISH = (
    # crude/commodity
    "draw", "drawdown", "cut", "cuts", "shortage", "disruption", "escalate",
    "tension", "sanction", "outage", "supply risk", "stimulus", "rally", "surge",
    # equity/index
    "beat", "beats", "upgrade", "record profit", "profit jumps", "profit rises",
    "rate cut", "dovish", "inflows", "buyback", "bonus", "order win", "raises guidance",
)
_BEARISH = (
    # crude/commodity
    "build", "buildup", "glut", "oversupply", "raise output", "increase output",
    "ceasefire", "demand slump", "recession", "hawkish", "stronger dollar", "weak demand",
    # equity/index
    "miss", "misses", "downgrade", "profit falls", "loss widens", "rate hike",
    "outflows", "probe", "fraud", "ban", "cuts guidance", "resigns", "default",
)

# Sensible free defaults if QT_NEWS_SOURCE=rss but no feeds were configured.
_DEFAULT_FEEDS = (
    "https://www.moneycontrol.com/rss/latestnews.xml",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://feeds.reuters.com/reuters/businessNews",
)

_cache: tuple[float, list[NewsItem]] | None = None
_TTL = 60.0


def _score(text: str) -> tuple[NewsSentiment, float]:
    t = text.lower()
    b = sum(1 for w in _BULLISH if w in t)
    s = sum(1 for w in _BEARISH if w in t)
    if b == s:
        return NewsSentiment.NEUTRAL, 0.0
    impact = max(-0.9, min(0.9, (b - s) * 0.3))
    return (NewsSentiment.BULLISH if impact > 0 else NewsSentiment.BEARISH), round(impact, 2)


def _feeds() -> list[str]:
    configured = [u.strip() for u in settings.news_rss_feeds.split(",") if u.strip()]
    return configured or list(_DEFAULT_FEEDS)


def fetch() -> list[NewsItem]:
    global _cache
    now = time.time()
    if _cache is not None and now - _cache[0] < _TTL:
        return _cache[1]

    items: list[NewsItem] = []
    for url in _feeds():
        try:
            resp = httpx.get(url, timeout=6.0, follow_redirects=True)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception:
            continue
        source = urlparse(url).netloc.replace("www.", "")
        for node in root.iter("item"):
            title = (node.findtext("title") or "").strip()
            if not title:
                continue
            sent, impact = _score(title)
            items.append(
                NewsItem(time=int(now), source=source, headline=title,
                         category="rss", sentiment=sent, impact=impact)
            )
            if len(items) >= 25:
                break

    _cache = (now, items)
    return items
