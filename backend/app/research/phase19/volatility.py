"""Which instrument, in which volatility state, actually reached T1. RESEARCH ONLY.

The question this answers is "is a volatile instrument a better trade?", and it
is answered per instrument, because volatility is not comparable across them in
points: 15 points of noise is a quiet minute in CRUDEOIL and a violent one in
NIFTY. Every reading here is therefore normalised to **basis points of the
underlying** and then banded **inside its own instrument**, so a band means "loud
for this instrument" rather than "loud in absolute points".

Two things this module refuses to do.

It does not publish a probability. A band's T1 figure is the share of its own
rows that reached target before stop — a count over a finite sample, not a
forecast, and not something to size a position off. The five-year study has not
calibrated a T1 model, so `t1_pct` is labelled a measured rate throughout.

It does not call a band good because its total is positive. A band has to clear
the sample bar (``MIN_DEV``/``MIN_HOLDOUT``) *and* survive walk-forward *and*
beat the whole-pool baseline. Volatility alone already came back `NO_EFFECT`
once, graded pool-wide; splitting it per instrument is a different question, not
a second attempt at the same one, and it gets the same bar.

R here is gross and, on a replayed spot pool, `UNDERLYING_ONLY`: it is the
underlying's excursion in units of the trade's own risk, not option P&L, and no
brokerage, tax or spread is charged. Absolute point figures are carried beside it
precisely because cost is fixed in rupees while R is not — a band whose median
move is 4 points and a band whose median move is 40 points have the same R and
wildly different odds of clearing a round trip.
"""
from __future__ import annotations

from app.research.phase15 import folds as folds_mod

# Development and holdout rows a band needs before its numbers are quoted as
# evidence rather than as a curiosity. Same bar as the rest of the study: a band
# that reads 71% T1 on 14 rows is telling you about 14 rows.
MIN_DEV = 100
MIN_HOLDOUT = 100

# Four bands, cut at the quartiles of the instrument's own noise distribution.
# Quartiles rather than fixed bps thresholds because the thresholds would then
# encode this pool's volatility level and stop meaning anything in a calmer year.
BANDS = ("QUIET", "NORMAL", "ACTIVE", "WILD")

ENOUGH = "MEASURED"
THIN = "REQUIRES_MORE_DATA"

# Verdicts a band can earn.
CANDIDATE = "SURVIVES_SO_FAR"
NO_EFFECT = "NO_EFFECT"
WORSE = "WORSE_THAN_BASELINE"


