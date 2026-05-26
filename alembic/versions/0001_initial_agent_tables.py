"""initial agent tables

Revision ID: 0001
Revises:
Create Date: 2026-05-02

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop all tables so a re-run always starts from a clean slate.
    op.execute("DROP TABLE IF EXISTS eval_run_results")
    op.execute("DROP TABLE IF EXISTS action_outcomes")
    op.execute("DROP TABLE IF EXISTS time_baselines")
    op.execute("DROP TABLE IF EXISTS baseline_memory")
    op.execute("DROP TABLE IF EXISTS orchestrator_results")
    op.execute("DROP TABLE IF EXISTS agent_results")

    op.create_table(
        "agent_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("app_info_id", sa.BigInteger(), nullable=True),
        sa.Column("agent_name", sa.String(50), nullable=True),
        sa.Column("anomaly_detected", sa.Boolean(), nullable=True),
        sa.Column("severity", sa.String(20), nullable=True),
        sa.Column("spike_ratio", sa.Numeric(10, 4), nullable=True),
        sa.Column("block_rate_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("slope_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("bot_ratio_pct", sa.Numeric(10, 4), nullable=True),
        sa.Column("peak_rps", sa.Numeric(10, 4), nullable=True),
        sa.Column("baseline_rps", sa.Numeric(10, 4), nullable=True),
        sa.Column("total_requests", sa.Integer(), nullable=True),
        sa.Column("blocked_requests", sa.Integer(), nullable=True),
        sa.Column("unique_ips", sa.Integer(), nullable=True),
        sa.Column("redis_failures", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("action", sa.String(20), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 8), nullable=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_results_app_info_id", "agent_results", ["app_info_id"])
    op.create_index("ix_agent_results_run_at", "agent_results", ["run_at"])

    op.create_table(
        "orchestrator_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("app_info_id", sa.BigInteger(), nullable=True),
        sa.Column("error_severity", sa.String(20), nullable=True),
        sa.Column("token_severity", sa.String(20), nullable=True),
        sa.Column("path_severity", sa.String(20), nullable=True),
        sa.Column("final_severity", sa.String(20), nullable=True),
        sa.Column("anomaly_detected", sa.Boolean(), nullable=True),
        sa.Column("spike_multiplier", sa.Numeric(10, 2), nullable=True),
        sa.Column("trend_direction", sa.String(15), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("action", sa.String(20), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 8), nullable=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_orchestrator_results_app_info_id", "orchestrator_results", ["app_info_id"]
    )
    op.create_index(
        "ix_orchestrator_results_run_at", "orchestrator_results", ["run_at"]
    )

    op.create_table(
        "baseline_memory",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("app_info_id", sa.BigInteger(), nullable=True),
        sa.Column("avg_rps_7d", sa.Numeric(10, 4), nullable=True),
        sa.Column("avg_block_rate_7d", sa.Numeric(10, 4), nullable=True),
        sa.Column("avg_bot_ratio_7d", sa.Numeric(10, 4), nullable=True),
        sa.Column("spike_threshold", sa.Numeric(10, 4), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("app_info_id"),
    )

    op.create_table(
        "time_baselines",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("app_info_id", sa.BigInteger(), nullable=True),
        sa.Column("hour_of_day", sa.Integer(), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("avg_block_rate_ewma", sa.Numeric(10, 4), nullable=True),
        sa.Column("avg_rps_ewma", sa.Numeric(10, 4), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "app_info_id", "hour_of_day", "day_of_week", name="uq_time_baseline"
        ),
    )
    op.create_index("ix_time_baselines_app_info_id", "time_baselines", ["app_info_id"])

    op.create_table(
        "action_outcomes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("orchestrator_result_id", sa.BigInteger(), nullable=True),
        sa.Column("app_info_id", sa.BigInteger(), nullable=True),
        sa.Column("action_taken", sa.String(20), nullable=True),
        sa.Column("severity_at_action", sa.String(20), nullable=True),
        sa.Column("severity_after", sa.String(20), nullable=True),
        sa.Column("anomaly_resolved", sa.Boolean(), nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_action_outcomes_orchestrator_result_id",
        "action_outcomes",
        ["orchestrator_result_id"],
    )
    op.create_index("ix_action_outcomes_app_info_id", "action_outcomes", ["app_info_id"])
    op.create_index("ix_action_outcomes_created_at", "action_outcomes", ["created_at"])

    op.create_table(
        "eval_run_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("scenario_name", sa.String(100), nullable=True),
        sa.Column("agent_name", sa.String(50), nullable=True),
        sa.Column("expected_severity", sa.String(20), nullable=True),
        sa.Column("actual_severity", sa.String(20), nullable=True),
        sa.Column("expected_action", sa.String(20), nullable=True),
        sa.Column("actual_action", sa.String(20), nullable=True),
        sa.Column("severity_correct", sa.Boolean(), nullable=True),
        sa.Column("action_correct", sa.Boolean(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 8), nullable=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eval_run_results_run_id", "eval_run_results", ["run_id"])
    op.create_index("ix_eval_run_results_run_at", "eval_run_results", ["run_at"])


def downgrade() -> None:
    op.drop_table("eval_run_results")
    op.drop_table("action_outcomes")
    op.drop_table("time_baselines")
    op.drop_table("baseline_memory")
    op.drop_table("orchestrator_results")
    op.drop_table("agent_results")
