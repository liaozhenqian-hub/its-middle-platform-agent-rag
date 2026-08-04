# RAG Quality And Latency Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 pgvector 产品文档领域过滤，限制单领域实际检索和公开引用，完成数据回填，并通过 Critical 5/10/30 与网页 P90 门禁。

**Architecture:** 保留现有 Agent、BM25、pgvector、RRF 和 Rerank 流程，在 pgvector 写入边界统一领域 ID，在 `AgentRunContext` 统一管理实际检索预算和公开引用选择。迁移通过只输出统计的 Storage CLI 幂等执行；质量验证继续使用现有 PostgreSQL 质量库和 Critical 语义裁判。

**Tech Stack:** Python 3.11、FastAPI、OpenAI Agents SDK、SQLAlchemy、psycopg/pgvector、Typer、pytest、Vue/Vitest。

---

## File Structure

- `knowledge/repositories/postgres_vector_store_repository.py`：pgvector 独立列规范化、产品文档领域回填与统计。
- `knowledge/storage_cli.py`：新增安全的 dry-run/apply 回填命令。
- `knowledge/agent_runtime/context.py`：跨工具标准化检索预算、查询结果复用标识和强引用选择。
- `knowledge/agent_runtime/rag_tools.py`：所有底层搜索计入预算，向引用传递检索质量信号。
- `knowledge/config/settings.py`、`.env.example`：4 次检索预算、5 条公开引用和相关性阈值。
- `knowledge/api/app.py`、`knowledge/agent_runtime/agent_factory.py`：将新配置注入 Agent 服务和证据工具。
- `tests/test_pgvector_repository.py`：领域写入和回填仓储测试。
- `tests/test_storage_cli.py`：回填 CLI dry-run/apply 测试。
- `tests/test_agent_context.py`：跨工具查询去重和引用门禁测试。
- `tests/test_agent_evidence_collection_budget.py`：实际底层检索预算和补查预算测试。
- `tests/test_settings.py`、`tests/test_app_lifespan.py`：配置默认值与依赖注入测试。
- `docs/postgresql-pgvector-migration.md`：回填、校验和回滚说明。

### Task 1: Normalize Pgvector Domain IDs

**Files:**
- Modify: `knowledge/repositories/postgres_vector_store_repository.py`
- Test: `tests/test_pgvector_repository.py`

- [x] **Step 1: Write the failing normalization test**

```python
def test_pgvector_domain_column_prefers_stable_domain_id():
    columns = PostgresVectorStoreRepository._normalized_columns({
        "domain": "审批流",
        "domain_id": "approval-flow",
        "source_type": "product_document",
    })
    assert columns["domain"] == "approval-flow"
```

- [x] **Step 2: Run the focused test and verify RED**

Run: `python -m pytest tests/test_pgvector_repository.py::test_pgvector_domain_column_prefers_stable_domain_id -q`

Expected: FAIL because the current implementation returns the display name.

- [x] **Step 3: Implement the minimal normalization change**

```python
"domain": PostgresVectorStoreRepository._text(
    metadata.get("domain_id") or metadata.get("domain")
),
```

- [x] **Step 4: Run repository tests and verify GREEN**

Run: `python -m pytest tests/test_pgvector_repository.py -q`

Expected: PASS.

- [x] **Step 5: Commit the normalization change**

```bash
git add knowledge/repositories/postgres_vector_store_repository.py tests/test_pgvector_repository.py
git commit -m "fix: normalize pgvector document domains"
```

### Task 2: Add Idempotent Product Document Backfill

**Files:**
- Modify: `knowledge/repositories/postgres_vector_store_repository.py`
- Modify: `knowledge/storage_cli.py`
- Modify: `docs/postgresql-pgvector-migration.md`
- Test: `tests/test_pgvector_repository.py`
- Test: `tests/test_storage_cli.py`

- [x] **Step 1: Write failing repository tests for preview and apply**

