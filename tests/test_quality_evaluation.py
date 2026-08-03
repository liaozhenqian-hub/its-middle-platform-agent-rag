from dataclasses import dataclass, field

import pytest

from knowledge.quality import (
    CitationSnapshot,
    EvalCaseCreate,
    QualityEvaluationService,
    QualityRepository,
    ToolRunSnapshot,
)
from knowledge.quality.behavior import BehaviorChecker
from knowledge.schemas.documents import KnowledgeChunk


def test_behavior_checker_recognizes_equivalent_chinese_clarification_and_refusal():
    assert BehaviorChecker.matches("clarify", "请问您希望选择哪个指标应用？", [])
    assert BehaviorChecker.matches("clarify", "请问您想查询哪一个指标？", [])
    assert BehaviorChecker.matches("clarify", "请告诉我具体的指标应用。", [])
    assert BehaviorChecker.matches("clarify", "请问您关注哪一个销售口径？", [])
    assert BehaviorChecker.matches("clarify", "建议你告诉我两个应用的名称。", [])
    assert BehaviorChecker.matches("clarify", "请问您使用哪一个应用来查询？", [])
    assert BehaviorChecker.matches("refuse", "无法查询他人的银行卡密码。", [])
    assert BehaviorChecker.matches("refuse", "无法执行指标删除操作。", [])


@dataclass
class FakeResponse:
    status: str = "completed"
    conversation_id: str = ""
    run_id: str = "agent-run"
    answer: str = "根据日志可以确认发生超时，代码位于 OrderService。"
    last_agent: str = "Manager Agent"
    tool_runs: list = field(
        default_factory=lambda: [
            ToolRunSnapshot(
                tool_call_id="call-1",
                tool_name="bug_diagnosis_expert",
                agent_name="Manager Agent",
                status="completed",
            )
        ]
    )
    citations: list = field(
        default_factory=lambda: [
            CitationSnapshot(source_type="log_trace", source_id="trace-1"),
            CitationSnapshot(source_type="code", source_id="code-1"),
        ]
    )


class FakeAgentService:
    def __init__(self):
        self.calls = []

    async def chat(self, message, conversation_id=None, **kwargs):
        self.calls.append((message, conversation_id, kwargs))
        if message == "会失败的用例":
            raise TimeoutError("private tool output")
        response = FakeResponse(conversation_id=conversation_id)
        return response


@pytest.mark.asyncio
async def test_evaluation_runs_isolated_cases_and_persists_deterministic_checks(tmp_path):
    repository = QualityRepository(tmp_path / "quality.db")
    await repository.initialize()
    passing = await repository.create_eval_case(
        EvalCaseCreate(
            name="Bug 根因定位",
            question="审批流超时",
            domain_id="approval-flow",
            required_tools=["bug_diagnosis_expert"],
            required_citation_types=["log_trace", "code"],
            required_facts=["日志", "超时"],
            forbidden_facts=["银行卡密码"],
        )
    )
    failing = await repository.create_eval_case(
        EvalCaseCreate(name="隔离失败", question="会失败的用例")
    )
    agent = FakeAgentService()
    evaluator = QualityEvaluationService(
        repository=repository,
        agent_service=agent,
        application_version="0.2.0",
        provider="deepseek",
        model_name="deepseek-chat",
    )

    run = await evaluator.run_cases([passing.id, failing.id])
    results = await repository.list_eval_results(run.id)

    assert run.status == "completed_with_failures"
    assert run.total_cases == 2
    assert run.passed_cases == 1
    assert run.failed_cases == 1
    assert len(results) == 2
    success = next(item for item in results if item.case_id == passing.id)
    assert success.passed is True
    assert all(success.checks.values())
    failure = next(item for item in results if item.case_id == failing.id)
    assert failure.passed is False
    assert failure.error_type == "TimeoutError"
    assert "private tool output" not in repr(failure)
    assert len({call[1] for call in agent.calls}) == 2
    assert all(call[1].startswith("eval:") for call in agent.calls)
    assert all(call[2]["scope_provided"] is True for call in agent.calls)
    assert (await repository.list_turns()).total == 0


@pytest.mark.asyncio
async def test_evaluation_checks_behavior_latency_and_output_budgets(tmp_path):
    repository = QualityRepository(tmp_path / "quality.db")
    await repository.initialize()
    case = await repository.create_eval_case(
        EvalCaseCreate(
            name="需要澄清指标应用",
            question="查销售额",
            expected_behavior="clarify",
            max_latency_ms=60_000,
            max_tool_calls=1,
            max_citations=1,
        )
    )
    response = FakeResponse(
        answer="找到两个候选，请您确认指标应用。",
        tool_runs=[
            ToolRunSnapshot(
                tool_call_id="call-1",
                tool_name="metric_platform_expert",
                agent_name="Manager Agent",
                status="completed",
            )
        ],
        citations=[
            CitationSnapshot(source_type="mcp_tool", source_id="searchBizMetric")
        ],
    )

    class ClarifyingAgent:
        async def chat(self, message, conversation_id=None, **kwargs):
            response.conversation_id = conversation_id
            return response

    evaluator = QualityEvaluationService(
        repository=repository,
        agent_service=ClarifyingAgent(),
        application_version="0.2.0",
        provider="deepseek",
        model_name="deepseek-chat",
    )

    run = await evaluator.run_cases([case.id])
    result = (await repository.list_eval_results(run.id))[0]

    assert result.passed is True
    assert result.checks["behavior"] is True
    assert result.checks["tool_count"] is True
    assert result.checks["citation_count"] is True
    assert result.checks["latency"] is True


