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
- Pending broker orders are held in memory only. A restart while an order is
  pending needs manual reconciliation against the broker order book. A partial
  fill's unfilled remainder is not auto-cancelled.
- No login/session UI exists in the repo. Live-arm auth is a header token
  (browser `localStorage.qt_live_token`). There is no frontend widget for
  setting it or for the kill switch yet (API functions exist in `lib/api.ts`).
- Main history/journal stores (`app/storage.py`, JSONL) remain SQLite/files,
  persisted via the `backend-data` volume. Only the research store
  (trade_history, signals, shadow log) is in PostgreSQL.
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
