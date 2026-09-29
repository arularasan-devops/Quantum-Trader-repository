"""Artefacts (§32-§35). Every number printed here is traceable to one run.

The report is written so that a reader who wants to disbelieve it can: the
pre-registration fingerprint, the full registered denominator, the partition
boundaries by date, the declared limitations and the named gate failures for
every row are all printed, not just the surviving rows. A table of only the
winners is how a grid search gets mistaken for a discovery.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import (
    COST_LABEL,
    DATA_LABEL,
    EVIDENCE_CEILING,
    FORBIDDEN_LABELS,
    MARKET_CONTEXT_LABEL,
    PREREG,
    ROBUST_CANDIDATE,
    SECTOR_CAP_STATUS,
    UNIVERSE_LABEL,
    VERSION,
)
from .engine import DISCOVERY, HOLDOUT, VALIDATION

HEADER = "=" * 78


def rank_key(row: dict) -> tuple:
    """Ranking is by out-of-sample net expectancy, never by win rate (§15)."""
    holdout = row["partitions"][HOLDOUT]
    validation = row["partitions"][VALIDATION]
    return (
        row["status"] == ROBUST_CANDIDATE,
        min(validation.get("net_expectancy", -1.0), holdout.get("net_expectancy", -1.0)),
        row["partitions"][DISCOVERY].get("net_expectancy", -1.0),
    )


def _table(rows: list[dict], limit: int) -> list[str]:
    lines = [
        f"{'ENTRY':<26} {'EXIT':<18} {'TRD':>6} {'NET%':>8} {'PF':>6} "
        f"{'WIN%':>6} {'VAL%':>8} {'OOS%':>8} {'DDu':>6} {'STATUS':<22}"
    ]
    for row in sorted(rows, key=rank_key, reverse=True)[:limit]:
        discovery = row["partitions"][DISCOVERY]
        validation = row["partitions"][VALIDATION]
        holdout = row["partitions"][HOLDOUT]
        lines.append(
            f"{row['entry']:<26} {row['exit']:<18} "
            f"{discovery.get('trades', 0):>6} "
            f"{100 * discovery.get('net_expectancy', 0.0):>8.3f} "
            f"{min(discovery.get('profit_factor', 0.0), 99.9):>6.2f} "
            f"{100 * discovery.get('win_rate', 0.0):>6.1f} "
            f"{100 * validation.get('net_expectancy', 0.0):>8.3f} "
            f"{100 * holdout.get('net_expectancy', 0.0):>8.3f} "
            f"{discovery.get('trade_sequence_drawdown_units', 0.0):>6.1f} "
            f"{row['status']:<22}"
        )
    return lines


def write(root: Path, result: dict) -> dict:
    """Write every artefact and return the paths."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    paths = {}

    paths["prereg"] = _dump(root / "prereg.json", {**PREREG.as_dict(), "fingerprint": PREREG.fingerprint()})
    paths["study_rows"] = _dump(root / "study_rows.json", result["rows"])
    paths["fdr"] = _dump(root / "fdr.json", result["fdr"])
    paths["event_families"] = _dump(root / "event_families.json", result["event_families"])
    paths["per_instrument"] = _dump(root / "per_instrument.json", result.get("per_instrument", {}))
    paths["portfolio"] = _dump(root / "portfolio.json", result.get("portfolio", {}))
    paths["benchmark"] = _dump(root / "benchmark.json", result.get("benchmark", {}))
    paths["verdict"] = _dump(
        root / "verdict.json",
        {
            "version": VERSION,
            "fingerprint": PREREG.fingerprint(),
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "status_counts": result["verdict"]["status_counts"],
            "robust_candidates": result["verdict"]["robust_candidates"],
            "evidence_ceiling": EVIDENCE_CEILING,
            "limitations": list(PREREG.limitations),
        },
    )
    text = report_text(result)
    (root / "study_report.txt").write_text(text)
    paths["study_report"] = str(root / "study_report.txt")
    for label in FORBIDDEN_LABELS:
        if label in text:
            raise AssertionError(f"forbidden label {label} in report")
    return paths


def _dump(path: Path, payload) -> str:
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str))
    return str(path)


