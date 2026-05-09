from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import AgentResult, OrchestratorResult
from ..providers import get_provider
from ..tools.memory_service import (
    get_or_create_baseline,
    get_recent_context,
    update_baseline,
)

logger = logging.getLogger(__name__)

_provider = get_provider()

_SYSTEM = """You are an orchestrator agent. Given signals from 3 specialist analysis agents,
decide the final severity and recommended action.

Escalation rules:
- If any agent is critical → final = critical
- If 2+ agents are high → final = critical
- If 1 agent is high → final = high
- If 2+ agents are medium → final = high
- If 1 agent is medium → final = medium
- Otherwise → severity of the highest agent or none

Action mapping:
- critical → block
- high → throttle
- medium → alert
- low/none → monitor

You may also receive recent_history (last few runs). Use it to detect trends:
escalating severity across runs warrants upgrading your decision by one level.
Stable or recovering patterns support downgrading.

Respond ONLY in this exact labeled format with no extra text:
FINAL_SEVERITY: none/low/medium/high/critical
ANOMALY: YES or NO
ACTION: monitor/alert/throttle/block
REASON: one sentence combining all signals, max 25 words"""


class Orchestrator:
    def run(
        self,
        agent_db: Session,
        app_info_id: int,
        error: AgentResult,
        token: AgentResult,
        paths: AgentResult,
        per_ip_address: bool = False,
    ) -> OrchestratorResult:
        try:
            baseline = get_or_create_baseline(agent_db, app_info_id)
            recent_history = get_recent_context(agent_db, app_info_id, limit=3)

            summary = {
                "rate_limiting_mode": "per-IP" if per_ip_address else "shared",
                "error_pattern": {
                    "severity": error.severity,
                    "block_rate": float(error.block_rate_pct or 0),
                    "reason": error.reason,
                },
                "token_bucket_health": {
                    "severity": token.severity,
                    "reason": token.reason,
                },
                "top_paths": {
                    "severity": paths.severity,
                    "block_rate": float(paths.block_rate_pct or 0),
                    "reason": paths.reason,
                },
                "baseline": {
                    "avg_rps_7d": float(baseline.avg_rps_7d),
                    "avg_block_rate_7d": float(baseline.avg_block_rate_7d),
                },
                "recent_history": recent_history,
            }

            user_msg = f"Agent signals:\n{json.dumps(summary, indent=2)}"

            response = _provider.complete_with_retry(_SYSTEM, user_msg, max_tokens=150)

            parsed = _parse(response.content)
            inp = response.input_tokens
            out = response.output_tokens
            cost = response.cost_usd

            result = OrchestratorResult(
                app_info_id=app_info_id,
                error_severity=error.severity,
                token_severity=token.severity,
                path_severity=paths.severity,
                final_severity=parsed.get("FINAL_SEVERITY", "none").lower(),
                anomaly_detected=parsed.get("ANOMALY", "NO").upper() == "YES",
                reason=parsed.get("REASON", ""),
                action=parsed.get("ACTION", "monitor").lower(),
                tokens_used=inp + out,
                cost_usd=round(cost, 8),
                run_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.error("orchestrator error: %s", e)
            result = OrchestratorResult(
                app_info_id=app_info_id,
                error_severity=error.severity,
                token_severity=token.severity,
                path_severity=paths.severity,
                final_severity="none",
                anomaly_detected=False,
                reason=f"agent_error: {e}",
                action="monitor",
                tokens_used=0,
                cost_usd=0,
                run_at=datetime.now(timezone.utc),
            )

        agent_db.add(result)
        agent_db.commit()
        agent_db.refresh(result)

        update_baseline(agent_db, app_info_id, [error, token, paths])
        return result


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            out[key.strip()] = val.strip()
    return out