```python
def test_product_document_domain_backfill_is_scoped_and_idempotent():
    report = repository.backfill_document_domains(apply=False)
    assert report.total == 268
    assert report.pending == 268
    assert report.updated == 0
    assert "content" not in report.__dict__

    applied = repository.backfill_document_domains(apply=True)
    assert applied.updated == 268
    assert repository.backfill_document_domains(apply=True).updated == 0
```

The fake cursor must assert that the SQL scopes by collection and `source_type='product_document'`, assigns `metadata ->> 'domain_id'`, and never selects content, embedding, ID, or complete source identifiers.

- [x] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_pgvector_repository.py -k document_domain_backfill -q`

Expected: FAIL because the method and report type do not exist.

- [x] **Step 3: Implement the report and transactional repository method**

```python
@dataclass(frozen=True)
class DomainBackfillReport:
    total: int
    pending: int
    updated: int
    by_domain: dict[str, int]
```

The method performs aggregate preview queries and, when `apply=True`, executes the scoped update in one transaction before rechecking mismatch count. A nonzero mismatch after apply raises and rolls back.

- [x] **Step 4: Write failing CLI tests**

```python
def test_backfill_document_domains_defaults_to_dry_run(monkeypatch):
    result = runner.invoke(storage_cli.app, ["backfill-document-domains"])
    assert result.exit_code == 0
    assert '"mode": "dry-run"' in result.stdout

def test_backfill_document_domains_requires_apply_for_writes(monkeypatch):
    result = runner.invoke(storage_cli.app, ["backfill-document-domains", "--apply"])
    assert result.exit_code == 0
    assert '"mode": "apply"' in result.stdout
```

- [x] **Step 5: Implement the CLI and documentation**

The CLI creates the configured knowledge pgvector repository without an Embedding client, calls the backfill method, closes the pool, and emits only mode and aggregate report fields.

- [x] **Step 6: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_pgvector_repository.py tests/test_storage_cli.py -q`

Expected: PASS.

- [x] **Step 7: Commit the backfill implementation**

```bash
git add knowledge/repositories/postgres_vector_store_repository.py knowledge/storage_cli.py tests/test_pgvector_repository.py tests/test_storage_cli.py docs/postgresql-pgvector-migration.md
git commit -m "feat: backfill pgvector document domains"
```

### Task 3: Enforce One Cross-Tool Retrieval Budget

**Files:**
- Modify: `knowledge/agent_runtime/context.py`
- Modify: `knowledge/agent_runtime/rag_tools.py`
- Modify: `knowledge/config/settings.py`
- Modify: `.env.example`
- Test: `tests/test_agent_context.py`
- Test: `tests/test_agent_evidence_collection_budget.py`
- Test: `tests/test_settings.py`

- [x] **Step 1: Write failing cross-tool deduplication tests**

```python
def test_context_deduplicates_equivalent_queries_across_tool_names():
    context = AgentRunContext("conversation", "run")
    assert context.reserve_retrieval(
        query=" SDK  鉴权 ", app_id="middle-platform",
        domain_id="metric-platform", source_type="product_document",
        branch=None, task_type="how_to", max_calls=4,
    ) == "allowed"
    assert context.reserve_retrieval(
        query="sdk鉴权", app_id="middle-platform",
        domain_id="metric-platform", source_type="product_document",
        branch=None, task_type="how_to", max_calls=4,
    ) == "duplicate"
```

Also assert four distinct retrieval keys are allowed and the fifth returns `budget_exhausted`.

- [x] **Step 2: Run the focused context tests and verify RED**

Run: `python -m pytest tests/test_agent_context.py -k retrieval -q`

Expected: FAIL because the current signature includes tool name and lacks scope fields.

- [x] **Step 3: Implement a stable retrieval key**

Use Unicode NFKC normalization, case folding, whitespace/punctuation removal, and the trusted scope fields. Keep signatures and counters run-local and excluded from serialization.

- [x] **Step 4: Write failing tests for nested supplemental searches**

Create an API-contract pipeline whose first result references DTO types and whose exact lookup misses. Assert the supplemental pipeline search reserves another call, total actual searches never exceeds four, and the same normalized search cannot execute through a second tool entry point.

