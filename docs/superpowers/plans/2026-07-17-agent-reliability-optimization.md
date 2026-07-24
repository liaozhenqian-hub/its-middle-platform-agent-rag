# Agent Reliability Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 100 条领域回放通过率从 89% 提升到至少 95%，重点修复工作流口语路由、指标候选确认、工具成本和引用膨胀。

**Architecture:** 在 Agent Runner 前增加高精度领域路由，只对明确命中的单领域问题选择“受限 Manager”；模糊和跨领域问题继续使用现有 Manager。将高风险指标数据/SQL MCP 工具收口到本地 Guarded Gateway，候选或应用未唯一确认时由代码拒绝继续。检索工具在 Run Context 中执行预算和重复 query 控制，最终响应通过稳定键聚合引用。

**Tech Stack:** Python 3.12、OpenAI Agents SDK、FastAPI、Pydantic v2、SQLite/aiosqlite、Chroma、现有 Metric MCP、pytest/pytest-asyncio。

---

## Delivery Order

1. 先建立 100 条正式回归门禁，防止优化过程中只看个别示例。
2. 修复领域路由，目标是消除 7 条工作流失败和 3 条指标跳过专家问题。
3. 实现指标 MCP 硬门禁，修复 2C 数据越过应用确认的问题。
4. 增加检索预算与重复 query 控制，降低工具调用和延迟。
5. 聚合引用并增加证据结论等级。
6. 完整回放、灰度开关和服务重启验证。

## Task 1: Promote The 100-Case Suite Into The Quality Evaluator

**Files:**
- Modify: `knowledge/quality/models.py`
- Modify: `knowledge/quality/repository.py`
- Modify: `knowledge/quality/evaluation.py`
- Modify: `knowledge/api/schemas.py`
- Modify: `knowledge/api/app.py`
- Create: `knowledge/quality/import_cases.py`
- Test: `tests/test_quality_repository.py`
- Test: `tests/test_quality_evaluation.py`
- Test: `tests/test_quality_api.py`

- [ ] **Step 1: Write failing model and migration tests**

Extend an evaluation case with deterministic behavior and budget fields:

```python
EvalCaseCreate(
    name="workflow connector variable colloquial",
    question="上一个节点返回的 token 怎么放进 HTTP body？",
    required_tools=["workflow_expert"],
    required_citation_types=["code"],
    expected_behavior="answer",
    max_latency_ms=60_000,
    max_tool_calls=6,
    max_citations=10,
)
```

Assert that repository initialization adds `expected_behavior`, `max_latency_ms`, `max_tool_calls`, and `max_citations` columns idempotently.

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```powershell
.\.venv-agent\Scripts\python.exe -m pytest tests\test_quality_repository.py tests\test_quality_evaluation.py -q
```

Expected: failures because the dataclasses, SQLite columns, and checks do not exist.

- [ ] **Step 3: Add schema migration version 3**

Add these fields to `EvalCaseCreate` and `EvalCase`:

```python
expected_behavior: str = "answer"
max_latency_ms: float = 60_000
max_tool_calls: int = 6
max_citations: int = 10
```

Validate `expected_behavior in {"answer", "clarify", "refuse"}` and positive budgets. Add the four SQLite columns with explicit defaults and record migration version 3.

- [ ] **Step 4: Implement deterministic checks**

In `QualityEvaluationService._run_case`, calculate:

```python
checks["tool_count"] = len(tool_names) <= case.max_tool_calls
checks["citation_count"] = len(citation_types) <= case.max_citations
checks["latency"] = duration_ms <= case.max_latency_ms
checks["behavior"] = self.behavior_checker.matches(case.expected_behavior, answer, tool_names)
```

Create a small `BehaviorChecker` with the same Chinese clarification/refusal markers used by the 100-case runner. Do not use an LLM for this structural check.

- [ ] **Step 5: Add an idempotent import command**

`knowledge/quality/import_cases.py` must read `storage/evaluations/domain-public-benchmark-cases-100-20260717.json`, upsert by stable case ID, and tag every item with `public-intent-100` plus its category/variant.

Run:

```powershell
.\.venv-agent\Scripts\python.exe -m knowledge.quality.import_cases --dry-run
```

Expected: `100 cases validated, 0 written`.

- [ ] **Step 6: Run focused tests**

Expected: all quality repository/evaluation/API tests pass.

## Task 2: Add A High-Precision Domain Intent Router

