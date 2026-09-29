"""Does a score mean what its number says, and does it rank outcomes at all?

``win_probability`` is published to the dashboard, stored in the journal, and
read by downstream gates as a probability. On the five-year candidate pool its
median is ~83 while the actual target-before-stop rate is ~43%, and the
``win_probability >= 60`` gate showed a *negative* lift in both periods. Those
are two different defects and they need separating, because the fixes are
opposite:

* **calibration** -- the level is wrong. The score says 83 where the event
  happens 43% of the time. This is repairable: if the score still *ranks*
  outcomes, a monotone remap turns 83 into the frequency that actually follows
  it, and everything downstream keeps working, only honestly.
* **discrimination** -- the ranking is wrong. Bars scoring 90 resolve no better
  than bars scoring 55. Nothing can repair this: remapping a score that carries
  no information yields the base rate for every bar, and the honest outcome is
  to stop publishing it as a probability and stop gating on it.

So this module measures rank first and level second, and reports a skill score
against the only benchmark that matters -- always predicting the base rate. A
score that cannot beat "43% every time" is not a weak predictor, it is not a
predictor, and a *negative* skill score means acting on it is worse than
ignoring it.

Everything here reads a saved candidate pool. It replays nothing, changes
nothing, and grades only the outcome the score claims to predict:
target-before-stop, taken strictly as TARGET reached before STOP.
"""
from __future__ import annotations

# A bucket thinner than this cannot support a frequency estimate, so it is
# reported with its count but excluded from the fit statistics.
MIN_BUCKET = 100

# Rank correlation below this is treated as no ordering information: the score
# does not sort outcomes, so recalibrating it would only publish the base rate
# under a different name.
MIN_RANK_CORRELATION = 0.05

# Gap between the score's claim and the observed frequency, in percentage
# points, that counts as materially miscalibrated rather than rounding.
MAX_CALIBRATION_GAP = 5.0

CALIBRATED = "CALIBRATED"
MISCALIBRATED = "MISCALIBRATED_BUT_RANKS"
NO_INFORMATION = "NO_INFORMATION"
INVERTED = "INVERTED"
NOT_ENOUGH_DATA = "NOT_ENOUGH_DATA"

# Scores the engine publishes as a percentage chance of success, so the number
# itself is a claim that can be checked against the observed frequency. The
# engine no longer publishes any -- ``win_probability`` was relabelled to a
# conviction meter precisely because this module showed its level was a fiction
# -- but pools saved before the relabel carry the old key, and those rows still
# have a level claim attached to them, so it is still graded as one.
PROBABILITY_SCORES = ("win_probability",)

# Scores published as 0-100 quality/conviction meters. Their level claims
# nothing, so only their ranking is graded.
RANKING_SCORES = ("trade_score", "confidence", "opportunity_score",
                  "conviction_meter", "htf_strength")

# Published as the chance the move is a trap, so a HIGHER value should mean a
# WORSE outcome; graded with the sign flipped.
INVERSE_SCORES = ("risk_score", "trap_prob", "fake_prob")


def _t1(trade: dict) -> bool:
    """Target reached before stop -- the event the score claims to predict.

    Deliberately not "ended up positive": a candidate that times out above
    entry never touched target, and counting it would flatter exactly the
    number being audited.
    """
    return trade.get("exit_reason") == "TARGET"


