"""Production readiness — Phase 19 §19.

Three strategies are being measured in parallel and each has its own gate, its
own sample and its own reason for not being ready. Until now those answers were
in three different reports, which makes the only question that matters — "is
anything close?" — a research exercise. This module states each one in the same
shape, in one place, with the **blocking reason spelled out** rather than a bare
NOT_READY.

Statuses, in increasing order of evidence:

``NOT_READY``   nothing resolved yet.
``RESEARCH``    rows exist but none are costed, so nothing is measurable.
``PAPER``       costed outcomes exist; the sample is short of the gate.
``VALIDATED``   development and holdout gates met with positive net expectancy.
``PRODUCTION_CANDIDATE``  the promotion gate passed. Still not active.

The last status is the one worth being precise about: it is a request for human
review. Nothing in this module, or anywhere downstream of it, activates a
strategy — there is no code path from a readiness row to an order.
"""
from __future__ import annotations

NOT_READY = "NOT_READY"
RESEARCH = "RESEARCH"
PAPER = "PAPER"
VALIDATED = "VALIDATED"
PRODUCTION_CANDIDATE = "PRODUCTION_CANDIDATE"

ORDER = (NOT_READY, RESEARCH, PAPER, VALIDATED, PRODUCTION_CANDIDATE)

# The shared gate. Deliberately the same numbers for options and futures: two
# gates with different thresholds is how a strategy gets promoted by whichever
# one is kinder.
MIN_DEV = 100
MIN_HOLDOUT = 100
MIN_CAPTURE_PCT = 90.0


