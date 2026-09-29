"""The §36 gate, re-run against the NSE archive dataset.

Stage 1 asked whether the *provider* could supply what the study needs and the
answer was no. This asks the same six questions of the archive dataset actually
on disk, and the difference is that every answer is now a measurement over
ingested files rather than a probe of an API.

The gate thresholds are frozen here, before the numbers exist, and partial credit
is still not credit:

``PRICE_COVERAGE``
    at least ``MIN_SESSIONS`` ingested sessions with no unresolved
    ``UNAVAILABLE`` day inside the window — an unreachable day is unfinished
    work, not a holiday, and a study run over a window with holes in it would
    report a distorted number of trading opportunities.
``RAW_PRICE_CONFIRMED``
    the declared split/bonus probes must show the discontinuity *in the raw
    prints*. This is the single check that failed on the previous source, where
    the observed ratios were 0.98–1.05 at ex-dates whose published ratio was 0.10
    or 0.50, and it is checked here with the same probe list rather than a
    friendlier one.
``CORPORATE_ACTION_COVERAGE``
    an action table exists, the probe events are in it with the right ratios, and
    the share of sessions whose action file was not obtained is under
    ``MAX_ACTION_GAP_RATE``.
``ADJUSTMENT_AUDITABLE``
    unexplained discontinuities stay under ``MAX_UNEXPLAINED_RATE`` of
    symbol-sessions, and ``CA_WITHOUT_DISCONTINUITY`` is zero — a non-zero count
    means something upstream pre-adjusted the prices and the raw claim is false.
``SURVIVORSHIP_CONTROL``
    the dataset must actually contain securities that stopped trading inside the
    window. A dataset of current survivors would pass every other check and still
    produce the fake momentum edge this gate exists to prevent.
``UNIVERSE_RECONSTRUCTABLE``
    the declared screen must yield a workable eligible set at the start and end of
    the window using strictly prior sessions.

Index membership and sector history remain absent, and the report says so in the
same breath as it says the gate is open: they are declared limitations under §5
and §11, not silent omissions. Nothing about that is presented as an index study.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from . import (
    ACTION_BONUS,
    ACTION_SPLIT,
    CA_RATIO_MISMATCH,
    CA_RATIO_TOLERANCE,
    PREREG,
    SURVIVORSHIP_CONTROL_FULL,
    SURVIVORSHIP_CONTROL_LIMITED,
    UNIVERSE_LABEL,
    VERSION,
)
from .adjust import (
    CA_WITHOUT_DISCONTINUITY,
    EXPLAINED,
    build_all,
    observed_open_ratio,
    observed_ratio,
    ratio_candidates,
    unmapped_actions,
)
from .ingest import SESSION_OK, SESSION_UNAVAILABLE
from .store import RawStore
from .universe import build_lives, detect_symbol_changes, eligible_on, stop_reasons

GATE_ADEQUATE = "DATASET_ADEQUATE_PROCEED"
GATE_INADEQUATE = "DATASET_INADEQUATE_STOP"

MIN_SESSIONS = 1000
MAX_ACTION_GAP_RATE = 0.02
MAX_UNEXPLAINED_RATE = 0.002
MIN_VANISHED_SECURITIES = 5

#: Declared before measurement. Each is a published NSE event with a known ratio;
#: the observed raw ratio must match. Same list as stage 1 so the two sources are
#: compared on identical evidence.
RAW_PRICE_PROBES = (
    ("TATASTEEL", date(2022, 7, 28), 0.10, ACTION_SPLIT),
    ("NESTLEIND", date(2024, 1, 5), 0.10, ACTION_SPLIT),
)

#: Extra probes used only when the ingest window covers them.
OPTIONAL_PROBES = (
    ("RELIANCE", date(2017, 9, 7), 0.50, ACTION_BONUS),
    ("WIPRO", date(2019, 3, 6), 0.50, ACTION_BONUS),
    ("BAJFINANCE", date(2016, 9, 15), 0.20, ACTION_SPLIT),
)


@dataclass
class Finding:
    name: str
    status: str
    detail: str
    numbers: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail, "numbers": self.numbers}


SATISFIED = "SATISFIED"
MISSING = "MISSING"
INCONCLUSIVE = "INCONCLUSIVE"
LIMITED = "LIMITED"


def audit(store: RawStore, *, window: tuple[date, date] | None = None) -> dict:
    sessions = store.sessions()
    actions = store.read_actions()
    findings: list[Finding] = []

    if not sessions:
        return {
            "version": VERSION,
            "prereg_fingerprint": PREREG.fingerprint(),
            "gate": GATE_INADEQUATE,
            "findings": [Finding("PRICE_COVERAGE", MISSING, "no sessions ingested").as_dict()],
        }

    start, end = (window or (sessions[0], sessions[-1]))
    by_day = {day.isoformat(): store.read_session(day) for day in sessions}

    # -- price coverage ----------------------------------------------------
    unavailable = [
        day
        for day, entry in (store.manifest.get("sessions") or {}).items()
        if entry.get("status") == SESSION_UNAVAILABLE and start.isoformat() <= day <= end.isoformat()
    ]
    rows_total = sum(len(rows) for rows in by_day.values())
    delivery_sessions = sum(
        1
        for entry in (store.manifest.get("sessions") or {}).values()
        if entry.get("status") == SESSION_OK and entry.get("delivery")
    )
    if unavailable:
        findings.append(
            Finding(
                "PRICE_COVERAGE",
                INCONCLUSIVE,
                f"{len(unavailable)} session(s) unreachable and unfinished — retry ingest",
                {"sessions": len(sessions), "unavailable": len(unavailable)},
            )
        )
    elif len(sessions) < MIN_SESSIONS:
        findings.append(
            Finding(
                "PRICE_COVERAGE",
                MISSING,
                f"{len(sessions)} sessions ingested, {MIN_SESSIONS} required",
                {"sessions": len(sessions)},
            )
        )
    else:
        findings.append(
            Finding(
                "PRICE_COVERAGE",
                SATISFIED,
                f"{len(sessions)} sessions {sessions[0]}..{sessions[-1]}, {rows_total} raw rows",
                {
                    "sessions": len(sessions),
                    "rows": rows_total,
                    "delivery_sessions": delivery_sessions,
                },
            )
        )

    # -- raw price confirmation -------------------------------------------
    symbol_rows = {}
    for day in sessions:
        for row in by_day[day.isoformat()]:
            if row.series == "EQ":
                symbol_rows.setdefault(row.symbol, []).append(row)
    for rows in symbol_rows.values():
        rows.sort(key=lambda r: r.trade_date)

    probes = []
    for symbol, ex_date, expected, _kind in RAW_PRICE_PROBES + OPTIONAL_PROBES:
        if not (start <= ex_date <= end):
            probes.append({"symbol": symbol, "ex_date": ex_date.isoformat(), "status": "OUT_OF_WINDOW"})
            continue
        rows = symbol_rows.get(symbol, [])
        positions = [i for i, row in enumerate(rows) if row.trade_date == ex_date.isoformat()]
        if not positions or positions[0] == 0:
            probes.append({"symbol": symbol, "ex_date": ex_date.isoformat(), "status": "NOT_INGESTED"})
            continue
        position = positions[0]
        current, previous = rows[position], rows[position - 1]
        observed = observed_ratio(previous, current)
        at_open = observed_open_ratio(previous, current)
        ratios = ratio_candidates(previous, current)
        basis, residual = "", None
        for label, value in ratios.items():
            candidate = abs(value / expected - 1.0)
            if residual is None or candidate < residual:
                basis, residual = label, candidate
        ok = residual is not None and residual <= CA_RATIO_TOLERANCE
        probes.append(
            {
                "symbol": symbol,
                "ex_date": ex_date.isoformat(),
                "expected_ratio": expected,
                "observed_ratio": round(observed, 4),
                "observed_open_ratio": round(at_open, 4),
                "matched_basis": basis if ok else "",
                "status": "RAW_CONFIRMED" if ok else "ADJUSTED_OR_MISMATCH",
            }
        )
    tested = [probe for probe in probes if probe["status"] in ("RAW_CONFIRMED", "ADJUSTED_OR_MISMATCH")]
    confirmed = [probe for probe in tested if probe["status"] == "RAW_CONFIRMED"]
    if not tested:
        findings.append(Finding("RAW_PRICE_CONFIRMED", INCONCLUSIVE, "no probe event inside window", {"probes": probes}))
    elif len(confirmed) == len(tested):
        findings.append(
            Finding(
                "RAW_PRICE_CONFIRMED",
                SATISFIED,
                f"{len(confirmed)}/{len(tested)} probe ex-dates show the raw discontinuity",
                {"probes": probes},
            )
        )
    else:
        findings.append(
            Finding(
                "RAW_PRICE_CONFIRMED",
                MISSING,
                f"only {len(confirmed)}/{len(tested)} probes raw — prices appear pre-adjusted",
                {"probes": probes},
            )
        )

    # -- corporate actions -------------------------------------------------
    manifest_sessions = [
        entry
        for day, entry in (store.manifest.get("sessions") or {}).items()
        if entry.get("status") == SESSION_OK and start.isoformat() <= day <= end.isoformat()
    ]
    with_actions = sum(1 for entry in manifest_sessions if entry.get("actions") == "OK")
    gap_rate = 1.0 - (with_actions / len(manifest_sessions)) if manifest_sessions else 1.0
    by_type: dict[str, int] = {}
    for action in actions:
        by_type[action.action_type] = by_type.get(action.action_type, 0) + 1
    quantified = sum(1 for action in actions if action.quantified)
    numbers = {
        "actions": len(actions),
        "quantified": quantified,
        "by_type": dict(sorted(by_type.items())),
        "sessions_with_action_file": with_actions,
        "action_gap_rate": round(gap_rate, 4),
    }
    if not actions:
        findings.append(Finding("CORPORATE_ACTION_COVERAGE", MISSING, "no action table", numbers))
    elif gap_rate > MAX_ACTION_GAP_RATE:
        findings.append(
            Finding(
                "CORPORATE_ACTION_COVERAGE",
                INCONCLUSIVE,
                f"action file missing for {gap_rate:.1%} of sessions (limit {MAX_ACTION_GAP_RATE:.1%})",
                numbers,
            )
        )
    else:
        findings.append(
            Finding(
                "CORPORATE_ACTION_COVERAGE",
                SATISFIED,
                f"{len(actions)} dated actions, {quantified} with a derivable ratio",
                numbers,
            )
        )

    # -- adjustment auditability ------------------------------------------
    _, ledger = build_all(symbol_rows, actions)
    by_status: dict[str, int] = {}
    for item in ledger:
        by_status[item.status] = by_status.get(item.status, 0) + 1
    orphans = unmapped_actions(symbol_rows, actions)
    orphan_reasons: dict[str, int] = {}
    for orphan in orphans:
        reason = orphan["reason"]
        orphan_reasons[reason] = orphan_reasons.get(reason, 0) + 1
    symbol_sessions = sum(len(rows) for rows in symbol_rows.values())
    unexplained = by_status.get("UNEXPLAINED_DISCONTINUITY", 0)
    pre_adjusted = by_status.get(CA_WITHOUT_DISCONTINUITY, 0)
    rate = unexplained / symbol_sessions if symbol_sessions else 1.0
    numbers = {
        "symbol_sessions": symbol_sessions,
        "breaks": len(ledger),
        "by_status": dict(sorted(by_status.items())),
        "unexplained_rate": round(rate, 6),
        "explained": by_status.get(EXPLAINED, 0),
        "mismatched_not_adjusted": by_status.get(CA_RATIO_MISMATCH, 0),
        "unmapped_action_groups": len(orphans),
        "unmapped_reasons": dict(sorted(orphan_reasons.items())),
    }
    if pre_adjusted:
        findings.append(
            Finding(
                "ADJUSTMENT_AUDITABLE",
                MISSING,
                f"{pre_adjusted} published action(s) left no mark on prices — source is pre-adjusted",
                numbers,
            )
        )
    elif rate > MAX_UNEXPLAINED_RATE:
        findings.append(
            Finding(
                "ADJUSTMENT_AUDITABLE",
                LIMITED,
                f"unexplained discontinuity rate {rate:.4%} exceeds {MAX_UNEXPLAINED_RATE:.2%}",
                numbers,
            )
        )
    else:
        findings.append(
            Finding(
                "ADJUSTMENT_AUDITABLE",
                SATISFIED,
                f"{by_status.get(EXPLAINED, 0)} breaks explained by published actions, "
                f"{unexplained} unexplained ({rate:.4%})",
                numbers,
            )
        )

    # -- survivorship ------------------------------------------------------
    all_rows_by_day = {day: rows for day, rows in by_day.items()}
    lives = build_lives(all_rows_by_day)
    renames = detect_symbol_changes(lives)
    stopped = stop_reasons(lives, actions)
    last_session = sessions[-1].isoformat()
    vanished = [
        life
        for life in lives.values()
        if life.last_session < last_session and life.symbol not in renames
    ]
    numbers = {
        "symbols": len(lives),
        "vanished": len(vanished),
        "renames": len(renames),
        "vanished_with_published_reason": sum(1 for life in vanished if life.symbol in stopped),
        "examples": [life.symbol for life in sorted(vanished, key=lambda x: x.last_session)[:10]],
    }
    if len(vanished) >= MIN_VANISHED_SECURITIES:
        findings.append(
            Finding(
                "SURVIVORSHIP_CONTROL",
                SATISFIED,
                f"{len(vanished)} securities stop trading inside the window and remain in the dataset",
                numbers,
            )
        )
    else:
        findings.append(
            Finding(
                "SURVIVORSHIP_CONTROL",
                MISSING,
                f"only {len(vanished)} vanished securities — dataset looks like survivors",
                numbers,
            )
        )

    # -- universe ----------------------------------------------------------
    checks = []
    for as_of in (sessions[min(len(sessions) - 1, 300)], sessions[-1]):
        prior = {
            day.isoformat(): by_day[day.isoformat()]
            for day in sessions
            if day < as_of
        }
        try:
            eligibility = eligible_on(as_of, prior)
            checks.append({"as_of": as_of.isoformat(), "eligible": len(eligibility.eligible)})
        except ValueError as exc:
            checks.append({"as_of": as_of.isoformat(), "error": str(exc)})
    workable = [check for check in checks if check.get("eligible", 0) >= 50]
    findings.append(
        Finding(
            "UNIVERSE_RECONSTRUCTABLE" if workable else "UNIVERSE_RECONSTRUCTABLE",
            SATISFIED if len(workable) == len(checks) and checks else MISSING,
            f"{UNIVERSE_LABEL}: " + ", ".join(
                f"{check['as_of']}={check.get('eligible', check.get('error'))}" for check in checks
            ),
            {"checks": checks},
        )
    )

    # -- declared limitations (never a silent omission) ---------------------
    findings.append(
        Finding(
            "HISTORICAL_INDEX_MEMBERSHIP",
            MISSING,
            "not published in these archives; eligibility uses the declared liquidity screen "
            "and no artefact describes this as an index universe",
            {},
        )
    )
    findings.append(
        Finding(
            "HISTORICAL_SECTOR_CLASSIFICATION",
            MISSING,
            "not published in these archives; the §22 sector concentration cap is therefore "
            "unenforceable and will be reported as UNENFORCED rather than assumed satisfied",
            {},
        )
    )

    blocking = {
        "PRICE_COVERAGE",
        "RAW_PRICE_CONFIRMED",
        "CORPORATE_ACTION_COVERAGE",
        "ADJUSTMENT_AUDITABLE",
        "SURVIVORSHIP_CONTROL",
        "UNIVERSE_RECONSTRUCTABLE",
    }
    failed = [
        finding.name
        for finding in findings
        if finding.name in blocking and finding.status != SATISFIED
    ]
    gate = GATE_ADEQUATE if not failed else GATE_INADEQUATE
    survivorship = (
        SURVIVORSHIP_CONTROL_FULL
        if "SURVIVORSHIP_CONTROL" not in failed
        else SURVIVORSHIP_CONTROL_LIMITED
    )

    payload = {
        "version": VERSION,
        "prereg_fingerprint": PREREG.fingerprint(),
        "prereg": PREREG.as_dict(),
        "window": [start.isoformat(), end.isoformat()],
        "gate": gate,
        "blocking_failures": failed,
        "survivorship_status": survivorship,
        "findings": [finding.as_dict() for finding in findings],
    }
    payload["fingerprint"] = _fingerprint(payload)
    return payload


def _fingerprint(payload: dict) -> str:
    import hashlib

    trimmed = {
        "version": payload["version"],
        "prereg_fingerprint": payload["prereg_fingerprint"],
        "window": payload["window"],
        "gate": payload["gate"],
        "findings": [
            {"name": finding["name"], "status": finding["status"]}
            for finding in payload["findings"]
        ],
    }
    blob = json.dumps(trimmed, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
