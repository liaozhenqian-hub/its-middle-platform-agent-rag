import logging

import pytest
from agents import RunContextWrapper

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.agent_runtime.metric_mcp import (
    METRIC_MCP_DISCOVERY_TOOLS,
    METRIC_MCP_ALLOWED_TOOLS,
    MetricMCPClient,
)


def test_metric_guard_excludes_high_risk_raw_tools_from_discovery_allowlist():
    assert "searchMetricAppQueryResult" not in METRIC_MCP_DISCOVERY_TOOLS
    assert "searchSqlByMetricTypeAndNameExact" not in METRIC_MCP_DISCOVERY_TOOLS
    assert "searchSqlByMetricTypeAndNameFuzzy" not in METRIC_MCP_DISCOVERY_TOOLS
    assert set(METRIC_MCP_DISCOVERY_TOOLS).issubset(METRIC_MCP_ALLOWED_TOOLS)
from knowledge.config.settings import Settings


class FakeServer:
    def __init__(self, connect_error=None):
        self.connect_error = connect_error
        self.connected = False
        self.closed = False

    async def connect(self):
        if self.connect_error:
            raise self.connect_error
        self.connected = True

    async def cleanup(self):
        self.closed = True


@pytest.mark.asyncio
async def test_metric_mcp_injects_bearer_and_exact_read_only_allowlist():
    captured = {}
    fake_server = FakeServer()

    def server_factory(**kwargs):
        captured.update(kwargs)
        return fake_server

    settings = Settings(
        _env_file=None,
        METRIC_MCP_ENABLED=True,
        METRIC_MCP_URL="http://127.0.0.1:9000/mcp/messages",
        METRIC_MCP_BEARER_TOKEN="test-secret-token",
        METRIC_MCP_TIMEOUT_SECONDS=11,
    )
    client = MetricMCPClient(settings, server_factory=server_factory)

    await client.connect()

    assert client.available is True
    assert captured["params"]["headers"] == {
        "Authorization": "Bearer test-secret-token"
    }
    assert captured["params"]["timeout"] == 11
    assert captured["cache_tools_list"] is True
    assert captured["tool_filter"]["allowed_tool_names"] == list(
        METRIC_MCP_DISCOVERY_TOOLS
    )
    assert captured["require_approval"] == "never"

    context = AgentRunContext(conversation_id="conversation-1", run_id="run-1")
    custom_data = captured["custom_data_extractor"]
    extracted = custom_data(
        type(
            "MCPContext",
            (),
            {
                "run_context": RunContextWrapper(context),
                "tool_name": "searchBizMetric",
                "arguments": {"query": "收入", "access_token": "must-hide"},
                "is_error": False,
            },
        )()
    )

    assert extracted == {"mcp_tool": "searchBizMetric"}
    assert context.citations[0].source_id == "searchBizMetric"
    assert context.tool_runs[0].tool_name == "searchBizMetric"
    assert context.tool_runs[0].arguments == {"query": "收入"}

    await client.close()
    assert fake_server.closed is True


@pytest.mark.asyncio
async def test_metric_mcp_does_not_duplicate_existing_bearer_prefix():
    captured = {}
    fake_server = FakeServer()

    def server_factory(**kwargs):
        captured.update(kwargs)
        return fake_server

    settings = Settings(
        _env_file=None,
        METRIC_MCP_ENABLED=True,
        METRIC_MCP_URL="http://127.0.0.1:9000/mcp/messages",
        METRIC_MCP_BEARER_TOKEN="Bearer test-secret-token",
    )

    await MetricMCPClient(settings, server_factory=server_factory).connect()

    assert captured["params"]["headers"] == {
        "Authorization": "Bearer test-secret-token"
    }


@pytest.mark.asyncio
async def test_metric_mcp_connection_failure_degrades_without_leaking_token(caplog):
    token = "must-not-appear"
    fake_server = FakeServer(connect_error=RuntimeError(f"failure {token}"))
    settings = Settings(
        _env_file=None,
        METRIC_MCP_ENABLED=True,
        METRIC_MCP_URL="http://127.0.0.1:9000/mcp/messages",
        METRIC_MCP_BEARER_TOKEN=token,
    )
    client = MetricMCPClient(settings, server_factory=lambda **_: fake_server)

    with caplog.at_level(logging.WARNING):
        await client.connect()

    assert client.available is False
    assert client.status == "unavailable"
    assert token not in caplog.text
    assert fake_server.closed is True


@pytest.mark.asyncio
async def test_metric_mcp_missing_configuration_degrades_without_constructing_server():
    settings = Settings(_env_file=None, METRIC_MCP_ENABLED=True)
    client = MetricMCPClient(
        settings,
        server_factory=lambda **_: pytest.fail("server must not be constructed"),
    )

    await client.connect()

    assert client.available is False
    assert client.status == "unavailable"
