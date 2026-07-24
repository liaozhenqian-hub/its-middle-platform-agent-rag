# User History And Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each anonymous or Feishu identity reopen its own conversations and review or confirm its own long-term-memory candidates.

**Architecture:** Keep conversation ownership in `user_auth.db` and message bodies in `agent_sessions.db`. A focused history repository joins them at the service boundary using `conversation_id`, returning only public user/assistant text. Memory candidates remain separate from transcripts; user-scoped candidates can be confirmed by their owner, while domain-scoped candidates remain admin-controlled.

**Tech Stack:** FastAPI, aiosqlite, Pydantic, Vue 3, Pinia, Vue Router, Element Plus, Vitest, pytest.

---

### Task 1: Conversation history repository and API

**Files:**
- Create: `knowledge/history/models.py`
- Create: `knowledge/history/repository.py`
- Modify: `knowledge/auth/models.py`
- Modify: `knowledge/auth/repository.py`
- Modify: `knowledge/api/app.py`
- Test: `tests/test_conversation_history.py`
- Test: `tests/test_user_auth_repository.py`

- [ ] Write failing repository tests for owner-scoped list, search, rename, detail, and cross-owner rejection.
- [ ] Run the focused tests and verify the missing interfaces fail.
- [ ] Add the idempotent title migration and public transcript parsing.
- [ ] Add owner-scoped list/detail/rename API routes.
- [ ] Run the focused backend tests until green.

### Task 2: Personal memory review

**Files:**
- Modify: `knowledge/api/app.py`
- Modify: `knowledge/memory/repository.py`
- Test: `tests/test_memory_api.py`

- [ ] Write failing tests that a user can list and confirm only their user-scoped candidates.
- [ ] Run the tests and verify authorization and missing-route failures.
- [ ] Add user candidate list and confirmation routes with Feishu CSRF protection.
- [ ] Run the focused memory tests until green.

### Task 3: Vue history and memory experience

**Files:**
- Create: `web/src/stores/history.ts`
- Create: `web/src/views/HistoryView.vue`
- Modify: `web/src/router/index.ts`
- Modify: `web/src/stores/chat.ts`
- Modify: `web/src/views/ChatView.vue`
- Modify: `web/src/stores/userMemory.ts`
- Modify: `web/src/views/MemoryView.vue`
- Modify: `web/src/types/api.ts`
- Test: `web/src/stores/history.test.ts`
- Test: `web/src/stores/userMemory.test.ts`

- [ ] Write failing store tests for history loading, transcript restore, rename/delete, and memory confirmation.
- [ ] Run Vitest and verify the new behavior fails for missing interfaces.
- [ ] Implement the stores and `/history` route.
- [ ] Build the history list/detail UI and distinguish pending from confirmed memory.
- [ ] Add history navigation to the chat header and restore selected conversations into the chat store.
- [ ] Run focused frontend tests until green.

### Task 4: Regression and runtime verification

**Files:**
- Modify: `README.md`

- [ ] Document the distinction between conversation history and long-term memory.
- [ ] Run the complete Python test suite.
- [ ] Run the complete Vue test suite and production build.
- [ ] Restart the single-worker service only after queues are idle.
- [ ] Verify `/history`, `/memory`, conversation restore, and owner isolation in a real browser.
