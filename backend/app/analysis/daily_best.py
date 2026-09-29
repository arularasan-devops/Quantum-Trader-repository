"""One signal a day, and how often it reached the target. Read-only.

This answers a single question: **if the tool had taken only one trade a day,
what would the record look like?** It is a record, not a rule — nothing here
suppresses a signal, sizes a position or changes what the board shows. It reads
``signal_journal.jsonl`` for the card as it was written and
``signal_outcomes.jsonl`` for how it resolved, and counts.

Four things are deliberate.

* **the day is selected on what was known at the time.** A policy may rank by the
  score on the card, or take the first call of the day — never by the outcome.
  Picking the day's best *result* is not a policy, it is hindsight, and it is the
  easiest way to make a losing book look like a winning one.
* **more than one policy is reported, because ranking is not validated.** The
  five-year study found the composite score does not sort outcomes (the lowest
  score band beat the highest). So ``TOP_SCORE`` is shown *beside*
  ``FIRST_OF_DAY``, and if the two records are the same then the ranking added
  nothing. Calling the top-scoring card "the best of the day" is a claim this
  evidence does not support.
* **a rate with no denominator is not a rate.** Under ``MIN_SESSIONS`` sessions
  every summary is stamped ``REQUIRES_MORE_DATA``, and a day with no resolved
  signal is a ``NO_TRADE`` day that stays in the day count — dropping empty days
  is how a 35%-empty schedule starts reading as one trade every day.
* **R is gross.** These are premium-to-premium outcomes. Brokerage, taxes and the
  spread are not charged here, and on the measured Flow legs cost was several
  times the gross travel — so a positive number in this table is not yet a
  profitable one.
"""
from __future__ import annotations

from statistics import median

from app.analysis import signal_journal as sj

MIN_SESSIONS = 20
ENOUGH = "MEASURED"
THIN = "REQUIRES_MORE_DATA"

TOP_SCORE = "TOP_SCORE"
TOP_CONVICTION = "TOP_CONVICTION"
FIRST_OF_DAY = "FIRST_OF_DAY"
POLICIES = (TOP_SCORE, TOP_CONVICTION, FIRST_OF_DAY)

_REACHED = ("T1", "T2", "T3")


def _rate(hits: int, total: int) -> float | None:
    return None if total <= 0 else round(100.0 * hits / total, 1)


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _index(journal: list[dict]) -> dict[str, dict]:
    """Card-as-written by every id it can be joined on, oldest write winning.

    The first journalled row for a signal is the card the call was made on; a
    later row for the same id is the same signal re-observed, and taking that one
    would rank the day on information the trade did not have.
    """
    out: dict[str, dict] = {}
    for row in journal:
        for key in ("market_signal_id", "global_signal_id", "signal_id"):
            sid = row.get(key)
            if isinstance(sid, str) and sid and sid not in out:
                out[sid] = row
    return out


def _card(res: dict, index: dict[str, dict]) -> dict | None:
    for key in ("market_signal_id", "global_signal_id", "signal_id"):
        sid = res.get(key)
        if isinstance(sid, str) and sid in index:
            return index[sid]
    return None


def _reached(res: dict) -> list[str]:
    got = res.get("targets_reached")
    keys = got.keys() if isinstance(got, dict) else ()
    return [t for t in _REACHED if t in keys]


def candidate(res: dict, index: dict[str, dict]) -> dict:
    """One resolved signal flattened to what a day's record needs."""
    card = _card(res, index) or {}
    info = card.get("signal_info") or {}
    hit = _reached(res)
    return {
        "session": res.get("session"),
        "signal_id": res.get("signal_id"),
        "instrument": res.get("instrument"),
        "symbol": res.get("symbol"),
        "direction": res.get("direction"),
        "signal_time_ist": res.get("signal_time_ist"),
        "ts": _num(res.get("signal_ts")),
        "signal_score": _num(info.get("signal_score")),
        "conviction": _num(info.get("conviction_meter")),
        "trade_quality": info.get("trade_quality"),
        "a_plus_shadow": res.get("a_plus_shadow"),
        "entry": _num(res.get("entry")),
        "stop": _num(res.get("stop")),
        "outcome": res.get("outcome"),
        "targets_reached": hit,
        "best_target": hit[-1] if hit else None,
        "reached_t1": bool(hit),
        "realized_r": _num(res.get("realized_r")),
        "mfe_r": _num(res.get("mfe_r")),
        "hold_minutes": _num(res.get("hold_minutes")),
        "minutes_to_t1": _num(res.get("minutes_to_t1")),
        "card_found": bool(card),
        "gross_only": True,
    }


