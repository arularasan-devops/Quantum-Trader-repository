# Recovery report — Compose/PostgreSQL, live feed, strategy, execution safety

Branch: `devin/1791619517-restore-compose-feed-autotrade`. All changes are
additive and preserve existing API contracts and UI layout.

## 1. Baseline evidence
- Git history: 4 commits (`9fed4b3` canonical import, `c38b4a9`, `68dc16e`
  frontend merge), no tags. Nothing older to restore; "last known working" =
  current tree + `*.bak` + `docs/source-overlays/phase56_*.zip`.
- Governance (`AGENTS.md`, `.ai/AI_LAB_RULES.md`): agents must not enable live
  execution, place orders or handle Angel credentials. Respected throughout.

## 2. Root causes
| Symptom | Root cause (evidence) | Fix |
|---|---|---|
| Simulated prices | `config.py` `data_provider="simulated"`, `.env.example` `QT_DATA_PROVIDER=simulated`. Angel adapter (`app/market/angelone.py`) and `build_provider("angelone")` exist. `.env.example` listed providers that don't exist (kite/upstox/truedata). | Corrected the provider list. Selection stays explicit. |
| Dead real feed shown as "Simulated"/"REST" | `_UnavailableProvider.feed_health` reported `mode="rest"`. Groww without a session reported `feed_mode()="simulated"`, which `_feed_ready()` treated as ready. ProDashboard labelled every unknown mode "Simulated". | `FeedHealth.provider/source/state/checked_at` + `classify_feed()`. Unavailable → `UNAVAILABLE/DISCONNECTED`, never ready. UI shows DISCONNECTED. |
| PostgreSQL unused | Compose never set `QT_DB_URL`, so `research/db.py` fell back to SQLite. No healthchecks/readiness; `.env.example` lacked the `POSTGRES_PASSWORD` that Compose requires. | `QT_DB_URL` points at the `postgres` service. pg/redis/backend healthchecks + `service_healthy` deps. `backend-data` volume for the SQLite/JSONL stores. `schema_migrations` + `/api/db/status`. |
| `docker compose build` failed | `frontend/Dockerfile` copies `/app/public`, which didn't exist. | Added `frontend/public/.gitkeep`. |
| Unauthenticated live arming | `/api/auto-trade` (`allow_live`) and `/api/auto-buy` changed live settings with no auth. | `QT_LIVE_ARM_TOKEN` + `X-QT-Live-Token` header (constant-time compare). With no token configured, arming is refused. |
| Capital not a single source | `capital=₹1L`. `/api/auto-buy` set the per-order cap to the broker cash balance and live lots to 50. Balance-based budget could exceed capital. | `capital=₹5,00,000` (QT_CAPITAL). The API can only lower it. Per-instrument capital, live order cap and balance budget are all clamped to it. Auto-buy no longer raises lots. |
| Ack treated as fill | `AngelOneProvider.place_order` used the order id as success and the **quote** LTP as the fill price. | Result is `ACKNOWLEDGED`, with fill data from read-only `order_status()` (orderBook). State polls up to `order_fill_timeout_sec`. Pending orders block duplicates and are adopted once filled. PARTIAL uses filled lots; REJECTED/CANCELLED opens nothing. Same handling for exits. |
| No kill switch API | Only the source constant `REAL_MONEY_EXECUTION_ENABLED`. | `/api/kill-switch`, persisted in `data_dir/kill_switch.json` and fail-closed if unreadable. Engaging needs no auth; releasing needs the token. It blocks new entries only. |

## 3. Strategy
The existing trend+pullback strategy is documented in
`docs/STRATEGY_TREND_PULLBACK.md` and pinned by
`_smoke_strategy_trend_pullback.py`. No thresholds were changed and no
profitability claim is made.

## 4. Verification (this session)
- `docker compose up -d --build`: all 4 services healthy. `/api/db/status`
  reported `postgresql`, schema 2. A marker trade row survived
  `compose down` / `up`. `_smoke_db_status.py` passed 9/9 inside the container
  against PostgreSQL.
- New smokes: db_status 9/9, feed_provenance 14/14, strategy 24/24,
  live_controls 35/35 (mocked broker; no real order path reachable).
- `verify_feed_readonly.py` with `QT_DATA_PROVIDER=angelone` and no creds
  returns **BLOCKED (exit 2)**, as designed.
- Full smoke suite: see §6.

## 5. Blocked / not done (needs a human)
- **Live Angel One feed verification**: needs operator credentials during
  market hours. Run `cd backend && python scripts/verify_feed_readonly.py CRUDEOIL`.
- **Live order enablement**: `REAL_MONEY_EXECUTION_ENABLED=False` is left as
  is (repo policy). `order_status()` field mapping (`orderid`, `status`,
  `filledshares`, `averageprice`) follows the SmartAPI order book and must be
  verified by the operator before any live use.
- Pending orders, restart recovery, journal mirror and UI controls: implemented
  in §7 below. Static review only, not executed in this session.
- Pre-existing research-phase smoke failures unrelated to this work: see §6.

## 6. Full smoke suite (121 `_smoke_*.py`, Python 3.11)
Baseline = commit `68dc16e` (before this branch), same runner.

- **New on this branch, all pass:** db_status, feed_provenance,
  strategy_trend_pullback, live_controls.
