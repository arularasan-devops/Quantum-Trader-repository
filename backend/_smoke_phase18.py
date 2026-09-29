#!/usr/bin/env python
"""Phase 18 smoke — CAS capture, paper book, reports, and the safety barrier.

Runs a synthetic CAS session through the live entry point and asserts the
properties that make the phase honest rather than merely working:

* a fill is never invented — a missing or stale book yields UNKNOWN, not a mid;
* entry is the ask and exit is the bid, so a spread cannot be earned;
* an empty dataset answers REQUIRES_MORE_DATA rather than producing numbers;
* nothing before the auction existed is counted as CAS evidence;
* and the package contains no route to an order, checked by reading the source.

    .venv/bin/python _smoke_phase18.py
"""
from __future__ import annotations

import datetime as dt
import os
import tempfile
import time

# Before app.config is imported: settings reads the environment with an "QT_"
# prefix, and without this the smoke's synthetic CAS rows are appended to the
# REAL phase18_cas_*.jsonl, putting fixture legs into the evidence that the CAS
# promotion gate counts.
os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="p18smoke_"))

from app.config import settings  # noqa: E402
from app.models import (  # noqa: E402
    Candle,
    IndicatorSnapshot,
    OptionQuote,
    OptionType,
)
from app.research.phase18 import (  # noqa: E402
    artefacts,
    cas_cache,
    direction,
    execution,
    expectation,
    experiments,
    journal,
    moves,
    overnight,
    paper,
    quality,
    reports,
    safety,
    schema,
    score,
    service,
    session,
    signal as cas_signal,
    store,
    validation,
)

CHECKS = 0
FAILED: list[str] = []


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILED.append(label)


def ts_at(hh: int, mm: int, ss: int = 0, *, day: str = "2025-09-25") -> float:
    """A UTC epoch for a given IST wall clock on a given session date."""
    d = dt.date.fromisoformat(day)
    naive = dt.datetime(d.year, d.month, d.day, hh, mm, ss)
    return (naive - dt.timedelta(hours=5, minutes=30)).replace(
        tzinfo=dt.timezone.utc).timestamp()


def chain(spot: float, *, step: float = 50.0, spread: float = 0.5,
          bump: float = 0.0, book: bool = True) -> list[OptionQuote]:
    """A synthetic NIFTY chain with a real two-sided book around ``spot``."""
    atm = round(spot / step) * step
    out: list[OptionQuote] = []
    for n in range(-12, 13):
        strike = atm + n * step
        for side in (OptionType.CALL, OptionType.PUT):
            dist = abs(strike - spot)
            intrinsic = max(0.0, (spot - strike) if side == OptionType.CALL
                            else (strike - spot))
            prem = max(0.5, intrinsic + max(1.0, 60.0 - dist / 12.0) + bump)
            out.append(OptionQuote(
                symbol=f"NIFTY{int(strike)}{side.value}",
                strike=float(strike), option_type=side, premium=round(prem, 2),
                iv=14.0, delta=0.45 if dist < step else 0.2, gamma=0.002,
                theta=-8.0, vega=3.0, oi=250_000, oi_change=1_000, volume=90_000,
                bid=round(prem - spread / 2, 2) if book else None,
                ask=round(prem + spread / 2, 2) if book else None,
            ))
    return out


def candles(spot: float, n: int = 40) -> list[Candle]:
    out = []
    base = int(ts_at(14, 30))
    for i in range(n):
        px = spot - (n - i) * 2.0
        out.append(Candle(time=base + i * 60, open=px, high=px + 3,
                          low=px - 3, close=px + 1.5, volume=1000))
    return out


def ind(atr: float = 40.0) -> IndicatorSnapshot:
    return IndicatorSnapshot(atr=atr, rsi=62.0, macd_hist=1.4, trend="UP",
                             momentum=0.6)


