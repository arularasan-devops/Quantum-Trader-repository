"""A+ shadow classifier — Phase 12 §13, RESEARCH / SHADOW ONLY.

The board already answers "is this a good market read". §7 showed that on
25 Aug the read was usually not the problem: of 231 resolved calls, 124 were
VEHICLE_FAILURE — direction fine, premium lost. So A+ does not re-score the
market. It asks a different question over facts already recorded beside the
call:

    was this idea expressed in a contract worth expressing it in?

The label is produced by a fixed, ordered set of checks over recorded evidence
— data quality, spread, vehicle quality, entry quality, room — and reports the
*first* reason it failed, so the rejection is explainable to the row it came
from. There is no fitted probability, no learned weight and no threshold
derived from the outcomes it is scored against; every threshold is a named
research seed in ``settings`` (§13 explicitly forbids a fitted model here).

Ordering is deliberate and is not a ranking of importance: it runs cheapest-
and-most-fundamental first, so a call with no two-sided quote is REJECT_DATA
rather than being judged on a spread that was never observed. A missing input
is never an assumed pass — it produces UNKNOWN, and UNKNOWN is not A+.

Nothing here gates, blocks, sizes, ranks or promotes a production trade, and
no output of this module reaches an order path. §15 requires >= 20 sessions
with >= 5 chronological holdout sessions before any A+ rule is even validated,
let alone promoted; the current data has 6 sessions.
"""
from __future__ import annotations

import json
import os
import statistics
import threading
from collections import defaultdict

from app.analysis import entry_quality, instrument_family, tradability
from app.config import settings

A_PLUS = "A_PLUS"
REJECT_DATA = "REJECT_DATA"
REJECT_SPREAD = "REJECT_SPREAD"
REJECT_VEHICLE = "REJECT_VEHICLE"
REJECT_ENTRY = "REJECT_ENTRY"
REJECT_ROOM = "REJECT_ROOM"
UNKNOWN = "UNKNOWN"

LABELS = (A_PLUS, REJECT_DATA, REJECT_SPREAD, REJECT_VEHICLE, REJECT_ENTRY,
          REJECT_ROOM, UNKNOWN)

# Reasons. Each names the observed fact that produced the label.
NO_BOOK = "NO_TWO_SIDED_QUOTE"
STALE = "STALE_QUOTE"
NO_RISK = "NON_POSITIVE_RISK"
NO_PREMIUM = "NO_PREMIUM"
SPREAD_PCT = "SPREAD_WIDE_VS_PREMIUM"
SPREAD_RISK = "SPREAD_LARGE_VS_RISK"
UNTRADABLE = "TRADABILITY_UNTRADABLE"
THIN = "THIN_LIQUIDITY"
UNAFFORDABLE = "UNAFFORDABLE"
LOW_DELTA = "LOW_DELTA_LEG"
FAMILY_RESEARCH = "FAMILY_RESEARCH_ONLY"
LOW_SCORE = "SCORE_BELOW_MIN"
NOT_ACTIONABLE = "PLAN_NOT_ACTIONABLE"
NO_LEG = "NO_TAKEABLE_LEG"
# Phase 12A §13 — entry location, from the signal-time entry-quality label. A
# leg bought well past its own plan's zone is rejected on the entry check, where
# score and actionability already live, rather than being called a spread fault.
CHASED = "ENTRY_CHASED"
SEVERELY_CHASED = "ENTRY_SEVERELY_CHASED"
ROOM = "ROOM_BELOW_MIN_R"
NO_ROOM_DATA = "NO_ROOM_DATA"

REPORT_JSON = "a_plus_shadow.json"

# §15 sufficiency. A+ cannot be validated, let alone promoted, below this.
MIN_SESSIONS = 20
MIN_HOLDOUT_SESSIONS = 5

_MAX_SAMPLES = 20000
_LOCK = threading.Lock()
_samples: list[dict] = []


