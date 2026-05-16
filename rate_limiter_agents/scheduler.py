from __future__ import annotations

import logging

from .agents.error_pattern import ErrorPatternAgent
from .agents.orchestrator import Orchestrator
from .agents.token_bucket_health import TokenBucketHealthAgent
from .agents.top_paths import TopPathsAgent
from .database import AgentScopedSession
from .evals.runner import run_evals
from .models import AgentResult, OrchestratorResult
from .tools.data_source import get_data_source

logger = logging.getLogger(__name__)


def execute_agent_pipeline(
    agent_db, app_info_id: int
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
    orch = Orchestrator().run(agent_db, app_info_id, error, token, paths, per_ip)
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