# ------------------------------------------------------------------ session
def test_session() -> None:
    check(session.state(ts_at(15, 12)) == session.CAS_PREP, "15:12 is CAS_PREP")
    check(session.state(ts_at(15, 17)) == session.CAS_OPEN, "15:17 is CAS_OPEN")
    check(session.state(ts_at(15, 22)) == session.CAS_ACTIVE, "15:22 is CAS_ACTIVE")
    check(session.state(ts_at(15, 27)) == session.CAS_CLOSE, "15:27 is CAS_CLOSE")
    check(session.state(ts_at(15, 35)) == session.POST_CAS, "15:35 is POST_CAS")
    check(session.state(ts_at(11, 0)) == session.OUTSIDE, "11:00 is OUTSIDE")
    check(not session.in_window(ts_at(15, 9, 59)), "15:09:59 is outside the window")
    check(session.in_window(ts_at(15, 10)), "15:10 opens the window")
    check(not session.in_window(ts_at(15, 30, 1)), "after 15:30 is outside")
    check(session.mechanism("NIFTY") == session.AUCTION_DERIVED,
          "an index is moved by its constituents' auctions, not auctioned itself")
    check(session.mechanism("CRUDEOIL") == session.AUCTION_NONE,
          "MCX has no CAS mechanism")
    check(session.sub_window(ts_at(15, 22)) == "W_1520_1525", "sub-window split")
    check(session.nearest_mark(ts_at(15, 20, 3)) == "T1520", "mark snapping")
    check(session.nearest_mark(ts_at(15, 22, 30)) is None,
          "a sample far from a mark is not snapped to one")
    rem = session.remaining_seconds(ts_at(15, 20))
    check(rem is not None and abs(rem - 600) < 1.0, "ten minutes left at 15:20")
    check(session.expiry_class(0) == schema.EXPIRY_DAY, "DTE 0 is expiry day")
    check(session.expiry_class(1) == schema.PRE_EXPIRY, "DTE 1 is pre-expiry")


# ------------------------------------------------------------------ quality
def test_quality() -> None:
    check(quality.classify_delay(200) == quality.EXACT, "200ms is EXACT")
    check(quality.classify_delay(2_500) == quality.NEAR_EXACT, "2.5s is NEAR_EXACT")
    check(quality.classify_delay(60_000) == quality.STALE, "60s is STALE")
    check(quality.classify_delay(None) == quality.MISSING, "no delay is MISSING")
    check(not quality.two_sided(None, 10.0), "a one-sided book is not two-sided")
    check(not quality.two_sided(10.0, 9.0), "a crossed book is rejected")
    g = quality.gate([quality.EXACT] * 89 + [quality.STALE] * 11)
    check(not g["meets_target"], "89% fails the 90% gate")
    g2 = quality.gate([quality.EXACT] * 95 + [quality.STALE] * 5)
    check(g2["meets_target"], "95% clears the gate")


# ---------------------------------------------------------------- execution
def test_execution() -> None:
    q = {"bid": 10.0, "ask": 11.0, "premium": 10.5, "spread": 1.0,
         "data_quality": quality.EXACT}
    entry = execution.entry_fill(q)
    check(entry["price"] == 11.0, "entry is filled at the ask, never the mid")
    ex = execution.exit_fill(q)
    check(ex["price"] == 10.0, "exit is filled at the bid, never the mid")

    stale = {"bid": 10.0, "ask": 11.0, "data_quality": quality.STALE}
    check(execution.entry_fill(stale)["executability"] == schema.EXECUTABILITY_UNKNOWN,
          "a stale book cannot be filled")
    check(execution.entry_fill(None)["executability"] == schema.EXECUTABILITY_UNKNOWN,
          "a missing book cannot be filled")
    nobook = {"premium": 10.5, "data_quality": quality.EXACT}
    check(execution.entry_fill(nobook)["executability"] == schema.EXECUTABILITY_UNKNOWN,
          "a last-traded price is not a fill")

    flat = execution.settle(instrument="NIFTY", entry_quote=q, exit_quote=q)
    check(flat["net_rupees"] is not None and flat["net_rupees"] < 0,
          "a round trip at an unchanged book must lose the spread and the costs")
    table = execution.slippage_table(instrument="NIFTY", entry_quote=q, exit_quote=q)
    nets = [table["grid"][f"{k}_tick"]["net_rupees"] for k in (0, 1, 2, 3)]
    check(all(nets[i] > nets[i + 1] for i in range(3)),
          "more slippage must never improve the result")


