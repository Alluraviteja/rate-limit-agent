from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from .. import config

logger = logging.getLogger(__name__)


def enforce_action(
    app_info_id: int,
    action: str,
    severity: str,
    reason: str,
) -> None:
    """POST every orchestrator decision to the configured enforcement webhook.

    The receiver is responsible for translating the action into real enforcement:
      block    → push IP to WAF / CDN block list or write Redis deny set
      throttle → update rate-limit policy in Redis
      alert    → fire PagerDuty / OpsGenie / Slack notification
      monitor  → no-op (record only)

    This call is best-effort and non-blocking to the pipeline — failures are
    logged but do not raise.
    """
    if not config.ENFORCEMENT_WEBHOOK_URL:
        return

    payload = {
        "app_info_id": app_info_id,
        "action": action,
        "severity": severity,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if config.ENFORCEMENT_WEBHOOK_SECRET:
        headers["Authorization"] = f"Bearer {config.ENFORCEMENT_WEBHOOK_SECRET}"

    try:
        resp = httpx.post(
            config.ENFORCEMENT_WEBHOOK_URL,
            json=payload,
            headers=headers,
            timeout=5.0,
        )
        resp.raise_for_status()
        logger.info(
            "enforcement webhook ok: app=%s action=%s status=%s",
            app_info_id,
            action,
            resp.status_code,
        )
    except Exception as exc:
        logger.error(
            "enforcement webhook failed (non-fatal): app=%s action=%s error=%s",
            app_info_id,
            action,
            exc,
        )
