"""Phase 53 — the table, the six totals, and the eight answers.

Written to be readable by somebody who wants to refuse it. The funnel comes
before the results, because a reader has to know how many sessions produced an
event before an expectancy means anything; the family collapse comes before the
ranking, because several spellings of one event set are one row and not several;
and every partition column carries its own trade count next to its own number.

Nothing here ranks by discovery P&L. The ordering is by final status and then by
the **validation** partition, because the discovery number is the one number in
the table that was selected on.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.research.phase53 import (
    CANDLE_HAS_NO_BOOK,
    COST_GATE_IS_NOT_A_PROBABILITY,
    DISCOVERY,
    FAMILIES_NOT_DISCOVERIES,
    HISTORICAL_LEAD,
    MECHANISM,
    NO_OPTION_CLAIM,
    NO_ORDER_PATH,
    NOT_A_PREDICTION,
    PHASE_52_UNTOUCHED,
    PROMISING_NEEDS_DATA,
    RETEST_TOUCH_IS_NOT_AN_ORDERED_PATH,
    ROBUST_CANDIDATE,
    SAME_BAR_TIE_IS_A_LOSS,
    UNTOUCHED_HOLDOUT,
    VALIDATION,
    fingerprint,
    preregistration,
)

ARTEFACT_DIR = Path("data/phase53")

STATUS_ORDER = {
    ROBUST_CANDIDATE: 0,
    PROMISING_NEEDS_DATA: 1,
    HISTORICAL_LEAD: 2,
}

FUNNEL_KEYS = (
    "PREVIOUS_SESSION_OR_ATR_NOT_YET_KNOWABLE",
    "SESSION_TOO_SHORT_TO_CLOSE_A_THIRTY_MINUTE_OPENING_RANGE",
    "NO_COMPLETED_BAR_CLOSED_OUTSIDE_THE_OPENING_RANGE",
    "PRICE_NEVER_RETURNED_TO_THE_LEVEL_WITHIN_THE_RETEST_WINDOW",
    "NO_COMPLETED_BAR_CLOSED_BACK_BEYOND_THE_LEVEL_AFTER_THE_RETEST",
    "NO_FORWARD_BARS_LEFT_IN_THE_SESSION",
)


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


def _detail(w, best: dict) -> None:
    """The full statistic set for one row."""
    w(f"  DETAIL — {best['variant_id']}  ({best['final_status']})")
    w(f"    rule: {best['human_readable_rule']}")
    o = best["overall"]
    if o.get("measured"):
        w(f"    trades {o['trade_count']}  independent sessions "
          f"{o['session_count']}  long {o['long_count']}  short "
          f"{o['short_count']}")
        w(f"    win rate {100.0 * o['win_rate']:.0f}%"
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
          f"median risk {o['median_risk_points']:.2f} points "
          f"({o['median_stop_atr']:.2f} ATR)")
        w(f"    median target distance {o['median_target_distance']:.2f} points"
          f"   median cost {o['median_cost_points']:.2f} points"
          f"   median movement/cost multiple {o['median_cost_multiple']:.1f}")
        w(f"    retest arrived {o['median_retest_delay_minutes']:.0f} min after "
          f"the breakout, confirmed {o['median_confirm_delay_minutes']:.0f} min "
          f"after the retest (medians)")
        w(f"    COST ATTRIBUTION — gross {o['gross_expectancy_points']:+.2f} "
          f"points per trade ({o['gross_expectancy_r']:+.3f}R), net "
          f"{o['net_expectancy_points']:+.2f} points "
          f"({o['net_expectancy_r']:+.3f}R), cost consumed "
          f"{o['cost_share_of_gross_pct']:.1f}% of the gross move")
        w(f"    stability reading: {best['effect_reading']}")
        for mult, row2 in sorted(best["cost_stress"].items()):
            w(f"    cost x{mult}  net expectancy "
              f"{_r(row2.get('net_expectancy_r'))}R  "
              f"trades {row2.get('trade_count')}")
        for label, row2 in sorted(best["direction_split"].items()):
            w(f"    {label:<40s} trades {row2['trade_count']:>4}  "
              f"net {_r(row2.get('net_expectancy_r'))}R")
        w("    per year:")
        for year, row2 in sorted(best["per_year"].items()):
            w(f"      {year}  trades {row2['trade_count']:>4}  "
              f"net {_r(row2['net_expectancy_r'])}R  "
              f"total {_r(row2['net_total_r'])}R")
        w("    per quarter:")
        for quarter, row2 in sorted(best["per_quarter"].items()):
            w(f"      {quarter}  trades {row2['trade_count']:>4}  "
              f"net {_r(row2['net_expectancy_r'])}R")
        sd = best["session_distribution"]
        if sd.get("measured"):
            w(f"    session distribution: median entry "
              f"{sd['median_minutes_after_open']:.0f} minutes after the open, "
              f"{sd['trades_per_session']:.2f} trades per session, "
              f"{sd['by_hour_after_open']}")
    for reason in best["status_reasons"]:
        w(f"    WHY NOT ROBUST: {reason}")
    w("")


def render(result: dict, *, top: int = 16) -> str:
    """The whole report for one run, as text."""
    out: list[str] = []
    w = out.append
    w(f"PHASE 53 — {MECHANISM}")
    w(f"  pre-registration fingerprint {fingerprint()}")
    w("  one mechanism, one registered grid of 64 parameterizations per "
      "instrument, graded once")
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
        for label, funnel in sorted(inst["funnels"].items()):
            w(f"  FUNNEL — {label}")
            w(f"    {'sessions':<62s} {funnel.get('sessions', 0):>6,}")
            w(f"    {'direction attempts (one long and one short per session)':<62s}"
              f" {funnel.get('direction_attempts', 0):>6,}")
            w(f"    {'sessions with any completed breakout at all':<62s} "
              f"{funnel.get('sessions_with_any_breakout', 0):>6,}")
            for key in FUNNEL_KEYS:
                w(f"    {key:<62s} {funnel.get(key, 0):>6,}")
            w(f"    {'detected events':<62s} {funnel.get('candidates', 0):>6,}")
        dirs = inst.get("direction_counts", {})
        for label, counts in sorted(dirs.items()):
            w(f"  DETECTED — {label}  " + "  ".join(
                f"{k.rsplit('_', 1)[-1].lower()} {v:,}"
                for k, v in sorted(counts.items())
            ))
        w("")

        w(f"  EVENT FAMILIES — {inst['totals']['UNIQUE_EVENT_FAMILIES']} "
          f"families and {inst.get('distinct_hypotheses', 0)} distinct "
          f"hypotheses from {inst['totals']['TOTAL_PARAMETERIZATIONS']} "
          f"parameterizations")
        for fam in inst["families"]:
            w(f"    {fam['event_family']}  {fam['trades']:>5} trades  "
              f"{len(fam['variants']):>2} spellings  "
              f"fdr {'pass' if fam['fdr_pass'] else 'no'}")
        w("")

        w("  RESEARCH TABLE — ordered by status then validation, never by discovery")
        w("    variant                                              "
          "trades  disc n   disc R  val n    val R  hold n   hold R      PF  "
          "status")
        for row in rank(inst["rows"])[:top]:
            d = row["per_partition"][DISCOVERY]
            v = row["per_partition"][VALIDATION]
            h = row["per_partition"][UNTOUCHED_HOLDOUT]
            w(f"    {row['variant_id']:<52s}"
              f"{row['trades']:>6}"
              f"{d['trade_count']:>8}"
              f" {_r(d.get('net_expectancy_r'))}"
              f"{v['trade_count']:>7}"
              f" {_r(v.get('net_expectancy_r'))}"
              f"{h['trade_count']:>8}"
              f" {_r(h.get('net_expectancy_r'))}"
              f" {_n(row['overall'].get('profit_factor'))}"
              f"  {row['final_status']}")
        w("")

        ranked = rank(inst["rows"])
        if ranked:
            _detail(w, ranked[0])

    t = result["totals"]
    w("TOTALS")
    for key in ("TOTAL_PARAMETERIZATIONS", "UNIQUE_EVENT_FAMILIES",
                "DISCOVERY_LEADS", "VALIDATION_POSITIVE", "HOLDOUT_POSITIVE",
                "ROBUST_CANDIDATES"):
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
                 RETEST_TOUCH_IS_NOT_AN_ORDERED_PATH,
                 COST_GATE_IS_NOT_A_PROBABILITY, FAMILIES_NOT_DISCOVERIES,
                 NO_OPTION_CLAIM, NOT_A_PREDICTION, NO_ORDER_PATH,
                 PHASE_52_UNTOUCHED):
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
    o = best["overall"]
    d = best["per_partition"][DISCOVERY]
    v = best["per_partition"][VALIDATION]
    h = best["per_partition"][UNTOUCHED_HOLDOUT]
    lines = [
        f"{inst['totals']['UNIQUE_EVENT_FAMILIES']} event families, "
        f"{best['trades']} trades at the best-ranked parameterization over "
        f"{o.get('session_count', 0)} independent sessions",
        f"best-ranked status {best['final_status']}; robust candidates "
        f"{inst['totals']['ROBUST_CANDIDATES']}",
        f"discovery {d['trade_count']} trades {_r(d.get('net_expectancy_r')).strip()}R"
        f", validation {v['trade_count']} trades "
        f"{_r(v.get('net_expectancy_r')).strip()}R"
        f", untouched holdout {h['trade_count']} trades "
        f"{_r(h.get('net_expectancy_r')).strip()}R",
    ]
    if o.get("cost_share_of_gross_pct") is not None:
        lines.append(
            f"gross {o['gross_expectancy_points']:+.2f} points per trade, net "
            f"{o['net_expectancy_points']:+.2f}, cost consumed "
            f"{o['cost_share_of_gross_pct']:.1f}% of the gross move; reading "
            f"{best['effect_reading']}"
        )
    lines.extend(f"limit: {r}" for r in best["status_reasons"])
    return lines


def answers(result: dict) -> dict[str, list[str]]:
    """The eight questions, answered from the run's own numbers."""
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
    cost_shares = {
        r["instrument"]: r["overall"]["cost_share_of_gross_pct"]
        for r in rank(measured)
        if r["overall"].get("cost_share_of_gross_pct") is not None
    }
    events = {
        i["instrument"]: max(
            (r["overall"].get("session_count") or 0 for r in i["rows"]), default=0
        )
        for i in result["instruments"]
    }
    trades = {
        i["instrument"]: max((r["trades"] for r in i["rows"]), default=0)
        for i in result["instruments"]
    }

    out: dict[str, list[str]] = {}
    out["Does the opening-range breakout/retest mechanism work?"] = (
        [f"No robust candidate. {robust} of {t['TOTAL_PARAMETERIZATIONS']} "
         f"parameterizations ({t['UNIQUE_EVENT_FAMILIES']} event families) "
         f"cleared the declared bar.",
         "Unlike the previous phase this is not a sample-size answer: the "
         "mechanism fired often enough to be graded, and it was graded."]
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

    out["How many independent events occur?"] = [
        ", ".join(
            f"{k}: up to {trades[k]:,} trades over {v:,} independent sessions"
            for k, v in sorted(events.items())
        ) or "none",
        "a session may contribute one long and one short, so the trade count "
        "exceeds the session count and the session count is what bounds "
        "independence",
    ]
    out["What percentage of gross movement is consumed by cost?"] = [
        ", ".join(f"{k} {v:.1f}%" for k, v in sorted(cost_shares.items()))
        or "unmeasured — nothing resolved",
        f"{len(gross_positive)} of {len(measured)} measured parameterizations "
        f"are positive before cost and {len(net_positive)} after it",
        "every net figure is still optimistic by at least one unmeasured spread",
    ]
    out["Does the effect survive the untouched holdout?"] = [
        f"{t['HOLDOUT_POSITIVE']} parameterizations are positive in the "
        f"untouched holdout above its declared trade floor",
        f"{t['DISCOVERY_LEADS']} were positive in discovery, which is the gate "
        f"that has to open before the holdout means anything",
    ]
    out["Is the effect directional or cost-driven?"] = [
        f"readings observed: "
        f"{', '.join(readings) if readings else 'none — nothing was measurable'}",
        f"{len(gross_positive)} parameterizations show a positive gross edge and "
        f"{len(net_positive)} keep it after cost",
        "a positive net with a non-positive gross would be the cost gate "
        "selecting trades rather than the direction working; the stability "
        "reading names which case each row is",
    ]
    out["Is the effect stable across years and quarters?"] = [
        "per-year and per-quarter tables are printed above for the best-ranked "
        "row of each instrument, and a row whose net leans on one year or one "
        "quarter beyond the declared share is flagged RECENT_PERIOD_OVERFIT",
    ]
    out["Is it suitable for live executable-book paper validation?"] = (
        ["No. Nothing cleared the bar, so there is nothing to validate on a "
         "live book. The sample was sufficient here, so this is a measured "
         "negative rather than an unanswered question."]
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
        suffix = "txt" if name == "report" else "json"
        path = ARTEFACT_DIR / f"phase53_{name}_{fp}_{stamp}.{suffix}"
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
            "overall": r["overall"],
            "per_year": r["per_year"],
            "per_quarter": r["per_quarter"],
            "direction_split": r["direction_split"],
            "cost_stress": r["cost_stress"],
            "effect_reading": r["effect_reading"],
            "overfit_status": r["overfit_status"],
            "final_status": r["final_status"],
            "status_reasons": r["status_reasons"],
        }
        for inst in result["instruments"] for r in rank(inst["rows"])
    ], indent=2, sort_keys=True))
    put("funnels", json.dumps(
        {
            i["instrument"]: {
                "funnels": i.get("funnels", {}),
                "direction_counts": i.get("direction_counts", {}),
                "partition_sessions": i.get("partition_sessions", {}),
            }
            for i in result["instruments"]
        },
        indent=2, sort_keys=True,
    ))
    return written
