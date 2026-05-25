# Rate Limiter Agents

An AI-powered anomaly detection service that continuously monitors rate limiter telemetry and recommends actions using a multi-agent pipeline backed by Claude.

![CI](https://github.com/raviteja/rate-limit-agent/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Docker](https://img.shields.io/badge/docker-multi--platform-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## Overview

Three specialist agents analyze the last 15 minutes of rate-limit logs in parallel. An orchestrator agent synthesizes their signals into a final severity verdict and recommended action. Results are persisted to PostgreSQL and exposed through a REST API with a built-in dashboard.

### Agent Pipeline

```mermaid
flowchart TB
    subgraph Sources["Data Sources"]
        RDB[(Rate Limiter DB\nread-only)]
        BM[(Baseline Memory\n7-day rolling avg)]
    end

    subgraph Agents["Specialist Agents  ·  run in parallel"]
        EPA["ErrorPatternAgent\nblock rate · error rate · 5xx"]
        TBHA["TokenBucketHealthAgent\ntoken depletion · near-limit %"]
        TPA["TopPathsAgent\nper-endpoint traffic · block rate"]
    end

    subgraph AI["LLM Provider (Anthropic / OpenAI)"]
        C1(["claude-haiku-4-5 / gpt-4o-mini"])
    end

    subgraph Orchestration["Orchestrator"]
        ORC["Escalation Rules\n→ final severity + action"]
    end

    subgraph Outputs["Outputs"]
        ADB[(Agent DB\nread-write)]
        API["FastAPI REST API\n/agents · /dashboard · /evals"]
        DASH["Web Dashboard\n/static/index.html"]
    end

    RDB --> EPA & TBHA & TPA
    EPA & TBHA & TPA <-->|prompt / response| C1
    EPA & TBHA & TPA --> ORC
    BM --> ORC
    ORC <-->|prompt / response| C1
    ORC --> ADB
    EPA & TBHA & TPA --> ADB
    ADB --> API --> DASH
```

### Scheduling

```mermaid
flowchart LR
    SCH["APScheduler\nevery N minutes"]
    SCH -->|triggers| EPA["ErrorPatternAgent"]
    SCH -->|triggers| TBHA["TokenBucketHealthAgent"]
    SCH -->|triggers| TPA["TopPathsAgent"]
    EPA & TBHA & TPA --> ORC["Orchestrator"]
    ORC -->|persist results| DB[(Agent DB)]

    DAILY["Daily Cron\n00:00 UTC"] -->|triggers| EVALS["Eval Runner\nscenario accuracy check"]
    EVALS --> DB
```

## Tech Stack

| Layer | Technology |
|---|---|
| API framework | FastAPI + Uvicorn |
| AI | Anthropic Claude Haiku (`claude-haiku-4-5-20251001`) or OpenAI |
| Database | PostgreSQL via SQLAlchemy + Alembic |
| Scheduler | APScheduler (background, configurable interval) |
| Container | Docker (multi-stage, non-root, tini PID 1) |
| CI/CD | GitHub Actions → Docker Hub → SSH deploy |

## Project Structure

```
rate_limiter_agents/
├── main.py                     # FastAPI app, scheduler setup, startup/shutdown
├── config.py                   # Env var loading
├── database.py                 # SQLAlchemy engines and session factories
├── models.py                   # ORM models (read-only rate limiter + agent DBs)
├── scheduler.py                # Pipeline entrypoint called on interval
├── logging_config.py           # Structured logging setup
├── schemas.py                  # Pydantic request/response schemas
├── agents/
│   ├── error_pattern.py        # Analyzes block rate and error metrics
│   ├── token_bucket_health.py  # Analyzes token depletion levels
│   ├── top_paths.py            # Analyzes per-endpoint traffic and block rates
│   └── orchestrator.py         # Synthesizes signals into final verdict
├── providers/
│   ├── base.py                 # Abstract LLM provider interface
│   ├── anthropic_provider.py   # Anthropic Claude implementation
│   ├── openai_provider.py      # OpenAI implementation
│   └── factory.py              # Provider selection from env config
├── tools/
│   ├── metrics_aggregator.py   # Builds structured summaries from raw logs
│   └── memory_service.py       # 7-day rolling baseline (avg RPS, block rate)
├── routers/
│   ├── agents.py               # POST /agents/run, GET /agents/history
│   ├── dashboard.py            # GET /dashboard/summary|timeline|baseline|cost|apps
│   └── evals.py                # POST /evals/run, GET /evals/results|summary
├── evals/
│   ├── scenarios.py            # Labeled test scenarios for each agent
│   └── runner.py               # Runs scenarios and stores accuracy metrics
└── static/
    └── index.html              # Built-in monitoring dashboard
alembic/                        # Database migration scripts
tests/                          # pytest test suite
```

## Agents

### ErrorPatternAgent
Examines block rate, error rate, and 5xx responses over the last 15 minutes.

| Threshold | Severity |
|---|---|
| error_rate > 50% or any 5xx | critical |
| error_rate > 30% | high |
| error_rate > 20% | medium |
| error_rate > 5% | low |

### TokenBucketHealthAgent
Measures how close clients are to exhausting their rate-limit buckets.

| Threshold | Severity |
|---|---|
| near_depletion_pct > 70% or depleted > 5 | critical |
| near_depletion_pct > 50% | high |
| near_depletion_pct > 30% | medium |
| near_depletion_pct > 10% | low |

### TopPathsAgent
Identifies which endpoints are most targeted and have the highest block rates.

| Threshold | Severity |
|---|---|
| block_rate > 80% on any path | critical |
| block_rate > 60% | high |
| block_rate > 40% | medium |
| block_rate > 20% | low |

### Orchestrator
Applies escalation rules to the three agent signals:

- Any agent is `critical` → final = **critical**
- 2+ agents are `high` → final = **critical**
- 1 agent is `high` → final = **high**
- 2+ agents are `medium` → final = **high**
- 1 agent is `medium` → final = **medium**

Action mapping: `critical → block`, `high → throttle`, `medium → alert`, `low/none → monitor`

---

## Local Development

### Prerequisites

- Python 3.12+
- PostgreSQL 14+ (two databases — see [Environment Variables](#environment-variables))
- An [Anthropic API key](https://console.anthropic.com) or [OpenAI API key](https://platform.openai.com)

### 1. Clone and configure

```bash
git clone <repo-url>
cd rate-limit-agent

cp .env.example .env
# Edit .env with your database DSNs and API key
```

### 2. Create virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
pip install -r requirements-dev.txt  # adds pytest, ruff, httpx
```

### 3. Run database migrations

```bash
# Apply all migrations to the agent DB
alembic upgrade head

# Check current migration state
alembic current

# Create a new migration after model changes
alembic revision --autogenerate -m "describe the change"
```

> The rate limiter DB is read-only — no migrations are needed for it.

### 4. Start the server

```bash
uvicorn rate_limiter_agents.main:app --reload --port 8000
```

| URL | Description |
|---|---|
| `http://localhost:8000` | API root |
| `http://localhost:8000/health` | Liveness check |
| `http://localhost:8000/docs` | Interactive Swagger UI |
| `http://localhost:8000/redoc` | ReDoc API reference |
| `http://localhost:8000/static/index.html` | Monitoring dashboard |

---

## Environment Variables

Copy `.env.example` to `.env` and set the following:

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_PROVIDER` | No | `anthropic` | LLM backend: `anthropic` or `openai` |
| `LLM_MODEL` | No | `claude-haiku-4-5-20251001` | Model name for the selected provider |
| `ANTHROPIC_API_KEY` | If provider=anthropic | — | Anthropic API key |
| `OPENAI_API_KEY` | If provider=openai | — | OpenAI API key |
| `RATE_LIMITER_DB` | Yes | — | PostgreSQL DSN for the rate limiter DB (read-only: `rate_limit_log`, `app_info`, `rate_limit_plan`) |
| `AGENT_DB_URL` | Yes | — | PostgreSQL DSN for the agent DB (read-write: `agent_results`, `orchestrator_results`, `baseline_memory`, `eval_run_results`) |
| `AGENT_INTERVAL_MINUTES` | No | `15` | How often the agent pipeline runs |
| `CORS_ORIGINS` | No | `http://localhost:8000,http://localhost:3000` | Comma-separated allowed CORS origins |

Agent DB tables are auto-created on startup via SQLAlchemy. Alembic manages schema migrations.

---

## Testing and Linting

```bash
# Run all tests
pytest

# Run with verbose output and coverage
pytest -v --tb=short

# Run a specific test file
pytest tests/test_metrics_aggregator.py

# Lint with ruff
ruff check .

# Auto-fix lint issues
ruff check . --fix

# Format code
ruff format .
```

---

## Docker

### Build

```bash
docker build -t rate-limit-agent .

# Build with version metadata
docker build \
  --build-arg VERSION=1.0.0 \
  --build-arg REVISION=$(git rev-parse --short HEAD) \
  -t rate-limit-agent .
```

### Run with inline environment variables

```bash
docker run -d \
  --name rate-limit-agent \
  --restart unless-stopped \
  -p 8000:8000 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e RATE_LIMITER_DB=postgresql://user:pass@host:5432/rate_limiter \
  -e AGENT_DB_URL=postgresql://user:pass@host:5432/rate_limiter_agents \
  -e AGENT_INTERVAL_MINUTES=15 \
  rate-limit-agent
```

### Run with an env file

```bash
docker run -d \
  --name rate-limit-agent \
  --restart unless-stopped \
  -p 8000:8000 \
  --env-file .env \
  rate-limit-agent
```

### Useful container commands

```bash
# View logs
docker logs rate-limit-agent -f

# Check health status
docker inspect --format='{{.State.Health.Status}}' rate-limit-agent

# Open a shell inside the container
docker exec -it rate-limit-agent /bin/bash

# Stop and remove
docker stop rate-limit-agent && docker rm rate-limit-agent
```

The image uses a non-root user (`appuser`), `tini` as PID 1 for clean signal forwarding, and a Python-native healthcheck on `/health` (no `curl` or `wget` required).

### Docker Compose (local development with PostgreSQL)

Create a `docker-compose.yml` at the project root:

```yaml
services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: app_user
      POSTGRES_PASSWORD: app_password
      POSTGRES_DB: rate_limiter_agents
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app_user"]
      interval: 5s
      timeout: 3s
      retries: 5

  app:
    build: .
    restart: unless-stopped
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      db:
        condition: service_healthy

volumes:
  pgdata:
```

```bash
# Start the full stack
docker compose up -d

# Tail logs from all services
docker compose logs -f

# Stop and remove containers (keep volumes)
docker compose down

# Stop and remove containers AND volumes
docker compose down -v
```

---

## API Reference

### Health

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness check — returns `{"status": "ok"}` |

### Agents

| Method | Path | Description |
|---|---|---|
| POST | `/agents/run` | Manually trigger the full agent pipeline. Query param: `app_info_id` (optional — runs all enabled apps if omitted) |
| GET | `/agents/history` | Paginated agent run history. Params: `app_info_id`, `limit` (max 100), `offset` |

### Dashboard

| Method | Path | Description |
|---|---|---|
| GET | `/dashboard/apps` | List all enabled apps |
| GET | `/dashboard/summary` | Aggregate counts, severity breakdown, token/cost totals, and baseline metrics |
| GET | `/dashboard/timeline` | Paginated orchestrator runs with per-agent detail. Filter by `filter` (all/anomaly/critical/high/medium/low) and `agent` (all/error/token/paths) |
| GET | `/dashboard/baseline` | Current 7-day rolling baseline per app |
| GET | `/dashboard/cost` | Token and USD cost breakdown by agent, today and 7-day series |

### Evals

| Method | Path | Description |
|---|---|---|
| POST | `/evals/run` | Run all labeled eval scenarios and store results |
| GET | `/evals/results` | Recent eval results grouped by run with accuracy metrics |
| GET | `/evals/summary` | Accuracy trend across all runs (severity and action accuracy %) |

Interactive API docs: `http://localhost:8000/docs`

### Quick API examples

```bash
# Health check
curl http://localhost:8000/health

# Manually trigger the agent pipeline
curl -X POST http://localhost:8000/agents/run

# Trigger for a specific app
curl -X POST "http://localhost:8000/agents/run?app_info_id=1"

# View agent run history
curl "http://localhost:8000/agents/history?limit=20&offset=0"

# Dashboard summary
curl http://localhost:8000/dashboard/summary

# Run evals
curl -X POST http://localhost:8000/evals/run

# Check eval accuracy trends
curl http://localhost:8000/evals/summary
```

---

## CI/CD

Two GitHub Actions workflows handle the full delivery pipeline:

```mermaid
flowchart LR
    PR["Push / PR\nto main"] --> CI

    subgraph CI["CI  •  ci.yml"]
        LINT["ruff lint"] --> TEST["pytest"]
        TEST --> BUILD["docker buildx\nlinux/amd64 + arm64\nSBOM + provenance"]
        BUILD --> PUSH["Push to Docker Hub\n:latest + :sha"]
    end

    CI -->|"CI passes on main"| CD

    subgraph CD["CD  •  cd.yml"]
        direction TB
        DEPLOY["SSH deploy to prod"] --> HEALTH["Health check\n90s polling"]
        HEALTH -->|"healthy"| DONE["Deploy complete"]
        HEALTH -->|"unhealthy"| ROLLBACK["Auto-rollback\nto previous image"]
    end
```

**CI** (`.github/workflows/ci.yml`) — runs on every push and PR to `main`:
1. Lints with `ruff`
2. Runs `pytest`
3. Builds and pushes a multi-platform image (`linux/amd64`, `linux/arm64`) to Docker Hub with SBOM and provenance attestations

**CD** (`.github/workflows/cd.yml`) — triggers after CI passes on `main`:
1. SSH-deploys the new image tagged with the exact commit SHA
2. Polls Docker's native healthcheck for up to 90 seconds
3. Automatically rolls back to the previous image on failure

### Required GitHub Secrets

| Secret | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key for production |
| `DOCKERHUB_USERNAME` | Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub access token |
| `RATE_LIMITER_DB` | Production rate limiter DB DSN |
| `AGENT_DB_URL` | Production agent DB DSN |
| `LLM_PROVIDER` | `anthropic` or `openai` |
| `LLM_MODEL` | Model name |
| `CORS_ORIGINS` | Comma-separated allowed origins |
| `AGENT_INTERVAL_MINUTES` | Pipeline run interval |
| `SSH_HOST` | Production server hostname or IP |
| `SSH_USER` | SSH username |
| `SSH_PRIVATE_KEY` | SSH private key |

---

## Eval System

The eval system validates agent accuracy against labeled scenarios defined in `evals/scenarios.py`. Each scenario specifies input metrics and the expected `severity` and `action` output. A daily cron job runs all scenarios automatically.

```bash
# Run evals manually via API
curl -X POST http://localhost:8000/evals/run

# View detailed results per scenario
curl http://localhost:8000/evals/results

# Check accuracy trends over time
curl http://localhost:8000/evals/summary
```

Results are stored in `eval_run_results` and include per-run severity and action accuracy percentages.

---

## Database Migrations (Alembic)

```bash
# Apply all pending migrations
alembic upgrade head

# Roll back one migration
alembic downgrade -1

# Roll back to a specific revision
alembic downgrade 0001

# Show migration history
alembic history --verbose

# Show current applied revision
alembic current

# Generate a new migration from model changes
alembic revision --autogenerate -m "add index on run_at"
```

The `alembic.ini` at the project root points to `alembic/` for migration scripts. The `AGENT_DB_URL` env var is used as the database connection.