# ---------------------------------------------------------------- direction
def test_direction() -> None:
    up = direction.classify(candles=candles(24_000), ind=ind(), spot=24_050,
                            window_prices=[24_000, 24_020, 24_050], atr=40.0,
                            htf_trend="UP")
    check(up["direction"] in schema.DIRECTIONS, "direction is from the fixed set")
    check(up["causal"], "the direction engine reports itself as causal")
    flat = direction.classify(window_prices=[24_000, 24_000], atr=40.0)
    check(flat["direction"] in (schema.CAS_NEUTRAL, schema.CAS_UNCLEAR),
          "no information yields NEUTRAL or UNCLEAR, never a guess")
    check(direction.side_for(schema.CAS_BULLISH) == schema.CE, "bullish buys CE")
    check(direction.side_for(schema.CAS_NEUTRAL) is None, "neutral buys nothing")


# -------------------------------------------------------------------- score
def test_score() -> None:
    s = score.score(direction=schema.CAS_BULLISH, direction_votes=(3, 0),
                    spread_pct=2.0, oi=200_000, volume=50_000, premium=40.0,
                    data_quality=quality.EXACT, rung=schema.OTM_1,
                    remaining_sec=600, expiry_class=schema.EXPIRY_DAY)
    check(0 <= s["score"] <= s["max"], "score is within its own range")
    check(s["is_probability"] is False, "the score is never presented as a chance")
    bad = score.score(direction=schema.CAS_UNCLEAR, spread_pct=45.0,
                      data_quality=quality.STALE)
    check(bad["score"] < s["score"], "an unfillable, unclear setup ranks lower")
    check(bad["capped_by_data_quality"], "a non-fillable quote caps the score")


# ------------------------------------------------------------- expectation
def test_expectation() -> None:
    block = expectation.card_block(
        instrument="NIFTY", underlying=24_000.0, atr=120.0, premium=40.0,
        delta=0.35, gamma=0.002, spread=0.6, per_session=None, paper_rows=None,
    )
    for field in ("current_underlying", "cas_expected_move_points",
                  "cas_move_range", "move_pct", "move_atr",
                  "current_option_premium", "expected_option_move_points",
                  "t1", "t2", "t3", "estimated_seconds_to_t1"):
        check(field in block, f"card carries {field}")
    check(block["expectation_basis"] == expectation.ATR_PRIOR,
          "with no CAS history the expected move is an ATR prior, and says so")
    check(block["estimated_seconds_to_t1"] is None,
          "time to T1 stays blank until resolved legs have actually reached T1")
    check(block["expectation_warning"], "the prior carries a visible warning")
    t = expectation.targets(entry=40.0, expected_option_move_points=20.0,
                            spread=0.6)
    check(t["t1"] < t["t2"] < t["t3"], "targets are ordered")


# ------------------------------------------------------------------- signal
def test_signal() -> None:
    bare = {"instrument": "NIFTY", "session": "2025-09-25", "underlying": 24_000.0,
            "ladder": [], "data_quality": quality.MISSING}
    card = cas_signal.build_card(bare, atr=120.0)
    check(card["signal"] == schema.CAS_NO_TRADE,
          "no ladder means no trade, not a guess")
    check(card["cas_expected_move_points"] is not None,
          "the underlying half of the card is shown even while waiting")
    check(card["paper_only"] and card["banner"] == "CAS PAPER ONLY",
          "a refusing card is still marked paper-only")

    wide = dict(bare, ladder=[{
        "rung": schema.OTM_1,
        "ce": {"bid": 10.0, "ask": 18.0, "premium": 14.0, "spread": 8.0,
               "spread_pct": 57.0, "data_quality": quality.EXACT,
               "delta": 0.3, "strike": 24_050.0},
        "pe": None,
    }])
    det = {"direction": schema.CAS_BULLISH, "bullish_votes": 3,
           "bearish_votes": 0, "components": {}}
    card2 = cas_signal.build_card(wide, direction_detail=det, atr=120.0)
    check(card2["signal"] == schema.CAS_NO_TRADE,
          "a 57% spread is refused however bullish the read")