def _mechanism_detail(rows: list[dict], result: dict) -> list[str]:
    """Cost, stress, path and fold detail for the rows a reader would chase."""
    lines = [
        "",
        "MECHANISM DETAIL OF THE TOP ROWS (§21, §24, §25, §29)",
        "  GROSS/NET are discovery expectancy per trade before and after modelled",
        "  costs; COSTx is net expectancy at 1x/1.5x/2x those costs. MFE/MAE are",
        "  median favourable and adverse excursions over the holding window.",
    ]
    for row in sorted(rows, key=rank_key, reverse=True)[:10]:
        discovery = row["partitions"][DISCOVERY]
        stress = row["cost_stress"]
        walk = row.get("walk_forward", {})
        lines.append(f"  {row['entry']} / {row['exit']}  ({row['entry_family']})")
        lines.append(
            f"      gross {100 * discovery.get('gross_expectancy', 0.0):>7.3f}%   "
            f"net {100 * discovery.get('net_expectancy', 0.0):>7.3f}%   "
            f"cost drag {100 * discovery.get('cost_drag', 0.0):>6.3f}%   "
            f"cost consumes {100 * discovery.get('cost_consumption', 0.0):>6.1f}% of gross"
        )
        lines.append(
            "      cost stress  "
            + "  ".join(
                f"{name} {100 * block.get('net_expectancy', 0.0):>7.3f}%"
                for name, block in sorted(stress.items())
            )
        )
        lines.append(
            f"      MFE {100 * (row.get('mfe_median') or 0.0):>6.2f}%   "
            f"MAE {100 * (row.get('mae_median') or 0.0):>6.2f}%   "
            f"median hold {row.get('median_holding_sessions')} sessions   "
            f"unresolved {row.get('unresolved_trades', 0)}"
        )
        lines.append(
            f"      walk-forward positive in {walk.get('positive_folds', 0)} of "
            f"{walk.get('graded_folds', 0)} graded folds   "
            + "  ".join(
                f"f{fold['fold']}={100 * fold['net_expectancy']:.2f}%"
                for fold in walk.get("folds", [])
            )
        )
        lines.append(
            "      exits  "
            + "  ".join(f"{name}={count}" for name, count in sorted(row["exit_reasons"].items()))
        )
        per_year = discovery.get("per_year", {})
        if per_year:
            lines.append(
                "      by year  "
                + "  ".join(
                    f"{year}={100 * block.get('net_expectancy', 0.0):.2f}%/{block.get('trades', 0)}"
                    for year, block in sorted(per_year.items())
                )
            )
        stock = discovery.get("stock_concentration", {})
        year = discovery.get("year_concentration", {})
        lines.append(
            f"      concentration  top stock {stock.get('top_key', '')} "
            f"{100 * stock.get('top_share', 0.0):.1f}% of net profit over "
            f"{stock.get('distinct_keys', 0)} stocks   "
            f"top year {year.get('top_key', '')} {100 * year.get('top_share', 0.0):.1f}%"
        )
        lines.append(
            f"      breadth {row['replication_breadth']}   "
            f"sector cap {SECTOR_CAP_STATUS}"
        )
    return lines


