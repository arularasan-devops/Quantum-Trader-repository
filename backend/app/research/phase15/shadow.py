"""§15-§17 the daily A+ shadow list, its score, and its honest T1 label.

Three rules shape this module, all of them from the spec:

* **never manufacture a signal.** The list is capped, not filled. Zero A+
  candidates on a day is a valid and expected output, so the list is built by
  applying a bar and then truncating, never by taking the best five of whatever
  turned up;
* **never print a probability that has not been calibrated.** The A+ score is a
  rank, and :func:`t1_label` will not emit a percentage unless a calibration
  record proves the number against out-of-sample outcomes. This is enforced in
  code rather than by convention, because "91%" beside a setup that reaches
  target a third of the time is the single most expensive thing this system can
  display;
* **show the components.** A composite that cannot be decomposed cannot be
  argued with, and the spec asks for market/vehicle/entry/tradability/room/data
  quality separately.
"""
from __future__ import annotations

from app.research.phase15 import attribution

# The shadow list's shape. Preferred candidates are the ones that cleared the
# bar; watch candidates are the next ones down, shown so the bar's effect is
# visible, never as tradable calls.
MAX_PREFERRED = 5
MAX_WATCH = 5

# Out-of-sample resolved outcomes a calibration needs before a probability may
# be printed at all, and the largest gap between claimed and observed frequency
# that still counts as calibrated.
MIN_CALIBRATION_SAMPLE = 100
MAX_CALIBRATION_ERROR_PCT = 5.0

NOT_CALIBRATED = "NOT_CALIBRATED"
CALIBRATED = "CALIBRATED"

# Whether the composite's ordering has been shown to sort outcomes at all. A
# list ranked by a score that does not sort is ordered, not prioritised, and the
# rows say which of the two they are.
RANK_NOT_VALIDATED = "RANK_NOT_VALIDATED"
RANK_SORTS_IN_SAMPLE = "RANK_SORTS_IN_SAMPLE"

# Component weights for the A+ composite. Deliberately not derived from the
# existing Signal Score (§16 forbids averaging it): each component is a
# separately measurable dimension, and tradability carries real weight because
# an untradable vehicle makes a perfect market read worthless.
COMPONENTS = ("market_edge", "vehicle_edge", "entry_edge", "tradability",
              "room", "data_quality")
WEIGHTS = {
    "market_edge": 0.25,
    "vehicle_edge": 0.20,
    "entry_edge": 0.15,
    "tradability": 0.25,
    "room": 0.10,
    "data_quality": 0.05,
}


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, round(float(value), 1)))


def composite(components: dict) -> dict:
    """Weighted A+ score with its component contributions shown.

    A missing component is *not* treated as zero and not treated as neutral:
    the weights are renormalised over the components that were actually
    measured, and the ones that were not are named. Scoring an unmeasured
    dimension as 50 invents evidence; scoring it as 0 punishes a candidate for
    a data gap.
    """
    present = {k: _clamp(v) for k, v in components.items()
               if k in COMPONENTS and v is not None}
    missing = [k for k in COMPONENTS if k not in present]
    total_weight = sum(WEIGHTS[k] for k in present)
    if total_weight <= 0:
        return {"score": None, "components": {}, "missing": missing,
                "note": "no component was measurable, so there is no score"}
    contributions = {
        k: round(present[k] * WEIGHTS[k] / total_weight, 1) for k in present}
    return {
        "score": round(sum(contributions.values()), 1),
        "components": present,
        "contributions": contributions,
        "missing": missing,
        "weights_renormalised": bool(missing),
        "note": ("weights renormalised over the measured components; "
                 f"unmeasured: {', '.join(missing)}") if missing else
        "every component measured",
    }


