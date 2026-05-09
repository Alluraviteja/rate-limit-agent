from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class TimelineFilter(str, Enum):
    all = "all"
    anomaly = "anomaly"
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class AgentFilter(str, Enum):
    all = "all"
    error = "error"
    token = "token"
    paths = "paths"
    orchestrator = "orchestrator"


# ── Agents router ─────────────────────────────────────────────────────────────

class AgentResultOut(BaseModel):
    id: int
    app_info_id: Optional[int] = None
    agent_name: Optional[str] = None
    anomaly_detected: Optional[bool] = None
    severity: Optional[str] = None
    spike_ratio: Optional[float] = None
    block_rate_pct: Optional[float] = None
    slope_pct: Optional[float] = None
    bot_ratio_pct: Optional[float] = None
    peak_rps: Optional[float] = None
    baseline_rps: Optional[float] = None
    avg_remaining_tokens: Optional[float] = None
    total_requests: Optional[int] = None
    blocked_requests: Optional[int] = None
    unique_ips: Optional[int] = None
    unique_paths: Optional[int] = None
    redis_failures: Optional[int] = None
    reason: Optional[str] = None
    action: Optional[str] = None
    tokens_used: Optional[int] = None
    cost_usd: Optional[float] = None
    run_at: Optional[datetime] = None


class OrchestratorResultOut(BaseModel):
    id: int
    app_info_id: Optional[int] = None
    error_severity: Optional[str] = None
    token_severity: Optional[str] = None
    path_severity: Optional[str] = None
    final_severity: Optional[str] = None
    anomaly_detected: Optional[bool] = None
    reason: Optional[str] = None
    action: Optional[str] = None
    tokens_used: Optional[int] = None
    cost_usd: Optional[float] = None
    run_at: Optional[datetime] = None


class RunResultOut(BaseModel):
    app_info_id: int
    error: AgentResultOut
    token: AgentResultOut
    paths: AgentResultOut
    orchestrator: OrchestratorResultOut


# ── Dashboard router ──────────────────────────────────────────────────────────

class AppOut(BaseModel):
    id: int
    service_name: Optional[str] = None
    description: Optional[str] = None


class BaselineOut(BaseModel):
    id: int
    app_info_id: Optional[int] = None
    avg_rps_7d: Optional[float] = None
    avg_block_rate_7d: Optional[float] = None
    avg_bot_ratio_7d: Optional[float] = None
    spike_threshold: Optional[float] = None
    sample_count: Optional[int] = None
    last_updated: Optional[datetime] = None


class SummaryOut(BaseModel):
    app_info_ids: list[int]
    total_agent_runs: int
    total_orchestrator_runs: int
    anomaly_count: int
    critical_count: int
    high_count: int
    total_tokens: int
    total_cost_usd: float
    last_run_at: Optional[str] = None
    last_severity: str
    baselines: list[BaselineOut]


class TimelineErrorDetail(BaseModel):
    severity: str
    block_rate_pct: float
    total_requests: int
    blocked_requests: int
    reason: str
    action: str = "monitor"
    tokens_used: Optional[int] = None


class TimelineTokenDetail(BaseModel):
    severity: str
    avg_remaining: float
    total_requests: int
    reason: str
    action: str = "monitor"
    tokens_used: Optional[int] = None


class TimelinePathsDetail(BaseModel):
    severity: str
    block_rate_pct: float
    unique_paths: int
    total_requests: int
    reason: str
    action: str = "monitor"
    tokens_used: Optional[int] = None


class TimelineItemOut(BaseModel):
    app_info_id: int
    run_at: str
    final_severity: Optional[str] = None
    anomaly_detected: Optional[bool] = None
    action: Optional[str] = None
    reason: Optional[str] = None
    tokens_used: Optional[int] = None
    cost_usd: float
    error: TimelineErrorDetail
    token: TimelineTokenDetail
    paths: TimelinePathsDetail


class CostByAgentOut(BaseModel):
    agent_name: str
    runs_today: int
    tokens_today: int
    cost_today: float


class DailyCostOut(BaseModel):
    date: str
    error_pattern: float
    token_bucket_health: float
    top_paths: float
    orchestrator: float


class CostOut(BaseModel):
    by_agent: list[CostByAgentOut]
    daily_series: list[DailyCostOut]


# ── Evals router ──────────────────────────────────────────────────────────────

class EvalCheckOut(BaseModel):
    scenario: str
    agent: str
    expected_severity: Optional[str] = None
    actual_severity: Optional[str] = None
    expected_action: Optional[str] = None
    actual_action: Optional[str] = None
    severity_correct: Optional[bool] = None
    action_correct: Optional[bool] = None
    tokens_used: Optional[int] = None
    cost_usd: float


class ScenarioAccuracyOut(BaseModel):
    severity_accuracy_pct: float
    action_accuracy_pct: float


class EvalRunOut(BaseModel):
    run_id: str
    run_at: Optional[str] = None
    total_checks: int
    severity_accuracy_pct: float
    action_accuracy_pct: float
    total_tokens_used: int
    total_cost_usd: float
    by_scenario: dict[str, ScenarioAccuracyOut]
    checks: list[EvalCheckOut]


class EvalTrendItemOut(BaseModel):
    run_id: str
    run_at: Optional[str] = None
    total_checks: int
    severity_accuracy_pct: float
    action_accuracy_pct: float
    total_cost_usd: float


class EvalRunTriggerOut(BaseModel):
    run_id: str
    scenarios_run: int
    total_checks: int
    severity_accuracy_pct: float
    action_accuracy_pct: float
    total_tokens_used: int
    total_cost_usd: float
    results: list[EvalCheckOut]
