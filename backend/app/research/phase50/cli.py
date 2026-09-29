"""Phase 50 CLI — events, their clock, their outcomes, and the two columns.

    .venv/bin/python -m app.research.phase50.cli events   --limit 5000
    .venv/bin/python -m app.research.phase50.cli board    --limit 5000
    .venv/bin/python -m app.research.phase50.cli compare  --limit 5000
    .venv/bin/python -m app.research.phase50.cli filter   --limit 5000
    .venv/bin/python -m app.research.phase50.cli coverage --limit 5000
    .venv/bin/python -m app.research.phase50.cli budget   --limit 50000
    .venv/bin/python -m app.research.phase50.cli readings
    .venv/bin/python -m app.research.phase50.cli record   --limit 5000
    .venv/bin/python -m app.research.phase50.cli report   --limit 5000
    .venv/bin/python -m app.research.phase50.cli status

``record`` is the only subcommand that writes, and it only appends: one event
summary per event per outcome, and one tally reading holding what the journal
held when it was read. Everything else reads.

Every count is printed with the bound it was read under, as it has been since
the 240 that was a ceiling: ``--limit`` defaults to the recorder's maximum, and a
reading that stops at its bound prints as ``at least N``.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase47 import ARM_PRODUCTION, ARM_RESEARCH
from app.research.phase50 import (
    ARM_PRODUCTION_ONLY,
    ARM_PRODUCTION_PLUS_RESEARCH,
    EARLY_SHADOW_RESULT,
    GROUP_DECLINED,
    GROUP_SELECTED,
    GROUP_UNMEASURED,
    NOT_A_PROMOTION,
    RESOLVED,
)
from app.research.phase50 import report as report_mod
from app.research.phase50 import service


def _n(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _bound_line(payload: dict, arm: str) -> str:
    bound = (payload.get("bound") or {}).get(arm) or {}
    return (
        f"available {bound.get('available')}  selected {bound.get('selected')}  "
        f"limit {bound.get('limit')}  {bound.get('count_is')}"
    )


def _events(args: argparse.Namespace) -> int:
    payload = service.events(
        session=args.session, instrument=args.instrument, limit=args.limit,
    )
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print("SHADOW EVENTS — LEG COUNT AND EVENT COUNT, PAPER ONLY")
    print(f"  session {payload['session'] or '—'}  "
          f"grouping {payload['rule_fingerprint']}  "
          f"resolution {payload['resolution_fingerprint']}")
    if payload.get("holiday_note"):
        print(f"  {payload['holiday_note']}")
    for arm in (ARM_RESEARCH, ARM_PRODUCTION):
        column = payload["arms"][arm]
        print()
        print(f"  {arm}")
        print(f"    LEG COUNT {column['leg_count']}   "
              f"EVENT COUNT {column['event_count']}")
        print(f"    {_bound_line(payload, arm)}")
        for row in column["events"]:
            print(f"    {row['event_id']}  {str(row['contract'] or '—'):<24} "
                  f"{row['observation_count']:>4} obs  "
                  f"{_n(row['duration_seconds']):>8}s  "
                  f"{str(row['market_session_status']):<10} "
                  f"{row['resolution_status']}")
            if row.get("resolution_status") == RESOLVED:
                print(f"       entry {row['entry_side']} {_n(row['entry_price'])}"
                      f"  exit {row['exit_side']} {_n(row['exit_price'])}"
                      f"  net% {_n(row['net_pct'])}"
                      f"  MFE {_n(row['mfe_pct'])}  MAE {_n(row['mae_pct'])}"
                      f"  giveback {_n(row['giveback_pct'])}")
                print(f"       exit chosen from {row['forward_samples']} "
                      f"executable books "
                      f"({row['forward_samples_from_observations']} observed by "
                      f"this event, "
                      f"{row['forward_samples_from_quote_store']} from the quote "
                      f"store), {row['forward_samples_dropped']} dropped for "
                      f"not quoting the exit side")
        met = column["metrics"]
        print(f"    resolved {met['resolved_events']}  "
              f"unresolved {met['unresolved_events']}  "
              f"net% total {_n(met['net_pct_total'])}  "
              f"win% {_n(met['win_rate_pct'])}  PF {_n(met['profit_factor'])}")
        for reason, count in (met.get("unresolved_reasons") or {}).items():
            print(f"      {count:>4}  {reason}")
    print()
    print(f"  {NOT_A_PROMOTION}")
    return 0


def _board(args: argparse.Namespace) -> int:
    payload = service.dashboard(
        session=args.session, instrument=args.instrument, limit=args.limit,
    )
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print("ACTIVE SHADOW EVENTS — ONE LINE PER OPPORTUNITY, NOT PER QUOTE")
    print(f"  session {payload['session'] or '—'}  "
          f"gap {payload['event_gap_sec']}s  rule {payload['rule_fingerprint']}")
    for title, key in (("ACTIVE", "active_events"),
                       ("COMPLETED", "completed_events")):
        rows = payload[key]
        print()
        print(f"  {title} ({len(rows)})")
        for row in rows:
            print(f"    {str(row['instrument'] or '—'):<12} "
                  f"{str(row['vehicle'] or '—'):<8} "
                  f"{str(row['shadow_state'] or '—'):<12} "
                  f"{row['event_id']}  {row['observation_count']:>4} obs = "
                  f"1 event  {_n(row['event_duration_sec']):>8}s  "
                  f"MARKET = {row['market_session_status']}  "
                  f"{row['data_quality']}  PAPER ONLY")
    print()
    print(f"  legs {payload['leg_counts']}  events {payload['event_counts']}")
    return 0


def _compare(args: argparse.Namespace) -> int:
    payload = service.compare(
        session=args.session, instrument=args.instrument, limit=args.limit,
    )
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print(f"PRODUCTION VS PRODUCTION PLUS OVERLAY — {EARLY_SHADOW_RESULT}")
    print("  DEGENERATE WHILE THE OVERLAY IS A SUBSET OF PRODUCTION — "
          "NOT A PERFORMANCE COMPARISON")
    print("  the answerable question is in `filter` (selected vs declined)")
    print(f"  session {payload['session'] or '—'}  "
          f"events counted as independent opportunities, not legs")
    arm_a = payload["arms"][ARM_PRODUCTION_ONLY]
    arm_b = payload["arms"][ARM_PRODUCTION_PLUS_RESEARCH]
    print()
    print(f"  {'metric':<28}{'A production':>16}{'B + overlay':>16}"
          f"{'B - A':>12}")
    for field in ("events", "resolved_events", "unresolved_events",
                  "net_pct_total", "net_pct_mean_per_event", "profit_factor",
                  "win_rate_pct", "max_drawdown_pct", "avg_cost_points",
                  "mfe_pct_mean", "mae_pct_mean", "giveback_pct_mean",
                  "t1_hit_rate_pct"):
        print(f"  {field:<28}{_n(arm_a['metrics'].get(field)):>16}"
              f"{_n(arm_b['metrics'].get(field)):>16}"
              f"{_n((payload['delta'] or {}).get(field)):>12}")
    print()
    print(f"  legs   A {arm_a['leg_count']}   B {arm_b['leg_count']}")
    print(f"  events A {arm_a['event_count']}   B {arm_b['event_count']}   "
          f"only in B {payload['events_only_in_b']}")
    verdict = payload["verdict"]
    print(f"  VERDICT {verdict['state']}  "
          f"(bar {payload['min_resolved_events']} resolved events per arm)")
    print()
    print(f"  {payload['overlap']}")
    print(f"  {NOT_A_PROMOTION}")
    return 0


def _filter(args: argparse.Namespace) -> int:
    payload = service.filter_compare(
        session=args.session, instrument=args.instrument, limit=args.limit,
    )
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    counts = payload["group_counts"]
    legs = payload["group_leg_counts"]
    print(f"OVERLAY AS A FILTER — SELECTED VS DECLINED — {EARLY_SHADOW_RESULT}")
    print(f"  session {payload['session'] or '—'}   "
          f"production events {payload['production_events']}   "
          f"legs {payload['production_legs']}")
    print()
    print(f"  {'group':<20}{'events':>8}{'legs':>8}")
    for group in (GROUP_SELECTED, GROUP_DECLINED, GROUP_UNMEASURED):
        print(f"  {group:<20}{counts.get(group, 0):>8}"
              f"{legs.get(group, 0):>8}")
    check = payload["partition_check"]
    print(f"  exhaustive and disjoint {check['exhaustive_and_disjoint']}   "
          f"unassigned {check['unassigned']}   "
          f"duplicate ids {check['duplicate_event_ids']}")
    for reason, count in (payload.get("unmeasured_reasons") or {}).items():
        print(f"  unmeasured because {reason}: {count}")
    guard = payload["coverage_guard"]
    print()
    print("  PRE-REGISTERED COVERAGE GUARD  "
          f"[{guard['fingerprint']}]")
    print(f"  comparable only within {guard['max_resolution_rate_gap_pp']}pp "
          f"and a factor of {guard['max_resolution_rate_ratio']} on resolution "
          f"rate")
    print(f"  {guard['resolution_is_not_performance']}")
    print(f"  cost bands cut on {payload['cost_band_basis']}")
    for policy_id in payload["primary_policies"]:
        _policy_block(payload["policies"][policy_id])
    for policy_id in payload["primary_policies"]:
        _strata_block(payload["policies"][policy_id])
    print()
    print("  COVERAGE DIAGNOSTIC — NOT A TRADING EXIT, NOT A STRATEGY RESULT")
    _policy_block(payload["policies"][payload["coverage_endpoint"]])
    print()
    print(f"  exit policy set {payload['exit_policy_set_fingerprint']}   "
          f"grouping {payload['rule_fingerprint']}   "
          f"resolution {payload['resolution_fingerprint']}   "
          f"partition {payload['partition_fingerprint']}")
    print(f"  {payload['subset_note']}")
    print(f"  {NOT_A_PROMOTION}")
    return 0


def _policy_block(block: dict) -> None:
    chosen = block["columns"][GROUP_SELECTED]
    declined = block["columns"][GROUP_DECLINED]
    delta = block["selected_minus_declined"] or {}
    print()
    print(f"  {block['policy_id']}  [{block['fingerprint']}]  "
          f"params {block['params'] or '—'}")
    print(f"  {'metric':<28}{'SELECTED':>14}{'DECLINED':>14}"
          f"{'SEL - DEC':>12}")
    for field in ("events", "resolved_events", "unresolved_events",
                  "win_rate_pct", "gross_pct_mean_per_event",
                  "avg_cost_points", "net_pct_mean_per_event",
                  "net_pct_total", "profit_factor", "max_drawdown_pct",
                  "mfe_pct_mean", "mae_pct_mean", "giveback_pct_mean",
                  "t1_hit_rate_pct"):
        print(f"  {field:<28}{_n(chosen.get(field)):>14}"
              f"{_n(declined.get(field)):>14}{_n(delta.get(field)):>12}")
    cover = block["coverage"]
    for name in (GROUP_SELECTED, GROUP_DECLINED):
        cohort = cover[name]
        print(f"  coverage {name:<20}"
              f"{cohort['eligible_events']} eligible / "
              f"{cohort['resolved_events']} resolved / "
              f"{cohort['unresolved_events']} unresolved / "
              f"{_n(cohort['resolution_rate_pct'])}%")
    print(f"  coverage {cover['status']}   "
          f"gap {_n(cover['resolution_rate_gap_pp'])}pp "
          f"(bar {cover['max_gap_pp']})   "
          f"ratio {_n(cover['resolution_rate_ratio'])} "
          f"(bar {cover['max_ratio']})")
    if block["delta_withheld_because"]:
        print(f"  SEL - DEC WITHHELD  {block['delta_withheld_because']}")
    print(f"  VERDICT {block['verdict']['state']}")


def _cohort(cohort: dict) -> str:
    """One cohort's coverage as eligible/resolved/rate — never beside a net figure."""
    return (f"{cohort['eligible_events']}/{cohort['resolved_events']}/"
            f"{_n(cohort['resolution_rate_pct'])}")


