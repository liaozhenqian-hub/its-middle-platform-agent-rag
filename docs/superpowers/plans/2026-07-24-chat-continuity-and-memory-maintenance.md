# Chat Continuity And Memory Maintenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the active conversation after browser refresh, place collapsible recent conversations at the bottom-left of chat, and automatically confirm user-owned memory candidates after 24 hours.

**Architecture:** Persist only the active conversation ID in browser storage and reload canonical messages from the owned-conversation API. Reuse the existing history Pinia store for the chat rail. Extend the existing memory worker with a periodic maintenance pass that approves only user-scoped candidates older than the configured delay and indexes them through `MemoryService`.

**Tech Stack:** FastAPI, aiosqlite, Pinia, Vue 3, Element Plus, Vitest, pytest.

---

### Task 1: Automatic User Memory Confirmation

**Files:**
- Modify: `knowledge/config/settings.py`
- Modify: `knowledge/memory/repository.py`
- Modify: `knowledge/memory/service.py`
- Modify: `knowledge/memory/worker.py`
- Modify: `knowledge/api/app.py`
- Modify: `web/src/views/MemoryView.vue`
- Test: `tests/test_memory_repository.py`
- Test: `tests/test_memory_service.py`
- Test: `tests/test_memory_worker.py`
- Test: `tests/test_memory_settings.py`

- [ ] Write repository tests proving only `scope_type='user'` candidates older than 24 hours are selected.
- [ ] Run focused tests and verify they fail because due-candidate APIs do not exist.
- [ ] Add `MEMORY_AUTO_CONFIRM_SECONDS=86400` and a bounded maintenance interval.
- [ ] Add `list_due_user_candidates(cutoff, limit)` ordered oldest first.
- [ ] Add `MemoryService.auto_confirm_due_candidates()` using actor `system:auto-confirm`, the existing retention period, and the existing Chroma index update.
- [ ] Run maintenance immediately at worker startup and periodically while idle or processing extraction jobs.
- [ ] Update the Memory page text to state that pending memories auto-confirm after 24 hours and remain deletable.
- [ ] Run focused memory tests.

### Task 2: Restore Active Conversation After Refresh

**Files:**
- Modify: `web/src/stores/chat.ts`
- Modify: `web/src/stores/chat.test.ts`
- Modify: `web/src/views/ChatView.vue`

- [ ] Write tests for persisting on `run.started`, restoring through `/v1/agent/conversations/{id}`, and clearing inaccessible IDs.
- [ ] Run the focused Vitest file and verify the new tests fail.
- [ ] Add a storage adapter that stores only `conversation_id`.
- [ ] Persist IDs when conversations start or are restored; clear them on new conversation and scope switch.
- [ ] Add `restorePersistedConversation()` that loads spaces first, reloads the owned server transcript, and clears stale/foreign IDs on failure.
- [ ] Call the restore action when ChatView mounts and show a bounded loading state instead of flashing an empty conversation.
- [ ] Run focused store and view tests.

### Task 3: Collapsible Recent Conversations In The Left Rail

**Files:**
- Modify: `web/src/views/ChatView.vue`
- Modify: `web/src/views/ChatView.test.ts`

- [ ] Write a view test proving the header history icon is gone and recent history appears in a collapsible bottom-left section.
- [ ] Run the focused view test and verify it fails.
- [ ] Load recent conversations with the existing history store after identity initialization.
- [ ] Render up to eight recent titles in a bounded independently scrolling list.
- [ ] Open a selected conversation in place; retain a compact link to the full history management page.
- [ ] Add a collapse control with `aria-expanded`; preserve stable rail dimensions and hide this desktop rail on mobile.
- [ ] Run focused frontend tests.

### Task 4: Verification And Restart

**Files:**
- Verify only.

- [ ] Run `python -m pytest -q`.
- [ ] Run `npm test -- --run`.
- [ ] Run `npm run build`.
- [ ] Verify no sync or eval job is queued/running.
- [ ] Restart one Uvicorn worker and verify `/health/ready` plus `/chat`.
