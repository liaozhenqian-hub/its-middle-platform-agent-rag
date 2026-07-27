# Manager 混合推理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Flash Manager 继续稳定调用工具，只在有证据的复杂跨领域回答阶段使用无工具的 DeepSeek Pro 思考模式进行综合。

**Architecture:** 新增独立 `ManagerReasoningSynthesizer`，内部持有无工具 Agent，并使用现有 reasoning model 与 thinking run config。`AgentService` 根据路由领域、实际专家数、引用和响应模式执行确定性门禁；综合失败时保留原 Flash 回答，最终仍经过现有证据策略和公开内容清理。

**Tech Stack:** Python 3.11、OpenAI Agents SDK、DeepSeek OpenAI-compatible API、FastAPI、Pydantic Settings、pytest。

---

### Task 1: 配置与模型装配

**Files:**
- Modify: `knowledge/config/settings.py`
- Modify: `.env.example`
- Test: `tests/test_settings.py`

- [ ] **Step 1: 编写失败测试**

```python
def test_manager_reasoning_defaults():
    settings = Settings(_env_file=None)
    assert settings.agent_manager_reasoning_enabled is True
    assert settings.agent_manager_reasoning_timeout_seconds == 60
```

- [ ] **Step 2: 运行测试并确认因字段不存在而失败**

Run: `.\.venv-agent\Scripts\python.exe -m pytest -q tests\test_settings.py::test_manager_reasoning_defaults`

Expected: FAIL，提示 `Settings` 没有 `agent_manager_reasoning_enabled`。

- [ ] **Step 3: 增加受约束配置**

```python
agent_manager_reasoning_enabled: bool = Field(
    default=True,
    alias="AGENT_MANAGER_REASONING_ENABLED",
)
agent_manager_reasoning_timeout_seconds: float = Field(
    default=60.0,
    gt=0,
    le=180,
    alias="AGENT_MANAGER_REASONING_TIMEOUT_SECONDS",
)
```

在 `.env.example` 的 Agent 配置区补充相同变量，不修改真实 `.env` 或任何密钥。

- [ ] **Step 4: 运行配置测试**

Run: `.\.venv-agent\Scripts\python.exe -m pytest -q tests\test_settings.py`

Expected: PASS。

### Task 2: 实现无工具 Pro 综合器

**Files:**
- Create: `knowledge/agent_runtime/reasoning_synthesis.py`
- Create: `tests/test_manager_reasoning_synthesis.py`

- [ ] **Step 1: 编写综合器失败测试**

```python
@pytest.mark.asyncio
async def test_synthesizer_uses_reasoning_run_config_without_tools():
    synthesizer = ManagerReasoningSynthesizer(
        model="pro-model",
        run_config_factory=fake_run_config,
        timeout_seconds=60,
        runner=runner,
    )
    answer = await synthesizer.synthesize(request)
    assert answer == "综合后的答案"
    assert synthesizer.agent.tools == []
    assert runner.kwargs["run_config"] == "thinking-enabled"
```

同时覆盖输入只包含 `question`、`draft`、`domains` 和脱敏后的 citation 摘要，不包含 `source_id`、chunk ID 或私有 metadata。

- [ ] **Step 2: 运行测试并确认模块不存在**

Run: `.\.venv-agent\Scripts\python.exe -m pytest -q tests\test_manager_reasoning_synthesis.py`

Expected: FAIL，提示无法导入 `reasoning_synthesis`。

- [ ] **Step 3: 实现最小综合器**

```python
@dataclass(frozen=True)
class ReasoningSynthesisRequest:
    question: str
    draft: str
    domains: tuple[str, ...]
    citations: tuple[Citation, ...]
    conversation_id: str

class ManagerReasoningSynthesizer:
    async def synthesize(self, request: ReasoningSynthesisRequest) -> str:
        async with asyncio.timeout(self.timeout_seconds):
            result = await self.runner.run(
                self.agent,
                self._build_input(request),
                max_turns=1,
                run_config=self.run_config_factory(
                    request.conversation_id,
                    thinking=True,
                ),
            )
        answer = str(result.final_output).strip()
        if not answer:
            raise ValueError("reasoning synthesis returned an empty answer")
        return answer
```

Agent instructions 明确禁止新增事实、工具调用和泄露内部 ID。citation 输入只保留类型、中文标题、领域、URL、分支、路径、符号、接口方法与接口路径等公开字段，并限制总字符数。

- [ ] **Step 4: 增加异常与超时测试并运行**

Run: `.\.venv-agent\Scripts\python.exe -m pytest -q tests\test_manager_reasoning_synthesis.py`

Expected: PASS，覆盖正常、空输出、超时和敏感 metadata 清理。

### Task 3: 接入 AgentService 门禁与回退

**Files:**
- Modify: `knowledge/agent_runtime/service.py`
- Modify: `knowledge/api/app.py`
- Test: `tests/test_agent_service.py`

