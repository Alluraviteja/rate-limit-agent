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
    UniqueConstraint,
)

from .database import AgentBase


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
    spike_multiplier = Column(Numeric(10, 2), nullable=True)
    trend_direction = Column(String(15), nullable=True)  # escalating / recovering / stable
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


class TimeBaseline(AgentBase):
    """Per-app, per-hour-of-day, per-day-of-week EWMA baselines.

    168 buckets per app (24h × 7 days). Each bucket tracks the rolling average
    block rate for that time slot using EWMA so anomaly detection can compare
    current metrics against what is normal for this specific time, not an
    all-time average.
    """

    __tablename__ = "time_baselines"
    __table_args__ = (
        UniqueConstraint("app_info_id", "hour_of_day", "day_of_week", name="uq_time_baseline"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    app_info_id = Column(BigInteger, index=True)
    hour_of_day = Column(Integer)   # 0–23
    day_of_week = Column(Integer)   # 0=Monday … 6=Sunday
    avg_block_rate_ewma = Column(Numeric(10, 4), default=5.0)
    avg_rps_ewma = Column(Numeric(10, 4), default=100.0)
    sample_count = Column(Integer, default=0)
    last_updated = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class ActionOutcome(AgentBase):
    """Records what happened after each orchestrator decision.

    Populated immediately after every pipeline run (severity_after=NULL).
    On the next run for the same app, the previous row is filled in with
    severity_after and anomaly_resolved so we can measure whether the
    recommended action actually resolved the anomaly.
    """

    __tablename__ = "action_outcomes"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    orchestrator_result_id = Column(BigInteger, index=True)
    app_info_id = Column(BigInteger, index=True)
    action_taken = Column(String(20))
    severity_at_action = Column(String(20))
    severity_after = Column(String(20), nullable=True)
    anomaly_resolved = Column(Boolean, nullable=True)
    measured_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
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
