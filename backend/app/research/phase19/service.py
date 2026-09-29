"""Phase 19 assembly — the futures paper observer, readiness and calibration.

This is the only module in Phase 19 that the running app touches. It does three
things and deliberately nothing else:

* :func:`observe_futures` — steps the futures paper book on a tick, failure
  isolated so a research book can never break a live tick;
* :func:`readiness` — the three strategies restated in one shape, computed from
  the evidence already on disk. It reuses Phase 17's OOS split and Phase 18's own
  CAS verdict rather than re-deriving either, so this panel cannot disagree with
  the reports it summarises;
* :func:`calibration` — the T1 harness over resolved option paper rows.

Nothing here writes a decision, and nothing production reads it.
"""
from __future__ import annotations

from app.models import FuturesSignalCard
from app.research.phase17 import capture as p17cap
from app.research.phase17 import oos as p17oos
from app.research.phase17 import quality as p17quality
from app.research.phase17 import reports as p17reports
from app.research.phase17 import store as p17store
from app.research.phase18 import quality as p18quality
from app.research.phase18 import store as p18store
from app.research.phase18 import validation as p18validation
from app.research.phase19 import calibration as calib
from app.research.phase19 import futbook, futcosts, readiness as ready

A_PLUS = "A_PLUS"


def observe_futures(
    instrument: str,
    card: object,
    price: float | None,
    *,
    now: float | None = None,
    feed_age_sec: float | None = None,
) -> None:
    """Tick hook. Paper only — there is no order path anywhere below this call."""
    if card is not None and not isinstance(card, FuturesSignalCard):
        return
    futbook.observe(
        instrument, card, price, now=now, feed_age_sec=feed_age_sec,
    )


def close_futures_session(instrument: str, price: float | None,
                          now: float | None = None) -> None:
    futbook.close_session(instrument, price, now=now)


def _with_session(rows: list[dict]) -> list[dict]:
    """Phase 17 paper rows carry timestamps, not a session label."""
    out: list[dict] = []
    for row in rows:
        rec = dict(row)
        if not rec.get("session"):
            ts = rec.get("entry_ts") or rec.get("signal_ts")
            if isinstance(ts, (int, float)):
                rec["session"] = p17cap.ist_parts(float(ts))[0]
        out.append(rec)
    return out


def _futures_trades(rows: list[dict]) -> list[dict]:
    """Futures paper rows in the shape the fold modules expect.

    ``COST_MODELLED`` rows are kept, unlike the option book. The futures feed
    publishes no depth, so requiring a measured spread would discard the entire
    sample and report zero forever; the row's ``cost_status`` travels with it and
    the readiness panel says the net is optimistic by one spread.
    """
    out: list[dict] = []
    for row in rows:
        r = row.get("net_r")
        session = row.get("session")
        if not isinstance(r, (int, float)) or not isinstance(session, str):
            continue
        if r != r or r in (float("inf"), float("-inf")):
            continue
        outcome = row.get("outcome")
        out.append({
            "r": float(r),
            "exit_reason": "TARGET" if outcome in (
                futbook.T1, futbook.T2, futbook.T3
            ) else str(outcome or "UNKNOWN"),
            "session": session,
            "entry_ts": row.get("entry_ts"),
            "instrument": row.get("instrument"),
            "cost_status": row.get("cost_status"),
        })
    out.sort(key=lambda t: (t["session"], str(t.get("entry_ts") or "")))
    return out


def futures_evaluation(rows: list[dict] | None = None) -> dict:
    """Chronological dev/validation/holdout over the futures paper book."""
    src = futbook.read_log() if rows is None else rows
    trades = _futures_trades(src)
    parts = p17oos.split(trades)
    measured = sum(
        1 for t in trades if t.get("cost_status") == futcosts.COST_MEASURED
    )
    return {
        "development": p17oos.summarise(parts["development"]),
        "validation": p17oos.summarise(parts["validation"]),
        "holdout": p17oos.summarise(parts["holdout"]),
        "split": {
            k: v for k, v in parts.items()
            if k in ("sessions", "session_bounds", "status", "note")
        },
        "rows_considered": len(src),
        "rows_excluded_uncosted": len(src) - len(trades),
        "rows_with_measured_spread": measured,
        "basis": "NET_OF_CHARGED_COSTS",
        "note": (
            "Rows without a two-sided futures book are charged brokerage, "
            "statutory fees and slippage but no spread, and are marked "
            "COST_MODELLED. Their net is optimistic by one spread."
        ),
    }


