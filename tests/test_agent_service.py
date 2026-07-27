import asyncio
from types import SimpleNamespace

import pytest
from agents.exceptions import ModelBehaviorError
from agents import RunConfig

from knowledge.agent_runtime.conversation_scopes import (
    ConversationScopeConflictError,
    ConversationScopeRepository,
)
from knowledge.agent_runtime.pending_runs import PendingRunRepository
from knowledge.agent_runtime.request_guard import RequestGuard
from knowledge.agent_runtime.service import AgentService, EVIDENCE_UNAVAILABLE_ANSWER
from knowledge.agent_runtime.sessions import AgentSessionFactory
from knowledge.bug_graph.service import BugDiagnosisResult


class FakeResult:
    def __init__(self, answer="完成"):
        self.final_output = answer
        self.last_agent = SimpleNamespace(name="Manager Agent")
        self.interruptions = []


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def run(self, agent, input, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append((agent, input, kwargs))
        await asyncio.sleep(0.01)
        self.active -= 1
        return FakeResult()


class RecordingReasoningSynthesizer:
    def __init__(self, answer="Pro 综合答案", error: Exception | None = None):
        self.answer = answer
        self.error = error
        self.calls = []

    async def synthesize(self, request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.answer


class GroundedCrossDomainRunner(FakeRunner):
    async def run(self, agent, input, **kwargs):
        context = kwargs["context"]
        for call_id, tool_name in (
            ("call-approval", "approval_flow_expert"),
            ("call-workflow", "workflow_expert"),
        ):
            context.start_tool(call_id, tool_name, "Manager Agent")
            context.finish_tool(call_id, "completed", 1.0)
        context.add_knowledge_citation(
            "approval-workflow-code",
            "审批通过触发工作流实现",
            "审批流/工作流",
            {
                "source_type": "code",
                "branch": "develop",
                "relative_path": "ApprovalWorkflowService.java",
                "symbol_name": "triggerWorkflow",
            },
        )
        return FakeResult("Flash 草稿")


class FakeStreamedResult(FakeResult):
    def __init__(self):
        super().__init__("流式完成")
        self.cancelled = False

    async def stream_events(self):
        yield SimpleNamespace(
            type="agent_updated_stream_event",
            new_agent=SimpleNamespace(name="Manager Agent"),
        )
        yield SimpleNamespace(
            type="raw_response_event",
            data=SimpleNamespace(type="response.output_text.delta", delta="你好"),
        )
        yield SimpleNamespace(
            type="run_item_stream_event",
            name="tool_called",
            item=SimpleNamespace(
                raw_item=SimpleNamespace(name="ordinary_tool", call_id="call-1")
            ),
        )
        yield SimpleNamespace(
            type="run_item_stream_event",
            name="tool_output",
            item=SimpleNamespace(
                raw_item={"call_id": "call-1"},
                output="must-not-appear",
            ),
        )

    def cancel(self):
        self.cancelled = True


class FakeStreamingRunner(FakeRunner):
    def __init__(self):
        super().__init__()
        self.streamed = FakeStreamedResult()

    def run_streamed(self, agent, input, **kwargs):
        self.calls.append((agent, input, kwargs))
        return self.streamed


class UnsupportedSpecialistRunner(FakeRunner):
    async def run(self, agent, input, **kwargs):
        context = kwargs["context"]
        context.start_tool(
            "call-bug",
            "bug_diagnosis_expert",
            "Manager Agent",
        )
        context.finish_tool("call-bug", "completed", 1.0)
        return FakeResult("这是没有任何检索证据的内部结论")


class EmptyLogSpecialistRunner(UnsupportedSpecialistRunner):
    async def run(self, agent, input, **kwargs):
        result = await super().run(agent, input, **kwargs)
        kwargs["context"].add_log_trace_citation(
            trace_id="trace-empty-123456",
            environment="test",
            from_ms=1000,
            to_ms=2000,
            log_count=0,
            exception_types=[],
            truncated=False,
            entries=[],
        )
        return result


class UnsupportedSpecialistStream(FakeResult):
    def __init__(self):
        super().__init__("这是没有任何检索证据的流式内部结论")
        self.cancelled = False

    async def stream_events(self):
        yield SimpleNamespace(
            type="run_item_stream_event",
            name="tool_called",
            item=SimpleNamespace(
                raw_item=SimpleNamespace(
                    name="bug_diagnosis_expert",
                    call_id="call-bug",
                )
            ),
        )
        yield SimpleNamespace(
            type="raw_response_event",
            data=SimpleNamespace(
                type="response.output_text.delta",
                delta="这是没有任何检索证据的流式内部结论",
            ),
        )
        yield SimpleNamespace(
            type="run_item_stream_event",
            name="tool_output",
            item=SimpleNamespace(raw_item={"call_id": "call-bug"}, output="ignored"),
        )

    def cancel(self):
        self.cancelled = True


class UnsupportedSpecialistStreamingRunner(FakeRunner):
    def run_streamed(self, agent, input, **kwargs):
        context = kwargs["context"]
        context.start_tool("call-bug", "bug_diagnosis_expert", "Manager Agent")
        context.finish_tool("call-bug", "completed", 1.0)
        return UnsupportedSpecialistStream()


class AuthoritativeBugRunner(UnsupportedSpecialistRunner):
    async def run(self, agent, input, **kwargs):
        result = await super().run(agent, input, **kwargs)
        kwargs["context"].response_override = "日志平台暂时不可用，请稍后重试。"
        return result


class AuthoritativeBugStreamingRunner(UnsupportedSpecialistStreamingRunner):
    def run_streamed(self, agent, input, **kwargs):
        kwargs["context"].response_override = "日志平台暂时不可用，请稍后重试。"
        return super().run_streamed(agent, input, **kwargs)


class GroundedSpecialistStream(FakeResult):
    def __init__(self):
        super().__init__("最终回答")
        self.cancelled = False

    async def stream_events(self):
        yield SimpleNamespace(
            type="run_item_stream_event",
            name="tool_called",
            item=SimpleNamespace(
                raw_item=SimpleNamespace(
                    name="approval_flow_expert",
                    call_id="call-approval",
                )
            ),
        )
        yield SimpleNamespace(
            type="raw_response_event",
            data=SimpleNamespace(
                type="response.output_text.delta",
                delta="专家内部草稿",
            ),
        )
        yield SimpleNamespace(
            type="run_item_stream_event",
            name="tool_output",
            item=SimpleNamespace(
                raw_item={"call_id": "call-approval"},
                output="受控工具输出",
            ),
        )
        yield SimpleNamespace(
            type="raw_response_event",
            data=SimpleNamespace(type="response.output_text.delta", delta="最终"),
        )
        yield SimpleNamespace(
            type="raw_response_event",
            data=SimpleNamespace(type="response.output_text.delta", delta="回答"),
        )

    def cancel(self):
        self.cancelled = True


class GroundedSpecialistStreamingRunner(FakeRunner):
    def run_streamed(self, agent, input, **kwargs):
        context = kwargs["context"]
        context.add_knowledge_citation(
            "approval-code-1",
            "ProcessTaskController.adminTransferTask",
            "审批流",
            {
                "source_type": "code",
                "symbol_type": "method",
                "symbol_name": "adminTransferTask",
            },
        )
        return GroundedSpecialistStream()


class GroundedCrossDomainStream(FakeResult):
    def __init__(self):
        super().__init__("Flash 草稿")
        self.cancelled = False

    async def stream_events(self):
        for call_id, tool_name in (
            ("call-approval", "approval_flow_expert"),
            ("call-workflow", "workflow_expert"),
        ):
            yield SimpleNamespace(
                type="run_item_stream_event",
                name="tool_called",
                item=SimpleNamespace(
                    raw_item=SimpleNamespace(name=tool_name, call_id=call_id)
                ),
            )
            yield SimpleNamespace(
                type="run_item_stream_event",
                name="tool_output",
                item=SimpleNamespace(raw_item={"call_id": call_id}, output="evidence"),
            )
        for delta in ("Flash ", "草稿"):
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.output_text.delta", delta=delta),
            )

    def cancel(self):
        self.cancelled = True


class GroundedCrossDomainStreamingRunner(FakeRunner):
    def run_streamed(self, agent, input, **kwargs):
        context = kwargs["context"]
        context.add_knowledge_citation(
            "approval-workflow-code",
            "审批通过触发工作流实现",
            "审批流/工作流",
            {
                "source_type": "code",
                "branch": "develop",
                "relative_path": "ApprovalWorkflowService.java",
                "symbol_name": "triggerWorkflow",
            },
        )
        return GroundedCrossDomainStream()


class StreamingReasoningSynthesizer(RecordingReasoningSynthesizer):
    async def synthesize(self, request, on_delta=None):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        if on_delta is not None:
            await on_delta("Pro ")
            await on_delta("综合答案")
        return self.answer


class DuplicateCitationRunner(FakeRunner):
    async def run(self, agent, input, **kwargs):
        context = kwargs["context"]
        for chunk_id in ("code-1", "code-2"):
            context.add_knowledge_citation(
                chunk_id,
                "WorkflowService.run",
                "工作流",
                {
                    "source_type": "code",
                    "branch": "develop",
                    "relative_path": "WorkflowService.java",
                    "symbol_name": "WorkflowService.run",
                },
            )
        return FakeResult("根据代码证据回答")


class UnsupportedCapabilityRunner(FakeRunner):
    async def run(self, agent, input, **kwargs):
        context = kwargs["context"]
        context.start_tool("call-workflow", "workflow_expert", "Manager Agent")
        context.finish_tool("call-workflow", "completed", 1.0)
        context.add_knowledge_citation(
            "workflow-dto",
            "工作流配置字段",
            "工作流",
            {
                "source_type": "code",
                "symbol_kind": "field",
                "symbol_name": "parallelJoin",
            },
        )
        return FakeResult("工作流不支持并行汇聚能力。")


class FakeModelFactory:
    def create_run_config(self, conversation_id):
        return RunConfig(
            group_id=conversation_id,
            trace_id="trace_0123456789abcdef0123456789abcdef",
            trace_include_sensitive_data=False,
            tracing_disabled=True,
        )


class FakeDirectBugGraph:
    def __init__(
        self,
        *,
        pending=False,
        reusable=False,
        answer="请补充 trace ID。",
    ):
        self.pending = pending
        self.reusable = reusable
        self.answer = answer
        self.calls = []

    async def has_pending(self, conversation_id):
        return self.pending

    async def should_resume(self, conversation_id, message):
        return self.reusable

    async def diagnose(self, bug_report, *, conversation_id, run_id):
        self.calls.append((bug_report, conversation_id, run_id))
        return BugDiagnosisResult(
            status="clarification_required",
            answer=self.answer,
            missing_fields=["trace_id"],
            citations=[],
        )


class FakeStreamingDirectBugGraph(FakeDirectBugGraph):
    async def diagnose(
        self,
        bug_report,
        *,
        conversation_id,
        run_id,
        on_diagnosis_delta=None,
    ):
        self.calls.append((bug_report, conversation_id, run_id))
        assert on_diagnosis_delta is not None
        await on_diagnosis_delta("问题")
        await on_diagnosis_delta("摘要")
        return BugDiagnosisResult(
            status="completed",
            answer="问题摘要",
            missing_fields=[],
            citations=[],
        )


@pytest.mark.asyncio
async def test_agent_service_runs_with_session_context_and_server_limits(tmp_path):
    runner = FakeRunner()
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        max_turns=12,
        runner=runner,
    )

    response = await service.chat("指标是什么", conversation_id="conversation-1")

    assert response.status == "completed"
    assert response.conversation_id == "conversation-1"
    assert response.answer == "完成"
    assert response.last_agent == "Manager Agent"
    assert response.trace_id == "trace_0123456789abcdef0123456789abcdef"
    _, message, kwargs = runner.calls[0]
    assert message == "指标是什么"
    assert kwargs["max_turns"] == 12
    assert kwargs["context"].conversation_id == "conversation-1"
    assert kwargs["session"].session_settings.limit == 50


