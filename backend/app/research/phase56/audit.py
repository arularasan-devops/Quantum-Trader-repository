"""The §36 gate: six fields, one verdict, no strategy.

The gate is deliberately unforgiving in one direction and generous in the other.
Unforgiving: a single MISSING field stops the phase, because the six fields are
not a scorecard to be averaged — each one, missing, invalidates a different part
of the study. Generous: an INCONCLUSIVE probe never stops it, it asks for a
retry, because a throttled provider is not an absent dataset.

What each field decides, so the verdict can be argued with:

* ``UNIVERSE_COVERAGE`` — whether the set of securities is the historical one.
  Missing means every cross-sectional result (§15, §23) is computed on names
  selected for having survived, and ranking survivors by trailing return
  produces a positive backtest from noise;
* ``HISTORICAL_MEMBERSHIP_COVERAGE`` — whether eligibility is knowable as of the
  decision date. Missing means §23's four conditions cannot be enforced;
* ``CORPORATE_ACTION_COVERAGE`` — whether splits, bonuses and demergers are
  recorded. Missing means a price change cannot be classified as a return or an
  artefact, and §5's RAW vs ADJUSTED separation is not constructible;
* ``LISTING_DELISTING_COVERAGE`` — whether a series ending means the company
  disappeared or the provider stopped. Missing means §6's terminal-position
  policy has nothing to key on;
* ``PRICE_SERIES_COVERAGE`` — whether the daily OHLCV window itself is long
  enough for the warm-up plus the study;
* ``COST_SCHEDULE_COVERAGE`` — whether a dated cost model exists. This one does
  not depend on the provider.
"""
from __future__ import annotations

import datetime as dt

from app.research.phase56 import (
    COVERAGE_FULL,
    COVERAGE_NONE,
    COVERAGE_PARTIAL,
    GATE_ADEQUATE,
    GATE_INADEQUATE,
    GATE_INCONCLUSIVE,
    MIN_SESSIONS_PER_SECURITY,
    SERIES_PRE_ADJUSTED,
    SERIES_RAW,
    STUDY_START,
    SURVIVORSHIP_CONTROLLED,
    SURVIVORSHIP_LIMITED,
    SURVIVORSHIP_UNKNOWN,
    VERSION,
    WARMUP_SESSIONS,
    fingerprint,
)
from app.research.phase56 import costs as p56costs
from app.research.phase56 import universe as p56universe

AVAILABLE = "AVAILABLE"
MISSING = "MISSING"
INCONCLUSIVE = "INCONCLUSIVE"


def _field(name: str, state: str, coverage: str, detail: str, **extra) -> dict:
    return {"field": name, "state": state, "coverage": coverage, "detail": detail, **extra}


