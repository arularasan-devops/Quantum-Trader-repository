"""Phase 17 smoke: capture quality, both-side paths, costed paper, promotion.

Run: .venv/bin/python _smoke_phase17.py

Every check here exists because of a specific way this dataset could lie: a stale
book filled as though it were live, a leg entered at the mid instead of the ask, a
cohort table computed over a handful of rows, a promotion granted in sample.
"""
from __future__ import annotations

import os
import sys
import tempfile

# QT_ prefixed: settings reads env with env_prefix="QT_", so a bare DATA_DIR is
# ignored and the smoke's fixture rows land in the REAL evidence files. That is
# how twelve identical fixture legs ended up in a live phase17_paper.jsonl.
os.environ.setdefault("QT_DATA_DIR", tempfile.mkdtemp(prefix="p17smoke_"))

from app.config import settings  # noqa: E402
from app.models import (  # noqa: E402
    Decision,
    OptionQuote,
    OptionType,
    Signal,
)
from app.research.phase17 import (  # noqa: E402
    aplus,
    artefacts,
    capture,
    cepe,
    economics,
    entry as entry_mod,
    htf_probe,
    journal,
    oos,
    paper,
    quality,
    reach,
    reports,
    schema,
    service,
    store,
    tracker,
)

CHECKS = 0


