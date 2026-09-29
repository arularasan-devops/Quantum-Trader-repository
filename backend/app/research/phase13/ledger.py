"""The rows every Phase 13 study reads. RESEARCH ONLY.

One row per resolved option call: the labels and plan it carried at signal time,
joined to what actually happened, costed with that call's own recorded spread.
The join and the cost model are :mod:`app.analysis.label_outcomes`' — reusing them
is deliberate, because two studies deriving "net R" two different ways is how a
report ends up arguing with itself.

Phase 13 adds three things to that row:

* **backfilled entry labels.** ``entry_quality`` and ``entry_state`` only started
  being recorded with the Phase 12A build, so every call before it reads UNKNOWN.
  Both classifiers use signal-time facts only — premium, the published entry zone,
  risk, the first target, ATR — and the journal recorded all of them from the
  start, so the labels can be recomputed for past sessions without hindsight.
  ``labels_backfilled`` says which rows were recomputed rather than recorded;
* **expiry state** (EXPIRY_DAY / PRE_EXPIRY / NON_EXPIRY), for Part 10;
* **the milestone timings** already latched by the journal, so the hold-window and
  time-stop studies never infer a time from a price.

A row is only included when the resolution carries an entry, a risk and a
realised R. Rows whose journal call was never found keep ``labels_recorded`` False
and are counted, never quietly dropped.
"""
from __future__ import annotations

from datetime import date, datetime

from app.analysis import entry_location, entry_quality, instrument_family
from app.analysis import label_outcomes, signal_journal

EXPIRY_DAY = "EXPIRY_DAY"
PRE_EXPIRY = "PRE_EXPIRY"
NON_EXPIRY = "NON_EXPIRY"
EXPIRY_UNKNOWN = "EXPIRY_UNKNOWN"
EXPIRY_STATES = (EXPIRY_DAY, PRE_EXPIRY, NON_EXPIRY, EXPIRY_UNKNOWN)

# Days to expiry that still counts as "into expiry" for Part 10.
PRE_EXPIRY_DTE = 2


def _date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def expiry_state(session: object, expiry: object) -> str:
    """EXPIRY_DAY / PRE_EXPIRY / NON_EXPIRY from the two recorded dates."""
    day = _date(session)
    exp = _date(expiry)
    if day is None or exp is None:
        return EXPIRY_UNKNOWN
    dte = (exp - day).days
    if dte <= 0:
        return EXPIRY_DAY
    return PRE_EXPIRY if dte <= PRE_EXPIRY_DTE else NON_EXPIRY


