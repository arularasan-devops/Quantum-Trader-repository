"""Smoke: the paper auto-entry eligibility layer (brief §9 / §15).

Builds BUY decisions directly (the frozen engine produces no BUY for long
stretches) and drives ``_auto_trade`` on a simulated engine. Real-feed cases
patch only the feed-health/quote inputs. Nothing here can reach a broker:
``provider.place_order`` is replaced with a tripwire.
"""
import os
import tempfile
import time

os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp())

from app.analysis import exec_funnel  # noqa: E402
from app.config import settings  # noqa: E402
from app.execution import live_control  # noqa: E402
from app.market.instruments import get_spec  # noqa: E402
from app.models import Decision, OptionType, Signal  # noqa: E402
from app.state import AppState  # noqa: E402

FAIL: list[str] = []
BROKER_CALLS: list[tuple] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAIL.append(name)


def fresh(regime: str = "TRENDING"):
    eng = AppState(instrument="CRUDEOIL")
    settings.auto_trade_enabled = False  # the warm-up tick must not trade by itself
    eng.tick()
    settings.auto_trade_enabled = True
    assert not eng.position.option_symbol, "fixture engine already in a position"
    eng.provider.place_order = lambda *a, **k: BROKER_CALLS.append(a)  # tripwire
    eng._last_market_status = regime
    ok = [o for o in eng.provider.option_chain()
          if o.premium >= settings.auto_trade_min_premium * 1.5]
    pick = min(ok, key=lambda o: o.premium)
    return eng, pick.symbol, pick.premium


def buy(symbol: str, premium: float, conf: float = 85.0, trig: str = "PULLBACK") -> Decision:
    return Decision(
        signal=Signal.BUY, confidence=conf, signal_strength=conf, trade_quality="A",
        recommended_option=symbol, option_type=OptionType.CALL, current_premium=premium,
        spot_price=7900.0, stop_loss=premium * 0.92, target1=premium * 1.11,
        target2=premium * 1.2, target3=premium * 1.3, entry_trigger=trig,
        premium_momentum=1.0, buy_trap_prob=0.0, premium_health="HEALTHY", reasons=["smoke"],
    )


def blocker(eng, sym: str) -> str:
    b = exec_funnel.last_block(eng.instrument, since=time.time() - 5, option=sym)
    return (b or {}).get("primary_blocker") or ""


def attempt(eng, d: Decision) -> tuple[bool, str]:
    eng._auto_trade(d, False, int(time.time()))
    return bool(eng.position.option_symbol), blocker(eng, d.recommended_option)


class _Health:
    def __init__(self, src: str):
        self.source = src
        self.live = src == "LIVE"
        self.underlying_age_sec = 0.5
        self.option_age_sec = 0.5
        self.stale_after_sec = 10


def as_real_feed(eng, with_book: bool):
    real_chain = eng.provider.option_chain
    eng._feed_health = lambda: _Health("LIVE")
    eng._feed_ready = lambda: True

    def chain():
        out = []
        for q in real_chain():
            upd = {"bid": round(q.premium - 0.5, 2), "ask": round(q.premium + 0.5, 2)} \
                if with_book else {"bid": None, "ask": None}
            out.append(q.model_copy(update=upd))
        return out
    eng.provider.option_chain = chain


settings.trade_mode = "paper"
settings.auto_trade_enabled = True
settings.auto_trade_min_confidence = 80.0
settings.auto_trade_strategy_gate = True
settings.opportunity_gate_enabled = False
settings.auto_trade_max_concurrent = 1000
settings.blocked_entry_hours = {}
settings.instrument_allowlist = []
settings.auto_trade_no_entry_open_minutes = 0
settings.capital = 500000.0
print(f"allowed triggers={settings.auto_trade_allowed_triggers} regimes="
      f"{settings.auto_trade_allowed_regimes} floor={settings.auto_trade_min_confidence}")

# 1-3. confidence floor
for conf, want in ((80.0, True), (85.0, True), (79.9, False)):
    eng, sym, prem = fresh()
    took, why = attempt(eng, buy(sym, prem, conf))
    check(f"TRENDING+PULLBACK at {conf}% -> {'entry' if want else 'rejected'}",
          took == want, why or eng._auto_last_action or "")
    if not want:
        check("  rejected by CONFIDENCE_FLOOR", why == "CONFIDENCE_FLOOR", why)

# 4. other regimes
for regime in ("RANGING", "REVERSAL_MODE", "NEWS_MODE", "BREAKOUT_MODE", "LOW_VOLUME", ""):
    eng, sym, prem = fresh(regime)
    took, why = attempt(eng, buy(sym, prem, 95.0))
    check(f"PULLBACK in {regime or 'unknown'} regime rejected",
          not took and why == "STRATEGY_REGIME", why)

# 5. other setups
for trig in ("MOMENTUM", "BREAKOUT_RETEST", "REVERSAL", "WAIT_PULLBACK"):
    eng, sym, prem = fresh()
    took, why = attempt(eng, buy(sym, prem, 95.0, trig))
    check(f"{trig} in TRENDING rejected", not took and why == "STRATEGY_TRIGGER", why)

