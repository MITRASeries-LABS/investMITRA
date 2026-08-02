# ClearedCircle — Phase 1 Infrastructure

**Cost: ₹0/month**

| Layer | Local dev | Production (free) |
|---|---|---|
| Orchestration | Prefect runs locally | **Prefect Cloud** free tier (3 users, unlimited flows) |
| Metadata DB | Docker postgres:15 | **Supabase** free (500MB Postgres 15) |
| Time-series DB | Docker timescaledb | **Supabase** free (plain partitioned table) |
| Object store | Docker MinIO | **Cloudflare R2** (10GB, 10M reads/mo, zero egress) |
| Worker | Your laptop | **Render.com** free dyno or laptop |
| Cache | Docker Redis | **Redis Cloud** free (30MB) |

---

## Project Structure

```
clearedcircle/
├── docker/
│   ├── docker-compose.yml      # 4 local services (postgres, timescale, minio, redis)
│   └── Dockerfile.worker       # Build once, deploy anywhere
├── infra/sql/
│   ├── init_postgres.sql       # Supabase schema (paste into SQL editor)
│   └── init_timescale.sql      # Local TimescaleDB (auto-runs in Docker)
├── src/
│   ├── config/
│   │   ├── storage.py          # MinIO ↔ Cloudflare R2 switch (env vars only)
│   │   └── database.py         # Local Postgres ↔ Supabase switch (env vars only)
│   ├── connectors/
│   │   ├── base.py             # BaseConnector — all connectors implement this
│   │   ├── nse_bhavcopy.py     # First working connector
│   │   └── flows/
│   │       └── ingest_nse_bhavcopy.py   # Prefect flow
│   ├── transforms/
│   │   └── lake_writer.py      # Routes to raw/ or quarantine/
│   └── quality/
│       └── db_logger.py        # Audit trail in Supabase
├── .env.example
└── requirements.txt
```

---

## Local Dev — Start in 3 commands

```bash
# 1. Environment
cp .env.example .env

# 2. Start Docker services
docker compose -f docker/docker-compose.yml up -d

# 3. Install Python deps
pip install -r requirements.txt && python -m spacy download en_core_web_sm

# Test the first connector
python -m src.connectors.flows.ingest_nse_bhavcopy
```

**Local service URLs:**

| Service | URL | Login |
|---|---|---|
| MinIO Console | http://localhost:9001 | minioadmin / minioadmin |
| PostgreSQL | localhost:5432 | cc / cc |
| TimescaleDB | localhost:5433 | cc / cc |
| Redis | localhost:6379 | — |

---

## Production Setup

### 1. Supabase (free)
1. Create project at supabase.com (ap-south-1 region)
2. SQL Editor → paste `infra/sql/init_postgres.sql` → Run
3. Copy **Session mode pooler** connection string → `CC_POSTGRES_URL` + `CC_TIMESCALE_URL` in `.env`

> Note: Supabase free tier doesn't have TimescaleDB. `init_postgres.sql` creates `equity_prices` as a declarative-partitioned Postgres table instead — identical columns, same queries.

### 2. Cloudflare R2 (free)
1. Cloudflare Dashboard → R2 → Create bucket: `cc-raw`, `cc-quarantine`
2. R2 → Manage API Tokens → Create token (Object Read & Write)
3. Copy Account ID, Access Key, Secret Key → `.env`
4. Set `AWS_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com`

### 3. Prefect Cloud (free)
1. Sign up at app.prefect.io (free: 3 users, unlimited flows)
2. Create workspace → Create work pool named `cc-worker-pool`
3. Settings → API Keys → Create key → set `PREFECT_API_KEY` in `.env`
4. Deploy flows:
   ```bash
   prefect deploy src/connectors/flows/ingest_nse_bhavcopy.py:ingest_nse_bhavcopy \
     --name nse-bhavcopy-daily \
     --cron "30 14 * * 1-5" \
     --pool cc-worker-pool
   ```

### 4. Worker — two options (both free)
**Option A: Your laptop (simplest)**
```bash
export PREFECT_API_KEY=pnu_...
export PREFECT_API_URL=https://api.prefect.cloud/api/accounts/.../workspaces/...
prefect worker start --pool cc-worker-pool
```

**Option B: Render.com free dyno**
```bash
# Build and push worker image to Docker Hub (free)
docker build -f docker/Dockerfile.worker -t yourdockerhub/cc-worker:latest .
docker push yourdockerhub/cc-worker:latest
```
Then on Render.com: New → Web Service → Docker → use image above → set all env vars.

---

## Adding a New Connector

1. `src/connectors/{source_id}.py` — extend `BaseConnector`, implement `fetch()` + `backfill()`
2. `src/connectors/flows/ingest_{source_id}.py` — copy the NSE Bhavcopy flow, change names
3. Deploy to Prefect Cloud with its own cron schedule
4. The source is already seeded in `source_registry` (see `init_postgres.sql`)

---

## Data Quality Rules

| Score | Destination |
|---|---|
| ≥ 50 | `cc-raw` bucket — enters transformation pipeline |
| < 50 | `cc-quarantine` bucket — manual review required |
| 30d avg < 60 | Source auto-disabled + Slack alert |
