# Quantum Trader — MCX Crude Oil Options Decision Engine

A decision-first, institutional-style desktop web application for intraday
**MCX Crude Oil options** traders. It continuously analyses the futures, the
full option chain, technical structure, options flow (OI / PCR / Greeks) and
news/geopolitical sentiment, and collapses everything into **one clear
recommendation**:

> **BUY · WAIT · HOLD · EXIT · NO TRADE** — with a confidence %.

You only **buy** and **sell**. The engine tells you *which* option, *when* to
enter, where the stop and three targets sit, how long to hold, and whether an
under-water position is likely to recover.

---

## What's honest about this

Every metric — confidence %, signal strength, recovery probability, trade
quality — is a **transparent, documented heuristic** computed from a weighted
vote across all analysis modules. They are decision aids, **not guarantees**,
and this is not investment advice.

The app ships with a **realistic simulated MCX feed** so the entire engine and
UI run end-to-end with zero external dependencies. A real broker/vendor feed
(Zerodha Kite, Upstox, Angel One, TrueData) drops in behind one interface
(`MarketDataProvider`) **without any change to the engine or UI**.

---

## Architecture

```
                    ┌──────────────────────────────────────────┐
                    │            Next.js frontend (3000)         │
                    │  Decision Card · TradingView futures chart │
                    │  Option premium chart · Watchlist · Alerts │
                    │  Indicator grid · News · Score breakdown   │
                    └───────────────▲────────────────────────────┘
                                    │ WebSocket /ws  (+ REST)
                    ┌───────────────┴────────────────────────────┐
                    │              FastAPI engine (8000)          │
                    │  tick loop → Snapshot every second          │
                    │                                             │
                    │  Decision Engine (weighted voting)          │
                    │   ├─ indicators  (EMA/VWAP/RSI/MACD/ATR/    │
                    │   │   ADX/SuperTrend/Bollinger/momentum)    │
                    │   ├─ structure   (S/R, HH/HL, breakout,     │
                    │   │   candles, crash detector)              │
                    │   ├─ options     (PCR, OI, Greeks, exp move)│
                    │   ├─ news         sentiment aggregator      │
                    │   └─ recovery engine                        │
                    │                                             │
                    │  MarketDataProvider (abstract)              │
                    │   └─ SimulatedProvider  ◄── swap for broker │
                    └─────────────────────────────────────────────┘
             (Redis + PostgreSQL wired in compose for the next phase)
```

Directory layout:

```
quantum-trader/
├─ backend/                 FastAPI + Python analysis engine
│  └─ app/
│     ├─ main.py            REST + WebSocket, tick broadcast loop
│     ├─ state.py          position, alerts, journal, snapshot assembly
│     ├─ config.py          env-driven settings
│     ├─ models.py          pydantic schemas
│     ├─ market/            provider interface + simulated feed + BS pricer
│     ├─ analysis/          indicators, structure, options, news
│     └─ engine/            decision engine + recovery engine
├─ frontend/                Next.js 14 + TS + Tailwind
│  ├─ app/                  layout + single dashboard page
│  ├─ components/           DecisionCard, charts, panels, alerts…
│  └─ lib/                  types, api client, live-data WS hook
├─ docker-compose.yml       backend + frontend + redis + postgres
├─ .env.example
└─ DEPLOYMENT.md            local, Docker and AWS deployment guide
```

---

## Quick start (local, no Docker)

**1. Backend**
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env          # optional; defaults work out of the box
uvicorn app.main:app --reload --port 8000
```

**2. Frontend** (separate terminal)
```bash
cd frontend
npm install
npm run dev                       # http://localhost:3000
```

Open **http://localhost:3000**. The dashboard connects over WebSocket and
updates every second.

## Quick start (Docker)

```bash
cp .env.example .env
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" >> .env   # required by compose
docker compose up --build
# frontend → http://localhost:3000   backend → http://localhost:8000/health
```

---

## The Decision Engine

It **never** trades on a single indicator. Each module casts a *signed,
weighted vote* (bullish +, bearish −). Votes are aggregated into a net
directional score and an agreement ratio, which produce four scores —
**BUY / SELL / WAIT / EXIT** — plus a confidence %. Strike selection then picks
the most liquid ~0.5-delta option on the favoured side; ATR + option delta set
the stop and three targets; holding time and recovery probability follow from
signal strength and Greeks.

Inputs currently voting: EMA trend, VWAP, momentum, RSI, MACD, SuperTrend, ADX
trend strength, Bollinger, market structure (HH/HL/LH/LL), breakout/breakdown/
retest, candlestick price-action, volume spike, PCR, institutional OI build-up,
and news/geopolitical sentiment. Market state is classified as TRENDING /
RANGING / VOLATILE / NEWS_MODE / REVERSAL / BREAKOUT.

---

## Connecting a real feed

1. Implement `app/market/<broker>.py` subclassing `MarketDataProvider`
   (`futures_price`, `futures_candles`, `option_chain`, `option_candles`,
   `news`, `step`).
2. Register it in `app/market/provider.py::build_provider`.
3. Set `QT_DATA_PROVIDER=<broker>` and supply credentials via environment /
   secrets manager (see `.env.example`). Nothing else changes.

Most broker APIs need a daily OAuth/TOTP login to mint an access token; script
it once and cache the token (see DEPLOYMENT.md).

---

## Roadmap (phased)

- **Phase 1 (this repo):** engine + UI + simulated feed, Docker, real reasons.
- **Phase 2:** live broker feed, real option Greeks/OI, PostgreSQL trade
  journal, Redis pub/sub for multi-client scale.
- **Phase 3:** live news scrapers/APIs (Reuters/Moneycontrol/EIA/OPEC),
  market replay, learning engine, AWS deployment.

See `DEPLOYMENT.md` for AWS.

> **Disclaimer:** For research/education. Not investment advice. Trading
> leveraged commodity options carries substantial risk.
