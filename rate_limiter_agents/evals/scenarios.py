from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable


@dataclass
class EvalScenario:
    name: str
    description: str
    log_factory: Callable[[], list[dict]]
    expected: dict[str, dict]  # agent_name -> {"severity": ..., "action": ...}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _log(
    *,
    app_info_id: int = 9999,
    was_blocked: bool = False,
    response_code: int = 200,
    remaining_tokens: int = 100,
    request_path: str = "/api/v1/data",
    http_method: str = "GET",
    request_at: datetime,
    reason: str | None = None,
    redis_failed: bool = False,
    client_ip: str = "1.2.3.4",
) -> dict:
    return {
        "app_info_id": app_info_id,
        "was_blocked": was_blocked,
        "response_code": response_code,
        "remaining_tokens": remaining_tokens,
        "request_path": request_path,
        "http_method": http_method,
        "request_at": request_at,
        "reason": reason,
        "redis_failed": redis_failed,
        "client_ip": client_ip,
    }


# ── Scenario factories ───────────────────────────────────────────────────────


def _normal_traffic() -> list[dict]:
    now = _now()
    paths = ["/api/v1/users", "/api/v1/items", "/api/v1/search", "/api/v1/status"]
    logs = []
    for i in range(200):
        at = now - timedelta(minutes=14) + timedelta(seconds=i * 4)
        blocked = i < 4
        logs.append(
            _log(
                request_at=at,
                was_blocked=blocked,
                response_code=429 if blocked else 200,
                remaining_tokens=80 + (i % 20),
                request_path=paths[i % len(paths)],
                reason="rate_limit" if blocked else None,
            )
        )
    return logs


def _high_error_rate() -> list[dict]:
    # 10% 5xx + 50% 4xx + 40% 2xx → error_pattern sees critical (has 5xx)
    now = _now()
    logs = []
    for i in range(100):
        at = now - timedelta(minutes=14) + timedelta(seconds=i * 8)
        if i < 10:
            code, blocked, reason = 500, True, "server_error"
        elif i < 60:
            code = 404 if i % 2 == 0 else 403
            blocked = i < 40
            reason = "not_found" if code == 404 else "forbidden"
        else:
            code, blocked, reason = 200, False, None
        logs.append(
            _log(
                request_at=at,
                was_blocked=blocked,
                response_code=code,
                remaining_tokens=50 + (i % 50),
                reason=reason,
            )
        )
    return logs


def _high_block_rate() -> list[dict]:
    # 35% block rate, no 5xx → error_pattern high
    now = _now()
    logs = []
    for i in range(100):
        at = now - timedelta(minutes=14) + timedelta(seconds=i * 8)
        blocked = i < 35
        logs.append(
            _log(
                request_at=at,
                was_blocked=blocked,
                response_code=429 if blocked else 200,
                remaining_tokens=50 + (i % 50),
                reason="rate_limit" if blocked else None,
            )
        )
    return logs


def _token_depletion() -> list[dict]:
    # 80% near depletion (0–3 tokens) → token_bucket_health critical
    now = _now()
    logs = []
    for i in range(100):
        at = now - timedelta(minutes=14) + timedelta(seconds=i * 8)
        if i < 20:
            tokens = 0
        elif i < 80:
            tokens = 2
        else:
            tokens = 90
        logs.append(
            _log(
                request_at=at,
                was_blocked=False,
                response_code=200,
                remaining_tokens=tokens,
            )
        )
    return logs


def _path_attack() -> list[dict]:
    # /api/attack: 180 req, 150 blocked (83%) → top_paths critical
    # /api/normal: 20 req, 0 blocked
    now = _now()
    logs = []
    for i in range(180):
        at = now - timedelta(minutes=59) + timedelta(seconds=i * 20)
        blocked = i < 150
        logs.append(
            _log(
                request_at=at,
                was_blocked=blocked,
                response_code=429 if blocked else 200,
                remaining_tokens=20 + (i % 30),
                request_path="/api/attack",
                reason="rate_limit" if blocked else None,
            )
        )
    for i in range(20):
        at = now - timedelta(minutes=59) + timedelta(seconds=i * 180)
        logs.append(
            _log(
                request_at=at,
                was_blocked=False,
                response_code=200,
                remaining_tokens=90,
                request_path="/api/normal",
            )
        )
    return logs


def _flash_crowd() -> list[dict]:
    # 500 requests, 1.6% block rate, healthy tokens — legitimate traffic spike
    now = _now()
    paths = ["/api/v1/products", "/api/v1/users", "/api/v1/orders", "/api/v1/search", "/api/v1/feed"]
    logs = []
    for i in range(500):
        at = now - timedelta(minutes=14) + timedelta(seconds=i * 1.68)
        blocked = i < 8
        logs.append(
            _log(
                request_at=at,
                was_blocked=blocked,
                response_code=429 if blocked else 200,
                remaining_tokens=70 + (i % 30),
                request_path=paths[i % len(paths)],
                reason="rate_limit" if blocked else None,
            )
        )
    return logs


