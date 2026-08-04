# PostgreSQL 问答链路延迟优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 PostgreSQL/Telepresence 下连接池与质量记录串行写入造成的问答首包和终止事件长时间阻塞。

**Architecture:** 保留统一 PostgreSQL 仓储与 pgvector，先恢复可复用的连接池，再为质量跨度提供单事务批量接口。SSE 将终止事件先交付用户，质量完成由应用级受控任务异步持久化，关闭服务时统一收尾。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2、asyncpg、PostgreSQL、pytest、httpx SSE

---

### Task 1: 批量持久化质量跨度

**Files:**
- Modify: `tests/test_quality_repository.py`
- Modify: `knowledge/quality/repository.py`

- [ ] **Step 1: 写入批量跨度失败测试**

在 `tests/test_quality_repository.py` 增加测试，创建两个 `QualitySpanCreate`，调用期望存在的 `record_spans`，断言返回顺序、名称和数据库行数均正确：

```python
@pytest.mark.asyncio
async def test_quality_repository_records_spans_as_one_batch(tmp_path):
    repository = QualityRepository(tmp_path / "quality-batch.db")
    await repository.initialize()
    turn = await repository.start_turn(
        TurnStart(run_id="run-batch", channel="codex", question="审批接口")
    )
    created = await repository.record_spans([
        QualitySpanCreate(
            turn_id=turn.id, run_id=turn.run_id, kind="llm",
            name="审批流专家", status="completed", duration_ms=10,
        ),
        QualitySpanCreate(
            turn_id=turn.id, run_id=turn.run_id, kind="tool",
            name="collect_domain_evidence", status="completed", duration_ms=20,
        ),
    ])
    assert [item.name for item in created] == [
        "审批流专家", "collect_domain_evidence"
    ]
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `pytest -q tests/test_quality_repository.py::test_quality_repository_records_spans_as_one_batch`

Expected: FAIL，提示 `QualityRepository` 没有 `record_spans`。

- [ ] **Step 3: 实现最小批量仓储接口**

在 `knowledge/quality/repository.py` 中实现：

```python
async def record_spans(
    self, values: list[QualitySpanCreate]
) -> list[QualitySpan]:
    if not values:
        return []
    for value in values:
        if value.kind not in {"agent", "llm", "tool", "graph"}:
            raise ValueError("unsupported quality span kind")
    rows = [(str(uuid4()), value, self._now()) for value in values]

    async def operation(database):
        for span_id, value, now in rows:
            await database.execute(
                """INSERT INTO quality_spans(
                    id, turn_id, run_id, kind, name, status, duration_ms,
                    input_tokens, output_tokens, total_tokens, metadata_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    span_id, value.turn_id, value.run_id, value.kind, value.name,
                    value.status, value.duration_ms, max(0, value.input_tokens),
                    max(0, value.output_tokens), max(0, value.total_tokens),
                    self._json(self._sanitize_audit(value.metadata)), now,
                ),
            )

    await self._write(operation)
    placeholders = ",".join("?" for _ in rows)
    async with self._connect() as database:
        stored = await (
            await database.execute(
                f"SELECT * FROM quality_spans WHERE id IN ({placeholders})",
                [item[0] for item in rows],
            )
        ).fetchall()
    by_id = {row["id"]: self._span_from_row(row) for row in stored}
    return [by_id[item[0]] for item in rows]

async def record_span(self, value: QualitySpanCreate) -> QualitySpan:
    return (await self.record_spans([value]))[0]
```

- [ ] **Step 4: 运行仓储测试并确认 GREEN**

Run: `pytest -q tests/test_quality_repository.py tests/test_postgres_runtime_repositories.py`

Expected: PASS。

- [ ] **Step 5: 提交批量仓储改造**

```bash
git add tests/test_quality_repository.py knowledge/quality/repository.py
git commit -m "perf: batch quality span persistence"
```

### Task 2: 质量服务每次完成只调用一次批量接口

**Files:**
- Modify: `tests/test_quality_capture.py`
- Modify: `knowledge/quality/service.py`

- [ ] **Step 1: 写入单次批量调用失败测试**

在 `tests/test_quality_capture.py` 增加一个记录调用次数的真实仓储子类，并断言包含 Agent 与 Tool 的完成操作只调用一次 `record_spans`，没有调用逐条接口：

```python
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
        TurnStart(run_id="run-batch-capture", channel="codex", question="审批接口")
    )
    await capture.complete(
        turn.run_id,
        TurnCompletion(
            status="completed", last_agent="审批流专家", duration_ms=100,
            tools=[ToolRunSnapshot(
                tool_call_id="one", tool_name="collect_domain_evidence",
                agent_name="审批流专家", status="completed", duration_ms=20,
            )],
        ),
    )
    assert repository.batch_calls == 1
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `pytest -q tests/test_quality_capture.py::test_quality_capture_records_completion_spans_in_one_batch`

Expected: FAIL，`batch_calls` 为跨度数量而不是 1，或批量接口尚未被调用。

- [ ] **Step 3: 将完成跨度改为一次批量调用**

在 `knowledge/quality/service.py` 中将逐条循环替换为：

```python
if not spans:
    return
try:
    await self.repository.record_spans(spans)
except Exception as exc:
    logger.warning(
        "Quality span batch failed turn_id=%s count=%s error_type=%s",
        turn.id, len(spans), type(exc).__name__,
    )
```

- [ ] **Step 4: 运行质量服务测试并确认 GREEN**

Run: `pytest -q tests/test_quality_capture.py tests/test_quality_repository.py`

Expected: PASS。

- [ ] **Step 5: 提交服务层改造**

```bash
git add tests/test_quality_capture.py knowledge/quality/service.py
git commit -m "perf: record completion spans in one batch"
```

### Task 3: SSE 终止事件与质量完成解耦

**Files:**
- Modify: `tests/test_agent_quality_capture.py`
- Modify: `tests/test_app_lifespan.py`
- Modify: `knowledge/api/app.py`

- [ ] **Step 1: 写入慢质量服务的 SSE 顺序失败测试**

在 `tests/test_agent_quality_capture.py` 新增 `BlockingQualityCapture`，其 `complete` 等待测试事件；启动流请求后断言在解除该事件之前已经收到 `run.completed`：

```python
import asyncio


class BlockingQualityCapture(FakeQualityCapture):
    def __init__(self):
        super().__init__()
        self.release = asyncio.Event()
        self.completed = asyncio.Event()

    async def complete(self, run_id, value):
        await self.release.wait()
        self.completed.set()


@pytest.mark.asyncio
async def test_stream_terminal_event_does_not_wait_for_quality_completion():
    capture = BlockingQualityCapture()
    application = create_app(
        agent_service=FakeAgentService(capture.events),
        quality_capture_service=capture,
    )
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST", "/api/v1/agent/chat/stream",
            json={"message": "审批接口", "conversation_id": "conv-fast-terminal"},
        ) as response:
            lines = response.aiter_lines()
            while True:
                line = await asyncio.wait_for(anext(lines), timeout=1)
                if "run.completed" in line:
                    break
            assert not capture.completed.is_set()
            capture.release.set()
            await asyncio.wait_for(capture.completed.wait(), timeout=1)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `pytest -q tests/test_agent_quality_capture.py::test_stream_terminal_event_does_not_wait_for_quality_completion`

Expected: 测试阻塞或超时，因为当前实现先等待 `capture.complete` 才发送终止事件。

- [ ] **Step 3: 增加应用级质量后台任务管理**

在 `knowledge/api/app.py` lifespan 初始化：

```python
application.state.quality_completion_tasks = set()
```

新增调度函数：

```python
def _schedule_quality_completion(coroutine) -> None:
    tasks = application.state.quality_completion_tasks
    task = asyncio.create_task(coroutine, name="quality-turn-completion")
    tasks.add(task)
    task.add_done_callback(tasks.discard)
```

SSE 终止分支改为构造完成协程、调度后立即 `yield _sse(event)`，不在终止事件前 `await`。后台函数内部沿用 `_complete_quality_turn` 的脱敏异常处理。

- [ ] **Step 4: lifespan 关闭时收尾任务**

在应用清理阶段复制任务集合并等待最多 10 秒：

```python
tasks = list(application.state.quality_completion_tasks)
if tasks:
    done, pending = await asyncio.wait(tasks, timeout=10)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
```

- [ ] **Step 5: 运行 API 与 lifespan 测试并确认 GREEN**

Run: `pytest -q tests/test_agent_quality_capture.py tests/test_agent_api.py tests/test_app_lifespan.py`

Expected: PASS，且测试结束没有 pending task 警告。

- [ ] **Step 6: 提交 SSE 解耦改造**

```bash
git add tests/test_agent_quality_capture.py tests/test_app_lifespan.py knowledge/api/app.py
git commit -m "perf: decouple SSE completion from quality persistence"
```

### Task 4: 本地连接池切换与实测验收

**Files:**
- Modify local only: `.env`
- Verify: `.env.example`
- Verify: `docs/superpowers/specs/2026-07-30-postgres-agent-latency-optimization-design.md`

- [ ] **Step 1: 只修改四个非敏感连接池键**

使用补丁将本地 `.env` 四项改为：

```dotenv
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=5
DATABASE_POOL_TIMEOUT_SECONDS=10
DATABASE_POOL_RECYCLE_SECONDS=1800
```

不得读取、输出、提交或改动 `.env` 中其他值。

- [ ] **Step 2: 运行配置和目标回归测试**

Run:

```bash
pytest -q tests/test_persistence_settings.py tests/test_quality_repository.py \
  tests/test_quality_capture.py tests/test_agent_api.py tests/test_app_lifespan.py \
  tests/test_postgres_runtime_repositories.py
```

Expected: PASS。

- [ ] **Step 3: 检查活动任务并重启单 Worker**

确认同步、评测和记忆队列没有 `queued` 或 `running` 后，停止旧进程；使用 PostgreSQL 工作树和 `.venv-agent` 隐藏启动一个 `knowledge.run_api` 进程。不得同时保留两个 Uvicorn Worker 或两个飞书长连接。

- [ ] **Step 4: 验证 readiness**

Run: `curl http://127.0.0.1:8000/health/ready`

Expected: `database/postgres`、`vector_store/pgvector`、`agent_quality`、`worker` 为 `available`，向量数量保持 39,845 或与重启前快照一致。

- [ ] **Step 5: 重放同一脱敏问题并记录阶段耗时**

使用诊断客户端重放“审批实例详情接口上的 operationSource 参数的枚举值有哪些”，只记录事件名、耗时、工具数与引用数，不输出回答正文、内部 chunk ID 或证据正文。

Expected:

- SSE 首事件不再等待约 35 秒；
- `run.completed` 不再额外等待约 105 秒；
- 回答状态为 completed；
- 至少包含有效引用；
- PostgreSQL、pgvector 和后台 Worker 仍然 available。

- [ ] **Step 6: 运行最终差异和敏感信息检查**

Run:

```bash
git diff --check
git status --short
```

确认 `.env` 未被跟踪，提交内容不包含 DSN、密码、Token、API Key、原始日志、代码正文或 Embedding。
