from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from knowledge.quality import (
    QualityAnnotationCreate,
    QualitySpanCreate,
    CitationSnapshot,
    EvalCaseCreate,
    QualityRepository,
    ToolRunSnapshot,
    TurnCompletion,
    TurnStart,
)


@pytest.mark.asyncio
async def test_quality_repository_persists_spans_annotations_and_analytics(tmp_path):
    repository = QualityRepository(tmp_path / "quality-v2.db")
    await repository.initialize()
    turn = await repository.start_turn(
        TurnStart(
            run_id="run-quality-v2",
            channel="codex",
            question="审批流管理员转办接口是什么？",
            provider="deepseek",
            model_name="deepseek-v4-flash",
        )
    )
    await repository.complete_turn(
        turn.run_id,
        TurnCompletion(
            status="completed",
            answer="代码中存在管理员转办接口。",
            domain_id="approval-flow",
            routed_domains=["approval-flow"],
            specialists_used=["approval_flow_expert"],
            duration_ms=1200,
            tools=[
                ToolRunSnapshot(
                    tool_call_id="tool-1",
                    tool_name="collect_domain_evidence",
                    agent_name="approval_flow_expert",
                    status="completed",
                )
            ],
            citations=[CitationSnapshot(source_type="code", source_id="chunk-1")],
        ),
    )
    span = await repository.record_span(
        QualitySpanCreate(
            turn_id=turn.id,
            run_id=turn.run_id,
            kind="llm",
            name="approval_answer",
            status="completed",
            duration_ms=700,
            input_tokens=100,
            output_tokens=50,
            metadata={"prompt": "must not persist", "model": "deepseek-v4-flash"},
        )
    )
    annotation = await repository.create_annotation(
        QualityAnnotationCreate(
            turn_id=turn.id,
            source="rule",
            code="duplicate_tool",
            severity="warning",
            confidence=0.95,
            details={"count": 2, "content": "must not persist"},
        )
    )
    await repository.upsert_feedback(
        turn_id=turn.id,
        feedback_token=turn.feedback_token,
        rating="negative",
        reason_code="too_slow",
        reason="回答太慢",
    )

    loaded = await repository.get_turn(turn.id)
    assert loaded.channel == "codex"
    assert loaded.routed_domains == ["approval-flow"]
    assert loaded.specialists_used == ["approval_flow_expert"]
    assert loaded.feedback[0].reason_code == "too_slow"
    assert span.metadata == {"model": "deepseek-v4-flash"}
    assert annotation.details == {"count": 2}

    annotations = await repository.list_annotations(code="duplicate_tool")
    assert annotations.total == 1
    reviewed = await repository.update_annotation_review(
        annotation.id, review_status="confirmed", reviewer="admin"
    )
    assert reviewed.review_status == "confirmed"

    analytics = await repository.get_analytics(channel="codex")
    assert analytics.total_turns == 1
    assert analytics.completed_turns == 1
    assert analytics.citation_coverage == 1.0
    assert analytics.average_tool_calls == 1.0
    assert analytics.feedback_rate == 1.0
    assert analytics.p50_duration_ms == 1200
    assert analytics.p90_duration_ms == 1200


@pytest.mark.asyncio
async def test_quality_repository_initializes_and_persists_complete_turn(tmp_path):
    database = tmp_path / "agent_quality.db"
    repository = QualityRepository(database)
    await repository.initialize()
    await repository.initialize()

    started = await repository.start_turn(
        TurnStart(
            run_id="run-1",
            conversation_id="conversation-1",
            channel="web",
            channel_message_id=None,
            user_id=None,
            user_name=None,
            chat_id=None,
            question="原始问题 token=用户主动输入",
            knowledge_space_id="middle-platform",
            domain_id="metric-platform",
            provider="deepseek",
            model_name="deepseek-chat",
            application_version="0.1.0",
            prompt_version="v1",
        )
    )
    duplicate = await repository.start_turn(
        TurnStart(run_id="run-1", question="不会覆盖", channel="web")
    )

    assert duplicate.id == started.id
    assert duplicate.question == "原始问题 token=用户主动输入"
    assert started.feedback_token

    completed = await repository.complete_turn(
        "run-1",
        TurnCompletion(
            status="completed",
            answer="最终公开回答",
            last_agent="Manager Agent",
            duration_ms=123.5,
            tools=[
                ToolRunSnapshot(
                    tool_call_id="call-1",
                    tool_name="search_domain_code",
                    agent_name="workflow_expert",
                    status="completed",
                    duration_ms=25.0,
                    arguments={"branch": "develop", "body": "must-not-store"},
                )
            ],
            citations=[
                CitationSnapshot(
                    source_type="code",
                    source_id="chunk-1",
                    title="OrderService.query",
                    domain="workflow",
                    metadata={"branch": "develop", "content": "must-not-store"},
                )
            ],
        ),
    )

    assert completed.answer == "最终公开回答"
    assert completed.status == "completed"
    assert completed.duration_ms == 123.5
    detail = await repository.get_turn(started.id)
    assert [item.tool_name for item in detail.tools] == ["search_domain_code"]
    assert detail.tools[0].arguments == {"branch": "develop"}
    assert detail.citations[0].metadata == {"branch": "develop"}

    async with aiosqlite.connect(database) as connection:
        migrations = await (
            await connection.execute("SELECT version FROM quality_schema_migrations")
        ).fetchall()
        journal_mode = await (await connection.execute("PRAGMA journal_mode")).fetchone()
    assert migrations == [(1,), (2,), (3,), (4,), (5,), (6,), (7,)]
    assert journal_mode[0].lower() == "wal"


