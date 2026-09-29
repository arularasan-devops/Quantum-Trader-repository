"""Phase 12 §2 — reconciliation must read the whole session or say it did not.

The measured defect this pins down: the 25 Aug session wrote 24,301 lifecycle
rows and ``reconcile()`` defaulted to ``limit=20000`` reading the *tail*, so the
daily artefact silently dropped 08:55–09:20 — the market open — while still
printing counts that looked like the day. The regression below builds a ledger
larger than the old cap, puts a BUY inside the part the cap used to cut, and
requires the report both to find it and to declare its own coverage.
"""
from __future__ import annotations

import json
import os
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="p12_")
os.environ["QT_DATA_DIR"] = _TMP

from app.config import settings  # noqa: E402

settings.data_dir = _TMP
settings.signal_lifecycle_log = True
settings.signal_journal_log = True

from app.analysis import signal_lifecycle as lc  # noqa: E402
from app.analysis import signal_reconciliation as recon  # noqa: E402

FAILS: list[str] = []
SESSION = "2026-08-25"
# 09:00 IST on the session date, so the rows land in the session they claim.
OPEN_TS = 1_787_628_600


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")
    if not cond:
        FAILS.append(name)


def row(ts: int, episode: str, stage: str, *, status: str = lc.OK,
        reason: str | None = None, instrument: str = "NIFTY",
        vehicle: str = "CE", market: str = lc.OPTIONS) -> dict:
    return {
        "event_id": f"{episode}-{stage}-{ts}",
        "global_signal_id": f"{instrument}-{vehicle}-{ts}",
        "episode_id": episode,
        "ts": ts,
        "ts_precise": float(ts),
        "time_ist": lc._ist(ts),
        "session": SESSION,
        "stage": stage,
        "status": status,
        "instrument": instrument,
        "vehicle": vehicle,
        "market": market,
        "source": "smoke",
        "reason": reason,
        "value": None,
        "threshold": None,
    }


# --- a ledger bigger than the retired 20,000-row cap -------------------------
# One BUY episode at the very start (inside the window the cap used to drop),
# one at the very end, and 25,000 rows of churn in between.
OPENING = f"NIFTY-{SESSION}-BUY|NIFTY25AUG2624000CE|CE|24000.0|-"
CLOSING = f"BANKNIFTY-{SESSION}-BUY|BANKNIFTY25AUG2653000PE|PE|53000.0|-"

lines: list[dict] = [
    row(OPEN_TS, OPENING, lc.GENERATED, reason="BUY"),
    row(OPEN_TS, OPENING, lc.DASHBOARD_PUBLISHED),
    row(OPEN_TS, OPENING, lc.USER_VISIBLE),
]
for i in range(25_000):
    ep = f"CRUDEOIL-{SESSION}-WAIT|CRUDEOIL17SEP268200CE|CE|8200.0|-"
    lines.append(row(OPEN_TS + 60 + i, ep, lc.GENERATED,
                     reason="DUPLICATE_EPISODE", instrument="CRUDEOIL"))
lines += [
    row(OPEN_TS + 30_000, CLOSING, lc.GENERATED, reason="BUY",
        instrument="BANKNIFTY", vehicle="PE"),
    row(OPEN_TS + 30_000, CLOSING, lc.DASHBOARD_PUBLISHED,
        instrument="BANKNIFTY", vehicle="PE"),
]

with open(os.path.join(_TMP, lc.LEDGER_LOG), "w", encoding="utf-8") as fh:
    for rec in lines:
        fh.write(json.dumps(rec) + "\n")

TOTAL = len(lines)
print(f"ledger rows written: {TOTAL}\n")

# --- 1. the complete session is read by default ------------------------------
records, cov = lc.scan_ledger(session=SESSION)
check("scan_ledger reads every row of the session by default",
      len(records) == TOTAL, f"{len(records)} of {TOTAL}")
check("coverage counts seen and processed alike when nothing is dropped",
      cov["records_seen"] == cov["records_processed"] == TOTAL)
check("records_omitted is zero on a complete read",
      cov["records_omitted"] == 0)
check("a complete read is not flagged as truncated", cov["truncated"] is False)

# --- 2. an explicit limit is reported, never silent --------------------------
_, cov_cut = lc.scan_ledger(session=SESSION, limit=20_000)
check("an explicit limit reports exactly what it left out",
      cov_cut["records_omitted"] == TOTAL - 20_000,
      str(cov_cut["records_omitted"]))
check("a truncated read says so", cov_cut["truncated"] is True)

# --- 3. the report declares its own completeness ------------------------------
rep = recon.reconcile(session=SESSION)
check("the default report is COMPLETE", rep["report_status"] == "COMPLETE")
check("the report exposes records_seen/processed/omitted",
      {"records_seen", "records_processed", "records_omitted"}
      <= set(rep["coverage"]))
check("the report processed every row",
      rep["coverage"]["records_processed"] == TOTAL)
check("records_omitted is zero in the report", rep["coverage"]["records_omitted"] == 0)

cut = recon.reconcile(session=SESSION, limit=20_000)
check("a sampled report fails rather than passing quietly",
      cut["report_status"] == "INCOMPLETE" and cut["complete"] is False)
check("the failed report says how much it lost",
      cut["coverage"]["records_omitted"] == TOTAL - 20_000)

# --- 4. the BUY the old cap dropped is now counted ---------------------------
check("both BUY episodes are counted", rep["counts"]["engine_buys"] == 2,
      str(rep["counts"]["engine_buys"]))
check("the opening BUY is missing from the sampled report",
      cut["counts"]["engine_buys"] == 1, str(cut["counts"]["engine_buys"]))
check("the opening BUY is recorded as published and seen",
      rep["counts"]["dashboard_published"] == 2
      and rep["counts"]["user_visible"] == 1)
check("churn is folded into episodes, not counted as calls",
      rep["counts"]["unique_episodes"] == 3,
      str(rep["counts"]["unique_episodes"]))

# --- 5. the markdown carries the failure where a human reads it --------------
md_ok = recon.daily_markdown(rep)
md_bad = recon.daily_markdown(cut)
check("a complete report's page does not claim to be incomplete",
      "REPORT INCOMPLETE" not in md_ok)
check("an incomplete report's page says so in the first lines",
      "REPORT INCOMPLETE" in md_bad.split("\n\n")[1])
check("the page states seen/processed/omitted",
      "omitted:" in md_ok and "processed:" in md_ok)

# --- 6. write_daily surfaces the status to its caller ------------------------
out = recon.write_daily(session=SESSION)
check("write_daily reports its status", out["report_status"] == "COMPLETE")
check("write_daily reports coverage", out["coverage"]["records_omitted"] == 0)
check("write_daily writes the unsuffixed and dated pair",
      len(out["written"]) == 4
      and all(os.path.exists(p) for p in out["written"]))
saved = json.load(open(os.path.join(_TMP, recon.DAILY_JSON), encoding="utf-8"))
check("the saved artefact carries its own completeness",
      saved["report_status"] == "COMPLETE" and saved["complete"] is True)


# =============================================================================
# §1 — signal churn: a repeat is published only when something changed
# =============================================================================
from app.analysis import signal_churn as churn  # noqa: E402

print("\n-- §1 signal churn control --")
churn.reset_for_tests()

EP = "NIFTY-2026-08-25-BUY|NIFTY25AUG2624000CE|CE|24000.0|-"


def snap(**kw) -> churn.Snapshot:
    base = dict(action="BUY", premium=100.0, score=72.0, direction="CE",
                strike=24000.0, setup="PULLBACK", plan_version=1,
                regime="UP", entry_low=98.0, entry_high=102.0,
                actionable=True)
    base.update(kw)
    return churn.Snapshot(**base)


t = 1_787_628_600.0
first = churn.observe(EP, snap(), t, instrument="NIFTY", market="OPTIONS")
check("the first event of an episode is INITIAL and published",
      first["publish"] is True and first["event_type"] == churn.INITIAL)

# 200 unchanged ticks, 2s apart: inside the heartbeat, so none is published.
for i in range(200):
    res = churn.observe(EP, snap(premium=100.0 + i * 0.01), t + 2 * (i + 1))
check("an unchanged repeat is suppressed, not published",
      res["publish"] is False and res["event_type"] == churn.SUPPRESSED)
check("the suppression names its reason",
      res["reasons"] == [churn.NO_MATERIAL_CHANGE])
st = churn.episode_stats(EP)
check("every raw event is still counted while publication is throttled",
      st["raw_event_count"] == 201, str(st["raw_event_count"]))
