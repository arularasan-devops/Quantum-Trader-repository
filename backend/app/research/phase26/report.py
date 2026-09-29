"""Phase 26 §6 — run the study and write the artefacts.

One run answers three questions and refuses to blur them:

1. does any exit rule make the captured books less negative than Phase 25's
   fixed 1.5R target, and does the best one survive its own holdout;
2. does any *event* cohort survive under that exit rule, with an honest
   multiple-testing denominator;
3. which vehicles are worth trading at all on measured economics, and what a
   refusal would have cost as well as saved.

Every artefact carries the window claim, and the verdict is allowed to be "the
captured window cannot answer this". Nothing here promotes anything.
"""
from __future__ import annotations

import gzip
import json
import os
import time

import numpy as np

from app.research.phase25 import books, conditions, discover, underlying
from app.research.phase25.report import jsonable, jsonable_dict
from app.research.phase26 import (
    ADVISORY_CLAIM,
    REJECTED,
    REQUIRES_MORE_DATA,
    RESEARCH_LEAD,
    VERSION,
    WINDOW_CLAIM,
    advisory,
    exits,
    paths,
    rank,
    study,
)
from app.research.phase26 import coverage as coverage_mod

ARTEFACT_DIR = "data/phase26"
CANDIDATE_ROWS_TOP_N = 10


def _abs(rel: str) -> str:
    backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))
    return os.path.join(backend, rel)


def artefact_dir(out_dir: str | None = None) -> str:
    """Where artefacts are written; the override keeps test runs separate."""
    return out_dir or _abs(ARTEFACT_DIR)


def _write(name: str, payload: object, out_dir: str | None = None) -> str:
    d = artefact_dir(out_dir)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(jsonable(payload), fh, indent=1, default=str, allow_nan=False)
    return path


def coverage(*, db_path: str | None = None) -> dict:
    """Which instruments have enough captured books to be studied at all."""
    return coverage_mod.coverage(db_path=db_path)


def variant_totals(rows: list[dict]) -> list[dict]:
    """Pooled result per exit variant across every studied instrument.

    Pooled on trades, not averaged over instruments: an instrument with 7,000
    candidates and one with 2,400 are not one vote each.
    """
    acc: dict[str, dict] = {}
    for r in rows:
        for v in r.get("variants") or []:
            if not v.get("trades"):
                continue
            a = acc.setdefault(v["variant"], {
                "variant": v["variant"],
                "geometry": v["geometry"],
                "instruments": 0,
                "trades": 0,
                "total_net_r": 0.0,
                "total_net_rupees": 0.0,
                "profit_exits": 0,
                "stop_outs": 0,
                "timeouts": 0,
                "timeouts_in_profit": 0,
                "positive_trades": 0.0,
                "hold_sec_weighted": 0.0,
                "instruments_positive": [],
                "instruments_better_than_baseline": [],
            })
            o = v["overall"]
            a["instruments"] += 1
            a["trades"] += o["trades"]
            a["total_net_r"] += float(o["total_net_r"])
            a["total_net_rupees"] += float(v["total_net_rupees"])
            a["profit_exits"] += int(o["t1_hits"])
            a["stop_outs"] += int(o["sl_hits"])
            a["timeouts"] += int(o["timeouts"])
            a["timeouts_in_profit"] += int(v.get("horizon_exits_in_profit") or 0)
            a["positive_trades"] += o["positive_net_pct"] * o["trades"] / 100.0
            a["hold_sec_weighted"] += float(v["avg_hold_sec"]) * o["trades"]
            if o["avg_net_r"] > 0:
                a["instruments_positive"].append(r["instrument"])
        base = next(
            (v for v in (r.get("variants") or [])
             if v["variant"] == exits.BASELINE_KEY and v.get("trades")),
            None,
        )
        if base:
            for v in r.get("variants") or []:
                if not v.get("trades") or v["variant"] == exits.BASELINE_KEY:
                    continue
                if v["avg_net_r"] > base["avg_net_r"]:
                    acc[v["variant"]]["instruments_better_than_baseline"].append(
                        r["instrument"]
                    )

    out: list[dict] = []
    for a in acc.values():
        n = max(1, a["trades"])
        out.append({
            "variant": a["variant"],
            "geometry": a["geometry"],
            "instruments_scored": a["instruments"],
            "trades": a["trades"],
            "avg_net_r": round(a["total_net_r"] / n, 4),
            "total_net_r": round(a["total_net_r"], 1),
            "total_net_rupees": round(a["total_net_rupees"], 2),
            "profit_exit_before_sl_pct": round(100.0 * a["profit_exits"] / n, 2),
            "stop_out_pct": round(100.0 * a["stop_outs"] / n, 2),
            # Phase 25's finding was a *flat* timeout. A target-less variant ends
            # at the horizon by construction, so both numbers are reported: how
            # often the horizon was reached, and how often that was in profit.
            "horizon_exit_pct": round(100.0 * a["timeouts"] / n, 2),
            "horizon_exit_in_profit_pct": round(
                100.0 * a["timeouts_in_profit"] / max(1, a["timeouts"]), 2
            ),
            "flat_timeout_pct": round(
                100.0 * (a["timeouts"] - a["timeouts_in_profit"]) / n, 2
            ),
            "positive_net_pct": round(100.0 * a["positive_trades"] / n, 2),
            "avg_hold_sec": round(a["hold_sec_weighted"] / n, 1),
            "instruments_positive": sorted(a["instruments_positive"]),
            "instruments_better_than_baseline": sorted(
                a["instruments_better_than_baseline"]
            ),
        })
    out.sort(key=lambda r: -r["avg_net_r"])
    return out


