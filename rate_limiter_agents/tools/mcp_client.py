from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0
_RETRY_DELAY = 1.0


def _to_snake(name: str) -> str:
    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s1).lower()


def _normalize(obj: Any) -> Any:
    """Recursively convert camelCase dict keys to snake_case."""
    if isinstance(obj, dict):
        return {_to_snake(k): _normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize(item) for item in obj]
    return obj


def _parse_result(result: Any, tool_name: str) -> Any:
    if result.isError or not result.content:
        raise RuntimeError(f"MCP tool {tool_name!r} returned error: {result}")
    return _normalize(json.loads(result.content[0].text))


class MCPClient:
    """Sync wrapper around the rate-limiting-service MCP server (HTTP+SSE transport).

    Each call to a single-tool method opens one SSE session.
    ``fetch_pipeline_data`` batches all four per-app calls into a single session
    to avoid the SSE handshake cost on every tool invocation.
    """

    def __init__(
        self,
        base_url: str,
        secret: str,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._sse_url = f"{base_url.rstrip('/')}/mcp/sse"
        self._headers = {"X-MCP-Secret": secret}
        self._timeout = timeout

    # ── internal async helpers ───────────────────────────────────────────────

    async def _call(self, tool_name: str, arguments: dict[str, Any], attempt: int = 0) -> Any:
        try:
            async with sse_client(
                self._sse_url, headers=self._headers, timeout=self._timeout
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
            return _parse_result(result, tool_name)
        except Exception as exc:
            if attempt < 1:
                logger.warning(
                    "MCP %r failed (attempt %d), retrying in %.1fs: %s",
                    tool_name, attempt + 1, _RETRY_DELAY, exc,
                )
                await asyncio.sleep(_RETRY_DELAY)
                return await self._call(tool_name, arguments, attempt + 1)
            raise

    async def _call_many(
        self, calls: list[tuple[str, dict[str, Any]]], attempt: int = 0
    ) -> list[Any]:
        """Multiple sequential tool calls over a single SSE session."""
        try:
            async with sse_client(
                self._sse_url, headers=self._headers, timeout=self._timeout
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    results = []
                    for tool_name, arguments in calls:
                        result = await session.call_tool(tool_name, arguments)
                        results.append(_parse_result(result, tool_name))
            return results
        except Exception as exc:
            if attempt < 1:
                logger.warning(
                    "MCP batch call failed (attempt %d), retrying in %.1fs: %s",
                    attempt + 1, _RETRY_DELAY, exc,
                )
                await asyncio.sleep(_RETRY_DELAY)
                return await self._call_many(calls, attempt + 1)
            raise

    def _run(self, coro: Any) -> Any:
        return asyncio.run(coro)

    # ── pipeline batch (4 calls, 1 SSE connection) ───────────────────────────

    def fetch_pipeline_data(self, app_info_id: int) -> dict:
        """Fetch app detail + all three summaries in one SSE session."""
        app, error_summary, token_summary, paths_summary = self._run(
            self._call_many([
                ("get_app", {"appInfoId": app_info_id}),
                ("get_error_summary", {"appInfoId": app_info_id, "windowMinutes": 15}),
                ("get_token_health_summary", {"appInfoId": app_info_id, "windowMinutes": 15}),
                ("get_top_paths_summary", {"appInfoId": app_info_id, "windowMinutes": 60}),
            ])
        )
        return {
            "app": app,
            "error_summary": error_summary,
            "token_summary": token_summary,
            "paths_summary": paths_summary,
        }

    # ── individual tool calls ────────────────────────────────────────────────

    def list_apps(self) -> list[dict]:
        return self._run(self._call("list_apps", {}))

    def get_service_health(self) -> dict:
        return self._run(self._call("get_service_health", {}))

    def get_bucket_state(self, app_info_id: int) -> dict:
        return self._run(self._call("get_bucket_state", {"appInfoId": app_info_id}))

    def get_all_bucket_states(self) -> list[dict]:
        return self._run(self._call("get_all_bucket_states", {}))

    def get_redis_failure_stats(
        self, app_info_id: int | None, window_minutes: int
    ) -> dict:
        args: dict[str, Any] = {"windowMinutes": window_minutes}
        if app_info_id is not None:
            args["appInfoId"] = app_info_id
        return self._run(self._call("get_redis_failure_stats", args))


# ── module-level singleton ───────────────────────────────────────────────────

_mcp: MCPClient | None = None


def _init() -> None:
    global _mcp
    from .. import config

    if config.MCP_SERVER_URL:
        _mcp = MCPClient(config.MCP_SERVER_URL, config.MCP_SECRET)
        logger.info("MCP client configured: %s", config.MCP_SERVER_URL)


_init()


def get_mcp() -> MCPClient | None:
    return _mcp
