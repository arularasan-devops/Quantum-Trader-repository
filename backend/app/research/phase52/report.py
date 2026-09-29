"""Phase 52 §14/§16/§18 — the table, the five totals, and the eight answers.

The report is written to be readable by somebody who wants to refuse it. So the
funnel comes before the results (a mechanism that fired nine times a year is a
sample-size story, and a reader should know that before reading an expectancy),
the family collapse comes before the ranking (ten spellings of one event set are
one row, not ten), and every partition column carries its own trade count.

Nothing here ranks by discovery P&L. The ordering is by final status, then by
the validation partition, because a discovery number is the one number in the
table that was chosen on.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.research.phase52 import (
    CANDLE_HAS_NO_BOOK,
    COST_GATE_IS_NOT_A_PROBABILITY,
    DISCOVERY,
    FAMILIES_NOT_DISCOVERIES,
    HISTORICAL_LEAD,
    MECHANISM,
    NO_OPTION_CLAIM,
    NO_ORDER_PATH,
    NOT_A_PREDICTION,
    PROMISING_NEEDS_DATA,
    ROBUST_CANDIDATE,
    SAME_BAR_TIE_IS_A_LOSS,
    UNTOUCHED_HOLDOUT,
    VALIDATION,
    fingerprint,
    preregistration,
)

ARTEFACT_DIR = Path("data/phase52")

STATUS_ORDER = {
    ROBUST_CANDIDATE: 0,
    PROMISING_NEEDS_DATA: 1,
    HISTORICAL_LEAD: 2,
}


def _r(value: float | None, width: int = 7, places: int = 3) -> str:
    if value is None:
        return "—".rjust(width)
    return f"{value:+.{places}f}".rjust(width)


def _n(value: float | None, width: int = 5) -> str:
    if value is None:
        return "—".rjust(width)
    return f"{value:.2f}".rjust(width)


def rank(rows: list[dict]) -> list[dict]:
    """Order by status, then by the validation partition. Never by discovery."""
    def key(r: dict) -> tuple:
        val = r["per_partition"][VALIDATION]
        return (
            STATUS_ORDER.get(r["final_status"], 9),
            -(val.get("net_expectancy_r") or -99.0),
            -r["trades"],
        )
    return sorted(rows, key=key)


def render(result: dict, *, top: int = 20) -> str:
    """The whole report for one run, as text."""
    out: list[str] = []
    w = out.append
    w(f"PHASE 52 — {MECHANISM}")
    w(f"  pre-registration fingerprint {fingerprint()}")
    w("  one mechanism, one bounded robustness grid, graded once")
    w("")

    for inst in result["instruments"]:
        name = inst["instrument"]
        if not inst["eligible"]:
            w(f"{name}  REFUSED  {inst['eligibility'].get('reason')}")
            w("")
            continue
        w(f"{name}")
        bars = inst.get("bars", {}).get("five_minute", {})
        w(f"  5-minute bars {bars.get('bars', 0):,} from "
          f"{bars.get('source_minute_bars', 0):,} minutes over "
          f"{bars.get('sessions', 0):,} sessions   "
          f"short bars {bars.get('short_bar_pct', 0)}%")
        for conf, funnel in sorted(inst["funnels"].items()):
            w(f"  FUNNEL — {conf}")
            w(f"    sessions                                   "
              f"{funnel.get('sessions', 0):>6,}")
            w(f"    {'opened beyond the level at all':<42s} "
              f"{funnel.get('opened_beyond_the_level_at_all', 0):>6,}")
            for key in (
                "PREVIOUS_SESSION_OR_ATR_NOT_YET_KNOWABLE",
                "NO_GAP",
                "NO_EXTENSION_BEYOND_THE_OPENING_AREA",
                "NO_CLOSE_BACK_INSIDE_THE_PREVIOUS_DAY_RANGE",
                "PREVIOUS_DAY_CLOSE_IS_NOT_BEYOND_THE_ENTRY",
                "STOP_WIDER_THAN_THE_DECLARED_CAP",
                "NO_FORWARD_BARS_LEFT_IN_THE_SESSION",
            ):
                w(f"    {key:<42s} {funnel.get(key, 0):>6,}")
            w(f"    {'priced events':<42s} {funnel.get('candidates', 0):>6,}")
        w("")

        w(f"  EVENT FAMILIES — {inst['totals']['UNIQUE_EVENT_FAMILIES']} "
          f"families from {inst['totals']['TOTAL_HYPOTHESES']} "
          f"parameterizations")
        for fam in inst["families"]:
            w(f"    {fam['event_family']}  {fam['trades']:>4} trades  "
              f"{len(fam['variants']):>2} spellings  "
              f"fdr {'pass' if fam['fdr_pass'] else 'no'}")
        w("")

        w("  RESEARCH TABLE — ordered by status then validation, never by discovery")
        w("    variant                                     "
          "trades  disc n   disc R  val n    val R  hold n   hold R      PF  "
          "status")
        for row in rank(inst["rows"])[:top]:
            d = row["per_partition"][DISCOVERY]
            v = row["per_partition"][VALIDATION]
            h = row["per_partition"][UNTOUCHED_HOLDOUT]
            w(f"    {row['variant_id']:<44s}"
              f"{row['trades']:>5}"
              f"{d['trade_count']:>8}"
              f" {_r(d.get('net_expectancy_r'))}"
              f"{v['trade_count']:>7}"
              f" {_r(v.get('net_expectancy_r'))}"
              f"{h['trade_count']:>8}"
              f" {_r(h.get('net_expectancy_r'))}"
              f" {_n(row['overall'].get('profit_factor'))}"
              f"  {row['final_status']}")
        w("")

        best = rank(inst["rows"])[0] if inst["rows"] else None
        if best is not None:
            w(f"  DETAIL — {best['variant_id']}  ({best['final_status']})")
            w(f"    rule: {best['human_readable_rule']}")
            o = best["overall"]
            if o.get("measured"):
                w(f"    trades {o['trade_count']}  sessions {o['session_count']}"
                  f"  win rate {100.0 * o['win_rate']:.0f}%"
                  f"  target {100.0 * o['target_rate']:.0f}%"
                  f"  stop {100.0 * o['stop_rate']:.0f}%"
                  f"  time exit {100.0 * o['time_exit_rate']:.0f}%")
                w(f"    net expectancy {o['net_expectancy_r']:+.3f}R "
                  f"({o['net_expectancy_points']:+.2f} points)"
                  f"   net total {o['net_total_r']:+.2f}R"
                  f"   PF {_n(o['profit_factor'])}")
                w(f"    average winner {_r(o['average_winner_r'])}R  "
                  f"average loser {_r(o['average_loser_r'])}R  "
                  f"largest winner {o['largest_winner_r']:+.2f}R  "
                  f"largest loser {o['largest_loser_r']:+.2f}R")
                w(f"    max drawdown {o['max_drawdown_r']:.2f}R   "
                  f"average hold {o['average_hold_minutes']:.0f} min   "
                  f"median target distance {o['median_target_distance']:.2f} "
                  f"points")
                w(f"    median cost {o['median_cost_points']:.2f} points   "
                  f"median movement/cost multiple "
                  f"{o['median_cost_multiple']:.2f}")
                w(f"    GROSS vs NET — gross "
                  f"{o['gross_expectancy_points']:+.2f} points per trade, net "
                  f"{o['net_expectancy_points']:+.2f}, cost consumed "
                  f"{o['cost_share_of_gross_pct']:.1f}% of the gross move")
                w(f"    §15 reading: {best['effect_reading']}")
                for mult, row2 in sorted(best["cost_stress"].items()):
                    w(f"    cost x{mult}  net expectancy "
                      f"{_r(row2.get('net_expectancy_r'))}R  "
                      f"trades {row2.get('trade_count')}")
                for label, row2 in sorted(best["direction_split"].items()):
                    w(f"    {label:<28s} trades {row2['trade_count']:>3}  "
                      f"net {_r(row2.get('net_expectancy_r'))}R")
                w("    per year:")
                for year, row2 in sorted(best["per_year"].items()):
                    w(f"      {year}  trades {row2['trade_count']:>3}  "
                      f"net {_r(row2['net_expectancy_r'])}R  "
                      f"total {_r(row2['net_total_r'])}R")
                w("    per quarter:")
                for quarter, row2 in sorted(best["per_quarter"].items()):
                    w(f"      {quarter}  trades {row2['trade_count']:>3}  "
                      f"net {_r(row2['net_expectancy_r'])}R")
                sd = best["session_distribution"]
                if sd.get("measured"):
                    w(f"    session distribution: median entry "
                      f"{sd['median_minutes_after_open']:.0f} minutes after the "
                      f"open, {sd['by_hour_after_open']}")
            for reason in best["status_reasons"]:
                w(f"    WHY NOT ROBUST: {reason}")
            w("")

    t = result["totals"]
    w("TOTALS")
    for key in ("TOTAL_HYPOTHESES", "UNIQUE_EVENT_FAMILIES", "DISCOVERY_LEADS",
                "VALIDATION_POSITIVE", "HOLDOUT_POSITIVE", "ROBUST_CANDIDATES"):
        w(f"  {key:<26s} {t[key]:>6,}")
    if t["ROBUST_CANDIDATES"] == 0:
        w("  ROBUST_CANDIDATES = 0 — nothing cleared the declared bar, and no "
          "row is substituted for one")
    w("")

    w("THE EIGHT ANSWERS")
    for i, (question, answer) in enumerate(answers(result).items(), start=1):
        w(f"  {i}. {question}")
        for line in answer:
            w(f"     {line}")
    w("")
    for note in (CANDLE_HAS_NO_BOOK, SAME_BAR_TIE_IS_A_LOSS,
                 COST_GATE_IS_NOT_A_PROBABILITY, FAMILIES_NOT_DISCOVERIES,
                 NO_OPTION_CLAIM, NOT_A_PREDICTION, NO_ORDER_PATH):
        w(f"  {note}")
    return "\n".join(out)


def _instrument_verdict(inst: dict) -> list[str]:
    """One instrument's answer, in its own numbers."""
    if not inst["eligible"]:
        return [f"not searched: {inst['eligibility'].get('reason')}"]
    rows = rank(inst["rows"])
    if not rows:
        return ["no parameterization produced an event"]
    best = rows[0]
    d = best["per_partition"][DISCOVERY]
    v = best["per_partition"][VALIDATION]
    lines = [
        f"{inst['totals']['UNIQUE_EVENT_FAMILIES']} event families, "
        f"{best['trades']} trades at the best-ranked parameterization, "
        f"{d['trade_count']} of them in discovery and {v['trade_count']} in "
        f"validation",
        f"best-ranked status {best['final_status']}; "
        f"robust candidates {inst['totals']['ROBUST_CANDIDATES']}",
    ]
    if d.get("measured"):
        lines.append(
            f"discovery net {d['net_expectancy_r']:+.3f}R, validation net "
            f"{_r(v.get('net_expectancy_r')).strip()}R"
        )
    lines.extend(f"limit: {r}" for r in best["status_reasons"])
    return lines


