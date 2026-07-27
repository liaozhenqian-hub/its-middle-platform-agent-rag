from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from agents import RunState, Runner
from agents.exceptions import ModelBehaviorError

from knowledge.agent_runtime.conversation_scopes import ConversationScopeRepository
from knowledge.agent_runtime.context import (
    AgentRunContext,
    ApprovalRecord,
    Citation,
    RuntimeSpan,
    ToolRun,
)
from knowledge.agent_runtime.hooks import AgentLifecycleHooks
from knowledge.agent_runtime.evidence_policy import EvidencePolicy
from knowledge.agent_runtime.pending_runs import PendingRunRepository
from knowledge.agent_runtime.public_answer import PublicAnswerStream, sanitize_public_answer
from knowledge.agent_runtime.request_guard import RequestGuard
from knowledge.agent_runtime.reasoning_synthesis import ReasoningSynthesisRequest
from knowledge.agent_runtime.sessions import AgentSessionFactory
from knowledge.bug_graph.tool import run_bug_graph


SPECIALIST_TOOL_NAMES = {
    "metric_platform_expert",
    "approval_flow_expert",
    "workflow_expert",
    "bug_diagnosis_expert",
}
SPECIALIST_DISPLAY_NAMES = {
    "metric_platform_expert": "指标平台专家",
    "approval_flow_expert": "审批流专家",
    "workflow_expert": "工作流专家",
    "bug_diagnosis_expert": "Bug 分析专家",
}
DOMAIN_SPECIALIST_DISPLAY_NAMES = {
    "metric-platform": "指标平台专家",
    "approval-flow": "审批流专家",
    "workflow": "工作流专家",
    "bug": "Bug 分析专家",
}
EVIDENCE_UNAVAILABLE_ANSWER = (
    "目前没有检索到可验证的内部证据，因此无法确认这个问题的准确结论。"
    "请补充具体环境、trace ID、接口路径、报错时间或更完整的问题现象后重试。"
)


def _public_run_error(exc: Exception) -> str:
    if isinstance(exc, ModelBehaviorError):
        return "模型返回的工具调用格式异常，请重新发送问题。"
    return "本次请求处理失败，请稍后重试。"


