from __future__ import annotations

import json
from time import perf_counter
from typing import Protocol, Sequence

from agents import FunctionTool, RunContextWrapper, function_tool

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.swagger.inspector import SwaggerInspector, SwaggerSource


class SwaggerSourceProvider(Protocol):
    async def list_for_domain(self, domain_id: str) -> Sequence[SwaggerSource]: ...


class EmptySwaggerSourceProvider:
    async def list_for_domain(self, domain_id: str) -> Sequence[SwaggerSource]:
        return ()


def create_domain_swagger_tool(
    inspector: SwaggerInspector | None,
    source_provider: SwaggerSourceProvider,
    domain_id: str,
    domain_name: str,
    agent_name: str,
) -> FunctionTool:
    """Create a Swagger tool whose domain and registered URLs are server-controlled."""

    @function_tool(
        name_override="inspect_domain_swagger",
        description_override=(
            f"查询{domain_name}登记过的 Swagger/OpenAPI 定义。"
            "只能传入用户问题，不能指定或调用任意 URL，也不会调用规范中的业务接口。"
        ),
    )
    async def inspect_domain_swagger(
        ctx: RunContextWrapper[AgentRunContext],
        query: str,
    ) -> str:
        call_id = str(
            getattr(ctx, "tool_call_id", "") or "inspect_domain_swagger-unknown"
        )
        ctx.context.start_tool(
            call_id,
            "inspect_domain_swagger",
            agent_name,
            {"query": query},
        )
        started_at = perf_counter()
        try:
            sources = await source_provider.list_for_domain(domain_id)
            if sources and inspector is None:
                raise RuntimeError("Swagger inspector is not configured")

            payload_sources = []
            for source in sources:
                result = await inspector.inspect(source, query)  # type: ignore[union-attr]
                operations = result.get("operations") or []
                refreshed_at = str(result.get("refreshed_at") or "")
                stale = bool(result.get("stale", False))
                for operation in operations:
                    ctx.context.add_swagger_citation(
                        source_id=source.source_id,
                        domain=domain_name,
                        operation=operation,
                        refreshed_at=refreshed_at,
                        stale=stale,
                    )
                payload_sources.append(
                    {
                        "source_id": source.source_id,
                        "stale": stale,
                        "refreshed_at": refreshed_at,
                        "operations": operations,
                    }
                )

            ctx.context.finish_tool(
                call_id,
                status="completed",
                duration_ms=(perf_counter() - started_at) * 1000,
            )
            return json.dumps({"sources": payload_sources}, ensure_ascii=False)
        except Exception:
            ctx.context.finish_tool(
                call_id,
                status="failed",
                duration_ms=(perf_counter() - started_at) * 1000,
            )
            # Inspector exceptions can contain a registered URL or HTTP auth details.
            # Keep the original exception out of model-visible tool output and tracing.
            return json.dumps(
                {"error": "Swagger 定义暂时不可用，请稍后重试。"},
                ensure_ascii=False,
            )

    return inspect_domain_swagger
