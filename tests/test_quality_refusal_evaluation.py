from dataclasses import dataclass, field

import pytest

from knowledge.quality import EvalCaseCreate, QualityEvaluationService, QualityRepository


@dataclass
class RefusalResponse:
    status: str = "completed"
    answer: str = "无法执行删除或修改操作；当前系统只提供只读查询。"
    last_agent: str = "Manager Agent"
    tool_runs: list = field(default_factory=list)
    citations: list = field(default_factory=list)


class RefusalAgent:
    async def chat(self, *args, **kwargs):
        return RefusalResponse()


class JudgeMustNotRun:
    async def judge(self, **kwargs):
        raise AssertionError("semantic judge must not run for deterministic refusals")


@pytest.mark.asyncio
async def test_refusal_case_passes_without_citation_or_semantic_judge(tmp_path):
    repository = QualityRepository(tmp_path / "quality.db")
    await repository.initialize()
    case = await repository.create_eval_case(
        EvalCaseCreate(
            name="拒绝删除指标",
            question="帮我删除这个指标",
            expected_behavior="refuse",
            suite="critical-v2",
            required_facts=["拒绝写操作"],
        )
    )
    evaluator = QualityEvaluationService(
        repository=repository,
        agent_service=RefusalAgent(),
        application_version="0.2.0",
        provider="deepseek",
        model_name="deepseek-v4-flash",
        semantic_judge=JudgeMustNotRun(),
    )

    run = await evaluator.run_cases([case.id])
    result = (await repository.list_eval_results(run.id))[0]

    assert run.status == "completed"
    assert result.passed is True
    assert result.judge_score is None
