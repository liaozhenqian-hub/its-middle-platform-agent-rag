import logging
from datetime import UTC, datetime, timedelta, timezone

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from knowledge.bug_graph.service import BugDiagnosisGraphService
from knowledge.logs.grafana import GrafanaLogError, LogEntry, StackFrame, TraceLogResult


class FakeLogClient:
    def __init__(self, entries=()):
        self.entries = tuple(entries)
        self.calls = []

    async def query_trace(
        self,
        trace_id,
        environment,
        time_range_minutes,
        *,
        now_ms=None,
    ):
        self.calls.append((trace_id, environment, time_range_minutes, now_ms))
        exceptions = tuple(
            dict.fromkeys(
                item
                for entry in self.entries
                for item in entry.exception_types
            )
        )
        return TraceLogResult(
            trace_id=trace_id,
            environment=environment,
            code_branch="master" if environment == "prod" else "develop",
            from_ms=1000,
            to_ms=2000,
            entries=self.entries,
            exception_types=exceptions,
        )


class FakeCodeRetriever:
    def __init__(self, matches=None):
        self.matches = matches or []
        self.calls = []

    async def search(self, state, log_result):
        self.calls.append((dict(state), log_result))
        return list(self.matches)


class FailingEnrichmentRetriever(FakeCodeRetriever):
    async def enrich(self, state, matches):
        raise RuntimeError("enrichment failed")


class FakeDiagnosisGenerator:
    def __init__(self):
        self.calls = []

    async def generate(self, state, evidence):
        self.calls.append((dict(state), evidence))
        return "基于日志与代码的诊断报告"

    async def generate_stream(self, state, evidence, on_delta):
        self.calls.append((dict(state), evidence))
        await on_delta("基于日志")
        await on_delta("与代码的诊断报告")
        return "基于日志与代码的诊断报告"


class FailingDiagnosisGenerator:
    async def generate(self, state, evidence):
        raise RuntimeError("model temporarily unavailable")


class FailingLogClient:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    async def query_trace(
        self,
        trace_id,
        environment,
        time_range_minutes,
        *,
        now_ms=None,
    ):
        self.calls += 1
        raise self.error


class FakeEvidenceEnricher:
    async def enrich(self, state, code_matches):
        return {
            "swagger_operations": [
                {
                    "source_id": "swagger-workflow",
                    "operation_id": "createOrder",
                    "method": "POST",
                    "path": "/orders",
                    "refreshed_at": "2026-07-16T08:00:00+00:00",
                }
            ],
            "document_matches": [
                {
                    "chunk_id": "doc-1",
                    "heading": "订单创建约束",
                    "domain": "workflow",
                    "content": "sanitized contract excerpt",
                    "metadata": {"source_type": "product_document"},
                }
            ],
        }


class FakeConversationContextResolver:
    def __init__(self, context=None):
        self.context = context
        self.calls = []

    async def get_latest_bug_context(self, conversation_id):
        self.calls.append(conversation_id)
        return self.context


def error_entry():
    return LogEntry(
        timestamp="1000",
        level="ERROR",
        logger="OrderService",
        message="sanitized NullPointerException",
        exception_types=("NullPointerException",),
        stack_frames=(
            StackFrame("com.example.OrderService.create", "OrderService.java", 156),
        ),
    )


@pytest.mark.asyncio
async def test_bug_graph_interrupts_and_resumes_same_conversation(tmp_path):
    log_client = FakeLogClient()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=log_client,
            code_retriever=FakeCodeRetriever(),
            log_range_minutes=1440,
        )

        first = await service.diagnose(
            "开发环境接口报错",
            conversation_id="conversation-1",
            run_id="run-1",
        )
        second = await service.diagnose(
            "trace ID 是 trace-resume-123456",
            conversation_id="conversation-1",
            run_id="run-2",
        )

    assert first.status == "clarification_required"
    assert first.missing_fields == ["trace_id"]
    assert second.status == "no_logs"
    assert log_client.calls == [("trace-resume-123456", "develop", 1440, None)]