def _strata_block(block: dict) -> None:
    """The same two cohorts inside one instrument, one vehicle, one cost band."""
    print()
    print(f"  STRATIFIED {block['policy_id']}  [{block['fingerprint']}]")
    for title, field in (("instrument", "by_instrument"),
                         ("vehicle", "by_vehicle"),
                         ("cost band", "by_cost_band")):
        strata = (block.get("strata") or {}).get(field) or {}
        print(f"    by {title}")
        if not strata:
            print("      no events")
            continue
        print(f"      {'stratum':<44}{'SEL ev/res/rate':>20}"
              f"{'DEC ev/res/rate':>20}{'SEL net':>10}{'DEC net':>10}"
              f"{'SEL-DEC':>12}  status")
        for key, stratum in strata.items():
            cover = stratum["coverage"]
            left, right = cover[GROUP_SELECTED], cover[GROUP_DECLINED]
            columns = stratum["columns"]
            delta = stratum["selected_minus_declined"]
            legs = stratum["leg_counts"]
            sel = _cohort(left)
            dec = _cohort(right)
            net_sel = _n(columns[GROUP_SELECTED].get("net_pct_mean_per_event"))
            net_dec = _n(columns[GROUP_DECLINED].get("net_pct_mean_per_event"))
            spread = (
                _n(delta.get("net_pct_mean_per_event")) if delta else "withheld"
            )
            print(
                f"      {key[:44]:<44}{sel:>20}{dec:>20}"
                f"{net_sel:>10}{net_dec:>10}{spread:>12}"
                f"  {stratum['verdict']['state']}"
            )
            print(f"      {'':<44}legs {legs[GROUP_SELECTED]} / "
                  f"{legs[GROUP_DECLINED]}   {cover['status']}")


