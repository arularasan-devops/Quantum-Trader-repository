"""Smoke checks for the RESEARCH futures signal vehicle (Parts 22-24, 31, 34-36)
and the acceptance scenarios of Part 38 that concern it.

Two properties matter most here and are checked directly rather than argued:
  * a futures card has no order path at all — paper or live — so no market read,
    however strong, can reach a broker through this vehicle;
  * a refusal names its measured reason instead of returning an empty card, which
    is what made the 24-Aug option defect invisible on screen.
"""
from __future__ import annotations

import json
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="futsig_")
os.environ["QT_DATA_DIR"] = _TMP

from app.config import settings  # noqa: E402

settings.data_dir = _TMP
settings.signal_journal_log = True
settings.signal_lifecycle_log = True

from app.analysis import futures_signal as fs  # noqa: E402
from app.analysis import journal_stats  # noqa: E402
from app.analysis import signal_journal as sj  # noqa: E402
from app.analysis import signal_reconciliation as recon  # noqa: E402
from app.analysis import signal_visibility as vis  # noqa: E402
from app.models import (  # noqa: E402
    Candle,
    Decision,
    IndicatorSnapshot,
    MarketStatus,
    OptionQuote,
    OptionType,
    Signal,
)

FAILS: list[str] = []
RAN: list[str] = []
NOW = 1_756_000_000.0


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")
    RAN.append(name)
    if not cond:
        FAILS.append(name)


def candles(n: int, start: float, step: float, atr: float = 40.0,
            volume: int = 1000) -> list[Candle]:
    out: list[Candle] = []
    px = start
    for i in range(n):
        px += step
        out.append(Candle(time=int(NOW) - (n - i) * 60, open=px - step,
                          high=px + atr / 2, low=px - atr / 2, close=px,
                          volume=volume))
    return out


def ind(atr: float = 40.0, adx: float = 28.0, trend: str = "UP",
        support: float | None = None,
        resistance: float | None = None) -> IndicatorSnapshot:
    return IndicatorSnapshot(atr=atr, adx=adx, trend=trend, momentum=0.4,
                             support=support, resistance=resistance)


CONTRACT = {"symbol": "NIFTY28AUG25FUT", "expiry": "2025-08-28",
            "lot_size": 75, "exchange": "NFO"}


def reset() -> None:
    vis.reset_for_tests()
    sj.reset_for_tests()


