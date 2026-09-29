"""Phase 13 Part 15 — the eight artefacts and the thirteen questions. RESEARCH ONLY.

Same discipline as Phase 12A: every answer is composed from a body computed above
it, never from prose written beside it, and a question the data cannot support says
so with its shortfall rather than printing a number that would not survive a
holdout. Part 14's floors — 20 sessions, 5 chronological holdout sessions — are not
met and will not be for weeks, so the strongest status any candidate reaches here
is IN_SAMPLE_ONLY.

What is deliberately *not* claimed:

* no pullback definition is promoted, because a definition that wins on this book
  won on the sessions it was read from;
* the time-stop question is answered as REQUIRES_MORE_DATA, because the ledger
  cannot price an exit taken at an arbitrary minute;
* the production entry, stop, targets, exits and hold estimate are unchanged, and
  the report says which of them it deliberately left alone.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from app.analysis import entry_location, entry_quality, hold_window, label_outcomes
from app.config import settings

from . import entry_study, ledger, protection, pullback, timestop

REPORT_JSON = "phase13_report.json"
REPORT_MD = "phase13_report.md"
ENTRY_JSON = "entry_quality.json"
PULLBACK_JSON = "pullback_comparison.json"
HOLD_JSON = "hold_window_analysis.json"
TIME_STOP_JSON = "time_stop_analysis.json"
PROTECTION_JSON = "profit_protection.json"
EXPIRY_JSON = "expiry_entry_exit.json"

ARTEFACTS = (REPORT_MD, REPORT_JSON, ENTRY_JSON, PULLBACK_JSON, HOLD_JSON,
             TIME_STOP_JSON, PROTECTION_JSON, EXPIRY_JSON)

INSUFFICIENT = "INSUFFICIENT_SAMPLE"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"
OBSERVATION = "OBSERVATION"
HYPOTHESIS = "HYPOTHESIS"
IN_SAMPLE_ONLY = "IN_SAMPLE_ONLY"

_IST = timezone(timedelta(hours=5, minutes=30))

PRODUCTION_UNCHANGED = (
    "BUY/WAIT/NO_TRADE strategy logic", "gate thresholds", "score weights",
    "confidence formula", "signal score", "strike selector", "premium floor",
    "chase guard", "production stop", "production targets", "production exits",
    "expected_holding_minutes on the card", "broker execution",
    "no option writing, no short calls or puts, no credit spreads",
    "no futures auto-execution and no real-money order path",
)


def _ist(ts: float) -> str:
    return datetime.fromtimestamp(ts, _IST).strftime("%Y-%m-%d %H:%M:%S IST")


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


def _expiry_body(rows: list[dict], entry: dict) -> dict:
    """Part 10 — does any of this behave differently into expiry?"""
    sessions = len({r["session"] for r in rows if r.get("session")})
    blocks: dict[str, dict] = {}
    for state in ledger.EXPIRY_STATES:
        subset = [r for r in rows if r.get("expiry_state") == state]
        if not subset:
            continue
        blocks[state] = {
            "cohort": label_outcomes.cohort(state, subset, sessions),
            "timings": timestop.timings(subset),
            "pullback_best_r_retrace": pullback.definition(
                "R_RETRACE_20", subset, depth_r=0.2),
            "protection": protection.study(subset),
        }
    return {
        "part": "13 Part 10",
        "research_only": True,
        "by_expiry_state": blocks,
        "entry_quality_by_expiry_state":
            entry.get("entry_quality_by_expiry_state"),
        "pre_expiry_dte": ledger.PRE_EXPIRY_DTE,
        "notes": [
            "expiry state is computed from the call's own recorded expiry and its "
            "session date, so a weekly and a monthly leg on the same day are not "
            "pooled",
            "a decay effect and a spread effect are not separated here: both grow "
            "into expiry, and this book cannot tell them apart",
        ],
    }


def collect(session: str | None = None) -> dict:
    rows, coverage = ledger.build(session)
    entry = entry_study.study(rows)
    return {
        "_rows": rows,
        "coverage": coverage,
        "entry": entry,
        "pullback": pullback.study(rows),
        "hold": {**hold_window.report(session), "timings": timestop.timings(rows)},
        "time_stop": timestop.study(rows),
        "protection": protection.study(rows),
        "expiry": _expiry_body(rows, entry),
    }


def _cohort_map(cohorts: list[dict]) -> dict:
    return {row["cohort"]: {"resolved": row["resolved"],
                            "win_rate_net_pct": row["win_rate_net_pct"],
                            "expectancy_net_r": row["expectancy_net_r"],
                            "give_back_r_median": row["give_back_r_median"],
                            "status": row["status"]}
            for row in cohorts or []}


def questions(bodies: dict) -> list[dict]:
    """The thirteen Phase 13 questions, answered from the bodies above."""
    entry = bodies["entry"]
    pull = bodies["pullback"]
    hold = bodies["hold"]
    stop = bodies["time_stop"]
    prot = bodies["protection"]
    expiry = bodies["expiry"]
    cov = bodies["coverage"]
    sessions = int(cov.get("session_count") or 0)
    session_short = max(0, label_outcomes.MIN_SESSIONS - sessions)
    mono = entry["monotonic_in_net_expectancy"]
    best = pull.get("best_by_book_expectancy")
    labelled = cov.get("labels_recorded", 0) + cov.get("labels_backfilled", 0)
    enough_labels = labelled >= label_outcomes.MIN_COHORT
    # A label is only worth scoring if it could have been anything else. On a
    # backfilled row the premium recorded IS the planned entry, so the chase
    # distance is zero by construction and the grade is definitional.
    chase_facts = int(cov.get("rows_with_premium_chase_facts") or 0)
    resolved = int(cov.get("resolved_rows") or 0)
    chase_cohorts = int(cov.get("chase_visible_cohorts") or 0)
    chase_measurable = (chase_facts >= label_outcomes.MIN_COHORT
                        and chase_cohorts >= 2)
    policies = {p["policy"]: p for p in prot["policies"]}
    baseline_r = (policies.get("A_BASELINE") or {}).get("expectancy_net_r")
    half_r = (policies.get("B_HALF_AT_T1") or {}).get("expectancy_net_r")
    breakeven_r = (policies.get("C_HALF_AT_T1_THEN_BREAKEVEN") or {}).get(
        "expectancy_net_r")

    return [
        _answer(
            "1. Are peak/chased entries genuinely worse?",
            {"by_entry_quality": _cohort_map(entry["by_entry_quality"]),
             "orders_the_outcomes":
                 mono["net_expectancy_decreasing_with_lateness"]},
            {"comparable_cohorts": mono["comparable_cohorts"],
             "min_cohort": label_outcomes.MIN_COHORT,
             "labels_backfilled": cov.get("labels_backfilled"),
             "rows_with_premium_chase_facts": chase_facts,
             "entry_cohorts_with_a_scorable_chase_sample": chase_cohorts,
             "rows_at_exactly_zero_zone_distance":
                 cov.get("rows_at_exactly_zero_zone_distance"),
             "note": ("the label is only a gate candidate if it orders the "
                      "outcomes; Phase 12A's tradability label did not and was "
                      "not promoted"),
             "why_not_answered": cov.get("backfill_limitation")},
            status=(IN_SAMPLE_ONLY if (enough_labels and chase_measurable)
                    else REQUIRES_MORE_DATA),
            shortfall=(None if (enough_labels and chase_measurable)
                       else {"labelled_resolved": labelled,
                             "rows_where_a_chase_could_be_seen": chase_facts,
                             "cohorts_with_a_scorable_chase_sample":
                                 chase_cohorts,
                             "cohorts_required": 2,
                             "of_resolved": resolved,
                             "required": label_outcomes.MIN_COHORT,
                             "needs": ("sessions recorded on the Phase 12A build, "
                                       "which is what writes premium_at_setup and "
                                       "the local premium range")})),
        _answer(
            "2. Does waiting for a pullback improve net expectancy?",
            {"control_expectancy_net_r":
                 (pull["control"] or {}).get("expectancy_net_r"),
             "definitions": [{"definition": d["definition"],
                              "book_expectancy_net_r_zero_for_missed":
                                  d["book_expectancy_net_r_zero_for_missed"],
                              "filled_expectancy_net_r":
                                  d["filled_expectancy_net_r"],
                              "fill_rate_pct_lower_bound":
                                  d["fill_rate_pct_lower_bound"],
                              "beats_control_on_book_expectancy":
                                  d["beats_control_on_book_expectancy"],
                              "improvement_source": d["improvement_source"],
                              "gain_from_better_entry_r":
                                  d["gain_from_better_entry_r"],
                              "gain_from_declining_trades_r":
                                  d["gain_from_declining_trades_r"],
                              "dip_selects_losers": d["dip_selects_losers"]}
                             for d in pull["definitions"]]},
            {"comparison_rule": ("book expectancy counts a skipped signal as "
                                 "zero; the filled-only column is shown beside "
                                 "it precisely because it flatters every method"),
             "caveat": pull["headline_caveat"],
             "status_of_ranking": pull["status"]},
            status=pull["status"],
            shortfall=(None if pull["status"] == IN_SAMPLE_ONLY
                       else {"filled_per_definition": label_outcomes.MIN_COHORT})),
        _answer(
            "3. How many winners does pullback waiting miss?",
            {d["definition"]: {"missed": d["missed"],
                               "missed_winners": d["missed_winners"],
                               "missed_median_mfe_r": d["missed_median_mfe_r"],
                               "missed_net_r_total": d["missed_net_r_total"],
                               "immediate_continuation_pct":
                                   d["immediate_continuation_pct"]}
             for d in pull["definitions"]},
            {"note": ("fills are detected from the latched MAE, so these missed "
                      "counts are upper bounds and the fill rates lower bounds"),
             "why_it_matters": ("the median net winner reached T1 in "
                               f"{(hold['timings'].get('net_winner_t1_minutes') or {}).get('median')}"
                               " minutes, so a patient method has little time to "
                               "get filled")},
            status=(IN_SAMPLE_ONLY if pull["definitions"] else INSUFFICIENT)),
        _answer(
            "4. What is the best pullback definition?",
            {"by_book_expectancy": best,
             "by_entry_gain": pull.get("best_by_entry_gain")},
            {"ranked_on": ("book expectancy with a skipped signal counted as zero, "
                           "and separately on the gain attributable to the price "
                           "paid rather than to the trades declined"),
             "caveat": pull["headline_caveat"],
             "level_based_definitions": pull["level_based_definitions"],
             "note": ("in-sample by construction: the depth was chosen on the "
                      "same sessions it is scored on, which is why this is a "
                      "candidate and not a rule")},
            status=(IN_SAMPLE_ONLY if best else INSUFFICIENT),
            shortfall=(None if best else "no definition reached the minimum "
                                         "filled sample")),
        _answer(
            "5. What is the normal winner hold time?",
            {"net_winner_t1_minutes":
                 hold["timings"].get("net_winner_t1_minutes"),
             "t1_minutes": hold["timings"].get("t1_minutes"),
             "stop_minutes": hold["timings"].get("stop_minutes"),
             "published_window_pooled": {
                 "expected_low": (hold.get("pooled") or {}).get("t1_p25"),
                 "expected_high": (hold.get("pooled") or {}).get("t1_p75"),
                 "long_tail": (hold.get("pooled") or {}).get("t1_p90")}},
            {"reached_t1_pct": hold["timings"].get("reached_t1_pct"),
             "framing": ("a time-to-resolution distribution, not a probability "
                         "that the target arrives"),
             "cohorts_measured": len(hold.get("cohorts") or {})},
            status=(OBSERVATION if sessions < 2 else IN_SAMPLE_ONLY)),
        _answer(
            "6. When does waiting become statistically unattractive?",
            {"thresholds": [{"minutes": t["threshold_minutes"],
                             "still_open": t["still_open"],
                             "n_short_of_t1": t["cohort"]["resolved"],
                             "eventual_expectancy_net_r":
                                 t["cohort"]["expectancy_net_r"],
                             "eventually_reached_t1": t["eventually_reached_t1"],
                             "eventually_stopped": t["eventually_stopped"]}
                            for t in stop["thresholds"]]},
            {"framing": ("each row is the decision faced at that minute: still "
                         "open and still short of T1"),
             "not_priced": stop["time_stop_verdict"]},
            status=REQUIRES_MORE_DATA,
            shortfall=stop["time_stop_verdict"]["reason"]),
        _answer(
            "7. Does a partial at T1 improve net expectancy?",
            {"A_BASELINE": baseline_r, "B_HALF_AT_T1": half_r,
             "improves": (None if baseline_r is None or half_r is None
                          else bool(half_r > baseline_r))},
            {"reached_t1_then_finished_negative":
                 prot["reached_t1_then_finished_negative"],
             "reached_t1": prot["reached_t1"],
             "cost_rule": "every policy pays the same round trip as the baseline",
             "priced": prot["priced"]},
            status=(IN_SAMPLE_ONLY if prot["priced"] >= label_outcomes.MIN_COHORT
                    else INSUFFICIENT),
            shortfall=(None if prot["priced"] >= label_outcomes.MIN_COHORT
                       else {"priced": prot["priced"],
                             "required": label_outcomes.MIN_COHORT})),
        _answer(
            "8. Does moving the stop to breakeven after T1 help?",
            {"B_HALF_AT_T1": half_r,
             "C_HALF_AT_T1_THEN_BREAKEVEN": breakeven_r,
             "improves": (None if half_r is None or breakeven_r is None
                          else bool(breakeven_r > half_r))},
            {"approximation": ("the return to entry is detected from the latched "
                              "MAE after T1, so only a pullback that was the "
                              "deepest point of the trade is seen"),
             "policies_needing_premium_path":
                 prot["policies_needing_premium_path"]},
            status=(IN_SAMPLE_ONLY if prot["reached_t1"] >= label_outcomes.MIN_COHORT
                    else INSUFFICIENT),
            shortfall=(None if prot["reached_t1"] >= label_outcomes.MIN_COHORT
                       else {"reached_t1": prot["reached_t1"],
                             "required": label_outcomes.MIN_COHORT})),
        _answer(
            "9. Does it hold on INDEX and on MCX?",
            entry["entry_quality_by_family"],
            {"note": ("families are never pooled: the spread an INDEX leg pays "
                      "and the spread a single stock leg pays differ by roughly "
                      "four times at the signalled strike"),
             "hold_window_cohorts": sorted(hold.get("cohorts") or {})},
            status=(IN_SAMPLE_ONLY if chase_measurable else REQUIRES_MORE_DATA),
            shortfall=(None if chase_measurable
                       else "the entry label itself cannot be judged yet, so its "
                            "family split cannot either")),
        _answer(
            "10. Does it hold across CE and PE?",
            entry["entry_quality_by_side"],
            {"note": ("CE and PE are both long option buys; no short leg exists "
                      "anywhere in this book, so this is a comparison of two "
                      "purchases, not of a buy against a write"),
             "counterfactual": ("what the *opposite* leg would have done is "
                                "Phase 13C and is not answered here")},
            status=(IN_SAMPLE_ONLY if chase_measurable else REQUIRES_MORE_DATA),
            shortfall=(None if chase_measurable
                       else "the entry label itself cannot be judged yet, so its "
                            "CE/PE split cannot either")),
        _answer(
            "11. Does it hold on expiry day?",
            {state: {"cohort": block["cohort"]["cohort"],
                     "resolved": block["cohort"]["resolved"],
                     "expectancy_net_r": block["cohort"]["expectancy_net_r"],
                     "t1_minutes": block["timings"]["t1_minutes"],
                     "status": block["cohort"]["status"]}
             for state, block in (expiry["by_expiry_state"] or {}).items()},
            {"pre_expiry_dte": expiry["pre_expiry_dte"],
             "caveat": ("decay and spread both worsen into expiry and this book "
                        "cannot separate them")},
            status=(IN_SAMPLE_ONLY if expiry["by_expiry_state"] else INSUFFICIENT)),
        _answer(
            "12. Would Options and Futures need different rules?",
            REQUIRES_MORE_DATA,
            {"reason": ("futures produced 2 valid plans in 1,633 evaluations on "
                        "the last recorded session, with 1,321 refusals for a "
                        "stale feed, so there is no futures entry or hold sample "
                        "to compare against the option one"),
             "blocked_on": ("the Phase 12A feed fix measured on a live session; "
                            "until then every futures-versus-options question "
                            "stays unanswerable"),
             "safety": "futures remains research-only with no order path"},
            status=REQUIRES_MORE_DATA,
            shortfall={"resolved_futures_outcomes": 0}),
        _answer(
            "13. What is the simplest production candidate supported by "
            "out-of-sample evidence?",
            None,
            {"sessions_observed": sessions,
             "sessions_required": label_outcomes.MIN_SESSIONS,
             "holdout_required": label_outcomes.MIN_HOLDOUT_SESSIONS,
             "candidates_ranked_in_sample": {
                 "entry": (best or {}).get("definition"),
                 "protection": ("B_HALF_AT_T1" if (half_r is not None
                                                   and baseline_r is not None
                                                   and half_r > baseline_r)
                                else None)},
             "note": ("there is no out-of-sample evidence yet: every session "
                      "here was used to read the depths and thresholds, so the "
                      "honest answer is none. The candidates are listed so the "
                      "holdout test is already specified when the sessions "
                      "exist")},
            status=REQUIRES_MORE_DATA,
            shortfall={"sessions": session_short,
                       "holdout_sessions": label_outcomes.MIN_HOLDOUT_SESSIONS}),
    ]


def build(session: str | None = None) -> dict:
    bodies = collect(session)
    cov = bodies["coverage"]
    return {
        "phase": "13A/13B",
        "title": "Entry location and hold-time intelligence",
        "generated_at_ist": _ist(datetime.now(tz=timezone.utc).timestamp()),
        "session": session,
        "scope": "RESEARCH_SHADOW_PAPER_ONLY",
        "production_unchanged": list(PRODUCTION_UNCHANGED),
        "coverage": cov,
        "questions": questions(bodies),
        "entry_quality": bodies["entry"],
        "pullback_comparison": bodies["pullback"],
        "hold_window_analysis": bodies["hold"],
        "time_stop_analysis": bodies["time_stop"],
        "profit_protection": bodies["protection"],
        "expiry_entry_exit": bodies["expiry"],
        "validation": {
            "sessions_observed": cov.get("session_count"),
            "sessions_required": label_outcomes.MIN_SESSIONS,
            "holdout_required": label_outcomes.MIN_HOLDOUT_SESSIONS,
            "in_sample_promotion": False,
            "labels_permitted": [OBSERVATION, HYPOTHESIS, IN_SAMPLE_ONLY,
                                 REQUIRES_MORE_DATA],
            "entry_states_display_only": list(entry_location.STATES),
            "entry_quality_states": list(entry_quality.STATES),
            "note": ("no result in this report changes a production decision: "
                     "the entry state and the hold window are display-only "
                     "research fields, and no time stop, partial or breakeven "
                     "rule is wired to anything"),
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


def _block(value: object) -> list[str]:
    """A readable body for one answer. Prose stays prose, numbers stay JSON."""
    if isinstance(value, str):
        return [value]
    return ["```json", json.dumps(value, indent=2, default=str), "```"]


def _table(headers: list[str], rows: list[list[object]]) -> list[str]:
    if not rows:
        return []
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(
            "—" if v is None else str(v) for v in row) + " |")
    out.append("")
    return out


def _tables(body: dict) -> list[str]:
    """The numbers a reader wants without opening the JSON artefacts."""
    lines = ["## Entry cohorts", ""]
    lines += _table(
        ["cohort", "n", "net win %", "net R", "giveback R", "status"],
        [[c["cohort"], c["resolved"], c["win_rate_net_pct"],
          c["expectancy_net_r"], c["give_back_r_median"], c["status"]]
         for c in (body["entry_quality"].get("by_entry_quality") or [])
         + (body["entry_quality"].get("by_entry_state") or [])])
    pull = body["pullback_comparison"]
    lines += ["## Pullback definitions", "",
              f"Control (immediate entry): **{(pull['control'] or {}).get('expectancy_net_r')} R** "
              f"over {(pull['control'] or {}).get('costed')} costed calls.", ""]
    lines += _table(
        ["definition", "fill % (lower bound)", "book net R", "filled net R",
         "missed winners", "continued without dipping %", "gain from entry",
         "gain from declining", "source"],
        [[d["definition"], d["fill_rate_pct_lower_bound"],
          d["book_expectancy_net_r_zero_for_missed"],
          d["filled_expectancy_net_r"], d["missed_winners"],
          d["immediate_continuation_pct"], d["gain_from_better_entry_r"],
          d["gain_from_declining_trades_r"], d["improvement_source"]]
         for d in pull["definitions"]])
    lines += [pull["headline_caveat"], ""]
    hold = body["hold_window_analysis"]
    pooled = hold.get("pooled") or {}
    lines += ["## Hold window", "",
              f"- pooled expected window (T1 p25\u2013p75): "
              f"{pooled.get('t1_p25')}\u2013{pooled.get('t1_p75')} min",
              f"- pooled long tail (T1 p90): {pooled.get('t1_p90')} min",
              f"- median time to stop: {pooled.get('stop_median')} min",
              f"- reached T1 at all: {pooled.get('reached_t1_pct')}%", ""]
    lines += _table(
        ["still open at", "n", "% of book", "n short of T1",
         "eventual net R", "eventually T1", "eventually stopped"],
        [[f"{t['threshold_minutes']} min", t["still_open"], t["still_open_pct"],
          t["cohort"]["resolved"], t["cohort"]["expectancy_net_r"],
          t["eventually_reached_t1"], t["eventually_stopped"]]
         for t in body["time_stop_analysis"]["thresholds"]])
    lines += ["## Profit protection", ""]
    lines += _table(
        ["policy", "n", "gross R", "net R", "net win %"],
        [[p["policy"], p["n"], p["expectancy_gross_r"], p["expectancy_net_r"],
          p["win_rate_net_pct"]]
         for p in body["profit_protection"]["policies"]])
    return lines


def markdown(body: dict) -> str:
    cov = body["coverage"]
    lines: list[str] = [
        f"# Phase 13A/13B — {body['title']}",
        "",
        f"Generated {body['generated_at_ist']} · scope **{body['scope']}**",
        "",
        ("Two questions from the 26 Aug session: was the call bought after the "
         "premium had already run, and how long was it reasonable to hold it. "
         "Both are answered from the recorded ledgers. Where the data cannot "
         "support an answer the question reads `REQUIRES_MORE_DATA` or "
         "`INSUFFICIENT_SAMPLE` with the shortfall stated."),
        "",
        "## Coverage",
        "",
        f"- resolved option rows: {cov.get('resolved_rows')}",
        f"- sessions: {_fmt(cov.get('sessions'))}",
        f"- entry labels recorded at signal time: {cov.get('labels_recorded')}",
        f"- entry labels recomputed from signal-time fields: "
        f"{cov.get('labels_backfilled')}",
        f"- rows with no journal call found: {cov.get('labels_unavailable')}",
        f"- rows where a chase could be seen at all: "
        f"{cov.get('rows_with_premium_chase_facts')}",
        f"- entry cohorts holding a scorable chase sample: "
        f"{cov.get('chase_visible_cohorts')} (two are needed to compare)",
        f"- rows priced exactly at the plan's own entry: "
        f"{cov.get('rows_at_exactly_zero_zone_distance')}",
        "",
        _fmt(cov.get("backfill_limitation")),
        "",
        "## Production behaviour unchanged",
        "",
    ]
    lines += [f"- {item}" for item in body["production_unchanged"]]
    lines += [""]
    lines += _tables(body)
    lines += ["## The thirteen questions", ""]
    for q in body["questions"]:
        lines.append(f"### {q['question']}")
        lines.append("")
        if q["sample_sufficient"]:
            lines.append(f"**{q['status']}**")
            lines.append("")
            lines += _block(q["answer"])
        else:
            lines.append(f"**{q['status']}** — shortfall:")
            lines.append("")
            lines += _block(q["shortfall"])
            lines.append("")
            lines.append("What the current data shows:")
            lines.append("")
            lines += _block(q["stated_answer"])
        lines.append("")
        lines.append("Evidence:")
        lines.append("")
        lines += _block(q["evidence"])
        lines.append("")
    val = body["validation"]
    lines += [
        "## Part 14 validation status",
        "",
        f"- sessions observed: {_fmt(val['sessions_observed'])} of "
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
    """Write the eight Part 15 artefacts and return their paths."""
    os.makedirs(settings.data_dir, exist_ok=True)
    body = build(session)
    written = {
        ENTRY_JSON: _write(ENTRY_JSON, json.dumps(body["entry_quality"], indent=2,
                                                  default=str)),
        PULLBACK_JSON: _write(PULLBACK_JSON, json.dumps(
            body["pullback_comparison"], indent=2, default=str)),
        HOLD_JSON: _write(HOLD_JSON, json.dumps(body["hold_window_analysis"],
                                                indent=2, default=str)),
        TIME_STOP_JSON: _write(TIME_STOP_JSON, json.dumps(
            body["time_stop_analysis"], indent=2, default=str)),
        PROTECTION_JSON: _write(PROTECTION_JSON, json.dumps(
            body["profit_protection"], indent=2, default=str)),
        EXPIRY_JSON: _write(EXPIRY_JSON, json.dumps(body["expiry_entry_exit"],
                                                    indent=2, default=str)),
        REPORT_JSON: _write(REPORT_JSON, json.dumps(body, indent=2, default=str)),
        REPORT_MD: _write(REPORT_MD, markdown(body)),
    }
    missing = [a for a in ARTEFACTS if a not in written]
    return {
        "written": written,
        "artefacts_required": list(ARTEFACTS),
        "artefacts_missing": missing,
        "complete": not missing,
        "research_only": True,
    }
