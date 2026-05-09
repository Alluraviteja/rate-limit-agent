from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
)

from .database import AgentBase, RateLimiterBase


# ── Read-only models (rate_limiter DB) ──────────────────────────────────────
class AppInfo(RateLimiterBase):
    __tablename__ = "app_info"
    __table_args__ = {"extend_existing": True}

    id = Column(BigInteger, primary_key=True)
    service_name = Column(String(255))
    service_port = Column(Integer)
    description = Column(String(1000))
    enabled = Column(Boolean)
    per_ip_address = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class RateLimitPlan(RateLimiterBase):
    __tablename__ = "rate_limit_plan"
    __table_args__ = {"extend_existing": True}

    id = Column(BigInteger, primary_key=True)
    app_info_id = Column(BigInteger)
    path_pattern = Column(String(500))
    capacity = Column(Integer)
    refill_rate = Column(Integer)
    refill_period_seconds = Column(Integer)
    description = Column(String)
    enabled = Column(Boolean)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


class RateLimitLog(RateLimiterBase):
    __tablename__ = "rate_limit_log"
    __table_args__ = {"extend_existing": True}

    id = Column(BigInteger, primary_key=True)
    app_info_id = Column(BigInteger)
    client_ip = Column(String(45))
    was_blocked = Column(Boolean)
    reason = Column(String)
    http_method = Column(String(10))
    request_path = Column(String(500))
    remaining_tokens = Column(BigInteger)
    trace_id = Column(String(64))
    response_code = Column(Integer)
    request_at = Column(DateTime(timezone=True))
    retry_after_seconds = Column(BigInteger)
    redis_failed = Column(Boolean)
    is_bot = Column(Boolean)
    bot_name = Column(String(100))
    user_agent = Column(Text)
    browser = Column(String(50))
    browser_version = Column(String(20))
    os = Column(String(50))
    os_version = Column(String(20))
    device_type = Column(String(10))
    request_size = Column(BigInteger)
    referer = Column(String(2048))
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))


# ── Agent models (agent DB) ─────────────────────────────────────────────────
class AgentResult(AgentBase):
    __tablename__ = "agent_results"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    app_info_id = Column(BigInteger, index=True)
    agent_name = Column(String(50))
    anomaly_detected = Column(Boolean)
    severity = Column(String(20))
    spike_ratio = Column(Numeric(10, 4), nullable=True)
    block_rate_pct = Column(Numeric(10, 4), nullable=True)
    slope_pct = Column(Numeric(10, 4), nullable=True)
    bot_ratio_pct = Column(Numeric(10, 4), nullable=True)
    peak_rps = Column(Numeric(10, 4), nullable=True)
    baseline_rps = Column(Numeric(10, 4), nullable=True)
    total_requests = Column(Integer, nullable=True)
    blocked_requests = Column(Integer, nullable=True)
    unique_ips = Column(Integer, nullable=True)
    redis_failures = Column(Integer, nullable=True)
    reason = Column(Text)
    action = Column(String(20))
    tokens_used = Column(Integer)
    cost_usd = Column(Numeric(12, 8))
    run_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


class OrchestratorResult(AgentBase):
    __tablename__ = "orchestrator_results"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    app_info_id = Column(BigInteger, index=True)
    error_severity = Column(String(20))
    token_severity = Column(String(20))
    path_severity = Column(String(20))
    final_severity = Column(String(20))
    anomaly_detected = Column(Boolean)
    reason = Column(Text)
    action = Column(String(20))
    tokens_used = Column(Integer)
    cost_usd = Column(Numeric(12, 8))
    run_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


class BaselineMemory(AgentBase):
    __tablename__ = "baseline_memory"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    app_info_id = Column(BigInteger, unique=True)
    avg_rps_7d = Column(Numeric(10, 4))
    avg_block_rate_7d = Column(Numeric(10, 4))
    avg_bot_ratio_7d = Column(Numeric(10, 4))
    spike_threshold = Column(Numeric(10, 4))
    sample_count = Column(Integer)
    last_updated = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class EvalRunResult(AgentBase):
    __tablename__ = "eval_run_results"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(String(36), index=True)
    scenario_name = Column(String(100))
    agent_name = Column(String(50))
    expected_severity = Column(String(20))
    actual_severity = Column(String(20))
    expected_action = Column(String(20))
    actual_action = Column(String(20))
    severity_correct = Column(Boolean)
    action_correct = Column(Boolean)
    tokens_used = Column(Integer, nullable=True)
    cost_usd = Column(Numeric(12, 8), nullable=True)
    run_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