def _gradual_ramp() -> list[dict]:
    # 25% block rate + 40% near-depletion → error_pattern medium, token medium
    # Orchestrator sees 2 medium agents → escalates to high
    now = _now()
    logs = []
    for i in range(100):
        at = now - timedelta(minutes=14) + timedelta(seconds=i * 8)
        blocked = i < 25
        tokens = 5 if i < 40 else 80
        logs.append(
            _log(
                request_at=at,
                was_blocked=blocked,
                response_code=429 if blocked else 200,
                remaining_tokens=tokens,
                request_path="/api/v1/data",
                reason="rate_limit" if blocked else None,
            )
        )
    return logs


def _multi_vector() -> list[dict]:
    # Path attack (83% block on /api/attack) + 20 depleted + 60 near-depletion
    # Tests that orchestrator fires critical when multiple independent signals align
    now = _now()
    logs = []
    for i in range(180):
        at = now - timedelta(minutes=59) + timedelta(seconds=i * 20)
        blocked = i < 150
        logs.append(
            _log(
                request_at=at,
                was_blocked=blocked,
                response_code=429 if blocked else 200,
                remaining_tokens=20 + (i % 30),
                request_path="/api/attack",
                reason="rate_limit" if blocked else None,
            )
        )
    for i in range(20):
        at = now - timedelta(minutes=59) + timedelta(seconds=i * 180)
        logs.append(
            _log(request_at=at, was_blocked=False, response_code=200, remaining_tokens=90, request_path="/api/normal")
        )
    for i in range(100):
        at = now - timedelta(minutes=14) + timedelta(seconds=i * 8)
        tokens = 0 if i < 20 else 2 if i < 80 else 90
        logs.append(
            _log(request_at=at, was_blocked=False, response_code=200, remaining_tokens=tokens, request_path="/api/v1/data")
        )
    return logs


# ── Scenario registry ────────────────────────────────────────────────────────

SCENARIOS: list[EvalScenario] = [
    EvalScenario(
        name="normal_traffic",
        description="Healthy traffic — low block rate, good token health, even path distribution",
        log_factory=_normal_traffic,
        expected={
            "error_pattern": {"severity": "none", "action": "monitor"},
            "token_bucket_health": {"severity": "none", "action": "monitor"},
            "top_paths": {"severity": "none", "action": "monitor"},
            "orchestrator": {"severity": "none", "action": "monitor"},
        },
    ),
    EvalScenario(
        name="high_error_rate",
        description="10% 5xx errors + 50% 4xx — error_pattern should fire critical",
        log_factory=_high_error_rate,
        expected={
            "error_pattern": {"severity": "critical", "action": "block"},
            "token_bucket_health": {"severity": "none", "action": "monitor"},
            "top_paths": {"severity": "none", "action": "monitor"},
            "orchestrator": {"severity": "critical", "action": "block"},
        },
    ),
    EvalScenario(
        name="high_block_rate",
        description="35% block rate, no 5xx — error_pattern high, no critical",
        log_factory=_high_block_rate,
        expected={
            "error_pattern": {"severity": "high", "action": "throttle"},
            "token_bucket_health": {"severity": "none", "action": "monitor"},
            "top_paths": {"severity": "none", "action": "monitor"},
            "orchestrator": {"severity": "high", "action": "throttle"},
        },
    ),
    EvalScenario(
        name="token_depletion",
        description="80% of requests at 0–2 remaining tokens — token agent should fire critical",
        log_factory=_token_depletion,
        expected={
            "error_pattern": {"severity": "none", "action": "monitor"},
            "token_bucket_health": {"severity": "critical", "action": "block"},
            "top_paths": {"severity": "none", "action": "monitor"},
            "orchestrator": {"severity": "critical", "action": "block"},
        },
    ),
    EvalScenario(
        name="path_attack",
        description="Single path with 83% block rate — top_paths critical, orchestrator critical",
        log_factory=_path_attack,
        expected={
            "error_pattern": {"severity": "high", "action": "throttle"},
            "token_bucket_health": {"severity": "none", "action": "monitor"},
            "top_paths": {"severity": "critical", "action": "block"},
            "orchestrator": {"severity": "critical", "action": "block"},
        },
    ),
    EvalScenario(
        name="flash_crowd",
        description="500 legitimate requests, 1.6% block rate — all agents should stay none",
        log_factory=_flash_crowd,
        expected={
            "error_pattern": {"severity": "none", "action": "monitor"},
            "token_bucket_health": {"severity": "none", "action": "monitor"},
            "top_paths": {"severity": "none", "action": "monitor"},
            "orchestrator": {"severity": "none", "action": "monitor"},
        },
    ),
    EvalScenario(
        name="gradual_ramp",
        description="25% block rate + 40% near-depletion — two medium agents, orchestrator escalates to high",
        log_factory=_gradual_ramp,
        expected={
            "error_pattern": {"severity": "medium", "action": "alert"},
            "token_bucket_health": {"severity": "medium", "action": "alert"},
            "top_paths": {"severity": "low", "action": "monitor"},
            "orchestrator": {"severity": "high", "action": "throttle"},
        },
    ),
    EvalScenario(
        name="multi_vector",
        description="Path attack (83% block) + token depletion (20 depleted) — orchestrator critical",
        log_factory=_multi_vector,
        expected={
            "error_pattern": {"severity": "high", "action": "throttle"},
            "token_bucket_health": {"severity": "critical", "action": "block"},
            "top_paths": {"severity": "critical", "action": "block"},
            "orchestrator": {"severity": "critical", "action": "block"},
        },
    ),
]