# 6. stale / disconnected feed
eng, sym, prem = fresh()
eng._feed_ready = lambda: False
took, why = attempt(eng, buy(sym, prem))
check("stale/disconnected feed rejected", not took and why == "FEED_NOT_READY", why)

# 7. real feed without a two-sided book
eng, sym, prem = fresh()
as_real_feed(eng, with_book=False)
took, why = attempt(eng, buy(sym, prem))
check("real feed, no bid/ask -> rejected", not took and why == "NO_TWO_SIDED_QUOTE", why)

# 8. real feed with a book -> entry fills at the ask, not LTP
eng, sym, prem = fresh()
as_real_feed(eng, with_book=True)
took, why = attempt(eng, buy(sym, prem))
ask = next(q.ask for q in eng.provider.option_chain() if q.symbol == sym)
check("real feed with bid/ask -> entry", took, why or eng._auto_last_action or "")
check("  paper entry filled at the ask (not LTP)",
      took and abs(eng.position.entry_premium - ask) < 1e-6,
      f"entry={eng.position.entry_premium} ask={ask} ltp={prem}")

# 9. failed risk gate
eng, sym, prem = fresh()
eng.risk.can_open = lambda t: (False, "daily loss cap reached")
took, why = attempt(eng, buy(sym, prem))
check("failed risk gate rejected", not took and why == "RISK_GOVERNOR", why)

# 10. sizing: whole lots, within capital
eng, sym, prem = fresh()
lot = get_spec("CRUDEOIL").lot_size
took, why = attempt(eng, buy(sym, prem))
lots = eng.position.quantity_lots
check("dynamic size is whole lots >= 1", took and isinstance(lots, int) and lots >= 1,
      f"lots={lots} lot_size={lot}")
check("  order value within ₹5,00,000 capital",
      took and eng.position.entry_premium * lot * lots <= settings.capital,
      f"value=₹{eng.position.entry_premium * lot * lots:,.0f}")

# 11. capital caps the lots, then refuses below one lot
settings.capital = prem * lot * 1.5
eng, sym, prem = fresh()
settings.auto_trade_max_lots = 10
took, why = attempt(eng, buy(sym, prem))
check("capital for 1.5 lots -> rounded down to 1 lot",
      took and eng.position.quantity_lots == 1, f"lots={eng.position.quantity_lots}")
settings.capital = prem * lot * 0.5
eng, sym, prem = fresh()
took, why = attempt(eng, buy(sym, prem))
check("capital below one lot -> rejected", not took and why == "CAPITAL", why)
settings.capital = 500000.0
settings.auto_trade_max_lots = 1

# 12. duplicate entry prevention
eng, sym, prem = fresh()
d = buy(sym, prem)
attempt(eng, d)
first = (eng.position.option_symbol, eng.position.quantity_lots)
eng._auto_trade(d, True, int(time.time()))
check("second call while in position does not add", (eng.position.option_symbol,
      eng.position.quantity_lots) == first, str(first))
eng.sell()
took, why = attempt(eng, d)
check("same signal episode not re-entered after exit",
      not took and why == "EPISODE_ALREADY_TRADED", why)

# 13. order -> stop exit lifecycle and journal row
from app.engine import account_risk  # noqa: E402
account_risk.account.reset()  # #12 closed a CRUDEOIL trade; clear its re-entry cooldown
eng, sym, prem = fresh()
took, why = attempt(eng, buy(sym, prem))
check("lifecycle fixture entered", took and eng._auto_stop is not None,
      why or (eng._auto_last_action or ""))
stop = eng._auto_stop or prem * 0.92
eng.position.current_premium = round(stop - 1.0, 2)
eng._auto_trade(buy(sym, prem), True, int(time.time()))
check("stop hit -> position closed", took and not eng.position.option_symbol,
      eng._auto_last_action or "")
from app import storage  # noqa: E402
row = storage.store._db.execute(
    "SELECT mode, auto, exit_reason, entry, exit, net_pnl FROM trade_journal "
    "ORDER BY id DESC LIMIT 1").fetchone()
check("journal row: paper, auto, stop reason, loss",
      row is not None and row[0] == "paper" and row[1] == 1
      and "STOP" in (row[2] or "").upper() and row[5] < 0, str(row))

# 14. paper isolation
check("no broker place_order call in any case", not BROKER_CALLS, str(BROKER_CALLS))
eng, sym, prem = fresh()
check("engine is not live in paper mode", eng.is_live is False)
settings.trade_mode = "LIVE-ish"
err = live_control.verify_trade_mode()
check("unknown trade mode disables auto-trade", bool(err) and not settings.auto_trade_enabled)
settings.trade_mode = "paper"
live_control.verify_trade_mode()

print(f"\n{'All checks passed!' if not FAIL else f'{len(FAIL)} FAILED: {FAIL}'}")
raise SystemExit(1 if FAIL else 0)