@dataclass
class AgentRunResponse:
    status: str
    conversation_id: str
    run_id: str
    answer: str | None
    last_agent: str
    citations: list[Citation]
    tool_runs: list[ToolRun]
    approvals: list[ApprovalRecord]
    routed_domains: list[str] = field(default_factory=list)
    specialists_used: list[str] = field(default_factory=list)
    trace_id: str | None = None
    quality_spans: list[RuntimeSpan] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentService:
    def __init__(
        self,
        manager: Any,
        model_factory: Any,
        session_factory: AgentSessionFactory,
        pending_runs: PendingRunRepository,
        max_turns: int = 12,
        runner: Any = Runner,
        hooks: AgentLifecycleHooks | None = None,
        scope_repository: ConversationScopeRepository | None = None,
        domain_managers: dict[str, Any] | None = None,
        intent_router: Any | None = None,
        intent_router_min_confidence: float = 0.75,
        public_citation_limit: int = 10,
        evidence_policy: EvidencePolicy | None = None,
        request_guard: RequestGuard | None = None,
        bug_graph_service: Any | None = None,
        memory_service: Any | None = None,
        reasoning_synthesizer: Any | None = None,
    ):
        self.manager = manager
        self.domain_managers = domain_managers or {}
        self.intent_router = intent_router
        self.intent_router_min_confidence = intent_router_min_confidence
        self.public_citation_limit = public_citation_limit
        self.evidence_policy = evidence_policy or EvidencePolicy()
        self.request_guard = request_guard or RequestGuard()
        self.bug_graph_service = bug_graph_service
        self.memory_service = memory_service
        self.reasoning_synthesizer = reasoning_synthesizer
        self.model_factory = model_factory
        self.session_factory = session_factory
        self.pending_runs = pending_runs
        self.max_turns = max_turns
        self.runner = runner
        self.hooks = hooks or AgentLifecycleHooks()
        self.scope_repository = scope_repository
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def chat(
        self,
        message: str,
        conversation_id: str | None = None,
        *,
        run_id: str | None = None,
        knowledge_space_id: str | None = None,
        domain_id: str | None = None,
        user_id: str | None = None,
        scope_provided: bool = False,
    ) -> AgentRunResponse:
        normalized_message = message.strip()
        if not normalized_message:
            raise ValueError("message cannot be blank")
        conversation_id = conversation_id or str(uuid4())
        run_id = run_id or str(uuid4())
        guard_decision = self.request_guard.evaluate(normalized_message)
        if guard_decision is not None:
            return AgentRunResponse(
                status="completed",
                conversation_id=conversation_id,
                run_id=run_id,
                answer=guard_decision.answer,
                last_agent="Manager Agent",
                citations=[],
                tool_runs=[],
                approvals=[],
                routed_domains=[],
                specialists_used=[],
            )
        lock = await self._conversation_lock(conversation_id)

        async with lock:
            knowledge_space_id, domain_id = await self._resolve_scope(
                conversation_id,
                knowledge_space_id,
                domain_id,
                scope_provided,
            )
            run_config = self.model_factory.create_run_config(conversation_id)
            context = AgentRunContext(
                conversation_id=conversation_id,
                run_id=run_id,
                knowledge_space_id=knowledge_space_id,
                domain_id=domain_id,
                user_id=user_id,
                trace_id=run_config.trace_id,
                current_user_message=normalized_message,
            )
            selected_manager = await self._select_manager(
                normalized_message, domain_id, context
            )
            if await self._should_run_bug_graph_direct(conversation_id, context):
                return await self._run_bug_graph_direct(normalized_message, context)
            session = self.session_factory.create(conversation_id)
            model_input = await self._memory_augmented_message(
                normalized_message, context
            )
            try:
                result = await self.runner.run(
                    selected_manager,
                    model_input,
                    context=context,
                    max_turns=self.max_turns,
                    hooks=self.hooks,
                    run_config=run_config,
                    session=session,
                )
                return await self._response_from_result(result, context, run_id)
            finally:
                session.close()

    async def decide(
        self,
        run_id: str,
        decisions: list[dict[str, str]],
    ) -> AgentRunResponse:
        first_read = await self.pending_runs.get_pending(run_id)
        lock = await self._conversation_lock(first_read.conversation_id)
        async with lock:
            pending = await self.pending_runs.get_pending(run_id)
            state = await RunState.from_json(
                self.manager,
                pending.state,
                context_deserializer=AgentRunContext.from_dict,
                strict_context=True,
            )
            interruptions = state.get_interruptions()
            decisions_by_id = {
                item["tool_call_id"]: item["decision"] for item in decisions
            }
            expected_ids = {self._approval_call_id(item) for item in interruptions}
            if set(decisions_by_id) != expected_ids:
                raise ValueError("a decision is required for every pending tool call")
            for interruption in interruptions:
                decision = decisions_by_id[self._approval_call_id(interruption)]
                if decision == "approve":
                    state.approve(interruption)
                elif decision == "reject":
                    state.reject(interruption)
                else:
                    raise ValueError("decision must be approve or reject")

            session = self.session_factory.create(pending.conversation_id)
            run_config = self.model_factory.create_run_config(pending.conversation_id)
            try:
                result = await self.runner.run(
                    self.manager,
                    state,
                    max_turns=self.max_turns,
                    hooks=self.hooks,
                    run_config=run_config,
                    session=session,
                )
                context = result.context_wrapper.context
                response = await self._response_from_result(result, context, run_id)
                if response.status == "completed":
                    await self.pending_runs.mark_completed(run_id)
                return response
            finally:
                session.close()

    async def stream_chat(
        self,
        message: str,
        conversation_id: str | None = None,
        *,
        run_id: str | None = None,
        knowledge_space_id: str | None = None,
        domain_id: str | None = None,
        user_id: str | None = None,
        scope_provided: bool = False,
    ):
        normalized_message = message.strip()
        if not normalized_message:
            raise ValueError("message cannot be blank")
        conversation_id = conversation_id or str(uuid4())
        run_id = run_id or str(uuid4())
        guard_decision = self.request_guard.evaluate(normalized_message)
        if guard_decision is not None:
            response = AgentRunResponse(
                status="completed",
                conversation_id=conversation_id,
                run_id=run_id,
                answer=guard_decision.answer,
                last_agent="Manager Agent",
                citations=[],
                tool_runs=[],
                approvals=[],
                routed_domains=[],
                specialists_used=[],
            )
            yield {
                "event": "run.started",
                "data": {
                    "conversation_id": conversation_id,
                    "run_id": run_id,
                    "trace_id": None,
                },
            }
            yield {"event": "text.delta", "data": {"delta": guard_decision.answer}}
            yield {"event": "run.completed", "data": response.to_dict()}
            return
        lock = await self._conversation_lock(conversation_id)

        async with lock:
            knowledge_space_id, domain_id = await self._resolve_scope(
                conversation_id,
                knowledge_space_id,
                domain_id,
                scope_provided,
            )
            run_config = self.model_factory.create_run_config(conversation_id)
            context = AgentRunContext(
                conversation_id=conversation_id,
                run_id=run_id,
                knowledge_space_id=knowledge_space_id,
                domain_id=domain_id,
                user_id=user_id,
                trace_id=run_config.trace_id,
                current_user_message=normalized_message,
            )
            selected_manager = await self._select_manager(
                normalized_message, domain_id, context
            )
            if await self._should_run_bug_graph_direct(conversation_id, context):
                yield {
                    "event": "run.started",
                    "data": {
                        "conversation_id": conversation_id,
                        "run_id": run_id,
                        "trace_id": context.trace_id,
                    },
                }
                yield {
                    "event": "agent.updated",
                    "data": {"agent": "Bug 分析专家"},
                }
                call_id = f"bug-direct-{run_id}"
                yield {
                    "event": "tool.started",
                    "data": {
                        "tool_call_id": call_id,
                        "tool_name": "bug_diagnosis_expert",
                    },
                }
                diagnosis_task: asyncio.Task[AgentRunResponse] | None = None
                try:
                    diagnosis_deltas: asyncio.Queue[str] = asyncio.Queue()

                    async def collect_diagnosis_delta(delta: str) -> None:
                        await diagnosis_deltas.put(delta)

                    diagnosis_task = asyncio.create_task(
                        self._run_bug_graph_direct(
                            normalized_message,
                            context,
                            call_id=call_id,
                            on_diagnosis_delta=collect_diagnosis_delta,
                        )
                    )
                    answer_stream = PublicAnswerStream(
                        lambda: context.public_citations(self.public_citation_limit)
                    )
                    streamed_diagnosis = False
                    while not diagnosis_task.done() or not diagnosis_deltas.empty():
                        try:
                            delta = await asyncio.wait_for(
                                diagnosis_deltas.get(), timeout=0.05
                            )
                        except TimeoutError:
                            continue
                        streamed_diagnosis = True
                        public_delta = answer_stream.feed(delta)
                        if public_delta:
                            yield {
                                "event": "text.delta",
                                "data": {"delta": public_delta},
                            }
                    response = await diagnosis_task
                    if streamed_diagnosis:
                        final_delta = answer_stream.flush()
                        if final_delta:
                            yield {
                                "event": "text.delta",
                                "data": {"delta": final_delta},
                            }
                    yield {
                        "event": "tool.completed",
                        "data": {
                            "tool_call_id": call_id,
                            "tool_name": "bug_diagnosis_expert",
                        },
                    }
                    if not streamed_diagnosis:
                        yield {
                            "event": "text.delta",
                            "data": {"delta": response.answer or ""},
                        }
                    yield {"event": "run.completed", "data": response.to_dict()}
                except asyncio.CancelledError:
                    if diagnosis_task is not None and not diagnosis_task.done():
                        diagnosis_task.cancel()
                        await asyncio.gather(diagnosis_task, return_exceptions=True)
                    raise
                except Exception as exc:
                    yield {
                        "event": "run.error",
                        "data": {
                            "conversation_id": conversation_id,
                            "run_id": run_id,
                            "error": _public_run_error(exc),
                            "error_type": type(exc).__name__,
                        },
                    }
                return
            session = self.session_factory.create(conversation_id)
            model_input = await self._memory_augmented_message(
                normalized_message, context
            )
            streamed = None
            tool_names: dict[str, str] = {}
            buffered_manager_deltas: list[str] = []
            public_answer_stream = PublicAnswerStream(
                lambda: context.public_citations(self.public_citation_limit)
            )
            active_specialist_calls: set[str] = set()
            specialist_invoked = False
            manager_deltas_streamed = False
            yield {
                "event": "run.started",
                "data": {
                    "conversation_id": conversation_id,
                    "run_id": run_id,
                    "trace_id": context.trace_id,
                },
            }
            try:
                streamed = self.runner.run_streamed(
                    selected_manager,
                    model_input,
                    context=context,
                    max_turns=self.max_turns,
                    hooks=self.hooks,
                    run_config=run_config,
                    session=session,
                )
                async for event in streamed.stream_events():
                    if event.type == "agent_updated_stream_event":
                        yield {
                            "event": "agent.updated",
                            "data": {"agent": event.new_agent.name},
                        }
                        continue
                    if event.type == "raw_response_event":
                        if getattr(event.data, "type", "") == "response.output_text.delta":
                            delta = str(event.data.delta)
                            if active_specialist_calls:
                                # Agent-as-tool output is internal evidence, not public prose.
                                continue
                            if specialist_invoked or self._specialist_invoked(context):
                                buffered_manager_deltas.append(delta)
                                if (
                                    self._has_evidence(context)
                                    and context.response_override is None
                                    and not self._bug_graph_invoked(context)
                                ):
                                    manager_deltas_streamed = True
                                    public_delta = public_answer_stream.feed(delta)
                                    if public_delta:
                                        yield {
                                            "event": "text.delta",
                                            "data": {"delta": public_delta},
                                        }
                            else:
                                public_delta = public_answer_stream.feed(delta)
                                if public_delta:
                                    yield {
                                        "event": "text.delta",
                                        "data": {"delta": public_delta},
                                    }
                        continue
                    if event.type != "run_item_stream_event":
                        continue
                    if event.name == "tool_called":
                        raw = event.item.raw_item
                        call_id = self._raw_call_id(raw)
                        tool_name = self._raw_tool_name(raw)
                        tool_names[call_id] = tool_name
                        if tool_name in SPECIALIST_TOOL_NAMES:
                            specialist_invoked = True
                            active_specialist_calls.add(call_id)
                            if not any(
                                item.tool_call_id == call_id
                                for item in context.tool_runs
                            ):
                                context.start_tool(
                                    call_id,
                                    tool_name,
                                    "Manager Agent",
                                )
                        yield {
                            "event": "tool.started",
                            "data": {
                                "tool_call_id": call_id,
                                "tool_name": tool_name,
                            },
                        }
                    elif event.name == "tool_output":
                        call_id = self._raw_call_id(event.item.raw_item)
                        active_specialist_calls.discard(call_id)
                        yield {
                            "event": "tool.completed",
                            "data": {
                                "tool_call_id": call_id,
                                "tool_name": tool_names.get(call_id, "unknown"),
                            },
                        }

                response = await self._response_from_result(streamed, context, run_id)
                if response.status == "completed" and specialist_invoked:
                    if (
                        context.response_override is not None
                        or response.answer != str(streamed.final_output)
                    ):
                        if not manager_deltas_streamed:
                            public_delta = public_answer_stream.feed(response.answer or "")
                            if public_delta:
                                yield {
                                    "event": "text.delta",
                                    "data": {"delta": public_delta},
                                }
                    elif response.answer == EVIDENCE_UNAVAILABLE_ANSWER:
                        yield {
                            "event": "text.delta",
                            "data": {"delta": EVIDENCE_UNAVAILABLE_ANSWER},
                        }
                    elif not manager_deltas_streamed:
                        for delta in buffered_manager_deltas:
                            public_delta = public_answer_stream.feed(delta)
                            if public_delta:
                                yield {
                                    "event": "text.delta",
                                    "data": {"delta": public_delta},
                                }
                final_delta = public_answer_stream.flush()
                if final_delta:
                    yield {"event": "text.delta", "data": {"delta": final_delta}}
                event_name = (
                    "approval.required"
                    if response.status == "approval_required"
                    else "run.completed"
                )
                yield {"event": event_name, "data": response.to_dict()}
            except asyncio.CancelledError:
                if streamed is not None:
                    streamed.cancel()
                raise
            except Exception as exc:
                yield {
                    "event": "run.error",
                    "data": {
                        "conversation_id": conversation_id,
                        "run_id": run_id,
                        "error": _public_run_error(exc),
                        "error_type": type(exc).__name__,
                    },
                }
            finally:
                session.close()

    async def delete_conversation(self, conversation_id: str) -> None:
        lock = await self._conversation_lock(conversation_id)
        async with lock:
            session = self.session_factory.create(conversation_id)
            try:
                await session.clear_session()
                await self.pending_runs.delete_conversation(conversation_id)
                if self.bug_graph_service is not None:
                    await self.bug_graph_service.cancel(conversation_id)
                if self.scope_repository is not None:
                    await self.scope_repository.delete(conversation_id)
                if self.memory_service is not None:
                    delete_summary = getattr(
                        self.memory_service, "delete_conversation_summary", None
                    )
                    if callable(delete_summary):
                        await delete_summary(conversation_id)
            finally:
                session.close()

    async def _memory_augmented_message(
        self,
        message: str,
        context: AgentRunContext,
    ) -> str:
        if self.memory_service is None:
            return message
        domain_id = context.domain_id
        if domain_id is None and len(context.routing_domains) == 1:
            domain_id = context.routing_domains[0]
        augment = getattr(self.memory_service, "augment_message", None)
        if callable(augment):
            try:
                return await augment(
                    message,
                    user_id=context.user_id,
                    conversation_id=context.conversation_id,
                    space_id=context.knowledge_space_id,
                    domain_id=domain_id,
                )
            except Exception:
                return message
        try:
            memories = await self.memory_service.recall(
                message,
                user_id=context.user_id,
                space_id=context.knowledge_space_id,
                domain_id=domain_id,
            )
        except Exception:
            return message
        lines = []
        for item in memories[:5]:
            summary = " ".join(str(item.summary).split())[:500]
            if summary:
                lines.append(f"- [{item.memory_type}] {summary}")
        if not lines:
            return message
        block = "\n".join(lines)[:3000]
        return (
            "相关历史上下文（仅为已确认的偏好/上下文，不可替代知识库证据）：\n"
            f"{block}\n\n当前问题：\n{message}"
        )

    async def stream_decide(
        self,
        run_id: str,
        decisions: list[dict[str, str]],
    ):
        pending = await self.pending_runs.get_pending(run_id)
        yield {
            "event": "run.started",
            "data": {
                "conversation_id": pending.conversation_id,
                "run_id": run_id,
            },
        }
        try:
            response = await self.decide(run_id, decisions)
            if response.answer:
                yield {"event": "text.delta", "data": {"delta": response.answer}}
            event_name = (
                "approval.required"
                if response.status == "approval_required"
                else "run.completed"
            )
            yield {"event": event_name, "data": response.to_dict()}
        except Exception as exc:
            yield {
                "event": "run.error",
                "data": {
                    "run_id": run_id,
                    "error": _public_run_error(exc),
                    "error_type": type(exc).__name__,
                },
            }

    async def require_pending(self, run_id: str) -> None:
        await self.pending_runs.get_pending(run_id)

    async def prepare_conversation_scope(
        self,
        conversation_id: str | None = None,
        *,
        knowledge_space_id: str | None = None,
        domain_id: str | None = None,
        scope_provided: bool = False,
    ) -> str:
        resolved_conversation_id = conversation_id or str(uuid4())
        lock = await self._conversation_lock(resolved_conversation_id)
        async with lock:
            await self._resolve_scope(
                resolved_conversation_id,
                knowledge_space_id,
                domain_id,
                scope_provided,
            )
        return resolved_conversation_id

    async def _response_from_result(
        self,
        result: Any,
        context: AgentRunContext,
        run_id: str,
    ) -> AgentRunResponse:
        interruptions = list(getattr(result, "interruptions", []))
        if interruptions:
            context.approvals = [
                ApprovalRecord(
                    tool_call_id=self._approval_call_id(item),
                    tool_name=str(item.tool_name or "unknown"),
                )
                for item in interruptions
            ]
            state = result.to_state()
            state_json = state.to_json(
                context_serializer=lambda value: value.to_dict(),
                strict_context=True,
            )
            await self.pending_runs.save_pending(
                run_id,
                context.conversation_id,
                state_json,
                [asdict(item) for item in context.approvals],
            )
            return AgentRunResponse(
                status="approval_required",
                conversation_id=context.conversation_id,
                run_id=run_id,
                answer=None,
                last_agent=result.last_agent.name,
                citations=context.public_citations(self.public_citation_limit),
                tool_runs=context.tool_runs,
                approvals=context.approvals,
                routed_domains=list(context.routing_domains),
                specialists_used=self._specialists_used(context),
                trace_id=context.trace_id,
                quality_spans=list(context.runtime_spans),
            )

        citations = context.public_citations(self.public_citation_limit)
        answer = str(result.final_output)
        if context.response_override is not None:
            answer = context.response_override
        elif (
            self._specialist_invoked(context)
            and context.response_mode != "clarification"
            and not self._has_evidence(context)
        ):
            answer = EVIDENCE_UNAVAILABLE_ANSWER
        else:
            if self._should_synthesize(context):
                answer = await self._synthesize_answer(answer, citations, context)
            if (
                self._specialist_invoked(context)
                and not self._bug_graph_invoked(context)
                and context.response_mode != "clarification"
            ):
                answer = self.evidence_policy.safeguard(answer, context.citations)
        answer = sanitize_public_answer(answer, citations)
        return AgentRunResponse(
            status="completed",
            conversation_id=context.conversation_id,
            run_id=run_id,
            answer=answer,
            last_agent=result.last_agent.name,
            citations=citations,
            tool_runs=context.tool_runs,
            approvals=context.approvals,
            routed_domains=list(context.routing_domains),
            specialists_used=self._specialists_used(context),
            trace_id=context.trace_id,
            quality_spans=list(context.runtime_spans),
        )

    async def _conversation_lock(self, conversation_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(conversation_id, asyncio.Lock())

    async def _should_run_bug_graph_direct(
        self,
        conversation_id: str,
        context: AgentRunContext,
    ) -> bool:
        if self.bug_graph_service is None:
            return False
        if context.routing_intent == "bug":
            return True
        if (
            await self.bug_graph_service.has_pending(conversation_id)
            and self._is_pending_bug_supplement(
                context.current_user_message, context.routing_intent
            )
        ):
            context.routing_domains = ["bug"]
            context.routing_intent = "bug"
            return True
        if await self.bug_graph_service.should_resume(
            conversation_id,
            context.current_user_message,
        ):
            context.routing_domains = ["bug"]
            context.routing_intent = "bug"
            return True
        return False

    @staticmethod
    def _is_pending_bug_supplement(message: str, routing_intent: str) -> bool:
        normalized = message.strip().casefold()
        if normalized == "取消诊断":
            return True
        if routing_intent in {"approval-flow", "workflow", "metric-platform", "cross-domain"}:
            return False
        if any(marker in normalized for marker in (
            "traceid", "trace id", "curl ", "http://", "https://",
            "exception", "stacktrace", "报错", "错误码", "超时",
            "继续", "上文", "刚才", "再查",
        )):
            return True
        compact = re.sub(r"[\s，。！？,:：;；]+", "", normalized)
        if compact in {
            "开发", "开发环境", "develop", "dev",
            "测试", "测试环境", "test",
            "线上", "线上环境", "生产", "生产环境", "prod", "production",
        }:
            return True
        if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", compact):
            return True
        return bool(re.search(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}:\d{2}(?::\d{2})?\b", normalized))

    async def _run_bug_graph_direct(
        self,
        message: str,
        context: AgentRunContext,
        *,
        call_id: str | None = None,
        on_diagnosis_delta: Any | None = None,
    ) -> AgentRunResponse:
        await run_bug_graph(
            self.bug_graph_service,
            context,
            message,
            call_id=call_id or f"bug-direct-{context.run_id}",
            on_diagnosis_delta=on_diagnosis_delta,
        )
        citations = context.public_citations(self.public_citation_limit)
        return AgentRunResponse(
            status="completed",
            conversation_id=context.conversation_id,
            run_id=context.run_id,
            answer=sanitize_public_answer(context.response_override, citations),
            last_agent="Bug 分析专家",
            citations=citations,
            tool_runs=context.tool_runs,
            approvals=context.approvals,
            routed_domains=["bug"],
            specialists_used=["Bug 分析专家"],
            trace_id=context.trace_id,
            quality_spans=list(context.runtime_spans),
        )

    async def _select_manager(
        self,
        message: str,
        explicit_domain_id: str | None,
        context: AgentRunContext,
    ) -> Any:
        if explicit_domain_id and explicit_domain_id in self.domain_managers:
            context.routing_domains = [explicit_domain_id]
            context.routing_intent = explicit_domain_id
            if self.intent_router is not None:
                context.task_type = (
                    await self._route_intent(message, rules_only=True)
                ).task_type
            return self.domain_managers[explicit_domain_id]
        if self.intent_router is None:
            return self.manager
        decision = await self._route_intent(message)
        context.routing_domains = list(decision.domains)
        context.routing_intent = decision.intent
        context.task_type = decision.task_type
        if decision.route_source == "flash_fallback":
            context.runtime_spans.append(RuntimeSpan(
                kind="tool",
                name="routing.flash_fallback",
                status="completed",
                duration_ms=decision.duration_ms,
            ))
        if (
            len(decision.domains) == 1
            and not decision.needs_clarification
            and decision.confidence >= self.intent_router_min_confidence
        ):
            return self.domain_managers.get(decision.domains[0], self.manager)
        return self.manager

    async def _route_intent(
        self, message: str, *, rules_only: bool = False
    ) -> Any:
        route = (
            getattr(self.intent_router, "route_rules")
            if rules_only and hasattr(self.intent_router, "route_rules")
            else self.intent_router.route
        )
        decision = route(message)
        if asyncio.iscoroutine(decision):
            decision = await decision
        return decision

    async def _resolve_scope(
        self,
        conversation_id: str,
        knowledge_space_id: str | None,
        domain_id: str | None,
        scope_provided: bool,
    ) -> tuple[str, str | None]:
        default_space_id = "middle-platform"
        if self.scope_repository is None:
            return knowledge_space_id or default_space_id, domain_id

        existing = await self.scope_repository.get(conversation_id)
        if existing is not None and not scope_provided:
            return existing.knowledge_space_id, existing.domain_id

        scope = await self.scope_repository.bind(
            conversation_id,
            knowledge_space_id or default_space_id,
            domain_id,
        )
        return scope.knowledge_space_id, scope.domain_id

    @staticmethod
    def _approval_call_id(item: Any) -> str:
        raw = item.raw_item
        if isinstance(raw, dict):
            return str(raw.get("call_id") or raw.get("id") or "")
        return str(getattr(raw, "call_id", None) or getattr(raw, "id", ""))

    @staticmethod
    def _raw_call_id(raw: Any) -> str:
        if isinstance(raw, dict):
            return str(raw.get("call_id") or raw.get("id") or "")
        return str(getattr(raw, "call_id", None) or getattr(raw, "id", ""))

    @staticmethod
    def _raw_tool_name(raw: Any) -> str:
        if isinstance(raw, dict):
            return str(raw.get("name") or "unknown")
        return str(getattr(raw, "name", "unknown"))

    @staticmethod
    def _specialist_invoked(context: AgentRunContext) -> bool:
        return context.routing_intent in DOMAIN_SPECIALIST_DISPLAY_NAMES or any(
            tool_run.tool_name in SPECIALIST_TOOL_NAMES
            for tool_run in context.tool_runs
        )

    @staticmethod
    def _bug_graph_invoked(context: AgentRunContext) -> bool:
        return any(
            tool_run.tool_name == "bug_diagnosis_expert"
            for tool_run in context.tool_runs
        )

    @staticmethod
    def _specialists_used(context: AgentRunContext) -> list[str]:
        specialists: list[str] = []
        for tool_run in context.tool_runs:
            display_name = SPECIALIST_DISPLAY_NAMES.get(tool_run.tool_name)
            if display_name and display_name not in specialists:
                specialists.append(display_name)
        if not specialists:
            display_name = DOMAIN_SPECIALIST_DISPLAY_NAMES.get(context.routing_intent)
            if display_name:
                specialists.append(display_name)
        return specialists

    @staticmethod
    def _has_evidence(context: AgentRunContext) -> bool:
        for citation in context.citations:
            if citation.source_type != "log_trace":
                return True
            if int(citation.metadata.get("log_count") or 0) > 0:
                return True
        return False

    def _should_synthesize(self, context: AgentRunContext) -> bool:
        return bool(
            self.reasoning_synthesizer is not None
            and context.response_mode == "answer"
            and context.response_override is None
            and not self._bug_graph_invoked(context)
            and self._has_evidence(context)
            and (
                len(set(context.routing_domains)) > 1
                or len(self._specialists_used(context)) > 1
            )
        )

    async def _synthesize_answer(
        self,
        draft: str,
        citations: list[Citation],
        context: AgentRunContext,
    ) -> str:
        started = asyncio.get_running_loop().time()
        status = "completed"
        try:
            return await self.reasoning_synthesizer.synthesize(
                ReasoningSynthesisRequest(
                    question=context.current_user_message,
                    draft=draft,
                    domains=tuple(context.routing_domains),
                    citations=tuple(citations),
                    conversation_id=context.conversation_id,
                )
            )
        except TimeoutError:
            status = "timeout"
            return draft
        except Exception:
            status = "failed"
            return draft
        finally:
            context.runtime_spans.append(
                RuntimeSpan(
                    kind="llm",
                    name="manager.reasoning_synthesis",
                    status=status,
                    duration_ms=(
                        asyncio.get_running_loop().time() - started
                    )
                    * 1000,
                )
            )