def rows(name: str) -> list[dict]:
    path = os.path.join(_TMP, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


# --- 1. a valid futures plan is written in POINTS, not premium ---------------
reset()
settings.futures_capital = 1_000_000.0
cs = candles(140, 24000.0, 8.0)
card = fs.evaluate("NIFTY", cs, cs[-1].close, ind(), MarketStatus.TRENDING,
                   contract=CONTRACT, feed_age_sec=2.0, minutes_to_close=200.0,
                   now=NOW)
check("an uptrend produces a LONG futures plan",
      card.status == fs.VALID and card.direction == fs.LONG,
      f"{card.status} / {card.direction} / {card.invalid_reason or card.reason[:60]}")
check("the plan names the contract it would trade, not just the instrument",
      card.contract == CONTRACT["symbol"] and card.expiry == CONTRACT["expiry"])
check("risk is stated in points and converted at the lot size",
      card.risk_points is not None and card.risk_rupees_per_lot ==
      round(card.risk_points * 75, 2),
      f"{card.risk_points} pts = Rs{card.risk_rupees_per_lot}/lot")
check("the stop is below the entry and T1 above it for a long",
      card.stop < card.entry < card.target1,
      f"{card.stop} < {card.entry} < {card.target1}")
check("the entry is a zone, not a single claimed fill price",
      card.entry_zone_low < card.entry < card.entry_zone_high)
check("R:R at T1 is reported from the plan's own distances",
      card.reward_risk_t1 == round(abs(card.target1 - card.entry) / card.risk_points, 2))
check("the modelled round trip is reported against the stop",
      card.cost_points is not None and card.cost_to_risk_pct is not None,
      f"{card.cost_points} pts = {card.cost_to_risk_pct}% of the stop")
check("every check that was run is on the card, passed or failed",
      len(card.checks) >= 7 and all(c.detail for c in card.checks),
      f"{len(card.checks)} checks")
check("the futures book is not recorded, so the spread is UNAVAILABLE not zero",
      card.spread_points is None and card.spread_pct is None
      and "UNAVAILABLE" in card.liquidity_note)
check("volume is what can honestly be stated about liquidity",
      card.volume == 20 * 1000, str(card.volume))
check("the card is research only and not executable",
      card.research_only is True and card.executable is False)
check("the futures score is not presented as the option confidence",
      card.signal_score is not None and 0.0 <= card.signal_score <= 100.0,
      str(card.signal_score))

# --- 2. a downtrend is a SHORT, traded directly, not through a decaying put --
reset()
down = candles(140, 24000.0, -8.0)
short = fs.evaluate("NIFTY", down, down[-1].close, ind(trend="DOWN"),
                    MarketStatus.TRENDING, contract=CONTRACT, feed_age_sec=2.0,
                    minutes_to_close=200.0, now=NOW)
check("a downtrend is a SHORT rather than a long put",
      short.direction == fs.SHORT and short.signal == "SELL",
      f"{short.status} / {short.direction}")
check("a short's stop is above the entry and T1 below it",
      short.status != fs.VALID or short.stop > short.entry > short.target1,
      f"{short.stop} > {short.entry} > {short.target1}")

# --- 3. refusals: each names its own measured reason ------------------------
reset()
thin = fs.evaluate("NIFTY", candles(20, 24000.0, 8.0), 24160.0, ind(),
                   MarketStatus.TRENDING, contract=CONTRACT, feed_age_sec=2.0,
                   minutes_to_close=200.0, now=NOW)
check("too little history is refused as INSUFFICIENT_DATA, not guessed",
      thin.status == fs.INVALID and thin.invalid_reason == fs.NO_DATA,
      thin.reason)

stale = fs.evaluate("NIFTY", cs, cs[-1].close, ind(), MarketStatus.TRENDING,
                    contract=CONTRACT, feed_age_sec=600.0,
                    minutes_to_close=200.0, now=NOW)
check("a stalled feed writes no plan at all",
      stale.status == fs.INVALID and stale.invalid_reason == fs.STALE_FEED,
      stale.reason)
check("the age that refused it is on the card",
      stale.data_freshness_sec == 600.0)

ranging = fs.evaluate("NIFTY", cs, cs[-1].close, ind(), MarketStatus.RANGING,
                      contract=CONTRACT, feed_age_sec=2.0,
                      minutes_to_close=200.0, now=NOW)
check("a ranging market yields NO_SIGNAL with the reason stated",
      ranging.status == fs.INVALID and ranging.invalid_reason == fs.NO_SETUP
      and ranging.signal == "NO_SIGNAL", ranging.reason)

nameless = fs.evaluate("NIFTY", cs, cs[-1].close, ind(), MarketStatus.TRENDING,
                       contract=None, feed_age_sec=2.0, minutes_to_close=200.0,
                       now=NOW)
check("a plan that cannot name its contract is refused",
      nameless.status == fs.INVALID
      and nameless.invalid_reason == fs.NO_CONTRACT, nameless.reason)

expiring = fs.evaluate("NIFTY", cs, cs[-1].close, ind(), MarketStatus.TRENDING,
                       contract={**CONTRACT, "expiry": "2025-08-24"},
                       feed_age_sec=2.0, minutes_to_close=200.0, now=NOW)
check("a contract at its expiry is refused, because liquidity has rolled",
      expiring.status == fs.INVALID
      and expiring.invalid_reason == fs.EXPIRY_TOO_CLOSE,
      f"dte={expiring.days_to_expiry}")

closing = fs.evaluate("NIFTY", cs, cs[-1].close, ind(), MarketStatus.TRENDING,
                      contract=CONTRACT, feed_age_sec=2.0,
                      minutes_to_close=1.0, now=NOW)
check("a plan minutes before the intraday square-off is refused",
      closing.status == fs.INVALID
      and closing.invalid_reason == "TOO_CLOSE_TO_SQUARE_OFF", closing.reason)

illiquid = fs.evaluate("NIFTY", candles(140, 24000.0, 8.0, volume=0),
                       24000.0 + 8.0 * 140, ind(), MarketStatus.TRENDING,
                       contract=CONTRACT, feed_age_sec=2.0,
                       minutes_to_close=200.0, now=NOW)
check("no traded volume is refused rather than assumed liquid",
      illiquid.status == fs.INVALID
      and illiquid.invalid_reason == "LIQUIDITY_UNKNOWN", illiquid.reason)

_max_cost = settings.futures_max_cost_to_risk_pct
settings.futures_max_cost_to_risk_pct = 0.01
costly = fs.evaluate("NIFTY", cs, cs[-1].close, ind(), MarketStatus.TRENDING,
                     contract=CONTRACT, feed_age_sec=2.0,
                     minutes_to_close=200.0, now=NOW)
settings.futures_max_cost_to_risk_pct = _max_cost
check("a round trip that eats the stop is refused with the number shown",
      costly.status == fs.INVALID
      and costly.invalid_reason == fs.COST_TOO_HIGH, costly.reason)
check("a refusal keeps its measured levels visible instead of blanking the card",
      costly.entry is not None and costly.stop is not None
      and costly.target1 is not None)

# --- 4. lifecycle identity for the futures vehicle (Parts 25, 36) ------------
reset()
card = fs.evaluate("NIFTY", cs, cs[-1].close, ind(), MarketStatus.TRENDING,
                   contract=CONTRACT, feed_age_sec=2.0, minutes_to_close=200.0,
                   now=NOW)
vis.observe_futures("NIFTY", card, NOW)
check("a futures plan is given a global signal id",
      bool(card.global_signal_id), card.global_signal_id or "")
check("and an episode id",
      bool(card.episode_id), card.episode_id or "")
check("and its own futures signal id",
      bool(card.futures_signal_id), card.futures_signal_id or "")
check("the market and vehicle are FUTURES, not OPTIONS",
      card.market == "FUTURES" and card.vehicle == "FUTURES")

life = rows("signal_lifecycle.jsonl")
stages = [r["stage"] for r in life if r["global_signal_id"] == card.global_signal_id]
check("the plan is recorded as generated, planned, validated and published",
      {"GENERATED", "PLAN_CREATED", "PLAN_VALIDATED",
       "DASHBOARD_PUBLISHED"} <= set(stages), " -> ".join(stages))
check("no futures stage claims paper eligibility, execution or a fill",
      not ({"PAPER_ELIGIBLE", "EXECUTION_CHECK", "EXECUTION_ACCEPTED",
            "FILLED", "POSITION_OPEN"} & set(stages)))
check("every futures lifecycle row carries its own event id",
      all(r.get("event_id") for r in life))

first_id = card.global_signal_id
again = fs.evaluate("NIFTY", cs, cs[-1].close, ind(), MarketStatus.TRENDING,
                    contract=CONTRACT, feed_age_sec=2.0, minutes_to_close=200.0,
                    now=NOW + 30)
vis.observe_futures("NIFTY", again, NOW + 30)
check("the same futures plan repeated is one episode, not two signals",
      again.global_signal_id == first_id and again.episode_id == card.episode_id,
      f"{again.episode_id}")

vis.mark_futures_visible("NIFTY", card, now=NOW + 31)
seen = [r["stage"] for r in rows("signal_lifecycle.jsonl")
        if r["global_signal_id"] == first_id]
check("publication and the user actually seeing it are separate stages",
      "USER_VISIBLE" in seen and "DASHBOARD_PUBLISHED" in seen)

reset()
no_setup = fs.evaluate("NIFTY", cs, cs[-1].close, ind(), MarketStatus.RANGING,
                       contract=CONTRACT, feed_age_sec=2.0,
                       minutes_to_close=200.0, now=NOW)
vis.observe_futures("NIFTY", no_setup, NOW)
published = [r for r in rows("signal_lifecycle.jsonl")
             if r["global_signal_id"] == no_setup.global_signal_id
             and r["stage"] == "DASHBOARD_PUBLISHED"]
check("NO_SIGNAL is published as a statement about the market, not dropped",
      bool(published) and published[0]["reason"] == fs.NO_SETUP,
      published[0]["reason"] if published else "no row")

reset()
settings.futures_max_cost_to_risk_pct = 0.01
bad = fs.evaluate("NIFTY", cs, cs[-1].close, ind(), MarketStatus.TRENDING,
                  contract=CONTRACT, feed_age_sec=2.0, minutes_to_close=200.0,
                  now=NOW)
settings.futures_max_cost_to_risk_pct = _max_cost
vis.observe_futures("NIFTY", bad, NOW)
inv = [r for r in rows("signal_lifecycle.jsonl")
       if r["global_signal_id"] == bad.global_signal_id
       and r["status"] == "MISSED" and r["stage"] == "PLAN_VALIDATED"]
check("a directional plan that failed validation is recorded as refused",
      bool(inv) and inv[0]["reason"] == "PLAN_INVALID"
      and inv[0]["detail"]["invalid_reason"] == fs.COST_TOO_HIGH,
      inv[0]["reason"] if inv else "no row")
check("and the checks that failed are named on the lifecycle row",
      bool(inv) and inv[0]["detail"]["failed_checks"] == ["cost_to_risk"],
      str(inv[0]["detail"]["failed_checks"]) if inv else "")

# --- 5. the futures journal row is point-based and research only -------------
reset()
card = fs.evaluate("NIFTY", cs, cs[-1].close, ind(), MarketStatus.TRENDING,
                   contract=CONTRACT, feed_age_sec=2.0, minutes_to_close=200.0,
                   now=NOW)
vis.observe_futures("NIFTY", card, NOW)
sj.observe_futures("NIFTY", card, NOW)
jrows = [r for r in sj.read_journal(limit=50) if r.get("market") == "FUTURES"]
check("the futures plan is journalled", len(jrows) == 1, str(len(jrows)))
row = jrows[-1] if jrows else {}
check("the journal row carries all three futures ids",
      row.get("global_signal_id") == card.global_signal_id
      and row.get("episode_id") == card.episode_id
      and row.get("futures_signal_id") == card.futures_signal_id)
check("the journalled vehicle is FUTURES, not an option leg",
      row.get("signal_vehicle") == "FUTURES" and row.get("vehicle") == "FUTURES")
check("the journalled plan states its unit is index points",
      (row.get("entry_plan") or {}).get("unit") == "INDEX_POINTS")
check("the futures row is never followed as a traded outcome",
      row.get("followed") is False
      and row.get("not_followed_reason") == "RESEARCH_ONLY")
check("and it says so on the row itself", row.get("research_only") is True)

# --- 6. journal filters by MARKET / VEHICLE / STATUS (Part 36) ---------------
opt = Decision(
    signal=Signal.BUY, confidence=70.0, signal_strength=70.0, trade_quality="B",
    recommended_option="NIFTY26AUG2524000CE", option_type=OptionType.CALL,
    strike=24000.0, current_premium=120.0, stop_loss=100.0, target1=160.0,
    target2=180.0, target3=200.0, spot_price=24000.0, vehicle="CE",
)
vis.observe_option("NIFTY", opt, sj.board_key(opt), now=NOW)
sj.observe("NIFTY", opt, [], ind(), 24000.0, MarketStatus.TRENDING, cs,
           now=NOW, expiry="2026-08-26", minutes_to_expiry=400)
fut_only = journal_stats.history(limit=50, filters={"market": "FUTURES"})
opt_only = journal_stats.history(limit=50, filters={"market": "OPTIONS"})
check("the MARKET filter separates the two vehicles' journals",
      len(fut_only) == 1 and len(opt_only) == 1,
      f"futures={len(fut_only)} options={len(opt_only)}")
check("the VEHICLE filter selects the futures leg",
      len(journal_stats.history(limit=50,
                                filters={"signal_vehicle": "FUTURES"})) == 1)
check("the VEHICLE filter selects a CE leg without matching FUTURES",
      len(journal_stats.history(limit=50, filters={"signal_vehicle": "CE"})) == 1)
check("every journalled row is given a lifecycle STATUS",
      all(r.get("signal_status") for r in journal_stats.history(limit=50)))
check("the STATUS filter can select what has not resolved yet",
      len(journal_stats.history(limit=50, filters={"status": "ACTIVE"})) >= 1)

# --- 7. options vs futures is a research label, never a selection (Part 34) --
cmp_valid = fs.compare(opt, card, opt_spread_pct=0.63)
check("two valid plans are BOTH_VALID, not a winner",
      cmp_valid["label"] == "BOTH_VALID", cmp_valid["label"])
check("the option side's measured spread is carried into the comparison",
      cmp_valid["option"]["spread_pct"] == 0.63)
check("the futures spread stays UNAVAILABLE rather than a flattering zero",
      cmp_valid["futures"]["spread_pct"] is None)
check("the comparison states that neither vehicle replaces the other",
      "no winner is selected" in cmp_valid["note"])
stale_opt = Decision(
    signal=Signal.BUY, confidence=84.0, signal_strength=80.0, trade_quality="A",
    recommended_option="ICICIBANK25AUG251420PE", option_type=OptionType.PUT,
    current_premium=13.7, stop_loss=9.0, target1=10.3, vehicle="PE",
)
cmp_fut = fs.compare(stale_opt, card, opt_spread_pct=6.76)
check("an unenterable option plan against a valid futures plan is FUTURES_BETTER",
      cmp_fut["label"] == "FUTURES_BETTER"
      and cmp_fut["option"]["invalid_reason"] == "TARGET_NOT_ABOVE_ENTRY",
      cmp_fut["label"])
cmp_none = fs.compare(None, no_setup)
check("with neither side offering a plan the label is not a recommendation",
      cmp_none["label"] in ("BOTH_BAD", "UNKNOWN"), cmp_none["label"])
check("the option spread is None when the leg was not quoted",
      fs.option_spread_pct([], "NIFTY26AUG2524000CE") is None)

# --- 8. MARKET_SIGNAL vs VEHICLE_SIGNAL attribution (Part 24) ---------------
reset()
mv = Decision(
    signal=Signal.BUY, confidence=70.0, signal_strength=70.0, trade_quality="B",
    recommended_option="NIFTY26AUG2524000CE", option_type=OptionType.CALL,
    current_premium=100.0, stop_loss=90.0, target1=130.0, spot_price=24000.0,
    vehicle="CE",
)


def quote(symbol: str, premium: float) -> OptionQuote:
    return OptionQuote(symbol=symbol, strike=24000.0, option_type=OptionType.CALL,
                       premium=premium, iv=0.2, delta=0.5, gamma=0.01,
                       theta=-1.0, vega=2.0, oi=10000, oi_change=100,
                       volume=5000, bid=premium - 0.5, ask=premium + 0.5)


sj.observe("NIFTY", mv, [quote("NIFTY26AUG2524000CE", 100.0)], ind(), 24000.0,
           MarketStatus.TRENDING, cs, now=NOW, expiry="2026-08-26",
           minutes_to_expiry=400)
# The underlying goes the called way while the premium is stopped out anyway:
# a wide spread and decay, not a wrong read on the market.
sj.observe("NIFTY", mv, [quote("NIFTY26AUG2524000CE", 89.0)], ind(), 24060.0,
           MarketStatus.TRENDING, cs, now=NOW + 120, expiry="2026-08-26",
           minutes_to_expiry=400)
res = [r for r in rows("signal_outcomes.jsonl") if r.get("event") == "RESOLVED"]
check("a stopped option whose underlying rose is a VEHICLE failure, not direction",
      bool(res) and res[-1]["failure_kind"] == sj.VEHICLE_FAILURE,
      res[-1]["failure_kind"] if res else "no resolution")
check("the market read is recorded as correct even though the trade lost",
      bool(res) and res[-1]["market_signal_correct"] is True
      and res[-1]["vehicle_signal_correct"] is False)
check("the underlying move is reported in the direction that was called",
      bool(res) and res[-1]["market_move_points"] == 60.0,
      str(res[-1].get("market_move_points") if res else None))

reset()
sj.observe("NIFTY", mv, [quote("NIFTY26AUG2524000CE", 100.0)], ind(), 24000.0,
           MarketStatus.TRENDING, cs, now=NOW, expiry="2026-08-26",
           minutes_to_expiry=400)
sj.observe("NIFTY", mv, [quote("NIFTY26AUG2524000CE", 85.0)], ind(), 23900.0,
           MarketStatus.TRENDING, cs, now=NOW + 120, expiry="2026-08-26",
           minutes_to_expiry=400)
res = [r for r in rows("signal_outcomes.jsonl") if r.get("event") == "RESOLVED"]
check("an underlying that went the other way is a DIRECTION failure",
      bool(res) and res[-1]["failure_kind"] == sj.DIRECTION_FAILURE
      and res[-1]["market_signal_correct"] is False,
      res[-1]["failure_kind"] if res else "no resolution")

# --- 9. reconciliation sees the futures vehicle separately (Part 27) --------
reset()
card = fs.evaluate("NIFTY", cs, cs[-1].close, ind(), MarketStatus.TRENDING,
                   contract=CONTRACT, feed_age_sec=2.0, minutes_to_close=200.0,
                   now=NOW)
vis.observe_futures("NIFTY", card, NOW)
sj.observe_futures("NIFTY", card, NOW)
vis.observe_option("NIFTY", opt, sj.board_key(opt), now=NOW)
sj.observe("NIFTY", opt, [], ind(), 24000.0, MarketStatus.TRENDING, cs,
           now=NOW, expiry="2026-08-26", minutes_to_expiry=400)
rep = recon.reconcile()
check("the report counts option and futures dashboard signals apart",
      rep["counts"]["option_dashboard_buys"] >= 1
      and rep["counts"]["futures_dashboard_buys"] >= 1,
      json.dumps({k: v for k, v in rep["counts"].items() if "dashboard" in k}))
check("a futures research plan is not counted as an execution attempt",
      rep["counts"]["execution_attempts"] == 0)
check("nothing published is reported as missing its dashboard row",
      not [m for m in rep["missing_dashboard_publication"]
           if m["episode_id"] == card.episode_id]
      and not [m for m in rep["missing_journal_row"]
               if m["episode_id"] == card.episode_id])

# --- 10. Part 38 scenario 14: this vehicle cannot place an order -------------
src = open(os.path.join("app", "analysis", "futures_signal.py"),
           encoding="utf-8").read()
for banned in ("place_order", "smart_connect", "broker", "auto_trade"):
    check(f"the futures research engine never mentions {banned}",
          banned not in src)
check("it imports only pure helpers from the futures paper tool",
      "from app.execution.futures_paper import _cost_points, _entry_setup, _levels"
      in src)
mod = __import__("app.analysis.futures_signal", fromlist=["x"])
check("the module exposes no order, buy, sell or size entry point",
      not [n for n in dir(mod)
           if n.startswith(("order", "buy", "sell", "place", "size"))])
check("a valid futures plan still refuses to call itself executable",
      card.status == fs.VALID and card.executable is False
      and card.research_only is True)

# --- 11. Part 38 scenario 16: a refusal is never a silent disappearance ------
reset()
settings.futures_max_cost_to_risk_pct = 0.01
bad = fs.evaluate("NIFTY", cs, cs[-1].close, ind(), MarketStatus.TRENDING,
                  contract=CONTRACT, feed_age_sec=2.0, minutes_to_close=200.0,
                  now=NOW)
settings.futures_max_cost_to_risk_pct = _max_cost
vis.observe_futures("NIFTY", bad, NOW)
missed = recon.missed_signals()
mine = [m for m in missed if m["episode_id"] == bad.episode_id]
check("a refused futures plan appears in the missed-signal report with a reason",
      bool(mine) and mine[0]["missed_reason"] == "PLAN_INVALID",
      mine[0]["missed_reason"] if mine else "absent")
check("the refusal is classified as correctly refused, not a lost opportunity",
      bool(mine) and mine[0]["classification"] == "CORRECTLY_REFUSED"
      and mine[0]["detail"]["invalid_reason"] == fs.COST_TOO_HIGH,
      mine[0]["classification"] if mine else "")
check("the refusal names the vehicle and instrument it concerns",
      bool(mine) and mine[0]["vehicle"] == "FUTURES"
      and mine[0]["instrument"] == "NIFTY")
check("a correct refusal is reported at the plan stage, not as a lost trade",
      bool(mine) and mine[0]["missed_stage"] == "PLAN_VALIDATED"
      and mine[0]["filled"] is False,
      mine[0]["missed_stage"] if mine else "")

print()
if FAILS:
    print(f"FUTURES SIGNAL SMOKE FAILED ({len(FAILS)}): " + "; ".join(FAILS))
    raise SystemExit(1)
print(f"FUTURES SIGNAL SMOKE PASSED ({len(RAN)} checks)")