check("the episode exposes raw/meaningful/last_published_at",
      {"raw_event_count", "meaningful_update_count", "last_published_at"}
      <= set(st))
check("200 unchanged ticks over ~7 minutes publish only the heartbeats",
      st["published_count"] <= 3 and st["suppressed_count"] >= 197,
      f"published {st['published_count']}, suppressed {st['suppressed_count']}")
check("the heartbeat is what published them, not a claimed change",
      st["meaningful_update_count"] == 0 and st["heartbeat_count"] >= 1,
      f"meaningful {st['meaningful_update_count']}, "
      f"heartbeats {st['heartbeat_count']}")

# A material premium move republishes at once.
mat = churn.observe(EP, snap(premium=104.0), t + 410.0)
check("a material premium move is a MEANINGFUL_UPDATE",
      mat["publish"] is True
      and mat["event_type"] == churn.MEANINGFUL_UPDATE
      and churn.PREMIUM_MOVED in mat["reasons"], str(mat["reasons"]))

# The heartbeat restates a long-lived unchanged call.
hb = churn.observe(EP, snap(premium=104.0), t + 410.0 + 301.0)
check("an unchanged call is restated on the heartbeat",
      hb["event_type"] == churn.HEARTBEAT and hb["publish"] is True)

# Each named trigger publishes on its own.
for name, kw, want in (
    ("score", {"score": 60.0}, churn.SCORE_MOVED),
    ("entry zone", {"entry_low": 108.0, "entry_high": 112.0}, churn.ZONE_MOVED),
    ("direction", {"direction": "PE"}, churn.DIRECTION_CHANGED),
    ("strike", {"strike": 24100.0}, churn.STRIKE_CHANGED),
    ("setup", {"setup": "BREAKOUT_RETEST"}, churn.SETUP_CHANGED),
    ("plan version", {"plan_version": 2}, churn.PLAN_VERSION_CHANGED),
    ("regime", {"regime": "SIDEWAYS"}, churn.REGIME_CHANGED),
    ("action", {"action": "WAIT"}, churn.ACTION_CHANGED),
):
    ep = f"{EP}-{name}"
    churn.observe(ep, snap(), t)
    got = churn.observe(ep, snap(**kw), t + 2.0)
    check(f"a {name} change republishes",
          got["publish"] is True and want in got["reasons"], str(got["reasons"]))

churn.observe(f"{EP}-inv", snap(), t)
inv = churn.observe(f"{EP}-inv", snap(actionable=False), t + 2.0)
check("a plan going unenterable publishes as INVALIDATED",
      inv["event_type"] == churn.INVALIDATED
      and churn.INVALIDATION in inv["reasons"])
rev = churn.observe(f"{EP}-inv", snap(actionable=True), t + 4.0)
check("a plan becoming enterable again publishes as a revalidation",
      churn.REVALIDATION in rev["reasons"])

res_ev = churn.mark_terminal(EP, churn.RESOLVED, t + 3600.0)
check("a resolution is always publishable",
      res_ev is not None and res_ev["publish"] is True
      and res_ev["event_type"] == churn.RESOLVED)

rep_churn = churn.report()
check("the churn report states raw events against publications",
      rep_churn["totals"]["raw_events"] > rep_churn["totals"]["publications"])
check("the churn report quantifies what was removed",
      rep_churn["totals"]["churn_removed_pct"] > 50.0,
      str(rep_churn["totals"]["churn_removed_pct"]))
check("the noisiest episode is listed first",
      rep_churn["episodes"][0]["episode_id"] == EP)

# With the control off, behaviour is the old one — so before/after is measurable.
churn.reset_for_tests()
settings.signal_churn_control = False
churn.observe(EP, snap(), t)
off = churn.observe(EP, snap(), t + 2.0)
check("with churn control off every repeat is published again",
      off["publish"] is True)
settings.signal_churn_control = True

# --- the ledger itself: the same call on 300 ticks writes few rows -----------
from app.analysis import signal_visibility as vis  # noqa: E402
from app.models import Decision, Signal  # noqa: E402


def decision(premium: float) -> Decision:
    return Decision(
        signal=Signal.BUY, market_signal=Signal.BUY, confidence=88.0,
        signal_strength=70.0, trade_quality="GOOD", trade_score=72.0,
        recommended_option="NIFTY25AUG2624000CE", strike=24000.0,
        current_premium=premium, entry_range=(premium - 2, premium + 2),
        stop_loss=premium * 0.8, target1=premium * 1.3, entry_trigger="PULLBACK",
        htf_trend="UP", plan_actionable=True,
    )


vis.reset_for_tests()
os.remove(os.path.join(_TMP, lc.LEDGER_LOG))
lc.reset_for_tests()
key = "BUY|NIFTY25AUG2624000CE|CE|24000.0|-"
for i in range(300):
    # A live setup ticking every 3s for 15 minutes, premium drifting 0.01%/tick.
    d = decision(100.0 * (1 + 0.0001 * i))
    vis.observe_option("NIFTY", d, key, now=OPEN_TS + 3 * i)

rows = lc.read_ledger()
repeats = [r for r in rows if r.get("reason") == lc.DUPLICATE_EPISODE]
check("300 raw ticks of one unchanged call do not write 300 ledger rows",
      len(repeats) <= 10, f"{len(repeats)} repeat rows")
check("the repeat rows carry an event_type",
      all((r.get("detail") or {}).get("event_type") in churn.EVENT_TYPES
          for r in repeats))
check("the repeat rows carry the raw events they stand for",
      all((r.get("detail") or {}).get("raw_event_count") for r in repeats))
last = vis.churn.episode_stats(lc.episode_id("NIFTY", key, OPEN_TS))
check("the episode counted all 300 raw events",
      last is not None and last["raw_event_count"] == 300,
      str((last or {}).get("raw_event_count")))
check("the decision exposes the counters to the dashboard",
      d.raw_event_count == 300 and d.event_type in churn.EVENT_TYPES
      and d.last_published_at is not None)

# ---------------------------------------------------------------------------
# §11 futures contract rollover — research only
# ---------------------------------------------------------------------------
print("\n--- §11 futures contract rollover ---")
from app.analysis import futures_rollover as roll  # noqa: E402

NOW = OPEN_TS
chain = [
    {"symbol": "CRUDEOIL25AUGFUT", "expiry": "2026-08-26", "lot_size": 100,
     "exchange": "MCX"},
    {"symbol": "CRUDEOIL25SEPFUT", "expiry": "2026-09-25", "lot_size": 100,
     "exchange": "MCX"},
    {"symbol": "CRUDEOIL25OCTFUT", "expiry": "2026-10-27", "lot_size": 100,
     "exchange": "MCX"},
]
roll.reset_for_tests()
sel = roll.select("CRUDEOIL", chain[0], chain, NOW)
check("a contract expiring in one day is not what the plan is written on",
      sel["selected_contract"] == "CRUDEOIL25SEPFUT" and sel["rolled"],
      f"{sel['selected_contract']} / rolled={sel['rolled']}")
check("the roll states which contract, which expiry, how many days and why",
      sel["expiry"] == "2026-09-25" and sel["days_to_expiry"] >= 3
      and sel["reason_for_roll"] == roll.ROLLED_NEAR_EXPIRY,
      f"{sel['expiry']} dte={sel['days_to_expiry']} {sel['reason_for_roll']}")
check("a rolled plan admits its levels come from the quoted contract",
      sel["price_basis"] == "CRUDEOIL25AUGFUT" and bool(sel["price_basis_note"]))
check("the lot size travels with the selected contract, not the old one",
      sel["lot_size"] == 100)
check("nothing about a rollover is executable",
      sel["research_only"] and not sel["executable"])

far = roll.select("CRUDEOIL", chain[1], chain, NOW)
check("a contract with room is left alone",
      not far["rolled"] and far["selected_contract"] == "CRUDEOIL25SEPFUT"
      and far["reason_for_roll"] == roll.NOT_ROLLED,
      far["reason_for_roll"])

only_near = roll.select("NATURALGAS", chain[0], [chain[0]], NOW)
check("a chain with nowhere to roll says so instead of inventing a contract",
      not only_near["rolled"]
      and only_near["reason_for_roll"] == roll.ROLL_UNAVAILABLE
      and only_near["selected_contract"] == "CRUDEOIL25AUGFUT",
      only_near["reason_for_roll"])

