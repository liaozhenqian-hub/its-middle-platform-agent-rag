# SSE Database Failure Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep chat SSE deterministic and user-friendly when PostgreSQL or another pre-stream dependency fails.

**Architecture:** The API owns the public stream lifecycle: it emits one early `run.started`, filters the service's duplicate start event, and converts uncaught pre-terminal exceptions into a sanitized `run.error`. The Vue store normalizes browser transport errors into a Chinese retry message and uses it as the empty assistant response.

**Tech Stack:** FastAPI, Python async generators, pytest/TestClient, Vue 3, Pinia, TypeScript, Vitest

---

### Task 1: Backend SSE lifecycle regression

**Files:**
- Modify: `tests/test_agent_quality_capture.py`
- Modify: `knowledge/api/app.py`

- [x] **Step 1: Write the failing test**

Add a fake streaming service that raises `OperationalError` before yielding an event. Assert the response contains exactly one `run.started`, one `run.error`, a Chinese public message, and no private exception text.

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agent_quality_capture.py -q`

Expected: the new test fails because the StreamingResponse connection currently terminates without `run.started` or `run.error`.

- [x] **Step 3: Implement the API-owned lifecycle**

In the stream generator:

```python
yield _sse({"event": "run.started", "data": {"conversation_id": conversation_id, "run_id": run_id}})
...
if event["event"] == "run.started":
    continue
...
except Exception as exc:
    yield _sse({
        "event": "run.error",
        "data": {
            "conversation_id": conversation_id,
            "run_id": run_id,
            "error": "服务暂时不可用，请稍后重试。",
            "error_type": type(exc).__name__,
        },
    })
```

Keep quality completion best-effort and never include `str(exc)` in the public payload.

- [x] **Step 4: Run backend regression**

Run: `python -m pytest tests/test_agent_quality_capture.py tests/test_agent_api.py -q`

Expected: all tests pass and successful streams contain exactly one `run.started`.

### Task 2: Frontend transport error normalization

**Files:**
- Modify: `web/src/stores/chat.test.ts`
- Modify: `web/src/stores/chat.ts`

- [x] **Step 1: Write the failing test**

Mock `streamChatEvents` to throw `new TypeError("network error")`. Assert both `store.error` and the empty assistant message use `网络连接已中断，请稍后重试。` and do not contain the English transport message.

- [x] **Step 2: Run test to verify it fails**

Run: `npm test -- src/stores/chat.test.ts`

Expected: the new test fails because the store currently exposes `network error`.

- [x] **Step 3: Implement the normalizer**

Add a focused helper:

```typescript
function publicChatError(error: unknown): string {
  const message = error instanceof Error ? error.message.trim() : "";
  if (/network error|networkerror|failed to fetch|load failed/i.test(message)) {
    return "网络连接已中断，请稍后重试。";
  }
  return message || "对话请求失败，请稍后重试。";
}
```

Use the normalized value for `store.error` and for an empty assistant message.

- [x] **Step 4: Run frontend regression**

Run: `npm test -- src/stores/chat.test.ts src/api/chat.test.ts`

Expected: all selected Vitest tests pass.

### Task 3: Integrated verification

**Files:**
- No production file changes

- [x] **Step 1: Run focused Python tests**

Run: `python -m pytest tests/test_agent_quality_capture.py tests/test_agent_api.py -q`

- [x] **Step 2: Run frontend tests and build**

Run: `npm test -- src/stores/chat.test.ts src/api/chat.test.ts`

Run: `npm run build`

- [x] **Step 3: Restart and smoke test**

Restart the single Uvicorn worker, call `/health/ready`, then make one SSE request and verify the observable event order is `run.started` followed by either a terminal success event or `run.error`.
