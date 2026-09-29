"""Do the research labels predict anything — Phase 12A §8, §9, §11, §13.

Phase 12 produced four labels beside every option call: tradability
(TRADABLE/CAUTION/UNTRADABLE), the A+ shadow label, the entry-quality state and
the instrument family. None of them gates a production BUY. This module answers
the only question that could ever justify promoting one of them: **when a label
was attached, what actually happened to that trade?**

The join is signal_id → resolved outcome, so a label is only ever scored against
the trade it was attached to at signal time. Two rules make the numbers honest:

* **net R charges that call's own recorded spread** plus the same round-trip cost
  model the board applies. A call with no quoted book gets no net number rather
  than a gross one relabelled net — the UNTRADABLE cohort is exactly the cohort
  most likely to be missing a book, so pooling gross into net would flatter it.
* **cohorts are reported with their sample size and a status**, never as a bare
  percentage. Below ``MIN_COHORT`` a cohort reads INSUFFICIENT_SAMPLE. Above it,
  it reads OBSERVATION on one session and IN_SAMPLE_ONLY until §19's 20 sessions
  with 5 chronological holdout sessions exist. Nothing here ever reads VALIDATED.

Reason cohorts overlap on purpose: one UNTRADABLE call can be wide *and* thin
*and* stale, and forcing a single cause on it would invent a ranking the data
does not contain.

Research only. No output of this module gates, blocks, sizes, ranks or suppresses
a signal, and none of it reaches an order path.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.analysis import a_plus_shadow, entry_quality
from app.analysis import instrument_family as fam
from app.analysis import instrument_studies, r_integrity, tradability
from app.market.instruments import REGISTRY

REPORT_JSON = "label_outcomes.json"

# Below this a cohort is reported but never compared: 3 fills are not a verdict.
MIN_COHORT = 10
# §19 sufficiency, restated here so a reader of one artefact sees the gate.
MIN_SESSIONS = 20
MIN_HOLDOUT_SESSIONS = 5

INSUFFICIENT = "INSUFFICIENT_SAMPLE"
OBSERVATION = "OBSERVATION"
IN_SAMPLE_ONLY = "IN_SAMPLE_ONLY"


def _num(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 4)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _share(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def _labels(row: dict) -> dict:
    """Every label recorded beside one journalled option call, plus its book."""
    trad = row.get("tradability") or {}
    eq = row.get("entry_quality") or {}
    grade = row.get("a_plus_shadow") or {}
    state = row.get("market_state") or {}
    plan = row.get("entry_plan") or {}
    return {
        "signal_id": str(row.get("signal_id") or ""),
        "session": row.get("session"),
        "instrument": str(row.get("instrument") or ""),
        "family": row.get("family") or fam.family(str(row.get("instrument") or "")),
        "option_type": str(row.get("option_type") or "UNKNOWN").upper(),
        "tradability": trad.get("status") or tradability.UNKNOWN,
        "tradability_reasons": list(trad.get("reasons") or []),
        "a_plus": grade.get("label") or a_plus_shadow.UNKNOWN,
        "a_plus_reasons": list(grade.get("reasons") or []),
        "entry_quality": eq.get("state") or entry_quality.UNKNOWN,
        "spread_points": _num(trad.get("spread")),
        "spread_pct": _num(state.get("spread_pct_of_premium")),
        "entry": _num(plan.get("entry_price")),
        "risk_points": _num(plan.get("risk_points")),
    }


def _minutes(event: object) -> float | None:
    if isinstance(event, dict):
        return _num(event.get("minutes_from_signal"))
    return None


def _resolved(outcome_rows: list[dict], calls: dict[str, dict],
              excluded: dict[str, int] | None = None) -> list[dict]:
    """Resolved outcomes carrying the labels and the costs of their own call."""
    out: list[dict] = []
    for row in outcome_rows:
        if row.get("event") != "RESOLVED":
            continue
        instrument = str(row.get("instrument") or "")
        if not instrument:
            continue
        why = r_integrity.rejection(row)
        if why is not None:
            if excluded is not None:
                excluded[why] = excluded.get(why, 0) + 1
            continue
        gross = _num(row.get("realized_r"))
        if gross is None:
            continue
        call = calls.get(str(row.get("signal_id") or ""))
        spec = REGISTRY.get(instrument)
        cost = instrument_studies._cost_r(
            _num(row.get("entry")), _num(row.get("risk_points")),
            (call or {}).get("spread_points"),
            int(spec.lot_size) if spec and spec.lot_size else 1,
        )
        reached = row.get("targets_reached") or {}
        out.append({
            **(call or {"signal_id": str(row.get("signal_id") or "")}),
            "labels_recorded": call is not None,
            "instrument": instrument,
            "family": row.get("family") or (call or {}).get("family")
            or fam.family(instrument),
            "session": row.get("session") or (call or {}).get("session"),
            "outcome": row.get("outcome"),
            "gross_r": gross,
            "cost_r": cost,
            "net_r": round(gross - cost, 3) if cost is not None else None,
            "target_before_stop": row.get("order") == "TARGET_FIRST",
            "mfe_r": _num(row.get("mfe_r")),
            "mae_r": _num(row.get("mae_r")),
            "mfe_capture": _num(row.get("mfe_capture")),
            "give_back_r": _num(row.get("give_back_r")),
            "minutes_to_mfe": _num(row.get("minutes_to_mfe")),
            "minutes_to_mae": _num(row.get("minutes_to_mae")),
            "minutes_to_resolution": _num(row.get("minutes_to_resolution")),
            "minutes_to_t1": _minutes(reached.get("T1")),
            "minutes_to_t2": _minutes(reached.get("T2")),
            "minutes_to_t3": _minutes(reached.get("T3")),
            "minutes_to_stop": _minutes(row.get("stop_event")),
            "failure_kind": (row.get("failure_attribution") or {}).get("failure_kind"),
        })
    return out


def _status(rows: list[dict], sessions: int) -> str:
    if len(rows) < MIN_COHORT:
        return INSUFFICIENT
    return OBSERVATION if sessions < 2 else IN_SAMPLE_ONLY


def cohort(name: str, rows: list[dict], sessions: int) -> dict:
    """One cohort's economics. Gross and net are kept apart, always."""
    gross = [r["gross_r"] for r in rows if r["gross_r"] is not None]
    net = [r["net_r"] for r in rows if r["net_r"] is not None]
    wins = [v for v in net if v > 0]
    losses = [v for v in net if v < 0]
    mfe = [r["mfe_r"] for r in rows if r["mfe_r"] is not None]
    mae = [r["mae_r"] for r in rows if r["mae_r"] is not None]
    capture = [r["mfe_capture"] for r in rows if r["mfe_capture"] is not None]
    giveback = [r["give_back_r"] for r in rows if r["give_back_r"] is not None]
    t1 = [r["minutes_to_t1"] for r in rows if r["minutes_to_t1"] is not None]
    stop = [r["minutes_to_stop"] for r in rows if r["minutes_to_stop"] is not None]
    won = sum(1 for v in gross if v > 0)
    profit = sum(wins)
    loss = -sum(losses)
    return {
        "cohort": name,
        "resolved": len(rows),
        "costed": len(net),
        "uncosted": len(rows) - len(net),
        "target_before_stop_pct": _share(
            sum(1 for r in rows if r["target_before_stop"]), len(rows)),
        "win_rate_gross_pct": _share(won, len(gross)),
        "win_rate_net_pct": _share(len(wins), len(net)),
        "gross_r_median": _pct(gross, 0.5),
        "expectancy_gross_r": _mean(gross),
        "net_r_median": _pct(net, 0.5),
        "expectancy_net_r": _mean(net),
        "net_r_total": round(sum(net), 3) if net else None,
        # Undefined, not infinite, when a cohort never lost: an unbounded factor
        # printed as a number reads like an edge.
        "profit_factor_net": (round(profit / loss, 3) if loss > 0
                              else (None if profit >= 0 else 0.0)),
        "mfe_r_median": _pct(mfe, 0.5),
        "mae_r_median": _pct(mae, 0.5),
        "mfe_capture_median": _pct(capture, 0.5),
        "give_back_r_median": _pct(giveback, 0.5),
        "minutes_to_t1_median": _pct(t1, 0.5),
        "minutes_to_t1_p90": _pct(t1, 0.90),
        "minutes_to_stop_median": _pct(stop, 0.5),
        "reached_half_r_then_lost": sum(
            1 for r in rows
            if (r["mfe_r"] or 0) >= 0.5 and (r["gross_r"] or 0) <= 0),
        "sessions": sessions,
        "status": _status(rows, sessions),
        "shortfall": (max(0, MIN_COHORT - len(rows)) or None),
    }


