# INSTALL — Phase 27: Higher-Timeframe Futures Research (5m / 15m)

Cumulative overlay. It contains every file from the Phase 26 overlay (refreshed
from the current workspace) plus the new Phase 27 package. Unzipping over your
tree is safe and idempotent.

**Research only. Paper only.** Phase 27 has no tick hook, no order path, no
paper-order automation and no promotion path. The highest label it can publish is
`RESEARCH_LEAD`. Production signal/gates/strike/stop/target/exit logic, the
futures live path, the Phase 23 hurdle shadow, Phase 24, Phase 25, Phase 26 and
the real option-book capture are all untouched — keep the capture running.

## 1. Install

```bash
cd ~/projects/phase8_delivery
unzip -o "/mnt/c/Users/djs admin/Downloads/qt_phase27.zip" -d .
```

## 2. Verify

```bash
cd backend
.venv/bin/python _smoke_phase27.py     # expect: 172 passed
.venv/bin/ruff check app/research/phase27
```

## 3. Run the study (safe with the engine up — it reads candle files only)

```bash
cd backend
.venv/bin/python -m app.research.phase27.cli coverage
.venv/bin/python -m app.research.phase27.cli run            # ~10 min, authoritative
.venv/bin/python -m app.research.phase27.cli run --quick     # first look only
.venv/bin/python -m app.research.phase27.cli show
```

Optional narrowing: `run --instrument NIFTY --timeframe 15`.

Artefacts are written to `backend/data/phase27/`:

```
p27_study_report.json        p27_ranked_strategies.json   p27_top10.json
p27_walk_forward.json        p27_final_holdout.json       p27_robustness.json
p27_geometry_sweep.json      p27_timeframe_comparison.json
p27_baseline_comparison.json p27_rejected.json            p27_research_leads.json
```

The artefacts from my run are included in this zip, so the panel has content
before you run anything. Re-running overwrites them with yours.

## 4. Read-only API

```
GET /api/phase27/coverage
GET /api/phase27/summary
GET /api/phase27/comparison
GET /api/phase27/strategies?limit=25    # 1..200
GET /api/phase27/geometry
```

No endpoint starts a study, and none writes anything.

## 5. Frontend

New read-only panel `frontend/components/HigherTimeframePanel.tsx`, rendered by
`ResearchDashboard` after the exit-study panel. It shows the verdict, the
pre-committed stopping rule, the per-timeframe funnel, the ranked cohorts, the
1m/5m/15m comparison, aggregate coverage and the instruments excluded for
insufficient history. There is no run button.

```bash
cd frontend && npx tsc --noEmit && npm run build
```

`npm run lint` still cannot run in this project: there is no ESLint config and
`next lint` drops into its interactive setup prompt. I did not weaken or bypass
the gate — tsc and the production build both pass.

## 6. Method, stated plainly

- 1-minute NIFTY/CRUDEOIL candles are resampled to 5m and 15m **causally and
  session-aware**: no aggregate bar spans a session boundary, each is timestamped
  at its closing constituent minute, short tail bars are kept and reported, and
  nothing is interpolated or filled.
- Aggregation never makes an instrument eligible: the 750-session / 100k-bar bar
  is applied to the source minutes, so the eight instruments without five years
  of one-minute history stay `INSUFFICIENT_HISTORY`, separate from the negative
  evidence.
- Decision at the close of bar *i*, entry at the **open of bar i+1**, outcomes
  from later bars only, stop wins a same-bar tie, every trade flattens inside its
  own session, 180-minute horizon (36 bars at 5m, 12 at 15m).
- Shared Phase 24 resolver and shared Phase 19 futures cost model. Slippage is
  charged once, never also added to the fill price.
- Bounded staged discovery over Phase 24's interpretable vocabulary (singles →
  pairs → triples, from development-qualified parents only), both LONG and SHORT.
  Every evaluated hypothesis is counted and BH/FDR uses that real denominator
  (2,144 per timeframe), not the number of rows that reached the table.
- Selection uses development only; validation and the untouched holdout are read
  after selection. Then walk-forward (5 folds), spread ×1.0/1.25/1.5, explicit
  slippage 2.0/3.0 points per side, entry delays 15/30/60 min, exit delays 1/2
  bars, band perturbation where a band exists, regime/outlier checks.
- **The futures feed publishes candles, not depth, so the bid/ask spread is
  unmeasured and every net figure is optimistic by exactly one spread.** A
  marginally positive result is therefore not positive.

## 7. Result of my authoritative run

`REJECTED — NO_ROBUST_INTRADAY_DIRECTIONAL_EDGE_FOUND`, 0 research leads, both
timeframes. The pre-committed stopping rule triggered: **no further
strategy-search phase should be created on this evidence.**