def _coverage(args: argparse.Namespace) -> int:
    payload = service.coverage_diagnostic(
        session=args.session, instrument=args.instrument, limit=args.limit,
    )
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    counts = payload["group_counts"]
    legs = payload["group_leg_counts"]
    print("FORWARD OBSERVATION COVERAGE — WHY THE COHORTS ARE NOT COMPARABLE")
    print(f"  session {payload['session'] or '—'}   "
          f"production events {payload['production_events']}   "
          f"legs {payload['production_legs']}   "
          f"[{payload['observation_diagnostic_fingerprint']}]")
    print(f"  {payload['diagnostic_has_no_outcome']}")
    _read_bound_block(payload["read_bound"])
    print()
    print(f"  {'group':<20}{'events':>8}{'legs':>8}")
    for group in (GROUP_SELECTED, GROUP_DECLINED, GROUP_UNMEASURED):
        print(f"  {group:<20}{counts.get(group, 0):>8}{legs.get(group, 0):>8}")
    check = payload["taxonomy_check"]
    print(f"  classified exactly once "
          f"{check['every_event_classified_exactly_once']}   "
          f"unclassified {check['unclassified']}   "
          f"outcome fields present {check['outcome_fields_present'] or 'none'}")
    _coverage_table(payload["comparison"])
    _distribution_block(payload["cohorts"])
    _attribution_block(payload["attribution"])
    _bias_block(payload["bias_sources"])
    for title, key in (("instrument", "by_instrument"),
                       ("vehicle", "by_vehicle"),
                       ("cost band", "by_cost_band"),
                       ("direction", "by_direction")):
        strata = (payload.get("strata") or {}).get(key) or {}
        print()
        print(f"  BY {title.upper()}")
        if not strata:
            print("    no events")
            continue
        print(f"    {'stratum':<26}{'SEL ev':>7}{'DEC ev':>7}"
              f"{'SEL gap s':>10}{'DEC gap s':>10}{'SEL obs':>8}{'DEC obs':>8}"
              f"{'SEL 5m%':>8}{'DEC 5m%':>8}  cadence")
        for name, stratum in strata.items():
            left = stratum["columns"]["selected"]
            right = stratum["columns"]["declined"]
            print(f"    {name[:26]:<26}{left['events']:>7}{right['events']:>7}"
                  f"{_n(left['median_inter_observation_sec']):>10}"
                  f"{_n(right['median_inter_observation_sec']):>10}"
                  f"{_n(left['median_observation_count']):>8}"
                  f"{_n(right['median_observation_count']):>8}"
                  f"{_n(left['pct_covered_to_5m']):>8}"
                  f"{_n(right['pct_covered_to_5m']):>8}"
                  f"  {(stratum.get('cadence_guard') or {}).get('status')}")
    _within_block(payload["bias_sources"]["within_instrument"])
    print()
    print("  REVISED EQUAL-CADENCE + EQUAL-WINDOW DESIGN (SPECIFICATION ONLY)")
    for term, text in (payload.get("equal_observation_policy_design") or {}).items():
        print(f"    {term:<32}{text}")
    print(f"    {payload['design_is_not_implemented']}")
    print()
    print(f"  {payload['diagnostic_is_read_only']}")
    print(f"  grouping {payload['rule_fingerprint']}   "
          f"resolution {payload['resolution_fingerprint']}   "
          f"exit set {payload['exit_policy_set_fingerprint']}   "
          f"partition {payload['partition_fingerprint']}   "
          f"guard {payload['coverage_guard_fingerprint']}   "
          f"cadence {payload['cadence_guard_fingerprint']}")
    print(f"  {NOT_A_PROMOTION}")
    return 0