@pytest.mark.asyncio
async def test_agent_service_propagates_authenticated_user_identity(tmp_path):
    runner = FakeRunner()
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=runner,
    )

    await service.chat(
        "接口怎么对接",
        conversation_id="conversation-memory",
        user_id="user-1",
    )

    assert runner.calls[0][2]["context"].user_id == "user-1"


@pytest.mark.asyncio
async def test_agent_service_injects_only_recalled_memory_as_bounded_context(tmp_path):
    class RecalledMemory:
        summary = "用户偏好接口回答包含入参与出参"
        memory_type = "user_preference"

    class FakeMemoryService:
        async def recall(self, query, *, user_id, space_id, domain_id):
            assert (query, user_id, space_id, domain_id) == (
                "接口怎么对接",
                "user-1",
                "middle-platform",
                None,
            )
            return [RecalledMemory()]

    runner = FakeRunner()
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=runner,
        memory_service=FakeMemoryService(),
    )

    await service.chat(
        "接口怎么对接",
        conversation_id="conversation-memory-context",
        user_id="user-1",
    )

    message = runner.calls[0][1]
    assert "相关历史上下文" in message
    assert "用户偏好接口回答包含入参与出参" in message
    assert message.endswith("接口怎么对接")


@pytest.mark.asyncio
async def test_agent_service_uses_conversation_summary_augmentation_when_available(tmp_path):
    class FakeMemoryService:
        def __init__(self):
            self.calls = []

        async def augment_message(self, message, **kwargs):
            self.calls.append((message, kwargs))
            return "历史会话摘要：管理员转办接口\n\n当前问题：\n继续"

    memory = FakeMemoryService()
    runner = FakeRunner()
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=runner,
        memory_service=memory,
    )

    await service.chat("继续", conversation_id="conversation-summary", user_id="user-1")

    assert runner.calls[0][1].startswith("历史会话摘要")
    assert memory.calls[0][1]["conversation_id"] == "conversation-summary"