@pytest.mark.asyncio
async def test_quality_repository_returns_latest_log_trace_context(tmp_path):
    repository = QualityRepository(tmp_path / "agent_quality.db")
    await repository.initialize()
    await repository.start_turn(
        TurnStart(
            run_id="run-log-context",
            conversation_id="conversation-log-context",
            channel="web",
            question="开发环境接口报错",
        )
    )
    await repository.complete_turn(
        "run-log-context",
        TurnCompletion(
            status="completed",
            citations=[
                CitationSnapshot(
                    source_type="log_trace",
                    source_id="trace-history-123456",
                    title="日志 Trace",
                    domain="develop",
                    metadata={
                        "environment": "develop",
                        "from_ms": 1000,
                        "to_ms": 2000,
                    },
                )
            ],
        ),
    )

    context = await repository.get_latest_bug_context(
        "conversation-log-context"
    )

    assert context == {
        "environment": "develop",
        "trace_id": "trace-history-123456",
        "request_time": None,
    }


@pytest.mark.asyncio
async def test_quality_repository_filters_feedback_eval_cases_and_cascades(tmp_path):
    repository = QualityRepository(tmp_path / "agent_quality.db")
    await repository.initialize()
    first = await repository.start_turn(
        TurnStart(
            run_id="run-1",
            channel="feishu",
            channel_message_id="om-user-1",
            user_id="ou-user-1",
            user_name="张三",
            question="审批流报错",
            domain_id="approval-flow",
        )
    )
    await repository.complete_turn(
        "run-1", TurnCompletion(status="error", error_type="TimeoutError")
    )
    await repository.start_turn(
        TurnStart(run_id="run-2", channel="web", question="指标定义")
    )

    feedback = await repository.upsert_feedback(
        turn_id=first.id,
        feedback_token=first.feedback_token,
        rating="negative",
        reason="回答不准确",
        user_id="ou-user-1",
        user_name="张三",
        channel="feishu",
    )
    updated = await repository.upsert_feedback(
        turn_id=first.id,
        feedback_token=first.feedback_token,
        rating="positive",
        reason="修正后正确",
        user_id="ou-user-1",
        user_name="张三",
        channel="feishu",
    )
    assert updated.id == feedback.id
    assert updated.rating == "positive"

    page = await repository.list_turns(
        page=1,
        page_size=10,
        channel="feishu",
        status="error",
        rating="positive",
        query="审批流",
    )
    assert page.total == 1
    assert page.items[0].user_name == "张三"

    case = await repository.create_eval_case(
        EvalCaseCreate(
            source_turn_id=first.id,
            name="审批流超时定位",
            question=first.question,
            knowledge_space_id="middle-platform",
            domain_id="approval-flow",
            required_tools=["bug_diagnosis_expert"],
            required_citation_types=["log_trace", "code"],
            required_facts=["超时"],
            forbidden_facts=["银行卡密码"],
            tags=["bug", "approval"],
        )
    )
    assert (await repository.list_eval_cases())[0].id == case.id

    await repository.delete_turn(first.id)
    assert await repository.get_turn(first.id) is None
    assert await repository.list_eval_cases() == []


@pytest.mark.asyncio
async def test_quality_repository_recovers_stale_running_turns(tmp_path):
    repository = QualityRepository(tmp_path / "agent_quality.db")
    await repository.initialize()
    turn = await repository.start_turn(
        TurnStart(run_id="run-stale", channel="api", question="中断请求")
    )
    stale_time = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    async with aiosqlite.connect(repository.database_path) as connection:
        await connection.execute(
            "UPDATE quality_turns SET created_at=?, updated_at=? WHERE id=?",
            (stale_time, stale_time, turn.id),
        )
        await connection.commit()

    recovered = await repository.recover_stale_running(60)

    assert recovered == 1
    assert (await repository.get_turn(turn.id)).status == "interrupted"