- [ ] **Step 1: 编写单领域不升级和跨领域升级的失败测试**

```python
@pytest.mark.asyncio
async def test_single_domain_answer_does_not_use_reasoning_synthesizer(tmp_path):
    await service.chat("审批流管理员转办接口是什么", "single-domain")
    assert synthesizer.calls == []

@pytest.mark.asyncio
async def test_grounded_cross_domain_answer_uses_reasoning_synthesizer(tmp_path):
    response = await service.chat("审批通过后如何触发工作流", "cross-domain")
    assert len(synthesizer.calls) == 1
    assert response.answer == "Pro 综合答案"
```

再增加无引用、clarification、Bug Graph、综合异常和空输出不升级或回退测试。

- [ ] **Step 2: 运行新增测试并确认跨领域用例失败**

Run: `.\.venv-agent\Scripts\python.exe -m pytest -q tests\test_agent_service.py -k "reasoning_synthesizer or cross_domain"`

Expected: FAIL，因为 `AgentService` 尚未接受综合器。

- [ ] **Step 3: 增加确定性门禁和非流式综合**

```python
def _should_synthesize(self, context: AgentRunContext) -> bool:
    return bool(
        self.reasoning_synthesizer
        and context.response_mode == "answer"
        and not self._bug_graph_invoked(context)
        and self._has_evidence(context)
        and (
            len(set(context.routing_domains)) > 1
            or len(self._specialists_used(context)) > 1
        )
    )
```

在 `_response_from_result` 中先取得 Flash draft，再尝试综合；捕获超时和供应商异常后保留 draft，并追加 `manager.reasoning_synthesis` runtime span。综合答案最后继续执行 `EvidencePolicy.safeguard()` 和 `sanitize_public_answer()`。

- [ ] **Step 4: 在应用生命周期装配综合器**

仅当 `AGENT_MANAGER_REASONING_ENABLED`、`DEEPSEEK_REASONING_ENABLED` 且 provider 为 `deepseek` 时创建 `ManagerReasoningSynthesizer`，模型来自 `model_factory.create_reasoning_model()`；否则向 `AgentService` 传入 `None`。

- [ ] **Step 5: 运行服务层测试**

Run: `.\.venv-agent\Scripts\python.exe -m pytest -q tests\test_agent_service.py tests\test_agent_model_factory.py`

Expected: PASS。

### Task 4: 流式 Pro 输出与最终回归

**Files:**
- Modify: `knowledge/agent_runtime/reasoning_synthesis.py`
- Modify: `knowledge/agent_runtime/service.py`
- Test: `tests/test_agent_service.py`
- Test: `tests/test_manager_reasoning_synthesis.py`

- [ ] **Step 1: 编写流式失败测试**

```python
@pytest.mark.asyncio
async def test_cross_domain_stream_emits_only_pro_answer(tmp_path):
    events = [event async for event in service.stream_chat(question, "stream-pro")]
    deltas = [event["data"]["delta"] for event in events if event["event"] == "text.delta"]
    assert "".join(deltas) == "Pro 综合答案"
    assert "Flash 草稿" not in "".join(deltas)
```

- [ ] **Step 2: 运行测试并确认当前泄露 Flash 草稿或未流式综合**

Run: `.\.venv-agent\Scripts\python.exe -m pytest -q tests\test_agent_service.py::test_cross_domain_stream_emits_only_pro_answer`

Expected: FAIL。

- [ ] **Step 3: 实现综合器流式回调**

```python
async def synthesize(
    self,
    request: ReasoningSynthesisRequest,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    streamed = self.runner.run_streamed(...)
    async for event in streamed.stream_events():
        if is_text_delta(event) and on_delta is not None:
            await on_delta(str(event.data.delta))
    return str(streamed.final_output).strip()
```

跨领域候选请求始终缓冲 Flash Manager 正文；Flash 完成后调用综合器并转发 Pro delta。Pro 在第一个 delta 前失败时回退并输出 Flash draft；已开始输出后异常则发送公开错误事件，避免拼接两份互相冲突的答案。

- [ ] **Step 4: 运行流式和路由回归**

Run: `.\.venv-agent\Scripts\python.exe -m pytest -q tests\test_manager_reasoning_synthesis.py tests\test_agent_service.py tests\test_agent_intent_router.py tests\test_bug_graph_intake.py`

Expected: PASS。

- [ ] **Step 5: 运行全量后端回归和静态差异检查**

Run: `.\.venv-agent\Scripts\python.exe -m pytest -q`

Expected: PASS。

Run: `git diff --check`

Expected: 无输出，退出码为 0。

- [ ] **Step 6: 重启前检查运行任务**

确认知识同步和质量评测不存在 `queued`/`running` 任务后，再停止旧的单 Uvicorn 进程并以 `--workers 1` 启动。验证 readiness 中 model、Chroma、catalog、worker、Grafana 和 bug_graph 状态，不输出密钥、原始日志、代码正文或内部 ID。

