# Long-Term Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a permission-aware, auditable long-term memory layer that can recall confirmed user and domain context across conversations without replacing the existing session, RAG, Bug Graph, or quality databases.

**Architecture:** A standalone SQLite `agent_memory.db` stores memory records and candidates. Chroma receives only confirmed memory embeddings through a separate collection or metadata scope. Extraction is asynchronous and candidate-only; Manager reads compact, permission-filtered memories through a server-controlled tool, while no model can directly persist confirmed memories.

**Tech Stack:** FastAPI, aiosqlite, Pydantic, Chroma, existing embedding pipeline, DeepSeek Flash structured JSON, OpenAI Agents SDK, Vue 3, Pinia, Element Plus, Vitest, Pytest.

---

### Task 1: Memory data model and repository

**Files:**
- Create: `knowledge/memory/models.py`
- Create: `knowledge/memory/repository.py`
- Create: `knowledge/memory/__init__.py`
- Modify: `knowledge/config/settings.py`
- Test: `tests/test_memory_repository.py`

- [ ] Write failing tests for SQLite initialization, WAL/foreign keys, candidate creation, approval/rejection, namespace filtering, expiry, soft delete, and conflict versioning.
- [ ] Add tables `memories`, `memory_candidates`, `memory_audit_events` with owner/scope/type/status/confidence/source/validity fields and indexes on namespace, status, and expiration.
- [ ] Enforce scopes `user`, `conversation`, `team`, `domain`, `global`; reject empty owner IDs and forbidden memory types.
- [ ] Add repository methods `create_candidate`, `approve_candidate`, `reject_candidate`, `search_candidates`, `list_memories`, `get_memory`, `expire_memories`, `soft_delete_memory`, and `record_audit`.
- [ ] Add `MEMORY_ENABLED`, `MEMORY_DB`, `MEMORY_MAX_RECALL`, `MEMORY_CANDIDATE_TTL_SECONDS`, `MEMORY_DEFAULT_RETENTION_DAYS`, and `MEMORY_EXTRACTION_ENABLED` settings with project-root path resolution.
- [ ] Run the repository and settings tests.

### Task 2: Safe extraction and memory retrieval service

**Files:**
- Create: `knowledge/memory/service.py`
- Create: `knowledge/memory/extractor.py`
- Create: `knowledge/memory/policy.py`
- Test: `tests/test_memory_service.py`
- Test: `tests/test_memory_extractor.py`

- [ ] Write failing tests proving passwords, tokens, Authorization headers, raw logs, embeddings, source code bodies, and arbitrary private data are rejected.
- [ ] Define extracted types `user_preference`, `user_context`, `episodic_memory`, and `decision_memory`; product facts remain in the knowledge catalog.
- [ ] Parse Flash JSON into Pydantic candidates with one repair attempt and deterministic empty fallback; never save model output directly as confirmed memory.
- [ ] Add deterministic normalization, namespace authorization, exact-key conflict detection, confidence thresholds, candidate TTL, and explicit user/admin confirmation.
- [ ] Implement keyword/vector recall over confirmed records only, returning at most the configured limit and only compact summaries plus source turn/citation IDs.
- [ ] Add asynchronous `extract_candidates` entry point that does not delay the user answer.
- [ ] Run focused memory service tests.

### Task 3: Chroma memory index and Agent tool

**Files:**
- Create: `knowledge/memory/index.py`
- Modify: `knowledge/api/app.py`
- Modify: `knowledge/agent_runtime/agent_factory.py`
- Modify: `knowledge/agent_runtime/service.py`
- Test: `tests/test_memory_tools.py`
- Test: `tests/test_agent_service.py`

- [ ] Write failing tests for user namespace isolation, domain/shared access, no candidate recall, and Manager use of a memory tool.
- [ ] Build a separate Chroma collection `middle_platform_memories` or an isolated metadata scope; never mix memory vectors with product/code vectors.
- [ ] Register server-controlled `search_user_memory` and `search_domain_memory` tools whose inputs are only the question; user, conversation, space, and domain come from `AgentRunContext`.
- [ ] Inject a bounded “相关历史上下文” block before Manager execution only when relevant memories pass policy checks.
- [ ] Add startup/close/readiness handling; memory failure must degrade to no-memory mode without disabling RAG/MCP/Bug Graph.
- [ ] Run focused Agent and lifecycle tests.

