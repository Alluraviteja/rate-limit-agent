# Rate Limit Agent — Deep Dive Documentation

## Table of Contents

1. [Project Overview & Purpose](#1-project-overview--purpose)
2. [Architecture Overview](#2-architecture-overview)
3. [Directory Structure](#3-directory-structure)
4. [Tech Stack](#4-tech-stack)
5. [Database Design](#5-database-design)
   - 5.1 [Rate Limiter DB (Read-Only)](#51-rate-limiter-db-read-only)
   - 5.2 [Agent DB (Read-Write)](#52-agent-db-read-write)
   - 5.3 [Schema Decisions](#53-schema-decisions)
6. [Multi-Agent Pipeline](#6-multi-agent-pipeline)
   - 6.1 [ErrorPatternAgent](#61-errorpatternagent)
   - 6.2 [TokenBucketHealthAgent](#62-tokenbuckethealthagent)
   - 6.3 [TopPathsAgent](#63-toppathsagent)
   - 6.4 [Orchestrator](#64-orchestrator)
   - 6.5 [Agent Interaction Flow](#65-agent-interaction-flow)
7. [LLM Provider Abstraction](#7-llm-provider-abstraction)
   - 7.1 [BaseLLMProvider](#71-basellmprovider)
   - 7.2 [AnthropicProvider](#72-anthropicprovider)
   - 7.3 [OpenAIProvider](#73-openaiprovider)
   - 7.4 [Provider Factory](#74-provider-factory)
   - 7.5 [Retry & Timeout Strategy](#75-retry--timeout-strategy)
8. [Metrics Aggregation (Tools Layer)](#8-metrics-aggregation-tools-layer)
   - 8.1 [build_error_summary](#81-build_error_summary)
   - 8.2 [build_token_health_summary](#82-build_token_health_summary)
   - 8.3 [build_top_paths_summary](#83-build_top_paths_summary)
9. [Memory Service & Baseline System](#9-memory-service--baseline-system)
10. [API Layer](#10-api-layer)
    - 10.1 [Agents Router](#101-agents-router)
    - 10.2 [Dashboard Router](#102-dashboard-router)
    - 10.3 [Evals Router](#103-evals-router)
    - 10.4 [Health Endpoints](#104-health-endpoints)
11. [Scheduler & Background Jobs](#11-scheduler--background-jobs)
12. [Eval System](#12-eval-system)
    - 12.1 [Scenarios](#121-scenarios)
    - 12.2 [Runner](#122-runner)
    - 12.3 [Accuracy Metrics](#123-accuracy-metrics)
13. [Configuration & Environment Variables](#13-configuration--environment-variables)
14. [Logging & Observability](#14-logging--observability)
15. [Database Migrations (Alembic)](#15-database-migrations-alembic)
16. [Docker & Containerization](#16-docker--containerization)
17. [CI/CD Pipeline](#17-cicd-pipeline)
    - 17.1 [Continuous Integration](#171-continuous-integration)
    - 17.2 [Continuous Deployment](#172-continuous-deployment)
18. [Testing Strategy](#18-testing-strategy)
    - 18.1 [Test Fixtures & In-Memory DB](#181-test-fixtures--in-memory-db)
    - 18.2 [Unit Tests](#182-unit-tests)
19. [Key Design Decisions & Trade-offs](#19-key-design-decisions--trade-offs)
20. [Per-IP vs. Shared Rate Limiting Mode](#20-per-ip-vs-shared-rate-limiting-mode)
21. [Cost Tracking](#21-cost-tracking)
22. [Security Considerations](#22-security-considerations)
23. [Local Development Setup](#23-local-development-setup)

---

## 1. Project Overview & Purpose

**Rate Limit Agent** is an AI-powered anomaly detection service that monitors a database-backed rate limiter in real time. Every 15 minutes (configurable) it reads the last window of rate limit logs, runs three specialist AI agents in sequence, then passes their signals to an orchestrator agent that produces a single severity verdict and recommended action.

**Core problem it solves:** A rate limiter produces thousands of log entries per minute. A human operator cannot watch these continuously. This system uses LLMs to understand patterns across those logs and surfaces only meaningful signals — distinguishing a bot attack (`critical/block`) from an occasional noisy client (`low/monitor`).

**Key outcomes delivered:**
- Automated anomaly detection on rate limiter telemetry
- Severity classification: `none → low → medium → high → critical`
- Recommended action: `monitor → alert → throttle → block`
- Historical timeline of all agent runs accessible via REST API
- Interactive web dashboard
- Eval harness that validates LLM decision quality on labeled scenarios
- Daily automated eval runs for continuous regression checking
- Cost tracking per agent run

---

## 2. Architecture Overview

```
                        ┌─────────────────────────────┐
                        │   APScheduler (every 15 min) │
                        └────────────┬────────────────┘
                                     │ triggers
                                     ▼
                        ┌─────────────────────────────┐
                        │    execute_agent_pipeline()  │
                        └──┬──────────────────────────┘
                           │  reads logs (last 15–60 min)
                           ▼
          ┌────────────────────────────────────────────┐
          │            RATE_LIMITER_DB (Postgres)       │
          │   rate_limit_log, rate_limit_plan, app_info │
          └──────────────┬─────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
   ┌─────────────┐ ┌───────────┐ ┌──────────────┐
   │ErrorPattern │ │TokenBucket│ │  TopPaths    │
   │   Agent     │ │  Health   │ │   Agent      │
   └──────┬──────┘ └─────┬─────┘ └──────┬───────┘
          │              │              │
          │   calls LLM (Anthropic / OpenAI)
          ▼              ▼              ▼
     AgentResult    AgentResult    AgentResult
          │              │              │
          └──────────────┼──────────────┘
                         ▼
                 ┌───────────────┐
                 │  Orchestrator │ ← reads baseline + recent history
                 └───────┬───────┘
                         │ calls LLM
                         ▼
                  OrchestratorResult
                  (final_severity, action)
                         │
                         ▼
          ┌──────────────────────────────┐
          │       AGENT_DB (Postgres)    │
          │  agent_results               │
          │  orchestrator_results        │
          │  baseline_memory             │
          │  eval_run_results            │
          └──────────────────────────────┘
                         │
                         ▼
          ┌──────────────────────────────┐
          │      FastAPI REST API        │
          │  /agents/  /dashboard/  /evals/ │
          └──────────────────────────────┘
                         │
                         ▼
              Browser Dashboard (index.html)
```

**Data flows in one direction:** logs → agents → orchestrator → persistence → API. No agent calls another agent directly; the orchestrator reads their already-persisted results passed in as Python objects.

---

## 3. Directory Structure

```
rate-limit-agent/
├── rate_limiter_agents/            # Main Python package
│   ├── main.py                     # FastAPI app wiring, middleware, startup/shutdown
│   ├── config.py                   # Environment variable loading (single source of truth)
│   ├── database.py                 # SQLAlchemy engine creation for both DBs
│   ├── models.py                   # ORM table definitions (2 metadata bases)
│   ├── schemas.py                  # Pydantic request/response models
│   ├── logging_config.py           # JSON structured logging + request_id context var
│   ├── scheduler.py                # APScheduler job definitions and pipeline runner
│   │
│   ├── agents/
│   │   ├── error_pattern.py        # ErrorPatternAgent
│   │   ├── token_bucket_health.py  # TokenBucketHealthAgent
│   │   ├── top_paths.py            # TopPathsAgent
│   │   └── orchestrator.py        # Orchestrator (synthesizes 3 agent signals)
│   │
│   ├── providers/
│   │   ├── base.py                 # BaseLLMProvider ABC + LLMResponse dataclass
│   │   ├── anthropic_provider.py   # Claude integration
│   │   ├── openai_provider.py      # GPT integration
│   │   └── factory.py             # get_provider() factory function
│   │
│   ├── tools/
│   │   ├── metrics_aggregator.py   # Raw log → structured metrics dict
│   │   └── memory_service.py       # 7-day rolling baseline get/update
│   │
│   ├── routers/
│   │   ├── agents.py               # POST /agents/run, GET /agents/history
│   │   ├── dashboard.py            # 5 dashboard read endpoints
│   │   └── evals.py               # POST /evals/run, GET /evals/results, /evals/summary
│   │
│   ├── evals/
│   │   ├── scenarios.py            # 5 labeled test scenarios with synthetic log factories
│   │   └── runner.py              # Eval execution engine
│   │
│   └── static/
│       └── index.html              # Single-page monitoring dashboard
│
├── alembic/
│   ├── env.py                      # Alembic environment (reads AGENT_DB_URL)
│   └── versions/
│       └── 0001_initial_agent_tables.py
│
├── tests/
│   ├── conftest.py                 # pytest fixtures (SQLite in-memory DBs)
│   ├── test_metrics_aggregator.py
│   ├── test_agent_helpers.py
│   ├── test_memory_service.py
│   └── test_routers.py
│
├── .github/workflows/
│   ├── ci.yml                      # Lint → typecheck → test → security scan → docker build
│   └── cd.yml                      # Build+push multi-arch image → SSH deploy → health rollback
│
├── Dockerfile                      # Multi-stage build (builder → runtime, non-root user, tini)
├── alembic.ini
├── requirements.txt
├── requirements-dev.txt
├── .env.example
└── README.md
```

---

## 4. Tech Stack

| Layer | Choice | Version | Why |
|---|---|---|---|
| Language | Python | 3.12+ | Async ecosystem, typing, LLM SDKs |
| Web Framework | FastAPI | 0.136.1 | Auto-generated OpenAPI docs, async-native, Pydantic integration |
| ASGI Server | Uvicorn | 0.46.0 | High-performance ASGI; works with FastAPI natively |
| LLM — Primary | Anthropic (Claude Haiku) | 0.97.0 | Fast, cheap, instruction-following for structured output |
| LLM — Alternative | OpenAI (GPT-4o-mini) | Latest | Swap-in via env var, same interface |
| ORM | SQLAlchemy | 2.0.49 | 2.0 style (Session), type-safe queries, dual-engine support |
| Migrations | Alembic | 1.18.4 | Versioned schema changes, auto-detect diff from models |
| DB Driver | psycopg2-binary | 2.9.12 | Synchronous Postgres driver (matches sync FastAPI routes) |
| Scheduling | APScheduler | 3.11.2 | In-process cron/interval jobs, no separate worker process |
| Numerics | NumPy | 2.2.6 | Efficient percentile/average calculations on log arrays |
| Testing | pytest | 8.3.5 | Fixtures, parametrize, JUnit XML output |
| HTTP Testing | httpx | 0.28.1 | Required by FastAPI TestClient |
| Linting | ruff | 0.11.7 | Fast, opinionated, single tool replaces flake8+isort |
| Type Checking | mypy | 1.15.0 | Static analysis, catches provider interface drift |
| Container | Docker multi-stage | Latest | Minimal runtime image, reproducible builds |
| PID 1 | tini | Latest | Correct signal forwarding inside Docker |
| Security Scan | Trivy | GHA action | Filesystem + image CVE scanning in CI |
| Secret Detection | TruffleHog | GHA action | Blocks accidental key commits |

---

## 5. Database Design

The system uses **two separate PostgreSQL databases**. This is a deliberate design choice:

- **`RATE_LIMITER_DB`** — Owned by the rate limiter service. The agent system only reads from it. No writes, no migrations.
- **`AGENT_DB_URL`** — Owned entirely by this service. Full control, managed by Alembic.

Keeping them separate means the agent service cannot accidentally corrupt the rate limiter's data, and the rate limiter can be upgraded independently.

### 5.1 Rate Limiter DB (Read-Only)

**`app_info`** — One row per monitored service.

| Column | Type | Notes |
|---|---|---|
| id | BigInteger PK | |
| service_name | String(255) | Human-readable name |
| service_port | Integer | |
| description | String(1000) | |
| enabled | Boolean | Only enabled apps are monitored |
| per_ip_address | Boolean | Switches agent thresholds (see §20) |
| created_at / updated_at | DateTime (TZ) | |

**`rate_limit_plan`** — Defines token bucket config per path pattern per app.

| Column | Type | Notes |
|---|---|---|
| id | BigInteger PK | |
| app_info_id | BigInteger FK → app_info | |
| path_pattern | String(500) | e.g., `/api/v1/*` |
| capacity | Integer | Max tokens in the bucket |
| refill_rate | Integer | Tokens added per refill period |
| refill_period_seconds | Integer | |
| enabled | Boolean | |

**`rate_limit_log`** — High-volume event log. Primary query target for agents.

| Column | Type | Notes |
|---|---|---|
| id | BigInteger PK | |
| app_info_id | BigInteger | App that generated this event |
| client_ip | String(45) | IPv4 or IPv6 |
| was_blocked | Boolean | True if request was rate limited |
| reason | String | Why it was blocked |
| http_method | String(10) | GET, POST, etc. |
| request_path | String(500) | Exact path |
| remaining_tokens | BigInteger | Tokens left in bucket after this request |
| trace_id | String(64) | For distributed tracing |
| response_code | Integer | HTTP status code |
| request_at | DateTime (TZ) | **Primary filter column** — indexed |
| retry_after_seconds | BigInteger | 429 header value |
| redis_failed | Boolean | True if Redis was unavailable (fallback mode) |
| is_bot | Boolean | Bot detection result |
| bot_name | String(100) | e.g., "Googlebot" |
| user_agent | Text | Full UA string |
| browser, os, device_type | String | Parsed UA fields |
| request_size | BigInteger | Bytes |
| referer | String(2048) | |

### 5.2 Agent DB (Read-Write)

**`agent_results`** — One row per specialist agent per pipeline run per app.

| Column | Type | Notes |
|---|---|---|
| id | BigInteger PK autoincrement | |
| app_info_id | BigInteger (indexed) | Which app |
| agent_name | String(50) | `error_pattern`, `token_bucket_health`, `top_paths` |
| anomaly_detected | Boolean | |
| severity | String(20) | `none/low/medium/high/critical` |
| spike_ratio | Numeric(10,4) | Nullable — only when relevant |
| block_rate_pct | Numeric(10,4) | |
| slope_pct | Numeric(10,4) | Trend direction |
| bot_ratio_pct | Numeric(10,4) | |
| peak_rps | Numeric(10,4) | |
| baseline_rps | Numeric(10,4) | |
| total_requests | Integer | |
| blocked_requests | Integer | |
| unique_ips | Integer | Nullable |
| redis_failures | Integer | Nullable |
| reason | Text | LLM-generated explanation |
| action | String(20) | `monitor/alert/throttle/block` |
| tokens_used | Integer | Input + output tokens |
| cost_usd | Numeric(12,8) | Actual API cost |
| run_at | DateTime (TZ, indexed) | When the run completed |

**`orchestrator_results`** — One row per pipeline run per app (final verdict).

| Column | Type | Notes |
|---|---|---|
| id | BigInteger PK autoincrement | |
| app_info_id | BigInteger (indexed) | |
| error_severity | String(20) | Copied from ErrorPatternAgent |
| token_severity | String(20) | Copied from TokenBucketHealthAgent |
| path_severity | String(20) | Copied from TopPathsAgent |
| final_severity | String(20) | Orchestrator's decision |
| anomaly_detected | Boolean | |
| reason | Text | LLM-generated synthesis |
| action | String(20) | Final recommended action |
| tokens_used | Integer | |
| cost_usd | Numeric(12,8) | |
| run_at | DateTime (TZ, indexed) | |

**`baseline_memory`** — One row per app, continuously updated.

| Column | Type | Notes |
|---|---|---|
| id | BigInteger PK | |
| app_info_id | BigInteger UNIQUE | One baseline per app |
| avg_rps_7d | Numeric(10,4) | Rolling 7-day average RPS |
| avg_block_rate_7d | Numeric(10,4) | Rolling 7-day average block rate |
| avg_bot_ratio_7d | Numeric(10,4) | Rolling 7-day average bot ratio |
| spike_threshold | Numeric(10,4) | Dynamic multiplier (2.5–4.0×) |
| sample_count | Integer | Capped at 672 (7d × 96 intervals) |
| last_updated | DateTime (TZ) | |

**`eval_run_results`** — Stores accuracy data from eval runs.

| Column | Type | Notes |
|---|---|---|
| id | BigInteger PK | |
| run_id | String(36) (indexed) | UUID groups checks from one eval run |
| scenario_name | String(100) | e.g., `high_error_rate` |
| agent_name | String(50) | Which agent was checked |
| expected_severity | String(20) | Ground truth label |
| actual_severity | String(20) | What the LLM actually said |
| expected_action | String(20) | |
| actual_action | String(20) | |
| severity_correct | Boolean | |
| action_correct | Boolean | |
| tokens_used | Integer | Nullable |
| cost_usd | Numeric(12,8) | Nullable |
| run_at | DateTime (TZ, indexed) | |

### 5.3 Schema Decisions

**Why `Numeric(10,4)` for percentages and rates?** Fixed-point avoids floating-point rounding errors when storing and retrieving metrics that will be displayed in dashboards.

**Why `Numeric(12,8)` for cost?** LLM costs are in fractions of a cent. 8 decimal places preserves precision even for runs costing 0.00001234 USD.

**Why indices on `(app_info_id, run_at)`?** The dashboard timeline query filters by app and orders by time. A composite index makes this fast even with millions of rows.

**Why UNIQUE on `baseline_memory.app_info_id`?** There must be exactly one baseline per app. A UNIQUE constraint enforces this at the DB level; the code does an upsert pattern (`get_or_create`).

---

## 6. Multi-Agent Pipeline

The pipeline runs one agent at a time (sequential, not parallel) so each agent's result can be read immediately and passed to the next stage. The orchestrator always runs last.

### 6.1 ErrorPatternAgent

**File:** `rate_limiter_agents/agents/error_pattern.py`

**What it analyzes:** HTTP error codes, block rates, and 5xx responses over the last **15 minutes**.

**System prompt logic (embedded in the agent):**

```
ANOMALY conditions (ANY triggers YES):
  - error_rate_pct > 20
  - block_rate_pct > 30
  - response_5xx > 0

SEVERITY escalation:
  - critical: error_rate > 50% OR any 5xx present
  - high:     error_rate > 30%
  - medium:   error_rate > 20%
  - low:      error_rate > 5%
  - none:     otherwise

ACTION mapping:
  - critical → block
  - high     → throttle
  - medium   → alert
  - low/none → monitor

Per-IP modifier: if blocks concentrated in 1–2 IPs, lower severity by one level
(concentrated attack is expected per-IP behavior, not a systemic problem)
```

**Metrics fed to the LLM:**

```json
{
  "window_minutes": 15,
  "total_requests": 842,
  "response_2xx": 610,
  "response_4xx": 180,
  "response_429": 52,
  "response_5xx": 0,
  "blocked_requests": 52,
  "block_rate_pct": 6.17,
  "error_rate_pct": 6.17,
  "top_error_paths": [{"path": "/api/search", "count": 30}],
  "top_block_reasons": [{"reason": "rate_limit_exceeded", "count": 52}]
}
```

**Per-IP mode adds:**

```json
{
  "unique_ips": 45,
  "top_blocking_ips": [{"ip": "1.2.3.4", "blocks": 40}],
  "ip_concentration_pct": 76.9
}
```

**Expected LLM output format (key:value lines):**

```
ANOMALY: NO
SEVERITY: low
ERROR_RATE: 6.17
BLOCK_RATE: 6.17
REASON: Block rate is elevated but concentrated in a single IP. Likely a single noisy client.
ACTION: monitor
```

**Parsing:** The agent splits each line on `:` and maps keys to `AgentResult` fields.

### 6.2 TokenBucketHealthAgent

**File:** `rate_limiter_agents/agents/token_bucket_health.py`

**What it analyzes:** Token bucket depletion levels. A depleted bucket means a client is being aggressively rate limited. Over-depletion can indicate a sustained attack or misconfigured client.

**Window:** Last **15 minutes**, up to 5000 rows.

**System prompt logic:**

```
ANOMALY conditions:
  - near_depletion_pct > 30   (more than 30% of requests arrived with < 10% tokens left)
  - depleted_count > 0        (any requests with 0 remaining tokens)

SEVERITY escalation:
  - critical: near_depletion_pct > 70 OR depleted > 5
  - high:     near_depletion_pct > 50
  - medium:   near_depletion_pct > 30
  - low:      near_depletion_pct > 10
  - none:     otherwise

Per-IP modifier: escalate only if MANY unique IPs are depleted
(many IPs depleted simultaneously = coordinated attack, worse than single IP)
```

**Key metric: `near_depletion_pct`**

The threshold for "near depletion" is dynamically computed as 10% of the observed `max_remaining_tokens` in the window. This adapts to the actual bucket size of the plan.

**Metrics example:**

```json
{
  "window_minutes": 15,
  "total_requests": 842,
  "avg_remaining_tokens": 48.3,
  "min_remaining_tokens": 0,
  "max_remaining_tokens": 100,
  "depleted_count": 3,
  "near_depletion_count": 210,
  "near_depletion_pct": 24.9,
  "top_token_consuming_paths": [{"path": "/api/search", "avg_remaining": 5.2}],
  "unique_ips": 45,
  "ips_near_depletion": 2,
  "ips_depleted": 1
}
```

### 6.3 TopPathsAgent

**File:** `rate_limiter_agents/agents/top_paths.py`

**What it analyzes:** Per-endpoint traffic distribution and block rates. Identifies whether attacks or anomalies are path-specific.

**Window:** Last **60 minutes** (longer than other agents to detect slower path-level attacks).

**System prompt logic:**

```
ANOMALY conditions:
  - Any single path has block_rate_pct > 50
  - Top path accounts for > 80% of total traffic (traffic concentration)

SEVERITY escalation (by highest-block-rate path):
  - critical: block_rate > 80% on any path
  - high:     block_rate > 60%
  - medium:   block_rate > 40%
  - low:      block_rate > 20%
  - none:     otherwise

Per-IP modifier: if blocks on a path are from 1–2 IPs, lower severity
(concentrated IP is expected per-IP behavior)
```

**Top paths by block rate only includes paths with ≥ 3 requests** to avoid false positives from rare endpoints.

**Metrics example:**

```json
{
  "window_minutes": 60,
  "total_requests": 3400,
  "unique_paths": 12,
  "top_paths_by_traffic": [
    {"path": "/api/search", "total": 2800, "blocked": 140, "block_rate_pct": 5.0, "top_method": "GET"}
  ],
  "top_paths_by_block_rate": [
    {"path": "/api/export", "total": 30, "blocked": 25, "block_rate_pct": 83.3, "top_method": "POST",
     "top_blocking_ips": [{"ip": "1.2.3.4", "blocks": 25}]}
  ]
}
```

### 6.4 Orchestrator

**File:** `rate_limiter_agents/agents/orchestrator.py`

**What it does:** Reads the three specialist agent results (passed as Python `AgentResult` objects), fetches baseline and recent history from the DB, builds a summary JSON, and calls the LLM to decide the final severity and action.

**System prompt decision tree:**

```
Input signals → Final severity rules:
  - Any agent = critical          → final = critical
  - 2+ agents = high              → final = critical
  - 1 agent = high                → final = high
  - 2+ agents = medium            → final = high
  - 1 agent = medium              → final = medium
  - Otherwise                     → highest agent severity or none

Trend modifier:
  - Previous 3 runs escalating    → bump severity up one level
  - Previous 3 runs recovering    → bump severity down one level

Action mapping:
  - critical → block
  - high     → throttle
  - medium   → alert
  - low/none → monitor
```

**Input to the LLM:**

```json
{
  "rate_limiting_mode": "shared",
  "error_pattern": {"severity": "high", "block_rate": 31.5, "reason": "..."},
  "token_bucket_health": {"severity": "medium", "reason": "..."},
  "top_paths": {"severity": "none", "block_rate": 0.0, "reason": "..."},
  "baseline": {"avg_rps_7d": 12.4, "avg_block_rate_7d": 3.1},
  "recent_history": [
    {"run_at": "2026-05-12T09:30Z", "severity": "low", "action": "monitor", "reason": "..."},
    {"run_at": "2026-05-12T09:45Z", "severity": "medium", "action": "alert", "reason": "..."},
    {"run_at": "2026-05-12T10:00Z", "severity": "high", "action": "throttle", "reason": "..."}
  ]
}
```

The `recent_history` (last 3 orchestrator results) gives the LLM trend context. An escalating pattern can raise the final severity; a recovering pattern can lower it.

**After producing its result, the orchestrator updates `baseline_memory`** — see §9.

### 6.5 Agent Interaction Flow

```
scheduler.execute_agent_pipeline()
    │
    ├── ErrorPatternAgent.analyze(rate_db, agent_db, app_info_id, per_ip)
    │       ├── query rate_limit_log (last 15 min)
    │       ├── build_error_summary(logs, per_ip)
    │       ├── LLM.complete_with_retry(system, user_msg)
    │       ├── parse response
    │       ├── INSERT agent_results
    │       └── return AgentResult
    │
    ├── TokenBucketHealthAgent.analyze(...)
    │       └── (same pattern, different metrics & prompt)
    │
    ├── TopPathsAgent.analyze(...)
    │       └── (same pattern, 60-min window)
    │
    └── Orchestrator.run(agent_db, app_info_id, error_result, token_result, paths_result, per_ip)
            ├── get_or_create_baseline(agent_db, app_info_id)
            ├── get_recent_context(agent_db, app_info_id, limit=3)
            ├── build summary dict
            ├── LLM.complete_with_retry(system, user_msg)
            ├── parse response
            ├── INSERT orchestrator_results
            ├── update_baseline(agent_db, app_info_id, results)
            └── return OrchestratorResult
```

---

## 7. LLM Provider Abstraction

The provider layer decouples agents from any specific LLM vendor. Swapping from Claude to GPT requires only changing one environment variable.

### 7.1 BaseLLMProvider

**File:** `rate_limiter_agents/providers/base.py`

```python
@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    cost_usd: float

class BaseLLMProvider(ABC):
    _max_retries: int = 3
    _base_delay: float = 2.0
    _call_timeout: float = 30.0   # per-call wall-clock limit

    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int) -> LLMResponse: ...

    def complete_with_retry(self, system, user, max_tokens) -> LLMResponse:
        # Runs complete() with exponential backoff + per-call threading timeout
```

### 7.2 AnthropicProvider

**File:** `rate_limiter_agents/providers/anthropic_provider.py`

- Uses `anthropic.Anthropic(api_key=...)`
- Default model: `claude-haiku-4-5-20251001` (configurable via `LLM_MODEL`)
- Pricing constants: `$1.00 / 1M input tokens`, `$5.00 / 1M output tokens`
- Calls `client.messages.create(model, max_tokens, system=..., messages=[{"role": "user", "content": ...}])`
- Extracts token counts from `response.usage.input_tokens` and `response.usage.output_tokens`

Why Haiku as the default? It is the fastest and cheapest Claude model. Each pipeline run calls the LLM 4 times (3 agents + orchestrator). At 96 runs/day (every 15 min), that is 384 LLM calls/day. Haiku keeps costs in the cents-per-day range.

### 7.3 OpenAIProvider

**File:** `rate_limiter_agents/providers/openai_provider.py`

- Uses `openai.OpenAI(api_key=...)`
- Default model: `gpt-4o-mini`
- Pricing: `$0.15 / 1M input`, `$0.60 / 1M output`
- Calls `client.chat.completions.create(model, max_tokens, messages=[system_msg, user_msg])`
- Extracts counts from `response.usage.prompt_tokens` / `completion_tokens`

### 7.4 Provider Factory

**File:** `rate_limiter_agents/providers/factory.py`

```python
def get_provider() -> BaseLLMProvider:
    if config.LLM_PROVIDER == LLMProviderType.ANTHROPIC:
        return AnthropicProvider(api_key=config.ANTHROPIC_API_KEY, model=config.LLM_MODEL)
    if config.LLM_PROVIDER == LLMProviderType.OPENAI:
        return OpenAIProvider(api_key=config.OPENAI_API_KEY, model=config.LLM_MODEL)
```

Each agent instantiates the provider fresh via `get_provider()` — this makes it easy to test with a mock provider.

### 7.5 Retry & Timeout Strategy

`complete_with_retry()` handles two failure modes:

**Transient API errors** (retried with exponential backoff):
- `RateLimitError` — provider is throttling us
- `APITimeoutError` — provider took too long
- `InternalServerError` — provider 5xx
- Network-level errors

Backoff formula: `delay = base_delay × 2^attempt + random_jitter`  
With `base_delay=2.0` and 3 retries: waits ~2s, ~4s, ~8s before giving up.

**Per-call wall-clock timeout** (enforced via threading):
- Each `complete()` call runs in a daemon thread
- If it doesn't return within `_call_timeout=30.0s`, raises `TimeoutError`
- Prevents a slow provider from blocking the scheduler for 15+ minutes

If all retries are exhausted, the agent catches the exception and returns an `AgentResult` with `severity="none"`, `action="monitor"`, `reason="agent_error: <exception message>"`. The pipeline continues without crashing.

---

## 8. Metrics Aggregation (Tools Layer)

**File:** `rate_limiter_agents/tools/metrics_aggregator.py`

This module converts raw `RateLimitLog` ORM objects into structured Python dicts that get JSON-serialized into LLM prompts. It is pure Python with no LLM dependency, making it fully unit-testable.

### 8.1 build_error_summary

**Inputs:** `list[RateLimitLog]`, `per_ip_address: bool`

**What it computes:**
- `response_2xx/4xx/429/5xx` — counts by status code bucket
- `blocked_requests` — count where `was_blocked=True`
- `block_rate_pct` = `blocked / total × 100`
- `error_rate_pct` = `(4xx + 5xx) / total × 100` (excludes 429 which is expected behavior)
- `top_error_paths` — top 5 paths by blocked count
- `top_block_reasons` — top 5 block reasons by count
- Per-IP additions: `unique_ips`, `top_blocking_ips` (top 5), `ip_concentration_pct`

**Edge case:** Returns zeroed-out dict when `logs` is empty.

### 8.2 build_token_health_summary

**Inputs:** `list[RateLimitLog]`, `per_ip_address: bool`

**What it computes:**
- Only uses logs where `remaining_tokens` is not null
- `avg_remaining_tokens` — mean via `numpy.mean()`
- `min_remaining_tokens`, `max_remaining_tokens`
- `threshold = max_remaining_tokens × 0.10` — 10% of capacity
- `near_depletion_count` — logs where `remaining_tokens <= threshold AND > 0`
- `depleted_count` — logs where `remaining_tokens == 0`
- `near_depletion_pct` = `near_depletion_count / total × 100`
- `top_token_consuming_paths` — top 5 paths sorted by `avg_remaining` ascending
- Per-IP: `unique_ips`, `ips_near_depletion`, `ips_depleted`

### 8.3 build_top_paths_summary

**Inputs:** `list[RateLimitLog]`, `per_ip_address: bool`

**What it computes:**
- Groups logs by `request_path`
- For each path: `total`, `blocked`, `block_rate_pct`, `top_method` (most common HTTP method)
- `top_paths_by_traffic` — top 10 by total requests
- `top_paths_by_block_rate` — top 10 by block rate, **minimum 3 requests** (filters noise)
- Per-IP: adds `top_blocking_ips` per path (top 3 IPs by block count on that path)

---

## 9. Memory Service & Baseline System

**File:** `rate_limiter_agents/tools/memory_service.py`

The baseline system gives the orchestrator a sense of "normal" for each app. Without it, the LLM would have no context for whether a 5% block rate is alarming or routine for that service.

**`get_or_create_baseline(agent_db, app_info_id) → BaselineMemory`**

- SELECTs `baseline_memory` WHERE `app_info_id = ?`
- If not found, creates a new row with:
  - `avg_rps_7d = 0.0`, `avg_block_rate_7d = 0.0`, `avg_bot_ratio_7d = 0.0`
  - `spike_threshold = 2.5`, `sample_count = 0`
- Returns the row (new or existing)

**`update_baseline(agent_db, app_info_id, error, token, paths, orchestrator)`**

Called after every orchestrator run. Uses a **capped running average** formula:

```python
# n = current sample_count, capped at 672 (7 days × 96 intervals/day)
n = min(current_sample_count, 672)
new_avg = (old_avg × n + new_value) / (n + 1)
new_count = min(n + 1, 672)
```

Values updated:
- `avg_rps_7d` — from `orchestrator.peak_rps` (if available)
- `avg_block_rate_7d` — from `error_agent.block_rate_pct`
- `avg_bot_ratio_7d` — from `error_agent.bot_ratio_pct`

**Dynamic spike threshold** (adjusted after each update):

```
avg_block_rate < 5%    → spike_threshold = 2.5×
avg_block_rate 5–20%   → spike_threshold = 3.0×
avg_block_rate > 20%   → spike_threshold = 4.0×
```

A service that normally blocks 25% of requests needs a higher threshold before an anomaly is declared. This prevents permanent false positives on services with legitimately high block rates.

---

## 10. API Layer

**File layout:** `rate_limiter_agents/routers/`

All routes are synchronous (not `async def`) because the underlying SQLAlchemy operations use the synchronous `psycopg2` driver. FastAPI runs them in a thread pool automatically.

### 10.1 Agents Router

**`POST /agents/run`**

- Optional query param: `app_info_id` — if provided, runs only for that app; otherwise all enabled apps
- Calls `execute_agent_pipeline()` for each applicable app
- Returns `list[RunResultOut]` — one entry per app with all four results
- Used for: on-demand pipeline runs (e.g., triggered from the dashboard)

**`GET /agents/history`**

- Query params: `app_info_id` (optional), `limit` (1–100, default 20), `offset` (default 0)
- Returns paginated `list[AgentResultOut]` ordered by `run_at DESC`
- Used for: inspecting recent per-agent decisions

### 10.2 Dashboard Router

**`GET /dashboard/apps`** — Lists all enabled apps (`id`, `service_name`, `description`).

**`GET /dashboard/summary`** — Aggregate metrics across all apps:

```json
{
  "app_info_ids": [1, 2],
  "total_agent_runs": 2880,
  "total_orchestrator_runs": 960,
  "anomaly_count": 24,
  "critical_count": 3,
  "high_count": 8,
  "total_tokens": 1152000,
  "total_cost_usd": 0.00576,
  "last_run_at": "2026-05-12T10:15:00Z",
  "last_severity": "low",
  "baselines": [...]
}
```

**`GET /dashboard/timeline`** — Paginated orchestrator runs with per-agent breakdowns. Supports filters:

- `filter`: `all | anomaly | critical | high | medium | low`
- `agent`: `all | error | token | paths | orchestrator`
- `limit`, `offset`

Each item includes the orchestrator result + joined specialist agent details for the same `run_at`.

**`GET /dashboard/baseline`** — Current 7-day rolling averages per app.

**`GET /dashboard/cost`** — Cost breakdown:

- `by_agent`: total tokens + cost per agent name (sum of all runs)
- `daily_series`: last 7 days, cost per agent per day

### 10.3 Evals Router

**`POST /evals/run`** — Executes all 5 labeled scenarios, stores results, returns accuracy report.

**`GET /evals/results`** — Recent eval results grouped by `run_id`. Includes per-scenario accuracy breakdown.

**`GET /evals/summary`** — Accuracy trend across all historical eval runs (for detecting model regression over time).

### 10.4 Health Endpoints

**`GET /health`** — Liveness check. Always returns `{"status": "ok"}` if the process is running.

**`GET /health/ready`** — Readiness check. Tests:
- Can connect to `AGENT_DB_URL`
- Can connect to `RATE_LIMITER_DB`

Returns `{"status": "ok"}` or `{"status": "degraded", "checks": {...}}`. Used by Docker `HEALTHCHECK` and CD rollback logic.

---

## 11. Scheduler & Background Jobs

**File:** `rate_limiter_agents/scheduler.py`

Uses APScheduler's `BackgroundScheduler` (runs in a daemon thread inside the FastAPI process — no separate worker needed).

**Job registration** (in `main.py` startup):

```python
scheduler = BackgroundScheduler()

# Anomaly detection pipeline
scheduler.add_job(
    run_all_agents,
    "interval",
    minutes=config.AGENT_INTERVAL_MINUTES,  # default: 15
    id="agent_pipeline"
)

# Daily eval regression check
scheduler.add_job(
    run_daily_evals,
    "cron",
    hour=0,
    minute=0,
    id="daily_evals"
)

scheduler.start()
```

**`run_all_agents()`:**

1. Opens DB sessions (rate_db + agent_db)
2. Queries `AppInfo` for all `enabled=True` apps
3. For each app: calls `execute_agent_pipeline()` in a try/except
4. Logs result or exception; continues with next app regardless
5. Closes sessions

**Why sequential per app, not parallel?** Each app makes 4 LLM calls. Running 10 apps in parallel would make 40 simultaneous calls, potentially hitting the LLM provider's rate limits. Sequential execution with retry logic is safer and still completes within the 15-minute interval for a reasonable number of apps.

**`execute_agent_pipeline(rate_db, agent_db, app_info_id, per_ip)`:**

```python
def execute_agent_pipeline(rate_db, agent_db, app_info_id, per_ip):
    error = ErrorPatternAgent().analyze(rate_db, agent_db, app_info_id, per_ip)
    token = TokenBucketHealthAgent().analyze(rate_db, agent_db, app_info_id, per_ip)
    paths = TopPathsAgent().analyze(rate_db, agent_db, app_info_id, per_ip)
    orch  = Orchestrator().run(agent_db, app_info_id, error, token, paths, per_ip)
    return error, token, paths, orch
```

**Startup/Shutdown** (`main.py`):

```python
@app.on_event("startup")
async def startup():
    run_alembic_migrations()  # Apply pending DB migrations
    scheduler.start()

@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown(wait=True)  # Let in-flight jobs finish
```

---

## 12. Eval System

**Purpose:** Validate that the LLM agents make correct decisions on known scenarios. Catches model regressions (e.g., after switching from Haiku to a different model) and prompt regressions (after editing system prompts).

### 12.1 Scenarios

**File:** `rate_limiter_agents/evals/scenarios.py`

Each scenario is a named dict with:
- `logs_factory` — a function that returns a synthetic `list[RateLimitLog]`
- `expected_per_agent` — a dict mapping agent names to `{severity, action}` ground truth

**The 5 scenarios:**

| Scenario | Description | Key Signal | Expected Orchestrator |
|---|---|---|---|
| `normal_traffic` | 200 requests, 2% block rate, mixed paths | No anomaly | `none / monitor` |
| `high_error_rate` | 100 req, 10% 5xx + 50% 4xx | 5xx present | `critical / block` |
| `high_block_rate` | 100 req, 35% blocked (no 5xx) | Block rate > 30% | `high / throttle` |
| `token_depletion` | 100 req, 80% near-depletion | `near_depletion_pct` = 80 | `critical / block` |
| `path_attack` | 200 req, `/api/attack` path has 83% block rate | Path-specific block | `critical / block` |

Synthetic logs are plain Python objects constructed without hitting any real database.

### 12.2 Runner

**File:** `rate_limiter_agents/evals/runner.py`

```
run_evals(agent_db) → EvalRunResult
    │
    ├── Generate run_id (UUID)
    │
    ├── For each scenario:
    │       ├── Create isolated in-memory SQLite DBs (rate_db, scenario_agent_db)
    │       ├── Build synthetic logs via logs_factory()
    │       ├── Run ErrorPatternAgent, TokenBucketHealthAgent, TopPathsAgent
    │       ├── Run Orchestrator
    │       ├── Compare actual vs expected severity + action
    │       └── Append EvalRunResult rows (one per agent per scenario)
    │
    ├── Bulk INSERT all EvalRunResult rows into AGENT_DB
    └── Compute and return accuracy metrics
```

**Key design:** Each scenario uses a **fresh isolated SQLite in-memory DB** for the agent side. This prevents baseline memory from accumulating across scenarios and corrupting later scenarios' results.

### 12.3 Accuracy Metrics

```python
total_checks = number_of_scenarios × number_of_agents  # e.g., 5 × 4 = 20
severity_accuracy_pct = (correct_severity_count / total_checks) × 100
action_accuracy_pct   = (correct_action_count   / total_checks) × 100
```

A 100% score means every agent made the exact expected decision on every scenario. In practice, LLMs occasionally make borderline calls (e.g., `high` instead of `critical` on the threshold), so 90%+ is considered healthy.

---

## 13. Configuration & Environment Variables

**File:** `rate_limiter_agents/config.py`

All configuration is loaded from environment variables at module import time. There are no defaults for secrets.

**Required:**

| Variable | Description |
|---|---|
| `RATE_LIMITER_DB` | Connection string for the rate limiter Postgres DB (read-only) |
| `AGENT_DB_URL` | Connection string for the agent Postgres DB (read-write) |

**LLM (one required based on provider):**

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `openai` |
| `LLM_MODEL` | `claude-haiku-4-5-20251001` | Model identifier |
| `ANTHROPIC_API_KEY` | — | Required if using Anthropic |
| `OPENAI_API_KEY` | — | Required if using OpenAI |

**Optional:**

| Variable | Default | Description |
|---|---|---|
| `AGENT_INTERVAL_MINUTES` | `15` | How often the pipeline runs |
| `CORS_ORIGINS` | `""` | Comma-separated allowed origins for CORS |

**In `.env.example`:**

```
RATE_LIMITER_DB=postgresql://user:pass@localhost:5432/rate_limiter
AGENT_DB_URL=postgresql://user:pass@localhost:5432/rate_limiter_agents
LLM_PROVIDER=anthropic
LLM_MODEL=claude-haiku-4-5-20251001
ANTHROPIC_API_KEY=sk-ant-...
AGENT_INTERVAL_MINUTES=15
CORS_ORIGINS=http://localhost:8000
```

---

## 14. Logging & Observability

**File:** `rate_limiter_agents/logging_config.py`

All logs are emitted as JSON to stdout. Each log line is a complete, machine-parseable JSON object.

**Log format:**

```json
{
  "timestamp": "2026-05-12T10:30:15.123456+00:00",
  "level": "INFO",
  "logger": "rate_limiter_agents.scheduler",
  "message": "app_info_id=1 (my-api) — severity=high action=throttle",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "exception": null
}
```

**Request ID propagation:**

- `main.py` middleware extracts `X-Request-ID` from incoming headers (or generates a UUID)
- Stored in a `contextvars.ContextVar`
- `_JsonFormatter` reads it and injects into every log line from that request's thread
- Returned in the `X-Request-ID` response header

This means you can grep a single request ID across all log lines to trace an entire pipeline execution.

**Log levels used:**
- `INFO` — normal pipeline completions, scheduler ticks
- `WARNING` — anomaly detected (high/critical)
- `ERROR` — agent exception, DB connection failure
- `DEBUG` — not emitted in production (level set to INFO)

---

## 15. Database Migrations (Alembic)

**Files:** `alembic.ini`, `alembic/env.py`, `alembic/versions/`

Alembic manages the schema of the `AGENT_DB_URL` only. The rate limiter DB schema is owned by another service.

**`alembic/env.py`** reads `AGENT_DB_URL` from environment and sets `sqlalchemy.url` dynamically. This means the same migration scripts work in development and production.

**Migration `0001_initial_agent_tables.py`** creates:

```
agent_results          + idx on (app_info_id), idx on (run_at)
orchestrator_results   + idx on (app_info_id), idx on (run_at)
baseline_memory        + unique idx on (app_info_id)
eval_run_results       + idx on (run_id), idx on (run_at)
```

**Applied automatically at startup:**

```python
# main.py startup handler
def run_alembic_migrations():
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
```

This means deploying a new version of the service automatically applies any pending migrations before the scheduler starts. No manual migration step needed.

**Common commands:**

```bash
alembic upgrade head              # Apply all pending migrations
alembic current                   # Show current revision
alembic downgrade -1              # Roll back one migration
alembic history                   # Show migration history
alembic revision --autogenerate -m "add column foo"  # Generate new migration from model diff
```

---

## 16. Docker & Containerization

**Dockerfile** uses a **multi-stage build** to produce a minimal runtime image.

**Stage 1 — Builder** (`python:3.12.13-slim-bookworm`):

```dockerfile
RUN apt-get install gcc libpq-dev   # Build tools for psycopg2
RUN python -m venv /opt/venv        # Isolated virtualenv
RUN pip install -r requirements.txt  # Build + cache wheels
```

**Stage 2 — Runtime** (`python:3.12.13-slim-bookworm`):

```dockerfile
RUN apt-get install libpq5 tini     # Runtime shared lib + PID 1 manager
RUN useradd --no-create-home appuser  # Non-root user
COPY --from=builder /opt/venv /opt/venv  # Only the venv, not build tools
COPY rate_limiter_agents/ /app/rate_limiter_agents/

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "rate_limiter_agents.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Why tini?** Docker containers that use `CMD` directly get PID 1, but Python is not designed to run as PID 1. It doesn't reap zombie child processes and may not handle SIGTERM correctly. `tini` handles both, enabling graceful shutdown.

**Why non-root user?** Defense in depth — if the app is compromised, the attacker has limited OS privileges.

**Why native Python healthcheck?** No dependency on `curl` or `wget` being present in the minimal image.

**Image sizes:** The multi-stage build drops the image from ~800MB (with build tools) to ~200MB (runtime only).

---

## 17. CI/CD Pipeline

### 17.1 Continuous Integration

**File:** `.github/workflows/ci.yml`  
**Triggers:** Push to `main`, any pull request

**Steps in order:**

1. **TruffleHog secret scan** — Detects verified secrets (API keys, tokens) in code. Fails the pipeline if found. Only scans verified secrets to minimize false positives.

2. **Python 3.12 setup** — Uses `pip` caching for fast dependency installs.

3. **mypy** — Type-checks `rate_limiter_agents/`. Catches interface mismatches between providers, agents, and schemas before runtime.

4. **ruff check** — Linting. Enforces code style, unused imports, etc.

5. **ruff format --check** — Verifies code is formatted (equivalent to running `ruff format` and checking nothing changed).

6. **pytest** — Full test suite with JUnit XML output. Exit code 5 (no tests found) is treated as success (handles the case where tests haven't been written yet for a new module).

7. **Test artifact upload** — JUnit XML uploaded to GitHub for the "Tests" tab in Actions UI.

8. **Trivy filesystem scan** — Scans source code and dependencies for known CVEs at `CRITICAL` and `HIGH` severity. Fails on findings.

9. **Docker build (no push)** — Builds the image to validate the Dockerfile. Uses GitHub Actions cache for faster layer rebuilds.

10. **Trivy image scan** — Scans the built Docker image for CVEs in OS packages and Python packages.

### 17.2 Continuous Deployment

**File:** `.github/workflows/cd.yml`  
**Triggers:** After CI passes on `main`

**Build & Push job:**

1. Multi-arch Docker build: `linux/amd64`
2. Pushes two tags: `:latest` and `:<git-sha>`
3. Generates SBOM (Software Bill of Materials) and provenance attestations for supply chain security
4. Uses a separate cache scope (`cd`) from CI to avoid cache conflicts

**Deploy job:**

```bash
# On production server via SSH:
docker pull image:SHA
docker stop old_container || true
docker run -d \
  --name new_container \
  --network DOCKER_NETWORK \
  -e RATE_LIMITER_DB=... \
  -e AGENT_DB_URL=... \
  -e ... \
  image:SHA

# Poll Docker healthcheck for 90 seconds
for i in $(seq 1 18); do
  status=$(docker inspect --format='{{.State.Health.Status}}' new_container)
  if [ "$status" = "healthy" ]; then exit 0; fi
  sleep 5
done

# Auto-rollback if not healthy:
docker stop new_container
docker start old_container
exit 1
```

**Rollback strategy:** The old container is stopped (not removed) before starting the new one. If the new container fails the healthcheck within 90s, the pipeline re-starts the old container and exits with failure. The old image is already pulled locally, so rollback is instant.

---

## 18. Testing Strategy

### 18.1 Test Fixtures & In-Memory DB

**File:** `tests/conftest.py`

The test suite avoids real Postgres by using SQLite in-memory databases. This makes tests fast, hermetic, and CI-friendly (no external services needed).

**Key tricks for SQLite compatibility:**

```python
# Set env vars BEFORE any app imports so config.py reads SQLite URLs
os.environ["RATE_LIMITER_DB"] = "sqlite://"
os.environ["AGENT_DB_URL"] = "sqlite://"

# StaticPool: all connections share the same in-memory DB
engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

# BIGINT → INTEGER patch: SQLite doesn't support BIGINT autoincrement
# The fixture patches this on the Column definitions before create_all()
```

**Session-scoped fixtures** create engines and schemas once per test session (fast).  
**Function-scoped session fixtures** yield a session and roll back after each test (isolation).

```python
@pytest.fixture(scope="session")
def agent_engine():
    engine = create_engine("sqlite://", ...)
    AgentBase.metadata.create_all(engine)
    yield engine

@pytest.fixture
def agent_db(agent_engine):
    session = Session(agent_engine)
    yield session
    session.rollback()
    session.close()
```

**TestClient fixture:**

```python
@pytest.fixture
def test_client(agent_engine, rate_engine):
    # Override FastAPI dependency injection to use test engines
    app.dependency_overrides[get_agent_db] = lambda: Session(agent_engine)
    app.dependency_overrides[get_rate_db]  = lambda: Session(rate_engine)
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
    app.dependency_overrides.clear()
```

### 18.2 Unit Tests

**`test_metrics_aggregator.py`** — Tests the pure-Python aggregation functions:

- Empty log list → zeroed-out dict with correct keys
- `block_rate_pct` calculation: 4 blocked / 10 total = 40.0
- `error_rate_pct` excludes 429 (expected behavior), includes 5xx
- `near_depletion_pct` threshold = 10% of max observed tokens
- `top_paths_by_block_rate` minimum 3 requests filter
- Per-IP mode adds IP fields to output

**`test_memory_service.py`** — Tests baseline upsert and running average math.

**`test_agent_helpers.py`** — Tests response parsing logic for each agent.

**`test_routers.py`** — Integration tests hitting the FastAPI routes via TestClient.

**Running tests:**

```bash
# Install dev deps
pip install -r requirements-dev.txt

# Run all tests
pytest

# Verbose with short tracebacks
pytest -v --tb=short

# Single file
pytest tests/test_metrics_aggregator.py

# Generate JUnit XML (same as CI)
pytest --junitxml=test-results.xml
```

---

## 19. Key Design Decisions & Trade-offs

**1. Synchronous SQLAlchemy over async**

The routes use sync `Session` with `psycopg2`. This is simpler to reason about and test (no `await` sprinkled everywhere, no async fixtures). FastAPI automatically runs sync routes in a thread pool. The trade-off: slightly higher thread usage under load vs. fully async. For an internal monitoring service with low concurrency, this is the right call.

**2. Sequential agents, not parallel**

Three agents run one after another. Parallel execution would require thread pool management and would hit the LLM provider's concurrent request limits. The extra ~2s of latency per run is irrelevant for a 15-minute interval pipeline.

**3. LLM for structured output via plain text parsing**

Agents parse LLM responses as `KEY: value` lines rather than enforcing JSON output. This is more resilient — LLMs sometimes wrap JSON in markdown code blocks, causing parse failures. Key-value parsing handles minor formatting variations gracefully.

**4. Two separate databases**

The rate limiter DB is read-only from this service's perspective. Keeping them separate enforces this at the connection level and prevents accidental writes. It also allows the rate limiter and the agent service to have independent deployment and backup cycles.

**5. APScheduler inside the FastAPI process**

No separate Celery/Redis setup. The scheduler is a background thread in the same process. This is simpler to deploy (single container, single process) at the cost of: if the web process crashes, the scheduler also stops. Acceptable for a monitoring service where brief gaps are tolerable.

**6. Baseline stored per-app, not computed on the fly**

Computing a 7-day average on every pipeline run would require a `GROUP BY` query over potentially millions of rows. The rolling average approach updates incrementally and keeps each read O(1).

**7. Eval isolation via in-memory SQLite per scenario**

Eval scenarios must not share state. If scenario 2 leaves a high block rate in baseline memory, scenario 3's orchestrator would receive a distorted baseline and potentially give wrong answers. Fresh in-memory DBs per scenario guarantee isolation.

---

## 20. Per-IP vs. Shared Rate Limiting Mode

Configured per-app via `AppInfo.per_ip_address`.

**Shared mode:** One token bucket shared by all clients. A high block rate means the service is under load from many sources. Thresholds apply at face value.

**Per-IP mode:** Each client IP has its own token bucket. A high block rate from a single IP is expected behavior (one bad actor being blocked). This should NOT trigger the same severity as many IPs being blocked.

**How agents handle this:**

- `ErrorPatternAgent`: If `ip_concentration_pct` is high (>70%), the severity is lowered by one level. Single-IP attacks are expected to be caught by per-IP mode.
- `TokenBucketHealthAgent`: Severity is only escalated if MANY unique IPs are depleted simultaneously. One depleted IP = expected; many = coordinated attack.
- `TopPathsAgent`: Per-path block concentration is checked. If blocks on a path come from 1–2 IPs, it's likely a targeted attempt on that path, which per-IP mode handles.
- `Orchestrator`: Receives `"rate_limiting_mode": "per-IP"` in its input and can factor this into its synthesis.

---

## 21. Cost Tracking

Every agent call records:

```python
tokens_used: int   # input_tokens + output_tokens
cost_usd: float    # computed from provider-specific pricing
```

**Anthropic Haiku pricing** (baked into `anthropic_provider.py`):

```
$1.00 per 1M input tokens
$5.00 per 1M output tokens
```

**OpenAI GPT-4o-mini pricing:**

```
$0.15 per 1M input tokens
$0.60 per 1M output tokens
```

**Example daily cost estimate (Anthropic Haiku):**

- 96 pipeline runs/day (every 15 min) × 4 LLM calls/run = 384 calls/day
- Average ~300 input tokens + 100 output tokens per call
- Daily tokens: 384 × 400 = 153,600 tokens
- Daily cost: ~$0.001 (less than a tenth of a cent)

The dashboard's `/dashboard/cost` endpoint aggregates this by agent and by day, making cost anomalies (e.g., if a bug causes runaway retries) visible.

---

## 22. Security Considerations

**Secret handling:**
- All secrets are environment variables; never hardcoded
- TruffleHog CI step blocks commits containing verified secrets
- `.env` is in `.gitignore`

**Container security:**
- Runs as non-root `appuser`
- Multi-stage build removes build tools from the runtime image
- Trivy scans both filesystem and image for CVEs in CI

**Database access:**
- The service only needs INSERT/SELECT on agent tables; it never modifies the rate limiter DB
- Connection strings are injected at runtime via env vars, not embedded in code

**CORS:**
- Configured via `CORS_ORIGINS` env var — restricted to specific origins in production
- Default is empty (no CORS headers) if not configured

**Input validation:**
- All API inputs validated by Pydantic schemas
- Query param ranges enforced (e.g., `limit: int = Query(20, ge=1, le=100)`)

**SQL injection:**
- All queries use SQLAlchemy ORM with parameterized statements — no raw string interpolation

---

## 23. Local Development Setup

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd rate-limit-agent

# 2. Create a virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 4. Copy and fill in environment variables
cp .env.example .env
# Edit .env with your DB connection strings and API key

# 5. Apply database migrations
alembic upgrade head

# 6. Start the service
cd rate_limiter_agents
uvicorn main:app --reload --port 8000

# 7. Open the dashboard
open http://localhost:8000/static/index.html

# 8. Manually trigger a pipeline run
curl -X POST http://localhost:8000/agents/run

# 9. Run an eval
curl -X POST http://localhost:8000/evals/run

# 10. Run tests
pytest -v
```

**Linting and formatting:**

```bash
ruff check rate_limiter_agents/    # Lint
ruff format rate_limiter_agents/   # Format
mypy rate_limiter_agents/          # Type check
```

**Without a real rate limiter DB** (for testing the agent side only):  
The eval system creates its own synthetic data, so `POST /evals/run` works without a real `RATE_LIMITER_DB` connection. Only the live pipeline (`POST /agents/run` and the scheduler) requires it.