**Files:**
- Create: `knowledge/agent_runtime/intent_router.py`
- Modify: `knowledge/agent_runtime/context.py`
- Modify: `knowledge/agent_runtime/agent_factory.py`
- Modify: `knowledge/agent_runtime/service.py`
- Modify: `knowledge/api/app.py`
- Modify: `knowledge/config/settings.py`
- Modify: `.env.example`
- Create: `tests/test_agent_intent_router.py`
- Modify: `tests/test_agent_factory.py`
- Modify: `tests/test_agent_service.py`

- [ ] **Step 1: Write failing router tests from the 11 failures**

Required mappings include:

```python
assert router.route("HTTP 节点超时了会重试几次").domains == ("workflow",)
assert router.route("两个节点都叫 result 会覆盖吗").domains == ("workflow",)
assert router.route("接口回字符串 10，规则节点按数字比较").domains == ("workflow",)
assert router.route("从零建一个能查数据的指标").domains == ("metric-platform",)
assert router.route("这个审批不对").needs_clarification is True
assert router.route("审批通过后触发工作流").domains == ("approval-flow", "workflow")
```

Bug terms must require actual failure evidence such as `traceId`, `500`, `报错`, `异常`, or `失败`; the word “转换” must never route to Bug Graph by itself.

- [ ] **Step 2: Implement `RoutingDecision` and high-confidence aliases**

```python
@dataclass(frozen=True)
class RoutingDecision:
    domains: tuple[str, ...]
    intent: str
    confidence: float
    needs_clarification: bool = False
    reason_codes: tuple[str, ...] = ()
```

Use normalized token/phrase groups:

- Workflow: `工作流`, `连接器`, `HTTP节点`, `上游节点`, `下游节点`, `回调节点`, `FOR节点`, `SWITCH`, `默认分支`, `节点变量`.
- Approval: `审批`, `会签`, `或签`, `审批人`, `撤回`, `加签`, `抄送`.
- Metric: `指标`, `原子指标`, `派生指标`, `复合指标`, `指标应用`, `口径`, `查数`, `SQL`.
- Bug: explicit error/trace vocabulary only.

Return no forced domain when confidence is below `0.75`; the existing Manager then handles the request.

- [ ] **Step 3: Create restricted Managers in `AgentFactory`**

Extend `AgentTopology` with `domain_managers`. Each restricted Manager keeps the same Manager instructions but exposes only the selected specialist tool. Cross-domain decisions continue using the full Manager.

The restricted Manager instruction must state: “路由层已确认当前问题属于工作流，必须调用 workflow_expert，不得再次要求用户确认属于哪个中台模块。”

- [ ] **Step 4: Select Manager in JSON and SSE paths**

Add `intent_router` to `AgentService`. In `chat` and `stream_chat`, compute the decision before `runner.run`, write `routing_domains` and `routing_intent` into `AgentRunContext`, then choose the restricted or full Manager.

Do not alter an explicitly bound `domain_id`; explicit API scope has higher priority than inferred routing.

- [ ] **Step 5: Add configuration**

```dotenv
AGENT_INTENT_ROUTER_ENABLED=true
AGENT_INTENT_ROUTER_MIN_CONFIDENCE=0.75
```