def _replication(result: dict) -> list[str]:
    """Per-instrument replication: a mechanism that works on a handful of names
    is not a mechanism, so the spread across securities is printed, not pooled away.
    """
    detail = result.get("per_instrument", {})
    if not detail:
        return []
    lines = [
        "",
        "PER-INSTRUMENT REPLICATION (§26)",
        f"  {'ENTRY|EXIT':<44} {'NAMES':>6} {'POS%':>6} {'MEDIAN%':>8} {'WORST%':>8} {'BEST%':>8}",
    ]
    for label, block in detail.items():
        securities = block.get("securities", {})
        values = [
            float(stats.get("net_expectancy", 0.0))
            for stats in securities.values()
            if stats.get("trades", 0) > 0
        ]
        if not values:
            lines.append(f"  {label:<44} no security has a resolved trade")
            continue
        values.sort()
        positive = sum(1 for value in values if value > 0)
        median = values[len(values) // 2]
        lines.append(
            f"  {label:<44} {len(values):>6} {100 * positive / len(values):>6.1f} "
            f"{100 * median:>8.3f} {100 * values[0]:>8.2f} {100 * values[-1]:>8.2f}"
        )
    return lines


def _six_questions(rows: list[dict], verdict: dict) -> list[str]:
    """The six operational questions, answered only from what was measured.

    With no promoted row there is exactly one honest answer to the first five,
    and it is NO_TRADE. Naming the best-ranked row here is deliberate: it is the
    row a reader would otherwise be tempted to trade, so its status is stated
    beside it.
    """
    ranked = sorted(rows, key=rank_key, reverse=True)
    best = ranked[0] if ranked else None
    lines = ["", "THE SIX OPERATIONAL QUESTIONS"]
    if verdict["robust_candidates"] == 0:
        label = f"{best['entry']} / {best['exit']} ({best['status']})" if best else "none"
        lines += [
            "  1. which share      NONE. No registered row cleared the promotion gates.",
            "  2. when to buy      NEVER, on this evidence.",
            "  3. entry price      NOT SPECIFIED. No row earned an entry rule.",
            "  4. invalidation     NOT SPECIFIED, for the same reason.",
            "  5. when to sell     NOT SPECIFIED, for the same reason.",
            "  6. when NO_TRADE    ALWAYS, until a row is promoted. The scanner is",
            "                      fail-closed and returns NO_TRADE by construction.",
            f"  best-ranked row    {label} — ranked, not recommended.",
        ]
        return lines
    for row in (row for row in ranked if row["status"] == ROBUST_CANDIDATE):
        config = row["exit_config"]
        lines += [
            f"  {row['entry']} / {row['exit']}",
            f"      1. which share   any name eligible under {UNIVERSE_LABEL} whose",
            f"                       prior sessions satisfy {row['entry']} "
            f"{row['entry_params']}",
            "      2. when to buy   the session after the condition is true on",
            "                       completed prior sessions only",
            "      3. entry price   that session's open",
            f"      4. invalidation  {config.get('stop_atr') and str(config['stop_atr']) + ' ATR below entry' or 'previous session low'}",
            f"      5. when to sell  {config.get('target_atr') and str(config['target_atr']) + ' ATR target, else ' or ''}"
            f"time exit at {config['hold']} sessions",
            "      6. NO_TRADE      when no eligible name satisfies the condition,",
            "                       or the book already holds its maximum positions",
        ]
    return lines


def report_text(result: dict) -> str:
    rows = result["rows"]
    verdict = result["verdict"]
    lines = [
        HEADER,
        "PHASE 56 STAGE 3 — NSE CASH-EQUITY MECHANISM STUDY (LONG ONLY, PAPER ONLY)",
        HEADER,
        f"version            {VERSION}",
        f"prereg fingerprint {PREREG.fingerprint()}",
        f"data               {DATA_LABEL}",
        f"universe           {UNIVERSE_LABEL}",
        f"market context     {MARKET_CONTEXT_LABEL}",
        f"costs              {COST_LABEL}",
        f"sector cap         {SECTOR_CAP_STATUS}",
        f"evidence ceiling   {EVIDENCE_CEILING}",
        "",
        f"dataset            {result['dataset']['sessions']} sessions, "
        f"{result['dataset']['symbols']} securities, "
        f"{result['dataset']['from']}..{result['dataset']['to']}",
        "",
        "PARTITIONS (chronological, never shuffled)",
    ]
    for name in (DISCOVERY, VALIDATION, HOLDOUT):
        block = result["partitions"][name]
        lines.append(f"  {name:<20} {block['sessions']:>5} sessions  {block['from']}..{block['to']}")
    lines += [
        "",
        "HYPOTHESIS ACCOUNTING (§19)",
        f"  registered entries          {len(PREREG.entry_grid)}",
        f"  registered exits            {len(PREREG.exit_grid)}",
        f"  registered hypotheses       {result['fdr']['denominator']}",
        f"  unique event families       {result['event_families']['unique_event_families']}",
        f"  duplicate entry groups      {len(result['event_families']['duplicate_groups'])}",
        f"  rows with gradeable sample  {result['fdr']['gradeable_rows']}",
        f"  BH alpha                    {result['fdr']['alpha']}",
        f"  smallest discovery p-value  {result['fdr']['min_p_value']:.6g}",
        f"  FDR survivors               {result['fdr']['fdr_survivors']}",
        "",
        "STATUS COUNTS (§31 gates, one status per registered row)",
    ]
    for status, count in verdict["status_counts"].items():
        lines.append(f"  {status:<24} {count:>5}")
    lines += [
        "",
        f"ROBUST CANDIDATES: {verdict['robust_candidates']}",
        "",
        "RANKED BY OUT-OF-SAMPLE NET EXPECTANCY",
        "  NET%/VAL%/OOS% are net expectancy per trade on discovery / validation /",
        "  untouched holdout. DDu is the additive per-position trade-sequence drawdown",
        "  in units of one full position — NOT a book drawdown, because these trades",
        "  overlap. The book drawdown is the portfolio section below, and it is the",
        "  number the promotion gate reads.",
        "",
    ]
    lines += _table(rows, 40)

    benchmark = result.get("benchmark", {})
    if benchmark:
        lines += ["", "BENCHMARK (§17, diagnostic only — not the question being asked)"]
        for name, block in benchmark.items():
            if block.get("status") == "BUY_AND_HOLD":
                lines.append(
                    f"  {name:<12} net CAGR {100 * block['net_cagr']:>7.2f}%  "
                    f"max DD {100 * block['max_drawdown']:>6.2f}%  sessions {block['sessions']}"
                )
            else:
                lines.append(f"  {name:<12} {block.get('status')}")

    portfolio = result.get("portfolio", {})
    if portfolio:
        lines += [
            "",
            "PORTFOLIO LAYER (§13, long only, cash only, no leverage)",
            "  Each row is one declared book: entry|exit|book size|sizing rule, run on",
            "  the stated capital with candidates rationed deterministically when more",
            "  names signal than the book can hold. The promotion gate reads the WORST",
            "  drawdown across a mechanism's declared books, so a mechanism is judged by",
            "  the sizing it also permits, not only by its most flattering one. Compare",
            "  every CAGR here against the benchmark block above before reading any of",
            "  them as an edge.",
        ]
        for key, block in portfolio.items():
            if "net_cagr" not in block:
                continue
            lines.append(
                f"  {key:<44} CAGR {100 * block['net_cagr']:>7.2f}%  DD {100 * block['max_drawdown']:>6.2f}%  "
                f"trades {block['trades']:>5}  rationed {block['rationed_candidates']:>6}"
            )
        graded = [(key, block) for key, block in portfolio.items() if "net_cagr" in block]
        if graded:
            best_key, best = max(graded, key=lambda item: item[1]["net_cagr"])
            lines += [
                "",
                f"  best book by net CAGR: {best_key}",
                f"      CAGR {100 * best['net_cagr']:.2f}%  DD {100 * best['max_drawdown']:.2f}%  "
                f"worst month {100 * best['worst_month']:.2f}%  trades {best['trades']}  "
                f"rationed {best['rationed_candidates']}",
                "      Read this one with suspicion, not enthusiasm: it is the best of",
                "      many books over one history, and the rationed count says most",
                "      signalling names were never bought — so the result depends on the",
                "      rationing order as much as on the mechanism.",
            ]

    lines += _mechanism_detail(rows, result)
    lines += _replication(result)

    lines += ["", "GATE FAILURES OF THE TOP ROWS (why each one is not promoted)"]
    for row in sorted(rows, key=rank_key, reverse=True)[:10]:
        failures = ", ".join(row["gate_failures"]) or "NONE"
        lines.append(f"  {row['entry']} / {row['exit']}: {row['status']}")
        lines.append(f"      {failures}")

    check = result.get("selection_self_check")
    if check:
        lines += [
            "",
            "SELECTION SELF-CHECK (§18)",
            f"  selected by discovery+validation only: {len(check['selected_without_holdout'])}",
            f"  promoted after holdout:                {len(check['promoted'])}",
            f"  promoted is a subset of the selection: {check['promoted_subset_of_selection']}",
        ]

    lines += _six_questions(rows, verdict)

    lines += ["", "DECLARED LIMITATIONS (§33)"]
    lines += [f"  - {item}" for item in PREREG.limitations]
    lines += [
        "",
        HEADER,
        "This is historical evidence only. It cannot authorise a production signal,",
        "and nothing here places an order. A surviving row is a candidate for live",
        "executable-book paper validation, not a strategy.",
        HEADER,
    ]
    return "\n".join(lines) + "\n"
