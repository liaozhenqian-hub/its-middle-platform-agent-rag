from knowledge.agent_runtime.context import AgentRunContext


def test_run_context_collects_public_citation_without_chunk_content():
    context = AgentRunContext(conversation_id="conversation-1", run_id="run-1")

    context.add_knowledge_citation(
        chunk_id="chunk-7",
        heading="指标应用查询",
        domain="指标平台",
        metadata={"module": "metric-app", "content": "must-not-leak"},
    )

    citation = context.citations[0]
    assert citation.source_type == "knowledge_chunk"
    assert citation.source_id == "chunk-7"
    assert citation.metadata == {"module": "metric-app"}
    assert "content" not in context.to_dict()["citations"][0]


def test_run_context_round_trips_serializable_audit_state():
    context = AgentRunContext(
        conversation_id="conversation-1",
        run_id="run-1",
        response_mode="clarification",
        response_override="请补充 trace ID。",
    )
    context.add_mcp_citation("searchBizMetric", {"metric_type": "biz"})
    context.start_tool("call-1", "searchBizMetric", "指标平台专家", {"query": "收入"})
    context.finish_tool("call-1", status="completed", duration_ms=12.5)

    restored = AgentRunContext.from_dict(context.to_dict())

    assert restored == context
    assert restored.tool_runs[0].status == "completed"
    assert restored.tool_runs[0].duration_ms == 12.5
    assert restored.response_mode == "clarification"
    assert restored.response_override == "请补充 trace ID。"


def test_run_context_redacts_common_secret_and_payload_key_variants():
    context = AgentRunContext(conversation_id="conversation-1", run_id="run-1")
    context.add_mcp_citation(
        "searchBizMetric",
        {
            "access_token": "secret-1",
            "apiKey": "secret-2",
            "authorization_header": "secret-3",
            "model_output": "large-response",
            "metric_type": "biz",
        },
    )

    serialized = str(context.to_dict())
    assert "secret-1" not in serialized
    assert "secret-2" not in serialized
    assert "secret-3" not in serialized
    assert "large-response" not in serialized
    assert context.citations[0].metadata == {"metric_type": "biz"}


def test_run_context_emits_typed_code_document_and_swagger_citations():
    context = AgentRunContext(
        conversation_id="conversation-1",
        run_id="run-1",
        knowledge_space_id="middle-platform",
        domain_id="metric-platform",
    )
    context.add_knowledge_citation(
        chunk_id="code-1",
        heading="MetricService.queryMetric",
        domain="指标平台",
        metadata={
            "source_type": "code",
            "relative_path": "server/metric/MetricService.java",
            "commit_sha": "abc123",
            "symbol_name": "MetricService.queryMetric",
            "start_line": 10,
            "end_line": 20,
            "gitlab_url": "https://gitlab.example/project/-/blob/abc123/file#L10-20",
            "content": "must-not-enter-citation",
        },
    )
    context.add_knowledge_citation(
        chunk_id="doc-1",
        heading="指标口径",
        domain="指标平台",
        metadata={
            "source_type": "product_document",
            "relative_path": "docs/metric.docx",
            "page_number": 3,
            "source_version": "v2",
        },
    )
    context.add_swagger_citation(
        source_id="swagger-1",
        domain="指标平台",
        operation={
            "operation_id": "getMetric",
            "method": "GET",
            "path": "/api/metrics/{id}",
        },
        refreshed_at="2026-07-15T00:00:00+00:00",
        stale=False,
    )

    assert [citation.source_type for citation in context.citations] == [
        "code",
        "product_document",
        "swagger",
    ]
    assert context.citations[0].metadata["start_line"] == 10
    assert "content" not in context.citations[0].metadata
    assert context.citations[2].source_id == "swagger-1:getMetric"


def test_swagger_citation_uses_method_and_path_when_operation_id_is_missing():
    context = AgentRunContext("conversation-1", "run-1")

    context.add_swagger_citation(
        source_id="swagger-1",
        domain="指标平台",
        operation={"operation_id": "", "method": "GET", "path": "/metrics/{id}"},
        refreshed_at="2026-07-15T00:00:00+00:00",
        stale=False,
    )

    citation = context.citations[0]
    assert citation.source_id == "swagger-1:GET:/metrics/{id}"
    assert citation.title == "GET /metrics/{id}"


def test_run_context_adds_log_trace_citation_without_raw_log_content():
    context = AgentRunContext(conversation_id="conversation-1", run_id="run-1")

    context.add_log_trace_citation(
        trace_id="trace-abc-123",
        environment="test",
        from_ms=1721091600000,
        to_ms=1721093400000,
        log_count=3,
        exception_types=["NullPointerException"],
        truncated=False,
        entries=[{"message": "must-not-enter-public-citation"}],
    )

    citation = context.citations[0]
    assert citation.source_type == "log_trace"
    assert citation.source_id == "trace-abc-123"
    assert citation.domain == "中台"
    assert citation.metadata == {
        "environment": "test",
        "from_ms": 1721091600000,
        "to_ms": 1721093400000,
        "log_count": 3,
        "exception_types": ["NullPointerException"],
        "truncated": False,
    }
    assert "must-not-enter-public-citation" not in str(context.to_dict())
