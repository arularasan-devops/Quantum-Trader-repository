"""The §4 corporate-action table, read from the exchange's own daily file.

``Bc<ddmmyy>.csv`` inside the daily PR archive is the published action list:

    SERIES,SYMBOL,SECURITY,RECORD_DT,BC_STRT_DT,BC_END_DT,EX_DT,ND_STRT_DT,ND_END_DT,PURPOSE
    EQ,TATASTEEL,Tata Steel Limited,29/07/2022,,,28/07/2022,,,FVSPLT FRM RS 10 TO RE 1

Each day's file repeats forthcoming actions, and lists the same action once per
series (``EQ`` and ``BE``), so the same event arrives many times. Deduplication is
therefore on (symbol, series, ex-date, purpose) — deliberately including the
purpose text, so that two genuinely different actions sharing an ex-date (a
dividend and a split on the same day, which happens) both survive instead of one
overwriting the other.

``EX_DT`` is the date the price basis changes: the first session that trades on
the new basis. That is the convention the adjustment engine consumes, and it is
the one the file publishes, so no date arithmetic is invented here.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from io import StringIO

from . import (
    ACTION_UNCLASSIFIED,
    CorporateAction,
    STORED_SERIES,
    classify_purpose,
)


def _date(value: str) -> date | None:
    text = (value or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_bc(text: str, *, source_file: str = "") -> tuple[list[CorporateAction], dict[str, int]]:
    """Parse one ``Bc`` file. Returns (actions, counters).

    Counters carry ``no_ex_date`` and ``unclassified`` so the audit can state how
    much of the published table the classifier actually understood. An
    unclassified purpose is kept in the list — it is evidence of an event at that
    date, and the adjustment engine treats it as a blackout rather than assuming
    it is harmless.
    """
    actions: list[CorporateAction] = []
    counters = {"rows": 0, "no_ex_date": 0, "other_series": 0, "unclassified": 0}
    for raw in csv.DictReader(StringIO(text)):
        counters["rows"] += 1
        series = (raw.get("SERIES") or "").strip().upper()
        if series not in STORED_SERIES:
            counters["other_series"] += 1
            continue
        ex_date = _date(raw.get("EX_DT") or "")
        if ex_date is None:
            counters["no_ex_date"] += 1
            continue
        purpose = " ".join((raw.get("PURPOSE") or "").split())
        action_type, factor, dividend = classify_purpose(purpose)
        if action_type == ACTION_UNCLASSIFIED:
            counters["unclassified"] += 1
        actions.append(
            CorporateAction(
                symbol=(raw.get("SYMBOL") or "").strip().upper(),
                series=series,
                ex_date=ex_date,
                purpose=purpose,
                action_type=action_type,
                factor=factor,
                dividend_inr=dividend,
                quantified=factor is not None,
                source_file=source_file,
            )
        )
    return actions, counters


def dedupe(actions: list[CorporateAction]) -> list[CorporateAction]:
    """One row per (symbol, ex-date, purpose); the first series seen is kept.

    Series is deliberately *not* part of the key. One split is published once per
    series the security trades in, and it is the same economic event: keeping both
    would multiply its factor into itself and adjust a 1:10 split as 1:100, which
    is the kind of error that looks like a price series rather than a bug.
    """
    seen: set[tuple[str, str, str]] = set()
    out: list[CorporateAction] = []
    for action in actions:
        key = (action.symbol, action.ex_date.isoformat(), action.purpose)
        if key in seen:
            continue
        seen.add(key)
        out.append(action)
    return out


def index_by_symbol_date(
    actions: list[CorporateAction],
) -> dict[tuple[str, str], list[CorporateAction]]:
    """``(symbol, ex-date iso) -> actions``, for the adjustment walk."""
    out: dict[tuple[str, str], list[CorporateAction]] = {}
    for action in actions:
        out.setdefault((action.symbol, action.ex_date.isoformat()), []).append(action)
    return out


def combined_factor(actions: list[CorporateAction]) -> tuple[float | None, list[str]]:
    """Multiply the quantified factors on one ex-date; report the rest.

    Two quantified actions on the same ex-date (a split and a bonus together)
    compose multiplicatively. If *any* action on that date is unquantified the
    combined factor is still returned for the quantified part, but the caller is
    told which types were not accounted for, and it is the caller's decision
    (blackout) — this function never pretends the unquantified part is 1.0.
    """
    factor: float | None = None
    unquantified: list[str] = []
    # Defensive: the same event arriving twice (once per series) must not have its
    # factor applied twice, whether or not the caller deduplicated first.
    for action in dedupe(list(actions)):
        if action.factor is not None:
            factor = action.factor if factor is None else factor * action.factor
        elif action.action_type not in ("NO_PRICE_EFFECT", "DIVIDEND", "BUYBACK"):
            unquantified.append(action.action_type)
    return factor, unquantified
