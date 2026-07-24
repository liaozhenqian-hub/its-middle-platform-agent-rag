import logging
from types import SimpleNamespace

import pytest
from agents import RunContextWrapper
from agents.tool_context import ToolContext

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.agent_runtime.hooks import AgentLifecycleHooks


@pytest.mark.asyncio
async def test_hooks_log_usage_without_prompt_or_model_output(caplog):
    hooks = AgentLifecycleHooks()
    context = RunContextWrapper(
        AgentRunContext(conversation_id="conversation-1", run_id="run-1")
    )
    agent = SimpleNamespace(name="Manager Agent")
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=10, output_tokens=4, total_tokens=14)
    )

    with caplog.at_level(logging.INFO):
        await hooks.on_llm_start(context, agent, "secret prompt", [{"secret": "body"}])
        await hooks.on_llm_end(context, agent, response)

    assert "input_tokens=10" in caplog.text
    assert "output_tokens=4" in caplog.text
    assert "secret prompt" not in caplog.text
    assert "body" not in caplog.text
    assert len(context.context.runtime_spans) == 1
    span = context.context.runtime_spans[0]
    assert span.kind == "llm"
    assert span.input_tokens == 10
    assert span.output_tokens == 4
    assert span.total_tokens == 14


@pytest.mark.asyncio
async def test_hooks_do_not_duplicate_tools_that_record_their_real_call_id():
    hooks = AgentLifecycleHooks()
    run_context = AgentRunContext(conversation_id="conversation-1", run_id="run-1")
    context = ToolContext(
        context=run_context,
        tool_name="inspect_domain_swagger",
        tool_call_id="call-1",
        tool_arguments='{"query":"指标接口"}',
    )
    agent = SimpleNamespace(name="指标平台专家")
    tool = SimpleNamespace(name="inspect_domain_swagger")

    await hooks.on_tool_start(context, agent, tool)
    run_context.start_tool(
        "call-1",
        "inspect_domain_swagger",
        "指标平台专家",
        {"query": "指标接口"},
    )
    run_context.finish_tool("call-1", "completed", 2.5)
    await hooks.on_tool_end(context, agent, tool, "result")

    assert len(run_context.tool_runs) == 1
    assert run_context.tool_runs[0].tool_call_id == "call-1"
    assert run_context.tool_runs[0].status == "completed"


@pytest.mark.asyncio
async def test_hooks_still_audit_unknown_inspect_tools():
    hooks = AgentLifecycleHooks()
    context = RunContextWrapper(
        AgentRunContext(conversation_id="conversation-1", run_id="run-1")
    )
    agent = SimpleNamespace(name="Manager Agent")
    tool = SimpleNamespace(name="inspect_future_tool")

    await hooks.on_tool_start(context, agent, tool)
    await hooks.on_tool_end(context, agent, tool, "result")

    assert len(context.context.tool_runs) == 1
    assert context.context.tool_runs[0].tool_name == "inspect_future_tool"


@pytest.mark.asyncio
async def test_hooks_record_failed_audit_when_tool_arguments_are_invalid():
    hooks = AgentLifecycleHooks()
    run_context = AgentRunContext(conversation_id="conversation-1", run_id="run-1")
    context = ToolContext(
        context=run_context,
        tool_name="inspect_domain_swagger",
        tool_call_id="call-invalid",
        tool_arguments="{invalid-json",
    )
    agent = SimpleNamespace(name="指标平台专家")
    tool = SimpleNamespace(name="inspect_domain_swagger")

    await hooks.on_tool_start(context, agent, tool)
    await hooks.on_tool_end(
        context,
        agent,
        tool,
        "Invalid JSON input for tool inspect_domain_swagger",
    )

    assert len(run_context.tool_runs) == 1
    assert run_context.tool_runs[0].tool_call_id == "call-invalid"
    assert run_context.tool_runs[0].status == "failed"
    assert run_context.tool_runs[0].arguments == {}


@pytest.mark.asyncio
async def test_hooks_do_not_store_valid_json_before_tool_schema_validation():
    hooks = AgentLifecycleHooks()
    run_context = AgentRunContext(conversation_id="conversation-1", run_id="run-1")
    context = ToolContext(
        context=run_context,
        tool_name="inspect_domain_swagger",
        tool_call_id="call-invalid-schema",
        tool_arguments=(
            '{"query":{"url":"https://swagger.internal/private.json",'
            '"auth":"Bearer secret-token"}}'
        ),
    )
    agent = SimpleNamespace(name="指标平台专家")
    tool = SimpleNamespace(name="inspect_domain_swagger")

    await hooks.on_tool_start(context, agent, tool)
    await hooks.on_tool_end(
        context,
        agent,
        tool,
        "Invalid JSON input for tool inspect_domain_swagger",
    )

    serialized = str(run_context.to_dict())
    assert run_context.tool_runs[0].status == "failed"
    assert run_context.tool_runs[0].arguments == {}
    assert "swagger.internal" not in serialized
    assert "secret-token" not in serialized