def _num(value: object) -> float | None:
    """A finite float, or None. Strings and NaN are not volatility readings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def vol_bps(row: dict) -> float | None:
    """The candidate's recent noise as basis points of the underlying.

    ``noise_points`` is the engine's own median recent range, so this is the
    instrument's live volatility at the moment of the candidate rather than a
    daily statistic bolted on afterwards. Normalising by entry price is what
    makes NIFTY and CRUDEOIL comparable at all.
    """
    noise = _num(row.get("noise_points"))
    entry = _num(row.get("entry"))
    if noise is None or entry is None or entry <= 0 or noise < 0:
        return None
    return round(10_000.0 * noise / entry, 2)


def _quantiles(values: list[float]) -> tuple[float, float, float] | None:
    """The three cut points of a four-way split, or None if too thin to cut."""
    if len(values) < 8:
        return None
    ordered = sorted(values)

    def at(share: float) -> float:
        idx = min(len(ordered) - 1, max(0, int(round(share * (len(ordered) - 1)))))
        return ordered[idx]

    lo, mid, hi = at(0.25), at(0.50), at(0.75)
    if not lo < mid < hi:
        # A degenerate distribution (one repeated noise reading) cannot be cut
        # into four bands, and forcing it would put identical rows in different
        # bands purely by sort order.
        return None
    return (round(lo, 2), round(mid, 2), round(hi, 2))


def cut_points(dev_rows: list[dict]) -> dict[str, tuple[float, float, float]]:
    """Per-instrument band edges, fitted on **development rows only**.

    Frozen before the holdout is touched. Fitting the edges on the whole pool
    would let the holdout's own volatility distribution choose the bands it is
    then graded in, which is leakage even though no outcome is read.
    """
    by_inst: dict[str, list[float]] = {}
    for row in dev_rows:
        inst = row.get("instrument")
        v = vol_bps(row)
        if isinstance(inst, str) and inst and v is not None:
            by_inst.setdefault(inst, []).append(v)
    out: dict[str, tuple[float, float, float]] = {}
    for inst, values in by_inst.items():
        cuts = _quantiles(values)
        if cuts is not None:
            out[inst] = cuts
    return out


def band_of(row: dict, cuts: dict[str, tuple[float, float, float]]) -> str | None:
    """Which volatility band this candidate sat in, or None if unmeasurable.

    An unmeasurable row (no noise reading, or an instrument with no frozen cut
    points) is returned as None and excluded — never quietly dropped into the
    middle band, which would dilute exactly the effect being tested.
    """
    inst = row.get("instrument")
    v = vol_bps(row)
    if not isinstance(inst, str) or v is None:
        return None
    edge = cuts.get(inst)
    if edge is None:
        return None
    lo, mid, hi = edge
    if v <= lo:
        return BANDS[0]
    if v <= mid:
        return BANDS[1]
    if v <= hi:
        return BANDS[2]
    return BANDS[3]


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[mid], 3)
    return round((ordered[mid - 1] + ordered[mid]) / 2.0, 3)


def _points(row: dict) -> float | None:
    """How far the trade travelled in favour, in points of the underlying.

    Carried beside R because cost is charged in rupees, not in R: this is the
    column that says whether a band's move is large enough to pay a round trip
    at all.
    """
    mfe = _num(row.get("mfe_r"))
    risk = _num(row.get("risk"))
    if mfe is None or risk is None or risk <= 0:
        return None
    return round(mfe * risk, 2)


def describe(rows: list[dict]) -> dict:
    """Counts, measured T1 rate and gross expectancy for one cohort."""
    n = len(rows)
    if n == 0:
        return {"rows": 0, "t1_pct": None, "expectancy_r": None,
                "profit_factor": None, "median_mfe_points": None,
                "median_risk_points": None, "sessions": 0}
    rs = [_num(r.get("r")) or 0.0 for r in rows]
    gain = sum(r for r in rs if r > 0)
    loss = -sum(r for r in rs if r < 0)
    t1 = sum(1 for r in rows if r.get("exit_reason") == "TARGET")
    pts = [p for p in (_points(r) for r in rows) if p is not None]
    risks = [v for v in (_num(r.get("risk")) for r in rows) if v is not None]
    return {
        "rows": n,
        # A measured share of this cohort's own rows. Not a probability.
        "t1_pct": round(100.0 * t1 / n, 1),
        "expectancy_r": round(sum(rs) / n, 3),
        "profit_factor": round(gain / loss, 2) if loss > 0 else None,
        "median_mfe_points": _median(pts),
        "median_risk_points": _median(risks),
        "sessions": len({str(r.get("session")) for r in rows if r.get("session")}),
    }


def _verdict(band: dict, baseline_median: float | None) -> str:
    if band["evidence"] != ENOUGH or band["walk_forward"]["verdict"] == THIN:
        return THIN
    wf = band["walk_forward"]
    med = wf.get("median_oos_expectancy_r")
    if med is None:
        return THIN
    if med <= 0:
        return WORSE
    if baseline_median is not None and med <= baseline_median:
        # Positive but no better than trading everything: the band is describing
        # the pool, not selecting within it.
        return NO_EFFECT
    if wf["verdict"] != folds_mod.STABLE:
        return NO_EFFECT
    return CANDIDATE


def grade(dev: list[dict], holdout: list[dict], *,
          folds: int = folds_mod.DEFAULT_FOLDS,
          min_fold_trades: int = folds_mod.MIN_FOLD_TRADES) -> dict:
    """Grade every instrument x volatility band, walk-forward, against baseline.

    ``dev`` fits the band edges and nothing else; ``holdout`` is graded in the
    frozen bands. Walk-forward runs over the combined pool so a band has enough
    rows per fold to be measurable at all, and the whole-pool baseline is
    reported beside every band because "positive" is not the bar — "better than
    taking everything" is.
    """
    cuts = cut_points(dev)
    pool = list(dev) + list(holdout)
    baseline = folds_mod.walk_forward(pool, lambda _t: True, folds=folds,
                                      min_fold_trades=min_fold_trades)
    base_med = baseline.get("median_oos_expectancy_r")

    sessions_total = len({str(r.get("session")) for r in pool if r.get("session")})
    unmeasurable = sum(1 for r in pool if band_of(r, cuts) is None)

    bands: list[dict] = []
    for inst in sorted(cuts):
        for name in BANDS:
            def keep(row: dict, _i: str = inst, _b: str = name) -> bool:
                return row.get("instrument") == _i and band_of(row, cuts) == _b

            dev_rows = [r for r in dev if keep(r)]
            out_rows = [r for r in holdout if keep(r)]
            all_rows = dev_rows + out_rows
            if not all_rows:
                continue
            enough = len(dev_rows) >= MIN_DEV and len(out_rows) >= MIN_HOLDOUT
            row = {
                "instrument": inst,
                "band": name,
                "vol_bps_range": _range_text(cuts[inst], name),
                "dev_rows": len(dev_rows),
                "holdout_rows": len(out_rows),
                "evidence": ENOUGH if enough else THIN,
                "short_by": {
                    "dev": max(0, MIN_DEV - len(dev_rows)),
                    "holdout": max(0, MIN_HOLDOUT - len(out_rows)),
                },
                "development": describe(dev_rows),
                "holdout": describe(out_rows),
                "combined": describe(all_rows),
                # How many days this instrument/band could even furnish a card
                # for: a band that fires on 6% of sessions cannot be the basis of
                # a one-a-day schedule however well it grades.
                "session_share_pct": (
                    round(100.0 * describe(all_rows)["sessions"] / sessions_total, 1)
                    if sessions_total else None),
                "walk_forward": folds_mod.walk_forward(
                    pool, keep, folds=folds, min_fold_trades=min_fold_trades),
            }
            row["verdict"] = _verdict(row, base_med)
            row["gross_only"] = True
            row["cost_basis"] = "UNDERLYING_ONLY"
            bands.append(row)

    return {
        "cut_points_bps": {i: list(c) for i, c in cuts.items()},
        "cut_points_fitted_on": "development rows only, frozen before holdout",
        "sessions": sessions_total,
        "rows": len(pool),
        "rows_without_a_volatility_reading": unmeasurable,
        "min_dev_rows": MIN_DEV,
        "min_holdout_rows": MIN_HOLDOUT,
        "baseline": baseline,
        "bands": bands,
        "instrument_summary": _by_instrument(pool, cuts, base_med, folds,
                                             min_fold_trades),
        "ranking": ranking(bands),
        "instruments_graded": len(cuts),
        "ranking_is_comparable": len(cuts) > 1,
        "publishes_probability": False,
        "research_only": True,
        "notes": [
            "t1_pct is the measured share of a cohort's own rows that reached "
            "target before stop — a count, not a forecast, and not a number to "
            "size a position off",
            "no probability is published and no band is labelled A+ here: the "
            "T1 model is not calibrated, so a '95%' badge would be decoration",
            "R is gross and UNDERLYING_ONLY on a replayed spot pool: no "
            "brokerage, tax or spread is charged, and it is not option P&L",
            "median_mfe_points is carried beside R because cost is fixed in "
            "rupees: two bands with the same R and different point moves have "
            "very different odds of clearing a round trip",
            "volatility graded pool-wide already returned NO_EFFECT; a band "
            "here has to clear the sample bar, survive walk-forward and beat "
            "the whole-pool baseline before it means anything",
            "the ranking orders cohorts that cleared the sample bar by measured "
            "holdout expectancy; a thin cohort is listed unranked rather than "
            "allowed to win on a handful of rows, and a rank is not a "
            "recommendation to trade that instrument",
            "band edges are the development quartiles of each instrument's own "
            "noise-in-bps distribution, so a band means loud for that "
            "instrument, not loud in points",
        ],
    }


def ranking(bands: list[dict]) -> list[dict]:
    """Instrument x band cohorts ordered by measured holdout expectancy.

    Only cohorts that cleared the sample bar are ranked; the rest are returned
    with ``rank: None`` so a thin cohort cannot win by being lucky on 12 rows.
    Ordering is a description of what these rows did, not a recommendation: a
    cohort at the top with 3 of 5 positive folds is a coin toss with a good
    average, which is why the walk-forward verdict travels beside every row.
    """
    graded = [b for b in bands if b["evidence"] == ENOUGH
              and b["holdout"]["expectancy_r"] is not None]
    graded.sort(key=lambda b: b["holdout"]["expectancy_r"], reverse=True)
    out = []
    for i, band in enumerate(graded, start=1):
        out.append(_ranked(band, i))
    for band in bands:
        if band["evidence"] != ENOUGH or band["holdout"]["expectancy_r"] is None:
            out.append(_ranked(band, None))
    return out


def _ranked(band: dict, rank: int | None) -> dict:
    return {
        "rank": rank,
        "instrument": band["instrument"],
        "band": band["band"],
        "vol_bps_range": band["vol_bps_range"],
        "holdout_rows": band["holdout_rows"],
        "holdout_t1_pct": band["holdout"]["t1_pct"],
        "holdout_expectancy_r": band["holdout"]["expectancy_r"],
        "median_mfe_points": band["combined"]["median_mfe_points"],
        "session_share_pct": band["session_share_pct"],
        "walk_forward_verdict": band["walk_forward"]["verdict"],
        "verdict": band["verdict"],
        "evidence": band["evidence"],
        "gross_only": True,
        "cost_basis": "UNDERLYING_ONLY",
    }


def _range_text(cuts: tuple[float, float, float], band: str) -> str:
    lo, mid, hi = cuts
    if band == BANDS[0]:
        return f"<= {lo} bps"
    if band == BANDS[1]:
        return f"{lo}-{mid} bps"
    if band == BANDS[2]:
        return f"{mid}-{hi} bps"
    return f"> {hi} bps"


def _by_instrument(pool: list[dict], cuts: dict[str, tuple[float, float, float]],
                   base_med: float | None, folds: int,
                   min_fold_trades: int) -> list[dict]:
    """The instrument on its own, before volatility is split out.

    Needed to keep the reading honest: if an instrument's whole book grades the
    same as its loudest band, the volatility split found nothing and the
    instrument was the whole story.
    """
    out = []
    for inst in sorted({str(r.get("instrument")) for r in pool
                        if isinstance(r.get("instrument"), str)}):
        rows = [r for r in pool if r.get("instrument") == inst]
        wf = folds_mod.walk_forward(
            pool, lambda r, _i=inst: r.get("instrument") == _i,
            folds=folds, min_fold_trades=min_fold_trades)
        out.append({
            "instrument": inst,
            "has_frozen_bands": inst in cuts,
            "median_vol_bps": _median([v for v in (vol_bps(r) for r in rows)
                                       if v is not None]),
            **describe(rows),
            "walk_forward_verdict": wf["verdict"],
            "median_oos_expectancy_r": wf.get("median_oos_expectancy_r"),
            "beats_baseline": bool(
                wf.get("median_oos_expectancy_r") is not None
                and base_med is not None
                and wf["median_oos_expectancy_r"] > base_med),
            "gross_only": True,
        })
    return out
