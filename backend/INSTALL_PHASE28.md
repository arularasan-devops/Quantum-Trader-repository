# INSTALL — Phase 28: Multi-Day Futures & Cash-Equity Validation

Cumulative overlay. It contains every file from the Phase 27 overlay (refreshed
from the current workspace) plus the new Phase 28 package. Unzipping over your
tree is safe and idempotent.

**Research only. Paper only.** Phase 28 has no tick hook, no order path, no
paper-order automation, no run endpoint and no promotion path. The highest label
it can publish is `RESEARCH_LEAD`. Production signal/gates/strike/stop/target/
exit logic, the futures live path, the Phase 23 hurdle shadow, Phase 24–27 and
the real option-book capture are all untouched — keep the capture running, and
option strategy logic stays frozen.

## 1. Install

```bash
cd ~/projects/phase8_delivery
unzip -o "/mnt/c/Users/djs admin/Downloads/qt_phase28.zip" -d .
```

## 2. Verify

```bash
cd backend
.venv/bin/python _smoke_phase28.py     # expect: 276 passed
.venv/bin/ruff check app/research/phase28
```

## 3. Run the study (safe with the engine up — it reads candle files only)

```bash
cd backend
.venv/bin/python -m app.research.phase28.cli coverage        # what is answerable
.venv/bin/python -m app.research.phase28.cli run             # ~8-12 min, authoritative
.venv/bin/python -m app.research.phase28.cli run --quick     # first look only
.venv/bin/python -m app.research.phase28.cli show            # re-print last verdict
```

Optional narrowing: `run --instrument NIFTY`.

Artefacts are written to `backend/data/phase28/`:

```
p28_coverage.json          p28_execution_model.json   p28_pool_economics.json
p28_ranked_strategies.json p28_funnel.json            p28_baselines.json
p28_verdict.json
```

The artefacts from my authoritative run are included in this zip, so the panel
has content before you run anything. Re-running overwrites them with yours.

### Cash-equity history (optional, and only with real credentials)

`coverage` will tell you that **no cash-equity name has five years of daily
history on disk**, so the stock half of the question is currently *unanswered*,
not answered negatively. To answer it you must collect daily history first:

```bash
cd backend
.venv/bin/python -m app.research.phase28.cli collect --instrument TCS --years 5
```

The collector is resumable (existing rows are preserved, merged by timestamp and
de-duplicated), it refuses to run against the simulator, and it reads Angel One
configuration from the environment only — never from CLI arguments, logs or
artefacts. Without real credentials configured, leave the stocks as
`INSUFFICIENT_HISTORY`; do not substitute recent partial data.

## 4. Read-only API

```
GET /api/phase28/coverage
GET /api/phase28/summary
GET /api/phase28/funnel
GET /api/phase28/economics
GET /api/phase28/baselines
GET /api/phase28/strategies?limit=25    # 1..200
```

No endpoint starts a study, and none writes anything.

## 5. Frontend

New read-only panel `frontend/components/MultiDayPanel.tsx`, rendered by
`ResearchDashboard` after the higher-timeframe panel. It shows the verdict, the
pre-committed stopping rule, the two vehicle blocks separately (futures
diagnostics vs the cash-equity `INSUFFICIENT_HISTORY` status), the ranked
cohorts with development/validation/untouched-holdout numbers, walk-forward, the
buy-and-hold comparison, the FDR denominator, the failed clauses, daily vs
intraday cost-per-risk, and data provenance (source, span, bar count, thin
sessions). There is no run button.

```bash
cd frontend && npx tsc --noEmit && npm run build
```

`npm run lint` still cannot run in this project: there is no ESLint config and
`next lint` drops into its interactive setup prompt. I did not weaken or bypass
the gate — tsc and the production build both pass.

## 6. Method, stated plainly

- **Daily bars, one source per instrument, precedence stated in the artefact:**
  a native `<INST>_ONE_DAY.jsonl` file if present, otherwise the five-year
  one-minute file aggregated one IST session per bar (first open, max high, min
  low, last close, summed volume, stamped 15:30 IST), otherwise stored spot
  candles as a fallback. Nothing is interpolated; missing sessions stay missing
  and thin sessions are reported rather than dropped silently.