def _read_bound_block(bound: dict) -> None:
    """How much was read, printed above every depth figure it could distort."""
    print(f"  {bound['status']}   legs read {bound['legs_read']}   "
          f"available {_n(bound['legs_available_in_session'])}   "
          f"limit {bound['effective_limit']}   "
          f"exhaustive {_n(bound['exhaustive'])}   "
          f"count is {bound['count_is']}")
    if bound["limit_was_reached"]:
        print(f"  {bound['conclusion_rule']}")


def _distribution_block(cohorts: dict) -> None:
    """The spread behind each median, for both cohorts, side by side."""
    left = cohorts.get(GROUP_SELECTED) or {}
    right = cohorts.get(GROUP_DECLINED) or {}
    fields = sorted(set(left.get("distributions") or {}))
    if not fields:
        return
    print()
    print("  CADENCE DISTRIBUTIONS — NOT ONLY THE MEDIAN")
    print(f"    {'field':<38}{'cohort':<10}{'n':>5}{'p10':>10}{'p25':>10}"
          f"{'median':>10}{'p75':>10}{'p90':>10}{'max':>12}")
    for field in fields:
        for label, column in ((GROUP_SELECTED, left), (GROUP_DECLINED, right)):
            row = (column.get("distributions") or {}).get(field) or {}
            print(f"    {field[:38]:<38}{label[8:18]:<10}{row.get('n', 0):>5}"
                  f"{_n(row.get('p10')):>10}{_n(row.get('p25')):>10}"
                  f"{_n(row.get('median')):>10}{_n(row.get('p75')):>10}"
                  f"{_n(row.get('p90')):>10}{_n(row.get('max')):>12}")
    print()
    print(f"    {'cadence label':<38}{'SELECTED':>12}{'DECLINED':>12}")
    for label in sorted(set(left.get("cadence_label_counts") or {})):
        sel = (left.get("cadence_label_counts") or {}).get(label, 0)
        dec = (right.get("cadence_label_counts") or {}).get(label, 0)
        print(f"    {label[:38]:<38}{sel:>12}{dec:>12}")


