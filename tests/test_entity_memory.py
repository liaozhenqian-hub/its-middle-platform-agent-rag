import json

import pytest
from agents.tool_context import ToolContext

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.memory.entities import EntityMemoryRepository
from knowledge.memory.tools import create_entity_memory_tool


@pytest.mark.asyncio
async def test_entity_memory_is_evidence_backed_branch_scoped_and_owner_isolated(tmp_path):
    repository = EntityMemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    service = await repository.upsert_entity(
        scope_type="user",
        owner_id="user-1",
        space_id="middle-platform",
        domain_id="approval-flow",
        entity_type="service",
        canonical_name="approval-service",
        branch="develop",
        environment="develop",
        aliases=("审批服务",),
    )
    endpoint = await repository.upsert_entity(
        scope_type="user",
        owner_id="user-1",
        space_id="middle-platform",
        domain_id="approval-flow",
        entity_type="endpoint",
        canonical_name="/transfer",
        branch="develop",
        environment="develop",
    )
    await repository.upsert_relation(
        source_entity_id=service.id,
        target_entity_id=endpoint.id,
        relation_type="serves_endpoint",
        summary="审批服务提供管理员转办接口",
        evidence=(("code", "code-1"),),
        confidence=0.9,
    )

    own = await repository.search(
        "审批服务 transfer",
        scope_type="user",
        owner_id="user-1",
        space_id="middle-platform",
        branch="develop",
    )
    noisy = await repository.search(
        "开发环境 管理员转办报错 approval-service",
        scope_type="user",
        owner_id="user-1",
        space_id="middle-platform",
        branch="develop",
    )
    other = await repository.search(
        "approval-service",
        scope_type="user",
        owner_id="user-2",
        space_id="middle-platform",
        branch="develop",
    )
    wrong_branch = await repository.search(
        "approval-service",
        scope_type="user",
        owner_id="user-1",
        space_id="middle-platform",
        branch="master",
    )

    assert own[0].source_name == "approval-service"
    assert own[0].target_name == "/transfer"
    assert own[0].evidence_ids == ("code-1",)
    assert noisy[0].target_name == "/transfer"
    assert other == []
    assert wrong_branch == []


@pytest.mark.asyncio
async def test_entity_memory_tool_uses_server_identity_and_environment(tmp_path):
    repository = EntityMemoryRepository(tmp_path / "memory.db")
    await repository.initialize()
    service = await repository.upsert_entity(
        scope_type="user",
        owner_id="user-1",
        space_id="middle-platform",
        domain_id="approval-flow",
        entity_type="service",
        canonical_name="approval-service",
        branch="develop",
        environment="develop",
    )
    endpoint = await repository.upsert_entity(
        scope_type="user",
        owner_id="user-1",
        space_id="middle-platform",
        domain_id="approval-flow",
        entity_type="endpoint",
        canonical_name="/transfer",
        branch="develop",
        environment="develop",
    )
    await repository.upsert_relation(
        source_entity_id=service.id,
        target_entity_id=endpoint.id,
        relation_type="serves_endpoint",
        summary="管理员转办接口",
        evidence=(("code", "code-1"),),
        confidence=0.9,
    )
    tool = create_entity_memory_tool(repository, recall_limit=5)
    context = AgentRunContext(
        "conversation-1",
        "run-1",
        user_id="user-1",
        domain_id="approval-flow",
        current_user_message="开发环境 approval-service 的 transfer 接口",
    )
    tool_context = ToolContext(
        context=context,
        tool_name=tool.name,
        tool_call_id="entity-call-1",
        tool_arguments='{"query":"transfer"}',
    )

    payload = json.loads(await tool.on_invoke_tool(tool_context, '{"query":"transfer"}'))

    assert payload["status"] == "ok"
    assert payload["relations"][0]["branch"] == "develop"
    assert payload["relations"][0]["evidence_ids"] == ["code-1"]
    assert [item.source_id for item in context.citations] == ["code-1"]