# -------------------------------------------------------------------- paper
def test_paper() -> None:
    q = {"bid": 20.0, "ask": 21.0, "premium": 20.5, "spread": 1.0,
         "data_quality": quality.EXACT}
    leg = paper.Leg(
        episode_id="t1", session="2025-09-25", instrument="NIFTY",
        strategy_variant="B", option_type=schema.CE, rung=schema.OTM_1,
        strike=24_050.0, symbol="NIFTY24050CE", expiry="2025-09-25",
        days_to_expiry=0, expiry_class=schema.EXPIRY_DAY,
        signal_ts=ts_at(15, 15), entry_ts=ts_at(15, 15), entry_quote=q,
        entry_price=21.0, stop=10.5, t1=31.5, t2=42.0, t3=63.0,
        status=schema.HOLD, data_quality=quality.EXACT,
    )
    up = dict(q, bid=35.0, ask=36.0, premium=35.5)
    paper.update(leg, up, ts_at(15, 20))
    check(leg.peak_bid == 35.0, "the path is tracked on the bid")
    check(any("T1" in m for m in leg.milestones), "T1 milestone recorded")
    paper.close(leg, up, ts_at(15, 25), status=schema.EXIT)
    row = paper.settle_leg(leg)
    check(row["executability"] == schema.EXECUTABLE, "a real book settles")
    check(row["net_rupees"] < row["theoretical_rupees"],
          "net must be below the marked value once the book and costs are paid")
    check(row["mfe_points"] == 14.0, "MFE measured from the entry ask to peak bid")
    ok, _why = paper.should_enter("E", ts=ts_at(15, 15), window_prices=[1.0],
                                  atr=40.0, premium_series=[20.0], spread_pct=40.0)
    check(not ok, "strategy E refuses a 40% spread")


# ----------------------------------------------------------------- overnight
def test_overnight() -> None:
    g = overnight.classify_gap(24_000.0, 24_100.0)
    check(g["gap_direction"] == schema.GAP_UP, "a 0.4% gap up is UP")
    check(overnight.classify_gap(24_000.0, 24_010.0)["gap_direction"]
          == schema.GAP_FLAT, "a 0.04% gap is inside the flat band")
    res = overnight.resolve(
        leg_row={"episode_id": "x", "instrument": "NIFTY", "session": "2025-09-25",
                 "entry_price": 21.0, "entry_quote": {"bid": 20.0, "ask": 21.0,
                                                      "data_quality": quality.EXACT},
                 "t1": 31.5, "stop": 10.5},
        next_session="2025-09-26", next_open_underlying=24_100.0,
        prev_close_underlying=24_000.0, opening_quotes=[], lots=1,
    )
    check(res["status"] == schema.EXECUTABILITY_UNKNOWN,
          "no opening book leaves the leg unresolved rather than scored")
    check(overnight.summarise([])["verdict"] == schema.REQUIRES_MORE_DATA,
          "no carried legs cannot answer whether carrying helps")


# ---------------------------------------------------------------- validation
def test_validation() -> None:
    check(validation.regime_of("2025-07-31") == schema.PRE_CAS_REGIME,
          "July 2025 predates the auction and is not CAS evidence")
    check(validation.regime_of("2025-08-04") == schema.CAS_REGIME,
          "August 2025 is inside the CAS regime")
    rows = [{"session": "2025-07-01", "executability": schema.EXECUTABLE,
             "net_rupees": 100.0, "net_r": 1.0}]
    part = validation.partition(rows)
    check(part["excluded_pre_cas"] == 1, "pre-CAS rows are excluded, not bucketed")
    v = validation.verdict([], match_pct=100.0)
    check(v["verdict"] == schema.REQUIRES_MORE_DATA, "an empty book cannot pass")
    check(v["promotion_is_manual"], "promotion is never automatic")

    rich = [
        {"session": f"2025-09-{d:02d}", "executability": schema.EXECUTABLE,
         "net_rupees": 100.0, "net_r": 0.5, "status": schema.EXIT,
         "expiry_class": schema.EXPIRY_DAY}
        for d in range(1, 26) for _ in range(5)
    ]
    v2 = validation.verdict(rich, match_pct=95.0)
    check(v2["verdict"] == schema.CAS_PRODUCTION_CANDIDATE,
          "a consistently positive, well-sampled book reaches CANDIDATE")
    check("not an instruction to trade" in v2["reason"],
          "even a candidate verdict says it is not an instruction")
    losing = [dict(r, net_rupees=-100.0, net_r=-0.5) if r["session"] > "2025-09-19"
              else r for r in rich]
    check(validation.verdict(losing, match_pct=95.0)["verdict"] == schema.FAILS,
          "a negative holdout fails regardless of the earlier folds")