def _bias_block(bias: dict) -> None:
    """Each declared mechanism with its measurement and the bar it had to clear."""
    print()
    print("  BIAS SOURCE — MEASURED AGAINST A PRE-REGISTERED BAR")
    print(f"    {'mechanism':<44}{'measured':>10}{'bar':>8}{'past bar':>10}"
          f"  crossed")
    for row in bias["rows"]:
        print(f"    {row['source'][:44]:<44}{_n(row['measured']):>10}"
              f"{_n(row['bar']):>8}{_n(row['strength']):>10}"
              f"  {'yes' if row['crossed'] else 'no'}")
    if bias["dominant_source"]:
        print(f"    dominant {bias['dominant_source']}  "
              f"({_n(bias['dominant_strength'])}x its bar)")
    else:
        print(f"    {bias['unranked_because']}")
    print(f"    all-market depth ratio {_n(bias['all_market_depth_ratio'])}   "
          f"all-market cadence ratio {_n(bias['all_market_cadence_ratio'])}   "
          f"within-instrument depth ratio "
          f"{_n(bias['within_instrument']['median_within_instrument_depth_ratio'])}")
    print(f"    {bias['overlay_correlation_note']}")


def _within_block(within: dict) -> None:
    """§4: the instruments holding both cohorts, and the ones that cannot be compared."""
    print()
    print("  WITHIN-INSTRUMENT — THE ONLY COMPARISONS THE MIX DOES NOT CONFOUND")
    print(f"    {'instrument':<16}{'SEL ev':>7}{'DEC ev':>7}{'SEL obs':>9}"
          f"{'DEC obs':>9}{'SEL gap s':>10}{'DEC gap s':>10}{'depth x':>9}"
          f"{'cadence x':>10}  status")
    for row in within["per_instrument"]:
        if not row["both_cohorts_present"]:
            continue
        status = (
            row["cadence_guard"]["status"] if row["clears_event_floor_both_cohorts"]
            else f"TOO_FEW_EVENTS_FLOOR_{row['event_floor']}_BOTH_COHORTS"
        )
        print(f"    {row['instrument'][:16]:<16}{row['selected_events']:>7}"
              f"{row['declined_events']:>7}"
              f"{_n(row['selected_median_observations']):>9}"
              f"{_n(row['declined_median_observations']):>9}"
              f"{_n(row['selected_median_inter_observation_sec']):>10}"
              f"{_n(row['declined_median_inter_observation_sec']):>10}"
              f"{_n(row['depth_ratio']):>9}{_n(row['cadence_ratio']):>10}"
              f"  {status}")
    for row in within["per_instrument"]:
        if not row["clears_event_floor_both_cohorts"]:
            continue
        block = row["comparison"]
        left = block["columns"]["selected"]
        right = block["columns"]["declined"]
        print()
        print(f"    {row['instrument']} — SELECTED vs DECLINED")
        for field in ("events", "legs", "median_inter_observation_sec",
                      "median_observation_duration_sec",
                      "median_executable_observations_per_minute",
                      "pct_covered_to_5m", "pct_covered_to_15m",
                      "pct_covered_to_30m", "pct_covered_to_60m"):
            print(f"      {field:<44}{_n(left.get(field)):>10}"
                  f"{_n(right.get(field)):>10}")
        for reason, count in (left.get("stopped_by") or {}).items():
            other = (right.get("stopped_by") or {}).get(reason, 0)
            if not count and not other:
                continue
            print(f"      stopped_by {reason:<33}{count:>10}{other:>10}")
    print(f"    {within['only_these_are_answerable']}")


def _coverage_table(comparison: dict) -> None:
    """The §3 table: one coverage quantity per row, both cohorts, and the gap."""
    left = comparison["columns"]["selected"]
    right = comparison["columns"]["declined"]
    gaps = comparison["gaps"]
    print()
    print(f"  {'coverage quantity':<40}{'SELECTED':>12}{'DECLINED':>12}"
          f"{'SEL - DEC':>12}")
    for field in ("events", "legs", "median_observation_count",
                  "median_observation_duration_sec",
                  "median_inter_observation_sec",
                  "median_p95_inter_observation_sec",
                  "median_max_inter_observation_sec",
                  "median_observations_per_minute",
                  "median_later_book_count", "median_executable_quote_count",
                  "median_executable_observation_count",
                  "median_executable_observations_per_minute",
                  "median_forward_coverage_sec",
                  "pct_with_any_later_executable_quote",
                  "pct_remained_observable", "pct_covered_to_5m",
                  "pct_covered_to_15m", "pct_covered_to_30m",
                  "pct_covered_to_60m", "pct_reached_session_close"):
        print(f"  {field:<40}{_n(left.get(field)):>12}"
              f"{_n(right.get(field)):>12}{_n(gaps.get(field)):>12}")
    guard = comparison.get("cadence_guard") or {}
    if guard:
        print(f"  cadence ratio {_n(guard.get('cadence_ratio'))} against a "
              f"declared maximum of {_n(guard.get('max_ratio'))}   "
              f"{guard.get('status')}")
    print()
    print(f"  {'stopped by':<40}{'SELECTED':>12}{'DECLINED':>12}"
          f"{'SEL - DEC pp':>14}")
    for reason, count in (left.get("stopped_by") or {}).items():
        other = (right.get("stopped_by") or {}).get(reason, 0)
        if not count and not other:
            continue
        sel = f"{count} / {_n((left.get('stopped_by_pct') or {}).get(reason))}%"
        dec = f"{other} / {_n((right.get('stopped_by_pct') or {}).get(reason))}%"
        print(f"  {reason:<40}{sel:>12}{dec:>12}"
              f"{_n((comparison.get('stop_reason_gaps_pp') or {}).get(reason)):>14}")


