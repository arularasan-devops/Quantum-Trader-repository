"""Could this signal actually be taken? — Phase 12 §4, RESEARCH ONLY.

The strategy answers "is this a good market read". It never answers "is the
contract this read must be expressed in economically takeable", and on 25 Aug
that gap was the whole loss: single-stock option spreads ran at a 9.52% median
of premium, so a plan risking 1R handed a large part of it to the book before
the underlying moved at all.

This module measures, per signal:

* spread, and spread as a percentage of premium;
* spread as a fraction of the plan's own risk (the number that decides whether
  the round trip is affordable at all);
* liquidity, from recorded volume and open interest;
* book quality — was there a two-sided quote to begin with;
* quote freshness, because a stale quote is not evidence of a current spread;
* contract affordability against the configured paper risk budget (§10);
* expected room to the first target, in R.

and returns ``TRADABLE`` / ``CAUTION`` / ``UNTRADABLE`` with the reasons that
produced it.

Nothing here gates, blocks, sizes or scores a production trade. It is attached to
signals as evidence, and the §16 report reads it back. Missing inputs produce
``UNKNOWN`` components and a ``DATA_INCOMPLETE`` reason — never an assumed pass.
"""
from __future__ import annotations

import json
import os
import statistics
import threading
from collections import defaultdict

from app.analysis import entry_quality
from app.analysis.instrument_family import OPTIONS, family
from app.config import settings

TRADABLE = "TRADABLE"
CAUTION = "CAUTION"
UNTRADABLE = "UNTRADABLE"
UNKNOWN = "UNKNOWN"
STATUSES = (TRADABLE, CAUTION, UNTRADABLE, UNKNOWN)

# Reasons. Every one names an observed fact, never a judgement.
NO_BOOK = "NO_TWO_SIDED_QUOTE"
CROSSED_BOOK = "CROSSED_OR_ZERO_BOOK"
SPREAD_WIDE = "SPREAD_WIDE_VS_PREMIUM"
SPREAD_EXTREME = "SPREAD_EXTREME_VS_PREMIUM"
SPREAD_VS_RISK = "SPREAD_LARGE_VS_RISK"
SPREAD_EATS_RISK = "SPREAD_EATS_RISK"
STALE_QUOTE = "STALE_QUOTE"
THIN = "THIN_LIQUIDITY"
NO_LIQUIDITY_DATA = "NO_LIQUIDITY_DATA"
UNAFFORDABLE = "UNAFFORDABLE"
ROOM_TOO_SMALL = "ROOM_BELOW_MIN_R"
NO_RISK = "NON_POSITIVE_RISK"
DATA_INCOMPLETE = "DATA_INCOMPLETE"
# Phase 12A §7: "premium already expanded" is a distinct reason from a wide
# spread — the book can be tight and the leg still be a bad purchase because the
# move being paid for has already happened. Supplied by the entry-quality
# classification, never inferred here.
PREMIUM_EXPANDED = "PREMIUM_ALREADY_EXPANDED"
_EXPANDED_ENTRIES = frozenset(entry_quality.EXPANDED)
SPREAD_VS_TARGET = "SPREAD_LARGE_VS_T1_DISTANCE"
REASONS = (
    NO_BOOK, CROSSED_BOOK, SPREAD_WIDE, SPREAD_EXTREME, SPREAD_VS_RISK,
    SPREAD_EATS_RISK, SPREAD_VS_TARGET, STALE_QUOTE, THIN, NO_LIQUIDITY_DATA,
    UNAFFORDABLE, ROOM_TOO_SMALL, PREMIUM_EXPANDED, NO_RISK, DATA_INCOMPLETE,
)

# Phase 12A §7: a spread worth this fraction of the distance to T1 means the
# round trip is paid for out of the move itself. Research band, not a gate.
SPREAD_VS_T1_CAUTION = 0.25

# Liquidity seeds. Research thresholds, deliberately loose: their job is to
# separate "the tape shows this leg trading" from "nothing traded here", not to
# rank liquid names against each other.
MIN_VOLUME = 100
MIN_OPEN_INTEREST = 500