none_at_all = roll.select("GOLD", None, [], NOW)
check("no chain at all is CHAIN_UNAVAILABLE, not a silent pick",
      none_at_all["selected_contract"] is None
      and none_at_all["reason_for_roll"] == roll.CHAIN_UNAVAILABLE,
      none_at_all["reason_for_roll"])

undated = roll.select("SILVER", {"symbol": "SILVERFUT", "expiry": None},
                      [{"symbol": "SILVERFUT", "expiry": None}], NOW)
check("an expiry the feed never stated is not treated as far away",
      undated["reason_for_roll"] == roll.EXPIRY_UNKNOWN
      and undated["days_to_expiry"] is None,
      undated["reason_for_roll"])

for _ in range(50):
    roll.record(roll.select("CRUDEOIL", chain[0], chain, NOW))
rep = roll.report()
crude = next(r for r in rep["rows"] if r["instrument"] == "CRUDEOIL")
check("the report holds one row per instrument, not one per tick",
      len([r for r in rep["rows"] if r["instrument"] == "CRUDEOIL"]) == 1)
check("repeating the same selection is one roll event, not fifty",
      rep["roll_events"] == 1 and rep["observations"] == 50,
      f"events={rep['roll_events']} obs={rep['observations']}")
check("the report names the price-basis caveat rather than burying it",
      bool(rep["price_basis_caveat"]) and crude["rolled"])

# The engine itself must now produce a plan in expiry week instead of refusing.
from app.analysis import futures_signal as fs  # noqa: E402
from app.models import Candle, IndicatorSnapshot, MarketStatus  # noqa: E402

candles = []
base = 6000.0
for i in range(120):
    px = base + i * 2.0
    candles.append(Candle(time=NOW - (120 - i) * 60, open=px, high=px + 4,
                          low=px - 4, close=px + 1, volume=1200))
ind = IndicatorSnapshot(rsi=62.0, macd=4.0, macd_signal=2.0, ema_fast=6100.0,
                        ema_slow=6050.0, atr=40.0, adx=28.0, trend="UP",
                        momentum=0.6, support=6000.0, resistance=6400.0)
card = fs.evaluate("CRUDEOIL", candles, candles[-1].close, ind,
                   MarketStatus.TRENDING, contract=chain[0], chain=chain,
                   feed_age_sec=5.0, minutes_to_close=180.0, now=NOW)
check("expiry week no longer refuses the whole futures research engine",
      card.invalid_reason != fs.EXPIRY_TOO_CLOSE,
      f"{card.status} / {card.invalid_reason}")
check("the card names the rolled contract and the one still being quoted",
      card.contract == "CRUDEOIL25SEPFUT"
      and card.quoted_contract == "CRUDEOIL25AUGFUT" and card.rolled,
      f"{card.contract} <- {card.quoted_contract}")
check("the card carries the roll reason and the basis caveat",
      card.reason_for_roll == roll.ROLLED_NEAR_EXPIRY
      and bool(card.price_basis_note))
check("a rolled research card is still research only",
      card.research_only and not card.executable)

# ---------------------------------------------------------------------------
# §6 direction attribution — five definitions, no single headline number
# ---------------------------------------------------------------------------
print("\n--- §6 direction attribution ---")
from app.analysis import direction_attribution as da  # noqa: E402


def path(atr: float | None, stop_dist: float | None, *, fav_half=None,
         fav_one=None, adverse=None, tgt=None, stp=None, seen=True) -> dict:
    m = da.marks_template(atr, 100.0, None, "BULLISH")
    m["stop_distance"] = stop_dist
    m.update({"fav_half_atr_sec": fav_half, "fav_one_atr_sec": fav_one,
              "adverse_atr_sec": adverse, "target_1r_sec": tgt,
              "stop_sec": stp, "favorable_seen": seen})
    return m


v = da.verdicts(path(10.0, 10.0, fav_half=30, fav_one=90, adverse=200,
                     tgt=120, stp=None), 8.0)
check("a move that got there first passes B, C and D",
      v[da.B] and v[da.C] and v[da.D])
check("a favourable finish passes E", v[da.E] is True)

v = da.verdicts(path(10.0, 10.0, fav_half=30, adverse=20, tgt=None, stp=15), -4.0)
check("half an ATR after the adverse threshold does not pass B",
      v[da.B] is False, str(v[da.B]))
check("a stop before the target does not pass D", v[da.D] is False)
check("an adverse finish fails E, even though A passed",
      v[da.E] is False and v[da.A] is True)

v = da.verdicts(path(None, None, fav_half=None, seen=True), None)
check("no ATR means B and C are unknown, not false",
      v[da.B] is None and v[da.C] is None)
check("no underlying stop means D is unknown, not false", v[da.D] is None)
check("no recorded finish means E is unknown, not false", v[da.E] is None)

marks = da.marks_template(20.0, 1000.0, 990.0, "BULLISH")
check("the 1R target distance is the plan's own stop distance, not a new number",
      marks["stop_distance"] == 10.0, str(marks["stop_distance"]))
for i, px in enumerate([1002.0, 1011.0, 1021.0, 980.0]):
    da.observe_underlying(marks, px, 1000.0, 60.0 * (i + 1))
check("crossings are latched on the first crossing, not the last",
      marks["fav_half_atr_sec"] == 120.0 and marks["fav_one_atr_sec"] == 180.0,
      f"half={marks['fav_half_atr_sec']} one={marks['fav_one_atr_sec']}")
check("the adverse threshold is recorded with its own time",
      marks["adverse_atr_sec"] == 240.0, str(marks["adverse_atr_sec"]))

short = da.marks_template(20.0, 1000.0, 1010.0, "BEARISH")
da.observe_underlying(short, 980.0, 1000.0, 60.0)
check("a bearish call measures a fall as favourable",
      short["favorable_seen"] and short["fav_one_atr_sec"] == 60.0)

body = da.summarise([da.verdicts(path(10.0, 10.0, fav_half=1, fav_one=2), 5.0)
                     for _ in range(10)], min_sample=30)
check("a sample under the minimum reports INSUFFICIENT_SAMPLE, not a rate",
      body["definitions"][da.B]["status"] == "INSUFFICIENT_SAMPLE"
      and body["definitions"][da.B]["correct_pct"] is None)
check("no single directional accuracy figure is published",
      body["single_number_withheld"] and bool(body["single_number_reason"]))
check("all five definitions are reported, none omitted",
      set(body["definitions"]) == set(da.DEFINITIONS))

mixed = ([da.verdicts(path(10.0, 10.0, fav_half=1, fav_one=2), 5.0)] * 30
         + [da.verdicts(path(10.0, 10.0, fav_half=9, adverse=1), -5.0)] * 10)
body = da.summarise(mixed, min_sample=30)
check("with enough samples each definition reports its own rate",
      body["definitions"][da.A]["correct_pct"] == 100.0
      and body["definitions"][da.B]["correct_pct"] == 75.0
      and body["definitions"][da.E]["correct_pct"] == 75.0,
      f"A={body['definitions'][da.A]['correct_pct']} "
      f"B={body['definitions'][da.B]['correct_pct']} "
      f"E={body['definitions'][da.E]['correct_pct']}")
check("definition A is visibly the most generous of the five",
      body["definitions"][da.A]["correct_pct"]
      >= body["definitions"][da.B]["correct_pct"])

legacy = da.summarise_outcomes([
    {"event": "RESOLVED", "market_signal_correct": True},
    {"event": "RESOLVED", "market_signal_correct": True},
    {"event": "RESOLVED", "market_signal_correct": False},
])
check("rows resolved before this build are not back-filled from the old rule",
      legacy["rows_without_path"] == 3
      and legacy["definitions"][da.A]["sample"] == 0)
check("the retired figure is reported once, as the retired figure",
      legacy["legacy_any_favorable_pct"] == 66.7,
      str(legacy["legacy_any_favorable_pct"]))

# ---------------------------------------------------------------------------
# §3 instrument families + §4 tradability
# ---------------------------------------------------------------------------
print("\n--- §3 families + §4 tradability ---")
from app.analysis import instrument_family as fam  # noqa: E402
from app.analysis import tradability as tr  # noqa: E402

check("an index root is INDEX, not a stock, even though it trades on NFO",
      fam.family("NIFTY") == fam.INDEX and fam.family("FINNIFTY") == fam.INDEX,
      f"{fam.family('NIFTY')} / {fam.family('FINNIFTY')}")