def write_candidate_rows(c: paths.Candidates, o, variant_key: str,
                         ranked: list[dict], out_dir: str | None = None) -> str:
    """Every candidate row under the winning variant, refusals included.

    A row with an empty ``selected_by`` is an opportunity no cohort took — the
    NO TRADE side of the study — and is kept so the tables can be audited
    instead of trusted.
    """
    masks = conditions.masks(c.feat, c.side)
    picks: list[tuple[str, np.ndarray]] = []
    for r in ranked[:CANDIDATE_ROWS_TOP_N]:
        if r["instrument"] != c.instrument or r["variant"] != variant_key:
            continue
        m = c.option_type == r["option_type"]
        for cond in r["conditions"]:
            m = m & masks[cond]
        picks.append((r["strategy_id"], m))

    hold = exits.hold_seconds(c, o)
    name = f"p26_candidates_{c.instrument}_{variant_key}.jsonl.gz"
    d = artefact_dir(out_dir)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for k in range(len(c)):
            fh.write(json.dumps({
                "instrument": c.instrument,
                "exit_variant": variant_key,
                "decision_ts": int(c.ts[k]),
                "entry_ts": int(c.entry_ts[k]),
                "session": int(c.session[k]),
                "option_type": str(c.option_type[k]),
                "symbol": str(c.symbol[k]),
                "strike": float(c.strike[k]),
                "underlying_at_decision": round(float(c.spot[k]), 2),
                "entry_bid": round(float(c.entry_bid[k]), 2),
                "entry_ask": round(float(c.entry_ask[k]), 2),
                "entry_paid": round(float(o.entry[k]), 2),
                "stop": round(float(o.stop[k]), 2),
                "target": (
                    round(float(o.t1[k]), 2) if np.isfinite(o.t1[k]) else None
                ),
                "measured_spread_pct": (
                    round(float(o.spread_pct[k]), 3)
                    if np.isfinite(o.spread_pct[k]) else None
                ),
                "break_even_hurdle_pct": (
                    round(float(c.feat["hurdle_pct"][k]), 3)
                    if np.isfinite(c.feat["hurdle_pct"][k]) else None
                ),
                "exit_received": (
                    round(float(o.exit_price[k]), 2)
                    if np.isfinite(o.exit_price[k]) else None
                ),
                "outcome": str(o.outcome[k]),
                "resolved": bool(o.resolved[k]),
                "holding_seconds": int(hold[k]) if hold[k] >= 0 else None,
                "quotes_held": int(o.bars_held[k]),
                "cost_points": round(float(o.cost_points[k]), 3),
                "net_points": round(float(o.net_points[k]), 3),
                "net_r": round(float(o.net_r[k]), 4),
                "net_rupees": round(float(o.net_rupees[k]), 2),
                "conditions_true": sorted(
                    n for n, m in masks.items() if bool(m[k])
                ),
                "selected_by": [sid for sid, m in picks if bool(m[k])],
                "paper_only": True,
            }) + "\n")
    return path