REPORT_JSON = "instrument_tradability.json"
# Per-instrument sample kept in memory for the report. A restart loses the
# samples; the journal and outcome ledgers remain the durable record.
_MAX_SAMPLES = 5000
_LOCK = threading.Lock()
_samples: dict[str, list[dict]] = defaultdict(list)


def _pct(spread: float, premium: float | None) -> float | None:
    return round(100.0 * spread / premium, 3) if premium else None


def _liquidity(volume: float | None, open_interest: float | None) -> dict:
    if volume is None and open_interest is None:
        return {"volume": None, "open_interest": None, "label": UNKNOWN}
    thin = ((volume is not None and volume < MIN_VOLUME)
            and (open_interest is None or open_interest < MIN_OPEN_INTEREST))
    if volume is None:
        thin = open_interest is not None and open_interest < MIN_OPEN_INTEREST
    return {
        "volume": None if volume is None else float(volume),
        "open_interest": None if open_interest is None else float(open_interest),
        "label": "THIN" if thin else "ADEQUATE",
    }


def _affordability(premium: float | None, risk_points: float | None,
                   lot_size: int | None) -> dict:
    """Can one lot of this contract fit the configured paper risk budget (§10).

    An MCX lot that cannot fit the budget is UNAFFORDABLE — a statement about
    the pot, not a failed signal, and §10 requires it not be counted as one.
    """
    out: dict = {
        "lot_size": lot_size,
        "lot_cost": None,
        "risk_per_lot": None,
        "risk_budget": round(
            settings.shadow_book_capital
            * settings.shadow_book_risk_per_trade_pct / 100.0, 2),
        "capital": settings.shadow_book_capital,
        "lots_affordable": None,
        "affordable": None,
    }
    if not lot_size or not premium:
        return out
    out["lot_cost"] = round(float(premium) * int(lot_size), 2)
    if risk_points and risk_points > 0:
        out["risk_per_lot"] = round(float(risk_points) * int(lot_size), 2)
        by_risk = int(out["risk_budget"] // out["risk_per_lot"])
        by_cost = int(settings.shadow_book_capital // out["lot_cost"])
        out["lots_affordable"] = max(0, min(by_risk, by_cost))
        out["affordable"] = out["lots_affordable"] >= 1
    return out


def assess(instrument: str, *, premium: float | None,
           bid: float | None, ask: float | None,
           risk_points: float | None = None,
           first_target: float | None = None,
           volume: float | None = None,
           open_interest: float | None = None,
           quote_age_sec: float | None = None,
           lot_size: int | None = None,
           delta: float | None = None,
           iv: float | None = None,
           entry_quality: str | None = None,
           vehicle: str = OPTIONS) -> dict:
    """Tradability of one signal. Research evidence; never a production block."""
    reasons: list[str] = []
    fam = family(instrument)

    spread = None
    spread_pct = None
    if bid is None or ask is None:
        reasons.append(NO_BOOK)
    elif float(ask) <= 0 or float(bid) <= 0 or float(ask) < float(bid):
        reasons.append(CROSSED_BOOK)
    else:
        spread = round(float(ask) - float(bid), 4)
        spread_pct = _pct(spread, premium)
        if spread_pct is None:
            reasons.append(DATA_INCOMPLETE)
        elif spread_pct >= settings.tradability_spread_untradable_pct:
            reasons.append(SPREAD_EXTREME)
        elif spread_pct >= settings.tradability_spread_caution_pct:
            reasons.append(SPREAD_WIDE)

    spread_over_risk = None
    if risk_points is not None and float(risk_points) <= 0:
        reasons.append(NO_RISK)
    elif spread is not None and risk_points:
        spread_over_risk = round(spread / float(risk_points), 4)
        if spread_over_risk >= settings.tradability_spread_risk_untradable:
            reasons.append(SPREAD_EATS_RISK)
        elif spread_over_risk >= settings.tradability_spread_risk_caution:
            reasons.append(SPREAD_VS_RISK)

    fresh = None
    if quote_age_sec is not None:
        fresh = float(quote_age_sec) <= settings.tradability_stale_quote_sec
        if not fresh:
            reasons.append(STALE_QUOTE)

    liq = _liquidity(volume, open_interest)
    if liq["label"] == "THIN":
        reasons.append(THIN)
    elif liq["label"] == UNKNOWN:
        reasons.append(NO_LIQUIDITY_DATA)

    aff = _affordability(premium, risk_points, lot_size)
    if aff["affordable"] is False:
        reasons.append(UNAFFORDABLE)

    room_r = None
    if first_target and premium and risk_points and float(risk_points) > 0:
        room_r = round((float(first_target) - float(premium)) / float(risk_points), 3)
        if room_r < settings.tradability_min_room_r:
            reasons.append(ROOM_TOO_SMALL)

    # §7 — the spread against the distance the trade has to travel to pay. A
    # 2-rupee spread is nothing on a 60-rupee move and most of the trade on a
    # 6-rupee one, which spread-as-%-of-premium cannot distinguish.
    spread_over_t1 = None
    if spread is not None and first_target and premium:
        distance = abs(float(first_target) - float(premium))
        if distance > 0:
            spread_over_t1 = round(spread / distance, 4)
            if spread_over_t1 >= SPREAD_VS_T1_CAUTION:
                reasons.append(SPREAD_VS_TARGET)

    if entry_quality in _EXPANDED_ENTRIES:
        reasons.append(PREMIUM_EXPANDED)

    hard = {CROSSED_BOOK, SPREAD_EXTREME, SPREAD_EATS_RISK, UNAFFORDABLE, NO_RISK}
    soft = {SPREAD_WIDE, SPREAD_VS_RISK, SPREAD_VS_TARGET, STALE_QUOTE, THIN,
            ROOM_TOO_SMALL, PREMIUM_EXPANDED}
    if any(r in hard for r in reasons):
        status = UNTRADABLE
    elif NO_BOOK in reasons or DATA_INCOMPLETE in reasons:
        # No two-sided quote is not a narrow spread and not a wide one. It is an
        # unanswerable question, and answering it TRADABLE is how an unpriceable
        # leg reaches a screen looking clean.
        status = UNKNOWN
    elif any(r in reasons for r in soft):
        status = CAUTION
    else:
        status = TRADABLE

    return {
        "instrument": instrument,
        "family": fam,
        "vehicle": vehicle,
        "status": status,
        "reasons": reasons,
        "premium": None if premium is None else float(premium),
        "bid": None if bid is None else float(bid),
        "ask": None if ask is None else float(ask),
        "spread": spread,
        "spread_pct_of_premium": spread_pct,
        "spread_over_risk": spread_over_risk,
        "spread_over_t1_distance": spread_over_t1,
        "delta": None if delta is None else round(float(delta), 4),
        "iv": None if iv is None else round(float(iv), 3),
        "entry_quality": entry_quality,
        "risk_points": None if risk_points is None else float(risk_points),
        "expected_room_r": room_r,
        "liquidity": liq,
        "quote_age_sec": (None if quote_age_sec is None else round(float(quote_age_sec), 1)),
        "quote_fresh": fresh,
        "affordability": aff,
        "research_only": True,
        "note": ("tradability is recorded beside the call as evidence; it does "
                 "not gate, block or size any production trade"),
    }


def record(assessment: dict) -> None:
    """Keep the assessment for the per-instrument studies (§8-§10)."""
    key = str(assessment.get("instrument") or UNKNOWN)
    with _LOCK:
        bucket = _samples[key]
        bucket.append(assessment)
        if len(bucket) > _MAX_SAMPLES:
            del bucket[:-_MAX_SAMPLES]


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return round(ordered[idx], 3)


def instrument_row(instrument: str, rows: list[dict]) -> dict:
    """§8 shape: median / p75 / p90 spread, spread-over-risk and sample size."""
    pcts = [r["spread_pct_of_premium"] for r in rows
            if r.get("spread_pct_of_premium") is not None]
    ratios = [r["spread_over_risk"] for r in rows
              if r.get("spread_over_risk") is not None]
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[str(r.get("status"))] += 1
    return {
        "instrument": instrument,
        "family": family(instrument),
        "samples": len(rows),
        "quoted_samples": len(pcts),
        "spread_pct_median": (round(statistics.median(pcts), 3) if pcts else None),
        "spread_pct_p75": _quantile(pcts, 0.75),
        "spread_pct_p90": _quantile(pcts, 0.90),
        "spread_over_risk_median": (round(statistics.median(ratios), 4)
                                    if ratios else None),
        "status_counts": dict(counts),
        "unaffordable": counts.get(UNTRADABLE, 0) and any(
            UNAFFORDABLE in (r.get("reasons") or []) for r in rows),
    }


def untradable_reasons(rows: list[dict]) -> dict:
    """§7 — *why* the UNTRADABLE calls were untradable, one row per reason.

    26 Aug had 17 UNTRADABLE exits losing ₹39,512 and the summary could only say
    "UNTRADABLE". A single count cannot separate "the book was 12% wide" from
    "one lot does not fit the risk budget" — the first is a market fact about the
    contract, the second is a fact about the size of the pot, and §10 requires
    they never be pooled.

    Reasons are counted per signal, so the totals overlap by design: one call can
    be both SPREAD_EXTREME and THIN_LIQUIDITY, and dropping either would
    misattribute it.
    """
    untradable = [r for r in rows if r.get("status") == UNTRADABLE]
    counts: dict[str, int] = defaultdict(int)
    families: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in untradable:
        for reason in (row.get("reasons") or []):
            if reason in REASONS:
                counts[reason] += 1
                families[str(row.get("family"))][reason] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "untradable_samples": len(untradable),
        "reasons": [
            {"reason": reason, "n": n,
             "pct_of_untradable": (round(100.0 * n / len(untradable), 1)
                                   if untradable else None)}
            for reason, n in ranked
        ],
        "by_family": {f: dict(v) for f, v in sorted(families.items())},
        "reasons_overlap": True,
        "note": ("one signal can carry several reasons, so these counts sum to "
                 "more than untradable_samples; each names an observed fact and "
                 "none of them blocked a production BUY"),
    }


def report() -> dict:
    """Per-instrument and per-family tradability. Research only."""
    with _LOCK:
        snapshot = {k: list(v) for k, v in _samples.items()}
    instruments = [instrument_row(k, v) for k, v in sorted(snapshot.items())]
    fams: dict[str, list[dict]] = defaultdict(list)
    for name, rows in snapshot.items():
        fams[family(name)].extend(rows)
    families = {f: instrument_row(f"ALL_{f}", rows) for f, rows in sorted(fams.items())}
    return {
        "thresholds": {
            "spread_caution_pct": settings.tradability_spread_caution_pct,
            "spread_untradable_pct": settings.tradability_spread_untradable_pct,
            "spread_risk_caution": settings.tradability_spread_risk_caution,
            "spread_risk_untradable": settings.tradability_spread_risk_untradable,
            "stale_quote_sec": settings.tradability_stale_quote_sec,
            "min_room_r": settings.tradability_min_room_r,
            "min_volume": MIN_VOLUME,
            "min_open_interest": MIN_OPEN_INTEREST,
        },
        "instruments": instruments,
        "families": families,
        "untradable_reasons": untradable_reasons(
            [r for rows in snapshot.values() for r in rows]),
        "total_samples": sum(len(v) for v in snapshot.values()),
        "research_only": True,
        "note": ("these thresholds are research seeds read from the 25 Aug "
                 "recorded book; no production gate consults them"),
    }


def write_report() -> str:
    os.makedirs(settings.data_dir, exist_ok=True)
    path = os.path.join(settings.data_dir, REPORT_JSON)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report(), fh, indent=2, default=str)
    os.replace(tmp, path)
    return path


def reset_for_tests() -> None:
    with _LOCK:
        _samples.clear()
