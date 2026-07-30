# BM25 + pgvector 查询性能优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 BM25 热查询对 PostgreSQL 大批量 ID 读取的依赖，并通过原子刷新、单管线预热和并行双路召回将代表问题总耗时降至 25 秒以内。

**Architecture:** PostgreSQL/pgvector 继续作为事实存储，BM25 使用初始化时加载的轻量 metadata 快照完成内存过滤。Registry 在锁外构建替换管线并在锁内原子交换；查询改写后，BM25 与 pgvector 由受控线程池并行执行，任一路失败时保留另一路结果。

**Tech Stack:** Python 3.11、FastAPI、Pydantic Settings、rank-bm25、pytest、PostgreSQL 15、pgvector 0.8。

---

## 文件结构

- `knowledge/services/metadata_filter.py`：纯函数 metadata 条件匹配器，仅实现现有检索所需的 Chroma where 子集。
- `knowledge/services/keyword_retrieval_service.py`：使用内存 metadata 计算 eligible records，Top-K 后才读取正文。
- `knowledge/agent_runtime/pipeline_registry.py`：维护缓存、预热状态、锁外刷新和原子替换。
- `knowledge/indexing/coordinator.py`：普通同步成功后刷新管线，删除/禁用场景继续强制失效。
- `knowledge/services/multi_route_retrieval_service.py`：并行执行关键词和向量召回并独立降级。
- `knowledge/config/settings.py`：提供三个独立回退开关。
- `knowledge/api/app.py`：只预热全局管线并在 readiness 中动态公开 BM25 状态。
- `tests/test_metadata_filter.py`：metadata matcher 语义测试。
- `tests/test_keyword_retrieval.py`：证明热查询不读取大批量 ID。
- `tests/test_pipeline_registry.py`：并发刷新、失败保旧和状态测试。
- `tests/test_indexing_coordinator.py`：同步刷新语义测试。
- `tests/test_multi_route_retrieval.py`：并行耗时与单路失败测试。
- `tests/test_app_lifespan.py`：单管线预热和 readiness 测试。
- `tests/test_settings.py`：配置默认值和覆盖测试。

### Task 1: 配置开关与内存 metadata 条件匹配

**Files:**
- Create: `knowledge/services/metadata_filter.py`
- Modify: `knowledge/config/settings.py`
- Create: `tests/test_metadata_filter.py`
- Modify: `tests/test_settings.py`

- [ ] **Step 1: 写配置与 matcher 失败测试**

```python
def test_metadata_matches_nested_boolean_and_collection_operators():
    metadata = {"domain": "审批流", "branch": "develop", "enabled": True}
    where = {"$and": [
        {"domain": {"$in": ["审批流", "工作流"]}},
        {"$or": [{"branch": "master"}, {"branch": {"$eq": "develop"}}]},
        {"enabled": {"$ne": False}},
    ]}
    assert matches_metadata(metadata, where) is True

def test_metadata_matcher_rejects_unknown_operator():
    with pytest.raises(ValueError, match="Unsupported metadata operator"):
        matches_metadata({"domain": "审批流"}, {"domain": {"$contains": "审批"}})

def test_retrieval_performance_switches_default_to_enabled():
    settings = Settings(_env_file=None)
    assert settings.bm25_memory_filter_enabled is True
    assert settings.bm25_stale_while_refresh_enabled is True
    assert settings.retrieval_parallel_routes_enabled is True
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `python -m pytest tests/test_metadata_filter.py tests/test_settings.py -q`

Expected: `ModuleNotFoundError` 或缺少三个 Settings 字段，证明新行为尚未实现。

- [ ] **Step 3: 实现最小 matcher 和配置字段**

```python
def matches_metadata(metadata: Mapping[str, Any], where: Mapping[str, Any] | None) -> bool:
    if not where:
        return True
    if set(where) == {"$and"}:
        return all(matches_metadata(metadata, clause) for clause in where["$and"])
    if set(where) == {"$or"}:
        return any(matches_metadata(metadata, clause) for clause in where["$or"])
    return all(_matches_field(metadata.get(field, _MISSING), condition) for field, condition in where.items())
