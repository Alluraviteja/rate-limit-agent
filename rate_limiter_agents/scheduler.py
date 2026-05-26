from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .agents.error_pattern import ErrorPatternAgent
from .agents.orchestrator import Orchestrator, _LEVELS
from .agents.token_bucket_health import TokenBucketHealthAgent
from .agents.top_paths import TopPathsAgent
from .database import AgentScopedSession
from .evals.runner import run_evals
from .models import ActionOutcome, AgentResult, OrchestratorResult
from .tools.data_source import get_data_source
from .tools.enforcement import enforce_action

logger = logging.getLogger(__name__)


# ── Outcome tracking ──────────────────────────────────────────────────────────


def _measure_previous_outcome(
    db: Session, app_info_id: int, current_severity: str
) -> None:
    """Fill in severity_after on the most recent unmeasured ActionOutcome row.

    Called at the start of each pipeline run so the outcome reflects what
    actually happened on the *next* observation after the action was taken.
    """
    prev = (
        db.query(ActionOutcome)
        .filter(
            ActionOutcome.app_info_id == app_info_id,
            ActionOutcome.measured_at.is_(None),
        )
        .order_by(ActionOutcome.created_at.desc())
        .first()
    )
    if prev is None:
        return

    prev.severity_after = current_severity
    prev.anomaly_resolved = (
        _LEVELS.get(current_severity, 0) < _LEVELS.get(prev.severity_at_action or "none", 0)
    )
    prev.measured_at = datetime.now(timezone.utc)
    db.commit()


def _record_outcome(db: Session, orch_result: OrchestratorResult) -> None:
    """Create a new ActionOutcome row for this run (severity_after filled in next run)."""
    outcome = ActionOutcome(
        orchestrator_result_id=orch_result.id,
        app_info_id=orch_result.app_info_id,
        action_taken=orch_result.action,
        severity_at_action=orch_result.final_severity,
    )
    db.add(outcome)
    db.commit()


# ── Pipeline ──────────────────────────────────────────────────────────────────


def execute_agent_pipeline(
    agent_db: Session, app_info_id: int
) -> tuple[AgentResult, AgentResult, AgentResult, OrchestratorResult]:
    ds = get_data_source()

    app = ds.get_app(app_info_id)
    per_ip = bool(app.get("per_ip_address", False))

    error = ErrorPatternAgent().analyze(
        agent_db,
        app_info_id,
        per_ip,
        ds.get_error_summary(app_info_id, 15, per_ip=per_ip),
    )
    token = TokenBucketHealthAgent().analyze(
        agent_db,
        app_info_id,
        per_ip,
        ds.get_token_health_summary(app_info_id, 15, per_ip=per_ip),
    )
    paths = TopPathsAgent().analyze(
        agent_db,
        app_info_id,
        per_ip,
        ds.get_top_paths_summary(app_info_id, 60, per_ip=per_ip),
    )

    # Measure how the previous run's action played out before recording this run.
    _measure_previous_outcome(agent_db, app_info_id, error.severity or "none")

    orch = Orchestrator().run(agent_db, app_info_id, error, token, paths, per_ip)

    _record_outcome(agent_db, orch)
    enforce_action(app_info_id, orch.action, orch.final_severity, orch.reason or "")

    return error, token, paths, orch


def run_daily_evals() -> None:
    agent_db = AgentScopedSession()
    try:
        summary = run_evals(agent_db)
        logger.info(
            "Daily evals complete — severity_accuracy=%.1f%% action_accuracy=%.1f%% cost=$%.6f",
            summary["severity_accuracy_pct"],
            summary["action_accuracy_pct"],
            summary["total_cost_usd"],
        )
    except Exception as e:
        logger.error("Daily evals failed: %s", e)
    finally:
        AgentScopedSession.remove()


def run_all_agents() -> None:
    agent_db = AgentScopedSession()
    try:
        ds = get_data_source()
        apps = ds.list_apps()
        if not apps:
            logger.warning("No enabled apps found — skipping run")
            return

        for app in apps:
            app_id = int(app["id"])
            try:
                _, _, _, orch = execute_agent_pipeline(agent_db, app_id)
                logger.info(
                    "app_info_id=%s (%s) — severity=%s action=%s",
                    app_id,
                    app.get("service_name"),
                    orch.final_severity,
                    orch.action,
                )
            except Exception as e:
                logger.error("Pipeline failed for app_info_id=%s: %s", app_id, e)
    except Exception as e:
        logger.error("Scheduler run failed: %s", e)
    finally:
        AgentScopedSession.remove()
