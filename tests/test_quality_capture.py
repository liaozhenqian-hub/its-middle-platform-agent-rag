from types import SimpleNamespace

import pytest

import aiosqlite

from knowledge.quality import (
    QualityCaptureService,
    QualityRepository,
    ToolRunSnapshot,
    TurnCompletion,
    TurnStart,
)


class CountingQualityRepository(QualityRepository):
    def __init__(self, path):
        super().__init__(path)
        self.batch_calls = 0

    async def record_spans(self, values):
        self.batch_calls += 1
        return await super().record_spans(values)


@pytest.mark.asyncio
async def test_quality_capture_records_completion_spans_in_one_batch(tmp_path):
    repository = CountingQualityRepository(tmp_path / "quality-batch.db")
    await repository.initialize()
    capture = QualityCaptureService(repository)
    turn = await capture.start(
        TurnStart(
            run_id="run-batch-capture", channel="codex", question="审批接口"
        )
    )

    await capture.complete(
        turn.run_id,
        TurnCompletion(
            status="completed",
            last_agent="审批流专家",
            duration_ms=100,
            tools=[
                ToolRunSnapshot(
                    tool_call_id="one",
                    tool_name="collect_domain_evidence",
                    agent_name="审批流专家",
                    status="completed",
                    duration_ms=20,
                )
            ],
        ),
    )

    assert repository.batch_calls == 1


@pytest.mark.asyncio
async def test_quality_capture_adds_rule_annotations_for_common_failures(tmp_path):
    repository = QualityRepository(tmp_path / "quality.db")
    await repository.initialize()
    capture = QualityCaptureService(repository)
    turn = await capture.start(
        TurnStart(run_id="run-rules", channel="web", question="审批接口是什么")
    )
    duplicated = SimpleNamespace(
        tool_call_id="one",
        tool_name="collect_domain_evidence",
        agent_name="审批流专家",
        status="completed",
        duration_ms=10,
        arguments={},
    )
    response = SimpleNamespace(
        answer="请问你指的是哪个接口？",
        last_agent="审批流专家",
        routed_domains=["approval-flow"],
        tool_runs=[duplicated, SimpleNamespace(**{**duplicated.__dict__, "tool_call_id": "two"})],
        citations=[],
    )

    await capture.complete_response(
        turn.run_id, response, status="completed", duration_ms=80_000
    )

    annotations = await repository.list_annotations()
    assert {item.code for item in annotations.items} == {
        "zero_citation",
        "duplicate_tool",
        "unexpected_clarification",
    }


@pytest.mark.asyncio
async def test_quality_capture_common_complete_path_records_spans_and_rules(tmp_path):
    repository = QualityRepository(tmp_path / "quality.db")
    await repository.initialize()
    capture = QualityCaptureService(repository)
    turn = await capture.start(
        TurnStart(run_id="run-common", channel="codex", question="审批接口")
    )

    await capture.complete(
        turn.run_id,
        TurnCompletion(
            status="completed",
            answer="无法确认",
            last_agent="审批流专家",
            routed_domains=["approval-flow"],
            duration_ms=100,
        ),
    )

    async with aiosqlite.connect(repository.database_path) as connection:
        span_count = (await (await connection.execute(
            "SELECT COUNT(*) FROM quality_spans WHERE turn_id=?", (turn.id,)
        )).fetchone())[0]
    assert span_count == 1
    annotations = await repository.list_annotations(code="zero_citation")
    assert annotations.total == 1
