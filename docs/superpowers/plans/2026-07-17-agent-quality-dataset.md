# Agent Quality Dataset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every real Agent turn and its feedback in a dedicated quality database, let administrators curate regression cases, and rerun those cases against the latest Agent stack.

**Architecture:** Add a focused `knowledge/quality/` package with SQLite repository, capture service, and evaluation service. API and Feishu adapters report lifecycle events without changing retrieval or Agent orchestration; Vue consumes public feedback and admin quality endpoints.

**Tech Stack:** Python 3.11, FastAPI, aiosqlite, Pydantic, OpenAI Agents SDK service boundary, Vue 3, Pinia, Element Plus, Vitest, pytest.

---

### Task 1: Quality database and settings

**Files:**
- Create: `knowledge/quality/models.py`
- Create: `knowledge/quality/repository.py`
- Create: `knowledge/quality/service.py`
- Create: `knowledge/quality/__init__.py`
- Modify: `knowledge/config/settings.py`
- Modify: `.env.example`
- Test: `tests/test_quality_repository.py`
- Test: `tests/test_settings.py`

- [x] Write repository tests that initialize the schema, create a running turn, complete it idempotently, persist tools/citations, update feedback, paginate/filter records, create an eval case, and cascade delete.

```python
turn = await repository.start_turn(TurnStart(run_id="run-1", question="原始问题", channel="web"))
await repository.complete_turn("run-1", TurnCompletion(status="completed", answer="回答"))
assert (await repository.get_turn(turn.id)).answer == "回答"
```

- [x] Run `python -m pytest tests/test_quality_repository.py tests/test_settings.py -q` and verify the new tests fail because the package and settings do not exist.
- [x] Implement typed records and an aiosqlite repository with migration table, WAL, foreign keys, busy timeout, unique `run_id`, partial channel-message uniqueness, indexes, short lock retries, and startup recovery of stale running turns.
- [x] Add `AGENT_QUALITY_ENABLED`, `AGENT_QUALITY_DB`, `AGENT_QUALITY_RUNNING_TIMEOUT_SECONDS`, `AGENT_QUALITY_PAGE_SIZE`, and application/prompt version settings with project-root path resolution.
- [x] Run the focused tests and verify they pass.

### Task 2: JSON and SSE capture

**Files:**
- Modify: `knowledge/api/app.py`
- Modify: `knowledge/api/schemas.py`
- Test: `tests/test_agent_quality_capture.py`
- Test: `tests/test_agent_api.py`

- [x] Write failing API tests using an injected quality service. Assert JSON and SSE establish `running` before Agent execution, complete all terminal statuses, mark disconnect/error, preserve existing response fields, and return `quality_turn_id` plus a one-time feedback token.

```python
assert result.json()["quality_turn_id"] == "turn-1"
assert result.json()["feedback_token"] == "public-feedback-token"
assert capture.calls[0].question == "指标是什么"
```

- [x] Extend `create_app()` injection and production lifespan to initialize/close quality services and expose readiness.
- [x] Wrap JSON and SSE routes with best-effort capture calls. Accumulate only public `text.delta` output in memory; use the terminal `AgentResponse` as the canonical answer and audit metadata.
- [x] Add optional quality fields to `AgentResponse` and `run.started`/terminal SSE payloads without changing existing contracts.
- [x] Run the focused API tests and verify they pass.

### Task 3: Feedback, admin query, deletion, export, and eval-case CRUD

**Files:**
- Modify: `knowledge/api/schemas.py`
- Modify: `knowledge/api/app.py`
- Modify: `knowledge/quality/repository.py`
- Create: `tests/test_quality_api.py`

- [x] Write failing tests for feedback-token validation, positive/negative upsert, admin read/write authorization, pagination/filtering, details, deletion, eval-case CRUD, and streaming CSV/JSONL exports.

```python
feedback = client.post(
    "/api/v1/quality/turns/turn-1/feedback",
    json={"feedback_token": "token", "rating": "negative", "reason": "引用错误"},
)
assert feedback.status_code == 204
```

- [x] Implement public feedback endpoint with constant-time hash comparison and no question/answer read access.
- [x] Implement admin list/detail/delete endpoints using existing Session and CSRF helpers.
- [x] Implement eval-case create/list/update/delete schemas and routes with required tools, citation types, required facts, forbidden facts, tags, and enabled state.
- [x] Implement paged JSONL/CSV export that never loads all permanent records into memory.
- [x] Run `python -m pytest tests/test_quality_api.py -q` and verify it passes.