@pytest.mark.asyncio
async def test_bug_graph_accepts_standalone_uuid_while_waiting_for_trace(tmp_path):
    log_client = FakeLogClient()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=log_client,
            code_retriever=FakeCodeRetriever(),
            log_range_minutes=1440,
        )

        first = await service.diagnose(
            "开发环境接口报错",
            conversation_id="conversation-standalone-trace",
            run_id="run-standalone-1",
        )
        second = await service.diagnose(
            "c141473b-764e-439d-803f-2912da7df986",
            conversation_id="conversation-standalone-trace",
            run_id="run-standalone-2",
        )

    assert first.missing_fields == ["trace_id"]
    assert second.status == "no_logs"
    assert log_client.calls == [
        (
            "c141473b-764e-439d-803f-2912da7df986",
            "develop",
            1440,
            None,
        )
    ]


@pytest.mark.asyncio
async def test_bug_graph_latest_clarification_overrides_stale_environment_options(
    tmp_path,
):
    log_client = FakeLogClient()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=log_client,
            code_retriever=FakeCodeRetriever(),
            log_range_minutes=1440,
        )

        first = await service.diagnose(
            "请确认问题在开发、测试还是生产环境，接口调用报错",
            conversation_id="conversation-stale-options",
            run_id="run-stale-1",
        )
        second = await service.diagnose(
            "开发环境，traceId: trace-latest-123456",
            conversation_id="conversation-stale-options",
            run_id="run-stale-2",
        )

    assert first.status == "clarification_required"
    assert second.status == "no_logs"
    assert log_client.calls == [
        ("trace-latest-123456", "develop", 1440, None)
    ]


@pytest.mark.asyncio
async def test_bug_graph_completed_diagnosis_reuses_context_for_follow_up(tmp_path):
    log_client = FakeLogClient()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=log_client,
            code_retriever=FakeCodeRetriever(),
            log_range_minutes=1440,
        )

        first = await service.diagnose(
            "开发环境，traceId: trace-completed-context-123456",
            conversation_id="conversation-completed-context",
            run_id="run-completed-1",
        )
        second = await service.diagnose(
            "为什么一开始没有从日志平台查到这个报错",
            conversation_id="conversation-completed-context",
            run_id="run-completed-2",
        )

    assert first.status == "no_logs"
    assert second.status == "no_logs"
    assert log_client.calls == [
        ("trace-completed-context-123456", "develop", 1440, None),
        ("trace-completed-context-123456", "develop", 1440, None),
    ]


@pytest.mark.asyncio
async def test_bug_graph_does_not_reuse_context_for_unrelated_new_bug(tmp_path):
    log_client = FakeLogClient()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=log_client,
            code_retriever=FakeCodeRetriever(),
            log_range_minutes=1440,
        )

        await service.diagnose(
            "开发环境，traceId: trace-old-context-123456",
            conversation_id="conversation-new-bug",
            run_id="run-old-bug",
        )
        new_bug = await service.diagnose(
            "另一个接口报错了",
            conversation_id="conversation-new-bug",
            run_id="run-new-bug",
        )

    assert new_bug.status == "clarification_required"
    assert new_bug.missing_fields == ["environment", "trace_id"]
    assert log_client.calls == [
        ("trace-old-context-123456", "develop", 1440, None)
    ]


@pytest.mark.asyncio
async def test_bug_graph_pending_follow_up_recovers_context_from_quality_history(
    tmp_path,
):
    log_client = FakeLogClient()
    resolver = FakeConversationContextResolver(
        {
            "environment": "develop",
            "trace_id": "trace-quality-context-123456",
            "request_time": None,
        }
    )
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=log_client,
            code_retriever=FakeCodeRetriever(),
            context_resolver=resolver,
            log_range_minutes=1440,
        )

        first = await service.diagnose(
            "接口报错了",
            conversation_id="conversation-quality-context",
            run_id="run-quality-1",
        )
        second = await service.diagnose(
            "在上下文中",
            conversation_id="conversation-quality-context",
            run_id="run-quality-2",
        )

    assert first.status == "clarification_required"
    assert second.status == "no_logs"
    assert resolver.calls == ["conversation-quality-context"]
    assert log_client.calls == [
        ("trace-quality-context-123456", "develop", 1440, None)
    ]


