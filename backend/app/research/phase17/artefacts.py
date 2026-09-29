"""The ten report pairs and the daily questions — §34, §35, §24.

Ten JSON files for machines and ten Markdown files for people, written from the
same computed dicts so the prose can never drift from the numbers. Every Markdown
file opens with the same three lines — basis, coverage and match rate — because a
reader who skips them will otherwise read a table of 12 rows as though it were a
result.

The daily questions in §35 are answered from the data or answered
``REQUIRES_MORE_DATA``. There is no third form of answer, and in the first weeks
most of them will take the second, which is the correct output of a phase whose
first job is to find out whether its own capture works.
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable

from app.research.phase17 import (
    aplus,
    htf_probe,
    journal,
    oos,
    quality,
    reports,
    schema,
    store,
)

PAIRS: tuple[str, ...] = (
    "capture", "vehicle", "ce_pe", "futures", "strike",
    "expiry", "entry", "aplus", "paper", "oos",
)

THIN = "REQUIRES_MORE_DATA"


def _write_json(outdir: str, name: str, payload: dict) -> str:
    p = os.path.join(outdir, f"phase17_{name}.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str, sort_keys=True)
    return p


def _write_md(outdir: str, name: str, text: str) -> str:
    p = os.path.join(outdir, f"phase17_{name}.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


def _header(title: str, cap: dict, coverage_rows: list[dict]) -> str:
    rate = cap.get("exact_match_rate_pct")
    sessions = sorted({
        str(r.get("session") or "")
        for r in coverage_rows if r.get("session")
    })
    return (
        f"# Phase 17 — {title}\n\n"
        f"- **Basis**: live option/futures capture. Historical context is "
        f"`UNDERLYING_ONLY` and is never option P&L.\n"
        f"- **Coverage**: {cap.get('total_candidates', 0)} candidates recorded, "
        f"{len(coverage_rows)} heartbeat(s)"
        + (f", sessions {sessions[0]}..{sessions[-1]}" if sessions else "")
        + ".\n"
        f"- **EXACT match rate**: {rate if rate is not None else 'n/a'}% "
        f"(target {quality.EXACT_RATE_TARGET_PCT}%, "
        f"{'MET' if cap.get('meets_target') else 'NOT MET'}). Below target, "
        f"every economic table below is descriptive only.\n"
        f"- **Production**: unchanged. No order was placed by this phase.\n\n"
    )


def _table(rows: list[tuple[str, ...]], head: tuple[str, ...]) -> str:
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        out.append("| " + " | ".join("n/a" if v is None else str(v) for v in r) + " |")
    return "\n".join(out) + "\n\n"


def _cohort_table(cohorts: dict, label: str) -> str:
    if not cohorts:
        return f"_No {label} cohort has a resolved, costed outcome yet._\n\n"
    rows = []
    for name, st in cohorts.items():
        rows.append((
            name, st.get("n"), st.get("t1_pct"), st.get("net_expectancy_r"),
            st.get("profit_factor"), st.get("median_reward_risk"),
            st.get("median_net_points"), st.get("status"),
        ))
    return _table(rows, (
        label, "n", "T1 %", "net exp R", "PF", "median R:R", "median net pts",
        "status",
    ))


def build_payloads(*, observations: list[dict] | None = None,
                   legs: list[dict] | None = None,
                   paper_rows: list[dict] | None = None,
                   coverage_rows: list[dict] | None = None,
                   pool: list[dict] | None = None) -> dict:
    """Compute every report payload without writing anything.

    Split out from :func:`build_all` so the dashboard can read the same numbers
    the artefacts contain — one implementation, so a screen and a report cannot
    disagree about what was measured.
    """
    obs = observations if observations is not None else store.observations()
    lg = legs if legs is not None else store.legs()
    pp = paper_rows if paper_rows is not None else store.paper()
    cov = coverage_rows if coverage_rows is not None else store.coverage()

    cap = reports.capture_report(obs)
    payloads: dict[str, dict] = {
        "capture": cap,
        "vehicle": reports.vehicle_report(obs, lg),
        "ce_pe": reports.ce_pe_report(lg),
        "futures": reports.futures_report(obs, lg),
        "strike": reports.strike_report(obs, lg),
        "expiry": reports.expiry_report(obs, lg),
        "entry": reports.entry_report(obs, lg),
        "aplus": reports.aplus_report(obs, lg),
        "paper": reports.paper_report(pp),
        "oos": oos.evaluate(pp, baseline_rows=lg),
    }
    payloads["aplus"]["htf"] = reports.htf_report(obs, lg)
    if pool:
        payloads["aplus"]["against_htf_probe"] = htf_probe.generate(pool)
    payloads["capture"]["questions"] = answer_questions(payloads, obs, lg, pp, cov)
    return payloads


def build_all(*, outdir: str, observations: list[dict] | None = None,
              legs: list[dict] | None = None, paper_rows: list[dict] | None = None,
              coverage_rows: list[dict] | None = None,
              pool: list[dict] | None = None) -> dict:
    """Compute and write all ten pairs. Returns the paths and the payloads."""
    os.makedirs(outdir, exist_ok=True)
    obs = observations if observations is not None else store.observations()
    lg = legs if legs is not None else store.legs()
    pp = paper_rows if paper_rows is not None else store.paper()
    cov = coverage_rows if coverage_rows is not None else store.coverage()
    payloads = build_payloads(
        observations=obs, legs=lg, paper_rows=pp, coverage_rows=cov, pool=pool,
    )

    written: dict[str, dict[str, str]] = {}
    for name in PAIRS:
        payload = dict(payloads[name])
        payload.update({
            "phase": 17, "artefact": name, "generated_ts": time.time(),
            "research_only": True, "production_unchanged": True,
        })
        written[name] = {
            "json": _write_json(outdir, name, payload),
            "md": _write_md(outdir, name, _render(name, payloads, cov)),
        }

    # §33 journal, alongside the ten pairs rather than as one of them: it is the
    # raw joined evidence a human reads in a spreadsheet, not a report.
    jrows = journal.rows(obs, lg, pp)
    jpath = os.path.join(outdir, "phase17_journal.csv")
    with open(jpath, "w", encoding="utf-8", newline="") as fh:
        fh.write(journal.to_csv(jrows))
    written["journal"] = {"csv": jpath}

    return {"outdir": outdir, "artefacts": written, "payloads": payloads,
            "journal_rows": len(jrows),
            "counts": {"observations": len(obs), "legs": len(lg),
                       "paper": len(pp), "coverage": len(cov)}}


def _render(name: str, payloads: dict, cov: list[dict]) -> str:
    fn: Callable[[dict, dict, list[dict]], str] = _RENDERERS[name]
    return fn(payloads[name], payloads, cov)


def _md_capture(p: dict, all_: dict, cov: list[dict]) -> str:
    out = _header("§3/§27 Capture and data quality", p, cov)
    out += (
        "The primary KPI of this phase. The previous attempt matched **48 of "
        "3,448 legs** to a real book at a median gap of 60s; every economic "
        "conclusion drawn from the other 3,400 would have been fiction.\n\n"
    )
    sel = p.get("selected_side") or {}
    out += _table(
        [(k, v) for k, v in (sel.get("counts") or {}).items()],
        ("quality state", "candidates"),
    )
    out += _table([(
        p.get("median_signal_to_snapshot_ms"), p.get("max_signal_to_snapshot_ms"),
        p.get("both_side_pct"), p.get("exact_match_rate_pct"),
        p.get("meets_target"),
    )], (
        "median signal→snapshot ms", "max ms", "both sides %", "EXACT %",
        "target met",
    ))
    out += f"**Gate.** {p.get('gate')}\n\n"
    out += "## §35 Daily questions\n\n"
    for q in p.get("questions") or []:
        out += f"- **{q['question']}** — {q['answer']}\n"
    return out + "\n"


def _md_vehicle(p: dict, all_: dict, cov: list[dict]) -> str:
    out = _header("§9/§10 Vehicle economics", all_["capture"], cov)
    out += (
        "Can the option pay for its own round trip? The measured sample that "
        "motivated this phase said no: **₹56 of round-trip cost against ₹8 of "
        "gross travel**.\n\n"
    )
    out += _table([(
        p.get("quotes_with_book"), p.get("quotes_total"),
        p.get("median_spread_points"), p.get("median_spread_pct"),
        p.get("median_cost_points"), p.get("median_cost_over_risk"),
        p.get("median_cost_over_expected_move"),
    )], (
        "quotes with book", "quotes total", "median spread", "median spread %",
        "median cost pts", "cost/risk", "cost/expected move",
    ))
    out += _table(
        [(k, v) for k, v in (p.get("vehicle_classes") or {}).items()],
        ("class", "candidates"),
    )
    out += _cohort_table(p.get("by_class") or {}, "vehicle class")
    thr = p.get("class_thresholds") or {}
    out += f"**Thresholds are seeds, not fits.** {thr.get('note')}\n\n"
    out += (
        f"{p.get('outcomes_costed')} costed outcome(s); "
        f"{p.get('outcomes_excluded_uncosted')} excluded for having no measured "
        "book — excluded rather than estimated.\n"
    )
    return out


def _md_ce_pe(p: dict, all_: dict, cov: list[dict]) -> str:
    out = _header("§6/§8 CE vs PE — wrong market or wrong vehicle", all_["capture"], cov)
    out += (
        "Both sides were quoted at the same instant off the same chain, so this "
        "is a comparison rather than an assertion.\n\n"
    )
    out += _table(
        [(k, v) for k, v in (p.get("side_verdicts") or {}).items()],
        ("side verdict", "observations"),
    )
    out += _table(
        [(k, v) for k, v in (p.get("faults") or {}).items()],
        ("fault", "observations"),
    )
    out += _table([(
        p.get("observations"), p.get("classified"), p.get("unclassified"),
        p.get("wrong_market_pct"), p.get("wrong_vehicle_pct"), p.get("status"),
    )], (
        "observations", "classified", "unclassified", "wrong market %",
        "wrong vehicle %", "status",
    ))
    for k, v in (p.get("interpretation") or {}).items():
        out += f"- **{k}** — {v}\n"
    return out + "\n"


def _md_futures(p: dict, all_: dict, cov: list[dict]) -> str:
    out = _header("§15 Futures beside options", all_["capture"], cov)
    st = p.get("outcomes") or {}
    out += _table([(
        p.get("quotes"), p.get("fresh_quotes"), p.get("median_spread_points"),
        st.get("n"), st.get("t1_pct"), st.get("net_expectancy_r"), p.get("status"),
    )], (
        "quotes", "fresh quotes", "median spread", "outcomes", "T1 %",
        "net exp R", "status",
    ))
    return out + f"{p.get('note')}\n"


def _md_strike(p: dict, all_: dict, cov: list[dict]) -> str:
    out = _header("§14 Strike distance and delta band", all_["capture"], cov)
    out += _table(
        [(k, v) for k, v in (p.get("median_spread_pct_by_moneyness") or {}).items()],
        ("moneyness", "median spread %"),
    )
    out += _cohort_table(p.get("outcomes_by_moneyness") or {}, "moneyness")
    out += _cohort_table(p.get("outcomes_by_delta_band") or {}, "delta band")
    return out + f"{p.get('note')}\n"


def _md_expiry(p: dict, all_: dict, cov: list[dict]) -> str:
    out = _header("§13 Expiry, DTE and weekday", all_["capture"], cov)
    out += _cohort_table(p.get("by_expiry_class") or {}, "expiry class")
    out += _cohort_table(p.get("by_days_to_expiry") or {}, "days to expiry")
    out += _cohort_table(p.get("by_weekday") or {}, "weekday")
    return out + f"{p.get('note')}\n"


def _md_entry(p: dict, all_: dict, cov: list[dict]) -> str:
    out = _header("§11 Entry quality", all_["capture"], cov)
    out += _table(
        [(k, v) for k, v in (p.get("counts") or {}).items()],
        ("entry quality", "candidates"),
    )
    out += f"UNKNOWN share: **{p.get('unknown_pct')}%**. {p.get('note')}\n\n"
    out += _cohort_table(p.get("outcomes_by_entry_quality") or {}, "entry quality")
    return out


def _md_aplus(p: dict, all_: dict, cov: list[dict]) -> str:
    out = _header("§20/§21 A+ research score", all_["capture"], cov)
    out += _table(
        [(k, v) for k, v in (p.get("labels") or {}).items()],
        ("label", "candidates"),
    )
    out += _table(
        [(k, v) for k, v in (p.get("weights") or {}).items()],
        ("component", "weight"),
    )
    ranks = (p.get("t1_ranks") or {}).get("counts") or {}
    out += _table([(k, v) for k, v in ranks.items()], ("T1 rank", "candidates"))
    out += _cohort_table(p.get("outcomes_by_label") or {}, "A+ label")
    out += _cohort_table(p.get("outcomes_by_t1_rank") or {}, "T1 rank")
    out += f"{p.get('note')}\n\n"
    htf = p.get("htf") or {}
    out += "## §19 HTF completeness\n\n"
    out += _table(
        [(k, v) for k, v in (htf.get("counts") or {}).items()],
        ("alignment", "candidates"),
    )
    out += f"Answerable from this feed: **{htf.get('answerable')}**. {htf.get('note')}\n\n"
    probe = p.get("against_htf_probe")
    if probe:
        out += "## §19 Against-HTF probe over the 5-year pool\n\n"
        rows = [
            (b, st.get("n"), st.get("t1_pct"), st.get("expectancy_r"),
             st.get("profit_factor"))
            for b, st in (probe.get("against_htf") or {}).items()
        ]
        out += _table(rows, ("bound", "n", "T1 %", "exp R", "PF"))
        out += (
            f"Ambiguous rows: **{probe.get('ambiguous_pct')}%**. Verdict: "
            f"**{probe.get('verdict')}**. {probe.get('note')}\n\n"
        )
    return out


def _md_paper(p: dict, all_: dict, cov: list[dict]) -> str:
    out = _header("§22/§24 Paper book", all_["capture"], cov)
    out += "**PAPER ONLY — NO REAL ORDER.**\n\n"
    for label in ("options", "futures"):
        b = p.get(label) or {}
        out += f"## {label}\n\n"
        out += _table([(
            b.get("entries"), b.get("costed"), b.get("uncosted_excluded"),
            b.get("win_rate_pct"), b.get("gross_points"), b.get("cost_points"),
            b.get("net_points"), b.get("expectancy_r"), b.get("profit_factor"),
        )], (
            "entries", "costed", "excluded", "win %", "gross pts", "cost pts",
            "net pts", "exp R", "PF",
        ))
    out += _cohort_table(p.get("hold_bucket_stats") or {}, "hold bucket")
    g = p.get("giveback") or {}
    out += _table([(
        g.get("reached_half_r_then_reversed"), g.get("reached_t1_then_reversed"),
        g.get("reached_t2_then_reversed"), g.get("median_giveback_points"),
    )], ("+0.5R reversed", "T1 reversed", "T2 reversed", "median giveback"))
    return out + f"{g.get('note')}\n"


def _md_oos(p: dict, all_: dict, cov: list[dict]) -> str:
    out = _header("§29-§32 Out-of-sample and promotion", all_["capture"], cov)
    out += f"**A+ status: {p.get('status')}**\n\n"
    for period in ("development", "validation", "holdout", "baseline_holdout"):
        b = p.get(period) or {}
        out += _table([(
            period, b.get("trades"), b.get("expectancy_r"),
            b.get("profit_factor"), b.get("t1_pct"), b.get("max_drawdown_r"),
        )], ("period", "n", "exp R", "PF", "T1 %", "max DD R"))
    v = p.get("verdict") or {}
    out += f"Promotion verdict: **{v.get('verdict')}**\n\n"
    out += _table(
        [(c.get("requirement"), c.get("passed"), c.get("detail"))
         for c in (v.get("checks") or [])],
        ("requirement", "passed", "detail"),
    )
    out += f"{v.get('note')}\n\n{p.get('note')}\n"
    return out


_RENDERERS: dict[str, Callable[[dict, dict, list[dict]], str]] = {
    "capture": _md_capture,
    "vehicle": _md_vehicle,
    "ce_pe": _md_ce_pe,
    "futures": _md_futures,
    "strike": _md_strike,
    "expiry": _md_expiry,
    "entry": _md_entry,
    "aplus": _md_aplus,
    "paper": _md_paper,
    "oos": _md_oos,
}


def answer_questions(payloads: dict, obs: list[dict], legs: list[dict],
                     paper_rows: list[dict], cov: list[dict]) -> list[dict]:
    """§35. Each question answered from the data, or REQUIRES_MORE_DATA."""
    cap = payloads["capture"]
    veh = payloads["vehicle"]
    ce = payloads["ce_pe"]
    ent = payloads["entry"]
    ap = payloads["aplus"]
    pp = payloads["paper"]
    o = payloads["oos"]
    opts = pp.get("options") or {}

    def thin(n: int, need: int = reports.MIN_COHORT) -> str | None:
        return f"{THIN} — {n} row(s), {need} needed" if n < need else None

    def q(question: str, answer: object) -> dict:
        return {"question": question, "answer": str(answer)}

    n_costed = int(veh.get("outcomes_costed") or 0)
    qs = [
        q("How many candidates were captured?", cap.get("total_candidates")),
        q("What share matched a book at the decision instant (EXACT)?",
          f"{cap.get('exact_match_rate_pct')}% against a "
          f"{quality.EXACT_RATE_TARGET_PCT}% target — "
          f"{'MET' if cap.get('meets_target') else 'NOT MET'}"),
        q("What was the median signal-to-snapshot gap?",
          f"{cap.get('median_signal_to_snapshot_ms')} ms "
          f"(previous attempt: 60,000 ms)"),
        q("How often were BOTH sides quoted at the same instant?",
          f"{cap.get('both_side_pct')}%"),
        q("Which sessions were covered, and what is missing?",
          f"{len(cov)} heartbeat(s) recorded; gaps between heartbeats are "
          "minutes with no capture"),
        q("What did a round trip cost in premium points?",
          veh.get("median_cost_points") or THIN),
        q("What fraction of risk did cost consume?",
          veh.get("median_cost_over_risk") or THIN),
        q("Could the vehicle pay for itself (cost/expected move)?",
          veh.get("median_cost_over_expected_move") or THIN),
        q("How many vehicles were GREEN / YELLOW / RED?",
          veh.get("vehicle_classes")),
        q("Was the money lost to the market or to the vehicle?",
          thin(int(ce.get("classified") or 0))
          or f"WRONG_MARKET {ce.get('wrong_market_pct')}%, "
             f"WRONG_VEHICLE {ce.get('wrong_vehicle_pct')}%"),
        q("Would the opposite side have won?",
          thin(int(ce.get("classified") or 0)) or ce.get("side_verdicts")),
        q("How often was the entry a chase?", ent.get("counts")),
        q("What share of entries cannot be graded at all?",
          f"{ent.get('unknown_pct')}% UNKNOWN"),
        q("Does entry quality change the outcome?",
          thin(n_costed) or ent.get("outcomes_by_entry_quality")),
        q("Does strike distance change spread or outcome?",
          payloads["strike"].get("median_spread_pct_by_moneyness") or THIN),
        q("Does days-to-expiry change the outcome?",
          thin(n_costed) or payloads["expiry"].get("by_days_to_expiry")),
        q("Does the weekday change the outcome?",
          thin(n_costed) or payloads["expiry"].get("by_weekday")),
        q("How many A+ candidates were found?",
          f"{(ap.get('labels') or {}).get(aplus.A_PLUS, 0)} "
          "(zero is a valid day)"),
        q("Did the A+ score order outcomes correctly?",
          thin(n_costed) or ap.get("outcomes_by_label")),
        q("Did the T1 rank order outcomes correctly?",
          thin(n_costed) or ap.get("outcomes_by_t1_rank")),
        q("What did the paper book make, gross and net?",
          f"gross {opts.get('gross_points')} pts, cost "
          f"{opts.get('cost_points')} pts, net {opts.get('net_points')} pts "
          f"over {opts.get('entries')} entr(ies)"),
        q("How long were winners held, and how much was given back?",
          f"median hold {opts.get('median_hold_minutes')} min, median MFE "
          f"capture {opts.get('median_mfe_capture_pct')}%"),
        q("Were refusals correct?",
          reports.missed_report(obs, legs).get("missed_winner_pct") or THIN),
        q("Is any A+ condition out-of-sample validated?",
          f"{o.get('status')} — promotion verdict "
          f"{(o.get('verdict') or {}).get('verdict')}"),
        q("May anything be promoted to production?",
          "No. Promotion requires the §31 gate to return "
          "PRODUCTION_CANDIDATE, and this phase changes no production logic "
          "regardless of its verdict"),
    ]
    return qs


def summary(payloads: dict) -> dict:
    """§24 one-screen daily summary for the dashboard."""
    cap = payloads["capture"]
    pp = payloads["paper"].get("options") or {}
    qs = cap.get("questions") or []
    unanswered = sum(1 for q in qs if THIN in str(q.get("answer")))
    return {
        "questions": {"total": len(qs), "requires_more_data": unanswered},
        "promotion": {
            "status": payloads["oos"].get("status"),
            "verdict": (payloads["oos"].get("verdict") or {}).get("verdict"),
        },
        "candidates": cap.get("total_candidates"),
        "exact_match_rate_pct": cap.get("exact_match_rate_pct"),
        "meets_target": cap.get("meets_target"),
        "both_side_pct": cap.get("both_side_pct"),
        "a_plus": (payloads["aplus"].get("labels") or {}).get(aplus.A_PLUS, 0),
        "paper_entries": pp.get("entries"),
        "paper_net_points": pp.get("net_points"),
        "paper_cost_points": pp.get("cost_points"),
        "paper_expectancy_r": pp.get("expectancy_r"),
        "a_plus_status": payloads["oos"].get("status"),
        "vehicle_classes": payloads["vehicle"].get("vehicle_classes"),
        "wrong_market_pct": payloads["ce_pe"].get("wrong_market_pct"),
        "wrong_vehicle_pct": payloads["ce_pe"].get("wrong_vehicle_pct"),
        "basis": schema.COST_MEASURED,
        "paper_only": True,
        "no_real_order": True,
    }