def _by(rows: list[dict], key: str, values: tuple[str, ...],
        sessions: int) -> list[dict]:
    return [cohort(f"{key}={v}", [r for r in rows if r.get(key) == v], sessions)
            for v in values]


def _by_reason(rows: list[dict], key: str, sessions: int) -> list[dict]:
    """Overlapping reason cohorts. A call with three reasons is in all three."""
    seen: list[str] = []
    for row in rows:
        for reason in row.get(key) or []:
            if reason not in seen:
                seen.append(reason)
    return [cohort(f"{key}~{reason}",
                   [r for r in rows if reason in (r.get(key) or [])], sessions)
            for reason in sorted(seen)]


def joined(journal_rows: list[dict], outcome_rows: list[dict],
           excluded: dict[str, int] | None = None) -> list[dict]:
    """Resolved option outcomes carrying their own signal-time labels and costs.

    The join every label study works from, exposed so a later phase reads the same
    costed rows instead of re-deriving them (and re-deriving them differently).
    """
    calls = {}
    for row in journal_rows:
        if str(row.get("signal_vehicle") or "").upper() == "FUTURES":
            continue  # futures are points, options are premium; never pooled
        rec = _labels(row)
        if rec["signal_id"]:
            calls[rec["signal_id"]] = rec
    return _resolved(outcome_rows, calls, excluded)