@pytest.mark.asyncio
async def test_evaluation_invokes_semantic_judge_only_after_hard_gates(tmp_path):
    repository = QualityRepository(tmp_path / "quality-judge.db")
    await repository.initialize()
    passing = await repository.create_eval_case(
        EvalCaseCreate(
            name="有证据的接口回答",
            question="审批接口入参",
            required_facts=["超时"],
        )
    )
    hard_failure = await repository.create_eval_case(
        EvalCaseCreate(
            name="缺少必需事实",
            question="审批接口入参",
            required_facts=["不存在的关键事实"],
        )
    )

    class Judge:
        def __init__(self):
            self.calls = []

        async def judge(self, **payload):
            self.calls.append(payload)
            return {
                "score": 82,
                "relevance": 90,
                "factual_correctness": 88,
                "citation_support": 86,
                "contradiction": False,
                "unknown_calibration": 85,
                "actionability": 87,
                "facts_supported": True,
                "critical_contradiction": False,
                "reasons": ["证据支持"],
            }

    judge = Judge()
    evaluator = QualityEvaluationService(
        repository=repository,
        agent_service=FakeAgentService(),
        application_version="0.2.0",
        provider="deepseek",
        model_name="deepseek-v4-flash",
        semantic_judge=judge,
    )

    run = await evaluator.run_cases([passing.id, hard_failure.id])
    results = await repository.list_eval_results(run.id)
    judged = next(item for item in results if item.case_id == passing.id)
    failed = next(item for item in results if item.case_id == hard_failure.id)

    assert len(judge.calls) == 1
    assert judged.judge_score == 82
    assert judged.passed is True
    assert judged.review_state == "review_required"
    assert failed.judge_score is None
    assert "required_facts" in failed.failure_codes


@pytest.mark.asyncio
async def test_semantic_judge_receives_bounded_redacted_evidence_excerpts(tmp_path):
    repository = QualityRepository(tmp_path / "quality-evidence.db")
    await repository.initialize()
    case = await repository.create_eval_case(
        EvalCaseCreate(
            name="Evidence support",
            question="Where is the timeout handled?",
            required_citation_types=["code"],
            required_facts=["timeout handling"],
            suite="critical-v2",
        )
    )

    class EvidenceRepository:
        def get_chunks(self, *, ids):
            assert ids == ["code-1"]
            return [
                KnowledgeChunk(
                    chunk_id="code-1",
                    heading="Timeout handler",
                    content="Authorization: Bearer private-token " + ("timeout handling " * 100),
                    metadata={"source_type": "code"},
                )
            ]

    class Judge:
        def __init__(self):
            self.payload = None

        async def judge(self, **payload):
            self.payload = payload
            return {
                "score": 90,
                "facts_supported": True,
                "critical_contradiction": False,
            }

    judge = Judge()
    evaluator = QualityEvaluationService(
        repository=repository,
        agent_service=FakeAgentService(),
        application_version="0.2.0",
        provider="deepseek",
        model_name="deepseek-v4-flash",
        semantic_judge=judge,
        evidence_repository=EvidenceRepository(),
        evidence_excerpt_max_chars=200,
    )

    result = await evaluator.run_cases([case.id])

    assert result.passed_cases == 1
    excerpt = judge.payload["evidence"][1]["excerpt"]
    assert "timeout handling" in excerpt
    assert len(excerpt) <= 200
    assert "private-token" not in excerpt


@pytest.mark.asyncio
async def test_semantic_judge_timeout_is_persisted_without_blocking_the_run(tmp_path):
    import asyncio

    repository = QualityRepository(tmp_path / "quality-judge-timeout.db")
    await repository.initialize()
    case = await repository.create_eval_case(
        EvalCaseCreate(name="judge timeout", question="审批接口入参")
    )

    class SlowJudge:
        async def judge(self, **payload):
            await asyncio.sleep(1)

    evaluator = QualityEvaluationService(
        repository=repository,
        agent_service=FakeAgentService(),
        application_version="0.2.0",
        provider="deepseek",
        model_name="deepseek-v4-flash",
        semantic_judge=SlowJudge(),
        judge_timeout_seconds=0.01,
    )
    run = await evaluator.run_cases([case.id])
    result = (await repository.list_eval_results(run.id))[0]

    assert run.status == "completed_with_failures"
    assert result.passed is False
    assert result.review_state == "review_required"
    assert "judge_timeout" in result.failure_codes
