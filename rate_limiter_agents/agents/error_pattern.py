from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import AgentResult, RateLimitLog
from ..providers import get_provider
from ..tools.metrics_aggregator import build_error_summary

logger = logging.getLogger(__name__)

_provider = get_provider()

_SYSTEM = """You are an error pattern analysis agent for a rate limiter system.
Analyze the provided error and block metrics and summarize the health of the API.

Rules:
- ANOMALY: YES if error_rate_pct > 20 OR block_rate_pct > 30 OR response_5xx > 0
- SEVERITY: critical if error_rate > 50% or any 5xx, high if > 30%, medium if > 20%, low if > 5%, none otherwise
- ACTION: block for critical, throttle for high, alert for medium/low, monitor for none

Per-IP mode: if ip_concentration_pct is high (one IP causing most blocks), the rate limiter is working as
intended against a single abuser — lower severity one level unless 5xx errors or very high total block rate.
Shared mode: high block rates affect all users — apply thresholds strictly.

Respond ONLY in this exact labeled format with no extra text:
ANOMALY: YES or NO
SEVERITY: none/low/medium/high/critical
ERROR_RATE: numeric value%
BLOCK_RATE: numeric value%
REASON: one sentence summarizing the error pattern, max 20 words
ACTION: monitor/alert/throttle/block"""


class ErrorPatternAgent:
    def analyze(
        self,
        rate_db: Session,
        agent_db: Session,
        app_info_id: int,
        per_ip_address: bool = False,
    ) -> AgentResult:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
            logs = (
                rate_db.query(RateLimitLog)
                .filter(
                    RateLimitLog.app_info_id == app_info_id,
                    RateLimitLog.request_at >= cutoff,
                )
                .limit(5000)
                .all()
            )

            summary = build_error_summary(logs, per_ip_address=per_ip_address)
            mode = (
                "per-IP (each client IP has its own token bucket)"
                if per_ip_address
                else "shared (all clients share one token bucket)"
            )
            user_msg = f"Rate limiting mode: {mode}\nError metrics (last 15 min):\n{json.dumps(summary, indent=2)}"

            response = _provider.complete_with_retry(_SYSTEM, user_msg, max_tokens=200)

            parsed = _parse(response.content)
            inp = response.input_tokens
            out = response.output_tokens
            cost = response.cost_usd

            result = AgentResult(
                app_info_id=app_info_id,
                agent_name="error_pattern",
                anomaly_detected=parsed.get("ANOMALY", "NO").upper() == "YES",
                severity=parsed.get("SEVERITY", "none").lower(),
                block_rate_pct=_num(parsed.get("BLOCK_RATE"))
                or summary["block_rate_pct"],
                total_requests=summary["total_requests"],
                blocked_requests=summary["blocked_requests"],
                reason=parsed.get("REASON", ""),
                action=parsed.get("ACTION", "monitor").lower(),
                tokens_used=inp + out,
                cost_usd=round(cost, 8),
                run_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.error("error_pattern agent error: %s", e)
            result = AgentResult(
                app_info_id=app_info_id,
                agent_name="error_pattern",
                anomaly_detected=False,
                severity="none",
                reason=f"agent_error: {e}",
                action="monitor",
                tokens_used=0,
                cost_usd=0,
                run_at=datetime.now(timezone.utc),
            )

        agent_db.add(result)
        agent_db.commit()
        agent_db.refresh(result)
        return result


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            out[key.strip()] = val.strip().rstrip("%")
    return out


def _num(val: str | None) -> float | None:
    if val is None:
        return None
    try:
        return float(val.replace("%", "").strip())
    except ValueError:
        return None
