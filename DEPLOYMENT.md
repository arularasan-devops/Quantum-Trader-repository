# Quantum Trader — Deployment Guide

Covers local dev, self-hosted Docker, connecting a live broker feed, and a
production AWS blueprint. Security posture follows least-privilege, no
hard-coded secrets, pinned container images and non-root runtime.

---

## 1. Local development

See README "Quick start (local, no Docker)". Backend on `:8000`, frontend on
`:3000`. `QT_DATA_PROVIDER=simulated` needs no external services.

## 2. Self-hosted Docker

```bash
cp .env.example .env
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" >> .env
docker compose up --build -d
```

- Backend, frontend, Redis and PostgreSQL start together.
- Images are pinned by version **and SHA256 digest**; containers run as a
  non-root user with health checks.
- Put a TLS-terminating reverse proxy (nginx / Caddy / Traefik) in front for
  anything beyond localhost. Never expose Redis/Postgres ports publicly.

## 3. Connecting a live broker feed

1. Implement `backend/app/market/<broker>.py` against `MarketDataProvider`.
2. Register in `build_provider()`; set `QT_DATA_PROVIDER=<broker>`.
3. Provide credentials as **environment variables / secrets-manager refs**
   (never in the image or git). Examples in `.env.example`.

### Daily token flow
Zerodha/Upstox/Angel One issue a short-lived access token after an interactive
login. Recommended pattern:
- Run a small scheduled login job (Playwright / requests) each morning before
  market open, store the token in a secret store (AWS Secrets Manager) or
  Redis with a TTL, and have the provider read it at startup / on 401.
- Store TOTP seeds as secrets prefixed `_2FA_...`.

## 4. AWS production blueprint

```
Route 53 ─► CloudFront ─► ALB (TLS 1.2+, ACM cert)
                             │
                 ┌───────────┴───────────┐
                 ▼                        ▼
        ECS Fargate: frontend    ECS Fargate: backend (WebSocket)
                                          │
                         ┌────────────────┼───────────────┐
                         ▼                ▼                ▼
                 ElastiCache (Redis)  RDS PostgreSQL   Secrets Manager
                   pub/sub + cache    journal/history   broker creds
```

Guidelines:
- **Secrets:** AWS Secrets Manager / SSM Parameter Store (SecureString). Inject
  as ECS task secrets; never bake into images or task-def plaintext env.
- **Networking:** ALB + tasks in private subnets; Redis/RDS in isolated subnets
  with security groups scoped to the backend task SG only. No public DB access.
  WebSocket needs ALB idle timeout raised and stickiness enabled.
- **Images:** build in CI, scan (Trivy/ECR scan), push to ECR by digest.
- **IAM:** one least-privilege task role per service; no wildcard `*` actions.
- **Scale:** backend is currently single-instance stateful (in-memory tick
  loop). For multi-instance, move the tick loop to one worker publishing
  Snapshots to Redis; API/WebSocket instances subscribe and fan out. Move the
  trade journal and learning-engine data to RDS.
- **Observability:** CloudWatch logs/metrics + alarms (failed logins,
  security-group changes, cert expiry, unusual egress) per the org policy.
- **CI/CD:** deploy only through your existing pipeline to ECR/ECS — no ad-hoc
  public deployments.

## 5. Production hardening checklist

- [ ] `QT_CORS_ORIGINS` set to the exact frontend origin (no `*`).
- [ ] TLS everywhere; HSTS at the edge.
- [ ] All secrets in a manager; `.env` never committed.
- [ ] Container images pinned by digest, non-root, read-only FS where possible.
- [ ] RDS/Redis private, encrypted at rest, automated backups.
- [ ] Rate-limit and authenticate the REST endpoints (add auth before exposing
      `buy`/`sell` beyond a trusted single-user desktop context).
- [ ] Structured audit logging of every order action.

## Docker Compose runtime (standard path)

```bash
cp .env.example .env            # set POSTGRES_PASSWORD (URL-safe chars), QT_CAPITAL, QT_LIVE_ARM_TOKEN
docker compose config -q        # validate
docker compose up -d --build    # backend, frontend, postgres, redis — waits on healthchecks
docker compose ps               # all should be (healthy)
curl -s localhost:8000/health
curl -s localhost:8000/api/db/status   # backend=postgresql, schema_version, row counts
curl -s localhost:8000/api/kill-switch
docker compose logs -f backend
docker compose restart backend  # data persists (pg-data, backend-data, redis-data volumes)
docker compose down             # keeps volumes; `down -v` DELETES all data
```

Schema: the research store applies additive migrations on startup and records
them in `schema_migrations` (see `/api/db/status`). Inside Compose the backend
always uses `postgres:5432` (never `localhost`).

## Live feed / live trading (operator only)
1. Put Angel One credentials in `.env` yourself (`SMARTAPI_*`), set
   `QT_DATA_PROVIDER=angelone`. Never share them with an AI agent.
2. Read-only check: `cd backend && python scripts/verify_feed_readonly.py CRUDEOIL`
   (0 PASS / 1 FAIL / 2 BLOCKED). It refuses to place orders.
3. A dead or unauthenticated feed shows **DISCONNECTED**, never simulated prices.
4. Live arming, live orders and kill-switch release require the
   `X-QT-Live-Token` header = `QT_LIVE_ARM_TOKEN` (browser: set
   `localStorage.qt_live_token`). Engage the kill switch any time:
   `curl -XPOST localhost:8000/api/kill-switch -H 'Content-Type: application/json' -d '{"engaged":true}'`.
5. Real orders remain blocked by `REAL_MONEY_EXECUTION_ENABLED=False` in
   `backend/app/ai/safety.py`. Changing it is a deliberate, reviewed human decision.
