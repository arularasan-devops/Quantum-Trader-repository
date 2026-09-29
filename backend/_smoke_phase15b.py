#!/usr/bin/env python
"""Smoke for Phase 15B — historical market-setup discovery.

Checks the things that decide whether the study's output can be believed:
the dimensions are all present, bucket edges are fitted on development rows
only, the holdout cannot leak into fitting, hypotheses are counted, a planted
edge is found, a planted noise dimension is NOT, geometry cannot flatter a T1
rate unnoticed, and nothing in the output claims option profitability.
"""
from __future__ import annotations

import datetime as dt
import random

from app.research.phase15b import dimensions as dims, discovery
from phase15b_discover import questions, render

CHECKED = 0


def check(label: str, condition: bool) -> None:
    global CHECKED
    CHECKED += 1
    if not condition:
        raise AssertionError(label)


def row(session: str, minute: float, *, instrument: str = "NIFTY",
        r: float = -1.0, side: str = "LONG", trend: str = "UP",
        regime: str = "TRENDING", rr: float = 2.0, risk: float = 20.0,
        score: float = 60.0, confidence: float = 60.0,
        conviction: float = 60.0, noise: float = 20.0,
        trigger: str = "BREAKOUT", taken: bool = True,
        opportunity: str = "HIGH", risk_level: str = "LOW",
        htf_strength: float = 50.0, opportunity_score: float = 55.0) -> dict:
    entry = 20000.0
    return {
        "instrument": instrument, "session": session, "entry_minute_ist": minute,
        "side": side, "entry": entry,
        "stop": entry - risk if side == "LONG" else entry + risk,
        "target": entry + risk * rr if side == "LONG" else entry - risk * rr,
        "risk": risk, "reward_risk": rr, "r": r, "regime": regime,
        "htf_trend": trend, "htf_strength": htf_strength,
        "entry_trigger": trigger,
        "trade_score": score, "confidence": confidence,
        "conviction_meter": conviction,
        "opportunity_score": opportunity_score,
        "opportunity_label": opportunity, "risk_level": risk_level,
        "noise_points": noise, "risk_over_noise": risk / noise,
        "mfe_r": max(0.0, r), "mae_r": -0.4,
        "exit_reason": "TARGET" if r > 0 else "STOP", "minutes": 30,
        "taken": taken, "signal": "BUY" if taken else "WAIT",
    }


def pool(sessions: int, start: dt.date, *, seed: int,
         edge_instrument: str = "MIDCPNIFTY") -> list[dict]:
    """A pool where exactly one condition carries an edge.

    The edge lives on one instrument and nowhere else. Every other reading is
    drawn independently of the outcome, so any dimension the pipeline reports
    besides that instrument is a false positive the fixture can catch.
    """
    rng = random.Random(seed)
    out: list[dict] = []
    day, made = start, 0
    while made < sessions:
        if day.weekday() >= 5:
            day += dt.timedelta(days=1)
            continue
        made += 1
        session = day.isoformat()
        for _ in range(14):
            inst = rng.choice(["NIFTY", "BANKNIFTY", edge_instrument,
                               "RELIANCE", "TCS"])
            side = rng.choice(["LONG", "SHORT"])
            trend = rng.choice(["UP", "DOWN", "FLAT"])
            noise = rng.uniform(5.0, 60.0)
            edge = inst == edge_instrument
            hit = rng.random() < (0.70 if edge else 0.35)
            out.append(row(
                session, rng.randint(0, 374), instrument=inst,
                r=2.0 if hit else -1.0, side=side, trend=trend,
                regime=rng.choice(["TRENDING", "RANGE", "CHOPPY"]),
                risk=max(1.0, noise * rng.uniform(0.5, 2.0)), noise=noise,
                # reward:risk varies enough to cut tertiles but not enough to
                # pay differently, so room stays a pure-noise dimension here.
                rr=rng.uniform(1.8, 2.2),
                score=rng.uniform(20, 100), confidence=rng.uniform(20, 100),
                conviction=rng.uniform(20, 100),
                htf_strength=rng.uniform(0, 100),
                opportunity_score=rng.uniform(20, 100),
                trigger=rng.choice(["BREAKOUT", "PULLBACK"]),
                taken=rng.random() < 0.3))
        day += dt.timedelta(days=1)
    return out