def answers(result: dict) -> dict[str, list[str]]:
    """§18's eight questions, answered from the run's own numbers."""
    t = result["totals"]
    by_name = {i["instrument"]: i for i in result["instruments"]}
    robust = t["ROBUST_CANDIDATES"]

    all_rows = [r for i in result["instruments"] for r in i["rows"]]
    measured = [r for r in all_rows if r["overall"].get("measured")]
    gross_positive = [
        r for r in measured
        if (r["overall"].get("gross_expectancy_points") or 0.0) > 0
    ]
    net_positive = [
        r for r in measured
        if (r["overall"].get("net_expectancy_points") or 0.0) > 0
    ]
    readings = sorted({r["effect_reading"] for r in measured})
    holdout_seen = [
        r for r in all_rows
        if r["per_partition"][UNTOUCHED_HOLDOUT]["trade_count"] > 0
    ]

    out: dict[str, list[str]] = {}
    out["Does gap failure -> range re-entry -> reversion work?"] = (
        [f"No robust candidate. {robust} of {t['TOTAL_HYPOTHESES']} "
         f"parameterizations ({t['UNIQUE_EVENT_FAMILIES']} event families) "
         f"cleared the declared bar."]
        if robust == 0 else
        [f"{robust} parameterization(s) cleared every declared gate. That is a "
         f"historical measurement on modelled fills, not a prediction."]
    )
    for name in ("CRUDEOIL", "NIFTY"):
        if name in by_name:
            out[f"Does it work on {name}?"] = _instrument_verdict(by_name[name])
    for name, inst in by_name.items():
        if name not in ("CRUDEOIL", "NIFTY"):
            out[f"Does it work on {name}?"] = _instrument_verdict(inst)

    out["Does it remain positive after realistic costs?"] = [
        f"{len(gross_positive)} of {len(measured)} measured parameterizations "
        f"are positive before cost and {len(net_positive)} after it",
        *(
            [
                "median share of the gross move consumed by the modelled round "
                "trip: "
                + ", ".join(
                    f"{r['instrument']} {r['overall']['cost_share_of_gross_pct']:.0f}%"
                    for r in sorted(
                        {r["instrument"]: r for r in rank(measured)}.values(),
                        key=lambda r: r["instrument"],
                    )
                    if r["overall"].get("cost_share_of_gross_pct") is not None
                )
            ] if measured else []
        ),
        "every net figure is still optimistic by at least one unmeasured spread",
    ]
    out["Does it survive untouched holdout?"] = [
        f"{t['HOLDOUT_POSITIVE']} parameterizations are positive in the "
        f"untouched holdout above its declared trade floor",
        f"{len(holdout_seen)} parameterizations placed any trade in the holdout "
        f"at all",
    ]
    out["Is the effect stable across years?"] = [
        f"readings observed across measured parameterizations: "
        f"{', '.join(readings) if readings else 'none — nothing was measurable'}",
        "per-year and per-quarter tables are printed above for the best-ranked "
        "row of each instrument",
    ]
    out["Is the edge directional or merely a cost artifact?"] = [
        f"{len(gross_positive)} parameterizations show a positive gross edge; "
        f"{len(net_positive)} keep it after cost",
        "a positive net with a non-positive gross would be the cost gate "
        "selecting trades rather than the direction working; the §15 reading "
        "names which case each row is",
    ]
    out["Is this suitable for live executable-book paper validation?"] = (
        ["No. Nothing cleared the bar, so there is nothing to validate on a "
         "live book, and the event rate measured here is the reason to say so "
         "plainly rather than to keep searching."]
        if robust == 0 else
        ["Only as a paper-only, record-first observation with a pre-declared "
         "sample floor, because the historical fill is modelled and the option "
         "vehicle question is entirely unmeasured here."]
    )
    return out


