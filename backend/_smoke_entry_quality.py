"""Smoke: late-entry check (pure) and Angel day P&L reads (mocked, no broker).
Run: cd backend && python _smoke_entry_quality.py"""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace as NS

os.environ["QT_DATA_DIR"] = tempfile.mkdtemp(prefix="qt_eq_")
os.environ.pop("QT_DB_URL", None)

from app.config import settings  # noqa: E402
from app.execution import late_entry  # noqa: E402
from app.market import angelone  # noqa: E402

FAIL: list[str] = []


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" — {info}"))
    if not cond:
        FAIL.append(name)


NOW = 1_800_000_000.0


def B(closes):
    """1-min bars, the last one being the minute still forming at NOW."""
    return [(int(NOW) - 30 - 60 * (len(closes) - 1 - i), c) for i, c in enumerate(closes)]


check("late_entry default off", type(settings).model_fields["late_entry_mode"].default == "off")
flat = [100.0] * 12
r = late_entry.assess(call=True, ask=101.0, bid=100.0, signal_premium=100.5, stop=86.0,
                      target=131.0, option_bars=B(flat), underlying_bars=B([24000.0] * 12), now=NOW)
check("calm entry OK", r["verdict"] == "OK" and r["prem_chg_10m_pct"] == 1.0, str(r))
check("reward/risk recorded", r["reward_risk"] == 2.0 and r["chase_pts"] == 0.5)
rise = [100.0] + [100.0 + i for i in range(1, 12)]
r = late_entry.assess(call=True, ask=112.0, bid=111.0, signal_premium=110.0, stop=97.0,
                      target=142.0, option_bars=B(rise), underlying_bars=B([24000.0] * 12), now=NOW)
check("premium +10% in 10m is late", r["late"] and r["reasons"][0].startswith("EXHAUSTED_PREMIUM"), str(r))
r = late_entry.assess(call=True, ask=101.0, bid=100.0, signal_premium=101.0, stop=91.0,
                      target=105.0, option_bars=B(flat), underlying_bars=B([24000.0] * 12), now=NOW)
check("low reward/risk is late", r["late"] and "LOW_REWARD_RISK" in r["reasons"][0], str(r))
r = late_entry.assess(call=False, ask=101.0, bid=100.0, signal_premium=101.0, stop=86.0,
                      target=131.0, option_bars=B(flat),
                      underlying_bars=B([24100.0, 24090, 24080, 24050, 24020, 24000]), now=NOW)
check("PE underlying move measured from the high", r["und_move_pts"] == 100.0
      and r["und_move_age_min"] == 5, str(r))
r = late_entry.assess(call=True, ask=101.0, bid=None, signal_premium=None, stop=None,
                      target=None, option_bars=[], underlying_bars=[], now=NOW)
check("no stop/T1 is late (fail closed)", r["late"] and "NO_REWARD_RISK" in r["reasons"][0])

stale = [(t - 180, c) for t, c in B(rise)]  # same history, 3 minutes old (cached)
r = late_entry.assess(call=True, ask=112.0, bid=111.0, signal_premium=110.0, stop=97.0,
                      target=142.0, option_bars=stale, underlying_bars=[], now=NOW)
check("10m window measured by time, not bar count", r["prem_chg_10m_pct"] == round((112 - 103) / 103 * 100, 2), str(r))
settings.late_entry_max_prem_rise_pct = {"10": 10.0, "2": 1.5}
r = late_entry.assess(call=True, ask=101.0, bid=100.0, signal_premium=100.5, stop=86.0,
                      target=131.0, option_bars=B([98.0] * 12), underlying_bars=[], now=NOW)
check("custom 2m window enforced", "prem_chg_2m_pct" in r and any(" 2m " in x for x in r["reasons"]), str(r))
settings.late_entry_max_prem_rise_pct = {"10": 10.0}


def shard(client, rows, status=True):
    def pos():
        return {"status": status, "data": rows if status else None, "message": "x"}
    return NS(smart=NS(_qt_client=client, rmsLimit=lambda: {"data": {"net": 1}}, position=pos))


row = [{"unrealised": "1500", "realised": "6000"}]
angelone._get_shards = lambda: [shard("C1", row)]
check("day P&L = realised + open", angelone.broker_day_pnl(ttl=0) == 7500.0)
angelone._get_shards = lambda: [shard("C1", row), shard("C1", row)]
check("same client on two keys counted once", angelone.broker_day_pnl(ttl=0) == 7500.0)
angelone._get_shards = lambda: [shard("C1", row), shard("C2", row)]
check("two clients summed", angelone.broker_day_pnl(ttl=0) == 15000.0)
angelone._get_shards = lambda: [shard("C1", row), shard("C2", row, status=False)]
check("status=False position read -> None", angelone.broker_day_pnl(ttl=0) is None)
angelone._get_shards = lambda: [shard("C1", [])]
check("no positions today -> 0", angelone.broker_day_pnl(ttl=0) == 0.0)
angelone._get_shards = lambda: []
check("no session -> None", angelone.broker_day_pnl(ttl=0) is None)

print("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
raise SystemExit(1 if FAIL else 0)
