"""Smoke: feed provenance is explicit and fails closed (Phase C).

A simulated feed is labelled SIMULATED; a real provider that cannot serve data
is UNAVAILABLE/DISCONNECTED and never "ready"; only a fresh live feed is LIVE.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="qt_feed_prov_"))

from app.market.instruments import DEFAULT_INSTRUMENT  # noqa: E402
from app.market.provider import build_provider  # noqa: E402
from app.models import FeedHealth  # noqa: E402
from app.state import AppState, _UnavailableProvider, classify_feed  # noqa: E402

passed = failed = 0
fails: list[str] = []


def ok(cond: bool, msg: str) -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS: {msg}")
    else:
        failed += 1
        fails.append(msg)
        print(f"FAIL: {msg}")


def ready(h: FeedHealth) -> bool:
    return AppState._feed_ready(SimpleNamespace(_feed_health=lambda: h))


sim = build_provider("simulated", DEFAULT_INSTRUMENT)
h = classify_feed(FeedHealth(**sim.feed_health()), sim)
ok(h.source == "SIMULATED" and h.state == "SIMULATED", "simulator is labelled SIMULATED")
ok(h.checked_at is not None, "reading carries a local timestamp")

dead = _UnavailableProvider("NIFTY unavailable — login failed")
h = classify_feed(FeedHealth(**dead.feed_health()), dead)
ok(h.source == "UNAVAILABLE" and h.state == "DISCONNECTED",
   "a provider that failed to build is UNAVAILABLE / DISCONNECTED")
ok(h.mode != "simulated" and not h.live, "an unavailable feed never reads as simulated or live")
ok(not ready(h), "an unavailable feed is never ready to trade on")

real = object()  # any non-simulator provider
h = classify_feed(FeedHealth(mode="simulated"), real)
ok(h.source == "UNAVAILABLE", "a real provider reporting 'simulated' is UNAVAILABLE, not synthetic")
ok(not ready(h), "...and is not ready")

h = classify_feed(FeedHealth(mode="push", live=True, underlying_age_sec=0.5,
                             option_age_sec=1.0, stale_after_sec=8.0), real)
ok(h.source == "LIVE" and h.state == "CONNECTED", "fresh push feed is LIVE / CONNECTED")
ok(ready(h), "fresh live feed is ready")

h = classify_feed(FeedHealth(mode="rest", live=False, underlying_age_sec=3.0,
                             option_age_sec=4.0, stale_after_sec=8.0), real)
ok(h.state == "DEGRADED" and ready(h), "REST-polled fresh feed is DEGRADED but usable")

h = classify_feed(FeedHealth(mode="rest", live=False, underlying_age_sec=20.0,
                             stale_after_sec=8.0), real)
ok(h.state == "DEGRADED" and not ready(h), "stale quotes are not ready")

h = classify_feed(FeedHealth(mode="rest", live=False, stale_after_sec=8.0), real)
ok(h.state == "DISCONNECTED" and not ready(h), "a live feed with no quote age is DISCONNECTED")

script = (Path(__file__).parent / "scripts/verify_feed_readonly.py").read_text()
ok("provider.place_order = _refuse_order" in script,
   "read-only verifier disables place_order before using the provider")
ok(script.count("place_order(") == 0, "read-only verifier never calls place_order")

print(f"\nFEED PROVENANCE SMOKE — {passed} passed, {failed} failed")
for f in fails:
    print(f"  - {f}")
sys.exit(1 if failed else 0)
