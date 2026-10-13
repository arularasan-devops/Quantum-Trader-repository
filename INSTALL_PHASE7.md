# Phase 7 delivery — install notes

This archive is a **patch overlay**, not a runnable application. It contains only the
files that changed. Copy them over a complete build.

## Install

```bash
# 1. make (or keep) a complete build
cp -a ~/projects/quantum-trader-phase6 ~/projects/quantum-trader-phase7   # first time only

# 2. overlay the patch, never touching data/, .env or .venv
cd /path/to/unzipped/delivery
rsync -av --exclude data --exclude .env --exclude .venv backend/  ~/projects/quantum-trader-phase7/backend/
rsync -av frontend/ ~/projects/quantum-trader-phase7/frontend/
```

Never copy `data/`, `.env` or `.venv` from the patch — `data/` in the live build holds
the recorded real option chains, which are the only irreplaceable thing here.

## Run — exactly one backend process

Two servers share the same Angel credentials and fight for the same REST budget; that
alone took the scan loop from 28 s to 140 s per tick.

```bash
cd ~/projects/quantum-trader-phase7/backend
pgrep -af "uvicorn app.main"          # MUST print nothing; if not: kill -9 <pid>
nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > ~/qt.log 2>&1 &
sleep 60
curl -s localhost:8000/api/scan-health | python3 -m json.tool | grep -E "tick_cost_ms|ticked_count"
```

```bash
cd ~/projects/quantum-trader-phase7/frontend
npm run dev        # npm install only if node_modules is missing
```

Open `http://localhost:3000` — `localhost`, not `127.0.0.1` (backend CORS allows
`localhost`).

## What makes it fast (keep these three files together)

- `backend/app/market/angelone.py` — the ATM option-chart fetch inside the production
  tick used to reserve a slot on the shared historical limiter and **sleep** until that
  slot arrived (3.5 s spacing per key, exponential AB1021 cooldown to 90 s), despite its
  own contract of never stalling the tick. It now takes a slot only if one is reachable
  within 0.5 s and otherwise defers to a later pass; declining does not consume a slot.
  Warm-up (an instrument's first candles) still blocks, so nothing about warm-up changes.
  Measured effect: `tick_cost_ms` 37,286 → 37.9.
- `backend/app/storage.py` — candle persistence writes only bars newer than the last
  write, with a full 60-bar re-offer every 5 minutes, instead of rewriting and committing
  the whole trailing window for every instrument on every pass.
- `backend/app/ai/service.py` — the outcome resolver runs every 30 s, 50 decisions per
  pass, with the forward-candle window cached for 60 s, instead of reading up to 1,200
  stored bars per instrument every AI cycle against a 13k-decision backlog.

Verification after a restart, once `warm_done == warm_total`:

```
tick_cost_ms      tens of milliseconds (was 37,286)
scanned_per_cycle close to the 2.0 s target
ticked_count      equal to the watchlist size
```

## Guarantees unchanged by this delivery

No change to BUY/WAIT/NO_TRADE, gates, thresholds, stops, targets, R:R, chase guard,
strike selection, direction rules, Early Momentum/Early-Early, or the auto-buy bot.
Real-money execution stays off by construction (a source constant, not a setting).

## Smokes

```bash
cd ~/projects/quantum-trader-phase7/backend
.venv/bin/python _smoke_hist_limiter.py    # 14 checks — the performance fix
.venv/bin/python _smoke_history_gaps.py    # 8 checks
.venv/bin/python _smoke_ai_resolver.py     # 12 checks
.venv/bin/python _smoke_phase7.py          # 72 checks
```

`_smoke_flow.py` fails on this build and did so before it: it expects one ordinary red
candle to HOLD while the existing `flow_strength_floor` produces EXIT. Neither the
production behaviour nor the test was changed to hide that.

## Known open item

`option_candles` caches a symbol's chart for the life of the process, so premium
behaviour on a long-held ATM strike goes stale. Pre-existing, deliberately not bundled
into the performance fix.