- [x] **Step 5: Run the evidence budget tests and verify RED**

Run: `python -m pytest tests/test_agent_evidence_collection_budget.py tests/test_agent_rag_tools.py -q`

Expected: FAIL because the supplemental code search is not independently reserved.

- [x] **Step 6: Route every actual search through one reservation helper**

`search_modality`, supplemental DTO search, product fallback, Swagger inspection and legacy scoped tools must reserve before performing I/O. A duplicate returns existing evidence status; budget exhaustion returns a bounded audit result. Set the default maximum to four.

- [x] **Step 7: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_agent_context.py tests/test_agent_evidence_collection_budget.py tests/test_agent_rag_tools.py tests/test_settings.py -q`

Expected: PASS.

- [x] **Step 8: Commit the retrieval budget change**

```bash
git add knowledge/agent_runtime/context.py knowledge/agent_runtime/rag_tools.py knowledge/config/settings.py .env.example tests/test_agent_context.py tests/test_agent_evidence_collection_budget.py tests/test_agent_rag_tools.py tests/test_settings.py
git commit -m "fix: enforce cross-tool retrieval budget"
```

### Task 4: Select Only Strong Public Citations

**Files:**
- Modify: `knowledge/agent_runtime/context.py`
- Modify: `knowledge/agent_runtime/rag_tools.py`
- Modify: `knowledge/config/settings.py`
- Modify: `knowledge/api/app.py`
- Modify: `knowledge/agent_runtime/agent_factory.py`
- Modify: `.env.example`
- Test: `tests/test_agent_context.py`
- Test: `tests/test_agent_service.py`
- Test: `tests/test_app_lifespan.py`
- Test: `tests/test_settings.py`

- [x] **Step 1: Write failing citation quality tests**

```python
def test_public_citations_keep_only_strong_results_without_padding():
    context = AgentRunContext("conversation", "run")
    add_ranked(context, "strong-a", rerank_score=0.91)
    add_ranked(context, "strong-b", rerank_score=0.73)
    add_ranked(context, "weak", rerank_score=0.12)
    citations = context.public_citations(
        5, min_rerank_score=0.35, min_rrf_score=0.02
    )
    assert [item.source_id for item in citations] == ["strong-a", "strong-b"]
```

Add tests for exact hits without a Rerank score, strict RRF fallback, logical-source deduplication, source diversity and hard cap five.

- [x] **Step 2: Run the context tests and verify RED**

Run: `python -m pytest tests/test_agent_context.py -k citation -q`

Expected: FAIL because citations are currently insertion-ordered and unscored.

- [x] **Step 3: Attach trusted quality signals when adding citations**

`rag_tools.py` merges server-generated `_retrieval` metadata into each citation:

```python
{
    "exact": exact_hit,
    "rerank_applied": result.rerank_applied,
    "rerank_score": item.rerank_score,
    "fusion_score": item.fusion_score,
    "rank": item.rank,
}
```

The fields are never taken from model input.

- [x] **Step 4: Implement the selector and configuration**

Defaults:

```dotenv
AGENT_PUBLIC_CITATION_LIMIT=5
AGENT_CITATION_MIN_RERANK_SCORE=0.35
AGENT_CITATION_MIN_RRF_SCORE=0.02
```

MCP, Swagger and verified log-trace citations remain eligible because they are deterministic tool evidence. Code and document citations pass exact/Rerank/RRF gates. The selector sorts by exactness and score, deduplicates, preserves useful source diversity, and never pads to three.

- [x] **Step 5: Inject thresholds into AgentService**

Extend `AgentService` constructor and all `public_citations` calls with the configured thresholds. Update app lifespan and tests to prove the values propagate.

- [x] **Step 6: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_agent_context.py tests/test_agent_service.py tests/test_app_lifespan.py tests/test_settings.py -q`

Expected: PASS.

- [x] **Step 7: Commit the citation gate**