def _num(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def backfill_labels(call: dict) -> dict:
    """Entry quality and entry state recomputed from one journal call's own facts.

    Signal-time inputs only. Nothing here reads the outcome, the forward path, or
    any field the journal recorded after the signal printed.
    """
    plan = call.get("entry_plan") or {}
    state = call.get("market_state") or {}
    vol = call.get("volatility") or {}
    premium = _num(call.get("premium")) or _num(plan.get("entry_price"))
    quality = entry_quality.classify(
        premium=premium,
        entry_zone_low=_num(plan.get("entry_zone_low")),
        entry_zone_high=_num(plan.get("entry_zone_high")),
        risk_points=_num(plan.get("risk_points")),
        first_target=_num(plan.get("target1")),
        underlying_price=_num(state.get("underlying_ltp")),
        underlying_ref=_num(plan.get("underlying_entry")),
        atr=_num(vol.get("atr_points")),
    )
    location = entry_location.classify(
        premium=premium,
        risk_points=_num(plan.get("risk_points")),
        entry_zone_low=_num(plan.get("entry_zone_low")),
        entry_zone_high=_num(plan.get("entry_zone_high")),
        first_target=_num(plan.get("target1")),
        planned_entry=_num(plan.get("entry_price")),
        entry_quality_state=quality["state"],
        zone_distance_r=quality["zone_distance_r"],
    )
    return {"entry_quality": quality, "entry_location": location}


def _chase_visible_cohorts(rows: list[dict]) -> int:
    """Entry-quality cohorts holding a scorable sample of chase-visible rows.

    Two of these are the minimum for the chased-entry question: one cohort on its
    own cannot show that a later entry pays less.
    """
    counts: dict[str, int] = {}
    for row in rows:
        if not row["chase_facts_present"]:
            continue
        counts[row["entry_quality"]] = counts.get(row["entry_quality"], 0) + 1
    return sum(1 for n in counts.values() if n >= label_outcomes.MIN_COHORT)


def build(session: str | None = None) -> tuple[list[dict], dict]:
    """(rows, coverage) for the recorded ledgers, newest session inclusive."""
    journal = signal_journal.read_journal(limit=None, session=session)
    outcomes = [r for r in signal_journal.read_outcomes(limit=500_000)
                if session is None or r.get("session") == session]
    calls = {str(r.get("signal_id") or ""): r for r in journal
             if str(r.get("signal_id") or "")}
    rows = label_outcomes.joined(journal, outcomes)
    out: list[dict] = []
    backfilled = 0
    recorded = 0
    non_buy = 0
    for row in rows:
        call = calls.get(str(row.get("signal_id") or "")) or {}
        # A WAIT or AVOID row is tracked to outcome as well, and an entry-location
        # label on one would describe an entry nobody was told to take.
        if str(call.get("board_action") or "").upper() not in ("", "BUY"):
            non_buy += 1
            continue
        plan = call.get("entry_plan") or {}
        state = call.get("market_state") or {}
        was_recorded = bool(call.get("entry_quality"))
        labels = backfill_labels(call) if call else {}
        quality = (call.get("entry_quality") if was_recorded
                   else (labels.get("entry_quality") or {}))
        # A session recorded by the 13A build carries its own signal-time state,
        # measured against the live premium; a backfilled one can only see the
        # plan's published entry, which is why it so often reads as at-plan.
        location = (call.get("entry_location")
                    or labels.get("entry_location") or {})
        if was_recorded:
            recorded += 1
        elif call:
            backfilled += 1
        out.append({
            **row,
            "entry_quality": (quality or {}).get("state") or entry_quality.UNKNOWN,
            "entry_quality_reasons": list((quality or {}).get("reasons") or []),
            "zone_distance_r": _num((quality or {}).get("zone_distance_r")),
            "entry_state": location.get("state") or entry_location.UNKNOWN,
            "entry_state_reasons": list(location.get("reasons") or []),
            "pullback_level": _num(location.get("pullback_level")),
            "labels_backfilled": bool(call) and not was_recorded,
            # The premium-path facts only. ATR extension is deliberately excluded:
            # it says the underlying is extended, not that this leg was chased.
            "chase_facts_present": any(
                _num((quality or {}).get(key)) is not None
                for key in ("premium_expansion_pct", "local_range_position",
                            "move_consumed_pct")),
            "zone_distance_zero": _num((quality or {}).get("zone_distance_r")) == 0.0,
            "expiry_state": expiry_state(row.get("session") or call.get("session"),
                                         call.get("expiry")),
            "regime": str(state.get("regime") or "UNKNOWN"),
            "option_type": str(call.get("option_type")
                               or row.get("option_type") or "UNKNOWN").upper(),
            "family": row.get("family")
            or instrument_family.family(str(row.get("instrument") or "")),
            "entry": _num(row.get("entry")) or _num(plan.get("entry_price")),
            "risk_points": _num(row.get("risk_points"))
            or _num(plan.get("risk_points")),
            "target1": _num(plan.get("target1")),
            "production_hold_estimate_min": _num(
                plan.get("expected_holding_minutes")),
        })
    sessions = sorted({str(r.get("session")) for r in out if r.get("session")})
    coverage = {
        "journal_rows": len(journal),
        "outcome_rows": len(outcomes),
        "resolved_rows": len(out),
        "labels_recorded": recorded,
        "labels_backfilled": backfilled,
        "labels_unavailable": sum(1 for r in out if not r["labels_recorded"]),
        "non_buy_resolutions_excluded": non_buy,
        "rows_with_premium_chase_facts": sum(
            1 for r in out if r["chase_facts_present"]),
        "rows_at_exactly_zero_zone_distance": sum(
            1 for r in out if r["zone_distance_zero"]),
        "chase_visible_cohorts": _chase_visible_cohorts(out),
        "sessions": sessions,
        "session_count": len(sessions),
        "note": ("entry labels were recomputed from signal-time journal fields "
                 "for calls recorded before the Phase 12A build; rows whose "
                 "journal call is missing carry no labels and are counted here "
                 "rather than dropped silently"),
        "backfill_limitation": (
            "a backfilled label sees only the zone the plan published, and on a "
            "pre-12A row the recorded premium IS the planned entry, so the "
            "distance between them is zero by construction and almost every row "
            "grades IDEAL_ENTRY / BUY_NOW. That is an artefact of what was "
            "recorded, not evidence that the book was entered well \u2014 the "
            "pre-signal premium expansion, the local premium high and the move "
            "already consumed \u2014 the facts that make a chase visible \u2014 are "
            "only recorded from the Phase 12A build forward; compare "
            "rows_with_premium_chase_facts and "
            "rows_at_exactly_zero_zone_distance against resolved_rows to see how "
            "much of this book can be judged at all. Where they are absent, ATR "
            "extension of the underlying is the only remaining evidence, and an "
            "extended underlying is not the same fact as a chased leg"),
    }
    return out, coverage
