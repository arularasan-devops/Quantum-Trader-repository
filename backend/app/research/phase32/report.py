"""Phase 32 — artefacts and the written answer.

Every number is written next to what it is: a measured underlying movement, a
derived premium bound, or a count that stands in for a verdict the data cannot
support.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.research.phase24 import data as p24data
from app.research.phase32 import (
    CAPS,
    NO_REACHABLE_T1,
    SL_MULTIPLES,
    T1_GRID,
    universe,
)

OUT_DIR = "data/phase32"

FILES = (
    "p32_inventory.json",
    "p32_configs.json",
    "p32_selected.json",
    "p32_short_capture.json",
    "p32_premium.json",
    "p32_answers.json",
    "p32_raw_result.json",
    "PHASE32_RESULT.md",
)


def _out(out_dir: str | None = None) -> Path:
    p = Path(out_dir) if out_dir else Path(p24data._resolve(OUT_DIR))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, default=str))


def _selected_rows(res: dict) -> list[dict]:
    rows = []
    for inst, row in res["instruments"].items():
        if row["tier"] != universe.FIVE_YEAR:
            continue
        for side, sr in row["sides"].items():
            sel = sr.get("selected")
            first = (sr.get("premium_translation") or [{}])[0]
            rows.append({
                "instrument": inst,
                "side": side,
                "verdict": sr.get("verdict"),
                "verdict_reason": sr.get("reason"),
                "selected": sel,
                # Where a target *could* sit when nothing was selected: reachable
                # and cost-clearing, carrying no claim that it is profitable.
                "descriptive_t1_pct": (
                    None if sel else first.get("t1_underlying_pct")
                ),
                "descriptive_basis": None if sel else first.get("selection_basis"),
            })
    return rows


def answers(res: dict) -> dict:
    """The user's questions, answered in the order they were asked."""
    sel = _selected_rows(res)
    any_validated = bool(res["validated"])
    return {
        "can_it_be_tested_on_all_instruments": {
            "answer": "PARTLY",
            "detail": (
                f"{len(res['five_year_instruments'])} instrument(s) have enough "
                "history for a chronological split and a verdict; "
                f"{len(res['short_capture_instruments'])} have captured candles "
                "only, so their reach rates are described and no T1 is chosen "
                "for them"
            ),
        },
        "how_much_premium_can_be_expected_over_5_years": {
            "answer": "NOT_MEASURABLE",
            "detail": (
                "there are no historical option books for those years in this "
                "store, so a premium percentage can only be derived from the "
                "measured underlying move plus a stated delta and premium/strike "
                "ratio; that derivation is an optimistic bound, not a measurement"
            ),
        },
        "can_t1_be_set_at_a_reachable_point": {
            "answer": "YES_REACHABILITY_IS_MEASURABLE",
            "detail": (
                "reach rate and time-to-reach are measured for every frozen T1 "
                "distance; the reported T1 for each instrument was chosen on the "
                "development window only"
            ),
        },
        "will_it_work": {
            "answer": "VALIDATED" if any_validated else NO_REACHABLE_T1,
            "detail": (
                "at least one configuration cleared development, validation, the "
                "untouched holdout, 2x cost, outlier removal and the "
                "multiple-testing correction"
                if any_validated else
                "no configuration cleared every bar; the reasons are recorded per "
                "instrument and side rather than summarised away"
            ),
        },
        "where_should_t1_be_set": [
            {
                "instrument": r["instrument"],
                "side": r["side"],
                "t1_pct": (r["selected"] or {}).get("t1_pct"),
                "stop_pct": (r["selected"] or {}).get("sl_pct"),
                "cap_min": (r["selected"] or {}).get("cap_min"),
                "verdict": r["verdict"],
                "descriptive_t1_pct": r["descriptive_t1_pct"],
                "descriptive_basis": r["descriptive_basis"],
            }
            for r in sel
        ],
        "which_premium_should_be_chosen": {
            "answer": "DERIVED_ONLY",
            "detail": (
                "for the same underlying move, a premium worth less of spot moves "
                "a larger percentage, and a higher delta converts more of the move "
                "into premium; the per-delta table states both, and none of it is "
                "a measured option result"
            ),
        },
        "grid_counted": {
            "t1_grid_pct": list(T1_GRID),
            "stop_multiples_of_t1": list(SL_MULTIPLES),
            "caps_min": list(CAPS),
            "hypotheses_counted": res["hypotheses_counted"],
            "bonferroni_threshold": res["bonferroni_threshold"],
        },
    }


