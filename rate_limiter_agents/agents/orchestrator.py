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
    get_time_baseline,
    update_baseline,
    update_time_baseline,
)

logger = logging.getLogger(__name__)

_provider = get_provider()

# ── Severity / action constants ───────────────────────────────────────────────

_LEVELS: dict[str, int] = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_LEVEL_NAMES: list[str] = ["none", "low", "medium", "high", "critical"]
_ACTION_MAP: dict[str, str] = {
    "critical": "block",
    "high": "throttle",
    "medium": "alert",
    "low": "monitor",
    "none": "monitor",
}

# LLM is now used only to write the human-readable reason sentence.
# Severity, action, and anomaly_detected are all computed deterministically below.
_REASON_SYSTEM = """You are a monitoring assistant for a rate limiter system.
Given signals from 3 analysis agents and time-of-day baseline context, write ONE sentence
(max 25 words) explaining the key anomaly for an operations engineer. Mention which agents
flagged issues and what metrics are elevated. Do not include severity labels or action words."""


# ── Pure-Python escalation logic ──────────────────────────────────────────────


def _escalate(error_sev: str, token_sev: str, paths_sev: str) -> str:
    """Deterministic multi-agent severity escalation.

    Rules (in priority order):
    - any agent critical          → critical
    - 2+ agents high              → critical
    - 1 agent high                → high
    - 2+ agents medium            → high
    - 1 agent medium              → medium
    - any agent low               → low
    - all none                    → none
    """
    sevs = [error_sev, token_sev, paths_sev]
    crit = sum(1 for s in sevs if s == "critical")
    high = sum(1 for s in sevs if s == "high")
    med = sum(1 for s in sevs if s == "medium")
    low_ = sum(1 for s in sevs if s == "low")

    if crit >= 1:
        return "critical"
    if high >= 2:
        return "critical"
    if high >= 1:
        return "high"
    if med >= 2:
        return "high"
    if med >= 1:
        return "medium"
    if low_ >= 1:
        return "low"
    return "none"


def _apply_trend(severity: str, history: list[dict]) -> tuple[str, str]:
    """Bump severity up or down one level when recent runs show a clear trend.

    Returns (adjusted_severity, direction) where direction is one of:
    'escalating', 'recovering', 'stable'.
    """
    if len(history) < 2:
        return severity, "stable"

    recent = history[-3:]
    seq = [_LEVELS.get(h.get("severity", "none"), 0) for h in recent]

    escalating = all(seq[i] < seq[i + 1] for i in range(len(seq) - 1))
    recovering = all(seq[i] > seq[i + 1] for i in range(len(seq) - 1))

    idx = _LEVELS.get(severity, 0)
    if escalating and idx < 4:
        return _LEVEL_NAMES[idx + 1], "escalating"
    if recovering and idx > 0:
        return _LEVEL_NAMES[idx - 1], "recovering"
    return severity, "stable"


def _fallback_reason(
    error: AgentResult, token: AgentResult, paths: AgentResult
) -> str:
    """Structured reason string used when the LLM call fails."""
    parts = []
    if error.severity and error.severity != "none":
        parts.append(
            f"error_pattern={error.severity} block_rate={float(error.block_rate_pct or 0):.1f}%"
        )
    if token.severity and token.severity != "none":
        parts.append(f"token_bucket={token.severity}")
    if paths.severity and paths.severity != "none":
        parts.append(
            f"top_paths={paths.severity} block_rate={float(paths.block_rate_pct or 0):.1f}%"
        )
    return "; ".join(parts) if parts else "all agents nominal"


# ── Orchestrator ──────────────────────────────────────────────────────────────


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
        baseline = get_or_create_baseline(agent_db, app_info_id)
        recent_history = get_recent_context(agent_db, app_info_id, limit=3)
        time_bl = get_time_baseline(agent_db, app_info_id)

        # ── Deterministic decision (no LLM) ───────────────────────────────────
        base_severity = _escalate(
            error.severity or "none",
            token.severity or "none",
            paths.severity or "none",
        )
        final_severity, trend_direction = _apply_trend(base_severity, recent_history)
        action = _ACTION_MAP[final_severity]
        anomaly_detected = _LEVELS.get(final_severity, 0) >= _LEVELS["medium"]

        # ── Spike multiplier (always computed, persisted for dashboard) ────────
        bl_block_rate = float(
            (time_bl.avg_block_rate_ewma if time_bl is not None else None)
            or baseline.avg_block_rate_7d
            or 0
        )
        current_block_rate = float(error.block_rate_pct or 0)
        spike_mult = (
            round(current_block_rate / bl_block_rate, 1) if bl_block_rate > 0 else None
        )

        # ── LLM call: reason sentence only ────────────────────────────────────
        tokens_used = 0
        cost = 0.0
        reason = _fallback_reason(error, token, paths)

        try:
            user_msg = json.dumps(
                {
                    "rate_limiting_mode": "per-IP" if per_ip_address else "shared",
                    "error_pattern": {
                        "severity": error.severity,
                        "block_rate_pct": current_block_rate,
                        "reason": error.reason,
                    },
                    "token_bucket_health": {
                        "severity": token.severity,
                        "reason": token.reason,
                    },
                    "top_paths": {
                        "severity": paths.severity,
                        "block_rate_pct": float(paths.block_rate_pct or 0),
                        "reason": paths.reason,
                    },
                    "time_context": {
                        "baseline_block_rate_this_hour": round(bl_block_rate, 2) if bl_block_rate else None,
                        "current_block_rate": current_block_rate,
                        "spike_multiplier": spike_mult,
                        "trend": trend_direction,
                    },
                },
                indent=2,
            )

            response = _provider.complete_with_retry(_REASON_SYSTEM, user_msg, max_tokens=60)
            reason = response.content.strip()
            tokens_used = response.input_tokens + response.output_tokens
            cost = response.cost_usd

        except Exception as e:
            logger.warning("reason LLM call failed, using structured fallback: %s", e)

        result = OrchestratorResult(
            app_info_id=app_info_id,
            error_severity=error.severity,
            token_severity=token.severity,
            path_severity=paths.severity,
            final_severity=final_severity,
            anomaly_detected=anomaly_detected,
            spike_multiplier=spike_mult,
            trend_direction=trend_direction,
            reason=reason,
            action=action,
            tokens_used=tokens_used,
            cost_usd=round(cost, 8),
            run_at=datetime.now(timezone.utc),
        )

        agent_db.add(result)
        agent_db.commit()
        agent_db.refresh(result)

        update_baseline(agent_db, app_info_id, [error, token, paths])
        update_time_baseline(agent_db, app_info_id, error)
        return result
