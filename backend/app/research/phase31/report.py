"""Phase 31 — artefacts and the written answer.

The report answers the question that prompted the phase — *how much percent can
be expected, and when* — with the measured half stated plainly and the assumed
half labelled everywhere it appears.
"""
from __future__ import annotations

import json
import os

from app.research.phase24 import data
from app.research.phase31 import (
    ASSUMED_PREMIUM_MAP,
    HORIZONS,
    PCT_GRID,
    PREMIUM_TARGETS_PCT,
    UNMEASURED,
)

OUT_DIR = "data/phase31"

ARTEFACTS = (
    "p31_evidence.json",
    "p31_underlying_excursion.json",
    "p31_by_session_period.json",
    "p31_realized_premium.json",
    "p31_premium_map.json",
    "p31_answers.json",
    "p31_raw_result.json",
    "PHASE31_RESULT.md",
)

# The headline premium target the summary table is written around.
HEADLINE_TARGET_PCT = 20.0
HEADLINE_DELTA = 0.50


def out_dir() -> str:
    return data._resolve(OUT_DIR)


def _headline_rows(result: dict) -> list[dict]:
    rows = []
    for inst, blk in result.get("instruments", {}).items():
        for side, tbl in (blk.get("premium_map") or {}).items():
            for row in tbl:
                if (row["target_premium_pct"] == HEADLINE_TARGET_PCT
                        and row["delta"] == HEADLINE_DELTA):
                    rows.append({**row, "side": side, "instrument": inst})
    return rows


def answers(result: dict) -> list[dict]:
    """The questions this phase exists to answer, each with its basis."""
    out: list[dict] = []

    def add(q: str, a: str, basis: str) -> None:
        out.append({"question": q, "answer": a, "basis": basis})

    inv = result.get("inventory", {})
    real = result.get("realized", {})

    for inst, blk in result.get("instruments", {}).items():
        for side in ("LONG", "SHORT"):
            s = (blk["sides"].get(side) or {}).get("summary") or {}
            h = (s.get("horizons") or {})
            def fav(hz: str) -> str:
                d = (h.get(hz) or {}).get("favourable_pct") or {}
                return (f"median {d.get('p50')}%, p75 {d.get('p75')}%, "
                        f"p90 {d.get('p90')}%")
            add(
                f"{inst} {side}: how far does it move in favour, and by when?",
                "; ".join(f"{hz}m: {fav(hz)}" for hz in ("5", "15", "30", "60")),
                result["instruments"][inst]["basis"],
            )
            adv = ((h.get("60") or {}).get("adverse_pct") or {})
            add(
                f"{inst} {side}: what does it give up while doing it?",
                f"within 60m the adverse excursion is median {adv.get('p50')}%, "
                f"p10 {adv.get('p10')}% — the favourable figure above is not "
                "reachable without sitting through this",
                result["instruments"][inst]["basis"],
            )
            thr = s.get("thresholds") or {}
            worth = thr.get("0.20") or {}
            add(
                f"{inst} {side}: how often does a 0.20% move arrive, and how "
                "quickly?",
                f"{worth.get('reached_pct_of_instants')}% of decision instants "
                f"within 60m, median {(worth.get('minutes_to_reach') or {}).get('p50')} "
                f"minutes; {worth.get('reached_within_15m_pct')}% within 15m, "
                f"and in {worth.get('adverse_same_size_first_pct')}% of those "
                "the same-size adverse move came first",
                result["instruments"][inst]["basis"],
            )

    for row in _headline_rows(result):
        add(
            f"{row['instrument']} {row['side']}: what does +{HEADLINE_TARGET_PCT:.0f}% "
            f"on the premium require, and how often does the market deliver it?",
            (f"at delta {row['delta']} and a premium worth "
             f"{row['premium_over_strike_pct']}% of the strike it needs a "
             f"{row['required_underlying_move_pct']}% underlying move; measured "
             f"at the {row['measured_threshold_used_pct']}% threshold that "
             f"arrived in {row['measured_reach_pct_of_instants']}% of instants "
             f"within 60m (median {row['measured_median_minutes_to_reach']} "
             f"minutes, {row['measured_reach_within_15m_pct']}% within 15m)")
            if row.get("measured_reach_pct_of_instants") is not None
            else row.get("off_grid", "not answered"),
            ASSUMED_PREMIUM_MAP,
        )

    add(
        "Can a premium percentage be measured from the stored option chains?",
        result.get("premium_book_reason", ""),
        result.get("premium_book_result", UNMEASURED),
    )
    add(
        "What did the tool's own recorded calls actually return in premium "
        "percent, and in how long?",
        (f"{real.get('usable_option_trades')} engine-resolved option fills exist "
         f"({real.get('option_premium_pct', {}).get('mean')}% mean, "
         f"{real.get('option_hold_minutes', {}).get('p50')} minute median hold) "
         f"— below the {real.get('min_required')} needed to describe a "
         "distribution, so this is history, not an expectation. "
         f"{(real.get('classification') or {}).get('by_class', {})} shows why: "
         "most journal rows are placeholder-priced or manual-panel closes")
        if not real.get("sufficient") else
        (f"{real.get('usable_option_trades')} fills: median "
         f"{real.get('option_premium_pct', {}).get('p50')}%, median hold "
         f"{real.get('option_hold_minutes', {}).get('p50')} minutes"),
        result.get("realized", {}).get("basis"),
    )
    add(
        "Is the recorded premium result a statement about the market or about "
        "the exit rule?",
        "about the exit rule as much as the market: every recorded row was cut "
        "by whatever exit was live at the time, so what was left on the table "
        "after the exit is not in these numbers and cannot be recovered from "
        "them",
        result.get("realized", {}).get("basis"),
    )
    add(
        "How much real two-sided premium evidence exists in total?",
        f"{inv.get('real_two_sided_decision_instants')} decision instants "
        f"({(inv.get('option_books') or {}).get('real_broker_snapshots')} "
        "REAL_BROKER chain snapshots plus "
        f"{(inv.get('captured_observations') or {}).get('two_sided_at_decision')} "
        "exact captures); everything else is labelled SIMULATOR or predates "
        "provenance labelling and is not evidence of a premium move",
        UNMEASURED,
    )
    return out