- **Regressions: none.** autobuy, concurrency and alerts hit the timeout when
  run 8-way parallel on a 2-CPU VM alongside a Docker build. Re-run serially:
  autobuy passes in about 40 s on both `68dc16e` and HEAD, and concurrency
  passes. alerts fails exactly as on baseline (below).
- **Pre-existing failures (identical on baseline, not touched):**
  - `historical_import`, `phase51`–`phase54`: the shipped NIFTY history file is
    missing from the tree. No data was fabricated to satisfy them.
  - `phase22`: `phase14.paths` has no `PATH_TRUNCATED`.
  - `phase43`, `phase45`, `phase46`: naming, count and refresh-time expectations.
  - `phase47`, `phase48`: `ResearchCallBoard`/`ResearchFunnelBoard` components
    are missing.
  - `alerts`: `StopIteration`, because there is no snapshot without a running feed.
  - `watchlist_movers`: `.card-scroll` CSS check.
  - `buypath`: produces no output and times out (300 s baseline, 600 s serial).
- These match the AGENTS.md note that Phase 57–61 artifacts are absent. They
  are reported here, not masked.

## 7. Order recovery, persistence completeness, UI safety (follow-up)

Not executed here (per instruction: no tests, Docker or migrations were run).
Verify locally with `backend/_smoke_pending_recovery.py` and the commands in
`DEPLOYMENT.md`.

### Persistence map (after this change)
| Record | Durable store | PostgreSQL |
|---|---|---|
| Live pending orders + app-tracked live position | `data_dir/live_orders.json` (atomic, locked) | — (recovery source is the file) |
| Order lifecycle events (intent, ack, status, fills, operator actions) | `data_dir/order_events.jsonl` (fsync, append-only) | `order_events` (v3, idempotent on `event_id`, cursor-based retry) |
| Closed trades + decision context + exit reason + P&L | SQLite `history.db.trade_journal` (unchanged read path) | `trade_journal` (v3 mirror, idempotent on source id, back-fills history) |
| Closed trades (research, MFE/MAE) | — | `trade_history` (unchanged) |
| Account-tab paper trades | SQLite `history.db.paper_trades` | not mirrored (unchanged) |
| Signals, shadow book, futures paper, funnels, risk guard, kill switch | JSON/JSONL in `data_dir` | not mirrored (unchanged) |
| Candles, chain snapshots | SQLite `history.db` | not mirrored (bulk market data) |

### Order lifecycle rules (live mode only; paper unchanged)
- An order's intent (`SUBMITTING`) is persisted **before** it is sent. If the
  ledger cannot be written, a live entry is refused before anything is sent
  (exits proceed, alerted as critical).
- Fills come only from the broker's order status (`filled_lots`, cumulative
  `averageprice`). Each new increment is priced as
  `(avg_now*filled_now - avg_prev*filled_prev) / new_lots`; the position's
  entry is the weighted average. Acknowledgements and quote LTP are never fills.
  Live exits are booked at the broker's fill price.
- A partial fill keeps the original order id, requested lots and filled lots;
  the remainder blocks duplicate entries and its later fills are applied by a
  read-only poll every 5 s. Nothing is cancelled or resubmitted automatically.
- **Restart:** saved state is restored as an *unverified claim*. New live
  entries are blocked and a critical alert is raised. Restored orders are
  reconciled against the broker order book (read-only). Recovery auto-clears
  only when every restored order ended with no fill and no position was saved.
  Otherwise the operator checks the broker and calls the authenticated
  `POST /api/order-recovery`:
  - `adopt_position`
  - `confirm_flat`
  - `clear_pending` (`which` = entry|exit)

  A note is required and every action is audited.
- Ledger/audit write failures block new live entries until a write succeeds
  again (retried by the poll), and are reported in `GET /api/order-recovery`.
- The PostgreSQL mirror never drops events: the cursor advances only after a
  successful idempotent insert. Torn JSONL lines are isolated and counted.

### Cannot be guaranteed without broker-side reconciliation
- There is **no broker position-book read** in the provider. Positions are
  never auto-restored; the operator must confirm against Angel One.
- Angel `order_status()` maps `cancelled` with fills to `PARTIAL`. A cancelled
  remainder therefore keeps blocking entries until `clear_pending`.
- A crash after the broker accepted an order but before the ack was saved
  leaves a `SUBMITTING` record with no order id. Check the broker order book.
  (`ordertag` matching is not implemented.)
- Orders or square-offs placed outside the app (broker terminal, RMS
  auto-square-off) are not visible to the app.
- Exits that complete while recovery is pending reduce the saved position and
  are in `order_events`, but they get no trade-journal row (entry context is lost).

### Frontend
- Kill-switch panel (Auto-Bot panel):
  - shows state ENGAGED/RELEASED/UNKNOWN and API errors
  - Engage needs no token
  - Release asks for the token in a password field, sends it once in a header, then clears it
- The live-arm token is now held only in memory. `liveHeaders()` no longer
  reads `localStorage`, and any legacy `qt_live_token` key is removed.
- Feed status and the "Option LTP" badge come from `feed_health.source/state`:
  LIVE, DELAYED, SIMULATED, DISCONNECTED, UNAVAILABLE. Missing or unrecognised
  health shows **UNKNOWN**, never LIVE or SIMULATED.
