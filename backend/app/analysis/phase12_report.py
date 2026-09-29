"""Phase 12 §16 — the eight artefacts and the ten questions, answered honestly.

This module runs the Phase 12 research modules over the recorded ledgers, writes
each one's artefact, and then answers the ten §16 questions from those bodies —
not from prose written alongside them. Every answer carries the evidence it was
derived from, and an answer that the current data cannot support is
``INSUFFICIENT_SAMPLE`` with the exact shortfall rather than a number.

That matters most for questions 7 and 10. §15 requires >= 20 sessions with >= 5
chronological holdout sessions, adequate bid/ask and an adequate resolved
futures sample. The recorded book currently holds six sessions, so those two
questions are answerable only as "not yet", and this module is written so that
saying anything else would require changing it.

Nothing here promotes, gates, sizes or routes anything. It reads ledgers and
writes files.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from app.analysis import a_plus_shadow
from app.analysis import direction_attribution
from app.analysis import failure_attribution
from app.analysis import futures_outcomes
from app.analysis import futures_rollover
from app.analysis import instrument_family
from app.analysis import instrument_studies
from app.analysis import signal_churn
from app.analysis import signal_journal
from app.analysis import signal_reconciliation
from app.analysis import tradability
from app.analysis import vehicle_comparison
from app.config import settings

REPORT_JSON = "phase12_report.json"
REPORT_MD = "phase12_report.md"

INSUFFICIENT = "INSUFFICIENT_SAMPLE"
_IST = timezone(timedelta(hours=5, minutes=30))

# The eight artefacts §16 asks for, in the order it lists them.
ARTEFACTS = (
    REPORT_MD,
    REPORT_JSON,
    signal_churn.REPORT_JSON,
    vehicle_comparison.REPORT_JSON,
    direction_attribution.REPORT_JSON,
    tradability.REPORT_JSON,
    futures_rollover.REPORT_JSON,
    futures_outcomes.REPORT_JSON,
)


def _ist(ts: float) -> str:
    return datetime.fromtimestamp(ts, _IST).strftime("%Y-%m-%d %H:%M:%S IST")


def _sessions(rows: list[dict]) -> list[str]:
    return sorted({str(r["session"]) for r in rows if r.get("session")})


def _trade(row: dict) -> dict:
    """The recorded facts of one resolved signal, for §7 attribution."""
    return {
        "realized_r": row.get("realized_r"),
        "mfe_r": row.get("mfe_r"),
        "spread_pct_of_premium": row.get("spread_pct_of_premium"),
        "spread_over_risk": row.get("spread_over_risk"),
        "delta": row.get("delta"),
        "thin_liquidity": row.get("thin_liquidity"),
        "expected_room_r": row.get("expected_room_r"),
        "book_quoted": row.get("book_quoted"),
        "underlying_recorded": row.get("underlying_recorded"),
        "underlying_favorable_move": row.get("underlying_favorable_move"),
    }


def _failures(resolved: list[dict]) -> tuple[list[dict], int]:
    """Attribution per resolved signal, attributed now if it was not then.

    Rows recorded before §6 landed carry no direction marks. Those are
    attributed with the marks absent, which lands them in DATA_FAILURE rather
    than inheriting the retired "the underlying moved our way by any amount"
    verdict — the definition §6 exists to retire.
    """
    rows: list[dict] = []
    reattributed = 0
    for row in resolved:
        existing = row.get("failure_attribution")
        if isinstance(existing, dict):
            rows.append(existing)
            continue
        reattributed += 1
        rows.append(failure_attribution.classify(
            verdicts=(row.get("direction_attribution")
                      if isinstance(row.get("direction_attribution"), dict)
                      else None),
            trade=_trade(row),
            filled=row.get("filled") if isinstance(row.get("filled"), bool)
            else None,
        ))
    return rows, reattributed


def _churn_baseline(session: str | None) -> dict:
    """What the lifecycle ledger says the churn was before control landed.

    §1 asks for before versus after. The in-process churn tracker only knows
    the episodes this process saw, so the "before" number has to come from the
    recorded ledger of a session that ran without churn control.
    """
    from app.analysis import signal_lifecycle as lc

    records, coverage = lc.scan_ledger(session=session, limit=None)
    episodes: dict[str, int] = {}
    for rec in records:
        key = str(rec.get("episode_id") or rec.get("global_signal_id") or "")
        if key:
            episodes[key] = episodes.get(key, 0) + 1
    raw = sum(episodes.values())
    noisiest = sorted(episodes.items(), key=lambda kv: kv[1], reverse=True)[:10]
    return {
        "source": "signal_lifecycle.jsonl",
        "complete": coverage["records_omitted"] == 0,
        "episodes": len(episodes),
        "raw_events": raw,
        "events_per_episode": (round(raw / len(episodes), 2) if episodes
                               else None),
        "noisiest_episodes": [{"episode_id": e, "raw_events": n}
                              for e, n in noisiest],
        "note": ("a recorded session's raw event count per episode; the "
                 "after-figure is the live churn tracker below, measured on "
                 "the episodes this process published"),
    }


def collect(session: str | None = None) -> dict:
    """Every Phase 12 body, computed over the recorded ledgers."""
    journal = signal_journal.read_journal(limit=500_000)
    outcomes = signal_journal.read_outcomes(limit=500_000)
    if session:
        journal = [r for r in journal if r.get("session") == session]
        outcomes = [r for r in outcomes if r.get("session") == session]
    resolved = [r for r in outcomes if r.get("event") == "RESOLVED"]
    failures, reattributed = _failures(resolved)

    churn = signal_churn.report()
    churn["recorded_baseline"] = _churn_baseline(session)

    return {
        "churn": churn,
        "reconciliation": signal_reconciliation.reconcile(session),
        "direction": direction_attribution.summarise_outcomes(outcomes),
        "failure": failure_attribution.summarise(failures),
        "studies": instrument_studies.study(journal, outcomes),
        "tradability": tradability.report(),
        "rollover": futures_rollover.report(),
        "futures_outcomes": futures_outcomes.report(session),
        "vehicles": vehicle_comparison.report(session),
        "a_plus": a_plus_shadow.report(),
        "_rows": {
            "journal": len(journal),
            "outcomes": len(outcomes),
            "resolved": len(resolved),
            "resolved_with_failure_kind": len(failures),
            "reattributed_now": reattributed,
            "sessions": _sessions(journal) or _sessions(outcomes),
        },
    }


def _answer(question: str, answer: object, evidence: dict,
            *, sufficient: bool = True, shortfall: object = None) -> dict:
    return {
        "question": question,
        "answer": answer if sufficient else INSUFFICIENT,
        "stated_answer": answer,
        "sample_sufficient": sufficient,
        "shortfall": shortfall,
        "evidence": evidence,
    }


def questions(bodies: dict) -> list[dict]:
    """The ten §16 questions, answered from the bodies above."""
    churn = bodies["churn"]["totals"]
    baseline = bodies["churn"]["recorded_baseline"]
    recon = bodies["reconciliation"]
    cov = recon.get("coverage") or {}
    direction = bodies["direction"]
    failure = bodies["failure"]
    studies = bodies["studies"]
    fut = bodies["futures_outcomes"]
    veh = bodies["vehicles"]
    aplus = bodies["a_plus"]
    sessions = bodies["_rows"]["sessions"]

    fam_rows = studies.get("families") or {}
    economic = {f: rows.get("economic") or [] for f, rows in fam_rows.items()}

    # q5: a definition is only reported when it cleared its own sample floor.
    defs = direction.get("definitions") or {}
    reported = {k: v.get("correct_pct") for k, v in defs.items()
                if v.get("correct_pct") is not None}

    # Both bodies are flat: ``report()`` returns its summary at the top level,
    # so reading a "summary" key here silently reported 0 resolved futures and 0
    # pairs no matter what the ledger held.
    fut_resolved = int(fut.get("resolved") or 0)
    fut_short = max(0, futures_outcomes.MIN_COMPARISON_SAMPLE - fut_resolved)
    session_short = max(0, a_plus_shadow.MIN_SESSIONS - len(sessions))

    return [
        _answer(
            "1. How much signal churn was eliminated?",
            {
                "raw_events": churn["raw_events"],
                "publications": churn["publications"],
                "suppressed": churn["suppressed"],
                "churn_removed_pct": churn["churn_removed_pct"],
            },
            {"episodes": churn["episodes"],
             "recorded_baseline": baseline,
             "note": ("raw events are still written in full; only restatement "
                      "of an unchanged call was removed. The baseline is a "
                      "recorded session that ran without churn control; the "
                      "live figures are the episodes this process published")},
            sufficient=churn["raw_events"] > 0,
            shortfall=(None if churn["raw_events"] else
                       "churn control is measured live: this process published "
                       "no episodes, so only the recorded baseline is shown")),
        _answer(
            "2. Are repeated signals now meaningful updates only?",
            {
                "meaningful_updates": churn["meaningful_updates"],
                "heartbeats": churn["heartbeats"],
                "publications_per_episode": churn["publications_per_episode"],
            },
            {"event_types": list(signal_churn.EVENT_TYPES),
             "enabled": bodies["churn"].get("enabled"),
             "thresholds": bodies["churn"].get("thresholds"),
             "note": ("a republication is INITIAL, MEANINGFUL_UPDATE, "
                      "HEARTBEAT, INVALIDATED or RESOLVED; anything else is "
                      "suppressed and still audited")},
            sufficient=churn["publications"] > 0,
            shortfall=(None if churn["publications"] else
                       "no publication observed in this process; run a live "
                       "session to measure the after-figure")),
        _answer(
            "3. Is reconciliation complete?",
            {
                "complete": bool(recon.get("complete")),
                "records_seen": cov.get("records_seen"),
                "records_processed": cov.get("records_processed"),
                "records_omitted": cov.get("records_omitted"),
            },
            {"note": ("a report with records_omitted > 0 is marked invalid "
                      "rather than published as a session total")},
            sufficient=cov.get("records_omitted") == 0,
            shortfall=cov.get("records_omitted")),
        _answer(
            "4. Which instrument families are economically tradable?",
            {f: {"economic": economic.get(f) or [],
                 "uneconomic": (fam_rows.get(f) or {}).get("uneconomic") or [],
                 "unaffordable": (fam_rows.get(f) or {}).get("unaffordable") or [],
                 "median_of_median_spread_pct":
                     (fam_rows.get(f) or {}).get("median_of_median_spread_pct")}
             for f in instrument_family.FAMILIES},
            {"min_sample_per_instrument": studies.get("min_sample"),
             "note": ("families are measured separately and never pooled; a "
                      "name below the sample floor is INSUFFICIENT_SAMPLE, not "
                      "uneconomic")}),
        _answer(
            "5. Is the market signal genuinely directional?",
            {"definitions_reported": reported,
             "definitions_withheld": [k for k, v in defs.items()
                                      if v.get("correct_pct") is None]},
            {"retired": direction.get("retired_definition_note"),
             "legacy_any_favorable_pct": direction.get("legacy_any_favorable_pct"),
             "single_number_withheld": direction.get("single_number_withheld"),
             "reason": direction.get("single_number_reason")},
            sufficient=bool(reported),
            shortfall=(None if reported else
                       f"no definition reached {direction.get('min_sample')} "
                       "answerable resolved signals")),
        _answer(
            "6. How often is the direction right but the option wrong?",
            {"vehicle_failure_pct": failure.get("direction_right_option_wrong_pct"),
             "counts": failure.get("counts")},
            {"vehicle_evidence_counts": failure.get("vehicle_evidence_counts"),
             "attributable": failure.get("attributable"),
             "unattributable": failure.get("unattributable"),
             "reattributed_now": bodies["_rows"]["reattributed_now"],
             "note": failure.get("direction_right_option_wrong_note"),
             "history_note": ("resolved rows recorded before §6 landed carry no "
                              "direction marks, so they are unattributable "
                              "rather than credited to the retired "
                              "any-favourable-move definition")},
            sufficient=bool(failure.get("attributable")),
            shortfall=(None if failure.get("attributable")
                       else "no resolved signal carried an attributable kind")),
        _answer(
            "7. Is Futures better than Options on the same market idea?",
            veh.get("answer"),
            {"resolved_futures_outcomes": fut_resolved,
             "two_sided_pairs": veh.get("two_sided_pairs"),
             "verdicts": veh.get("verdict_counts"),
             "note": ("options and futures are never summed: each leg keeps its "
                      "own unit and R is only compared inside one paired idea")},
            sufficient=bool(veh.get("sample_sufficient")),
            shortfall={"resolved_futures_short_of_comparison": fut_short,
                       "pairs_short": veh.get("shortfall")}),
        _answer(
            "8. Which instruments should become A+ candidates?",
            {"by_economics": studies.get("a_plus_candidates") or [],
             "by_a_plus_label": aplus.get("a_plus_candidates") or []},
            {"a_plus_thresholds": aplus.get("thresholds"),
             "note": ("a candidate is a name whose recorded economics allow a "
                      "trade to survive its own spread; it is not a promotion "
                      "and no gate consults this list")}),
        _answer(
            "9. Which instruments should remain research-only?",
            {"by_economics": studies.get("research_only_names") or [],
             "uneconomic": studies.get("uneconomic_names") or [],
             "unaffordable": studies.get("unaffordable_names") or [],
             "by_a_plus_label": aplus.get("research_only_instruments") or []},
            {"note": ("UNAFFORDABLE is a fact about the contract against the "
                      "configured paper risk budget, never a failed signal")}),
        _answer(
            "10. Is there enough evidence to begin production A+ validation?",
            False,
            {"sessions_observed": len(sessions),
             "sessions": sessions,
             "sessions_required": a_plus_shadow.MIN_SESSIONS,
             "holdout_required": a_plus_shadow.MIN_HOLDOUT_SESSIONS,
             "resolved_futures_outcomes": fut_resolved,
             "note": ("§15 requires >= 20 sessions with >= 5 chronological "
                      "holdout sessions, complete data, adequate bid/ask and an "
                      "adequate resolved futures sample; in-sample evidence "
                      "cannot promote anything")},
            sufficient=(session_short == 0 and fut_short == 0
                        and cov.get("records_omitted") == 0),
            shortfall={"sessions": session_short,
                       "resolved_futures": fut_short,
                       "records_omitted": cov.get("records_omitted")}),
    ]


def build(session: str | None = None) -> dict:
    bodies = collect(session)
    qs = questions(bodies)
    return {
        "phase": "12",
        "title": ("Instrument-aware A+ vehicle selection and signal churn "
                  "control"),
        "generated_at_ist": _ist(datetime.now(tz=timezone.utc).timestamp()),
        "session": session,
        "scope": "RESEARCH_SHADOW_PAPER_ONLY",
        "production_unchanged": [
            "BUY/WAIT/NO_TRADE strategy logic", "gate thresholds",
            "score weights", "confidence formula", "strike selector",
            "premium floor", "stop and target philosophy", "exits",
            "broker execution",
        ],
        "rows_read": bodies["_rows"],
        "questions": qs,
        "signal_churn": bodies["churn"],
        "reconciliation": bodies["reconciliation"],
        "direction_attribution": bodies["direction"],
        "failure_attribution": bodies["failure"],
        "instrument_studies": bodies["studies"],
        "instrument_tradability": bodies["tradability"],
        "futures_rollover": bodies["rollover"],
        "futures_paper_outcomes": bodies["futures_outcomes"],
        "vehicle_comparison": bodies["vehicles"],
        "a_plus_shadow": bodies["a_plus"],
        "validation": {
            "sessions_observed": len(bodies["_rows"]["sessions"]),
            "sessions_required": a_plus_shadow.MIN_SESSIONS,
            "holdout_required": a_plus_shadow.MIN_HOLDOUT_SESSIONS,
            "in_sample_promotion": False,
            "note": ("no result in this report promotes anything: A+ is shadow, "
                     "vehicle selection is research, futures has no order path"),
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
        f"# Phase 12 — {body['title']}",
        "",
        f"Generated {body['generated_at_ist']} · scope **{body['scope']}**",
        "",
        ("Every number below is measured from the recorded ledgers. Where the "
         "data cannot support an answer it says `INSUFFICIENT_SAMPLE` and states "
         "the shortfall, rather than reporting a figure that would not survive "
         "a holdout."),
        "",
        "## Production behaviour unchanged",
        "",
    ]
    lines += [f"- {item}" for item in body["production_unchanged"]]
    lines += [
        "",
        "## Rows read",
        "",
        f"- journal rows: {body['rows_read']['journal']}",
        f"- outcome rows: {body['rows_read']['outcomes']} "
        f"({body['rows_read']['resolved']} resolved)",
        f"- sessions: {_fmt(body['rows_read']['sessions'])}",
        "",
        "## The ten questions",
        "",
    ]
    for q in body["questions"]:
        lines.append(f"### {q['question']}")
        lines.append("")
        if q["sample_sufficient"]:
            lines.append(f"**{_fmt(q['answer'])}**")
        else:
            lines.append(f"**{INSUFFICIENT}** — shortfall: "
                         f"{_fmt(q['shortfall'])}")
            lines.append("")
            lines.append(f"What the current data shows: {_fmt(q['stated_answer'])}")
        lines.append("")
        lines.append(f"Evidence: {_fmt(q['evidence'])}")
        lines.append("")

    val = body["validation"]
    lines += [
        "## §15 validation status",
        "",
        f"- sessions observed: {val['sessions_observed']} of "
        f"{val['sessions_required']} required",
        f"- chronological holdout sessions required: {val['holdout_required']}",
        f"- promotion from in-sample evidence: {val['in_sample_promotion']}",
        "",
        val["note"],
        "",
        "## Artefacts",
        "",
    ]
    lines += [f"- `{name}`" for name in body["artefacts"]]
    lines.append("")
    return "\n".join(lines)


def write_all(session: str | None = None) -> dict:
    """Write the eight §16 artefacts and return their paths."""
    os.makedirs(settings.data_dir, exist_ok=True)
    body = build(session)
    written: dict[str, str] = {}

    written[signal_churn.REPORT_JSON] = signal_churn.write_report()
    written[vehicle_comparison.REPORT_JSON] = vehicle_comparison.write_report(session)
    written[direction_attribution.REPORT_JSON] = direction_attribution.write_report(
        body["direction_attribution"])
    written[tradability.REPORT_JSON] = tradability.write_report()
    written[futures_rollover.REPORT_JSON] = futures_rollover.write_report()
    written[futures_outcomes.REPORT_JSON] = futures_outcomes.write_report(session)
    # Not one of the eight, but the §13 body the report quotes.
    written[a_plus_shadow.REPORT_JSON] = a_plus_shadow.write_report()
    written[instrument_studies.REPORT_JSON] = str(
        instrument_studies.write_report(session))

    for name, payload in ((REPORT_JSON, json.dumps(body, indent=2, default=str)),
                          (REPORT_MD, markdown(body))):
        path = os.path.join(settings.data_dir, name)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
        written[name] = path

    missing = [a for a in ARTEFACTS if a not in written]
    return {
        "written": written,
        "artefacts_required": list(ARTEFACTS),
        "artefacts_missing": missing,
        "complete": not missing,
        "research_only": True,
    }