def _attribution_block(attribution: dict) -> None:
    """Which measured cause the asymmetry sits in, or why none can be named."""
    print()
    print("  MEASURED SOURCE OF THE ASYMMETRY")
    if not attribution.get("attributable"):
        print(f"    not attributable: {attribution.get('reason')}")
        return
    for row in attribution["ranked"]:
        print(f"    {row['cause']:<34}{_n(row['contribution_pp']):>10}pp")
    print(f"    dominant {attribution['dominant_cause']}  "
          f"({_n(attribution['dominant_contribution_pp'])}pp)")
    residual = attribution["residual_depth_asymmetry"] or {}
    print(f"    residual depth: {residual.get('reasons_with_depth_ratio_beyond_2x')}"
          f" of {residual.get('reasons_compared')} shared stop reasons show a "
          f"2x-or-worse observation-depth ratio")
    for row in residual.get("per_stop_reason") or []:
        print(f"      {row['stop_reason']:<34}"
              f"sel {row['selected_events']} ev / "
              f"{_n(row['selected_median_observations'])} obs   "
              f"dec {row['declined_events']} ev / "
              f"{_n(row['declined_median_observations'])} obs   "
              f"ratio {_n(row['ratio'])}")
    print(f"    {attribution['overlay_cause_note']}")


def _readings(args: argparse.Namespace) -> int:
    payload = service.readings(session=args.session)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print("TALLY READINGS — AN EARLIER READING IS NOT A WRONG READING")
    print(f"  {'session':<12}{'arm':<32}{'legs':>6}{'events':>8}"
          f"{'held':>8}  state")
    for row in payload["readings"]:
        print(f"  {str(row.get('session')):<12}{str(row.get('arm')):<32}"
              f"{_n(row.get('calls')):>6}{_n(row.get('events')):>8}"
              f"{_n(row.get('journal_rows_available_at_read')):>8}  "
              f"{row.get('tally_state')}")
        if row.get("supersession_reason"):
            print(f"      superseded by {str(row.get('superseded_by'))[:12]}  "
                  f"reason {row.get('supersession_reason')}")
        print(f"      evidence {row.get('tally_state_evidence')}")
        if row.get("confounded_note"):
            print(f"      confounded {row.get('confounded_note')}")
    print()
    for state, count in (payload.get("state_counts") or {}).items():
        print(f"  {count:>4}  {state}")
    print()
    print(f"  {payload['append_only']}")
    return 0


def _record(args: argparse.Namespace) -> int:
    payload = service.record(
        session=args.session, instrument=args.instrument, limit=args.limit,
    )
    print(json.dumps(payload, indent=2, default=str))
    return 0


def _report(args: argparse.Namespace) -> int:
    payload = service.events(
        session=args.session, instrument=args.instrument, limit=args.limit,
    )
    comparison = service.compare(
        session=args.session, instrument=args.instrument, limit=args.limit,
    )
    filtered = service.filter_compare(
        session=args.session, instrument=args.instrument, limit=args.limit,
    )
    readings = service.readings(session=payload.get("session"))
    path = report_mod.write(payload, comparison, readings, filtered)
    print(f"wrote {path}")
    return 0


