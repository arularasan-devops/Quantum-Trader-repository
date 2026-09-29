"""Phase 50 — the Markdown artefact. Formatting only, no measurement.

The rules the layout obeys, because a table can lie by arrangement alone: a leg
count is never printed without its event count, a net is never printed without
how many events could not be resolved, and the ``EARLY_SHADOW_RESULT`` label sits
on the heading rather than in a footnote. The words this project refuses to use
on a sample this size are not in the template at all, so a reader can grep for
them and find nothing.
"""
from __future__ import annotations

import datetime as dt
import os

from app.research.phase49 import GROUPING_RULE, RULE_TEXT
from app.research.phase50 import (
    ARM_PRODUCTION_ONLY,
    ARM_PRODUCTION_PLUS_RESEARCH,
    EARLY_SHADOW_RESULT,
    GROUP_DECLINED,
    GROUP_SELECTED,
    GROUP_UNMEASURED,
    NO_MIDPOINT,
    NOT_A_PROMOTION,
    OVERLAP,
    REPORT_NAME,
    SAMPLE_FRESHNESS,
    SAMPLE_SOURCE,
)
from app.research.phase50 import store as store_mod

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def artefact_dir() -> str:
    return os.path.join(store_mod.data_dir(), "phase50")


def write(
    payload: dict, comparison: dict, readings: dict,
    filtered: dict | None = None,
) -> str:
    """Write the board artefact and return its path."""
    target = os.path.join(artefact_dir(), REPORT_NAME)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(render(payload, comparison, readings, filtered))
    return target