```

在 `Settings` 增加默认开启的 `BM25_MEMORY_FILTER_ENABLED`、`BM25_STALE_WHILE_REFRESH_ENABLED` 和 `RETRIEVAL_PARALLEL_ROUTES_ENABLED` 布尔字段。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `python -m pytest tests/test_metadata_filter.py tests/test_settings.py -q`

Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add knowledge/services/metadata_filter.py knowledge/config/settings.py tests/test_metadata_filter.py tests/test_settings.py
git commit -m "feat: add in-memory retrieval metadata matcher"
```

### Task 2: BM25 热查询完全使用内存 metadata

**Files:**
- Modify: `knowledge/services/keyword_retrieval_service.py`
- Modify: `tests/test_keyword_retrieval.py`

- [ ] **Step 1: 写仓储调用门禁失败测试**

```python
def test_filtered_keyword_search_does_not_fetch_eligible_ids_from_repository():
    repository = InMemoryChunkRepository([
        _chunk("faq", "审批流管理员转办", "管理员, 转办", chunk_type="faq"),
        _chunk("code", "审批流管理员转办实现", "adminTransfer", chunk_type="code"),
    ])
    repository.chunk_id_requests = []
    service = KeywordRetrievalService(repository, app_id="middle-platform")
    results = service.search("管理员转办", where={"chunk_type": "faq"})
    assert repository.chunk_id_requests == []
    assert [item.chunk_id for item in results] == ["faq"]
    assert repository.body_requests == [["faq"]]
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `python -m pytest tests/test_keyword_retrieval.py::test_filtered_keyword_search_does_not_fetch_eligible_ids_from_repository -q`

Expected: `chunk_id_requests` 包含过滤条件，证明当前仍访问 PostgreSQL ID 列表。

- [ ] **Step 3: 实现内存 eligible index 计算**

在构造函数接收 `memory_filter_enabled: bool = True`。启用时，对 `self._records` 的 metadata 使用 `matches_metadata(record.metadata, self.build_where(where))` 计算 eligible indexes；关闭时保留 `get_chunk_ids()` 旧路径作为独立回退开关。候选遍历直接基于 eligible indexes，Top-K 后只调用一次 `get_chunks(ids=[record.chunk_id for _, record in selected])`。

- [ ] **Step 4: 运行关键词检索测试并确认 GREEN**

Run: `python -m pytest tests/test_metadata_filter.py tests/test_keyword_retrieval.py -q`

Expected: 全部通过，且现有分数归一化与结果顺序不变。

- [ ] **Step 5: 提交**

```bash
git add knowledge/services/keyword_retrieval_service.py tests/test_keyword_retrieval.py
git commit -m "perf: filter bm25 candidates in memory"
```

### Task 3: Registry 锁外构建与原子刷新

**Files:**
- Modify: `knowledge/agent_runtime/pipeline_registry.py`
- Modify: `knowledge/indexing/coordinator.py`
- Modify: `tests/test_pipeline_registry.py`
- Modify: `tests/test_indexing_coordinator.py`

- [ ] **Step 1: 写刷新并发与失败保旧测试**

```python
def test_refresh_keeps_old_pipeline_visible_until_replacement_is_ready():
    started, release = Event(), Event()
    builds = []
    def build(app_id, domain):
        value = object(); builds.append(value)
        if len(builds) == 2:
            started.set(); release.wait(1)
        return value
    registry = RetrievalPipelineRegistry(pipeline_builder=build)
    old = registry.get("middle-platform", None)
    thread = Thread(target=lambda: registry.refresh(app_id="middle-platform"))
    thread.start(); assert started.wait(0.5)
    assert registry.get("middle-platform", None) is old
    release.set(); thread.join()
    assert registry.get("middle-platform", None) is builds[1]

