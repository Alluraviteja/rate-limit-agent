from __future__ import annotations

import logging

# ── New agents ───────────────────────────────────────────────────────────────
from .agents.error_pattern import ErrorPatternAgent
from .agents.orchestrator import Orchestrator
from .agents.token_bucket_health import TokenBucketHealthAgent
from .agents.top_paths import TopPathsAgent

from .database import AgentScopedSession, RateLimiterScopedSession
from .evals.runner import run_evals
from .models import AgentResult, AppInfo, OrchestratorResult

logger = logging.getLogger(__name__)


def execute_agent_pipeline(
    rate_db, agent_db, app_info_id: int
) -> tuple[AgentResult, AgentResult, AgentResult, OrchestratorResult]:
    app_info = rate_db.query(AppInfo).filter(AppInfo.id == app_info_id).first()
    per_ip   = bool(app_info.per_ip_address) if app_info and app_info.per_ip_address is not None else False

    error  = ErrorPatternAgent().analyze(rate_db, agent_db, app_info_id, per_ip)
    token  = TokenBucketHealthAgent().analyze(rate_db, agent_db, app_info_id, per_ip)
    paths  = TopPathsAgent().analyze(rate_db, agent_db, app_info_id, per_ip)
    orch   = Orchestrator().run(agent_db, app_info_id, error, token, paths, per_ip)
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
    """Fetches all enabled apps from app_info on every run."""
    rate_db  = RateLimiterScopedSession()
    agent_db = AgentScopedSession()
    try:
        app_infos = rate_db.query(AppInfo).filter(AppInfo.enabled.is_(True)).all()
        if not app_infos:
            logger.warning("No enabled apps found in app_info — skipping run")
            return

        for app_info in app_infos:
            try:
                _, _, _, orch = execute_agent_pipeline(rate_db, agent_db, app_info.id)
                logger.info(
                    "app_info_id=%s (%s) — severity=%s action=%s",
                    app_info.id,
                    app_info.service_name,
                    orch.final_severity,
                    orch.action,
                )
            except Exception as e:
                logger.error("Pipeline failed for app_info_id=%s: %s", app_info.id, e)
    except Exception as e:
        logger.error("Scheduler run failed: %s", e)
    finally:
        RateLimiterScopedSession.remove()
        AgentScopedSession.remove()