check("an MCX contract is MCX", fam.family("CRUDEOIL") == fam.MCX)
check("a single stock is STOCK", fam.family("RELIANCE") == fam.STOCK)
check("an instrument the registry never heard of is UNKNOWN, not a stock",
      fam.family("NOT_A_REAL_NAME") == fam.UNKNOWN)
check("every instrument §9 and §10 name resolves to the right family",
      all(fam.family(i) == fam.INDEX for i in fam.INDEX_STUDY)
      and all(fam.family(i) == fam.MCX for i in fam.MCX_STUDY))
check("the families do not overlap and the members are non-empty",
      all(fam.members(f) for f in fam.FAMILIES)
      and not set(fam.members(fam.INDEX)) & set(fam.members(fam.STOCK)))

groups = fam.group_by_family([{"instrument": "NIFTY"}, {"instrument": "GOLD"},
                              {"instrument": "TCS"}, {"instrument": "XYZ"}])
check("grouping keeps UNKNOWN as its own bucket instead of pooling it",
      len(groups[fam.INDEX]) == 1 and len(groups[fam.MCX]) == 1
      and len(groups[fam.STOCK]) == 1 and len(groups[fam.UNKNOWN]) == 1)

tr.reset_for_tests()
# an index-like leg: 0.8% spread, real book, room, affordable
good = tr.assess("NIFTY", premium=100.0, bid=99.6, ask=100.4, risk_points=20.0,
                 first_target=140.0, volume=5000, open_interest=20000,
                 quote_age_sec=2.0, lot_size=65)
check("a tight index-style leg with room is TRADABLE",
      good["status"] == tr.TRADABLE, f"{good['status']} {good['reasons']}")
# a single-stock-like leg: the 9.52% median from 25 Aug
bad = tr.assess("RELIANCE", premium=100.0, bid=95.3, ask=104.8, risk_points=20.0,
                first_target=140.0, volume=5000, open_interest=20000,
                quote_age_sec=2.0, lot_size=500)
check("the 25 Aug single-stock median spread comes out UNTRADABLE",
      bad["status"] == tr.UNTRADABLE and tr.SPREAD_EXTREME in bad["reasons"],
      f"{bad['status']} {bad['reasons']}")
check("the spread is reported against the plan's own risk, not just the premium",
      bad["spread_over_risk"] == round(9.5 / 20.0, 4),
      str(bad["spread_over_risk"]))

nobook = tr.assess("NIFTY", premium=100.0, bid=None, ask=None, risk_points=20.0,
                   first_target=140.0, quote_age_sec=1.0, lot_size=65)
check("a leg with no two-sided quote is UNKNOWN, never TRADABLE",
      nobook["status"] == tr.UNKNOWN and tr.NO_BOOK in nobook["reasons"],
      nobook["status"])
crossed = tr.assess("NIFTY", premium=100.0, bid=101.0, ask=99.0, risk_points=20.0,
                    lot_size=65)
check("a crossed book is UNTRADABLE, not a negative spread",
      crossed["status"] == tr.UNTRADABLE
      and tr.CROSSED_BOOK in crossed["reasons"])

stale = tr.assess("NIFTY", premium=100.0, bid=99.6, ask=100.4, risk_points=20.0,
                  first_target=140.0, volume=5000, open_interest=20000,
                  quote_age_sec=300.0, lot_size=65)
check("a stale quote is not evidence of a current spread",
      stale["status"] == tr.CAUTION and tr.STALE_QUOTE in stale["reasons"],
      stale["status"])
thin = tr.assess("NIFTY", premium=100.0, bid=99.6, ask=100.4, risk_points=20.0,
                 first_target=140.0, volume=1, open_interest=2,
                 quote_age_sec=2.0, lot_size=65)
check("a leg nothing traded on is CAUTION with THIN_LIQUIDITY",
      thin["status"] == tr.CAUTION and tr.THIN in thin["reasons"])
noroom = tr.assess("NIFTY", premium=100.0, bid=99.6, ask=100.4, risk_points=20.0,
                   first_target=105.0, volume=5000, open_interest=20000,
                   quote_age_sec=2.0, lot_size=65)
check("a target too close to cover the risk is flagged as room below min R",
      tr.ROOM_TOO_SMALL in noroom["reasons"]
      and noroom["expected_room_r"] == 0.25, str(noroom["expected_room_r"]))

# §10 affordability: a SILVER-sized lot against the configured paper pot
poor = tr.assess("SILVER", premium=4000.0, bid=3990.0, ask=4010.0,
                 risk_points=800.0, first_target=6000.0, volume=5000,
                 open_interest=20000, quote_age_sec=2.0, lot_size=30)
check("a lot that cannot fit the configured risk budget is UNAFFORDABLE",
      tr.UNAFFORDABLE in poor["reasons"] and poor["status"] == tr.UNTRADABLE,
      f"{poor['status']} lots={poor['affordability']['lots_affordable']}")
check("affordability states the lot cost and the risk per lot it was judged on",
      poor["affordability"]["lot_cost"] == 120000.0
      and poor["affordability"]["risk_per_lot"] == 24000.0)
check("no assessment is executable or a production gate",
      all(a["research_only"] for a in (good, bad, poor)))

tr.reset_for_tests()
for pct in (0.5, 0.8, 1.0, 4.0, 12.0):
    tr.record(tr.assess("RELIANCE", premium=100.0, bid=100.0 - pct / 2,
                        ask=100.0 + pct / 2, risk_points=20.0,
                        first_target=140.0, volume=5000, open_interest=20000,
                        quote_age_sec=1.0, lot_size=500))
tr.record(tr.assess("NIFTY", premium=100.0, bid=99.6, ask=100.4,
                    risk_points=20.0, first_target=140.0, volume=5000,
                    open_interest=20000, quote_age_sec=1.0, lot_size=65))
rep = tr.report()
rel = next(r for r in rep["instruments"] if r["instrument"] == "RELIANCE")
check("the study reports median, p75 and p90 spread per instrument (§8)",
      rel["spread_pct_median"] == 1.0 and rel["spread_pct_p75"] == 4.0
      and rel["spread_pct_p90"] == 12.0,
      f"{rel['spread_pct_median']}/{rel['spread_pct_p75']}/{rel['spread_pct_p90']}")
check("the sample size is reported beside every instrument row",
      rel["samples"] == 5 and rel["quoted_samples"] == 5)
check("families are reported apart, never pooled into one number",
      rep["families"][fam.STOCK]["samples"] == 5
      and rep["families"][fam.INDEX]["samples"] == 1)
check("the report states the thresholds it judged with",
      rep["thresholds"]["spread_untradable_pct"] > 0 and rep["research_only"])

# ---------------------------------------------------------------------------
# §7 failure taxonomy
# ---------------------------------------------------------------------------
print("\n--- §7 failure attribution ---")
from app.analysis import failure_attribution as fa  # noqa: E402

BASE = {"book_quoted": True, "underlying_recorded": True,
        "spread_pct_of_premium": 0.8, "spread_over_risk": 0.04,
        "expected_room_r": 2.0, "thin_liquidity": False, "delta": 0.5}
RIGHT = {da.A: True, da.B: True, da.C: True, da.D: True, da.E: True}
WRONG = {da.A: True, da.B: False, da.C: False, da.D: False, da.E: False}


def attrib(verdicts, **facts):
    trade = {**BASE, **facts}
    return fa.classify(verdicts=verdicts, trade=trade,
                       filled=facts.pop("filled", True) if "filled" in facts else True)


r = attrib(RIGHT, realized_r=1.4, mfe_r=1.8)
check("a winning trade is NO_FAILURE", r["failure_kind"] == fa.NO_FAILURE)
r = attrib(WRONG, realized_r=-1.0, mfe_r=0.1)
check("no direction on the stated definition is a DIRECTION_FAILURE",
      r["failure_kind"] == fa.DIRECTION_FAILURE
      and r["direction_decided_by"] == fa.PRIMARY, r["failure_kind"])
r = attrib(RIGHT, realized_r=-1.0, mfe_r=0.4, spread_pct_of_premium=9.5,
           spread_over_risk=0.48, underlying_favorable_move=40.0)
check("the read right and the option wrong is a VEHICLE_FAILURE with evidence",
      r["failure_kind"] == fa.VEHICLE_FAILURE
      and fa.EV_SPREAD in r["vehicle_evidence"]
      and fa.EV_DIVERGENCE in r["vehicle_evidence"], str(r["vehicle_evidence"]))