def assess(
    master: list[dict],
    coverage_probe: dict,
    action_probe: dict,
    vanished_probe: dict,
    *,
    as_of: dt.date | None = None,
) -> dict:
    """Compute the six fields, the survivorship status and the gate."""
    as_of = as_of or dt.date.today()
    equities = p56universe.equity_rows(master)
    statuses = p56universe.status_assignability()
    unassignable = [row["status"] for row in statuses if not row["assignable"]]
    membership = p56universe.membership_sources(master)

    fields: list[dict] = []

    # 1 — universe -----------------------------------------------------------
    vanished_reachable = vanished_probe.get("history_reachable", 0)
    vanished_probed = vanished_probe.get("probed", 0)
    vanished_inconclusive = vanished_probe.get("inconclusive", 0)
    if vanished_probed and vanished_inconclusive == vanished_probed:
        fields.append(
            _field(
                "UNIVERSE_COVERAGE",
                INCONCLUSIVE,
                COVERAGE_NONE,
                "every vanished-security probe failed on the provider rather "
                "than answering; the universe question is unmeasured",
            )
        )
    elif vanished_reachable == vanished_probed and vanished_probed > 0:
        fields.append(
            _field(
                "UNIVERSE_COVERAGE",
                AVAILABLE,
                COVERAGE_FULL,
                f"all {vanished_probed} securities that stopped trading inside "
                "the window remain reachable",
                securities_now=len(equities),
            )
        )
    else:
        fields.append(
            _field(
                "UNIVERSE_COVERAGE",
                MISSING,
                COVERAGE_PARTIAL if vanished_reachable else COVERAGE_NONE,
                f"{vanished_reachable} of {vanished_probed} securities that "
                "stopped trading inside the window are reachable; the master "
                f"lists {len(equities)} present-day '-EQ' names and nothing "
                "that has since ceased to exist, so the only constructible "
                "universe is today's survivors",
                securities_now=len(equities),
                vanished_unreachable=vanished_probed - vanished_reachable,
            )
        )

    # 2 — membership ---------------------------------------------------------
    dated = [row for row in membership if row["dated_membership_available"]]
    fields.append(
        _field(
            "HISTORICAL_MEMBERSHIP_COVERAGE",
            AVAILABLE if dated else MISSING,
            COVERAGE_FULL if dated else COVERAGE_NONE,
            "no source in the §3 hierarchy publishes membership by date; "
            "eligibility as of a historical decision date is not derivable"
            if not dated
            else f"{len(dated)} of {len(membership)} sources publish dated membership",
            sources=membership,
        )
    )

    # 3 — corporate actions --------------------------------------------------
    verdict = action_probe.get("series_verdict")
    graded = action_probe.get("graded", 0)
    if not graded:
        fields.append(
            _field(
                "CORPORATE_ACTION_COVERAGE",
                INCONCLUSIVE,
                COVERAGE_NONE,
                "no corporate-action probe produced a gradeable session pair",
            )
        )
    elif verdict == SERIES_RAW and action_probe.get("action_record_published"):
        fields.append(
            _field(
                "CORPORATE_ACTION_COVERAGE",
                AVAILABLE,
                COVERAGE_FULL,
                "prices arrive raw and the action record is published, so RAW "
                "and ADJUSTED_RESEARCH_PRICE can be derived separately",
            )
        )
    else:
        detail = (
            "the provider applied every probed split and bonus retroactively "
            "and publishes no ex-date, ratio or action type: the series is "
            "silently back-adjusted, RAW_PRICE is unavailable, and an "
            "adjustment cannot be audited or reversed"
            if verdict == SERIES_PRE_ADJUSTED
            else "some probed actions appear raw and others pre-applied, so "
            "neither state can be assumed for an unprobed symbol"
        )
        fields.append(
            _field(
                "CORPORATE_ACTION_COVERAGE",
                MISSING,
                COVERAGE_NONE,
                detail,
                series_verdict=verdict,
                raw_jumps_detected=action_probe.get("raw_jumps_detected"),
                graded=graded,
            )
        )

    # 4 — listing / delisting ------------------------------------------------
    field_avail = p56universe.field_availability()
    missing_fields = [row["field"] for row in field_avail if not row["available"]]
    fields.append(
        _field(
            "LISTING_DELISTING_COVERAGE",
            MISSING if missing_fields else AVAILABLE,
            COVERAGE_NONE if missing_fields else COVERAGE_FULL,
            "the master supplies symbol and token only; "
            f"{', '.join(missing_fields)} are absent, and "
            f"{len(unassignable)} of the seven §2 statuses "
            f"({', '.join(unassignable)}) cannot be assigned at all",
            fields=field_avail,
            unassignable_statuses=unassignable,
        )
    )

    # 5 — price series -------------------------------------------------------
    floor = coverage_probe.get("history_floor_latest")
    sessions_min = coverage_probe.get("sessions_min")
    needed_from = dt.date.fromisoformat(STUDY_START)
    if coverage_probe.get("present", 0) == 0:
        fields.append(
            _field("PRICE_SERIES_COVERAGE", INCONCLUSIVE, COVERAGE_NONE,
                   "no ruler symbol returned a series; coverage is unmeasured")
        )
    else:
        floor_date = dt.date.fromisoformat(floor)
        enough = sessions_min is not None and sessions_min >= MIN_SESSIONS_PER_SECURITY
        covers_warmup = floor_date <= needed_from
        fields.append(
            _field(
                "PRICE_SERIES_COVERAGE",
                AVAILABLE if (enough and covers_warmup) else MISSING,
                COVERAGE_FULL if (enough and covers_warmup) else COVERAGE_PARTIAL,
                f"daily series begin {floor} at the latest and carry "
                f"{sessions_min}–{coverage_probe.get('sessions_max')} sessions; "
                f"the study window opens {STUDY_START} and needs "
                f"{WARMUP_SESSIONS} warm-up sessions before it, which the "
                + ("floor supports" if covers_warmup else "floor does not reach"),
                history_floor=floor,
                sessions_min=sessions_min,
                calls_per_security=1,
            )
        )

    # 6 — cost schedule ------------------------------------------------------
    example = p56costs.breakeven_example()
    fields.append(
        _field(
            "COST_SCHEDULE_COVERAGE",
            AVAILABLE,
            COVERAGE_PARTIAL,
            "a dated schedule is constructible from published statutory rates "
            "and is labelled MODELLED_COST throughout; it is not read back from "
            f"contract notes. Round-trip hurdle at a flat price: "
            f"{example['breakeven_move_pct']}% of turnover",
            schedule=p56costs.schedule(),
            breakeven_example=example,
        )
    )

    # ------------------------------------------------------------- the verdict
    missing = [f["field"] for f in fields if f["state"] == MISSING]
    inconclusive = [f["field"] for f in fields if f["state"] == INCONCLUSIVE]
    if inconclusive and not missing:
        gate = GATE_INCONCLUSIVE
    elif missing:
        gate = GATE_INADEQUATE
    else:
        gate = GATE_ADEQUATE

    if "UNIVERSE_COVERAGE" in inconclusive:
        survivorship = SURVIVORSHIP_UNKNOWN
    elif "UNIVERSE_COVERAGE" in missing or "HISTORICAL_MEMBERSHIP_COVERAGE" in missing:
        survivorship = SURVIVORSHIP_LIMITED
    else:
        survivorship = SURVIVORSHIP_CONTROLLED

    return {
        "version": VERSION,
        "fingerprint": fingerprint(),
        "as_of": as_of.isoformat(),
        "securities_in_master": len(equities),
        "other_nse_series": p56universe.other_nse_series(master),
        "universes": p56universe.universes(master, as_of=as_of),
        "data_available": [f["field"] for f in fields if f["state"] == AVAILABLE],
        "data_missing": missing,
        "data_inconclusive": inconclusive,
        "fields": fields,
        "survivorship_control_status": survivorship,
        "gate": gate,
        "gate_reason": (
            "every §36 field is satisfiable"
            if gate == GATE_ADEQUATE
            else "a probe did not answer; retry before concluding anything"
            if gate == GATE_INCONCLUSIVE
            else "; ".join(missing) + " are missing from this source"
        ),
        "probes": {
            "coverage": coverage_probe,
            "corporate_actions": action_probe,
            "vanished_securities": vanished_probe,
        },
        "study_stages_blocked": _blocked(missing, inconclusive),
    }