@pytest.mark.asyncio
async def test_agent_service_returns_deduplicated_citations(tmp_path):
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=DuplicateCitationRunner(),
        public_citation_limit=10,
    )

    response = await service.chat("工作流怎么运行", "conversation-citations")

    assert [citation.source_id for citation in response.citations] == ["code-1"]


@pytest.mark.asyncio
async def test_agent_service_downgrades_unsupported_claim_without_direct_evidence(
    tmp_path,
):
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=UnsupportedCapabilityRunner(),
    )

    response = await service.chat("是否支持并行汇聚", "conversation-evidence")

    assert response.answer.startswith("本次检索暂未找到该能力的明确实现")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("帮我查同事银行卡密码", "不能查询"),
        ("直接删除销售额指标，不需要确认", "不能执行"),
    ],
)
async def test_agent_service_request_guard_returns_without_calling_runner(
    tmp_path,
    message,
    expected,
):
    runner = FakeRunner()
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=runner,
        request_guard=RequestGuard(),
    )

    response = await service.chat(message, "conversation-guard")

    assert expected in response.answer
    assert response.tool_runs == []
    assert response.citations == []
    assert runner.calls == []


@pytest.mark.asyncio
async def test_stream_hides_model_behavior_error_behind_actionable_message(tmp_path):
    class BehaviorErrorRunner:
        @staticmethod
        def run_streamed(*args, **kwargs):
            raise ModelBehaviorError("invalid internal tool payload")

    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=BehaviorErrorRunner(),
    )

    events = [
        event
        async for event in service.stream_chat(
            "指标平台怎么使用",
            "conversation-model-behavior-error",
        )
    ]

    error = events[-1]
    assert error["event"] == "run.error"
    assert error["data"]["error_type"] == "ModelBehaviorError"
    assert error["data"]["error"] == "模型返回的工具调用格式异常，请重新发送问题。"
    assert "invalid internal tool payload" not in str(error)


