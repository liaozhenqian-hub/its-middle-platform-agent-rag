from __future__ import annotations

import json
import secrets
from typing import Any

from agents import FunctionTool, RunContextWrapper, function_tool

from knowledge.agent_runtime.context import AgentRunContext
from knowledge.agent_runtime.metric_mcp import MetricMCPResultCache


class MetricQueryGuard:
    _CONFIRMATION_MARKERS = ("确认", "选择", "选用", "使用", "第一个", "第二个", "选a", "选b")

    def __init__(self, server: Any, cache: MetricMCPResultCache | None = None):
        self.server = server
        self.cache = cache or MetricMCPResultCache()

    def prepare(self, context: AgentRunContext, *, selected_app: str) -> dict[str, Any]:
        message = self._normalize(context.current_user_message)
        app = self._normalize(selected_app)
        explicitly_confirmed = bool(app) and app in message and any(
            marker in message for marker in self._CONFIRMATION_MARKERS
        )
        if not explicitly_confirmed:
            context.response_mode = "clarification"
            context.metric_query_stage = "awaiting_app_confirmation"
            return {
                "status": "clarification_required",
                "message": "请先向用户展示指标应用候选，并让用户明确选择应用后再查询。",
            }
        token = secrets.token_urlsafe(12)
        context.metric_confirmation_token = token
        context.metric_confirmed_app = selected_app.strip()
        context.metric_query_stage = "confirmed"
        return {
            "status": "confirmed",
            "selected_app": context.metric_confirmed_app,
            "confirmation_token": token,
        }

    async def query_data(
        self,
        context: AgentRunContext,
        *,
        req: dict[str, Any],
        limit: int,
        confirmation_token: str,
    ) -> dict[str, Any]:
        if not self._valid_token(context, confirmation_token):
            context.response_mode = "clarification"
            return {
                "status": "clarification_required",
                "message": "指标应用尚未由用户明确确认，已阻止数据查询。",
            }
        scope = self._scope(context)
        arguments = {"req": req, "limit": limit}
        cached = self.cache.get(*scope, "searchMetricAppQueryResult", arguments)
        if cached is not None:
            context.metric_query_stage = "completed"
            return {
                "status": "completed", "tool": "searchMetricAppQueryResult",
                "result": cached, "cache_hit": True,
            }
        raw = await self.server.call_tool("searchMetricAppQueryResult", arguments)
        result = self._public_result(raw)
        self.cache.put(*scope, "searchMetricAppQueryResult", arguments, result)
        context.metric_query_stage = "completed"
        return {
            "status": "completed",
            "tool": "searchMetricAppQueryResult",
            "result": result,
            "cache_hit": False,
        }

    async def query_sql(
        self,
        context: AgentRunContext,
        *,
        metric_type: str,
        name: str,
        confirmation_token: str,
        fuzzy: bool = False,
    ) -> dict[str, Any]:
        if not self._valid_token(context, confirmation_token):
            context.response_mode = "clarification"
            return {
                "status": "clarification_required",
                "message": "指标及应用尚未由用户明确确认，已阻止 SQL 查询。",
            }
        tool_name = (
            "searchSqlByMetricTypeAndNameFuzzy"
            if fuzzy
            else "searchSqlByMetricTypeAndNameExact"
        )
        scope = self._scope(context)
        arguments = {"metricType": metric_type, "name": name}
        cached = self.cache.get(*scope, tool_name, arguments)
        if cached is not None:
            context.metric_query_stage = "completed"
            return {"status": "completed", "tool": tool_name, "result": cached, "cache_hit": True}
        raw = await self.server.call_tool(tool_name, arguments)
        result = self._public_result(raw)
        self.cache.put(*scope, tool_name, arguments, result)
        context.metric_query_stage = "completed"
        return {
            "status": "completed",
            "tool": tool_name,
            "result": result,
            "cache_hit": False,
        }

    @staticmethod
    def _valid_token(context: AgentRunContext, token: str) -> bool:
        return bool(token) and secrets.compare_digest(
            token,
            context.metric_confirmation_token or "",
        )

    @staticmethod
    def _scope(context: AgentRunContext) -> tuple[str, str]:
        return (context.user_id or "anonymous", context.conversation_id)

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(value.casefold().split())

    @staticmethod
    def _public_result(result: Any) -> Any:
        payloads: list[Any] = []
        for item in list(getattr(result, "content", None) or []):
            text = str(getattr(item, "text", "") or "")
            if not text:
                continue
            try:
                payloads.append(json.loads(text))
            except json.JSONDecodeError:
                payloads.append(text[:6000])
        if len(payloads) == 1:
            return payloads[0]
        return payloads


def create_metric_gateway_tools(server: Any) -> list[FunctionTool]:
    guard = MetricQueryGuard(server)

    @function_tool(
        name_override="prepare_metric_query",
        description_override=(
            "用户明确选择指标应用后调用，生成本轮数据或SQL查询凭证。"
            "用户未明确选择时返回 clarification_required。"
        ),
    )
    async def prepare_metric_query(
        ctx: RunContextWrapper[AgentRunContext],
        selected_app: str,
    ) -> str:
        return json.dumps(
            guard.prepare(ctx.context, selected_app=selected_app),
            ensure_ascii=False,
        )

    @function_tool(
        name_override="query_metric_data_guarded",
        description_override="使用确认凭证查询指标数据；不能在用户确认应用前调用。",
    )
    async def query_metric_data_guarded(
        ctx: RunContextWrapper[AgentRunContext],
        req_json: str,
        limit: int,
        confirmation_token: str,
    ) -> str:
        try:
            req = json.loads(req_json)
        except json.JSONDecodeError:
            return json.dumps(
                {"status": "invalid_request", "message": "req_json 必须是合法 JSON 对象"},
                ensure_ascii=False,
            )
        if not isinstance(req, dict):
            return json.dumps(
                {"status": "invalid_request", "message": "req_json 必须是 JSON 对象"},
                ensure_ascii=False,
            )
        result = await guard.query_data(
            ctx.context,
            req=req,
            limit=limit,
            confirmation_token=confirmation_token,
        )
        if result["status"] == "completed":
            ctx.context.add_mcp_citation("searchMetricAppQueryResult")
        return json.dumps(result, ensure_ascii=False)

    @function_tool(
        name_override="query_metric_sql_guarded",
        description_override="使用确认凭证查询指标SQL；不能在用户确认指标和应用前调用。",
    )
    async def query_metric_sql_guarded(
        ctx: RunContextWrapper[AgentRunContext],
        metric_type: str,
        name: str,
        confirmation_token: str,
        fuzzy: bool = False,
    ) -> str:
        result = await guard.query_sql(
            ctx.context,
            metric_type=metric_type,
            name=name,
            confirmation_token=confirmation_token,
            fuzzy=fuzzy,
        )
        if result["status"] == "completed":
            ctx.context.add_mcp_citation(str(result["tool"]))
        return json.dumps(result, ensure_ascii=False)

    return [prepare_metric_query, query_metric_data_guarded, query_metric_sql_guarded]