def render(
    payload: dict, comparison: dict, readings: dict,
    filtered: dict | None = None,
) -> str:
    lines: list[str] = []
    lines.append(f"# SHADOW EVENT BOARD — {EARLY_SHADOW_RESULT}")
    lines.append("")
    lines.append(f"- session `{payload.get('session')}`")
    lines.append(f"- definition `{payload.get('definition')}`")
    lines.append(f"- grouping rule `{GROUPING_RULE}`")
    lines.append(f"- grouping fingerprint `{payload.get('rule_fingerprint')}`")
    lines.append(
        f"- resolution fingerprint `{payload.get('resolution_fingerprint')}`")
    lines.append(f"- holiday list loaded: `{payload.get('holiday_list_loaded')}`")
    lines.append("")
    lines.append(RULE_TEXT)
    lines.append("")

    lines.append("## LEG COUNT AND EVENT COUNT")
    lines.append("")
    lines.append("| arm | legs | events | resolved | unresolved |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for arm, column in (payload.get("arms") or {}).items():
        met = column.get("metrics") or {}
        lines.append(
            f"| {arm} | {column.get('leg_count')} | {column.get('event_count')} "
            f"| {met.get('resolved_events')} | {met.get('unresolved_events')} |"
        )
    lines.append("")

    lines.append("## MARKET SESSION STATUS — MACHINE DERIVED")
    lines.append("")
    for arm, column in (payload.get("arms") or {}).items():
        counts = column.get("session_status_counts") or {}
        rendered = ", ".join(f"{k} {v}" for k, v in counts.items()) or "—"
        lines.append(f"- **{arm}**: {rendered}")
    if payload.get("holiday_note"):
        lines.append("")
        lines.append(f"`{payload['holiday_note']}`")
    lines.append("")

    lines.append("## EVENTS")
    lines.append("")
    for arm, column in (payload.get("arms") or {}).items():
        lines.append(f"### {arm} — {column.get('leg_count')} legs, "
                     f"{column.get('event_count')} events")
        lines.append("")
        lines.append(
            "| event | contract | veh | dir | obs | dur s | market | "
            "resolution | net % | MFE % | MAE % | exit books own/store |"
        )
        lines.append("| --- | --- | --- | --- | ---: | ---: | --- | --- | "
                     "---: | ---: | ---: | ---: |")
        for row in column.get("events") or []:
            lines.append(
                f"| {row.get('event_id')} | {row.get('contract')} "
                f"| {row.get('vehicle')} | {row.get('direction')} "
                f"| {row.get('observation_count')} "
                f"| {_n(row.get('duration_seconds'))} "
                f"| {row.get('market_session_status')} "
                f"| {row.get('resolution_status')} | {_n(row.get('net_pct'))} "
                f"| {_n(row.get('mfe_pct'))} | {_n(row.get('mae_pct'))} "
                f"| {_n(row.get('forward_samples_from_observations'))}/"
                f"{_n(row.get('forward_samples_from_quote_store'))} |"
            )
        lines.append("")

    if filtered:
        lines.extend(_filter_section(filtered))

    lines.append(f"## COMPARISON — DEGENERATE — {EARLY_SHADOW_RESULT}")
    lines.append("")
    lines.append(
        "This table is kept for the record and is **not** a performance "
        "comparison. While the overlay is a subset of production, "
        "`production ∪ overlay` **is** the production column, so every "
        "difference below is zero by set arithmetic rather than by measurement. "
        "The answerable question is in the filter section above."
    )
    lines.append("")
    lines.append(f"`{filtered.get('degenerate_note') if filtered else ''}`")
    lines.append("")
    lines.append("| metric | A production | B production + overlay | B − A |")
    lines.append("| --- | ---: | ---: | ---: |")
    arm_a = ((comparison.get("arms") or {}).get(ARM_PRODUCTION_ONLY) or {})
    arm_b = ((comparison.get("arms") or {}).get(
        ARM_PRODUCTION_PLUS_RESEARCH) or {})
    met_a, met_b = arm_a.get("metrics") or {}, arm_b.get("metrics") or {}
    delta = comparison.get("delta") or {}
    for field in (
        "events", "resolved_events", "unresolved_events", "net_pct_total",
        "net_pct_mean_per_event", "profit_factor", "win_rate_pct",
        "max_drawdown_pct", "avg_cost_points", "mfe_pct_mean", "mae_pct_mean",
        "giveback_pct_mean", "t1_hit_rate_pct",
    ):
        lines.append(
            f"| {field} | {_n(met_a.get(field))} | {_n(met_b.get(field))} "
            f"| {_n(delta.get(field))} |"
        )
    lines.append("")
    verdict = comparison.get("verdict") or {}
    lines.append(f"- verdict `{verdict.get('state')}`")
    lines.append(f"- resolved events per arm bar "
                 f"`{comparison.get('min_resolved_events')}`")
    lines.append(f"- events only in B `{comparison.get('events_only_in_b')}`")
    lines.append("")
    lines.append(OVERLAP)
    lines.append("")

    lines.append("## BREAKDOWNS — B, PRODUCTION PLUS OVERLAY")
    lines.append("")
    breakdowns = ((comparison.get("breakdowns") or {}).get(
        ARM_PRODUCTION_PLUS_RESEARCH) or {})
    for name, groups in breakdowns.items():
        lines.append(f"### {name}")
        lines.append("")
        lines.append("| key | events | resolved | net % total | win % | T1 % |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        for key, met in groups.items():
            lines.append(
                f"| {key} | {met.get('events')} | {met.get('resolved_events')} "
                f"| {_n(met.get('net_pct_total'))} "
                f"| {_n(met.get('win_rate_pct'))} "
                f"| {_n(met.get('t1_hit_rate_pct'))} |"
            )
        lines.append("")

    lines.append("## TALLY READINGS — APPEND ONLY")
    lines.append("")
    lines.append("| session | arm | legs | events | state | evidence |")
    lines.append("| --- | --- | ---: | ---: | --- | --- |")
    for row in readings.get("readings") or []:
        lines.append(
            f"| {row.get('session')} | {row.get('arm')} | {row.get('calls')} "
            f"| {row.get('events')} | {row.get('tally_state')} "
            f"| {row.get('tally_state_evidence')} |"
        )
    lines.append("")

    lines.append("## WHAT THIS IS NOT")
    lines.append("")
    lines.append(NOT_A_PROMOTION)
    lines.append("")
    lines.append(NO_MIDPOINT)
    lines.append("")
    lines.append(SAMPLE_SOURCE)
    lines.append("")
    lines.append(SAMPLE_FRESHNESS)
    lines.append("")
    stamp = dt.datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S IST")
    lines.append(f"_written {stamp}_")
    lines.append("")
    return "\n".join(lines)


def _filter_section(filtered: dict) -> list[str]:
    """The primary comparison: the events the overlay kept against the ones it refused.

    Every policy is printed for both groups with its own fingerprint in the
    heading, and the coverage endpoint is printed last under its own warning —
    it answers "where did the capture stop looking", which is not an exit.
    """
    lines: list[str] = []
    counts = filtered.get("group_counts") or {}
    legs = filtered.get("group_leg_counts") or {}
    check = filtered.get("partition_check") or {}
    lines.append(f"## FILTER COMPARISON — SELECTED VS DECLINED — "
                 f"{EARLY_SHADOW_RESULT}")
    lines.append("")
    lines.append(f"- production events `{filtered.get('production_events')}` "
                 f"from `{filtered.get('production_legs')}` legs")
    lines.append(f"- **{GROUP_SELECTED}** `{counts.get(GROUP_SELECTED, 0)}` "
                 f"events, `{legs.get(GROUP_SELECTED, 0)}` observations")
    lines.append(f"- **{GROUP_DECLINED}** `{counts.get(GROUP_DECLINED, 0)}` "
                 f"events, `{legs.get(GROUP_DECLINED, 0)}` observations")
    lines.append(f"- **{GROUP_UNMEASURED}** "
                 f"`{counts.get(GROUP_UNMEASURED, 0)}` events — the overlay "
                 f"reached no decision at the admitting instant, so the filter "
                 f"cannot be credited or blamed for them")
    for reason, count in (filtered.get("unmeasured_reasons") or {}).items():
        lines.append(f"  - `{reason}` {count}")
    lines.append(f"- a decline means one of "
                 f"`{', '.join(filtered.get('declining_states') or [])}` — "
                 f"a refusal the overlay actually made")
    lines.append(f"- `{filtered.get('unmeasured_is_not_a_decline')}`")
    lines.append(f"- exhaustive and disjoint `{check.get('exhaustive_and_disjoint')}`"
                 f", unassigned `{check.get('unassigned')}`, duplicate ids "
                 f"`{check.get('duplicate_event_ids')}`")
    lines.append(f"- events whose overlay state changed during the event "
                 f"`{filtered.get('mixed_events')}` (assigned by their first "
                 f"observation)")
    lines.append(f"- exit policy set fingerprint "
                 f"`{filtered.get('exit_policy_set_fingerprint')}`, partition "
                 f"fingerprint `{filtered.get('partition_fingerprint')}`")
    lines.append("")
    lines.append(f"`{filtered.get('partition_rule')}`")
    lines.append("")
    lines.append(f"`{filtered.get('same_exit_both_groups')}`")
    lines.append("")
    lines.append(f"`{filtered.get('no_hindsight')}`")
    lines.append("")
    lines.extend(_guard_block(filtered))
    policies = filtered.get("policies") or {}
    for policy_id in filtered.get("primary_policies") or []:
        lines.extend(_policy_table(policies.get(policy_id) or {}))
    coverage = filtered.get("coverage_endpoint")
    lines.append("### COVERAGE DIAGNOSTIC — NOT A TRADING EXIT")
    lines.append("")
    lines.append(
        "The last observed executable book. It measures where the capture "
        "stopped looking, not a decision, and its numbers may not be quoted as "
        "a strategy outcome or pooled with the policies above."
    )
    lines.append("")
    lines.extend(_policy_table(policies.get(coverage) or {}))
    lines.extend(_strata_section(filtered))
    lines.append(f"`{filtered.get('subset_note')}`")
    lines.append("")
    return lines


def _guard_block(filtered: dict) -> list[str]:
    """The symmetry threshold, printed above the tables it is about to withhold."""
    guard = filtered.get("coverage_guard") or {}
    lines = [
        "### PRE-REGISTERED COVERAGE GUARD",
        "",
        f"- comparable only when the two cohorts' resolution rates differ by "
        f"at most `{guard.get('max_resolution_rate_gap_pp')}` percentage points "
        f"**and** by at most a factor of `{guard.get('max_resolution_rate_ratio')}`",
        f"- guard fingerprint `{guard.get('fingerprint')}` — "
        f"{guard.get('pre_registered')}",
        f"- `{guard.get('resolution_is_not_performance')}`",
        f"- when the guard fails the raw numbers stay and only the subtraction "
        f"goes: `{guard.get('asymmetric_status')}`",
        "",
        f"- cost bands are cut on `{filtered.get('cost_band_basis')}`",
        f"- `{filtered.get('cost_band_is_ex_ante')}`",
        "",
    ]
    return lines


def _strata_section(filtered: dict) -> list[str]:
    """Selected against declined inside one instrument, one vehicle, one cost band.

    The unstratified cost column cannot answer the question that matters: one of
    the decline states is ``COST_BLOCKED``, so the overlay showing a cheaper
    selected cohort is the filter's definition rather than evidence about its
    judgement. Only within a stratum is the comparison about direction.
    """
    policies = filtered.get("policies") or {}
    lines = [
        "## STRATIFIED — SELECTED VS DECLINED WITHIN INSTRUMENT / VEHICLE / COST BAND",
        "",
        "A cheaper selected cohort is not a finding while `COST_BLOCKED` is one "
        "of the decline states. These strata ask the non-circular question: "
        "inside the same instrument, the same vehicle and the same ex-ante cost "
        "band, do the events the overlay kept beat the events it refused?",
        "",
    ]
    for policy_id in filtered.get("primary_policies") or []:
        block = policies.get(policy_id) or {}
        strata = block.get("strata") or {}
        lines.append(f"### {policy_id} — strata")
        lines.append("")
        for title, field in (
            ("INSTRUMENT", "by_instrument"),
            ("VEHICLE", "by_vehicle"),
            ("COST BAND", "by_cost_band"),
        ):
            lines.extend(_strata_table(title, strata.get(field) or {}))
    return lines


def _strata_table(title: str, strata: dict) -> list[str]:
    lines = [
        f"#### by {title}",
        "",
        "| stratum | SEL legs | SEL elig | SEL res | SEL rate% | DEC legs "
        "| DEC elig | DEC res | DEC rate% | coverage | SEL net/ev | DEC net/ev "
        "| SEL − DEC net/ev | status |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- "
        "| ---: | ---: | ---: | --- |",
    ]
    if not strata:
        lines.append("| — | | | | | | | | | | | | | no events |")
        lines.append("")
        return lines
    for key, block in strata.items():
        cover = block.get("coverage") or {}
        left = cover.get(GROUP_SELECTED) or {}
        right = cover.get(GROUP_DECLINED) or {}
        columns = block.get("columns") or {}
        legs = block.get("leg_counts") or {}
        delta = block.get("selected_minus_declined")
        verdict = block.get("verdict") or {}
        lines.append(
            f"| {key} | {legs.get(GROUP_SELECTED, 0)} "
            f"| {left.get('eligible_events', 0)} "
            f"| {left.get('resolved_events', 0)} "
            f"| {_n(left.get('resolution_rate_pct'))} "
            f"| {legs.get(GROUP_DECLINED, 0)} "
            f"| {right.get('eligible_events', 0)} "
            f"| {right.get('resolved_events', 0)} "
            f"| {_n(right.get('resolution_rate_pct'))} "
            f"| {cover.get('status')} "
            f"| {_n((columns.get(GROUP_SELECTED) or {}).get('net_pct_mean_per_event'))} "
            f"| {_n((columns.get(GROUP_DECLINED) or {}).get('net_pct_mean_per_event'))} "
            f"| {_n((delta or {}).get('net_pct_mean_per_event')) if delta else 'withheld'} "
            f"| {verdict.get('state')} |"
        )
    lines.append("")
    return lines


def _policy_table(block: dict) -> list[str]:
    columns = block.get("columns") or {}
    chosen = columns.get(GROUP_SELECTED) or {}
    declined = columns.get(GROUP_DECLINED) or {}
    everything = columns.get("PRODUCTION_ALL_EVENTS") or {}
    delta = block.get("selected_minus_declined") or {}
    lines = [
        f"### {block.get('policy_id')} — `{block.get('fingerprint')}`",
        "",
        f"- rule: {block.get('rule')}",
        f"- params: `{block.get('params') or '—'}`",
        f"- measurable on this capture: `{block.get('measurable')}`",
        "",
        "| metric | SELECTED | DECLINED | SEL − DEC | all production |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for field in (
        "events", "resolved_events", "unresolved_events", "win_rate_pct",
        "gross_pct_mean_per_event", "avg_cost_points",
        "net_pct_mean_per_event", "net_pct_total", "profit_factor",
        "max_drawdown_pct", "mfe_pct_mean", "mae_pct_mean",
        "giveback_pct_mean", "t1_hit_rate_pct",
    ):
        lines.append(
            f"| {field} | {_n(chosen.get(field))} | {_n(declined.get(field))} "
            f"| {_n(delta.get(field))} | {_n(everything.get(field))} |"
        )
    verdict = block.get("verdict") or {}
    cover = block.get("coverage") or {}
    left = cover.get(GROUP_SELECTED) or {}
    right = cover.get(GROUP_DECLINED) or {}
    lines.append("")
    lines.append("| cohort | eligible | resolved | unresolved | resolution rate |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for name, cohort in ((GROUP_SELECTED, left), (GROUP_DECLINED, right)):
        lines.append(
            f"| {name} | {cohort.get('eligible_events', 0)} "
            f"| {cohort.get('resolved_events', 0)} "
            f"| {cohort.get('unresolved_events', 0)} "
            f"| {_n(cohort.get('resolution_rate_pct'))}% |"
        )
    lines.append("")
    lines.append(f"- coverage `{cover.get('status')}` — gap "
                 f"`{_n(cover.get('resolution_rate_gap_pp'))}pp` against a "
                 f"`{cover.get('max_gap_pp')}pp` bar, ratio "
                 f"`{_n(cover.get('resolution_rate_ratio'))}` against a "
                 f"`{cover.get('max_ratio')}` bar")
    if block.get("delta_withheld_because"):
        lines.append(f"- **SEL − DEC withheld** — "
                     f"`{block.get('delta_withheld_because')}`")
    lines.append(f"- verdict `{verdict.get('state')}`")
    lines.append("")
    return lines


def _n(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)