def t1_label(score: float | None, calibration: dict | None = None) -> dict:
    """The only sanctioned way to publish a T1 expectation on a card.

    Returns a SCORE and a RANK band. It returns a probability **only** when a
    calibration record shows the mapping was checked against at least
    ``MIN_CALIBRATION_SAMPLE`` resolved out-of-sample outcomes and its claimed
    rate matched the observed one. Absent that, ``t1_probability`` is ``None``
    and ``probability_status`` says why — so a UI that renders the field can
    never accidentally print an uncalibrated percentage.
    """
    if score is None:
        return {"t1_score": None, "t1_rank": None, "t1_probability": None,
                "probability_status": NOT_CALIBRATED,
                "probability_note": "no A+ score, so nothing to label"}
    band = ("TOP" if score >= 85 else "HIGH" if score >= 70
            else "MID" if score >= 55 else "LOW")
    out = {"t1_score": round(float(score), 1), "t1_rank": band,
           "t1_probability": None, "probability_status": NOT_CALIBRATED,
           "probability_note": (
               "T1 likelihood is published as a score and a rank, not a "
               "percentage: no calibration record has yet matched this score to "
               "an observed out-of-sample target-before-stop rate")}
    if not calibration:
        return out
    sample = int(calibration.get("oos_resolved") or 0)
    observed = calibration.get("observed_t1_pct")
    claimed = calibration.get("claimed_t1_pct")
    if sample < MIN_CALIBRATION_SAMPLE:
        out["probability_note"] = (
            f"the calibration record carries only {sample} resolved "
            f"out-of-sample outcomes; {MIN_CALIBRATION_SAMPLE} are required "
            "before a percentage may be shown")
        return out
    if observed is None or claimed is None:
        out["probability_note"] = (
            "the calibration record is missing its observed or claimed rate, "
            "so the mapping cannot be checked")
        return out
    error = abs(float(claimed) - float(observed))
    if error > MAX_CALIBRATION_ERROR_PCT:
        out["probability_note"] = (
            f"the calibration claims {float(claimed):.1f}% where "
            f"{float(observed):.1f}% was observed over {sample} outcomes — a "
            f"{error:.1f} point error, so the number is not published as a "
            "probability")
        return out
    out["t1_probability"] = round(float(observed), 1)
    out["probability_status"] = CALIBRATED
    out["probability_note"] = (
        f"calibrated: {float(observed):.1f}% observed over {sample} resolved "
        "out-of-sample outcomes, matching the claimed rate to within "
        f"{error:.1f} points")
    return out


def card(candidate: dict, components: dict,
         calibration: dict | None = None) -> dict:
    """One shadow-list row: what it is, what it scored, and what it may claim."""
    comp = composite(components)
    label = t1_label(comp["score"], calibration)
    net = candidate.get("net_r")
    return {
        "instrument": candidate.get("instrument"),
        "side": candidate.get("side"),
        "vehicle": candidate.get("vehicle") or candidate.get("option_symbol"),
        "session": candidate.get("session"),
        "entry_ts": candidate.get("entry_ts"),
        "entry": candidate.get("entry"),
        "stop": candidate.get("stop"),
        "target": candidate.get("target"),
        "reward_risk": candidate.get("reward_risk"),
        "regime": candidate.get("regime"),
        "entry_quality": candidate.get("entry_quality"),
        "expected_hold_min": candidate.get("expected_hold_min"),
        "a_plus_score": comp["score"],
        "components": comp["components"],
        "contributions": comp.get("contributions", {}),
        "unmeasured_components": comp["missing"],
        **label,
        # §19: gross R is never presented as the result when the vehicle's own
        # costs are knowable, and an underlying-only row says so on its face.
        "expected_net_r": net,
        "basis": candidate.get("cost_basis") or "UNDERLYING_ONLY",
        "reason": candidate.get("reason") or [],
    }


def shadow_list(cards: list[dict], *, bar: float,
                ranking: dict | None = None,
                max_preferred: int = MAX_PREFERRED,
                max_watch: int = MAX_WATCH) -> dict:
    """Apply the A+ bar, then truncate. Never pad to a quota.

    Candidates at or above ``bar`` become preferred, up to the cap. Watch rows
    are the next ones down and are labelled as below the bar, because a watch
    row shown without that label is read as a weaker BUY.

    ``ranking`` is a :func:`rank_cohort` result. When it shows the composite does
    not sort outcomes, every row is stamped ``RANK_NOT_VALIDATED``: the list is
    then an ordering of a number that has not been shown to mean anything, and a
    row presented as "score 86.7, TOP" without that stamp is read as a quality
    claim the evidence does not support.
    """
    sorts = bool(ranking.get("sorts_outcomes")) if ranking else None
    status = (RANK_SORTS_IN_SAMPLE if sorts else RANK_NOT_VALIDATED)
    if sorts:
        rank_note = (
            "the composite sorted outcomes in this pool: the top band beat the "
            "bottom band on both target rate and expectancy. That is in-sample "
            "ordering only and still carries no probability")
    elif ranking:
        rank_note = (
            "the composite did NOT sort outcomes in this pool "
            f"(top minus bottom band: {ranking.get('top_minus_bottom_t1_pct')} "
            f"points of target rate, "
            f"{ranking.get('top_minus_bottom_expectancy_r')}R of expectancy), so "
            "a higher score here is not evidence of a better candidate and this "
            "list is an ordering, not a priority")
    else:
        rank_note = (
            "the composite's ordering was not tested in this run, so it is "
            "treated as unvalidated")
    stamp = {"rank_status": status}
    scored = [c for c in cards if c.get("a_plus_score") is not None]
    scored.sort(key=lambda c: float(c["a_plus_score"]), reverse=True)
    qualified = [c for c in scored if float(c["a_plus_score"]) >= bar]
    preferred = qualified[:max_preferred]
    watch = [c for c in scored if c not in preferred][:max_watch]
    return {
        "bar": bar,
        "candidates_considered": len(cards),
        "qualified": len(qualified),
        "preferred": [{**c, **stamp} for c in preferred],
        "watch": [{**c, **stamp, "below_bar": True} for c in watch],
        "truncated": max(0, len(qualified) - len(preferred)),
        "unscored": len(cards) - len(scored),
        "rank_status": status,
        "rank_note": rank_note,
        "note": (
            "zero preferred candidates is a valid result and is not padded from "
            "the watch list; watch rows sit below the A+ bar and are shown for "
            "visibility, not as weaker buys"),
    }


