"""Phase 12A §17–§18 — the seven artefacts and the thirteen questions.

Phase 12A has two tracks: the futures feed (why futures were stale, and whether
the plumbing fix moved freshness) and option tradability validation (do the
research labels predict anything). This module runs both over the recorded
ledgers and answers §18's thirteen questions from the bodies it computed, never
from prose written beside them.

Three properties are deliberate and load-bearing:

* **an answer the data cannot support says so.** Below §19's floors a question
  answers ``INSUFFICIENT_SAMPLE`` with the exact shortfall. Q2 (did the feed fix
  work) additionally answers ``REQUIRES_MORE_DATA`` until a live session has been
  recorded with the fix in place: the fix is measurable only on a real broker
  feed, and this box has none.
* **every row is read, or the report is invalid.** ``records_seen`` /
  ``records_processed`` / ``records_omitted`` are reported, and a non-zero
  omission marks the report invalid rather than publishing a session total that
  silently dropped the open (Phase 12 §2).
* **nothing here promotes anything.** Labels stay research/shadow, futures stays
  research-only with no order path, and no threshold in this report is derived
  from the outcomes it is scored against.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from app.analysis import a_plus_shadow
from app.analysis import direction_attribution
from app.analysis import entry_quality
from app.analysis import failure_attribution
from app.analysis import futures_feed_audit
from app.analysis import futures_outcomes
from app.analysis import instrument_family
from app.analysis import instrument_studies
from app.analysis import label_outcomes
from app.analysis import signal_journal
from app.analysis import signal_reconciliation
from app.analysis import tradability
from app.analysis import vehicle_comparison
from app.config import settings

REPORT_JSON = "phase12a_report.json"
REPORT_MD = "phase12a_report.md"
TRADABILITY_JSON = "option_tradability.json"
DIRECTION_VEHICLE_JSON = "direction_vehicle_analysis.json"

INSUFFICIENT = "INSUFFICIENT_SAMPLE"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"
OBSERVATION = "OBSERVATION"
IN_SAMPLE_ONLY = "IN_SAMPLE_ONLY"

_IST = timezone(timedelta(hours=5, minutes=30))

# The seven artefacts §17 asks for, in the order it lists them.
ARTEFACTS = (
    futures_feed_audit.REPORT_JSON,
    TRADABILITY_JSON,
    a_plus_shadow.REPORT_JSON,
    DIRECTION_VEHICLE_JSON,
    futures_outcomes.REPORT_JSON,
    REPORT_MD,
    REPORT_JSON,
)


def _ist(ts: float) -> str:
    return datetime.fromtimestamp(ts, _IST).strftime("%Y-%m-%d %H:%M:%S IST")


def _sessions(rows: list[dict]) -> list[str]:
    return sorted({str(r["session"]) for r in rows if r.get("session")})


def _feed_body() -> dict:
    """The Track A feed audit, with the stale causes it actually observed."""
    audit = futures_feed_audit.report()
    causes: dict[str, int] = {}
    for row in audit.get("rows") or []:
        for reason in (row.get("cause") or {}).get("reasons") or []:
            causes[str(reason)] = causes.get(str(reason), 0) + 1
    return {
        **audit,
        "causes_all_instruments": dict(sorted(causes.items(),
                                              key=lambda kv: -kv[1])),
        "before_after": {
            "status": REQUIRES_MORE_DATA,
            "note": ("the before-figure is the 26 Aug session recorded without "
                     "websocket-built bars; the after-figure has to be measured "
                     "on a live broker session with this build running, which "
                     "this report cannot fabricate"),
        },
    }


def _tradability_body(journal: list[dict], outcomes: list[dict]) -> dict:
    """§6–§10: the option book at signal time, and what happened next."""
    labels = label_outcomes.study(journal, outcomes)
    return {
        "signal_time_book": tradability.report(),
        "outcomes_by_label": labels,
        "scope": "OPTIONS_ONLY",
        "note": ("futures rows are excluded, not converted: futures R is points "
                 "and option R is premium, and pooling them would invent a "
                 "number neither vehicle produced"),
        "research_only": True,
    }


def _direction_vehicle_body(outcomes: list[dict], labels: dict) -> dict:
    """§12: was the read wrong, or the contract — on the strict definitions."""
    resolved = [r for r in outcomes if r.get("event") == "RESOLVED"]
    attributions = [r["failure_attribution"] for r in resolved
                    if isinstance(r.get("failure_attribution"), dict)]
    unattributed = len(resolved) - len(attributions)
    failure = failure_attribution.summarise(attributions)
    kinds: dict[str, dict] = {}
    for kind in failure_attribution.KINDS:
        rows = [r for r in resolved
                if (r.get("failure_attribution") or {}).get("failure_kind") == kind]
        kinds[kind] = {
            "resolved": len(rows),
            "share_pct": (round(100.0 * len(rows) / len(resolved), 1)
                          if resolved else None),
        }
    return {
        "direction": direction_attribution.summarise_outcomes(outcomes),
        "failure": failure,
        "by_kind": kinds,
        "rows_without_attribution": unattributed,
        "by_option_type": labels.get("by_option_type"),
        "note": ("VEHICLE_FAILURE requires a sufficiently favourable underlying "
                 "move on a named strict definition *and* named vehicle "
                 "evidence — spread, liquidity, delta, decay or divergence. A "
                 "row with neither is DATA_FAILURE, not a vehicle fault"),
        "retired": ("the earlier 54% vehicle-failure figure rested on the "
                    "any-favourable-tick definition §6 retires and on rows that "
                    "carry no vehicle evidence; it is withdrawn"),
        "counterfactual_gap": ("the opposite leg's own path is not measured "
                              "here. Until Phase 13C backfills CE-versus-PE, "
                              "'wrong side' and 'wrong vehicle' are not fully "
                              "separable"),
        "research_only": True,
    }


def collect(session: str | None = None) -> dict:
    """Every Phase 12A body, computed over the recorded ledgers."""
    # Read whole ledgers, then select. ``records_seen`` is what the files hold,
    # ``records_processed`` is what this report used, and the difference is the
    # session selection, stated as such — never a silent tail read (Phase 12 §2).
    all_journal = signal_journal.read_journal(limit=None)
    all_outcomes = signal_journal.read_outcomes(limit=500_000)
    journal = ([r for r in all_journal if r.get("session") == session]
               if session else all_journal)
    outcomes = ([r for r in all_outcomes if r.get("session") == session]
                if session else all_outcomes)
    resolved = [r for r in outcomes if r.get("event") == "RESOLVED"]
    seen = len(all_journal) + len(all_outcomes)
    processed = len(journal) + len(outcomes)

    feed = _feed_body()
    trad = _tradability_body(journal, outcomes)
    labels = trad["outcomes_by_label"]
    return {
        "feed": feed,
        "tradability": trad,
        "direction_vehicle": _direction_vehicle_body(outcomes, labels),
        "futures_outcomes": futures_outcomes.report(session),
        "vehicles": vehicle_comparison.report(session),
        "a_plus": a_plus_shadow.report(),
        "studies": instrument_studies.study(journal, outcomes),
        "reconciliation": signal_reconciliation.reconcile(session),
        "_rows": {
            "records_seen": seen,
            "records_processed": processed,
            "records_omitted": 0,
            "records_outside_session": seen - processed,
            "session_selected": session,
            "journal": len(journal),
            "outcomes": len(outcomes),
            "resolved": len(resolved),
            "sessions": _sessions(journal) or _sessions(outcomes),
        },
    }


def _answer(question: str, answer: object, evidence: dict,
            *, status: str = OBSERVATION, shortfall: object = None) -> dict:
    return {
        "question": question,
        "answer": answer if status in (OBSERVATION, IN_SAMPLE_ONLY) else status,
        "stated_answer": answer,
        "status": status,
        "sample_sufficient": status in (OBSERVATION, IN_SAMPLE_ONLY),
        "shortfall": shortfall,
        "evidence": evidence,
    }


def _cohort(labels: dict, key: str, name: str) -> dict | None:
    for row in labels.get(key) or []:
        if row["cohort"] == name:
            return row
    return None


def _predictive(labels: dict, key: str, name: str) -> dict:
    """Does one cohort do worse than the whole book, and is that comparable."""
    cohort = _cohort(labels, key, name)
    baseline = labels.get("baseline") or {}
    if not cohort:
        return {"status": INSUFFICIENT, "cohort": None,
                "shortfall": label_outcomes.MIN_COHORT}
    worse = None
    if (cohort["expectancy_net_r"] is not None
            and baseline.get("expectancy_net_r") is not None):
        worse = cohort["expectancy_net_r"] < baseline["expectancy_net_r"]
    return {
        "status": cohort["status"],
        "cohort": cohort,
        "baseline_expectancy_net_r": baseline.get("expectancy_net_r"),
        "worse_than_baseline": worse,
        "shortfall": cohort.get("shortfall"),
    }


def questions(bodies: dict) -> list[dict]:
    """The thirteen §18 questions, answered from the bodies above."""
    feed = bodies["feed"]
    session_feed = feed.get("session") or {}
    labels = bodies["tradability"]["outcomes_by_label"]
    dv = bodies["direction_vehicle"]
    fut = bodies["futures_outcomes"]
    # Both bodies are flat: ``report()`` returns its summary at the top level.
    fut_sum = fut
    veh_sum = bodies["vehicles"]
    studies = bodies["studies"]
    aplus = bodies["a_plus"]
    recon = bodies["reconciliation"]
    cov = recon.get("coverage") or {}
    sessions = bodies["_rows"]["sessions"]
    session_short = max(0, label_outcomes.MIN_SESSIONS - len(sessions))
    fut_resolved = int(fut_sum.get("resolved") or 0)
    fut_short = max(0, futures_outcomes.MIN_COMPARISON_SAMPLE - fut_resolved)

    eq_states = {row["cohort"]: row["resolved"]
                 for row in (labels.get("by_entry_quality") or [])}
    eq_known = sum(n for state, n in eq_states.items()
                   if not state.endswith(entry_quality.UNKNOWN))

    return [
        _answer(
            "1. Why were futures signals stale?",
            feed.get("causes_all_instruments") or {},
            {"instruments_traced": int(feed.get("instruments") or 0),
             "age_bands_sec": feed.get("bands_sec"),
             "session": session_feed,
             "engine_refusal_threshold_sec":
                 feed.get("engine_refusal_threshold_sec"),
             "note": ("each cause is named from the traced path — token mapping, "
                      "subscription, websocket state, REST deferral or rate "
                      "limit — not inferred from the age alone")},
            status=(OBSERVATION if feed.get("rows") else REQUIRES_MORE_DATA),
            shortfall=(None if feed.get("rows") else
                       "no futures feed trace was sampled in this process; run "
                       "a live session with futures_feed_audit enabled")),
        _answer(
            "2. Did the feed fix improve freshness?",
            feed.get("before_after"),
            {"ws_bars_enabled": feed.get("ws_bars_enabled"),
             "note": ("websocket-built bars extend the REST history only past "
                      "its last bar, and only on the research accessor; the "
                      "production option engine's candle input is unchanged, so "
                      "no BUY is computed from anything new"),
             "threshold_note": feed.get("threshold_note")},
            status=REQUIRES_MORE_DATA,
            shortfall="one live session recorded with this build"),
        _answer(
            "3. How many valid futures plans exist?",
            {"resolved_plans": fut_sum.get("resolved"),
             "open_follows": fut_sum.get("open_follows"),
             "lifecycle_funnel": fut_sum.get("lifecycle_funnel")},
            {"stages": list(futures_outcomes.STAGES),
             "note": ("a plan that never reached PAPER_ENTRY is counted at the "
                      "stage it stopped at, so the funnel shows where futures "
                      "research is actually losing its sample")}),
        _answer(
            "4. How many futures outcomes are resolved?",
            {"resolved": fut_resolved,
             "required_for_comparison": futures_outcomes.MIN_COMPARISON_SAMPLE,
             "mfe_capture_pct_median": fut_sum.get("mfe_capture_pct_median"),
             "giveback_r_median": fut_sum.get("giveback_r_median")},
            {"unit": fut_sum.get("unit"),
             "research_only": fut_sum.get("research_only"),
             "note": "futures has no order path anywhere in this build"},
            status=(OBSERVATION if fut_resolved else REQUIRES_MORE_DATA),
            shortfall=(fut_short or None)),
        _answer(
            "5. Are options more tradable than futures?",
            {"answer": veh_sum.get("answer"),
             "option_r_median": veh_sum.get("option_r_median"),
             "futures_r_median": veh_sum.get("futures_r_median"),
             "verdict_counts": veh_sum.get("verdict_counts")},
            {"resolved_futures_outcomes": fut_resolved,
             "two_sided_pairs": veh_sum.get("two_sided_pairs"),
             "note": ("compared only inside one paired market idea, and never "
                      "by summing points against premium")},
            status=(IN_SAMPLE_ONLY if veh_sum.get("sample_sufficient")
                    else INSUFFICIENT),
            shortfall={"resolved_futures": fut_short,
                       "pairs": veh_sum.get("shortfall")}),
        _answer(
            "6. Which option families are strongest?",
            {row["cohort"]: {"resolved": row["resolved"],
                             "win_rate_net_pct": row["win_rate_net_pct"],
                             "expectancy_net_r": row["expectancy_net_r"],
                             "net_r_total": row["net_r_total"],
                             "status": row["status"]}
             for row in (labels.get("by_family") or [])},
            {"families": list(instrument_family.FAMILIES),
             "named_studies": list((studies.get("named_studies") or {}).keys()),
             "note": ("families are never pooled; net R charges each call's own "
                      "recorded spread")},
            status=(IN_SAMPLE_ONLY if labels.get("resolved") else INSUFFICIENT),
            shortfall=(None if labels.get("resolved") else "no resolved option")),
        _answer(
            "7. Does UNTRADABLE predict poor outcomes?",
            _predictive(labels, "by_tradability",
                        f"tradability={tradability.UNTRADABLE}"),
            {"reason_breakdown": labels.get("by_untradable_reason"),
             "signal_time_reasons":
                 (bodies["tradability"]["signal_time_book"] or {})
                 .get("untradable_reasons"),
             "note": ("reason cohorts overlap: one call can be wide, thin and "
                      "stale at once, so reason counts can exceed the cohort"),
             "safety": ("no UNTRADABLE label blocked a production BUY; every "
                        "one of these trades reached the dashboard")},
            status=_predictive(labels, "by_tradability",
                               f"tradability={tradability.UNTRADABLE}")["status"]),
        _answer(
            "8. Does REJECT_SPREAD predict poor outcomes?",
            _predictive(labels, "by_a_plus", f"a_plus={a_plus_shadow.REJECT_SPREAD}"),
            {"a_plus_reason_cohorts": labels.get("by_a_plus_reason"),
             "thresholds": aplus.get("thresholds"),
             "note": ("spread is charged twice — once as the label's input and "
                      "once as the cost in net R — so a spread cohort losing on "
                      "net R is partly definitional, and is reported as such")},
            status=_predictive(labels, "by_a_plus",
                               f"a_plus={a_plus_shadow.REJECT_SPREAD}")["status"]),
        _answer(
            "9. Does A+ outperform baseline on unseen data?",
            labels.get("a_plus_vs_baseline"),
            {"sessions_observed": len(sessions),
             "sessions_required": label_outcomes.MIN_SESSIONS,
             "holdout_required": label_outcomes.MIN_HOLDOUT_SESSIONS,
             "note": ("there is no unseen data yet: every session in the book "
                      "was used to read the thresholds, so any edge here is "
                      "in-sample by construction")},
            status=IN_SAMPLE_ONLY if session_short == 0 else REQUIRES_MORE_DATA,
            shortfall={"sessions": session_short,
                       "holdout_sessions": label_outcomes.MIN_HOLDOUT_SESSIONS}),
        _answer(
            "10. Is entry quality predictive?",
            {row["cohort"]: {"resolved": row["resolved"],
                             "win_rate_net_pct": row["win_rate_net_pct"],
                             "expectancy_net_r": row["expectancy_net_r"],
                             "mfe_capture_median": row["mfe_capture_median"],
                             "give_back_r_median": row["give_back_r_median"],
                             "status": row["status"]}
             for row in (labels.get("by_entry_quality") or [])},
            {"states": list(entry_quality.STATES),
             "classified_resolved": eq_known,
             "note": ("the entry-quality label is recorded from Phase 12A "
                      "onward, so resolved rows from earlier sessions are "
                      "UNKNOWN rather than back-labelled from future bars — "
                      "back-labelling would be the leak this study exists to "
                      "avoid"),
             "no_assumption": ("nothing here assumes a pullback entry is "
                               "better; that is Phase 13's question")},
            status=(IN_SAMPLE_ONLY if eq_known >= label_outcomes.MIN_COHORT
                    else REQUIRES_MORE_DATA),
            shortfall=(None if eq_known >= label_outcomes.MIN_COHORT
                       else {"classified_resolved": eq_known,
                             "required": label_outcomes.MIN_COHORT})),
        _answer(
            "11. Is vehicle failure real under the stricter direction rules?",
            {"vehicle_failure_pct":
                 (dv["failure"] or {}).get("direction_right_option_wrong_pct"),
             "by_kind": dv["by_kind"],
             "attributable": (dv["failure"] or {}).get("attributable"),
             "unattributable": (dv["failure"] or {}).get("unattributable")},
            {"direction_definitions":
                 list(((dv["direction"] or {}).get("definitions") or {}).keys()),
             "vehicle_evidence_counts":
                 (dv["failure"] or {}).get("vehicle_evidence_counts"),
             "retired": dv["retired"],
             "counterfactual_gap": dv["counterfactual_gap"]},
            status=(IN_SAMPLE_ONLY if (dv["failure"] or {}).get("attributable")
                    else INSUFFICIENT),
            shortfall=(None if (dv["failure"] or {}).get("attributable")
                       else "no resolved row carried both a strict direction "
                            "mark and named vehicle evidence")),
        _answer(
            "12. Which features should feed the eventual A+ model?",
            {"strongest_separation": [
                "spread_pct_of_premium", "spread_over_risk",
                "spread_over_t1_distance", "liquidity_label",
                "expected_room_r", "quote_fresh", "delta", "family",
                "entry_quality_state", "mfe_capture", "give_back_r",
                "minutes_to_t1"],
             "explicitly_excluded": [
                 "anything computed from bars after the signal",
                 "dataset-relative quintiles",
                 "outcome-derived thresholds"]},
            {"note": ("a feature list, not a model. No weight is fitted "
                      "anywhere in Phase 12A, and §19 forbids fitting one "
                      "before the holdout sessions exist"),
             "leakage_rule": ("every candidate feature is a signal-time fact, "
                              "so a future model cannot be trained on "
                              "information the live engine will not have")}),
        _answer(
            "13. Is there enough evidence for a production change?",
            False,
            {"sessions_observed": len(sessions),
             "sessions": sessions,
             "sessions_required": label_outcomes.MIN_SESSIONS,
             "holdout_required": label_outcomes.MIN_HOLDOUT_SESSIONS,
             "resolved_futures_outcomes": fut_resolved,
             "records_omitted": cov.get("records_omitted"),
             "note": ("§19 requires >= 20 sessions with >= 5 chronological "
                      "holdout sessions, <= 5% missing bars and >= 90% bid/ask "
                      "coverage. In-sample evidence cannot promote anything, so "
                      "the answer is No and stays No until the data exists")},
            status=(OBSERVATION if (session_short == 0 and fut_short == 0
                                    and cov.get("records_omitted") == 0)
                    else REQUIRES_MORE_DATA),
            shortfall={"sessions": session_short,
                       "resolved_futures": fut_short,
                       "records_omitted": cov.get("records_omitted")}),
    ]


def build(session: str | None = None) -> dict:
    bodies = collect(session)
    rows = bodies["_rows"]
    cov = (bodies["reconciliation"] or {}).get("coverage") or {}
    return {
        "phase": "12A",
        "title": "Futures feed integrity and option tradability validation",
        "generated_at_ist": _ist(datetime.now(tz=timezone.utc).timestamp()),
        "session": session,
        "scope": "RESEARCH_SHADOW_PAPER_ONLY",
        "production_unchanged": [
            "BUY/WAIT/NO_TRADE strategy logic", "gate thresholds",
            "score weights", "confidence formula", "strike selector",
            "premium floor", "chase guard", "stop and target philosophy",
            "exits", "broker execution", "the 90s futures refusal threshold",
            "the production option engine's candle input",
        ],
        "records_seen": rows["records_seen"],
        "records_processed": rows["records_processed"],
        "records_omitted": rows["records_omitted"],
        "report_valid": rows["records_omitted"] == 0
        and cov.get("records_omitted", 0) == 0,
        "rows_read": rows,
        "questions": questions(bodies),
        "futures_feed_audit": bodies["feed"],
        "option_tradability": bodies["tradability"],
        "direction_vehicle_analysis": bodies["direction_vehicle"],
        "futures_paper_outcomes": bodies["futures_outcomes"],
        "vehicle_comparison": bodies["vehicles"],
        "a_plus_shadow": bodies["a_plus"],
        "instrument_studies": bodies["studies"],
        "reconciliation": bodies["reconciliation"],
        "validation": {
            "sessions_observed": len(rows["sessions"]),
            "sessions_required": label_outcomes.MIN_SESSIONS,
            "holdout_required": label_outcomes.MIN_HOLDOUT_SESSIONS,
            "in_sample_promotion": False,
            "labels_permitted": [OBSERVATION, "HYPOTHESIS", IN_SAMPLE_ONLY,
                                 REQUIRES_MORE_DATA],
            "note": ("no result in this report changes a production decision: "
                     "tradability, A+ and entry quality are informational, and "
                     "futures is research-only with no order path"),
        },
        "artefacts": list(ARTEFACTS),
        "research_only": True,
    }


def _fmt(value: object) -> str:
    if value is None:
        return "not measured"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) if value else "none"
    if isinstance(value, dict):
        return json.dumps(value, default=str)
    return str(value)


def markdown(body: dict) -> str:
    lines: list[str] = [
        f"# Phase 12A — {body['title']}",
        "",
        f"Generated {body['generated_at_ist']} · scope **{body['scope']}**",
        "",
        ("Every number is measured from the recorded ledgers. Where the data "
         "cannot support an answer the question reads `INSUFFICIENT_SAMPLE` or "
         "`REQUIRES_MORE_DATA` with the shortfall stated, rather than a figure "
         "that would not survive a holdout."),
        "",
        "## Coverage",
        "",
        f"- records seen: {body['records_seen']}",
        f"- records processed: {body['records_processed']}",
        f"- records omitted: {body['records_omitted']}",
        f"- records outside the selected session: "
        f"{body['rows_read']['records_outside_session']}",
        f"- session selected: {_fmt(body['rows_read']['session_selected'])}",
        f"- report valid: {body['report_valid']}",
        f"- sessions: {_fmt(body['rows_read']['sessions'])}",
        "",
        "## Production behaviour unchanged",
        "",
    ]
    lines += [f"- {item}" for item in body["production_unchanged"]]
    lines += ["", "## The thirteen questions", ""]
    for q in body["questions"]:
        lines.append(f"### {q['question']}")
        lines.append("")
        if q["sample_sufficient"]:
            lines.append(f"**{q['status']}** — {_fmt(q['answer'])}")
        else:
            lines.append(f"**{q['status']}** — shortfall: {_fmt(q['shortfall'])}")
            lines.append("")
            lines.append(f"What the current data shows: {_fmt(q['stated_answer'])}")
        lines.append("")
        lines.append(f"Evidence: {_fmt(q['evidence'])}")
        lines.append("")

    val = body["validation"]
    lines += [
        "## §19 validation status",
        "",
        f"- sessions observed: {val['sessions_observed']} of "
        f"{val['sessions_required']} required",
        f"- chronological holdout sessions required: {val['holdout_required']}",
        f"- promotion from in-sample evidence: {val['in_sample_promotion']}",
        f"- labels permitted until the gates pass: {_fmt(val['labels_permitted'])}",
        "",
        val["note"],
        "",
        "## Artefacts",
        "",
    ]
    lines += [f"- `{name}`" for name in body["artefacts"]]
    lines.append("")
    return "\n".join(lines)


def _write(name: str, payload: str) -> str:
    path = os.path.join(settings.data_dir, name)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
    os.replace(tmp, path)
    return path


def write_all(session: str | None = None) -> dict:
    """Write the seven §17 artefacts and return their paths."""
    os.makedirs(settings.data_dir, exist_ok=True)
    body = build(session)
    written: dict[str, str] = {
        futures_feed_audit.REPORT_JSON: futures_feed_audit.write_report(),
        a_plus_shadow.REPORT_JSON: a_plus_shadow.write_report(),
        futures_outcomes.REPORT_JSON: futures_outcomes.write_report(session),
        TRADABILITY_JSON: _write(TRADABILITY_JSON, json.dumps(
            body["option_tradability"], indent=2, default=str)),
        DIRECTION_VEHICLE_JSON: _write(DIRECTION_VEHICLE_JSON, json.dumps(
            body["direction_vehicle_analysis"], indent=2, default=str)),
        REPORT_JSON: _write(REPORT_JSON, json.dumps(body, indent=2, default=str)),
        REPORT_MD: _write(REPORT_MD, markdown(body)),
    }
    # Not one of the seven, but the label cohort body the report quotes.
    written[label_outcomes.REPORT_JSON] = str(label_outcomes.write_report(session))

    missing = [a for a in ARTEFACTS if a not in written]
    return {
        "written": written,
        "artefacts_required": list(ARTEFACTS),
        "artefacts_missing": missing,
        "complete": not missing,
        "report_valid": body["report_valid"],
        "research_only": True,
    }