def write_artefacts(result: dict, *, text: str) -> list[str]:
    """Immutable, uniquely timestamped artefacts. Nothing is overwritten."""
    ARTEFACT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    fp = fingerprint()
    written: list[str] = []

    def put(name: str, payload: str) -> None:
        path = ARTEFACT_DIR / f"phase52_{name}_{fp}_{stamp}.{'txt' if name == 'report' else 'json'}"
        path.write_text(payload)
        written.append(str(path))

    put("report", text)
    put("preregistration", json.dumps(preregistration(), indent=2, sort_keys=True))
    put("totals", json.dumps(result["totals"], indent=2, sort_keys=True))
    put("answers", json.dumps(answers(result), indent=2, sort_keys=True))
    put("table", json.dumps([
        {
            "candidate_id": r["candidate_id"],
            "mechanism_family": r["mechanism_family"],
            "human_readable_rule": r["human_readable_rule"],
            "instrument": r["instrument"],
            "entry_rule": r["entry_rule"],
            "exit_rule": r["exit_rule"],
            "event_family": r["event_family"],
            "discovery_result": r["per_partition"][DISCOVERY],
            "validation_result": r["per_partition"][VALIDATION],
            "untouched_holdout_result": r["per_partition"][UNTOUCHED_HOLDOUT],
            "net_expectancy_r": r["overall"].get("net_expectancy_r"),
            "profit_factor": r["overall"].get("profit_factor"),
            "drawdown_r": r["overall"].get("max_drawdown_r"),
            "trade_count": r["overall"].get("trade_count"),
            "session_count": r["overall"].get("session_count"),
            "cost_stress": r["cost_stress"],
            "effect_reading": r["effect_reading"],
            "overfit_status": r["overfit_status"],
            "final_status": r["final_status"],
            "status_reasons": r["status_reasons"],
        }
        for inst in result["instruments"] for r in rank(inst["rows"])
    ], indent=2, sort_keys=True))
    put("funnels", json.dumps(
        {i["instrument"]: i.get("funnels", {}) for i in result["instruments"]},
        indent=2, sort_keys=True,
    ))
    return written