@pytest.mark.asyncio
async def test_bug_graph_centers_log_query_on_user_request_time(tmp_path):
    log_client = FakeLogClient()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=log_client,
            code_retriever=FakeCodeRetriever(),
            log_range_minutes=1440,
        )

        result = await service.diagnose(
            "prod traceId trace-time-123456 请求时间 2026-07-16 11:48:34",
            conversation_id="conversation-request-time",
            run_id="run-request-time",
        )

    expected_end = int(
        datetime(
            2026,
            7,
            16,
            12,
            18,
            34,
            tzinfo=timezone(timedelta(hours=8)),
        ).timestamp()
        * 1000
    )
    assert result.status == "no_logs"
    assert log_client.calls == [
        ("trace-time-123456", "prod", 60, expected_end)
    ]


@pytest.mark.asyncio
async def test_bug_graph_expired_interrupt_starts_fresh_intake(tmp_path):
    current = datetime(2026, 7, 16, tzinfo=UTC)

    def now():
        return current

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=FakeLogClient(),
            code_retriever=FakeCodeRetriever(),
            interrupt_ttl_seconds=86400,
            now=now,
        )
        await service.diagnose(
            "开发环境接口报错",
            conversation_id="conversation-expired",
            run_id="run-1",
        )
        current += timedelta(days=2)

        result = await service.diagnose(
            "trace ID 是 trace-fresh-123456",
            conversation_id="conversation-expired",
            run_id="run-2",
        )

    assert result.status == "clarification_required"
    assert result.missing_fields == ["environment"]


@pytest.mark.asyncio
async def test_bug_graph_stops_before_code_search_when_logs_are_empty(tmp_path):
    retriever = FakeCodeRetriever(matches=[{"chunk_id": "must-not-use"}])
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=FakeLogClient(),
            code_retriever=retriever,
        )

        result = await service.diagnose(
            "测试环境 trace ID 是 trace-empty-123456",
            conversation_id="conversation-empty",
            run_id="run-1",
        )

    assert result.status == "no_logs"
    assert result.evidence_grade == "none"
    assert retriever.calls == []
    assert [item["source_type"] for item in result.citations] == ["log_trace"]


@pytest.mark.asyncio
async def test_bug_graph_correlates_logs_and_code_before_generation(tmp_path):
    code_match = {
        "chunk_id": "code-1",
        "heading": "OrderService.create",
        "content": "sanitized code excerpt",
        "domain": "workflow",
        "metadata": {
            "source_type": "code",
            "branch": "develop",
            "relative_path": "service/OrderService.java",
            "start_line": 150,
            "end_line": 170,
        },
    }
    generator = FakeDiagnosisGenerator()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=FakeLogClient([error_entry()]),
            code_retriever=FakeCodeRetriever([code_match]),
            diagnosis_generator=generator,
        )

        result = await service.diagnose(
            "测试环境 trace ID 是 trace-code-123456",
            conversation_id="conversation-code",
            run_id="run-1",
        )

    assert result.status == "completed"
    assert result.answer == "基于日志与代码的诊断报告"
    assert result.evidence_grade == "correlated"
    assert [item["source_type"] for item in result.citations] == ["log_trace", "code"]
    assert generator.calls[0][1]["logs"][0]["message"] == "sanitized NullPointerException"


@pytest.mark.asyncio
async def test_bug_graph_forwards_diagnosis_generator_deltas(tmp_path):
    generator = FakeDiagnosisGenerator()
    deltas: list[str] = []

    async def collect(delta: str) -> None:
        deltas.append(delta)

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=FakeLogClient([error_entry()]),
            code_retriever=FakeCodeRetriever(),
            diagnosis_generator=generator,
        )

        result = await service.diagnose(
            "测试环境 trace ID 是 trace-stream-123456",
            conversation_id="conversation-stream",
            run_id="run-stream",
            on_diagnosis_delta=collect,
        )

    assert result.status == "completed"
    assert deltas == ["基于日志", "与代码的诊断报告"]


