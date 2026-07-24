import json

import httpx
import pytest

from knowledge.logs.grafana import GrafanaLogClient, GrafanaLogError, GrafanaTarget


@pytest.mark.asyncio
async def test_grafana_client_queries_fixed_target_and_sanitizes_logs():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": {
                    "A": {
                        "frames": [
                            {
                                "schema": {
                                    "fields": [
                                        {"name": "Time"},
                                        {"name": "Line"},
                                    ]
                                },
                                "data": {
                                    "values": [
                                        [1721091600000, 1721091601000],
                                        [
                                            "ERROR OrderService token=secret-value "
                                            "java.lang.NullPointerException at "
                                            "com.example.OrderService.create(OrderService.java:156)",
                                            '{"level":"INFO","logger":"ApiController",'
                                            '"message":"request user@example.com phone 13800138000"}',
                                        ],
                                    ]
                                },
                            }
                        ]
                    }
                }
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GrafanaLogClient(
        http_client,
        url="https://grafana.example/api/ds/query",
        token="Bearer private-token",
        targets={
            "develop": GrafanaTarget("develop-uid", "api-center-develop", "develop"),
            "test": GrafanaTarget("test-uid", "api-center-release", "develop"),
            "prod": GrafanaTarget("prod-uid", "master", "master"),
        },
        max_entries=20,
        max_entry_chars=2000,
        max_total_chars=30000,
        app_label="api-center-server",
        query_max_lines=1000,
    )

    result = await client.query_trace(
        "trace-abc.123",
        "test",
        30,
        now_ms=1721093400000,
    )
    await http_client.aclose()

    assert captured["authorization"] == "Bearer private-token"
    assert captured["body"]["queries"][0]["expr"] == (
        '{app="api-center-server",namespace="api-center-release"} '
        '|= "trace-abc.123"'
    )
    assert captured["body"]["queries"][0]["maxLines"] == 1000
    assert captured["body"]["queries"][0]["direction"] == "backward"
    assert captured["body"]["queries"][0]["datasource"]["uid"] == "test-uid"
    assert captured["body"]["from"] == "1721091600000"
    assert captured["body"]["to"] == "1721093400000"
    assert result.code_branch == "develop"
    assert result.log_count == 2
    assert "NullPointerException" in result.exception_types
    serialized = json.dumps(result.to_model_dict(), ensure_ascii=False)
    assert "secret-value" not in serialized
    assert "private-token" not in serialized
    assert "user@example.com" not in serialized
    assert "13800138000" not in serialized
    assert "[REDACTED]" in serialized
    assert result.entries[0].stack_frames[0].file == "OrderService.java"
    assert result.entries[0].stack_frames[0].line == 156


@pytest.mark.asyncio
async def test_grafana_client_rejects_untrusted_scope_before_http_call():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GrafanaLogClient(
        http_client,
        url="https://grafana.example/api/ds/query",
        token="private-token",
        targets={"develop": GrafanaTarget("uid", "namespace", "develop")},
    )

    with pytest.raises(ValueError, match="environment"):
        await client.query_trace("trace-123", "other", 30)
    with pytest.raises(ValueError, match="trace_id"):
        await client.query_trace('trace"unsafe', "develop", 30)
    with pytest.raises(ValueError, match="time_range"):
        await client.query_trace("trace-123", "develop", 61)
    await http_client.aclose()

    assert called is False


@pytest.mark.asyncio
async def test_grafana_client_reports_status_without_response_or_token_body():
    token = "must-not-leak"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"unauthorized {token}")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GrafanaLogClient(
        http_client,
        url="https://grafana.example/api/ds/query",
        token=token,
        targets={"prod": GrafanaTarget("uid", "master", "master")},
    )

    with pytest.raises(GrafanaLogError) as captured:
        await client.query_trace("trace-123", "prod", 30)
    await http_client.aclose()

    assert "401" in str(captured.value)
    assert token not in str(captured.value)