def ok(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        print(f"FAIL: {label}")
        sys.exit(1)


def eq(a: object, b: object, label: str) -> None:
    ok(a == b, f"{label} (got {a!r}, want {b!r})")


# ---------------------------------------------------------------- quality
def t_quality() -> None:
    eq(quality.classify_delay(0), quality.EXACT, "0ms is EXACT")
    eq(quality.classify_delay(1_999), quality.EXACT, "under 2s is EXACT")
    eq(quality.classify_delay(2_001), quality.GOOD, "over 2s is GOOD")
    eq(quality.classify_delay(11_000), quality.DEGRADED, "over 10s is DEGRADED")
    eq(quality.classify_delay(61_000), quality.STALE, "over 60s is STALE")
    eq(quality.classify_delay(None), quality.MISSING, "no delay is MISSING")
    # The 60s median gap of the previous attempt must not be called usable-exact.
    eq(quality.classify_delay(60_000), quality.DEGRADED, "60s is at best DEGRADED")
    ok(not quality.fillable(quality.DEGRADED), "DEGRADED is not fillable")
    ok(not quality.fillable(quality.STALE), "STALE is not fillable")
    eq(quality.worst_of(quality.EXACT, quality.STALE), quality.STALE,
       "worst_of takes the worse side")
    ok(not quality.two_sided(10.0, 9.0), "a crossed book is not two-sided")
    ok(not quality.two_sided(0.0, 5.0), "a zero bid is not two-sided")
    t = quality.tally([quality.EXACT] * 9 + [quality.STALE])
    eq(t["exact_match_rate_pct"], 90.0, "tally computes the KPI")
    ok(t["meets_target"], "90% meets the target")
    ok(not quality.tally([quality.EXACT] * 8 + [quality.STALE] * 2)["meets_target"],
       "80% does not meet the target")


# ---------------------------------------------------------------- fixtures
def quote(*, bid: float | None = 100.0, ask: float | None = 101.0,
          premium: float | None = 100.5, vehicle: str = schema.CE,
          delta: float | None = 0.5, dq: str = quality.EXACT,
          symbol: str = "NIFTY24500CE") -> schema.Quote:
    return schema.Quote(
        instrument="NIFTY", vehicle=vehicle, symbol=symbol, strike=24500.0,
        expiry="2026-07-16", days_to_expiry=2, expiry_class=schema.PRE_EXPIRY,
        bid=bid, ask=ask, premium=premium, oi=500000, volume=120000, iv=14.5,
        delta=delta, gamma=0.002, theta=-8.0, underlying_price=24510.0,
        atm_strike=24500.0, moneyness=schema.ATM, distance_from_atm=0.0,
        strike_steps_from_atm=0, delta_band="0.40-0.60",
        source=quality.WEBSOCKET, feed_age_ms=300.0,
        signal_to_snapshot_ms=120.0, snapshot_ts=1_800_000_000.0, data_quality=dq,
    )


def plan() -> schema.Plan:
    return schema.Plan(
        entry=101.0, entry_low=99.0, entry_high=103.0, stop=91.0,
        target1=115.0, target2=125.0, target3=140.0, risk=10.0,
        reward_risk=1.4, expected_move_points=40.0, lot_size=75,
    )


def chain(ce_bid: float = 100.0, ce_ask: float = 101.0,
          pe_bid: float = 95.0, pe_ask: float = 96.0) -> list[OptionQuote]:
    rows: list[OptionQuote] = []
    for strike in (24400.0, 24450.0, 24500.0, 24550.0, 24600.0):
        for kind in (OptionType.CALL, OptionType.PUT):
            atm = abs(strike - 24500.0) < 1e-9
            bid = (ce_bid if kind is OptionType.CALL else pe_bid) if atm else 40.0
            ask = (ce_ask if kind is OptionType.CALL else pe_ask) if atm else 41.0
            rows.append(OptionQuote(
                symbol=f"NIFTY{int(strike)}{kind.value}",
                strike=strike, option_type=kind,
                premium=round((bid + ask) / 2, 2), bid=bid, ask=ask,
                oi=400000, oi_change=1200, volume=90000, iv=14.0,
                delta=0.5 if kind is OptionType.CALL else -0.5,
                gamma=0.002, theta=-8.0, vega=12.0,
            ))
    return rows


def decision(action: str = "BUY") -> Decision:
    return Decision(
        signal=Signal(action), confidence=72.0, signal_strength=68.0,
        trade_quality="GOOD", trade_score=71.0, reasons=["smoke"],
        recommended_option="NIFTY24500CE", option_type=OptionType.CALL,
        vehicle=schema.CE, current_premium=100.5, spot_price=24510.0,
        entry_range=(99.0, 103.0), stop_loss=91.0,
        target1=115.0, target2=125.0, target3=140.0,
        expected_move_points=40.0, htf_trend="UP", htf_strength=62.0,
        atr_points=45.0, episode_id="ep-1", global_signal_id="gs-1",
    )


# ---------------------------------------------------------------- economics
def t_economics() -> None:
    e = economics.assess(quote(), plan())
    ok(e["spread"] == 1.0, "spread comes from the book")
    ok(e["cost_status"] == schema.COST_MEASURED, "a real book gives measured cost")
    ok(e["cost_points"] and e["cost_points"] > 1.0,
       "cost exceeds the spread alone (brokerage, taxes, slippage)")
    ok(e["cost_over_risk"] is not None, "cost/risk is computed against plan risk")
    # Expected move must be converted through delta, not compared to index points.
    ok(e["expected_premium_move"] == 20.0,
       f"40 index points at 0.5 delta is 20 premium points (got "
       f"{e.get('expected_premium_move')})")

    wide = economics.assess(quote(bid=90.0, ask=101.0), plan())
    eq(wide["vehicle_class"], economics.RED, "an 11-point spread is RED")

    nobook = economics.assess(quote(bid=None, ask=None), plan())
    eq(nobook["cost_status"], schema.COST_UNKNOWN, "no book means cost UNKNOWN")
    ok(nobook["vehicle_class"] == economics.UNKNOWN,
       "an unquotable vehicle is UNKNOWN, never GREEN by default")
    eq(economics.assess(None, plan())["vehicle_class"], economics.UNKNOWN,
       "a missing quote is UNKNOWN")

    stale = economics.assess(quote(dq=quality.STALE), plan())
    ok("QUALITY_STALE" in stale["reasons"], "a stale book is named as such")
    eq(stale["cost_status"], schema.COST_UNKNOWN,
       "a stale book cannot produce a measured cost")

    leg = economics.cost_leg("NIFTY", 101.0, 115.0, quoted_spread=1.0, lots=1)
    eq(leg["gross_points"], 14.0, "gross is exit minus entry")
    ok(leg["net_points"] < leg["gross_points"], "net is below gross")
    ok(leg["slippage_points"] > 0, "slippage is charged on both sides")
    eq(economics.cost_leg("NIFTY", 101.0, None, quoted_spread=1.0)["cost_status"],
       schema.COST_UNKNOWN, "an unresolved leg has no net")


# ---------------------------------------------------------------- entry quality
def t_entry() -> None:
    entry_mod.reset()
    q = quote()
    e = entry_mod.assess(q, plan())
    eq(e["entry_quality"], entry_mod.UNKNOWN, "no history means UNKNOWN")
    ok("INSUFFICIENT_PREMIUM_HISTORY" in e["reasons"], "the reason is explicit")

    now = 1_800_000_000.0
    for i in range(8):
        entry_mod.note(q.symbol, 100.0, ts=now - 600 + i * 10)
    e = entry_mod.assess(q, plan(), now=now)
    eq(e["entry_quality"], entry_mod.IDEAL, "a flat premium is an IDEAL entry")

    entry_mod.reset()
    for i in range(8):
        entry_mod.note(q.symbol, 60.0 + i, ts=now - 300 + i * 10)
    chased = entry_mod.assess(quote(bid=100.0, ask=101.0, premium=100.5),
                              plan(), now=now)
    eq(chased["entry_quality"], entry_mod.SEVERELY_CHASED,
       "a 67% run from the trough is SEVERELY_CHASED")
    ok(chased["extension_from_trough_pct"] > 30.0, "extension is measured")

    # An old observation must not be used as this entry's trough.
    entry_mod.reset()
    for i in range(8):
        entry_mod.note(q.symbol, 20.0, ts=now - 7200 + i)
    stale = entry_mod.assess(q, plan(), now=now)
    eq(stale["entry_quality"], entry_mod.UNKNOWN,
       "history outside the look-back does not grade an entry")
    eq(entry_mod.historical_unknown()["basis"], "UNDERLYING_ONLY",
       "replayed rows are explicitly UNDERLYING_ONLY")


# ---------------------------------------------------------------- reachability
def t_reach() -> None:
    r = reach.assess(quote(), plan(), economics.assess(quote(), plan()))
    ok(r["t1_score"] is not None, "a live book scores")
    ok(r["not_a_probability"], "the row says it is not a probability")
    eq(r["premium_distance_to_t1"], 14.0, "distance to T1 in premium points")
    eq(r["underlying_move_required"], 28.0,
       "14 premium points at 0.5 delta needs 28 index points")
    ok(r["t1_rank"] in reach.RANKS, "the rank is one of the bands")

    far = reach.assess(quote(delta=0.05), plan())
    ok(far["underlying_move_required"] > r["underlying_move_required"],
       "a low-delta contract needs a bigger index move for the same T1")
    ok(far["t1_score"] < r["t1_score"], "and therefore scores lower")

    stale = reach.assess(quote(dq=quality.STALE), plan())
    eq(stale["t1_score"], None, "a stale book produces no score")
    eq(reach.assess(None, plan())["t1_rank"], reach.RANK_UNKNOWN,
       "no quote means no rank")
    # A plan whose T1 is already below the ask is a stale plan, not a gift.
    bad = reach.assess(quote(), schema.Plan(target1=50.0, stop=91.0, risk=10.0))
    eq(bad["t1_score"], None, "T1 below entry scores nothing")
    eq(r["t1_basis"], reach.BASIS_ENGINE,
       "a published target is measured against as the engine's own")
    eq(bad["t1_basis"], reach.BASIS_NONE,
       "no usable target and no expected move leaves T1 unmeasurable")

    # §room. The engine publishes a target only for the leg it is recommending, so
    # every other candidate was unmeasurable by construction. A derived target
    # makes it gradeable — and must say so on the row.
    no_target = schema.Plan(entry=101.0, stop=91.0, risk=10.0,
                            expected_move_points=40.0)
    mod = reach.assess(quote(), no_target)
    eq(mod["t1_basis"], reach.BASIS_MODELLED,
       "with no published target the expected move supplies one")
    eq(mod["t1_premium"], 121.0, "40 index points at 0.5 delta is 20 premium points")
    ok(mod["t1_score"] is not None, "and the candidate can therefore be scored")
    ok("T1_MODELLED_FROM_EXPECTED_MOVE" in mod["reasons"],
       "the derived basis is stated in the reasons, not only in a field")
    eq(mod["components"].get("move_coverage"), None,
       "move_coverage is NOT scored off a target derived from the expected move")
    ok(r["components"].get("move_coverage") is not None,
       "but it is still scored where the target came from the engine")
    eq(reach.assess(quote(delta=None), no_target)["t1_basis"], reach.BASIS_NONE,
       "no delta means the expected move cannot be converted to premium")
    stale_plan = reach.assess(quote(), schema.Plan(
        entry=101.0, target1=50.0, stop=91.0, risk=10.0,
        expected_move_points=40.0))
    ok("ENGINE_T1_NOT_ABOVE_ENTRY" in stale_plan["reasons"],
       "a published target below the ask is reported as stale, not silently replaced")

    table = reach.rank_table([r, far, stale])
    eq(table["total"], 3, "the rank table counts every row")
    ok(table["not_a_probability"], "the table repeats the disclaimer")


# ---------------------------------------------------------------- CE/PE
def t_cepe() -> None:
    def leg(side: str, net: float, *, vehicle: str = schema.CE,
            u_entry: float = 24500.0, u_best: float = 24560.0,
            u_worst: float = 24495.0, dq: str = quality.EXACT) -> dict:
        return {
            "observation_id": "obs-1", "side": side, "vehicle": vehicle,
            "net_points": net, "gross_points": net + 3.0,
            "cost_status": schema.COST_MEASURED, "data_quality": dq,
            "underlying_entry": u_entry, "underlying_best": u_best,
            "underlying_worst": u_worst,
        }

    c = cepe.compare(leg("SELECTED", 8.0), leg("OPPOSITE", -6.0, vehicle=schema.PE))
    eq(c["side_verdict"], cepe.RIGHT_SIDE, "a winning chosen side is RIGHT_SIDE")
    eq(c["fault"], cepe.RIGHT_BOTH, "and no fault")

    c = cepe.compare(leg("SELECTED", -7.0), leg("OPPOSITE", 9.0, vehicle=schema.PE))
    eq(c["side_verdict"], cepe.DIRECTION_FAILURE,
       "losing while the other side won is a direction failure")
    eq(c["fault"], cepe.WRONG_MARKET, "which is WRONG_MARKET")

    # The case the whole phase exists to expose: right read, unpayable vehicle.
    c = cepe.compare(
        leg("SELECTED", -2.0, u_best=24560.0, u_worst=24498.0),
        leg("OPPOSITE", -9.0, vehicle=schema.PE, u_best=24560.0, u_worst=24498.0),
    )
    eq(c["side_verdict"], cepe.BOTH_BAD, "both sides losing is BOTH_BAD")
    eq(c["fault"], cepe.WRONG_VEHICLE,
       "with the underlying still right, the vehicle took the money")

    eq(cepe.compare(leg("SELECTED", 5.0), None)["side_verdict"], cepe.UNKNOWN,
       "one side alone cannot be classified")
    eq(cepe.compare(leg("SELECTED", 5.0),
                    leg("OPPOSITE", -1.0, dq=quality.STALE))["side_verdict"],
       cepe.UNKNOWN, "a stale opposite side is not compared")

    t = cepe.tally([leg("SELECTED", -7.0), leg("OPPOSITE", 9.0, vehicle=schema.PE)])
    eq(t["classified"], 1, "the tally counts classified observations")
    eq(t["faults"][cepe.WRONG_MARKET], 1, "and attributes the fault")


# ---------------------------------------------------------------- A+
def t_aplus() -> None:
    entry_mod.reset()
    obs = capture.build("NIFTY", decision(), chain(), spot=24510.0,
                        signal_ts=1_800_000_000.0, capture_ts=1_800_000_000.5,
                        source=quality.WEBSOCKET, quote_age_ms=200.0,
                        expiry="2026-07-16", days_to_expiry=2)
    ok(obs is not None, "a live chain builds an observation")
    ok(obs.both_sides, "both sides were captured from the same chain")
    eq(obs.data_quality, quality.EXACT, "a 500ms gap is EXACT")
    service.enrich(obs)
    sc = obs.aplus
    ok(sc["a_plus_score"] is not None, "the score is computed")
    eq(len(sc["components"]), 6, "all six components are reported")
    ok(not sc["components"]["entry_edge"]["measured"],
       "entry edge is unmeasured without premium history")
    ok("MISSING_ENTRY_EDGE" in " ".join(sc["reasons"]),
       "and the missing component is named")
    ok(sc["a_plus_label"] != aplus.A_PLUS,
       "a candidate with an ungradeable entry is not A+")

    # A stale observation is rejected on data before anything else is considered.
    obs2 = capture.build("NIFTY", decision(), chain(), spot=24510.0,
                         signal_ts=1_800_000_000.0, capture_ts=1_800_000_090.0,
                         source=quality.CACHE, quote_age_ms=90_000.0)
    service.enrich(obs2)
    eq(obs2.aplus["a_plus_label"], aplus.REJECT_DATA,
       "a stale row is REJECT_DATA whatever else it scores")

    # Stocks are refused on arithmetic, not on statistics.
    prior = aplus.PRIOR_DEV_R["ICICIBANK"]
    ok(prior < 0, "the stock prior is negative")
    ok(aplus.PRIOR_DEV_R["MIDCPNIFTY"] > aplus.PRIOR_DEV_R["BANKNIFTY"],
       "the prior ordering matches the 5-year run")
    ok("Signal Score is not reused" in reports.aplus_report([], [])["note"],
       "the report says the Signal Score is not a component")

    b = aplus.board([obs, obs2], preferred=5, watch=5)
    ok(len(b["preferred"]) <= 5, "the board is capped at five")
    ok(b["empty_is_valid"], "an empty board is valid")
    ok(b["paper_only"] and b["no_real_order"], "the board is labelled paper only")
    eq(aplus.board([], preferred=5, watch=5)["preferred"], [],
       "no candidates produces no rows")


# ---------------------------------------------------------------- tracker/paper
def t_tracker() -> None:
    tracker.reset()
    entry_mod.reset()
    obs = capture.build("NIFTY", decision(), chain(), spot=24510.0,
                        signal_ts=1_800_000_000.0, capture_ts=1_800_000_000.2)
    service.enrich(obs)
    eq(tracker.open_legs(obs, now=1_800_000_001.0), tracker.TRACKED,
       "both sides open")
    eq(tracker.open_count(), 2, "one leg per side")
    eq(tracker.open_legs(obs, now=1_800_000_002.0), tracker.TRACKED,
       "re-observing the same candidate does not reopen it")
    eq(tracker.open_count(), 2, "so the count is unchanged")
    row = tracker.open_rows()[0]
    ok(row["entry"] == 101.0, "the selected leg entered at the ASK, not the mid")

    # T3 resolves the leg, and the exit is taken at the bid.
    resolved = tracker.update("NIFTY", chain(ce_bid=141.0, ce_ask=142.0),
                              now=1_800_000_600.0, underlying=24600.0)
    sel = [r for r in resolved if r["side"] == tracker.SELECTED]
    ok(sel, "the selected leg resolved")
    r = sel[0]
    eq(r["outcome"], schema.T3, "T3 was reached")
    eq(r["exit_premium"], 141.0, "the exit was taken at the BID")
    eq(r["cost_status"], schema.COST_MEASURED, "a two-sided book costs the leg")
    ok(r["net_points"] < r["gross_points"], "net is below gross")
    ok(r["mfe"] > 0 and r["minutes_to_t1"] is not None, "the path was recorded")
    eq(r["hold_bucket"], "M_5_15", "hold time is bucketed")

    # Budget: a bounded tracker drops rather than silently missing rows.
    tracker.reset()
    eq(tracker.open_legs(obs, now=1.0, budget=1), tracker.DROPPED,
       "a full budget drops the candidate")
    ok(tracker.health()["dropped"] >= 1, "and the drop is counted honestly")

    # A stale chain cannot fill an entry.
    tracker.reset()
    obs3 = capture.build("NIFTY", decision(), chain(), spot=24510.0,
                         signal_ts=1_800_000_000.0, capture_ts=1_800_000_500.0)
    service.enrich(obs3)
    lbl = tracker.open_legs(obs3, now=1_800_000_500.0)
    if lbl == tracker.TRACKED:
        ok(all(r["cost_status"] == schema.COST_UNKNOWN
               for r in tracker.open_rows()),
           "a stale entry is never marked as a measured fill")


def t_paper() -> None:
    tracker.reset()
    paper.reset()
    entry_mod.reset()
    obs = capture.build("NIFTY", decision(), chain(), spot=24510.0,
                        signal_ts=1_800_000_000.0, capture_ts=1_800_000_000.2)
    service.enrich(obs)
    # Force the A+ label so the paper path is exercised deterministically; the
    # gates themselves are tested in t_aplus.
    obs.aplus = dict(obs.aplus or {})
    obs.aplus["a_plus_label"] = aplus.A_PLUS
    d = paper.consider(obs, now=1_800_000_001.0)
    eq(d["status"], paper.ENTERED, "an A+ candidate enters paper")
    eq(d["entry_price"], 101.0, "at the ask")
    eq(paper.consider(obs, now=1_800_000_002.0)["status"], paper.REFUSED_OPEN,
       "the same contract is not entered twice")

    ok(obs.aplus["promotable"] is True and obs.aplus["t1_basis"] == "ENGINE_TARGET",
       "the entered row was measured against the engine's own target")

    obs.aplus["a_plus_label"] = aplus.WATCH
    eq(paper.consider(obs, now=1_800_000_003.0)["status"],
       paper.REFUSED_NOT_A_PLUS, "a watch row does not enter")

    # A+ earned against a DERIVED target is research evidence. It must not reach
    # the costed book, because the promotion sample is drawn from that book.
    opened = paper.health()["entered"]
    modelled = capture.build("NIFTY", decision(), chain(), spot=24510.0,
                             signal_ts=1_800_000_000.0, capture_ts=1_800_000_000.2)
    service.enrich(modelled)
    modelled.aplus = dict(modelled.aplus or {})
    modelled.aplus["a_plus_label"] = aplus.A_PLUS
    modelled.aplus["promotable"] = False
    modelled.aplus["t1_basis"] = "MODELLED_FROM_EXPECTED_MOVE"
    eq(paper.consider(modelled, now=1_800_000_004.0)["status"],
       paper.REFUSED_MODELLED_T1,
       "an A+ row graded off a derived target is kept out of the costed book")
    eq(paper.health()["entered"], opened, "and nothing was opened for it")

    stale = capture.build("NIFTY", decision(), chain(), spot=24510.0,
                          signal_ts=1_800_000_000.0, capture_ts=1_800_000_090.0)
    service.enrich(stale)
    stale.aplus = dict(stale.aplus or {})
    stale.aplus["a_plus_label"] = aplus.A_PLUS
    eq(paper.consider(stale, now=1_800_000_090.0)["status"], paper.REFUSED_DATA,
       "a stale book is refused rather than filled at the LTP")

    q = quote(bid=90.0, ask=91.0, premium=90.5)
    closed = paper.advance("NIFTY", [q], now=1_800_000_400.0)
    ok(closed, "the stop resolved the episode")
    row = closed[0]
    eq(row["outcome"], schema.STOP, "at the stop")
    eq(row["exit_price"], 90.0, "sold at the bid, not the LTP")
    eq(row["exit_side"], "BID", "sold at the bid")
    ok(row["net_points"] < row["gross_points"], "the round trip was charged")
    ok(row["net_r"] is not None and row["net_r"] < 0, "and the loss is in R")
    ok(row["paper_only"] and row["no_real_order"], "the row is labelled paper")
    ok(row["hold_minutes"] > 0, "hold time was recorded")


# ---------------------------------------------------------------- reports
def t_reports() -> None:
    obs_rows = []
    legs = []
    for i in range(6):
        entry_mod.reset()
        o = capture.build("NIFTY", decision(), chain(), spot=24510.0,
                          signal_ts=1_800_000_000.0 + i * 60,
                          capture_ts=1_800_000_000.2 + i * 60)
        service.enrich(o)
        obs_rows.append(o.as_dict())
        legs.append({
            "observation_id": o.observation_id, "side": "SELECTED",
            "vehicle": schema.CE, "outcome": schema.T1 if i % 2 else schema.STOP,
            "net_points": 9.0 if i % 2 else -11.0,
            "gross_points": 12.0 if i % 2 else -8.0,
            "cost_points": 3.0, "cost_status": schema.COST_MEASURED,
            "data_quality": quality.EXACT, "net_r": 0.9 if i % 2 else -1.1,
            "risk": 10.0, "target1": 115.0, "entry_premium": 101.0,
            "vehicle_class": economics.GREEN, "hold_bucket": "M_15_30",
        })
    cap = reports.capture_report(obs_rows)
    eq(cap["total_candidates"], 6, "every candidate is counted")
    ok(cap["exact_match_rate_pct"] == 100.0, "all six were EXACT")
    ok(cap["prior_failure_reference"]["matched"] == 48,
       "the report carries the failure it exists to avoid repeating")

    veh = reports.vehicle_report(obs_rows, legs)
    eq(veh["status"], reports.THIN, "6 quotes is REQUIRES_MORE_DATA")
    by = veh["by_class"][economics.GREEN]
    eq(by["status"], reports.THIN, "a 6-row cohort reports REQUIRES_MORE_DATA")
    ok(by["median_reward_risk"] is not None,
       "R:R travels beside T1 so geometry cannot masquerade as skill")
    ok(by["t1_pct"] is not None and by["net_expectancy_r"] is not None,
       "T1 is never printed without expectancy")
    ok(veh["class_thresholds"]["fitted"] is False,
       "thresholds are declared unfitted")

    uncosted = legs + [{
        "observation_id": "x", "side": "SELECTED", "outcome": schema.T1,
        "gross_points": 5.0, "cost_status": schema.COST_UNKNOWN,
    }]
    v2 = reports.vehicle_report(obs_rows, uncosted)
    eq(v2["outcomes_excluded_uncosted"], 1,
       "an uncosted row is excluded and counted, not estimated")

    ent = reports.entry_report(obs_rows, legs)
    eq(ent["unknown_pct"], 100.0, "with no history every entry is UNKNOWN")
    ap = reports.aplus_report(obs_rows, legs)
    ok(ap["reuses_signal_score"] is False, "A+ does not reuse the Signal Score")
    ok(sum(ap["labels"].values()) == 6, "every candidate carries a label")
    htf = reports.htf_report(obs_rows, legs)
    ok("unanswerable" in htf["note"], "the HTF gap is described honestly")
    miss = reports.missed_report(obs_rows, legs)
    ok(miss["status"] == reports.THIN, "refusal grading needs a real sample")


def t_oos() -> None:
    rows = []
    for day in range(9):
        for i in range(4):
            rows.append({
                "session": f"2026-07-{day + 1:02d}",
                "entry_ts": 1_800_000_000.0 + day * 86400 + i * 600,
                "signal_ts": 1_800_000_000.0 + day * 86400 + i * 600,
                "outcome": schema.T1 if i % 2 else schema.STOP,
                "net_r": 1.2 if i % 2 else -1.0,
                "cost_status": schema.COST_MEASURED,
                "instrument": "NIFTY",
            })
    trades = oos.to_trades(rows)
    eq(len(trades), 36, "costed rows map to trades")
    eq(len([t for t in trades if t["exit_reason"] == "TARGET"]), 18,
       "T1 maps to TARGET so the fold module recognises it")
    ok(oos.to_trades([{**rows[0], "cost_status": schema.COST_UNKNOWN}]) == [],
       "an uncosted row is excluded from the OOS sample")

    sp = oos.split(trades)
    ok(sp["development"] and sp["validation"] and sp["holdout"],
       "the split is three non-empty chronological periods")
    dev_max = max(t["session"] for t in sp["development"])
    hold_min = min(t["session"] for t in sp["holdout"])
    ok(dev_max < hold_min, "no session appears in two periods")

    ev = oos.evaluate(rows)
    eq(ev["basis"], "NET_OF_MEASURED_COSTS", "OOS is graded on net")
    ok(ev["verdict"]["verdict"] != "PRODUCTION_CANDIDATE",
       "36 outcomes cannot promote anything")
    ok(any(c["requirement"] == "min_development_trades" and not c["passed"]
           for c in ev["verdict"]["checks"]),
       "the sample-size requirement is the blocker, and it is named")
    eq(oos.evaluate([])["status"], aplus.NOT_READY, "no data is NOT_READY")


def t_htf_probe() -> None:
    pool = []
    for day in range(4):
        for i in range(30):
            pool.append({
                "session": f"2021-03-{day + 1:02d}", "instrument": "NIFTY",
                "side": "LONG", "risk": 10.0, "reward_risk": 1.4,
                "mfe_r": 1.6 if i % 3 else 0.2,
                "mae_r": -0.3 if i % 3 else -1.5,
                "r": 1.4 if i % 3 else -1.0,
                "exit_reason": "TARGET" if i % 3 else "STOP",
                "regime": "RANGING" if i % 2 else "TRENDING",
                "htf_trend": "UP", "entry": 100.0, "target": 114.0,
            })
    out = htf_probe.generate(pool)
    eq(out["mirrored_rows"], 120, "every usable row mirrors")
    ok(out["basis"] == "UNDERLYING_ONLY", "the probe is underlying-only")
    ok(out["production_htf_unchanged"], "production HTF logic is untouched")
    ok(set(out["against_htf"]) == {
        htf_probe.OPTIMISTIC, htf_probe.PESSIMISTIC, htf_probe.UNAMBIGUOUS},
       "all three bounds are reported")
    m = htf_probe.mirror(pool[0])
    eq(m["htf_alignment"], htf_probe.AGAINST_HTF, "the mirror is against-HTF")
    eq(m["side"], "SHORT", "and on the other side")
    eq(m["reward_risk"], pool[0]["reward_risk"],
       "geometry is preserved so outcome differences cannot come from it")
    ok(htf_probe.mirror({"risk": 0.0}) is None, "an ungradeable row is skipped")
    amb = htf_probe.mirror({
        "risk": 10.0, "reward_risk": 1.0, "mfe_r": 1.5, "mae_r": -1.5,
        "side": "LONG",
    })
    eq(amb["resolution"], htf_probe.AMBIGUOUS,
       "touching both levels is AMBIGUOUS, never assumed")


def t_artefacts() -> None:
    outdir = os.path.join(settings.data_dir, "p17_reports")
    res = artefacts.build_all(outdir=outdir, observations=[], legs=[],
                              paper_rows=[], coverage_rows=[])
    eq(len(res["artefacts"]), 11, "ten artefact pairs plus the journal")
    jcsv = res["artefacts"]["journal"]["csv"]
    ok(os.path.exists(jcsv) and os.path.getsize(jcsv) > 0,
       "the journal CSV is written even with nothing captured (header only)")
    for name in artefacts.PAIRS:
        for kind in ("json", "md"):
            p = res["artefacts"][name][kind]
            ok(os.path.exists(p) and os.path.getsize(p) > 0, f"{name}.{kind} written")
    md = open(res["artefacts"]["capture"]["md"], encoding="utf-8").read()
    ok("EXACT match rate" in md, "the match rate leads the report")
    ok("Production**: unchanged" in md, "the safety line is in the header")
    qs = res["payloads"]["capture"]["questions"]
    ok(len(qs) >= 25, f"at least 25 daily questions (got {len(qs)})")
    ok(any("REQUIRES_MORE_DATA" in q["answer"] for q in qs),
       "with no data, questions answer REQUIRES_MORE_DATA")
    s = artefacts.summary(res["payloads"])
    ok(s["paper_only"] and s["no_real_order"], "the summary is labelled")


def t_journal() -> None:
    """§33 — the joined row, and the two ways it is allowed to be empty."""
    obs = {
        "observation_id": "obs-j", "session": "2026-07-14", "signal_ts": 100.0,
        "instrument": "NIFTY", "candidate_class": schema.BUY,
        "selected_vehicle": schema.CE, "data_quality": quality.EXACT,
        "both_sides": True, "market_basis": "UNDERLYING_ONLY",
        "selected": {"symbol": "NIFTY24500CE", "premium": 100.0, "bid": 99.0,
                     "ask": 101.0, "spread": 2.0, "delta": 0.5},
        "opposite": {"symbol": "NIFTY24500PE", "premium": 95.0, "bid": 94.0,
                     "ask": 96.0, "spread": 2.0, "delta": -0.5},
        "plan": {"entry": 101.0, "stop": 91.0, "target1": 115.0, "risk": 10.0},
        "economics": {"vehicle_class": economics.YELLOW, "cost_points": 1.8},
        "entry": {"entry_quality": entry_mod.UNKNOWN, "basis": "LIVE"},
        "reach": {"t1_score": 70.0, "t1_rank": "RANK_B"},
        "aplus": {"a_plus_score": 62.0, "a_plus_label": "WATCH",
                  "missing_components": ["entry_edge"]},
    }
    sel_leg = {
        "observation_id": "obs-j", "side": "SELECTED", "exit_ts": 400.0,
        "vehicle": schema.CE,
        "net_points": -2.0, "gross_points": 1.0, "mfe": 6.0, "mae": -4.0,
        "cost_status": schema.COST_MEASURED, "data_quality": quality.EXACT,
        "underlying_entry": 24500.0, "underlying_best": 24560.0,
        "underlying_worst": 24498.0, "hold_bucket": "M_5_15",
    }
    opp_leg = dict(sel_leg, side="OPPOSITE", vehicle=schema.PE,
                   net_points=-9.0, mfe=1.0)
    pp = {"observation_id": "obs-j", "episode_id": "ep-j", "entry_ts": 120.0,
          "entry_price": 101.0, "entry_side": "ASK", "exit_price": 99.0,
          "exit_side": "BID", "net_points": -3.8, "gross_points": -2.0,
          "cost_status": schema.COST_MEASURED}

    rows = journal.rows([obs], [sel_leg, opp_leg], [pp])
    eq(len(rows), 1, "one journal row per candidate")
    r = rows[0]
    eq(r["symbol"], "NIFTY24500CE", "the selected contract is carried")
    eq(r["opposite_symbol"], "NIFTY24500PE",
       "and the opposite side from the same timestamp")
    eq(r["leg_net_points"], -2.0, "the resolved leg's net is joined")
    eq(r["opposite_leg_net_points"], -9.0, "as is the opposite leg's")
    eq(r["attribution"], cepe.WRONG_VEHICLE,
       "right read plus two losing sides attributes to the vehicle")
    eq(r["paper_entry_side"], "ASK", "paper entry is ask-side")
    eq(r["paper_exit_side"], "BID", "and paper exit is bid-side")
    ok(r["paper_only"] and r["no_real_order"], "every row carries the labels")
    ok(r["not_a_probability"], "the T1 score is never a probability")

    # An unresolved candidate must still appear: dropping it would bias the
    # journal towards the legs that happened to fit the tracking budget.
    bare = journal.rows([dict(obs, observation_id="obs-k")], [], [])
    eq(len(bare), 1, "a candidate with no resolved leg still journals")
    eq(bare[0]["leg_net_points"], None, "with no invented outcome")
    eq(bare[0]["ce_pe_verdict"], None, "and no CE/PE verdict")

    csv_text = journal.to_csv(rows)
    head = csv_text.splitlines()[0].split(",")
    eq(len(head), len(journal.FIELDS), "the CSV header is the declared schema")
    ok("net_points" in csv_text.splitlines()[0], "net is a column")
    eq(len(journal.to_csv([]).strip().splitlines()), 1,
       "an empty journal is a header, not an empty file")


def t_service() -> None:
    service.reset()
    store.reset_health()
    oid = service.observe("NIFTY", decision(), chain(), spot=24510.0,
                          signal_ts=None, source=quality.WEBSOCKET,
                          quote_age_ms=150.0, expiry="2026-07-16",
                          days_to_expiry=2)
    ok(oid, "the tick hook records an observation")
    h = service.health()
    eq(h["observations"], 1, "and counts it")
    eq(h["failures"], 0, "with no failures")
    ok(h["written"] == 1, "the row reached the evidence file")
    ok(store.observations(), "and can be read back")

    ok(service.observe("SBIN", decision(), chain(), spot=800.0),
       "an optionable equity is captured now that the universe is not index-only")
    ok(service.observe("CRUDEOIL", decision(), chain(), spot=6900.0),
       "and so is an MCX commodity")
    eq(service.observe("ICICIBANK", decision(), chain(), spot=1200.0), None,
       "an instrument refused on a validated negative prior is still skipped")
    eq(service.observe("NOT_A_REAL_NAME", decision(), chain(), spot=100.0), None,
       "and so is a name the registry does not know")
    ok(service.health()["skipped_ineligible"] == 2, "and both skips are counted")

    ev = service.evidence()
    ok(ev["rows"], "the evidence panel has rows")
    ok(ev["rows"][0]["ce_pe"]["opposite"]["symbol"], "including the opposite side")
    b = service.board()
    ok(b["paper_only"], "the board is paper-only")

    # A malformed decision must not raise into the tick.
    broken = decision()
    broken.recommended_option = "NOT-IN-CHAIN"
    ok(service.observe("NIFTY", broken, chain(), spot=24510.0) is not None,
       "an unknown contract still records against the ATM pair")
    ok(service.observe("NIFTY", decision(), [], spot=24510.0) is None,
       "an empty chain records nothing rather than a fabricated row")
    eq(service.health()["failures"], 0, "no failure was logged for either case")


def main() -> None:
    t_quality()
    t_economics()
    t_entry()
    t_reach()
    t_cepe()
    t_aplus()
    t_tracker()
    t_paper()
    t_reports()
    t_oos()
    t_htf_probe()
    t_artefacts()
    t_journal()
    t_service()
    print(f"checked {CHECKS}")
    print("phase17 smoke: OK")


if __name__ == "__main__":
    main()
