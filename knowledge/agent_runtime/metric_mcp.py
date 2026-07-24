from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from agents.mcp import MCPServerStreamableHttp, create_static_tool_filter

from knowledge.config.settings import Settings


logger = logging.getLogger(__name__)

METRIC_MCP_ALLOWED_TOOLS = (
    "metricMcpInfo",
    "searchAtomicMetric",
    "searchDerivedMetric",
    "searchCompositeMetric",
    "searchBizMetric",
    "searchBizMetricDetail",
    "searchMetricApp",
    "searchMetricAppQueryResult",
    "searchSqlByMetricTypeAndNameExact",
    "searchSqlByMetricTypeAndNameFuzzy",
)

METRIC_MCP_DISCOVERY_TOOLS = tuple(
    tool
    for tool in METRIC_MCP_ALLOWED_TOOLS
    if tool
    not in {
        "searchMetricAppQueryResult",
        "searchSqlByMetricTypeAndNameExact",
        "searchSqlByMetricTypeAndNameFuzzy",
    }
)


def _bearer_authorization(token: str) -> str:
    normalized = token.strip()
    if normalized.lower().startswith("bearer "):
        normalized = normalized[7:].strip()
    return f"Bearer {normalized}"


def _extract_mcp_audit(context: Any) -> dict[str, str]:
    run_context = context.run_context.context
    call_id = f"mcp-{uuid4()}"
    run_context.add_mcp_citation(context.tool_name)
    run_context.start_tool(
        call_id,
        context.tool_name,
        "指标平台专家",
        dict(context.arguments),
    )
    run_context.finish_tool(
        call_id,
        status="failed" if context.is_error else "completed",
    )
    return {"mcp_tool": context.tool_name}


class MetricMCPClient:
    """Own the optional metric MCP connection for the FastAPI lifespan."""

    def __init__(
        self,
        settings: Settings,
        server_factory: Callable[..., Any] = MCPServerStreamableHttp,
    ):
        self.settings = settings
        self._server_factory = server_factory
        self.server: Any | None = None
        self.status = "disabled" if not settings.metric_mcp_enabled else "unavailable"

    @property
    def available(self) -> bool:
        return self.status == "available" and self.server is not None

    async def connect(self) -> None:
        if not self.settings.metric_mcp_enabled:
            logger.info("Metric MCP disabled by configuration")
            return
        token = self.settings.resolved_metric_mcp_bearer_token
        if not self.settings.metric_mcp_url.strip() or not token:
            logger.warning("Metric MCP unavailable: URL or bearer credential is not configured")
            self.status = "unavailable"
            return

        server = self._server_factory(
            params={
                "url": self.settings.metric_mcp_url.strip(),
                "headers": {"Authorization": _bearer_authorization(token)},
                "timeout": self.settings.metric_mcp_timeout_seconds,
            },
            cache_tools_list=True,
            name="metric-platform-mcp",
            client_session_timeout_seconds=self.settings.metric_mcp_timeout_seconds,
            tool_filter=create_static_tool_filter(
                list(
                    METRIC_MCP_DISCOVERY_TOOLS
                    if self.settings.metric_query_guard_enabled
                    else METRIC_MCP_ALLOWED_TOOLS
                )
            ),
            require_approval="never",
            custom_data_extractor=_extract_mcp_audit,
        )
        try:
            await server.connect()
        except Exception as exc:
            logger.warning(
                "Metric MCP connection failed; RAG remains available error_type=%s",
                type(exc).__name__,
            )
            try:
                await server.cleanup()
            except Exception as cleanup_exc:
                logger.warning(
                    "Metric MCP cleanup failed error_type=%s",
                    type(cleanup_exc).__name__,
                )
            self.status = "unavailable"
            return

        self.server = server
        self.status = "available"
        allowed_count = len(
            METRIC_MCP_DISCOVERY_TOOLS
            if self.settings.metric_query_guard_enabled
            else METRIC_MCP_ALLOWED_TOOLS
        )
        logger.info("Metric MCP connected allowed_tool_count=%d", allowed_count)

    async def close(self) -> None:
        if self.server is not None:
            await self.server.cleanup()
            self.server = None
        if self.status == "available":
            self.status = "closed"