```bash
git add knowledge/agent_runtime/context.py knowledge/agent_runtime/rag_tools.py knowledge/config/settings.py knowledge/api/app.py knowledge/agent_runtime/agent_factory.py .env.example tests/test_agent_context.py tests/test_agent_service.py tests/test_app_lifespan.py tests/test_settings.py
git commit -m "feat: gate public citations by evidence strength"
```

### Task 5: Verify Latency-Sensitive Execution

**Files:**
- Modify only files proven necessary by timing tests
- Test: `tests/test_agent_evidence_collection_budget.py`
- Test: `tests/test_multi_route_retrieval.py`
- Test: `tests/test_agent_quality_capture.py`

- [x] **Step 1: Add timing-independent concurrency tests**

Use events instead of wall-clock sleeps to prove product-document and code searches start before either finishes, while their budget reservations remain atomic.

- [x] **Step 2: Add single-execution tests**

Instrument fake Query Rewrite, keyword, vector and Rerank services. Repeating one normalized query in the same run must leave every counter at one.

- [x] **Step 3: Run focused tests and verify RED or existing coverage**

Run: `python -m pytest tests/test_agent_evidence_collection_budget.py tests/test_multi_route_retrieval.py tests/test_agent_quality_capture.py -q`

Expected: any missing single-execution or span behavior fails; already-correct concurrency tests remain green and are not rewritten.

- [x] **Step 4: Implement only measured gaps**

Reuse completed evidence within a run, stop fallback retrieval once strong evidence exists, and ensure quality completion remains asynchronous. Do not add caches that cross users or knowledge scopes.

- [x] **Step 5: Run focused tests and verify GREEN**

Run the same focused command and require PASS.

- [x] **Step 6: Commit measured latency fixes if production code changed**

```bash
git add knowledge tests
git commit -m "perf: reduce repeated single-domain retrieval work"
```

### Task 6: Apply Backfill And Run Release Gates

**Files:**
- Modify: `openspec/changes/postgres-pgvector-persistence/tasks.md` only after evidence exists
- Generated runtime data: PostgreSQL aggregate updates only; never commit data dumps

- [ ] **Step 1: Run the complete automated suite before data mutation**

Run: `python -m pytest -q`

Run: `cd web && npm test -- --run && npm run build`

Expected: all commands exit zero.

- [ ] **Step 2: Preview the 268-row backfill**

Run: `python -m knowledge.storage_cli backfill-document-domains`

Expected: dry-run reports 268 total product-document rows and the current mismatch count, without IDs or content.

- [ ] **Step 3: Apply and verify the backfill**

Run: `python -m knowledge.storage_cli backfill-document-domains --apply`

Run the dry-run again. Expected: pending mismatch is zero and all four stable domain IDs have the expected aggregate counts.

- [ ] **Step 4: Restart one worker and verify readiness**

Wait for sync, eval and memory queues to have no queued or running jobs. Restart exactly one API process, then verify model, PostgreSQL, pgvector, BM25, catalog, quality worker and memory worker are available.

- [ ] **Step 5: Run Critical 5**

Select the first five approved `official-critical-v2` cases in stable priority/ID order and run them with the existing evaluator. Require 5/5. On failure stop, classify, add a failing regression test, fix and rerun.

- [ ] **Step 6: Run Critical 10**

Run the first ten approved cases and require 10/10 with the same failure discipline.

- [ ] **Step 7: Run Critical 30**

Run all thirty approved cases and require 30/30. Do not weaken case facts, citation requirements or judge thresholds.

- [ ] **Step 8: Measure a fresh 30-request web window**

Record a release marker timestamp, run at least thirty representative single-domain web requests through the SSE endpoint, and query only completed `channel='web'` rows created after the marker. Require P50 <=15 seconds, P90 <=30 seconds, actual tool calls <=4 and duplicate normalized retrievals zero.

- [ ] **Step 9: Run final verification and record evidence**

Run backend tests, frontend tests/build, readiness, vector domain aggregates and Critical summaries again. Update the OpenSpec task only with observed counts and outcomes. If any gate fails, report the exact remaining failure class and do not claim readiness for expanded dev use.
