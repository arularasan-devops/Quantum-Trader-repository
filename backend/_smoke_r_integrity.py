"""R-integrity smoke: corrupted denominators must be excluded and counted.

The recorded 25/26 Aug ledgers hold resolutions written before the journal
refused a non-positive risk, so ``realized_r`` sentinels of +/-1e8 sit beside
honest-but-explosive rows such as 41.5R off a 0.2-point stop. This checks that
every aggregation path drops them with a reason and a count, and that nothing is
silently clamped into an average instead.
"""
from __future__ import annotations

import json
import tempfile

CHECKS = 0


def ok(cond: bool, what: str) -> None:
    global CHECKS
    CHECKS += 1
    assert cond, what


def main() -> int:
    from app.analysis import label_outcomes, r_integrity

    sentinel = {"event": "RESOLVED", "signal_id": "s1", "instrument": "SBIN",
                "session": "2026-08-26", "realized_r": 100000000.0,
                "mfe_r": 100000000.0, "entry": 3.0, "risk_points": 0.0}
    subtick = {"event": "RESOLVED", "signal_id": "s2", "instrument": "HCLTECH",
               "session": "2026-08-26", "realized_r": 41.5, "mfe_r": 41.5,
               "entry": 4.0, "risk_points": 0.02}
    wide_r = {"event": "RESOLVED", "signal_id": "s3", "instrument": "GRASIM",
              "session": "2026-08-26", "realized_r": 23.0, "mfe_r": 23.0,
              "entry": 1.0, "risk_points": 0.5}
    good = {"event": "RESOLVED", "signal_id": "s4", "instrument": "NIFTY",
            "session": "2026-08-26", "realized_r": 1.2, "mfe_r": 1.6,
            "mae_r": -0.4, "entry": 120.0, "risk_points": 24.0, "order": "TARGET_FIRST"}
    loser = {"event": "RESOLVED", "signal_id": "s5", "instrument": "NIFTY",
             "session": "2026-08-26", "realized_r": -1.0, "mfe_r": 0.3,
             "mae_r": -1.0, "entry": 100.0, "risk_points": 20.0, "order": "STOP_FIRST"}
    no_r = {"event": "RESOLVED", "signal_id": "s6", "instrument": "NIFTY",
            "session": "2026-08-26", "realized_r": None, "risk_points": 20.0}

    ok(r_integrity.rejection(sentinel) == r_integrity.NON_POSITIVE_RISK,
       "a zero risk is the defect, named as such")
    ok(r_integrity.rejection(subtick) == r_integrity.SUB_TICK_RISK,
       "a stop closer than one option tick cannot be honoured by the book")
    ok(r_integrity.rejection(wide_r) == r_integrity.IMPLAUSIBLE_R,
       "23R on a 1-rupee premium is the denominator talking")
    ok(r_integrity.rejection(no_r) == r_integrity.NO_R,
       "a resolution with no R is not an R of zero")
    ok(r_integrity.rejection(good) is None and r_integrity.usable(good),
       "an ordinary resolution must survive the filter")
    ok(r_integrity.rejection({"realized_r": float("inf"), "risk_points": 10.0})
       == r_integrity.NO_R, "a non-finite R is not a number")

    rows = [sentinel, subtick, wide_r, good, loser, no_r]
    kept, excluded = r_integrity.partition(rows)
    ok([r["signal_id"] for r in kept] == ["s4", "s5"],
       f"only the honest rows survive, got {[r['signal_id'] for r in kept]}")
    ok(sum(excluded.values()) == 4, "every dropped row is counted")
    ok(excluded[r_integrity.IMPLAUSIBLE_R] == 1, "reasons are counted separately")
    ok(all(abs(float(r["realized_r"])) <= r_integrity.MAX_PLAUSIBLE_R for r in kept),
       "no clamped survivor: exclusion, not truncation")

    # The cohort study is the Phase 13 artefact that showed the blow-ups.
    journal = [{"signal_id": r["signal_id"], "session": r["session"],
                "instrument": r["instrument"], "option_type": "UNKNOWN",
                "entry_plan": {"entry_price": r.get("entry"),
                               "risk_points": r.get("risk_points")},
                "tradability": {"status": "UNKNOWN", "spread": 0.5}}
               for r in rows]
    study = label_outcomes.study(journal, rows)
    ok(study["resolved"] == 2, f"study scores 2 rows, got {study['resolved']}")
    ok(study["excluded"] == 4, f"study reports 4 exclusions, got {study['excluded']}")
    ok(set(study["excluded_by_reason"]) == {r_integrity.NON_POSITIVE_RISK,
                                            r_integrity.SUB_TICK_RISK,
                                            r_integrity.IMPLAUSIBLE_R,
                                            r_integrity.NO_R},
       "the reader is told which defect removed which rows")
    unknown = [c for c in study["by_option_type"] if c["cohort"] == "option_type=UNKNOWN"]
    ok(len(unknown) == 1, "the UNKNOWN option-type cohort is still reported")
    exp = unknown[0]["expectancy_gross_r"]
    ok(exp is not None and abs(exp) <= r_integrity.MAX_PLAUSIBLE_R,
       f"the cohort that carried +1e8 R now reads {exp}")
    ok(any("excluded_by_reason" in n for n in study["notes"]),
       "the exclusion rule travels with the artefact")

    # Same filter through the journal-stats path, over a real ledger layout.
    from app.analysis import journal_stats
    from app.analysis import signal_journal as sj
    from app.config import settings

    original_dir = settings.data_dir
    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = tmp
        with open(sj.outcomes_path(), "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        with open(sj.journal_path(), "w", encoding="utf-8") as fh:
            for row in journal:
                fh.write(json.dumps({**row, "event": "SIGNAL",
                                     "board_action": "BUY", "followed": True}) + "\n")
        stats = journal_stats.statistics("2026-08-26")
        ok(stats["resolved_signals"] == 6,
           "hit rates keep their full denominator")
        ok(stats["r_scored_signals"] == 2,
           f"R averages use 2 rows, got {stats['r_scored_signals']}")
        ok(stats["r_excluded_signals"] == 4, "the tally is on the statistics payload")
        ok(stats["avg_r"] is not None
           and abs(stats["avg_r"]) <= r_integrity.MAX_PLAUSIBLE_R,
           f"avg_r is an average of trades again, got {stats['avg_r']}")
        ok(abs(stats["avg_r"] - 0.1) < 1e-9,
           f"avg_r is exactly the mean of +1.2 and -1.0, got {stats['avg_r']}")
    settings.data_dir = original_dir

    print(f"checked {CHECKS}")
    print("r integrity smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