def _futures_freshness_pct(rows: list[dict], health: dict) -> float | None:
    """Share of futures paper entries priced off a fresh feed.

    Every entry already passed the staleness refusal, so this is expressed
    against refused-for-staleness attempts: it answers "how much of the feed was
    usable", which is the futures analogue of the option capture rate.
    """
    skipped = health.get("skipped")
    stale = 0
    if isinstance(skipped, dict):
        stale = int(skipped.get(futbook.SKIP_STALE, 0))
    entries = int(health.get("entries") or 0)
    total = entries + stale
    if not total:
        # The counters are per-process. After a restart, fall back to what the
        # resolved rows themselves recorded, so freshness stays answerable.
        ages = [
            float(r["entry_feed_age_sec"]) for r in rows
            if isinstance(r.get("entry_feed_age_sec"), (int, float))
        ]
        if not ages:
            return None
        fresh = sum(1 for a in ages if a <= futbook.MAX_FEED_AGE_SEC)
        return round(100.0 * fresh / len(ages), 1)
    return round(100.0 * entries / total, 1)


def _cas_quality_states(observations: list[dict]) -> list[str]:
    """Both sides of every ladder rung, so a missing quote counts as missing."""
    out: list[str] = []
    for obs in observations:
        ladder = obs.get("ladder")
        if not isinstance(ladder, list):
            continue
        for rung in ladder:
            if not isinstance(rung, dict):
                continue
            for side in ("ce", "pe"):
                quote = rung.get(side)
                out.append(
                    str(quote.get("data_quality")) if isinstance(quote, dict)
                    else p18quality.MISSING
                )
    return out


def _capture_pct(observations: list[dict]) -> float | None:
    rate = p17reports.capture_report(observations).get("exact_match_rate_pct")
    return float(rate) if isinstance(rate, (int, float)) else None


def calibration(rows: list[dict] | None = None) -> dict:
    """T1 calibration over resolved option paper rows."""
    src = _with_session(p17store.paper() if rows is None else rows)
    return calib.evaluate(calib.to_rows(src))


def readiness() -> dict:
    """The Production Readiness panel payload. Read-only, informational."""
    observations = p17store.observations()
    paper_rows = _with_session(p17store.paper())
    a_plus = [r for r in paper_rows if r.get("a_plus_label") == A_PLUS]
    option_oos = p17oos.evaluate(a_plus, baseline_rows=paper_rows)
    capture_pct = _capture_pct(observations)

    fut_rows = futbook.read_log()
    fut_health = futbook.health()
    fut_oos = futures_evaluation(fut_rows)

    cas_rows = p18store.paper()
    cas_obs = p18store.observations()
    cas_match = p18quality.match_rate_pct(_cas_quality_states(cas_obs))
    cas_verdict = p18validation.verdict(cas_rows, match_pct=cas_match)

    rows = [
        ready.option_row(
            option_oos,
            capture_pct=capture_pct,
            last_validation=_last_session(paper_rows),
        ),
        ready.futures_row(
            fut_oos,
            freshness_pct=_futures_freshness_pct(fut_rows, fut_health),
            plans=int(fut_health.get("plans_seen") or 0),
            entries=int(fut_health.get("entries") or 0),
            resolved=len(fut_rows),
            last_validation=_last_session(fut_rows),
        ),
        ready.cas_row(
            cas_verdict,
            capture_pct=cas_match,
            resolved=len(cas_rows),
            last_validation=_last_session(cas_rows),
        ),
    ]
    board = ready.board(rows, calibration=calibration(paper_rows))
    board["capture"] = {
        "option_exact_near_pct": capture_pct,
        "option_target_pct": p17quality.EXACT_RATE_TARGET_PCT,
        "cas_match_pct": cas_match,
        "futures_freshness_pct": _futures_freshness_pct(fut_rows, fut_health),
    }
    return board


def _last_session(rows: list[dict]) -> str | None:
    sessions = sorted(
        {str(r.get("session")) for r in rows if isinstance(r.get("session"), str)}
    )
    return sessions[-1] if sessions else None


def futures_board() -> dict:
    """Futures paper book for the panel: health, open trades, resolved rows."""
    rows = futbook.read_log()
    health = futbook.health()
    return {
        "health": health,
        "freshness_pct": _futures_freshness_pct(rows, health),
        "open": futbook.open_positions(),
        "resolved": rows[-100:],
        "evaluation": futures_evaluation(rows),
        "paper_only": True,
        "no_real_order": True,
        "banner": "RESEARCH / PAPER ONLY — NO ORDER PATH",
    }
