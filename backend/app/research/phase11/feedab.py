"""Phase 11 §2 — did configuring the deep watchlist actually improve the feed?

RESEARCH / MEASUREMENT ONLY.

The argument for a smaller deep list is arithmetic: warm-up candles are REST and
rate-limited, so 50 names queue behind each other and the ones that need complete
minute bars wait behind 44 that do not. Arithmetic is a hypothesis. The
measurement is: record the same numbers on sessions that ran at the full deep
watchlist and on sessions that ran at the reduced one, and compare.

This module refuses to produce the comparison from one side of it. Every session
is labelled BEFORE, AFTER or UNKNOWN from the recorded feed profile — the
configuration that was actually live, not the configuration in today's .env — and
where no AFTER session exists the answer is UNMEASURED with the reason attached.
An estimate of the improvement is available and is deliberately not reported as
one: an arithmetic projection presented next to measured numbers reads as a
result.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from app.research.phase7.dataset import load_candles, session_of
from app.research.phase7.policies import median
from app.research.phase7.quality import candle_audit

from .families import family_of, split

BEFORE = "BEFORE"
AFTER = "AFTER"
UNKNOWN = "UNKNOWN"

# The recorded metrics §2 asks for. Held as data so the report cannot claim a
# field was measured that was never captured.
MEASURED_FIELDS = (
    "deep_count", "broad_count", "active_count", "tick_cost_ms",
    "scanned_per_cycle", "median_scan_age_sec", "p90_scan_age_sec",
    "worst_scan_age_sec", "instruments_behind", "recorded_chain_writes_per_cycle",
    "quote_calls_per_cycle", "feed_fresh", "feed_stale", "feed_no_data",
    "cpu_sec_total", "peak_rss_mb",
)
# What the replay database cannot show, named so its absence is not read as a
# zero. True exchange-to-receive latency, REST 429s and reconnect counts are not
# in the schema; the funnel and profile logs are where they would have to come
# from, and only for sessions recorded after those logs existed.
NOT_MEASURABLE_FROM_REPLAY = (
    "rest_poll_count", "rate_limit_errors", "websocket_ticks", "reconnects",
    "startup_time_sec", "exchange_to_receive_latency_ms",
)


def _profiles(path: str | Path | None) -> dict[str, list[dict]]:
    """Recorded feed profiles grouped by session."""
    out: dict[str, list[dict]] = defaultdict(list)
    p = Path(path) if path else None
    if p is None or not p.exists():
        return out
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            session = rec.get("session")
            if session:
                out[str(session)].append(rec)
    return out


def _phase_of(profiles: list[dict]) -> tuple[str, str]:
    """BEFORE, AFTER or UNKNOWN for one session's recorded profiles, and why.

    A session with no profile is UNKNOWN rather than BEFORE. Today's ``.env`` is
    not evidence about a session recorded weeks ago, and labelling those sessions
    BEFORE would put an assumed configuration into the comparison's control arm.
    """
    if not profiles:
        return UNKNOWN, ("no feed profile was recorded for this session, so the "
                         "configuration that was live is unrecorded. It is not "
                         "assumed from the current .env")
    split_on = [bool(p.get("split_enabled")) for p in profiles]
    if all(split_on):
        return AFTER, "every profile in this session recorded the split enabled"
    if not any(split_on):
        return BEFORE, "every profile in this session recorded the split disabled"
    # The operator changed the configuration mid-session, so neither label is
    # true of the whole session and it is excluded rather than assigned.
    return UNKNOWN, ("the configuration changed mid-session, so neither label is "
                     "true of the whole session")


def _profile_block(profiles: list[dict]) -> dict:
    if not profiles:
        return {"snapshots": 0}
    out: dict[str, float | int | None] = {"snapshots": len(profiles)}
    for field in MEASURED_FIELDS:
        vals = [float(p[field]) for p in profiles
                if p.get(field) is not None]
        out[f"median_{field}"] = median(vals)
    deep = profiles[-1].get("deep") or []
    out["deep"] = deep
    out["deep_by_family"] = {k: v for k, v in split(
        [{"instrument": d, "family": family_of(d)} for d in deep]).items()}
    out["deep_by_family"] = {k: [r["instrument"] for r in v]
                             for k, v in out["deep_by_family"].items()}
    return out


def _candle_quality(db: str, instruments: list[str]) -> dict:
    """Per session, per instrument: how much of the minute series is missing.

    This is the number the whole deep-watchlist argument is about, so it is
    measured from the recorded candles rather than taken from a summary.
    """
    per_session: dict[str, dict[str, dict]] = defaultdict(dict)
    for name in instruments:
        candles = load_candles(db, name)
        if not candles:
            continue
        grouped: dict[str, list] = defaultdict(list)
        for c in candles:
            grouped[session_of(c.time)].append(c)
        for session, cs in grouped.items():
            audit = candle_audit(cs)
            per_session[session][name] = {
                "family": family_of(name),
                "bars": audit.get("bars"),
                "expected_bars": audit.get("expected_bars_in_sessions"),
                "missing_bars": audit.get("missing_bars"),
                "missing_pct": audit.get("missing_pct"),
            }
    return per_session


def _session_summary(per_instrument: dict[str, dict]) -> dict:
    def agg(rows: list[dict]) -> dict:
        expected = sum(r["expected_bars"] or 0 for r in rows)
        missing = sum(r["missing_bars"] or 0 for r in rows)
        return {
            "instruments": len(rows),
            "expected_bars": expected,
            "missing_bars": missing,
            "missing_pct": round(100.0 * missing / expected, 1) if expected else None,
        }

    rows = list(per_instrument.values())
    by_family: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_family[r["family"]].append(r)
    return {
        "all": agg(rows),
        "by_family": {fam: agg(sub) for fam, sub in sorted(by_family.items())},
    }


def study(db: str, instruments: list[str], sessions: list[str],
          profile_path: str | Path | None,
          *, configured_deep: list[str], stale_rate_by_session: dict[str, float],
          ) -> dict:
    """§2 — the before/after table, with AFTER left empty until it exists."""
    profiles = _profiles(profile_path)
    quality = _candle_quality(db, instruments)

    per_session: dict[str, dict] = {}
    for session in sorted(set(sessions) | set(quality) | set(profiles)):
        recs = profiles.get(session) or []
        phase, why = _phase_of(recs)
        per_session[session] = {
            "phase": phase,
            "phase_reason": why,
            "feed_profile": _profile_block(recs),
            "candle_quality": _session_summary(quality.get(session) or {}),
            "stale_signal_pct": stale_rate_by_session.get(session),
        }

    groups: dict[str, list[str]] = {BEFORE: [], AFTER: [], UNKNOWN: []}
    for session, block in per_session.items():
        groups[block["phase"]].append(session)

    def group_block(names: list[str]) -> dict:
        if not names:
            return {"sessions": 0}
        expected = sum((per_session[s]["candle_quality"]["all"].get("expected_bars")
                        or 0) for s in names)
        missing = sum((per_session[s]["candle_quality"]["all"].get("missing_bars")
                       or 0) for s in names)
        stale = [per_session[s]["stale_signal_pct"] for s in names
                 if per_session[s]["stale_signal_pct"] is not None]
        prof = [p for s in names for p in (profiles.get(s) or [])]
        return {
            "sessions": len(names),
            "session_dates": sorted(names),
            "missing_bar_pct": round(100.0 * missing / expected, 1)
            if expected else None,
            "median_stale_signal_pct": median(stale),
            "feed_profile": _profile_block(prof),
        }

    before, after = group_block(groups[BEFORE]), group_block(groups[AFTER])
    unknown = group_block(groups[UNKNOWN])
    comparable = bool(before.get("sessions") and after.get("sessions"))
    if comparable:
        reason = None
    elif not after.get("sessions") and not before.get("sessions"):
        reason = (f"{unknown.get('sessions', 0)} session(s) were recorded before the "
                  "feed profile existed, so neither side of this comparison has a "
                  "session whose live configuration is known. Their bar "
                  "completeness is reported under configuration_unrecorded as a "
                  "baseline, not as the BEFORE arm")
    elif not after.get("sessions"):
        reason = ("every session whose configuration was recorded ran with the split "
                  "disabled, so the AFTER side does not exist yet. One session "
                  "recorded with QT_DEEP_WATCHLIST set produces it")
    else:
        reason = ("no session recorded with the split disabled, so there is nothing "
                  "to compare the AFTER side against")
    return {
        "status": "MEASURED" if comparable else "AFTER_NOT_MEASURED",
        "configured_deep_watchlist_now": list(configured_deep),
        "profile_source": str(profile_path) if profile_path else None,
        "profile_records": sum(len(v) for v in profiles.values()),
        "by_session": per_session,
        "groups": {k: sorted(v) for k, v in groups.items()},
        "before": before,
        "after": after,
        "configuration_unrecorded": unknown,
        "comparison": ({
            "missing_bar_pct_before": before.get("missing_bar_pct"),
            "missing_bar_pct_after": after.get("missing_bar_pct"),
            "stale_signal_pct_before": before.get("median_stale_signal_pct"),
            "stale_signal_pct_after": after.get("median_stale_signal_pct"),
        } if comparable else None),
        "unmeasured_reason": reason,
        "measured_fields": list(MEASURED_FIELDS),
        "not_measurable_from_replay": list(NOT_MEASURABLE_FROM_REPLAY),
        "refusal": "no improvement is claimed and no arithmetic projection is "
                   "reported as a result. §2's questions about stale BUY rate and "
                   "REST pressure are answerable only against a recorded AFTER "
                   "session",
    }