def frequency(pool: list[dict], keep, *, bar_label: str = "A+") -> dict:
    """§17 how often the selector actually fires, per session.

    Answers the question the whole selective-trading premise depends on: if the
    A+ subset produces 0.2 candidates a day, "a few best trades" is not a
    trading style, it is a handful of trades a year.
    """
    sessions: dict[str, int] = {}
    for t in pool:
        s = t.get("session")
        if not s:
            continue
        sessions.setdefault(str(s), 0)
        if keep(t):
            sessions[str(s)] += 1
    if not sessions:
        return {"sessions": 0, "note": "the pool carries no session stamps"}
    counts = sorted(sessions.values())
    n = len(counts)

    def pct(p: float) -> float:
        idx = min(n - 1, max(0, int(round(p / 100.0 * (n - 1)))))
        return float(counts[idx])

    hist = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5+": 0}
    for c in counts:
        hist["5+" if c >= 5 else str(c)] += 1
    return {
        "label": bar_label,
        "sessions": n,
        "total_selected": sum(counts),
        "mean_per_day": round(sum(counts) / n, 2),
        "median_per_day": pct(50),
        "p25_per_day": pct(25),
        "p75_per_day": pct(75),
        "max_per_day": counts[-1],
        "days_with": hist,
        "days_with_pct": {k: round(100.0 * v / n, 1) for k, v in hist.items()},
        "days_with_none_pct": round(100.0 * hist["0"] / n, 1),
        "note": (
            f"{round(100.0 * hist['0'] / n, 1)}% of sessions produced no "
            f"{bar_label} candidate at all; that is the cost of selectivity and "
            "it is reported rather than smoothed into a per-day average"),
    }


def rank_cohort(pool: list[dict], score_of, *, buckets: int = 5) -> dict:
    """Does the A+ score sort outcomes? Reported as ranked bands, not a curve.

    This is what has to be true before the score may ever carry a percentage:
    the top band must reach target more often than the bottom one. It is
    reported per band with the band's own sample size so a 12-candidate top band
    cannot be read as a discovery.
    """
    scored = [(score_of(t), t) for t in pool]
    scored = [(s, t) for s, t in scored if s is not None]
    if not scored:
        return {"bands": [], "note": "no candidate carried a score"}
    scored.sort(key=lambda st: float(st[0]))
    size = max(1, len(scored) // buckets)
    bands = []
    for i in range(buckets):
        chunk = [t for _s, t in scored[i * size:(i + 1) * size]] if i < buckets - 1 \
            else [t for _s, t in scored[i * size:]]
        if not chunk:
            continue
        cap = attribution.capture(chunk)
        rs = [float(t["r"]) for t in chunk]
        vals = [float(s) for s, t in scored[i * size:(i + 1) * size]] if i < buckets - 1 \
            else [float(s) for s, t in scored[i * size:]]
        bands.append({
            "band": i + 1,
            "score_from": round(min(vals), 1),
            "score_to": round(max(vals), 1),
            "candidates": len(chunk),
            "t1_pct": cap["t1_pct"],
            "expectancy_r": round(sum(rs) / len(rs), 3),
        })
    if len(bands) < 2:
        return {"bands": bands, "sorts_outcomes": False,
                "note": "too few bands to compare"}
    top, bottom = bands[-1], bands[0]
    return {
        "bands": bands,
        "top_minus_bottom_t1_pct": round(top["t1_pct"] - bottom["t1_pct"], 1),
        "top_minus_bottom_expectancy_r": round(
            top["expectancy_r"] - bottom["expectancy_r"], 3),
        "sorts_outcomes": bool(top["t1_pct"] > bottom["t1_pct"]
                               and top["expectancy_r"] > bottom["expectancy_r"]),
        "note": (
            "the score may only be mapped to a percentage after this ordering "
            "holds out of sample; a score that does not sort outcomes here "
            "cannot carry a probability no matter how it is rescaled"),
    }
