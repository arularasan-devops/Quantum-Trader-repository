# Journal v2: sessions, signals, trade events, sizing decisions (Phases B + C)

Record-only. Nothing in the trading path reads these tables back, and the
engine's paper/live quantity is unchanged. Live order placement stays blocked
(`REAL_MONEY_EXECUTION_ENABLED=False`).

## Storage
The tables live in the research database: PostgreSQL when `QT_DB_URL` is set
(Compose), otherwise SQLite `data_dir/research.db`. All tables are new and
additive, with a `jr_` prefix; no existing table is changed.

| Table | One row per | Key (idempotent) |
|---|---|---|
| `jr_sessions` | instrument × IST date × mode (written even when there are no trades) | `INSTRUMENT:YYYY-MM-DD:mode` |
| `jr_signals` | BUY signal per minute per outcome; non-BUY decisions only when the signal changes | hash(instrument, signal, option, minute, outcome) |
| `jr_trades` | trade (summary; exit totals recomputed from events) | `trade_id` |
| `jr_trade_events` | ENTRY / EXIT / PARTIAL_EXIT / STOP_CHANGE / TARGET_LEVEL | hash(trade, kind, ts, lots, price, stop, target) |
| `jr_sizing_decisions` | sizing method per entry (5 hypothetical + `ENGINE_ACTUAL`) | hash(trade, instrument, ts, method) |
| `jr_schema_migrations` | applied journal schema version (currently 1) | `version` |

Inserts use `ON CONFLICT DO NOTHING`, so a retry never creates a duplicate.
Journal write errors are counted (`/api/journal/v2/status`) and never block a trade.

Signal outcomes:
- `EXECUTED`
- `SKIPPED`: the gate that refused it, taken from the execution funnel
- `REJECTED`: broker/order rejection
- `NOT_EXECUTABLE`: stale feed, or no two-sided quote
- `FAILED_CONDITIONS`: a WAIT/AVOID/NO_TRADE decision

## Sizing comparison (observational)
The simulated starting capital is `QT_SIM_STARTING_CAPITAL`, default ₹5,00,000.
Equity = starting capital + realised net P&L recorded in the journal for that mode.

Methods:
- `FIXED_1`, `FIXED_2`
- `CAPITAL_BASED`: `QT_SIZING_CAPITAL_FRACTION_PCT` (default 10%) of equity
- `AGGRESSIVE`: capital-based × `QT_SIZING_AGGRESSIVE_MULTIPLIER` (default 2)
- `RISK_BUDGET`: `QT_SIZING_RISK_BUDGET_PCT` (default 1%) of equity lost at the stop, including costs

Rules for every method:
- It is capped by available simulated funds and never exceeds them.
- It records the constraint that applied and the reason a larger size was or wasn't possible.
- Options are bought outright, so margin is `NOT_APPLICABLE`.
- Futures margin is `ESTIMATED` from a percentage of notional unless a verified per-contract figure is supplied. With neither, the method is `UNAVAILABLE`.
- Costs are `ESTIMATED` with the shared cost model.

The lot-size source is either `BROKER_SCRIP_MASTER` (live Angel feed) or
`REGISTRY_SEED_UNVERIFIED` (built-in seed), and it is recorded with a timestamp.

## API (read-only; `/api/journal` unchanged)
- `GET /api/journal/v2/status`
- `GET /api/journal/v2/trades`
  - filters: `instrument`, `mode`, `strategy`, `setup`, `regime`, `status`, `result=WIN|LOSS`, `since`, `until` (epoch seconds)
  - paging: `page`, `page_size` (≤500)
- `GET /api/journal/v2/trades.csv` (same filters, stable columns `TRADE_CSV_COLUMNS`)
- `GET /api/journal/v2/trades/{trade_id}` (trade + event timeline + sizing + signals)
- `GET /api/journal/v2/sessions`
- `GET /api/journal/v2/signals` (adds filters `outcome`, `signal`, `session_id`)

## Rollback / recovery
The tables depend on nothing else.
1. Back up first: `pg_dump -t 'jr_*'`.
2. Drop only the journal tables:
   `python -c "from app.journal.store import store; store().rollback('DROP_JOURNAL_V2')"`.
3. To disable recording without dropping anything, set `QT_JOURNAL_V2_ENABLED=false`.

## Verification status
- **Synthetic only:** `_smoke_journal_v2.py` (45 checks) passed on SQLite and on a throwaway PostgreSQL 16.
- **Not verified on real data:** none of this has been checked against your real journal, `history.db` or market data.
- **Not backfilled:** trades closed before this change are not copied into `jr_*` tables.
