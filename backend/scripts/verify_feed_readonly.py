"""READ-ONLY live-feed verification. Run by the operator, never by an AI agent.

Builds the configured live provider (QT_DATA_PROVIDER=angelone|groww) with the
operator's own credentials from the environment / .env, polls it for a short
window and checks provider selection, quote freshness, timestamps and the
broker instrument mapping (symbol, exchange, expiry, lot size).

It CANNOT place orders: ``place_order`` is replaced with a function that raises
before the provider is used, and the source-level REAL_MONEY_EXECUTION_ENABLED
guard stays False. Credentials are never printed.

Usage (from backend/):
    python scripts/verify_feed_readonly.py [INSTRUMENT] [--seconds 20]

Exit codes: 0 PASS, 1 FAIL, 2 BLOCKED (no credentials / market closed / no data).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai import safety  # noqa: E402
from app.config import settings  # noqa: E402
from app.market.instruments import DEFAULT_INSTRUMENT, get_spec  # noqa: E402
from app.market.provider import build_provider  # noqa: E402
from app.state import classify_feed  # noqa: E402
from app.models import FeedHealth  # noqa: E402

LIVE_PROVIDERS = ("angelone", "groww")


def _refuse_order(*_a, **_k):
    raise RuntimeError("verify_feed_readonly is read-only: order placement refused")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("instrument", nargs="?", default=DEFAULT_INSTRUMENT)
    ap.add_argument("--seconds", type=float, default=20.0)
    args = ap.parse_args()

    assert safety.REAL_MONEY_EXECUTION_ENABLED is False, "real-money guard must stay off"
    provider_name = (settings.data_provider or "").lower()
    print(f"configured provider : {provider_name or '(unset)'}")
    if provider_name not in LIVE_PROVIDERS:
        print(f"FAIL: QT_DATA_PROVIDER must be one of {LIVE_PROVIDERS} for a live-feed check")
        return 1

    spec = get_spec(args.instrument)
    try:
        provider = build_provider(provider_name, args.instrument)
    except Exception as exc:  # credentials/session/instrument master
        print(f"BLOCKED: provider could not start — {type(exc).__name__}: {str(exc)[:200]}")
        return 2
    provider.place_order = _refuse_order  # type: ignore[method-assign]

    deadline = time.time() + max(3.0, args.seconds)
    health = FeedHealth()
    while time.time() < deadline:
        try:
            provider.step()
        except Exception as exc:
            print(f"step error: {type(exc).__name__}: {str(exc)[:160]}")
        health = classify_feed(FeedHealth(**provider.feed_health()), provider)
        if health.state == "CONNECTED":
            break
        time.sleep(1.0)

    ltp = provider.futures_price()
    contract = provider.futures_contract() if hasattr(provider, "futures_contract") else None
    chain = provider.option_chain()
    print(f"instrument          : {args.instrument} {spec.exchange}:{spec.symbol} (config lot_size={spec.lot_size})")
    print(f"source / state      : {health.source} / {health.state} (mode={health.mode})")
    print(f"underlying LTP      : {ltp}")
    print(f"underlying age (s)  : {health.underlying_age_sec}")
    print(f"option age (s)      : {health.option_age_sec}")
    print(f"local check time    : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(health.checked_at or 0))}")
    print(f"broker contract     : {contract}")
    print(f"option quotes       : {len(chain)}")
    print(f"note                : {health.note}")

    problems: list[str] = []
    if health.source != "LIVE":
        problems.append(f"source is {health.source}, expected LIVE")
    if not ltp or ltp <= 0:
        problems.append("no underlying LTP")
    if contract:
        broker_lot = contract.get("lot_size")
        if broker_lot and int(broker_lot) != int(spec.lot_size):
            problems.append(f"lot size mismatch: broker={broker_lot} config={spec.lot_size}")
        if not contract.get("expiry"):
            problems.append("broker contract has no expiry")
    if health.state == "DISCONNECTED":
        print("BLOCKED: feed is DISCONNECTED (market closed, no session, or outage)")
        for p in problems:
            print(f"  - {p}")
        return 2
    if problems:
        for p in problems:
            print(f"FAIL: {p}")
        return 1
    print("PASS: live feed selected, fresh, and mapped (read-only; no orders placed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