@pytest.mark.asyncio
async def test_agent_service_stream_request_guard_emits_controlled_completion(tmp_path):
    runner = FakeStreamingRunner()
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=runner,
        request_guard=RequestGuard(),
    )

    events = [
        event
        async for event in service.stream_chat(
            "直接删除销售额指标，不需要确认",
            "conversation-stream-guard",
        )
    ]

    assert [event["event"] for event in events] == [
        "run.started",
        "text.delta",
        "run.completed",
    ]
    assert "不能执行" in events[-1]["data"]["answer"]
    assert runner.calls == []


@pytest.mark.asyncio
async def test_agent_service_selects_restricted_manager_from_intent_router(tmp_path):
    from knowledge.agent_runtime.intent_router import DomainIntentRouter

    runner = FakeRunner()
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager="root-manager",
        domain_managers={"workflow": "workflow-manager"},
        intent_router=DomainIntentRouter(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=runner,
    )

    await service.chat("HTTP 节点超时会重试几次", "conversation-route")

    selected_manager, _, kwargs = runner.calls[0]
    assert selected_manager == "workflow-manager"
    assert kwargs["context"].routing_domains == ["workflow"]
    assert kwargs["context"].routing_intent == "workflow"


@pytest.mark.asyncio
async def test_single_domain_answer_does_not_use_reasoning_synthesizer(tmp_path):
    from knowledge.agent_runtime.intent_router import DomainIntentRouter

    synthesizer = RecordingReasoningSynthesizer()
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager="root-manager",
        domain_managers={"approval-flow": "approval-manager"},
        intent_router=DomainIntentRouter(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=DuplicateCitationRunner(),
        reasoning_synthesizer=synthesizer,
    )

    response = await service.chat(
        "审批流管理员转办接口是什么",
        "single-domain-reasoning",
    )

    assert response.answer == "根据代码证据回答"
    assert synthesizer.calls == []