### Task 4: Evaluation runner

**Files:**
- Create: `knowledge/quality/evaluation.py`
- Modify: `knowledge/quality/models.py`
- Modify: `knowledge/quality/repository.py`
- Modify: `knowledge/api/app.py`
- Modify: `knowledge/api/schemas.py`
- Create: `tests/test_quality_evaluation.py`

- [x] Write failing tests with a fake Agent service. Assert each enabled case runs in a fresh `eval:<uuid>` conversation, current scope is used, results do not create quality turns, one failure does not stop the batch, and deterministic rules score tools, citations, facts, forbidden content, status, and latency.

```python
run = await evaluator.run_cases([case.id])
result = (await repository.list_eval_results(run.id))[0]
assert result.passed is True
assert result.checks["required_tools"] is True
```

- [x] Implement `QualityEvaluationService` with bounded sequential execution for the single-server deployment and persisted run/result state.
- [x] Add admin endpoints to start a run, list runs, and inspect results.
- [x] Run focused evaluation tests and verify they pass.

### Task 5: Feishu capture and reactions

**Files:**
- Modify: `knowledge/feishu/models.py`
- Modify: `knowledge/feishu/messages.py`
- Modify: `knowledge/feishu/gateway.py`
- Modify: `knowledge/feishu/bridge.py`
- Modify: `knowledge/api/app.py`
- Test: `tests/test_feishu_messages.py`
- Test: `tests/test_feishu_gateway.py`
- Test: `tests/test_feishu_bridge.py`

- [x] Write failing tests for sender open ID/name parsing, turn capture for completed/timeout/error replies, returned bot reply message IDs, reaction-created/deleted normalization, and positive/negative feedback updates.

```python
assert parsed.sender_id == "ou_user"
assert parsed.sender_name == "张三"
assert await bridge.handle_event(reaction_payload) is True
```

- [x] Extend gateway dispatcher with `register_p2_im_message_reaction_created_v1` and deleted counterpart. Normalize reaction payloads without message content.
- [x] Return reply message IDs from text/card gateway methods and bind the first reply ID to the captured turn.
- [x] Capture Feishu turns with real sender identity and map configured positive/negative emoji sets to feedback; deleting a reaction removes that user's feedback.
- [x] Run focused Feishu tests and verify they pass.

### Task 6: Web feedback and admin quality workspace

**Files:**
- Modify: `web/src/types/api.ts`
- Modify: `web/src/stores/chat.ts`
- Modify: `web/src/views/ChatView.vue`
- Create: `web/src/stores/quality.ts`
- Create: `web/src/components/admin/QualityTable.vue`
- Create: `web/src/components/admin/QualityDetailDrawer.vue`
- Create: `web/src/components/admin/EvalCasesTable.vue`
- Modify: `web/src/views/AdminView.vue`
- Test: `web/src/stores/chat.test.ts`
- Create: `web/src/stores/quality.test.ts`
- Create: `web/src/components/admin/QualityTable.test.ts`

- [x] Write failing Vitest cases for terminal quality metadata, thumbs feedback submission, admin filters/page loading, detail/delete, eval promotion, and batch-run result rendering.

```ts
expect(api.post).toHaveBeenCalledWith("/v1/quality/turns/turn-1/feedback", {
  feedback_token: "token",
  rating: "negative",
  reason: "引用错误",
});
```

- [x] Store quality ID/token only on assistant messages and add compact thumbs controls after completed answers.
- [x] Add an admin segmented navigation for knowledge sources, Q&A quality, and eval cases; keep operational density and independent scrolling on desktop/mobile.
- [x] Implement paged filters, detail drawer, delete confirmation, eval-case form, export actions, and run comparison display.
- [x] Run `npm test` and `npm run build` from `web` and verify both pass.

### Task 7: Documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `docs/superpowers/plans/2026-07-17-agent-quality-dataset.md`

- [x] Document database location, raw retention decision, admin-only access, feedback behavior, evaluation workflow, and server-held sensitive-data exclusions.
- [x] Run `python -m pytest -q` and confirm the complete backend suite passes.
- [x] Run `npm test` and `npm run build` in `web` and confirm the frontend suite and production build pass.
- [x] Start the single-worker Uvicorn service, check `/health/ready`, execute one real web turn, verify the quality row contains the public question/answer and excludes tool bodies, then stop the verification process.
- [x] Mark every completed checkbox in this plan; because the project is not a Git repository, do not execute commit, branch, or PR commands.

