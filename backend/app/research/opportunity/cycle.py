"""§12 — one market-wide cycle, and the status of the funnel between cycles.

A cycle is the whole funnel in order: generate, screen, shadow, journal, then
read the promotion gates and the ranking. Each stage narrows the set, and each
stage reports what it could not look at alongside what it did, because a funnel
that silently drops the unmeasurable ends up describing two instruments and
calling it the market.

The stages are ordered so that nothing expensive runs on something cheap could
have eliminated: a candidate rejected by the historical screen never reaches
the live shadow stage, and one that never produced a priceable shadow signal
never reaches the paper journal. That ordering is the only reason a wide search
is affordable at all.

Running a cycle twice over the same data converges rather than doubling:
candidate registration is idempotent on the definition fingerprint, shadow rows
are keyed on candidate and observation, and journal legs are keyed on candidate
and observation too.
"""
from __future__ import annotations

import datetime as dt
import zoneinfo

from app.research.opportunity import (
    DISCOVERY,
    HISTORICAL_TESTING,
    SCHEMA_VERSION,
    SHADOW,
    STANDING_LIMITS,
    VERSION,
)
from app.research.opportunity import (
    generator,
    paper,
    promotion,
    ranking,
    registry,
    screen,
    shadow,
    store,
    universe,
)
from app.research.opportunity import CANDIDATE_FILE, JOURNAL_FILE, SHADOW_FILE

_IST = zoneinfo.ZoneInfo("Asia/Kolkata")


def run(*, stride: int = screen.DEFAULT_STRIDE,
        screen_limit: int | None = None,
        shadow_limit: int | None = None,
        session: str | None = None,
        skip_generate: bool = False,
        remeasure: bool = False) -> dict:
    """One full pass through the funnel. Paper only, start to finish."""
    started = dt.datetime.now(_IST)
    gen = ({"skipped": True} if skip_generate
           else generator.generate())
    scr = screen.run(stride=stride, limit=screen_limit, remeasure=remeasure)
    sha = shadow.run(session=session, limit=shadow_limit)
    pap = paper.run(session=session)
    prom = promotion.report()
    rank = ranking.rank()
    finished = dt.datetime.now(_IST)
    return {
        "version": VERSION,
        "schema_version": SCHEMA_VERSION,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "elapsed_sec": (finished - started).total_seconds(),
        "stages": {
            "1_generate": gen,
            "2_historical_screen": scr,
            "3_live_shadow": sha,
            "4_paper_journal": pap,
            "5_promotion": {
                "evaluated": prom["evaluated"],
                "promotable": prom["promotable"],
                "verdict": prom["verdict"],
                "champion": prom["champion"],
            },
            "6_market_ranking": {
                "verdict": rank["verdict"],
                "opportunities": rank["opportunities"],
                "ranked": len(rank["ranked"]),
                "unranked": len(rank["unranked"]),
            },
        },
        "standing_limits": list(STANDING_LIMITS),
    }


def status() -> dict:
    """Where the funnel currently stands, without running anything."""
    counts = registry.counts()
    funnel = universe.funnel()
    scr = screen.results()
    sha = shadow.observations()
    journal = store.read(JOURNAL_FILE)
    resolved = [r for r in journal if r.get("resolved")]
    prom = promotion.report()
    return {
        "version": VERSION,
        "schema_version": SCHEMA_VERSION,
        "candidates_by_status": counts,
        "candidates_total": sum(counts.values()),
        "universe": {
            "requested": len(funnel["requested"]["index"])
            + len(funnel["requested"]["mcx"]),
            "requested_note": funnel["requested"]["equity_and_cash"],
            "screenable": funnel["tier_1_screenable"],
            "no_history": funnel["no_history_count"],
            "not_screenable": [
                {"instrument": r["instrument"], "absence": r.get("status")}
                for r in funnel["not_screenable"]
            ],
        },
        "historical_screen": {
            "rows": len(scr),
            "rejected": sum(1 for r in scr if r.get("kill_reason")),
            "not_screenable": sum(1 for r in scr if not r.get("screenable")),
            "eligible_for_shadow": len(registry.by_status(SHADOW)),
            "awaiting_screen": sum(
                1 for c in registry.candidates()
                if c.get("status") in (DISCOVERY, HISTORICAL_TESTING)),
        },
        "live_shadow": {
            "rows": len(sha),
            "signals": sum(1 for r in sha if r.get("verdict") == shadow.SIGNAL),
            "unpriced": sum(1 for r in sha if r.get("verdict") == shadow.UNPRICED),
            "evidence": _counts(sha, "evidence"),
        },
        "paper": {
            "legs": len(journal),
            "resolved": len(resolved),
            "sessions": len({str(r.get("session")) for r in resolved}),
            "candidates_with_legs": len(
                {str(r.get("candidate_id")) for r in journal}),
        },
        "promotion": {
            "verdict": prom["verdict"],
            "promotable": prom["promotable"],
            "champion": prom["champion"],
        },
        "files": [store.integrity(f)
                  for f in (CANDIDATE_FILE, SHADOW_FILE, JOURNAL_FILE)],
        "standing_limits": list(STANDING_LIMITS),
        "generated_at": dt.datetime.now(_IST).isoformat(),
    }


def _counts(rows: list[dict], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        value = r.get(field)
        if value is None:
            continue
        out[str(value)] = out.get(str(value), 0) + 1
    return out
