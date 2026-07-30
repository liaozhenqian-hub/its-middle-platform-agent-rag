from __future__ import annotations

import asyncio
import json
import re
from time import perf_counter
from typing import Any, Protocol

from agents import FunctionTool, RunContextWrapper, function_tool

from knowledge.agent_runtime.context import AgentRunContext, RuntimeSpan


class PipelineRegistry(Protocol):
    def get(self, app_id: str, domain: str | None): ...


def _record_retrieval_spans(context: AgentRunContext, result: Any) -> None:
    for stage, duration_ms in (getattr(result, "stage_timings_ms", None) or {}).items():
        context.runtime_spans.append(
            RuntimeSpan(
                kind="tool",
                name=f"retrieval.{stage}",
                status="completed",
                duration_ms=max(0.0, float(duration_ms)),
            )
        )


def _code_branch_for_message(message: str) -> str | None:
    normalized = "".join(message.casefold().split())
    if any(marker in normalized for marker in ("开发环境", "开发", "develop", "dev", "测试环境", "测试", "test")):
        return "develop"
    if any(marker in normalized for marker in ("线上环境", "线上", "生产环境", "生产", "prod", "production")):
        return "master"
    return None


def create_domain_rag_tool(
    registry: PipelineRegistry,
    tool_name: str,
    app_id: str,
    domain: str,
    agent_name: str,
    keyword_k: int = 20,
    vector_k: int = 20,
    final_k: int = 5,
    max_calls: int = 6,
    max_identical_queries: int = 1,
) -> FunctionTool:
    """Create a RAG tool whose application and domain cannot be model-controlled."""

    @function_tool(
        name_override=tool_name,
        description_override=(
            f"检索{domain}内部知识。回答该领域事实前必须调用，只传入用户问题。"
        ),
    )
    async def search_domain_knowledge(
        ctx: RunContextWrapper[AgentRunContext],
        query: str,
    ) -> str:
        call_id = str(getattr(ctx, "tool_call_id", "") or f"{tool_name}-unknown")
        ctx.context.start_tool(call_id, tool_name, agent_name, {"query": query})
        started_at = perf_counter()
        reservation = ctx.context.reserve_retrieval(
            tool_name,
            query,
            max_calls,
            max_identical_queries,
        )
        if reservation != "allowed":
            ctx.context.finish_tool(call_id, status="skipped", duration_ms=0.0)
            payload = {"status": "duplicate_query", "reuse_existing_evidence": True}
            if reservation == "budget_exhausted":
                payload = {"status": "budget_exhausted", "max_calls": max_calls}
            return json.dumps(payload, ensure_ascii=False)
        try:
            pipeline = await asyncio.to_thread(registry.get, app_id, domain)
            result = await asyncio.to_thread(
                pipeline.search,
                query,
                keyword_k,
                vector_k,
                final_k,
                None,
            )
            _record_retrieval_spans(ctx.context, result)
            payload_results = []
            for item in result.final_results:
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
                    "retrieval_needed": result.retrieval_needed,
                    "clarification_needed": result.clarification_needed,
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

    return search_domain_knowledge