@pytest.mark.asyncio
async def test_grafana_client_accepts_server_configured_24_hour_range():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"results": {"A": {"frames": []}}})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GrafanaLogClient(
        http_client,
        url="https://grafana.example/api/ds/query",
        token="private-token",
        targets={"test": GrafanaTarget("uid", "namespace", "develop")},
        max_time_range_minutes=1440,
    )

    result = await client.query_trace(
        "trace-24hours",
        "test",
        1440,
        now_ms=1721093400000,
    )
    await http_client.aclose()

    assert result.from_ms == 1721007000000
    assert captured["from"] == "1721007000000"


@pytest.mark.asyncio
async def test_grafana_client_extracts_only_observed_service_and_endpoint_signals():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": {
                    "A": {
                        "frames": [
                            {
                                "schema": {"fields": [{"name": "Line"}]},
                                "data": {
                                    "values": [[
                                        '{"logger":"OrderService","message":"POST /orders failed"}'
                                    ]]
                                },
                            }
                        ]
                    }
                }
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GrafanaLogClient(
        http_client,
        url="https://grafana.example/api/ds/query",
        token="private-token",
        targets={"test": GrafanaTarget("uid", "namespace", "develop")},
        max_time_range_minutes=1440,
    )

    result = await client.query_trace("trace-signals", "test", 1440)
    await http_client.aclose()

    assert result.service_names == ("OrderService",)
    assert result.endpoint_paths == ("/orders",)


@pytest.mark.asyncio
async def test_grafana_client_extracts_spring_logger_and_label_context():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": {
                    "A": {
                        "frames": [
                            {
                                "schema": {
                                    "fields": [
                                        {"name": "labels"},
                                        {"name": "Line"},
                                    ]
                                },
                                "data": {
                                    "values": [
                                        [{"app": "api-center-server"}],
                                        [
                                            "2026-07-17 10:01:35.826 INFO "
                                            "[trace-123] --- [nio-8001-exec-9] "
                                            "c.loctek.common.core.util.RequestUtil : "
                                            "Securing GET /auth/client/getClientInfo"
                                        ],
                                    ]
                                },
                            }
                        ]
                    }
                }
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GrafanaLogClient(
        http_client,
        url="https://grafana.example/api/ds/query",
        token="private-token",
        targets={"prod": GrafanaTarget("uid", "api-center-master", "master")},
        app_label="api-center-server",
    )

    result = await client.query_trace("trace-123", "prod", 30)
    await http_client.aclose()

    assert result.service_names == ("c.loctek.common.core.util.RequestUtil",)
    assert result.endpoint_paths == ("/auth/client/getClientInfo",)


@pytest.mark.asyncio
async def test_grafana_client_prioritizes_errors_beyond_result_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": {
                    "A": {
                        "frames": [
                            {
                                "schema": {"fields": [{"name": "Line"}]},
                                "data": {
                                    "values": [[
                                        "DEBUG first diagnostic line",
                                        "INFO second diagnostic line",
                                        "ERROR final BusinessException line",
                                    ]]
                                },
                            }
                        ]
                    }
                }
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GrafanaLogClient(
        http_client,
        url="https://grafana.example/api/ds/query",
        token="private-token",
        targets={"test": GrafanaTarget("uid", "api-center-release", "develop")},
        max_entries=2,
    )

    result = await client.query_trace("trace-123", "test", 30)
    await http_client.aclose()

    assert [entry.level for entry in result.entries] == ["ERROR", "INFO"]
    assert result.truncated is True


@pytest.mark.asyncio
async def test_grafana_error_classifies_auth_and_server_failures_for_retry():
    responses = iter([401, 503])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(responses), text="hidden upstream response")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GrafanaLogClient(
        http_client,
        url="https://grafana.example/api/ds/query",
        token="private-token",
        targets={"test": GrafanaTarget("uid", "namespace", "develop")},
    )

    with pytest.raises(GrafanaLogError) as auth:
        await client.query_trace("trace-auth", "test", 30)
    with pytest.raises(GrafanaLogError) as server:
        await client.query_trace("trace-server", "test", 30)
    await http_client.aclose()

    assert auth.value.retryable is False
    assert server.value.retryable is True