@pytest.mark.asyncio
async def test_grounded_cross_domain_answer_uses_reasoning_synthesizer(tmp_path):
    from knowledge.agent_runtime.intent_router import DomainIntentRouter

    synthesizer = RecordingReasoningSynthesizer()
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager="root-manager",
        intent_router=DomainIntentRouter(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=GroundedCrossDomainRunner(),
        reasoning_synthesizer=synthesizer,
    )

    response = await service.chat(
        "审批通过后如何触发工作流连接器",
        "cross-domain-reasoning",
    )

    assert response.answer == "Pro 综合答案"
    assert len(synthesizer.calls) == 1
    assert synthesizer.calls[0].draft == "Flash 草稿"
    assert synthesizer.calls[0].domains == ("approval-flow", "workflow")
    assert response.quality_spans[-1].name == "manager.reasoning_synthesis"
    assert response.quality_spans[-1].status == "completed"


@pytest.mark.asyncio
async def test_multiple_specialists_trigger_reasoning_when_rule_route_is_unknown(tmp_path):
    synthesizer = RecordingReasoningSynthesizer()
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager="root-manager",
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=GroundedCrossDomainRunner(),
        reasoning_synthesizer=synthesizer,
    )

    response = await service.chat(
        "请综合相关中台能力",
        "multi-specialist-reasoning",
    )

    assert response.answer == "Pro 综合答案"
    assert len(synthesizer.calls) == 1


@pytest.mark.asyncio
async def test_reasoning_synthesis_failure_falls_back_to_flash_answer(tmp_path):
    from knowledge.agent_runtime.intent_router import DomainIntentRouter

    synthesizer = RecordingReasoningSynthesizer(error=TimeoutError())
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager="root-manager",
        intent_router=DomainIntentRouter(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=GroundedCrossDomainRunner(),
        reasoning_synthesizer=synthesizer,
    )

    response = await service.chat(
        "审批通过后如何触发工作流连接器",
        "cross-domain-reasoning-timeout",
    )

    assert response.answer == "Flash 草稿"
    assert response.quality_spans[-1].status == "timeout"


@pytest.mark.asyncio
async def test_agent_service_routes_bug_directly_without_calling_model_runner(tmp_path):
    from knowledge.agent_runtime.intent_router import DomainIntentRouter

    runner = FakeRunner()
    graph = FakeDirectBugGraph()
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager="root-manager",
        domain_managers={"bug": "bug-manager"},
        intent_router=DomainIntentRouter(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=runner,
        bug_graph_service=graph,
    )

    response = await service.chat(
        "开发环境接口报错，traceId: trace-direct-123456",
        "conversation-direct-bug",
    )

    assert runner.calls == []
    assert graph.calls == [
        (
            "开发环境接口报错，traceId: trace-direct-123456",
            "conversation-direct-bug",
            response.run_id,
        )
    ]
    assert response.answer == "请补充 trace ID。"
    assert response.routed_domains == ["bug"]
    assert response.specialists_used == ["Bug 分析专家"]
    assert [item.tool_name for item in response.tool_runs] == [
        "bug_diagnosis_expert"
    ]


@pytest.mark.asyncio
async def test_agent_service_routes_pending_bug_supplement_directly(tmp_path):
    runner = FakeRunner()
    graph = FakeDirectBugGraph(pending=True)
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager="root-manager",
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=runner,
        bug_graph_service=graph,
    )

    await service.chat("开发环境", "conversation-pending-bug")

    assert runner.calls == []
    assert graph.calls[0][0] == "开发环境"


