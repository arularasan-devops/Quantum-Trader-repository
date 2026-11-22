"""Smoke: Pullback Quality classifier (pure, log-only). Run: cd backend && python _smoke_pullback_quality.py"""
from __future__ import annotations

from app.config import settings
from app.execution.pullback_quality import classify
from app.models import Candle

FAIL: list[str] = []


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {info}"))
    if not cond:
        FAIL.append(name)


def bar(c, v=100.0, h=None, low=None, o=None):
    return (o if o is not None else c, h if h is not None else c + 0.5,
            low if low is not None else c - 0.5, c, v)


def mk(rows):
    return [Candle(time=60 * i, open=o, high=h, low=lo, close=c, volume=v)
            for i, (o, h, lo, c, v) in enumerate(rows)]


def mirror(rows):
    return [(200 - o, 200 - lo, 200 - h, 200 - c, v) for (o, h, lo, c, v) in rows]


base = [bar(100.0) for _ in range(10)]
impulse = [bar(100.0 + i, 200) for i in range(1, 11)]


def lab(rows, call=True, **kw):
    return classify(call=call, candles=mk(rows), **kw)


check("default off", type(settings).model_fields["pullback_quality_mode"].default == "off")
check("flat -> NO_TRADE", lab(base)["label"] == "NO_TRADE")
r = lab(base + impulse + [bar(109, 80), bar(108, 80), bar(107, 80)])
check("40% low-volume pullback -> HEALTHY_PULLBACK",
      r["label"] == "HEALTHY_PULLBACK" and r["path"] == "PULLBACK_CONTINUATION", str(r))
cont = base + impulse + [bar(109, 80), bar(108, 80), bar(107, 80),
                         bar(108.8, 150, h=109.0, low=107.2, o=107.3)]
r = lab(cont)
check("break of prior high -> CONTINUATION_CONFIRMED", r["label"] == "CONTINUATION_CONFIRMED", str(r))
check("PE mirror -> same label", lab(mirror(cont), call=False)["label"] == "CONTINUATION_CONFIRMED")
r = lab(cont, option_closes=[100, 120, 140, 104])
check("weak option downgrades to WAIT", r["label"] == "WAIT_FOR_CONFIRMATION", str(r))
r = lab(base + impulse + [bar(c, 80) for c in (107, 104, 101, 99)])
check("whole impulse retraced -> PULLBACK_FAILURE", r["label"] == "PULLBACK_FAILURE", str(r))
r = lab(base + impulse + [bar(c, 80) for c in (108, 105, 103)])
check("~73% retrace -> TREND_REVERSAL_RISK", r["label"] == "TREND_REVERSAL_RISK", str(r))
r = lab(base + impulse + [bar(109, 300), bar(108.2, 300)])
check("shallow high-volume pullback -> ABSORPTION_POSSIBLE", r["label"] == "ABSORPTION_POSSIBLE", str(r))
fresh = base + [bar(100.0 + i) for i in range(1, 5)] + [bar(105.4, 250, h=105.5, low=104.0, o=104.0)]
r = lab(fresh)
check("strong high-volume new high -> FRESH_MOMENTUM confirmed",
      r["label"] == "CONTINUATION_CONFIRMED" and r["path"] == "FRESH_MOMENTUM", str(r))
r = lab(base + [bar(100.0 + i) for i in range(1, 5)] + [bar(105.4, 90, h=105.5, low=104.0, o=104.0)])
check("new high on low volume -> WAIT", r["label"] == "WAIT_FOR_CONFIRMATION", str(r))
r = lab(base + [bar(100.0 + i) for i in range(1, 11)] + [bar(111.4, 250, h=111.5, low=110, o=110)])
check("over-extended new high -> EXHAUSTED_MOVE", r["label"] == "EXHAUSTED_MOVE", str(r))
r = lab(cont, ask=100.0, stop=90.0, target=105.0)
check("reward/risk 0.5 -> NO_TRADE", r["label"] == "NO_TRADE" and "room" in r["reasons"][0], str(r))

print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
raise SystemExit(1 if FAIL else 0)