def _rank_correlation(pairs: list[tuple[float, float]]) -> float:
    """Spearman rank correlation, ties averaged.

    Rank rather than Pearson because a score's *units* are arbitrary -- the
    question is only whether higher scores are followed by more targets, not
    whether the relationship is linear.
    """
    n = len(pairs)
    if n < 2:
        return 0.0

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: values[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[order[j + 1]] == values[order[i]]:
                j += 1
            shared = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = shared
            i = j + 1
        return out

    rx = ranks([p[0] for p in pairs])
    ry = ranks([p[1] for p in pairs])
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    dx = sum((a - mx) ** 2 for a in rx)
    dy = sum((b - my) ** 2 for b in ry)
    if dx <= 0 or dy <= 0:
        return 0.0
    return round(num / (dx * dy) ** 0.5, 4)


def _deciles(values: list[float], buckets: int) -> list[float]:
    """Cut points that split the observed values into equal-count buckets.

    Equal *count* rather than equal width: a score that spends 88% of its rows
    on one value would otherwise produce empty buckets and a reliability curve
    fitted to a handful of rows.
    """
    if not values:
        return []
    ordered = sorted(values)
    cuts: list[float] = []
    for i in range(1, buckets):
        v = ordered[min(len(ordered) - 1, i * len(ordered) // buckets)]
        if not cuts or v > cuts[-1]:
            cuts.append(v)
    return cuts


def reliability(trades: list[dict], field: str, *,
                buckets: int = 10, inverse: bool = False,
                min_bucket: int = MIN_BUCKET) -> dict:
    """Observed target-before-stop rate per score bucket, against the claim.

    ``inverse`` marks a score where a higher number is supposed to predict a
    worse outcome (risk, trap, fake), so its ranking is graded with the sign
    flipped and a positive correlation is the failure.
    """
    rows = [(float(t[field]), 1.0 if _t1(t) else 0.0, float(t.get("r") or 0.0))
            for t in trades if t.get(field) is not None]
    n = len(rows)
    base = round(100.0 * sum(r[1] for r in rows) / n, 1) if n else 0.0

    corr = _rank_correlation([(r[0], r[1]) for r in rows])
    effective = -corr if inverse else corr

    cuts = _deciles([r[0] for r in rows], buckets)
    groups: list[list[tuple[float, float, float]]] = [[] for _ in cuts] + [[]]
    for row in rows:
        idx = 0
        while idx < len(cuts) and row[0] >= cuts[idx]:
            idx += 1
        groups[idx].append(row)

    table = []
    for group in groups:
        if not group:
            continue
        cnt = len(group)
        hits = sum(g[1] for g in group)
        claim = sum(g[0] for g in group) / cnt
        actual = 100.0 * hits / cnt
        table.append({
            "score_min": round(min(g[0] for g in group), 1),
            "score_max": round(max(g[0] for g in group), 1),
            "mean_score": round(claim, 1),
            "trades": cnt,
            "t1_pct": round(actual, 1),
            "expectancy_r": round(sum(g[2] for g in group) / cnt, 4),
            "gap_pct": round(claim - actual, 1),
            "thin": cnt < min_bucket,
        })

    usable = [row for row in table if not row["thin"]]
    return {
        "field": field,
        "trades": n,
        "distinct_values": len({r[0] for r in rows}),
        "base_t1_pct": base,
        "rank_correlation": corr,
        "effective_correlation": round(effective, 4),
        "inverse": inverse,
        "buckets": table,
        "usable_buckets": len(usable),
        "spread_pct": (round(max(r["t1_pct"] for r in usable)
                             - min(r["t1_pct"] for r in usable), 1)
                       if usable else 0.0),
    }


def brier(trades: list[dict], field: str) -> dict:
    """Squared-error skill of a published probability against the base rate.

    The benchmark is the constant base rate, because that is what a user gets
    for free by ignoring the score. ``skill <= 0`` means the number is worse
    than useless -- it is actively misleading, since a reader who trusts 83%
    sizes a position for a 43% event.
    """
    rows = [(float(t[field]) / 100.0, 1.0 if _t1(t) else 0.0)
            for t in trades if t.get(field) is not None]
    n = len(rows)
    if not n:
        return {"trades": 0, "brier": None, "baseline_brier": None,
                "skill": None, "mean_claim_pct": None, "actual_pct": None}
    base = sum(r[1] for r in rows) / n
    score = sum((p - o) ** 2 for p, o in rows) / n
    ref = sum((base - o) ** 2 for _, o in rows) / n
    return {
        "trades": n,
        "brier": round(score, 4),
        "baseline_brier": round(ref, 4),
        "skill": round((ref - score) / ref, 4) if ref > 0 else None,
        "mean_claim_pct": round(100.0 * sum(r[0] for r in rows) / n, 1),
        "actual_pct": round(100.0 * base, 1),
    }


def _verdict(rel: dict, skill: float | None, *,
             min_corr: float, max_gap: float,
             is_probability: bool) -> tuple[str, str]:
    """Rank first, level second -- the order the fixes depend on."""
    if rel["usable_buckets"] < 2:
        return (NOT_ENOUGH_DATA,
                "too few rows outside one bucket to fit a curve — the score "
                "barely varies")

    eff = rel["effective_correlation"]
    if eff <= -min_corr:
        direction = ("higher values predicted a WORSE outcome"
                     if not rel["inverse"] else
                     "higher values predicted a BETTER outcome, the opposite "
                     "of what a risk/trap score means")
        return (INVERTED,
                f"{direction} (rank correlation {eff:+.3f}) — gating on it in "
                f"the published direction selects against you")
    if eff < min_corr:
        return (NO_INFORMATION,
                f"target-before-stop varies only {rel['spread_pct']} points "
                f"from its lowest bucket to its highest (rank correlation "
                f"{eff:+.3f}) — it does not sort outcomes, so recalibrating it "
                f"would publish the base rate under a different name")

    if not is_probability:
        return (CALIBRATED,
                f"ranks outcomes (rank correlation {eff:+.3f}); it is a "
                f"0-100 meter, not a probability, so only the ordering is "
                f"graded")

    gaps = [abs(row["gap_pct"]) for row in rel["buckets"] if not row["thin"]]
    worst = max(gaps) if gaps else 0.0
    if worst <= max_gap:
        return (CALIBRATED,
                f"ranks outcomes and every bucket lands within {worst} points "
                f"of its own claim")
    return (MISCALIBRATED,
            f"ranks outcomes (rank correlation {eff:+.3f}) but overstates the "
            f"level by up to {worst} points — repairable by remapping each "
            f"bucket to the frequency actually observed after it"
            + (f", and its skill against a flat {rel['base_t1_pct']}% "
               f"prediction is {skill:+.3f}" if skill is not None else ""))


# Reward:risk bands the score comparison is run inside. A candidate's target
# distance is derived from the same directional strength the scores are, so
# comparing target-before-stop ACROSS scores compares different targets; these
# bands hold the target roughly still so the score is the only thing varying.
RR_BANDS: tuple[tuple[float, float], ...] = (
    (0.0, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 1e9),
)

# Rank correlation must keep its sign in enough bands, on enough rows, before an
# inversion is called real rather than a by-product of target distance.
MIN_BAND_ROWS = 300
MIN_BANDS_AGREEING = 2

CONFOUNDED = "CONFOUNDED_BY_TARGET_DISTANCE"


def _band_label(lo: float, hi: float) -> str:
    return f"rr {lo:g}-{hi:g}" if hi < 1e9 else f"rr {lo:g}+"


def controlled(trades: list[dict], field: str, *, inverse: bool = False,
               min_band_rows: int = MIN_BAND_ROWS) -> dict:
    """Is the score's ranking still there once target distance is held still?

    The engine builds both the score and the target from the same directional
    strength -- ``expected_move = atr * (1.2 + directional_strength)`` against
    ``win_probability = 50 + 47 * directional_strength * agreement`` -- so a
    high-scoring bar is aiming FURTHER. Target-before-stop then falls as the
    score rises for a purely mechanical reason, and reading that as "the score
    is inverted" would have the engine flip its gates to buy back a hit rate it
    only lost by being more ambitious.

    Inside a reward:risk band the target is roughly fixed, so a ranking that
    survives here is about the signal; one that vanishes was about the target.
    Expectancy is reported alongside because it is not distorted by target
    distance the way a hit rate is -- a further target trades frequency for
    payoff and R already accounts for both.
    """
    rows = [t for t in trades
            if t.get(field) is not None and t.get("reward_risk") is not None]
    bands = []
    for lo, hi in RR_BANDS:
        band = [t for t in rows if lo <= float(t["reward_risk"]) < hi]
        if not band:
            continue
        rel = reliability(band, field, buckets=4, inverse=inverse,
                          min_bucket=min_band_rows // 4)
        pairs = [(float(t[field]), float(t.get("r") or 0.0)) for t in band]
        rs = [float(t.get("r") or 0.0) for t in band]
        rrs = sorted(float(t["reward_risk"]) for t in band)
        wins = [x for x in rs if x > 0]
        losses = [x for x in rs if x < 0]
        bands.append({
            "band": _band_label(lo, hi),
            "trades": len(band),
            "t1_pct": rel["base_t1_pct"],
            # A shorter target buys a higher hit rate and pays less for it, so
            # the hit rate on its own cannot say which band is worth trading.
            "median_reward_risk": round(rrs[len(rrs) // 2], 2),
            "expectancy_r": round(sum(rs) / len(rs), 4),
            "mean_win_r": round(sum(wins) / len(wins), 3) if wins else None,
            "mean_loss_r": round(sum(losses) / len(losses), 3) if losses else None,
            "rank_correlation_t1": rel["rank_correlation"],
            "effective_correlation": rel["effective_correlation"],
            "rank_correlation_r": _rank_correlation(pairs),
            "thin": len(band) < min_band_rows,
            "buckets": rel["buckets"],
        })

    usable = [b for b in bands if not b["thin"]]
    agree_neg = sum(1 for b in usable
                    if b["effective_correlation"] <= -MIN_RANK_CORRELATION)
    agree_pos = sum(1 for b in usable
                    if b["effective_correlation"] >= MIN_RANK_CORRELATION)
    return {
        "field": field,
        "inverse": inverse,
        "bands": bands,
        "usable_bands": len(usable),
        "bands_inverted": agree_neg,
        "bands_positive": agree_pos,
        # R is the honest yardstick once target distance is in play, so it is
        # reported separately from the hit-rate ranking.
        "r_correlation_overall": _rank_correlation(
            [(float(t[field]), float(t.get("r") or 0.0)) for t in rows]),
    }


def geometry(trades: list[dict]) -> dict:
    """Target-before-stop against target distance, with no score involved.

    This exists because "raise the T1 rate" is not a signal-quality objective.
    T1 is largely a consequence of where the target was put: aim closer and more
    candidates reach it, aim further and fewer do. Expectancy is reported beside
    it because the two move in opposite directions -- a closer target is hit more
    often and pays less each time -- so a hit rate quoted without its payoff
    cannot say which geometry is worth trading, and selecting for T1 alone can
    walk straight away from the money.
    """
    rows = [t for t in trades if t.get("reward_risk") is not None]
    out = []
    for lo, hi in RR_BANDS:
        band = [t for t in rows if lo <= float(t["reward_risk"]) < hi]
        if not band:
            continue
        rs = [float(t.get("r") or 0.0) for t in band]
        rrs = sorted(float(t["reward_risk"]) for t in band)
        hits = sum(1 for t in band if _t1(t))
        out.append({
            "band": _band_label(lo, hi),
            "trades": len(band),
            "median_reward_risk": round(rrs[len(rrs) // 2], 2),
            "t1_pct": round(100.0 * hits / len(band), 1),
            "expectancy_r": round(sum(rs) / len(rs), 4),
            "thin": len(band) < MIN_BAND_ROWS,
        })
    usable = [b for b in out if not b["thin"]]
    best_t1 = max(usable, key=lambda b: b["t1_pct"], default=None)
    best_r = max(usable, key=lambda b: b["expectancy_r"], default=None)
    return {
        "bands": out,
        "highest_t1_band": best_t1["band"] if best_t1 else None,
        "best_expectancy_band": best_r["band"] if best_r else None,
        # If the two are not the same band, chasing a hit rate costs expectancy,
        # and the objective has to choose between them explicitly.
        "t1_and_expectancy_conflict": bool(
            best_t1 and best_r and best_t1["band"] != best_r["band"]),
    }


def audit(in_sample: list[dict], holdout: list[dict], *,
          min_bucket: int = MIN_BUCKET,
          min_corr: float = MIN_RANK_CORRELATION,
          max_gap: float = MAX_CALIBRATION_GAP) -> dict:
    """Grade every published score on both periods.

    The verdict is taken from the holdout, and a score whose rank correlation
    flips sign between the periods is downgraded: a ranking that does not
    survive into later data is not a ranking, however good the fit looks on the
    period that suggested it.
    """
    fields = [(f, False, True) for f in PROBABILITY_SCORES]
    fields += [(f, False, False) for f in RANKING_SCORES]
    fields += [(f, True, False) for f in INVERSE_SCORES]

    graded = []
    for field, inverse, is_prob in fields:
        a = reliability(in_sample, field, inverse=inverse, min_bucket=min_bucket)
        b = reliability(holdout, field, inverse=inverse, min_bucket=min_bucket)
        skill = brier(holdout, field)["skill"] if is_prob else None
        verdict, why = _verdict(b, skill, min_corr=min_corr, max_gap=max_gap,
                                is_probability=is_prob)

        stable = (a["effective_correlation"] >= min_corr
                  and b["effective_correlation"] >= min_corr)
        if verdict in (CALIBRATED, MISCALIBRATED) and not stable:
            verdict = NO_INFORMATION
            why = (f"ranked outcomes out of sample ({b['effective_correlation']:+.3f})"
                   f" but not in the earlier period "
                   f"({a['effective_correlation']:+.3f}) — a ranking that does "
                   f"not hold in both periods is period luck, not information")

        # An inversion is the one verdict that would have the engine flip a gate,
        # so it has to survive holding target distance still. The engine derives
        # the target from the same directional strength as the score, so a naive
        # hit-rate comparison across scores compares different targets.
        ctrl = controlled(holdout, field, inverse=inverse)
        if verdict == INVERTED:
            if ctrl["usable_bands"] == 0:
                verdict = CONFOUNDED
                why = (f"{why}. But no reward:risk band held enough rows to "
                       f"check it at a fixed target distance, and the engine "
                       f"builds the target from the same directional strength "
                       f"as the score — so this may be arithmetic, not a signal")
            elif ctrl["bands_inverted"] < min(MIN_BANDS_AGREEING,
                                              ctrl["usable_bands"]):
                verdict = CONFOUNDED
                why = (f"looks inverted overall ({b['effective_correlation']:+.3f}) "
                       f"but the inversion does NOT survive inside a fixed "
                       f"reward:risk band ({ctrl['bands_inverted']} of "
                       f"{ctrl['usable_bands']} bands) — a high score buys a "
                       f"FURTHER target (expected_move scales with the same "
                       f"directional strength), so it reaches target less often "
                       f"by arithmetic. Its rank correlation with R is "
                       f"{ctrl['r_correlation_overall']:+.3f}; flipping a gate "
                       f"on the hit rate would buy back frequency by aiming "
                       f"shorter and gain nothing")
            else:
                why = (f"{why}. This SURVIVES the control: still inverted in "
                       f"{ctrl['bands_inverted']} of {ctrl['usable_bands']} "
                       f"reward:risk bands, so it is not merely aiming further; "
                       f"its rank correlation with R is "
                       f"{ctrl['r_correlation_overall']:+.3f}")

        graded.append({
            "field": field,
            "verdict": verdict,
            "why": why,
            "is_probability": is_prob,
            "inverse": inverse,
            "in_sample": a,
            "holdout": b,
            "holdout_brier": brier(holdout, field) if is_prob else None,
            "controlled": ctrl,
        })

    by_verdict: dict[str, int] = {}
    for row in graded:
        by_verdict[row["verdict"]] = by_verdict.get(row["verdict"], 0) + 1

    return {
        "in_sample_trades": len(in_sample),
        "holdout_trades": len(holdout),
        "base_t1_pct": {
            "in_sample": reliability(in_sample, "trade_score")["base_t1_pct"],
            "holdout": reliability(holdout, "trade_score")["base_t1_pct"],
        },
        # Reported before any score, because it bounds what selection can do:
        # if T1 is set by target distance, no score can raise it.
        "geometry": {
            "in_sample": geometry(in_sample),
            "holdout": geometry(holdout),
        },
        "min_bucket": min_bucket,
        "min_rank_correlation": min_corr,
        "max_calibration_gap_pct": max_gap,
        "by_verdict": by_verdict,
        "scores": graded,
    }


def remap(rel: dict) -> list[dict]:
    """The honest replacement curve: score bucket -> observed frequency.

    Offered as a *proposal* only, and only meaningful when the score ranks
    outcomes; applied to a NO_INFORMATION score it collapses to the base rate
    in every bucket, which is itself the finding rather than a fix.
    """
    return [{"score_min": row["score_min"], "score_max": row["score_max"],
             "publishes_now": row["mean_score"],
             "should_publish": row["t1_pct"], "trades": row["trades"]}
            for row in rel["buckets"] if not row["thin"]]