@pytest.mark.asyncio
async def test_agent_service_does_not_let_pending_bug_capture_new_domain_question(tmp_path):
    from knowledge.agent_runtime.intent_router import DomainIntentRouter

    runner = FakeRunner()
    graph = FakeDirectBugGraph(pending=True)
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager="root-manager",
        domain_managers={"metric-platform": "metric-manager"},
        intent_router=DomainIntentRouter(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=runner,
        bug_graph_service=graph,
    )

    await service.chat("如何对接中台的指标平台", "conversation-pending-bug-new-topic")

    assert graph.calls == []
    assert runner.calls[0][0] == "metric-manager"


@pytest.mark.asyncio
async def test_agent_service_routes_completed_bug_follow_up_directly(tmp_path):
    runner = FakeRunner()
    graph = FakeDirectBugGraph(reusable=True)
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager="root-manager",
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=runner,
        bug_graph_service=graph,
    )

    await service.chat("继续看上面的原因", "conversation-completed-follow-up")

    assert runner.calls == []
    assert graph.calls[0][0] == "继续看上面的原因"


@pytest.mark.asyncio
async def test_agent_service_streams_direct_bug_graph_lifecycle(tmp_path):
    from knowledge.agent_runtime.intent_router import DomainIntentRouter

    runner = FakeStreamingRunner()
    graph = FakeDirectBugGraph(answer="请补充问题环境和 trace ID。")
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager="root-manager",
        domain_managers={"bug": "bug-manager"},
        intent_router=DomainIntentRouter(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=runner,
        bug_graph_service=graph,
    )

    events = [
        event
        async for event in service.stream_chat(
            "接口报错，traceId: trace-stream-direct-123456",
            "conversation-stream-direct-bug",
        )
    ]

    assert runner.calls == []
    assert [event["event"] for event in events] == [
        "run.started",
        "agent.updated",
        "tool.started",
        "tool.completed",
        "text.delta",
        "run.completed",
    ]
    assert events[-2]["data"]["delta"] == "请补充问题环境和 trace ID。"


@pytest.mark.asyncio
async def test_agent_service_forwards_bug_diagnosis_deltas_before_tool_completion(tmp_path):
    from knowledge.agent_runtime.intent_router import DomainIntentRouter

    graph = FakeStreamingDirectBugGraph()
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager="root-manager",
        domain_managers={"bug": "bug-manager"},
        intent_router=DomainIntentRouter(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=FakeStreamingRunner(),
        bug_graph_service=graph,
    )

    events = [
        event
        async for event in service.stream_chat(
            "生产环境接口报错，traceId: trace-stream-direct-123456",
            "conversation-streaming-diagnosis",
        )
    ]
    names = [event["event"] for event in events]
    deltas = [event["data"]["delta"] for event in events if event["event"] == "text.delta"]

    assert deltas == ["问题", "摘要"]
    assert names.index("text.delta") < names.index("tool.completed")


@pytest.mark.asyncio
async def test_cross_domain_stream_emits_only_pro_answer(tmp_path):
    from knowledge.agent_runtime.intent_router import DomainIntentRouter

    synthesizer = StreamingReasoningSynthesizer(answer="Pro 综合答案")
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager="root-manager",
        intent_router=DomainIntentRouter(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=GroundedCrossDomainStreamingRunner(),
        reasoning_synthesizer=synthesizer,
    )

    events = [
        event
        async for event in service.stream_chat(
            "审批通过后如何触发工作流连接器",
            "cross-domain-stream-reasoning",
        )
    ]

    deltas = [
        event["data"]["delta"]
        for event in events
        if event["event"] == "text.delta"
    ]
    assert "".join(deltas) == "Pro 综合答案"
    assert "Flash 草稿" not in "".join(deltas)
    assert events[-1]["data"]["answer"] == "Pro 综合答案"


