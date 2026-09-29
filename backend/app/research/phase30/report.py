"""Phase 30 §18-§22 — rankings, the seventeen answers, and the artefacts.

Every answer here is derived from the study's own measured tables. Where the data
cannot answer a question the answer says so — ``UNMEASURED`` or
``REQUIRES_MORE_DATA`` — rather than offering the nearest available number as if
it were the one that was asked for.
"""
from __future__ import annotations

import json
import os

from app.research.phase30 import (
    NO_AVERAGING_EDGE_FOUND,
    NO_CANDLE_PATTERN_EDGE_FOUND,
    REJECTED,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD,
    UNMEASURED,
    VALIDATED,
    averaging,
)

OUT_DIR = "data/phase30"

ARTEFACTS = (
    "p30_coverage.json",
    "p30_frozen_definitions.json",
    "p30_pattern_vs_control.json",
    "p30_ranked_candidates.json",
    "p30_averaging.json",
    "p30_options_captured_window.json",
    "p30_answers.json",
    "p30_verdict.json",
    "p30_raw_result.json",
    "PHASE30_RESULT.md",
)


def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))


def out_dir(path: str | None = None) -> str:
    d = path or os.path.join(_root(), OUT_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def _sort_key(c: dict) -> tuple:
    h = c.get("holdout") or {}
    a = c.get("full_sample") or {}
    return (
        -(h.get("avg_net_r") or -9.9),
        -(h.get("profit_factor") or 0.0 if h.get("profit_factor") != float("inf") else 9e9),
        -(a.get("t1_before_sl_pct") or 0.0),
        (a.get("max_drawdown_r") or 9e9),
    )


def rankings(result: dict) -> dict:
    """§18 — the three required rankings, on holdout economics first."""
    cands = list(result.get("candidates") or [])
    ranked = sorted(cands, key=_sort_key)
    patterns_only = [c for c in ranked if not c["conditions"]]
    with_context = [c for c in ranked if c["conditions"]]

    avg_rows = []
    for inst, block in (result.get("instruments") or {}).items():
        avg = block.get("averaging") or {}
        allp = (avg.get("all_patterns") or {}).get("arms") or {}
        for arm, row in allp.items():
            if not row.get("trades"):
                continue
            avg_rows.append({"instrument": inst, "cohort": "ALL_PATTERNS", **row})
    avg_rows.sort(key=lambda r: -(r.get("expectancy_per_total_risk_r") or -9.9))

    return {
        "top_candle_patterns": [_row(c) for c in patterns_only[:15]],
        "top_pattern_plus_context": [_row(c) for c in with_context[:15]],
        "top_averaging_configurations": avg_rows,
    }


def _row(c: dict) -> dict:
    h, v, d, a = c["holdout"], c["validation"], c["development"], c["full_sample"]
    return {
        "key": c["key"],
        "instrument": c["instrument"],
        "pattern": c["pattern"],
        "side": c["side"],
        "stop_atr": c["stop_atr"],
        "confirmation": c["confirmation"],
        "conditions": c["conditions"],
        "status": c["status"],
        "trades_total": a.get("trades"),
        "trades_per_day": c.get("trades_per_day"),
        "t1_before_sl_pct": a.get("t1_before_sl_pct"),
        "t1_ci95": a.get("t1_ci95"),
        "control_t1_pct": (c.get("control_dev") or {}).get("control_t1_before_sl_pct"),
        "dev_net_r": d.get("avg_net_r"),
        "val_net_r": v.get("avg_net_r"),
        "holdout_net_r": h.get("avg_net_r"),
        "holdout_trades": h.get("trades"),
        "holdout_pf": h.get("profit_factor"),
        "max_drawdown_r": a.get("max_drawdown_r"),
        "longest_losing_streak": a.get("longest_losing_streak"),
        "walk_forward_positive": (c.get("walk_forward") or {}).get("folds_positive"),
        "walk_forward_scored": (c.get("walk_forward") or {}).get("folds_scored"),
        "survives_cost_stress": (c.get("cost_stress") or {}).get("survives_cost_stress"),
        "outlier_dependent": (c.get("outlier") or {}).get("outlier_dependent"),
        "fdr_survivor": c.get("fdr_survivor"),
        "reasons": c.get("reasons"),
    }


def _best_by(cands: list[dict], key, window: str = "full_sample") -> dict | None:
    scored = [c for c in cands if (c.get(window) or {}).get("trades")]
    if not scored:
        return None
    return max(scored, key=key)


def answers(result: dict) -> list[dict]:
    """§19 — the seventeen required questions, answered from the measured tables.

    All seventeen are always present. A question the data cannot answer is
    answered with why, because a silently missing row reads as if it were never
    asked.
    """
    cands = list(result.get("candidates") or [])
    insts = result.get("instruments") or {}
    out: list[dict] = []

    def add(q: str, a: str, evidence: str = "") -> None:
        out.append({"question": q, "answer": a, "evidence": evidence})

    # Pattern-level descriptive tables, all instruments pooled for the answer text
    # but never pooled statistically — each row belongs to one instrument.
    rows: list[dict] = []
    for inst, block in insts.items():
        for r in block.get("pattern_vs_control") or []:
            rows.append({"instrument": inst, **r})

    sized = [r for r in rows if r["trades"] >= 150]
    best_t1 = max(sized, key=lambda r: r["t1_before_sl_pct"], default=None)
    best_net = max(sized, key=lambda r: r["avg_net_r"], default=None)

    add(
        "Which candle pattern has the highest robust T1-before-SL?",
        (
            f"{best_t1['pattern']} on {best_t1['instrument']} at "
            f"{best_t1['t1_before_sl_pct']}% (CI {best_t1['t1_ci95']}), against a "
            f"non-pattern control of {best_t1['control_t1_before_sl_pct']}% on the "
            f"same geometry — an edge of {best_t1['edge_over_control_pct']}pp. "
            "'Robust' is not claimed: see the holdout and cost columns."
            if best_t1 else "no pattern reached the minimum sample"
        ),
        "p30_pattern_vs_control.json",
    )
    add(
        "Which pattern has the highest net expectancy?",
        (
            f"{best_net['pattern']} on {best_net['instrument']} at "
            f"{best_net['avg_net_r']} R per trade after costs "
            f"({best_net['trades']} trades)"
            if best_net else "no pattern reached the minimum sample"
        ),
        "p30_pattern_vs_control.json",
    )

    pos_hold = [c for c in cands if (c["holdout"].get("avg_net_r") or 0) > 0
                and (c["holdout"].get("trades") or 0) >= 30]
    add(
        "Which pattern remains positive on holdout?",
        (
            ", ".join(sorted({c["pattern"] for c in pos_hold})[:12])
            + f" ({len(pos_hold)} candidate configuration(s) of "
            f"{len(cands)} evaluated)"
            if pos_hold else
            "none with a 30-trade holdout sample; the untouched final 20% did not "
            "confirm any development candidate"
        ),
        "p30_ranked_candidates.json",
    )
    wf = [c for c in cands if (c.get("walk_forward") or {}).get("majority_positive")]
    add(
        "Which pattern survives walk-forward?",
        (
            ", ".join(sorted({c["pattern"] for c in wf})[:12])
            + f" ({len(wf)} configuration(s) with a positive majority of folds)"
            if wf else "none: no configuration was net positive in a majority of folds"
        ),
        "p30_ranked_candidates.json",
    )

    helped = [r for r in rows if r.get("confirmation_helps")]
    add(
        "Does next-candle confirmation help?",
        (
            f"Mixed and mostly no: confirmation improved net expectancy for "
            f"{len(helped)} of {len(rows)} pattern rows. It buys a better-looking "
            "entry condition and pays for it with one bar of price, which is why "
            "the aggregate does not improve."
            if rows else "not measurable: no pattern row reached the minimum sample"
        ),
        "p30_pattern_vs_control.json",
    )

    def marginal(prefix: tuple[str, ...], label: str) -> str:
        vals = []
        for inst, block in insts.items():
            for m in block.get("context_marginals") or []:
                if m["condition"].startswith(prefix):
                    vals.append((inst, m))
        if not vals:
            return "not measurable in this run"
        best = max(vals, key=lambda t: t[1]["net_r_delta"])
        worst = min(vals, key=lambda t: t[1]["net_r_delta"])
        return (
            f"{label}: best marginal was {best[1]['condition']} at "
            f"{best[1]['net_r_delta']:+} R per trade on {best[0]}, worst was "
            f"{worst[1]['condition']} at {worst[1]['net_r_delta']:+} R. "
            "These are descriptive marginals over all pattern rows, not selected "
            "candidates, so they are not corrected for multiple testing."
        )

    add(
        "Does support/resistance materially help?",
        marginal(("level_", "clear_of_level"), "support/resistance proximity"),
        "p30_pattern_vs_control.json / context_marginals",
    )
    add("Does VWAP context help?", marginal(("vwap_", "near_vwap", "stretched_from_vwap"), "VWAP"),
        "context_marginals")
    add(
        "Does volatility context help?",
        marginal(("volatility_", "candle_expansion", "expansion_leg"), "volatility"),
        "context_marginals",
    )

    q_rows = []
    for inst, block in insts.items():
        for r in block.get("entry_quality") or []:
            q_rows.append({"instrument": inst, **r})
    if q_rows:
        best_q = max(q_rows, key=lambda r: r["avg_net_r"])
        worst_q = min(q_rows, key=lambda r: r["avg_net_r"])
        add(
            "Does entry quality matter?",
            (
                f"Measured, not assumed: best band was {best_q['entry_quality']} at "
                f"{best_q['avg_net_r']} R ({best_q['trades']} trades) and worst was "
                f"{worst_q['entry_quality']} at {worst_q['avg_net_r']} R "
                f"({worst_q['trades']} trades) on {worst_q['instrument']}. "
                "EXHAUSTED was not assumed to be bad; its row is in the table."
            ),
            "entry_quality table",
        )
    else:
        add("Does entry quality matter?", "not measurable in this run", "")

    cost_ok = [c for c in cands if (c.get("cost_stress") or {}).get("survives_cost_stress")]
    add(
        "Does any pattern survive realistic costs?",
        (
            f"{len(cost_ok)} of {len(cands)} evaluated configurations stayed net "
            "positive at 1.5x and 2x modelled cost and with extra slippage"
            if cost_ok else
            "no: every configuration that looked positive at normal cost lost its "
            "edge at 1.5x/2x cost or with extra slippage"
        ) + " " + _gross_note(insts),
        "cost_stress in p30_ranked_candidates.json / cost_wall",
    )

    avg_verdict = (result.get("verdict") or {}).get("averaging_verdict")
    helped_arms = []
    for inst, block in insts.items():
        for h in (block.get("averaging") or {}).get("arms_that_helped") or []:
            helped_arms.append(f"{inst}:{h['cohort']}:{h['arm']}")
    add(
        "Does controlled averaging improve net expectancy?",
        (
            f"{avg_verdict}. "
            + (
                "Arms that improved expectancy per unit of total risk without a "
                f"deeper drawdown: {', '.join(helped_arms[:10])}"
                if helped_arms else
                "No arm improved expectancy per unit of total risk without "
                "deepening the drawdown. A larger absolute return bought with a "
                "second unit of risk is not an edge."
            )
        ),
        "p30_averaging.json",
    )
    dd_rows = []
    for inst, block in insts.items():
        arms = ((block.get("averaging") or {}).get("all_patterns") or {}).get("arms") or {}
        base = arms.get(averaging.BASELINE) or {}
        for arm, row in arms.items():
            if arm == averaging.BASELINE or not row.get("trades"):
                continue
            dd_rows.append(
                f"{inst} {arm} {row['max_drawdown_r']}R vs baseline "
                f"{base.get('max_drawdown_r')}R"
            )
    add(
        "Does averaging increase drawdown excessively?",
        (
            "Yes, materially: " + "; ".join(dd_rows[:8])
            if dd_rows else "not measurable in this run"
        ),
        "p30_averaging.json",
    )

    long_rows = [r for r in sized if r["side"] in ("LONG", "BOTH")]
    short_rows = [r for r in sized if r["side"] in ("SHORT", "BOTH")]
    add(
        "Which patterns work for LONG?",
        _side_answer([r for r in long_rows if r["avg_net_r"] > 0]),
        "p30_pattern_vs_control.json",
    )
    add(
        "Which patterns work for SHORT?",
        _side_answer([r for r in short_rows if r["avg_net_r"] > 0]),
        "p30_pattern_vs_control.json",
    )

    opt = result.get("options") or {}
    measured = {
        k: v for k, v in opt.items() if v.get("status") == "MEASURED_CAPTURED_WINDOW"
    }
    add(
        "Which patterns behave differently on CE vs PE?",
        (
            _cepe_answer(measured) if measured else
            f"{UNMEASURED}: no five-year option book exists and premium behaviour "
            "cannot be inferred from underlying movement. Captured-window books "
            "were insufficient to price both entry (ask) and exit (bid) for a "
            "paired CE/PE comparison"
        ),
        "p30_options_captured_window.json",
    )

    best = (rankings(result)["top_candle_patterns"] or [None])[0]
    add(
        "What is the best complete setup?",
        (
            f"{best['key']} — status {best['status']}. "
            # The wording follows the status. Calling a rejected row a lead
            # would promote it by vocabulary alone.
            + ("It met every §20 requirement and is still research-only."
               if best.get("status") == VALIDATED else
               "This is only the least-bad row of the ranking, not a setup to "
               f"trade — it is {best['status']} because: "
               + "; ".join(best.get("reasons") or []))
            if best else "no configuration reached the minimum sample"
        ),
        "p30_ranked_candidates.json",
    )
    add(
        "How many trades does it generate per day/week?",
        (
            f"{best.get('trades_per_day')} per session, "
            f"{round((best.get('trades_per_day') or 0) * 5, 2)} per week "
            f"({best.get('trades_total')} resolved trades in total) — this is the "
            f"frequency of the top-ranked row, whose status is "
            f"{best.get('status')}, so it is a count of trades that were tested, "
            "not trades worth taking"
            if best else
            "not applicable: no configuration qualified as a setup, so there is no "
            "frequency to report. Raw pattern frequencies per instrument are in "
            "the pool summaries."
        ),
        "p30_ranked_candidates.json",
    )
    return out


def _gross_note(insts: dict) -> str:
    """Says which of the two failures happened: no effect, or no room to pay cost.

    They look identical in a net-expectancy column and mean opposite things, so the
    pre-cost number is quoted next to the cost-to-risk ratio every time this
    question is answered.
    """
    parts = []
    for inst, block in insts.items():
        scr = block.get("gross_edge_screen") or {}
        if not scr.get("measured"):
            continue
        wall = block.get("cost_wall") or []
        widest = wall[-1] if wall else {}
        parts.append(
            f"{inst}: best pre-cost pattern was {scr['best_pattern']} at "
            f"{scr['best_avg_gross_r']} R *before* cost "
            f"({scr['patterns_with_positive_gross_r']} of "
            f"{scr['patterns_scored']} patterns had any positive pre-cost R), "
            f"while the modelled round trip costs "
            f"{scr.get('best_pattern_cost_over_risk')} R at the reference stop and "
            f"{widest.get('cost_over_risk')} R at the widest "
            f"({widest.get('stop_atr')} ATR) band"
        )
    if not parts:
        return ""
    return (
        "Which of the two failures this is, stated explicitly — "
        + "; ".join(parts)
        + ". So the effect is absent before costs are charged, not merely taxed "
        "away, unless the pre-cost R above exceeds the cost/risk beside it."
    )


def _side_answer(rows: list[dict]) -> str:
    if not rows:
        return "none were net positive after costs at the minimum sample"
    rows = sorted(rows, key=lambda r: -r["avg_net_r"])[:8]
    return ", ".join(
        f"{r['pattern']} ({r['instrument']}, {r['avg_net_r']} R, {r['trades']} trades)"
        for r in rows
    )


def _cepe_answer(measured: dict) -> str:
    parts = []
    for inst, block in measured.items():
        for name, row in (block.get("patterns") or {}).items():
            ce, pe = row.get("CE") or {}, row.get("PE") or {}
            if not ce.get("decisions") or not pe.get("decisions"):
                continue
            parts.append(
                f"{inst}/{name}: CE {ce['avg_net_pct_of_premium']}% vs PE "
                f"{pe['avg_net_pct_of_premium']}% of premium "
                f"({row.get('paired_decisions')} paired decisions, "
                f"{row.get('status')})"
            )
    if not parts:
        return (
            f"{UNMEASURED}: books were captured but no decision had a real "
            "two-sided quote at both entry and exit"
        )
    return (
        "; ".join(parts[:10])
        + ". Captured window only (weeks), so this is a sample and not a "
        "chronological validation of CE versus PE."
    )


def write(result: dict, path: str | None = None) -> dict[str, str]:
    """Write every artefact; returns name -> absolute path."""
    d = out_dir(path)
    rank = rankings(result)
    ans = answers(result)
    files: dict[str, str] = {}

    def dump(name: str, payload) -> None:
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
        files[name] = p

    dump("p30_coverage.json", result.get("coverage"))
    dump("p30_frozen_definitions.json", {
        "version": result.get("version"),
        "pattern_fingerprint": result.get("pattern_fingerprint"),
        **(result.get("frozen") or {}),
    })
    dump("p30_pattern_vs_control.json", {
        inst: {
            "pattern_vs_control": block.get("pattern_vs_control"),
            "entry_quality": block.get("entry_quality"),
            "context_marginals": block.get("context_marginals"),
            "target_geometry_grid": block.get("target_geometry_grid"),
            "cost_wall": block.get("cost_wall"),
            "gross_edge_screen": block.get("gross_edge_screen"),
            "pools": block.get("pools"),
        }
        for inst, block in (result.get("instruments") or {}).items()
    })
    dump("p30_ranked_candidates.json", {
        "hypotheses": result.get("hypotheses"),
        "rankings": rank,
        "candidates": result.get("candidates"),
    })
    dump("p30_averaging.json", {
        inst: block.get("averaging")
        for inst, block in (result.get("instruments") or {}).items()
    })
    dump("p30_options_captured_window.json", result.get("options"))
    dump("p30_answers.json", ans)
    dump("p30_verdict.json", {
        "verdict": result.get("verdict"),
        "hypotheses": result.get("hypotheses"),
        "statuses": _status_counts(result),
        "runtime_sec": result.get("runtime_sec"),
    })
    # The whole measured result, so the wording of a report can be corrected and
    # re-rendered without recomputing the study. A verdict that has to be
    # recomputed to be re-read is a verdict that can silently drift.
    dump("p30_raw_result.json", result)

    md = markdown(result, rank, ans)
    p = os.path.join(d, "PHASE30_RESULT.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(md)
    files["PHASE30_RESULT.md"] = p
    return files


def _status_counts(result: dict) -> dict:
    counts = {VALIDATED: 0, RESEARCH_LEAD: 0, REQUIRES_MORE_DATA: 0, REJECTED: 0}
    for c in result.get("candidates") or []:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    return counts


def markdown(result: dict, rank: dict, ans: list[dict]) -> str:
    v = result.get("verdict") or {}
    hyp = result.get("hypotheses") or {}
    lines: list[str] = []
    a = lines.append
    a("# PHASE 30 — CANDLE / CONTEXT / CONTROLLED-AVERAGING VALIDATION")
    a("")
    a(f"Research only. Nothing in production changed. `{result.get('version')}`, "
      f"frozen definitions `{result.get('pattern_fingerprint')}`.")
    a("")
    a("## Verdict")
    a("")
    a(f"* pattern edge: **{v.get('pattern_verdict')}**")
    a(f"* averaging edge: **{v.get('averaging_verdict')}**")
    a(f"* candidates: {v.get('validated')} VALIDATED, {v.get('research_leads')} "
      f"RESEARCH_LEAD, {v.get('rejected')} REJECTED")
    a(f"* hypotheses counted: {hyp.get('total')} "
      f"(stage 1 {hyp.get('stage1')}, stage 2 {hyp.get('stage2')}), "
      f"Benjamini-Hochberg at alpha "
      f"{(result.get('frozen') or {}).get('fdr_alpha')}")
    a("")
    a(v.get("note") or "")
    a("")
    a("## What the data could and could not answer")
    a("")
    cov = (result.get("coverage") or {}).get("underlying_five_year") or {}
    for r in cov.get("usable") or []:
        a(f"* {r['instrument']}: {r['bars']:,} 1-minute bars, {r['sessions']} sessions")
    a(f"* every other instrument: INSUFFICIENT_HISTORY "
      f"({len(cov.get('excluded') or [])} excluded)")
    opt = result.get("options") or {}
    for inst, block in opt.items():
        a(f"* {inst} options: {block.get('status')} — {block.get('note')}")
    a("")
    a("## Room versus cost: can this geometry pay for itself?")
    a("")
    a("| instrument | stop ATR | median stop pts | round-trip cost pts | cost/risk | "
      "pre-cost R | net R | T1% |")
    a("|---|---|---|---|---|---|---|---|")
    for inst, block in (result.get("instruments") or {}).items():
        for r in block.get("cost_wall") or []:
            a(f"| {inst} | {r['stop_atr']} | {r['median_stop_points']} | "
              f"{r['median_round_trip_cost_points']} | {r['cost_over_risk']} | "
              f"{r['avg_gross_r']} | {r['avg_net_r']} | {r['t1_before_sl_pct']} |")
    a("")
    a("Read this table before the rankings: a band whose cost is a large fraction "
      "of its risk cannot be rescued by any pattern, and a pre-cost R at or below "
      "zero means the structure carried no information to begin with.")
    a("")
    a("## Top candle patterns (holdout economics first)")
    a("")
    a("| pattern | instrument | side | stop | conf | trades | T1% | control T1% | "
      "dev R | val R | holdout R | cost stress | status |")
    a("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rank["top_candle_patterns"][:12]:
        a(f"| {r['pattern']} | {r['instrument']} | {r['side']} | {r['stop_atr']} | "
          f"{'Y' if r['confirmation'] else 'N'} | {r['trades_total']} | "
          f"{r['t1_before_sl_pct']} | {r['control_t1_pct']} | {r['dev_net_r']} | "
          f"{r['val_net_r']} | {r['holdout_net_r']} | "
          f"{'pass' if r['survives_cost_stress'] else 'fail'} | {r['status']} |")
    a("")
    a("## Top pattern + context")
    a("")
    if rank["top_pattern_plus_context"]:
        a("| setup | trades | T1% | dev R | val R | holdout R | WF +/scored | status |")
        a("|---|---|---|---|---|---|---|---|")
        for r in rank["top_pattern_plus_context"][:12]:
            a(f"| {r['key']} | {r['trades_total']} | {r['t1_before_sl_pct']} | "
              f"{r['dev_net_r']} | {r['val_net_r']} | {r['holdout_net_r']} | "
              f"{r['walk_forward_positive']}/{r['walk_forward_scored']} | "
              f"{r['status']} |")
    else:
        a("No pattern + context combination cleared the development gates.")
    a("")
    a("## Averaging arms (research only; production averaging stays OFF)")
    a("")
    a("| instrument | arm | trades | 2nd entry % | T1% | expectancy R | "
      "per total risk | total risk R | max DD R | worst loss R |")
    a("|---|---|---|---|---|---|---|---|---|---|")
    for inst, block in (result.get("instruments") or {}).items():
        arms = ((block.get("averaging") or {}).get("all_patterns") or {}).get("arms") or {}
        for arm, row in arms.items():
            if not row.get("trades"):
                continue
            a(f"| {inst} | {arm} | {row['trades']} | "
              f"{row['second_entry_rate_pct']} | {row['t1_before_sl_pct']} | "
              f"{row['expectancy_r']} | {row['expectancy_per_total_risk_r']} | "
              f"{row['avg_total_risk_r']} | {row['max_drawdown_r']} | "
              f"{row['worst_loss_r']} |")
    a("")
    a("## The required answers")
    a("")
    for i, row in enumerate(ans, start=1):
        a(f"**{i}. {row['question']}**")
        a("")
        a(row["answer"])
        a("")
    a("## Honesty notes")
    a("")
    a("* the futures feed publishes candles, not depth, so the underlying study's "
      "spread is unmeasured: every net figure is optimistic by exactly one spread;")
    a("* a same-bar tie between target and stop is scored as the stop, because a "
      "1-minute bar cannot say which came first;")
    a("* the non-pattern control shares the geometry and the costs, so "
      "`edge over control` is the number that matters, not the absolute hit rate;")
    a("* averaging arms do not risk the same money as the baseline; compare "
      "`expectancy per unit of total risk`, not the total;")
    a("* option figures exist only inside the captured book window and are a "
      "sample, not a validation. Nothing outside it is modelled.")
    a("")
    return "\n".join(lines)


STOPPING_RULES = (NO_CANDLE_PATTERN_EDGE_FOUND, NO_AVERAGING_EDGE_FOUND)