@pytest.mark.asyncio
async def test_quality_completion_backfills_inferred_domain(tmp_path):
    repository = QualityRepository(tmp_path / "agent_quality.db")
    await repository.initialize()
    turn = await repository.start_turn(
        TurnStart(
            run_id="run-inferred-domain",
            channel="web",
            question="审批流转交接口更新了吗",
            domain_id=None,
        )
    )

    await repository.complete_turn(
        "run-inferred-domain",
        TurnCompletion(
            status="completed",
            answer="审批流回答",
            domain_id="approval-flow",
        ),
    )

    completed = await repository.get_turn(turn.id)
    assert completed.domain_id == "approval-flow"


@pytest.mark.asyncio
async def test_quality_initialization_backfills_historical_single_domain_tools(tmp_path):
    repository = QualityRepository(tmp_path / "agent_quality.db")
    await repository.initialize()
    approval = await repository.start_turn(
        TurnStart(run_id="run-old-approval", channel="web", question="审批接口")
    )
    cross = await repository.start_turn(
        TurnStart(run_id="run-old-cross", channel="web", question="审批触发工作流")
    )
    await repository.complete_turn(
        "run-old-approval",
        TurnCompletion(
            status="completed",
            tools=[
                ToolRunSnapshot(
                    tool_call_id="approval",
                    tool_name="approval_flow_expert",
                    agent_name="Manager Agent",
                    status="completed",
                )
            ],
        ),
    )
    await repository.complete_turn(
        "run-old-cross",
        TurnCompletion(
            status="completed",
            tools=[
                ToolRunSnapshot(
                    tool_call_id="approval-cross",
                    tool_name="approval_flow_expert",
                    agent_name="Manager Agent",
                    status="completed",
                ),
                ToolRunSnapshot(
                    tool_call_id="workflow-cross",
                    tool_name="workflow_expert",
                    agent_name="Manager Agent",
                    status="completed",
                ),
            ],
        ),
    )

    await repository.initialize()

    assert (await repository.get_turn(approval.id)).domain_id == "approval-flow"
    assert (await repository.get_turn(cross.id)).domain_id is None


@pytest.mark.asyncio
async def test_quality_repository_persists_eval_behavior_and_budgets(tmp_path):
    repository = QualityRepository(tmp_path / "quality.db")
    await repository.initialize()

    created = await repository.create_eval_case(
        EvalCaseCreate(
            name="指标候选确认",
            question="帮我查销售额",
            expected_behavior="clarify",
            max_latency_ms=30_000,
            max_tool_calls=4,
            max_citations=8,
        )
    )
    loaded = await repository.get_eval_case(created.id)

    assert loaded is not None
    assert loaded.expected_behavior == "clarify"
    assert loaded.max_latency_ms == 30_000
    assert loaded.max_tool_calls == 4
    assert loaded.max_citations == 8

    await repository.initialize()
    async with aiosqlite.connect(repository.database_path) as connection:
        columns = {
            row[1]
            for row in await (
                await connection.execute("PRAGMA table_info(eval_cases)")
            ).fetchall()
        }
    assert {
        "expected_behavior",
        "max_latency_ms",
        "max_tool_calls",
        "max_citations",
    }.issubset(columns)


@pytest.mark.asyncio
async def test_quality_repository_versions_eval_cases_and_queues_runs(tmp_path):
    repository = QualityRepository(tmp_path / "quality-v2.db")
    await repository.initialize()
    created = await repository.create_eval_case(
        EvalCaseCreate(
            name="Bug 多轮补充",
            question="接口报错",
            turns=["接口报错", "开发环境", "traceId abc-123"],
            task_type="bug",
            suite="critical",
            priority="critical",
            approval_state="approved",
            required_facts=["根因"],
        )
    )
    assert created.version == 1
    assert created.turns[-1] == "traceId abc-123"
    assert created.suite == "critical"
    updated = await repository.update_eval_case(
        created.id,
        EvalCaseCreate(
            name=created.name,
            question=created.question,
            turns=created.turns,
            task_type=created.task_type,
            suite=created.suite,
            priority=created.priority,
            approval_state=created.approval_state,
            required_facts=["根因", "代码位置"],
        ),
    )
    assert updated.version == 2

    run = await repository.create_eval_run(
        total_cases=1,
        application_version="0.2.0",
        provider="deepseek",
        model_name="deepseek-v4-flash",
        status="queued",
        case_ids=[created.id],
        config_snapshot={"case_timeout_seconds": 120},
    )
    assert run.status == "queued"
    assert run.case_ids == [created.id]
    claimed = await repository.claim_next_eval_run()
    assert claimed.id == run.id
    assert claimed.status == "running"
    cancelled = await repository.request_eval_run_cancel(run.id)
    assert cancelled.cancel_requested is True
