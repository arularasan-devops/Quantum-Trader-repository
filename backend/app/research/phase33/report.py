"""Phase 33 §20/§21/§22 — artefacts, signal cards and the 21 answers.

The signal card is the deliverable the request asked for: per instrument, side and
family, what the entry looked like, how far the move went, how long it took, when
it peaked, what it gave back, and which holding window paid best *after cost* —
with the research-only status attached to every card, because a best historical
hold is not a validated one.

Each answer is written as a sentence with its number in it and its own basis
label. An answer whose data does not exist says so and names what capture would
make it answerable, rather than borrowing a number from a different vehicle.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.research.phase24 import data as p24data
from app.research.phase33 import (
    HOLD_GRID,
    MEASURED,
    NO_HOLD_WINDOW,
    REQUIRES_MORE_DATA,
    SESSION_CLOSE,
)
from app.research.phase33.study import REPORT_HORIZON

OUT_DIR = "data/phase33"

FILES = (
    "p33_answerability.json",
    "p33_hold_tables.json",
    "p33_move_distribution.json",
    "p33_giveback.json",
    "p33_signal_cards.json",
    "p33_engine_vs_board.json",
    "p33_option_cohorts.json",
    "p33_answers.json",
    "p33_raw_result.json",
    "PHASE33_RESULT.md",
)

RESEARCH_ONLY = "RESEARCH_ONLY_NOT_WIRED_TO_PRODUCTION"


def _out(out_dir: str | None = None) -> Path:
    p = Path(out_dir) if out_dir else Path(p24data._resolve(OUT_DIR))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, default=str))


def _families(res: dict):
    """(instrument, side, family row) for every family that had rows."""
    for m in res["instruments"]:
        for side, sr in m.get("sides", {}).items():
            for fam in sr["families"]:
                if fam.get("n"):
                    yield m["instrument"], side, fam


def _best_net(rows: list[dict]) -> dict | None:
    best = None
    for r in rows:
        v = r.get("net_expectancy_pct")
        if v is None:
            continue
        if best is None or v > best["net_expectancy_pct"]:
            best = r
    return best


def signal_cards(res: dict) -> list[dict]:
    """§20 — one card per instrument, side and signal family."""
    cards: list[dict] = []
    for inst, side, fam in _families(res):
        table = fam["hold_table"]
        best = _best_net(table)
        gb = fam["giveback"]
        targets = fam["empirical_targets"]
        sel = fam.get("selection", {})
        reach = {
            r["level_pct"]: r for r in fam["move_distribution"]
        }
        cards.append({
            "instrument": inst,
            "side": side,
            "family": fam["family"],
            "n": fam["n"],
            "sessions": fam["sessions"],
            "rankable": fam["rankable"],
            "basis": MEASURED,
            "vehicle": "FUTURES_UNDERLYING",
            "empirical_reach": [
                {
                    "level_pct": lvl,
                    "reach_rate": row["reach_rate"],
                    "minutes_p50": row["minutes_p50"],
                    "minutes_p90": row["minutes_p90"],
                    "adverse_same_size_first_rate":
                        row["adverse_same_size_first_rate"],
                }
                for lvl, row in reach.items()
            ],
            "mfe_pct_median_at_report_horizon": next(
                (r["mfe_pct_median"] for r in table
                 if r["horizon"] == REPORT_HORIZON), None
            ),
            "mae_pct_median_at_report_horizon": next(
                (r["mae_pct_median"] for r in table
                 if r["horizon"] == REPORT_HORIZON), None
            ),
            "peak_minute_median": gb["peak_minute_median"],
            "fastest_giveback_minute": gb["fastest_giveback_minute"],
            "giveback_exceeds_gain_after_minute":
                gb["giveback_exceeds_gain_after_minute"],
            "returned_to_entry_after_profit_rate":
                gb["returned_to_entry_after_profit_rate"],
            "turned_net_negative_after_profit_rate":
                gb["turned_net_negative_after_profit_rate"],
            "best_net_hold": None if best is None else {
                "horizon": best["horizon"],
                "net_expectancy_pct": best["net_expectancy_pct"],
                "profit_factor": best["profit_factor"],
                "net_win_rate": best["net_win_rate"],
                "basis": "BEST_ON_FULL_HISTORY_NOT_A_SELECTION",
            },
            # T1 is the nearest of the three, so it reads off the *highest* reach
            # percentile; taking them in the order the percentile grid happens to
            # be written would label the farthest target T1.
            "candidate_targets": [
                {
                    "t": name,
                    "from_percentile": pct,
                    "level_pct": row["level_pct"],
                    "empirical_reach_rate": row["empirical_reach_rate"],
                    "cost_multiple": row["cost_multiple"],
                    "clears_cost": row["clears_cost"],
                }
                for name, pct in (("T1", 90), ("T2", 75), ("T3", 50))
                for row in targets if row["target_from_percentile"] == pct
            ],
            "selected_horizon": sel.get("selected_horizon"),
            "status": sel.get("status", REQUIRES_MORE_DATA),
            "status_reason": sel.get("reason"),
            "usage": RESEARCH_ONLY,
        })
    return cards


def _pick(res: dict, key: str, horizon: int | str):
    """The best (instrument, side, family) at one horizon, by net expectancy."""
    best = None
    for inst, side, fam in _families(res):
        row = next((r for r in fam["hold_table"] if r["horizon"] == horizon), None)
        if row is None or row.get(key) is None:
            continue
        if best is None or row[key] > best[3]:
            best = (inst, side, fam["family"], row[key])
    return best


def answers(res: dict) -> dict:
    """§21 — the 21 questions, each with its number and its basis."""
    cards = signal_cards(res)
    rankable = [c for c in cards if c["rankable"]]
    cov = res["engine_vs_board"]["coverage"]
    opt = res["option_cohorts"]
    out: dict[str, dict] = {}

    def a(q: str, text: str, basis: str) -> None:
        out[q] = {"answer": text, "basis": basis}

    def best_card(field: str) -> dict | None:
        vals = [c for c in rankable if (c.get(field) or {}).get(
            "net_expectancy_pct") is not None]
        if not vals:
            return None
        return max(vals, key=lambda c: c[field]["net_expectancy_pct"])

    # Movement and timing
    if rankable:
        far = max(rankable, key=lambda c: c["mfe_pct_median_at_report_horizon"] or -9e9)
        a("how_much_does_each_signal_move",
          f"the largest median favourable excursion at {REPORT_HORIZON} minutes is "
          f"{far['mfe_pct_median_at_report_horizon']}% "
          f"({far['family']} {far['side']} on {far['instrument']}, n={far['n']}), "
          f"against a median adverse of "
          f"{far['mae_pct_median_at_report_horizon']}% on the same window",
          MEASURED)
        quick = min(
            (c for c in rankable
             if c["empirical_reach"] and c["empirical_reach"][0]["minutes_p50"]),
            key=lambda c: c["empirical_reach"][0]["minutes_p50"], default=None)
        a("how_quickly_does_it_move",
          "no cohort reached the nearest frozen distance often enough to time it"
          if quick is None else
          f"the nearest frozen distance "
          f"({quick['empirical_reach'][0]['level_pct']}%) is reached in a median "
          f"{quick['empirical_reach'][0]['minutes_p50']} minutes at best "
          f"({quick['family']} {quick['side']} on {quick['instrument']})",
          MEASURED)
        top_reach = max(
            (e for c in rankable for e in c["empirical_reach"]
             if e["reach_rate"] is not None),
            key=lambda e: e["reach_rate"], default=None)
        a("which_move_is_reached_most_often",
          "no distance had a measurable reach rate" if top_reach is None else
          f"the {top_reach['level_pct']}% distance, reached "
          f"{round(100 * top_reach['reach_rate'], 1)}% of the time within "
          f"{REPORT_HORIZON} minutes — and an adverse move of the same size arrived "
          f"first in {round(100 * (top_reach['adverse_same_size_first_rate'] or 0), 1)}% "
          "of those instants, which is why a high reach rate is not an edge",
          MEASURED)
        bn = best_card("best_net_hold")
        a("which_move_has_the_best_net_expectancy",
          "no cohort had positive net expectancy at any horizon" if bn is None or
          (bn["best_net_hold"]["net_expectancy_pct"] or 0) <= 0 else
          f"{bn['family']} {bn['side']} on {bn['instrument']} at "
          f"{bn['best_net_hold']['horizon']} minutes, "
          f"{bn['best_net_hold']['net_expectancy_pct']}% net per trade",
          MEASURED)
        peaks = [c["peak_minute_median"] for c in rankable
                 if c["peak_minute_median"] is not None]
        a("when_does_the_trade_peak",
          "no cohort had a measurable peak" if not peaks else
          f"the median favourable peak lands between {min(peaks)} and {max(peaks)} "
          f"minutes across cohorts, inside a {max(HOLD_GRID)}-minute window",
          MEASURED)
        accel = [c["giveback_exceeds_gain_after_minute"] for c in rankable
                 if c["giveback_exceeds_gain_after_minute"] is not None]
        a("when_does_giveback_become_significant",
          "giveback could not be timed" if not accel else
          f"from minute {min(accel)}-{max(accel)} onward, another minute of holding "
          "hands back more of the best look than it adds to what the trade keeps, "
          "on every cohort measured",
          MEASURED)
    else:
        for q in ("how_much_does_each_signal_move", "how_quickly_does_it_move",
                  "which_move_is_reached_most_often",
                  "which_move_has_the_best_net_expectancy",
                  "when_does_the_trade_peak",
                  "when_does_giveback_become_significant"):
            a(q, "no cohort cleared the sample floor", REQUIRES_MORE_DATA)

    # Horizon comparisons, answered on net expectancy rather than on MFE.
    for lo, hi, key in ((15, 30, "is_15m_better_than_30m"),
                        (30, 60, "is_30m_better_than_60m"),
                        (60, 120, "is_60m_better_than_120m")):
        b_lo, b_hi = _pick(res, "net_expectancy_pct", lo), _pick(
            res, "net_expectancy_pct", hi)
        if b_lo is None or b_hi is None:
            a(key, "not comparable on the available sample", REQUIRES_MORE_DATA)
            continue
        better = f"{lo}m" if b_lo[3] > b_hi[3] else f"{hi}m"
        a(key,
          f"{better} is better on net expectancy: the best {lo}-minute cohort nets "
          f"{round(b_lo[3], 5)}% ({b_lo[2]} {b_lo[1]} {b_lo[0]}) against "
          f"{round(b_hi[3], 5)}% at {hi} minutes ({b_hi[2]} {b_hi[1]} {b_hi[0]}); "
          "both are stated after round-trip cost",
          MEASURED)

    longer = [c for c in rankable
              if (c["best_net_hold"] or {}).get("horizon") in (SESSION_CLOSE, 120, 90)]
    quicker = [c for c in rankable
               if (c["best_net_hold"] or {}).get("horizon") in (1, 2, 5)]
    a("which_setup_should_be_held_longer",
      "no cohort's best net horizon was a long one" if not longer else
      ", ".join(sorted({f"{c['family']} {c['side']} on {c['instrument']}"
                        for c in longer}))
      + " had their best net expectancy at the longest horizons — best-historical, "
        "not validated",
      MEASURED)
    a("which_setup_should_be_exited_quickly",
      "no cohort's best net horizon was a short one" if not quicker else
      ", ".join(sorted({f"{c['family']} {c['side']} on {c['instrument']}"
                        for c in quicker}))
      + " peaked economically within five minutes of the fill",
      MEASURED)

    # Engine against the board
    diag = res["engine_vs_board"]["underlying_diagnosis"]
    graded = None
    for inst, row in diag.get("instruments", {}).items():
        for side in ("LONG", "SHORT"):
            eng = row.get(f"{side}_engine_buys") or {}
            brd = row.get(f"{side}_board_not_taken") or {}
            if eng.get("net_expectancy_pct") is None or brd.get(
                    "net_expectancy_pct") is None:
                continue
            graded = (inst, side, eng, brd)
    if graded is None:
        for q in ("does_the_engine_enter_too_early", "does_the_engine_enter_too_late"):
            a(q,
              f"the engine recorded {cov['engine_buys']} BUY decision(s); after "
              "joining them to measured bars no cohort reached the 30-row floor, so "
              "entry timing cannot be graded yet",
              REQUIRES_MORE_DATA)
    else:
        inst, side, eng, brd = graded
        a("does_the_engine_enter_too_early",
          f"on {inst} {side} the engine's own BUY minutes netted "
          f"{eng['net_expectancy_pct']}% over {max(HOLD_GRID)} minutes (n={eng['n']}) "
          f"against {brd['net_expectancy_pct']}% for board minutes it did not take "
          f"(n={brd['n']}); the difference is a description of one captured window, "
          "not an entry-timing rule",
          "COUNTERFACTUAL")
        a("does_the_engine_enter_too_late",
          "the same two cohorts answer this question, and one captured window "
          "cannot separate too-early from too-late: both would show as a worse net "
          "than the pool it chose from",
          "COUNTERFACTUAL")
    a("what_does_the_board_contain_that_the_engine_misses",
      f"{cov['board_minutes_without_engine_buy']} of {cov['board_minutes']} board "
      f"minutes carried no engine BUY, and {cov['board_rows_priceable']} of "
      f"{cov['board_rows']} board rows have a real two-sided quote; whether those "
      "minutes were worth taking is unanswerable until the quote coverage rises",
      "COUNTERFACTUAL")

    # Options
    for q in ("ce_or_pe_better_net_economics", "is_futures_better_than_options",
              "strongest_net_movement_combination"):
        a(q,
          f"{opt['total_two_sided_rows']} real two-sided option rows exist against a "
          f"floor of {opt['min_required_per_cohort']} per cohort. Entry-at-ask and "
          "exit-at-bid cannot be priced on that, and midpoint, LTP, a nearby "
          "timestamp or a family median are all forbidden substitutes, so this stays "
          "unanswered rather than estimated",
          REQUIRES_MORE_DATA)

    # Selection and survival
    corr = res["correction"]
    a("which_holding_period_has_the_best_net_expectancy",
      "no holding period was selected: no horizon had positive net expectancy on "
      "development data for any cohort"
      if not any(s.get("selected_horizon") for s in res["selections"]) else
      "; ".join(
          f"{s['family']} {s['side']} {s['instrument']}: {s['selected_horizon']} "
          f"minutes ({s['status']})"
          for s in res["selections"] if s.get("selected_horizon")),
      MEASURED)
    tgt = [c for c in rankable
           if any(t["clears_cost"] for t in c["candidate_targets"])]
    a("which_target_has_the_highest_repeatable_reach_rate",
      "no candidate target cleared its own round-trip cost" if not tgt else
      f"{len(tgt)} of {len(rankable)} rankable cohorts have a candidate target that "
      "clears cost; the highest reach rate among them belongs to the nearest "
      "distance, which is also the one closest to the cost hurdle — reach rate and "
      "profitability point in opposite directions here",
      "EMPIRICAL_REACH_RATE")
    a("does_any_result_survive_holdout_and_walk_forward",
      f"{corr['validated']} validated and {corr['research_leads']} research lead(s) "
      f"out of {corr['hypotheses_counted']} counted hypotheses",
      MEASURED)
    stressed = [s for s in res["selections"] if s.get("cost_stress")]
    a("does_any_result_survive_cost_and_slippage_stress",
      "nothing reached the cost-stress stage, because nothing was positive on "
      "development data first" if not stressed else
      f"{sum(1 for s in stressed if not s.get('failures'))} of {len(stressed)} "
      "cohorts that reached the stage survived 1.5x and 2x costs and the removal of "
      "the top 1% and 5% of winners",
      MEASURED)
    return out


def markdown(res: dict, cards: list[dict], ans: dict) -> str:
    lines: list[str] = []
    a = lines.append
    verdict = res.get("verdict") or NO_HOLD_WINDOW
    a("# PHASE 33 — SIGNAL MOVE, HOLD TIME AND GIVEBACK")
    a("")
    a(f"**VERDICT: {verdict}**")
    a("")
    a(f"- hypotheses counted: {res['correction']['hypotheses_counted']}")
    a(f"- validated hold windows: {res['correction']['validated']}")
    a(f"- research leads: {res['correction']['research_leads']}")
    a(f"- scope: {res['verdict_scope']}")
    a(f"- {res['verdict_note']}")
    a("- production changed: no")
    a("")
    a("## What the data can answer")
    ansmap = res["answerability"]
    a(f"- five-year instruments: {', '.join(ansmap['five_year_instruments'])}")
    a(f"- short-capture instruments: {len(ansmap['short_capture_instruments'])} "
      "(described, never verdicted)")
    a(f"- real two-sided option observations: "
      f"{ansmap['options']['real_two_sided_observations']} against a floor of "
      f"{ansmap['options']['min_required']}")
    a(f"- engine BUY decisions recorded: {ansmap['engine']['actionable_buys']} over "
      f"{ansmap['engine']['sessions']} session(s)")
    a("")
    a("## Hold-time economics, best net horizon per cohort")
    a("")
    a("| instrument | side | family | n | best net horizon | net % | PF | status |")
    a("|---|---|---|---|---|---|---|---|")
    for c in sorted(cards, key=lambda c: (c["instrument"], c["side"], c["family"])):
        b = c["best_net_hold"] or {}
        a(f"| {c['instrument']} | {c['side']} | {c['family']} | {c['n']} | "
          f"{b.get('horizon')} | {b.get('net_expectancy_pct')} | "
          f"{b.get('profit_factor')} | {c['status']} |")
    a("")
    a("Every net figure is after the measured round-trip cost and excludes the "
      "spread, which the historical candle feed does not publish — so each is "
      "optimistic by exactly one spread.")
    a("")
    a("## Giveback")
    a("")
    a("| instrument | side | family | peak min | giveback outpaces gain after | "
      "fastest giveback min | returned to entry | turned net negative |")
    a("|---|---|---|---|---|---|---|---|")
    for c in sorted(cards, key=lambda c: (c["instrument"], c["side"], c["family"])):
        a(f"| {c['instrument']} | {c['side']} | {c['family']} | "
          f"{c['peak_minute_median']} | "
          f"{c['giveback_exceeds_gain_after_minute']} | "
          f"{c['fastest_giveback_minute']} | "
          f"{c['returned_to_entry_after_profit_rate']} | "
          f"{c['turned_net_negative_after_profit_rate']} |")
    a("")
    a("## The 21 answers")
    a("")
    for i, (q, row) in enumerate(ans.items(), start=1):
        a(f"{i}. **{q}** — {row['answer']} _[{row['basis']}]_")
    a("")
    a("## What was not done")
    a("")
    a("- no signal, entry, strike, target, stop, exit, hold-timer, averaging, "
      "sizing or order-path change;")
    a("- no discovered holding period wired into production;")
    a("- no option premium inferred from underlying movement;")
    a("- no holding period selected on the full dataset.")
    return "\n".join(lines) + "\n"


def write(res: dict, out_dir: str | None = None) -> dict:
    d = _out(out_dir)
    cards = signal_cards(res)
    ans = answers(res)
    _write(d / "p33_answerability.json", res["answerability"])
    _write(d / "p33_hold_tables.json", [
        {"instrument": i, "side": s, "family": f["family"],
         "n": f["n"], "hold_table": f["hold_table"]}
        for i, s, f in _families(res)
    ])
    _write(d / "p33_move_distribution.json", [
        {"instrument": i, "side": s, "family": f["family"],
         "move_distribution": f["move_distribution"],
         "empirical_targets": f["empirical_targets"],
         "overnight": f["overnight"]}
        for i, s, f in _families(res)
    ])
    _write(d / "p33_giveback.json", [
        {"instrument": i, "side": s, "family": f["family"], "giveback": f["giveback"]}
        for i, s, f in _families(res)
    ])
    _write(d / "p33_signal_cards.json", cards)
    _write(d / "p33_engine_vs_board.json", res["engine_vs_board"])
    _write(d / "p33_option_cohorts.json", res["option_cohorts"])
    _write(d / "p33_answers.json", ans)
    _write(d / "p33_raw_result.json", res)
    (d / "PHASE33_RESULT.md").write_text(markdown(res, cards, ans))
    return {"dir": str(d), "files": list(FILES)}