@pytest.mark.asyncio
async def test_bug_graph_cancel_deletes_pending_diagnosis(tmp_path):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=FakeLogClient(),
            code_retriever=FakeCodeRetriever(),
        )
        await service.diagnose(
            "生产环境接口异常",
            conversation_id="conversation-cancel",
            run_id="run-1",
        )

        cancelled = await service.diagnose(
            "取消诊断",
            conversation_id="conversation-cancel",
            run_id="run-2",
        )
        fresh = await service.diagnose(
            "trace ID 是 trace-after-cancel-123456",
            conversation_id="conversation-cancel",
            run_id="run-3",
        )

    assert cancelled.status == "cancelled"
    assert fresh.missing_fields == ["environment"]


@pytest.mark.asyncio
async def test_bug_graph_does_not_retry_non_transient_log_errors(tmp_path):
    log_client = FailingLogClient(ValueError("invalid trace query"))
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=log_client,
            code_retriever=FakeCodeRetriever(),
            log_retry_count=2,
        )

        result = await service.diagnose(
            "test trace ID trace-invalid-123456",
            conversation_id="conversation-invalid",
            run_id="run-invalid",
        )

    assert result.status == "unavailable"
    assert log_client.calls == 1


@pytest.mark.asyncio
async def test_bug_graph_retries_retryable_grafana_errors(tmp_path):
    error = GrafanaLogError("temporary upstream failure")
    error.retryable = True

    class RecoveringLogClient(FakeLogClient):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def query_trace(
            self,
            trace_id,
            environment,
            time_range_minutes,
            *,
            now_ms=None,
        ):
            self.attempts += 1
            if self.attempts < 3:
                raise error
            return await super().query_trace(
                trace_id,
                environment,
                time_range_minutes,
                now_ms=now_ms,
            )

    log_client = RecoveringLogClient()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=log_client,
            code_retriever=FakeCodeRetriever(),
            log_retry_count=2,
        )
        result = await service.diagnose(
            "test trace ID trace-retry-123456",
            conversation_id="conversation-retry",
            run_id="run-retry",
        )

    assert result.status == "no_logs"
    assert log_client.attempts == 3


@pytest.mark.asyncio
async def test_bug_graph_grades_contract_evidence_and_adds_typed_citations(tmp_path):
    code_match = {
        "chunk_id": "code-contract-1",
        "heading": "OrderService.create",
        "content": "sanitized code excerpt",
        "domain": "workflow",
        "metadata": {
            "source_type": "code",
            "domain_id": "workflow",
            "branch": "develop",
            "relative_path": "service/OrderService.java",
        },
    }
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=FakeLogClient([error_entry()]),
            code_retriever=FakeCodeRetriever([code_match]),
            evidence_enricher=FakeEvidenceEnricher(),
        )
        result = await service.diagnose(
            "test trace ID trace-contract-123456 POST /orders failed",
            conversation_id="conversation-contract",
            run_id="run-contract",
        )

    assert result.evidence_grade == "contract_supported"
    assert [item["source_type"] for item in result.citations] == [
        "log_trace",
        "code",
        "product_document",
        "swagger",
    ]


@pytest.mark.asyncio
async def test_bug_graph_checkpoint_never_contains_evidence_bodies(tmp_path):
    database = tmp_path / "graph.db"
    private_log = "PRIVATE_LOG_BODY_BEARER_SECRET"
    private_code = "PRIVATE_CODE_BODY_PASSWORD_SECRET"
    entry = LogEntry(
        timestamp="1000",
        level="ERROR",
        logger="OrderService",
        message=private_log,
        exception_types=("OrderException",),
        stack_frames=(StackFrame("OrderService.create", "OrderService.java", 12),),
    )
    match = {
        "chunk_id": "private-code-1",
        "heading": "OrderService.create",
        "content": private_code,
        "domain": "workflow",
        "metadata": {"source_type": "code", "branch": "develop"},
    }
    async with AsyncSqliteSaver.from_conn_string(str(database)) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=FakeLogClient([entry]),
            code_retriever=FakeCodeRetriever([match]),
        )
        await service.diagnose(
            "test trace ID trace-private-123456",
            conversation_id="conversation-private",
            run_id="run-private",
        )

    import sqlite3

    with sqlite3.connect(database) as connection:
        persisted = " ".join(
            str(value)
            for table in ("checkpoints", "writes")
            for row in connection.execute(f"SELECT * FROM {table}").fetchall()
            for value in row
        )
    assert private_log not in persisted
    assert private_code not in persisted