def conclusion(rows: list[dict], totals: list[dict],
               ranked: list[dict]) -> dict:
    """The verdict, including the honest 'this changed nothing' one."""
    studied = [r for r in rows if r.get("status") == "STUDIED"]
    leads = [r for r in ranked if r["status"] == RESEARCH_LEAD]
    if not studied or not totals:
        return {
            "verdict": REQUIRES_MORE_DATA,
            "headline": (
                "no instrument has enough captured real option books to study; "
                "the study did not run, which is not a negative result"
            ),
            "window": WINDOW_CLAIM,
            "leads": 0,
        }
    base = next((t for t in totals if t["variant"] == exits.BASELINE_KEY), None)
    best = totals[0]
    improved = bool(base and best["avg_net_r"] > base["avg_net_r"])
    any_positive = best["avg_net_r"] > 0
    headline = (
        f"the best exit variant ({best['variant']}) pooled "
        f"{best['avg_net_r']}R per trade against the Phase 25 baseline's "
        f"{base['avg_net_r'] if base else 'n/a'}R"
    )
    if not any_positive:
        headline += (
            "; both are still negative, so exit management reduced the loss "
            "without creating an edge"
        )
    if leads:
        return {
            "verdict": RESEARCH_LEAD,
            "headline": headline + f"; {len(leads)} event cohort(s) survived "
                                   "every clause the captured window can test",
            "window": WINDOW_CLAIM,
            "leads": len(leads),
            "best_variant": best["variant"],
            "baseline_variant": exits.BASELINE_KEY,
            "exit_management_improved_the_pool": improved,
            "next_step": (
                "keep the live capture running and re-run on a longer window; "
                "nothing here changes a live decision"
            ),
        }
    closest = ranked[0] if ranked else None
    return {
        "verdict": REJECTED,
        "headline": headline,
        "window": WINDOW_CLAIM,
        "leads": 0,
        "best_variant": best["variant"],
        "baseline_variant": exits.BASELINE_KEY,
        "exit_management_improved_the_pool": improved,
        "pool_positive_under_any_variant": any_positive,
        "closest_cohort": None if closest is None else {
            "strategy_id": closest.get("strategy_id"),
            "instrument": closest.get("instrument"),
            "option_type": closest.get("option_type"),
            "variant": closest.get("variant"),
            "conditions": closest.get("conditions"),
            "status": closest.get("status"),
            "failed_clauses": closest.get("failed_clauses"),
        },
    }