def _budget(args: argparse.Namespace) -> int:
    payload = service.poll_budget(
        session=args.session, instrument=args.instrument, limit=args.limit,
    )
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    cur = payload["current_policy"]
    prop = payload["proposed_policy"]
    cmp_ = payload["comparison"]
    print("POLL BUDGET — MEASURED CURRENT vs REPLAYED 15-SECOND SHADOW POLICY")
    print(f"  session {payload['session'] or '—'}   "
          f"production events {payload['production_events']}   "
          f"[{payload['poll_budget_fingerprint']}]")
    print(f"  {cur['budget_rule']}")
    print(f"  {cur['read_status']}   lines read {cur['lines_read']}   "
          f"files {cur['files_read']}")
    print(f"  {cur['attempt_count_is']}")
    print()
    print(f"  {'quantity':<42}{'CURRENT':>14}{'PROPOSED':>14}{'DELTA':>12}")
    for label, key in (
        ("total poll attempts", "total_poll_attempts"),
        ("poll attempts per minute", "attempts_per_minute"),
        ("peak attempts in one minute", "peak_attempts_in_one_minute"),
        ("observation rows / observations", "total_observation_rows"),
        ("fast-tier attempts", "fast_tier_attempts"),
        ("slow-tier attempts", "slow_tier_attempts"),
        ("wall span seconds", "wall_span_seconds"),
        ("mean of per-instrument mean interval s", "mean_poll_interval_sec"),
        ("median of per-instrument median int s", "median_poll_interval_sec"),
    ):
        a, b = cur.get(key), prop.get(key)
        delta = (
            round(b - a, 3)
            if isinstance(a, (int, float)) and isinstance(b, (int, float))
            else None
        )
        print(f"  {label:<42}{_n(a):>14}{_n(b):>14}{_n(delta):>12}")
    print()
    print("  NOT RECORDED ON THIS PATH — REPORTED ABSENT, NEVER MODELLED")
    for label, key in (
        ("CPU seconds", "cpu_seconds"),
        ("request count", "http_request_count"),
        ("timeout count", "timeout_count"),
        ("failed request count", "failed_request_count"),
        ("skipped / duplicate polls", "skipped_or_duplicate_polls"),
    ):
        print(f"    {label:<28}{cur.get(key)}")
    print()
    print(f"  SAFETY VERDICT   {cmp_['verdict']}")
    for reason in cmp_["blocking_reasons"] or []:
        print(f"    blocking       {reason}")
    print(f"    {cmp_['safe_rule']}")
    feas = cmp_["feasibility"]
    print(f"    eligible {feas['eligible_events']}   "
          f"grid-satisfiable {feas['feasible_events']}   "
          f"infeasible {feas['infeasible_events']}")
    print()
    print("  EVENT-LEVEL FAIRNESS UNDER THE 15-SECOND POLICY")
    fair = payload["fairness"]
    header = f"    {'quantity':<44}"
    for group in (GROUP_SELECTED, GROUP_DECLINED, GROUP_UNMEASURED):
        header += f"{group.replace('OVERLAY_', '')[:11]:>12}"
    print(header)
    for label, key in (
        ("eligible events", "eligible_events"),
        ("observed events", "observed_events"),
        ("resolved events", "resolved_events"),
        ("unresolved events", "unresolved_events"),
        ("resolution rate %", "resolution_rate_pct"),
        ("median observation count", "median_policy_observation_count"),
        ("median inter-observation sec",
         "median_policy_inter_observation_seconds"),
        ("median observation duration sec",
         "median_policy_observation_duration_seconds"),
        ("median scheduled instants", "median_policy_scheduled_instants"),
        ("median satisfied %", "median_policy_satisfied_pct"),
        ("5m coverage %", "pct_policy_covered_to_5m"),
        ("15m coverage %", "pct_policy_covered_to_15m"),
        ("30m coverage %", "pct_policy_covered_to_30m"),
        ("60m coverage %", "pct_policy_covered_to_60m"),
    ):
        row = f"    {label:<44}"
        for group in (GROUP_SELECTED, GROUP_DECLINED, GROUP_UNMEASURED):
            row += f"{_n((fair.get(group) or {}).get(key)):>12}"
        print(row)
    eq = payload["equalisation"]
    print()
    print(f"  {eq['verdict']}   selected "
          f"{_n(eq['selected_median_inter_observation_seconds'])}s   declined "
          f"{_n(eq['declined_median_inter_observation_seconds'])}s   "
          f"declared {_n(eq['declared_cadence_sec'])}s   "
          f"tolerated {_n(eq['tolerated_median_sec'])}s")
    print(f"    publishable {_n(eq['publishable'])}   "
          f"withheld reason {eq['withheld_reason'] or '—'}")
    print()
    print("  WITHIN INSTRUMENT (POLICY CADENCE, SELECTED vs DECLINED)")
    print(f"    {'instrument':<16}{'SEL ev':>7}{'DEC ev':>7}{'SEL gap s':>11}"
          f"{'DEC gap s':>11}{'SEL 5m%':>9}{'DEC 5m%':>9}")
    for inst, cols in (payload["within_instrument"] or {}).items():
        sel = cols.get(GROUP_SELECTED) or {}
        dec = cols.get(GROUP_DECLINED) or {}
        if not sel.get("eligible_events") and not dec.get("eligible_events"):
            continue
        print(f"    {inst[:16]:<16}{sel.get('eligible_events', 0):>7}"
              f"{dec.get('eligible_events', 0):>7}"
              f"{_n(sel.get('median_policy_inter_observation_seconds')):>11}"
              f"{_n(dec.get('median_policy_inter_observation_seconds')):>11}"
              f"{_n(sel.get('pct_policy_covered_to_5m')):>9}"
              f"{_n(dec.get('pct_policy_covered_to_5m')):>9}")
    print()
    print(f"  {payload['policy_is_read_only']}")
    print(f"  {payload['pooling_forbidden']}")
    print(f"  observation policy {payload['observation_policy_fingerprint']}   "
          f"poll budget {payload['poll_budget_fingerprint']}")
    print(f"  grouping {payload['rule_fingerprint']}   "
          f"resolution {payload['resolution_fingerprint']}   "
          f"exit set {payload['exit_policy_set_fingerprint']}   "
          f"partition {payload['partition_fingerprint']}   "
          f"guard {payload['coverage_guard_fingerprint']}   "
          f"cadence {payload['cadence_guard_fingerprint']}")
    print(f"  {NOT_A_PROMOTION}")
    return 0