@pytest.mark.asyncio
async def test_agent_response_exposes_inferred_domain_and_specialist(tmp_path):
    class ApprovalRunner(FakeRunner):
        async def run(self, agent, input, **kwargs):
            context = kwargs["context"]
            context.start_tool(
                "call-approval",
                "approval_flow_expert",
                "Manager Agent",
            )
            context.finish_tool("call-approval", "completed", 1.0)
            context.add_knowledge_citation(
                "approval-doc",
                "审批流转交接口",
                "审批流",
                {"source_type": "product_document"},
            )
            return FakeResult("审批流回答")

    from knowledge.agent_runtime.intent_router import DomainIntentRouter

    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager="root-manager",
        domain_managers={"approval-flow": "approval-manager"},
        intent_router=DomainIntentRouter(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=ApprovalRunner(),
    )

    response = await service.chat(
        "审批流转交审核的接口 API 更新了吗",
        "conversation-approval-route",
    )

    assert response.routed_domains == ["approval-flow"]
    assert response.specialists_used == ["审批流专家"]
    assert response.last_agent == "Manager Agent"


@pytest.mark.asyncio
async def test_direct_specialist_without_citations_is_evidence_gated(tmp_path):
    class DirectApprovalRunner(FakeRunner):
        async def run(self, agent, input, **kwargs):
            context = kwargs["context"]
            context.start_tool(
                "collect-1", "collect_domain_evidence", "审批流专家"
            )
            context.finish_tool("collect-1", "completed", 1.0)
            result = FakeResult("代码中已实现管理员转办接口。")
            result.last_agent = SimpleNamespace(name="审批流专家")
            return result

    from knowledge.agent_runtime.intent_router import DomainIntentRouter
    from knowledge.agent_runtime.service import EVIDENCE_UNAVAILABLE_ANSWER

    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager="root-manager",
        domain_managers={"approval-flow": "approval-specialist"},
        intent_router=DomainIntentRouter(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=DirectApprovalRunner(),
    )

    response = await service.chat(
        "审批流管理员转办接口是什么", "conversation-direct-no-evidence"
    )

    assert response.answer == EVIDENCE_UNAVAILABLE_ANSWER
    assert response.specialists_used == ["审批流专家"]


@pytest.mark.asyncio
async def test_agent_service_serializes_concurrent_turns_for_same_conversation(tmp_path):
    runner = FakeRunner()
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        max_turns=12,
        runner=runner,
    )

    await asyncio.gather(
        service.chat("first", "same-conversation"),
        service.chat("second", "same-conversation"),
    )

    assert runner.max_active == 1


@pytest.mark.asyncio
async def test_agent_service_rejects_blank_message(tmp_path):
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=FakeRunner(),
    )

    with pytest.raises(ValueError, match="message"):
        await service.chat("   ")


@pytest.mark.asyncio
async def test_agent_service_maps_sdk_stream_events_without_tool_output(tmp_path):
    runner = FakeStreamingRunner()
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=runner,
    )

    events = [
        event
        async for event in service.stream_chat("你好", "conversation-stream")
    ]

    assert [event["event"] for event in events] == [
        "run.started",
        "agent.updated",
        "text.delta",
        "tool.started",
        "tool.completed",
        "run.completed",
    ]
    assert events[2]["data"]["delta"] == "你好"
    assert "must-not-appear" not in str(events)
    assert events[-1]["data"]["answer"] == "流式完成"


@pytest.mark.asyncio
async def test_agent_service_replaces_unsupported_specialist_answer(tmp_path):
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=UnsupportedSpecialistRunner(),
    )

    response = await service.chat("帮我分析这个 Bug", "conversation-evidence-gate")

    assert response.answer == EVIDENCE_UNAVAILABLE_ANSWER
    assert response.citations == []


@pytest.mark.asyncio
async def test_agent_service_does_not_treat_empty_log_query_as_evidence(tmp_path):
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=EmptyLogSpecialistRunner(),
    )

    response = await service.chat("帮我分析这个 Bug", "conversation-empty-logs")

    assert response.answer == EVIDENCE_UNAVAILABLE_ANSWER
    assert response.citations[0].metadata["log_count"] == 0


@pytest.mark.asyncio
async def test_agent_service_preserves_authoritative_bug_graph_answer(tmp_path):
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=AuthoritativeBugRunner(),
    )

    response = await service.chat("帮我分析这个 Bug", "conversation-bug-status")

    assert response.answer == "日志平台暂时不可用，请稍后重试。"