def create_scoped_rag_tool(
    registry: PipelineRegistry,
    tool_name: str,
    app_id: str,
    domain_id: str,
    domain_name: str,
    source_type: str,
    agent_name: str,
    keyword_k: int = 20,
    vector_k: int = 20,
    final_k: int = 5,
    max_calls: int = 6,
    max_identical_queries: int = 1,
) -> FunctionTool:
    """Create a fixed domain + shared RAG tool for one source modality."""
    base_clauses = [
            {"$or": [{"domain_id": domain_id}, {"domain_id": "shared"}]},
            {"source_type": source_type},
    ]

    @function_tool(
        name_override=tool_name,
        description_override=f"检索{domain_name}的{source_type}知识，只传入用户问题。",
    )
    async def search_scoped_knowledge(
        ctx: RunContextWrapper[AgentRunContext],
        query: str,
    ) -> str:
        call_id = str(getattr(ctx, "tool_call_id", "") or f"{tool_name}-unknown")
        ctx.context.start_tool(call_id, tool_name, agent_name, {"query": query})
        started_at = perf_counter()
        reservation = ctx.context.reserve_retrieval(
            tool_name,
            query,
            max_calls,
            max_identical_queries,
        )
        if reservation != "allowed":
            ctx.context.finish_tool(call_id, status="skipped", duration_ms=0.0)
            payload = {"status": "duplicate_query", "reuse_existing_evidence": True}
            if reservation == "budget_exhausted":
                payload = {"status": "budget_exhausted", "max_calls": max_calls}
            return json.dumps(payload, ensure_ascii=False)
        try:
            pipeline = await asyncio.to_thread(registry.get, app_id, None)
            clauses = list(base_clauses)
            if source_type == "code":
                branch = _code_branch_for_message(ctx.context.current_user_message)
                if branch is not None:
                    clauses.append({"branch": branch})
            where = {"$and": clauses}
            result = await asyncio.to_thread(
                pipeline.search,
                query,
                keyword_k,
                vector_k,
                final_k,
                where,
            )
            _record_retrieval_spans(ctx.context, result)
            payload_results = []
            for item in result.final_results:
                ctx.context.add_knowledge_citation(
                    chunk_id=item.chunk_id,
                    heading=item.heading,
                    domain=domain_name,
                    metadata=item.metadata,
                )
                payload_results.append(
                    {
                        "chunk_id": item.chunk_id,
                        "heading": item.heading,
                        "content": item.content,
                        "domain": domain_name,
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
                    "retrieval_needed": result.retrieval_needed,
                    "clarification_needed": result.clarification_needed,
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

    return search_scoped_knowledge


def create_domain_evidence_tool(
    *,
    registry: PipelineRegistry,
    inspector: Any | None,
    source_provider: Any | None,
    app_id: str,
    domain_id: str,
    domain_name: str,
    agent_name: str,
    keyword_k: int = 20,
    vector_k: int = 20,
    final_k: int = 5,
    max_calls: int = 3,
    max_identical_queries: int = 1,
    retrieval_timeout_seconds: float = 20,
) -> FunctionTool:
    """Collect task-specific evidence while keeping scope and call count server-controlled."""

    async def search_modality(
        ctx: RunContextWrapper[AgentRunContext], query: str, source_type: str
    ) -> dict[str, Any]:
        pipeline = await asyncio.to_thread(registry.get, app_id, None)
        clauses: list[dict[str, Any]] = [
            {"$or": [{"domain_id": domain_id}, {"domain_id": "shared"}]},
            {"source_type": source_type},
        ]
        if source_type == "code":
            branch = _code_branch_for_message(ctx.context.current_user_message)
            if branch:
                clauses.append({"branch": branch})
        result = await asyncio.to_thread(
            pipeline.search,
            query,
            keyword_k,
            vector_k,
            final_k,
            {"$and": clauses},
        )
        _record_retrieval_spans(ctx.context, result)
        search_results = [result]
        exact_type_chunks: list[Any] = []
        if source_type == "code" and ctx.context.task_type == "api_contract":
            referenced_types: list[str] = []
            for hit in result.final_results:
                for identifier in re.findall(
                    r"\b[A-Z][A-Za-z0-9]*(?:VO|DTO|Req|Resp|Request|Response)\b",
                    str(hit.content or ""),
                ):
                    if identifier not in referenced_types and identifier not in query:
                        referenced_types.append(identifier)
                    if len(referenced_types) >= 2:
                        break
                if len(referenced_types) >= 2:
                    break
            if referenced_types:
                for identifier in referenced_types:
                    exact_type_chunks.extend(
                        await asyncio.to_thread(
                            registry.repository.get_chunks,
                            {"$and": [*clauses, {"symbol_name": identifier}]},
                            None,
                        )
                    )
                if not exact_type_chunks:
                    supplemental = await asyncio.to_thread(
                        pipeline.search,
                        " ".join(referenced_types) + " 字段定义",
                        min(keyword_k, 12),
                        min(vector_k, 12),
                        final_k,
                        {"$and": clauses},
                    )
                    _record_retrieval_spans(ctx.context, supplemental)
                    search_results.append(supplemental)
        items: list[dict[str, Any]] = []
        seen_chunks: set[str] = set()
        for item in exact_type_chunks:
            if item.chunk_id in seen_chunks:
                continue
            seen_chunks.add(item.chunk_id)
            ctx.context.add_knowledge_citation(
                chunk_id=item.chunk_id,
                heading=item.heading,
                domain=domain_name,
                metadata=item.metadata,
            )
            items.append(
                {
                    "chunk_id": item.chunk_id,
                    "heading": item.heading,
                    "content": item.content,
                    "retrieval_routes": ["exact_symbol"],
                }
            )
        for search_result in search_results:
            for item in search_result.final_results:
                if item.chunk_id in seen_chunks:
                    continue
                seen_chunks.add(item.chunk_id)
                ctx.context.add_knowledge_citation(
                    chunk_id=item.chunk_id,
                    heading=item.heading,
                    domain=domain_name,
                    metadata=item.metadata,
                )
                items.append(
                    {
                        "chunk_id": item.chunk_id,
                        "heading": item.heading,
                        "content": item.content,
                        "retrieval_routes": list(item.retrieval_routes),
                    }
                )
        return {"source_type": source_type, "results": items}

    async def inspect_swagger(
        ctx: RunContextWrapper[AgentRunContext], query: str
    ) -> dict[str, Any]:
        sources = (
            await source_provider.list_for_domain(domain_id)
            if source_provider is not None
            else ()
        )
        payload_sources: list[dict[str, Any]] = []
        if sources and inspector is None:
            return {"source_type": "swagger", "error": "swagger_unavailable", "sources": []}
        for source in sources:
            result = await inspector.inspect(source, query)
            operations = list(result.get("operations") or [])
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
        return {"source_type": "swagger", "sources": payload_sources}

    @function_tool(
        name_override="collect_domain_evidence",
        description_override=(
            f"为{domain_name}问题收集受控证据。只传入用户问题；服务端会根据任务类型"
            "选择产品文档、代码和已登记 Swagger，最多执行三次底层检索。"
        ),
    )
    async def collect_domain_evidence(
        ctx: RunContextWrapper[AgentRunContext], query: str
    ) -> str:
        call_id = str(
            getattr(ctx, "tool_call_id", "") or "collect_domain_evidence-unknown"
        )
        ctx.context.start_tool(
            call_id, "collect_domain_evidence", agent_name, {"task_type": ctx.context.task_type}
        )
        started_at = perf_counter()
        if domain_id in ctx.context.evidence_collection_domains:
            ctx.context.finish_tool(call_id, status="skipped", duration_ms=0.0)
            return json.dumps(
                {
                    "status": "duplicate_query",
                    "reuse_existing_evidence": True,
                    "domain_id": domain_id,
                },
                ensure_ascii=False,
            )
        ctx.context.evidence_collection_domains.append(domain_id)
        task_type = ctx.context.task_type or "unknown"
        plans = {
            "how_to": ["product_document"],
            "api_contract": ["code", "swagger"],
            "code_lookup": ["code"],
            "requirement_analysis": ["product_document", "code"],
            "metric_query": ["product_document"],
        }
        modalities = list(plans.get(task_type, ["product_document", "code"]))
        if task_type == "requirement_analysis" and any(
            marker in query.casefold() for marker in ("接口", "api", "入参", "出参")
        ):
            modalities.append("swagger")

        executed: list[str] = []
        skipped: list[dict[str, str]] = []
        allowed: list[str] = []
        for modality in modalities:
            reservation = ctx.context.reserve_retrieval(
                f"collect_domain_evidence:{modality}",
                query,
                max_calls,
                max_identical_queries,
            )
            if reservation == "allowed":
                allowed.append(modality)
            else:
                skipped.append({"source_type": modality, "reason": reservation})

        async def execute(modality: str) -> dict[str, Any]:
            executed.append(modality)
            try:
                if modality == "swagger":
                    operation = inspect_swagger(ctx, query)
                else:
                    operation = search_modality(ctx, query, modality)
                return await asyncio.wait_for(
                    operation, timeout=retrieval_timeout_seconds
                )
            except asyncio.TimeoutError:
                return {
                    "source_type": modality,
                    "error": "retrieval_timeout",
                    "timeout_seconds": retrieval_timeout_seconds,
                }
            except Exception:
                return {"source_type": modality, "error": "retrieval_unavailable"}

        evidence = await asyncio.gather(*(execute(item) for item in allowed))
        if task_type == "api_contract":
            has_evidence = any(
                item.get("results")
                or any(source.get("operations") for source in item.get("sources", []))
                for item in evidence
            )
            if not has_evidence:
                reservation = ctx.context.reserve_retrieval(
                    "collect_domain_evidence:product_document",
                    query,
                    max_calls,
                    max_identical_queries,
                )
                if reservation == "allowed":
                    evidence.append(await execute("product_document"))
                else:
                    skipped.append({"source_type": "product_document", "reason": reservation})

        ctx.context.finish_tool(
            call_id,
            status="completed",
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return json.dumps(
            {
                "task_type": task_type,
                "executed_retrievals": executed,
                "skipped": skipped,
                "evidence": evidence,
            },
            ensure_ascii=False,
        )

    return collect_domain_evidence
