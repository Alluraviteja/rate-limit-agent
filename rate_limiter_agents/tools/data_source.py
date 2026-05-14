from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .mcp_client import MCPClient


@runtime_checkable
class RateLimitDataSource(Protocol):
    """Uniform interface for rate-limit data regardless of transport."""

    def list_apps(self) -> list[dict]: ...

    def get_app(self, app_info_id: int) -> dict: ...

    def get_error_summary(
        self, app_info_id: int, window_minutes: int, per_ip: bool = False
    ) -> dict: ...

    def get_token_health_summary(
        self, app_info_id: int, window_minutes: int, per_ip: bool = False
    ) -> dict: ...

    def get_top_paths_summary(
        self, app_info_id: int, window_minutes: int, per_ip: bool = False
    ) -> dict: ...


class MCPDataSource:
    """Reads from the rate-limiting-service MCP server.

    All per-app pipeline data (app detail + three summaries) is fetched in a
    single SSE session on first access, then served from an in-memory cache
    for the lifetime of this instance. The ``per_ip`` parameter is accepted for
    protocol compatibility but ignored — the MCP server always returns the full
    field set including IP-level stats.
    """

    def __init__(self, client: MCPClient) -> None:
        self._client = client
        self._pipeline_cache: dict[int, dict] = {}

    def _pipeline(self, app_info_id: int) -> dict:
        if app_info_id not in self._pipeline_cache:
            self._pipeline_cache[app_info_id] = self._client.fetch_pipeline_data(
                app_info_id
            )
        return self._pipeline_cache[app_info_id]

    def list_apps(self) -> list[dict]:
        return self._client.list_apps()

    def get_app(self, app_info_id: int) -> dict:
        return self._pipeline(app_info_id)["app"]

    def get_error_summary(
        self, app_info_id: int, window_minutes: int, per_ip: bool = False
    ) -> dict:
        return self._pipeline(app_info_id)["error_summary"]

    def get_token_health_summary(
        self, app_info_id: int, window_minutes: int, per_ip: bool = False
    ) -> dict:
        return self._pipeline(app_info_id)["token_summary"]

    def get_top_paths_summary(
        self, app_info_id: int, window_minutes: int, per_ip: bool = False
    ) -> dict:
        return self._pipeline(app_info_id)["paths_summary"]


def get_data_source() -> RateLimitDataSource:
    """Return the MCP-backed data source. Raises if MCP_SERVER_URL is not set."""
    from .mcp_client import get_mcp

    mcp = get_mcp()
    if mcp is None:
        raise RuntimeError(
            "No data source available: set MCP_SERVER_URL and MCP_SECRET"
        )
    return MCPDataSource(mcp)