def study(journal_rows: list[dict], outcome_rows: list[dict]) -> dict:
    """§8/§9/§11/§13 label-versus-outcome tables over the rows given."""
    excluded: dict[str, int] = {}
    rows = joined(journal_rows, outcome_rows, excluded)
    sessions = len({r["session"] for r in rows if r.get("session")})
    baseline = cohort("ALL", rows, sessions)
    a_plus_rows = [r for r in rows if r["a_plus"] == a_plus_shadow.A_PLUS]
    a_plus_cohort = cohort("A_PLUS", a_plus_rows, sessions)
    edge = None
    if (a_plus_cohort["expectancy_net_r"] is not None
            and baseline["expectancy_net_r"] is not None):
        edge = round(a_plus_cohort["expectancy_net_r"]
                     - baseline["expectancy_net_r"], 4)
    return {
        "min_cohort": MIN_COHORT,
        "sessions": sessions,
        "sessions_required": MIN_SESSIONS,
        "holdout_sessions_required": MIN_HOLDOUT_SESSIONS,
        "session_shortfall": max(0, MIN_SESSIONS - sessions),
        "resolved": len(rows),
        "excluded": sum(excluded.values()),
        "excluded_by_reason": dict(sorted(excluded.items())),
        "labels_missing": sum(1 for r in rows if not r["labels_recorded"]),
        "baseline": baseline,
        "by_tradability": _by(rows, "tradability", tradability.STATUSES, sessions),
        "by_untradable_reason": _by_reason(
            [r for r in rows if r["tradability"] == tradability.UNTRADABLE],
            "tradability_reasons", sessions),
        "by_a_plus": _by(rows, "a_plus", a_plus_shadow.LABELS, sessions),
        "by_a_plus_reason": _by_reason(
            [r for r in rows if r["a_plus"] != a_plus_shadow.A_PLUS],
            "a_plus_reasons", sessions),
        "by_entry_quality": _by(rows, "entry_quality", entry_quality.STATES, sessions),
        "by_family": _by(rows, "family", fam.FAMILIES, sessions),
        "by_option_type": _by(rows, "option_type", ("CE", "PE", "UNKNOWN"), sessions),
        "a_plus_vs_baseline": {
            "a_plus": a_plus_cohort,
            "baseline": baseline,
            "net_r_edge": edge,
            "status": _status(a_plus_rows, sessions),
            "note": ("in-sample by construction: these are the sessions the "
                     "thresholds were read on, so no edge here is evidence of "
                     "an out-of-sample edge"),
        },
        "research_only": True,
        "notes": [
            "cohorts are joined signal_id -> resolved outcome, so every label is "
            "scored only against the trade it was attached to at signal time",
            "net R charges that call's own recorded spread plus the board's "
            "round-trip cost model; a call with no quoted book has no net number",
            "reason cohorts overlap: one UNTRADABLE call can be wide, thin and "
            "stale at once, so the reason counts can exceed the cohort size",
            "no label in this study gated, blocked or suppressed any production "
            "BUY; every one of these trades reached the dashboard",
            f"no cohort reads VALIDATED below {MIN_SESSIONS} sessions with "
            f"{MIN_HOLDOUT_SESSIONS} chronological holdout sessions (§19)",
            r_integrity.note(),
        ],
    }


def report(session: str | None = None) -> dict:
    from app.analysis.signal_journal import journal_path, outcomes_path

    journal = instrument_studies._read_jsonl(journal_path(), session)
    outcomes = instrument_studies._read_jsonl(outcomes_path(), session)
    out = study(journal, outcomes)
    out["session"] = session
    out["rows_read"] = {"journal": len(journal), "outcomes": len(outcomes)}
    return out


def write_report(session: str | None = None) -> Path:
    from app.analysis.signal_journal import journal_path

    path = Path(journal_path()).parent / REPORT_JSON
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report(session), indent=2, default=str),
                   encoding="utf-8")
    tmp.replace(path)
    return path