Validate the threshold in `[0, 1]`.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
.\.venv-agent\Scripts\python.exe -m pytest tests\test_agent_intent_router.py tests\test_agent_factory.py tests\test_agent_service.py -q
```

Expected: all router, JSON, and SSE routing tests pass.

## Task 3: Replace High-Risk Raw Metric MCP Calls With A Guarded Gateway

**Files:**
- Create: `knowledge/agent_runtime/metric_gateway.py`
- Modify: `knowledge/agent_runtime/metric_mcp.py`
- Modify: `knowledge/agent_runtime/agent_factory.py`
- Modify: `knowledge/agent_runtime/context.py`
- Create: `tests/test_metric_gateway.py`
- Modify: `tests/test_metric_mcp.py`
- Modify: `tests/test_agent_factory.py`

- [ ] **Step 1: Write failing candidate gate tests**

Cover these scenarios with a fake MCP server implementing `call_tool`:

```python
result = await gateway.query_data(question="近七天2C包裹数", selected_app_id=None)
assert result.status == "clarification_required"
assert fake_mcp.calls.count("searchMetricAppQueryResult") == 0
```

Also assert:

- Unique business metric + unique app can query.
- Multiple metrics cannot query.
- Unique metric + multiple apps cannot query without an explicit selected app ID.
- Selected app ID must belong to the latest candidate set.
- SQL lookup obeys the same gate.

- [ ] **Step 2: Reduce the raw MCP exposure list**

Split the allowlist:

```python
METRIC_MCP_DISCOVERY_TOOLS = (
    "metricMcpInfo",
    "searchAtomicMetric",
    "searchDerivedMetric",
    "searchCompositeMetric",
    "searchBizMetric",
    "searchBizMetricDetail",
    "searchMetricApp",
)
```

Do not expose `searchMetricAppQueryResult`, `searchSqlByMetricTypeAndNameExact`, or `searchSqlByMetricTypeAndNameFuzzy` directly to the model.

- [ ] **Step 3: Implement local guarded tools**

Use `MCPServerStreamableHttp.call_tool(tool_name, arguments)` inside:

```python
search_metric_candidates(question: str) -> CandidateResult
query_metric_data_guarded(question: str, selected_app_id: str | None) -> str
query_metric_sql_guarded(question: str, selected_metric_id: str | None, selected_app_id: str | None) -> str
```

Parse MCP JSON with structured APIs. Store only candidate IDs/names and confirmation state in `AgentRunContext`; never store raw MCP output or credentials.

- [ ] **Step 4: Set deterministic clarification mode**

When candidates are not unique, set `context.response_mode = "clarification"` and return a bounded list containing candidate name,口径摘要, application name, and stable ID. The data/SQL tool must remain uncalled.

- [ ] **Step 5: Update Metric Agent instructions**

Tell the model to use the three gateway tools. Remove instructions that encourage direct raw data/SQL MCP calls.

- [ ] **Step 6: Run focused metric tests**

Expected: the `metric-2c-packages-colloquial` scenario cannot reach the data tool until an application is selected.

## Task 4: Enforce Retrieval Budgets And Duplicate-Query Suppression

**Files:**
- Modify: `knowledge/agent_runtime/context.py`
- Modify: `knowledge/agent_runtime/rag_tools.py`
- Modify: `knowledge/config/settings.py`
- Modify: `.env.example`
- Create: `tests/test_agent_context.py`
- Modify: `tests/test_agent_rag_tools.py`

- [ ] **Step 1: Write failing budget tests**

Test that the same normalized query is executed only once and that the seventh retrieval attempt is blocked:

```python
assert context.reserve_retrieval("search_domain_code", "  HTTP 节点 重试 ") == "allowed"
assert context.reserve_retrieval("search_domain_code", "http节点 重试") == "duplicate"
```

- [ ] **Step 2: Add private run-local retrieval state**

Add `retrieval_signatures`, `retrieval_call_count`, and `retrieval_result_cache` to `AgentRunContext`, but explicitly exclude cached content from `to_dict()` and `from_dict()` so chunk text never enters session or pending-run serialization.

- [ ] **Step 3: Apply the budget in both RAG tool factories**

Before `pipeline.search`:

- Return `{"status":"duplicate_query","reuse_existing_evidence":true}` for a duplicate.
- Return `{"status":"budget_exhausted","max_calls":6}` after the budget.
- Execute normally otherwise.

- [ ] **Step 4: Add settings**

```dotenv
AGENT_RETRIEVAL_MAX_CALLS=6
AGENT_RETRIEVAL_MAX_IDENTICAL_QUERIES=1
```

- [ ] **Step 5: Verify**

Run RAG tool tests and assert the repository call count, not only the returned text.

## Task 5: Aggregate And Cap Public Citations

**Files:**
- Modify: `knowledge/agent_runtime/context.py`
- Modify: `knowledge/agent_runtime/service.py`
- Modify: `knowledge/config/settings.py`
- Modify: `.env.example`
- Modify: `tests/test_agent_context.py`
- Modify: `tests/test_agent_service.py`

- [ ] **Step 1: Write failing logical-deduplication tests**

For code, group by branch + relative path + symbol name. For documents, group by catalog source + relative path + heading. Keep the first/highest-ranked chunk.

- [ ] **Step 2: Implement `public_citations(max_count)`**

Priority order:

1. Log trace used by Bug Graph.
2. MCP tool citations.
3. Code methods/classes directly referenced in the answer.
4. Product document sections.
5. Remaining code fields and supplemental chunks.

Return at most 10 citations while retaining at least one citation from each source type used in the answer.

- [ ] **Step 3: Use compact citations in all terminal responses**

Apply the method in completed JSON, SSE `run.completed`, approval-required responses, and quality capture. Keep the full internal list only during the run.

- [ ] **Step 4: Add configuration**

```dotenv
AGENT_PUBLIC_CITATION_LIMIT=10
```

- [ ] **Step 5: Verify duplicate-title target**

Re-run a citation fixture with multiple chunks from the same heading and assert one public citation remains.

## Task 6: Add Evidence Conclusion Levels

**Files:**
- Create: `knowledge/agent_runtime/evidence_policy.py`
- Modify: `knowledge/agent_runtime/agent_factory.py`
- Modify: `knowledge/agent_runtime/service.py`
- Create: `tests/test_evidence_policy.py`
- Modify: `tests/test_agent_service.py`

- [ ] **Step 1: Write failing negative-claim tests**

Answers containing `没有`, `不支持`, `一定`, or `立即` must be downgraded when citations contain only DTO fields, generic documents, or no explicit negative statement.

- [ ] **Step 2: Implement three evidence levels**

```python
class EvidenceLevel(str, Enum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    NOT_FOUND = "not_found"
```

Code methods and explicit product limitations may support `CONFIRMED`. Structural inference supports `INFERRED`. Absence of retrieval results is `NOT_FOUND`, never a confirmed lack of capability.

- [ ] **Step 3: Strengthen specialist instructions**

Require every capability conclusion to use one label: `已确认`, `根据现有证据推断`, or `本次检索暂未找到`. Explicitly prohibit converting `NOT_FOUND` into “系统没有”。

- [ ] **Step 4: Add a deterministic final-answer safeguard**

When the answer contains a negative capability claim and `EvidencePolicy` cannot find a qualifying citation, replace the claim prefix with `本次检索暂未找到该能力的明确实现，不能据此确认系统不支持。`

- [ ] **Step 5: Verify**

Test version migration, sub-workflow, type conversion, and parallel join examples.

## Task 7: Full Regression, Benchmark Gate, And Rollout

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Update: `storage/evaluations/domain-public-benchmark-report-100-20260717.md`

- [ ] **Step 1: Run focused backend tests**

```powershell
.\.venv-agent\Scripts\python.exe -m pytest tests\test_agent_intent_router.py tests\test_metric_gateway.py tests\test_agent_rag_tools.py tests\test_agent_context.py tests\test_evidence_policy.py tests\test_quality_evaluation.py -q
```

Expected: zero failures.

- [ ] **Step 2: Run the full backend suite**

```powershell
.\.venv-agent\Scripts\python.exe -m pytest -q
```

Expected: all non-live tests pass; live tests remain explicitly skipped unless credentials are enabled.

- [ ] **Step 3: Run frontend tests and build**

```powershell
Set-Location web
npm test
npm run build
```

Expected: all Vitest tests pass and Vite production build succeeds.

- [ ] **Step 4: Re-run the 100-case benchmark**

```powershell
.\.venv-agent\Scripts\python.exe storage\evaluations\run-domain-benchmark-100.py --concurrency 3 --timeout 180
```

Acceptance gate:

- Overall pass rate >= 95%.
- Workflow >= 95%.
- Metric platform >= 96%.
- Formal/colloquial pass-rate gap <= 3 percentage points.
- Median latency < 15 seconds and P90 < 30 seconds.
- Average tool calls <= 4.
- Public citations <= 10 and duplicate-title rate < 5%.
- Metric candidate/application ambiguity produces zero data/SQL calls.

- [ ] **Step 5: Clean benchmark state**

Delete only conversations and quality turns whose ID starts with `public-benchmark-100:`. Verify zero matching rows in both `agent_sessions.db` and `agent_quality.db`.

- [ ] **Step 6: Restart one Uvicorn worker and verify readiness**

```powershell
uvicorn knowledge.api.app:app --host 127.0.0.1 --port 8000 --workers 1
```

Verify `/health/ready` reports model, Chroma, MCP, Grafana, Bug Graph, quality database, catalog, worker, GitLab, and Swagger cache as available.

## Rollout Flags

All changes must be reversible without reverting code:

```dotenv
AGENT_INTENT_ROUTER_ENABLED=true
AGENT_INTENT_ROUTER_MIN_CONFIDENCE=0.75
AGENT_RETRIEVAL_MAX_CALLS=6
AGENT_RETRIEVAL_MAX_IDENTICAL_QUERIES=1
AGENT_PUBLIC_CITATION_LIMIT=10
METRIC_QUERY_GUARD_ENABLED=true
```

Deploy in this order: router only → metric guard → retrieval budget → citation compression → evidence safeguard. Run the 100-case suite after each stage so regressions can be attributed to one change.

## Repository Note

The target directory is not a Git repository. Do not run commit, branch, or PR commands during execution unless the user explicitly initializes Git first.
