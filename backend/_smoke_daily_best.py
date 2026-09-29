#!/usr/bin/env python
"""Does the one-a-day record pick on entry-time facts and count honestly?

    .venv/bin/python _smoke_daily_best.py

Writes a throwaway journal and outcome ledger into a temporary data directory, so
every assertion is about the selection and the arithmetic rather than about
whatever happens to be in the real book. The hard one is hindsight: the day must
be selected on the card as written even when a worse-scoring card on the same day
ended better.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.analysis import daily_best as db
from app.analysis import signal_journal as sj
from app.config import settings

CHECKS = 0


def ok(cond: bool, what: str) -> None:
    global CHECKS
    CHECKS += 1
    assert cond, what


def card(sid: str, score: float, conviction: float | None, ts: int) -> dict:
    return {
        "signal_id": sid, "market_signal_id": sid, "session": "IGNORED",
        "ts": ts, "board_action": "BUY",
        "signal_info": {"signal_score": score, "conviction_meter": conviction,
                        "trade_quality": "B"},
    }


def resolved(sid: str, session: str, ts: int, *, outcome: str,
             targets: tuple[str, ...] = (), realized: float | None = None,
             **kw) -> dict:
    row = {
        "event": "RESOLVED", "signal_id": sid, "market_signal_id": sid,
        "session": session, "signal_ts": ts, "instrument": "NIFTY",
        "symbol": "NIFTY23850CE", "direction": "BULLISH", "outcome": outcome,
        "targets_reached": {t: {"at": ts + 60} for t in targets},
        "realized_r": realized, "mfe_r": 1.0, "entry": 100.0, "stop": 90.0,
    }
    row.update(kw)
    return row


def write(tmp: Path, journal: list[dict], outcomes: list[dict]) -> None:
    (tmp / "signal_journal.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in journal), encoding="utf-8")
    (tmp / "signal_outcomes.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in outcomes), encoding="utf-8")
    sj._read_cache.clear()


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        keep = settings.data_dir
        settings.data_dir = str(tmp)
        try:
            # Day one: the high-scoring card stops out while a low-scoring card on
            # the same day runs to T3. Day two: one card, reaches T1 only.
            write(
                tmp,
                [card("a", 88.0, 70.0, 1000), card("b", 21.0, 30.0, 1100),
                 card("c", 55.0, 60.0, 2000)],
                [resolved("a", "2026-01-01", 1000, outcome="STOP",
                          realized=-1.0),
                 resolved("b", "2026-01-01", 1100, outcome="T3",
                          targets=("T1", "T2", "T3"), realized=2.5),
                 resolved("c", "2026-01-02", 2000, outcome="EXPIRED",
                          targets=("T1",), realized=0.4)],
            )
            rep = db.report()
            top = rep["policies"][db.TOP_SCORE]
            first = rep["policies"][db.FIRST_OF_DAY]

            ok([p["signal_id"] for p in top["history"]] == ["c", "a"],
               "the day is picked on the score written on the card, so day one is "
               "the card that stopped out — not the one that happened to win")
            ok(top["summary"]["sum_r_gross"] == -0.6,
               "and the record carries that loss instead of the hindsight winner")
            ok(top["history"][0]["session"] == "2026-01-02",
               "history is newest-first, the way a tab reads it")

            ok(top["summary"]["graded"] == 2
               and top["summary"]["reached_t1"] == 1
               and top["summary"]["reached_t1_pct"] == 50.0,
               "one of two days reached a target, counted on days not on signals")
            ok(top["summary"]["reached_t2_pct"] == 0.0
               and top["summary"]["reached_t3_pct"] == 0.0,
               "reaching T1 is not reported as reaching T2 or T3")
            ok(top["summary"]["stopped"] == 1 and top["summary"]["expired"] == 1,
               "a card that expired past T1 is not counted as a stop")

            ok(rep["every_signal"]["graded"] == 3
               and rep["every_signal"]["reached_t1_pct"] == 66.7,
               "the uncapped book is reported beside it as the comparator")
            ok(rep["every_signal"]["signals_per_day"] == 1.5,
               "including how many signals a day the book actually resolved")

            # Selection must be a policy choice, not a result: first-of-day picks
            # 'a' too, so on this fixture the two policies agree and the score has
            # earned nothing.
            ok([p["signal_id"] for p in first["history"]] == ["c", "a"],
               "first-of-day is reported beside the ranking, so a ranking that "
               "adds nothing is visible as an identical record")
            ok(rep["ranking_validated"] is False
               and "RANK_NOT_VALIDATED" in rep["ranking_verdict"],
               "and no ranking is claimed to be validated")

            ok(top["summary"]["evidence"] == db.THIN
               and top["summary"]["sessions_shortfall"] == db.MIN_SESSIONS - 2,
               "two sessions is REQUIRES_MORE_DATA with the shortfall stated")
            ok(db.MIN_SESSIONS >= 20, "and the bar is at least 20 sessions")
            ok(all(p["gross_only"] for p in top["history"])
               and top["summary"]["gross_only"] is True
               and any("gross" in n for n in rep["notes"]),
               "every R is flagged gross, so nothing here reads as profit")
            ok(rep["research_only"] is True,
               "the report declares itself research only")

            # ---- a tie, and a card with no score at all ----
            write(
                tmp,
                [card("d", 50.0, None, 3000), card("e", 50.0, None, 3100)],
                [resolved("d", "2026-01-03", 3000, outcome="STOP", realized=-1.0),
                 resolved("e", "2026-01-03", 3100, outcome="T3",
                          targets=("T1", "T2", "T3"), realized=3.0)],
            )
            rep = db.report()
            ok([p["signal_id"] for p in rep["policies"][db.TOP_SCORE]["history"]]
               == ["d"],
               "a tie on score breaks to the earlier call, never to the better "
               "outcome")
            conv = rep["policies"][db.TOP_CONVICTION]["summary"]
            ok(conv["days_with_a_signal"] == 0
               and conv["days_no_card_to_rank"] == 1
               and conv["reached_t1_pct"] is None,
               "a day with nothing to rank on is a gap in the record, not a 0% "
               "day, and a rate with no denominator stays None")

            # ---- a signal whose card was never journalled ----
            write(tmp, [],
                  [resolved("z", "2026-01-04", 4000, outcome="T3",
                            targets=("T1", "T2", "T3"), realized=2.0)])
            rep = db.report()
            hist = rep["policies"][db.FIRST_OF_DAY]["history"]
            ok(len(hist) == 1 and hist[0]["card_found"] is False
               and hist[0]["signal_score"] is None,
               "an outcome with no journalled card is still graded, with the "
               "missing card declared rather than filled in")
            ok(rep["policies"][db.TOP_SCORE]["summary"]["days_with_a_signal"] == 0,
               "but it cannot be score-ranked, so the ranking policies skip it")

            # ---- an empty book reports nothing rather than zero ----
            write(tmp, [], [])
            rep = db.report()
            ok(rep["every_signal"]["reached_t1_pct"] is None
               and rep["policies"][db.TOP_SCORE]["summary"]["sessions"] == 0,
               "with no resolved signal there is no hit rate to publish")
        finally:
            settings.data_dir = keep
            sj._read_cache.clear()

    print(f"checked {CHECKS}")
    print("daily best one-a-day record smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