r = attrib(RIGHT, realized_r=-1.0, mfe_r=2.5, spread_pct_of_premium=9.5)
check("a 2.5R give-back is an EXIT_FAILURE, not the contract's fault",
      r["failure_kind"] == fa.EXIT_FAILURE, r["failure_kind"])
r = attrib(RIGHT, realized_r=-1.0, mfe_r=0.2)
check("a losing trade with no vehicle evidence is DATA_FAILURE, not an assertion",
      r["failure_kind"] == fa.DATA_FAILURE
      and "NO_VEHICLE_EVIDENCE" in r["data_gaps"], r["failure_kind"])
r = attrib(RIGHT, realized_r=-1.0, mfe_r=0.2, book_quoted=False)
check("no two-sided quote at signal time cannot be attributed to the market",
      r["failure_kind"] == fa.DATA_FAILURE and fa.NO_BOOK in r["data_gaps"])
r = attrib(None, realized_r=-1.0, mfe_r=0.2)
check("no answerable direction definition is DATA_FAILURE, not DIRECTION_FAILURE",
      r["failure_kind"] == fa.DATA_FAILURE
      and fa.NO_DIRECTION_DEFINITION in r["data_gaps"], r["failure_kind"])
r = fa.classify(verdicts=RIGHT, trade={**BASE, "realized_r": None, "mfe_r": None},
                filled=False)
check("a plan that never filled is an EXECUTION_FAILURE",
      r["failure_kind"] == fa.EXECUTION_FAILURE)
r = attrib({da.A: True, da.B: None, da.C: None, da.D: True, da.E: None},
           realized_r=-1.0, mfe_r=0.1, spread_pct_of_premium=9.5)
check("with B unanswerable the decision falls back and names which definition",
      r["direction_decided_by"] == da.D, r["direction_decided_by"])
r = attrib(RIGHT, realized_r=-1.0, mfe_r=0.1, delta=0.1,
           thin_liquidity=True, expected_room_r=0.4)
check("delta, liquidity and room are each named as vehicle evidence",
      {fa.EV_DELTA, fa.EV_LIQUIDITY, fa.EV_ROOM} <= set(r["vehicle_evidence"]),
      str(r["vehicle_evidence"]))

summary = fa.summarise([
    attrib(RIGHT, realized_r=-1.0, mfe_r=0.2, spread_pct_of_premium=9.5),
    attrib(RIGHT, realized_r=-1.0, mfe_r=0.2, spread_pct_of_premium=9.5),
    attrib(WRONG, realized_r=-1.0, mfe_r=0.1),
    attrib(RIGHT, realized_r=1.0, mfe_r=1.2),
])
check("the summary answers 'direction right, option wrong' as a share",
      summary["direction_right_option_wrong_pct"] == 50.0,
      str(summary["direction_right_option_wrong_pct"]))
check("unattributable rows are excluded from that share, not counted as either",
      summary["attributable"] == 4 and summary["unattributable"] == 0)
check("all six kinds exist in the summary, even at zero",
      set(summary["counts"]) == set(fa.KINDS))

# ---------------------------------------------------------------------------
# §8/§9/§10 per-instrument studies
# ---------------------------------------------------------------------------
print("\n--- §8/§9/§10 instrument studies ---")
from app.analysis import instrument_studies as ist  # noqa: E402


def call_row(instrument, sid, spread_pct, spread_points, entry=100.0, risk=20.0):
    return {
        "signal_id": sid, "instrument": instrument, "session": "2026-08-25",
        "signal_vehicle": "CE",
        "market_state": {"spread_pct_of_premium": spread_pct},
        "tradability": {"spread": spread_points,
                        "spread_over_risk": (round(spread_points / risk, 4)
                                             if spread_points is not None else None),
                        "status": "TRADABLE"},
        "entry_plan": {"entry_price": entry, "risk_points": risk},
    }


def out_row(instrument, sid, realized, order="STOP_FIRST", entry=100.0, risk=20.0):
    return {"event": "RESOLVED", "signal_id": sid, "instrument": instrument,
            "session": "2026-08-25", "realized_r": realized, "entry": entry,
            "risk_points": risk, "order": order, "outcome": "STOP",
            "failure_attribution": {"failure_kind": fa.VEHICLE_FAILURE}}


j, o = [], []
for n in range(12):  # a tight index name that pays
    j.append(call_row("NIFTY", f"n{n}", 0.8, 0.8))
    o.append(out_row("NIFTY", f"n{n}", 1.5, "TARGET_FIRST"))
for n in range(12):  # the 25 Aug single stock: 9.5% spread, affordable lot
    j.append(call_row("RELIANCE", f"r{n}", 9.5, 9.5, risk=8.0))
    o.append(out_row("RELIANCE", f"r{n}", 0.2, risk=8.0))
for n in range(3):  # too few to judge either way
    j.append(call_row("TCS", f"t{n}", 1.0, 1.0))
st = ist.study(j, o)
rows = {r["instrument"]: r for r in st["instruments"]}

check("an index name with a tight spread and positive net R is ECONOMIC",
      rows["NIFTY"]["verdict"] == ist.ECONOMIC, rows["NIFTY"]["verdict"])
check("a 9.5% spread name is UNECONOMIC and named as such",
      rows["RELIANCE"]["verdict"] == ist.UNECONOMIC
      and "RELIANCE" in st["uneconomic_names"], rows["RELIANCE"]["verdict"])
check("a small sample is INSUFFICIENT_SAMPLE, not a verdict",
      rows["TCS"]["verdict"] == ist.INSUFFICIENT)
check("gross R and net R are separate, and net is the smaller",
      rows["RELIANCE"]["gross_r_median"] == 0.2
      and rows["RELIANCE"]["net_r_median"] < 0,
      f"gross={rows['RELIANCE']['gross_r_median']} net={rows['RELIANCE']['net_r_median']}")
check("the spread is what turns a positive gross into a negative net",
      rows["RELIANCE"]["net_r_median"] < rows["RELIANCE"]["gross_r_median"])
check("median, p75, p90, spread/risk, win rate and sample size are all reported",
      all(rows["NIFTY"][k] is not None for k in
          ("spread_pct_median", "spread_pct_p75", "spread_pct_p90",
           "spread_over_risk_median", "win_rate_pct", "samples", "resolved")))
check("families are reported apart, with their own net R totals",
      st["families"][fam.INDEX]["instruments"] == 1
      and st["families"][fam.STOCK]["instruments"] == 2
      and st["families"][fam.INDEX]["net_r_total"] > 0
      and st["families"][fam.STOCK]["net_r_total"] < 0)
check("§9 and §10 have their own named slices",
      [r["instrument"] for r in st["named_studies"]["index_study"]] == ["NIFTY"]
      and st["named_studies"]["mcx_study"] == []
      and len(st["named_studies"]["stock_study"]) == 2)
check("A+ candidates and research-only names are stated, not implied",
      st["a_plus_candidates"] == ["NIFTY"]
      and set(st["research_only_names"]) == {"RELIANCE", "TCS"})

# §10 affordability: a SILVER lot that the configured pot cannot risk
j2 = [call_row("SILVER", f"s{n}", 1.6, 64.0, entry=4000.0, risk=800.0)
      for n in range(12)]
for row in j2:
    row["tradability"].pop("status", None)
st2 = ist.study(j2, [])
silver = st2["instruments"][0]
check("a lot the paper risk budget cannot hold is UNAFFORDABLE, not a failure",
      silver["verdict"] == ist.UNAFFORDABLE
      and "SILVER" in st2["unaffordable_names"], silver["verdict"])
check("the affordability row states lot cost, risk per lot and the budget",
      silver["affordability_sample"]["lot_cost"] is not None
      and silver["affordability_sample"]["risk_per_lot"] is not None
      and silver["affordability_sample"]["risk_budget"] > 0)
check("an unaffordable name is counted in no win rate at all",
      silver["resolved"] == 0 and silver["win_rate_pct"] is None)

fut = ist.study([{"signal_id": "f1", "instrument": "NIFTY",
                  "signal_vehicle": "FUTURES", "session": "2026-08-25",
                  "market_state": {"spread_pct_of_premium": 0.02},
                  "entry_plan": {"entry_price": 24000.0, "risk_points": 50.0}}], [])
check("futures rows are never pooled into the option studies",
      fut["instruments"] == [])
unquoted = ist.study([call_row("NIFTY", "x1", None, None)],
                     [out_row("NIFTY", "x1", 1.0)])["instruments"][0]