def _num(value: object) -> float | None:
    """A float, or None. Never a silently coerced zero."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _data_check(trad: dict, delta: float | None) -> tuple[str | None, list[str]]:
    """Is there enough recorded evidence to judge this leg at all."""
    reasons: list[str] = []
    if trad.get("status") == tradability.UNKNOWN:
        reasons.append(NO_BOOK)
    if trad.get("bid") is None or trad.get("ask") is None:
        if NO_BOOK not in reasons:
            reasons.append(NO_BOOK)
    if trad.get("quote_fresh") is False:
        reasons.append(STALE)
    risk = _num(trad.get("risk_points"))
    if risk is None or risk <= 0:
        reasons.append(NO_RISK)
    if _num(trad.get("premium")) is None:
        reasons.append(NO_PREMIUM)
    return (REJECT_DATA if reasons else None), reasons


def _spread_check(trad: dict) -> tuple[str | None, list[str]]:
    reasons: list[str] = []
    pct = _num(trad.get("spread_pct_of_premium"))
    ratio = _num(trad.get("spread_over_risk"))
    if pct is not None and pct > settings.a_plus_max_spread_pct:
        reasons.append(SPREAD_PCT)
    if ratio is not None and ratio > settings.a_plus_max_spread_over_risk:
        reasons.append(SPREAD_RISK)
    return (REJECT_SPREAD if reasons else None), reasons


def _vehicle_check(trad: dict, delta: float | None,
                   family: str) -> tuple[str | None, list[str]]:
    """Does the contract track the idea, and can the pot hold one lot of it."""
    reasons: list[str] = []
    if trad.get("status") == tradability.UNTRADABLE:
        reasons.append(UNTRADABLE)
    liq = trad.get("liquidity") or {}
    if liq.get("label") == "THIN":
        reasons.append(THIN)
    aff = trad.get("affordability") or {}
    if aff.get("affordable") is False:
        reasons.append(UNAFFORDABLE)
    if delta is not None and abs(delta) < settings.a_plus_min_delta:
        reasons.append(LOW_DELTA)
    # §8: single stocks ran a 9.52% median spread across three sessions. They are
    # not blocked anywhere in production, and they are not A+ candidates either.
    if family == instrument_family.STOCK:
        reasons.append(FAMILY_RESEARCH)
    return (REJECT_VEHICLE if reasons else None), reasons


def _entry_check(score: float | None,
                 plan_actionable: bool | None,
                 has_leg: bool,
                 entry_state: str | None = None) -> tuple[str | None, list[str]]:
    reasons: list[str] = []
    if not has_leg:
        reasons.append(NO_LEG)
    if entry_state == entry_quality.SEVERELY_CHASED:
        reasons.append(SEVERELY_CHASED)
    elif entry_state == entry_quality.CHASED:
        reasons.append(CHASED)
    if score is None or score < settings.a_plus_min_score:
        reasons.append(LOW_SCORE)
    if plan_actionable is False:
        reasons.append(NOT_ACTIONABLE)
    return (REJECT_ENTRY if reasons else None), reasons


def _room_check(trad: dict) -> tuple[str | None, list[str]]:
    room = _num(trad.get("expected_room_r"))
    if room is None:
        return REJECT_ROOM, [NO_ROOM_DATA]
    if room < settings.a_plus_min_room_r:
        return REJECT_ROOM, [ROOM]
    return None, []


def classify(instrument: str, *, tradability_row: dict | None,
             score: float | None = None,
             plan_actionable: bool | None = None,
             has_leg: bool = True,
             delta: float | None = None,
             family: str | None = None,
             entry_state: str | None = None) -> dict:
    """One A+ shadow label for one call, with the reasons that produced it.

    Missing tradability evidence is UNKNOWN, not a pass: a call nobody could
    price is not an A+ candidate, and calling it one is how an unpriceable leg
    reaches a screen looking clean.
    """
    fam = family or instrument_family.family(instrument)
    if not tradability_row:
        return {
            "instrument": instrument,
            "family": fam,
            "label": UNKNOWN,
            "reasons": [NO_BOOK],
            "checks": {},
            "thresholds": _thresholds(),
            "research_only": True,
            "executable": False,
            "note": ("no tradability evidence was recorded for this call, so "
                     "it is UNKNOWN — an unpriceable leg is not an A+ "
                     "candidate"),
        }

    checks: dict[str, dict] = {}
    label: str | None = None
    reasons: list[str] = []
    order = (
        ("data", _data_check(tradability_row, delta)),
        ("spread", _spread_check(tradability_row)),
        ("vehicle", _vehicle_check(tradability_row, delta, fam)),
        ("entry", _entry_check(score, plan_actionable, has_leg, entry_state)),
        ("room", _room_check(tradability_row)),
    )
    for name, (verdict, why) in order:
        checks[name] = {"passed": verdict is None, "reasons": why}
        if verdict is not None and label is None:
            # First failure wins the label, so the rejection points at the fact
            # that caused it. Later checks are still recorded, not short-circuited.
            label, reasons = verdict, why

    return {
        "instrument": instrument,
        "family": fam,
        "label": label or A_PLUS,
        "reasons": reasons,
        "checks": checks,
        "score": score,
        "spread_pct_of_premium": tradability_row.get("spread_pct_of_premium"),
        "spread_over_risk": tradability_row.get("spread_over_risk"),
        "expected_room_r": tradability_row.get("expected_room_r"),
        "tradability_status": tradability_row.get("status"),
        "delta": delta,
        "thresholds": _thresholds(),
        "research_only": True,
        "executable": False,
        "note": ("shadow label over recorded evidence; it does not gate, size, "
                 "rank or promote any production trade"),
    }


def _thresholds() -> dict:
    return {
        "min_score": settings.a_plus_min_score,
        "max_spread_pct": settings.a_plus_max_spread_pct,
        "max_spread_over_risk": settings.a_plus_max_spread_over_risk,
        "min_room_r": settings.a_plus_min_room_r,
        "min_delta": settings.a_plus_min_delta,
    }


def record(row: dict, session: str | None = None) -> None:
    """Keep a label for the §13/§16 report. Memory only; the journal is durable."""
    if not settings.a_plus_shadow:
        return
    with _LOCK:
        _samples.append({
            "instrument": row.get("instrument"),
            "family": row.get("family"),
            "label": row.get("label"),
            "reasons": list(row.get("reasons") or []),
            "session": session,
            "spread_pct_of_premium": row.get("spread_pct_of_premium"),
            "expected_room_r": row.get("expected_room_r"),
            "score": row.get("score"),
        })
        if len(_samples) > _MAX_SAMPLES:
            del _samples[:-_MAX_SAMPLES]


def _bucket(rows: list[dict]) -> dict:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[str(r.get("label"))] += 1
    a_plus = counts.get(A_PLUS, 0)
    spreads = [r["spread_pct_of_premium"] for r in rows
               if r.get("label") == A_PLUS
               and r.get("spread_pct_of_premium") is not None]
    return {
        "samples": len(rows),
        "labels": dict(counts),
        "a_plus": a_plus,
        "a_plus_pct": (round(100.0 * a_plus / len(rows), 2) if rows else None),
        "a_plus_spread_pct_median": (round(statistics.median(spreads), 3)
                                     if spreads else None),
    }


def summarise(rows: list[dict] | None = None) -> dict:
    """Label distribution overall, per family and per instrument (§13, §16 q8/q9)."""
    if rows is None:
        with _LOCK:
            rows = list(_samples)

    by_family: dict[str, list[dict]] = defaultdict(list)
    by_instrument: dict[str, list[dict]] = defaultdict(list)
    reasons: dict[str, int] = defaultdict(int)
    sessions: set[str] = set()
    for r in rows:
        by_family[str(r.get("family"))].append(r)
        by_instrument[str(r.get("instrument"))].append(r)
        for why in r.get("reasons") or []:
            reasons[str(why)] += 1
        if r.get("session"):
            sessions.add(str(r["session"]))

    # q8/q9: a name is an A+ *candidate* only if the label actually appears for
    # it. Everything else is research-only, which is where all of it stays until
    # §15 is satisfied — this is a reading of the sample, not a promotion.
    candidates = sorted(
        name for name, rs in by_instrument.items()
        if any(r.get("label") == A_PLUS for r in rs))
    research_only_names = sorted(set(by_instrument) - set(candidates))

    shortfall = max(0, MIN_SESSIONS - len(sessions))
    return {
        "overall": _bucket(rows),
        "by_family": {k: _bucket(v) for k, v in sorted(by_family.items())},
        "by_instrument": {k: _bucket(v) for k, v in sorted(by_instrument.items())},
        "reason_counts": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "a_plus_candidates": candidates,
        "research_only_instruments": research_only_names,
        "sessions_observed": len(sessions),
        "sessions_required": MIN_SESSIONS,
        "holdout_required": MIN_HOLDOUT_SESSIONS,
        "sessions_shortfall": shortfall,
        "validation_ready": shortfall == 0,
        "answer": ("INSUFFICIENT_SAMPLE" if shortfall else "READY_TO_VALIDATE"),
        "thresholds": _thresholds(),
        "research_only": True,
        "note": ("A+ is a shadow label only. Even at zero shortfall this is a "
                 "readiness statement about validation, never a promotion: no "
                 "production gate, size or route consults it"),
    }


def report() -> dict:
    return summarise()


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
