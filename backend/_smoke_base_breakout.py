"""Smoke: Base Breakout shadow detector + log-only hook (never changes decisions/orders)."""
from __future__ import annotations

from app.execution import base_breakout as bb
from app.models import Candle

T0 = 1_800_000_000


def _bars(n, px=100.0, rng=1.0, vol=100.0):
    return [Candle(time=T0 + i * 60, open=px, high=px + rng / 2, low=px - rng / 2,
                   close=px, volume=vol) for i in range(n)]


def _with_last(c_close, vol, quiet=0.2, n=81):
    b = _bars(n - 21, rng=1.0) + [
        Candle(time=T0 + (n - 21 + i) * 60, open=100, high=100 + quiet, low=100 - quiet,
               close=100, volume=100) for i in range(20)]
    b.append(Candle(time=T0 + (n - 1) * 60, open=100, high=max(100, c_close) + .1,
                    low=min(100, c_close) - .1, close=c_close, volume=vol))
    return b


def main():
    now = T0 + 81 * 60 + 1
    up = bb.detect(_with_last(101.0, 200), now)
    assert up and up["side"] == "CE" and up["base_mid"] == 100.0 and up["vol_ratio"] == 2.0, up
    dn = bb.detect(_with_last(99.0, 200), now)
    assert dn and dn["side"] == "PE", dn
    assert bb.detect(_with_last(101.0, 120), now) is None, "low volume must not fire"
    assert bb.detect(_with_last(100.1, 200), now) is None, "inside base must not fire"
    assert bb.detect(_with_last(101.0, 200), now - 30) is None, "forming candle ignored"
    wide = _with_last(101.0, 200, quiet=2.5)
    assert bb.detect(wide, now) is None, "wide base must not fire"
    novol = [c.model_copy(update={"volume": 0}) for c in _with_last(101.0, 0)]
    r = bb.detect(novol, now)
    assert r and r["vol_ratio"] is None, "no-volume feed skips the volume check"
    assert bb.detect(_with_last(101.0, 200)[-60:], now) is None, "short history"
    gap = _with_last(101.0, 200)
    del gap[30]
    assert bb.detect(gap, now) is None, "missing minute must not fire"
    assert bb.detect(_with_last(101.0, 200), now + 120) is None, "stale candle ignored"

    import json
    import os
    import tempfile

    from app.config import settings
    from app.models import OptionQuote, OptionType
    from app.state import AppState
    st = AppState.__new__(AppState)
    st._bb_last_ct, st._bb_day, st._instrument = None, ("", 0), "CRUDEOIL"
    d = tempfile.mkdtemp()
    old, old_feed = settings.data_dir, settings.data_provider
    settings.data_dir = d
    settings.data_provider = "simulated"
    chain = [OptionQuote(symbol=f"X{k}{t.value}", strike=k, option_type=t, premium=50.0,
                         iv=.3, delta=.5, gamma=.01, theta=-1, vega=1, oi=1, oi_change=0,
                         volume=1, bid=49.5, ask=50.5)
             for k in (99.0, 101.0) for t in (OptionType.CALL, OptionType.PUT)]
    try:
        bars = _with_last(101.0, 200)
        st._log_base_breakout(bars, chain, now)
        assert not os.path.exists(os.path.join(d, "base_breakout_shadow.jsonl")), "sim feed not logged"
        settings.data_provider = "angelone"
        st._log_base_breakout(bars, chain, now)
        st._log_base_breakout(bars, chain, now + 5)  # same candle: no duplicate
        rows = [json.loads(x) for x in open(os.path.join(d, "base_breakout_shadow.jsonl"))]
        assert len(rows) == 1 and rows[0]["option"] == "X101.0CE" and rows[0]["ask"] == 50.5, rows
        assert rows[0]["mode"] == "shadow" and rows[0]["n_today"] == 1 and rows[0]["feed"] == "angelone"
        st._log_base_breakout(bars[:10], chain, now)  # no signal: no crash, no row
    finally:
        settings.data_dir, settings.data_provider = old, old_feed
    assert settings.base_breakout_shadow_enabled is False
    print("base_breakout smoke OK")


if __name__ == "__main__":
    main()
