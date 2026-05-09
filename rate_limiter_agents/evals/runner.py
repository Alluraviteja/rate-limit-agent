from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..agents.error_pattern import ErrorPatternAgent
from ..agents.orchestrator import Orchestrator
from ..agents.token_bucket_health import TokenBucketHealthAgent
from ..agents.top_paths import TopPathsAgent
from ..database import AgentBase, RateLimiterBase
from ..models import EvalRunResult, RateLimitLog
from .scenarios import SCENARIOS, EvalScenario

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def run_evals(real_agent_db: Session) -> dict:
    run_id = str(uuid.uuid4())
    run_at = datetime.now(timezone.utc)
    all_results: list[EvalRunResult] = []

    for scenario in SCENARIOS:
        logger.info("eval scenario: %s", scenario.name)
        results = _run_scenario(scenario, real_agent_db, run_id, run_at)
        all_results.extend(results)

    total = len(all_results)
    sev_ok = sum(1 for r in all_results if r.severity_correct)
    act_ok = sum(1 for r in all_results if r.action_correct)
    total_cost = sum(float(r.cost_usd or 0) for r in all_results)
    total_tokens = sum(int(r.tokens_used or 0) for r in all_results)

    return {
        "run_id": run_id,
        "scenarios_run": len(SCENARIOS),
        "total_checks": total,
        "severity_accuracy_pct": round(sev_ok / total * 100, 1) if total else 0.0,
        "action_accuracy_pct": round(act_ok / total * 100, 1) if total else 0.0,
        "total_tokens_used": total_tokens,
        "total_cost_usd": round(total_cost, 6),
        "results": [_to_dict(r) for r in all_results],
    }


def _run_scenario(
    scenario: EvalScenario,
    real_agent_db: Session,
    run_id: str,
    run_at: datetime,
) -> list[EvalRunResult]:
    # Isolated in-memory DBs so eval logs never touch real data
    rate_engine = create_engine("sqlite:///:memory:")
    RateLimiterBase.metadata.create_all(bind=rate_engine)
    rate_session = sessionmaker(bind=rate_engine)()

    agent_engine = create_engine("sqlite:///:memory:")
    AgentBase.metadata.create_all(bind=agent_engine)
    agent_session = sessionmaker(bind=agent_engine)()

    try:
        for log_data in scenario.log_factory():
            rate_session.add(RateLimitLog(**log_data))
        rate_session.commit()

        app_id = 9999

        error_res = ErrorPatternAgent().analyze(rate_session, agent_session, app_id)
        token_res = TokenBucketHealthAgent().analyze(
            rate_session, agent_session, app_id
        )
        paths_res = TopPathsAgent().analyze(rate_session, agent_session, app_id)
        orch_res = Orchestrator().run(
            agent_session, app_id, error_res, token_res, paths_res
        )

        actuals = {
            "error_pattern": (
                error_res.severity,
                error_res.action,
                error_res.tokens_used,
                error_res.cost_usd,
            ),
            "token_bucket_health": (
                token_res.severity,
                token_res.action,
                token_res.tokens_used,
                token_res.cost_usd,
            ),
            "top_paths": (
                paths_res.severity,
                paths_res.action,
                paths_res.tokens_used,
                paths_res.cost_usd,
            ),
            "orchestrator": (
                orch_res.final_severity,
                orch_res.action,
                orch_res.tokens_used,
                orch_res.cost_usd,
            ),
        }

        eval_results: list[EvalRunResult] = []
        for agent_name, expected in scenario.expected.items():
            actual_sev, actual_act, tokens, cost = actuals[agent_name]
            row = EvalRunResult(
                run_id=run_id,
                scenario_name=scenario.name,
                agent_name=agent_name,
                expected_severity=expected["severity"],
                actual_severity=actual_sev,
                expected_action=expected["action"],
                actual_action=actual_act,
                severity_correct=(actual_sev == expected["severity"]),
                action_correct=(actual_act == expected["action"]),
                tokens_used=tokens,
                cost_usd=cost,
                run_at=run_at,
            )
            real_agent_db.add(row)
            eval_results.append(row)

        real_agent_db.commit()
        for r in eval_results:
            real_agent_db.refresh(r)

        return eval_results

    except Exception as e:
        logger.error("scenario %s failed: %s", scenario.name, e)
        real_agent_db.rollback()
        return []
    finally:
        rate_session.close()
        agent_session.close()
        rate_engine.dispose()
        agent_engine.dispose()


def _to_dict(r: EvalRunResult) -> dict:
    return {
        "scenario": r.scenario_name,
        "agent": r.agent_name,
        "expected_severity": r.expected_severity,
        "actual_severity": r.actual_severity,
        "expected_action": r.expected_action,
        "actual_action": r.actual_action,
        "severity_correct": r.severity_correct,
        "action_correct": r.action_correct,
        "tokens_used": r.tokens_used,
        "cost_usd": float(r.cost_usd or 0),
    }