def test_refresh_failure_retains_old_pipeline_and_marks_unavailable():
    # 第二次 builder 抛出 RuntimeError；refresh 返回 0，get 仍返回 old，warm_status 不含异常正文。
```

同时扩展 `FakeRegistry`，断言普通 Git/文档同步成功记录 `refresh(app_id="middle-platform")`，删除或停用来源仍记录 `invalidate(app_id="middle-platform")`。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `python -m pytest tests/test_pipeline_registry.py tests/test_indexing_coordinator.py -q`

Expected: `RetrievalPipelineRegistry` 缺少 `refresh/warm_status`，协调器仍调用 invalidate。

- [ ] **Step 3: 实现锁外刷新和状态机**

`refresh()` 先在锁内复制匹配 key；逐个在锁外调用 builder；所有构建成功后在锁内更新字典。失败时不修改缓存，状态设为 `unavailable` 并只记录异常类型。`warm()` 在开始/成功/失败时维护 `warming/available/unavailable`。关闭 stale-while-refresh 开关时，协调器保留旧 invalidate 行为。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `python -m pytest tests/test_pipeline_registry.py tests/test_indexing_coordinator.py -q`

Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add knowledge/agent_runtime/pipeline_registry.py knowledge/indexing/coordinator.py tests/test_pipeline_registry.py tests/test_indexing_coordinator.py
git commit -m "perf: refresh retrieval pipelines atomically"
```

### Task 4: 单全局管线预热和 BM25 readiness

**Files:**
- Modify: `knowledge/api/app.py`
- Modify: `tests/test_app_lifespan.py`

- [ ] **Step 1: 写预热 scope 与 readiness 失败测试**

```python
def test_bm25_component_uses_live_registry_status():
    class Registry:
        def warm_status(self):
            return {"status": "available", "cached_pipelines": 1}
    assert app_module._bm25_component(Registry(), enabled=True) == {
        "status": "available",
        "cached_pipelines": 1,
    }

def test_bm25_component_is_disabled_when_warmup_is_off():
    assert app_module._bm25_component(object(), enabled=False) == {
        "status": "disabled",
        "cached_pipelines": 0,
    }
```

在现有 production lifespan wiring 测试的 `FakeRegistry.warm()` 中捕获 `scopes`，并追加 `assert captured["warm_scopes"] == [("middle-platform", None)]`。在注入组件状态的 readiness 测试中加入 `bm25={"status": "warming"}`，断言响应状态为 `degraded`。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `python -m pytest tests/test_app_lifespan.py -q`

Expected: 当前捕获四个预热 scope，readiness 中没有 bm25 动态状态。

- [ ] **Step 3: 实现单 scope 预热和动态状态**

启动只传入 `[("middle-platform", None)]`。`component_status` 增加 bm25 初始状态；`/health/ready` 每次从 registry `warm_status()` 更新该组件，并在启用预热时把 bm25 纳入 critical components。关闭预热时返回 `disabled` 且不阻塞 ready。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `python -m pytest tests/test_app_lifespan.py tests/test_pipeline_registry.py -q`

Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add knowledge/api/app.py tests/test_app_lifespan.py
git commit -m "feat: expose bm25 warmup readiness"
```

### Task 5: BM25 与 pgvector 并行召回及最终回归

**Files:**
- Modify: `knowledge/services/multi_route_retrieval_service.py`
- Modify: `knowledge/agent_runtime/pipeline_registry.py`
- Modify: `tests/test_multi_route_retrieval.py`

- [ ] **Step 1: 写并行耗时和 BM25 失败降级测试**

```python
def test_keyword_and_vector_routes_run_in_parallel():
    # 两路各阻塞约 0.15 秒；search 总耗时应小于 0.25 秒，而不是约 0.30 秒。
    started = monotonic()
    result = service.search("管理员转办")
    assert monotonic() - started < 0.25
    assert result.keyword_results
    assert result.vector_results