def answers(out: dict) -> list[dict]:
    """Direct answers to the questions this phase was built to settle."""
    rows = [r for r in out["instruments"] if r.get("status") == "STUDIED"]
    totals = out["variant_totals"]
    base = next((t for t in totals if t["variant"] == exits.BASELINE_KEY), None)
    best = totals[0] if totals else None
    adv = out["advisory"]["counts"]
    leads = [r for r in out["ranked"] if r["status"] == RESEARCH_LEAD]

    def q(question: str, answer: str) -> dict:
        return {"question": question, "answer": answer}

    winners = {}
    for r in rows:
        winners[r["best_variant_on_development"]] = winners.get(
            r["best_variant_on_development"], 0
        ) + 1
    return [
        q("Was the flat timeout the real problem, and did a better exit fix it?",
          "no variant was measured" if not best or not base else
          f"the baseline left {base['flat_timeout_pct']}% of trades at the "
          f"horizon in loss; the best variant ({best['variant']}) reaches the "
          f"horizon on {best['horizon_exit_pct']}% of trades — "
          f"{best['horizon_exit_in_profit_pct']}% of those in profit, since a "
          f"variant without a fixed target ends there by construction — and "
          f"pools {best['avg_net_r']}R against {base['avg_net_r']}R. "
          + ("It is still negative, so the exit reduced the loss without making "
             "the pool profitable." if best["avg_net_r"] <= 0 else
             "It is positive on the pooled window, which is a lead and not a "
             "validated edge.")),
        q("Which exit won on development sessions, per instrument?",
          "; ".join(f"{k}: {v} instrument(s)" for k, v in sorted(
              winners.items(), key=lambda kv: -kv[1])) or "none"),
        q("Does a longer hold help?",
          "; ".join(
              f"{t['variant']}: {t['avg_net_r']}R over {t['trades']:,} trades"
              for t in totals if "long_hold" in (t["geometry"].get("tags") or [])
          ) or "not measured"),
        q("Does scaling out at +0.5R help, after paying for the extra exit order?",
          "; ".join(
              f"{t['variant']}: {t['avg_net_r']}R, profit exits "
              f"{t['profit_exit_before_sl_pct']}%"
              for t in totals if "scale_out" in (t["geometry"].get("tags") or [])
          ) or "not measured"),
        q("Did any event cohort survive under the winning exit?",
          f"{len(leads)} of {len(out['ranked'])} ranked cohorts reached "
          f"{RESEARCH_LEAD}"
          + ("" if leads else
             "; the strongest failed clauses are listed with each cohort")),
        q("Which vehicles are worth trading at all on measured economics?",
          f"{adv.get('TRADABLE_ECONOMICS', 0)} instrument(s) have tradable "
          f"economics, {adv.get('MARGINAL_ECONOMICS', 0)} marginal, "
          f"{adv.get('AVOID_ECONOMICS', 0)} should be avoided on the book alone. "
          + ADVISORY_CLAIM),
        q("What would refusing the >5% hurdle band have done on this window?",
          json.dumps(out["advisory"]["refusing_hurdle_gt_5pct_on_this_window"])
          + " — retrospective on research candidates, not a forecast for the "
            "live selected book"),
        q("Was anything promoted?",
          "No. Phase 26 has no tick hook, no order path and no write path into "
          "any book, and the strongest verdict it can emit is a research lead."),
        q("What would change the answer?",
          "A longer capture. Every number here is bounded by a window of weeks, "
          "and the same code on a longer store is the only route to a validated "
          "answer."),
    ]


