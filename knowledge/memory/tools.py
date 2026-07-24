from __future__ import annotations

import json
from typing import Any

from agents import FunctionTool, RunContextWrapper, function_tool

from knowledge.agent_runtime.context import AgentRunContext, Citation
from knowledge.memory.service import MemoryService
from knowledge.memory.entities import EntityMemoryRepository


def _public_memory(item: Any) -> dict[str, Any]:
    return {
        "memory_id": item.id,
        "memory_type": item.memory_type,
        "subject": item.subject,
        "summary": item.summary[:1000],
        "confidence": item.confidence,
        "source_turn_id": item.source_turn_id,
        "source_citation_ids": list(item.source_citations),
    }


def create_memory_tools(service: MemoryService) -> tuple[FunctionTool, FunctionTool]:
    @function_tool(
        name_override="search_user_memory",
        description_override=(
            "检索当前登录用户已确认的长期记忆。只能传入问题，用户身份、空间和领域由服务端提供。"
        ),
    )
    async def search_user_memory(
        ctx: RunContextWrapper[AgentRunContext], query: str
    ) -> str:
        context = ctx.context
        if not context.user_id:
            return json.dumps({"status": "identity_required", "memories": []}, ensure_ascii=False)
        memories = await service.recall(
            query,
            user_id=context.user_id,
            space_id=context.knowledge_space_id,
            domain_id=context.domain_id,
        )
        return json.dumps(
            {"status": "ok", "memories": [_public_memory(item) for item in memories]},
            ensure_ascii=False,
        )

    @function_tool(
        name_override="search_domain_memory",
        description_override=(
            "检索当前领域已确认的团队记忆。只能传入问题，领域和空间由服务端固定。"
        ),
    )
    async def search_domain_memory(
        ctx: RunContextWrapper[AgentRunContext], query: str
    ) -> str:
        context = ctx.context
        if not context.domain_id:
            return json.dumps({"status": "domain_required", "memories": []}, ensure_ascii=False)
        memories = await service.recall(
            query,
            user_id=None,
            space_id=context.knowledge_space_id,
            domain_id=context.domain_id,
        )
        return json.dumps(
            {"status": "ok", "memories": [_public_memory(item) for item in memories]},
            ensure_ascii=False,
        )

    return search_user_memory, search_domain_memory


def create_entity_memory_tool(
    repository: EntityMemoryRepository, *, recall_limit: int = 5
) -> FunctionTool:
    @function_tool(
        name_override="search_entity_memory",
        description_override=(
            "检索当前用户已确认 Bug 事件形成的服务、接口和代码证据关系。"
            "身份、领域、环境和分支由服务端从当前会话固定，不能由模型指定。"
        ),
    )
    async def search_entity_memory(
        ctx: RunContextWrapper[AgentRunContext], query: str
    ) -> str:
        context = ctx.context
        if not context.user_id:
            return json.dumps({"status": "identity_required", "relations": []}, ensure_ascii=False)
        environment, branch = _environment_branch(context.current_user_message)
        relations = await repository.search(
            query,
            scope_type="user",
            owner_id=context.user_id,
            space_id=context.knowledge_space_id,
            domain_id=context.domain_id,
            branch=branch,
            environment=environment,
            limit=recall_limit,
        )
        for relation in relations:
            for source_type, source_id in relation.evidence_refs:
                if source_type not in {
                    "code", "product_document", "knowledge_chunk", "swagger"
                }:
                    continue
                citation = Citation(
                    source_type=source_type,
                    source_id=source_id,
                    title=relation.summary,
                    domain=context.domain_id or "中台",
                    metadata={"entity_relation": relation.relation_type},
                )
                if citation not in context.citations:
                    context.citations.append(citation)
        return json.dumps(
            {
                "status": "ok",
                "relations": [
                    {
                        "relation_type": item.relation_type,
                        "source": item.source_name,
                        "source_type": item.source_type,
                        "target": item.target_name,
                        "target_type": item.target_type,
                        "summary": item.summary,
                        "branch": item.branch,
                        "environment": item.environment,
                        "confidence": item.confidence,
                        "evidence_ids": list(item.evidence_ids),
                    }
                    for item in relations
                ],
            },
            ensure_ascii=False,
        )

    return search_entity_memory


def _environment_branch(message: str) -> tuple[str | None, str | None]:
    normalized = message.casefold()
    if any(marker in normalized for marker in ("线上", "生产", "prod", "production")):
        return "prod", "master"
    if any(marker in normalized for marker in ("测试", "test")):
        return "test", "develop"
    if any(marker in normalized for marker in ("开发", "develop", " dev ")):
        return "develop", "develop"
    return None, None