- Eligibility is 1,000 daily bars and 4.0 years. A name that fails it is
  `INSUFFICIENT_HISTORY` / `REQUIRES_MORE_DATA` — **absent history is never
  counted as negative strategy evidence.**
- **Futures and cash delivery are separate vehicle blocks**, because they are not
  the same trade: futures roll and are allowed LONG and SHORT; cash delivery has
  no expiry, pays delivery charges and is **LONG only** for an overnight hold.
  The instrument registry's exchange decides which is which, not a name list.
- Signal at the daily close, **entry at the next session open**, then each later
  bar's open is checked before its high/low: a gap through the stop or the target
  fills **at the open**, not at the level. A same-bar stop/target ambiguity
  resolves as a **stop**. Exit reasons distinguish level stop, gap stop, level
  target, gap target and horizon close.
- Futures reuse the Phase 19 cost model (brokerage, statutory and transaction
  charges, slippage) plus a **rollover charge**: one extra full round trip per
  30 calendar days held, so a multi-week hold does not cross expiry for free.
  Where expiry metadata is absent this is an approximation, and it is labelled as
  one — a continuous series is not exact contract execution.
- Cash equity is itemised: STT, exchange transaction charges, SEBI fee, stamp
  duty, GST, brokerage and slippage, with the depository charge reported as a
  flat rupee amount rather than divided by an assumed share count.
- **Buy-and-hold is the bar for cash equity**, measured over the same chronology
  and the same costs. A positive absolute return that loses to holding the name
  is a failure, not a result. Corporate actions, splits, dividends, survivorship
  and delisting limitations are disclosed in the artefacts.
- Bounded staged discovery over a frozen daily vocabulary (singles → pairs →
  triples from development-qualified parents only), maximum complexity three, no
  condition added after seeing results. Every evaluated hypothesis is counted and
  BH/FDR uses that real denominator (4,098 in my run), not the number of rows
  that reached the table.
- 60% development / 20% validation / 20% untouched holdout, with a 250-session
  warm-up reserved so long-horizon features are never computed from the future.
  Selection uses development only; validation and the holdout are read after
  selection. Then walk-forward, cost/slippage/gap/timing stress, and the FDR
  correction.
- **The futures feed publishes candles, not depth, so the bid/ask spread is
  modelled rather than measured.** A marginally positive result is therefore not
  positive.

## 7. Result of my authoritative run

`REJECTED — NO_MULTI_DAY_DIRECTIONAL_EDGE_FOUND`, 0 research leads.

- Qualifying data: NIFTY 1,243 daily bars / 5.01 years, CRUDEOIL 1,192 / 5.01
  years, both from the five-year one-minute files.
- Cash equity: **0 names with five years of daily history — `INSUFFICIENT_HISTORY`.
  The stock question is unanswered, not rejected.**
- 4,098 hypotheses, 562 ranked futures cohorts, 562 positive in development, 329
  in validation, 253 in the untouched holdout, 169 in all three, 30 with an
  adequate holdout sample, 85 walk-forward stable, 10 stress-clean, 23 through
  FDR — **0 clearing every clause together.**
- Closest cohort: CRUDEOIL LONG, 2.0×ATR stop, 20-session horizon,
  `pullback_0_20pct` + `far_from_52w_high` — dev +0.3952R → validation +0.7996R →
  holdout +0.5748R (n=19, PF 2.546), 5/5 walk-forward folds. It fails on holdout
  sample size (19 against a 100-trade bar) and on multiple testing.
- Cost arithmetic did improve as expected: median cost-per-risk **0.1532
  intraday (Phase 27) → 0.0168 on daily holds**. The toll fell by ~9×; the
  direction still did not survive.
- The pre-committed stopping rule triggered: **no further strategy-search phase
  should be created on this evidence.** The one genuinely open question left is
  the cash-equity half, and it needs collected daily history, not another search.
