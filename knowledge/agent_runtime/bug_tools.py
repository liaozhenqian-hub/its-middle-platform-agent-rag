from __future__ import annotations

import asyncio
import json
from time import perf_counter
from typing import Literal, Protocol

from agents import FunctionTool, RunContextWrapper, function_tool

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.logs.grafana import GrafanaLogClient


class PipelineRegistry(Protocol):
    def get(self, app_id: str, domain: str | None): ...


def _completed_duplicate_call(
    context: AgentRunContext,
    *,
    current_call_id: str,
    tool_name: str,
    arguments: dict,
) -> bool:
    return any(
        run.tool_call_id != current_call_id
        and run.tool_name == tool_name
        and run.status == "completed"
        and run.arguments == arguments
        for run in context.tool_runs
    )


def _duplicate_response() -> str:
    return json.dumps(
        {
            "duplicate_call_suppressed": True,
            "instruction": "Reuse the previous result from this run.",
        },
        ensure_ascii=False,
    )


def _completed_trace_query(
    context: AgentRunContext,
    *,
    current_call_id: str,
    trace_id: str,
    environment: str,
) -> bool:
    return any(
        run.tool_call_id != current_call_id
        and run.tool_name == "query_middle_trace_logs"
        and run.status == "completed"
        and run.arguments.get("trace_id") == trace_id
        and run.arguments.get("environment") == environment
        for run in context.tool_runs
    )


def create_trace_log_tool(
    client: GrafanaLogClient,
    *,
    agent_name: str,
) -> FunctionTool:
    @function_tool(
        name_override="query_middle_trace_logs",
        description_override=(
            "按 trace ID 查询中台系统的脱敏日志。环境只能是 develop、test 或 prod，"
            "时间范围只能是最近 1 到 60 分钟。"
        ),
    )
    async def query_middle_trace_logs(
        ctx: RunContextWrapper[AgentRunContext],
        trace_id: str,
        environment: Literal["develop", "test", "prod"],
        time_range_minutes: int = 60,
    ) -> str:
        call_id = str(
            getattr(ctx, "tool_call_id", "") or "query_middle_trace_logs-unknown"
        )
        arguments = {
            "trace_id": trace_id,
            "environment": environment,
            "time_range_minutes": time_range_minutes,
        }
        ctx.context.start_tool(
            call_id,
            "query_middle_trace_logs",
            agent_name,
            arguments,
        )
        started_at = perf_counter()
        if _completed_trace_query(
            ctx.context,
            current_call_id=call_id,
            trace_id=trace_id,
            environment=environment,
        ):
            ctx.context.finish_tool(call_id, status="completed", duration_ms=0.0)
            return _duplicate_response()
        try:
            result = await client.query_trace(
                trace_id,
                environment,
                time_range_minutes,
            )
            ctx.context.add_log_trace_citation(
                trace_id=result.trace_id,
                environment=result.environment,
                from_ms=result.from_ms,
                to_ms=result.to_ms,
                log_count=result.log_count,
                exception_types=result.exception_types,
                truncated=result.truncated,
                entries=result.entries,
            )
            ctx.context.finish_tool(
                call_id,
                status="completed",
                duration_ms=(perf_counter() - started_at) * 1000,
            )
            return json.dumps(result.to_model_dict(), ensure_ascii=False)
        except Exception:
            ctx.context.finish_tool(
                call_id,
                status="failed",
                duration_ms=(perf_counter() - started_at) * 1000,
            )
            raise

    return query_middle_trace_logs


def create_bug_code_search_tool(
    registry: PipelineRegistry,
    *,
    app_id: str,
    agent_name: str,
    environment_branches: dict[str, str] | None = None,
    keyword_k: int = 20,
    vector_k: int = 20,
    final_k: int = 5,
) -> FunctionTool:
    branches = environment_branches or {
        "develop": "develop",
        "test": "develop",
        "prod": "master",
    }

    @function_tool(
        name_override="search_bug_code",
        description_override=(
            "按 Bug 所在环境检索对应分支的中台代码。开发和测试使用 develop 代码，"
            "生产使用 master 代码；只传问题和环境，不传 branch。"
        ),
    )
    async def search_bug_code(
        ctx: RunContextWrapper[AgentRunContext],
        query: str,
        environment: Literal["develop", "test", "prod"],
    ) -> str:
        branch = branches[environment]
        call_id = str(getattr(ctx, "tool_call_id", "") or "search_bug_code-unknown")
        arguments = {"query": query, "environment": environment}
        ctx.context.start_tool(
            call_id,
            "search_bug_code",
            agent_name,
            arguments,
        )
        started_at = perf_counter()
        if _completed_duplicate_call(
            ctx.context,
            current_call_id=call_id,
            tool_name="search_bug_code",
            arguments=arguments,
        ):
            ctx.context.finish_tool(call_id, status="completed", duration_ms=0.0)
            return _duplicate_response()
        log_citations = [
            citation
            for citation in ctx.context.citations
            if citation.source_type == "log_trace"
        ]
        has_positive_logs = any(
            int(citation.metadata.get("log_count") or 0) > 0
            for citation in log_citations
        )
        if not has_positive_logs:
            ctx.context.finish_tool(call_id, status="completed", duration_ms=0.0)
            return json.dumps(
                {
                    "code_search_skipped": True,
                    "reason": (
                        "trace_query_returned_no_logs"
                        if log_citations
                        else "positive_log_evidence_required"
                    ),
                },
                ensure_ascii=False,
            )
        where_clauses: list[dict] = [
            {"source_type": "code"},
            {"branch": branch},
        ]
        if ctx.context.domain_id:
            where_clauses.append(
                {
                    "$or": [
                        {"domain_id": ctx.context.domain_id},
                        {"domain_id": "shared"},
                    ]
                }
            )
        where = {"$and": where_clauses}
        try:
            pipeline = registry.get(app_id, None)
            result = await asyncio.to_thread(
                pipeline.search,
                query,
                keyword_k,
                vector_k,
                final_k,
                where,
            )
            payload_results = []
            for item in result.final_results:
                domain = str(item.metadata.get("domain") or ctx.context.domain_id or "中台")
                ctx.context.add_knowledge_citation(
                    chunk_id=item.chunk_id,
                    heading=item.heading,
                    domain=domain,
                    metadata=item.metadata,
                )
                payload_results.append(
                    {
                        "chunk_id": item.chunk_id,
                        "heading": item.heading,
                        "content": item.content,
                        "domain": domain,
                        "retrieval_routes": list(item.retrieval_routes),
                    }
                )
            ctx.context.finish_tool(
                call_id,
                status="completed",
                duration_ms=(perf_counter() - started_at) * 1000,
            )
            return json.dumps(
                {
                    "environment": environment,
                    "code_branch": branch,
                    "results": payload_results,
                },
                ensure_ascii=False,
            )
        except Exception:
            ctx.context.finish_tool(
                call_id,
                status="failed",
                duration_ms=(perf_counter() - started_at) * 1000,
            )
            raise

    return search_bug_code