### Task 4: Quality capture and asynchronous candidate extraction

**Files:**
- Modify: `knowledge/api/app.py`
- Modify: `knowledge/quality/service.py`
- Create: `knowledge/memory/worker.py`
- Modify: `knowledge/config/settings.py`
- Test: `tests/test_memory_worker.py`
- Test: `tests/test_quality_capture.py`

- [ ] Write failing tests proving completed user turns enqueue extraction without changing response latency and worker restart recovers queued jobs.
- [ ] Add a memory extraction queue in `agent_memory.db` with queued/running/succeeded/failed states and one-process mutual exclusion.
- [ ] Submit only sanitized question/answer summaries, citations, user identity, conversation ID, domain, and channel; exclude raw logs, tool payloads, prompts, and credentials.
- [ ] On worker completion, persist only candidates and audit status; never auto-approve.
- [ ] Add retry/backoff and stale-running recovery.
- [ ] Run worker and quality tests.

### Task 5: Admin and user memory APIs

**Files:**
- Modify: `knowledge/api/schemas.py`
- Modify: `knowledge/api/app.py`
- Modify: `web/src/types/api.ts`
- Modify: `web/src/api/client.ts`
- Create: `web/src/api/memory.ts`
- Create: `web/src/stores/memory.ts`
- Test: `tests/test_memory_api.py`
- Test: `web/src/stores/memory.test.ts`

- [ ] Write failing tests for candidate listing, approve/reject, scoped listing, user deletion, admin deletion, expired-memory hiding, and forbidden cross-user access.
- [ ] Add read-only user endpoints for “我的记忆” and delete/forget; add admin endpoints for candidate review, filtering by scope/type/status, confirmation, rejection, and deletion.
- [ ] Require admin session + CSRF for administrative writes; require authenticated user identity for private-memory operations.
- [ ] Never return embeddings, raw evidence, credentials, full conversation bodies, or internal prompts.
- [ ] Run backend API and TypeScript store tests.

### Task 6: Vue management page and settings

**Files:**
- Create: `web/src/views/MemoryView.vue`
- Modify: `web/src/router/index.ts`
- Modify: `web/src/views/AdminView.vue`
- Modify: `web/src/stores/auth.ts`
- Test: `web/src/views/MemoryView.test.ts`
- Test: `web/e2e/memory-admin.spec.ts`

- [ ] Add an admin memory tab with candidate/confirmed/expired filters, scope/type badges, source citation IDs, confidence, and approve/reject/delete actions.
- [ ] Add a user-facing “我的记忆” view with individual forget controls and no access to team/domain/global memories unless authorized.
- [ ] Display candidate status clearly; do not imply that a candidate affects Agent answers.
- [ ] Add loading, empty, error, confirmation, and mobile layout states.
- [ ] Run Vitest, production build, and Playwright memory flows.

### Task 7: Summaries, migration, and rollout

**Files:**
- Create: `knowledge/memory/summarizer.py`
- Modify: `knowledge/agent_runtime/service.py`
- Modify: `knowledge/api/app.py`
- Create: `knowledge/cli.py` command for memory cleanup/rebuild
- Test: `tests/test_memory_summarizer.py`
- Test: `tests/test_memory_rollout.py`

- [ ] Add conversation summaries containing only goals, confirmed facts, unresolved items, and explicit preferences.
- [ ] Keep summaries separate from raw `SQLiteSession` messages and cap summary length.
- [ ] Add dry-run cleanup and rebuild commands; do not migrate historical questions automatically into confirmed memory.
- [ ] Roll out behind `MEMORY_ENABLED` and `MEMORY_EXTRACTION_ENABLED` flags, initially read-only, then candidate extraction, then user/admin approval.
- [ ] Run full Python tests, frontend tests/build, Playwright, readiness checks, and verify sync/eval queues remain empty.