check("a resolved outcome with no quoted book has no net R rather than a fake one",
      unquoted["gross_r_median"] == 1.0 and unquoted["net_r_median"] is None,
      f"gross={unquoted['gross_r_median']} net={unquoted['net_r_median']}")
check("a name with no net numbers is not called economic",
      unquoted["verdict"] == ist.INSUFFICIENT, unquoted["verdict"])
check("no study output is a production block", st["research_only"])

# ---------------------------------------------------------------------------
# §12 futures shadow outcomes
# ---------------------------------------------------------------------------
print("\n--- §12 futures shadow outcomes ---")
from app.analysis import futures_outcomes as fo  # noqa: E402
from app.models import FuturesSignalCard  # noqa: E402

_fo_dir = tempfile.mkdtemp(prefix="qt_fut_outcomes_")
_orig_data_dir = settings.data_dir
settings.data_dir = _fo_dir


def fut_card(direction="LONG", entry=24000.0, stop=23950.0, t1=24050.0,
             t2=24100.0, t3=24150.0, status=fo.VALID, spread=1.0, cost=2.0):
    return FuturesSignalCard(
        status=status, signal="BUY" if direction == "LONG" else "SELL",
        direction=direction, instrument="NIFTY", contract="NIFTY25SEPFUT",
        expiry="2026-09-24", days_to_expiry=30, lot_size=65,
        price=entry, entry=entry, stop=stop, target1=t1, target2=t2, target3=t3,
        risk_points=abs(entry - stop), spread_points=spread, cost_points=cost,
        futures_signal_id="fut-1", episode_id="NIFTY-2026-08-25-FUT",
        signal_score=72.0, setup_type="BREAKOUT", atr=60.0,
    )


t0 = time.time()
fo.reset_for_tests()
fo.observe("NIFTY", fut_card(), 24000.0, t0)
check("a valid futures plan opens exactly one shadow follow",
      fo.summarise(fo.read_outcomes())["open_follows"] == 1)
fo.observe("NIFTY", fut_card(), 24010.0, t0 + 60)
fo.observe("NIFTY", fut_card(), 24060.0, t0 + 120)   # through T1
fo.observe("NIFTY", fut_card(), 24160.0, t0 + 180)   # through T3 -> resolved
rows = fo.read_outcomes()
check("a plan that runs to T3 resolves once, as a target-before-stop",
      len(rows) == 1 and rows[0]["outcome"] == "T3"
      and rows[0]["target_before_stop"] is True, str(len(rows)))
r0 = rows[0]
check("the outcome is denominated in index points, never in premium",
      r0["unit"] == "INDEX_POINTS" and r0["vehicle"] == "FUTURES")
check("entry, stop, all three targets and the risk are recorded",
      r0["entry"] == 24000.0 and r0["stop"] == 23950.0
      and set(r0["targets"]) == {"T1", "T2", "T3"} and r0["risk_points"] == 50.0)
check("MFE, MAE, time-to-target and time-to-resolution are recorded",
      r0["mfe_points"] == 160.0 and r0["mae_points"] == 0.0
      and r0["minutes_to_target"] == 2.0 and r0["minutes_to_resolution"] == 3.0,
      f"mfe={r0['mfe_points']} t={r0['minutes_to_target']}")
check("gross R and net R are separate, and net charges spread plus costs",
      r0["gross_r"] == 3.2 and r0["net_r"] == round(3.2 - 3.0 / 50.0, 3),
      f"{r0['gross_r']} / {r0['net_r']}")
check("no futures outcome is executable and none has an order path",
      r0["research_only"] and r0["executable"] is False)

fo.reset_for_tests()
fo.observe("NIFTY", fut_card(direction="SHORT", entry=24000.0, stop=24050.0,
                              t1=23950.0, t2=23900.0, t3=23850.0), 24000.0, t0)
fo.observe("NIFTY", fut_card(direction="SHORT", entry=24000.0, stop=24050.0,
                              t1=23950.0, t2=23900.0, t3=23850.0), 24060.0, t0 + 60)
short = fo.read_outcomes()[-1]
check("a SHORT is measured with a falling price as favourable and stops above",
      short["direction"] == "SHORT" and short["outcome"] == "STOP"
      and short["order"] == fo.STOP_FIRST and short["gross_r"] < 0,
      f"{short['outcome']} {short['gross_r']}")
check("time-to-stop is recorded for a stopped plan",
      short["minutes_to_stop"] == 1.0, str(short["minutes_to_stop"]))

fo.reset_for_tests()
fo.observe("NIFTY", fut_card(status="INVALID_FUTURES_PLAN"), 24000.0, t0)
check("an invalid futures plan is not followed at all",
      fo.summarise(fo.read_outcomes())["open_follows"] == 0)
fo.observe("NIFTY", fut_card(stop=24000.0), 24000.0, t0)
check("a plan whose stop is its entry is refused, so no 1e8 R can appear",
      fo.summarise(fo.read_outcomes())["open_follows"] == 0)

fo.reset_for_tests()
fo.observe("NIFTY", fut_card(), 24000.0, t0)
fo.observe("NIFTY", fut_card(t1=24055.0), 24000.0, t0 + 30)  # restated plan
opened = fo.summarise(fo.read_outcomes())
check("a restated plan while one is followed is counted, not opened again",
      opened["open_follows"] == 1)
fo.observe("NIFTY", fut_card(), 24000.0,
           t0 + settings.futures_shadow_follow_minutes * 60 + 1)
timeout = fo.read_outcomes()[-1]
check("an unresolved plan times out at the follow window with NEITHER",
      timeout["outcome"] == fo.TIMEOUT and timeout["order"] == fo.NEITHER
      and timeout["restatements"] == 1, timeout["outcome"])

fo.reset_for_tests()
nocost = fut_card()
nocost.cost_points = None
fo.observe("NIFTY", nocost, 24000.0, t0)
fo.observe("NIFTY", nocost, 24160.0, t0 + 60)
uncosted = fo.read_outcomes()[-1]
check("a plan with no cost model has no net R rather than the gross relabelled",
      uncosted["gross_r"] == 3.2 and uncosted["net_r"] is None)

summary = fo.summarise(fo.read_outcomes())
check("the summary reports its own sample and refuses comparison below it",
      summary["comparison_ready"] is False
      and summary["comparison_status"] == "INSUFFICIENT_SAMPLE"
      and summary["shortfall"] == fo.MIN_COMPARISON_SAMPLE - summary["net_r_samples"],
      f"{summary['comparison_status']} shortfall={summary['shortfall']}")
check("costed coverage is reported, so an uncosted row cannot hide",
      summary["costed_coverage_pct"] is not None
      and summary["costed_coverage_pct"] < 100.0,
      str(summary["costed_coverage_pct"]))
check("target-before-stop share and per-instrument rows are reported",
      summary["target_before_stop_pct"] is not None
      and "NIFTY" in summary["by_instrument"])

settings.data_dir = _orig_data_dir

# ---------------------------------------------------------------------------
# §5 vehicle comparison
# ---------------------------------------------------------------------------
print("\n--- §5 option vs futures vs no-trade, same idea ---")
from app.analysis import vehicle_comparison as vc  # noqa: E402


def opt_res(mid="IDEA-1", instrument="NIFTY", direction="BULLISH", r=1.0,
            ts=1_787_628_600, order="TARGET_FIRST", spread=0.9,
            status="TRADABLE"):
    return {
        "event": "RESOLVED", "market_signal_id": mid, "instrument": instrument,
        "symbol": f"{instrument}25AUG2624000CE", "session": SESSION,
        "signal_ts": ts, "signal_time_ist": lc._ist(ts), "direction": direction,
        "family": "INDEX", "vehicle": "OPTIONS", "unit": "PREMIUM",
        "entry": 100.0, "stop": 80.0, "targets": {"T1": 130.0},
        "realized_r": r, "mfe_r": max(r, 0.5), "mae_r": -0.4, "order": order,
        "outcome": "T1" if order == "TARGET_FIRST" else "STOP",
        "minutes_to_resolution": 12.0,
        "vehicle_selection": {"vehicle_alternatives": [
            {"vehicle": "OPTIONS", "spread": spread,
             "spread_pct_of_premium": spread, "expected_room_r": 1.5,
             "tradability_status": status}]},
    }