@pytest.mark.asyncio
async def test_bug_graph_falls_back_when_diagnosis_model_fails(tmp_path):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=FakeLogClient([error_entry()]),
            code_retriever=FakeCodeRetriever(),
            diagnosis_generator=FailingDiagnosisGenerator(),
        )
        result = await service.diagnose(
            "test trace ID trace-model-fallback-123456",
            conversation_id="conversation-model-fallback",
            run_id="run-model-fallback",
        )

    assert result.status == "completed"
    assert result.evidence_grade == "log_only"
    assert "代码根因" in result.answer
    assert service._evidence == {}


@pytest.mark.asyncio
async def test_bug_graph_public_lifecycle_clears_ephemeral_evidence(tmp_path):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=FakeLogClient(),
            code_retriever=FakeCodeRetriever(),
        )
        await service.start()
        service._evidence["run-private"] = {"logs": "private body"}

        await service.close()

    assert service._evidence == {}


@pytest.mark.asyncio
async def test_bug_graph_node_audit_excludes_inputs_and_evidence(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="knowledge.bug_graph.service")
    private_trace = "trace-audit-private-123456"
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=FakeLogClient(),
            code_retriever=FakeCodeRetriever(),
        )
        await service.diagnose(
            f"test trace ID {private_trace}",
            conversation_id="conversation-audit",
            run_id="run-audit",
        )

    serialized = "\n".join(record.getMessage() for record in caplog.records)
    assert "node=query_trace_logs" in serialized
    assert "status=completed" in serialized
    assert "duration_ms=" in serialized
    assert private_trace not in serialized


@pytest.mark.asyncio
async def test_bug_graph_checkpoint_keeps_only_public_code_metadata_on_failure(tmp_path):
    database = tmp_path / "graph.db"
    private_metadata = "PRIVATE_IMPORT_AND_BM25_BODY"
    match = {
        "chunk_id": "code-public-state-1",
        "heading": "OrderService.create",
        "content": "PRIVATE_CODE_BODY",
        "domain": "workflow",
        "metadata": {
            "source_type": "code",
            "branch": "develop",
            "relative_path": "service/OrderService.java",
            "symbol_name": "OrderService.create",
            "imports": private_metadata,
            "bm25_keywords": private_metadata,
        },
    }
    async with AsyncSqliteSaver.from_conn_string(str(database)) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=FakeLogClient([error_entry()]),
            code_retriever=FailingEnrichmentRetriever([match]),
        )
        with pytest.raises(RuntimeError, match="enrichment failed"):
            await service.diagnose(
                "test trace ID trace-state-privacy-123456",
                conversation_id="conversation-state-privacy",
                run_id="run-state-privacy",
            )

    import sqlite3

    with sqlite3.connect(database) as connection:
        persisted = " ".join(
            str(value)
            for table in ("checkpoints", "writes")
            for row in connection.execute(f"SELECT * FROM {table}").fetchall()
            for value in row
        )
    assert private_metadata not in persisted
    assert "PRIVATE_CODE_BODY" not in persisted


@pytest.mark.asyncio
async def test_bug_graph_bounds_code_evidence_before_diagnosis_model(tmp_path):
    generator = FakeDiagnosisGenerator()
    long_code = "x" * 12000
    match = {
        "chunk_id": "code-long-1",
        "heading": "OrderService.create",
        "content": long_code,
        "domain": "workflow",
        "metadata": {"source_type": "code", "branch": "develop"},
    }
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "graph.db")) as saver:
        service = BugDiagnosisGraphService(
            checkpointer=saver,
            log_client=FakeLogClient([error_entry()]),
            code_retriever=FakeCodeRetriever([match]),
            diagnosis_generator=generator,
        )
        await service.diagnose(
            "test trace ID trace-bounded-123456",
            conversation_id="conversation-bounded",
            run_id="run-bounded",
        )

    model_code = generator.calls[0][1]["code"][0]["content"]
    assert len(model_code) <= 6000
