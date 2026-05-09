from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import AgentResult, RateLimitLog
from ..providers import get_provider
from ..tools.metrics_aggregator import build_top_paths_summary

logger = logging.getLogger(__name__)

_provider = get_provider()

_SYSTEM = """You are a top paths analysis agent for a rate limiter system.
Analyze which API endpoints receive the most traffic and which have the highest block rates.

Rules:
- ANOMALY: YES if any single path has block_rate_pct > 50 OR top path accounts for > 80% of all traffic
- SEVERITY: critical if block_rate > 80% on any path, high if > 60%, medium if > 40%, low if > 20%, none otherwise
- ACTION: block for critical, throttle for high, alert for medium/low, monitor for none

Per-IP mode: check top_blocking_ips on high-block paths; if blocks are concentrated in 1-2 IPs the rate
limiter is working correctly — lower severity one level. If many unique IPs are hitting the same path, apply
thresholds strictly (coordinated attack or traffic spike).
Shared mode: path block rates affect all users — apply thresholds strictly.

Respond ONLY in this exact labeled format with no extra text:
ANOMALY: YES or NO
SEVERITY: none/low/medium/high/critical
TOP_PATH: the single most requested path
BLOCK_RATE: block rate of the top path as numeric value%
REASON: one sentence summarizing traffic distribution, max 20 words
ACTION: monitor/alert/throttle/block"""


class TopPathsAgent:
    def analyze(
        self,
        rate_db: Session,
        agent_db: Session,
        app_info_id: int,
        per_ip_address: bool = False,
    ) -> AgentResult:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=60)
            logs = (
                rate_db.query(RateLimitLog)
                .filter(
                    RateLimitLog.app_info_id == app_info_id,
                    RateLimitLog.request_at >= cutoff,
                )
                .limit(5000)
                .all()
            )

            summary = build_top_paths_summary(logs, per_ip_address=per_ip_address)
            mode = (
                "per-IP (each client IP has its own token bucket)"
                if per_ip_address
                else "shared (all clients share one token bucket)"
            )
            user_msg = f"Rate limiting mode: {mode}\nTop paths traffic metrics (last 60 min):\n{json.dumps(summary, indent=2)}"

            response = _provider.complete_with_retry(_SYSTEM, user_msg, max_tokens=200)

            parsed = _parse(response.content)
            inp = response.input_tokens
            out = response.output_tokens
            cost = response.cost_usd

            top = (
                summary["top_paths_by_traffic"][0]
                if summary["top_paths_by_traffic"]
                else {}
            )

            result = AgentResult(
                app_info_id=app_info_id,
                agent_name="top_paths",
                anomaly_detected=parsed.get("ANOMALY", "NO").upper() == "YES",
                severity=parsed.get("SEVERITY", "none").lower(),
                block_rate_pct=_num(parsed.get("BLOCK_RATE"))
                or top.get("block_rate_pct"),
                total_requests=summary["total_requests"],
                blocked_requests=sum(
                    p["blocked"] for p in summary["top_paths_by_traffic"]
                ),
                unique_ips=summary["unique_paths"],
                reason=parsed.get("REASON", ""),
                action=parsed.get("ACTION", "monitor").lower(),
                tokens_used=inp + out,
                cost_usd=round(cost, 8),
                run_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.error("top_paths agent error: %s", e)
            result = AgentResult(
                app_info_id=app_info_id,
                agent_name="top_paths",
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
