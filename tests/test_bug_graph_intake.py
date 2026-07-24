import pytest

from knowledge.bug_graph.intake import BugIntakeParser


class FakeNormalizer:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def normalize(self, message, validation_feedback=None):
        self.calls.append((message, validation_feedback))
        return self.outputs.pop(0)


@pytest.mark.asyncio
async def test_intake_understands_conversational_environment_and_verified_trace():
    model = FakeNormalizer(
        [
            """{
              "normalized_problem": "发货接口偶发卡住",
              "environment": "prod",
              "environment_evidence": "线上",
              "trace_id": "trace-prod-123456",
              "service": "发货接口",
              "endpoint": null,
              "symptoms": ["偶发卡住"],
              "domain_hints": ["workflow"]
            }"""
        ]
    )

    intake = await BugIntakeParser(model).parse(
        "线上发货接口偶发卡住，trace ID 是 trace-prod-123456，帮我看看"
    )

    assert intake.environment == "prod"
    assert intake.trace_id == "trace-prod-123456"
    assert intake.normalized_problem == "发货接口偶发卡住"
    assert intake.missing_fields == []


@pytest.mark.asyncio
async def test_intake_extracts_request_time_from_original_message():
    intake = await BugIntakeParser().parse(
        "环境 prod，traceId 55b06a6d-e30d-4748-b141-10a9091c6d29，"
        "请求时间 2026-07-16 11:48:34，接口 getInstanceDetail 报错"
    )

    assert intake.environment == "prod"
    assert intake.trace_id == "55b06a6d-e30d-4748-b141-10a9091c6d29"
    assert intake.request_time == "2026-07-16T11:48:34+08:00"
    assert intake.request_time_evidence == "2026-07-16 11:48:34"


@pytest.mark.asyncio
async def test_intake_rejects_model_invented_trace_and_master_environment_guess():
    model = FakeNormalizer(
        [
            '{"normalized_problem":"接口失败","environment":"prod",'
            '"environment_evidence":"master","trace_id":"invented-trace-999",'
            '"symptoms":[],"domain_hints":[]}'
        ]
    )

    intake = await BugIntakeParser(model).parse("master 分支接口失败")

    assert intake.environment is None
    assert intake.trace_id is None
    assert intake.missing_fields == ["environment", "trace_id"]


@pytest.mark.asyncio
async def test_intake_repairs_invalid_json_once_then_uses_deterministic_fallback():
    model = FakeNormalizer(["not json", "still not json"])

    intake = await BugIntakeParser(model).parse(
        "测试环境报错，traceId: fallback-trace-123456"
    )

    assert len(model.calls) == 2
    assert model.calls[1][1]
    assert intake.environment == "test"
    assert intake.trace_id == "fallback-trace-123456"
    assert intake.missing_fields == []


@pytest.mark.asyncio
async def test_intake_reports_exact_missing_fields_without_model():
    intake = await BugIntakeParser().parse("开发环境接口报错")

    assert intake.environment == "develop"
    assert intake.trace_id is None
    assert intake.missing_fields == ["trace_id"]
    assert "trace ID" in intake.clarification_question


@pytest.mark.asyncio
async def test_intake_deterministic_mode_never_calls_model_normalizer():
    model = FakeNormalizer([])

    intake = await BugIntakeParser(model).parse(
        "开发环境，traceId: trace-fast-123456",
        normalize=False,
    )

    assert intake.environment == "develop"
    assert intake.trace_id == "trace-fast-123456"
    assert intake.missing_fields == []
    assert model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["raceId", "raceld", "traceld"])
async def test_intake_accepts_common_trace_id_label_typos(label):
    intake = await BugIntakeParser().parse(
        f"{label} 是 typo-trace-123456，开发环境",
        normalize=False,
    )

    assert intake.environment == "develop"
    assert intake.trace_id == "typo-trace-123456"
    assert intake.missing_fields == []


@pytest.mark.asyncio
async def test_intake_accepts_standalone_uuid_only_when_explicitly_allowed():
    trace_id = "c141473b-764e-439d-803f-2912da7df986"

    ordinary = await BugIntakeParser().parse(trace_id, normalize=False)
    clarification = await BugIntakeParser().parse(
        trace_id,
        normalize=False,
        allow_standalone_trace=True,
    )

    assert ordinary.trace_id is None
    assert clarification.trace_id == trace_id
