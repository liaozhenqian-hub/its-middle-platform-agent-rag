# Typed Memory System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe conversation-summary, Bug incident, entity, and procedural memory layers while keeping Chroma as the source of truth for product and code knowledge.

**Architecture:** Conversation summaries remain in `agent_memory.db` and are generated asynchronously from bounded, policy-filtered turns. Confirmed Bug incidents become user-scoped `episodic_memory` candidates with citations and environment/branch facts; they are never auto-confirmed. Entity relations and procedural playbooks use structured SQLite rows with evidence references, scope, branch, and validity metadata instead of opaque vectors. Manager receives only compact, permission-filtered memory results.

**Tech Stack:** Python, aiosqlite, FastAPI, OpenAI Agents SDK tools, LangGraph Bug Graph, SQLite/Chroma, pytest.

---

### Phase 1: Conversation Summary Memory

**Files:**
- Modify: `knowledge/memory/models.py`
- Modify: `knowledge/memory/repository.py`
- Modify: `knowledge/memory/worker.py`
- Modify: `knowledge/memory/service.py`
- Modify: `knowledge/agent_runtime/service.py`
- Test: `tests/test_memory_summary_runtime.py`

- [ ] Write a failing test proving a completed extraction job creates a bounded summary and the next turn receives that summary before the current question.
- [ ] Run the focused test and verify the summary is currently absent from runtime input.
- [ ] Add an idempotent summary update method that stores only policy-approved question/answer abstracts and preserves user/space/domain ownership.
- [ ] Run the summary worker after candidate extraction and include the latest summary in `_memory_augmented_message` with an explicit “context only” instruction.
- [ ] Run focused summary tests and the existing memory worker tests.

### Phase 2: Bug Incident Episodic Memory

**Files:**
- Modify: `knowledge/bug_graph/models.py`
- Modify: `knowledge/bug_graph/service.py`
- Modify: `knowledge/bug_graph/tool.py`
- Create: `knowledge/memory/incidents.py`
- Modify: `knowledge/agent_runtime/service.py`
- Modify: `knowledge/api/app.py`
- Test: `tests/test_bug_incident_memory.py`

- [ ] Write a failing test proving only completed `correlated` or `contract_supported` diagnoses create a user-scoped candidate, while clarification, no-log, and log-only results create none.
- [ ] Run the test and confirm Bug Graph has no incident recorder.
- [ ] Add a recorder that hashes a normalized incident subject, strips trace IDs/secrets/raw stack content, stores environment/branch/service/endpoint facts, and references public citations.
- [ ] Pass the current user identity into Bug Graph runs and attach the recorder at application startup without persisting evidence正文.
- [ ] Expose a concise candidate summary in the existing `/memory` UI for explicit user confirmation.
- [ ] Run focused incident tests and Bug Graph regressions.

### Phase 3: Structured Entity Memory

**Files:**
- Create: `knowledge/memory/entities.py`
- Modify: `knowledge/memory/repository.py`
- Modify: `knowledge/memory/tools.py`
- Modify: `knowledge/agent_runtime/agent_factory.py`
- Modify: `knowledge/api/app.py`
- Test: `tests/test_entity_memory.py`

- [ ] Write failing tests for entity alias normalization, relation upsert, branch/environment scoping, evidence references, and cross-user isolation.
- [ ] Run the tests and verify the structured entity tables/tools are missing.
- [ ] Add `entities`, `entity_aliases`, `entity_relations`, and `entity_evidence` tables with WAL, indexes, and idempotent migrations.
- [ ] Add a server-controlled `search_entity_memory(query)` tool that returns only matching public facts and citation IDs; the model cannot choose owner, branch, or environment filters.
- [ ] Register the tool for Manager and use it as supplementary context, never as a replacement for RAG or current code retrieval.
- [ ] Run focused entity tests and AgentFactory regressions.

### Phase 4: Procedural / Workflow Memory

**Files:**
- Modify: `knowledge/memory/models.py`
- Modify: `knowledge/memory/repository.py`
- Modify: `knowledge/memory/service.py`
- Modify: `web/src/views/MemoryView.vue`
- Modify: `web/src/types/api.ts`
- Test: `tests/test_procedural_memory.py`
- Test: `web/src/stores/userMemory.test.ts`

- [ ] Write failing tests for a `procedural_memory` candidate containing bounded ordered steps, evidence IDs, confirmation, expiration, and recall.
- [ ] Run the tests and confirm the existing four-type memory model rejects procedural records.
- [ ] Add the type with the same candidate/confirmation gate; store verified troubleshooting steps, not model chain-of-thought or raw tool traces.
- [ ] Add labels and a separate “排障流程记忆” section in the memory page.
- [ ] Run focused procedural tests and full Python/Vue suites.

### Phase 5: Rollout and Verification

- [ ] Add settings for summary size, incident candidate TTL, entity recall limit, and procedural memory enablement.
- [ ] Run `python -m pytest -q`, `npm test`, and `npm run build`.
- [ ] Back up `storage/agent_memory.db` before startup migration.
- [ ] Restart with one Uvicorn worker only when sync/eval/memory queues have no queued or running jobs.
- [ ] Verify readiness, summary creation, Bug candidate gating, entity search, and procedural confirmation through the web UI.

**Explicit non-goals:** no Neo4j, Redis, Celery, automatic confirmation, raw log/code storage, chain-of-thought persistence, or copying the Chroma knowledge catalog into memory tables.