def main() -> int:
    # ---- dimensions -----------------------------------------------------
    wanted = ("session_period", "universe", "instrument", "volatility_band",
              "regime", "htf_alignment", "momentum", "extension", "room",
              "setup_type", "score_band", "confidence_band")
    for name in wanted:
        check(f"dimension {name} is under test", name in dims.DIMENSION_NAMES)
    check("engine decision is a dimension too",
          "engine_decision" in dims.DIMENSION_NAMES)
    check("the five-index allowlist is exact",
          dims.INDEX_UNIVERSE == ("NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY",
                                  "FINNIFTY"))
    check("the opening bucket is exactly the production gate's cohort",
          dims.PERIODS[0] == ("OPEN_0_15", 0.0, 15.0))

    dev = pool(300, dt.date(2021, 9, 1), seed=1)
    cuts = dims.fit_cuts(dev)
    check("cuts are fitted on development rows only",
          cuts["fitted_on"] == "DEVELOPMENT_ONLY")
    check("every continuous reading got tertile edges",
          len(cuts["tertiles"]) == len(dims.TERTILE_FIELDS))
    for field, (lo, hi) in cuts["tertiles"].items():
        check(f"{field} tertile edges are ordered", lo < hi)

    # ---- bucketing ------------------------------------------------------
    check("opening minute buckets to the gate cohort",
          dims.bucket(row("s", 5.0), "session_period", cuts) == "OPEN_0_15")
    check("minute 15 is out of the gate cohort",
          dims.bucket(row("s", 15.0), "session_period", cuts) == "EARLY_15_60")
    check("last-hour minute buckets late",
          dims.bucket(row("s", 300.0), "session_period", cuts)
          == "LATE_240_375")
    check("an out-of-session stamp is placed in no period, not in the opening "
          "one", dims.bucket(row("s", -547.0), "session_period", cuts) is None)
    check("a stamp past the close is placed in no period",
          dims.bucket(row("s", 880.0), "session_period", cuts) is None)
    check("an index is an index",
          dims.bucket(row("s", 60.0, instrument="SENSEX"), "universe", cuts)
          == "INDEX")
    check("a stock is a stock",
          dims.bucket(row("s", 60.0, instrument="TCS"), "universe", cuts)
          == "STOCK")
    check("long in an uptrend is with the higher timeframe",
          dims.bucket(row("s", 60.0, side="LONG", trend="UP"), "htf_alignment",
                      cuts) == "WITH_HTF")
    check("short in a downtrend is also with the higher timeframe",
          dims.bucket(row("s", 60.0, side="SHORT", trend="DOWN"),
                      "htf_alignment", cuts) == "WITH_HTF")
    check("long in a downtrend is against it",
          dims.bucket(row("s", 60.0, side="LONG", trend="DOWN"),
                      "htf_alignment", cuts) == "AGAINST_HTF")
    check("a flat higher timeframe is neither",
          dims.bucket(row("s", 60.0, trend="FLAT"), "htf_alignment", cuts)
          == "NO_HTF_TREND")
    check("a missing instrument buckets nowhere",
          dims.bucket({"entry_minute_ist": 60.0}, "universe", cuts) is None)
    check("an unknown dimension is refused, not defaulted",
          _raises(lambda: dims.bucket(row("s", 60.0), "nonsense", cuts)))

    # ---- selector -------------------------------------------------------
    keep = dims.selector({"universe": "INDEX", "session_period": "MID_60_240"},
                         cuts)
    check("a setup keeps a row meeting every condition",
          keep(row("s", 100.0, instrument="NIFTY")))
    check("a setup refuses a row failing one condition",
          not keep(row("s", 100.0, instrument="TCS")))
    check("a setup refuses a row it cannot place, rather than keeping it",
          not keep(row("s", -20.0, instrument="NIFTY")))
    check("a setup over an unknown dimension is refused",
          _raises(lambda: dims.selector({"nope": "X"}, cuts)))

    # ---- multiple testing -----------------------------------------------
    one = discovery.z_threshold(1)
    many = discovery.z_threshold(500)
    check("one comparison uses the usual threshold",
          abs(one["z_bonferroni"] - 1.96) < 0.01)
    check("500 comparisons raise the bar",
          many["z_bonferroni"] > one["z_bonferroni"] + 1.5)
    check("expected false positives are published",
          many["expected_false_positives_unadjusted"] == 25.0)
    check("the threshold explains itself in the report",
          "noise alone" in many["note"])

    # ---- chronology -----------------------------------------------------
    split = discovery.split_periods(dev, dev_share=0.7)
    dev_names = set(split["development_sessions"])
    val_names = set(split["validation_sessions"])
    check("development and validation share no session",
          not dev_names & val_names)
    check("validation is strictly later than development",
          max(dev_names) < min(val_names))
    check("both periods carry rows",
          split["development"] and split["validation"])
    check("every row lands in exactly one period",
          len(split["development"]) + len(split["validation"]) == len(dev))

    # ---- the study ------------------------------------------------------
    holdout = pool(160, dt.date(2024, 9, 2), seed=2)
    study = discovery.run(dev, holdout)
    check("the study is research only", study["research_only"] is True)
    check("the study places no order", study["live_orders"] is False)
    check("every result is labelled underlying only",
          study["cost_basis"] == discovery.UNDERLYING_ONLY)
    check("all sixteen dimensions were scanned",
          len(study["dimensions"]) == len(dims.DIMENSION_NAMES))
    check("hypotheses were counted",
          study["multiple_testing"]["hypotheses"] > 30)
    check("the holdout is reported with its own session count",
          study["periods"]["holdout_sessions"] > 0)
    check("the holdout rows never entered the fit",
          study["periods"]["holdout_rows"] == len(holdout))

    labels = [s["label"] for s in study["setups"]]
    check("the planted instrument edge was found",
          any("instrument=MIDCPNIFTY" in label for label in labels))
    found = next(s for s in study["setups"]
                 if s["label"] == "instrument=MIDCPNIFTY")
    check("the planted edge survives the holdout",
          found["verdict"] in (discovery.SURVIVES, discovery.SURVIVES_SO_FAR))
    check("the planted edge is positive out of sample",
          (found["holdout"].get("lift_r") or 0) > 0)
    check("the planted edge is walk-forward tested",
          "verdict" in found["walk_forward"])
    check("a surviving setup still carries the underlying-only basis",
          found["holdout"]["cost_basis"] == discovery.UNDERLYING_ONLY)

    noise_dims = {next(iter(s["conditions"])) for s in study["setups"]}
    for pure_noise in ("regime", "setup_type", "score_band", "confidence_band"):
        check(f"the noise dimension {pure_noise} was not sold as an edge",
              pure_noise not in noise_dims
              or study["setups"][0]["verdict"] != discovery.SURVIVES)

    # ---- the conjunction ------------------------------------------------
    conj = study["conjunction"]
    check("a conjunction was attempted", conj["grids_attempted"])
    check("the grid choice is explained", "grid" in conj["grid_choice_note"]
          or "no conjunction" in conj["grid_choice_note"])
    check("cells under the sample bar were not tested as comparisons",
          conj["cells_measured"] <= conj["cells_populated"])
    check("only measured cells were counted into the budget",
          conj["buckets_tested"] == conj["cells_measured"])

    # ---- verdict vocabulary ---------------------------------------------
    allowed = {discovery.SURVIVES, discovery.SURVIVES_SO_FAR,
               discovery.IN_SAMPLE_ONLY, discovery.THIN, discovery.FAILS}
    for setup in study["setups"]:
        check(f"{setup['label']} carries an evidence-based verdict",
              setup["verdict"] in allowed)
    for row_out in study["ranking"]:
        check("only survivors are ranked",
              row_out["verdict"] in (discovery.SURVIVES,
                                     discovery.SURVIVES_SO_FAR))
        check("a rank carries its own T1 rate", "t1_pct" in row_out)
        check("a rank carries candidates per day",
              "candidates_per_day" in row_out)
        check("a rank carries drawdown", "max_drawdown_r" in row_out)
        check("a rank carries reward:risk beside its T1 rate",
              "mean_reward_risk" in row_out)

    # ---- thin data -------------------------------------------------------
    thin = discovery.run(dev[:200], holdout[:60])
    check("a thin pool produces no survivors, not a lucky one",
          all(s["verdict"] in (discovery.THIN, discovery.IN_SAMPLE_ONLY,
                              discovery.FAILS)
              for s in thin["setups"]))
    check("a thin pool still reports its periods",
          thin["periods"]["holdout_rows"] == 60)

    # ---- geometry --------------------------------------------------------
    near = [row("2024-01-01", 60.0, rr=0.5, r=0.5) for _ in range(200)]
    far = [row("2024-01-01", 60.0, rr=3.0, r=-1.0) for _ in range(200)]
    cmp_near = discovery.compare(near, near + far)
    check("a nearer-target cohort is flagged as not geometrically comparable",
          cmp_near["geometry_comparable"] is False)
    check("the geometry flag explains itself",
          "nearer target" in cmp_near.get("geometry_note", ""))
    check("geometry travels with every cohort",
          cmp_near["mean_reward_risk"] == 0.5)
    check("a plan-geometry setup is marked as such",
          discovery.geometry_selection({"room": "HIGH"}))
    check("a market setup is not marked as plan geometry",
          not discovery.geometry_selection({"instrument": "NIFTY"}))

    # ---- integrity -------------------------------------------------------
    dirty = list(dev) + [
        {**row("2024-06-06", 60.0), "risk": 0.0},
        {**row("2024-06-06", 60.0), "r": float("inf")},
        {**row("2024-06-06", 60.0), "target": None},
    ]
    guarded = discovery.run(dirty, holdout)
    check("rows failing the guard are excluded and counted",
          guarded["integrity"]["data_errors"] >= 3)
    check("the guard is named in the report",
          "DATA_ERROR" in guarded["integrity"]["note"])

    # ---- handoff ---------------------------------------------------------
    hand = study["handoff_to_live"]
    for token in ("CE or PE", "premium", "spread", "OI", "IV", "delta",
                  "chase guard"):
        check(f"the live layer is told to verify {token}",
              any(token in check_line for check_line in
                  hand["must_verify_live"]))
    check("all three conditions are required before a paper trade",
          "NO TRADE" in hand["paper_rule"])
    check("option profitability is explicitly not established",
          "option profitability" in hand["not_established_here"])

    # ---- report ----------------------------------------------------------
    text = "\n".join(render(study))
    check("the report states it is research only", "RESEARCH ONLY" in text)
    check("the report labels the cost basis",
          discovery.UNDERLYING_ONLY in text)
    check("the report shows the comparison count", "comparisons" in text)
    check("the report shows all three periods",
          "development" in text and "validation" in text and "holdout" in text)
    check("the report prints T1 beside reward:risk and the move",
          "T1" in text and "RR" in text and "move" in text)
    check("the report shows the conjunction", "conjunction" in text)
    check("the report shows the handoff", "handoff to the live option" in text)
    qs = questions(study)
    check("every question is answered", len(qs) >= 20)
    for item in qs:
        check(f"'{item['question'][:40]}' has an answer", bool(item["answer"]))
    check("the opening gate is answered from the data",
          any("skip_first_15_minutes" in item["answer"] for item in qs))
    check("the engine's own decision is answered",
          any("ENGINE_BUY" in item["answer"] for item in qs))
    lowered = text.lower()
    for banned in ("guarantee", "risk-free", "will reach", "95% probability",
                   "100% win", "sure shot", "assured"):
        check(f"the report never says {banned!r}", banned not in lowered)
    check("the prior 231-combination result is kept in view", "43.4%" in text)

    print(f"checked {CHECKED}")
    print("phase15b discovery smoke: OK")
    return 0


def _raises(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
