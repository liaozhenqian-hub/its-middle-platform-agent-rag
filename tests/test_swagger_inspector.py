from datetime import datetime, timezone

import httpx
import pytest

from knowledge.swagger.inspector import (
    InMemorySwaggerCache,
    SwaggerInspector,
    SwaggerSource,
    SwaggerUrlNotAllowedError,
)


OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "Metric API", "version": "1"},
    "paths": {
        "/api/metrics/{id}": {
            "get": {
                "operationId": "getMetric",
                "summary": "查询指标详情",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
                ],
                "responses": {"200": {"description": "成功"}},
            }
        },
        "/api/workflows": {
            "post": {
                "operationId": "createWorkflow",
                "summary": "创建工作流",
                "responses": {"201": {"description": "已创建"}},
            }
        },
    },
}


@pytest.mark.asyncio
async def test_swagger_inspector_refreshes_with_etag_and_returns_relevant_operation():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json=OPENAPI, headers={"ETag": '"v1"'})

    cache = InMemorySwaggerCache()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inspector = SwaggerInspector(
            client=client,
            cache=cache,
            allowed_hosts={"swagger.internal"},
        )
        source = SwaggerSource(
            source_id="swagger-1",
            url="https://swagger.internal/openapi.json",
            auth_type="bearer",
            bearer_token="private-token",
        )

        first = await inspector.inspect(source, "怎么查询指标详情")
        second = await inspector.inspect(source, "getMetric")

    assert first["stale"] is False
    assert first["operations"][0]["operation_id"] == "getMetric"
    assert first["operations"][0]["path"] == "/api/metrics/{id}"
    assert second["etag"] == '"v1"'
    assert requests[0].headers["authorization"] == "Bearer private-token"
    assert requests[1].headers["if-none-match"] == '"v1"'
    assert "private-token" not in str(first)


@pytest.mark.asyncio
async def test_swagger_inspector_uses_basic_auth_and_last_good_cache_on_failure():
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json=OPENAPI)
        raise httpx.ConnectError("offline", request=request)

    cache = InMemorySwaggerCache()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        inspector = SwaggerInspector(client, cache, {"swagger.internal"})
        source = SwaggerSource(
            source_id="swagger-1",
            url="https://swagger.internal/openapi.json",
            auth_type="basic",
            username="reader",
            password="secret",
        )
        await inspector.inspect(source, "workflow")
        fallback = await inspector.inspect(source, "workflow")

    assert fallback["stale"] is True
    assert fallback["operations"][0]["operation_id"] == "createWorkflow"
    assert "secret" not in str(fallback)


@pytest.mark.asyncio
async def test_swagger_inspector_rejects_unregistered_hosts_before_request():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("must not request"))
    ) as client:
        inspector = SwaggerInspector(client, InMemorySwaggerCache(), {"swagger.internal"})
        source = SwaggerSource(
            source_id="swagger-1",
            url="http://169.254.169.254/latest/meta-data",
            auth_type="none",
        )

        with pytest.raises(SwaggerUrlNotAllowedError):
            await inspector.inspect(source, "metadata")