def _blocked(missing: list[str], inconclusive: list[str]) -> list[dict]:
    """Which requested sections cannot be run, and which could be."""
    blocked_by = {
        "UNIVERSE_COVERAGE": [
            "§15 CROSS_SECTIONAL_STOCK_SELECTION",
            "§16 PORTFOLIO_CONSTRUCTION",
            "§24 DELISTING_ROBUSTNESS",
            "§29 SURVIVORSHIP_CONTROL_COMPARISON (arm A)",
        ],
        "HISTORICAL_MEMBERSHIP_COVERAGE": [
            "§23 CROSS_SECTIONAL_LEAKAGE_CONTROL",
            "§15 CROSS_SECTIONAL_STOCK_SELECTION",
        ],
        "CORPORATE_ACTION_COVERAGE": [
            "§5 RAW_VS_ADJUSTED_SEPARATION",
            "§11-§14 single-name level studies (a level in rupees is not "
            "comparable across an unrecorded adjustment)",
            "§19 EXIT_FAMILY (previous-low violation, ATR and trailing exits "
            "all key on absolute levels)",
        ],
        "LISTING_DELISTING_COVERAGE": [
            "§6 TERMINAL_POSITION_POLICY",
            "§24 DELISTING_ROBUSTNESS",
        ],
        "PRICE_SERIES_COVERAGE": ["every section"],
    }
    out = []
    for field in missing + inconclusive:
        for section in blocked_by.get(field, []):
            out.append({"section": section, "blocked_by": field})
    return out