def _cadence(args: argparse.Namespace) -> int:
    payload = service.tier_cadence(session=args.session, limit=args.limit)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    pol = payload["tier_policy"]
    fresh = payload["fresh_sample"]
    print("SHADOW RECORDER TIER CADENCE")
    print(f"  {pol['policy']}   {pol['state']}   [{pol['tier_fingerprint']}]")
    print(f"  slow tier {_n(pol['previous_slow_tier_sec'])}s -> "
          f"{_n(pol['slow_tier_sec'])}s   sampled "
          f"{','.join(pol['sampled_instruments'])}")
    print(f"  fast tier {pol['fast_tier']}   ungated names "
          f"{','.join(pol['fast_instruments'])}")
    print(f"  {pol['tier_rule']}")
    print(f"  {pol['fast_tier_unchanged']}")
    print(f"  {pol['no_branch']}")
    print(f"  {pol['not_equal_market']}")
    print(f"  {pol['gate_is_not_a_quote']}")
    print(f"  {pol['cost_is_writes_not_requests']}")
    print(f"  {pol['rollback']}")
    print()
    print("  FRESH SAMPLE UNDER THIS CADENCE")
    print(f"    sessions captured {fresh['sessions_captured']}   "
          f"required {fresh['sessions_required']}   "
          f"remaining {fresh['sessions_remaining']}   "
          f"checkpoint reached {_n(fresh['checkpoint_reached'])}")
    print(f"    sessions {', '.join(fresh['sessions']) or '—'}")
    print(f"    pre-change sessions counted separately "
          f"{payload['pre_change_sessions']}")
    print(f"    {fresh['rule']}")
    print()
    print("  SESSIONS BY CADENCE FINGERPRINT — NEVER POOLED")
    print(f"    {'fingerprint':<20}{'slow s':>9}{'sessions':>10}{'current':>9}")
    for key, block in sorted(payload["by_cadence_fingerprint"].items()):
        print(f"    {key[:20]:<20}{_n(block.get('slow_tier_sec')):>9}"
              f"{block['session_count']:>10}"
              f"{_n(block['is_current_policy']):>9}")
    health = payload["capture_health"]
    if health is not None:
        print()
        print(f"  MEASURED CAPTURE HEALTH — {health['session']}   "
              f"under this cadence {_n(health['captured_under_current_policy'])}")
        print(f"    {health['read_status']}   exhaustive "
              f"{_n(health['exhaustive'])}")
        for label, key in (
            ("poll attempts", "total_poll_attempts"),
            ("attempts per minute", "attempts_per_minute"),
            ("peak attempts in one minute", "peak_attempts_in_one_minute"),
            ("observation rows", "total_observation_rows"),
            ("median of per-instrument median int s", "median_poll_interval_sec"),
            ("fast-tier attempts", "fast_tier_attempts"),
            ("slow-tier attempts", "slow_tier_attempts"),
            ("capture stall lines", "stall_lines"),
            ("tick ran, no row lines", "tick_ran_no_row_lines"),
        ):
            print(f"    {label:<30}{_n(health.get(key)):>14}")
        print(f"    {health['attempt_count_is']}")
        for label, key in (
            ("CPU seconds", "cpu_seconds"),
            ("request count", "http_request_count"),
            ("timeout count", "timeout_count"),
            ("failed request count", "failed_request_count"),
            ("skipped / duplicate polls", "skipped_or_duplicate_polls"),
        ):
            print(f"    {label:<28}{health.get(key)}")
    print()
    print(f"  {payload['pooling_forbidden']}")
    print(f"  tier cadence {pol['tier_fingerprint']}   "
          f"observation policy {payload['observation_policy_fingerprint']}   "
          f"poll budget {payload['poll_budget_fingerprint']}")
    print(f"  grouping {payload['rule_fingerprint']}   "
          f"resolution {payload['resolution_fingerprint']}   "
          f"exit set {payload['exit_policy_set_fingerprint']}   "
          f"partition {payload['partition_fingerprint']}   "
          f"guard {payload['coverage_guard_fingerprint']}   "
          f"cadence {payload['cadence_guard_fingerprint']}")
    print(f"  {payload['production_effect']}")
    print(f"  {NOT_A_PROMOTION}")
    return 0


def _status(args: argparse.Namespace) -> int:
    print(json.dumps(service.status(), indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phase50")
    subs = parser.add_subparsers(dest="command", required=True)
    for name, handler, writes in (
        ("events", _events, False),
        ("board", _board, False),
        ("compare", _compare, False),
        ("filter", _filter, False),
        ("coverage", _coverage, False),
        ("budget", _budget, False),
        ("cadence", _cadence, False),
        ("readings", _readings, False),
        ("record", _record, True),
        ("report", _report, False),
    ):
        sub = subs.add_parser(name)
        sub.add_argument("--session", default=None)
        sub.add_argument("--instrument", default=None)
        sub.add_argument("--limit", type=int, default=service.DEFAULT_LIMIT)
        if not writes:
            sub.add_argument("--json", action="store_true")
        sub.set_defaults(func=handler)
    status = subs.add_parser("status")
    status.set_defaults(func=_status)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