def _pick(rows: list[dict], policy: str) -> dict | None:
    """The day's one signal under ``policy``, chosen only on entry-time facts.

    Ties and a missing score both fall back to the earliest call of the day
    rather than to the better result, so a card with no score can never be
    promoted by how it happened to end.
    """
    if not rows:
        return None
    order = sorted(rows, key=lambda r: (r["ts"] is None, r["ts"] or 0.0))
    if policy == FIRST_OF_DAY:
        return order[0]
    key = "signal_score" if policy == TOP_SCORE else "conviction"
    scored = [r for r in order if r[key] is not None]
    if not scored:
        return None
    best = max(r[key] for r in scored)
    return next(r for r in scored if r[key] == best)


def _summary(picks: list[dict], sessions: int, unrankable: int) -> dict:
    graded = [p for p in picks if p["outcome"]]
    hits = [p for p in graded if p["reached_t1"]]
    rs = [p["realized_r"] for p in graded if p["realized_r"] is not None]
    return {
        "sessions": sessions,
        "days_with_a_signal": len(picks),
        "days_no_card_to_rank": unrankable,
        "graded": len(graded),
        "reached_t1": len(hits),
        "reached_t1_pct": _rate(len(hits), len(graded)),
        "reached_t2_pct": _rate(sum(1 for p in graded if "T2" in p["targets_reached"]),
                                len(graded)),
        "reached_t3_pct": _rate(sum(1 for p in graded if "T3" in p["targets_reached"]),
                                len(graded)),
        "stopped": sum(1 for p in graded if p["outcome"] == "STOP"),
        "expired": sum(1 for p in graded if p["outcome"] == "EXPIRED"),
        "sum_r_gross": round(sum(rs), 3) if rs else None,
        "mean_r_gross": round(sum(rs) / len(rs), 3) if rs else None,
        "median_r_gross": round(median(rs), 3) if rs else None,
        "evidence": ENOUGH if sessions >= MIN_SESSIONS else THIN,
        "sessions_required": MIN_SESSIONS,
        "sessions_shortfall": max(0, MIN_SESSIONS - sessions),
        "gross_only": True,
    }


def report(limit_days: int = 120) -> dict:
    """The one-a-day record under each selection policy, plus the whole book.

    ``every_signal`` is the comparator that stops this being a story: it is the
    same resolved signals with no daily cap. If one-a-day reads better, the gap
    is the only thing that argues for taking fewer trades — and on this many
    sessions that gap is still noise.
    """
    resolved = [r for r in sj.resolutions() if r.get("session")]
    index = _index(sj.read_journal(limit=None))
    rows = [candidate(r, index) for r in resolved]

    by_day: dict[str, list[dict]] = {}
    for row in rows:
        by_day.setdefault(str(row["session"]), []).append(row)
    days = sorted(by_day)[-limit_days:]

    policies = {}
    for policy in POLICIES:
        picks, unrankable = [], 0
        for day in days:
            got = _pick(by_day[day], policy)
            if got is None:
                # The day had resolved signals but none carried the field this
                # policy ranks on. That is a gap in the record, not a quiet day,
                # so it is counted separately from a day the market gave nothing.
                unrankable += 1
                continue
            picks.append({**got, "policy": policy})
        policies[policy] = {
            "history": picks[::-1],
            "summary": _summary(picks, len(days), unrankable),
        }

    graded_all = [r for r in rows if r["outcome"]]
    hits_all = [r for r in graded_all if r["reached_t1"]]
    rs_all = [r["realized_r"] for r in graded_all if r["realized_r"] is not None]

    top = policies[TOP_SCORE]["summary"]["reached_t1_pct"]
    first = policies[FIRST_OF_DAY]["summary"]["reached_t1_pct"]
    return {
        "policies": policies,
        "every_signal": {
            "sessions": len(days),
            "graded": len(graded_all),
            "reached_t1": len(hits_all),
            "reached_t1_pct": _rate(len(hits_all), len(graded_all)),
            "signals_per_day": (round(len(rows) / len(days), 2) if days else None),
            "sum_r_gross": round(sum(rs_all), 3) if rs_all else None,
            "mean_r_gross": (round(sum(rs_all) / len(rs_all), 3) if rs_all else None),
        },
        "ranking_validated": False,
        "notes": [
            "a record of what one trade a day would have done, not a rule: "
            "nothing here suppresses, sizes or routes a signal",
            "the day's signal is chosen on the card as it was written — never on "
            "how it resolved",
            "TOP_SCORE is shown beside FIRST_OF_DAY because the five-year study "
            "found the composite score does not sort outcomes; if the two records "
            "match, the ranking added nothing",
            "R is premium-to-premium and gross: brokerage, taxes and the spread "
            "are not charged here, and on measured Flow legs cost ran several "
            "times the gross travel",
            "no probability is published from this table — a rate over "
            f"{MIN_SESSIONS} sessions is a count, not a forecast",
        ],
        "ranking_verdict": (
            "RANK_NOT_VALIDATED — score-ranked and first-of-day are within noise"
            if top is not None and first is not None and abs(top - first) < 10.0
            else "RANK_NOT_VALIDATED — not enough sessions to compare policies"
        ),
        "research_only": True,
    }