def _md(result: dict) -> str:
    L: list[str] = []
    A = L.append
    A("# PHASE 31 — HOW FAR IT MOVES, AND WHEN")
    A("")
    A("Research only. No production, order-path or live-gate change.")
    A("")
    A("Phase 30 asked a binary question (did T1 beat the stop) and that hid the "
      "number actually wanted: how much a call can be expected to give, and how "
      "long it takes. This phase drops stops and targets and measures the "
      "excursion distribution instead.")
    A("")
    A("## What is measured and what is assumed")
    A("")
    A("| basis | what it covers |")
    A("|---|---|")
    for k, v in (result.get("bases_used") or {}).items():
        A(f"| `{k}` | {v} |")
    A("")
    inv = result.get("inventory", {})
    A(f"Real two-sided premium instants in the store: "
      f"**{inv.get('real_two_sided_decision_instants')}** "
      f"(need {inv.get('min_required_for_premium_distribution')}). "
      f"So the premium distribution from stored chains is "
      f"`{result.get('premium_book_result')}` — "
      f"{result.get('premium_book_reason')}.")
    A("")

    A("## MEASURED — underlying excursion, five years, per side")
    A("")
    for inst, blk in (result.get("instruments") or {}).items():
        A(f"### {inst} — {blk.get('bars'):,} bars, {blk.get('sessions')} sessions")
        A("")
        for side in ("LONG", "SHORT"):
            s = (blk["sides"].get(side) or {}).get("summary") or {}
            A(f"**{side}** — {s.get('decision_instants'):,} decision instants")
            A("")
            A("| horizon | favourable p50 | p75 | p90 | adverse p50 | p10 |")
            A("|---|---|---|---|---|---|")
            for h in HORIZONS:
                hz = (s.get("horizons") or {}).get(str(h)) or {}
                f = hz.get("favourable_pct") or {}
                a = hz.get("adverse_pct") or {}
                A(f"| {h}m | {f.get('p50')}% | {f.get('p75')}% | {f.get('p90')}% "
                  f"| {a.get('p50')}% | {a.get('p10')}% |")
            A("")
            A("| move | reached within 60m | median minutes | within 15m | "
              "adverse same size first |")
            A("|---|---|---|---|---|")
            for thr in PCT_GRID:
                t = (s.get("thresholds") or {}).get(f"{thr:.2f}") or {}
                A(f"| {thr:.2f}% | {t.get('reached_pct_of_instants')}% | "
                  f"{(t.get('minutes_to_reach') or {}).get('p50')} | "
                  f"{t.get('reached_within_15m_pct')}% | "
                  f"{t.get('adverse_same_size_first_pct')}% |")
            A("")

    A("## ASSUMED_PREMIUM_MAP — what a premium target requires")
    A("")
    A("The requirement is arithmetic (`target% x premium/strike / delta`); the "
      "reach and timing columns beside it are measured. Theta, IV change and the "
      "spread are excluded, all of which make the real premium outcome worse — "
      "so these are optimistic bounds.")
    A("")
    A(f"At delta {HEADLINE_DELTA} for a +{HEADLINE_TARGET_PCT:.0f}% premium gain:")
    A("")
    A("| instrument | side | needs underlying | measured at | reached 60m | "
      "median min | reached 15m |")
    A("|---|---|---|---|---|---|---|")
    for row in _headline_rows(result):
        A(f"| {row['instrument']} | {row['side']} | "
          f"{row['required_underlying_move_pct']}% | "
          f"{row['measured_threshold_used_pct']}% | "
          f"{row['measured_reach_pct_of_instants']}% | "
          f"{row['measured_median_minutes_to_reach']} | "
          f"{row['measured_reach_within_15m_pct']}% |")
    A("")
    A(f"Other targets ({', '.join(f'{t:.0f}%' for t in PREMIUM_TARGETS_PCT)}) and "
      "delta bands are in `p31_premium_map.json`.")
    A("")

    real = result.get("realized") or {}
    A("## MEASURED_REALIZED — the tool's own recorded calls")
    A("")
    cls = (real.get("classification") or {}).get("by_class") or {}
    A(f"Rows seen: {real.get('rows_seen')}. Classified: "
      + ", ".join(f"`{k}` {v}" for k, v in sorted(cls.items())) + ".")
    A("")
    A(f"Usable engine-resolved option fills: **{real.get('usable_option_trades')}** "
      f"(need {real.get('min_required')} to describe a distribution → "
      f"sufficient: {real.get('sufficient')}).")
    A("")
    op = real.get("option_premium_pct") or {}
    A(f"Those fills: mean {op.get('mean')}%, best {op.get('best')}%, "
      f"worst {op.get('worst')}%, median hold "
      f"{(real.get('option_hold_minutes') or {}).get('p50')} minutes.")
    A("")
    fut = real.get("futures_pct_of_price") or {}
    A(f"Futures rows are reported separately ({real.get('futures_rows_kept_separate')} "
      f"rows, median {fut.get('p50')}% of price, "
      f"{(real.get('futures_hold_minutes') or {}).get('p50')} minute median hold): "
      "a percentage of a futures price is not a premium percentage.")
    A("")
    for lim in real.get("limits") or []:
        A(f"- {lim}")
    A("")

    A("## ANSWERS")
    A("")
    for row in answers(result):
        A(f"**{row['question']}**")
        A("")
        A(f"{row['answer']}  ")
        A(f"*basis: `{row['basis']}`*")
        A("")
    A(f"Runtime {result.get('runtime_sec')}s.")
    return "\n".join(L)