def fut_res(mid="IDEA-1", instrument="NIFTY", direction="LONG", gross=1.0,
            net=None, ts=1_787_628_600, tbs=True):
    return {
        "event": "RESOLVED", "market_signal_id": mid, "instrument": instrument,
        "session": SESSION, "signal_ts": ts, "signal_time_ist": lc._ist(ts),
        "direction": direction, "direction_of_idea": (
            "BULLISH" if direction == "LONG" else "BEARISH"),
        "vehicle": "FUTURES", "unit": "INDEX_POINTS",
        "contract": f"{instrument}25SEPFUT", "entry": 24000.0, "stop": 23950.0,
        "targets": {"T1": 24050.0}, "spread_points": 1.0, "rolled": True,
        "days_to_expiry": 30, "gross_r": gross,
        "net_r": gross if net is None else net,
        "mfe_r": max(gross, 0.5), "mae_r": -0.3, "target_before_stop": tbs,
        "outcome": "T1" if tbs else "STOP", "minutes_to_resolution": 9.0,
    }


p = vc.pair([opt_res(r=0.2)], [fut_res(gross=1.8)])[0]
check("both vehicles on one idea are paired by market_signal_id",
      len(vc.pair([opt_res()], [fut_res()])) == 1
      and p["matched_on"] == "market_signal_id")
check("the better vehicle on the same idea is named, not assumed",
      p["verdict"] == vc.FUTURES_BETTER and p["winner"] == "FUTURES",
      p["verdict"])
check("each leg keeps its own unit, so premium and points are never summed",
      p["option"]["unit"] == "PREMIUM"
      and p["futures"]["unit"] == "INDEX_POINTS")
check("entry, stop, targets, spread, MFE, MAE and target-before-stop are recorded"
      " for both legs",
      all(k in p["option"] for k in ("entry", "stop", "targets", "spread",
                                     "mfe_r", "mae_r", "target_before_stop"))
      and all(k in p["futures"] for k in ("entry", "stop", "targets", "spread",
                                          "mfe_r", "mae_r",
                                          "target_before_stop")))
check("no-trade is carried as a real third option at zero",
      p["no_trade"]["r"] == 0.0)

check("a pair inside the material margin is BOTH_VALID, not a winner",
      vc.pair([opt_res(r=1.0)], [fut_res(gross=1.1)])[0]["verdict"]
      == vc.BOTH_VALID)
both_bad = vc.pair([opt_res(r=-1.0, order="STOP")],
                   [fut_res(gross=-0.8, tbs=False)])[0]
check("when both vehicles lose, no-trade wins and it is called BOTH_BAD",
      both_bad["verdict"] == vc.BOTH_BAD and both_bad["winner"] == "NO_TRADE")
check("an option-only idea is UNKNOWN, never scored as a win for options",
      vc.pair([opt_res(r=2.0)], [])[0]["verdict"] == vc.UNKNOWN)
check("a futures-only idea is kept as evidence but stays UNKNOWN",
      vc.pair([], [fut_res(gross=2.0)])[0]["verdict"] == vc.UNKNOWN)
check("an unrelated trade in the same name is not paired as a comparison",
      vc.pair([opt_res(mid="IDEA-A", ts=1_787_628_600)],
              [fut_res(mid="IDEA-B", ts=1_787_628_600 + 4 * 3600)]
              )[0]["matched_on"] is None)
check("a same-name same-direction pair inside the window falls back to a match",
      vc.pair([opt_res(mid="IDEA-A")],
              [fut_res(mid="IDEA-B", ts=1_787_628_600 + 60)]
              )[0]["matched_on"] == "instrument_direction_window")
check("opposite directions in the same name are never paired",
      vc.pair([opt_res(mid="IDEA-A", direction="BULLISH")],
              [fut_res(mid="IDEA-B", direction="SHORT")]
              )[0]["matched_on"] is None)

vsum = vc.summarise(vc.pair([opt_res(r=0.2)], [fut_res(gross=1.8)]))
check("the comparison refuses a recommendation below its sample",
      vsum["sample_sufficient"] is False
      and vsum["answer"] == "INSUFFICIENT_SAMPLE"
      and vsum["recommendation"] is None,
      vsum["answer"])
check("the shortfall to a usable comparison is stated",
      vsum["shortfall"] == vc.MIN_SAMPLE - 1, str(vsum["shortfall"]))
check("verdicts are counted per family, so index and stock are not pooled",
      "INDEX" in vsum["by_family"])
check("a pair decided on gross records that it was not costed",
      vc.pair([opt_res()], [fut_res()])[0]["basis"]["option_costed"] is False)
check("no vehicle comparison output is a production selector",
      vsum["research_only"])

# ---------------------------------------------------------------------------
# §14 journal: market idea vs vehicle selection vs execution
# ---------------------------------------------------------------------------
print("\n--- §14 journal separates idea, vehicle and execution ---")
from app.analysis import signal_journal as sj  # noqa: E402

sel = sj._vehicle_selection(
    "NIFTY",
    Decision(market="OPTIONS", signal=Signal.BUY, market_signal=Signal.BUY,
             confidence=80.0, signal_strength=70.0, trade_quality="GOOD",
             vehicle="CE", recommended_option="NIFTY25AUG2624000CE",
             current_premium=120.0, stop_loss=100.0, target1=150.0,
             target2=170.0, target3=190.0, trade_score=71.0,
             spot_price=24000.0),
    {"status": "TRADABLE", "spread": 1.2, "spread_pct_of_premium": 1.0,
     "spread_over_risk": 0.06, "expected_room_r": 1.5},
    time.time())
check("the market idea is recorded as a direction on the underlying",
      sel["market_idea"]["direction"] == "BULLISH"
      and sel["market_idea"]["underlying"] == "NIFTY")
check("the vehicle actually selected is named and reason-coded",
      sel["selected_vehicle"] == "OPTIONS"
      and sel["selection_reason"] == "PRODUCTION_TRADES_OPTIONS_ONLY")
check("option, futures and no-trade are all recorded as alternatives",
      [a["vehicle"] for a in sel["vehicle_alternatives"]]
      == ["OPTIONS", "FUTURES", "NO_TRADE"])
check("the option alternative carries spread, room and tradability",
      sel["vehicle_alternatives"][0]["spread"] == 1.2
      and sel["vehicle_alternatives"][0]["tradability_status"] == "TRADABLE")
check("the futures alternative is never executable",
      sel["vehicle_alternatives"][1]["executable"] is False)
check("recording the alternatives does not select between them",
      sel["research_only"] and sel["selected_vehicle"] == "OPTIONS")
no_leg = sj._vehicle_selection(
    "RELIANCE",
    Decision(market="OPTIONS", signal=Signal.WAIT, confidence=40.0,
             signal_strength=30.0, trade_quality="WEAK", vehicle=None,
             trade_score=40.0, spot_price=1400.0),
    None, time.time())
check("an idea with no takeable leg selects NO_TRADE, not a phantom option",
      no_leg["selected_vehicle"] == "NO_TRADE"
      and no_leg["selection_reason"] == "NO_TAKEABLE_LEG")

# ---------------------------------------------------------------------------
# §13 A+ shadow classifier
# ---------------------------------------------------------------------------
print("\n--- §13 A+ shadow: a composite label, not a fitted model ---")
from app.analysis import a_plus_shadow as ap  # noqa: E402
from app.analysis import tradability  # noqa: E402

ap.reset_for_tests()


def trad_row(instrument="NIFTY", premium=100.0, bid=99.6, ask=100.4,
             risk=20.0, target=140.0, volume=5000, oi=20000, age=2.0,
             lot=75):
    return tradability.assess(instrument, premium=premium, bid=bid, ask=ask,
                              risk_points=risk, first_target=target,
                              volume=volume, open_interest=oi,
                              quote_age_sec=age, lot_size=lot)


good = ap.classify("NIFTY", tradability_row=trad_row(), score=72.0,
                   plan_actionable=True, delta=0.52)
check("a clean index call with room, a tight book and a real leg is A_PLUS",
      good["label"] == ap.A_PLUS, f"{good['label']} {good['reasons']}")
check("every one of the seven inputs is checked, not a subset",
      set(good["checks"]) == {"data", "spread", "vehicle", "entry", "room"}
      and good["family"] == "INDEX")
check("the label carries the thresholds that produced it",
      good["thresholds"]["min_room_r"] == settings.a_plus_min_room_r)
check("no A+ output is executable or a production gate",
      good["research_only"] and good["executable"] is False)

check("a call with no two-sided quote is REJECT_DATA, never an assumed pass",
      ap.classify("NIFTY", tradability_row=trad_row(bid=None, ask=None),
                  score=72.0, plan_actionable=True, delta=0.5)["label"]
      == ap.REJECT_DATA)
