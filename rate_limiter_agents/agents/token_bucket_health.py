from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import AgentResult
from ..providers import get_provider

logger = logging.getLogger(__name__)

_provider = get_provider()

_SYSTEM = """You are a token bucket health agent for a rate limiter system.
Analyze remaining token levels across requests to assess how close clients are to hitting their rate limits.

Rules:
- ANOMALY: YES if near_depletion_pct > 30 OR depleted_count > 0
- SEVERITY: critical if near_depletion_pct > 70 or depleted > 5, high if > 50%, medium if > 30%, low if > 10%, none otherwise
- ACTION: block for critical, throttle for high, alert for medium/low, monitor for none

Per-IP mode: a small number of IPs depleting their individual buckets is expected (bots/abusers); only escalate
when ips_depleted or ips_near_depletion represent a large fraction of unique_ips (coordinated surge).
Shared mode: near-depletion affects all users — apply thresholds strictly.

Respond ONLY in this exact labeled format with no extra text:
ANOMALY: YES or NO
SEVERITY: none/low/medium/high/critical
NEAR_DEPLETION_PCT: numeric value%
AVG_REMAINING: numeric value
REASON: one sentence summarizing token health, max 20 words
ACTION: monitor/alert/throttle/block"""


class TokenBucketHealthAgent:
    def analyze(
        self,
        agent_db: Session,
        app_info_id: int,
        per_ip_address: bool,
        summary: dict,
    ) -> AgentResult:
        try:
            mode = (
                "per-IP (each client IP has its own token bucket)"
                if per_ip_address
                else "shared (all clients share one token bucket)"
            )
            user_msg = (
                f"Rate limiting mode: {mode}\n"
                f"Token bucket health metrics (last 15 min):\n{json.dumps(summary, indent=2)}"
            )

            response = _provider.complete_with_retry(_SYSTEM, user_msg, max_tokens=200)

            parsed = _parse(response.content)
            result = AgentResult(
                app_info_id=app_info_id,
                agent_name="token_bucket_health",
                anomaly_detected=parsed.get("ANOMALY", "NO").upper() == "YES",
                severity=parsed.get("SEVERITY", "none").lower(),
                baseline_rps=(
                    _num(parsed.get("AVG_REMAINING"))
                    or summary.get("avg_remaining_tokens")
                ),
                total_requests=summary.get("total_requests"),
                reason=parsed.get("REASON", ""),
                action=parsed.get("ACTION", "monitor").lower(),
                tokens_used=response.input_tokens + response.output_tokens,
                cost_usd=round(response.cost_usd, 8),
                run_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.error("token_bucket_health agent error: %s", e)
            result = AgentResult(
                app_info_id=app_info_id,
                agent_name="token_bucket_health",
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
