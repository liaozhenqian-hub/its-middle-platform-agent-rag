from __future__ import annotations

import json
import inspect
from dataclasses import asdict
from time import perf_counter
from typing import Any

from agents import FunctionTool, RunContextWrapper, function_tool

from knowledge.agent_runtime.context import AgentRunContext, Citation, redact_mapping


async def run_bug_graph(
    service: Any,
    context: AgentRunContext,
    bug_report: str,
    *,
    call_id: str,
    on_diagnosis_delta: Any | None = None,
):
    report = context.current_user_message.strip() or bug_report.strip()
    context.start_tool(
        call_id,
        "bug_diagnosis_expert",
        "Manager Agent",
        {"bug_report": report},
    )
    started_at = perf_counter()
    try:
        kwargs = {
            "conversation_id": context.conversation_id,
            "run_id": context.run_id,
        }
        if "user_id" in inspect.signature(service.diagnose).parameters:
            kwargs["user_id"] = context.user_id
        if (
            on_diagnosis_delta is not None
            and "on_diagnosis_delta" in inspect.signature(service.diagnose).parameters
        ):
            kwargs["on_diagnosis_delta"] = on_diagnosis_delta
        result = await service.diagnose(report, **kwargs)
        context.response_mode = (
            "clarification"
            if result.status == "clarification_required"
            else "answer"
        )
        context.response_override = result.answer
        for item in result.citations:
            citation = Citation(
                source_type=str(item["source_type"]),
                source_id=str(item["source_id"]),
                title=str(item.get("title") or ""),
                domain=str(item.get("domain") or ""),
                metadata=redact_mapping(item.get("metadata") or {}),
            )
            if citation not in context.citations:
                context.citations.append(citation)
        context.finish_tool(
            call_id,
            status="completed",
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return result
    except Exception:
        context.finish_tool(
            call_id,
            status="failed",
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        raise


def create_bug_graph_tool(service: Any) -> FunctionTool:
    @function_tool(
        name_override="bug_diagnosis_expert",
        description_override=(
            "诊断中台 Bug。传入用户的完整问题描述；缺环境或 trace ID 时会暂停并追问，"
            "补充信息和“取消诊断”也必须继续调用本工具。"
        ),
    )
    async def bug_diagnosis_expert(
        ctx: RunContextWrapper[AgentRunContext],
        bug_report: str,
    ) -> str:
        call_id = str(getattr(ctx, "tool_call_id", "") or "bug-graph-unknown")
        result = await run_bug_graph(
            service,
            ctx.context,
            bug_report,
            call_id=call_id,
        )
        return json.dumps(asdict(result), ensure_ascii=False)

    return bug_diagnosis_expert
