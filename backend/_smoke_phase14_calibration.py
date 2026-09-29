"""Smoke: the score audit must not let a broken score look repairable.

The failure mode this guards against is the flattering one: a score that
carries no information gets a reliability curve fitted to it, the curve is
"applied", and the engine goes on publishing a number that means nothing —
now with a calibration report vouching for it. So the assertions below are
mostly about what the audit must REFUSE to say.
"""
from __future__ import annotations

import datetime as dt

from app.research.phase14 import calibration as cal

CHECKS = 0


def ok(cond: bool, msg: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(msg)


def row(score: float, hit: bool, *, field: str = "win_probability",
        r: float | None = None, d: int = 1, rr: float = 2.0) -> dict:
    """One candidate. ``hit`` means it reached target before stop.

    ``rr`` is the reward:risk the target was set at. It is held constant by
    default so a fixture's inversion is a property of the score rather than of
    how far away its target was.
    """
    out = {
        "session": (dt.date(2025, 1, 1) + dt.timedelta(days=d)).isoformat(),
        "exit_reason": "TARGET" if hit else "STOP",
        "r": (2.0 if hit else -1.0) if r is None else r,
        "reward_risk": rr,
        "trade_score": 50.0, "confidence": 50.0, "opportunity_score": 50.0,
        "htf_strength": 50.0, "risk_score": 50.0, "trap_prob": 8.0,
        "fake_prob": 8.0, "win_probability": 50.0,
    }
    out[field] = score
    return out


def _informative(n: int = 1200, field: str = "win_probability") -> list[dict]:
    """A score that genuinely ranks: high scores hit, low scores miss.

    Level is deliberately WRONG (the score says 90 where 70% hit) so the audit
    has to separate a bad level from a bad ranking.
    """
    out = []
    for i in range(n):
        if i % 3 == 0:
            out.append(row(90.0, i % 10 < 7, field=field, d=(i % 25) + 1))
        elif i % 3 == 1:
            out.append(row(70.0, i % 10 < 4, field=field, d=(i % 25) + 1))
        else:
            out.append(row(55.0, i % 10 < 2, field=field, d=(i % 25) + 1))
    return out


def _uninformative(n: int = 1200, field: str = "win_probability") -> list[dict]:
    """Scores spread 50..97, outcome independent of the score.

    The hit pattern is driven by the row index and the score by a different
    cycle, so the two cannot correlate — the same shape as a score that is
    really just a rescaled indicator reading.
    """
    out = []
    for i in range(n):
        score = 50.0 + (i % 24) * 2.0
        out.append(row(score, i % 100 < 43, field=field, d=(i % 25) + 1))
    return out


def _mechanically_inverted(n: int = 2400) -> list[dict]:
    """The confound, reproduced: the score only looks inverted because a high
    score comes with a FURTHER target.

    This is how the real engine behaves -- ``expected_move`` and
    ``win_probability`` are both built from ``directional_strength`` -- so a
    high-scoring bar is aiming further and reaches target less often for a
    purely arithmetic reason. Inside each reward:risk band here the score says
    nothing at all, which is the truth the control has to recover.
    """
    out = []
    for i in range(n):
        if i % 2 == 0:
            score, rr, base = 90.0, 2.5, 30      # far target, rarely reached
        else:
            score, rr, base = 55.0, 1.2, 60      # near target, often reached
        # Within a band the hit depends on the row, never on the score: the
        # score is jittered independently of the outcome.
        out.append(row(score + (i % 5), i % 100 < base,
                       d=(i % 25) + 1, rr=rr))
    return out


def main() -> None:
    # --- T1 is target-before-stop, never "ended positive" ---------------
    ok(cal._t1({"exit_reason": "TARGET"}) is True, "TARGET is a T1")
    ok(cal._t1({"exit_reason": "STOP"}) is False, "STOP is not a T1")
    for reason in ("TIMEOUT", "SESSION_END", "PROTECT", None):
        ok(cal._t1({"exit_reason": reason, "r": 1.5}) is False,
           f"{reason} above entry never touched target — counting it as T1 "
           f"would flatter the very number being audited")

    # --- a score that ranks but overstates the level is REPAIRABLE -------
    a = cal.audit(_informative(), _informative())
    wp = next(r for r in a["scores"] if r["field"] == "win_probability")
    ok(wp["verdict"] == cal.MISCALIBRATED,
       f"a score that ranks outcomes but overstates them must be reported as "
       f"repairable, got {wp['verdict']}")
    ok(wp["holdout"]["rank_correlation"] > 0.05,
       "the informative fixture must show a positive rank correlation")
    curve = cal.remap(wp["holdout"])
    ok(len(curve) >= 2, "a repairable score must yield a remap curve")
    ok(all(c["should_publish"] <= c["publishes_now"] + 1 for c in curve),
       "the remap must pull an overstated score DOWN toward observed frequency")
    ok(curve[0]["should_publish"] < curve[-1]["should_publish"],
       "the remap must stay monotone — a higher score should still map to a "
       "higher published frequency, or the ordering information is destroyed")

    # --- a score with no information must NOT be called repairable -------
    b = cal.audit(_uninformative(), _uninformative())
    wp2 = next(r for r in b["scores"] if r["field"] == "win_probability")
    ok(wp2["verdict"] == cal.NO_INFORMATION,
       f"a score whose outcome is independent of it must be NO_INFORMATION, "
       f"got {wp2['verdict']} — calling it miscalibrated would imply a remap "
       f"fixes it")
    ok(abs(wp2["holdout"]["rank_correlation"]) < 0.05,
       "the uninformative fixture must not correlate")
    flat = cal.remap(wp2["holdout"])
    if flat:
        spread = max(c["should_publish"] for c in flat) - \
            min(c["should_publish"] for c in flat)
        ok(spread < 10.0,
           "remapping a no-information score must collapse toward the base "
           "rate — that flatness is the finding, not a fix")

    # --- an inverted score is worse than useless, and must say so --------
    inverted = [row(90.0 if i % 2 == 0 else 55.0,
                    i % 2 == 1,  # the HIGH score is the one that misses
                    d=(i % 25) + 1) for i in range(1200)]
    c = cal.audit(inverted, inverted)
    wp3 = next(r for r in c["scores"] if r["field"] == "win_probability")
    ok(wp3["verdict"] == cal.INVERTED,
       f"a score whose high values predict failure must be INVERTED, got "
       f"{wp3['verdict']}")
    ok("selects against you" in wp3["why"],
       "an inverted score must be described as actively harmful to gate on")
    ok("SURVIVES the control" in wp3["why"],
       "a real inversion, measured at one fixed reward:risk, must be reported "
       "as having survived the target-distance control")

    # --- the confound: an inversion that is only a further target --------
    # This is the check that stops the whole exercise recommending the engine
    # flip its gates. Overall the score looks inverted; inside a fixed
    # reward:risk band it says nothing, because all it encoded was how far the
    # target sat. Flipping a gate on that buys back hit rate by aiming shorter.
    mech = _mechanically_inverted()
    m = cal.audit(mech, mech)
    wpm = next(r for r in m["scores"] if r["field"] == "win_probability")
    ok(wpm["holdout"]["effective_correlation"] <= -0.05,
       "the fixture must reproduce the symptom: inverted across the whole book")
    ok(wpm["verdict"] == cal.CONFOUNDED,
       f"an inversion that vanishes once target distance is held still must be "
       f"CONFOUNDED, not INVERTED — got {wpm['verdict']}, which would have the "
       f"engine flip a working gate")
    ok("does NOT survive" in wpm["why"] and "FURTHER target" in wpm["why"],
       "the explanation must name target distance as the cause, or the reader "
       "cannot tell this apart from a real inversion")
    ctrl = wpm["controlled"]
    ok(ctrl["usable_bands"] >= 2,
       "the fixture must give the control at least two bands to compare in")
    ok(ctrl["bands_inverted"] == 0,
       f"no band should be inverted in the mechanical fixture, got "
       f"{ctrl['bands_inverted']}")

    # --- a real inversion must still survive the control ------------------
    # Same two reward:risk bands, but now the high score misses INSIDE each
    # band. The control must not explain away a genuine defect.
    real = []
    for i in range(2400):
        rr = 2.5 if i % 2 == 0 else 1.2
        high = (i // 2) % 2 == 0
        real.append(row(90.0 if high else 55.0, not high,
                        d=(i % 25) + 1, rr=rr))
    rl = cal.audit(real, real)
    wpr = next(r for r in rl["scores"] if r["field"] == "win_probability")
    ok(wpr["verdict"] == cal.INVERTED,
       f"an inversion present inside every reward:risk band is real and must "
       f"survive the control, got {wpr['verdict']} — a control that hides real "
       f"defects is worse than none")
    ok(wpr["controlled"]["bands_inverted"] >= 2,
       "the real fixture must stay inverted in both bands")

    # --- without reward_risk the control cannot clear anything ------------
    no_rr = [{k: v for k, v in t.items() if k != "reward_risk"}
             for t in _mechanically_inverted()]
    nr = cal.audit(no_rr, no_rr)
    wpn = next(r for r in nr["scores"] if r["field"] == "win_probability")
    ok(wpn["verdict"] == cal.CONFOUNDED,
       f"with no reward:risk on the rows the inversion cannot be checked at a "
       f"fixed target distance, so it must stay unproven, got {wpn['verdict']}")
    ok(wpn["controlled"]["usable_bands"] == 0,
       "missing reward_risk must leave the control with no usable band")

    # --- T1 is a consequence of target distance, and must be shown as one --
    # The objective "raise the T1 rate" is only meaningful if T1 is a property
    # of the signal. Where a closer target is hit more often but pays less, the
    # audit has to say so, or selection work chases a number it can buy for free
    # by aiming shorter — and loses money doing it.
    geo_rows = []
    for i in range(4000):
        if i % 2 == 0:                       # near target: often hit, pays 0.8
            geo_rows.append(row(50.0, i % 100 < 60, r=0.8 if i % 100 < 60 else -1.0,
                                d=(i % 25) + 1, rr=0.8))
        else:                                # far target: rarely hit, pays 2.5
            geo_rows.append(row(50.0, i % 100 < 32, r=2.5 if i % 100 < 32 else -1.0,
                                d=(i % 25) + 1, rr=2.5))
    geo = cal.geometry(geo_rows)
    near = next(b for b in geo["bands"] if b["band"] == "rr 0-1")
    far = next(b for b in geo["bands"] if b["band"] == "rr 2-3")
    ok(near["t1_pct"] > far["t1_pct"],
       "a closer target must show the higher hit rate, or the fixture is wrong")
    ok(far["expectancy_r"] > near["expectancy_r"],
       "the far target must show the better expectancy here — that opposition "
       "is the whole point of reporting both")
    ok(geo["t1_and_expectancy_conflict"] is True,
       "when the best hit rate and the best expectancy are different geometries "
       "the audit MUST flag the conflict, or 'maximise T1' silently overrides "
       "'make money'")
    ok(geo["highest_t1_band"] == "rr 0-1" and geo["best_expectancy_band"] == "rr 2-3",
       "the conflict must name which band wins on which measure")

    # and where they agree, it must NOT invent a conflict
    # The near target here wins on BOTH measures outright, not by a tie.
    agree = []
    for i in range(4000):
        if i % 2 == 0:
            hit = i % 100 < 60
            agree.append(row(50.0, hit, r=3.0 if hit else -1.0,
                             d=(i % 25) + 1, rr=0.8))
        else:
            hit = i % 100 < 32
            agree.append(row(50.0, hit, r=2.5 if hit else -1.0,
                             d=(i % 25) + 1, rr=2.5))
    agreed = cal.geometry(agree)
    ok(agreed["highest_t1_band"] == agreed["best_expectancy_band"] == "rr 0-1",
       "the fixture must have one band winning on both measures")
    ok(agreed["t1_and_expectancy_conflict"] is False,
       "a book where one geometry is best on both measures must not be reported "
       "as a conflict — a warning that always fires carries no information")

    ok(all(b["median_reward_risk"] > 0 for b in geo["bands"]),
       "each band must report the target distance it actually contained, not "
       "just the band label, or the reader cannot price the trade-off")

    # --- expectancy is reported next to the hit-rate ranking -------------
    # A further target trades hit rate for payoff, so R is the yardstick that
    # is not distorted by target distance; the audit has to publish it.
    ok(isinstance(ctrl["r_correlation_overall"], float),
       "the control must report the score's rank correlation with R, since a "
       "hit rate alone cannot separate 'worse signal' from 'further target'")

    # --- skill is measured against the base rate, and can be negative ----
    sk = cal.brier(_uninformative(), "win_probability")
    ok(sk["skill"] is not None and sk["skill"] <= 0.0,
       f"a no-information probability cannot beat a flat base-rate forecast, "
       f"got skill {sk['skill']}")
    ok(sk["mean_claim_pct"] > sk["actual_pct"] + 10,
       "the fixture must reproduce the real defect: claims far above reality")
    good = cal.brier(_informative(), "win_probability")
    ok(good["skill"] is not None,
       "a ranking score must still get a skill number")

    # --- a ranking that only holds in one period is not information ------
    split = cal.audit(_informative(), _uninformative())
    wp4 = next(r for r in split["scores"] if r["field"] == "win_probability")
    ok(wp4["verdict"] == cal.NO_INFORMATION,
       f"a ranking present in-sample but absent in holdout must not be graded "
       f"repairable, got {wp4['verdict']}")
    late_only = cal.audit(_uninformative(), _informative())
    wp5 = next(r for r in late_only["scores"] if r["field"] == "win_probability")
    ok(wp5["verdict"] == cal.NO_INFORMATION,
       "a ranking that appears only in the LATER period is period luck too — "
       "chronological evidence has to hold in both halves")

    # --- an inverse meter is graded with its sign flipped ----------------
    # risk_score claims a HIGHER value is worse, so high-risk rows missing is
    # the score working, not failing.
    risk = [row(80.0 if i % 2 == 0 else 20.0, i % 2 == 1,
                field="risk_score", d=(i % 25) + 1) for i in range(1200)]
    d = cal.audit(risk, risk)
    rs = next(r for r in d["scores"] if r["field"] == "risk_score")
    ok(rs["inverse"] is True, "risk_score must be graded as an inverse meter")
    ok(rs["verdict"] == cal.CALIBRATED,
       f"a risk meter whose high values DO predict failure is working, got "
       f"{rs['verdict']}")
    ok(rs["holdout"]["rank_correlation"] < 0,
       "a working risk meter correlates negatively with target-before-stop")
    ok(rs["holdout"]["effective_correlation"] > 0,
       "the effective correlation must flip the sign for an inverse meter, or "
       "a working risk score would be reported as broken")

    # --- a 0-100 meter is not held to a probability's level --------------
    meters = _informative(field="trade_score")
    e = cal.audit(meters, meters)
    ts = next(r for r in e["scores"] if r["field"] == "trade_score")
    ok(ts["is_probability"] is False,
       "trade_score is a conviction meter, not a published probability")
    ok(ts["verdict"] == cal.CALIBRATED,
       f"a meter that ranks outcomes must pass on ranking alone — its number "
       f"claims no frequency, got {ts['verdict']}")
    ok(ts["holdout_brier"] is None,
       "a meter must not be scored with Brier: it never claimed a probability")

    # --- a near-constant score cannot be graded, and must not be guessed -
    flat_rows = [row(8.0, i % 100 < 43, field="fake_prob", d=(i % 25) + 1)
                 for i in range(600)]
    f = cal.audit(flat_rows, flat_rows)
    fp = next(r for r in f["scores"] if r["field"] == "fake_prob")
    ok(fp["verdict"] == cal.NOT_ENOUGH_DATA,
       f"a score with one distinct value has no curve to fit, got "
       f"{fp['verdict']} — that is NOT_ENOUGH_DATA, not 'no information'")
    ok(fp["holdout"]["distinct_values"] == 1,
       "the constant fixture must report a single distinct value")

    # --- thin buckets are excluded from the fit, not silently averaged ---
    thin = _informative(n=1200) + [row(99.0, True, d=1) for _ in range(3)]
    rel = cal.reliability(thin, "win_probability", min_bucket=100)
    ok(any(bkt["thin"] for bkt in rel["buckets"]) or rel["usable_buckets"] >= 2,
       "a three-row bucket must be flagged thin rather than quoted as a rate")
    ok(all(bkt["trades"] >= 100 or bkt["thin"] for bkt in rel["buckets"]),
       "every bucket under the floor must carry the thin flag")

    # --- equal-count buckets, so a saturated score still splits ----------
    lopsided = [row(8.0, i % 100 < 43, field="trap_prob", d=1)
                for i in range(1000)]
    lopsided += [row(78.0, i % 100 < 20, field="trap_prob", d=1)
                 for i in range(200)]
    rel2 = cal.reliability(lopsided, "trap_prob", inverse=True)
    ok(len(rel2["buckets"]) >= 2,
       "a score with 88% of its mass on one value must still produce a "
       "comparison, or the real trap_prob can never be graded")

    # --- empty input must not raise ---------------------------------------
    empty = cal.audit([], [])
    ok(empty["scores"] and all(r["verdict"] == cal.NOT_ENOUGH_DATA
                               for r in empty["scores"]),
       "an empty book must grade every score NOT_ENOUGH_DATA, not crash")
    ok(cal.brier([], "win_probability")["skill"] is None,
       "no rows means no skill number")
    ok(cal.reliability([], "win_probability")["trades"] == 0,
       "an empty reliability curve must report zero rows")

    # --- the audit uses only the score, exit_reason and r -----------------
    # Anything else present on a pool row must be irrelevant to the verdict,
    # so removing it cannot change the answer.
    book = _informative()
    reference = cal.audit(book, book)
    for name in ("mfe_r", "mae_r", "minutes", "bars_held", "exit_price",
                 "exit_ts", "entry_price", "side", "signal", "taken"):
        stripped = [{k: v for k, v in t.items() if k != name} for t in book]
        got = cal.audit(stripped, stripped)
        ok([r["verdict"] for r in got["scores"]]
           == [r["verdict"] for r in reference["scores"]],
           f"removing {name} changed a verdict — the audit is reading a field "
           f"it has no business reading")

    print(f"checked {CHECKS}")
    print("phase 14 calibration smoke: OK")


if __name__ == "__main__":
    main()