def _f(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _row(
    *,
    strategy: str,
    vehicle: str,
    status: str,
    dev: dict,
    holdout: dict,
    capture_pct: float | None,
    last_validation: str | None,
    blockers: list[str],
    extra: dict | None = None,
) -> dict:
    row = {
        "strategy": strategy,
        "vehicle": vehicle,
        "status": status,
        "sample_size": int(dev.get("trades") or 0),
        "holdout_size": int(holdout.get("trades") or 0),
        "net_expectancy_r": _f(holdout.get("expectancy_r")),
        "dev_net_expectancy_r": _f(dev.get("expectancy_r")),
        "profit_factor": _f(holdout.get("profit_factor")),
        "max_drawdown_r": _f(holdout.get("max_drawdown_r")),
        "t1_pct": _f(holdout.get("t1_pct")),
        "capture_pct": capture_pct,
        "capture_target_pct": MIN_CAPTURE_PCT,
        "last_validation": last_validation,
        "blockers": blockers,
        "paper_only": True,
        "no_real_order": True,
        "promotion_is_manual": True,
    }
    if extra:
        row.update(extra)
    return row


def _gate_blockers(dev: dict, holdout: dict, capture_pct: float | None) -> list[str]:
    out: list[str] = []
    dev_n = int(dev.get("trades") or 0)
    hold_n = int(holdout.get("trades") or 0)
    if dev_n < MIN_DEV:
        out.append(f"development outcomes {dev_n} < {MIN_DEV}")
    if hold_n < MIN_HOLDOUT:
        out.append(f"chronological holdout outcomes {hold_n} < {MIN_HOLDOUT}")
    exp = _f(holdout.get("expectancy_r"))
    if hold_n and (exp is None or exp <= 0):
        out.append(f"holdout net expectancy {exp} is not positive")
    pf = _f(holdout.get("profit_factor"))
    if hold_n and (pf is None or pf <= 1.0):
        out.append(f"holdout profit factor {pf} is not > 1")
    if capture_pct is None:
        out.append("capture quality unmeasured — no rows captured yet")
    elif capture_pct < MIN_CAPTURE_PCT:
        out.append(
            f"exact/near-exact capture {capture_pct}% < {MIN_CAPTURE_PCT}% — "
            "performance figures should not be read yet"
        )
    return out


def _status_from(dev: dict, holdout: dict, blockers: list[str],
                 *, costed_rows: int, total_rows: int) -> str:
    dev_n = int(dev.get("trades") or 0)
    hold_n = int(holdout.get("trades") or 0)
    if total_rows == 0:
        return NOT_READY
    if costed_rows == 0:
        return RESEARCH
    if not blockers:
        return PRODUCTION_CANDIDATE
    exp = _f(holdout.get("expectancy_r"))
    if (
        dev_n >= MIN_DEV and hold_n >= MIN_HOLDOUT
        and exp is not None and exp > 0
    ):
        return VALIDATED
    return PAPER


def option_row(oos_result: dict, *, capture_pct: float | None,
               last_validation: str | None = None) -> dict:
    """Option A+ readiness from Phase 17's own OOS evaluation.

    The upstream status is deliberately not re-derived here; where it already
    says PRODUCTION_CANDIDATE this row agrees, so the two reports cannot
    disagree about the same rows.
    """
    dev = oos_result.get("development") or {}
    hold = oos_result.get("holdout") or {}
    considered = int(oos_result.get("rows_considered") or 0)
    excluded = int(oos_result.get("rows_excluded_uncosted") or 0)
    blockers = _gate_blockers(dev, hold, capture_pct)
    baseline = oos_result.get("baseline_holdout") or {}
    base_exp = _f(baseline.get("expectancy_r"))
    hold_exp = _f(hold.get("expectancy_r"))
    if (
        hold_exp is not None and base_exp is not None
        and hold_exp <= base_exp
    ):
        blockers.append(
            f"holdout expectancy {hold_exp} does not beat the unchanged "
            f"baseline {base_exp}"
        )
    status = _status_from(
        dev, hold, blockers,
        costed_rows=max(0, considered - excluded), total_rows=considered,
    )
    upstream = str(oos_result.get("status") or "")
    if upstream == PRODUCTION_CANDIDATE:
        status = PRODUCTION_CANDIDATE
    return _row(
        strategy="OPTION_A_PLUS", vehicle="OPTION", status=status,
        dev=dev, holdout=hold, capture_pct=capture_pct,
        last_validation=last_validation, blockers=blockers,
        extra={
            "rows_considered": considered,
            "rows_excluded_uncosted": excluded,
            "upstream_status": upstream or None,
            "basis": oos_result.get("basis"),
        },
    )


def futures_row(oos_result: dict, *, freshness_pct: float | None,
                plans: int = 0, entries: int = 0, resolved: int = 0,
                last_validation: str | None = None) -> dict:
    """Futures A+ readiness from the Phase 19 futures paper book."""
    dev = oos_result.get("development") or {}
    hold = oos_result.get("holdout") or {}
    blockers = _gate_blockers(dev, hold, freshness_pct)
    if resolved == 0:
        blockers.append(
            "no resolved futures paper trades yet"
            + (f" ({plans} valid plan(s), {entries} paper entr(ies))" if plans else "")
        )
    considered = int(oos_result.get("rows_considered") or 0)
    excluded = int(oos_result.get("rows_excluded_uncosted") or 0)
    status = _status_from(
        dev, hold, blockers,
        costed_rows=max(0, considered - excluded), total_rows=considered,
    )
    return _row(
        strategy="FUTURES_A_PLUS", vehicle="FUTURES", status=status,
        dev=dev, holdout=hold, capture_pct=freshness_pct,
        last_validation=last_validation, blockers=blockers,
        extra={
            "valid_plans": plans,
            "paper_entries": entries,
            "resolved": resolved,
            "rows_considered": considered,
            "rows_excluded_uncosted": excluded,
            "capture_metric": "FUTURES_FEED_FRESHNESS_PCT",
            "note": (
                "The futures feed publishes candles, not depth, so a spread is "
                "charged only where a book was recorded. Where it was not, net "
                "is optimistic by one spread and the row says so."
            ),
        },
    )


def cas_row(verdict: dict, *, capture_pct: float | None,
            resolved: int = 0, last_validation: str | None = None) -> dict:
    """CAS readiness from Phase 18's own verdict, restated in this shape.

    CAS keeps its own gate (100 resolved legs over 20 sessions, 5 of them
    expiries) because its regime is weeks old; this row reports that gate rather
    than replacing it with the shared one.
    """
    folds = verdict.get("folds") or {}
    dev = folds.get("DEVELOPMENT") or {}
    hold = folds.get("HOLDOUT") or {}
    ev = verdict.get("evidence") or {}
    checks = ev.get("checks") or {}
    blockers: list[str] = []
    for name, chk in checks.items():
        if isinstance(chk, dict) and not chk.get("ok"):
            blockers.append(f"{name}: have {chk.get('have')}, need {chk.get('need')}")
    upstream = str(verdict.get("verdict") or "")
    if upstream == "FAILS":
        blockers.append("CAS failed on the chronological holdout")

    if resolved == 0:
        status = NOT_READY
    elif upstream == "CAS_PRODUCTION_CANDIDATE":
        status = PRODUCTION_CANDIDATE
    elif not blockers:
        status = VALIDATED
    else:
        status = PAPER
    return _row(
        strategy="CAS", vehicle="OPTION", status=status,
        dev={
            "trades": dev.get("legs") or dev.get("trades") or 0,
            "expectancy_r": dev.get("net_expectancy_r"),
        },
        holdout={
            "trades": hold.get("legs") or hold.get("trades") or 0,
            "expectancy_r": hold.get("net_expectancy_r"),
            "profit_factor": hold.get("profit_factor"),
            "max_drawdown_r": hold.get("max_drawdown_r"),
            "t1_pct": hold.get("t1_pct"),
        },
        capture_pct=capture_pct,
        last_validation=last_validation, blockers=blockers,
        extra={
            "upstream_status": upstream or None,
            "resolved": resolved,
            "window": "15:10-15:30 IST",
            "gate": "CAS-specific: 100 resolved legs, 20 sessions, 5 expiries",
        },
    )


def board(rows: list[dict], *, calibration: dict | None = None) -> dict:
    """The panel payload: the three rows plus what a reader should conclude."""
    best = NOT_READY
    for row in rows:
        status = str(row.get("status") or NOT_READY)
        if status in ORDER and ORDER.index(status) > ORDER.index(best):
            best = status
    candidates = [r["strategy"] for r in rows if r.get("status") == PRODUCTION_CANDIDATE]
    return {
        "rows": rows,
        "statuses": list(ORDER),
        "best_status": best,
        "production_candidates": candidates,
        "any_production_candidate": bool(candidates),
        "calibration": calibration or {},
        "paper_only": True,
        "no_real_order": True,
        "promotion_is_manual": True,
        "banner": "RESEARCH / PAPER ONLY — NO REAL ORDER",
        "note": (
            "PRODUCTION_CANDIDATE is a request for review, not an activation. "
            "Nothing in this codebase promotes a strategy, and there is no code "
            "path from this panel to an order."
        ),
    }
