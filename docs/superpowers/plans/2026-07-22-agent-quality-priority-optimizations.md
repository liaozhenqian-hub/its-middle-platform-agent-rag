# Agent Quality Priority Optimizations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve citation completeness, runtime observability, ambiguous-query routing, exact technical retrieval, and specialist answer consistency without introducing an unbounded ReAct loop.

**Architecture:** Keep deterministic routing and retrieval budgets as the default. Add bounded fallbacks: source-backed document detail, Flash routing only when rules are uncertain, deterministic identifier preservation/boosting, and structured specialist answer sections enforced at the prompt and response boundary. The target directory is not a Git repository, so commit steps are intentionally omitted.

**Tech Stack:** FastAPI, Pydantic, SQLite catalog, Chroma, OpenAI Agents SDK, DeepSeek Flash, Vue 3, TypeScript, Element Plus, Vitest, Pytest, Playwright.

---

### Task 1: Product-document section, full text, and original URL

**Files:**
- Modify: `knowledge/api/schemas.py`
- Modify: `knowledge/api/app.py`
- Modify: `knowledge/services/citation_detail_service.py`
- Modify: `web/src/api/citations.ts`
- Modify: `web/src/types/api.ts`
- Modify: `web/src/components/chat/CitationPanel.vue`
- Test: `tests/test_citation_detail.py`
- Test: `web/src/components/chat/CitationPanel.test.ts`

- [ ] Write failing backend tests proving the default detail joins every chunk in the matched heading, `view=full` reparses the registered original file in source order, disabled/missing sources remain 404, and the original URL contains only the citation chunk ID.
- [ ] Add `view=section|full`, `content_scope`, `full_text_available`, and `document_url` to the response while preserving existing clients.
- [ ] Resolve original files only through catalog source/version/file records and `storage/uploads`; reject traversal and never accept a client path.
- [ ] Add a read-only original-document endpoint keyed by chunk ID and return `FileResponse` only for enabled product-document sources.
- [ ] Write failing Vue tests for section rendering, “查看全文”, Markdown formatting, loading/error recovery, and original-document links.
- [ ] Render product documents with the existing sanitized Markdown renderer; keep code in a monospace scroller and lazy-load full text only after user action.
- [ ] Run focused backend and frontend tests.

### Task 2: Complete retrieval timing spans

**Files:**
- Modify: `knowledge/schemas/documents.py`
- Modify: `knowledge/services/multi_route_retrieval_service.py`
- Modify: `knowledge/agent_runtime/rag_tools.py`
- Test: `tests/test_multi_route_retrieval.py`
- Test: `tests/test_agent_rag_tools.py`

- [ ] Write failing tests for `query_rewrite`, `keyword_search`, `vector_search`, and `rerank` timing measurements without prompt or content metadata.
- [ ] Measure each stage with `perf_counter`, including degraded vector/rerank paths.
- [ ] Convert stage timings into `RuntimeSpan(kind="retrieval")` entries from the request context.
- [ ] Ensure quality persistence accepts the existing `retrieval` span kind and no public API exposes spans.
- [ ] Run focused quality and retrieval tests.

### Task 3: Rule-first, Flash-fallback intent routing

**Files:**
- Modify: `knowledge/services/query_rewrite_service.py`
- Create: `knowledge/agent_runtime/hybrid_intent_router.py`
- Modify: `knowledge/agent_runtime/service.py`
- Modify: `knowledge/api/app.py`
- Modify: `knowledge/config/settings.py`
- Test: `tests/test_intent_router.py`
- Test: `tests/test_agent_service.py`
- Test: `tests/test_settings.py`

- [ ] Write failing tests showing explicit approval/workflow/metric/Bug signals never call Flash, while uncertain wording can be mapped from trusted Chinese domain names to fixed domain IDs.
- [ ] Extend the rewrite payload with an optional task type and preserve exact environment, trace ID, URL, API path, class, method, and field tokens from the original text.
- [ ] Add an asynchronous hybrid router that invokes the existing Flash rewriter through `asyncio.to_thread()` only when deterministic confidence is below the configured threshold.
- [ ] Reject unknown model domains and fall back to Manager on timeout, validation error, or disagreement with explicit original-text signals.
- [ ] Record one routing span without storing user text or model output.
- [ ] Run focused routing and service tests.

### Task 4: Deterministic exact-identifier retrieval

**Files:**
- Create: `knowledge/services/query_identifiers.py`
- Modify: `knowledge/services/multi_route_retrieval_service.py`
- Modify: `knowledge/services/hybrid_rerank_service.py`
- Modify: `knowledge/agent_runtime/rag_tools.py`
- Test: `tests/test_query_identifiers.py`
- Test: `tests/test_multi_route_retrieval.py`
- Test: `tests/test_agent_rag_tools.py`

- [ ] Write failing tests for duplicated gateway prefixes, URL paths, Java/Vue symbols, camelCase fields, trace IDs, and environment aliases.
- [ ] Extract original exact identifiers deterministically before LLM rewrite and append them to BM25 queries.
- [ ] Preserve the original API path and add one normalized path variant without silently changing the user-visible question.
- [ ] Boost candidates whose path, symbol, heading, or content contains exact identifiers; keep rerank ordering as the secondary signal and deduplicate identical chunks.
- [ ] For code evidence, include bounded structural/context metadata already available from catalog rather than launching another free retrieval loop.
- [ ] Run focused retrieval tests.

### Task 5: Specialist answer contract and Manager preservation

**Files:**
- Create: `knowledge/agent_runtime/specialist_answers.py`
- Modify: `knowledge/agent_runtime/agent_factory.py`
- Modify: `knowledge/agent_runtime/service.py`
- Modify: `knowledge/agent_runtime/evidence_policy.py`
- Test: `tests/test_agent_factory.py`
- Test: `tests/test_agent_service.py`
- Test: `tests/test_evidence_policy.py`

- [ ] Write failing tests for normalized `conclusion`, `evidence`, `unknowns`, `deployment_status`, and `confidence` sections.
- [ ] Require specialists to emit the contract and provide a custom Manager tool extractor that serializes it without adding unsupported facts.
- [ ] Convert direct-specialist structured output into concise Markdown for web/Feishu while preserving citations.
- [ ] Apply deployment disclaimers only to deployment claims and Swagger disclaimers only to contract claims.
- [ ] Keep zero-citation internal answers behind the existing evidence gate.
- [ ] Run focused agent and Feishu formatting tests.

### Task 6: Full regression and live readiness

**Files:**
- Verify only.

- [ ] Run `python -m pytest -q` with `.venv-agent`.
- [ ] Run `npm test -- --run`, `npm run build`, and `npx playwright test` under `web/`.
- [ ] Restart the single Uvicorn worker only if backend modules changed in the live process.
- [ ] Verify `/health/ready`, Chroma count, and that sync/eval active queues are zero before restart.
- [ ] Run one product-document citation smoke test and one ambiguous-domain routing smoke test without exposing credentials or raw internal content.
