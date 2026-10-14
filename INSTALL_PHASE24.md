# PHASE 24 — FIVE-YEAR DISCOVERY STUDY (research only, additive)

Cumulative overlay. Nothing is removed or renamed. Phase 23's hurdle shadow, the
production Option Signal, gates, confidence, Signal Score, strike selector,
premium floor, stops, targets, exits, futures logic and the broker order path are
all untouched — Phase 24 has no tick hook and no write path into any book.

## Install

```bash
cd ~/projects/phase8_delivery
unzip -o "/mnt/c/Users/djs admin/Downloads/qt_phase24.zip" -d .
cd backend && .venv/bin/python _smoke_phase24.py
cd .. && ./stop.sh && ./start.sh
```

## The five-year history files are NOT in this zip

The study reads two files that the zip cannot carry (48 MB + 94 MB uncompressed):

```
backend/data/backtest/NIFTY_ONE_MINUTE.jsonl
backend/data/backtest/CRUDEOIL_ONE_MINUTE.jsonl
```

Without them **no instrument has a five-year series**, and the study refuses to
run rather than writing a report full of zeroes:

```
PHASE 24 — CANNOT RUN
  no instrument has a five-year 1-minute series on disk, so there is nothing to search.
```

That is a missing-data state, not a verdict about any strategy. The smoke reports
the three history-dependent checks as SKIPPED rather than FAILED, and
`coverage` prints the exact paths it expects.

To install them, put the two `.jsonl.gz` files sent alongside this zip into
`backend/data/backtest/` and unpack in place:

```bash
cd backend/data/backtest
gunzip -k NIFTY_ONE_MINUTE.jsonl.gz CRUDEOIL_ONE_MINUTE.jsonl.gz
cd ../../ && .venv/bin/python -m app.research.phase24.cli coverage   # expect USABLE NIFTY + CRUDEOIL
```

Each line is one bar: `{"time": <epoch seconds>, "open":, "high":, "low":,
"close":, "volume":}`. Duplicated timestamps are collapsed on load, so re-running
the collector to extend a file is safe.

## Run the study

```bash
cd backend
.venv/bin/python -m app.research.phase24.cli coverage      # what history exists
.venv/bin/python -m app.research.phase24.cli run           # ~11 min, writes artefacts
.venv/bin/python -m app.research.phase24.cli run --quick   # 2 stop bands, no geometry sweep
.venv/bin/python -m app.research.phase24.cli show          # print the last report
```

Artefacts land in `backend/data/phase24/` and are read-only inputs for the panel:

| file | contents |
| --- | --- |
| `p24_discovery_report.json` | windows, pools, coverage, the fifteen answers, the conclusion |
| `p24_candidate_pool.json` | pool summaries plus a pointer to the per-candidate files |
| `p24_candidates_<INST>_1atr.jsonl.gz` | one row per candidate opportunity, selected or refused |
| `p24_ranked_strategies.json` / `p24_top10.json` | ranked cohorts with failed clauses |
| `p24_walk_forward.json`, `p24_final_holdout.json` | fold and holdout metrics |
| `p24_robustness.json` | cost/entry/exit stress, parameter perturbation, regimes |
| `p24_geometry_sweep.json` | unconditional stop/target economics |
| `p24_rejected.json` | every non-validated cohort and why |
| `p24_fingerprints.json` | published only for a VALIDATED strategy |
| `p24_baseline_comparison.json`, `p24_vehicles.json` | baselines, vehicle coverage |

## Where it appears

Research tab → **5-YEAR DISCOVERY (RESEARCH · PAPER ONLY)**, below the Phase 23
hurdle shadow. Read-only: the three endpoints are
`/api/phase24/coverage`, `/api/phase24/summary`, `/api/phase24/strategies`.
There is no run, start, promote or arm endpoint — a dashboard cannot launch the
study or push a strategy anywhere.

## What this study can and cannot answer

- Usable five-year 1-minute history on disk is **NIFTY and CRUDEOIL only**
  (2021-08-02 → 2026-08-05). BANKNIFTY, SENSEX, FINNIFTY, MIDCPNIFTY, GOLD,
  SILVER, COPPER and NATURALGAS have 1.4k–3.2k bars each and are reported as
  `INSUFFICIENT_HISTORY` with their real counts. No equity has a five-year
  series, so equity discovery is not run rather than reported as a negative.
- Historical option books are **measured from your own store, not assumed**.
  `coverage` counts snapshots, real-broker snapshots and snapshots carrying a
  genuine two-sided book (bid > 0, ask > 0, ask ≥ bid), broken down per
  instrument with the first and last capture timestamp. On the development
  machine that is 58,138 snapshots with **1** usable book; a machine that has
  been capturing live will show far more, and the note changes accordingly.
- CE and PE stay `REQUIRES_MORE_DATA` **for the five-year study** regardless of
  that count: a large capture over recent weeks is not a five-year option
  history, and no premium, bid, ask, mid or family-median spread is ever modelled
  to fill the gap. What a rich capture does unlock is a *separate* CE/PE study
  over its own window, where entry-at-ask and exit-at-bid can be honoured
  honestly — `ce_pe_study_possible` reports whether the store has enough
  (≥ 5,000 two-sided books) for that.
- The book count is read in **short pages (2,000 rows) with per-page retries**,
  so a running engine that takes the write lock costs one retried page rather
  than the whole count. Reads use a probed read-only handle with a busy timeout
  and never write to the store; the captured-bar counts behind each
  `INSUFFICIENT_HISTORY` line are retried the same way, so a busy store cannot
  make them read as zero.
- If the chain table still cannot be read (engine mid-write, WAL shared-memory
  unavailable, or no chain table yet) the count is reported as **unmeasured**,
  never as zero, with a plain-language cause and the suggestion to retry with
  `./stop.sh`. The sqlite message itself is never surfaced.
- Futures outcomes charge per-order brokerage, statutory charges and configured
  slippage per trade from the existing Phase 19 cost model (costs scale with the
  price level, so a trade at NIFTY 15,000 is not priced like one at 26,000).
  No historical two-sided futures spread exists, so futures results are
  **optimistic by one unmeasured spread** — that is stated, not hidden.

## Current result

`STATUS: REJECTED` — no strategy is VALIDATED, so no fingerprint is published.

2,144 hypotheses over 527,290 candidates; 5 cleared development, none survived
validation, the unseen holdout year, walk-forward or the multiple-testing
correction. The closest candidate (CRUDEOIL SHORT, 2×ATR stop,
`prev_day_level_agrees AND time_late_1400_close AND volatility_expanding`) is
negative in all three windows: dev −0.1823R, validation −0.1512R, holdout
−0.1645R with PF 0.763.

The unconditional geometry sweep shows why, and it is the more useful finding:
at a 1-minute ATR the round-trip cost is **43% of risk at a 2×ATR stop and 98%
at 1×ATR**, so the geometry has to be paid for before any rule is applied. At
2×ATR/1R, CRUDEOIL hits T1 first 49.89% of the time against a 50.0% pre-cost
break-even — the base rate is a coin flip and the cost is the whole edge. No
entry rule in the searched space recovered that on this data.

This is a negative result reported as a negative result. Nothing here is
promoted, and paper-only remains the state of the platform.

On a machine **without** the two history files the conclusion instead reads
`STATUS: REQUIRES_MORE_DATA` with `CLOSEST_CANDIDATE: "the study did not run…"`.
The two states are deliberately different: one means every rule was searched and
lost, the other means nothing was searched.