def write(result: dict, path: str | None = None) -> dict[str, str]:
    d = path or out_dir()
    os.makedirs(d, exist_ok=True)
    files: dict[str, str] = {}

    def dump(name: str, payload) -> None:
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, sort_keys=False, default=str)
        files[name] = p

    dump("p31_evidence.json", result.get("inventory"))
    dump("p31_underlying_excursion.json", {
        inst: {
            "bars": blk.get("bars"),
            "sessions": blk.get("sessions"),
            "sides": {
                side: (blk["sides"][side] or {}).get("summary")
                for side in blk.get("sides", {})
            },
        }
        for inst, blk in (result.get("instruments") or {}).items()
    })
    dump("p31_by_session_period.json", {
        inst: {
            side: (blk["sides"][side] or {}).get("by_period")
            for side in blk.get("sides", {})
        }
        for inst, blk in (result.get("instruments") or {}).items()
    })
    dump("p31_realized_premium.json", result.get("realized"))
    dump("p31_premium_map.json", {
        inst: blk.get("premium_map")
        for inst, blk in (result.get("instruments") or {}).items()
    })
    dump("p31_answers.json", answers(result))
    dump("p31_raw_result.json", result)

    md = os.path.join(d, "PHASE31_RESULT.md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write(_md(result))
    files["PHASE31_RESULT.md"] = md
    return files
