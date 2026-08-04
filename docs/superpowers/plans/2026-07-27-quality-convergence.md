# Quality Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve answer completeness and safety by making evidence gaps explicit, applying task-specific answer structures, controlling metric MCP discovery, isolating memory scopes, and enforcing fact-level evaluation gates.

**Architecture:** Keep the existing Manager/specialist routing and static MCP allowlist. Add deterministic post-processing around specialist answers, a server-side metric query state/cache keyed by user and conversation scope, and explicit memory retrieval scope validation. Extend quality checks without storing raw prompts or hidden model reasoning.

**Tech Stack:** Python 3.11, FastAPI, OpenAI Agents SDK, SQLite, Chroma, pytest/pytest-asyncio.

---

### Task 1: Evidence-gap wording and task templates

**Files:**
- Modify: `knowledge/agent_runtime/agent_factory.py`
- Modify: `knowledge/agent_runtime/evidence_policy.py`
- Test: `tests/test_evidence_policy_documents.py`

- [ ] Add failing tests for explicit missing-evidence notices followed by supported conclusions, and for `api_contract`, `how_to`, `requirement_analysis`, and `code_lookup` section headings.
- [ ] Run the focused tests and confirm failure.
- [ ] Add deterministic task-template instructions and preserve supported conclusions when one fact is unknown.
- [ ] Run focused tests and confirm pass.

### Task 2: Metric MCP cache and state machine

**Files:**
- Create: `knowledge/agent_runtime/metric_query_state.py`
- Modify: `knowledge/agent_runtime/metric_mcp.py`
- Modify: `knowledge/agent_runtime/context.py`
- Test: `tests/test_metric_query_state.py`

- [ ] Add failing tests for normalized-query cache hits, app-confirmation gating, duplicate discovery suppression, and conversation/user isolation.
- [ ] Run focused tests and confirm failure.
- [ ] Implement an in-memory bounded TTL cache and states `discovering`, `awaiting_app_confirmation`, `confirmed`, and `completed`; cached results must never cross user or conversation scope.
- [ ] Keep the static tool allowlist and return cached discovery results through the wrapper instead of dynamically removing tools.
- [ ] Run focused tests and confirm pass.

### Task 3: Memory scope isolation

**Files:**
- Modify: `knowledge/memory/tools.py`
- Modify: `knowledge/memory/service.py`
- Modify: `knowledge/agent_runtime/context.py`
- Test: `tests/test_memory_scope_isolation.py`

- [ ] Add failing tests for personal, domain, conversation, and bug memory isolation plus deletion non-recall.
- [ ] Run focused tests and confirm failure.
- [ ] Require server-injected `user_id`, `conversation_id`, `domain_id`, and `memory_type` scope on retrieval; reject caller-supplied scope overrides.
- [ ] Ensure bug memory uses trace/environment scope and short retention, while domain memory cannot be returned to another domain.
- [ ] Run focused tests and confirm pass.

### Task 4: Fact-level evaluation gates

**Files:**
- Modify: `knowledge/quality/behavior.py`
- Modify: `knowledge/quality/evaluation.py`
- Modify: `knowledge/quality/models.py`
- Test: `tests/test_quality_fact_gates.py`

- [ ] Add failing tests for supported facts, unsupported required facts with an explicit gap notice, forbidden facts, required citations, and tool budget failures.
- [ ] Run focused tests and confirm failure.
- [ ] Add a deterministic fact gate that distinguishes unsupported facts from a completely unsupported answer and records failure codes.
- [ ] Preserve semantic judging only after hard gates pass.
- [ ] Run focused tests and confirm pass.

### Task 5: Regression and verification

**Files:**
- Modify: `knowledge/quality/critical_cases.py` only if case expectations need correction.
- Test: existing `tests/` suite.

- [ ] Run focused tests for all four modules.
- [ ] Run the complete backend suite.
- [ ] Run the 30-case critical-v2 evaluation and report per-domain failures, latency, tool counts, and citation coverage.
- [ ] Verify readiness and ensure no queued/running evaluation or sync task is left behind.