def _md(res: dict, ans: dict) -> str:
    lines: list[str] = []
    a = lines.append
    a("# PHASE 32 — REACHABLE T1 ACROSS INSTRUMENTS, AND WHAT IT IMPLIES FOR A PREMIUM")
    a("")
    a("Research only. No production, order-path or live-gate change.")
    a("")
    a(f"Headline: **{res['headline']}**")
    a("")
    a(f"- hypotheses counted: {res['hypotheses_counted']}")
    a(f"- Bonferroni threshold: {res['bonferroni_threshold']:.3e}")
    a(f"- instruments with a chronological split: "
      f"{', '.join(res['five_year_instruments']) or 'none'}")
    a(f"- instruments with captured candles only: "
      f"{len(res['short_capture_instruments'])}")
    a(f"- runtime: {res['runtime_sec']}s")
    a("")
    a("## What is measured and what is not")
    for k, v in res["bases_used"].items():
        a(f"- `{k}` — {v}")
    a("")
    for lim in res["limits"]:
        a(f"- {lim}")
    a("")

    a("## Selected T1 per instrument and side")
    a("")
    a("| instrument | side | T1 % | stop % | cap | holdout net R | holdout trades "
      "| T1 first % | median min | verdict |")
    a("|---|---|---|---|---|---|---|---|---|---|")
    for r in _selected_rows(res):
        s = r["selected"]
        if not s:
            a(f"| {r['instrument']} | {r['side']} | — | — | — | — | — | — | — | "
              f"{r['verdict']} |")
            continue
        h = s["holdout"]
        a(f"| {r['instrument']} | {r['side']} | {s['t1_pct']} | {s['sl_pct']} | "
          f"{s['cap_min']}m | {h.get('net_expectancy_r')} | {h.get('trades')} | "
          f"{h.get('t1_first_pct')} | {s.get('minutes_to_t1_median')} | "
          f"{r['verdict']} |")
    a("")
    for r in _selected_rows(res):
        if r["verdict_reason"]:
            a(f"**{r['instrument']} {r['side']} — why not validated**")
            a(f"- {r['verdict_reason']}")
            a("")

    a("## Where a T1 could sit: reach, adverse side and the cost hurdle")
    a("")
    a("Measured on the untouched holdout of each five-year instrument. A distance "
      "has to pay its round trip several times over before it is worth "
      "attempting, and the equal-sized adverse move is printed beside every reach "
      "rate because a frequently reached target is not a profitable one.")
    a("")
    a("| instrument | side | T1 % | xcost | within 15m | within 30m | within 60m "
      "| median min | adverse same size first % |")
    a("|---|---|---|---|---|---|---|---|---|")
    for inst, row in res["instruments"].items():
        if row["tier"] != universe.FIVE_YEAR:
            continue
        for side, sr in row.get("sides", {}).items():
            for r in (sr.get("cost_hurdle") or {}).get("reach", []):
                a(f"| {inst} | {side} | {r['t1_pct']} | "
                  f"{r.get('t1_in_multiples_of_cost')} | "
                  f"{r.get('reached_within_15m_pct')} | "
                  f"{r.get('reached_within_30m_pct')} | "
                  f"{r.get('reached_within_60m_pct')} | "
                  f"{r.get('minutes_to_reach_median')} | "
                  f"{r.get('adverse_same_size_first_within_60m_pct')} |")
    a("")

    a("## Derived premium at that distance (optimistic bound, not measured)")
    a("")
    a("| instrument | side | T1 % underlying | delta | premium/strike % | "
      "implied premium gain % | measured reach % | median min | basis |")
    a("|---|---|---|---|---|---|---|---|---|")
    for inst, row in res["instruments"].items():
        for side, sr in row.get("sides", {}).items():
            for p in sr.get("premium_translation") or []:
                a(f"| {inst} | {side} | {p['t1_underlying_pct']} | {p['delta']} | "
                  f"{p['premium_over_strike_pct']} | "
                  f"{p['implied_premium_gain_pct']} | "
                  f"{p['measured_reach_pct']} | {p['measured_median_minutes']} | "
                  f"{p.get('selection_basis') or 'SELECTED_ON_DEVELOPMENT'} |")
    a("")

    a("## Instruments with captured candles only")
    a("")
    a("No T1 is chosen for these; the columns describe the capture window.")
    a("")
    a("| instrument | side | instants | sessions | cost % | 0.20% within 60m | "
      "0.50% within 60m |")
    a("|---|---|---|---|---|---|---|")
    for inst, row in res["instruments"].items():
        if row["tier"] != universe.SHORT_CAPTURE:
            continue
        for side, sr in row.get("sides", {}).items():
            by = {r["t1_pct"]: r for r in sr.get("reach", [])}
            r20 = (by.get(0.20) or {}).get("reached_within_60m_pct")
            r50 = (by.get(0.50) or {}).get("reached_within_60m_pct")
            a(f"| {inst} | {side} | {sr.get('decision_instants')} | "
              f"{sr.get('sessions')} | {sr.get('round_trip_cost_pct_median')} | "
              f"{r20} | {r50} |")
    a("")

    a("## Answers")
    for key in ("can_it_be_tested_on_all_instruments",
                "how_much_premium_can_be_expected_over_5_years",
                "can_t1_be_set_at_a_reachable_point",
                "will_it_work",
                "which_premium_should_be_chosen"):
        row = ans[key]
        a("")
        a(f"**{key.replace('_', ' ')}** — `{row['answer']}`")
        a("")
        a(row["detail"])
    a("")
    return "\n".join(lines)


def write(res: dict, out_dir: str | None = None) -> dict:
    """Write every artefact and return the paths.

    ``out_dir`` exists so a fixture run writes somewhere disposable instead of
    overwriting the authoritative artefacts of the real five-year run.
    """
    d = _out(out_dir)
    ans = answers(res)
    _write(d / "p32_inventory.json", res["inventory"])
    _write(d / "p32_configs.json", {
        inst: {side: sr.get("configs") for side, sr in row.get("sides", {}).items()}
        for inst, row in res["instruments"].items()
        if row["tier"] == universe.FIVE_YEAR
    })
    _write(d / "p32_selected.json", _selected_rows(res))
    _write(d / "p32_short_capture.json", {
        inst: row for inst, row in res["instruments"].items()
        if row["tier"] == universe.SHORT_CAPTURE
    })
    _write(d / "p32_premium.json", {
        inst: {side: sr.get("premium_translation")
               for side, sr in row.get("sides", {}).items()}
        for inst, row in res["instruments"].items()
    })
    _write(d / "p32_answers.json", ans)
    _write(d / "p32_raw_result.json", res)
    (d / "PHASE32_RESULT.md").write_text(_md(res, ans))
    return {"dir": str(d), "files": list(FILES)}