# ------------------------------------------------------------------- safety
def test_safety() -> None:
    scan = safety.scan()
    check(scan["clean"], f"no order path in Phase 18: {scan['violations']}")
    check(scan["statement"]["cas_real_order_path"] == "NO", "stated: no order path")
    check(scan["statement"]["real_money_execution"] == "DISABLED",
          "stated: real money disabled")
    raised = False
    try:
        safety.assert_paper_only("LIVE")
    except safety.RealOrderPathError:
        raised = True
    check(raised, "asking CAS for a live mode raises rather than proceeding")

    src = (safety.PACKAGE_DIR / "service.py").read_text(encoding="utf-8")
    check("import" in src and "app.broker" not in src,
          "the live entry point does not import a broker")


# ------------------------------------------------------------ live end-to-end
def test_live_flow() -> None:
    service.reset_for_tests()
    spot = 24_000.0
    ids: list[str] = []
    for i in range(24):
        t = ts_at(15, 10 + (i * 50) // 60, (i * 50) % 60)
        px = spot + i * 6.0
        obs_id = service.observe(
            "NIFTY", chain(px, bump=i * 1.5), spot=px, ind=ind(),
            candles=candles(px), htf_trend="UP", tick_ts=t,
            capture_ts=t + 0.2, expiry="2025-09-25", days_to_expiry=0,
            quote_age_ms=200.0, market_open=True,
        )
        if obs_id:
            ids.append(obs_id)
    check(len(ids) >= 20, f"the CAS window was sampled ({len(ids)} observations)")

    outside = service.observe("NIFTY", chain(spot), spot=spot,
                              tick_ts=ts_at(11, 0), capture_ts=ts_at(11, 0),
                              quote_age_ms=200.0)
    check(outside is None, "nothing is captured outside the CAS window")
    mcx = service.observe("CRUDEOIL", chain(spot), spot=spot,
                          tick_ts=ts_at(15, 20), capture_ts=ts_at(15, 20),
                          quote_age_ms=200.0)
    check(mcx is None, "an instrument with no auction is not captured")

    rows = service.observations_today()
    check(rows, "observations are retained for the dashboard")
    first = rows[0]
    check(len(first["ladder"]) >= 5, "the ladder reaches far OTM")
    rungs = {r["rung"] for r in first["ladder"]}
    check(schema.VERY_FAR_OTM in rungs, "the very-far-OTM rung is captured")
    check(all(r.get("ce") and r.get("pe") for r in first["ladder"]),
          "CE and PE are captured at the same instant on every rung")
    check(first["timestamp_delta_ms"] is not None, "the capture delay is recorded")

    cards = service.cards()
    check(cards, "a CAS card is produced")
    card = cards[0]
    check(card["signal"] in schema.SIGNALS, "the signal is from the fixed set")
    check(card["paper_only"] and card["no_real_order"], "the card is marked paper")
    check(card["banner"] == "CAS PAPER ONLY", "the banner is on the card")
    check(card["status"] in schema.STATUSES, "the status is from the fixed set")
    check("cas_expected_move_points" in card and "t3" in card,
          "the requested card fields are present on the live card")
    check(card.get("cas_score_is_probability") is False,
          "the card never presents its score as a probability")

    health = service.health()
    check(health["failures"] == 0, f"no capture failures: {health['last_error']}")
    check(health["observations"] == len(ids), "every observation is counted")
    check(health["paper_only"], "health reports paper-only")

    recon = service.reconciliation()
    check(recon["ce_captures"] == recon["pe_captures"],
          "both sides are captured equally often")
    check(recon["exact_or_near_pct"] is not None, "the match rate is measured")

    # Push past the window so anything open resolves rather than lingering.
    service.observe("NIFTY", chain(spot + 200), spot=spot + 200, ind=ind(),
                    tick_ts=ts_at(15, 29, 55), capture_ts=ts_at(15, 29, 55),
                    quote_age_ms=200.0, expiry="2025-09-25", days_to_expiry=0)
    check(len(service.open_positions()) + len(service.resolved()) > 0,
          "the paper book opened at least one leg")
    for row in service.resolved():
        check(row.get("paper_only") is True, "every settled row is marked paper")


# ------------------------------------------------------------------ studies
def test_studies() -> None:
    obs = service.observations_today()
    u = moves.underlying_report(obs)
    check(u["measured"] >= 1, "the underlying move is measured")
    check(schema.REQUIRES_MORE_DATA in u["answer"],
          "one session cannot answer repeatability, and says so")
    p = moves.premium_report(obs)
    check(p["paths"] > 0, "premium paths are built")
    for path in p["per_path"]:
        if path.get("status") == "MEASURED":
            check("theoretical_return_pct" in path and
                  "executable_return_pct" in path,
                  "marked and executable returns are kept apart")
            break

    t = experiments.entry_timing(obs)
    check(t["comparisons"] == len(paper.ENTRY_TIMES), "every clock is compared")
    check(t["best"]["verdict"] in (schema.REQUIRES_MORE_DATA, schema.RESEARCH),
          "a single session cannot produce a finding")
    s = experiments.strike_distance(obs)
    check(set(s["by_side"]) == set(schema.SIDES), "both sides are run separately")
    check(s["production_untouched"], "the study states production is untouched")
    e = experiments.expiry_cohorts(obs)
    check(set(e["cells"]) == set(schema.EXPIRY_CLASSES), "three expiry cohorts")


# ------------------------------------------------------------------ reports
def test_reports() -> None:
    obs = service.observations_today()
    payloads = reports.build_payloads(
        observations=obs, paper_rows=service.resolved(), coverage=[],
        cards=service.cards(),
    )
    check(set(payloads) == set(reports.REPORTS), "every report payload is built")
    qa = reports.answers(payloads)
    check(len(qa) == 20, "twenty questions are answered")
    check(all(q.get("basis") for q in qa), "every answer carries its basis")
    check(qa[-1]["answer"] == "NO", "'is CAS ready for production' answers NO")

    empty = reports.build_payloads(observations=[], paper_rows=[], coverage=[])
    check(empty["phase18_cas_validation"]["verdict"] == schema.REQUIRES_MORE_DATA,
          "an empty dataset yields REQUIRES_MORE_DATA, not a number")
    for a in reports.answers(empty)[:8]:
        check(str(a["answer"]).startswith(schema.REQUIRES_MORE_DATA),
              "an empty dataset answers nothing")

    with tempfile.TemporaryDirectory() as tmp:
        res = artefacts.build_all(
            outdir=tmp, observations=obs, paper_rows=service.resolved(),
            coverage=[], cards=service.cards(),
        )
        check(len(res["written"]) == len(reports.REPORTS) * 2 + 3,
              "ten pairs plus questions and the safety scan are written")
        md = (safety.PACKAGE_DIR / "..").resolve()  # keep the import meaningful
        check(md.exists(), "package path resolves")
        for pth in res["written"]:
            if pth.endswith("cas_daily_report.md"):
                text = open(pth, encoding="utf-8").read()
                check("CAS PAPER ONLY" in text, "the banner leads the daily report")


# ------------------------------------------------------------------ journal
def test_journal() -> None:
    rows = journal.rows(service.resolved())
    for r in rows:
        check(r["strategy"] == schema.STRATEGY, "journal rows are tagged CAS")
        check(r["paper_only"] is True, "journal rows are marked paper")
        break
    csv_text = journal.to_csv(rows)
    check(csv_text.splitlines()[0].startswith("strategy,"),
          "the journal CSV leads with the strategy column")
    recon = journal.reconcile(observations=[], cards=[], paper_rows=[])
    check(recon["observations"] == 0 and recon["quality_gate"]
          == quality.INSUFFICIENT, "an empty reconciliation is insufficient")


# --------------------------------------------------------------------- store
def test_store() -> None:
    h = store.health()
    check("files" in h, "the store reports which evidence files exist")
    check(settings.phase18_tick_size > 0, "a tick size is configured")
    check(len(str(settings.phase18_universe).split(",")) >= 1, "a universe is set")


# ----------------------------------------------------------------- CAS cache
def test_cas_cache() -> None:
    """The cache must be faster and never a different answer.

    /api/cas-report rebuilds from the whole journal, timed out at 30s on
    2026-09-15 and cost that session its CAS tab. The cache removes the rebuild
    from the request, so what has to hold is that a hit is only ever served for
    the exact journals it was built from.
    """
    check(set(cas_cache.SOURCES) == {store.OBSERVATIONS, store.PAPER,
                                     store.COVERAGE},
          "the fingerprint covers every journal the report is built from")

    for p in (cas_cache.path(), cas_cache.path() + ".tmp"):
        if os.path.exists(p):
            os.remove(p)

    fp = cas_cache.fingerprint()
    check(set(fp) == set(cas_cache.SOURCES),
          "one fingerprint entry per source journal")
    missing = [n for n, v in fp.items() if v.get("state") == cas_cache.ABSENT]
    check(all("bytes" not in fp[n] for n in missing),
          "an absent journal is recorded absent, not as an empty one")

    check(cas_cache.load() is None and cas_cache.fresh() is None,
          "no cache file means no cache, not a stale read")

    first = cas_cache.report()
    check(first["cache"]["served"] == cas_cache.REBUILT_ABSENT,
          "the first call rebuilds and says so")
    check(os.path.exists(cas_cache.path()), "the rebuild writes the cache")
    check(first["paper_only"] is True and first["no_real_order"] is True,
          "the cached payload keeps the paper-only statement")

    second = cas_cache.report()
    check(second["cache"]["served"] == cas_cache.HIT,
          "the second call is served from the cache")
    check(second["cache"]["built_at"] == first["cache"]["built_at"],
          "a hit reports when the numbers were computed, not when asked")

    direct = reports.build_payloads(
        observations=store.observations(), paper_rows=store.paper(),
        coverage=store.coverage(),
    )
    check(second["capture"] == direct["phase18_cas_capture"],
          "a cached capture payload equals a freshly built one")
    check(second["validation"] == direct["phase18_cas_validation"],
          "a cached verdict equals a freshly built one")
    check(second["daily"]["verdict"] == direct["cas_daily_report"]["verdict"],
          "caching cannot change the verdict")

    # A journal that grew must invalidate: same answer served over new evidence
    # would be a report describing a store it never saw.
    with open(store.path(store.COVERAGE), "a", encoding="utf-8") as fh:
        fh.write('{"session": "2025-09-25", "smoke": true}\n')
    check(cas_cache.fresh() is None,
          "an appended journal invalidates the cache")
    third = cas_cache.report()
    check(third["cache"]["served"] == cas_cache.REBUILT_STALE,
          "a changed journal rebuilds and says which of the two it was")
    check(third["cache"]["built_at"] != first["cache"]["built_at"],
          "the rebuilt payload carries its own build time")

    # An mtime change with no size change still invalidates: a rewritten file of
    # the same length is not the same evidence.
    entry = cas_cache.load()
    check(entry is not None, "the rebuilt cache is readable")
    stamp = os.stat(store.path(store.COVERAGE))
    os.utime(store.path(store.COVERAGE),
             (stamp.st_atime, stamp.st_mtime + 60.0))
    check(cas_cache.fresh() is None,
          "a same-size journal with a new mtime invalidates the cache")
    os.utime(store.path(store.COVERAGE), (stamp.st_atime, stamp.st_mtime))
    check(cas_cache.fresh() is not None,
          "restoring the journal's state makes the same cache current again")

    # A torn or hand-edited cache is not evidence about anything.
    with open(cas_cache.path(), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    check(cas_cache.load() is None and cas_cache.fresh() is None,
          "an unreadable cache is ignored rather than raising")
    check(cas_cache.report()["cache"]["served"] == cas_cache.REBUILT_ABSENT,
          "an unreadable cache counts as no cache and is rebuilt over")

    warm = cas_cache.ensure()
    check(warm["rebuilt"] is False and warm["state"] == cas_cache.HIT,
          "warming a current cache rebuilds nothing")

    src = open("app/research/phase18/cas_cache.py", encoding="utf-8").read()
    for bad in ("place_order", "buy(", "sell(", "smart", "broker"):
        check(bad not in src, f"the cache has no {bad!r} anywhere near it")

    main_src = open("app/main.py", encoding="utf-8").read()
    head = main_src.split('@app.get("/api/cas-report")')[1][:600]
    check("cas_cache.report" in head,
          "the endpoint serves the report through the cache")


# -------------------------------------------------------- snapshot pre-warm
def test_prewarm() -> None:
    """The nightly collection must read a finished CAS report, not race one."""
    from app.research.phase37 import schedule as p37schedule

    check(p37schedule.PREWARM_MINUTES_BEFORE > 0,
          "the warm-up runs before the trigger, not at it")
    warm_at = ts_at(23, 36, day="2025-09-25")
    early = ts_at(23, 30, day="2025-09-25")
    check(p37schedule.in_prewarm_window(warm_at),
          "23:36 IST is inside the warm-up window")
    check(not p37schedule.in_prewarm_window(early),
          "23:30 IST is before the warm-up window")
    check(not p37schedule.in_window(warm_at),
          "the warm-up happens before the snapshot window opens")
    check(p37schedule.prewarm_once(early) is None,
          "nothing is warmed outside the window")
    first = p37schedule.prewarm_once(warm_at)
    check(isinstance(first, dict) and "state" in first,
          "the warm-up reports what it did")
    check(p37schedule.prewarm_once(warm_at) is None,
          "the warm-up runs once per session")
    state = p37schedule.status(warm_at)
    check(state["in_prewarm_window"] is True and state["last_prewarm"] == first,
          "the schedule status carries the warm-up outcome")
    check(state["back_fill"].startswith("NEVER"),
          "warming changed nothing about back-filling a past session")

    # A warm that raised must not consume the session's one attempt: the whole
    # point is that the report is ready before 23:40.
    later = ts_at(23, 37, day="2025-09-26")
    def _boom() -> dict:
        raise OSError("the store was unreadable")
    original = cas_cache.ensure
    cas_cache.ensure = _boom
    try:
        failed = p37schedule.prewarm_once(later)
    finally:
        cas_cache.ensure = original
    check(isinstance(failed, dict)
          and failed["state"] == p37schedule.PREWARM_FAILED,
          "a warm-up that raised is reported rather than swallowed")
    again = p37schedule.prewarm_once(later)
    check(isinstance(again, dict) and again["state"] != p37schedule.PREWARM_FAILED,
          "a failed warm-up is retried on the next poll, not marked done")


def main() -> int:
    started = time.time()
    for fn in (test_session, test_quality, test_execution, test_direction,
               test_score, test_expectation, test_signal, test_paper,
               test_overnight,
               test_validation, test_safety, test_live_flow, test_studies,
               test_reports, test_journal, test_store, test_cas_cache,
               test_prewarm):
        fn()
    service.reset_for_tests()
    print(f"checked {CHECKS} in {time.time() - started:.1f}s")
    if FAILED:
        for f in FAILED:
            print(f"FAIL: {f}")
        print(f"phase18 smoke: {len(FAILED)} FAILED")
        return 1
    print("phase18 smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
