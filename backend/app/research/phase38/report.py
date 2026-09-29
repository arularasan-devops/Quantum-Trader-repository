"""Phase 38 — the markdown document, in the order and shape §13 asks for.

Nothing is computed here. Every number is looked up from the state built by
:mod:`app.research.phase38.service`, so the document and the JSON cannot
disagree — a report that recomputes anything eventually prints a figure the
machine-readable payload does not contain.
"""
from __future__ import annotations

from app.research.phase35 import CE, FUTURES, PE
from app.research.phase38 import (
    HEADER_LINES,
    INSUFFICIENT_EVIDENCE,
    PAPER_ONLY,
    RESEARCH_ONLY,
    VERSION,
)

VEHICLES: tuple[str, ...] = (FUTURES, CE, PE)


def _r(val, *, suffix: str = "", dash: str = "—") -> str:
    if val is None:
        return dash
    if isinstance(val, bool):
        return "yes" if val else "no"
    if isinstance(val, float):
        if val == float("inf"):
            return "inf"
        return f"{val:,.2f}{suffix}"
    if isinstance(val, int):
        return f"{val:,}{suffix}"
    return str(val)


def _flag(body: dict) -> str:
    return INSUFFICIENT_EVIDENCE if body.get("insufficient") else ""


def render(state: dict) -> str:
    cov = state.get("coverage") or {}
    horizon = state.get("reference_horizon")
    lines: list[str] = []
    add = lines.append

    add(f"SESSION_COUNT = {state.get('session_count')}")
    add("")
    for line in HEADER_LINES:
        add(line)
    add("")
    add(f"# CRUDEOIL — Phase 36 loss diagnostic ({state.get('instrument')})")
    add("")
    add(f"Phase 38 v{VERSION} · {RESEARCH_ONLY} · {PAPER_ONLY} · reference "
        f"horizon **{horizon}** minutes")
    add("")
    add(f"- sessions measured: {_r(state.get('session_count'))} "
        f"({', '.join(state.get('sessions') or []) or '—'})")
    add(f"- eligible triples: {_r(cov.get('eligible'))} of "
        f"{_r(cov.get('observations'))} observations")
    add(f"- entered legs: {_r(cov.get('vehicle_rows_entered'))} of "
        f"{_r(cov.get('vehicle_rows'))} resolved")
    add(f"- legs with no measurable round-trip cost: "
        f"{_r(cov.get('entered_without_measured_cost'))} "
        f"({_r(state.get('unmeasured_cost_pct'), suffix='%')})")
    add("")
    add("Every rupee figure is one lot per leg. Sizing is production logic and "
        "is not modelled here.")
    add("")

    add("## 1. Vehicle P&L decomposition — GROSS → COSTS → NET")
    add("")
    add("| vehicle | trades | gross ₹ | spread ₹ | brokerage ₹ | statutory ₹ | "
        "slippage ₹ | net ₹ | net ₹/trade | net %/leg | win % | PF | sample |")
    add("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    pnl = state.get("vehicle_pnl") or {}
    for v in VEHICLES:
        row = pnl.get(v) or {}
        add(f"| {v} | {_r(row.get('trades'))} | {_r(row.get('gross_rupees'))} "
            f"| {_r(row.get('spread_rupees'))} "
            f"| {_r(row.get('brokerage_rupees'))} "
            f"| {_r(row.get('statutory_rupees'))} "
            f"| {_r(row.get('slippage_rupees'))} "
            f"| {_r(row.get('net_rupees'))} "
            f"| {_r(row.get('net_rupees_per_trade'))} "
            f"| {_r(row.get('net_mean_pct'), suffix='%')} "
            f"| {_r(row.get('win_pct'), suffix='%')} "
            f"| {_r(row.get('profit_factor'))} | {_flag(row)} |")
    add("")
    add(f"`net R` is not reported: {(pnl.get(FUTURES) or {}).get('net_r_reason')}"
        ". Net per leg in percent of entry is given in its place.")
    add("")

    add("## 2. Move vs cost")
    add("")
    mvc = state.get("move_vs_cost") or {}
    add(f"- legs categorised: {_r(mvc.get('legs'))}")
    add(f"- favourable move / round-trip cost: mean "
        f"{_r(mvc.get('move_over_cost_mean'))}, median "
        f"{_r(mvc.get('move_over_cost_median'))}")
    add(f"- legs whose excursion cleared their own round trip: "
        f"{_r(mvc.get('cleared_own_cost_pct'), suffix='%')}")
    add("")
    add("| category | legs | share | gross ₹ | cost ₹ | net ₹ | mean MFE % |")
    add("|---|---|---|---|---|---|---|")
    for cat, body in (mvc.get("by_category") or {}).items():
        add(f"| {cat} | {_r(body.get('n'))} "
            f"| {_r(body.get('share_pct'), suffix='%')} "
            f"| {_r(body.get('gross_rupees'))} "
            f"| {_r(body.get('cost_rupees'))} "
            f"| {_r(body.get('net_rupees'))} "
            f"| {_r(body.get('mfe_mean_pct'), suffix='%')} |")
    add("")
    for cat, text in (mvc.get("definitions") or {}).items():
        add(f"- `{cat}` — {text}")
    add("")

    add("## 3. Hold time")
    add("")
    hold = state.get("hold_time") or {}
    for v in VEHICLES:
        body = hold.get(v) or {}
        add(f"### {v}")
        add("")
        add("| horizon | n | gross ₹ | net ₹ | net ₹/trade | PF | win % | "
            "MFE % | MAE % | sample |")
        add("|---|---|---|---|---|---|---|---|---|---|")
        for h, s in (body.get("by_horizon") or {}).items():
            add(f"| {h} | {_r(s.get('n'))} | {_r(s.get('gross_rupees'))} "
                f"| {_r(s.get('net_rupees'))} "
                f"| {_r(s.get('net_rupees_per_trade'))} "
                f"| {_r(s.get('profit_factor'))} "
                f"| {_r(s.get('win_pct'), suffix='%')} "
                f"| {_r(s.get('mfe_mean_pct'), suffix='%')} "
                f"| {_r(s.get('mae_mean_pct'), suffix='%')} | {_flag(s)} |")
        add("")
        add(f"least-negative horizon: **{_r(body.get('least_negative_horizon'))}"
            f"** at {_r(body.get('least_negative_net_per_trade'))} ₹/trade — "
            f"{body.get('note')}")
        add("")

    add("## 4. Giveback — money available vs money captured")
    add("")
    gb = state.get("giveback") or {}
    add("| vehicle | losing trades | of those, never went green | available ₹ "
        "| captured ₹ | given back ₹ | capture % | median % of MFE returned "
        "| went green | returned to entry % | turned negative % | sample |")
    add("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for v in VEHICLES:
        row = (gb.get("by_vehicle") or {}).get(v) or {}
        add(f"| {v} | {_r(row.get('losing_trades'))} "
            f"| {_r(row.get('never_went_green_n'))} "
            f"| {_r(row.get('money_available_rupees'))} "
            f"| {_r(row.get('money_captured_rupees'))} "
            f"| {_r(row.get('money_given_back_rupees'))} "
            f"| {_r(row.get('capture_rate_pct'), suffix='%')} "
            f"| {_r(row.get('pct_of_mfe_returned_median'), suffix='%')} "
            f"| {_r(row.get('went_green_n'))} "
            f"| {_r(row.get('returned_to_entry_pct'), suffix='%')} "
            f"| {_r(row.get('turned_negative_pct'), suffix='%')} "
            f"| {_flag(row)} |")
    add("")
    add(f"- {gb.get('note')}")
    add("")

    add("## 5. CE vs PE at the same instant")
    add("")
    cp = state.get("ce_vs_pe") or {}
    add(f"- same-timestamp pairs: {_r(cp.get('pairs'))} {_flag(cp)}")
    add("")
    add("| comparison | CE better | PE better | tie | not measurable |")
    add("|---|---|---|---|---|")
    for key, body in (cp.get("wins") or {}).items():
        add(f"| {key} | {_r(body.get(CE))} | {_r(body.get(PE))} "
            f"| {_r(body.get('TIE'))} | {_r(body.get('UNMEASURED'))} |")
    add("")
    add(f"{cp.get('note')}")
    add("")

    add("## 6. Futures vs options on the identical opportunity")
    add("")
    fvo = state.get("futures_vs_options") or {}
    add(f"- instants with both a future and its directional option: "
        f"{_r(fvo.get('complete_triples'))} {_flag(fvo)}")
    add(f"- option excursion exceeded the future's: "
        f"{_r(fvo.get('option_excursion_beat_futures_pct'), suffix='%')}")
    add("")
    add("| pattern | instants | share |")
    add("|---|---|---|")
    for pattern, n in (fvo.get("patterns") or {}).items():
        share = (fvo.get("pattern_share_pct") or {}).get(pattern)
        add(f"| {pattern} | {_r(n)} | {_r(share, suffix='%')} |")
    add("")
    add(f"{fvo.get('note')}")
    add("")

    add("## 7. Entry timing (counterfactual)")
    add("")
    ent = state.get("entry_timing") or {}
    add("| entry offset | legs | net ₹ | net ₹/trade | net %/leg | win % | "
        "sample |")
    add("|---|---|---|---|---|---|---|")
    for label, s in (ent.get("by_offset") or {}).items():
        add(f"| {label} | {_r(s.get('n'))} | {_r(s.get('net_rupees'))} "
            f"| {_r(s.get('net_rupees_per_trade'))} "
            f"| {_r(s.get('net_mean_pct'), suffix='%')} "
            f"| {_r(s.get('win_pct'), suffix='%')} | {_flag(s)} |")
    add("")
    add(f"verdict: **{ent.get('label')}** — {state.get('entry_note')}")
    add("")
    add(f"`TOO_LATE` is not reachable from this data: {ent.get('too_late_note')}.")
    add("")

    add("## 8. Premium bands")
    add("")
    pb = state.get("premium_bands") or {}
    add("| band | vehicle | trades | mean spread % | gross % | cost % | net % "
        "| net ₹ | cost/MFE % | sample |")
    add("|---|---|---|---|---|---|---|---|---|---|")
    for band, per in (pb.get("by_band") or {}).items():
        for v, s in per.items():
            add(f"| {band} | {v} | {_r(s.get('trades'))} "
                f"| {_r(s.get('spread_pct_mean'), suffix='%')} "
                f"| {_r(s.get('gross_mean_pct'), suffix='%')} "
                f"| {_r(s.get('cost_mean_pct'), suffix='%')} "
                f"| {_r(s.get('net_mean_pct'), suffix='%')} "
                f"| {_r(s.get('net_rupees'))} "
                f"| {_r(s.get('cost_over_mfe_pct'), suffix='%')} "
                f"| {_flag(s)} |")
    add("")
    walls = pb.get("cost_wall_bands") or []
    where = ", ".join(walls) if walls else "no band above the evidence floor"
    add(f"cost wall present in: {where} — {pb.get('cost_wall_definition')}")
    add("")

    add("## 9. DTE and moneyness")
    add("")
    dm = state.get("dte_and_moneyness") or {}
    for name in ("dte", "moneyness"):
        add(f"### {name}")
        add("")
        add("| cohort | vehicle | trades | gross % | cost % | net % | win % | "
            "PF | sample |")
        add("|---|---|---|---|---|---|---|---|---|")
        for label, body in (dm.get(name) or {}).items():
            for v, s in (body.get("by_vehicle") or {}).items():
                add(f"| {label} | {v} | {_r(s.get('trades'))} "
                    f"| {_r(s.get('gross_mean_pct'), suffix='%')} "
                    f"| {_r(s.get('cost_mean_pct'), suffix='%')} "
                    f"| {_r(s.get('net_mean_pct'), suffix='%')} "
                    f"| {_r(s.get('win_pct'), suffix='%')} "
                    f"| {_r(s.get('profit_factor'))} | {_flag(s)} |")
        add("")
    add(f"{dm.get('note')}")
    add("")

    add("## 10. Money-loss waterfall")
    add("")
    wf = state.get("waterfall") or {}
    add("```")
    for step in wf.get("steps") or []:
        amount = step.get("rupees")
        add(f"{step['step']:<26} {_r(amount):>14}   running "
            f"{_r(step.get('running_rupees')):>14}")
    add("```")
    add("")
    add(f"- legs in the waterfall: {_r(wf.get('n_legs'))}")
    add(f"- arithmetic closes: {_r(wf.get('closes'))} (residual "
        f"{_r(wf.get('residual_rupees'))} ₹, unexplained cost lines "
        f"{_r(wf.get('cost_lines_unexplained_rupees'))} ₹)")
    add(f"- {wf.get('note')}")
    add("")

    add("## 11. Final diagnosis")
    add("")
    add("| rank | cause | kind | rupees | legs | cohort above floor | evidence |")
    add("|---|---|---|---|---|---|---|")
    for i, row in enumerate(state.get("ranked_causes") or [], start=1):
        add(f"| {i} | {row.get('cause')} | {row.get('kind')} "
            f"| {_r(row.get('rupees'))} | {_r(row.get('legs'))} "
            f"| {_r(row.get('sufficient'))} | {row.get('evidence')} |")
    add("")
    add("Only the three REALISED_LOSS causes sum to the final net; the "
        "COUNTERFACTUAL rows are opportunities measured on the same legs and "
        "would double-count if added.")
    add("")

    add("## 12. What this cannot say")
    add("")
    add(f"- {state.get('session_count')} session(s) of evidence. This document "
        "produces no promotion status of any kind: the vocabulary it can emit "
        "is three loss drivers and one of three actions, none of which enables "
        "anything.")
    add("- No signal, strike, target, stop, exit, sizing or order-path logic "
        "was read for a decision or changed.")
    add("- Every counterfactual above is measured on the legs that were "
        "actually captured; none of them is a rule the system has.")
    add("")

    diag = state.get("diagnosis") or {}
    act = state.get("action") or {}
    add("---")
    add("")
    # The reason goes above the three required lines, never below: §13 says the
    # document ends with them, and a trailing sentence would be the first thing
    # a reader quotes.
    add(f"_{act.get('reason')}_")
    add("")
    add(f"PRIMARY LOSS DRIVER: {diag.get('primary')}"
        f"{_impact(diag.get('primary_rupees'))}")
    add(f"SECONDARY LOSS DRIVER: {diag.get('secondary')}"
        f"{_impact(diag.get('secondary_rupees'))}")
    add(f"CURRENT ACTION: {act.get('action')}")
    return "\n".join(lines) + "\n"


def final_net(state: dict):
    """The waterfall's last step, or None when there was nothing to convert."""
    steps = (state.get("waterfall") or {}).get("steps") or []
    return steps[-1].get("rupees") if steps else None


def _impact(rupees) -> str:
    if not isinstance(rupees, (int, float)):
        return ""
    return f" ({rupees:,.2f} ₹ over the measured legs)"


def headline(state: dict) -> str:
    """The short block the CLI prints."""
    diag = state.get("diagnosis") or {}
    act = state.get("action") or {}
    cov = state.get("coverage") or {}
    final = final_net(state)
    lines = [
        f"CRUDEOIL PHASE 36 LOSS DIAGNOSTIC — {state.get('instrument')}",
        f"  SESSION_COUNT            : {state.get('session_count')}",
        f"  eligible triples         : {cov.get('eligible')} of "
        f"{cov.get('observations')} observations",
        f"  reference horizon        : {state.get('reference_horizon')} min",
        f"  final net (one lot/leg)  : {_r(final)} ₹",
        f"  PRIMARY LOSS DRIVER      : {diag.get('primary')}",
        f"  SECONDARY LOSS DRIVER    : {diag.get('secondary')}",
        f"  CURRENT ACTION           : {act.get('action')}",
        "  THIS IS A SESSION DIAGNOSTIC. IT IS NOT A STRATEGY VERDICT.",
        f"  {RESEARCH_ONLY} / {PAPER_ONLY} — no order path, no production change",
    ]
    return "\n".join(lines)