check("a stale quote is not evidence of a current spread, so it is REJECT_DATA",
      ap.classify("NIFTY", tradability_row=trad_row(age=600.0), score=72.0,
                  plan_actionable=True, delta=0.5)["label"] == ap.REJECT_DATA)
check("a non-positive risk is REJECT_DATA, not a divide by zero",
      ap.classify("NIFTY", tradability_row=trad_row(risk=0.0), score=72.0,
                  plan_actionable=True, delta=0.5)["label"] == ap.REJECT_DATA)
wide = ap.classify("NIFTY", tradability_row=trad_row(bid=97.0, ask=103.0),
                   score=72.0, plan_actionable=True, delta=0.5)
check("a call whose spread is wide against premium is REJECT_SPREAD",
      wide["label"] == ap.REJECT_SPREAD, str(wide["reasons"]))
check("a spread large against the plan's own risk is REJECT_SPREAD",
      ap.SPREAD_RISK in wide["reasons"])
thin = ap.classify("NIFTY", tradability_row=trad_row(volume=5, oi=10),
                   score=72.0, plan_actionable=True, delta=0.5)
check("a thin book is a vehicle problem, not a market one",
      thin["label"] == ap.REJECT_VEHICLE and ap.THIN in thin["reasons"])
check("a low-delta leg that will not track the underlying is REJECT_VEHICLE",
      ap.classify("NIFTY", tradability_row=trad_row(), score=72.0,
                  plan_actionable=True, delta=0.08)["label"]
      == ap.REJECT_VEHICLE)
stock = ap.classify("RELIANCE", tradability_row=trad_row("RELIANCE", lot=250),
                    score=72.0, plan_actionable=True, delta=0.5)
check("single stocks stay research-only rather than becoming A+ candidates",
      stock["label"] == ap.REJECT_VEHICLE
      and ap.FAMILY_RESEARCH in stock["reasons"])
check("a score below the A+ seed is REJECT_ENTRY, and the gates are untouched",
      ap.classify("NIFTY", tradability_row=trad_row(), score=40.0,
                  plan_actionable=True, delta=0.5)["label"] == ap.REJECT_ENTRY)
check("a plan the board itself called unactionable is REJECT_ENTRY",
      ap.classify("NIFTY", tradability_row=trad_row(), score=72.0,
                  plan_actionable=False, delta=0.5)["label"] == ap.REJECT_ENTRY)
check("an idea with no leg to express it is REJECT_ENTRY, not A+",
      ap.classify("NIFTY", tradability_row=trad_row(), score=72.0,
                  plan_actionable=True, has_leg=False,
                  delta=0.5)["label"] == ap.REJECT_ENTRY)
check("a target too close to cover its own spread is REJECT_ROOM",
      ap.classify("NIFTY", tradability_row=trad_row(target=105.0), score=72.0,
                  plan_actionable=True, delta=0.5)["label"] == ap.REJECT_ROOM)
check("a call with no room measurable at all is REJECT_ROOM, not A+",
      ap.classify("NIFTY", tradability_row=trad_row(target=None), score=72.0,
                  plan_actionable=True, delta=0.5)["label"] == ap.REJECT_ROOM)
check("a call with no tradability evidence is UNKNOWN and never A+",
      ap.classify("NIFTY", tradability_row=None, score=99.0)["label"]
      == ap.UNKNOWN)
check("the first failure names the label, so the reason points at a fact",
      ap.classify("NIFTY", tradability_row=trad_row(bid=None, ask=None),
                  score=10.0, plan_actionable=False,
                  delta=0.01)["label"] == ap.REJECT_DATA)
check("every label is one of the seven §13 outputs",
      all(ap.classify("NIFTY", tradability_row=t, score=s,
                      plan_actionable=True, delta=d)["label"] in ap.LABELS
          for t, s, d in ((trad_row(), 72.0, 0.5),
                          (trad_row(bid=None, ask=None), 72.0, 0.5),
                          (trad_row(target=105.0), 72.0, 0.5))))

ap.reset_for_tests()
for _ in range(3):
    ap.record(good, "2026-08-25")
ap.record(stock, "2026-08-25")
asum = ap.summarise()
check("the label distribution is reported per family, never pooled",
      asum["by_family"]["INDEX"]["a_plus"] == 3
      and asum["by_family"]["STOCK"]["a_plus"] == 0)
check("A+ candidates are the names the label actually appeared for",
      asum["a_plus_candidates"] == ["NIFTY"]
      and "RELIANCE" in asum["research_only_instruments"])
check("the reasons behind the rejections are counted, not just the labels",
      asum["reason_counts"].get(ap.FAMILY_RESEARCH) == 1)
check("§15 sufficiency is stated honestly against 20 sessions",
      asum["sessions_observed"] == 1
      and asum["sessions_shortfall"] == ap.MIN_SESSIONS - 1
      and asum["validation_ready"] is False
      and asum["answer"] == "INSUFFICIENT_SAMPLE")
check("the chronological holdout requirement is carried in the report",
      asum["holdout_required"] == ap.MIN_HOLDOUT_SESSIONS == 5)
check("the whole A+ summary is marked research only",
      asum["research_only"])
ap.reset_for_tests()

# ---------------------------------------------------------------------------
# §16 artefacts and the ten questions
# ---------------------------------------------------------------------------
print("\n--- §16 eight artefacts, ten questions, honest §15 ---")
from app.analysis import phase12_report as p12  # noqa: E402

_p16 = tempfile.mkdtemp(prefix="qt_p12_report_")
settings.data_dir = _p16
lc.reset_for_tests()
built = p12.build()
qs = built["questions"]
check("all ten §16 questions are answered, in order",
      len(qs) == 10 and qs[0]["question"].startswith("1.")
      and qs[9]["question"].startswith("10."))
check("every answer carries the evidence it was derived from",
      all(isinstance(q["evidence"], dict) and q["evidence"] for q in qs))
q7, q10 = qs[6], qs[9]
check("q7 (futures vs options) refuses a verdict on this sample",
      q7["sample_sufficient"] is False and q7["answer"] == "INSUFFICIENT_SAMPLE")
check("q7 states the futures sample shortfall as a number",
      isinstance(q7["shortfall"], dict)
      and q7["shortfall"]["resolved_futures_short_of_comparison"]
      == fo.MIN_COMPARISON_SAMPLE)
check("q10 (begin production A+ validation) answers no, with the shortfall",
      q10["sample_sufficient"] is False
      and q10["stated_answer"] is False
      and q10["shortfall"]["sessions"] > 0)
check("q10 names the §15 requirement it is short of",
      q10["evidence"]["sessions_required"] == 20
      and q10["evidence"]["holdout_required"] == 5)
check("q5 does not print a single directional accuracy figure",
      built["direction_attribution"]["single_number_withheld"] is True)
check("the report states production behaviour is unchanged",
      "gate thresholds" in built["production_unchanged"]
      and "broker execution" in built["production_unchanged"]
      and built["scope"] == "RESEARCH_SHADOW_PAPER_ONLY")
check("nothing in the report is a promotion",
      built["validation"]["in_sample_promotion"] is False
      and built["research_only"])

written = p12.write_all()
check("all eight §16 artefacts are written",
      written["complete"] and not written["artefacts_missing"],
      str(written["artefacts_missing"]))
check("each artefact exists on disk and is non-empty",
      all(os.path.getsize(os.path.join(_p16, name)) > 0
          for name in p12.ARTEFACTS))
md = open(os.path.join(_p16, p12.REPORT_MD), encoding="utf-8").read()
check("the markdown report carries the ten questions",
      all(f"{i}." in md for i in range(1, 11)))
check("the markdown says INSUFFICIENT_SAMPLE where the data is short",
      "INSUFFICIENT_SAMPLE" in md)
check("the markdown states the §15 session shortfall rather than implying none",
      "of 20 required" in md)
loaded = json.loads(open(os.path.join(_p16, p12.REPORT_JSON),
                        encoding="utf-8").read())
check("the json report round-trips with its questions intact",
      len(loaded["questions"]) == 10)
settings.data_dir = _orig_data_dir

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
    raise SystemExit(1)
print("PHASE 12 SMOKE PASSED (§1 churn, §2 reconciliation, §3/§4 family+tradability, §6 direction, §7 failure, §8-§10 studies, §11 rollover, §12 futures outcomes, §5 vehicle comparison, §13 A+ shadow, §14 journal, §16 report)")