@pytest.mark.asyncio
async def test_agent_service_stream_does_not_leak_unsupported_specialist_text(tmp_path):
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=UnsupportedSpecialistStreamingRunner(),
    )

    events = [
        event
        async for event in service.stream_chat(
            "帮我分析这个 Bug",
            "conversation-stream-evidence-gate",
        )
    ]

    deltas = [
        event["data"]["delta"]
        for event in events
        if event["event"] == "text.delta"
    ]
    assert deltas == [EVIDENCE_UNAVAILABLE_ANSWER]
    assert "这是没有任何检索证据的流式内部结论" not in str(events)
    assert events[-1]["event"] == "run.completed"
    assert events[-1]["data"]["answer"] == EVIDENCE_UNAVAILABLE_ANSWER


@pytest.mark.asyncio
async def test_agent_service_stream_uses_authoritative_bug_graph_answer(tmp_path):
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=AuthoritativeBugStreamingRunner(),
    )

    events = [
        event
        async for event in service.stream_chat(
            "帮我分析这个 Bug",
            "conversation-stream-bug-status",
        )
    ]

    deltas = [
        event["data"]["delta"]
        for event in events
        if event["event"] == "text.delta"
    ]
    assert deltas == ["日志平台暂时不可用，请稍后重试。"]
    assert events[-1]["data"]["answer"] == "日志平台暂时不可用，请稍后重试。"


@pytest.mark.asyncio
async def test_agent_service_streams_grounded_manager_answer_after_specialist(tmp_path):
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=GroundedSpecialistStreamingRunner(),
    )

    events = [
        event
        async for event in service.stream_chat(
            "管理员转办接口是什么",
            "conversation-stream-grounded-specialist",
        )
    ]

    event_names = [event["event"] for event in events]
    deltas = [
        event["data"]["delta"]
        for event in events
        if event["event"] == "text.delta"
    ]
    tool_completed_index = event_names.index("tool.completed")
    first_delta_index = event_names.index("text.delta")

    assert deltas == ["最终", "回答"]
    assert "专家内部草稿" not in str(events)
    assert first_delta_index > tool_completed_index
    assert events[-1]["event"] == "run.completed"
    assert events[-1]["data"]["answer"] == "最终回答"


@pytest.mark.asyncio
async def test_agent_service_persists_scope_and_rejects_conversation_switch(tmp_path):
    runner = FakeRunner()
    db_path = tmp_path / "agent.db"
    pending = PendingRunRepository(db_path)
    scopes = ConversationScopeRepository(db_path)
    await pending.initialize()
    await scopes.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(db_path, 50),
        pending_runs=pending,
        scope_repository=scopes,
        runner=runner,
    )

    await service.chat(
        "指标是什么",
        "conversation-scope",
        knowledge_space_id="middle-platform",
        domain_id="metric-platform",
        scope_provided=True,
    )
    await service.chat("继续", "conversation-scope")

    first_context = runner.calls[0][2]["context"]
    second_context = runner.calls[1][2]["context"]
    assert first_context.domain_id == "metric-platform"
    assert second_context.domain_id == "metric-platform"
    with pytest.raises(ConversationScopeConflictError):
        await service.chat(
            "切换工作流",
            "conversation-scope",
            knowledge_space_id="middle-platform",
            domain_id="workflow",
            scope_provided=True,
        )

    await service.delete_conversation("conversation-scope")
    assert await scopes.get("conversation-scope") is None


@pytest.mark.asyncio
async def test_deleting_conversation_also_deletes_its_summary(tmp_path):
    from knowledge.memory.models import ConversationSummary
    from knowledge.memory.repository import MemoryRepository
    from knowledge.memory.service import MemoryService

    memory_repository = MemoryRepository(tmp_path / "memory.db")
    await memory_repository.initialize()
    await memory_repository.upsert_conversation_summary(ConversationSummary(
        conversation_id="conversation-delete-summary",
        user_id="user-1",
        space_id="middle-platform",
        domain_id=None,
        summary="待删除摘要",
        goals=(),
        confirmed_facts=(),
        unresolved_items=(),
        preferences=(),
    ))
    pending = PendingRunRepository(tmp_path / "agent.db")
    await pending.initialize()
    service = AgentService(
        manager=object(),
        model_factory=FakeModelFactory(),
        session_factory=AgentSessionFactory(tmp_path / "agent.db", 50),
        pending_runs=pending,
        runner=FakeRunner(),
        memory_service=MemoryService(memory_repository),
    )

    await service.delete_conversation("conversation-delete-summary")

    assert await memory_repository.get_conversation_summary(
        "conversation-delete-summary"
    ) is None