def run(*, quick: bool = False, instruments: list[str] | None = None,
        db_path: str | None = None, out_dir: str | None = None) -> dict:
    """Run the exit/tradability study and write every artefact."""
    started = time.time()
    cov = coverage(db_path=db_path)
    if cov.get("unmeasured"):
        out = {
            "version": VERSION,
            "generated_at": int(started),
            "runtime_seconds": round(time.time() - started, 1),
            "quick_mode": bool(quick),
            "coverage": cov,
            "instruments": [],
            "variant_totals": [],
            "ranked": [],
            "advisory": {"rows": [], "counts": {}},
            "conclusion": {
                "verdict": REQUIRES_MORE_DATA,
                "headline": cov["note"],
                "window": WINDOW_CLAIM,
                "leads": 0,
            },
            "answers": [],
            "paper_only": True,
        }
        _write("p26_study_report.json", out, out_dir)
        return out

    names = [r["instrument"] for r in cov["eligible"]]
    if instruments:
        wanted = {i.upper() for i in instruments}
        names = [n for n in names if n in wanted]

    rows: list[dict] = []
    events_all: list[dict] = []
    adv_rows: list[dict] = []
    tests = 0
    for inst in names:
        res = study.run_instrument(inst, quick=quick, db_path=db_path)
        rows.append(res)
        tests += int(res.get("event_hypotheses_evaluated") or 0)
        events_all.extend(res.get("events") or [])
        if res.get("advisory"):
            adv_rows.append(res["advisory"])
    for r in cov["reported_only"]:
        rows.append({
            "instrument": r["instrument"],
            "coverage": r,
            "status": REQUIRES_MORE_DATA,
            "variants": [],
            "events": [],
        })

    totals = variant_totals(rows)
    ranked = rank.rank(events_all, hypotheses_evaluated=tests)
    out = {
        "version": VERSION,
        "generated_at": int(started),
        "quick_mode": bool(quick),
        "coverage": cov,
        "window_claim": WINDOW_CLAIM,
        "advisory_claim": ADVISORY_CLAIM,
        "execution": {
            "entry": "ask of a later stored quote, plus configured slippage",
            "exit": "bid of a stored quote, minus configured slippage",
            "slippage_pct_per_side": round(100.0 * exits.slippage_pct(), 4),
            "costs_charged": (
                "brokerage + statutory only, per exit order; the spread is paid "
                "in the prices, so charging the cost model's spread term as well "
                "would pay it twice. A scale-out is charged one entry and two "
                "exits"
            ),
            "tie_break": "a stop and a target inside the same quote resolve as the stop",
            "split": (
                f"chronological by session: {int(100 * discover.DEV_FRAC)}% "
                f"development / {int(100 * discover.VAL_FRAC)}% validation / "
                "remainder holdout; the variant is chosen on development only"
            ),
        },
        "variants_tested": [exits.geometry(v) for v in exits.VARIANTS],
        "instruments": rows,
        "variant_totals": totals,
        "ranked": ranked,
        "advisory": advisory.table(adv_rows),
        "event_hypotheses_evaluated": tests,
        "paper_only": True,
    }
    out["conclusion"] = conclusion(rows, totals, ranked)
    out["answers"] = answers(out)
    out["runtime_seconds"] = round(time.time() - started, 1)

    artefacts = {
        "report": _write("p26_study_report.json", out, out_dir),
        "coverage": _write("p26_coverage.json", cov, out_dir),
        "variant_totals": _write("p26_exit_variants.json", totals, out_dir),
        "per_instrument_variants": _write("p26_variants_by_instrument.json", [
            {
                "instrument": r["instrument"],
                "best_variant_on_development": r.get("best_variant_on_development"),
                "uplift": r.get("variant_uplift_vs_baseline_r"),
                "variants": r.get("variants"),
            }
            for r in rows if r.get("status") == "STUDIED"
        ], out_dir),
        "ranked_events": _write("p26_ranked_events.json", ranked, out_dir),
        "advisory": _write("p26_tradability_advisory.json", out["advisory"], out_dir),
        "rejected_log": _write("p26_rejected.json", [
            {"strategy_id": r["strategy_id"], "instrument": r["instrument"],
             "option_type": r["option_type"], "variant": r["variant"],
             "conditions": r["conditions"], "status": r["status"],
             "failed_clauses": r["failed_clauses"]}
            for r in ranked if r["status"] != RESEARCH_LEAD
        ], out_dir),
        "fingerprints": _write("p26_fingerprints.json", [
            r["fingerprint"] for r in ranked if r["status"] == RESEARCH_LEAD
        ] or {"fingerprints": [], "note": "no cohort reached RESEARCH_LEAD, so "
              "nothing is published even as a paper lead"}, out_dir),
    }

    if not quick:
        for r in rows:
            if r.get("status") != "STUDIED":
                continue
            inst = r["instrument"]
            chain = books.load_chain(inst, db_path=db_path)
            series = underlying.load_series(inst, db_path=db_path)
            c = paths.build(inst, chain=chain, series=series)
            if c is None:
                continue
            key = r.get("best_variant_on_development") or exits.BASELINE_KEY
            o = exits.resolve(c, exits.VARIANTS_BY_KEY[key])
            artefacts[f"candidates_{inst}"] = write_candidate_rows(
                c, o, key, ranked, out_dir
            )
    out["artefacts"] = artefacts
    _write("p26_study_report.json", out, out_dir)
    return out


def latest() -> dict | None:
    path = os.path.join(_abs(ARTEFACT_DIR), "p26_study_report.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _latest_list(name: str) -> list[dict]:
    path = os.path.join(_abs(ARTEFACT_DIR), name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    return rows if isinstance(rows, list) else []


def latest_variants() -> list[dict]:
    return _latest_list("p26_exit_variants.json")


def latest_events() -> list[dict]:
    return _latest_list("p26_ranked_events.json")


def latest_advisory() -> dict:
    path = os.path.join(_abs(ARTEFACT_DIR), "p26_tradability_advisory.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload if isinstance(payload, dict) else {}


__all__ = [
    "run", "coverage", "latest", "latest_variants", "latest_events",
    "latest_advisory", "conclusion", "answers", "variant_totals",
    "write_candidate_rows", "artefact_dir", "jsonable", "jsonable_dict",
]
