from __future__ import annotations

import logging
from time import perf_counter
from uuid import uuid4

from agents import RunHooks

from knowledge.agent_runtime.context import AgentRunContext, RuntimeSpan
from knowledge.agent_runtime.metric_mcp import METRIC_MCP_ALLOWED_TOOLS


logger = logging.getLogger(__name__)

class AgentLifecycleHooks(RunHooks[AgentRunContext]):
    """Log timings and usage while deliberately excluding prompts and tool payloads."""

    def __init__(self):
        self._agent_started: dict[tuple[int, str], float] = {}
        self._llm_started: dict[tuple[int, str], float] = {}
        self._tool_started: dict[tuple[int, str, str, str], tuple[str, float]] = {}

    async def on_agent_start(self, context, agent) -> None:
        self._agent_started[(id(context.context), agent.name)] = perf_counter()
        logger.info("Agent started agent=%s run_id=%s", agent.name, context.context.run_id)

    async def on_agent_end(self, context, agent, output) -> None:
        started = self._agent_started.pop((id(context.context), agent.name), perf_counter())
        duration_ms = (perf_counter() - started) * 1000
        context.context.runtime_spans.append(RuntimeSpan(
            kind="agent", name=agent.name, status="completed", duration_ms=duration_ms
        ))
        logger.info(
            "Agent completed agent=%s run_id=%s duration_ms=%.2f",
            agent.name,
            context.context.run_id,
            duration_ms,
        )

    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        self._llm_started[(id(context.context), agent.name)] = perf_counter()
        logger.info("LLM started agent=%s run_id=%s", agent.name, context.context.run_id)

    async def on_llm_end(self, context, agent, response) -> None:
        started = self._llm_started.pop((id(context.context), agent.name), perf_counter())
        usage = response.usage
        duration_ms = (perf_counter() - started) * 1000
        context.context.runtime_spans.append(RuntimeSpan(
            kind="llm",
            name=agent.name,
            status="completed",
            duration_ms=duration_ms,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
        ))
        logger.info(
            "LLM completed agent=%s run_id=%s input_tokens=%s output_tokens=%s "
            "total_tokens=%s duration_ms=%.2f",
            agent.name,
            context.context.run_id,
            getattr(usage, "input_tokens", 0),
            getattr(usage, "output_tokens", 0),
            getattr(usage, "total_tokens", 0),
            duration_ms,
        )

    async def on_tool_start(self, context, agent, tool) -> None:
        if tool.name in METRIC_MCP_ALLOWED_TOOLS:
            return
        call_id = str(
            getattr(context, "tool_call_id", "") or f"audit-{uuid4()}"
        )
        key = (id(context.context), agent.name, tool.name, call_id)
        self._tool_started[key] = (call_id, perf_counter())
        context.context.start_tool(
            call_id,
            tool.name,
            agent.name,
            {},
        )
        logger.info(
            "Tool started agent=%s tool=%s run_id=%s",
            agent.name,
            tool.name,
            context.context.run_id,
        )

    async def on_tool_end(self, context, agent, tool, result) -> None:
        if tool.name in METRIC_MCP_ALLOWED_TOOLS:
            return
        observed_call_id = str(getattr(context, "tool_call_id", "") or "")
        key = (id(context.context), agent.name, tool.name, observed_call_id)
        if not observed_call_id:
            key = next(
                (
                    item
                    for item in reversed(self._tool_started)
                    if item[:3] == (id(context.context), agent.name, tool.name)
                ),
                key,
            )
        call_id, started = self._tool_started.pop(
            key,
            (f"audit-{uuid4()}", perf_counter()),
        )
        audit = next(
            (
                item
                for item in reversed(context.context.tool_runs)
                if item.tool_call_id == call_id
            ),
            None,
        )
        if audit is None:
            context.context.start_tool(call_id, tool.name, agent.name)
            audit = context.context.tool_runs[-1]
        if audit.status == "started":
            context.context.finish_tool(
                call_id,
                status="failed" if self._is_error_result(result) else "completed",
                duration_ms=(perf_counter() - started) * 1000,
            )
        logger.info(
            "Tool completed agent=%s tool=%s run_id=%s duration_ms=%.2f",
            agent.name,
            tool.name,
            context.context.run_id,
            (perf_counter() - started) * 1000,
        )

    @staticmethod
    def _is_error_result(result) -> bool:
        if bool(getattr(result, "is_error", False)):
            return True
        if not isinstance(result, str):
            return False
        normalized = result.strip().lower()
        return normalized.startswith("invalid json input") or normalized.startswith(
            "an error occurred while running the tool"
        )
