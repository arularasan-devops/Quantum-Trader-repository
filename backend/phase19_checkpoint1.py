"""Checkpoint 1 — is the recorder working? One command, one PASS/FAIL per line.

    .venv/bin/python phase19_checkpoint1.py                  # all sessions on disk
    .venv/bin/python phase19_checkpoint1.py --sessions 2     # the last two only
    .venv/bin/python phase19_checkpoint1.py --json           # machine-readable

This answers exactly one question — whether the pipeline recorded what happened —
and refuses to answer any other. It prints no expectancy, no win rate and no
vehicle conclusion, because a table computed over 41% of the trades that occurred
describes 41% of the trades that occurred, and reading it is how a broken
recorder gets mistaken for a working strategy.

A FAIL here means stop and fix capture. There is no value in twenty more sessions
of the same data.

Two honest limits on what can be measured in two sessions:

* **Exit-time capture** is reported as the share of resolved paper legs that
  exited against a real BID rather than falling back to the last traded price. A
  leg is only counted once it has resolved, so a session that ends with legs
  still open reports on fewer rows than it entered — that is stated, not hidden.
* **The futures book** can be legitimately empty: if no valid futures plan
  occurred, there is nothing to enter. That case is reported as NO DATA with the
  refusal counters beside it, and is distinguished from a book that saw plans and
  entered none.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request

from app.config import settings
from app.research.phase17 import capture as p17capture
from app.research.phase17 import quality as p17quality
from app.research.phase17 import reports as p17reports
from app.research.phase17 import store as p17store
from app.research.phase19 import futbook

PASS, FAIL, NODATA = "PASS", "FAIL", "NO DATA"

# Checkpoint 1 thresholds. The 90% figures are the capture design's own target;
# the orphan count is zero because an unexplained missing row means the lifecycle
# is losing trades, and a dataset that loses trades non-randomly is worse than no
# dataset at all.
MIN_SIGNAL_CAPTURE_PCT = 90.0
MIN_EXIT_CAPTURE_PCT = 90.0
MIN_BOTH_SIDE_PCT = 90.0
MAX_ORPHANS = 0
MIN_FUT_FRESHNESS_PCT = 90.0


def _sessions_of(rows: list[dict], key: str = "signal_ts") -> list[str]:
    out: list[str] = []
    for r in rows:
        ts = r.get(key) or r.get("ts") or r.get("capture_ts")
        if isinstance(ts, (int, float)):
            day = p17capture.ist_parts(float(ts))[0]
            if day and day not in out:
                out.append(day)
    return sorted(out)


def _within(rows: list[dict], keep: set[str] | None, key: str = "signal_ts") -> list[dict]:
    """Rows belonging to a given set of IST sessions. None keeps everything.

    Every table is filtered against the SAME session set, taken from the
    observations, so a window cannot contain two sessions of capture and a
    different session of paper rows.
    """
    if keep is None:
        return rows
    out = []
    for r in rows:
        ts = r.get(key) or r.get("ts") or r.get("capture_ts")
        if isinstance(ts, (int, float)) and p17capture.ist_parts(float(ts))[0] in keep:
            out.append(r)
    return out


def _pct(hit: int, total: int) -> float | None:
    return round(100.0 * hit / total, 2) if total else None


def verdict_of(value: float | None, minimum: float, *, lower_is_better: bool = False) -> str:
    """PASS / FAIL / NO DATA. An unmeasured line is never a pass."""
    if value is None:
        return NODATA
    if lower_is_better:
        return PASS if value <= minimum else FAIL
    return PASS if value >= minimum else FAIL


def live_health(timeout: float = 1.5) -> dict | None:
    """Futures book counters from the running backend, if there is one.

    The refusal counters live in the engine's memory, not on disk, so a fresh CLI
    process cannot see them: it would report "no valid futures plan occurred"
    for a session that in fact refused fifty plans as stale. Read them from the
    local read-only endpoint while the app is up; when it is down, say so rather
    than printing zeros as if they were measurements.
    """
    url = f"http://127.0.0.1:{settings.port}/api/phase19/futures-paper"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    health = payload.get("health")
    return health if isinstance(health, dict) else None


def futures_freshness_pct(rows: list[dict], health: dict | None) -> float | None:
    """Share of futures paper entries priced off a fresh feed.

    While the app is up this is expressed against attempts refused for
    staleness — how much of the feed was usable, the futures analogue of the
    option capture rate. Offline, it falls back to the feed age each resolved
    row recorded for itself.
    """
    if health is not None:
        skipped = health.get("skipped")
        stale = 0
        if isinstance(skipped, dict):
            stale = int(skipped.get(futbook.SKIP_STALE, 0))
        entries = int(health.get("entries") or 0)
        if entries + stale:
            return round(100.0 * entries / (entries + stale), 2)
    ages = [
        float(r["entry_feed_age_sec"]) for r in rows
        if isinstance(r.get("entry_feed_age_sec"), (int, float))
    ]
    if not ages:
        return None
    fresh = sum(1 for a in ages if a <= futbook.MAX_FEED_AGE_SEC)
    return round(100.0 * fresh / len(ages), 2)


def lifecycle_verdict(resolved: int, plans: int) -> str:
    """Did the futures paper lifecycle run end to end?

    A book that saw no valid plan is NO DATA, not a failure: on a day the market
    offers no futures setup there is nothing to enter. A book that saw plans and
    resolved nothing is a failure, because something between plan and resolution
    is dropping trades.
    """
    if resolved > 0:
        return PASS
    return NODATA if plans == 0 else FAIL


def exit_capture(paper_rows: list[dict]) -> dict:
    """Resolved legs that exited against a real bid, not a last-traded fallback."""
    resolved = [r for r in paper_rows if r.get("exit_ts")]
    on_bid = sum(1 for r in resolved if r.get("exit_side") == "BID")
    fallback: dict[str, int] = {}
    for r in resolved:
        side = str(r.get("exit_side") or "UNKNOWN")
        if side != "BID":
            fallback[side] = fallback.get(side, 0) + 1
    return {
        "resolved_legs": len(resolved),
        "exited_on_bid": on_bid,
        "pct": _pct(on_bid, len(resolved)),
        "fallback_sides": fallback,
    }


def orphans(observations: list[dict], paper_rows: list[dict]) -> dict:
    """Lifecycle rows that cannot be accounted for.

    Two ways the pipeline can lose a trade, both of which bias the surviving
    sample: a paper row whose observation was never written, and a row that
    resolved without an exit price.
    """
    obs_ids = {str(o.get("observation_id")) for o in observations}
    # Only sessions that also have observations can be reconciled. A paper row
    # from a day whose observation file was rotated away is not evidence of a
    # lost trade, and counting it as one would make the gate cry wolf.
    obs_sessions = set(_sessions_of(observations, "signal_ts"))
    reconcilable = [
        r for r in paper_rows
        if isinstance(r.get("signal_ts"), (int, float))
        and p17capture.ist_parts(float(r["signal_ts"]))[0] in obs_sessions
    ]
    unmatched = [
        str(r.get("episode_id"))
        for r in reconcilable
        if str(r.get("observation_id")) not in obs_ids
    ]
    resolved_without_exit = [
        str(r.get("episode_id"))
        for r in paper_rows
        if r.get("exit_ts") and not isinstance(r.get("exit_price"), (int, float))
    ]
    return {
        "paper_rows_without_observation": len(unmatched),
        "resolved_without_exit_price": len(resolved_without_exit),
        "total": len(unmatched) + len(resolved_without_exit),
        "paper_rows_reconcilable": len(reconcilable),
        "paper_rows_outside_observed_sessions": len(paper_rows) - len(reconcilable),
        "examples": (unmatched + resolved_without_exit)[:5],
    }


def build(sessions: int | None) -> dict:
    all_obs = p17store.observations()
    present = _sessions_of(all_obs, "signal_ts")
    keep = set(present[-sessions:]) if sessions else None
    observations = _within(all_obs, keep, "signal_ts")
    paper_rows = _within(p17store.paper(), keep, "signal_ts")

    cap = p17reports.capture_report(observations)
    sel = cap.get("selected_side") or {}
    counts = sel.get("counts") or {}
    total_q = int(sel.get("total") or 0)
    # "EXACT/NEAR_EXACT" in Checkpoint 1 is this phase's EXACT (<=2s) plus GOOD
    # (<=10s). DEGRADED is a minute-old book — recorded, never counted as a
    # captured spread.
    near = int(counts.get(p17quality.EXACT, 0)) + int(counts.get(p17quality.GOOD, 0))
    signal_pct = _pct(near, total_q)
    exits = exit_capture(paper_rows)
    orph = orphans(observations, paper_rows)

    fut_rows = _within(futbook.read_log(), keep, "entry_ts")
    fut_health = live_health()
    online = fut_health is not None
    fut_freshness = futures_freshness_pct(fut_rows, fut_health)
    resolved_fut = len(fut_rows)
    plans = int((fut_health or {}).get("plans_seen") or 0) if online else resolved_fut
    entries = int((fut_health or {}).get("entries") or 0) if online else resolved_fut

    lines = [
        {
            "measurement": "option signal-time EXACT/NEAR_EXACT capture",
            "value": signal_pct,
            "unit": "%",
            "minimum": MIN_SIGNAL_CAPTURE_PCT,
            "verdict": verdict_of(signal_pct, MIN_SIGNAL_CAPTURE_PCT),
            "detail": (
                f"{counts.get(p17quality.EXACT, 0)} exact + "
                f"{counts.get(p17quality.GOOD, 0)} near of {total_q} candidates; "
                f"median gap {cap.get('median_signal_to_snapshot_ms')} ms; "
                f"sources {cap.get('sources')}"
            ),
        },
        {
            "measurement": "option exit-time usable capture (exited on a real BID)",
            "value": exits["pct"],
            "unit": "%",
            "minimum": MIN_EXIT_CAPTURE_PCT,
            "verdict": verdict_of(exits["pct"], MIN_EXIT_CAPTURE_PCT),
            "detail": (
                f"{exits['exited_on_bid']} of {exits['resolved_legs']} resolved legs"
                + (f"; fell back to {exits['fallback_sides']}" if exits["fallback_sides"] else "")
            ),
        },
        {
            "measurement": "CE + PE same-timestamp coverage",
            "value": cap.get("both_side_pct"),
            "unit": "%",
            "minimum": MIN_BOTH_SIDE_PCT,
            "verdict": verdict_of(cap.get("both_side_pct"), MIN_BOTH_SIDE_PCT),
            "detail": (
                f"{cap.get('both_side_matches', 0)} of {cap.get('total_candidates', 0)} "
                "candidates captured on both sides at the same instant"
            ),
        },
        {
            "measurement": "unexplained missing / orphan lifecycle rows",
            "value": float(orph["total"]),
            "unit": "rows",
            "minimum": float(MAX_ORPHANS),
            "verdict": verdict_of(float(orph["total"]), float(MAX_ORPHANS),
                                lower_is_better=True),
            "detail": (
                f"{orph['paper_rows_without_observation']} paper rows without an "
                f"observation, {orph['resolved_without_exit_price']} resolved "
                f"without an exit price, over "
                f"{orph['paper_rows_reconcilable']} reconcilable rows "
                f"({orph['paper_rows_outside_observed_sessions']} rows fall "
                "outside the observed sessions and are not judged)"
            ),
        },
        {
            "measurement": "futures feed freshness at paper entry",
            "value": fut_freshness,
            "unit": "%",
            "minimum": MIN_FUT_FRESHNESS_PCT,
            "verdict": verdict_of(fut_freshness, MIN_FUT_FRESHNESS_PCT),
            "detail": (
                f"entered within {futbook.MAX_FEED_AGE_SEC:g}s of the feed; "
                + (
                    f"refusals {(fut_health or {}).get('skipped') or {}} "
                    "(live counters, whole process, not session-filtered)"
                    if online else
                    f"backend not reachable on port {settings.port} — measured "
                    "from the feed age each resolved row recorded"
                )
            ),
        },
        {
            "measurement": "futures paper lifecycle end-to-end",
            "value": float(resolved_fut) if plans else None,
            "unit": "resolved",
            "minimum": 1.0,
            "verdict": lifecycle_verdict(resolved_fut, plans),
            "detail": (
                (
                    f"plans {plans} · entries {entries} · exits "
                    f"{(fut_health or {}).get('exits')} · resolved {resolved_fut} "
                    f"· open {(fut_health or {}).get('open')}"
                    if online else
                    f"resolved rows on disk {resolved_fut} (backend down — plan "
                    "and refusal counters are in-memory and unreadable from here)"
                )
                + (
                    "  (no valid futures plan occurred — nothing to enter)"
                    if online and plans == 0 else ""
                )
            ),
        },
    ]

    failed = [ln["measurement"] for ln in lines if ln["verdict"] == FAIL]
    nodata = [ln["measurement"] for ln in lines if ln["verdict"] == NODATA]
    if failed:
        verdict = "CAPTURE_BROKEN"
        advice = (
            "Stop and fix capture. Do not read the vehicle, CE/PE or expectancy "
            "tables: they describe only the fraction of trades that were recorded."
        )
    elif nodata:
        verdict = "INCOMPLETE"
        advice = (
            "Nothing has failed, but not everything has been measured yet. Run "
            "another full session before drawing a conclusion."
        )
    else:
        verdict = "CAPTURE_HEALTHY"
        advice = (
            "Checkpoint 1 met. Checkpoint 2 is a volume question: keep sessions "
            "running to 300-500 resolved option legs before reading vehicle "
            "economics, 30+ resolved futures trades for the first Option-vs-"
            "Futures comparison, and note that A+ and CAS run on their own, "
            "longer clocks."
        )

    return {
        "checkpoint": 1,
        "question": "Is the recorder working?",
        "sessions_requested": sessions,
        "sessions_present": present,
        "sessions_measured": _sessions_of(observations, "signal_ts"),
        "backend_online": online,
        "lines": lines,
        "failed": failed,
        "unmeasured": nodata,
        "verdict": verdict,
        "advice": advice,
        "orphan_detail": orph,
        "note": (
            "Checkpoint 1 deliberately reports no performance figure. It says "
            "whether the data is trustworthy, not whether the strategy works."
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sessions", type=int, default=None,
                    help="measure only the last N recorded sessions (default: all)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = ap.parse_args()

    result = build(args.sessions)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    print("CHECKPOINT 1 — is the recorder working?")
    measured = result["sessions_measured"]
    print(f"sessions measured: {', '.join(measured) if measured else 'none'}"
          f"   (on disk: {len(result['sessions_present'])})")
    print(f"capture target: {p17quality.EXACT_RATE_TARGET_PCT:.0f}% exact/near")
    print("backend: " + ("running (live futures counters read)"
                         if result["backend_online"] else
                         "not reachable (futures counters from disk only)") + "\n")
    for ln in result["lines"]:
        value = ln["value"]
        shown = "—" if value is None else (
            f"{value:.2f}{ln['unit']}" if ln["unit"] == "%" else f"{value:.0f} {ln['unit']}"
        )
        print(f"[{ln['verdict']:<7}] {ln['measurement']}")
        print(f"           {shown}  (min {ln['minimum']:g}{'%' if ln['unit'] == '%' else ''})")
        print(f"           {ln['detail']}")
    print(f"\nVERDICT: {result['verdict']}")
    print(result["advice"])


if __name__ == "__main__":
    main()