def test_multi_route_search_falls_back_to_vector_when_bm25_fails(caplog):
    # keyword_service.search 抛出 RuntimeError，vector 仍返回结果；日志只包含异常类型。
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `python -m pytest tests/test_multi_route_retrieval.py -q`

Expected: 当前顺序执行导致耗时门禁失败，BM25 异常会终止整次检索。

- [ ] **Step 3: 实现受控双路并行与独立计时**

使用 `ThreadPoolExecutor(max_workers=2)` 同时提交 keyword 和 vector callable。分别在 callable 内记录耗时并捕获异常；等待两个 future 后继续现有 RouteSearchResult 转换和 RRF/Rerank。构造函数接收 `parallel_routes_enabled: bool = True`，关闭时执行兼容的顺序路径。Registry builder 从 Settings 注入三个开关。

- [ ] **Step 4: 运行局部和全量测试**

Run: `python -m pytest tests/test_multi_route_retrieval.py tests/test_agent_rag_tools.py tests/test_pipeline_registry.py -q`

Expected: 全部通过。

Run: `python -m pytest -q`

Expected: 全量通过，无未处理异常和测试警告新增。

- [ ] **Step 5: 运行真实问题重放并记录脱敏指标**

重放审批流接口/API 查询，仅记录阶段耗时和引用数量，不输出问题正文、chunk ID、日志正文、Embedding 或连接信息。验收热查询：`keyword_search <= 1.5s`、证据收集 `<= 6s`、总耗时 `<= 25s`；若模型供应商波动，单列模型耗时，不误判为存储回归。

- [ ] **Step 6: 提交**

```bash
git add knowledge/services/multi_route_retrieval_service.py knowledge/agent_runtime/pipeline_registry.py tests/test_multi_route_retrieval.py
git commit -m "perf: run hybrid retrieval routes concurrently"
```

### Task 6: 最终完整性检查

**Files:**
- Modify: `docs/superpowers/plans/2026-07-30-bm25-pgvector-query-optimization.md`

- [ ] **Step 1: 检查计划与设计覆盖**

Run: `$patterns = @(("T"+"BD"), ("TO"+"DO"), ("implement "+"later"), ("待"+"定")); Select-String -Path docs/superpowers/plans/2026-07-30-bm25-pgvector-query-optimization.md -Pattern $patterns`

Expected: 无输出。

- [ ] **Step 2: 检查补丁格式和工作树**

Run: `git diff --check && git status --short`

Expected: `git diff --check` 无输出；只显示预期的计划勾选或验证记录修改。

- [ ] **Step 3: 提交验证记录**

```bash
git add docs/superpowers/plans/2026-07-30-bm25-pgvector-query-optimization.md
git commit -m "docs: record bm25 optimization verification"
```

## 实施验证记录（2026-07-30）

- Task 1 至 Task 5 已按 RED → GREEN 顺序完成并分别提交。
- 全量 `python -m pytest -q` 通过；条件性 live 测试按既有配置跳过，仅保留一条 FastAPI TestClient 依赖弃用警告。
- 本地通过 Telepresence 连接 dev PostgreSQL/pgvector 的真实重放结果：
  - BM25 冷构建：441.897 秒；该成本由启动预热承担，readiness 在完成前不导流。
  - 首次查询：4.471 秒。
  - 热查询：3.969 秒。
  - Query Rewrite：1,172.5 毫秒。
  - BM25：1,638.4 毫秒；较改造前 10.9 秒降低约 85%。
  - pgvector：2,514.5 毫秒。
  - Rerank：273.0 毫秒。
  - 最终证据：5 条。
- 热查询和证据收集低于 6 秒目标，总耗时低于 25 秒目标。BM25 比 1.5 秒目标高 138.4 毫秒，保留为 dev 同网络部署后的复测项，不将 Telepresence 网络开销误报为代码达标。
